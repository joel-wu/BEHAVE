"""Shared foreground child waits retain the campaign cleanup contract."""

import signal
import subprocess
import sys
from threading import Event

import pytest

from behave.core import runtime_defaults
from behave.runtime import jobs


def test_campaign_entry_shares_foreground_child():
    from tools import eval_campaign

    assert eval_campaign._child is jobs.run_child


@pytest.mark.parametrize("code", [0, 7])
def test_foreground_child_appends_log_and_preserves_exit(tmp_path, code):
    log = tmp_path / "child.log"
    log.write_text("retained log\n")
    result = jobs.run_child([sys.executable, "-I", "-B", "-c",
        f"import sys;print('owned child');sys.exit({code})"], log, Event())
    assert result == code
    assert log.read_text() == "retained log\nowned child\n"


@pytest.mark.parametrize("expired,missing", [(False, False), (True, False), (False, True)])
def test_stop_terminates_only_owned_group_and_waits_for_cleanup(tmp_path, monkeypatch, expired, missing):
    calls = []
    monkeypatch.setattr(runtime_defaults, "JOB_CLEANUP_SECONDS", 19)

    class Child:
        pid = 12345

        def poll(self):
            return None

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            if expired and timeout is not None:
                raise subprocess.TimeoutExpired("owned child", timeout)
            return -9 if expired else -15

    def spawn(argv, **kwargs):
        assert argv == ["owned-fixture"]
        assert kwargs["start_new_session"] is True
        assert kwargs["stderr"] is subprocess.STDOUT
        assert kwargs["stdout"].name == str(tmp_path / "child.log")
        return Child()

    def kill(pid, sig):
        assert pid == Child.pid
        calls.append(("signal", sig))
        if missing:
            raise ProcessLookupError

    monkeypatch.setattr(jobs.subprocess, "Popen", spawn)
    monkeypatch.setattr(jobs.os, "killpg", kill)
    stopped = Event()
    stopped.set()
    assert jobs.run_child(["owned-fixture"], tmp_path / "child.log", stopped) == (-9 if expired else -15)
    expected = [("signal", signal.SIGTERM), ("wait", None if missing else 19)]
    if expired:
        expected.extend([("signal", signal.SIGKILL), ("wait", None)])
    assert calls == expected


def test_no_child_is_started_if_log_cannot_open(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("log must open first"))
    with pytest.raises(FileNotFoundError):
        jobs.run_child(["owned-fixture"], tmp_path / "absent/child.log", Event())
