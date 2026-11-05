"""Adversarial tests for lowering-only references to finite module ROMs."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from behave.sim.behavior import flow_facts
from behave.sim.symbolic import interp, ir
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.catalogue import frontend_catalogue
from behave.sim.behavior.instance import BehaviorInstance
from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges
from behave.sim.behavior.lower_static_refs import (
    _StaticRef, _TupleKey, choose_payload, key_equal, lookup, value_key,
)
from behave.sim.behavior.source_rom import evaluate_frozen
from behave.sim.core.catalogue import SAFETY


@pytest.mark.parametrize('left,right', [
    (True, 1), (False, 0), ('x', b'x'), ((True,), (1,)),
    ({'a': 1, 'b': 2}, {'b': 2, 'a': 1}),
    ({True: (1,)}, {1: (1,)}), (None, 'None'),
])
def test_reference_identity_keeps_source_types_and_iteration_order(left, right):
    assert len({value_key(left), value_key(right)}) == 2
    low = SimpleNamespace(_static_tag_ids={})
    bound = _StaticRef.bound(low, 'tag', [left, right, left], None)
    assert len(bound.alternatives) == 2
    for index, (_, value) in enumerate(bound.alternatives, 1):
        assert value_key(value) == value_key((left, right)[index - 1])
        active = [payload for guard, payload in bound.alternatives
                  if interp._Interp({'tag': index}).expr(guard)]
        assert len(active) == 1 and value_key(active[0]) == value_key(value)


@pytest.mark.parametrize('value', [[], {1}, 1.25])
def test_reference_tag_refusal_names_the_unsupported_source_value(value):
    node = ast.parse('items').body[0].value
    low = SimpleNamespace(_static_tag_ids={})
    with pytest.raises(LoweringError) as caught:
        _StaticRef.bound(low, 'tag', [value], node)
    assert caught.value.code == 'static-collection-depth-unsupported'
    assert type(value).__name__ in str(caught.value)
    assert low._static_tag_ids == {}


@pytest.mark.parametrize('values', [(0, 1, 2), (False, True, False), ((1, 2), (3, 4), (5, 6))])
def test_literal_payload_selection_keeps_first_true_priority_and_zero_fallback(values):
    costs = []
    low = SimpleNamespace(_spend=lambda count=1: costs.append(count))
    alternatives = tuple((ir.Var(f'g{i}'), value) for i, value in enumerate(values))
    expression = choose_payload(low, alternatives, None)
    width = len(values[0]) if isinstance(values[0], tuple) else 1
    assert costs == [len(values) * width]
    for mask in range(8):
        guards = {f'g{i}': bool(mask & (1 << i)) for i in range(3)}
        fallback = [0] * width if isinstance(values[0], tuple) else type(values[0])(0)
        expected = next((list(v) if isinstance(v, tuple) else v
                         for i, v in enumerate(values) if guards[f'g{i}']), fallback)
        actual = interp._Interp(guards).value(expression)
        assert actual == expected and type(actual) is type(expected)


def test_reference_assignment_owns_tag_binding_and_discards_stale_numeric_facts():
    events = []
    def written(target):
        events.append(('write', target.id))
        return 'local'
    def flush(command):
        assert low.flow.static_values['local'].tag == 'local'
        events.append(('flush', command.var))
        return command
    low = SimpleNamespace(
        _static_tag_ids={}, flow=flow_facts.FlowState(
            static_values={'other': 'keep'},
            lengths={'local': 2, 'other': 3}, carriers={'local': 'tuple', 'other': 'list'},
            contracts={'local': 'stale', 'other': 'keep'},
            ranges={'local': 'stale', 'other': 'keep'}, bounds={'local': 'stale', 'other': 'keep'},
            unknown_ranges={'local', 'other'}), _written=written, _flush=flush,
        _spend=lambda count=1: events.append(('budget', count)))
    ref = _StaticRef(((ir.Var('pick'), ('left',)), (ir.Lit(True), ('right',))))
    command = ref.assign(low, ast.parse('target').body[0].value, None)
    assert events == [('write', 'target'), ('budget', 2), ('flush', 'local')]
    assert low.flow.static_values['other'] == 'keep'
    for name in ('lengths', 'carriers', 'contracts', 'ranges', 'bounds'):
        assert 'local' not in getattr(low.flow, name) and 'other' in getattr(low.flow, name)
    assert low.flow.unknown_ranges == {'other'}
    for pick in (False, True):
        interpreter = interp._Interp({'pick': pick})
        interpreter.run(command, set())
        selected = [value for guard, value in low.flow.static_values['local'].alternatives
                    if interpreter.expr(guard)]
        assert selected == [('left',) if pick else ('right',)]
    assert ref.tag is None  # Source carrier is immutable; only the context was installed.


def test_empty_reference_binding_stays_unreachable_total_without_new_tags():
    low = SimpleNamespace(_static_tag_ids={})
    command, bound = _StaticRef(()).bind(low, 'dead', None)
    assert command.var == 'dead' and isinstance(command.expr, ir.Lit)
    assert command.expr.value == 0
    assert bound == _StaticRef((), 'dead') and low._static_tag_ids == {}


def test_reference_binding_keeps_the_last_tag_as_its_total_fallback():
    costs = []
    low = SimpleNamespace(_static_tag_ids={}, _spend=costs.append)
    values = (('first',), ('second',), ('last',))
    ref = _StaticRef(tuple((ir.Var(f'g{i}'), value)
                          for i, value in enumerate(values)))
    command, bound = ref.bind(low, 'tag', None)
    assert costs == [3] and ref.tag is None
    for mask in range(8):
        guards = {f'g{i}': bool(mask & (1 << i)) for i in range(3)}
        expected = next((value for i, value in enumerate(values) if guards[f'g{i}']),
                        values[-1])
        interpreter = interp._Interp(guards)
        interpreter.run(command, set())
        assert [value for guard, value in bound.alternatives
                if interpreter.expr(guard)] == [expected]


@pytest.mark.parametrize('literal,expected', [
    ((True, 'ready'), True), ((1, 'ready'), True),
    ((False, 'ready'), False), ((1, 'busy'), False),
    ((1,), False), ('ready', False),
])
def test_finite_key_equality_needs_no_lowerer_context(literal, expected):
    key = _TupleKey((ir.Lit(1), _StaticRef(((ir.Lit(True), 'ready'),))))
    assert interp._Interp({}).expr(key_equal(key, literal, None)) is expected


def test_table_default_shape_refusal_keeps_the_source_diagnostic():
    node = ast.parse('TABLE.get(key, (1, 2))').body[0].value
    default = ir.Op('arraylit', ir.Lit(1), ir.Lit(2))
    low = SimpleNamespace(tables={'TABLE': {0: 7, 1: 9}},
                          _shape=lambda value: (2,) if value is default else ())
    with pytest.raises(LoweringError) as caught:
        lookup(low, 'TABLE', ir.Var('key'), node, default)
    assert caught.value.code == 'table-default-shape-mismatch'
    assert 'has shape (2,), but its values have shape ()' in str(caught.value)


@pytest.mark.parametrize('first,last', [
    ((2, 3), (9,)), ('ab', 'c'), (b'xy', b'z'), ({4: 1, 2: 3}, {9: 0}),
])
def test_reference_iteration_and_length_preserve_sequence_order_and_type(first, last):
    costs = []
    low = SimpleNamespace(
        _spend=costs.append, _sequence_table_expr=lambda value: ir.Lit(value),
        _shape=lambda value: ())
    ref = _StaticRef(((ir.Var('first'), first), (ir.Lit(True), last)))
    select, count, span = ref.iterable(low, None)
    length = ref.length(low, None)
    assert span == (1, 2) and costs
    for pick, expected in ((True, list(first)), (False, list(last))):
        interpreter = interp._Interp({'first': pick})
        assert interpreter.expr(count) == interpreter.expr(length) == len(expected)
        actual = []
        for index in range(len(expected)):
            item = select(ir.Lit(index))
            if isinstance(item, _StaticRef):
                item = next(value for active, value in item.alternatives
                            if interpreter.expr(active))
            else:
                item = interpreter.expr(item)
            actual.append(item)
        assert actual == expected
        assert list(map(type, actual)) == list(map(type, expected))


def _native(source, a):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    try:
        result = model.process(a)
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    if result is None:
        return [], ""
    if isinstance(result, Mapping):
        return [dict(result)], ""
    return [dict(row) for row in result], ""


def _lowered(source, a, *, width=3):
    proc = lower_with_state_ranges(
        source, input_widths=[width], outputs=["y"])
    _state, output, _hits, error = interp.run_step(proc, {"a": a})
    return (None if error else [dict(row) for row in output.transactions],
            error)


def _assert_agrees(source, cases, *, width=3):
    for value in cases:
        expected = _native(source, value)
        assert _lowered(source, value, width=width) == expected


def _assert_refused(source, code):
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[3], outputs=["y"])
    assert caught.value.code == code, (caught.value.code, str(caught.value))


def _bind(proc, source):
    """Cross the exact-representation boundary after semantic lowering."""
    return BehaviorInstance(
        proc, proc.state_ranges, source=source).bind(1)


def test_a_repeated_source_rom_can_be_selected_through_a_local_reference():
    source = """\
