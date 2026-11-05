"""Observation closure eliminates only total, unobserved state definitions."""

import pytest
import z3

from behave.sim.symbolic import ir, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import StepTemplate, _leaves, project_templates
from behave.sim.core.contract import FiniteType

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('array', (False, True))
@pytest.mark.parametrize('failure', ('none', 'before', 'after'))
def test_local_selection_prunes_future_state_before_encoding(monkeypatch, private, array, failure):
    from behave.sim.symbolic import step_template
    from behave.sim.symbolic.bitvectors import widen

    product = ir.Op('mul', ir.Var('a'), ir.Var('b'))
    future = (ir.Store('memory', ir.Lit(0), product) if array else ir.Assign('x', product))
    stop = ir.If(ir.Op('eq', ir.Var('b'), ir.Lit(3)), ir.Fail('Stop', 'failure'), ir.Skip())
    body = ir._sequence((stop if failure == 'before' else ir.Skip(),
        ir.If(ir.Op('eq', ir.Var('x'), ir.Var('a')), ir.Mark('target'), ir.Skip()),
        ir.Assign('out', ir.Op('arraylit', ir.Var('x'))), future,
        stop if failure == 'after' else ir.Skip()))
    proc = ir.Procedure(('x', 'memory'), ('a', 'b'), (), ('out',), body,
        initial={'x': 0, 'memory': [0] * 8},
        types={'memory': types.Array(8), 'out': types.Array(1)},
        signature=ProcedureSignature((('a', FiniteType(4)), ('b', FiniteType(4))),
                                     (('out', FiniteType(4)),)))
    env = dict.fromkeys((*proc.state, *proc.inputs), ranges.Range(0, 15))
    full = StepTemplate(proc, env)
    encode, bodies = step_template._encode_step, []
    def record(procedure, *args, **kwargs):
        bodies.append(procedure.body)
        return encode(procedure, *args, **kwargs)
    monkeypatch.setattr(step_template, '_encode_step', record)
    local = StepTemplate.selected(proc, env, ('target',), outputs=('out',), successors=False)
    assert bodies and all(id(future) not in {id(c) for c in ir.walk(b)} for b in bodies)
    assert local.state_names == ()
    if private:
        context = z3.Context()
        full, local = full.translate(context), local.translate(context)
    actual = local.encode()
    expected = full.encode(actual.pre)
    solver = z3.Solver(ctx=local.context)
    solver.add(actual.constraint, expected.constraint)
    assert solver.check() == z3.sat
    left, right = widen(actual.post['out'][0], expected.post['out'][0])
    solver.add(z3.Or(actual.hits['target'] != expected.hits['target'],
                     actual.ok != expected.ok, actual.error != expected.error,
                     left != right))
    assert solver.check() == z3.unsat
    with pytest.raises(ValueError, match='successor state'):
        StepTemplate.selected(proc, env, ('target',), state_reads=('x',), successors=False)


@pytest.mark.parametrize('private', (False, True))
def test_local_selection_keeps_loop_carried_updates_and_late_failures(private):
    update = ir.Assign('x', ir.Op('and', ir.Op('add', ir.Var('x'), ir.Lit(1)), ir.Lit(7)))
    body = ir.For('i', 4, ir._sequence((
        ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(2)), ir.Mark('target'), ir.Skip()),
        update, ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(3)), ir.Fail('Stop', 'failure'), ir.Skip()))))
    proc = ir.Procedure(('x',), (), ('i',), (), body, initial={'x': 0},
                        signature=ProcedureSignature((), ()))
    env = {'x': ranges.Range(0, 7)}
    full, local = StepTemplate(proc, env), StepTemplate.selected(proc, env, ('target',), successors=False)
    if private:
        context = z3.Context()
        full, local = full.translate(context), local.translate(context)
    actual = local.encode()
    expected = full.encode(actual.pre)
    solver = z3.Solver(ctx=local.context)
    solver.add(actual.constraint, expected.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(actual.hits['target'] != expected.hits['target'],
                     actual.ok != expected.ok, actual.error != expected.error))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('failure', (False, True))
def test_selected_template_drops_overwritten_datapath_before_encoding(monkeypatch, private, failure):
    from behave.sim.symbolic import step_template

    dead = ir.Assign('x', ir.Op('mul', ir.Var('a'), ir.Var('b')))
    body = ir._sequence((dead,
        ir.If(ir.Op('ne', ir.Var('a'), ir.Lit(0)), ir.Assign('x', ir.Lit(1)), ir.Assign('x', ir.Lit(2))),
        ir.If(ir.Op('ne', ir.Var('b'), ir.Lit(0)), ir.Fail('Stop', 'failure'), ir.Skip()) if failure else ir.Skip(),
        ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(1)), ir.Mark('target'), ir.Skip())))
    proc = ir.Procedure(('x',), ('a', 'b'), (), (), body, initial={'x': 0},
        signature=ProcedureSignature((('a', FiniteType(3)), ('b', FiniteType(3))), ()))
    env = {name: ranges.Range(0, 7) for name in ('a', 'b', 'x')}
    full = StepTemplate(proc, env)
    encode, bodies = step_template._encode_step, []

    def record(procedure, *args, **kwargs):
        bodies.append(procedure.body)
        return encode(procedure, *args, **kwargs)

    monkeypatch.setattr(step_template, '_encode_step', record)
    selected = StepTemplate.selected(proc, env, ('target',))
    assert bodies and all(id(dead) not in {id(c) for c in ir.walk(b)} for b in bodies)
    assert not any(isinstance(c, ir.Assign) and isinstance(c.expr, ir.Op) and c.expr.op == 'mul'
                   for body in bodies for c in ir.walk(body))
    if private:
        context = z3.Context()
        full, selected = full.translate(context), selected.translate(context)
    expected = full.encode()
    # The removed multiplication widened the full template's unused state
    # carrier. Keep entry x independent in both formulas: the proof below
    # requires equal hits/errors for ALL entry values, not just one sample.
    observed = selected.encode({name: expected.pre[name] for name in proc.inputs})
    solver = z3.Solver(ctx=full.context)
    solver.add(expected.constraint, observed.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(expected.hits['target'] != observed.hits['target'],
                     expected.ok != observed.ok, expected.error != observed.error))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('successors', (False, True))
def test_optional_observations_cannot_refuse_a_representable_query(monkeypatch, successors):
    from behave.sim.symbolic import encoder, plan, step_template

    proc = ir.Procedure((), ('a',), ('i',), (), ir.For('i', 8, ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(3)), ir.Mark('target'), ir.Skip()),
        *(ir.Mark(f'peer{index}') for index in range(40))))), initial={},
        signature=ProcedureSignature((('a', FiniteType(2)),), ()))
    env = {'a': ranges.Range(0, 3)}
    monkeypatch.setattr(plan, 'TERM_BUDGET', 200)
    monkeypatch.setattr(encoder, 'TERM_BUDGET', 200)
    bodies, initialize = [], StepTemplate.__init__
    def record(self, procedure, environment):
        bodies.append(procedure.body)
        initialize(self, procedure, environment)
    monkeypatch.setattr(StepTemplate, '__init__', record)
    selected = StepTemplate.selected(proc, env, ('target',), include_controlled=True,
                                     successors=successors)
    assert len(bodies) == 2  # Broad image refused; exact requested image retained.
    assert selected.hit_names == ('target',)
    assert len(ir.goals_of(bodies[0])) == 41 and ir.goals_of(bodies[1]) == {'target'}
    encoded = selected.encode()
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.hits['target'])
    assert solver.check() == z3.sat
    assert solver.model().eval(encoded.pre['a']).as_long() == 3


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('late_failure', [False, True])
def test_controlled_observations_preserve_complete_hits_without_new_computations(
        monkeypatch, loop, private, late_failure):
    from behave.sim.symbolic import step_template

    shared = ir.Mark('partly_observed')
    branch = ir.If(ir.Op('eq', ir.Var('x'), ir.Var('a')),
                  ir.Seq(ir.Mark('target'), ir.Mark('peer')), ir.Mark('opposite'))
    body = ir._sequence((
        shared, ir.For('i', 2, branch) if loop else branch,
        ir.If(ir.Op('eq', ir.Var('hidden'), ir.Var('a')), shared, ir.Skip()),
        ir.Assign('x', ir.Var('y')), ir.Assign('y', ir.Var('a')),
        ir.Assign('hidden', ir.Op('and', ir.Op('mul', ir.Var('a'), ir.Var('a')), ir.Lit(15))),
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(7)), ir.Fail('Stop', 'failure'), ir.Skip())
        if late_failure else ir.Skip(),
        ir.Mark('finished')))
    proc = ir.Procedure(('x', 'y', 'hidden'), ('a',), ('i',) if loop else (), (), body,
        initial={'x': 0, 'y': 0, 'hidden': 0},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    env = {name: ranges.Range(0, 15) for name in (*proc.state, 'a')}
    full = StepTemplate(proc, env)
    encoded_bodies, encode = [], step_template._encode_step

    def record(procedure, *args, **kwargs):
        encoded_bodies.append(procedure.body)
        return encode(procedure, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(step_template, '_encode_step', record)
        single = StepTemplate.selected(proc, env, ('target',))
        together = StepTemplate.selected(proc, env, ('target',), include_controlled=True)
    assert single.hit_names == ('target',)
    assert set(together.hit_names) == {'target', 'peer', 'opposite', 'finished'} | (
        {'failure'} if late_failure else set())
    # A shared Mark with even one unretained occurrence must not be advertised
    # as complete. No unselected branch or datapath is pulled into encoding.
    assert 'partly_observed' not in together.hit_names
    assert set(together.state_names) == {'x', 'y'}
    computations = lambda tree: [repr(command) for command in ir.walk(tree)
                                if isinstance(command, (ir.Assign, ir.Store))]
    assert len(encoded_bodies) == 2
    assert computations(encoded_bodies[0]) == computations(encoded_bodies[1])
    if private:
        context = z3.Context()
        full, together = full.translate(context), together.translate(context)
    solver = z3.Solver(ctx=full.context)
    state, selected_state, differences = {}, {}, []
    for _ in range(4):
        expected = full.encode(state)
        observed = together.encode({**expected.pre, **selected_state})
        solver.add(expected.constraint, observed.constraint)
        differences.extend(expected.hits[name] != observed.hits[name] for name in together.hit_names)
        differences.extend((expected.ok != observed.ok, expected.error != observed.error))
        differences.extend(left != right for name in together.state_names
            for left, right in zip(_leaves(expected.state_post[name]),
                                   _leaves(observed.state_post[name]), strict=True))
        state, selected_state = expected.state_post, observed.state_post
    assert solver.check() == z3.sat
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_wire_recognition_matches_original_predicate_and_shares_live_dag(private_context, monkeypatch):
    from z3 import z3core as native
    from behave.sim.symbolic.step_template import _wire_value

    context = z3.Context() if private_context else None
    word, other = z3.BitVecs('wire_word wire_other', 32, ctx=context)
    flag = z3.Bool('wire_flag', ctx=context)
    one, zero = (z3.BitVecVal(value, 8, ctx=context) for value in (1, 0))
    bit = z3.If(flag, one, zero)
    values = [word, bit, word + other, z3.If(word == other, one, zero),
              z3.If(flag, word, other), z3.Var(0, word.sort())]
    for depth in range(24):
        bit = z3.Concat(z3.Extract(7, 0, word), bit, z3.ZeroExt(8, zero))
        values.extend((bit, z3.SignExt(3, bit), z3.Concat(bit, other + depth)))

    def original(root):
        pending, seen = [root], set()
        while pending:
            term = pending.pop()
            if term.get_id() in seen:
                continue
            seen.add(term.get_id())
            if z3.is_const(term):
                continue
            if not z3.is_app(term):
                return False
            if term.decl().kind() in (z3.Z3_OP_EXTRACT, z3.Z3_OP_ZERO_EXT,
                                     z3.Z3_OP_SIGN_EXT, z3.Z3_OP_CONCAT):
                pending.extend(term.children())
            elif (z3.is_app_of(term, z3.Z3_OP_ITE) and z3.is_const(term.arg(0))
                  and z3.is_bv_value(term.arg(1)) and z3.is_bv_value(term.arg(2))):
                continue
            else:
                return False
        return True

    expected = [original(value) for value in values]
    decoded = []
    get_decl = native.Z3_get_app_decl

    def count(ctx, term):
        decoded.append(native.Z3_get_ast_id(ctx, term))
        return get_decl(ctx, term)

    monkeypatch.setattr(native, 'Z3_get_app_decl', count)
    shared = {}
    assert [_wire_value(value, shared) for value in values] == expected
    assert len(decoded) == len(set(decoded))
    count_before = len(decoded)
    assert [_wire_value(value, shared) for value in reversed(values)] == expected[::-1]
    assert len(decoded) == count_before


def test_normalized_boundaries_preserve_wiring_but_name_computation():
    from behave.sim.symbolic.step_template import _bind_successors, _wire_value
    from behave.sim.core.construction_checkpoint import finish_construction

    word = z3.BitVec('boundary_word', 16)
    flag = z3.Bool('boundary_flag')
    wire = z3.Concat(z3.Extract(7, 0, word), z3.If(flag, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)))
    arithmetic = word + 1
    assert _wire_value(wire)
    assert not _wire_value(arithmetic)
    equations = []
    result = finish_construction(_bind_successors({'wire': wire, 'sum': arithmetic},
        equations, {}, 'boundary', wires=True), None)
    assert result['wire'].eq(wire)
    assert z3.is_const(result['sum']) and len(equations) == 1
    solver = z3.Solver()
    solver.add(*equations, z3.Or(result['wire'] != wire, result['sum'] != arithmetic))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('wires', [False, True])
def test_equal_ast_wrappers_share_one_successor_without_sharing_other_values(wires):
    from behave.sim.symbolic.step_template import _bind_successors
    from behave.sim.core.construction_checkpoint import finish_construction

    a = z3.BitVec('shared_boundary_input', 8)
    left, right = a + 1, a + 1
    assert left is not right and left.eq(right)
    equations = []
    state = finish_construction(_bind_successors({'left': left, 'right': right, 'other': a + 2},
        equations, {}, 'shared_boundary', wires=wires), None)
    assert state['left'].eq(state['right'])
    assert not state['left'].eq(state['other'])
    assert len(equations) == 2
    solver = z3.Solver()
    solver.add(*equations, z3.Or(state['left'] != left, state['right'] != right,
                               state['other'] != a + 2))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('wrapper', ['concat', 'unsigned', 'signed'])
def test_computed_successor_preserves_explicit_constant_prefix(private_context, wrapper):
    from behave.sim.symbolic.step_template import _bind_successors
    from behave.sim.core.construction_checkpoint import finish_construction

    context = z3.Context() if private_context else None
    word = z3.BitVec('computed_boundary', 8, ctx=context)
    computation = word + 1
    value = (z3.Concat(z3.BitVecVal(5, 3, ctx=context), computation) if wrapper == 'concat'
             else z3.ZeroExt(3, computation) if wrapper == 'unsigned'
             else z3.SignExt(3, computation))
    equations = []
    result = finish_construction(_bind_successors(value,
        equations, {}, 'explicit_wiring', wires=True), None)
    assert len(equations) == 1
    if wrapper == 'signed':
        assert z3.is_const(result)  # Its high bits depend on an unknown sign.
    else:
        assert z3.is_app_of(result, z3.Z3_OP_CONCAT)
        assert result.arg(0).as_long() == (5 if wrapper == 'concat' else 0)
    solver = z3.Solver(ctx=context)
    solver.add(*equations, result != value)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('ends', [(0, 2), (0, 4), (1, 6), (-5, -2), (-8, -4), (0, 0), (-128, 127)])
def test_successor_narrowing_preserves_signed_control_hull(private_context, ends):
    from behave.sim.symbolic.step_template import _bind_successors
    from behave.sim.symbolic.term_bounds import _SyntacticClosureProof
    from behave.sim.symbolic.ranges import Range
    from behave.sim.core.construction_checkpoint import finish_construction

    context = z3.Context() if private_context else z3.main_ctx()
    choose = z3.Bool('signed_boundary_choice', ctx=context)
    left, right = (z3.BitVecVal(value, 32, ctx=context) for value in ends)
    value = z3.If(choose, left, right)
    proof = _SyntacticClosureProof({})
    assert proof.coarse_signed_hull(value) == Range(*ends)
    equations = []
    successor = finish_construction(_bind_successors(value, equations, {},
        'signed_control', wires=True, _facts=proof), None)
    assert proof.coarse_signed_hull(successor) == Range(*ends)
    solver = z3.Solver(ctx=context)
    solver.add(*equations, successor != value)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('depth', [16, 2048])
def test_constant_prefix_walk_is_shared_iterative_and_does_not_expand_computation(depth, monkeypatch):
    from behave.sim.symbolic.step_template import _constant_prefix
    from behave.sim.core.construction_checkpoint import finish_construction

    word = z3.BitVec('deep_wiring', 8)
    computation = word + 1
    value = computation
    for _ in range(depth):
        value = z3.ZeroExt(1, value)
    original = z3.ExprRef.get_id
    visits = []

    def count(term):
        visits.append(original(term))
        return original(term)

    shared = {}
    monkeypatch.setattr(z3.ExprRef, 'get_id', count)
    first = finish_construction(_constant_prefix(value, shared), None)
    visited = len(visits)
    again = finish_construction(_constant_prefix(value, shared), None)
    assert first == again == (0, depth)
    # A live shared node is decoded at most a constant number of times,
    # independent of the length of the enclosing wiring chain.
    from collections import Counter
    assert max(Counter(visits[:visited]).values()) <= 8
    assert len(visits) - visited <= 3
    assert original(word) not in visits


@pytest.mark.parametrize('private_context', [False, True])
def test_constant_prefix_mux_facts_are_structural_and_match_exact_bits(private_context):
    from behave.sim.symbolic.step_template import _constant_prefix, _bind_successors
    from behave.sim.core.construction_checkpoint import finish_construction

    context = z3.Context() if private_context else None
    x = z3.BitVec('prefix_payload', 8, ctx=context)
    guard = x * x == 9  # The prefix walk never analyzes the condition.
    literal = lambda value, width: z3.BitVecVal(value, width, ctx=context)
    zero = z3.ZeroExt(4, x + 1)
    packed = lambda value: z3.Concat(literal(value, 4), x + 2)
    values = [
        (z3.If(guard, zero, z3.ZeroExt(4, x - 1)), (0, 4)),
        (z3.If(guard, packed(8), packed(9)), (4, 3)),
        (z3.If(guard, packed(8), packed(0)), (0, 0)),
        (z3.If(guard, literal(0x800, 12), packed(8)), (8, 4)),
        (z3.If(guard, literal(0x800, 12), literal(0x800, 12)), (0x800, 12)),
        (z3.SignExt(4, packed(8)), (0xf8, 8)),
        (z3.SignExt(4, zero), (0, 8)),
        (z3.SignExt(4, x), (0, 0)),
        (z3.Extract(10, 6, packed(8)), (0, 3)),
        (z3.Extract(7, 0, packed(8)), (0, 0)),
    ]
    shared = {}
    for term, expected in values:
        assert finish_construction(_constant_prefix(term, shared), None) == expected
        bits, width = expected
        solver = z3.Solver(ctx=context)
        if width:
            solver.add(z3.Extract(term.size() - 1, term.size() - width, term) != literal(bits, width))
            assert solver.check() == z3.unsat
        equations = []
        result = finish_construction(_bind_successors(term, equations, {}, 'prefix', wires=True), None)
        solver = z3.Solver(ctx=context)
        solver.add(*equations, result != term)
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_prefix_walk_stops_at_unknown_high_bits_and_shares_native_nodes(private_context, monkeypatch):
    from z3 import z3core as native
    from behave.sim.symbolic.step_template import _constant_prefix
    from behave.sim.core.construction_checkpoint import finish_construction

    context = z3.Context() if private_context else None
    x = z3.BitVec('prefix_short_circuit', 8, ctx=context)
    opaque = x + 1
    low = z3.If(x * x == 9, x + 2, x - 3)
    concat = z3.Concat(z3.BitVecVal(5, 3, ctx=context), opaque, low)
    mux = z3.If(x == 7, opaque, low)
    visited = []
    get_sort = native.Z3_get_sort

    def observe(ctx, term):
        visited.append(native.Z3_get_ast_id(ctx, term))
        return get_sort(ctx, term)

    shared = {}
    with monkeypatch.context() as patch:
        patch.setattr(native, 'Z3_get_sort', observe)
        assert finish_construction(_constant_prefix(concat, shared), None) == (5, 3)
        assert finish_construction(_constant_prefix(mux, shared), None) == (0, 0)
        before = len(visited)
        assert finish_construction(_constant_prefix(concat, shared), None) == (5, 3)
        assert len(visited) == before
    assert len(visited) == len(set(visited))
    assert low.get_id() not in visited
    assert x.get_id() not in visited


@pytest.mark.parametrize('private_context', [False, True])
def test_prefix_native_walk_matches_eager_wiring_reference(private_context):
    from functools import lru_cache
    from behave.sim.symbolic.step_template import _constant_prefix
    from behave.sim.core.construction_checkpoint import finish_construction

    @lru_cache(None)
    def expected(term):
        if not z3.is_bv(term):
            return ''
        if z3.is_bv_value(term):
            return format(term.as_long(), f'0{term.size()}b')
        if not z3.is_app(term):
            return ''
        kind = term.decl().kind()
        if kind == z3.Z3_OP_ITE:
            left, right = expected(term.arg(1)), expected(term.arg(2))
            prefix = ''
            for a, b in zip(left, right):
                if a != b:
                    break
                prefix += a
            return prefix
        if kind == z3.Z3_OP_CONCAT:
            prefix = ''
            for child in term.children():
                bits = expected(child)
                prefix += bits
                if len(bits) != child.size():
                    break
            return prefix
        if kind in (z3.Z3_OP_ZERO_EXT, z3.Z3_OP_SIGN_EXT):
            bits = expected(term.arg(0))
            return (('0' if kind == z3.Z3_OP_ZERO_EXT else bits[:1])
                    * term.decl().params()[0]) + bits
        if kind == z3.Z3_OP_EXTRACT:
            high, low = term.decl().params()
            size = term.arg(0).size()
            return expected(term.arg(0))[size - high - 1:size - low]
        return ''

    context = z3.Context() if private_context else None
    x = z3.BitVec('prefix_mixed', 8, ctx=context)
    flag = z3.Bool('prefix_choice', ctx=context)
    values = [x, x + 1, z3.Var(0, x.sort()), z3.BitVecVal(0xe3, 8, ctx=context)]
    for index in range(48):
        left, right = values[-1], values[(index * 7) % len(values)]
        value = (z3.If(flag, left, right),
                 z3.Extract(7, 0, z3.SignExt(3, left)),
                 z3.Extract(10, 3, z3.ZeroExt(3, right)),
                 z3.Concat(z3.Extract(7, 4, left), z3.Extract(3, 0, right)))[index % 4]
        values.append(value)
    shared = {}
    for value in [*values, flag, z3.IntVal(2, ctx=context), *reversed(values)]:
        bits = expected(value)
        assert finish_construction(_constant_prefix(value, shared), None) == (int(bits or '0', 2), len(bits))


def _template(*, failure=False):
    proc = ir.Procedure(('x', 'y', 'unused'), ('a',), (), (), ir._sequence((
        ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(3)), ir.Mark('target'), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('unused'), ir.Lit(7)),
              ir.Fail('RTLFatal', 'failure') if failure else ir.Mark('other'), ir.Skip()),
        ir.Assign('x', ir.Var('y')),
        ir.Assign('y', ir.Var('a')),
        ir.Assign('unused', ir.Op('and', ir.Op('add', ir.Var('unused'), ir.Var('a')), ir.Lit(15))),
    )), initial={'x': 0, 'y': 0, 'unused': 0},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    env = ranges.fixpoint(proc, {'a': ranges.Range(0, 15)})
    return StepTemplate(proc, env)


@pytest.mark.parametrize('private_context', [False, True])
def test_projected_multistep_relation_matches_all_observations(private_context):
    template = _template()
    view = template.project({'hit': ('target',)}, outputs=())
    assert set(view._layout['state_post']) == {'x', 'y'}
    assert set(template._layout['state_post']) == {'x', 'y', 'unused'}
    assert view._layout['post'] == {}
    if private_context:
        context = z3.Context()
        template, view = template.translate(context), view.translate(context)
    solver = z3.Solver(ctx=template.context)
    full_state = projected_state = {}
    different = []
    for _ in range(4):
        full = template.encode(full_state)
        projected = view.encode({**full.pre, **projected_state})
        solver.add(full.constraint, projected.constraint)
        different.extend((full.hits['target'] != projected.hits['hit'],
                          full.ok != projected.ok, full.error != projected.error))
        for name in projected.state_post:
            different.extend(a != b for a, b in zip(
                _leaves(full.state_post[name]), _leaves(projected.state_post[name]), strict=True))
        full_state, projected_state = full.state_post, projected.state_post
    assert solver.check() == z3.sat
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


def test_outputs_and_interstep_reads_are_roots_not_only_target_hits():
    template = _template()
    assert set(template.project({}, outputs=())._layout['state_post']) == set()
    assert set(template.project({}, outputs=('x',))._layout['state_post']) == {'y'}
    view = template.project({'hit': ('target',)}, outputs=(), state_reads=('unused',))
    assert set(view._layout['state_post']) == {'x', 'y', 'unused'}
    # Asking for all outputs keeps all their predecessors; projection is never
    # inferred merely from which goals happen to be in a caller's query.
    view = template.project({'hit': ('target',)}, outputs=tuple(template._layout['post']))
    assert set(view._layout['state_post']) == {'x', 'y', 'unused'}


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_local_observations_match_full_step_without_successor_equations(private_context, failure):
    template = _template(failure=failure)
    view = template.project_local({'hit': ('target', 'target')}, outputs=('x',))
    assert view.state_names == ()
    assert set(template.project({'hit': ('target',)}, outputs=()).state_names) >= {'x', 'y'}
    if private_context:
        ctx = z3.Context()
        template, view = template.translate(ctx), view.translate(ctx)
    full = template.encode()
    local = view.encode(full.pre)
    assert not local.definitions and not local.state_post
    assert set(local.pre) == set(full.pre)
    solver = z3.Solver(ctx=template.context)
    solver.add(full.constraint, local.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(full.hits['target'] != local.hits['hit'],
                     full.ok != local.ok, full.error != local.error,
                     full.post['x'] != local.post['x']))
    assert solver.check() == z3.unsat
    # The prepared full transition and its composable projection are intact.
    assert set(template.state_names) == {'x', 'y', 'unused'}


@pytest.mark.parametrize('private_context', [False, True])
def test_local_projection_keeps_input_state_restrictions_and_failure_events(private_context):
    proc = ir.Procedure(('flag',), ('a',), (), (),
        ir.If(ir.Op('truth', ir.Var('flag')),
              ir._sequence((ir.Mark('before-failure'), ir.Fail('bad', 'bad'))),
              ir.Mark('ordinary')),
        initial={'flag': True}, types={'flag': types.BOOLEAN},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    template = StepTemplate(proc, {'flag': ranges.Range(0, 1), 'a': ranges.Range(0, 15)})
    if private_context:
        template = template.translate(z3.Context())
    local = template.project_local({'failure': ('before-failure',), 'ordinary': ('ordinary',)}).encode()
    assert not local.state_post and not local.definitions
    for bad in (local.pre['flag'] == 2, local.pre['a'] == 16, local.pre['a'] == -1):
        solver = z3.Solver(ctx=template.context)
        solver.add(local.constraint, bad)
        assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=template.context)
    solver.add(local.constraint, local.hits['failure'])
    assert solver.check() == z3.sat  # No successful-execution premise hides it.
    assert solver.model().eval(local.error).as_long() != 0
    assert z3.is_false(solver.model().eval(local.ok))


def test_local_projection_size_does_not_follow_unobserved_next_state_chain():
    from behave.sim.symbolic.exact_formula import _dag_nodes

    sizes = []
    for count in (2, 8, 32):
        names = tuple(f's{i}' for i in range(count))
        body = [ir.If(ir.Op('eq', ir.Var('s0'), ir.Lit(3)), ir.Mark('target'), ir.Skip())]
        body += [ir.Assign(a, ir.Var(b)) for a, b in zip(names, names[1:])]
        body.append(ir.Assign(names[-1], ir.Op('and', ir.Op('mul',
            ir.Var(names[-1]), ir.Var('a')), ir.Lit(15))))
        proc = ir.Procedure(names, ('a',), (), (), ir._sequence(body),
            initial=dict.fromkeys(names, 0),
            signature=ProcedureSignature((('a', FiniteType(4)),), ()))
        template = StepTemplate(proc, {name: ranges.Range(0, 15) for name in (*names, 'a')})
        history = template.project({'hit': ('target',)}, outputs=())
        assert set(history.state_names) == set(names)
        local = template.project_local({'hit': ('target',)}).encode()
        assert not local.state_post and not local.definitions
        sizes.append(_dag_nodes((local.constraint, local.hits['hit'])))
    assert len(set(sizes)) == 1  # No per-goal next-state closure in a one-step query.


def test_terminal_failure_retains_its_state_even_without_a_requested_failure_goal():
    template = _template(failure=True)
    view = template.project({'hit': ('target',)}, outputs=())
    assert set(view._layout['state_post']) == {'x', 'y', 'unused'}
    for field in ('constraint', 'ok', 'error'):
        assert template._image.arg(template._layout[field]).eq(view._image.arg(view._layout[field]))


def test_semantic_state_and_input_constraints_survive_projection():
    proc = ir.Procedure(('flag',), ('a',), (), (), ir.Mark('target'),
        initial={'flag': True}, types={'flag': types.BOOLEAN},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    template = StepTemplate(proc, {'flag': ranges.Range(0, 1), 'a': ranges.Range(0, 15)})
    view = template.project({'hit': ('target',)}, outputs=())
    assert set(view._layout['state_post']) == {'flag'}
    encoded = view.encode()
    for bad in (encoded.pre['flag'] == 2, encoded.pre['a'] == 16, encoded.pre['a'] == -1):
        solver = z3.Solver()
        solver.add(encoded.constraint, bad)
        assert solver.check() == z3.unsat


def test_nested_cells_are_closed_as_state_and_keep_signed_carriers():
    proc = ir.Procedure(('cells', 'unused'), ('a',), (), (), ir._sequence((
        ir.If(ir.Op('lt', ir.Sel(ir.Sel(ir.Var('cells'), ir.Lit(1)), ir.Lit(0)), ir.Lit(0)),
              ir.Mark('negative'), ir.Skip()),
        ir.Store('cells', ir.Lit(1), ir.Op('arraylit', ir.Var('a'), ir.Lit(0))),
        ir.Assign('unused', ir.Var('a')),
    )), initial={'cells': [[1, -2], [3, -4]], 'unused': 0},
        types={'cells': types.Array(2, types.Array(2))},
        signature=ProcedureSignature((('a', FiniteType(5, signed=True)),), ()))
    env = {'cells': ranges.Range(-16, 15), 'unused': ranges.Range(-16, 15),
           'a': ranges.Range(-16, 15)}
    template = StepTemplate(proc, ranges.fixpoint(proc, env))
    view = template.project({'hit': ('negative',)}, outputs=())
    assert set(view._layout['state_post']) == {'cells'}
    full = template.encode()
    selected = view.encode(full.pre)
    solver = z3.Solver()
    solver.add(full.constraint, selected.constraint, z3.Or(
        full.hits['negative'] != selected.hits['hit'],
        *(a != b for a, b in zip(_leaves(full.state_post['cells']),
                                _leaves(selected.state_post['cells']), strict=True))))
    assert solver.check() == z3.unsat


def test_dependency_index_is_reused_but_not_shared_with_a_different_view(monkeypatch):
    template = _template()
    template.project({'hit': ('target',)}, outputs=())
    index = template._dependencies
    assert index is not None and all(type(mask) is int for mask in index)
    assert len(index) == template._image.num_args()

    def unnecessary(*args):
        pytest.fail('a prepared dependency index walked the DAG again')

    with monkeypatch.context() as patch:
        patch.setattr(z3.z3core, 'Z3_get_ast_kind', unnecessary)
        view = template.project({'all': ('target', 'other')}, outputs=())
    assert template._dependencies is index
    assert view._dependencies is not index
    assert len(view._dependencies) == view._image.num_args()
    assert set(view._layout['state_post']) == {'x', 'y', 'unused'}
    assert view.project({'hit': ('all',)}, outputs=())._layout['state_post'] == view._layout['state_post']


def test_empty_transition_and_unknown_roots():
    template = StepTemplate(ir.Procedure((), (), (), (), ir.Mark('empty')), {})
    view = template.project({'hit': ('empty',), 'never': ()}, outputs=())
    encoded = view.encode()
    assert encoded.pre == encoded.state_post == encoded.post == {}
    assert z3.is_true(z3.simplify(encoded.hits['hit']))
    assert z3.is_false(z3.simplify(encoded.hits['never']))
    for options in ({'outputs': ('absent',)}, {'outputs': (), 'state_reads': ('absent',)}):
        with pytest.raises(KeyError):
            template.project({'hit': ('empty',)}, **options)


@pytest.mark.parametrize('private', [False, True])
def test_literal_false_images_do_not_assume_entry_ranges_or_remove_public_goals(private):
    proc = ir.Procedure(('x',), ('a',), ('local',), (), ir._sequence((
        ir.Assign('local', ir.Lit(1)),
        ir.If(ir.Op('ne', ir.Var('local'), ir.Lit(1)), ir.Mark('local_never'), ir.Skip()),
        ir.If(ir.Lit(False), ir.Fail('SourceDomain', 'never'), ir.Skip()),
        ir.If(ir.Op('lt', ir.Var('x'), ir.Lit(0)), ir.Mark('negative'), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(3)), ir.Mark('mutable'), ir.Skip()),
        ir.Mark('always'),
    )), initial={'x': 0}, signature=ProcedureSignature((('a', FiniteType(2)),), ()))
    template = StepTemplate(proc, {'x': ranges.Range(0, 3), 'a': ranges.Range(0, 3)})
    if private:
        template = template.translate(z3.Context())
    names = ('local_never', 'never', 'negative', 'mutable', 'always')
    assert template.literal_false_hits(names) == {'local_never', 'never'}
    assert set(template._layout['hits']) == set(names)
    encoded = template.encode()
    solver = z3.Solver(ctx=template.context)
    solver.add(encoded.constraint, encoded.hits['negative'], encoded.hits['mutable'])
    assert solver.check() == z3.sat
    with pytest.raises(KeyError):
        template.literal_false_hits(('absent',))


def test_dependency_masks_follow_total_definitions_and_survive_normalization():
    from behave.sim.symbolic.exact_formula import FormulaDefinition
    from behave.sim.symbolic.step_template import _inline_definitions

    a, b, x, y = z3.BitVecs('dep_a dep_b dep_x dep_y', 8)
    choose = z3.Bool('dep_choose')
    roots, masks = _inline_definitions([y, y - y, b], (
        FormulaDefinition('x', x, a + b),
        FormulaDefinition('y', y, z3.If(choose, x, a)),
    ), (a, b, choose))
    assert masks == (0b111, 0b111, 0b010)
    assert z3.is_bv_value(z3.simplify(roots[1]))
    # Eliminating a read during normalization leaves a safe over-approximation,
    # not permission to infer a state invariant or narrow an input domain.
    assert z3.is_true(z3.simplify(roots[0] == z3.If(choose, a + b, a)))


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('real_failure', [False, True])
def test_normalized_projection_drops_only_eliminated_dependencies(private_context, real_failure):
    payload = ir.Var('payload')
    fault = (ir.Op('eq', payload, ir.Lit(7)) if real_failure else
             ir.Op('ne', ir.Op('sub', payload, payload), ir.Lit(0)))
    proc = ir.Procedure(('control', 'payload'), ('a',), (), (), ir._sequence((
        ir.If(fault, ir.Fail('RTLFatal', 'failure'), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('control'), ir.Lit(3)), ir.Mark('target'), ir.Skip()),
        ir.Assign('control', ir.Var('a')),
        ir.Assign('payload', ir.Op('and', ir.Op('add', payload, ir.Var('a')), ir.Lit(255))),
    )), initial={'control': 0, 'payload': 0},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    template = StepTemplate(proc, {'control': ranges.Range(0, 15),
                                   'payload': ranges.Range(0, 255), 'a': ranges.Range(0, 15)})
    if private_context:
        template = template.translate(z3.Context())
    original_image, original_dependencies = template._image, template._dependencies
    normalized = template.normalized()
    assert template._image is original_image and template._dependencies is original_dependencies
    selected = normalized.project({'hit': ('target',)}, outputs=())
    assert set(selected.state_names) == ({'control', 'payload'} if real_failure else {'control'})
    solver = z3.Solver(ctx=template.context)
    before = after = {}
    different = []
    for _ in range(4):
        full = template.encode(before)
        local = selected.encode({**full.pre, **after})
        solver.add(full.constraint, local.constraint)
        different.extend((full.hits['target'] != local.hits['hit'],
                          full.ok != local.ok, full.error != local.error))
        different.extend(a != b for name in selected.state_names
                         for a, b in zip(_leaves(full.state_post[name]),
                                         _leaves(local.state_post[name]), strict=True))
        before, after = full.state_post, local.state_post
    assert solver.check() == z3.sat
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


def test_normalized_projection_keeps_semantic_restrictions_and_input_specialization():
    proc = ir.Procedure(('flag',), ('a',), (), (), ir.Mark('target'),
        initial={'flag': True}, types={'flag': types.BOOLEAN},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    template = StepTemplate(proc, {'flag': ranges.Range(0, 1), 'a': ranges.Range(0, 15)})
    selected = template.specialize_inputs({'a': 2}).normalized().project({'hit': ('target',)}, outputs=())
    encoded = selected.encode()
    for bad in (encoded.pre['flag'] == 2, encoded.pre['a'] != 2):
        solver = z3.Solver()
        solver.add(encoded.constraint, bad)
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('size', [8, 32, 128])
@pytest.mark.parametrize('private_context', [False, True])
def test_joint_worklist_closes_alternating_views_once(size, private_context):
    from collections import Counter

    class CountedPosts(dict):
        def __getitem__(self, name):
            self.reads[name] += 1
            return super().__getitem__(name)

    names, templates, counters = tuple(f's{i}' for i in range(size)), [], []
    for parity in (0, 1):
        commands = [ir.If(ir.Op('eq', ir.Var(names[0]), ir.Lit(3)),
                          ir.Mark('target'), ir.Skip())]
        commands += [ir.Assign(name, ir.Var(names[index + 1]))
                     for index, name in enumerate(names[:-1]) if index % 2 == parity]
        # Parameter positions belong to each view, not to a shared name order.
        states = (*names, 'unused') if parity == 0 else ('unused', *reversed(names))
        proc = ir.Procedure(states, (), (), (), ir._sequence(commands),
                            initial=dict.fromkeys(states, 0))
        templates.append(StepTemplate(proc, dict.fromkeys(states, ranges.Range(-8, 7))))
    if private_context:
        context = z3.Context()
        templates = [view.translate(context) for view in templates]
    for template in templates:
        posts = CountedPosts(template._layout['state_post'])
        posts.reads = Counter()
        template._layout = {**template._layout, 'state_post': posts}
        counters.append(posts.reads)
    selected = project_templates(templates, {'target': ('target',)}, outputs=())
    assert all(set(view.state_names) == set(names) for view in selected)
    assert all(reads == dict.fromkeys(names, 1) for reads in counters)
    solver, state, smaller, different = z3.Solver(ctx=templates[0].context), {}, {}, []
    for turn in range(6):
        full = templates[turn % 2].encode(state)
        local = selected[turn % 2].encode({**full.pre, **smaller})
        solver.add(full.constraint, local.constraint)
        different.extend((full.hits['target'] != local.hits['target'],
                          full.ok != local.ok, full.error != local.error))
        different.extend(a != b for name in names for a, b in zip(
            _leaves(full.state_post[name]), _leaves(local.state_post[name]), strict=True))
        state, smaller = full.state_post, local.state_post
    assert solver.check() == z3.sat
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_joint_projection_keeps_a_peer_failure_and_explicit_reads(private_context):
    templates = (_template(), _template(failure=True))
    if private_context:
        context = z3.Context()
        templates = tuple(view.translate(context) for view in templates)
    selected = project_templates(templates, {'hit': ('target',)}, outputs=(), state_reads=('y',))
    for full, view in zip(templates, selected, strict=True):
        assert set(view.state_names) == {'x', 'y', 'unused'}
        for field in ('constraint', 'ok', 'error'):
            assert full._image.arg(full._layout[field]).eq(view._image.arg(view._layout[field]))
    complete = project_templates(templates, {'hit': ('target',)})
    assert all(set(view.state_names) == {'x', 'y', 'unused'} for view in complete)


@pytest.mark.parametrize('size', [4, 16, 64])
@pytest.mark.parametrize('private_context', [False, True])
def test_shared_closure_accepts_phase_local_events_without_rebuilding_views(
        monkeypatch, size, private_context):
    from behave.sim.symbolic.step_template import template_state_dependencies

    names = tuple(f's{i}' for i in range(size))
    states = (*names, 'private', 'fault', 'explicit', 'unused')
    templates = []
    for parity in (0, 1):
        observed = names[0] if parity == 0 else 'private'
        commands = [ir.If(ir.Op('eq', ir.Var(observed), ir.Lit(3)),
                         ir.Mark(f'phase{parity}'), ir.Skip())]
        commands += [ir.Assign(name, ir.Var(names[index + 1]))
                     for index, name in enumerate(names[:-1]) if index % 2 == parity]
        if parity:
            commands.append(ir.If(ir.Op('eq', ir.Var('fault'), ir.Lit(1)),
                                  ir.Fail('Stop', 'fault-event'), ir.Skip()))
        proc = ir.Procedure(states, (), (), (), ir._sequence(commands),
                            initial=dict.fromkeys(states, 0))
        templates.append(StepTemplate(proc, dict.fromkeys(states, ranges.Range(-8, 7))))
    if private_context:
        context = z3.Context()
        templates = [view.translate(context) for view in templates]

    # Independent old fixed-point algorithm: each phase retains its own events
    # and failures; newly needed state must survive every other phase as well.
    expected, rounds = {'explicit'}, 0
    while True:
        rounds += 1
        following = set(expected)
        for view in templates:
            following.update(view.project({name: (name,) for name in view.hit_names},
                                           outputs=(), state_reads=expected).state_names)
        if following == expected:
            break
        expected = following
    assert rounds > 1
    assert expected == {*names, 'private', 'fault', 'explicit'}

    def no_projection(*args, **kwargs):
        pytest.fail('dependency discovery must not construct projected templates')

    with monkeypatch.context() as patch:
        patch.setattr(StepTemplate, '_project', no_projection)
        actual = template_state_dependencies(iter(templates), outputs=(), state_reads=('explicit',))
        without_events = template_state_dependencies(templates, outputs=(), hits=(),
                                                     state_reads=('explicit',))
    assert actual == expected
    assert without_events == {'fault', 'explicit'}  # Failures survive even without hit roots.


def test_projection_cancellation_never_changes_prepared_images(monkeypatch):
    from behave.sim.symbolic import step_template

    templates = (_template(), _template(failure=True))
    originals = tuple((view._image, view._layout, view._dependencies) for view in templates)

    class Cancelled(Exception):
        pass

    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise Cancelled

    with monkeypatch.context() as patch:
        patch.setattr(step_template, 'check_preparation', cancel)
        with pytest.raises(Cancelled):
            project_templates(templates, {'hit': ('target',)}, outputs=())
    for view, before in zip(templates, originals, strict=True):
        assert all(a is b for a, b in zip((view._image, view._layout, view._dependencies), before))
    assert project_templates(templates, {'hit': ('target',)}, outputs=())
