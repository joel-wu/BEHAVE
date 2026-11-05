"""Compare complete observables at the same symbolic input/state boundary."""
from unittest.mock import patch

import pytest
import z3

from behave.sim.symbolic import encoder, ir, ranges, typecheck as types
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.guarded_reads import GuardedReads
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import _tree
from behave.sim.core.contract import FiniteType


def flat(value):
    if isinstance(value, FunctionalArray):
        return flat(_tree(value))
    if isinstance(value, (tuple, list)):
        return [leaf for child in value for leaf in flat(child)]
    return [value]


def compare(proc, env):
    with patch.object(GuardedReads, 'read', lambda self, value, guard: value):
        boundary = encoder.encode_step(proc, env=env, tag='boundary').pre
        plain = encoder.encode_step(proc, pre=boundary, env=env, tag='guarded')
    small = encoder.encode_step(proc, pre=plain.pre, env=env, tag='active')
    solver = z3.Solver()
    solver.add(plain.constraint, small.constraint)
    assert solver.check() == z3.sat
    different = [plain.ok != small.ok, plain.error != small.error]
    for field in ('state_post', 'post', 'hits', 'hit_occurrences', 'final'):
        original, optimized_values = getattr(plain, field), getattr(small, field)
        assert original.keys() == optimized_values.keys()
        for name, value in original.items():
            original_values, new_values = flat(value), flat(optimized_values[name])
            if field == 'hit_occurrences':
                # A proved-dead visit can be omitted, not changed into a hit.
                original_values = [term for term in original_values if not z3.is_false(term)]
                new_values = [term for term in new_values if not z3.is_false(term)]
            for a, b in zip(original_values, new_values, strict=True):
                if z3.is_bv(a):
                    a, b = encoder.widen(a, b)
                different.append(a != b)
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


def make(body, *, kinds=None):
    proc = ir.Procedure(('q',), ('a', 'n'), ('t', 'i'), (), body, initial={'q': 0},
                        types={'i': types.INTEGER, **(kinds or {})},
                        signature=ProcedureSignature((('a', FiniteType(3, signed=True)), ('n', FiniteType(2))), ()))
    env = {'q': ranges.Range(-16, 15), 'a': ranges.Range(-4, 3), 'n': ranges.Range(0, 3)}
    return proc, ranges.fixpoint(proc, env)


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('array', [False, True])
def test_full_outputs_failure_visits_and_audit_values(loop, failure, array):
    value = ir.Op('add', ir.Var('a'), ir.Lit(1))
    read = ir.Sel(ir.Var('t'), ir.Lit(1)) if array else ir.Var('t')
    body = ir.If(ir.Op('lt', ir.Var('a'), ir.Lit(2)), ir._sequence((
        ir.Assign('t', ir.Op('arraylit', ir.Var('a'), value) if array else value),
        ir.If(ir.Op('eq', read, ir.Lit(1)), ir.Mark('one'), ir.Skip()),
        ir.If(ir.Op('eq', read, ir.Lit(0)), ir.Fail('RTLFatal', 'failure') if failure else ir.Mark('zero'), ir.Skip()),
        ir.Assign('q', read))), ir.Assign('q', ir.Lit(0)))
    if loop:
        body = ir.For('i', ir.Var('n'), body)
    proc, env = make(body, kinds={'t': types.Array(2)} if array else None)
    compare(proc, env)


@pytest.mark.parametrize('body', [
    ir._sequence((ir.Assign('t', ir.Lit(1)), ir.Assign('t', ir.Lit(2)), ir.Assign('q', ir.Var('t')))),
    ir._sequence((ir.Assign('t', ir.Lit(-2)), ir.For('i', ir.Var('n'), ir.Assign('t', ir.Var('a'))), ir.Assign('q', ir.Var('t')))),
    ir._sequence((ir.Assign('t', ir.Lit(-2)), ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(1)), ir.Assign('t', ir.Lit(2)), ir.Skip()),
                  ir.Assign('q', ir.Var('t')))),
    ir._sequence((
        ir.Assign('t', ir.Lit(-2)),
        ir.For('i', ir.Lit(2), ir._sequence((
            ir.Assign('q', ir.Var('t')), ir.Assign('t', ir.Var('a'))))),
    )),
])
def test_redefinitions_untaken_paths_and_loop_carried_values(body):
    compare(*make(body))


