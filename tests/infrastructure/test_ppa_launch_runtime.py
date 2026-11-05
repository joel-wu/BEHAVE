"""PPA launch and old-snapshot recovery use the shared production runtime."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_ppa_campaign import campaign, plan

from behave.agent.jobs import JobControl
from behave.ppa import launch as launching
from behave.runtime import jobs
from behave.runtime import snapshots as snapshots_module

REPOSITORY = Path(__file__).resolve().parents[2]
SUPPORT = (
    "src/behave/__init__.py", "src/behave/core/__init__.py",
    "src/behave/core/records.py", "src/behave/core/storage.py",
    "src/behave/core/artifacts.py", "src/behave/core/runtime_defaults.py",
    "src/behave/core/lifecycle.py",
    "src/behave/core/paths.py", "src/behave/ppa/__init__.py",
    "src/behave/ppa/config.py", "src/behave/ppa/technology.py",
    "src/behave/ppa/sky130.py",
)


CONTROLLER_FILES = (
    "tools/ppa_campaign.py", "src/behave/ppa/planning.py", "src/behave/ppa/campaign_inputs.py",
    "src/behave/ppa/campaign_recovery.py", "src/behave/ppa/campaign_report.py", "src/behave/ppa/campaign.py",
    "src/behave/ppa/study_report.py", "src/behave/ppa/records.py", "src/behave/ppa/launch.py",
    "src/behave/ppa/staging.py", "src/behave/ppa/finalize.py", "src/behave/ppa/__main__.py",
    "src/behave/core/file_input.py", "src/behave/data/published/files.py", "src/behave/isolation/scratch.py",
    "src/behave/runtime/__init__.py", "src/behave/runtime/jobs.py", "src/behave/runtime/records.py",
    "src/behave/runtime/snapshots.py", "src/behave/runtime/models.py", "src/behave/agent/trace_export.py",
)


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def source(root):
    for name in SUPPORT:
        put(root, name, (REPOSITORY / name).read_bytes())
    put(root, "data/splits/current.json", b'{"authored":"fixture"}\n')
    for name in ("eval.txt", "external_eval.txt"):
        put(root, "data/splits/fixture/" + name, b"evaluation-task\n")
    put(root, "src/behave/ppa/worker.py", b"# frozen native executor\n")
    put(root, "src/behave/agent/prompts/ppa.md", b"frozen prompt\n")
    # This minimal snapshot probes import/policy ownership, not journal replay.
    put(root, "src/behave/agent/run_store.py", b"""class JournalDirectory:
    def __init__(self, *args, **kwargs):
        raise AssertionError('policy probe must not open evidence')
