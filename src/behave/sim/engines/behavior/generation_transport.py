"""Lazy, search-scoped Behavior worker; no process is retained by idle handles."""

import hashlib
import json
import math

from ...core.refusal import Refused
from ...core.record_value import canonical_json
from ...job_isolation import IsolatedJobFailure
from ...job_session import JobSession
from ...execution_control import remaining_timeout
from ...worker import WorkerFailed
from .generation_job import decode_layout_reply, decode_probe_reply, decode_selection_reply

VERSION = "behavior-generation-session/11"


def _selection_key(seed, trace):
    return hashlib.sha256(canonical_json((seed, trace)).encode()).hexdigest()


class BehaviorSearch:
    def __init__(self, context, layout, *, timeout_s, probe_budget, preparation_plans=None):
        self.context, self.layout = context, layout
        self.timeout_s = timeout_s
        self.probe_budget = probe_budget
        self.remaining = probe_budget
        self.seed_count = 1
        self.preparation_count = 0
        self.timings = []
        self.session = None
        self.failure = None
        self._selected = None
        self._probing = None
        # These detached input recipes belong to the provider, not the worker
        # process. Recorded-domain switches may close/reopen this transport.
        self.preparation_plans = {} if preparation_plans is None else preparation_plans

    def select(self, seed, trace, targets):
        targets = tuple(targets)
        payload = {
            "seed": seed.as_dict(), "trace": trace.as_dict(), "targets": list(targets),
        }
        prior = self.preparation_plans.get(_selection_key(payload['seed'], payload['trace']))
        if prior is not None:
            payload['preparation_plan'] = json.loads(prior)
        answer = self._call("select", payload)
        self._selected = seed, trace, targets
        return answer

    def prepare(self, payload):
        return self._call("prepare", payload)

    def probe(self, seed, trace, targets, request, timeout_ms):
        targets = tuple(targets)
        payload = {"targets": list(targets), "request": request.as_dict(),
                   "timeout_ms": timeout_ms}
        # These are immutable objects from a successful Select in this session.
        # Equal-but-distinct inputs use the full checked restoration path.
        for role, context in (("selected", self._selected), ("probing", self._probing)):
            if (context is not None and context[0] is seed and context[1] is trace
                    and context[2] == targets):
                payload["retained"] = role
                break
        else:
            payload.update(seed=seed.as_dict(), trace=trace.as_dict())
        answer = self._call("probe", payload)
        self._selected = self._probing = seed, trace, targets
        return answer

    def _call(self, op, payload):
        if self.failure is not None:
            raise self.failure
        try:
            if op not in {"select", "probe", "prepare"}:
                raise ValueError("invalid Behavior search operation")
            if op == "probe":
                if self.remaining is not None and self.remaining < 1:
                    raise IsolatedJobFailure("refused", "the search Probe budget is exhausted")
                if self.remaining is not None:
                    self.remaining -= 1
            if self.session is None:
                # Each selection can import one donor delta and export one.
                # Every retained candidate still costs a Probe; no independent
                # time allowance is introduced for preparation exchanges.
                exchanges = (None if self.probe_budget is None else
                             1 + 3 * self.seed_count + self.preparation_count + 4 * self.probe_budget)
                wall_s = remaining_timeout(float("inf"))
                if not math.isfinite(wall_s) and exchanges is not None:
                    wall_s = self.timeout_s * exchanges
                self.session = JobSession(
                    "behavior_generation_session", self.context,
                    module="behave.sim.engines.behavior.generation_session", version=VERSION,
                    timeout_s=self.timeout_s,
                    wall_s=wall_s,
                    exchanges=exchanges, timings=self.timings)
                opened = decode_layout_reply(self.session.opened)
                if type(opened) is not type(self.layout) \
                        or opened.as_dict() != self.layout.as_dict():
                    raise IsolatedJobFailure("no-answer", "session rebuilt a different layout")
            # The payload's quantum bounds cooperative construction/solving,
            # not the worker's lifetime. Planning may finish an indivisible
            # carrier computation before yielding retained construction. A
            # query-plus-overhead kill timer would discard that same work.
            # JobSession also checks this owning stage while awaiting a reply.
            timeout_s = remaining_timeout(float("inf")) if op == "probe" else self.timeout_s
            if not math.isfinite(timeout_s):
                timeout_s = self.timeout_s  # Standalone, finite-Probe callers.
            result = self.session._exchange(op, payload, timeout_s)
            if op == "select":
                decoded = decode_selection_reply(result)
                if decoded.catalogue_digest != self.layout.catalogue.digest():
                    raise ValueError("session rebuilt a different selection catalogue")
                if result['preparation_plan'] is not None:
                    key = _selection_key(payload['seed'], payload['trace'])
                    image = canonical_json(result['preparation_plan'])
                    if self.preparation_plans.setdefault(key, image) != image:
                        raise ValueError('session changed an issued preparation plan')
            elif op == "probe":
                decoded = decode_probe_reply(
                    result, expected_catalogue_digest=self.layout.catalogue.digest())
            else:
                decoded = result  # Proposal material has no score or goal authority.
            return decoded
        except (IsolatedJobFailure, Refused, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, Refused):
                # Keep the source/IR audit and other typed evidence already
                # decoded by JobSession; reducing it to prose loses the cause.
                failure = exc
            else:
                failure = WorkerFailed(getattr(exc, "how", "no-answer"), str(exc),
                                       code=getattr(exc, "code", ""))
                for note in getattr(exc, "__notes__", ()):
                    failure.add_note(note)
            self.failure = failure
            try:
                raise failure from None
            finally:
                self.close()

    def close(self):
        self._selected = self._probing = None
        if self.session is not None:
            self.session.close()
