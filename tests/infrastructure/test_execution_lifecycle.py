"""Committed results and execution quiescence are independent facts."""

import pytest
from test_attempts import Scripted, call, local_shell
from test_judge import ScriptedJudge
from test_task_workflow import setup

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.model_relay import ModelRelay
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError
from behave.isolation.service import ServiceResult
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.solver import run_solver
from behave.task_execution.workflow import execute_task


def commit(job, run):
    backend = Scripted(
        call('run_command', {'command': "printf 'class M: pass\\n' > agent.py"}, 'b'),
        call('run_command', {'command': "printf 'module top; endmodule\\n' > top.sv"}, 'r'),
        call('submit', {
                        'behavior': {'files': ['agent.py'], 'entry_file': 'agent.py', 'entry_symbol': 'M'},
                        'rtl': {'files': ['top.sv'], 'entry_file': 'top.sv', 'entry_symbol': 'top'},
                        'testbench': None}, 's'))
    return run_solver(task=job.task, spec=job.attempt, backend=backend, journal=run.journal('solver'), command_tool=local_shell())


@pytest.mark.parametrize('status,expected', [('exited', CleanupState.COMPLETE),
    ('cancelled', CleanupState.COMPLETE), ('timeout', CleanupState.COMPLETE),
    ('cleanup-incomplete', CleanupState.INCOMPLETE), ('future-status', CleanupState.UNKNOWN)])
def test_service_receipt_and_cleanup_are_distinct(status, expected):
    assert ServiceResult(status, None, b'', b'', 0).cleanup_state is expected
    assert CleanupState.combine(CleanupState.COMPLETE, expected) is expected
    if not expected.quiescent:
        with pytest.raises(RecordError):
            expected.require_quiescent('owner')
        with pytest.raises(ValueError):
            ComponentJudgment(None, 'any-code', 'infrastructure', retryable=True, cleanup=expected)


@pytest.mark.parametrize('state', [CleanupState.UNKNOWN, CleanupState.INCOMPLETE])
def test_releasing_capacity_never_clears_an_unfenced_service(state):
    from concurrent.futures import ThreadPoolExecutor
    pool = ResourcePool(AdmissionPolicy('shared-resource', 1))
    def queued():
        with pytest.raises(AttemptInterrupted) as error, pool.acquire():
            pytest.fail('quarantined resource must not dispatch')
        assert error.value.code == 'resource-cleanup-' + state.value
    with ThreadPoolExecutor(max_workers=1) as threads:
        with pool.acquire():
            future = threads.submit(queued)
            pool.quarantine(state)
            pool.quarantine(CleanupState.COMPLETE)  # Another clean owner cannot clear it.
        future.result(timeout=5)
    assert pool.counts == (0, 0)
    queued()


def test_worker_preparation_failure_does_not_claim_an_unconfirmed_live_process(tmp_path, publication, monkeypatch):
    from behave.core import paths
    from behave.isolation import service

    job, run = setup(tmp_path, publication)
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(local))

    def bad_path():
        raise OSError("PRIVATE_PATH_FAILURE")

    monkeypatch.setattr(paths, "eda_root", bad_path)
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("preparation must fail before launch"))
    judge = ScriptedJudge()
    report = execute_task(reader=publication.reader, job=job, run=run, judge=judge)
    assert report.result.score is None and report.result.failure_code == "solver-supervision-OSError"
    assert not judge.requests and not run.journal("solver").load()
    receipt = run.journal("supervision").load()[-1].payload
    assert receipt["cleanup"] == "complete" and receipt["status"] == "supervisor-failed"
    assert receipt["supervision_errors"] == ("solver-supervision-OSError",)
    assert not list(local.iterdir())
    assert execute_task(reader=publication.reader, job=job, run=run, judge=judge) == report


