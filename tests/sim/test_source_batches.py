"""The source-level algebra for output transaction batches."""

from __future__ import annotations

import ast

import pytest

from behave.sim.symbolic import encoder, interp, ir
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import (
    LoweringError,
    _Lowerer,
    lower_with_state_ranges,
)
from behave.sim.behavior.source_batches import (
    BatchContract,
    BatchContractError,
    BatchForm,
    BatchPlan,
    batch_contract_of_value,
    concatenate_batches,
    join_batch_alternatives,
    join_batch_plans,
    plan_batch_expression,
    record_batch_payload,
)
from behave.sim.behavior.source_values import (
    NONE,
    SCALAR,
    UNKNOWN,
    ValueContract,
    contract_of_expression,
    contracts_for_target,
)

from .test_native_oracle import _ir_trace, _native_trace, _state_fields_of, _traces_agree


def _expr(source):
    return ast.parse(source, mode="eval").body


def _record(*names):
    return ValueContract.record({name: SCALAR for name in names})


def _literal_contract(node):
    if isinstance(node, ast.Dict):
        fields = {}
        for key in node.keys:
            if not isinstance(key, ast.Constant) \
                    or type(key.value) is not str:
                return UNKNOWN
            fields[key.value] = SCALAR
        return ValueContract.record(fields) if fields else UNKNOWN
    return UNKNOWN


_PRODUCTION_LOWERERS = (
    pytest.param(_Lowerer, id="term"),
    pytest.param(GraphLowerer, id="graph"),
)


def _production_transactions(source, row, lowerer):
    """Transactions from a complete production lowering, not the batch oracle."""
    proc = lower_with_state_ranges(
        source, input_widths=[4], lowerer=lowerer)
    _state, output, _hits, error = interp.run_step(proc, row)
    assert not error
    return [dict(transaction) for transaction in output.transactions]


def _transaction_boundary_value(value):
    """JSON transaction-boundary value used by the isolated reference."""
    if isinstance(value, dict):
        return {key: _transaction_boundary_value(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_transaction_boundary_value(item) for item in value]
    return value


def _assert_production_agrees(source, row, expected, lowerer):
    native = _native_trace(source, [row])
    assert native[0][3] == ""
    assert _transaction_boundary_value(native[0][1]) == expected
    assert _production_transactions(source, row, lowerer) == expected


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", [0, 1, 7])
def test_optional_helper_record_keeps_its_recursive_boundary_plan(
        lowerer, value):
    source = """\
class M:
    def reset(self):
        pass
    def choose(self, a):
        if a:
            return {"y": (a, a + 1)}
        return None
    def process(self, a):
        return self.choose(a)
"""
    expected = [] if value == 0 else [{"y": [value, value + 1]}]
    _assert_production_agrees(source, {"a": value}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", [0, 1, 7])
def test_optional_record_local_uses_the_zero_or_one_batch_carrier(
        lowerer, value):
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        row = {"y": a + 1} if a else None
        return row
"""
    expected = [] if value == 0 else [{"y": value + 1}]
    _assert_production_agrees(source, {"a": value}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", [0, 1, 7])
def test_helper_local_can_join_none_with_a_record_before_return(
        lowerer, value):
    source = """\
class M:
    def reset(self):
        pass
    def build(self, a):
        row = None
        if a:
            row = {"y": a + 1}
        return row
    def process(self, a):
        return self.build(a)
"""
    expected = [] if value == 0 else [{"y": value + 1}]
    _assert_production_agrees(source, {"a": value}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_one_return_widens_from_none_to_record_across_state_layers(lowerer):
    source = """\
class M:
    def reset(self):
        self.ready = 0
    def process(self, a):
        result = None
        if self.ready:
            result = {"y": a + 1}
        self.ready = 1
        return result
"""
    rows = [{"a": 2}, {"a": 5}, {"a": 7}]
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer,
        bound=len(rows))
    expected = _native_trace(source, rows)
    state = None
    got = []
    for row in rows:
        state, output, hits, error = interp.run_step(proc, row, state)
        got.append((
            {"ready": state["ready"]},
            None if error else [dict(item) for item in output.transactions],
            {hit for hit in hits if not hit.startswith("safety")},
            error,
        ))

    assert got == expected


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_one_step_state_proof_prunes_an_unreachable_batch_arm(lowerer):
    source = """\
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
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer, bound=1)
    state, output, _hits, error = interp.run_step(proc, {"a": 3})

    assert not error
    assert state["valid"] == 1
    assert output.transactions == []


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_fixed_record_key_joins_use_typed_identity(lowerer):
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        if a:
            key = True
        else:
            key = 1
        row = {}
        row[f"x{key}"] = a + 7
        return row
"""
    native = _native_trace(source, [{"a": 0}, {"a": 1}])
    assert [_transaction_boundary_value(row[1]) for row in native] == [
        [{"x1": 7}], [{"xTrue": 8}]]
    # The public output schema cannot vary with the input.  In particular,
    # Python's host equality True == 1 must not collapse these typed keys and
    # silently freeze one branch's spelling into the other branch.
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], lowerer=lowerer)
    # The shared fixed-record plan rejects the varying schema before public
    # output-name selection. This is also the refusal on behavior/133.
    assert caught.value.code == "helper-return-shape-disagrees"


def test_none_mapping_and_list_are_three_forms_of_one_batch_boundary():
    row = _record("x", "y")
    absent = BatchContract.empty(BatchForm.NONE)
    one = BatchContract.one(row)
    many = BatchContract.sequence(
        ValueContract.sequence("list", 0, 4, row))

    assert (absent.minimum, absent.maximum, absent.field_names) == (0, 0, ())
    assert (one.minimum, one.maximum, one.field_names) == (1, 1, ("x", "y"))
    assert (many.minimum, many.maximum, many.field_names) == (0, 4, ("x", "y"))
    assert absent.forms == frozenset({BatchForm.NONE})
    assert one.forms == frozenset({BatchForm.MAPPING})
    assert many.forms == frozenset({BatchForm.LIST})


def test_none_value_contract_lifts_to_the_empty_batch_form():
    assert batch_contract_of_value(NONE) == \
        BatchContract.empty(BatchForm.NONE)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, []),
        (1, [{"x": 1}]),
        (2, [{"x": 2}, {"x": 12}]),
    ],
)
def test_none_join_after_mapping_and_list_preserves_all_batch_forms(
        lowerer, value, expected):
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        result = {"x": a}
        if a == 2:
            result = [{"x": a}, {"x": a + 10}]
        if a == 0:
            result = None
        return result
"""
    _assert_production_agrees(source, {"a": value}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, []),
        (1, [{"x": 1, "last": 1}]),
        (2, [{"x": 2, "last": 0}, {"x": 12, "last": 1}]),
    ],
)
def test_mixed_batch_keeps_a_record_local_embedded_in_its_list_arm(
        lowerer, value, expected):
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        result = None
        if a == 1:
            result = {"x": a, "last": 1}
        elif a == 2:
            first = {"x": a, "last": 0}
            result = [first, {"x": a + 10, "last": 1}]
        return result
