"""Reset epochs preserve wrong responses and cancel only declared pending work."""

import itertools
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.compare import _match_epochs
from behave.sim.core.reference_progress import ReferenceProgress
from tests.sim.test_protocol_adapters import (
    _contract,
    _evaluate,
    _ReadyValidDoubling,
    _realization,
    _reference,
)


@pytest.mark.parametrize("clears", [False, True])
def test_reference_reset_matching_is_shared_with_ready_valid(clears):
    policy = api.ResetPolicy.CLEARS if clears else api.ResetPolicy.KEEPS
    contract = _contract("ready-valid", reset_policy=policy)
    reference = _reference()
    task = api.admit(contract, _realization("ready-valid", "rv-device"), reference=reference)
    stimulus = api.Stimulus(
        {"in": [(3,), (5,)]},
        [api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
         api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
         api.EventSlot(controls={"rst": 0, "out_ready": 1})])
    result = _evaluate(task, stimulus, _ReadyValidDoubling(), reference)
    assert api.transfers(result.records[0], "out") == [(10,)]
    if clears:
        assert result.complete and result.passed, result.as_dict()
        assert result.results[0].comparison.cancelled == (("out", 2, (6,)),)
    else:
        # The same device violates KEEPS: nothing authorizes canceling its first reply.
        assert result.complete and not result.passed
        assert result.mismatch_count > 0
        assert not result.no_result_failures


@pytest.mark.parametrize("behavior", [False, True])
def test_progress_counts_batches_and_respects_reset_domain(behavior):
    contract = _contract("ready-valid")
    guard = ReferenceProgress(contract, behavior=behavior)
    expected = {"out": [(6,), (10,)]}
    # Other domains' resets cannot cancel this stream.
    guard.before_reset(("other",), expected, 1)
    if behavior:
        guard.observe(SimpleNamespace(outputs=(("out", ((6,), (10,))),)))
    else:
        guard.observe(SimpleNamespace(accepted=(("out", (6,)),)))
        guard.before_reset(("clk",), expected, 2)
        assert guard.cuts["out"] == [(2, 2, 1, api.ResetPolicy.CLEARS)]
        guard.observe(SimpleNamespace(accepted=(("out", (10,)),)))
    guard.before_reset(("clk",), expected, 3)
    assert expected == {"out": [(6,), (10,)]}


def test_a_quiescent_reset_cannot_erase_wrong_values():
    class Wrong(_ReadyValidDoubling):
        def settle(self, presented, controls):
            signals = super().settle(presented, controls)
            if signals["out_valid"]:
                signals["out_y"] ^= 1
            return signals

    contract = _contract("ready-valid")
    reference = _reference()
    task = api.admit(contract, _realization("ready-valid", "rv-device"), reference=reference)
    stimulus = api.Stimulus(
        {"in": [(3,)]},
        [api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
         api.EventSlot(controls={"rst": 0, "out_ready": 1}),
         api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 1})])
    result = _evaluate(task, stimulus, Wrong(), reference)
    assert result.complete and not result.passed
    assert result.mismatch_count == 1
    assert not result.no_result_failures


def _sink(matching):
    return api.Endpoint(
        "out", "out", [api.Field("id", api.FiniteType(8), "out_id"),
                       api.Field("value", api.FiniteType(8), "out_value")],
        api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
        kind=api.Kind.TRANSACTION, matching=matching,
        identifier="id" if matching is api.Matching.IDENTIFIER else None)


@pytest.mark.parametrize("matching", list(api.Matching))
def test_reset_cancellation_keeps_duplicates_wrong_values_and_terminal_obligations(matching):
    endpoint = _sink(matching)
    want = [(1, 10), (1, 10), (1, 20)]
    cuts = [(4, 2, 1, api.ResetPolicy.CLEARS)]
    honest = _match_epochs(endpoint, want, [(1, 10), (1, 20)], cuts)
    assert honest.ok and honest.complete
    assert honest.cancelled == (("out", 4, (1, 10)),)
    # A stale pre-reset response cannot satisfy a new use of the identifier.
    stale = _match_epochs(endpoint, want, [(1, 10), (1, 10)], cuts)
    assert not stale.ok
    # Cancellation never erases a wrong/unknown response already observed.
    wrong = _match_epochs(endpoint, want, [(9, 99), (1, 20)], cuts)
    assert not wrong.ok
    assert any(fault[0] in ("value", "extra") for fault in wrong.faults)
    missing = _match_epochs(endpoint, want, [(1, 10)], cuts)
    assert not missing.ok and not missing.complete
    assert any(fault[0] == "missing" for fault in missing.faults)


