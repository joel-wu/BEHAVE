"""Position-indexed finite carriers are evidence, not a joined-range alias."""

import pickle
from itertools import pairwise
from types import SimpleNamespace

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic.exact_formula import FormulaDefinition
from behave.sim.behavior import residual
from behave.sim.symbolic import closure_proof, encoder, ir, ranges, typecheck as types
from behave.sim.behavior import instance as behavior_instance
from behave.sim.symbolic import plan as plan_module
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.plan import (
    ExpressionPosition,
    FiniteCarrier,
    OccurrenceCarriers,
    PlanMismatch,
    PositionEncodingPlan,
    plan_positions,
)
from behave.sim.behavior.signature import bind_procedure_contract
from behave.sim.symbolic.uniform_array import UniformArray
from behave.sim.core.instance import BoundedInstance, Feasible
from behave.sim.core.query import SolverAnswer
from behave.sim.core.schema import (
    Authority,
    Endpoint,
    Reading,
    TransactionSchema,
)

GROWING = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = self.x + 1
        y = 0
        if self.x == 2:
            y = 1
        return {"y": y}
"""

ARRAY_STATE = """class M:
    def reset(self):
        self.buf = [0, 1]

    def process(self, a):
        self.buf[0] = self.buf[0] + 1
        return {"y": self.buf[0] & 1}
"""

EMPTY_ARRAY_STATE = """class M:
    def reset(self):
        self.empty = []

    def process(self, a):
        self.empty = []
        return {"y": a & 1}
"""

ARRAY_FROM_INPUT = """class M:
    def reset(self):
        self.buf = [0] * 64

    def process(self, a):
        self.buf[0] = a
        return {"y": self.buf[0]}
"""


def test_array_cell_facts_do_not_escape_the_encoded_state_layer():
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = '''class M:
    def reset(self): self.buf = [0, 0]
    def process(self, a):
        y = self.buf[0] + self.buf[1]
        self.buf = [a, a + 1]
        return {"y": y}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], bound=3)
    plan = plan_positions(proc, proc.state_ranges, 3)
    assert plan.complete, plan.unresolved
    # Layer 1 carries each cell in [0,16], not the stronger correlation
    # established by the prior expression. Its full product must be closed.
    assert (plan.output_layers[1]["y"].lo, plan.output_layers[1]["y"].hi) == (0, 32)

ARRAY_LOOP_FROM_INPUT = """class M:
    def reset(self):
        self.buf = [0] * 8

    def process(self, a):
        buf = self.buf[:]
        for index in range(8):
            buf[index] = a + index
        self.buf = buf
        return {"y": buf[0]}
"""

LARGE_ARRAY_STATE = """class M:
    def reset(self):
        self.buf = [0] * 65536

    def process(self, index, value):
        self.buf[index] = value
        return {"y": self.buf[index]}
"""


def _schema():
    return TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("y", "output", 1)],
        source="declared by the position-plan test",
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )


def _byte_output_schema():
    return TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("y", "output", 8)],
        source="declared by the position-plan test",
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )


def _large_array_schema():
    return TransactionSchema(
        [
            Endpoint("index", "input", 16),
            Endpoint("value", "input", 8),
            Endpoint("y", "output", 8),
        ],
        source="declared by the large-array position-plan test",
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )


def _instance(bound=3):
    return behavior_instance.lower(GROWING, _schema(), bound=bound)


def _array_instance(bound=3):
    return behavior_instance.lower(ARRAY_STATE, _schema(), bound=bound)


def test_guarded_capacity_layers_stabilize_without_joining_history():
    source = """class M:
    def reset(self):
        self.free = 64
    def process(self, a):
        amount = a & 15
        if amount <= self.free:
            self.free = self.free - amount
        else:
            self.free = 64 + (self.free - amount)
        return {"y": self.free}
"""
    instance = behavior_instance.lower(source, _byte_output_schema(), bound=100)
    bound = instance.bind(100)
    prepared = bound.instance._position_plan.plan
    # Each layer is still independently closed by the symbolic transition.
    assert [(layer["free"].lo, layer["free"].hi)
            for layer in prepared.state_layers[:6]] == [
                (64, 64), (49, 64), (34, 64), (19, 64), (4, 64), (0, 64)]
    assert all((layer["free"].lo, layer["free"].hi) == (0, 64)
               for layer in prepared.state_layers[5:])
    trace = [{"a": (index * 7) & 255} for index in range(100)]
    remaining = 64
    expected = []
    for entry in trace:
        amount = entry["a"] & 15
        remaining = remaining - amount if amount <= remaining else 64 + remaining - amount
        expected.append([{"y": remaining}])
    assert [row.transactions for row in bound.replay(trace).outputs] == expected


@pytest.mark.parametrize("operation,width", [
    ("uwrap", 33), ("swrap", 32), ("usat", 33), ("ssat", 32),
    ("ushl", 33), ("sshl", 32), ("ulshr", 33), ("sashr", 32),
])
def test_narrow_value_hull_preserves_fixed_primitive_result_width(operation, width):
    args = ((ir.Var("a"), ir.Lit(32)) if operation.endswith(("wrap", "sat"))
            else (ir.Var("a"), ir.Lit(0), ir.Lit(32)))
    proc = ir.Procedure((), ("a",), (), ("y",),
                        ir.Assign("y", ir.Op(operation, *args)), output_vars={"y": "y"})
    env = {"a": ranges.Range(0, 3)}
    planned = plan_positions(proc, env, 2)
    assert planned.complete, planned.unresolved
    assert planned.scalar_width == width
    if operation.endswith(("wrap", "sat")):
        assert planned.output_layers[0]["y"].hi == 3
    assert residual.unroll(proc, env, 2, plan=planned)


def test_a_finite_carrier_has_an_injective_partial_inverse():
    carrier = FiniteCarrier.of(ranges.Range(-2, 3))
    images = [carrier.encode(value) for value in range(-2, 4)]

    assert len(set(images)) == 6
    assert [carrier.decode(image) for image in images] == list(range(-2, 4))
    assert carrier.decode(carrier.encode(3) + 1) is None
    with pytest.raises(ValueError):
        carrier.encode(4)


def test_a_wider_shared_encoding_keeps_the_same_partial_inverse():
    carrier = FiniteCarrier(-2, 3, 9)
    images = [carrier.encode(value) for value in range(-2, 4)]

    assert len(set(images)) == 6
    assert [carrier.decode(image) for image in images] == list(range(-2, 4))
    assert carrier.decode(4) is None


def test_a_full_signed_carrier_needs_no_solver_range_proof():
    """A bit-vector's type already proves membership in its signed carrier."""

    value = z3.BitVec("typed_value", 8)

    assert z3.is_true(closure_proof._carrier_membership(
        value, FiniteCarrier(-128, 127, 8)))
    assert not z3.is_true(closure_proof._carrier_membership(
        value, FiniteCarrier(-127, 127, 8)))


def test_a_registered_sign_extension_proves_its_narrow_signed_carrier():
    """Closure can read the exact encoded shape without replaying its cone."""

    source = z3.BitVec("wide_source", 36)
    wrapped = z3.BitVec("registered_wrap", 36)
    definition = FormulaDefinition(
        "signed output boundary",
        wrapped,
        z3.SignExt(4, z3.Extract(31, 0, source)),
    )
    definitions = {definition.lhs.get_id(): definition.rhs}

    assert z3.is_true(closure_proof._carrier_membership(
        wrapped, FiniteCarrier(-(1 << 31), (1 << 31) - 1, 36),
        definitions))
    assert not z3.is_true(closure_proof._carrier_membership(
        source, FiniteCarrier(-(1 << 31), (1 << 31) - 1, 36),
        definitions))


def test_a_position_plan_keeps_the_layers_instead_of_only_their_join():
    instance = _instance()
    plan = plan_positions(instance.proc, instance.env, 3)

    assert type(plan) is PositionEncodingPlan
    assert plan.complete, plan.unresolved
    assert instance.proc.output_vars["y"] != "y", \
        "the fixture must exercise internal-variable to public-port projection"
    assert [(layer["x"].lo, layer["x"].hi)
            for layer in plan.state_layers] == [
                (0, 0), (1, 1), (2, 2), (3, 3)]
    assert [(layer["a"].lo, layer["a"].hi)
            for layer in plan.input_layers] == [(0, 255)] * 3
    assert [(layer["y"].lo, layer["y"].hi)
            for layer in plan.output_layers] == [(0, 1)] * 3
    # The output representation reuses its proved hull; the relation still
    # emits 0, 1, 0. Unlike outputs, exact state layers must not be joined.
    bound = instance.bind(3)
    trace = [{'a': 0}] * 3
    assert [row.transactions for row in bound.replay(trace).outputs] == [
        [{'y': 0}], [{'y': 1}], [{'y': 0}]]
    assert bound.audit([trace]).as_dict() == {'status': 'agreed'}
    assert all(layer for layer in plan.occurrence_layers)
    assert {
        carrier.width
        for layers in (
            plan.state_layers,
            plan.input_layers,
            plan.output_layers,
            plan.occurrence_layers,
        )
        for layer in layers
        for carrier in layer.values()
    } == {plan.scalar_width}


