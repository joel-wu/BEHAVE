import json

import pytest

from tools.slime_pause_after_checkpoint import checkpoint_event


def test_pause_waits_for_target_checkpoint_not_scored_batch_or_other_save(tmp_path):
    journal = tmp_path / "artifacts/plan/rounds/native/rounds"
    journal.mkdir(parents=True)
    for index, (kind, step) in enumerate((
            ("checkpoint_saved", 3), ("round_scored", 4), ("round_started", 5))):
        (journal / f"{index:06d}.json").write_text(
            json.dumps({"kind": kind, "payload": {"round": step}}))
    assert checkpoint_event(tmp_path, 5) is None
    saved = journal / "000003.json"
    saved.write_text(json.dumps({"kind": "checkpoint_saved", "payload": {"round": 4}}))
    assert checkpoint_event(tmp_path, 5) == saved


def test_no_checkpoint_journal_is_not_a_save(tmp_path):
    assert checkpoint_event(tmp_path, 5) is None


def test_successor_validation_failure_never_stops_parent(tmp_path, monkeypatch):
    from behave.train import jobs as jobs_module

    helper = tmp_path / 'stop.py'
    helper.write_text('')
    monkeypatch.setattr(jobs_module, 'require_live_allocation', lambda *a: None)
    monkeypatch.setattr(jobs_module, 'checkpoint_event', lambda *a: tmp_path / 'saved')
    monkeypatch.setattr(jobs_module, 'verify_checkpoint', lambda *a: tmp_path / 'saved')
    monkeypatch.setattr(jobs_module.subprocess, 'run', lambda *a, **kw: pytest.fail('must not signal'))
    def changed():
        raise ValueError('prepared successor changed')
    with pytest.raises(ValueError, match='prepared successor changed'):
        jobs_module.wait_and_pause(tmp_path, 123, 50, poll_seconds=1,
                                 stop_script=helper, before_stop=changed)
    assert not (tmp_path / 'operator_stop.json').exists()
