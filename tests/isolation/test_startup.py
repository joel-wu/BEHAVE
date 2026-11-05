"""Startup admission tests, with real inherited descriptors and no sandbox bypass."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from behave.core import runtime_defaults as defaults
from behave.isolation import startup
from behave.isolation.preparation_control import PreparationCancelled, preparation_scope


@pytest.fixture(autouse=True)
def private_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "1")
    monkeypatch.setattr(startup, "isolation_startup_root", lambda: tmp_path / "startup")


def test_default_does_not_add_a_gate_to_cpu_admission(tmp_path, monkeypatch):
    monkeypatch.delenv("BEHAVE_ISOLATION_STARTUP_SLOTS")
    assert defaults.ISOLATION_STARTUP_SLOTS is None
    assert startup.startup_slots() is None
    first, second = startup.acquire_startup(), startup.acquire_startup()
    try:
        assert first.pass_fds == second.pass_fds == ()
        assert not (tmp_path / "startup").exists()
        with pytest.raises(TimeoutError):
            startup.acquire_startup(deadline=time.monotonic() - 1)
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("value,expected", [("4", 4), ("8", 8), ("none", None)])
def test_launcher_can_configure_startup_slots(monkeypatch, value, expected):
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", value)
    assert startup.startup_slots() == expected


@pytest.mark.parametrize("slots,expected", [(None, "none"), (1, "1"), (90, "90")])
def test_explicit_launch_policy_overrides_inherited_gate(monkeypatch, slots, expected):
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "4")
    env = startup.startup_environment(slots)
    assert env == {"BEHAVE_ISOLATION_STARTUP_SLOTS": expected}
    assert os.environ["BEHAVE_ISOLATION_STARTUP_SLOTS"] == "4"  # Pure encoder.
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", env["BEHAVE_ISOLATION_STARTUP_SLOTS"])
    assert startup.startup_slots() == slots


@pytest.mark.parametrize("slots", [0, -1, True, False, 1.5, "4", "none", "None"])
def test_launch_policy_rejects_non_json_integer_limits(slots):
    with pytest.raises(ValueError, match="positive integer or null"):
        startup.startup_environment(slots)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "", "true"])
def test_invalid_launcher_startup_slots(monkeypatch, value):
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", value)
    with pytest.raises(ValueError):
        startup.startup_slots()


def test_ready_releases_admission_without_waiting_for_command_exit():
    first = startup.acquire_startup()
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                             stdin=subprocess.PIPE, pass_fds=first.pass_fds)
    try:
        first.ready()
        second = startup.acquire_startup(deadline=time.monotonic() + 1)
        second.close()
        assert child.poll() is None
    finally:
        first.close()
        child.stdin.close()
        child.wait(timeout=5)


def test_unconfirmed_bootstrap_keeps_slot_after_parent_closes():
    first = startup.acquire_startup()
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                             stdin=subprocess.PIPE, pass_fds=first.pass_fds)
    try:
        first.close()
        with pytest.raises(TimeoutError, match="waiting for namespace startup"):
            startup.acquire_startup(deadline=time.monotonic() + .03)
    finally:
        child.stdin.close()
        child.wait(timeout=5)
    startup.acquire_startup(deadline=time.monotonic() + 1).close()


def test_cancelled_wait_does_not_release_another_owner():
    first = startup.acquire_startup()
    try:
        began = time.monotonic()
        with pytest.raises(startup.StartupCancelled):
            startup.acquire_startup(cancelled=lambda: time.monotonic() - began > .02)
        with pytest.raises(TimeoutError):
            startup.acquire_startup(deadline=time.monotonic() + .02)
    finally:
        first.close()


def test_preparation_scope_cancels_wait():
    first = startup.acquire_startup()
    def stop():
        raise PreparationCancelled()
    try:
        with preparation_scope(stop), pytest.raises(PreparationCancelled):
            startup.acquire_startup()
    finally:
        first.close()


def test_configured_slots_and_thread_contention(monkeypatch):
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "2")
    first, second = startup.acquire_startup(), startup.acquire_startup()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(startup.acquire_startup, deadline=time.monotonic() + 2)
            time.sleep(.03)
            assert not future.done()
            first.ready()
            future.result(timeout=2).close()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_invalid_runtime_configuration(bad, monkeypatch):
    monkeypatch.delenv("BEHAVE_ISOLATION_STARTUP_SLOTS")
    monkeypatch.setattr(defaults, "ISOLATION_STARTUP_SLOTS", bad)
    with pytest.raises(ValueError):
        startup.acquire_startup()


def test_explicitly_disabled_admission_still_checks_cancellation(monkeypatch):
    monkeypatch.delenv("BEHAVE_ISOLATION_STARTUP_SLOTS")
    monkeypatch.setattr(defaults, "ISOLATION_STARTUP_SLOTS", None)
    assert startup.acquire_startup().pass_fds == ()
    with pytest.raises(startup.StartupCancelled):
        startup.acquire_startup(cancelled=lambda: True)


def test_rejects_symlink_or_shared_directory(tmp_path):
    actual = tmp_path / "elsewhere"
    actual.mkdir()
    gate = tmp_path / "startup"
    gate.symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        startup.acquire_startup()


def test_rejects_shared_lock_file(tmp_path):
    gate = tmp_path / "startup"
    gate.mkdir(mode=0o700)
    lock = gate / "0"
    lock.touch(mode=0o600)
    os.chmod(lock, 0o666)
    with pytest.raises(PermissionError):
        startup.acquire_startup()
