"""Retired source entry points may verify evidence, never restart old work."""

from dataclasses import replace

import pytest
from support.source_archive_fixture import archive_source_run, historical_job
from test_attempts import call
from test_role_process import Unavailable
from test_source_discovery import seed as discovery_inputs
from test_source_selection import assessments, page
from test_source_selection import seed as selection_inputs

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.records import RecordError, parse_json
from behave.data import role as role_module
from behave.data.discovery import DiscoveryConfiguration, discover_sources
from behave.data.role import RoleJob, input_closure
from behave.data.selection import SelectionConfiguration, select_source
from behave.isolation.model import ResourceLimits


@pytest.fixture(params=["discovery", "selection"])
def source_case(tmp_path, request):
    discovery = request.param == "discovery"
    store, inputs = (discovery_inputs if discovery else selection_inputs)(tmp_path)
    config_type = DiscoveryConfiguration if discovery else SelectionConfiguration
    config = config_type(historical_job(inputs), AdmissionPolicy("saved-source-model", 1))
    replies = [call("submit_sources", {})] if discovery else [page(1), call("submit_selection", assessments())]
    replay = discover_sources if discovery else select_source
    return request.param, store, config, replies, replay


def files(run):
    return {str(p.relative_to(run.directory)): p.read_bytes() for p in run.directory.rglob("*") if p.is_file()}


def no_launch(*args, **kwargs):
    pytest.fail("historical replay must not dispatch a model or worker")


def test_fresh_retired_coordinator_fails_before_creating_run(tmp_path, source_case, monkeypatch):
    _, _, config, _, replay = source_case
    monkeypatch.setattr(role_module, "supervise_worker", no_launch)
    run = JournalDirectory(tmp_path / "not-created")
    with pytest.raises(ValueError, match="retired"):
        replay(configuration=config, reader=Unavailable(), run=run)
    assert not run.directory.exists()


@pytest.mark.parametrize("capability", ["historical", "python", "bash"])
def test_retired_preflight_does_not_read_inputs_or_suggest_new_capabilities(source_case, capability):
    _, _, config, _, _ = source_case
    changes = {} if capability == "historical" else {
        "python_limits" if capability == "python" else "shell_limits": ResourceLimits(wall_seconds=1)}
    job = replace(config.job, **changes)
    assert RoleJob.from_bytes(job.to_bytes()) == job
    with pytest.raises(ValueError, match="retired"):
        input_closure(job, Unavailable())
    with pytest.raises(ValueError, match="retired"):
        replace(config, job=job).preflight(Unavailable())


def test_worker_rejects_retired_manifest_before_shell_creation(tmp_path, source_case, monkeypatch, capsys):
    from behave.task_execution import shell

    _, store, config, _, _ = source_case
    reference = store.put(config.job.to_bytes())
    root = tmp_path / "worker-not-created"
    monkeypatch.setattr(role_module.sys, "argv", ["worker", str(root), str(store.directory),
                        reference.sha256, str(reference.size_bytes), "", ""])
    monkeypatch.setattr(shell, "development_shell", no_launch)
    with pytest.raises(SystemExit) as error:
        role_module.worker_main()
    assert error.value.code == 2
    assert parse_json(capsys.readouterr().out.encode()) == {"interruption": "construction-worker-ValueError"}
    assert not root.exists()


def test_low_level_process_rejects_retired_job_before_opening_journals(tmp_path, source_case, monkeypatch):
    _, _, config, _, _ = source_case
    run = JournalDirectory(tmp_path / "never-dispatched")
    monkeypatch.setattr(role_module, "supervise_worker", no_launch)
    with pytest.raises(ValueError, match="retired"):
        role_module.run_role_process(config.job, reader=Unavailable(), journal=run.journal("role"),
                                      supervision=run.journal("supervision"))
    assert not run.directory.exists()


def test_pending_archive_stays_pending_without_relaunch(tmp_path, source_case, monkeypatch):
    kind, store, config, replies, replay = source_case
    run = JournalDirectory(tmp_path / "pending")
    with pytest.raises(AttemptInterrupted, match="fixture-owner-lost"):
        archive_source_run(config, store, run, replies, pending=True)
    # The compatibility lock may predate the crash, but no event may be added.
    with run.owner():
        before = files(run)
    monkeypatch.setattr(role_module, "supervise_worker", no_launch)
    for _ in range(2):
        with pytest.raises(AttemptInterrupted, match="legacy-source-run-unfinished"):
            replay(configuration=config, reader=Unavailable(), run=run)
    assert files(run) == before
    assert run.journal(kind).load()[-1].kind == kind + "_requested"


