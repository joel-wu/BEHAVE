"""Delete only explicitly inventoried checkpoint files, retaining receipts.

There is no recursive deletion or directory-wide wildcard. Unexpected files,
links, changed metadata and non-iteration targets fail closed. A durable receipt
is installed before the first unlink and permits recovery after interruption.
"""

import json
import os
import re
import stat
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import mkdtemp

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.file_input import read_regular_file
from behave.core.paths import scratch_root
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version
from behave.core.runtime_locations import runtime_path
from behave.core.storage import DirectoryStore, install_exclusive
from behave.runtime.models import CheckpointFile
from behave.serving.model_files import safetensor_shards
from behave.serving.spec import ServingSpec

from .checkpoints import Evidence, StateFile, copy_state_file, recover
from .slime_checkpoint import checkpoint_inventory
from .training_data import TokenVerifier
from .update_records import TrainerContract


def regular_directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("checkpoint cleanup requires an explicit absolute directory")
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError("checkpoint cleanup refuses linked directories")
    if not path.is_dir():
        raise ValueError(f"checkpoint directory is missing: {path}")
    return path


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink]


def _files(directory, expected):
    result = {}
    allowed_directories = {parent.as_posix() for name in expected
                           for parent in Path(name).parents if parent != Path('.')}
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories:
            path = regular_directory(Path(parent) / name)
            if path.relative_to(directory).as_posix() not in allowed_directories:
                raise ValueError("checkpoint deletion inventory contains an unexpected directory")
        for name in files:
            path = Path(parent) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("checkpoint cleanup refuses links or special files")
            result[path.relative_to(directory).as_posix()] = info
    return result


def checkpoint_delete_plan(directory, files, receipt_root):
    """Validate an exact iteration and its committed, directory-relative inventory."""
    directory, receipt_root = Path(directory), Path(receipt_root)
    if not re.fullmatch(r"iter_[0-9]{7}", directory.name):
        raise ValueError("checkpoint cleanup only accepts an exact iteration directory")
    regular_directory(directory.parent)
    if directory.is_symlink():
        raise ValueError("checkpoint cleanup refuses linked directories")
    expected = {}
    for row in files:
        path = Path(row["path"])
        size = row["size_bytes"]
        if (path.is_absolute() or ".." in path.parts or path.as_posix() != row["path"]
                or path.as_posix() in ("", ".") or row["path"] in expected
                or type(size) is not int or size < 0):
            raise ValueError("invalid checkpoint deletion inventory")
        expected[row["path"]] = size
    if not expected:
        raise ValueError("checkpoint deletion needs a nonempty committed inventory")
    request = {"directory": str(directory), "files": dict(sorted(expected.items()))}
    key = BlobRef.of(canonical_json(request)).sha256
    # Receipts must be outside the checkpoint, so deleting tensors cannot erase
    # the authorization or the interruption-recovery record.
    if receipt_root == directory or directory in receipt_root.parents:
        raise ValueError("checkpoint cleanup receipts must live outside the iteration")
    receipt_root.mkdir(parents=True, exist_ok=True)
    regular_directory(receipt_root)
    receipt = receipt_root / f"{key}.json"
    if receipt.is_symlink():
        raise ValueError("checkpoint cleanup refuses a linked receipt")
    prior = json.loads(receipt.read_bytes()) if receipt.exists() else None
    if prior is not None and prior["request"] != request:
        raise ValueError("checkpoint cleanup receipt differs from the request")
    if directory.exists():
        regular_directory(directory)
        actual = _files(directory, expected)
    else:
        actual = {}
    if set(actual) - expected.keys() or (prior is None and actual.keys() != expected.keys()):
        raise ValueError("checkpoint deletion inventory differs: missing or unexpected files")
    for name, info in actual.items():
        if info.st_size != expected[name] or (prior is not None and _identity(info) != prior["identities"][name]):
            raise ValueError("checkpoint file changed after inventory")
    record = prior if prior is not None else {
        "schema": "behave.checkpoint-file-deletion/1", "request": request,
        "identities": {name: _identity(info) for name, info in actual.items()},
        "allocated_bytes": sum(info.st_blocks * 512 for info in actual.values()),
        "reclaimable_bytes": sum(info.st_blocks * 512 for info in actual.values() if info.st_nlink == 1),
    }
    return receipt, record


