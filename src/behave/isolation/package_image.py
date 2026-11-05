"""Node-local immutable images of verifier-owned runtime trees.

Preparation copies semantic package files into a content-addressed, read-only
image once per fixed worker batch. Derived ``__pycache__`` files are excluded;
content is identified from the local copy during preparation, not by repeatedly
reading the installation. Updating code requires a new batch.

The same publication machinery handles native tool resources without filtering
any files. Permitted symlinks keep their text and guest namespace meaning; they
are never followed while copying or sealing an image.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .content import _content_digest, _frame
from .model import Binary, ReadOnlyMount
from .preparation_control import acquire_lock, check_preparation

def _cache_root() -> Path:
    from .runtime_environment import runtime_environment

    # Declarations are shared for the batch. Their images must have the same
    # lifetime, not that of whichever command/arm first prepared a resource.
    root = runtime_environment().directory / "images"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) \
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("the Python-package image cache is not private")
    return root


@contextmanager
def _build_lock(root: Path, source: Path):
    key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    path = root / f"{key}.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
                or info.st_nlink != 1:
            raise ValueError("the Python-package image lock is not private")
        acquire_lock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _copy_tree(source: Path, destination: Path, *, allow_symlinks: bool = False,
               exclude_python_bytecode: bool = True, exclude_directories=()) -> None:
    destination.mkdir(mode=0o755)
    for entry in sorted(os.scandir(source), key=lambda item: item.name):
        check_preparation()
        if entry.name in exclude_directories and entry.is_dir(follow_symlinks=False):
            continue
        if exclude_python_bytecode and (
                (entry.name == "__pycache__" and entry.is_dir())
                or (entry.name.endswith((".pyc", ".pyo")) and not entry.is_dir())):
            continue
        item = Path(entry.path)
        info = item.lstat()
        target = destination / entry.name
        if stat.S_ISLNK(info.st_mode):
            if not allow_symlinks:
                raise ValueError(f"a runtime image cannot contain a symlink: {item}")
            target.symlink_to(os.readlink(item))
            continue
        if stat.S_ISDIR(info.st_mode):
            _copy_tree(item, target, allow_symlinks=allow_symlinks,
                       exclude_python_bytecode=exclude_python_bytecode)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(
                f"a Python package image contains a special file: {item}")
        with item.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        target.chmod(0o555 if info.st_mode & 0o111 else 0o444)


def _seal_directories(root: Path) -> None:
    for current, directories, _files in os.walk(root, topdown=False):
        for name in directories:
            directory = Path(current) / name
            if not directory.is_symlink():
                directory.chmod(0o555)
        Path(current).chmod(0o555)


def _tree_mount(source: str, target: str, *, allow_symlinks: bool,
                exclude_python_bytecode: bool, exclude_directories=()) -> ReadOnlyMount:
    from .runtime_environment import runtime_environment

    policy = {"allow_symlinks": allow_symlinks,
              "exclude_python_bytecode": exclude_python_bytecode,
              "exclude_directories": exclude_directories}
    return runtime_environment().prepare(
        ["tree", source, target, policy],
        lambda: _prepare_fixed_tree(source, target, **policy),
        encode=asdict, decode=lambda row: ReadOnlyMount(**row))


def _prepare_fixed_tree(source, target, *, allow_symlinks=False,
                        exclude_python_bytecode=True, exclude_directories=()):
    """Prepare a stable installation, once. No update polling or attestation walk."""
    source = Path(source).resolve()
    policy = {"allow_symlinks": allow_symlinks,
              "exclude_python_bytecode": exclude_python_bytecode,
              "exclude_directories": exclude_directories}
    root = _cache_root()
    with tempfile.TemporaryDirectory(prefix=".building-", dir=root) as temporary:
        staged = Path(temporary) / "tree"
        _copy_tree(source, staged, **policy)
        digest = _content_digest(staged, allow_symlinks=allow_symlinks)
        image = root / digest
        with _build_lock(root, image):
            if image.exists():
                if _content_digest(image, allow_symlinks=allow_symlinks) != digest:
                    raise ValueError(f"prepared runtime image is corrupt: {image}")
            else:
                check_preparation()
                os.rename(staged, image)
                _seal_directories(image)
    return ReadOnlyMount(str(image), target, digest, allow_symlinks=allow_symlinks,
                         prepared_runtime=True)


def runtime_file(source: str, target: str, *, executable=None) -> ReadOnlyMount:
    """One small runtime file copied locally; never used for candidate inputs."""
    from .runtime_environment import runtime_environment

    return runtime_environment().prepare(
        ["file", source, target, executable],
        lambda: _prepare_fixed_file(source, target, executable=executable),
        encode=asdict, decode=lambda row: ReadOnlyMount(**row))


def _prepare_fixed_file(source, target, *, executable):
    source = Path(source).resolve()
    payload = source.read_bytes()
    if executable is None:
        executable = bool(source.stat().st_mode & 0o111)
    mode = 0o555 if executable else 0o444
    digest = hashlib.sha256()
    for part in (b"file", b"executable" if mode == 0o555 else b"data", payload):
        _frame(digest, part)
    expected = digest.hexdigest()
    root = _cache_root()
    image = root / ("file-" + expected)
    with _build_lock(root, image):
        if not image.exists():
            fd, temporary = tempfile.mkstemp(prefix=".file-", dir=root)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    os.fchmod(stream.fileno(), mode)
                check_preparation()
                os.replace(temporary, image)
            finally:
                Path(temporary).unlink(missing_ok=True)
        elif _content_digest(image) != expected:
            raise ValueError(f"prepared runtime file is corrupt: {image}")
    return ReadOnlyMount(str(image), target, expected, prepared_runtime=True)


def python_package(source: str, target: str, *, exclude_directories=()) -> ReadOnlyMount:
    """Return an exact mount of a bytecode-free immutable package image."""
    return _tree_mount(source, target, allow_symlinks=False, exclude_python_bytecode=True,
                       exclude_directories=exclude_directories)


def runtime_tree(source: str, target: str, *, allow_symlinks: bool = False) -> ReadOnlyMount:
    """Freeze a complete trusted tool tree locally, preserving its mount identity.

    The batch owns the prepared image. Updating an installation requires a new
    batch; neither lookup nor launch polls the source tree. Guest targets and
    permitted symlink text stay fixed. Candidate inputs use read_only instead.
    """
    if type(allow_symlinks) is not bool:
        raise TypeError("allow_symlinks must be Boolean")
    return _tree_mount(source, target, allow_symlinks=allow_symlinks,
                       exclude_python_bytecode=False)


def executable_script(source: str, target: str):
    """Freeze a trusted script with executable permissions in the runtime image.

    Python installers need not preserve execute bits on package modules. Execution
    is an explicit runtime-policy choice, not a mutation of the installed package.
    The image digest includes both the exact source bytes and the executable flag.
    """
    mount = runtime_file(source, target, executable=True)
    return Binary(mount.source, target, mount.sha256, prepared_runtime=True)


__all__ = ["executable_script", "python_package", "runtime_tree"]
