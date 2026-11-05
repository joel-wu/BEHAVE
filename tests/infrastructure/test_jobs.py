"""CPU job identity, idempotency and the distinction between cancel and cleanup."""

import fcntl
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from behave.agent import job_worker
from behave.agent.attempt import AttemptInterrupted
from behave.agent.jobs import JobControl, JobRequest
from behave.core import runtime_defaults as defaults
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError
from behave.isolation.service import ServiceResult


def job(tmp_path, **kwargs):
    request = JobRequest((sys.executable, "-c", "pass"), str(tmp_path), wall_seconds=10, **kwargs)
    control = JobControl(tmp_path / "job")
    return control, control.create(request)


@pytest.mark.parametrize("field,value", [
    ("wall_seconds", 0), ("wall_seconds", True), ("wall_seconds", float("inf")),
    ("cpus", 0), ("cpus", True), ("allocation", 0), ("allocation", True),
    ("cleanup_seconds", 0), ("cleanup_seconds", True), ("output_bytes", 0), ("output_bytes", True),
    ("gpus", True), ("gpus", -1), ("gpus", 2),
    ("network", "all"), ("cwd", "."), ("argv", ("python",)), ("argv", ("/bin/true", "bad\0arg")),
    ("argv", ()), ("argv", ["/bin/true"]),
])
def test_job_request_rejects_invalid_values(tmp_path, field, value):
    request = JobRequest((sys.executable, "-c", "pass"), str(tmp_path), wall_seconds=10)
    with pytest.raises(ValueError):
        replace(request, **{field: value})


def test_slurm_request_is_one_bounded_existing_allocation_step(tmp_path):
    request = JobRequest((sys.executable, "-c", "pass"), str(tmp_path), 61, cpus=8, allocation=27037)
    assert JobRequest.from_dict(request.as_dict()) == request
    prefix = request.slurm_prefix()
    assert "--jobid=27037" in prefix and "--nodes=1" in prefix and "--ntasks=1" in prefix
    assert "--cpus-per-task=8" in prefix and "--exact" in prefix and "--export=NONE" in prefix
    assert "--time=3" in prefix and "--immediate=15" in prefix and "--gres=none" in prefix
    assert "--overlap" not in prefix and "--overlap" in request.slurm_prefix(overlap=True)
    with pytest.raises(ValueError):
        replace(request, allocation=None).slurm_prefix()


def test_slurm_operational_times_use_shared_defaults(tmp_path, monkeypatch):
    request = JobRequest(("/bin/true",), str(tmp_path), 61, cleanup_seconds=10, allocation=27037)
    monkeypatch.setattr(defaults, "JOB_SLURM_WAIT_SECONDS", 23)
    monkeypatch.setattr(defaults, "JOB_SLURM_EXIT_SECONDS", 120)
    assert "--immediate=23" in request.slurm_prefix()
    assert "--time=4" in request.slurm_prefix()


@pytest.mark.parametrize("changes", [{"wall_seconds": None}, {"cleanup_seconds": None},
                                      {"wall_seconds": None, "cleanup_seconds": None}])
def test_unlimited_job_does_not_gain_an_outer_slurm_deadline(tmp_path, changes):
    request = replace(JobRequest(("/bin/true",), str(tmp_path), 61, allocation=27037), **changes)
    assert not any(arg.startswith("--time=") for arg in request.slurm_prefix())


def test_job_worker_preserves_unlimited_execution_and_cleanup(tmp_path, monkeypatch):
    control = JobControl(tmp_path / "job")
    key = control.create(JobRequest(("/bin/true",), str(tmp_path), None, cleanup_seconds=None))

    def run(*args, **kwargs):
        assert kwargs["deadline"] is None and kwargs["cleanup_seconds"] is None
        return ServiceResult("exited", 0, b"", b"", 0.1)

    monkeypatch.setattr(job_worker, "run_service", run)
    observed = job_worker.execute(control.run.directory, key)
    assert observed.result.status == "exited" and observed.cleanup is CleanupState.COMPLETE


def test_gpu_job_records_explicit_resources_without_changing_cpu_identity(tmp_path):
    request = JobRequest(("/bin/true",), str(tmp_path), 10, allocation=27037)
    assert "gpus" not in request.as_dict()
    gpu = replace(request, gpus=2)
    assert JobRequest.from_dict(gpu.as_dict()) == gpu
    assert "--gpus-per-task=2" in gpu.slurm_prefix() and "--gres=none" not in gpu.slurm_prefix()
    control = JobControl(tmp_path / "gpu")
    key = control.create(gpu)
    assert control.inspect(key).request == gpu
    assert control.run.journal("job").load()[0].payload["schema"] == "behave.allocated-job/2"


