"""Source guards bound each visit without freezing loop-carried values."""

import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import encoder, interp, ir, plan, ranges
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lowering import lower_procedure
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
from behave.sim.engines.local_ir import local_hit_any
from behave.sim.engines.probing import Outcome

z3 = pytest.importorskip("z3")

pytestmark = pytest.mark.solver


GUARDED = """def signed(v, width=16):
    v &= (1 << width) - 1
    return v - (1 << width) if v >= (1 << (width - 1)) else v

def scaled(v, shift):
    return v << (shift if shift > 0 else 0)

class M:
    def reset(self):
        pass
    def process(self, a):
        total = 0
        for i in range(2):
            shift = signed(a >> (16*i))
            if shift < 0 or shift > 3:
                return {'y': 0}
            total += scaled(1, shift)
        return {'y': total}
"""


def test_helper_shift_guard_is_preserved_on_all_loop_visits():
    proc = lower_procedure(GUARDED, outputs=["y"], input_widths=[32])
    env = {"a": ranges.Range(0, 2**32-1)}
    prepared = plan.plan_encoding(proc, env)
    assert prepared.complete, prepared.unresolved
    encoder.encode_step(proc, env=env, plan=prepared)


def test_loop_free_source_query_uses_its_proved_guard_carriers():
    source = """class M:
    def reset(self):
        pass
    def process(self, a):
        if a < 0 or a > 3:
            return {'y': 0}
        value = 1 << a
        if value == 8:
            return {'y': 1}
        return {'y': 0}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 16), Endpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    obj = lower(source, schema, bound=1)
    enc = encoder.encode_step(obj.proc, env=obj.env)
    solver = z3.Solver()
    solver.add(enc.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.BV2Int(enc.post["y"][0], is_signed=True)
               != z3.If(enc.pre["a"] == 3, 1, 0))
    assert solver.check() == z3.unsat
    goal, = [goal for goal in obj.frontend_catalogue_manifest()
             if goal.startswith("location:") and ":9:12:" in goal]
    result = local_hit_any(obj.proc, obj.env, {}, (goal,),
        timeout_ms=60_000)
    assert result.outcome is Outcome.SAT, result.detail
    assert result.candidate == {"a": 3}
    projected = encoder.encode_goal_query(obj.proc, (goal,), env=obj.env, tag='query')
    solver = z3.Solver()
    solver.add(projected.constraint, projected.hits[goal])
    assert solver.check() == z3.sat
    assert solver.model().eval(projected.pre['a']).as_long() == 3
    solver.add(projected.pre['a'] != 3)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('condition', [
    'shift < 0 or shift > 3',
    'not 0 <= shift <= 3',
    'a < 2 or not 0 <= shift <= 3 or a > 100',
])
@pytest.mark.parametrize('retained', [False, True])
def test_preceding_short_circuit_guard_bounds_loop_invariant(condition, retained):
    body = ('        result = 0\n        for item in [1, 2]:\n'
            '            result += item * (1 << shift)\n' if retained else
            '        result = 3 * (1 << shift)\n')
    source = ("class M:\n    def reset(self):\n        pass\n"
              "    def process(self, a):\n        shift = a - 16\n"
              f"        if {condition}:\n            return {{'y': 0}}\n"
              + body + "        return {'y': result}\n")
    schema = TransactionSchema(
        [Endpoint('a', 'input', 5), Endpoint('y', 'output', 5)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    obj = lower(source, schema, bound=1)
    prepared = plan.plan_encoding(obj.proc, obj.env)
    assert prepared.complete, prepared.unresolved
    numbers = plan._command_numbers(obj.proc.body)
    entries = plan._loop_plan(obj.proc, obj.env, numbers)[3]
    checked = 0
    for command in ir.walk(obj.proc.body):
        expression = getattr(command, 'expr', None)
        if expression is None:
            continue
        position = plan.ExpressionPosition(numbers[id(command)], 'rhs')
        if any(isinstance(term, ir.Op) and term.op == 'shl'
               and isinstance(term.args[1], ir.Var) and term.args[1].name == 'shift'
               for _key, term in plan._expression_occurrences(expression, position)):
            assert entries[numbers[id(command)]]['shift'] == ranges.Range(0, 3)
            checked += 1
    assert checked
    enc = encoder.encode_step(obj.proc, env=obj.env, plan=prepared)
    source_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=source_plan)
    model = namespace['M']()
    model.reset()
    solver = z3.Solver()
    solver.add(enc.constraint)
    for value in range(32):
        native_hits.clear()
        expected = model.process(value)
        _state, outputs, hits, error = interp.run_step(obj.proc, {'a': value})
        assert not error and outputs.transactions == [expected]
        assert hits == set(native_hits)
        solver.push()
        solver.add(enc.pre['a'] == value)
        assert solver.check() == z3.sat
        solver.add(z3.Or(
            z3.BV2Int(enc.post['y'][0], is_signed=True) != expected['y'],
            *(term != (goal in hits) for goal, term in enc.hits.items())))
        assert solver.check() == z3.unsat
        solver.pop()


def test_chained_guard_on_helper_results_retains_finite_shift_carriers():
    source = """def signed(value):
    value &= 0xffffffff
    return value - (1 << 32) if value & (1 << 31) else value

class M:
    def reset(self):
        pass
    def process(self, a, b, c):
        mult, shift, cutoff = signed(a), signed(b), signed(c)
        if mult < 0 or not 0 <= shift <= 3 or cutoff > 0:
            return {'y': 0}
        result = 0
        for item in [1, 2]:
            result += item * (1 << shift)
        return {'y': result}
