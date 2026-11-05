"""Template normalization preserves shared associative bit-vector prefixes."""

import pytest
import z3

from behave.sim.symbolic import encoder, ir, ranges
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import StepTemplate
from behave.sim.core.contract import FiniteType


def _prefixes(count, operation='xor'):
    inputs = tuple(f'a{i}' for i in range(count))
    commands = [ir.Assign('q', ir.Lit(0 if operation != 'and' else 255))]
    for index, name in enumerate(inputs):
        commands.extend((
            ir.Assign('q', ir.Op(operation, ir.Var('q'), ir.Var(name))),
            ir.If(ir.Op('eq', ir.Var('q'), ir.Lit(index)),
                  ir.Mark(f'prefix{index}'), ir.Skip())))
    proc = ir.Procedure(('q',), inputs, (), (), ir._sequence(commands),
        initial={'q': 0}, signature=ProcedureSignature(
            tuple((name, FiniteType(8)) for name in inputs), ()))
    return proc, {name: ranges.Range(0, 255) for name in ('q', *inputs)}


def _edges(expression):
    pending, seen, count = [expression], set(), 0
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        if z3.is_app(term):
            count += term.num_args()
            pending.extend(term.children())
    return count


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('width', [4, 16, 32])
def test_shared_region_normalization_restores_every_original_term(private, width):
    from behave.sim.symbolic.step_template import _normalize_shared
    from z3.z3util import get_vars

    ctx = z3.Context() if private else z3.main_ctx()
    value = z3.BitVec('normalization_value', width, ctx=ctx)
    choose = z3.Bool('normalization_choice', ctx=ctx)
    terms = [value]
    for index in range(5):
        value = z3.If(z3.Extract(width - 1, width - 1, value) == 0, value << 1, value)
        terms.extend((value, z3.SignExt(3, value), z3.Extract(width - 2, 0, value)))
    terms.extend((z3.If(choose, value, value), value - value == 0, value ^ value))
    pack = z3.FreshFunction(*(term.sort() for term in terms), z3.BoolSort(ctx=ctx))
    original = pack(*terms)
    normalized = _normalize_shared(original)
    assert normalized.ctx == ctx and normalized.num_args() == len(terms)
    assert {term.get_id() for term in get_vars(normalized)} <= {
        term.get_id() for term in get_vars(original)}
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(*(old != new for old, new in zip(terms, normalized.children(), strict=True))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('count', [16, 64, 128])
def test_shared_region_normalization_keeps_selected_prefixes_shared(count):
    from behave.sim.symbolic.step_template import _normalize_shared

    value = z3.BitVec('shared_prefix_value', 32)
    terms = []
    for _ in range(count):
        value = z3.If(z3.Extract(31, 31, value) == 0, value << 1, value)
        terms.extend((value, z3.Extract(23, 0, value)))
    pack = z3.FreshFunction(*(term.sort() for term in terms), z3.BoolSort())
    image = pack(*terms)
    normalized = _normalize_shared(image)
    assert _edges(normalized) < 25 * count
    assert _edges(normalized) < _edges(z3.simplify(image, flat=False))


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [1, 16, 64])
def test_shared_negations_do_not_hide_control_identities(private, count):
    from behave.sim.symbolic.step_template import _normalize_shared

    ctx = z3.Context() if private else z3.main_ctx()
    value = z3.Bool('shared_control_entry', ctx=ctx)
    fields = []
    for index in range(count):
        guard = z3.Bool(f'shared_control_guard_{index}', ctx=ctx)
        value = z3.And(value, guard)
        negative = z3.Not(value)
        fields.extend((value, negative, z3.Not(negative),
                       z3.And(value, negative), z3.Or(value, negative)))
    pack = z3.FreshFunction(*(term.sort() for term in fields), z3.BoolSort(ctx=ctx))
    normalized = _normalize_shared(pack(*fields))
    for index in range(count):
        assert normalized.arg(5 * index).eq(normalized.arg(5 * index + 2))
        assert z3.is_false(normalized.arg(5 * index + 3))
        assert z3.is_true(normalized.arg(5 * index + 4))
    # Do not trade the cancelled negations for flattened, quadratic prefixes.
    assert _edges(normalized) <= 8 * count
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(*(old != new for old, new in zip(
        fields, normalized.children(), strict=True))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('kind', ['bv', 'integer', 'array'])
def test_shared_normalization_preserves_native_node_ownership(private, kind):
    import gc
    from behave.sim.symbolic.step_template import _normalize_shared

    ctx = z3.Context() if private else z3.main_ctx()
    guard = z3.Bool('shared_native_guard', ctx=ctx)
    index = z3.Int('shared_native_index', ctx=ctx)
    if kind == 'array':
        value = z3.Array('shared_native_value', z3.IntSort(ctx), z3.IntSort(ctx))
        update = lambda term, i: z3.Store(term, index + i, z3.Select(term, index) + 1)
    elif kind == 'integer':
        value = z3.Int('shared_native_value', ctx=ctx)
        update = lambda term, i: term * 2 + i
    else:
        value = z3.BitVec('shared_native_value', 16, ctx=ctx)
        update = lambda term, i: (term << 1) + i
    roots = []
    for i in range(12):
        value = z3.If(guard, update(value, i), value)
        roots.extend((value, z3.If(guard, value, value), value == value))
    pack = z3.FreshFunction(*(term.sort() for term in roots), z3.BoolSort(ctx))
    original = pack(*roots)
    normalized = _normalize_shared(original)
    gc.collect()  # Temporary wrappers/vector have gone; returned roots must own the DAG.
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(*(old != new for old, new in zip(roots, normalized.children(), strict=True))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('specialized', [False, True])
def test_prefix_observations_keep_linear_shared_dag(specialized, monkeypatch):
    simplify = z3.simplify

    def no_whole_template_rewrite(expression, *args, **kwargs):
        if z3.is_app(expression) and str(expression.decl().name()) == '_step_fields':
            pytest.fail('whole-datapath simplification runs before the query budget')
        return simplify(expression, *args, **kwargs)

    monkeypatch.setattr(z3, 'simplify', no_whole_template_rewrite)
    proc, env = _prefixes(64)
    template = StepTemplate(proc, env)
    if specialized:
        template = template.specialize_inputs({'a0': 7})
    # Every intermediate prefix is observable. Flattening each into all its
    # operands makes this quadratic even though the original circuit is linear.
    assert _edges(template._image) < 20 * len(proc.inputs)
    assert len(template._layout['hits']) == len(proc.inputs)


@pytest.mark.parametrize('operation', ['add', 'xor', 'or', 'and'])
@pytest.mark.parametrize('normalized', [False, True])
@pytest.mark.parametrize('specialized', [False, True])
def test_shared_prefixes_preserve_state_hits_and_input_domain(operation, normalized, specialized):
    proc, env = _prefixes(6, operation)
    plain = encoder.encode_step(proc, env=env)
    template = StepTemplate(proc, env)
    if specialized:
        template = template.specialize_inputs({'a0': 7})
    if normalized:
        template = template.normalized()
    compiled = template.encode(plain.pre)
    solver = z3.Solver()
    solver.add(plain.constraint, compiled.constraint)
    if specialized:
        solver.add(plain.pre['a0'] == 7)
    solver.add(z3.Or(plain.ok != compiled.ok, plain.error != compiled.error,
        plain.state_post['q'] != compiled.state_post['q'],
        *(plain.hits[name] != compiled.hits[name] for name in plain.hits)))
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(compiled.constraint, compiled.pre['a0'] == (-1 if not specialized else 8))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('operation', ['add', 'xor', 'or', 'and'])
@pytest.mark.parametrize('count', [16, 64, 256])
@pytest.mark.parametrize('private', [False, True])
def test_normalized_prefixes_preserve_linear_edges(operation, count, private):
    proc, env = _prefixes(count, operation)
    template = StepTemplate(proc, env)
    if private:
        template = template.translate(z3.Context())
    normalized = template.normalized()
    assert _edges(normalized._image) < 40 * count
    assert len(normalized._layout['hits']) == count
    # Normalization may simplify formulas, not mutate the original template
    # or change its context/parameter ownership.
    assert normalized.context is template.context
    assert normalized._parameters is template._parameters


def test_normalization_still_removes_constant_control_and_unused_dependencies():
    proc = ir.Procedure(('q',), ('a',), (), (), ir._sequence((
        ir.If(ir.Op('eq', ir.Op('sub', ir.Var('a'), ir.Var('a')), ir.Lit(0)),
              ir.Mark('always'), ir.Mark('never')),
        ir.Assign('q', ir.Op('xor', ir.Var('a'), ir.Var('a'))))),
        initial={'q': 0}, signature=ProcedureSignature((('a', FiniteType(8)),), ()))
    view = StepTemplate(proc, dict.fromkeys(('q', 'a'), ranges.Range(0, 255))).normalized()
    encoded = view.instantiate()
    assert z3.is_true(encoded.hits['always']) and z3.is_false(encoded.hits['never'])
    assert z3.is_bv_value(encoded.state_post['q']) and encoded.state_post['q'].as_long() == 0
    for index in (*view._layout['hits'].values(), view._layout['state_post']['q']):
        assert view._dependencies[index] == 0


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('binding', ['concrete', 'symbolic', 'mixed'])
def test_native_scalar_binding_matches_interpreter_and_keeps_values_fresh(private, binding):
    from copy import copy

    proc, env = _prefixes(6)
    view = StepTemplate(proc, env).normalized()
    if private:
        view = view.translate(z3.Context())
    interpreted = copy(view)
    interpreted._arrays = copy(view._arrays)
    # Force the existing array-capable interpreter without adding any array
    # to the scalar formula. The unused recipe must never execute.
    def unused(*_args):
        raise AssertionError('scalar formula entered an absent array recipe')
    interpreted._arrays.constructors = {'unused': unused}
    interpreted._array_image = None
    original = view._image
    for seed in (3, 17):
        arguments = {name: (z3.BitVec(f'{name}_{seed}', value.size(), ctx=view.context)
                            if binding == 'symbolic' or binding == 'mixed' and name != 'a0'
                            else z3.BitVecVal(seed, value.size(), ctx=view.context))
                     for name, value in view._parameters.items()}
        actual, expected = view.instantiate(arguments), interpreted.instantiate(arguments)
        differences = [actual.constraint != expected.constraint,
                       actual.ok != expected.ok, actual.error != expected.error]
        for field in ('state_post', 'post', 'hits'):
            left, right = getattr(actual, field), getattr(expected, field)
            assert left.keys() == right.keys()
            differences.extend(a != b for name in left
                for a, b in zip(encoder._flatten_tree(left[name]),
                                encoder._flatten_tree(right[name]), strict=True))
        solver = z3.Solver(ctx=view.context)
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        assert view._image is original
        assert view._array_image is None


@pytest.mark.parametrize('private', [False, True])
def test_scalar_specialization_reuses_literals_not_symbolic_boundaries(private, monkeypatch):
    proc, env = _prefixes(6)
    view = StepTemplate(proc, env).normalized()
    if private:
        view = view.translate(z3.Context())
    simplify, rewrites = z3.simplify, []

    def record(expression, *args, **kwargs):
        if z3.is_app(expression) and str(expression.decl().name()) == '_step_fields':
            rewrites.append(expression)
            # Symbolic names belong to instances, never to the cached rewrite.
            assert 'instance_' not in expression.sexpr()
        return simplify(expression, *args, **kwargs)

    monkeypatch.setattr(z3, 'simplify', record)
    for index, literal in enumerate((3, 3, 17, 17, 3)):
        before = {name: z3.BitVec(f'instance_{index}_{name}', value.size(), ctx=view.context)
                  for name, value in view._parameters.items()}
        # Use a narrower signed carrier too: the original binder sign extends.
        before['a0'] = z3.BitVecVal(literal, 5, ctx=view.context)
        actual = view.instantiate(before)
        arguments = [z3.SignExt(value.size() - before[name].size(), before[name])
                     for name, value in view._parameters.items()]
        expected = z3.substitute_vars(view._image, *arguments)
        actual_roots = {view._layout['constraint']: actual.constraint,
                        view._layout['ok']: actual.ok, view._layout['error']: actual.error}
        for field in ('state_post', 'post', 'hits'):
            actual_roots.update((slot, getattr(actual, field)[name])
                                for name, slot in view._layout[field].items())
        solver = z3.Solver(ctx=view.context)
        solver.add(z3.Or(*(root != expected.arg(slot) for slot, root in actual_roots.items())))
        assert solver.check() == z3.unsat
    assert len(rewrites) == 3  # Only the most recent specialization is retained.


@pytest.mark.parametrize('change', ['projection', 'translation', 'specialization', 'normalization'])
def test_scalar_specialization_does_not_leak_across_template_views(change):
    proc, env = _prefixes(6)
    original = StepTemplate(proc, env).normalized()
    original.instantiate({'a0': z3.BitVecVal(3, original._parameters['a0'].size())})
    if change == 'projection':
        view = original.project({'only': ('prefix5',)}, outputs=())
    elif change == 'translation':
        view = original.translate(z3.Context())
    elif change == 'specialization':
        view = original.specialize_inputs({'a0': 7})
    else:
        view = original.normalized()
    assert view._scalar_image is None
    encoded = view.instantiate({'a0': z3.BitVecVal(3, view._parameters['a0'].size(), ctx=view.context)})
    if change == 'projection':
        assert set(encoded.hits) == {'only'}
    elif change == 'translation':
        assert encoded.constraint.ctx is view.context and view.context is not original.context
    elif change == 'specialization':
        solver = z3.Solver()
        solver.add(encoded.constraint)
        assert solver.check() == z3.unsat


def test_symbolic_wiring_binds_without_another_whole_template_rewrite(monkeypatch):
    proc = ir.Procedure(('q',), ('a',), (), (), ir._sequence((
        ir.If(ir.Op('eq', ir.Op('shr', ir.Var('a'), ir.Lit(4)), ir.Lit(0)),
              ir.Mark('low'), ir.Mark('high')), ir.Assign('q', ir.Var('a')))),
        initial={'q': 0}, signature=ProcedureSignature((('a', FiniteType(8)),), ()))
    view = StepTemplate(proc, dict.fromkeys(('q', 'a'), ranges.Range(0, 255))).normalized()
    simplify = z3.simplify

    def counted(term, *args, **kwargs):
        if z3.is_app(term) and str(term.decl().name()) == '_step_fields':
            pytest.fail('symbolic wiring must not specialize the whole datapath again')
        return simplify(term, *args, **kwargs)

    monkeypatch.setattr(z3, 'simplify', counted)
    for index in range(3):
        a = z3.Concat(z3.BitVecVal(0, 5), z3.BitVec(f'fresh_nibble_{index}', 4))
        encoded = view.instantiate({'a': a})
        solver = z3.Solver()
        solver.add(z3.Or(z3.Not(encoded.hits['low']), encoded.hits['high'],
                         encoded.state_post['q'] != a))
        assert solver.check() == z3.unsat
    assert view._scalar_image is None
