"""Source/installed stop entries share one stdlib implementation; no Slurm calls."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from behave.train import job_stop as owned
from tools import slime_stop_owned as legacy


@pytest.mark.parametrize("entry", ["package", "legacy"])
def test_selected_checkout_helper_runs_without_site_packages(tmp_path, entry):
    source = tmp_path / "selected-checkout/src/behave/train/job_stop.py"
    source.parent.mkdir(parents=True)
    shutil.copyfile(owned.__file__, source)
    if entry == "legacy":
        shim = tmp_path / "selected-checkout/tools/slime_stop_owned.py"
        shim.parent.mkdir()
        shutil.copyfile(legacy.__file__, shim)
        source = shim
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(source), "--help"],
                            cwd=tmp_path, text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert "--wrapper-pid" in result.stdout and "--started-at" in result.stdout
    assert "--reason" in result.stdout


@pytest.mark.parametrize("matches", [0, 1, 2])
def test_descendant_selection_requires_exactly_one_matching_execute_child(tmp_path, monkeypatch, matches):
    commands = {100: b"wrapper", 101: b"unrelated", 102: b"unrelated", 103: b"unrelated"}
    expected = f"python\0execute(*sys.argv[1:])\0{tmp_path}\0/tmp/owned\0".encode()
    # 104 disappeared before inspection; 103 has an execute-shaped command but
    # belongs to a different job and must not be selected.
    commands[103] = expected.replace(str(tmp_path).encode(), b"/some/other/job")
    for pid in (101, 102)[:matches]:
        commands[pid] = expected
    children = {100: "101 103 104", 101: "102", 102: "", 103: ""}

    def read_bytes(path):
        assert str(path).startswith("/proc/") and path.name == "cmdline"
        pid = int(path.parts[2])
        if pid == 104:
            raise FileNotFoundError(path)
        return commands[pid]

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", lambda path: children[int(path.parts[2])])
    if matches == 1:
        assert owned.execute_child(tmp_path, {"pid": 100}, {"local_directory": "/tmp/owned"}) == 101
    else:
        with pytest.raises(RuntimeError, match="exactly one owned execute"):
            owned.execute_child(tmp_path, {"pid": 100}, {"local_directory": "/tmp/owned"})


@pytest.fixture
def owner(tmp_path, monkeypatch):
    started = "2026-10-17T01:00:00+00:00"
    node = {"state": "live", "host": "node", "pid": 100, "started_at": started}
    (tmp_path / "node_status.json").write_text(json.dumps(node))
    (tmp_path / "runtime.json").write_text(json.dumps({"host": "node", "local_directory": "/tmp/owned"}))
    monkeypatch.setattr(owned.socket, "gethostname", lambda: "node")
    monkeypatch.setattr(owned, "execute_child", lambda *args: 101)
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: (
        f"python\0execute(*sys.argv[1:])\0{tmp_path}\0/tmp/owned\0".encode()
        if str(path) == "/proc/101/cmdline" else read_bytes(path)))
    actions = []
    monkeypatch.setattr(owned.os, "pidfd_open", lambda pid: actions.append(("open", pid)) or 7, raising=False)
    monkeypatch.setattr(owned.os, "close", lambda fd: actions.append(("close", fd)))
    monkeypatch.setattr(owned.signal, "pidfd_send_signal",
                        lambda fd, sig: actions.append(("signal", fd, sig)), raising=False)
    return tmp_path, started, node, actions


@pytest.mark.parametrize(("key", "value"), [
    ("state", "stopped"), ("host", "other-node"), ("pid", 200),
    ("started_at", "2026-10-17T02:00:00+00:00"),
])
def test_changed_wrapper_never_opens_or_signals_process(owner, key, value):
    job, started, node, actions = owner
    (job / "node_status.json").write_text(json.dumps({**node, key: value}))
    with pytest.raises(RuntimeError, match="identity changed"):
        owned.stop(job, 100, started, "fixture")
    assert actions == [] and not (job / "operator_stop.json").exists()


def test_changed_process_after_pidfd_open_is_rejected_and_handle_closed(owner, monkeypatch):
    job, started, _, actions = owner
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"unrelated\0")
    with pytest.raises(RuntimeError, match="changed before stop"):
        owned.stop(job, 100, started, "fixture")
    assert actions == [("open", 101), ("close", 7)]
    assert not (job / "operator_stop.json").exists()


def test_existing_stop_request_is_not_overwritten_or_replayed(owner):
    job, started, _, actions = owner
    receipt = job / "operator_stop.json"
    receipt.write_text('{"reason": "prior intent"}\n')
    before = receipt.read_bytes()
    with pytest.raises(FileExistsError):
        owned.stop(job, 100, started, "fixture")
    assert actions == [("open", 101), ("close", 7)]
    assert receipt.read_bytes() == before


def test_failed_signal_retains_intent_not_a_cleanup_witness(owner, monkeypatch):
    job, started, _, actions = owner

    def signal_failed(*args):
        receipt = json.loads((job / "operator_stop.json").read_text())
        assert receipt["reason"] == "fixture" and receipt["files_removed"] is False
        assert "cleanup_confirmed" not in receipt
        raise ProcessLookupError("child already exited")

    monkeypatch.setattr(owned.signal, "pidfd_send_signal", signal_failed)
    with pytest.raises(ProcessLookupError):
        owned.stop(job, 100, started, "fixture")
    assert actions == [("open", 101), ("close", 7)]


def test_missing_pidfd_support_fails_before_reading_job(tmp_path, monkeypatch):
    monkeypatch.delattr(owned.os, "pidfd_open", raising=False)
    with pytest.raises(RuntimeError, match="pidfd support"):
        owned.stop(tmp_path / "absent", 100, "started", "fixture")
