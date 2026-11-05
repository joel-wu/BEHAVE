"""Release only this PPA worker's verified staging after settled success."""

import json
import os
import subprocess
import sys

import pytest
from test_ppa_campaign import plan

from behave.core import process_limits
from behave.core.lifecycle import CleanupState
from behave.isolation import scratch
from behave.ppa import launch, staging
from behave.runtime import snapshots as snapshots_module
from behave.runtime.records import publish_json, write_json


@pytest.fixture(params=[False, True], ids=["single", "group"])
def prepared(request, tmp_path, monkeypatch):
    root, local = tmp_path / "archive", tmp_path / "scratch"
    root.mkdir()
    local.mkdir()
    config = plan()
    config.update(replicates=1, methods=config["methods"][:1])
    publish_json(root / "plan.json", config)
    group = {"schema": "behave.ppa-campaign-group/1", "plans": {
        name: str(root / "plan.json") for name in ("first", "second")}, "cpu_workers": 2}
    if request.param:
        publish_json(root / "group.json", group)
    publish_json(root / "inputs.json", {})
    shared = tmp_path / "shared-runtime"
    shared.mkdir()
    (shared / "keep").write_bytes(b"other runtime consumers")
    publish_json(root / "launch-settings.json", {"runtime_directory": str(shared), "cpu_offset": 1})
    monkeypatch.setattr(scratch, "mkdtemp", lambda **_: str(local))
    monkeypatch.setattr(process_limits, "configure_worker_process_limit", dict)
    affinity = [{2, 3, 4}]
    monkeypatch.setattr(os, "sched_getaffinity", lambda _: set(affinity[0]))
    monkeypatch.setattr(os, "sched_setaffinity", lambda _, cpus: affinity.__setitem__(0, set(cpus)))
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "original-runtime"))
    monkeypatch.setenv("BEHAVE_EDA_ROOT", str(tmp_path / "original-eda"))
    installation = tmp_path / "installation"
    installation.mkdir()
    (installation / "tool").write_bytes(b"retained installation")
    (root / "snapshot").mkdir()
    (root / "snapshot/source.py").write_bytes(b"retained code")

    def stage(source, target):
        assert source == root / "snapshot"
        target.mkdir()
        (target / "source.py").write_bytes((source / "source.py").read_bytes())
        (target / "external").symlink_to(installation, target_is_directory=True)
        target.chmod(0o555)
        return {"sha256": "verified-fixture-snapshot"}

    monkeypatch.setattr(snapshots_module, "stage", stage)
    monkeypatch.setattr(snapshots_module, "verify", lambda _: {"sha256": "verified-fixture-snapshot"})
    monkeypatch.setattr(launch, "freeze_plan", lambda config, target: publish_json(target / "plan.json", config))
    execution = root / "execution"
    owners = [execution / "campaigns" / name for name in group["plans"]] if request.param else [execution]
    services = [owner / "runs" / config["methods"][0]["name"] / "r00" / "search-service" for owner in owners]
    state = {"code": 0, "cleanup": "complete", "calls": 0, "services": services}

    def run(path, *, snapshot, **kwargs):
        state["calls"] += 1
        assert path == execution and snapshot == local / "snapshot"
        assert affinity[0] == {3, 4}
        (path / "paid-record").write_bytes(b"must survive")
        for service in services:
            publish_json(service / "requested.json", {"arguments": ["search"], "seconds": None})
            publish_json(service / "service.json", {"cleanup": state["cleanup"], "ok": state["code"] == 0})
        if state.get("error"):
            raise RuntimeError("interrupted worker")
        return state["code"]

    monkeypatch.setattr(launch, "run_group" if request.param else "run", run)
    yield root, local, state
    assert affinity[0] == {2, 3, 4}
    assert os.environ["BEHAVE_RUNTIME_DIR"] == str(tmp_path / "original-runtime")
    assert os.environ["BEHAVE_EDA_ROOT"] == str(tmp_path / "original-eda")
    assert (shared / "keep").read_bytes() == b"other runtime consumers"
    assert (installation / "tool").read_bytes() == b"retained installation"
    assert (root / "snapshot/source.py").read_bytes() == b"retained code"


