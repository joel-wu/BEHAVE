"""The task-declared finite output carrier reaches every Behavior boundary.

These are production-path regressions.  They deliberately start at
``behavior.instance.lower`` with a task-owned ``TransactionSchema`` rather
than constructing an IR procedure or a solver formula by hand.  A task type
may reject a source statically or a concrete replay dynamically; what it may
not do is let an out-of-carrier transaction become bounded evidence.
"""

from behave.sim.symbolic import closure_proof

import pickle

import pytest

from behave.sim.behavior import instance as behavior_instance
from behave.sim.symbolic import ir, ranges
from behave.sim.behavior import residual as residual_mod
from behave.sim.behavior import signature as signature_mod
from behave.sim.behavior.lowering import LoweringError, lower_procedure
from behave.sim.behavior.output_boundary import (
    OutputBoundaryError,
    output_boundary_bounds,
)
from behave.sim.symbolic.plan import plan_encoding
from behave.sim.behavior.signature import (
    OutputContractError,
    ProcedureSignature,
    output_contract_evidence,
)
from behave.sim.core.refusal import Refused
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema


def _schema(width=1, signed=False):
    return TransactionSchema(
        [Endpoint("a", "input", 1),
         Endpoint("y", "output", width, signed)],
        source="declared by the test task",
        authority=Authority.TASK,
        reading=Reading.ELABORATED)


def _source(result):
    return ("class M:\n"
            "    def reset(self): pass\n"
            "    def process(self, a):\n"
            f"        return {result}\n")


def _lower(result, *, width=1, signed=False):
    return behavior_instance.lower(
        _source(result), _schema(width, signed), bound=1)


def _replay(instance, value=0):
    return instance.bind(1).replay([{"a": value}])


def _assert_output_refused(result, *, width=1, signed=False, value=0):
    """Accept an early typed refusal or the production replay's broken result.

    A constant invalid return can be discharged before an IR is built.  A
    path-dependent one needs the symbolic/concrete boundary later.  Both are
    legitimate placements of the same fail-closed decision; an exception that
    is not one of the two typed refusal classes is not.
    """
    try:
        instance = _lower(result, width=width, signed=signed)
    except (LoweringError, Refused):
        return
    replay = _replay(instance, value)
    assert replay.broken, (
        f"the task declares y:{'s' if signed else 'u'}{width}, but replay "
        f"returned {replay.outputs!r} without refusing")


def test_a_direct_input_output_keeps_the_task_owned_input_range():
    instance = _lower("{'y': a}")

    assert instance.proc.state_ranges["a"] == ranges.Range(0, 1)
    assert _replay(instance, 0).outputs[0].transactions == [{"y": 0}]
    assert _replay(instance, 1).outputs[0].transactions == [{"y": 1}]


@pytest.mark.parametrize(
    "result",
    ["{'y': 2}", "[{'y': 0}, {'y': 2}]"],
    ids=["mapping", "list-batch"],
)
def test_an_active_unsigned_one_bit_output_cannot_escape_its_carrier(result):
    """The original P0, in both source batch forms."""
    _assert_output_refused(result)


@pytest.mark.parametrize(
    "result,transactions",
    [("{'y': 1}", [{"y": 1}]),
     ("[{'y': 0}, {'y': 1}]", [{"y": 0}, {"y": 1}])],
    ids=["mapping", "list-batch"],
)
def test_valid_mapping_and_list_batch_outputs_survive_unchanged(
        result, transactions):
    instance = _lower(result)
    replay = _replay(instance)
    assert not replay.broken
    assert replay.error == ""
    assert replay.outputs[0].transactions == transactions


def test_each_source_return_starts_one_fresh_output_batch():
    """Guarded loop copies cannot look like repeated transaction appends."""
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        row = [a, a, a, a]
        for j in range(4):
            if row[j] == 0:
                return {"y": j}
            row[j] = 0
        return {"y": 0}
"""
    instance = behavior_instance.lower(source, _schema(width=2), bound=1)
    after = ranges.graph_post(instance.proc.graph, instance.env)

    assert after[instance.proc.count] == ranges.Range(0, 1)
    assert instance.proc.output_capacity == 1
    assert _replay(instance, 0).outputs[0].transactions == [{"y": 0}]
    assert _replay(instance, 1).outputs[0].transactions == [{"y": 0}]


def test_a_batch_return_beyond_the_state_horizon_needs_no_storage():
    source = """class M:
    def reset(self):
        self.count = 0

    def process(self, a):
        self.count += 1
        if self.count != 128:
            return None
        self.count = 0
        return [{"y": a} for _ in range(4)]
