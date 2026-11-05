"""Cross-stage capability boundaries of the Behavior frontend."""

from __future__ import annotations

import ast

import pytest

from behave.sim.behavior import admission, budgets, lowering
from behave.sim.symbolic import ir, ranges
from behave.sim.symbolic import typecheck as value_types
from behave.sim.symbolic.encoder import (
    EncodingError,
    encode_step,
    verify_encoding,
)
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.lower_prepare import (
    validate_input_bounds,
    validate_input_widths,
)
from behave.sim.symbolic.plan import plan_encoding
from behave.sim.behavior.source_batches import (
    BatchContract,
    BatchForm,
    concatenate_batches,
)
from behave.sim.behavior.source_rom import FrozenValue, evaluate_frozen
from behave.sim.behavior.source_values import (
    SCALAR,
    SourceValueBudget,
    SourceValueBudgetExceeded,
    ValueContract,
    contract_of_expression,
    finite_cardinality_add,
    finite_cardinality_product,
)
from behave.sim.core.contract import FiniteType
from behave.sim.core.refusal import Refused
from behave.sim.core.schema import Endpoint, InterfaceError

_ONE_INPUT = ast.parse(
    "class M:\n"
    "    def reset(self): pass\n"
    "    def process(self, a): return {'y': a}\n"
).body[0].body[-1]


def test_every_new_budget_dimension_is_reported_once():
    report = budgets.as_dict()

    assert report["HELPER_ACTIVATION_LIMIT"]["value"] == \
        budgets.HELPER_ACTIVATION_LIMIT
    assert report["HELPER_ACTIVATION_LIMIT"]["measure"] == \
        "simultaneously nested helper activations below the public step"
    assert report["SCALAR_BITS"]["value"] == budgets.SOURCE_INTEGER_BITS
    assert "SOURCE_INTEGER_BITS" not in report
    assert report["ROM_EXPANSION_NODES"]["value"] == \
        budgets.ROM_EXPANSION_NODES
    assert report["SEMANTIC_IR_NODES"]["measure"] == \
        "retained semantic IR construction nodes"
    assert report["TERM_BUDGET"]["measure"] == \
        "finite exact-encoding and recursive representation work"
    assert report["REPRESENTATION_DEPTH"]["value"] == \
        budgets.REPRESENTATION_DEPTH
    assert report["REPRESENTATION_DEPTH"]["measure"] == \
        "nesting of an IR command, expression, type, or initial value"
    assert report["ARTIFACT_WIRE_MB"] == {
        "kind": "declared", "value": budgets.ARTIFACT_WIRE_MB}
    assert report["SOLVER_MEMORY_MB"] == {
        "kind": "declared", "value": budgets.SOLVER_MEMORY_MB}


def test_solver_memory_leaves_room_inside_the_complete_artifact():
    assert budgets.solver_memory_mb() == budgets.SOLVER_MEMORY_MB
    assert budgets.solver_memory_mb(4_096) == 1_536
    assert budgets.solver_memory_mb(1) == 1
    with pytest.raises(ValueError, match="positive integer"):
        budgets.solver_memory_mb(0)
    with pytest.raises(ValueError, match="positive integer"):
        budgets.solver_memory_mb(True)


def test_required_source_audit_uses_the_declared_artifact_envelope():
    limits = budgets.source_audit_limits()

    assert limits.seconds == (budgets.ARTIFACT_MS + 999) // 1000
    assert limits.wall_seconds == limits.seconds
    assert limits.memory_bytes == budgets.ARTIFACT_MEMORY_MB << 20
    assert limits.processes == 1