def test_identical_position_requests_are_evaluated_once(monkeypatch):
    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Var("a")),
        output_vars={"y": "y"})
    env = {"a": ranges.Range(0, 3)}
    calls = {"transfer": 0, "plan": 0, "loop_plan": 0, "closure": 0}
    original_transfer = plan_module._normal_position_successor
    original_plan = plan_module.plan_encoding
    original_loop_plan = plan_module._loop_plan
    original_closure = closure_proof._check_position_closure

    def transfer(*args, **kwargs):
        calls["transfer"] += 1
        return original_transfer(*args, **kwargs)

    def step_plan(*args, **kwargs):
        calls["plan"] += 1
        return original_plan(*args, **kwargs)

    def loop_plan(*args, **kwargs):
        calls["loop_plan"] += 1
        return original_loop_plan(*args, **kwargs)

    def closure(*args, **kwargs):
        calls["closure"] += 1
        return original_closure(*args, **kwargs)

    monkeypatch.setattr(plan_module, "_normal_position_successor", transfer)
    monkeypatch.setattr(plan_module, "plan_encoding", step_plan)
    monkeypatch.setattr(plan_module, "_loop_plan", loop_plan)
    monkeypatch.setattr(closure_proof, "_check_position_closure", closure)
    planned = plan_positions(proc, env, 8)

    assert planned.complete, planned.unresolved
    assert calls == {
        "transfer": 1, "plan": 1, "loop_plan": 1, "closure": 1}
    assert len(planned.steps) == 8
    assert len({step.fingerprint for step in planned.steps}) == 1


def test_graph_position_plan_reuses_the_successor_derived_with_the_step(
        monkeypatch):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a & 1}
"""
    instance = behavior_instance.lower(source, _schema(), bound=8)
    calls = {"fallback": 0}
    original = plan_module._normal_position_successor

    def fallback(*args, **kwargs):
        calls["fallback"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(plan_module, "_normal_position_successor", fallback)
    planned = plan_positions(instance.proc, instance.env, 8)

    assert planned.complete, planned.unresolved
    assert instance.proc.graph is not None
    assert calls == {"fallback": 0}


def test_counted_loop_planning_separates_carriers_from_context(monkeypatch):
    """Joined carriers and pointwise allocation bounds are distinct facts."""
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        y = 0
        for i in range(4):
            y = (y + i) & 1
        return {"y": y}
"""
    instance = behavior_instance.lower(source, _schema(), bound=1)
    calls = 0
    original = ranges.walk_post

    def walk(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ranges, "walk_post", walk)
    planned = plan_module.plan_encoding(
        instance.proc, instance.env, bound=1)

    assert planned.complete, planned.unresolved
    assert calls == 2


def test_repeated_source_entries_reuse_only_identical_complete_graph_questions(monkeypatch):
    source = """class M:
    def reset(self):
        pass
    def process(self, a):
        y = 0
        for i in range(4):
            y = 0
            for j in range(3):
                y = (y + a) & 1
        return {"y": y}
"""
    instance = behavior_instance.lower(source, _schema(), bound=1)
    original, calls = ranges.graph_post, []

    def counted(graph, env, *args, **kwargs):
        if kwargs.get("_unreachable_witness") is not None:
            calls.append((id(graph), tuple(sorted(env.items()))))
        return original(graph, env, *args, **kwargs)

    monkeypatch.setattr(ranges, "graph_post", counted)
    first = plan_module.plan_encoding(instance.proc, instance.env, bound=1)
    assert first.complete, first.unresolved
    assert calls and len(calls) == len(set(calls))
    first_calls = tuple(calls)
    calls.clear()
    second = plan_module.plan_encoding(instance.proc, instance.env, bound=1)
    assert second == first
    assert tuple(calls) == first_calls, "a new planning call must not reuse a previous result"


def test_repeated_graph_questions_distinguish_equal_hull_array_cells():
    source = """class M:
    def reset(self):
        pass
    def process(self, a):
        y = 0
        for outer in range(3):
            buf = [0, 1]
            y = 0
            for inner in range(2):
                y = (2 * y + buf[0]) & 1
                buf = [buf[1], buf[0]]
        return {"y": y}
"""
    instance = behavior_instance.lower(source, _schema(), bound=1)
    planned, successor = plan_module._derive_encoding_plan(
        instance.proc, instance.env, bound=1)
    assert planned.complete, planned.unresolved
    result = successor[instance.proc.output_vars["y"]]
    assert (result.lo, result.hi) == (1, 1)


def test_prepared_unrolling_reuses_its_sealed_step_plans(monkeypatch):
    instance = _instance(bound=3)
    prepared = closure_proof.prepare_positions(
        instance.proc, instance.env, 3)

    def should_not_rederive(*_args, **_kwargs):
        raise AssertionError("a prepared step plan was re-derived")

    monkeypatch.setattr(plan_module, "plan_encoding", should_not_rederive)
    run = residual.unroll(
        instance.proc, instance.env, 3, plan=prepared)

    assert len(run.encodings) == 3


def test_symbolic_closure_checks_boundary_components_separately(monkeypatch):
    from behave.sim.symbolic import exact_solve

    proc = ir.Procedure(
        (), ("a",), (), ("y", "z"),
        ir.Seq(
            ir.Assign("y", ir.Op("fmod", ir.Var("a"), ir.Lit(3))),
            ir.Assign("z", ir.Op("fmod", ir.Var("a"), ir.Lit(3)))),
        output_vars={"y": "y", "z": "z"})
    calls = []

    def unsat(_formula, target, **kwargs):
        calls.append((target, kwargs["timeout_ms"]))
        return SimpleNamespace(answer=SolverAnswer.UNSAT, reason="")

    # Exercise the exact-query fallback rather than whichever primitive range
    # identities the structural proof happens to know.  Floor modulo is now
    # discharged structurally in normal production. A non-power-of-two
    # divisor avoids Z3 simplifying the remainder to an independently bounded
    # bit mask before this mocked range rule is consulted.
    monkeypatch.setattr(
        closure_proof._SyntacticClosureProof, "_floor_mod_hull",
        lambda _self, _term: None)
    monkeypatch.setattr(exact_solve, "solve_exact", unsat)
    planned = plan_positions(proc, {"a": ranges.Range(0, 10)}, 1)

    assert planned.complete, planned.unresolved
    assert len(calls) >= 2, "output components were folded into one solver query"
    assert all(0 < timeout <= closure_proof._POSITION_CLOSURE_TIMEOUT_MS
               for _target, timeout in calls)
    assert all(later <= earlier for (_target, earlier), (_other, later)
               in pairwise(calls)), \
        "all component checks must consume one shared deadline"


def test_structural_exhaustion_emits_membership_not_a_proof():
    import time

    work = closure_proof._StructuralProofWork(0, time.monotonic() + 60)
    proof = closure_proof._SyntacticClosureProof({}, work=work)
    value = z3.BitVec("exhausted-value", 8)
    ((_path, member),) = closure_proof._carrier_leaf_memberships(
        value, FiniteCarrier.of(ranges.Range(0, 3)), proof=proof)

    assert not z3.is_true(member)
    assert z3.is_true(z3.simplify(z3.substitute(
        member, (value, z3.BitVecVal(2, 8)))))
    assert z3.is_false(z3.simplify(z3.substitute(
        member, (value, z3.BitVecVal(4, 8)))))


def test_branch_proofs_share_the_structural_work_allowance():
    import time

    work = closure_proof._StructuralProofWork(1, time.monotonic() + 60)
    proof = closure_proof._SyntacticClosureProof({}, work=work)
    branch = proof._with_branch_assumption(z3.Bool("branch-budget"), True)
    assert branch.work is proof.work
    branch.work.charge()
    with pytest.raises(closure_proof._StructuralProofLimit):
        proof.work.charge()
    with pytest.raises(closure_proof._StructuralProofLimit):
        closure_proof._PropositionalProof(z3.BoolVal(True), {}, work=work)


def test_exhausted_structural_work_falls_back_to_real_exact_closure(monkeypatch):
    from behave.sim.symbolic import exact_solve

    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Op("fmod", ir.Var("a"), ir.Lit(2))),
        output_vars={"y": "y"})
    original = exact_solve.solve_exact
    answers = []

    def counted(*args, **kwargs):
        result = original(*args, **kwargs)
        answers.append(result.answer)
        return result

    monkeypatch.setattr(closure_proof, "_POSITION_STRUCTURAL_PROOF_WORK", 0)
    monkeypatch.setattr(exact_solve, "solve_exact", counted)
    planned = plan_positions(proc, {"a": ranges.Range(0, 2)}, 1)

    assert planned.complete, planned.unresolved
    assert answers and all(answer is SolverAnswer.UNSAT for answer in answers)


@pytest.mark.parametrize("precheck_error", [False, True])
def test_exact_closure_does_not_retain_the_optional_boundary_graph(
        monkeypatch, precheck_error):
    import weakref

    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Op("fmod", ir.Var("a"), ir.Lit(2))),
        output_vars={"y": "y"})
    original_boundary = encoder._encode_boundary_step
    original_exact = encoder._encode_derived_step
    original_membership = closure_proof._carrier_leaf_memberships
    references = []
    exact_calls = []

    class TrackedEncoding:
        def __init__(self, encoding):
            self.encoding = encoding

        def __getattr__(self, name):
            return getattr(self.encoding, name)

    def boundary(*args, **kwargs):
        result = TrackedEncoding(original_boundary(*args, **kwargs))
        references.append(weakref.ref(result))
        return result

    def membership(*args, **kwargs):
        if precheck_error and kwargs.get("_coarse_only"):
            raise ValueError("optional proof declined")
        return original_membership(*args, **kwargs)

    def exact(*args, **kwargs):
        assert references and all(ref() is None for ref in references)
        exact_calls.append(True)
        return original_exact(*args, **kwargs)

    monkeypatch.setattr(encoder, "_encode_boundary_step", boundary)
    monkeypatch.setattr(encoder, "_encode_derived_step", exact)
    monkeypatch.setattr(closure_proof, "_carrier_leaf_memberships", membership)
    monkeypatch.setattr(closure_proof, "_POSITION_STRUCTURAL_PROOF_WORK", 0)
    planned = plan_positions(proc, {"a": ranges.Range(0, 2)}, 1)

    assert planned.complete, planned.unresolved
    assert exact_calls == [True]


