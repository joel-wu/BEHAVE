"""Real namespace lifetime tests; no subprocess or namespace fallback mocks."""

import fcntl
import json
import os
import selectors
import signal
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from behave.isolation.service import run_service


def invoke(tmp_path, program, *, budget=5, limit=1 << 20, cancelled=None):
    return run_service((sys.executable, "-I", "-B", "-c", program), cwd=tmp_path,
                       environment={"PATH": os.defpath},
                       deadline=None if budget is None else time.monotonic() + budget, output_bytes=limit,
                       cancelled=cancelled)


@pytest.mark.isolation
def test_startup_admission_does_not_serialize_service_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "1")
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_DIR", str(tmp_path / "startup"))
    program = """import os, time
from pathlib import Path
assert 'BEHAVE_ISOLATION_STARTUP_FD' not in os.environ
Path('ready-LABEL').touch()
deadline = time.monotonic() + 10
while not Path('ready-OTHER').exists():
    assert time.monotonic() < deadline, 'execution was serialized behind startup'
    time.sleep(.01)
print('both-running')
"""
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(invoke, tmp_path, program.replace('LABEL', a).replace('OTHER', b), budget=30)
                for a, b in [('a', 'b'), ('b', 'a')]]
        for job in jobs:
            result = job.result(timeout=40)
            assert result.status == 'exited' and result.returncode == 0, result
            assert result.stdout == b'both-running\n' and result.cleanup_state.quiescent


@pytest.mark.isolation
@pytest.mark.parametrize("slots", ["1", "4", "none"])
def test_trusted_nested_service_retains_startup_configuration(tmp_path, monkeypatch, slots):
    root = str(tmp_path / "startup")
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", slots)
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_DIR", root)
    program = """import os, sys, time
from pathlib import Path
from behave.isolation.service import run_service
assert os.environ['BEHAVE_ISOLATION_STARTUP_SLOTS'] == EXPECTED_SLOTS
if EXPECTED_SLOTS != 'none':
    assert os.environ['BEHAVE_ISOLATION_STARTUP_DIR'] == ROOT
result = run_service((sys.executable, '-c', "import os; print(os.environ['BEHAVE_ISOLATION_STARTUP_SLOTS'])"), cwd=Path.cwd(),
                     environment={'PATH': os.defpath}, deadline=time.monotonic()+20)
assert result.status == 'exited' and result.cleanup_state.quiescent, result
print(result.stdout.decode().strip())
""".replace('ROOT', repr(root)).replace('EXPECTED_SLOTS', repr(slots))
    result = invoke(tmp_path, program, budget=30)
    assert result.status == 'exited' and result.returncode == 0, result
    assert result.stdout == (slots + '\n').encode()


@pytest.mark.parametrize('ending', ['timeout', 'cancelled'])
def test_startup_admission_wait_respects_caller_without_launch(tmp_path, monkeypatch, ending):
    from behave.isolation.startup import acquire_startup

    monkeypatch.setenv('BEHAVE_ISOLATION_STARTUP_SLOTS', '1')
    monkeypatch.setenv('BEHAVE_ISOLATION_STARTUP_DIR', str(tmp_path / 'startup'))
    permit = acquire_startup()
    began = time.monotonic()
    cancelled = (lambda: time.monotonic() - began >= .03) if ending == 'cancelled' else None
    try:
        result = invoke(tmp_path, "open('must-not-start', 'w').close()",
                        budget=.03 if ending == 'timeout' else None, cancelled=cancelled)
        assert result.status == ending and result.cleanup_state.quiescent, result
        assert result.returncode is None and not (tmp_path / 'must-not-start').exists()
    finally:
        permit.close()


@pytest.mark.isolation
@pytest.mark.parametrize("ending", ["timeout", "cancelled"])
def test_slow_bootstrap_is_reaped_without_starting_an_expired_service(tmp_path, monkeypatch, ending):
    from behave.isolation import service, service_init

    bootstrap = tmp_path / "slow_init.py"
    bootstrap.write_text("import time\ntime.sleep(0.5)\n" + Path(service_init.__file__).read_text())
    popen = service.subprocess.Popen

    def slow_launch(argv, **kwargs):
        return popen(tuple(str(bootstrap) if part == service_init.__file__ else part for part in argv), **kwargs)

    monkeypatch.setattr(service.subprocess, "Popen", slow_launch)
    began = time.monotonic()
    cancelled = (lambda: time.monotonic() - began >= 0.1) if ending == "cancelled" else None
    result = invoke(tmp_path, "open('must-not-start', 'w').close()",
                    budget=0.1 if ending == "timeout" else 5, cancelled=cancelled)
    assert result.status == ending and result.cleanup_state.quiescent, result
    assert not (tmp_path / "must-not-start").exists()