"""
    short = behavior_instance.lower(source, _schema(), bound=2)
    assert short.proc.output_capacity == 1
    replay = short.bind(2).replay([{"a": 1}, {"a": 1}])
    assert not replay.broken
    assert [step.transactions for step in replay.outputs] == [[], []]


@pytest.mark.parametrize(
    "width,signed,value",
    [(2, False, 0), (2, False, 3),
     (2, True, -2), (2, True, 1)],
    ids=["u2-low", "u2-high", "s2-low", "s2-high"],
)
def test_unsigned_and_signed_boundary_values_are_admitted(width, signed, value):
    instance = _lower(f"{{'y': {value}}}", width=width, signed=signed)
    replay = _replay(instance)
    assert not replay.broken
    assert replay.outputs[0].transactions == [{"y": value}]


@pytest.mark.parametrize(
    "width,signed,value",
    [(2, False, -1), (2, False, 4),
     (2, True, -3), (2, True, 4)],
    ids=["u2-below", "u2-above", "s2-below", "s2-above"],
)
def test_unsigned_and_signed_values_outside_the_declared_bounds_are_refused(
        width, signed, value):
    _assert_output_refused(
        f"{{'y': {value}}}", width=width, signed=signed)


@pytest.mark.parametrize(("pattern", "logical"), [(2, -2), (3, -1)])
def test_signed_source_patterns_are_projected_to_logical_outputs(
        pattern, logical):
    from behave.sim.behavior import audit

    source = _source(f"{{'y': {pattern}}}")
    instance = behavior_instance.lower(
        source, _schema(width=2, signed=True), bound=1)
    replay = instance.bind(1).replay([{"a": 0}])
    assert not replay.broken
    assert replay.outputs[0].transactions == [{"y": logical}]

    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    assert audit.model_audit(
        instance.proc, namespace["M"], [[{"a": 0}]]) == []


def test_computed_mapping_uses_the_same_signed_output_projection():
    """A DictComp is a return spelling, not a second output boundary."""
    schema = TransactionSchema(
        [Endpoint("a", "input", 2),
         Endpoint("y", "output", 2, True)],
        source="declared by the test task",
        authority=Authority.TASK,
        reading=Reading.ELABORATED)
    source = _source("{f'y': (a & 3) for _ in range(1)}")

    instance = behavior_instance.lower(source, schema, bound=1)
    replay = instance.bind(1).replay([{"a": 3}])

    assert not replay.broken
    assert replay.outputs[0].transactions == [{"y": -1}]


def test_computed_mapping_does_not_mask_an_out_of_domain_output():
    schema = TransactionSchema(
        [Endpoint("a", "input", 2),
         Endpoint("y", "output", 2, True)],
        source="declared by the test task",
        authority=Authority.TASK,
        reading=Reading.ELABORATED)
    source = _source("{f'y': (a + 4) for _ in range(1)}")

    with pytest.raises((LoweringError, Refused)):
        behavior_instance.lower(source, schema, bound=1)


def test_mutable_mapping_field_keeps_its_signed_boundary_range():
    """A record carrier does not hide the range proved at its source write."""
    schema = TransactionSchema(
        [Endpoint("a", "input", 2),
         Endpoint("y", "output", 2, True)],
        source="declared by the test task",
        authority=Authority.TASK,
        reading=Reading.ELABORATED)
    source = """def mask(value, width):
    return value & ((1 << width) - 1)


class M:
    def reset(self):
        pass

    def process(self, a):
        out = {}
        for _ in range(1):
            out["y"] = mask(a + 2, 2)
        return out
"""

    instance = behavior_instance.lower(source, schema, bound=1)
    replay = instance.bind(1).replay([{"a": 1}])

    assert not replay.broken
    assert replay.outputs[0].transactions == [{"y": -1}]


def test_mutable_mapping_range_does_not_mask_an_invalid_signed_output():
    schema = TransactionSchema(
        [Endpoint("a", "input", 2),
         Endpoint("y", "output", 2, True)],
        source="declared by the test task",
        authority=Authority.TASK,
        reading=Reading.ELABORATED)
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        out = {}
        for _ in range(1):
            out["y"] = a + 4
        return out
"""

    with pytest.raises((LoweringError, Refused)):
        behavior_instance.lower(source, schema, bound=1)


def test_source_boolean_does_not_implicitly_satisfy_an_integer_task_port():
    """A one-bit integer task field is not a Boolean declaration.

    Until the task ValueType/flattening contract can declare a Boolean port,
    returning ``True`` is a typed mismatch rather than an implicit conversion
    to integer one.
    """
    with pytest.raises(LoweringError) as caught:
        _lower("{'y': True}")
    assert caught.value.code == "type-inconsistent"


