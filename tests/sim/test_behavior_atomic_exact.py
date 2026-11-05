"""The exact SolverIR instance of a timestamped atomic Behavior schedule."""

from itertools import product

import pytest

from tests.support.atomic_behavior import (
    ATOMIC_PARTIAL_FAILURE_SOURCE,
    SOURCE,
    U2,
    U4,
    _contract,
    _schedule,
    _transaction_stimulus,
)

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.symbolic.exact_formula import SliceMode
from behave.sim.behavior import atomic_exact
from behave.sim.symbolic import ir
from behave.sim.behavior.atomic_exact import build_atomic_behavior
from behave.sim.behavior.catalogue import source_frontend_catalogue
from behave.sim.behavior.construction import (
    CONSTRUCTION_VERSION,
    ConstructionFacts,
)
from behave.sim.behavior.extract import source_model
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.behavior.native_values import DEFAULT_WIRE_NODES, WireEncoder
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.clock_schedule import (
    ClockTiming,
    GlobalEventSlot,
    GlobalSchedule,
)
from behave.sim.core.contract import ValueType
from behave.sim.core.instance import endpoint_batch_rows
from behave.sim.core.query import SolverAnswer
from behave.sim.core.refusal import Refused

from .test_command_handler import _BaselineOnly


def test_atomic_frontend_version_is_pinned():
    assert atomic_exact.ATOMIC_EXACT_FRONTEND == "behavior-atomic/20"




def _clock_only_contract():
    return api.TaskContract(
        (
            api.Endpoint(
                "a_in", api.Direction.IN, [api.Field("a", U2, "a")],
                api.ready_valid(api.Direction.IN, "a_valid", "a_ready"),
                clock="a", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "a_out", api.Direction.OUT, [api.Field("oa", U4, "oa")],
                api.ready_valid(api.Direction.OUT, "oa_valid", "oa_ready"),
                clock="a", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "b_in", api.Direction.IN, [],
                api.ready_valid(api.Direction.IN, "b_valid", "b_ready"),
                clock="b", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "b_out", api.Direction.OUT, [api.Field("ob", U4, "ob")],
                api.ready_valid(api.Direction.OUT, "ob_valid", "ob_ready"),
                clock="b", kind=api.Kind.TRANSACTION),
        ),
        [api.ClockDomain("a", "a_clk", "a_rst", reset_hold_cycles=0),
         api.ClockDomain("b", "b_clk", "b_rst", reset_hold_cycles=0)],
        source="atomic clock-only handler task")








def _rows(a0=1, b0=2, a1=0, b2=3):
    # The fixed product has one complete valuation at every global slot.  Values
    # belonging to an uninvoked domain are canonical padding and semantically inert.
    return [
        {"a.__invoke__": 1, "a.a": a0,
         "b.__invoke__": 1, "b.b": b0},
        {"a.__invoke__": 1, "a.a": a1,
         "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 0, "a.a": 0,
         "b.__invoke__": 1, "b.b": b2},
    ]


def _instance(source=SOURCE):
    contract = _contract()
    construction = ConstructionFacts.from_dict({
        "version": CONSTRUCTION_VERSION,
        "values": WireEncoder(DEFAULT_WIRE_NODES).encode({"x": 0, "y": 8}),
        "unsupported": [],
        "shared_pairs": [],
        "opaque_fields": [],
        "boundary_fields": [],
    })
    return build_atomic_behavior(
        source, "atomic_behavior.py", contract, _schedule(),
        artifact="atomic-model", task_contract=contract.digest,
        construction=construction)


def _admitted_atomic_task(identity="atomic-model", *, reference=None):
    ports = (
        api.Port("a", 2, driven_by="environment", clock="a"),
        api.Port("oa", 4, driven_by="artifact", clock="a"),
        api.Port("b", 2, driven_by="environment", clock="b"),
        api.Port("ob", 4, driven_by="artifact", clock="b"),
        api.Port("a_valid", 1, driven_by="environment", clock="a"),
        api.Port("a_ready", 1, driven_by="artifact", clock="a"),
        api.Port("oa_valid", 1, driven_by="artifact", clock="a"),
        api.Port("oa_ready", 1, driven_by="environment", clock="a"),
        api.Port("b_valid", 1, driven_by="environment", clock="b"),
        api.Port("b_ready", 1, driven_by="artifact", clock="b"),
        api.Port("ob_valid", 1, driven_by="artifact", clock="b"),
        api.Port("ob_ready", 1, driven_by="environment", clock="b"),
        api.Port("a_clk", 1, driven_by="environment"),
        api.Port("a_rst", 1, driven_by="environment"),
        api.Port("b_clk", 1, driven_by="environment"),
        api.Port("b_rst", 1, driven_by="environment"),
    )
    return api.admit(
        _contract(),
        api.Realization(
            "behavior", ports,
            channel_ports={
                "a_in": ("a",), "a_out": ("oa",),
                "b_in": ("b",), "b_out": ("ob",),
            },
            identity=identity),
        reference=reference)


def _schedule_stimulus(schedule=None):
    schedule = _schedule() if schedule is None else schedule
    return api.Stimulus(
        queues={"a_in": (), "b_in": ()},
        slots=[slot.event for slot in schedule.slots],
        source="atomic-public-test", clock_schedule=schedule)




