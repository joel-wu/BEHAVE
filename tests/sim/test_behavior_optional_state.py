"""Persistent source options retain presence, payload and history."""

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


SOURCES = [
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        if self.pending is None:
            self.pending = a
            return None
        y = self.pending * 16 + a
        self.pending = None
        return {"y": y}
''',
    '''class M:
    def reset(self): self.a = None
    def process(self, a):
        old = 0 if self.a is None else self.a + 1
        self.a = a if a != 7 else None
        return {"y": old}
''',
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        if self.pending is None:
            self.pending = (a, a + 1)
            return {"y": 0}
        old = self.pending[0] + self.pending[1]
        self.pending = None
        return {"y": old}
''',
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        if self.pending is None:
            self.pending = {"x": a, "y": a + 1}
            return {"y": 0}
        old = self.pending["x"] + self.pending["y"]
        self.pending = None
        return {"y": old}
''',
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        self.pending = None
        return {"y": a if self.pending is None else 0}
''',
    '''class M:
    def reset(self): self.pending = None
    def update(self, a):
        if self.pending is not None and self.pending < a:
            self.pending = None
        else:
            self.pending = a
    def process(self, a):
        self.update(a)
        return {"y": 0 if self.pending is None else self.pending + 1}
''',
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        old, self.pending = self.pending, a
        return {"y": 0 if old is None else old + 1}
''',
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        self.pending = a
        y = self.pending + 1
        self.pending = None
        return {"y": y}
''',
    '''class M:
    def reset(self): self.pending = 5
    def process(self, a):
        old = 0 if self.pending is None else self.pending
        self.pending = a if a != 7 else None
        return {"y": old}
''',
    '''class M:
    def reset(self): self.pending = None
    def fill(self, a):
        self.pending = a
        return a
    def clear(self):
        self.pending = None
        return 0
    def process(self, a):
        unused = self.fill(a) if a else self.clear()
        return {"y": 0 if self.pending is None else self.pending + 1}
''',
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        if self.pending is None:
            self.pending = [a, a + 1]
        self.pending[0] = a + 2
        return {"y": self.pending[0] + self.pending[1]}
''',
    '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        if self.pending is None:
            self.pending = a
        self.pending += 1
        return {"y": self.pending}
''',
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_persistent_option_matches_native_across_calls(source, lowerer):
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer, bound=4)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    native = namespace["M"]()
    native.reset()
    state = None
    trace = [{"a": a} for a in (0, 3, 7, 1)]
    for inputs in trace:
        state, output, _, error = interp.run_step(proc, inputs, state)
        assert error == ""
        expected = native.process(**inputs)
        assert output.transactions == ([] if expected is None else [expected])
    assert model_audit(proc, namespace["M"], [trace]) == []


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("source", SOURCES)
def test_persistent_option_goal_queries_have_native_witnesses(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert not result.undecided
    assert result.reachable
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


def test_product_projection_preserves_presence_and_sequence_carrier():
    from behave.sim.behavior.source_values import SCALAR, ValueContract
    from behave.sim.symbolic.state_projection import SourceProductBinding, StateProjectionError

    binding = SourceProductBinding("pending", ValueContract.option(
        ValueContract.fixed((2,), "tuple")), (
            (("present",), "tag"), (("payload",), "value")))
    assert binding.solver_values(None) == {"tag": False, "value": [0, 0]}
    assert binding.reconstruct({"tag": False}) is None
    assert binding.reconstruct(binding.solver_values((0, 3))) == (0, 3)
    with pytest.raises(StateProjectionError):
        binding.reconstruct({"tag": 1, "value": [0, 3]})
    scalar = SourceProductBinding("pending", ValueContract.option(SCALAR), (
        (("present",), "tag"), (("payload",), "value")))
    assert scalar.reconstruct(scalar.solver_values(0)) == 0
    assert scalar.reconstruct(scalar.solver_values(0)) is not None


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_mutable_optional_payload_alias_is_not_copied_silently(lowerer):
    from behave.sim.behavior.lowering import LoweringError

    source = '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        if self.pending is None:
            self.pending = [a, a + 1]
        saved = self.pending
        saved[0] = a + 2
        return {"y": self.pending[0]}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], bound=4, lowerer=lowerer)
    assert caught.value.code == "array-assigned-by-reference"


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_boolean_payload_does_not_silently_acquire_integer_semantics(lowerer):
    from behave.sim.behavior.lowering import LoweringError

    source = '''class M:
    def reset(self): self.pending = None
    def process(self, a):
        if self.pending is None:
            self.pending = a != 0
            return {"y": 0}
        out = int(self.pending)
        self.pending = None
        return {"y": out}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], bound=4, lowerer=lowerer)
    assert caught.value.code == "type-inconsistent"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("wrong", [False, True])
def test_optional_state_public_search_replays_only_new_input(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.pending = None
    def process(self, a, b):
        self.pending = a if a == 37 else None
        if self.pending is not None:
            return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        left = slot["inputs"]["left"]["a"]
        right = slot["inputs"]["right"]["b"]
        return {"result": self.process(left, right)}
'''
    candidate = source.replace(b'{"y": 7}', b'{"y": 6}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("bound", [1, 4])
@pytest.mark.parametrize("width, extra, suffix", [(3, False, 1), (8, False, 1),
                                                (8, True, 2)])
def test_helper_built_sequence_retains_final_element(lowerer, bound, width, extra, suffix):
    source = f'''class M:
    def __init__(self):
        self.width = {width}
        self.extra = {int(extra)}
        self.suffix = {suffix}
    def reset(self): self.pending = None
    def build(self, a):
        bits = [0]
        for k in range(self.width):
            bits.append((a >> k) & 1)
        if self.extra != 0:
            bits.append(a & 1)
        bits = bits + [1] * self.suffix
        return bits
    def process(self, a):
        self.pending = self.build(a)
        return {{"y": self.pending[-1]}}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"],
                                   lowerer=lowerer, bound=bound)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    traces = [[{"a": value}] for value in (0, 3, 7)]
    assert model_audit(proc, namespace["M"], traces) == []