@pytest.mark.parametrize('failure', ['incomplete', 'exception'])
def test_committed_solver_without_cleanup_never_advances_or_relaunches(tmp_path, publication, monkeypatch, failure):
    from behave.isolation import service
    job, run = setup(tmp_path, publication)
    committed = []
    def execute(*args, **kwargs):
        committed.append(commit(job, run))
        if failure == 'exception':
            raise OSError('not a cleanup witness')
        return ServiceResult('cleanup-incomplete', None, b'', b'', .1)
    monkeypatch.setattr(service, 'run_service', execute)
    judge = ScriptedJudge()
    for _ in range(2):
        with pytest.raises(RecordError, match='not quiescent'):
            execute_task(reader=publication.reader, job=job, run=run, judge=judge)
        monkeypatch.setattr(service, 'run_service', lambda *a, **kw: pytest.fail('must not relaunch'))
    assert len(committed) == 1 and committed[0].submission is not None
    assert not judge.requests
    receipt = run.journal('supervision').load()[-1]
    assert receipt.kind == 'solver_settled'
    assert receipt.payload['cleanup'] == ('unknown' if failure == 'exception' else 'incomplete')
    assert run.journal('solver').load()[-1].kind == 'terminal'
    # The provider may still be writing: do not pretend this is final accounting.
    assert [e.kind for e in run.journal('task').load()] == ['task_started']


@pytest.mark.parametrize('state', list(CleanupState))
def test_failed_relay_entry_propagates_cleanup_to_shared_admission(tmp_path, publication, monkeypatch, state):
    from behave.isolation import service
    job, run = setup(tmp_path, publication)
    pool = ResourcePool(AdmissionPolicy('models', 1))
    def fail_enter(self):
        # Inject the lifetime receipt: no actual service is started by this fixture.
        self.cleanup_state = state
        raise OSError('relay start failed')
    monkeypatch.setattr(ModelRelay, '__enter__', fail_enter)
    monkeypatch.setattr(service, 'run_service', lambda *a, **kw: pytest.fail('worker must not start'))
    def execute():
        return execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(), model_pool=pool)
    if state.quiescent:
        report = execute()
        assert report.result.score is None and report.result.failure_code == 'solver-supervision-OSError'
    else:
        with pytest.raises(RecordError, match='not quiescent'):
            execute()
    assert run.journal('supervision').load()[-1].payload['cleanup'] == state.value
    if state.quiescent:
        with pool.acquire():
            pass
    else:
        with pytest.raises(AttemptInterrupted) as error, pool.acquire():
            pytest.fail('failed entry without cleanup must quarantine shared capacity')
        assert error.value.code == 'resource-cleanup-' + state.value


@pytest.mark.parametrize('stage', ['judge', 'tb'])
@pytest.mark.parametrize('lose_settlement', [False, True])
def test_backend_interruption_is_not_cleanup_even_when_settlement_fails(tmp_path, monkeypatch, stage, lose_settlement):
    from test_testbench_reports import POLICY, ReportBackend, inputs

    from behave.agent.replay import Tape
    from behave.task_execution.judge import judge_submission
    from behave.task_execution.testbench import observe_testbench
    task, public, submission, judge_journal, tb_journal = inputs(tmp_path)
    pool = ResourcePool(AdmissionPolicy('verification', 1))
    backend = (ScriptedJudge if stage == 'judge' else ReportBackend)(AttemptInterrupted('backend-interrupted'))
    journal = judge_journal if stage == 'judge' else tb_journal
    def execute():
        if stage == 'judge':
            return judge_submission(task=task, submission=submission, journal=journal, backend=backend, admission=pool)
        return observe_testbench(task=public, submission=submission, policy=POLICY,
                                 journal=journal, backend=backend, admission=pool)
    if lose_settlement:
        original = Tape.record
        def fail_record(self, kind, *args, **kwargs):
            if kind == stage + '_settled':
                raise OSError('injected journal outage')
            return original(self, kind, *args, **kwargs)
        monkeypatch.setattr(Tape, 'record', fail_record)
        with pytest.raises(OSError, match='journal outage'):
            execute()
        assert journal.load()[-1].kind == stage + '_requested'
    else:
        report = execute()
        result = report.behavior if stage == 'judge' else report.observation
        assert result.cleanup is CleanupState.UNKNOWN and not result.retryable
        assert result.code == 'backend-interrupted'
        assert execute() == report  # A cold read never dispatches again.
    assert len(backend.requests) == 1
    with pytest.raises(AttemptInterrupted) as error, pool.acquire():
        pytest.fail('unconfirmed backend cleanup must block shared capacity')
    assert error.value.code == 'resource-cleanup-unknown'
    assert pool.counts == (0, 0)