def test_coincident_ir_handlers_read_old_state_and_commit_once():
    instance = _instance()
    replay, witness = instance.replay_witness(
        _rows(), instance.bind().domain.digest())

    assert replay.broken is None
    # At t0 both handlers read (x=0,y=8): a writes 8+1 then its branch adds one,
    # while b writes 0+2.  Neither observes the other's coincident proposal.
    assert witness.states[1] == {"x": 10, "y": 2}
    assert witness.states[2] == {"x": 2, "y": 2}
    assert witness.states[3] == {"x": 2, "y": 5}
    assert instance.audit((_rows(),)).status is AuditCheckStatus.AGREED


@pytest.mark.parametrize("helper", (None, "process_debug", "process_coincident"))
def test_small_atomic_input_space_agrees_with_the_symbolic_relation(helper):
    source = SOURCE if helper is None else SOURCE + (
        f"\n    def {helper}(self):\n        self.x = 15\n        self.y = 15\n")
    instance = _instance(source)
    traces = tuple(
        _rows(a0=a0, b0=b0, a1=a1, b2=b2)
        for a0, b0, a1, b2 in product(range(2), repeat=4))

    assert instance.audit(traces).status is AuditCheckStatus.AGREED


def test_atomic_frontend_catalogue_is_independent_of_domain_interleaving():
    source = """
class M:
    def reset(self):
        self.x = 0

    def process_a(self, a):
        if self.x:
            value = 1
        else:
            value = 0
        return {"oa": value}

    def process_b(self, b):
        self.x = b
        return {"ob": b}
"""
    contract = _contract()
    construction = ConstructionFacts.from_dict({
        "version": CONSTRUCTION_VERSION,
        "values": WireEncoder(DEFAULT_WIRE_NODES).encode({"x": 0}),
        "unsupported": [],
        "shared_pairs": [],
        "opaque_fields": [],
        "boundary_fields": [],
    })
    schedule = _schedule()

    frozen = atomic_exact.atomic_frontend_catalogue(
        source, "interleaved.py", contract, len(schedule.slots),
        construction=construction)
    instance = build_atomic_behavior(
        source, "interleaved.py", contract, schedule,
        artifact="interleaved", task_contract=contract.digest,
        construction=construction)

    assert instance.frontend_catalogue_manifest() == frozen
    assert len(tuple(
        goal for goal in frozen.values()
        if goal.cls == "coverage" and goal.where("line") == 7
        and goal.family == "decision")) == 2


def test_atomic_frontend_retains_goals_of_a_helper_on_a_pruned_path():
    source = """
class M:
    def reset(self):
        self.x = 0

    def helper(self, value):
        if value:
            return 4 // value
        return 0

    def process_a(self, a):
        if a < 0:
            value = self.helper(a)
        else:
            value = 0
        return {"oa": value}

    def process_b(self, b):
        self.x = b
        return {"ob": self.x}
"""
    contract = _contract()
    construction = ConstructionFacts.from_dict({
        "version": CONSTRUCTION_VERSION,
        "values": WireEncoder(DEFAULT_WIRE_NODES).encode({"x": 0}),
        "unsupported": [],
        "shared_pairs": [],
        "opaque_fields": [],
        "boundary_fields": [],
    })

    frozen = atomic_exact.atomic_frontend_catalogue(
        source, "atomic_pruned.py", contract, 3,
        construction=construction)
    source_manifest = source_frontend_catalogue(
        source_model(source, "atomic_pruned.py"), source,
        "atomic_pruned.py")

    assert frozen == source_manifest
    assert sum(goal.family == "location" for goal in frozen.values()) == 9
    assert sum(goal.family == "decision" for goal in frozen.values()) == 4
    assert sorted(goal.family for goal in frozen.values() if goal.cls == "safety") == [
        "attributeerror", "divzero"]


def test_atomic_discharged_safety_goal_has_an_exact_unsat_query():
    source = """
class M:
    def reset(self):
        self.x = 0

    def process_a(self, a):
        table = [0, 1]
        return {"oa": table[1]}

    def process_b(self, b):
        self.x = b
        return {"ob": self.x}
"""
    contract = _contract()
    construction = ConstructionFacts.from_dict({
        "version": CONSTRUCTION_VERSION,
        "values": WireEncoder(DEFAULT_WIRE_NODES).encode({"x": 0}),
        "unsupported": [],
        "shared_pairs": [],
        "opaque_fields": [],
        "boundary_fields": [],
    })
    instance = build_atomic_behavior(
        source, "discharged.py", contract, _schedule(),
        artifact="discharged", task_contract=contract.digest,
        construction=construction)
    safety = tuple(
        goal for goal, declaration in instance.goals().items()
        if declaration.cls == "safety")

    assert sorted(instance.goals()[goal].family for goal in safety) == [
        "attributeerror", "indexerror"]
    verdict = instance.query(safety, len(instance.schedule.slots), timeout_ms=5_000)

    assert verdict.unreachable == set(safety)
    assert verdict.undecided == {}
    assert len(verdict.queries) == 1
    assert verdict.queries[0].answer is SolverAnswer.UNSAT


