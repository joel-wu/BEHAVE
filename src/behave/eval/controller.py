"""Shared launch recipes and a foreground entry for saved Eval controllers.

The foreground command belongs inside an existing Slurm batch/step. It neither
requests allocations nor fences workers; it executes the selected frozen entry.
Scheduler requeue policy remains with the batch owner.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

from behave.core.records import canonical_json

from . import checkpoint as checkpoint_module


def _configuration(root, kind):
    if kind == "stages":
        root = Path(root)
        stages = checkpoint_module.read(root / "stages.json")
        if (not isinstance(stages, list) or not stages
                or any(not isinstance(stage, str) or not stage.strip() for stage in stages)):
            raise ValueError("stages.json must list prepared stage directories")
        roots = [(root / stage).resolve() for stage in stages]
        if len(set(roots)) != len(roots):
            raise ValueError("duplicate stage roots")
        snapshots = {checkpoint_module.snapshot_directory(stage).resolve() for stage in roots}
        allocations = [checkpoint_module.read(stage / "configuration.json")["resources"]["allocation"]
                       for stage in roots]
        if (len(snapshots) != 1 or any(type(value) is not int or value < 1 for value in allocations)
                or len(set(allocations)) != 1):
            raise ValueError("stages require one frozen snapshot and one allocation")
        return {"stages": [str(stage) for stage in roots], "snapshot": str(snapshots.pop()),
                "allocation": allocations[0]}
    if kind not in {"checkpoint", "fleet"}:
        raise ValueError("controller kind must be checkpoint, fleet or stages")
    return checkpoint_module.read(Path(root) / ("fleet.json" if kind == "fleet" else "configuration.json"))


def _command(root, kind, config):
    """Keep historical frozen bootstraps, independent of the launch host."""
    root = Path(root)
    if kind == "stages":
        snapshot = Path(config["snapshot"])
        boot = """import os, sys
from pathlib import Path
snapshot = Path(sys.argv.pop(1))
sys.path[:0] = [str(snapshot), sys.argv.pop(1)]
from tools.eval_checkpoint_study import run_stages
from behave.eval.checkpoint import read, snapshot_directory
from behave.runtime.jobs import node_heartbeat
root = Path(sys.argv[1])
stages = [Path(stage) for stage in sys.argv[2:]]
for stage in stages:
    allocation = read(stage / 'configuration.json')['resources']['allocation']
    if (type(allocation) is not int or str(allocation) != os.environ.get('SLURM_JOB_ID')
            or snapshot_directory(stage).resolve() != snapshot.resolve()):
        raise SystemExit('saved stage placement or snapshot changed before entry')
with node_heartbeat(root / 'controller_node.json', interval=30):
    raise SystemExit(run_stages(stages, progress_directory=root))
"""
        tail = [str(root), *config["stages"]]
        metadata = {"purpose": "prepared Eval stages with independent GPU generation and CPU scoring"}
    elif kind == "checkpoint":
        snapshot = checkpoint_module.snapshot_directory(root)
        boot = ("import sys;from pathlib import Path;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
                "from tools.eval_checkpoint_study import run;raise SystemExit(run(Path(sys.argv[1])))")
        tail = [str(root)]
        metadata = {"purpose": "checkpoint-first multi-seed eval queue",
                    "configuration": str(root / "configuration.json")}
    else:
        snapshot = Path(config["archive_root"]) / config["code_directory"] / "snapshot"
        boot = ("import sys;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
                "from tools.eval_checkpoint_fleet import run;raise SystemExit(run(sys.argv[1]))")
        tail = [str(root)]
        metadata = {"purpose": "work-conserving eval GPU slots and separate CPU consumers"}
        if config.get("controller_script"):
            script = Path(config["controller_script"])
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            if digest != config["controller_sha256"]:
                raise ValueError("controller script identity changed")
            boot = ("import sys,runpy;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
                    "raise SystemExit(runpy.run_path(sys.argv[1])['run'](sys.argv[2]))")
            tail = [str(script), str(root)]
            metadata.update(controller_script=str(script), controller_sha256=digest)
    return [sys.executable, "-I", "-B", "-u", "-c", boot, str(snapshot), str(snapshot / "src"), *tail], metadata


def _control(root, kind, config):
    if kind == "stages":
        return root / "control"
    return (root / config.get("controller_control", "control") if kind == "fleet"
            else checkpoint_module.control_directory(root))


def _foreground_command(root, control, command):
    # The process that runs the frozen controller holds this lock itself. A
    # parent-only lock would disappear if its wrapper died while the child lived.
    # Import the ownership primitive from the selected snapshot, never checkout.
    boot = """import sys
from pathlib import Path
entry, control, program = Path(sys.argv.pop(1)), Path(sys.argv.pop(1)), sys.argv.pop(1)
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from behave.task_execution.run_store import RunStore
with RunStore(entry).owner():
    if control.exists() or control.is_symlink():
        raise SystemExit('existing detached controller records require explicit reconciliation; no automatic takeover')
    exec(program)
"""
    return [*command[:5], boot, str(root / "controller-entry"), str(control), command[5], *command[6:]]


def launch(root, kind, *, cwd=None):
    """Legacy detached placement; repeated calls only observe the saved owner."""
    from behave.runtime.jobs import status_of, submit
    from behave.task_execution.run_store import RunStore

    if kind == "stages":
        raise ValueError("multi-stage controllers require the foreground compute-node entry")
    root = Path(root)
    config = _configuration(root, kind)
    control = _control(root, kind, config)
    if (control / "launch.json").exists():
        return status_of(control)
    argv, metadata = _command(root, kind, config)
    with RunStore(root / "controller-entry").owner():
        if (control / "launch.json").exists():
            return status_of(control)
        return submit(control, argv, cwd=Path.cwd() if cwd is None else Path(cwd),
                      wall_seconds=None, cleanup_seconds=300, metadata=metadata)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("checkpoint", "fleet", "stages"))
    parser.add_argument("root", type=Path, help="existing queue/fleet directory, or a lane containing stages.json")
    parser.add_argument("--allocation", required=True, type=int,
                        help="expected enclosing Slurm job ID, not a new allocation request")
    args = parser.parse_args(argv)
    try:
        if (args.allocation < 1 or os.environ.get("SLURM_JOB_ID") != str(args.allocation)
                or not os.environ.get("SLURMD_NODENAME")):
            raise ValueError("foreground controller must run inside the specified Slurm allocation on a compute node")
        root = args.root.resolve()
        config = _configuration(root, args.kind)
        if args.kind == "stages" and config["allocation"] != args.allocation:
            raise ValueError("saved stages do not belong to the enclosing allocation")
        command, _ = _command(root, args.kind, config)
        # The process stays in the enclosing batch/step. Do not detach another
        # supervisor onto the login host or reinterpret any worker receipts.
        control = _control(root, args.kind, config)
        if control.exists() or control.is_symlink():
            raise ValueError("existing detached controller records require explicit reconciliation; no automatic takeover")
        os.execv(sys.executable, _foreground_command(root, control, command))
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(canonical_json({"status": "unavailable", "code": "evaluation-controller-invalid",
                              "message": str(error)}).decode())
        return 1
    return 0
