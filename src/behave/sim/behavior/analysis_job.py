"""Strict worker protocol for exact Behavior construction and bounded analysis.

The parent freezes source bytes and obtains the executable construction image through
``behavior.construction``'s isolated model service.  This job consumes only those JSON
records.  It parses and lowers the source, prepares the position-indexed exact provider,
and performs every Z3 operation in a fresh named judge job.  Submitted source code is
never executed in this process: source audits use the nested isolated-model interpreter,
which remains a separate descendant inside the same outer namespace boundary.
"""

from __future__ import annotations

import time
from contextlib import nullcontext

from ..analysis_budget import AnalysisStopped, analysis_scope
from ..core.admission import (
    _admitted_task_from_record,
)
from ..core.audit import AuditCheck, AuditCheckStatus
from ..core.catalogue import CatalogueManifest
from ..core.contract import TaskContract
from ..core.failure import RunFailure
from ..core.instance import Feasible
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
from ..core.record_value import freeze_record_value, thaw_record_value
from ..core.refusal import Refusal, Refused
from ..core.run_record import RunResult
from ..core.stimulus import AuditedTrace, Stimulus
from ..goal_policy import evaluation_goal_policy
from . import budgets as budgets_mod
from .construction import (
    ConstructionFacts,
    ConstructionUnavailable,
    _dependency_identity,
    _seed_construction_facts,
)

__all__ = ["decode_analysis_reply", "run_analysis_job"]


VERSION = "behavior-exact-analysis/109"
OPERATIONS = frozenset({
    "frontend", "atomic-frontend", "bind", "record-audit", "replay", "audit-request",
    "step-audit", "source-audit", "feasible", "query", "analyse",
})
_BINDING_FIELDS = frozenset({
    "artifact", "task_contract", "horizon", "domain",
    "analysis_identity", "catalogue", "admission",
})
_ENCODING_FAILURE_KINDS = frozenset({
    "range", "shape", "type", "width", "capability", "resource", "goal",
})


def _construction_failure(exc):
    """Preserve a frontend's typed reason at the exact-service boundary."""

    code = str(getattr(exc, "code", "") or "")
    if code:
        return Refusal(
            code,
            "the isolated Behavior exact service could not construct this operation",
            error=f"{type(exc).__name__}: {exc}")

    # Import lazily so decoding a stored reply does not load the solver frontend.
    from ..symbolic.validation import EncodingError

    if type(exc) is EncodingError:
        reason = exc.kind if exc.kind in _ENCODING_FAILURE_KINDS else "operation"
        return Refusal(
            f"behavior-exact-encoding-{reason}-unavailable",
            "the Behavior frontend cannot encode this exact operation",
            encoding_kind=reason,
            error=f"EncodingError: {exc}")
    return Refusal(
        "behavior-exact-construction-failed",
        "the isolated Behavior exact service could not construct this operation",
        error=f"{type(exc).__name__}: {exc}")


def _binding_image(value, operation, *, reply=False):
    if type(value) is not dict or set(value) != _BINDING_FIELDS:
        raise TypeError("a Behavior exact operation has one closed identity binding")
    out = dict(value)
    for name in (
            "artifact", "task_contract", "analysis_identity", "admission"):
        if type(out[name]) is not str or not out[name]:
            raise TypeError(
                f"a Behavior exact operation binding has a nonempty {name}")
    if type(out["horizon"]) is not int or out["horizon"] < 1:
        raise TypeError("a Behavior exact operation binding has a positive horizon")
    for name in ("domain", "catalogue"):
        if out[name] is not None \
                and (type(out[name]) is not str or not out[name]):
            raise TypeError(
                f"a Behavior exact operation binding has an optional {name}")
    if operation not in {"frontend", "atomic-frontend", "bind", "analyse"} and any(
            out[name] is None for name in ("domain", "catalogue")):
        raise ValueError(
            "a post-bind Behavior exact operation names its domain and catalogue")
    if reply and operation in {"bind", "analyse"} and any(
            out[name] is None for name in ("domain", "catalogue")):
        raise ValueError(
            "a successful Behavior exact bind reply names its domain and catalogue")
    if reply and operation in {"frontend", "atomic-frontend"} \
            and (out["domain"] is not None or out["catalogue"] is None):
        raise ValueError(
            "a frontend reply names its catalogue but no trace domain")
    return out


