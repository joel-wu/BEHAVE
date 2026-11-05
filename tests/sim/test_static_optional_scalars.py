"""Finite optional lookup tags disappear only after presence is established."""

import itertools

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


def source_for(body, *, table="{1: 2, 3: 4}"):
    return f'''class M:
    TABLE = {table}
    def reset(self): self.count = 0
    def key(self, a):
        self.count = (self.count + 1) & 3
        return a
    def process(self, a):
''' + '\n'.join('        ' + line for line in body.splitlines()) + '\n'


CASES = [
    ("guarded-shift", source_for('''value = self.TABLE.get(a)
if value is not None: return {"y": (1 << value) - 1}
return {"y": 0}''')),
    ("early-none-return", source_for('''value = self.TABLE.get(a)
if value is None: return {"y": 7}
return {"y": 3 * value}''')),
    ("conditional-expression", source_for('''value = self.TABLE.get(a)
return {"y": value + 1 if value is not None else 0}''')),
    ("short-circuit", source_for('''value = self.TABLE.get(a)
if value is not None and value > 2: return {"y": value}
return {"y": 0}''')),
    ("short-circuit-or", source_for('''value = self.TABLE.get(a)
if value is None or value < 3: return {"y": 0}
return {"y": value + 1}''')),
    ("negated-none", source_for('''value = self.TABLE.get(a)
if not (value is None): return {"y": value + 1}
return {"y": 0}''')),
    ("key-evaluated-once", source_for('''value = self.TABLE.get(self.key(a))
if value is not None: return {"y": value * 4 + self.count}
return {"y": self.count}''')),
    ("negative-and-zero", source_for('''value = self.TABLE.get(a)
return {"y": 9 + value if value is not None else 0}''', table="{0: -3, 2: 0}")),
    ("boolean-is-not-none", source_for('''value = self.TABLE.get(a)
return {"y": int(value) + 2 if value is not None else 0}''', table="{0: False, 2: True}")),
]


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_guarded_static_scalar_matches_native(lowerer, name, source):
    agrees(source, lowerer, bound=2)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("body", [
    '''value = self.TABLE.get(a)
return {"y": value + 1}''',
    '''value = self.TABLE.get(a)
if value is not None:
    value = self.TABLE.get(a ^ 1)
    return {"y": value + 1}
return {"y": 0}''',
    '''value = self.TABLE.get(a)
valid = value is not None and value > 2
return {"y": value + int(valid)}''',
])
def test_absence_and_stale_guard_are_not_invented_as_numeric_zero(lowerer, body):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source_for(body), input_widths=[2], outputs=["y"],
                                bound=2, lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_all_guarded_lookup_goals_match_native_oracle(name, source):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.source_plan import plan_source

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=["y"], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    instance.bind(2)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    expected = set()
    for values in itertools.product(range(4), repeat=2):
        model = namespace["M"]()
        model.reset()
        for value in values:
            hits.clear()
            model.process(value)
            expected.update(hits)
    result = instance.query(instance.goals(), 2)
    assert not result.undecided, result.as_dict()
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert set(result.reachable) == expected, name
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("wrong", [False, True])
def test_public_search_queries_present_lookup_then_confirms_new_hit(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    TABLE = {37: 5, 193: 7}
    def reset(self): pass
    def process(self, a, b):
        value = self.TABLE.get(a)
        if value is not None:
            return {"y": value + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b"{37: 5, 193: 7}", b"{37: 4, 193: 6}") if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)
