"""Budget boundaries preserve complete evidence, not merely a passing prefix."""

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim import api, evaluation
from behave.sim.evaluation_policy import verdict
from behave.sim.scoring_policy import active_scoring, check_scoring
from behave.sim.search_policy import SearchBudget, remaining_timeout, search_scope

from .test_api_path import Doubling, FirstOnlyDoubling, _ref, contract, realization, schedule
from .test_command_handler import _request


def test_public_settlement_default_is_owned_by_runtime():
    assert api.ScoringPolicy().transaction_checkpoints is defaults.SIM_TRANSACTION_CHECKPOINTS
    assert api.ScoringPolicy(transaction_checkpoints=True).as_dict()["boundary"] == (
        "fully-checked-transaction-prefix")
    assert api.ScoringPolicy(transaction_checkpoints=False).as_dict()["boundary"] == (
        "fully-checked-run")


def _run(monkeypatch, *, failure=None, fail_first=False, gain=2):
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    artifact = Doubling(gain)
    execution = evaluation.ReplayExecution(task, artifact, reference, catalogue)
    stimuli = (schedule([3]), schedule([4]), schedule([5]))
    original = evaluation.run
    calls = []

    def run(task, stimulus, artifact, **kwargs):
        calls.append(stimulus)
        if len(calls) == (1 if fail_first else 3):
            active_scoring().deadline = 0
            check_scoring()
        if len(calls) == 2 and failure is not None:
            return failure
        return original(task, stimulus, artifact, **kwargs)

    monkeypatch.setattr(evaluation, "run", run)
    with api.scoring_scope(api.ScoringPolicy()):
        result = execution.evaluate(stimuli)
    return result, calls


def test_incomplete_baseline_preserves_evidence_without_passing(monkeypatch):
    result, calls = _run(monkeypatch)
    assert len(calls) == 3
    assert result.budget_exhausted and not result.required_complete
    assert not result.passed and not result.complete
    assert len(result.requested_stimuli) == 3
    assert len(result.results) == 2 and len(result.unexecuted) == 1
    assert not verdict(result).authoritative
    assert result.as_dict()["score_scope"] == "incomplete-baseline"
    from behave.sim.authority import CONCRETE, Authoritative

    with pytest.raises(api.Refused, match="authority-evidence-not-whole"):
        Authoritative(CONCRETE, evaluation=result)


def test_empty_budget_has_no_score(monkeypatch):
    result, _ = _run(monkeypatch, fail_first=True)
    assert not result.results and not result.passed
    assert not verdict(result).authoritative


def test_interruption_stage_survives_final_assembly_and_public_diagnostics(monkeypatch):
    from behave.sim.diagnostics import evaluation_diagnostics

    task = api.admit(contract(), realization(), reference=_ref())
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    execution = evaluation.ReplayExecution(task, Doubling(2), None, catalogue)

    def stopped(*args, **kwargs):
        kwargs["_execution"]._stage = "reset-state-ownership"
        active_scoring().deadline = 0
        error = api.ScoringStopped()
        error.interrupted_tool = {"role": "state-ownership", "wall_seconds": 2.5,
                                  "status": "timed-out", "log_directory": "/private/tool/logs"}
        raise error

    monkeypatch.setattr(evaluation, "_evaluate", stopped)
    with api.scoring_scope(api.ScoringPolicy()):
        for _ in range(2):
            result = execution.evaluate((schedule([3]),))
            assert not result.passed and not result.results
            assert evaluation_diagnostics(result)["summary.json"]["interrupted_stage"] == "reset-state-ownership"
            assert evaluation_diagnostics(result)["summary.json"]["interrupted_tool"] == {
                "role": "state-ownership", "wall_seconds": 2.5, "status": "timed-out"}
            assert "/private" not in str(evaluation_diagnostics(result))


