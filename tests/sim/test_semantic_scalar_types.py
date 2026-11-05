"""Boolean/integer separation at the shared SolverIR semantic boundary."""

import pickle

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import encoder, interp, ir, plan, primitives, ranges, typecheck as types
from behave.sim.behavior.lowering import (
    LoweringError,
    _fixed_expression_type,
    _Lowerer,
    lower_expression,
    lower_with_state_ranges,
)
from behave.sim.behavior.signature import (
    ProcedureContract,
    ProcedureContractError,
    ProcedureSignature,
    bind_procedure_contract,
)
from behave.sim.symbolic.value_layout import BooleanLayout, IntegerLayout
from behave.sim.core.contract import FiniteType


def _guarded(guard):
    return ir.Procedure(
        (), (), (), (),
        ir.If(guard, ir.Mark("true"), ir.Mark("false")))


@pytest.mark.parametrize("shape", [(), (0,), (2,), (2, 0, 3), (0, 2, 3),
                                  (2, 3), (2, 3, 4)])
@pytest.mark.parametrize("leaf", [types.BOOLEAN, types.INTEGER])
def test_declared_shape_projection_preserves_empty_and_nested_dimensions(shape, leaf):
    kind = types.of_shape(shape, leaf)
    assert types.shape_of(kind) == shape
    assert types.of_shape(types.shape_of(kind), leaf) == kind
    assert pickle.loads(pickle.dumps(kind)) == kind


@pytest.mark.parametrize("leaf", [types.BOOLEAN, types.INTEGER])
def test_declared_shape_projection_is_iterative(leaf):
    shape = (2,) * 3_000
    kind = types.of_shape(shape, leaf)
    assert types.shape_of(kind) == shape
    current = kind
    for _ in shape:
        current = current.elem
    assert current is leaf


def test_physical_shape_does_not_replace_semantic_type_identity():
    boolean = types.of_shape((2, 3), types.BOOLEAN)
    integer = types.of_shape((2, 3), types.INTEGER)
    assert types.shape_of(boolean) == types.shape_of(integer) == (2, 3)
    assert boolean != integer


@pytest.mark.parametrize("operation", ["assign", "delete", "reinitialize"])
@pytest.mark.parametrize("field", ["cells", "elem"])
def test_shared_array_type_cannot_be_changed_after_construction(operation, field):
    kind = types.Array(2, types.Array(3, types.BOOLEAN))
    identity = hash(kind)
    with pytest.raises(AttributeError, match="immutable"):
        if operation == "assign":
            setattr(kind, field, 7)
        elif operation == "delete":
            delattr(kind, field)
        else:
            kind.__init__(7, types.INTEGER)
    assert hash(kind) == identity
    assert types.shape_of(kind) == (2, 3)
    assert pickle.loads(pickle.dumps(kind)) == kind


def test_true_is_not_the_integer_one_in_ir_or_type_inference():
    boolean = ir.Lit(True)
    integer = ir.Lit(1)

    assert boolean.value is True
    assert type(integer.value) is int
    assert types.BOOLEAN != types.INTEGER

    faults = []
    assert types._of_expression(boolean, {}, faults, "test") == types.BOOLEAN
    assert types._of_expression(integer, {}, faults, "test") == types.INTEGER
    assert faults == []

    mixed = ir.Procedure(
        (), (), ("x",), (),
        ir.Seq(ir.Assign("x", boolean), ir.Assign("x", integer)))
    assert any("x is an integer" in fault and "a Boolean" in fault
               for fault in types.type_errors(mixed))

    disguised = ir.Procedure(
        (), (), ("x",), (),
        ir.Assign("x", ir.Lit(True, type=types.INTEGER)))
    assert any("declares an integer but carries a Boolean" in fault
               for fault in types.type_errors(disguised))
    assert not hasattr(ir, "BV")
    assert not hasattr(ir, "Arr")


def test_compiler_scalar_type_discovery_is_iterative_for_deep_ir():
    expression = ir.Lit(0)
    for _ in range(2_000):
        expression = ir.Op("ite", ir.Lit(True), expression, ir.Lit(0))

    assert _fixed_expression_type(expression) == types.INTEGER


