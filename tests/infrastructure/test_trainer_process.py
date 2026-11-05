"""Real process dispatch plus crash/receipt injection; no GRPO assumption."""

import shutil
import sys
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401 -- pytest fixture registration

from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal, load_chain
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.isolation import service
from behave.train.checkpoints import (
    TrainingLedger,
    TrainingState,
    copy_state_file,
    load_checkpoint,
    restore_ledger,
    store_state_file,
)
from behave.train.trainer import (
    BackendUpdate,
    TrainerExecutionError,
    TrainerProcess,
    commit_recorded_update,
    execute_update,
    invocation_bytes,
    receipt_bytes,
    response_bytes,
    response_state,
    verify_invocation,
    verify_receipt,
)
from behave.train.training_data import TokenTrace
from behave.train.update_records import ExampleUse, TrainerContract, UpdateRequest


def initial_state(store):
    parts = {
        "model": {"weight": 0.25},
        "optimizer": {"velocity": 0.0, "steps": 0},
        "rng": {"value": 314},
        "sampler": {"position": 0},
    }
    files = tuple(
        store_state_file(
            BytesIO(canonical_json(value)), store, component=component, path=f"{component}.json", chunk_bytes=11
        )
        for component, value in parts.items()
    )
    return TrainingState(BlobRef.of(canonical_json(parts["model"])), files, {"step": 0})


def process_spec(**changes):
    return TrainerProcess(
        (sys.executable, "-B", str(Path(__file__).with_name("support") / "trainer_fixture.py")),
        {"PYTHONPATH": str(Path(sys.modules["behave"].__file__).resolve().parent.parent),
         "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        **{"wall_seconds": 20, **changes},
    )


@pytest.fixture
def numerical(selection_fixture):  # noqa: F811 -- pytest fixture injection
    source, contract, _, request, report = selection_fixture
    contract = replace(contract, configuration=source.put(canonical_json({"learning_rate": 0.05})))
    ref = contract.configuration
    report["candidates"][0]["examples"]["turns"][0]["tokens"] = TokenTrace(
        "fixture/1", ref, ref, ref, ref, (1, 2), (10, 11, 12), (1, 0, 1), {}
    ).as_dict()
    return source, contract, initial_state(source), request


@pytest.mark.parametrize(
    "changes",
    [
        {"argv": ("relative",)},
        {"argv": ("/bin/true", "\0")},
        {"environment": {"A=B": "x"}},
        {"environment": {"X": 1}},
        {"wall_seconds": True},
        {"cleanup_seconds": 0},
    ],
)
def test_process_configuration_is_explicit_and_bounded(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(process_spec(), **changes)


@pytest.mark.parametrize("unlimited", [False, True])
def test_trainer_stages_and_passes_native_scratch_under_the_selected_root(tmp_path, numerical, monkeypatch, unlimited):
    source, contract, initial, request = numerical
    selected = tmp_path / "operator-local"
    selected.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path / "unused-parent"))
    process = process_spec(**({"wall_seconds": None, "cleanup_seconds": None} if unlimited else {}))
    process = replace(process, environment={**process.environment, "BEHAVE_SCRATCH_DIR": str(selected)})
    seen = []

    def backend(*args, **kwargs):
        assert kwargs["cleanup_seconds"] == process.cleanup_seconds
        assert (kwargs["deadline"] is None) == unlimited
        workspace = kwargs["cwd"]
        assert workspace.parent == selected
        assert kwargs["environment"]["TMPDIR"] == kwargs["environment"]["BEHAVE_SCRATCH_DIR"] == str(workspace)
        seen.append(workspace)
        wire = parse_json(kwargs["input_data"])
        output = DirectoryStore(wire["output"])
        output.publish(source, initial.references)
        return service.ServiceResult("exited", 0, response_bytes(BlobRef.of(update.to_bytes()), initial), b"", 0.1)

    monkeypatch.setattr(service, "run_service", backend)
    with TrainingLedger(JournalDirectory(tmp_path / "ledger"), contract) as ledger:
        before = ledger.initialize(initial, source)
        update = request(before.reference)
        execute_update(ledger, update, source, process)
    assert len(seen) == 1 and not seen[0].exists()
    retained, = selected.glob("retained-trainer-*/*")
    assert retained.name == seen[0].name and (retained / "input").is_dir()


def test_trainer_accepts_unlimited_runtime_and_default_cleanup():
    process = process_spec(wall_seconds=None)
    assert process.cleanup_seconds is None
    assert TrainerProcess.from_dict(process.as_dict()) == process


