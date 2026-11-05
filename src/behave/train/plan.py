"""Training membership and capability policy over shared candidate execution.

No optimizer, group advantage or loss reduction lives here. The operator supplies
an explicit training-only task manifest; it is a content-pinned allowlist, not a
claim of cryptographic dataset approval. Held-out tasks cannot enter through
directory discovery or an unlisted job.
"""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, file_set, read_verified
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, thaw_json, version
from behave.data.corpus import assigned, load_index, record
from behave.data.published import ReleaseManifest, TaskManifest
from behave.data.release_records import ReleasePolicy
from behave.task_execution.batch_plan import BatchPlan

from .training_data import TrainingRequirements


@dataclass(frozen=True, slots=True)
class TrainingManifest:
    release: BlobRef
    tasks: tuple[tuple[str, BlobRef], ...]
    corpus: BlobRef | None = None

    def __post_init__(self):
        from behave.core.records import text

        if type(self.release) is not BlobRef or type(self.tasks) is not tuple or not self.tasks:
            raise TypeError("training manifest requires a pinned release and nonempty task tuple")
        for entry in self.tasks:
            if type(entry) is not tuple or len(entry) != 2 or type(entry[1]) is not BlobRef:
                raise TypeError("training task entries require task id and revision")
            text(entry[0], "training task id")
        if len({task for task, _ in self.tasks}) != len(self.tasks):
            raise ValueError("training task identifiers must be unique")
        if self.corpus is not None and type(self.corpus) is not BlobRef:
            raise TypeError("training corpus must be a pinned index")

    def to_bytes(self):
        return canonical_json(
            {
                "schema": f"behave.training-manifest/{2 if self.corpus is not None else 1}",
                "split": "train",
                "release": self.release.as_dict(),
                "tasks": [{"task_id": task, "revision": ref.as_dict()} for task, ref in self.tasks],
                **({"corpus": self.corpus.as_dict()} if self.corpus is not None else {}),
            }
        )

    @classmethod
    def from_bytes(cls, payload):
        value = parse_json(payload)
        extended = value.get("schema") == "behave.training-manifest/2"
        row = fields(value, {"schema", "split", "release", "tasks"} | ({"corpus"} if extended else set()))
        version(row["schema"], f"behave.training-manifest/{2 if extended else 1}")
        if row["split"] != "train":
            raise RecordError("training-split-forbidden", "only the approved training split may generate rollouts")
        tasks = [fields(item, {"task_id", "revision"}) for item in array(row["tasks"])]
        result = cls(
            BlobRef.from_dict(row["release"]),
            tuple((item["task_id"], BlobRef.from_dict(item["revision"])) for item in tasks),
            BlobRef.from_dict(row["corpus"]) if extended else None,
        )
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "training manifest must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class TrainingPlan:
    execution: BatchPlan
    manifest: BlobRef
    requirements: TrainingRequirements
    group_ids: tuple[str | None, ...]

    def __post_init__(self):
        if type(self.execution) is not BatchPlan or type(self.manifest) is not BlobRef:
            raise TypeError("training plan requires shared execution and an approved manifest")
        if type(self.requirements) is not TrainingRequirements:
            raise TypeError("training plan requires token capabilities")
        if type(self.group_ids) is not tuple or len(self.group_ids) != len(self.execution.jobs):
            raise ValueError("group assignments must match rollout candidates, not turns")
        from behave.core.records import text

        for group in self.group_ids:
            if group is not None:
                text(group, "algorithm group id")

    def to_bytes(self):
        return canonical_json(
            {
                "schema": "behave.training-plan/1",
                "execution": parse_json(self.execution.to_bytes()),
                "manifest": self.manifest.as_dict(),
                "requirements": self.requirements.as_dict(),
                "group_ids": list(self.group_ids),
            }
        )

    @classmethod
    def from_bytes(cls, payload, reader):
        row = fields(parse_json(payload), {"schema", "execution", "manifest", "requirements", "group_ids"})
        version(row["schema"], "behave.training-plan/1")
        result = cls(
            BatchPlan.from_bytes(canonical_json(row["execution"]), reader),
            BlobRef.from_dict(row["manifest"]),
            TrainingRequirements.from_bytes(canonical_json(row["requirements"])),
            tuple(array(row["group_ids"])),
        )
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "training plan must be canonical")
        return result

    def preflight(self, reader, *, approved_manifest):
        if approved_manifest != self.manifest:
            raise RecordError("training-manifest-unapproved", "run manifest is not the operator-approved allowlist")
        manifest = TrainingManifest.from_bytes(read_verified(reader, self.manifest))
        tasks = dict(manifest.tasks)
        for job in self.execution.jobs:
            if job.task.release != manifest.release or tasks.get(job.task.task_id) != job.task.revision:
                raise RecordError("training-task-forbidden", "job is outside the approved training task revisions")
        if manifest.corpus is not None:
            from .plan import validate_membership

            return validate_membership(reader, manifest, self.execution.jobs)
        # v1 is the explicitly approved legacy allowlist, not a corpus audit.
        return ()


