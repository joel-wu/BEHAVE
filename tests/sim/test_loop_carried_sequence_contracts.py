"""Growing-list contracts follow the entered range iteration, not all at once."""

import itertools

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

CASES = [
    ("zero-trip", "range(a)", "i == 0", ""),
    ("ascending", "range(a + 1)", "i == 0", ""),
    ("descending", "range(5, a, -1)", "i == 5", ""),
    ("stride", "range(2, a + 8, 2)", "i == 2", ""),
    ("target-reassigned", "range(a + 1)", "i == 0", "            i = 99\n"),
    ("stop-reassigned", "range(a + 1)", "i == 0", "            a = 0\n"),
]


def source_for(iterable, first, effect):
    return f'''class M:
    def reset(self): self.last = []
    def process(self, a):
        values = []
        for i in {iterable}:
            if {first}:
                value = 7
            else:
                value = values[-1] + 1
{effect}            values.append(value)
        self.last = values
        return {{"y": sum(values)}}
'''


SOURCES = [(name, source_for(iterable, first, effect))
           for name, iterable, first, effect in CASES]
SOURCES.append(("helper-feedback", "def next_value(values):\n    return values[-1] + 1\n\n"
                + source_for("range(a + 1)", "not values", "")
                .replace("values[-1] + 1", "next_value(values)")))


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("name,source", SOURCES, ids=[row[0] for row in SOURCES])
def test_loop_feedback_preserves_every_small_native_history(name, source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=["y"],
                                   bound=2, lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102 -- fixed fixtures
    traces = [[{"a": value} for value in history]
              for history in itertools.product(range(4), repeat=2)]
    assert model_audit(proc, namespace["M"], traces) == [], name


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("name,source", SOURCES, ids=[row[0] for row in SOURCES])
def test_loop_feedback_queries_match_complete_native_goal_hits(name, source):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.source_plan import plan_source

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=["y"], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    namespace, _, hits = instrumented.load_source(
        source, goal_plan=plan_source(source_model(source), source))
    expected = set()
    for history in itertools.product(range(4), repeat=2):
        native = namespace["M"]()
        native.reset()
        for value in history:
            hits.clear()
            native.process(value)
            expected.update(hits)
    result = instance.query(instance.goals(), 2)
    assert not result.undecided
    assert set(result.reachable) == expected, name
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_uses_loop_feedback_after_baseline_miss(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.last = []
    def process(self, a, b):
        values = []
        for i in range((a & 7) + 1):
            if i == 0:
                value = 7
            else:
                value = values[-1] + 1
            values.append(value)
        self.last = values
        if sum(values) == 45:
            return {"y": 45 + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'45 + (b & 1)', b'46 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


def test_helper_cleanup_keeps_caller_state_and_returned_aliases():
    import ast

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.source_values import ValueContract

    low = _Lowerer(())
    low.storage.state_names = {'saved': 'state_saved'}
    low.flow.contracts['caller'] = ValueContract.fixed((2,))
    before = low._fact_name_snapshot()
    names = {'caller', 'formal', 'local', 'result', 'state_saved'}
    group = set(names)
    low.shared = dict.fromkeys(names, group)
    low._discard_inlined_local_facts(before, {'result'})
    assert set(low.shared) == {'caller', 'result', 'state_saved'}
    assert group == set(low.shared)
    with pytest.raises(LoweringError) as caught:
        low._check_not_shared('caller', ast.parse('caller[0] = 1').body[0], 'writing')
    assert caught.value.code == 'array-assigned-by-reference'
