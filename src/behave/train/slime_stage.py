"""One owned learning stage, shared by Task RL and Raw source learning.

The native driver owns optimization. A successful process is insufficient: the
saved native history and checkpoint metadata must match the requested update range.
Cold replay never starts another process, including after an uncertain dispatch.
"""

import json
import math
import os
import time
from pathlib import Path
from types import SimpleNamespace

from behave.agent.journal import load_chain
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.process_limits import process_limit
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json, version
from behave.core.storage import DirectoryStore
from behave.isolation.service import run_service
from behave.isolation.startup import startup_environment

from .slime_checkpoint import iteration
from .slime_launch import logged_arguments, native_arguments, tokenize_environment
from .trainer import NativeAnchor, NativeUpdates, active_events


def _native(binding, configuration, *, load, save, updates, parent_reader=None):
    raw = binding["schema"] == "behave.slime-raw-binding/1"
    parent = binding.get("parent_checkpoint")
    anchor = None if parent is None else NativeAnchor.load(
        DirectoryStore(binding["objects"]) if parent_reader is None else parent_reader, BlobRef.from_dict(parent))
    return NativeUpdates(args=SimpleNamespace(load=str(load), save=str(save), num_rollout=updates,
        save_interval=configuration["checkpoint_interval"]),
        run=JournalDirectory(Path(binding["run_directory"]) / "native"),
        identity=BlobRef.of(canonical_json(binding)), ready_kind="raw_batch_ready" if raw else "round_scored",
        evidence_key="batch" if raw else "score", critic=not raw and binding["recipe"]["algorithm"] == "ppo", anchor=anchor,
        retention=binding.get("checkpoint_retention"))


def _completed_checkpoint(native, run, save, updates):
    head, closure = native.run.seal("rounds")
    if head is None:
        raise RecordError("learning-checkpoint-missing", "native process produced no saved learning history")
    run.objects.publish(native.run.objects, closure)
    parent = NativeAnchor.load(run.objects, head)
    last = native.journal.load()[-1]
    if parent.step != updates - 1 or last.payload["binding"] != native.identity.as_dict():
        raise RecordError("learning-checkpoint-mismatch", "saved checkpoint differs from the requested learning stage")
    batches = [event.payload["round"] for event in active_events(native.journal.load()) if event.kind == native.ready_kind]
    if batches != list(range(native.first_step, updates)):
        raise RecordError("learning-update-gap", "native learning history skips or repeats a requested update")
    native._check_files(save, parent.step, [file.as_dict() for file in parent.files])
    return head, closure


def load_learning_report(reader, head):
    """Read a settled phase from its sealed input/process/checkpoint evidence."""
    events = load_chain(reader, head)
    if tuple(event.kind for event in events) != (
            "learning_requested", "learning_dispatched", "learning_process_settled", "learning_settled"):
        raise RecordError("learning-incomplete", "a complete supervised learning stage is required")
    request, dispatched, process, terminal = (thaw_json(event.payload) for event in events)
    report = fields(parse_json(read_verified(reader, BlobRef.from_dict(terminal["report"]))), {
        "schema", "status", "route", "first_update", "stop_update", "checkpoint", "checkpoint_directory", "process"})
    version(report["schema"], "behave.learning-result/1")
    binding_ref = BlobRef.from_dict(request["inputs"][1])
    binding = parse_json(read_verified(reader, binding_ref))
    route = "raw" if binding["schema"] == "behave.slime-raw-binding/1" else "task"
    success = process["status"] == "exited" and process["returncode"] == 0 and process["cleanup"] == "complete"
    if (report["process"] != process or report["route"] != route or
            report["status"] != ("completed" if success else "interrupted") or
            report["first_update"] != dispatched["first_update"] or report["stop_update"] != request["updates"] or
            report["checkpoint_directory"] != request["save"]):
        raise RecordError("learning-evidence-mismatch", "learning report differs from its supervised inputs/outcome")
    if success:
        checkpoint = BlobRef.from_dict(report["checkpoint"])
        parent = NativeAnchor.load(reader, checkpoint)
        native = load_chain(reader, checkpoint)
        expected_start = 0
        if binding.get("parent_checkpoint") is not None:
            previous = NativeAnchor.load(reader, BlobRef.from_dict(binding["parent_checkpoint"]))
            if previous.route != route:
                raise RecordError("learning-evidence-mismatch", "learning parent belongs to the other route")
            expected_start = previous.step + 1
        batches = [event.payload["round"] for event in active_events(native)
                   if event.kind == ("raw_batch_ready" if route == "raw" else "round_scored")]
        if (parent.step != request["updates"] - 1 or parent.route != route or
                native[-1].payload["binding"] != binding_ref.as_dict() or
                batches != list(range(expected_start, request["updates"]))):
            raise RecordError("learning-evidence-mismatch", "learning report points to a different checkpoint/update range")
    elif report["checkpoint"] is not None:
        raise RecordError("learning-evidence-mismatch", "interrupted process cannot authorize a learning checkpoint")
    return report


