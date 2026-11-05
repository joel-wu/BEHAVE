"""Both replay backends resolve HDL identifiers, never Python handle attributes."""

from behave.sim.rtl.source_handles import exact_handle, path_handle, read_handle, state_path, width_of

import pytest


def test_hdl_name_does_not_read_python_attribute():
    signal = object()

    class Scope:
        _path = "python path metadata"

        def __getitem__(self, name):
            return {"_path": signal}[name]

    assert exact_handle(Scope(), "_path") is signal
    assert exact_handle(Scope(), "missing") is None


def test_unknown_bits_remain_unknown():
    class Bits:
        def __int__(self):
            raise ValueError("unresolved")

        def __str__(self):
            return "10xz"

    class Signal:
        value = Bits()

        def __len__(self):
            return 4

    assert read_handle(Signal()) == "10xz"
    assert width_of(Signal()) == 4
    assert read_handle(None) is None
    assert width_of(None) is None


@pytest.mark.parametrize("dimensions", [((0, 1), (0, 0)), ((2, 3), (6, 4)),
                                       ((-1, -3), (8, 9), (5, 3))])
def test_flattened_words_follow_declared_multidimensional_indices(dimensions):
    from itertools import product

    shape = 18
    for left, right in reversed(dimensions):
        shape = {"left": left, "right": right, "element": shape}
    expected = [("store", *indices) for indices in product(*(
        range(left, right + (1 if left <= right else -1), 1 if left <= right else -1)
        for left, right in dimensions))]
    paths = [state_path(f"store[{ordinal}]", {"store": shape}) for ordinal in range(len(expected))]
    assert paths == expected
    assert state_path(f"store[{len(paths)}]", {"store": shape}) is None
    assert state_path("store[-1]", {"store": shape}) is None
    marker = object()
    root = {"store": {dimensions[0][0]: {}}}
    leaf = root["store"]
    for index in expected[0][1:-1]:
        leaf = leaf.setdefault(index, {})
    leaf[expected[0][-1]] = marker
    assert path_handle(root, paths[0]) is marker
    assert path_handle(root, None) is None


def test_one_dimensional_indices_and_escaped_names_are_not_reinterpreted():
    fields = {"mem": {"left": 6, "right": 4, "element": 8}, "mem[0]": 18}
    assert state_path("mem[5]", fields) == ("mem", 5)
    assert state_path("mem[0]", fields) == ("mem[0]",)
    assert state_path("unbound[0]", fields) == ("unbound[0]",)
