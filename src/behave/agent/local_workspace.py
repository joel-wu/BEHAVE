"""One rollout's live directory. Only explicitly submitted sources are archived.

The directory is owned by the attempt's lifetime, not individual shell calls.
The inherited snapshot describes archived inputs/submission, never a recoverable
checkpoint of the live directory. Command history is retained separately.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path

from behave.core.artifacts import FileRef, relative_path
from behave.core.records import RecordError

from .workspace import Workspace, resolve_workspace_path


class LocalWorkspace(Workspace):
    local = True

    def __init__(self, store, directory: Path, *, read_only_paths=()):
        super().__init__(store, read_only_paths=read_only_paths)
        # The caller owns this entire rollout directory and its cleanup.
        # Temporary tool files persist across commands but are not submissions.
        self.directory = directory / "workspace"
        self.temporary_directory = directory / "tmp"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.temporary_directory.mkdir(parents=True, exist_ok=True)

    def seed(self, files):
        super().seed(files)
        for file in files:
            target = self.directory / file.reference.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file.data)

    def _path(self, name: str) -> Path:
        """Resolve guest workspace aliases without following links into the host."""
        def readlink(path):
            candidate = self.directory / path
            return os.readlink(candidate) if candidate.is_symlink() else None

        resolved = resolve_workspace_path(name, readlink)
        if resolved is None:
            raise RecordError("artifact-path-invalid", "workspace link escapes or cycles")
        return self.directory / resolved

    def read(self, path: str) -> bytes:
        source = self._path(path)
        try:
            if not stat.S_ISREG(source.stat().st_mode):
                raise RecordError("workspace-special-file", "source must be a regular file")
            return source.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            raise RecordError("workspace-file-missing", f"no workspace file {path!r}") from None

    def _editable(self, path: str) -> Path:
        target = self._path(path)
        if target.relative_to(self.directory).as_posix() in self.read_only_paths:
            raise RecordError("workspace-read-only", "this role cannot change an input file")
        return target

    def write(self, path: str, data: bytes) -> None:
        target = self._editable(path)
        if target.exists() and not target.is_file():
            raise RecordError("workspace-special-file", "source must be a regular file")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except (FileExistsError, NotADirectoryError):
            raise RecordError("artifact-path-conflict", "a parent path is already a file") from None

    def delete(self, path: str) -> None:
        parent, _, name = relative_path(path).rpartition("/")
        target = (self._path(parent) if parent else self.directory) / name
        # Like unlink/rm: resolve parent directories, remove a final symlink
        # itself rather than deleting the file to which it points.
        if target.relative_to(self.directory).as_posix() in self.read_only_paths:
            raise RecordError("workspace-read-only", "this role cannot change an input file")
        try:
            target.unlink()
        except (FileNotFoundError, NotADirectoryError):
            raise RecordError("workspace-file-missing", f"no workspace file {path!r}") from None
        except IsADirectoryError:
            raise RecordError("workspace-special-file", "delete_file requires a file") from None

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted((Path(root) / name).relative_to(self.directory).as_posix()
                            for root, _, names in os.walk(self.directory, followlinks=False)
                            for name in names))

    def archive(self, paths: tuple[str, ...]) -> None:
        files = dict(self._protected)
        for path in sorted(set(paths)):
            try:
                data = self.read(path)
            except RecordError as error:
                if error.code != "workspace-file-missing":
                    raise
                continue
            files[path] = FileRef(path, self.store.put(data))
        self._files = files

    @contextmanager
    def materialize(self):
        """The same directory and build files survive every command in this rollout."""
        yield self.directory