def test_completed_archive_replay_needs_no_upstream_or_pool_creation(tmp_path, source_case, monkeypatch):
    from behave.agent import admission

    _, store, config, replies, replay = source_case
    run = JournalDirectory(tmp_path / "archive")
    archive_source_run(config, store, run, replies)
    monkeypatch.setattr(role_module, "supervise_worker", no_launch)
    monkeypatch.setattr(admission.ResourcePool, "__init__", no_launch)
    result = replay(configuration=config, reader=Unavailable(), run=run)
    before = files(run)
    assert replay(configuration=config, reader=Unavailable(), run=run) == result
    assert files(run) == before


def test_replay_refuses_changed_configuration_without_publishing_it(tmp_path, source_case):
    _, store, config, replies, replay = source_case
    run = JournalDirectory(tmp_path / "archive")
    archive_source_run(config, store, run, replies)
    replay(configuration=config, reader=Unavailable(), run=run)
    before = files(run)
    changed = replace(config, job=replace(config.job, attempt=replace(config.job.attempt, max_turns=61)))
    with pytest.raises(RecordError, match="configuration differs"):
        replay(configuration=changed, reader=Unavailable(), run=run)
    assert files(run) == before


@pytest.mark.parametrize("channel", ["role", "supervision", "provider", "development"])
def test_replay_rejects_changed_phase_or_auxiliary_journal(tmp_path, source_case, channel):
    _, store, config, replies, replay = source_case
    run = JournalDirectory(tmp_path / "archive")
    archive_source_run(config, store, run, replies)
    name = config.job.inputs.role + ("" if channel == "role" else "_" + channel)
    journal = run.journal("revisions", "r0", name)
    journal.append(journal.load(), "unexpected", {})
    with pytest.raises(RecordError):
        replay(configuration=config, reader=Unavailable(), run=run)


def test_replay_still_requires_exclusive_owner_and_matching_pool(tmp_path, source_case):
    _, store, config, replies, replay = source_case
    run = JournalDirectory(tmp_path / "archive")
    archive_source_run(config, store, run, replies)
    with run.owner(), pytest.raises(AttemptInterrupted, match="already-owned"):
        replay(configuration=config, reader=Unavailable(), run=run)
    with pytest.raises(ValueError, match="pool differs"):
        replay(configuration=config, reader=Unavailable(), run=run, model_pool=ResourcePool(AdmissionPolicy("other", 1)))
    with pytest.raises(TypeError, match="control"):
        replay(configuration=config, reader=Unavailable(), run=run, control=object())


def installed_source_archives(root):
    for kind, seed, config_type, replies in [
        ("discovery", discovery_inputs, DiscoveryConfiguration, [call("submit_sources", {})]),
        ("selection", selection_inputs, SelectionConfiguration, [page(1), call("submit_selection", assessments())]),
    ]:
        store, inputs = seed(root / kind / "inputs")
        config = config_type(historical_job(inputs), AdmissionPolicy("installed-source-model", 1))
        run = JournalDirectory(root / kind / "run")
        archive_source_run(config, store, run, replies)
        (root / kind / "configuration.json").write_bytes(config.to_bytes())
    return root


SOURCE_REPLAY_PROBE = r'''
import importlib.abc, sys
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'behave.data.synthesis', 'behave.data.role', 'behave.agent.loop'}:
            raise AssertionError('source replay imported execution: ' + fullname)
sys.meta_path.insert(0, Block())
from behave.agent.run_store import JournalDirectory
from behave.data.discovery import DiscoveryConfiguration, discover_sources, load_discovery
from behave.data.selection import select_source
from behave.data.selection import SelectionConfiguration
from behave.data.selection import load_selection

def forbidden(*args, **kwargs):
    raise AssertionError("installed source replay dispatched a worker or read upstream")
class Unavailable:
    read = forbidden
root = Path(sys.argv[1])
for kind, config_type, replay, load in (
    ("discovery", DiscoveryConfiguration, discover_sources, load_discovery),
    ("selection", SelectionConfiguration, select_source, load_selection),
):
    config = config_type.from_bytes((root / kind / "configuration.json").read_bytes())
    run = JournalDirectory(root / kind / "run")
    result = replay(configuration=config, reader=Unavailable(), run=run)
    head, _ = run.seal(kind)
    recovered = load(run.objects, head)
    assert (recovered if kind == "discovery" else recovered[0]) == result
    fresh = JournalDirectory(root / kind / "never-started")
    try:
        replay(configuration=config, reader=Unavailable(), run=fresh)
    except ValueError as error:
        assert "retired" in str(error)
    else:
        raise AssertionError("retired source coordinator accepted new work")
    assert not fresh.directory.exists()
print("source-replay-installed")
'''