@pytest.mark.parametrize('body', [
    'always_comb if(a==3) q=a; else q=0;',
    'always @(posedge clk) q<=a; always @(negedge clk) if(a==q) r=1; else r=0;',
    'always @(posedge clk) q=a; always @(posedge clk) if(q==a) r=1; else r=0;',
    ('reg pulse; always @(posedge clk) begin pulse<=a[0]; pulse<=0; end '
     'always @(posedge pulse) if(a==3) q=1; else q=0;'),
    'always @(posedge clk) begin if(a==3) $fatal(1,"stop"); q<=a; end',
    'always_comb case(a) 0:q=1; 1:q=2; 2,3:q=0; default:q=3; endcase',
])
def test_source_scheduler_arbitrary_states(tmp_path, body):
    from tests.sim.test_rtl_source_scheduler import _lower
    machine, _ = _lower(tmp_path, f'module M(input clk,input [1:0] a,output reg [1:0] q,r); {body} endmodule')
    compare(machine.procedure, ranges.fixpoint(machine.procedure,
            {**machine.procedure.state_ranges, **machine.input_ranges}))


def test_alias_reduction_does_not_overwrite_an_existing_definition():
    enc = encoder._Encoder(None, {})
    x, y = z3.Bools('x y')
    term = enc.bind(z3.And(x, y))
    assert enc.bind(z3.And(term, term)).eq(term)
    assert not enc._guarded_reads._known(term)[1]


@pytest.mark.parametrize("arms", [(1, 0), (0, 1), (7, -2)])
@pytest.mark.parametrize("value", [0, 1, 7, -2, 3])
@pytest.mark.parametrize("negated", [False, True])
def test_integer_flag_comparison_recovers_only_exact_boolean_facts(arms, value, negated):
    reads = GuardedReads()
    condition, guard = z3.Bools("flag_condition flag_guard")
    flag = z3.BitVec("flag_value", 8)
    choice = z3.If(condition, z3.BitVecVal(arms[0], 8), z3.BitVecVal(arms[1], 8))
    predicate = flag != value if negated else flag == value
    reads.define(flag, choice)
    reads.define(guard, predicate)
    normalized = reads._definitions[guard.get_id()]
    solver = z3.Solver()
    solver.add(flag == choice, normalized != predicate)
    assert solver.check() == z3.unsat
    if value in arms:
        selected = (value == arms[0]) != negated
        current = z3.If(condition, z3.BitVecVal(11, 8), z3.BitVecVal(22, 8))
        assert reads.read(current, guard).as_long() == (11 if selected else 22)


def test_integer_flag_does_not_open_arithmetic_or_nonliteral_histories():
    reads = GuardedReads()
    condition, guard = z3.Bools("symbolic_flag_condition symbolic_flag_guard")
    flag, other = z3.BitVecs("symbolic_flag_value symbolic_other", 8)
    reads.define(flag, z3.If(condition, z3.BitVecVal(1, 8), other))
    predicate = flag != 0
    reads.define(guard, predicate)
    assert reads._definitions[guard.get_id()].eq(predicate)
    assert not reads.impossible(z3.And(guard, z3.Not(condition)))