"""
    _assert_production_agrees(source, {"a": value}, expected, lowerer)


def test_mixed_batch_has_the_same_symbolic_and_interpreted_step():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        result = None
        if a == 1:
            result = {"x": a, "last": 1}
        elif a == 2:
            result = [
                {"x": a, "last": 0},
                {"x": a + 10, "last": 1},
            ]
        return result
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert encoder.verify_encoding(proc, env=proc.state_ranges) == []


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_retained_batch_copy_preserves_order_state_hits_and_empty_batches(lowerer):
    source = """\
class M:
    def reset(self):
        self.calls = 0
    def build(self, a):
        self.calls = (self.calls + 1) & 3
        rows = []
        for i in range(4):
            if i < a:
                rows.append({"y": i + self.calls})
        return rows
    def process(self, a):
        rows = self.build(a)
        return [{"y": -1}] + rows + rows
"""
    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    rows = [{"a": value} for value in (0, 3, 1, 2, 0)]
    assert _traces_agree(_native_trace(source, rows),
                         _ir_trace(proc, rows, _state_fields_of(source)))
    assert encoder.verify_encoding(proc, env=proc.state_ranges) == []


@pytest.mark.parametrize("capacity", [2, 32])
def test_batch_copy_command_size_is_independent_of_capacity(capacity):
    source = f"""\
class M:
    def reset(self):
        pass
    def process(self, a):
        rows = []
        for i in range({capacity}):
            rows.append({{"y": i + a}})
        return rows
