"""Versioned task files shared by file tools, shell execution and submission."""

from __future__ import annotations

import os
import posixpath
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from behave.core.artifacts import BlobRef, FileData, FileRef, file_set, read_verified, relative_path, relative_paths
from behave.core.paths import scratch_root
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, version
from behave.core.storage import DirectoryStore


def resolve_workspace_path(path: str, readlink: Callable[[str], str | None]) -> str | None:
    """Resolve aliases with sandbox /workspace semantics, never host semantics."""
    original = relative_path(path)

    def resolve(parts, active):
        resolved = []
        for part in parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not resolved:
                    return None
                resolved.pop()
                continue
            resolved.append(part)
            prefix = "/".join(resolved)
            target = readlink(prefix)
            if target is None:
                continue
            if prefix in active:
                return None
            if target == "/workspace":
                target = ""
            elif target.startswith("/workspace/"):
                target = target[len("/workspace/"):]
            elif target.startswith("/"):
                return None
            else:
                target = posixpath.join(posixpath.dirname(prefix), target)
            resolved = resolve(target.split("/"), active | {prefix})
            if resolved is None:
                return None
        return resolved

    resolved = resolve(original.split("/"), frozenset())
    return None if resolved is None else "/".join(resolved)


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    files: tuple[FileRef, ...]
    directories: tuple[str, ...] = ()
    symlinks: tuple[tuple[str, str], ...] = ()
    executables: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", file_set(self.files))
        object.__setattr__(self, "directories", tuple(sorted(relative_path(p) for p in self.directories)))
        object.__setattr__(self, "symlinks", tuple(sorted((relative_path(p), target) for p, target in self.symlinks)))
        object.__setattr__(self, "executables", tuple(sorted(relative_path(p) for p in self.executables)))
        leaves = relative_paths(tuple(file.path for file in self.files) + tuple(p for p, _ in self.symlinks))
        if any(directory == leaf or directory.startswith(leaf + "/")
               for directory in self.directories for leaf in leaves):
            raise RecordError("artifact-path-conflict", "directory overlaps a file or symlink")
        if any(type(target) is not str or not target or "\x00" in target for _, target in self.symlinks):
            raise RecordError("artifact-path-invalid", "invalid workspace symlink target")
        if not set(self.executables) <= {file.path for file in self.files}:
            raise RecordError("artifact-path-invalid", "executable entry is not a workspace file")

    def to_bytes(self) -> bytes:
        row = {"schema": "behave.workspace/1", "files": [file.as_dict() for file in self.files]}
        if self.directories or self.symlinks or self.executables:
            row.update(directories=list(self.directories), symlinks=[list(link) for link in self.symlinks],
                       executables=list(self.executables))
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, data: bytes) -> WorkspaceSnapshot:
        row = parse_json(data)
        extra = {"directories", "symlinks", "executables"} if type(row) is dict and "directories" in row else set()
        row = fields(row, {"schema", "files", *extra})
        version(row["schema"], "behave.workspace/1")
        result = cls(tuple(FileRef.from_dict(file) for file in array(row["files"])),
                     tuple(array(row.get("directories", []))),
                     tuple(tuple(array(link)) for link in array(row.get("symlinks", []))),
                     tuple(array(row.get("executables", []))))
        if result.to_bytes() != data:
            raise RecordError("workspace-not-canonical", "workspace snapshot must be canonical")
        return result

    def file(self, path: str) -> FileRef | None:
        """Resolve source aliases inside the workspace without following host links."""
        original = relative_path(path)
        resolved = resolve_workspace_path(original, dict(self.symlinks).get)
        if resolved is None:
            return None
        found = next((file for file in self.files if file.path == resolved), None)
        return None if found is None else FileRef(original, found.content)


