"""Worker protocol and orchestration for bounded RTL Analysis.

Standalone calls and sessions use the same dispatcher. ``analysis_query`` owns
goal formulas and their solver lifetime; ``observation_audit`` owns native
evidence. This module orders queries, validates requests and returns results.
Query preparation is reusable, but neither a cached formula nor a SAT proposal
is an independently confirmed native witness.
"""

from __future__ import annotations

import time
from dataclasses import replace
from functools import cached_property

from ..core.admission import _admitted_task_from_record
from ..symbolic.diagnostics import diagnostic_stage
from ..core.audit import AuditCheck, AuditedWitness, AuditedWitnessLimitError, WitnessInconsistent
from ..core.failure import RunFailure
from ..core.instance import Feasible, Replay, StepOutput
from ..core.monitor import INPUT_COMPLETION
from ..core.query_telemetry import (
    AnalysisCacheBinding,
    QueryKind,
    QueryTelemetry,
    QueryTelemetryScope,
    StageTimings,
    annotate_refusal,
    annotate_verdict,
    process_peak_rss_bytes,
    semantic_instance_of,
)
from ..core.refusal import Refusal, Refused
from ..core.run_record import RunResult
from ..core.stimulus import AuditedTrace, Stimulus
from .exact_job_instance import PreparedSourceExact
from .source_relation import SourceObservation
from .analysis_query import (
    GoalQueryCache, GoalQueryPrefixes, LocalGoalProofs, LocalProofCost,
    NativeQueryProposal, goal_batches, goal_union as _goal_union,
)

__all__ = [
    "decode_analysis_reply",
    "decode_audit_check",
    "decode_replay",
    "run_analysis_job",
]


VERSION = "rtl-exact-analysis/500"
# Storage bounds only: eviction changes cost, never the accepted request domain.
_MEMBERSHIP_CACHE_BYTES = 1 << 20
_MEMBERSHIP_CACHE_KEY_BYTES = 64 << 10


def _membership_key(rows, check):
    """Exact request identity with lossless compression, never a hash match.

    Whole-history rows repeat their shared queue coordinates. Raw JSON can
    exceed the bounded cache even for 100 inputs; compress that representation
    without discarding any field, initial bit, unused cell or schedule slot.
    Queues wider than the compressor window use the witness's structural
    encoding. Incompressible keys still take the normal uncached proof path.
    """
    from zlib import compress
    from ..core.audit import encode_valuation_trace
    from ..core.stimulus import canonical_json

    check()
    raw = canonical_json(rows).encode('utf-8')
    key = b'J' + raw if len(raw) < _MEMBERSHIP_CACHE_KEY_BYTES else b'Z' + compress(raw, 1)
    check()
    if len(key) > _MEMBERSHIP_CACHE_KEY_BYTES:
        image = encode_valuation_trace(rows, 'request', check=check)
        key = b'D' + compress(canonical_json(image).encode('utf-8'), 1)
        check()
    return key


_OPERATION_FIELDS = {
    "bind": frozenset(),
    "decode": frozenset({"rows"}),
    "prepare-replay": frozenset({"rows"}),
    "record-audit": frozenset({"stimulus", "raw_trace"}),
    "replay-source": frozenset({"rows", "trace"}),
    "audit-request": frozenset({"rows", "trace", "domain"}),
    "step-audit": frozenset({"traces"}),
    "feasible": frozenset({"timeout_ms"}),
    "query": frozenset({"goals", "timeout_ms"}),
    "query-start": frozenset({"goals", "timeout_ms"}),
    "query-next": frozenset(),
    "query-discard": frozenset(),
    "query-close": frozenset(),
}
OPERATIONS = frozenset(_OPERATION_FIELDS)


def _rows(value, name="rows"):
    if type(value) is not list or not value:
        raise TypeError(f"{name} is a nonempty exact list of valuations")
    out = []
    for index, row in enumerate(value):
        if type(row) is not dict or any(type(key) is not str or not key
                                        for key in row):
            raise TypeError(f"{name}[{index}] is an exact string-keyed valuation")
        if any(type(item) is not int for item in row.values()):
            raise TypeError(f"{name}[{index}] contains a non-integer value")
        out.append(dict(row))
    return out


def _solve_timings(answer, **measured):
    """Retain measured solver stages; a skipped solve has no invented zeroes."""
    if answer.solver_load_ns is not None:
        measured.update(solver_load_ns=answer.solver_load_ns, solve_ns=answer.solve_ns)
    return StageTimings(measured)


def _reply(operation, value):
    return {
        "version": VERSION,
        "operation": operation,
        "status": "ok",
        "value": value,
    }


def _refused(operation, refusal):
    if type(refusal) is not Refusal:
        raise TypeError("an exact-analysis refusal is an exact Refusal")
    return {
        "version": VERSION,
        "operation": operation,
        "status": "refused",
        "refusal": refusal.as_dict(),
    }


def decode_analysis_reply(value, operation):
    """Decode a value/refusal, or propagate a completed audit's contradiction."""

    if operation not in OPERATIONS:
        raise ValueError("the exact-analysis operation is not registered")
    if type(value) is not dict or value.get("version") != VERSION \
            or value.get("operation") != operation:
        raise ValueError("an isolated exact-analysis reply has the expected identity")
    status = value.get("status")
    if status in ("refused", "inconsistent"):
        field = "refusal" if status == "refused" else "inconsistency"
        if set(value) != {"version", "operation", "status", field}:
            raise ValueError("an adverse exact-analysis reply has no extra fields")
        finding = value[field]
        if type(finding) is not dict or set(finding) not in (
                {"kind", "why"}, {"kind", "why", "detail"}) \
                or any(type(finding[name]) is not str or not finding[name]
                       for name in ("kind", "why")) \
                or "detail" in finding and type(finding["detail"]) is not dict:
            raise TypeError(
                "an exact-analysis finding has exact kind, why and detail")
        if status == "inconsistent":
            raise WitnessInconsistent(
                finding["kind"], finding["why"], **finding.get("detail", {}))
        return Refusal(
            finding["kind"], finding["why"], **finding.get("detail", {}))
    if value.get("status") != "ok" \
            or set(value) != {"version", "operation", "status", "value"}:
        raise ValueError("a successful exact-analysis reply has one typed value")
    return value["value"]


def decode_audit_check(value):
    if type(value) is not dict or type(value.get("status")) is not str:
        raise TypeError("an audit-check image has an exact status")
    status = value["status"]
    if status == "agreed" and set(value) == {"status"}:
        return AuditCheck.agreed()
    if status == "inconsistent" and set(value) == {"status", "findings"} \
            and type(value["findings"]) is list:
        return AuditCheck.inconsistent(value["findings"])
    if status == "refused" and set(value) == {"status", "refusal"}:
        refusal = value["refusal"]
        if type(refusal) is dict and set(refusal) in (
                {"kind", "why"}, {"kind", "why", "detail"}) \
                and type(refusal.get("detail", {})) is dict:
            return AuditCheck.refused(
                refusal["kind"], refusal["why"], **refusal.get("detail", {}))
    raise ValueError("an audit-check image is not canonical")


def _replay_image(value):
    return {
        "hits": sorted(value.hits),
        "outputs": [
            {"count": item.count, "buffers": item.buffers}
            for item in value.outputs
        ],
        "error": value.error,
        "state": value.state,
        "broken": value.broken,
        "steps": value.steps,
    }


def decode_replay(value):
    fields = {"hits", "outputs", "error", "state", "broken", "steps"}
    if type(value) is not dict or set(value) != fields \
            or type(value["hits"]) is not list \
            or type(value["outputs"]) is not list \
            or type(value["error"]) is not str \
            or value["broken"] is not None and type(value["broken"]) is not str \
            or type(value["steps"]) is not int or value["steps"] < 0:
        raise TypeError("an exact replay image has its complete finite field types")
    outputs = []
    for row in value["outputs"]:
        if type(row) is not dict or set(row) != {"count", "buffers"} \
                or type(row["count"]) is not int or type(row["buffers"]) is not dict:
            raise TypeError("an exact replay output has count and buffers")
        outputs.append(StepOutput(row["count"], row["buffers"]))
    return Replay(
        value["hits"], outputs, error=value["error"], state=value["state"],
        broken=value["broken"], steps=value["steps"])


def _tool_provenance(timeout_ms):
    import z3

    out = {
        "backend_version": VERSION,
        "tool": "z3",
        "tool_version": z3.get_version_string(),
    }
    if timeout_ms is not None:
        out["timeout_ms"] = timeout_ms
    return out


def _cache_binding(exact):
    import z3

    return AnalysisCacheBinding(
        semantic_instance=semantic_instance_of(exact), backend="rtl-z3",
        backend_version=VERSION, tool="z3",
        tool_version=z3.get_version_string())


