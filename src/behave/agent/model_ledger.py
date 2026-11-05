"""Durable provider dispatches, distinct from a client's RPC attempt.

One relay owns this ledger. Results commit before delivery. Repeated call IDs
must name identical request bytes; a pending prior dispatch is unknown, not
permission to issue the external request again. Queued declines are not usage.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json, version

from .admission import AdmissionPolicy
from .attempt import AttemptSpec
from .journal import AttemptJournal
from .model import ModelFailure, ModelReply, ModelRequest
from .replay import Tape

if TYPE_CHECKING:
    from .process_model import ModelCommand


def validate_provider_configuration(configuration: dict, *, spec: AttemptSpec, command: ModelCommand,
                                    admission: str | None, mismatch_code: str) -> None:
    """Authenticate an existing provider config; missing-ledger policy is the caller's.

    This reads no history, computes no fees and dispatches nothing. Comparing the
    exact serialized spec also rejects extra fields and noncanonical value types.
    """
    row = fields(configuration, {"schema", "spec", "command", "admission"})
    version(row["schema"], "behave.model-relay/1")
    policy = AdmissionPolicy(**fields(row["admission"], set(AdmissionPolicy.__dataclass_fields__)))
    if (canonical_json(row["spec"]) != canonical_json(spec.as_dict())
            or canonical_json(row["command"]) != canonical_json(command.as_dict())
            or policy.identity != admission):
        raise RecordError(mismatch_code, "provider configuration differs from its supervised invocation")


class ModelLedger:
    def __init__(self, journal: AttemptJournal, configuration: dict):
        self.journal = journal
        self.tape = Tape(journal)
        config = journal.objects.put(canonical_json(configuration))
        self.tape.record("broker_initialized", {"configuration": config.as_dict()}, (config,))
        self.calls: dict[str, tuple[BlobRef, dict | None]] = {}
        for event in self.tape.events[self.tape.cursor:]:
            row = thaw_json(event.payload)
            call_id = row["call_id"]
            if event.kind in {"model_requested", "model_declined"}:
                if call_id in self.calls:
                    raise RecordError("broker-call-duplicate", "provider dispatch identity reused")
                self.calls[call_id] = (BlobRef.from_dict(row["request"]), row if event.kind == "model_declined" else None)
            elif event.kind == "model_settled":
                if call_id not in self.calls or self.calls[call_id][1] is not None:
                    raise RecordError("broker-outcome-unbound", "provider outcome is unbound or repeated")
                self.calls[call_id] = (self.calls[call_id][0], row)
            else:
                raise RecordError("broker-event-invalid", "unexpected provider event")
        self.tape.cursor = len(self.tape.events)

    def _read(self, row: dict) -> ModelReply | ModelFailure:
        raw = read_verified(self.journal.objects, BlobRef.from_dict(row["raw"]))
        record = parse_json(read_verified(self.journal.objects, BlobRef.from_dict(row["record"])))
        if row["outcome"] == "reply":
            return ModelReply.from_dict(record, raw=raw)
        if row["outcome"] != "failure":
            raise RecordError("broker-outcome-invalid", "unknown provider outcome")
        return ModelFailure.from_dict(record, raw=raw)

    def lookup(self, request: ModelRequest) -> ModelReply | ModelFailure | None:
        if request.call_id not in self.calls:
            return None
        ref, outcome = self.calls[request.call_id]
        if ref != BlobRef.of(canonical_json(request.as_dict())):
            raise RecordError("broker-request-conflict", "call ID names different request bytes")
        return ModelFailure("unknown", "provider-outcome-unknown") if outcome is None else self._read(outcome)

    def _settle(self, request: ModelRequest, result: ModelReply | ModelFailure, *, declined: bool = False):
        objects = self.journal.objects
        if declined:
            request_ref = objects.put(canonical_json(request.as_dict()))
        else:
            request_ref = self.calls[request.call_id][0]
        record, raw = objects.put(canonical_json(result.as_dict())), objects.put(result.raw)
        row = {"call_id": request.call_id, "outcome": "reply" if type(result) is ModelReply else "failure",
               "record": record.as_dict(), "raw": raw.as_dict()}
        if declined:
            row["request"] = request_ref.as_dict()
        self.tape.record("model_declined" if declined else "model_settled", row, (request_ref, record, raw))
        self.calls[request.call_id] = (request_ref, row)
        return result

    def decline(self, request: ModelRequest, code: str) -> ModelFailure:
        if prior := self.lookup(request):
            return prior
        return self._settle(request, ModelFailure("permanent", code), declined=True)

    def dispatch(self, request: ModelRequest, generate: Callable[[ModelRequest], ModelReply | ModelFailure]):
        if prior := self.lookup(request):
            return prior
        ref = self.journal.objects.put(canonical_json(request.as_dict()))
        self.tape.record("model_requested", {"call_id": request.call_id, "request": ref.as_dict()}, (ref,))
        self.calls[request.call_id] = (ref, None)
        try:
            result = generate(request)
            if type(result) not in (ModelReply, ModelFailure):
                raise TypeError("provider returned an invalid outcome")
        except Exception as error:  # noqa: BLE001 -- provider faults do not license a redispatch
            result = ModelFailure("unknown", f"provider-{type(error).__name__}")
        return self._settle(request, result)
