"""Audit one concrete RTL run into its finite task-protocol exact product."""

from __future__ import annotations

from contextlib import contextmanager

from ..core.admission import AdmittedTask
from ..core.analysis import RecordAudit
from ..core.audit import AuditCheck, AuditCheckStatus, AuditedWitness, WitnessInconsistent
from ..core.audit_plan import AuditPlan
from ..core.catalogue import CatalogueManifest
from ..core.conditional_evidence import TrustedAssumption
from ..core.domain import TraceDomain
from ..core.frozen import Frozen
from ..core.instance import BoundedInstance, Feasible, Replay, Verdict
from ..core.query import FeasibilityCall
from ..core.query_telemetry import (
    AnalysisCacheBinding,
    QueryTelemetry,
    analysis_semantic_instance,
)
from ..core.record_value import freeze_record_value, thaw_record_value
from ..core.refusal import Refusal, Refused
from ..core.run_record import RunResult
from ..core.stimulus import Completion, Stimulus, contract_of_domain
from ..stimulus_set import GenerationSpec

__all__ = [
    "RTLAnalysisRequest",
    "RTLIsolatedAnalysisInstance",
]


def _record_identity_audit(result, task, domain):
    """Check the original identity before deriving any fixed-window view."""
    if type(result) is not RunResult:
        return RecordAudit.refused(
            "rtl-record-not-a-run-result",
            f"the concrete entry is a {type(result).__name__}, not a RunResult")
    if task is None or getattr(getattr(task, "contract", None), "digest", None) != domain.task_contract:
        return RecordAudit.refused(
            "rtl-record-task-unbound", "the record audit needs the concrete TaskContract")
    if not result.ran:
        return RecordAudit.refused(
            "rtl-record-run-unavailable",
            f"the raw run has no trace: {result.run_failure or 'no reason recorded'}")
    if not result.trace.artifact:
        return RecordAudit.refused(
            "rtl-record-artifact-unbound", "the raw RTL record does not identify its source artifact")
    if result.trace.artifact != domain.artifact:
        return RecordAudit.inconsistent("the raw RTL record and exact product name different artifacts")
    if contract_of_domain(result.trace.domain) != domain.task_contract:
        return RecordAudit.inconsistent("the raw RTL record and exact product name different task contracts")
    if result.trace.completion is Completion.CHECKPOINT:
        return RecordAudit.outside(
            "a transaction checkpoint is not a complete execution of the analysis domain")
    if result.trace.completion is not Completion.NORMAL:
        return RecordAudit.refused(
            "rtl-record-run-incomplete", "only a normally completed RTL record can enter exact analysis")
    return None


def _record_domain_audit(result, task, domain, *, queue_bound, schedule, memory_environment):
    """Exclude other finite environments; None still requires the exact audit."""
    identity = _record_identity_audit(result, task, domain)
    if identity is not None:
        return identity
    # Runtime requests contain queues, not a fixed history. The immutable trace
    # already binds their resolved schedule without changing those inputs.
    stimulus = result.trace.stimulus
    if len(stimulus.slots) != domain.bound:
        return RecordAudit.outside(
            'the raw record has a different horizon than the declared analysis domain '
            f'(record={len(stimulus.slots)}, analysis={domain.bound} schedule slots; '
            f'queue_bound={queue_bound} counts inputs)')
    if any(stimulus.depth(endpoint.name) > queue_bound for endpoint in task.contract.inputs()):
        return RecordAudit.outside("the raw record exceeds the declared analysis queue bound")
    if schedule is not None:
        from .exact_product import schedule_matches_stimulus

        if not schedule_matches_stimulus(task.contract, schedule, stimulus):
            return RecordAudit.outside("the raw record uses a different GlobalSchedule than the analysis domain")
    elif stimulus.clock_schedule is not None:
        return RecordAudit.outside("the raw record has a physical schedule outside the symbolic-slot profile")
    actual = stimulus.memory_environment
    if actual is not memory_environment and (
            actual is None or memory_environment is None
            # The declared environment is the record's run expanded to the
            # analysis horizon; a run still in policy form expands the same way.
            or actual.materialize(domain.bound).as_dict() != memory_environment.as_dict()):
        return RecordAudit.outside("the raw record uses a different external-memory environment")
    return None


