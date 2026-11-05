"""Soundness checks for source-level value and helper contracts."""

from __future__ import annotations

import ast
import gc
import itertools
import textwrap
import weakref

import pytest

from behave.sim.symbolic import interp, ir, ranges
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_sequences import SequencePlan, _FunctionContracts
from behave.sim.behavior.source_values import (
    INCOMPATIBLE,
    SCALAR,
    UNKNOWN,
    SourceValueBudget,
    ValueContract,
    contract_contains,
    contract_of_expression,
    join_contracts,
    representation_value,
)
from behave.sim.symbolic.uniform_array import UniformArray


def _source(helper_body, *, helpers=""):
    body = textwrap.indent(textwrap.dedent(helper_body).strip(), " " * 8)
    more = textwrap.indent(textwrap.dedent(helpers).strip(), " " * 4)
    middle = f"{more}\n" if more.strip() else ""
    return ("class M:\n"
            "    def reset(self):\n"
            "        pass\n"
            f"{middle}"
            "    def helper(self, flag):\n"
            f"{body}\n"
            "    def process(self, a):\n"
            "        return {\"y\": self.helper(a)}\n")


def _helper_contract(helper_body, *, helpers=""):
    model = source_model(_source(helper_body, helpers=helpers))
    contracts = _FunctionContracts(model, ScopeResolver(model))
    result = contracts.summarize(
        model.helpers["helper"], (SCALAR,), (None,))
    return result, contracts, model


@pytest.mark.parametrize(("expression", "expected"), [
    ("unknown or 1", INCOMPATIBLE),
    ("-unknown", UNKNOWN),
])
def test_unknown_operand_is_not_guessed_to_be_scalar(expression, expected):
    node = ast.parse(expression, mode="eval").body

    assert contract_of_expression(node, resolve=lambda _node, _env: UNKNOWN) \
        is expected


@pytest.mark.parametrize('expression', ['unknown << 2', '2 >> unknown',
                                       'unknown & 255', '1 | unknown', 'unknown ^ 1'])
def test_integer_operator_normal_result_contract(expression):
    # This states the type on normal return, not that the operands are valid.
    node = ast.parse(expression, mode='eval').body
    assert contract_of_expression(node, resolve=lambda _node, _env: UNKNOWN) is SCALAR


@pytest.mark.parametrize('expression', ['a & b', 'a | b', 'a ^ b', 'a + 1', 'a * 2'])
def test_unknown_operator_result_does_not_erase_collection_alternatives(expression):
    node = ast.parse(expression, mode='eval').body
    assert contract_of_expression(node, resolve=lambda _node, _env: UNKNOWN) is not SCALAR


def test_scalar_multiplication_does_not_request_a_sequence_length():
    node = ast.parse("(a if a else 7) * 256", mode="eval").body

    def no_sequence_length(_node, _bindings):
        pytest.fail("a scalar product has no sequence multiplier")

    assert contract_of_expression(
        node,
        resolve=lambda _node, _env: SCALAR,
        integer_interval=no_sequence_length,
    ) is SCALAR


def test_compact_interpreter_arrays_have_the_componentwise_ir_value():
    value = UniformArray.uniform(3, [0, 1]).store(1, [2, 3])

    assert representation_value(value) == [[0, 1], [2, 3], [0, 1]]


def test_lowering_contract_queries_share_one_procedure_budget():
    lowerer = _Lowerer([])
    lowerer._source_value_budget = SourceValueBudget(2)
    node = ast.parse("1", mode="eval").body

    assert lowerer._contract_ast(node) is SCALAR
    assert lowerer._contract_ast(node) is SCALAR
    with pytest.raises(LoweringError) as caught:
        lowerer._contract_ast(node)
    assert caught.value.code == "source-plan-budget-exceeded"


