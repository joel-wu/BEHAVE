"""Private torchrun adapter inside the existing trainer PID namespace.

All ranks must return valid states and torchrun must exit zero. The outer service
owns the execution deadline and kills the entire namespace on any failed cleanup.
Rank code is trusted trainer code, not a sandbox for submitted artifacts.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from behave.agent.jobs import JobControl, JobRequest, validate_worker
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, fields, parse_json
from behave.core.storage import install_exclusive
from behave.isolation.service import ServiceResult

from .checkpoints import verify_state
from .cluster_state import pack_rank_states, unpack_rank_states
from .trainer import BackendUpdate, TrainerProcess, read_record, response_bytes, response_state

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def rank_main(envelope_path: Path) -> int:
    # torchrun owns RANK/LOCAL_RANK/WORLD_SIZE and env:// rendezvous variables.
    rank = int(os.environ["RANK"])
    world = int(os.environ["WORLD_SIZE"])
    envelope = parse_json(envelope_path.read_bytes())
    os.environ["BEHAVE_TRAIN_RANK"] = str(rank)
    os.environ["BEHAVE_TRAIN_WORLD_SIZE"] = str(world)
    call_path = envelope_path.parent / f"rank-{rank}.input.json"
    call = parse_json(call_path.read_bytes())
    # Reuse the normal backend protocol. Rank stdout is copied completely and
    # decoded by the parent; diagnostic stderr uses the service's log retention.
    output_path = envelope_path.parent / f"rank-{rank}.response.json"
    with output_path.open("xb") as output, subprocess.Popen(
        envelope["argv"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    ) as child:
        child.stdin.write(canonical_json(call))
        child.stdin.close()
        while chunk := child.stdout.read(65536):
            output.write(chunk)
        code = child.wait()
    if code != 0:
        return code if code > 0 else 128 - code
    return 0


def prepare_ranks(update: BackendUpdate, root: Path, ranks: range) -> Path:
    """Prepare only this node's original rank-local states and invocations."""
    states = unpack_rank_states(update.state, update.world_size)
    from behave.core.storage import DirectoryStore

    inputs = DirectoryStore(update.inputs.directory)
    for rank in ranks:
        state = states[rank]
        invocation = inputs.put(canonical_json({
            "schema": "behave.trainer-invocation/2", "process": update.process.as_dict(),
            "contract": parse_json(update.contract.to_bytes()), "request": parse_json(update.request.to_bytes()),
            "state": parse_json(state.to_bytes()), "examples": [ref.as_dict() for ref in update.examples],
        }))
        (root / f"rank-{rank}.input.json").write_bytes(canonical_json({
            "invocation": invocation.as_dict(), "input": str(update.inputs.directory),
            "output": str(update.output.directory),
        }))
    envelope = root / "distributed.json"
    envelope.write_bytes(canonical_json({"argv": list(update.process.argv)}))
    return envelope


def collect_ranks(update: BackendUpdate, root: Path, ranks: range) -> tuple:
    after = []
    for rank in ranks:
        with (root / f"rank-{rank}.response.json").open("rb") as stream:
            data = stream.read()
        state = response_state(ServiceResult("exited", 0, data, b"", 0),
                               BlobRef.of(update.request.to_bytes()), replace(update.process, distributed=None))
        verify_state(update.output, state, update.contract.components)
        after.append(state)
    return tuple(after)