ROW = (2, 3)
ROM = (ROW,) * 2
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        return {"y": 100 * ref[a & 1][0] + ref[a & 1][1]}
"""

    _assert_agrees(source, [0, 1])


def test_a_direct_alias_can_feed_repeated_rom_reads():
    source = """\
ROM = ((1, 2), (3, 4))
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        row = ref[a & 1]
        return {"y": row[0] + 10 * row[1] + ref[0][0]}
"""

    _assert_agrees(source, [0, 1])


def test_if_expression_joins_compatible_static_references():
    source = """\
LEFT = (1, 2)
RIGHT = (3, 4)
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = LEFT if (a & 1) else RIGHT
        return {"y": ref[(a >> 1) & 1]}
"""

    _assert_agrees(source, [0, 1, 2, 3])


def test_if_expression_joins_values_selected_from_static_collections():
    source = """\
LEVEL = {0: 0, 1: 10, 2: 20}
NEXT = {0: {0: 2, 1: 4}, 1: {0: 6, 1: 8}}
class M:
    def reset(self):
        pass
    def process(self, a):
        signed = (a & 3) - 2
        magnitude = -LEVEL[-signed] if signed < 0 else LEVEL[signed]
        transition = NEXT[(a >> 2) & 1][a & 1] if a < 6 else 9
        return {"y": magnitude + transition}
