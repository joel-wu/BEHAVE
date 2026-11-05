"""Checkpoint plumbing/fault injection; no optimizer or numerical-training claim."""

from dataclasses import replace
from io import BytesIO

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import Event, load_chain
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, metadata, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.train import checkpoints as evidence
from behave.train.checkpoints import (
    Checkpoint,
    PendingUpdate,
    StateFile,
    TrainingLedger,
    TrainingState,
    copy_state_file,
    load_checkpoint,
    restore_ledger,
    store_state_file,
    verify_state,
)
from behave.train.plan import TrainingManifest
from behave.train.update_records import ExampleUse, TrainerContract, UpdateRequest


def state_at(store, step=0, components=("model", "optimizer", "rng", "sampler")):
    files = tuple(
        store_state_file(
            BytesIO(f"{component}:{step}".encode()),
            store,
            component=component,
            path=f"{component}/state.bin",
            chunk_bytes=3,
        )
        for component in components
    )
    return TrainingState(BlobRef.of(f"policy-{step}".encode()), files, {"step": step, "task_cursor": [step, 0]})


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    """Unit selection fixture; the integration below uses real report verification."""
    source = DirectoryStore(tmp_path / "input")
    sentinel = source.put(b"fixture-result")
    manifest = source.put(TrainingManifest(sentinel, (("task", sentinel),)).to_bytes())
    contract = TrainerContract("fixture-trainer/1", source.put(b"fixture-config"), manifest)
    event = Event(0, None, "training_fixture", {}, (sentinel,))
    head = source.put(event.to_bytes())
    turn = {
        "turn": 0,
        "request": sentinel.as_dict(),
        "response": sentinel.as_dict(),
        "status": "eligible",
        "reason": None,
        "tokens": {"checkpoint": sentinel.as_dict()},
    }
    report = {
        "billing": {"USD": "0.03"},
        "candidates": [
            {
                "task_id": "task",
                "attempt_id": "attempt",
                "group_id": "original-group",
                "examples": {
                    "result": sentinel.as_dict(),
                    "reward": 0,
                    "turns": [
                        turn,
                        {**turn, "turn": 1, "status": "excluded", "reason": "output-length"},
                        {**turn, "turn": 2, "status": "unavailable", "reason": "token-data-missing"},
                    ],
                },
            }
        ],
    }

    def verify(reader, ref, *, approved_manifest, verifier):
        assert ref == head and approved_manifest == manifest and verifier is None
        return thaw_json(metadata(report))

    monkeypatch.setattr(evidence, "load_training_report", verify)
    request = lambda parent, **kw: UpdateRequest(
        kw.get("key", "update-0"),
        parent,
        (ExampleUse(head, 0, kw.get("turn", 0), kw.get("exposure", "epoch-0")),),
        source.put(b"algorithm-decision"),
    )
    return source, contract, state_at(source), request, report


