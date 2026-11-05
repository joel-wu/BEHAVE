"""Cross-node dispatch, all-node acceptance and cold receipt verification."""

import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_trainer_process import numerical  # noqa: F401
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401

from behave.agent.attempt import AttemptInterrupted
from behave.agent.jobs import JobControl, JobRequest, validate_worker
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.isolation.service import ServiceResult
from behave.train import cluster as cluster_module
from behave.train.checkpoints import TrainingLedger, restore_ledger
from behave.train.cluster_state import SlurmTraining, TrainingNode, pack_rank_states, unpack_rank_states
from behave.train.trainer import (
    DistributedLaunch,
    TrainerExecutionError,
    TrainerProcess,
    commit_recorded_update,
    execute_update,
    verify_receipt,
)


def configuration(tmp_path):
    return TrainerProcess(("/native/bin/python", "/shared/backend.py"), {}, 60, cleanup_seconds=1,
                          distributed=DistributedLaunch("/native/bin/python", 2,
                              SlurmTraining((TrainingNode(10, "node-a"), TrainingNode(11, "node-b")),
                                            str(tmp_path / "dispatch"), 29777)))


@pytest.mark.parametrize("port", [0, 65536, True, 1.5])
def test_rendezvous_still_requires_a_valid_tcp_port(tmp_path, port):
    with pytest.raises(ValueError, match="valid port"):
        replace(configuration(tmp_path).distributed.cluster, master_port=port)


def test_transport_reaping_shares_the_configured_cleanup_window(monkeypatch):
    waits, killed = [], []
    clock = iter((100., 101., 102.))
    monkeypatch.setattr(cluster_module.time, "monotonic", lambda: next(clock))

    class Client:
        def poll(self):
            return None

        def kill(self):
            killed.append(self)

        def wait(self, timeout):
            waits.append(timeout)

    clients = [Client(), Client()]
    cluster_module._stop_clients(clients, 17)
    assert killed == clients
    assert waits == [16., 15.]


def test_transport_reaping_can_wait_without_a_deadline():
    waits = []

    class Client:
        def poll(self):
            return 0

        def wait(self, timeout):
            waits.append(timeout)

    cluster_module._stop_clients([Client(), Client()], None)
    assert waits == [None, None]


def test_cross_node_topology_and_legacy_job_bytes(tmp_path):
    process = configuration(tmp_path)
    assert TrainerProcess.from_dict(process.as_dict()) == process
    job = JobRequest(("/bin/true",), "/tmp", 10, allocation=10)
    assert "host" not in job.as_dict()
    assert JobRequest.from_dict(job.as_dict()) == job
    assigned = replace(job, host="node-a")
    assert "--nodelist=node-a" in assigned.slurm_prefix()
    assert JobRequest.from_dict(assigned.as_dict()) == assigned
    with pytest.raises(ValueError, match="requested host"):
        validate_worker({"host": "node-b", "boot_id": "boot", "pid": 1, "allocation": 10, "step": 7}, assigned)
    cluster = process.distributed.cluster
    with pytest.raises(ValueError, match="distinct hosts"):
        replace(cluster, nodes=(cluster.nodes[0], cluster.nodes[0]))
    with pytest.raises(ValueError, match="divide evenly"):
        replace(process.distributed, ranks=3)
    with pytest.raises(ValueError, match="visibility"):
        replace(process, environment={"CUDA_VISIBLE_DEVICES": "0,1"})


@pytest.mark.parametrize("changes", [{"allocation": True}, {"host": "node[1-2]"}, {"host": "a,b"},
                                      {"cpus": 0}, {"gpus": True}])
def test_node_placement_is_not_a_shell_or_hostlist(changes):
    with pytest.raises((TypeError, ValueError)):
        TrainingNode(**{"allocation": 1, "host": "node-a", **changes})