def test_a_path_missing_from_a_sample_still_blocks_exact_analysis():
    """A finite audit cannot replace the exact output-domain query.

    The only audited trace presents ``a=0`` and observes the valid output zero.
    The other admitted input presents ``a=1`` and produces 2 on a one-bit
    output.  Exact-analysis preflight must therefore fail even though one sampled
    reading agrees.
    """
    try:
        instance = _lower("{'y': a * 2}")
    except (LoweringError, Refused):
        return
    assert not _replay(instance, 0).broken
    assert _replay(instance, 1).broken
    assert instance.well_formed()


def test_a_list_batch_checks_every_active_row_not_only_the_first():
    try:
        instance = _lower("[{'y': 0}, {'y': a * 2}]")
    except (LoweringError, Refused):
        return
    assert not _replay(instance, 0).broken
    assert _replay(instance, 1).broken
    assert instance.well_formed()


def test_a_valid_output_contract_passes_exact_analysis_preflight():
    instance = _lower("{'y': a}")
    assert instance.well_formed() == []


def _force_exact_output_check(monkeypatch):
    from behave.sim.symbolic import procedure_contract

    def require_exact(_proc, _env):
        raise OutputContractError(
            "output-range-unproved", "forced exact-query regression")

    monkeypatch.setattr(procedure_contract, "output_boundary_evidence", lambda _proc: None)
    monkeypatch.setattr(signature_mod, "output_range_evidence", require_exact)


def _stateful_identity():
    return behavior_instance.lower("""class M:
    def reset(self):
        self.previous = 0

    def process(self, a):
        self.previous = (self.previous + a) & 1
        return {"y": a}
""", _schema(), bound=1)


@pytest.mark.parametrize('stateful', [False, True])
def test_output_proof_uses_shared_exact_query_without_assuming_validity(monkeypatch, stateful):
    import z3
    from behave.sim.behavior import exact_solver

    instance = _stateful_identity() if stateful else _lower("{'y': a}")
    _force_exact_output_check(monkeypatch)
    monkeypatch.setattr(signature_mod, '_position_output_evidence', lambda *args: None)
    queries = []
    solve = exact_solver.solve_exact

    def checked(formula, target, timeout_ms):
        assert z3.is_bool(target)
        queries.append(timeout_ms)
        return solve(formula, target, timeout_ms)

    monkeypatch.setattr(exact_solver, 'solve_exact', checked)
    result = output_contract_evidence(instance.proc, instance.env, bound=2, timeout_ms=5000)
    assert result['y']['method'] == 'exact'
    assert len(queries) == 1 and 0 < queries[0] <= 5000


def test_unanswered_shared_output_proof_is_not_an_admission(monkeypatch):
    from types import SimpleNamespace
    from behave.sim.behavior import exact_solver
    from behave.sim.core.query import SolverAnswer

    instance = _lower("{'y': a}")
    _force_exact_output_check(monkeypatch)
    monkeypatch.setattr(exact_solver, 'solve_exact', lambda *args: SimpleNamespace(
        answer=SolverAnswer.UNKNOWN, reason='test-unanswered'))
    with pytest.raises(OutputContractError, match='test-unanswered') as caught:
        output_contract_evidence(instance.proc, instance.env, bound=100)
    assert caught.value.code == 'output-domain-unresolved'


@pytest.mark.solver
@pytest.mark.parametrize("guarded", [False, True])
def test_output_proof_keeps_selected_value_and_sentinel_correlated(guarded):
    # A mask can skip every update. Independent intervals then retain the
    # score sentinel even after a guard rules out the unselected index. The
    # joint-write invariant preserves that relation without whole-step SMT;
    # the unguarded variant must still fail instead of clamping its output.
    source = '''class M:
    def reset(self):
        pass

    def process(self, a):
        score = 2147483647
        pick = -1
        for j in range(4):
            if ((a >> (j + 2)) & 1) == 0:
                value = ((a & 3) + j) & 3
                if value < score:
                    score = value
                    pick = j
GUARD
        return {"y": score}
'''.replace('GUARD', '        checked = 1 << pick' if guarded else '')
    schema = TransactionSchema(
        [Endpoint("a", "input", 6), Endpoint("y", "output", 2)],
        source="declared by the test task", authority=Authority.TASK,
        reading=Reading.ELABORATED)
    if not guarded:
        with pytest.raises(LoweringError) as caught:
            behavior_instance.lower(source, schema, bound=1)
        assert caught.value.code == "output-outside-contract"
        return
    instance = behavior_instance.lower(source, schema, bound=1)
    assert instance.proc.output_ranges["y"]["method"] == "range"
    native = {}
    exec(compile(source, "selection.py", "exec"), native)  # noqa: S102
    for value in range(64):
        model = native["M"]()
        model.reset()
        replay = _replay(instance, value)
        if value >> 2 == 15:
            with pytest.raises(ValueError, match="negative shift"):
                model.process(value)
            assert replay.error == "NegativeShift"
        else:
            assert not replay.broken and not replay.error
            assert replay.outputs[0].transactions == [model.process(value)]