def delete_checkpoint_files(directory, files, receipt_root):
    receipt, record = checkpoint_delete_plan(directory, files, receipt_root)
    payload = canonical_json(record)
    if not install_exclusive(receipt.parent, receipt.name, payload) and receipt.read_bytes() != payload:
        raise ValueError("checkpoint cleanup receipt changed")
    directory = Path(directory)
    for name in sorted(record["request"]["files"]):
        path = directory / name
        if not path.exists() and not path.is_symlink():
            continue  # An earlier, receipted unlink may have completed.
        regular_directory(path.parent)
        if _identity(path.lstat()) != record["identities"][name]:
            raise ValueError("checkpoint file changed before unlink")
        path.unlink()
    # rmdir removes only empty, explicitly derived directories. It never walks
    # into unknown content and cannot delete an unexpected file.
    directories = {directory}
    for name in record["request"]["files"]:
        parent = (directory / name).parent
        while parent != directory:
            directories.add(parent)
            parent = parent.parent
    for parent in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        if parent.exists():
            regular_directory(parent)
            parent.rmdir()
    completion = canonical_json({"schema": "behave.checkpoint-file-deletion-complete/1",
                                 "receipt": str(receipt), "directory": str(directory)})
    if not install_exclusive(receipt.parent, receipt.stem + ".done.json", completion):
        if (receipt.parent / (receipt.stem + ".done.json")).read_bytes() != completion:
            raise ValueError("checkpoint cleanup completion differs")
    return receipt


def absolute_directory(value):
    from behave.core.runtime_locations import runtime_path
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise ValueError("checkpoint location requires an explicit absolute directory")
    path = runtime_path(path)
    if path.resolve() != path or path.is_symlink():
        raise ValueError("checkpoint location must not traverse symlinks")
    return path


def run_directory(value):
    """Canonical run identity across an operator-migrated work-root alias.

    Run/plan paths may traverse that alias; native and HF destinations still
    use absolute_directory and cannot traverse symlinks. Persisted ownership
    records are compared, not rewritten, when an old run root is relocated.
    """
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise ValueError("run location requires an explicit absolute directory")
    from behave.core.runtime_locations import runtime_path
    return runtime_path(path).resolve()


def location_record(root):
    from behave.core.runtime_locations import runtime_path
    root = runtime_path(root)
    return root.with_name(root.name + ".location.json")


def resolve_checkpoint(root, *, step=None, files=None, checkpoint=None):
    """Resolve one recorded relocation and verify its native inventory.

    Missing records preserve the legacy path. Broken or stale records fail
    closed; there is no search for a similarly named checkpoint or fallback.
    """
    from behave.core.runtime_locations import runtime_path
    root = runtime_path(root)
    marker = location_record(root)
    if not marker.exists() and not marker.is_symlink():
        return root
    row = fields(json.loads(read_regular_file(marker)), {
        "schema", "source", "destination", "step", "files", "checkpoint"})
    if (row["schema"] != "behave.checkpoint-location/1"
            or run_directory(row["source"]) != run_directory(root)):
        raise ValueError("checkpoint location record does not belong to this source")
    identity = BlobRef.from_dict(row["checkpoint"])
    if checkpoint is not None and checkpoint != identity:
        raise ValueError("checkpoint location identity differs from the committed checkpoint")
    target = absolute_directory(row["destination"])
    if target == root or location_record(target).exists() or location_record(target).is_symlink():
        raise ValueError("chained checkpoint relocations are not supported")
    if type(row["step"]) is not int or row["step"] < 0 or (step is not None and step != row["step"]):
        raise ValueError("checkpoint location step differs from the requested checkpoint")
    expected = tuple(CheckpointFile.from_dict(v) for v in row["files"])
    if not expected or (files is not None and tuple(files) != expected):
        raise ValueError("checkpoint location inventory differs from the committed checkpoint")
    if checkpoint_inventory(target, row["step"]) != expected:
        raise ValueError("relocated checkpoint inventory differs from its location record")
    return target