def test_unplanned_call_does_not_rederive_its_arguments(monkeypatch):
    lowerer = _Lowerer([])
    lowerer.sequence_plan = SequencePlan()
    node = ast.parse("abs(a + b)", mode="eval").body

    def no_helper_arguments(*_args, **_kwargs):
        pytest.fail("no activation of this occurrence has a helper summary")

    monkeypatch.setattr(lowerer, "_source_call_arguments", no_helper_arguments)
    assert lowerer._planned_call_contract(node) is UNKNOWN
    # The ordinary expression walker still evaluates builtin arguments and
    # establishes their contracts; only the absent helper-summary query skips.
    assert lowerer._contract_ast(
        ast.parse("abs(1 + 2)", mode="eval").body) is SCALAR


def test_occurrence_cache_retains_the_ast_object_not_a_recyclable_id():
    lowerer = _Lowerer([])
    scope = ast.parse("def process(): pass").body[0]
    context = ("entry",)
    temporary = ast.Constant(3)
    lowerer.activation.scope = scope
    lowerer.activation.summary = context
    lowerer.sequence_plan = SequencePlan(
        context_functions=((context, scope),),
        expression_contracts=(((context, id(temporary)), SCALAR),),
    )
    assert lowerer._contract_ast(temporary) is SCALAR
    reference = weakref.ref(temporary)
    del temporary
    gc.collect()
    assert reference() is not None
    assert lowerer._contract_ast(ast.Constant(None)).is_none


def test_reused_literal_contract_does_not_freeze_sibling_bindings():
    lowerer = _Lowerer([], env={"a": ranges.Range(0, 7)})
    lowerer._source_value_budget = SourceValueBudget(100)
    node = ast.parse("a + 3", mode="eval").body

    assert lowerer._contract_ast(node) is SCALAR
    charged_once = 100 - lowerer._source_value_budget.left
    for value in (0, 1, 7):
        before = lowerer._source_value_budget.left
        assert lowerer._contract_ast(node, bindings={"a": value}) is SCALAR
        assert before - lowerer._source_value_budget.left == charged_once - 1
    sequence = ValueContract.sequence("list", 1, 1, SCALAR)
    # A sequence-plus-integer has no represented value contract. It must not
    # inherit the scalar answer from the earlier bindings.
    assert lowerer._contract_ast(node, contracts={"a": sequence}) is UNKNOWN
    assert lowerer._contract_ast(node, contracts={"a": UNKNOWN}) is UNKNOWN
    assert len(lowerer._source_literal_contracts) == 1


def test_call_index_keeps_occurrences_contexts_and_fixed_arguments_distinct():
    first = ast.parse("helper(a)", mode="eval").body
    second = ast.parse("helper(a)", mode="eval").body
    absent = ast.parse("helper(a)", mode="eval").body
    caller, other_caller = ("caller",), ("other",)
    rows = (
        ((caller, id(first), (SCALAR,), (("int", 1),), ()), SCALAR),
        ((caller, id(first), (SCALAR,), (("int", 2),), ()),
         ValueContract.sequence("list", 2, 2, SCALAR)),
        ((other_caller, id(first), (SCALAR,), (("int", 1),), ()),
         ValueContract.sequence("tuple", 1, 1, SCALAR)),
        ((caller, id(second), (SCALAR,), (("int", 1),), ()),
         ValueContract.sequence("list", 3, 3, SCALAR)),
    )
    plan = SequencePlan(
        call_contexts=rows,
        call_targets=((key, ("callee", index))
                      for index, (key, _contract) in enumerate(rows)),
    )

    assert plan.has_call(first) and plan.has_call(second)
    assert not plan.has_call(absent)
    for index, (key, contract) in enumerate(rows):
        node = first if key[1] == id(first) else second
        assert plan.call_variants(
            node, (SCALAR,), (key[3][0][1],), key[0]) == (
                ((), contract, ("callee", index)),)
    assert len(plan.call_variants(first, (SCALAR,), (None,), caller)) == 2
    assert plan.call_variants(absent, (SCALAR,), (1,), caller) == ()


