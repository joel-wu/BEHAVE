"""Classify one catalogue using audited replay and exact bounded queries.

Replay credits reached goals and leaves a residual set for exact queries.
Only an audited UNSAT answer establishes bounded unreachability; unfinished
queries remain unresolved. A witness or transcript inconsistency invalidates
the analysis globally rather than becoming a per-goal answer.
"""

from __future__ import annotations

import time
from contextlib import ExitStack, closing

from ..analysis_budget import AnalysisStopped, check_analysis, remaining_query_ms
from .audit import (
    AuditCheck,
    AuditCheckStatus,
    AuditedWitness,
    WitnessInconsistent,
    audit_check,
    witness_replay_check,
)
from .audit_plan import AuditKind, AuditPlan
from .catalogue import SAFETY, CatalogueManifest, catalogue_of
from .conditional_evidence import QueryEvidence
from .evidence import Closure, GoalEvidence, GoalStatus, Origin
from .instance import BoundedInstance
from .instance import Verdict as QueryVerdict
from .query import QueryCall, SolverAnswer
from .refusal import Refusal, Refused
from .stimulus import canonical_json

__all__ = [
    "Closure",
    "QueryRefused",
    "Refuted",
    "WitnessInconsistent",
    "close_over",
    "report",
    "self_check",
]


class QueryRefused(Refused):
    """A run-level refusal after one or more exact calls were already issued."""

    __slots__ = ("queries",)

    def __init__(self, refusal, queries):
        super().__init__(refusal)
        if any(type(item) is not QueryEvidence for item in queries):
            raise TypeError("a stopped query transcript accepts only QueryEvidence")
        self.queries = tuple(queries)

    def __reduce__(self):
        return type(self), (self.refusal, self.queries)


class Refuted(Exception):
    """A stimulus reached a goal the queries had called unreachable.

    For a caller of ``self_check``, which returns what it caught rather than
    raising, so that the caller decides whether a refuted verdict stops the run.

    ``close_over`` reports this condition as the run-level
    :class:`WitnessInconsistent` result.  It cannot be one goal's status: the
    contradiction invalidates the exact query mechanism used for every goal.
    """

    def __init__(self, refuted):
        self.refuted = dict(refuted)
        first = min(self.refuted)
        super().__init__(
            f"{len(self.refuted)} goals called unreachable are reached by a "
            f"stimulus, the first being {first}")


def self_check(closure, instance, traces):
    """Return goals declared unreachable but hit by independent replay traces.

    Use traces beyond the pool that defined the residual, such as later query
    witnesses or another provider's traces. Each returned goal has a concrete
    counterexample. An empty result does not replace the step-encoding audit.
    """
    caught = {}
    suspect = set(closure.unreachable)
    if not suspect:
        return caught
    for trace in traces:
        for goal in instance.replay(trace).hits & suspect:
            caught.setdefault(goal, trace)
    return caught


def _replay_pool(instance, pool, known, audited=None):
    """Read coordinator-verified witnesses or replay an unaudited request pool.

    ``audited`` is the internal handoff after the coordinator has checked the
    complete witnesses. It must not be reconstructed from caller-supplied hit IDs.

    The pool is traces, not the names of goals somebody says were covered.
    A goal is covered because a trace reached it and the trace is on record;
    otherwise the record says a goal was covered on a caller's word and there
    is nothing in it to recheck. What the theorem quantifies over is a set of
    audited traces of the declared length, so that is what comes in here.

    A trace this side could not carry out is not evidence of anything and is
    reported so. A trace that halts is evidence: halting is an outcome of the
    artifact and may be exactly what reaches a safety goal.

    And a trace that is not of this domain is refused **before** it is replayed, which
    is the third case and was missing. The theorem quantifies over the traces of this
    artifact at this bound under this contract, and nothing checked that the pool was
    made of them: a four-step trace counted towards a two-step closure, and a trace
    presenting an input the contract does not admit counted at all. Refusing after the
    replay would be too late, the hits having been read off a run of something else.
    """
    if audited is not None and (
            type(audited) is not tuple or len(audited) != len(pool)
            or any(type(witness) is not AuditedWitness for witness in audited)):
        raise TypeError("the internal audited pool needs one complete witness per request")
    hits, broken = {}, []
    for index, trace in enumerate(pool):
        wrong = instance.validate(trace)
        if wrong:
            broken.append((index, f"not a trace of this domain: {wrong[0]}"))
            continue
        if audited is None:
            got = instance.replay(trace)
            if got.broken:
                broken.append((index, got.broken))
                continue
            observed = got.hits
        else:
            witness = audited[index]
            if (witness.domain != instance.domain.digest()
                    or not _same_trace(witness.inputs, trace)):
                raise WitnessInconsistent(
                    "closure-pool-witness-mismatch",
                    "the coordinator's audited witness names another domain or request",
                    index=index)
            observed = witness.hits
        unknown = set(observed) - set(known)
        if unknown:
            raise WitnessInconsistent(
                "closure-replay-hit-outside-catalogue",
                "an admitted pool replay reports a goal outside the goal catalogue",
                goals=sorted(unknown)[:8], count=len(unknown))
        for goal in set(observed) & known:
            hits.setdefault(goal, trace)
    return hits, broken