def test_budget_stop_keeps_tool_phases_even_when_final_evaluation_reuses_nothing(monkeypatch, tmp_path):
    from behave.sim.core.frozen import freeze
    from behave.sim.rtl.artifact import RTLArtifact

    from .test_rtl_tool_isolation import _command_row, _completed

    task = api.admit(contract(), realization(), reference=_ref())
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    artifact = RTLArtifact(tmp_path / "unchanged.sv", "M", tmp_path)
    tool = _completed(commands=(_command_row("verilator-simulate", timed_out=True),))
    artifact._record_tool_execution(tool)
    execution = evaluation.ReplayExecution(task, artifact, None, catalogue)
    with api.scoring_scope(api.ScoringPolicy()):
        active_scoring().deadline = 0
        for _ in range(2):
            result = execution.evaluate((schedule([3]),))
            assert result.budget_exhausted and not result.passed
            assert not result.results
            assert dict(result.telemetry)["rtl_toolchain"] == freeze({
                "version": "rtl-tool-lineage/1", "executions": [tool.as_dict()]})


@pytest.mark.parametrize("copy_fails", [False, True])
def test_interrupted_build_logs_survive_scratch_release(tmp_path, monkeypatch, copy_fails):
    from pathlib import Path
    from types import SimpleNamespace

    from behave.sim.rtl import replay_build

    error = api.ScoringStopped()
    if copy_fails:
        def fail(*args, **kwargs):
            raise OSError("diagnostic disk unavailable")
        monkeypatch.setattr(replay_build.shutil, "copytree", fail)
    with (pytest.raises(api.ScoringStopped) as caught,
          replay_build.replay_build_scope(SimpleNamespace(workdir=tmp_path)) as scope):
        scratch = scope.directory("logs-")
        (scratch / "0-0.stderr").write_text("compile in progress\n")
        error.interrupted_tool = {"role": "verilator-build", "log_directory": str(scratch)}
        raise error
    assert caught.value is error
    assert not scratch.exists()
    if copy_fails:
        assert error.interrupted_tool["log_retention_error"] == "OSError"
    else:
        log = Path(error.interrupted_tool["log_directory"])
        assert log.is_relative_to(tmp_path / "tool-logs")
        assert (log / "0-0.stderr").read_text() == "compile in progress\n"


def test_nested_replay_retains_logs_before_evaluation_consumes_cancellation(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from behave.sim.rtl.replay_build import replay_build_scope

    artifact = SimpleNamespace(workdir=tmp_path)
    error = api.ScoringStopped()
    with replay_build_scope(artifact) as scope:
        with pytest.raises(api.ScoringStopped), replay_build_scope(artifact):
            scratch = scope.directory("logs-")
            (scratch / "0-0.stdout").write_text("building\n")
            error.interrupted_tool = {"role": "verilator-build", "log_directory": str(scratch)}
            raise error
        retained = Path(error.interrupted_tool["log_directory"])
        assert retained.is_relative_to(tmp_path / "tool-logs")
    assert not scratch.exists()
    assert (retained / "0-0.stdout").read_text() == "building\n"


@pytest.mark.parametrize("kind", ["rtl", "behavior"])
@pytest.mark.parametrize("stop", [False, True])
def test_public_explicit_replay_reuses_checked_failure_stop(monkeypatch, kind, stop):
    from types import SimpleNamespace

    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    handle = SimpleNamespace(_task=task, _artifact=Doubling(3),
                             _goal_policy=SimpleNamespace(manifest=catalogue))
    stimuli = tuple(schedule([value]) for value in (3, 4, 5))
    method = api.RTLHandle.replay if kind == "rtl" else api.BehaviorHandle.replay
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "1")
    result = method(handle, stimuli, reference=reference, stop_on_failure=stop)
    assert result.authoritative and not result.passed
    assert len(result.evaluation.results) == (1 if stop else 3)
    assert result.evaluation.requested_stimuli == stimuli
    assert result.evaluation.failure_witness is not None


def test_known_output_mismatch_is_not_discarded(monkeypatch):
    result, _ = _run(monkeypatch, gain=3)
    assert result.mismatch_count > 0 and result.failure_witness is not None
    assert verdict(result).authoritative and not verdict(result).passed


def test_known_safety_hit_survives_interrupted_reference_comparison(monkeypatch):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))

    def interrupted(*args, **kwargs):
        active_scoring().deadline = 0
        check_scoring()

    monkeypatch.setattr(evaluation, "compare_run", interrupted)
    with api.scoring_scope(api.ScoringPolicy()):
        result = evaluation.evaluate(task, (schedule([3, 5], drain=3),), FirstOnlyDoubling(),
                                     reference, catalogue=catalogue)
    assert "monitor.input-completion" in result.safety_hits
    assert verdict(result).authoritative and not verdict(result).passed


