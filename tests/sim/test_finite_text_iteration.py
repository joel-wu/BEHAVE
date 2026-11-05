"""Closed text iterators keep their Python values, lengths and snapshot."""

import itertools

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


def model(body):
    return '''TABLE = {0: "", 1: ".-", 2: "--."}
class M:
    TABLE = {0: "", 1: ".-", 2: "--."}
    KEYS = {"AA": 1, "B": 2}
    def reset(self): self.count = 0
    def key(self, a):
        self.count += 1
        return a
    def process(self, a):
''' + '\n'.join('        ' + line for line in body.splitlines()) + '\n'


CASES = [
    ('optional-table', model('''text = self.TABLE.get(a)
if text is None: return {"y": 15}
y = 0
for char in text:
    y = (y << 1) | int(char == "-")
return {"y": y + 16 * len(text)}''')),
    ('captured-key', model('''text = TABLE.get(self.key(a), "!")
y = 0
for char in text:
    y += ord(char)
return {"y": y + 16 * self.count}''')),
    ('literal-unicode', model('''y = a
for char in "A\u03bb":
    y += ord(char)
return {"y": y}''')),
    ('bytes-integers', model('''y = a
for byte in b"\\x01\\xff":
    y += byte
return {"y": y}''')),
    ('reversed', model('''text = TABLE.get(a, "!")
y = 0
for char in reversed(text):
    y = y * 4 + int(char == "-") + 2 * int(char == "!")
return {"y": y}''')),
    ('enumerate', model('''text = TABLE.get(a, "!")
y = 0
for i, char in enumerate(text, 1):
    y += i * ord(char)
return {"y": y}''')),
    ('zip', model('''text = TABLE.get(a, "!")
y = 0
for char, other in zip(text, "ab"):
    y += ord(char) + ord(other)
return {"y": y}''')),
    ('held-enumerate-pair', model('''text = TABLE.get(a, "!")
y = 0
for pair in enumerate(text):
    y += pair[0] + ord(pair[1])
return {"y": y}''')),
    ('last-character', model('''char = "Q"
text = TABLE.get(a, "!")
for char in text:
    pass
return {"y": ord(char)}''')),
    ('nested-characters', model('''text = TABLE.get(a, "!")
y = 0
for char in text:
    for other in "A\u03bb":
        y += ord(char) + ord(other)
return {"y": y}''')),
    ('empty-text', model('''y = a
for char in "":
    y += ord(char)
return {"y": y}''')),
    ('break-continue-else', model('''text = TABLE.get(a, "!")
y = 0
for char in text:
    if char == "!": break
    if char == ".": continue
    y += 1
else:
    y += 8
return {"y": y}''')),
    ('rebind-iterable', model('''text = TABLE.get(a, "!")
y = 0
for char in text:
    text = "Q"
    y += ord(char)
return {"y": y}''')),
    ('conditional', model('''text = "A" if a & 1 else "\u03bbB"
y = 0
for char in text:
    y += ord(char)
return {"y": y + len(text)}''')),
    ('string-tuple', model('''y = a
for text in ("AA", "B"):
    y += len(text)
return {"y": y}''')),
    ('integer-mapping-keys', model('''y = a
for key in self.TABLE:
    y += key
return {"y": y}''')),
    ('string-mapping-keys', model('''y = a
for key in self.KEYS:
    y += len(key)
return {"y": y}''')),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_text_iteration_matches_native(lowerer, name, source):
    agrees(source, lowerer, bound=2)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_text_iteration_queries_match_all_native_hits(name, source):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.source_plan import plan_source

    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    namespace, _, hits = instrumented.load_source(
        source, goal_plan=plan_source(source_model(source), source))
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


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('body', [
    '''for char in TABLE.get(a, "!"):
    pass
return {"y": ord(char)}''',
    '''y = 0
for char in "AB":
    y += char
return {"y": y}''',
])
def test_text_tags_and_unbound_targets_are_not_numeric_values(lowerer, body):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(model(body), input_widths=[2], outputs=['y'],
                                bound=2, lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_closes_text_iteration_goal(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    TABLE = {37: "-.", 33: "--"}
    def reset(self): pass
    def process(self, a, b):
        text = self.TABLE.get(a, "")
        y = 0
        for char in text:
            y = y * 2 + int(char == "-")
        if y:
            return {"y": y + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    # Keep the target branch unchanged: the shared helper checks that the
    # candidate, as well as the reference, hit that branch before comparing.
    candidate = source.replace(b'y + (b & 1)', b'y + 1 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)
