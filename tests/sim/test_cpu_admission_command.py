"""CPU waiting is resource control, not candidate evidence or a new score."""

from behave.sim import api

from .test_command_handler import _request


def test_command_resource_failure_preserves_typed_no_result(tmp_path):
    with api.CpuPool(1) as cpu:
        endpoint = cpu.endpoint
    request = _request(tmp_path, run_analysis=False)
    with api.cpu_scope(endpoint):
        result = api.handle_command(request)
    assert result.status is api.CommandStatus.INFRASTRUCTURE_FAILURE
    assert not result.decision.authoritative
    assert result.decision.passed is None
    cause = result.failure["run_failure"]
    assert cause["origin"] == "infrastructure"
    assert cause["disposition"] == "no-result"
    assert cause["reason"]["kind"] == "cpu-admission-unavailable"
    assert result.failure["cpu_admission"]["reservations"]


def test_command_score_is_unchanged_with_shared_admission(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "pooled").mkdir()
    plain = api.handle_command(_request(tmp_path / "plain", run_analysis=False))
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        pooled = api.handle_command(_request(tmp_path / "pooled", run_analysis=False))
    assert pooled.status is plain.status is api.CommandStatus.EVALUATED, (plain.failure, pooled.failure)
    assert pooled.decision.authoritative == plain.decision.authoritative
    assert pooled.evaluation.passed == plain.evaluation.passed
    assert pooled.evaluation.hits == plain.evaluation.hits
    execution = pooled.evaluation.execution_cost
    # Final assembly reuses this invocation's baseline; it needs no new lease.
    assert execution["reuse"]["completed_runs"] == len(pooled.evaluation.results)
    assert execution["cpu_admission"]["reservations"] == []
    reservations = usage.as_dict()["reservations"]
    assert reservations and all(row["released"] for row in reservations)
    execution["cpu_admission"]["reservations"].append(reservations[0])
    assert pooled.evaluation.execution_cost["cpu_admission"]["reservations"] == []


def test_baseline_service_failure_is_not_replaced_by_workload_cpu_admission(tmp_path, monkeypatch):
    from behave.sim import evaluation
    from behave.sim.core.failure import FailureRaised
    from behave.sim.cpu_admission import cpu_cleanup_unconfirmed, reserve_cpus
    from behave.sim.core.run_record import PER_PROCESS, VERIFIED_NOT_NEEDED
    from tests.support.device_boundary import prepare_device_command

    from .test_api_path import Doubling, _ref, contract, realization
    from .test_command_handler import _BaselineOnly
    from .test_cpu_admission import eventually

    reference, task_contract = _ref(), contract()
    task = api.admit(task_contract, realization(), reference=reference)
    baseline = api.build_baseline(task_contract, policy=api.BaselinePolicy(case_limit=1))
    command = prepare_device_command(task, Doubling(), run_directory=tmp_path / "command",
        baseline=baseline, providers=(_BaselineOnly(),), probe_budget=0,
        generation_spec=api.GenerationSpec(256, 64, 2, 4),
        run_analysis=False, reference=reference)
    original = api.RunFailure.infrastructure("rtl-simulation-service", api.Refusal(
        "original-namespace-failure", "the namespace cleanup was not confirmed"))
    batches = []

    def failed_batch(_task, stimuli, _artifact, **_kwargs):
        batches.append(tuple(stimuli))
        try:
            with reserve_cpus(1):
                cpu_cleanup_unconfirmed()
        except FailureRaised:
            pass  # The replay service returns the original per-run diagnostic.
        return ({seed.fingerprint: original for seed in stimuli},
                evaluation._replay_note(PER_PROCESS, VERIFIED_NOT_NEEDED, len(stimuli), original.why))

    monkeypatch.setattr(evaluation, "_batched", failed_batch)
    with api.CpuPool(1) as pool, api.cpu_scope(pool.endpoint) as usage:
        result = api.handle_command(command)
        eventually(lambda: pool.snapshot()["quarantined"])
    assert result.evaluation is not None, result.as_dict()
    assert not result.decision.authoritative and result.decision.passed is None
    assert len(batches) == 1
    assert result.evaluation.no_result_failures[0][1] is original
    assert len(result.evaluation.unexecuted) == 1  # No new continuous workload.
    assert all(row["status"] != "cpu-pool-quarantined" for row in usage.as_dict()["reservations"])