"""
    proc = lower_with_state_ranges(source, input_widths=[2])
    copies = [command for command in ir.walk(proc.body)
              if isinstance(command, ir.For) and command.flow is None
              and isinstance(command.trip_count, ir.Lit)
              and command.trip_count.value == capacity
              and isinstance(command.body, ir.If)]
    assert len(copies) == 1
    stores = [command for command in ir.walk(copies[0].body)
              if isinstance(command, ir.Store)]
    assert len(stores) == 1
    _state, output, _hits, error = interp.run_step(proc, {"a": 2})
    assert not error
    assert [dict(row) for row in output.transactions] == [
        {"y": i + 2} for i in range(capacity)]


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_explicit_scalar_and_batch_returns_cannot_be_partially_planned(
        lowerer):
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        if a:
            return {"x": a}
        return a
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1], lowerer=lowerer)
    assert caught.value.code == "output-batch-arm-disagrees"


def test_only_record_batches_use_the_parallel_batch_carrier():
    row = _record("x")
    many = ValueContract.sequence("list", 2, 4, row)

    assert record_batch_payload(row) is None
    assert record_batch_payload(ValueContract.option(SCALAR)) is None
    assert record_batch_payload(ValueContract.sequence(
        "list", 0, 4, SCALAR)) is None
    assert record_batch_payload(ValueContract.option(row)) == \
        ValueContract.sequence("list", 0, 1, row)
    assert record_batch_payload(many) == many
    assert record_batch_payload(ValueContract.option(many)) == \
        ValueContract.sequence("list", 0, 4, row)


@pytest.mark.parametrize("args", [
    (1, 1, _record("x"), frozenset({BatchForm.NONE})),
    (0, 1, _record("x"), frozenset({BatchForm.NONE})),
    (0, 0, None, frozenset({BatchForm.MAPPING})),
    (0, 1, _record("x"), frozenset({BatchForm.MAPPING})),
    (0, 2, _record("x"), frozenset({BatchForm.NONE, BatchForm.MAPPING})),
    (2, 2, _record("x"), frozenset({BatchForm.MAPPING, BatchForm.LIST})),
    (0, 0, _record("x"), frozenset({BatchForm.LIST})),
    (0, 1, None, frozenset({BatchForm.NONE, BatchForm.MAPPING})),
])
def test_raw_batch_contracts_cannot_describe_impossible_source_forms(args):
    with pytest.raises(ValueError):
        BatchContract(*args)


def test_a_batch_contract_cannot_hide_mutable_or_unknown_form_metadata():
    with pytest.raises(TypeError, match="immutable"):
        BatchContract(0, 0, None, {BatchForm.LIST})
    with pytest.raises(ValueError, match="known"):
        BatchContract(0, 0, None, frozenset({"list"}))


def test_every_legal_source_form_combination_still_has_a_contract():
    row = _record("x")
    legal = (
        BatchContract(0, 0, None, frozenset({BatchForm.NONE})),
        BatchContract(1, 1, row, frozenset({BatchForm.MAPPING})),
        BatchContract(0, 3, row, frozenset({BatchForm.LIST})),
        BatchContract(
            0, 1, row, frozenset({BatchForm.NONE, BatchForm.MAPPING})),
        BatchContract(
            0, 3, row, frozenset({BatchForm.NONE, BatchForm.LIST})),
        BatchContract(
            1, 3, row, frozenset({BatchForm.MAPPING, BatchForm.LIST})),
        BatchContract(0, 3, row, frozenset(BatchForm)),
    )
    assert len(legal) == 7


def test_leaf_batch_plans_reject_children_and_mismatched_source_forms():
    none_node = ast.Constant(value=None)
    record_node = _expr("{'x': a}")
    record = BatchPlan("record", record_node, BatchContract.one(_record("x")))

    with pytest.raises(ValueError, match="cannot have children"):
        BatchPlan(
            "none", none_node, BatchContract.empty(BatchForm.NONE), (record,))
    with pytest.raises(ValueError, match="None occurrence"):
        BatchPlan(
            "none", record_node, BatchContract.empty(BatchForm.NONE))
    with pytest.raises(ValueError, match="source mapping"):
        BatchPlan(
            "record", record_node,
            BatchContract.sequence(
                ValueContract.sequence("list", 1, 1, _record("x"))))
    with pytest.raises(ValueError, match="source list"):
        BatchPlan("sequence", record_node, BatchContract.one(_record("x")))
    with pytest.raises(ValueError, match="more precise"):
        BatchPlan("summary", record_node, BatchContract.one(_record("x")))
    with pytest.raises(TypeError, match="immutable plan tuple"):
        BatchPlan("record", record_node, record.contract, [])
    with pytest.raises(ValueError, match="unknown batch-plan kind"):
        BatchPlan("invented", record_node, record.contract)


def test_choice_plan_children_are_the_actual_arms_in_source_order():
    node = _expr("{'x': a} if a else None")
    left = BatchPlan("record", node.body, BatchContract.one(_record("x")))
    right = BatchPlan(
        "none", node.orelse, BatchContract.empty(BatchForm.NONE))
    joined = join_batch_alternatives(left.contract, right.contract, node)
    assert BatchPlan("choice", node, joined, (left, right)).children == (
        left, right)

    with pytest.raises(ValueError, match="source arms in order"):
        BatchPlan("choice", node, joined, (right, left))
    with pytest.raises(ValueError, match="join of its arms"):
        BatchPlan("choice", node, left.contract, (left, right))

    disagreeing = _expr("{'x': a} if a else {'z': a}")
    x = BatchPlan(
        "record", disagreeing.body, BatchContract.one(_record("x")))
    z = BatchPlan(
        "record", disagreeing.orelse, BatchContract.one(_record("z")))
    with pytest.raises(ValueError, match="incompatible contracts"):
        BatchPlan("choice", disagreeing, x.contract, (x, z))


def test_revisiting_one_batch_expression_joins_its_finite_envelope():
    node = _expr("result")
    absent = BatchPlan(
        "summary", node, BatchContract.empty(BatchForm.NONE))
    present = BatchPlan(
        "record", node, BatchContract.one(_record("x")))

    joined = join_batch_plans(absent, present)

    assert joined.kind == "summary"
    assert joined.node is node
    assert joined.contract == BatchContract(
        0, 1, _record("x"),
        frozenset({BatchForm.NONE, BatchForm.MAPPING}))


def test_revisiting_a_proved_choice_restores_both_source_arms():
    node = _expr("{'x': a} if ready else None")
    present = BatchPlan(
        "record", node.body, BatchContract.one(_record("x")))
    absent = BatchPlan(
        "none", node.orelse, BatchContract.empty(BatchForm.NONE))
    true_plan = BatchPlan(
        "choice", node, present.contract, (present,), True)
    false_plan = BatchPlan(
        "choice", node, absent.contract, (absent,), False)

    joined = join_batch_plans(true_plan, false_plan)

    assert joined.choice_truth is None
    assert joined.children == (present, absent)
    assert joined.contract.minimum == 0
    assert joined.contract.maximum == 1


def test_a_source_proved_choice_may_retain_only_its_reachable_arm():
    node = _expr("{'x': a} if True else {'dead': a}")
    child = BatchPlan("record", node.body, BatchContract.one(_record("x")))
    assert BatchPlan(
        "choice", node, child.contract, (child,), True).children == (
        child,)

    stranger = BatchPlan(
        "record", _expr("{'x': a}"), BatchContract.one(_record("x")))
    with pytest.raises(ValueError, match="one of the source arms"):
        BatchPlan("choice", node, stranger.contract, (stranger,), True)
    with pytest.raises(ValueError, match="equal its reachable arm"):
        BatchPlan(
            "choice", node,
            BatchContract.one(_record("different")), (child,), True)


def test_concat_plan_contract_and_children_are_derived_from_its_operands():
    node = _expr("[{'x': a}] + [{'x': b}]")
    row = _record("x")
    children = tuple(BatchPlan(
        "list", operand,
        BatchContract.sequence(ValueContract.sequence("list", 1, 1, row)),
        (BatchPlan("record", operand.elts[0], BatchContract.one(row)),))
        for operand in (node.left, node.right))
    contract = concatenate_batches(
        children[0].contract, children[1].contract, node)
    assert BatchPlan("concat", node, contract, children).children == children

    with pytest.raises(ValueError, match="source operands in order"):
        BatchPlan("concat", node, contract, tuple(reversed(children)))
    with pytest.raises(ValueError, match="add its operand"):
        BatchPlan("concat", node, children[0].contract, children)

    records = tuple(
        BatchPlan("record", operand, BatchContract.one(row))
        for operand in (node.left, node.right))
    with pytest.raises(ValueError, match="compatible source lists"):
        BatchPlan("concat", node, contract, records)


def test_list_plan_count_schema_and_child_order_are_not_caller_choices():
    node = _expr("[{'x': a}, {'x': b}]")
    row = _record("x")
    children = tuple(
        BatchPlan("record", element, BatchContract.one(row))
        for element in node.elts)
    contract = BatchContract.sequence(
        ValueContract.sequence("list", 2, 2, row))
    assert BatchPlan("list", node, contract, children).children == children

    with pytest.raises(ValueError, match="source-order"):
        BatchPlan("list", node, contract, tuple(reversed(children)))
    with pytest.raises(ValueError, match="count and join"):
        BatchPlan(
            "list", node,
            BatchContract.sequence(
                ValueContract.sequence("list", 1, 2, row)),
            children)

    disagreeing = (
        children[0],
        BatchPlan(
            "record", node.elts[1], BatchContract.one(_record("z"))),
    )
    with pytest.raises(ValueError, match="incompatible schemas"):
        BatchPlan("list", node, contract, disagreeing)


def test_control_alternatives_widen_count_but_keep_one_schema():
    row = _record("x", "y")
    got = join_batch_alternatives(
        BatchContract.empty(BatchForm.NONE), BatchContract.one(row))

    assert (got.minimum, got.maximum) == (0, 1)
    assert got.transaction == row
    assert got.forms == frozenset({BatchForm.NONE, BatchForm.MAPPING})


def test_an_empty_arm_is_schema_neutral_but_two_nonempty_schemas_must_agree():
    x = BatchContract.one(_record("x"))
    z = BatchContract.one(_record("z"))
    assert join_batch_alternatives(
        BatchContract.empty(BatchForm.LIST), x).field_names == ("x",)
    with pytest.raises(BatchContractError) as caught:
        join_batch_alternatives(x, z)
    assert caught.value.code == "outputs-disagree-across-returns"


def test_ordered_list_concatenation_adds_finite_capacities():
    row = _record("kind", "real", "imag")
    poles = BatchContract.sequence(
        ValueContract.sequence("list", 0, 16, row))
    gain = BatchContract.sequence(
        ValueContract.sequence("list", 1, 1, row))

    got = concatenate_batches(poles, gain)
    assert (got.minimum, got.maximum) == (1, 17)
    assert got.transaction == row
    assert got.forms == frozenset({BatchForm.LIST})


def test_normalization_does_not_make_mapping_plus_list_a_python_list_addition():
    row = _record("x")
    with pytest.raises(BatchContractError) as caught:
        concatenate_batches(
            BatchContract.one(row),
            BatchContract.sequence(
                ValueContract.sequence("list", 0, 2, row)))
    assert caught.value.code == "output-batch-concat-not-list"


def test_only_a_python_list_of_records_is_a_multi_transaction_value():
    row = _record("x")
    with pytest.raises(ValueError, match="Python list"):
        BatchContract.sequence(
            ValueContract.sequence("tuple", 0, 2, row))
    with pytest.raises(ValueError, match="record"):
        BatchContract.sequence(
            ValueContract.sequence("list", 0, 2, SCALAR))


def test_exact_sequence_contracts_bind_recursive_and_starred_targets():
    pair = ValueContract.sequence("tuple", 2, 2, SCALAR)
    two_pairs = ValueContract.sequence("list", 2, 2, pair)
    nested = ast.parse("for (a, b), (c, d) in rows:\n    pass").body[0]
    assert contracts_for_target(nested.target, two_pairs) == {
        "a": SCALAR, "b": SCALAR, "c": SCALAR, "d": SCALAR}

    starred = ast.parse("for head, *tail in row:\n    pass").body[0]
    bound = contracts_for_target(
        starred.target, ValueContract.sequence("tuple", 4, 4, SCALAR))
    assert bound == {
        "head": SCALAR,
        "tail": ValueContract.sequence("list", 3, 3, SCALAR),
    }
    assert contracts_for_target(
        nested.target, ValueContract.sequence("list", 3, 3, pair)) is None


def test_list_comprehension_contract_recursively_binds_tuple_elements():
    node = _expr("[(10 * x + y) for x, y in [(1, 2), (3, 4)]]")
    got = contract_of_expression(node)
    assert got == ValueContract.sequence("list", 2, 2, SCALAR)


def test_interface_order_is_reused_only_after_schema_equality():
    got = BatchContract.one(_record("a", "m", "z"))
    assert got.ordered_fields(("z", "a", "m")) == ("z", "a", "m")
    with pytest.raises(ValueError, match="disagree"):
        got.ordered_fields(("z", "a", "missing"))
    with pytest.raises(ValueError, match="duplicate"):
        got.ordered_fields(("a", "a", "z"))


def test_helper_record_contract_is_lifted_only_at_the_return_boundary():
    node = _expr("make_row(a)")
    row = _record("fr2", "fr1", "fr0", "dfr")
    plan = plan_batch_expression(
        node, value_contract=lambda candidate: (
            row if candidate is node else UNKNOWN))

    assert plan.kind == "record"
    assert plan.node is node
    assert plan.contract == BatchContract.one(row)


def test_a_callee_batch_summary_is_reused_instead_of_reparsed_at_the_call():
    node = _expr("maybe_emit(a)")
    row = _record("x")
    summary = join_batch_alternatives(
        BatchContract.empty(BatchForm.NONE), BatchContract.one(row))
    plan = plan_batch_expression(
        node,
        value_contract=lambda _candidate: UNKNOWN,
        batch_contract=lambda candidate: summary if candidate is node else None)

    assert plan.kind == "summary"
    assert plan.contract == summary


def test_conditional_plan_retains_both_source_arms_and_their_count_interval():
    node = _expr('{"x": a} if a else None')
    plan = plan_batch_expression(node, value_contract=_literal_contract)

    assert plan.kind == "choice"
    assert [child.kind for child in plan.children] == ["record", "none"]
    assert (plan.contract.minimum, plan.contract.maximum) == (0, 1)
    assert plan.contract.field_names == ("x",)


def test_a_batch_conditional_cannot_hide_a_scalar_arm():
    node = _expr('{"x": a} if a else 7')
    with pytest.raises(BatchContractError) as caught:
        plan_batch_expression(node, value_contract=_literal_contract)
    assert caught.value.code == "output-batch-arm-disagrees"


def test_list_literal_preserves_source_order_and_joins_field_order():
    node = _expr('[{"x": first()}, {"x": second()}]')
    plan = plan_batch_expression(node, value_contract=_literal_contract)

    assert plan.kind == "list"
    assert [ast.unparse(child.node) for child in plan.children] == [
        "{'x': first()}", "{'x': second()}"]
    assert (plan.contract.minimum, plan.contract.maximum) == (2, 2)


def test_bounded_comprehension_plus_singleton_matches_the_butterworth_shape():
    node = _expr(
        '[{"kind": 0, "real": r, "imag": i} for r, i in poles] + '
        '[{"kind": 1, "real": one, "imag": zero}]')
    row = _record("kind", "real", "imag")
    comprehension = node.left

    def contract(candidate):
        if candidate is comprehension:
            return ValueContract.sequence("list", 0, 16, row)
        return _literal_contract(candidate)

    plan = plan_batch_expression(node, value_contract=contract)

    assert plan.kind == "concat"
    assert [child.kind for child in plan.children] == ["sequence", "list"]
    assert (plan.contract.minimum, plan.contract.maximum) == (1, 17)
    assert plan.contract.field_names == ("imag", "kind", "real")


def test_the_butterworth_shape_is_derived_from_its_collection_contract():
    node = _expr(
        '[{"kind": 0, "real": r, "imag": i} for r, i in poles[n]] + '
        '[{"kind": 1, "real": one, "imag": zero}]')
    pair = ValueContract.sequence("tuple", 2, 2, SCALAR)
    rows = ValueContract.sequence("list", 0, 16, pair)

    def contract(candidate):
        return contract_of_expression(
            candidate,
            resolve=lambda name, _bindings: (
                SCALAR if isinstance(name, ast.Name)
                and name.id in {"n", "one", "zero"} else UNKNOWN),
            select=lambda subscript, _bindings, _contracts: (
                rows if isinstance(subscript.value, ast.Name)
                and subscript.value.id == "poles" else UNKNOWN),
        )

    value = contract(node)
    assert value == ValueContract.sequence(
        "list", 1, 17, _record("kind", "real", "imag"))
    plan = plan_batch_expression(node, value_contract=contract)
    assert (plan.contract.minimum, plan.contract.maximum) == (1, 17)
    assert plan.contract.field_names == ("imag", "kind", "real")


def test_list_concatenation_preserves_left_then_right_in_the_plan_tree():
    node = _expr('[{"x": left()}] + [{"x": right()}]')
    plan = plan_batch_expression(node, value_contract=_literal_contract)

    assert plan.kind == "concat"
    assert ast.unparse(plan.children[0].children[0].node) == "{'x': left()}"
    assert ast.unparse(plan.children[1].children[0].node) == "{'x': right()}"


@pytest.mark.parametrize(
    ("expression", "list_side"),
    [('[{"x": value()}] * count()', "left"),
     ('count() * [{"x": value()}]', "right")],
)
def test_repetition_plan_retains_the_single_source_list_operand(
        expression, list_side):
    node = _expr(expression)
    row = _record("x")

    def contract(candidate):
        if candidate is node:
            return ValueContract.sequence("list", 0, 3, row)
        return _literal_contract(candidate)

    plan = plan_batch_expression(node, value_contract=contract)

    assert plan.kind == "repeat"
    assert plan.children[0].node is getattr(node, list_side)
    assert plan.contract == BatchContract.sequence(
        ValueContract.sequence("list", 0, 3, row))


def test_repetition_requires_one_list_operand_and_a_finite_result():
    two_lists = _expr('[{"x": a}] * [{"x": b}]')
    with pytest.raises(BatchContractError) as count:
        plan_batch_expression(two_lists, value_contract=_literal_contract)
    assert count.value.code == "output-batch-repeat-count-not-integer"

    unbounded = _expr('[{"x": a}] * count()')
    with pytest.raises(BatchContractError) as result:
        plan_batch_expression(unbounded, value_contract=_literal_contract)
    assert result.value.code == "output-batch-repeat-unbounded"


def test_nonrecord_list_element_is_a_typed_batch_boundary():
    node = _expr('[{"x": a}, 3]')
    with pytest.raises(BatchContractError) as caught:
        plan_batch_expression(node, value_contract=_literal_contract)
    assert caught.value.code == "output-batch-element-unsupported"


def test_a_nested_singleton_list_is_not_one_mapping():
    node = _expr('[[{"x": a}]]')

    def contract(candidate):
        if isinstance(candidate, ast.List) and len(candidate.elts) == 1 \
                and isinstance(candidate.elts[0], ast.Dict):
            return ValueContract.sequence(
                "list", 1, 1, _record("x"))
        return _literal_contract(candidate)

    with pytest.raises(BatchContractError) as caught:
        plan_batch_expression(node, value_contract=contract)
    assert caught.value.code == "output-batch-element-unsupported"


def test_explicit_batch_syntax_cannot_fall_back_when_its_contract_is_unknown():
    with pytest.raises(BatchContractError) as mapping:
        plan_batch_expression(
            _expr('{dynamic_key: a}'), value_contract=lambda _node: UNKNOWN)
    assert mapping.value.code == "output-fields-not-static"

    with pytest.raises(BatchContractError) as comprehension:
        plan_batch_expression(
            _expr('[{"x": x} for x in opaque]'),
            value_contract=lambda _node: UNKNOWN)
    assert comprehension.value.code == "output-capacity-unproved"


def test_scalar_and_positional_returns_remain_outside_this_algebra():
    assert plan_batch_expression(
        _expr("a + 1"), value_contract=lambda _node: SCALAR) is None
    assert plan_batch_expression(
        _expr("(a, b)"), value_contract=lambda _node: (
            ValueContract.fixed((2,), "tuple"))) is None


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("dead", [
    "if False:\n            return {'dead': a}",
    "return {'x': a}\n        return {'dead': a}",
], ids=("literal-false", "after-return"))
def test_production_ignores_returns_that_source_control_cannot_reach(
        lowerer, dead):
    """A syntax scan must not let a dead return choose the output schema."""
    if dead.startswith("return"):
        body = dead
    else:
        body = f"{dead}\n        return {{'x': a}}"
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        f"        {body}\n"
    )

    _assert_production_agrees(source, {"a": 5}, [{"x": 5}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_literal_true_return_ifexp_does_not_plan_the_dead_schema(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        return {'x': a} if True else {'dead': a}\n"
    )

    _assert_production_agrees(source, {"a": 6}, [{"x": 6}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize(
    ("condition", "field", "expected"),
    [
        ("False", "dead", [{"x": 5}]),
        ("True", "x", [{"x": 6}, {"x": 5}]),
    ],
    ids=("dead", "live"),
)
def test_static_list_comprehension_filter_controls_element_and_schema(
        lowerer, condition, field, expected):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def bump(self, value):\n"
        "        return value + 1\n"
        "    def process(self, a):\n"
        f"        return ([{{'{field}': self.bump(a)}} "
        f"for _ in [0] if {condition}] + [{{'x': a}}])\n"
    )

    _assert_production_agrees(source, {"a": 5}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_production_list_concat_emits_left_then_right(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        return ([{'tag': 0, 'value': a}, "
        "{'tag': 1, 'value': a + 1}] + "
        "[{'tag': 2, 'value': a + 2}])\n"
    )
    expected = [
        {"tag": 0, "value": 3},
        {"tag": 1, "value": 4},
        {"tag": 2, "value": 5},
    ]

    _assert_production_agrees(source, {"a": 3}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("a", [0, 1])
def test_branch_record_schemas_may_converge_before_return(lowerer, a):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        if a:\n"
        "            out['x'] = 1\n"
        "        else:\n"
        "            out['z'] = 2\n"
        "        out['x'] = 3\n"
        "        out['z'] = 4\n"
        "        return out\n"
    )

    _assert_production_agrees(
        source, {"a": a}, [{"x": 3, "z": 4}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_final_record_schema_divergence_is_refused(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        if a:\n"
        "            out['x'] = 1\n"
        "        else:\n"
        "            out['z'] = 2\n"
        "        return out\n"
    )

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], lowerer=lowerer)
    assert caught.value.code in {
        "helper-return-shape-disagrees",
        "outputs-disagree-across-returns",
    }


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_a_record_write_after_return_is_not_part_of_the_plan(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        out['x'] = a\n"
        "        return out\n"
        "        out['dead'] = a\n"
    )

    _assert_production_agrees(source, {"a": 7}, [{"x": 7}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_break_skips_loop_else_when_building_a_record(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        for i in range(3):\n"
        "            out['x'] = i\n"
        "            break\n"
        "        else:\n"
        "            out['dead'] = a\n"
        "        return out\n"
    )

    _assert_production_agrees(source, {"a": 0}, [{"x": 0}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_continue_still_reaches_loop_else_when_building_a_record(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        for i in range(3):\n"
        "            out[f'x{i}'] = i\n"
        "            continue\n"
        "            out['dead'] = a\n"
        "        else:\n"
        "            out['done'] = 1\n"
        "        return out\n"
    )
    expected = [{"x0": 0, "x1": 1, "x2": 2, "done": 1}]

    _assert_production_agrees(source, {"a": 0}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_one_helper_occurrence_keeps_two_activation_specific_record_plans(
        lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, prefix, value):\n"
        "        out = {}\n"
        "        for i in range(2):\n"
        "            out[f'x{prefix}_{i}'] = value + i\n"
        "        return out\n"
        "    def process(self, a):\n"
        "        left = self.build(0, a)\n"
        "        right = self.build(1, a + 10)\n"
        "        return {'x': left['x0_0'], 'y': right['x1_1']}\n"
    )

    _assert_production_agrees(
        source, {"a": 2}, [{"x": 2, "y": 13}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_helper_returns_an_ordered_transaction_batch(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        return [{'x': value}, {'x': value + 1}]\n"
        "    def process(self, a):\n"
        "        return self.build(a)\n"
    )

    _assert_production_agrees(
        source, {"a": 4}, [{"x": 4}, {"x": 5}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_two_helper_batch_activations_remain_distinct_and_concatenate(
        lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        return [{'x': value}, {'x': value + 1}]\n"
        "    def process(self, a):\n"
        "        left = self.build(a)\n"
        "        right = self.build(a + 10)\n"
        "        return left + right\n"
    )

    _assert_production_agrees(
        source, {"a": 2},
        [{"x": 2}, {"x": 3}, {"x": 12}, {"x": 13}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_batch_binding_survives_nested_helper_return_paths(
        lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, n, value):\n"
        "        if n == 1:\n"
        "            return [{'x': value}]\n"
        "        return [{'x': value}, {'x': value + 1}]\n"
        "    def wrap(self, n, value):\n"
        "        batch = self.build(n, value)\n"
        "        return batch\n"
        "    def process(self, a):\n"
        "        left = self.wrap(1, a)\n"
        "        right = self.wrap(2, a + 10)\n"
        "        return left + right\n"
    )

    _assert_production_agrees(
        source, {"a": 2}, [{"x": 2}, {"x": 12}, {"x": 13}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize(
    ("row", "expected"),
    [({"a": 0}, []), ({"a": 3}, [{"x": 3}, {"x": 4}])],
)
def test_optional_helper_batch_retains_none_distinct_from_a_list(
        lowerer, row, expected):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        if value:\n"
        "            return [{'x': value}, {'x': value + 1}]\n"
        "        return None\n"
        "    def process(self, a):\n"
        "        batch = self.build(a)\n"
        "        if batch is None:\n"
        "            return []\n"
        "        return batch\n"
    )

    _assert_production_agrees(source, row, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize(
    ("row", "expected"),
    [({"a": 0}, [{"x": 10}]),
     ({"a": 1}, [{"x": 11}]),
     ({"a": 2}, [{"x": 2}])],
)
def test_optional_helper_batch_distinguishes_none_empty_and_nonempty_lists(
        lowerer, row, expected):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        if value == 0:\n"
        "            return None\n"
        "        if value == 1:\n"
        "            return []\n"
        "        return [{'x': value}]\n"
        "    def process(self, a):\n"
        "        batch = self.build(a)\n"
        "        if batch is None:\n"
        "            return [{'x': 10}]\n"
        "        if not batch:\n"
        "            return [{'x': 11}]\n"
        "        return batch\n"
    )

    _assert_production_agrees(source, row, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize(
    ("empty", "expected"),
    [("None", []), ("[]", [])],
)
def test_batch_local_initialization_uses_the_later_joined_contract(
        lowerer, empty, expected):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        return [{'x': value}]\n"
        "    def process(self, a):\n"
        f"        batch = {empty}\n"
        "        if a:\n"
        "            batch = self.build(a)\n"
        "        if batch is None:\n"
        "            return []\n"
        "        return batch\n"
    )

    _assert_production_agrees(source, {"a": 0}, expected, lowerer)
    _assert_production_agrees(source, {"a": 3}, [{"x": 3}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_rebinding_a_batch_materializes_the_rhs_before_commit(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        return [{'x': value}]\n"
        "    def process(self, a):\n"
        "        batch = self.build(a)\n"
        "        batch = batch + [{'x': a + 10}]\n"
        "        return batch\n"
    )

    _assert_production_agrees(
        source, {"a": 2}, [{"x": 2}, {"x": 12}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_one_mutable_helper_batch_cannot_be_bound_to_two_names(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        return [{'x': value}]\n"
        "    def process(self, a):\n"
        "        left = right = self.build(a)\n"
        "        return left\n"
    )

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], lowerer=lowerer)
    assert caught.value.code == "batch-value-alias-unsupported"


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_transaction_batch_fields_keep_their_recursive_value_shape(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        return [{'x': (value, value + 1)}]\n"
        "    def process(self, a):\n"
        "        return self.build(a)\n"
    )

    _assert_production_agrees(
        source, {"a": 6}, [{"x": [6, 7]}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_internal_transaction_batch_read_recovers_one_record_value(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def build(self, value):\n"
        "        return [{'pair': (value, value + 1)}, "
        "{'pair': (value + 2, value + 3)}]\n"
        "    def process(self, a):\n"
        "        rows = self.build(a)\n"
        "        row = rows[1]\n"
        "        return {'y': row['pair'][0] * 10 + row['pair'][1]}\n"
    )

    _assert_production_agrees(
        source, {"a": 4}, [{"y": 67}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, [{"x": 0, "last": 1}]),
        (3, [{"x": 3, "last": 0}, {"x": 13, "last": 1}]),
    ],
)
def test_transaction_batch_field_store_updates_the_selected_record(
        lowerer, value, expected):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        rows = []\n"
        "        rows.append({'x': a, 'last': 0})\n"
        "        if a:\n"
        "            rows.append({'x': a + 10, 'last': 0})\n"
        "        rows[-1]['last'] = 1\n"
        "        return rows\n"
    )

    _assert_production_agrees(source, {"a": value}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_transaction_batch_field_store_preserves_index_failure(lowerer):
    empty = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        rows = []\n"
        "        if a:\n"
        "            rows.append({'x': a})\n"
        "        rows[-1]['x'] = 7\n"
        "        return rows\n"
    )
    proc = lower_with_state_ranges(
        empty, input_widths=[1], outputs=["x"], lowerer=lowerer)
    _state, _output, _hits, error = interp.run_step(proc, {"a": 0})
    assert error == "IndexError"

    changes_schema = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        rows = [{'x': a}]\n"
        "        rows[0]['missing'] = 7\n"
        "        return rows\n"
    )
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            changes_schema, input_widths=[1], outputs=["x"], lowerer=lowerer)
    assert caught.value.code == "record-schema-mutation-unsupported"


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_repeated_computed_keys_observe_python_last_write_order(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        for i in range(4):\n"
        "            out[f'x{i % 2}'] = a + i\n"
        "        return out\n"
    )

    _assert_production_agrees(
        source, {"a": 4}, [{"x0": 6, "x1": 7}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_duplicate_literal_keys_evaluate_in_order_and_keep_the_last_value(
        lowerer):
    source = (
        "class M:\n"
        "    def reset(self): self.total = 0\n"
        "    def take(self, value):\n"
        "        self.total += 1\n"
        "        return value\n"
        "    def process(self, a):\n"
        "        return {'x': self.take(a), 'x': self.take(a + 1), "
        "'calls': self.total}\n"
    )

    _assert_production_agrees(
        source, {"a": 4}, [{"x": 5, "calls": 2}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_local_dict_comprehension_is_a_recursive_record_value(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        row = {f'x{i % 2}': {'v': a + i} for i in range(4)}\n"
        "        return {'y': row['x0']['v'] * 10 + row['x1']['v']}\n"
    )

    _assert_production_agrees(source, {"a": 4}, [{"y": 67}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_computed_record_destructures_one_fixed_tuple_target(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        return {f'x{index}': a + delta for index, delta in "
        "((0, 1), (1, 2))}\n"
    )

    _assert_production_agrees(
        source, {"a": 4}, [{"x0": 5, "x1": 6}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", (0, 4))
def test_local_record_recursively_carries_options_and_fixed_sequences(
        lowerer, value):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        row = {'meta': {'pair': (a, a + 1)}, "
        "'maybe': a if a else None}\n"
        "        extra = 10 if row['maybe'] is None else 20\n"
        "        return {'y': row['meta']['pair'][1] + extra}\n"
    )

    expected = value + 11 if value == 0 else value + 21
    _assert_production_agrees(source, {"a": value}, [{"y": expected}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_fresh_fixed_list_inside_a_record_has_value_semantics(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        row = {'cells': [a, a + 1]}\n"
        "        return {'y': row['cells'][0] * 10 + row['cells'][1]}\n"
    )

    _assert_production_agrees(source, {"a": 4}, [{"y": 45}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", (0, 4))
def test_mutable_record_fields_share_the_recursive_product_carrier(
        lowerer, value):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        out['row'] = {'pair': (a, a + 1)}\n"
        "        out['maybe'] = {'x': a + 2} if a else None\n"
        "        extra = 7 if out['maybe'] is None else 9\n"
        "        return {'y': out['row']['pair'][1] + extra}\n"
    )
    expected = value + (8 if value == 0 else 10)
    _assert_production_agrees(
        source, {"a": value}, [{"y": expected}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", (0, 4))
def test_optional_record_field_unwraps_to_its_recursive_payload(
        lowerer, value):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        out['maybe'] = {'x': a + 2} if a else None\n"
        "        if out['maybe'] is None:\n"
        "            y = 7\n"
        "        else:\n"
        "            y = out['maybe']['x']\n"
        "        return {'y': y}\n"
    )
    expected = 7 if value == 0 else value + 2
    _assert_production_agrees(
        source, {"a": value}, [{"y": expected}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", (0, 4))
def test_nested_record_field_keeps_option_and_fixed_array_layouts(
        lowerer, value):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        out['row'] = {'cells': [a, a + 1], "
        "'maybe': {'x': a + 2} if a else None}\n"
        "        if out['row']['maybe'] is None:\n"
        "            y = out['row']['cells'][0] + 7\n"
        "        else:\n"
        "            y = (out['row']['cells'][1] + "
        "out['row']['maybe']['x'])\n"
        "        return {'y': y}\n"
    )
    expected = 7 if value == 0 else 2 * value + 3
    _assert_production_agrees(
        source, {"a": value}, [{"y": expected}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_returned_dict_comprehension_keeps_repeated_key_last_write(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        return {f'x{i % 2}': a + i for i in range(4)}\n"
    )

    _assert_production_agrees(
        source, {"a": 4}, [{"x0": 6, "x1": 7}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_a_closure_cannot_mutate_its_callers_record_builder(lowerer):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        "        def add(value):\n"
        "            out['x'] = value\n"
        "        add(a)\n"
        "        return out\n"
    )

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], lowerer=lowerer)
    assert caught.value.code in {
        "record-builder-alias-unsupported",
        "record-builder-mutation-unsupported",
    }


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("body, expected", [
    ("out = {}\nout.update(x=a, y=a+1)", [{"x": 4, "y": 5}]),
    ("out = {'x': a, 'y': a+1}\nout.update(x=out['y'], y=out['x'])",
     [{"x": 5, "y": 4}]),
    ("out = {'x': a}\nif a:\n    out.update(x=a+2)\nout.update()", [{"x": 6}]),
    ("out = {'x': a}\nfor i in range(3):\n    out.update(x=out['x']+i)", [{"x": 7}]),
    ("out = {'x': a}\nvalue = out.update(x=a+1)\nif value is None:\n    out['x'] += 1",
     [{"x": 6}]),
    ("out = {'x': (a, a+1), 'y': (1, 2)}\nout.update(x=out['y'], y=out['x'])",
     [{"x": [1, 2], "y": [4, 5]}]),
])
def test_record_keyword_update_retains_argument_values_and_order(lowerer, body, expected):
    source = ("class M:\n    def reset(self): pass\n    def process(self, a):\n"
              + "\n".join("        " + line for line in body.splitlines())
              + "\n        return out\n")
    _assert_production_agrees(source, {"a": 4}, expected, lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_record_keyword_update_evaluates_stateful_arguments_once(lowerer):
    source = """\
