"""Positive reference goals from the execution already used for matching.

This is separate from candidate coverage and never changes a comparison's score.
An adapter drains its existing native source-hit log. ReferenceProgress also
collects task transaction predicates from the accepted inputs and computed
expected outputs. Neither collector executes another model or constructs IR.
"""

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .catalogue import CatalogueManifest
from .record_value import canonical_json
from .refusal import Refusal

VERSION = "reference-source-observation/1"


@dataclass(frozen=True, slots=True)
class ReferenceSourceObservation:
    reference: str
    trace: str
    catalogue: CatalogueManifest
    hits: frozenset[str]

    def __post_init__(self):
        if type(self.catalogue) is not CatalogueManifest:
            raise TypeError("reference source observation requires its declared catalogue")
        if type(self.hits) is not frozenset or self.hits - set(self.catalogue):
            raise ValueError("reference source hits must belong to that catalogue")

    def as_dict(self):
        return {"version": VERSION, "reference": self.reference, "trace": self.trace,
                "catalogue": self.catalogue.as_dict(), "hits": sorted(self.hits)}

    @property
    def fingerprint(self):
        return hashlib.sha256(canonical_json(self.as_dict()).encode()).hexdigest()


class ReferenceSourceRecorder:
    """Run-local union of selected step events, separate from reset/tick events."""

    def __init__(self, catalogue: CatalogueManifest, take_hits: Callable[[], Iterable[str]]):
        if type(catalogue) is not CatalogueManifest or not callable(take_hits):
            raise TypeError("a source recorder needs a catalogue and native hit reader")
        self.catalogue, self._take_hits = catalogue, take_hits
        self._declared = frozenset(catalogue)
        self._hits = set()
        self._refusal = None

    def discard_pending(self) -> None:
        """Drop events outside the selected invocation before it starts."""
        self._take_hits()

    def observe(self, hits: Iterable[str] | None = None) -> None:
        if self._refusal is not None:
            return
        try:
            reached = set(self._take_hits() if hits is None else hits)
            if reached - self._declared:
                raise ValueError("reference emitted goals outside its source catalogue")
            self._hits.update(reached)
        except (TypeError, ValueError) as exc:
            # Ancillary source feedback is not another condition on correct
            # reference output. Preserve its failure; never call it an empty hit set.
            self._refusal = Refusal("reference-source-observation-invalid", str(exc))

    def finish(self, reference: str, trace: str) -> ReferenceSourceObservation | Refusal:
        if self._refusal is not None:
            return self._refusal
        return ReferenceSourceObservation(
            reference, trace, self.catalogue, frozenset(self._hits))
