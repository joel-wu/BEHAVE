"""Structural width and scalar codecs retain the leaf-wise definitions."""

import pytest

from behave.sim import api
from behave.sim.core import physical_value as codec


def _leafwise_unpack(kind, pattern):
    leaves = codec.packed_leaves(kind)
    width = sum(leaf.width for leaf in leaves)
    if width == 0:
        raise codec.PhysicalValueError("physical-value-carrier-empty", "empty")
    if type(pattern) is not int or not 0 <= pattern < 1 << width:
        raise codec.PhysicalValueError("physical-value-pattern-outside-width", "outside")
    values = {leaf.path: codec._decode_leaf(leaf.value_type,
              (pattern >> leaf.offset) & ((1 << leaf.width) - 1)) for leaf in leaves}
    return codec._reconstruct(kind, values)


KINDS = (
    api.ValueType.boolean(), api.ValueType.integer(api.FiniteType(3)),
    api.ValueType.integer(api.FiniteType(3, signed=True)),
    api.ValueType.enumeration(("two", "three", "one")),
    api.ValueType.array(0, api.ValueType.boolean()),
    api.ValueType.array(3, api.ValueType.boolean()),
    api.ValueType.record({"a": api.ValueType.array(2, api.ValueType.boolean()),
                         "b": api.ValueType.integer(api.FiniteType(2, signed=True))}),
)


@pytest.mark.parametrize("kind", KINDS)
def test_codec_agrees_with_leafwise_definition_on_the_entire_small_carrier(kind):
    leaves = codec.packed_leaves(kind)
    width = sum(leaf.width for leaf in leaves)
    assert codec.packed_width(kind) == width
    for pattern in (True, False, None, "1", -1, *range(1 << width), 1 << width):
        try:
            expected = _leafwise_unpack(kind, pattern)
        except codec.PhysicalValueError as exc:
            with pytest.raises(codec.PhysicalValueError) as caught:
                codec.unpack_value(kind, pattern)
            assert caught.value.code == exc.code
        else:
            got = codec.unpack_value(kind, pattern)
            assert got == expected and type(got) is type(expected)
            assert codec.pack_value(kind, got) == pattern


@pytest.mark.parametrize("kind,bad", (
    (api.ValueType.boolean(), 1), (api.ValueType.boolean(), "True"),
    (api.FiniteType(3), True), (api.FiniteType(3), -1),
    (api.FiniteType(3, signed=True), 4),
    (api.ValueType.enumeration(("a", "b", "c")), "missing"),
))
def test_scalar_invalid_values_keep_the_leaf_encoder_error(kind, bad):
    leaf = codec.packed_leaves(kind)[0]
    with pytest.raises(codec.PhysicalValueError) as before:
        codec._encode_leaf(leaf.value_type, bad, "value")
    with pytest.raises(codec.PhysicalValueError) as after:
        codec.pack_value(kind, bad)
    assert (after.value.code, str(after.value)) == (before.value.code, str(before.value))


def test_scalar_codec_and_large_array_width_do_not_build_leaf_layouts(monkeypatch):
    kind = api.ValueType.array(1 << 24, api.ValueType.record({
        "flag": api.ValueType.boolean(), "value": api.FiniteType(17)}))

    def unexpected(*args):
        raise AssertionError("a scalar or width-only query materialized leaf layouts")

    monkeypatch.setattr(codec, "packed_leaves", unexpected)
    assert codec.packed_width(kind) == (1 << 24) * 18
    assert codec.pack_value(api.FiniteType(8, signed=True), -1) == 255
    assert codec.unpack_value(api.FiniteType(8, signed=True), 255) == -1
    assert codec.unpack_value(api.ValueType.boolean(), 1) is True


def test_zero_length_array_does_not_descend_into_its_element_type():
    kind = api.ValueType.boolean()
    for _ in range(2000):
        kind = api.ValueType.array(1, kind)
    kind = api.ValueType.array(0, kind)
    assert codec.packed_width(kind) == sum(leaf.width for leaf in codec.packed_leaves(kind)) == 0
    with pytest.raises(codec.PhysicalValueError) as caught:
        codec.pack_value(kind, ())
    assert caught.value.code == "physical-value-carrier-empty"


@pytest.mark.parametrize("kind", (*KINDS, api.FiniteType(3, signed=True)))
def test_signal_decode_preserves_normalization_and_every_invalid_pattern(kind):
    """The old three-call physical observation rule is the differential oracle."""
    class IntSubclass(int):
        pass

    width = codec.packed_width(kind)
    for pattern in (None, True, False, "0", 1.0, IntSubclass(0),
                    *range(-(1 << width) - 1, (1 << width) + 2)):
        normalized = pattern
        if (type(pattern) is int and codec.packed_signed(kind)
                and -(1 << (width - 1)) <= pattern < 0):
            normalized &= (1 << width) - 1
        try:
            expected = codec.unpack_value(kind, normalized)
        except codec.PhysicalValueError as exc:
            with pytest.raises(codec.PhysicalValueError) as caught:
                codec.unpack_signal_value(kind, pattern)
            assert (caught.value.code, str(caught.value)) == (exc.code, str(exc))
        else:
            got = codec.unpack_signal_value(kind, pattern)
            assert type(got) is type(expected) and got == expected
