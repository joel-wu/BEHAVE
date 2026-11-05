"""GPU task slots plus independent CPU consumers over existing Eval entries.

Each slot owns a complete model/checkpoint/inference-seed measurement. A slow
measurement therefore holds one TP1 GPU, not an eight-GPU batch barrier. Nothing
changes prompts, inference seeds, sampling or archived attempt identities.
"""

from __future__ import annotations

import json
import stat
import sys
import time
from functools import partial
from pathlib import Path

from behave.core.file_input import read_regular_file
from behave.core.records import parse_json
from behave.eval import checkpoint
from behave.eval.checkpoint import resolve_checkpoint, submit_checkpoint
from behave.runtime.records import publish_json, write_json

from .checkpoint import verify_preparation_directory
from .resources import execution_cpu_ids, node_capacity


def terminal(control):
    return observe_control(control)["state"] in TERMINAL


def generation_blocker(node):
    """A training-to-eval handoff needs successful cleanup and committed weights."""
    states = [observe_control(control)["state"] for control in node["legacy_generation"]]
    if any(state in ACTIVE for state in states):
        return "waiting for previous GPU owner"
    if any(state != "completed" for state in states):
        return "legacy generation needs cleanup verification"
    if node.get("required_checkpoint") is not None and resolve_checkpoint(node["required_checkpoint"]) is None:
        return "waiting for committed checkpoint"
    return None


def start_measurement(root, item, slot, handoff, *, cwd=None):
    lane = assignment_directory(root, item["key"])
    if (lane / "configuration.json").exists():
        raise ValueError("measurement already has an execution owner")
    config = measurement_config(root, item, slot)
    assignment = begin_preparation(root, item, slot, config, handoff)
    return continue_preparation(root, item, assignment, submit=partial(submit_checkpoint, cwd=cwd))


def start_scores(root, allocation, *, cwd=None):
    from behave.runtime.jobs import submit

    root = Path(root)
    fleet = checkpoint.read(root / "fleet.json")
    node = fleet["nodes"][str(allocation)]
    control = root / "scoring" / str(allocation) / node.get(
        "scoring_control", fleet.get("scoring_control", "control"))
    if (control / "launch.json").exists():
        return
    execution_allocation = node.get("scoring_allocation", allocation)
    execution_node = fleet["nodes"][str(execution_allocation)]
    cpus = node["scoring_cpu_ids"]
    if execution_allocation != allocation and (
            set(cpus) - set(execution_node["allocated_cpu_ids"])
            or set(cpus).intersection(execution_node["generation_cpu_ids"])
            or execution_node.get("scoring_enabled", True)):
        raise ValueError("relocated scoring needs an unoccupied CPU complement")
    cpus = execution_cpu_ids(execution_node, cpus)
    allocated_cpus, _ = node_capacity(execution_node)
    snapshot = Path(fleet["archive_root"]) / node.get(
        "scoring_code_directory", fleet["code_directory"]) / "snapshot"
    # Staging and imports use the same snapshot as generation. No GPU request.
    boot = ("import sys;from pathlib import Path;from tempfile import mkdtemp;"
        "sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
        "from tools.infra_validation_job import stage;"
        "local=Path(mkdtemp(prefix='behave-stream-code-',dir='/tmp'))/'checkout';"
        "stage(Path(sys.argv.pop(1)),local);"
        "import os;os.execv(sys.executable,[sys.executable,'-I','-B','-u','-c',"
        "'import sys;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
        "from tools.eval_score_stream import run;run(sys.argv[1],int(sys.argv[2]))',"
        "str(local),str(local/'src'),sys.argv[1],sys.argv[2]])")
    submit(control, ["srun", f"--jobid={execution_allocation}", "--overlap", "--exact", "--nodes=1", "--ntasks=1",
        f"--cpus-per-task={allocated_cpus}",
        f"--cpu-bind=mask_cpu:{hex(sum(1 << cpu for cpu in cpus))}",
        "--gres=none", "--propagate=NONE", sys.executable, "-I", "-B", "-u", "-c", boot,
        str(snapshot), str(snapshot / "src"), str(snapshot), str(root), str(allocation)],
        cwd=Path.cwd() if cwd is None else Path(cwd), wall_seconds=None, cleanup_seconds=300,
        metadata={"purpose": "independent committed-submission scoring", "allocation": allocation,
                  "execution_allocation": execution_allocation,
                  "cpus": cpus, "workers": fleet["scoring_workers"]})


