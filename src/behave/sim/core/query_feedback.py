"""Replay checkpoints for scheduling, never a second evidence authority.

The core replays each SAT request before the backend's next question. These
hits are provisional: closure must still complete its unchanged independent
audits and reconcile every reading before publishing any result. Prepared
formulas stay on the backend's stack. No caller-provided hit set is accepted.
"""

from __future__ import annotations

from collections import Counter

from .audit import AuditCheckStatus, WitnessInconsistent, witness_replay_check
from .audit_plan import AuditPlan
from .catalogue import CatalogueManifest
from .conditional_evidence import QueryEvidence
from .instance import BoundedInstance, Replay
from .query import QueryCall, SolverAnswer
from .refusal import Refusal, Refused
from .stimulus import canonical_json


def _call_image(call):
    # The cache layer may attach measurement metadata after a checkpoint. The
    # logical call, witness and physical work count must still be unchanged.
    return canonical_json({key: value for key, value in call.as_dict().items()
                           if key != "telemetry"})


class QueryFeedback:
    """One bound query round's provisional, core-replayed SAT requests."""

    __slots__ = (
        "_asking", "_calls", "_catalogue", "_closed",
        "_identity", "_instance", "_observed", "_plan", "_replays",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("query feedback is a closed internal protocol")

    def __init__(self, instance, asking, catalogue, plan):
        if type(instance) is not BoundedInstance:
            raise TypeError("query feedback needs a bound instance")
        if type(catalogue) is not CatalogueManifest or type(plan) is not AuditPlan:
            raise TypeError("query feedback binds a catalogue and audit plan")
        asking = tuple(asking)
        if len(set(asking)) != len(asking) or not set(asking) <= set(catalogue):
            raise ValueError("query feedback needs distinct declared goals")
        self._instance, self._asking = instance, frozenset(asking)
        self._catalogue, self._plan = catalogue, plan
        self._identity = self._current_identity()
        self._replays, self._observed = {}, []
        self._calls = Counter()
        self._closed = False

    @property
    def goals(self):
        return self._asking

    def _current_identity(self):
        return canonical_json({
            "domain": self._instance.domain.digest(),
            "artifact": self._instance.analysis_identity(),
            "evidence": self._instance.evidence_identity(),
            "source": self._instance.source,
            "file": getattr(self._instance.instance, "file", None),
        })

    def check(self, instance, asking):
        if self._closed:
            raise ValueError("query feedback has already closed")
        if instance is not self._instance or frozenset(asking) != self._asking:
            raise ValueError("query feedback belongs to another bound query")
        if (self._current_identity() != self._identity
                or self._instance.audit_plan() != self._plan):
            raise WitnessInconsistent(
                "closure-query-feedback-identity-changed",
                "the bound representation or audit plan changed during a query")
        self._catalogue.reconcile(instance.goals(), "query-feedback", exhaustive=True)

    def checkpoint(self, call):
        """Validate and replay a request; do not issue audit evidence here."""
        from .closure import QueryRefused, _replay_query_witness

        if type(call) is not QueryCall or call.answer is not SolverAnswer.SAT:
            raise TypeError("a replay checkpoint needs an exact SAT QueryCall")
        self.check(self._instance, self._asking)
        trace = call.witness
        raw = QueryEvidence(
            len(self._observed), call.goals, call.answer,
            decoded_request=trace, telemetry=call.telemetry,
            solver_calls=call.solver_calls)
        prior = (*self._observed, raw)
        if not set(call.goals) <= self._asking:
            raise QueryRefused(Refusal(
                "closure-query-transcript-goal-mismatch",
                "a replay checkpoint asks outside this round's goal set"), prior)
        key = canonical_json(trace)
        try:
            if key not in self._replays:
                replayed = _replay_query_witness(self._instance, call.goals, trace)
                if type(replayed) is not Replay:
                    raise Refused(Refusal(
                        "closure-query-feedback-replay-invalid",
                        "scheduling feedback needs the closed Replay record"))
                self._replays[key] = replayed
            replayed = self._replays[key]
            if not set(call.goals) & replayed.hits:
                raise WitnessInconsistent(
                    "closure-query-witness-misses-goal",
                    "the replay does not reach this call's requested set")
            if not replayed.hits <= set(self._catalogue):
                raise WitnessInconsistent(
                    "closure-query-witness-hit-outside-catalogue",
                    "the replay reports a goal outside the frozen catalogue")
            self.check(self._instance, self._asking)
        except WitnessInconsistent as exc:
            exc.queries = prior
            raise
        except Refused as exc:
            raise QueryRefused(exc.refusal, prior) from None
        self._observed.append(raw)
        self._calls[_call_image(call)] += 1
        return replayed

    def finish(self, transcript):
        """Feedback never authorizes dropping a call from the final record."""
        self.check(self._instance, self._asking)
        returned = Counter(_call_image(call) for call in transcript
                           if type(call) is QueryCall)
        if self._calls - returned:
            raise WitnessInconsistent(
                "closure-query-feedback-transcript-incomplete",
                "the provider omitted or changed a replay checkpoint call",
                queries=tuple(self._observed))

    def check_audits(self, sat_calls):
        """Every provisional reading must match the final independent audit."""
        self.check(self._instance, self._asking)
        remaining = set(self._replays)
        for call, witness, _hits in sat_calls:
            key = canonical_json(call.witness)
            if key not in remaining:
                continue
            agreement = witness_replay_check(witness, self._replays[key])
            if agreement.status is not AuditCheckStatus.AGREED:
                raise WitnessInconsistent(
                    "closure-query-feedback-replay-changed",
                    "the scheduling replay and final independent audit disagree",
                    queries=tuple(self._observed), request=key)
            remaining.remove(key)
        if remaining:
            raise WitnessInconsistent(
                "closure-query-feedback-audit-missing",
                "a scheduling replay has no final audited witness",
                queries=tuple(self._observed))

    def close(self):
        self._closed = True
        self._replays.clear()
        self._observed.clear()
        self._calls.clear()
