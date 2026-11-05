"""Resource checks use live memory, and independent limits remain independent."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.isolation import _linux_child as child


@pytest.mark.parametrize("state,expected", [("Z", 65536), ("X", 65536), ("S", None)])
def test_pss_handles_an_exited_process_between_samples(tmp_path, monkeypatch, state, expected):
    proc = tmp_path / "proc"
    for pid in (2, 3):
        (proc / str(pid)).mkdir(parents=True)
    (proc / "2" / "smaps_rollup").write_text("Pss: 64 kB\n")
    # A zombie still has a proc directory but has released its address space.
    (proc / "3" / "stat").write_text(f"3 (a child) {state} 0 0 0\n")
    monkeypatch.setattr(child, "Path", lambda name: tmp_path / Path(name).relative_to("/"))
    assert child._proportional_rss([2, 3]) == expected


@pytest.mark.parametrize("cpu,elapsed,expected", [(0, 0, None), (11, 0, "cpu-limit"), (0, 9, "timed-out")])
def test_shared_pages_do_not_suppress_other_limits(monkeypatch, cpu, elapsed, expected):
    limits = {"processes": 2, "rss_bytes": 112, "cpu_seconds": 10, "wall_seconds": 8}
    monkeypatch.setattr(child, "_proportional_rss", lambda pids: 80)
    assert child._limit_reason([2, 3], 160, cpu, elapsed, limits) == expected


@pytest.mark.parametrize("pss", [None, 113])
def test_unverified_or_excessive_memory_still_fails_closed(monkeypatch, pss):
    limits = {"processes": 2, "rss_bytes": 112, "cpu_seconds": 10, "wall_seconds": 8}
    monkeypatch.setattr(child, "_proportional_rss", lambda pids: pss)
    assert child._limit_reason([2, 3], 160, 0, 0, limits) == "memory-limit"


@pytest.mark.parametrize("returncode, status_done", [(None, False), (None, True), (0, False), (0, True)])
def test_close_requires_reaped_launcher_and_finished_status_reader(tmp_path, returncode, status_done):
    from behave.isolation import IsolationCleanupError, SandboxProcess

    proc = object.__new__(SandboxProcess)
    proc._popen = SimpleNamespace(poll=lambda: returncode)
    proc._done = SimpleNamespace(is_set=lambda: status_done)
    root = tmp_path / "owned-workspace"
    root.mkdir()
    proc._root = str(root)
    closed = []
    proc.stdin = proc.stdout = proc.stderr = SimpleNamespace(close=lambda: closed.append(True))
    proc.kill = lambda **kwargs: None  # Model the bounded best-effort kill having returned.
    if returncode is None or not status_done:
        with pytest.raises(IsolationCleanupError, match="cleanup is unconfirmed") as caught:
            proc.close()
        assert f"launcher_returncode={returncode}" in str(caught.value)
        assert f"status_reader_finished={status_done}" in str(caught.value)
        assert not closed and root.exists()
    else:
        proc.close()
        assert len(closed) == 3 and not root.exists()