@pytest.mark.solver
@pytest.mark.parametrize("bound", [1, 100, 1000])
def test_stateless_output_proof_encodes_one_call_not_a_run(monkeypatch, bound):
    from behave.sim.symbolic import encoder

    instance = _lower("{'y': a}")
    assert not instance.proc.state
    _force_exact_output_check(monkeypatch)
    calls = []
    original = encoder.encode_step

    def encode(*args, **kwargs):
        calls.append(kwargs["tag"])
        return original(*args, **kwargs)

    def no_run(*_args, **_kwargs):
        pytest.fail("a stateless output proof must not prepare/unroll a finite run")

    monkeypatch.setattr(encoder, "encode_step", encode)
    monkeypatch.setattr(closure_proof, "prepare_positions", no_run)
    monkeypatch.setattr(residual_mod, "unroll", no_run)
    evidence = output_contract_evidence(instance.proc, instance.env, bound=bound)
    assert calls == ["@output-contract"]
    assert evidence["y"]["method"] == "exact"
    assert evidence["y"]["bound"] == bound


@pytest.mark.parametrize("bound", [None, 1, 4])
def test_output_proof_consumes_state_cells_without_rebuilding_a_run(monkeypatch, bound):
    from behave.sim.behavior import exact_solver

    source = '''class M:
    def reset(self):
        self.cells = [0, 0]
    def process(self, a):
        y = self.cells[1]
        self.cells[0] = -a
        self.cells[1] = a
        return {"y": y}
'''
    schema = TransactionSchema(
        [Endpoint("a", "input", 3), Endpoint("y", "output", 3)],
        source="declared by the test task", authority=Authority.TASK,
        reading=Reading.ELABORATED)

    def no_reproof(*_args, **_kwargs):
        pytest.fail("proved independent cells must not trigger whole-run SMT")

    monkeypatch.setattr(exact_solver, "solve_exact", no_reproof)
    instance = behavior_instance.lower(source, schema, bound=bound)
    evidence = instance.proc.output_ranges["y"]
    if bound == 1:
        # The sole call reads reset's zero. Its existing frontend certificate
        # already suffices, independently of the new state-product boundary.
        assert evidence["method"] == "frontend-write-range"
    else:
        assert evidence["method"] == "range"
        assert evidence["inferred"] == [0, 7]
    values = [7] if bound == 1 else [7, 2, 5, 1]
    replay = instance.bind(len(values)).replay([{"a": value} for value in values])
    assert not replay.error and not replay.broken
    assert [item.transactions for item in replay.outputs] == [
        [{"y": value}] for value in [0, *values[:-1]]]


@pytest.mark.solver
@pytest.mark.parametrize("result", ["{'y': a * 2}", "[{'y': 0}, {'y': a * 2}]"])
def test_stateless_exact_proof_still_rejects_an_unsampled_invalid_output(monkeypatch, result):
    signature = ProcedureSignature.from_schema(_schema(), input_names=("a",))
    proc = lower_procedure(_source(result), outputs=["y"],
                           state_ranges={"a": ranges.Range(0, 1)}, signature=signature)
    assert not proc.state
    _force_exact_output_check(monkeypatch)
    with pytest.raises(OutputContractError) as caught:
        output_contract_evidence(proc, {"a": ranges.Range(0, 1)}, bound=100)
    assert caught.value.code == "output-outside-contract"


@pytest.mark.solver
def test_framing_history_prevents_the_stateless_one_call_shortcut(monkeypatch):
    from behave.sim.behavior.task_boundary import lower_task_behavior

    from .test_input_framing import contract, nested

    source = """class M:
    def reset(self):
        pass
    def process(self, pixel, sof, eof, sol, eol):
        return {"out_pixel": pixel}
"""
    instance, _ = lower_task_behavior(source, "framed.py", contract(nested()), bound=2)
    assert not instance.proc.state and instance.proc.input_framing
    _force_exact_output_check(monkeypatch)
    calls = []
    original = closure_proof.prepare_positions

    def prepare(proc, env, bound, **kwargs):
        calls.append(bound)
        return original(proc, env, bound, **kwargs)

    monkeypatch.setattr(closure_proof, "prepare_positions", prepare)
    evidence = output_contract_evidence(instance.proc, instance.env, bound=2)
    assert calls == [2]
    assert evidence["out_pixel"]["method"] == "position-closure"