@pytest.mark.isolation
@pytest.mark.parametrize("ending", ["normal", "timeout", "exception"])
def test_service_terminates_descendants_that_start_new_sessions(tmp_path, ending):
    program = r'''
import ctypes, fcntl, os, socket, subprocess, sys, time
print(os.readlink('/proc/self/ns/pid'), flush=True)
lock = os.open('descendant.lock', os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(lock, fcntl.LOCK_EX)
ready, notify = os.pipe()
child = subprocess.Popen([sys.executable, '-c',
    'import os,sys,time; os.write(int(sys.argv[1]),b"1"); time.sleep(60)', str(notify)],
    pass_fds=(lock, notify), start_new_session=True)
os.close(notify)
assert os.read(ready, 1) == b'1'
pidfd_open = ctypes.CDLL(None, use_errno=True).pidfd_open
pidfd_open.argtypes = (ctypes.c_int, ctypes.c_uint)
pidfd_open.restype = ctypes.c_int
child_fd = pidfd_open(child.pid, 0)
assert child_fd >= 0
with socket.socket(socket.AF_UNIX) as peer:
    peer.connect(SOCKET_PATH)
    namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY)
    socket.send_fds(peer, [b'namespace'], [namespace_fd, child_fd])
    os.close(namespace_fd)
    os.close(child_fd)
    assert peer.recv(1) == b'1'
ENDING
'''.replace("ENDING", {"normal": "pass", "timeout": "time.sleep(60)", "exception": "raise RuntimeError('test')"}[ending])
    # Pin the namespace while it exists: its inode number alone can be reused
    # after destruction by an unrelated parallel test. Keep the descriptor
    # through both cleanup checks, without keeping any child process alive.
    # A short path also stays within AF_UNIX's pathname limit under pytest.
    with TemporaryDirectory(prefix="pidns-test-", dir="/tmp") as scratch, ExitStack() as cleanup:
        listener = cleanup.enter_context(socket.socket(socket.AF_UNIX))
        address = str(Path(scratch) / "pin.sock")
        listener.bind(address)
        listener.listen(1)
        listener.settimeout(10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(invoke, tmp_path, program.replace("SOCKET_PATH", repr(address)),
                                  budget=2 if ending == "timeout" else None)
            try:
                peer, _ = listener.accept()
            except TimeoutError:
                pytest.fail(f"service did not reach the lifetime handshake: {pending.result(timeout=1)}")
            with peer:
                peer.settimeout(10)
                message, descriptors, _, _ = socket.recv_fds(peer, 32, 2)
                for descriptor in descriptors:
                    cleanup.callback(os.close, descriptor)
                assert message == b"namespace" and len(descriptors) == 2
                namespace = os.readlink(f"/proc/self/fd/{descriptors[0]}")
                child_fd = descriptors[1]
                peer.sendall(b"1")
            result = pending.result(timeout=10)
        assert result.status == ("timeout" if ending == "timeout" else "exited"), result
        assert result.returncode == 0 if ending == "normal" else result.returncode != 0
        assert namespace == result.stdout.decode().strip()
        assert namespace.startswith("pid:["), result
        assert namespace != os.readlink("/proc/self/ns/pid")
        # A /proc enumeration can still observe dead tasks awaiting adoption/
        # reaping by an outer namespace's init. Pin the actual live descendant
        # before shutdown and require its kernel exit notification immediately;
        # neither a reused PID nor a grace sleep can satisfy this assertion.
        with selectors.DefaultSelector() as exited:
            exited.register(child_fd, selectors.EVENT_READ)
            assert exited.select(0), "the acknowledged descendant is still alive"
        # The ready child holds this inherited lock for its entire lifetime.
        # This independently checks cleanup even on /proc hiding other PIDs.
        with (tmp_path / "descendant.lock").open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.isolation
def test_log_retention_does_not_terminate_a_successful_service(tmp_path):
    result = invoke(tmp_path, "import os\nfor _ in range(32): os.write(1,b'x'*4096)", limit=1024)
    assert result.status == "exited", result
    assert len(result.stdout) + len(result.stderr) == 1024
    assert result.returncode == 0
    assert result.dropped_output_bytes == 32 * 4096 - 1024


@pytest.mark.isolation
@pytest.mark.parametrize("ending", ["normal", "timeout", "cancelled"])
def test_logs_are_drained_after_truncation_without_losing_lifetime_control(tmp_path, ending):
    program = r'''
import fcntl, os, subprocess, sys, time
lock = os.open('descendant.lock', os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(lock, fcntl.LOCK_EX)
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                 pass_fds=(lock,), start_new_session=True)
os.close(lock)
for _ in range(32):
    os.write(1, b'x' * 65536)
    os.write(2, b'y' * 65536)
with open('finished-output', 'w') as stream:
    stream.write('done')
ENDING
'''.replace("ENDING", "pass" if ending == "normal" else "time.sleep(60)")
    cancelled = (lambda: (tmp_path / "finished-output").exists()) if ending == "cancelled" else None
    result = invoke(tmp_path, program, budget=2 if ending == "timeout" else None,
                    limit=1024, cancelled=cancelled)
    assert (tmp_path / "finished-output").read_text() == "done"
    assert result.status == ("exited" if ending == "normal" else ending), result
    assert (result.returncode == 0) == (ending == "normal"), result
    assert len(result.stdout) + len(result.stderr) == 1024
    if ending != "cancelled":
        assert result.dropped_output_bytes == 64 * 65536 - 1024
    else:
        # Cancellation may occur before the last pipe chunk has been drained.
        assert result.dropped_output_bytes > 0
    assert result.cleanup_state.quiescent
    with (tmp_path / "descendant.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.isolation
def test_service_does_not_impose_a_second_input_size_budget(tmp_path):
    data = b'x' * (17 << 20)
    result = run_service((sys.executable, "-I", "-B", "-c", "import sys; print(len(sys.stdin.buffer.read()))"),
                         cwd=tmp_path, environment={"PATH": os.defpath}, input_data=data,
                         deadline=time.monotonic() + 5)
    assert result.status == "exited" and result.returncode == 0, result
    assert int(result.stdout) == len(data)


@pytest.mark.isolation
def test_service_stages_stdin_under_configured_local_root(tmp_path, monkeypatch):
    # Startup coordination persists across services; it is not a staged input.
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_DIR", str(tmp_path / "startup"))
    with TemporaryDirectory(prefix="b-stdin-", dir="/tmp") as local:
        monkeypatch.setenv("BEHAVE_SCRATCH_DIR", local)
        program = "import os,sys; print(os.readlink('/proc/self/fd/0')); print(sys.stdin.buffer.read().decode())"
        result = run_service((sys.executable, "-I", "-B", "-c", program), cwd=tmp_path,
                             environment={"PATH": os.defpath}, input_data=b"fixture-input",
                             deadline=time.monotonic() + 5)
        assert result.status == "exited" and result.returncode == 0, result
        path, content = result.stdout.decode().splitlines()
        assert path.startswith(local + "/") and content == "fixture-input"
        assert not list(Path(local).iterdir())


@pytest.mark.isolation
def test_service_neither_inherits_secrets_nor_changes_caller_alarm(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_KEY_CANARY", "must-not-leak")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        result = invoke(tmp_path, "import json,os; print(json.dumps(dict(os.environ)))")
        remaining, _ = signal.getitimer(signal.ITIMER_REAL)
        assert result.returncode == 0, result
        assert "PROVIDER_KEY_CANARY" not in json.loads(result.stdout)
        assert 20 < remaining <= 30
        assert signal.getsignal(signal.SIGALRM) is previous_handler
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def test_expired_dispatch_never_starts_a_process(tmp_path, monkeypatch):
    from behave.isolation import service

    def forbidden(*args, **kwargs):
        pytest.fail("expired dispatch must not launch")
    monkeypatch.setattr(service.subprocess, "Popen", forbidden)
    assert invoke(tmp_path, "pass", budget=-1).status == "timeout"


@pytest.mark.parametrize("grace", [0, -1, True, float("inf")])
def test_cleanup_grace_is_an_explicit_finite_bound(tmp_path, grace):
    with pytest.raises(ValueError, match="cleanup grace"):
        run_service((sys.executable, "-c", "pass"), cwd=tmp_path, environment={},
                    deadline=time.monotonic() + 1, cleanup_seconds=grace)


def test_cleanup_uses_the_explicit_budget_without_an_arbitrary_upper_cap(tmp_path):
    result = run_service((sys.executable, "-c", "pass"), cwd=tmp_path, environment={},
                         deadline=time.monotonic() - 1, cleanup_seconds=121)
    assert result.status == "timeout"


@pytest.mark.isolation
def test_uncapped_cleanup_reaps_a_cancelled_namespace(tmp_path):
    marker = tmp_path / "started"
    result = run_service((sys.executable, "-c",
        "from pathlib import Path; import time; Path('started').touch(); time.sleep(60)"),
        cwd=tmp_path, environment={}, deadline=None,
        cancelled=marker.exists, cleanup_seconds=None)
    assert result.execution_status == "cancelled"
    assert result.cleanup_state.quiescent


@pytest.mark.isolation
def test_missing_namespace_launcher_has_no_host_fallback(tmp_path, monkeypatch):
    from behave.isolation import service

    calls = []
    def unavailable(command, **kwargs):
        calls.append(command)
        raise FileNotFoundError("unshare")
    monkeypatch.setattr(service.subprocess, "Popen", unavailable)
    assert invoke(tmp_path, "raise AssertionError('must not run')").status == "launch-failed"
    assert len(calls) == 1 and calls[0][0] == "/usr/bin/unshare"


@pytest.mark.isolation
def test_cleanup_waits_on_the_namespace_init_pidfd(tmp_path, monkeypatch):
    from behave.isolation import service

    real_selector = service.selectors.DefaultSelector
    waits = []

    class ObservedSelector(real_selector):
        def register(self, fileobj, events, data=None):
            if type(fileobj) is int:
                assert os.readlink(f"/proc/self/fd/{fileobj}") == "anon_inode:[pidfd]"
                self.namespace_fence = True
            return super().register(fileobj, events, data)

        def select(self, timeout=None):
            ready = super().select(timeout)
            if getattr(self, "namespace_fence", False):
                waits.append(bool(ready))
            return ready

    monkeypatch.setattr(service.selectors, "DefaultSelector", ObservedSelector)
    result = invoke(tmp_path, "import os; print(os.getpid(), os.getppid())")
    assert result.status == "exited" and result.returncode == 0
    pid, parent = map(int, result.stdout.split())
    assert pid > 1 and parent == 1  # the application is not its namespace's reaper
    assert waits == [True]


@pytest.mark.isolation
def test_service_reaps_adopted_children_while_the_application_is_still_running(tmp_path):
    program = r'''
import os, time
from pathlib import Path
read, write = os.pipe()
parent = os.fork()
if parent == 0:
    os.close(read)
    child = os.fork()
    if child == 0:
        os.write(write, str(os.getpid()).encode())
        os._exit(0)
    os._exit(0)
os.close(write)
orphan = int(os.read(read, 64))
os.close(read)
assert os.waitpid(parent, 0)[1] == 0
deadline = time.monotonic() + 3
while Path(f'/proc/{orphan}/stat').exists() and time.monotonic() < deadline:
    time.sleep(0.01)
assert not Path(f'/proc/{orphan}/stat').exists(), 'namespace init retained an exited orphan'
# The application must still receive the exit status of children it owns.
child = os.fork()
if child == 0:
    os._exit(23)
assert os.waitstatus_to_exitcode(os.waitpid(child, 0)[1]) == 23
print('reaped')
raise SystemExit(7)
'''
    result = invoke(tmp_path, program, budget=10)
    assert result.status == "exited" and result.returncode == 7, result
    assert result.stdout.strip() == b"reaped", result
    assert result.cleanup_state.quiescent


def test_service_bootstrap_cannot_run_without_a_pid_namespace(monkeypatch):
    from behave.isolation import service_init

    monkeypatch.setattr(service_init.os, "getpid", lambda: 99)
    with pytest.raises(RuntimeError, match="new PID namespace"):
        service_init.main()
