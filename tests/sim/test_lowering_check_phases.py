"""Lowering cannot return around a failed post-construction contract check."""

import ast
from types import SimpleNamespace

import pytest

from behave.sim.behavior import lower_checks, lower_prepare, lowering, step_alias
from behave.sim.symbolic import interp, ir, typecheck as types
from behave.sim.behavior.construction import CONSTRUCTION_VERSION, ConstructionFacts
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.native_values import DEFAULT_WIRE_NODES, WireEncoder
from behave.sim.behavior.source_values import SCALAR, SourceValueBudget, ValueContract

SOURCE = '''class M:
    def reset(self):
        self.buf = [0]
    def process(self, a):
        self.buf[0] = a
        return {"y": self.buf[0]}
'''
PHASES = ('check_goals', 'finalize_entry_state', 'check_array_state_types',
          'check_step_aliases', 'check_construction_sharing')


def construction():
    return ConstructionFacts.from_dict({
        'version': CONSTRUCTION_VERSION,
        'values': WireEncoder(DEFAULT_WIRE_NODES).encode({'buf': [0]}),
        'unsupported': [], 'shared_pairs': [], 'opaque_fields': [], 'boundary_fields': [],
    })


@pytest.mark.parametrize('failure', [None, *PHASES])
def test_checks_run_in_order_and_failure_cannot_be_bypassed(monkeypatch, failure):
    seen = []
    fault = LoweringError(ast.parse('pass').body[0], 'injected check failure',
                          code='injected-check-failure')

    def wrap(name, original):
        def run(*args, **kwargs):
            seen.append(name)
            if name == failure:
                raise fault
            return original(*args, **kwargs)
        return run

    for name in PHASES:
        monkeypatch.setattr(lower_checks, name, wrap(name, getattr(lower_checks, name)))
    if failure is not None:
        with pytest.raises(LoweringError) as caught:
            lowering.lower_procedure(SOURCE, input_widths=[2], construction=construction())
        assert caught.value is fault
        assert seen == list(PHASES[:PHASES.index(failure) + 1])
    else:
        proc = lowering.lower_procedure(SOURCE, input_widths=[2], construction=construction())
        assert seen == list(PHASES)
        state, outputs, _, error = interp.run_step(proc, {'a': 3}, None)
        assert error == ''
        assert outputs.transactions == [{'y': 3}]
        assert state['buf'] == [3]


@pytest.mark.parametrize('boundary_initial', [{}, {'left': [0]}, {'right': [0]}])
def test_construction_sharing_respects_exact_boundary_rebindings(boundary_initial):
    proc = ir.Procedure(['left', 'right'], [], [], [], ir.Skip(),
                        types={'left': types.Array(1), 'right': types.Array(1)})
    model = SimpleNamespace(cls=ast.parse('class M: pass').body[0])
    facts = SimpleNamespace(shared_pairs=(('left', 'right'),), opaque_fields=())
    kwargs = {'state_names': {}, 'active_structured': {}, 'active_products': {},
              'boundary_initial': boundary_initial}
    if boundary_initial:
        lower_checks.check_construction_sharing(proc, model, facts, **kwargs)
    else:
        with pytest.raises(LoweringError) as caught:
            lower_checks.check_construction_sharing(proc, model, facts, **kwargs)
        assert caught.value.code == 'state-aliased-in-construction'


@pytest.mark.parametrize('bound', [False, True])
def test_opaque_array_requires_attested_sharing_unless_boundary_owned(bound):
    proc = ir.Procedure(['buffer'], [], [], [], ir.Skip(), types={'buffer': types.Array(1)})
    model = SimpleNamespace(cls=ast.parse('class M: pass').body[0])
    facts = SimpleNamespace(shared_pairs=(), opaque_fields=('buffer',))
    kwargs = {'state_names': {}, 'active_structured': {}, 'active_products': {},
              'boundary_initial': {'buffer': [0]} if bound else {}}
    if bound:
        lower_checks.check_construction_sharing(proc, model, facts, **kwargs)
    else:
        with pytest.raises(LoweringError) as caught:
            lower_checks.check_construction_sharing(proc, model, facts, **kwargs)
        assert caught.value.code == 'state-graph-unverified'


@pytest.mark.parametrize('loop_count', [False, True])
def test_array_shape_check_includes_dynamic_loop_count_expressions(loop_count):
    selection = ir.Sel(ir.Var('buf'), ir.Lit(0))
    body = (ir.For('i', selection, ir.Skip()) if loop_count
            else ir.Assign('temporary', selection))
    proc = ir.Procedure(['buf'], [], [], [], body, types={'another': types.INTEGER})
    model = SimpleNamespace(cls=ast.parse('class M: pass').body[0])
    with pytest.raises(LoweringError) as caught:
        lower_checks.check_array_state_types(proc, model)
    assert caught.value.code == 'state-type-unresolved'


