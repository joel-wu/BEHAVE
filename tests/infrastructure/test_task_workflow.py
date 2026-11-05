"""Exercise the shared application boundary, not a second solver loop."""

from dataclasses import replace

import pytest
from test_judge import ScriptedJudge
from test_solver_process import setup as solver_setup

from behave.agent.attempt import AttemptInterrupted
from behave.core.artifacts import read_verified
from behave.core.records import canonical_json, parse_json
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.result import TaskResult
from behave.task_execution.run_store import RunStore
from behave.task_execution.workflow import execute_task

SUBMIT = r'''
if request.turn == 0:
    calls = (ToolCall('b','run_command',{'command':"printf 'class M: pass\\n' > agent.py"}),
             ToolCall('r','run_command',{'command':"printf 'module top; endmodule\\n' > top.sv"}))
else:
    calls = (ToolCall('s','submit',{
        'behavior':{'files':['agent.py'],'entry_file':'agent.py','entry_symbol':'M'},
        'rtl':{'files':['top.sv'],'entry_file':'top.sv','entry_symbol':'top'},'testbench':None}),)
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('',calls,charge=Charge('0.01','USD','fixture'))))
'''
STOP = "sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('done')))"


def setup(tmp_path, publication, program=SUBMIT):
    policy = JudgePolicy("fixture-command", "fixture-authority", search_seconds=0, max_retries=0)
    publication.task = replace(publication.task, judge_policy=publication.put(policy.to_bytes()))
    publication.release, publication.ref = publication.publish()
    job, _, _ = solver_setup(tmp_path / "unused", publication, program)
    return job, RunStore(tmp_path / "run")


@pytest.mark.isolation
def test_solver_submission_judgment_and_cold_replay(tmp_path, publication, monkeypatch):
    job, run = setup(tmp_path, publication)
    judge = ScriptedJudge(ComponentJudgment(1, "match"), ComponentJudgment(0, "mismatch"))
    observed = []
    report = execute_task(reader=publication.reader, job=job, run=run, judge=judge,
                          observe_trajectory=lambda ref, view: observed.append((ref, view)))
    assert report.result.score == .5 and report.result.status == "scored"
    assert len(judge.requests) == 2 and report.result.submission is not None
    accounting = parse_json(read_verified(run.objects, report.result.accounting))
    assert accounting["cost_by_currency"] == {"USD": "0.02"}
    assert len(accounting["turns"]) == 2
    assert len(observed) == 1 and observed[0][0] == report.content
    assert len(observed[0][1].turns) == 2
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("settled task must not dispatch"))
    assert execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(),
                        cancelled=lambda: True,
                        observe_trajectory=lambda *a: pytest.fail("no fresh trajectory on cold replay")) == report
    assert TaskResult.from_bytes(report.result.to_bytes()) == report.result
    for key, value in (("score", 1), ("status", "no_submission"), ("score", True)):
        row = report.result.as_dict()
        row[key] = value
        with pytest.raises(ValueError):
            TaskResult.from_bytes(canonical_json(row))


@pytest.mark.isolation
def test_normal_no_submit_and_cancel_have_distinct_results(tmp_path, publication):
    job, run = setup(tmp_path, publication, STOP)
    normal = execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge())
    assert normal.result.score == 0 and normal.result.status == "no_submission"
    cancelled_run = RunStore(tmp_path / "cancelled")
    cancelled = execute_task(reader=publication.reader, job=job, run=cancelled_run,
                             judge=ScriptedJudge(), cancelled=lambda: True)
    assert cancelled.result.score is None and cancelled.result.failure_stage == "solver"
    assert cancelled.result.failure_code == "solver-worker-cancelled"
    assert not cancelled_run.journal("judge").load()
    assert execute_task(reader=publication.reader, job=job, run=cancelled_run, judge=ScriptedJudge()) == cancelled


@pytest.mark.isolation
def test_resume_after_solver_commit_does_not_resample(tmp_path, publication, monkeypatch):
    from behave.task_execution import workflow
    job, run = setup(tmp_path, publication)
    real = workflow.judge_submission
    class Crash(BaseException):
        pass
    monkeypatch.setattr(workflow, "judge_submission", lambda **kw: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge())
    assert run.journal("task").load()[-1].kind == "task_started"
    model_events = run.journal("solver").load()
    monkeypatch.setattr(workflow, "judge_submission", real)
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("must use committed submission"))
    report = execute_task(reader=publication.reader, job=job, run=run,
                          judge=ScriptedJudge(ComponentJudgment(1, "ok"), ComponentJudgment(1, "ok")))
    assert report.result.score == 1 and run.journal("solver").load() == model_events