@pytest.mark.solver
def test_a_closed_position_output_layer_avoids_a_horizon_wide_query(
        monkeypatch):
    """A proved layer contained in the task carrier is already sufficient."""
    instance = _stateful_identity()

    _force_exact_output_check(monkeypatch)
    monkeypatch.setattr(
        residual_mod, "unroll",
        lambda *_args, **_kwargs: pytest.fail(
            "a closed contained layer must not build the whole run"))

    evidence = output_contract_evidence(instance.proc, instance.env, bound=1)

    assert evidence["y"]["method"] == "position-closure"
    assert evidence["y"]["declared"] == [0, 1]


@pytest.mark.solver
@pytest.mark.parametrize("elapsed", [0.25, 1.0])
def test_output_contract_planning_consumes_one_deadline(monkeypatch, elapsed):
    import time

    instance = _stateful_identity()
    _force_exact_output_check(monkeypatch)
    now = [100.0]
    deadlines = []
    original = closure_proof.prepare_positions

    def prepare(*args, **kwargs):
        deadlines.append(kwargs["_closure_deadline"])
        result = original(*args, **kwargs)
        now[0] += elapsed
        return result

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(closure_proof, "prepare_positions", prepare)
    if elapsed < 1.0:
        evidence = output_contract_evidence(
            instance.proc, instance.env, bound=1, timeout_ms=1000)
        assert evidence["y"]["method"] == "position-closure"
    else:
        with pytest.raises(OutputContractError, match="deadline expired") as caught:
            output_contract_evidence(
                instance.proc, instance.env, bound=1, timeout_ms=1000)
        assert caught.value.code == "output-domain-unresolved"
    assert deadlines == [101.0]


@pytest.mark.solver
@pytest.mark.parametrize('elapsed', [0.25, 1.0])
def test_output_contract_never_renews_the_search_lease(monkeypatch, elapsed):
    import time
    import z3

    from behave.sim.symbolic import encoder
    from behave.sim.search_policy import SearchBudget, SearchPolicy, SearchStopped, search_scope

    instance = _lower("{'y': a}")
    _force_exact_output_check(monkeypatch)
    now = [100.0]
    original = encoder.encode_step
    settings = []
    original_set = z3.Solver.set

    def encode(*args, **kwargs):
        encoded = original(*args, **kwargs)
        now[0] += elapsed
        return encoded

    def configure(solver, *args, **kwargs):
        if args and args[0] == 'timeout':
            settings.append(args[1])
        return original_set(solver, *args, **kwargs)

    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(encoder, 'encode_step', encode)
    monkeypatch.setattr(z3.Solver, 'set', configure)
    # Two directions share two seconds; this direction owns only one second.
    lease = SearchBudget(SearchPolicy(time_budget_s=2)).direction(2)
    with search_scope(lease):
        if elapsed < 1:
            result = output_contract_evidence(instance.proc, instance.env, bound=100, timeout_ms=900000)
            assert result['y']['method'] == 'exact'
            assert settings[-1] == 750
        else:
            with pytest.raises(SearchStopped, match='time-budget-exhausted'):
                output_contract_evidence(instance.proc, instance.env, bound=100, timeout_ms=900000)
            assert not settings


def test_an_invalid_output_is_rejected_not_assumed_away():
    signature = ProcedureSignature.from_schema(
        _schema(), input_names=("a",))
    with pytest.raises(LoweringError) as caught:
        lower_procedure(
            _source("{'y': 2}"), outputs=["y"],
            state_ranges={"a": ranges.Range(0, 1)}, signature=signature)
    assert caught.value.code == "output-outside-contract"


def test_the_declared_bound_controls_a_state_dependent_output_proof():
    source = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        self.n += 1
        return {"y": 0 if self.n < 2 else 2}
"""
    one = behavior_instance.lower(source, _schema(), bound=1)
    assert _replay(one).outputs[0].transactions == [{"y": 0}]
    with pytest.raises(LoweringError) as caught:
        behavior_instance.lower(source, _schema(), bound=2)
    assert caught.value.code == "output-outside-contract"


def test_a_conditional_array_update_retains_its_output_range():
    source = """def mask(value, width):
    return value & ((1 << width) - 1)


class M:
    def reset(self):
        self.mem = [0] * 16

    def process(self, write, addr, side, data):
        addr = addr & 15
        if write:
            self.mem[addr] = mask(data, 32)
            return None
        return {"y": self.mem[addr], "side_out": mask(side, 1)}