@pytest.mark.parametrize('contradiction', (False, True))
def test_repeated_command_guard_reuses_the_same_contradiction_check(contradiction):
    reads = GuardedReads()
    a, b, rejected, reached = z3.Bools('reuse_a reuse_b reuse_rejected reuse_reached')
    reads.define(rejected, z3.And(a, b))
    reads.define(reached, z3.And(z3.Not(rejected), a, b) if contradiction
                 else z3.And(z3.Not(rejected), a))
    with patch.object(reads, '_condition', wraps=reads._condition) as condition:
        assert reads.impossible(reached) is contradiction
        calls = condition.call_count
        assert calls > 0  # Exercise compound facts, not only a & !a.
        for _ in range(30):
            assert reads.impossible(reached) is contradiction
        assert condition.call_count == calls
    # A new guard has its own facts; neither verdict leaks to its neighbor.
    assert reads.impossible(z3.And(z3.Not(rejected), a, b))
    assert not reads.impossible(z3.And(z3.Not(rejected), z3.Not(a)))


@pytest.mark.parametrize('disjunction', [False, True])
def test_compound_checks_reuse_only_the_deciding_child_facts(disjunction):
    reads = GuardedReads()
    a, b, compound = z3.Bools('compound_a compound_b compound_owner')
    reads.define(compound, z3.Or(a, b) if disjunction else z3.And(a, b))
    asserted = compound if disjunction else z3.Not(compound)
    first = z3.Not(a) if disjunction else a
    deciding = z3.Not(b) if disjunction else b
    with patch.object(reads, '_condition', wraps=reads._condition) as condition:
        assert not reads.impossible(z3.And(asserted, first))  # b is unknown.
        calls = condition.call_count
        compounds = len(reads._compound_checks)
        assert 1 <= compounds <= 2  # The owned alias and its Boolean body.
        assert calls == 2 * compounds
        guard = asserted
        for index in range(64):
            guard = z3.And(guard, z3.Bool(f'unrelated_{index}'))
            assert not reads.impossible(guard)
        # a is no longer known, but unknown b still prevents contradiction.
        assert condition.call_count == calls
        assert reads.impossible(z3.And(asserted, first, deciding))
        assert condition.call_count > calls
        assert not reads.impossible(z3.And(asserted, first))
        assert not reads.impossible(z3.And(asserted, z3.Not(first), deciding))
    assert len(reads._compound_checks) == compounds  # Not one per guard/history.


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('disjunction', [False, True])
def test_compound_reuse_matches_uncached_checks_across_changing_contexts(private, alias, disjunction):
    from itertools import product
    from behave.sim.symbolic.guarded_reads import _Facts

    context = z3.Context() if private else None
    reads, reference = GuardedReads(), GuardedReads()
    a, b, c, nested, compound = z3.Bools('ctx_a ctx_b ctx_c ctx_nested ctx_compound', ctx=context)
    inner = z3.And(b, c) if disjunction else z3.Or(b, c)
    outer = z3.Or(a, nested if alias else inner) if disjunction else z3.And(a, nested if alias else inner)
    for instance in (reads, reference):
        instance.define(nested, inner)
        instance.define(compound, outer)
    asserted = compound if disjunction else z3.Not(compound)
    for values in product((None, True, False), repeat=3):
        guard = z3.And(asserted, *(term if value else z3.Not(term)
                                  for term, value in zip((a, b, c), values, strict=True)
                                  if value is not None))
        yes, no = reference._known(guard)
        expected = bool(yes & no) or any(
            all(check(reference._condition(child, _Facts.of(yes), _Facts.of(no)))
                for child in table[index])
            for indices, table, check in (
                (reference._denied_atoms & no, reference._denied_conjunctions, z3.is_true),
                (reference._required_atoms & yes, reference._required_disjunctions, z3.is_false))
            for index in indices)
        assert reads.impossible(guard) == expected
        if expected:
            solver = z3.Solver(ctx=context)
            solver.add(nested == inner, compound == outer, guard)
            assert solver.check() == z3.unsat