@pytest.mark.parametrize("devices", [None, "", "0", "0,0", "0,1,2", "-1,0", "0, 1", "GPU-a,../dev"])
def test_gpu_worker_requires_slurm_visible_device_assignment(tmp_path, monkeypatch, devices):
    control, key = job(tmp_path, allocation=27037, gpus=2)
    for name, value in {"SLURM_JOB_ID": "27037", "SLURM_STEP_ID": "2", "SLURM_PROCID": "0",
                        "SLURM_STEP_NUM_TASKS": "1"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    if devices is not None:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", devices)
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **kw: pytest.fail("invalid GPU assignment dispatched"))
    with pytest.raises(RecordError, match="Slurm device visibility"):
        job_worker.execute(control.run.directory, key)
    assert control.inspect(key).state == "prepared"


@pytest.mark.parametrize("devices", ["2,3", "GPU-abc,GPU-def", "MIG-GPU-abc/1/0,MIG-GPU-def/2/0"])
def test_gpu_worker_retains_assignment_and_forwards_no_other_environment(tmp_path, monkeypatch, devices):
    control, key = job(tmp_path, allocation=27037, gpus=2)
    for name, value in {"SLURM_JOB_ID": "27037", "SLURM_STEP_ID": "2", "SLURM_PROCID": "0",
                        "SLURM_STEP_NUM_TASKS": "1", "CUDA_VISIBLE_DEVICES": devices,
                        "PROVIDER_SECRET_CANARY": "not-forwarded"}.items():
        monkeypatch.setenv(name, value)

    def run(*args, **kwargs):
        assert kwargs["environment"]["CUDA_VISIBLE_DEVICES"] == devices
        assert "PROVIDER_SECRET_CANARY" not in kwargs["environment"]
        return ServiceResult("exited", 0, b"", b"", .1)

    monkeypatch.setattr(job_worker, "run_service", run)
    outcome = job_worker.execute(control.run.directory, key)
    assert outcome.worker["cuda_visible_devices"] == devices
    assert control.inspect(key) == outcome


def test_cpu_worker_hides_ambient_gpu_assignment(tmp_path, monkeypatch):
    control, key = job(tmp_path)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")

    def run(*args, **kwargs):
        assert kwargs["environment"]["CUDA_VISIBLE_DEVICES"] == ""
        return ServiceResult("exited", 0, b"", b"", .1)

    monkeypatch.setattr(job_worker, "run_service", run)
    job_worker.execute(control.run.directory, key)


def test_created_jobs_are_unique_and_cancel_does_not_assert_cleanup(tmp_path):
    control, key = job(tmp_path)
    other = JobControl(tmp_path / "other")
    assert other.create(control.inspect(key).request) != key
    initial = control.inspect(key)
    assert initial.state == "prepared" and initial.cleanup is CleanupState.UNKNOWN
    assert not initial.cancel_requested
    with pytest.raises(RecordError) as error:
        control.create(initial.request)
    assert error.value.code == "job-already-created"
    for _ in range(3):
        observed = control.cancel(key)
        assert observed.cancel_requested and observed.cleanup is CleanupState.UNKNOWN
        assert observed.head == initial.head
    assert len(control.run.journal("cancel").load()) == 1
    with pytest.raises(RecordError) as error:
        control.cancel("0" * 64)
    assert error.value.code == "job-identity-mismatch"


@pytest.mark.parametrize("point", ["before", "after"])
def test_cancelled_worker_never_dispatches_and_replay_never_runs_again(tmp_path, monkeypatch, point):
    from behave.isolation import service

    control, key = job(tmp_path)
    monkeypatch.setattr(service.subprocess, "Popen", lambda *a, **kw: pytest.fail("cancelled job dispatched"))
    if point == "before":
        control.cancel(key)
    else:
        original = job_worker.run_service

        def cancel_at_boundary(*args, **kwargs):
            control.cancel(key)
            return original(*args, **kwargs)

        monkeypatch.setattr(job_worker, "run_service", cancel_at_boundary)
    outcome = job_worker.execute(control.run.directory, key)
    assert outcome.state == "settled" and outcome.result.status == "cancelled"
    assert outcome.cleanup is CleanupState.COMPLETE
    assert job_worker.execute(control.run.directory, key) == outcome