def test_chained_comparison_short_circuit_result_is_exactly_boolean():
    result, pending = lower_expression("0 < a < 7")

    assert isinstance(result, ir.Var)
    assert len(pending) == 1
    body = pending[0]
    locals_ = tuple(sorted({
        command.var for command in ir.walk(body)
        if isinstance(command, ir.Assign)}))
    proc = ir.Procedure(
        (), ("a",), locals_, (), body,
        types={"a": types.INTEGER},
    )

    inferred, faults = types.of_procedure(proc)
    assert faults == []
    assert inferred[result.name] == types.BOOLEAN
    assert any(
        isinstance(command, ir.Assign)
        and command.var == result.name
        and isinstance(command.expr, ir.Lit)
        and command.expr.value is False
        for command in ir.walk(body)
    )


def test_registry_distinguishes_numeric_boolean_and_conversion_primitives():
    assert primitives.primitive("add").signature == primitives.TypeSignature(
        (primitives.INTEGER, primitives.INTEGER), primitives.INTEGER)
    assert primitives.primitive("eq").signature == primitives.TypeSignature(
        (primitives.SAME_SCALAR, primitives.SAME_SCALAR),
        primitives.BOOLEAN)
    assert primitives.primitive("ite").signature.arguments[0] \
        is primitives.BOOLEAN
    assert primitives.primitive("truth").signature == primitives.TypeSignature(
        (primitives.SCALAR,), primitives.BOOLEAN)
    assert primitives.primitive("toint").signature == primitives.TypeSignature(
        (primitives.SCALAR,), primitives.INTEGER)
    assert primitives.primitive("land").signature == primitives.TypeSignature(
        (primitives.BOOLEAN, primitives.BOOLEAN), primitives.BOOLEAN)


def test_integer_guard_is_rejected_by_checker_interpreter_and_encoder():
    proc = _guarded(ir.Lit(1))

    assert any("branch" in fault and "integer" in fault
               for fault in ir.normalized(proc))
    with pytest.raises(interp.InterpError, match="Boolean|integer"):
        interp.run_step(proc, {})
    with pytest.raises(encoder.EncodingError) as caught:
        encoder.encode_step(proc, env={})
    assert caught.value.kind == "type"


def test_boolean_guard_agrees_in_interpreter_and_exact_encoding():
    proc = _guarded(ir.Lit(True))

    assert ir.normalized(proc) == []
    assert interp.run_step(proc, {})[2] == {"true"}
    encoded = encoder.encode_step(proc, env={})
    solver = z3.Solver()
    solver.add(encoded.constraint)
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_true(model.eval(encoded.hits["true"],
                                 model_completion=True))

    assert z3.is_false(model.eval(encoded.hits["false"],
                                  model_completion=True))


def test_one_bit_integer_input_is_not_an_implicitly_boolean_guard():
    integer = ir.Procedure(
        (), ("flag",), (), (),
        ir.If(ir.Var("flag"), ir.Mark("true"), ir.Mark("false")),
        types={"flag": types.INTEGER},
    )
    assert any("decided by an integer" in fault
               for fault in types.type_errors(integer))
    with pytest.raises(encoder.EncodingError) as caught:
        encoder.encode_step(integer, env={"flag": ranges.Range(0, 1)})
    assert caught.value.kind == "type"

    boolean = ir.Procedure(
        (), ("flag",), (), (),
        ir.If(ir.Var("flag"), ir.Mark("true"), ir.Mark("false")),
        types={"flag": types.BOOLEAN},
    )
    assert interp.run_step(boolean, {"flag": True})[2] == {"true"}
    with pytest.raises(interp.InterpError, match="declared a Boolean"):
        interp.run_step(boolean, {"flag": 1})

    encoded = encoder.encode_step(
        boolean, env={"flag": ranges.Range(0, 1)})
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.pre["flag"] == 1)
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_true(model.eval(encoded.hits["true"],
                                 model_completion=True))

    # A wider implementation range cannot smuggle integer 2 into the Boolean
    # semantic carrier and have branch encoding interpret it as truthy.
    wide = encoder.encode_step(
        boolean, env={"flag": ranges.Range(0, 3)})
    solver = z3.Solver()
    solver.add(wide.constraint, wide.pre["flag"] == 2)
    assert solver.check() == z3.unsat