def test_atomic_goals_use_the_common_bounded_instance_path():
    exact = _instance().bind()
    goals = tuple(exact.goals())
    verdict = exact.query(goals)

    assert goals
    assert verdict.queries[0].goals == tuple(sorted(goals))
    assert set(verdict.reachable) | set(verdict.unreachable) == set(goals)
    for trace in verdict.reachable.values():
        assert exact.validate(trace) == []
    for call in verdict.queries:
        if call.answer is SolverAnswer.SAT:
            replayed = exact.replay(call.witness)
            assert set(call.goals) & set(replayed.hits)


def test_atomic_behavior_registers_one_exact_schedule_formula_for_slicing():
    exact = _instance().bind()
    formula = exact.instance._formula
    goals = tuple(exact.goals())

    selected = formula.formula.slice(z3.Or(*(
        formula.goal_condition(goal) for goal in goals)))

    assert selected.mode is SliceMode.SLICED
    assert selected.kept_definitions
    assert selected.dropped_definitions
    # The fixed schedule's row variables are the only external symbolic atoms;
    # internal encoder names are registered definitions, never inferred later.
    assert {atom.get_id() for atom in formula.formula.boundary_atoms} == {
        term.get_id()
        for slot in formula.slots
        for term in slot.inputs.values()
    }


def test_public_atomic_handle_defers_schedule_binding_to_the_named_job(
        tmp_path, monkeypatch):
    path = tmp_path / "atomic.py"
    path.write_text(SOURCE)
    realization = api.realization_of_behavior_source(
        SOURCE, _contract(), file="stable/atomic.py")
    task = api.admit(_contract(), realization)
    assert task.realization.identity.startswith("behavior-source/2:")
    generation = api.GenerationSpec(
        horizon=3, queue_bound=4, reset_hold_cycles=0, drain=0)

    # Parent-process lowering would violate the public isolation boundary.
    parent_calls = []

    def forbidden(*_args, **_kwargs):
        parent_calls.append("lowered-in-parent")
        raise AssertionError("the verifier parent ran atomic Behavior lowering")

    monkeypatch.setattr(
        "behave.sim.behavior.atomic_exact.build_atomic_behavior", forbidden)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(
            path, file="stable/atomic.py", timeout_s=90),
        generation, bounded_analysis=True)

    with pytest.raises(Refused) as unbound:
        _ = handle.exact
    assert unbound.value.refusal.kind == \
        "behavior-atomic-exact-schedule-unbound"
    stimulus = _transaction_stimulus()
    exact = handle.bind((stimulus,))
    assert exact.domain.bound == 3
    assert exact.domain.task_contract == task.contract.digest
    assert exact.instance.frontend_catalogue_manifest() == \
        handle.frontend_catalogue
    assert parent_calls == []

    replay = exact.replay(_rows())
    witness = exact.audit_request(_rows())
    assert type(witness) is api.AuditedWitness, witness.as_dict()
    fields = {
        endpoint.name: tuple(field.name for field in endpoint.fields)
        for endpoint in task.contract.outputs()
    }
    input_batches = (
        {"a_in": (1,), "b_in": (2,)},
        {"a_in": (0,)},
        {"b_in": (3,)},
    )
    records = []
    goals = tuple(exact.catalogue_manifest())
    for position, (inputs, output) in enumerate(zip(
            input_batches, replay.outputs)):
        accepted = dict(inputs)
        emitted = {}
        for transaction in endpoint_batch_rows(output):
            endpoint, payload = next(iter(transaction.items()))
            emitted.setdefault(endpoint, []).append(
                tuple(payload[name] for name in fields[endpoint]))
        records.append(api.SlotRecord(
            accepted=accepted,
            outputs=emitted,
            hits={goal: goal in witness.hit_sets[position] for goal in goals}))
    trace = api.AuditedTrace(
        stimulus, records=records, completion=api.Completion.NORMAL,
        domain="concrete-atomic", artifact=task.realization.identity)
    result = api.RunResult.not_compared(
        stimulus, trace,
        api.RunFailure.verifier(
            "test-audit-view",
            api.Refusal(
                "analysis-raw-run-not-compared",
                "bounded analysis reads this raw run")))
    audited = exact.instance.audit_record(result, task)
    assert audited.disposition is api.AuditDisposition.ADMITTED
    assert audited.trace == tuple(_rows())


def test_public_atomic_handle_refuses_a_shorter_analysis_bound(tmp_path):
    path = tmp_path / "atomic.py"
    path.write_text(SOURCE)
    realization = api.realization_of_behavior_source(
        SOURCE, _contract(), file="stable/atomic.py")
    task = api.admit(_contract(), realization)
    generation = api.GenerationSpec(
        horizon=3, queue_bound=4, reset_hold_cycles=0, drain=0)

    with pytest.raises(Refused) as caught:
        api.prepare_behavior(
            task,
            api.BehaviorConfig(
                path, file="stable/atomic.py", timeout_s=90),
            generation,
            bounded_analysis=True,
            analysis_bound=1,
        )
    assert caught.value.refusal.kind == \
        "behavior-atomic-analysis-bound-unsupported"


