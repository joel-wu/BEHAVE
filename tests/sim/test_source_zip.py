"""Finite zip keeps Python iteration semantics through the shared source IR."""

import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import ir
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source

from .test_source_sequences import _source


@pytest.mark.parametrize("iterable,target,term", [
    ("zip([a, 2, 3], [4, 5])", "x, y", "x * y"),
    ("zip([a], [2, 3], [4, 5])", "x, y, z", "x * y + z"),
    ("zip([a, 2])", "(x,)", "x"),
    ("zip()", "x", "100"),
    ("zip([], [a, 2])", "x, y", "100"),
    ("zip(range(a - 2), [2, 3, 4])", "x, y", "x * y"),
    ("zip(range(5, a - 2, -2), range(9, 0, -1))", "x, y", "x * y"),
    ("zip(reversed([a, 2, 3]), [4, 5])", "x, y", "x * y"),
    ("enumerate(zip([a, 2], [4, 5]), 3)", "i, (x, y)", "i + x * y"),
    ("zip(enumerate([a, 2]), [4, 5])", "(i, x), y", "i + x * y"),
    ("zip([[a, 2], [3, 4]], [5, 6])", "row, y", "row[0] * y"),
])
def test_zip_outputs_and_catalogue_match_python(iterable, target, term):
    source = _source(f"""
        total = 0
        for {target} in {iterable}:
            total += {term}
        return {{"y": total}}
    """)
    _check(source)


def _check(source):
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    planned = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=planned)
    traces = [[{"a": value}] for value in (0, 1, 2, 3, 7, 15)]
    assert audit.model_audit(proc, namespace["M"], traces, native_hits) == []
    assert ir.normalized(proc) == []
    produced = {command.goal for command in ir.walk(proc.body)
                if isinstance(command, (ir.Mark, ir.Fail)) and command.goal
                and not (isinstance(command, ir.Fail)
                         and command.code in proc.semantics.analysis_errors)}
    assert produced | set(proc.discharged) == set(planned.manifest)
    return proc


@pytest.mark.parametrize("change", [
    "xs[1] = a",
    "ys[1] = a",
    "xs = [a, a]",
    "xs[1] = a\n    xs = [7, 8]",
    "ys.pop()",
    "xs.append(a)",
])
def test_zip_holds_each_object_but_reads_live_elements_and_lengths(change):
    source = _source("""
        xs = [1, 2]
        ys = [3, 4, 5]
        total = 0
        for x, y in zip(xs, ys):
            total += x * y
            if x == 1:
                CHANGE
        return {"y": total}
    """.replace("CHANGE", change.replace("\n    ", "\n                ")))
    _check(source)


def test_zip_same_sequence_is_two_independent_iterators():
    _check(_source("""
        xs = [a, 2, 3]
        total = 0
        for x, y in zip(xs, xs):
            total += x * y
        return {"y": total}
    """))


@pytest.mark.parametrize("body", [
    "return [{'y': x * y} for x, y in zip(range(a), [3, 5, 7])]",
    """total = 0
for item in zip([a, 2], [3, 4]):
    total += item[0] * item[1]
return {'y': total + item[0]}""",
    """total = 0
for x, *tail in zip([a, 2], [3, 4], [5, 6]):
    total += x + tail[0] + tail[1]
return {'y': total}""",
])
def test_zip_comprehensions_and_tuple_bindings(body):
    _check(_source(body))


def test_module_helper_named_zip_is_not_builtin_zip():
    _check("""def zip(a, b):
    return [(a + b, 3)]
""" + _source("""
        total = 0
        for x, y in zip(a, 2):
            total += x * y
        return {"y": total}
    """))


def test_zip_argument_side_effects_evaluate_once_and_left_to_right():
    _check("""class M:
    def reset(self):
        self.n = 0
    def values(self, a):
        self.n += 1
        return [self.n * a, self.n]
    def process(self, a):
        total = 0
        for x, y in zip(self.values(a), self.values(a + 1)):
            total += x * y
        return {"y": total + self.n}
""")


def test_zip_retains_the_shared_mutable_argument_alias_refusal():
    source = """class M:
    def reset(self):
        self.xs = [2, 3]
    def replace(self, a):
        self.xs[1] = a
        self.xs = [7, 8]
        return [4, 5]
    def process(self, a):
        total = 0
        for x, y in zip(self.xs, self.replace(a)):
            total += x * y
        return {"y": total}
"""
    with pytest.raises(LoweringError, match="earlier call argument"):
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])


def test_zip_loop_control_and_exhaustion():
    _check(_source("""
        total = 0
        for x, y in zip([1, 2, 3], range(a)):
            if y == 1:
                continue
            if y == 2:
                break
            total += x
        else:
            total += 9
        return {"y": total}
    """))


def test_zip_shadowed_helper_is_not_reinterpreted_as_builtin():
    _check(_source("""
        total = 0
        for x, y in self.zip(a):
            total += x * y
        return {"y": total}
    """, helpers="""
        def zip(self, a):
            return [(a, 3)]
    """))


@pytest.mark.solver
def test_zip_goals_are_queryable_and_sat_witnesses_have_native_hits():
    from behave.sim.behavior.instance import lower
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
    from behave.sim.engines.local_ir import local_hit_any
    from behave.sim.engines.probing import Outcome

    source = _source("""
        total = 0
        for x, y in zip(range(a), [3, 5, 7]):
            total += x * y
        if total == 19:
            total = 42
        return {"y": total}
    """)
    schema = TransactionSchema([Endpoint("a", "input", 4), Endpoint("y", "output", 8)],
                               authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    planned = plan_source(source_model(source), source)
    assert exact.frontend_catalogue_manifest() == planned.manifest
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=planned)
    sat = 0
    for goal in planned.manifest:
        answer = local_hit_any(exact.proc, exact.env, dict(exact.proc.initial), (goal,),
            timeout_ms=60_000)
        assert answer.outcome in (Outcome.SAT, Outcome.LOCAL_UNSAT), answer.detail
        if answer.outcome is Outcome.SAT:
            model = namespace["M"]()
            model.reset()
            native_hits.clear()
            model.process(**answer.candidate)
            assert goal in native_hits
            sat += 1
    assert sat > 0
    assert audit.step_audit(exact.proc, exact.env,
        [[{"a": value}] for value in (0, 1, 2, 3, 15)], workers=1) == []


@pytest.mark.parametrize("call", ["zip([a], [1], strict=True)", "zip(*[[a], [1]])"])
def test_zip_unimplemented_call_forms_remain_explicit(call):
    source = _source(f"""
        total = 0
        for x, y in {call}:
            total += x * y
        return {{"y": total}}
    """)
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
