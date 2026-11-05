"""Source loop-entry events agree with both lowerings and their bit encodings."""

import textwrap

import pytest

from behave.sim.behavior import goals, instrumented
from behave.sim.symbolic import encoder, interp, ir, plan, ranges
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver


CASES = (
    ("exact", """\
        i = 0
        while i < a:
            i += 1
        return {'y': i}
    """, lambda a: (False,) if a == 0 else (True,)),
    ("ranked", """\
        i = 0
        j = 0
        while i < a and j < a:
            if i <= j:
                i += 1
            else:
                j += 1
        return {'y': i + j}
    """, lambda a: (False,) if a == 0 else (True,)),
    ("cutoff", """\
        i = 0
        while True:
            if i >= a:
                break
            i += 1
        return {'y': i}
    """, lambda a: (True,)),
    ("reentry", """\
        y = 0
        for limit in [0, a]:
            i = 0
            while i < limit:
                i += 1
                y += 1
        return {'y': y}
    """, lambda a: (False,) if a == 0 else (False, True)),
    ("return", """\
        n = a
        while n > 0:
            if n == 2:
                return {'y': n}
            n -= 1
        return {'y': 0}
    """, lambda a: (False,) if a == 0 else (True,)),
)


def _source(body):
    return ("class M:\n    def reset(self):\n        pass\n"
            "    def process(self, a):\n" + textwrap.indent(textwrap.dedent(body), "        "))


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer), ids=("graph", "term"))
@pytest.mark.parametrize("name,body,outcomes", CASES, ids=[row[0] for row in CASES])
def test_while_activation_from_source_through_symbolic_hits(lowerer, name, body, outcomes):
    source = _source(body)
    plain = {}
    exec(compile(source, "behavior.py", "exec"), plain)  # noqa: S102 - trusted test fixture
    native, sites, native_hits = instrumented.load_source(source)
    loop_sites = [site for site in sites if site.kind == "while-loop"]
    # The expected schema count comes from the fixture, not the produced IR.
    assert len(loop_sites) == 1
    loop = loop_sites[0]
    assert loop.family == goals.LOOP and loop.outcomes == (True, False)

    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    assert set(loop.goals) <= ir.goals_of(proc.body)
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    prepared = plan.plan_encoding(proc, env)
    assert prepared.complete, prepared.unresolved
    encoded = encoder.encode_step(proc, env=env, plan=prepared)
    for value in range(4):
        native_hits.clear()
        expected = plain["M"]().process(value)
        assert native["M"]().process(value) == expected
        _state, output, hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [expected]
        expected_activation = {goals.goal_id(loop, outcome) for outcome in outcomes(value)}
        assert set(native_hits) & set(loop.goals) == expected_activation
        assert hits & set(loop.goals) == expected_activation
        assert set(native_hits) == hits
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value)
        assert solver.check() == z3.sat
        model = solver.model()
        symbolic_hits = {goal for goal, term in encoded.hits.items()
                         if z3.is_true(model.eval(term, model_completion=True))}
        assert symbolic_hits == hits


@pytest.mark.isolation
@pytest.mark.parametrize("name,body,outcomes", CASES, ids=[row[0] for row in CASES])
def test_while_activation_survives_child_replay_and_audit(name, body, outcomes):
    from behave.sim.behavior.audit import model_audit
    from behave.sim.behavior.isolated import isolated_model

    source = _source(body)
    proc = lower_with_state_ranges(source, input_widths=[2])
    make = isolated_model(source)
    try:
        trace = [{"a": value} for value in (0, 2, 3, 0)]
        assert model_audit(proc, make, [trace], make.hits) == []
    finally:
        make.close()


@pytest.mark.isolation
def test_while_body_failure_keeps_the_activation_hit_through_audit():
    from behave.sim.behavior.audit import model_audit
    from behave.sim.behavior.isolated import isolated_model

    source = _source("""\
        n = a
        y = 0
        while n > 0:
            y = 3 // (n - 2)
            n -= 1
        return {'y': y}
    """)
    proc = lower_with_state_ranges(source, input_widths=[2])
    loop = next(site for site in goals.enumerate_sites(source) if site.kind == "while-loop")
    _state, _output, hits, error = interp.run_step(proc, {"a": 2})
    assert error and goals.goal_id(loop, True) in hits
    assert goals.goal_id(loop, False) not in hits
    make = isolated_model(source)
    try:
        assert model_audit(proc, make, [[{"a": 2}]], make.hits) == []
    finally:
        make.close()
