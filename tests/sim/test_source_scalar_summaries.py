"""Only identical state-independent scalar transfers cross planning layers."""

import pytest

from behave.sim.symbolic import ranges
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.source_sequences import (
    _FunctionContracts, _SourceQueryCache, _Spec,
    _for_body_has_stable_contract_transfer, _static_value, plan_sequences,
)
from behave.sim.behavior.source_values import SCALAR, SourceValueBudget


@pytest.mark.parametrize('expression', (
    '(a + 1) * (b + 2)', 'a if b > 0 else -a',
    '(a // b) if b != 0 else 0', 'self.cells[i] + (a & 7)',
    '(a and b) or (a + 1)',
))
def test_numeric_queries_reuse_static_facts_without_reusing_old_ranges(expression):
    from behave.sim.behavior.source_sequences import _ScalarBindings, _scalar_bounds

    model = source_model('class M:\n    def process(self, a, b, i):\n'
                         '        return {"y": ' + expression + '}\n')
    resolver = ScopeResolver(model)
    planner = _FunctionContracts(model, resolver)
    node = model.step.body[0].value.values[0]
    for index in (0, 1, 0):
        for upper in (3, 9, 2):
            bindings = _ScalarBindings({'i': index}, scalar_bounds={
                'a': ranges.Bounds(-upper, upper), 'b': ranges.Bounds(-1, 2),
                'self.cells': ranges.Bounds(-10, 20),
                'self.cells[0]': ranges.Bounds(1, 2),
                'self.cells[1]': ranges.Bounds(10, 20),
            })
            expected = _scalar_bounds(node, resolver.owner_of(model.step), bindings,
                                      budget=SourceValueBudget())
            assert planner._bounds_of(model.step, node, bindings) == expected


def test_numeric_transfer_does_not_recompute_static_subtrees_for_new_intervals(monkeypatch):
    import behave.sim.behavior.source_sequences as sequences

    model = source_model('class M:\n    def process(self, a, b):\n'
                         '        return {"y": (a + 1) * (b + 2)}\n')
    planner = _FunctionContracts(model, ScopeResolver(model))
    node = model.step.body[0].value.values[0]
    visited = []
    original = sequences._static_value

    def measured(*args, **kwargs):
        visited.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(sequences, '_static_value', measured)
    for upper in (3, 9):
        bindings = sequences._ScalarBindings(scalar_bounds={
            'a': ranges.Bounds(0, upper), 'b': ranges.Bounds(0, upper)})
        assert planner._bounds_of(model.step, node, bindings) == ranges.Bounds(
            2, (upper + 1) * (upper + 2))
        if upper == 3:
            first = len(visited)
    assert first > 0 and len(visited) == first


SOURCE = '''def helper(x):
    q = abs(x)
    if q & 1:
        q = q + 1
    return q

class M:
    def reset(self):
        self.count = 0

    def process(self, a):
        self.count = self.count + 1
        return {"y": helper(a) + self.count}
'''


@pytest.mark.parametrize('expression', [
    'min(a, i)', 'max(a, i)', 'abs(a - i)', 'int(i == a)',
    'bool(a)', 'pow(a, 2)', 'min(15, max(0, a + i))',
])
def test_scalar_builtins_use_the_same_stable_loop_transfer_as_scalar_operators(expression):
    model = source_model('class M:\n'
                         '    def reset(self):\n        pass\n'
                         '    def process(self, a):\n'
                         '        value = a\n'
                         '        for i in range(128):\n'
                         '            value = ' + expression + '\n'
                         '        return {"y": value}\n')
    assert _for_body_has_stable_contract_transfer(
        model.step.body[1], ScopeResolver(model), model.step)


@pytest.mark.parametrize('expression', ['list(range(i))', 'range(i)',
                                       'min(a, i, key=abs)'])
def test_collection_or_callback_builtins_keep_precise_loop_planning(expression):
    model = source_model('class M:\n'
                         '    def reset(self):\n        pass\n'
                         '    def process(self, a):\n'
                         '        for i in range(8):\n'
                         '            value = ' + expression + '\n'
                         '        return {"y": 0}\n')
    assert not _for_body_has_stable_contract_transfer(
        model.step.body[0], ScopeResolver(model), model.step)


def test_loop_summary_does_not_skip_stateful_helper_effects():
    model = source_model('''class M:
    def reset(self):
        self.count = 0
    def bump(self, value):
        self.count += 1
        return value
    def process(self, a):
        for i in range(128):
            value = self.bump(i)
        return {"y": self.count}
''')
    assert not _for_body_has_stable_contract_transfer(
        model.step.body[0], ScopeResolver(model), model.step)


@pytest.mark.parametrize('expression', [
    'min(value, a - i)', 'max(value, a + i)',
    'abs(a - i)', 'int(i == a)',
])
def test_scalar_builtin_loop_ir_matches_native_execution(expression):
    from behave.sim.behavior import audit, instrumented
    from behave.sim.symbolic import interp
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = ('class M:\n'
              '    def reset(self):\n        pass\n'
              '    def process(self, a):\n'
              '        value = a\n'
              '        for i in range(128):\n'
              '            value = ' + expression + '\n'
              '        return {"y": value}\n')
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=['y'])
    for value in (0, 7, 15):
        _state, outputs, _hits, error = interp.run_step(proc, {'a': value})
        assert not error
        assert outputs.transactions == [namespace['M']().process(value)]
    observed, _sites, hits = instrumented.load_source(source)
    assert not audit.model_audit(
        proc, observed['M'], [[{'a': value} for value in (0, 7, 15)]], hits)