def test_public_atomic_behavior_command_reuses_the_common_score_path(tmp_path):
    path = tmp_path / "atomic.py"
    path.write_text(SOURCE)
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": SOURCE.encode("utf-8")},
        config={"class": "M"},
    )
    contract = _contract()
    realization = api.realization_of_behavior_source(
        SOURCE, contract, file="stable/atomic.py")
    task = api.admit(contract, realization, reference=reference)
    generation = api.GenerationSpec(
        horizon=3, queue_bound=1, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(
            path, file="stable/atomic.py", timeout_s=90),
        generation,
        bounded_analysis=True,
    )
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "atomic-command",
        baseline=api.build_baseline(contract, generation, realization=realization,
            policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)),
        providers=(_BaselineOnly(),),
        probe_budget=0,
        run_analysis=True,
        reference=reference,
        feasibility_timeout_ms=30_000,
        query_timeout_ms=30_000,
        analysis_wall_timeout_seconds=180,
    ))

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert result.analysis is not None
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    assert all(run.trace.stimulus.clock_schedule is not None
               for run in result.evaluation.results)


def test_atomic_concrete_replay_records_a_partial_failure_and_pads(tmp_path):
    path = tmp_path / "atomic_partial.py"
    path.write_text(ATOMIC_PARTIAL_FAILURE_SOURCE)
    contract = _contract()
    task = api.admit(
        contract,
        api.realization_of_behavior_source(
            ATOMIC_PARTIAL_FAILURE_SOURCE, contract,
            file="stable/atomic_partial.py"))
    handle = api.prepare_behavior(
        task,
        api.BehaviorConfig(
            path, file="stable/atomic_partial.py", timeout_s=90),
        api.GenerationSpec(
            horizon=3, queue_bound=2, reset_hold_cycles=0, drain=0),
        bounded_analysis=False,
    )

    trace = handle._artifact.trace(task.contract, _transaction_stimulus())

    safety = tuple(
        goal.id for goal in handle.frontend_catalogue.values()
        if goal.cls == "safety")
    assert len(safety) == 1
    assert trace.completion is api.Completion.PADDED
    assert len(trace.records) == 3
    assert dict(trace.records[1].accepted) == {"a_in": (0,)}
    assert dict(trace.records[1].hits)[safety[0]] is True
    assert dict(trace.records[1].monitor) == {"terminal_failure": 1}
    assert dict(trace.records[2].monitor) == {"padding": 1}
    assert not any(reached for _goal, reached in trace.records[2].hits)


def test_public_atomic_handle_refuses_a_mixed_concrete_schedule_pool(tmp_path):
    path = tmp_path / "atomic.py"
    path.write_text(SOURCE)
    task = _admitted_atomic_task()
    generation = api.GenerationSpec(
        horizon=3, queue_bound=4, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, timeout_s=90), generation,
        bounded_analysis=True)
    other = _schedule((3, 2))

    with pytest.raises(Refused) as caught:
        handle.bind((_schedule_stimulus(), _schedule_stimulus(other)))
    assert caught.value.refusal.kind == \
        "exact-action-schedule-not-unique"


def _facts(values, *, unsupported=()):
    return ConstructionFacts.from_dict({
        "version": CONSTRUCTION_VERSION,
        "values": WireEncoder(DEFAULT_WIRE_NODES).encode(
            dict(sorted(values.items()))),
        "unsupported": [list(row) for row in sorted(unsupported)],
        "shared_pairs": [],
        "opaque_fields": [],
        "boundary_fields": [],
    })