def _bound(payload, extras, *, direct_source=True):
    if payload.get('source_layout') is True:
        source = PreparedSourceExact(payload, extras={*extras, 'admission', 'admission_digest'},
                                     direct=direct_source, observation_scope=SourceObservation.GOAL_PREFIX)
        task = _admitted_task_from_record(source.contract, payload['admission'],
                                         expected_identity=payload['admission_digest'])
        if task.realization.kind != 'rtl' or task.realization.identity != source.artifact:
            raise Refused(Refusal('rtl-exact-admission-binding-mismatch',
                'the admitted task and source image name different artifacts'))
        return _SourceAnalysis(source, payload['horizon']), task
    raise Refused(Refusal(
        "rtl-analysis-image-not-source",
        "RTL exact analysis binds the compiler-owned source image"))


class _SourceOperation:
    """One deadline and solver policy, retained by an incremental query.

    This belongs to the call, not the shared source: audits between cursor
    steps must not replace its budget or provenance. Solver preparation stays
    with the source/query that owns the corresponding formulas.
    """

    def __init__(self, exact, timeout_ms, started_ns):
        from ..symbolic.exact_solve import _Budget

        timeout = _timeout(timeout_ms)
        if timeout is not None:
            timeout -= (time.monotonic_ns() - started_ns) // 1_000_000
            if timeout <= 0:
                raise Refused(Refusal('rtl-source-analysis-budget-exhausted',
                    'source preparation exhausted the operation budget'))
        self.exact, self.timeout = exact, timeout
        self.budget = _Budget(timeout)
        self.domain_admission = None

    def check(self):
        from behave.isolation.preparation_control import check_preparation

        check_preparation()
        if self.budget.remaining_ms() == 0:
            raise Refused(Refusal('rtl-source-analysis-budget-exhausted',
                'source construction or admission exhausted the operation budget'))

    def solve(self, phase, assertions, *, prefer_finite=None, context=None, preparation=None,
              allowance_ms=None):
        from behave.isolation.preparation_control import check_preparation
        from ..symbolic.exact_solve import ExactCheckResult, z3_check_finite
        from ..analysis_budget import remaining_query_ms
        from ..core.query import SolverAnswer

        exact = self.exact
        try:
            check_preparation()
            if context is None and preparation is None:
                preparation = exact._member_preparation(phase)
            left = self.budget.remaining_ms()
            if left != 0:
                left = remaining_query_ms(left)
            if allowance_ms is not None:
                left = allowance_ms if left is None else min(left, allowance_ms)
            if left == 0:
                if preparation is not None:
                    preparation.clear()
                return ExactCheckResult(SolverAnswer.UNKNOWN, reason='source operation budget exhausted')
            # Binding, native audits and decoding have no private query cap;
            # all solving consumes only the enclosing Analysis remainder.
            finite = exact._prefer_finite if prefer_finite is None else prefer_finite
            if ((context is not None and preparation is not None)
                    or phase in {'confluence:protocol:prefix', 'confluence:product:prefix'}):
                # Repeated prefix and congruence checks share SMT preparation.
                finite = False
            with diagnostic_stage('proof', phase=phase, backend='finite' if finite else 'smt',
                                  remaining_ms=left):
                answer = z3_check_finite(assertions, left,
                    _owned_context=exact.source.template.context if context is None else context,
                    _prefer_finite=finite,
                    # Whole-history queries preserve shared equations; local
                    # protocol/transition proofs retain equation elimination.
                    _preserve_equations=phase in {'query', 'query-prefix'},
                    **({} if preparation is None else {'_preparation': preparation}))
            if context is None and answer.answer is SolverAnswer.UNKNOWN:
                # Interruption retires every solver sharing this context,
                # including preparation not used by this call.
                if exact._source_solver is not None:
                    exact._source_solver.clear(interrupted=True)
                exact._queries.clear_solver(interrupted=True)
            check_preparation()
            if self.budget.remaining_ms() == 0:
                if preparation is not None:
                    preparation.clear()
                return ExactCheckResult(SolverAnswer.UNKNOWN, reason='source operation budget exhausted',
                    solver_load_ns=answer.solver_load_ns, solve_ns=answer.solve_ns)
            return answer
        except BaseException as exc:
            if preparation is not None:
                preparation.clear()
            exc.add_note(f'RTL exact Analysis proof phase: {phase}')
            raise

    def provenance(self, timeout_ms):
        return {**_tool_provenance(timeout_ms), 'source_domain_admission': self.domain_admission}