def test_release_preserves_shared_evidence_and_external_runtime(prepared, monkeypatch):
    root, local, state = prepared
    original = scratch.ScratchDirectory.cleanup

    def cleanup(owner):
        receipt = json.loads((root / "scratch-release.json").read_text())
        assert receipt["local_directory"] == str(local)
        assert receipt["cleanup"] == "complete" and receipt["exit_code"] == 0
        assert receipt["snapshot"] == "verified-fixture-snapshot"
        assert json.loads((root / "runtime.json").read_text())["scratch"] == "release-ready"
        assert (root / "execution/trace_index.json").exists()
        original(owner)

    monkeypatch.setattr(scratch.ScratchDirectory, "cleanup", cleanup)
    assert launch.run_staged(root) == 0
    assert not local.exists()
    assert (root / "execution/paid-record").read_bytes() == b"must survive"
    assert json.loads((root / "runtime.json").read_text())["scratch"] == "released"
    with pytest.raises(RuntimeError, match="already dispatched"):
        launch.run_staged(root)
    assert state["calls"] == 1


@pytest.mark.parametrize("cleanup", ["unknown", "incomplete", None, "invalid"])
def test_returned_success_is_not_a_child_cleanup_witness(prepared, cleanup):
    root, local, state = prepared
    state["cleanup"] = cleanup
    assert launch.run_staged(root) == 0  # Preserve the existing campaign exit code.
    assert local.exists() and not (root / "scratch-release.json").exists()
    assert json.loads((root / "runtime.json").read_text())["scratch"] == "retained"


@pytest.mark.parametrize("failure", ["cleanup", "released-status"])
def test_release_failure_keeps_receipt_and_never_reexecutes(prepared, monkeypatch, failure):
    root, local, state = prepared
    if failure == "cleanup":
        def cleanup(_):
            raise OSError("release interrupted")
        monkeypatch.setattr(scratch.ScratchDirectory, "cleanup", cleanup)
    else:
        def write(path, value, **kwargs):
            if value.get("scratch") == "released":
                raise OSError("release interrupted")
            write_json(path, value, **kwargs)
        monkeypatch.setattr(staging, "write_json", write)
    with pytest.raises(OSError, match="release interrupted"):
        launch.run_staged(root)
    assert local.exists() is (failure == "cleanup")
    assert (root / "scratch-release.json").exists()
    assert json.loads((root / "runtime.json").read_text())["scratch"] == "release-ready"
    assert (root / "execution/paid-record").read_bytes() == b"must survive"
    with pytest.raises(RuntimeError, match="already dispatched"):
        launch.run_staged(root)
    assert state["calls"] == 1


@pytest.mark.parametrize("failure", ["nonzero", "exception", "staging", "export", "receipt", "status"])
def test_incomplete_execution_or_publication_retains_staging(prepared, monkeypatch, failure):
    root, local, state = prepared

    def broken(*args, **kwargs):
        raise OSError("injected publication failure")

    if failure == "nonzero":
        state["code"] = 1
    elif failure == "exception":
        state["error"] = True
    elif failure == "staging":
        monkeypatch.setattr(snapshots_module, "stage", broken)
    elif failure == "export":
        monkeypatch.setattr(launch, "export_traces", broken)
    elif failure == "receipt":
        publish = staging.publish_json

        def commit(path, value):
            if path.name == "scratch-release.json":
                broken()
            publish(path, value)

        monkeypatch.setattr(staging, "publish_json", commit)
    else:
        def write(path, value, **kwargs):
            if value.get("scratch") == "release-ready":
                assert kwargs["durable"] is True
                broken()
            write_json(path, value, **kwargs)
        monkeypatch.setattr(launch, "write_json", write)
    if failure == "nonzero":
        assert launch.run_staged(root) == 1
    else:
        with pytest.raises((OSError, RuntimeError)):
            launch.run_staged(root)
    assert local.exists()
    if state["calls"]:
        assert (root / "execution/paid-record").read_bytes() == b"must survive"


