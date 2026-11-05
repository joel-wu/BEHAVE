"""Content identities and explicit Python-runtime policy construction."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import sysconfig
from dataclasses import asdict, replace
from pathlib import Path

from .content_attestation import reusable_content_digest
from .model import Binary, MaskedPath, ReadOnlyMount
from .preparation_control import check_preparation


def _frame(hasher: hashlib._Hash, payload: bytes) -> None:
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def _content_digest(
    root: Path,
    *,
    allow_symlinks: bool = False,
    exclude_python_bytecode: bool = False,
    exclude_directories: tuple[str, ...] = (),
) -> str:
    """Digest a regular file or a directory tree under an explicit link policy.

    Symlinks and special files are not immutable byte inputs: their meaning depends on
    a namespace outside the mounted object.  Devices have a separate typed policy.
    """
    check_preparation()
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"read-only input is a symlink: {root}")
    digest = hashlib.sha256()
    if stat.S_ISREG(info.st_mode):
        _frame(digest, b"file")
        _frame(digest, b"executable" if info.st_mode & 0o111 else b"data")
        _frame(digest, root.read_bytes())
        return digest.hexdigest()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"read-only input is neither a file nor directory: {root}")
    _frame(digest, b"tree")
    for current, directories, files in os.walk(root, followlinks=False):
        check_preparation()
        directories.sort()
        files.sort()
        current_path = Path(current)
        if current_path == root:
            directories[:] = [name for name in directories if name not in exclude_directories]
        if exclude_python_bytecode:
            directories[:] = [
                name for name in directories if name != "__pycache__"]
            files = [
                name for name in files
                if not name.endswith((".pyc", ".pyo"))]
        # ``os.walk(..., followlinks=False)`` still puts a symlink to a
        # directory in ``directories``.  Remove it before the next iteration,
        # and hash the link text rather than the object it happens to name.
        linked_dirs = [
            name for name in directories
            if stat.S_ISLNK((current_path / name).lstat().st_mode)
        ]
        directories[:] = [name for name in directories if name not in linked_dirs]
        for name in [*linked_dirs, *directories, *files]:
            check_preparation()
            item = current_path / name
            item_info = item.lstat()
            relative = item.relative_to(root).as_posix().encode("utf-8")
            if stat.S_ISLNK(item_info.st_mode):
                if not allow_symlinks:
                    raise ValueError(f"read-only tree contains a symlink: {item}")
                _frame(digest, b"symlink")
                _frame(digest, relative)
                _frame(digest, os.readlink(item).encode("utf-8"))
                continue
            if stat.S_ISDIR(item_info.st_mode):
                _frame(digest, b"dir")
                _frame(digest, relative)
            elif stat.S_ISREG(item_info.st_mode):
                _frame(digest, b"file")
                _frame(digest, relative)
                _frame(
                    digest,
                    b"executable" if item_info.st_mode & 0o111 else b"data",
                )
                _frame(digest, item.read_bytes())
            else:
                raise ValueError(f"read-only tree contains a special file: {item}")
    return digest.hexdigest()


def content_digest(path: str, *, allow_symlinks: bool = False) -> str:
    """Digest every byte and executable bit visible through one declared mount.

    This public primitive always reads the bytes.  Verifier-owned runtime builders may
    separately opt in to reusable attestation through :func:`read_only`; submitted
    inputs do not.  A directory's own mtime is never accepted as a tree identity.
    """
    root = Path(path)
    return _content_digest(root, allow_symlinks=allow_symlinks)


def read_only(
    source: str,
    target: str,
    *,
    allow_symlinks: bool = False,
    reuse_attestation: bool = False,
) -> ReadOnlyMount:
    from .runtime_environment import active_runtime_environment

    environment = active_runtime_environment() if reuse_attestation else None
    if environment is not None:
        def prepare():
            from .package_image import runtime_file

            path = Path(source)
            if path.is_file():
                return runtime_file(source, target)
            # Node system directories are fixed by the batch's installation.
            return replace(_read_only(source, target, allow_symlinks=allow_symlinks),
                           prepared_runtime=True)
        return environment.prepare(
            ["mount", source, target, allow_symlinks],
            prepare,
            encode=asdict, decode=lambda row: ReadOnlyMount(**row))
    return _read_only(source, target, allow_symlinks=allow_symlinks,
                      reuse_attestation=reuse_attestation)


def _read_only(source, target, *, allow_symlinks=False, reuse_attestation=False):
    if type(reuse_attestation) is not bool:
        raise TypeError("reuse_attestation must be Boolean")
    source = os.path.realpath(source)
    digest = (
        reusable_content_digest(
            source,
            allow_symlinks=allow_symlinks,
            byte_digest=_content_digest,
        )
        if reuse_attestation
        else content_digest(source, allow_symlinks=allow_symlinks)
    )
    return ReadOnlyMount(
        source,
        target,
        digest,
        allow_symlinks=allow_symlinks,
        reuse_attestation=reuse_attestation,
    )


def binary(source: str, target: str, *, reuse_attestation: bool = False) -> Binary:
    from .runtime_environment import active_runtime_environment

    environment = active_runtime_environment() if reuse_attestation else None
    if environment is not None:
        def prepare():
            from .package_image import runtime_file

            if not os.access(source, os.X_OK):
                raise ValueError(f"declared binary is not executable: {source}")
            mount = runtime_file(source, target)
            return Binary(mount.source, target, mount.sha256, prepared_runtime=True)
        return environment.prepare(
            ["binary", source, target],
            prepare,
            encode=asdict, decode=lambda row: Binary(**row))
    return _binary(source, target, reuse_attestation=reuse_attestation)


def _binary(source, target, *, reuse_attestation=False):
    if type(reuse_attestation) is not bool:
        raise TypeError("reuse_attestation must be Boolean")
    source = os.path.realpath(source)
    if not os.access(source, os.X_OK):
        raise ValueError(f"declared binary is not executable: {source}")
    digest = (
        reusable_content_digest(
            source, allow_symlinks=False, byte_digest=_content_digest)
        if reuse_attestation
        else content_digest(source)
    )
    return Binary(source, target, digest, reuse_attestation=reuse_attestation)


def _system_executable(name: str) -> str:
    """Resolve a required tool without accepting authority from ambient PATH."""
    resolved = shutil.which(name, path=os.defpath)
    if resolved is None:
        raise RuntimeError(f"required system tool {name!r} is unavailable")
    path = Path(resolved).resolve()
    if not path.is_file():
        raise RuntimeError(
            f"system tool {name!r} is not a regular file: {path}")
    return str(path)


def _ldd_closure(files: tuple[str, ...]) -> tuple[str, ...]:
    command = [_system_executable("ldd"), *files]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.defpath},
    )
    if completed.returncode:
        raise RuntimeError(f"ldd could not resolve the declared runtime: {completed.stderr.strip()}")
    found: set[str] = set()
    for line in completed.stdout.splitlines():
        fields = line.strip().split()
        candidates = []
        if "=>" in fields:
            index = fields.index("=>")
            if index + 1 < len(fields):
                candidates.append(fields[index + 1])
        elif fields:
            candidates.append(fields[0])
        for candidate in candidates:
            if candidate.startswith("/") and os.path.isfile(candidate):
                # Preserve the loader-visible path as the guest target.  ``read_only``
                # resolves only the host source.  Replacing /lib64/ld-linux... by its
                # real path makes an otherwise present ELF binary fail with ENOENT.
                # A relocatable runtime may nevertheless report an absolute path with
                # lexical ``..`` components (for example ``bin/../lib/libpython``).
                # Guest paths are required to be normalized, and removing only those
                # lexical components preserves the loader-visible symlink itself.
                found.add(os.path.normpath(candidate))
    return tuple(sorted(found))


def native_runtime(
    files: tuple[str, ...],
) -> tuple[ReadOnlyMount, ...]:
    from .runtime_environment import runtime_environment

    return runtime_environment().prepare(
        ["native-runtime", files], lambda: _native_runtime(files),
        encode=lambda mounts: [asdict(item) for item in mounts],
        decode=lambda rows: tuple(ReadOnlyMount(**item) for item in rows))


def _native_runtime(files):
    """Freeze the dynamic-library closure required by trusted native files.

    A Python package may carry a shared object loaded later through ``ctypes`` rather
    than as a Python extension.  Such an object is not part of CPython's own ``ldd``
    closure.  The caller names the trusted files explicitly; this function exposes only
    their resolved loader dependencies at their loader-visible absolute paths.
    """
    if type(files) is not tuple or not files \
            or any(type(path) is not str for path in files):
        raise TypeError("native_runtime needs a nonempty tuple of file paths")
    resolved = tuple(sorted(os.path.realpath(path) for path in files))
    return tuple(read_only(
        dependency, dependency, reuse_attestation=True)
                 for dependency in _ldd_closure(resolved))


def python_runtime(
    executable: str = sys.executable,
) -> tuple[tuple[Binary, ...], tuple[ReadOnlyMount, ...], tuple[MaskedPath, ...]]:
    from .runtime_environment import runtime_environment

    def encode(runtime):
        return [[asdict(item) for item in group] for group in runtime]

    def decode(groups):
        return tuple(tuple(kind(**row) for row in rows)
                     for kind, rows in zip((Binary, ReadOnlyMount, MaskedPath), groups))

    return runtime_environment().prepare(
        ["python-runtime", executable], lambda: _python_runtime(executable),
        encode=encode, decode=decode)


def _python_runtime(executable):
    """The explicit runtime closure for the current CPython interpreter.

    This is a trusted policy builder, not backend discovery.  Its returned mounts are
    frozen in the caller's ``IsolationSpec``.  The guest never sees the virtualenv,
    host site-packages, a ``.pth`` target, or the parent repository.
    """
    executable = os.path.realpath(executable)
    stdlib = os.path.realpath(sysconfig.get_path("stdlib"))
    extension_modules = []
    for current, directories, files in os.walk(stdlib):
        directories[:] = [name for name in directories
                           if name not in {"site-packages", "dist-packages", "__pycache__"}]
        extension_modules.extend(str(Path(current) / name) for name in files if name.endswith(".so"))
    dependencies = _ldd_closure((executable, *extension_modules))
    guest_prefix = "/runtime/python"
    guest_binary = f"{guest_prefix}/bin/python"
    binaries = (binary(executable, guest_binary, reuse_attestation=True),)
    # CPython may create ``__pycache__`` entries in a user-owned installation
    # while another verifier process is freezing the same standard library.
    # Those files are derived runtime state, not Python's source semantics, and
    # including them makes two concurrent launches invalidate each other's
    # otherwise identical policy.  Mount the same bytecode-free immutable image
    # used for verifier-owned packages.  The image still binds every source,
    # extension and executable bit. A new batch after an installation update
    # obtains the updated content identity.
    from .package_image import python_package

    mounts = [python_package(
        stdlib,
        f"{guest_prefix}/lib/python{sys.version_info.major}.{sys.version_info.minor}",
        exclude_directories=("site-packages", "dist-packages"),
    )]
    # ``ldd`` gives the interpreter enough runtime to start, but build systems which
    # embed Python locate the development shared object through ``sys.prefix/lib``.
    # The host interpreter may keep that object under its immutable base installation
    # (outside a virtual environment), so expose the exact file under the guest prefix
    # as well.  This is still a byte-bound runtime input; it is not an ambient library
    # search path.
    libdir = sysconfig.get_config_var("LIBDIR")
    library_names = tuple(dict.fromkeys(
        name for name in (
            sysconfig.get_config_var("LDLIBRARY"),
            sysconfig.get_config_var("INSTSONAME"),
        )
        if type(name) is str and name
    ))
    if type(libdir) is str:
        for name in library_names:
            source = Path(libdir) / name
            if source.is_file():
                mounts.append(read_only(
                    str(source), f"{guest_prefix}/lib/{name}",
                    reuse_attestation=True,
                ))
    for dependency in dependencies:
        mounts.append(read_only(
            dependency, dependency, reuse_attestation=True))
    # Installed third-party directories were excluded before copying. There is
    # nothing to mask (and no mountpoint to create in this read-only image).
    return binaries, tuple(mounts), ()


__all__ = [
    "binary",
    "content_digest",
    "native_runtime",
    "python_runtime",
    "read_only",
]