def _binding_matches(expected, actual):
    return all(wanted is None or actual[name] == wanted
               for name, wanted in expected.items())


def _reply(operation, binding, value):
    return {
        "version": VERSION,
        "operation": operation,
        "binding": _binding_image(binding, operation, reply=True),
        "status": "ok",
        "value": value,
    }


def _refused(operation, binding, refusal):
    if type(refusal) is not Refusal:
        raise TypeError("a Behavior exact-analysis refusal is an exact Refusal")
    return {
        "version": VERSION,
        "operation": operation,
        "binding": _binding_image(binding, operation),
        "status": "refused",
        "refusal": refusal.as_dict(),
    }


def decode_analysis_reply(value, operation, *, expected_binding,
                          with_binding=False):
    """Decode the closed common envelope returned across the job boundary."""

    if operation not in OPERATIONS:
        raise ValueError("the Behavior exact-analysis operation is not registered")
    expected = _binding_image(expected_binding, operation)
    if type(value) is not dict or value.get("version") != VERSION \
            or value.get("operation") != operation:
        raise ValueError(
            "an isolated Behavior exact-analysis reply has the expected identity")
    binding = _binding_image(
        value.get("binding"), operation,
        reply=value.get("status") == "ok")
    if not _binding_matches(expected, binding):
        raise ValueError(
            "an isolated Behavior exact-analysis reply names another frozen binding")
    if value.get("status") == "refused":
        if set(value) != {
                "version", "operation", "binding", "status", "refusal"}:
            raise ValueError(
                "a refused Behavior exact-analysis reply has no extra fields")
        refusal = value["refusal"]
        if type(refusal) is not dict or set(refusal) not in (
                {"kind", "why"}, {"kind", "why", "detail"}) \
                or any(type(refusal.get(name)) is not str or not refusal[name]
                       for name in ("kind", "why")) \
                or "detail" in refusal and type(refusal["detail"]) is not dict:
            raise TypeError(
                "a Behavior exact-analysis refusal has exact kind, why and detail")
        result = Refusal(
            refusal["kind"], refusal["why"], **dict(refusal.get("detail") or {}))
        return (result, binding) if with_binding else result
    if value.get("status") != "ok" \
            or set(value) != {
                "version", "operation", "binding", "status", "value"}:
        raise ValueError(
            "a successful Behavior exact-analysis reply has one typed value")
    result = value["value"]
    return (result, binding) if with_binding else result


def _rows(value, name="rows", *, allow_empty=False):
    if type(value) is not list or (not value and not allow_empty):
        qualifier = "finite" if allow_empty else "nonempty finite"
        raise TypeError(f"{name} is a {qualifier} exact list of valuations")
    out = []
    for index, row in enumerate(value):
        if type(row) is not dict or any(
                type(key) is not str or not key for key in row):
            raise TypeError(f"{name}[{index}] is an exact string-keyed valuation")
        out.append({
            key: thaw_record_value(freeze_record_value(
                item, f"{name}[{index}].{key}"))
            for key, item in row.items()
        })
    return out


def _timeout(value):
    if value is not None and (type(value) is not int or value < 1):
        raise TypeError("an exact-analysis timeout is absent or a positive integer")
    return value


def _analysis_execution_policy(wall_ms, started_ns, feasibility_ms, query_ms):
    """Construction, queries and audits consume the same named job wall."""

    if type(wall_ms) is not int or wall_ms < 1:
        raise TypeError("an exact analysis wall envelope is a positive integer")
    if type(started_ns) is not int or started_ns < 0:
        raise TypeError("an exact analysis start is a monotonic timestamp")
    elapsed_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    # The public invocation supplies its remaining time. Do not shorten it with
    # frontend worker defaults or a fixed construction/query fraction. Witnesses
    # still require their complete audits; running out of time is not evidence.
    if elapsed_ms >= wall_ms:
        raise Refused(Refusal(
            "behavior-exact-analysis-wall-budget-spent",
            "the named exact-analysis wall envelope was spent before bounded "
            "queries could begin",
            wall_ms=wall_ms, elapsed_ms=elapsed_ms))
    remaining_ms = wall_ms - elapsed_ms
    effective_feasibility = min(
        remaining_ms if feasibility_ms is None else feasibility_ms,
        remaining_ms)
    effective_query = min(
        remaining_ms if query_ms is None else query_ms,
        remaining_ms)
    return {
        "artifact_timeout_ms": wall_ms,
        "artifact_started": started_ns / 1_000_000_000,
        "feasibility_timeout_ms": max(1, effective_feasibility),
        "query_timeout_ms": max(1, effective_query),
    }