def _replay_query_witness(instance, goals, trace):
    """Validate and replay one SAT request before semantic cross-checks.

    The solver's model is a candidate witness, not evidence by itself.  Domain
    disagreement or a replay that omits the queried goal contradicts the query
    and stops the whole closure as inconsistent.
    """
    goals = tuple(goals)
    try:
        outside = instance.validate(trace)
    except (Refused, WitnessInconsistent):
        raise
    except Exception as exc:                                  # noqa: BLE001
        raise Refused(Refusal(
            "closure-query-witness-validation-failed",
            "the bound instance could not validate a decoded query witness",
            goals=list(goals), error=f"{type(exc).__name__}: {exc}")) from None
    if outside:
        raise WitnessInconsistent(
            "closure-query-witness-outside-domain",
            "the exact query returned a witness outside the domain it was asked "
            "about",
            goals=list(goals), first=str(outside[0]))

    try:
        replayed = instance.replay(trace)
    except (Refused, WitnessInconsistent):
        raise
    except Exception as exc:                                  # noqa: BLE001
        raise Refused(Refusal(
            "closure-query-witness-replay-failed",
            "the decoded query witness could not be replayed",
            goals=list(goals), error=f"{type(exc).__name__}: {exc}")) from None
    if not hasattr(replayed, "hits") or not hasattr(replayed, "broken"):
        raise Refused(Refusal(
            "closure-query-witness-replay-invalid",
            "replaying the decoded query witness returned no typed replay record",
            goals=list(goals), got=type(replayed).__name__))
    if replayed.broken:
        raise Refused(Refusal(
            "closure-query-witness-replay-failed",
            "the decoded query witness could not be replayed",
            goals=list(goals), detail=str(replayed.broken)))
    if not set(goals) & set(replayed.hits):
        raise WitnessInconsistent(
            "closure-query-witness-misses-goal",
            "the exact query returned a witness whose replay does not reach the "
            "set it was returned for",
            goals=list(goals))
    return replayed


def _query_witnesses(instance, requests, audit_plan):
    """Independently replay and batch-audit finite SAT requests.

    Each request retains its own replay result.  The audit operations already accept a
    trace list, so invoking each required reading once avoids repeatedly constructing
    its source-isolation boundary while preserving the same per-trace comparisons.
    """

    requests = tuple(requests)
    outcomes = [None] * len(requests)
    pending = []
    for index, (goals, trace) in enumerate(requests):
        try:
            outcomes[index] = _replay_query_witness(instance, goals, trace)
            pending.append(index)
        except (Refused, WitnessInconsistent) as exc:
            outcomes[index] = exc
    if not pending:
        return tuple(outcomes)

    all_goals = sorted({goal for index in pending
                        for goal in requests[index][0]})
    traces = tuple(requests[index][1] for index in pending)

    for audit, method_name in ((AuditKind.STEP, "audit"),
                               (AuditKind.SOURCE, "source_audit")):
        if not audit_plan.requires(audit):
            continue
        required_name = audit.value
        method = getattr(instance, method_name, None)
        if method is None:
            failure = Refused(Refusal(
                f"closure-query-witness-{required_name.replace('_', '-')}-missing",
                f"this frontend requires {required_name} but its bound instance "
                "does not provide it",
                goals=all_goals))
            for index in pending:
                outcomes[index] = failure
            return tuple(outcomes)
        try:
            check = audit_check(method(traces), required_name.replace("_", "-"))
        except (Refused, WitnessInconsistent) as exc:
            for index in pending:
                outcomes[index] = exc
            return tuple(outcomes)
        except Exception as exc:                              # noqa: BLE001
            failure = Refused(Refusal(
                f"closure-query-witness-{required_name.replace('_', '-')}-failed",
                f"the required {required_name.replace('_', ' ')} could not run on "
                "the decoded query witnesses",
                goals=all_goals,
                error=f"{type(exc).__name__}: {exc}"))
            for index in pending:
                outcomes[index] = failure
            return tuple(outcomes)
        if check.status is AuditCheckStatus.REFUSED:
            failure = Refused(Refusal(
                check.kind, check.why, **{**check.refusal.detail, "goals": all_goals}))
            for index in pending:
                outcomes[index] = failure
            return tuple(outcomes)
        if check.status is AuditCheckStatus.INCONSISTENT:
            failure = WitnessInconsistent(
                f"closure-query-witness-{required_name.replace('_', '-')}-disagrees",
                f"the required {required_name.replace('_', ' ')} disagrees on a "
                "decoded query witness",
                goals=all_goals, first=check.findings[0],
                count=len(check.findings))
            for index in pending:
                outcomes[index] = failure
            return tuple(outcomes)
    return tuple(outcomes)