"""
    schema = TransactionSchema(
        [Endpoint("write", "input", 1),
         Endpoint("addr", "input", 4),
         Endpoint("side", "input", 1),
         Endpoint("data", "input", 32),
         Endpoint("y", "output", 32),
         Endpoint("side_out", "output", 1)],
        source="declared by the test task",
        authority=Authority.TASK,
        reading=Reading.ELABORATED)
    instance = behavior_instance.lower(source, schema, bound=2)
    replay = instance.bind(2).replay([
        {"write": 1, "addr": 3, "side": 0, "data": 15},
        {"write": 0, "addr": 3, "side": 1, "data": 0},
    ])

    assert not replay.broken
    assert [step.transactions for step in replay.outputs] == [
        [], [{"y": 15, "side_out": 1}]]


def test_state_and_output_names_do_not_collide_between_steps():
    source = """class M:
    def reset(self):
        self.y = 0

    def process(self, a):
        self.y += 1
        return {"y": self.y & 1}
"""
    instance = behavior_instance.lower(source, _schema(), bound=2)
    replay = instance.bind(2).replay([{"a": 0}, {"a": 0}])
    assert not replay.broken
    assert [step.transactions for step in replay.outputs] == [
        [{"y": 1}], [{"y": 0}]]
    # Building the exact two-step formula exercises the separate state_post
    # channel; the old merged post changed state y into the output buffer.
    from behave.sim.behavior import residual

    run = residual.unroll(instance.proc, instance.env, 2)
    assert len(run.encodings) == 2


def test_a_nested_source_value_cannot_satisfy_a_scalar_output_type():
    with pytest.raises(LoweringError) as caught:
        _lower("{'y': [0, 1]}")
    assert caught.value.code == "declared-value-shape-mismatch"


def test_a_goal_slice_cannot_inherit_a_full_output_boundary_proof():
    instance = _lower("{'y': a}")
    sliced = instance.proc.derived(
        body=instance.proc.body, boundary_complete=False)
    with pytest.raises(OutputContractError) as caught:
        output_contract_evidence(sliced, instance.env, bound=1)
    assert caught.value.code == "output-boundary-incomplete"


def test_the_frontend_output_boundary_survives_worker_serialization():
    instance = _lower("{'y': a}")
    restored = pickle.loads(pickle.dumps(instance.proc))

    assert output_contract_evidence(
        restored, instance.env, bound=1)["y"] == {
            "inferred": [0, 1],
            "declared": [0, 1],
        "method": "frontend-write-range",
    }


def test_the_frontend_certificate_exposes_physical_output_carriers():
    instance = _lower("[{'y': 1}, {'y': 1}]")

    assert output_boundary_bounds(instance.proc) == {
        "#count": (0, 2),
        # The inactive physical suffix is initialized to zero.
        "y": (0, 1),
    }


def test_count_proof_survives_an_unproved_output_leaf(monkeypatch):
    from behave.sim.behavior.output_boundary import (
        OutputBoundaryBuilder,
        output_boundary_evidence,
    )

    original = OutputBoundaryBuilder.record_values

    def unknown_leaf(self, binding, values, range_of, source=None):
        return original(self, binding, values, lambda _expr: None, source)

    monkeypatch.setattr(OutputBoundaryBuilder, 'record_values', unknown_leaf)
    instance = _lower("[{'y': a}, {'y': a}]")
    proc = instance.proc
    assert output_boundary_evidence(proc) is None
    assert output_boundary_bounds(proc) == {'#count': (0, 2)}
    restored = pickle.loads(pickle.dumps(proc))
    assert output_boundary_evidence(restored) is None
    assert output_boundary_bounds(restored) == {'#count': (0, 2)}
    assert _replay(instance, 1).outputs[0].transactions == [{'y': 1}, {'y': 1}]
    # Count evidence must not certify an invalid value or changed body.
    _assert_output_refused("{'y': a + a}", value=1)
    changed = proc.derived(body=ir.Skip(), graph=None)
    assert output_boundary_bounds(changed) is None
    object.__setattr__(changed, '_output_boundary', proc._output_boundary)
    with pytest.raises(OutputBoundaryError, match='body changed'):
        output_boundary_bounds(changed)


def test_missing_return_proof_cannot_produce_a_count_certificate(monkeypatch):
    from behave.sim.behavior.output_boundary import OutputBoundaryBuilder

    original = OutputBoundaryBuilder.record_return
    monkeypatch.setattr(OutputBoundaryBuilder, 'record_return',
                        lambda self, node, plan: original(self, node, None))
    assert output_boundary_bounds(_lower("{'y': a}").proc) is None


@pytest.mark.parametrize('multiple_returns', [False, True])
def test_proved_output_survives_an_unproved_sibling(monkeypatch, multiple_returns):
    from behave.sim.behavior.output_boundary import (
        OutputBoundaryBuilder,
        output_boundary_evidence,
    )

    original = OutputBoundaryBuilder.record_values
    visits = {}

    def partial_ranges(self, binding, values, range_of, source=None):
        calls = visits.setdefault(self, [])
        calls.append(binding.name)
        # Only the first z occurrence lacks a proof. A later proved write must
        # not turn the earlier unknown path into a certificate (or zero).
        if binding.name == 'z' and calls.count('z') == 1:
            range_of = lambda _expr: None
        return original(self, binding, values, range_of, source)

    monkeypatch.setattr(OutputBoundaryBuilder, 'record_values', partial_ranges)
    schema = TransactionSchema(
        [Endpoint('a', 'input', 1), Endpoint('y', 'output', 1),
         Endpoint('z', 'output', 1)], source='independent output proofs',
        authority=Authority.TASK, reading=Reading.ELABORATED)
    source = _source("{'y': a, 'z': a}")
    if multiple_returns:
        source = source.replace("        return", "        if a:\n            return")
        source += "        return {'y': 1, 'z': 1}\n"
    instance = behavior_instance.lower(source, schema, bound=1)
    for proc in (instance.proc, pickle.loads(pickle.dumps(instance.proc))):
        assert output_boundary_evidence(proc) is None
        assert output_boundary_bounds(proc) == {'#count': (0, 1), 'y': (0, 1)}
    for a in (0, 1):
        replay = instance.bind(1).replay([{'a': a}])
        expected = a if a or not multiple_returns else 1
        assert not replay.broken
        assert replay.outputs[0].transactions == [{'y': expected, 'z': expected}]


def test_certified_stateful_outputs_need_no_tighter_position_carrier():
    source = '''class M:
    def reset(self): self.x = 0
    def process(self, a):
        y = self.x
        self.x = a
        return {'y': y}
'''
    instance = behavior_instance.lower(source, _schema(), bound=3)
    planned = closure_proof.plan_positions(instance.proc, instance.env, 3)
    assert planned.complete
    assert [layer['y'].range for layer in planned.output_layers] == [ranges.Range(0, 1)] * 3
    assert [layer['x'].range for layer in planned.state_layers] == [
        ranges.Range(0, 0), *[ranges.Range(0, 1)] * 3]
    trace = [{'a': 1}, {'a': 0}, {'a': 1}]
    bound = instance.bind(3)
    replay = bound.replay(trace)
    assert not replay.broken
    assert [row.transactions for row in replay.outputs] == [
        [{'y': 0}], [{'y': 1}], [{'y': 0}]]
    assert bound.audit([trace]).as_dict() == {'status': 'agreed'}


def test_a_changed_body_cannot_reuse_its_output_carriers():
    instance = _lower("{'y': a}")
    instance.proc.body = ir.Skip()

    with pytest.raises(OutputBoundaryError) as caught:
        output_boundary_bounds(instance.proc)

    assert caught.value.code == "output-boundary-body-changed"


def test_position_closure_reuses_the_sealed_output_proof(monkeypatch):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        if a < 1:
            return {"y": 0}
        return {"y": 2}
"""
    instance = behavior_instance.lower(source, _schema(width=2), bound=1)

    from behave.sim.symbolic import exact_solve

    monkeypatch.setattr(
        exact_solve, "solve_exact",
        lambda *_args, **_kwargs: pytest.fail(
            "a sealed output carrier must not be re-proved by SMT"))

    planned = closure_proof.plan_positions(instance.proc, instance.env, 1)

    assert planned.complete
    assert planned.output_layers[0]["#count"].range == ranges.Range(0, 1)
    assert planned.output_layers[0]["y"].range == ranges.Range(0, 2)