def _analysis_query_execution(policy):
    """Resources shared by every query in one complete analysis job.

    The residual scheduler selects workers from the available CPU and memory
    authority. This handoff retains the original artifact clock and aggregate
    memory ceiling; it neither restarts the budget nor imposes a worker count.
    """

    return {
        "artifact_ms": policy["artifact_timeout_ms"],
        "artifact_started": policy["artifact_started"],
        "memory_mb": budgets_mod.ARTIFACT_MEMORY_MB,
    }


def _audit_image(value):
    if type(value) is not AuditCheck:
        raise TypeError("a Behavior audit returned something other than AuditCheck")
    return value.as_dict()


def _tool_provenance(timeout_ms):
    import z3

    out = {
        "backend_version": VERSION,
        "tool": "z3",
        "tool_version": z3.get_version_string(),
        "memory_mb": budgets_mod.SOLVER_MEMORY_MB,
    }
    if timeout_ms is not None:
        out["timeout_ms"] = timeout_ms
    return out


def _cache_binding(exact):
    import z3

    return AnalysisCacheBinding(
        semantic_instance=semantic_instance_of(exact), backend="behavior-z3",
        backend_version=VERSION, tool="z3",
        tool_version=z3.get_version_string())


def _build(payload, extras, expected_binding, *, position_closure_deadline=None,
           position_closure_timeout_ms=None):
    common = {
        "operation", "source", "file", "class", "construction",
        "construction_dependency", "contract", "artifact", "horizon",
        "binding", "clock_schedule", "complete_catalogue", "admission",
        "memory_environment", "memory_horizon",
    }
    if type(payload) is not dict or set(payload) != common | set(extras):
        raise TypeError(
            "a Behavior exact-analysis request has its closed operation fields")
    operation = payload["operation"]
    source, file, cls_name = (
        payload["source"], payload["file"], payload["class"])
    if type(source) is not str or type(file) is not str or not file \
            or type(cls_name) is not str or not cls_name.isidentifier():
        raise TypeError(
            "a Behavior exact-analysis request carries source, file and class")
    horizon = payload["horizon"]
    if type(horizon) is not int or horizon < 1:
        raise TypeError("a Behavior exact-analysis horizon is a positive integer")
    artifact = payload["artifact"]
    if type(artifact) is not str or not artifact:
        raise TypeError("a Behavior exact-analysis request identifies its artifact")
    contract = TaskContract.from_dict(payload["contract"])
    memory_image = payload["memory_environment"]
    memory_horizon = payload["memory_horizon"]
    if memory_image is None:
        if memory_horizon is not None:
            raise TypeError(
                "a memoryless exact Behavior request has no memory horizon")
        memory_environment = None
    else:
        from ..core.memory import MemoryEnvironment

        if type(memory_horizon) is not int or memory_horizon < 1:
            raise TypeError(
                "an external-memory exact Behavior request has a positive memory "
                "schedule horizon")
        memory_environment = MemoryEnvironment.from_dict(memory_image)
        faults = memory_environment.faults_against(
            contract.memories, memory_horizon)
        if faults:
            raise Refused(Refusal(
                "behavior-exact-memory-environment-outside-contract",
                "the carried memory environment is outside the frozen task and "
                "schedule horizon",
                first=faults[0], count=len(faults)))
    if expected_binding["artifact"] != artifact \
            or expected_binding["task_contract"] != contract.digest \
            or expected_binding["horizon"] != horizon \
            or expected_binding["analysis_identity"] != artifact:
        raise Refused(Refusal(
            "behavior-exact-request-binding-mismatch",
            "the exact-analysis request payload and frozen operation binding name "
            "different tasks, artifacts, horizons or analysis identities"))
    task = _admitted_task_from_record(
        contract, payload["admission"],
        expected_identity=expected_binding["admission"])
    if task.realization.kind != "behavior" \
            or task.realization.identity != artifact:
        raise Refused(Refusal(
            "behavior-exact-admission-binding-mismatch",
            "the admitted-task handoff and exact Behavior artifact name different "
            "frontends or transition systems"))
    if artifact.startswith("behavior-source/"):
        from .extract import source_model
        from .realization import _source_realization_identity

        model = source_model(source, file, cls=cls_name)
        domains = tuple(sorted(
            domain for domain in model.domains if domain is not None))
        source_identity = _source_realization_identity(
            source, file, model.cls.name, domains)
        if source_identity != artifact:
            raise Refused(Refusal(
                "behavior-exact-source-identity-mismatch",
                "the exact-analysis source does not reproduce the admitted Behavior "
                "source identity",
                admitted=artifact, source=source_identity))
    facts = ConstructionFacts.from_dict(payload["construction"])
    expected_boundary = frozenset(
        memory.name for memory in contract.memories
        if memory_environment is not None)
    if frozenset(facts.boundary_fields) != expected_boundary:
        raise Refused(Refusal(
            "behavior-exact-construction-boundary-mismatch",
            "the construction projection and external-memory boundary name "
            "different state",
            construction=sorted(facts.boundary_fields),
            memory=sorted(expected_boundary)))
    dependency = payload["construction_dependency"]
    if type(dependency) is not str or not dependency:
        raise TypeError("a Behavior construction image identifies its dependency")
    if dependency != _dependency_identity():
        raise Refused(Refusal(
            "behavior-exact-construction-dependency-mismatch",
            "the isolated lowering job has different trusted construction "
            "dependency bytes from the frozen construction observation"))
    if not facts.boundary_fields:
        _seed_construction_facts(
            source, file, cls_name, facts, dependency)
    schedule_image = payload["clock_schedule"]
    if schedule_image is None:
        schedule = None
    else:
        from ..core.clock_schedule import GlobalSchedule

        schedule = GlobalSchedule.from_dict(schedule_image)
        if len(schedule.slots) != horizon:
            raise Refused(Refusal(
                "behavior-atomic-schedule-horizon-mismatch",
                "the frozen atomic schedule and exact-analysis horizon differ",
                schedule=len(schedule.slots), horizon=horizon))
    if operation == "atomic-frontend":
        if schedule is not None:
            raise Refused(Refusal(
                "behavior-atomic-frontend-already-scheduled",
                "the schedule-independent frontend request cannot carry a trace "
                "schedule"))
        from .atomic_exact import atomic_frontend_catalogue

        frontend = atomic_frontend_catalogue(
            source, file, contract, horizon, construction=facts, cls=cls_name)
        behavior = None
    elif schedule is None:
        from .task_boundary import lower_task_behavior

        behavior, _boundary = lower_task_behavior(
            source, file, contract, horizon, cls=cls_name,
            artifact_identity=(artifact if artifact.startswith(
                "behavior-source/") else None),
            memory_environment=memory_environment,
            memory_horizon=memory_horizon,
            construction=facts)
        if behavior.analysis_identity() != artifact:
            raise Refused(Refusal(
                "behavior-public-exact-artifact-mismatch",
                "the admitted realization and isolated Behavior lowering name "
                "different transition systems",
                admitted=artifact, behavior=behavior.analysis_identity()))
        frontend = behavior.frontend_catalogue_manifest()
    else:
        from .atomic_exact import atomic_frontend_catalogue

        frontend = atomic_frontend_catalogue(
            source, file, contract, horizon, construction=facts)
    policy = evaluation_goal_policy(contract, frontend, artifact_kind="behavior")
    complete = policy.manifest
    carried = payload["complete_catalogue"]
    if carried is not None:
        carried = CatalogueManifest.from_dict(carried)
        if carried != complete:
            raise Refused(Refusal(
                "behavior-exact-catalogue-mismatch",
                "the exact job and public handle received different frozen complete "
                "catalogues",
                carried=carried.digest(), rebuilt=complete.digest()))
    if operation in {"frontend", "atomic-frontend"}:
        exact = None
    elif schedule is None:
        exact = behavior.bind(
            horizon, task_contract=contract.digest,
            catalogue_manifest=complete,
            _position_closure_deadline=position_closure_deadline,
            _position_closure_timeout_ms=position_closure_timeout_ms)
    else:
        from .atomic_exact import build_atomic_behavior

        exact = build_atomic_behavior(
            source, file, contract, schedule, artifact=artifact, cls=cls_name,
            task_contract=contract.digest, complete_catalogue=complete,
            construction=facts,
            memory_environment=memory_environment,
            memory_horizon=memory_horizon).bind()
    if exact is None:
        actual_binding = {
            **expected_binding,
            "domain": None,
            "catalogue": complete.digest(),
        }
        if not _binding_matches(expected_binding, actual_binding):
            raise Refused(Refusal(
                "behavior-exact-operation-binding-mismatch",
                "the frontend does not name the frozen artifact and task"))
        return task, None, frontend, policy, actual_binding
    actual_binding = {
        "artifact": exact.domain.artifact,
        "task_contract": exact.domain.task_contract,
        "horizon": exact.domain.bound,
        "domain": exact.domain.digest(),
        "analysis_identity": exact.instance.analysis_identity(),
        "catalogue": complete.digest(),
        "admission": task.handoff_identity,
    }
    if not _binding_matches(expected_binding, actual_binding):
        raise Refused(Refusal(
            "behavior-exact-operation-binding-mismatch",
            "the rebuilt exact operation does not name the frozen artifact, domain, "
            "analysis identity and catalogue"))
    return task, exact, frontend, policy, actual_binding