@pytest.mark.parametrize("defect", ["missing-receipt", "missing-request", "invalid-json", "fifo", "symlink"])
def test_receipt_inspection_fails_closed(tmp_path, defect):
    config = plan()
    config.update(replicates=1, methods=config["methods"][:1])
    path = tmp_path / "runs" / config["methods"][0]["name"] / "r00/search-service"
    publish_json(path / "requested.json", {})
    publish_json(path / "service.json", {"cleanup": "complete"})
    if defect == "missing-request":
        (path / "requested.json").unlink()
    else:
        (path / "service.json").unlink()
        if defect == "invalid-json":
            (path / "service.json").write_bytes(b"{invalid")
        elif defect == "fifo":
            os.mkfifo(path / "service.json")
        elif defect == "symlink":
            publish_json(tmp_path / "other-service.json", {"cleanup": "complete"})
            (path / "service.json").symlink_to(tmp_path / "other-service.json")
    assert launch._service_cleanup(tmp_path, [(tmp_path, config)]) is CleanupState.UNKNOWN


PPA_SCRATCH_PROBE = r'''
import json, os, pathlib, sys, tempfile
from unittest.mock import patch
from behave.core.artifacts import BlobRef
from behave.ppa import launch, staging
from behave.runtime import snapshots as snapshots_module
from behave.runtime.records import publish_json
repository, plan, root = map(pathlib.Path, sys.argv[1:])
os.environ.pop('SLURM_STEP_ID', None)
local_parent = root.with_name(root.name + '-scratch')
local_parent.mkdir()
real_mkdtemp = tempfile.mkdtemp
with patch.object(snapshots_module, 'FILES', ('tools/ppa_campaign.py', 'data/splits/current.json')), \
     patch('behave.runtime.jobs.submit', lambda *a, **kw: None):
    launch.submit(plan, root, 123, 8, repository=repository)
source_manifest = snapshots_module.verify(root / 'snapshot')
calls = []
def execute(execution, *, snapshot):
    calls.append(execution)
    assert snapshot.is_relative_to(local_parent)
    publish_json(execution / 'retained-result.json', {'source': BlobRef.of(b'recorded RTL').as_dict()})
    return 0
write = staging.write_json
def interrupted_status(path, value, **kwargs):
    if value.get('scratch') == 'released':
        raise OSError('interrupted terminal publication')
    write(path, value, **kwargs)
with patch('behave.isolation.scratch.mkdtemp', lambda **kw: real_mkdtemp(prefix=kw['prefix'], dir=local_parent)), \
     patch('behave.core.process_limits.configure_worker_process_limit', lambda: {}), \
     patch.object(launch, 'run', execute), patch.object(staging, 'write_json', interrupted_status):
    try:
        launch.run_staged(root)
    except OSError as exc:
        assert str(exc) == 'interrupted terminal publication'
    else:
        raise AssertionError('expected injected interruption')
assert list(local_parent.iterdir()) == []
assert json.loads((root / 'runtime.json').read_text())['scratch'] == 'release-ready'
with patch.object(launch, 'run', side_effect=AssertionError('must not run another model')):
    assert launch.release_staged(root)['scratch'] == 'released'
    assert launch.release_staged(root)['scratch'] == 'released'
assert len(calls) == 1
assert snapshots_module.verify(root / 'snapshot') == source_manifest
receipt = json.loads((root / 'scratch-release.json').read_text())
assert receipt['snapshot'] == source_manifest['sha256']
assert json.loads((root / 'runtime.json').read_text())['scratch'] == 'released'
assert (root / 'execution/retained-result.json').exists()
assert (root / 'execution/inputs.json').exists()
'''


def test_real_snapshot_staging_and_release_without_cluster(tmp_path, publication):
    from test_ppa_launch_inputs import input_plan
    from test_ppa_launch_runtime import source

    repository = source(tmp_path / "repository")
    config = tmp_path / "plan.json"
    publish_json(config, input_plan(tmp_path, publication))
    result = subprocess.run([sys.executable, "-B", "-c", PPA_SCRATCH_PROBE,
        str(repository), str(config), str(tmp_path / "run")], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
