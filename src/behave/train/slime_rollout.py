"""Slime's control-Python bridge to the existing scored candidate application.

The native trainer does not import EDA dependencies or run a second agent loop.
An optional frozen sampler selects complete task groups. Rebinding then changes
only the generating policy and round identity, never score policy, capabilities
or turn-exclusion requirements.
"""

import json
import math
import multiprocessing
import os
import statistics
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.run_store import JournalDirectory
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, DirectoryReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json, version
from behave.core.storage import DirectoryStore
from behave.task_execution.batch import BatchRecovery
from behave.task_execution.judgment import final_score
from behave.task_execution.result import TaskResult, final_score_from_record

from .plan import TrainingPlan
from .pool import (
    SamplingPolicy,
    TaskPoolPolicy,
    replenishment_limit,
    sample_plan,
    select_tasks,
    selected_indices,
    task_reserve,
)
from .slime_launch import GROUPED, SlimeRecipe, require_sampling_profile
from .trainer import NativeAnchor, active_events
from .training_data import (
    NativeChatVerifier,
    RolloutExamples,
    TokenTrace,
    TrainingCandidate,
    TrainingRequirements,
    TurnExample,
    prefix_cache_usage,
)


def bind_round(plan: TrainingPlan, endpoint: LocalChatEndpoint, *, round_id: int,
               policy: BlobRef, control_python: str | None = None,
               control_package: str | None = None) -> TrainingPlan:
    if type(round_id) is not int or round_id < 0 or type(policy) is not BlobRef:
        raise ValueError("explicit nonnegative round and policy identity required")
    if endpoint.tokens is None or endpoint.tokens.runtime not in {"sglang", "sglang-slime"}:
        raise ValueError("Slime requires a native-token SGLang endpoint")
    require_sampling_profile(endpoint.parameters)
    tokens = replace(endpoint.tokens, checkpoint=policy)
    endpoint = replace(endpoint, tokens=tokens, routing="attempt")
    command = endpoint.command(python=control_python, package=control_package)
    model_policy = endpoint.model_policy(command=command)
    jobs = tuple(replace(job, model=command, attempt=replace(
        job.attempt, model_policy=model_policy,
        attempt_id=f"{job.attempt.attempt_id}:round:{round_id}",
    )) for job in plan.execution.jobs)
    return replace(plan, execution=replace(plan.execution, jobs=jobs,
        run_id=f"{plan.execution.run_id}:round:{round_id}"), requirements=replace(
            plan.requirements, adapter=tokens.adapter, checkpoint=policy,
            tokenizer=tokens.tokenizer, template=tokens.template))


def read_binding(path: Path) -> dict:
    from behave.core.file_input import read_regular_file
    from behave.core.runtime_locations import runtime_path

    raw = read_regular_file(runtime_path(path))
    value = parse_json(raw)
    keys = {"schema", "control_python", "control_package", "objects",
        "plan", "manifest", "endpoint", "recipe", "run_directory"}
    extended = "sampler" in value
    continued = "parent_checkpoint" in value
    optional = {"round_seconds", "cleanup_seconds", "control_timeout_seconds", "round_log_bytes", "preparation_workers",
                "update_groups", "rollout_engine_count"}
    row = fields(value, keys | ((optional | {"recorded_rounds", "prepared_batches", "preparation_start_method",
                                          "missing_reward_policy", "curriculum", "empty_rollout_retries",
                                          "group_replenishment", "checkpoint_retention", "parent_task_pool",
                                          "self_improvement", "rollout_recovery"}) & value.keys())
                 | ({"sampler"} if extended else set()) | ({"parent_checkpoint"} if continued else set()))
    version(row["schema"], f"behave.slime-rollout-binding/{3 if continued else 2 if extended else 1}")
    if row.get("missing_reward_policy", defaults.TRAIN_MISSING_REWARD_POLICY) not in {"error", "skip_group"}:
        raise ValueError("missing_reward_policy must be error or skip_group")
    if continued:
        BlobRef.from_dict(row["parent_checkpoint"])
    if extended:
        SamplingPolicy.from_dict(row["sampler"])
    if "curriculum" in row:
        if not extended:
            raise ValueError("task curriculum requires an explicit sampler")
        TaskPoolPolicy.from_dict(row["curriculum"])
    if "group_replenishment" in row:
        if not extended or row.get("update_groups") is None:
            raise ValueError("group replenishment requires sampler and update_groups")
        replenishment_limit(row["group_replenishment"], target=row["update_groups"],
            window=row["sampler"]["tasks_per_update"], pool_size=row["sampler"]["tasks_per_update"])
    if "recorded_rounds" in row:
        if type(row["recorded_rounds"]) is not list or not row["recorded_rounds"]:
            raise ValueError("recorded_rounds requires an explicit nonempty sequence of scored receipts")
        for receipt in row["recorded_rounds"]:
            BlobRef.from_dict(receipt)
    if "checkpoint_retention" in row:
        from .checkpoint_storage import retention_policy

        retention_policy(row["checkpoint_retention"])
    for name in ("control_python", "control_package", "objects", "run_directory"):
        if type(row[name]) is not str or not Path(row[name]).is_absolute():
            raise ValueError(f"absolute Slime binding path required: {name}")
    for name in optional:
        value = row.get(name)
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(f"{name} must be a positive integer or None")
    for name in ("plan", "manifest"):
        BlobRef.from_dict(row[name])
    endpoint = LocalChatEndpoint.from_dict(row["endpoint"])
    if endpoint.tokens is None:
        raise ValueError("Slime rollout binding must pin native token identities")
    require_sampling_profile(endpoint.parameters)
    SlimeRecipe.from_dict(row["recipe"])
    retries = row.get("empty_rollout_retries", 0)
    if type(retries) is not int or retries < 0:
        raise ValueError("empty_rollout_retries must be a nonnegative integer")
    return row


def round_plan(binding, reader, *, round_id, endpoint, policy, task_selection=None):
    """Derive the exact planned group membership on both sides of the bridge."""
    from .pool import extended_selection
    task_selection = extended_selection(binding, round_id, task_selection)
    template = TrainingPlan.from_bytes(read_verified(reader, BlobRef.from_dict(binding["plan"])), reader)
    template.preflight(reader, approved_manifest=BlobRef.from_dict(binding["manifest"]))
    if "group_replenishment" in binding:
        tasks = tuple(dict.fromkeys(job.task.task_id for job in template.execution.jobs))
        sampler = SamplingPolicy.from_dict(binding["sampler"])
        limit = replenishment_limit(binding["group_replenishment"], target=binding["update_groups"],
                                    window=sampler.tasks_per_update, pool_size=len(tasks))
        if "curriculum" in binding:
            if task_selection is None or not binding["update_groups"] <= len(task_selection) <= limit:
                raise ValueError("replenishment requires the recorded approved task reserve")
            reserve = tuple(row["task_id"] for row in task_selection)
        else:
            reserve = task_reserve(tasks, sampler, round_id)[:limit]
        template = select_tasks(template, reserve)
    elif "curriculum" in binding:
        if task_selection is None or len(task_selection) != binding["sampler"]["tasks_per_update"]:
            raise ValueError("curriculum rollout requires its recorded complete task selection")
        template = select_tasks(template, tuple(row["task_id"] for row in task_selection))
    elif "sampler" in binding:
        template = sample_plan(template, SamplingPolicy.from_dict(binding["sampler"]), round_id)
    result = bind_round(template, endpoint, round_id=round_id, policy=policy,
                        control_python=binding["control_python"], control_package=binding["control_package"])
    if "rollout_recovery" in binding:
        from .slime_rollout import mixed_plan

        result = mixed_plan(binding, reader, result, round_id)
    return result