def test_a_sealed_stateless_output_needs_no_symbolic_closure_encoding(
        monkeypatch):
    """The independent output certificate is already the required proof."""

    from behave.sim.symbolic import encoder as encoder_mod

    # The active abstract value is the point 1, while the physical output
    # carrier also contains the inactive zero padding.  Keep the independently
    # sealed carrier instead of inventing a narrower closure obligation.
    instance = _lower("{'y': 1}")

    def unexpected(*_args, **_kwargs):
        pytest.fail("a sealed stateless boundary must not encode a closure step")

    monkeypatch.setattr(encoder_mod, "_encode_boundary_step", unexpected)
    monkeypatch.setattr(encoder_mod, "_encode_derived_step", unexpected)

    planned = closure_proof.plan_positions(instance.proc, instance.env, 1)

    assert planned.complete, planned.unresolved
    assert planned.output_layers[0]["y"].range == ranges.Range(0, 1)


def test_an_exact_normal_state_literal_needs_no_datapath_closure_encoding(
        monkeypatch):
    """A path-wise CFG certificate is independent of range propagation."""

    from behave.sim.symbolic import encoder as encoder_mod

    source = """class M:
    def reset(self):
        self.pending = 0

    def process(self, a):
        irrelevant = a * 173
        self.pending = 1
        return {"y": irrelevant & 1}
"""
    instance = behavior_instance.lower(source, _schema(), bound=1)

    def unexpected(*_args, **_kwargs):
        pytest.fail("an exact normal-state source must not encode the datapath")

    monkeypatch.setattr(encoder_mod, "_encode_boundary_step", unexpected)
    monkeypatch.setattr(encoder_mod, "_encode_derived_step", unexpected)

    planned = closure_proof.plan_positions(instance.proc, instance.env, 1)

    assert planned.complete, planned.unresolved
    assert planned.state_layers[1]["pending"].range == ranges.Range(1, 1)


