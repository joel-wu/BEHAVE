"""Resume-safe completed-attempt publication, from local storage to e.g. NFS."""

from __future__ import annotations

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, thaw_json

from .journal import AttemptJournal


def publish_attempt(source: AttemptJournal, destination: AttemptJournal,
                    *, workers: int = defaults.PUBLICATION_WORKERS) -> BlobRef:
    """Publish the referenced closure and identical event chain; terminal last.

    Both attempt identities need exclusive ownership. This is not a distributed
    lease or a worker launcher. Interrupted publication neither resamples a model
    nor re-executes tools; an active session needs persistent journaling already.
    """
    events = source.load()
    if not events or events[-1].kind != "terminal" or any(event.kind == "terminal" for event in events[:-1]):
        raise RecordError("attempt-not-terminal", "only a completed attempt may be published")
    existing = list(destination.load())
    if len(existing) > len(events) or any(a.to_bytes() != b.to_bytes() for a, b in zip(existing, events, strict=False)):
        raise RecordError("attempt-publication-conflict", "destination is not a prefix of this attempt")
    refs = tuple(dict.fromkeys(ref for event in events for ref in event.artifacts))
    destination.objects.publish(source.objects, refs, workers=workers)
    for event in events[len(existing):]:
        copied = destination.append(existing, event.kind, thaw_json(event.payload), event.artifacts)
        if copied.to_bytes() != event.to_bytes():
            raise RecordError("attempt-publication-mismatch", "published event identity changed")
        existing.append(copied)
    verified = destination.load()
    if tuple(event.to_bytes() for event in verified) != tuple(event.to_bytes() for event in events):
        raise RecordError("attempt-publication-mismatch", "published history differs from the source")
    return BlobRef.of(verified[-1].to_bytes())
