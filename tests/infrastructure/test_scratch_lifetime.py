"""No live/uncertain owner loses its scratch merely by leaving Python scope."""

import gc
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_data_checks import comparison
from test_judge import ScriptedJudge, setup
from test_process_model import command, request
from test_testbench_reports import POLICY, inputs

from behave.agent import AttemptJournal, AttemptSpec
from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.process_model import ProcessModelBackend
from behave.agent.process_worker import supervise_worker
from behave.core import paths
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, parse_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.isolation import scratch, service
from behave.task_execution.judge import judge_submission
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.tb_report import TBObservation
from behave.task_execution.testbench import observe_testbench


@pytest.fixture
def directories(tmp_path, monkeypatch):
    created = []

    def make(**kwargs):
        assert Path(kwargs["dir"]) == paths.scratch_root()
        path = tmp_path / (kwargs["prefix"] + str(len(created)))
        path.mkdir()
        created.append(path)
        return str(path)

    monkeypatch.setattr(scratch, "mkdtemp", make)
    yield created
    # All subprocesses in this module are fixtures; no real owner remains.
    for path in created:
        if path.exists():
            shutil.rmtree(path)


def test_temporary_and_persistent_roots_are_independent(tmp_path, monkeypatch):
    monkeypatch.delenv("BEHAVE_SCRATCH_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "ambient-nfs"))
    monkeypatch.setenv("BEHAVE_WORK_DIR", str(tmp_path / "persistent"))
    assert paths.scratch_root() == Path("/tmp")
    assert paths.work_root() == tmp_path / "persistent"
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path / "local"))
    assert paths.scratch_root() == tmp_path / "local"
    assert paths.work_root() == tmp_path / "persistent"
    assert list(tmp_path.iterdir()) == []  # Path selection does not create or move data.


def test_stopped_scratch_cleans_read_only_profiles_without_following_links(tmp_path):
    installation = tmp_path / "installation"
    installation.mkdir()
    (installation / "tool").write_bytes(b"keep")
    installation.chmod(0o555)
    try:
        with scratch.ScratchDirectory(prefix="owned-", parent=tmp_path,
                                      cleanup=lambda: CleanupState.COMPLETE) as created:
            profile = created / "profile"
            profile.mkdir()
            (profile / "sim_main.cpp").write_bytes(b"source")
            (profile / "tools").symlink_to(installation, target_is_directory=True)
            profile.chmod(0o555)
        assert not created.exists()
        assert (installation / "tool").read_bytes() == b"keep"
        assert installation.stat().st_mode & 0o777 == 0o555
    finally:
        installation.chmod(0o755)


@pytest.mark.parametrize("state", list(CleanupState))
def test_configured_scratch_retains_the_same_cleanup_rules(tmp_path, monkeypatch, state):
    local = tmp_path / "local"
    local.mkdir()
    persistent = DirectoryStore(tmp_path / "persistent")
    reference = persistent.put(b"must survive scratch cleanup")
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(local))
    with scratch.ScratchDirectory(prefix="owned-", cleanup=lambda: state) as created:
        assert created.parent == local
    try:
        assert created.exists() is (not state.quiescent)
        assert persistent.read(reference) == b"must survive scratch cleanup"
    finally:
        # This test creates no processes; retained fixture data has no live owner.
        if created.exists():
            shutil.rmtree(created)


@pytest.mark.parametrize("state", list(CleanupState))
def test_retained_scratch_reuses_the_same_lifetime_witness(tmp_path, state):
    owned = tmp_path / "retained"
    owned.mkdir()
    (owned / "result").write_bytes(b"retained evidence")
    with scratch.ScratchDirectory.retained(owned, cleanup=lambda: state) as reopened:
        assert reopened == owned and (reopened / "result").read_bytes() == b"retained evidence"
    assert owned.exists() is (not state.quiescent)