"""

    _assert_agrees(source, range(8))


def test_ordinary_if_uses_the_same_static_reference_join():
    source = """\
LEFT = (1, 2)
RIGHT = (3, 4)
class M:
    def reset(self):
        pass
    def process(self, a):
        if a & 1:
            ref = LEFT
        else:
            ref = RIGHT
        return {"y": ref[(a >> 1) & 1]}
"""

    _assert_agrees(source, [0, 1, 2, 3])


def test_a_selected_static_value_keeps_its_contract_inside_an_operator():
    source = """\
LEFT = {0: 1, 1: 2}
RIGHT = {0: 4, 1: 8}
class M:
    def reset(self):
        pass
    def process(self, a):
        table = LEFT if (a & 1) else RIGHT
        total = 0
        for i in range(2):
            total |= table[i] << i
        return {"y": total}
"""

    _assert_agrees(source, [0, 1])


def test_a_static_reference_is_carried_by_a_finite_tag_across_a_loop():
    source = """\
LEFT = (1, 2)
RIGHT = (3, 4)
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = LEFT
        for i in range(2):
            if i == (a & 1):
                ref = RIGHT
        return {"y": ref[(a >> 1) & 1]}
"""

    _assert_agrees(source, [0, 1, 2, 3])


def test_each_loop_iteration_may_select_a_different_nested_rom_row():
    source = """\
ROM = ((1, 2), (3, 4))
class M:
    def reset(self):
        pass
    def process(self, a):
        y = 0
        for i in range(2):
            row = ROM[i]
            y += row[(a >> i) & 1]
        return {"y": y}
"""

    _assert_agrees(source, range(8), width=4)


def test_loop_selected_mapping_rows_keep_their_literal_field_schema():
    source = """\
ROM = ({"bias": 1}, {"bias": 3})
class M:
    def reset(self):
        pass
    def process(self, a):
        y = a
        for i in range(2):
            row = ROM[i]
            y += row["bias"]
        return {"y": y}
"""

    _assert_agrees(source, range(4), width=4)


def test_a_single_arm_static_reference_definition_is_not_initialized():
    source = """\