def _query_witness(instance, goals, trace, audit_plan):
    """Validate, replay and audit one SAT witness before it contributes a hit."""

    outcome = _query_witnesses(instance, ((tuple(goals), trace),), audit_plan)[0]
    if isinstance(outcome, (Refused, WitnessInconsistent)):
        raise outcome
    return outcome


def _same_trace(left, right):
    """Whether two decoded requests have the same strict finite image."""
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _finish_audited_query_witness(
        instance, goals, trace, audit_plan, got, *, replayed=None):
    """Check one frontend-produced witness against an independent replay."""
    if type(got) is AuditedWitness:
        if not set(goals) & set(got.hits):
            raise WitnessInconsistent(
                "closure-query-witness-misses-goal",
                "the exact query returned a request whose audited trace does not "
                "reach the set it was returned for",
                goals=list(goals))
        if replayed is None:
            replayed = _query_witness(instance, goals, trace, audit_plan)
        elif isinstance(replayed, (Refused, WitnessInconsistent)):
            raise replayed
        agreement = witness_replay_check(got, replayed)
        if agreement.status is AuditCheckStatus.REFUSED:
            raise Refused(Refusal(
                agreement.kind, agreement.why,
                **{**agreement.refusal.detail, "goals": list(goals)}))
        if agreement.status is AuditCheckStatus.INCONSISTENT:
            raise WitnessInconsistent(
                "closure-query-witness-replay-disagrees",
                "the complete audited witness and the independent replay disagree",
                goals=list(goals), first=agreement.findings[0],
                count=len(agreement.findings))
        return got
    if type(got) is not AuditCheck:
        raise Refused(Refusal(
            "closure-query-witness-audit-result-invalid",
            "the decoded query request returned neither an AuditedWitness nor a "
            "typed audit result",
            goals=list(goals), got=type(got).__name__))
    if got.status is AuditCheckStatus.REFUSED:
        raise Refused(Refusal(got.kind, got.why,
            **{**got.refusal.detail, "goals": list(goals)}))
    if got.status is AuditCheckStatus.INCONSISTENT:
        raise WitnessInconsistent(
            "closure-query-witness-audit-disagrees",
            "the decoded query request has inconsistent semantic readings",
            goals=list(goals), first=got.findings[0], count=len(got.findings))
    raise Refused(Refusal(
        "closure-query-witness-audit-empty",
        "the witness audit reported agreement without returning the complete trace",
        goals=list(goals)))


def _audited_query_witnesses(instance, requests, audit_plan):
    """Audit distinct decoded requests as one batch, retaining per-request faults."""

    requests = tuple(requests)
    if not requests:
        return {}
    started = time.monotonic_ns()
    try:
        method = getattr(instance, "audit_requests", None)
        if callable(method):
            candidates = method(tuple(trace for _key, _goals, trace in requests))
        else:
            candidates = tuple(instance.audit_request(trace)
                               for _key, _goals, trace in requests)
        if type(candidates) not in (tuple, list) \
                or len(candidates) != len(requests):
            raise Refused(Refusal(
                "closure-query-witness-audit-batch-invalid",
                "the bound provider returned no audit result for every distinct "
                "decoded request"))
    except (Refused, WitnessInconsistent) as exc:
        candidates = tuple(exc for _ in requests)
    except Exception as exc:                                  # noqa: BLE001
        failure = Refused(Refusal(
            "closure-query-witness-audit-failed",
            "the decoded query requests could not be audited into complete traces",
            error=f"{type(exc).__name__}: {exc}"))
        candidates = tuple(failure for _ in requests)

    independent = _query_witnesses(
        instance,
        tuple((goals, trace) for _key, goals, trace in requests),
        audit_plan)
    outcomes = {}
    for (key, goals, trace), candidate, replayed in zip(
            requests, candidates, independent):
        try:
            if isinstance(candidate, (Refused, WitnessInconsistent)):
                raise candidate
            outcomes[key] = _finish_audited_query_witness(
                instance, goals, trace, audit_plan, candidate,
                replayed=replayed)
        except (Refused, WitnessInconsistent) as exc:
            outcomes[key] = exc
    elapsed = time.monotonic_ns() - started
    # The batched work has no honest per-request split. Attribute its measured
    # wall time once, to the first distinct request, so aggregate telemetry stays
    # exact without pretending that every request paid the whole batch.
    first = requests[0][0]
    return {key: (outcome, elapsed if key == first else 0)
            for key, outcome in outcomes.items()}