@pytest.mark.parametrize("status,cleanup", [
    ("exited", CleanupState.COMPLETE), ("timeout", CleanupState.COMPLETE),
    ("cleanup-incomplete", CleanupState.INCOMPLETE), ("supervisor-error", CleanupState.UNKNOWN),
])
def test_terminal_outcome_preserves_cleanup_and_output_closure(tmp_path, monkeypatch, status, cleanup):
    control, key = job(tmp_path)
    result = ServiceResult(status, 0, b"stdout", b"stderr", 1., .1)
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **kw: result)
    outcome = job_worker.execute(control.run.directory, key)
    assert outcome.result == result and outcome.cleanup is cleanup
    assert outcome == JobControl(control.run.directory).inspect(key)
    head, closure = control.run.seal("job")
    assert head == outcome.head and len(closure) == 5
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **kw: pytest.fail("cold terminal redispatched"))
    assert job_worker.execute(control.run.directory, key) == outcome
    control.cancel(key)
    assert control.inspect(key).result == result


def test_supervisor_exception_surfaces_but_receipt_keeps_unknown_cleanup(tmp_path, monkeypatch):
    control, key = job(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("secret-canary")

    monkeypatch.setattr(job_worker, "run_service", fail)
    with pytest.raises(RuntimeError, match="secret-canary"):
        job_worker.execute(control.run.directory, key)
    observed = control.inspect(key)
    assert observed.cleanup is CleanupState.UNKNOWN
    assert observed.result.status == "supervisor-error"
    assert all(b"secret-canary" not in event.to_bytes() for event in control.run.journal("job").load())
    assert job_worker.execute(control.run.directory, key) == observed


@pytest.mark.parametrize("lost_event", ["job_started", "job_settled"])
def test_lost_append_acknowledgement_never_repeats_work(tmp_path, monkeypatch, lost_event):
    from behave.agent.journal import AttemptJournal

    control, key = job(tmp_path)
    original = AttemptJournal.append
    calls = []

    def append(self, expected, kind, payload, artifacts=()):
        result = original(self, expected, kind, payload, artifacts)
        if kind == lost_event:
            raise OSError("lost commit acknowledgement")
        return result

    def run(*args, **kwargs):
        calls.append(True)
        return ServiceResult("exited", 0, b"", b"", 0.)

    monkeypatch.setattr(AttemptJournal, "append", append)
    monkeypatch.setattr(job_worker, "run_service", run)
    with pytest.raises(OSError):
        job_worker.execute(control.run.directory, key)
    monkeypatch.setattr(AttemptJournal, "append", original)
    if lost_event == "job_started":
        assert control.inspect(key).cleanup is CleanupState.UNKNOWN
        with pytest.raises(RecordError) as error:
            job_worker.execute(control.run.directory, key)
        assert error.value.code == "job-already-dispatched"
        assert calls == []
    else:
        assert job_worker.execute(control.run.directory, key).cleanup is CleanupState.COMPLETE
        assert calls == [True]


def test_live_owner_cannot_be_adopted_but_can_be_cancelled(tmp_path, monkeypatch):
    control, key = job(tmp_path)
    ready, release = Event(), Event()

    def run(*args, **kwargs):
        ready.set()
        assert release.wait(5)
        assert kwargs["cancelled"]()
        return ServiceResult("cancelled", None, b"", b"", 0.)

    monkeypatch.setattr(job_worker, "run_service", run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(job_worker.execute, control.run.directory, key)
        try:
            assert ready.wait(5)
            with pytest.raises(AttemptInterrupted):
                job_worker.execute(control.run.directory, key)
            assert control.cancel(key).cleanup is CleanupState.UNKNOWN
        finally:
            release.set()
        assert pending.result(5).cleanup is CleanupState.COMPLETE


def test_concurrent_cancel_requests_share_one_durable_record(tmp_path):
    control, key = job(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: JobControl(control.run.directory).cancel(key), range(8)))
    assert all(result.cancel_requested and result.cleanup is CleanupState.UNKNOWN for result in results)
    assert len(control.run.journal("cancel").load()) == 1


def test_insufficient_cpu_affinity_does_not_start_work(tmp_path, monkeypatch):
    control, key = job(tmp_path, cpus=2)
    monkeypatch.setattr(job_worker.os, "sched_getaffinity", lambda _: {7})
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **kw: pytest.fail("oversubscribed dispatch"))
    with pytest.raises(RecordError) as error:
        job_worker.execute(control.run.directory, key)
    assert error.value.code == "job-cpus-unavailable"
    assert control.inspect(key).state == "prepared"


