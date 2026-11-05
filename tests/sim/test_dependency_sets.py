"""Dependency compression is exact, including branching/divergent histories."""

import random
from types import SimpleNamespace

from behave.sim.symbolic.dependency_sets import Support


def test_persistent_sets_match_python_sets_across_sparse_dense_and_divergent_histories():
    randomizer = random.Random(7)
    histories = [(Support(), set())]
    for step in range(1000):
        prior, values = randomizer.choice(histories)
        atom = randomizer.choice((randomizer.randrange(4096), 1_000_000 + step))
        updated = prior | Support.atom(atom)
        expected = values | {atom}
        other, other_values = randomizer.choice(histories)
        united = updated | other
        facts = SimpleNamespace(mask=other, projections={})
        assert set(updated) == expected
        assert set(united) == expected | other_values
        assert set(updated & other) == expected & other_values
        assert bool(updated & other) == bool(expected & other_values)
        assert set(updated.project(facts)) == expected & other_values
        assert updated.intersects(facts) == bool(expected & other_values)
        assert (atom in prior) == (atom in values)
        assert united == other | updated
        assert hash(united) == hash(other | updated)
        histories.append((updated, expected))


def test_retained_prefix_storage_does_not_recopy_every_older_word():
    history, roots = Support(), []
    for position in range(4096):
        history |= Support.atom(position * 3)
        roots.append(history._root)
    nodes = {}
    pending = list(roots)
    while pending:
        node = pending.pop()
        if node is None or id(node) in nodes:
            continue
        nodes[id(node)] = node
        pending.extend((node.left, node.right))
    # A path copy changes logarithmically many nodes, never the whole prefix.
    assert len(nodes) < 8 * len(roots)
    assert set(history) == set(range(0, 3 * 4096, 3))


def test_projection_reuse_is_owned_by_exact_guard_not_the_dependency_set():
    from behave.sim.symbolic.guarded_reads import _Facts

    support = Support.atom(7) | Support.atom(100_000)
    first = _Facts.of(frozenset((7,)))
    second = _Facts.of(frozenset((100_000,)))
    result = support.project(first)
    assert support.project(first) is result
    assert set(result) == {7}
    assert set(support.project(second)) == {100_000}
    assert set(support.project(_Facts.of(frozenset()))) == set()
    assert support == Support.atom(7) | Support.atom(100_000)


def test_sparse_fact_construction_is_canonical_and_independent_of_atom_span():
    from behave.sim.symbolic.guarded_reads import _Facts

    atoms = (0, 17, 4095, 4096, 1_000_000_000_000)
    expected = Support()
    for atom in atoms:
        expected |= Support.atom(atom)
    built = _Facts.of(frozenset(atoms)).mask
    assert set(built) == set(atoms)
    assert built == expected == Support.from_atoms(reversed(atoms))
    assert hash(built) == hash(expected)
    assert Support.from_atoms((*atoms, *atoms)) == built
    assert Support.from_atoms(()) == Support()
    pending, leaves = [built._root], []
    while pending:
        node = pending.pop()
        if node.level:
            pending.extend((node.left, node.right))
        else:
            leaves.append(node)
    assert len(leaves) == 3
    assert max(node.word.bit_length() for node in leaves) <= 4096


def test_fact_words_match_the_previous_relative_bitmap_for_random_sets():
    randomizer = random.Random(29)
    for _ in range(100):
        atoms = set(randomizer.choices(range(30_000), k=randomizer.randrange(200)))
        first = min(atoms, default=0)
        bits = sum(1 << (atom - first) for atom in atoms)
        expected = Support(first, bits)
        result = Support.from_atoms(atoms)
        assert result == expected
        assert hash(result) == hash(expected)
        assert set(result) == atoms