def verify_unstarted(directory):
    """Fail closed: this is not recovery of an interrupted evaluation."""
    directory = Path(directory)
    if not (directory / "generation.json").is_file() or not (directory / "rollout/cleanup.json").is_file():
        raise ValueError("no failed startup with confirmed cleanup")
    generation = checkpoint.read(directory / "generation.json")
    cleanup = checkpoint.read(directory / "rollout/cleanup.json")
    if (generation.get("state") != "interrupted"
            or generation.get("cleanup_complete") is not True
            or cleanup.get("cleanup_complete") is not True):
        raise ValueError("startup cleanup is not confirmed")
    services = cleanup.get("services", [])
    if not services or any(s.get("ready") is not False or s.get("cleanup_complete") is not True
                           for s in services):
        raise ValueError("a service became ready, or its cleanup is unknown")
    # An unexpected file is evidence requiring operator review, not permission
    # to erase a potentially started run. In particular run/progress/report,
    # journals, trace indices and scoring outputs are never allowed here.
    allowed = {"control", "generation.json", "generation.log", "manifest.json",
               "node_status.json", "rollout", "serving.json", "startup_failures"}
    rollout_allowed = {"cleanup.json", "runtime.json", "runtime_settings.json",
                       "sampling.json", "serving", "settings.json", "trained"}
    if ({p.name for p in directory.iterdir()} - allowed
            or {p.name for p in (directory / "rollout").iterdir()} - rollout_allowed):
        raise ValueError("possible rollout or scoring artifacts; no startup-only retry")


def verify_port_preflight(directory, control):
    """Recognize only an explicit bind failure before staging or any rollout."""

    directory, control = Path(directory), Path(control)
    status = observe_control(control)
    if status["state"] != "failed" or status.get("returncode") != 1:
        raise ValueError("port preflight owner must have exited with failure")
    log = (control / "run.log").read_text()
    if not all(text in log for text in ("in serving_port_lease", "sock.bind(",
                                       "OSError: [Errno 98] Address already in use")):
        raise ValueError("not an explicit port preflight failure")
    allowed = {"control", "manifest.json", "serving.json", "rollout", "startup_failures"}
    prepared = {"settings.json", "sampling.json", "trained"}
    if ({p.name for p in directory.iterdir()} - allowed
            or {p.name for p in (directory / "rollout").iterdir()} != prepared
            or {p.name for p in (directory / "rollout/trained").iterdir()} != {"configuration.json"}):
        raise ValueError("possible staging, serving or rollout artifacts; no preflight retry")


def retry_startup(root, key, retry_id, *, port_preflight=False, preflight_check=None, cwd=None):
    """Explicit, locked recovery of a cleaned startup that admitted no cases.

    The immutable failed control and all startup evidence are retained. Neither
    task identities nor the canonical path consumed by CPU scoring changes.
    Stop the fleet controller first; its owner lock prevents concurrent edits.
    An optional operator-supplied preflight check runs under that same lock,
    before any write. It must prove no cases were admitted and cleanup finished;
    exceptions abort recovery. Historical decoder signatures belong to the
    operator, not the shared scheduler or its automatic preparation path.
    """
    from behave.task_execution.run_store import RunStore


    root = Path(root).resolve()
    if not retry_id or Path(retry_id).name != retry_id or retry_id in {".", ".."}:
        raise ValueError("retry ID must be a simple directory name")
    if port_preflight and preflight_check is not None:
        raise ValueError("select exactly one preflight failure type")
    with RunStore(root / "owner").owner():
        fleet = checkpoint.read(root / "fleet.json")
        active = checkpoint.read(root / "assignments.json")
        matches = [row for row in active if row["key"] == key]
        if len(matches) != 1:
            raise ValueError("startup retry requires exactly one assigned owner")
        old = matches[0]
        item, = (item for item in fleet["measurements"] if item["key"] == key)
        lane = assignment_directory(root, key)
        entry = checkpoint.read(lane / "queue.json")["checkpoints"][0]
        directory = checkpoint.measurement_directory(lane, entry, item["seed"])
        if directory.resolve() != Path(item["directory"]).resolve():
            raise ValueError("measurement directory changed")
        archive = directory / "startup_failures" / retry_id
        if (archive / "intent.json").exists():
            return resume(root, item, retry_id, submit=partial(submit_checkpoint, cwd=cwd))
        if observe_control(old["control"])["state"] not in {"failed", "timed-out", "launch-error"}:
            raise ValueError("former owner is not terminal and failed")
        if preflight_check is not None:
            preflight_check(directory, old["control"])
        elif port_preflight:
            verify_port_preflight(directory, old["control"])
        else:
            verify_unstarted(directory)
        old_config = checkpoint.read(lane / "configuration.json")
        if checkpoint.control_directory(lane, entry).resolve() != Path(old["control"]).resolve():
            raise ValueError("former control identity changed")
        config = measurement_config(root, item, old)
        config["launch_id"] = retry_id
        if (directory / "control" / retry_id).exists():
            raise ValueError("retry control already exists")
        # Only launcher infrastructure may change. Preserve all sampling, task,
        # model and context settings, even if the base config was later edited.
        new_config = {**old_config, "code_directory": config["code_directory"], "launch_id": retry_id,
                      "resources": {**old_config["resources"], **{k: config["resources"][k] for k in
                          ("port_first", "nccl_port_first", "lease_serving_ports")}}}
        begin(root, item, old, entry, new_config, retry_id)
        return resume(root, item, retry_id, submit=partial(submit_checkpoint, cwd=cwd))


