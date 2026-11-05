"""Bounded scope reopening around the existing construction and scope runners.

This supervisor never approves publication or changes a previous judgment. Each
reopening starts a new independently reviewed scope and a fresh task lineage.
Role execution, cleanup, retries and provider accounting remain in shared code.
"""

from dataclasses import dataclass, replace

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.journal import load_chain
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.agent.trajectory import CostLedger
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version

from .drafts import load_scope, scope_closure
from .escalation import ScopeEscalation
from .process_evidence import synthesis_billing, synthesis_costs
from .release_records import ReviewPackage
from .scope import ScopeConfiguration, ScopePlan, load_scope_preparation, prepare_scope
from .synthesis import SynthesisConfiguration, seal_review_package, synthesize
from .synthesis_records import SynthesisPlan, SynthesisResult


@dataclass(frozen=True, slots=True)
class ConstructionPlan:
    run_id: str
    initial: SynthesisPlan
    scope_review: ScopePlan | None
    max_reopens: int = defaults.SYN_MAX_REOPENS

    def __post_init__(self):
        text(self.run_id, "construction run id")
        if type(self.initial) is not SynthesisPlan or self.initial.processes is None:
            raise TypeError("construction supervisor requires fixed process roles")
        if self.scope_review is not None and type(self.scope_review) is not ScopePlan:
            raise TypeError("construction supervisor requires fixed scope-review roles")
        if type(self.max_reopens) is not int or self.max_reopens < 0:
            raise ValueError("scope reopening budget must be nonnegative")

    def to_bytes(self):
        return canonical_json({"schema": f"behave.construction-plan/{2 if self.scope_review is None else 1}", "run_id": self.run_id,
            "initial": parse_json(self.initial.to_bytes()), "scope_review": None if self.scope_review is None else parse_json(self.scope_review.to_bytes()),
            "max_reopens": self.max_reopens})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "run_id", "initial", "scope_review", "max_reopens"})
        version(row["schema"], f"behave.construction-plan/{2 if row['scope_review'] is None else 1}")
        result = cls(row["run_id"], SynthesisPlan.from_bytes(canonical_json(row["initial"])),
                     None if row["scope_review"] is None else ScopePlan.from_bytes(canonical_json(row["scope_review"])), row["max_reopens"])
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "construction plan must be canonical")
        return result


def _scope_plan(plan, round_index, synthesis_result, objects):
    request = ScopeEscalation.from_bytes(read_verified(objects, synthesis_result.escalation))
    previous = SynthesisPlan.from_bytes(read_verified(objects, synthesis_result.plan))
    if (request.task_id, request.scope, request.parent) != (previous.task_id, previous.scope,
            synthesis_result.drafts[-1] if synthesis_result.drafts else None):
        raise RecordError("construction-escalation-mismatch", "request differs from the completed construction")
    scope, _, _ = load_scope(objects, request.scope)
    if (scope.source, scope.selection, scope.brief) != (plan.scope_review.author.inputs.source,
            plan.scope_review.author.inputs.selection, plan.scope_review.author.inputs.brief):
        raise RecordError("construction-source-mismatch", "scope review cannot change the selected source")
    inputs = replace(plan.scope_review.author.inputs, parent=request.scope,
                     feedback=request.to_bytes().decode())
    return replace(plan.scope_review, run_id=f"{plan.run_id}-scope{round_index}",
                   author=replace(plan.scope_review.author, inputs=inputs))


