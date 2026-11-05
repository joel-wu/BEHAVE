"""The finite Optional source contract shared by every source planner."""

from __future__ import annotations

import ast
import itertools

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.lowering import (
    LoweringError,
    _Lowerer,
    lower_with_state_ranges,
)
from behave.sim.behavior.source_options import (
    NONE,
    OptionValue,
    narrow_none,
    none_test,
)
from behave.sim.behavior.source_sequences import (
    SequencePlanError,
    _FunctionContracts,
    _ScalarBindings,
)
from behave.sim.behavior.source_values import (
    INCOMPATIBLE,
    SCALAR,
    UNKNOWN,
    ValueContract,
    contract_contains,
    contract_of_expression,
    contract_of_value,
    contracts_may_overlap,
    join_contracts,
)


def test_none_and_integer_zero_have_different_contracts():
    optional = join_contracts(NONE, SCALAR)

    assert contract_of_value(None) is NONE
    assert contract_of_expression(ast.Constant(value=None)) is NONE
    assert contract_of_value(0) is SCALAR
    assert optional == ValueContract.option(SCALAR)
    assert optional != SCALAR
    assert narrow_none(optional, is_none=True) is NONE
    assert narrow_none(optional, is_none=False) is SCALAR


def test_optional_payload_reuses_recursive_value_contracts():
    row = ValueContract.record({
        "distance": SCALAR,
        "point": ValueContract.fixed((4,), "tuple"),
    })

    assert join_contracts(NONE, row) == ValueContract.option(row)


def test_option_join_is_associative():
    sequence = ValueContract.fixed((3,), "tuple")
    values = (NONE, SCALAR, sequence, ValueContract.option(SCALAR),
              UNKNOWN, INCOMPATIBLE)

    for left, middle, right in itertools.product(values, repeat=3):
        assert join_contracts(
            join_contracts(left, middle), right) == \
            join_contracts(
                left, join_contracts(middle, right))


def test_option_containment_is_refinement_not_presence_erasure():
    payload = ValueContract.sequence("list", 0, 4, SCALAR)
    exact = ValueContract.sequence("list", 2, 2, SCALAR)
    optional = ValueContract.option(payload)

    assert contract_contains(optional, NONE)
    assert contract_contains(optional, exact)
    assert contract_contains(optional, ValueContract.option(exact))
    assert not contract_contains(payload, optional)
    assert not contract_contains(NONE, exact)


def test_optional_contracts_overlap_on_none_even_when_payloads_do_not():
    scalar = ValueContract.option(SCALAR)
    sequence = ValueContract.option(ValueContract.fixed((2,), "tuple"))

    assert contracts_may_overlap(scalar, sequence)
    assert contracts_may_overlap(NONE, sequence)
    assert not contracts_may_overlap(NONE, SCALAR)
    assert contracts_may_overlap(UNKNOWN, NONE)


@pytest.mark.parametrize("payload", [UNKNOWN, INCOMPATIBLE])
def test_an_optional_payload_needs_a_known_representation(payload):
    with pytest.raises(ValueError):
        ValueContract.option(payload)


@pytest.mark.parametrize(("source", "operand", "none_when_true"), [
    ("x is None", "x", True),
    ("None is x", "x", True),
    ("x is not None", "x", False),
    ("None is not x", "x", False),
])
def test_exact_none_identity_tests_are_recognized(
        source, operand, none_when_true):
    test = none_test(ast.parse(source, mode="eval").body)

    assert isinstance(test.operand, ast.Name)
    assert test.operand.id == operand
    assert test.none_when_true is none_when_true


@pytest.mark.parametrize("source", [
    "x == None",
    "x != None",
    "x is y",
    "None is None",
    "x is None is y",
])
def test_operations_other_than_one_none_identity_test_are_not_tags(source):
    assert none_test(ast.parse(source, mode="eval").body) is None


def test_unreachable_none_arms_are_explicit():
    assert narrow_none(SCALAR, is_none=True) is INCOMPATIBLE
    assert narrow_none(NONE, is_none=False) is INCOMPATIBLE


def test_lowering_boundary_is_one_tag_and_one_existing_payload():
    none = OptionValue(present="false", payload=None, contract=NONE)
    optional = OptionValue(
        present="tag", payload="payload",
        contract=ValueContract.option(SCALAR))

    assert (none.present, none.payload) == ("false", None)
    assert (optional.present, optional.payload) == ("tag", "payload")
    with pytest.raises(ValueError):
        OptionValue("tag", None, ValueContract.option(SCALAR))
    with pytest.raises(ValueError):
        OptionValue("true", "payload", SCALAR)


