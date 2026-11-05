"""Exclusive immutable publication. A copied object is not a committed run.

Each directory has a trusted owner. Distinct writers may publish the same blob;
an attempt journal, in contrast, requires one externally fenced coordinator.
"""

from __future__ import annotations

import errno
import os
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path

from . import runtime_defaults as defaults
from .artifacts import BlobRef, ContentReader, DirectoryReader, read_verified
from .records import RecordError, canonical_json


def publication_map(operation, values, *, workers: int = defaults.PUBLICATION_WORKERS):
    """Yield ordered I/O results without enqueueing an entire artifact closure.

    Keep at most eight references per worker in flight, including queued work.
    A failed operation stops input consumption and cancels work not yet started;
    already running operations settle before control returns to the owner.
    """
    if type(workers) is not int or workers < 1:
        raise ValueError("publication workers must be a positive integer")
    remaining = iter(values)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = deque()
        try:
            pending.extend(pool.submit(operation, value) for value in islice(remaining, workers * 8))
            while pending:
                yield pending.popleft().result()
                for value in islice(remaining, 1):
                    pending.append(pool.submit(operation, value))
        finally:
            for future in pending:
                future.cancel()


def _validate_publication(references, workers):
    if type(workers) is not int or workers < 1:
        raise ValueError("publication workers must be a positive integer")
    if type(references) is not tuple or any(type(ref) is not BlobRef for ref in references):
        raise TypeError("publication requires a tuple of BlobRef")


def shared_owned_store(directory: Path, owner: DirectoryStore) -> DirectoryStore:
    """Bind a new internal phase to an ancestor CAS, preserving legacy layouts.

    The enclosing run is the lifetime/cleanup unit. Exporting a sealed closure
    to an independent DirectoryStore still creates a self-contained copy.
    Never redirect an already materialized legacy phase or relocate its data.
    """
    if owner.directory.name != "objects" or owner.directory.parent not in directory.parent.parents:
        raise ValueError("shared object owner must be an enclosing round")
    receipt = directory.parent / "objects.owner.json"
    if receipt.exists():
        current = DirectoryStore(directory)
        if current.directory != owner.directory:
            raise RecordError("content-owner-conflict", "phase already has another object owner")
        return current
    if directory.parent.exists() and any(p.name != "owner.lock" for p in directory.parent.iterdir()):
        return DirectoryStore(directory)  # Existing flat journals replay unchanged.
    owner.directory.mkdir(parents=True, exist_ok=True)
    payload = canonical_json({"schema": "behave.object-store-owner/1",
                              "owner": os.path.relpath(owner.directory, directory.parent)})
    if not install_exclusive(directory.parent, receipt.name, payload) and receipt.read_bytes() != payload:
        raise RecordError("content-owner-conflict", "phase already has another object owner")
    return DirectoryStore(directory)


def install_exclusive(directory: Path, name: str, data: bytes) -> bool:
    """Fsync bytes, link without overwrite, then fsync the destination directory.

    The temporary file must live on the destination filesystem (including NFS).
    Returns False for an existing name; the caller must authenticate that object.
    """
    if type(name) is not str or name in ("", ".", "..") or "/" in name or "\x00" in name:
        raise ValueError("publication requires a single file name")
    if type(data) is not bytes:
        raise TypeError("publication requires bytes")
    directory.mkdir(parents=True, exist_ok=True)
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary = f".pending-{uuid.uuid4().hex}"
    try:
        try:
            os.stat(name, dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            # Avoid rewriting an existing blob. It may have just been linked
            # by another writer: sync the name before returning, and let the
            # caller authenticate its contents (including non-regular entries).
            os.fsync(root)
            return False
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600, dir_fd=root)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
        except FileExistsError:
            os.fsync(root)
            return False
        os.fsync(root)
        return True
    finally:
        try:
            os.unlink(temporary, dir_fd=root)
        except FileNotFoundError:
            pass
        finally:
            os.close(root)


class DirectoryStore(DirectoryReader):
    """A content store with verified, idempotent, non-overwriting publication."""

    def put(self, data: bytes) -> BlobRef:
        ref = BlobRef.of(data)
        if not install_exclusive(self.directory, ref.sha256, data):
            # Existing content may have been written by another owner. Newly
            # written bytes were just fsynced and need no readback/hash pass.
            self.read(ref)
        return ref

    def publish_owned(self, source: DirectoryStore, references: tuple[BlobRef, ...],
                      *, workers: int = defaults.PUBLICATION_WORKERS) -> None:
        """Publish a trusted producer's immutable files without payload rereads.

        Local run stores own and fsync these files before returning references.
        Hardlinks preserve them independently of the source directory lifetime.
        External/untrusted readers continue to use publish() and its checks.
        Use the existing publication concurrency for NFS metadata operations.
        """
        _validate_publication(references, workers)
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory == source.directory:
            return
        def link(ref):
            try:
                os.link(source.directory / ref.sha256, self.directory / ref.sha256,
                        follow_symlinks=False)
            except FileExistsError:
                pass
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                self.put(source.read(ref))

        for _ in publication_map(link, dict.fromkeys(references), workers=workers):
            pass
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def publish(self, source: ContentReader, references: tuple[BlobRef, ...],
                *, workers: int = defaults.PUBLICATION_WORKERS) -> None:
        """Copy and verify an explicit closure before its owner commits a record."""
        _validate_publication(references, workers)
        for _ in publication_map(lambda ref: self.put(read_verified(source, ref)),
                                 dict.fromkeys(references), workers=workers):
            pass
