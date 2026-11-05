"""Static-world records and native CPU collectives, without an RL algorithm."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from test_native_torch import invoke, native_process
from test_trainer_process import initial_state, numerical  # noqa: F401
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.train.checkpoints import TrainingLedger, TrainingState, copy_state_file, restore_ledger
from behave.train.cluster_state import pack_rank_states, unpack_rank_states
from behave.train.trainer import DistributedLaunch, TrainerExecutionError, execute_update


def test_rank_checkpoint_is_lossless_and_rejects_topology_changes(tmp_path):
    state = initial_state(DirectoryStore(tmp_path))
    ranks = (state, replace(state, cursor={"step": 9}))
    packed = pack_rank_states(ranks)
    assert unpack_rank_states(packed, 2) == ranks
    assert len(packed.files) == len(state.files) * 2
    with pytest.raises(RecordError, match="topology"):
        unpack_rank_states(packed, 3)
    with pytest.raises(RecordError, match="unclaimed"):
        unpack_rank_states(replace(packed, policy=BlobRef.of(b"wrong")), 2)
    with pytest.raises((ValueError, RecordError)):
        unpack_rank_states(replace(packed, files=packed.files[1:]), 2)
    with pytest.raises(RecordError, match="component set"):
        pack_rank_states((state, replace(state, files=state.files[1:])))


def distributed_process(mode="ok"):
    process = native_process()
    return replace(process, argv=(process.argv[0], "-B", str(
        Path(__file__).with_name("support") / "distributed_trainer_fixture.py"), mode),
        distributed=DistributedLaunch(process.argv[0], 2))


def values(objects, state):
    result = []
    for rank in unpack_rank_states(state, 2):
        row = {}
        for file in rank.files:
            stream = BytesIO()
            copy_state_file(objects, file, stream)
            row[file.component] = parse_json(stream.getvalue())
        result.append(row)
    return result


@pytest.mark.native_training
@pytest.mark.isolation
def test_real_two_rank_collective_update_and_cold_resume(tmp_path, numerical):  # noqa: F811
    source, contract, initial, request = numerical
    process = distributed_process()
    initial = pack_rank_states((initial, initial))
    run = JournalDirectory(tmp_path / "run")
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        for step in range(2):
            update = request(checkpoint.reference, key=f"step-{step}", exposure=f"epoch-{step}")
            checkpoint = execute_update(ledger, update, source, process)
        expected = values(run.objects, checkpoint.state)
        assert [row["sampler"]["position"] for row in expected] == [2, 2]
        # Each rank supplied a different gradient; both applied the all-reduced one.
        assert expected[0]["model"] == expected[1]["model"] != {"weight": 0.25}
        assert expected[0]["rng"] != expected[1]["rng"]
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    restored = JournalDirectory(tmp_path / "restored")
    restore_ledger(exported, head, run=restored, contract=contract)
    with TrainingLedger(restored, contract) as ledger:
        assert execute_update(ledger, update, source, replace(process, argv=("/never/launch",))) == checkpoint
        update = request(checkpoint.reference, key="step-2", exposure="epoch-2")
        resumed = execute_update(ledger, update, source, process)
    with TrainingLedger(run, contract) as ledger:
        uninterrupted = execute_update(ledger, update, source, process)
    assert values(restored.objects, resumed.state) == values(run.objects, uninterrupted.state)


@pytest.mark.native_training
@pytest.mark.isolation
@pytest.mark.parametrize("mode", ["fail", "malformed", "hang"])
def test_one_failed_rank_never_commits_or_relaunches(tmp_path, numerical, mode):  # noqa: F811
    source, contract, initial, request = numerical
    process = distributed_process(mode)
    if mode == "hang":
        process = replace(process, wall_seconds=15)
    run = JournalDirectory(tmp_path / mode)
    with TrainingLedger(run, contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        update = request(before.reference)
        with pytest.raises(TrainerExecutionError) as failed:
            execute_update(ledger, update, source, process)
        receipt = parse_json(run.objects.read(failed.value.receipt))
        stderr = run.objects.read(BlobRef.from_dict(receipt["stderr"]))
        assert ("fixture-rank-failure:" + mode).encode() in stderr, stderr.decode(errors="replace")
        assert ledger.checkpoint == before and ledger.pending is not None
        with pytest.raises(AttemptInterrupted, match="training-update-outcome-unknown"):
            execute_update(ledger, update, source, process)


@pytest.mark.native_training
@pytest.mark.isolation
def test_real_ddp_adamw_native_checkpoint_cold_resume(tmp_path, numerical):  # noqa: F811
    native = native_process()
    script = Path(__file__).with_name("support") / "ddp_trainer_fixture.py"
    process = replace(native, argv=(native.argv[0], "-B", str(script)),
                      distributed=DistributedLaunch(native.argv[0], 2))
    assert ddp_cold_resume(tmp_path, numerical, process) == {"equal": True, "steps": 2, "ranks": 2}


def ddp_cold_resume(tmp_path, fixture, process):
    """Same ledger/export/cold-restore path for CPU and allocated GPU backends."""
    source, contract, _, request = fixture
    initial = TrainingState.from_bytes(invoke(process, "initial", source.directory))
    contract = replace(contract, adapter="torch-ddp-fixture/1",
                       components=tuple(dict.fromkeys(file.component for file in initial.files)),
                       configuration=source.put(canonical_json({"optimizer": "adamw"})))
    original = JournalDirectory(tmp_path / "original")
    with TrainingLedger(original, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        first = request(checkpoint.reference, key="first", exposure="first")
        checkpoint = execute_update(ledger, first, source, process)
        assert checkpoint.state.policy != initial.policy
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(original.objects, refs)
    restored = JournalDirectory(tmp_path / "restored")
    restore_ledger(exported, head, run=restored, contract=contract)
    second = request(checkpoint.reference, key="second", exposure="second")
    with TrainingLedger(restored, contract) as ledger:
        resumed = execute_update(ledger, second, source, process)
    with TrainingLedger(original, contract) as ledger:
        uninterrupted = execute_update(ledger, second, source, process)
    exported.publish(restored.objects, resumed.state.references)
    exported.publish(original.objects, uninterrupted.state.references)
    left, right = tmp_path / "left.json", tmp_path / "right.json"
    left.write_bytes(uninterrupted.state.to_bytes())
    right.write_bytes(resumed.state.to_bytes())
    return parse_json(invoke(process, "compare", exported.directory, left, right))