def test_retained_scratch_refuses_missing_paths_and_symlinks(tmp_path):
    other, link = tmp_path / "owned", tmp_path / "link"
    other.mkdir()
    link.symlink_to(other, target_is_directory=True)
    for path in (tmp_path / "missing", link):
        with pytest.raises(ValueError, match="existing owned directory"):
            scratch.ScratchDirectory.retained(path, cleanup=lambda: CleanupState.COMPLETE)
    assert other.is_dir() and link.is_symlink() and not (tmp_path / "missing").exists()


def test_fixed_workers_share_local_root_but_keep_per_dispatch_work(tmp_path, monkeypatch):
    from behave.task_execution._worker_transport import exchange

    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(local))
    monkeypatch.setenv("BEHAVE_WORK_DIR", str(tmp_path / "persistent-default"))
    journal = AttemptJournal(tmp_path / "explicit-run/events", DirectoryStore(tmp_path / "explicit-run/objects"))
    reference = journal.objects.put(b"fixture")
    calls = []

    def run(argv, *, cwd, environment, **kwargs):
        assert cwd.parent == local
        assert environment["TMPDIR"] == environment["BEHAVE_SCRATCH_DIR"] == str(local)
        assert environment["BEHAVE_WORK_DIR"] == str(cwd / "sim-work")
        calls.append(cwd)
        return service.ServiceResult("timeout", None, b"", b"", 0.)

    monkeypatch.setattr(service, "run_service", run)
    for _ in range(2):
        receipt, _ = supervise_worker(namespace="fixture", module="behave.task_execution.solver",
            attempt=AttemptSpec("fixture", "solver", {}), model=command("pass"), journal=journal,
            reference=reference, log_bytes=1024, ingresses=(None,))
        assert receipt["cleanup"] == "complete"
    with scratch.ScratchDirectory(prefix="judge-", cleanup=lambda: CleanupState.COMPLETE) as workdir:
        response = exchange("judge", b"{}", workdir=workdir, objects=journal.objects,
                            deadline=time.monotonic() + 10, timeout_seconds=10, log_bytes=1024)
        assert response.cleanup is CleanupState.COMPLETE
    assert len(set(calls)) == 3 and not any(path.exists() for path in calls)
    assert journal.objects.read(reference) == b"fixture"


@pytest.mark.parametrize("state", list(CleanupState))
def test_directory_release_requires_the_owner_witness_even_after_gc(directories, state):
    owner = scratch.ScratchDirectory(prefix="owned-", cleanup=lambda: state)
    with owner as path:
        (path / "input").write_bytes(b"keep for active owner")
    del owner
    gc.collect()
    assert path.exists() is (not state.quiescent)


@pytest.mark.parametrize("status", ["timeout", "cleanup-incomplete", "unknown-result", "raise"])
def test_model_broker_preserves_unknown_workspace_and_blocks_reuse(directories, monkeypatch, status):
    cmd = command("pass")
    backend = ProcessModelBackend(cmd)
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs["cwd"])
        (kwargs["cwd"] / "input").write_bytes(b"model input")
        if status == "raise":
            raise RuntimeError("lost supervisor response")
        return service.ServiceResult(status, None, b"", b"", .01)

    monkeypatch.setattr(service, "run_service", run)
    result = backend.generate(request(cmd))
    assert result.kind == "unknown"
    if status == "timeout":
        assert backend.retained_workspace is None and not calls[0].exists()
    else:
        assert backend.retained_workspace == calls[0] and calls[0].is_dir()
        assert parse_json(result.raw)["retained_workspace"] == str(calls[0])
        with pytest.raises(RecordError):
            backend.generate(request(cmd))
        assert len(calls) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, OSError, KeyError])
def test_model_supervisor_keeps_exception_type_not_secret_text(directories, monkeypatch, error_type):
    cmd = command("pass")
    backend = ProcessModelBackend(cmd)

    def broken(*args, **kwargs):
        raise error_type("fixture-secret-do-not-record")

    monkeypatch.setattr(service, "run_service", broken)
    result = backend.generate(request(cmd))
    receipt = parse_json(result.raw)
    assert result.kind == "unknown" and result.code == "model-worker-supervisor-error"
    assert receipt["status"] == "supervisor-error"
    assert receipt["exception_type"] == error_type.__name__
    assert b"fixture-secret-do-not-record" not in result.raw
    assert b"Traceback" not in result.raw
    assert backend.cleanup_state is CleanupState.UNKNOWN
    assert backend.retained_workspace.is_dir()
    with pytest.raises(RecordError):
        backend.generate(request(cmd))


