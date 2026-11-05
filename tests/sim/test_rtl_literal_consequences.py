"""Fixed history reduction uses actual implications, not a completed model."""

from types import SimpleNamespace

import pytest
import z3

from behave.sim.rtl.fixed_target import LiteralConsequences
from behave.sim.rtl.source_member import (
    SourceMemberPlan, reduce_fixed_source_defect, source_literal_consequences,
)


def _reduce(roots, coordinates, target):
    facts = LiteralConsequences(roots, context=target.ctx, check=lambda: None)
    facts.derive(coordinates)
    reduced = facts.evaluate(target)
    solver = z3.Solver(ctx=target.ctx)
    solver.add(*roots, target != reduced)
    assert solver.check() == z3.unsat  # Independent equivalence, not a model check.
    return reduced


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [8, 128])
@pytest.mark.parametrize('mode', ['coordinates', 'targets'])
def test_unused_point_equalities_do_not_expand_array_histories(private, count, mode):
    context = z3.Context() if private else None
    sort = z3.BitVecSort(8, ctx=context)
    table = z3.Array('unused_history', sort, sort)
    index, data, selected = z3.BitVecs('unused_index unused_data selected_cell', 8, ctx=context)
    read = table[index]
    roots = [selected == 7]
    for i in range(count):
        table = z3.Store(table, index + i, read + data)
        read = table[index + i + 1]
        roots.append(z3.BitVec(f'unused_value_{i}', 8, ctx=context) == read)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    assert not facts._dag.nodes  # Indexing equations is not evaluating their RHSs.
    target = selected != 7
    if mode == 'coordinates':
        facts.derive((selected,))
    else:
        facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    assert read.as_ast().value not in facts._dag.nodes
    assert data.as_ast().value not in facts._dag.nodes
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('enabled', [False, True, None])
@pytest.mark.parametrize('mode', ['coordinates', 'targets'])
def test_derived_point_equalities_are_requested_with_their_guards(enabled, mode):
    table = z3.Array('derived_points', z3.IntSort(), z3.IntSort())
    index, value, result = z3.Ints('derived_index derived_value derived_result')
    guard = z3.Bool('derived_guard')
    roots = [index == 2, value == index + 5,
             z3.Implies(guard, table[2] == value), result == table[index]]
    if enabled is not None:
        roots.append(guard == enabled)
    facts = LiteralConsequences(tuple(reversed(roots)), context=None, check=lambda: None)
    target = result != 7
    if mode == 'coordinates':
        facts.derive((result,))
    else:
        facts.derive_targets((target,))
    reduced = facts.constant_target(target)
    assert z3.is_false(reduced) is (enabled is True)
    assert not facts._literal(facts.evaluate(table[3]))
    solver = z3.Solver()
    solver.add(*roots, z3.Or(target != reduced, z3.Not(z3.And(*facts.literal_constraints()))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('enabled', [False, True, None])
def test_demanded_history_uses_asserted_definitions_not_frame_values(private, enabled):
    context = z3.Context() if private else None
    first, last, free = z3.Ints('demand_first demand_last demand_free', ctx=context)
    guard, escape = z3.Bools('demand_guard demand_fault', ctx=context)
    roots = [first == 3, z3.Implies(guard, last == first + 1), escape == (free < 0)]
    if enabled is not None:
        roots.append(guard == enabled)
    targets = (last != 4, escape)
    history = SimpleNamespace(member_plan=SourceMemberPlan(
        ((first, z3.IntVal(99, ctx=context)),), (((last, z3.IntVal(99, ctx=context)),),)))
    facts = source_literal_consequences(roots, history, context=context,
        check=lambda: None, targets=targets)
    reduced = facts.constant_targets(targets)
    assert z3.is_false(reduced[0]) is (enabled is True)
    assert not z3.is_false(reduced[1])
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Or(*(a != b for a, b in zip(targets, reduced))))
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('count', [32, 512])
def test_demanded_definitions_do_not_open_unrelated_or_dead_datapaths(count):
    x, y, free = z3.Ints('selected_x selected_y unrelated_input')
    enabled = z3.Bool('selected_enabled')
    unused = z3.Ints(' '.join(f'unused_{i}' for i in range(count)))
    roots = (enabled, x == 5, y == z3.If(enabled, x + 1, unused[-1]),
             *(value == free * free + i for i, value in enumerate(unused)))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    target = y != 6
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    assert not {value.as_ast().value for value in unused} & facts._dag.nodes.keys()
    assert free.as_ast().value not in facts._dag.nodes


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('depth', [32, 512])
@pytest.mark.parametrize('negated', [False, True])
def test_unrequested_guarded_predicate_does_not_open_its_history(private, depth, negated):
    context = z3.Context() if private else None
    data, value = z3.BitVecs('predicate_history_data predicate_history_value', 32, ctx=context)
    gate = z3.Bool('predicate_history_gate', ctx=context)
    selected = z3.Int('predicate_history_selected', ctx=context)
    history = data
    for _ in range(depth):
        history = z3.UDiv(history * history + 7, history | 1)
    predicate = z3.ULT(value, 9)
    assertion = z3.Not(predicate) if negated else predicate
    roots = (gate == (history == 17), z3.Implies(gate, assertion), selected == 5)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    target = selected != 5
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    assert history.as_ast().value not in facts._dag.nodes
    assert data.as_ast().value not in facts._dag.nodes
    assert len(facts._dag.nodes) < 8
    # The same guarded fact becomes relevant to a later target. Its unknown
    # guard must still be checked, not assumed true or forgotten permanently.
    facts.derive_targets((predicate,))
    assert history.as_ast().value in facts._dag.nodes
    assert facts.constant_target(predicate) is predicate
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('negated', [False, True])
def test_demanded_guarded_predicate_follows_other_guarded_predicates(negated):
    a, b, c = z3.BitVecs('predicate_chain_a predicate_chain_b predicate_chain_c', 8)
    first, second, last = (z3.ULT(term, 9) for term in (a, b, c))
    selected = z3.Not(last) if negated else last
    roots = (first, z3.Implies(first, second), z3.Implies(second, selected))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    facts.derive_targets((last,))
    assert (z3.is_false if negated else z3.is_true)(facts.constant_target(last))
    solver = z3.Solver()
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('take_left', [False, True])
@pytest.mark.parametrize('depth', [32, 512])
def test_demanded_control_definitions_finish_before_inactive_datapaths(private, take_left, depth):
    context = z3.Context() if private else None
    control, data, result = z3.BitVecs('control_first_word control_first_data control_first_result',
                                    32, ctx=context)
    gate = z3.Bool('control_first_gate', ctx=context)
    inactive = data
    for _ in range(depth):
        inactive = z3.UDiv(inactive * inactive + 7, inactive | 1)
    selected = z3.BitVecVal(19, 32, ctx=context)
    expression = z3.If(gate, selected, inactive) if take_left else z3.If(gate, inactive, selected)
    roots = (gate == (control == control if take_left else control != control), result == expression)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    target = result != 19
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    assert inactive.as_ast().value not in facts._dag.nodes
    assert data.as_ast().value not in facts._dag.nodes
    assert len(facts._dag.nodes) < 12  # Independent of the unneeded datapath depth.
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Or(target, z3.Not(z3.And(*facts.literal_constraints()))))
    assert solver.check() == z3.unsat
    # Deferred syntax is not erased or assumed unreachable in another query.
    independent = inactive == 3
    facts.derive_targets((independent,))
    assert facts.constant_target(independent) is independent
    assert inactive.as_ast().value in facts._dag.nodes