def test_recursive_boolean_arrays_keep_exact_leaf_types_in_both_backends():
    kind = types.Array(2, types.Array(2, types.BOOLEAN))
    row = ir.Op(
        "arraylit", ir.Var("flag"), ir.Op("not", ir.Var("flag")))
    proc = ir.Procedure(
        ("flags",), ("flag",), (), (),
        ir.Store("flags", ir.Lit(0), row),
        types={"flags": kind, "flag": types.BOOLEAN},
        initial={"flags": [[False, False], [True, True]]},
    )

    state, _output, _hits, _error = interp.run_step(proc, {"flag": True})
    assert state["flags"] == [[True, False], [True, True]]

    encoded = encoder.encode_step(
        proc,
        env={"flags": ranges.Range(0, 1), "flag": ranges.Range(0, 1)},
    )
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.pre["flag"] == 1)
    assert solver.check() == z3.sat
    model = solver.model()
    said = [model.eval(cell, model_completion=True).as_long()
            for cell in encoded.state_post["flags"][0]]
    assert said == [1, 0]


def test_source_integer_truthiness_is_an_explicit_boolean_conversion():
    source = """
class M:
    def reset(self):
        self.flag = False

    def process(self, a):
        if a:
            self.flag = not self.flag
        return {"y": 1 if self.flag else 0}
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert proc.types["a"] == types.INTEGER
    assert proc.types["flag"] == types.BOOLEAN
    assert types.type_errors(proc) == []
    assert any(isinstance(expr, ir.Op) and expr.op == "truth"
               for expr in ir.expression_nodes(proc.body))

    unchanged = interp.run_step(proc, {"a": 0})
    changed = interp.run_step(proc, {"a": 2})
    assert unchanged[0]["flag"] is False
    assert changed[0]["flag"] is True
    assert changed[1].buffers["y"][0] == 1

    env = ranges.fixpoint(proc, {
        "a": ranges.Range(0, 3),
        "flag": ranges.Range(0, 1),
    })
    assert encoder.verify_encoding(proc, env=env) == []


def test_python_booleans_are_promoted_only_at_numeric_use_sites():
    source = """
class M:
    def reset(self):
        pass

    def process(self, wave, direction):
        at_max = wave == 3
        at_min = wave == 0
        next_direction = ((1 - direction) * at_max
                          + direction * (1 - at_min))
        return {"y": next_direction}
"""
    proc = lower_with_state_ranges(source, input_widths=[2, 1])

    assert types.type_errors(proc) == []
    assert sum(
        isinstance(expr, ir.Op) and expr.op == "toint"
        for expr in ir.expression_nodes(proc.body)
    ) >= 2
    _state, output, _hits, error = interp.run_step(
        proc, {"wave": 3, "direction": 0})
    assert error == ""
    assert output.buffers["y"][0] == 1
    env = ranges.fixpoint(proc, {
        "wave": ranges.Range(0, 3), "direction": ranges.Range(0, 1),
    })
    assert encoder.verify_encoding(proc, env=env) == []


def test_an_inlined_boolean_result_keeps_a_boolean_join_placeholder():
    source = """
def positive(value):
    if value > 0:
        return True
    return False

class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": 7 if positive(a) else 2}
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert types.type_errors(proc) == []
    for row, expected in [({"a": 0}, 2), ({"a": 3}, 7)]:
        _state, output, _hits, error = interp.run_step(proc, row)
        assert error == ""
        assert output.buffers["y"][0] == expected
    assert encoder.verify_encoding(
        proc, env={"a": ranges.Range(0, 3)}) == []


def test_boolean_type_crosses_nested_helper_arguments_and_returns():
    source = """
def identity(value):
    return value

def positive(value):
    return identity(value > 0)

class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": 7 if positive(a) else 2}
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert types.type_errors(proc) == []
    for row, expected in [({"a": 0}, 2), ({"a": 3}, 7)]:
        _state, output, _hits, error = interp.run_step(proc, row)
        assert error == ""
        assert output.buffers["y"][0] == expected
    assert encoder.verify_encoding(
        proc, env={"a": ranges.Range(0, 3)}) == []


def test_directly_unpacked_helper_result_is_an_ordered_typed_product():
    source = """
def integer_key(value):
    if value == 0:
        return True, 0
    if value == 1:
        return False, 7
    return True, value + 3

class M:
    def reset(self):
        pass

    def process(self, a):
        valid, key = integer_key(a)
        return {"y": key if valid else 5}
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert types.type_errors(proc) == []
    assert any(kind == types.BOOLEAN for _name, kind in proc.types.items())
    for row, expected in [({"a": 0}, 0), ({"a": 1}, 5), ({"a": 3}, 6)]:
        _state, output, _hits, error = interp.run_step(proc, row)
        assert error == ""
        assert output.buffers["y"][0] == expected
    assert encoder.verify_encoding(
        proc, env={"a": ranges.Range(0, 3)}) == []


