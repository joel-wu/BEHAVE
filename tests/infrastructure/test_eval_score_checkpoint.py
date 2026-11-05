"""Scoring must survive loss of scratch after each committed candidate."""

import json
import socket
from types import SimpleNamespace

import pytest

from behave.core.artifacts import BlobRef
from behave.eval.score_stream import ScoreCheckpoint
from behave.runtime.records import write_json
from behave.task_execution.run_store import RunStore
from tools.eval_score_checkpoint import checkpoint_pass


def start(local):
    config = local.objects.put(b'configuration')
    local.journal('task').append([], 'rejudgment_started', {}, (config,))
    return local.journal('report').append([], 'evaluation_started', {}, (config,))


def case(local, head, value=b'result'):
    evidence = local.objects.put(value)
    return local.journal('report').append_after(head, 'evaluation_case',
        {'row': {'result': evidence.as_dict(), 'score': 1}}, (evidence,))


def test_checkpoint_is_incremental_and_survives_missing_scratch(tmp_path):
    local, durable = RunStore(tmp_path / 'local'), RunStore(tmp_path / 'durable')
    first = case(local, start(local))
    checkpoint = ScoreCheckpoint(local, durable)
    assert checkpoint.publish() == 1
    # Ongoing work is not a committed case and must not be copied or scored.
    local.objects.put(b'unfinished simulation')
    local.journal('judge').append([], 'judge_started', {})
    assert checkpoint.publish() == 0
    second = case(local, first, b'another result')
    assert checkpoint.publish() == 1
    assert not (durable.directory / 'judge').exists()
    assert not (durable.objects.directory / BlobRef.of(b'unfinished simulation').sha256).exists()
    local.directory.rename(tmp_path / 'retained-scratch')
    events = durable.journal('report').load()
    assert len(events) == 3 and events[-1] == second
    assert durable.objects.read(events[1].artifacts[0]) == b'result'


def test_failed_evidence_publication_cannot_commit_a_case(tmp_path, monkeypatch):
    local, durable = RunStore(tmp_path / 'local'), RunStore(tmp_path / 'durable')
    head = start(local)
    checkpoint = ScoreCheckpoint(local, durable)
    checkpoint.publish()
    event = case(local, head)
    publish = durable.objects.publish
    monkeypatch.setattr(durable.objects, 'publish', lambda *_: (_ for _ in ()).throw(OSError('archive unavailable')))
    with pytest.raises(OSError, match='archive unavailable'):
        checkpoint.publish()
    assert durable.journal('report').load() == (head,)
    monkeypatch.setattr(durable.objects, 'publish', publish)
    assert checkpoint.publish() == 1
    assert durable.journal('report').load()[-1] == event


def test_resume_does_not_republish_finished_case_payloads(tmp_path, monkeypatch):
    local, durable = RunStore(tmp_path / 'local'), RunStore(tmp_path / 'durable')
    first = case(local, start(local))
    ScoreCheckpoint(local, durable).publish()
    checkpoint = ScoreCheckpoint(local, durable)
    calls = []
    publish = durable.objects.publish
    monkeypatch.setattr(durable.objects, 'publish', lambda reader, refs: (calls.extend(refs), publish(reader, refs)))
    assert checkpoint.publish() == 0 and calls == []
    second = case(local, first, b'second')
    assert checkpoint.publish() == 1
    assert calls == list(second.artifacts)


def test_external_checkpoint_does_not_modify_frozen_run_or_final_journal(tmp_path):
    source = tmp_path / 'original'
    local = RunStore(tmp_path / 'scratch/run')
    target = RunStore(local.directory / 'judgments/baseline', objects=local.objects)
    case(target, start(target))
    output = tmp_path / 'pass'
    write_json(output / 'plan.json', {'name': 'baseline', 'shards': [[str(source)]]})
    write_json(output / 'live-0.json', {'sources': {str(source): {'state': 'preparing'}}})
    runtime = source / 'run/judgments/baseline/runtime.json'
    write_json(runtime, {'host': socket.gethostname(), 'state': 'running',
                         'local_directory': str(local.directory.parent)})
    before = runtime.read_bytes()
    terminal, rows = checkpoint_pass(output)
    assert not terminal and rows[0]['new_cases'] == 1
    assert runtime.read_bytes() == before
    assert not (runtime.parent / 'report').exists()
    destination = RunStore(rows[0]['directory'], objects=RunStore(source / 'run').objects)
    assert destination.journal('report').load()[-1].kind == 'evaluation_case'
    assert json.loads((destination.directory / 'source.json').read_bytes())['source'] == str(source)
    assert checkpoint_pass(output)[1][0]['new_cases'] == 0


def test_score_callback_follows_durable_case_publication(tmp_path, monkeypatch):
    from tools.eval_review_trial import _score_local

    output = tmp_path / 'archive'
    durable = RunStore(output / 'run')
    target = RunStore(durable.directory / 'judgments/baseline', objects=durable.objects)
    callbacks = []

    def score(*, directory, name, objects, on_case, **_):
        local = RunStore(directory / 'judgments' / name, objects=objects)
        event = case(local, start(local))
        on_case({'status': 'scored', 'score': 1})
        assert target.journal('report').load()[-1] == event
        local.journal('report').append_after(event, 'evaluation_finished', {})
        return SimpleNamespace(summary={'completed': 1})

    monkeypatch.setattr('behave.task_execution.rejudge.rejudge_batch', score)
    monkeypatch.setattr('behave.task_execution.simulator_judge.SimulatorJudge', lambda **_: None)
    result = _score_local(output, 'baseline', workers=1, previous=None,
        judge_policy=None, cpu_pool=None, stage_from=None, selection={}, retry_testbench=False,
        on_case=lambda row: callbacks.append(target.journal('report').load()[-1].kind))
    assert result.summary == {'completed': 1} and callbacks == ['evaluation_case']
    assert json.loads((target.directory / 'runtime.json').read_bytes())['state'] == 'archived'
