"""Eval migration keeps completed submissions AND recorded failures unchanged."""
import json
from dataclasses import replace

import pytest
from test_evaluation import plan_for
from test_rejudge import local_rollout  # noqa: F401
from test_task_workflow import setup

from behave.agent.admission import AdmissionPolicy
from behave.core.artifacts import BlobRef
from behave.eval.generation import recover_jobs, recovered_model_admission, semantic_job
from behave.task_execution.batch import load_batch_report, run_batch
from behave.task_execution.run_store import RunStore


def test_incomplete_frozen_runtime_fails_before_model_start(monkeypatch):
    from behave.eval.generation import validate_recovery_runtime
    from behave.task_execution.batch_plan import BatchPlan
    validate_recovery_runtime()
    monkeypatch.setattr(BatchPlan, '__dataclass_fields__', {
        k: v for k, v in BatchPlan.__dataclass_fields__.items() if k != 'recorded_admissions'})
    with pytest.raises(RuntimeError, match='snapshot lacks'):
        validate_recovery_runtime()


def test_generation_handoff_keeps_pending_and_no_submission(tmp_path, publication, local_rollout, monkeypatch):
    source = tmp_path/'old'
    base, _ = setup(tmp_path/'fixture', publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ('submitted', 'no_submission'))
    plan = replace(plan_for(*jobs), score_on_submit=False)
    original = run_batch(plan=plan, reader=publication.reader, directory=source/'run')
    calls = list(local_rollout)
    fence = tmp_path/'fence.json'
    fence.write_text(json.dumps({'old_workers_stopped':True,'sources':[str(source.resolve())]}))
    descriptor = {'schema':'behave.eval-generation-recovery/1','source':str(source),'fence':str(fence)}
    monkeypatch.setattr('behave.eval.generation.semantic_job', lambda job: job.to_bytes())
    with monkeypatch.context() as recovery_only:
        recovery_only.setattr('behave.task_execution.accounting.trajectory_of',
                              lambda *args: pytest.fail('recovery reconstructed conversation history'))
        restored, recovery = recover_jobs(jobs,descriptor,score_on_submit=False)
    assert recovery.indices == {0,1}
    admission = recovered_model_admission(AdmissionPolicy('eval-review-model', 6), recovery)
    result = run_batch(plan=replace(plan,jobs=restored,workers=6,model_admission=admission),reader=publication.reader,
                       directory=tmp_path/'new',recovery=recovery)
    assert local_rollout == calls
    assert result.summary['pending_judgment'] == original.summary['pending_judgment'] == 1
    assert result.summary['no_submission'] == original.summary['no_submission'] == 1
    old_events = RunStore(source/'run').journal('report').load(verify_artifacts=False)
    new_events = RunStore(tmp_path/'new').journal('report').load(verify_artifacts=False)
    assert {e.payload['index']: e.payload['row'] for e in old_events if e.kind=='evaluation_case'} == {
        e.payload['index']: e.payload['row'] for e in new_events if e.kind=='evaluation_case'}
    # The downstream scorer uses the strict reader, not summarize_recorded.
    new_run = RunStore(tmp_path/'new')
    assert load_batch_report(new_run.objects, result.head).summary == result.summary
    from test_rejudge import Judge

    from behave.task_execution.rejudge import rejudge_batch
    judged = rejudge_batch(directory=new_run.directory,name='baseline',reader=publication.reader,
                          judge=Judge(unavailable=False),workers=2,all_submitted=True)
    assert judged.summary.get('pending_judgment', 0) == 0
    assert judged.summary['scored'] == 2
    assert local_rollout == calls
    with pytest.raises(ValueError,match='separated generation'):
        recover_jobs(jobs,descriptor,score_on_submit=True)
    fence.write_text(json.dumps({'old_workers_stopped':False,'sources':[str(source.resolve())]}))
    with pytest.raises(ValueError,match='fencing'):
        recover_jobs(jobs,descriptor,score_on_submit=False)


def test_recovery_preserves_admission_identity_across_worker_redistribution():
    from types import SimpleNamespace
    old = AdmissionPolicy('eval-review-model', 5)
    configured = AdmissionPolicy('eval-review-model', 6)
    recovery = SimpleNamespace(plan=SimpleNamespace(model_admission=old))
    assert old.identity != configured.identity
    assert recovered_model_admission(configured, recovery) is old
    assert recovered_model_admission(configured, None) is configured


@pytest.mark.parametrize('invalid', [None, 1, 'true', []])
def test_cancellation_retry_requires_an_explicit_boolean(invalid):
    descriptor = {'schema': 'behave.eval-generation-recovery/1',
                  'retry_solver_cancellations': invalid}
    with pytest.raises(ValueError, match='must be a boolean'):
        recover_jobs((), descriptor, score_on_submit=False)


