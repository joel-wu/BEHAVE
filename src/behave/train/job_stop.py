"""Stop only the authenticated execute child; never cancel an allocation.

Run on the allocation. SIGTERM asks the existing trainer to quiesce and clean
its owned workers. A successful signal is not a cleanup receipt.

Keep this module standard-library-only: the selected checkout's copy runs under
the node's system Python, without an installed BEHAVE or native training stack.
"""

import argparse
import json
import os
import signal
import socket
from datetime import UTC, datetime
from pathlib import Path


def execute_child(job, node, runtime):
    pending = [node["pid"]]
    matches = []
    while pending:
        pid = pending.pop()
        try:
            command = [part.decode() for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]
            pending.extend(int(value) for value in Path(f"/proc/{pid}/task/{pid}/children").read_text().split())
        except FileNotFoundError:
            continue
        if (str(job) in command and runtime["local_directory"] in command
                and any("execute(*sys.argv" in part for part in command)):
            matches.append(pid)
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one owned execute process, found {matches}")
    return matches[0]


def stop(job, wrapper_pid, started_at, reason):
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("owned stop requires the node's /usr/bin/python3 with pidfd support")
    job = Path(job).resolve()
    runtime = json.loads((job / "runtime.json").read_text())
    node = json.loads((job / "node_status.json").read_text())
    if (node["state"] != "live" or node["host"] != socket.gethostname()
            or node["host"] != runtime["host"] or node["pid"] != wrapper_pid
            or node["started_at"] != started_at):
        raise RuntimeError("node wrapper identity changed; refusing to stop")
    pid = execute_child(job, node, runtime)
    descriptor = os.pidfd_open(pid)
    try:
        # Validate again after obtaining a stable process handle (PID reuse safe).
        command = [part.decode() for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]
        if (str(job) not in command or runtime["local_directory"] not in command
                or not any("execute(*sys.argv" in part for part in command)):
            raise RuntimeError("execute process changed before stop")
        record = {"requested_at": datetime.now(UTC).isoformat(), "owner_pid": pid,
                  "wrapper": node, "runtime_before_stop": runtime, "command": command,
                  "signal": "SIGTERM", "reason": reason, "files_removed": False}
        with (job / "operator_stop.json").open("x") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
    finally:
        os.close(descriptor)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--wrapper-pid", type=int, required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(stop(args.job, args.wrapper_pid, args.started_at, args.reason)))


if __name__ == "__main__":
    main()