def execute_round(binding: dict, *, round_id: int, endpoint: LocalChatEndpoint,
                  policy_record: dict, cancelled: Callable[[], bool] = lambda: False) -> dict:
    from behave.task_execution.cocotb_worker import CocotbBackend
    from behave.task_execution.simulator_judge import SimulatorJudge

    from .learning_report import round_metrics
    from .rollouts import run_rollouts
    from .slime_rollout import from_prepared_report, save_batch

    began = time.monotonic()
    started_at = datetime.now(UTC).isoformat()

    inputs = DirectoryStore(binding["objects"])
    manifest = BlobRef.from_dict(binding["manifest"])
    policy = inputs.put(canonical_json(policy_record))
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy), routing="attempt")
    task_selection = policy_record.get("task_selection")
    plan = round_plan(binding, inputs, round_id=round_id, endpoint=endpoint, policy=policy,
                      task_selection=task_selection)
    recovery = policy_record.get("recovery", 0)
    run = JournalDirectory(Path(binding["run_directory"]) / (f"round_{round_id:08d}" +
                           (f"_recovery_{recovery}" if recovery else "")))
    verifier = NativeChatVerifier(endpoint)
    handoff = None
    if "rollout_recovery" in binding:
        from behave.task_execution.batch import BatchRecovery

        from .slime_rollout import handoff_verifier, recovery_for

        record = recovery_for(binding, round_id)
        if record is not None:
            handoff = BatchRecovery.load(inputs, BlobRef.from_dict(record["case_head"]))
            if "task_selection_extension" in record:
                handoff = replace(handoff, allow_append=True)
            verifier = handoff_verifier(binding, round_id, endpoint)
    # Same public scheduling boundary as offline Eval: candidates share their
    # actual CPU allocation rather than each claiming the whole node.
    batch = None
    preparation_seconds = 0.

    def ready(prepared):
        nonlocal batch, preparation_seconds
        if cancelled():
            from behave.agent.attempt import AttemptInterrupted

            raise AttemptInterrupted("external-workflow-cancelled")
        preparation_started = time.monotonic()
        batch = from_prepared_report(prepared, verifier=verifier,
            recipe=SlimeRecipe.from_dict(binding["recipe"]),
            missing_reward_policy=binding.get("missing_reward_policy", defaults.TRAIN_MISSING_REWARD_POLICY),
            preparation_workers=binding.get("preparation_workers", defaults.TRAIN_PREPARATION_WORKERS),
            preparation_start_method=binding.get("preparation_start_method", defaults.TRAIN_PREPARATION_START_METHOD),
            preparation_directory=run.directory / "preparation")
        preparation_seconds = time.monotonic() - preparation_started

    run_rollouts(plan=plan, approved_manifest=manifest, reader=inputs, run=run,
                 judge=SimulatorJudge(), verifier=verifier, on_prepared=ready,
                 cancelled=cancelled,
                 update_groups=binding.get("update_groups"),
                 active_groups=(binding["sampler"]["tasks_per_update"]
                                if "group_replenishment" in binding else None),
                 empty_rollout_retries=binding.get("empty_rollout_retries", 0),
                 **({"recovery": handoff} if handoff is not None else {}),
                 testbench=None if plan.execution.testbench is None else CocotbBackend())
    head, _ = run.seal("training")
    if batch is None:
        from behave.core.records import RecordError

        raise RecordError("training-groups-insufficient",
                          f"not enough reward-varying groups; no update; retained training report {head.sha256}")
    prepared_path = run.directory / "prepared_batch.json"
    save_batch(prepared_path, batch, head=head, manifest=manifest, plan=BlobRef.of(plan.to_bytes()))
    metrics = round_metrics(batch, run.objects, round_id=round_id, policy=policy_record,
        started_at=started_at, finished_at=datetime.now(UTC).isoformat(),
        preparation_seconds=preparation_seconds, producer_seconds=time.monotonic() - began,
        directory=run.directory)
    with (run.directory / "metrics.json").open("x") as stream:
        json.dump(metrics, stream, indent=2)
    # The producer already checked the records. Transfer its prepared payloads,
    # while retaining the original report for independent offline inspection.
    return {"schema": "behave.slime-scored-round/1", "round": round_id,
            "objects": str(run.objects.directory), "head": head.as_dict(),
            "manifest": manifest.as_dict(), "endpoint": endpoint.as_dict(), "policy": policy.as_dict(),
            "prepared_batch": str(prepared_path),
            **({} if task_selection is None else {"task_selection": task_selection})}


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding", type=Path)
    parser.add_argument("request", type=Path)
    parser.add_argument("response", type=Path)
    args = parser.parse_args(argv)
    binding = read_binding(args.binding)
    from behave.core.file_input import read_regular_file

    raw = read_regular_file(args.request)
    request = fields(parse_json(raw), {"round", "endpoint", "policy"})
    result = execute_round(binding, round_id=request["round"],
        endpoint=LocalChatEndpoint.from_dict(request["endpoint"]), policy_record=request["policy"])
    with args.response.open("xb") as stream:
        stream.write(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def select_scored_groups(rows, policy):
    if policy not in {"error", "skip_group"}:
        raise ValueError("missing_reward_policy must be error or skip_group")
    groups = {}
    for i, row in enumerate(rows):
        key = ("group", row["group_id"]) if row["group_id"] is not None else ("candidate", i)
        groups.setdefault(key, []).append(i)
    omitted, skipped = set(), []
    for indices in groups.values():
        if all(rows[i]["examples"] is not None for i in indices):
            continue
        if policy == "error":
            raise RecordError("training-examples-unavailable", "rollout records are missing, not just their scores")
        omitted.update(indices)
        skipped.append({"group_id": rows[indices[0]]["group_id"], "reason": "examples-unavailable",
            "members": [{"index": i, "task_id": rows[i]["task_id"], "attempt_id": rows[i]["attempt_id"],
                "result": None if rows[i]["examples"] is None else rows[i]["examples"]["result"],
                "reward": None if rows[i]["examples"] is None else rows[i]["examples"]["reward"]}
                for i in indices]})
    kept = tuple(i for i in range(len(rows)) if i not in omitted)
    if not kept:
        raise RecordError("training-no-scored-groups",
                          "no groups with rollout records: finish without an optimizer update; no automatic retry")
    return kept, tuple(skipped)


def admitted_batch(batch, *, policy, planned, skipped):
    if policy == "skip_group":
        admission = {"planned_candidates": planned, "selected_candidates": len(batch.candidates)}
        for part in batch.payloads:
            part["metadata"]["behave_batch_admission"] = admission
    return replace(batch, skipped_groups=skipped)


_SIGNALS = ("rollout_log_probs", "teacher_log_probs")


def _signals(payload):
    return tuple(name for name in _SIGNALS if name in payload), tuple(payload.get("train_metadata", {}))


def coalesce_payloads(payloads):
    """Return independent prefix chains, with original response-span metadata.

    Try every existing chain, not just the preceding call: an agent may return
    to a previously used branch. Prefer the longest compatible chain. Missing
    optional signals keep their own chain rather than inventing probabilities.
    No length threshold, lossy realignment or candidate regrouping is involved.
    The input dictionaries remain unchanged.
    """
    chains = []
    for payload in payloads:
        prompt_length = len(payload["tokens"]) - payload["response_length"]
        compatible = (
            chain for chain in chains
            if len(chain["tokens"]) <= prompt_length
            and _signals(chain) == _signals(payload)
            and payload["tokens"][:len(chain["tokens"])] == chain["tokens"]
        )
        chain = max(compatible, key=lambda item: len(item["tokens"]), default=None)
        original = payload["metadata"]
        span = {name: original[name] for name in ("turn", "request", "response")}
        span.update(response_start=prompt_length, response_length=payload["response_length"])
        if chain is None:
            chain = deepcopy(payload)
            chain["metadata"]["turns"] = [span]
            chains.append(chain)
            continue

        gap = prompt_length - len(chain["tokens"])
        chain["tokens"].extend(payload["tokens"][len(chain["tokens"]):])
        chain["response_length"] += gap + payload["response_length"]
        chain["loss_mask"].extend([0] * gap)
        chain["loss_mask"].extend(payload["loss_mask"])
        for name in _signals(chain)[0]:
            chain[name].extend([0.0] * gap)
            chain[name].extend(payload[name])
        for name in _signals(chain)[1]:
            chain["train_metadata"][name].extend([0.0] * gap)
            chain["train_metadata"][name].extend(payload["train_metadata"][name])
        chain["metadata"]["turns"].append(span)

    if payloads:
        for offset, chain in enumerate(chains):
            chain["index"] = payloads[0]["index"] + offset
    return tuple(chains)


def response_spans(payload):
    """Original model calls represented by a packed or historical payload."""
    metadata = payload["metadata"]
    return metadata.get("turns", (metadata,))


def sample_payloads(
    examples: RolloutExamples, *, rollout_id: int, first_index: int, group_index: int | None = None
) -> tuple[dict, ...]:
    if type(examples) is not RolloutExamples:
        raise TypeError("Slime handoff requires validated rollout examples")
    for value in (rollout_id, first_index, group_index):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("sample indices must be nonnegative integers")
    if rollout_id is None or first_index is None:
        raise ValueError("rollout and sample index are required")
    result = examples.candidate.result
    payloads = []
    for offset, turn in enumerate(examples.eligible):
        trace = turn.tokens
        payload = {
            "group_index": group_index,
            "rollout_id": rollout_id,
            "index": first_index + offset,
            "tokens": list(trace.input_ids + trace.response_ids),
            "response_length": len(trace.response_ids),
            "loss_mask": list(trace.response_mask),
            "reward": examples.reward,
            "weight_versions": [trace.checkpoint.sha256],
            "metadata": {
                "task_id": result.task_id,
                "attempt_id": result.attempt_id,
                "turn": turn.turn,
                "result": examples.candidate.result_ref.as_dict(),
                "request": turn.request.as_dict(),
                "response": turn.response.as_dict(),
                "release": result.release.as_dict(),
                "tokenizer": trace.tokenizer.as_dict(),
                "template": trace.template.as_dict(),
            },
        }
        for name, target in (("old_log_probs", "rollout_log_probs"), ("teacher_log_probs", "teacher_log_probs")):
            if name in trace.signals:
                payload[target] = list(trace.signals[name])
        if "values" in trace.signals:
            payload["train_metadata"] = {"values": list(trace.signals["values"])}
        payloads.append(payload)
    return tuple(payloads)


def make_samples(
    examples: RolloutExamples, *, rollout_id: int, first_index: int, group_index: int | None = None
) -> tuple:
    """Materialize native Slime Samples only in the explicitly selected trainer.

    Dependency loading is delayed until this call. Neither this adapter nor the
    shared runtime chooses GRPO, advantage estimation, loss weights or a sampler.
    """
    payloads = sample_payloads(examples, rollout_id=rollout_id, first_index=first_index, group_index=group_index)
    if not payloads:
        return ()
    from slime.utils.types import Sample

    return tuple(Sample(**payload, status=Sample.Status.COMPLETED) for payload in payloads)


def validate_execution_change(old, new):
    """Allow serving changes and CP-only resharding, never training semantics.

    Keep TP, PP, DP and the optimizer recipe unchanged. Native distributed
    checkpoint loading must restore optimizer state; an HF-only restart is not
    a substitute. CP changes are explicit execution changes, not bitwise replay.
    """
    from dataclasses import asdict

    from .pool import is_upgrade
    from .slime_launch import SlimeLayout

    cooldown_upgrade = is_upgrade(old, new)
    checked_new = new
    if cooldown_upgrade:
        # Only this explicit opt-in changes selection; rewards, seeds and all
        # other policy fields remain fixed. Existing candidate slots are kept.
        checked_new = {**new, "curriculum": {k: v for k, v in new["curriculum"].items()
                                             if k != "cooldown_fallback"}}
        old = {**old, "curriculum": {k: v for k, v in old["curriculum"].items()
                                    if k != "cooldown_fallback"}}

    layouts = [asdict(SlimeLayout(**value["layout"])) for value in (old, new)]
    serving_keys = {"rollout_gpus", "rollout_gpus_per_engine", "rollout_excluded_gpus"}
    reshard_keys = {"actor_gpus_per_node", "context_parallel"}
    fixed_keys = serving_keys | reshard_keys
    if ({k: v for k, v in layouts[0].items() if k not in fixed_keys}
            != {k: v for k, v in layouts[1].items() if k not in fixed_keys}
            or layouts[0]["actor_gpus_per_node"] * layouts[1]["context_parallel"]
            != layouts[1]["actor_gpus_per_node"] * layouts[0]["context_parallel"]):
        raise ValueError("rollout handoff cannot change the training layout")
    # HF export cadence affects storage only, never candidate weights or rewards.
    ignored = {"initial_updates", "resume_updates", "endpoint_url", "native_python", "workers", "layout",
               "hf_checkpoint_updates", "hf_checkpoint_interval"}
    if ({k: v for k, v in old.items() if k not in ignored}
            != {k: v for k, v in checked_new.items() if k not in ignored}):
        raise ValueError("rollout handoff cannot change training/sampling configuration or seed")
    if type(new["workers"]) is not int or new["workers"] < 1:
        raise ValueError("rollout worker count must be positive")
    return {"before": {"layout": layouts[0], "workers": old["workers"]},
            "after": {"layout": layouts[1], "workers": new["workers"]},
            **({"cooldown_fallback_enabled": True} if cooldown_upgrade else {})}


def inspect_handoff(previous, configuration, release, tasks, *, workers_stopped):
    from behave.core.runtime_locations import runtime_path
    if not workers_stopped:
        raise ValueError("rollout handoff requires explicit --workers-stopped confirmation")
    previous = runtime_path(previous).resolve()
    old_config = json.loads((previous / "configuration.json").read_text())
    execution_change = validate_execution_change(old_config, configuration)
    selection = json.loads((previous / "selection.json").read_text())
    if selection["tasks"] != tasks or runtime_path(selection["release"]).resolve() != runtime_path(release).resolve():
        raise ValueError("rollout handoff cannot change the task selection or release")
    plan_dir = previous / "artifacts/plan"
    binding = json.loads((plan_dir / "binding.json").read_text())
    native = JournalDirectory(Path(binding["run_directory"]) / "native")
    # A lock is an additional live-coordinator check, NOT remote worker fencing.
    with native.owner():
        raw_events = native.journal("rounds").load(verify_artifacts=False)
        events = active_events(raw_events)
        saved = next((e for e in reversed(events) if e.kind == "checkpoint_saved"), None)
        parent = (BlobRef.of(saved.to_bytes()) if saved is not None else
                  None if binding.get("parent_checkpoint") is None else
                  BlobRef.from_dict(binding["parent_checkpoint"]))
        parent_reader = native.objects if saved is not None else DirectoryStore(binding["objects"])
        prefix = ()
        anchor_reader = parent_reader
        if saved is not None:
            from types import SimpleNamespace

            prefix = tuple(e for e in raw_events if e.sequence <= saved.sequence)
            receipts = {BlobRef.of(e.to_bytes()): e.to_bytes() for e in prefix}
            anchor_reader = SimpleNamespace(read=lambda ref: receipts[ref] if ref in receipts else parent_reader.read(ref))
        anchor = None if parent is None else NativeAnchor.load(anchor_reader, parent)
        step = -1 if anchor is None else anchor.step
        started = next((e for e in reversed(events)
            if e.kind == "round_started" and e.payload["round"] == step + 1), None)
        if started is None:
            raise ValueError("no recoverable first uncommitted rollout after the saved weights")
        if "policy" in started.payload:
            policy_ref = BlobRef.from_dict(thaw_json(started.payload["policy"]))
            policy = parse_json(read_verified(native.objects, policy_ref))
            generation = policy.get("recovery", 0)
            suffix = f"_recovery_{generation}" if generation else ""
            directory = runtime_path(binding["run_directory"]) / f"round_{step + 1:08d}{suffix}"
            # The native request uses this coordinator's abandonment count.
            request_generation = sum(e.kind == "round_abandoned" for e in raw_events
                                     if e.sequence <= started.sequence)
            request_suffix = f"_recovery_{request_generation}" if request_generation else ""
            request = json.loads((native.directory / f"request_{step + 1:08d}{request_suffix}" / "request.json").read_text())
            if request["round"] != step + 1 or BlobRef.of(canonical_json(request["policy"])) != policy_ref:
                raise ValueError("rollout request differs from the recorded generating policy")
            endpoint = request["endpoint"]
            if "self_improvement" in binding:
                from .slime_acquisition import validate_active_binding
                from .slime_rollout import read_binding

                active = read_binding(native.directory / f"request_{step + 1:08d}{request_suffix}" / "binding.json")
                validate_active_binding(binding, active, parent, anchor_reader)
                binding = active
        elif "recorded_receipt" in started.payload:
            if "self_improvement" in binding:
                from .slime_acquisition import validate_active_binding

                validate_active_binding(binding, binding, parent, anchor_reader)
            # Repeated interruption of a completed-batch replay has no new
            # rollout request. Its immutable receipt points to the same batch.
            original = parse_json(read_verified(native.objects,
                BlobRef.from_dict(thaw_json(started.payload["recorded_receipt"]))))
            policy_ref = BlobRef.from_dict(original["policy"])
            policy = parse_json(read_verified(native.objects, policy_ref))
            validate_scored_receipt(original, round_id=step + 1, manifest=binding["manifest"],
                                    policy=policy_ref.as_dict())
            directory = runtime_path(original["objects"]).parent
            endpoint = original["endpoint"]
        else:
            raise ValueError("no recoverable generating policy or completed receipt")
        run = JournalDirectory(directory)
        execution = JournalDirectory(directory / "execution")
        with run.owner(), execution.owner():
            training = run.journal("training").load(verify_artifacts=False)
            report = execution.journal("report").load(verify_artifacts=False)
            if not training or not report:
                raise ValueError("no recorded candidate plan to recover")
            original_plan = BlobRef.from_dict(thaw_json(training[0].payload["plan"]))
            recovery = BatchRecovery.from_events(execution.objects, report)
        load = None
        if saved is not None:
            load = saved.payload["native_directory"]
        elif parent is not None:
            load = json.loads((previous / "continuation.json").read_text())["load"]
        if anchor is not None:
            from .checkpoint_storage import resolve_checkpoint
            from .slime_checkpoint import checkpoint_inventory

            load = str(resolve_checkpoint(load, step=anchor.step, files=anchor.files, checkpoint=anchor.head))
            if checkpoint_inventory(Path(load), anchor.step,
                    critic=binding["recipe"]["algorithm"] == "ppo") != anchor.files:
                raise ValueError("rollout handoff checkpoint inventory changed")
        scored = next((e for e in reversed(events)
            if e.kind == "round_scored" and e.payload["round"] == step + 1), None)
        receipt = started.payload.get("recorded_receipt") if scored is None else scored.payload.get("receipt")
        if receipt is not None:
            row = parse_json(read_verified(native.objects, BlobRef.from_dict(thaw_json(receipt))))
            validate_scored_receipt(row, round_id=step + 1, manifest=binding["manifest"],
                                    policy=policy_ref.as_dict())
            if "self_improvement" in binding:
                from .slime_acquisition import committed_plan

                state, _ = committed_plan(binding, parent, anchor_reader)
                if row.get("acquisition") != BlobRef.of(canonical_json(state)).as_dict():
                    raise ValueError("scored receipt differs from the committed acquisition")
        info = dict(previous=previous, binding=binding, native=native, events=events, saved=saved,
            parent=parent, parent_reader=parent_reader, parent_events=prefix, step=step, load=load,
            run=run, training=training, original_plan=original_plan, recovery=recovery,
            policy=policy, policy_ref=policy_ref, endpoint=endpoint, execution_change=execution_change,
            scored_receipt=None if receipt is None else thaw_json(receipt))
        from .pool import inspect_extension
        extension = inspect_extension(info, configuration)
        if extension is not None:
            info["task_selection_extension"] = extension
        return info


def validate_scored_receipt(row, *, round_id, manifest, policy):
    if (row.get("schema") != "behave.slime-scored-round/1" or row.get("round") != round_id
            or row.get("manifest") != manifest or row.get("policy") != policy):
        raise ValueError("completed rollout receipt differs from the restored policy/round")


def export_handoff(info, destination):
    """Hardlink immutable evidence on NFS; never edit the source candidate rows."""
    destination = DirectoryStore(Path(destination))
    recovery = info["recovery"]
    recovery.publish(destination)
    destination.publish_owned(info["run"].objects, tuple(info["training"][0].artifacts))
    destination.publish_owned(DirectoryStore(info["binding"]["objects"]),
        (BlobRef.from_dict(info["binding"]["plan"]), BlobRef.from_dict(info["binding"]["manifest"])))
    destination.put(canonical_json(info["policy"]))
    receipt = info.get("scored_receipt")
    if receipt is not None:
        destination.publish_owned(info["native"].objects, (BlobRef.from_dict(receipt),))
    record = {"schema": "behave.rollout-handoff/1", "round": info["step"] + 1,
        "checkpoint": None if info["parent"] is None else info["parent"].as_dict(),
        "manifest": info["binding"]["manifest"], "plan": info["original_plan"].as_dict(),
        "policy": info["policy_ref"].as_dict(), "case_head": recovery.head.as_dict(),
        "completed_indices": sorted(recovery.indices), "workers_stopped": True,
        "source": str(info["previous"]), "source_binding": info["binding"], "endpoint": info["endpoint"],
        "execution_change": info["execution_change"],
        **({"scored_receipt": receipt} if receipt is not None else {}),
        **({"task_selection_extension": info["task_selection_extension"]}
           if "task_selection_extension" in info else {})}
    return {"objects": str(destination.directory), "record": destination.put(canonical_json(record)).as_dict()}


def attach_handoff(binding, descriptor, objects):
    source = DirectoryStore(descriptor["objects"])
    reference = BlobRef.from_dict(descriptor["record"])
    record = parse_json(read_verified(source, reference))
    if record["schema"] != "behave.rollout-handoff/1" or record["workers_stopped"] is not True:
        raise ValueError("invalid or unfenced rollout handoff")
    if record["checkpoint"] != binding.get("parent_checkpoint") or record["manifest"] != binding["manifest"]:
        raise ValueError("rollout handoff differs from restored weights or approved tasks")
    parent = binding.get("parent_checkpoint")
    step = -1 if parent is None else NativeAnchor.load(objects, BlobRef.from_dict(parent)).step
    policy_ref = BlobRef.from_dict(record["policy"])
    policy = parse_json(read_verified(source, policy_ref))
    if record["round"] != step + 1 or policy["round"] != record["round"]:
        raise ValueError("rollout handoff is not the first round after the restored weights")
    receipt = record.get("scored_receipt")
    if receipt is not None:
        reference_receipt = BlobRef.from_dict(receipt)
        row = parse_json(read_verified(source, reference_receipt))
        validate_scored_receipt(row, round_id=record["round"], manifest=record["manifest"],
                               policy=record["policy"])
        objects.publish_owned(source, (reference_receipt,))
    recovery = BatchRecovery.load(source, BlobRef.from_dict(record["case_head"]))
    if sorted(recovery.indices) != record["completed_indices"]:
        raise ValueError("rollout handoff changed its completed candidate set")
    recovery.publish(objects)
    # The original training plan owns the immutable solver/tool input blobs.
    original = TrainingPlan.from_bytes(read_verified(source, BlobRef.from_dict(record["plan"])), source)
    if (original.execution != recovery.plan or original.requirements.checkpoint != policy_ref or
            original.manifest.as_dict() != record["manifest"]):
        raise ValueError("rollout handoff evidence differs from the original training plan")
    for job in original.execution.jobs:
        for data in job.input_blobs():
            objects.put(data)
    objects.publish_owned(source, (reference, BlobRef.from_dict(record["plan"]),
                                  BlobRef.from_dict(record["policy"])))
    binding["rollout_recovery"] = record


def recovery_for(binding, round_id):
    record = binding.get("rollout_recovery")
    return record if record is not None and record["round"] == round_id else None


def handoff_verifier(binding, round_id, endpoint):
    from behave.agent.providers.local_chat import LocalChatEndpoint

    from .training_data import NativeChatVerifier

    history = []
    record = recovery_for(binding, round_id)
    while record is not None:
        prior = LocalChatEndpoint.from_dict(record["endpoint"])
        history.append(replace(prior, tokens=replace(prior.tokens, checkpoint=endpoint.tokens.checkpoint)))
        record = recovery_for(record["source_binding"], round_id)
    return NativeChatVerifier(endpoint, *history)


def mixed_plan(binding, reader, fresh, round_id):
    record = recovery_for(binding, round_id)
    if record is None:
        return fresh
    original = TrainingPlan.from_bytes(read_verified(reader, BlobRef.from_dict(record["plan"])), reader)
    count = len(original.execution.jobs)
    extending = "task_selection_extension" in record
    if (original.manifest != fresh.manifest or original.requirements != fresh.requirements or
            original.group_ids != fresh.group_ids[:count] or
            (not extending and count != len(fresh.execution.jobs)) or count > len(fresh.execution.jobs)):
        raise ValueError("rollout recovery changed task groups or generating policy")
    if extending:
        from .pool import extended_selection
        policy = parse_json(read_verified(reader, BlobRef.from_dict(record["policy"])))
        selection = extended_selection(binding, round_id, policy["task_selection"])
        if tuple(dict.fromkeys(fresh.group_ids)) != tuple(row["task_id"] for row in selection):
            raise ValueError("rollout recovery differs from the recorded appended reserve")
    kept = set(record["completed_indices"])
    jobs = []
    for index, (old, new) in enumerate(zip(original.execution.jobs, fresh.execution.jobs[:count], strict=True)):
        # Only the model connection/launcher may differ, never task content,
        # candidate identity, prompt, tools, turn budget or scoring conditions.
        normalized = replace(old, model=new.model,
                             attempt=replace(old.attempt, model_policy=new.attempt.model_policy))
        if normalized.to_bytes() != new.to_bytes():
            raise ValueError("rollout recovery changed a candidate beyond its transport")
        # A fully scored batch is replayed verbatim, including the identities of
        # cancelled/unselected slots. No worker will run those old transports.
        jobs.append(old if index in kept or record.get("scored_receipt") is not None else new)
    jobs.extend(fresh.execution.jobs[count:])
    return replace(fresh, execution=replace(fresh.execution, jobs=tuple(jobs)))


@dataclass(frozen=True, slots=True)
class SlimeBatch:
    payloads: tuple[dict, ...]
    candidates: tuple[dict, ...]
    recipe: SlimeRecipe
    skipped_groups: tuple[dict, ...] = ()
    group_selection: dict | None = None

    def make_samples(self) -> tuple:
        from slime.utils.types import Sample

        samples = []
        for payload in self.payloads:
            sample = Sample(**payload, status=Sample.Status.COMPLETED)
            cache = payload["metadata"].get("behave_prefix_cache")
            if cache is not None:
                sample.prefix_cache_info = Sample.PrefixCacheInfo(**cache)
            samples.append(sample)
        return tuple(samples)

    def rollout_output(self):
        """Return Slime's native rollout-hook result without another agent loop.

        Metrics count candidates, including all-excluded candidates; a native
        flat-Sample reward mean would give longer conversations more weight.
        """
        from slime.rollout.base_types import RolloutFnTrainOutput

        siblings = {}
        for sample in self.make_samples():
            siblings.setdefault(sample.rollout_id, []).append(sample)
        return RolloutFnTrainOutput(samples=list(siblings.values()), metrics={
            "behave/candidates": len(self.candidates), "behave/retained_candidates": len(siblings),
            "behave/skipped_groups": len(self.skipped_groups),
            "behave/skipped_candidates": sum(len(group["members"]) for group in self.skipped_groups),
            "behave/planned_candidates": len(self.candidates) + sum(len(g["members"]) for g in self.skipped_groups),
            "behave/retained_turns": sum(len(response_spans(p)) for p in self.payloads),
            "behave/training_segments": len(self.payloads),
            # Upstream's default cache object is zero even when no measurement
            # was supplied. Publish coverage so missing telemetry is not a miss.
            "behave/cache_measured_turns": sum(p["metadata"].get("behave_cache_measured_turns",
                int("behave_prefix_cache" in p["metadata"])) for p in self.payloads),
            "behave/excluded_turns": sum(row["excluded_turns"] for row in self.candidates),
            "behave/unavailable_turns": sum(row.get("unavailable_turns", 0) for row in self.candidates),
            "behave/candidate_reward_mean": statistics.fmean(row["reward"] for row in self.candidates),
            **({} if self.group_selection is None else {
                "behave/launched_candidates": self.group_selection["launched_candidates"],
                "behave/launched_groups": self.group_selection["launched_groups"],
                "behave/selected_groups": self.group_selection["update_groups"]}),
            **({} if self.group_selection is None or "observations" not in self.group_selection else {
                "behave/constant_groups": sum(g["status"] == "constant" for g in self.group_selection["observations"]),
                "behave/unavailable_groups": sum(g["status"] == "unavailable" for g in self.group_selection["observations"])}),
        })


def from_training_report(
    reader: ContentReader, head: BlobRef, *, approved_manifest: BlobRef,
    verifier: NativeChatVerifier, recipe: SlimeRecipe,
    first_rollout: int = 0, first_sample: int = 0,
    expected_plan: BlobRef | None = None,
    preparation_workers: int = defaults.TRAIN_PREPARATION_WORKERS,
    preparation_start_method: str = defaults.TRAIN_PREPARATION_START_METHOD,
    preparation_directory=None,
    missing_reward_policy=defaults.TRAIN_MISSING_REWARD_POLICY,
) -> SlimeBatch:
    """Read producer-owned training records; do not replay their audit history."""
    from .slime_rollout import load_recorded_batch

    for endpoint in verifier.endpoints.values():
        require_sampling_profile(endpoint.parameters)
    return load_recorded_batch(reader, head, approved_manifest=approved_manifest,
        expected_plan=expected_plan, recipe=recipe, first_rollout=first_rollout,
        first_sample=first_sample, missing_reward_policy=missing_reward_policy, workers=preparation_workers,
        start_method=preparation_start_method, directory=preparation_directory)


def from_prepared_report(prepared, *, verifier, recipe, first_rollout=0, first_sample=0,
                         expected_plan=None, preparation_workers=defaults.TRAIN_PREPARATION_WORKERS,
                         preparation_start_method=defaults.TRAIN_PREPARATION_START_METHOD,
                         preparation_directory=None,
                         missing_reward_policy=defaults.TRAIN_MISSING_REWARD_POLICY) -> SlimeBatch:
    """Consume the producer's prepared objects without reading their records again."""
    if prepared.reader is not None:
        from .slime_rollout import pack_report

        for endpoint in verifier.endpoints.values():
            require_sampling_profile(endpoint.parameters)
        return pack_report(prepared.summary, prepared.reader, recipe=recipe, expected_plan=expected_plan,
            first_rollout=first_rollout, first_sample=first_sample, workers=preparation_workers,
            start_method=preparation_start_method, directory=preparation_directory, captures=prepared.captures,
            missing_reward_policy=missing_reward_policy)
    report, plan = prepared.summary, prepared.plan
    if expected_plan is not None and report["plan"] != expected_plan.as_dict():
        raise RecordError("slime-sampling-mismatch", "scored candidate groups differ from the requested update")
    candidates, caches = [], {}
    chosen = selected_indices(report)
    selected, skipped = select_scored_groups([report["candidates"][i] for i in chosen], missing_reward_policy)
    selected = tuple(chosen[i] for i in selected)
    for i in selected:
        examples = prepared.examples[i]
        for turn in examples.eligible:
            endpoint = verifier.endpoints[turn.capture["endpoint"]]
            require_sampling_profile(endpoint.parameters)
            cache = turn.capture["prefix_cache"]
            if cache is not None:
                caches[(examples.candidate.result_ref, turn.turn)] = cache
        candidates.append(examples)
    batch = prepare_batch(tuple(candidates), group_ids=tuple(plan.group_ids[i] for i in selected), recipe=recipe,
                          first_rollout=first_rollout, first_sample=first_sample)
    for payload in batch.payloads:
        metadata = payload["metadata"]
        measured = [caches[(BlobRef.from_dict(metadata["result"]), span["turn"])]
                    for span in response_spans(payload)
                    if (BlobRef.from_dict(metadata["result"]), span["turn"]) in caches]
        if measured:
            metadata["behave_prefix_cache"] = {
                name: sum(item[name] for item in measured)
                for name in ("cached_tokens", "total_prompt_tokens")}
            metadata["behave_cache_measured_turns"] = len(measured)
    if report.get("group_selection") is not None:
        # prepare_batch numbers its input tuple. Preserve original launch IDs
        # in the export when only a subset of the planned groups is trained.
        for payload in batch.payloads:
            payload["rollout_id"] = first_rollout + selected[payload["rollout_id"] - first_rollout]
        batch = replace(batch, candidates=tuple({**row, "rollout_id": first_rollout + selected[i]}
                                               for i, row in enumerate(batch.candidates)))
    batch = admitted_batch(batch, policy=missing_reward_policy, planned=len(chosen), skipped=skipped)
    return replace(batch, group_selection=report.get("group_selection"))


def prepare_batch(
    candidates: tuple[RolloutExamples, ...], *, group_ids: tuple[str | None, ...],
    recipe: SlimeRecipe, first_rollout: int = 0, first_sample: int = 0,
    missing_reward_policy=defaults.TRAIN_MISSING_REWARD_POLICY,
) -> SlimeBatch:
    """Consume a complete planned batch, not just candidates with trainable turns.

    Unresolved judgments contribute zero without removing any siblings. Runtime
    may omit groups with missing rollout records, not merely missing scores.
    Membership authorization remains TrainingPlan's.
    """
    if type(recipe) is not SlimeRecipe or not candidates or len(candidates) != len(group_ids):
        raise ValueError("complete candidates, group assignments and recipe are required")
    for value in (first_rollout, first_sample):
        if type(value) is not int or value < 0:
            raise ValueError("Slime indices must be nonnegative integers")
    planned = len(candidates)
    if any(type(item) is not RolloutExamples for item in candidates):
        raise TypeError("Slime training requires verified rollout examples")
    admission_rows = [{"group_id": group, "task_id": item.candidate.result.task_id,
        "attempt_id": item.candidate.result.attempt_id,
        "examples": {"reward": item.reward, "result": item.candidate.result_ref.as_dict()}}
        for item, group in zip(candidates, group_ids, strict=True)]
    selected, skipped = select_scored_groups(admission_rows, missing_reward_policy)
    candidates, group_ids = tuple(candidates[i] for i in selected), tuple(group_ids[i] for i in selected)
    groups, identities, provenance = {}, set(), set()
    for i, (examples, group) in enumerate(zip(candidates, group_ids, strict=True)):
        if group is not None and (type(group) is not str or not group):
            raise ValueError("invalid group id")
        if recipe.algorithm in GROUPED and group is None:
            raise ValueError("group-relative algorithms require explicit candidate groups")
        result = examples.candidate.result
        identity = (result.release, result.task_id, result.attempt_id)
        if identity in identities:
            raise ValueError("duplicate rollout candidate")
        identities.add(identity)
        reward = examples.reward
        if type(reward) not in (int, float) or not math.isfinite(reward):
            raise ValueError("finite candidate reward required")
        if not examples.eligible and any(turn.status == "unavailable" for turn in examples.turns):
            raise RecordError("training-tokens-unavailable", "candidate has no usable native-token turns")
        provenance.add((examples.requirements.checkpoint, examples.requirements.tokenizer, examples.requirements.template))
        for turn in examples.eligible:
            if "old_log_probs" not in turn.tokens.signals:
                raise RecordError("training-signals-missing", "Slime RL requires native old-policy log probabilities")
        if group is not None:
            groups.setdefault(group, []).append(i)
    if len(provenance) != 1:
        raise ValueError("a synchronous Slime batch requires one policy/tokenizer/template")

    credit = candidate_credit([c.reward for c in candidates], group_ids, recipe)
    for indices in groups.values():
        if len({(candidates[i].candidate.result.task_id, candidates[i].candidate.result.release) for i in indices}) != 1:
            raise ValueError("candidate groups must refer to one task and release")

    payloads, rows = [], []
    group_indices = {group: i for i, group in enumerate(groups)}
    for i, examples in enumerate(candidates):
        rollout_id = first_rollout + selected[i]
        parts = sample_payloads(examples, rollout_id=rollout_id, first_index=first_sample + len(payloads),
                                group_index=group_indices.get(group_ids[i]))
        # GRPO has a tokenwise objective with one candidate-level advantage and
        # denominator. Coalescing preserves both. Do not silently change GSPO's
        # sequence-ratio boundary or another estimator's return calculation.
        if recipe.algorithm == "grpo":
            parts = coalesce_payloads(parts)
        for part in parts:
            part["metadata"]["behave_credit"] = {"recipe": recipe.as_dict(), "value": credit[i]}
            # Do not confuse a per-call ID with the stable candidate identity.
            part["session_id"] = examples.candidate.result.attempt_id
        payloads.extend(parts)
        rows.append({"rollout_id": rollout_id, "group_id": group_ids[i],
                     "result": examples.candidate.result_ref.as_dict(), "reward": examples.reward,
                     "credit": credit[i], "retained_turns": len(examples.eligible),
                     "training_segments": len(parts),
                     "excluded_turns": sum(t.status == "excluded" for t in examples.turns),
                     "unavailable_turns": sum(t.status == "unavailable" for t in examples.turns)})
    if not payloads:
        raise RecordError("training-no-eligible-turns", "retain the candidate report but do not run an empty optimizer update")
    return admitted_batch(SlimeBatch(tuple(payloads), tuple(rows), recipe),
                          policy=missing_reward_policy, planned=planned, skipped=skipped)


def candidate_credit(rewards, group_ids, recipe):
    """Compute the same candidate-level credit before any turn/segment fanout."""
    if len(rewards) != len(group_ids) or not rewards:
        raise ValueError("a complete candidate group assignment is required")
    groups = {}
    for i, (reward, group) in enumerate(zip(rewards, group_ids, strict=True)):
        if reward is not None and (type(reward) not in (int, float) or not math.isfinite(reward)):
            raise ValueError("finite candidate reward required")
        if recipe.algorithm in GROUPED and group is None:
            raise ValueError("group-relative algorithms require explicit candidate groups")
        if group is not None:
            groups.setdefault(group, []).append(i)
    rewards = [final_score(reward) for reward in rewards]
    credit = list(rewards)
    for indices in groups.values():
        if recipe.algorithm not in GROUPED:
            continue
        if len(indices) < 2:
            raise ValueError("group-relative credit requires at least two candidates")
        values = [float(rewards[i]) for i in indices]
        mean = statistics.fmean(values)
        scale = (statistics.stdev(values) + 1e-6
                 if recipe.group_std and recipe.algorithm != "reinforce_plus_plus_baseline" else 1.0)
        for i in indices:
            credit[i] = (float(rewards[i]) - mean) / scale
    return credit


def post_process_rewards(args, samples) -> tuple[list[float], list[float]]:
    """Slime's ``--custom-reward-post-process-path``; no re-grouping by turn count."""
    recipe = None
    admission = (samples[0].metadata or {}).get("behave_batch_admission") if samples else None
    raw, credit, seen = [], [], {}
    for sample in samples:
        if (sample.metadata or {}).get("behave_batch_admission") != admission:
            raise ValueError("training samples disagree about group admission")
        record = (sample.metadata or {}).get("behave_credit")
        if type(record) is not dict or set(record) != {"recipe", "value"}:
            raise ValueError("Slime sample lacks BEHAVE candidate credit")
        selected = SlimeRecipe.from_dict(record["recipe"])
        if recipe is None:
            recipe = selected
            recipe.validate_args(args)
        elif recipe != selected:
            raise ValueError("mixed Slime recipes")
        value = record["value"]
        reward = sample.get_reward_value(args)
        if (type(value) not in (int, float) or not math.isfinite(value)
                or type(reward) not in (int, float) or not math.isfinite(reward)
                or type(sample.rollout_id) is not int or sample.rollout_id < 0):
            raise ValueError("invalid Slime credit or rollout identity")
        identity = (sample.group_index, reward, value, sample.metadata["result"])
        if sample.rollout_id in seen and seen[sample.rollout_id] != identity:
            raise ValueError("sibling turns disagree about their candidate credit")
        seen[sample.rollout_id] = identity
        raw.append(float(reward))
        credit.append(float(value))
    if not raw:
        raise ValueError("empty Slime training batch")
    step_size = getattr(args, "global_batch_size", None)
    if admission is not None:
        # The Task entry requests one optimizer update per scored round. The
        # native DP schedule and loss both receive this actual admitted count.
        # Keep the planned size separately so the next complete round restores it.
        planned = getattr(args, "behave_planned_batch_size", step_size)
        if planned != admission["planned_candidates"]:
            raise ValueError("skip_group requires one optimizer update per planned candidate batch")
        if len(seen) != admission["selected_candidates"]:
            raise ValueError("admitted candidates without loss tokens require a separate batch policy")
        args.behave_planned_batch_size = planned
        args.global_batch_size = step_size = len(seen)
    # The pinned upstream scheduler drops trailing rollout IDs. Never allow a
    # turn exclusion to turn into silent loss of another candidate's exposure.
    if type(step_size) is not int or step_size <= 0 or len(seen) % step_size:
        raise ValueError("Slime global_batch_size must divide the retained candidate count; trailing candidates must not drop")
    return raw, credit


def _binding(head, manifest, plan, recipe):
    return {"schema": "behave.slime-prepared-batch/1", "head": head.as_dict(),
            "manifest": manifest.as_dict(), "plan": plan.as_dict(), "recipe": recipe.as_dict()}


def save_batch(path, batch, *, head, manifest, plan):
    """Write a completed producer batch to a new, private path."""
    value = {"binding": _binding(head, manifest, plan, batch.recipe),
             "candidates": batch.candidates, "payloads": batch.payloads, "skipped_groups": batch.skipped_groups,
             "group_selection": batch.group_selection}
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def load_batch(path, *, head, manifest, plan, recipe):
    """Load native payloads from the control producer, not from an agent path."""
    value = json.loads(Path(path).read_bytes())
    if value["binding"] != _binding(head, manifest, plan, recipe):
        raise ValueError("prepared batch belongs to another report, manifest, plan or recipe")
    return SlimeBatch(tuple(value["payloads"]), tuple(value["candidates"]), recipe,
                      tuple(value.get("skipped_groups", ())), value.get("group_selection"))


def prepare_recorded_batches(plan_directory):
    """Prepare legacy receipts on CPU before starting the native GPU application.

One distinct receipt is prepared once even if the operator requests several
offline updates on it. Subsequent updates load the same batch and old-policy
probabilities. This does not imply fresh rollouts or measured policy accuracy.
"""
    from behave.agent.providers.local_chat import LocalChatEndpoint

    from .slime_rollout import read_binding, round_plan

    directory = Path(plan_directory)
    path = directory / "binding.json"
    binding = json.loads(path.read_bytes())
    if "recorded_rounds" not in binding or "prepared_batches" in binding:
        return []
    binding = read_binding(path)
    reader = DirectoryStore(binding["objects"])
    recipe = SlimeRecipe.from_dict(binding["recipe"])
    manifest = BlobRef.from_dict(binding["manifest"])
    prepared, timings = {}, []
    for image in binding["recorded_rounds"]:
        ref = BlobRef.from_dict(image)
        if ref.sha256 in prepared:
            continue
        began = time.monotonic()
        row = parse_json(read_verified(reader, ref))
        if row["manifest"] != binding["manifest"]:
            raise ValueError("recorded round differs from the selected training manifest")
        endpoint = LocalChatEndpoint.from_dict(row["endpoint"])
        plan = round_plan(binding, reader, round_id=row["round"], endpoint=endpoint,
                          policy=BlobRef.from_dict(row["policy"]), task_selection=row.get("task_selection"))
        identity = BlobRef.of(plan.to_bytes())
        batch = from_training_report(DirectoryStore(row["objects"]), BlobRef.from_dict(row["head"]),
            approved_manifest=manifest, verifier=NativeChatVerifier(endpoint), recipe=recipe,
            expected_plan=identity,
            preparation_workers=binding.get("preparation_workers", defaults.TRAIN_PREPARATION_WORKERS),
            preparation_start_method=binding.get("preparation_start_method", defaults.TRAIN_PREPARATION_START_METHOD),
            missing_reward_policy=binding.get("missing_reward_policy", defaults.TRAIN_MISSING_REWARD_POLICY),
            preparation_directory=directory / "preparation")
        target = directory / "prepared" / f"{ref.sha256}.json"
        save_batch(target, batch, head=BlobRef.from_dict(row["head"]), manifest=manifest, plan=identity)
        prepared[ref.sha256] = str(target.resolve())
        timings.append({"receipt": ref.as_dict(), "seconds": time.monotonic() - began,
                        "candidates": len(batch.candidates), "segments": len(batch.payloads),
                        "skipped_groups": batch.skipped_groups})
    binding["prepared_batches"] = prepared
    path.write_bytes(canonical_json(binding))
    return timings


def read_report(reader, head):
    """Read the completed producer export, not every event/artifact leading to it."""
    terminal = json.loads(reader.read(head))
    if terminal["kind"] != "training_finished":
        raise RecordError("training-report-incomplete", "a completed training report is required")
    return json.loads(reader.read(BlobRef.from_dict(terminal["payload"]["result"])))


def _write(path, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    with Path(path).open("xb") as stream:
        stream.write(data)


def _pack(path):
    began = time.monotonic()
    with Path(path).open() as stream:
        job = json.load(stream)
    row, result = job["row"], job["result"]
    saved = row["examples"]
    requirements = TrainingRequirements.from_bytes(canonical_json(saved["requirements"]))
    turns, caches = [], {}
    for turn in saved["turns"]:
        tokens = None
        if turn["status"] == "eligible":
            data = turn.get("tokens")
            reply = None
            if data is None:
                with Path(job["token_sources"][str(turn["turn"])]).open() as stream:
                    reply = json.load(stream)
                    data = reply["token_data"]
            tokens = TokenTrace.from_dict(data)
            if tokens.request.as_dict() != turn["request"]:
                raise ValueError("token record belongs to another model request")
            if any(getattr(tokens, name) != getattr(requirements, name)
                   for name in ("adapter", "checkpoint", "tokenizer", "template")):
                raise ValueError("candidate tokens belong to another generating policy")
            if "old_log_probs" not in tokens.signals:
                raise RecordError("training-signals-missing", "native old-policy log probabilities are required")
            capture = turn.get("capture")
            if capture and capture.get("prefix_cache") is not None:
                caches[turn["turn"]] = capture["prefix_cache"]
            elif reply is not None:
                cache = prefix_cache_usage({"usage": reply.get("usage", {}).get("raw")}, len(tokens.input_ids))
                if cache is not None:
                    caches[turn["turn"]] = cache
        turns.append(TurnExample(turn["turn"], BlobRef.from_dict(turn["request"]),
            BlobRef.from_dict(turn["response"]), turn["status"], turn["reason"], tokens))
    candidate = TrainingCandidate(BlobRef.from_dict(saved["result"]), TaskResult.from_bytes(canonical_json(result)))
    examples = RolloutExamples(candidate, tuple(turns), requirements)
    if not examples.eligible and any(turn.status == "unavailable" for turn in turns):
        raise RecordError("training-tokens-unavailable", "candidate has no usable native-token turns")
    recipe = SlimeRecipe.from_dict(job["recipe"])
    parts = sample_payloads(examples, rollout_id=job["rollout_id"], first_index=0,
                            group_index=job["group_index"])
    if recipe.algorithm == "grpo":
        parts = coalesce_payloads(parts)
    for part in parts:
        part["metadata"]["behave_credit"] = {"recipe": recipe.as_dict(), "value": job["credit"]}
        part["session_id"] = result["attempt_id"]
        measured = [caches[span["turn"]] for span in response_spans(part) if span["turn"] in caches]
        if measured:
            part["metadata"]["behave_prefix_cache"] = {
                key: sum(item[key] for item in measured) for key in ("cached_tokens", "total_prompt_tokens")}
            part["metadata"]["behave_cache_measured_turns"] = len(measured)
    summary = {"rollout_id": job["rollout_id"], "group_id": row["group_id"],
        "result": saved["result"], "reward": saved["reward"], "credit": job["credit"],
        "retained_turns": len(examples.eligible), "training_segments": len(parts),
        "excluded_turns": sum(turn.status == "excluded" for turn in turns),
        "unavailable_turns": sum(turn.status == "unavailable" for turn in turns)}
    target = str(Path(path).with_suffix(".packed.json"))
    _write(target, {"payloads": parts, "candidate": summary})
    return {"path": target, "pid": os.getpid(), "seconds": time.monotonic() - began,
            "segments": len(parts), "loss_tokens": sum(sum(p["loss_mask"]) for p in parts)}


def pack_report(report, reader, *, recipe, expected_plan=None, first_rollout=0, first_sample=0,
                workers=defaults.TRAIN_PREPARATION_WORKERS,
                start_method=defaults.TRAIN_PREPARATION_START_METHOD, directory=None, captures=None,
                missing_reward_policy=defaults.TRAIN_MISSING_REWARD_POLICY):
    """Keep whole groups and order; assign credit once, before parallel packing."""
    began = time.monotonic()
    if expected_plan is not None and report["plan"] != expected_plan.as_dict():
        raise RecordError("slime-sampling-mismatch", "candidate groups differ from the requested update")
    # Settle before group admission or credit. Old exports may still contain
    # the raw null score; never omit that candidate or its siblings for it.
    normalized, results = [], {}
    chosen = selected_indices(report)
    for index in chosen:
        row = report["candidates"][index]
        saved = row["examples"]
        if saved is not None:
            result = json.loads(reader.read(BlobRef.from_dict(saved["result"])))
            reward = final_score_from_record(result)
            if (result["task_id"], result["attempt_id"]) != (row["task_id"], row["attempt_id"]) \
                    or saved["reward"] not in (result["score"], reward):
                raise ValueError("training row and recorded result differ")
            results[saved["result"]["sha256"]] = result
            row = {**row, "examples": {**saved, "reward": reward}}
        normalized.append(row)
    selected, skipped = select_scored_groups(normalized, missing_reward_policy)
    rows = [normalized[i] for i in selected]
    selected = tuple(chosen[i] for i in selected)
    groups = [row["group_id"] for row in rows]
    rewards = [row["examples"]["reward"] for row in rows]
    credit = candidate_credit(rewards, groups, recipe)
    group_indices = {group: i for i, group in enumerate(dict.fromkeys(g for g in groups if g is not None))}
    identities, group_tasks, provenance = set(), {}, set()
    for row in rows:
        saved = row["examples"]
        req = saved["requirements"]
        provenance.add(tuple(req[name]["sha256"] for name in ("checkpoint", "tokenizer", "template")))
        identity = (row["task_id"], row["attempt_id"])
        if identity in identities:
            raise ValueError("duplicate rollout candidate")
        identities.add(identity)
        if row["group_id"] is not None:
            group_tasks.setdefault(row["group_id"], set()).add(row["task_id"])
    if len(provenance) != 1 or any(len(tasks) != 1 for tasks in group_tasks.values()):
        raise ValueError("candidate groups require one task, and the batch requires one policy")
    if directory is not None:
        Path(directory).mkdir(parents=True, exist_ok=True)
    workspace = Path(mkdtemp(prefix="training-prepare-", dir=directory))
    _write(workspace / "admission.json", {"policy": missing_reward_policy,
        "planned_candidates": len(chosen), "selected_indices": selected, "skipped_groups": skipped,
        "group_selection": report.get("group_selection")})
    paths = []
    for i, row in enumerate(rows):
        if captures:
            saved = row["examples"]
            row = {**row, "examples": {**saved, "turns": [dict(turn, capture=captures.get(
                (saved["result"]["sha256"], turn["turn"]))) for turn in saved["turns"]]}}
        result = results[row["examples"]["result"]["sha256"]]
        token_sources = {}
        for turn in row["examples"]["turns"]:
            if turn["status"] != "eligible" or turn.get("tokens") is not None:
                continue
            response = BlobRef.from_dict(turn["response"])
            if isinstance(reader, DirectoryReader):
                source = reader.directory / response.sha256
            else:
                # Generic readers need one byte copy, not serial JSON parsing
                # and re-encoding of every cumulative token history.
                source = workspace / response.sha256
                if not source.exists():
                    with source.open("xb") as stream:
                        stream.write(reader.read(response))
            token_sources[str(turn["turn"])] = str(source)
        path = workspace / f"candidate_{i:06d}.json"
        _write(path, {"row": row, "result": result, "recipe": recipe.as_dict(),
            "rollout_id": first_rollout + selected[i], "group_index": group_indices.get(groups[i]), "credit": credit[i],
            "token_sources": token_sources})
        paths.append(str(path))
    staged = time.monotonic()
    if workers == 1:
        completed = [_pack(path) for path in paths]
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context(start_method)) as pool:
            completed = list(pool.map(_pack, paths))
    packed = time.monotonic()
    payloads, candidates = [], []
    for item in completed:
        with Path(item["path"]).open() as stream:
            result = json.load(stream)
        for part in result["payloads"]:
            part["index"] = first_sample + len(payloads)
            payloads.append(part)
        candidates.append(result["candidate"])
    _write(workspace / "timing.json", {"workers": workers, "start_method": start_method,
        "staging_seconds": staged - began, "packing_seconds": packed - staged,
        "assembly_seconds": time.monotonic() - packed, "candidates": completed})
    if not payloads:
        raise RecordError("training-no-eligible-turns", "do not run an empty optimizer update")
    batch = admitted_batch(SlimeBatch(tuple(payloads), tuple(candidates), recipe),
                           policy=missing_reward_policy, planned=len(chosen), skipped=skipped)
    return replace(batch, group_selection=report.get("group_selection"))


def load_recorded_batch(reader, head, *, approved_manifest, expected_plan, recipe,
                        first_rollout, first_sample, workers, start_method, directory,
                        missing_reward_policy=defaults.TRAIN_MISSING_REWARD_POLICY):
    report = read_report(reader, head)
    # Check the selected training/evaluation split once. No chain walk or source
    # material revalidation is needed to read the producer's own token export.
    plan = json.loads(reader.read(BlobRef.from_dict(report["plan"])))
    if plan["manifest"] != approved_manifest.as_dict():
        raise RecordError("training-task-forbidden", "training manifest differs from the operator-approved split")
    if [row["group_id"] for row in report["candidates"]] != plan["group_ids"]:
        raise ValueError("training groups differ from the selected plan")
    return pack_report(report, reader, recipe=recipe, expected_plan=expected_plan,
        first_rollout=first_rollout, first_sample=first_sample, workers=workers,
        start_method=start_method, directory=directory, missing_reward_policy=missing_reward_policy)
