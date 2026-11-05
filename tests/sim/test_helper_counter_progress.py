"""Counter-effect proofs preserve behavior for indexed-state worklists."""

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('calls', ['single', 'increasing', 'decreasing'])
def test_heap_descent_then_parent_ascent_reads_the_updated_array(lowerer, calls):
    source = '''class M:
    def reset(self): self.keys = [0] * 8
    def take(self, n):
        key = self.keys[n]
        hole = 0
        while 2 * hole + 1 < n:
            child = 2 * hole + 1
            if child + 1 < n:
                if self.keys[child + 1] >= self.keys[child]:
                    child += 1
            self.keys[hole] = self.keys[child]
            hole = child
        while hole > 0:
            parent = (hole - 1) // 2
            if self.keys[parent] >= key:
                break
            self.keys[hole] = self.keys[parent]
            hole = parent
        self.keys[hole] = key
    def process(self, a):
        for i in range(7):
            self.keys[i] = (a >> (4 * i)) & 15
        self.take(6)
        return {'y': self.keys[0] + (self.keys[2] << 4) + (self.keys[5] << 8)}
'''
    if calls == 'increasing':
        source = source.replace('        self.take(6)',
            '        for n in range(1, 7):\n            self.take(n)')
    elif calls == 'decreasing':
        source = source.replace('        self.take(6)',
            '        n = 6\n        while n > 0:\n            self.take(n)\n            n -= 1')
    proc = lower_with_state_ranges(source, input_widths=[28], outputs=['y'],
                                   bound=1, lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    for heap in ((9, 7, 8, 3, 4, 5, 6), (15, 9, 14, 4, 8, 5, 13),
                 (7, 6, 5, 4, 3, 2, 1)):
        value = sum(key << (4 * i) for i, key in enumerate(heap))
        model = namespace['M']()
        model.reset()
        native_hits.clear()
        expected = model.process(value)
        state, outputs, hits, error = interp.run_step(proc, {'a': value})
        assert not error and outputs.transactions == [expected]
        assert list(state['keys']) == model.keys
        assert set(hits) == set(native_hits)


@pytest.mark.parametrize('unproved_reached', [False, True])
def test_joined_loop_proof_may_skip_only_an_unreached_activation(unproved_reached):
    import ast
    from behave.sim.symbolic import ranges
    from behave.sim.behavior.source_sequences import SequencePlan, _ContextSet

    owner = ast.parse('def helper():\n    while pending: pass').body[0]
    loop = owner.body[0]
    first, second = ('first', id(loop)), ('second', id(loop))
    reached = [first, second] if unproved_reached else [first]
    plan = SequencePlan(
        context_functions={'first': owner, 'second': owner},
        statement_contexts=reached,
        loop_spans={first: ranges.Range(0, 5)},
        loop_domains={first: ('finite-progress', None)})
    context = _ContextSet(frozenset(('first', 'second')))
    if unproved_reached:
        assert plan.loop_span(loop, owner, context) is None
        assert plan.loop_domain(loop, owner, context) is None
    else:
        assert plan.loop_span(loop, owner, context) == ranges.Range(0, 5)
        assert plan.loop_domain(loop, owner, context) == ('finite-progress', None)
    assert plan.loop_span(loop, owner, 'second') is None
    assert plan.loop_domain(loop, owner, 'second') is None


def test_visited_admission_supply_is_global_not_renewed_on_every_visit():
    import ast
    from behave.sim.symbolic import ranges
    from behave.sim.behavior.extract import ScopeResolver, source_model
    from behave.sim.behavior.finite_descent import finite_progress_domain
    from behave.sim.behavior.source_sequences import _FunctionContracts, _ScalarBindings

    model = source_model(SOURCE)
    loop = next(stmt for stmt in model.step.body if isinstance(stmt, ast.While))
    resolver = ScopeResolver(model)
    domain = finite_progress_domain(loop, resolver=resolver, owner=model.step,
        contract_of=lambda _: None,
        bounds_of=lambda expr: ranges.Bounds(1, 1) if isinstance(expr, ast.Subscript)
        else ranges.Bounds(0, 0))
    assert domain.maximum == 5  # One initial item plus four possible visited bits.
    _count, limit = domain.head_bounds[0]
    assert limit == ranges.Bounds(hi=5)
    bindings = _ScalarBindings(scalar_bounds={
        'self.counts': ranges.Bounds(0, 100),
        'self.counts[1]': ranges.Bounds(0, 17),
        'self.counts[0]': ranges.Bounds(0, 9),
    })
    planner = _FunctionContracts(model, resolver)
    (_, narrowed), = planner._bounded_loop_heads(model.step, [({}, bindings)], domain.head_bounds)
    assert narrowed.bounds['self.counts[1]'] == ranges.Bounds(0, 5)
    assert narrowed.bounds['self.counts'] == bindings.bounds['self.counts']
    assert narrowed.bounds['self.counts[0]'] == bindings.bounds['self.counts[0]']
    assert bindings.bounds['self.counts[1]'].hi == 17


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_worklist_head_bound_does_not_clip_temporary_helper_values(lowerer):
    source = SOURCE.replace('remaining = self.counts[which] - 1',
                            'self.counts[which] += 16\n'
                            '        remaining = self.counts[which] - 17')
    proc = lower_with_state_ranges(source, input_widths=[8], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    for values in ((0, 255), (27, 228), (85, 170)):
        model = namespace['M']()
        model.reset()
        state = None
        for value in values:
            native_hits.clear()
            expected = model.process(value)
            state, outputs, hits, error = interp.run_step(proc, {'a': value}, state)
            assert not error and outputs.transactions == [expected]
            assert set(hits) == set(native_hits)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_joined_helper_loop_plan_uses_only_activations_that_reach_the_loop(lowerer):
    source = '''class M:
    def reset(self): pass
    def walk(self, n, choose):
        if n > 0:
            i = 0
            while 2 * i + 1 < n:
                child = 2 * i + 1
                if child + 1 < n:
                    if choose: child += 1
                i = child
            return i
        return 0
    def process(self, a):
        result = 0
        for j in range(4):
            result += self.walk(j, a)
        return {'y': result}
'''
    proc = lower_with_state_ranges(source, input_widths=[1], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    for value in (0, 1):
        model = namespace['M']()
        model.reset()
        native_hits.clear()
        expected = model.process(value)
        _, outputs, hits, error = interp.run_step(proc, {'a': value})
        assert not error and outputs.transactions == [expected]
        assert set(hits) == set(native_hits)


SOURCE = '''class M:
    def reset(self): self.counts = [0, 0]
    def put(self, which): self.counts[which] += 1
    def take(self, which):
        remaining = self.counts[which] - 1
        self.counts[which] = remaining
        for i in range(2):
            if i: break
        return 0
    def process(self, a):
        self.counts[0] = 0
        self.counts[1] = 1
        seen = 0
        visits = 0
        while self.counts[1] > 0:
            self.take(1)
            visits += 1
            for j in range(4):
                target = (a >> (2 * j)) & 3
                if ((seen >> target) & 1) == 0:
                    seen |= 1 << target
                    if target != 3:
                        self.put(1)
                    self.put(0)
        while self.counts[0] > (a & 1):
            self.take(0)
        return {'y': visits + self.counts[0] * 16}
'''.replace('while self.counts[0] > (a & 1):',
            'floor = a & 1\n        while self.counts[0] > floor:')


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('forward,keywords', [(False, False), (True, True)])
def test_state_worklist_through_helpers_retains_outputs_state_and_hits(lowerer, forward, keywords):
    source = SOURCE
    if forward:
        source = source.replace('def put(self, which): self.counts[which] += 1',
                                'def put(self, which): self.inner(which)\n'
                                '    def inner(self, which): self.counts[which] += 1')
    if keywords:
        source = source.replace('self.take(1)', 'self.take(which=1)')
        source = source.replace('self.put(1)', 'self.put(which=1)')
    proc = lower_with_state_ranges(source, input_widths=[8], outputs=['y'], bound=2, lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    for a in range(256):
        model = namespace['M']()
        model.reset()
        native_hits.clear()
        expected = model.process(a)
        _, outputs, hits, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(hits) == set(native_hits)


@pytest.mark.parametrize('before,after', [
    ('self.take(1)', 'self.take(a & 1)'),
    ('self.take(1)', 'if a: self.take(1)'),
    ('self.take(1)', 'if a: continue\n            self.take(1)'),
    ('self.counts[which] = remaining', 'if which: return 0\n        self.counts[which] = remaining'),
    ('self.counts[which] = remaining', 'self.counts[which] = remaining + 1'),
    ('self.counts[which] = remaining', 'self.counts[which] = remaining\n        self.counts[1] += 1'),
    ('self.counts[which] = remaining', 'alias = self.counts\n        alias[which] = remaining'),
    ('seen |= 1 << target', 'seen = 1 << target'),
    ('self.put(1)', 'self.put(1)\n                        self.put(1)'),
    ('self.take(0)', 'floor -= 1\n            self.take(0)'),
])
def test_unknown_or_nonterminating_counter_effect_is_not_a_bound(before, after):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(SOURCE.replace(before, after), input_widths=[8], outputs=['y'], bound=2)


GROWTH = '''class M:
    def reset(self): pass
    def process(self, limit, choose):
        i = 0
        total = 0
        while 2 * i + 1 < limit:
            child = 2 * i + 1
            if child + 1 < limit:
                if choose: child += 1
            total += 1
            i = child
        else:
            total += 8
        while i > 0:
            parent = (i - 1) // 2
            i = parent
        return {'y': total + i}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('control', ['normal', 'break', 'return', 'continue'])
def test_affine_guard_with_conditional_growth_and_parent_walk(lowerer, control):
    source = GROWTH
    if control == 'break':
        source = source.replace('i = child', 'i = child\n            if i > 6: break')
    elif control == 'return':
        source = source.replace('i = child', "i = child\n            if i > 6: return {'y': total}")
    elif control == 'continue':
        source = source.replace('i = child', 'i = child\n            if choose: continue\n            total += 2')
    proc = lower_with_state_ranges(source, input_widths=[5, 1], outputs=['y'], lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    for limit in range(32):
        for choose in range(2):
            model = namespace['M']()
            model.reset()
            native_hits.clear()
            expected = model.process(limit, choose)
            _, outputs, hits, error = interp.run_step(proc, {'limit': limit, 'choose': choose})
            assert not error and outputs.transactions == [expected]
            assert set(hits) == set(native_hits)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source,widths', [(SOURCE, [8]), (GROWTH, [3, 1])])
def test_helper_counter_and_growth_queries_have_native_witnesses(source, widths):
    from behave.sim.behavior.instance import BehaviorInstance
    proc = lower_with_state_ranges(source, input_widths=widths, outputs=['y'], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    result = instance.query(instance.goals(), 1)
    assert not result.undecided
    assert result.reachable
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'