def run(root, *, cwd=None):
    from behave.task_execution.run_store import RunStore

    root = Path(root)
    fleet = checkpoint.read(root / "fleet.json")
    assignments = root / "assignments.json"
    with RunStore(root / "owner").owner():
        active = checkpoint.read(assignments) if assignments.exists() else []
        reconciled = reconcile_assignments(root, fleet, active)
        if reconciled != active:
            write_json(assignments, reconciled)
            active = reconciled
        launched = {row["key"] for row in active}
        while True:
            fleet = reload_fleet(root, fleet)
            publish_score_readiness(fleet)
            blocked = {}
            busy, running = set(), []
            owner_states = {}
            for row in active:
                status = observe_control(row["control"])
                owner_states[row["key"]] = status["state"]
                if status["state"] in ACTIVE:
                    busy.add((row["allocation"], row["gpu"]))
                    running.append(row)
                elif status["state"] != "completed":
                    blocked[row["key"]] = status["state"]
                    # Cleanup must be explicitly reconciled before this GPU is reused.
                    busy.add((row["allocation"], row["gpu"]))
            available_nodes = set()
            for allocation, node in fleet["nodes"].items():
                allocation = int(allocation)
                blocker = generation_blocker(node)
                if not node.get("generation_enabled", True):
                    blocked[str(allocation)] = "generation disabled by operator"
                elif blocker:
                    blocked[str(allocation)] = blocker
                    busy.update((allocation, gpu) for gpu in generation_gpus(node))
                else:
                    available_nodes.add(allocation)
                if node.get("scoring_enabled", True) and all(terminal(c) for c in node["legacy_scoring"]):
                    start_scores(root, allocation, cwd=cwd)
            waiting = []
            for item in fleet["measurements"]:
                if item["key"] in launched:
                    assigned = next(row for row in active if row["key"] == item["key"])
                    if assigned["allocation"] in available_nodes and resumable_preparation(assigned):
                        if any(row["key"] != assigned["key"]
                               and (row["allocation"], row["gpu"]) == (assigned["allocation"], assigned["gpu"])
                               and owner_states.get(row["key"]) != "completed" for row in active):
                            blocked[item["key"]] = "preparation slot has another unresolved owner"
                            continue
                        try:
                            resumed = continue_preparation(root, item, assigned, submit=partial(submit_checkpoint, cwd=cwd))
                        except (ValueError, OSError, RuntimeError) as error:
                            blocked[item["key"]] = f"preparation resume failed: {type(error).__name__}"
                        else:
                            active[active.index(assigned)] = resumed
                            write_json(assignments, active)
                            state = observe_control(resumed["control"])["state"]
                            owner_states[item["key"]] = state
                            if state in ACTIVE:
                                blocked.pop(item["key"], None)
                                running.append(resumed)
                            elif state != "completed":
                                blocked[item["key"]] = state
                    continue
                marker = Path(item["directory"]) / "generation.json"
                if marker.exists():
                    if checkpoint.read(marker)["state"] == "completed":
                        continue
                    # Never rerun an active/interrupted existing measurement.
                    waiting.append(item["key"])
                    continue
                slot = generation_slot(fleet, item, busy, available_nodes)
                if slot is None:
                    waiting.append(item["key"])
                    continue
                handoff = resolve_checkpoint(item["entry"])
                if handoff is None:
                    waiting.append(item["key"])
                    continue  # An unavailable later checkpoint cannot stall ready work.
                record = start_measurement(root, item, slot, handoff, cwd=cwd)
                active.append(record)
                launched.add(item["key"])
                busy.add((slot["allocation"], slot["gpu"]))
                write_json(assignments, active)
            write_json(root / "progress.json", {"state": "running", "generation_busy_gpus": len(busy),
                "running": running, "waiting_measurements": len(waiting), "blocked": blocked,
                "updated_at": time.time()})
            time.sleep(20)


def launch(root, *, cwd=None):
    from behave.eval.controller import launch as launch_controller

    return launch_controller(root, "fleet", cwd=cwd)


RETRY_SCHEMA = "behave.eval-startup-retry/1"
MEMBERS = ("rollout", "generation.json", "generation.log", "manifest.json", "node_status.json", "serving.json")


