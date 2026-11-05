"""Solver-free parent capability for one isolated exact Behavior artifact."""

from __future__ import annotations

from types import MappingProxyType

from ..core.admission import AdmittedTask
from ..core.analysis import RecordAudit
from ..core.audit import AuditCheck, AuditCheckStatus, AuditedWitness
from ..core.audit_plan import AuditPlan
from ..core.catalogue import CatalogueManifest
from ..core.conditional_evidence import TrustedAssumption
from ..core.domain import TraceDomain
from ..core.frozen import Frozen, FrozenMap
from ..core.instance import BoundedInstance, Feasible, Replay, StepOutput, Verdict
from ..core.query import FeasibilityCall
from ..core.query_telemetry import QueryTelemetry, analysis_semantic_instance
from ..core.record_value import freeze_record_value, thaw_record_value
from ..core.refusal import Refusal, Refused
from ..core.run_record import RunResult

__all__ = ["BehaviorExactJobContext", "BehaviorIsolatedAnalysisInstance"]


def _analysis_subject_image(evaluation):
    """The complete score identity plus only the raw runs analysis consumes."""

    from ..core.outcome import ConcreteEvaluation

    if type(evaluation) is not ConcreteEvaluation:
        raise TypeError("an isolated Behavior analysis needs ConcreteEvaluation")
    return {
        "concrete": evaluation.fingerprint,
        "evaluation": evaluation.as_evidence_dict(),
        "raw_records": [
            {
                "stimulus": result.stimulus.as_dict(),
                "trace": (None if result.trace is None else
                          result.trace.as_dict()),
            }
            for result in evaluation.results
        ],
    }


def _wire_rows(rows, name="rows"):
    """Copy trusted immutable trace rows into the strict plain-JSON carrier."""

    if type(rows) not in (tuple, list):
        raise TypeError(f"{name} must be one materialized finite trace")
    encoded = []
    for index, row in enumerate(rows):
        if type(row) not in (dict, FrozenMap, MappingProxyType):
            raise TypeError(
                f"{name}[{index}] must be an exact finite valuation mapping")
        if any(type(key) is not str or not key for key in row):
            raise TypeError(
                f"{name}[{index}] has a field outside the string-keyed carrier")
        # This is the public logical-value carrier, not the SolverIR storage row.
        # Preserve Boolean/enumeration/record/fixed-array values recursively; the
        # isolated instance validates them against its frozen ProcedureSignature.
        encoded.append({
            key: thaw_record_value(freeze_record_value(
                item, f"{name}[{index}].{key}"))
            for key, item in row.items()
        })
    return encoded


def _json_image(value):
    """The exact image after the standard JSON transport erases Python tuples."""

    kind = type(value)
    if value is None or kind in (bool, int, float, str):
        return value
    if kind in (tuple, list):
        return [_json_image(item) for item in value]
    if kind is dict:
        return {key: _json_image(item) for key, item in value.items()}
    raise TypeError(f"{kind.__name__} has no exact JSON protocol image")


def _decode_audit_check(value):
    if type(value) is not dict or type(value.get("status")) is not str:
        raise TypeError("an audit-check image has an exact status")
    status = value["status"]
    if status == "agreed" and set(value) == {"status"}:
        return AuditCheck.agreed()
    if status == "inconsistent" and set(value) == {"status", "findings"} \
            and type(value["findings"]) is list \
            and all(type(item) is str for item in value["findings"]):
        return AuditCheck.inconsistent(value["findings"])
    if status == "refused" and set(value) == {"status", "refusal"}:
        refusal = value["refusal"]
        if type(refusal) is dict and set(refusal) == {"kind", "why"} \
                and all(type(refusal[name]) is str and refusal[name]
                        for name in ("kind", "why")):
            return AuditCheck.refused(refusal["kind"], refusal["why"])
    raise ValueError("an audit-check image is not canonical")


def _decode_replay(value):
    required = {"hits", "outputs", "error", "state", "steps"}
    if type(value) is not dict or set(value) not in (
            required, required | {"broken"}) \
            or type(value["hits"]) is not list \
            or type(value["outputs"]) is not list \
            or type(value["error"]) is not str \
            or value.get("broken") is not None \
            and type(value.get("broken")) is not str \
            or value["steps"] is not None and (
                type(value["steps"]) is not int or value["steps"] < 0):
        raise TypeError("an exact replay image has its complete finite field types")
    outputs = []
    for row in value["outputs"]:
        if type(row) is not dict or set(row) != {"count", "buffers"} \
                or type(row["count"]) is not int \
                or type(row["buffers"]) is not dict:
            raise TypeError("an exact replay output has count and buffers")
        outputs.append(StepOutput(row["count"], row["buffers"]))
    got = Replay(
        value["hits"], outputs, error=value["error"], state=value["state"],
        broken=value.get("broken"), steps=value["steps"])
    if _json_image(got.as_dict()) != value:
        raise ValueError("an exact replay image is not canonical")
    return got