def test_cancellation_retry_requires_a_pinned_head():
    descriptor = {'schema': 'behave.eval-generation-recovery/1',
                  'retry_solver_cancellations': True}
    with pytest.raises(ValueError, match='pinned recovery head'):
        recover_jobs((), descriptor, score_on_submit=False)


def test_explicit_recovery_retries_only_fenced_solver_cancellations(
        tmp_path, publication, local_rollout, monkeypatch):
    from behave.agent.process_worker import WorkerChannels, settled_attempt
    from behave.task_execution import workflow

    base, _ = setup(tmp_path/'fixture', publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ('submitted', 'no_submission', 'cancelled', 'transport-error'))
    plan = replace(plan_for(*jobs), score_on_submit=False, workers=1)
    execute = workflow.run_solver_process

    def interrupted(job, **kwargs):
        code = {'cancelled': 'solver-worker-cancelled',
                'transport-error': 'model-response-invalid'}.get(job.attempt.attempt_id)
        if code:
            journal, supervision = kwargs['journal'], kwargs['supervision']
            reference = journal.objects.put(job.to_bytes())
            refs = tuple(journal.objects.put(file.data) for file in job.task.files)
            channels = WorkerChannels(journal, supervision)
            return settled_attempt(namespace='solver', spec=job.attempt, journal=journal,
                supervision=supervision, payload=channels.payload(reference),
                dependencies=(reference, *refs), launch=lambda: ({
                    'schema': 'behave.solver-settlement/1', 'cleanup': 'complete',
                    'status': 'cancelled', 'returncode': -15, 'elapsed_seconds': 0.,
                    'interruption': code, 'supervision_errors': []}, ()))
        return execute(job, **kwargs)

    monkeypatch.setattr(workflow, 'run_solver_process', interrupted)
    source = tmp_path/'old'
    original = run_batch(plan=plan, reader=publication.reader, directory=source/'run')
    assert original.summary['no_result'] == 2
    origin = RunStore(source/'run')
    old_events = origin.journal('report').load()
    old_bytes = tuple(event.to_bytes() for event in old_events)
    fence = tmp_path/'fence.json'
    fence.write_text(json.dumps({'old_workers_stopped': True,
                                'sources': [str(source.resolve())]}))
    descriptor = {'schema': 'behave.eval-generation-recovery/1', 'source': str(source),
                  'fence': str(fence), 'head': original.head.as_dict()}
    monkeypatch.setattr('behave.eval.generation.semantic_job', lambda job: job.to_bytes())
    _, unchanged = recover_jobs(jobs, descriptor, score_on_submit=False)
    assert unchanged.indices == {0, 1, 2, 3}
    descriptor['retry_solver_cancellations'] = True
    restored, recovery = recover_jobs(jobs, descriptor, score_on_submit=False)
    assert recovery.indices == {0, 1, 3}
    monkeypatch.setattr(workflow, 'run_solver_process', execute)
    before = list(local_rollout)
    target = RunStore(tmp_path/'new')
    result = run_batch(plan=replace(recovery.plan, jobs=restored), reader=publication.reader,
                       directory=target.directory, recovery=recovery)
    assert local_rollout == before + ['cancelled']
    assert result.summary['pending_judgment'] == 2
    assert result.summary['no_submission'] == 1
    assert result.summary['no_result'] == 1
    prior = {e.payload['index']: e.payload['row'] for e in old_events if e.kind == 'evaluation_case'}
    current = {e.payload['index']: e.payload['row'] for e in target.journal('report').load()
               if e.kind == 'evaluation_case'}
    assert all(current[index] == prior[index] for index in recovery.indices)
    assert tuple(e.to_bytes() for e in origin.journal('report').load()) == old_bytes
    assert load_batch_report(target.objects, result.head).summary == result.summary
    fence.write_text(json.dumps({'old_workers_stopped': False,
                                'sources': [str(source.resolve())]}))
    with pytest.raises(ValueError, match='fencing'):
        recover_jobs(jobs, descriptor, score_on_submit=False)


def test_mixed_recorded_admissions_and_pinned_head(tmp_path, publication, local_rollout, monkeypatch):
    from test_rejudge import Judge

    from behave.agent.replay import Tape
    from behave.core.records import RecordError, thaw_json
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.rejudge import rejudge_batch
    from tools.eval_score_stream import committed_cases, consolidate, publish_subset

    base, _ = setup(tmp_path/'fixture', publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ('submitted', 'no_submission'))
    plan = replace(plan_for(*jobs), score_on_submit=False)
    original = RunStore(tmp_path/'original')
    run_batch(plan=plan, reader=publication.reader, directory=original.directory)
    events = original.journal('report').load()
    cases = [event for event in events if event.kind == 'evaluation_case']
    mixed = tmp_path/'mixed'
    (mixed/'inputs').mkdir(parents=True)
    source = RunStore(mixed/'run')
    changed = replace(plan, model_admission=AdmissionPolicy('eval-review-model', 6))
    with source.owner():
        refs = tuple(dict.fromkeys(ref for e in events for ref in e.artifacts))
        source.objects.publish_owned(original.objects, refs)
        ref = source.objects.put(changed.to_bytes())
        tape = Tape(source.journal('report'))
        tape.record('evaluation_started', {'plan': ref.as_dict()}, (ref, *refs))
        tape.record('evaluation_case', thaw_json(cases[0].payload), cases[0].artifacts)
        head = BlobRef.of(source.journal('report').load()[-1].to_bytes())
        tape.record('evaluation_case', thaw_json(cases[1].payload), cases[1].artifacts)
    fence = tmp_path/'fence.json'
    fence.write_text(json.dumps({'old_workers_stopped':True,'sources':[str(mixed.resolve())]}))
    descriptor = {'schema':'behave.eval-generation-recovery/1','source':str(mixed),
                  'fence':str(fence),'head':head.as_dict()}
    monkeypatch.setattr('behave.eval.generation.semantic_job', lambda job: job.to_bytes())
    _, prefix = recover_jobs(jobs, descriptor, score_on_submit=False)
    assert prefix.indices == {cases[0].payload['index']}
    descriptor.pop('head')
    restored, recovery = recover_jobs(jobs, descriptor, score_on_submit=False)
    assert len(recovery.plan.recorded_admissions) == 2
    assert all(identity is None for _, identity in recovery.plan.recorded_admissions)
    assert 'recorded_admissions' not in json.loads(plan.to_bytes())
    assert BatchPlan.from_bytes(recovery.plan.to_bytes(), source.objects) == recovery.plan
    calls = list(local_rollout)
    target = tmp_path/'recovered'
    (target/'inputs').mkdir(parents=True)
    result = run_batch(plan=replace(recovery.plan, jobs=restored), reader=publication.reader,
                       directory=target/'run', recovery=recovery)
    run = RunStore(target/'run')
    assert load_batch_report(run.objects, result.head).summary == result.summary
    chunks = []
    for index, event in enumerate(committed_cases(target)):
        chunk = publish_subset(target, tmp_path/f'chunk-{index}', [event])
        rejudge_batch(directory=chunk/'run', name='baseline', reader=publication.reader,
                      judge=Judge(unavailable=False), workers=1, all_submitted=True)
        chunks.append(chunk)
    consolidate(target, chunks, target.parent/'scoring/stream')
    judged = run.directory/'judgments/stream'
    last = RunStore(judged, objects=run.objects).journal('report').load()[-1]
    assert load_batch_report(run.objects, BlobRef.of(last.to_bytes())).summary['scored'] == 2
    assert local_rollout == calls
    with pytest.raises(RecordError, match='imported or replayed'):
        run_batch(plan=recovery.plan, reader=publication.reader, directory=tmp_path/'invalid')
    with pytest.raises(ValueError, match='duplicate recorded'):
        replace(recovery.plan, recorded_admissions=recovery.plan.recorded_admissions * 2)
    with pytest.raises(ValueError, match='absent job'):
        replace(recovery.plan, jobs=(jobs[0],))


def test_semantics_ignore_only_ephemeral_transport_addresses():
    from copy import deepcopy
    from types import SimpleNamespace
    row={'model':{'revision':'behave.local-chat/8','argv':['python','worker','/tmp/a/code/src',json.dumps({
        'url':'http://127.0.0.1:24000','worker_urls':['http://127.0.0.1:24000'],
        'parameters':{'temperature':1}})]},'attempt':{'model_policy':{'provider':'old','transport':'old'},'max_turns':60}}
    encode=lambda r:SimpleNamespace(to_bytes=lambda:json.dumps(r).encode())
    other=deepcopy(row);other['model']['argv'][-2]='/tmp/b/code/src'
    endpoint=json.loads(other['model']['argv'][-1]);endpoint['url']='http://127.0.0.1:25000';endpoint['worker_urls']=[endpoint['url']]
    other['model']['argv'][-1]=json.dumps(endpoint)
    other['attempt']['model_policy']={'provider':'new','transport':'new'}
    assert semantic_job(encode(row))==semantic_job(encode(other))
    endpoint['parameters']['temperature']=0.6;other['model']['argv'][-1]=json.dumps(endpoint)
    assert semantic_job(encode(row))!=semantic_job(encode(other))