def _present(path):
    return path.exists() or path.is_symlink()


def _read_record(path):
    return parse_json(read_regular_file(path))


def _retained(path):
    if path.is_symlink():
        raise ValueError("startup recovery receipt cannot be linked")
    return _read_record(path)


def _inputs(lane):
    # Older generation-only lanes do not carry a separate scoring policy.
    return {name: None if name == "judge_policy.json" and not _present(lane / name)
            else _read_record(lane / name) for name in INPUTS}


def _identity(path):
    info = path.lstat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("startup recovery requires regular files and unlinked directories")
    identity = [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_uid]
    return identity + ([info.st_size, info.st_mtime_ns] if stat.S_ISREG(info.st_mode) else [])


def _layout(path):
    """Pin startup-only directories without walking traces or model artifacts."""
    result = {}
    for directory in (path, path / "trained"):
        if _present(directory):
            _identity(directory)
            for child in directory.iterdir():
                result[str(child.relative_to(path))] = _identity(child)
    return result


def begin(root, item, old, entry, configuration, retry_id):
    """Pin identities and generated metadata before moving any failed artifact."""
    root, directory = Path(root), Path(item["directory"])
    lane = assignment_directory(root, item["key"])
    archive = directory / "startup_failures" / retry_id
    if archive.resolve() != archive:
        raise ValueError("startup recovery archive cannot be linked")
    if _present(archive):
        raise ValueError("legacy or incomplete retry directory needs explicit reconciliation")
    handoff = _read_record(checkpoint.checkpoint_directory(lane, entry) / "checkpoint.json")
    before = _read_record(directory / "manifest.json")
    files = checkpoint.measurement_files(lane, entry, item["seed"], handoff, configuration=configuration)
    after = files["manifest.json"]
    if before["checkpoint"] != handoff or {k: v for k, v in before.items() if k != "code"} != {
            k: v for k, v in after.items() if k != "code"}:
        raise ValueError("retry would change the measurement identity")
    record = {"schema": RETRY_SCHEMA, "key": item["key"], "measurement": item, "entry": entry,
        "previous_assignment": old, "configuration": _read_record(lane / "configuration.json"),
        "new_configuration": configuration, "canonical_directory": str(directory),
        "created_at": time.time(), "files": files, "checkpoint": handoff,
        "inputs": _inputs(lane),
        "snapshot": _read_record(checkpoint.snapshot_directory(lane, configuration=configuration).parent / "snapshot.json"),
        "members": {name: _identity(directory / name) for name in MEMBERS if _present(directory / name)},
        "rollout_layout": _layout(directory / "rollout"),
        "controls": {path.name: _identity(path) for path in (directory / "control").iterdir()}}
    publish_json(archive / "intent.json", record)
    return archive


def _archive(directory, archive, record):
    members = record["members"]
    if set(members) - set(MEMBERS):
        raise ValueError("unexpected startup archive member")
    rollout = archive / "rollout" if _present(archive / "rollout") else directory / "rollout"
    if _layout(rollout) != record["rollout_layout"]:
        raise ValueError("failed startup contents changed before recovery")
    committed = archive / "archived.json"
    if _present(committed):
        if _retained(committed) != members or any(_identity(archive / name) != value for name, value in members.items()):
            raise ValueError("retained startup archive changed")
        return
    pending = []
    for name, identity in members.items():
        source, target = directory / name, archive / name
        if _present(target):
            if _present(source) or _identity(target) != identity:
                raise ValueError("ambiguous startup artifact ownership")
        elif _identity(source) != identity:
            raise ValueError("startup artifact changed before archival")
        else:
            pending.append((source, target))
    # Reject a changed later member before moving any earlier evidence.
    for source, target in pending:
        source.rename(target)
    publish_json(committed, members)