class _SourceAnalysis:
    """Source-backed worker queries; native certificate admission stays separate.

    Complete domain admission and bounded positive request-membership proofs
    are retained alongside formulas. Native records are always projected afresh;
    identical, fully proved native obligations may be reused. No execution,
    target answer survives as reusable evidence. A query model may be handed
    once to its native membership check, after validating every original root;
    it cannot replace the independent native agreement proof. One source-membership
    solver may retain preparation, with fresh request scopes and complete-model
    validation; it cannot establish native agreement or goal reachability.
    Direct queries select a dependency-closed goal batch before unrolling and
    share solver preparation across its setwise/singleton checks. Its partial
    state is not a native observation image: that is constructed on demand.
    This worker establishes the symbolic domain obligations. Public exact
    authority additionally requires independently audited native witnesses;
    transport success alone is not that certificate.
    """

    observation_scope = SourceObservation.PHASE_EVENTS

    def __init__(self, source, horizon):
        from .source_values import ArrayType

        self.source = source
        self.observation_scope = SourceObservation.GOAL_PREFIX
        self.domain = source.trace_domain(horizon, observation_scope=self.observation_scope)
        self.request = source.request_coordinates(horizon)
        self._membership_source = source
        if source.template.direct is None:
            self._base_history = source.bind_request(horizon)
        # Array products can expand dramatically in QF_FD. Direct intervals
        # also use immutable protocol-queue tables, even without RTL memories.
        # Select from representation, not task names or observed runs.
        self._prefer_finite = source.template.direct is None and not source.contract.memories and not any(
            isinstance(kind, ArrayType) for kind in source.machine.program.kinds.values())
        self._comparison = None
        self._admitted_comparison = None
        self._admitted_request = None
        self._request_members = {}
        self._source_solver = None
        self._pending_query_member = None
        self._queries = GoalQueryCache()

    @cached_property
    def _native(self):
        """Native evidence has its own lazy lifetime, not query-model state."""
        from .observation_audit import NativeEvidence

        return NativeEvidence()

    @cached_property
    def _base_history(self):
        """Untargeted feasibility history; fixed members specialize before encoding."""
        return self._membership_source.bind_request(self.domain.bound, goals=())

    @cached_property
    def relation(self):
        return self._base_history[0]

    @cached_property
    def encoding(self):
        return self._base_history[2]

    @cached_property
    def constraints(self):
        import z3

        # Preserve environment legality, never assume DUT progress or safety.
        return (*self.encoding.constraints,
                *(z3.Not(term) for term in self.encoding.environment_assumptions.values()))

    def _retain_query_member(self, rows, model, check, *, encoding=None):
        """Keep one complete SAT proposal for its next native check, not a proof.

        A projected query member is not full-history admission. It may seed
        the native constructor, but cannot skip decode's domain check.
        """
        import z3
        from .symbolic_state import assertion_conjunction

        self._pending_query_member = None
        check()
        projected = encoding is not None
        if projected:
            constraints = (*encoding.constraints,
                *(z3.Not(term) for term in encoding.environment_assumptions.values()))
        else:
            encoding, constraints = self.encoding, self.constraints
        roots = (*constraints, *self.request.fix(rows))
        valid = z3.is_true(model.eval(assertion_conjunction(roots, check=check),
                                      model_completion=False))
        check()
        if not valid:
            return
        key = _membership_key(rows, check)
        if not projected:
            self._remember_member(key)
        self._pending_query_member = (encoding, self.request, key, model)

    def _take_query_member(self, rows, encoding, assertions, check, *, proposal=False, consume=True):
        """Read one hint; a cross-projection proposal proves no native fact.

        The ordinary mode still requires every current root. Proposal mode
        checks request identity and fixed coordinates only; its recipient must
        extend and validate ALL current roots before accepting a member.
        Decode may read without consuming: the subsequent native audit still
        needs the same proposal and independently checks its own full roots.
        """
        import z3
        from .symbolic_state import assertion_conjunction

        pending = self._pending_query_member
        if consume:
            self._pending_query_member = None
        check()
        if (pending is None or (not proposal and pending[0] is not encoding)
                or pending[1] is not self.request
                or pending[2] != _membership_key(rows, check)):
            return None
        model = pending[3]
        fixed = self.request.fix(rows)
        if any(term.ctx != model.ctx for term in (*assertions, *fixed)):
            return None
        roots = (*self.request.constraints, *fixed) if proposal else (*assertions, *fixed)
        valid = z3.is_true(model.eval(assertion_conjunction(roots, check=check),
                                      model_completion=False))
        check()
        return model if valid else None

    def _member_preparation(self, phase):
        """Reuse only the admitted source history, never native/goal proofs."""
        from ..symbolic.exact_solve import SolverPreparation

        if phase not in {'feasibility-source', 'fixed-request-source'} \
                or self._admitted_comparison is None \
                or self.encoding is not self._admitted_comparison.left:
            return None
        if self._source_solver is None:
            omitted = {term.get_id() for term in self.encoding.monitor_constraints}
            base = tuple(term for term in self.encoding.constraints if term.get_id() not in omitted)
            if not base:
                return None
            self._source_solver = SolverPreparation(base)
        return self._source_solver

    def _request_failure(self, rows, *, solve, check):
        """Prove membership in this immutable product, not a hit or native audit.

        Keep only exact request bytes after complete SAT. A pending query model
        may seed this check but remains only a proposal for the native audit;
        other requests, failed proofs and new bound workers need a fresh check.
        Validate before reuse and honor cancellation even on a retained member.
        """
        import z3

        from ..core.query import SolverAnswer
        from .source_member import solve_fixed_source_member

        check()
        fixed = self.request.fix(rows)
        key = _membership_key(rows, check)
        if key in self._request_members:
            check()
            return None
        if self.source.template.direct is not None:
            # Membership is existential for these exact public coordinates.
            # Specialize the ordinary complete-domain constructor BEFORE
            # unrolling, instead of building another unrestricted history.
            # No native values or goal-only projection can admit this request.
            with diagnostic_stage('member.encoding'):
                _, request, encoding = self._membership_source.bind_request(
                    self.domain.bound, goals=(), fixed_rows=rows)
            if request is not self.request:
                raise ValueError('fixed membership must retain the request owner')
            constraints = (*encoding.constraints,
                *(z3.Not(term) for term in encoding.environment_assumptions.values()))
        else:
            encoding, constraints = self.encoding, self.constraints
        roots = (*constraints, *fixed)
        proposal = self._take_query_member(rows, encoding, roots, check,
                                          proposal=True, consume=False)
        answer = solve_fixed_source_member(roots, (encoding,),
            (*self.request.constraints, *fixed), solve, phase='fixed-request', check=check,
            proposal=proposal)
        check()
        if answer.answer is not SolverAnswer.SAT:
            return answer
        self._remember_member(key)
        return None

    def _remember_member(self, key):
        """Retain only complete positive membership, not a solver/model/replay."""
        # Charge actual key storage, including a per-entry overhead allowance.
        # A fixed entry count made sequential audits evict each other even
        # when the complete baseline pool fit in the existing 1-MiB budget.
        size = len(key) + 128
        if len(key) > _MEMBERSHIP_CACHE_KEY_BYTES or size > _MEMBERSHIP_CACHE_BYTES:
            return
        if key in self._request_members:
            return
        used = sum(len(item) + 128 for item in self._request_members)
        while used + size > _MEMBERSHIP_CACHE_BYTES:
            oldest = next(iter(self._request_members))
            used -= len(oldest) + 128
            self._request_members.pop(oldest)
        self._request_members[key] = None

    def _use_admitted_history(self, check):
        """Use the admitted exact history for goals, decode and native audits.

        It was constructed from the same complete public request and compiler
        initial layout, without concrete-state bindings. Retire proofs owned by
        a different encoding; later calls cannot carry them across this change.
        """
        import z3

        check()
        comparison = self._admitted_comparison
        if comparison is None or self.encoding is comparison.left:
            return
        encoding = comparison.left
        constraints = (*encoding.constraints,
                       *(z3.Not(term) for term in encoding.environment_assumptions.values()))
        check()
        self.encoding, self.constraints = encoding, constraints
        self._request_members.clear()
        self._source_solver = None
        self._pending_query_member = None
        self._native.clear()

    def _member_in_query_domain(self, encoding, model, check):
        """A complete source member must also satisfy every query-environment filter."""
        import z3

        check()
        owned = self._native.encoding if self.source.template.direct is not None else self.encoding
        if encoding is not owned:
            return False
        for term in encoding.environment_assumptions.values():
            check()
            if not z3.is_false(model.eval(term, model_completion=False)):
                return False
        check()
        return True

    def _admit_domain(self, solve, check, *, prove=None, proof_scope=None):
        """Admit this worker's complete request domain once, never a seed cone.

        The closed session cannot rebind its artifact, horizon, schedule or
        memory environment. Retain only the fully admitted comparison itself;
        failed/partial proofs and answers about particular goals are not cached.
        """
        from contextlib import nullcontext

        from ..symbolic.exact_solve import ExactCheckResult
        from ..core.audit import AuditCheckStatus
        from ..core.query import SolverAnswer

        check()
        if self.source.template.direct is not None:
            # Direct intervals are used only when structurally certified;
            # otherwise all source orders remain in the same bounded cover.
            # Every query includes unencoded exits. Native membership and hit
            # confirmation, not global order independence, justify positives.
            return 'native-cover-active-nba'
        if self._comparison is None:
            comparison = self.source.compare_request(self.request, check=check,
                observation_scope=self.observation_scope)
            check()
            self._comparison = comparison
        if self._admitted_comparison is self._comparison:
            return 'reused'
        membership = None
        proved = {}
        exact_solve = solve
        def domain_solve(phase, assertions):
            nonlocal membership
            check()
            if phase == 'feasibility':
                if membership is None:
                    # Abstraction can invent members. Nonemptiness must always
                    # be established on the original complete request product.
                    membership = self._domain_feasibility(solve, assertions, check=check)
                return membership
            if phase in proved:
                return proved[phase]
            return exact_solve(phase, assertions)

        audit = None
        if prove is not None:
            abstract = self.relation.abstract_order_comparison(self.request, check=check,
                observation_scope=self.observation_scope)
            base = self._comparison if abstract is None else abstract
            induction = base.inductive(check=check)
            if induction is not None:
                def inductive_proof(phase, assertions, **kwargs):
                    # Preserve the ordinary backend choice despite the receipt
                    # prefix: protocol/product cuts use general SMT, while
                    # exact local transitions keep their finite solver.
                    general = phase in {'confluence:protocol:prefix', 'confluence:product:prefix'}
                    owner = prove if abstract is not None or general else solve
                    return owner('inductive:' + phase, assertions, **kwargs)
                scope = (proof_scope(induction, inductive_proof, check) if proof_scope is not None
                         else nullcontext(inductive_proof))
                with scope as ordered_proof:
                    def induction_solve(phase, assertions):
                        if phase == 'feasibility':
                            return domain_solve(phase, self._comparison.constraints)
                        return ordered_proof(phase, assertions)
                    audit = induction.audit(induction_solve, prefix_proofs=True, check=check)
                check()
                # A stronger premise chain is NOT interchangeable with the
                # original per-phase proof. Retain no partial answers here.
                if audit.status is not AuditCheckStatus.AGREED:
                    audit = None
            if audit is None and abstract is not None:
                def physical_proof(phase, assertions, **kwargs):
                    return prove('abstract:' + phase, assertions, **kwargs)
                scope = (proof_scope(abstract, physical_proof, check) if proof_scope is not None
                         else nullcontext(physical_proof))
                with scope as ordered_proof:
                    def proof_solve(phase, assertions):
                        if phase == 'feasibility':
                            return domain_solve(phase, self._comparison.constraints)
                        answer = ordered_proof(phase, assertions)
                        check()
                        # Only ordered consumption admits a conditional proof.
                        # Speculative completed futures never enter this map.
                        if (type(answer) is ExactCheckResult and answer.answer is SolverAnswer.UNSAT
                                and not phase.startswith('completeness:local:')):
                            proved[phase] = answer
                        return answer
                    audit = abstract.audit(proof_solve, prefix_proofs=True, check=check)
                check()
                # Each UNSAT transfers to the same exact obligation, including
                # its prefix scope. Do not discard it merely because a later
                # abstract obligation is SAT/UNKNOWN. Those answers transfer
                # nothing; the remaining original obligations must still pass.
                # This map lives only in this admission attempt, not the session.
                if audit.status is not AuditCheckStatus.AGREED:
                    audit = None
        if audit is None:
            # Retained abstract proofs can skip arbitrary original groups.
            # Keep that fallback serial, avoiding speculative duplicate work.
            scope = (proof_scope(self._comparison, solve, check)
                     if proof_scope is not None and not proved else nullcontext(solve))
            with scope as exact_solve:
                audit = self._comparison.audit(domain_solve, prefix_proofs=True, check=check)
        if audit.status is not AuditCheckStatus.AGREED:
            refusal = audit.as_dict()['refusal']
            raise Refused(Refusal(refusal['kind'], refusal['why']))
        check()
        self._admitted_comparison = self._comparison
        self._retain_admission_request(membership, check)
        return 'proved'

    def _retain_admission_request(self, answer, check):
        """Keep one fully validated request, never a model or a goal answer."""
        import z3
        from ..core.query import SolverAnswer
        from .symbolic_state import assertion_conjunction

        self._admitted_request = None
        comparison = self._admitted_comparison
        if (comparison is None or answer is None or answer.answer is not SolverAnswer.SAT
                or answer.model is None):
            return
        # Admission proves the unfiltered source domain. Public feasibility
        # also excludes environment violations, so its member must satisfy
        # every original left root AND those query filters, without completion.
        roots = (*comparison.left.constraints,
                 *(z3.Not(term) for term in getattr(comparison.left, 'environment_assumptions', {}).values()))
        check()
        valid = z3.is_true(answer.model.eval(assertion_conjunction(roots, check=check),
                                             model_completion=False))
        check()
        if not valid:
            return
        rows = _rows(self.request.rows(answer.model))
        self.request.fix(rows)
        check()
        self._admitted_request = (comparison, self.request,
                                  tuple(tuple(row.items()) for row in rows))

    def _admission_witness(self, check):
        check()
        cached = getattr(self, '_admitted_request', None)
        if (cached is None or cached[0] is not self._admitted_comparison
                or cached[1] is not self.request or self.encoding is not cached[0].left):
            return None
        rows = [dict(row) for row in cached[2]]
        self._remember_member(_membership_key(rows, check))
        return rows

    def _completion_hint(self, encoding, assertions, check):
        """Try end-of-window bursts, accepting only a complete SAT model.

        This probes the bounded completion monitor, not functional correctness.
        A short receptive tail can leave a legal request pending even for a
        working pipeline. The candidate restricts NO universal query: if it
        fails any original root, the ordinary unrestricted solver still runs.
        Model completion uses existing source/monitor definitions, not replay.
        """
        import z3
        from .source_member import source_member_hint

        request = self.request
        if (self.source.template.direct is None or request.atomic or request.settled
                or encoding.member_plan is None or self.source.queue_bound < 2
                or len(request.slots) < self.source.reset_hold + 4):
            return None
        check()
        codec, count = request.codec, len(request.slots)
        burst = min(self.source.queue_bound, count - codec.reset_hold - 1)
        model = z3.Model(ctx=encoding.alive.ctx)
        variable = {codec.offer, codec.ready, codec.reset}
        for name, atom in request.valuations[0].items():
            check()
            if name not in variable:
                value = False if z3.is_bool(atom) else burst if name == codec.length else 0
                model.update_value(atom.decl(), atom.sort().cast(value))
        clock, = self.source.contract.clocks
        for i, row in enumerate(request.valuations):
            check()
            reset = (i < codec.reset_hold) != clock.reset_active_low
            for name, value in ((codec.offer, count - burst - 1 <= i < count - 1),
                                (codec.ready, i == count-1), (codec.reset, reset)):
                atom = row[name]
                model.update_value(atom.decl(), z3.BoolVal(value, ctx=model.ctx))
        # Zero-valued commands can consume input without producing output.
        # Try both uniform bit polarities before the unrestricted fallback.
        # Only queued payload changes: lengths, initialization and the legal
        # schedule stay fixed. These are existential proposals, not domain
        # restrictions; enum/command legality still needs every original root.
        for high in (False, True):
            check()
            if high:
                for queue in request.queues.values():
                    for row in queue:
                        for atom in row:
                            check()
                            value = True if z3.is_bool(atom) else (1 << atom.size()) - 1
                            model.update_value(atom.decl(), atom.sort().cast(value))
            member = source_member_hint(model, (encoding,), assertions, check=check)
            if member is not None:
                return member
        return None

    def _domain_feasibility(self, solve, assertions, *, check=lambda: None):
        """Try one legal environment, but establish membership on all roots.

        A successful full-formula member proves only nonemptiness. Its fixed
        coordinates never enter later universal proofs. Any unsuccessful hint
        falls back to the original domain under the same owner's deadline.
        """
        from ..core.query import SolverAnswer
        from .monitor_template import solve_monitor_member
        from .source_member import source_member_hint
        from ..symbolic.exact_solve import ExactCheckResult

        # A fixed sampled schedule can consume beyond an empty queue. Keep
        # complete queues for that hint, but prefer an idle member for the
        # ordinary handshake domain: proving existence should not require
        # solving the maximum-length datapath first. Neither choice restricts
        # admission, goal queries or the complete original fallback below.
        preferred = (self.request.full_queue_hint() if self.request.atomic or self.request.settled
                     else tuple(length == 0 for length in self.request.lengths.values()))
        environment = solve('environment-hint', (*self.request.constraints, *preferred))
        if environment.answer is SolverAnswer.SAT:
            fixed = self.request.fix(self.request.rows(environment.model))
            comparison = getattr(self, '_comparison', None)
            histories = tuple(getattr(comparison, side, None) for side in ('left', 'right'))
            member = source_member_hint(environment.model, histories, (*assertions, *fixed), check=check)
            if member is not None:
                return ExactCheckResult(SolverAnswer.SAT, model=member)
            member = solve_monitor_member((*assertions, *fixed),
                histories, solve,
                phase='environment-member', check=check)
            if member.answer is SolverAnswer.SAT:
                return member
        return solve('feasibility', assertions)

    def catalogue_manifest(self):
        return self.source.complete

    def analysis_identity(self):
        return self.source.artifact

    def evidence_identity(self):
        return {'representation': f'rtl-source:{self.source.artifact}:{self.source.contract.digest}',
                'encoding': f'{self.domain.frontend}:{self.domain.digest()}'}

    def call(self, operation, payload, *, started_ns, task=None):
        from ..core.audit_plan import AuditPlan
        from ..core.conditional_evidence import TrustedAssumption
        from ..core.goal_provider import RTL_EXACT_BOUNDED_POLICY
        from ..core.query import SolverAnswer, collect_verdicts

        scope = _SourceOperation(self, payload.get('timeout_ms'), started_ns)
        solve, check = scope.solve, scope.check
        check()
        if operation in {'query', 'query-start'}:
            self._pending_query_member = None
            self.release_witness()
            goals = payload['goals']
            if (type(goals) is not list or not goals
                    or any(type(goal) is not str or not goal for goal in goals)
                    or len(goals) != len(set(goals))):
                raise ValueError('a source query names distinct nonempty exact goal strings')
            unknown = set(goals) - set(self.source.complete)
            if unknown:
                raise Refused(Refusal('rtl-source-query-goal-unknown',
                    'the query names a goal outside its frozen catalogue', first=min(unknown)))
        rows = None
        if operation in {'decode', 'prepare-replay', 'audit-request', 'replay-source'}:
            rows = _bound_rows(payload['rows'], self)
            self.request.fix(rows)  # Validate before an expensive domain audit.
        if operation == 'audit-request' and payload['domain'] != self.domain.digest():
            raise Refused(Refusal('rtl-analysis-request-domain-mismatch',
                'the request audit cannot name another bound domain'))
        traces = None
        if operation == 'step-audit':
            if type(payload['traces']) is not list or not payload['traces']:
                raise Refused(Refusal('rtl-source-step-audit-empty',
                    'step auditing requires at least one complete request'))
            traces = [_bound_rows(trace, self, f'traces[{index}]')
                      for index, trace in enumerate(payload['traces'])]
            for trace in traces:
                self.request.fix(trace)
        if operation == 'record-audit':
            from ..core.analysis import RecordAudit
            from ..core.record_value import thaw_record_value
            from .analysis import _record_domain_audit
            from .source_boundary_capture import source_history_view
            from .startup_state import RTLStartupState

            stimulus = Stimulus.from_dict(payload['stimulus'])
            raw = AuditedTrace.from_dict(payload['raw_trace'])
            if (raw.request or raw.stimulus) != stimulus:
                raise Refused(Refusal('rtl-record-stimulus-mismatch',
                    'the raw run and audit request name different stimuli'))
            result = RunResult.not_compared(stimulus, raw, RunFailure.verifier(
                'bounded-analysis-view', Refusal('analysis-raw-run-not-compared',
                                                 'bounded analysis reads the raw run only')))
            finding = _record_domain_audit(result, task, self.domain,
                queue_bound=self.source.queue_bound, schedule=self.source.schedule,
                memory_environment=self.source.memory_environment)
            if finding is not None:
                return finding.as_dict()
            try:
                raw = source_history_view(raw, self.source.initial_layout.widths)
            except Refused as exc:
                return RecordAudit.refused(
                    exc.refusal.kind, exc.refusal.why, **exc.refusal.detail).as_dict()
            choice = RTLStartupState.from_dict(thaw_record_value(raw.initialization))
            rows = self.request.codec.encode(raw.stimulus, initial_state=choice)
            self.request.fix(rows)

        from .source_proofs import order_proof_scope

        scope.domain_admission = self._admit_domain(solve, check, proof_scope=order_proof_scope,
            prove=lambda phase, assertions, **kwargs:
                solve(phase, assertions, prefer_finite=False, **kwargs))
        self._use_admitted_history(check)

        if operation == 'bind':
            assumptions = (
                TrustedAssumption('rtl-source-lowering-fidelity',
                    'compiler-owned source lowering preserves the admitted event semantics',
                    scope='frontend-semantics'),
                TrustedAssumption('solver', 'the solver answers the complete bounded relation correctly',
                    scope='bounded-solver'),
                TrustedAssumption('rtl-simulator-observation-fidelity',
                    'independent native observations preserve the admitted RTL event semantics',
                    scope='concrete-run-semantics'),
                TrustedAssumption('driver-monitor-semantics',
                    'shared protocol drivers and monitors implement the task contract',
                    scope='task-product-semantics'))
            return {'domain': self.domain.as_dict(), 'domain_digest': self.domain.digest(),
                'analysis_identity': self.analysis_identity(), 'evidence_identity': self.evidence_identity(),
                'analysis_cache': _cache_binding(self).as_dict(),
                'catalogue': self.source.complete.as_dict(), 'audit_plan': AuditPlan.all().as_dict(),
                'trusted_assumptions': [item.as_dict() for item in assumptions],
                'contract': self.domain.contract, 'goal_implementation': RTL_EXACT_BOUNDED_POLICY}
        if operation == 'step-audit':
            # The whole-domain proof above compares the independent source
            # scheduling products. Each supplied request must also belong to
            # that domain; an empty or impossible sample is not agreement.
            for trace in traces:
                answer = self._request_failure(trace, solve=solve, check=check)
                if answer is not None:
                    return AuditCheck.refused('rtl-source-step-request-unresolved'
                        if answer.answer is SolverAnswer.UNKNOWN else 'rtl-source-step-request-outside',
                        answer.reason or 'the request is outside the declared source domain').as_dict()
            return AuditCheck.agreed().as_dict()
        if operation == 'record-audit':
            witness = self._witness(rows, raw, solve=solve, check=check)
            # The baseline/search trace is already a native execution. _witness
            # checks its complete history, request membership and environment
            # against the bound source product; executing it again adds no input.
            from ..core.audit_plan import AuditKind

            return RecordAudit.admitted(witness, audits=AuditPlan(
                (AuditKind.STEP, AuditKind.SOURCE))).as_dict()
        if operation in {'audit-request', 'replay-source'}:
            from ..core.instance import endpoint_batch_output

            image = payload['trace']
            trace = (RunFailure.from_dict(image) if 'origin' in image
                     else AuditedTrace.from_dict(image))
            witness = self._witness(rows, trace, solve=solve, check=check)
            if operation == 'audit-request':
                return witness.as_record()
            return _replay_image(Replay(witness.hits,
                [endpoint_batch_output(batch) for batch in witness.outputs],
                state=witness.final_state, steps=len(rows), error=witness.terminal_error))
        if operation in {'decode', 'prepare-replay'}:
            # Preparing physical inputs grants no member, hit or audit. The
            # replay consumer checks the complete original source product in
            # audit-request/replay-source. Standalone decode retains its
            # stronger, independently proved membership contract.
            if operation == 'decode':
                answer = self._request_failure(rows, solve=solve, check=check)
                if answer is not None:
                    raise Refused(Refusal('rtl-source-request-outside-domain' if answer.answer is SolverAnswer.UNSAT
                                         else 'rtl-source-request-unresolved',
                        'the request has no established complete source execution', reason=answer.reason))
            return {'stimulus': self.request.codec.decode(rows).as_dict(),
                    'initialization': self.source.initial_layout.decode(rows).as_dict()}
        if operation == 'feasible':
            from .monitor_template import solve_monitor_member
            from .source_member import solve_fixed_source_member

            witness = self._admission_witness(check)
            reused = witness is not None
            answer = None
            if not reused:
                if self.source.template.direct is not None:
                    # The constructor already has a forward definition plan.
                    # Try one legal environment and validate EVERY original
                    # root, as for a fixed native request. Failure retains the
                    # unrestricted solver; this is existence, not a domain
                    # restriction or a proof of any goal.
                    answer = solve_fixed_source_member(self.constraints, (self.encoding,),
                        self.request.constraints, solve, phase='feasibility', check=check)
                else:
                    answer = solve_monitor_member(self.constraints, (self.encoding,),
                        solve, phase='feasibility', check=check)
                if answer.answer is SolverAnswer.SAT:
                    witness = self.request.rows(answer.model)
            outcome = SolverAnswer.SAT if reused else answer.answer
            feasible = {SolverAnswer.SAT: Feasible.YES, SolverAnswer.UNSAT: Feasible.NO,
                        SolverAnswer.UNKNOWN: Feasible.UNRESOLVED}[outcome]
            try:
                measured = {'total_ns': time.monotonic_ns() - started_ns}
                telemetry = QueryTelemetry(
                    backend='rtl-z3', kind=QueryKind.FEASIBILITY,
                    semantic_instance=semantic_instance_of(self), horizon=self.domain.bound,
                    outcome=outcome.value,
                    timings=StageTimings(measured) if reused else _solve_timings(answer, **measured),
                    peak_rss_bytes=process_peak_rss_bytes(), provenance={**scope.provenance(scope.timeout),
                        'feasibility_member': 'admission-request' if reused else 'solver'})
            except Exception:  # noqa: BLE001 - telemetry cannot change the answer
                telemetry = None
            return {'answer': feasible.value, 'detail': '' if reused else answer.reason,
                    'witness': witness,
                    'telemetry': None if telemetry is None else telemetry.as_dict()}

        parts = self._query_steps(goals, scope, native_proposals=operation == 'query-start')
        if operation == 'query-start':
            return parts
        try:
            return collect_verdicts(parts).as_record()
        except BaseException:
            try:
                parts.close()
            finally:
                self.close_query()
            raise

    def close_query(self):
        """Release query syntax/proposals, preserving independently admitted evidence."""
        self._pending_query_member = None
        self._queries.close()
        self.release_witness()

    def _query_steps(self, goals, scope, *, native_proposals=False):
        """Share construction among goals with the same observation semantics.

        Settled protocol/value goals do not observe transient source events.
        Reset-value comparisons additionally observe every owned register.
        Keep those roots out of control/scenario and source-event batches;
        every batch uses the same public request, deadline and complete escape
        obligations, and shares its history across the goals it contains.
        Goals with no additional observation demand share the control batch:
        their actual predicates and escape checks still go through construction.
        Protocol lemmas have their own final batch: an optional arbitrary-state
        induction must not precede unrelated finite-history target queries.
        """
        batches, protocol_batch, unproposable = goal_batches(self.source, self.request, goals)
        # Structural domain facts are identical across observation batches.
        # Collect them once without launching arbitrary-entry SMT searches;
        # unproved faults remain in the complete bounded query.
        proofs = LocalGoalProofs(self.source, self.request)
        deferred = []
        for batch in batches:
            pending = yield from self._query_batch_steps(batch, scope,
                proofs=proofs, defer_proved=len(batches) > 1, native_proposals=native_proposals,
                unproposable=unproposable)
            if pending is not None:
                deferred.append(pending)
        # Only facts and measured costs wait here, not suspended generators.
        # Closing the outer cursor closes its active child via yield-from;
        # deferred work has no execution lifetime and publishes no verdict.
        for reduction, cost in deferred:
            yield from self._query_prepared_steps(reduction, cost, scope, proofs=proofs,
                                                 native_proposals=native_proposals,
                                                 unproposable=unproposable)
        if protocol_batch:
            # Finish ordinary batches' complete escape checks before entering
            # a potentially hard protocol lemma under the same deadline.
            yield from self._query_batch_steps(protocol_batch, scope, proofs=proofs,
                                              native_proposals=native_proposals,
                                              unproposable=unproposable)

    def _query_batch_steps(self, goals, scope, *, proofs=None, defer_proved=False,
                           native_proposals=False, unproposable=frozenset()):
        """Query live goals first, then close any remaining local exclusions."""
        scope.check()  # A previous batch may have consumed the operation allowance.
        # Local facts have one request owner, separate from batch ordering.
        proofs = LocalGoalProofs(self.source, self.request) if proofs is None else proofs
        proofs.require_binding(self.source, self.request)
        cost = LocalProofCost()
        reduction = cost.run(proofs.prepare, goals, solve=scope.solve, check=scope.check)
        return (yield from self._query_prepared_steps(reduction, cost, scope,
            proofs=proofs, defer_proved=defer_proved, native_proposals=native_proposals,
            unproposable=unproposable))

    def _query_prepared_steps(self, reduction, cost, scope, *, proofs, defer_proved=False,
                              native_proposals=False, unproposable=frozenset()):
        """Return deferred facts, or finish their full bounded escape check."""
        scope.check()
        proofs.require_binding(self.source, self.request)
        while True:
            if not reduction.residual:
                if defer_proved:
                    return reduction, cost
                reduction = cost.run(proofs.close_escape, reduction, solve=scope.solve, check=scope.check)
            remaining = yield from self._query_reduction_steps(reduction, scope,
                proof_ns=cost.elapsed_ns, proof_answers=cost.calls, native_proposals=native_proposals,
                unproposable=unproposable)
            if not remaining:
                return
            # The previous query frame has finished before constructing a new
            # view. Reuse its request-bound local facts, not its live-goal cone.
            reduction = replace(reduction, goals=remaining)
            cost = LocalProofCost()

    def _query_reduction_steps(self, reduction, scope, *, proof_ns, proof_answers,
                              native_proposals=False, unproposable=frozenset()):
        """Share one history/solver until this reduction's live goals finish."""
        import z3

        from ..symbolic.exact_solve import ExactCheckResult
        from ..core.query import QueryCall, SolverAnswer, iter_decide_setwise
        from .analysis_query import source_domain_refusal

        solve, check = scope.solve, scope.check
        check()
        try:
            telemetry_scope = QueryTelemetryScope(backend='rtl-z3',
                semantic_instance=semantic_instance_of(self), horizon=self.domain.bound)
        except Exception:  # noqa: BLE001 - measurement cannot own query semantics
            telemetry_scope = None
        goals = reduction.goals
        proved, residual = reduction.proved, reduction.residual
        proved_domains, query_source = reduction.domains, reduction.source
        if self.source.template.direct is not None and '_base_history' not in self.__dict__:
            # Decode can reuse the equivalent request view; native observation
            # still uses the original source and its complete observations.
            self._membership_source = query_source
        construction_ns = None
        query_escape = None
        model = prefixes = growing = None
        if self.source.template.direct is not None:
            # One dependency closure/history and one solver base for this
            # batch. Setwise SAT/UNSAT and singleton fallbacks only replace the
            # target disjunction; they do not rebuild the 100-input history.
            construction_started = time.monotonic_ns()
            growing = self._queries.start(query_source, self.domain.bound, residual, proved_domains,
                                          check=check)
            if growing is None:
                model = self._queries.prepare(query_source, self.domain.bound, residual, proved_domains,
                                              check=check)
                query_hits, query_escape = model.hits, model.escape
                prefixes = GoalQueryPrefixes(model, goals, proved,
                    source_goals=self.source.goal_names, check=check)
            check()
            construction_ns = time.monotonic_ns() - construction_started

        if native_proposals and growing is not None:
            from ..core.instance import Verdict

            # The streaming owner can run and audit a complete native history
            # between requests. A symbolic full-tail SAT proof before that
            # same audit duplicates work and supplies no additional hit.
            # Standalone queries retain their full symbolic-member path.
            # No history hits a literal-false goal, so do not search for one;
            # the batch query below closes it with its escapes in one step.
            try:
                while pending := tuple(goal for goal in goals
                                       if goal not in proved and goal not in unproposable):
                    started = time.monotonic_ns()
                    before = growing.construction_ns
                    candidate, calls = growing.propose_request(pending, solve=solve, check=check)
                    encoded_ns = growing.construction_ns - before
                    construction_ns = (construction_ns or 0) + encoded_ns
                    proof_ns += time.monotonic_ns() - started - encoded_ns
                    proof_answers.extend({key: getattr(answer, key)
                        for key in ('solver_load_ns', 'solve_ns')} for answer in calls)
                    if candidate is None:
                        break
                    rows = self.request.rows(candidate.model)
                    witness = yield NativeQueryProposal(rows)
                    check()
                    if witness is None:
                        growing.retire_proposals()
                        break  # A discarded proposal grants no hit or exclusion.
                    if (type(witness) is not AuditedWitness
                            or witness.domain != self.domain.digest()
                            or not witness.matches_inputs(rows)):
                        raise ValueError('a query proposal requires its complete native audit')
                    hits = tuple(goal for goal in pending if goal in witness.hits)
                    if not hits:
                        growing.retire_proposals()
                        break  # Misses leave every original negative obligation.
                    measured = {'total_ns': proof_ns + (construction_ns or 0),
                                'encode_unroll_ns': construction_ns or 0}
                    for key in ('solver_load_ns', 'solve_ns'):
                        if all(answer[key] is not None for answer in proof_answers):
                            measured[key] = sum(answer[key] for answer in proof_answers)
                    telemetry = None
                    if telemetry_scope is not None:
                        try:
                            telemetry = telemetry_scope.fragment(pending,
                                timings=StageTimings(measured)).finalize(outcome='sat', goals_settled=hits)
                        except Exception:  # noqa: BLE001 - timing cannot change an audited answer
                            pass
                    call = QueryCall(pending, SolverAnswer.SAT, witness=rows,
                                     solver_calls=len(proof_answers), telemetry=telemetry)
                    verdict = Verdict({goal: rows for goal in hits}, (), {}, queries=(call,))
                    proof_answers, proof_ns, construction_ns = [], 0, None
                    goals = tuple(goal for goal in goals if goal not in hits)
                    yield annotate_verdict(verdict, self, scope.timeout, backend='rtl-z3',
                                           provenance=scope.provenance)
            except BaseException:
                self._queries.close_construction()
                raise

        completion_hint_attempted = False
        escape_excluded = False
        def pose(batch):
            nonlocal construction_ns, completion_hint_attempted, proof_answers, proof_ns
            nonlocal escape_excluded, model
            query_started = time.monotonic_ns()
            answer, solver_calls = None, 0
            prefix_answers, inline_construction_ns = [], 0
            if self.source.template.direct is not None:
                prepared = model
                if prepared is None:
                    before = growing.construction_ns
                    prepared, member, prefix_answers = growing.propose(
                        batch, solve=solve, check=check)
                    inline_construction_ns = growing.construction_ns - before
                    if prepared is None:
                        started = time.monotonic_ns()
                        model = prepared = self._queries.prepare(query_source, self.domain.bound,
                            residual, proved_domains, check=check)
                        inline_construction_ns += time.monotonic_ns() - started
                    else:
                        answer = ExactCheckResult(SolverAnswer.SAT, model=member,
                                                  solver_load_ns=0, solve_ns=0)
                assert prepared.request is self.request
                query_encoding, query_constraints = prepared.encoding, prepared.constraints
                query_hits, query_escape = prepared.hits, prepared.escape
                query_observations, query_faults = prepared.observations, prepared.domain_faults
                preparation = prepared.preparation
            else:
                query_encoding, query_constraints = self.encoding, self.constraints
                query_hits = query_observations = query_encoding.hits
                query_escape = preparation = None
            target = _goal_union((query_hits[goal] for goal in batch if goal not in proved),
                                 context=query_encoding.alive.ctx)
            if query_escape is not None:
                # UNSAT is valid only when neither the goal nor an unencoded
                # continuation is possible. Do not constrain such exits away.
                target = _goal_union((target, query_escape), context=query_encoding.alive.ctx)
            # A literal-false target (including every unsupported exit) is
            # already UNSAT. A hard sibling may exhaust the solver allowance;
            # that must not turn this exact constant fact into UNKNOWN too.
            constant_false = z3.is_false(target)
            exhausted = scope.budget.remaining_ms() == 0
            if answer is None and (constant_false or escape_excluded and all(goal in proved for goal in batch)):
                # A prior full-query UNSAT excluded this same escape under
                # these same roots. Local exclusions need no second proof.
                answer = ExactCheckResult(SolverAnswer.UNSAT, solver_load_ns=0, solve_ns=0)
            elif answer is None and not exhausted and not completion_hint_attempted and INPUT_COMPLETION in batch:
                completion_hint_attempted = True
                member = self._completion_hint(query_encoding,
                    (*query_constraints, target, z3.Not(query_escape)) if query_escape is not None
                    else (*query_constraints, target), check)
                if member is not None:
                    answer = ExactCheckResult(SolverAnswer.SAT, model=member,
                                              solver_load_ns=0, solve_ns=0)
            if answer is None and not exhausted and prefixes is not None:
                member, prefix_answers = prefixes.propose(batch, target, solve=solve, check=check)
                if member is not None:
                    answer = ExactCheckResult(SolverAnswer.SAT, model=member,
                                              solver_load_ns=0, solve_ns=0)
            if answer is None:
                # This base has never contained proposal-only restrictions.
                solver_calls = int(scope.budget.remaining_ms() != 0)
                answer = solve('query', (*query_constraints, target), preparation=preparation)
            solver_calls += len(proof_answers) + len(prefix_answers)
            if (answer.answer is SolverAnswer.SAT and query_escape is not None
                    and not z3.is_false(answer.model.eval(query_escape, model_completion=True))):
                refusal = source_domain_refusal(answer.model, query_faults,
                    query_encoding.startup, self.source.machine.domain_checks)
                if refusal is not None:
                    raise Refused(refusal)
                answer = ExactCheckResult(SolverAnswer.UNKNOWN,
                    reason='rtl-direct-unencoded-continuation',
                    solve_ns=answer.solve_ns, solver_load_ns=answer.solver_load_ns)
            hits = ()
            witness = None
            measured = {}
            if query_escape is not None and answer.answer is SolverAnswer.UNSAT:
                # Prefix-only UNSAT never reaches here as the final answer.
                escape_excluded = True
            if answer.answer is SolverAnswer.SAT:
                decode_started = time.monotonic_ns()
                witness = self.request.rows(answer.model)
                hits = tuple(goal for goal in batch if goal in query_observations and z3.is_true(
                    answer.model.eval(query_observations[goal], model_completion=True)))
                self._retain_query_member(witness, answer.model, check,
                    encoding=query_encoding if self.source.template.direct is not None else None)
                measured['decode_ns'] = time.monotonic_ns() - decode_started
            measured['total_ns'] = time.monotonic_ns() - query_started + proof_ns
            for key in ('solver_load_ns', 'solve_ns'):
                values = [item[key] for item in proof_answers]
                values.extend(getattr(item, key) for item in (*prefix_answers, answer))
                if all(value is not None for value in values):
                    measured[key] = sum(values)
            proof_answers, proof_ns = [], 0
            if construction_ns is not None:
                # Charge the shared construction once, not once per fallback.
                measured['encode_unroll_ns'] = construction_ns
                measured['total_ns'] += construction_ns
                construction_ns = None
            if inline_construction_ns:
                # Construction during pose is already included in total_ns.
                measured['encode_unroll_ns'] = measured.get('encode_unroll_ns', 0) + inline_construction_ns
            telemetry = None
            if telemetry_scope is not None:
                try:
                    settled = (batch if answer.answer is SolverAnswer.UNSAT
                               or answer.answer is SolverAnswer.UNKNOWN and len(batch) == 1 else hits)
                    telemetry = telemetry_scope.fragment(batch,
                        timings=StageTimings(measured)).finalize(
                            outcome=answer.answer.value, goals_settled=settled)
                except Exception:  # noqa: BLE001 - timing failure cannot replace a solver answer
                    telemetry = None
            return QueryCall(batch, answer.answer, witness=witness, reason=answer.reason,
                             solver_calls=solver_calls, telemetry=telemetry), hits

        # Publish exact constant facts before an expensive sibling or its
        # native witness audit can consume the remaining deadline. Reuse this
        # batch's encoding; no extra history is constructed. An unencoded exit
        # forbids this shortcut even when the goal itself is literally false.
        closed = query_escape is not None and z3.is_false(query_escape)
        # A local lemma with a live escape is not ready. Finish live targets
        # first, then return the exclusions to the existing escape-only path.
        # Every live query still includes the SAME complete escape obligation.
        ready = set(proved) if closed else set()
        deferred = (tuple(goal for goal in goals if goal in proved)
                    if not closed and residual else ())
        if closed:
            for goal in goals:
                check()
                if goal in query_hits and z3.is_false(query_hits[goal]):
                    ready.add(goal)
        # Local lemmas still carry their full-history escape obligation in
        # pose(): a timeout or escape model cannot certify bounded UNSAT.
        try:
            if ready:
                for verdict in iter_decide_setwise(tuple(goal for goal in goals if goal in ready), pose):
                    yield annotate_verdict(verdict, self, scope.timeout, backend='rtl-z3',
                                           provenance=scope.provenance)
                goals = [goal for goal in goals if goal not in ready]
            if deferred:
                goals = [goal for goal in goals if goal not in proved]
            if goals:
                for verdict in iter_decide_setwise(goals, pose):
                    yield annotate_verdict(verdict, self, scope.timeout, backend='rtl-z3',
                                           provenance=scope.provenance)
            if deferred and escape_excluded:
                check()
                for verdict in iter_decide_setwise(deferred, pose):
                    yield annotate_verdict(verdict, self, scope.timeout, backend='rtl-z3',
                                           provenance=scope.provenance)
                deferred = ()
        finally:
            try:
                if prefixes is not None:
                    prefixes.close()
            finally:
                self._queries.close_construction()
        return deferred

    def _witness(self, rows, trace, *, solve, check):
        """Validate native evidence independently of the goal-query model."""
        return self._native.audit_witness(self.request, self.domain, self.observation_scope,
            _membership_key(rows, check), trace,
            build=lambda: self._build_witness(rows, trace, solve=solve, check=check), check=check)

    def release_witness(self):
        """Retire query-local evidence through its owner, not transport fields."""
        self._native.release_witness()

    def _build_witness(self, rows, trace, *, solve, check):
        from ..core.analysis import audit_environment_assumptions
        from ..core.audit import AuditCheckStatus
        from ..core.query import SolverAnswer
        from ..core.record_value import canonical_json
        from ..symbolic.exact_solve import ExactCheckResult
        from .observation_audit import compare_native_history
        from .source_member import solve_fixed_source_member

        check()
        direct = self.source.template.direct is not None
        terminal = type(trace) is RunFailure
        # A prefix proposal may violate an input precondition later in the
        # history. That is an inadmissible stimulus, not a source/native
        # contradiction. Check the environment before constructing its proof.
        environment = (None if terminal else
            audit_environment_assumptions(self.source.contract, trace.stimulus, trace))
        if environment is not None:
            raise Refused(Refusal('rtl-source-witness-environment-invalid',
                'the native witness is outside the declared task environment', audit=environment.as_dict()))
        record_identity = canonical_json(trace.as_dict()) if terminal else trace.fingerprint
        key = _membership_key(rows, check)
        attempt = self.request, key, record_identity, self.observation_scope
        if self._native.member_miss != attempt:
            self._native.member_miss = None
            with diagnostic_stage('native.proposal'):
                proposed = self._native.propose_member(
                    self.source, self.request, self.domain, self.observation_scope, rows, trace,
                    key=key, remember=self._remember_member, solve=solve, check=check)
            if proposed is not None:
                return proposed
            check()
            # This only avoids repeating an unsuccessful optional construction.
            # It grants no fact: the full native proof below still runs. A hash
            # here can at worst miss an acceleration, never certify a witness.
            self._native.member_miss = attempt
        # Terminal encodings use only the fixed request, not observed samples.
        # Fresh failure receipts must be compared again, but do not require
        # reconstructing identical source equations.
        native_key = (key, None if terminal else record_identity) if direct else None
        if self._native.encoding is None or (direct and self._native.trace != native_key):
            # Reuse syntax from the fully admitted exact product. Native
            # execution, projection and fixed-request membership remain fresh.
            if direct:
                # A new native encoding has a different proof owner. These
                # old comparisons cannot be reused (identity is checked), so
                # release them BEFORE constructing the next complete history.
                self._native.release_projection()
                with diagnostic_stage('native.encoding'):
                    relation, request, encoded = self.source.bind_request(
                        self.domain.bound, native_history=(rows, trace))
                assert request is self.request
            else:
                relation = self.relation
                encoded = (self._admitted_comparison.left if self._admitted_comparison is not None
                           else self.relation.bind_native_request(self.request, check=check))
            check()  # A cancelled construction must not become reusable.
            self._native.encoding = encoded
            self._native.relation = relation
            self._native.trace = native_key
        comparison = compare_native_history(self._native.relation, self.request, self._native.encoding,
            rows, trace, self.source.frontend, observation=self.source.observation, check=check,
            observation_scope=self.observation_scope)
        query_member = self._take_query_member(rows, self._native.encoding,
                                               comparison.constraints, check, proposal=direct)
        # The complete witness is newly constructed from this actual replay.
        # Only its equality to an already proved obligation permits proof reuse.
        witness = AuditedWitness(domain=self.domain.digest(), inputs=rows, **comparison.native_image)
        if self._native.agreement is None:
            self._native.proofs.clear()
        identity = None
        same = comparison.same_obligation(self._native.agreement, check=check)
        if not same:
            identity = self._native.proofs.identity(comparison, witness, check=check)
            same = self._native.proofs.contains(key, identity, check=check)
        member = False
        def native_solve(phase, assertions):
            nonlocal member
            if phase == 'feasibility':
                # Fixed input/startup coordinates may construct a complete
                # member cheaply. No recorded state/output becomes a premise;
                # source/native correspondence remains the subsequent audit.
                if query_member is not None:
                    answer = ExactCheckResult(SolverAnswer.SAT, model=query_member)
                else:
                    environment = (*self.request.constraints, *self.request.fix(rows))
                    answer = solve_fixed_source_member(assertions, (self._native.encoding,),
                        environment, solve, phase=phase, check=check)
                check()
                if answer.answer is SolverAnswer.SAT:
                    # The complete native roots already include C and the
                    # exact request. Decode additionally excludes environment
                    # faults; validate those too without model completion.
                    member = self._member_in_query_domain(self._native.encoding, answer.model, check)
                return answer
            return solve(phase, assertions)

        if not same:
            admitted = self._admitted_comparison
            facts = None if admitted is None else lambda model: admitted.native_literals(
                comparison.encoding, model, self.request.fix(rows), check=check)
            if self.source.template.direct is not None:
                def covered_member(phase, assertions):
                    # A fixed native history needs one member, not a new
                    # unconstrained execution search. The existing forward
                    # constructor validates ALL roots; a failed hint retains
                    # the original full solver path and the same deadline.
                    environment = (*self.request.constraints, *self.request.fix(rows))
                    return solve_fixed_source_member(assertions, (self._native.encoding,),
                        environment, solve, phase=phase, check=check, proposal=query_member,
                        prefixes=comparison.member_prefixes(environment, check=check))
                audited = comparison.admit_member(covered_member, check=check)
                member = audited.status is AuditCheckStatus.AGREED
            else:
                audited = comparison.audit(native_solve, check=check, proved_literals=facts)
            if audited.status is AuditCheckStatus.INCONSISTENT:
                raise WitnessInconsistent('rtl-source-native-witness-inconsistent',
                    'native replay contradicts the complete source history: '
                    + '; '.join(audited.findings[:8]), findings=list(audited.findings))
            if audited.status is AuditCheckStatus.REFUSED:
                raise Refused(audited.refusal)
        check()
        # Changed coordinates, roots, names or ANY earlier observation need a
        # new proof. Fresh executions and environment checks remain mandatory.
        self._native.agreement = comparison
        if identity is not None:
            self._native.proofs.remember(key, identity, check=check)
        if member:
            self._remember_member(_membership_key(rows, check))
        return witness


