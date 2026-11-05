"""Runtime requests keep their identity while recording the schedule that ran."""

import pytest

from behave.sim import api
from behave.sim.core.runtime_schedule import validate_resolution
from behave.sim.runtime_execution import completion_targets
from tests.sim.test_api_path import Doubling, _evaluate, _ref, contract, realization
from tests.sim.test_command_handler import _BaselineOnly
from tests.support.device_boundary import prepare_device_command


class DelayedDoubling(Doubling):
    def reset(self):
        super().reset()
        self.time = 0

    def settle(self, presented, controls):
        if self.time < 300:
            return {"in_ready": 0, "out_valid": 0, "out_y": 0}
        return super().settle(presented, controls)

    def edge(self):
        super().edge()
        self.time += 1


def _baseline(c):
    return api.build_baseline(c, policy=api.BaselinePolicy(case_limit=1),
                              execution_policy=api.ReplayPolicy(2, 4, 3))


@pytest.mark.parametrize("gain", [2, 3])
def test_public_runtime_replay_uses_actual_completion_and_binds_request(gain):
    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    # Nonzero values make the mutant observable; no fixed clock horizon is given.
    request = api.Stimulus(queues={"in": [(i,) for i in range(100)]},
                          replay_policy=api.ReplayPolicy(2, 4, 3))
    assert api.Stimulus.from_dict(request.as_dict()).fingerprint == request.fingerprint
    result = _evaluate(task, [request], DelayedDoubling(gain), reference)
    assert result.complete, result.as_dict()
    assert result.passed is (gain == 2), result.as_dict()
    run = result.results[0]
    trace = run.trace
    assert len(trace.records) > 400
    assert trace.accepted_count(["in"]) == trace.accepted_count(["out"]) == 100
    assert run.stimulus.fingerprint == trace.request.fingerprint == request.fingerprint
    assert trace.stimulus.replay_policy is None
    assert api.AuditedTrace.from_dict(trace.as_dict()).fingerprint == trace.fingerprint
    validate_resolution(c, request, trace.stimulus, trace.records, {"out": 100})
    with pytest.raises(api.Refused, match="runtime-schedule-incomplete"):
        truncated = api.Stimulus(queues=request.queues, slots=trace.stimulus.slots[:-1])
        validate_resolution(c, request, truncated, trace.records[:-1], {"out": 100})


def test_runtime_baseline_public_command_is_not_capped_by_analysis_bounds(tmp_path):
    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    request = prepare_device_command(
        task, DelayedDoubling(), run_directory=tmp_path / "command",
        baseline=_baseline(c), providers=(_BaselineOnly(),),
        generation_spec=api.GenerationSpec(256, 64, 2, 4),
        probe_budget=0, run_analysis=False, reference=reference)
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.complete and result.evaluation.passed, result.as_dict()
    assert all(len(run.trace.records) > 400 for run in result.evaluation.results)
    assert all(run.trace.accepted_count(["in"]) == 100 for run in result.evaluation.results)


@pytest.mark.parametrize("outputs", [0, 1, 3])
def test_completion_counts_reference_sequences_not_assumed_one_to_one(outputs):
    from tests.support.reference import load

    source = ("class M:\n"
              "    def reset(self): pass\n"
              "    def process(self, in_a):\n"
              f"        return [{{'out_y': in_a}} for _ in range({outputs})]\n")
    c = contract()
    reference = load({"behavior.py": source.encode()}, config={"class": "M"})
    task = api.admit(c, realization(), reference=reference)
    request = _baseline(c).stimuli[0]
    assert completion_targets(task, request, reference) == {"out": 100 * outputs}


@pytest.mark.parametrize("group,outputs", [(1, 0), (1, 3), (2, 1)])
def test_variable_output_completion_reaches_the_independent_matcher(group, outputs):
    from collections import deque

    from tests.support.reference import load

    class GroupedDevice:
        kind, identity = "device", "doubling"

        def reset(self):
            self.count, self.pending = 0, deque()

        def settle(self, presented, controls):
            self.input = presented
            self.take = bool(self.pending) and bool(controls["out_ready"])
            return {"in_ready": 1, "out_valid": int(bool(self.pending)),
                    "out_y": self.pending[0] if self.pending else 0}

        def edge(self):
            if self.take:
                self.pending.popleft()
            if self.input is not None:
                self.count += 1
                if self.count % group == 0:
                    self.pending.extend([self.input[0]] * outputs)

    source = ("class M:\n"
              "    def reset(self): self.n = 0\n"
              "    def process(self, in_a):\n"
              "        self.n += 1\n"
              f"        return [{{'out_y': in_a}} for _ in range({outputs})] if self.n % {group} == 0 else []\n")
    c = contract()
    reference = load({"behavior.py": source.encode()}, config={"class": "M"})
    task = api.admit(c, realization(), reference=reference)
    request = api.Stimulus(queues={"in": [(i,) for i in range(100)]},
                          replay_policy=api.ReplayPolicy(2, 4, 3))
    result = _evaluate(task, [request], GroupedDevice(), reference)
    assert result.complete and result.passed, result.as_dict()
    assert result.results[0].trace.accepted_count(["out"]) == 100 // group * outputs


def test_completion_reference_failure_is_no_result_not_candidate_zero():
    from behave.sim.core.failure import FailureDisposition, FailureOrigin
    from tests.support.reference import load

    source = ("class M:\n"
              "    def reset(self): pass\n"
              "    def process(self, in_a): raise RuntimeError('broken reference')\n")
    c = contract()
    reference = load({"behavior.py": source.encode()}, config={"class": "M"})
    task = api.admit(c, realization(), reference=reference)
    result = _evaluate(task, _baseline(c).stimuli, Doubling(), reference)
    failure = result.results[0].run_failure
    assert failure.origin is FailureOrigin.REFERENCE
    assert failure.disposition is FailureDisposition.NO_RESULT
    assert not result.complete and not result.passed


def test_completion_preserves_the_original_transport_failure(monkeypatch):
    from behave.sim.behavior.isolated import Broken
    from behave.sim.core.failure import FailureDisposition, FailureOrigin, FailureRaised

    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)

    def unavailable(*_args, **_kwargs):
        raise Broken("reference stopped", code="isolation-output-limit",
                     detail="explicit operator quota exhausted")

    monkeypatch.setattr(type(reference), "instantiate", unavailable)
    with pytest.raises(FailureRaised) as caught:
        completion_targets(task, _baseline(c).stimuli[0], reference)
    failure = caught.value.failure
    assert failure.origin is FailureOrigin.REFERENCE
    assert failure.disposition is FailureDisposition.NO_RESULT
    assert failure.reason.detail["cause"] == "isolation-output-limit"
    assert failure.reason.detail["detail"] == "explicit operator quota exhausted"