def test_mixed_and_or_used_only_as_a_guard_has_one_boolean_ir_type():
    source = """
class M:
    def reset(self):
        pass

    def process(self, bit, seen):
        if bit and not seen:
            y = 7
        else:
            y = 2
        return {"y": y}
"""
    proc = lower_with_state_ranges(source, input_widths=[2, 2])

    assert types.type_errors(proc) == []
    for row, expected in [({"bit": 0, "seen": 0}, 2),
                          ({"bit": 2, "seen": 0}, 7),
                          ({"bit": 2, "seen": 1}, 2)]:
        _state, output, _hits, error = interp.run_step(proc, row)
        assert error == ""
        assert output.buffers["y"][0] == expected
    assert encoder.verify_encoding(proc, env={
        "bit": ranges.Range(0, 3),
        "seen": ranges.Range(0, 3),
    }) == []


def test_short_circuit_value_is_promoted_when_arithmetic_consumes_it():
    source = """
class M:
    def reset(self):
        pass

    def process(self, a, b):
        return {"y": (a and not b) + 2}
"""
    proc = lower_with_state_ranges(source, input_widths=[2, 2])

    assert types.type_errors(proc) == []
    for row, expected in [({"a": 0, "b": 0}, 2),
                          ({"a": 3, "b": 0}, 3),
                          ({"a": 3, "b": 1}, 2)]:
        _state, output, _hits, error = interp.run_step(proc, row)
        assert error == ""
        assert output.buffers["y"][0] == expected


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("body", [
    "return a > 1 or b",
    "return True if a else b",
    "if a:\n            return True\n        return b",
])
@pytest.mark.parametrize("consumer", [
    "self.pick(a, b) + 3",
    "8 if self.pick(a, b) else 9",
    "int(self.pick(a, b))",
    "int(bool(self.pick(a, b)))",
    "self.forward(a, b) + 3",
])
def test_helper_scalar_consumption_preserves_native_values_and_hits(lowerer, body, consumer):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.construction import ConstructionFacts
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_plan import plan_source

    source = f'''class M:
    def reset(self): pass
    def pick(self, a, b):
        {body}
    def forward(self, a, b):
        return self.pick(a, b)
    def process(self, a, b):
        return {{"y": {consumer}}}
'''
    proc = lower_with_state_ranges(
        source, input_widths=[2, 2], lowerer=lowerer,
        construction=ConstructionFacts((), (), (), ()))
    goal_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=goal_plan)
    native = namespace['M']()
    native.reset()
    assert types.type_errors(proc) == []
    encoded = encoder.encode_step(proc, env={
        "a": ranges.Range(0, 3), "b": ranges.Range(0, 3)})
    for a in range(4):
        for b in range(4):
            native_hits.clear()
            expected = native.process(a, b)
            _state, output, hits, error = interp.run_step(proc, {"a": a, "b": b})
            assert error == ""
            assert output.transactions == [expected]
            assert type(output.buffers['y'][0]) is type(expected['y'])
            assert hits == set(native_hits)
            solver = z3.Solver()
            solver.add(encoded.constraint, encoded.pre['a'] == a, encoded.pre['b'] == b)
            assert solver.check() == z3.sat
            solver.add(z3.Or(
                encoded.error != 0, encoded.post[encoder.COUNT] != 1,
                encoded.post['y'][0] != expected['y'],
                *(term != (goal in hits) for goal, term in encoded.hits.items())))
            assert solver.check() == z3.unsat
    assert encoder.verify_encoding(proc, env={
        "a": ranges.Range(0, 3), "b": ranges.Range(0, 3)}) == []


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("scope", ["module", "nested"])
def test_scalar_consumption_respects_lexical_helper_resolution(lowerer, scope):
    body = "def pick(a):\n    return a or True\n"
    source = (body if scope == "module" else "") + '''class M:
    def reset(self): pass
    def process(self, a):
'''
    if scope == "nested":
        source += "        def pick(value):\n            return value or True\n"
    source += "        return {'y': pick(a) + 3}\n"
    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    assert types.type_errors(proc) == []
    for a in range(4):
        _state, output, _hits, error = interp.run_step(proc, {"a": a})
        assert error == ""
        assert output.transactions == [{"y": (a or True) + 3}]


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_helper_numeric_result_does_not_coerce_arguments_or_repeat_effects(lowerer):
    source = '''class M:
    def reset(self):
        self.count = 0
        self.flag = False
    def pick(self, flag):
        self.count += 1
        self.flag = flag
        return True if flag else self.count
    def process(self, a):
        value = self.pick(a > 0) + 10 * self.pick(False)
        return {"y": value, "count": self.count, "flag": int(self.flag)}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer, bound=4)
    assert proc.types["flag"] == types.BOOLEAN
    namespace = {}
    exec(source, namespace)  # noqa: S102 -- local test fixture
    native = namespace['M']()
    native.reset()
    state = None
    for a in (0, 3, 1, 0):
        expected = native.process(a)
        state, output, _hits, error = interp.run_step(proc, {"a": a}, state=state)
        assert error == ""
        assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
def test_helper_boolean_bitwise_result_keeps_boolean_type(lowerer):
    source = '''class M:
    def reset(self): pass
    def predicate(self, a):
        return a > 0
    def process(self, a):
        selected = self.predicate(a) & True
        return {"y": int(selected & True)}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    assert proc.types['selected'] == types.BOOLEAN
    for a in range(4):
        _state, output, _hits, error = interp.run_step(proc, {"a": a})
        assert error == ""
        assert output.transactions == [{"y": int(a > 0)}]


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("exposed", ["return {'y': self.pick(a)}",
                                      "self.value = self.pick(a)\n        return {'y': a}"])
