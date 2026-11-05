"""The unit-only launcher uses the production wire, not a second file protocol."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from behave.sim import worker
from tests.support import worker as direct


def _program(monkeypatch, source):
    launch = direct.subprocess.Popen
    children = []

    def fixture_process(argv, **kwargs):
        assert argv == [sys.executable, "-m", "behave.sim.worker", "--stdio"]
        assert kwargs["start_new_session"] is True
        child = launch([sys.executable, "-c", source], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(direct.subprocess, "Popen", fixture_process)
    return children


def _reaped(children):
    assert children
    for child in children:
        assert child.poll() is not None
        assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))


def _not_running(pid):
    deadline = time.monotonic() + 2
    while True:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return
        if state in {"Z", "X"}:
            return
        assert time.monotonic() < deadline, f"test worker descendant {pid} is still running"
        time.sleep(0.01)


@pytest.mark.parametrize("answer", [None, False, 0, {"finite": [3, -2.5]}, "large α\n" * 40000],
                         ids=["null", "false", "zero", "record", "larger-than-pipe"])
def test_direct_worker_round_trips_without_job_files(tmp_path, monkeypatch, answer):
    monkeypatch.setenv("BEHAVE_WORK_DIR", str(tmp_path))
    with worker._testing_job_runner(direct.run_direct_job):
        assert worker.run_job_strict("echo", {"answer": answer}, timeout_s=10) == answer
    assert list(tmp_path.iterdir()) == []


def test_direct_worker_retains_complete_failure():
    message = "fixture diagnostic α " * 2000 + "actual failure"
    with pytest.raises(worker.WorkerFailed) as caught:
        direct.run_direct_job("echo", {"raise": message}, timeout_s=10)
    assert caught.value.how == "crashed"
    assert message in caught.value.detail


@pytest.mark.parametrize("payload", [{"value": object()}, {"value": float("nan")},
                                     {"value": {1, 2}}])
def test_direct_worker_rejects_non_json_before_start(monkeypatch, payload):
    def unexpected(*args, **kwargs):
        pytest.fail("non-JSON job must not start a process")

    monkeypatch.setattr(direct.subprocess, "Popen", unexpected)
    with pytest.raises(worker.WorkerFailed) as caught:
        direct.run_direct_job("echo", payload, timeout_s=1)
    assert caught.value.how == "refused"


@pytest.mark.parametrize("output,how", [
    (b'not json', "no-answer"),
    (b'{"ok":true,"result":1,"result":2}', "no-answer"),
    (b'{"ok":true,"result":NaN}', "no-answer"),
    (b'{"ok":true}', "no-answer"),
    (b'{"ok":true,"result":0,"extra":1}', "no-answer"),
    (b'{"ok":1,"result":0}', "crashed"),
    (b'{"ok":false,"error":"fixture failure"}', "crashed"),
])
def test_direct_worker_checks_the_production_response_shape(monkeypatch, output, how):
    children = _program(monkeypatch, f"import os; os.write(1, {output!r})")
    with pytest.raises(worker.WorkerFailed) as caught:
        direct.run_direct_job("echo", {}, timeout_s=10)
    assert caught.value.how == how
    _reaped(children)


def test_direct_worker_nonzero_exit_keeps_stderr(monkeypatch):
    children = _program(monkeypatch, "import sys; sys.stderr.write('fixture crash'); sys.exit(7)")
    with pytest.raises(worker.WorkerFailed) as caught:
        direct.run_direct_job("echo", {}, timeout_s=10)
    assert caught.value.how == "crashed"
    assert "7" in caught.value.detail and "fixture crash" in caught.value.detail
    _reaped(children)


def test_direct_worker_stdout_noise_does_not_corrupt_a_response(monkeypatch):
    children = _program(monkeypatch, """
import os
from behave.sim import worker
def echo(payload):
    print('fixture print', flush=True)
    os.write(1, b'fixture raw stdout')
    return payload['answer']
worker.JOBS['echo'] = echo
worker.main(['--stdio'])
""")
    assert direct.run_direct_job("echo", {"answer": 42}, timeout_s=10) == 42
    _reaped(children)


def test_worker_releases_decoded_wire_before_entering_handler(monkeypatch):
    children = _program(monkeypatch, """
