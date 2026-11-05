"""Native checkpoint metadata on the hot path; byte hashing only on request.

Slime owns save completion and deserialization. Normal save/resume records the
completed step and state-file sizes without rereading weights or optimizer
states. ``checkpoint_files`` is an explicit offline byte audit, not an update
hook or a portable CAS export.
"""

import ast
import hashlib
import json
import math
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, FileRef
from behave.core.file_input import read_regular_file
from behave.runtime.models import CheckpointFile


def checkpoint_due(args, rollout_id: int) -> bool:
    """Pinned Slime cadence for explicit finite runs (no epoch triggers).

    Slime performs the save; this only guards our journal between those saves.
    A positive interval always saves the final round, even off cadence.
    """
    if any(type(value) is not int or value < 1 for value in (args.save_interval, args.num_rollout)):
        raise ValueError("explicit positive native save interval and stopping round required")
    return (rollout_id + 1) % args.save_interval == 0 or rollout_id == args.num_rollout - 1


def iteration(root: Path) -> int:
    value = read_regular_file(root / "latest_checkpointed_iteration.txt").strip()
    if not value.isdigit():
        raise ValueError("native checkpoint needs a numeric iteration")
    return int(value)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _file_reference(root: Path, path: Path, expected) -> FileRef:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if _identity(before) != _identity(expected):
            raise ValueError("native checkpoint changed during inventory")
        digest, size = hashlib.sha256(), 0
        while data := stream.read(defaults.CHECKPOINT_CHUNK_BYTES):
            size += len(data)
            if size > before.st_size:
                raise ValueError("native checkpoint changed during inventory")
            digest.update(data)
        after = os.fstat(stream.fileno())
        if (_identity(before) != _identity(after) or size != before.st_size
                or _identity(path.stat(follow_symlinks=False)) != _identity(after)):
            raise ValueError("native checkpoint changed during inventory")
    return FileRef(path.relative_to(root).as_posix(), BlobRef(digest.hexdigest(), size))


def _state_paths(root: Path, step: int) -> list[Path]:
    if root.is_symlink():
        raise ValueError("native checkpoint root must not be linked")
    if type(step) is not int or step < 0 or iteration(root) != step:
        raise ValueError("native checkpoint marker disagrees with completed round")
    directory = root / f"iter_{step:07d}"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("native checkpoint iteration directory missing or linked")

    result = []
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories:
            if (Path(parent) / name).is_symlink():
                raise ValueError("native checkpoint contains a linked directory")
        result.extend(Path(parent) / name for name in files)
    if not result:
        raise ValueError("native checkpoint has no state files")
    return sorted(result)


def checkpoint_inventory(root: Path, step: int, *, critic: bool = False) -> tuple[CheckpointFile, ...]:
    """Record native save metadata without reading any state-file contents."""
    result = []
    for path in _state_paths(root, step):
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("native checkpoint requires regular state files")
        result.append(CheckpointFile(path.relative_to(root).as_posix(), info.st_size))
    if critic:
        result.extend(CheckpointFile("critic/" + file.path, file.size_bytes)
                      for file in checkpoint_inventory(root / "critic", step))
    return tuple(result)


def checkpoint_files(root: Path, step: int, *, critic: bool = False) -> tuple[FileRef, ...]:
    """Explicit offline SHA256 audit. Not called by native save or resume."""
    paths, expected = _state_paths(root, step), []
    for path in paths:
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("native checkpoint requires regular state files")
        expected.append(info)
    # A real 27B Adam checkpoint has hundreds of GB across independent shards.
    # SHA256 releases the GIL; configured concurrent readers avoid a serial CPU
    # bottleneck without loading weights into memory. map preserves path order.
    with ThreadPoolExecutor(max_workers=min(defaults.PUBLICATION_WORKERS, len(paths))) as pool:
        result = list(pool.map(lambda item: _file_reference(root, *item), zip(paths, expected, strict=True)))
    if paths != _state_paths(root, step):
        raise ValueError("native checkpoint changed during inventory")
    if any(_identity(path.stat(follow_symlinks=False)) != _identity(info)
           for path, info in zip(paths, expected, strict=True)):
        raise ValueError("native checkpoint changed during inventory")
    if critic:
        result.extend(FileRef("critic/" + file.path, file.content)
                      for file in checkpoint_files(root / "critic", step))
    return tuple(result)