def _two_endpoint_contract(*, reverse=False):
    boolean = ValueType.boolean()
    pair = ValueType.array(2, ValueType.integer(U2))
    endpoints = [
        api.Endpoint(
            "a_left", api.Direction.IN,
            [api.Field("data", boolean, "flag")],
            api.ready_valid(api.Direction.IN, "left_valid", "left_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "a_right", api.Direction.IN,
            [api.Field("data", pair, "pair")],
            api.ready_valid(api.Direction.IN, "right_valid", "right_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "a_high", api.Direction.OUT,
            [api.Field("data", U4, "high")],
            api.ready_valid(api.Direction.OUT, "high_valid", "high_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "a_low", api.Direction.OUT,
            [api.Field("data", U4, "low")],
            api.ready_valid(api.Direction.OUT, "low_valid", "low_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "b_in", api.Direction.IN, [api.Field("b", U2, "b")],
            api.ready_valid(api.Direction.IN, "b_valid", "b_ready"),
            clock="b", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "b_out", api.Direction.OUT, [api.Field("ob", U4, "ob")],
            api.ready_valid(api.Direction.OUT, "ob_valid", "ob_ready"),
            clock="b", kind=api.Kind.TRANSACTION),
    ]
    if reverse:
        endpoints.reverse()
    return api.TaskContract(
        endpoints,
        [api.ClockDomain("a", "a_clk", "a_rst", reset_hold_cycles=0),
         api.ClockDomain("b", "b_clk", "b_rst", reset_hold_cycles=0)],
        source="atomic multi-endpoint task")


MULTI_ENDPOINT_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0

    def process_a(self, flag, pair):
        self.x = pair[0] + pair[1] + int(flag)
        return {"low": self.x, "high": self.x + 1}

    def process_b(self, b):
        self.y = self.y + b
        return {"ob": self.y}
"""


STRUCTURED_OUTPUT_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0

    def process_a(self, a):
        self.x = self.x + a
        return {"packet": {
            "enabled": bool(a),
            "pair": [self.x, self.x + 1],
        }}

    def process_b(self, b):
        self.y = self.y + b
        return {"ob": self.y}
"""


FUNCTIONAL_ARRAY_STATE_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mem = [0] * 8
        self.x = 0
        self.y = 0

    def process_a(self, a):
        self.mem[a] = a + 1
        self.x = self.mem[a]
        return {"oa": self.x}

    def process_b(self, b):
        self.y = self.mem[b]
        return {"ob": self.y}
"""


def test_uniform_large_state_uses_persistent_qfbv_array_semantics(monkeypatch):
    # Exercise the production representation at a small test-only threshold;
    # the real threshold is a capacity choice, not a semantic distinction.
    monkeypatch.setattr(atomic_exact, "_FUNCTIONAL_ARRAY_MIN_CELLS", 8)
    instance = build_atomic_behavior(
        FUNCTIONAL_ARRAY_STATE_SOURCE, "functional_state.py", _contract(),
        _schedule(), artifact="functional-state",
        task_contract=_contract().digest,
        construction=_facts({"mem": [0] * 8, "x": 0, "y": 0}))

    assert isinstance(instance._formula.states[0]["mem"], FunctionalArray)
    rows = _rows(a0=1, b0=2, a1=0, b2=1)
    replay, witness = instance.replay_witness(
        rows, instance.bind().domain.digest())
    assert replay.broken is None
    assert witness.states[-1]["mem"][1] == 2
    assert endpoint_batch_rows(replay.outputs[-1]) == (
        {"b_out": {"ob": 2}},)
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED


def _structured_output_contract():
    packet = ValueType.record({
        "enabled": ValueType.boolean(),
        "pair": ValueType.array(2, ValueType.integer(U4)),
    })
    endpoints = (
        api.Endpoint(
            "a_in", api.Direction.IN, [api.Field("a", U2, "a")],
            api.ready_valid(api.Direction.IN, "a_valid", "a_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "a_out", api.Direction.OUT,
            [api.Field("packet", packet, "packet")],
            api.ready_valid(api.Direction.OUT, "oa_valid", "oa_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "b_in", api.Direction.IN, [api.Field("b", U2, "b")],
            api.ready_valid(api.Direction.IN, "b_valid", "b_ready"),
            clock="b", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "b_out", api.Direction.OUT, [api.Field("ob", U4, "ob")],
            api.ready_valid(api.Direction.OUT, "ob_valid", "ob_ready"),
            clock="b", kind=api.Kind.TRANSACTION),
    )
    return api.TaskContract(
        endpoints,
        [api.ClockDomain("a", "a_clk", "a_rst", reset_hold_cycles=0),
         api.ClockDomain("b", "b_clk", "b_rst", reset_hold_cycles=0)],
        source="atomic structured-output task")


def _multi_endpoint_rows():
    return [
        {"a.__invoke__": 1, "a.flag": 1,
         "a.pair[0]": 1, "a.pair[1]": 2,
         "b.__invoke__": 1, "b.b": 1},
        {"a.__invoke__": 1, "a.flag": 0,
         "a.pair[0]": 0, "a.pair[1]": 1,
         "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 0, "a.flag": 0,
         "a.pair[0]": 0, "a.pair[1]": 0,
         "b.__invoke__": 1, "b.b": 1},
    ]


def _multi_endpoint_instance(*, reverse=False):
    contract = _two_endpoint_contract(reverse=reverse)
    return build_atomic_behavior(
        MULTI_ENDPOINT_SOURCE, "multi_endpoint.py", contract,
        _schedule(offers_by_domain={
            "a": ("a_left", "a_right"), "b": ("b_in",)}),
        artifact="multi-endpoint", task_contract=contract.digest,
        construction=_facts({"x": 0, "y": 0}))


def test_complete_multi_endpoint_call_and_same_slot_outputs_are_injective():
    instance = _multi_endpoint_instance()
    replay, witness = instance.replay_witness(
        _multi_endpoint_rows(), instance.bind().domain.digest())

    assert replay.broken is None
    first = endpoint_batch_rows(replay.outputs[0])
    assert first == (
        {"a_high": {"data": 5}},
        {"a_low": {"data": 4}},
        {"b_out": {"ob": 1}},
    )
    assert witness.states[1] == {"x": 4, "y": 1}
    assert instance.audit((_multi_endpoint_rows(),)).status \
        is AuditCheckStatus.AGREED
    assert instance.source_audit((_multi_endpoint_rows(),)).status \
        is AuditCheckStatus.AGREED


def test_partial_multi_endpoint_offer_has_no_invented_source_arguments():
    schedule = _schedule(offers_by_domain={
        "a": ("a_left",), "b": ("b_in",)})
    with pytest.raises(Refused) as caught:
        build_atomic_behavior(
            MULTI_ENDPOINT_SOURCE, "partial_multi_endpoint.py",
            _two_endpoint_contract(), schedule,
            construction=_facts({"x": 0, "y": 0}))
    assert caught.value.refusal.kind == "behavior-atomic-partial-input-offer"


def test_endpoint_declaration_order_does_not_change_atomic_observation():
    normal = _multi_endpoint_instance()
    reversed_instance = _multi_endpoint_instance(reverse=True)
    rows = _multi_endpoint_rows()

    assert normal.replay(rows).outputs == reversed_instance.replay(rows).outputs


def test_structured_output_uses_the_shared_recursive_value_binding():
    contract = _structured_output_contract()
    instance = build_atomic_behavior(
        STRUCTURED_OUTPUT_SOURCE, "structured_output.py", contract, _schedule(),
        artifact="structured-output", task_contract=contract.digest,
        construction=_facts({"x": 0, "y": 0}))
    rows = _rows(a0=1, b0=1, a1=0, b2=1)

    replay = instance.replay(rows)
    first = endpoint_batch_rows(replay.outputs[0])
    assert first == (
        {"a_out": {"packet": {
            "enabled": True, "pair": [1, 2]}}},
        {"b_out": {"ob": 1}},
    )
    checked = instance.audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED


STRUCTURED_STATE_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.cfg = {"enabled": False, "mode": "idle"}
        self.cells = [1, 2]
        self.y = 0

    def process_a(self, a):
        self.cfg["enabled"] = not self.cfg["enabled"]
        if a:
            self.cfg["mode"] = "run"
        else:
            self.cfg["mode"] = "idle"
        self.cells = [self.cells[1], self.cells[0] + a]
        return {"oa": self.cells[0] + int(self.cfg["enabled"])}

    def process_b(self, b):
        self.y = self.cells[0] + b
        return {"ob": self.y}
"""


def test_boolean_array_enum_and_record_state_use_the_common_value_bindings():
    cfg = ValueType.record({
        "enabled": ValueType.boolean(),
        "mode": ValueType.enumeration(("idle", "run")),
    })
    cells = ValueType.array(2, ValueType.integer(U4))
    instance = build_atomic_behavior(
        STRUCTURED_STATE_SOURCE, "structured_atomic.py", _contract(), _schedule(),
        artifact="structured-atomic", task_contract=_contract().digest,
        construction=_facts(
            {"cfg": {"enabled": False, "mode": "idle"},
             "cells": [1, 2], "y": 0},
            unsupported=(("cfg", "declared recursive ValueType"),)),
        value_types={"cfg": cfg, "cells": cells})
    rows = _rows(a0=1, b0=0, a1=0, b2=1)

    replay, witness = instance.replay_witness(
        rows, instance.bind().domain.digest())
    assert replay.broken is None
    first_state = witness.states[1]
    assert instance.state_bindings["cfg"].reconstruct(first_state) == {
        "enabled": True, "mode": "run"}
    assert first_state["cells"] == [2, 2]
    assert first_state["y"] == 1
    checked = instance.audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED


COLLISION_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.shared = 0

    def process_a(self, a):
        if a == 1:
            self.shared = a
        return {"oa": self.shared}

    def process_b(self, b):
        if b == 1:
            self.shared = b
        return {"ob": self.shared}
"""


DOUBLE_FAILURE_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0

    def process_a(self, a):
        self.x = 1 // a
        return {"oa": self.x}

    def process_b(self, b):
        self.y = 1 // b
        return {"ob": self.y}
"""


SINGLE_FAILURE_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0

    def process_a(self, a):
        self.x = 1 // a
        return {"oa": self.x}

    def process_b(self, b):
        self.y = b
        return {"ob": self.y}
"""


def test_collision_input_is_refused_instead_of_removed_from_the_domain():
    with pytest.raises(Refused) as caught:
        build_atomic_behavior(
            COLLISION_SOURCE, "collision.py", _contract(), _schedule(),
            construction=_facts({"shared": 0}))
    assert caught.value.refusal.kind == "behavior-atomic-transition-not-total"
    assert any(row["a.a"] == 1 and row["b.b"] == 1
               for row in caught.value.refusal.detail["witness"])


def test_two_coincident_failures_are_refused_not_constrained_away():
    with pytest.raises(Refused) as caught:
        build_atomic_behavior(
            DOUBLE_FAILURE_SOURCE, "double_failure.py", _contract(), _schedule(),
            construction=_facts({"x": 0, "y": 0}))
    assert caught.value.refusal.kind == "behavior-atomic-transition-not-total"


@pytest.mark.parametrize("outcome", ("unsat", "unknown", "late-unsat"))
def test_atomic_totality_uses_the_remaining_owner_budget(monkeypatch, outcome):
    from types import SimpleNamespace

    from behave.sim import analysis_budget

    now, settings = [10.0], []
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(atomic_exact._AtomicContext, "__init__", lambda *_a, **_kw: None)

    def formula(_instance):
        now[0] += 3  # Construction is part of this lifetime.
        return SimpleNamespace(constraint=True, invalid=False)

    def check():
        if outcome == "late-unsat":
            now[0] += 2
        return z3.unknown if outcome == "unknown" else z3.unsat

    monkeypatch.setattr(atomic_exact, "_AtomicFormula", formula)
    monkeypatch.setattr(z3, "Solver", lambda: SimpleNamespace(
        add=lambda *_args: None, set=lambda **kwargs: settings.append(kwargs),
        check=check, reason_unknown=lambda: "totality-test-budget"))
    with analysis_budget.analysis_scope(5):
        if outcome == "late-unsat":
            with pytest.raises(analysis_budget.AnalysisStopped):
                atomic_exact.AtomicBehaviorInstance()
        elif outcome == "unknown":
            with pytest.raises(Refused) as caught:
                atomic_exact.AtomicBehaviorInstance()
            assert caught.value.refusal.kind == "behavior-atomic-totality-unresolved"
            assert caught.value.refusal.detail["reason"] == "totality-test-budget"
        else:
            atomic_exact.AtomicBehaviorInstance()
    assert settings == [{"timeout": 2000}]


def test_one_failure_discards_all_coincident_outputs_and_holds_state():
    instance = build_atomic_behavior(
        SINGLE_FAILURE_SOURCE, "single_failure.py", _contract(), _schedule(),
        construction=_facts({"x": 0, "y": 0}))
    rows = _rows(a0=0, b0=1, a1=0, b2=0)

    replay, witness = instance.replay_witness(
        rows, instance.bind().domain.digest())
    assert replay.error == "DivZero"
    assert all(output.count == 0 for output in replay.outputs)
    assert witness.states == (
        {"x": 0, "y": 0},
        {"x": 0, "y": 0},
        {"x": 0, "y": 0},
        {"x": 0, "y": 0},
    )
    checked = instance.audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


RESET_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0

    def reset_a(self):
        self.x = 1

    def reset_b(self):
        self.y = 2

    def process_a(self, a):
        self.x = self.x + a
        return {"oa": self.x}

    def process_b(self, b):
        self.y = self.y + b
        return {"ob": self.y}
"""


BACKPRESSURE_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0

    def _a_can_accept(self):
        if self.x == 0:
            return True
        return False

    def a_ready(self):
        return self._a_can_accept()

    def process_a(self, a):
        self.x = 1
        return {"oa": a}

    def process_b(self, b):
        self.y = self.y + b
        return {"ob": self.y}
"""


CLOCK_ONLY_BACKPRESSURE_SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 0

    def b_ready(self):
        return self.y == 0

    def process_a(self, a):
        self.x = self.x + a
        return {"oa": self.x}

    def process_b(self):
        self.y = 1
        return {"ob": self.y}
"""


CROSS_DOMAIN_PIPELINE_SOURCE = """
def mask(value, bits):
    return value & ((1 << bits) - 1)


class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.source = 0
        self.pipe_one = 0
        self.pipe_two = 0

    def process_a(self, a):
        self.source = mask(a, 2)
        return {"oa": a}

    def process_b(self):
        old_one = self.pipe_one
        old_two = self.pipe_two
        old_source = self.source
        self.pipe_one = mask(old_source, 2)
        self.pipe_two = mask(old_one, 2)
        if old_two:
            return {"ob": mask(old_source, 4)}
        return None
"""


def test_cross_domain_state_keeps_a_later_output_arm():
    schedule = _schedule(
        offers_by_domain={"a": ("a_in",), "b": ()}, n_slots=10)
    instance = build_atomic_behavior(
        CROSS_DOMAIN_PIPELINE_SOURCE, "cross_domain_pipeline.py",
        _clock_only_contract(), schedule,
        construction=_facts({"source": 0, "pipe_one": 0, "pipe_two": 0}))

    _state, output, _hits, error = atomic_exact.interp.run_step(
        instance.components["b"].proc, {},
        {"source": 3, "pipe_one": 1, "pipe_two": 1})
    assert not error
    assert output.transactions == [{"ob": 3}]


def test_old_state_ready_rejects_an_input_when_not_enabled():
    instance = build_atomic_behavior(
        BACKPRESSURE_SOURCE, "backpressure.py", _contract(), _schedule(),
        construction=_facts({"x": 0, "y": 0}))
    rows = _rows(a0=1, b0=1, a1=0, b2=1)
    rows[1]["a.__invoke__"] = 0

    replay, witness = instance.replay_witness(
        rows, instance.bind().domain.digest())
    assert replay.broken is None
    assert witness.states[1] == {"x": 1, "y": 1}
    assert witness.states[2] == {"x": 1, "y": 1}
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED

    forged = [dict(row) for row in rows]
    forged[1]["a.__invoke__"] = 1
    broken = instance.replay(forged)
    assert "behavior-atomic-invocation-readiness-mismatch" in broken.broken

    feasible, symbolic = instance.bind().feasible()
    assert feasible.name == "YES"
    assert instance.replay(symbolic).broken is None


def test_ready_control_flow_is_in_the_catalogue_and_all_three_hit_paths():
    instance = build_atomic_behavior(
        BACKPRESSURE_SOURCE, "backpressure_goals.py", _contract(), _schedule(),
        construction=_facts({"x": 0, "y": 0}))
    ready_goals = ir.goals_of(instance.components["a"].ready.body)
    manifest = instance.frontend_catalogue_manifest()
    assert ready_goals
    assert ready_goals <= set(manifest)

    rows = _rows(a0=1, b0=1, a1=0, b2=1)
    rows[1]["a.__invoke__"] = 0
    replay, witness = instance.replay_witness(
        rows, instance.bind().domain.digest())
    assert replay.broken is None
    assert ready_goals <= set().union(*(set(hits) for hits in witness.hit_sets))
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED

    removed = next(iter(ready_goals))
    incomplete = CatalogueManifest(
        [manifest[goal] for goal in manifest if goal != removed],
        occurrences=[
            (key, goal) for key, goal in manifest.occurrences
            if goal != removed
        ])
    with pytest.raises(
            Refused,
            match="catalogue-behavior-atomic-frontend-unknown-goal"):
        build_atomic_behavior(
            BACKPRESSURE_SOURCE, "backpressure_goals.py", _contract(),
            _schedule(), construction=_facts({"x": 0, "y": 0}),
            complete_catalogue=incomplete)

    silent_schedule = _schedule(
        offers_by_domain={"a": (), "b": ("b_in",)})
    silent = build_atomic_behavior(
        BACKPRESSURE_SOURCE, "backpressure_goals.py", _contract(),
        silent_schedule, construction=_facts({"x": 0, "y": 0}))
    silent_rows = _rows(a0=0, b0=1, a1=0, b2=1)
    for row in silent_rows:
        row["a.__invoke__"] = 0
    replay, witness = silent.replay_witness(
        silent_rows, silent.bind().domain.digest())
    assert replay.broken is None
    assert ready_goals & set(witness.hit_sets[0])
    assert ready_goals & set(witness.hit_sets[1])
    assert silent.audit((silent_rows,)).status is AuditCheckStatus.AGREED
    assert silent.source_audit((silent_rows,)).status is AuditCheckStatus.AGREED


def test_noncallable_ready_member_is_not_treated_as_constant_true():
    source = BACKPRESSURE_SOURCE.replace(
        "    def _a_can_accept(self):\n"
        "        if self.x == 0:\n"
        "            return True\n"
        "        return False\n\n",
        "").replace(
            "    def a_ready(self):\n"
            "        return self._a_can_accept()\n\n",
            "    a_ready = 1\n\n")
    with pytest.raises(Refused, match="behavior-atomic-ready-not-callable"):
        build_atomic_behavior(
            source, "noncallable_ready.py", _contract(), _schedule(),
            construction=_facts({"x": 0, "y": 0}))


@pytest.mark.parametrize(("method", "kind"), (
    (
        (
            "    def a_ready(self):\n"
            "        self.x = 1\n"
            "        return True\n\n"
        ),
        "behavior-atomic-ready-mutates-state",
    ),
    (
        (
            "    def a_ready(self):\n"
            "        return 1\n\n"
        ),
        "behavior-atomic-ready-unsupported",
    ),
    (
        (
            "    def a_ready(self):\n"
            "        return (1 // self.x) == 0\n\n"
        ),
        "behavior-atomic-transition-not-total",
    ),
))
def test_ready_is_a_total_read_only_boolean_observation(method, kind):
    source = SOURCE.replace(
        "    def process_a(self, a):\n",
        method + "    def process_a(self, a):\n")
    with pytest.raises(Refused, match=kind):
        build_atomic_behavior(
            source, "invalid_ready.py", _contract(), _schedule(),
            construction=_facts({"x": 0, "y": 8}))


def test_old_state_ready_also_gates_an_inputless_clock_handler():
    schedule = _schedule(offers_by_domain={
        "a": ("a_in",), "b": ("b_in",)})
    instance = build_atomic_behavior(
        CLOCK_ONLY_BACKPRESSURE_SOURCE, "clock_only_backpressure.py",
        _clock_only_contract(), schedule,
        construction=_facts({"x": 0, "y": 0}))
    rows = (
        {"a.__invoke__": 1, "a.a": 1, "b.__invoke__": 1},
        {"a.__invoke__": 1, "a.a": 1, "b.__invoke__": 0},
        {"a.__invoke__": 0, "a.a": 0, "b.__invoke__": 0},
    )

    replay, witness = instance.replay_witness(
        rows, instance.bind().domain.digest())
    assert replay.broken is None
    assert witness.states[-1] == {"x": 2, "y": 1}
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED


def test_independent_reset_and_untriggered_domain_hold_in_the_atomic_product():
    timings = (
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
    )
    schedule = GlobalSchedule(timings, (
        GlobalEventSlot(0, ("a", "b"), resets=("a", "b")),
        GlobalEventSlot(2, event=api.EventSlot(
            offers=("a_in",), ticks=("a",))),
        GlobalEventSlot(3, event=api.EventSlot(
            offers=("b_in",), ticks=("b",))),
        GlobalEventSlot(4, event=api.EventSlot(
            offers=("a_in",), ticks=("a",))),
        GlobalEventSlot(6, event=api.EventSlot(
            offers=("a_in",), ticks=("a", "b"), resets=("b",))),
    ))
    instance = build_atomic_behavior(
        RESET_SOURCE, "independent_reset.py", _contract(), schedule,
        construction=_facts({"x": 0, "y": 0}))
    rows = (
        {"a.__invoke__": 0, "a.a": 0,
         "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 1, "a.a": 1,
         "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 0, "a.a": 0,
         "b.__invoke__": 1, "b.b": 1},
        {"a.__invoke__": 1, "a.a": 1,
         "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 1, "a.a": 1,
         "b.__invoke__": 0, "b.b": 0},
    )

    replay, witness = instance.replay_witness(
        rows, instance.bind().domain.digest())
    assert replay.broken is None
    reset_goals = set().union(*(
        ir.goals_of(component.reset.body)
        for component in instance.components.values()
        if component.reset is not None
    ))
    assert reset_goals
    assert reset_goals.isdisjoint(instance.frontend_catalogue_manifest())
    assert reset_goals.isdisjoint(
        set().union(*(set(hits) for hits in witness.hit_sets)))
    assert witness.states == (
        {"x": 0, "y": 0},
        {"x": 0, "y": 0},
        {"x": 1, "y": 0},
        {"x": 1, "y": 1},
        {"x": 2, "y": 1},
        {"x": 3, "y": 2},
    )
    checked = instance.audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED
