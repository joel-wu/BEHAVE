"""Sparse guard facts must not allocate up to the highest unrelated atom ID."""

import sys

import pytest
import z3

from behave.sim.symbolic.guarded_reads import GuardedReads, _Support, _Facts

pytestmark = pytest.mark.solver


def _storage(values):
    """Count actual shared nodes and words, not a flattened diagnostic copy."""
    total = sum(sys.getsizeof(value) for value in values)
    pending = [value._root for value in values if isinstance(value, _Support)]
    seen = set()
    while pending:
        node = pending.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        total += sys.getsizeof(node) + sys.getsizeof(node.word)
        pending.extend((node.left, node.right))
    return total


def _independent_storage(count):
    reads = GuardedReads()
    for index in range(count):
        guard = z3.Bool(f"sparse_guard_{count}_{index}")
        term = z3.If(guard, z3.BitVecVal(7, 8), z3.BitVecVal(2, 8))
        reads._index_choices(term)
        assert reads.read(term, guard).as_long() == 7
    # Count the actual guard-fact and support objects, without double-counting
    # shared immutable values. This checks growth, not a machine memory quota.
    masks = {id(value): value for pair in reads._facts.values() for value in pair}
    masks.update((id(row[3]), row[3]) for row in reads._logical.values())
    masks.update((id(row[3]), row[3]) for row in reads._choices.values())
    return _storage(masks.values())


def test_independent_guard_storage_scales_with_members_not_highest_symbol_id():
    # Compare complete leaf-word spans. The first partial word has lower bit
    # offsets on average; that warm-up is not growing global-index storage.
    small, large = _independent_storage(4096), _independent_storage(32768)
    assert large < 10 * small, (small, large)


@pytest.mark.parametrize("negative", [False, True])
def test_late_atoms_keep_exact_negation_and_disjunction_semantics(negative):
    reads = GuardedReads()
    for index in range(1024):
        reads._known(z3.Bool(f"unrelated_atom_{index}"))
    a, b, alias = z3.Bools("late_a late_b late_alias")
    expression = z3.Or(a, b) if negative else z3.And(a, b)
    reads.define(alias, expression)
    guard = z3.Not(alias) if negative else alias
    value = z3.If(a, z3.BitVecVal(7, 8), z3.BitVecVal(2, 8))
    reduced = reads.read(value, guard)
    solver = z3.Solver()
    solver.add(alias == expression, guard, reduced != value)
    assert solver.check() == z3.unsat
    assert reduced.as_long() == (2 if negative else 7)
    assert reads.impossible(z3.And(guard, a if negative else z3.Not(a)))


def test_equivalent_conjunction_prefixes_do_not_duplicate_guard_facts():
    reads = GuardedReads()
    a, b = z3.Bools('prefix_a prefix_b')
    guard = a
    for index in range(256):
        name = z3.Bool(f'prefix_alias_{index}')
        reads.define(name, z3.And(guard, b))
        guard = name
        yes, no = reads._known(guard)
        assert len(set(yes)) == 2 and not no
    value = z3.If(b, z3.BitVecVal(7, 8), z3.BitVecVal(2, 8))
    assert reads.read(value, guard).as_long() == 7
    # An opposite whole-conjunction fact does not imply either child is false.
    unknown = reads.read(value, z3.Not(guard))
    assert not z3.is_bv_value(unknown)


def test_relative_support_union_and_projection_match_exact_sets():
    import random

    randomizer = random.Random(0)
    support, expected = _Support(), set()
    for _ in range(200):
        atom = randomizer.randrange(100_000, 101_000)
        singleton = _Support.atom(atom)
        forward, backward = support | singleton, singleton | support
        expected.add(atom)
        assert forward == backward
        support = forward
        facts = frozenset(randomizer.randrange(99_000, 102_000) for _ in range(50))
        known = _Facts.of(facts)
        assert set(support.project(known)) == expected & facts
        assert support.intersects(known) == bool(expected & facts)
    assert _Support().project(_Facts.of(frozenset(expected))) == _Support()
    assert not _Support().intersects(_Facts.of(frozenset(expected)))
    assert (support | _Support()) is support


def test_late_support_and_dense_history_have_compact_storage():
    # A singleton has a bounded leaf word even at a high global atom number.
    assert _storage([_Support.atom(1_000_000_000)]) < 1024
    support = _Support()
    for index in range(10_000, 14_096):
        support |= _Support.atom(index)
    assert set(support) == set(range(10_000, 14_096))
    assert _storage([support]) < sys.getsizeof(frozenset(range(10_000, 14_096)))


def test_single_condition_does_not_walk_an_unrelated_execution_prefix():
    class Facts(frozenset):
        def __iter__(self):
            pytest.fail('a singleton condition must not scan the complete fact history')

    values = frozenset(range(4096))
    facts = _Facts(Facts(values), _Facts.of(values).mask)
    assert _Support.atom(123).project(facts) == _Support.atom(123)
    assert _Support.atom(123).intersects(facts)
    assert _Support.atom(10_000).project(facts) == _Support()
    assert not _Support.atom(10_000).intersects(facts)