@pytest.mark.parametrize("matching", list(api.Matching))
def test_undeclared_reset_policy_refuses_only_an_unmatched_prefix(matching):
    endpoint = _sink(matching)
    with pytest.raises(api.Refused, match="reference-reset-policy-undeclared"):
        _match_epochs(endpoint, [(1, 10)], [], [(2, 1, 0, None)])
    complete = _match_epochs(endpoint, [(1, 10)], [(1, 10)], [(2, 1, 1, None)])
    assert complete.ok and complete.complete and not complete.cancelled


def test_identifier_reorder_and_reuse_do_not_cross_reset_epochs():
    endpoint = _sink(api.Matching.IDENTIFIER)
    want = [(1, 10), (2, 20), (3, 30), (1, 40), (2, 50)]
    got = [(3, 30), (1, 10), (2, 50), (1, 40)]
    result = _match_epochs(endpoint, want, got, [(7, 3, 2, api.ResetPolicy.CLEARS)])
    assert result.ok and result.complete
    assert result.reordered > 0
    assert result.cancelled == (("out", 7, (2, 20)),)
    # The flat matcher cannot express this legitimate cancellation/tag reuse.
    assert not api.compare(endpoint, want, got).ok


@pytest.mark.parametrize("matching", list(api.Matching))
def test_reset_match_agrees_with_independent_small_epoch_enumeration(matching):
    from collections import Counter

    endpoint = _sink(matching)
    values = ((0, 0), (1, 1))
    streams = [(), *[(value,) for value in values], *itertools.product(values, repeat=2)]

    def pairs(expected, observed, cancel):
        if matching is api.Matching.ORDERED:
            return (observed == expected[:len(observed)]
                    and (cancel or len(expected) == len(observed)))
        if matching is api.Matching.CONTENT:
            return (not (Counter(observed) - Counter(expected))
                    and (cancel or Counter(expected) == Counter(observed)))
        by_id = {key: [row for row in expected if row[0] == key] for key in (0, 1)}
        for row in observed:
            queue = by_id[row[0]]
            if not queue or queue.pop(0) != row:
                return False
        return cancel or not any(by_id.values())

    for before, after, got_before, got_after in itertools.product(streams, repeat=4):
        want, got = list(before + after), list(got_before + got_after)
        cut = (3, len(before), len(got_before), api.ResetPolicy.CLEARS)
        result = _match_epochs(endpoint, want, got, [cut])
        assert result.ok == (pairs(before, got_before, True) and pairs(after, got_after, False))


def test_cancellation_evidence_is_immutable_and_not_silently_truncated():
    endpoint = _sink(api.Matching.ORDERED)
    want = [(1, 10)] * 30
    result = _match_epochs(endpoint, want, [], [(40, 30, 0, api.ResetPolicy.CLEARS)])
    image = result.as_dict()
    assert image["cancelled"] == 30 and image["cancellations_elided"] == 10
    assert len(image["cancellations"]) == 20
    image["cancellations"][0][2][1] = 99
    assert result.cancelled[0] == ("out", 40, (1, 10))
    mutated = _match_epochs(endpoint, want[:-1] + [(1, 11)], [],
                           [(40, 30, 0, api.ResetPolicy.CLEARS)])
    assert result.as_dict()["cancellations_digest"] != mutated.as_dict()["cancellations_digest"]