@pytest.mark.isolation
def test_unknown_judge_is_not_zero_or_permission_to_restart_solver(tmp_path, publication):
    job, run = setup(tmp_path, publication)
    class Crash(BaseException):
        pass
    with pytest.raises(Crash):
        execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(Crash()))
    events = run.journal("solver").load()
    result = execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge())
    assert result.result.failure_stage == "judge" and result.result.score is None
    assert result.result.submission is not None
    assert run.journal("solver").load() == events


def test_owner_lock_is_fail_fast_and_released_without_unlink(tmp_path):
    run = RunStore(tmp_path / "run")
    with run.owner():
        inode = (run.directory / "owner.lock").stat().st_ino
        with pytest.raises(AttemptInterrupted, match="run-already-owned"), RunStore(run.directory).owner():
            pytest.fail("two owners entered")
    with run.owner():
        assert (run.directory / "owner.lock").stat().st_ino == inode


@pytest.mark.isolation
@pytest.mark.parametrize("full_baseline", [False, True])
@pytest.mark.parametrize("setting", ["joint", "rtl_only"])
def test_complete_workflow_uses_real_behavior_and_verilator_judge(tmp_path, publication, full_baseline, setting):
    from test_judge import BEHAVIOR, RTL, real_task

    from behave.task_execution.simulator_judge import SimulatorJudge

    private, _, _ = real_task(tmp_path / "fixture")
    if full_baseline:
        from behave.sim import api
        from behave.task_execution.judge_policy import JudgePolicy

        # Use Sim's public baseline defaults (100 transactions), with
        # a domain large enough for them and backpressure. No caller stimuli.
        policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0, max_retries=0)
        assert policy.sequence_length == 100
        private = replace(private, judge_policy=policy.to_bytes(),
            run_policy=canonical_json(api.GenerationSpec(1024, 100, 3, 16, stall_every=3).as_dict()))
    # Publish an actual task release; the solver never receives its reference or
    # private generation policy. The judge is not ScriptedJudge in this test.
    for file in private.reference_behavior.files:
        publication.put(file.data)
    publication.task = replace(publication.task, contract=publication.file("contract.json", private.contract.data),
                               reference_behavior=private.reference_behavior.artifact, reference_rtl=None,
                               run_policy=publication.put(private.run_policy),
                               judge_policy=publication.put(private.judge_policy))
    publication.release, publication.ref = publication.publish()
    program = f'''
if request.turn == 0:
    from shlex import quote
    calls = (ToolCall('b','run_command',{{'command':'printf %s ' + quote({BEHAVIOR.decode()!r}) + ' > agent.py'}}),
             ToolCall('r','run_command',{{'command':'printf %s ' + quote({RTL.decode()!r}) + ' > top.sv'}}))
else:
    selections = {{
        'behavior':{{'files':['agent.py'],'entry_file':'agent.py','entry_symbol':'M'}},
        'rtl':{{'files':['top.sv'],'entry_file':'top.sv','entry_symbol':'M'}},'testbench':None}}
    if {setting!r} == 'rtl_only':
        selections = {{'rtl': selections['rtl']}}
    calls = (ToolCall('s','submit',selections),)
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('', calls)))
'''
    job, _, _ = solver_setup(tmp_path / "solver", publication, program)
    job = replace(job, setting=setting)
    run = RunStore(tmp_path / "real-run")
    report = execute_task(reader=publication.reader, job=job, run=run, judge=SimulatorJudge())
    assert report.result.score == 1, report.result.as_dict()
    judgment = report.result.judgment
    components = (judgment.rtl,) if setting == "rtl_only" else (judgment.behavior, judgment.rtl)
    assert (judgment.behavior is None) == (setting == "rtl_only")
    assert all(component.evidence for component in components)
    if full_baseline:
        for component in components:
            evidence = parse_json(run.objects.read(component.evidence[0]))
            evaluation = evidence["evaluation"]
            assert evaluation["results"]
            baseline = evidence["request"]["baseline"]
            assert baseline["policy"]["max_queue_depth"] == 100
            stimuli = tuple(api.Stimulus.from_dict(row) for row in baseline["stimuli"])
            assert all(len(queue) == 100 for stimulus in stimuli for _, queue in stimulus.queues)
            assert {stimulus.fingerprint for stimulus in stimuli} <= {
                row["stimulus"] for row in evaluation["results"]}
            # Sim may additionally replay a continuous composition of baseline
            # members. Its total accepted count is not length * all run count.
            assert evaluation["accepted"] >= 100 * len(stimuli), evaluation
    assert run.journal("task").load()[-1].kind == "task_finished"