def job_checkpoint_directory(job, *, step=None, files=None, checkpoint=None):
    """Read a run's storage-only choice, falling back to the historical layout."""
    job = run_directory(Path(job).absolute())
    record = job / "checkpoint_storage.json"
    root = job / "artifacts/checkpoint"
    if record.exists() or record.is_symlink():
        row = fields(json.loads(read_regular_file(record)), {"schema", "job", "directory"})
        if row["schema"] != "behave.checkpoint-storage/1" or run_directory(row["job"]) != job:
            raise ValueError("checkpoint storage record belongs to another run")
        root = absolute_directory(row["directory"])
    target = resolve_checkpoint(root, step=step, files=files, checkpoint=checkpoint)
    if record.exists() or record.is_symlink():
        owner = json.loads(read_regular_file(target / "storage_owner.json"))
        if owner != row:
            raise ValueError("checkpoint directory ownership differs from the run storage record")
    return target


def storage_destination(repository, job, directory=None):
    """Resolve the workspace save profile without changing experiment config.

    Jobs outside the profile's run tree retain their legacy layout unless an
    explicit directory is supplied. This keeps fixture and external callers
    backward-compatible and prevents basename collisions between experiments.
    """
    if directory is not None:
        return absolute_directory(directory)
    profile = Path(repository) / "infra/train/checkpoint_storage.json"
    if not profile.exists():
        return None
    row = fields(json.loads(read_regular_file(profile)), {"schema", "run_root", "checkpoint_root"})
    if row["schema"] != "behave.checkpoint-storage-profile/1":
        raise ValueError("unknown checkpoint storage profile")
    runs = run_directory(row["run_root"])
    root = absolute_directory(row["checkpoint_root"])
    if root != Path(row["checkpoint_root"]):
        raise ValueError("runtime profile remaps the configured checkpoint save root; "
                         "use a profile that permits its direct destination")
    job = run_directory(Path(job).absolute())
    if not job.is_relative_to(runs):
        return None
    if job == runs:
        raise ValueError("the run tree itself is not a training job")
    return absolute_directory(root / job.relative_to(runs) / "native")


def install_storage(job, directory):
    """Claim a unique save directory and persist the choice before dispatch."""
    job = run_directory(Path(job).absolute())
    directory = absolute_directory(directory)
    if directory.exists():
        raise ValueError("new run checkpoint directory already exists")
    directory.mkdir(parents=True, exist_ok=False)
    row = {"schema": "behave.checkpoint-storage/1", "job": str(job), "directory": str(directory)}
    data = canonical_json(row)
    if not install_exclusive(directory, "storage_owner.json", data):
        raise ValueError("checkpoint directory already has an owner")
    if not install_exclusive(job, "checkpoint_storage.json", data):
        raise ValueError("run already has a checkpoint storage choice")
    from .checkpoint_storage import install_hf_storage
    install_hf_storage(job, directory)
    return row


def profile_pin_overrides(repository):
    """Pin the active runtime-locations profile into the new snapshot's helper.

    A child with a sanitized environment must never silently revert to the
    original cold paths.
    """
    from behave.core.runtime_locations import profile_identity

    identity = profile_identity()
    if identity is None:
        return {}
    helper = "src/behave/core/runtime_locations.py"
    data = (Path(repository) / helper).read_bytes()
    data = data.replace(b"DEFAULT_PROFILE = None", ("DEFAULT_PROFILE = " + repr(identity["path"])).encode(), 1)
    data = data.replace(b"EXPECTED_PROFILE_SHA256 = None",
                        ("EXPECTED_PROFILE_SHA256 = " + repr(identity["sha256"])).encode(), 1)
    return {helper: data}


