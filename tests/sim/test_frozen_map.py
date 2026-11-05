"""Record mappings retain their identity without quadratic mapping operations."""

import copy
import pickle

import pytest

from behave.sim.core.frozen import FrozenMap


def test_mapping_order_values_and_round_trips_are_stable():
    source = {"z": [1, {"nested": [2]}], "a": 3}
    mapping = FrozenMap(source)
    source["z"][1]["nested"].append(99)
    expected = (("a", 3), ("z", (1, (("nested", (2,)),))))

    for restored in (mapping, FrozenMap(mapping), copy.deepcopy(mapping),
                     pickle.loads(pickle.dumps(mapping))):
        assert restored.pairs() == expected
        assert tuple(restored.items()) == expected
        assert tuple(restored.keys()) == ("a", "z")
        assert tuple(restored.values()) == tuple(value for _, value in expected)
        assert dict(restored) == dict(expected)
        with pytest.raises(KeyError):
            restored["missing"]


def test_mapping_has_no_mutable_index_or_reinitialization_path():
    mapping = FrozenMap({"x": [1, 2]})
    with pytest.raises(TypeError):
        mapping._index["x"] = 3
    with pytest.raises(AttributeError):
        mapping.__init__({"x": 3})
    with pytest.raises(AttributeError):
        del mapping._pairs
    assert mapping["x"] == (1, 2)


def test_mapping_conversion_and_lookup_do_not_scan_every_key():
    class Key(str):
        comparisons = 0
        __hash__ = str.__hash__

        def __eq__(self, other):
            type(self).comparisons += 1
            return str.__eq__(self, other)

    count = 1024
    mapping = FrozenMap((Key(str(index)), index) for index in range(count))
    Key.comparisons = 0
    assert len(dict(mapping)) == count
    assert sum(mapping[Key(str(index))] for index in range(count)) == \
        count * (count - 1) // 2
    assert Key.comparisons <= 2 * count
