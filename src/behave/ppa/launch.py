"""Shared PPA campaign launch and node staging over explicit deployment inputs.

The source repository, allocation and CPU budget belong to the caller. Saved
journals remain on the selected durable filesystem; only code/runtime scratch
is staged locally. Existing JobControl, snapshot and ownership rules apply.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from behave.core.file_input import read_regular_file
from behave.core.records import parse_json
from behave.runtime.records import write_json

from .campaign import export_traces, run, run_group
from .campaign_inputs import freeze_plan, publish, validate_durable_campaign
from .campaign_recovery import recovery_binding
from .planning import campaign_slots as slots
from .planning import campaign_wall
from .planning import validate_campaign as validate_plan
from .planning import validate_campaign_group as validate_group
from .staging import service_cleanup as _service_cleanup

def _submission_inputs(repository, allocation, cpus, runtime_directory):
    """Reject deployment mistakes before copying inputs or creating a job."""
    if os.environ.get("SLURM_STEP_ID") is not None:
        raise ValueError("submit from the login host; a detached supervisor cannot outlive its submitting Slurm step")
    if type(allocation) is not int or allocation <= 0 or type(cpus) is not int or cpus <= 0:
        raise ValueError("allocation and CPUs must be positive integers")
    if runtime_directory is not None and (not Path(runtime_directory).is_absolute()
            or Path(runtime_directory).parent == Path(runtime_directory)):
        raise ValueError("runtime directory must be an explicit absolute directory")
    repository = Path(repository).resolve()
    if not repository.is_dir():
        raise ValueError("source repository must be an existing directory")
    return repository


def run_staged(root):
    """Own this campaign through execution, archive commit and staging release."""
    from behave.agent.run_store import JournalDirectory

    root = Path(root).resolve()
    with JournalDirectory(root).owner():
        return _run_staged(root)


def release_staged(root):
    """Resume only a committed scratch release on its original boot; no rollout."""
    from behave.agent.run_store import JournalDirectory

    from . import staging

    root = Path(root).resolve(strict=True)
    with JournalDirectory(root).owner():
        return staging.release(root)


def _run_staged(root):
    """Keep journals on shared storage; stage only code and runtime scratch locally."""
    import shutil

    from behave.core.artifacts import BlobRef
    from behave.core.lifecycle import CleanupState
    from behave.core.process_limits import configure_worker_process_limit
    from behave.core.storage import install_exclusive
    from behave.isolation.scratch import ScratchDirectory
    from behave.runtime.snapshots import stage

    from . import staging

    if not install_exclusive(root, "node-dispatch.json", b'{"requested":true}\n'):
        raise RuntimeError("campaign already dispatched; inspect its existing job and archive, never redispatch")
    for path, digest in json.loads((root / "inputs.json").read_text()).items():
        if BlobRef.of(Path(path).read_bytes()).sha256 != digest:
            raise ValueError(f"campaign input changed before staging: {path}")
    settings = json.loads((root / "launch-settings.json").read_text())
    original_cpus = sorted(os.sched_getaffinity(0))
    offset = settings.get('cpu_offset', 0)
    if type(offset) is not int or not 0 <= offset < len(original_cpus):
        raise ValueError('CPU offset must select a nonempty subset of the allocated step')
    process_limit = configure_worker_process_limit()
    group = json.loads((root / "group.json").read_text()) if (root / "group.json").exists() else None
    plans, plan_inputs = staging.plans(root)
    execution = root / "execution"
    execution.mkdir()
    scratch = ScratchDirectory(prefix="bppa-", parent=Path("/tmp"), owner_marker=staging.MARKER,
                               cleanup=lambda: CleanupState.UNKNOWN)
    local = scratch.path
    staging.record_owner(root, local)
    runtime = {"host": socket.gethostname(), "pid": os.getpid(),
               "local_directory": str(local), "execution_directory": str(execution),
               "storage": "shared", "state": "staging",
               "cpus": original_cpus[offset:],
               "process_limit": process_limit}
    old_environment = {key: os.environ.get(key) for key in ("BEHAVE_RUNTIME_DIR", "BEHAVE_EDA_ROOT")}
    completed, code = False, None
    try:
        write_json(root / "runtime.json", runtime, durable=True)
        if offset:
            os.sched_setaffinity(0, original_cpus[offset:])
        staged = stage(root / "snapshot", local / "snapshot")
        os.environ["BEHAVE_RUNTIME_DIR"] = settings.get("runtime_directory") or str(local / "runtime")
        os.environ["BEHAVE_EDA_ROOT"] = str((local / "snapshot/external/oss-cad-suite").resolve())
        if group is None:
            freeze_plan(plans[0][1], execution)
            for name in ("recovery.json", "preflight-gate.json"):
                if (root / name).exists():
                    shutil.copy2(root / name, execution / name)
        write_json(root / "runtime.json", {**runtime, "state": "running"})
        code = (run(execution, snapshot=local / "snapshot") if group is None else
                run_group(execution, snapshot=local / "snapshot", group=group))
        completed = True
    finally:
        try:
            export_traces(execution)
            # Source captures, provider receipts and journals already reside in
            # execution. Their survival must not depend on reaching this block.
            cleanup = _service_cleanup(execution, plans) if completed and code == 0 else CleanupState.UNKNOWN
            ready = completed and code == 0 and cleanup.quiescent
            if ready:
                # The copied source remains in the verified shared snapshot;
                # journals/accounts have always lived outside this scratch.
                staging.prepare_release(root, runtime, staged["sha256"], plan_inputs)
            terminal = {**runtime, "state": "archived" if completed else "failed-retained",
                        "scratch": "release-ready" if ready else "retained"}
            write_json(root / "runtime.json", terminal, durable=True)
        finally:
            if offset:
                os.sched_setaffinity(0, original_cpus)
            for key, value in old_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if ready:
            staging.release(root)
    return code


def submit_group(group_path, root, allocation, cpus, *, repository, runtime_directory=None, cpu_offset=0,
                 recover_from=None):
    """One durable worker and one CPU pool for several existing campaign plans."""
    from behave.core.artifacts import BlobRef
    from behave.runtime.snapshots import freeze

    repository = _submission_inputs(repository, allocation, cpus, runtime_directory)
    if root.exists():
        raise ValueError("use a fresh campaign directory; no automatic resume")
    if type(cpu_offset) is not int or not 0 <= cpu_offset < cpus:
        raise ValueError('CPU offset must leave at least one allocated CPU')
    group = validate_group(parse_json(read_regular_file(group_path)))
    plans = {name: validate_plan(parse_json(read_regular_file(path))) for name, path in group["plans"].items()}
    validate_durable_campaign(root, plans.values())
    if any(p.get("cpus") is not None or p.get("cpu_workers") is not None for p in plans.values()):
        raise ValueError("group plans cannot override shared CPU scheduling")
    frozen = freeze(repository, root / "snapshot", extra_files=("tools/ppa_campaign.py",))
    inputs, durations, paths = {}, [], {}
    for name, plan in plans.items():
        target = root / "campaigns" / name
        binding = recovery_binding(plan, recover_from / name) if recover_from is not None else None
        plan = freeze_plan(plan, target)
        if binding is not None:
            publish(target / 'recovery.json', binding)
            inputs[str(target / 'recovery.json')] = BlobRef.of((target / 'recovery.json').read_bytes()).sha256
        paths[name] = str(target / "plan.json")
        inputs.update(json.loads((target / "inputs.json").read_text()))
        inputs[paths[name]] = BlobRef.of((target / "plan.json").read_bytes()).sha256
        batches = (len(slots(plan, target)) + plan["parallelism"] - 1) // plan["parallelism"]
        durations.append(campaign_wall(plan, batches))
    publish(root / "group.json", {**group, "plans": paths})
    inputs[str(root / "group.json")] = BlobRef.of((root / "group.json").read_bytes()).sha256
    publish(root / "inputs.json", inputs)
    publish(root / "launch-settings.json", {"runtime_directory": runtime_directory,
                                           "cpu_offset": cpu_offset})
    # CPU admission can serialize work across campaigns; sum their envelopes.
    wall = None if any(value is None for value in durations) else sum(durations) + 600
    return dispatch(root, allocation, cpus, wall, frozen, repository=repository)


def submit(plan_path, root, allocation, cpus, preflight=None, recover_from=None, *, repository, runtime_directory=None):
    from behave.runtime.snapshots import freeze

    repository = _submission_inputs(repository, allocation, cpus, runtime_directory)
    plan = validate_plan(parse_json(read_regular_file(plan_path)))
    validate_durable_campaign(root, (plan,))
    if root.exists():
        raise ValueError("use a fresh campaign directory; no automatic resume")
    binding = recovery_binding(plan, recover_from) if recover_from is not None else None
    frozen = freeze(repository, root / "snapshot", extra_files=("tools/ppa_campaign.py",))
    if preflight is not None:
        prior = json.loads((preflight / "snapshot.json").read_text())
        mismatches = [name for name, digest in frozen["files"].items()
                      if name.startswith("src/behave/") and prior["files"].get(name) != digest]
        if mismatches:
            raise ValueError(f"preflight and campaign package snapshots differ: {mismatches}")
        preflight_wall = json.loads((preflight / "preflight-plan.json").read_text())["wall_seconds"]
        publish(root / "preflight-gate.json", {"directory": str(preflight),
            "snapshot": prior["sha256"], "candidate_excluded": True, "wait_seconds": preflight_wall + 120})
    plan = freeze_plan(plan, root)
    publish(root / "launch-settings.json", {"runtime_directory": runtime_directory})
    if binding is not None:
        publish(root / "recovery.json", binding)
    batches = (len(slots(plan, root)) + plan["parallelism"] - 1) // plan["parallelism"]
    wall = campaign_wall(plan, batches)
    if wall is not None:
        wall += 600 + (preflight_wall + 120 if preflight else 0)
        if binding is not None:
            wall += binding["wait_seconds"]
    return dispatch(root, allocation, cpus, wall, frozen, repository=repository)


def dispatch(root, allocation, cpus, wall, frozen, *, repository):
    from behave.agent.jobs import JobControl, JobRequest
    from behave.core import runtime_defaults as defaults
    from behave.runtime.jobs import submit as detached

    repository = Path(repository).resolve()
    bootstrap = ("import sys; sys.path.insert(0,sys.argv.pop(1)); from pathlib import Path; "
                 "from behave.ppa.launch import run_staged; raise SystemExit(run_staged(Path(sys.argv[1])))")
    command = (sys.executable, "-I", "-B", "-c", bootstrap, str(root / "snapshot/src"), str(root))
    request = JobRequest(command, str(repository), wall_seconds=wall, cpus=cpus,
                         allocation=allocation, network="host", output_bytes=defaults.JOB_OUTPUT_BYTES)
    job = root / "job"
    key = JobControl(job).create(request)
    worker = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
              "from behave.core.process_limits import configure_worker_process_limit; "
              "configure_worker_process_limit(); "
              "from behave.agent.job_worker import main; raise SystemExit(main())")
    argv = [*request.slurm_prefix(overlap=True), sys.executable, "-I", "-B", "-c", worker,
            str(root / "snapshot/src"), "run", str(job), key]
    return detached(root / "supervision", argv, cwd=repository,
                    wall_seconds=None if wall is None else wall + 120,
                    metadata={"purpose": "PPA comparative campaign", "snapshot": frozen["sha256"],
                              "allocation": allocation, "cpus": cpus, "job_key": key})