def construct(*, plan: ConstructionPlan, reader, run: JournalDirectory, control=None,
              model_pool: ResourcePool | None = None, check_pool: ResourcePool | None = None,
              development_pool: ResourcePool | None = None, continuation_from=None) -> dict:
    """Execute/recover a finite construction graph; return a review package only.

    Existing terminal child runs are validated and reused. Uncertain ownership
    propagates without a terminal supervisor record, so it cannot authorize a
    new scope or a duplicate provider call.
    """
    if type(plan) is not ConstructionPlan or type(run) is not JournalDirectory:
        raise TypeError("construct requires ConstructionPlan and JournalDirectory")
    with run.owner():
        objects = run.objects
        tape = Tape(run.journal("construction"))
        ref = objects.put(plan.to_bytes())
        configuration = {"plan": ref.as_dict(), **{name: None if pool is None else pool.policy.as_dict()
            for name, pool in (("model_pool", model_pool), ("check_pool", check_pool),
                               ("development_pool", development_pool))}}
        if continuation_from is not None:
            configuration["continuation_from"] = continuation_from.as_dict()
        if not tape.events:
            scope, _, _ = load_scope(reader, plan.initial.scope)
            if plan.scope_review is not None and (scope.source, scope.selection, scope.brief) != (plan.scope_review.author.inputs.source,
                    plan.scope_review.author.inputs.selection, plan.scope_review.author.inputs.brief):
                raise RecordError("construction-source-mismatch", "scope policy targets a different source")
            refs = (*scope_closure(reader, plan.initial.scope), plan.initial.judge_policy,
                    *((plan.scope_review.author.inputs.guidance,) if plan.scope_review is not None else ()))
            objects.publish(reader, refs)
            start = tape.record("construction_started", configuration, (ref, *refs))
        else:
            start = tape.record("construction_started", configuration)
        objects.put(start.to_bytes())  # Prefix export also needs the root event, not only its children.
        histories, bills = [], []
        stage_prefix = None

        def retain(child, stage):
            nonlocal stage_prefix
            head, closure = child.seal(stage)
            objects.publish(child.objects, closure)
            event = tape.record("construction_stage", {"stage": stage, "head": head.as_dict()}, closure)
            stage_prefix = objects.put(event.to_bytes())
            histories.append({"stage": stage, "head": head.as_dict()})
            return head

        def finish(status, code, package=None):
            bill = CostLedger.combine(tuple(bills))
            result = {"schema": "behave.construction-result/1", "plan": ref.as_dict(),
                "status": status, "code": code, "stages": histories,
                "package": None if package is None else package.as_dict(),
                "cost_by_currency": thaw_json(bill.cost_by_currency()),
                "unpriced_dispatches": bill.unpriced_dispatches, "unresolved_dispatches": bill.unresolved_dispatches}
            result_ref = objects.put(canonical_json(result))
            tape.record("construction_finished", {"result": result_ref.as_dict()},
                        (result_ref, *(() if package is None else (package,))))
            tape.finish()
            return result

        current = plan.initial
        for index in range(plan.max_reopens + 1):
            child = JournalDirectory(run.directory / "runs" / f"task_{index}")
            result = synthesize(plan=current, reader=reader if index == 0 else objects, run=child,
                                control=control, model_admission=model_pool, check_admission=check_pool,
                                development_admission=development_pool,
                                continuation_from=continuation_from if index == 0 else None)
            head = retain(child, "synthesis")
            bills.append(synthesis_costs(objects, head))
            if result.status != "scope_review_requested":
                base = ReviewPackage.from_bytes(read_verified(child.objects, seal_review_package(child)))
                package = objects.put(replace(base, construction_prefix=stage_prefix).to_bytes())
                # The live owner just committed this prefix and collected its
                # child billing. Admission/offline readers verify the exported
                # lineage; do not reread every role transcript here a second time.
                from .rtl import construction_status

                status, code = construction_status(objects, result)
                return finish(status, code, package)
            if index == plan.max_reopens:
                return finish("scope_reopen_limit", "scope-reopening-budget-exhausted")
            if plan.scope_review is None:
                return finish("unfinished", "builder-objective-unresolved")
            preparation = _scope_plan(plan, index + 1, result, objects)
            child = JournalDirectory(run.directory / "runs" / f"scope_{index + 1}")
            prepared = prepare_scope(plan=preparation, reader=objects, run=child, control=control, model_pool=model_pool)
            head = retain(child, "scope")
            verified, ledger = load_scope_preparation(objects, head)
            if prepared != verified:
                raise RecordError("construction-scope-mismatch", "scope result differs from its sealed evidence")
            bills.append(ledger)
            if prepared.status != "review_ready":
                return finish("scope_" + prepared.status, prepared.code)
            current = replace(plan.initial, run_id=f"{plan.run_id}-task{index + 1}",
                              scope=prepared.scopes[-1], preparation=head)
        raise AssertionError("bounded construction loop must terminate")


@dataclass(frozen=True, slots=True)
class ConstructionConfiguration:
    run_id: str
    synthesis: SynthesisConfiguration
    scope: ScopeConfiguration | None
    max_reopens: int = defaults.SYN_MAX_REOPENS

    def __post_init__(self):
        if type(self.synthesis) is not SynthesisConfiguration or (self.scope is not None and type(self.scope) is not ScopeConfiguration):
            raise TypeError("construction requires synthesis and scope configurations")
        if self.scope is not None and self.synthesis.model_admission != self.scope.model_admission:
            raise ValueError("construction stages must share their model admission policy")
        _ = self.plan  # Validate the composed plan without executing either stage.

    @property
    def plan(self):
        return ConstructionPlan(self.run_id, self.synthesis.plan,
                                None if self.scope is None else self.scope.plan, self.max_reopens)

    def to_bytes(self):
        return canonical_json({"schema": f"behave.construction-configuration/{2 if self.scope is None else 1}", "run_id": self.run_id,
            "synthesis": parse_json(self.synthesis.to_bytes()), "scope": None if self.scope is None else parse_json(self.scope.to_bytes()),
            "max_reopens": self.max_reopens})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "run_id", "synthesis", "scope", "max_reopens"})
        version(row["schema"], f"behave.construction-configuration/{2 if row['scope'] is None else 1}")
        result = cls(row["run_id"], SynthesisConfiguration.from_bytes(canonical_json(row["synthesis"])),
                     None if row["scope"] is None else ScopeConfiguration.from_bytes(canonical_json(row["scope"])), row["max_reopens"])
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "construction configuration must be canonical")
        return result

    def preflight(self, reader):
        self.synthesis.preflight(reader)
        if self.scope is None:
            return
        self.scope.preflight(reader)
        proposal, _, _ = load_scope(reader, self.synthesis.plan.scope)
        if (proposal.source, proposal.selection, proposal.brief) != (self.scope.plan.author.inputs.source,
                self.scope.plan.author.inputs.selection, self.scope.plan.author.inputs.brief):
            raise RecordError("construction-source-mismatch", "scope review targets a different source")

    def pools(self):
        pools = self.synthesis.pools()
        return {name: pools[source] for name, source in (("model_pool", "model_admission"),
            ("check_pool", "check_admission"), ("development_pool", "development_admission"))}


