"""Atomic operator-facing records and derived files.

Mutable status writes need only the standard library. Immutable publication
loads the shared record/storage implementation when explicitly requested.
"""

import filecmp
import json
import os
import shutil
import signal
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event


def publish_bytes(path, data):
    """Durably publish immutable input bytes; matching retries never rewrite.

    This is not the mutable status writer below. The caller owns the enclosing
    preparation and commits its runnable record only after all inputs exist.
    """
    from behave.core.storage import install_exclusive

    path = Path(path)
    if install_exclusive(path.parent, path.name, data):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size != len(data)
                or stream.read(len(data) + 1) != data):
            raise ValueError(f"refusing to replace a different experiment record: {path}")


def publish_json(path, value):
    """Publish canonical immutable JSON through the common exclusive writer."""
    from behave.core.records import canonical_json

    publish_bytes(path, canonical_json(value))


def write_json(path, value, *, durable=False):
    write_bytes(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                durable=durable)


def write_bytes(path, data, *, durable=False):
    """Publish a complete file; optional durability retains receipt fsync order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A manual review export can overlap the background trace exporter.
    # Each writer needs its own staging file before atomic replacement.
    with NamedTemporaryFile(mode="wb", dir=path.parent,
                           prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if durable:
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def archive_spending(source, destination):
    """Publish a quiescent account without replacing conflicting sealed data.

    The caller fences all account users before reconciliation and publication.
    This copies an explicit active/staged ledger, not a cold-store inventory.
    Only the derived cache is mutable; existing journal/blob bytes must match.
    """
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("spending archive requires an existing account directory")
    if destination.is_symlink():
        raise ValueError("spending archive destination cannot be a symlink")
    for directory, children, files in os.walk(source):
        parent = Path(directory)
        for name in (*children, *files):
            if (parent / name).is_symlink():
                raise ValueError("spending archive cannot follow symlinks")
        target = destination / parent.relative_to(source)
        for part in (target, *target.parents):
            if part.is_symlink():
                raise ValueError("spending archive destination cannot follow symlinks")
            if part == destination:
                break
        for name in files:
            if name in {"owner.lock", "spending_cache.json"} or name.startswith("."):
                continue
            publish_bytes(target / name, (parent / name).read_bytes())
    cache = source / "spending_cache.json"
    if cache.exists():
        write_bytes(destination / cache.name, cache.read_bytes(), durable=True)


def archive_run(source, destination):
    """Publish completed local evidence without rewriting existing sealed blobs.

    The caller owns the top-level runtime status. Nested execution records are
    archived normally. Temporary scratch and process locks are not published.
    """
    source, destination = Path(source), Path(destination)

    def publish_file(src, dst):
        if Path(dst).exists() and filecmp.cmp(src, dst, shallow=False):
            return dst
        return shutil.copy2(src, dst)

    def ignore(directory, names):
        return {name for name in names if name == "owner.lock"
                or (Path(directory) == source and name in {"runtime.json", "scratch"})}

    return shutil.copytree(source, destination, dirs_exist_ok=True,
                           copy_function=publish_file, ignore=ignore)


def _restore_environment(name, previous):
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


@contextmanager
def command_scope(*, environment=None, stopped=None):
    """Request cooperative cancellation and restore only command-owned settings.

    Workers must join before the scope exits. This does not kill processes,
    reset unrelated environment variables or change resource/cleanup budgets.
    Register restoration incrementally so partial setup also unwinds safely.
    An optional caller-owned stop event retains existing cancellation state.
    """
    stopped = Event() if stopped is None else stopped
    with ExitStack() as restore:
        for name, value in (environment or {}).items():
            restore.callback(_restore_environment, name, os.environ.get(name))
            os.environ[name] = value
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous = signal.signal(signum, lambda *_: stopped.set())
            restore.callback(signal.signal, signum, previous)
        yield stopped.is_set
