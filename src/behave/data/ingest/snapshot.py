"""Import an explicitly selected local source snapshot, without executing it.

The SourceRecord pins paths and bytes before import. Origin/revision/licence are
attributed declarations, not a Git/remote-authenticity or publication decision.
No directory discovery, extension fallback, network access or source evaluation
occurs here. The source record is published only after all its dependencies.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from behave.core.artifacts import BlobRef, FileData, FileRef, file_set, read_verified
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore

from ..drafts import SourceRecord


def _check(source: SourceRecord) -> None:
    if type(source) is not SourceRecord:
        raise TypeError("source import requires a SourceRecord")


def _read(root: int, file: FileRef) -> FileData:
    """Walk only declared relative components; never follow a source symlink."""
    directory = os.dup(root)
    try:
        parts = file.path.split("/")
        for part in parts[:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise RecordError("source-import-not-regular", "selected source must be a regular file")
            if info.st_size != file.content.size_bytes:
                raise RecordError("source-import-changed", "selected source size differs from its pinned record")
            data = stream.read(file.content.size_bytes + 1)
        if BlobRef.of(data) != file.content:
            raise RecordError("source-import-changed", "selected source bytes differ from their pinned record")
        return FileData(file, data)
    except OSError as error:
        raise RecordError("source-import-unavailable", "a selected source path cannot be read safely") from error
    finally:
        os.close(directory)


def read_files(files: tuple[FileRef, ...], root: str | Path) -> tuple[FileData, ...]:
    """Read an explicit, pinned file set without following any path symlinks."""
    files = file_set(files)
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        raise RecordError("source-import-unavailable", "source root cannot be opened safely") from error
    try:
        return tuple(_read(directory, file) for file in files)
    finally:
        os.close(directory)


def read_source(source: SourceRecord, root: str | Path) -> tuple[FileData, ...]:
    """Validate selected bytes without creating a store or changing the source."""
    _check(source)
    return read_files(source.selected.files, root)


def ingest_source(source: SourceRecord, root: str | Path, objects: DirectoryStore) -> BlobRef:
    """Publish one verified closure; a complete retry needs no original checkout.

    Existing but corrupt/incomplete closures are errors, never silently repaired.
    Interrupted pre-publication imports may leave unreferenced immutable blobs;
    these are not completed source imports or publication approvals.
    """
    if type(objects) is not DirectoryStore:
        raise TypeError("source import requires a DirectoryStore")
    _check(source)
    payload = source.to_bytes()
    reference = BlobRef.of(payload)
    if source.acquisition is not None:
        from .fetch_evidence import source_acquisition_closure

        # Local bytes cannot stand in for a missing remote provenance receipt.
        source_acquisition_closure(objects, source)
    if os.path.lexists(objects.directory / reference.sha256):
        read_verified(objects, reference)
        for file in source.selected.files:
            read_verified(objects, file.content)
        return reference
    files = read_source(source, root)
    for file in files:
        objects.put(file.data)
    return objects.put(payload)