@pytest.mark.parametrize("directories", [("relative", "/tmp/output"), ("/tmp/same", "/tmp/same"), (1, "/tmp/output")])
def test_backend_rejects_ambiguous_directories(monkeypatch, directories):
    payload = canonical_json(
        {"invocation": BlobRef.of(b"unused").as_dict(), "input": directories[0], "output": directories[1]}
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(payload)))
    with pytest.raises(RecordError, match="distinct absolute"):
        BackendUpdate.read()


def test_backend_reads_complete_envelopes(monkeypatch):
    payload = b" " * (17 << 20) + b"{}"
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(payload)))
    monkeypatch.setattr(BackendUpdate, "from_envelope", lambda data: data)
    assert BackendUpdate.read() == payload


def test_complete_large_example_reaches_backend_without_a_second_quota(tmp_path, numerical):
    source, contract, initial, request = numerical
    example = {"messages": [{"role": "assistant", "content": "x" * ((32 << 20) + 1)}]}
    pending = SimpleNamespace(request=request(BlobRef.of(b"parent")), examples=(example,))
    checkpoint = SimpleNamespace(state=initial)
    process = process_spec()
    data = invocation_bytes(contract, pending, checkpoint, process)
    ref = source.put(data)
    source.put(canonical_json(example))
    assert verify_invocation(source, ref, contract, pending, checkpoint)[0] == process
    backend = BackendUpdate.from_envelope(canonical_json({
        "invocation": ref.as_dict(), "input": str(source.directory), "output": str(tmp_path / "output")}))
    assert list(backend.iter_examples()) == [example]


@pytest.mark.parametrize("failure", ["world", "invocation", "staging", "cancelled"])
def test_pre_dispatch_failure_does_not_leave_pending_update(tmp_path, numerical, monkeypatch, failure):
    from behave.train import trainer as execution
    from behave.train.trainer import DistributedLaunch

    source, contract, initial, request = numerical
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        update = request(checkpoint.reference)
        with monkeypatch.context() as patch:
            spec = process_spec()
            if failure == "world":
                spec = replace(spec, distributed=DistributedLaunch(sys.executable, 2))
            elif failure == "invocation":
                def invalid_invocation(*args):
                    raise RecordError("trainer-invocation-invalid", "injected malformed invocation")
                patch.setattr(execution, "invocation_bytes", invalid_invocation)
            elif failure == "staging":
                def failed_staging(*args, **kwargs):
                    raise OSError("injected staging creation failure")
                patch.setattr(execution, "mkdtemp", failed_staging)
            def must_not_launch(*args, **kwargs):
                pytest.fail("preflight failure must not launch a trainer")
            patch.setattr(execution.service, "run_service", must_not_launch)
            with pytest.raises((RecordError, OSError, AttemptInterrupted)):
                execute_update(ledger, update, source, spec, cancelled=lambda: failure == "cancelled")
        assert ledger.pending is None
        assert ledger.checkpoint == checkpoint
        head, _ = ledger.seal()
        assert [event.kind for event in load_chain(run.objects, head)] == ["trainer_started"]
    # Corrected configuration can use the same explicit key, including cold resume.
    with TrainingLedger(run, contract) as ledger:
        assert ledger.begin(update, source).request == update


@pytest.mark.isolation
def test_verbose_successful_trainer_is_not_rejected_by_a_log_quota(tmp_path, numerical):
    source, contract, initial, request = numerical
    contract = replace(contract, configuration=source.put(canonical_json(
        {"learning_rate": 0.05, "mode": "verbose"})))
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        before = ledger.initialize(initial, source)
        after = execute_update(ledger, request(before.reference), source, process_spec())
        assert after.state != before.state
        assert ledger.pending is None