def _helper_summary(body):
    source = "class M:\n    def reset(self):\n        pass\n" + \
        "    def helper(self, flag):\n" + \
        "\n".join(f"        {line}" for line in body.splitlines()) + \
        "\n    def process(self, a):\n        return {\"y\": 0}\n"
    model = source_model(source)
    contracts = _FunctionContracts(model, ScopeResolver(model))
    helper = model.helpers["helper"]
    result = contracts.summarize(helper, (SCALAR,), (None,))
    return result, contracts, helper


def test_helper_fallthrough_joins_with_a_present_return():
    result, _contracts, _helper = _helper_summary(
        "if flag:\n    return 7")

    assert result == ValueContract.option(SCALAR)


def test_none_test_prunes_an_unreachable_representation_arm():
    result, _contracts, _helper = _helper_summary(
        "value = 7\n"
        "if value is None:\n"
        "    return [1, 2]\n"
        "return value")

    assert result is SCALAR


def test_short_circuit_none_test_narrows_the_false_path():
    result, contracts, helper = _helper_summary(
        "value = None\n"
        "if flag:\n"
        "    value = 7\n"
        "if value is None or flag:\n"
        "    value = 3\n"
        "return value")

    assert contracts.binding_contract(helper, "value") == \
        ValueContract.option(SCALAR)
    assert result is SCALAR


def test_exact_unpacking_propagates_the_none_element_contract():
    result, contracts, helper = _helper_summary(
        "left, right = (None, None)\n"
        "return left if flag else right")

    assert contracts.binding_contract(helper, "left") is NONE
    assert contracts.binding_contract(helper, "right") is NONE
    assert result is NONE


_LOCAL_OPTION = """
class M:
    def reset(self):
        pass

    def process(self, a):
        value = None
        if a:
            value = 0
        if value is None or value < 1:
            y = 7
        else:
            y = 9
        return {"y": y}
"""


def _outputs(proc, values):
    rows = []
    state = None
    for value in values:
        state, out, _hits, error = interp.run_step(
            proc, {"a": value}, state=state)
        assert not error
        rows.append([dict(row) for row in out.transactions])
    return rows


def _native_outputs(source, values):
    namespace = {}
    exec(source, namespace)  # noqa: S102 -- the test source is local above
    model = namespace["M"]()
    model.reset()
    rows = []
    for value in values:
        out = model.process(value)
        rows.append([] if out is None else [dict(out)])
    return rows


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_local_option_is_tagged_and_unwrapped_on_both_lowerers(lowerer):
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    proc = lower_with_state_ranges(
        _LOCAL_OPTION, input_widths=[2], **keywords)

    assert _outputs(proc, [0, 1]) == _native_outputs(
        _LOCAL_OPTION, [0, 1])


_LOOP_OPTION = """
class M:
    def reset(self):
        pass

    def process(self, a):
        best = None
        for index in range(2):
            candidate = a + index
            if best is None or candidate < best:
                best = candidate
        return {"y": best}
"""


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_nonempty_loop_preserves_short_circuit_option_refinement(lowerer):
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    proc = lower_with_state_ranges(
        _LOOP_OPTION, input_widths=[3], **keywords)

    assert _outputs(proc, [0, 3]) == _native_outputs(
        _LOOP_OPTION, [0, 3])


_HELPER_OPTION = """
class M:
    def reset(self):
        pass

    def choose(self, a):
        if a:
            return 9

    def process(self, a):
        value = self.choose(a)
        if value is None:
            y = 2
        else:
            y = value
        return {"y": y}
"""


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_helper_return_and_fallthrough_share_one_option_product(lowerer):
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    proc = lower_with_state_ranges(
        _HELPER_OPTION, input_widths=[2], **keywords)

    assert _outputs(proc, [0, 1]) == _native_outputs(
        _HELPER_OPTION, [0, 1])


_OPTION_ARGUMENT = """
class M:
    def reset(self):
        pass

    def consume(self, value):
        if value is None:
            return 4
        return value

    def process(self, a):
        value = 8 if a else None
        return {"y": self.consume(value)}
"""


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_conditional_option_crosses_a_helper_argument_boundary(lowerer):
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    proc = lower_with_state_ranges(
        _OPTION_ARGUMENT, input_widths=[2], **keywords)

    assert _outputs(proc, [0, 1]) == _native_outputs(
        _OPTION_ARGUMENT, [0, 1])


_RECORD_OPTION = """
class M:
    def reset(self):
        pass

    def process(self, a):
        row = ({"x": a, "pair": (a, a + 1)} if a else None)
        if row is None:
            y = 3
        else:
            y = row["x"] + row["pair"][1]
        return {"y": y}
"""


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_record_valued_option_uses_one_recursive_payload_carrier(lowerer):
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    proc = lower_with_state_ranges(
        _RECORD_OPTION, input_widths=[3], **keywords)

    assert _outputs(proc, [0, 2]) == _native_outputs(
        _RECORD_OPTION, [0, 2])