def _require(condition, message):
    if not condition:
        raise RecordError("construction-evidence-mismatch", message)


def _record(reader, ref):
    return read_verified(reader, ref)


def verify_construction_prefix(reader, head, *, synthesis_head):
    """Bind each new scope to the preceding request, and every child to its plan.

    The prefix ends immediately before package creation, avoiding a content-hash
    cycle. Its last construction must be the package's exact synthesis head.
    Admission still checks final source, reader and concrete evidence separately.
    """
    events = load_chain(reader, head)
    _require(len(events) >= 2 and events[0].kind == "construction_started", "missing construction history")
    config = fields(thaw_json(events[0].payload), {"plan", "model_pool", "check_pool", "development_pool"}
                    | ({"continuation_from"} if "continuation_from" in events[0].payload else set()))
    pools = {key: None if config[key] is None else AdmissionPolicy(**fields(
        config[key], set(AdmissionPolicy.__dataclass_fields__))).identity
        for key in ("model_pool", "check_pool", "development_pool")}
    plan = ConstructionPlan.from_bytes(_record(reader, BlobRef.from_dict(config["plan"])))
    _require(len(events) <= 2 + 2 * plan.max_reopens, "construction exceeds reopening budget")
    current, previous, ledgers = plan.initial, None, []
    expected_scope = None
    for index, event in enumerate(events[1:]):
        _require(event.kind == "construction_stage", "unexpected construction transition")
        row = fields(thaw_json(event.payload), {"stage", "head"})
        stage_head = BlobRef.from_dict(row["head"])
        _require(row["stage"] == ("synthesis" if index % 2 == 0 else "scope"), "construction stage order changed")
        child = load_chain(reader, stage_head)
        _require(bool(child), "construction stage has no history")
        child_config = parse_json(_record(reader, BlobRef.from_dict(fields(
            thaw_json(child[0].payload), {"configuration"})["configuration"])))
        _require(child_config["model_admission"] == pools["model_pool"], "model admission policy changed across stages")
        if row["stage"] == "synthesis":
            _require(child_config.get("continuation_from") == (config.get("continuation_from") if index == 0 else None),
                     "construction resumed another synthesis run")
            _require(child_config["check_admission"] == pools["check_pool"]
                     and child_config["development_admission"] == pools["development_pool"],
                     "verification/development policy changed across stages")
            _require(child and child[-1].kind == "synthesis_finished", "construction stage did not finish")
            result = SynthesisResult.from_bytes(_record(reader, BlobRef.from_dict(
                fields(thaw_json(child[-1].payload), {"result"})["result"])))
            _require(result.plan == BlobRef.of(current.to_bytes()), "construction stage changed its plan")
            ledgers.append(synthesis_billing(reader, stage_head))
            previous = result
            if index < len(events) - 2:
                _require(result.status == "scope_review_requested", "scope was reopened without an author request")
                expected_scope = _scope_plan(plan, index // 2 + 1, result, reader)
            else:
                _require(stage_head == synthesis_head and result.status != "scope_review_requested",
                         "package does not correspond to the last completed construction")
        else:
            _require(previous is not None and expected_scope is not None, "scope review lacks an escalation")
            result, ledger = load_scope_preparation(reader, stage_head)
            _require(result.plan == BlobRef.of(expected_scope.to_bytes()) and result.status == "review_ready",
                     "new construction needs this escalation's independently reviewed scope")
            ledgers.append(ledger)
            current = replace(plan.initial, run_id=f"{plan.run_id}-task{index // 2 + 1}",
                              scope=result.scopes[-1], preparation=stage_head)
    _require(events[-1].payload["stage"] == "synthesis", "review package cannot end at scope review")
    refs = tuple(dict.fromkeys(ref for event in events for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
    return CostLedger.combine(tuple(ledgers)), refs