def _timeout(value):
    if value is not None and (type(value) is not int or value < 1):
        raise TypeError("an exact-analysis timeout is absent or a positive integer")
    return value


def _bound_rows(value, exact, name="rows"):
    rows = _rows(value, name)
    if len(rows) != exact.domain.bound:
        raise Refused(Refusal(
            "rtl-analysis-request-horizon-mismatch",
            "the request must contain exactly the bound domain's slots",
            expected=exact.domain.bound, got=len(rows)))
    return rows


def run_analysis_job(payload):
    """Execute one closed operation; all relation and solver objects stay here."""

    if type(payload) is not dict or type(payload.get("operation")) is not str:
        raise TypeError("an exact-analysis job names one exact operation")
    operation = payload["operation"]
    if operation not in OPERATIONS:
        raise ValueError("an exact-analysis job names a registered operation")
    if operation in {'query-start', 'query-next', 'query-discard', 'query-close'}:
        raise ValueError('incremental queries require a bound analysis session')
    extras = {"operation", "horizon"} | _OPERATION_FIELDS[operation]
    started_ns = time.monotonic_ns()
    try:
        exact, task = _bound(payload, extras)
    except (WitnessInconsistent, Refused) as exc:
        return _operation_failure(operation, exc, None, payload, started_ns)
    return _run_bound_operation(operation, payload, exact, task, started_ns=started_ns)