def _decode_record_audit(value):
    if type(value) is not dict or type(value.get("disposition")) is not str:
        raise TypeError("a Behavior record-audit image has an exact disposition")
    disposition = value["disposition"]
    if disposition == "admitted" and set(value) == {
            "disposition", "witness", "witness_ref"}:
        witness = AuditedWitness.from_record(value["witness"])
        if value["witness_ref"] != "witness:" + witness.fingerprint:
            raise ValueError("the record-audit witness reference is inconsistent")
        return RecordAudit.admitted(witness)
    if disposition in {"outside", "inconsistent"} \
            and set(value) == {"disposition", "why"} \
            and type(value["why"]) is str:
        constructor = (RecordAudit.outside if disposition == "outside"
                       else RecordAudit.inconsistent)
        return constructor(value["why"])
    if disposition == "refused" \
            and set(value) in ({"disposition", "kind", "why"},
                               {"disposition", "kind", "why", "detail"}) \
            and all(type(value[name]) is str and value[name]
                    for name in ("kind", "why")):
        if 'detail' in value and (type(value['detail']) is not dict or not value['detail']):
            raise ValueError('record-audit details must be a nonempty mapping')
        return RecordAudit.refused(value["kind"], value["why"], **value.get('detail', {}))
    raise ValueError("a Behavior record-audit image is not canonical")