"""
    schema = TransactionSchema(
        [*(Endpoint(name, 'input', 32) for name in ('a', 'b', 'c')),
         Endpoint('y', 'output', 5)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    obj = lower(source, schema, bound=1)
    encoded = encoder.encode_step(obj.proc, env=obj.env)
    namespace, _, native_hits = instrumented.load_source(
        source, goal_plan=plan_source(source_model(source), source))
    native = namespace['M']()
    native.reset()
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for a, b, c in itertools.product((0, 1, 2**32-1), (0, 3, 4, 2**32-1),
                                     (0, 1, 2**32-1)):
        native_hits.clear()
        native_output = native.process(a, b, c)
        _state, outputs, hits, error = interp.run_step(obj.proc, {'a': a, 'b': b, 'c': c})
        expected = 3 * (1 << b) if a < 2**31 and b <= 3 and c in (0, 2**32-1) else 0
        assert not error and outputs.transactions == [{'y': expected}]
        assert native_output == {'y': expected} and hits == set(native_hits)
        solver.push()
        solver.add(encoded.pre['a'] == a, encoded.pre['b'] == b, encoded.pre['c'] == c)
        assert solver.check() == z3.sat
        solver.add(z3.Or(
            z3.BV2Int(encoded.post['y'][0], is_signed=True) != expected,
            *(term != (goal in hits) for goal, term in encoded.hits.items())))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.fixture(scope="module")
def guarded_instance():
    schema = TransactionSchema(
        [Endpoint("a", "input", 32), Endpoint("y", "output", 16)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    return lower(GUARDED, schema, bound=1)


def test_guarded_output_agrees_over_the_complete_input_domain(guarded_instance):
    obj = guarded_instance
    enc = encoder.encode_step(obj.proc, env=obj.env)
    a = enc.pre["a"]
    low, high = z3.Extract(15, 0, a), z3.Extract(31, 16, a)
    valid = z3.And(z3.ULE(low, 3), z3.ULE(high, 3))

    def power(word):
        return z3.If(word == 0, 1, z3.If(word == 1, 2, z3.If(word == 2, 4, 8)))

    expected = z3.If(valid, power(low) + power(high), 0)
    solver = z3.Solver()
    solver.add(enc.constraint, z3.ULE(a, 2**32-1))
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(enc.ok), enc.post["#count"] != 1,
                     z3.BV2Int(enc.post["y"][0], is_signed=True) != expected))
    assert solver.check() == z3.unsat


def test_native_ir_and_symbolic_hits_agree_at_guard_boundaries(guarded_instance):
    obj = guarded_instance
    source_plan = plan_source(source_model(GUARDED), GUARDED)
    namespace, _, native_hits = instrumented.load_source(GUARDED, goal_plan=source_plan)
    model = namespace["M"]()
    model.reset()
    enc = encoder.encode_step(obj.proc, env=obj.env)
    solver = z3.Solver()
    solver.add(enc.constraint)
    for low, high in itertools.product((0, 1, 3, 4, 32767, 32768, 65535), repeat=2):
        value = low | high << 16
        native_hits.clear()
        expected = model.process(value)
        _state, output, hits, error = interp.run_step(obj.proc, {"a": value})
        assert not error
        assert output.transactions == [expected]
        assert hits == set(native_hits)
        assert hits <= enc.hits.keys()
        solver.push()
        solver.add(enc.pre["a"] == value)
        assert solver.check() == z3.sat
        solver.add(z3.Or(*(term != (goal in hits) for goal, term in enc.hits.items())))
        assert solver.check() == z3.unsat
        solver.pop()


def test_every_declared_goal_has_an_exact_local_query(guarded_instance):
    obj = guarded_instance
    source_plan = plan_source(source_model(GUARDED), GUARDED)
    namespace, _, hits = instrumented.load_source(GUARDED, goal_plan=source_plan)
    model = namespace["M"]()
    model.reset()
    for goal in obj.frontend_catalogue_manifest():
        result = local_hit_any(obj.proc, obj.env, {}, (goal,),
            timeout_ms=60_000)
        assert result.outcome in (Outcome.SAT, Outcome.LOCAL_UNSAT), (goal, result.detail)
        if result.outcome is Outcome.SAT:
            hits.clear()
            model.process(**result.candidate)
            assert goal in hits


@pytest.mark.parametrize("nested", [False, True])
def test_later_loop_carried_values_are_not_frozen_to_first_visit(nested):
    body = ("        for i in range(4):\n            value += a\n" if not nested else
            "        for i in range(2):\n            for j in range(2):\n                value += a\n")
    source = ("class M:\n    def reset(self):\n        pass\n"
              "    def process(self, a):\n        value = 0\n" + body +
              "        return {'y': value}\n")
    schema = TransactionSchema(
        [Endpoint("a", "input", 4), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    obj = lower(source, schema, bound=1)
    enc = encoder.encode_step(obj.proc, env=obj.env)
    solver = z3.Solver()
    solver.add(enc.constraint, z3.ULE(enc.pre["a"], 15))
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(enc.ok), enc.post["#count"] != 1,
                     z3.BV2Int(enc.post["y"][0], is_signed=True)
                     != 4 * z3.BV2Int(enc.pre["a"], is_signed=True)))
    assert solver.check() == z3.unsat