def _operation_failure(operation, error, exact, payload, started_ns):
    """One wire representation for expected failures; callers own cleanup."""
    if isinstance(error, WitnessInconsistent):
        return {"version": VERSION, "operation": operation,
                "status": "inconsistent", "inconsistency": error.as_dict()}
    if isinstance(error, AuditedWitnessLimitError):
        return _refused(operation, Refusal('analysis-audited-witness-evidence-too-large',
            str(error), node_limit=error.limit))
    return _refused(operation, annotate_refusal(error.refusal,
        operation, exact, payload, started_ns, backend='rtl-z3', provenance=_tool_provenance))


class PreparedAnalysis:
    """One checked context/horizon; all operations still perform their own checks."""

    def __init__(self, context, *, direct_source=True):
        # Public calls share the native-cover constructor. SAT proposals still
        # need native confirmation; all domain escapes remain in each query.
        # The all-orders proof backend is an explicit diagnostic control only.
        self.exact, self.task = _bound(context, {"horizon"}, direct_source=direct_source)
        self._query_cursor = None
        self._query_proposal = None
        self._query_witness = None

    def binding(self):
        return _run_bound_operation("bind", {}, self.exact, self.task)

    def call(self, operation, payload):
        if type(operation) is not str or operation not in OPERATIONS or operation == "bind":
            raise ValueError("a prepared analysis cannot bind again or change operation schema")
        if type(payload) is not dict or set(payload) != _OPERATION_FIELDS[operation]:
            raise TypeError("a prepared analysis operation requires exactly its declared fields")
        if operation in {'query-start', 'query-next', 'query-discard', 'query-close'}:
            return self._query_operation(operation, payload)
        if operation == 'query' and self._query_cursor is not None:
            raise ValueError('an active query cursor must finish before another query starts')
        reply = _run_bound_operation(operation, payload, self.exact, self.task)
        proposal = getattr(self, '_query_proposal', None)
        if operation == 'audit-request' and proposal is not None and reply['status'] == 'ok':
            value = decode_analysis_reply(reply, operation)
            if not isinstance(value, Refusal):
                witness = AuditedWitness.from_record(value)
                if (witness.domain == self.exact.domain.digest()
                        and witness.matches_inputs(proposal.rows)):
                    self._query_witness = witness
        return reply

    def _close_query(self):
        cursor, self._query_cursor = self._query_cursor, None
        self._query_proposal = self._query_witness = None
        try:
            if cursor is not None:
                close = getattr(cursor, 'close', None)
                if close is not None:
                    close()
        finally:
            if type(self.exact) is _SourceAnalysis:
                self.exact.close_query()

    def _query_operation(self, operation, payload):
        """A single ordered cursor; ordinary audits may run between its calls."""
        from ..core.instance import Verdict

        started_ns = time.monotonic_ns()
        try:
            if operation == 'query-close':
                self._close_query()
                return _reply(operation, None)
            if operation == 'query-start':
                if self._query_cursor is not None:
                    raise ValueError('an analysis session already has an active query cursor')
                if type(self.exact) is _SourceAnalysis:
                    self._query_cursor = self.exact.call(operation, payload,
                        started_ns=started_ns, task=self.task)
                else:
                    # Legacy backends retain their exact aggregate API.
                    result = decode_analysis_reply(_run_bound_operation(
                        'query', payload, self.exact, self.task, started_ns=started_ns), 'query')
                    if isinstance(result, Refusal):
                        raise Refused(result)
                    self._query_cursor = iter((Verdict.from_record(result),))
            elif self._query_cursor is None:
                raise ValueError('an analysis query must start before it can resume')
            if type(self.exact) is _SourceAnalysis:
                # The previous result's native checks have completed. Do not
                # carry their full construction into the next solver call.
                self.exact.release_witness()
            proposal = getattr(self, '_query_proposal', None)
            if operation == 'query-discard' and proposal is None:
                raise ValueError('only a pending native proposal can be discarded')
            if proposal is not None:
                witness = None if operation == 'query-discard' else self._query_witness
                if witness is None and operation != 'query-discard':
                    raise ValueError('a query proposal must be audited before resuming')
                self._query_proposal = self._query_witness = None
                try:
                    part = self._query_cursor.send(witness)
                except StopIteration:
                    part = None
            else:
                part = next(self._query_cursor, None)
            if type(part) is NativeQueryProposal:
                self._query_proposal = part
            if part is None:
                self._close_query()
            return _reply(operation, None if part is None else part.as_record())
        except (WitnessInconsistent, AuditedWitnessLimitError, Refused) as exc:
            self._close_query()
            return _operation_failure(operation, exc, self.exact, payload, started_ns)
        except BaseException:
            self._close_query()
            raise


def _run_bound_operation(operation, payload, exact, task, *, started_ns=None):
    started_ns = time.monotonic_ns() if started_ns is None else started_ns
    if type(exact) is not _SourceAnalysis:
        raise TypeError("RTL exact analysis operations run on the bound source image")
    try:
        return _reply(operation, exact.call(operation, payload, started_ns=started_ns, task=task))
    except (WitnessInconsistent, AuditedWitnessLimitError, Refused) as exc:
        return _operation_failure(operation, exc, exact, payload, started_ns)