class BehaviorExactJobContext(Frozen):
    """Frozen source inputs; executable construction belongs to exact jobs.

    The score path needs the source catalogue and signature, not a successful
    reset. Exact requests authenticate the construction image when building
    their payload, using the existing immutable construction cache.
    """

    __slots__ = (
        "class_name",
        "clock_schedule",
        "construction_dependency",
        "domains",
        "file",
        "goal_policy",
        "process_inputs",
        "refusal",
        "source",
        "source_catalogue",
        "source_identity",
        "task",
        "timeout_s",
    )

    def __init__(self, task, source, file, *, timeout_s, model=None):
        from ..core.failure import CandidateFault, FailureRaised, RunFailure

        if type(task) is not AdmittedTask:
            raise TypeError("a Behavior exact job context needs an AdmittedTask")
        if type(source) is not str or type(file) is not str or not file:
            raise TypeError("a Behavior exact job context needs frozen source and file")
        if type(timeout_s) is not int or timeout_s < 1:
            raise TypeError("a Behavior exact job timeout is a positive integer")
        self.task = task
        self.source = source
        self.file = file
        self.timeout_s = timeout_s
        from .isolated import Broken, _trusted_source_protocol_facts

        class_name = None
        construction_dependency = None
        domains = ()
        process_inputs = ()
        source_catalogue = None
        source_identity = None
        refusal = None
        try:
            from .construction import _dependency_identity

            facts, _offers, source_catalogue = _trusted_source_protocol_facts(
                source, file, model, task.contract.source_rules,
                tuple(clock.name for clock in task.contract.clocks)
                if len(task.contract.clocks) > 1 else None)
            if type(source_catalogue) is not CatalogueManifest:
                raise TypeError("the source judge did not return a catalogue manifest")
            class_name = facts["cls_name"]
            from .task_boundary import task_call_inputs, task_clock_domains

            domains = task_clock_domains(task.contract, facts["domains"])

            process_inputs = task_call_inputs(
                task.contract, facts["process_inputs"],
                extra_keywords=facts["process_kwargs"],
                optional_inputs=facts["process_optional_inputs"])
            from .realization import _source_realization_identity

            source_identity = _source_realization_identity(
                source, file, class_name, domains)
            if task.realization.identity.startswith("behavior-source/") \
                    and task.realization.identity != source_identity:
                raise Refused(Refusal(
                    "behavior-public-exact-artifact-mismatch",
                    "the admitted Behavior realization and exact source snapshot "
                    "name different source artifacts",
                    admitted=task.realization.identity,
                    source=source_identity))
            construction_dependency = _dependency_identity()
        except CandidateFault as exc:
            raise FailureRaised(RunFailure.candidate("candidate-frontend", exc.refusal)) from None
        except FailureRaised:
            raise
        except Refused as exc:
            refusal = exc.refusal
        except (Broken, OSError) as exc:
            raise FailureRaised(RunFailure.infrastructure(
                "behavior-frontend", self.unavailable("source", exc))) from None
        except Exception as exc:  # noqa: BLE001 - source metadata service boundary
            raise FailureRaised(RunFailure.verifier(
                "behavior-frontend", self.unavailable("source", exc))) from None
        self.class_name = class_name
        self.construction_dependency = construction_dependency
        self.domains = domains
        self.process_inputs = process_inputs
        self.source_catalogue = source_catalogue
        self.source_identity = source_identity
        self.clock_schedule = None
        self.refusal = refusal
        from ..goal_policy import evaluation_goal_policy

        self.goal_policy = (None if refusal is not None else evaluation_goal_policy(
            task.contract, source_catalogue, artifact_kind=task.realization.kind))
        self._freeze()

    def for_schedule(self, schedule):
        """Return this exact source context bound to one immutable schedule."""

        from ..core.clock_schedule import GlobalSchedule

        if type(schedule) is not GlobalSchedule:
            raise TypeError("an atomic Behavior context needs a GlobalSchedule")
        scheduled = tuple(timing.name for timing in schedule.timings)
        if scheduled != self.domains:
            raise Refused(Refusal(
                "behavior-atomic-context-domain-mismatch",
                "the frozen source handlers and concrete schedule name different "
                "clock domains",
                source=list(self.domains), schedule=list(scheduled)))
        got = object.__new__(type(self))
        for name in (
                "task", "source", "file", "timeout_s", "class_name",
                "construction_dependency", "domains",
                "process_inputs", "source_catalogue", "source_identity", "refusal", "goal_policy"):
            object.__setattr__(got, name, getattr(self, name))
        object.__setattr__(got, "clock_schedule", schedule)
        got._freeze()
        return got

    @staticmethod
    def unavailable(stage, error):
        detail = getattr(error, "detail", "") or str(error)
        how = getattr(error, "how", type(error).__name__)
        code = str(getattr(error, "code", "") or "")
        return Refusal(
            code or f"behavior-exact-isolated-{stage}-unavailable",
            f"the isolated Behavior exact {stage} did not return a valid answer",
            failure=str(how), detail=str(detail))

    def prepare(self):
        """Return any source-admission refusal without executing the candidate."""

        return self.refusal

    def _construction_image(self):
        """Authenticate an exact-job entry image against the frozen dependency."""
        from .construction import _dependency_identity, construction_facts

        def check_dependency():
            if _dependency_identity() != self.construction_dependency:
                raise Refused(Refusal(
                    "behavior-exact-construction-dependency-changed",
                    "exact construction must use the dependency frozen with the "
                    "Behavior source"))

        try:
            check_dependency()
            construction = construction_facts(
                self.source, self.file, cls_name=self.class_name)
            check_dependency()
            return construction
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - exact construction boundary
            raise Refused(self.unavailable("construction", exc)) from None

    def source_goal_handoff(self):
        """Bind the source-stable catalogue to the task policy before scoring.

        The isolated source judge has already selected the executable call closure
        and frozen its structural and safety occurrences.  This handoff deliberately
        does not lower the Behavior or construct a symbolic relation: those are
        ancillary analysis work and belong after concrete-score persistence.
        """

        prepared = self.prepare()
        if isinstance(prepared, Refusal):
            raise Refused(prepared)
        frontend = self.source_catalogue
        if type(frontend) is not CatalogueManifest:
            raise Refused(Refusal(
                "behavior-source-catalogue-unavailable",
                "the isolated source judge did not return a concrete Behavior "
                "catalogue"))
        from ..core.goal_provider import (
            BEHAVIOR_EXACT_BOUNDED_POLICY,
            EXACT_BOUNDED_HIT,
        )
        policy = self.goal_policy
        capability = policy.bind_complete_implementation(
            EXACT_BOUNDED_HIT, BEHAVIOR_EXACT_BOUNDED_POLICY,
            policy.manifest)
        return frontend, policy, capability

    def initial_binding(self, horizon):
        if type(horizon) is not int or horizon < 1:
            raise TypeError("a Behavior exact binding needs a positive horizon")
        return {
            "artifact": self.task.realization.identity,
            "task_contract": self.task.contract.digest,
            "horizon": horizon,
            "domain": None,
            "analysis_identity": self.task.realization.identity,
            "catalogue": None,
            "admission": self.task.handoff_identity,
        }

    def analyse(self, evaluation, horizon, *, goal_policy, goal_capability,
                memory_environment=None, memory_horizon=None,
                feasibility_timeout_ms=None, query_timeout_ms=None, batch=None):
        """Bind and analyse once, after the score has been persisted.

        The lazy request already froze source, task, catalogue and implementation
        capability before generation.  One named judge now lowers that snapshot,
        binds the finite domain, audits the raw records and closes its goals without
        rebuilding the same exact artifact between those stages.
        """

        from ..core.goal_provider import GoalCapabilityUse, GoalPolicy
        from ..core.outcome import AnalysisOutcome
        from ..worker import WorkerFailed, run_job_strict
        from .analysis_job import decode_analysis_reply

        if type(goal_policy) is not GoalPolicy \
                or type(goal_capability) is not GoalCapabilityUse \
                or goal_policy.manifest is not goal_capability.source_catalogue \
                or goal_policy.digest() != goal_capability.policy_digest:
            raise TypeError("a complete Behavior analysis needs its frozen goal handoff")
        binding = self.initial_binding(horizon)
        binding["catalogue"] = goal_policy.manifest.digest()
        from ..analysis_budget import active_analysis

        budget = active_analysis()
        timeout_s = self.timeout_s if budget is None else budget.remaining()
        try:
            raw = run_job_strict(
                "behavior_exact_analysis",
                self.payload(
                    "analyse", horizon, binding=binding,
                    complete_catalogue=goal_policy.manifest,
                    evaluation=_analysis_subject_image(evaluation),
                    memory_environment=(
                        None if memory_environment is None
                        else memory_environment.as_dict()),
                    memory_horizon=memory_horizon,
                    analysis_wall_ms=max(1, int(timeout_s * 1000)),
                    feasibility_timeout_ms=feasibility_timeout_ms,
                    query_timeout_ms=query_timeout_ms, batch=batch),
                timeout_s=timeout_s,
                label="isolated complete Behavior bounded analysis")
            value, reply_binding = decode_analysis_reply(
                raw, "analyse", expected_binding=binding, with_binding=True)
            if isinstance(value, Refusal):
                raise Refused(value)
            outcome = AnalysisOutcome.from_dict(value)
            if outcome.concrete != evaluation.fingerprint:
                raise ValueError(
                    "the isolated analysis answer names another concrete result")
            if reply_binding["catalogue"] != goal_policy.manifest.digest():
                raise ValueError(
                    "the isolated analysis answer names another goal catalogue")
            return outcome
        except WorkerFailed as exc:
            raise Refused(self.unavailable("analysis-complete", exc)) from None
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - strict worker protocol boundary
            raise Refused(self.unavailable("analysis-complete", exc)) from None

    def payload(self, operation, horizon, *, binding, complete_catalogue=None,
                **extra):
        prepared = self.prepare()
        if isinstance(prepared, Refusal):
            raise Refused(prepared)
        fields = {
            "artifact", "task_contract", "horizon", "domain",
            "analysis_identity", "catalogue", "admission",
        }
        binding = dict(binding) if isinstance(binding, FrozenMap) else binding
        if type(binding) is not dict or set(binding) != fields:
            raise TypeError("a Behavior exact request has one closed binding")
        if binding["artifact"] != self.task.realization.identity \
                or binding["task_contract"] != self.task.contract.digest \
                or binding["horizon"] != horizon \
                or binding["analysis_identity"] != self.task.realization.identity \
                or binding["admission"] != self.task.handoff_identity:
            raise Refused(Refusal(
                "behavior-exact-context-binding-mismatch",
                "a Behavior exact request cannot replace the frozen task, artifact, "
                "horizon or analysis identity"))
        if complete_catalogue is not None \
                and type(complete_catalogue) is not CatalogueManifest:
            raise TypeError(
                "a Behavior exact request carries an exact catalogue manifest")
        if (complete_catalogue is None) != (binding["catalogue"] is None):
            raise Refused(Refusal(
                "behavior-exact-context-catalogue-mismatch",
                "a Behavior exact request binds its carried catalogue identity"))
        if complete_catalogue is not None \
                and complete_catalogue.digest() != binding["catalogue"]:
            raise Refused(Refusal(
                "behavior-exact-context-catalogue-mismatch",
                "a Behavior exact request carries another catalogue"))
        if operation == "analyse":
            # The public analysis invocation owns the wall; frontend preparation's
            # worker limit must not silently shorten a separately configured analysis.
            extra.setdefault("analysis_wall_ms", self.timeout_s * 1000)
        construction = self._construction_image()
        if extra.get("memory_environment") is not None:
            construction = construction.for_boundary(
                memory.name for memory in self.task.contract.memories)
        return {
            "operation": operation,
            "source": self.source,
            "file": self.file,
            "class": self.class_name,
            "construction": construction.as_dict(),
            "construction_dependency": self.construction_dependency,
            "contract": self.task.contract.as_dict(),
            "admission": self.task.as_dict(),
            "artifact": self.task.realization.identity,
            "horizon": horizon,
            "clock_schedule": (
                None if self.clock_schedule is None
                else self.clock_schedule.as_dict()),
            "binding": dict(binding),
            "complete_catalogue": (
                None if complete_catalogue is None
                else complete_catalogue.as_dict()),
            **extra,
        }

    def validate_catalogues(self, frontend, complete):
        if type(frontend) is not CatalogueManifest \
                or type(complete) is not CatalogueManifest:
            raise TypeError("a Behavior exact context binds exact catalogue manifests")
        if frontend != self.source_catalogue:
            source_ids = set(self.source_catalogue)
            exact_ids = set(frontend)
            raise Refused(Refusal(
                "behavior-public-exact-frontend-catalogue-mismatch",
                "the isolated exact frontend changed the source catalogue frozen "
                "before concrete scoring",
                source_digest=self.source_catalogue.digest(),
                exact_digest=frontend.digest(),
                missing_from_exact=sorted(source_ids - exact_ids),
                added_by_exact=sorted(exact_ids - source_ids),
                shared_schema_changes={
                    goal: self.source_catalogue[goal].conflict_with(frontend[goal])
                    for goal in sorted(source_ids & exact_ids)
                    if self.source_catalogue[goal] != frontend[goal]
                }))
        policy = self.goal_policy
        if policy.manifest != complete:
            raise Refused(Refusal(
                "behavior-public-exact-catalogue-mismatch",
                "the public task policy and isolated Behavior exact service produced "
                "different complete catalogues"))
        return policy