def run_learning(*, plan: Path, run: JournalDirectory, updates: int, load: Path, save: Path,
                 cpus: int, environment: dict[str, str], seconds: float | None = None,
                 cleanup_seconds: int | None = None, cancelled=None):
    """Run an already prepared route within the caller's selected allocation.

    ``updates`` is the absolute stopping index plus one, not a reset of the
    native cursor. This function does not allocate nodes, retry unknown jobs,
    reinterpret unresolved scores, or decide which tasks/materials to learn.
    """
    if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0):
        raise ValueError("an explicit stage time budget must be positive")
    plan, load, save = plan.resolve(), load.resolve(), save.resolve()
    from .slime_rollout import prepare_recorded_batches

    configuration = json.loads((plan / "configuration.json").read_text())
    startup = startup_environment(configuration.get("isolation_startup_slots"))
    os.environ.update(startup)
    environment = {**environment, **startup}
    prepare_recorded_batches(plan)
    environment = tokenize_environment(environment, configuration.get("tokenize_workers"))
    binding = json.loads((plan / "binding.json").read_text())
    arguments = native_arguments(plan, updates=updates, load=load, cpus=cpus, checkpoint_directory=save)
    with run.owner():
        tape = Tape(run.journal("learning"))
        native = _native(binding, configuration, load=load, save=save, updates=updates,
                         parent_reader=run.objects if tape.events else None)
        inputs = tuple(run.objects.put((plan / name).read_bytes()) for name in
                       ("configuration.json", "binding.json", "training_selection.json"))
        request = {"plan": str(plan), "load": str(load), "save": str(save), "updates": updates,
            "seconds": seconds, "cleanup_seconds": cleanup_seconds, "arguments": list(arguments),
            "environment": BlobRef.of(canonical_json(environment)).as_dict(),
            "inputs": [ref.as_dict() for ref in inputs]}
        parents = ()
        if not tape.events and native.anchor is not None:
            reader = DirectoryStore(binding["objects"])
            parents = tuple(dict.fromkeys(ref for event in load_chain(reader, native.anchor.head)
                for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
            run.objects.publish(reader, parents)
        tape.record("learning_requested", request, (*inputs, *parents))
        if tape.cursor < len(tape.events):
            dispatched = tape.take("learning_dispatched")
            first = dispatched.payload["first_update"]
            # A dangling dispatch has no trustworthy cleanup witness. Never
            # infer quiescence from files or retry the native update here.
            process = tape.take("learning_process_settled")
        else:
            previous = iteration(load) if (load / "latest_checkpointed_iteration.txt").exists() else -1
            native.load(previous)
            if previous + 1 >= updates:
                raise ValueError("learning stage must request at least one new native update")
            first = previous + 1
            tape.record("learning_dispatched", {"first_update": first})
            result = run_service(logged_arguments(configuration["native_python"], "behave.train.slime_driver",
                arguments, run.directory / "stdout", run.directory / "stderr"),
                cwd=plan, environment=environment, network="none", output_bytes=None,
                deadline=None if seconds is None else time.monotonic() + seconds,
                cancelled=cancelled, cleanup_seconds=cleanup_seconds,
                process_limit=process_limit(configuration.get("process_limit")))
            logs = tuple(run.objects.put(value) for value in (result.stdout, result.stderr))
            process = tape.record("learning_process_settled", {
                "status": result.status, "returncode": result.returncode,
                "cleanup": result.cleanup_state.value, "elapsed_seconds": result.elapsed_seconds,
                "supervisor_logs": [ref.as_dict() for ref in logs]}, logs)
        process_row = thaw_json(process.payload)
        if tape.cursor < len(tape.events):
            tape.take("learning_settled")
        else:
            success = process_row["status"] == "exited" and process_row["returncode"] == 0 and process_row["cleanup"] == "complete"
            head, closure = _completed_checkpoint(native, run, save, updates) if success else (None, ())
            report = {"schema": "behave.learning-result/1", "status": "completed" if success else "interrupted",
                "route": "raw" if native.evidence_key == "batch" else "task",
                "first_update": first, "stop_update": updates,
                "checkpoint": None if head is None else head.as_dict(), "checkpoint_directory": str(save),
                "process": process_row}
            reference = run.objects.put(canonical_json(report))
            tape.record("learning_settled", {"report": reference.as_dict()}, (reference, *closure))
        tape.finish()
        head, _ = run.seal("learning")
        return load_learning_report(run.objects, head)


def main():
    """Node-local entry; the ordinary job supervisor owns submission/polling."""
    import argparse
    import signal
    from threading import Event

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "run", "load", "save", "environment"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--updates", type=int, required=True, help="absolute stopping update index plus one")
    parser.add_argument("--cpus", type=int, required=True)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--cleanup-seconds", type=int, help="optional cleanup deadline; omitted means no time cap")
    args = parser.parse_args()
    from behave.core.process_limits import configure_worker_process_limit

    configuration = json.loads((args.plan / "configuration.json").read_text())
    configure_worker_process_limit(configuration.get("process_limit"))
    stopped = Event()
    previous = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        result = run_learning(plan=args.plan, run=JournalDirectory(args.run), updates=args.updates,
            load=args.load, save=args.save, cpus=args.cpus, seconds=args.seconds, cleanup_seconds=args.cleanup_seconds,
            environment=json.loads(args.environment.read_text()), cancelled=stopped.is_set)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "completed" else 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