@pytest.mark.isolation
@pytest.mark.parametrize("wrong", [False, True])
def test_behavior_batches_use_the_shared_reset_matcher(tmp_path, wrong):
    from tests.support.device_boundary import evaluate_device

    reference_source = """class M:
    def reset(self):
        pass
    def process(self, in_a):
        return [{'out_y': in_a}, {'out_y': in_a + 1}]
"""
    source = reference_source.replace(
        "return [{'out_y': in_a}, {'out_y': in_a + 1}]",
        f"if in_a == 3:\n            return [{{'out_y': {4 if wrong else 3}}}]\n"
        "        return [{'out_y': in_a}, {'out_y': in_a + 1}]")
    path = tmp_path / "behavior.py"
    path.write_text(source)
    contract = _contract("ready-valid")
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": reference_source.encode()},
        config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract),
                     reference=reference)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, timeout_s=90),
        api.GenerationSpec(4, 2, 1, 0), bounded_analysis=False)
    stimulus = api.Stimulus(
        {"in": [(3,), (5,)]},
        [api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
         api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1})])
    assert not stimulus.faults_against(contract)
    result = evaluate_device(
        task, [stimulus], handle._artifact, reference=reference,
        catalogue=handle.frontend_catalogue)
    assert not result.no_result_failures, result.no_result_failures
    assert result.complete and result.passed is (not wrong), result.as_dict()
    comparison = result.results[0].comparison
    assert comparison.cancelled == (("out", 2, (4,)),)
    assert len(comparison.faults) == int(wrong)
    records = result.records[0].records
    assert any(value for _, value in records[1].hits)
    assert not any(value for _, value in records[2].hits)
    assert any(value for _, value in records[3].hits)


def test_mixed_input_reset_policies_do_not_invent_a_response_pairing():
    from tests.sim.test_multi_endpoint_reference import _contract as multi_contract

    image = multi_contract().as_dict()
    for endpoint in image["endpoints"]:
        if endpoint["direction"] == "in":
            endpoint["reset_policy"] = "clears" if endpoint["name"] == "left" else "keeps"
    contract = api.TaskContract.from_dict(image)
    progress = ReferenceProgress(contract)
    progress.before_reset(("clk",), {"sum": [(9,)]}, 3)
    with pytest.raises(api.Refused, match="reference-reset-policy-undeclared"):
        _match_epochs(next(endpoint for endpoint in contract.outputs()
                           if endpoint.name == "sum"), [(9,)], [], progress.cuts["sum"])


@pytest.mark.isolation
@pytest.mark.parametrize("missing_other_domain", [False, True])
def test_atomic_reference_reset_cancels_only_its_own_domain(tmp_path, missing_other_domain):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from tests.support.atomic_behavior import _contract as atomic_contract
    from tests.support.device_boundary import evaluate_device

    reference_source = """class M:
    def reset(self):
        pass
    def reset_a(self):
        pass
    def reset_b(self):
        pass
    def process_a(self, a):
        return [{'oa': a}, {'oa': a + 1}]
    def process_b(self, b):
        return [{'ob': b}, {'ob': b + 1}]
"""
    source = reference_source.replace(
        "return [{'oa': a}, {'oa': a + 1}]", "return [{'oa': a}]")
    if missing_other_domain:
        source = source.replace(
            "return [{'ob': b}, {'ob': b + 1}]",
            "if b == 2:\n            return []\n"
            "        return [{'ob': b}, {'ob': b + 1}]")
    image = atomic_contract().as_dict()
    for endpoint in image["endpoints"]:
        endpoint["reset_policy"] = "clears"
    contract = api.TaskContract.from_dict(image)
    path = tmp_path / "behavior.py"
    path.write_text(source)
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": reference_source.encode()},
        config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract),
                     reference=reference)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, timeout_s=90),
        api.GenerationSpec(3, 2, 0, 0), bounded_analysis=False)
    schedule = GlobalSchedule(
        [ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
         ClockTiming("b", 3, clock="b_clk", reset="b_rst")],
        [GlobalEventSlot(time, event=api.EventSlot(
            offers=offers, ticks=ticks, resets=resets,
            controls={"a_rst": int("a" in resets), "b_rst": 0,
                      "oa_ready": 1, "ob_ready": 1}))
         for time, offers, ticks, resets in (
             (0, ("a_in", "b_in"), ("a", "b"), ()),
             (2, (), ("a",), ("a",)),
             (3, ("b_in",), ("b",), ()))])
    stimulus = api.Stimulus(
        {"a_in": [(1,)], "b_in": [(2,), (1,)]},
        [slot.event for slot in schedule.slots], clock_schedule=schedule)
    assert not stimulus.faults_against(contract)
    result = evaluate_device(
        task, [stimulus], handle._artifact, reference=reference,
        catalogue=handle.frontend_catalogue)
    assert not result.no_result_failures, result.no_result_failures
    assert result.complete and result.passed is (not missing_other_domain), result.as_dict()
    comparison = result.results[0].comparison
    assert comparison.cancelled == (("a_out", 1, (2,)),)
    assert bool(comparison.faults) is missing_other_domain
