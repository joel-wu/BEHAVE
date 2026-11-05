"""Finite character conversions retain source identity and evaluation order."""

import itertools

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


def model(body):
    return '''TABLE = {"A": 7, "B": 9, "C": 11}
class M:
    TABLE = {"A": 7, "B": 9, "C": 11}
    def reset(self): self.count = 0
    def key(self, a):
        self.count += 1
        return 65 + a
    def default(self):
        self.count += 4
        return 13
    def process(self, a):
''' + '\n'.join('        ' + line for line in body.splitlines()) + '\n'


CASES = [
    ('module-lookup', model('''key = chr(65 + a)
return {"y": TABLE.get(key, 0)}''')),
    ('class-membership', model('''key = chr(65 + a)
if key in self.TABLE:
    return {"y": self.TABLE[key]}
return {"y": 0}''')),
    ('direct-lookup', model('''return {"y": TABLE.get(chr(65 + a), 0)}''')),
    ('conditional-character', model('''key = chr(65 + a) if a & 1 else "A"
return {"y": TABLE.get(key, 0)}''')),
    ('conditional-effects', model('''key = chr(self.key(a)) if a & 1 else chr(self.key(3 - a))
return {"y": ord(key) + 16 * self.count}''')),
    ('conditional-selector-effects', model('''table = {0: (1, 2), 1: (3, 4)}
row = table[self.key(a) & 1] if a & 1 else (5, 6)
return {"y": row[0] + 16 * self.count}''')),
    ('unselected-character', model('''return {"y": a + 1 if True else chr(a)}''')),
    ('guarded-codepoint', model('''point = a - 1
if point >= 0:
    return {"y": ord(chr(point))}
return {"y": 7}''')),
    ('equality', model('''key = chr(65 + a)
return {"y": int(key == "B") + 2 * int(key != "C") + 4 * int(key == 66)}''')),
    ('length-and-truth', model('''key = chr(a)
return {"y": len(key) + int(bool(key))}''')),
    ('unicode-roundtrip', model('''key = chr(0x10FFFC + a)
return {"y": ord(key) - 0x10FFFC}''')),
    ('surrogate-roundtrip', model('''return {"y": ord(chr(0xD800 + a)) - 0xD800}''')),
    ('boolean-codepoint', model('''return {"y": ord(chr(bool(a)))}''')),
    ('captured-key', model('''key = chr(self.key(a))
return {"y": TABLE.get(key, 0) + 16 * self.count}''')),
    ('lookup-default-effect', model('''return {"y": TABLE.get(chr(self.key(a)), self.default()) + 16 * self.count}''')),
    ('rebinding', model('''key = chr(65 + a)
old = ord(key)
key = chr(67 - a)
return {"y": old + ord(key)}''')),
    ('finite-string-ord', model('''key = "\u03bb" if a & 1 else "A"
return {"y": ord(key)}''')),
    ('literal-byte-ord', model('''return {"y": ord(b"A") + a}''')),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_finite_character_values_match_native(lowerer, name, source):
    agrees(source, lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('expression', [
    'chr(a - 1)', 'chr(0x10FFFF + a)', 'chr([a])',
    'ord("AB")', 'ord("")', 'ord(a)', 'ord(b"AB")',
])
def test_unproved_character_domains_are_not_silently_coerced(lowerer, expression):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(model(f'return {{"y": len({expression})}}'),
                                input_widths=[2], outputs=['y'], bound=2,
                                lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name', ['chr', 'ord'])
def test_shadowed_character_builtin_remains_a_helper(lowerer, name):
    source = f'def {name}(a): return a + 7\n' + model(f'return {{"y": {name}(a)}}')
    agrees(source, lowerer, bound=2)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
def test_character_identity_is_not_an_integer_storage_tag(lowerer):
    with pytest.raises(LoweringError, match='no numeric operator semantics'):
        lower_with_state_ranges(model('return {"y": chr(a) + 1}'),
                                input_widths=[2], outputs=['y'], bound=2,
                                lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_character_goal_queries_match_exhaustive_native_hits(name, source):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.source_plan import plan_source

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    expected = set()
    for values in itertools.product(range(4), repeat=2):
        native = namespace['M']()
        native.reset()
        for value in values:
            hits.clear()
            native.process(value)
            expected.update(hits)
    answer = instance.query(instance.goals(), 2)
    assert not answer.undecided
    assert set(answer.reachable) == expected, name
    assert set(instance.goals()) == set(answer.reachable) | set(answer.unreachable)
    if answer.reachable:
        assert instance.source_audit(tuple(answer.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_finds_character_lookup_branch(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    TABLE = {"%": 5, "!": 7}
    def reset(self): pass
    def process(self, a, b):
        key = chr(a)
        if key in self.TABLE:
            return {"y": self.TABLE[key] + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'{"%": 5, "!": 7}', b'{"%": 4, "!": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)