def test_mixed_helper_result_is_not_coerced_at_output_or_state_boundary(lowerer, exposed):
    source = f'''class M:
    def reset(self): self.value = 0
    def pick(self, a): return True if a else 2
    def process(self, a):
        {exposed}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    assert caught.value.code == "type-inconsistent"


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("prefix,operator", [("False", "or"), ("True", "and")])
def test_nonreturning_boolean_prefix_keeps_effects_and_native_hits(lowerer, prefix, operator):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_plan import plan_source

    source = f'''class M:
    def reset(self): self.count = 0
    def touch(self):
        self.count += 1
        return {prefix}
    def process(self, a):
        value = self.touch() {operator} (a + 2)
        return {{"y": value, "count": self.count}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer, bound=4)
    goal_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=goal_plan)
    native = namespace['M']()
    native.reset()
    state = None
    for a in range(4):
        native_hits.clear()
        expected = native.process(a)
        state, output, hits, error = interp.run_step(proc, {"a": a}, state=state)
        assert error == ""
        assert output.transactions == [expected]
        assert hits == set(native_hits)
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    assert encoder.verify_encoding(proc, env=env) == []
    encoder.encode_step(proc, env=env)


def test_mixed_boolean_integer_short_circuit_has_one_local_carrier():
    source = """
class M:
    def reset(self):
        pass

    def process(self, sign, special):
        selected = ((special == 1 and sign)
                    or (special == 2 and (not sign)))
        return {"y": 7 if selected else 2}
"""
    proc = lower_with_state_ranges(source, input_widths=[1, 2])

    assert types.type_errors(proc) == []
    assert proc.types["selected"] == types.INTEGER
    for row, expected in [({"sign": 0, "special": 0}, 2),
                          ({"sign": 1, "special": 1}, 7),
                          ({"sign": 0, "special": 2}, 7),
                          ({"sign": 1, "special": 2}, 2)]:
        _state, output, _hits, error = interp.run_step(proc, row)
        assert error == ""
        assert output.buffers["y"][0] == expected
    assert encoder.verify_encoding(proc, env={
        "sign": ranges.Range(0, 1),
        "special": ranges.Range(0, 3),
    }) == []


def test_mixed_boolean_integer_conditional_has_one_local_carrier():
    source = """
class M:
    def reset(self):
        pass

    def process(self, invalid, value):
        positive = 0 if invalid else value > 0
        return {"y": 7 if positive else 2}
"""
    proc = lower_with_state_ranges(source, input_widths=[1, 2])

    assert types.type_errors(proc) == []
    assert proc.types["positive"] == types.INTEGER
    for row, expected in [({"invalid": 1, "value": 3}, 2),
                          ({"invalid": 0, "value": 0}, 2),
                          ({"invalid": 0, "value": 3}, 7)]:
        _state, output, _hits, error = interp.run_step(proc, row)
        assert error == ""
        assert output.buffers["y"][0] == expected
    assert encoder.verify_encoding(proc, env={
        "invalid": ranges.Range(0, 1),
        "value": ranges.Range(0, 3),
    }) == []


