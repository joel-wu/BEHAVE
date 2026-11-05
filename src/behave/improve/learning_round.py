"""Task and Raw continuation from one committed material/task collection.

The caller supplies the curriculum and runtime, including zero-update choices.
Each Task pass names an approved manifest (new tasks or explicit seed/replay).
Native learning and its recovery remain in the shared training stage.
"""

from dataclasses import dataclass
from pathlib import Path

from behave.agent.journal import load_chain
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.published import PublishedReleaseReader
from behave.train.plan import TrainingManifest, validate_membership
from behave.train.slime_data import prepare_raw_plan, prepare_task_plan
from behave.train.slime_stage import load_learning_report, run_learning
from behave.train.trainer import NativeAnchor
from behave.train.training_data import RawCorpus, RawEncoding, tokenize_materials

from .input_stores import ReadSet
from .materials import MaterialRequest, load_materials
from .round_data import committed_round_data, load_round_data


@dataclass(frozen=True, slots=True)
class LearningRuntime:
    configuration: dict
    cpus: int
    environment: dict[str, str]
    seconds: float | None
    cleanup_seconds: int | None

    def record(self):
        return {"configuration": self.configuration, "cpus": self.cpus,
                "environment": BlobRef.of(canonical_json(self.environment)).as_dict(),
                "seconds": self.seconds, "cleanup_seconds": self.cleanup_seconds}


@dataclass(frozen=True, slots=True)
class LearningParent:
    checkpoint: BlobRef | None
    directory: Path

    def record(self):
        return {"checkpoint": None if self.checkpoint is None else self.checkpoint.as_dict(),
                "directory": str(self.directory.resolve())}

    @classmethod
    def from_record(cls, value):
        return cls(None if value["checkpoint"] is None else BlobRef.from_dict(value["checkpoint"]),
                   Path(value["directory"]))


def _next_update(reader, parent, route):
    if parent.checkpoint is None:
        return 0
    anchor = NativeAnchor.load(reader, parent.checkpoint)
    if anchor.route != route:
        raise RecordError("learning-parent-route", "Task and Raw checkpoints cannot be interchanged")
    return anchor.step + 1


def load_learning_round(reader, head):
    """Verify both lineages using the original supervised native receipts."""
    events = load_chain(reader, head)
    if not events or events[0].kind != "paired_learning_requested" or events[-1].kind != "paired_learning_settled":
        raise RecordError("paired-learning-incomplete", "both learning routes must settle")
    request = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[0].payload)["request"])))
    data = load_round_data(reader, BlobRef.from_dict(request["data"]))
    parents = dict(request["parents"])
    expected = [("task", item["updates"]) for item in request["task_passes"]]
    expected.append(("raw", request["raw_updates"]))
    receipts, prepared = [], None
    for event in events[1:-1]:
        row = thaw_json(event.payload)
        if event.kind == "learning_pass_prepared":
            if prepared is not None or len(receipts) >= len(expected):
                raise RecordError("paired-learning-order", "unexpected prepared pass")
            route, count = expected[len(receipts)]
            configuration, binding_ref, _ = event.artifacts[:3]
            binding = parse_json(read_verified(reader, binding_ref))
            if (not count or row["route"] != route or row["binding"] != binding_ref.as_dict()
                    or parse_json(read_verified(reader, configuration)) != request["runtime"][route]["configuration"]
                    or binding.get("parent_checkpoint") != parents[route]["checkpoint"]):
                raise RecordError("paired-learning-inputs", "prepared pass differs from its runtime/parent")
            if route == "task":
                if binding["manifest"] != request["task_passes"][len(receipts)]["manifest"]:
                    raise RecordError("paired-learning-inputs", "prepared pass selected another task pool")
            else:
                corpus = RawCorpus.from_bytes(read_verified(reader, BlobRef.from_dict(binding["corpus"])))
                if corpus.materials != data.materials or corpus.encoding.as_dict() != request["encoding"]:
                    raise RecordError("paired-learning-inputs", "Raw pass uses different material/encoding")
            prepared = binding_ref
            continue
        if event.kind != "learning_pass_settled" or len(receipts) >= len(expected):
            raise RecordError("paired-learning-order", "unexpected learning pass")
        route, count = expected[len(receipts)]
        if row["route"] != route or row["parent"] != parents[route]:
            raise RecordError("paired-learning-parent", "learning pass changed its route or parent")
        if count:
            report = load_learning_report(reader, BlobRef.from_dict(row["head"]))
            start = _next_update(reader, LearningParent.from_record(parents[route]), route)
            child_events = load_chain(reader, BlobRef.from_dict(row["head"]))
            child_request = thaw_json(child_events[0].payload)
            binding = parse_json(read_verified(reader, BlobRef.from_dict(child_request["inputs"][1])))
            if (report["status"] != "completed" or report["route"] != route
                    or report["first_update"] != start or report["stop_update"] != start + count
                    or binding.get("parent_checkpoint") != parents[route]["checkpoint"]
                    or prepared is None or child_request["inputs"][1] != prepared.as_dict()):
                raise RecordError("paired-learning-evidence", "learning pass differs from its requested continuation")
            parents[route] = {"checkpoint": report["checkpoint"], "directory": report["checkpoint_directory"]}
        elif row["head"] is not None:
            raise RecordError("paired-learning-evidence", "a zero-update pass must not invent a checkpoint")
        if row["result"] != parents[route]:
            raise RecordError("paired-learning-evidence", "next parent differs from the saved native checkpoint")
        receipts.append(row)
        prepared = None
    if len(receipts) != len(expected):
        raise RecordError("paired-learning-incomplete", "a route is missing its requested passes")
    result = {"status": "completed", "data": request["data"], "parents": parents, "passes": receipts}
    if thaw_json(events[-1].payload) != result:
        raise RecordError("paired-learning-evidence", "round result differs from its completed passes")
    return result