class M:
    def reset(self): self.n = 0
    def next_value(self):
        self.n = (self.n + 1) & 15
        return self.n
    def process(self, a):
        out = {'x': 0, 'y': 0}
        out.update(x=self.next_value(), y=self.next_value())
        out['z'] = self.n
        return out
"""
    _assert_production_agrees(source, {"a": 4}, [{"x": 1, "y": 2, "z": 2}], lowerer)


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("operation", [
    "out.update(x=out)",
    "out.update(x=(out := {'x': a}))",
    "def change():\n    out.update(x=a)\nchange()",
])
def test_record_keyword_update_does_not_erase_receiver_aliasing(lowerer, operation):
    source = ("class M:\n    def reset(self): pass\n    def process(self, a):\n"
              "        out = {'x': 0}\n"
              + "\n".join("        " + line for line in operation.splitlines())
              + "\n        return out\n")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], lowerer=lowerer)
    assert caught.value.code in {
        "record-builder-alias-unsupported", "record-builder-mutation-unsupported",
    }


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
@pytest.mark.parametrize("value", ["out['y']", "values"])
@pytest.mark.parametrize("target", ["out['y'][0]", "values[0]"])
def test_record_keyword_update_cannot_copy_a_mutated_shared_child(lowerer, value, target):
    source = f"""\
class M:
    def reset(self): pass
    def process(self, a):
        values = [2]
        out = {{'x': [1], 'y': values}}
        out.update(x={value})
        {target} = a
        return {{'y': out['x'][0]}}
"""
    try:
        actual = _production_transactions(source, {"a": 9}, lowerer)
    except LoweringError as error:
        assert error.code in {'array-assigned-by-reference', 'mutation-through-view',
                              'record-mutation-unsupported',
                              'record-builder-alias-unsupported', 'record-builder-mutation-unsupported'}
    else:
        # An admitted representation must preserve identity, never just copy
        # the old child value (which would return 2 instead of Python's 9).
        assert actual == [{"y": 9}]


@pytest.mark.parametrize("lowerer", _PRODUCTION_LOWERERS)
def test_seven_identical_schema_joins_do_not_form_128_builder_paths(lowerer):
    decisions = "\n".join(
        f"        if a & {1 << bit}:\n"
        f"            out['x'] = {2 * bit + 1}\n"
        "        else:\n"
        f"            out['x'] = {2 * bit}"
        for bit in range(7)
    )
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        "        out = {}\n"
        f"{decisions}\n"
        "        return out\n"
    )

    _assert_production_agrees(source, {"a": 5}, [{"x": 12}], lowerer)