def retention_policy(value):
    mode = value.get("mode", "archive")
    if mode not in {"archive", "delete"}:
        raise ValueError("checkpoint retention mode must be archive or delete")
    keys = {"keep_full", "archive_root"} if mode == "archive" else {"keep_full", "mode"}
    row = fields(value, keys | ({"mode", "protected_paths", "retire_parent"} & value.keys()))
    if type(row["keep_full"]) is not int or row["keep_full"] < 1:
        raise ValueError("checkpoint retention must keep at least one full save")
    if mode == "archive" and (type(row["archive_root"]) is not str or not Path(row["archive_root"]).is_absolute()):
        raise ValueError("checkpoint archive_root must be an explicit absolute path")
    if "retire_parent" in row and (type(row["retire_parent"]) is not bool or (mode == "archive" and row["retire_parent"])):
        raise ValueError("parent retirement must be an explicit delete-policy boolean")
    if "protected_paths" in row:
        if not isinstance(row["protected_paths"], (list, tuple)) or any(
                type(path) is not str or not Path(path).is_absolute() or ".." in Path(path).parts
                for path in row["protected_paths"]):
            raise ValueError("protected checkpoints must be explicit absolute paths")
    return row


def retain_checkpoints(root, events, policy, *, critic=False, parent_events=()):
    """Retire committed native iterations according to the explicit policy.

    The default archives only this run's saves; it does not free storage.
    Opt-in deletion checks the replacement and exact inventory, honoring pins.
    Neither mode retires HF snapshots or rollout/pool evidence.
    """
    policy = retention_policy(policy)
    if policy.get("mode") == "delete":
        return _delete_retired(root, events, policy, critic=critic, parent_events=parent_events)
    root = Path(root).resolve()
    steps = sorted({event.payload["round"] for event in events
                    if event.kind == "checkpoint_saved"
                    and Path(event.payload["native_directory"]).resolve() == root})
    sources = [base / f"iter_{step:07d}" for step in steps[:-policy["keep_full"]]
               for base in ((root, root / "critic") if critic else (root,))]
    sources = [source for source in sources if source.exists()]
    if not sources:
        return None
    archive = Path(policy["archive_root"]) / datetime.now(UTC).strftime("%Y-%m-%d") / "training-checkpoints"
    archive.mkdir(parents=True, exist_ok=True)
    destination = Path(mkdtemp(prefix="retained-", dir=archive))
    moves = [{"source": str(source), "destination": str(destination / source.relative_to(source.anchor))}
             for source in sources]
    # Written before moves, so even an interrupted rename sequence is recoverable.
    receipt = destination / "moves.json"
    receipt.write_bytes(canonical_json({"checkpoint_root": str(root),
        "kept_steps": steps[-policy["keep_full"]:], "moves": moves}))
    for move in moves:
        target = Path(move["destination"])
        target.parent.mkdir(parents=True, exist_ok=True)
        Path(move["source"]).rename(target)
    return receipt


def _pinned(directory, policy):
    if directory in (Path(path) for path in policy.get("protected_paths", ())):
        return True
    marker = directory.parent / "retention_pins.json"
    if marker.is_symlink():
        raise ValueError("checkpoint pins must not be linked")
    if not marker.exists():
        return False
    row = fields(json.loads(marker.read_bytes()), {"steps"})
    if not isinstance(row["steps"], list) or any(type(step) is not int or step < 0 for step in row["steps"]):
        raise ValueError("checkpoint pins must list nonnegative native iteration indices")
    return int(directory.name[5:]) in row["steps"]


