"""Identifier matching pairs values; the protocol monitor owns lifecycle validity."""

from behave.sim import api


def _endpoint():
    u8 = api.FiniteType(8)
    return api.Endpoint(
        "out", api.Direction.OUT,
        [api.Field("id", u8, "out_id"), api.Field("value", u8, "out_value")],
        api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
        matching=api.Matching.IDENTIFIER, identifier="id")


def test_serial_reuse_and_deep_reordering_compose():
    expected = [(0, 10), (0, 20), (1, 30), (2, 40), (3, 50), (4, 60)]
    observed = [(0, 10), (4, 60), (0, 20), (1, 30), (2, 40), (3, 50)]

    result = api.compare(_endpoint(), expected, observed)

    assert result.ok, result.faults
    assert result.reordered > 0


def test_each_identifier_uses_an_occurrence_queue():
    endpoint = _endpoint()

    wrong = api.compare(endpoint, [(0, 10), (0, 20)], [(0, 10), (0, 99)])
    duplicate = api.compare(endpoint, [(0, 10)], [(0, 10), (0, 10)])
    unknown = api.compare(endpoint, [(0, 10)], [(7, 10)])
    missing = api.compare(endpoint, [(0, 10), (0, 20)], [(0, 10)])

    assert [fault[0] for fault in wrong.faults] == ["value"]
    assert [fault[0] for fault in duplicate.faults] == ["extra"]
    assert [fault[0] for fault in unknown.faults] == ["extra", "missing"]
    assert [fault[0] for fault in missing.faults] == ["missing"]