import sys
from types import SimpleNamespace
from behave.sim import worker
released = []
original = sys.stdin.buffer
class WireBytes(bytes):
    def __del__(self):
        released.append(True)
class Input:
    def read(self):
        return WireBytes(original.read())
sys.stdin = SimpleNamespace(buffer=Input())
worker.JOBS['echo'] = lambda payload: [bool(released), payload['answer']]
worker.main(['--stdio'])
""")
    answer = {"message": "retained decoded α", "values": [0, False, None, -3]}
    assert direct.run_direct_job("echo", {"answer": answer}, timeout_s=10) == [True, answer]
    _reaped(children)


@pytest.mark.parametrize("document", [b'{bad json', b'\xff'])
def test_worker_keeps_invalid_document_diagnostic(monkeypatch, document):
    children = _program(monkeypatch, f"""
import io, sys
from types import SimpleNamespace
from behave.sim import worker
sys.stdin = SimpleNamespace(buffer=io.BytesIO({document!r}))
worker.main(['--stdio'])
""")
    with pytest.raises(worker.WorkerFailed, match="the job document is not JSON"):
        direct.run_direct_job("echo", {}, timeout_s=10)
    _reaped(children)


@pytest.mark.parametrize("overrun", [False, True], ids=["completed", "timed-out"])
def test_direct_worker_reaps_descendants_even_after_leader_exit(tmp_path, monkeypatch, overrun):
    receipt = tmp_path / "child.json"
    children = _program(monkeypatch, f"""
import json, os, subprocess, sys, time
from pathlib import Path
from behave.sim import worker
def echo(payload):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    Path({str(receipt)!r}).write_text(json.dumps([os.getpid(), child.pid]))
    if {overrun!r}:
        time.sleep(30)
    return 42
worker.JOBS['echo'] = echo
worker.main(['--stdio'])
""")
    if overrun:
        with pytest.raises(worker.WorkerFailed) as caught:
            direct.run_direct_job("echo", {}, timeout_s=3)
        assert caught.value.how == "timeout"
    else:
        assert direct.run_direct_job("echo", {}, timeout_s=10) == 42
    _reaped(children)
    leader, descendant = json.loads(receipt.read_text())
    assert leader == children[0].pid
    _not_running(descendant)


def test_direct_worker_reaps_on_parent_interruption(monkeypatch):
    launch = direct.subprocess.Popen
    children = []

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    def start(*args, **kwargs):
        child = launch(*args, **kwargs)
        child.communicate = interrupted
        children.append(child)
        return child

    monkeypatch.setattr(direct.subprocess, "Popen", start)
    with pytest.raises(KeyboardInterrupt):
        direct.run_direct_job("echo", {"sleep_s": 30}, timeout_s=10)
    _reaped(children)


@pytest.mark.parametrize("available", [True, False])
def test_pytest_alone_selects_the_weaker_launcher(monkeypatch, available):
    from behave.isolation import IsolationRefusal
    from tests import conftest

    capability = object() if available else IsolationRefusal("unit-host", "test-only refusal")
    monkeypatch.setattr(conftest, "capabilities", lambda: capability)
    before = worker._TEST_JOB_RUNNER
    fixture = conftest._unit_isolation_backend.__wrapped__()
    next(fixture)
    try:
        assert worker._TEST_JOB_RUNNER is (before if available else direct.run_direct_job)
        if not available:
            assert worker.run_job_strict("echo", {"answer": 42}, timeout_s=10) == 42
    finally:
        with pytest.raises(StopIteration):
            next(fixture)
    assert worker._TEST_JOB_RUNNER is before


def test_direct_worker_uses_this_tree_before_ambient_pythonpath(monkeypatch):
    children = _program(monkeypatch, """
import os, behave
from behave.sim import worker
worker.JOBS['echo'] = lambda payload: [behave.__file__, os.environ['PYTHONPATH']]
worker.main(['--stdio'])
""")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent/unrelated-installation")
    module, path = direct.run_direct_job("echo", {}, timeout_s=10)
    assert Path(module).resolve() == Path(direct.behave.__file__).resolve()
    assert path.split(os.pathsep)[0] == str(Path(module).resolve().parents[1])
    _reaped(children)
