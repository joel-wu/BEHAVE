"""Authenticated attempt history, committed after its artifact dependencies.

One coordinator owns an attempt. Immutable sequence slots catch conflicting
writes, but do not fence a stale coordinator's *external calls*. Takeover requires
external process fencing; neither NFS flock nor a PID file supplies that guarantee.
"""

from __future__ import annotations

import os
import re
import stat
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, metadata, parse_json, text, thaw_json, version
from behave.core.storage import DirectoryStore, install_exclusive


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int
    parent: BlobRef | None
    kind: str
    payload: MappingProxyType
    artifacts: tuple[BlobRef, ...]

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("event sequence must be a nonnegative integer")
        if self.parent is not None and type(self.parent) is not BlobRef:
            raise TypeError("event parent requires BlobRef")
        text(self.kind, "event kind")
        object.__setattr__(self, "payload", metadata(self.payload))
        if type(self.artifacts) is not tuple or any(type(ref) is not BlobRef for ref in self.artifacts):
            raise TypeError("event artifacts require a tuple of BlobRef")

    def to_bytes(self) -> bytes:
        return canonical_json({
            "schema": "behave.attempt-event/1", "sequence": self.sequence,
            "parent": None if self.parent is None else self.parent.as_dict(), "kind": self.kind,
            "payload": thaw_json(self.payload), "artifacts": [ref.as_dict() for ref in self.artifacts],
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> Event:
        row = fields(parse_json(data), {"schema", "sequence", "parent", "kind", "payload", "artifacts"})
        version(row["schema"], "behave.attempt-event/1")
        if type(row["sequence"]) is not int or row["sequence"] < 0 or type(row["artifacts"]) is not list:
            raise RecordError("journal-event-invalid", "invalid sequence or artifact list")
        result = cls(row["sequence"], None if row["parent"] is None else BlobRef.from_dict(row["parent"]),
                     text(row["kind"], "event kind"), metadata(row["payload"]),
                     tuple(BlobRef.from_dict(ref) for ref in row["artifacts"]))
        if result.to_bytes() != data:
            raise RecordError("journal-event-invalid", "noncanonical event")
        return result


def load_chain(reader: ContentReader, head: BlobRef | None, *, verify_artifacts: bool = True) -> tuple[Event, ...]:
    """Read a sealed prefix; trusted receipt consumers may skip payload rereads."""
    events, verified = [], set()
    while head is not None:
        event = Event.from_bytes(read_verified(reader, head))
        if events and event.sequence != events[-1].sequence - 1:
            raise RecordError("journal-chain-invalid", "sealed event sequence disagrees")
        for ref in event.artifacts if verify_artifacts else ():
            if ref not in verified:
                read_verified(reader, ref)
                verified.add(ref)
        events.append(event)
        head = event.parent
    if events and events[-1].sequence != 0:
        raise RecordError("journal-chain-invalid", "sealed history has no initial event")
    return tuple(reversed(events))


class AttemptJournal:
    def __init__(self, directory: str | Path, objects: DirectoryStore):
        self.directory = Path(directory).absolute()
        self.objects = objects
        self._lock = threading.Lock()

    @staticmethod
    def _read(root: int, name: str) -> bytes:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise RecordError("journal-event-invalid", "journal event is not a regular file")
            return stream.read()

    def load(self, *, verify_artifacts: bool = True) -> tuple[Event, ...]:
        return self.load_after(None, verify_artifacts=verify_artifacts)

    def load_after(self, head: Event | None, *, verify_artifacts: bool = True) -> tuple[Event, ...]:
        """Verify the tail after an already authenticated immutable prefix.

        The caller owns the journal and retains the prefix's state. Full cold
        loading can verify the complete artifact closure. Live cursors and
        export only need the event metadata, not another read of every payload.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        root = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            # Six digits are minimum padding, not a limit on the event number.
            names = sorted((name for name in os.listdir(root) if not name.startswith(".pending-")),
                           key=lambda name: (len(name), name))
            if names != [f"{i:06d}.json" for i in range(len(names))]:
                raise RecordError("journal-sequence-invalid", "journal has gaps or unexpected files")
            start = 0 if head is None else head.sequence + 1
            if head is not None and self._read(root, f"{head.sequence:06d}.json") != head.to_bytes():
                raise RecordError("journal-conflict", "authenticated journal head changed")
            events = []
            parent = None if head is None else BlobRef.of(head.to_bytes())
            verified = set()
            for index, name in enumerate(names[start:], start=start):
                data = self._read(root, name)
                event = Event.from_bytes(data)
                if event.sequence != index or event.parent != parent:
                    raise RecordError("journal-chain-invalid", "journal hash chain disagrees")
                for ref in event.artifacts if verify_artifacts else ():
                    if ref not in verified:
                        read_verified(self.objects, ref)
                        verified.add(ref)
                events.append(event)
                parent = BlobRef.of(data)
            return tuple(events)
        finally:
            os.close(root)

    def append(self, expected: Sequence[Event], kind: str, payload: dict,
               artifacts: tuple[BlobRef, ...] = ()) -> Event:
        if expected and expected[-1].sequence != len(expected) - 1:
            raise RecordError("journal-conflict", "expected prefix has the wrong length")
        return self.append_after(expected[-1] if expected else None, kind, payload, artifacts)

    def append_after(self, head: Event | None, kind: str, payload: dict,
                     artifacts: tuple[BlobRef, ...] = ()) -> Event:
        """Append from the authenticated head without rebuilding the old prefix."""
        if type(kind) is not str or re.fullmatch(r"[a-z][a-z_]*", kind) is None:
            raise ValueError("event kind must be a lowercase identifier")
        if any(type(ref) is not BlobRef for ref in artifacts):
            raise TypeError("event artifacts must be BlobRef values")
        with self._lock:
            # The caller authenticated this prefix on cold load. Check its head,
            # not the entire history/artifact closure on every live append.
            # Exclusive installation below rejects a concurrently filled slot.
            self.directory.mkdir(parents=True, exist_ok=True)
            if head is not None:
                root = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    if self._read(root, f"{head.sequence:06d}.json") != head.to_bytes():
                        raise RecordError("journal-conflict", "expected journal head changed")
                finally:
                    os.close(root)
            # Producers publish immutable artifacts before appending their
            # references. Do not reread the entire closure at each boundary.
            # Explicit load()/load_chain() remain the offline integrity checks.
            event = Event(0 if head is None else head.sequence + 1, None if head is None else BlobRef.of(head.to_bytes()),
                          kind, metadata(payload), tuple(dict.fromkeys(artifacts)))
            data = event.to_bytes()
            if not install_exclusive(self.directory, f"{event.sequence:06d}.json", data):
                raise RecordError("journal-conflict", "another coordinator already filled this sequence slot")
            return event

    def seal(self) -> tuple[BlobRef | None, tuple[BlobRef, ...]]:
        """Export this journal's current prefix.

        The caller owns the journal while loading and publishing. This does not
        append a terminal event or prevent later appends to the live journal.
        """
        references = []
        head = None
        for event in self.load(verify_artifacts=False):
            head = self.objects.put(event.to_bytes())
            references.extend((*event.artifacts, head))
        return head, tuple(dict.fromkeys(references))
