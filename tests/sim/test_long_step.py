"""One long step, through every reader of it.

The same fault has been found eight times, once per reader, each time by a
different model in the population: a walk over a term that recurses once per
command. A sequence is nested one node per command, so the depth of such a walk
follows the length of the unrolled step and not the nesting of the source, and a
step of a few thousand commands is ordinary here. Four of ours had to become
loops, then the encoder, then the range analysis, then the pickler the queries
send the step through, then the printing used to debug it.

Eight discoveries is seven too many. What was missing is not another fix but the
statement that the property belongs to every reader: a step longer than the
interpreter's stack must be readable by all of them. So each reader gets a line
here, and a ninth reader added later fails this test rather than a model.

The recursion limit is deliberately not raised. Raising it is what would hide
the next one, and the point of the file is that there is no next one to hide.
"""
import pickle

import pytest

from behave.sim.symbolic import cfg, interp, ir, ranges, ssa
from behave.sim.behavior import instance as behavior_instance
from behave.sim.symbolic.encoder import encode_step
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import lower_procedure

#: Past the default recursion limit of a thousand, and past it by enough that a
#: walk which recurses per command cannot get through on slack.
LENGTH = 1500


def _source():
    body = ["z = 0"]
    body += [f"z = z + {n % 7}" for n in range(LENGTH)]
    body += ['return {"y": z}']
    return ("class M:\n"
            "    def reset(self): pass\n"
            "    def process(self, a):\n"
            + "".join(f"        {line}\n" for line in body))


@pytest.fixture(scope="module")
def long_step():
    """The step, lowered both ways, since both produce a sequence this long."""
    source = _source()
    return source, lower_procedure(source), \
        lower_procedure(source, lowerer=GraphLowerer)


def _both(long_step):
    _, term, graph = long_step
    return (("term", term), ("graph", graph))


def test_the_lowering_produces_it_at_all(long_step):
    for which, proc in _both(long_step):
        assert sum(1 for _ in ir.walk(proc.body)) > LENGTH, which


def test_the_interpreter_runs_it(long_step):
    wanted = sum(n % 7 for n in range(LENGTH))
    for which, proc in _both(long_step):
        _, out, _, error = interp.run_step(proc, {"a": 1})
        assert not error, which
        assert out.transactions == [{"y": wanted}], which


def test_the_well_formedness_check_reads_it(long_step):
    for which, proc in _both(long_step):
        assert ir.normalized(proc) == [], which


def test_the_range_analysis_reads_it(long_step):
    for which, proc in _both(long_step):
        env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
        assert env["z"] is not None, which


def test_the_encoder_reads_it(long_step):
    for which, proc in _both(long_step):
        env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
        assert encode_step(proc, env=env) is not None, which


def test_it_survives_being_sent_to_a_worker(long_step):
    """The queries run in a pool, so the step is pickled. This was found in the
    pickler rather than in our own code, which is why the file is about readers
    and not about walks we wrote."""
    for which, proc in _both(long_step):
        back = pickle.loads(pickle.dumps(proc.body))
        assert sum(1 for _ in ir.walk(back)) == \
            sum(1 for _ in ir.walk(proc.body)), which


def test_it_can_be_printed(long_step):
    """Printing a step is what one does while working out why it is wrong, so a
    representation that raises on the long ones raises exactly when wanted."""
    for which, proc in _both(long_step):
        assert len(repr(proc.body)) > LENGTH, which


def test_the_graph_can_be_read_off_it_and_flattened_back(long_step):
    for which, proc in _both(long_step):
        graph = cfg.of_command(proc.body)
        assert len(graph.order()) > 1, which
        assert graph.dominators() and graph.post_dominators(), which
        assert ssa.undefined_uses(graph, set(proc.inputs)) == [], which
        assert cfg.if_convert(graph) is not None, which


def test_the_instance_answers_about_it(long_step):
    """The certificate's own questions, which is the reader that matters most:
    a step this side cannot read is a step it cannot say anything about."""
    for which, proc in _both(long_step):
        env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
        inst = behavior_instance.BehaviorInstance(proc, env)
        assert inst.well_formed() == [], which
        assert inst.may_read_unassigned() == [], which
