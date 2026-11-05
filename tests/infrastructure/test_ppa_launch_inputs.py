"""PPA deployment inputs and installed launch preparation use the package API."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_ppa_campaign import campaign  # noqa: F401
from test_ppa_campaign_inputs import input_plan
from test_ppa_launch_runtime import source

from behave.ppa import launch


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("options,message", [
    ({"allocation": 0}, "positive integers"),
    ({"allocation": True}, "positive integers"),
    ({"cpus": -1}, "positive integers"),
    ({"cpus": "8"}, "positive integers"),
    ({"runtime_directory": "/"}, "absolute directory"),
    ({"runtime_directory": "relative"}, "absolute directory"),
])
def test_invalid_deployment_is_rejected_before_input_reads_or_writes(tmp_path, monkeypatch, grouped, options, message):
    monkeypatch.delenv("SLURM_STEP_ID", raising=False)
    values = {"repository": tmp_path, "allocation": 123, "cpus": 8, **options}
    target = launch.submit_group if grouped else launch.submit
    with pytest.raises(ValueError, match=message):
        target(tmp_path / "never-read.json", tmp_path / "not-created", **values)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("grouped", [False, True])
def test_missing_explicit_repository_never_uses_installed_package_root(tmp_path, monkeypatch, grouped):
    monkeypatch.delenv("SLURM_STEP_ID", raising=False)
    target = launch.submit_group if grouped else launch.submit
    with pytest.raises(ValueError, match="source repository"):
        target(tmp_path / "never-read.json", tmp_path / "not-created", 123, 8,
               repository=tmp_path / "missing-source")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("kind", ["fifo", "duplicate-json"])
def test_submit_inputs_are_strict_regular_records(tmp_path, monkeypatch, grouped, kind):
    monkeypatch.delenv("SLURM_STEP_ID", raising=False)
    path = tmp_path / "input.json"
    if kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_text('{"schema":"first","schema":"second"}')
    code = """
from pathlib import Path
from behave.ppa import launch
root = Path(sys.argv[1])
try:
    getattr(launch, sys.argv[2])(root / 'input.json', root / 'not-created', 123, 8, repository=root)
except ValueError as error:
    print(str(error))
else:
    raise AssertionError('malformed operator input accepted')
assert not (root / 'not-created').exists()
"""
    source_path = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run([sys.executable, "-I", "-B", "-c",
        f"import sys; sys.path.insert(0, {str(source_path)!r})\n" + code,
        str(tmp_path), "submit_group" if grouped else "submit"],
        capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert ("regular file" if kind == "fifo" else "duplicate") in result.stdout


PPA_LAUNCH_PROBE = r'''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from behave.agent.jobs import JobControl
from behave.ppa import launch
from behave.runtime import jobs, snapshots as snapshots_module
repository, plan, output = map(Path, sys.argv[1:])
os.environ.pop('SLURM_STEP_ID', None)
group = output.parent / 'launch-group.json'
group.write_text(json.dumps({'schema': 'behave.ppa-campaign-group/1',
    'plans': {'fixture': str(plan)}, 'cpu_workers': 2}))
calls = []
def submit(directory, argv, **options):
    calls.append((directory, argv, options))
    return {'state': 'submitted'}
with patch.object(snapshots_module, 'FILES', ('tools/ppa_campaign.py', 'data/splits/current.json')), \
     patch.object(jobs, 'submit', submit):
    for name, function, inputs in (('single', launch.submit, plan), ('group', launch.submit_group, group)):
        root = output / name
        assert function(inputs, root, 123, 8, repository=repository) == {'state': 'submitted'}
        directory, argv, options = calls[-1]
        assert directory == root / 'supervision' and options['cwd'] == repository
        meta = options['metadata']
        request = JobControl(root / 'job').inspect(meta['job_key']).request
        assert request.cpus == 8 and request.allocation == 123 and request.gpus == 0
        assert request.argv[-2:] == (str(root / 'snapshot/src'), str(root))
        assert 'from behave.ppa.launch import run_staged' in request.argv[4]
        assert all('tools/ppa_campaign.py' not in arg for arg in request.argv)
        assert snapshots_module.verify(root / 'snapshot')['sha256'] == meta['snapshot']
        assert (root / 'inputs.json').is_file()
        try:
            function(inputs, root, 123, 8, repository=repository)
        except ValueError as error:
            assert 'fresh campaign directory' in str(error)
        else:
            raise AssertionError('existing launch redispatched')
assert len(calls) == 2
'''


def test_explicit_launch_prepares_real_inputs(tmp_path, publication):
    repository = source(tmp_path / "repository")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    plan = inputs / "plan.json"
    plan.write_text(json.dumps(input_plan(inputs, publication)))
    source_path = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run([sys.executable, "-I", "-B", "-c",
        f"import sys; sys.path.insert(0, {str(source_path)!r})\n" + PPA_LAUNCH_PROBE,
        str(repository), str(plan), str(tmp_path / "launched")],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