def test_position_closure_uses_compact_arrays_in_both_encodings(monkeypatch):
    address = ir.Op("fmod", ir.Var("a"), ir.Lit(4))
    proc = ir.Procedure(
        ("m",), ("a",), (), ("y",),
        ir.Seq(ir.Store("m", address, ir.Var("a")),
               ir.Assign("y", ir.Op("fmod", ir.Sel(ir.Var("m"), address),
                                    ir.Lit(2)))),
        initial={"m": [0] * 4}, types={"m": types.Array(4)},
        output_vars={"y": "y"})
    calls = []

    def observe(name, original):
        def encode(*args, **kwargs):
            assert kwargs["_validation"].compact_arrays
            result = original(*args, **kwargs)
            assert isinstance(result.state_post["m"], FunctionalArray)
            calls.append(name)
            return result
        return encode

    for name in ("_encode_boundary_step", "_encode_derived_step"):
        monkeypatch.setattr(encoder, name, observe(name, getattr(encoder, name)))
    # Force the authoritative fallback too, rather than accepting only the
    # optional structural proof as evidence that both paths use the setting.
    monkeypatch.setattr(closure_proof, "_POSITION_STRUCTURAL_PROOF_WORK", 0)
    planned = plan_positions(proc, {"a": ranges.Range(0, 7)}, 2)

    assert planned.complete, planned.unresolved
    assert set(calls) == {"_encode_boundary_step", "_encode_derived_step"}


@pytest.mark.parametrize("same_public_name", [False, True])
def test_partial_broad_closure_proofs_survive_budget_exhaustion(monkeypatch, same_public_name):
    from behave.sim.symbolic import exact_solve

    proc = ir.Procedure(
        ("y",) if same_public_name else (), ("a",), (),
        ("z",) if same_public_name else ("y", "z"),
        ir.Seq(ir.Assign("y", ir.Op("fmod", ir.Var("a"), ir.Lit(2))),
               ir.Assign("z", ir.Op("fmod", ir.Var("a"), ir.Lit(3)))),
        initial={"y": 0} if same_public_name else {},
        output_vars={"y": "z"} if same_public_name else {"y": "y", "z": "z"})
    original_membership = closure_proof._carrier_leaf_memberships
    original_solve = exact_solve.solve_exact
    observed, answers = [], []

    def spend_after_first(*args, **kwargs):
        result = original_membership(*args, **kwargs)
        observed.append(kwargs.get("_coarse_only", False))
        if len(observed) == 1:
            assert all(z3.is_true(check) for _path, check in result)
            kwargs["proof"].work.remaining = 0
        return result

    def solve(*args, **kwargs):
        result = original_solve(*args, **kwargs)
        answers.append(result.answer)
        return result

    monkeypatch.setattr(closure_proof, "_carrier_leaf_memberships", spend_after_first)
    monkeypatch.setattr(exact_solve, "solve_exact", solve)
    planned = plan_positions(proc, {"a": ranges.Range(0, 4)}, 1)
    assert planned.complete, planned.unresolved
    assert observed == [True, True, False], "the first component was proved twice"
    assert answers == [SolverAnswer.UNSAT]


def test_output_closure_cannot_use_a_same_named_state_field(monkeypatch):
    proc = ir.Procedure(
        ("y",), ("a",), (), ("z",),
        ir.Seq(ir.Assign("y", ir.Lit(0)), ir.Assign("z", ir.Var("a"))),
        initial={"y": 0}, output_vars={"y": "z"})
    original = ranges._post

    def wrong_output_range(*args, **kwargs):
        post = original(*args, **kwargs)
        if post is not ranges._NO_NORMAL:
            post = dict(post, z=ranges.Range(0, 0))
        return post

    monkeypatch.setattr(ranges, "_post", wrong_output_range)
    planned = plan_positions(proc, {"a": ranges.Range(0, 2)}, 1)
    assert not planned.complete
    assert any(item.what == "closure[0]" and "outside" in item.detail
               for item in planned.unresolved), planned.unresolved


@pytest.mark.parametrize("state_array", [False, True])
@pytest.mark.parametrize("unwritten_cell", [False, True])
def test_exact_fallback_checks_cells_not_overwritten_array_sources(
        monkeypatch, state_array, unwritten_cell):
    proc = ir.Procedure(
        ("buf",) if state_array else (), ("a",), (), () if state_array else ("buf",),
        ir.Seq(ir.Assign("buf", ir.Op("arraylit", ir.Lit(99), ir.Lit(99))),
               ir.Seq(ir.Store("buf", ir.Lit(0), ir.Var("a")),
                      ir.Skip() if unwritten_cell else
                      ir.Store("buf", ir.Lit(1), ir.Var("a")))),
        initial={"buf": [0, 0]} if state_array else {},
        types={"buf": types.Array(2), "a": types.INTEGER},
        output_vars={} if state_array else {"y": "buf"})
    original = ranges._post

    def exact_output_range(*args, **kwargs):
        post = original(*args, **kwargs)
        if post is not ranges._NO_NORMAL:
            post = dict(post, buf=ranges.Range(0, 1))
        return post

    monkeypatch.setattr(ranges, "_post", exact_output_range)
    monkeypatch.setattr(closure_proof, "_POSITION_STRUCTURAL_PROOF_WORK", 0)
    planned = plan_positions(proc, {"a": ranges.Range(0, 1)}, 1)
    assert planned.complete is not unwritten_cell, planned.unresolved
    if unwritten_cell:
        assert any(item.what == "closure[0]" and "outside" in item.detail
                   for item in planned.unresolved), planned.unresolved


def test_compact_carrier_membership_is_extensional_with_guarded_stores():
    import time

    index, payload = z3.BitVecs("membership-index membership-value", 8)
    enabled, replace = z3.Bools("membership-enabled membership-replace")
    array = FunctionalArray.uniform(4, z3.BitVecVal(99, 8))
    for cell in range(4):
        array = array.store(z3.BitVecVal(cell, 8), z3.BitVecVal(1, 8), replace)
    array = array.store(index, payload, enabled)
    work = closure_proof._StructuralProofWork(0, time.monotonic() + 60)
    proof = closure_proof._SyntacticClosureProof({}, work=work)
    checks = closure_proof._carrier_leaf_memberships(
        array, FiniteCarrier.of(ranges.Range(0, 3)), proof=proof)
    actual = z3.And(*(member for _path, member in checks))
    final = [array.select(z3.BitVecVal(cell, 8)) for cell in range(4)]
    expected = z3.And(*(z3.And(value >= 0, value <= 3) for value in final))
    solver = z3.Solver()
    solver.add(actual != expected)
    assert solver.check() == z3.unsat
    # A dead old value and a write outside the finite array cannot refute it.
    solver = z3.Solver()
    solver.add(replace, enabled, index == 7, payload == 99, actual)
    assert solver.check() == z3.sat


@pytest.mark.parametrize('boundary_cone', [False, True])
def test_closure_construction_receives_its_own_deadline(monkeypatch, boundary_cone):
    import time

    from behave.sim.symbolic import exact_solve
    from behave.sim.symbolic import slicing

    proc = ir.Procedure((), ('a',), (), ('y',),
                        ir.Assign('y', ir.Op('fmod', ir.Var('a'), ir.Lit(2))),
                        output_vars={'y': 'y'})
    now = [100.0]
    name = '_encode_boundary_step' if boundary_cone else '_encode_derived_step'
    original = getattr(encoder, name)

    def delayed(*args, **kwargs):
        assert callable(kwargs['_checkpoint'])
        now[0] = 102.0
        return original(*args, **kwargs)

    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(encoder, name, delayed)
    if not boundary_cone:
        monkeypatch.setattr(slicing, 'for_boundary', lambda *_args: None)
    monkeypatch.setattr(exact_solve, 'solve_exact', lambda *_args, **_kwargs:
                        pytest.fail('expired construction must not start a solver'))
    planned = plan_positions(proc, {'a': ranges.Range(0, 2)}, 1, _closure_deadline=101.0)
    assert not planned.complete
    assert any(item.kind == 'resource' and 'deadline' in item.detail
               for item in planned.unresolved)


def test_structural_precheck_consumes_the_shared_closure_deadline(monkeypatch):
    import time

    from behave.sim.symbolic import exact_solve

    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Op("fmod", ir.Var("a"), ir.Lit(2))),
        output_vars={"y": "y"})
    now = [100.0]
    original = closure_proof._carrier_leaf_memberships

    def delayed(*args, **kwargs):
        now[0] = 102.0
        return original(*args, **kwargs)

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(closure_proof, "_carrier_leaf_memberships", delayed)
    monkeypatch.setattr(exact_solve, "solve_exact", lambda *_args, **_kwargs:
                        pytest.fail("an expired precheck must not start a fresh budget"))
    planned = plan_positions(
        proc, {"a": ranges.Range(0, 2)}, 1, _closure_deadline=101.0)

    assert not planned.complete
    assert any(item.kind == "resource" and "deadline" in item.detail
               for item in planned.unresolved), planned.unresolved


def test_symbolic_closure_reuses_the_exact_boundary_hulls(monkeypatch):
    """Copies and stores retain premises already asserted by the formula."""

    from behave.sim.symbolic import exact_solve

    instance = behavior_instance.lower(
        ARRAY_FROM_INPUT, _byte_output_schema(), bound=1)

    def unexpected_solver(*_args, **_kwargs):
        raise AssertionError("boundary copies should need no solver query")

    monkeypatch.setattr(exact_solve, "solve_exact", unexpected_solver)
    planned = plan_positions(instance.proc, instance.env, 1)

    assert planned.complete, planned.unresolved