class RTLIsolatedAnalysisInstance:
    """Solver-free parent proxy for one bound RTL exact product.

    Static identities are frozen by the worker's bind reply.  Formula operations cross
    the one named strict-JSON job; independent source executions are obtained through
    the already-isolated ``RTLArtifact`` service and sent back as finite trace images.
    No SMT2 parser or Z3 object exists in this process.
    """

    __slots__ = (
        "_analysis_cache", "_analysis_identity", "_audit_plan", "_binding", "_catalogue",
        "_context", "_contract", "_domain", "_evidence", "_goal_capability",
        "_goal_policy", "_session", "_session_active", "_session_attempted",
        "_session_owner", "_trusted", "_pending_native",
    )

    def __init__(self, context, horizon):
        self._context = context
        self._session = None
        self._session_active = False
        self._session_attempted = False
        self._session_owner = None
        self._binding = None
        self._pending_native = None
        layout = context.prepare_source()
        if isinstance(layout, Refusal):
            raise Refused(layout)
        value = self._raw_call("bind", horizon=horizon)
        try:
            self._accept_binding(value)
        except BaseException:
            self._close_session()
            raise

    def _accept_binding(self, value):
        """Validate the first worker's full identity before exposing the instance."""
        context = self._context
        if isinstance(value, Refusal):
            raise Refused(value)
        fields = {
            "domain", "domain_digest", "analysis_identity", "evidence_identity",
            "analysis_cache",
            "catalogue", "audit_plan", "trusted_assumptions", "contract",
            "goal_implementation",
        }
        if type(value) is not dict or set(value) != fields:
            raise Refused(Refusal(
                "rtl-analysis-isolated-bind-invalid",
                "the isolated RTL analysis bind reply is not a complete identity "
                "record"))
        image = value["domain"]
        if type(image) is not dict or set(image) != {
                "bound", "contract", "initial", "schema", "frontend", "policy",
                "artifact", "task_contract"}:
            raise TypeError("the isolated RTL analysis domain image is not complete")
        domain = TraceDomain(
            image["bound"], contract=image["contract"], initial=image["initial"],
            schema=image["schema"], frontend=image["frontend"],
            policy=image["policy"], artifact=image["artifact"],
            task_contract=image["task_contract"])
        if domain.as_dict() != image or domain.digest() != value["domain_digest"]:
            raise ValueError("the isolated RTL analysis domain is not canonical")
        catalogue = CatalogueManifest.from_dict(value["catalogue"])
        if catalogue != context.complete_catalogue:
            raise Refused(Refusal(
                "rtl-analysis-isolated-catalogue-mismatch",
                "the isolated RTL analysis and frozen concrete catalogue differ"))
        evidence = value["evidence_identity"]
        if type(evidence) is not dict or set(evidence) != {
                "representation", "encoding"} \
                or any(type(item) is not str or not item for item in evidence.values()):
            raise TypeError("the isolated RTL evidence identities are nonempty strings")
        analysis_cache = AnalysisCacheBinding.from_dict(value["analysis_cache"])
        if analysis_cache.semantic_instance != analysis_semantic_instance(
                artifact=domain.artifact, task_contract=domain.task_contract,
                domain=domain.digest(), catalogue=catalogue.digest(),
                representation=evidence["representation"],
                encoding=evidence["encoding"]):
            raise ValueError(
                "the isolated RTL cache identity names another bounded semantic "
                "instance")
        audit_plan = AuditPlan.from_dict(value["audit_plan"])
        trusted = value["trusted_assumptions"]
        if type(trusted) is not list:
            raise TypeError("the isolated RTL trusted assumptions are a finite list")
        assumptions = []
        for row in trusted:
            if type(row) is not dict or set(row) != {
                    "code", "scope", "statement"}:
                raise TypeError("an isolated RTL trusted assumption is complete")
            assumptions.append(TrustedAssumption(
                row["code"], row["statement"], scope=row["scope"]))
        if type(value["contract"]) is not dict:
            raise TypeError("the isolated RTL finite input contract is a mapping")
        analysis_identity = value["analysis_identity"]
        if type(analysis_identity) is not str or not analysis_identity:
            raise TypeError("the isolated RTL analysis identity is nonempty")
        from ..core.goal_provider import RTL_EXACT_BOUNDED_POLICY

        if value["goal_implementation"] != RTL_EXACT_BOUNDED_POLICY:
            raise Refused(Refusal(
                "rtl-analysis-goal-implementation-mismatch",
                "the isolated RTL exact worker reports a different bounded-hit "
                "implementation",
                expected=RTL_EXACT_BOUNDED_POLICY,
                got=value["goal_implementation"]))
        if context.goal_policy is None or context.goal_capability_use is None:
            raise Refused(Refusal(
                "rtl-analysis-goal-capability-missing",
                "the isolated RTL exact provider has no pre-generation goal-policy "
                "handoff"))
        self._domain = domain
        self._catalogue = catalogue
        self._analysis_identity = analysis_identity
        self._analysis_cache = analysis_cache
        self._evidence = dict(evidence)
        self._audit_plan = audit_plan
        self._trusted = tuple(assumptions)
        self._contract = dict(value["contract"])
        self._goal_policy = context.goal_policy
        self._goal_capability = context.goal_capability_use
        from ..core.stimulus import canonical_json

        self._binding = canonical_json(value)

    def _close_session(self):
        try:
            if self._session is not None:
                self._session.close()
        finally:
            self._session = None
            self._pending_native = None

    def _release_owner(self, owner):
        if self._session_owner is owner:
            try:
                self._close_session()
            finally:
                self._session_owner = None
                self._session_attempted = False

    @contextmanager
    def execution_scope(self):
        """Share preparation for one coordinator call; close on every exit path.

        The invocation deadline, not a guessed number of record/goal exchanges,
        bounds this session. Preparation, queries and audits share that lifetime.
        """
        from ..analysis_budget import analysis_scope
        from .replay_build import replay_build_scope

        if self._session_active:
            raise RuntimeError("an isolated analysis scope cannot be nested")
        self._session_active = True
        try:
            # Reuse only the immutable, byte/toolchain-bound executable. Every
            # native witness still comes from its own fresh isolated process.
            with analysis_scope(), replay_build_scope(self._context.artifact):
                yield
        finally:
            try:
                self._close_session()
            finally:
                self._session_active = False
                self._session_owner = None
                self._session_attempted = False

    def _raw_call(self, operation, **extra):
        from ..analysis_budget import active_analysis
        from ..worker import WorkerFailed, run_job_strict
        from .analysis_job import decode_analysis_reply

        if operation == "bind":
            if self._binding is not None:
                raise ValueError("an isolated analysis instance cannot rebind its domain")
            horizon = extra.pop("horizon")
        else:
            if "horizon" in extra:
                raise ValueError("an exact operation cannot override its bound horizon")
            horizon = self._domain.bound
        try:
            # Completed evidence owns immutable rows. Both transport paths need
            # their lossless wire form, not a shallow list of FrozenMap objects.
            extra = thaw_record_value(freeze_record_value(extra))
            budget = active_analysis()
            owned = self._session_active or self._session_owner is not None \
                or (operation == "bind" and budget is not None)
            if not owned:
                payload = {**self._context.analysis_payload(), **extra,
                           "operation": operation, "horizon": horizon}
                timeout_s = (budget.remaining() if budget is not None
                             else self._context.artifact.timeout_s)
                raw = run_job_strict(
                    "rtl_exact_analysis", payload,
                    timeout_s=timeout_s,
                    label=f"isolated RTL exact analysis {operation}")
            else:
                if budget is None or (self._session_owner is not None
                                      and self._session_owner is not budget):
                    raise RuntimeError("an RTL analysis session cannot cross its owning scope")
                if self._session is None:
                    if self._session_attempted:
                        raise RuntimeError("a failed analysis session cannot restart within its scope")
                    from .analysis_transport import RTLAnalysisSession

                    self._session_attempted = True
                    self._session_owner = budget
                    budget.on_close(self._release_owner, budget)
                    self._session = RTLAnalysisSession(
                        {**self._context.analysis_payload(), "horizon": horizon},
                        self._binding, timeout_s=budget.remaining())
                # Opening already constructs and binds the worker. Keep it for
                # the later coordinator instead of proving in a second process.
                raw = (self._session.opened if operation == "bind"
                       else self._session.call(operation, extra))
            return decode_analysis_reply(raw, operation)
        except WorkerFailed as exc:
            self._close_session()
            raise Refused(self._context.unavailable(f"analysis-{operation}", exc)) \
                from None
        except (Refused, WitnessInconsistent):
            self._close_session()
            raise
        except Exception as exc:  # noqa: BLE001 - exact worker protocol boundary
            self._close_session()
            raise Refused(self._context.unavailable(
                f"analysis-{operation}", exc)) from None
        except BaseException as exc:
            exc.add_note(f"RTL exact Analysis operation: {operation}")
            self._close_session()
            raise

    @property
    def domain(self):
        return self._domain

    def bound(self):
        from ..core.query_cache import AnalysisRequestBudget

        return BoundedInstance(
            self, self._domain, analysis_cache=self._analysis_cache,
            request_budget=AnalysisRequestBudget())

    def catalogue_manifest(self):
        return self._catalogue

    def goals(self):
        return dict(self._catalogue.items())

    def analysis_identity(self):
        return self._analysis_identity

    def exact_goal_implementation(self):
        from ..core.goal_provider import RTL_EXACT_BOUNDED_POLICY

        return RTL_EXACT_BOUNDED_POLICY

    def goal_policy(self):
        return self._goal_policy

    def goal_capability_use(self):
        return self._goal_capability

    @staticmethod
    def goal_capability_required():
        return True

    def evidence_identity(self, steps):
        if steps != self._domain.bound:
            raise ValueError("the isolated RTL evidence identity is bound to one horizon")
        return dict(self._evidence)

    def contract(self):
        return dict(self._contract)

    def audit_plan(self):
        return self._audit_plan

    def trusted_assumptions(self):
        return self._trusted

    def task_audit(self, task):
        got = getattr(task, "contract", None)
        if getattr(got, "digest", None) != self._domain.task_contract:
            return AuditCheck.inconsistent(
                "the exact product and concrete result name different task contracts")
        return AuditCheck.agreed()

    def well_formed(self):
        return []

    def unaccounted_goals(self):
        return []

    def ruled_out(self):
        return {}

    def _source_payload(self, rows):
        # This is an execution request, not evidence. Both callers below
        # submit the resulting trace to the complete source audit before
        # returning a Replay or AuditedWitness. Do not build a second whole
        # history merely to prepare the same input coordinates.
        value = self._raw_call("prepare-replay", rows=list(rows))
        if isinstance(value, Refusal):
            raise Refused(value)
        from .initial_state import RTLInitialState, decode_initialization

        if type(value) is not dict or set(value) != {"stimulus", "initialization"}:
            raise TypeError("an exact replay request has a stimulus and initial choice")
        stimulus = Stimulus.from_dict(value["stimulus"])
        initial = decode_initialization(value["initialization"])
        field = "initial_state" if type(initial) is RTLInitialState else "startup_state"
        try:
            trace = self._context.source_trace(stimulus, **{field: initial}, reuse=False)
        except Refused as exc:
            from ..core.failure import FailureOrigin, RunFailure
            from ..core.record_value import thaw_record_value

            image = exc.refusal.detail.get('failure')
            if image is not None:
                failure = RunFailure.from_dict(thaw_record_value(image))
                if (failure.origin is FailureOrigin.CANDIDATE
                        and failure.phase == 'rtl-source-execution'
                        and failure.kind == 'rtl-source-failure'):
                    # The worker still validates initialization, occurrences and
                    # full source membership. Attribution alone grants no hit.
                    return stimulus, failure
            raise Refused(Refusal(
                exc.refusal.kind, exc.refusal.why, **{**exc.refusal.detail,
                    "stimulus": stimulus.as_dict(),
                    "initialization": value["initialization"]})) from exc
        initial.check_trace(trace)
        return stimulus, trace

    def replay(self, rows):
        try:
            _stimulus, trace = self._source_payload(rows)
            value = self._raw_call(
                "replay-source", rows=list(rows), trace=trace.as_dict())
            if isinstance(value, Refusal):
                raise Refused(value)
            from .analysis_job import decode_replay

            return decode_replay(value)
        except WitnessInconsistent:
            raise
        except Exception as exc:  # noqa: BLE001 - Replay has a broken outcome
            return Replay((), broken=f"{type(exc).__name__}: {exc}", steps=len(rows))

    def audit_request(self, rows, domain):
        if domain != self._domain.digest():
            return AuditCheck.refused(
                "rtl-analysis-request-domain-mismatch",
                "the request audit cannot name another bound domain")
        from ..core.stimulus import canonical_json

        pending = getattr(self, '_pending_native', None)
        if pending is not None and pending[0] == canonical_json(rows):
            self._pending_native = None
            return pending[1]
        try:
            _stimulus, trace = self._source_payload(rows)
            value = self._raw_call(
                "audit-request", rows=list(rows), trace=trace.as_dict(),
                domain=domain)
            if isinstance(value, Refusal):
                return AuditCheck.refused(value.kind, value.why, **value.detail)
            return AuditedWitness.from_record(value)
        except WitnessInconsistent as exc:
            return AuditCheck.inconsistent(str(exc))
        except Refused as exc:
            return AuditCheck.refused(exc.refusal.kind, exc.refusal.why, **exc.refusal.detail)
        except Exception as exc:  # noqa: BLE001 - typed audit boundary
            return AuditCheck.refused(
                "rtl-analysis-isolated-witness-audit-failed",
                f"the isolated witness audit raised {type(exc).__name__}: {exc}")

    def audit(self, traces):
        try:
            value = self._raw_call(
                "step-audit", traces=[list(rows) for rows in traces])
            if isinstance(value, Refusal):
                return AuditCheck.refused(value.kind, value.why, **value.detail)
            from .analysis_job import decode_audit_check

            return decode_audit_check(value)
        except WitnessInconsistent as exc:
            return AuditCheck.inconsistent(str(exc))
        except Refused as exc:
            return AuditCheck.refused(exc.refusal.kind, exc.refusal.why, **exc.refusal.detail)

    def source_audit(self, traces):
        if not traces:
            return AuditCheck.refused(
                "rtl-task-source-audit-no-traces",
                "the independent RTL source audit needs at least one finite trace")
        for rows in traces:
            got = self.audit_request(rows, self._domain.digest())
            if type(got) is AuditCheck:
                return got
        return AuditCheck.agreed()

    def feasible(self, steps, timeout_ms=None):
        if steps != self._domain.bound:
            raise ValueError("the isolated feasibility query is bound to one horizon")
        value = self._raw_call("feasible", timeout_ms=timeout_ms)
        if isinstance(value, Refusal):
            raise Refused(value)
        if type(value) is not dict or set(value) != {
                "answer", "detail", "witness", "telemetry"}:
            raise TypeError("an isolated feasibility answer has its complete fields")
        answer = Feasible(value["answer"])
        if answer is Feasible.YES:
            if type(value["witness"]) is not list or value["detail"] != "":
                raise ValueError("a feasible answer carries one decoded request")
            return FeasibilityCall(
                answer, value["witness"],
                telemetry=(
                    None if value["telemetry"] is None else
                    QueryTelemetry.from_dict(value["telemetry"])))
        if type(value["detail"]) is not str or not value["detail"] \
                or value["witness"] is not None:
            raise ValueError("an unresolved/infeasible answer carries only its reason")
        return FeasibilityCall(
            answer, value["detail"],
            telemetry=(
                None if value["telemetry"] is None else
                QueryTelemetry.from_dict(value["telemetry"])))

    def query(self, goals, steps, timeout_ms=None):
        if steps != self._domain.bound:
            raise ValueError("the isolated goal query is bound to one horizon")
        value = self._raw_call(
            "query", goals=list(goals), timeout_ms=timeout_ms)
        if isinstance(value, Refusal):
            raise Refused(value)
        return self._confirm_proposals(Verdict.from_record(value))

    def _proposal_witness(self, rows):
        """One evidence boundary for symbolic and streaming SAT proposals."""
        witness = self.audit_request(rows, self._domain.digest())
        if type(witness) is AuditedWitness:
            return witness
        if type(witness) is AuditCheck:
            if witness.status is AuditCheckStatus.INCONSISTENT:
                raise WitnessInconsistent('rtl-source-proposal-native-inconsistent',
                    '; '.join(witness.findings))
            if witness.status is AuditCheckStatus.REFUSED:
                raise Refused(witness.refusal)
        raise Refused(Refusal('rtl-source-proposal-native-unavailable',
            'the proposal needs a complete native witness'))

    def _confirm_proposals(self, verdict):
        """A source-cover SAT is not a native reachability answer by itself.

        Confirm complete native evidence before exposing SAT to the ordinary
        closure. A non-native scheduler proposal stays UNKNOWN, never UNSAT or
        a tool inconsistency. The next witness audit consumes this evidence;
        closure's independent replay still checks it in the normal way.
        """
        from ..core.query import QueryCall, SolverAnswer
        from ..core.stimulus import canonical_json

        semantics = self._domain.schema.get('source_query_semantics')
        if semantics not in {None, 'order-equivalent/1', 'native-cover/1'}:
            raise Refused(Refusal('rtl-analysis-query-semantics-unsupported',
                'unknown source query semantics cannot bypass native confirmation', semantics=semantics))
        if semantics != 'native-cover/1' or not verdict.reachable:
            return verdict
        reachable, undecided, calls, checked = {}, verdict.undecided, [], {}
        reason = 'rtl-source-proposal-not-native-reached'
        for call in verdict.queries:
            if call.answer is not SolverAnswer.SAT:
                calls.append(call)
                continue
            key = canonical_json(call.witness)
            if key not in checked:
                checked[key] = self._proposal_witness(call.witness)
            witness = checked[key]
            self._pending_native = key, witness
            misses = []
            for goal, rows in verdict.reachable.items():
                if canonical_json(rows) == key:
                    if goal in witness.hits:
                        reachable[goal] = rows
                    else:
                        undecided[goal] = reason
                        misses.append(goal)
            if set(call.goals).intersection(witness.hits):
                calls.append(call)
            else:
                telemetry = None
                if call.telemetry is not None:
                    image = call.telemetry.as_dict()
                    image.update(outcome='unknown', goals_settled=list(call.goals) if len(call.goals) == 1 else [])
                    image['provenance'] = {**image.get('provenance', {}),
                                           'source_proposal_outcome': 'sat', 'native_confirmation': 'missed'}
                    telemetry = QueryTelemetry.from_dict(image)
                calls.append(QueryCall(call.goals, SolverAnswer.UNKNOWN, reason=reason,
                                       solver_calls=call.solver_calls, telemetry=telemetry))
            for goal in misses:
                if not (len(call.goals) == 1 and calls[-1].answer is SolverAnswer.UNKNOWN):
                    calls.append(QueryCall((goal,), SolverAnswer.UNKNOWN, reason=reason, solver_calls=0))
        return Verdict(reachable, verdict.unreachable, undecided, queries=tuple(calls))

    def query_steps(self, goals, steps, timeout_ms=None):
        """Return each complete call before asking the retained worker to resume."""
        if steps != self._domain.bound:
            raise ValueError("the isolated goal query is bound to one horizon")
        # Standalone jobs cannot own a resumable cursor. Keep their aggregate
        # contract; public Analysis owns one scoped session and takes this path.
        if self._session is None:
            yield self.query(goals, steps, timeout_ms)
            return
        session = self._session
        finished = False
        try:
            value = self._raw_call('query-start', goals=list(goals), timeout_ms=timeout_ms)
            while True:
                if isinstance(value, Refusal):
                    raise Refused(value)
                if value is None:
                    finished = True
                    return
                if type(value) is dict and set(value) == {'native_proposal'}:
                    # This frame is only an execution request. The worker
                    # resumes with its OWN complete audit, never a client hit
                    # list or the prefix solver's modeled observations.
                    from ..core.stimulus import canonical_json

                    rows = value['native_proposal']
                    # Resume requires this worker's audit, not an earlier
                    # parent-side witness cache hit for identical inputs.
                    self._pending_native = None
                    try:
                        witness = self._proposal_witness(rows)
                    except Refused as exc:
                        refusal = exc.refusal
                        driver = refusal.detail.get('driver_refusal', {})
                        outside = (refusal.kind == 'rtl-source-witness-environment-invalid'
                            and refusal.detail.get('audit', {}).get('disposition') == 'outside'
                            or refusal.kind == 'rtl-native-run-interrupted'
                            and driver.get('kind') == 'input-framing-violated')
                        if not outside:
                            raise  # Observation/transport failures are not misses.
                        value = self._raw_call('query-discard')
                        continue
                    self._pending_native = canonical_json(rows), witness
                    value = self._raw_call('query-next')
                    continue
                yield self._confirm_proposals(Verdict.from_record(value))
                value = self._raw_call('query-next')
        finally:
            if not finished and self._session is session:
                # Cancellation must release formulas without replacing the
                # original failure with a second, expired-budget RPC.
                self._close_session()

    def audit_record(self, result, task):
        identity = _record_identity_audit(result, task, self._domain)
        if identity is not None:
            return identity
        try:
            from ..core.failure import RunFailure
            from .source_boundary_capture import source_history_prefix

            trace = self._extend_record(result.trace, task)
            trace = source_history_prefix(trace, self._domain.bound)
            if trace is not result.trace:
                result = RunResult.not_compared(trace.stimulus, trace, RunFailure.verifier(
                    'bounded-analysis-view', Refusal('analysis-window-not-compared',
                        'a native analysis window does not inherit the original workload score')))
            audit = _record_domain_audit(
                result, task, self._domain, queue_bound=self._context.generation_spec.queue_bound,
                schedule=self._context.schedule, memory_environment=self._context.memory_environment)
            if audit is not None:
                return audit
            value = self._raw_call(
                "record-audit", stimulus=result.stimulus.as_dict(),
                raw_trace=result.trace.as_dict())
            if isinstance(value, Refusal):
                return RecordAudit.refused(value.kind, value.why, **value.detail)
            return _decode_record_audit(value)
        except WitnessInconsistent as exc:
            return RecordAudit.inconsistent(str(exc))
        except Refused as exc:
            return RecordAudit.refused(exc.refusal.kind, exc.refusal.why, **exc.refusal.detail)
        except Exception as exc:  # noqa: BLE001 - typed worker boundary
            return RecordAudit.refused(
                "rtl-analysis-isolated-record-audit-failed",
                "the isolated record audit returned no canonical answer: "
                f"{type(exc).__name__}: {exc}")

    def _extend_record(self, trace, task):
        """Propose a full native run from a short record, never pad evidence.

        Keep its queue, startup and executed schedule prefix. An idle suffix is
        only a proposed environment: source execution and the ordinary worker
        audit must establish every hit and membership in the declared domain.
        Explicit clock/memory schedules are not extrapolated here.
        """
        from ..core.stimulus import EventSlot
        from .source_boundary_capture import source_history_view
        from .startup_state import RTLStartupState, VERSION as STARTUP_VERSION

        stimulus = trace.stimulus
        context = self._context
        if not 0 < len(stimulus.slots) < self._domain.bound \
                or context.schedule is not None or stimulus.clock_schedule is not None \
                or len(task.contract.clocks) != 1 or task.contract.memories \
                or context.memory_environment is not None or stimulus.memory_environment is not None \
                or any(stimulus.depth(endpoint.name) > context.generation_spec.queue_bound
                       for endpoint in task.contract.inputs()):
            return trace
        if trace.initialization is None and trace.query_boundaries is None:
            return trace  # No recorded startup to preserve; keep ordinary exclusion.
        layout = context.prepare_source()
        if isinstance(layout, Refusal):
            raise Refused(layout)
        try:
            view = source_history_view(trace, layout.state_names)
        except Refused as exc:
            if exc.refusal.kind in {'rtl-record-construction-unrecorded',
                                    'rtl-record-construction-unavailable'}:
                # Optional reuse must not turn an otherwise outside record
                # into an Analysis refusal. Malformed evidence still fails.
                return trace
            raise
        image = thaw_record_value(view.initialization)
        if image.get('version') != STARTUP_VERSION:
            return trace  # A settled deposit is not a pre-HDL startup choice.
        startup = RTLStartupState.from_dict(image)
        last = stimulus.slots[-1]
        idle = EventSlot(controls=dict(last.controls), reset=last.reset,
                         ticks=last.ticks, resets=last.resets)
        proposal = Stimulus(dict(stimulus.queues),
            (*stimulus.slots, *(idle for _ in range(self._domain.bound - len(stimulus.slots)))),
            memory_policy=stimulus.memory_policy, initial_state=stimulus.initial_state,
            source=f'analysis-extension:{self._domain.bound}:{stimulus.fingerprint}')
        extended = context.source_trace(proposal, startup_state=startup, reuse=False)
        if extended.stimulus != proposal:
            raise WitnessInconsistent('rtl-record-extension-stimulus-mismatch',
                                      'native extension returned a different stimulus')
        startup.check_trace(extended)
        return extended


