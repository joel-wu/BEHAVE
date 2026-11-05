"""Array templates defer finite reads; solver formulae remain pure QF_BV."""

import pytest
import z3

from behave.sim.symbolic import encoder, ir, ranges, typecheck as types
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import StepTemplate, _leaves
from behave.sim.symbolic.uniform_array import UniformArray
from behave.sim.core.contract import FiniteType
from behave.sim.core.construction_checkpoint import finish_construction

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('conjunction', [False, True])
@pytest.mark.parametrize('parameterized', [False, True])
def test_neutral_boolean_roots_are_literals_and_skip_dead_array_recipes(
        private_context, conjunction, parameterized):
    from behave.sim.symbolic.template_arrays import TemplateArrays

    context = z3.Context() if private_context else z3.main_ctx()
    recipes, calls = TemplateArrays(), []
    neutral = z3.BoolVal(conjunction)
    terms = (z3.Var(0, z3.BoolSort()), neutral) if parameterized else ()
    guard = z3.And(*terms) if conjunction else z3.Or(*terms)
    def array():
        calls.append(None)
        return FunctionalArray.uniform(2, z3.BitVecVal(99, 8, ctx=context))
    deferred = recipes._call('deferred', 2, 8, (), array)
    read = z3.Select(deferred, z3.BitVecVal(0, 3))
    value = z3.BitVecVal(7, 8)
    image = z3.If(guard, value, read) if conjunction else z3.If(guard, read, value)
    if private_context:
        guard, image = guard.translate(context), image.translate(context)
    arguments = (z3.BoolVal(conjunction, ctx=context),) if parameterized else ()
    folded = recipes.instantiate(guard, arguments)
    assert (z3.is_true(folded) if conjunction else z3.is_false(folded))
    result = recipes.instantiate(image, arguments)
    assert z3.is_bv_value(result) and result.as_long() == 7
    assert calls == []
    if parameterized:
        result = recipes.instantiate(image, (z3.BoolVal(not conjunction, ctx=context),))
        assert result.as_long() == 99
        assert calls == [None]  # A genuinely enabled recipe still executes.