def test_boundary_precheck_does_not_reopen_erased_failure_control(monkeypatch):
    """The broad carrier proof is deliberately independent of completion."""

    proc = ir.Procedure(
        ("x",), ("a", "stop"), (), (),
        ir.Seq(
            ir.If(
                ir.Var("stop"),
                ir.Fail("Stopped", "safety:stop"),
                ir.Skip()),
            ir.Assign("x", ir.Var("a"))),
        initial={"x": 0},
        types={"x": types.INTEGER, "a": types.INTEGER,
               "stop": types.BOOLEAN})
    original = closure_proof._SyntacticClosureProof._prepare_assumption_literals

    def prepare(self):
        if self.assumption is not None:
            pytest.fail(
                "the failure-erased boundary proof reopened completion control")
        return original(self)

    monkeypatch.setattr(
        closure_proof._SyntacticClosureProof,
        "_prepare_assumption_literals", prepare)
    planned = plan_positions(
        proc,
        {"a": ranges.Range(0, 1), "stop": ranges.Range(0, 1)},
        1)

    assert planned.complete, planned.unresolved
    assert planned.state_layers[1]["x"].range == ranges.Range(0, 1)


def test_symbolic_closure_proves_mutually_exclusive_unrolled_stores(
        monkeypatch):
    """A fixed loop updates each indexed cell once, not once per body copy."""

    from behave.sim.symbolic import exact_solve

    instance = behavior_instance.lower(
        ARRAY_LOOP_FROM_INPUT, _byte_output_schema(), bound=1)

    def unexpected_solver(*_args, **_kwargs):
        raise AssertionError("fixed store indices should need no solver query")

    def unexpected_formula(*_args, **_kwargs):
        raise AssertionError(
            "a structurally closed boundary should not materialize a formula")

    monkeypatch.setattr(exact_solve, "solve_exact", unexpected_solver)
    monkeypatch.setattr(encoder.StepEncoding, "exact_formula", unexpected_formula)
    planned = plan_positions(instance.proc, instance.env, 1)

    assert planned.complete, planned.unresolved
    assert (planned.state_layers[1]["buf"].lo,
            planned.state_layers[1]["buf"].hi) == (0, 262)


def test_syntactic_closure_proves_wide_constants_and_unsigned_masks():
    width = 155
    count = z3.BitVec("count", width)
    bits = z3.BitVec("bits", width)
    mask = z3.BitVec("mask", width)
    value = z3.BitVec("value", width)
    result = z3.BitVec("result", width)
    selected_bits = z3.BitVec("selected_bits", width)
    selected_mask = z3.BitVec("selected_mask", width)
    selected_result = z3.BitVec("selected_result", width)
    active = z3.Bool("active")
    definitions = {
        count.get_id(): z3.BitVecVal(0, width) + z3.BitVecVal(1, width),
        bits.get_id(): z3.BitVecVal(32, width),
        mask.get_id(): (
            (z3.BitVecVal(1, width) << bits) - z3.BitVecVal(1, width)),
        result.get_id(): z3.If(
            active, value & mask, z3.BitVecVal(0, width)),
        selected_bits.get_id(): z3.If(
            active, z3.BitVecVal(32, width), z3.BitVecVal(0, width)),
        selected_mask.get_id(): (
            z3.If(
                z3.BitVecVal(0, width) > selected_bits,
                z3.BitVecVal(0, width),
                z3.BitVecVal(1, width) << selected_bits,
            )
            - z3.BitVecVal(1, width)),
        selected_result.get_id(): value & selected_mask,
    }

    assert closure_proof._syntactic_signed_hull(
        count, definitions) == ranges.Range(1, 1)
    assert closure_proof._syntactic_signed_hull(
        result, definitions) == ranges.Range(0, (1 << 32) - 1)
    assert closure_proof._syntactic_signed_hull(
        selected_result, definitions) == ranges.Range(0, (1 << 32) - 1)
    proof = closure_proof._SyntacticClosureProof(definitions)
    assert proof.coarse_signed_hull(
        selected_result) == ranges.Range(0, (1 << 32) - 1)


def test_coarse_closure_retains_exact_boundary_hulls_through_dynamic_stores():
    """Untouched array cells keep the declared layer range."""

    width = 257
    old = z3.BitVec("dynamic_store_old", width)
    incoming = z3.BitVec("dynamic_store_incoming", width)
    result = z3.BitVec("dynamic_store_result", width)
    definitions = {
        result.get_id(): z3.If(
            z3.Bool("dynamic_store_active"),
            incoming & z3.BitVecVal((1 << 16) - 1, width),
            old,
        ),
    }
    proof = closure_proof._SyntacticClosureProof(definitions)
    proof.assume_boundary_hull(old, ranges.Range(0, (1 << 16) - 1))

    assert proof.coarse_signed_hull(result) == \
        ranges.Range(0, (1 << 16) - 1)


def test_syntactic_closure_proves_nonnegative_packed_words():
    """Bitwise field packing stays within the joined unsigned carrier."""

    width = 129
    low = z3.BitVec("packed_low", width)
    high = z3.BitVec("packed_high", width)
    result = z3.BitVec("packed_result", width)
    definitions = {
        result.get_id(): low | (high << z3.BitVecVal(32, width)),
    }
    proof = closure_proof._SyntacticClosureProof(definitions)
    word = ranges.Range(0, (1 << 32) - 1)
    proof.assume_boundary_hull(low, word)
    proof.assume_boundary_hull(high, word)

    assert proof.coarse_signed_hull(result) == \
        ranges.Range(0, (1 << 64) - 1)

    overlapping = z3.BitVec("overlapping_or", width)
    proof = closure_proof._SyntacticClosureProof({
        overlapping.get_id(): low | high,
    })
    proof.assume_boundary_hull(low, ranges.Range(0, 8))
    proof.assume_boundary_hull(high, ranges.Range(0, 8))
    assert proof.coarse_signed_hull(overlapping) == ranges.Range(0, 15)


def test_syntactic_closure_proves_fixed_prefix_concatenations():
    """A fixed high prefix bounds a suffix without opening its data cone."""

    suffix = z3.BitVec("fixed_prefix_suffix", 256)
    positive = z3.Concat(z3.BitVecVal(0, 255), suffix)
    negative = z3.Concat(z3.BitVecVal(0b11, 2), suffix)
    unknown = z3.Concat(z3.BitVec("unknown_prefix", 2), suffix)
    proof = closure_proof._SyntacticClosureProof({})

    assert proof.signed_hull(positive) == \
        ranges.Range(0, (1 << 256) - 1)
    assert proof.signed_hull(negative) == \
        ranges.Range(-(1 << 256), -1)
    assert proof.signed_hull(unknown) == \
        ranges.Range(-(1 << 257), (1 << 257) - 1)


def test_syntactic_closure_is_not_limited_by_python_recursion_depth():
    """A long SSA chain is an encoding size, not a Python stack limit."""

    width = 32
    values = [z3.BitVec(f"deep_ssa_{index}", width)
              for index in range(2_048)]
    definitions = {
        values[0].get_id(): z3.BitVecVal(1, width),
        **{
            values[index].get_id():
                values[index - 1] + z3.BitVecVal(0, width)
            for index in range(1, len(values))
        },
    }

    assert closure_proof._syntactic_signed_hull(
        values[-1], definitions) == ranges.Range(1, 1)


def test_syntactic_closure_is_not_limited_by_nested_boolean_joins():
    """Boolean-valued SSA chains are graph depth, not Python stack depth."""

    width = 8
    zero = z3.BitVecVal(0, width)
    one = z3.BitVecVal(1, width)
    seed = z3.BitVec("deep_boolean_seed", width)
    values = [z3.BitVec(f"deep_boolean_ssa_{index}", width)
              for index in range(2_048)]
    definitions = {
        values[0].get_id(): z3.If(seed != zero, one, zero),
    }
    definitions.update({
        values[index].get_id(): z3.If(values[index - 1] != zero, one, zero)
        for index in range(1, len(values))
    })
    proof = closure_proof._SyntacticClosureProof(definitions)

    formula = proof.boolean_formula(values[-1] != zero)
    assert z3.is_true(z3.simplify(formula == (seed != zero)))


def test_syntactic_control_normalization_closes_nested_branch_exits():
    """Complementary return paths form one total command-completion flag."""

    width = 8
    zero = z3.BitVecVal(0, width)
    one = z3.BitVecVal(1, width)
    entered = z3.Bool("nested_exit_entered")
    branch = z3.Bool("nested_exit_branch")
    left = z3.BitVec("nested_exit_left", width)
    right = z3.BitVec("nested_exit_right", width)
    merged = z3.BitVec("nested_exit_merged", width)
    result = z3.BitVec("nested_exit_result", width)
    definitions = {
        left.get_id(): z3.If(z3.And(entered, branch), one, zero),
        right.get_id(): z3.If(
            z3.And(entered, z3.Not(branch)), one, zero),
        merged.get_id(): z3.If(
            z3.Or(left != zero, right != zero), one, zero),
        result.get_id(): z3.If(merged != zero, z3.BitVecVal(7, width), zero),
    }
    proof = closure_proof._SyntacticClosureProof(
        definitions, assumption=entered)

    assert proof.implies(merged != zero)
    assert proof.signed_hull(result) == ranges.Range(7, 7)


