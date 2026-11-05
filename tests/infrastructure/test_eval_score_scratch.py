"""A saved score's private workspace lives until archive commit and quiescence."""

import json
from pathlib import Path

import pytest
from test_eval_score_saved import saved_run, score
from test_rejudge import Judge, local_rollout  # noqa: F401

from behave.eval import scoring as scoring_module
from behave.eval.scoring import select_completed_run
from behave.task_execution.batch import load_batch_report
from behave.task_execution.rejudge import judgment_run


def prepare(tmp_path, publication, monkeypatch, indexed):
    output, root, original = saved_run(tmp_path, publication, monkeypatch)
    scratch = tmp_path / "node-local"
    scratch.mkdir()
    unrelated = scratch / "another-owner"
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"unrelated active workspace")
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(scratch))
    judge = Judge(unavailable=False)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda **kw: judge)
    options = {"selection": select_completed_run(root.directory, root.objects)} if indexed else {}
    return output, root, original, scratch, unrelated, judge, options


@pytest.mark.parametrize("indexed", [False, True], ids=["whole-run", "indexed"])
def test_successful_archive_releases_only_its_owned_workspace(
        tmp_path, publication, monkeypatch, request, indexed):
    launches = request.getfixturevalue("local_rollout")
    output, root, original, scratch, unrelated, judge, options = prepare(tmp_path, publication, monkeypatch, indexed)
    before = list(launches)
    target = judgment_run(root, "release")
    observed = []

    def case(_):
        runtime = json.loads((target.directory / "runtime.json").read_bytes())
        assert runtime["state"] == "running"
        assert Path(runtime["local_directory"]).is_dir()
        observed.append(runtime["local_directory"])

    report = score(output, "release", on_case=case, **options)
    runtime = json.loads((target.directory / "runtime.json").read_bytes())
    assert observed and set(observed) == {runtime["local_directory"]}
    assert runtime["state"] == "archived"
    assert not Path(runtime["local_directory"]).exists()
    assert list(scratch.iterdir()) == [unrelated]
    assert (unrelated / "keep").read_bytes() == b"unrelated active workspace"
    assert report.summary["no_submission"] == report.summary["no_result"] == 1
    assert load_batch_report(root.objects, report.head) == report
    assert root.seal("report")[0] == original.head
    count = len(judge.requests)
    assert score(output, "release", **options).summary == report.summary
    assert len(judge.requests) == count and launches == before
    assert list(scratch.iterdir()) == [unrelated]


@pytest.mark.usefixtures("local_rollout")
@pytest.mark.parametrize("indexed", [False, True], ids=["whole-run", "indexed"])
@pytest.mark.parametrize("failure", ["summary", "receipt", "receipt-sync"])
def test_failed_archive_commit_retains_scratch_and_replay_does_not_rescore(
        tmp_path, publication, monkeypatch, indexed, failure):
    from behave.core import storage

    output, root, _, scratch, unrelated, judge, options = prepare(tmp_path, publication, monkeypatch, indexed)
    target = judgment_run(root, "retry")
    write, install = scoring_module.write_json, storage.install_exclusive

    def fail_receipt(path, row, **kwargs):
        if path == target.directory / "runtime.json" and row["state"] == "archived":
            if failure == "receipt-sync":
                write(path, row, **kwargs)  # Atomic replacement preceded the failed directory sync.
            raise OSError("archive commit interrupted")
        return write(path, row, **kwargs)

    def fail_summary(directory, name, data):
        if directory == target.directory and name == "summary.json":
            raise OSError("archive commit interrupted")
        return install(directory, name, data)

    with monkeypatch.context() as fault:
        if failure.startswith("receipt"):
            fault.setattr(scoring_module, "write_json", fail_receipt)
        else:
            fault.setattr(storage, "install_exclusive", fail_summary)
        with pytest.raises(OSError, match="archive commit interrupted"):
            score(output, "retry", **options)
    runtime = json.loads((target.directory / "runtime.json").read_bytes())
    retained = Path(runtime["local_directory"])
    assert runtime["state"] == ("archived" if failure == "receipt-sync" else "running")
    assert retained.is_dir()
    count = len(judge.requests)
    report = score(output, "retry", **options)
    assert len(judge.requests) == count
    assert load_batch_report(root.objects, report.head) == report
    assert json.loads((target.directory / "runtime.json").read_bytes())["state"] == "archived"
    # A successful new workspace does not authorize sweeping earlier failures.
    assert set(scratch.iterdir()) == {retained, unrelated}