def _audit_query_transcript(instance, transcript, asking, known, audit_plan,
                            start_order=0, prior=()):
    """Audit every logical exact attempt and return its typed evidence.

    A provider's per-goal summary is not a query transcript.  In particular, one
    unresolved set attempt followed by singleton retries is several logical attempts,
    and only a singleton UNKNOWN can support a final unresolved goal.  Its
    ``solver_calls`` field states whether the artifact actually launched a physical
    solver or the enclosing budget had already expired.  SAT models are decoded
    requests until this function validates, replays and audits them.
    """
    asked = set(asking)
    trace_required = tuple(
        audit.value for audit in audit_plan.audits
        if audit in {AuditKind.STEP, AuditKind.SOURCE})
    records = []
    sat = []
    unsat = set()
    singleton_unknown = {}
    request_keys = {}
    distinct_requests = {}
    preliminary = []
    for index, call in enumerate(transcript):
        if type(call) is not QueryCall:
            raise Refused(Refusal(
                "closure-query-transcript-entry-invalid",
                "the exact provider's query transcript contains something other "
                "than an exact QueryCall",
                index=index, got=type(call).__name__))
        raw = QueryEvidence(
            start_order + index, call.goals, call.answer,
            decoded_request=(call.witness
                             if call.answer is SolverAnswer.SAT else None),
            reason=(call.reason
                    if call.answer is SolverAnswer.UNKNOWN else ""),
            telemetry=call.telemetry, solver_calls=call.solver_calls)
        if not set(call.goals) <= asked:
            raise QueryRefused(Refusal(
                "closure-query-transcript-goal-mismatch",
                "a logical query in this round asks about a goal outside the "
                "round's requested set",
                index=index, unasked=sorted(set(call.goals) - asked)),
                tuple(prior) + tuple(preliminary) + (raw,))
        preliminary.append(raw)
        if call.answer is SolverAnswer.SAT:
            try:
                key = canonical_json(call.witness)
            except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                raise QueryRefused(Refusal(
                    "closure-query-witness-image-invalid",
                    "a decoded SAT request has no canonical finite image",
                    index=index, error=f"{type(exc).__name__}: {exc}"),
                    tuple(prior) + tuple(preliminary)) from None
            request_keys[index] = key
            distinct_requests.setdefault(key, (call.goals, call.witness))
    requests = tuple((key, goals, trace)
                     for key, (goals, trace) in distinct_requests.items())
    audited_requests = _audited_query_witnesses(instance, requests, audit_plan)
    reported_audit_time = set()
    for index, call in enumerate(transcript):
        raw = QueryEvidence(
            start_order + index, call.goals, call.answer,
            decoded_request=(call.witness
                             if call.answer is SolverAnswer.SAT else None),
            reason=(call.reason
                    if call.answer is SolverAnswer.UNKNOWN else ""),
            telemetry=call.telemetry, solver_calls=call.solver_calls)
        if call.answer is SolverAnswer.SAT:
            try:
                request_key = request_keys[index]
                witness, audit_ns = audited_requests[request_key]
                if request_key in reported_audit_time:
                    audit_ns = 0
                else:
                    reported_audit_time.add(request_key)
                if isinstance(witness, (Refused, WitnessInconsistent)):
                    raise witness
                hits = tuple(sorted(witness.hits))
                # Auditing is a property of the complete request and frozen
                # instance, so an identical request is replayed once.  Its reuse
                # still has to satisfy this call's own disjunction.
                if not set(call.goals) & set(hits):
                    raise WitnessInconsistent(
                        "closure-query-witness-misses-goal",
                        "the exact query returned a request whose audited trace "
                        "does not reach the set it was returned for",
                        goals=list(call.goals))
            except WitnessInconsistent as exc:
                exc.queries = tuple(prior) + tuple(records) + (raw,)
                raise
            except Refused as exc:
                raise QueryRefused(
                    exc.refusal,
                    tuple(prior) + tuple(records) + (raw,)) from None
            unknown = set(witness.hits) - set(known)
            if unknown:
                raise WitnessInconsistent(
                    "closure-query-witness-hit-outside-catalogue",
                    "an audited query witness reports a goal outside the goal "
                    "catalogue",
                    queries=tuple(prior) + tuple(records) + (raw,),
                    goals=sorted(unknown)[:8], count=len(unknown))
            telemetry = call.telemetry
            if telemetry is not None:
                telemetry = telemetry.with_settled(
                    tuple(sorted(set(hits) & set(call.goals))),
                    replay_audit_ns=audit_ns)
            records.append(QueryEvidence(
                start_order + index, call.goals, SolverAnswer.SAT,
                decoded_request=call.witness,
                audited_witness=witness.as_record(),
                replayed_hits=hits, passed_audits=trace_required,
                telemetry=telemetry, solver_calls=call.solver_calls))
            sat.append((call, witness, hits))
        elif call.answer is SolverAnswer.UNSAT:
            records.append(raw)
            unsat.update(call.goals)
        else:
            records.append(raw)
            if len(call.goals) == 1:
                singleton_unknown.setdefault(call.goals[0], call.reason)
    return records, sat, unsat, singleton_unknown


