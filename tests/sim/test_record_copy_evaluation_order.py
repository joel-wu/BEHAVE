"""Record-copy queries retain evaluation order and native event occurrences."""

import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source


def _source(body):
    return '''class M:
    def reset(self):
        self.regs = {"x": 1, "y": 2}
        self.count = 0
    def make(self, a):
        self.count = (self.count + 1) & 15
        return {"x": a + self.count, "y": self.count}
    def change(self, a):
        self.regs = {"x": a, "y": a + 1}
        return a
    def process(self, a):
''' + '\n'.join('        ' + line for line in body.splitlines()) + '\n'


CASES = [
    ("copy-before-later-call", _source('''saved = dict(self.regs)
return {"y": saved["x"] * 32 + self.change(a) + saved["y"]}''')),
    ("copy-in-operand", _source('''return {"y": dict(self.regs)["x"] * 32 + self.change(a)}''')),
    ("two-effectful-copies", _source('''left = dict(self.make(a))
right = dict(self.make(a))
return {"y": left["x"] * 32 + right["x"] + self.count}''')),
    ("conditional-call", _source('''saved = dict(self.make(a)) if a else dict(x=0, y=0)
return {"y": saved["x"] * 32 + self.count}''')),
    ("independent-outer-copies", _source('''original = {"x": a}
first = dict(original)
second = dict(first)
original["x"] += 1
first["x"] += 2
second["x"] += 3
return {"y": original["x"] * 64 + first["x"] * 8 + second["x"]}''')),
    ("immutable-tuple-child", _source('''original = {"pair": (a, a + 1)}
saved = dict(original)
original["pair"] = (a + 2, a + 3)
return {"y": saved["pair"][0] * 8 + saved["pair"][1]}''')),
    ("mutable-duplicate-key-effects", _source('''raw = {"x": self.make(a)["x"], "x": self.make(a)["x"]}
raw["y"] = self.count
return {"y": raw["x"] * 32 + raw["y"]}''')),
    ("mutable-computed-initializer", _source('''raw = dict(self.make(a))
raw["x"] += self.count
raw["y"] = raw["x"] + raw["y"]
return {"y": raw["x"] * 32 + raw["y"]}''')),
    ("mutable-reinitialization", _source('''raw = {"x": a, "y": a + 1}
for i in range(2):
    raw = {"x": raw["y"], "y": raw["x"]}
    raw["x"] += i
return {"y": raw["x"] * 32 + raw["y"]}''')),
    ("immutable-rebinding", _source('''raw = {"x": a, "y": a + 1}
raw = {"x": raw["y"], "y": raw["x"]}
return {"y": raw["x"] * 32 + raw["y"]}''')),
    ("immutable-nested-rebinding", _source('''raw = {"pair": (a, a + 1), "nested": {"x": a + 2}}
raw = {"pair": (raw["nested"]["x"], raw["pair"][0]), "nested": {"x": raw["pair"][1]}}
return {"y": raw["pair"][0] * 32 + raw["pair"][1] * 4 + raw["nested"]["x"]}''')),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('source,code,line', [
    (_source('''raw = self.make(a)
raw["x"] += self.count
raw["y"] = raw["x"] + raw["y"]
return {"y": raw["x"] * 32 + raw["y"]}'''), 'record-mutation-unsupported', 13),
    (_source('''raw = {"x": a, "y": a + 1}
for i in range(2):
    raw = {"x": a + i}
    raw["y"] = raw["x"] + 1
return {"y": raw["x"] * 32 + raw["y"]}'''), 'helper-return-shape-disagrees', 16),
], ids=['uncopied-helper-result', 'loop-schema-change'])
def test_record_binding_cleanup_keeps_existing_source_refusals(lowerer, source, code, line):
    # Confirmed with retained pre-cleanup _set_record_value. Extending source
    # support is separate from deduplicating its storage writeback.
    with pytest.raises(LoweringError) as error:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2, lowerer=lowerer)
    assert error.value.code == code and error.value.node.lineno == line


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_record_copy_output_and_events_match_each_native_step(lowerer, name, source):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=["y"],
                                   bound=2, lowerer=lowerer)
    plan = plan_source(source_model(source), source)
    observed, _, hits = instrumented.load_source(source, goal_plan=plan)
    plain = {}
    exec(compile(source, "behavior.py", "exec"), plain)  # noqa: S102 -- fixed test fixtures
    for values in itertools.product(range(4), repeat=2):
        native, reference = observed["M"](), plain["M"]()
        native.reset()
        reference.reset()
        state = None
        for value in values:
            hits.clear()
            expected = reference.process(value)
            assert native.process(value) == expected
            state, output, actual_hits, error = interp.run_step(proc, {"a": value}, state)
            assert not error
            assert output.transactions == [expected], (name, values, value)
            assert actual_hits == set(hits), (name, values, value)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_copy_goal_queries_equal_exhaustive_two_call_native_oracle(name, source):
    from behave.sim.behavior.instance import BehaviorInstance

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
    assert not result.undecided, (name, result.as_dict())
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert set(result.reachable) == expected, name
    audit = instance.source_audit(tuple(result.reachable.values()))
    assert audit.status.value == "agreed", audit.as_dict()