def _delete_retired(root, events, policy, *, critic, parent_events):
    from .checkpoint_storage import checkpoint_delete_plan, delete_checkpoint_files, regular_directory
    from .slime_checkpoint import CheckpointFile, checkpoint_inventory

    root = regular_directory(Path(root).absolute())
    current = [event for event in events if event.kind == "checkpoint_saved"
               and Path(event.payload["native_directory"]).absolute() == root]
    if not current or events[-1] != current[-1]:
        raise ValueError("checkpoint deletion requires a newly committed local save")
    latest = thaw_json(current[-1].payload)
    actual = checkpoint_inventory(root, latest["round"], critic=critic)
    expected = tuple(CheckpointFile.from_dict(row) for row in latest["files"])
    if actual != expected:
        raise ValueError("replacement checkpoint differs from its committed inventory")
    records = [*parent_events, *current] if policy.get("retire_parent", False) else current
    saved = {(str(Path(event.payload["native_directory"]).absolute()), event.payload["round"]): thaw_json(event.payload)
             for event in records if event.kind == "checkpoint_saved"}
    ordered = sorted(saved, key=lambda item: item[1])
    if ordered[-1] != (str(root), latest["round"]):
        raise ValueError("replacement checkpoint must be newer than its parents")
    receipts = root / "retention_receipts"
    targets = []
    for key in ordered[:-policy["keep_full"]]:
        row = saved[key]
        base = runtime_path(key[0])
        for prefix in (Path(), Path("critic")) if critic else (Path(),):
            relative = prefix / f"iter_{row['round']:07d}"
            directory = base / relative
            if _pinned(base / f"iter_{row['round']:07d}", policy):
                continue
            # Skip previously retired saves, but do not follow a dangling link.
            if not directory.exists() and not directory.is_symlink():
                continue
            files = []
            for entry in row["files"]:
                file = CheckpointFile.from_dict(entry)
                if Path(file.path).is_relative_to(relative):
                    files.append({"path": Path(file.path).relative_to(relative).as_posix(),
                                  "size_bytes": file.size_bytes})
            checkpoint_delete_plan(directory, files, receipts)
            targets.append((directory, files))
    for directory, files in targets:
        delete_checkpoint_files(directory, files, receipts)
    return receipts if targets else None


def _absolute(path):
    from .checkpoint_storage import absolute_directory
    return absolute_directory(path)


def _run(path):
    from .checkpoint_storage import run_directory
    return run_directory(path)


def _install(path, row):
    data = canonical_json(row)
    if not install_exclusive(path.parent, path.name, data) and read_regular_file(path) != data:
        raise ValueError('HF storage record changed')


def install_hf_storage(job, native):
    """Claim a unique sibling for HF, alongside the run's external native save."""
    job, native = _run(job), _absolute(native)
    target = native.with_name('hf' if native.name == 'native' else native.name + '.hf')
    if target.exists() or target.is_symlink():
        raise ValueError('HF storage directory already exists')
    row = {'schema': 'behave.hf-storage/1', 'job': str(job), 'directory': str(target)}
    target.mkdir(parents=True, exist_ok=False)
    _install(target / 'storage_owner.json', row)
    _install(job / 'hf_storage.json', row)
    return target


def job_hf_directory(job):
    job = _run(job)
    record = job / 'hf_storage.json'
    if not record.exists() and not record.is_symlink():
        return None
    row = fields(json.loads(read_regular_file(record)), {'schema', 'job', 'directory'})
    if row['schema'] != 'behave.hf-storage/1' or _run(row['job']) != job:
        raise ValueError('HF storage record belongs to another run')
    target = _absolute(row['directory'])
    if json.loads(read_regular_file(target / 'storage_owner.json')) != row:
        raise ValueError('HF storage ownership differs from its run')
    return target


