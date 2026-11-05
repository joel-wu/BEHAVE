"""Cooling tasks remain a fallback, and saved candidates are never resampled."""
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from test_slime_rollout_handoff import candidate
from test_slime_task_pool import source_at
from test_slime_training_plan import inputs
from test_training_sampling import pool_plan
from test_training_task_pool import tasks

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore
from behave.task_execution.batch import BatchRecovery
from behave.task_execution.result import TaskReport
from behave.task_execution.simulator_judge import SimulatorJudge
from behave.train.pool import (
    SamplingPolicy,
    TaskPoolPolicy,
    empty_state,
    extended_selection,
    extension_for,
    inspect_extension,
    select_pool,
    sync_state,
)
from behave.train.rollouts import run_rollouts
from behave.train.slime_rollout import round_plan, validate_execution_change
from behave.train.training_data import NativeChatVerifier


def test_soft_cooldown_keeps_history_and_orders_only_the_reserve_tail(tmp_path, publication):
    pool = tasks(pool_plan(tmp_path, publication))
    state = sync_state(empty_state(), pool)
    for task, until in zip('cde', (7, 6, 7)):
        state['tasks'][task].update(cooldown_until=until, category='mastered')
    before = deepcopy(state)
    sampler = SamplingPolicy(3, 'shuffle', '17')
    legacy = select_pool(pool, state, sampler, 3, include_reserve=True)
    policy = TaskPoolPolicy(10, 2, cooldown_fallback=True)
    full = select_pool(pool, state, sampler, 3, policy=policy, include_reserve=True)
    assert full[:2] == legacy
    assert full[2] == {'task_id': 'd', 'reason': 'cooldown-fallback'}
    assert {r['task_id'] for r in full} == set('abcde')
    assert len(full) == 5 and state == before
    assert select_pool(pool, state, sampler, 3, policy=policy) == full[:3]
    assert select_pool(pool, json.loads(json.dumps(state)), sampler, 3,
                       policy=policy, include_reserve=True) == full
    assert TaskPoolPolicy.from_dict(policy.as_dict()) == policy
    with pytest.raises(ValueError, match='boolean'):
        TaskPoolPolicy(10, 2, cooldown_fallback=1)


def test_opt_in_does_not_allow_other_sampling_changes(inputs):
    old = {**inputs[0], 'curriculum': TaskPoolPolicy(10, 2).as_dict()}
    new = {**old, 'curriculum': TaskPoolPolicy(10, 2, cooldown_fallback=True).as_dict()}
    assert validate_execution_change(old, new)['cooldown_fallback_enabled'] is True
    with pytest.raises(ValueError, match='training/sampling'):
        validate_execution_change(old, {**new, 'samples_per_task': 99})


def test_appended_reserve_does_not_leak_into_later_updates():
    selection = [{'task_id': 'a', 'reason': 'rotation'}]
    extension = [{'task_id': 'b', 'reason': 'cooldown-fallback'}]
    config = {'curriculum': TaskPoolPolicy(10, 2, cooldown_fallback=True).as_dict()}
    binding = {**config, 'rollout_recovery': {
        'round': 50, 'task_selection_extension': extension}}
    assert extended_selection(binding, 50, selection) == selection + extension
    assert extended_selection(binding, 51, selection) == selection
    info = {'binding': binding, 'step': 49, 'scored_receipt': {'saved': True}}
    assert inspect_extension(info, config) == extension
    assert inspect_extension({**info, 'step': 50}, config) is None


def test_append_recovery_reuses_completed_groups_and_only_dispatches_new_tasks(tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path/'source', publication, inputs, tasks='abcde', replenishment={'max_groups': None})
    binding = {**source.binding, 'update_groups': 2}
    reader = DirectoryStore(binding['objects'])
    endpoint = LocalChatEndpoint.from_dict(binding['endpoint'])
    selection = [{'task_id': task, 'reason': 'rotation'} for task in 'ab']
    policy = {'schema': 'behave.slime-policy/1', 'round': 0, 'task_selection': selection}
    ref = reader.put(canonical_json(policy))
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=ref))
    old = round_plan(binding, reader, round_id=0, endpoint=endpoint, policy=ref, task_selection=selection)
    run = JournalDirectory(tmp_path/'old')
    plan_ref = run.objects.put(old.execution.to_bytes())
    events = [run.journal('report').append((), 'evaluation_started', {'plan': plan_ref.as_dict()}, (plan_ref,))]
    for job in old.execution.jobs:
        for data in job.input_blobs():
            run.objects.put(data)
    for i, job in enumerate(old.execution.jobs):
        _, _, row, closure = candidate(job, run.objects, old.requirements, 1 if i < 3 else i)
        events.append(run.journal('report').append(events, 'evaluation_case', {'index': i, 'row': row}, closure))
    recovered = BatchRecovery.from_events(run.objects, events)
    recovered.publish(reader)
    old_ref = reader.put(old.to_bytes())
    pool = source._pool_tasks
    state = sync_state(empty_state(), pool)
    for task in 'cde':
        state['tasks'][task].update(cooldown_until=5, category='mastered')
    curriculum = TaskPoolPolicy(10, 2, cooldown_fallback=True)
    extension = extension_for(binding, policy, old, state, reader, curriculum)
    record = {'round': 0, 'plan': old_ref.as_dict(), 'policy': ref.as_dict(),
        'completed_indices': sorted(recovered.indices), 'task_selection_extension': extension}
    new_binding = {**binding, 'curriculum': curriculum.as_dict(), 'rollout_recovery': record}
    fresh = round_plan(new_binding, reader, round_id=0, endpoint=endpoint, policy=ref, task_selection=selection)
    assert fresh.execution.jobs[:6] == old.execution.jobs
    assert len(fresh.execution.jobs) == 15
    assert len(set(fresh.group_ids)) == 5
    with pytest.raises(RecordError, match='slots'):
        recovered.validate_plan(fresh.execution)
    recovered = replace(recovered, allow_append=True)
    recovered.validate_plan(fresh.execution)
    calls = []
    def execute(**kwargs):
        job, child = kwargs['job'], kwargs['run']
        index = fresh.execution.jobs.index(job)
        calls.append(index)
        result, result_ref, _, closure = candidate(job, child.objects, fresh.requirements, index)
        event = child.journal('task').append((), 'fixture', {'result': result_ref.as_dict()}, closure)
        return TaskReport(result, result_ref, BlobRef.of(event.to_bytes()))
    monkeypatch.setattr('behave.task_execution.batch.execute_task', execute)
    monkeypatch.setattr('behave.task_execution.batch.require_task_quiescent', lambda *a, **kw: None)
    new_run = JournalDirectory(tmp_path/'new')
    summary = run_rollouts(plan=fresh, approved_manifest=fresh.manifest, reader=reader,
        run=new_run, judge=SimulatorJudge(), verifier=NativeChatVerifier(endpoint),
        recovery=recovered, active_groups=2, update_groups=2)
    assert summary['group_selection']['status'] == 'ready'
    assert summary['group_selection']['selected_groups'][0] == 'b'
    assert calls and min(calls) >= 6 and len(calls) == len(set(calls))
    assert 'a' not in summary['group_selection']['selected_groups']
    with pytest.raises(ValueError, match='duplicate'):
        extended_selection({**new_binding, 'rollout_recovery': {**record,
            'task_selection_extension': [{'task_id': 'a', 'reason': 'cooldown-fallback'}]}}, 0, selection)
    # A second interruption keeps the same prefix and does not append twice.
    assert extension_for(new_binding, policy, fresh, state, reader, curriculum) == extension