def test_array_shape_check_accepts_a_typed_array_in_the_loop_count():
    body = ir.For('i', ir.Sel(ir.Var('buf'), ir.Lit(0)), ir.Skip())
    proc = ir.Procedure(['buf'], [], ['i'], [], body, types={'buf': types.Array(1)})
    lower_checks.check_array_state_types(proc, SimpleNamespace(cls=None))


@pytest.fixture
def alias_case():
    model = source_model(SOURCE, 'alias_case.py')
    array = types.Array(1)
    proc = ir.Procedure(['buf'], [], [], [], ir.Skip(), types={'buf': array})
    low = SimpleNamespace(resolver=ScopeResolver(model), source_loop_spans={},
                          borrowed_arguments={})
    prepared = SimpleNamespace(model=model)
    declarations = SimpleNamespace(state_names={'buf': 'buf'})
    planned = SimpleNamespace(boundary_arrays={}, boundary_contracts={})
    contexts = SimpleNamespace(held=set(), active_products={})
    return SimpleNamespace(
        args=(proc, low, prepared, declarations, planned, contexts),
        kwargs={'root_renames': {}, 'source_value_budget': SourceValueBudget(100),
                'analysis_cache': lower_prepare.PreparationCache()},
        proc=proc, low=low, model=model, declarations=declarations,
        planned=planned, contexts=contexts)


@pytest.mark.parametrize('cached', [False, True])
def test_alias_checks_reuse_none_and_keep_the_callers_budget(monkeypatch, alias_case, cached):
    case = alias_case
    cache = case.kwargs['analysis_cache'] if cached else None
    case.kwargs['analysis_cache'] = cache
    budget = case.kwargs['source_value_budget']
    calls = []

    def analyze(model, array_vars, resolver, **kwargs):
        assert model is case.model and resolver is case.low.resolver
        assert array_vars == {'buf'}
        assert kwargs['loop_spans'] is case.low.source_loop_spans
        assert kwargs['borrowed_arguments'] is case.low.borrowed_arguments
        assert kwargs['array_types'] == {'buf': types.Array(1)}
        assert kwargs['state_contracts'] == {}
        assert kwargs['source_value_budget'] is budget
        budget.spend(model.step)
        calls.append(1)

    monkeypatch.setattr(step_alias, 'step_alias_hazard', analyze)
    for _ in range(2):
        lower_checks.check_step_aliases(*case.args, **case.kwargs)
    assert len(calls) == (1 if cached else 2)
    assert budget.left == 100 - len(calls)
    if cached:
        key = step_alias.alias_query_identity(case.model, {'buf'}, {}, case.proc.types)
        assert cache.alias_results == {key: None}
        assert lower_prepare.PreparationCache().alias_results == {}


@pytest.mark.parametrize(('kind', 'detail'), [
    ('mutation', "the step binds 'left' and 'right' to one mutable object and then writes through 'right'"),
    ('retained', "the step retains one mutable object through distinct state locations rooted at 'left' and 'right'"),
    ('unproved', "the alias analysis cannot establish the effect of 'right' relative to 'left'"),
])
def test_cached_alias_hazards_keep_site_and_diagnostic(monkeypatch, alias_case, kind, detail):
    case, calls = alias_case, []
    hazard = step_alias.AliasHazard(kind, 'right', 'left')

    def analyze(*_args, **_kwargs):
        calls.append(1)
        return hazard

    monkeypatch.setattr(step_alias, 'step_alias_hazard', analyze)
    for _ in range(2):
        with pytest.raises(LoweringError) as caught:
            lower_checks.check_step_aliases(*case.args, **case.kwargs)
        assert caught.value.node is case.model.step
        assert caught.value.code == 'array-assigned-by-reference'
        assert caught.value.what == detail + ': the IR copies arrays by value where Python shares references'
    assert calls == [1]
    assert next(iter(case.kwargs['analysis_cache'].alias_results.values())) is hazard