def test_suspended_definition_worklist_can_resume_after_cancellation():
    values = z3.Ints(' '.join(f'resume_demand_{i}' for i in range(128)))
    roots = (values[0] == 0, *(b == a + 1 for a, b in zip(values, values[1:])))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    target = values[-1] != len(values) - 1
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        if calls == 100:
            raise InterruptedError('suspended dependency deadline')

    facts.check = stop
    with pytest.raises(InterruptedError, match='suspended dependency'):
        facts.derive_targets((target,))
    assert facts._dag.notifications is None and facts._dag.request is None
    facts.check = lambda: None
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [16, 512])
def test_equation_index_wraps_only_requested_definitions(monkeypatch, private, count):
    from behave.sim.rtl import fixed_target

    context = z3.Context() if private else None
    free, selected = z3.Ints('view_free view_selected', ctx=context)
    cold = [free * free + i for i in range(count)]
    fields = [z3.Int(f'view_cold_{i}', ctx=context) for i in range(count)]
    roots = (selected == 7, *(field == value for field, value in zip(fields, cold)))
    cold_ids = {term.get_id() for term in cold}
    convert = fixed_target._to_expr_ref
    wrapped = set()

    def measured(term, ctx):
        wrapped.add(fixed_target.native.Z3_get_ast_id(ctx.ref(), term))
        return convert(term, ctx)

    monkeypatch.setattr(z3.z3, '_to_expr_ref', measured)
    monkeypatch.setattr(fixed_target, '_to_expr_ref', measured)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    target = selected != 7
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    assert not wrapped & cold_ids
    assert len(facts._equations) == count + 1  # All cold definitions are retained.
    # A later target can demand the same definition; it was not sliced away.
    facts.derive_targets((fields[-1] == 0,))
    assert cold[-1].get_id() in wrapped
    assert facts.evaluate(free).eq(free)


@pytest.mark.parametrize('private', [False, True])
def test_lazy_equations_own_roots_and_new_definitions(private):
    import gc

    context = z3.Context() if private else None
    x, y, z = z3.Ints('owned_index_x owned_index_y owned_index_z', ctx=context)
    facts = LiteralConsequences((term for term in (x == 3, y == x + 1)),
                               context=context, check=lambda: None)
    gc.collect()
    facts.derive_targets((y != 4,))
    assert z3.is_false(facts.constant_target(y != 4))
    facts.derive((z,))  # No definition yet.
    facts._add(z, y + 1, ())
    gc.collect()
    facts.derive((z,))
    assert facts.evaluate(z).as_long() == 5


def test_native_equation_index_rejects_foreign_context_before_reading_handles():
    private = z3.Context()
    with pytest.raises(ValueError, match='owned context'):
        LiteralConsequences((z3.Bool('foreign_index', ctx=private),),
                            context=None, check=lambda: None)
    with pytest.raises(ValueError, match='Boolean'):
        LiteralConsequences((z3.IntVal(7),), context=None, check=lambda: None)
    facts = LiteralConsequences((), context=None, check=lambda: None)
    with pytest.raises(ValueError, match='owned context'):
        facts._add(z3.Int('foreign_lhs', ctx=private), z3.IntVal(3, ctx=private), ())


@pytest.mark.parametrize('private', [False, True])
def test_literal_dag_reuses_owned_native_identity_without_id_queries(monkeypatch, private):
    from behave.sim.rtl import fixed_target

    context = z3.Context() if private else None
    x, y, free = z3.Ints('handle_x handle_y handle_free', ctx=context)
    roots = (x == 4, y == x + 1)
    calls = []
    original = fixed_target.native.Z3_get_ast_id

    def measured(*args):
        calls.append(None)
        return original(*args)

    monkeypatch.setattr(fixed_target.native, 'Z3_get_ast_id', measured)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    facts.derive_targets((y != 5, free == 7))
    assert z3.is_false(facts.constant_target(y != 5))
    assert facts.evaluate(free).eq(free)
    assert not calls


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('count', [32, 1400])
def test_demanded_chain_is_iterative_and_only_wakes_affected_equations(reverse, count, monkeypatch):
    values = z3.Ints(' '.join(f'demand_chain_{i}' for i in range(count)))
    roots = (values[0] == 0, *(b == a + 1 for a, b in zip(values, values[1:])))
    facts = LiteralConsequences(tuple(reversed(roots)) if reverse else roots,
                                context=None, check=lambda: None)
    evaluate, calls = facts._dag.evaluate_steps, []
    def measured(term):
        calls.append(term.get_id())
        return evaluate(term)
    monkeypatch.setattr(facts._dag, 'evaluate_steps', measured)
    target = values[-1] != count - 1
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    assert len(calls) < 5 * count


@pytest.mark.parametrize('shape', ['cycle', 'guarded-cycle', 'disjunction', 'free'])
def test_demand_does_not_choose_unknown_values_or_assert_unproved_guards(shape):
    x, y = z3.Ints('demand_cycle_x demand_cycle_y')
    guard = z3.Bool('demand_cycle_guard')
    roots = {
        'cycle': (x == y, y == x),
        'guarded-cycle': (z3.Implies(guard, x == 3), guard == (x == 3)),
        'disjunction': (z3.Or(x == 3, y == 4),),
        'free': (),
    }[shape]
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    target = x == 3
    facts.derive_targets((target,))
    assert facts.constant_target(target) is target
    solver = z3.Solver()
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


def test_demanded_predicates_and_point_reads_share_one_worklist():
    x, index, result = z3.Ints('predicate_dep_x predicate_dep_index predicate_dep_result')
    guard = z3.Bool('predicate_dep_guard')
    table = z3.Array('predicate_dep_table', z3.IntSort(), z3.IntSort())
    roots = (x == 3, guard == (x == 3), z3.Implies(guard, index < 10),
             index == x + 1, table[4] == 17,
             result == z3.If(index < 10, table[index], 0))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    target = result != 17
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    assert not facts._literal(facts.evaluate(table[5]))
    solver = z3.Solver()
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('mode', ['ordered', 'demand'])
@pytest.mark.parametrize('shape', ['comparison', 'or', 'negative'])
def test_asserted_predicate_does_not_expand_unrequested_arithmetic(private, mode, shape):
    context = z3.Context() if private else None
    data, result = z3.BitVecs('asserted_data asserted_result', 32, ctx=context)
    independent = z3.Bool('asserted_independent', ctx=context)
    arithmetic = data
    for _ in range(128):
        arithmetic = z3.UDiv(arithmetic * arithmetic + 7, arithmetic | 1)
    compare = z3.ULT(arithmetic, 17)
    predicate = {'comparison': compare, 'or': z3.Or(compare, independent),
                 'negative': z3.Not(compare)}[shape]
    roots = (predicate, result == z3.If(predicate,
             z3.BitVecVal(19, 32, ctx=context), z3.BitVecVal(23, 32, ctx=context)))
    target = result != 19
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    if mode == 'demand':
        # This target doesn't request the unrelated condition; registering
        # its asserted truth must not open that condition's arithmetic cone.
        facts.derive_targets((independent,))
    else:
        facts.derive_predicates()
    assert arithmetic.as_ast().value not in facts._dag.nodes
    assert data.as_ast().value not in facts._dag.nodes
    assert z3.is_true(facts.evaluate(predicate))
    facts.derive_targets((target,))
    assert z3.is_false(facts.constant_target(target))
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat
    # A premise about a compound expression never determines its input.
    assert facts.evaluate(data).eq(data)