@pytest.mark.parametrize("uncertain_owner", [None, "worker", "relay"])
def test_worker_releases_after_all_relays_and_records_retention(
    tmp_path, directories, monkeypatch, uncertain_owner,
):
    def run(*args, **kwargs):
        return service.ServiceResult("cleanup-incomplete" if uncertain_owner == "worker" else "exited",
                                     0, b"", b"", .01)

    class Relay:
        pool = SimpleNamespace(quarantine=lambda state: None)
        endpoint = "fixture.sock"
        cleanup_state = CleanupState.UNKNOWN

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert directories[0].is_dir()  # Cleanup must happen before scratch disposal.
            self.cleanup_state = CleanupState.UNKNOWN if uncertain_owner == "relay" else CleanupState.COMPLETE

    monkeypatch.setattr(service, "run_service", run)
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    receipt, logs = supervise_worker(namespace="fixture", module="behave.task_execution.solver",
        attempt=AttemptSpec("fixture", "solver", {}), model=command("pass"), journal=journal,
        reference=journal.objects.put(b"fixture"), log_bytes=1024, ingresses=(lambda *a: Relay(),))
    assert (receipt["cleanup"] == "complete") is (uncertain_owner is None)
    assert directories[0].exists() is (uncertain_owner is not None)
    if uncertain_owner is not None:
        retained = parse_json(journal.objects.read(logs[-1]))
        assert retained["schema"] == "behave.retained-scratch/1"
        assert retained["path"] == str(directories[0]) and retained["host"]


@pytest.mark.parametrize("state", list(CleanupState))
def test_judge_retention_does_not_change_score_or_redispatch_on_replay(tmp_path, directories, state):
    task, submission, journal = setup(tmp_path)

    class Judge(ScriptedJudge):
        def evaluate(self, request, objects):
            return ComponentJudgment(None, "fixture", "infrastructure", cleanup=state)

    result = judge_submission(task=task, submission=submission, journal=journal, backend=Judge())
    assert result.score is None and result.behavior.cleanup is state
    original = list(directories)
    assert all(path.exists() is (not state.quiescent) for path in original)
    assert judge_submission(task=task, submission=submission, journal=journal, backend=ScriptedJudge()) == result
    assert all(not path.exists() for path in directories[len(original):])  # Replay owns no old process.
    if not state.quiescent:
        record = parse_json(journal.objects.read(result.behavior.evidence[-1]))
        assert record["path"] == str(original[0])


@pytest.mark.parametrize("state", list(CleanupState))
def test_testbench_retention_preserves_report_and_replay(tmp_path, directories, state):
    _, public, submission, _, journal = inputs(tmp_path)

    class TB:
        identity = POLICY.backend

        def observe(self, request, objects):
            return TBObservation("unavailable", "fixture", cleanup=state)

    result = observe_testbench(task=public, submission=submission, journal=journal, backend=TB(), policy=POLICY)
    assert result.observation.cleanup is state
    original = list(directories)
    assert all(path.exists() is (not state.quiescent) for path in original)
    assert observe_testbench(task=public, submission=submission, journal=journal, backend=TB(), policy=POLICY) == result
    assert all(not path.exists() for path in directories[len(original):])
    if not state.quiescent:
        record = parse_json(journal.objects.read(result.observation.evidence[-1]))
        assert record["path"] == str(original[0])


@pytest.mark.parametrize("state", list(CleanupState))
def test_development_cocotb_retains_unfinished_workspace(tmp_path, directories, monkeypatch, state):
    from behave.task_execution import development as development_module
    from behave.task_execution.cocotb_worker import CocotbRun

    monkeypatch.setattr(development_module, "run_cocotb", lambda *a, **kw: CocotbRun(
        TBObservation("unavailable", "fixture", cleanup=state)))
    objects = DirectoryStore(tmp_path / "objects")
    result = development_module.execute_cocotb(object(), objects)
    assert result.observation.cleanup is state
    assert directories[0].exists() is (not state.quiescent)
    if not state.quiescent:
        assert parse_json(objects.read(result.observation.evidence[-1]))["path"] == str(directories[0])