@pytest.mark.parametrize('failure', ['budget', 'unexpected'])
def test_alias_failure_does_not_publish_a_cache_result(monkeypatch, alias_case, failure):
    case = alias_case
    site = ast.parse('broken = 1').body[0]
    fault = RuntimeError('injected alias failure')

    def analyze(*_args, **kwargs):
        if failure == 'budget':
            kwargs['source_value_budget'].spend(site, 101)
        raise fault

    monkeypatch.setattr(step_alias, 'step_alias_hazard', analyze)
    with pytest.raises(LoweringError if failure == 'budget' else RuntimeError) as caught:
        lower_checks.check_step_aliases(*case.args, **case.kwargs)
    if failure == 'budget':
        assert caught.value.code == 'source-plan-budget-exceeded'
        assert caught.value.node is site
        assert caught.value.what == 'source value planning exceeded the procedure budget during lowering'
        assert case.kwargs['source_value_budget'].left == -1
    else:
        assert caught.value is fault
    assert case.kwargs['analysis_cache'].alias_results == {}


def test_output_only_arrays_do_not_trigger_source_alias_analysis(monkeypatch, alias_case):
    case = alias_case
    case.contexts.held.add('buf')
    case.declarations.state_names = {}

    def unexpected(*_args, **_kwargs):
        pytest.fail('output-only storage is not a source array')

    monkeypatch.setattr(step_alias, 'step_alias_hazard', unexpected)
    monkeypatch.setattr(step_alias, 'alias_query_identity', unexpected)
    lower_checks.check_step_aliases(*case.args, **case.kwargs)


def test_alias_check_maps_source_names_and_keeps_structured_contracts(monkeypatch, alias_case):
    case = alias_case
    array = types.Array(1)
    record = ValueContract.record({'x': SCALAR})
    sequence = ValueContract.sequence('list', 1, 1, SCALAR)
    products = {'record': record, 'sequence': sequence,
                'mapping': ValueContract.mapping(0, 1, SCALAR),
                'optional_record': ValueContract.option(record),
                'scalar': SCALAR, 'optional_scalar': ValueContract.option(SCALAR)}
    case.proc.types = {'output': array, 'reserved_local': array, 'state_buf': array}
    case.contexts.held = {'output'}
    case.contexts.active_products = {k: SimpleNamespace(contract=v) for k, v in products.items()}
    case.declarations.state_names = {'buf': 'state_buf'}
    case.kwargs['root_renames'] = {'local': 'reserved_local'}
    case.planned.boundary_arrays = {'external': array}
    case.planned.boundary_contracts = {'external': sequence, 'ignored_record': record}
    calls = []

    def analyze(_model, array_vars, _resolver, **kwargs):
        assert array_vars == {'reserved_local', 'state_buf', 'buf', 'local', 'external',
                              'record', 'sequence', 'mapping', 'optional_record'}
        assert kwargs['array_types'] == {'reserved_local': array, 'state_buf': array,
                                         'local': array, 'external': array}
        assert kwargs['state_contracts'] == {'external': sequence, **products}
        calls.append(1)

    monkeypatch.setattr(step_alias, 'step_alias_hazard', analyze)
    lower_checks.check_step_aliases(*case.args, **case.kwargs)
    assert calls == [1]


@pytest.mark.parametrize('change', ['array_type', 'source_names', 'state_contract',
                                   'loop_span', 'borrowed_argument'])
def test_alias_cache_misses_when_the_semantic_request_changes(monkeypatch, alias_case, change):
    case, calls = alias_case, []
    if change in ('loop_span', 'borrowed_argument'):
        model = source_model(SOURCE.replace(
            'self.buf[0] = a', 'for i in range(1):\n            self.buf[0] = a'), 'alias_case.py')
        case.args[2].model = model
        case.low.resolver = ScopeResolver(model)

    def analyze(*_args, **_kwargs):
        calls.append(1)

    monkeypatch.setattr(step_alias, 'step_alias_hazard', analyze)
    lower_checks.check_step_aliases(*case.args, **case.kwargs)
    if change == 'array_type':
        case.proc.types['buf'] = types.Array(2)
    elif change == 'source_names':
        case.kwargs['root_renames']['local'] = 'buf'
    elif change == 'state_contract':
        case.planned.boundary_contracts['buf'] = ValueContract.sequence('list', 1, 1, SCALAR)
    elif change == 'loop_span':
        loop = next(n for n in ast.walk(model.step) if isinstance(n, ast.For))
        case.low.source_loop_spans[id(loop)] = (0, 1, 1)
    else:
        call = next(n for n in ast.walk(model.step) if isinstance(n, ast.Call))
        case.low.borrowed_arguments[id(call)] = {'argument': 'buf'}
    lower_checks.check_step_aliases(*case.args, **case.kwargs)
    assert calls == [1, 1]
    assert len(case.kwargs['analysis_cache'].alias_results) == 2
