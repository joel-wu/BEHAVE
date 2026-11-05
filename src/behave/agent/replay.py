"""A deterministic journal cursor shared by live execution and cold recovery."""

from __future__ import annotations

from behave.core.records import RecordError, canonical_json, thaw_json

from .attempt import AttemptInterrupted
from .journal import AttemptJournal, Event


class Tape:
    def __init__(self, journal: AttemptJournal):
        self.journal = journal
        self.events = list(journal.load(verify_artifacts=False))
        self.cursor = 0

    def record(self, kind: str, payload: dict, artifacts=()) -> Event:
        if self.cursor < len(self.events):
            event = self.take(kind)
            if canonical_json(thaw_json(event.payload)) != canonical_json(payload):
                raise RecordError("attempt-replay-diverged", f"replay differs at event {event.sequence}")
            return event
        event = self.journal.append(self.events, kind, payload, artifacts)
        self.events.append(event)
        self.cursor += 1
        return event

    def take(self, kind: str) -> Event:
        if self.cursor == len(self.events):
            raise AttemptInterrupted("attempt-outcome-unknown")
        event = self.events[self.cursor]
        if event.kind != kind:
            raise RecordError("attempt-event-unexpected", f"expected {kind} at event {self.cursor}")
        self.cursor += 1
        return event

    def begin(self, kind: str, payload: dict, artifacts=()) -> bool:
        """True only for a newly durable request, never a dangling old request."""
        new = self.cursor == len(self.events)
        self.record(kind, payload, artifacts)
        return new

    def finish(self) -> None:
        if self.cursor != len(self.events):
            raise RecordError("attempt-after-terminal", "unexpected events follow the terminal result")