def test_static_short_circuit_prefix_preserves_truth_only_local_value():
    source = """
class M:
    def reset(self):
        pass

    def process(self, value):
        selected = True and (value > 0)
        return {"y": 7 if selected else 2}
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert types.type_errors(proc) == []
    assert proc.types["selected"] == types.INTEGER
    assert encoder.verify_encoding(
        proc, env={"value": ranges.Range(0, 3)}) == []


@pytest.mark.parametrize("initial", ["0", "False"])
@pytest.mark.parametrize("chained", [False, True])
def test_truth_only_helper_local_has_one_carrier_across_assignments(initial, chained):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.construction import ConstructionFacts
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_plan import plan_source

    target = "up = other" if chained else "up"
    guard = "up or other" if chained else "up"
    source = f'''class M:
    def reset(self): pass
    def helper(self, a, sg):
        g, st = a & 1, a != 0
        {target} = {initial}
        if a:
            if sg: {target} = g and (st or (a & 1))
            else: {target} = not bool(sg)
        return a + 1 if {guard} else a
    def process(self, a, sg):
        return {{'y': self.helper(a, sg)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[3, 1],
                                  construction=ConstructionFacts((), (), (), ()))
    goal_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=goal_plan)
    native = namespace['M']()
    native.reset()
    assert types.type_errors(proc) == []
    for a in range(8):
        for sg in range(2):
            native_hits.clear()
            expected = native.process(a, sg)
            _state, output, hits, error = interp.run_step(proc, {"a": a, "sg": sg})
            up = (a & 1) if sg else 1
            assert error == ""
            assert output.buffers["y"][0] == a + int(bool(a and up))
            assert output.buffers['y'][0] == expected['y']
            assert hits == set(native_hits)
    assert encoder.verify_encoding(proc, env={
        "a": ranges.Range(0, 7), "sg": ranges.Range(0, 1)}) == []


def test_record_fields_in_a_truth_only_local_share_one_integer_carrier():
    source = """
class M:
    def reset(self):
        pass

    def fields(self, value):
        return {"left": value & 1, "right": (value >> 1) & 1}

    def process(self, value):
        raw = self.fields(value)
        selected = raw["left"] and (not raw["right"])
        return {"y": 7 if selected else 2}
"""
    proc = lower_with_state_ranges(source, input_widths=[2])

    assert types.type_errors(proc) == []
    assert proc.types["selected"] == types.INTEGER
    for value, expected in ((0, 2), (1, 7), (2, 2), (3, 2)):
        _state, output, _hits, error = interp.run_step(
            proc, {"value": value})
        assert error == ""
        assert output.buffers["y"][0] == expected
    assert encoder.verify_encoding(
        proc, env={"value": ranges.Range(0, 3)}) == []


def test_short_circuit_operand_returned_as_a_value_is_not_truth_only():
    source = """
class M:
    def reset(self):
        pass

    def process(self, choose):
        selected = True if choose else 2
        exposed = selected or 7
        return {"y": exposed}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1])
    assert getattr(caught.value, "code", None) == "type-inconsistent"


def test_python_numeric_equality_and_boolean_bitwise_result_types_are_exact():
    source = """
class M:
    def reset(self):
        pass

    def process(self, a, b):
        both = (a > 0) & (b > 0)
        mixed = (a > 0) & 1
        same = (a > 0) == 1
        y = int(both) * 100 + mixed * 10 + int(same)
        return {"y": y}
"""
    proc = lower_with_state_ranges(source, input_widths=[2, 2])

    assert types.type_errors(proc) == []
    assert proc.types["both"] == types.BOOLEAN
    assert proc.types["mixed"] == types.INTEGER
    assert proc.types["same"] == types.BOOLEAN
    _state, output, _hits, error = interp.run_step(
        proc, {"a": 2, "b": 0})
    assert error == ""
    assert output.buffers["y"][0] == 11


def test_assignment_and_integer_output_boundaries_still_reject_boolean_values():
    source = """
class M:
    def reset(self):
        pass

    def process(self, a, b):
        return {"y": a and not b}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[2, 2],
            signature=ProcedureSignature(
                (("a", FiniteType(2)), ("b", FiniteType(2))),
                (("y", FiniteType(2)),),
            ),
        )
    assert getattr(caught.value, "code", None) == "type-inconsistent"


def test_mixed_local_cannot_hide_a_noninteger_public_result():
    source = """
