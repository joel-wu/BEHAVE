"""Two execution phases, one driver/reference/matcher and one score."""

from dataclasses import replace

import pytest

from behave.sim import api
from tests.sim.test_api_path import Doubling, _evaluate, _ref, contract, realization
from tests.support.reference import load


def _requests(*, tail=200):
    return tuple(api.Stimulus(queues={"in": [(a,)] * 100},
                             replay_policy=api.ReplayPolicy(3, tail, 3)) for a in (0, 1))


def test_suite_preserves_order_rows_initialization_and_policy():
    c, requests = contract(), _requests()
    suite = api.build_replay_suite(c, requests)
    assert suite.isolated == requests
    assert [s.depth("in") for s in suite.stimuli] == [100, 100, 200]
    stream, = suite.continuous
    assert dict(stream.queues)["in"] == ((0,),) * 100 + ((1,),) * 100
    assert stream.replay_policy == api.ReplayPolicy(3, 200, 0)
    assert suite.members == ((stream.fingerprint, tuple(s.fingerprint for s in requests)),)
    assert not stream.slots  # Runtime determines clock length, not 100 or 200.


def test_suite_never_merges_distinct_initial_states_or_drain_policies():
    a, b = _requests()
    b = api.Stimulus(queues=b.queues, initial_state={"seed": 1}, replay_policy=b.replay_policy)
    suite = api.build_replay_suite(contract(), (a, b))
    assert [s.depth("in") for s in suite.continuous] == [100, 100]
    assert [s.initial_state for s in suite.continuous] == [a.initial_state, b.initial_state]
    short = api.Stimulus(queues=b.queues, replay_policy=replace(a.replay_policy, drain_slots=7))
    suite = api.build_replay_suite(contract(), (a, short))
    assert [s.replay_policy.drain_slots for s in suite.continuous] == [200, 7]


def test_identical_single_receptive_member_does_not_duplicate_evidence():
    request = api.Stimulus(queues={"in": [(1,)] * 100}, replay_policy=api.ReplayPolicy(3, 200, 0))
    suite = api.build_replay_suite(contract(), (request,))
    assert suite.stimuli == suite.isolated == (request,)
    assert suite.continuous == ()
    assert suite.members == ((request.fingerprint, (request.fingerprint,)),)


def test_fixed_schedule_is_not_silently_reinterpreted():
    request = api.Stimulus(queues={"in": [(1,)]}, slots=[api.EventSlot(offers=["in"])])
    with pytest.raises(api.Refused, match="replay-suite-fixed-schedule"):
        api.build_replay_suite(contract(), (request,))
    with pytest.raises(ValueError, match="deduplicate"):
        api.build_replay_suite(contract(), (_requests()[0],) * 2)


class CountingDevice(Doubling):
    def __init__(self, wrap):
        self.wrap = wrap
        super().__init__(gain=1)

    def reset(self):
        super().reset()
        self.count = 0

    def settle(self, presented, controls):
        output = super().settle(presented, controls)
        self.accepting = bool(presented is not None and controls["in_valid"] and output["in_ready"])
        if self.accepting:
            count = self.count % 100 if self.wrap else self.count
            self.next_held = ((presented[0] + count + 1) & 255,)
        return output

    def edge(self):
        super().edge()
        self.count += self.accepting


@pytest.mark.parametrize("wrap", [False, True])
def test_reference_and_candidate_keep_state_across_sequence_boundaries(wrap):
    reference = load({"behavior.py": b"""class M:
    def reset(self): self.n = 0
    def process(self, in_a):
        self.n += 1
        return {'out_y': (in_a + self.n) & 255}
"""}, config={"class": "M"})
    c = contract()
    task = api.admit(c, realization(), reference=reference)
    suite = api.build_replay_suite(c, _requests())
    result = _evaluate(task, suite, CountingDevice(wrap), reference)
    assert result.complete, result.as_dict()
    assert all(not run.faults for run in result.results[:2])
    assert bool(result.results[-1].faults) is wrap
    assert result.passed is not wrap
    for run, size in zip(result.results, (100, 100, 200), strict=True):
        trace = run.trace
        assert trace.accepted_count(["in"]) == trace.accepted_count(["out"]) == size
        assert sum(slot.reset for slot in trace.stimulus.slots) == 3
        window = api.workload_window(c, trace, expected_outputs={"out": size})
        assert window["start_slot"] == 3
        assert window["observation_slots"] == 200
        assert window["finish_slot"] + 200 == len(trace.records)
    stream = result.results[-1].trace
    assert all(dict(s.controls)["out_ready"] == 1 for s in stream.stimulus.slots)


class LateExtraOutput(Doubling):
    def reset(self):
        super().reset()
        self.time = 0

    def settle(self, presented, controls):
        result = super().settle(presented, controls)
        if self.time == 150:
            result.update(out_valid=1, out_y=7)
        return result

    def edge(self):
        super().edge()
        self.time += 1