def test_loop_state_effects_are_preserved_in_ir():
    from behave.sim.behavior import audit, instrumented
    from behave.sim.symbolic import interp
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = '''class M:
    def reset(self):
        self.count = 0
    def bump(self, value):
        self.count += 1
        return value
    def process(self, a):
        for i in range(128):
            value = self.bump(i)
        return {"y": self.count}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=['y'], bound=1)
    _state, outputs, _hits, error = interp.run_step(proc, {'a': 0})
    assert not error
    assert outputs.transactions == [{'y': 128}]
    observed, _sites, hits = instrumented.load_source(source)
    assert not audit.model_audit(proc, observed['M'], [[{'a': 0}]], hits)


@pytest.mark.solver
@pytest.mark.parametrize('expression', ['min(value, a + i)', 'max(value, a - i)'])
def test_scalar_builtin_loop_residual_query_has_a_native_witness(expression):
    from behave.sim.behavior import goals, instrumented
    from behave.sim.symbolic import interp
    from behave.sim.behavior.instance import lower
    from behave.sim.behavior.source_plan import plan_source
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
    from behave.sim.engines.local_ir import local_hit_any
    from behave.sim.engines.probing import Outcome

    source = ('class M:\n'
              '    def reset(self):\n        pass\n'
              '    def process(self, a):\n'
              '        value = a\n'
              '        for i in range(128):\n'
              '            value = ' + expression + '\n'
              '        if value == 7:\n'
              '            return {"y": 1}\n'
              '        return {"y": 0}\n')
    schema = TransactionSchema(
        [Endpoint('a', 'input', 4), Endpoint('y', 'output', 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    plan = plan_source(source_model(source), source)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    namespace, sites, hits = instrumented.load_source(source, goal_plan=plan)
    branch, = [site for site in sites if site.family == goals.DECISION]
    target = goals.goal_id(branch, True)
    native = namespace['M']()
    native.reset()
    hits.clear()
    assert native.process(a=0) == {'y': 0}
    assert target not in hits

    answer = local_hit_any(exact.proc, exact.env, dict(exact.proc.initial), (target,),
        timeout_ms=60_000)
    assert answer.outcome is Outcome.SAT, answer.detail
    hits.clear()
    expected = native.process(**answer.candidate)
    _state, output, ir_hits, error = interp.run_step(exact.proc, answer.candidate)
    assert not error and output.transactions == [expected]
    assert set(hits) == set(ir_hits) and target in hits


@pytest.mark.parametrize('expression', [
    '(a + 1) * (b + 2)',
    '(a + 1) if b < 3 else (a + 1)',
    'f"{a + 1:03d}:{b + 2}"',
])
def test_static_descendants_reuse_exact_bindings_without_changing_values(expression):
    model = source_model('class M:\n'
                         '    def reset(self):\n        pass\n'
                         '    def process(self, a, b):\n'
                         '        return {"y": ' + expression + '}\n')
    resolver = ScopeResolver(model)
    query = model.step.body[0].value.values[0]
    used, reference = SourceValueBudget(1000), SourceValueBudget(1000)
    planner = _FunctionContracts(model, resolver, budget=used)
    for b in range(10):
        bindings = {'a': 7, 'b': b}
        assert planner._static_scalar(model.step, query, bindings) == _static_value(
            query, resolver.owner_of(model.step), bindings, budget=reference)
    assert used.left > reference.left


@pytest.mark.parametrize('expression', [
    '(a // b) if b != 0 else 0',
    '(a << b) if b >= 0 else (a >> -b)',
    '(a & b) ^ (a | b)',
    'a == b',
    '(a + 1) if b else (a - 1)',
    'f"{a!r}:{b!s}"',
])
def test_recursive_static_reuse_preserves_unknowns_types_and_changed_bindings(expression):
    model = source_model('class M:\n'
                         '    def reset(self):\n        pass\n'
                         '    def process(self, a, b):\n'
                         '        return {"y": ' + expression + '}\n')
    resolver = ScopeResolver(model)
    query = model.step.body[0].value.values[0]
    planner = _FunctionContracts(model, resolver)
    bindings = {}
    for a in (None, False, True, -3, 0, 1, 7):
        for b in (None, False, True, -2, 0, 1, 3):
            bindings.update(a=a, b=b)
            expected = _static_value(query, resolver.owner_of(model.step), bindings)
            actual = planner._static_scalar(model.step, query, bindings)
            assert type(actual) is type(expected)
            assert actual == expected


def _layers(source, domains, monkeypatch, *, state_only=True):
    model = source_model(source)
    resolver, cache = ScopeResolver(model), _SourceQueryCache()
    visits = []
    original = _FunctionContracts._block

    def counted(self, fn, body, states):
        if fn.name == 'helper' and body is fn.body:
            visits.append((fn, self.state_only))
        return original(self, fn, body, states)

    monkeypatch.setattr(_FunctionContracts, '_block', counted)
    results = []
    for count, domain in enumerate(domains):
        planner = _FunctionContracts(
            model, resolver, declaration_contracts={'count': SCALAR},
            query_cache=cache, state_only=state_only)
        planner.summarize(model.step, (SCALAR,), (None,),
                          argument_ranges=(domain,),
                          state_contracts={'count': SCALAR},
                          state_ranges={'count': ranges.Range(count, count)})
        results.append(planner.final_state_ranges(planner.root_context)['count'])
    return visits, results, cache


def test_stateless_scalar_transfer_reused_without_replaying_old_state(monkeypatch):
    visits, states, cache = _layers(SOURCE, [ranges.Range(-7, 7)] * 4, monkeypatch)
    assert len(visits) == 1
    assert states == [ranges.Range(i, i) for i in range(1, 5)]
    assert len(cache.scalar_summaries) == 1


def test_different_argument_ranges_do_not_share_a_scalar_summary(monkeypatch):
    visits, _, cache = _layers(
        SOURCE, [ranges.Range(-7, 7), ranges.Range(2, 4), ranges.Range(-7, 7)], monkeypatch)
    assert len(visits) == len(cache.scalar_summaries) == 2


def test_final_layout_still_visits_every_helper_activation(monkeypatch):
    visits, _, cache = _layers(
        SOURCE, [ranges.Range(-7, 7)] * 4, monkeypatch, state_only=False)
    assert len(visits) == 4
    assert not cache.scalar_summaries


@pytest.mark.parametrize('body', ['return x + self.count', 'self.count += 1\n        return x'])
def test_reading_or_writing_state_is_not_a_stateless_summary(monkeypatch, body):
    source = '''class M:
    def reset(self):
        self.count = 0
    def helper(self, x):
        BODY
    def process(self, a):
        self.count += 1
        return {"y": self.helper(a)}