class M:
    def reset(self):
        pass

    def process(self, a, b):
        selected = a and not b
        return {"y": selected}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[2, 2],
            signature=ProcedureSignature(
                (("a", FiniteType(2)), ("b", FiniteType(2))),
                (("y", FiniteType(2)),),
            ),
        )
    assert getattr(caught.value, "code", None) == "type-inconsistent"


def test_procedure_owns_complete_typed_contexts_and_survives_pickle():
    source = """
class M:
    def reset(self):
        self.flag = False

    def process(self, a):
        if a:
            self.flag = not self.flag
        return {"y": 1 if self.flag else 0}
"""
    proc = lower_with_state_ranges(source, input_widths=[2])
    semantics = proc.semantics

    assert semantics is not None
    assert dict(semantics.state_types) == {"flag": types.BOOLEAN}
    assert dict(semantics.input_types) == {"a": types.INTEGER}
    assert dict(semantics.local_types) == {
        name: proc.types[name] for name in proc.locals
    }
    assert dict(semantics.output_types) == {"y": types.INTEGER}
    assert type(semantics.value_layout("state", "flag")) is BooleanLayout
    input_layout = semantics.value_layout("input", "a")
    output_layout = semantics.value_layout("output", "y")
    assert type(input_layout) is type(output_layout) is IntegerLayout
    assert input_layout.carrier is output_layout.carrier is None
    assert any(name.startswith(("#taken:", "#reached:"))
               for name, kind in semantics.local_types
               if kind == types.BOOLEAN)

    restored = pickle.loads(pickle.dumps(proc))
    assert restored.semantics == semantics
    assert restored.semantics.identity == semantics.identity


def test_derived_procedure_inherits_only_an_unchanged_semantic_authority():
    proc = ir.Procedure((), (), (), (), ir.Fail("First", "g:first"))
    semantics = bind_procedure_contract(proc)

    metadata_only = proc.derived(provenance={"g:first": ("test.py", 1)})
    assert metadata_only.semantics is semantics

    changed = proc.derived(body=ir.Fail("Second", "g:second"))
    assert changed.semantics is None
    assert bind_procedure_contract(changed).error_codes == ("", "Second")

    proc.body = ir.Fail("Forged", "g:forged")
    with pytest.raises(ProcedureContractError, match="changed after"):
        bind_procedure_contract(proc)
    assert any("semantic contract is inconsistent" in fault
               for fault in ir.normalized(proc))


def test_interpreter_and_encoder_use_the_procedure_error_alphabet():
    proc = ir.Procedure((), (), (), (), ir.Fail("ModelFault", "g:fault"))

    _state, output, hits, error = interp.run_step(proc, {})
    assert output.count == 0
    assert hits == {"g:fault"}
    assert error == "ModelFault"
    assert proc.semantics.error_codes == ("", "ModelFault")
    assert proc.semantics.artifact_errors == ("ModelFault",)
    assert proc.semantics.analysis_errors == ()
    assert "ModelFault" not in encoder.ERROR_CODES

    encoded = encoder.encode_step(proc, env={})
    assert encoded.error_codes == proc.semantics.error_codes
    solver = z3.Solver()
    solver.add(encoded.constraint)
    assert solver.check() == z3.sat
    model = solver.model()
    assert encoder.error_name(
        model.eval(encoded.error, model_completion=True),
        encoded.error_codes,
    ) == "ModelFault"

    limit = ir.Procedure((), (), (), (), ir.Fail("Unwound", "g:limit"))
    limited = bind_procedure_contract(limit)
    assert limited.artifact_errors == ()
    assert limited.analysis_errors == ("Unwound",)
    assert limited.error_codes == ("", "Unwound")


def test_procedure_contract_identity_changes_with_every_owned_fact_class():
    base = ProcedureContract(
        (), (("a", types.INTEGER),), (), (("y", types.INTEGER),),
        (("a", FiniteType(2)),), (("y", FiniteType(2)),),
        ("ModelFault",), ("Unwound",),
    )
    changed_type = ProcedureContract(
        (), (("a", types.BOOLEAN),), (), (("y", types.INTEGER),),
        artifact_errors=("ModelFault",), analysis_errors=("Unwound",),
    )
    changed_domain = ProcedureContract(
        (), (("a", types.INTEGER),), (), (("y", types.INTEGER),),
        (("a", FiniteType(3)),), (("y", FiniteType(2)),),
        ("ModelFault",), ("Unwound",),
    )
    changed_errors = ProcedureContract(
        (), (("a", types.INTEGER),), (), (("y", types.INTEGER),),
        (("a", FiniteType(2)),), (("y", FiniteType(2)),),
        ("OtherFault",), ("Unwound",),
    )

    assert len({base.identity, changed_type.identity,
                changed_domain.identity, changed_errors.identity}) == 4


