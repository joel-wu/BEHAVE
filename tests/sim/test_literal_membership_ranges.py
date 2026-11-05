"""Literal finite domains reuse exact interval membership without dropping goals."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError, lower_with_state_ranges
from tests.sim.test_behavior_collection_membership import _compare


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('values', ['(0, 1, 2, 3, 4, 5, 6, 7)', '[0, 1, 2, 3, 4, 5, 6, 7]'])
@pytest.mark.parametrize('binding', ['', '        a = int(a)\n', '        a = a + 0\n'])
def test_literal_domain_exposes_the_same_proof_as_a_closed_table(lowerer, values, binding):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
{binding}        if a not in {values}: raise ValueError("outside the input domain")
        return {{"y": a}}
'''
    _compare(source, lowerer=lowerer)
    # Domain widening makes the exception reachable. It must not be erased.
    with pytest.raises(LoweringError, match='statement Raise'):
        lower_with_state_ranges(source, input_widths=[4], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('values', ['()', '(False, True, 1, 4, 4)', '(-3, -2, 0, 2, 3)', '[0, 2, 7]'])
def test_literals_keep_boolean_equality_gaps_negatives_and_duplicates(lowerer, values):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        return {{"y": int(a in {values}) + 2 * int(a - 3 not in {values})}}
'''
    _compare(source, lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_nonconstant_elements_keep_runtime_effects_and_evaluation_order(lowerer):
    source = '''class M:
    def reset(self): self.counter = 0
    def next(self):
        previous = self.counter
        self.counter += 1
        return previous
    def process(self, a):
        self.counter = a
        found = self.next() in (self.next(), self.next())
        return {"y": int(found) + 4 * self.counter}
'''
    _compare(source, lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
def test_literal_domain_queries_keep_unreachable_exception_goals_and_native_hits():
    from behave.sim.behavior.instance import BehaviorInstance

    source = '''class M:
    def reset(self): pass
    def process(self, a):
        if a not in (0, 1): raise ValueError("domain")
        if a in (1,): return {"y": 3}
        return {"y": 0}
'''
    proc = lower_with_state_ranges(source, input_widths=[1], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    instance.bind(2)
    goals = instance.goals()
    result = instance.query(goals, 2)
    assert result.reachable and result.unreachable and not result.undecided
    assert set(goals) == set(result.reachable) | set(result.unreachable)
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_literal_membership_public_search_replays_the_new_hit(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        if a in (37, 193):
            return {"y": 7 + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'7 + (b & 1)', b'6 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)
