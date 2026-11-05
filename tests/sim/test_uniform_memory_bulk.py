"""Bulk external-memory reconstruction preserves sequential total stores."""
import pickle
import random

import pytest

from behave.sim.symbolic.uniform_array import UniformArray


def sequential(length, entries):
    result = UniformArray.memory(length, 'ram', 32)
    for index, value in entries:
        result = result.store(index, value)
    return result


def test_bulk_matches_sequential_with_duplicates_and_out_of_range():
    rng = random.Random(921)
    for length in (0, 1, 3, 19):
        entries = [(rng.randrange(-2, length + 3), rng.randrange(1 << 32))
                   for _ in range(40)]
        bulk = UniformArray.memory(length, 'ram', 32, iter(entries))
        old = sequential(length, entries)
        assert bulk.base_image == old.base_image
        assert bulk.sparse_items() == old.sparse_items()
        assert bulk.extensionally_equal(old)
        assert [bulk[i] for i in range(length)] == [old[i] for i in range(length)]


def test_bulk_preserves_last_write_order_and_immutable_values():
    nested = [1, [2, 3]]
    bulk = UniformArray.memory(4, 'ram', 32, [(1, 2), (2, 3), (1, nested)])
    assert bulk.sparse_items() == ((2, 3), (1, [1, [2, 3]]))
    nested[1][0] = 99
    selected = bulk[1]
    selected[1][0] = 88
    assert bulk[1] == [1, [2, 3]]
    updated = bulk.store(1, 7)
    assert bulk[1] == [1, [2, 3]] and updated[1] == 7
    assert pickle.loads(pickle.dumps(bulk)).sparse_items() == bulk.sparse_items()
    with pytest.raises(AttributeError):
        bulk.length = 99


@pytest.mark.parametrize('index', [True, False, 0.0, '0', None])
def test_bulk_rejects_non_integer_index_even_when_empty(index):
    for length in (0, 2):
        with pytest.raises(TypeError, match='index is an integer'):
            UniformArray.memory(length, 'ram', 32, [(index, 0)])


def test_bulk_ignores_invalid_value_only_outside_array():
    ignored = object()
    assert not UniformArray.memory(0, 'ram', 32, [(0, ignored)]).sparse_items()
    assert not UniformArray.memory(2, 'ram', 32, [(-1, ignored), (2, ignored)]).sparse_items()
    with pytest.raises(TypeError, match='contains only'):
        UniformArray.memory(2, 'ram', 32, [(0, ignored)])


@pytest.mark.parametrize('length,name,width', [(-1, 'ram', 32), (True, 'ram', 32),
                                                (2, '', 32), (2, 'ram', 0), (2, 'ram', True)])
def test_bulk_still_validates_base(length, name, width):
    with pytest.raises(TypeError):
        UniformArray.memory(length, name, width, {})


def test_dense_image_does_not_build_by_persistent_stores(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('bulk initialization must not repeat persistent stores')
    monkeypatch.setattr(UniformArray, 'store', forbidden)
    count = 65536
    memory = UniformArray.memory(count, 'weights', 16, {i: i for i in range(count)})
    assert len(memory.sparse_items()) == count
    assert memory[0] == 0 and memory[count - 1] == count - 1


def test_uninitialized_cells_keep_name_dependent_junk():
    blank = UniformArray.memory(8, 'ram', 8)
    filled = UniformArray.memory(8, 'ram', 8, {1: 255, 7: 0})
    assert [filled[i] for i in range(8) if i not in (1, 7)] == [
        blank[i] for i in range(8) if i not in (1, 7)]