def resume(root, item, retry_id, *, submit):
    """Resume pinned preparation under the caller's lock; never replay a launch."""
    root, directory = Path(root), Path(item["directory"])
    archive = directory / "startup_failures" / retry_id
    if archive.resolve() != archive:
        raise ValueError("startup recovery archive cannot be linked")
    record = _retained(archive / "intent.json")
    if (record.get("schema") != RETRY_SCHEMA or record.get("measurement") != item
            or record.get("canonical_directory") != str(directory)):
        raise ValueError("startup recovery identity changed")
    old, config = record["previous_assignment"], record["new_configuration"]
    lane = assignment_directory(root, item["key"])
    control = directory / "control" / retry_id
    replacement = {**{k: v for k, v in old.items() if k != "preparation"},
        "control": str(control), "launch_state": "prepared", "recovery": str(archive / "recovery.json")}
    active = checkpoint.read(root / "assignments.json")
    matches = [index for index, row in enumerate(active) if row["key"] == item["key"]]
    if len(matches) != 1 or active[matches[0]]["control"] not in {old["control"], str(control)}:
        raise ValueError("startup retry aggregate ownership changed")
    if config.get("launch_id") != retry_id or _read_record(lane / "queue.json") != {"checkpoints": [record["entry"]]}:
        raise ValueError("startup retry control or queue changed")
    if observe_control(old["control"])["state"] not in {"failed", "timed-out", "launch-error"}:
        raise ValueError("previous startup owner is no longer terminal and failed")
    current = _read_record(lane / "configuration.json")
    if current not in (record["configuration"], config):
        raise ValueError("startup retry configuration changed")
    if _present(control):
        owner = saved_assignment(root, item)
        if (control.resolve() != control or (control / "launch.json").is_symlink()
                or not (control / "launch.json").is_file() or current != config
                or _retained(archive / "dispatch.json") != replacement
                or {k: v for k, v in owner.items() if k != "launch_state"} != {
                    k: v for k, v in replacement.items() if k != "launch_state"}):
            raise ValueError("existing retry control requires explicit reconciliation")
        launched = observe_control(control)
    else:
        if _present(archive / "dispatch.json"):
            raise ValueError("submitted retry lost its control; explicit reconciliation required")
        owner_path = lane / "owner.json"
        if _present(owner_path):
            owner = saved_assignment(root, item)
            if owner["control"] == str(control) and owner.get("launch_state") not in {"preparing", "prepared"}:
                raise ValueError("submitted retry lost its control; explicit reconciliation required")
        # Until dispatch there must still be exactly the old, pinned controls.
        if {p.name: _identity(p) for p in (directory / "control").iterdir()} != record["controls"]:
            raise ValueError("startup recovery control ownership changed")
        if (_inputs(lane) != record["inputs"]
                or _read_record(checkpoint.snapshot_directory(lane, configuration=config).parent / "snapshot.json") != record["snapshot"]
                or _read_record(checkpoint.checkpoint_directory(lane, record["entry"]) / "checkpoint.json") != record["checkpoint"]):
            raise ValueError("pinned startup retry inputs changed")
        expected = checkpoint.measurement_files(lane, record["entry"], item["seed"],
                                                    record["checkpoint"], configuration=config)
        if expected != record["files"]:
            raise ValueError("pinned startup retry output plan changed")
        _archive(directory, archive, record)
        checkpoint.verify_preparation_directory(directory, retained=("control", "startup_failures"))
        if current != config:
            write_json(lane / "configuration.json", config, durable=True)
        for name, value in record["files"].items():
            target = directory / name
            if _present(target):
                if target.is_symlink() or _read_record(target) != value:
                    raise ValueError("prepared startup retry contents changed")
            else:
                write_json(target, value, durable=True)
        write_json(archive / "recovery.json", {**record, "state": "prepared"}, durable=True)
        record_assignment(root, item, replacement, previous=old)
        # A crash inside submission cannot prove that no worker was launched.
        # Missing control after this boundary needs reconciliation, not replay.
        publish_json(archive / "dispatch.json", replacement)
        launched = submit(lane, 0)
    replacement = {**replacement, "launch_state": launched["state"]}
    record_assignment(root, item, replacement)
    active[matches[0]] = replacement
    write_json(root / "assignments.json", active, durable=True)
    write_json(archive / "recovery.json", {**record, "state": "submitted", "assignment": replacement}, durable=True)
    return replacement


ACTIVE = frozenset({"submitted", "starting", "supervising"})
TERMINAL = frozenset({"completed", "failed", "timed-out", "launch-error"})
OWNER_SCHEMA = "behave.eval-fleet-owner/1"


def observe_control(control):
    from behave.runtime.jobs import status_of

    try:
        return status_of(control)
    except (FileNotFoundError, json.JSONDecodeError) as error:
        # A receipt may not yet exist if the controller died before submission.
        # Neither absence nor a torn read proves that the GPU is unoccupied.
        return {"state": "unknown", "observation_error": type(error).__name__}


def _key(value):
    if not isinstance(value, str) or not value or Path(value).name != value or value in {".", ".."}:
        raise ValueError("measurement key must be a simple directory name")
    return value


def _binding(row):
    key = _key(row["key"])
    if (
        type(row["allocation"]) is not int
        or row["allocation"] <= 0
        or type(row["gpu"]) is not int
        or row["gpu"] < 0
        or not isinstance(row["control"], str)
        or not row["control"]
    ):
        raise ValueError("invalid fleet assignment owner")
    # Capacity is checked against the chosen deployment at preparation time.
    # An archived owner keeps its identity even when the current fleet changes.
    return key, row["allocation"], row["gpu"], row["control"]


def assignment_directory(root, key):
    return Path(root) / "generation" / _key(key)


