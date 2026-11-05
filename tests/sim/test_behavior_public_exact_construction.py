"""T3-09/T3-16 public Behavior exact construction through named jobs only."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.behavior import instance as behavior_instance
from behave.sim.behavior.construction import clear_construction_cache
from behave.sim.behavior.task_boundary import lower_task_behavior
from behave.sim.core.instance import BoundedInstance
from behave.sim.core.query_cache import clear_analysis_answer_cache
from behave.sim.core.query_telemetry import (
    CacheDisposition,
    analysis_semantic_instance,
)
from behave.sim.core.schema import (
    Authority,
    Reading,
    TransactionSchema,
)
from behave.sim.core.schema import Endpoint as SchemaEndpoint
from tests.support.reference import load as load_test_reference

from .test_command_handler import _BaselineOnly
from .test_multi_endpoint_evaluation import (
    _contract as _endpoint_product_contract,
)
from .test_multi_endpoint_evaluation import (
    _realization as _endpoint_product_realization,
)
from .test_multi_endpoint_evaluation import (
    _reference as _endpoint_product_reference,
)

SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        if a:
            return {"y": 1}
        return {"y": 0}
"""

STRAIGHT_LINE_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a}
"""

NATIVE_ONLY_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        try:
            y = a
        except Exception:
            y = 0
        return {"y": y}
"""

MULTI_OUTPUT_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        if a:
            return [{"y": 1}, {"y": 0}]
        return [{"y": 0}, {"y": 1}]
"""

REPEATED_MULTI_OUTPUT_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        row = {"y": a}
        return [row, row]
"""

PARTIAL_FAILURE_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": 1 // a}
"""

INVERTED_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        if a:
            return {"y": 0}
        return {"y": 1}
"""

MULTI_ENDPOINT_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, left_data, right_data):
        if left_data & 1:
            left_result_data = (left_data + 1) & 255
        else:
            left_result_data = (left_data + 1) & 255
        return {
            "left_result_data": left_result_data,
            "right_result_data": (right_data + 2) & 255,
        }
"""

STATE_PROVED_OPTION_SOURCE = """\
class M:
    def reset(self):
        self.valid = 0
        self.data = 0

    def process(self, a):
        result = {"y": self.data} if self.valid else None
        self.valid = 1
        self.data = a
        return result