@pytest.mark.usefixtures("local_rollout")
@pytest.mark.parametrize("indexed", [False, True], ids=["whole-run", "indexed"])
def test_unconfirmed_worker_cleanup_retains_even_an_archived_report(
        tmp_path, publication, monkeypatch, indexed):
    from behave.task_execution import recovery

    output, root, _, _, _, _, options = prepare(tmp_path, publication, monkeypatch, indexed)
    target = judgment_run(root, "uncertain")
    original = recovery.require_task_quiescent

    def unconfirmed(run, **kwargs):
        if "/judgments/uncertain/candidates/" in str(run.directory):
            raise RuntimeError("worker cleanup unknown")
        return original(run, **kwargs)

    monkeypatch.setattr(recovery, "require_task_quiescent", unconfirmed)
    with pytest.raises(RuntimeError, match="worker cleanup unknown"):
        score(output, "uncertain", **options)
    runtime = json.loads((target.directory / "runtime.json").read_bytes())
    assert runtime["state"] == "failed-retained" and Path(runtime["local_directory"]).is_dir()


@pytest.mark.usefixtures("local_rollout")
def test_runtime_receipts_are_durable_before_scratch_release(tmp_path, publication, monkeypatch):
    output, _, _, _, _, _, options = prepare(tmp_path, publication, monkeypatch, True)
    write, receipts = scoring_module.write_json, []

    def observe(path, row, **kwargs):
        if path.name == "runtime.json":
            assert Path(row["local_directory"]).is_dir()
            receipts.append((row["state"], kwargs.get("durable", False)))
        return write(path, row, **kwargs)

    monkeypatch.setattr(scoring_module, "write_json", observe)
    score(output, "durable", **options)
    assert receipts == [("running", True), ("archived", True)]


@pytest.mark.usefixtures("local_rollout")
@pytest.mark.parametrize("indexed", [False, True], ids=["whole-run", "indexed"])
def test_contended_archive_does_not_allocate_or_change_scratch(tmp_path, publication, monkeypatch, indexed):
    from behave.agent.attempt import AttemptInterrupted

    output, root, _, scratch, unrelated, judge, options = prepare(tmp_path, publication, monkeypatch, indexed)
    with root.owner(), pytest.raises(AttemptInterrupted, match="run-already-owned"):
        score(output, "contended", **options)
    assert list(scratch.iterdir()) == [unrelated]
    assert not (root.directory / "judgments/contended/runtime.json").exists()
    assert not (root.directory / "judgments/contended/report").exists()
    assert judge.requests == []


# The installed-wheel check supplies archived fixture submissions, not test
# helpers or source imports. No model, simulator, Slurm job or network is used.
SCORE_SCRATCH_PROBE = '''
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from behave.eval.scoring import score_saved
from behave.eval.scoring import select_completed_run
from behave.task_execution.batch import load_batch_report
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.rejudge import judgment_run
from behave.task_execution.run_store import RunStore

output = Path(sys.argv[1])
scratch = output.parent / 'installed-score-scratch'
scratch.mkdir()
os.environ['BEHAVE_SCRATCH_DIR'] = str(scratch)
root = RunStore(output / 'run')
original_head = root.seal('report')[0]
selected = select_completed_run(root.directory, root.objects)
class Judge:
    identity = 'installed-lifecycle-fixture/1'
    calls = 0
    def evaluate(self, request, objects):
        self.calls += 1
        return ComponentJudgment(1, 'matched')
judge = Judge()
with patch('behave.task_execution.simulator_judge.SimulatorJudge', return_value=judge), \\
     patch('behave.task_execution.batch.execute_task', side_effect=AssertionError('no generation')):
    for name, selection in [('whole', None), ('indexed', selected)]:
        arguments = dict(workers=2, previous=None, judge_policy=None, cpu_pool=None,
                         stage_from=None, all_submitted=True, selection=selection, retry_testbench=False)
        report = score_saved(output, name, **arguments)
        assert report.summary['requested'] == 3
        assert report.summary['no_result'] == report.summary['no_submission'] == 1
        assert load_batch_report(root.objects, report.head) == report
        assert not list(scratch.iterdir())
        status = json.loads((judgment_run(root, name).directory / 'runtime.json').read_bytes())
        assert status['state'] == 'archived' and not Path(status['local_directory']).exists()
        count = judge.calls
        assert score_saved(output, name, **arguments).summary == report.summary
        assert judge.calls == count and not list(scratch.iterdir())
assert root.seal('report')[0] == original_head
assert not any(name == 'tools' or name.startswith('tools.') for name in sys.modules)
'''