def _measurement(item):
    for name in ("key", "entry", "seed", "directory"):
        if name not in item:
            raise ValueError("incomplete measurement identity")
    # Queue entries are append-only, including their independent scoring owner.
    return dict(item)


def _check_assignment(item, row):
    if _binding(row)[0] != item["key"]:
        raise ValueError("assignment belongs to another measurement")
    owner_root = (Path(item["directory"]) / "control").resolve()
    control = Path(row["control"]).resolve()
    if control.parent != owner_root:
        raise ValueError("control is outside its measurement ownership directory")


def _read_receipt(path, item):
    record = json.loads(path.read_text())
    if record.get("schema") != OWNER_SCHEMA or record["measurement"] != _measurement(item):
        raise ValueError("fleet owner receipt changed measurement identity")
    _check_assignment(item, record["assignment"])
    previous = record.get("previous")
    if previous is not None:
        _check_assignment(item, previous)
        if _binding(previous)[:3] != _binding(record["assignment"])[:3]:
            raise ValueError("startup recovery cannot silently relocate its GPU owner")
    return record


def record_assignment(root, item, assignment, *, previous=None):
    """Record a launch intent/update under the coordinator's existing lock.

    Only an explicit, already-validated recovery may replace a control. This
    receipt records that transition; it is not itself evidence of old-worker
    fencing. Callers must perform their ordinary cleanup checks first.
    """
    _check_assignment(item, assignment)
    path = assignment_directory(root, item["key"]) / "owner.json"
    if previous is not None:
        _check_assignment(item, previous)
        if _binding(previous)[:3] != _binding(assignment)[:3]:
            raise ValueError("startup recovery cannot silently relocate its GPU owner")
    if path.exists():
        old = _read_receipt(path, item)
        if _binding(old["assignment"]) == _binding(assignment):
            # Submission only adds its returned state; do not rewrite recovery
            # provenance or any other stable assignment fields.
            if {k: v for k, v in old["assignment"].items() if k != "launch_state"} != {
                k: v for k, v in assignment.items() if k != "launch_state"
            }:
                raise ValueError("existing assignment identity changed")
            previous = old.get("previous")
        elif previous is None or _binding(previous) != _binding(old["assignment"]):
            raise ValueError("replacement owner lacks its previous assignment")
    record = {"schema": OWNER_SCHEMA, "measurement": _measurement(item), "assignment": assignment}
    if previous is not None:
        record["previous"] = previous
    write_json(path, record)


def saved_assignment(root, item):
    """Read the immutable owner identity for one explicitly queued measurement."""
    path = assignment_directory(root, item["key"]) / "owner.json"
    return _read_receipt(path, item)["assignment"]


def reconcile_assignments(root, fleet, active):
    """Rebuild missing index entries from receipts, without submitting work.

    Old arrays without receipts remain valid. Unknown or incomplete launches
    retain their slot until the ordinary explicit recovery verifies cleanup.
    All identities are validated before the caller writes the repaired index.
    """
    result = list(active)
    keys = [_key(item["key"]) for item in fleet["measurements"]]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate queued measurement identity")
    positions = {}
    for index, row in enumerate(result):
        key = _binding(row)[0]
        if key in positions:
            raise ValueError("duplicate assigned measurement owner")
        positions[key] = index
    for item in fleet["measurements"]:
        key = _key(item["key"])
        path = assignment_directory(root, key) / "owner.json"
        if not path.exists():
            continue
        record = _read_receipt(path, item)
        row = record["assignment"]
        if key not in positions:
            positions[key] = len(result)
            result.append(row)
            continue
        index = positions[key]
        current = result[index]
        if _binding(current) != _binding(row):
            previous = record.get("previous")
            if previous is None or _binding(previous) != _binding(current):
                raise ValueError("ambiguous fleet ownership transition")
            result[index] = row
        else:
            result[index] = {**current, **row}
    controls = [str(Path(row["control"]).resolve()) for row in result]
    if len(set(controls)) != len(controls):
        raise ValueError("one control cannot own multiple measurements")
    return result


def generation_gpus(node):
    """Eligible device IDs inside the declared allocation; never change TP."""
    _, capacity = node_capacity(node)
    devices = node.get("generation_gpu_ids", list(range(capacity)))
    if (not isinstance(devices, list) or not devices
            or any(type(device) is not int or not 0 <= device < capacity for device in devices)
            or len(set(devices)) != len(devices)):
        raise ValueError("generation_gpu_ids must be distinct GPU IDs within allocated_gpus")
    return list(devices)


def _read(path):
    return json.loads(Path(path).read_text())


