"""Reuse only fully validated immutable branches, never mutable caller storage."""
import json
import io
from collections import UserList

import pytest

from behave.sim.core.record_value import (
    FrozenValueMap,
    _project,
    canonical_json,
    write_canonical_json,
    freeze_record_value,
)


def _streamed(value):
    stream = io.StringIO()
    write_canonical_json(value, stream)
    return stream.getvalue()


@pytest.mark.parametrize("json_view", [False, True])
def test_immutable_sequence_projection_keeps_the_original_tree(json_view):
    value = (("accepted", (("in", (1, False, None)),)),
             ("outputs", (("y", 2**256),)), ("meta", ("Ω", -0.0, 1.25)))
    assert _project(value, "record", set(), json_view=json_view) is value
    assert canonical_json(value) == json.dumps(value, sort_keys=True, ensure_ascii=False,
                                               allow_nan=False, separators=(",", ":"))


@pytest.mark.parametrize("json_view", [False, True])
def test_only_the_mutable_branch_and_its_ancestors_are_copied(json_view):
    shared = (("a", (1, 2, 3)), ("b", (True, None)))
    mutable = [7]
    value = (shared, (shared, mutable), shared)
    projected = _project(value, "record", set(), json_view=json_view)
    assert projected is not value and projected[1] is not value[1]
    assert projected[0] is projected[1][0] is projected[2] is shared
    mutable.append(8)
    assert canonical_json(projected) == canonical_json((shared, (shared, [7]), shared))


@pytest.mark.parametrize("kind", [lambda x: [x], lambda x: {"x": x},
                                  lambda x: FrozenValueMap((("x", x),))])
def test_tuple_does_not_certify_mutable_or_unsealed_children(kind):
    mutable = [1]
    value = (kind(mutable),)
    frozen = freeze_record_value(value)
    encoded = canonical_json(value)
    streamed = _streamed(value)
    mutable.append(2)
    assert canonical_json(frozen) == encoded == streamed


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {False: 1},
                                FrozenValueMap((("a", 1), ("a", 2))), UserList([1])])
def test_immutable_ancestors_do_not_bypass_carrier_validation(bad):
    value = (("first", 1), ((bad,),))
    for project in (freeze_record_value, canonical_json, _streamed):
        with pytest.raises((TypeError, ValueError)):
            project(value)


def test_tuple_list_cycle_and_sequence_subclasses_still_fail():
    child = []
    parent = (child,)
    child.append(parent)

    class Tuple(tuple):
        pass

    for project in (freeze_record_value, canonical_json, _streamed):
        with pytest.raises(ValueError, match="cycle"):
            project(parent)
        with pytest.raises(TypeError):
            project((Tuple((1, 2)),))


def test_json_encoding_errors_are_still_eager(monkeypatch):
    from behave.sim.core import record_value

    def refuse(**kwargs):
        raise ValueError("test-only encoder failure")

    monkeypatch.setattr(record_value, "_json_encoder", refuse)
    with pytest.raises(ValueError, match="encoder failure"):
        _streamed((("x", (1, 2)),))
