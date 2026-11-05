"""Operator failures explain typed reasons without echoing private input bytes."""

import errno
import importlib
import os

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json


@pytest.fixture(params=["train", "fetch", "release", "trainer"])
def command(request, tmp_path):
    lane = request.param
    source = tmp_path / "input.json"
    objects = tmp_path / "objects"
    modules = {
        "train": "behave.train.__main__",
        "fetch": "behave.data.ingest.fetch_cli",
        "release": "behave.cli.release",
        "trainer": "behave.cli.trainer",
    }
    arguments = {
        "train": ["--plan", source, "--approved-manifest", source, "--task-objects", objects, "--dry-run"],
        "fetch": ["--plan", source, "--policy", source, "--dry-run"],
        "release": ["--request", source, "--objects", objects, "--dry-run"],
        "trainer": ["inspect", "--contract", source, "--head", source, "--objects", objects],
    }
    return lane, importlib.import_module(modules[lane]), list(map(str, arguments[lane])), source


@pytest.mark.parametrize("case", ["invalid-json", "missing", "not-regular", "fifo", "io-error", "unknown-io",
                                 "invalid-errno", "oversize-errno",
                                 "value-error", "type-error", "cancel", "record-error"])
def test_failures_keep_stdout_contract_and_safe_stderr(command, case, tmp_path, capsys, monkeypatch):
    lane, module, args, source = command
    default = "training" if lane == "train" else lane
    code = f"{default}-command-invalid"
    private = "operator-private-payload-canary"
    failure = None
    if case == "invalid-json":
        source.write_text(private)
        code, diagnostic = "record-json-invalid", "RecordError: invalid UTF-8 JSON record"
    elif case == "missing":
        diagnostic = f"FileNotFoundError: {os.strerror(errno.ENOENT)}"
    elif case == "not-regular":
        source.mkdir()
        diagnostic = f"IsADirectoryError: {os.strerror(errno.EISDIR)}"
    elif case == "fifo":
        os.mkfifo(source)
        diagnostic = "ValueError: invalid command input"
    elif case == "io-error":
        failure = OSError(errno.ENOSPC, private, private)
        diagnostic = f"OSError: {os.strerror(errno.ENOSPC)}"
    elif case == "unknown-io":
        failure = OSError(private)
        diagnostic = "OSError: I/O operation failed"
    elif case in {"invalid-errno", "oversize-errno"}:
        failure = OSError([private] if case == "invalid-errno" else 10 ** 100, private)
        diagnostic = "OSError: I/O operation failed"
    elif case in {"value-error", "type-error"}:
        failure = (ValueError if case == "value-error" else TypeError)(private)
        diagnostic = f"{type(failure).__name__}: invalid command input"
    elif case == "cancel":
        failure = AttemptInterrupted("fixture-cancelled")
        code, diagnostic = "fixture-cancelled", "AttemptInterrupted: fixture-cancelled"
    else:
        failure = RecordError("fixture-input-invalid", "selection exceeds the approved manifest")
        failure.__cause__ = RuntimeError(private)
        code, diagnostic = failure.code, f"RecordError: {failure}"
    if failure is not None:
        def fail(*args, **kwargs):
            raise failure
        monkeypatch.setattr(module, "read_regular_file", fail)
    assert module.main(args) == 1
    output = capsys.readouterr()
    expected = {"status": "unavailable", "code": code}
    if lane == "trainer":
        expected.update(schema="behave.trainer-command/1", action="inspect")
    assert parse_json(output.out.encode()) == expected
    assert output.err == diagnostic + "\n"
    assert private not in output.out + output.err
    assert not (tmp_path / "objects").exists()
    assert set(tmp_path.iterdir()) <= {source}


def test_unexpected_programming_errors_still_propagate(command, monkeypatch, capsys):
    _, module, args, _ = command

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected command bug")

    monkeypatch.setattr(module, "read_regular_file", fail)
    with pytest.raises(RuntimeError, match="unexpected command bug"):
        module.main(args)
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_trainer_retains_reconciliation_evidence_in_stdout(tmp_path, monkeypatch, capsys):
    from behave.cli import trainer

    receipt = BlobRef.of(b"recorded failure")
    workspace = tmp_path / "retained"

    def fail(args):
        error = RecordError("trainer-update-uncommitted", "update requires reconciliation")
        error.receipt = receipt
        error.workspace = workspace
        raise error

    monkeypatch.setattr(trainer, "_execute", fail)
    assert trainer.main(["inspect", "--contract", "contract", "--objects", "objects", "--head", "head"]) == 1
    output = capsys.readouterr()
    assert parse_json(output.out.encode()) == {
        "schema": "behave.trainer-command/1", "action": "inspect", "status": "unavailable",
        "code": "trainer-update-uncommitted", "receipt": receipt.as_dict(), "retained_workspace": str(workspace),
    }
    assert output.err == "RecordError: update requires reconciliation\n"