def test_truth_use_analysis_is_shared_only_within_the_same_lexical_body(monkeypatch):
    first, second = ast.parse("""
def first(a, b, c, d, e, f, mixed):
    if a and b:
        assert not c
    selected = d if e else f
    if mixed:
        pass
    return selected + mixed

def second(a):
    return a
""").body
    lowerer = _Lowerer([])
    lowerer.activation.scope = first
    expected = {"a": True, "b": True, "c": True, "e": True,
                "d": False, "f": False, "mixed": False, "absent": False}
    for spelling, truth_only in expected.items():
        assert lowerer._local_has_only_truth_uses(spelling) is truth_only
    lowerer.activation.scope = second
    assert not lowerer._local_has_only_truth_uses("a")

    def no_rescan(*_args):
        pytest.fail("the frozen lexical body was already analyzed")

    monkeypatch.setattr(ast, "iter_child_nodes", no_rescan)
    lowerer.activation.scope = first
    for spelling, truth_only in expected.items():
        assert lowerer._local_has_only_truth_uses(spelling) is truth_only
    lowerer.activation.scope = second
    assert not lowerer._local_has_only_truth_uses("a")


def test_nested_call_arguments_execute_once_in_the_source_summary():
    source = """class M:
    def reset(self):
        self.n = 0

    def bump(self):
        self.n = self.n + 1
        return self.n

    def take(self, value):
        return value

    def process(self, a):
        return {"y": self.take(self.bump())}
"""
    model = source_model(source)
    contracts = _FunctionContracts(
        model, ScopeResolver(model), declarations={"n": 0},
        declaration_contracts={"n": SCALAR})
    argument_range = ranges.Range(0, 15)
    state_range = ranges.Range(0, 0)
    context = contracts.context_key(
        model.step, (SCALAR,), (None,), (), (argument_range,),
        {"n": SCALAR}, {"n": state_range})

    contracts.summarize(
        model.step, (SCALAR,), (None,),
        argument_ranges=(argument_range,),
        state_contracts={"n": SCALAR}, state_ranges={"n": state_range})

    assert contracts.final_state_ranges(context)["n"] == ranges.Range(1, 1)


def test_name_missing_on_one_branch_is_not_a_value_contract_conflict():
    contract, _contracts, _model = _helper_contract("""
        if flag:
            value = 1
        return value
    """)

    # A missing binding is a control-flow fact, not a second value shape.
    # The CFG/entry-state pass owns the eventual unassigned read; guessing an
    # incompatible value contract here used to make two authorities race to
    # classify the same source error.
    assert contract is UNKNOWN


def test_contract_join_is_associative_and_keeps_observed_conflicts():
    contracts = (
        UNKNOWN,
        INCOMPATIBLE,
        SCALAR,
        ValueContract.sequence(None, 0, 2, SCALAR),
        ValueContract.sequence("list", 0, 2, SCALAR),
        ValueContract.sequence("tuple", 1, 1, SCALAR),
    )
    for left, middle, right in itertools.product(contracts, repeat=3):
        assert join_contracts(join_contracts(left, middle), right) == \
            join_contracts(left, join_contracts(middle, right))


def test_fixed_for_break_stops_later_iterations():
    contract, _contracts, _model = _helper_contract("""
        built = []
        for value in range(4):
            built.append(value)
            break
        return built
    """)

    assert contract == ValueContract.sequence("list", 1, 1, SCALAR)


def test_fixed_for_continue_skips_the_rest_of_the_iteration():
    contract, _contracts, _model = _helper_contract("""
        built = []
        for value in range(4):
            continue
            built.append(value)
        return built
    """)

    assert contract == ValueContract.sequence("list", 0, 0)


@pytest.mark.parametrize("control", ["break", "continue"])
def test_branch_loop_exit_contributes_a_zero_length_path(control):
    contract, _contracts, _model = _helper_contract(f"""
        built = []
        for value in range(4):
            if flag:
                {control}
            built.append(value)
        return built
    """)

    assert contract == ValueContract.sequence("list", 0, 4, SCALAR)


def test_nested_break_is_consumed_by_the_innermost_loop():
    contract, _contracts, _model = _helper_contract("""
        built = []
        for row in range(2):
            for column in range(3):
                built.append(column)
                break
        return built
    """)

    assert contract == ValueContract.sequence("list", 2, 2, SCALAR)


def test_return_in_loop_excludes_dead_tail_from_the_contract():
    contract, _contracts, _model = _helper_contract("""
        built = []
        for value in range(4):
            built.append(value)
            return built
        built.append([1, 2])
        return built
    """)

    assert contract == ValueContract.sequence("list", 1, 1, SCALAR)