@pytest.mark.parametrize("corrupt", ["cancel", "order", "worker", "closure", "result", "output"])
def test_cold_reader_rejects_invalid_lifecycle_evidence(tmp_path, monkeypatch, corrupt):
    from behave.agent.journal import Event as JournalEvent

    control, key = job(tmp_path)
    if corrupt == "cancel":
        control.run.journal("cancel").append((), "job_cancel_requested", {"key": "other"})
    elif corrupt in {"order", "worker"}:
        journal = control.run.journal("job")
        journal.append(journal.load(), "job_settled" if corrupt == "order" else "job_started", {})
    else:
        monkeypatch.setattr(job_worker, "run_service", lambda *a, **kw: ServiceResult("exited", 0, b"x", b"", 0.))
        job_worker.execute(control.run.directory, key)
        event = control.run.journal("job").load()[-1]
        if corrupt == "output":
            path = control.run.objects.directory / event.artifacts[0].sha256
            path.chmod(0o600)
            path.write_bytes(b"y")
        else:
            path = control.run.directory / "job" / "000002.json"
            payload = dict(event.payload)
            if corrupt == "result":
                payload["returncode"] = None
            changed = JournalEvent(event.sequence, event.parent, event.kind, payload,
                                   () if corrupt == "closure" else event.artifacts)
            path.chmod(0o600)
            path.write_bytes(changed.to_bytes())
    with pytest.raises(RecordError):
        control.inspect(key)


@pytest.mark.parametrize("changes", [
    {"SLURM_JOB_ID": "1"}, {"SLURM_STEP_ID": "batch"}, {"SLURM_PROCID": "1"},
    {"SLURM_STEP_NUM_TASKS": "2"}, {"SLURM_STEP_ID": "-1"}, {"SLURM_JOB_ID": "bad"},
])
def test_worker_checks_actual_slurm_binding_before_dispatch(tmp_path, monkeypatch, changes):
    control, key = job(tmp_path, allocation=27037)
    env = {"SLURM_JOB_ID": "27037", "SLURM_STEP_ID": "42", "SLURM_PROCID": "0", "SLURM_STEP_NUM_TASKS": "1"}
    for name, value in {**env, **changes}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **kw: pytest.fail("invalid binding dispatched"))
    with pytest.raises(RecordError) as error:
        job_worker.execute(control.run.directory, key)
    assert error.value.code == "job-worker-invalid"
    assert control.inspect(key).state == "prepared"


def test_worker_commits_scheduler_identity_and_only_allows_explicit_environment(tmp_path, monkeypatch):
    control, key = job(tmp_path, allocation=27037)
    for name, value in {"SLURM_JOB_ID": "27037", "SLURM_STEP_ID": "42", "SLURM_PROCID": "0",
                        "SLURM_STEP_NUM_TASKS": "1", "PROVIDER_KEY_CANARY": "secret"}.items():
        monkeypatch.setenv(name, value)

    def run(argv, **kwargs):
        worker = control.inspect(key).worker
        assert worker["allocation"] == 27037 and worker["step"] == 42
        assert worker["host"] and worker["boot_id"] and worker["pid"] == os.getpid()
        assert argv[0] == "/usr/bin/taskset"
        assert kwargs["environment"] == {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
                                          "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8", "CUDA_VISIBLE_DEVICES": ""}
        return ServiceResult("exited", 0, b"", b"", 0.)

    monkeypatch.setattr(job_worker, "run_service", run)
    assert job_worker.execute(control.run.directory, key).cleanup is CleanupState.COMPLETE