def test_dense_projection_is_canonical_without_expanding_its_members(monkeypatch):
    def no_iteration(self):
        pytest.fail('a dependency projection must not enumerate its atoms')

    monkeypatch.setattr(_Support, '__iter__', no_iteration)
    support = _Support(100, (1 << 4096) - 1)
    facts = _Facts.of(frozenset(range(90, 4100)))
    projected = support.project(facts)
    assert projected == _Support(100, (1 << 4000) - 1)
    assert _Support(0, 1 << 123).project(_Facts.of(frozenset((123,)))) == _Support.atom(123)
    assert _Support(123, 1).project(_Facts.of(frozenset((0, 123)))) == _Support.atom(123)


def test_union_reuses_an_unchanged_dependency_history():
    history = _Support(10_000, (1 << 4096) - 1)
    subset = _Support(12_000, (1 << 100) - 1)
    assert (history | history) is history
    assert (history | subset) is history
    assert (subset | history) is history
    extended = history | _Support.atom(14_096)
    assert extended == _Support(10_000, (1 << 4097) - 1)


def test_equal_dependency_sets_share_storage_only_while_in_use():
    import weakref

    reads = GuardedReads()
    original = reads._support(_Support(10_000, (1 << 8192) - 1))
    duplicate = reads._support(_Support(10_000, (1 << 8192) - 1))
    shifted = reads._support(_Support(10_001, (1 << 8192) - 1))
    assert duplicate is original
    assert shifted != original
    owner = weakref.ref(original)
    del original, duplicate
    # The reuse index must not itself retain old query histories. No gc cycle
    # or global cache is needed to reclaim an unused immutable set.
    assert owner() is None
    assert len(reads._supports) == 1


def test_repeated_guard_aliases_share_the_same_known_facts():
    reads = GuardedReads()
    a, b = z3.Bools('same_fact_a same_fact_b')
    guard = z3.And(a, b)
    yes, no = reads._known(guard)
    for index in range(128):
        alias = z3.Bool(f'same_fact_alias_{index}')
        reads.define(alias, z3.And(guard, a))
        got_yes, got_no = reads._known(alias)
        assert got_yes is yes
        assert got_no is no
        guard = alias


def test_growing_guard_prefixes_share_storage_and_keep_every_fact():
    def build(count):
        reads = GuardedReads()
        guard = z3.BoolVal(True)
        for index in range(count):
            atom = z3.Bool(f'growing_fact_{count}_{index}')
            name = z3.Bool(f'growing_prefix_{count}_{index}')
            reads.define(name, z3.And(guard, atom))
            guard = name
            yes, no = reads._known(guard)
            assert reads._atoms[atom.get_id()][1] in yes and not no
        assert len(set(yes)) == count + 1  # Includes the initial True guard.
        assert reads.impossible(z3.And(guard, z3.Not(z3.Bool(f'growing_fact_{count}_0'))))
        values = {id(value): value for pair in reads._facts.values() for value in pair}
        return _storage(values.values())

    # Compare complete leaf-word spans, not the first partially filled word.
    small, large = build(4096), build(8192)
    assert large < 3 * small, (small, large)


@pytest.mark.parametrize('private_context', [False, True])
def test_unconditional_reads_do_not_expand_unrelated_guard_histories(private_context, monkeypatch):
    context = z3.Context() if private_context else None
    reads = GuardedReads()
    a = z3.BitVec('ordinary_input', 8, ctx=context)
    flag = z3.Bool('ordinary_flag', ctx=context)
    named = z3.BitVec('ordinary_arithmetic', 8, ctx=context)
    arithmetic = z3.If(flag, a, a + 1) + 7
    reads.define(named, arithmetic)
    values = [a, [z3.BitVecVal(0, 8, ctx=context), named, arithmetic], flag]
    with monkeypatch.context() as patch:
        patch.setattr(reads, '_known', lambda _: pytest.fail('ordinary read expanded guard facts'))
        for index in range(128):
            guard = z3.Bool(f'unrelated_read_guard_{index}', ctx=context)
            result = reads.read(values, guard)
            assert result[0] is a and result[1][1] is named and result[1][2] is arithmetic
            assert result[2] is flag
    assert reads._active_facts is None
    assert not reads._facts and not reads._reads and not reads._logical
    # A subsequent conditional read must still use its actual guard. Named
    # arithmetic stays named; this is not a recursive simplification rule.
    choice = z3.BitVec('conditional_value', 8, ctx=context)
    expression = z3.If(flag, named, a)
    reads.define(choice, expression)
    assert reads.read(choice, flag).eq(named)
    assert reads.read(choice, z3.Not(flag)).eq(a)
    unknown = reads.read(choice, z3.Bool('unrelated_choice_guard', ctx=context))
    solver = z3.Solver(ctx=context)
    solver.add(named == arithmetic, choice == expression, unknown != choice)
    assert solver.check() == z3.unsat
