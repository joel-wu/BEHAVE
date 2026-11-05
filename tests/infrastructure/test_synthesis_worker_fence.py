"""An external lifetime fence must never manufacture a task outcome."""

import copy

import pytest

from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.process_worker import settled_attempt, worker_receipt
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.train import acquisition_fence
from behave.train.acquisition_fence import native_witness, reconcile, reconcile_role


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def witness_fixture(tmp_path):
    previous, settled = tmp_path / "previous", tmp_path / "settled"
    study = tmp_path / "study/study.json"
    acquisition = study.parent / "acquisitions/update_00000003/parent"
    parent = {"sha256": "f" * 64, "size_bytes": 10}
    for job in (previous, settled):
        write(job / "configuration.json", {"self_improvement": {"study": str(study)}})
        write(job / "artifacts/plan/binding.json", {"parent_checkpoint": parent})
    old = {"state": "live", "pid": 101, "host": "old-node"}
    write(previous / "node_status.json", old)
    write(settled / "node_status.json", {"state": "stopped"})
    write(settled / "runtime.json", {"state": "interrupted", "phase": "initial", "local_directory": "/tmp/native-test"})
    write(settled / "continuation.json", {"job": str(previous)})
    write(acquisition / "request.json", {"study": str(study), "parent_checkpoint": parent})
    write(acquisition / "connection.json", {"control_package": "/tmp/native-test/installed/behave"})
    journal = JournalDirectory(settled / "artifacts/plan/learning/initial").journal("learning")
    journal.append((), "learning_process_settled", {"cleanup": "complete"})
    fence = {
        "workers_stopped": True,
        "old_owner": old,
        "old_restarts": 0,
        "allocation": {"JobId": "7", "Restarts": "1"},
    }
    observed = {"JobId": "7", "Restarts": "1"}
    return acquisition, previous, settled, fence, observed


def role_fixture(tmp_path):
    args = witness_fixture(tmp_path)
    witness = native_witness(*args)
    run = JournalDirectory(tmp_path / "role")
    run.journal("synthesis").append((), "phase_requested", {"revision": 1, "role": "reader"})
    supervision = run.journal("revisions", "r1", "reader_supervision")
    supervision.append((), "construction_requested", {"job": "unchanged"})
    journal = run.journal("revisions", "r1", "reader")
    journal.append((), "tool_requested", {"unresolved": True})
    return run, witness, journal, supervision


def test_fence_preserves_unknown_role_and_reuses_existing_failure_handling(tmp_path):
    run, witness, journal, supervision = role_fixture(tmp_path)
    before = journal.load()
    assert reconcile_role(run, 1, "reader", witness)["state"] == "fenceable"
    assert len(supervision.load()) == 1
    assert reconcile_role(run, 1, "reader", witness, commit=True)["state"] == "fenced"
    assert journal.load() == before
    receipt = worker_receipt(supervision.load(), namespace="construction")
    assert receipt["cleanup"] == "complete" and receipt["returncode"] is None
    assert receipt["interruption"] == "construction-owner-preempted"
    with pytest.raises(AttemptInterrupted, match="construction-owner-preempted"):
        settled_attempt(
            namespace="construction",
            spec=AttemptSpec("same", "reader", {}),
            journal=journal,
            supervision=supervision,
            payload={"job": "unchanged"},
            dependencies=(),
            launch=lambda: pytest.fail("must not retry an unknown tool or fabricate an answer"),
        )
    assert reconcile_role(run, 1, "reader", witness, commit=True)["state"] == "already_fenced"
    assert len(supervision.load()) == 2


@pytest.mark.parametrize(
    "bad",
    [
        "live",
        "cleanup",
        "checkpoint",
        "study",
        "scheduler",
        "owner",
        "continuation",
        "control",
        "runtime",
        "workers",
        "jobid",
        "restart",
    ],
)
def test_native_witness_rejects_missing_or_mismatched_fence(tmp_path, bad):
    acquisition, previous, settled, fence, observed = witness_fixture(tmp_path)
    if bad == "live":
        write(settled / "node_status.json", {"state": "live"})
    elif bad == "cleanup":
        j = JournalDirectory(settled / "artifacts/plan/learning/initial").journal("learning")
        j.append(j.load(), "learning_process_settled", {"cleanup": "unknown"})
    elif bad == "checkpoint":
        write(previous / "artifacts/plan/binding.json", {"parent_checkpoint": None})
    elif bad == "study":
        write(acquisition / "request.json", {"study": str(tmp_path / "different/study.json")})
    elif bad == "scheduler":
        observed["Restarts"] = "0"
    elif bad == "owner":
        fence["old_owner"] = {"state": "live", "pid": 102}
    elif bad == "continuation":
        write(settled / "continuation.json", {"job": str(tmp_path / "unrelated")})
    elif bad == "control":
        write(acquisition / "connection.json", {"control_package": "/tmp/different/behave"})
    elif bad == "runtime":
        write(settled / "runtime.json", {"state": "running", "phase": "initial", "local_directory": "/tmp/native-test"})
    elif bad == "workers":
        fence["workers_stopped"] = False
    elif bad == "jobid":
        observed["JobId"] = "8"
    else:
        fence["allocation"]["Restarts"] = "0"
    with pytest.raises(ValueError):
        native_witness(acquisition, previous, settled, fence, observed)