def test_budget_settlement_releases_unfinished_comparison_carrier(monkeypatch):
    import weakref

    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    execution = evaluation.ReplayExecution(task, Doubling(), reference, catalogue)
    carriers = []

    def interrupted(*args, **kwargs):
        carriers.extend(weakref.ref(row) for row in execution._checked.values())
        active_scoring().deadline = 0
        check_scoring()

    monkeypatch.setattr(evaluation, "compare_run", interrupted)
    with api.scoring_scope(api.ScoringPolicy()):
        result = execution.evaluate((schedule([3, 5], drain=3),))
    assert result.budget_exhausted and not verdict(result).authoritative
    assert not execution._checked
    assert carriers and all(carrier() is None for carrier in carriers)


@pytest.mark.parametrize("wrong_memory", [False, True])
def test_budget_still_requires_final_external_memory_comparison(wrong_memory):
    from behave.sim.core.compare import comparison_for

    from .test_memory_result_score import setup, trace_for

    task, reference = setup()
    trace = trace_for(task, {1: 7 if wrong_memory else 5, 2: 11})
    pending = trace_for(task, {1: 7, 2: 11}, input_value=9).stimulus
    comparison = comparison_for(task, reference, trace)
    result = api.ConcreteEvaluation(task,
        [api.RunResult.compared(trace.stimulus, trace, comparison)],
        catalogue=api.CatalogueManifest(()), requested_stimuli=(trace.stimulus, pending),
        budget_exhausted=True)
    assert verdict(result).authoritative is wrong_memory
    assert result.mismatch_count == 0
    assert result.memory_mismatch_count == int(wrong_memory)
    assert not result.passed


@pytest.mark.parametrize("origin", ["candidate", "reference", "verifier", "infrastructure"])
def test_budget_cannot_hide_already_reported_fault(monkeypatch, origin):
    failure = getattr(api.RunFailure, origin)("test", api.Refusal("test-fault", origin))
    result, _ = _run(monkeypatch, failure=failure)
    assert result.failures[0][1] == failure
    assert not result.passed
    assert verdict(result).authoritative == (origin == "candidate")
    if origin != "candidate":
        assert "no-result failures" in verdict(result).why
        assert origin in verdict(result).why


@pytest.mark.parametrize("budget_exhausted", [False, True])
def test_startup_failure_is_not_reported_as_an_empty_budget_window(budget_exhausted):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    stimulus = schedule([3])
    failure = api.RunFailure.reference("reference-startup", api.Refusal("startup-failed", "failed"))
    result = api.ConcreteEvaluation(task,
        [api.RunResult.failed_to_run(stimulus, failure)],
        catalogue=api.CatalogueManifest(()), budget_exhausted=budget_exhausted)
    decision = verdict(result)
    assert not decision.authoritative and result.accepted == 0
    assert "no-result failures (reference)" in decision.why
    assert result.failures == ((0, failure),)


def test_search_cannot_renew_scoring_deadline():
    with (api.scoring_scope(api.ScoringPolicy(time_budget_s=1)) as scoring,
          search_scope(SearchBudget(api.SearchPolicy(time_budget_s=600)).direction(1))):
        assert 0 < remaining_timeout(100) <= 1
        scoring.deadline = 0
        with pytest.raises(api.ScoringStopped):
            remaining_timeout(100)


def test_settled_scoring_scope_cannot_dispatch_another_command():
    policy = api.ScoringPolicy()
    with api.scoring_scope(policy) as budget:
        budget.deadline = 0
        budget.finish()
        check_scoring()  # Publication may finish after the work deadline.
        with pytest.raises(ValueError, match="already settled"), api.scoring_scope(policy):
            pytest.fail("a settled command must not obtain fresh scoring time")


def test_command_budget_stop_persists_empty_no_result(tmp_path):
    command = _request(tmp_path, run_analysis=False)
    with api.scoring_scope(command.scoring_policy) as scoring:
        scoring.deadline = 0
        result = api.handle_command(command)
    assert result.evaluation is not None, result.as_dict()
    assert result.evaluation.budget_exhausted
    assert result.decision is not None and not result.decision.authoritative
    assert not result.evaluation.results