@pytest.mark.isolation
def test_quiescent_relay_exit_failure_preserves_submission_and_settlement(tmp_path, publication, monkeypatch):
    from behave.isolation import service
    job, run = setup(tmp_path, publication)
    pool = ResourcePool(AdmissionPolicy('models', 1))
    def execute(*args, **kwargs):
        commit(job, run)
        return ServiceResult('exited', 0, b'', b'', .1)
    original = ModelRelay.__exit__
    def exit_with_error(self, *args):
        original(self, *args)  # Real listener/thread cleanup, then a late error.
        raise AttemptInterrupted('model-relay-OSError')
    monkeypatch.setattr(service, 'run_service', execute)
    monkeypatch.setattr(ModelRelay, '__exit__', exit_with_error)
    judge = ScriptedJudge(ComponentJudgment(1, 'fixture'), ComponentJudgment(1, 'fixture'))
    report = execute_task(reader=publication.reader, job=job, run=run, judge=judge, model_pool=pool)
    assert report.result.score == 1 and report.result.submission is not None
    settled = run.journal('supervision').load()[-1]
    assert settled.kind == 'solver_settled' and settled.payload['cleanup'] == 'complete'
    assert settled.payload['supervision_errors'] == ('model-relay-OSError',)
    monkeypatch.setattr(service, 'run_service', lambda *a, **kw: pytest.fail('must not resample'))
    assert execute_task(reader=publication.reader, job=job, run=run, judge=judge, model_pool=pool) == report


@pytest.mark.parametrize('unsafe_first', [False, True])
def test_judge_and_tb_consume_cleanup_not_error_spelling(tmp_path, publication, monkeypatch, unsafe_first):
    from test_testbench_reports import POLICY, ReportBackend

    from behave.eval.plan import EvaluationPlan
    from behave.eval.runner import run_evaluation
    from behave.isolation import service
    job, _ = setup(tmp_path, publication)
    def execute(*args, **kwargs):
        # The ordinary workflow owns this run; recover its paths from the launch.
        from pathlib import Path

        from behave.task_execution.run_store import RunStore
        run = RunStore(Path(args[0][6]).parent)
        commit(job, run)
        return ServiceResult('exited', 0, b'', b'', .1)
    monkeypatch.setattr(service, 'run_service', execute)
    unsafe = ComponentJudgment(1, 'score', cleanup=CleanupState.UNKNOWN)
    judge = ScriptedJudge(*((unsafe,) if unsafe_first else (ComponentJudgment(1, 'score'), unsafe)))
    plan = EvaluationPlan('cleanup-eval', (job,), judge.identity, 1,
                          AdmissionPolicy('verification', 1), testbench=POLICY)
    report = run_evaluation(plan=plan, reader=publication.reader, directory=tmp_path / 'batch',
                            judge=judge, testbench=ReportBackend())
    assert len(judge.requests) == (1 if unsafe_first else 2)
    row = report.summary['candidates'][0]
    assert row['score'] == (None if unsafe_first else 1)
    assert row['testbench'] is None
    assert row['testbench_error']['code'] == 'judge-cleanup-unknown'


@pytest.mark.isolation
def test_relay_thread_exit_does_not_certify_its_provider_child(tmp_path, monkeypatch):
    from test_model_relay import make
    from test_process_model import command

    from behave.agent.model import ModelFailure
    from behave.agent.process_model import ProcessModelBackend
    from behave.core.records import canonical_json
    relay, request, _ = make(tmp_path, command('pass'), ResourcePool(AdmissionPolicy('models', 1)))
    def incomplete(self, request):
        self.cleanup_state = CleanupState.INCOMPLETE
        return ModelFailure('unknown', 'model-worker-cleanup-incomplete')
    monkeypatch.setattr(ProcessModelBackend, 'generate', incomplete)
    with pytest.raises(RecordError, match='not quiescent'), relay:
        relay.respond(canonical_json(request.as_dict()), cancelled=lambda: False)
        assert relay.child_cleanup is CleanupState.INCOMPLETE
        with pytest.raises(RecordError):
            relay.respond(canonical_json(request.as_dict()), cancelled=lambda: False)
    assert not relay._thread.is_alive() and relay.cleanup_state is CleanupState.INCOMPLETE
    relay._scratch.cleanup()  # Fault injection created no provider child.