def test_return_branches_join_only_their_reachable_values():
    contract, _contracts, _model = _helper_contract("""
        if flag:
            return [1]
        return [1, 2]
    """)

    assert contract == ValueContract.sequence("list", 1, 2, SCALAR)


def test_break_path_skips_for_else():
    contract, _contracts, _model = _helper_contract("""
        built = []
        for value in range(2):
            break
        else:
            built.append(1)
        return built
    """)

    assert contract == ValueContract.sequence("list", 0, 0)


def test_empty_loop_runs_for_else_before_return():
    contract, _contracts, _model = _helper_contract("""
        built = []
        for value in []:
            pass
        else:
            built.append(1)
        return built
    """)

    assert contract == ValueContract.sequence("list", 1, 1, SCALAR)


def test_while_break_and_exhaustion_keep_else_paths_separate():
    contract, _contracts, _model = _helper_contract("""
        built = []
        while flag:
            built.append(flag)
            break
        else:
            built.append(0)
        return built
    """)

    assert contract == ValueContract.sequence("list", 1, 1, SCALAR)


def test_exit_only_while_contract_does_not_invent_an_ir_trip_plan():
    source = _source("""
        built = []
        while flag:
            built.append(flag)
            break
        else:
            built.append(0)
        return built
    """)

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4])
    assert caught.value.code == "while-trip-plan-unavailable"


def test_while_continue_skips_the_rest_of_the_iteration():
    contract, _contracts, _model = _helper_contract("""
        built = []
        count = 0
        while count < 2:
            count += 1
            continue
            built.append(count)
        return built
    """)

    assert contract == ValueContract.sequence("list", 0, 0)


def test_compare_visits_helper_call_for_reachability():
    contract, contracts, model = _helper_contract("""
        return self.first(flag) == 0
    """, helpers="""
        def first(self, value):
            built = []
            built.append(value)
            return built[0]
    """)

    assert contract is SCALAR
    assert model.helpers["first"] in contracts.reachable


def test_conditional_expression_visits_its_test_for_reachability():
    contract, contracts, model = _helper_contract("""
        return 1 if self.first(flag) else 0
    """, helpers="""
        def first(self, value):
            built = []
            built.append(value)
            return built[0]
    """)

    assert contract is SCALAR
    assert model.helpers["first"] in contracts.reachable


@pytest.mark.parametrize("expression", [
    "False and hidden()",
    "True or hidden()",
    "hidden() if False else 1",
])
def test_static_control_does_not_visit_an_unreachable_call(expression):
    reached = []
    node = ast.parse(expression, mode="eval").body

    contract = contract_of_expression(
        node, call=lambda call, _bindings, _contracts: reached.append(call))

    assert contract is SCALAR
    assert reached == []


def test_dynamic_comprehension_visits_its_body_for_reachability():
    _contract, contracts, model = _helper_contract("""
        values = []
        if flag:
            values.append(flag)
        return [self.first(value) for value in values]
    """, helpers="""
        def first(self, value):
            built = []
            built.append(value)
            return built[0]
    """)

    assert model.helpers["first"] in contracts.reachable


def test_static_comprehension_target_has_a_scalar_contract():
    contract, _contracts, _model = _helper_contract("""
        return [value for value in range(2)]
    """)

    assert contract == ValueContract.sequence("list", 2, 2, SCALAR)


@pytest.mark.parametrize("body", [
    "return {value: self.first(value) for value in range(2)}",
    "return sum(self.first(value) for value in range(2))",
])
def test_other_executed_comprehensions_visit_their_payload(body):
    _contract, contracts, model = _helper_contract(body, helpers="""
        def first(self, value):
            built = []
            built.append(value)
            return built[0]
    """)

    assert model.helpers["first"] in contracts.reachable


def test_attribute_augassign_visits_its_value_for_reachability():
    contract, contracts, model = _helper_contract("""
        self.total += self.first(flag)
        return 0
    """, helpers="""
        def first(self, value):
            built = []
            built.append(value)
            return built[0]
    """)

    assert contract is SCALAR
    assert model.helpers["first"] in contracts.reachable