class Workspace:
    """Single-coordinator mutable index over immutable bytes."""

    local = False

    def __init__(self, store: DirectoryStore, *, read_only_paths: tuple[str, ...] = ()):
        self.store = store
        self.read_only_paths = relative_paths(read_only_paths)
        self._protected: dict[str, FileRef] = {}
        self._files: dict[str, FileRef] = {}
        self._directories: tuple[str, ...] = ()
        self._symlinks: tuple[tuple[str, str], ...] = ()
        self._executables: tuple[str, ...] = ()

    @cached_property
    def temporary_directory(self) -> Path:
        """Private shell scratch for this workspace, never part of its checkpoint."""
        self._temporary = tempfile.TemporaryDirectory(prefix="behave-agent-tmp-", dir=scratch_root())
        return Path(self._temporary.name)

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(tuple(self._files.values()), self._directories, self._symlinks, self._executables)

    def archive(self, paths: tuple[str, ...]) -> None:
        """Checkpointed roles already retain their complete source revisions."""

    def paths(self) -> tuple[str, ...]:
        return tuple(file.path for file in self.snapshot().files)

    def checkpoint(self) -> tuple[BlobRef, tuple[BlobRef, ...]]:
        snapshot = self.snapshot()
        root = self.store.put(snapshot.to_bytes())
        return root, (root, *(file.content for file in snapshot.files))

    def restore(self, reference: BlobRef) -> None:
        snapshot = WorkspaceSnapshot.from_bytes(read_verified(self.store, reference))
        files = {file.path: file for file in snapshot.files}
        if any(files.get(path) != file for path, file in self._protected.items()):
            raise RecordError("workspace-read-only-changed", "checkpoint changed a protected input")
        for file in snapshot.files:
            read_verified(self.store, file.content)
        self._files = files
        self._directories, self._symlinks, self._executables = (
            snapshot.directories, snapshot.symlinks, snapshot.executables)

    def seed(self, files: tuple[FileData, ...]) -> None:
        if self._files:
            raise RecordError("workspace-not-empty", "seed requires an empty workspace")
        snapshot = WorkspaceSnapshot(tuple(file.reference for file in files))
        references = {file.path: file for file in snapshot.files}
        if any(path not in references for path in self.read_only_paths):
            raise RecordError("workspace-read-only-missing", "protected paths must be present in the initial files")
        for file in files:
            if self.store.put(file.data) != file.reference.content:
                raise RecordError("content-identity-mismatch", "seed bytes disagree with the declared file")
        self._files = references
        self._protected = {path: references[path] for path in self.read_only_paths}

    def read(self, path: str) -> bytes:
        path = relative_path(path)
        file = self.snapshot().file(path)
        if file is None:
            raise RecordError("workspace-file-missing", f"no workspace file {path!r}")
        return read_verified(self.store, file.content)

    def write(self, path: str, data: bytes) -> None:
        file = FileRef(relative_path(path), BlobRef.of(data))
        if path in self.read_only_paths:
            raise RecordError("workspace-read-only", "this role cannot change an input file")
        proposed = {**self._files, path: file}
        links = tuple(link for link in self._symlinks if link[0] != path)
        WorkspaceSnapshot(tuple(proposed.values()), self._directories, links, self._executables)
        self.store.put(data)
        self._files = proposed
        self._symlinks = links

    def delete(self, path: str) -> None:
        path = relative_path(path)
        if path in self.read_only_paths:
            raise RecordError("workspace-read-only", "this role cannot delete an input file")
        if path not in self._files and path not in dict(self._symlinks):
            raise RecordError("workspace-file-missing", f"no workspace file {path!r}")
        self._files = {name: file for name, file in self._files.items() if name != path}
        self._symlinks = tuple(link for link in self._symlinks if link[0] != path)
        self._executables = tuple(name for name in self._executables if name != path)

    def import_directory(self, root: Path) -> None:
        """Capture shell edits after its entire process tree has stopped."""
        files, directories, links, executables = [], [], [], []
        for current, dirs, names in os.walk(root, followlinks=False):
            current = Path(current)
            for name in (*dirs, *names):
                source = current / name
                path = source.relative_to(root).as_posix()
                mode = source.lstat().st_mode
                if stat.S_ISLNK(mode):
                    links.append((path, os.readlink(source)))
                    if name in dirs:
                        dirs.remove(name)
                elif stat.S_ISDIR(mode):
                    directories.append(path)
                elif stat.S_ISREG(mode):
                    with source.open("rb") as stream:
                        files.append(FileRef(path, self.store.put(stream.read())))
                    if mode & 0o111:
                        executables.append(path)
                else:
                    raise RecordError("workspace-special-file", "persistent workspace supports files, directories and symlinks")
        snapshot = WorkspaceSnapshot(tuple(files), tuple(directories), tuple(links), tuple(executables))
        self.restore(self.store.put(snapshot.to_bytes()))

    @contextmanager
    def materialize(self) -> Iterator[Path]:
        """Materialize under the configured local root, not NFS cwd or TMPDIR."""
        with tempfile.TemporaryDirectory(prefix="behave-sources-", dir=scratch_root()) as temporary:
            root = Path(temporary)
            for directory in self._directories:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for file in self.snapshot().files:
                target = root / file.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(read_verified(self.store, file.content))
                os.chmod(target, 0o755 if file.path in self._executables else 0o644)
            for path, link in self._symlinks:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
            yield root
