"""Public operator lifecycle; fixture updates are not a model-training claim."""

import os
import signal
import subprocess
import sys
from dataclasses import replace
from io import BytesIO

import pytest
from test_native_chat_tokens import native_training_run
from test_trainer_process import initial_state, numerical, process_spec  # noqa: F401
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401
from test_training_checkpoints import state_at
from test_weight_publication import with_weights

from behave.agent.journal import AttemptJournal, load_chain
from behave.agent.run_store import JournalDirectory
from behave.cli.trainer import main
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.train.checkpoint_storage import load_weights
from behave.train.checkpoints import copy_state_file, load_checkpoint
from behave.train.training_data import NativeChatVerifier
from behave.train.update_records import ExampleUse, TrainerContract, UpdateRequest


def record(tmp_path, name, value):
    path = tmp_path / name
    path.write_bytes(value if isinstance(value, bytes) else canonical_json(value))
    return path


def cold(*args, success=True):
    result = subprocess.run([sys.executable, "-B", "-m", "behave.cli", "trainer", *map(str, args)],
                            capture_output=True, timeout=90, check=False)
    assert result.returncode == (0 if success else 1), result.stderr + result.stdout
    assert bool(result.stderr) == (not success)
    assert b"operator-private-canary" not in result.stdout + result.stderr
    assert b"private-operator-canary" not in result.stdout + result.stderr
    row = parse_json(result.stdout)
    assert row["schema"] == "behave.trainer-command/1"
    return row


def local(capsys, *args, success=True):
    assert main(list(map(str, args))) == (0 if success else 1)
    output = capsys.readouterr()
    assert bool(output.err) == (not success)
    assert "operator-private-canary" not in output.out + output.err
    assert "private-operator-canary" not in output.out + output.err
    return parse_json(output.out.encode())


def inputs(tmp_path, source, contract, state):
    run = tmp_path / "trainer"
    contract_path = record(tmp_path, "contract.json", contract.to_bytes())
    state_path = record(tmp_path, "state.json", state.to_bytes())
    common = ("--contract", contract_path, "--run-directory", run)
    initialize = (*common, "--objects", source.directory, "--state", state_path)
    return run, contract_path, common, initialize


@pytest.mark.parametrize("components", [("model", "optimizer", "rng", "sampler"), ("model", "rng", "sampler", "archive")])
def test_cold_initialize_inspect_export_restore_and_publish(tmp_path, selection_fixture, components):  # noqa: F811
    source, contract, state, _, _ = selection_fixture
    contract = replace(contract, components=(*components, "serving"))
    state = state_at(source, components=components)
    state, payloads = with_weights(source, state)
    run, contract_path, common, initialize = inputs(tmp_path, source, contract, state)
    first = cold("initialize", *initialize)
    assert first["status"] == "committed" and first["pending"] is None
    assert first["policy"] == state.policy.as_dict()
    assert cold("initialize", *initialize, success=False)["status"] == "unavailable"
    exported = tmp_path / "export"
    assert cold("export", *common, "--destination", exported)["head"] == first["head"]
    head_path = record(tmp_path, "head.json", first["head"])
    readonly = ("--contract", contract_path, "--objects", exported, "--head", head_path)
    verified = cold("inspect", *readonly)
    assert verified["checkpoint"] == first["checkpoint"] and verified["status"] == "verified"
    restored = cold("restore", *readonly, "--run-directory", tmp_path / "restored", "--fenced")
    assert restored["checkpoint"] == first["checkpoint"] and restored["status"] == "restored"
    destination = tmp_path / "published"
    result = cold("publish", *readonly, "--destination", destination, "--component", "serving")
    publication, files = load_weights(DirectoryStore(destination), BlobRef.from_dict(result["publication"]),
                                     checkpoint=BlobRef.from_dict(first["head"]), contract=contract, component="serving")
    assert publication.policy == state.policy and {f.path for f in files} == set(payloads)
    assert len(JournalDirectory(run).journal("optimizer").load()) == 1  # No native dispatch.
    wrong = record(tmp_path, "wrong-contract.json", replace(contract, adapter="other/1").to_bytes())
    assert cold("inspect", "--contract", wrong, "--objects", exported, "--head", head_path,
                success=False)["status"] == "unavailable"