def test_subscript_assignment_visits_its_index_for_reachability():
    contract, contracts, model = _helper_contract("""
        built = [0]
        built[self.index(flag)] = 1
        return built
    """, helpers="""
        def index(self, value):
            temporary = []
            temporary.append(value)
            return 0
    """)

    assert contract == ValueContract.sequence("list", 1, 1, SCALAR)
    assert model.helpers["index"] in contracts.reachable


def _run_one(source, value):
    proc = lower_with_state_ranges(source, input_widths=[3])
    _state, output, _hits, error = interp.run_step(proc, {"a": value})
    assert error == ""
    return output.transactions[0]["y"]


def test_path_source_plan_can_resolve_a_scalar_helper_arm():
    source = """\
from fp_lib import fp32_add

class M:
    def reset(self):
        self.running_sum = 0
        self.active = 0
    def process(self, a):
        next_sum = a if self.active == 0 else fp32_add(self.running_sum, a)
        self.running_sum = next_sum
        self.active = 1
        return {"y": next_sum}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[32], outputs=["y"])
    state = None
    observed = []
    for value in (0x3F800000, 0x40000000):
        state, output, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert error == ""
        observed.append(output.transactions[0]["y"])

    assert observed == [0x3F800000, 0x40400000]


def test_contract_containment_is_refinement_not_widening():
    exact_two = ValueContract.sequence("list", 2, 2, SCALAR)
    up_to_three = ValueContract.sequence("list", 0, 3, SCALAR)
    nested_two = ValueContract.sequence("list", 2, 2, exact_two)

    assert contract_contains(up_to_three, exact_two)
    assert not contract_contains(exact_two, up_to_three)
    assert not contract_contains(
        nested_two, ValueContract.sequence("list", 2, 2, SCALAR))
    assert not contract_contains(exact_two, INCOMPATIBLE)
    assert not contract_contains(UNKNOWN, exact_two)
    assert not contract_contains(UNKNOWN, INCOMPATIBLE)


def test_unknown_sequence_carrier_is_not_a_concrete_flow_fact():
    unknown_carrier = ValueContract.sequence(None, 1, 1, SCALAR)
    as_list = ValueContract.sequence("list", 1, 1, SCALAR)
    as_tuple = ValueContract.sequence("tuple", 1, 1, SCALAR)

    joined = join_contracts(as_list, unknown_carrier)
    assert joined.carrier is None
    assert joined.observed_carriers == frozenset({"list"})
    assert joined.unproved_carrier
    assert join_contracts(as_list, as_tuple) is INCOMPATIBLE
    assert contract_contains(unknown_carrier, as_list)
    assert not contract_contains(as_list, unknown_carrier)


def test_concatenation_retains_unproved_carrier_evidence():
    unknown_carrier = ValueContract.sequence(None, 1, 1, SCALAR)
    as_list = ValueContract.sequence("list", 1, 1, SCALAR)
    expression = ast.parse("left + right", mode="eval").body

    result = contract_of_expression(
        expression,
        resolve=lambda node, _env: {
            "left": unknown_carrier,
            "right": as_list,
        }[node.id],
    )

    assert result.carrier is None
    assert result.observed_carriers == frozenset({"list"})
    assert result.unproved_carrier
    assert result.minimum == result.maximum == 2


def test_helper_return_arms_cannot_change_sequence_carrier():
    source = """\
class M:
    def reset(self):
        pass
    def make(self, flag):
        if flag:
            return [1]
        return (1,)
    def process(self, a):
        return {"y": int(self.make(a) == [1])}
"""

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[3])
    assert caught.value.code in {
        "helper-return-shape-disagrees",
        "source-value-contract-disagrees",
    }


def test_loop_carried_name_cannot_change_ir_representation():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        x = a
        for _ in range(2):
            x = [x]
        return {"y": x[0][0]}
"""

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[3])
    assert caught.value.code == "sequence-element-shape-disagrees"


