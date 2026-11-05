"""Live Behavior alignment and task-declared output matching policies."""

import pytest

from behave.sim import api


def _endpoint(policy, names=("y",)):
    return api.Endpoint(
        "out", api.Direction.OUT,
        [api.Field(name, api.FiniteType(8), "out_" + name) for name in names],
        api.ready_valid("out", "out_valid", "out_ready"),
        clock="clk", kind=api.Kind.TRANSACTION, matching=policy)


@pytest.mark.parametrize("policy", [api.Matching.ORDERED, api.Matching.CONTENT])
@pytest.mark.parametrize(("expected", "observed", "ordered_faults", "content_faults"), [
    ([(1,), (2,)], [(1,), (2,)], (), ()),
    ([(1,), (2,)], [(2,), (1,)], ("value", "value"), ()),
    ([(5,), (5,)], [(5,), (5,)], (), ()),
    ([(5,), (5,)], [(5,)], ("missing",), ("missing",)),
    ([(1,)], [(1,), (1,)], ("extra",), ("extra",)),
    ([(1,), (2,)], [(9,)], ("value", "missing"), ("missing", "missing", "extra")),
    ([], [], (), ()),
    ([(1,)], [], ("missing",), ("missing",)),
    ([], [(1,)], ("extra",), ("extra",)),
])
def test_output_matching_uses_declared_policy(
        policy, expected, observed, ordered_faults, content_faults):
    result = api.compare(_endpoint(policy), expected, observed)
    faults = ordered_faults if policy is api.Matching.ORDERED else content_faults
    assert tuple(fault[0] for fault in result.faults) == faults
    assert result.ok is (not faults)
    assert bool(result.assumptions) is (policy is api.Matching.CONTENT)


def test_content_matching_uses_all_fields_and_retains_multiplicity():
    endpoint = _endpoint(api.Matching.CONTENT, names=("a", "b"))
    expected = [(1, 2), (1, 3), (1, 3)]
    assert api.compare(endpoint, expected, [(1, 3), (1, 2), (1, 3)]).ok
    faults = api.compare(endpoint, expected, [(1, 2), (1, 2), (1, 3)]).faults
    assert faults == (("missing", 0, (1, 3), None), ("extra", 0, None, (1, 2)))


def test_task_output_normalization_retains_signed_bit_patterns():
    endpoint = api.Endpoint(
        "out", api.Direction.OUT,
        [api.Field("s", api.FiniteType(8, signed=True), "out_s"),
         api.Field("u", api.FiniteType(8), "out_u")],
        api.ready_valid("out", "out_valid", "out_ready"), clock="clk")
    assert api.normalise(endpoint, (-1, 5)) == api.normalise(endpoint, (255, 5))