"""


def _schema():
    return TransactionSchema(
        [SchemaEndpoint("a", "input", 1),
         SchemaEndpoint("y", "output", 1)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )


def _contract(*, answers_per_transaction=1):
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", api.FiniteType(1), "a")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", api.FiniteType(1), "y")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=answers_per_transaction),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
    )


def _task(identity, *, reference=None, answers_per_transaction=1):
    ports = (
        api.Port("a", 1, driven_by="environment", clock="clk"),
        api.Port("y", 1, driven_by="artifact", clock="clk"),
        api.Port("in_valid", 1, driven_by="environment"),
        api.Port("in_ready", 1, driven_by="artifact"),
        api.Port("out_valid", 1, driven_by="artifact"),
        api.Port("out_ready", 1, driven_by="environment"),
        api.Port("clk", 1, driven_by="environment"),
        api.Port("rst", 1, driven_by="environment"),
    )
    return api.admit(
        _contract(answers_per_transaction=answers_per_transaction),
        api.Realization(
            "behavior", ports,
            channel_ports={"in": ("a",), "out": ("y",)},
            identity=identity),
        reference=reference)


def _generation(contract):
    return api.GenerationSpec(
        horizon=1, queue_bound=1,
        reset_hold_cycles=0,
        drain=0)


def test_one_step_task_boundary_omits_a_state_proved_absent_output():
    behavior, _boundary = lower_task_behavior(
        STATE_PROVED_OPTION_SOURCE, "stable/behavior.py", _contract(), 1)

    replay = behavior.replay([{"a": 1}])
    assert not replay.broken
    assert not replay.error
    assert [tuple(output.transactions) for output in replay.outputs] == [()]


def _reference(source=SOURCE):
    return load_test_reference(
        {"behavior.py": source.encode("utf-8")},
        config={"class": "M"}, loader="public Behavior exact fixture")


def _run_public_behavior_command(
        tmp_path, source=SOURCE, *, reference_source=None,
        answers_per_transaction=1):
    path = tmp_path / "candidate.py"
    path.write_text(source)
    identity = behavior_instance.lower(
        source, _schema(), file="stable/behavior.py", bound=1).identity
    clear_construction_cache()
    reference = _reference(source if reference_source is None else reference_source)
    task = _task(
        identity,
        reference=reference,
        answers_per_transaction=answers_per_transaction,
    )
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(
            path, file="stable/behavior.py", timeout_s=90),
        _generation(task.contract),
        bounded_analysis=True,
    )
    request = handle.command_request(
        run_directory=tmp_path / "behavior-command",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        probe_budget=0,
        run_analysis=True,
        reference=reference,
        feasibility_timeout_ms=20_000,
        query_timeout_ms=20_000,
        analysis_wall_timeout_seconds=180,
    )
    return api.handle_command(request), identity


def _transaction_records(run):
    """These fixtures emit only when their transaction input is accepted."""
    records = []
    for slot, record in zip(run.trace.stimulus.slots, run.trace.records, strict=True):
        if slot.reset:
            assert not record.accepted and not record.outputs
        elif record.accepted:
            records.append(record)
        else:
            assert not record.outputs
    assert records, "the fixture must execute, not merely reset or idle"
    return records


def test_public_behavior_command_reuses_the_score_and_analysis_path(tmp_path):
    result, identity = _run_public_behavior_command(tmp_path)

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert result.analysis is not None
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    assert all(
        run.trace.artifact == identity
        for run in result.evaluation.results
    )
    assert all(
        dict(record.outputs).get("out") in (((0,),), ((1,),))
        for run in result.evaluation.results
        for record in _transaction_records(run)
    )
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["evaluation"] == \
        json.loads(json.dumps(result.evaluation.as_evidence_dict()))
    assert persisted["report"]["analysis"] == \
        json.loads(json.dumps(result.analysis.as_dict()))


@pytest.mark.parametrize("clocked", [False, True], ids=["settled", "sampled-edge"])
def test_public_behavior_analysis_does_not_require_rtl_pin_observations(tmp_path, clocked):
    adapter = api.sampled_edge if clocked else api.settled
    clock = "clk" if clocked else None
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint("in", api.Direction.IN,
                         [api.Field("a", api.FiniteType(1), "a")],
                         adapter(api.Direction.IN), clock=clock, kind=api.Kind.TRANSACTION),
            api.Endpoint("out", api.Direction.OUT,
                         [api.Field("y", api.FiniteType(1), "y")],
                         adapter(api.Direction.OUT), clock=clock, kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")] if clocked else [],
    )
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    reference = _reference(SOURCE)
    realization = api.realization_of_behavior_source(
        SOURCE, contract, file="stable/plain.py", model="M")
    task = api.admit(contract, realization, reference=reference)
    hold = max((item.reset_hold_cycles for item in contract.clocks), default=0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="stable/plain.py"),
                                  api.GenerationSpec(hold + 2, 2, hold, 0),
                                  bounded_analysis=True)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=2),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True,
        analysis_wall_timeout_seconds=180))

    assert result.evaluation.passed, result.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert set(result.analysis.per_goal) == set(handle.frontend_catalogue)
    assert result.analysis.concrete == result.evaluation.fingerprint
    records = [record for run in result.evaluation.results
               for record in _transaction_records(run)]
    assert records and all(dict(record.outputs)["out"] for record in records)
    # The concrete stream exists without a physical y pin. RTL's audit must
    # still reject that very same record; only Behavior uses output batches.
    from behave.sim.core.analysis import audit_environment_assumptions

    run = result.evaluation.results[0]
    pin_audit = audit_environment_assumptions(contract, run.trace.stimulus, run.trace)
    assert pin_audit.kind == "driver-output-unreadable", (pin_audit.disposition, pin_audit.kind, pin_audit.why)


def test_production_behavior_replay_records_a_partial_failure_and_pads(tmp_path):
    """Concrete replay and exact lowering name the same source safety occurrence."""

    path = tmp_path / "candidate.py"
    path.write_text(PARTIAL_FAILURE_SOURCE)
    lowered = behavior_instance.lower(
        PARTIAL_FAILURE_SOURCE, _schema(), file="stable/behavior.py", bound=1)
    task = _task(lowered.identity)
    generation = api.GenerationSpec(
        horizon=2, queue_bound=1, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(path, file="stable/behavior.py", timeout_s=90),
        generation,
        bounded_analysis=False,
    )
    stimulus = api.Stimulus(
        {"in": ((0,),)},
        (api.EventSlot(
             offers=("in",), controls={"rst": 0, "out_ready": 1}),
         api.EventSlot(controls={"rst": 0, "out_ready": 1})),
        source="partial-operation witness",
    )

    trace = handle._artifact.trace(task.contract, stimulus)
    safety = tuple(
        goal.id for goal in handle.frontend_catalogue.values()
        if goal.cls == "safety")
    exact_safety = tuple(
        goal.id for goal in lowered.frontend_catalogue_manifest().values()
        if goal.cls == "safety")
    assert len(safety) == 1
    assert safety == exact_safety
    assert trace.completion is api.Completion.PADDED
    assert len(trace.records) == 2
    assert dict(trace.records[0].accepted) == {"in": (0,)}
    assert dict(trace.records[0].hits)[safety[0]] is True
    assert dict(trace.records[0].monitor) == {"terminal_failure": 1}
    assert dict(trace.records[1].monitor) == {"padding": 1}
    assert not any(reached for _goal, reached in trace.records[1].hits)


def test_public_score_keeps_the_behavior_failure_goal(tmp_path):
    path = tmp_path / "candidate.py"
    path.write_text(PARTIAL_FAILURE_SOURCE)
    lowered = behavior_instance.lower(
        PARTIAL_FAILURE_SOURCE, _schema(), file="stable/behavior.py", bound=1)
    reference = _reference(STRAIGHT_LINE_SOURCE)
    task = _task(lowered.identity, reference=reference)
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(path, file="stable/behavior.py", timeout_s=90),
        _generation(task.contract),
        bounded_analysis=False,
    )
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "partial-failure-command",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        probe_budget=0,
        run_analysis=False,
        reference=reference,
    ))

    safety = tuple(
        goal.id for goal in handle.frontend_catalogue.values()
        if goal.cls == "safety")
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert len(safety) == 1 and safety[0] in result.evaluation.safety_hits
    assert not result.evaluation.passed
    assert any(run.trace.completion is api.Completion.PADDED
               for run in result.evaluation.results)


def test_public_analysis_classifies_a_concrete_behavior_failure(tmp_path):
    result, _identity = _run_public_behavior_command(
        tmp_path,
        source=PARTIAL_FAILURE_SOURCE,
        reference_source=STRAIGHT_LINE_SOURCE,
    )

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    assert len(result.evaluation.safety_hits) == 1
    assert not result.evaluation.passed
    failure = next(iter(result.evaluation.safety_hits))
    assert result.analysis.per_goal[failure]["status"] == "reached"


def test_public_behavior_command_accepts_an_explicit_smaller_analysis_bound(
        tmp_path):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(
        SOURCE, _schema(), file="stable/behavior.py", bound=1).identity
    clear_construction_cache()
    reference = _reference()
    task = _task(identity, reference=reference)
    generation = api.GenerationSpec(
        horizon=3, queue_bound=3, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(path, file="stable/behavior.py", timeout_s=90),
        generation,
        bounded_analysis=True,
        analysis_bound=1,
    )

    exact = handle.bind()
    assert exact.domain.bound == 1
    request = handle.command_request(
        run_directory=tmp_path / "bounded-command",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        probe_budget=0,
        run_analysis=True,
        reference=reference,
        feasibility_timeout_ms=20_000,
        query_timeout_ms=20_000,
        analysis_wall_timeout_seconds=180,
    )
    assert request.analysis_bound == 1

    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    assert result.analysis.concrete == result.evaluation.fingerprint


@pytest.mark.parametrize("analysis_bound", [0, True, 4])
def test_public_behavior_refuses_an_invalid_analysis_bound(
        tmp_path, analysis_bound):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(
        SOURCE, _schema(), file="stable/behavior.py", bound=1).identity
    clear_construction_cache()
    task = _task(identity)
    generation = api.GenerationSpec(
        horizon=3, queue_bound=3, reset_hold_cycles=0, drain=0)

    with pytest.raises(ValueError, match="analysis bound"):
        api.prepare_behavior(
            task, api.BehaviorConfig(path, timeout_s=90), generation,
            bounded_analysis=True, analysis_bound=analysis_bound)


def test_analysis_bound_is_not_a_score_only_option(tmp_path):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(
        SOURCE, _schema(), file="stable/behavior.py", bound=1).identity
    clear_construction_cache()
    task = _task(identity)

    with pytest.raises(ValueError, match="requires bounded_analysis"):
        api.prepare_behavior(
            task, api.BehaviorConfig(path, timeout_s=90),
            _generation(task.contract), analysis_bound=1)


@pytest.mark.parametrize("bound", [None, 100])
@pytest.mark.parametrize("horizon, hold, drain", [(2, 0, 0), (205, 3, 200)])
def test_transaction_analysis_limit_does_not_count_reset_drain_or_rtl_slots(
        tmp_path, bound, horizon, hold, drain):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    reference = _reference()
    contract = _contract()
    task = api.admit(contract, api.realization_of_behavior_source(
        SOURCE, contract, model="M"), reference=reference)
    generation = api.GenerationSpec(horizon, 100, hold, drain)
    assert generation.max_body_steps == 2  # Still bounds stimulus construction.
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"),
        generation, bounded_analysis=True, analysis_bound=bound)
    command = handle.command_request(run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1), reference=reference,
        run_analysis=True, analysis_wall_timeout_seconds=180,
        search_policy=api.SearchPolicy(time_budget_s=0))
    assert command.analysis_bound == 100
    assert all(seed.depth("in") == 100 for seed in command.baseline.stimuli)
    request = command.as_dict()["analysis"]["request"]
    assert request["analysis_bound"] == 100
    assert request["analysis_bound_unit"] == "transaction-batches"
    assert request["generation_spec"] == generation.as_dict()
    with pytest.raises(ValueError, match="queue_bound"):
        api.prepare_behavior(task, api.BehaviorConfig(path, model="M"),
            generation, bounded_analysis=True, analysis_bound=101)


def test_native_behavior_scores_when_bounded_lowering_is_unsupported(tmp_path):
    path = tmp_path / "native_only.py"
    path.write_text(NATIVE_ONLY_SOURCE)
    reference = _reference(NATIVE_ONLY_SOURCE)
    realization = api.realization_of_behavior_source(
        NATIVE_ONLY_SOURCE, _contract(), file=path.name)
    task = api.admit(_contract(), realization, reference=reference)
    generation = _generation(task.contract)

    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file=path.name, timeout_s=90), generation)
    with pytest.raises(api.Refused) as unavailable:
        handle.bind()
    assert unavailable.value.refusal.kind == \
        "behavior-bounded-analysis-not-prepared"

    scored = api.handle_command(handle.command_request(
        run_directory=tmp_path / "native-score",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0,
        run_analysis=False, reference=reference))
    assert scored.status is api.CommandStatus.EVALUATED
    assert scored.evaluation.passed
    assert scored.analysis is None

    # Local guidance is ancillary.  Both source-guided directions may be unavailable
    # for a source outside the bounded frontend, but their typed refusals must leave the
    # canonical baseline available to the concrete scorer.
    providers = handle.generation_providers(
        reference, workdir=tmp_path / "native-generation")
    production = api.handle_command(handle.command_request(
        run_directory=tmp_path / "native-production-score",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=providers, probe_budget=0,
        run_analysis=False, reference=reference))
    assert production.status is api.CommandStatus.EVALUATED
    assert production.evaluation.passed
    assert len(production.generated.searches) == 2
    assert all(not search.generated for search in production.generated.searches)

    requested = api.handle_command(handle.command_request(
        run_directory=tmp_path / "native-score-with-analysis",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0,
        run_analysis=True, reference=reference,
        analysis_wall_timeout_seconds=180))
    assert requested.status is api.CommandStatus.EVALUATED
    assert requested.evaluation.passed
    assert requested.analysis.verdict is api.Verdict.REFUSED

    bounded = api.prepare_behavior(
        task, api.BehaviorConfig(path, file=path.name, timeout_s=90),
        generation, bounded_analysis=True)
    analysed = api.handle_command(bounded.command_request(
        run_directory=tmp_path / "native-bounded-score",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0,
        run_analysis=True, reference=reference,
        analysis_wall_timeout_seconds=180))

    assert analysed.status is api.CommandStatus.EVALUATED
    assert analysed.evaluation.passed
    assert analysed.analysis.verdict is api.Verdict.REFUSED
    assert analysed.analysis.evidence["refusal"]["kind"] == \
        "statement-unsupported"
    persisted = json.loads(Path(analysed.record_path).read_text())
    assert persisted["authority"]["evaluation"] == \
        json.loads(json.dumps(analysed.evaluation.as_evidence_dict()))


POSITIONED_SHIFT_SOURCE = """\
from fp_lib import fp32_add