'''.replace('BODY', body)
    visits, _, cache = _layers(source, [ranges.Range(0, 7)] * 3, monkeypatch)
    assert len(visits) == 3
    assert not cache.scalar_summaries


@pytest.mark.parametrize('captured', [False, True])
def test_fixed_arguments_and_closure_values_remain_in_summary_key(monkeypatch, captured):
    if captured:
        source = '''class M:
    def reset(self):
        self.count = 0
    def process(self, a):
        self.count += 1
        x = self.count
        def helper():
            return x + 1
        return {"y": helper()}
'''
    else:
        source = SOURCE.replace('helper(a)', 'helper(self.count)')
    visits, _, cache = _layers(source, [ranges.Range(0, 7)] * 3, monkeypatch)
    assert len(visits) == 3
    assert len(cache.scalar_summaries) == 3


def test_transitive_closure_values_remain_in_summary_key(monkeypatch):
    source = '''class M:
    def reset(self):
        self.count = 0
    def process(self, a):
        self.count = self.count + 1
        x = self.count
        def leaf():
            return x + 1
        def helper():
            return leaf()
        return {"y": helper()}
'''
    visits, _, _ = _layers(source, [ranges.Range(0, 7)] * 3, monkeypatch)
    assert len(visits) == 3


def test_transitive_state_reads_are_not_a_stateless_summary(monkeypatch):
    source = '''class M:
    def reset(self):
        self.count = 0
    def process(self, a):
        self.count = self.count + 1
        def leaf():
            return self.count
        def helper():
            return leaf()
        return {"y": helper()}
'''
    visits, _, cache = _layers(source, [ranges.Range(0, 7)] * 3, monkeypatch)
    assert len(visits) == 3
    assert not cache.scalar_summaries


@pytest.mark.parametrize('bound', [1, 3, 8])
@pytest.mark.parametrize('helper', [
    None,
    'assert x != 0\n    return 12 // x',
    'if x < 0:\n        return -x\n    return x + 1',
])
def test_complete_plan_matches_no_scalar_summary_reuse(monkeypatch, bound, helper):
    source = SOURCE if helper is None else (
        'def helper(x):\n    ' + helper + '\n\nclass M:' + SOURCE.split('class M:', 1)[1])
    model = source_model(source)

    def plan():
        return plan_sequences(
            model, ScopeResolver(model), ('y',),
            declaration_contracts={'count': SCALAR},
            input_contracts={'a': SCALAR},
            input_ranges={'a': ranges.Range(-7, 7), 'count': ranges.Range(0, 0)},
            state_bound=bound)

    actual = plan()
    monkeypatch.setattr(_FunctionContracts, '_scalar_summary_key', lambda *args: None)
    expected = plan()
    for field in actual.__slots__:
        left, right = getattr(actual, field), getattr(expected, field)
        if field == '_specs':
            def records(values):
                return {key: tuple(getattr(value, name) for name in _Spec.__slots__)
                        for key, value in values.items()}
            left, right = records(left), records(right)
        assert left == right, field