def test_path_sensitive_closure_discards_infeasible_interval_combinations():
    """Complementary guards keep correlated wide values from wrapping."""

    width = 8
    zero = z3.BitVecVal(0, width)
    one = z3.BitVecVal(1, width)
    selector = z3.Bool("correlated_selector")
    flag = z3.BitVec("correlated_flag", width)
    positive = z3.Bool("correlated_positive")
    negative = z3.Bool("correlated_negative")
    left = z3.BitVec("correlated_left", width)
    right = z3.BitVec("correlated_right", width)
    result = z3.BitVec("correlated_result", width)
    definitions = {
        flag.get_id(): z3.If(selector, one, zero),
        positive.get_id(): flag != zero,
        negative.get_id(): flag == zero,
        left.get_id(): z3.If(
            positive, z3.BitVecVal(100, width),
            z3.BitVecVal(-100, width)),
        right.get_id(): z3.If(
            negative, z3.BitVecVal(100, width),
            z3.BitVecVal(-100, width)),
        result.get_id(): left + right,
    }
    proof = closure_proof._SyntacticClosureProof(definitions)

    assert proof.signed_hull(result) == ranges.Range(-128, 127)
    assert z3.is_true(closure_proof._carrier_membership(
        result, plan_module.FiniteCarrier.of(ranges.Range(0, 0)),
        definitions))


def test_path_sensitive_closure_does_not_hide_a_feasible_escape():
    width = 8
    zero = z3.BitVecVal(0, width)
    one = z3.BitVecVal(1, width)
    selector = z3.Bool("escaping_selector")
    flag = z3.BitVec("escaping_flag", width)
    positive = z3.Bool("escaping_positive")
    negative = z3.Bool("escaping_negative")
    left = z3.BitVec("escaping_left", width)
    right = z3.BitVec("escaping_right", width)
    result = z3.BitVec("escaping_result", width)
    definitions = {
        flag.get_id(): z3.If(selector, one, zero),
        positive.get_id(): flag != zero,
        negative.get_id(): flag == zero,
        left.get_id(): z3.If(
            positive, z3.BitVecVal(100, width),
            z3.BitVecVal(-100, width)),
        right.get_id(): z3.If(
            negative, z3.BitVecVal(99, width),
            z3.BitVecVal(-100, width)),
        result.get_id(): left + right,
    }

    assert not z3.is_true(closure_proof._carrier_membership(
        result, plan_module.FiniteCarrier.of(ranges.Range(0, 0)),
        definitions))


def test_control_normalization_retains_proved_arithmetic_guards():
    """Range-proved failure guards remain facts after a 0/1 CFG merge."""

    width = 8
    zero = z3.BitVecVal(0, width)
    one = z3.BitVecVal(1, width)
    entered = z3.Bool("bounded_exit_entered")
    branch = z3.Bool("bounded_exit_branch")
    index = z3.BitVec("bounded_exit_index", width)
    left = z3.BitVec("bounded_exit_left", width)
    right = z3.BitVec("bounded_exit_right", width)
    merged = z3.BitVec("bounded_exit_merged", width)
    result = z3.BitVec("bounded_exit_result", width)
    outside = z3.Or(index < z3.BitVecVal(0, width),
                    index >= z3.BitVecVal(8, width))
    definitions = {
        left.get_id(): z3.If(
            z3.And(entered, branch, z3.Not(outside)), one, zero),
        right.get_id(): z3.If(
            z3.And(entered, z3.Not(branch)), one, zero),
        merged.get_id(): z3.If(
            z3.Or(left != zero, right != zero), one, zero),
        result.get_id(): z3.If(
            merged != zero, z3.BitVecVal(7, width), zero),
    }
    proof = closure_proof._SyntacticClosureProof(
        definitions, assumption=entered)
    proof.assume_boundary_hull(index, ranges.Range(0, 7))

    assert proof.implies(merged != zero)
    assert proof.signed_hull(result) == ranges.Range(7, 7)


def test_control_rewrite_ignores_known_facts_outside_its_dag(monkeypatch):
    """A local guard is not rewritten against the whole transition."""

    proof = closure_proof._SyntacticClosureProof({})
    left = z3.Bool("local_rewrite_left")
    known = z3.Bool("local_rewrite_known")
    target = z3.Or(z3.And(left, known), z3.Not(left))

    # Cache the formula before the fact is learned, matching sibling hulls
    # whose truth becomes available later in the shared closure proof.
    proof.boolean_formula(known)
    proof.booleans[known.get_id()] = True
    proof.terms[known.get_id()] = known
    for index in range(2_048):
        unrelated = z3.Bool(f"unrelated_known_fact_{index}")
        proof.booleans[unrelated.get_id()] = bool(index & 1)
        proof.terms[unrelated.get_id()] = unrelated

    original = z3.substitute
    replacement_counts = []

    def counted(term, *replacements):
        replacement_counts.append(len(replacements))
        return original(term, *replacements)

    monkeypatch.setattr(z3, "substitute", counted)

    assert proof._boolean_value(target) is True
    assert replacement_counts and max(replacement_counts) == 1


def test_propositional_proof_reuses_queries_under_its_fixed_premise(monkeypatch):
    left, right = z3.Bools("proof_cache_left proof_cache_right")
    proof = closure_proof._PropositionalProof(left, {})
    original = z3.Solver.check
    checks = []

    def counted(solver, *args, **kwargs):
        checks.append(solver)
        return original(solver, *args, **kwargs)

    monkeypatch.setattr(z3.Solver, "check", counted)
    for _ in range(12):
        assert proof.implies(z3.Or(left, right))
        assert not proof.implies(right)
    assert len(checks) == 2
    # Premises are local to a proof, never reused across different branches.
    assert closure_proof._PropositionalProof(right, {}).implies(right)


def test_local_guard_reuses_unknown_proofs_but_learns_new_facts(monkeypatch):
    left, known = z3.Bools("guard_cache_left guard_cache_known")
    target = z3.Or(z3.And(left, known), z3.Not(left))
    proof = closure_proof._SyntacticClosureProof({})
    original = z3.Solver.check
    checks = []

    def counted(solver, *args, **kwargs):
        checks.append(solver)
        return original(solver, *args, **kwargs)

    monkeypatch.setattr(z3.Solver, "check", counted)
    assert proof._boolean_value(target) is None
    first = len(checks)
    assert first > 0
    for _ in range(12):
        assert proof._boolean_value(target) is None
    assert len(checks) == first

    proof.booleans[known.get_id()] = True
    proof.terms[known.get_id()] = known
    assert proof._boolean_value(target) is True


def test_syntactic_closure_bounds_signed_division_and_remainder():
    width = 16
    numerator = z3.BitVec("bounded_dividend", width)
    denominator = z3.BitVec("bounded_divisor", width)
    proof = closure_proof._SyntacticClosureProof({})
    proof.assume_boundary_hull(numerator, ranges.Range(-7, 9))
    proof.assume_boundary_hull(denominator, ranges.Range(3, 3))

    assert proof.signed_hull(numerator / denominator) == ranges.Range(-2, 3)
    assert proof.signed_hull(z3.SRem(numerator, denominator)) == \
        ranges.Range(-2, 2)


def test_syntactic_closure_recognizes_exact_floor_modulo_encoding():
    width = 16
    numerator = z3.BitVec("floor_mod_numerator", width)
    positive = z3.BitVecVal(8, width)
    negative = z3.BitVecVal(-8, width)
    proof = closure_proof._SyntacticClosureProof({})

    assert proof.signed_hull(
        encoder._floor_mod(numerator, positive)) == ranges.Range(0, 7)
    assert proof.signed_hull(
        encoder._floor_mod(numerator, negative)) == ranges.Range(-7, 0)

    unrelated = numerator - positive * (numerator / positive)
    assert proof.signed_hull(unrelated) != ranges.Range(0, 7)


def test_syntactic_closure_bounds_variable_bitvector_shifts():
    width = 8
    positive = z3.BitVec("variable_shift_positive", width)
    signed = z3.BitVec("variable_shift_signed", width)
    amount = z3.BitVec("variable_shift_amount", width)
    maybe_large = z3.BitVec("variable_shift_maybe_large", width)
    proof = closure_proof._SyntacticClosureProof({})
    proof.assume_boundary_hull(positive, ranges.Range(1, 3))
    proof.assume_boundary_hull(signed, ranges.Range(-8, 7))
    proof.assume_boundary_hull(amount, ranges.Range(0, 2))
    proof.assume_boundary_hull(maybe_large, ranges.Range(-1, 2))

    assert proof.signed_hull(positive << amount) == ranges.Range(1, 12)
    assert proof.signed_hull(signed >> amount) == ranges.Range(-8, 7)
    assert proof.signed_hull(positive << maybe_large) == ranges.Range(0, 12)


def test_syntactic_closure_recognizes_signed_width_conversion_relation():
    width = 16
    base = z3.BitVec("signed_wrap_base", width)
    entered = z3.Bool("signed_wrap_entered")
    sign = (base & z3.BitVecVal(128, width)) != 0
    wrapped = z3.If(
        z3.And(entered, sign), base - z3.BitVecVal(256, width), base)
    proof = closure_proof._SyntacticClosureProof({}, assumption=entered)
    proof.assume_boundary_hull(base, ranges.Range(0, 255))

    assert proof.signed_hull(wrapped) == ranges.Range(-128, 127)