@pytest.mark.parametrize("state", list(CleanupState))
def test_construction_scratch_uses_the_same_owner_witness(tmp_path, directories, monkeypatch, state):
    selected, journal = comparison(tmp_path)
    called = []

    def compare(*args, workdir, **kwargs):
        called.append(workdir)
        (workdir / "input").write_bytes(b"fixed comparison inputs")
        return ComponentJudgment(None, "fixture", "infrastructure", cleanup=state)

    monkeypatch.setattr(checks, "compare_sources", compare)
    report = checks.check_sources(selected, journal=journal)
    assert report.result.cleanup is state
    assert called[0].exists() is (not state.quiescent)
    assert checks.check_sources(selected, journal=journal) == report and len(called) == 1
    if not state.quiescent:
        retained = parse_json(journal.objects.read(report.result.evidence[-1]))
        assert retained["schema"] == "behave.retained-scratch/1"
        assert retained["path"] == str(called[0]) and retained["cleanup"] == state.value
    for event in journal.load():
        journal.objects.put(event.to_bytes())
    assert checks.load_check(journal.objects, report.event) == report


@pytest.mark.parametrize("error", [OSError("lost return"), AttemptInterrupted("lost return"), KeyboardInterrupt()])
def test_construction_scratch_survives_a_lost_supervisor_return(tmp_path, directories, monkeypatch, error):
    selected, journal = comparison(tmp_path)
    pool = ResourcePool(AdmissionPolicy("checks", 1))
    called = []

    def compare(*args, workdir, **kwargs):
        called.append(workdir)
        raise error

    monkeypatch.setattr(checks, "compare_sources", compare)
    with pytest.raises(type(error)):
        checks.check_sources(selected, journal=journal, admission=pool)
    gc.collect()
    assert called[0].is_dir()
    assert journal.load()[-1].kind == "check_requested"
    assert checks.check_cleanup(journal) is CleanupState.UNKNOWN
    pool = ResourcePool(pool.policy)
    with pytest.raises(AttemptInterrupted):
        checks.check_sources(selected, journal=journal, admission=pool)
    assert len(called) == 1  # Cold recovery cannot create another worker or workspace.
    with pytest.raises(AttemptInterrupted), pool.acquire():
        pytest.fail("unconfirmed service must quarantine capacity")


def test_unconfirmed_rpc_is_not_deleted_by_object_collection(directories, monkeypatch):
    from behave.agent import local_rpc

    class Listener:
        def bind(self, path):
            Path(path).touch()

        def listen(self, *args):
            pass

        def settimeout(self, *args):
            pass

        def close(self):
            pass

    thread = SimpleNamespace(start=lambda: None, join=lambda **kw: None, is_alive=lambda: True)
    monkeypatch.setattr(local_rpc.socket, "socket", lambda *a, **kw: Listener())
    monkeypatch.setattr(local_rpc.threading, "Thread", lambda **kw: thread)
    relay = local_rpc.LocalRPCServer(name="model-relay",
                                     deadline=time.monotonic() + 10)
    relay.__enter__()
    with pytest.raises(RecordError):
        relay.__exit__()
    del relay
    gc.collect()
    assert directories[0].is_dir()


def test_model_wire_import_does_not_initialize_execution_backend():
    root = str(Path(sys.modules["behave"].__file__).resolve().parent.parent)
    script = ("import sys; sys.path.insert(0, sys.argv[1]); "
              "from behave.agent.process_model import ModelCommand, encode_model_outcome; "
              "from behave.serving.lifecycle import ServingFailure; "
              "assert 'behave.isolation' not in sys.modules")
    subprocess.run((sys.executable, "-I", "-B", "-c", script, root), check=True,
                   capture_output=True, timeout=10)