@pytest.mark.parametrize('case', ['conjunction', 'false-disjunction', 'true-disjunction', 'outside'])
def test_only_known_boolean_consequences_can_select_an_arm(case):
    reads = GuardedReads()
    a, b, guard_name = z3.Bools('fact_a fact_b fact_guard')
    x, y, stored = z3.BitVecs('fact_x fact_y fact_stored', 8)
    guard = {
        'conjunction': z3.And(a, b),
        'false-disjunction': z3.Not(z3.Or(a, b)),
        'true-disjunction': z3.Or(a, b),
        'outside': z3.BoolVal(True),
    }[case]
    reads.define(guard_name, guard)
    conditional = z3.If(a, x, y)
    reads.define(stored, conditional)
    result = reads.read(stored, guard_name)
    solver = z3.Solver()
    solver.add(guard_name == guard, stored == conditional, guard_name, result != stored)
    assert solver.check() == z3.unsat
    if case == 'conjunction':
        assert result.eq(x)
    elif case == 'false-disjunction':
        assert result.eq(y)
    else:
        assert result.eq(stored)


def test_deep_guard_dag_is_iterative_and_does_not_leak_to_other_reads():
    reads = GuardedReads()
    first = last = z3.Bool('guard_0')
    for index in range(1500):
        parent = z3.Bool(f'guard_{index+1}')
        reads.define(parent, z3.And(last, z3.Bool(f'input_guard_{index}')))
        last = parent
    a, b, stored = z3.BitVecs('deep_a deep_b deep_stored', 8)
    reads.define(stored, z3.If(first, a, b))
    assert reads.read(stored, last).eq(a)
    assert reads.read(stored, None).eq(stored)
    assert reads.read(stored, z3.BoolVal(True)).eq(stored)


def test_unknown_outer_choice_keeps_both_arms_but_refines_nested_known_writes():
    reads = GuardedReads()
    active, choice = z3.Bools('nested_active nested_choice')
    old, a, b, first, last = z3.BitVecs('nested_old nested_a nested_b nested_first nested_last', 8)
    reads.define(first, z3.If(active, a, old))
    reads.define(last, z3.If(z3.And(active, choice), b, first))
    selected = reads.read(last, active)
    assert selected.eq(z3.If(choice, b, a))
    solver = z3.Solver()
    solver.add(first == z3.If(active, a, old), last == z3.If(z3.And(active, choice), b, first),
               active, selected != last)
    assert solver.check() == z3.unsat
    assert reads.read(last, None).eq(last)
    assert reads.read(last, z3.BoolVal(True)).eq(last)
    assert reads.read(last, z3.Not(active)).eq(old)


def test_partially_written_value_keeps_the_unknown_history_arm():
    reads = GuardedReads()
    active, choice = z3.Bools('partial_active partial_choice')
    old, value, saved = z3.BitVecs('partial_old partial_value partial_saved', 8)
    reads.define(saved, z3.If(z3.And(active, choice), value, old))
    assert reads.read(saved, active).eq(z3.If(choice, value, old))


@pytest.mark.parametrize('negative_first', [False, True])
@pytest.mark.parametrize('named_guards', [False, True])
def test_complementary_writes_forget_old_value_only_inside_the_parent_guard(negative_first, named_guards):
    reads = GuardedReads()
    active, choice = z3.Bools('complete_active complete_choice')
    old, a, b, first, last = z3.BitVecs('complete_old complete_a complete_b complete_first complete_last', 8)
    first_condition = z3.Not(choice) if negative_first else choice
    left, right = z3.And(active, first_condition), z3.And(active, z3.Not(first_condition))
    definitions = []
    if named_guards:
        named_left, named_right = z3.Bools('complete_left complete_right')
        reads.define(named_left, left)
        reads.define(named_right, right)
        definitions.extend((named_left == left, named_right == right))
        left, right = named_left, named_right
    reads.define(first, z3.If(left, a, old))
    reads.define(last, z3.If(right, b, first))
    definitions.extend((first == z3.If(left, a, old), last == z3.If(right, b, first)))
    selected = reads.read(last, active)
    assert 'complete_old' not in selected.sexpr()
    for guard in (active, z3.Not(active), z3.BoolVal(True)):
        solver = z3.Solver()
        solver.add(*definitions, guard, reads.read(last, guard) != last)
        assert solver.check() == z3.unsat
    assert reads.read(last, z3.Not(active)).eq(old)
    assert reads.read(last, None).eq(last)


