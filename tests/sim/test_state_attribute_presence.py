"""A source attribute can be absent between calls, not silently equal to zero."""

import itertools
import pickle

import pytest

from behave.sim.behavior import audit
from behave.sim.symbolic import interp, ir
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_values import NONE, SCALAR, ValueContract
from behave.sim.symbolic.state_projection import (
    ABSENT_STATE,
    SourceProductBinding,
    StatePresenceBinding,
    StateProjectionError,
    StateVariableBinding,
)

CASES = [
    ('conditional-write', '''class M:
    def reset(self): pass
    def process(self, a):
        if a: self.x = a
        return {"y": a}
'''),
    ('two-call-register', '''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if self.ready:
            self.ready = 0
            return {"y": self.x}
        if a:
            self.x = a
            self.ready = 1
        return {"y": 0}
'''),
    ('helper-created', '''class M:
    def reset(self): self.ready = 0
    def put(self, a): self.x = a; self.ready = 1
    def get(self): return self.x
    def process(self, a):
        if a: self.put(a)
        if self.ready: return {"y": self.get()}
        return {"y": 0}
'''),
    ('chained', '''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if a: self.x = self.z = a; self.ready = 1
        if self.ready: return {"y": self.x + self.z}
        return {"y": 0}
'''),
    ('unpack', '''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if a: self.x, self.z = a, a + 1; self.ready = 1
        if self.ready: return {"y": self.x + self.z}
        return {"y": 0}
'''),
    ('array', '''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if a: self.x = [a, a + 1]; self.ready = 1
        if self.ready: return {"y": self.x[a & 1]}
        return {"y": 0}
'''),
    ('early-exit', '''class M:
    def reset(self): pass
    def process(self, a):
        if a: return {"y": a}
        self.x = a
        return {"y": self.x}
'''),
    ('optional-field', '''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if a: self.x = None if a == 1 else a; self.ready = 1
        if self.ready and self.x is not None: return {"y": self.x}
        return {"y": 0}
'''),
    ('record-field', '''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if a: self.x = {"left": a, "right": a + 1}; self.ready = 1
        if self.ready: return {"y": self.x["left"] + self.x["right"]}
        return {"y": 0}
'''),
    ('boolean-field', '''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if a: self.x = a == 1; self.ready = 1
        if self.ready: return {"y": int(self.x)}
        return {"y": 0}
'''),
    ('delayed-copy', '''class M:
    def reset(self): self.phase = 0; self.result = 0
    def process(self, a):
        if self.phase == 0:
            if a: self.phase = 1
        elif self.phase == 1:
            self.x = a + 1
            self.phase = 2
        else:
            self.result = self.x
            self.phase = 0
        return {"y": self.result}
'''),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_native_state_presence_over_all_three_call_histories(name, source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=3)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    traces = [[{'a': a} for a in values]
              for values in itertools.product(range(4), repeat=3)]
    assert audit.model_audit(proc, namespace['M'], traces) == [], name
    projection = proc.contract.projection('x')
    assert isinstance(projection, StatePresenceBinding)
    assert projection.reconstruct(proc.initial) is ABSENT_STATE
    assert not ir.normalized(proc), ir.normalized(proc)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('read', ['self.x', 'self.x + a'])
def test_actual_read_before_creation_is_a_source_failure(lowerer, read):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        if a: self.x = a
        return {{"y": {read}}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=3)
    assert interp.run_step(proc, {'a': 0})[-1] == 'AttributeError'
    assert interp.run_step(proc, {'a': 1})[-1] == ''
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    assert audit.model_audit(proc, namespace['M'],
                             [[{'a': 0}], [{'a': 1}, {'a': 0}]]) == []


def test_presence_projection_distinguishes_absent_none_and_zero():
    scalar = StatePresenceBinding(StateVariableBinding('x', 'value'), 'exists')
    assert scalar.reconstruct({'exists': False, 'value': 91}) is ABSENT_STATE
    assert scalar.reconstruct({'exists': True, 'value': 0}) == 0
    for state in ({'value': 0}, {'exists': 0, 'value': 0}, {'exists': True}):
        with pytest.raises(StateProjectionError):
            scalar.reconstruct(state)
    option = ValueContract.option(SCALAR)
    inner = SourceProductBinding('x', option,
                                 ((('present',), 'tag'), (('payload',), 'value')))
    binding = StatePresenceBinding(inner, 'exists')
    assert binding.reconstruct({'exists': True, 'tag': False, 'value': 17}) is None
    assert binding.reconstruct({'exists': False, 'tag': True, 'value': 0}) is ABSENT_STATE
    contract = ir.StateContract(('x',), {'x': ('state',)}, (binding,))
    restored = pickle.loads(pickle.dumps(contract))
    assert restored == contract
    assert restored.projection('x').reconstruct({'exists': False}) is ABSENT_STATE
    none = StatePresenceBinding(SourceProductBinding('x', NONE, ()), 'exists')
    assert none.reconstruct({'exists': True}) is None


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,source', CASES, ids=[name for name, _ in CASES])
def test_queries_replay_original_attribute_lifetimes(name, source):
    from behave.sim.behavior.instance import BehaviorInstance
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=3)
    result = instance.query(instance.goals(), 3)
    assert result.reachable and not result.undecided, name
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
def test_native_attribute_error_has_its_exact_source_goal():
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.behavior.source_plan import plan_source
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        if a: self.x = a
        self.x += 1
        return {"y": self.x}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=3)
    plan = plan_source(source_model(source), source)
    result = instance.query(instance.goals(), 3)
    assert not result.undecided
    site = next(site.id for site in plan.safety_sites
                if site.kind == 'attributeerror' and site.line == 5)
    assert site in result.reachable
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('read', [
    'int(self.x is None)', 'int(self.x is not None)',
    'int(self.x == None)', 'int(self.x != None)',
])
def test_optional_value_test_still_reads_the_attribute(lowerer, read):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        if a: self.x = None if a == 1 else a
        return {{"y": {read}}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   bound=3, lowerer=lowerer)
    assert interp.run_step(proc, {'a': 0})[-1] == 'AttributeError'
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    assert audit.model_audit(proc, namespace['M'], [
        [{'a': 0}], [{'a': 1}, {'a': 0}], [{'a': 2}, {'a': 0}]]) == []


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_replays_late_attribute_creation(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop
    source = b'''class M:
    def reset(self): pass
    def process(self, a, b):
        if a == 37:
            self.x = a + b
            return {"y": self.x}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'self.x = a + b', b'self.x = a + b + 1') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('value', ['a', '[a, a + 1]',
                                  'None if a == 1 else a', '{"v": a}'])
def test_reset_preserves_late_created_attribute(lowerer, value):
    source = f'''class M:
    def reset(self): self.ready = 0
    def process(self, a):
        if a: self.x = {value}; self.ready = 1
        return {{"y": self.ready}}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=3)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    traces = [[{'a': a}, {'a': 0}, {'a': b}]
              for a, b in itertools.product(range(4), repeat=2)]
    assert audit.model_audit(proc, namespace['M'], traces,
                             reset_indices=((1,),) * len(traces)) == []


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_late_attribute_is_read_after_reset_without_reconstruction(lowerer):
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        if a: self.x = a
        return {"y": self.x}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=3)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    assert audit.model_audit(proc, namespace['M'], [
        [{'a': 1}, {'a': 0}, {'a': 0}], [{'a': 0}]],
        reset_indices=((1,), (0,))) == []


def test_state_created_by_later_reset_needs_explicit_transition():
    source = '''class M:
    def __init__(self): self.armed = 0
    def reset(self):
        if self.armed: self.x = 7
    def process(self, a):
        self.armed = 1
        if a: self.x = a
        return {"y": a}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    assert caught.value.code == 'state-conditional-reset-unsupported'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_class_fallback_needs_more_than_instance_attribute_presence(lowerer):
    source = '''class M:
    x = 2
    def reset(self): pass
    def process(self, a):
        if a: self.x = a
        return {"y": self.x}
'''
    # Native Python returns 2 before the first instance write. The current
    # presence representation has no class-fallback carrier; report that
    # limitation instead of manufacturing an AttributeError witness.
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                lowerer=lowerer, bound=3)
    assert caught.value.code == 'state-class-fallback-unsupported'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_unconditional_write_keeps_class_fallback_dead(lowerer):
    source = '''class M:
    x = 2
    def reset(self): pass
    def process(self, a):
        self.x = a
        return {"y": self.x}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=3)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    assert audit.model_audit(proc, namespace['M'], [[{'a': a} for a in range(4)]]) == []