def test_wrong_phase_cannot_be_fenced(tmp_path):
    run, witness, _, supervision = role_fixture(tmp_path)
    with pytest.raises(ValueError):
        reconcile_role(run, 0, "author", witness, commit=True)
    assert len(supervision.load()) == 1


def construction_fixture(tmp_path):
    args = witness_fixture(tmp_path)
    root = args[0] / "round"
    batch = root / "batches/batch_0"
    task = batch / "construction/candidate_0"
    runs = []
    for index, role in enumerate(("author", "reader")):
        run = JournalDirectory(task / "runs" / f"task_{index}")
        run.journal("synthesis").append((), "phase_requested", {"revision": 1, "role": role})
        supervision = run.journal("revisions", "r1", role + "_supervision")
        supervision.append((), "construction_requested", {"job": f"original-{role}"})
        for channel in (role, role + "_provider", role + "_development"):
            ref = run.objects.put(f"original {channel} artifact".encode())
            run.journal("revisions", "r1", channel).append((), "tool_requested", {"unresolved": True}, (ref,))
        runs.append((run, role, supervision))
    return args, (root, batch, task, runs[0][0].directory), runs


def test_complete_reconciliation_is_idempotent_and_preserves_agent_evidence(tmp_path):
    args, _, runs = construction_fixture(tmp_path)
    before = {
        (str(run.directory), channel): run.journal("revisions", "r1", channel).load()
        for run, role, _ in runs
        for channel in (role, role + "_provider", role + "_development")
    }
    preview = reconcile(*args)
    assert not preview["committed"] and len(preview["roles"]) == 2
    assert all(row["state"] == "fenceable" for row in preview["roles"])
    assert all(len(supervision.load()) == 1 for _, _, supervision in runs)
    assert reconcile(*args, commit=True)["committed"]
    repeated = reconcile(*args, commit=True)
    assert all(row["state"] == "already_fenced" for row in repeated["roles"])
    for run, role, supervision in runs:
        history = supervision.load()
        assert len(history) == 2 and history[-1].payload["returncode"] is None
        assert history[-1].payload["interruption"] == "construction-owner-preempted"
        witness = parse_json(run.objects.read(history[-1].artifacts[0]))
        assert witness["worker_request"] == parse_json(history[0].to_bytes())
        for channel in (role, role + "_provider", role + "_development"):
            events = run.journal("revisions", "r1", channel).load()
            assert events == before[str(run.directory), channel]
            assert witness["journals"][channel] == BlobRef.of(events[-1].to_bytes()).as_dict()
        assert len(run.journal("synthesis").load()) == 1


@pytest.mark.parametrize("level", range(4))
def test_reconciliation_refuses_any_live_owner_before_writing(tmp_path, level):
    args, directories, runs = construction_fixture(tmp_path)
    with JournalDirectory(directories[level]).owner(), pytest.raises(AttemptInterrupted, match="run-already-owned"):
        reconcile(*args, commit=True)
    assert all(len(supervision.load()) == 1 for _, _, supervision in runs)
    assert len(reconcile(*args)["roles"]) == 2  # Locks were released after refusal.


def test_all_pending_roles_are_validated_before_any_settlement(tmp_path):
    args, _, runs = construction_fixture(tmp_path)
    invalid = runs[-1][2]
    invalid.append(invalid.load(), "unexpected_event", {})
    with pytest.raises(ValueError, match="single dangling construction request"):
        reconcile(*args, commit=True)
    assert len(runs[0][2].load()) == 1


def test_partial_commit_can_be_repeated_without_duplicate_settlements(tmp_path, monkeypatch):
    args, _, runs = construction_fixture(tmp_path)
    original = acquisition_fence.reconcile_role

    def interrupted(run, revision, role, witness, *, commit=False):
        if commit and role == "reader":
            raise OSError("interrupted fixture publication")
        return original(run, revision, role, witness, commit=commit)

    with monkeypatch.context() as patch:
        patch.setattr(acquisition_fence, "reconcile_role", interrupted)
        with pytest.raises(OSError, match="interrupted fixture"):
            reconcile(*args, commit=True)
    assert [len(supervision.load()) for _, _, supervision in runs] == [2, 1]
    reconcile(*args, commit=True)
    assert [len(supervision.load()) for _, _, supervision in runs] == [2, 2]


@pytest.mark.parametrize("field", ["schema", "kind", "cleanup"])
def test_role_refuses_invalid_native_cleanup_evidence(tmp_path, field):
    run, witness, _, supervision = role_fixture(tmp_path)
    witness = copy.deepcopy(witness)
    if field == "schema":
        witness["schema"] = "unrecognized"
    elif field == "kind":
        witness["owner_settlement"]["kind"] = "learning_process_started"
    else:
        witness["owner_settlement"]["payload"]["cleanup"] = "unknown"
    with pytest.raises(ValueError, match="native cleanup evidence missing"):
        reconcile_role(run, 1, "reader", witness, commit=True)
    assert len(supervision.load()) == 1


def test_existing_ordinary_settlement_is_not_overwritten(tmp_path):
    run, witness, _, supervision = role_fixture(tmp_path)
    supervision.append(supervision.load(), "construction_settled", {"status": "original"})
    before = supervision.load()
    assert reconcile_role(run, 1, "reader", witness, commit=True) is None
    assert supervision.load() == before