@pytest.mark.parametrize("tail,passed", [(64, True), (200, False)])
def test_extended_tail_still_checks_late_unsolicited_outputs(tail, passed):
    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    request = api.Stimulus(queues={"in": [(1,)]}, replay_policy=api.ReplayPolicy(3, tail, 0))
    result = _evaluate(task, (request,), LateExtraOutput(), reference)
    assert result.complete and result.passed is passed, result.as_dict()
    assert bool(result.results[0].faults) is not passed
    window = api.workload_window(c, result.results[0].trace, expected_outputs={"out": 1})
    assert window["work_slots"] == 2  # The late error is scored, not useful work.
    assert window["observation_slots"] == tail


def test_default_tail_is_200_but_explicit_policy_is_not_overwritten():
    assert api.ReplayPolicy().drain_slots == 200
    assert api.ReplayPolicy.from_dict(api.ReplayPolicy(2, 7, 0).as_dict()).drain_slots == 7


def test_unfinished_continuous_phase_cannot_publish_isolated_pass(monkeypatch):
    from behave.sim import evaluation
    from behave.sim.evaluation_policy import verdict

    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    suite = api.build_replay_suite(c, _requests())
    run = evaluation.run

    def interrupted(task, stimulus, artifact, **kwargs):
        if stimulus.depth("in") > 100:
            return api.RunFailure.infrastructure("test-worker", api.Refusal("worker-lost", "test"))
        return run(task, stimulus, artifact, **kwargs)

    monkeypatch.setattr(evaluation, "run", interrupted)
    result = _evaluate(task, suite, Doubling(), reference)
    assert all(not run.faults and run.was_compared for run in result.results[:2])
    assert len(result.results) == 3 and not result.complete and not result.passed
    assert not verdict(result).authoritative
    assert [row["phase"] for row in result.execution_cost["phases"]] == ["isolated", "continuous"]


@pytest.mark.parametrize("capture,phases,expected", [
    (True, None, [True, True]),
    (True, ("isolated",), [True, False]),
    (False, ("isolated",), [False, False]),
    ('history', None, ['history', 'history']),
    ('history', ('isolated',), ['history', False]),
])
def test_query_capture_follows_consumed_phases_without_changing_checks(
        monkeypatch, capture, phases, expected):
    from behave.sim import evaluation

    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    suite = api.build_replay_suite(c, _requests(tail=4))
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    original = evaluation._batched
    profiles = []

    def batched(*args, **kwargs):
        profiles.append(kwargs["capture_query_boundaries"])
        return original(*args, **kwargs)

    monkeypatch.setattr(evaluation, "_batched", batched)
    execution = evaluation.ReplayExecution(
        task, Doubling(), reference, catalogue,
        capture_query_boundaries=capture, query_boundary_phases=phases)
    result = execution.evaluate(suite)
    assert profiles == expected
    assert result.required_complete and result.passed
    assert [run.trace.accepted_count(["out"]) for run in result.results] == [100, 100, 200]
    assert all(run.was_compared for run in result.results)


@pytest.mark.parametrize("analysis", (False, True))
def test_command_keeps_continuous_query_capture_only_for_analysis(
        tmp_path, monkeypatch, analysis):
    from behave.sim import evaluation
    from tests.sim.test_command_handler import _BaselineOnly
    from tests.support.device_boundary import prepare_device_command

    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    profiles = []
    original = evaluation._batched

    def batched(_task, stimuli, _artifact, **kwargs):
        if stimuli:
            profiles.append((tuple(s.depth("in") for s in stimuli),
                             kwargs["capture_query_boundaries"]))
        return original(_task, stimuli, _artifact, **kwargs)

    monkeypatch.setattr(evaluation, "_batched", batched)
    request = prepare_device_command(
        task, Doubling(), run_directory=tmp_path / "run",
        baseline=api.build_baseline(c, policy=api.BaselinePolicy(
            case_limit=3, max_queue_depth=2)),
        providers=(_BaselineOnly(),), generation_spec=api.GenerationSpec(12, 2, 2, 2),
        probe_budget=1, run_analysis=analysis, reference=reference)
    result = api.handle_command(request)
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    assert result.evaluation.required_complete
    assert len(profiles) == 2
    assert profiles[0][1] == ('history' if analysis else True)
    assert profiles[1][1] == ('history' if analysis else False)
    assert sum(profiles[0][0]) == sum(profiles[1][0])


def test_command_handoff_rejects_omission_of_the_continuous_phase(tmp_path, monkeypatch):
    from behave.sim import evaluation
    from tests.sim.test_command_handler import _BaselineOnly
    from tests.support.device_boundary import prepare_device_command

    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    command = prepare_device_command(
        task, Doubling(), run_directory=tmp_path / "run",
        baseline=api.build_baseline(c, policy=api.BaselinePolicy(case_limit=3)),
        providers=(_BaselineOnly(),), generation_spec=api.GenerationSpec(256, 64, 2, 4),
        probe_budget=0, run_analysis=False, reference=reference)

    def omit_continuous(generated, artifact, reference=None, **kwargs):
        return evaluation.evaluate(generated.task, generated.stimuli, artifact, reference,
                                   catalogue=generated.catalogue, **kwargs)

    monkeypatch.setattr(evaluation, "evaluate_generated", omit_continuous)
    result = api.handle_command(command)
    assert result.status is api.CommandStatus.REFUSED
    assert result.failure.kind == "sim-command-concrete-stimuli-mismatch"