@pytest.mark.parametrize('enabled', [False, True, None])
def test_unproved_predicate_guards_never_supply_facts(enabled):
    data = z3.Int('asserted_guard_data')
    guard = z3.Bool('asserted_guard')
    predicate = data * data > 7
    roots = [z3.Implies(guard, predicate)]
    if enabled is not None:
        roots.append(guard == enabled)
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    facts.derive_targets((z3.Not(predicate),))
    reduced = facts.constant_target(z3.Not(predicate))
    assert z3.is_false(reduced) is (enabled is True)
    solver = z3.Solver()
    solver.add(*roots, z3.Or(z3.Not(predicate) != reduced,
                            z3.Not(z3.And(*facts.literal_constraints()))))
    assert solver.check() == z3.unsat


def test_demand_restores_callbacks_on_owner_cancellation():
    value = z3.Int('cancel_demand')
    facts = LiteralConsequences((value == 3,), context=None, check=lambda: None)
    def cancel():
        raise InterruptedError('owned analysis deadline')
    facts.check = cancel
    with pytest.raises(InterruptedError, match='owned analysis deadline'):
        facts.derive_targets((value == 3,))
    assert facts._dag.notifications is None and facts._dag.request is None


@pytest.mark.parametrize('seed', range(16))
def test_demanded_mixed_circuits_preserve_the_complete_model_set(seed):
    import random
    randomizer = random.Random(seed)
    data, free = z3.BitVecs(f'demand_data_{seed} demand_free_{seed}', 4)
    guards = z3.Bools(f'demand_g_{seed} demand_h_{seed}')
    fields = [data, free]
    roots = [data == seed % 16]
    operations = [lambda a, b: a + b, lambda a, b: a * b,
                  lambda a, b: a ^ b, lambda a, b: z3.If(guards[0], a, b),
                  lambda a, b: z3.UDiv(a, b | 1)]
    for index in range(20):
        value = z3.BitVec(f'demand_wire_{seed}_{index}', 4)
        expression = randomizer.choice(operations)(randomizer.choice(fields), randomizer.choice(fields))
        equation = value == expression
        roots.append(z3.Implies(guards[1], equation) if index % 3 == 0 else equation)
        fields.append(value)
    roots.extend((guards[0] == (data < 4), z3.Implies(guards[0], guards[1]),
                  z3.Implies(guards[1], z3.Or(free == 3, free == 7))))
    randomizer.shuffle(roots)
    targets = tuple(field == (index % 16) for index, field in enumerate(fields))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    facts.derive_targets(targets)
    reduced = facts.constant_targets(targets)
    solver = z3.Solver()
    solver.add(*roots, z3.Or(*(a != b for a, b in zip(targets, reduced)),
                            z3.Not(z3.And(*facts.literal_constraints()))))
    assert solver.check() == z3.unsat
    # A second target batch must not inherit stale notification queues.
    facts.derive_targets((free == 0,))
    assert facts._dag.notifications is None and facts._dag.request is None


@pytest.mark.parametrize('private', (False, True))
def test_live_query_targets_keep_the_original_shared_arithmetic(private):
    context = z3.Context() if private else None
    fixed, data = z3.BitVecs('target_fixed target_data', 8, ctx=context)
    escape = z3.Bool('target_escape', ctx=context)
    roots = (fixed == 3,)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    target = z3.Or(escape, (data + fixed) * (data - fixed) == 7)
    assert facts.evaluate(target).eq(target)  # Unknown arithmetic is never specialized.
    assert facts.constant_target(target) is target  # Do not retain its duplicate circuit.
    assert z3.is_false(facts.constant_target(fixed != 3))
    assert z3.is_true(facts.constant_target(fixed == 3))
    uncertain = z3.Or(fixed != 3, escape)
    assert facts.constant_target(uncertain) is uncertain  # Unknown exits remain.
    for original in (target, fixed != 3, fixed == 3, uncertain):
        solver = z3.Solver(ctx=context)
        solver.add(*roots, original != facts.constant_target(original))
        assert solver.check() == z3.unsat
    batch = (target, fixed != 3, target, fixed == 3, uncertain)
    reduced = facts.constant_targets(batch)
    assert reduced[0] is target and reduced[2] is target and reduced[4] is uncertain
    assert z3.is_false(reduced[1]) and z3.is_true(reduced[3])
    assert facts.constant_targets(()) == ()


@pytest.mark.parametrize('private', (False, True))
def test_published_constants_are_redundant_not_partial_model_choices(private):
    context = z3.Context() if private else None
    x, y = z3.BitVecs('publish_x publish_y', 8, ctx=context)
    guard, unknown = z3.Bools('publish_guard publish_unknown', ctx=context)
    sort = z3.BitVecSort(8, ctx=context)
    table = z3.Array('publish_table', sort, sort)
    predicate = z3.Or(x < y, unknown)
    roots = (guard, x == 3, z3.Implies(guard, y == x + 1),
             table[x] == y, predicate)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    facts.derive((x, y, table[x]))
    facts.derive_predicates()
    published = facts.literal_constraints()
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*published)))
    assert solver.check() == z3.unsat
    # No default value is assigned to the rest of the array or a free choice.
    for flag in (False, True):
        solver = z3.Solver(ctx=context)
        solver.add(*roots, *published, unknown == flag, table[7] == 123)
        assert solver.check() == z3.sat


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('guard', (False, True, None))
def test_nested_guarded_definitions_need_proved_guards(private, guard):
    context = z3.Context() if private else None
    g, h = z3.Bools('guard nested_guard', ctx=context)
    x, y = z3.BitVecs('input successor', 8, ctx=context)
    roots = [x == 4, h, z3.Implies(g, z3.And(z3.Implies(h, y == x + 1)))]
    if guard is not None:
        roots.append(g == guard)
    result = _reduce(roots, (y,), y != 5)
    assert z3.is_false(result) is (guard is True)


@pytest.mark.parametrize('shape', ('or', 'not', 'existential', 'cycle', 'free-function'))
def test_nonconsequences_and_unconstrained_choices_remain_symbolic(shape):
    x, y, z = z3.Ints('unbound_x unbound_y unbound_z')
    g = z3.Bool('unbound_guard')
    roots = {
        'or': (z3.Or(g, x == 4),),
        'not': (z3.Not(z3.Implies(g, x == 4)),),
        'existential': (z3.Exists((x,), x == 4),),
        'cycle': (x == y, y == x),
        'free-function': (x == z3.Function('free_f', z3.IntSort(), z3.IntSort())(z),),
    }[shape]
    result = _reduce(roots, (x, y), x != 4)
    assert not z3.is_false(result) and not z3.is_true(result)


@pytest.mark.parametrize('width', (1, 4))
@pytest.mark.parametrize('flag', (False, True))
def test_boolean_bit_bridge_is_not_generalized_to_wide_carriers(width, flag):
    value, control = z3.BitVec('bridge_value', width), z3.Bool('bridge_control')
    roots = ((value == 0) == control, control == flag)
    result = _reduce(roots, (value,), value != (0 if flag else 1))
    assert z3.is_false(result) is (width == 1)


@pytest.mark.parametrize('private', (False, True))
def test_array_point_facts_never_assign_unobserved_cells(private):
    context = z3.Context() if private else None
    sort = z3.IntSort(ctx=context)
    array = z3.Array('queue', sort, sort)
    index, value = z3.Ints('index cell', ctx=context)
    roots = (array[0] == 1, array[1] == 9, index == 0, value == array[array[index]])
    assert z3.is_false(_reduce(roots, (value,), value != 9))
    assert not z3.is_false(_reduce(roots, (), array[2] != 0))


def test_fully_literal_arrays_do_not_use_python_recursion():
    sort = z3.IntSort()
    array = z3.Array('deep_array', sort, sort)
    value = z3.K(sort, 0)
    for i in range(1300):
        value = z3.Store(value, i, i + 1)
    assert z3.is_false(_reduce((array == value,), (), array[1299] != 1300))


@pytest.mark.parametrize('private', [False, True])
def test_literal_cache_reuses_syntax_classification_and_keeps_unknown_cells(monkeypatch, private):
    from behave.sim.rtl import fixed_target

    context = z3.Context() if private else None
    sort = z3.IntSort(ctx=context)
    unknown = z3.Int('literal_unknown', ctx=context)
    terms = (z3.BoolVal(False, ctx=context), z3.IntVal(7, ctx=context),
             z3.BitVecVal(5, 8, ctx=context), z3.RealVal('1/2', ctx=context),
             unknown, z3.K(sort, 3), z3.Store(z3.K(sort, 3), 0, 9),
             z3.Store(z3.K(sort, 3), 0, unknown))
    facts = LiteralConsequences((), context=context, check=lambda: None)
    expected = (True, True, True, False, False, True, True, False)
    assert tuple(facts._literal(term) for term in terms) == expected

    def reclassified(*args):
        pytest.fail('an owned immutable term must not be classified twice')

    monkeypatch.setattr(fixed_target, '_kind', reclassified)
    monkeypatch.setattr(fixed_target, '_literal', reclassified)
    for _ in range(8):
        assert tuple(facts._literal(term) for term in terms) == expected

    def cancelled():
        raise InterruptedError('cached literal cancellation')

    facts.check = cancelled
    with pytest.raises(InterruptedError, match='cached literal cancellation'):
        facts._literal(terms[0])


def test_construction_hint_cannot_supply_unasserted_values():
    value = z3.Int('hint_value')
    history = SimpleNamespace(member_plan=SourceMemberPlan(((value, z3.IntVal(0)),), ()))
    target = value != 0
    # The plan says zero, but no actual defining equation establishes that.
    reduced = reduce_fixed_source_defect((z3.BoolVal(True),), history, target, check=lambda: None)
    assert z3.is_true(z3.simplify(reduced == target))
    reduced = reduce_fixed_source_defect((value == 7,), history, target, check=lambda: None)
    assert z3.is_true(reduced)
    history.member_plan = None
    assert reduce_fixed_source_defect((value == 7,), history, target,
                                      check=lambda: None) is target


def test_cancellation_propagates_instead_of_returning_a_partial_proof():
    def cancel():
        raise InterruptedError('owner deadline')

    with pytest.raises(InterruptedError, match='owner deadline'):
        LiteralConsequences((z3.BoolVal(True),), context=None, check=cancel)
    facts = LiteralConsequences((), context=None, check=lambda: None)
    facts.check = cancel
    with pytest.raises(InterruptedError, match='owner deadline'):
        facts.evaluate(z3.BoolVal(False))


@pytest.mark.parametrize('guarded', [False, True])
def test_goal_batch_shares_only_asserted_literals_and_retains_escape(guarded):
    initial, successor, arbitrary = z3.Ints('initial successor arbitrary')
    guard, escape = z3.Bools('enabled escape')
    equation = successor == initial + 1
    roots = (initial == 3, z3.Implies(guard, equation) if guarded else equation,
             escape == (arbitrary < 0))
    plan = SourceMemberPlan(((initial, z3.IntVal(9)),),
                            (((successor, z3.IntVal(9)),),))
    history = SimpleNamespace(member_plan=plan, monitor_constraints=(roots[-1],))
    facts = source_literal_consequences(roots, history, context=None, check=lambda: None)
    for target in (initial != 3, successor != 4, escape, z3.Or(escape, successor != 4)):
        reduced = facts.evaluate(target)
        solver = z3.Solver()
        solver.add(*roots, target != reduced)
        assert solver.check() == z3.unsat
    assert z3.is_false(facts.evaluate(successor != 4)) is (not guarded)
    assert not z3.is_false(facts.evaluate(escape))  # No invented nonnegative input.


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('guard_value', (False, True, None))
def test_ordered_propagation_keeps_unknown_datapath_and_proved_guards(private, guard_value):
    context = z3.Context() if private else None
    initial, successor, unrelated = z3.Ints('demand_initial demand_next unrelated', ctx=context)
    enable, guard, escape = z3.Bools('demand_enable demand_guard demand_escape', ctx=context)
    data = z3.BitVec('large_datapath', 4096, ctx=context)
    unused = z3.BV2Int(data * data)
    roots = [initial == 3, guard == enable,
             z3.Implies(guard, successor == initial + 1),
             unrelated == unused, escape == (initial != 3)]
    if guard_value is not None:
        roots.append(enable == guard_value)
    plan = SourceMemberPlan(((initial, z3.IntVal(99, ctx=context)),), (
        ((guard, enable),), ((successor, initial + 1), (unrelated, unused))))
    history = SimpleNamespace(member_plan=plan, monitor_constraints=(roots[4],))
    target = successor != 4
    facts = source_literal_consequences(roots, history, context=context,
        check=lambda: None)
    assert z3.is_false(facts.evaluate(target)) is (guard_value is True)
    assert z3.is_false(facts.evaluate(escape))
    assert unrelated.as_ast().value not in facts._values
    for term in (target, escape):
        solver = z3.Solver(ctx=context)
        solver.add(*roots, term != facts.evaluate(term))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', (False, True))
def test_ordered_propagation_keeps_all_definitions_cycles_and_escape(private):
    context = z3.Context() if private else None
    x, y, first, second, unrelated = z3.Ints('dep_x dep_y dep_a dep_b dep_unused', ctx=context)
    guard, escape = z3.Bools('dep_guard dep_escape', ctx=context)
    roots = (z3.Implies(guard, x == first), z3.Implies(z3.Not(guard), x == second),
             y == x, x == y, escape == (second < 0), unrelated == first + 1)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    targets = (y > 0, escape)
    facts.derive((first, second, x, y, escape, unrelated))
    for target in targets:
        reduced = facts.evaluate(target)
        assert not z3.is_true(reduced) and not z3.is_false(reduced)
        solver = z3.Solver(ctx=context)
        solver.add(*roots, target != reduced)
        assert solver.check() == z3.unsat


def test_ordered_propagation_is_iterative_and_cancellable():
    values = z3.Ints(' '.join(f'chain_{i}' for i in range(1400)))
    roots = tuple(b == a + 1 for a, b in zip(values, values[1:]))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    facts.derive(values)
    assert not facts._values  # A chain without an initial fact stays unknown.

    def cancel():
        raise InterruptedError('propagation deadline')

    facts.check = cancel
    with pytest.raises(InterruptedError, match='propagation deadline'):
        facts.derive(values)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('enabled', [False, True, None])
def test_compound_protocol_validity_is_used_only_under_proved_guard(private, enabled):
    context = z3.Context() if private else None
    running, sampled = z3.Bools('running sampled', ctx=context)
    pointer, length, pending = z3.Ints('pointer length pending', ctx=context)
    present = pointer < length
    roots = [sampled == running, z3.Implies(sampled, present),
             pending == z3.If(present, 1, 0)]
    if enabled is not None:
        roots.append(running == enabled)
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (((sampled, running),),)),
                              monitor_constraints=(roots[2],))
    target = pending == 0
    facts = source_literal_consequences(roots, history, context=context,
                                      check=lambda: None)
    reduced = facts.evaluate(target)
    assert z3.is_false(reduced) is (enabled is True)
    solver = z3.Solver(ctx=context)
    solver.add(*roots, target != reduced)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('shape', ['or', 'negative', 'nested', 'comparison'])
def test_asserted_compound_fact_never_selects_a_disjunct_or_a_value(shape):
    x, y = z3.Ints('predicate_x predicate_y')
    a, b = x == 4, y == 7
    predicate = {'or': z3.Or(a, b), 'negative': z3.Not(z3.And(a, b)),
                 'nested': z3.And(z3.Or(a, b), z3.Not(x < 0)),
                 'comparison': x < y}[shape]
    facts = LiteralConsequences((predicate,), context=None, check=lambda: None)
    facts.derive_predicates()
    assert z3.is_true(facts.evaluate(predicate))
    for target in (a, b, x == 0, y == 0):
        reduced = facts.evaluate(target)
        assert not z3.is_true(reduced) and not z3.is_false(reduced)
        solver = z3.Solver()
        solver.add(predicate, target != reduced)
        assert solver.check() == z3.unsat


def test_compound_facts_do_not_hide_an_independent_escape():
    x, y = z3.Ints('compound_x compound_y')
    present = x < y
    escape = z3.Bool('compound_escape')
    target = z3.Or(z3.Not(present), escape)
    facts = LiteralConsequences((present,), context=None, check=lambda: None)
    facts.derive_predicates()
    assert facts.evaluate(target).eq(escape)
    facts.check = lambda: (_ for _ in ()).throw(InterruptedError('predicate deadline'))
    with pytest.raises(InterruptedError, match='predicate deadline'):
        facts.derive_predicates()


@pytest.mark.parametrize('private', [False, True])
def test_independent_compound_facts_need_no_global_substitution_index(monkeypatch, private):
    from behave.sim.rtl.symbolic_state import FixedBindings

    context = z3.Context() if private else None
    values = z3.Ints(' '.join(f'batch_predicate_{i}' for i in range(96)), ctx=context)
    roots = tuple(value < index for index, value in enumerate(values))
    builds = []
    original = FixedBindings.__init__

    def bindings(self, *args, **kwargs):
        builds.append(len(args[1]))
        original(self, *args, **kwargs)

    monkeypatch.setattr(FixedBindings, '__init__', bindings)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    facts.derive_predicates()
    assert all(z3.is_true(facts.evaluate(root)) for root in roots)
    assert not builds, 'exact point facts propagate through their users, not a global substitution'
    target = z3.Or(z3.Not(roots[-1]), values[0] == -1)
    reduced = facts.evaluate(target)
    solver = z3.Solver(ctx=context)
    solver.add(*roots, target != reduced)
    assert solver.check() == z3.unsat
    assert not z3.is_true(reduced) and not z3.is_false(reduced)


@pytest.mark.parametrize('reverse', [False, True])
def test_batched_compound_facts_revisit_newly_enabled_guards(reverse):
    a, b, c, d = z3.Ints('guard_a guard_b guard_c guard_d')
    first, second, last = a < b, b < c, c < d
    roots = [first, z3.Implies(first, second), z3.Implies(second, last)]
    if reverse:
        roots.reverse()
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    facts.derive_predicates()
    assert all(z3.is_true(facts.evaluate(term)) for term in (first, second, last))
    solver = z3.Solver()
    solver.add(*roots, z3.Not(last))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [8, 128])
def test_shared_guards_are_reduced_once_per_immutable_round(monkeypatch, private, count):
    context = z3.Context() if private else None
    enabled, limit = z3.Ints('shared_enabled shared_limit', ctx=context)
    guard = enabled < limit
    values = z3.Ints(' '.join(f'shared_value_{i}' for i in range(count)), ctx=context)
    predicates = tuple(value < index for index, value in enumerate(values))
    # The traversal sees guarded rows before the independent enabling fact.
    roots = (guard, *(z3.Implies(guard, term) for term in predicates))
    calls = []
    evaluate = LiteralConsequences.evaluate

    def measured(self, term):
        if term.eq(guard):
            calls.append(term)
        return evaluate(self, term)

    monkeypatch.setattr(LiteralConsequences, 'evaluate', measured)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    facts.derive_predicates()
    assert len(calls) == 2  # Guard once per round; asserted predicates need no evaluation.
    assert all(z3.is_true(facts.evaluate(term)) for term in predicates)
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Or(*(z3.Not(term) for term in predicates)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('count', [16, 64, 256])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_predicate_chains_wake_dependents_without_rescanning(monkeypatch, count, reverse, private):
    context = z3.Context() if private else None
    values = [z3.BitVec(f'chain_{index}', 8, ctx=context) for index in range(count)]
    predicates = [z3.ULT(value, 127) for value in values]
    roots = [predicates[0], *(z3.Implies(a, b) for a, b in zip(predicates, predicates[1:]))]
    if reverse:
        roots.reverse()
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    evaluate, calls = facts.evaluate, []

    def measured(term):
        calls.append(term)
        return evaluate(term)

    monkeypatch.setattr(facts, 'evaluate', measured)
    facts.derive_predicates()
    assert len(calls) <= 3 * count
    assert facts._dag.notifications is None
    assert all(z3.is_true(evaluate(term)) for term in predicates)
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Or(*(z3.Not(term) for term in predicates)))
    assert solver.check() == z3.unsat


def test_predicate_notifications_are_released_after_cancellation():
    values = z3.Ints('predicate_a predicate_b predicate_c predicate_d')
    predicates = [a < b for a, b in zip(values, values[1:])]
    roots = [predicates[0], *(z3.Implies(a, b) for a, b in zip(predicates, predicates[1:]))]
    facts = LiteralConsequences(roots, context=None, check=lambda: None)

    def stop():
        raise InterruptedError('predicate cancellation')

    facts.check = stop
    with pytest.raises(InterruptedError, match='predicate cancellation'):
        facts.derive_predicates()
    assert facts._dag.notifications is None
    facts.check = lambda: None
    facts.derive_predicates()
    assert all(z3.is_true(facts.evaluate(term)) for term in predicates)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('depth', [8, 64])
def test_asserted_shared_conjunction_is_walked_as_a_dag(monkeypatch, private, depth):
    context = z3.Context() if private else None
    guard, left, right, untouched = z3.Bools('shared_guard shared_left shared_right shared_free',
                                            ctx=context)
    conjunction = z3.And(left, right)
    shared = {conjunction.get_id()}
    leaves = [left, right]
    for index in range(depth):
        a, b = z3.Bools(f'shared_a_{index} shared_b_{index}', ctx=context)
        leaves.extend((a, b))
        first, second = z3.And(conjunction, a), z3.And(conjunction, b)
        conjunction = z3.And(first, second)
        shared.update(term.get_id() for term in (first, second, conjunction))
    # The assertion exposes a conjunction only after reducing its guard.
    # The constructor's own root deduplication cannot handle this traversal.
    roots = (guard, z3.If(guard, conjunction, untouched))
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    facts.evaluate(roots[1])
    children, visited = z3.ExprRef.children, set()

    def once(term):
        if term.get_id() in shared:
            assert term.get_id() not in visited, 'shared conjunction expanded more than once'
            visited.add(term.get_id())
        return children(term)

    with monkeypatch.context() as patch:
        patch.setattr(z3.ExprRef, 'children', once)
        facts.derive_predicates()
    assert visited == shared
    assert all(z3.is_true(facts.evaluate(term)) for term in leaves)
    assert facts.evaluate(untouched).eq(untouched)
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat
    for value in (False, True):
        solver = z3.Solver(ctx=context)
        solver.add(*roots, *facts.literal_constraints(), untouched == value)
        assert solver.check() == z3.sat


@pytest.mark.parametrize('kind', ['bool', 'int', 'bv'])
def test_proved_scalar_literals_never_traverse_compound_bindings(monkeypatch, kind):
    from behave.sim.rtl.symbolic_state import FixedBindings

    sort = {'bool': z3.BoolSort, 'int': z3.IntSort, 'bv': lambda: z3.BitVecSort(8)}[kind]()
    value = z3.Const('constant_guard_value', sort)
    literal = sort.cast(True if kind == 'bool' else 4)
    index = z3.Int('constant_guard_index')
    roots = (value == literal, index < 9)
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    facts.derive_predicates()
    monkeypatch.setattr(FixedBindings, 'substitute',
                        lambda *args: pytest.fail('a literal has no replaceable subterms'))
    assert facts.evaluate(value).eq(literal)


def test_guard_cache_does_not_cross_scalar_or_compound_publication():
    gate, scalar = z3.Bools('cache_gate cache_scalar')
    x, y, z = z3.Ints('cache_x cache_y cache_z')
    first, last = x < y, y < z
    roots = (z3.Implies(gate, scalar), z3.Implies(scalar, first),
             z3.Implies(first, last))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    facts.derive_predicates()
    assert not z3.is_true(facts.evaluate(last))
    # Extend the asserted roots, then propagate; an earlier unknown guard
    # cannot survive the changed interpretation or compound-fact rounds.
    roots = (*roots, gate)
    facts._add(gate, z3.BoolVal(True), ())
    facts.derive((scalar,))
    facts.derive_predicates()
    assert z3.is_true(facts.evaluate(last))
    solver = z3.Solver()
    solver.add(*roots, z3.Not(last))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
def test_proved_reductions_survive_new_facts_but_unknowns_do_not(monkeypatch, private):
    context = z3.Context() if private else None
    x, y = z3.Ints('resolved_x resolved_y', ctx=context)
    gate = z3.Bool('resolved_gate', ctx=context)
    roots = (x == 4, gate, z3.Implies(gate, y == 9))
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    target, pending = x + 2, y + 2
    fold, calls = facts._dag._fold, []

    def measured(key):
        calls.append(key)
        return fold(key)

    monkeypatch.setattr(facts._dag, '_fold', measured)
    assert facts.evaluate(target).as_long() == 6
    unresolved = facts.evaluate(pending)
    assert not z3.is_int_value(unresolved)
    assert z3.is_true(z3.simplify(unresolved == pending))
    calls.clear()
    facts.derive((y,))
    assert facts.evaluate(target).as_long() == 6
    assert facts.evaluate(x).as_long() == 4
    assert calls.count(pending.as_ast().value) == 1
    assert target.as_ast().value not in calls
    calls.clear()
    assert facts.evaluate(pending).as_long() == 11
    assert not calls  # Already-proved expressions are not evaluated again.
    # Publishing y has already notified the cached expression y + 2.
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Or(target != facts.evaluate(target), pending != facts.evaluate(pending)))
    assert solver.check() == z3.unsat
    facts.check = lambda: (_ for _ in ()).throw(InterruptedError('cached deadline'))
    with pytest.raises(InterruptedError, match='cached deadline'):
        facts.evaluate(target)


@pytest.mark.parametrize('private', [False, True])
def test_sibling_successors_share_partial_evaluation_without_inventing_values(monkeypatch, private):
    context = z3.Context() if private else None
    data = z3.BitVec('shared_datapath', 32, ctx=context)
    common = z3.UDiv(data * data + 7, data | 1)
    fields = tuple(z3.BitVec(f'shared_next_{i}', 32, ctx=context) for i in range(64))
    roots = tuple(field == common + i for i, field in enumerate(fields))
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    fold, calls = facts._dag._fold, []

    def measured(key):
        calls.append(key)
        return fold(key)

    monkeypatch.setattr(facts._dag, '_fold', measured)
    facts.derive(fields)
    assert calls.count(common.as_ast().value) == 1  # Not once per successor.
    assert not facts.literal_constraints()  # No arbitrary data was concretized.
    facts._add(data, z3.BitVecVal(3, 32, ctx=context), ())
    facts.derive(fields)
    assert all(facts.evaluate(field).as_long() == 5 + i for i, field in enumerate(fields))
    solver = z3.Solver(ctx=context)
    solver.add(*roots, data == 3, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


def test_batch_propagation_keeps_a_dependency_chain_linear_and_guarded(monkeypatch):
    values = z3.Ints(' '.join(f'batched_chain_{i}' for i in range(128)))
    guard = z3.Bool('batched_chain_guard')
    roots = (values[0] == 0, guard == (values[-1] == 127),
             *(b == a + 1 for a, b in zip(values, values[1:])))
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    fold, calls = facts._dag._fold, []

    def measured(key):
        calls.append(key)
        return fold(key)

    monkeypatch.setattr(facts._dag, '_fold', measured)
    facts.derive((*values, guard))
    assert z3.is_true(facts.evaluate(guard))
    assert len(calls) <= 5 * len(values)  # Dependency notifications, not repeated whole tails.
    assert facts.evaluate(values[-1]).as_long() == 127
    solver = z3.Solver()
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [8, 128])
def test_history_steps_share_evaluation_when_no_new_literals_exist(monkeypatch, private, count):
    from behave.sim.rtl.source_relation import SourceIntervalEncoding
    from behave.sim.rtl.fixed_target import _LiteralDAG

    context = z3.Context() if private else None
    data = z3.BitVec('history_public_data', 32, ctx=context)
    common = z3.UDiv(data * data + 7, data | 1)
    fields = tuple(z3.BitVec(f'history_unknown_{i}', 32, ctx=context) for i in range(count))
    roots = tuple(field == common + i for i, field in enumerate(fields))
    steps = tuple(
        SourceIntervalEncoding((equation,), {}, {}, {}, {}, (), None, None,
                               {}, None, None, step_definitions=((equation,),))
        if i % 2 else ((field, common + i),)
        for i, (field, equation) in enumerate(zip(fields, roots, strict=True)))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), steps))
    fold, calls = _LiteralDAG._fold, []

    def measured(self, key):
        calls.append(key)
        return fold(self, key)

    monkeypatch.setattr(_LiteralDAG, '_fold', measured)
    facts = source_literal_consequences(roots, history, context=context, check=lambda: None)
    assert calls.count(common.as_ast().value) == 1  # One indexing of the shared datapath.
    assert not facts.literal_constraints()  # Unknown public input stays unknown.
    assert facts.evaluate(data).eq(data)


@pytest.mark.parametrize('private', [False, True])
def test_history_batch_retains_forward_constant_dependencies(private):
    context = z3.Context() if private else None
    fields = z3.Ints(' '.join(f'history_chain_{i}' for i in range(32)), ctx=context)
    enabled, escaped = z3.Bools('history_enabled history_escaped', ctx=context)
    steps = tuple(((b, a + 1),) for a, b in zip(fields, fields[1:]))
    roots = (fields[0] == 0, *(b == a + 1 for a, b in zip(fields, fields[1:])),
             enabled == (fields[-1] == 31), escaped == z3.Not(enabled))
    history = SimpleNamespace(
        member_plan=SourceMemberPlan(((fields[0], z3.IntVal(99, ctx=context)),),
                                    (*steps, ((enabled, fields[-1] == 31),))),
        monitor_constraints=(roots[-1],))
    facts = source_literal_consequences(roots, history, context=context, check=lambda: None)
    assert facts.evaluate(fields[-1]).as_long() == 31
    assert z3.is_true(facts.evaluate(enabled)) and z3.is_false(facts.evaluate(escaped))
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
def test_new_facts_update_shared_bv_dependencies_without_any_partial_model(monkeypatch, private):
    context = z3.Context() if private else None
    data, unknown = z3.BitVecs('refresh_data refresh_unknown', 32, ctx=context)
    common = data * data + 7
    facts = LiteralConsequences((), context=context, check=lambda: None)
    roots = (common, common + 1, unknown)
    monkeypatch.setattr(z3.ModelRef, 'eval',
        lambda *_a, **_k: pytest.fail('constant propagation must not evaluate a partial model'))
    facts._evaluate_many(roots)
    facts._add(data, z3.BitVecVal(3, 32, ctx=context), ())
    reduced = facts._evaluate_many(roots)
    assert [value.as_long() for value in reduced[:2]] == [16, 17]
    assert reduced[2].eq(unknown)
    assert facts.constant_target(unknown == 9).eq(unknown == 9)


@pytest.mark.parametrize('count', [16, 256])
def test_fanout_updates_only_affected_users_without_symbolic_simplification(monkeypatch, count):
    data, independent = z3.BitVecs('not_expanded independent_arithmetic', 32)
    shared = z3.UDiv(data * data + 7, data | 1)
    targets = tuple(shared + index for index in range(count))
    facts = LiteralConsequences((), context=None, check=lambda: None)
    simplify, folds = z3.simplify, []
    def closed_only(term, **options):
        assert all(facts._literal(child) for child in term.children())
        folds.append(term)
        return simplify(term, **options)
    monkeypatch.setattr(z3, 'simplify', closed_only)
    assert all(a.eq(b) for a, b in zip(targets, facts._evaluate_many(targets), strict=True))
    assert not folds
    node_count = len(facts._dag.nodes)
    facts._add(independent, z3.BitVecVal(9, 32), ())
    facts._evaluate_many(targets)
    assert not folds and len(facts._dag.nodes) == node_count
    facts._add(data, z3.BitVecVal(3, 32), ())
    actual = facts._evaluate_many(targets)
    assert [value.as_long() for value in actual] == [5 + index for index in range(count)]
    assert len(folds) <= count + 5


@pytest.mark.parametrize('reverse', [False, True])
def test_normalized_point_reads_track_later_facts_without_array_completion(reverse):
    array = z3.Array('later_point', z3.IntSort(), z3.IntSort())
    index = z3.Int('later_point_index')
    facts = LiteralConsequences((), context=None, check=lambda: None)
    term = array[index]
    facts.evaluate(term)
    pairs = [(index, z3.IntVal(3)), (array[3], z3.IntVal(17))]
    for lhs, rhs in reversed(pairs) if reverse else pairs:
        facts._add(lhs, rhs, ())
        facts.evaluate(term)
    assert facts.evaluate(term).as_long() == 17
    assert facts.evaluate(array[4]).eq(array[4])


def test_literal_facts_cannot_cross_native_contexts():
    facts = LiteralConsequences((), context=None, check=lambda: None)
    private = z3.Context()
    with pytest.raises(ValueError, match='share one context'):
        facts.evaluate(z3.BoolVal(False, ctx=private))


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('take_left', [False, True])
def test_ordered_history_does_not_visit_a_proved_inactive_datapath(private, take_left):
    context = z3.Context() if private else None
    gate, successor_gate = z3.Bools('lazy_gate lazy_successor_gate', ctx=context)
    data, result = z3.BitVecs('lazy_data lazy_result', 32, ctx=context)
    inactive = data
    for _ in range(128):
        inactive = z3.UDiv(inactive * inactive + 7, inactive | 1)
    selected = z3.BitVecVal(19, 32, ctx=context)
    value = z3.If(successor_gate, selected, inactive) if take_left else z3.If(
        successor_gate, inactive, selected)
    roots = (gate == take_left, successor_gate == gate, result == value)
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    facts.derive((successor_gate, result))
    assert facts.evaluate(result).as_long() == 19
    assert inactive.as_ast().value not in facts._dag.nodes
    assert data.as_ast().value not in facts._dag.nodes
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat
    # An independent later request for that expression must still see it.
    assert facts.evaluate(inactive).eq(inactive)
    assert inactive.as_ast().value in facts._dag.nodes


@pytest.mark.parametrize('operation', ['and', 'or', 'implies'])
def test_known_boolean_short_circuit_does_not_index_an_irrelevant_cone(operation):
    flag = z3.Bool('short_circuit_flag')
    data = z3.BitVec('short_circuit_data', 32)
    expression = z3.UDiv(data * data + 7, data | 1) == 4
    if operation == 'and':
        roots, target, expected = (z3.Not(flag),), z3.And(flag, expression), False
    elif operation == 'or':
        roots, target, expected = (flag,), z3.Or(expression, flag), True
    else:
        roots, target, expected = (z3.Not(flag),), z3.Implies(flag, expression), True
    facts = LiteralConsequences(roots, context=None, check=lambda: None)
    assert z3.is_true(facts.evaluate(target)) is expected
    assert expression.as_ast().value not in facts._dag.nodes
    solver = z3.Solver()
    solver.add(*roots, target != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('operation', ('and', 'or'))
@pytest.mark.parametrize('unknown_prefix', (False, True))
def test_newly_proved_boolean_short_circuit_stops_before_later_cones(
        private, operation, unknown_prefix):
    context = z3.Context() if private else None
    value = z3.BitVec('lazy_control_value', 8, ctx=context)
    unknown = z3.Bool('lazy_control_unknown', ctx=context)
    leaves = tuple(z3.Bool(f'lazy_unused_{i}', ctx=context) for i in range(128))
    expensive = z3.Xor(z3.And(*leaves[:64]), z3.Or(*leaves[64:]))
    expected = operation == 'or'
    proved = value == value if expected else value != value
    operands = ((unknown,) if unknown_prefix else ()) + (proved, expensive)
    target = (z3.Or if expected else z3.And)(*operands)
    facts = LiteralConsequences((), context=context, check=lambda: None)
    result = facts.evaluate(target)
    assert z3.is_true(result) if expected else z3.is_false(result)
    assert expensive.as_ast().value not in facts._dag.nodes
    assert all(leaf.as_ast().value not in facts._dag.nodes for leaf in leaves)
    if unknown_prefix:
        facts._add(unknown, z3.BoolVal(not expected, ctx=context), ())
        assert facts.evaluate(target).eq(result)
        assert expensive.as_ast().value not in facts._dag.nodes
    # Skipping an irrelevant sibling is not global pruning: another target
    # must still be able to request its original symbolic expression.
    assert facts.evaluate(expensive).eq(expensive)
    solver = z3.Solver(ctx=context)
    solver.add(target != result)
    assert solver.check() == z3.unsat


def test_unknown_condition_keeps_both_paths_and_later_dependency_updates():
    gate = z3.Bool('unknown_path_gate')
    left, right = z3.BitVecs('unknown_path_left unknown_path_right', 8)
    target = z3.If(gate, left + 1, right + 2)
    facts = LiteralConsequences((), context=None, check=lambda: None)
    assert facts.evaluate(target).eq(target)
    assert left.as_ast().value in facts._dag.nodes and right.as_ast().value in facts._dag.nodes
    facts._add(gate, z3.BoolVal(False), ())
    facts._add(right, z3.BitVecVal(7, 8), ())
    assert facts.evaluate(target).as_long() == 9
    assert facts.evaluate(left).eq(left)


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('operation', ('add', 'multiply', 'divide', 'less', 'concat'))
def test_symbolic_operand_defers_other_arithmetic_until_its_value_is_proved(private, operation):
    context = z3.Context() if private else None
    first, data = z3.BitVecs('deferred_first deferred_data', 8, ctx=context)
    suffix = data
    for i in range(128):
        suffix = z3.UDiv(suffix * suffix + i + 1, suffix | 1)
    target = {
        'add': lambda: first + suffix,
        'multiply': lambda: first * suffix,
        'divide': lambda: z3.UDiv(first, suffix),
        'less': lambda: z3.ULT(first, suffix),
        'concat': lambda: z3.Concat(first, suffix),
    }[operation]()
    facts = LiteralConsequences((), context=context, check=lambda: None)
    assert facts.evaluate(target).eq(target)
    assert suffix.as_ast().value not in facts._dag.nodes
    assert data.as_ast().value not in facts._dag.nodes
    assert len(facts._dag.nodes) == 2  # Target and its first unknown operand only.

    # Facts can arrive out of operand order. No read or default is invented.
    data_value = z3.BitVecVal(3, 8, ctx=context)
    first_value = z3.BitVecVal(11, 8, ctx=context)
    facts._add(data, data_value, ())
    assert facts.evaluate(target).eq(target)
    assert suffix.as_ast().value not in facts._dag.nodes
    facts._add(first, first_value, ())
    actual = facts.evaluate(target)
    expected = z3.simplify(z3.substitute(target, (first, first_value), (data, data_value)))
    assert actual.eq(expected)
    solver = z3.Solver(ctx=context)
    solver.add(first == first_value, data == data_value, target != actual)
    assert solver.check() == z3.unsat


def test_deferred_operand_resumes_one_dependency_at_a_time():
    first, second, data = z3.BitVecs('deferred_a deferred_b deferred_c', 8)
    suffix = (data + 3) * (data + 5)
    target = z3.Concat(first, second, suffix)
    facts = LiteralConsequences((), context=None, check=lambda: None)
    assert facts.evaluate(target).eq(target)
    assert second.as_ast().value not in facts._dag.nodes
    facts._add(first, z3.BitVecVal(1, 8), ())
    assert facts.evaluate(target).eq(target)
    assert second.as_ast().value in facts._dag.nodes
    assert suffix.as_ast().value not in facts._dag.nodes
    facts._add(second, z3.BitVecVal(2, 8), ())
    assert facts.evaluate(target).eq(target)
    assert suffix.as_ast().value in facts._dag.nodes
    facts._add(data, z3.BitVecVal(4, 8), ())
    assert facts.evaluate(target).as_long() == (1 << 16) | (2 << 8) | 63


def test_deferred_dependency_resumption_uses_no_python_recursion():
    fields = z3.BitVecs(' '.join(f'deferred_deep_{i}' for i in range(1300)), 16)
    target = z3.BitVecVal(0, 16)
    for field in reversed(fields):
        target = field + target
    facts = LiteralConsequences((), context=None, check=lambda: None)
    assert facts.evaluate(target).eq(target)
    assert len(facts._dag.nodes) == 2
    for field in fields:
        facts._add(field, z3.BitVecVal(1, 16), ())
    assert facts.evaluate(target).as_long() == len(fields)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('forward', [False, True])
def test_ordered_definitions_prune_later_guards_before_indexing_datapaths(private, forward):
    context = z3.Context() if private else None
    data = z3.BitVec('ordered_guard_data', 32, ctx=context)
    enabled = z3.Bool('ordered_guard_enabled', ctx=context)
    result = z3.Int('ordered_guard_result', ctx=context)
    predicates = tuple(z3.Bool(f'ordered_guard_leaf_{i}', ctx=context) for i in range(128))
    unused = z3.And(*predicates)
    guard = z3.Or(enabled, unused)
    # Neither constructor metadata nor a partial model provides the fact.
    # It follows from a real earlier equation in the asserted history.
    roots = (enabled == (data == data), z3.Implies(guard, result == 9))
    facts = LiteralConsequences(roots, context=context, check=lambda: None)
    facts.derive((result, enabled) if forward else (enabled, result))
    assert facts.evaluate(result).as_long() == 9
    if not forward:
        assert unused.as_ast().value not in facts._dag.nodes
        assert all(term.as_ast().value not in facts._dag.nodes for term in predicates)
    # Unknown operands have not acquired a value merely because their branch
    # is unnecessary. Forward references use the same dependency worklist.
    assert facts.evaluate(data).eq(data)
    solver = z3.Solver(ctx=context)
    solver.add(*roots, z3.Not(z3.And(*facts.literal_constraints())))
    assert solver.check() == z3.unsat
