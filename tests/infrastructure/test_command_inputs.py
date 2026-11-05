"""Operator inputs are regular files, not unsupervised streams or devices."""

import errno
import os
import subprocess
import sys

import pytest

from behave.core.records import parse_json

COMMANDS = [
    ("eval", "--plan", "INPUT", "--task-objects", "objects", "--run-directory", "run", "--dry-run"),
    ("train", "--plan", "INPUT", "--approved-manifest", "INPUT", "--task-objects", "objects",
     "--run-directory", "run", "--dry-run"),
    ("data", "--configuration", "INPUT", "--source-objects", "objects", "--run-directory", "run", "--dry-run"),
    ("data", "ingest", "--source-record", "INPUT", "--source-root", "source", "--objects", "objects", "--dry-run"),
    ("data", "fetch", "--plan", "INPUT", "--policy", "policy", "--run-directory", "run", "--dry-run"),
    ("release", "--request", "INPUT", "--objects", "objects", "--destination", "run", "--dry-run"),
    ("inspect", "--kind", "eval", "--record", "INPUT", "--objects", "objects"),
    ("trainer", "initialize", "--contract", "INPUT", "--state", "state", "--objects", "objects",
     "--run-directory", "run"),
]


@pytest.mark.parametrize("arguments", COMMANDS, ids=["eval", "train", "data", "ingest", "fetch",
                                                    "release", "inspect", "trainer"])
@pytest.mark.parametrize("kind", ["fifo", "directory", "malformed"])
def test_commands_refuse_non_records_before_startup(tmp_path, arguments, kind):
    path = tmp_path / "operator-private-canary"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"operator-private-canary")
    args = [str(path) if arg == "INPUT" else arg for arg in arguments]
    completed = subprocess.run([sys.executable, "-B", "-m", "behave.cli", *args], cwd=tmp_path,
                               capture_output=True, timeout=5, check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert parse_json(completed.stdout)["status"] == "unavailable"
    assert b"operator-private-canary" not in completed.stdout
    if arguments[0] == "inspect" or arguments[:2] == ("data", "--configuration"):
        # These operator commands report errors, including the operator's own
        # path for OS failures, but never echoes malformed file contents.
        assert completed.stderr
        if kind == "malformed":
            assert b"operator-private-canary" not in completed.stderr
    elif arguments[0] in {"train", "release", "trainer"} or arguments[:2] == ("data", "fetch"):
        expected = {
            "fifo": "ValueError: invalid command input",
            "directory": f"IsADirectoryError: {os.strerror(errno.EISDIR)}",
            "malformed": "RecordError: invalid UTF-8 JSON record",
        }
        assert completed.stderr == (expected[kind] + "\n").encode()
        assert b"operator-private-canary" not in completed.stderr
    else:
        assert completed.stderr == b""
    assert set(tmp_path.iterdir()) == {path}  # No run, CAS, lock or worker.


def test_native_endpoint_refuses_fifo_before_startup(tmp_path):
    path = tmp_path / "endpoint"
    os.mkfifo(path)
    program = """
import sys
from pathlib import Path
from behave.train.training_data import NativeChatVerifier
try:
    NativeChatVerifier.from_file(Path(sys.argv[1]))
except (ValueError, OSError):
    print('refused')
else:
    raise AssertionError('special file accepted')
"""
    completed = subprocess.run([sys.executable, "-B", "-c", program, str(path)],
                               capture_output=True, timeout=5, check=True)
    assert completed.stdout == b"refused\n" and completed.stderr == b""


@pytest.mark.parametrize("size", [0, 8, 9, 10000])
def test_regular_file_reads_all_bytes_and_allows_operator_symlinks(tmp_path, size):
    from behave.core.file_input import read_regular_file

    source = tmp_path / "record"
    source.write_bytes(b"a" * size)
    alias = tmp_path / "operator-alias"
    alias.symlink_to(source)
    for path in (source, alias):
        assert read_regular_file(path) == b"a" * size


def test_regular_file_refuses_devices(tmp_path):
    from behave.core.file_input import read_regular_file

    with pytest.raises(ValueError, match="regular file"):
        read_regular_file("/dev/zero")


def test_operator_can_explicitly_read_the_whole_regular_file(tmp_path):
    from behave.core.file_input import read_regular_file

    path = tmp_path / "profile"
    payload = b"x" * (33 << 10)
    path.write_bytes(payload)
    assert read_regular_file(path) == payload
    with pytest.raises(ValueError, match="regular file"):
        read_regular_file("/dev/zero")


@pytest.mark.parametrize("consumer", ["fetch", "native", "inspect"])
def test_valid_prefix_does_not_hide_invalid_full_input(tmp_path, capsys, consumer):
    from behave.cli.inspect import main
    from behave.core.artifacts import BlobRef
    from behave.core.records import RecordError, canonical_json
    from behave.data.ingest.fetch_cli import _read
    from behave.train.training_data import NativeChatVerifier

    path = tmp_path / "oversized"
    if consumer == "fetch":
        path.write_bytes(b"{}" + b" " * (1 << 20))
        assert _read(path) == b"{}"  # Whitespace size is not a record limit.
        path.write_bytes(path.read_bytes() + b"{}")
        with pytest.raises(RecordError) as error:
            _read(path)
        assert error.value.code == "record-json-invalid"
    elif consumer == "native":
        from test_native_chat_tokens import native

        endpoint, _ = native("sglang")
        path.write_bytes(canonical_json(endpoint.as_dict()) + b" " * (32 << 10) + b"{}")
        with pytest.raises(ValueError):
            NativeChatVerifier.from_file(path)
    else:
        path.write_bytes(canonical_json(BlobRef.of(b"record").as_dict()) + b" " * 4096 + b"{}")
        assert main(["--kind", "eval", "--record", str(path), "--objects", str(tmp_path / "absent")]) == 1
        assert parse_json(capsys.readouterr().out.encode())["code"] == "record-json-invalid"