def test_finite_carriers_do_not_change_mathematical_integer_arithmetic():
    signature = ProcedureSignature(
        (("u", FiniteType(2)), ("s", FiniteType(2, signed=True))), ())
    comparisons = ir.Procedure(
        (), ("u", "s"), (), (),
        ir.Seq(
            ir.If(ir.Op("lt", ir.Var("u"), ir.Lit(1)),
                  ir.Mark("unsigned-less"), ir.Skip()),
            ir.If(ir.Op("lt", ir.Var("s"), ir.Lit(0)),
                  ir.Mark("signed-less"), ir.Skip()),
        ),
        types={"u": types.INTEGER, "s": types.INTEGER},
        signature=signature,
    )
    assert interp.run_step(comparisons, {"u": 3, "s": -2})[2] == {
        "signed-less"}
    encoded = encoder.encode_step(
        comparisons,
        env={"u": ranges.Range(0, 3), "s": ranges.Range(-2, 1)},
    )
    solver = z3.Solver()
    solver.add(
        encoded.constraint,
        encoded.pre["u"] == 3,
        encoded.pre["s"] == -2,
    )
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_false(model.eval(
        encoded.hits["unsigned-less"], model_completion=True))
    assert z3.is_true(model.eval(
        encoded.hits["signed-less"], model_completion=True))

    output_signature = ProcedureSignature(
        (("a", FiniteType(2)),), (("y", FiniteType(2)),))
    overflow = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Op(
            "arraylit", ir.Op("add", ir.Var("a"), ir.Lit(1)))),
        types={"a": types.INTEGER, "y": types.Array(1, types.INTEGER)},
        signature=output_signature,
        output_capacity=1,
    )
    with pytest.raises(interp.InterpError, match="outside u2"):
        interp.run_step(overflow, {"a": 3})

    encoded = encoder.encode_step(
        overflow, env={"a": ranges.Range(0, 3)})
    solver = z3.Solver()
    solver.add(
        encoded.constraint,
        encoded.pre["a"] == 3,
        z3.Not(encoded.output_valid),
    )
    assert solver.check() == z3.sat
    assert solver.model().eval(
        encoded.post["y"][0], model_completion=True).as_long() == 4


def test_finite_task_domain_does_not_implicitly_admit_boolean_port():
    signature = ProcedureSignature((), (("y", FiniteType(1)),))
    proc = ir.Procedure(
        (), (), (), ("y",), ir.Assign("y", ir.Lit(True)),
        types={"y": types.BOOLEAN},
        signature=signature,
    )

    with pytest.raises(ProcedureContractError, match="integer-valued"):
        bind_procedure_contract(proc)
    with pytest.raises(interp.InterpError, match="integer-valued"):
        interp.run_step(proc, {})
    with pytest.raises(encoder.EncodingError) as caught:
        encoder.encode_step(proc, env={"y": ranges.Range(0, 1)})
    assert caught.value.kind == "type"


def test_native_and_symbolic_inputs_share_the_procedure_finite_domain():
    signature = ProcedureSignature((("a", FiniteType(1)),), ())
    proc = ir.Procedure(
        (), ("a",), (), (), ir.Skip(),
        types={"a": types.INTEGER},
        signature=signature,
    )
    semantics = bind_procedure_contract(proc)
    assert semantics.input_domains == signature.inputs
    assert semantics.value_layout("input", "a").carrier == FiniteType(1)
    assert interp.run_step(proc, {"a": 1})[3] == ""
    with pytest.raises(interp.InterpError, match="outside u1"):
        interp.run_step(proc, {"a": 2})

    assert encoder.encode_step(
        proc, env={"a": ranges.Range(0, 1)}).constraint is not None
    with pytest.raises(encoder.EncodingError) as caught:
        encoder.encode_step(proc, env={"a": ranges.Range(0, 3)})
    assert caught.value.kind == "range"
    assert "finite procedure domain" in str(caught.value)
    with pytest.raises(plan.PlanMismatch, match="finite procedure domain"):
        plan.plan_encoding(proc, {"a": ranges.Range(0, 3)})