class M:
    def reset(self):
        pass
    def process(self, a, b):
        return {"y": fp32_add(a, b)}
"""


def test_step_audit_uses_the_prepared_position_plan(monkeypatch):
    from behave.sim.behavior import audit
    from behave.sim.symbolic import encoder

    schema = TransactionSchema(
        [SchemaEndpoint("a", "input", 32),
         SchemaEndpoint("b", "input", 32),
         SchemaEndpoint("y", "output", 32)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )
    instance = behavior_instance.lower(
        POSITIONED_SHIFT_SOURCE, schema, file="positioned-shift.py", bound=1)
    prepared = instance.bind(1).instance._position_plan
    trace = [[{"a": a, "b": b}] for a, b in (
        (0, 0), (0x80000000, 0), (0x3f800000, 0x3f800000),
        (0x3f800000, 0xbf800000), (1, 1), (0x7f7fffff, 0x7f7fffff),
        (0x7f800000, 0xff800000), (0x7fc00000, 0x3f800000),
    )]
    assert audit.step_audit(instance.proc, instance.env, trace, workers=1) == []

    raw = prepared.checked_for(instance.proc, instance.env, prepared.bound)
    original = encoder._encode_derived_step
    built = []

    def checked(*args, **kwargs):
        assert kwargs["plan"] is raw.steps[0]
        assert kwargs["occurrence_carriers"] is raw.occurrence_layers[0]
        built.append(kwargs["tag"])
        return original(*args, **kwargs)

    def unexpected_standalone(*_args, **_kwargs):
        pytest.fail("a prepared audit rebuilt a standalone encoding")

    monkeypatch.setattr(audit, "encode_step", unexpected_standalone)
    monkeypatch.setattr(encoder, "_encode_derived_step", checked)
    assert audit.step_audit(
        instance.proc, instance.env, trace, workers=1,
        position_plan=prepared) == []
    assert built == ["@step-audit-0"]
    with pytest.raises(TypeError, match="either one test encoding"):
        audit.step_audit(
            instance.proc, instance.env, trace, encoding=object(),
            position_plan=prepared)
    with pytest.raises(ValueError, match="prepared plan covers 1"):
        audit.step_audit(
            instance.proc, instance.env,
            [[{"a": 0, "b": 0}, {"a": 1, "b": 1}]], workers=1,
            position_plan=prepared)


def test_bound_step_audits_reuse_representation_but_not_results(monkeypatch):
    from behave.sim.symbolic import encoder

    schema = TransactionSchema(
        [SchemaEndpoint("a", "input", 32),
         SchemaEndpoint("b", "input", 32),
         SchemaEndpoint("y", "output", 32)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )
    behavior = behavior_instance.lower(
        POSITIONED_SHIFT_SOURCE, schema, file="positioned-shift.py", bound=1)
    exact = behavior.bind(1).instance
    original = encoder._encode_derived_step
    built = []

    def counted(*args, **kwargs):
        built.append(kwargs.get("tag"))
        return original(*args, **kwargs)

    monkeypatch.setattr(encoder, "_encode_derived_step", counted)
    traces = ([{"a": 0, "b": 0}],)

    assert exact.audit(traces).status.value == "agreed"
    assert exact.audit(traces).status.value == "agreed"
    assert built == ["@step-audit-0"]


def test_stateless_step_audit_extends_its_prefix_without_reencoding(monkeypatch):
    from behave.sim.symbolic import encoder

    schema = TransactionSchema(
        [SchemaEndpoint("a", "input", 32),
         SchemaEndpoint("b", "input", 32),
         SchemaEndpoint("y", "output", 32)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )
    behavior = behavior_instance.lower(
        POSITIONED_SHIFT_SOURCE, schema, file="positioned-prefix.py", bound=3)
    exact = behavior.bind(3).instance
    original = encoder._encode_derived_step
    built = []

    def counted(*args, **kwargs):
        built.append(kwargs.get("tag"))
        return original(*args, **kwargs)

    monkeypatch.setattr(encoder, "_encode_derived_step", counted)
    one = ([{"a": 0, "b": 0}],)
    two = ([{"a": 0, "b": 0}, {"a": 1, "b": 1}],)

    assert exact.audit(one).status.value == "agreed"
    assert built == ["@step-audit-0"]
    assert exact.audit(two).status.value == "agreed"
    assert built == ["@step-audit-0"]
    assert len(exact._prepared_step_audit.encodings) == 2
    assert exact.audit(one).status.value == "agreed"
    assert built == ["@step-audit-0"]

def test_ordinary_behavior_exact_refusal_happens_after_score_persistence(
        tmp_path, monkeypatch):
    from behave.sim.behavior.transaction_analysis import BehaviorAnalysisRequest
    from behave.sim.result_store import ResultStore

    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(
        SOURCE, _schema(), file="stable/behavior.py", bound=1).identity
    clear_construction_cache()
    reference = _reference()
    task = _task(identity, reference=reference)
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(
            path, file="stable/behavior.py", timeout_s=90),
        _generation(task.contract),
        bounded_analysis=True,
    )
    request = handle.command_request(
        run_directory=tmp_path / "behavior-refusal-command",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        probe_budget=0,
        run_analysis=True,
        reference=reference,
        analysis_wall_timeout_seconds=180,
    )

    persisted = []
    real_decided = ResultStore.decided

    def observed_decision(store, authority):
        if authority.evaluation is not None:
            persisted.append(authority.evaluation.as_evidence_dict())
        return real_decided(store, authority)

    def unavailable(_request, _evaluation, **_policy):
        assert persisted, "ordinary exact construction preceded score persistence"
        raise api.Refused(api.Refusal(
            "test-behavior-exact-encoding-refused",
            "the test forces ordinary exact construction to refuse"))

    monkeypatch.setattr(ResultStore, "decided", observed_decision)
    monkeypatch.setattr(BehaviorAnalysisRequest, "analyse", unavailable)

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED, got.as_dict()
    assert got.evaluation.passed
    assert got.analysis.verdict is api.Verdict.REFUSED
    assert got.analysis.evidence["refusal"]["kind"] == \
        "test-behavior-exact-encoding-refused"
    assert persisted == [got.evaluation.as_evidence_dict()]
    record = json.loads(Path(got.record_path).read_text())
    assert record["authority"]["evaluation"] == \
        json.loads(json.dumps(persisted[0]))
    assert record["authority"]["passed"] is True


def test_public_behavior_command_classifies_a_straight_line_location(tmp_path):
    result, _identity = _run_public_behavior_command(
        tmp_path, STRAIGHT_LINE_SOURCE)

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    assert len(result.analysis.per_goal) == 1
    (goal, row), = result.analysis.per_goal.items()
    assert goal.startswith("location:")
    assert row["status"] == "reached"


def test_public_behavior_command_preserves_multi_transaction_batches(tmp_path):
    result, _identity = _run_public_behavior_command(
        tmp_path,
        MULTI_OUTPUT_SOURCE,
        answers_per_transaction=2,
    )

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert all(
        len(dict(record.outputs)["out"]) == 2
        for run in result.evaluation.results
        for record in _transaction_records(run)
    )


def test_public_behavior_command_treats_repeated_output_rows_as_values(tmp_path):
    result, _identity = _run_public_behavior_command(
        tmp_path,
        REPEATED_MULTI_OUTPUT_SOURCE,
        answers_per_transaction=2,
    )

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert result.evaluation.accepted == len(result.evaluation.results)
    assert all(
        len(dict(record.outputs)["out"]) == 2
        for run in result.evaluation.results
        for record in _transaction_records(run)
    )


def test_public_behavior_command_uses_the_frozen_golden_behavior(tmp_path):
    result, _identity = _run_public_behavior_command(
        tmp_path,
        SOURCE,
        reference_source=INVERTED_SOURCE,
    )

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert not result.evaluation.passed
    assert result.evaluation.mismatch_count > 0
    assert result.analysis.concrete == result.evaluation.fingerprint


def test_public_behavior_command_preserves_each_endpoint_stream(tmp_path):
    schema = TransactionSchema(
        [
            SchemaEndpoint("left_data", "input", 8),
            SchemaEndpoint("right_data", "input", 8),
            SchemaEndpoint("left_result_data", "output", 8),
            SchemaEndpoint("right_result_data", "output", 8),
        ],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )
    identity = behavior_instance.lower(
        MULTI_ENDPOINT_SOURCE,
        schema,
        file="stable/endpoint_product.py",
        bound=1,
    ).identity
    offered = _endpoint_product_realization(identity)
    reference = _endpoint_product_reference()
    task = api.admit(
        _endpoint_product_contract(),
        api.Realization(
            "behavior",
            offered.ports.values(),
            channel_ports=dict(offered.channel_ports),
            identity=identity,
        ),
        reference=reference,
    )
    path = tmp_path / "candidate.py"
    path.write_text(MULTI_ENDPOINT_SOURCE)
    generation = api.GenerationSpec(
        horizon=1,
        queue_bound=1,
        reset_hold_cycles=0,
        drain=0,
    )
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(
            path, file="stable/endpoint_product.py", timeout_s=90),
        generation,
        bounded_analysis=True,
    )
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "endpoint-product-command",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        probe_budget=0,
        run_analysis=True,
        reference=reference,
        feasibility_timeout_ms=20_000,
        query_timeout_ms=20_000,
        analysis_wall_timeout_seconds=180,
    ))

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    for run in result.evaluation.results:
        for record in _transaction_records(run):
            outputs = dict(record.outputs)
            assert set(outputs) == {"left_result", "right_result"}
            assert len(outputs["left_result"]) == 1
            assert len(outputs["right_result"]) == 1


def test_public_behavior_handle_lowers_and_solves_only_in_named_jobs(
        tmp_path, monkeypatch):
    clear_analysis_answer_cache()
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)

    # Existing admission/frontends already carry this identity.  Compute the fixture's
    # value before installing the spies; the assertion below is specifically that the
    # new public construction and query do not call either lowering entry in this
    # verifier process.
    identity = behavior_instance.lower(
        SOURCE, _schema(), file="stable/behavior.py", bound=1).identity
    clear_construction_cache()
    task = _task(identity)
    generation = _generation(task.contract)
    parent_calls = []

    def forbidden(*_args, **_kwargs):
        parent_calls.append("lowered-in-parent")
        raise AssertionError("the verifier parent ran Behavior lowering")

    monkeypatch.setattr(behavior_instance, "lower", forbidden)
    config = api.BehaviorConfig(path, file="stable/behavior.py", timeout_s=90)
    handle = api.prepare_behavior(
        task, config, generation, bounded_analysis=True)
    exact = handle.bind()

    assert type(handle) is api.BehaviorHandle
    assert handle.artifact_identity == identity
    assert exact.domain.bound == 1
    assert exact.domain.task_contract == task.contract.digest
    assert handle.frontend_catalogue == \
        exact.instance.frontend_catalogue_manifest()
    assert type(exact.instance).__name__ == \
        "BehaviorIsolatedAnalysisInstance"
    assert not hasattr(exact.instance, "proc")
    assert parent_calls == []

    feasibility = exact.feasible(timeout_ms=5_000)
    answer, witness = feasibility
    assert answer.value == "feasible"
    assert witness in ([{"a": 0}], [{"a": 1}])
    assert feasibility.telemetry.backend == "behavior-z3"
    assert feasibility.telemetry.cache is CacheDisposition.MISS
    identities = exact.evidence_identity()
    assert feasibility.telemetry.semantic_instance == analysis_semantic_instance(
        artifact=exact.domain.artifact,
        task_contract=exact.domain.task_contract,
        domain=exact.domain.digest(),
        catalogue=exact.catalogue_manifest().digest(),
        representation=identities["representation"],
        encoding=identities["encoding"])
    assert feasibility.telemetry.as_dict()["timings_ns"]["total_ns"] >= 0
    audited = exact.audit_request(witness)
    assert type(audited) is api.AuditedWitness
    assert audited.inputs == tuple(witness)

    concrete = api.ConcreteEvaluation(
        task, catalogue=exact.catalogue_manifest())
    before = json.dumps(concrete.as_dict(), sort_keys=True)
    analysed = api.analyse(
        concrete, exact,
        feasibility_timeout_ms=5_000, query_timeout_ms=5_000,
        wall_timeout_seconds=60)
    assert analysed.verdict is api.Verdict.CLASSIFIED, analysed.as_dict()
    assert analysed.concrete == concrete.fingerprint
    assert analysed.evidence["feasibility"]["telemetry"]["query_kind"] == \
        "feasibility"
    assert analysed.evidence["feasibility"]["telemetry"]["cache"] == "hit"
    assert all(
        row["telemetry"]["backend"] == "behavior-z3"
        for row in analysed.evidence["queries"])
    for row in analysed.evidence["queries"]:
        telemetry = row["telemetry"]
        assert {
            "full_assertions", "full_nodes", "full_text_bytes",
            "sliced_assertions", "sliced_nodes", "sliced_text_bytes",
        } <= set(telemetry["formula"])
        assert {"slice_ns", "solver_load_ns", "solve_ns"} \
            <= set(telemetry["timings_ns"])
        assert telemetry["peak_rss_bytes"] > 0
        assert telemetry["provenance"]["tool"] == "z3"
    assert json.dumps(concrete.as_dict(), sort_keys=True) == before
    assert parent_calls == []


def test_public_behavior_exact_capability_is_required_before_any_query(tmp_path):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(
        SOURCE, _schema(), file="stable/behavior.py", bound=1).identity
    clear_construction_cache()
    task = _task(identity)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(
            path, file="stable/behavior.py", timeout_s=90),
        _generation(task.contract), bounded_analysis=True)
    exact = handle.bind()
    concrete = api.ConcreteEvaluation(
        task, catalogue=exact.catalogue_manifest())

    rebuilt = api.evaluation_policy(task, handle.frontend_catalogue)
    replaced = api.analyse(concrete, exact, goal_policy=rebuilt,
        wall_timeout_seconds=60)
    assert replaced.verdict is api.Verdict.REFUSED
    assert replaced.concrete == concrete.fingerprint
    assert replaced.evidence["refusal"]["kind"] == \
        "analysis-goal-policy-reconstructed"

    class Proxy:
        def __init__(self, base, *, capability, implementation):
            self.base = base
            self.capability = capability
            self.implementation = implementation

        def __getattr__(self, name):
            return getattr(self.base, name)

        @staticmethod
        def goal_capability_required():
            return True

        def goal_policy(self):
            return self.base.goal_policy()

        def goal_capability_use(self):
            return self.capability

        def exact_goal_implementation(self):
            return self.implementation

        def feasible(self, *_args, **_kwargs):
            raise AssertionError("capability refusal must precede feasibility")

        def query(self, *_args, **_kwargs):
            raise AssertionError("capability refusal must precede goal queries")

    base = exact.instance
    foreign_capability = rebuilt.bind_complete_implementation(
        api.EXACT_BOUNDED_HIT, base.exact_goal_implementation(),
        rebuilt.manifest)
    for capability, implementation, kind in (
            (None, base.exact_goal_implementation(),
             "analysis-exact-goal-capability-missing"),
            (foreign_capability, base.exact_goal_implementation(),
             "analysis-exact-goal-capability-policy-mismatch"),
            (base.goal_capability_use(), "behavior-bounded-hit/changed",
             "goal-provider-capability-use-changed")):
        proxy = Proxy(
            base, capability=capability, implementation=implementation)
        altered = BoundedInstance(proxy, exact.domain)
        got = api.analyse(concrete, altered,
            wall_timeout_seconds=60)
        assert got.verdict is api.Verdict.REFUSED
        assert got.concrete == concrete.fingerprint
        assert got.evidence["refusal"]["kind"] == kind

def test_public_behavior_handle_freezes_source_and_closes_construction(
        tmp_path):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(SOURCE, _schema(), bound=1).identity
    clear_construction_cache()
    task = _task(identity)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, timeout_s=90),
        _generation(task.contract), bounded_analysis=True)
    exact = handle.bind()

    path.write_text("this is not Python\n")
    answer, _witness = exact.feasible(timeout_ms=5_000)
    assert answer.value == "feasible"

    with pytest.raises(AttributeError):
        handle._exact = None
    with pytest.raises(AttributeError):
        exact.domain = None
    # The handle used to freeze only its outer shell.  Its reachable context and proxy
    # remained ordinary slotted objects, so replacing this context after bind made later
    # jobs analyse another artifact while the public domain kept the first identity.
    with pytest.raises(AttributeError):
        handle._context.source = "class Other: pass\n"
    with pytest.raises(AttributeError):
        exact.instance._context = object()
    with pytest.raises(AttributeError):
        exact.instance._analysis_identity = "another-artifact"
    with pytest.raises(TypeError, match="prepare_behavior"):
        api.BehaviorHandle(None, None, None, None, None)


def test_malformed_exact_job_reply_is_a_typed_refusal(tmp_path, monkeypatch):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(SOURCE, _schema(), bound=1).identity
    clear_construction_cache()
    task = _task(identity)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, timeout_s=90),
        _generation(task.contract), bounded_analysis=True)
    exact = handle.bind()

    monkeypatch.setattr(
        "behave.sim.worker.run_job_strict",
        lambda *_args, **_kwargs: {
            "version": "behavior-exact-analysis/14",
            "operation": "query",
            "status": "ok",
            "value": {"reachable": {}, "unreachable": [], "undecided": {}},
        })
    with pytest.raises(api.Refused) as caught:
        exact.query(tuple(handle.frontend_catalogue), timeout_ms=5_000)
    assert caught.value.refusal.kind == \
        "behavior-exact-isolated-analysis-query-unavailable"


@pytest.mark.parametrize(
    ("encoding_kind", "refusal_kind"),
    [
        ("range", "behavior-exact-encoding-range-unavailable"),
        ("shape", "behavior-exact-encoding-shape-unavailable"),
        ("type", "behavior-exact-encoding-type-unavailable"),
        ("width", "behavior-exact-encoding-width-unavailable"),
        ("capability", "behavior-exact-encoding-capability-unavailable"),
        ("resource", "behavior-exact-encoding-resource-unavailable"),
        ("goal", "behavior-exact-encoding-goal-unavailable"),
        (None, "behavior-exact-encoding-operation-unavailable"),
    ],
)
def test_exact_job_preserves_the_frontend_encoding_failure_kind(
        encoding_kind, refusal_kind):
    from behave.sim.behavior.analysis_job import _construction_failure
    from behave.sim.symbolic.encoder import EncodingError

    refusal = _construction_failure(
        EncodingError("the exact encoding is unavailable", encoding_kind))

    assert refusal.kind == refusal_kind
    assert refusal.detail["encoding_kind"] == (encoding_kind or "operation")


def test_cross_artifact_exact_job_reply_is_rejected_by_its_binding(
        tmp_path, monkeypatch):
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(SOURCE, _schema(), bound=1).identity
    clear_construction_cache()
    task = _task(identity)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, timeout_s=90),
        _generation(task.contract), bounded_analysis=True)
    exact = handle.bind()
    domain = exact.domain
    seen = []

    def crossed(_name, payload, **_kwargs):
        seen.append(payload["binding"])
        return {
            "version": "behavior-exact-analysis/14",
            "operation": "query",
            "binding": {
                **payload["binding"],
                "artifact": "behavior/15:another-artifact",
            },
            "status": "ok",
            # Binding rejection must precede trusting any operation-specific value.
            "value": {},
        }

    monkeypatch.setattr("behave.sim.worker.run_job_strict", crossed)
    with pytest.raises(api.Refused) as caught:
        exact.query(tuple(handle.frontend_catalogue), timeout_ms=5_000)

    assert seen == [{
        "artifact": identity,
        "task_contract": task.contract.digest,
        "horizon": 1,
        "domain": domain.digest(),
        "analysis_identity": identity,
        "catalogue": exact.catalogue_manifest().digest(),
        "admission": task.handoff_identity,
    }]
    assert caught.value.refusal.kind == \
        "behavior-exact-isolated-analysis-query-unavailable"


def test_invalid_source_refuses_without_an_untyped_worker_failure(tmp_path):
    path = tmp_path / "candidate.py"
    path.write_text("class M(:\n")
    task = _task("not-a-transition-system")

    with pytest.raises(api.FailureRaised) as caught:
        api.prepare_behavior(
            task, api.BehaviorConfig(path, timeout_s=30),
            _generation(task.contract))
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.reason.kind == "behavior-source-syntax-error"


def test_public_behavior_boundary_contains_no_task_or_source_dispatch_hacks():
    from behave.sim.behavior import task_boundary

    body = inspect.getsource(task_boundary._endpoint_projections)
    assert "task.name" not in body
    assert "startswith(" not in body
    assert "problem" not in body
    assert "benchmark" not in body