def _procedure(cells):
    address = ir.Op('add', ir.Lit(cells // 2), ir.Var('a'))
    value = ir.Op('uwrap', ir.Op('add', ir.Var('a'), ir.Lit(4)), ir.Lit(32))
    body = [ir.Store('m', address, value)]
    for i in range(8):
        body.append(ir.Assign(f'copy{i}', ir.Var('m')))
    body.append(ir.If(ir.Op('eq', ir.Sel(ir.Var('copy7'), ir.Lit(cells // 2 + 5)), ir.Lit(9)),
                      ir.Mark('target'), ir.Skip()))
    names = ('m', *(f'copy{i}' for i in range(8)))
    proc = ir.Procedure(names, ('a',), (), (), ir._sequence(body),
        initial=dict.fromkeys(names, UniformArray.uniform(cells, 0)),
        types=dict.fromkeys(names, types.Array(cells)),
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    env = dict.fromkeys(names, ranges.Range(0, 2**32 - 1))
    env['a'] = ranges.Range(0, 15)
    return proc, env


@pytest.mark.parametrize('private_context', [False, True])
def test_aliases_decide_controls_without_reopening_scalar_datapaths(private_context):
    from behave.sim.symbolic.template_arrays import TemplateArrays

    context = z3.Context() if private_context else z3.main_ctx()
    recipes, calls = TemplateArrays(), []
    left, right = (z3.Var(i, z3.BitVecSort(32)) for i in range(2))
    guard = z3.And(left == right, z3.Not(z3.Distinct(left, right)))
    def array():
        calls.append(None)
        return FunctionalArray.uniform(2, z3.BitVecVal(99, 8, ctx=context))
    deferred = recipes._call('deferred', 2, 8, (), array)
    image = z3.If(guard, z3.BitVecVal(7, 8), z3.Select(deferred, z3.BitVecVal(0, 3)))
    if private_context:
        image = image.translate(context)
    a, b = z3.BitVecs('alias_a alias_b', 32, ctx=context)
    term = a * b  # Not a constant and not a constraint on either operand.
    result = recipes.instantiate(image, (term, term))
    assert z3.is_bv_value(result) and result.as_long() == 7
    assert not calls
    result = recipes.instantiate(image, (a, b))
    assert calls == [None]  # Different inputs remain genuinely conditional.
    solver = z3.Solver(ctx=context)
    expected = z3.If(a == b, z3.BitVecVal(7, 8, ctx=context),
                     z3.BitVecVal(99, 8, ctx=context))
    assert solver.check(result != expected) == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('comparison,signed,truth', [
    (lambda x: x == 16, False, False),
    (lambda x: z3.Distinct(x, 16), False, True),
    (lambda x: z3.ULE(x, 15), False, True),
    (lambda x: z3.UGT(x, 15), False, False),
    (lambda x: x >= 16, True, False),
    (lambda x: x < 16, True, True),
    # The coarse hull need not exploit sign-bit/suffix correlations. Keep
    # the original expression when it cannot prove the tighter comparison.
    (lambda x: x >= 8, True, None),
    (lambda x: x < 8, True, None),
    # Sign extension does NOT prove the corresponding unsigned comparison.
    (lambda x: z3.ULE(x, 15), True, None),
])
def test_wiring_comparisons_select_recipes_before_array_expansion(
        private_context, comparison, signed, truth):
    from behave.sim.symbolic.template_arrays import TemplateArrays

    context = z3.Context() if private_context else z3.main_ctx()
    recipes, calls = TemplateArrays(), []
    parameter = z3.Var(0, z3.BitVecSort(32))
    guard = comparison(parameter)
    if truth is False:
        guard = z3.Not(guard)
    def array():
        calls.append(None)
        return FunctionalArray.uniform(2, z3.BitVecVal(99, 8, ctx=context))
    deferred = recipes._call('deferred', 2, 8, (), array)
    image = z3.If(guard, z3.BitVecVal(7, 8), z3.Select(deferred, z3.BitVecVal(0, 3)))
    if private_context:
        image = image.translate(context)
    low = z3.BitVec('wire_low', 4, ctx=context)
    wire = z3.SignExt(28, low) if signed else z3.Concat(z3.BitVecVal(0, 28, ctx=context), low)
    result = recipes.instantiate(image, (wire,))
    if truth is not None:
        assert z3.is_bv_value(result) and result.as_long() == 7
        assert calls == []
    else:
        assert calls == [None]
    full = z3.BitVec('wire_full', 32, ctx=context)
    for argument in (wire, full):
        result = recipes.instantiate(image, (argument,))
        condition = comparison(argument)
        if truth is False:
            condition = z3.Not(condition)
        expected = z3.If(condition, z3.BitVecVal(7, 8, ctx=context),
                         z3.BitVecVal(99, 8, ctx=context))
        assert z3.Solver(ctx=context).check(result != expected) == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('source', ['packed', 'signed', 'store', 'choice', 'merge', 'wide-store'])
def test_prefix_array_controls_keep_exact_selection_sources(private_context, source):
    from behave.sim.symbolic.template_arrays import TemplateArrays
    from behave.sim.symbolic.term_bounds import _SyntacticClosureProof

    context = z3.Context() if private_context else z3.main_ctx()
    array = FunctionalArray.symbolic(4, 'control_cells', 8, 0, 15).translate(context)
    index = z3.BitVec('control_index', 4, ctx=context)
    enabled = z3.Bool('control_enabled', ctx=context)
    if source == 'signed':
        array = FunctionalArray.symbolic(4, 'signed_cells', 4, -8, 7).widen(8).translate(context)
    elif source in ('store', 'wide-store'):
        value = (z3.BitVec('unrestricted_write', 8, ctx=context) if source == 'wide-store'
                 else z3.BitVecVal(-5, 8, ctx=context))
        array = array.store(z3.BitVec('write_index', 4, ctx=context), value, enabled)
    elif source == 'choice':
        array = FunctionalArray.choose(enabled, array,
            FunctionalArray.uniform(4, z3.BitVecVal(-3, 8, ctx=context)))
    elif source == 'merge':
        array = FunctionalArray.merge_bits(array, array,
            FunctionalArray.uniform(4, z3.BitVecVal(3, 8, ctx=context)), 4, signed=False).widen(8)
    recipes, calls = TemplateArrays(), []
    def expensive():
        calls.append(None)
        return FunctionalArray.uniform(2, z3.BitVecVal(99, 8, ctx=context))
    parameter = z3.Var(0, z3.ArraySort(z3.BitVecSort(4), z3.BitVecSort(8)))
    read = z3.Select(parameter, z3.Var(1, z3.BitVecSort(4)))
    deferred = recipes._call('deferred', 2, 8, (), expensive)
    image = z3.If(read >= 16, z3.Select(deferred, z3.BitVecVal(0, 3)), z3.BitVecVal(7, 8))
    if private_context:
        image = image.translate(context)
    prepared = recipes.prepare(image)
    for address in (index, z3.BitVecVal(-1, 4, ctx=context), z3.BitVecVal(4, 4, ctx=context)):
        proof = _SyntacticClosureProof({})
        calls.clear()
        result = finish_construction(prepared.instantiate_steps((array, address), _facts=proof))
        expected = z3.If(array.select(address) >= 16,
            z3.BitVecVal(99, 8, ctx=context), z3.BitVecVal(7, 8, ctx=context))
        assert z3.Solver(ctx=context).check(result != expected) == z3.unsat
        assert proof.array_selections
        if source == 'packed':
            assert array._base.value.get_id() not in proof.terms
        if source != 'wide-store':
            assert z3.is_bv_value(result) and result.as_long() == 7
            assert calls == []
        elif address is index:
            assert calls == [None]  # Real, reachable writes cannot be pruned.
            assert z3.Solver(ctx=context).check(result == 99) == z3.sat


def _qfbv(roots):
    seen, pending = set(), list(roots)
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        assert z3.is_bool(term) or z3.is_bv(term), term
        assert not z3.is_quantifier(term) and not z3.is_var(term)
        assert term.decl().kind() != z3.Z3_OP_UNINTERPRETED or not term.num_args(), term
        pending.extend(term.children())


@pytest.mark.parametrize('private_context', [False, True])
def test_65536_word_template_does_not_select_cells_during_preparation(monkeypatch, private_context):
    proc, env = _procedure(65536)
    assert ir.procedure_representation_cost(proc) > 1_000_000
    assert ir.procedure_representation_cost(proc, compact_arrays=True) < 200
    selected = []
    original = FunctionalArray.select
    def select(array, index):
        selected.append(index)
        return original(array, index)
    monkeypatch.setattr(FunctionalArray, 'select', select)
    template = StepTemplate(proc, env)
    assert len(selected) < 10  # One source read, not one read per memory cell.
    assert template._image.num_args() < 30
    view = template.project({'hit': ('target',)}, outputs=())
    if private_context:
        view = view.translate(z3.Context())
    before = {name: encoder.encode_concrete_value(proc.initial[name], env[name], name, kind=proc.types[name])
              for name in view.state_names}
    step = view.encode(before)
    roots = (step.constraint, step.ok, step.error, step.hits['hit'])
    _qfbv(roots)
    solver = z3.Solver(ctx=view.context)
    solver.add(step.constraint, step.hits['hit'])
    assert solver.check() == z3.sat
    assert solver.model().eval(step.pre['a']).as_long() == 5
    solver.add(step.pre['a'] != 5)
    assert solver.check() == z3.unsat
    assert len(selected) < 30


def test_array_template_matches_dense_arbitrary_states_and_multistep_updates():
    proc, env = _procedure(16)
    template = StepTemplate(proc, env)
    plain = encoder.encode_step(proc, env=env, tag='plain')
    compact = template.encode(plain.pre)
    solver = z3.Solver()
    solver.add(plain.constraint, compact.constraint)
    different = [plain.hits['target'] != compact.hits['target'], plain.ok != compact.ok]
    for name in proc.state:
        different.extend(a != b for a, b in zip(_leaves(plain.state_post[name]),
                                                _leaves(compact.state_post[name]), strict=True))
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat
    next_step = template.encode(compact.state_post)
    _qfbv((next_step.constraint, next_step.hits['target']))


@pytest.mark.parametrize('steps', [8, 32])
@pytest.mark.parametrize('normalized', [False, True])
def test_template_updates_keep_recipes_until_state_binding(monkeypatch, steps, normalized):
    cells = 4
    address = ir.Op('fmod', ir.Op('add', ir.Var('a'), ir.Var('j')), ir.Lit(cells))
    incremented = ir.Op('uwrap', ir.Op('add', ir.Sel(ir.Var('m'), address), ir.Lit(1)), ir.Lit(4))
    body = ir.If(ir.Op('eq', ir.Var('j'), ir.Var('stop')),
        ir.Seq(ir.Mark('before-failure'), ir.Fail('stop')),
        ir.If(ir.Op('eq', ir.Op('and', ir.Var('j'), ir.Lit(1)), ir.Lit(0)),
              ir.Store('m', address, incremented),
              ir.Assign('m', ir.Op('store', ir.Var('m'), address, ir.Var('a')))))
    proc = ir.Procedure(('m',), ('a', 'stop'), ('j',), ('y',),
        ir._sequence([ir.For('j', steps, body), ir.Mark('done'),
                      ir.Assign('y', ir.Sel(ir.Var('m'), ir.Var('a')))]),
        initial={'m': [0] * cells}, types={'m': types.Array(cells)},
        output_vars={'y': 'y'})
    env = {'m': ranges.Range(0, 15), 'a': ranges.Range(0, cells-1),
           'stop': ranges.Range(0, steps), 'j': ranges.Range(0, steps-1),
           'y': ranges.Range(0, 15)}

    def packed_too_early(*args, **kwargs):
        pytest.fail('template construction materialized an array before state binding')

    with monkeypatch.context() as patch:
        patch.setattr(FunctionalArray, '_packed_value_steps', packed_too_early)
        template = StepTemplate(proc, env)
        if normalized:
            template = template.normalized()
    plain = encoder.encode_step(proc, env=env, tag='recipe-control')
    actual = template.encode(plain.pre)
    differences = [plain.ok != actual.ok, plain.error != actual.error]
    differences.extend(plain.hits[name] != actual.hits[name] for name in plain.hits)
    for name in plain.post:
        for left, right in zip(_leaves(plain.post[name]), _leaves(actual.post[name]), strict=True):
            left, right = encoder.widen(left, right)
            differences.append(left != right)
    _qfbv((actual.constraint, actual.ok, actual.error, *actual.hits.values(),
           *(leaf for value in actual.post.values() for leaf in _leaves(value))))
    solver = z3.Solver()
    solver.add(plain.constraint, actual.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('cells', [8, 32])
def test_compiled_reset_fill_remains_one_recipe(monkeypatch, cells):
    proc = ir.Procedure(('m',), ('reset', 'a'), ('j',), (),
        ir.If(ir.Op('truth', ir.Var('reset')),
              ir.For('j', cells, ir.Store('m', ir.Var('j'), ir.Lit(0))),
              ir.Store('m', ir.Var('a'), ir.Lit(7))),
        types={'m': types.Array(cells)})
    env = {'m': ranges.Range(0, 255), 'reset': ranges.Range(0, 1),
           'a': ranges.Range(0, cells-1), 'j': ranges.Range(0, cells-1)}
    template = StepTemplate(proc, env).normalized()
    stores, original = [], FunctionalArray.store

    def store(*args):
        stores.append(None)
        return original(*args)

    monkeypatch.setattr(FunctionalArray, 'store', store)
    step = template.instantiate({'reset': z3.BitVecVal(1, template._parameters['reset'].size())})
    assert not stores  # A reset fill, not one interpreted store per cell.
    assert all(z3.is_true(z3.simplify(value == 0)) for value in _leaves(step.state_post['m']))


@pytest.mark.parametrize('cells', [4, 16])
def test_compact_derived_step_preserves_guarded_writes_and_first_failure(cells):
    address = ir.Op('fmod', ir.Op('add', ir.Var('i'), ir.Var('j')), ir.Lit(cells))
    proc = ir.Procedure(('m',), ('i', 'a'), ('j',), ('y',),
        ir._sequence([
            ir.Assign('y', ir.Lit(0)),
            ir.For('j', 3, ir._sequence([
                ir.If(ir.Op('eq', ir.Var('i'), ir.Lit(cells)),
                      ir.Fail('IndexError', 'bad-index'), ir.Skip()),
                ir.If(ir.Op('eq', ir.Var('a'), ir.Var('j')),
                      ir.Fail('ValueError', 'bad-value'), ir.Skip()),
                ir.Store('m', address, ir.Op('add', ir.Var('a'), ir.Var('j'))),
                ir.Mark('write'),
            ])),
            ir.Assign('y', ir.Sel(ir.Var('m'), ir.Op('fmod', ir.Var('i'), ir.Lit(cells)))),
        ]), initial={'m': [0] * cells}, types={'m': types.Array(cells)},
        output_vars={'y': 'y'})
    env = {'m': ranges.Range(-4, 7), 'i': ranges.Range(0, cells),
           'a': ranges.Range(0, 3), 'j': ranges.Range(0, 2), 'y': ranges.Range(-4, 7)}
    plain = encoder.encode_step(proc, env=env, tag='dense-closure')
    compact = encoder._encode_derived_step(proc, env=env, pre=plain.pre,
        tag='compact-closure',
        _validation=encoder._procedure_validation(proc, compact_arrays=True))
    assert isinstance(compact.state_post['m'], FunctionalArray)
    assert plain.error_codes == compact.error_codes
    different = [plain.ok != compact.ok, plain.error != compact.error]
    for name in plain.post:
        for a, b in zip(_leaves(plain.post[name]), _leaves(compact.post[name]), strict=True):
            a, b = encoder.widen(a, b)
            different.append(a != b)
    assert plain.hits.keys() == compact.hits.keys()
    for goal in plain.hits:
        different.append(plain.hits[goal] != compact.hits[goal])
        different.extend(a != b for a, b in zip(plain.hit_occurrences[goal],
            compact.hit_occurrences[goal], strict=True))
    solver = z3.Solver()
    solver.add(plain.constraint, compact.constraint)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat
    solver.pop()
    # Neither success nor either failure mode is vacuous in the shared domain.
    for condition in (compact.ok, compact.hits['bad-index'], compact.hits['bad-value']):
        solver.push()
        solver.add(condition)
        assert solver.check() == z3.sat
        solver.pop()


@pytest.mark.parametrize('steps', [32, 128])
@pytest.mark.parametrize('functional_store', [False, True])
def test_indexed_updates_do_not_retain_a_growing_read_history(
        monkeypatch, steps, functional_store):
    cells = 16
    address = ir.Op('fmod', ir.Op('add', ir.Var('a'), ir.Var('j')), ir.Lit(cells))
    value = ir.Op('and', ir.Op('add', ir.Sel(ir.Var('m'), address), ir.Lit(1)), ir.Lit(15))
    update = (ir.Assign('m', ir.Op('store', ir.Var('m'), address, value))
              if functional_store else ir.Store('m', address, value))
    proc = ir.Procedure(('m',), ('a',), ('j',), ('y',),
        ir.Seq(ir.For('j', steps, update),
               ir.Assign('y', ir.Sel(ir.Var('m'), ir.Var('a')))),
        initial={'m': [0] * cells}, types={'m': types.Array(cells)},
        output_vars={'y': 'y'})
    env = {'m': ranges.Range(0, 15), 'a': ranges.Range(0, cells - 1),
           'j': ranges.Range(0, steps - 1), 'y': ranges.Range(0, 15)}
    history_lengths = []
    original = FunctionalArray._read_history

    def history(self):
        if self._read_blocks is None:
            history_lengths.append(len(self.writes))
        return original(self)

    monkeypatch.setattr(FunctionalArray, '_read_history', history)
    compact = encoder._encode_derived_step(proc, env=env, tag='bounded-history',
        _validation=encoder._procedure_validation(proc, compact_arrays=True))
    assert history_lengths and max(history_lengths) <= cells + 1
    assert sum(history_lengths) <= (steps + 1) * (cells + 1)
    # State naming must retain its equations, not replace the old history with
    # an unconstrained array. Check every cell under arbitrary shared pre-state.
    plain = encoder.encode_step(proc, env=env, pre=compact.pre, tag='history-control')
    different = [compact.ok != plain.ok, compact.error != plain.error]
    for name in plain.post:
        for a, b in zip(_leaves(plain.post[name]), _leaves(compact.post[name]), strict=True):
            a, b = encoder.widen(a, b)
            different.append(a != b)
    solver = z3.Solver()
    solver.add(compact.constraint, plain.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_array_expressions_preserve_every_successor_without_boundary_definitions(private_context):
    proc, env = _procedure(16)
    template = StepTemplate(proc, env)
    if private_context:
        template = template.translate(z3.Context())
    bound = template.encode()
    direct = template.instantiate(bound.pre)
    assert not direct.definitions
    differences = [bound.ok != direct.ok, bound.error != direct.error,
                   bound.hits['target'] != direct.hits['target']]
    for name in proc.state:
        differences.extend(a != b for a, b in zip(_leaves(bound.state_post[name]),
                                                  _leaves(direct.state_post[name]), strict=True))
    solver = z3.Solver(ctx=template.context)
    solver.add(bound.constraint, direct.constraint, z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_local_array_observations_preserve_arbitrary_entry_cells_and_writes(private_context):
    proc, env = _procedure(16)
    template = StepTemplate(proc, env)
    if private_context:
        template = template.translate(z3.Context())
    full = template.encode()
    local = template.project_local({'hit': ('target',)}).encode(full.pre)
    assert set(local.pre) == set(full.pre)
    assert not local.state_post and not local.definitions
    roots = (local.constraint, local.hits['hit'], local.ok, local.error)
    _qfbv(roots)
    solver = z3.Solver(ctx=template.context)
    solver.add(full.constraint, local.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(full.hits['target'] != local.hits['hit'],
                     full.ok != local.ok, full.error != local.error))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('variant', ['fill', 'partial', 'different-guard', 'different-value'])
def test_fill_template_preserves_all_cells_without_reinstantiating_full_store_loop(monkeypatch, private, variant):
    from behave.sim.symbolic.template_arrays import TemplateArrays

    length = 9
    array = FunctionalArray.parameter(length, 'fill_before', 8)
    initial = array
    active, other = z3.Bools('fill_active fill_other')
    value = z3.BitVec('fill_data', 8)
    for i in range(length - int(variant == 'partial')):
        array = array.store(z3.BitVecVal(i, 5), value + 1 if variant == 'different-value' and i == 3 else value,
                            other if variant == 'different-guard' and i == 3 else active)
    at = z3.BitVec('fill_later_address', 6)
    array = array.store(at, z3.BitVecVal(-17, 8), other)
    recipes = TemplateArrays()
    image = recipes.term(array)
    parameters = (initial._base.value, active, other, value, at)
    image = z3.substitute(image, *((p, z3.Var(i, p.sort())) for i, p in enumerate(parameters)))
    context = z3.Context() if private else z3.main_ctx()
    if private:
        image = image.translate(context)
    plan = recipes.prepare(image)
    cells = [z3.BitVec(f'fill_cell{i}', 8, ctx=context) for i in range(length)]
    enabled, alt = z3.Bools('fill_enabled fill_alt', ctx=context)
    data, address = z3.BitVec('fill_new', 8, ctx=context), z3.BitVec('fill_address', 6, ctx=context)
    before = FunctionalArray.from_cells(cells)
    store, calls = FunctionalArray.store, []

    def counted(*args):
        calls.append(None)
        return store(*args)

    with monkeypatch.context() as patch:
        patch.setattr(FunctionalArray, 'store', counted)
        actual = finish_construction(plan.instantiate_steps((before, enabled, alt, data, address)))
    if variant == 'fill':
        assert len(calls) == 1  # The later alias, not all reset/fill cells.
    expected = []
    for i, old in enumerate(cells):
        cell = old
        if i < length - int(variant == 'partial'):
            cell = z3.If(alt if variant == 'different-guard' and i == 3 else enabled,
                         data + 1 if variant == 'different-value' and i == 3 else data, old)
        expected.append(z3.If(z3.And(alt, address == i), z3.BitVecVal(-17, 8, ctx=context), cell))
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(*(actual.select(z3.BitVecVal(i, 5, ctx=context)) != cell
                      for i, cell in enumerate(expected))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
def test_fill_coalescing_preserves_intermediate_observations(private):
    procedure = ir.Procedure(('m',), (), ('j',), (), ir.For('j', ir.Lit(4), ir._sequence((
        ir.If(ir.Op('ne', ir.Sel(ir.Var('m'), ir.Var('j')), ir.Lit(0)),
              ir.Mark('before_clear'), ir.Skip()),
        ir.Store('m', ir.Var('j'), ir.Lit(0)),
        ir.If(ir.Op('ne', ir.Sel(ir.Var('m'), ir.Var('j')), ir.Lit(0)),
              ir.Mark('after_clear'), ir.Skip()),
    ))), types={'m': types.Array(4)})
    environment = {'m': ranges.Range(-8, 7)}
    plain = encoder.encode_step(procedure, env=environment)
    template = StepTemplate(procedure, environment)
    if private:
        context = z3.Context()
        template = template.translate(context)
        before = {'m': [value.translate(context) for value in plain.pre['m']]}
        translate = lambda value: value.translate(context)
    else:
        before, translate = plain.pre, lambda value: value
    compiled = template.encode(before)
    solver = z3.Solver(ctx=template.context)
    solver.add(translate(plain.constraint), compiled.constraint)
    solver.push()
    solver.add(compiled.hits['before_clear'])
    assert solver.check() == z3.sat  # A final zero array does not erase earlier hits.
    solver.pop()
    differences = [compiled.ok != translate(plain.ok),
                   *(compiled.hits[name] != translate(plain.hits[name]) for name in plain.hits)]
    for i, cell in enumerate(plain.state_post['m']):
        actual = compiled.state_post['m'].select(z3.BitVecVal(i, 4, ctx=template.context))
        left, right = encoder.widen(actual, translate(cell))
        differences.append(left != right)
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_array_template_cancellation_does_not_modify_prepared_image(private_context):
    proc, env = _procedure(16)
    template = StepTemplate(proc, env)
    if private_context:
        template = template.translate(z3.Context())
    image = template._image
    checks = []

    def cancel():
        checks.append(None)
        if len(checks) == 12:
            raise TimeoutError('query budget exhausted inside array instantiation')

    with pytest.raises(TimeoutError, match='inside array instantiation'):
        template.encode(check=cancel)
    assert len(checks) == 12
    assert template._image is image
    plain = template.encode()
    resumed = template.encode(plain.pre, check=lambda: checks.append(None))
    assert len(checks) > 12
    solver = z3.Solver(ctx=template.context)
    solver.add(plain.constraint, resumed.constraint)
    differences = [plain.hits['target'] != resumed.hits['target'], plain.ok != resumed.ok]
    for name in proc.state:
        differences.extend(a != b for a, b in zip(_leaves(plain.state_post[name]),
            _leaves(resumed.state_post[name]), strict=True))
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


def test_scalar_template_honors_cancellation_before_instantiation():
    proc = ir.Procedure(('q',), (), (), (), ir.Skip(), initial={'q': 0})
    template = StepTemplate(proc, {'q': ranges.Range(0, 7)})

    def cancel():
        raise TimeoutError('cancel scalar query')

    with pytest.raises(TimeoutError, match='cancel scalar query'):
        template.encode(check=cancel)


def test_array_template_walk_does_not_wrap_each_scalar_edge(monkeypatch):
    from behave.sim.symbolic.template_arrays import TemplateArrays

    arrays = TemplateArrays()
    parameter = z3.Var(0, z3.BitVecSort(8))
    stored = FunctionalArray.uniform(4, z3.BitVecVal(3, 8)).store(
        z3.BitVecVal(2, 4), parameter, z3.BoolVal(True))
    array = arrays.term(stored)
    shared = parameter + 1
    fields = [z3.Select(array, z3.BitVecVal(2, 4)),
              z3.Select(array, z3.BitVecVal(1, 4)), shared + shared]
    image = z3.Function('_step_fields', *(field.sort() for field in fields), z3.BoolSort())(*fields)

    def wrapped_children(_term):
        raise AssertionError('scalar edge traversal created Python wrappers')

    with monkeypatch.context() as patch:
        patch.setattr(z3.ExprRef, 'children', wrapped_children)
        actual = arrays.instantiate(image, [z3.BitVecVal(5, 8)])
    assert [z3.simplify(value).as_long() for value in actual] == [5, 3, 12]


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [8, 96])
@pytest.mark.parametrize('symbolic', [False, True])
@pytest.mark.parametrize('wrapped', [False, True])
def test_demanded_scalar_roots_share_substitution_without_sharing_values(
        monkeypatch, private, count, symbolic, wrapped):
    from z3 import z3core as native
    from behave.sim.symbolic.template_arrays import TemplateArrays

    arrays = TemplateArrays()
    parameter = z3.Var(0, z3.BitVecSort(8))
    shared = parameter
    fields = []
    for i in range(count):
        shared = shared * 3 + i
        fields.extend((shared, shared == i))
    # Mixed sorts, duplicates and false roots must survive packing. The
    # finite read is a separate interpreter barrier, never an SMT array.
    finite = arrays.term(FunctionalArray.uniform(4, z3.BitVecVal(7, 8)))
    fields.extend((z3.BoolVal(False), fields[0], z3.Select(finite, z3.BitVecVal(1, 4))))
    image = z3.Function('_step_fields', *(term.sort() for term in fields), z3.BoolSort())(*fields)
    context = z3.Context() if private else z3.main_ctx()
    if private:
        image = image.translate(context)
        fields = [term.translate(context) for term in fields]
    plan = arrays.prepare(image)
    original, specialize = native.Z3_substitute, z3.substitute
    decoded = None
    for turn in (3, 9):
        argument = z3.BitVec(f'batch_input_{turn}', 8, ctx=context) if symbolic else z3.BitVecVal(turn, 8, ctx=context)
        if wrapped:
            argument = z3.ZeroExt(1, z3.Extract(6, 0, argument))
        calls, specializations = [], []
        def counted(*args):
            calls.append(None)
            return original(*args)
        def specialized(*args):
            specializations.append(None)
            return specialize(*args)
        with monkeypatch.context() as patch:
            patch.setattr(native, 'Z3_substitute', counted)
            patch.setattr(z3, 'substitute', specialized)
            actual = finish_construction(plan.instantiate_steps((argument,)))
        # Keep one structural decoder for successive literal bindings too.
        # Values stay fresh. Symbolic siblings share one substitution; ground
        # nodes are evaluated once through the per-instantiation value table.
        assert not specializations
        assert len(calls) == int(symbolic)
        assert plan.nodes
        if decoded is not None:
            assert all(plan.nodes[key] is node for key, node in decoded.items())
        decoded = dict(plan.nodes)
        expected = [z3.substitute_vars(term, argument) for term in fields[:-1]]
        expected.append(z3.BitVecVal(7, 8, ctx=context))
        assert len(actual) == len(expected)
        solver = z3.Solver(ctx=context)
        solver.add(z3.Or(*(left != right for left, right in zip(actual, expected, strict=True))))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [16, 128])
def test_ground_cones_share_values_across_symbolic_control_boundaries(monkeypatch, private, count):
    from z3 import z3core as native
    from behave.sim.symbolic.template_arrays import TemplateArrays

    parameter = z3.Var(0, z3.BitVecSort(16))
    guard = z3.Var(1, z3.BoolSort())
    value, fields = parameter, []
    for index in range(count):
        value = (value * 3 + index) ^ z3.LShR(value, 2)
        fields.append(z3.If(guard, value, value + 1))
    image = z3.Function('_step_fields', *(field.sort() for field in fields), z3.BoolSort())(*fields)
    context = z3.Context() if private else z3.main_ctx()
    if private:
        image = image.translate(context)
        fields = [field.translate(context) for field in fields]
    plan = TemplateArrays().prepare(image)
    active = z3.Bool('ground_cone_guard', ctx=context)
    original = native.Z3_update_term
    for seed in (7, 13):
        updates = []
        def updated(ctx, term, *args):
            updates.append(native.Z3_get_ast_id(ctx, term))
            return original(ctx, term, *args)
        def repeated_cone(*args):
            raise AssertionError('ground cone must use memoized child values')
        with monkeypatch.context() as patch:
            patch.setattr(native, 'Z3_substitute', repeated_cone)
            patch.setattr(native, 'Z3_update_term', updated)
            result = finish_construction(plan.instantiate_steps((z3.BitVecVal(seed, 16, ctx=context), active)))
        assert len(updates) == len(set(updates))
        expected = [z3.substitute_vars(field, z3.BitVecVal(seed, 16, ctx=context), active)
                    for field in fields]
        solver = z3.Solver(ctx=context)
        solver.add(z3.Or(*(left != right for left, right in zip(result, expected, strict=True))))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('selected', [0, 1])
def test_template_literal_branch_does_not_instantiate_inactive_array(private_context, selected):
    from behave.sim.symbolic.template_arrays import TemplateArrays

    arrays = TemplateArrays()
    condition = z3.Var(0, z3.BitVecSort(8)) + 1 == 1
    choices = [FunctionalArray.uniform(4, z3.BitVecVal(value, 8)) for value in (7, 19)]
    terms = [arrays.term(array) for array in choices]
    image_array = arrays._call('choose', 4, 8, (condition, *terms), FunctionalArray.choose)
    # A second scalar branch also owns an array read. Both branches must use
    # the substituted condition, not assume a symbolic input's seed value.
    read = z3.If(condition, z3.Select(terms[0], z3.BitVecVal(2, 4)),
                 z3.Select(terms[1], z3.BitVecVal(1, 4)))
    roots = [z3.Select(image_array, z3.BitVecVal(0, 4)), read]
    image = z3.Function('_step_fields', *(term.sort() for term in roots), z3.BoolSort())(*roots)
    built = []
    original = arrays.constructors[str(terms[0].decl().name())]

    def construct(value):
        built.append(value.as_long())
        return original(value)

    arrays.constructors[str(terms[0].decl().name())] = construct
    if private_context:
        image = image.translate(z3.Context())
    result = arrays.instantiate(image, [z3.BitVecVal(selected, 8, ctx=image.ctx)])
    expected = 7 if selected == 0 else 19
    assert built == [expected]
    assert [z3.simplify(value).as_long() for value in result] == [expected, expected]


def test_mixed_scalar_region_keeps_array_guard_short_circuit():
    from behave.sim.symbolic.template_arrays import TemplateArrays

    arrays = TemplateArrays()
    x, y = (z3.Var(i, z3.BitVecSort(8)) for i in (0, 1))
    guard = z3.Or(x == 0, y == 0)
    calculation = z3.If(guard, y, z3.BitVecVal(19, 8)) + 1
    choices = [arrays.term(FunctionalArray.uniform(4, z3.BitVecVal(v, 8))) for v in (7, 19)]
    selected = arrays._call('choose', 4, 8, (guard, *choices), FunctionalArray.choose)
    read = z3.Select(selected, z3.BitVecVal(0, 4))
    image = z3.Function('_step_fields', calculation.sort(), read.sort(), z3.BoolSort())(calculation, read)
    built = []
    name = str(choices[0].decl().name())
    original = arrays.constructors[name]

    def construct(value):
        built.append(value.as_long())
        return original(value)

    arrays.constructors[name] = construct
    symbolic = z3.BitVec('remaining_input', 8)
    actual = arrays.instantiate(image, [z3.BitVecVal(0, 8), symbolic])
    assert built == [7]
    assert z3.is_true(z3.simplify(actual[0] == symbolic + 1))
    assert z3.simplify(actual[1]).as_long() == 7


@pytest.mark.parametrize('enabled', [False, True])
def test_template_disabled_store_does_not_construct_its_value(enabled):
    from behave.sim.symbolic.template_arrays import TemplateArrays

    arrays = TemplateArrays()
    base = FunctionalArray.uniform(4, z3.BitVecVal(3, 8))
    other = arrays.term(FunctionalArray.uniform(4, z3.BitVecVal(19, 8)))
    value = z3.Select(other, z3.BitVecVal(1, 4))
    updated = base.store(z3.BitVecVal(2, 4), value, z3.Var(0, z3.BoolSort()))
    root = z3.Select(arrays.term(updated), z3.BitVecVal(2, 4))
    image = z3.Function('_step_fields', root.sort(), z3.BoolSort())(root)
    constructor = str(other.decl().name())
    original = arrays.constructors[constructor]
    built = []

    def construct(value):
        built.append(value.as_long())
        return original(value)

    arrays.constructors[constructor] = construct
    result = arrays.instantiate(image, [z3.BoolVal(enabled)])
    assert (19 in built) == enabled
    assert z3.simplify(result[0]).as_long() == (19 if enabled else 3)


def test_template_symbolic_choice_keeps_both_values_and_shared_live_root():
    from behave.sim.symbolic.template_arrays import TemplateArrays

    arrays = TemplateArrays()
    condition = z3.Var(0, z3.BoolSort())
    yes = FunctionalArray.uniform(4, z3.BitVecVal(7, 8))
    no = FunctionalArray.uniform(4, z3.BitVecVal(19, 8))
    selected = arrays.term(FunctionalArray.choose(condition, yes, no))
    independent = z3.Select(arrays.term(no), z3.BitVecVal(0, 4))
    roots = [z3.Select(selected, z3.BitVecVal(0, 4)), independent]
    image = z3.Function('_step_fields', *(term.sort() for term in roots), z3.BoolSort())(*roots)
    guard = z3.Bool('template_symbolic_guard')
    result = arrays.instantiate(image, [guard])
    solver = z3.Solver()
    expected = z3.If(guard, z3.BitVecVal(7, 8), z3.BitVecVal(19, 8))
    solver.add(z3.Or(result[0] != expected, result[1] != 19))
    assert solver.check() == z3.unsat
    result = arrays.instantiate(image, [z3.BoolVal(True)])
    assert [z3.simplify(value).as_long() for value in result] == [7, 19]


def test_array_length_is_not_inferred_from_address_width():
    # Three and four cells can share one address carrier; shape is task data.
    proc = ir.Procedure(('a', 'b'), (), (), (), ir.Skip(),
        initial={'a': [0]*3, 'b': [0]*4}, types={'a': types.Array(3), 'b': types.Array(4)})
    template = StepTemplate(proc, {'a': ranges.Range(0, 7), 'b': ranges.Range(0, 7)})
    values = template.encode().state_post
    assert values['a'].length == 3 and values['b'].length == 4


def test_native_array_binding_checks_every_word_but_shares_repeated_constants(monkeypatch):
    original = z3.BitVecVal
    calls = []
    def literal(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(z3, 'BitVecVal', literal)
    cells = [0]*65536
    cells[-1] = 9
    value = encoder.encode_concrete_value(cells, ranges.Range(0, 15), 'm', kind=types.Array(len(cells)))
    assert isinstance(value, FunctionalArray) and len(calls) < 10
    assert z3.simplify(value.select(original(65535, 18))).as_long() == 9
    for invalid in (16, -1, True):
        cells[-1] = invalid
        with pytest.raises(encoder.EncodingError):
            encoder.encode_concrete_value(cells, ranges.Range(0, 15), 'm', kind=types.Array(len(cells)))


@pytest.mark.parametrize('cells', [(4, 7), (-7, -4)])
@pytest.mark.parametrize('base', ['uniform', 'memory'])
def test_fully_overwritten_compact_base_is_not_a_live_state_value(cells, base):
    from behave.sim.symbolic.uniform_array import UniformArray

    value = UniformArray.uniform(2, 0) if base == 'uniform' else UniformArray.memory(2, 'm', 8)
    value = value.store(0, cells[0]).store(1, cells[1])
    rng = ranges.range_of_value(value)
    assert (rng.lo, rng.hi) == (min(cells), max(cells))
    encoded = encoder.encode_concrete_value(value, rng, 'm', kind=types.Array(2))
    for index, expected in enumerate(cells):
        actual = z3.simplify(encoded.select(z3.BitVecVal(index, 3)))
        assert actual.as_signed_long() == expected


def test_fully_overwritten_boolean_base_does_not_reintroduce_false():
    from behave.sim.symbolic.uniform_array import UniformArray

    value = UniformArray.uniform(2, False).store(0, True).store(1, True)
    encoded = encoder.encode_concrete_value(
        value, ranges.range_of_value(value), 'm', kind=types.Array(2, types.BOOLEAN))
    assert [z3.simplify(encoded.select(z3.BitVecVal(i, 3))).as_long() for i in range(2)] == [1, 1]


def test_a_live_compact_base_or_overwrite_still_needs_its_declared_carrier():
    from behave.sim.symbolic.uniform_array import UniformArray

    for value in (UniformArray.uniform(2, 0).store(0, 4),
                  UniformArray.uniform(2, 0).store(0, 4).store(1, 9)):
        with pytest.raises(encoder.EncodingError, match='outside its declared range'):
            encoder.encode_concrete_value(value, ranges.Range(4, 7), 'm', kind=types.Array(2))


@pytest.mark.parametrize('value,kind', [
    (-3, types.INTEGER), (True, types.BOOLEAN),
    ([[1, -2], [3, 0]], types.Array(2, types.Array(2))),
    ([True, False], types.Array(2, types.BOOLEAN)),
])
def test_concrete_boundary_uses_the_query_context_recursively(value, kind):
    context = z3.Context()
    encoded = encoder.encode_concrete_value(value, ranges.Range(-3, 3), 'state',
                                             kind=kind, context=context)
    solver = z3.Solver(ctx=context)
    assert solver.check() == z3.sat
    assert encoder.decode_value(solver.model(), encoded, kind) == value


@pytest.mark.parametrize('base', ['uniform', 'memory'])
def test_compact_concrete_boundary_keeps_base_and_stores_in_query_context(base):
    from behave.sim.symbolic.uniform_array import UniformArray

    value = (UniformArray.uniform(4, 0) if base == 'uniform' else
             UniformArray.memory(4, 'query_memory', 8)).store(2, 19)
    context = z3.Context()
    encoded = encoder.encode_concrete_value(value, ranges.Range(0, 255), 'm',
        kind=types.Array(4), context=context)
    selected = [encoded.select(z3.BitVecVal(i, 4, ctx=context)) for i in range(4)]
    assert all(term.ctx == context for term in selected)
    assert [z3.simplify(term).as_long() for term in selected] == list(value)


@pytest.mark.parametrize('offset', [0, 16])
def test_recursive_public_output_keeps_leaf_values_and_contract_checks(offset):
    from behave.sim.symbolic.value_binding import SolverLeafPath, ValueBinding
    from behave.sim.core.contract import ValueType

    packet = ValueBinding('packet', ValueType.array(2, ValueType.integer(FiniteType(4))),
        tuple(((i,), SolverLeafPath('cells', (i,))) for i in range(2)))
    proc = ir.Procedure(('m',), ('a',), ('out',), ('packet',),
        ir.Assign('out', ir.Op('arraylit', ir.Op('arraylit', ir.Lit(1),
            ir.Op('add', ir.Var('a'), ir.Lit(offset))))),
        initial={'m': [0, 0]}, types={'m': types.Array(2), 'out': types.Array(1, types.Array(2))},
        output_vars={'cells': 'out'},
        signature=ProcedureSignature((('a', FiniteType(4)),), (('packet', packet),)))
    env = dict.fromkeys(('m', 'a', 'out'), ranges.Range(0, 31))
    env['a'] = ranges.Range(0, 15)
    plain = encoder.encode_step(proc, env=env)
    compact = encoder._encode_step(proc, env=env, pre=plain.pre,
        _procedure_validation=encoder._procedure_validation(proc, compact_arrays=True))
    template = StepTemplate(proc, env).encode(plain.pre)
    assert isinstance(template.post['cells'], list)
    assert plain.output_checks.keys() == compact.output_checks.keys()
    different = [plain.output_checks[k] != compact.output_checks[k] for k in plain.output_checks]
    for a, b in zip(_leaves(plain.post['cells']), _leaves(template.post['cells']), strict=True):
        left, right = encoder.widen(a, b)
        different.append(left != right)
    solver = z3.Solver()
    solver.add(plain.constraint, compact.constraint, template.constraint, z3.Or(*different))
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(compact.constraint, z3.Not(z3.And(*compact.output_checks.values())))
    assert solver.check() == (z3.sat if offset else z3.unsat)
