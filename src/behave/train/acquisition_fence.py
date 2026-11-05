"""Reconcile an interrupted construction worker after external owner cleanup.

The caller supplies scheduler observations and explicit owner paths.
This records lifetime evidence only. It never submits a task, supplies a model
answer, approves a package, or retries a tool whose outcome is unknown.
"""

from contextlib import ExitStack
from pathlib import Path

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json, thaw_json


def read(path):
    return parse_json(Path(path).read_bytes())


def native_witness(acquisition, previous_job, settled_job, scheduler_fence, observed):
    acquisition, previous_job, settled_job = (Path(p).resolve() for p in (acquisition, previous_job, settled_job))
    request = read(acquisition / "request.json")
    study = Path(request["study"]).resolve()
    if not acquisition.is_relative_to(study.parent / "acquisitions"):
        raise ValueError("acquisition is outside the native study")
    for job in (previous_job, settled_job):
        config = read(job / "configuration.json")
        binding = read(job / "artifacts/plan/binding.json")
        if (
            Path(config["self_improvement"]["study"]).resolve() != study
            or binding["parent_checkpoint"] != request["parent_checkpoint"]
        ):
            raise ValueError("native owner belongs to a different acquisition/checkpoint")
    if Path(read(settled_job / "continuation.json")["job"]).resolve() != previous_job:
        raise ValueError("settled recovery does not continue the fenced native job")
    node, runtime = read(settled_job / "node_status.json"), read(settled_job / "runtime.json")
    connection = read(acquisition / "connection.json")
    if (
        node["state"] != "stopped"
        or runtime["state"] not in {"interrupted", "failed"}
        or not Path(connection["control_package"]).is_relative_to(runtime["local_directory"])
    ):
        raise ValueError("actual acquisition recovery owner has not stopped")
    learning = JournalDirectory(settled_job / "artifacts/plan/learning" / runtime["phase"])
    settlements = [
        e for e in learning.journal("learning").load(verify_artifacts=False) if e.kind == "learning_process_settled"
    ]
    if not settlements or settlements[-1].payload["cleanup"] != "complete":
        raise ValueError("recovery owner cleanup is unconfirmed")
    old = read(previous_job / "node_status.json")
    allocation = scheduler_fence["allocation"]
    if (
        not scheduler_fence["workers_stopped"]
        or scheduler_fence["old_owner"] != old
        or int(allocation["Restarts"]) <= scheduler_fence["old_restarts"]
        or observed["JobId"] != allocation["JobId"]
        or int(observed["Restarts"]) < int(allocation["Restarts"])
    ):
        raise ValueError("original native worker has no matching scheduler fence")
    return {
        "schema": "behave.construction-native-fence/1",
        "acquisition": str(acquisition),
        "previous_job": str(previous_job),
        "settled_job": str(settled_job),
        "scheduler_fence": scheduler_fence,
        "observed_allocation": observed,
        "owner_settlement": parse_json(settlements[-1].to_bytes()),
    }


def reconcile_role(run, revision, role, witness, *, commit=False):
    """Close only a dangling lifetime request; role/provider evidence is untouched."""
    if role not in {"author", "reader", "source_review"} or type(revision) is not int or revision < 0:
        raise ValueError("unsupported synthesis role")
    if (
        witness.get("schema") != "behave.construction-native-fence/1"
        or witness["owner_settlement"]["kind"] != "learning_process_settled"
        or witness["owner_settlement"]["payload"]["cleanup"] != "complete"
    ):
        raise ValueError("native cleanup evidence missing")
    events = run.journal("synthesis").load(verify_artifacts=False)
    if (
        not events
        or events[-1].kind != "phase_requested"
        or (events[-1].payload["revision"], events[-1].payload["role"]) != (revision, role)
    ):
        raise ValueError("role is not the pending synthesis phase")
    supervision = run.journal("revisions", f"r{revision}", role + "_supervision")
    history = supervision.load(verify_artifacts=False)
    expected = ["construction_requested"]
    if [e.kind for e in history] == [*expected, "construction_settled"]:
        last = history[-1]
        if last.payload.get("interruption") != "construction-owner-preempted":
            return None  # An ordinary settlement is never overwritten.
        return {"state": "already_fenced", "role": role, "revision": revision}
    if [e.kind for e in history] != expected:
        raise ValueError("not a single dangling construction request")
    heads = {}
    for channel in (role, role + "_provider", role + "_development"):
        rows = run.journal("revisions", f"r{revision}", channel).load(verify_artifacts=False)
        heads[channel] = None if not rows else BlobRef.of(rows[-1].to_bytes()).as_dict()
    record = {
        **witness,
        "worker_request": parse_json(history[0].to_bytes()),
        "journals": heads,
        "synthesis_head": BlobRef.of(events[-1].to_bytes()).as_dict(),
    }
    if commit:
        ref = run.objects.put(canonical_json(record))
        supervision.append(
            history,
            "construction_settled",
            {
                "schema": "behave.construction-settlement/2",
                "cleanup": "complete",
                "status": "supervisor-failed",
                "execution_status": None,
                "returncode": None,
                "elapsed_seconds": 0.0,
                "interruption": "construction-owner-preempted",
                "supervision_errors": [],
            },
            (ref,),
        )
    return {"state": "fenced" if commit else "fenceable", "role": role, "revision": revision, "journals": heads}


def reconcile(acquisition, previous_job, settled_job, scheduler_fence, observed, *, commit=False):
    acquisition = Path(acquisition).resolve()
    witness = native_witness(acquisition, previous_job, settled_job, scheduler_fence, observed)
    root = JournalDirectory(acquisition / "round")
    results = []
    with ExitStack() as owners:
        owners.enter_context(root.owner())
        candidates = []
        for batch in sorted((root.directory / "batches").iterdir()):
            owners.enter_context(JournalDirectory(batch).owner())
            for task in sorted((batch / "construction").glob("*")):
                owners.enter_context(JournalDirectory(task).owner())
                for child in sorted((task / "runs").glob("task_*")):
                    run = JournalDirectory(child)
                    owners.enter_context(run.owner())
                    rows = run.journal("synthesis").load(verify_artifacts=False)
                    if rows and rows[-1].kind == "phase_requested":
                        row = thaw_json(rows[-1].payload)
                        if row["role"] in {"author", "reader", "source_review"}:
                            candidates.append((task.name, run, row["revision"], row["role"]))
        # Validate all candidates before modifying any lifetime journal.
        for task, run, revision, role in candidates:
            result = reconcile_role(run, revision, role, witness)
            if result is not None:
                results.append(dict(task=task, run=str(run.directory), **result))
        if commit:
            for task, run, revision, role in candidates:
                reconcile_role(run, revision, role, witness, commit=True)
    return {"committed": commit, "witness": witness, "roles": results}
