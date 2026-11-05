"""Legacy checkpoint watcher CLI and compatibility exports.

Production boundary maintenance uses behave.train.jobs.wait_and_pause.
The standalone --owner-pid / --stop-script watcher remains available unchanged.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from behave.train.jobs import (
    checkpoint_event,
    cleanup_confirmed,
    require_live_allocation,
    storage_observation,
    verify_checkpoint,
)

__all__ = [
    "checkpoint_event",
    "cleanup_confirmed",
    "main",
    "require_live_allocation",
    "storage_observation",
    "verify_checkpoint",
    "wait_and_pause",
]


def wait_and_pause(job, allocation, completed_updates, *, poll_seconds):
    """Retain the old signature and source-checkout stop helper location."""
    from behave.train.jobs import wait_and_pause as pause

    return pause(job, allocation, completed_updates, poll_seconds=poll_seconds,
                 stop_script=Path(__file__).with_name("slime_stop_owned.py"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--allocation", type=int, required=True)
    parser.add_argument("--owner-pid", type=int, required=True)
    parser.add_argument("--after-updates", type=int, required=True)
    parser.add_argument("--interval-seconds", type=float, required=True)
    parser.add_argument("--stop-script", type=Path, required=True)
    args = parser.parse_args()
    job = args.job.resolve()
    print(json.dumps({"state": "waiting", "job": str(job),
        "after_updates": args.after_updates, "owner_pid": args.owner_pid,
        "interval_seconds": args.interval_seconds}), flush=True)
    while True:
        event = checkpoint_event(job, args.after_updates)
        if event is not None:
            break
        runtime = json.loads((job / "runtime.json").read_text())
        control = json.loads((job / "control/status.json").read_text())
        if (runtime["state"] in {"completed", "interrupted"}
                or control["state"] in {"completed", "failed", "timed-out", "interrupted"}):
            print(json.dumps({"state": "ended_before_target", "runtime_state": runtime["state"],
                              "control_state": control["state"]}), flush=True)
            return 1
        time.sleep(args.interval_seconds)
    command = ["srun", f"--jobid={args.allocation}", "--overlap", "--exact", "--propagate=NONE",
        "-N1", "-n1", "--cpus-per-task=1", "--cpu-bind=threads", "--gres=none", "/usr/bin/prlimit",
        "--nproc=unlimited:unlimited", sys.executable, "-B", str(args.stop_script.resolve()),
        str(job), "--owner-pid", str(args.owner_pid), "--reason",
        f"User-approved pause after {args.after_updates} committed updates for sampler/recovery review"]
    print(json.dumps({"state": "checkpoint_observed", "event": str(event),
                      "at": datetime.now(UTC).isoformat()}), flush=True)
    subprocess.run(command, check=True)
    print(json.dumps({"state": "stop_requested", "cleanup_confirmed": False}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