def _decode_record_audit(value):
    if type(value) is not dict or type(value.get("disposition")) is not str:
        raise TypeError("an RTL record-audit image has an exact disposition")
    disposition = value["disposition"]
    if disposition == "admitted" and set(value) in (
            {"disposition", "witness", "witness_ref"},
            {"disposition", "witness", "witness_ref", "audits"}):
        witness = AuditedWitness.from_record(value["witness"])
        if value["witness_ref"] != "witness:" + witness.fingerprint:
            raise ValueError("the record-audit witness reference is inconsistent")
        return RecordAudit.admitted(witness, audits=(
            None if "audits" not in value else AuditPlan.from_dict(value["audits"])))
    if disposition in {"outside", "inconsistent"} \
            and set(value) == {"disposition", "why"}:
        constructor = (RecordAudit.outside if disposition == "outside"
                       else RecordAudit.inconsistent)
        return constructor(value["why"])
    if disposition == "refused" \
            and set(value) in ({"disposition", "kind", "why"},
                               {"disposition", "kind", "why", "detail"}):
        if 'detail' in value and (type(value['detail']) is not dict or not value['detail']):
            raise ValueError('record-audit details must be a nonempty mapping')
        return RecordAudit.refused(value["kind"], value["why"], **value.get('detail', {}))
    raise ValueError("an RTL record-audit image is not canonical")