def reload_fleet(root, previous):
    """Accept queue additions only; never reinterpret an existing measurement."""
    current = _read(Path(root) / "fleet.json")
    if {k: v for k, v in current.items() if k != "measurements"} != {
        k: v for k, v in previous.items() if k != "measurements"
    }:
        raise ValueError("live fleet update may only append measurements")
    rows = current["measurements"]
    keys = [row["key"] for row in rows]
    identities = [(row["entry"]["model_line"], row["entry"]["update"], row["seed"]) for row in rows]
    directories = [row["directory"] for row in rows]
    if any(len(set(values)) != len(values) for values in (keys, identities, directories)):
        raise ValueError("duplicate live measurement identity")
    indexed = {row["key"]: row for row in rows}
    if any(indexed.get(row["key"]) != row for row in previous["measurements"]):
        raise ValueError("live fleet update changed or removed an existing measurement")
    return current


def publish_score_readiness(fleet):
    """Hand newly queued, completed generation to the existing CPU score owner."""
    for item in fleet["measurements"]:
        destination = item.get("score_ready")
        if destination is None or Path(destination).is_file():
            continue
        directory = Path(item["directory"])
        marker = directory / "generation.json"
        if marker.is_file() and _read(marker).get("state") == "completed":
            write_json(
                Path(destination),
                {
                    "key": item["key"],
                    "source": str(directory / "rollout"),
                    "state": "ready",
                    "generation_completed": True,
                },
            )


def generation_slot(fleet, item, busy, available_nodes):
    """Borrow an idle GPU without changing the item's independent scoring owner."""
    primary = item["allocation"]
    candidates = [primary]
    for allocation, node in fleet["nodes"].items():
        lanes = node.get("overflow_lanes", [])
        if int(allocation) != primary and ("*" in lanes or item["entry"]["model_line"] in lanes):
            candidates.append(int(allocation))
    return next(
        (
            {"allocation": allocation, "gpu": gpu}
            for allocation in candidates
            if allocation in available_nodes
            for gpu in generation_gpus(fleet["nodes"][str(allocation)])
            if (allocation, gpu) not in busy
        ),
        None,
    )


def measurement_config(root, item, slot):
    fleet = _read(Path(root) / "fleet.json")
    base = _read(Path(fleet.get("configuration_file", Path(fleet["archive_root"]) / "configuration.json")))
    node = fleet.get("nodes", {}).get(str(slot["allocation"]), {})
    allocated_cpus, allocated_gpus = node_capacity(node)
    ports = fleet.get("serving_ports", {"http_first": 24000, "nccl_first": 25000, "slot_stride": 32})
    assigned = [ports[kind] + gpu * ports["slot_stride"]
                for kind in ("http_first", "nccl_first") for gpu in range(allocated_gpus)]
    if len(set(assigned)) != 2 * allocated_gpus or any(not 1024 <= port < 32768 for port in assigned):
        raise ValueError("serving ports must be distinct and below the default ephemeral range")
    devices = generation_gpus(node)
    if slot["gpu"] not in devices:
        raise ValueError("measurement GPU is not eligible for generation")
    cpus = execution_cpu_ids(node, node["generation_cpu_ids"])
    cpu_ids = cpus[devices.index(slot["gpu"]) :: len(devices)]
    if not cpu_ids:
        raise ValueError("each generation GPU requires at least one CPU")
    workers = node.get("rollout_workers_per_gpu", fleet.get("rollout_workers_per_gpu"))
    if (not isinstance(workers, list) or len(workers) != allocated_gpus
            or any(type(workers[gpu]) is not int or workers[gpu] < 1 for gpu in devices)):
        raise ValueError("rollout_workers_per_gpu must declare a positive budget for each eligible GPU")
    # Allocation identity belongs to the selected node, not the archived recipe.
    base_resources = dict(base["resources"])
    base_resources.pop("allocated_cpu_ids", None)
    resources = {
        **base_resources,
        **({"allocated_cpu_ids": node["allocated_cpu_ids"]} if "allocated_cpu_ids" in node else {}),
        "allocated_cpus": allocated_cpus,
        "allocated_gpus": allocated_gpus,
        "allocation": slot["allocation"],
        "gpu_count": 1,
        "engines": 1,
        "tensor_parallel_per_engine": 1,
        "device_groups": [str(slot["gpu"])],
        "generation_cpu_ids": cpu_ids,
        "generation_cpus": len(cpu_ids),
        "rollout_workers": workers[slot["gpu"]],
        "port_first": ports["http_first"] + slot["gpu"] * ports["slot_stride"],
        "nccl_port_first": ports["nccl_first"] + slot["gpu"] * ports["slot_stride"],
        "lease_serving_ports": True,
    }
    return {
        **base,
        "archive_root": fleet["archive_root"],
        "code_directory": fleet["code_directory"],
        "launch_id": fleet["name"],
        "independent_measurement": True,
        "resources": resources,
        "isolation_startup_slots": fleet.get("isolation_startup_slots"),
        **({"generation_recovery": item["generation_recovery"]} if item.get("generation_recovery") else {}),
        "serving_probe_request": fleet["serving_probe_request"],
    }


