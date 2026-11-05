"""Restart barriers must inspect skipped candidates before admitting new work."""

from dataclasses import replace
from threading import Event

import pytest
from test_evaluation import Judge, plan_for
from test_task_workflow import setup

from behave.agent.attempt import AttemptInterrupted
from behave.agent.replay import Tape
from behave.core.artifacts import BlobRef, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError
from behave.task_execution import batch as batch_module
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.recovery import task_cleanup
from behave.task_execution.run_store import RunStore
from behave.task_execution.tb_report import TBObservation


@pytest.fixture(params=["local-owner-fixture", pytest.param("isolated-worker", marks=pytest.mark.isolation)])
def worker_owner(request, monkeypatch):
    """Run the same recovery cases with and without a production namespace worker.

    The local lane substitutes only worker launch/cleanup; journals, solver
    loop, submission, judging, batch recovery and accounting remain real code.
    It is not an isolation or model-service acceptance test.
    """
    if request.param == "local-owner-fixture":
        from test_execution_lifecycle import commit

        from behave.task_execution import solver as solver_module
        from behave.task_execution.solver import SolverJob

        def launch(*, attempt, model, journal, reference, ingresses, **kwargs):
            assert ingresses == (None, None)
            job = SolverJob.from_bytes(read_verified(journal.objects, reference), journal.objects)
            assert (job.attempt, job.model) == (attempt, model)
            commit(job, RunStore(journal.directory.parent))
            return supervision_rows(CleanupState.COMPLETE)[1][1], ()

        monkeypatch.setattr(solver_module, "supervise_worker", launch)


@pytest.mark.parametrize("state", list(CleanupState))
@pytest.mark.parametrize("case_committed", [False, True])
def test_cold_batch_does_not_forget_completed_candidate_cleanup(
        tmp_path, publication, monkeypatch, worker_owner, state, case_committed):
    job, _ = setup(tmp_path, publication)
    second = replace(job, attempt=replace(job.attempt, attempt_id="second"))
    plan = replace(plan_for(job, second), workers=1)
    run = RunStore(tmp_path / "batch")
    committed = Event()
    execute, record = batch_module.execute_task, Tape.record

    class Crash(BaseException):
        pass

    class UnknownJudge(Judge):
        def evaluate(self, request, objects):
            self.requests.append(request)
            return ComponentJudgment(1, "match", cleanup=state)

    def first_only(**kwargs):
        if kwargs["job"] == second:
            assert committed.wait(10)
            raise Crash()
        return execute(**kwargs)

    def observe_case(self, kind, *args, **kwargs):
        if kind == "evaluation_case" and not case_committed:
            committed.set()
            raise Crash()
        event = record(self, kind, *args, **kwargs)
        if kind == "evaluation_case":
            committed.set()
        return event

    judge = UnknownJudge()
    monkeypatch.setattr(batch_module, "execute_task", first_only)
    monkeypatch.setattr(Tape, "record", observe_case)
    with pytest.raises(Crash):
        batch_module.run_batch(plan=plan, reader=publication.reader, directory=run.directory, judge=judge)
    history = run.journal("report").load()
    assert [e.kind for e in history] == ["evaluation_started"] + (["evaluation_case"] if case_committed else [])
    assert len(judge.requests) == (2 if state.quiescent else 1)
    monkeypatch.setattr(Tape, "record", record)
    if state.quiescent:
        first = RunStore(run.directory / "candidates" / BlobRef.of(job.to_bytes()).sha256)
        previous_solver = first.journal("solver").load()
        new_judge = Judge()
        monkeypatch.setattr(batch_module, "execute_task", execute)
        report = batch_module.run_batch(plan=plan, reader=publication.reader, directory=run.directory, judge=new_judge)
        assert report.summary["scored"] == 2
        assert first.journal("solver").load() == previous_solver
        assert len(new_judge.requests) == 2  # Only the second candidate is judged.
    else:
        monkeypatch.setattr(batch_module, "execute_task", lambda **kw: pytest.fail("cold restart admitted another candidate"))
        with pytest.raises(RecordError) as caught:
            batch_module.run_batch(plan=plan, reader=publication.reader, directory=run.directory, judge=Judge())
        assert caught.value.code == "batch-recovery-cleanup-" + state.value
        assert run.journal("report").load() == history


def write_phase(run, phase, rows):
    tape = Tape(run.journal(phase))
    for kind, payload in rows:
        tape.record(kind, payload)
    return tape.events


