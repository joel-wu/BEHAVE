"""Freeze selected native tasks for Eval/Train using the public Sim contract API.

No generation, reference evaluation, search, or semantic repairs occur here.
Preparing current task files copies inputs to scratch before elaboration; failed
inputs remain in the inventory and prevent a partial release. --from-release
instead retains saved task bytes and changes only the pinned simulator versions
in a new release, without elaboration or reinterpreting historical scores.
Known native inputs and source/license attachments are retained byte-for-byte.
This freezes experiment inputs; it does not replace reviewed publication or
certify source correctness, licensing completeness or redistribution approval.
"""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from behave.core.artifacts import BlobRef, FileRef, SourceArtifact
from behave.core.file_input import read_regular_file
from behave.core.paths import scratch_root
from behave.core.records import canonical_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.published import PublishedReleaseReader, ReleaseManifest, TaskManifest, TaskRevision
from behave.data.published.files import task_files
from behave.data.spec_format import behavior_entry, without_behavior_interface
from behave.runtime.records import publish_json, write_json
from behave.sim import api
from behave.task_execution.judge_policy import JudgePolicy

ELABORATION_FILES = ("spec.md", "behavior.py", "task.toml", "interface.json", "fp_lib.py")
NATIVE_INPUT_FILES = (
    *ELABORATION_FILES,
    "rtl.sv", "behavior2.py", "source.json", "provenance.json",
    "LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "NOTICE", "NOTICE.txt", "NOTICE.md",
)


def _selection(lists):
    sources = tuple((path, read_regular_file(path)) for path in lists)
    selected = [task for _, raw in sources for task in raw.decode("utf-8").split()]
    if not selected:
        raise ValueError("select at least one task")
    if len(set(selected)) != len(selected):
        raise ValueError("task lists overlap")
    return sources, tuple(selected)


def selected_task_ids(lists: list[Path]) -> tuple[str, ...]:
    return _selection(lists)[1]


def selected_tasks(repository: Path, lists: list[Path]) -> tuple[Path, ...]:
    return _resolve_tasks(repository, selected_task_ids(lists))


def _resolve_tasks(repository, selected):
    paths: dict[str, list[Path]] = {}
    for path in (repository / "data/problems").glob("*/*/spec.md"):
        paths.setdefault(path.parent.name, []).append(path.parent)
    resolved = []
    for task in selected:
        matches = paths[task]
        if len(matches) != 1:
            raise ValueError(f"ambiguous task ID {task!r}: {', '.join(map(str, sorted(matches)))}")
        resolved.append(matches[0])
    return tuple(resolved)


def refresh_simulator(source: Path, output: Path, lists: list[Path], *, workers: int = 16) -> dict:
    """Explicitly rebind saved native task inputs to the installed Sim version.

    Preserve task bytes, baseline settings and old results. This metadata-only
    operation neither reads the current problem directories nor re-elaborates
    an interface. It is not evidence of functional validity under the new Sim.
    """
    reader = DirectoryStore(source / "objects")
    original_ref = BlobRef.from_dict(json.loads((source / "release.json").read_text()))
    original = ReleaseManifest.from_bytes(reader.read(original_ref))
    if "corpus" in original.metadata:
        raise ValueError("corpus-backed releases require their corpus membership publication path")
    by_id = {task.task_id: task.manifest for task in original.tasks}
    selected = tuple((task, by_id[task]) for task in selected_task_ids(lists))
    output.mkdir(parents=True, exist_ok=False)
    objects = DirectoryStore(output / "objects")
    retained = {original_ref, original.policy}
    policies, revisions, inventory = {}, [], []
    for task_id, revision in selected:
        task = TaskManifest.from_bytes(reader.read(revision))
        if task.judge_policy not in policies:
            prior = JudgePolicy.from_bytes(reader.read(task.judge_policy))
            current = replace(prior, command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION)
            policies[task.judge_policy] = objects.put(current.to_bytes())
        updated = replace(task, judge_policy=policies[task.judge_policy])
        new_revision = objects.put(updated.to_bytes())
        revisions.append(TaskRevision(task_id, new_revision))
        files = task_files(task)
        retained.update((revision, task.judge_policy, task.run_policy, *(file.content for file in files)))
        inventory.append({"task_id": task_id, "source_revision": revision.as_dict(),
                          "revision": new_revision.as_dict(), "task_content_changed": False})
    objects.publish_owned(reader, tuple(retained), workers=workers)
    record = {"source_release": original_ref.as_dict(), "task_content_changed": False,
              "old_scores_relabelled": False, "functional_validation_performed": False,
              "judge_versions": {"command": api.COMMAND_VERSION, "authority": api.RESULT_VERSION},
              "policies": [{"source": prior.as_dict(), "updated": current.as_dict()}
                           for prior, current in policies.items()]}
    release = ReleaseManifest(output.name, tuple(revisions), objects.put(canonical_json(record)),
        {**thaw_json(original.metadata), "simulator_version_update": record})
    reference = objects.put(release.to_bytes())
    write_json(output / "inventory.json", inventory)
    write_json(output / "simulator_update.json", record)
    publish_json(output / "release.json", reference.as_dict())
    return {"task_objects": str(output / "objects"), "release": reference.as_dict(),
            "tasks": len(revisions), **record}