INPUTS = ("task_index.json", "sampling.json", "judge_policy.json", "serving_template.json")


def _retain_json(path, value):
    if path.is_symlink():
        raise ValueError("preparation record cannot be a symlink")
    if path.exists():
        if _read(path) != value:
            raise ValueError("saved preparation identity changed")
    else:
        write_json(path, value)


def _check_resources(assignment, configuration):
    # This fleet allocates single-GPU slots. A different serving topology is
    # valid for the study planner, but cannot silently consume this slot's peers.
    resources = configuration["resources"]
    expected = {"allocation": assignment["allocation"], "gpu_count": 1, "engines": 1,
                "tensor_parallel_per_engine": 1, "device_groups": [str(assignment["gpu"])]}
    if any(resources.get(key) != value for key, value in expected.items()):
        raise ValueError("preparation resources differ from the assigned GPU slot")
    checkpoint.validate_generation_resources(resources)


def begin_preparation(root, item, slot, configuration, handoff):
    """Record all launch inputs before writing configuration or preparing data."""
    lane = assignment_directory(root, item["key"])
    if _present(lane / "configuration.json") or _present(lane / "owner.json"):
        raise ValueError("measurement already has an execution owner")
    directory = Path(item["directory"])
    if _present(directory):
        raise ValueError("unowned measurement artifacts require explicit recovery")
    launch = configuration["launch_id"]
    if not launch or Path(launch).name != launch or launch in {".", ".."}:
        raise ValueError("launch ID must be a simple directory name")
    if configuration.get("independent_measurement") is not True:
        raise ValueError("fleet preparation requires an independent measurement")
    _check_resources(slot, configuration)
    archive = Path(configuration["archive_root"])
    assignment = {**slot, "key": item["key"], "control": str(directory / "control" / launch),
        "launch_state": "preparing", "preparation": {
            "schema": "behave.eval-fleet-preparation/1", "configuration": configuration,
            "checkpoint": handoff, "inputs": {name: _read(archive / name) for name in INPUTS},
            "snapshot": _read(archive / configuration["code_directory"] / "snapshot.json")}}
    record_assignment(root, item, assignment)
    return assignment


def resumable_preparation(assignment):
    return (assignment.get("launch_state") in {"preparing", "prepared"}
            and assignment.get("preparation", {}).get("schema") == "behave.eval-fleet-preparation/1"
            and not _present(Path(assignment["control"])))


def continue_preparation(root, item, assignment, *, submit):
    """Prepare with the shared planner, then invoke the existing submit operation."""
    if saved_assignment(root, item) != assignment:
        raise ValueError("preparation differs from its durable owner")
    if not resumable_preparation(assignment):
        return assignment
    plan = assignment["preparation"]
    config, handoff = plan["configuration"], plan["checkpoint"]
    _check_resources(assignment, config)
    archive, directory = Path(config["archive_root"]), Path(item["directory"])
    verify_preparation_directory(directory)
    if set(plan["inputs"]) != set(INPUTS) or any(
        _read(archive / name) != plan["inputs"][name] for name in INPUTS
    ) or _read(archive / config["code_directory"] / "snapshot.json") != plan["snapshot"]:
        raise ValueError("preparation inputs or worker snapshot changed")
    lane = assignment_directory(root, item["key"])
    entry = {**item["entry"], "inference_seeds": [item["seed"]]}
    _retain_json(lane / "configuration.json", config)
    if (checkpoint.measurement_directory(lane, entry, item["seed"]).resolve() != directory.resolve()
            or checkpoint.control_directory(lane, entry).resolve() != Path(assignment["control"]).resolve()):
        raise ValueError("preparation changed measurement or control path")
    _retain_json(lane / "queue.json", {"checkpoints": [entry]})
    for name in INPUTS:
        link, target = lane / name, archive / name
        if _present(link):
            if not link.is_symlink() or link.resolve() != target.resolve():
                raise ValueError("preparation input link changed")
        else:
            link.symlink_to(target.resolve())
    _retain_json(checkpoint.checkpoint_directory(lane, entry) / "checkpoint.json", handoff)
    checkpoint.prepare_measurement(lane, entry, item["seed"], handoff, resume_preparation=True)
    prepared = {**assignment, "launch_state": "prepared"}
    record_assignment(root, item, prepared)
    launched = submit(lane, 0)
    result = {**prepared, "launch_state": launched["state"]}
    record_assignment(root, item, result)
    return result
