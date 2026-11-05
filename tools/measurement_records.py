"""Small, dependency-free primitives for durable measurement records.

The measurement tools are deliberately not part of the simulator runtime, but they
must agree on two pieces of record semantics: identities are SHA-256 digests over
length-framed logical names and bytes, and a completed record is installed atomically
without replacing an earlier invocation.  Keeping those rules here avoids four subtly
different implementations of what is meant to be the same evidence boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

RECORD_SCHEMA = "measurement-record/1"


def framed_sha256(entries):
    """Hash ``(logical_name, payload)`` entries without delimiter ambiguity.

    ``payload`` is bytes, or ``None`` for an explicitly absent member.  Names are
    sorted before hashing.  Duplicate names are rejected because otherwise a caller
    could accidentally give one logical file two meanings while still receiving one
    plausible-looking digest.
    """

    normalized = []
    seen = set()
    for name, payload in entries:
        if type(name) is not str or not name:
            raise ValueError("measurement member names must be nonempty strings")
        if name in seen:
            raise ValueError(f"duplicate measurement member {name!r}")
        seen.add(name)
        if payload is not None and type(payload) is not bytes:
            raise TypeError(
                f"measurement member {name!r} must be bytes or None")
        normalized.append((name, payload))

    digest = hashlib.sha256()

    def frame(value):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    for name, payload in sorted(normalized):
        frame(name.encode("utf-8"))
        frame(b"present" if payload is not None else b"missing")
        if payload is not None:
            frame(payload)
    return digest.hexdigest()


def write_json_exclusive(path, document):
    """Durably install one JSON record, never replacing another invocation.

    The temporary file is created in the destination directory, flushed and fsynced,
    then linked to the final name.  ``link`` is the atomic no-clobber operation: a
    concurrent writer cannot pass an ``exists`` check and overwrite this record.  An
    identical existing document is idempotent; different bytes are refused.
    """

    path = Path(path)
    body = json.dumps(document, indent=1, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".partial")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text() == body:
                return path
            raise RuntimeError(
                f"refusing to replace existing measurement record {path}") from None
        # Persist the directory entry as well as the file contents where supported.
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return path
