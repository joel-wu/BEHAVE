"""Source goal evidence belongs to a particular step and failure occurrence."""

import pytest

pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import audit, goals, instrumented
from behave.sim.symbolic import interp, ir
from behave.sim.core.audit import AuditCheckStatus

from .test_encoding_plan import instance

BRANCH = """
class M:
    def reset(self):
        pass
    def process(self, a):
        if a:
            return {"y": a}
        return {"y": 0}
"""

FAILURES = """
class M:
    def reset(self):
        pass
    def process(self, a):
        if a:
            y = 8 // (a - 1)
        else:
            y = 5 // a
        return {"y": y}
"""


@pytest.mark.parametrize("isolated", [False, True])
def test_source_audit_detects_hits_swapped_between_steps(monkeypatch, isolated):
    model = instance(BRANCH, bound=2)
    rows = [{"a": 0}, {"a": 1}]
    original = interp._run_step
    first = interp.run_step(model.proc, rows[0])[2]
    second = interp.run_step(model.proc, rows[1])[2]
    assert first and second and first != second
    namespace, _sites, hits = instrumented.load_source(BRANCH, file=model.file)

    def checked():
        if isolated:
            return model.source_audit((rows,)).status is AuditCheckStatus.AGREED
        return not audit.model_audit(model.proc, namespace["M"], (rows,), hits)

    assert checked()

    def swapped(proc, semantics, inputs, state=None, watch=None):
        state, outputs, _hits, error = original(proc, semantics, inputs, state, watch)
        return state, outputs, set(second if inputs["a"] == 0 else first), error

    # The union is unchanged. Only per-step comparison can detect this mutation.
    assert first | second == second | first
    monkeypatch.setattr(interp, "_run_step", swapped)
    assert not checked()


@pytest.mark.parametrize("isolated", [False, True])
def test_source_audit_preserves_output_scalar_types(monkeypatch, isolated):
    model = instance(BRANCH, bound=1)
    rows = [{"a": 1}]
    namespace, _sites, hits = instrumented.load_source(BRANCH, file=model.file)
    original = interp._run_step

    def checked():
        if isolated:
            return model.source_audit((rows,)).status is AuditCheckStatus.AGREED
        return not audit.model_audit(model.proc, namespace["M"], (rows,), hits)

    assert checked()

    def wrong_type(proc, semantics, inputs, state=None, watch=None):
        state, outputs, reached, error = original(proc, semantics, inputs, state, watch)
        assert outputs.transactions == [{"y": 1}]
        return state, interp.StepOutput(1, {"y": [True]}), reached, error

    monkeypatch.setattr(interp, "_run_step", wrong_type)
    assert not checked()


def test_source_audit_checks_the_native_safety_occurrence_not_only_error_code(monkeypatch):
    model = instance(FAILURES, bound=2)
    rows = [{"a": 1}, {"a": 0}]
    assert model.source_audit((rows,)).status is AuditCheckStatus.AGREED
    goals = {cmd.goal for cmd in ir.walk(model.proc.body)
             if isinstance(cmd, ir.Fail) and cmd.code == "DivZero"}
    assert len(goals) == 2
    original = interp._run_step

    def wrong_occurrence(proc, semantics, inputs, state=None, watch=None):
        state, outputs, hits, error = original(proc, semantics, inputs, state, watch)
        if error == "DivZero":
            hit = goals & hits
            assert len(hit) == 1
            hits = (hits - hit) | (goals - hit)
        return state, outputs, hits, error

    monkeypatch.setattr(interp, "_run_step", wrong_occurrence)
    got = model.source_audit((rows,))
    assert got.status is AuditCheckStatus.INCONSISTENT, got.as_dict()
    assert any("goals reached" in finding for finding in got.findings)


@pytest.mark.parametrize("source,rows", [
    (BRANCH, [{"a": 1}, {"a": 1}, {"a": 0}]),
    (FAILURES, [{"a": 2}, {"a": 1}, {"a": 0}]),
])
def test_source_audit_accepts_repeated_hits_and_a_partial_failure(source, rows):
    model = instance(source, bound=len(rows))
    assert model.source_audit((rows,)).status is AuditCheckStatus.AGREED


@pytest.mark.parametrize("shape,expression", [
    ("and", "1 if (a & 1) and self.adjust(a) else 0"),
    ("or", "1 if (a & 1) or self.adjust(a) else 0"),
    ("constant", "self.adjust(0)"),
])
def test_source_owned_helper_goals_survive_lazy_calls_and_constant_folding(shape, expression):
    """The source, native hooks and IR marks retain one goal definition.

    These are the Behavior counterparts of the RTL source/compiler-action
    counterexamples. Equal output values alone are insufficient: verify the
    helper's actual invocation and branch on every input, including no call.
    """
    source = '''class M:
    def reset(self):
        pass
    def adjust(self, v):
        if v & 1:
            return (v + 1) & 15
        return (v - 1) & 15
    def process(self, a):
        return {"y": EXPRESSION}
'''.replace("EXPRESSION", expression)
    rows = [{"a": a} for a in range(16)]
    model = instance(source, bound=len(rows))
    namespace, sites, hits = instrumented.load_source(source, file=model.file)
    native = namespace["M"]()
    native.reset()
    branch, = [site for site in sites
               if site.owner[0] == "adjust" and site.family == goals.DECISION]
    helper_goals = {goals.goal_id(branch, truth) for truth in (False, True)}
    assert helper_goals <= set(model.frontend_catalogue_manifest())
    assert helper_goals <= ir.goals_of(model.proc.body)

    for row in rows:
        a = row["a"]
        argument = 0 if shape == "constant" else a
        called = shape == "constant" or bool(a & 1) == (shape == "and")
        adjusted = (argument + (1 if argument & 1 else -1)) & 15
        expected_output = (adjusted if shape == "constant" else
                           int(bool((a & 1) and adjusted)) if shape == "and" else
                           int(bool((a & 1) or adjusted)))
        expected_hits = {goals.goal_id(branch, bool(argument & 1))} if called else set()
        hits.clear()
        assert native.process(**row) == {"y": expected_output}
        _, output, ir_hits, error = interp.run_step(model.proc, row)
        assert not error and output.transactions == [{"y": expected_output}]
        assert set(hits) == set(ir_hits)
        assert set(hits) & helper_goals == expected_hits

    assert model.source_audit((rows,)).status is AuditCheckStatus.AGREED