def prepare_hf_storage(job, plan, updates):
    """Bind one fresh plan and expose legacy export names without weight copies."""
    job, plan = _run(job), _run(plan)
    if type(updates) is not int or updates < 1:
        raise ValueError('HF storage needs a positive update target')
    target = job_hf_directory(job)
    if target is None:
        return
    if plan != job / 'artifacts/plan':
        raise ValueError('HF plan does not belong to its run')
    for step in range(updates):
        alias = plan / f'hf_{step}'
        destination = target / alias.name
        if alias.is_symlink():
            if alias.readlink() != destination:
                raise ValueError('Existing HF alias points elsewhere')
        elif alias.exists():
            raise ValueError('Refusing to replace an existing HF export')
    record = plan / 'hf_storage.json'
    if record.exists() or record.is_symlink():
        # Validate old aliases without rewriting immutable pre-migration records.
        if hf_save_pattern(plan) != str(target / 'hf_{rollout_id}'):
            raise ValueError('HF plan destination differs from its run')
    else:
        _install(record, {'schema': 'behave.hf-plan-storage/1',
            'job': str(job), 'plan': str(plan), 'directory': str(target)})
    for step in range(updates):
        alias = plan / f'hf_{step}'
        if not alias.is_symlink():
            alias.symlink_to(target / alias.name, target_is_directory=True)


def hf_save_pattern(plan):
    """Read storage-only placement; legacy plans retain the original save path."""
    plan = _run(plan)
    record = plan / 'hf_storage.json'
    if not record.exists() and not record.is_symlink():
        return str(plan / 'hf_{rollout_id}')
    row = fields(json.loads(read_regular_file(record)), {'schema', 'job', 'plan', 'directory'})
    job = _run(row['job'])
    if (row['schema'] != 'behave.hf-plan-storage/1' or _run(row['plan']) != plan
            or plan != job / 'artifacts/plan'):
        raise ValueError('HF storage record belongs to another plan')
    target = job_hf_directory(job)
    if target is None or str(target) != row['directory']:
        raise ValueError('HF plan destination differs from its run')
    return str(target / 'hf_{rollout_id}')