def test_cleanup_failure_cannot_be_settled_as_budget_pass(tmp_path, monkeypatch):
    from behave.sim import generation
    from behave.sim.cpu_admission import cpu_cleanup_unconfirmed

    command = _request(tmp_path, run_analysis=False)
    generate = generation.generate_stimuli

    def stop(*args, **kwargs):
        result = generate(*args, **kwargs)
        cpu_cleanup_unconfirmed()
        active_scoring().deadline = 0
        return result

    monkeypatch.setattr("behave.sim.command.generation_service.generate_stimuli", stop)
    result = api.handle_command(command)
    assert result.status is api.CommandStatus.INFRASTRUCTURE_FAILURE
    assert result.decision is None or not result.decision.authoritative


@pytest.mark.isolation
@pytest.mark.parametrize("late_terminal", [False, True])
def test_real_inflight_tool_deadline_is_cancellation_not_malformed_json(tmp_path, monkeypatch, late_terminal):
    from behave.isolation import IsolationStatus
    from behave.sim.rtl import tool_isolation

    environment = api.prepare_tool_environment("simulation")
    command = api.ToolCommand("slow", ("/runtime/python/bin/python", "-c",
        "import time; print('started', flush=True); time.sleep(30)"), timeout_s=30)
    run = tool_isolation.run
    completed = []

    def capture(*args, **kwargs):
        result = run(*args, **kwargs)
        completed.append(result)
        return result

    monkeypatch.setattr(tool_isolation, "run", capture)
    if late_terminal:
        import subprocess

        from behave.isolation import linux

        wait = linux.SandboxProcess.wait

        def collect_then_expire(process, timeout=None):
            wait(process, timeout)
            # Reproduce a timed wait racing with the reader's valid terminal
            # receipt, using a real terminated namespace and real cleanup.
            raise subprocess.TimeoutExpired(process._spec.argv, timeout)

        monkeypatch.setattr(linux.SandboxProcess, "wait", collect_then_expire)
    with api.scoring_scope(api.ScoringPolicy(time_budget_s=5)) as budget:
        with pytest.raises(api.ScoringStopped) as caught:
            environment.execute(tmp_path, (api.ToolGroup((command,)),),
                                limits=api.tool_limits(wall_seconds=30))
        assert budget.cleanup_confirmed
        stopped = caught.value.interrupted_tool
        assert stopped["role"]
        assert stopped["wall_seconds"] > 0
        assert stopped["status"] == IsolationStatus.TIMED_OUT.value
        assert stopped["log_directory"] is not None
    assert len(completed) == 1
    assert completed[0].status is IsolationStatus.TIMED_OUT
    assert completed[0].cleanup_confirmed


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ["behavior", pytest.param("rtl", marks=pytest.mark.eda)])
@pytest.mark.parametrize("workers", [1, 2])
def test_public_command_settles_real_first_run_without_replaying_it(tmp_path, monkeypatch, kind, workers):
    from .test_baseline_public_sequence import REFERENCE, RTL, _contract

    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(REFERENCE)
        task = api.admit(contract, api.realization_of_behavior_source(REFERENCE, contract), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path), bounded_analysis=False)
    else:
        source = RTL.replace("VALUE", "a")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", str(workers))
    assess = evaluation.assess_checked
    calls = []

    def settle(*args, **kwargs):
        result = assess(*args, **kwargs)
        calls.append(result)
        assert result.result.ok
        active_scoring().deadline = 0
        return result

    monkeypatch.setattr(evaluation, "assess_checked", settle)
    command = handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=2),
        search_policy=api.SearchPolicy(time_budget_s=0))
    result = api.handle_command(command)
    assert result.decision is not None and not result.decision.authoritative, result.as_dict()
    assert not result.evaluation.passed and not result.evaluation.required_complete
    assert result.evaluation.budget_exhausted and not result.evaluation.complete
    # A second active lane may finish concurrently with budget expiry. Every
    # completed comparison must survive, without rerunning either lane to settle.
    assert 1 <= len(calls) == len(result.evaluation.results) <= workers
    assert len(result.evaluation.unexecuted) > 0
    retained = {row.stimulus.identity_bytes: row for row in result.evaluation.results}
    assert all(retained[row.result.stimulus.identity_bytes] is row.result for row in calls)
    if kind == "rtl":
        windows = dict(dict(result.evaluation.telemetry)["workload_windows"])
        assert set(windows) == {row.result.stimulus.fingerprint for row in calls}