def test_unproved_optional_payload_use_is_typed_refused():
    source = """
class M:
    def reset(self):
        pass

    def process(self, a):
        value = None
        if a:
            value = 3
        y = value + 1
        return {"y": y}
"""

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2])
    assert caught.value.code == "optional-value-not-unwrapped"


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("expression", [
    "value + 2", "2 + value", "value - 2", "2 - value",
    "value * 2", "2 * value", "value // 2", "2 // value",
    "value % 2", "2 % value", "value << 2", "2 >> value",
    "value & 2", "2 | value", "value ^ 2",
])
@pytest.mark.parametrize("use", [
    "y = EXPR\n        return {'y': y}",
    "return {'y': (EXPR) + 1}",
    "return {'y': self.identity(EXPR)}",
])
def test_optional_binary_operand_is_reported_at_operation(lowerer, expression, use):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def identity(self, x): return x\n"
        "    def process(self, a):\n"
        "        value = 3 if a else None\n"
        f"        {use.replace('EXPR', expression)}\n"
    )
    keywords = {} if lowerer is None else {"lowerer": lowerer}

    with pytest.raises(TypeError):
        _native_outputs(source, [0])
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], **keywords)
    assert caught.value.code == "optional-value-not-unwrapped"
    assert caught.value.node.lineno == 6
    assert ast.unparse(caught.value.node) == expression


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("setup", [
    "value = 3 if a else None",
    "value = None\n        if a:\n            value = 3",
])
def test_optional_augassign_keeps_its_source_location(lowerer, setup):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def process(self, a):\n"
        f"        {setup}\n"
        "        value += 2\n"
        "        return {'y': value}\n"
    )
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], **keywords)
    assert caught.value.code == "optional-value-not-unwrapped"
    assert caught.value.node.lineno == source.splitlines().index('        value += 2') + 1


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("body", [
    "if value is None:\n            return 7\n        return value + 2",
    "return 7 if value is None else value + 2",
    "return value is None or value + 2 > 0",
    "return value is None or value + 2",
    "if value is not None:\n            value += 2\n            return value\n        return 7",
])
def test_guarded_optional_operations_preserve_native_values(lowerer, body):
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def helper(self, value):\n"
        f"        {body}\n"
        "    def process(self, a):\n"
        "        value = 3 if a else None\n"
        "        x = self.helper(4)\n"
        "        return {'y': x + self.helper(value)}\n"
    )
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    proc = lower_with_state_ranges(source, input_widths=[2], **keywords)
    assert _outputs(proc, range(4)) == _native_outputs(source, range(4))


@pytest.mark.parametrize("absent", [NONE, ValueContract.option(SCALAR)])
def test_cached_scalar_query_does_not_hide_an_optional_operand(absent):
    model = source_model('''class M:
    def reset(self): pass
    def process(self, a):
        return {"y": a + 2}
''')
    contracts = _FunctionContracts(model, ScopeResolver(model))
    contracts._context.append(("test",))
    expression = model.step.body[0].value.values[0]

    def query(value):
        return contracts._expr(
            model.step, expression, {"a": value}, _ScalarBindings())

    assert query(SCALAR) is SCALAR
    with pytest.raises(SequencePlanError) as caught:
        query(absent)
    assert caught.value.code == "optional-value-not-unwrapped"
    assert caught.value.node is expression
    assert query(SCALAR) is SCALAR


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("optional_first", [False, True])
def test_helper_diagnostic_is_activation_specific(lowerer, optional_first):
    arguments = ("value", "3") if optional_first else ("3", "value")
    source = (
        "class M:\n"
        "    def reset(self): pass\n"
        "    def helper(self, x):\n"
        "        return x + 2\n"
        "    def process(self, a):\n"
        "        value = 3 if a else None\n"
        f"        first = self.helper({arguments[0]})\n"
        f"        return {{'y': first + self.helper({arguments[1]})}}\n"
    )
    keywords = {} if lowerer is None else {"lowerer": lowerer}
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], **keywords)
    assert caught.value.code == "optional-value-not-unwrapped"
    assert caught.value.node.lineno == 4
    assert ast.unparse(caught.value.node) == "x + 2"


def test_complete_process_none_remains_the_zero_transaction_boundary():
    source = """
class M:
    def reset(self):
        pass

    def process(self, a):
        if a:
            return {"y": 3}
        return None
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert _outputs(proc, [0, 1]) == _native_outputs(source, [0, 1])


def test_unbounded_plan_does_not_treat_reset_value_as_every_step_value():
    source = """
class M:
    def reset(self):
        self.seen = 0

    def process(self, a):
        result = None
        if self.seen:
            result = {"y": a}
        self.seen = 1
        return result
"""
    proc = lower_with_state_ranges(source)

    assert _outputs(proc, [2, 5]) == _native_outputs(source, [2, 5])
