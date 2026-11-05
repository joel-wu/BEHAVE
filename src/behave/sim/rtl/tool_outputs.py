"""Publish validated tool artifacts and retain complete command logs.

The executor calls these only after supervising the guest process tree. Path,
archive and symlink checks stay here; this module makes no execution decisions.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import stat
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


def retain_command_logs(source: Path, output_root: Path, groups) -> str | None:
    """Export only declared command logs after the isolated process tree stops.

    Logs stay outside the input workspace so a later stage does not copy them
    back into /work. During execution all writes go to node-local scratch.
    The guest rotates at its per-file resource limit, not a total log quota.
    Join those parts here, outside that limit, preserving the public single-file
    interface and byte order without accumulating the stream in memory.
    """
    destination = None
    available = {path.name for path in source.iterdir()}
    for group_index, group in enumerate(groups):
        for command_index, _command in enumerate(group.commands):
            for stream in ("stdout", "stderr"):
                name = f"{group_index}-{command_index}.{stream}"
                parts = [(0, name)] if name in available else []
                pattern = re.compile(re.escape(name) + r"\.part-([1-9][0-9]*)\Z")
                parts.extend((int(match[1]), part) for part in available
                             if (match := pattern.fullmatch(part)) is not None)
                parts.sort()
                if not parts:
                    continue  # Commands not reached, e.g. after a compile failure.
                if [index for index, _ in parts] != list(range(len(parts))):
                    raise ValueError("an isolated command log has missing parts")
                if destination is None:
                    destination = Path(tempfile.mkdtemp(
                        prefix=output_root.name + "-logs-", dir=output_root.parent))
                with (destination / name).open("wb") as writer:
                    for _, part in parts:
                        descriptor = os.open(source / part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                        with os.fdopen(descriptor, "rb") as reader:
                            if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                                raise ValueError("an isolated command log is not a regular file")
                            shutil.copyfileobj(reader, writer)
    return None if destination is None else str(destination)


def paths_are_disjoint(paths: tuple[str, ...]) -> bool:
    """Whether no declared output is another declared output's descendant."""

    parts = [PurePosixPath(path).parts for path in paths]
    return not any(
        left != right and len(left) < len(right) and right[:len(left)] == left
        for left in parts for right in parts
    )


@contextmanager
def _safe_parent(root: Path, relative: str, *, create: bool = True):
    """Open/create an output parent without following a host-side symlink.

    Candidate bytes never choose ``relative`` -- it is a trusted declaration -- but
    the supplied output tree may already contain links.  Resolving those links while
    materialising a result would turn a relative declaration into authority over a
    different host path.  Directory descriptors plus ``O_NOFOLLOW`` keep lookup below
    the exact root for the whole operation.
    """

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def write_output(root: Path, relative: str, payload: bytes) -> None:
    """Atomically replace one regular output below ``root`` without link traversal."""

    with _safe_parent(root, relative) as (parent, name):
        partial = f".{name}.isolation-{os.getpid()}-{os.urandom(8).hex()}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(partial, flags, 0o600, dir_fd=parent)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("a declared RTL output could not be staged")
                view = view[written:]
            os.fchmod(descriptor, 0o644)
        except BaseException:
            os.close(descriptor)
            try:
                os.unlink(partial, dir_fd=parent)
            except OSError:
                pass
            raise
        else:
            os.close(descriptor)
        try:
            os.replace(partial, name, src_dir_fd=parent, dst_dir_fd=parent)
        except BaseException:
            try:
                os.unlink(partial, dir_fd=parent)
            except OSError:
                pass
            raise


def remove_output(root: Path, relative: str) -> None:
    """Remove one absent declared file without following its path or target."""

    try:
        with _safe_parent(root, relative, create=False) as (parent, name):
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def install_tree(root: Path, relative: str, staged: Path) -> None:
    """Install one already-validated tree below ``root`` without link traversal."""

    with _safe_parent(root, relative) as (parent, name):
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("a declared RTL output tree already exists in the host workspace")
        os.rename(staged, name, dst_dir_fd=parent)


def unpack_tree(
    payload: bytes,
    destination: Path,
    *,
    file_limit: int,
) -> None:
    """Extract a regular-file archive without trusting archive path semantics."""

    seen: set[str] = set()
    used = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if (not member.name or path.is_absolute() or ".." in path.parts
                        or str(path) != member.name or member.name in seen):
                    raise ValueError("an output tree archive contains an unsafe path")
                seen.add(member.name)
                target = destination / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                    continue
                if not member.isreg():
                    raise ValueError(
                        "an output tree archive contains a non-regular object")
                used += member.size
                if used > file_limit:
                    raise ValueError("an output tree exceeds the file budget")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("an output tree archive lost a file payload")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (OSError, tarfile.TarError) as error:
        raise ValueError("an output tree archive is malformed") from error