""")
    put(root, "src/behave/sim/worker.py", b"# frozen scoring executor\n")
    put(root, "tests/infrastructure/test_fixture.py", b"# not a production input\n")
    (root / "external").mkdir()
    for name in CONTROLLER_FILES:
        put(root, name, (REPOSITORY / name).read_bytes())
    return root


@pytest.fixture
def launch(tmp_path, monkeypatch):
    repository = source(tmp_path / "repository")
    monkeypatch.setattr(snapshots_module, "FILES", ("tools/ppa_campaign.py", "data/splits/current.json"))
    monkeypatch.setattr(campaign, "__file__", str(repository / "tools/ppa_campaign.py"))
    monkeypatch.delenv("SLURM_STEP_ID", raising=False)
    # Input publication has its own tests; here use real frozen code, input
    # digests and JobControl records without starting a supervisor or Slurm.
    monkeypatch.setattr("behave.ppa.campaign_inputs.freeze_inputs", lambda config, root: config)
    submitted = []

    def submit(directory, argv, **options):
        submitted.append({"directory": directory, "argv": argv, **options})
        return {"state": "submitted", "directory": str(directory)}

    monkeypatch.setattr(jobs, "submit", submit)
    return repository, submitted


def inputs(root, *, unlimited=False):
    provider = root / "provider.json"
    provider.write_text("{}\n")
    config = plan()
    for key in ("release", "experiment", "liberty", "sta", "retained_pool"):
        config[key] = str(provider)
    for method in config["methods"]:
        method.update(provider=str(provider), prompt=str(provider))
    if unlimited:
        config["replicate_seconds"] = None
    campaign.publish(root / "plan.json", config)
    campaign.publish(root / "group.json", {
        "schema": "behave.ppa-campaign-group/1",
        "plans": {"mx": str(root / "plan.json"), "mx2": str(root / "plan.json")},
        "cpu_workers": 24,
    })
    return config


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("unlimited", [False, True])
@pytest.mark.parametrize("entry", ["legacy", "package"])
def test_launch_retains_resources_budgets_and_uses_production_snapshot(tmp_path, launch, grouped, unlimited, entry):
    repository, submitted = launch
    inputs(tmp_path, unlimited=unlimited)
    root = tmp_path / "campaign"
    target = campaign if entry == "legacy" else launching
    options = {} if entry == "legacy" else {"repository": repository}
    if grouped:
        result = target.submit_group(tmp_path / "group.json", root, 123, 90,
                                     runtime_directory="/tmp/ppa-runtime", cpu_offset=4, **options)
    else:
        result = target.submit(tmp_path / "plan.json", root, 123, 90,
                               runtime_directory="/tmp/ppa-runtime", **options)
    assert result["state"] == "submitted"
    assert len(submitted) == 1
    call = submitted[0]
    assert call["directory"] == root / "supervision"
    assert call["cwd"] == repository
    wall = None if unlimited else (2 if grouped else 1) * 14400 + 600
    assert call["wall_seconds"] == (None if wall is None else wall + 120)
    meta = call["metadata"]
    assert meta["allocation"] == 123 and meta["cpus"] == 90
    observation = JobControl(root / "job").inspect(meta["job_key"])
    request = observation.request
    assert observation.state == "prepared" and observation.result is None
    assert request.cpus == 90 and request.allocation == 123
    assert request.cwd == str(repository) and request.wall_seconds == wall
    assert request.network == "host" and request.gpus == 0
    assert request.argv[-2:] == (str(root / "snapshot/src"), str(root))
    assert "from behave.ppa.launch import run_staged" in request.argv[4]
    assert all("tools/ppa_campaign.py" not in arg for arg in request.argv)
    prefix = request.slurm_prefix(overlap=True)
    assert call["argv"][:len(prefix)] == list(prefix)
    assert call["argv"][-4:] == [str(root / "snapshot/src"), "run", str(root / "job"), meta["job_key"]]
    record = snapshots_module.verify(root / "snapshot")
    assert meta["snapshot"] == record["sha256"]
    assert not any(name.startswith("tests/") for name in record["files"])
    assert "overrides" not in record
    settings = json.loads((root / "launch-settings.json").read_text())
    assert settings == {"runtime_directory": "/tmp/ppa-runtime", **({"cpu_offset": 4} if grouped else {})}


def isolated(source_path, entry, *args):
    return subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); " + entry,
        str(source_path / "src"), *map(str, args)],
        cwd=source_path, capture_output=True, text=True, timeout=20, check=False)


def test_prepared_worker_executes_only_its_frozen_package_entry(tmp_path, launch):
    repository, submitted = launch
    inputs(tmp_path)
    put(repository, "tools/ppa_campaign.py", b"raise AssertionError('site wrapper must not execute')\n")
    put(repository, "src/behave/ppa/launch.py", b"""from pathlib import Path
def run_staged(root):
    assert Path(__file__).is_relative_to(root / 'snapshot')
    (root / 'entry.txt').write_text(str(__file__))
    return 9
""")
    root = tmp_path / "campaign"
    launching.submit(tmp_path / "plan.json", root, 123, 90, repository=repository)
    meta = submitted[0]["metadata"]
    request = JobControl(root / "job").inspect(meta["job_key"]).request
    result = subprocess.run(request.argv, cwd=request.cwd, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 9, result.stderr
    assert (root / "entry.txt").read_text() == str(root / "snapshot/src/behave/ppa/launch.py")
