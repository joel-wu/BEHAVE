"""Reusable byte identities for host-trusted runtime trees.

The isolation policy binds mounted objects by a digest of their bytes.  Re-reading a
Python runtime and every installed package before every short-lived child is exact but
needlessly expensive: these are verifier-owned inputs, and a normal host edit changes
the metadata of the edited descendant.  This module records the digest together with a
manifest of *every* descendant's identity and metadata.  A later process may reuse the
digest only after reproducing that complete manifest.

This is deliberately not the verifier for submitted inputs.  Callers opt in only for
host-trusted runtime objects; submitted source files continue to be byte-hashed at each
launch.  The host account is trusted.  The manifest is therefore a provenance guard
against normal edits and replacements, not a defence against a privileged host that
can forge inode metadata.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import copy_context
from functools import partial
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from .preparation_control import (
    PreparationCancelled,
    acquire_lock,
    check_preparation,
    poll_interval,
)

_SCHEMA = "content-attestation/1"
_FREEZE_ATTEMPTS = defaults.ISOLATION_ATTESTATION_ATTEMPTS
_METADATA_SETTLE_NS = int(defaults.ISOLATION_ATTESTATION_SETTLE_SECONDS * 1_000_000_000)
_MANIFEST_WORKERS = defaults.ISOLATION_ATTESTATION_WORKERS

# Tests replace this with a private directory.  Production uses one directory per uid;
# all census workers on one node consequently share the first byte scan.
_CACHE_ROOT = scratch_root() / f"behave-content-attestation-{os.getuid()}"
_MEMORY: dict[
    tuple[str, bool, bool], tuple[str, tuple[tuple[object, ...], ...]]
] = {}
_MEMORY_LOCK = threading.Lock()
_FLIGHTS_LOCK = threading.Lock()
_FLIGHTS: dict[tuple[str, bool, bool], _ThreadFlight] = {}


class _ThreadFlight:
    """One in-process validation shared only by callers that overlap in time.

    Sequential calls still recheck the complete metadata manifest.  Callers already
    waiting for the same check reuse its result, rather than walking a large runtime
    tree concurrently and turning a short tool launch into an NFS metadata storm.
    """

    __slots__ = ("condition", "failure", "generation", "result", "running")

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.failure: BaseException | None = None
        self.generation = 0
        self.result: str | None = None
        self.running = False


class ContentAttestationError(ValueError):
    """A reusable runtime object could not be frozen to one stable byte image."""


def _frame(hasher: hashlib._Hash, payload: bytes) -> None:
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _entry(path: str | Path, relative: str, info: os.stat_result) -> tuple[object, ...]:
    common = (
        relative,
        stat.S_IFMT(info.st_mode),
        stat.S_IMODE(info.st_mode),
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
    if stat.S_ISLNK(info.st_mode):
        return (*common, os.readlink(path))
    return (*common, None)


def metadata_manifest(
    root: Path,
    *,
    allow_symlinks: bool,
    exclude_python_bytecode: bool = False,
) -> tuple[tuple[object, ...], ...]:
    """Return sorted metadata for every entry in the selected content image.

    A root-directory timestamp is not used as a shortcut.  Existing descendants may
    be rewritten without changing it, so every path is enumerated and checked.
    The Python-image filter omits bytecode files and ``__pycache__`` directories,
    matching the canonical byte scanner; it never omits source or extension files.
    """
    check_preparation()
    root = Path(root)
    root_info = os.lstat(root)
    if stat.S_ISLNK(root_info.st_mode):
        raise ValueError(f"read-only input is a symlink: {root}")
    if stat.S_ISREG(root_info.st_mode):
        return (_entry(root, ".", root_info),)
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError(
            f"read-only input is neither a file nor directory: {root}")

    def scan(directory: str, prefix: str):
        rows, directories = [], []
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            check_preparation()
            if exclude_python_bytecode and (
                (child.name == "__pycache__" and child.is_dir())
                or (child.name.endswith((".pyc", ".pyo")) and not child.is_dir())
            ):
                continue
            # Carry the relative name through the traversal instead of parsing
            # both absolute paths for every leaf. Still take a fresh lstat:
            # DirEntry's cached metadata must not replace the descendant check.
            path = child.path
            relative = prefix + child.name
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                if not allow_symlinks:
                    raise ValueError(
                        f"read-only tree contains a symlink: {path}")
                rows.append(_entry(path, relative, info))
            elif stat.S_ISDIR(info.st_mode):
                rows.append(_entry(path, relative, info))
                directories.append((path, relative + "/"))
            elif stat.S_ISREG(info.st_mode):
                rows.append(_entry(path, relative, info))
            else:
                raise ValueError(
                    f"read-only tree contains a special file: {path}")
        return rows, directories

    # Directory I/O can overlap without trusting cached DirEntry metadata or
    # skipping any descendants. Keep the executor local to this walk, with at
    # most four outstanding scans; the attestation's existing single flight
    # prevents concurrent callers from multiplying walks of the same tree.
    root_rows, todo = scan(str(root), "")
    directories = {"": root_rows}
    if todo:
        with ThreadPoolExecutor(max_workers=_MANIFEST_WORKERS) as executor:
            pending = {}
            while todo or pending:
                check_preparation()
                while todo and len(pending) < _MANIFEST_WORKERS:
                    path, prefix = todo.pop()
                    pending[executor.submit(copy_context().run, scan, path, prefix)] = prefix
                done, _ = wait(pending, timeout=poll_interval(), return_when=FIRST_COMPLETED)
                for future in done:
                    prefix = pending.pop(future)
                    entries, children = future.result()
                    directories[prefix] = entries
                    todo.extend(children)

    # Completion order is not manifest order. Reassemble the original sorted
    # depth-first image, including directory-before-descendant ordering. A
    # global lexical sort differs for names such as ``a`` and ``a.``.
    rows = [_entry(root, ".", root_info)]

    def append(prefix):
        for row in directories.pop(prefix):
            rows.append(row)
            if row[1] == stat.S_IFDIR:
                append(row[0] + "/")

    append("")
    return tuple(rows)


def _cache_directory() -> Path | None:
    """Return a private cache directory, or ``None`` rather than trust a bad one."""
    root = _CACHE_ROOT
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) \
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            return None
    except OSError:
        return None
    return root


def _identity(source: str, allow_symlinks: bool,
              exclude_python_bytecode: bool) -> dict[str, object]:
    identity = {
        "schema": _SCHEMA,
        "source": source,
        "allow_symlinks": allow_symlinks,
    }
    # Preserve existing full-content attestations. A filtered image has a
    # distinct identity and can never serve as a full-content attestation.
    if exclude_python_bytecode:
        identity["exclude_python_bytecode"] = True
    return identity


def _key(source: str, allow_symlinks: bool,
         exclude_python_bytecode: bool = False) -> str:
    return hashlib.sha256(_canonical(
        _identity(source, allow_symlinks, exclude_python_bytecode))).hexdigest()


@contextmanager
def _lock(root: Path, key: str, *, exclusive: bool):
    path = root / f"{key}.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
                or info.st_nlink != 1:
            raise ContentAttestationError(
                "the runtime-attestation lock is not a private regular file")
        acquire_lock(
            descriptor,
            fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
        )
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _document(
    source: str,
    allow_symlinks: bool,
    digest: str,
    manifest: tuple[tuple[object, ...], ...],
    exclude_python_bytecode: bool = False,
) -> dict[str, object]:
    body = {
        **_identity(source, allow_symlinks, exclude_python_bytecode),
        "digest": digest,
        "manifest": [list(row) for row in manifest],
    }
    return {
        **body,
        "record_digest": hashlib.sha256(_canonical(body)).hexdigest(),
    }


def _read_record(
    path: Path,
    source: str,
    allow_symlinks: bool,
    exclude_python_bytecode: bool = False,
) -> tuple[str, tuple[tuple[object, ...], ...]] | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) \
                or info.st_uid != os.getuid() or info.st_nlink != 1:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    identity = _identity(source, allow_symlinks, exclude_python_bytecode)
    if type(value) is not dict or set(value) != set(identity) | {
        "digest", "manifest", "record_digest"}:
        return None
    body = {name: item for name, item in value.items() if name != "record_digest"}
    if any(type(value[name]) is not type(expected) or value[name] != expected
           for name, expected in identity.items()):
        return None
    if type(value["digest"]) is not str \
            or len(value["digest"]) != 64 \
            or any(char not in "0123456789abcdef" for char in value["digest"]) \
            or value["record_digest"] != hashlib.sha256(_canonical(body)).hexdigest() \
            or type(value["manifest"]) is not list:
        return None
    rows = []
    for row in value["manifest"]:
        if type(row) is not list or len(row) != 11 \
                or type(row[0]) is not str \
                or any(type(field) is not int for field in row[1:10]) \
                or (row[10] is not None and type(row[10]) is not str):
            return None
        rows.append(tuple(row))
    if not rows:
        return None
    return value["digest"], tuple(rows)


def _write_record(path: Path, document: dict[str, object]) -> None:
    check_preparation()
    payload = _canonical(document)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        check_preparation()
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _reusable_record(
    root: Path,
    record: tuple[str, tuple[tuple[object, ...], ...]],
    *,
    allow_symlinks: bool,
    byte_digest,
    exclude_python_bytecode: bool = False,
) -> str | None:
    """Return the recorded digest only after an exact unchanged check.

    Some filesystems may assign the same timestamp to two rapid same-size writes.
    Metadata from that still-open clock interval is therefore not sufficient: one
    byte scan checks it.  Once every timestamp is older than the interval, a later
    normal write necessarily changes ctime and the complete manifest is sufficient.
    """
    digest, expected = record
    current = metadata_manifest(
        root, allow_symlinks=allow_symlinks,
        exclude_python_bytecode=exclude_python_bytecode)
    if current != expected:
        return None
    newest = max(max(int(row[8]), int(row[9])) for row in current)
    age = time.time_ns() - newest
    if age >= _METADATA_SETTLE_NS:
        return digest
    actual = byte_digest(root, allow_symlinks=allow_symlinks)
    return digest if actual == digest else None


def _reusable_content_digest_once(
    path: str,
    *,
    allow_symlinks: bool,
    byte_digest,
    exclude_python_bytecode: bool = False,
) -> str:
    """Reuse a digest only when every descendant still has its frozen metadata.

    ``byte_digest`` is the canonical byte-level implementation owned by
    :mod:`behave.isolation.content`.  It is injected to keep this module independent
    of the digest framing and to make cache-hit tests able to prove no byte scan ran.
    """
    source = os.path.realpath(path)
    root = Path(source)
    memory_key = (source, allow_symlinks, exclude_python_bytecode)
    with _MEMORY_LOCK:
        memory = _MEMORY.get(memory_key)
    if memory is not None:
        digest = _reusable_record(
            root,
            memory,
            allow_symlinks=allow_symlinks,
            byte_digest=byte_digest,
            exclude_python_bytecode=exclude_python_bytecode,
        )
        if digest is not None:
            return digest

    cache = _cache_directory()
    if cache is None:
        return byte_digest(root, allow_symlinks=allow_symlinks)
    key = _key(source, allow_symlinks, exclude_python_bytecode)
    record_path = cache / f"{key}.json"
    # Cache hits take a shared lock: ninety-six census workers may verify the same
    # runtime concurrently.  Serializing their complete stat walks behind an
    # exclusive lock merely replaces byte-I/O contention with lock contention.
    with _lock(cache, key, exclusive=False):
        record = _read_record(
            record_path, source, allow_symlinks, exclude_python_bytecode)
        if record is not None:
            try:
                digest = _reusable_record(
                    root,
                    record,
                    allow_symlinks=allow_symlinks,
                    byte_digest=byte_digest,
                    exclude_python_bytecode=exclude_python_bytecode,
                )
            except (OSError, ValueError):
                # Let the canonical scanner below produce the public error.  A cache
                # miss never turns an unverifiable input into an accepted one.
                digest = None
            if digest is not None:
                with _MEMORY_LOCK:
                    _MEMORY[memory_key] = record
                return digest

    # A miss is singleflight.  Recheck after acquiring the exclusive lock because
    # another process may have published while this process waited.
    with _lock(cache, key, exclusive=True):
        record = _read_record(
            record_path, source, allow_symlinks, exclude_python_bytecode)
        if record is not None:
            digest = _reusable_record(
                root,
                record,
                allow_symlinks=allow_symlinks,
                byte_digest=byte_digest,
                exclude_python_bytecode=exclude_python_bytecode,
            )
            if digest is not None:
                with _MEMORY_LOCK:
                    _MEMORY[memory_key] = record
                return digest

        # A before/after manifest makes a digest from a concurrently changing tree
        # ineligible for publication.  Two equal byte scans also cover filesystems
        # whose timestamp granularity cannot distinguish two rapid same-size writes.
        # This double scan is paid only by the process that publishes a new shared
        # attestation, never by its warm consumers.
        for _attempt in range(_FREEZE_ATTEMPTS):
            before = metadata_manifest(
                root, allow_symlinks=allow_symlinks,
                exclude_python_bytecode=exclude_python_bytecode)
            digest = byte_digest(root, allow_symlinks=allow_symlinks)
            again = byte_digest(root, allow_symlinks=allow_symlinks)
            after = metadata_manifest(
                root, allow_symlinks=allow_symlinks,
                exclude_python_bytecode=exclude_python_bytecode)
            if before == after and digest == again:
                _write_record(
                    record_path,
                    _document(source, allow_symlinks, digest, after,
                              exclude_python_bytecode),
                )
                with _MEMORY_LOCK:
                    _MEMORY[memory_key] = (digest, after)
                return digest
        raise ContentAttestationError(
            f"read-only runtime changed while it was being frozen: {source}")


def _flight_for(key: tuple[str, bool, bool]) -> _ThreadFlight:
    with _FLIGHTS_LOCK:
        return _FLIGHTS.setdefault(key, _ThreadFlight())


def reusable_content_digest(
    path: str,
    *,
    allow_symlinks: bool,
    byte_digest,
    exclude_python_bytecode: bool = False,
) -> str:
    """Return a checked reusable digest, coalescing concurrent identical checks.

    A completed check is shared only with threads that were already waiting for it.
    A later call executes :func:`_reusable_content_digest_once` again, so replacing a
    trusted runtime descendant between launches is still detected.
    Bytecode filtering is part of the cache identity and is used only to construct
    bytecode-free package images, never to certify an unfiltered live mount.
    """

    if type(exclude_python_bytecode) is not bool:
        raise TypeError("exclude_python_bytecode must be Boolean")
    if exclude_python_bytecode:
        byte_digest = partial(byte_digest, exclude_python_bytecode=True)
    key = (os.path.realpath(path), allow_symlinks, exclude_python_bytecode)
    flight = _flight_for(key)
    with flight.condition:
        while flight.running:
            generation = flight.generation
            while flight.running and flight.generation == generation:
                check_preparation()
                flight.condition.wait(timeout=poll_interval())
            check_preparation()
            if flight.failure is not None:
                raise flight.failure
            if flight.result is not None:
                return flight.result
            # The previous owner was cancelled, not this invocation. Re-enter
            # to join a replacement owner or perform a fresh complete check.
        flight.running = True

    try:
        result = _reusable_content_digest_once(
            path,
            allow_symlinks=allow_symlinks,
            byte_digest=byte_digest,
            exclude_python_bytecode=exclude_python_bytecode,
        )
    except BaseException as exc:
        with flight.condition:
            flight.failure = None if isinstance(exc, PreparationCancelled) else exc
            flight.result = None
            flight.running = False
            flight.generation += 1
            flight.condition.notify_all()
        raise
    else:
        with flight.condition:
            flight.failure = None
            flight.result = result
            flight.running = False
            flight.generation += 1
            flight.condition.notify_all()
        return result


__all__ = [
    "ContentAttestationError",
    "metadata_manifest",
    "reusable_content_digest",
]