def test_streaming_state_round_trip_and_no_framework_deserialization(tmp_path):
    store = DirectoryStore(tmp_path / "objects")

    class Bounded(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 7
            return super().read(size)

    data = bytes(range(200))
    file = store_state_file(Bounded(data), store, component="model", path="model/weights", chunk_bytes=7)
    assert file.content == BlobRef.of(data) and len(file.chunks) == 29
    assert all(ref.size_bytes <= 7 for ref in file.chunks)
    restored = BytesIO()
    copy_state_file(store, file, restored)
    assert restored.getvalue() == data
    empty = store_state_file(BytesIO(), store, component="rng", path="rng", chunk_bytes=1)
    assert not empty.chunks and empty.content == BlobRef.of(b"")
    copy_state_file(store, empty)
    with pytest.raises(OSError, match="short checkpoint write"):
        copy_state_file(store, file, type("Short", (), {"write": lambda *_: 0})())


def test_state_file_count_chunk_count_and_manifest_have_no_quotas(tmp_path):
    chunk = BlobRef.of(b"x")
    content = BlobRef.of(b"x" * 65537)
    file = StateFile("model", "weights", content, (chunk,) * 65537)
    empty = BlobRef.of(b"")
    files = (file, *(StateFile("optimizer", f"slots/{i}", empty, ()) for i in range(4096)))
    state = TrainingState(content, files, {"backend_cursor": "x" * (8 << 20)})
    encoded = state.to_bytes()
    assert len(encoded) > 8 << 20
    assert TrainingState.from_bytes(encoded) == state
    objects = DirectoryStore(tmp_path / "objects")
    objects.put(b"x")
    restored = BytesIO()
    copy_state_file(objects, file, restored)
    assert restored.getvalue() == b"x" * 65537
    # I/O granularity is configurable, not a maximum file or chunk size.
    tiny = store_state_file(BytesIO(b"small"), objects, component="rng", path="rng",
                            chunk_bytes=(32 << 20) + 1)
    assert tiny.content == BlobRef.of(b"small")


@pytest.mark.parametrize("path", ["/absolute", "../parent", "a/../b", "a//b", "a\\b", ""])
def test_state_paths_cannot_escape_the_adapter_destination(tmp_path, path):
    with pytest.raises(RecordError):
        store_state_file(BytesIO(b"x"), DirectoryStore(tmp_path), component="model", path=path)


def test_state_and_contract_schema_fail_closed(fixture):
    source, contract, state, _, _ = fixture
    assert TrainerContract.from_bytes(contract.to_bytes()) == contract
    assert TrainingState.from_bytes(state.to_bytes()) == state
    verify_state(source, state, contract.components)
    with pytest.raises(RecordError):
        TrainingState.from_bytes(state.to_bytes() + b"\n")
    with pytest.raises(ValueError):
        replace(contract, components=("model", "optimizer"))
    with pytest.raises(RecordError):
        replace(state, files=(*state.files, state.files[0]))
    with pytest.raises(RecordError):
        replace(state, files=(replace(state.files[0], path="nested"), replace(state.files[1], path="nested/x")))
    with pytest.raises(RecordError, match="components"):
        verify_state(source, replace(state, files=state.files[:-1]), contract.components)
    file = state.files[0]
    with pytest.raises(RecordError, match="sum"):
        replace(file, chunks=file.chunks[:-1])
    bad = replace(file, chunks=tuple(reversed(file.chunks)))
    with pytest.raises(RecordError, match="complete file"):
        copy_state_file(source, bad)
    with pytest.raises(RecordError):
        StateFile("model", "file", BlobRef.of(b""), (BlobRef.of(b""),))


@pytest.mark.parametrize("decode", [False, True])
def test_empty_state_file_set_is_a_record_error(fixture, decode):
    _, _, state, _, _ = fixture
    with pytest.raises(RecordError) as failure:
        if decode:
            row = parse_json(state.to_bytes())
            row["files"] = []
            TrainingState.from_bytes(canonical_json(row))
        else:
            replace(state, files=())
    assert failure.value.code == "checkpoint-files-invalid"


def test_checkpoint_commit_cold_export_explicit_epoch_reuse_and_no_redispatch(tmp_path, fixture):
    source, contract, initial, request, _ = fixture
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        first = ledger.initialize(initial, source)
        update = request(first.reference)
        assert UpdateRequest.from_bytes(update.to_bytes()) == update
        pending = ledger.begin(update, source)
        assert isinstance(pending, PendingUpdate)
        assert pending.examples[0]["reward"] == 0 and pending.examples[0]["group_id"] == "original-group"
        assert pending.examples[0]["tokens"]["checkpoint"]  # Generating policy retained, not replaced by new state.
        after = state_at(source, 1)
        second = ledger.commit(pending.reference, after, source)
        assert second.reference != first.reference and second.state.cursor["step"] == 1
        assert ledger.begin(update, source) == second
        assert ledger.commit(pending.reference, after, source) == second
        with pytest.raises(RecordError, match="already committed"):
            ledger.begin(replace(update, decision=source.put(b"different-policy")), source)
        with pytest.raises(RecordError, match="already used"):
            ledger.begin(request(second.reference, key="not-a-new-exposure"), source)
        reuse = request(second.reference, key="update-1", exposure="epoch-1")
        ticket = ledger.begin(reuse, source)
        assert ticket.examples[0]["result"] == pending.examples[0]["result"]
        third = ledger.commit(ticket.reference, state_at(source, 2), source)
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    restored, pending = load_checkpoint(exported, head, contract=contract)
    assert restored == third and pending is None
    with TrainingLedger(run, contract) as ledger:
        assert ledger.checkpoint == third and ledger.begin(update, source) == second
        with pytest.raises(RecordError, match="do not reset"):
            ledger.initialize(initial, source)
    with pytest.raises(RecordError, match="independently supplied"):
        load_checkpoint(exported, head, contract=replace(contract, adapter="wrong-version"))


def test_pending_update_is_not_permission_to_run_it_again(tmp_path, fixture):
    source, contract, initial, request, _ = fixture
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        first = ledger.initialize(initial, source)
        update = request(first.reference)
        ticket = ledger.begin(update, source)
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    checkpoint, pending = load_checkpoint(exported, head, contract=contract)
    assert checkpoint == first and pending == ticket
    with TrainingLedger(run, contract) as ledger:
        with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
            ledger.begin(update, source)
        with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
            ledger.begin(request(first.reference, key="another"), source)
        with pytest.raises(RecordError, match="another pending"):
            ledger.commit(BlobRef.of(b"wrong"), initial, source)
        # Only a state recovered by a trusted, externally fenced backend may be committed.
        settled = ledger.commit(ticket.reference, state_at(source, 1), source)
        assert ledger.pending is None and settled.state.cursor["step"] == 1


def test_prepared_inputs_do_not_authorize_commit_and_are_rechecked_at_begin(tmp_path, fixture):
    source, contract, initial, request, _ = fixture
    with TrainingLedger(JournalDirectory(tmp_path / "trainer"), contract) as ledger:
        first = ledger.initialize(initial, source)
        update = request(first.reference)
        preview = ledger.prepare(update, source)
        assert ledger.prepare(update, ledger.run.objects) == preview
        assert ledger.pending is None
        with pytest.raises(RecordError, match="no matching pending"):
            ledger.commit(preview.reference, state_at(source, 1), source)
        head, _ = ledger.seal()
        assert load_checkpoint(ledger.run.objects, head, contract=contract) == (first, None)
        other = request(first.reference, key="other-update")
        pending = ledger.begin(other, source)
        with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
            ledger.prepare(update, source)
        second = ledger.commit(pending.reference, state_at(source, 1), source)
        with pytest.raises(RecordError, match="current committed"):
            ledger.begin(update, source)
        with pytest.raises(RecordError, match="already used"):
            ledger.begin(replace(update, parent=second.reference), source)


@pytest.mark.parametrize("turn", [1, 2, 99])
def test_bad_turn_does_not_create_an_update_or_poison_selection_cache(tmp_path, fixture, turn):
    source, contract, initial, request, _ = fixture
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        with pytest.raises(RecordError, match="excluded or unavailable"):
            ledger.begin(request(checkpoint.reference, turn=turn), source)
        assert ledger.pending is None and len(run.journal("optimizer").load()) == 1
        assert isinstance(ledger.begin(request(checkpoint.reference), source), PendingUpdate)


def test_settled_zero_can_train_and_parent_cannot_be_substituted(tmp_path, fixture):
    source, contract, initial, request, report = fixture
    report["candidates"][0]["examples"]["reward"] = 0
    with TrainingLedger(JournalDirectory(tmp_path / "trainer"), contract) as ledger:
        first = ledger.initialize(initial, source)
        with pytest.raises(RecordError, match="current committed"):
            ledger.begin(request(BlobRef.of(b"wrong-parent")), source)
        assert ledger.pending is None
        update = ledger.begin(request(first.reference), source)
        assert isinstance(update, PendingUpdate)
        assert update.examples[0]["reward"] == 0


def test_duplicate_candidate_aliases_do_not_reuse_one_exposure(tmp_path, fixture):
    source, contract, initial, request, report = fixture
    report["candidates"].append(report["candidates"][0])
    with TrainingLedger(JournalDirectory(tmp_path / "trainer"), contract) as ledger:
        first = ledger.initialize(initial, source)
        update = request(first.reference)
        update = replace(update, uses=(*update.uses, replace(update.uses[0], candidate=1)))
        with pytest.raises(RecordError, match="already used"):
            ledger.begin(update, source)


def test_missing_or_corrupt_shard_cannot_commit_checkpoint(tmp_path, fixture):
    source, contract, initial, request, _ = fixture
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        first = ledger.initialize(initial, source)
        pending = ledger.begin(request(first.reference), source)
        after = state_at(source, 1)

        class Broken:
            def read(self, ref):
                return b"corrupt" if ref == after.files[0].chunks[0] else source.read(ref)

        with pytest.raises(RecordError):
            ledger.commit(pending.reference, after, Broken())
        assert ledger.checkpoint == first and ledger.pending == pending
        assert len(run.journal("optimizer").load()) == 2
        ledger.commit(pending.reference, after, source)


def test_lost_commit_ack_recovers_without_reapplying_update(tmp_path, fixture, monkeypatch):
    from behave.agent.journal import AttemptJournal

    source, contract, initial, request, _ = fixture
    run = JournalDirectory(tmp_path / "trainer")
    original = AttemptJournal.append

    def lost_ack(self, expected, kind, payload, artifacts=()):
        result = original(self, expected, kind, payload, artifacts)
        if kind == "update_committed":
            raise OSError("lost acknowledgement after durable commit")
        return result

    with TrainingLedger(run, contract) as ledger:
        first = ledger.initialize(initial, source)
        update = request(first.reference)
        ticket = ledger.begin(update, source)
        monkeypatch.setattr(AttemptJournal, "append", lost_ack)
        with pytest.raises(OSError, match="lost acknowledgement"):
            ledger.commit(ticket.reference, state_at(source, 1), source)
        with pytest.raises(AttemptInterrupted, match="journal-outcome-unknown"):
            ledger.seal()
        with pytest.raises(AttemptInterrupted, match="journal-outcome-unknown"):
            ledger.begin(update, source)
    monkeypatch.setattr(AttemptJournal, "append", original)
    with TrainingLedger(run, contract) as ledger:
        assert ledger.pending is None and ledger.checkpoint.state.cursor["step"] == 1
        assert isinstance(ledger.begin(update, source), Checkpoint)
        assert len(run.journal("optimizer").load()) == 3


def test_owner_and_lifetime_and_alternate_algorithm_state_contract(tmp_path, fixture):
    source, contract, _, _, _ = fixture
    contract = replace(contract, adapter="non-gradient-fixture/1", components=("model", "rng", "sampler", "population"))
    run = JournalDirectory(tmp_path / "trainer")
    ledger = TrainingLedger(run, contract)
    with pytest.raises(RuntimeError, match="ownership"):
        ledger.seal()
    with ledger:
        with pytest.raises(AttemptInterrupted, match="run-already-owned"), TrainingLedger(run, contract):
            pytest.fail("two owners")
        with pytest.raises(RuntimeError, match="already open"):
            ledger.__enter__()
        ledger.initialize(state_at(source, components=contract.components), source)
    with TrainingLedger(run, contract) as restored:
        assert {file.component for file in restored.checkpoint.state.files} == set(contract.components)


def test_cold_restore_is_resumable_but_never_overwrites_a_divergent_or_newer_run(tmp_path, fixture, monkeypatch):
    from behave.agent.journal import AttemptJournal

    source, contract, initial, request, _ = fixture
    origin, target = JournalDirectory(tmp_path / "origin"), JournalDirectory(tmp_path / "target")
    with TrainingLedger(origin, contract) as ledger:
        first = ledger.initialize(initial, source)
        initial_head, _ = ledger.seal()
        ticket = ledger.begin(request(first.reference), source)
        last = ledger.commit(ticket.reference, state_at(source, 1), source)
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(origin.objects, refs)
    original = AttemptJournal.append

    def crash(self, expected, kind, payload, artifacts=()):
        if len(expected) == 1:
            raise OSError("interrupted import")
        return original(self, expected, kind, payload, artifacts)

    monkeypatch.setattr(AttemptJournal, "append", crash)
    with pytest.raises(OSError, match="interrupted import"):
        restore_ledger(exported, head, run=target, contract=contract)
    assert len(target.journal("optimizer").load()) == 1
    monkeypatch.setattr(AttemptJournal, "append", original)
    assert restore_ledger(exported, head, run=target, contract=contract) == (last, None)
    assert restore_ledger(exported, head, run=target, contract=contract) == (last, None)
    with pytest.raises(RecordError, match="not a prefix"):
        restore_ledger(exported, initial_head, run=target, contract=contract)
    with TrainingLedger(target, contract) as ledger:
        assert ledger.checkpoint == last
        assert ledger.begin(ticket.request, source) == last


@pytest.mark.parametrize("attack", ["dependency", "request", "kind"])
def test_offline_checkpoint_rejects_tampered_commit(tmp_path, fixture, attack):
    source, contract, initial, request, _ = fixture
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        first = ledger.initialize(initial, source)
        ticket = ledger.begin(request(first.reference), source)
        ledger.commit(ticket.reference, state_at(source, 1), source)
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    events = load_chain(exported, head)
    last = events[-1]
    changes = (
        {"artifacts": ()}
        if attack == "dependency"
        else {"kind": "update_requested"}
        if attack == "kind"
        else {"payload": {**thaw_json(last.payload), "request": events[0].artifacts[0].as_dict()}}
    )
    bad = exported.put(replace(last, **changes).to_bytes())
    with pytest.raises(RecordError):
        load_checkpoint(exported, bad, contract=contract)


@pytest.mark.isolation
def test_real_native_rollout_to_checkpoint_and_cold_export_preserves_all_turns(tmp_path, publication, monkeypatch):
    from test_native_chat_tokens import native_training_run

    from behave.train.learning_report import load_training_report

    rollout, manifest, verifier, report, _ = native_training_run(tmp_path, publication)
    report_head, _ = rollout.seal("training")
    contract = TrainerContract("fixture-trainer/1", rollout.objects.put(b"cpu-fixture-not-an-optimizer"), manifest)
    initial = state_at(rollout.objects)
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract, verifier=verifier) as ledger:
        first = ledger.initialize(initial, rollout.objects)
        request = UpdateRequest(
            "batch-0",
            first.reference,
            tuple(ExampleUse(report_head, 0, turn, "epoch-0") for turn in (0, 2)),
            rollout.objects.put(b"fixture-use-policy"),
        )
        ticket = ledger.begin(request, rollout.objects)
        assert [row["turn"] for row in ticket.examples] == [0, 2]
        assert [row["reward"] for row in ticket.examples] == [0, 0]
        assert all(row["tokens"]["response_mask"] for row in ticket.examples)
        after = ledger.commit(ticket.reference, state_at(rollout.objects, 1), rollout.objects)
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "cold-export")
    exported.publish(run.objects, refs)
    from behave.isolation import service

    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("offline verifier must not dispatch"))
    restored, pending = load_checkpoint(exported, head, contract=contract, verifier=verifier)
    assert restored == after and pending is None
    original = load_training_report(exported, report_head, approved_manifest=manifest, verifier=verifier)
    # Verified truncated prefixes are eligible too. The deliberately unused
    # middle turn must still survive export, with its original group and billing.
    assert original == report
    assert [t["status"] for t in original["candidates"][0]["examples"]["turns"]] == ["eligible"] * 3
    with pytest.raises(RecordError, match="token verifier"):
        load_checkpoint(exported, head, contract=contract)
    migrated = JournalDirectory(tmp_path / "migrated-trainer")
    assert restore_ledger(exported, head, run=migrated, contract=contract, verifier=verifier) == (after, None)
    with TrainingLedger(migrated, contract, verifier=verifier) as ledger:
        assert ledger.checkpoint == after and ledger.begin(request, exported) == after