ROM = (1, 2)
class M:
    def reset(self):
        pass
    def process(self, a):
        if a & 1:
            ref = ROM
        return {"y": ref[0]}
"""

    assert _native(source, 1) == ([{"y": 1}], "")
    assert _native(source, 0) == (None, "UnboundLocalError")
    _assert_refused(source, "variable-may-be-unassigned")


def test_nested_mapping_refs_preserve_literal_string_key_selection():
    source = """\
ROM = {
    "left": {"row": (1, 2)},
    "right": {"row": (3, 4)},
}
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM["left"] if (a & 1) else ROM["right"]
        row = ref["row"]
        return {"y": row[(a >> 1) & 1]}
"""

    _assert_agrees(source, [0, 1, 2, 3])


@pytest.mark.parametrize("body", [
    'ROM["missing"]["x"]',
    'ROM["missing"][1 // 0]',
    'ref["missing"]',
    'ref["missing"]["x"]',
    'ref["missing"][1 // 0]',
])
def test_a_fixed_missing_static_path_has_one_exact_failure(body):
    setup = "ref = ROM\n        " if body.startswith("ref") else ""
    source = f"""\
ROM = {{"present": {{"x": 1}}}}
class M:
    def reset(self):
        pass
    def process(self, a):
        {setup}return {{"y": {body}}}
"""

    assert _native(source, 0) == (None, "KeyError")
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"])
    _state, output, hits, error = interp.run_step(proc, {"a": 0})
    assert error == "KeyError"
    assert output.count == 0
    catalogue = frontend_catalogue(proc, source)
    failures = {goal for goal in hits if catalogue[goal].cls == SAFETY}
    assert len(failures) == 1
    assert next(iter(failures)).endswith(":indexerror")


def test_dynamic_negative_and_out_of_range_indices_keep_exact_errors():
    source = """\
ROM = (10, 20, 30)
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        return {"y": ref[a - 2]}
"""

    _assert_agrees(source, [0, 1, 2, 3, 4, 5, 7])


def test_dynamic_mapping_keys_keep_exact_key_errors():
    source = """\
ROM = {-1: 10, 2: 20}
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        return {"y": ref[a - 2]}
"""

    _assert_agrees(source, [0, 1, 2, 4, 7])


def test_boolean_and_integer_mapping_keys_follow_python_equality():
    source = """\
ROM = {True: 7}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": ROM[a]}
"""

    _assert_agrees(source, [0, 1, 2])


def test_duplicate_mapping_keys_keep_the_last_value():
    source = """\
ROM = {1: 3, 1: 7}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": ROM[a]}
"""

    _assert_agrees(source, [0, 1, 2])


def test_a_static_reference_key_is_evaluated_exactly_once():
    source = """\
ROM = (10, 20)
class M:
    def reset(self):
        self.calls = 0
    def key(self):
        result = self.calls
        self.calls += 1
        return result
    def process(self, a):
        ref = ROM
        value = ref[self.key()]
        return {"y": 100 * self.calls + value}
"""

    assert _native(source, 0) == ([{"y": 110}], "")
    _assert_agrees(source, [0])


def test_a_static_reference_may_die_before_a_scalar_rebinding():
    source = """\
ROM = (10, 20)
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        selected = ref[a & 1]
        ref = 7
        return {"y": selected + ref}
"""

    _assert_agrees(source, [0, 1])


def test_mutating_a_static_reference_is_a_typed_boundary():
    source = """\
ROM = [1, 2]
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        ref[0] = a
        return {"y": ref[0]}
"""

    assert _native(source, 7) == ([{"y": 7}], "")
    _assert_refused(source, "static-collection-mutated")


def test_observing_static_reference_identity_is_a_typed_boundary():
    source = """\
ROM = (1, 2)
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        return {"y": int(ref is ROM) + (a & 0)}
"""

    assert _native(source, 0) == ([{"y": 1}], "")
    _assert_refused(source, "static-collection-identity-observed")


def test_passing_a_static_reference_to_an_opaque_call_is_an_escape():
    source = """\
ROM = (1, 2)
def decorate(fn):
    return fn