@pytest.mark.parametrize('nested_then', [False, True])
@pytest.mark.parametrize('complete', [False, True])
def test_nested_guard_factoring_is_exact_and_keeps_partial_history(nested_then, complete):
    reads = GuardedReads()
    active, p, q = z3.Bools('factor_active factor_p factor_q')
    old, a, b, c = z3.BitVecs('factor_old factor_a factor_b factor_c', 8)
    parent = p if nested_then else z3.Not(p)
    guards = [z3.And(active, parent, q), z3.And(active, parent, z3.Not(q)),
              z3.And(active, z3.Not(parent))]
    updates = list(zip(guards, (a, b, c), strict=True))
    if not complete:
        updates.pop(1)
    if not nested_then:
        updates.insert(0, updates.pop())
    original, definitions = old, []
    for index, (guard, value) in enumerate(updates):
        current = z3.BitVec(f'factor_write_{index}', 8)
        reads.define(current, z3.If(guard, value, original))
        definitions.append(current == z3.If(guard, value, original))
        original = current
    result = reads.read(original, active)
    assert ('factor_old' not in result.sexpr()) is complete
    for guard in (active, z3.Not(active), z3.BoolVal(True)):
        solver = z3.Solver()
        solver.add(*definitions, guard, reads.read(original, guard) != original)
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('partial', [False, True])
def test_branch_overwrites_preserve_complete_observables(loop, failure, partial):
    positive = ir._sequence((
        ir.Assign('t', ir.Var('a')),
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)),
              ir.Fail('RTLFatal', 'failed') if failure else ir.Mark('zero'), ir.Skip()),
        ir.Assign('t', ir.Op('add', ir.Var('a'), ir.Lit(1)))))
    body = ir._sequence((
        ir.Assign('t', ir.Lit(-2)),
        ir.If(ir.Op('ne', ir.Var('n'), ir.Lit(0)),
              ir.If(ir.Op('ge', ir.Var('a'), ir.Lit(0)), positive,
                    ir.Skip() if partial else ir.Assign('t', ir.Var('a'))), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('t'), ir.Lit(1)), ir.Mark('one'), ir.Mark('other')),
        ir.Assign('q', ir.Var('t'))))
    compare(*make(ir.For('i', ir.Var('n'), body) if loop else body))


def test_deep_conditional_value_dag_is_iterative_and_guard_scoped():
    reads = GuardedReads()
    active = z3.Bool('deep_value_active')
    old, value, first = z3.BitVecs('deep_value_old deep_value_new deep_value_first', 8)
    reads.define(first, z3.If(active, value, old))
    last = first
    for index in range(1500):
        updated = z3.BitVec(f'deep_value_{index}', 8)
        reads.define(updated, z3.If(z3.Bool(f'deep_choice_{index}'), last, value))
        last = updated
    selected = reads.read(last, active)
    assert z3.is_true(z3.simplify(selected == value))
    assert reads.read(last, active).eq(selected)
    assert reads.read(last, None).eq(last)


def test_unrelated_path_facts_share_the_same_conditional_read():
    reads = GuardedReads()
    active, choice = z3.Bools('shared_active shared_choice')
    old, value, first, last = z3.BitVecs('shared_old shared_value shared_first shared_last', 8)
    reads.define(first, z3.If(active, value, old))
    reads.define(last, z3.If(choice, value + 1, first))
    expected = reads.read(last, active)
    choices, results = len(reads._choices), len(reads._reads)
    for i in range(40):
        guard = z3.And(active, z3.Bool(f'unrelated_{i}'))
        assert reads.read(last, guard).eq(expected)
    assert len(reads._choices) == choices
    assert len(reads._reads) == results
    # A relevant opposite fact must not reuse the previous answer.
    negative = reads.read(last, z3.Not(active))
    assert negative.eq(z3.If(choice, value + 1, old))
    assert not negative.eq(expected)