def native_phase_timings(text: str) -> list[dict]:
    """Read rank-zero durations measured by Slime's exited timer scopes.

    Keep the logged clock and phase name; do not infer an unfinished duration
    or assign cold-start weight publication to a preceding optimizer update.
    A scope can exit through an exception. Its timer alone proves no update.
    """
    pattern = (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] "
               r"timer\.py:\d+ - Timer (actor_train|save_model|update_weights) "
               r"end \(elapsed: (\d+(?:\.\d+)?)s\)")
    return [{"logged_at": stamp, "phase": phase, "seconds": float(seconds)}
            for stamp, phase, seconds in re.findall(pattern, text)]


def training_steps(text: str, *, role: str = "actor", require_nonzero: bool = True) -> list[dict]:
    if role not in ("actor", "critic"):
        raise ValueError("unknown native training role")
    prefix = "train/" if role == "actor" else "train/critic-"
    key = prefix + "grad_norm"
    rows = []
    for match in re.finditer(r"step \d+: (\{[^\n]*'train/[^\n]*grad_norm'[^\n]*\})", text):
        row = ast.literal_eval(match.group(1))
        if key not in row:
            continue
        step, norm = row["train/step"], row[key]
        if (type(step) is not int or step < 0 or type(norm) not in (int, float)
                or not math.isfinite(norm) or norm < 0):
            raise ValueError("invalid native training metric")
        # Variable-length rounds can reuse native step IDs. Suppress identical
        # stdout/stderr copies, not distinct updates with the same step label.
        if row not in rows:
            rows.append(row)
    if not rows:
        raise ValueError("no native training gradient evidence for " + role)
    if require_nonzero and not any(row[key] > 0 for row in rows):
        raise ValueError("no nonzero native training gradient for " + role)
    return rows


def native_training_metrics(text: str) -> list[dict]:
    """Keep the actor's logged optimization metrics and native clock.

    Reuse the existing gradient-record parser without requesting another model
    forward. A later update may reuse a native step number and metric values.
    """
    pattern = (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] "
               r"[^\n]*? - (step \d+: \{[^\n]*'train/grad_norm'[^\n]*\})")
    rows = []
    for stamp, record in re.findall(pattern, text):
        row, = training_steps(record, require_nonzero=False)
        measured = {"logged_at": stamp, **row}
        if measured not in rows:
            rows.append(measured)
    return rows


def changed_decoder_weight(left: Path, right: Path, *, require_change: bool = True) -> dict | None:
    """Find a numerical decoder update. Imported only in the selected Torch ABI."""
    import torch
    from safetensors import safe_open

    before = json.loads((left / "model.safetensors.index.json").read_bytes())["weight_map"]
    after = json.loads((right / "model.safetensors.index.json").read_bytes())["weight_map"]
    for name in sorted(before.keys() & after.keys()):
        if not name.startswith("model.language_model.layers."):
            continue
        with safe_open(left / before[name], framework="pt", device="cpu") as a, \
                safe_open(right / after[name], framework="pt", device="cpu") as b:
            x, y = a.get_tensor(name), b.get_tensor(name)
            if x.shape != y.shape or not (torch.isfinite(x).all() and torch.isfinite(y).all()):
                raise ValueError("incompatible or non-finite decoder weights")
            if not torch.equal(x, y):
                return {"tensor": name, "changed_elements": int(torch.count_nonzero(x != y)),
                        "max_abs_difference": float((x.float() - y.float()).abs().max())}
    if require_change:
        raise ValueError("no numerical decoder-weight update found")
    return None