EXPERIMENT_SCHEMA = "behave.training-experiment/1"
MACHINE_SCHEMA = "behave.training-machine/1"
PATH_FIELDS = frozenset({"model_path", "native_python", "slime_source", "megatron_source", "kernel_cache"})
WORKER_FIELDS = frozenset({
    "workers", "tokenize_workers", "judge_workers", "preparation_workers",
    "cpu_threads", "build_jobs", "ray_cpus",
})
MACHINE_FIELDS = PATH_FIELDS | WORKER_FIELDS | {
    "endpoint_url", "preparation_start_method", "process_limit", "isolation_startup_slots",
}
REQUIRED_MACHINE_FIELDS = frozenset({"model_path", "native_python", "slime_source", "endpoint_url"})


def _settings(document: dict, schema: str) -> dict:
    fields(document, {"schema", "settings"})
    if document["schema"] != schema or type(document["settings"]) is not dict:
        raise ValueError(f"expected {schema} with an object-valued settings field")
    return document["settings"]


def _absolute_path(value, name: str) -> None:
    if type(value) is not str or not value or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} must be an explicit absolute path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise ValueError(f"{name} must be an explicit absolute path, not a filesystem root")


def compose_configuration(experiment: dict, machine: dict) -> dict:
    """Combine disjoint settings without recursive overlays or implicit defaults."""
    from behave.isolation.startup import startup_environment

    settings = _settings(experiment, EXPERIMENT_SCHEMA)
    deployment = _settings(machine, MACHINE_SCHEMA)
    misplaced = set(settings) & MACHINE_FIELDS
    if misplaced:
        raise ValueError(f"machine settings belong in the machine profile: {sorted(misplaced)}")
    unexpected = set(deployment) - MACHINE_FIELDS - {"checkpoint_retention"}
    if unexpected:
        raise ValueError(f"machine profile cannot set experiment/state fields: {sorted(unexpected)}")
    missing = REQUIRED_MACHINE_FIELDS - set(deployment)
    if missing:
        raise ValueError(f"machine profile is missing required settings: {sorted(missing)}")
    for name in PATH_FIELDS & deployment.keys():
        if name != "kernel_cache" or deployment[name] is not None:
            _absolute_path(deployment[name], name)
    for name in WORKER_FIELDS & deployment.keys():
        if type(deployment[name]) is not int or deployment[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    limit = deployment.get("process_limit")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("process_limit must be positive or null")
    startup_environment(deployment.get("isolation_startup_slots"))
    if ("preparation_start_method" in deployment
            and deployment["preparation_start_method"] not in ("spawn", "fork", "forkserver")):
        raise ValueError("unsupported preparation_start_method")
    endpoint = deployment["endpoint_url"]
    if type(endpoint) is not str or any(ord(char) < 33 for char in endpoint):
        raise ValueError("endpoint_url must be an HTTP(S) URL")
    url = urlsplit(endpoint)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("endpoint_url must be an HTTP(S) URL without embedded credentials")

    retention = settings.get("checkpoint_retention")
    if retention is not None and type(retention) is not dict:
        raise ValueError("checkpoint_retention must be an object")
    if retention is not None and "protected_paths" in retention:
        raise ValueError("protected_paths belong in the machine profile, not the experiment")
    protected = deployment.get("checkpoint_retention")
    if "checkpoint_retention" in deployment:
        fields(protected, {"protected_paths"})
        if type(protected["protected_paths"]) is not list:
            raise ValueError("protected_paths must be a list")
        for path in protected["protected_paths"]:
            _absolute_path(path, "protected_paths")
        if retention is None:
            raise ValueError("machine protection requires an explicit experiment retention policy")

    result = deepcopy(settings)
    result.update(deepcopy({key: value for key, value in deployment.items() if key != "checkpoint_retention"}))
    if protected is not None:
        result["checkpoint_retention"].update(deepcopy(protected))
    return result


def load_configuration(configuration: Path, *, machine_profile: Path | None = None) -> dict:
    """Load a legacy flat configuration or an explicitly paired split recipe."""
    document = parse_json(read_regular_file(configuration))
    if "schema" not in document:
        if machine_profile is not None:
            raise ValueError("a machine profile requires a split experiment; flat configs are already resolved")
        return document
    _settings(document, EXPERIMENT_SCHEMA)
    if machine_profile is None:
        raise ValueError("a split experiment requires an explicit --machine-profile")
    return compose_configuration(document, parse_json(read_regular_file(machine_profile)))


def release_membership(reader, release_ref):
    release = ReleaseManifest.from_bytes(record(reader, release_ref))
    policy = ReleasePolicy.from_bytes(record(reader, release.policy))
    guard = policy.corpus
    if guard is None or guard.partition == "final-test":
        raise RecordError("training-corpus-required", "a guarded training release is required")
    if thaw_json(release.metadata).get("corpus") != guard.as_dict():
        raise RecordError("training-corpus-mismatch", "release and admission policy name different corpus guards")
    index = load_index(reader, guard.index)
    return release, guard, {e.task_id: e for e in index.entries}


def manifest_for_release(reader, release_ref, *, task_ids=None):
    """Prepare, but do not approve, a v2 training allowlist for explicit tasks.

    Omitting task_ids selects the whole guarded training release, never a folder.
    An operator must separately pin the returned manifest to authorize a run.
    """
    from .plan import TrainingManifest

    release, guard, _ = release_membership(reader, release_ref)
    selected = tuple(r.task_id for r in release.tasks) if task_ids is None else task_ids
    if type(selected) is not tuple or not selected or len(set(selected)) != len(selected):
        raise ValueError("select a nonempty tuple of distinct training task ids")
    revisions = {r.task_id: r.manifest for r in release.tasks}
    if not set(selected) <= revisions.keys():
        raise RecordError("training-task-forbidden", "task is not in the selected training release")
    result = TrainingManifest(release_ref, tuple((task, revisions[task]) for task in selected), guard.index)
    validate_membership(reader, result, ())
    return result


def validate_membership(reader, manifest, jobs):
    """Authenticate all allowlisted revisions, then each actual solver input view."""
    release, guard, entries = release_membership(reader, manifest.release)
    if manifest.corpus != guard.index:
        raise RecordError("training-corpus-mismatch", "approved manifest pins another corpus index")
    revisions = {r.task_id: r.manifest for r in release.tasks}
    tasks = {}
    for task_id, ref in manifest.tasks:
        if revisions.get(task_id) != ref:
            raise RecordError("training-task-forbidden", "allowlist revision is outside its published release")
        task = TaskManifest.from_bytes(record(reader, ref))
        if task.task_id != task_id:
            raise RecordError("training-task-forbidden", "revision has another task id")
        assigned(entries, guard, task=task)
        tasks[task_id] = task
    for job in jobs:
        task = tasks[job.task.task_id]
        expected = file_set((task.spec, task.contract, *task.public_files))
        if (file_set(tuple(f.reference for f in job.task.files)) != expected
                or job.task.spec_path != task.spec.path or job.task.contract_path != task.contract.path):
            raise RecordError("training-public-view-mismatch", "solver view differs from the approved task revision")
    return tuple(dict.fromkeys((manifest.release, release.policy, guard.index, *(r for _, r in manifest.tasks))))


def chunks(refs):
    ordered = tuple(sorted(set(refs), key=lambda ref: ref.sha256))
    for offset in range(0, len(ordered), defaults.PUBLICATION_CHUNK_REFS):
        chunk = ordered[offset:offset + defaults.PUBLICATION_CHUNK_REFS]
        yield {"offset": offset, "count": len(chunk),
               "digest": BlobRef.of(canonical_json([r.as_dict() for r in chunk])).sha256}, chunk


def verify_chunks(events, refs):
    # A reader validates the recorded partition, not today's I/O batch size.
    ordered = tuple(sorted(set(refs), key=lambda ref: ref.sha256))
    offset = 0
    for event in events:
        chunk = event.artifacts
        payload = {"offset": offset, "count": len(chunk),
                   "digest": BlobRef.of(canonical_json([r.as_dict() for r in chunk])).sha256}
        if (not chunk or event.kind != "training_membership" or thaw_json(event.payload) != payload
                or chunk != ordered[offset:offset + len(chunk)]):
            raise RecordError("training-membership-mismatch", "training membership closure differs from its allowlist")
        offset += len(chunk)
    if offset != len(ordered):
        raise RecordError("training-membership-mismatch", "training membership closure differs from its allowlist")


def resolve_training_seed(configuration):
    result = deepcopy(configuration)
    if "training_seed" not in result:
        return result
    seed = result["training_seed"]
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError("training_seed must be a nonnegative signed 32-bit integer")
    if result.get("sampler") is None:
        raise ValueError("training_seed requires an explicit sampler")
    result["sampler"]["seed"] = str(seed)
    arguments = list(result["native_arguments"])
    if any(value.startswith("--seed=") for value in arguments) or arguments.count("--seed") > 1:
        raise ValueError("native seed must have a single --seed VALUE representation")
    if "--seed" in arguments:
        index = arguments.index("--seed") + 1
        if index == len(arguments) or arguments[index].startswith("--"):
            raise ValueError("native --seed requires a value")
        arguments[index] = str(seed)
    else:
        arguments.extend(("--seed", str(seed)))
    if "seed" in result.get("sampling", {}):
        raise ValueError("training_seed uses advancing engine RNG, not a shared per-request seed")
    result["native_arguments"] = arguments
    return result


def current_path(kind: str = "authored", *, repository: Path) -> Path:
    base = repository / "data/splits"
    index = parse_json(read_regular_file(base / "current.json"))
    if kind not in index:
        raise RecordError("training-split-invalid", f"current split index does not select {kind!r}")
    return base / text(index[kind], "split directory")


def check_training_selection(task_ids, train_split: Path, *, repository: Path) -> bytes:
    payload = read_regular_file(train_split)
    allowed = set(payload.decode().split())
    current = current_path(repository=repository)
    held_out = set(read_regular_file(current / "eval.txt").decode("utf-8").split())
    held_out.update(read_regular_file(current / "external_eval.txt").decode("utf-8").split())
    if overlap := allowed & held_out:
        raise ValueError(f"training list contains current evaluation tasks: {sorted(overlap)}")
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("select distinct training task IDs")
    if forbidden := set(task_ids) - allowed:
        raise ValueError(f"tasks outside the training split: {sorted(forbidden)}")
    return payload


def prepare_from_release(output: Path, configuration: dict, release: Path, task_ids: list[str],
                         train_split: Path, *, repository: Path,
                         parent_reader=None, parent_checkpoint=None) -> dict:
    """Prepare the existing native plan without launching a model or optimizer."""
    from behave.core.artifacts import BlobRef, DirectoryReader, read_verified
    from behave.core.storage import DirectoryStore
    from behave.data.published import ReleaseManifest
    from behave.runtime.records import write_json

    from .plan import TrainingManifest
    from .slime_data import prepare_task_plan

    split = check_training_selection(task_ids, train_split, repository=repository)
    reader = DirectoryReader(release.resolve() / "objects")
    release_ref = BlobRef.from_dict(parse_json(read_regular_file(release / "release.json")))
    record = ReleaseManifest.from_bytes(read_verified(reader, release_ref))
    revisions = {entry.task_id: entry.manifest for entry in record.tasks}
    if missing := set(task_ids) - revisions.keys():
        raise RecordError("training-task-unpublished", f"selected tasks are absent from the release: {sorted(missing)}")
    if "corpus" in record.metadata:
        from .plan import manifest_for_release

        manifest = manifest_for_release(reader, release_ref, task_ids=tuple(task_ids))
    else:
        manifest = TrainingManifest(release_ref, tuple((task, revisions[task]) for task in task_ids))
    binding = prepare_task_plan(output, configuration, reader, manifest,
        parent_reader=parent_reader, parent_checkpoint=parent_checkpoint)
    write_json(output / "source_split.json", DirectoryStore(binding["objects"]).put(split).as_dict())
    return binding