@decorate
def consume(value):
    return value[0]
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        return {"y": consume(ref) + (a & 0)}
"""

    assert _native(source, 0) == ([{"y": 1}], "")
    _assert_refused(source, "call-unlowered")


def test_a_static_reference_may_cross_an_inlined_helper_parameter():
    source = """\
ROM = (1, 2)
def first(value):
    return value[0]
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        return {"y": first(ref) + (a & 0)}
"""

    _assert_agrees(source, [0, 1])


def test_an_inlined_helper_still_cannot_mutate_a_static_reference():
    source = """\
ROM = [1, 2]
def overwrite(value, replacement):
    value[0] = replacement
    return value[0]
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": overwrite(ROM, a)}
"""

    assert _native(source, 7) == ([{"y": 7}], "")
    _assert_refused(source, "static-collection-mutated")


def test_an_inlined_helper_may_iterate_a_static_reference():
    source = """\
ROM = (3, 5, 7)
def weighted(value, table):
    result = 0
    for item in table:
        result = result * 10 + item
    return result + value
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": weighted(a, ROM)}
"""

    _assert_agrees(source, [0, 1, 7])


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("body,code", [
    ("self.saved = ref\n        return {'y': a & 0}",
     "state-value-contract-disagrees"),
    ("return {'y': ref}", None),
])
def test_closed_tuple_state_and_output_follow_the_value_contract(body, code):
    source = f"""\
ROM = (1, 2)
class M:
    def reset(self):
        self.saved = 0
    def process(self, a):
        ref = ROM
        {body}
"""

    native, error = _native(source, 0)
    assert error == ""
    assert native in ([{"y": 0}], [{"y": (1, 2)}])
    if code is not None:
        # Changing integer state to a tuple still violates its state contract.
        _assert_refused(source, code)
        return
    # A proved numeric tuple now uses the ordinary output value projection.
    # Native tuples and IR arrays are carriers of the same ordered public
    # sequence; scalar/Boolean distinctions are still checked by the audit.
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=["y"])
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    assert model_audit(proc, namespace["M"], [[{"a": a}] for a in range(8)]) == []
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    instance.bind(1)
    query = instance.query(instance.goals(), 1)
    assert query.reachable and not query.undecided
    assert instance.source_audit(tuple(query.reachable.values())).status.value == "agreed"


def test_a_static_reference_cannot_cross_a_helper_return():
    source = """\
ROM = (1, 2)
def choose():
    return ROM
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = choose()
        return {"y": ref[a & 1]}
"""

    assert _native(source, 0) == ([{"y": 1}], "")
    _assert_refused(source, "static-collection-escapes")


def test_a_static_reference_and_a_scalar_do_not_share_a_branch_join():
    source = """\
ROM = (1, 2)
class M:
    def reset(self):
        pass
    def process(self, a):
        if a & 1:
            ref = ROM
        else:
            ref = 7
        return {"y": ref[0]}
"""

    assert _native(source, 1) == ([{"y": 1}], "")
    assert _native(source, 0) == (None, "TypeError")
    _assert_refused(source, "static-collection-join-disagrees")


@pytest.mark.parametrize("read", [
    "ROM[a]",
    "ref[a]",
    "ROM.get(a, 0)",
])
def test_numeric_lookup_of_string_keyed_rom_preserves_missing_key(read):
    setup = "ref = ROM\n        " if read.startswith("ref") else ""
    source = f"""\
ROM = {{"left": 1, "right": 2}}
class M:
    def reset(self):
        pass
    def process(self, a):
        {setup}return {{"y": {read}}}
"""

    _assert_agrees(source, range(8))


def test_a_noninteger_sequence_index_is_a_typed_boundary():
    source = """\
ROM = (1, 2)
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": ROM["left"] + (a & 0)}
"""

    assert _native(source, 0) == (None, "TypeError")
    _assert_refused(source, "static-collection-key-unsupported")


@pytest.mark.parametrize("key", ["1.5", "[a]"])
def test_other_noninteger_static_collection_keys_are_typed_boundaries(key):
    source = f"""\
ROM = {{0: 1, 1: 2}}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {{"y": ROM[{key}]}}
"""

    _assert_refused(source, "static-collection-key-unsupported")


def test_tuple_key_cannot_match_a_numeric_key():
    source = '''ROM = {0: 1, 1: 2}
class M:
    def reset(self): pass
    def process(self, a): return {"y": ROM[(a,)]}
'''
    assert _native(source, 0) == (None, "KeyError")
    _assert_agrees(source, range(8))


def test_a_selected_scalar_may_cross_an_inlined_helper_boundary():
    source = """\