def prepare(repository: Path, output: Path, lists: list[Path], *, workers: int = 16,
            scratch: Path | None = None) -> dict:
    if scratch is not None:
        staging, destination = Path(scratch).resolve(), output.resolve()
        if staging.is_relative_to(destination) or destination.is_relative_to(staging):
            raise ValueError("explicit scratch and publication directories must not overlap")
    selection, task_ids = _selection(lists)
    if len({path.name for path, _ in selection}) != len(selection):
        raise ValueError("task list names must be distinct to retain unambiguous provenance")
    tasks = _resolve_tasks(repository, task_ids)
    output.mkdir(parents=True, exist_ok=False)
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0, analysis=None)
    # An explicit caller-owned scratch directory is retained for inspection.
    workspace = (TemporaryDirectory(prefix="behave-release-", dir=scratch_root())
                 if scratch is None else nullcontext(scratch))
    with workspace as directory:
        local = Path(directory)
        objects = DirectoryStore(local / "objects")
        run_policy = objects.put(b"null")  # Sim owns scheduling and completion.
        judge_policy = objects.put(policy.to_bytes())
        split_lists = {path.name: objects.put(raw) for path, raw in selection}
        captured = []
        for task in tasks:
            target = local / "tasks" / task.parent.name / task.name
            target.mkdir(parents=True)
            inputs = []
            for name in NATIVE_INPUT_FILES:
                try:
                    raw = read_regular_file(task / name)
                except FileNotFoundError:
                    if (task / name).is_symlink():
                        raise  # A broken declared input is not an absent optional file.
                    continue
                (target / name).write_bytes(raw)
                inputs.append(FileRef(name, objects.put(raw)))
            captured.append((target, tuple(inputs)))

        def package(capture):
            task, inputs = capture
            row = {"task_id": task.name, "category": task.parent.name,
                   "input_files": {f.path: f.content.as_dict() for f in inputs}}
            try:
                spec = (task / "spec.md").read_text()
                entry = behavior_entry(spec)
                if without_behavior_interface(spec) == spec:
                    raise ValueError("Behavior interface was not masked")
                # Preserve the existing elaboration input view. Merely archiving
                # auxiliary solutions must not make Sim use them as evidence.
                elaboration = local / "elaboration" / task.parent.name / task.name
                elaboration.mkdir(parents=True)
                for name in ELABORATION_FILES:
                    if (task / name).is_file():
                        shutil.copyfile(task / name, elaboration / name)
                contract = api.contract_of_task(elaboration)

                def file(name, data):
                    return FileRef(name, objects.put(data))

                source = SourceArtifact(tuple(f for f in inputs if f.path in ("behavior.py", "fp_lib.py")),
                                        "behavior.py", entry)
                manifest = TaskManifest(task.name, file("spec.md", spec.encode()),
                    file("contract.json", canonical_json(contract.as_dict())), source, run_policy, judge_policy,
                    metadata={"category": task.parent.name, "source": "current native task files",
                              "archive_files": [f.as_dict() for f in inputs]})
                revision = TaskRevision(task.name, objects.put(manifest.to_bytes()))
                row.update(status="prepared", revision=revision.manifest.as_dict(), reference_entry=entry)
                return revision, row
            except (api.Refused, ValueError, OSError) as error:
                row.update(status="input_refused", error=f"{type(error).__name__}: {error}")
                if isinstance(error, api.Refused):
                    row["refusal"] = error.refusal.as_dict()
                return None, row

        with ThreadPoolExecutor(max_workers=workers) as pool:
            packaged = []
            for revision, row in pool.map(package, captured):
                packaged.append((revision, row))
                print(json.dumps({key: row[key] for key in ("task_id", "status")}), flush=True)
        inventory = [row for _, row in packaged]
        write_json(output / "inventory.json", inventory)
        write_json(output / "judge_policy.json", json.loads(policy.to_bytes()))
        if any(revision is None for revision, _ in packaged):
            # Keep the exact rejected inputs for diagnosis, without authorizing
            # a release that silently omits harder-to-prepare tasks.
            shutil.copytree(local / "tasks", output / "inputs")
            raise ValueError("input preparation failed; see inventory.json; no release published")
        release = ReleaseManifest(output.name, tuple(revision for revision, _ in packaged),
            objects.put(canonical_json({"purpose": "operator-selected task inputs",
                "semantic_readmission": False, "split_lists": {
                    name: ref.as_dict() for name, ref in split_lists.items()}})))
        reference = objects.put(release.to_bytes())
        published = PublishedReleaseReader(objects, reference)
        retained = {reference, release.policy, *split_lists.values()}
        for revision, _ in packaged:
            task = published.load(revision.task_id).manifest
            retained.update((revision.manifest, task.run_policy, task.judge_policy,
                             *(file.content for file in task_files(task))))
        # Publish only the verified declared closure, not a caller's whole CAS.
        # The immutable head is the final commit, after every object is retained.
        DirectoryStore(output / "objects").publish_owned(
            objects, tuple(sorted(retained, key=lambda ref: ref.sha256)), workers=workers)
        publish_json(output / "release.json", reference.as_dict())
        return {"task_objects": str(output / "objects"), "release": reference.as_dict(),
                "tasks": len(tasks), "search_seconds": 0, "analysis": None}