def scored_rounds(root: Path) -> list[dict]:
    """Read recorded round metrics, or fall back to exported training reports."""
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.agent.run_store import JournalDirectory
    from behave.core import runtime_defaults as defaults
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.records import parse_json, thaw_json
    from behave.core.storage import DirectoryStore
    from behave.task_execution.result import TaskResult

    from .slime_launch import SlimeRecipe
    from .slime_rollout import from_training_report, read_binding
    from .training_data import NativeChatVerifier

    binding = read_binding(root / "binding.json")
    initial_manifest = BlobRef.from_dict(binding["manifest"])
    recipe = SlimeRecipe.from_dict(binding["recipe"])
    run = JournalDirectory(root / "rounds/native")
    rows = []
    for event in run.journal("rounds").load(verify_artifacts=False):
        if event.kind != "round_scored":
            continue
        row = parse_json(read_verified(run.objects, BlobRef.from_dict(thaw_json(event.payload["receipt"]))))
        manifest = initial_manifest
        if event.payload.get("acquisition") is not None:
            transition = parse_json(read_verified(run.objects,
                BlobRef.from_dict(thaw_json(event.payload["acquisition"]))))
            manifest = BlobRef.from_dict(transition["training_manifest"])
        recorded_round = event.payload.get("recorded_round", event.payload["round"])
        if row["round"] != recorded_round or BlobRef.from_dict(row["manifest"]) != manifest:
            raise ValueError("native round receipt differs from the binding")
        metrics_path = Path(row["objects"]).parent / "metrics.json"
        if metrics_path.is_file():
            measured = json.loads(metrics_path.read_text())
            rows.append({**measured, "round": event.payload["round"], "skipped_groups": ()})
            continue
        objects = DirectoryStore(row["objects"] if "recorded_round" in event.payload else
                                 root / f"rounds/round_{row['round']:08d}/objects")
        batch = from_training_report(objects, BlobRef.from_dict(row["head"]), approved_manifest=manifest,
            verifier=NativeChatVerifier(LocalChatEndpoint.from_dict(row["endpoint"])), recipe=recipe,
            missing_reward_policy=binding.get("missing_reward_policy", defaults.TRAIN_MISSING_REWARD_POLICY))
        candidates = []
        for item in batch.candidates:
            result = TaskResult.from_bytes(read_verified(objects, BlobRef.from_dict(item["result"])))
            judgment = result.judgment
            candidates.append({**item, "task": result.task_id, "termination": result.termination,
                "submitted": result.submission is not None,
                "rtl_score": None if judgment is None else judgment.rtl.score,
                "behavior_score": None if judgment is None or judgment.behavior is None else judgment.behavior.score})
        rows.append({"round": event.payload["round"], "candidates": candidates,
            "skipped_groups": batch.skipped_groups,
            "retained_turns": sum(item["retained_turns"] for item in batch.candidates),
                     "max_sample_tokens": max((len(p["tokens"]) for p in batch.payloads), default=0)})
    return rows


HOOK = "behave.train.slime_checkpoint.before_train_step"


def before_train_step(args, rollout_id, step_id, model, optimizer, opt_param_scheduler):
    if getattr(args, "behave_resume_export_checked", False):
        return
    if step_id != 0:
        raise ValueError("resume HF export must run before the first optimizer step")
    from .slime_checkpoint import iteration

    saved = iteration(Path(args.load))
    if rollout_id != saved + 1:
        raise ValueError("resume HF export cursor differs from the loaded checkpoint")
    interval = args.behave_hf_checkpoint_interval
    if interval is None or interval < 1 or getattr(args, "behave_hf_checkpoint_updates", None):
        raise ValueError("resume HF export needs the periodic HF schedule")
    if (saved + 1) % interval == 0:
        from slime.backends.megatron_utils.hf_checkpoint_saver import save_hf_model_to_path

        # All ranks participate, including when retrying an incomplete export.
        # The native exporter installs the index only after writing all shards.
        target = Path(args.behave_hf_save_pattern.format(rollout_id=saved))
        save_hf_model_to_path(args, target, model)
    # Do not read the parent again: retention may retire it after the next save.
    args.behave_resume_export_checked = True
