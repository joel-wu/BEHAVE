"""Fresh fixed-schema constructors retain Python values, effects and goals."""

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import encoder, interp
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema


def _source(body, *, helper=""):
    return helper + """\nclass M:
    def reset(self):
        self.x = 0
    def bump(self):
        self.x = (self.x + 1) & 15
        return self.x
    def process(self, a):
""" + "\n".join("        " + line for line in body.splitlines()) + "\n"


CASES = [
    ("direct", _source("return dict(y=a+1)")),
    ("comprehension", _source("return [dict(y=a+i) for i in range(3)]")),
    ("filtered", _source("return [dict(y=a+i) for i in range(3) if i < a]")),
    ("local", _source("r = dict(y=a)\nreturn r")),
    ("empty-builder", _source("r = dict()\nr['y'] = a\nreturn r")),
    ("update", _source("r = dict(y=a)\nr['y'] = r['y']+1\nreturn r")),
    ("keyword-update", _source("r = dict(y=a)\nr.update(y=a+1)\nreturn r")),
    ("update-swap", _source(
        "r = dict(left=a, right=a+1)\nr.update(left=r['right'], right=r['left'])\n"
        "return dict(y=16*r['left']+r['right'])")),
    ("update-effects", _source(
        "r = dict(left=0, right=0)\nr.update(left=self.x, right=self.bump())\n"
        "return dict(y=16*r['left']+r['right'])")),
    ("append", _source("rows = []\nfor i in range(3):\n    rows.append(dict(y=a+i))\nreturn rows")),
    ("helper-comprehension", _source(
        "return [row(a+i) for i in range(3)]",
        helper="def row(value):\n    return dict(y=value)\n")),
    ("nested", _source("r = dict(q=dict(z=a))\nreturn dict(y=r['q']['z']+1)")),
    ("capture-before-effect", _source(
        "r = dict(left=self.x, right=self.bump())\nreturn dict(y=16*r['left']+r['right'])")),
    ("keyword-order", _source(
        "r = dict(right=self.bump(), left=self.x)\nreturn dict(y=16*r['left']+r['right'])")),
    ("shadowed-module", _source("return dict(y=a)",
        helper="def dict(y):\n    return {'y': y+1}\n")),
    ("shadowed-nested", _source(
        "def dict(y):\n    return {'y': y+2}\nreturn dict(y=a)")),
    ("conditional", _source(
        "return dict(y=a+1) if a < 8 else dict(y=a-1)")),
]


def _schema():
    return TransactionSchema(
        [Endpoint("a", "input", 4), Endpoint("y", "output", 16)],
        authority=Authority.TASK, reading=Reading.ELABORATED)


@pytest.mark.solver
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_records_match_python_ir_and_source_hits(name, source):
    plan = plan_source(source_model(source), source)
    exact = lower(source, _schema(), bound=1)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    native_namespace, _, native_hits = instrumented.load_source(source, goal_plan=plan)
    plain_namespace = {}
    exec(compile(source, "behavior.py", "exec"), plain_namespace)
    native, plain = native_namespace["M"](), plain_namespace["M"]()
    native.reset()
    plain.reset()
    state = None
    for a in range(16):
        native_hits.clear()
        expected = plain.process(a)
        assert native.process(a) == expected
        state, output, hits, error = interp.run_step(exact.proc, {"a": a}, state)
        assert not error
        rows = expected if isinstance(expected, list) else [expected]
        assert output.buffers["y"] == [row["y"] for row in rows]
        assert hits == set(native_hits)


@pytest.mark.solver
@pytest.mark.parametrize('body', [
    "return [dict(y=a+i) for i in range(3) if i < a]",
    "r = dict(y=a)\nif a == 9:\n    r.update(y=a+1)\nreturn r",
])
def test_constructor_every_goal_query_matches_finite_native_oracle(body):
    import z3

    source = _source(body)
    plan = plan_source(source_model(source), source)
    exact = lower(source, _schema(), bound=1)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    model = namespace["M"]()
    model.reset()
    oracle = []
    for a in range(16):
        hits.clear()
        model.process(a)
        oracle.append(frozenset(hits))
    encoded = encoder.encode_step(exact.proc, env=exact.env)
    solver = z3.Solver()
    solver.add(encoded.constraint, z3.ULE(encoded.pre["a"], 15))
    assert set(plan.manifest) <= encoded.hits.keys()
    for goal in plan.manifest:
        solver.push()
        solver.add(encoded.hits[goal])
        possible = any(goal in row for row in oracle)
        assert solver.check() == (z3.sat if possible else z3.unsat), goal
        if possible:
            a = solver.model().eval(encoded.pre["a"]).as_long()
            hits.clear()
            model.process(a)
            assert goal in hits
        solver.pop()


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("value", [0, 1, 15])
def test_both_retained_lowerers_emit_keyword_records(lowerer, value):
    source = _source("return [dict(y=a+i) for i in range(3) if i < a]")
    proc = lower_with_state_ranges(source, input_widths=[4], lowerer=lowerer)
    _state, output, _hits, error = interp.run_step(proc, {"a": value})
    assert not error
    assert output.transactions == [{"y": value+i} for i in range(3) if i < value]


@pytest.mark.parametrize("body", [
    "dict = 0\nreturn dict(y=a)",
    "return [dict(y=a) for dict in range(2)]",
])
def test_shadowed_noncallable_dict_is_not_a_constructor(body):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(_source(body), input_widths=[4])


@pytest.mark.parametrize("body", [
    "return {'y': 1//a}", "return dict(y=1//a)",
    "r = dict(y=0)\nr.update(y=1//a)\nreturn r",
])
def test_constructor_retains_failure_goal(body):
    source = _source(body)
    plan = plan_source(source_model(source), source)
    exact = lower(source, _schema(), bound=1)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=plan)
    model = namespace["M"]()
    model.reset()
    native_hits.clear()
    with pytest.raises(ZeroDivisionError) as failure:
        model.process(0)
    goal = instrumented.attribute_safety_failure(failure.value, plan.safety_sites)
    assert goal is not None
    native_hits.append(goal)
    _state, _output, hits, error = interp.run_step(exact.proc, {"a": 0})
    assert error
    assert hits == set(native_hits)
    assert any(goal.startswith("safety:") for goal in hits)