@pytest.mark.parametrize('size', [8, 32])
def test_changing_context_retains_one_projection_per_node(size):
    reads = GuardedReads()
    conditions = z3.Bools(' '.join(f'bounded_context_{size}_{i}' for i in range(size)))
    value = z3.BitVecVal(0, 8)
    equations = []
    for i, condition in enumerate(conditions):
        arm, next_value = z3.BitVecs(f'bounded_arm_{size}_{i} bounded_value_{size}_{i}', 8)
        alternative = z3.If(condition, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))
        choice = z3.If(z3.Bool(f'bounded_choice_{size}_{i}'), arm, value)
        reads.define(arm, alternative)
        reads.define(next_value, choice)
        equations.extend((arm == alternative, next_value == choice))
        value = next_value
    solver = z3.Solver()
    solver.add(*equations)
    # Revisit replaced contexts in a different order and polarity. Every
    # result remains exact under its own guard; no result leaks across guards.
    guards = [*conditions, *(z3.Not(c) for c in reversed(conditions)), *conditions]
    for guard in guards:
        selected = reads.read(value, guard)
        solver.push()
        solver.add(guard)
        assert solver.check() == z3.sat
        solver.add(selected != value)
        assert solver.check() == z3.unsat
        solver.pop()
    assert 0 < len(reads._reads) <= len(reads._choices)
    assert 0 < len(reads._conditions) <= len(reads._logical)


def test_compound_conditions_replace_context_without_losing_shared_children():
    reads = GuardedReads()
    a, b, c, condition = z3.Bools('context_a context_b context_c context_condition')
    expression = z3.Or(z3.And(a, b), z3.And(a, c))
    reads.define(condition, expression)
    value = z3.BitVec('context_stored', 8)
    original = z3.If(condition, z3.BitVecVal(7, 8), z3.BitVecVal(9, 8))
    reads.define(value, original)
    for guard in (a, z3.Not(a), b, z3.Not(b), c, a):
        selected = reads.read(value, guard)
        solver = z3.Solver()
        solver.add(condition == expression, value == original, guard)
        assert solver.check() == z3.sat
        solver.add(selected != value)
        assert solver.check() == z3.unsat
    assert len(reads._conditions) <= len(reads._logical)


def test_unknown_guards_do_not_rewalk_the_history_chain():
    reads = GuardedReads()
    value = z3.BitVec('history_value', 8)
    for i in range(40):
        name = z3.BitVec(f'history_{i}', 8)
        reads.define(name, z3.If(z3.Bool(f'choice_{i}'), value, z3.BitVecVal(i, 8)))
        value = name
    assert reads.read(value, z3.Bool('outside_history')).eq(value)
    choices, results = len(reads._choices), len(reads._reads)
    for i in range(40):
        assert reads.read(value, z3.Bool(f'outside_history_{i}')).eq(value)
    assert len(reads._choices) == choices
    assert len(reads._reads) == results <= len(reads._choices)


def test_known_inactive_writes_reuse_their_selected_history(monkeypatch):
    reads = GuardedReads()
    active = z3.Bool('inactive_history_active')
    old = value = z3.BitVec('inactive_history_old', 8)
    condition, visits = reads._condition, []

    def inspect(term, yes, no):
        visits.append(term)
        return condition(term, yes, no)

    monkeypatch.setattr(reads, '_condition', inspect)
    for i in range(128):
        saved = z3.BitVec(f'inactive_history_{i}', 8)
        reads.define(saved, z3.If(active, z3.BitVecVal(i, 8), value))
        value = saved
        # Different unrelated facts cannot change the selected old arm.
        guard = z3.And(z3.Not(active), z3.Bool(f'irrelevant_history_{i}'))
        assert reads.read(value, guard).eq(old)
    assert len(visits) <= 128
    # The same history under the opposite fact must return the latest write.
    assert reads.read(value, active).eq(z3.BitVecVal(127, 8))
    assert reads.read(value, z3.BoolVal(True)).eq(value)


