"""The check that stands between a wrong prover and a report that reads as one.

Every fault found in the encoding so far changed what the formula computed
rather than whether it was satisfiable, and a goal made unreachable by such a
fault is indistinguishable, in a report, from one that really is. The only
thing that separates them is running a stimulus at it, which needs to trust
nothing the encoder did.
"""
import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import instance as behavior_instance
from behave.sim.symbolic import interp, ranges
from behave.sim.symbolic.encoder import encode_step
from behave.sim.behavior.lowering import lower_with_state_ranges
from behave.sim.core import closure, evidence

MODEL = ('class M:\n'
         '    def reset(self): pass\n'
         '    def process(self, a):\n'
         '        if a & 1:\n'
         '            y = 1\n'
         '        else:\n'
         '            y = 2\n'
         '        return {"y": y}\n')


def build():
    proc = lower_with_state_ranges(MODEL)
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
    return behavior_instance.BehaviorInstance(proc, env, source=MODEL)


def stimuli(values):
    return [[{"a": v}] for v in values]


def made(goals, statuses):
    """A closure asserting ``statuses``, everything else reached."""
    got = {g: evidence.GoalEvidence(
        statuses.get(g, evidence.GoalStatus.REACHED), evidence.Origin.QUERY)
        for g in goals}
    return closure.Closure(
        got, {g: evidence.COVERAGE for g in goals}, steps=1,
        allow_legacy=True)


def test_a_verdict_contradicted_by_a_stimulus_is_caught():
    inst = build()
    goals = list(inst.goals())
    reachable = sorted(g for g in goals if g.endswith(":T"))
    assert reachable, "the model has a branch whose true outcome is taken"
    wrong = made(goals, {reachable[0]: evidence.GoalStatus.UNREACHABLE})
    caught = closure.self_check(wrong, inst, stimuli(range(4)))
    assert reachable[0] in caught
    assert caught[reachable[0]] in stimuli(range(4))


def test_a_verdict_no_stimulus_contradicts_is_left_alone():
    inst = build()
    goals = list(inst.goals())
    honest = made(goals, {})
    assert closure.self_check(honest, inst, stimuli(range(8))) == {}


def test_the_formula_and_a_run_agree_on_which_branch_a_stimulus_takes():
    """What the check relies on: the run attributes hits without asking the
    encoder anything."""
    inst = build()
    enc = encode_step(inst.proc, env=inst.env)
    for value in (0, 1, 254, 255):
        _, _, hits, _ = interp.run_step(inst.proc, {"a": value})
        s = z3.Solver()
        s.add(enc.constraint, enc.pre["a"] == value)
        assert s.check() == z3.sat
        m = s.model()
        said = {g for g, c in enc.hits.items()
                if z3.is_true(m.eval(c, model_completion=True))}
        assert said == hits


def test_the_step_audit_sees_a_state_variable_left_alone():
    """What it claimed to compare and did not. Every variable the step leaves is
    compared, and a state variable the run left alone is one of them: the values
    it watched were the ones a command wrote, so a formula that writes where the
    source does not was invisible in exactly the case where the source writes
    nothing.

    Only the formula is bent, and that is the whole of arranging this. The audit
    compares two readings of one procedure, so bending the procedure bends both
    readings and they agree about the bent thing: the first version of this test
    did that and found nothing, which is the audit working. `step_audit` takes an
    encoding, and that is the seam: the formula is of the bent step and the run
    is of the honest one.
    """
    from behave.sim.behavior import audit
    from behave.sim.symbolic import ir

    # Potentially written by the step, so it is state; on the even-input path
    # below the run leaves it alone. A reset-only field is a construction
    # constant and would make this test about nothing.
    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.held = 7\n'
           '    def process(self, a):\n'
           '        if a & 2:\n'
           '            self.held = 7\n'
           '        return {"y": self.held + (a & 1)}\n')
    proc = lower_with_state_ranges(src)
    assert "held" in proc.state
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
    assert audit.step_audit(proc, env, stimuli(range(4))) == [], \
        "a step that leaves the state alone agrees with itself"

    # A rule that writes a state variable the source never touches. Nothing the
    # run watched changed, so this is the shape the audit used to miss.
    bent = ir.Procedure(proc.state, proc.inputs, proc.locals, proc.outputs,
                        ir.Seq(proc.body, ir.Assign("held", ir.Lit(9))),
                        count=proc.count, initial=proc.initial,
                        output_vars=proc.output_vars)
    wrong = audit.step_audit(proc, env, stimuli(range(4)),
                             encoding=encode_step(bent, env=env))
    assert any(d.what == "held" for d in wrong), \
        [(d.what, d.said, d.ran) for d in wrong]