def _resolve(claims, known):
    """One status per goal, by a priority that is written down.

    Three kinds of claim arrive about the same goal in the same round, and which
    one wins cannot be left to the order they happen to be recorded in. It was,
    by way of ``setdefault``, and the effect was that a goal whose own query
    timed out stayed undecided while a trace from the same round sat in the
    record reaching it.

    In order:

    Contradictory claims are rejected before this function: they stop the whole
    analysis rather than entering the per-goal partition.  Of the remaining
    claims:

    1. **reached**, when a trace on record reaches it. This is the only claim
       backed by a run, so nothing that is not about the prover outranks it.
    2. **unreachable**, when a query says so and no run contradicts it.
    3. **unresolved**, which is what is left. Failing to decide never improves
       a number, so it has to lose to every claim that decided something.
    """
    reached, unreachable, unresolved = claims
    out = {}
    for goal in known:
        if goal in reached:
            trace, origin = reached[goal]
            out[goal] = GoalEvidence(GoalStatus.REACHED, origin, trace)
        elif goal in unreachable:
            origin, why = unreachable[goal]
            out[goal] = GoalEvidence(GoalStatus.UNREACHABLE, origin,
                                     detail=why)
        elif goal in unresolved:
            origin, why = unresolved[goal]
            out[goal] = GoalEvidence(GoalStatus.UNRESOLVED, origin, detail=why)
        else:
            out[goal] = GoalEvidence(GoalStatus.UNRESOLVED, Origin.NOT_ASKED)
    return out


def _query_round(open_goals, catalogue, batch, *, streaming=False):
    """Let an incremental backend plan the complete remaining goal set.

    Provider labels describe catalogue ownership, not construction cost. A
    streaming backend already owns its shared constructions and can publish
    easy batches before hard ones. Legacy aggregate backends retain the
    smaller-provider-first policy, with stable goal-ID ties.
    An explicit caller batch retains the caller's existing ordered grouping.
    """
    if batch is not None:
        return open_goals[:batch], open_goals[batch:]
    if streaming:
        return open_goals, []
    groups = {}
    for goal in open_goals:
        groups.setdefault(catalogue[goal].provider, []).append(goal)
    asking = min(groups.values(), key=lambda group: (len(group), tuple(group)))
    selected = set(asking)
    return asking, [goal for goal in open_goals if goal not in selected]


def _query_rounds(instance, catalogue, plan, batch, queries, reached, unreachable, unresolved):
    """One round can return several parts, with the same frozen premises.

    No part is evidence here: the consumer applies the ordinary transcript and
    native checks before resuming this iterator. UNKNOWN sets settle nothing.
    """
    pending = sorted(set(catalogue) - set(reached) - set(unreachable) - set(unresolved))
    streaming = callable(getattr(instance.instance, 'query_steps', None))
    while pending:
        asking, pending = _query_round(pending, catalogue, batch, streaming=streaming)
        check_analysis()
        limits = dict(queries)
        timeout = remaining_query_ms(limits.get('timeout_ms'))
        if timeout is not None:
            limits['timeout_ms'] = timeout
        if streaming:
            residual = set(asking)
            with closing(instance.query_steps(asking, **limits)) as parts:
                for part in parts:
                    yield tuple(sorted(residual)), part, None, True
                    residual -= set(part.reachable) | set(part.unreachable) | set(part.undecided)
                    # The consumer has now audited this part. Native replay
                    # can reach goals beyond the solver's reported subset;
                    # do not resume an obsolete cursor once every requested
                    # goal has evidence. Retain its original pending set while
                    # any remain, since the provider has not received feedback.
                    if residual <= set(reached) | set(unreachable) | set(unresolved):
                        residual.clear()
                        break
            if residual:
                raise Refused(Refusal('closure-query-goal-set-mismatch',
                    'an incremental query ended without answering every requested goal',
                    missing=sorted(residual), unasked=[]))
        else:
            feedback = None
            try:
                if callable(getattr(instance.instance, 'query_with_feedback', None)):
                    from .query_feedback import QueryFeedback

                    feedback = QueryFeedback(instance, asking, catalogue, plan)
                decided = instance.query(asking, **limits,
                    **({'_feedback': feedback} if feedback is not None else {}))
                yield asking, decided, feedback, False
            finally:
                if feedback is not None:
                    feedback.close()
        pending = [goal for goal in pending if goal not in reached]


def close_over(instance, pool=(), steps=1, batch=None, *,
               catalogue_manifest=None, audit_plan=None,
               _audited_witnesses=None, **queries):
    """Own query cursors through normal completion, refusal and cancellation."""
    with ExitStack() as lifetime:
        return _close_over(instance, pool, steps, batch,
            catalogue_manifest=catalogue_manifest, audit_plan=audit_plan,
            _audited_witnesses=_audited_witnesses, _lifetime=lifetime, **queries)