def test_selected_history_memo_retains_unknown_stopping_conditions():
    reads = GuardedReads()
    a, b = z3.Bools('arm_memo_a arm_memo_b')
    old, first, last = z3.BitVecs('arm_memo_old arm_memo_first arm_memo_last', 8)
    reads.define(first, z3.If(a, z3.BitVecVal(7, 8), old))
    reads.define(last, z3.If(b, z3.BitVecVal(9, 8), first))
    assert reads.read(last, z3.Not(b)).eq(first)
    assert reads.read(last, z3.And(a, z3.Not(b))).eq(z3.BitVecVal(7, 8))
    assert reads.read(last, z3.And(z3.Not(a), z3.Not(b))).eq(old)
    assert reads.read(last, b).eq(z3.BitVecVal(9, 8))


def test_known_write_does_not_index_unselected_history():
    reads = GuardedReads()
    active = z3.Bool('fresh_write_active')
    old, fresh = z3.BitVecs('fresh_write_old fresh_write_value', 16)
    history = old
    definitions = []
    for i in range(512):
        saved = z3.BitVec(f'cold_history_{i}', 16)
        value = z3.If(z3.Bool(f'cold_choice_{i}'), history, z3.BitVecVal(i, 16))
        reads.define(saved, value)
        definitions.append(saved == value)
        history = saved
    latest = z3.BitVec('fresh_write_latest', 16)
    reads.define(latest, z3.If(active, fresh, history))
    assert reads.read(latest, active).eq(fresh)
    assert history.get_id() not in reads._choices
    # A different guard must retain the entire old history, not reuse this
    # selected value or treat the overwritten history as globally dead.
    previous = reads.read(latest, z3.Not(active))
    assert previous.eq(history)
    solver = z3.Solver()
    solver.add(*definitions, latest == z3.If(active, fresh, history),
               z3.Not(active), previous != latest)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('depth', [8, 128, 2048])
def test_unknown_outer_guard_does_not_expand_a_dead_inner_history(depth):
    reads = GuardedReads()
    active, outer = z3.Bools(f'inner_active_{depth} outer_choice_{depth}')
    first, second, history = z3.BitVecs(f'first_{depth} second_{depth} old_{depth}', 16)
    equations, old_nodes = [], []
    for i in range(depth):
        node = z3.BitVec(f'dead_inner_{depth}_{i}', 16)
        expression = z3.If(z3.Bool(f'dead_choice_{depth}_{i}'), history, z3.BitVecVal(i, 16))
        reads.define(node, expression)
        equations.append(node == expression)
        old_nodes.append(node)
        history = node
    inner, value = z3.BitVecs(f'inner_value_{depth} outer_value_{depth}', 16)
    reads.define(inner, z3.If(active, first, history))
    reads.define(value, z3.If(outer, inner, second))
    result = reads.read(value, active)
    assert result.eq(z3.If(outer, first, second))
    assert not any(node.get_id() in reads._choices for node in old_nodes)
    assert len(reads._choices) == 4  # Two live choices and their two leaf values.
    assert reads.read(value, z3.And(active, z3.Bool(f'unrelated_{depth}'))).eq(result)
    # The opposite condition really needs that history. It must be visited,
    # not silently removed by reuse of the first projection's cache entry.
    opposite = reads.read(value, z3.Not(active))
    assert opposite.eq(z3.If(outer, history, second))
    assert history.get_id() in reads._choices
    solver = z3.Solver()
    solver.add(*equations, inner == z3.If(active, first, history),
               value == z3.If(outer, inner, second))
    solver.add(z3.Or(z3.And(active, result != value),
                     z3.And(z3.Not(active), opposite != value)))
    assert solver.check() == z3.unsat