def test_the_step_audit_compares_how_many_transactions():
    """A buffer alone cannot tell an empty answer from an answer of zero, so the
    count is what the two readings have to agree about. It was not compared."""
    from behave.sim.behavior import audit
    from behave.sim.symbolic import ir

    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        if a & 1:\n'
           '            return [{"y": 0}]\n'
           '        return []\n')
    proc = lower_with_state_ranges(src, input_widths=[4])
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 15)})
    assert audit.step_audit(proc, env, stimuli(range(4))) == []

    # A formula that says one transaction whatever the step did. The run says
    # none on an even stimulus, and that is the difference a buffer cannot hold.
    bent = ir.Procedure(proc.state, proc.inputs, proc.locals, proc.outputs,
                        ir.Seq(proc.body, ir.Assign(proc.count, ir.Lit(1))),
                        count=proc.count, initial=proc.initial,
                        output_vars=proc.output_vars)
    wrong = audit.step_audit(proc, env, stimuli(range(4)),
                             encoding=encode_step(bent, env=env))
    assert any(d.what == "how many transactions" for d in wrong), \
        [(d.what, d.said, d.ran) for d in wrong]


def test_the_model_audit_compares_which_failure():
    """It compared only whether the step failed, and two guards firing on one
    stimulus in different orders halt with different codes, so "both failed"
    passes on a step that failed for the wrong reason.

    Written as its own test because the population cannot exercise it: over 120
    sampled models and their sixteen traces of four steps, no stimulus reaches a
    guard at all, so a comparison added here would never be evaluated by the
    sweep that is supposed to be checking it.
    """
    from behave.sim.behavior import audit, instrumented

    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        return {"y": a // 0}\n')
    proc = lower_with_state_ranges(src, input_widths=[4])
    namespace, _sites, hits = instrumented.load_source(src, "behavior.py")
    model = instrumented._model_class(namespace)

    # Both sides fail, and about the same thing, so there is nothing to report.
    assert audit.model_audit(proc, model, stimuli(range(3)), hits) == []

    # A model whose failure is a different one. The representation halts with
    # DivZero because that is the guard the source's `//` earned; the model is
    # made to raise a KeyError instead, and "both failed" cannot tell them apart.
    class Renamed(model):
        def process(self, a):
            raise KeyError("something else")

    wrong = audit.model_audit(proc, Renamed, stimuli(range(3)), hits)
    assert any(d.what == "which failure" for d in wrong), \
        [(d.what, d.left, d.right) for d in wrong]


def test_a_failed_trace_is_still_a_trace_of_the_length_asked_for():
    """A step that fails ends the run, and what follows it is not another step:
    the state stands still and nothing is produced, for as many steps as were
    asked for. Every verdict about a bound is relative to that, and it was the one
    part of a trace neither audit looked at, because both returned at the failure.

    Three cases, and the third is the one that makes the other two mean anything.
    """
    from behave.sim.behavior import audit
    from behave.sim.symbolic import interp

    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.n = 0\n'
           '    def process(self, a):\n'
           '        self.n = self.n + 1\n'
           '        return {"y": a // (a & 1)}\n')
    proc = lower_with_state_ranges(src, input_widths=[4])

    assert audit.absorbed_suffix(proc, [{"a": 3}, {"a": 3}]) == [], \
        "a trace that does not fail has no suffix to absorb"
    assert audit.absorbed_suffix(proc, [{"a": 3}, {"a": 2}, {"a": 3}]) == [], \
        "and one that does fail absorbs what follows"

    # The check has to be able to fail, and the only way to make it is a reading
    # that does not absorb, since the one in the tree does.
    honest = interp.run_trace

    def greedy(proc_, stimulus, state=None):
        state_after, outputs, hits, error = honest(proc_, stimulus, state)
        if error and len(outputs) > 1:
            outputs = list(outputs)
            outputs[-1] = interp.StepOutput(2, {})
        return state_after, outputs, hits, error

    interp.run_trace = greedy
    try:
        wrong = audit.absorbed_suffix(proc, [{"a": 3}, {"a": 2}, {"a": 3}])
    finally:
        interp.run_trace = honest
    assert any("after the failure produced transactions" in d.what
               for d in wrong), [d.what for d in wrong]