ROM = ({"coefficient": 3}, {"coefficient": 7})
def bias(value):
    return value + 1
class M:
    def reset(self):
        pass
    def process(self, a):
        row = ROM[a & 1]
        return {"y": bias(row["coefficient"])}
"""

    _assert_agrees(source, [0, 1])


def test_a_directly_selected_collection_may_enter_an_inlined_helper():
    source = """\
ROM = ((1, 2), (3, 4))
def first(row):
    return row[0]
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": first(ROM[a & 1])}
"""

    _assert_agrees(source, [0, 1])


def test_a_selected_collection_local_may_enter_an_inlined_helper():
    source = """\
ROM = ((1, 2), (3, 4))
def first(row):
    return row[0]
class M:
    def reset(self):
        pass
    def process(self, a):
        row = ROM[a & 1]
        return {"y": first(row)}
"""

    _assert_agrees(source, [0, 1])


@pytest.mark.parametrize(("expression", "expected"), [
    ("6 + 3", 9),
    ("6 - 3", 3),
    ("6 * 3", 18),
    ("7 // 3", 2),
    ("7 % 3", 1),
    ("3 << 2", 12),
    ("12 >> 2", 3),
    ("6 & 3", 2),
    ("6 | 3", 7),
    ("6 ^ 3", 5),
    ("~6", -7),
    ("-7 // 3", -3),
    ("-7 % 3", 2),
    ("-8 >> 2", -2),
])
def test_finite_rom_integer_arithmetic_is_closed(expression, expected):
    node = ast.parse(expression, mode="eval").body
    value = evaluate_frozen(node, lambda _node: None)
    assert value is not None
    assert value.kind == "int"
    assert value.payload == expected


@pytest.mark.parametrize("expression", [
    "1 // 0",
    "1 % 0",
    "1 << -1",
])
def test_finite_rom_integer_arithmetic_refuses_unsafe_operations(expression):
    node = ast.parse(expression, mode="eval").body
    assert evaluate_frozen(node, lambda _node: None) is None


def test_finite_rom_shift_uses_the_shared_scalar_budget_or_explicit_override():
    from behave.sim.behavior.budgets import SCALAR_BITS

    node = ast.parse("1 << 513", mode="eval").body
    assert evaluate_frozen(node, lambda _node: None, max_int_bits=514).payload == 1 << 513
    assert evaluate_frozen(node, lambda _node: None, max_shift=512) is None
    outside = ast.parse(f"1 << {SCALAR_BITS}", mode="eval").body
    assert evaluate_frozen(outside, lambda _node: None) is None


def test_finite_rom_integer_literals_and_results_share_one_size_bound():
    literal = ast.parse("128", mode="eval").body
    expression = ast.parse("127 + 1", mode="eval").body
    assert evaluate_frozen(
        literal, lambda _node: None, max_int_bits=7) is None
    assert evaluate_frozen(
        expression, lambda _node: None, max_int_bits=7) is None


def test_read_only_rom_can_be_built_by_a_closed_scalar_helper():
    source = """\
def signed(v, bits):
    v &= (1 << bits) - 1
    if v & (1 << (bits - 1)):
        v -= 1 << bits
    return v
ROM = [signed(0x2, 4), signed(0xE, 4)]
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": ROM[a & 1]}
"""

    _assert_agrees(source, [0, 1])


def test_rom_function_does_not_reevaluate_a_nonliteral_default():
    tree = ast.parse("""
K = 1
def pick(value=K):
    return value
ROM = [pick()]
""")
    fn = tree.body[1]
    call = tree.body[2].value.elts[0]

    assert evaluate_frozen(
        call,
        lambda node: 2 if isinstance(node, ast.Name)
        and node.id == "K" else None,
        function_of=lambda _call: fn,
    ) is None


@pytest.mark.parametrize(("key", "expected"), [
    ("left", 203),
    ("missing", 209),
])
def test_mapping_get_evaluates_its_default_even_for_a_fixed_string_key(
        key, expected):
    source = f"""\