@dataclass(frozen=True, slots=True)
class WeightPublication:
    checkpoint: BlobRef
    contract: BlobRef
    state: BlobRef
    policy: BlobRef
    component: str
    rank: int | None = None

    def __post_init__(self) -> None:
        if any(type(ref) is not BlobRef for ref in (self.checkpoint, self.contract, self.state, self.policy)):
            raise TypeError("weight publication requires checkpoint and content references")
        text(self.component, "inference state component")
        if self.rank is not None and (type(self.rank) is not int or self.rank < 0):
            raise ValueError("inference export rank must be a nonnegative integer")

    def to_bytes(self) -> bytes:
        return canonical_json({
            "schema": "behave.weight-publication/2" if self.rank is not None else "behave.weight-publication/1",
            "component": self.component,
            **{name: getattr(self, name).as_dict() for name in ("checkpoint", "contract", "state", "policy")},
            **({"rank": self.rank} if self.rank is not None else {}),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> "WeightPublication":
        image = parse_json(data)
        row = fields(image, {"schema", "checkpoint", "contract", "state", "policy", "component"}
                     | ({"rank"} if "rank" in image else set()))
        version(row["schema"], "behave.weight-publication/2" if "rank" in row else "behave.weight-publication/1")
        result = cls(*(BlobRef.from_dict(row[name]) for name in ("checkpoint", "contract", "state", "policy")),
                     row["component"], row.get("rank"))
        if result.to_bytes() != data:
            raise RecordError("record-not-canonical", "weight publication must be canonical")
        return result


def _select(
    reader: ContentReader, head: BlobRef, contract: TrainerContract, component: str, verifier: TokenVerifier | None,
    rank: int | None = None,
) -> tuple[Evidence, WeightPublication, tuple[StateFile, ...]]:
    text(component, "inference state component")
    if component not in contract.components:
        raise RecordError("weights-component-missing", "inference component must be declared by the trainer")
    evidence = recover(reader, load_chain(reader, head), contract, verifier)
    checkpoint = evidence.checkpoint
    if checkpoint is None or evidence.pending is not None or checkpoint.reference != head:
        raise RecordError("weights-checkpoint-uncommitted", "weight publication requires an exact committed head")
    selected = checkpoint.state
    if rank is not None:
        from .cluster_state import unpack_rank_states

        world = selected.cursor.get("world_size")
        states = unpack_rank_states(selected, world)
        if type(rank) is not int or not 0 <= rank < len(states):
            raise RecordError("weights-rank-invalid", "inference export rank is outside this checkpoint")
        selected = states[rank]
    elif selected.cursor.get("schema") == "behave.distributed-cursor/1":
        raise RecordError("weights-rank-required", "select the rank containing the backend's complete inference export")
    files = tuple(file for file in selected.files if file.component == component)
    by_name = {file.path: file for file in files}
    if "config.json" not in by_name:
        raise RecordError("weights-config-missing", "inference component must include its local config.json")
    index = by_name.get("model.safetensors.index.json")
    data = None
    if index is not None:
        stream = BytesIO()
        copy_state_file(reader, index, stream)
        data = stream.getvalue()
    safetensor_shards(by_name, data)
    publication = WeightPublication(head, BlobRef.of(contract.to_bytes()), checkpoint.state_ref,
                                    checkpoint.state.policy, component, rank)
    return evidence, publication, files


def publish_weights(
    reader: ContentReader, head: BlobRef, *, contract: TrainerContract, component: str,
    objects: DirectoryStore, verifier: TokenVerifier | None = None,
    rank: int | None = None,
) -> BlobRef:
    """Copy the verified private checkpoint closure, then publish one immutable root.

    Repeating this operation is byte-idempotent and dispatches no conversion or
    training. Lost publication acknowledgement can be retried with the same
    inputs. The source must be a sealed ledger, not a mutable checkpoint folder.
    """
    evidence, publication, _ = _select(reader, head, contract, component, verifier, rank)
    data = publication.to_bytes()
    objects.publish(reader, tuple(sorted(evidence.references, key=lambda ref: ref.sha256)))
    return objects.put(data)


def load_weights(
    reader: ContentReader, reference: BlobRef, *, checkpoint: BlobRef, contract: TrainerContract,
    component: str, verifier: TokenVerifier | None = None,
) -> tuple[WeightPublication, tuple[StateFile, ...]]:
    """Verify against independent expected lineage, never infer the expected policy."""
    publication = WeightPublication.from_bytes(read_verified(reader, reference))
    if (publication.checkpoint != checkpoint or publication.contract != BlobRef.of(contract.to_bytes())
            or publication.component != component):
        raise RecordError("weights-lineage-mismatch", "publication differs from the expected checkpoint or contract")
    _, expected, files = _select(reader, checkpoint, contract, component, verifier, publication.rank)
    if publication != expected:
        raise RecordError("weights-lineage-mismatch", "publication state or policy differs from its committed head")
    return publication, files


def stage_weights(
    reader: ContentReader, reference: BlobRef, *, checkpoint: BlobRef, contract: TrainerContract,
    component: str, spec: ServingSpec, verifier: TokenVerifier | None = None,
) -> ServingSpec:
    """Verify and stream inference files below the configured node-local scratch root.

    Returns a new spec binding model_path and model_revision to this publication;
    all other operator settings are preserved. No service is started. On success
    the caller owns the directory and must retain it until ServingSession.close
    confirms cleanup. It is deliberately NOT automatically removed on an error
    in the caller, which could leave a live server using the files. Partial
    staging files are also retained, with their path attached to the error.
    """
    import os

    if type(spec) is not ServingSpec:
        raise TypeError("staging requires a serving spec")
    _, files = load_weights(reader, reference, checkpoint=checkpoint, contract=contract,
                            component=component, verifier=verifier)
    directory = Path(mkdtemp(prefix="behave-weights-", dir=scratch_root()))
    try:
        for file in files:
            path = directory / file.path
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as stream:
                copy_state_file(reader, file, stream)
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
        return replace(spec, model_path=str(directory), model_revision=reference.sha256)
    except BaseException as error:
        error.add_note(f"Partial weight staging retained at {directory}")
        raise
