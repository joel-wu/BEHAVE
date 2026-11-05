"""Small, explicit I/O boundary for development batches.

Run against local files; optionally transfer one completed tar bundle. This
module does not schedule jobs, score tasks, retry work, or remove any files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile


def require_node_local(path: Path) -> Path:
    root = Path('/tmp').resolve()
    path = Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f'live Analysis paths must be beneath node-local /tmp: {path}')
    ancestor = next(item for item in (path, *path.parents) if item.exists())
    if ancestor.stat().st_dev != root.stat().st_dev:
        raise ValueError(f'Analysis path is not on the local /tmp filesystem: {path}')
    return path


def new_directory(prefix='behave-batch-') -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir='/tmp'))


def environment(checkout: Path, scratch: Path, eda: Path) -> dict[str, str]:
    """One environment for a batch; all its workers share the runtime cache."""
    checkout, scratch = require_node_local(checkout), require_node_local(scratch)
    return dict(
        PATH=f'{eda}/bin:/usr/bin:/bin', PYTHONPATH=f'{checkout}/src:{checkout}',
        PYTHONDONTWRITEBYTECODE='1', BEHAVE_EDA_ROOT=str(eda),
        TMPDIR=str(scratch), BEHAVE_SCRATCH_DIR=str(scratch),
        BEHAVE_RUNTIME_DIR=str(scratch / 'runtime'),
        BEHAVE_ISOLATION_STARTUP_DIR=f'/tmp/behave-isolation-startup-{os.getuid()}',
        LC_ALL='C.UTF-8',
    )


def _copy(source: Path, target: Path) -> tuple[str, int]:
    digest, count = hashlib.sha256(), 0
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        while block := incoming.read(8 << 20):
            outgoing.write(block)
            digest.update(block)
            count += len(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    return digest.hexdigest(), count


def save(source: Path, destination: Path) -> dict:
    """Pack completed local results, then publish one tar and its small receipt.

    The destination is a NEW directory. A failed transfer keeps its .partial
    file and has no completion receipt; existing results are never overwritten.
    """
    source = require_node_local(source)
    if not source.is_dir():
        raise ValueError('bundle source must be a directory')
    destination = Path(destination).resolve()
    if destination.is_relative_to(source):
        raise ValueError('bundle destination must be outside its source')
    if destination.exists():
        raise FileExistsError(destination)
    local = new_directory('behave-pack-') / 'bundle.tar'
    count = 0
    with tarfile.open(local, 'w', dereference=True) as archive:
        for path in source.rglob('*'):
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f'bundle accepts regular files/directories only: {path}')
            archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
            count += stat.S_ISREG(mode)
    destination.mkdir(parents=True, exist_ok=False)
    pending = destination / 'bundle.tar.partial'
    digest, size = _copy(local, pending)
    pending.rename(destination / 'bundle.tar')
    receipt = dict(schema='behave.local-batch-bundle/1', sha256=digest,
                   size_bytes=size, files=count)
    with (destination / 'bundle.json').open('x') as output:
        output.write(json.dumps(receipt, indent=2) + '\n')
        output.flush()
        os.fsync(output.fileno())
    return dict(directory=str(destination), **receipt)


def load(bundle: Path, destination: Path) -> Path:
    """Read a packed input once, verify locally, then unpack only below /tmp."""
    destination = require_node_local(destination)
    if destination.exists():
        raise FileExistsError(destination)
    bundle = Path(bundle)
    receipt = json.loads((bundle / 'bundle.json').read_text())
    if receipt.get('schema') != 'behave.local-batch-bundle/1':
        raise ValueError('unknown bundle receipt')
    local = new_directory('behave-unpack-') / 'bundle.tar'
    digest, size = _copy(bundle / 'bundle.tar', local)
    if (digest, size) != (receipt['sha256'], receipt['size_bytes']):
        raise ValueError('bundle checksum or size differs')
    destination.mkdir(parents=True, exist_ok=False)
    count = 0
    with tarfile.open(local, 'r|') as archive:
        for item in archive:
            archive.members.clear()
            name = Path(item.name)
            if name.is_absolute() or '..' in name.parts or not (item.isdir() or item.isfile()):
                raise ValueError(f'unsafe bundle member: {item.name}')
            target = destination / name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 8 << 20)
                target.chmod(item.mode & 0o777)
                count += 1
    if count != receipt['files']:
        raise ValueError('bundle file count differs')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('save', 'load'))
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    result = (save if args.action == 'save' else load)(args.source, args.destination)
    print(json.dumps(result if isinstance(result, dict) else dict(directory=str(result))))


if __name__ == '__main__':
    main()