def run_analysis_job(payload):
    """Execute one exact operation and return only a closed finite JSON record."""

    if type(payload) is not dict or type(payload.get("operation")) is not str:
        raise TypeError("a Behavior exact-analysis job names one exact operation")
    operation = payload["operation"]
    if operation not in OPERATIONS:
        raise ValueError("a Behavior exact-analysis job names a registered operation")
    scope = nullcontext()
    if operation == "analyse":
        wall_ms = payload.get("analysis_wall_ms")
        if type(wall_ms) is not int or wall_ms < 1:
            raise TypeError("an exact analysis wall envelope is a positive integer")
        # The isolated child must own the same lifetime as its named job. Source
        # replay and preparation read this owner too; neither gets a fresh clock.
        scope = analysis_scope(wall_ms / 1000)
    with scope:
        return _run_analysis_job(payload, operation)


def _run_analysis_job(payload, operation):
    extras = {
        "frontend": set(),
        "atomic-frontend": set(),
        "bind": set(),
        "record-audit": {"stimulus", "raw_trace"},
        "replay": {"rows"},
        "audit-request": {"rows", "domain"},
        "step-audit": {"traces"},
        "source-audit": {"traces"},
        "feasible": {"timeout_ms"},
        "query": {"goals", "timeout_ms"},
        "analyse": {
            "evaluation", "feasibility_timeout_ms", "query_timeout_ms", "batch",
            "analysis_wall_ms"},
    }[operation]
    request_binding = _binding_image(payload.get("binding"), operation)
    exact = None
    started_ns = time.monotonic_ns()
    execution_policy = None
    try:
        position_closure_deadline = None
        position_closure_timeout_ms = None
        if operation == "analyse":
            execution_policy = _analysis_execution_policy(
                payload["analysis_wall_ms"], started_ns,
                payload["feasibility_timeout_ms"],
                payload["query_timeout_ms"])
            artifact_deadline = (
                execution_policy["artifact_started"]
                + execution_policy["artifact_timeout_ms"] / 1000.0)
            position_closure_deadline = artifact_deadline
            position_closure_timeout_ms = execution_policy["query_timeout_ms"]
        task, exact, frontend, policy, reply_binding = _build(
            payload, extras, request_binding,
            position_closure_deadline=position_closure_deadline,
            position_closure_timeout_ms=position_closure_timeout_ms)
        complete = policy.manifest
        if operation in {"frontend", "atomic-frontend"}:
            value = {
                "frontend_catalogue": frontend.as_dict(),
                "catalogue": complete.as_dict(),
            }
            return _reply(operation, reply_binding, value)
        instance = exact.instance
        if operation == "analyse":
            from ..core.analysis import (
                _analyse_subject,
                _analysis_subject_from_wire,
            )
            from ..core.goal_provider import (
                BEHAVIOR_EXACT_BOUNDED_POLICY,
                EXACT_BOUNDED_HIT,
            )
            from .atomic_exact import AtomicBehaviorInstance

            subject = _analysis_subject_from_wire(
                task, payload["evaluation"], complete)
            capability = policy.bind_complete_implementation(
                EXACT_BOUNDED_HIT, BEHAVIOR_EXACT_BOUNDED_POLICY,
                policy.manifest)

            if type(instance) is AtomicBehaviorInstance:
                from .atomic_record import audit_atomic_record

                record_audit = lambda result, bound: audit_atomic_record(
                    result, bound, task=task)
            else:
                from .analysis import audit_record as audit_behavior_record

                record_audit = lambda result, bound: audit_behavior_record(
                    result, bound, task=task)
            assert execution_policy is not None
            outcome = _analyse_subject(
                subject, exact=exact, record_audit=record_audit,
                goal_capability_use=capability,
                feasibility_timeout_ms=(
                    execution_policy["feasibility_timeout_ms"]),
                query_timeout_ms=execution_policy["query_timeout_ms"],
                batch=payload["batch"],
                _artifact_timeout_ms=(
                    execution_policy["artifact_timeout_ms"]),
                # All descendants remain inside this named process tree and share its
                # aggregate memory and wall ceilings.  The residual scheduler decides
                # from those limits whether singleton refinement is local or parallel.
                _query_execution=_analysis_query_execution(execution_policy))
            value = outcome.as_dict()
        elif operation == "bind":
            from ..core.goal_provider import BEHAVIOR_EXACT_BOUNDED_POLICY

            value = {
                "domain": exact.domain.as_dict(),
                "domain_digest": exact.domain.digest(),
                "analysis_identity": instance.analysis_identity(),
                "evidence_identity": instance.evidence_identity(
                    exact.domain.bound),
                "analysis_cache": _cache_binding(exact).as_dict(),
                "frontend_catalogue": frontend.as_dict(),
                "catalogue": complete.as_dict(),
                "audit_plan": instance.audit_plan().as_dict(),
                "trusted_assumptions": [
                    item.as_dict() for item in instance.trusted_assumptions()],
                "contract": instance.contract(),
                "well_formed": list(instance.well_formed()),
                "unaccounted_goals": list(instance.unaccounted_goals()),
                "ruled_out": dict(instance.ruled_out()),
                "budgets": dict(instance.budgets()),
                "task_audit": _audit_image(instance.task_audit(task)),
                "goal_implementation": BEHAVIOR_EXACT_BOUNDED_POLICY,
            }
        elif operation == "record-audit":
            stimulus = Stimulus.from_dict(payload["stimulus"])
            raw_trace = AuditedTrace.from_dict(payload["raw_trace"])
            if raw_trace.stimulus != stimulus:
                raise Refused(Refusal(
                    "behavior-record-stimulus-mismatch",
                    "the raw record and Behavior record-audit request name different "
                    "stimuli"))
            result = RunResult.not_compared(
                stimulus, raw_trace,
                RunFailure.verifier(
                    "bounded-analysis-view",
                    Refusal(
                        "analysis-raw-run-not-compared",
                        "bounded analysis reads the raw run only")))
            from .atomic_exact import AtomicBehaviorInstance

            if type(instance) is AtomicBehaviorInstance:
                from .atomic_record import audit_atomic_record

                audited = audit_atomic_record(result, exact, task=task)
            else:
                from .analysis import audit_record

                audited = audit_record(result, exact, task=task)
            value = audited.as_dict()
        elif operation == "replay":
            value = instance.replay(_rows(payload["rows"])).as_dict()
        elif operation == "audit-request":
            rows = _rows(payload["rows"])
            domain = payload["domain"]
            if type(domain) is not str or not domain:
                raise TypeError("a witness audit names one trace-domain identity")
            if domain != exact.domain.digest():
                raise Refused(Refusal(
                    "behavior-witness-domain-mismatch",
                    "a Behavior witness audit cannot stamp a request with a trace "
                    "domain other than the frozen exact operation binding",
                    requested=domain, exact=exact.domain.digest()))
            replayed, witness = instance.replay_witness(rows, domain)
            if replayed.broken or witness is None:
                value = {
                    "kind": "audit",
                    "value": AuditCheck.refused(
                        "behavior-witness-replay-failed",
                        "the decoded solver request could not be replayed completely: "
                        f"{replayed.broken or 'no complete witness was produced'}"
                    ).as_dict(),
                }
            else:
                check = instance.audit((rows,))
                if type(check) is not AuditCheck \
                        or check.status is not AuditCheckStatus.AGREED:
                    value = {"kind": "audit", "value": _audit_image(check)}
                else:
                    value = {"kind": "witness", "value": witness.as_record()}
        elif operation in {"step-audit", "source-audit"}:
            traces = [
                _rows(rows, f"traces[{index}]")
                for index, rows in enumerate(payload["traces"])
            ] if type(payload["traces"]) is list else None
            if traces is None:
                raise TypeError("a Behavior audit receives a finite trace list")
            method = (instance.audit if operation == "step-audit"
                      else instance.source_audit)
            value = _audit_image(method(traces))
        elif operation == "feasible":
            started = time.monotonic_ns()
            answer, detail = instance.feasible(
                exact.domain.bound, timeout_ms=_timeout(payload["timeout_ms"]))
            elapsed = time.monotonic_ns() - started
            outcome = {
                Feasible.YES: "sat",
                Feasible.NO: "unsat",
                Feasible.UNRESOLVED: "unknown",
            }[answer]
            try:
                telemetry = QueryTelemetry(
                    backend="behavior-z3", kind=QueryKind.FEASIBILITY,
                    semantic_instance=semantic_instance_of(exact),
                    horizon=exact.domain.bound, outcome=outcome,
                    timings=StageTimings({"total_ns": elapsed}),
                    peak_rss_bytes=process_peak_rss_bytes(),
                    provenance=_tool_provenance(payload["timeout_ms"]))
            except Exception:  # noqa: BLE001 - telemetry cannot change the answer
                telemetry = None
            value = {
                "answer": answer.value,
                "detail": detail if answer is not Feasible.YES else "",
                "witness": detail if answer is Feasible.YES else None,
                "telemetry": (
                    None if telemetry is None else telemetry.as_dict()),
            }
        else:
            goals = payload["goals"]
            if type(goals) is not list or any(
                    type(goal) is not str or not goal for goal in goals):
                raise TypeError("a Behavior exact query names a finite goal list")
            timeout_ms = _timeout(payload["timeout_ms"])
            query_options = {
                "timeout_ms": timeout_ms,
                "telemetry_scope": QueryTelemetryScope(
                    backend="behavior-z3",
                    semantic_instance=semantic_instance_of(exact),
                    horizon=exact.domain.bound),
            }
            # Ordinary Behavior uses ``residual.decide_goals`` and must remain
            # inside this named worker.  The atomic provider has one in-process
            # fixed-schedule formula and no worker option.
            from .atomic_exact import AtomicBehaviorInstance

            if type(instance) is not AtomicBehaviorInstance:
                query_options["workers"] = 1
            verdict = instance.query(
                goals, exact.domain.bound, **query_options)
            value = annotate_verdict(
                verdict, exact, timeout_ms, backend="behavior-z3",
                provenance=_tool_provenance).as_dict()
        return _reply(operation, reply_binding, value)
    except AnalysisStopped as exc:
        refusal = Refusal(
            "analysis-budget-exhausted",
            "the shared analysis wall budget ended before a complete audited classification",
            wall_seconds=exc.wall_seconds, elapsed_seconds=exc.elapsed_seconds,
            notes=list(getattr(exc, "__notes__", ())))
        return _refused(
            operation, request_binding,
            annotate_refusal(
                refusal, operation, exact, payload, started_ns,
                backend="behavior-z3", provenance=_tool_provenance))
    except Refused as exc:
        return _refused(
            operation, request_binding,
            annotate_refusal(
                exc.refusal, operation, exact, payload, started_ns,
                backend="behavior-z3", provenance=_tool_provenance))
    except ConstructionUnavailable as exc:
        refusal = Refusal(
            "behavior-exact-construction-unavailable",
            "the frozen construction observation could not be bound to isolated "
            "lowering",
            error=str(exc))
        return _refused(
            operation, request_binding,
            annotate_refusal(
                refusal, operation, exact, payload, started_ns,
                backend="behavior-z3", provenance=_tool_provenance))
    except Exception as exc:  # noqa: BLE001 - typed ancillary job boundary
        refusal = _construction_failure(exc)
        return _refused(
            operation, request_binding,
            annotate_refusal(
                refusal, operation, exact, payload, started_ns,
                backend="behavior-z3", provenance=_tool_provenance))