def test_signed_width_conversion_proof_rejects_mismatched_relations():
    width = 16
    base = z3.BitVec("mismatched_wrap_base", width)
    entered = z3.Bool("mismatched_wrap_entered")
    wrong_sign = (base & z3.BitVecVal(64, width)) != 0
    wrong_guard = z3.If(
        z3.And(entered, wrong_sign),
        base - z3.BitVecVal(256, width), base)
    unguarded = z3.If(
        z3.And(entered, (base & z3.BitVecVal(128, width)) != 0),
        base - z3.BitVecVal(256, width), base)

    proof = closure_proof._SyntacticClosureProof({}, assumption=entered)
    proof.assume_boundary_hull(base, ranges.Range(0, 255))
    assert proof.signed_hull(wrong_guard) == ranges.Range(-256, 255)

    proof = closure_proof._SyntacticClosureProof({})
    proof.assume_boundary_hull(base, ranges.Range(0, 255))
    assert proof.signed_hull(unguarded) == ranges.Range(-256, 255)

    proof = closure_proof._SyntacticClosureProof({}, assumption=entered)
    proof.assume_boundary_hull(base, ranges.Range(-1, 255))
    assert proof.signed_hull(unguarded) == ranges.Range(-257, 255)


def test_syntactic_closure_does_not_ignore_bitvector_wrap_or_sign():
    value = z3.BitVec("small_value", 8)
    added = value + z3.BitVecVal(1, 8)
    signed_mask = value & z3.BitVecVal(0xFF, 8)
    full = ranges.Range(-128, 127)

    assert closure_proof._syntactic_signed_hull(added, {}) == full
    assert closure_proof._syntactic_signed_hull(signed_mask, {}) == full


def test_syntactic_closure_uses_only_the_querys_exact_normal_guard():
    active = z3.Bool("normal_path")
    other = z3.Bool("another_path")
    packed = z3.If(active, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))

    assert closure_proof._syntactic_signed_hull(
        packed, {}, assumption=active) == ranges.Range(1, 1)
    assert closure_proof._syntactic_signed_hull(
        packed, {}, assumption=z3.And(other, active)) == ranges.Range(1, 1)
    assert closure_proof._syntactic_signed_hull(
        packed, {}, assumption=other) == ranges.Range(0, 1)


def test_syntactic_closure_follows_normal_control_implications_only():
    width = 8
    entered = z3.Bool("entered")
    completed = z3.Bool("completed")
    final = z3.Bool("final")
    index = z3.BitVec("guarded_index", width)
    data = z3.BitVec("unrelated_data", width)
    definitions = {
        completed.get_id(): z3.And(entered, data == 0),
        final.get_id(): z3.And(completed, data != 7),
        index.get_id(): z3.If(
            completed, z3.BitVecVal(3, width), z3.BitVecVal(0, width)),
    }
    proof = closure_proof._SyntacticClosureProof(
        definitions, assumption=final)

    assert proof.implies(completed)
    assert proof.signed_hull(index) == ranges.Range(3, 3)
    assert not proof.implies(data < 1), \
        "arithmetic relations must remain exact-solver obligations"


def test_syntactic_closure_preserves_boolean_bitvector_joins():
    """A lowered CFG join remains Boolean even though it is stored as 0/1."""

    width = 8
    branch = z3.Bool("source_branch")
    reached_then = z3.BitVec("reached_then", width)
    reached_else = z3.BitVec("reached_else", width)
    reached_join = z3.BitVec("reached_join", width)
    index = z3.BitVec("index_after_join", width)
    zero = z3.BitVecVal(0, width)
    one = z3.BitVecVal(1, width)
    definitions = {
        reached_then.get_id(): z3.If(branch, one, zero),
        reached_else.get_id(): z3.If(z3.Not(branch), one, zero),
        reached_join.get_id(): z3.If(
            z3.Or(reached_then != zero, reached_else != zero), one, zero),
        index.get_id(): z3.If(
            reached_join != zero, z3.BitVecVal(7, width), zero),
    }
    proof = closure_proof._SyntacticClosureProof(
        definitions, assumption=z3.BoolVal(True))

    assert proof.signed_hull(index) == ranges.Range(7, 7)

    arbitrary = z3.BitVec("arbitrary_integer", width)
    guarded = z3.If(arbitrary != zero, z3.BitVecVal(7, width), zero)
    assert closure_proof._syntactic_signed_hull(
        guarded, {}, assumption=z3.BoolVal(True)) == ranges.Range(0, 7)


def test_output_count_carrier_includes_the_encoders_empty_prefix():
    instance = _instance(bound=1)
    planned = plan_positions(instance.proc, instance.env, 1)

    assert planned.output_layers[0]["#count"].lo == 0
    assert planned.output_layers[0]["#count"].hi == 1


def test_an_identical_unresolved_closure_request_is_not_repeated(monkeypatch):
    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Var("a")),
        output_vars={"y": "y"})
    calls = []

    def unresolved(_proc, position, _env, _step, _occurrences, _width,
                   _following, _outputs, diagnostics, *,
                   certified_outputs=None, deadline=None,
                   _procedure_validation=None):
        assert deadline is None
        assert certified_outputs == {}
        assert _procedure_validation is not None
        calls.append(position)
        diagnostics.append(plan_module.Unresolved(
            "capability", f"closure[{position}]", "solver returned unknown"))

    monkeypatch.setattr(closure_proof, "_check_position_closure", unresolved)
    planned = plan_positions(proc, {"a": ranges.Range(0, 3)}, 8)

    assert calls == [0]
    assert [item.what for item in planned.unresolved] == ["closure[0]"]


def test_position_planning_validates_one_procedure_once(monkeypatch):
    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Var("a")),
        output_vars={"y": "y"})
    original = ir.validate_procedure_representation
    checked = []

    def validate(value, *args, **kwargs):
        checked.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(ir, "validate_procedure_representation", validate)
    planned = plan_positions(proc, {"a": ranges.Range(0, 3)}, 1)

    assert planned.complete
    assert checked == [proc]


def test_position_closure_uses_the_outer_analysis_deadline(monkeypatch):
    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Var("a")),
        output_vars={"y": "y"})
    seen = []

    def closure(*_args, deadline=None, **_kwargs):
        seen.append(deadline)

    monkeypatch.setattr(closure_proof, "_check_position_closure", closure)
    planned = plan_positions(
        proc, {"a": ranges.Range(0, 3)}, 1,
        _closure_deadline=123.5)

    assert planned.complete
    assert seen == [123.5]


def test_occurrence_entry_joins_mutate_only_the_private_accumulator():
    entries = {}
    first = {
        "shared": ranges.Range(0, 1),
        "left_only": ranges.Range(4, 4),
    }
    plan_module._merge_occurrence(entries, 7, first)
    accumulated = entries[7]

    plan_module._merge_occurrence(entries, 7, {
        "shared": ranges.Range(2, 3),
        "right_only": ranges.Range(5, 5),
    })

    assert entries[7] is accumulated
    assert entries[7] == {"shared": ranges.Range(0, 3)}
    assert first == {
        "shared": ranges.Range(0, 1),
        "left_only": ranges.Range(4, 4),
    }


def test_the_exact_unrolling_consumes_each_position_carrier():
    instance = _instance()
    plan = plan_positions(instance.proc, instance.env, 3)
    run = residual.unroll(instance.proc, instance.env, 3, plan=plan)

    assert [encoding.pre["x"].size()
            for encoding in run.encodings] == [plan.scalar_width] * 3
    assert [encoding.state_post["x"].size()
            for encoding in run.encodings] == [plan.scalar_width] * 3
    solver = z3.Solver()
    solver.add(run.constraint)
    assert solver.check() == z3.sat
    model = solver.model()
    assert [model.eval(encoding.state_post["x"]).as_signed_long()
            for encoding in run.encodings] == [1, 2, 3]


def test_recursive_state_uses_each_layer_carrier_for_every_leaf():
    instance = _array_instance()
    plan = plan_positions(instance.proc, instance.env, 3)
    run = residual.unroll(instance.proc, instance.env, 3, plan=plan)

    assert plan.complete, plan.unresolved
    assert [(layer["buf"].lo, layer["buf"].hi)
            for layer in plan.state_layers] == [
                (0, 1), (0, 2), (0, 3), (0, 4)]
    cells = []
    for encoding in run.encodings:
        array = encoding.state_post["buf"]
        assert isinstance(array, FunctionalArray)
        assert array.length == 2
        cells.append([array.select(z3.BitVecVal(index, plan.scalar_width))
                      for index in range(2)])
    assert [[cell.size() for cell in row] for row in cells] == [
        [plan.scalar_width, plan.scalar_width]] * 3
    solver = z3.Solver()
    solver.add(run.constraint)
    assert solver.check() == z3.sat
    model = solver.model()
    assert [[model.eval(cell).as_signed_long() for cell in row]
            for row in cells] == [[1, 1], [2, 1], [3, 1]]


def test_large_uniform_state_has_a_compact_exact_position_closure():
    """Closure proves the component carrier without materializing every cell."""

    instance = behavior_instance.lower(
        LARGE_ARRAY_STATE, _large_array_schema(), bound=1)
    proc = instance.proc.derived(initial={
        **instance.proc.initial,
        "buf": UniformArray.uniform(1 << 16, 0),
    })

    planned = plan_positions(proc, instance.env, 1)
    run = residual.unroll(proc, instance.env, 1, plan=planned)
    successor = run.encodings[0].state_post["buf"]

    assert planned.complete, planned.unresolved
    assert type(successor) is FunctionalArray
    assert len(successor.formula_terms) < 10
    assert all(not z3.is_array(term) for term in successor.formula_terms)


def test_an_empty_array_is_a_complete_zero_component_state_product():
    instance = behavior_instance.lower(EMPTY_ARRAY_STATE, _schema(), bound=2)
    planned = plan_positions(instance.proc, instance.env, 2)
    run = residual.unroll(instance.proc, instance.env, 2, plan=planned)

    assert planned.complete, planned.unresolved
    assert [(layer["empty"].lo, layer["empty"].hi)
            for layer in planned.state_layers] == [(0, 0)] * 3
    assert all(encoding.state_post["empty"] == []
               for encoding in run.encodings)
    assert z3.is_true(closure_proof._carrier_membership(
        [], FiniteCarrier(0, 0, 1)))


