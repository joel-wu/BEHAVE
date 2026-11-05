"""Content references and read-only resolution, independent of EDA and agents.

A digest identifies bytes, not permission to read them. Readers stay in the
trusted control plane; an agent receives only its explicitly materialized view.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from .records import RecordError, array, fields, parse_json, text, version


def validate_sha256_digest(value: object) -> None:
    """Check an existing digest's text format; do not hash or read content."""
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RecordError("content-digest-invalid", "expected a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class BlobRef:
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_sha256_digest(self.sha256)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise RecordError("content-size-invalid", "content size must be a nonnegative integer")

    @classmethod
    def of(cls, payload: bytes) -> BlobRef:
        if type(payload) is not bytes:
            raise RecordError("content-bytes-required", "content must be immutable bytes")
        return cls(hashlib.sha256(payload).hexdigest(), len(payload))

    def as_dict(self) -> dict:
        return {"sha256": self.sha256, "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, value: object) -> BlobRef:
        row = fields(value, {"sha256", "size_bytes"})
        return cls(row["sha256"], row["size_bytes"])


class ContentReader(Protocol):
    def read(self, ref: BlobRef) -> bytes: ...


def read_verified(reader: ContentReader, ref: BlobRef) -> bytes:
    payload = reader.read(ref)
    if type(payload) is not bytes or BlobRef.of(payload) != ref:
        raise RecordError("content-identity-mismatch", f"content does not match {ref.sha256}")
    return payload


def owned_store_directory(directory: Path) -> Path:
    """Resolve an explicit, relative ancestor owner; never follow a symlink.

    Internal phase journals may share their enclosing round's immutable CAS.
    The small owner receipt survives reopening and relocation of the whole
    round. Ordinary/exported directories retain the original flat format.
    """
    while directory.name == "objects":
        try:
            root = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return directory
        try:
            try:
                fd = os.open("objects.owner.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=root)
            except FileNotFoundError:
                return directory
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                    raise RecordError("content-owner-invalid", "invalid object-store owner receipt")
                row = fields(parse_json(stream.read(4097)), {"schema", "owner"})
        finally:
            os.close(root)
        version(row["schema"], "behave.object-store-owner/1")
        name = text(row["owner"], "object-store owner")
        parts = PurePosixPath(name)
        target = Path(os.path.abspath(directory.parent / name))
        if (parts.is_absolute() or "\\" in name or parts.as_posix() != name
                or target.name != "objects" or target.parent not in directory.parent.parents):
            raise RecordError("content-owner-invalid", "shared objects must belong to an enclosing round")
        # Ascending strictly also rules out cycles; payloads still use the same
        # regular-file, size and SHA-256 checks in DirectoryReader.read().
        directory = target
    return directory


class DirectoryReader:
    """Read ``directory/<sha256>`` regular files; never discover a repository.

    The explicit directory is trusted configuration, not an agent capability.
    No write operation, symlink following or special-file read is exposed.
    Object size is checked against its reference, not a separate byte quota.
    """

    def __init__(self, directory: str | Path):
        from .runtime_locations import runtime_path
        self.directory = owned_store_directory(runtime_path(directory))

    def read(self, ref: BlobRef) -> bytes:
        if type(ref) is not BlobRef:
            raise TypeError("read requires BlobRef")
        try:
            root = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                descriptor = os.open(
                    ref.sha256, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root,
                )
            finally:
                os.close(root)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise RecordError("content-not-regular", "content must be a regular file")
                if info.st_size != ref.size_bytes:
                    raise RecordError("content-identity-mismatch", "content size differs from its reference")
                payload = stream.read(ref.size_bytes + 1)
        except OSError as error:
            raise RecordError("content-unavailable", f"cannot read content {ref.sha256}") from error
        if BlobRef.of(payload) != ref:
            raise RecordError("content-identity-mismatch", "content digest differs from its reference")
        return payload


def relative_path(value: object) -> str:
    path = text(value, "artifact path")
    parts = PurePosixPath(path)
    if "\\" in path or parts.is_absolute() or parts.as_posix() != path or any(
        part in ("", ".", "..") for part in path.split("/")
    ):
        raise RecordError("artifact-path-invalid", "expected a normalized relative POSIX file path")
    return path


def relative_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    if type(paths) is not tuple:
        raise TypeError("paths must be a tuple")
    ordered = tuple(sorted(relative_path(path) for path in paths))
    names = set(ordered)
    if len(names) != len(ordered) or any(
        str(parent) in names for name in ordered for parent in PurePosixPath(name).parents
    ):
        raise RecordError("artifact-path-conflict", "file paths duplicate or overlap a parent file")
    return ordered


@dataclass(frozen=True, slots=True)
class FileRef:
    path: str
    content: BlobRef

    def __post_init__(self) -> None:
        relative_path(self.path)
        if type(self.content) is not BlobRef:
            raise TypeError("file content requires BlobRef")

    def as_dict(self) -> dict:
        return {"path": self.path, "content": self.content.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> FileRef:
        row = fields(value, {"path", "content"})
        return cls(row["path"], BlobRef.from_dict(row["content"]))


def file_set(files: tuple[FileRef, ...]) -> tuple[FileRef, ...]:
    if type(files) is not tuple or any(type(item) is not FileRef for item in files):
        raise TypeError("files must be a tuple of FileRef")
    relative_paths(tuple(item.path for item in files))
    return tuple(sorted(files, key=lambda item: item.path))


@dataclass(frozen=True, slots=True)
class ArtifactSelection:
    """Explicit workspace paths, before capture into a SourceArtifact."""

    files: tuple[str, ...]
    entry_file: str
    entry_symbol: str

    def __post_init__(self) -> None:
        names = relative_paths(self.files)
        object.__setattr__(self, "files", names)
        relative_path(self.entry_file)
        text(self.entry_symbol, "entry symbol")
        if self.entry_file not in names:
            raise RecordError("artifact-entry-missing", "entry_file must be one of the selected paths")

    def as_dict(self) -> dict:
        return {"files": list(self.files), "entry_file": self.entry_file, "entry_symbol": self.entry_symbol}

    @classmethod
    def from_dict(cls, value: object) -> ArtifactSelection:
        row = fields(value, {"files", "entry_file", "entry_symbol"})
        return cls(tuple(array(row["files"])), row["entry_file"], row["entry_symbol"])


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    """An explicit source bundle; loader/build settings belong to judge policy."""

    files: tuple[FileRef, ...]
    entry_file: str
    entry_symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", file_set(self.files))
        relative_path(self.entry_file)
        text(self.entry_symbol, "entry symbol")
        if self.entry_file not in {item.path for item in self.files}:
            raise RecordError("artifact-entry-missing", "entry_file must name a file in the source bundle")

    def as_dict(self) -> dict:
        return {
            "files": [item.as_dict() for item in self.files],
            "entry_file": self.entry_file, "entry_symbol": self.entry_symbol,
        }

    @classmethod
    def from_dict(cls, value: object) -> SourceArtifact:
        row = fields(value, {"files", "entry_file", "entry_symbol"})
        return cls(tuple(FileRef.from_dict(item) for item in array(row["files"])),
                   row["entry_file"], row["entry_symbol"])


@dataclass(frozen=True, slots=True)
class FileData:
    """One verified file's bytes, with no store handle or host path."""

    reference: FileRef
    data: bytes

    def __post_init__(self) -> None:
        if type(self.reference) is not FileRef or BlobRef.of(self.data) != self.reference.content:
            raise RecordError("content-identity-mismatch", "file bytes do not match their reference")


def file_references(files: tuple[FileData, ...]) -> tuple[FileRef, ...]:
    if type(files) is not tuple or any(type(item) is not FileData for item in files):
        raise TypeError("loaded files must be a tuple of FileData")
    return file_set(tuple(item.reference for item in files))


@dataclass(frozen=True, slots=True)
class LoadedSource:
    """A source bundle and its verified bytes, independent of task publication."""

    artifact: SourceArtifact
    files: tuple[FileData, ...]

    def __post_init__(self) -> None:
        if type(self.artifact) is not SourceArtifact or file_references(self.files) != self.artifact.files:
            raise RecordError("publication-source-view-invalid", "loaded files differ from the source bundle")