def transports(monkeypatch, source, *, failure=None):
    """Inject remote outcomes, not a second implementation of trainer commit."""
    calls = []
    ticks = [0.]
    monkeypatch.setattr(cluster_module.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(cluster_module.time, "sleep", lambda _: ticks.__setitem__(0, ticks[0] + 1.))

    class Client:
        def __init__(self, argv, **kwargs):
            calls.append(argv)
            self.returncode = None
            directory, key = Path(argv[-2]), argv[-1]
            index = int(directory.name.split("-")[-1])
            control = JobControl(directory)
            journal, events, request = control._load(key)
            if failure == "missing" and index == 1:
                self.returncode = 0  # Transport exit alone cannot settle a node.
                return
            identity = {"host": request.host, "boot_id": f"boot-{index}", "pid": 10 + index,
                        "allocation": request.allocation, "step": 3}
            events += (journal.append(events, "job_started", identity),)
            envelope = parse_json((directory.parent / "invocation.json").read_bytes())
            inputs = DirectoryStore(envelope["input"])
            invocation = parse_json(inputs.read(BlobRef.from_dict(envelope["invocation"])))
            from behave.train.checkpoints import TrainingState

            state = unpack_rank_states(TrainingState.from_bytes(canonical_json(invocation["state"])), 2)[index]
            DirectoryStore(envelope["output"]).publish(inputs, state.references)
            stdout = canonical_json({"schema": "behave.trainer-node-response/1", "node": index,
                                     "request": BlobRef.of(canonical_json(invocation["request"])).as_dict(),
                                     "states": [parse_json(state.to_bytes())]})
            status, code = "exited", 0
            if index == 1:
                if failure == "fail":
                    code = 7
                elif failure == "malformed":
                    stdout = b"bad checkpoint"
                elif failure == "cleanup":
                    status, code = "cleanup-incomplete", None
            control._settle(journal, events, ServiceResult(status, code, stdout, b"rank reached", 0.))
            self.returncode = 0 if status == "exited" and code == 0 else 1

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def wait(self, timeout):
            return self.returncode

    monkeypatch.setattr(cluster_module.subprocess, "Popen", Client)
    return calls


def test_all_nodes_commit_and_cold_restore_authenticates_receipts(tmp_path, numerical, monkeypatch):  # noqa: F811
    source, contract, initial, request = numerical
    calls = transports(monkeypatch, source)
    process = configuration(tmp_path)
    run = JournalDirectory(tmp_path / "run")
    with TrainingLedger(run, contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        update = request(before.reference)
        after = execute_update(ledger, update, source, process)
        assert after.state == before.state and after.reference != before.reference
        assert len(calls) == 2
        assert execute_update(ledger, update, source, process) == after
        assert len(calls) == 2
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    restored = JournalDirectory(tmp_path / "cold")
    checkpoint, pending = restore_ledger(exported, head, run=restored, contract=contract)
    assert checkpoint == after and pending is None
    workspace = Path(process.distributed.cluster.shared_directory) / BlobRef.of(update.to_bytes()).sha256
    assert (workspace / "nodes.json").is_file() and not (workspace / "input").exists()


@pytest.mark.parametrize("unlimited", [False, True])
def test_clean_transport_waits_for_delayed_node_receipt(tmp_path, numerical, monkeypatch, unlimited):  # noqa: F811
    source, contract, initial, request = numerical
    calls = transports(monkeypatch, source)
    inspect = JobControl.inspect
    reads = {}

    def delayed(self, key):
        observed = inspect(self, key)
        reads[key] = reads.get(key, 0) + 1
        # The transport has already exited zero, but the shared journal is not
        # visible in the coordinator's first polls. Neither success nor failure
        # may be inferred until the receipt arrives (or the deadline expires).
        return replace(observed, result=None, state="started") if reads[key] <= 3 else observed

    monkeypatch.setattr(JobControl, "inspect", delayed)
    process = configuration(tmp_path)
    if unlimited:
        process = replace(process, wall_seconds=None, cleanup_seconds=None)
    with TrainingLedger(JournalDirectory(tmp_path / "run"), contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        update = request(before.reference)
        after = execute_update(ledger, update, source, process)
        assert after.state == before.state and after.reference != before.reference
    assert len(calls) == 2
    for argv in calls:
        observed = inspect(JobControl(argv[-2]), argv[-1])
        assert observed.cleanup == CleanupState.COMPLETE and not observed.cancel_requested


def test_pre_cancelled_cluster_never_creates_a_dispatch(tmp_path, numerical, monkeypatch):  # noqa: F811
    source, contract, initial, request = numerical
    calls = transports(monkeypatch, source)
    with TrainingLedger(JournalDirectory(tmp_path / "run"), contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        with pytest.raises(AttemptInterrupted, match="cancelled-before-dispatch"):
            execute_update(ledger, request(before.reference), source, configuration(tmp_path), cancelled=lambda: True)
        assert ledger.pending is None and not calls


@pytest.mark.parametrize("failure", ["staging", "cancelled"])
def test_cluster_preparation_failure_releases_only_its_unstarted_directory(tmp_path, numerical, monkeypatch, failure):  # noqa: F811
    from behave.train import trainer as execution

    source, contract, initial, request = numerical
    calls = transports(monkeypatch, source)
    process = configuration(tmp_path)
    with TrainingLedger(JournalDirectory(tmp_path / "run"), contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        update = request(before.reference)
        workspace = Path(process.distributed.cluster.shared_directory) / BlobRef.of(update.to_bytes()).sha256
        with monkeypatch.context() as patch:
            if failure == "staging":
                def failed_store(*args, **kwargs):
                    raise OSError("injected staging failure")
                patch.setattr(execution, "DirectoryStore", failed_store)
            with pytest.raises((AttemptInterrupted, OSError)):
                execute_update(ledger, update, source, process,
                               cancelled=lambda: failure == "cancelled" and workspace.exists())
        assert ledger.pending is None and not calls and not workspace.exists()
        # Same key now reaches both fixed nodes; no stale preparation marker.
        after = execute_update(ledger, update, source, process)
        assert after.state == before.state and len(calls) == 2


@pytest.mark.parametrize("failure", ["fail", "malformed", "cleanup", "missing"])
def test_partial_node_results_never_commit_or_redispatch(tmp_path, numerical, monkeypatch, failure):  # noqa: F811
    source, contract, initial, request = numerical
    calls = transports(monkeypatch, source, failure=failure)
    process = configuration(tmp_path)
    with TrainingLedger(JournalDirectory(tmp_path / "run"), contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        update = request(before.reference)
        with pytest.raises(TrainerExecutionError) as error:
            execute_update(ledger, update, source, process)
        assert ledger.checkpoint == before
        result, _ = verify_receipt(ledger.run.objects, error.value.receipt, ledger.pending.invocation)
        assert result.cleanup_state == (CleanupState.INCOMPLETE if failure in {"missing", "cleanup"} else CleanupState.COMPLETE)
        with pytest.raises(AttemptInterrupted):
            execute_update(ledger, update, source, process)
        with pytest.raises(ValueError):
            commit_recorded_update(ledger, ledger.pending.reference)
        assert len(calls) == 2
        if failure in {"cleanup", "missing"}:
            with pytest.raises(ValueError, match="fence"):
                ledger.abort(BlobRef.of(update.to_bytes()))


def test_cold_verifier_rejects_missing_node_or_forged_cleanup(tmp_path, numerical, monkeypatch):  # noqa: F811
    source, contract, initial, request = numerical
    transports(monkeypatch, source)
    with TrainingLedger(JournalDirectory(tmp_path / "run"), contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        # Keep the receipt unresolved to exercise the same cold verifier directly.
        monkeypatch.setattr(ledger, "commit", lambda *_: (_ for _ in ()).throw(OSError("lost acknowledgement")))
        with pytest.raises(TrainerExecutionError):
            execute_update(ledger, request(before.reference), source, configuration(tmp_path))
        objects = ledger.run.objects
        pending = ledger.pending
        with pytest.raises(ValueError, match="successful state"):
            ledger.abort(pending.reference)
        receipt = parse_json(objects.read(pending.receipt))
        cluster = parse_json(objects.read(BlobRef.from_dict(receipt["cluster"])))
        cluster["nodes"][1]["result"] = None
        receipt["cluster"] = objects.put(canonical_json(cluster)).as_dict()
        with pytest.raises(ValueError, match="cleanup/state evidence"):
            verify_receipt(objects, objects.put(canonical_json(receipt)), pending.invocation)


def test_abort_is_explicit_durable_and_allows_new_key_without_losing_examples(tmp_path, numerical, monkeypatch, capsys):  # noqa: F811
    from behave.cli.trainer import main

    source, contract, initial, request = numerical
    transports(monkeypatch, source, failure="fail")
    process = configuration(tmp_path)
    run = JournalDirectory(tmp_path / "run")
    with TrainingLedger(run, contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        update = request(before.reference)
        with pytest.raises(TrainerExecutionError):
            execute_update(ledger, update, source, process)
    contract_file, request_file = tmp_path / "contract.json", tmp_path / "request.json"
    contract_file.write_bytes(contract.to_bytes())
    request_file.write_bytes(update.to_bytes())
    args = ["abort", "--contract", str(contract_file), "--run-directory", str(run.directory),
            "--request", str(request_file)]
    assert main(args) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "aborted"
    assert main(args) == 0  # A lost command acknowledgement is idempotent.
    capsys.readouterr()
    with TrainingLedger(run, contract) as ledger:
        assert ledger.checkpoint == before and ledger.pending is None
        with pytest.raises(ValueError, match="new explicit update key"):
            execute_update(ledger, update, source, process)
        head, refs = ledger.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    cold = JournalDirectory(tmp_path / "cold")
    restore_ledger(exported, head, run=cold, contract=contract)
    calls = transports(monkeypatch, source)
    with TrainingLedger(cold, contract) as ledger:
        with pytest.raises(ValueError, match="new explicit update key"):
            execute_update(ledger, update, source, process)
        after = execute_update(ledger, replace(update, key="explicit-retry"), source, process)
        assert after.reference != before.reference and len(calls) == 2


def native_cluster(label, mode="ok"):
    placement = os.environ.get("BEHAVE_TEST_TRAINING_CLUSTER")
    if not placement:
        pytest.skip("requires explicit cross-node fixture placement")
    cluster = SlurmTraining.from_dict(parse_json(Path(placement).read_bytes()))
    cluster = replace(cluster, shared_directory=str(Path(cluster.shared_directory) / label))
    python = os.environ["BEHAVE_NATIVE_TRAINER_PYTHON"]
    script = Path(os.environ["BEHAVE_TEST_CLUSTER_FIXTURES"]) / "distributed_trainer_fixture.py"
    return TrainerProcess((python, "-B", str(script), mode),
                          {"PATH": "/usr/bin:/bin", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
                          wall_seconds=180, cleanup_seconds=15,
                          distributed=DistributedLaunch(python, len(cluster.nodes), cluster))


@pytest.mark.cluster_training
@pytest.mark.native_training
@pytest.mark.isolation
def test_real_cross_node_collective_and_cold_resume(tmp_path, numerical, record_property):  # noqa: F811
    source, contract, initial, request = numerical
    process = native_cluster("original")
    original = JournalDirectory(tmp_path / "original")
    with TrainingLedger(original, contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        first = request(before.reference, key="first", exposure="first")
        after = execute_update(ledger, first, source, process)
        assert after.state.policy != before.state.policy
        head, refs = ledger.seal()
    workspace = Path(process.distributed.cluster.shared_directory) / BlobRef.of(first.to_bytes()).sha256
    dispatches = parse_json((workspace / "nodes.json").read_bytes())["nodes"]
    observed = [JobControl(row["directory"]).inspect(row["key"]) for row in dispatches]
    assert len({row.worker["host"] for row in observed}) == 2
    assert all(row.cleanup == CleanupState.COMPLETE for row in observed)
    record_property("hosts", ",".join(row.worker["host"] for row in observed))
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(original.objects, refs)
    cold = JournalDirectory(tmp_path / "cold")
    restore_ledger(exported, head, run=cold, contract=contract)
    second = request(after.reference, key="second", exposure="second")
    with TrainingLedger(cold, contract) as ledger:
        resumed = execute_update(ledger, second, source, native_cluster("restored"))
    with TrainingLedger(original, contract) as ledger:
        uninterrupted = execute_update(ledger, second, source, process)
    assert resumed.state == uninterrupted.state


@pytest.mark.cluster_training
@pytest.mark.native_training
@pytest.mark.isolation
def test_real_cross_node_failure_never_commits(tmp_path, numerical):  # noqa: F811
    source, contract, initial, request = numerical
    process = native_cluster("failure", "fail")
    with TrainingLedger(JournalDirectory(tmp_path / "failure"), contract) as ledger:
        before = ledger.initialize(pack_rank_states((initial, initial)), source)
        update = request(before.reference)
        with pytest.raises(TrainerExecutionError) as error:
            execute_update(ledger, update, source, process)
        receipt = parse_json(ledger.run.objects.read(error.value.receipt))
        nodes = parse_json(ledger.run.objects.read(BlobRef.from_dict(receipt["cluster"])))["nodes"]
        logs = b"\n".join(ledger.run.objects.read(BlobRef.from_dict(row["result"]["stderr"])) for row in nodes)
        assert b"fixture-rank-failure:fail" in logs, logs.decode(errors="replace")
        result, _ = verify_receipt(ledger.run.objects, error.value.receipt, ledger.pending.invocation)
        assert result.cleanup_state == CleanupState.COMPLETE
        assert ledger.checkpoint == before
        with pytest.raises(AttemptInterrupted):
            execute_update(ledger, update, source, process)