@pytest.mark.parametrize('uses_loop_result', [False, True],
                         ids=['unrelated-output', 'loop-dependent-output'])
def test_an_unrelated_retained_loop_does_not_erase_a_state_certificate(
        monkeypatch, uses_loop_result):
    from behave.sim.symbolic import encoder as encoder_mod, slicing
    from behave.sim.symbolic.state_boundary import StateSource, normal_state_sources

    source = """class M:
    def reset(self):
        self.pending = 0

    def process(self, a):
        self.pending = 1
        total = 0
        for i in range(4):
            total = total + i
        return {"y": a + total}
"""
    if not uses_loop_result:
        source = source.replace('a + total', 'a')
    instance = behavior_instance.lower(source, _schema(width=4), bound=1)
    assert normal_state_sources(instance.proc)['pending'] == StateSource('literal', 1)

    def unexpected(*_args, **_kwargs):
        pytest.fail("certified state and output need no boundary encoding")

    selected_roots = []
    original = slicing.for_boundary
    def record_roots(proc, selected):
        selected_roots.append(set(selected))
        assert 'pending' not in selected
        return original(proc, selected)

    monkeypatch.setattr(slicing, 'for_boundary', record_roots)
    if not uses_loop_result:
        monkeypatch.setattr(encoder_mod, "_encode_boundary_step", unexpected)
        monkeypatch.setattr(encoder_mod, "_encode_derived_step", unexpected)

    planned = closure_proof.plan_positions(instance.proc, instance.env, 1)

    assert planned.complete, planned.unresolved
    assert planned.state_layers[1]["pending"].range == ranges.Range(1, 1)
    if uses_loop_result:
        # This narrower *output* carrier is not a sealed source certificate.
        # Independently checking it must not be mistaken for lost state proof.
        assert selected_roots == [{instance.proc.output_vars['y']}]
        assert planned.output_layers[0]['y'].range == ranges.Range(6, 7)
    else:
        assert selected_roots == []


def test_normal_state_certificate_declines_disagreeing_exit_values():
    from behave.sim.symbolic.state_boundary import normal_state_sources

    source = """class M:
    def reset(self):
        self.pending = 0

    def process(self, a):
        if a:
            self.pending = 1
        else:
            self.pending = 2
        return {"y": a}
"""
    instance = behavior_instance.lower(source, _schema(), bound=1)

    assert "pending" not in normal_state_sources(instance.proc)


def test_the_production_procedure_owns_and_preserves_the_task_signature():
    # Use a literal here so this metadata regression is independent of the
    # range pass's ability to prove a forwarded input.  Forwarding is exercised
    # separately by the valid-preflight test above.
    procedure = _lower("{'y': 0}", width=2, signed=True).proc
    assert type(procedure.signature) is ProcedureSignature
    assert procedure.signature.input_bounds() == {"a": (0, 1)}
    assert procedure.signature.output_type("y").bounds == (-2, 1)

    rebuilt = procedure.derived(body=procedure.body)
    assert rebuilt.signature is procedure.signature
    assert rebuilt.signature.as_dict() == procedure.signature.as_dict()


def test_output_width_and_signedness_are_part_of_trace_domain_identity():
    source_result = "{'y': 0}"
    u1 = _lower(source_result, width=1).domain(1)
    u2 = _lower(source_result, width=2).domain(1)
    s2 = _lower(source_result, width=2, signed=True).domain(1)

    assert u1.schema == {
        "inputs": {"a": {"width": 1, "signed": False}},
        "outputs": {"y": {"width": 1, "signed": False}},
    }
    assert len({u1.digest(), u2.digest(), s2.digest()}) == 3
    assert not u1.same_as(u2)
    assert not u2.same_as(s2)
    instances = [_lower(source_result, width=width, signed=signed)
                 for width, signed in ((1, False), (2, False), (2, True))]
    assert len({plan_encoding(item.proc, item.env, bound=1)
                .procedure_fingerprint for item in instances}) == 3
