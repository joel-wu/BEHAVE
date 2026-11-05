"""Boolean entry values must survive range planning and source-goal queries."""

import pytest

from behave.sim.behavior import audit, goals, instrumented
from behave.sim.symbolic import interp, ir, ranges
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.engines import local_ir, probing

from .test_encoding_plan import instance

pytestmark = pytest.mark.solver


@pytest.mark.parametrize("value,bounds", [
    (False, (0, 0)),
    (True, (1, 1)),
    ([False, True], (0, 1)),
    (([False], [False]), (0, 0)),
    ((True, -2), (-2, 1)),
])
def test_boolean_leaves_have_exact_entry_ranges(value, bounds):
    assert ranges.range_of_value(value) == ranges.Range(*bounds)


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("bound", [None, 0, 1, 4])
def test_state_range_always_contains_its_boolean_initial_value(initial, bound):
    proc = ir.Procedure(
        ("flag",), (), (), (), ir.Assign("flag", ir.Lit(not initial)),
        initial={"flag": initial})
    result = (ranges.fixpoint(proc, {}) if bound is None else
              ranges.bounded(proc, {}, bound))
    expected = (ranges.Range(0, 1) if bound != 0 else
                ranges.Range(int(initial), int(initial)))
    assert result["flag"] == expected


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("array_state", [False, True])
def test_boolean_state_retains_native_branch_hits_outputs_and_queries(initial, array_state):
    source = '''class M:
    def reset(self):
        self.previous = [0, 0]
        self.started = INITIAL
    def process(self, a):
        values = [a & 15, a >> 4]
        total = 0
        for i in range(2):
            diff = values[i] - self.previous[i]
            if diff > 0:
                total += diff
        result = total >> 1
        if not self.started:
            result = 0
        UPDATE
        self.started = FINAL
        return {"y": result}
'''.replace("INITIAL", repr(initial)).replace("FINAL", repr(not initial)).replace(
        "UPDATE", "self.previous = values" if array_state else "result += 0")
    rows = [{"a": value} for value in (0, 255, 17, 240, 15)]
    model = instance(source, bound=len(rows))
    assert model.proc.state_ranges["started"] == ranges.Range(0, 1)
    namespace, sites, hits = instrumented.load_source(source, file=model.file)
    assert audit.model_audit(
        model.proc, namespace["M"], (rows,), hits, reset_indices=((3,),)) == []
    checked = model.source_audit((rows,), reset_indices=((3,),))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()
    branch = [site for site in sites if site.family == goals.DECISION][-1]
    for flag in (False, True):
        state = {**model.proc.initial, "started": flag}
        for truth in (False, True):
            goal = goals.goal_id(branch, truth)
            answer = local_ir.local_hit(model.proc, model.env, state, goal,
                timeout_ms=60_000)
            assert answer.outcome is (probing.Outcome.SAT if truth != flag else
                                      probing.Outcome.LOCAL_UNSAT), answer.detail
            if answer.outcome is probing.Outcome.SAT:
                assert goal in interp.run_step(model.proc, answer.candidate, state)[2]
