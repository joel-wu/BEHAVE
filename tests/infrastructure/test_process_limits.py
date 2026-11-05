import json
import resource
import subprocess
import sys

import pytest

from behave.core.process_limits import configure_worker_process_limit


def test_worker_already_at_hard_limit_does_not_change_it(monkeypatch):
    monkeypatch.setattr(resource, "getrlimit", lambda _: (600, 600))
    monkeypatch.setattr(resource, "setrlimit", lambda *args: pytest.fail("already configured"))
    assert configure_worker_process_limit() == {"previous_soft": 600, "soft": 600, "hard": 600}


def test_child_lifts_inherited_soft_limit_without_changing_parent():
    before = resource.getrlimit(resource.RLIMIT_NPROC)
    script = """
import json, resource, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from behave.core.process_limits import configure_worker_process_limit
soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
low = 300 if hard == resource.RLIM_INFINITY else min(300, hard)
resource.setrlimit(resource.RLIMIT_NPROC, (low, hard))
receipt = configure_worker_process_limit()
with ThreadPoolExecutor(max_workers=2) as workers:
    assert list(workers.map(lambda n: n + 1, range(8))) == list(range(1, 9))
child = subprocess.check_output([sys.executable, '-c',
    'import json, resource; print(json.dumps(resource.getrlimit(resource.RLIMIT_NPROC)))'])
print(json.dumps({'receipt': receipt, 'child': json.loads(child)}))
"""
    result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True, check=True)
    record = json.loads(result.stdout)
    assert record["receipt"]["soft"] == record["receipt"]["hard"] == before[1]
    assert record["child"] == [before[1], before[1]]
    assert resource.getrlimit(resource.RLIMIT_NPROC) == before


@pytest.mark.isolation
def test_limit_is_lifted_before_creating_service_namespace(tmp_path):
    before = resource.getrlimit(resource.RLIMIT_NPROC)
    script = """
import json, os, resource, sys, time
from pathlib import Path
from behave.core.process_limits import configure_worker_process_limit
from behave.isolation.service import run_service
from behave.core.process_limits import configure_worker_process_limit, process_limit
soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
resource.setrlimit(resource.RLIMIT_NPROC, (1, hard))
receipt = configure_worker_process_limit()
result = run_service((sys.executable, '-c',
    'import json, resource; print(json.dumps(resource.getrlimit(resource.RLIMIT_NPROC)))'),
    cwd=Path(sys.argv[1]), environment={'PATH': os.defpath}, deadline=time.monotonic() + 20)
assert result.status == 'exited' and result.returncode == 0, result
assert result.cleanup_state.quiescent, result
assert json.loads(result.stdout) == [hard, hard]
print(json.dumps(receipt))
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["previous_soft"] == 1
    assert resource.getrlimit(resource.RLIMIT_NPROC) == before


@pytest.mark.parametrize("selected", [None, 4096])
def test_score_stager_configures_before_preparation(tmp_path, monkeypatch, selected):
    from tools import eval_score_job

    order = []

    def configure(limit):
        order.append(("limit", limit))
        return {"previous_soft": 300, "soft": -1 if limit is None else limit, "hard": -1}

    def stage(*args):
        assert order == [("limit", selected)]
        raise RuntimeError("probe stops before copying")

    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", configure)
    monkeypatch.setattr("behave.eval.scoring.mkdtemp", lambda **kw: str(tmp_path))
    monkeypatch.setattr("behave.runtime.snapshots.stage", stage)
    with pytest.raises(RuntimeError, match="probe stops"):
        eval_score_job.staged(tmp_path, process_limit=selected)
    assert json.loads((tmp_path / "process-0.json").read_text())["previous_soft"] == 300


def test_score_run_preserves_explicit_limit_before_starting_queues(tmp_path, monkeypatch):
    from tools import eval_score_job

    (tmp_path / "plan.json").write_text(json.dumps({"workers": 2, "shards": [[]]}))
    configured = []

    def configure(limit):
        configured.append(limit)
        return {"previous_soft": 300, "soft": limit, "hard": -1}

    def environment(*, environment):
        assert configured == [4096]
        assert isinstance(environment, dict)
        raise RuntimeError("probe stops before any workers")

    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", configure)
    monkeypatch.setattr("behave.eval.scoring.scoring_environment", environment)
    with pytest.raises(RuntimeError, match="probe stops"):
        eval_score_job.run(tmp_path, process_limit=4096)
    assert json.loads((tmp_path / "cpu-0.json").read_text())["process_limit"]["soft"] == 4096


def test_external_pipeline_configures_before_workspace_and_tool_preparation(tmp_path, monkeypatch):
    from tools import pipeline_trial

    (tmp_path / "settings.json").write_text('{"process_limit":4096}')
    order = []

    def configure(limit):
        order.append(("limit", limit))
        return {"previous_soft": 300, "soft": limit, "hard": -1}

    def scratch(**kwargs):
        assert order == [("limit", 4096)]
        raise RuntimeError("probe stops before workspace creation")

    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", configure)
    monkeypatch.setattr("behave.isolation.scratch.ScratchDirectory", scratch)
    with pytest.raises(RuntimeError, match="probe stops"), pipeline_trial.local_run(tmp_path, "rollout"):
        pytest.fail("no work should run")