def test_source_ir_reconciliation_checks_a_dynamic_sequences_storage_shape():
    source = """\
class M:
    def reset(self):
        pass
    def values(self, a):
        result = []
        if a:
            result.append(7)
        return result
    def process(self, a):
        result = self.values(a)
        return {"y": int(bool(result))}
"""

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=["y"])
    assert [interp.run_step(proc, {"a": value})[1].transactions[0]["y"]
            for value in (0, 1)] == [0, 1]

    class ScalarHelperMutation(_Lowerer):
        def _inline(self, node, fn, *, result_kind=None):
            if fn.name == "values":
                return ir.Lit(0)
            return super()._inline(node, fn, result_kind=result_kind)

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[2], outputs=["y"],
            lowerer=ScalarHelperMutation)
    assert caught.value.code == "source-value-contract-disagrees"


def test_recursive_fixed_sequence_equality_matches_python():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        left = [[a, 2], [3, 4]]
        same = [[a, 2], [3, 4]]
        different = [[a, 2], [3, 5]]
        return {"y": int(left == same) * 2 + int(left != different)}
"""

    assert _run_one(source, 6) == 3


def test_fixed_sequence_equality_preserves_python_carriers():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        left = [[a]]
        right = ((a,),)
        return {"y": int(left == right)}
"""

    assert _run_one(source, 6) == 0


def test_dynamic_sequence_equality_is_refused_until_lengths_are_encoded():
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        left = []
        right = []
        if a:
            left.append(a)
            right.append(a)
        return {"y": int(left == right)}
"""

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[3])
    assert caught.value.code == "dynamic-sequence-comparison-unsupported"


def test_self_constant_range_and_dynamic_range_bind_scalar_indices():
    fixed_source = """\
def ident(value):
    return value

class M:
    def __init__(self):
        self.n = 2
        self.reset()
    def reset(self):
        pass
    def process(self, a):
        y = 0
        for i in range(self.n):
            y = ident(i + a)
        return {"y": y}
"""
    dynamic_source = """\
def ident(value):
    return value

class M:
    def reset(self):
        pass
    def process(self, a):
        y = 0
        for i in range(a):
            y = ident(i + 1)
        return {"y": y}
"""

    fixed = lower_with_state_ranges(
        fixed_source, input_widths=[2], outputs=["y"])
    dynamic = lower_with_state_ranges(
        dynamic_source, input_widths=[2], outputs=["y"])
    assert [interp.run_step(fixed, {"a": value})[1].transactions[0]["y"]
            for value in range(4)] == [1, 2, 3, 4]
    assert [interp.run_step(dynamic, {"a": value})[1].transactions[0]["y"]
            for value in range(4)] == [0, 1, 2, 3]


@pytest.mark.parametrize("table", [
    "T = (3, 5)",
    "T = {0: 3, 1: 5}",
])
def test_readonly_module_collection_lookup_has_a_value_contract(table):
    source = f"""\
{table}

def lookup(index):
    if index < 0:
        return 0
    return T[index]

class M:
    def reset(self):
        pass
    def process(self, a):
        return {{"y": lookup(a & 1)}}
"""

    proc = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y"])
    assert [interp.run_step(proc, {"a": value})[1].transactions[0]["y"]
            for value in range(4)] == [3, 5, 3, 5]


def test_syntax_only_array_declaration_supplies_its_recursive_contract():
    source = """\
class M:
    def __init__(self, seed=3):
        self.seed = seed
        self.reset()
    def reset(self):
        self.buf = [self.seed for _ in range(4)]
    def process(self, a):
        return {"y": len(self.buf) + (a & 0)}
"""

    proc = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y"])
    assert [interp.run_step(proc, {"a": value})[1].transactions[0]["y"]
            for value in range(4)] == [4, 4, 4, 4]


def test_heterogeneous_readonly_table_does_not_invent_one_value_contract():
    source = """\
T = {0: 3, 1: (5,)}

def lookup(index):
    return T[index]

class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": int(bool(lookup(a & 1)))}
"""

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=["y"])
    assert caught.value.code in {
        "helper-return-shape-disagrees",
        "source-value-contract-disagrees",
        "table-value-shape-unsupported",
    }