class BehaviorIsolatedAnalysisInstance(Frozen):
    """Parent-side exact provider proxy containing no lowered IR or solver object."""

    __slots__ = (
        "_analysis_cache", "_analysis_identity", "_audit_plan", "_binding", "_budgets",
        "_catalogue", "_context", "_contract", "_domain", "_evidence",
        "_frontend_catalogue", "_goal_capability", "_goal_policy",
        "_memory_environment", "_memory_horizon",
        "_request_limit", "_ruled_out", "_task_audit", "_trusted", "_unaccounted",
        "_well_formed",
    )

    def __init__(self, context, horizon, *, goal_policy=None,
                 goal_capability=None, memory_environment=None,
                 memory_horizon=None):
        if type(context) is not BehaviorExactJobContext:
            raise TypeError(
                "an isolated Behavior analysis instance needs its exact context")
        self._context = context
        from ..core.memory import MemoryEnvironment

        if memory_environment is None:
            if memory_horizon is not None:
                raise ValueError(
                    "a memoryless Behavior analysis has no memory schedule horizon")
        elif type(memory_environment) is not MemoryEnvironment \
                or type(memory_horizon) is not int or memory_horizon < 1:
            raise TypeError(
                "an external-memory Behavior analysis needs one exact environment "
                "and its positive schedule horizon")
        self._memory_environment = memory_environment
        self._memory_horizon = memory_horizon
        prepared = context.prepare()
        if isinstance(prepared, Refusal):
            raise Refused(prepared)
        initial_binding = context.initial_binding(horizon)
        value, reply_binding = self._raw_call(
            "bind", horizon=horizon, binding=initial_binding,
            complete_catalogue=None, with_binding=True)
        if isinstance(value, Refusal):
            raise Refused(value)
        fields = {
            "domain", "domain_digest", "analysis_identity", "evidence_identity",
            "analysis_cache",
            "frontend_catalogue", "catalogue", "audit_plan",
            "trusted_assumptions", "contract", "well_formed",
            "unaccounted_goals", "ruled_out", "budgets",
            "task_audit", "goal_implementation",
        }
        try:
            if type(value) is not dict or set(value) != fields:
                raise TypeError(
                    "the isolated Behavior bind reply is a complete identity record")
            image = value["domain"]
            if type(image) is not dict or set(image) != {
                    "bound", "contract", "initial", "schema", "frontend", "policy",
                    "artifact", "task_contract"}:
                raise TypeError("the isolated Behavior domain image is complete")
            domain = TraceDomain(**image)
            if domain.as_dict() != image \
                    or domain.digest() != value["domain_digest"]:
                raise ValueError("the isolated Behavior domain is not canonical")
            frontend = CatalogueManifest.from_dict(value["frontend_catalogue"])
            catalogue = CatalogueManifest.from_dict(value["catalogue"])
            derived_policy = context.validate_catalogues(frontend, catalogue)
            from ..core.goal_provider import (
                BEHAVIOR_EXACT_BOUNDED_POLICY,
                EXACT_BOUNDED_HIT,
                GoalCapabilityUse,
                GoalPolicy,
            )

            if value["goal_implementation"] != BEHAVIOR_EXACT_BOUNDED_POLICY:
                raise ValueError(
                    "the isolated Behavior worker reports another bounded-hit "
                    "implementation")
            if goal_policy is None:
                # This is the policy frozen by the pre-generation catalogue check
                # above, not a reconstruction from the worker's complete manifest.
                goal_policy = derived_policy
            if type(goal_policy) is not GoalPolicy:
                raise TypeError(
                    "a Behavior exact provider needs the frozen GoalPolicy")
            if goal_policy.manifest != catalogue:
                raise Refused(Refusal(
                    "behavior-exact-goal-policy-catalogue-mismatch",
                    "the exact worker and pre-generation GoalPolicy name different "
                    "catalogues"))
            if goal_capability is None:
                goal_capability = goal_policy.bind_complete_implementation(
                    EXACT_BOUNDED_HIT, BEHAVIOR_EXACT_BOUNDED_POLICY,
                    goal_policy.manifest)
            if type(goal_capability) is not GoalCapabilityUse \
                    or goal_capability.policy_digest != goal_policy.digest() \
                    or goal_capability.source_catalogue is not goal_policy.manifest:
                raise Refused(Refusal(
                    "behavior-exact-goal-capability-mismatch",
                    "the exact bounded-hit capability was not minted by the supplied "
                    "pre-generation GoalPolicy"))
            goal_capability.reconcile(
                EXACT_BOUNDED_HIT, BEHAVIOR_EXACT_BOUNDED_POLICY,
                goal_policy.manifest, tuple(goal_policy.manifest))
            catalogue = goal_policy.manifest
            if domain.artifact != context.task.realization.identity \
                    or domain.task_contract != context.task.contract.digest:
                raise ValueError(
                    "the isolated Behavior domain names another task or artifact")
            identity = value["analysis_identity"]
            if type(identity) is not str or not identity \
                    or identity != domain.artifact:
                raise ValueError(
                    "the isolated Behavior analysis identity is bound to the domain")
            evidence = value["evidence_identity"]
            if type(evidence) is not dict or set(evidence) != {
                    "representation", "encoding"} \
                    or any(type(item) is not str or not item
                           for item in evidence.values()):
                raise TypeError(
                    "the isolated Behavior evidence identities are exact strings")
            from ..core.query_telemetry import AnalysisCacheBinding

            analysis_cache = AnalysisCacheBinding.from_dict(
                value["analysis_cache"])
            if analysis_cache.semantic_instance != analysis_semantic_instance(
                    artifact=domain.artifact,
                    task_contract=domain.task_contract,
                    domain=domain.digest(), catalogue=catalogue.digest(),
                    representation=evidence["representation"],
                    encoding=evidence["encoding"]):
                raise ValueError(
                    "the isolated Behavior cache identity names another bounded "
                    "semantic instance")
            audit_plan = AuditPlan.from_dict(value["audit_plan"])
            trusted_rows = value["trusted_assumptions"]
            if type(trusted_rows) is not list:
                raise TypeError("trusted assumptions are a finite list")
            trusted = []
            for row in trusted_rows:
                if type(row) is not dict or set(row) != {
                        "code", "scope", "statement"}:
                    raise TypeError("a trusted-assumption image is complete")
                trusted.append(TrustedAssumption(
                    row["code"], row["statement"], scope=row["scope"]))
            if type(value["contract"]) is not dict \
                    or type(value["well_formed"]) is not list \
                    or any(type(item) is not str for item in value["well_formed"]) \
                    or type(value["unaccounted_goals"]) is not list \
                    or any(type(item) is not str
                           for item in value["unaccounted_goals"]) \
                    or type(value["ruled_out"]) is not dict \
                    or any(type(key) is not str or type(why) is not str
                           for key, why in value["ruled_out"].items()) \
                    or type(value["budgets"]) is not dict:
                raise TypeError("the isolated Behavior static facts are finite records")
            request_budget = value["budgets"].get("ARTIFACT_QUERIES")
            if type(request_budget) is not dict \
                    or set(request_budget) != {"kind", "value"} \
                    or request_budget.get("kind") != "declared" \
                    or type(request_budget.get("value")) is not int \
                    or request_budget["value"] < 1:
                raise TypeError(
                    "the isolated Behavior request budget is a positive declared "
                    "logical-call limit")
            request_limit = request_budget["value"]
            task_audit = _decode_audit_check(value["task_audit"])
            binding = {
                "artifact": domain.artifact,
                "task_contract": domain.task_contract,
                "horizon": domain.bound,
                "domain": domain.digest(),
                "analysis_identity": identity,
                "catalogue": catalogue.digest(),
                "admission": context.task.handoff_identity,
            }
            if reply_binding != binding:
                raise ValueError(
                    "the isolated Behavior bind envelope and value name different "
                    "identities")
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - strict bind protocol boundary
            raise Refused(context.unavailable("bind-protocol", exc)) from None

        self._domain = domain
        self._frontend_catalogue = frontend
        self._catalogue = catalogue
        self._analysis_identity = identity
        self._analysis_cache = analysis_cache
        self._binding = FrozenMap(binding)
        self._evidence = FrozenMap(evidence)
        self._audit_plan = audit_plan
        self._trusted = tuple(trusted)
        self._contract = FrozenMap(value["contract"])
        self._well_formed = tuple(value["well_formed"])
        self._unaccounted = tuple(value["unaccounted_goals"])
        self._ruled_out = FrozenMap(value["ruled_out"])
        self._budgets = FrozenMap(value["budgets"])
        self._request_limit = request_limit
        self._task_audit = task_audit
        self._goal_policy = goal_policy
        self._goal_capability = goal_capability
        self._freeze()

    def _raw_call(self, operation, *, horizon=None, binding=None,
                  complete_catalogue=None, with_binding=False, **extra):
        from ..worker import WorkerFailed, run_job_strict
        from .analysis_job import decode_analysis_reply

        horizon = horizon if horizon is not None else self._domain.bound
        if binding is None:
            binding = dict(self._binding)
            complete_catalogue = self._catalogue
        timeout_s = self._context.timeout_s
        if operation == "analyse":
            from ..analysis_budget import active_analysis

            budget = active_analysis()
            if budget is not None:
                timeout_s = budget.remaining()
            extra["analysis_wall_ms"] = max(1, int(timeout_s * 1000))
        try:
            raw = run_job_strict(
                "behavior_exact_analysis",
                self._context.payload(
                    operation, horizon, binding=binding,
                    complete_catalogue=complete_catalogue,
                    memory_environment=(
                        None if self._memory_environment is None
                        else self._memory_environment.as_dict()),
                    memory_horizon=self._memory_horizon,
                    **extra),
                timeout_s=timeout_s,
                label=f"isolated Behavior exact analysis {operation}")
            return decode_analysis_reply(
                raw, operation, expected_binding=binding,
                with_binding=with_binding)
        except WorkerFailed as exc:
            raise Refused(self._context.unavailable(
                f"analysis-{operation}", exc)) from None
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - strict job protocol boundary
            raise Refused(self._context.unavailable(
                f"analysis-{operation}", exc)) from None

    @property
    def domain(self):
        return self._domain

    def bound(self):
        from ..core.query_cache import AnalysisRequestBudget

        return BoundedInstance(
            self, self._domain, analysis_cache=self._analysis_cache,
            request_budget=AnalysisRequestBudget(self._request_limit))

    def frontend_catalogue_manifest(self):
        return self._frontend_catalogue

    def catalogue_manifest(self):
        return self._catalogue

    def goals(self):
        return dict(self._catalogue.items())

    def analysis_identity(self):
        return self._analysis_identity

    def exact_goal_implementation(self):
        from ..core.goal_provider import BEHAVIOR_EXACT_BOUNDED_POLICY

        return BEHAVIOR_EXACT_BOUNDED_POLICY

    def goal_policy(self):
        return self._goal_policy

    def goal_capability_use(self):
        return self._goal_capability

    @staticmethod
    def goal_capability_required():
        return True

    def evidence_identity(self, steps):
        if steps != self._domain.bound:
            raise ValueError(
                "the isolated Behavior evidence identity is bound to one horizon")
        return dict(self._evidence)

    def contract(self):
        return {name: list(bounds) for name, bounds in self._contract.items()}

    def audit_plan(self):
        return self._audit_plan

    def trusted_assumptions(self):
        return self._trusted

    def task_audit(self, task):
        got = getattr(task, "contract", None)
        if getattr(got, "digest", None) != self._domain.task_contract:
            return AuditCheck.inconsistent(
                "the exact Behavior and concrete result name different task contracts")
        return self._task_audit

    def well_formed(self):
        return list(self._well_formed)

    def unaccounted_goals(self):
        return list(self._unaccounted)

    def ruled_out(self):
        return dict(self._ruled_out)

    def budgets(self):
        return dict(self._budgets)

    def replay(self, rows):
        try:
            wire_rows = _wire_rows(rows)
            value = self._raw_call("replay", rows=wire_rows)
            if isinstance(value, Refusal):
                raise Refused(value)
            return _decode_replay(value)
        except Exception as exc:  # noqa: BLE001 - Replay has a typed broken branch
            return Replay(
                (), broken=f"{type(exc).__name__}: {exc}",
                steps=(len(rows) if type(rows) in (tuple, list) else None))

    def audit(self, traces):
        try:
            value = self._raw_call(
                "step-audit", traces=[
                    _wire_rows(rows, f"traces[{index}]")
                    for index, rows in enumerate(traces)
                ])
            if isinstance(value, Refusal):
                return AuditCheck.refused(value.kind, value.why)
            return _decode_audit_check(value)
        except Refused as exc:
            return AuditCheck.refused(exc.refusal.kind, exc.refusal.why)
        except Exception as exc:  # noqa: BLE001 - typed audit protocol boundary
            refusal = self._context.unavailable("analysis-step-audit", exc)
            return AuditCheck.refused(refusal.kind, refusal.why)

    def source_audit(self, traces):
        try:
            value = self._raw_call(
                "source-audit", traces=[
                    _wire_rows(rows, f"traces[{index}]")
                    for index, rows in enumerate(traces)
                ])
            if isinstance(value, Refusal):
                return AuditCheck.refused(value.kind, value.why)
            return _decode_audit_check(value)
        except Refused as exc:
            return AuditCheck.refused(exc.refusal.kind, exc.refusal.why)
        except Exception as exc:  # noqa: BLE001 - typed audit protocol boundary
            refusal = self._context.unavailable("analysis-source-audit", exc)
            return AuditCheck.refused(refusal.kind, refusal.why)

    def audit_request(self, rows, domain):
        try:
            wire_rows = _wire_rows(rows)
            value = self._raw_call(
                "audit-request", rows=wire_rows, domain=domain)
            if isinstance(value, Refusal):
                return AuditCheck.refused(value.kind, value.why)
            if type(value) is not dict or set(value) != {"kind", "value"}:
                raise TypeError("a witness-audit reply has one closed alternative")
            if value["kind"] == "audit":
                return _decode_audit_check(value["value"])
            if value["kind"] != "witness":
                raise ValueError("a witness-audit reply has a registered alternative")
            witness = AuditedWitness.from_record(value["value"])
            source = self.source_audit((wire_rows,))
            if source.status is not AuditCheckStatus.AGREED:
                return source
            return witness
        except Refused as exc:
            return AuditCheck.refused(exc.refusal.kind, exc.refusal.why)
        except Exception as exc:  # noqa: BLE001 - typed witness-audit boundary
            return AuditCheck.refused(
                "behavior-analysis-isolated-witness-audit-failed",
                "the isolated Behavior witness audit returned no canonical answer: "
                f"{type(exc).__name__}: {exc}")

    def feasible(self, steps, timeout_ms=None):
        if steps != self._domain.bound:
            raise ValueError("the isolated feasibility query is bound to one horizon")
        try:
            value = self._raw_call("feasible", timeout_ms=timeout_ms)
            if isinstance(value, Refusal):
                raise Refused(value)
            if type(value) is not dict or set(value) != {
                    "answer", "detail", "witness", "telemetry"}:
                raise TypeError(
                    "an isolated feasibility answer has its complete fields")
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
                raise ValueError(
                    "an unresolved/infeasible answer carries only its reason")
            return FeasibilityCall(
                answer, value["detail"],
                telemetry=(
                    None if value["telemetry"] is None else
                    QueryTelemetry.from_dict(value["telemetry"])))
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - strict job protocol boundary
            raise Refused(self._context.unavailable(
                "analysis-feasible", exc)) from None

    def query(self, goals, steps, timeout_ms=None):
        if steps != self._domain.bound:
            raise ValueError("the isolated goal query is bound to one horizon")
        try:
            value = self._raw_call(
                "query", goals=list(goals), timeout_ms=timeout_ms)
            if isinstance(value, Refusal):
                raise Refused(value)
            return Verdict.from_record(value)
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - strict job protocol boundary
            raise Refused(self._context.unavailable(
                "analysis-query", exc)) from None

    def analyse(self, evaluation, *, feasibility_timeout_ms=None,
                query_timeout_ms=None, batch=None):
        """Run the complete post-score coordinator in one isolated exact job.

        Lowering and position planning dominate large Behavior tasks.  Sending each
        record audit, feasibility question and goal query through a fresh job repeated
        that identical work.  This operation crosses the same strict JSON boundary but
        keeps one exact instance for the whole analysis; the returned record is decoded
        through every public evidence constructor before it is accepted.
        """

        from ..core.outcome import AnalysisOutcome

        try:
            value = self._raw_call(
                "analyse", evaluation=_analysis_subject_image(evaluation),
                feasibility_timeout_ms=feasibility_timeout_ms,
                query_timeout_ms=query_timeout_ms, batch=batch)
            if isinstance(value, Refusal):
                raise Refused(value)
            outcome = AnalysisOutcome.from_dict(value)
            if outcome.concrete != evaluation.fingerprint:
                raise ValueError(
                    "the isolated analysis answer names another concrete result")
            return outcome
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - strict analysis protocol boundary
            raise Refused(self._context.unavailable(
                "analysis-complete", exc)) from None

    def audit_record(self, result, task):
        if type(result) is not RunResult:
            return RecordAudit.refused(
                "behavior-record-not-a-run-result",
                f"the concrete entry is a {type(result).__name__}, not a RunResult")
        if not result.ran:
            return RecordAudit.refused(
                "behavior-record-run-unavailable",
                f"the raw run has no trace: {result.run_failure or 'no reason recorded'}")
        if task is None or getattr(getattr(task, "contract", None), "digest", None) \
                != self._domain.task_contract:
            return RecordAudit.refused(
                "behavior-record-task-unbound",
                "the record audit was not bound to the concrete TaskContract")
        try:
            value = self._raw_call(
                "record-audit", stimulus=result.stimulus.as_dict(),
                raw_trace=result.trace.as_dict())
            if isinstance(value, Refusal):
                return RecordAudit.refused(value.kind, value.why, **value.detail)
            return _decode_record_audit(value)
        except Refused as exc:
            return RecordAudit.refused(exc.refusal.kind, exc.refusal.why, **exc.refusal.detail)
        except Exception as exc:  # noqa: BLE001 - typed record-audit boundary
            return RecordAudit.refused(
                "behavior-analysis-isolated-record-audit-failed",
                "the isolated Behavior record audit returned no canonical answer: "
                f"{type(exc).__name__}: {exc}")

    @property
    def source(self):
        """The submitted source stays private to the job context."""

        return None
