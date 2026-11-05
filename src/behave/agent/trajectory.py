"""Lossless training/accounting views, not a reward or advantage algorithm."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, metadata, parse_json, thaw_json

from .billing import sum_amounts
from .journal import AttemptJournal, Event
from .model import Charge, ModelReply


@dataclass(frozen=True, slots=True)
class RecordedTurn:
    turn: int
    request: BlobRef
    response: BlobRef
    raw_response: BlobRef
    token_data: MappingProxyType | None
    exclusion: str | None

    @property
    def training_status(self) -> str:
        """Recorded payloads still require tokenizer/mask/log-probability checks."""
        if self.exclusion is not None:
            return "excluded"
        return "unavailable" if self.token_data is None else "recorded"


@dataclass(frozen=True, slots=True)
class CostLedger:
    """Provider usage is not a source of training turns, including undelivered replies."""

    charges: tuple[Charge, ...]
    unpriced_dispatches: int
    unresolved_dispatches: int

    @classmethod
    def combine(cls, ledgers: tuple[CostLedger, ...]) -> CostLedger:
        """Add distinct invocations, never deduplicate equal charges across candidates."""
        if type(ledgers) is not tuple or any(type(ledger) is not cls for ledger in ledgers):
            raise TypeError("cost aggregation requires CostLedger values")
        return cls(tuple(charge for ledger in ledgers for charge in ledger.charges),
                   sum(ledger.unpriced_dispatches for ledger in ledgers),
                   sum(ledger.unresolved_dispatches for ledger in ledgers))

    def cost_by_currency(self) -> MappingProxyType:
        amounts: dict[str, list[Decimal]] = {}
        for charge in self.charges:
            amounts.setdefault(charge.currency, []).append(Decimal(charge.amount))
        return metadata({currency: str(sum_amounts(values)) for currency, values in amounts.items()})


@dataclass(frozen=True, slots=True)
class Trajectory:
    turns: tuple[RecordedTurn, ...]
    charges: tuple[Charge, ...]
    unpriced_dispatches: int
    unresolved_dispatches: int

    @property
    def billing(self) -> CostLedger:
        return CostLedger(self.charges, self.unpriced_dispatches, self.unresolved_dispatches)

    def cost_by_currency(self) -> MappingProxyType:
        return self.billing.cost_by_currency()


def trajectory(journal: AttemptJournal, *, verify_artifacts: bool = True) -> Trajectory:
    """Every settled dispatch is billed separately, including transport retries.

    Missing token data is unavailable, not inferred from text. Excluding one
    reply never excludes earlier replies or changes the candidate's identity.
    Unpriced/unresolved usage is explicit rather than reported as free.
    """
    return trajectory_of(journal.load(verify_artifacts=verify_artifacts), journal.objects)


def trajectory_of(events: tuple[Event, ...], reader: ContentReader) -> Trajectory:
    """Reduce an already authenticated event prefix, live or sealed."""
    return _reduce(events, reader, include_turns=True)


def billing_of(events: tuple[Event, ...], reader: ContentReader) -> CostLedger:
    """Account for dispatches without loading request text or raw responses.

    This is a cost projection, not a replacement for trajectory verification.
    Provider journals also include replies never delivered to a role.
    """
    return _reduce(events, reader, include_turns=False).billing


def _reduce(events, reader, *, include_turns):
    pending = {}
    seen = set()
    turns, charges = [], []
    unpriced = 0
    unknown = 0
    for event in events:
        row = thaw_json(event.payload)
        if event.kind == "model_requested":
            if row["call_id"] in seen:
                raise RecordError("trajectory-call-duplicate", "model dispatch identity reused")
            seen.add(row["call_id"])
            pending[row["call_id"]] = BlobRef.from_dict(row["request"])
        elif event.kind == "model_settled":
            request_ref = pending.pop(row["call_id"], None)
            if request_ref is None:
                raise RecordError("trajectory-call-unbound", "model outcome has no preceding request")
            request = parse_json(read_verified(reader, request_ref)) if include_turns else None
            response_ref = BlobRef.from_dict(row["record"])
            raw_ref = BlobRef.from_dict(row["raw"])
            record = parse_json(read_verified(reader, response_ref))
            if record["charge"] is None:
                unpriced += 1
            else:
                charges.append(Charge(**record["charge"]))
            counting = record.get("usage", {}).get("input_count")
            if counting is not None:
                if counting.get("charge") is None:
                    unpriced += 1
                else:
                    charges.append(Charge(**counting["charge"]))
            if row["outcome"] == "reply":
                if include_turns:
                    reply = ModelReply.from_dict(record, raw=read_verified(reader, raw_ref))
                    turns.append(RecordedTurn(request["turn"], request_ref, response_ref, raw_ref,
                                              reply.token_data, reply.training_exclusion))
            elif row["outcome"] == "failure":
                unknown += record["kind"] == "unknown"
            else:
                raise RecordError("trajectory-outcome-invalid", "unknown model outcome")
    return Trajectory(tuple(turns), tuple(charges), unpriced, len(pending) + unknown)
