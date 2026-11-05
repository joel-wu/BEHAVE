"""Shared monetary admission, separate from scores and concurrency admission.

Reservations are conservative operator-selected estimates, not provider invoice
guarantees. Unknown charges retain their reservation without stopping unrelated
dispatches. Reserved money still counts against the account's budget.
An observed estimate overrun is recorded in full; subsequent admission uses
the actual total, not a permanent account stop. Provider quota failures remain
provider outcomes, not a stop for other providers. Cooperating processes share a short journal lock, never
a lock held across a network request. A pending call is not safe to retry.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json

from .attempt import AttemptInterrupted
from .billing import sum_amounts
from .journal import Event
from .model import Charge, ModelFailure, ModelReply, ModelRequest
from .process_model import decode_model_outcome, encode_model_outcome
from .run_store import JournalDirectory


def _balances(calls):
    spent, held, stopped = [], [], False
    for reservation, outcome in calls.values():
        if outcome is None or outcome["amount"] is None:
            held.append(Decimal(reservation["ceiling"]["amount"]))
        else:
            spent.append(Decimal(outcome["amount"]))
        stopped |= outcome is not None and outcome["stop"]
    return sum_amounts(spent), sum_amounts(held), stopped


def _authorized_limit(directory, config):
    """Operator additions do not change the original endpoint or call history."""
    amounts = [Decimal(config["limit"])]
    for event in JournalDirectory(directory).journal("funding").load():
        row = fields(dict(event.payload), {"authorization", "amount", "currency", "account"})
        if (event.kind != "budget_added" or row["account"] != config["account"]
                or row["currency"] != config["currency"]):
            raise ValueError("funding record names a different account")
        amount = Charge(row["amount"], row["currency"], "operator-funding/1")
        if Decimal(amount.amount) <= 0:
            raise ValueError("budget additions must be positive")
        amounts.append(Decimal(amount.amount))
    return sum_amounts(amounts)


def spending_snapshot(directory: Path) -> dict:
    """Read the last atomic cache without locks, reconciliation or any writes.

    This is a provisional live view. Final billing still uses summary(), which
    reconciles pending receipts after the shared workers have finished.
    """
    cache = json.loads((directory / "spending_cache.json").read_text())
    calls, config = cache["calls"], cache["configuration"]
    spent, held, stopped = _balances(calls)
    return {"account": config["account"], "currency": config["currency"],
        "limit": str(_authorized_limit(directory, config)), "spent": str(spent), "reserved": str(held),
        "stopped": stopped, "calls": len(calls), "provisional": True,
        "ledger_sequence": cache["head"]["sequence"],
        "pending": sum(outcome is None for _, outcome in calls.values()),
        "unpriced": sum(outcome is not None and outcome["amount"] is None
                        for _, outcome in calls.values())}


@dataclass(frozen=True, slots=True)
class SpendPolicy:
    directory: str
    account: str
    currency: str
    limit: str
    maximum_input_tokens: int

    def __post_init__(self):
        if not Path(self.directory).is_absolute() or Path(self.directory).parent == Path(self.directory):
            raise ValueError("budget needs an explicit account directory")
        text(self.account, "budget account")
        Charge(self.limit, self.currency, "budget-limit/1")
        if type(self.maximum_input_tokens) is not int or self.maximum_input_tokens < 1:
            raise ValueError("explicit input-token reservation bound required")

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value):
        return cls(**fields(value, set(cls.__dataclass_fields__)))


class SpendAccount:
    def __init__(self, policy: SpendPolicy):
        if type(policy) is not SpendPolicy:
            raise TypeError("explicit spending policy required")
        self.policy = policy
        self.run = JournalDirectory(policy.directory)
        # One account spans many attempts; it has no per-attempt event cap.
        self.journal = self.run.journal("spending")

    def _limit(self):
        return _authorized_limit(self.run.directory, self.policy.as_dict())

    def add_budget(self, amount: str, *, authorization: str):
        """Record one explicit operator authorization, idempotently by its id."""
        text(authorization, "budget authorization")
        grant = Charge(amount, self.policy.currency, "operator-funding/1")
        if Decimal(grant.amount) <= 0:
            raise ValueError("budget additions must be positive")
        payload = {"authorization": authorization, "amount": grant.amount,
                   "currency": self.policy.currency, "account": self.policy.account}

        def add():
            self._load()
            journal = self.run.journal("funding")
            events = journal.load()
            for event in events:
                if event.payload["authorization"] == authorization:
                    if dict(event.payload) != payload:
                        raise ValueError("authorization already records a different addition")
                    return str(self._limit())
            journal.append(events, "budget_added", payload)
            return str(self._limit())

        return self._transaction(add, wait=True)

    def _transaction(self, action, *, deadline: float | None = None, wait: bool = False):
        """Wait for admission when requested; settlement only tries the lock once."""
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise AttemptInterrupted("run-already-owned")
            with ExitStack() as ownership:
                try:
                    ownership.enter_context(self.run.owner())
                except AttemptInterrupted as error:
                    if error.code != "run-already-owned" or (deadline is None and not wait):
                        raise
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise
                else:
                    # An action failure must propagate, not repeat a write.
                    return action()
            time.sleep(defaults.ADMISSION_POLL_SECONDS if remaining is None
                       else min(defaults.ADMISSION_POLL_SECONDS, remaining))

    def _outcomes(self, reference):
        return self.run.journal("outcomes", "r_" + reference.sha256)

    @staticmethod
    def _settlement(call_id, ceiling, outcome, reference):
        charge = outcome.charge
        # A router supplies a native debit instead of a token-price estimate.
        # Bind it to the same reservation's price snapshot and raw receipt.
        usage = outcome.usage
        native_cost = (charge is not None and charge.pricing_revision == "openrouter-usage-cost/1"
            and usage.get("billing_source") == "usage.cost"
            and bool(usage.get("pricing"))
            and ceiling.pricing_revision.endswith(":" + usage["pricing"])
            and isinstance(usage.get("raw"), Mapping)
            and str(usage["raw"].get("cost")) == charge.amount)
        known = (charge is not None and charge.currency == ceiling.currency
                 and (charge.pricing_revision == ceiling.pricing_revision or native_cost))
        return {"call_id": call_id, "outcome": reference.as_dict(),
                "amount": charge.amount if known else None,
                "stop": False}

    def _recover_outcome(self, reservation):
        reference = BlobRef.from_dict(dict(reservation["request"]))
        journal = self._outcomes(reference)
        if not journal.directory.exists():
            return None
        rows = journal.load()
        if not rows:
            return None  # A writer may have created the directory, not committed.
        if len(rows) != 1:
            raise RecordError("spend-outcome-invalid", "one settlement is required per reservation")
        event = rows[0]
        data = fields(dict(event.payload), {"request", "ceiling", "outcome"})
        if (event.kind != "provider_returned" or data["request"] != reservation["request"]
                or data["ceiling"] != reservation["ceiling"]):
            raise ValueError("budget outcome receipt differs from reservation")
        request = ModelRequest.from_dict(parse_json(read_verified(self.run.objects, reference)))
        outcome_ref = BlobRef.from_dict(dict(data["outcome"]))
        outcome = decode_model_outcome(request, read_verified(self.run.objects, outcome_ref))
        ceiling = Charge(**dict(reservation["ceiling"]))
        return self._settlement(request.call_id, ceiling, outcome, outcome_ref)

    def _tail(self, head):
        # Rolling deployment can leave a process with the old journal class
        # already imported. Its full reader remains correct, just slower.
        reader = getattr(self.journal, "load_after", None)
        if reader is not None:
            return reader(head)
        events = self.journal.load()
        if head is not None and events[head.sequence] != head:
            raise RecordError("journal-conflict", "authenticated journal head changed")
        return events[0 if head is None else head.sequence + 1:]

    def _append(self, head, kind, payload, artifacts=()):
        writer = getattr(self.journal, "append_after", None)
        if writer is not None:
            return writer(head, kind, payload, artifacts)
        events = self.journal.load()
        if (events[-1] if events else None) != head:
            raise RecordError("journal-conflict", "expected journal head changed")
        return self.journal.append(events, kind, payload, artifacts)

    def _load(self):
        # Different models may share money; each has its own reservation bound.
        config = {"schema": "behave.spend-account/1", "account": self.policy.account,
                  "currency": self.policy.currency, "limit": self.policy.limit}
        # The derived state is local and protected by the same owner lock as
        # the append-only ledger. Reconcile its authenticated tail, not every
        # old model request/response artifact on every monetary admission.
        cache_path = self.run.directory / "spending_cache.json"
        try:
            cache = json.loads(cache_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            cache = None
        if cache is None:
            head, calls = None, {}
        else:
            if cache["configuration"] != config:
                raise ValueError("budget account configuration changed")
            head = Event.from_bytes(canonical_json(cache["head"]))
            calls = cache["calls"]
        events = self._tail(head)
        if head is None:
            if not events:
                events = (self._append(None, "account_opened", config),)
            if dict(events[0].payload) != config:
                raise ValueError("budget account configuration changed")
            head, events = events[0], events[1:]
        for event in events:
            row = thaw_json(event.payload)
            if event.kind == "spend_reserved" and row["call_id"] not in calls:
                calls[row["call_id"]] = [row, None]
            elif event.kind == "spend_settled" and row["call_id"] in calls and calls[row["call_id"]][1] is None:
                calls[row["call_id"]][1] = row
            elif (event.kind == "spend_reconciled" and row["call_id"] in calls
                    and calls[row["call_id"]][1] is not None
                    and calls[row["call_id"]][1]["amount"] is None
                    and row["amount"] is not None
                    and row == self._recover_outcome(calls[row["call_id"]][0])):
                calls[row["call_id"]][1] = row
            else:
                raise ValueError("invalid budget dispatch history")
            head = event
        # A per-call result is committed before competing for the shared ledger.
        # Every subsequent admission reconciles those receipts under this lock,
        # including actual charges, before considering another request.
        for call_id, (reservation, settled) in calls.items():
            if settled is None:
                recovered = self._recover_outcome(reservation)
                if recovered is not None:
                    ref = BlobRef.from_dict(recovered["outcome"])
                    head = self._append(head, "spend_settled", recovered, (ref,))
                    calls[call_id][1] = recovered
        spent, held, stopped = _balances(calls)
        # Atomic derived-cache replacement. A crash before it leaves an older
        # valid prefix, and the next owner replays the missing ledger tail.
        with NamedTemporaryFile(mode="w", dir=self.run.directory, prefix=".spending-cache-", delete=False) as stream:
            # dumps uses the native encoder; dump's incremental Python encoder
            # kept every competing provider waiting while this lock was held.
            stream.write(json.dumps({"configuration": config, "head": json.loads(head.to_bytes()), "calls": calls},
                                    separators=(",", ":"), allow_nan=False))
        os.replace(stream.name, cache_path)
        return head, calls, spent, held, stopped

    def reconcile_receipts(self):
        """Append newly recognized native charges; never replay API calls or overwrite known charges."""
        def reconcile():
            head, calls, *_ = self._load()
            count = 0
            for reservation, settled in calls.values():
                if settled is None or settled["amount"] is not None:
                    continue
                recovered = self._recover_outcome(reservation)
                if recovered is not None and recovered["amount"] is not None:
                    ref = BlobRef.from_dict(recovered["outcome"])
                    head = self._append(head, "spend_reconciled", recovered, (ref,))
                    count += 1
            self._load()
            return count
        return self._transaction(reconcile)

    def summary(self):
        """Reconcile receipts and report totals; fail fast if another writer owns the account."""
        def read():
            _, calls, spent, held, stopped = self._load()
            return {"account": self.policy.account, "currency": self.policy.currency,
                    "spent": str(spent), "reserved": str(held), "limit": str(self._limit()),
                    "stopped": stopped, "calls": len(calls),
                    "pending": sum(outcome is None for _, outcome in calls.values()),
                    "unpriced": sum(outcome is not None and outcome["amount"] is None
                                    for _, outcome in calls.values())}
        return self._transaction(read)

    def dispatch(self, request: ModelRequest, ceiling: Charge, generate):
        if type(ceiling) is not Charge or ceiling.currency != self.policy.currency:
            raise ValueError("reservation currency differs from account")
        reference = BlobRef.of(canonical_json(request.as_dict()))
        # No separate queue timeout. The supervising worker still owns the
        # whole request's deadline/cancellation, including transport and cleanup.
        deadline = None if request.timeout_seconds is None else time.monotonic() + request.timeout_seconds

        def reserve():
            head, calls, spent, held, stopped = self._load()
            if request.call_id in calls:
                reservation, settled = calls[request.call_id]
                previous = Charge(**dict(reservation["ceiling"]))
                if (reservation["request"] != reference.as_dict()
                        or previous.currency != ceiling.currency
                        or previous.pricing_revision != ceiling.pricing_revision
                        or Decimal(previous.amount) != Decimal(ceiling.amount)):
                    raise ValueError("budget call ID names different request or prices")
                if settled is None:
                    return ModelFailure("unknown", "budget-dispatch-pending")
                return decode_model_outcome(request, read_verified(self.run.objects,
                    BlobRef.from_dict(dict(settled["outcome"]))))
            exhausted = sum_amounts((spent, held, Decimal(ceiling.amount))) > self._limit()
            if stopped or exhausted:
                return ModelFailure("permanent", "model-spending-stopped" if stopped else "model-budget-exhausted")
            ref = self.run.objects.put(canonical_json(request.as_dict()))
            self._append(head, "spend_reserved", {"call_id": request.call_id,
                "request": ref.as_dict(), "ceiling": ceiling.as_dict()}, (ref,))
            return None

        try:
            prior = self._transaction(reserve, deadline=deadline, wait=True)
        except AttemptInterrupted as error:
            if error.code != "run-already-owned":
                raise
            # No dispatch in this invocation, but this call ID could already be
            # pending. Do not invent a zero charge or authorize an uncertain retry.
            return ModelFailure("unknown", "budget-account-busy")
        if prior is not None:
            return prior
        try:
            outcome = generate(request)
            if type(outcome) not in (ModelReply, ModelFailure):
                raise TypeError("invalid provider outcome")
        except Exception as error:  # noqa: BLE001 -- reservation survives uncertain transport
            outcome = ModelFailure("unknown", "budget-provider-" + type(error).__name__)

        # Do not lose a billable response when another broker owns the budget
        # lock. The immutable receipt uses the reserved request as its identity;
        # it is durable before either returning tools or releasing any reservation.
        ref = self.run.objects.put(encode_model_outcome(request, outcome))
        receipt = self._outcomes(reference)
        receipt.append((), "provider_returned", {"request": reference.as_dict(),
            "ceiling": ceiling.as_dict(), "outcome": ref.as_dict()}, (reference, ref))
        try:
            # The result is already durable. Never delay its delivery waiting
            # for this shared lock; the next admission must reconcile it first.
            self._transaction(self._load)
        except AttemptInterrupted as error:
            if error.code != "run-already-owned":
                raise
            # The receipt is durable; a later ledger admission will settle it.
            # Until then the full reservation remains held, never treated as free.
        return outcome