@pytest.mark.parametrize("kind", ["fifo", "directory", "oversize", "malformed"])
def test_bounded_operator_inputs_do_not_start_a_run_or_echo_contents(tmp_path, selection_fixture, kind):  # noqa: F811
    source, contract, state, _, _ = selection_fixture
    run, contract_path, _, initialize = inputs(tmp_path, source, contract, state)
    contract_path.unlink()
    if kind == "fifo":
        os.mkfifo(contract_path)
    elif kind == "directory":
        contract_path.mkdir()
    else:
        contract_path.write_bytes(b'operator-private-canary' + (b"x" * (1 << 20) if kind == "oversize" else b""))
    result = cold("initialize", *initialize, success=False)
    assert result["status"] == "unavailable" and not run.exists()
    assert b"operator-private-canary" not in canonical_json(result)
    if kind == "oversize":
        assert result["code"] == "record-json-invalid"


def test_restore_requires_explicit_operator_fencing_before_opening_any_run(tmp_path):
    run = tmp_path / "must-not-exist"
    result = subprocess.run([sys.executable, "-B", "-m", "behave.cli", "trainer", "restore",
                             "--contract", "absent", "--head", "absent", "--objects", "absent",
                             "--run-directory", str(run)], capture_output=True, timeout=20, check=False)
    assert result.returncode == 2 and b"requires --fenced" in result.stderr and not run.exists()


def test_update_restores_command_handlers_after_cancellation(tmp_path, numerical, capsys, monkeypatch):  # noqa: F811
    from behave.agent.attempt import AttemptInterrupted
    from behave.train import trainer as trainer_module

    source, contract, state, request = numerical
    _, _, common, initialize = inputs(tmp_path, source, contract, state)
    first = local(capsys, 'initialize', *initialize)
    request_path = record(tmp_path, 'request.json', request(BlobRef.from_dict(first['checkpoint'])).to_bytes())
    process_path = record(tmp_path, 'trainer.json', process_spec().as_dict())
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    calls = []

    def update(*args, cancelled):
        calls.append(args)
        assert not cancelled()
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert cancelled()
        raise AttemptInterrupted('fixture-update-cancelled')

    monkeypatch.setattr(trainer_module, 'execute_update', update)
    row = local(capsys, 'update', *common, '--objects', source.directory,
                '--request', request_path, '--process', process_path, success=False)
    assert row == {'schema': 'behave.trainer-command/1', 'action': 'update',
                   'status': 'unavailable', 'code': 'fixture-update-cancelled'}
    assert len(calls) == 1
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers


@pytest.mark.isolation
def test_failed_native_command_remains_pending_and_retry_does_not_dispatch(tmp_path, numerical, capsys, monkeypatch):  # noqa: F811
    source, contract, state, request = numerical
    contract = replace(contract, configuration=source.put(canonical_json({"mode": "crash"})))
    run, _, common, initialize = inputs(tmp_path, source, contract, state)
    first = local(capsys, "initialize", *initialize)
    request_path = record(tmp_path, "request.json", request(BlobRef.from_dict(first["checkpoint"])).to_bytes())
    process_path = record(tmp_path, "trainer.json", process_spec().as_dict())
    args = (*common, "--objects", source.directory, "--request", request_path, "--process", process_path)
    failed = local(capsys, "update", *args, success=False)
    assert failed["code"] == "trainer-update-uncommitted" and failed["receipt"]
    from behave.isolation import service

    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("pending update redispatched"))
    assert local(capsys, "update", *args, success=False)["code"] == "training-update-outcome-unknown"
    assert local(capsys, "reconcile", *common, "--request", request_path, success=False)["status"] == "unavailable"
    exported = local(capsys, "export", *common, "--destination", tmp_path / "pending-export")
    assert exported["pending"]["receipt"] == failed["receipt"]
    assert exported["checkpoint"] == first["checkpoint"]
    assert len([e for e in JournalDirectory(run).journal("optimizer").load() if e.kind == "trainer_dispatched"]) == 1