@pytest.mark.isolation
def test_numeric_checkpoint_cold_resume_matches_uninterrupted_updates(tmp_path, numerical):
    source, contract, initial, request = numerical
    process = process_spec()
    uninterrupted = JournalDirectory(tmp_path / "uninterrupted")
    with TrainingLedger(uninterrupted, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        for step in range(4):
            update = request(checkpoint.reference, key=f"update-{step}", exposure=f"epoch-{step}")
            checkpoint = execute_update(ledger, update, source, process)
        expected = checkpoint.state
    original = JournalDirectory(tmp_path / "original")
    with TrainingLedger(original, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        for step in range(2):
            update = request(checkpoint.reference, key=f"update-{step}", exposure=f"epoch-{step}")
            checkpoint = execute_update(ledger, update, source, process)
        head, refs = ledger.seal()
    cold = DirectoryStore(tmp_path / "export")
    cold.publish(original.objects, refs)
    restored = JournalDirectory(tmp_path / "restored")
    assert restore_ledger(cold, head, run=restored, contract=contract) == (checkpoint, None)
    with TrainingLedger(restored, contract) as ledger:
        # Even a changed command must not execute an already committed update.
        assert execute_update(ledger, update, source, replace(process, argv=("/must/not/launch",))) == checkpoint
        assert execute_update(ledger, update, source, process,
                              cancelled=lambda: True) == checkpoint
        for step in range(2, 4):
            update = request(checkpoint.reference, key=f"update-{step}", exposure=f"epoch-{step}")
            checkpoint = execute_update(ledger, update, source, process)
        assert checkpoint.state == expected
        assert checkpoint.state.policy != initial.policy and checkpoint.state.cursor["step"] == 4
        values = {}
        for file in checkpoint.state.files:
            stream = BytesIO()
            copy_state_file(restored.objects, file, stream)
            values[file.component] = parse_json(stream.getvalue())
        # Independent four-step numeric baseline, including masked-out token 11,
        # optimizer momentum, evolving RNG and sampler position.
        assert values["model"]["weight"] == pytest.approx(0.24733475563453772, abs=1e-15)
        assert values["optimizer"]["velocity"] == pytest.approx(0.020336590407297742, abs=1e-15)
        assert values["optimizer"]["steps"] == values["sampler"]["position"] == 4
        assert values["rng"]["value"] == 1249860366
        head, _ = ledger.seal()
    assert load_checkpoint(restored.objects, head, contract=contract) == (checkpoint, None)
    assert [e.kind for e in load_chain(restored.objects, head)].count("trainer_dispatched") == 4


@pytest.mark.parametrize("attack", ["reward", "tokens", "group", "parent", "configuration", "missing"])
def test_dispatch_cannot_change_verified_examples_or_parent(tmp_path, numerical, attack):
    source, contract, initial, request = numerical
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        ticket = ledger.begin(request(checkpoint.reference), source)
        data = parse_json(invocation_bytes(contract, ticket, checkpoint, process_spec()))
        example = parse_json(canonical_json(ticket.examples[0]))
        if attack == "reward":
            example["reward"] = 1
        elif attack == "tokens":
            example["tokens"]["response_mask"] = [1, 1, 1]
        elif attack == "group":
            example["group_id"] = "fake-group"
        elif attack == "parent":
            data["request"]["parent"] = BlobRef.of(b"different-parent").as_dict()
        elif attack == "configuration":
            data["contract"]["configuration"] = BlobRef.of(b"different-config").as_dict()
        if attack != "missing":
            data["examples"] = [source.put(canonical_json(example)).as_dict()]
        invocation = source.put(canonical_json(data))
        with pytest.raises(RecordError):
            ledger.record_dispatch(invocation, source)
        assert ledger.pending == ticket


@pytest.mark.isolation
@pytest.mark.parametrize(
    "mode",
    [
        "crash",
        "sleep",
        "invalid_response",
        "wrong_request",
        "missing_chunk",
        "missing_component",
        "empty_state",
    ],
)
def test_backend_failure_is_pending_not_zero_reward_or_automatic_retry(tmp_path, numerical, mode):
    source, contract, initial, request = numerical
    contract = replace(contract, configuration=source.put(canonical_json({"learning_rate": 0.05, "mode": mode})))
    run = JournalDirectory(tmp_path / "trainer")
    spec = process_spec(wall_seconds=1 if mode == "sleep" else 20)
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        update = request(checkpoint.reference)
        with pytest.raises(TrainerExecutionError) as failure:
            execute_update(ledger, update, source, spec)
        assert ledger.checkpoint == checkpoint and ledger.pending.receipt == failure.value.receipt
        assert failure.value.workspace is None
        result, _ = verify_receipt(run.objects, ledger.pending.receipt, ledger.pending.invocation)
        if mode == "crash":
            assert result.returncode == 7 and b"fixture backend failed" in result.stderr
        if mode == "sleep":
            assert result.status == "timeout"
        if mode == "empty_state":
            assert result.status == "exited" and result.returncode == 0
            assert parse_json(result.stdout)["state"]["files"] == []
            assert result.stderr == b"fixture returned empty state\n"
        head, refs = ledger.seal()
    cold = DirectoryStore(tmp_path / "export")
    cold.publish(run.objects, refs)
    assert load_checkpoint(cold, head, contract=contract)[1].receipt == failure.value.receipt
    with TrainingLedger(run, contract) as ledger:
        with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
            execute_update(ledger, update, source, spec)
        with pytest.raises(RecordError):
            ledger.commit(ledger.pending.reference, initial, source)


def test_uncertain_supervisor_exit_retains_staging_and_refuses_new_launch(tmp_path, numerical, monkeypatch):
    source, contract, initial, request = numerical
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: (_ for _ in ()).throw(OSError("secret-token")))
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        with pytest.raises(TrainerExecutionError) as failure:
            execute_update(ledger, request(checkpoint.reference), source, process_spec())
        workspace = failure.value.workspace
        try:
            assert workspace.is_dir()
            result, _ = verify_receipt(run.objects, ledger.pending.receipt, ledger.pending.invocation)
            assert result.status == "supervisor-error" and result.stderr == b""
            assert result.cleanup_state.value == "unknown"
        finally:
            shutil.rmtree(workspace)  # Mock never started a process; test owns this directory.


@pytest.mark.parametrize("kind", ["trainer_dispatched", "trainer_returned", "update_committed"])
def test_lost_journal_ack_never_redispatches(tmp_path, numerical, monkeypatch, kind):
    source, contract, initial, request = numerical
    run = JournalDirectory(tmp_path / "trainer")
    append = AttemptJournal.append
    calls = []

    def lose_ack(self, expected, event_kind, payload, artifacts=()):
        result = append(self, expected, event_kind, payload, artifacts)
        if event_kind == kind:
            raise OSError("lost acknowledgement")
        return result

    def backend(*args, **kwargs):
        calls.append(1)
        wire = parse_json(kwargs["input_data"])
        output = DirectoryStore(wire["output"])
        output.publish(source, initial.references)
        return service.ServiceResult("exited", 0, response_bytes(BlobRef.of(update.to_bytes()), initial), b"", 0.1)

    monkeypatch.setattr(service, "run_service", backend)
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        update = request(checkpoint.reference)
        monkeypatch.setattr(AttemptJournal, "append", lose_ack)
        with pytest.raises((OSError, TrainerExecutionError)):
            execute_update(ledger, update, source, process_spec())
        with pytest.raises(AttemptInterrupted, match="journal-outcome-unknown"):
            ledger.seal()
    monkeypatch.setattr(AttemptJournal, "append", append)
    with TrainingLedger(run, contract) as ledger:
        if kind == "update_committed":
            assert execute_update(ledger, update, source, process_spec()) == ledger.checkpoint
        else:
            with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
                execute_update(ledger, update, source, process_spec())
            head, refs = ledger.seal()
    if kind == "trainer_returned":
        # The process succeeded but its journal acknowledgement was lost. Its
        # complete returned state survives export after /tmp was removed.
        cold = DirectoryStore(tmp_path / "cold-export")
        cold.publish(run.objects, refs)
        migrated = JournalDirectory(tmp_path / "migrated")
        restore_ledger(cold, head, run=migrated, contract=contract)
        with TrainingLedger(migrated, contract) as ledger:
            result = commit_recorded_update(ledger, BlobRef.of(update.to_bytes()))
            assert result.state == initial and ledger.pending is None
            assert execute_update(ledger, update, source, process_spec()) == result
    assert len(calls) == (0 if kind == "trainer_dispatched" else 1)


@pytest.mark.parametrize("attack", ["request", "status", "time", "extra", "stdout", "state"])
def test_receipt_tampering_and_manual_state_override_are_rejected(tmp_path, numerical, monkeypatch, attack):
    source, contract, initial, request = numerical
    run = JournalDirectory(tmp_path / "trainer")

    def interrupted(*a, **kw):
        return service.ServiceResult("cancelled", None, b"", b"", 0.1)

    monkeypatch.setattr(service, "run_service", interrupted)
    with TrainingLedger(run, contract) as ledger:
        first = ledger.initialize(initial, source)
        update = request(first.reference)
        with pytest.raises(TrainerExecutionError):
            execute_update(ledger, update, source, process_spec())
        pending = ledger.pending
        data = parse_json(run.objects.read(pending.receipt))
        if attack == "request":
            data["invocation"] = BlobRef.of(b"not-the-invocation").as_dict()
        elif attack == "status":
            data["status"] = "maybe-success"
        elif attack == "time":
            data["elapsed_seconds"] = -1
        elif attack == "extra":
            data["ignore_failure"] = True
        elif attack == "stdout":
            data["stdout"] = BlobRef.of(b"not-in-the-store").as_dict()
        else:
            data["state"] = run.objects.put(initial.to_bytes()).as_dict()
        tampered = run.objects.put(canonical_json(data))
        with pytest.raises(RecordError):
            verify_receipt(run.objects, tampered, pending.invocation)
        with pytest.raises(RecordError, match="fully captured"):
            commit_recorded_update(ledger, pending.reference)
        with pytest.raises(RecordError):
            ledger.commit(pending.reference, initial, source)


def test_valid_looking_stdout_without_captured_state_is_not_recovery_evidence(tmp_path, numerical, monkeypatch):
    source, contract, initial, request = numerical
    run = JournalDirectory(tmp_path / "trainer")
    monkeypatch.setattr(
        service, "run_service", lambda *a, **kw: service.ServiceResult("cancelled", None, b"", b"", 0.0)
    )
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        with pytest.raises(TrainerExecutionError):
            execute_update(ledger, request(checkpoint.reference), source, process_spec())
        pending = ledger.pending
        stdout = response_bytes(pending.reference, initial)
        ref = run.objects.put(
            receipt_bytes(pending.invocation, service.ServiceResult("exited", 0, stdout, b"", 0.1), run.objects)
        )
        with pytest.raises(RecordError, match="fully captured"):
            verify_receipt(run.objects, ref, pending.invocation, require_state=True)


def test_cancelled_service_cannot_commit_a_valid_response(numerical):
    _, _, initial, request = numerical
    result = service.ServiceResult(
        "cancelled", 0, response_bytes(request(BlobRef.of(b"parent")).parent, initial), b"", 0.1
    )
    with pytest.raises(RecordError, match="clean zero exit"):
        response_state(result, BlobRef.of(b"parent"), process_spec())
    completed = replace(result, status="exited", execution_status="exited")
    assert response_state(completed, BlobRef.of(b"parent"), process_spec()) == initial


@pytest.mark.parametrize("cause", ["exited", "timeout", "cancelled"])
def test_cleanup_failure_retains_execution_cause_in_cold_trainer_receipt(tmp_path, cause):
    objects = DirectoryStore(tmp_path / "objects")
    invocation = objects.put(canonical_json({"process": process_spec().as_dict()}))
    original = service.ServiceResult("cleanup-incomplete", 17, b"output", b"original diagnostic", 2.,
                                     cleanup_elapsed_seconds=1., execution_status=cause)
    current = receipt_bytes(invocation, original, objects)
    restored, _ = verify_receipt(objects, objects.put(current), invocation)
    assert restored == original
    assert not restored.cleanup_state.quiescent
    with pytest.raises(RecordError, match="clean zero exit"):
        response_state(restored, BlobRef.of(b"request"), process_spec())

    legacy = parse_json(current)
    assert legacy["schema"] == "behave.trainer-receipt/3"
    legacy["schema"] = "behave.trainer-receipt/1"
    legacy.pop("execution_status")
    restored, _ = verify_receipt(objects, objects.put(canonical_json(legacy)), invocation)
    assert restored.status == "cleanup-incomplete" and restored.execution_status is None
    assert restored.stderr == original.stderr and not restored.cleanup_state.quiescent


@pytest.mark.isolation
def test_real_verified_rollout_runs_trainer_and_cold_closure_is_complete(tmp_path, publication, monkeypatch):
    from test_native_chat_tokens import native_training_run

    rollout, manifest, verifier, report, _ = native_training_run(tmp_path, publication)
    report_head, _ = rollout.seal("training")
    contract = TrainerContract(
        "momentum-fixture/1", rollout.objects.put(canonical_json({"learning_rate": 0.05})), manifest
    )
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract, verifier=verifier) as ledger:
        initial = ledger.initialize(initial_state(rollout.objects), rollout.objects)
        update = UpdateRequest(
            "batch-0",
            initial.reference,
            tuple(ExampleUse(report_head, 0, turn, "epoch-0") for turn in (0, 2)),
            rollout.objects.put(b"fixture-supervised-loss-not-grpo"),
        )
        checkpoint = execute_update(ledger, update, rollout.objects, process_spec())
        head, refs = ledger.seal()
    assert checkpoint.state.policy != initial.state.policy
    exported = DirectoryStore(tmp_path / "cold-export")
    exported.publish(run.objects, refs)
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("read-only replay must not dispatch"))
    assert load_checkpoint(exported, head, contract=contract, verifier=verifier) == (checkpoint, None)
    from behave.train.learning_report import load_training_report

    assert load_training_report(exported, report_head, approved_manifest=manifest, verifier=verifier) == report
