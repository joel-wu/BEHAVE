"""A ready status is received by its owner, without a reader-thread race."""

import io
import json
import os
import subprocess
import threading
from types import SimpleNamespace

import pytest

from behave.isolation import linux
from tests.isolation.test_policy import _spec


@pytest.fixture
def handle(tmp_path):
    descriptor, writer = os.pipe()
    popen = SimpleNamespace(
        pid=12345, stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
        poll=lambda: 0, wait=lambda timeout: 0)
    process = linux.SandboxProcess(
        popen, linux._StatusReader(descriptor), str(tmp_path / "owned"),
        _spec(), SimpleNamespace(identity="test"), text=False)
    try:
        yield process, writer
    finally:
        os.close(writer)
        process.close()


def publish(writer, status):
    os.write(writer, json.dumps(status).encode() + b"\n")


def test_ready_receipt_survives_an_unscheduled_background_reader(tmp_path, monkeypatch):
    # A long host-side pause may consume the cleanup allowance before a
    # background reader runs. The terminal bytes are nevertheless already here.
    monkeypatch.setattr(linux.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    descriptor, writer = os.pipe()
    process = linux.SandboxProcess(
        SimpleNamespace(pid=12345, stdin=io.BytesIO(), stdout=io.BytesIO(),
                        stderr=io.BytesIO(), poll=lambda: 0, wait=lambda timeout: 0),
        linux._StatusReader(descriptor), str(tmp_path / "owned"), _spec(),
        SimpleNamespace(identity="test"), text=False)
    try:
        publish(writer, {"kind": "finished", "status": "completed", "exit_code": 0})
        process.close(deadline=0)
        assert process.poll() == 0
        assert process.final_status == "completed"
        assert process.status_refusal is None
    finally:
        process._finish_status()
        os.close(writer)
        process.close()


def test_nonblocking_poll_keeps_an_incomplete_receipt_open(handle):
    process, writer = handle
    process._popen.poll = lambda: None
    os.write(writer, b'{"kind":"finished",')
    assert process.poll() is None
    assert not process._done.is_set()
    assert process.status_refusal is None
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=0)
    os.write(writer, b'"status":"exited","exit_code":7}\n')
    process._popen.poll = lambda: 0
    assert process.wait(timeout=0) == 7
    assert process.final_status == "exited"


def test_ready_receipt_is_consumed_even_after_cleanup_deadline(handle):
    process, writer = handle
    publish(writer, {"kind": "finished", "status": "completed", "exit_code": 0})
    process.close(deadline=0)
    assert process._done.is_set()
    assert process.stdout.closed


def test_live_status_writer_does_not_become_a_cleanup_receipt(handle):
    process, writer = handle
    with pytest.raises(linux.IsolationCleanupError, match="cleanup is unconfirmed"):
        process.close(deadline=0)
    assert not process.stdout.closed
    publish(writer, {"kind": "finished", "status": "completed", "exit_code": 0})
    process.close(deadline=0)
    assert process.stdout.closed


def test_concurrent_poll_does_not_take_over_a_blocking_reader(handle):
    process, writer = handle
    entered = threading.Event()
    read = process._status_reader.read_line

    def read_line(timeout):
        entered.set()
        return read(timeout)

    process._status_reader.read_line = read_line
    process._popen.poll = lambda: None
    reader = threading.Thread(target=lambda: process.wait(timeout=5))
    reader.start()
    try:
        assert entered.wait(5)
        assert process.poll() is None
        publish(writer, {"kind": "finished", "status": "completed", "exit_code": 0})
    finally:
        reader.join(5)
    assert not reader.is_alive()
    process._popen.poll = lambda: 0
    assert process.poll() == 0