@pytest.mark.parametrize("seconds", [12, 1800])
def test_source_audit_inherits_remaining_analysis_wall(monkeypatch, seconds):
    from behave.sim import analysis_budget

    now = [1.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    with analysis_budget.analysis_scope(seconds):
        now[0] += 2.25
        first = budgets.source_audit_limits()
        assert first.seconds == seconds - 2
        assert first.wall_seconds == first.seconds
        assert first.memory_bytes == budgets.ARTIFACT_MEMORY_MB << 20
        assert first.processes == 1
        now[0] += 3
        assert budgets.source_audit_limits().seconds == first.seconds - 3
        now[0] = seconds + 1
        with pytest.raises(analysis_budget.AnalysisStopped):
            budgets.source_audit_limits()


def test_helper_activation_limit_has_one_exact_boundary():
    assert not budgets.helper_activation_exceeds(
        budgets.HELPER_ACTIVATION_LIMIT)
    assert budgets.helper_activation_exceeds(
        budgets.HELPER_ACTIVATION_LIMIT + 1)
    with pytest.raises(TypeError, match="exact integer"):
        budgets.helper_activation_exceeds(True)
    with pytest.raises(ValueError, match="nonnegative"):
        budgets.helper_activation_exceeds(-1)


def test_source_cardinality_and_analysis_work_are_independent():
    node = ast.parse("value", mode="eval").body
    budget = SourceValueBudget(12)

    assert finite_cardinality_add(5, 7) == 12
    assert finite_cardinality_product(3, 4) == 12
    assert finite_cardinality_product(0, 10_000) == 0
    assert finite_cardinality_product(1 << 100, 1 << 100) == 1 << 200
    budget.spend(node, 12)
    assert budget.left == 0
    with pytest.raises(AttributeError):
        budget.limit = 13
    with pytest.raises(SourceValueBudgetExceeded):
        budget.spend(node)
    with pytest.raises(TypeError, match="exact integers"):
        finite_cardinality_add(True)
    with pytest.raises(ValueError, match="nonnegative"):
        finite_cardinality_product(-1)


def test_source_sequence_and_batch_contracts_keep_exact_cardinalities():
    def static_value(node, _bindings):
        return node.value if isinstance(node, ast.Constant) else None

    repeated = ast.parse("[0] * 13", mode="eval").body
    repeated_budget = SourceValueBudget(20)
    repeated_contract = contract_of_expression(
        repeated, static_value=static_value, budget=repeated_budget)
    assert (repeated_contract.minimum, repeated_contract.maximum) == (13, 13)
    assert repeated_budget.left == 16

    concatenated = ast.parse("[0] * 11 + [0] * 10", mode="eval").body
    concatenated_budget = SourceValueBudget(20)
    concatenated_contract = contract_of_expression(
        concatenated, static_value=static_value,
        budget=concatenated_budget)
    assert (concatenated_contract.minimum,
            concatenated_contract.maximum) == (21, 21)
    assert concatenated_budget.left == 11

    transaction = ValueContract.record({"y": SCALAR})
    left = BatchContract(
        6, 6, transaction, frozenset({BatchForm.LIST}))
    right = BatchContract(
        5, 5, transaction, frozenset({BatchForm.LIST}))
    combined = concatenate_batches(left, right, repeated)
    assert (combined.minimum, combined.maximum) == (11, 11)


def test_source_text_is_bounded_before_parsing(monkeypatch):
    monkeypatch.setattr(budgets, "SOURCE_TEXT_CHARS", 16)
    source = "class M:\n    pass\n"

    rejected = admission.check(source)
    assert len(rejected) == 1
    assert rejected[0].construct == "SourceText"
    with pytest.raises(LoweringError) as caught:
        source_model(source)
    assert caught.value.code == "source-text-too-large"


@pytest.mark.parametrize(("widths", "code"), [
    ([True], "input-width-invalid"),
    ([0], "input-width-invalid"),
    ([-1], "input-width-invalid"),
    ([], "input-width-count"),
    ([2, 3], "input-width-count"),
    ([budgets.SCALAR_BITS + 1], "input-width-exceeds-capability"),
])
def test_input_width_contract_is_exact_and_not_zip_truncated(widths, code):
    with pytest.raises(LoweringError) as caught:
        validate_input_widths(_ONE_INPUT, widths)
    assert caught.value.code == code

    assert validate_input_widths(_ONE_INPUT, [budgets.SCALAR_BITS]) == \
        (budgets.SCALAR_BITS,)


@pytest.mark.parametrize(("bounds", "code"), [
    ({}, "input-bound-names"),
    ({"a": (0, 1), "extra": (0, 1)}, "input-bound-names"),
    ({"a": (True, 1)}, "input-bound-invalid"),
    ({"a": (2, 1)}, "input-bound-invalid"),
    ({"a": (0, 1 << budgets.SCALAR_BITS)},
     "input-bound-exceeds-capability"),
])
def test_named_input_bounds_are_complete_typed_and_representable(bounds, code):
    with pytest.raises(LoweringError) as caught:
        validate_input_bounds(_ONE_INPUT, bounds)
    assert caught.value.code == code

    assert validate_input_bounds(_ONE_INPUT, {"a": (-2, 1)}) == \
        {"a": (-2, 1)}


def test_equivalent_width_and_bounds_share_the_scalar_boundary():
    largest = (1 << budgets.SCALAR_BITS) - 1

    assert validate_input_widths(_ONE_INPUT, [budgets.SCALAR_BITS]) == \
        (budgets.SCALAR_BITS,)
    assert validate_input_bounds(_ONE_INPUT, {"a": (0, largest)}) == \
        {"a": (0, largest)}


def test_one_kib_hardware_block_is_within_the_scalar_capability():
    assert validate_input_widths(_ONE_INPUT, [8192]) == (8192,)
    assert validate_input_bounds(
        _ONE_INPUT, {"a": (0, (1 << 8192) - 1)}) == \
        {"a": (0, (1 << 8192) - 1)}


def test_encoding_plan_and_encoder_refuse_every_oversized_range():
    proc = ir.Procedure(
        ["x"], [], [], [], ir.Skip(), types={"x": value_types.VALUE})
    env = {"x": ranges.Range(0, 1 << budgets.SCALAR_BITS)}

    plan = plan_encoding(proc, env)
    assert [(fault.kind, fault.what) for fault in plan.unresolved] == \
        [("width", "x")]
    with pytest.raises(EncodingError) as caught:
        encode_step(proc, env=env)
    assert caught.value.kind == "width"


@pytest.mark.parametrize("body", [
    'return {"y": HUGE}',
    'return {"y": self.HUGE}',
])
def test_all_source_constant_routes_meet_one_scalar_boundary(body):
    huge = 1 << budgets.SCALAR_BITS
    source = f"""HUGE = {huge}
class M:
    HUGE = HUGE
    def reset(self):
        pass
    def process(self, a):
        {body}
"""

    with pytest.raises(LoweringError) as caught:
        lowering.lower_procedure(
            source, outputs=["y"], input_widths=[1])
    assert caught.value.code == "scalar-value-exceeds-capability"


def test_encoder_defensively_refuses_a_hand_built_oversized_literal():
    huge = 1 << budgets.SCALAR_BITS
    proc = ir.Procedure(
        ["x"], [], [], [], ir.Assign("x", ir.Lit(huge)),
        types={"x": value_types.VALUE}, initial={"x": 0})
    faults = verify_encoding(proc, env={"x": ranges.Range(0, 1)})
    assert len(faults) == 1
    assert faults[0].kind == "width"
    assert (f"literal needs {budgets.SCALAR_BITS + 1} magnitude bits"
            in faults[0].message)
    with pytest.raises(EncodingError) as caught:
        encode_step(proc, env={"x": ranges.Range(0, 1)})
    assert caught.value.kind == "width"


def test_deep_public_procedure_is_one_typed_capability_refusal():
    admitted_kind = value_types.VALUE
    for _ in range(budgets.REPRESENTATION_DEPTH - 1):
        admitted_kind = value_types.Array(1, admitted_kind)
    admitted = ir.Procedure(
        ["x"], [], [], [], ir.Skip(), types={"x": admitted_kind})
    env = {"x": ranges.Range(0, 1)}
    assert plan_encoding(admitted, env).complete
    assert encode_step(admitted, env=env).widths["x"] == 2

    kind = value_types.Array(1, admitted_kind)
    proc = ir.Procedure(
        ["x"], [], [], [], ir.Skip(), types={"x": kind})

    plan = plan_encoding(proc, env)
    assert [(fault.kind, fault.what) for fault in plan.unresolved] == [
        ("capability", "representation-depth-exceeded")]
    faults = verify_encoding(proc, env=env)
    assert len(faults) == 1
    assert faults[0].kind == "capability"
    assert "representation-depth-exceeded" in faults[0].message
    with pytest.raises(EncodingError) as caught:
        encode_step(proc, env=env)
    assert caught.value.kind == "capability"
    assert "representation-depth-exceeded" in str(caught.value)
    # Supplying the diagnostic plan must not move its recursive fingerprint
    # reader ahead of the same public capability boundary.
    with pytest.raises(EncodingError) as caught:
        encode_step(proc, env=env, plan=plan)
    assert caught.value.kind == "capability"


def test_cyclic_public_array_is_a_typed_plan_and_encoder_refusal():
    kind = value_types.Array(1)
    object.__setattr__(kind, "elem", kind)
    proc = ir.Procedure(
        ["x"], [], [], [], ir.Skip(), types={"x": kind})
    env = {"x": ranges.Range(0, 1)}

    plan = plan_encoding(proc, env)
    assert [(fault.kind, fault.what) for fault in plan.unresolved] == [
        ("capability", "representation-cycle")]
    faults = verify_encoding(proc, env=env)
    assert len(faults) == 1
    assert faults[0].kind == "capability"
    assert "representation-cycle" in faults[0].message
    with pytest.raises(EncodingError) as caught:
        encode_step(proc, env=env, plan=plan)
    assert caught.value.kind == "capability"


def test_bounded_and_exact_paths_gate_depth_before_z3_allocation(monkeypatch):
    from behave.sim.behavior import residual
    from behave.sim.engines import local_ir as exact

    kind = value_types.VALUE
    value = 0
    for _ in range(budgets.REPRESENTATION_DEPTH):
        kind = value_types.Array(1, kind)
        value = [value]
    proc = ir.Procedure(
        ["x"], [], [], [], ir.Skip(), types={"x": kind},
        initial={"x": value})
    env = {"x": ranges.Range(0, 1)}

    def allocated_too_early(*_args, **_kwargs):
        raise AssertionError("a solver term was allocated before the depth gate")

    monkeypatch.setattr(residual.z3, "BitVec", allocated_too_early)
    monkeypatch.setattr(residual.z3, "BitVecVal", allocated_too_early)
    with pytest.raises(EncodingError) as caught:
        residual.unroll(proc, env, 1)
    assert caught.value.kind == "capability"

    monkeypatch.setattr(exact.z3, "BitVec", allocated_too_early)
    monkeypatch.setattr(exact.z3, "BitVecVal", allocated_too_early)
    with pytest.raises(EncodingError) as caught:
        exact.pin_state(proc, env, {"x": value})
    assert caught.value.kind == "capability"


def test_internal_sign_bit_does_not_shrink_the_public_scalar_boundary():
    proc = ir.Procedure(
        ["x"], [], [], [], ir.Skip(), types={"x": value_types.VALUE})
    env = {"x": ranges.Range(0, (1 << budgets.SCALAR_BITS) - 1)}

    plan = plan_encoding(proc, env)
    assert plan.complete
    assert plan.widths["x"] == budgets.SCALAR_BITS + 1
    assert encode_step(proc, env=env).widths["x"] == \
        budgets.SCALAR_BITS + 1


def test_interface_widths_do_not_coerce_boolean_or_empty_values():
    for width in (True, 0, -1):
        with pytest.raises(InterfaceError):
            Endpoint("a", "input", width)

    with pytest.raises(Refused, match="contract-type-width-not-integer"):
        FiniteType(True)


def _frozen_chain(depth):
    value = FrozenValue("int", 0)
    for _ in range(depth):
        value = FrozenValue("tuple", (value,))
    return value


def test_deep_frozen_value_projections_are_iterative():
    value = _frozen_chain(1_200)

    assert value.fits_expansion(1_201)
    plain = value.to_plain()
    source = value.to_source_value()
    for _ in range(1_200):
        assert type(plain) is tuple and len(plain) == 1
        assert type(source) is tuple and len(source) == 1
        plain, source = plain[0], source[0]
    assert plain == source == 0


def test_frozen_dag_expansion_has_one_exact_boundary_and_cycles_refuse():
    value = FrozenValue("int", 0)
    for _ in range(9):
        value = FrozenValue("tuple", (value, value))
    assert value.fits_expansion(1_023)
    assert not value.fits_expansion(1_022)

    cycle = FrozenValue("tuple", ())
    object.__setattr__(cycle, "payload", (cycle,))
    assert not cycle.fits_expansion(10)
    with pytest.raises(ValueError, match="contains itself"):
        cycle.to_plain()


def test_rom_bindings_share_one_aggregate_source_budget():
    node = ast.parse("[0, 1]").body[0].value
    budget = SourceValueBudget(5)

    assert evaluate_frozen(node, lambda _node: None, budget=budget) is not None
    with pytest.raises(SourceValueBudgetExceeded):
        evaluate_frozen(node, lambda _node: None, budget=budget)


def test_rom_expansion_and_projection_spend_the_shared_source_budget():
    node = ast.parse("ROM").body[0].value
    value = FrozenValue(
        "tuple", (FrozenValue("tuple", (FrozenValue("int", 0),)),))
    budget = SourceValueBudget(4)

    assert value.fits_expansion(3, budget=budget, node=node)
    assert budget.left == 1
    with pytest.raises(SourceValueBudgetExceeded):
        value.to_plain(budget=budget, node=node)


def test_one_budget_crosses_source_model_planning_and_lowering(monkeypatch):
    seen = []
    spend = SourceValueBudget.spend

    def record(self, node, amount=1):
        seen.append(id(self))
        return spend(self, node, amount)

    monkeypatch.setattr(SourceValueBudget, "spend", record)
    source = """class M:
    def __init__(self):
        self.x = 0
    def reset(self):
        self.x = 0
    def process(self, a):
        self.x = a
        return {"y": self.x}
"""

    lowering.lower_procedure(source, outputs=["y"], input_widths=[8])
    assert seen
    assert len(set(seen)) == 1