def test_constant_choices_still_reduce_without_any_matching_guard_fact():
    reads = GuardedReads()
    active, outside = z3.Bools('constant_active constant_outside')
    a, b, c, first, last = z3.BitVecs('constant_a constant_b constant_c constant_first constant_last', 8)
    reads.define(first, z3.If(z3.BoolVal(True), a, b))
    reads.define(last, z3.If(active, c, first))
    result = reads.read(last, outside)
    assert result.eq(z3.If(active, c, a))
    solver = z3.Solver()
    solver.add(first == z3.If(z3.BoolVal(True), a, b), last == z3.If(active, c, first),
               outside, result != last)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_nested_clamped_writes_preserve_complete_formula(loop, failure):
    body = ir.If(ir.Op('ne', ir.Var('n'), ir.Lit(0)), ir._sequence((
        ir.Assign('t', ir.Var('a')),
        ir.If(ir.Op('lt', ir.Var('t'), ir.Lit(0)), ir.Assign('t', ir.Lit(0)), ir.Skip()),
        ir.If(ir.Op('gt', ir.Var('t'), ir.Lit(2)), ir.Assign('t', ir.Lit(2)), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('t'), ir.Lit(1)),
              ir.Fail('RTLFatal', 'one') if failure else ir.Mark('one'), ir.Skip()),
        ir.Assign('q', ir.Var('t')))), ir.Mark('idle'))
    compare(*make(ir.For('i', ir.Var('n'), body) if loop else body))


@pytest.mark.parametrize('guard, expected', [
    (lambda a,b: z3.And(a,z3.Not(a)), True),
    (lambda a,b: z3.And(a,z3.Not(z3.Or(a,b))), True),
    (lambda a,b: z3.Or(a,z3.Not(a)), False),
    (lambda a,b: z3.And(a,z3.Not(b)), False),
    (lambda a,b: z3.And(a,b,z3.Not(z3.And(a,b))), True),
    (lambda a,b: z3.And(z3.Or(a,b),z3.Not(a),z3.Not(b)), True),
    (lambda a,b: z3.And(a,z3.Not(z3.And(a,b))), False),
    (lambda a,b: z3.And(z3.Or(a,b),z3.Not(a)), False),
])
def test_contradiction_certificate_uses_only_owned_boolean_definitions(guard, expected):
    reads = GuardedReads()
    a, b, first, final = z3.Bools('contradiction_a contradiction_b contradiction_first contradiction_final')
    expression = guard(a,b)
    reads.define(first, expression)
    other = z3.Bool('contradiction_other')
    reads.define(final, z3.And(first,other))
    assert reads.impossible(final) == expected
    if expected:
        solver = z3.Solver()
        solver.add(first == expression, final == z3.And(first,other), final)
        assert solver.check() == z3.unsat
    assert not reads.impossible(z3.Bool('unowned_guard'))


@pytest.mark.parametrize('nested', [False, True, 'independent'])
def test_planned_template_keeps_failure_prefix_without_encoding_dead_suffix(nested):
    from behave.sim.symbolic.step_template import StepTemplate

    failure = ir._sequence((ir.Assign('q',ir.Lit(7)), ir.Fail('stop','failed'),
                            ir.If(ir.Var('t'),ir.Assign('q',ir.Lit(99)),ir.Mark('dead'))))
    if nested:
        condition = ir.Op('eq',ir.Var('n' if nested == 'independent' else 'a'),ir.Lit(1))
        failure = ir.If(condition, failure, failure)
    proc, env = make(ir._sequence((
        ir.Assign('t',ir.Lit(1)),
        ir.For('i',ir.Lit(2),ir.Mark('loop')),
        ir.If(ir.Op('eq',ir.Var('a'),ir.Lit(1)),failure,ir.Assign('q',ir.Lit(3))),
        ir.Mark('suffix'))))
    compiled = StepTemplate(proc,env).encode()
    solver = z3.Solver()
    solver.add(compiled.constraint,compiled.pre['a']==1)
    assert solver.check() == z3.sat
    solver.add(z3.Or(compiled.ok, compiled.state_post['q']!=7,
                     z3.Not(compiled.hits['failed']), compiled.hits['dead'], compiled.hits['suffix']))
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(compiled.constraint,compiled.pre['a']==0)
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(compiled.ok), compiled.state_post['q']!=3,
                     compiled.hits['failed'],z3.Not(compiled.hits['suffix'])))
    assert solver.check() == z3.unsat
