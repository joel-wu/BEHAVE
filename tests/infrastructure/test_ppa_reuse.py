"""PPA reuses the shared comparator, replay suite and Sim workload windows."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_ppa import experiment, fixture_task

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.ppa import performance, worker
from behave.sim import api
from behave.task_execution.comparison import ComparisonInputs
from behave.task_execution.judgment import ComponentJudgment


@pytest.fixture
def execution(tmp_path, monkeypatch):
    from behave.task_execution import comparison

    _, task, contract = fixture_task()
    exp = experiment()
    requests = tuple(performance.stimulus(contract, replace(exp.workloads[0], inputs=((value,),)))
                     for value in (1, 2))
    case = SimpleNamespace(
        inputs=ComparisonInputs("rtl", task.reference_behavior, task.reference_behavior,
                                task.contract, task.run_policy, task.judge_policy),
        exp=exp, contract=contract, requests=requests, calls=[], score=1,
        objects=DirectoryStore(tmp_path / "objects"), windows=True, work_slots=4,
    )

    def compare(inputs, **kwargs):
        case.calls.append((inputs, kwargs))
        stimuli = (requests if inputs.replay_stimuli is None else
                   tuple(api.Stimulus.from_dict(row) for row in parse_json(inputs.replay_stimuli)["stimuli"]))
        pool = case.objects.put(canonical_json({"stimuli": [request.as_dict() for request in stimuli]}))
        windows = {request.fingerprint: {"start_slot": 3,
                   "finish_slot": None if case.work_slots is None else 3 + case.work_slots,
                   "work_slots": case.work_slots, "observation_slots": 200} for request in stimuli}
        detail = {"replay_pool": pool.as_dict(),
                  "workload_windows": windows if case.windows else {}}
        return ComponentJudgment(case.score, "shared-comparison",
            origin="infrastructure" if case.score is None else None,
            detail=detail, evidence=(pool,))

    monkeypatch.setattr(comparison, "compare_sources", compare)
    case.run = lambda mode="check", pool=(): worker.execute(
        case.inputs, exp, mode, tuple(request.as_dict() for request in pool),
        tmp_path, case.objects, liberty=None, sta=None)
    return case


@pytest.mark.parametrize("score", [0, 1, None])
def test_check_is_one_shared_comparison_with_normal_failure_feedback(execution, score):
    execution.score = score
    result = execution.run()
    assert len(execution.calls) == 1
    assert execution.calls[0][0] == execution.inputs
    assert result["score"] == score
    assert result["judgment"]["code"] == "shared-comparison"
    assert len(result["pool"]) == 2


def test_historical_inputs_are_retained_for_common_final_not_replayed_during_each_check(execution):
    extra = performance.stimulus(execution.contract, execution.exp.workloads[0])
    result = execution.run(pool=(extra,))
    assert len(execution.calls) == 1 and len(result["pool"]) == 3
    assert execution.calls[0][0].replay_stimuli is None


def test_final_uses_the_shared_explicit_replay_interface(execution):
    execution.run("final", execution.requests)
    inputs, = [entry[0] for entry in execution.calls]
    assert {api.Stimulus.from_dict(row).fingerprint for row in parse_json(inputs.replay_stimuli)["stimuli"]} == {
        request.fingerprint for request in (*execution.requests,
            performance.stimulus(execution.contract, execution.exp.workloads[0]))}


@pytest.mark.parametrize("mode", ["mapped", "performance", "final"])
@pytest.mark.parametrize("score", [0, 1, None])
def test_timing_consumes_public_windows_without_another_replay(execution, mode, score):
    execution.score = score
    result = execution.run(mode, execution.requests)
    assert len(execution.calls) == 1
    inputs = execution.calls[0][0]
    requests = parse_json(inputs.replay_stimuli)["stimuli"]
    assert len(requests) == (1 if mode == "performance" else 3)
    assert all(api.Stimulus.from_dict(row).replay_policy is not None for row in requests)
    assert result["performance"]["score"] == score
    assert result["performance"]["time_ns"] == (80. if score == 1 else None)
    if score == 1:
        assert result["performance"]["workloads"][0]["cycles"] == 4


def test_missing_public_timing_is_not_inferred_from_wall_time(execution):
    execution.windows = False
    result = execution.run("mapped", execution.requests)
    assert result["score"] == 1
    assert result["performance"]["time_ns"] is None


@pytest.mark.parametrize("mode", ["mapped", "performance", "final"])
def test_budget_pass_without_complete_workload_cannot_produce_a_fast_point(execution, mode):
    execution.work_slots = None
    result = execution.run(mode, execution.requests)
    assert result["score"] == 1
    assert result["performance"]["score"] == 1
    assert result["performance"]["time_ns"] is None
    assert all(row["status"] == "not-completed"
               for row in result["performance"]["workloads"])


def test_explicit_pool_roundtrips_through_shared_worker_inputs(execution):
    inputs = replace(execution.inputs,
        replay_stimuli=canonical_json({"stimuli": [request.as_dict() for request in execution.requests]}))
    ref, closure = inputs.stage(execution.objects)
    copied = DirectoryStore(execution.objects.directory.parent / "copied")
    copied.publish(execution.objects, closure)
    assert ComparisonInputs.from_dict(parse_json(copied.read(ref)), copied) == inputs


@pytest.mark.parametrize("completion,compared,completed", [
    (api.Completion.NORMAL, True, 41),
    (api.Completion.NORMAL, False, 0),
    (api.Completion.CHECKPOINT, True, 0),
    (api.Completion.INCOMPLETE, True, 0),
    (api.Completion.PADDED, True, 0),
    (None, True, 0),
])
def test_replay_metadata_distinguishes_completed_baseline_from_continuous_tail(
        execution, completion, compared, completed):
    from behave.task_execution.simulator_judge import _replay_metadata

    requests = tuple(performance.stimulus(execution.contract,
        replace(execution.exp.workloads[0], inputs=((value,),) * 100)) for value in range(41))
    suite = api.build_replay_suite(execution.contract, requests)
    evaluation = SimpleNamespace(stimuli=suite.stimuli,
        results=tuple(SimpleNamespace(stimulus=s, comparison=SimpleNamespace(complete=compared),
                                      trace=None if completion is None else SimpleNamespace(completion=completion))
                      for s in requests),
        telemetry=(("replay_suite", suite.as_dict()),))
    detail, _ = _replay_metadata(execution.objects, requests, evaluation)
    assert detail["replay_phases"] == [
        {"phase": "isolated", "requested_runs": 41, "completed_runs": completed, "input_transactions": 4100},
        {"phase": "continuous", "requested_runs": 1, "completed_runs": 0, "input_transactions": 4100}]
    assert "score" not in detail
    saved = parse_json(execution.objects.read(BlobRef.from_dict(detail['replay_pool'])))
    assert len(saved['stimuli']) == 41  # Reporting never adds derived runs to the retained pool.


def test_single_receptive_workload_is_not_reported_as_a_second_continuous_run(execution):
    from behave.task_execution.simulator_judge import _replay_metadata

    request = performance.stimulus(execution.contract,
        replace(execution.exp.workloads[0], inputs=((1,),) * 64))
    suite = api.build_replay_suite(execution.contract, (request,))
    evaluation = SimpleNamespace(stimuli=suite.stimuli, results=(),
                                 telemetry=(("replay_suite", suite.as_dict()),))
    detail, _ = _replay_metadata(execution.objects, (request,), evaluation)
    assert detail['replay_phases'] == [
        {"phase": "isolated", "requested_runs": 1, "completed_runs": 0, "input_transactions": 64}]


@pytest.mark.parametrize("include_fixed", [False, True])
def test_shared_replay_builds_both_phases_and_retains_public_time_windows(execution, monkeypatch, include_fixed):
    from behave.task_execution.simulator_judge import _explicit_replay

    calls = []
    window = {"start_slot": 3, "finish_slot": 7, "work_slots": 4, "observation_slots": 200}
    evaluation = SimpleNamespace(fingerprint="evaluation", no_result_failures=(),
        compared=(), results=(), score_scope="full", required_stimuli=(),
        required_complete=True, budget_exhausted=False, requested_stimuli=(),
        unexecuted=(), accepted=0, checked_inputs=0,
        telemetry=(("workload_windows", (("continuous", tuple(window.items())),)),))

    class Handle:
        def replay(self, suite, *, reference, stop_on_failure=False):
            calls.append(suite)
            assert reference == "bound-reference"
            assert stop_on_failure is True
            return SimpleNamespace(evaluation=evaluation, authoritative=True, passed=False,
                                   as_dict=lambda: {"retained": "public-authority"})

    settled = []
    def diagnostics(*args, **kwargs):
        assert settled == [True]
        assert kwargs['reference_visible'] is False
        return {}
    monkeypatch.setattr(api, "evaluation_diagnostics", diagnostics)
    requests = execution.requests
    if include_fixed:
        left = execution.contract.inputs()[0].name
        fixed = api.framed(execution.contract, (api.EventSlot(offers=(left,)),),
                           queues={left: ((3,),)}, drain=4, reset_hold_cycles=3)
        requests += (fixed,)
    inputs = replace(execution.inputs,
        replay_stimuli=canonical_json({"stimuli": [request.as_dict() for request in requests]}))
    result = _explicit_replay(api, Handle(), "bound-reference", execution.contract, inputs,
                              inputs.policy, execution.objects, (),
                              SimpleNamespace(finish=lambda: settled.append(True)))
    suite, = calls
    assert suite.isolated == requests and len(suite.continuous) == 1
    assert result.score == 0  # Directly from Sim, not inferred from isolated tests.
    assert dict(result.detail["workload_windows"]["continuous"]) == window
    pool = parse_json(execution.objects.read(BlobRef.from_dict(dict(result.detail["replay_pool"]))))["stimuli"]
    assert len(pool) == len(requests)  # Continuous runs never recursively grow the pool.


def test_ppa_cpu_work_uses_the_shared_lease(monkeypatch):
    from behave.agent.cpu_resources import CpuResources, current_cpu_lease
    from behave.core.lifecycle import CleanupState

    resources = CpuResources(1, cpus=1)
    seen = []

    class Pool:
        def __init__(self, *, capacity):
            seen.append(capacity)
            self.endpoint = SimpleNamespace(as_dict=lambda: {"fixture_pool": capacity})
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def run(*args, lease, cpu_pool):
        assert current_cpu_lease() is lease
        assert cpu_pool == {"fixture_pool": 1}
        lease.cleanup = CleanupState.COMPLETE
        return {"done": True}

    monkeypatch.setattr(api, "CpuPool", Pool)
    native = SimpleNamespace(cpu_resources=resources, _run=run)
    assert worker.NativePPA.run(native, None, None, "eda", (), None) == {"done": True}
    assert seen == [1] and resources.snapshot()["active"] == 0
