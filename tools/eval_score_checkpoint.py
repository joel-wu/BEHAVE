"""Persist committed score prefixes without copying mutable execution scratch.

The scorer uses this at each case commit. The CLI also checkpoints an existing
frozen run without changing its code or writing into its owned judgment journal.
No scoring, model calls, candidate cancellation or result promotion happens here.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

from behave.core.records import canonical_json
from behave.core.storage import install_exclusive
from behave.eval.score_stream import ScoreCheckpoint
from behave.eval.scoring import StagedObjects
from behave.task_execution.run_store import RunStore


def checkpoint_pass(directory, *, shard=0, checkpoints=None):
    """One non-mutating-source checkpoint sweep on the execution node."""
    directory = Path(directory)
    plan = json.loads((directory / "plan.json").read_bytes())
    live_path = directory / f"live-{shard}.json"
    if not live_path.exists():
        return False, []
    live = json.loads(live_path.read_bytes())
    checkpoints = {} if checkpoints is None else checkpoints
    rows = []
    for index, source in enumerate(plan["shards"][shard]):
        status = live["sources"].get(source, {})
        if status.get("state") == "completed":
            continue  # The scorer has already published the complete source group.
        runtime_path = Path(source) / "run/judgments" / plan["name"] / "runtime.json"
        if not runtime_path.exists():
            continue
        runtime = json.loads(runtime_path.read_bytes())
        if runtime["host"] != socket.gethostname() or runtime["state"] == "archived":
            continue
        local = Path(runtime["local_directory"]) / "run"
        if not local.is_dir():
            raise FileNotFoundError(f"unarchived scoring workspace is missing: {local}")
        key = str(local)
        if key not in checkpoints:
            original = RunStore(Path(source) / "run")
            destination = RunStore(directory / "checkpoints" / str(shard) / str(index), objects=original.objects)
            identity = canonical_json({"source": source, "judgment": plan["name"],
                                       "host": runtime["host"], "local_directory": str(local)})
            if (not install_exclusive(destination.directory, "source.json", identity)
                    and (destination.directory / "source.json").read_bytes() != identity):
                raise ValueError("checkpoint directory belongs to a different scoring workspace")
            local_run = RunStore(local, objects=StagedObjects(local / "objects", original.objects.directory))
            local_target = RunStore(local / "judgments" / plan["name"], objects=local_run.objects)
            checkpoints[key] = ScoreCheckpoint(local_target, destination)
        checkpoint = checkpoints[key]
        with checkpoint.destination.owner():
            count = checkpoint.publish()
        rows.append({"source": source, "new_cases": count, "directory": str(checkpoint.destination.directory)})
    terminal = all(live["sources"].get(source, {}).get("state") in {"completed", "failed", "cancelled"}
                   for source in plan["shards"][shard])
    return terminal, rows


def main():
    from behave.core.process_limits import configure_worker_process_limit

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--poll-seconds", type=float)
    args = parser.parse_args()
    if args.poll_seconds is not None and not (0 < args.poll_seconds < float("inf")):
        parser.error("--poll-seconds must be positive and finite")
    configure_worker_process_limit()
    for directory in args.directories:
        checkpoints = {}
        while True:
            terminal, rows = checkpoint_pass(directory, shard=args.shard, checkpoints=checkpoints)
            if rows:
                print(json.dumps({"pass": str(directory), "checkpoints": rows}), flush=True)
            if terminal or args.poll_seconds is None:
                break
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