def _close_over(instance, pool=(), steps=1, batch=None, *,
               catalogue_manifest=None, audit_plan=None,
               _audited_witnesses=None, _lifetime, **queries):
    """Decide what the pool left open, one status per goal.

    Raw pool requests are replayed first; the coordinator can instead hand off
    complete witnesses after it has finished their required audits. Each remaining
    round is passed to the exact provider as one set. Incremental providers
    plan the complete open set; legacy aggregate providers get separate rounds,
    smallest residual first. ``batch`` requests explicit ordered chunks.
    SAT requests are admitted only after domain validation,
    replay and every required audit.  UNSAT supports exactly the goals named by
    its recorded call, and only singleton UNKNOWN calls support per-goal
    unresolved status.

    ``instance`` must already be a :class:`BoundedInstance`, so the domain,
    catalogue and audit plan are fixed before any evidence is gathered.  A
    decoded witness outside that domain is a run-level inconsistency; a missing
    audit capability is a run-level refusal.
    """
    if batch is not None and (type(batch) is not int or batch < 1):
        raise ValueError(
            f"a batch must be a positive integer, got {batch!r}")
    if type(instance) is not BoundedInstance:
        raise Refused(Refusal(
            "closure-unbound-instance",
            "this instance is not bound to a domain, so there is nothing to check the "
            "pool against and no set of traces the conclusion would be about",
            instance=type(instance).__name__,
            hint="wrap it: BoundedInstance(instance, TraceDomain(bound, contract))"))
    steps = instance.domain.bound
    # Reconcile required audits before replay or bounded queries can produce evidence.
    try:
        current_plan = instance.audit_plan()
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        raise Refused(Refusal(
            "closure-audit-plan-unavailable",
            "the exact provider could not state the typed audits required before "
            "the first bounded query",
            error=f"{type(exc).__name__}: {exc}")) from None
    if type(current_plan) is not AuditPlan:
        raise Refused(Refusal(
            "closure-audit-plan-invalid",
            "the exact provider must return the closed AuditPlan record",
            got=type(current_plan).__name__))
    plan = current_plan if audit_plan is None else audit_plan
    if type(plan) is not AuditPlan:
        raise TypeError("a frozen closure audit plan must be an exact AuditPlan")
    if audit_plan is not None and current_plan != plan:
        raise Refused(Refusal(
            "closure-audit-plan-changed",
            "the exact provider's required audit plan changed after it was frozen",
            frozen=plan.as_dict(), current=current_plan.as_dict()))
    if catalogue_manifest is None:
        catalogue = catalogue_of(instance.goals(), allow_legacy=False)
    else:
        if type(catalogue_manifest) is not CatalogueManifest:
            raise TypeError(
                "a frozen closure catalogue must be an exact CatalogueManifest")
        catalogue = catalogue_manifest
        catalogue.reconcile(
            instance.goals(), "closure", exhaustive=True)
    known = set(catalogue)

    # Claims, gathered before any of them is resolved. Keeping them apart is
    # what makes the priority in `_resolve` a rule rather than an accident of
    # the order the loop below happens to record things in.
    reached, unreachable, unresolved = {}, {}, {}
    query_records = []
    transcript_complete = True

    pool_hits, broken = _replay_pool(instance, pool, known, _audited_witnesses)
    for goal, trace in pool_hits.items():
        reached[goal] = (trace, Origin.POOL)

    # Retain frontend claims as diagnostics only. They cannot remove a goal
    # from the residual or substitute for exact UNSAT evidence.
    claimed = {goal: why
               for goal, why in getattr(instance, "ruled_out", dict)().items()
               if goal in known}

    rounds = _lifetime.enter_context(closing(_query_rounds(
        instance, catalogue, plan, batch, queries, reached, unreachable, unresolved)))
    while True:
        feedback = None
        try:
            asking, decided, feedback, incremental = next(rounds)
        except StopIteration:
            break
        except AnalysisStopped as exc:
            if feedback is not None:
                feedback.close()
            # Retain only completed rounds and the already checked pool. The
            # interrupted call has no answer, so it cannot manufacture UNKNOWN
            # or UNSAT evidence. This remains a stopped run, not a partition.
            raise QueryRefused(Refusal(
                "analysis-budget-exhausted",
                "the shared analysis wall budget ended during goal closure; "
                "completed evidence is retained but classification is incomplete",
                wall_seconds=exc.wall_seconds,
                elapsed_seconds=exc.elapsed_seconds,
                notes=list(getattr(exc, "__notes__", ())),
                completed_reached=sorted(reached),
                completed_unreachable=sorted(unreachable),
                completed_unresolved=sorted(unresolved),
                pending=sorted(known - set(reached) - set(unreachable) - set(unresolved))),
                query_records) from None
        except Refused as exc:
            raise QueryRefused(Refusal(exc.refusal.kind, exc.refusal.why,
                **{**exc.refusal.as_dict().get('detail', {}),
                   'completed_reached': sorted(reached),
                   'completed_unreachable': sorted(unreachable),
                   'completed_unresolved': sorted(unresolved),
                   'pending': sorted(known - set(reached) - set(unreachable) - set(unresolved))}),
                query_records) from None
        except BaseException:
            if feedback is not None:
                feedback.close()
            raise
        if type(decided) is not QueryVerdict:
            if feedback is not None:
                feedback.close()
            raise Refused(Refusal(
                "closure-query-result-invalid",
                "the exact provider returned something outside the closed query "
                "result algebra",
                got=type(decided).__name__))
        answered = (set(decided.reachable) | set(decided.unreachable)
                    | set(decided.undecided))
        asked = set(asking)
        if incremental and not decided.queries:
            raise QueryRefused(Refusal('closure-query-part-without-transcript',
                'every incremental answer must carry at least one exact call'), query_records)
        if decided.queries is None:
            # Compatibility for the old low-level closure tests and diagnostic
            # frontends.  The certificate-grade coordinator sees this flag and refuses
            # to publish a conditional conclusion: a partition is not evidence of which
            # formulas were actually asked.
            transcript_complete = False
            audited_calls, sat_calls, unsat_calls, singleton_unknown = \
                [], [], set(), {}
            if feedback is not None:
                try:
                    feedback.finish(())
                finally:
                    feedback.close()
        else:
            try:
                if feedback is not None:
                    feedback.finish(decided.queries)
                audited_calls, sat_calls, unsat_calls, singleton_unknown = \
                    _audit_query_transcript(
                        instance, decided.queries, asking, known, plan,
                        start_order=len(query_records), prior=query_records)
                if feedback is not None:
                    feedback.check_audits(sat_calls)
            finally:
                if feedback is not None:
                    feedback.close()
            query_records.extend(audited_calls)

        if not answered <= asked or (not incremental and answered != asked):
            refusal = Refusal(
                "closure-query-goal-set-mismatch",
                "an exact query must return exactly one answer for every goal it was "
                "asked and no answer for an unasked goal; otherwise an unasked UNSAT "
                "could become bounded-unreachable",
                missing=sorted(asked - answered),
                unasked=sorted(answered - asked))
            if decided.queries is not None:
                raise QueryRefused(refusal, query_records)
            raise Refused(refusal)

        if decided.queries is not None:

            for goal, trace in decided.reachable.items():
                if not any(goal in replayed.hits and _same_trace(trace, call.witness)
                           for call, replayed, _hits in sat_calls):
                    raise WitnessInconsistent(
                        "closure-query-summary-sat-disagrees",
                        "the exact provider's SAT summary is not supported by any "
                        "actual SAT call after replay",
                        queries=query_records,
                        goal=goal)
            unsupported_unsat = set(decided.unreachable) - unsat_calls
            if unsupported_unsat:
                raise QueryRefused(Refusal(
                    "closure-query-summary-unsat-unsubstantiated",
                    "the exact provider called a goal bounded-unreachable without an "
                    "actual UNSAT call containing it",
                    goals=sorted(unsupported_unsat)), query_records)
            replayed_hits = set().union(
                *(set(hits) for _call, _replayed, hits in sat_calls)) \
                if sat_calls else set()
            contradictory_unsat = ((unsat_calls & (replayed_hits | set(reached)))
                                   | (set(unreachable) & replayed_hits))
            if contradictory_unsat:
                raise WitnessInconsistent(
                    "closure-query-transcript-unsat-refuted",
                    "an actual UNSAT call is refuted by a decoded request whose "
                    "independent replay reaches the same goal",
                    queries=query_records,
                    goals=sorted(contradictory_unsat)[:8])
            omitted_unsat = unsat_calls - set(decided.unreachable)
            if omitted_unsat:
                raise QueryRefused(Refusal(
                    "closure-query-summary-omits-unsat",
                    "the exact provider's summary omits a goal established by an "
                    "actual UNSAT call",
                    goals=sorted(omitted_unsat)), query_records)
            unsupported_unknown = (
                set(decided.undecided) - set(singleton_unknown))
            if unsupported_unknown:
                raise QueryRefused(Refusal(
                    "closure-query-unresolved-without-singleton",
                    "a goal may be unresolved only after its own exact singleton "
                    "query returns unresolved; a worker failure, exhausted cap or "
                    "unsupported hit term is a run-level refusal",
                    goals=sorted(unsupported_unknown)), query_records)
            for goal, why in decided.undecided.items():
                if why != singleton_unknown[goal]:
                    raise QueryRefused(Refusal(
                        "closure-query-summary-unknown-disagrees",
                        "the per-goal unresolved reason differs from the actual "
                        "singleton query response",
                        goal=goal,
                        summary=why, query=singleton_unknown[goal]),
                        query_records)
        for goal in decided.unreachable:
            unreachable.setdefault(goal, (Origin.QUERY, None))
        for goal, why in decided.undecided.items():
            unresolved.setdefault(goal, (Origin.QUERY, why))
        for goal, trace in decided.reachable.items():
            # Transcript-bearing providers have already replayed every SAT call.  A
            # legacy aggregate is kept working for diagnostic closure callers, but its
            # result is never publishable as certificate-grade evidence.
            got = (_query_witness(instance, (goal,), trace, plan)
                   if decided.queries is None else
                   next(replayed for call, replayed, _hits in sat_calls
                        if goal in replayed.hits and _same_trace(trace, call.witness)))
            for hit in got.hits & known:
                reached.setdefault(hit, (trace, Origin.QUERY))

    # The catalogue and audit plan are premises of every call above.  Re-read them
    # after the transcript so a mutable or replaced provider cannot answer queries
    # under one premise and publish the partition under another.
    try:
        ending_plan = instance.audit_plan()
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        raise QueryRefused(Refusal(
            "closure-audit-plan-recheck-failed",
            "the exact provider could not reproduce its required audit plan after "
            "the bounded calls",
            error=f"{type(exc).__name__}: {exc}"), query_records) from None
    if type(ending_plan) is not AuditPlan or ending_plan != plan:
        raise WitnessInconsistent(
            "closure-audit-plan-changed",
            "the exact provider changed its required audit plan while bounded "
            "evidence was being gathered",
            queries=query_records, before=plan.as_dict(),
            after=(ending_plan.as_dict()
                   if type(ending_plan) is AuditPlan else
                   {"invalid_type": type(ending_plan).__name__}))
    try:
        catalogue.reconcile(instance.goals(), "closure", exhaustive=True)
    except Refused as exc:
        raise WitnessInconsistent(
            "closure-catalogue-changed",
            "the exact provider changed its goal catalogue while bounded evidence "
            "was being gathered",
            queries=query_records, cause=exc.refusal.kind) from None
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        raise QueryRefused(Refusal(
            "closure-catalogue-recheck-failed",
            "the exact provider could not reproduce its goal catalogue after the "
            "bounded calls",
            error=f"{type(exc).__name__}: {exc}"), query_records) from None

    # Contradictions are findings about the prover, not statuses of individual
    # goals.  Once an admitted run reaches anything an exact query called
    # unreachable, no member of that query-derived partition is publishable.
    refuted = sorted(set(reached) & set(unreachable))
    if refuted:
        raise WitnessInconsistent(
            "closure-unreachable-refuted",
            "an admitted run reaches a goal that an exact query called bounded "
            "unreachable",
            queries=query_records, goals=refuted[:8], count=len(refuted))

    # Construction claims are diagnostics rather than proof.  A concrete
    # counterexample nevertheless shows that the frontend and its executable
    # semantics disagree, so it stops the analysis globally as well.
    refuted_claims = sorted(set(reached) & set(claimed))
    if refuted_claims:
        raise WitnessInconsistent(
            "closure-construction-claim-refuted",
            "an admitted run reaches a goal that the frontend ruled out during "
            "construction",
            queries=query_records, goals=refuted_claims[:8],
            count=len(refuted_claims),
            first_reason=claimed[refuted_claims[0]])

    return Closure(
        _resolve((reached, unreachable, unresolved), known),
        catalogue,
        steps,
        broken=broken,
        construction_claims=claimed,
        domain=instance.domain.digest(),
        queries=query_records,
        query_transcript_complete=transcript_complete,
    )