def test_boolean_state_has_an_exact_position_carrier():
    proc = ir.Procedure(
        ("flag",), (), (), ("y",),
        ir.Seq(
            ir.Assign("flag", ir.Op("not", ir.Var("flag"))),
            ir.Assign("y", ir.Var("flag"))),
        types={"flag": types.BOOLEAN, "y": types.BOOLEAN},
        initial={"flag": False}, output_vars={"y": "y"})

    planned = plan_positions(proc, {}, 2)
    run = residual.unroll(proc, {}, 2, plan=planned)

    assert planned.complete, planned.unresolved
    assert [(layer["flag"].lo, layer["flag"].hi)
            for layer in planned.state_layers] == [(0, 0), (1, 1), (0, 0)]
    solver = z3.Solver()
    solver.add(run.constraint)
    assert solver.check() == z3.sat


def test_a_boolean_type_supplies_a_finite_occurrence_carrier_after_a_join():
    """Losing singleton precision does not erase a Boolean's finite domain."""

    proc = ir.Procedure(
        (), (), ("same",), ("y",),
        ir.Assign("y", ir.Op("truth", ir.Var("same"))),
        types={"same": types.BOOLEAN, "y": types.BOOLEAN},
        output_vars={"y": "y"})
    numbers = plan_module._command_numbers(proc.body)
    command = numbers[id(proc.body)]

    carriers, faults = plan_module._occurrence_carriers(
        proc, numbers, {command: {}}, {}, exceeded=False)

    assert faults == ()
    leaf = carriers[ExpressionPosition(command, "rhs", (0,))]
    assert (leaf.lo, leaf.hi) == (0, 1)


def test_an_oversized_dynamic_shift_reports_the_representation_capability():
    from behave.sim.behavior.budgets import SHIFT_LIMIT

    proc = ir.Procedure(
        (), (), ("amount",), ("y",),
        ir.Assign("y", ir.Op("shl", ir.Lit(1), ir.Var("amount"))),
        types={"amount": types.INTEGER, "y": types.INTEGER},
        output_vars={"y": "y"})

    planned = plan_module.plan_encoding(
        proc, {"amount": ranges.Range(0, SHIFT_LIMIT + 1)}, bound=1)

    matching = [fault for fault in planned.unresolved
                if fault.kind == "capability"
                and "left-shift count" in fault.detail]
    assert len(matching) == 1, [fault.as_dict() for fault in planned.unresolved]


def test_failure_empties_the_normal_layers_and_is_absorbed():
    proc = ir.Procedure(
        ("x",), (), (), ("y",),
        ir.Seq(
            ir.Assign("y", ir.Lit(7)),
            ir.Fail("DivZero", "safety:stop")),
        initial={"x": 0})
    planned = plan_positions(proc, {}, 3)
    run = residual.unroll(proc, {}, 3, plan=planned)

    assert planned.complete, planned.unresolved
    assert planned.normal_layers == (True, False, False, False)
    assert [z3.is_false(z3.simplify(flag)) for flag in run.halted] == [
        True, False, False, False]
    solver = z3.Solver()
    solver.add(run.constraint)
    assert solver.check() == z3.sat
    model = solver.model()
    assert [z3.is_true(model.eval(flag, model_completion=True))
            for flag in run.halted] == [False, True, True, True]
    assert [z3.is_true(model.eval(
        boundary.normal, model_completion=True))
            for boundary in run.state_boundaries] == [True, False, False, False]
    assert [z3.is_true(model.eval(
        boundary.present, model_completion=True))
            for boundary in run.output_boundaries] == [False, False, False]
    assert model.eval(
        run.output_boundaries[0].value["y"],
        model_completion=True).as_signed_long() == 7, \
        "the failed command's raw value is present but the output tag selects bottom"
    assert z3.is_true(model.eval(
        run.encodings[0].goal_condition("safety:stop"),
        model_completion=True))
    assert all(z3.is_false(model.eval(
        z3.And(
            z3.Not(run.halted[position]),
            run.encodings[position].goal_condition("safety:stop")),
        model_completion=True))
               for position in (1, 2))


def test_a_position_plan_is_immutable_and_pickle_stable():
    instance = _instance()
    plan = plan_positions(instance.proc, instance.env, 3)
    restored = pickle.loads(pickle.dumps(plan))

    assert restored == plan
    assert restored.fingerprint == plan.fingerprint
    assert plan.identity.startswith("position-encoding-plan/21:")
    with pytest.raises(AttributeError):
        plan.bound = 4
    with pytest.raises(TypeError):
        plan.state_layers[0]["x"] = FiniteCarrier(0, 0, 1)


def test_pickle_preserves_per_cell_input_support():
    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Sel(ir.Var("a"), ir.Lit(0))),
        types={"a": types.Array(2, types.INTEGER), "y": types.INTEGER},
        initial={}, output_vars={"y": "y"})
    bind_procedure_contract(proc)
    support = ranges._CellRange((ranges.Range(0, 0), ranges.Range(1, 1)))
    plan = plan_positions(proc, {"a": support}, 1)

    restored = pickle.loads(pickle.dumps(plan))

    assert restored == plan
    assert restored.input_environment_fingerprint \
        == plan.input_environment_fingerprint
    assert restored.input_environment["a"] == support


def test_a_plan_for_another_horizon_is_rejected_before_unrolling():
    instance = _instance()
    plan = plan_positions(instance.proc, instance.env, 3)

    with pytest.raises(PlanMismatch, match="3 steps, not 2"):
        residual.unroll(instance.proc, instance.env, 2, plan=plan)


def test_replacing_one_layer_does_not_forge_closure_evidence():
    instance = _instance()
    plan = plan_positions(instance.proc, instance.env, 3)
    layers = list(plan.state_layers)
    changed = dict(layers[1])
    changed["x"] = FiniteCarrier.of(ranges.Range(0, 0))
    layers[1] = changed
    forged = plan.replaced(state_layers=layers)

    with pytest.raises(PlanMismatch, match="layers, closure evidence"):
        forged.validate(instance.proc, instance.env, bound=3)


@pytest.mark.parametrize("structural_work", [0, 65_536])
def test_a_wrong_abstract_successor_is_found_by_the_symbolic_closure_query(
        monkeypatch, structural_work):
    instance = _instance(bound=1)
    monkeypatch.setattr(closure_proof, "_POSITION_STRUCTURAL_PROOF_WORK", structural_work)
    original = ranges._post

    def too_narrow(command, env, witness=None, _flows=None,
                   _point_product=1, *, _guard=None):
        got = original(
            command, env, witness, _flows, _point_product, _guard=_guard)
        if got is ranges._NO_NORMAL:
            return got
        got = dict(got)
        got["x"] = ranges.Range(0, 0)
        return got

    monkeypatch.setattr(ranges, "_post", too_narrow)
    plan = plan_positions(instance.proc, instance.env, 1)

    assert not plan.complete
    assert any(item.what == "closure[0]" for item in plan.unresolved), \
        plan.unresolved


def test_an_input_support_change_invalidates_every_position():
    instance = _instance()
    plan = plan_positions(instance.proc, instance.env, 3)
    changed = dict(instance.env)
    changed["a"] = ranges.Range(0, 7)

    with pytest.raises(PlanMismatch, match="another input support"):
        plan.validate(instance.proc, changed, bound=3)


def test_position_identity_survives_the_solver_worker_pickle_boundary():
    instance = _instance()
    plan = plan_positions(instance.proc, instance.env, 3)
    goals = tuple(instance.goals())

    result = residual.decide_goals(
        instance.proc,
        instance.env,
        goals,
        steps=3,
        workers=2,
        timeout_ms=10_000,
        plan=plan,
    )

    assert set(result.reachable) | result.unreachable \
        | set(result.undecided) == set(goals)


def test_bind_builds_one_plan_used_by_identity_feasibility_and_queries(
        monkeypatch):
    instance = _instance()
    built = []
    original = closure_proof.plan_positions

    def counted(*args, **kwargs):
        built.append((args[0], args[2]))
        return original(*args, **kwargs)

    monkeypatch.setattr(closure_proof, "plan_positions", counted)
    exact = instance.bind(3)
    prepared = exact.instance._position_plan
    assert len(built) == 1

    def rebuilt(*_args, **_kwargs):
        raise AssertionError("a prepared query rebuilt its closure plan")

    monkeypatch.setattr(closure_proof, "plan_positions", rebuilt)
    identities = exact.evidence_identity()
    assert identities["representation"].startswith(
        "behavior-position-representation/1:")
    assert identities["encoding"].startswith(
        "behavior-position-symbolic-transition/1:")
    assert exact.feasible(timeout_ms=10_000)[0].established

    goals = tuple(exact.goals())
    local = exact.query(goals, workers=1, timeout_ms=10_000)
    parallel = exact.query(goals, workers=2, timeout_ms=10_000)
    assert set(local.reachable) | local.unreachable | set(local.undecided) \
        == set(goals)
    assert set(parallel.reachable) | parallel.unreachable \
        | set(parallel.undecided) == set(goals)
    assert exact.instance._position_plan is prepared


def test_behavior_feasibility_constructs_a_domain_trace_without_smt(
        monkeypatch):
    instance = _instance()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Behavior feasibility invoked symbolic unrolling")

    monkeypatch.setattr(residual, "unroll", forbidden)

    answer, witness = instance.feasible(3, timeout_ms=1)

    assert answer is Feasible.YES
    assert witness == [{"a": 0}, {"a": 0}, {"a": 0}]