def service_rows(phase, states=(), *, pending=False, decided=False):
    prefix = "tb" if phase == "testbench" else "judge"
    rows = [(prefix + "_started", {})]
    for state in states:
        result = (TBObservation("unavailable", "fixture", cleanup=state) if prefix == "tb"
                  else ComponentJudgment(None, "fixture", "infrastructure", cleanup=state))
        rows.extend([(prefix + "_requested", {}), (prefix + "_settled", {"result": result.as_dict()})])
    if pending:
        rows.append((prefix + "_requested", {}))
    if decided:
        rows.append(("judge_score_decided", {"result": ComponentJudgment(1, "match").as_dict()}))
    return rows


def supervision_rows(state):
    return [("solver_requested", {}), ("solver_settled", {
        "schema": "behave.solver-settlement/1", "cleanup": state.value, "status": "exited",
        "returncode": 0, "elapsed_seconds": 1., "interruption": None, "supervision_errors": []})]


@pytest.mark.parametrize("phase", ["judge", "testbench"])
@pytest.mark.parametrize("states,pending,expected", [
    ((), False, CleanupState.COMPLETE), ((), True, CleanupState.UNKNOWN),
    ((CleanupState.COMPLETE,), False, CleanupState.COMPLETE),
    ((CleanupState.COMPLETE,), True, CleanupState.UNKNOWN),
    ((CleanupState.UNKNOWN,), False, CleanupState.UNKNOWN),
    ((CleanupState.INCOMPLETE,), False, CleanupState.INCOMPLETE),
    ((CleanupState.UNKNOWN, CleanupState.COMPLETE), False, CleanupState.UNKNOWN),
    ((CleanupState.INCOMPLETE, CleanupState.COMPLETE), True, CleanupState.INCOMPLETE),
])
def test_phase_cleanup_is_monotone_and_pending_is_not_complete(tmp_path, phase, states, pending, expected):
    run = RunStore(tmp_path / "run")
    events = write_phase(run, phase, service_rows(phase, states, pending=pending))
    row = next(row for row in task_cleanup(run) if row.phase == phase)
    assert row.state is expected and row.head == BlobRef.of(events[-1].to_bytes())


@pytest.mark.parametrize("before", list(CleanupState))
@pytest.mark.parametrize("retained", list(CleanupState))
def test_retained_component_keeps_cleanup_evidence_without_a_new_dispatch(tmp_path, before, retained):
    run = RunStore(tmp_path / "run")
    result = ComponentJudgment(1, "previous-score", cleanup=retained)
    rows = service_rows("judge", (before,)) + [
        ("component_retained", {"component": "rtl", "result": result.as_dict()}),
        ("judgment_finished", {}),
    ]
    write_phase(run, "judge", rows)
    row = next(row for row in task_cleanup(run) if row.phase == "judge")
    assert row.state is CleanupState.combine(before, retained)


def test_retained_component_cannot_settle_an_active_dispatch(tmp_path):
    run = RunStore(tmp_path / "run")
    result = ComponentJudgment(1, "previous-score")
    write_phase(run, "judge", service_rows("judge", pending=True) + [
        ("component_retained", {"component": "rtl", "result": result.as_dict()}),
    ])
    with pytest.raises(RecordError) as caught:
        task_cleanup(run)
    assert caught.value.code == "recovery-history-invalid"


def test_early_score_and_role_terminal_do_not_attest_cleanup(tmp_path):
    run = RunStore(tmp_path / "run")
    write_phase(run, "supervision", [("solver_requested", {})])
    write_phase(run, "solver", [("terminal", {})])
    write_phase(run, "judge", service_rows("judge", pending=True, decided=True))
    write_phase(run, "task", [("task_finished", {})])
    assert [r.state for r in task_cleanup(run)] == [CleanupState.UNKNOWN, CleanupState.UNKNOWN, CleanupState.COMPLETE]


@pytest.mark.parametrize("state", list(CleanupState))
def test_solver_owner_receipt_covers_worker_and_relays(tmp_path, state):
    run = RunStore(tmp_path / "run")
    write_phase(run, "supervision", supervision_rows(state))
    # A interrupted provider request may have no outcome, but the enclosing
    # owner can still attest that the local worker/relay have been terminated.
    write_phase(run, "provider", [("model_requested", {})])
    assert task_cleanup(run)[0].state is state


@pytest.mark.parametrize("phase", ["solver", "provider", "development"])
def test_orphaned_child_is_unknown_not_a_fresh_run(tmp_path, phase):
    run = RunStore(tmp_path / "run")
    write_phase(run, phase, [("initialized", {})])
    assert task_cleanup(run)[0].state is CleanupState.UNKNOWN


