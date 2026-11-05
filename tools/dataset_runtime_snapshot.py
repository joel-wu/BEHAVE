"""Freeze a validation runtime so concurrent simulator edits cannot mix versions.

This copies implementation and witness bytes, not their meaning. A snapshot
still needs its own tests; a stable file set is not a correctness certificate.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile


def inventory(root: Path, selections: tuple[str, ...]) -> dict[str, str]:
    """Hash explicit, repository-relative trees without following symlinks."""
    files = {}
    for selection in selections:
        relative = Path(selection)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('snapshot selections must be repository-relative')
        path = root / relative
        if not path.exists():
            raise FileNotFoundError(path)
        entries = [path, *path.rglob('*')] if path.is_dir() else [path]
        for entry in entries:
            local = entry.relative_to(root)
            if '__pycache__' in local.parts or entry.suffix in ('.pyc', '.pyo'):
                continue
            if entry.is_symlink():
                raise ValueError(f'snapshot cannot follow symlink: {local}')
            if entry.is_file():
                files[local.as_posix()] = sha256(entry.read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def freeze(root: Path, destination: Path, selections: tuple[str, ...]) -> dict:
    root, destination = root.resolve(), destination.absolute()
    if destination.exists():
        raise FileExistsError(destination)
    before = inventory(root, selections)
    stage = Path(tempfile.mkdtemp(prefix=destination.name + '.incomplete-',
                                  dir=destination.parent))
    # An interrupted attempt retains its explicitly named incomplete directory.
    # No previous snapshot is overwritten or recursively deleted.
    for name in before:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    if inventory(root, selections) != before or inventory(stage, selections) != before:
        raise RuntimeError(f'source changed during snapshot; unpublished copy: {stage}')
    image = json.dumps(before, sort_keys=True, separators=(',', ':')).encode()
    manifest = dict(schema='dataset-runtime-snapshot/1', source_root=str(root),
        snapshot_root=str(destination), selections=list(selections),
        digest=sha256(image).hexdigest(), files=before,
        qualification='Frozen bytes only; tests and public B/R remain required.')
    (stage / 'runtime-snapshot.json').write_text(json.dumps(manifest, indent=2) + '\n')
    stage.rename(destination)
    return manifest


def verify(destination: Path) -> dict:
    manifest = json.loads((destination / 'runtime-snapshot.json').read_text())
    actual = inventory(destination, tuple(manifest['selections']))
    image = json.dumps(actual, sort_keys=True, separators=(',', ':')).encode()
    if actual != manifest['files'] or sha256(image).hexdigest() != manifest['digest']:
        raise RuntimeError('validation runtime no longer matches its frozen manifest')
    return manifest