class RTLAnalysisRequest(Frozen):
    """The frozen post-score inputs needed to bind one RTL exact instance.

    The legacy authority path constructs its concrete artifact before scoring.  This
    record carries that *same* admitted task, artifact, generation policy and frontend
    manifest across the score-persistence boundary; the bounded branch may then build
    its provider without re-reading declarations or inventing another trace domain.
    It contains no result and has no authority over the concrete verdict.
    """

    __slots__ = (
        "_clock_schedule", "_memory_environment",
        "artifact", "exact_context", "frontend_catalogue", "generation_spec",
        "goal_capability", "goal_policy", "goal_refusal", "task",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("RTLAnalysisRequest is a closed post-score capability record")

    def __init__(self, task, artifact, generation_spec, frontend_catalogue,
                 exact_context=None, *, clock_schedule=None, memory_environment=None):
        from ..core.clock_schedule import GlobalSchedule
        from ..core.memory import MemoryEnvironment
        from .artifact import RTLArtifact
        from .exact_job_context import RTLExactJobContext

        if type(task) is not AdmittedTask:
            raise TypeError("an RTL analysis request needs the exact AdmittedTask")
        if type(artifact) is not RTLArtifact:
            raise TypeError("an RTL analysis request needs the exact RTLArtifact")
        if type(generation_spec) is not GenerationSpec:
            raise TypeError(
                "an RTL analysis request needs the frozen GenerationSpec")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError(
                "an RTL analysis request needs the frozen frontend manifest")
        if task.realization.identity != artifact.identity:
            raise Refused(Refusal(
                "rtl-analysis-request-artifact-mismatch",
                "the admitted task and concrete artifact name different source bytes",
                admitted=task.realization.identity, artifact=artifact.identity))
        if exact_context is not None \
                and type(exact_context) is not RTLExactJobContext:
            raise TypeError(
                "an RTL analysis request exact context has its closed exact type")
        if exact_context is not None and (
                exact_context.artifact is not artifact
                or exact_context.contract is not task.contract
                or exact_context.generation_spec is not generation_spec
                or exact_context.frontend_catalogue != frontend_catalogue):
            raise Refused(Refusal(
                "rtl-analysis-goal-policy-context-mismatch",
                "the generation context belongs to another artifact, task, "
                "generation policy or frontend catalogue"))
        from ..core.goal_provider import (
            EXACT_BOUNDED_HIT,
            RTL_EXACT_BOUNDED_POLICY,
        )
        from ..goal_policy import evaluation_goal_policy

        if exact_context is not None and exact_context.goal_policy is not None:
            # Generation already froze the authority.  Do not even construct an equal
            # second policy here: equality would verify the denominator but could not
            # prove which registered adapters generation actually used.
            policy = exact_context.goal_policy
            capability = exact_context.goal_capability_use
        else:
            policy = evaluation_goal_policy(
                task.contract, frontend_catalogue, artifact_kind="rtl")
            capability = None
        goal_refusal = None
        if capability is None:
            try:
                capability = policy.bind_complete_implementation(
                    EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY,
                    policy.manifest)
            except Refused as exc:
                goal_refusal = exc.refusal
        if exact_context is not None:
            exact_context.bind_goal_policy(policy, capability)
        self.task = task
        self.artifact = artifact
        self.generation_spec = generation_spec
        self.frontend_catalogue = frontend_catalogue
        self.exact_context = exact_context
        # A reused context already owns a domain. Otherwise the caller declares
        # it explicitly; the scored pool never chooses or narrows this domain.
        if exact_context is not None:
            if clock_schedule is None:
                clock_schedule = exact_context.schedule
            if memory_environment is None:
                memory_environment = exact_context.memory_environment
        if clock_schedule is not None and type(clock_schedule) is not GlobalSchedule:
            raise TypeError("the analysis clock schedule must be a GlobalSchedule")
        if memory_environment is not None and type(memory_environment) is not MemoryEnvironment:
            raise TypeError("the analysis memory environment must be a MemoryEnvironment")
        self._clock_schedule = clock_schedule
        self._memory_environment = memory_environment
        if exact_context is not None and not self.matches_environment(exact_context):
            raise Refused(Refusal(
                "rtl-analysis-environment-context-mismatch",
                "the exact context and declared analysis environment differ"))
        self.goal_policy = policy
        self.goal_capability = capability
        self.goal_refusal = goal_refusal
        self._freeze()

    def matches_environment(self, context):
        """Reuse construction only for the same declared finite environment."""
        return all(
            declared is actual or (declared is not None and actual is not None
                                   and declared.as_dict() == actual.as_dict())
            for declared, actual in (
                (self._clock_schedule, context.schedule),
                (self._memory_environment, context.memory_environment)))

    def bind(self):
        """Construct the declared exact domain after concrete score persistence."""

        from .analysis_binding import bind_analysis
        from .exact_product import needs_global_schedule_product

        # This capability was frozen before scoring. Do not prepare schedules or
        # a relation when its required event encoding is already unavailable.
        if self.goal_refusal is not None:
            raise Refused(self.goal_refusal)
        if (self.task.contract.clocks and self._clock_schedule is None
                and needs_global_schedule_product(self.task.contract)):
            raise Refused(Refusal(
                "rtl-exact-action-schedule-unbound",
                "this protocol needs a declared finite analysis_clock_schedule; "
                "runtime replay requests do not define the analysis domain"))
        if self.task.contract.memories and self._memory_environment is None:
            raise Refused(Refusal(
                "rtl-exact-memory-environment-unbound",
                "external-memory analysis needs a declared finite "
                "analysis_memory_environment"))
        if self.exact_context is not None and not self.matches_environment(self.exact_context):
            raise Refused(Refusal(
                "rtl-analysis-environment-context-mismatch",
                "the reused exact context changed its frozen analysis environment"))
        return bind_analysis(
            self.task, self.artifact, self.generation_spec,
            self.frontend_catalogue, exact_context=self.exact_context,
            goal_policy=self.goal_policy, goal_capability=self.goal_capability,
            clock_schedule=self._clock_schedule,
            memory_environment=self._memory_environment)

    def as_dict(self):
        schedule = self._clock_schedule
        memory = self._memory_environment
        return {
            "admission": self.task.handoff_identity,
            "task_contract": self.task.contract.digest,
            "artifact": self.artifact.identity,
            "generation_spec": self.generation_spec.as_dict(),
            "analysis_bound": self.generation_spec.horizon,
            "analysis_bound_unit": "schedule-slots",
            "frontend_catalogue": self.frontend_catalogue.digest(),
            "observation": (None if self.artifact.observation_binding is None
                            else self.artifact.observation_binding.as_dict()),
            "goal_policy": self.goal_policy.digest(),
            "goal_capability_use": (
                None if self.goal_capability is None
                else self.goal_capability.as_dict()),
            "goal_capability_refusal": (
                None if self.goal_refusal is None
                else self.goal_refusal.as_dict()),
            "shared_exact_context": self.exact_context is not None,
            "clock_schedule": (None if schedule is None else {
                "digest": schedule.digest,
                "record": schedule.as_dict(),
            }),
            "memory_environment": (None if memory is None else {
                "digest": memory.digest,
                "record": memory.as_dict(),
            }),
        }