@pytest.mark.parametrize("rows", [
    [("judge_settled", {})],
    [("judge_started", {}), ("judge_settled", {})],
    [("judge_started", {}), ("judge_requested", {}), ("judgment_finished", {})],
    [("judge_started", {}), ("judge_requested", {}), ("judge_requested", {})],
    [("judge_started", {}), ("judge_score_decided", {})],
    [("judge_started", {}), ("judgment_finished", {}), ("judge_requested", {})],
])
def test_invalid_owner_history_never_authorizes_recovery(tmp_path, rows):
    run = RunStore(tmp_path / "run")
    write_phase(run, "judge", rows)
    with pytest.raises(RecordError):
        task_cleanup(run)


@pytest.mark.parametrize("phase", ["supervision", "judge", "testbench"])
def test_pending_old_candidate_blocks_dispatch_before_any_pool_is_built(tmp_path, publication, monkeypatch, phase):
    job, _ = setup(tmp_path, publication)
    second = replace(job, attempt=replace(job.attempt, attempt_id="second"))
    root = RunStore(tmp_path / "batch")
    old = RunStore(root.directory / "candidates" / BlobRef.of(second.to_bytes()).sha256)
    write_phase(old, phase, [("solver_requested", {})] if phase == "supervision"
                else service_rows(phase, pending=True))
    # The blocked candidate is later in plan order; no earlier worker may race it.
    monkeypatch.setattr(batch_module, "shared_pools", lambda **kw: pytest.fail("pools created before recovery barrier"))
    with pytest.raises(RecordError) as caught:
        batch_module.run_batch(plan=plan_for(job, second), reader=publication.reader, directory=root.directory, judge=Judge())
    assert caught.value.code == "batch-recovery-cleanup-unknown"
    assert phase in str(caught.value)


def test_active_candidate_owner_blocks_cold_admission(tmp_path, publication, monkeypatch):
    job, _ = setup(tmp_path, publication)
    root = RunStore(tmp_path / "batch")
    old = RunStore(root.directory / "candidates" / BlobRef.of(job.to_bytes()).sha256)
    monkeypatch.setattr(batch_module, "shared_pools", lambda **kw: pytest.fail("must not overtake a live owner"))
    with old.owner(), pytest.raises(AttemptInterrupted, match="run-already-owned"):
        batch_module.run_batch(plan=plan_for(job), reader=publication.reader, directory=root.directory, judge=Judge())


def test_known_missing_candidate_history_is_not_a_fresh_resource(tmp_path, publication, monkeypatch):
    job, _ = setup(tmp_path, publication)
    second = replace(job, attempt=replace(job.attempt, attempt_id="second"))
    plan = plan_for(job, second)
    root = RunStore(tmp_path / "batch")
    reference = root.objects.put(plan.to_bytes())
    write_phase(root, "report", [
        ("evaluation_started", {"plan": reference.as_dict()}),
        ("evaluation_case", {"index": 0, "row": {
            "task_id": job.task.task_id, "attempt_id": job.attempt.attempt_id,
            "run_key": BlobRef.of(job.to_bytes()).sha256, "result": None, "score": None,
            "status": "no_result", "error": {"type": "OSError", "code": "storage-unavailable"},
            "testbench": None, "testbench_error": None}}),
    ])
    monkeypatch.setattr(batch_module, "shared_pools", lambda **kw: pytest.fail("history was lost, not reconciled"))
    with pytest.raises(RecordError) as caught:
        batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge())
    assert caught.value.code == "batch-recovery-history-missing"


def test_report_only_finalization_needs_no_new_admission_even_with_unknown_cleanup(
        tmp_path, publication, monkeypatch, worker_owner):
    job, _ = setup(tmp_path, publication)
    plan = plan_for(job)
    root = RunStore(tmp_path / "batch")
    record = Tape.record

    class Crash(BaseException):
        pass

    class UnknownJudge(Judge):
        def evaluate(self, request, objects):
            return ComponentJudgment(1, "match", cleanup=CleanupState.UNKNOWN)

    def interrupt_summary(self, kind, *args, **kwargs):
        if kind == "evaluation_finished":
            raise Crash()
        return record(self, kind, *args, **kwargs)

    monkeypatch.setattr(Tape, "record", interrupt_summary)
    with pytest.raises(Crash):
        batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory, judge=UnknownJudge())
    monkeypatch.setattr(Tape, "record", record)
    monkeypatch.setattr(batch_module, "shared_pools", lambda **kw: pytest.fail("finalizing reports must not create pools"))
    monkeypatch.setattr(batch_module, "require_task_quiescent", lambda *a, **kw: pytest.fail("offline report is not admission"))
    got = batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge())
    assert (got.summary["scored"], got.summary["no_result"]) == (0, 1)
    assert batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge()) == got