#: How each per-goal status reads.  Prover disagreement stops the analysis globally
#: and therefore is deliberately absent here.
STATUS_ORDER = (
    (GoalStatus.REACHED, "a trace reaches it"),
    (GoalStatus.UNREACHABLE, "no admissible trace reaches it, at the bound"),
    (GoalStatus.UNRESOLVED, "neither was established"),
)

ORIGIN_READS = {
    Origin.POOL: "a witness from the pool",
    Origin.QUERY: "a query, replayed",
    Origin.CONSTRUCTION: "the construct",
    Origin.BUDGET: "the budget ran out",
    Origin.NOT_ASKED: "nothing asked",
}


def _where(goal, lines):
    """Render source location from explicit provenance, not goal-ID syntax."""
    if goal is None:
        return ""
    line = goal.where("line")
    if line is None:
        return ""
    if lines:
        try:
            return lines[int(line) - 1].strip()
        except (ValueError, IndexError):
            return f"line {line}"
    return f"line {line}"


def report(closure, proc=None, source=None):
    """The closure as lines a reader can check, one per goal.

    A verdict is reviewable next to the thing it is about, so where the goal
    lives is printed beside it. What that means is the frontend's to say: a
    Python goal names a source position and an RTL goal will name something
    else, and this prints whichever was declared.
    """
    lines = source.splitlines() if source else []
    out = [f"{len(closure.evidence)} goals over {closure.steps} step(s)"]
    for status, why in STATUS_ORDER:
        held = sorted(closure.with_status(status))
        if not held:
            continue
        out.append(f"\n{status.value} ({len(held)}) — {why}")
        for key in held:
            got = closure.of(key)
            entry = closure.of_goal(key)
            note = got.detail or ORIGIN_READS.get(got.origin, "")
            kind = " failure" if (status is GoalStatus.REACHED and entry
                                  and entry.cls == SAFETY) else ""
            text = _where(entry, lines)
            out.append(f"  {key}{kind}  [{note}]"
                       + (f"   {text[:60]}" if text else ""))
    return "\n".join(out)