@pytest.mark.isolation
@pytest.mark.parametrize("ending", ["exited", "timeout", "cancelled", "verbose"])
@pytest.mark.parametrize("unlimited", [False, True])
def test_real_worker_reaps_descendants_and_produces_cold_readable_evidence(tmp_path, ending, unlimited):
    # The detached descendant inherits a lock; acquisition after settlement
    # independently proves it no longer exists, without guessing a numeric PID.
    program = '''
import fcntl, os, subprocess, sys, time
lock = os.open('held.lock', os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(lock, fcntl.LOCK_EX)
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                 pass_fds=(lock,), start_new_session=True)
open('ready', 'w').close()
print(len(os.sched_getaffinity(0)), flush=True)
ENDING
'''.replace("ENDING", {"exited": "pass", "timeout": "time.sleep(60)", "cancelled": "time.sleep(60)",
                       "verbose": "print('x' * 10000, flush=True)"}[ending])
    control = JobControl(tmp_path / "job")
    request = JobRequest((sys.executable, "-c", program), str(tmp_path),
                         wall_seconds=2 if ending == "timeout" else None if unlimited else 15,
                         cleanup_seconds=None if unlimited else defaults.JOB_CLEANUP_SECONDS, output_bytes=1024)
    key = control.create(request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(job_worker.execute, control.run.directory, key)
        if ending == "cancelled":
            stop = time.monotonic() + 10
            while not (tmp_path / "ready").exists() and not pending.done() and time.monotonic() < stop:
                time.sleep(.02)
            assert (tmp_path / "ready").exists()
            control.cancel(key)
        result = pending.result(timeout=25)
    assert result.result.status == ("exited" if ending == "verbose" else ending), result
    assert result.result.stdout.startswith(b"1\n")
    if ending == "verbose":
        assert result.result.returncode == 0
        assert len(result.result.stdout) + len(result.result.stderr) == 1024
    assert result.cleanup is CleanupState.COMPLETE
    with (tmp_path / "held.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert result == JobControl(control.run.directory).inspect(key)


@pytest.mark.isolation
def test_cli_worker_cancelled_from_another_process_owner(tmp_path):
    from behave.isolation.service import run_service

    control = JobControl(tmp_path / "job")
    key = control.create(JobRequest((sys.executable, "-c", "import time; open('ready','w').close(); time.sleep(60)"),
                                   str(tmp_path), wall_seconds=15))
    argv = (sys.executable, "-B", "-m", "behave.agent.job_worker", "run", str(control.run.directory), key)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_service, argv, cwd=tmp_path,
                              environment={"PATH": os.defpath, "PYTHONPATH": str(Path(job_worker.__file__).parents[2])},
                              deadline=time.monotonic() + 25)
        stop = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and not pending.done() and time.monotonic() < stop:
            time.sleep(.02)
        assert (tmp_path / "ready").exists()
        before = JobControl(control.run.directory).inspect(key)
        assert before.state == "started" and before.cleanup is CleanupState.UNKNOWN
        assert JobControl(control.run.directory).cancel(key).cancel_requested
        process_result = pending.result(30)
    assert process_result.status == "exited" and process_result.returncode == 1, process_result
    after = JobControl(control.run.directory).inspect(key)
    assert after.result.status == "cancelled" and after.cleanup is CleanupState.COMPLETE


def test_command_init_never_runs_as_a_host_process(monkeypatch):
    from behave.isolation import command_init

    monkeypatch.setattr(command_init.os, "getpid", lambda: 42)
    with pytest.raises(RuntimeError, match="supervised PID namespace"):
        command_init.main()


@pytest.mark.isolation
def test_live_job_reaps_orphaned_grandchildren(tmp_path):
    program = '''
import os, time
read, write = os.pipe()
parent = os.fork()
if parent == 0:
    orphan = os.fork()
    if orphan == 0:
        time.sleep(.1)
        os._exit(0)
    os.write(write, str(orphan).encode())
    os._exit(0)
os.close(write)
orphan = int(os.read(read, 64))
os.close(read)
os.waitpid(parent, 0)
stop = time.monotonic() + 5
while os.path.exists('/proc/' + str(orphan)) and time.monotonic() < stop:
    time.sleep(.01)
assert not os.path.exists('/proc/' + str(orphan)), 'job did not reap its orphan'
print('reaped while job remains alive', flush=True)
'''
    control = JobControl(tmp_path / "job")
    key = control.create(JobRequest((sys.executable, "-c", program), str(tmp_path), wall_seconds=10))
    outcome = job_worker.execute(control.run.directory, key)
    assert outcome.result.status == "exited" and outcome.result.returncode == 0, outcome
    assert outcome.result.stdout == b"reaped while job remains alive\n"
    assert outcome.cleanup is CleanupState.COMPLETE