def test_a_prepared_plan_is_pickle_stable_reused_and_write_once():
    exact = _instance().bind(3)
    prepared = exact.instance._position_plan
    restored = pickle.loads(pickle.dumps(prepared))

    assert type(restored) is type(prepared)
    assert restored.identity == prepared.identity
    assert restored.checked_for(
        exact.instance.proc, exact.instance.env, 3) == prepared.plan
    rebound = exact.instance.bind(3)
    assert rebound.instance._position_plan is prepared
    with pytest.raises(AttributeError, match="write-once"):
        exact.instance._position_plan = restored
    with pytest.raises((ValueError, PlanMismatch)):
        exact.instance.bind(2)


def test_an_unbound_instance_cannot_name_analysis_evidence():
    instance = _instance()
    manual = BoundedInstance(instance, instance.domain(3))

    with pytest.raises(ValueError, match="prepared by bind"):
        manual.evidence_identity()


def test_prepared_plan_from_previous_closure_version_is_not_reused(monkeypatch):
    instance = _instance(bound=1)
    with monkeypatch.context() as patch:
        patch.setattr(closure_proof, "_POSITION_PLAN_VERSION", "position-encoding-plan/16")
        old = closure_proof.prepare_positions(instance.proc, instance.env, 1)
        payload = pickle.dumps(old)
    with pytest.raises(PlanMismatch, match="seal"):
        pickle.loads(payload)
    with pytest.raises(PlanMismatch, match="seal"):
        old.checked_for(instance.proc, instance.env, 1)


def test_a_prepared_plan_detects_later_input_support_mutation():
    exact = _instance().bind(3)
    exact.instance.env["a"] = ranges.Range(0, 7)

    with pytest.raises(PlanMismatch, match="another input support"):
        exact.evidence_identity()


def test_bind_refuses_an_incomplete_position_plan(monkeypatch):
    instance = _instance()
    complete = plan_positions(instance.proc, instance.env, 3)
    incomplete = complete.replaced(unresolved=(plan_module.Unresolved(
        "capability", "fixture", "not discharged"),))
    monkeypatch.setattr(
        closure_proof, "plan_positions", lambda *_args, **_kwargs: incomplete)

    with pytest.raises(encoder.EncodingError, match="incomplete"):
        instance.bind(3)


def test_preparation_stops_before_materializing_an_incomplete_step(monkeypatch):
    instance = _instance()
    complete = plan_module.plan_encoding(instance.proc, instance.env, bound=3)
    incomplete = complete.replaced(unresolved=(plan_module.Unresolved(
        "width", "fixture", "too wide"),))
    monkeypatch.setattr(
        plan_module, "plan_encoding", lambda *_args, **_kwargs: incomplete)

    def should_not_continue(*_args, **_kwargs):
        raise AssertionError("an unusable step reached position materialization")

    monkeypatch.setattr(plan_module, "_position_scalar_width", should_not_continue)
    with pytest.raises(encoder.EncodingError, match=r"step\[0\].*too wide"):
        closure_proof.prepare_positions(instance.proc, instance.env, 3)


def test_occurrence_numbers_include_the_full_sequence_structure():
    body = ir.Seq(
        ir.Assign("x", ir.Lit(1)),
        ir.Assign("y", ir.Op("add", ir.Var("x"), ir.Lit(1))))
    proc = ir.Procedure(
        ("x",), (), (), ("y",), body,
        initial={"x": 0}, output_vars={"y": "y"})
    planned = plan_positions(proc, {}, 1)
    occurrences = planned.occurrence_layers[0]

    assert ExpressionPosition(1, "rhs") in occurrences
    assert ExpressionPosition(2, "rhs") in occurrences
    assert ExpressionPosition(2, "rhs", (0,)) in occurrences
    assert ExpressionPosition(0, "rhs") not in occurrences, \
        "command zero is the Seq node, not the first semantic command"


def test_array_expression_occurrences_carry_their_leaf_range():
    instance = _array_instance()
    planned = plan_positions(instance.proc, instance.env, 1)
    numbers = plan_module._command_numbers(instance.proc.body)
    array_roots = []
    arrays = {
        name: shape for name, shape in instance.proc.types.items()
        if hasattr(shape, "cells")
    }
    for command in ir.walk(instance.proc.body):
        number = numbers.get(id(command))
        if number is None:
            continue
        for role, expression in plan_module._expression_roots(command):
            if plan_module._is_array(expression, arrays):
                array_roots.append(ExpressionPosition(number, role))

    assert array_roots, "the fixture must contain an array-valued expression"
    assert all(key in planned.occurrence_layers[0] for key in array_roots)


def test_a_missing_or_narrowed_occurrence_carrier_is_rejected():
    # The counter-only fixture now has singleton carriers at every occurrence.
    # Keep a genuinely input-dependent expression so narrowing remains a real
    # forged proof, rather than relying on an imprecise range in the planner.
    source = """class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": a & 1}
"""
    instance = behavior_instance.lower(source, _schema(), bound=1)
    planned = plan_positions(instance.proc, instance.env, 1)
    layer = planned.occurrence_layers[0]
    key = next(key for key, carrier in layer.items() if carrier.lo < carrier.hi)

    missing = dict(layer)
    missing.pop(key)
    forged_position = planned.replaced(occurrence_layers=(missing,))
    with pytest.raises(PlanMismatch, match="layers, closure evidence"):
        forged_position.validate(instance.proc, instance.env, bound=1)

    narrowed = dict(layer)
    original = narrowed[key]
    narrowed[key] = FiniteCarrier.of(ranges.Range(original.lo, original.lo))
    forged_step = planned.steps[0].replaced(
        occurrence_carriers=OccurrenceCarriers(narrowed))
    with pytest.raises(PlanMismatch, match="occurrence carriers"):
        encoder.encode_step(
            instance.proc,
            env=planned.environment_at(0),
            plan=forged_step,
            occurrence_carriers=OccurrenceCarriers(narrowed),
        )


def test_position_encoding_never_falls_back_to_expression_range_analysis(
        monkeypatch):
    body = ir.Seq(
        ir.For(
            "i", ir.Lit(2),
            ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1)))),
        ir.Assign("y", ir.Var("x")))
    proc = ir.Procedure(
        ("x",), (), ("i",), ("y",), body,
        initial={"x": 0}, output_vars={"y": "y"})
    planned = plan_positions(proc, {}, 1)
    assert planned.complete, planned.unresolved
    original = ranges.of_expression

    def not_from_encoder(*args, **kwargs):
        import inspect

        caller = inspect.currentframe().f_back.f_code.co_filename
        if caller.endswith("/symbolic/encoder.py"):
            raise AssertionError("position encoding used a range-analysis fallback")
        return original(*args, **kwargs)

    monkeypatch.setattr(ranges, "of_expression", not_from_encoder)
    run = residual.unroll(proc, {}, 1, plan=planned)
    assert len(run.encodings) == 1


def test_a_retained_while_keeps_the_short_circuit_result_carrier():
    source = """class M:
    def reset(self):
        pass

    def process(self, a, b):
        i = 0
        j = 0
        while i < a and j < b:
            if i <= j:
                i += 1
            else:
                j += 1
        return {"y": i + j}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 3), Endpoint("b", "input", 3),
         Endpoint("y", "output", 4)],
        source="declared by the retained-loop position test",
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )
    instance = behavior_instance.lower(source, schema, bound=1)
    planned = plan_positions(instance.proc, instance.env, 1)

    assert planned.complete, planned.unresolved


def test_repeated_guard_restores_a_branch_local_finite_carrier():
    source = """class M:
    def reset(self):
        pass

    def process(self, choose, value):
        if choose:
            left = value
        else:
            right = value + 1
        if choose:
            result = left
        else:
            result = right
        return {"y": result}
"""
    schema = TransactionSchema(
        [Endpoint("choose", "input", 1), Endpoint("value", "input", 8),
         Endpoint("y", "output", 9)],
        source="declared by the branch-local position test",
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )
    instance = behavior_instance.lower(source, schema, bound=1)
    planned = plan_positions(instance.proc, instance.env, 1)

    assert planned.complete, planned.unresolved


def test_an_unreachable_short_circuit_still_has_a_finite_representation():
    """A layer-infeasible RHS remains a syntactic part of the formula.

    The signed conversion contributes an inlined source CFG before the outer
    short circuit.  On the first seven layers the RHS is unreachable, while on
    the eighth it is reachable.  Every layer still needs a carrier for the one
    shared symbolic expression, without treating that RHS as reachable.
    """
    source = """def signed(value, width):
    value &= (1 << width) - 1
    if value & (1 << (width - 1)):
        value -= 1 << width
    return value

class M:
    def reset(self):
        self.count = 0
        self.history = [0, 0]

    def process(self, a):
        sample = signed(a, 16)
        new_history = [self.history[1], sample]
        new_count = self.count + 1
        emit = new_count >= 8 and ((new_count - 8) % 4) == 0
        self.history = new_history
        self.count = new_count
        if not emit:
            return None
        return {"y": new_history[0]}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 16, signed=True),
         Endpoint("y", "output", 32, signed=True)],
        source="declared by the position-plan test",
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )
    instance = behavior_instance.lower(source, schema, bound=8)
    planned = plan_positions(instance.proc, instance.env, 8)
    restored = pickle.loads(pickle.dumps(planned))

    assert planned.complete, planned.unresolved
    assert planned.normal_layers == (True,) * 9
    # Intra-step cell facts are not a public layer predicate. The following
    # step consumes the same aggregate support its symbolic boundary encodes.
    history = restored.environment_at(1)["history"]
    assert (history.lo, history.hi) == (-32768, 32767)
    assert not hasattr(history, "cells")
    assert residual.unroll(instance.proc, instance.env, 8, plan=restored)