ROM = {{"left": 3}}
class M:
    def reset(self):
        self.calls = 0
    def fallback(self):
        self.calls = self.calls * 10 + 2
        return 9
    def process(self, a):
        value = ROM.get("{key}", self.fallback())
        return {{"y": 100 * self.calls + value + (a & 0)}}
"""

    assert _native(source, 0) == ([{"y": expected}], "")
    _assert_agrees(source, [0])


def test_mapping_get_evaluates_key_before_default_and_only_once():
    source = """\
ROM = {0: 3}
class M:
    def reset(self):
        self.calls = 0
    def key(self):
        self.calls = self.calls * 10 + 1
        return 0
    def fallback(self):
        self.calls = self.calls * 10 + 2
        return 9
    def process(self, a):
        value = ROM.get(self.key(), self.fallback())
        return {"y": 100 * self.calls + value + (a & 0)}
"""

    assert _native(source, 0) == ([{"y": 1203}], "")
    _assert_agrees(source, [0])


def test_integer_arithmetic_can_size_a_repeated_rom():
    source = """\
ROM = [5] * (8 * 3 + 57)
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": ROM[a & 7]}
"""

    _assert_agrees(source, range(8))


@pytest.mark.solver
def test_static_reference_expansion_is_in_the_aggregate_representation_budget(
        monkeypatch):
    from behave.sim.symbolic import capabilities
    from behave.sim.symbolic.encoder import EncodingError

    def source(size):
        return f"""\
ROW = (1, 2)
ROM = (ROW,) * {size}
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = ROM
        return {{"y": ref[a][0]}}
"""
    baseline = lower_with_state_ranges(
        source(8), input_widths=[3], outputs=["y"])
    exact_small_cost = ir.procedure_representation_cost(baseline)
    monkeypatch.setattr(capabilities, "TERM_BUDGET", exact_small_cost)

    small_source = source(8)
    small = lower_with_state_ranges(
        small_source, input_widths=[3], outputs=["y"])
    _bind(small, small_source)

    large_source = source(64)
    large = lower_with_state_ranges(
        large_source, input_widths=[3], outputs=["y"])
    with pytest.raises(EncodingError, match="representation-budget-exceeded") as caught:
        _bind(large, large_source)
    assert caught.value.kind == "capability"


def test_sparse_mapping_cost_depends_on_entries_not_numeric_span():
    def source(last):
        return f"""\
ROM = {{0: 1, {last}: 2}}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {{"y": ROM.get(a, 0)}}
"""
    far = 1 << 30
    adjacent = lower_with_state_ranges(
        source(1), input_widths=[31], outputs=["y"])
    sparse = lower_with_state_ranges(
        source(far), input_widths=[31], outputs=["y"])

    assert (ir.procedure_representation_cost(sparse)
            <= ir.procedure_representation_cost(adjacent) + 16)
    _assert_agrees(source(far), [0, 1, far - 1, far], width=31)


@pytest.mark.solver
def test_a_statically_unreachable_rom_alternative_consumes_no_term_budget(
        monkeypatch):
    from behave.sim.symbolic import capabilities
    from behave.sim.symbolic.encoder import EncodingError

    def source(test):
        return f"""\
ROW = {{"value": 3}}
SMALL = (ROW,) * 2
LARGE = (ROW,) * 64
class M:
    def reset(self):
        pass
    def process(self, a):
        ref = LARGE if {test} else SMALL
        return {{"y": ref[a & 1]["value"]}}
"""
    baseline = lower_with_state_ranges(
        source("False"), input_widths=[3], outputs=["y"])
    exact_small_cost = ir.procedure_representation_cost(baseline)
    monkeypatch.setattr(capabilities, "TERM_BUDGET", exact_small_cost)

    small_source = source("False")
    _assert_agrees(small_source, [0, 1])
    small = lower_with_state_ranges(
        small_source, input_widths=[3], outputs=["y"])
    _bind(small, small_source)

    dynamic_source = source("a & 1")
    dynamic = lower_with_state_ranges(
        dynamic_source, input_widths=[3], outputs=["y"])
    with pytest.raises(EncodingError, match="representation-budget-exceeded") as caught:
        _bind(dynamic, dynamic_source)
    assert caught.value.kind == "capability"