def launch_main() -> int:
    update = BackendUpdate.read()
    launch = update.process.distributed
    if launch is None or launch.cluster is not None:
        raise ValueError("local launcher requires an explicit single-node topology")
    root = update.output.directory.parent
    ranks = range(launch.ranks)
    envelope = prepare_ranks(update, root, ranks)
    result = subprocess.run((
        launch.python, "-m", "torch.distributed.run", "--standalone", "--nnodes=1",
        f"--nproc-per-node={launch.ranks}", "--max-restarts=0", "--no-python",
        sys.executable, "-I", "-B", str(Path(__file__).resolve()), "rank", str(envelope),
    ), stdout=sys.stderr, check=False)
    if result.returncode != 0:
        return result.returncode if result.returncode > 0 else 128 - result.returncode
    update.finish(pack_rank_states(collect_ranks(update, root, ranks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(rank_main(Path(sys.argv[2])) if sys.argv[1:] and sys.argv[1] == "rank" else launch_main())


def node_states(data, request, index, count):
    """Shared online/cold-receipt decoder; never accept missing/extra ranks."""
    from behave.train.checkpoints import TrainingState

    row = fields(parse_json(data), {"schema", "request", "node", "states"})
    if (row["schema"] != "behave.trainer-node-response/1" or row["request"] != request.as_dict()
            or type(row["node"]) is not int or row["node"] != index
            or type(row["states"]) is not list or len(row["states"]) != count or data != canonical_json(row)):
        raise ValueError("node response differs from its dispatched rank range")
    return tuple(TrainingState.from_bytes(canonical_json(state)) for state in row["states"])


def _request(process, workspace, index):
    cluster = process.distributed.cluster
    node = cluster.nodes[index]
    return JobRequest(
        (process.distributed.python, "-I", "-B", "-m", "behave.train.cluster_node",
         str(workspace / "invocation.json"), str(index)), str(workspace), process.wall_seconds,
        cpus=node.cpus, allocation=node.allocation, host=node.host, gpus=node.gpus,
        cleanup_seconds=process.cleanup_seconds, output_bytes=None,
        network="host",
    )


def _stop_clients(clients, cleanup_seconds):
    # These handles own only local srun transports, not the remote workload.
    # They never authorize a checkpoint or stand in for node cleanup receipts.
    for child in clients:
        if child.poll() is None:
            child.kill()
    deadline = None if cleanup_seconds is None else time.monotonic() + cleanup_seconds
    for child in clients:
        child.wait(timeout=None if deadline is None else max(0, deadline - time.monotonic()))


def run_cluster(process, workspace, invocation, inputs, output, objects, *, cancelled=None):
    cluster = process.distributed.cluster
    began = time.monotonic()
    envelope = canonical_json({"invocation": invocation.as_dict(), "input": str(inputs.directory),
                               "output": str(output.directory)})
    if not install_exclusive(workspace, "invocation.json", envelope):
        raise ValueError("cross-node invocation has already been prepared")
    jobs = []
    for index in range(len(cluster.nodes)):
        control = JobControl(workspace / f"node-{index}")
        key = control.create(_request(process, workspace, index))
        jobs.append((control, key))
    # Persist every target/key before starting the first remote worker. A lost
    # coordinator can be inspected/cancelled without redispatching any node.
    install_exclusive(workspace, "nodes.json", canonical_json({
        "schema": "behave.trainer-nodes/1", "invocation": invocation.as_dict(),
        "nodes": [{"directory": str(control.run.directory), "key": key} for control, key in jobs],
    }))
    clients = []
    stopped = False
    observations = []
    try:
        for index, (control, key) in enumerate(jobs):
            request = _request(process, workspace, index)
            command = (cluster.srun, *request.slurm_prefix(overlap=cluster.overlap)[1:],
                       process.distributed.python, "-I", "-B", "-m", "behave.agent.job_worker",
                       "run", str(control.run.directory), key)
            clients.append(subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True,
                                            env={"PATH": os.defpath, "LANG": "C.UTF-8"}))
        deadline = None if process.wall_seconds is None else began + process.wall_seconds
        while True:
            observations = [control.inspect(key) for control, key in jobs]
            done = all(row.result is not None for row in observations)
            failed = any(row.result is not None and (row.result.status != "exited" or row.result.returncode != 0)
                         for row in observations)
            # A clean transport can exit before a shared journal read observes
            # settlement. Wait for that receipt within the existing deadline;
            # zero exit is neither success evidence nor a reason to cancel.
            # Once present, the worker receipt also outranks transport failure.
            transport_failed = any(child.poll() not in (None, 0) and row.result is None
                                   for child, row in zip(clients, observations, strict=True))
            expired = deadline is not None and time.monotonic() >= deadline
            if not stopped and (failed or transport_failed or expired
                                or cancelled is not None and cancelled()):
                stopped = True
                for control, key in jobs:
                    control.cancel(key)
                deadline = None if process.cleanup_seconds is None else (
                    time.monotonic() + process.cleanup_seconds + defaults.JOB_SLURM_EXIT_SECONDS)
            if done or stopped and deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(defaults.ISOLATION_COMPLETION_POLL_SECONDS)
        # A terminal worker journal is stronger than the transport's exit: srun
        # may still be draining. Stop/reap those clients only after observation.
    finally:
        try:
            if not observations or not all(row.cleanup == CleanupState.COMPLETE for row in observations):
                for control, key in jobs:
                    control.cancel(key)
        finally:
            _stop_clients(clients, process.cleanup_seconds)
    observations = [control.inspect(key) for control, key in jobs]
    rows = []
    for (control, key), observed in zip(jobs, observations, strict=True):
        result = observed.result
        rows.append({"key": key, "worker": observed.worker, "result": None if result is None else {
            "status": result.status, "execution_status": result.execution_status, "returncode": result.returncode,
            "stdout": objects.put(result.stdout).as_dict(), "stderr": objects.put(result.stderr).as_dict(),
            "elapsed_seconds": result.elapsed_seconds, "cleanup_elapsed_seconds": result.cleanup_elapsed_seconds,
        }})
    evidence = objects.put(canonical_json({"schema": "behave.trainer-cluster-receipt/2",
                                          "invocation": invocation.as_dict(), "nodes": rows}))
    aggregate, _ = verify_cluster_receipt(objects, evidence, invocation)
    if stopped and aggregate.status == "exited" and aggregate.returncode == 0:
        aggregate = ServiceResult("cancelled", None, b"", b"", 0.)
    return ServiceResult(aggregate.status, aggregate.returncode, aggregate.stdout, aggregate.stderr,
                         time.monotonic() - began, execution_status=aggregate.execution_status), evidence


def verify_cluster_receipt(reader, ref, invocation):
    """Authenticate all node results and reconstruct the exact rank product.

The caller includes the returned closure in the ordinary sealed trainer ledger.
This verifier performs no scheduler access and is also used on cold restore.
"""
    data = read_record(reader, ref)
    row = fields(parse_json(data), {"schema", "invocation", "nodes"})
    call = parse_json(read_record(reader, invocation))
    process = TrainerProcess.from_dict(call["process"])
    cluster = process.distributed.cluster if process.distributed is not None else None
    legacy = row["schema"] == "behave.trainer-cluster-receipt/1"
    if (cluster is None or row["schema"] not in {"behave.trainer-cluster-receipt/1", "behave.trainer-cluster-receipt/2"}
            or row["invocation"] != invocation.as_dict() or data != canonical_json(row)
            or type(row["nodes"]) is not list or len(row["nodes"]) != len(cluster.nodes)):
        raise ValueError("cluster receipt differs from its dispatched placement")
    refs, states, results = [ref], [], []
    request = BlobRef.of(canonical_json(call["request"]))
    for index, value in enumerate(row["nodes"]):
        item = fields(value, {"key", "worker", "result"})
        BlobRef(item["key"], 0)
        expected = _request(process, Path(cluster.shared_directory) / request.sha256, index)
        if item["worker"] is not None:
            validate_worker(item["worker"], expected)
        if item["result"] is None:
            results.append(ServiceResult("cleanup-incomplete", None, b"", b"", 0.))
            continue
        if item["worker"] is None:
            raise ValueError("node result needs its worker identity")
        record = fields(item["result"], {"status", "returncode", "stdout", "stderr", "elapsed_seconds", "cleanup_elapsed_seconds"}
                        | (set() if legacy else {"execution_status"}))
        if (record["status"] not in {"exited", "timeout", "cancelled", "output-limit", "launch-failed",
                                     "cleanup-incomplete", "supervisor-error"}
                or record["returncode"] is not None and (type(record["returncode"]) is not int
                                                         or not -255 <= record["returncode"] <= 255)
                or record["status"] == "exited" and record["returncode"] is None
                or any(type(record[key]) not in (int, float) or not math.isfinite(record[key]) or record[key] < 0
                       for key in ("elapsed_seconds", "cleanup_elapsed_seconds"))):
            raise ValueError("invalid node execution outcome")
        blobs = tuple(BlobRef.from_dict(record[key]) for key in ("stdout", "stderr"))
        result = ServiceResult(record["status"], record["returncode"],
                               *(read_record(reader, blob) for blob in blobs),
                               record["elapsed_seconds"], record["cleanup_elapsed_seconds"],
                               execution_status=None if legacy else record["execution_status"])
        refs.extend(blobs)
        results.append(result)
        if result.status == "exited" and result.returncode == 0:
            try:
                states.extend(node_states(result.stdout, request, index, process.distributed.ranks // len(cluster.nodes)))
            except ValueError:
                results[-1] = ServiceResult("exited", 1, b"", b"", 0.)
    if any(result.cleanup_state != CleanupState.COMPLETE for result in results):
        aggregate = ServiceResult("cleanup-incomplete", None, b"", b"", 0.)
    elif any(result.status != "exited" or result.returncode != 0 for result in results):
        aggregate = ServiceResult("exited", 1, b"", b"", 0.)
    else:
        try:
            stdout = response_bytes(request, pack_rank_states(tuple(states)))
            aggregate = ServiceResult("exited", 0, stdout, b"", 0.)
        except ValueError:
            aggregate = ServiceResult("exited", 1, b"", b"", 0.)
    return aggregate, tuple(refs)