def learn_round(*, data: BlobRef, base_checkpoint: BlobRef,
                task_passes: tuple[tuple[BlobRef, int], ...], raw_updates: int, encoding: RawEncoding,
                parents: dict[str, LearningParent], task_runtime: LearningRuntime, raw_runtime: LearningRuntime,
                reader, run: JournalDirectory, cancelled=None):
    """Run/recover explicit Task passes, then Raw, without resetting either route.

    The two routes may use different runtime budgets. Zero means an explicitly
    requested carry-forward, not a successful optimizer update. An interrupted
    native child leaves this round unfinished and is never silently retried.
    """
    if raw_updates < 0 or any(count < 0 for _, count in task_passes):
        raise ValueError("learning update counts cannot be negative")
    request = {"data": data.as_dict(), "base_checkpoint": base_checkpoint.as_dict(),
        "task_passes": [{"manifest": ref.as_dict(), "updates": count} for ref, count in task_passes],
        "raw_updates": raw_updates, "encoding": encoding.as_dict(),
        "parents": {route: parents[route].record() for route in ("task", "raw")},
        "runtime": {"task": task_runtime.record(), "raw": raw_runtime.record()}}
    with run.owner():
        tape = Tape(run.journal("paired_learning"))
        objects = run.objects
        request_ref = objects.put(canonical_json(request))
        inputs = ()
        if not tape.events:
            collection, data_closure = committed_round_data(reader, data)
            tracked = ReadSet(reader)
            material = load_materials(tracked, collection.materials)
            context = MaterialRequest.from_bytes(read_verified(tracked, material.request))
            if context.checkpoint != (parents["task"].checkpoint or base_checkpoint):
                raise RecordError("paired-learning-acquisition", "material was acquired by another Task checkpoint")
            read_verified(tracked, base_checkpoint)
            read_verified(tracked, encoding.tokenizer)
            for route, parent in parents.items():
                _next_update(tracked, parent, route)
            for manifest, _ in task_passes:
                approved = TrainingManifest.from_bytes(read_verified(tracked, manifest))
                published = PublishedReleaseReader(tracked, approved.release)
                for task, _ in approved.tasks:
                    published.load(task)
                if approved.corpus is not None or "corpus" in published.manifest.metadata:
                    validate_membership(tracked, approved, ())
            if isinstance(reader, DirectoryStore):
                objects.publish_owned(reader, data_closure)
            else:
                objects.publish(reader, data_closure)
            tracked.publish(objects)
            inputs = tuple(dict.fromkeys((*data_closure, *tracked.references)))
        tape.record("paired_learning_requested", {"request": request_ref.as_dict()}, (request_ref, *inputs))
        if tape.events[-1].kind == "paired_learning_settled":
            return thaw_json(tape.events[-1].payload)
        collection, _ = committed_round_data(objects, data)
        current = dict(parents)
        completed = []
        passes = [("task", ref, count, task_runtime) for ref, count in task_passes]
        passes.append(("raw", None, raw_updates, raw_runtime))
        for index, (route, manifest_ref, count, runtime) in enumerate(passes):
            parent = current[route]
            plan_dir = run.directory / "plans" / f"pass_{index}"
            if tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "learning_pass_settled":
                row = thaw_json(tape.take("learning_pass_settled").payload)
            elif not count:
                row = {"route": route, "parent": parent.record(), "head": None, "result": parent.record()}
                tape.record("learning_pass_settled", row)
            else:
                if tape.cursor < len(tape.events):
                    prepared = tape.take("learning_pass_prepared")
                else:
                    if route == "task":
                        manifest = TrainingManifest.from_bytes(read_verified(objects, manifest_ref))
                        binding = prepare_task_plan(plan_dir, runtime.configuration, objects, manifest,
                            parent_reader=objects, parent_checkpoint=parent.checkpoint)
                    else:
                        corpus = tokenize_materials(objects, collection.materials, encoding)
                        corpus_ref = objects.put(corpus.to_bytes())
                        binding = prepare_raw_plan(plan_dir, runtime.configuration, objects.directory, corpus_ref,
                            parent_reader=objects, parent_checkpoint=parent.checkpoint)
                    plan_files = tuple(objects.put((plan_dir / name).read_bytes()) for name in
                                       ("configuration.json", "binding.json", "training_selection.json"))
                    prepared = tape.record("learning_pass_prepared", {"route": route, "plan": str(plan_dir),
                        "binding": BlobRef.of(canonical_json(binding)).as_dict()},
                        (*plan_files, *((corpus_ref,) if route == "raw" else ())))
                if tape.cursor < len(tape.events):
                    row = thaw_json(tape.take("learning_pass_settled").payload)
                    current[route] = LearningParent.from_record(row["result"])
                    completed.append(row)
                    continue
                for name, reference in zip(("configuration.json", "binding.json", "training_selection.json"),
                                           prepared.artifacts[:3], strict=True):
                    if BlobRef.of((plan_dir / name).read_bytes()) != reference:
                        raise RecordError("paired-learning-inputs", "prepared plan files changed")
                child = JournalDirectory(run.directory / "learning" / f"pass_{index}")
                report = run_learning(plan=plan_dir, run=child,
                    updates=_next_update(objects, parent, route) + count, load=parent.directory,
                    save=run.directory / "checkpoints" / f"pass_{index}", cpus=runtime.cpus,
                    environment=runtime.environment, seconds=runtime.seconds, cleanup_seconds=runtime.cleanup_seconds,
                    cancelled=cancelled)
                if report["status"] != "completed":
                    return {"status": "unfinished", "route": route, "pass": index, "learning": report}
                head, closure = child.seal("learning")
                objects.publish_owned(child.objects, closure)
                next_parent = LearningParent(BlobRef.from_dict(report["checkpoint"]), Path(report["checkpoint_directory"]))
                row = {"route": route, "parent": parent.record(), "head": head.as_dict(), "result": next_parent.record()}
                tape.record("learning_pass_settled", row, closure)
            current[route] = LearningParent.from_record(row["result"])
            completed.append(row)
        result = {"status": "completed", "data": data.as_dict(),
            "parents": {route: current[route].record() for route in ("task", "raw")}, "passes": completed}
        tape.record("paired_learning_settled", result)
        tape.finish()
        run.seal("paired_learning")
        return result