@pytest.mark.isolation
def test_lost_return_ack_is_reconciled_without_another_optimizer_call(tmp_path, numerical, capsys, monkeypatch):  # noqa: F811
    source, contract, state, request = numerical
    run, _, common, initialize = inputs(tmp_path, source, contract, state)
    first = local(capsys, "initialize", *initialize)
    request_path = record(tmp_path, "request.json", request(BlobRef.from_dict(first["checkpoint"])).to_bytes())
    process_path = record(tmp_path, "trainer.json", process_spec().as_dict())
    args = (*common, "--objects", source.directory, "--request", request_path, "--process", process_path)
    append = AttemptJournal.append

    def lose_ack(self, expected, kind, payload, artifacts=()):
        result = append(self, expected, kind, payload, artifacts)
        if kind == "trainer_returned":
            raise OSError("private-operator-canary")
        return result

    monkeypatch.setattr(AttemptJournal, "append", lose_ack)
    assert local(capsys, "update", *args, success=False)["code"] == "trainer-command-invalid"
    monkeypatch.setattr(AttemptJournal, "append", append)
    from behave.isolation import service

    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("reconciliation dispatched"))
    assert local(capsys, "update", *args, success=False)["code"] == "training-update-outcome-unknown"
    committed = local(capsys, "reconcile", *common, "--request", request_path)
    assert committed["pending"] is None and committed["policy"] != first["policy"]
    # Retry after the commit is idempotent even if the executable is now absent.
    process_path.write_bytes(canonical_json(replace(process_spec(), argv=("/must/not/execute",)).as_dict()))
    assert local(capsys, "update", *args)["checkpoint"] == committed["checkpoint"]
    checkpoint, pending = load_checkpoint(JournalDirectory(run).objects, BlobRef.from_dict(committed["head"]),
                                         contract=contract)
    assert checkpoint.state.cursor["step"] == 1 and pending is None


@pytest.mark.isolation
def test_cold_cli_updates_use_real_sealed_rollout_and_resume_numeric_state(tmp_path, publication):
    # Fixture HTTP response tokens, real isolated rollout, full report verification,
    # real scalar trainer processes. No monkeypatched evidence or LLM/GPU claim.
    rollout, manifest, _, summary, endpoint = native_training_run(tmp_path / "rollout", publication)
    source = DirectoryStore(tmp_path / "source")
    report, closure = rollout.seal("training")
    source.publish(rollout.objects, closure)
    contract = TrainerContract("numeric-command-fixture/1", source.put(canonical_json({"learning_rate": 0.05})), manifest)
    state = replace(initial_state(source), policy=endpoint.tokens.checkpoint)
    _, contract_path, common, initialize = inputs(tmp_path, source, contract, state)
    endpoint_path = record(tmp_path, "endpoint.json", endpoint.as_dict())
    history = ("--native-endpoint", endpoint_path)
    first = cold("initialize", *initialize, *history)
    process_path = record(tmp_path, "trainer.json", process_spec(wall_seconds=30).as_dict())
    uses = tuple(ExampleUse(report, 0, turn, "epoch-0") for turn in (0, 2))
    update = UpdateRequest("step-0", BlobRef.from_dict(first["checkpoint"]), uses, source.put(b"fixture decision"))
    request_path = record(tmp_path, "request.json", update.to_bytes())
    args = (*common, "--objects", source.directory, "--request", request_path, "--process", process_path, *history)
    second = cold("update", *args)
    assert second["policy"] != first["policy"] and second["pending"] is None
    assert cold("update", *args)["checkpoint"] == second["checkpoint"]
    cold_objects = tmp_path / "cold-export"
    assert cold("export", *common, *history, "--destination", cold_objects)["head"] == second["head"]
    head_path = record(tmp_path, "head.json", second["head"])
    readonly = ("--contract", contract_path, "--objects", cold_objects, "--head", head_path, *history)
    assert cold("inspect", *readonly)["checkpoint"] == second["checkpoint"]
    restored = tmp_path / "restored"
    assert cold("restore", *readonly, "--run-directory", restored, "--fenced")["checkpoint"] == second["checkpoint"]
    update = replace(update, key="step-1", parent=BlobRef.from_dict(second["checkpoint"]),
                     uses=tuple(replace(use, exposure="epoch-1") for use in uses))
    request_path.write_bytes(update.to_bytes())
    third = cold("update", "--contract", contract_path, "--run-directory", restored,
                 "--objects", source.directory, "--request", request_path, "--process", process_path, *history)
    checkpoint, pending = load_checkpoint(JournalDirectory(restored).objects, BlobRef.from_dict(third["head"]),
                                         contract=contract, verifier=NativeChatVerifier(endpoint))
    assert checkpoint.state.cursor["step"] == 2 and pending is None
    parts = {}
    for file in checkpoint.state.files:
        data = BytesIO()
        copy_state_file(JournalDirectory(restored).objects, file, data)
        parts[file.component] = parse_json(data.getvalue())
    assert parts["optimizer"]["steps"] == parts["sampler"]["position"] == 2
    assert len([e for e in load_chain(JournalDirectory(restored).objects, BlobRef.from_dict(third["head"]))
                if e.kind == "trainer_dispatched"]) == 2
    assert summary["eligible_turns"] == 3  # The length-finished middle turn keeps its sampled tokens.
