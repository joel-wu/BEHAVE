"""Export observed per-round/per-task learning and timing CSVs from a run.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from statistics import fmean, pstdev

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json
from behave.task_execution.batch import BatchReport, load_batch_report

from .plan import TrainingPlan, verify_chunks
from .slime_checkpoint import native_phase_timings, native_training_metrics
from .training_data import RolloutExamples, TokenVerifier, prepare_examples


def candidate_groups(candidates):
    groups = {"": candidates}
    for task in sorted({row["task"] for row in candidates}):
        groups[task] = [row for row in candidates if row["task"] == task]
    return groups.items()


def credit_usage(record):
    """Account for zero-credit work without filtering candidates or rewards."""
    candidates = record["candidates"]
    if not candidates or any("credit" not in row for row in candidates):
        return []
    result = []
    for task, rows in candidate_groups(candidates):
        zero = [row for row in rows if row["credit"] == 0]
        usage = {"round": record["round"], "task": task, "candidates": len(rows),
                 "zero_credit_candidates": len(zero)}
        for name in ("training_tokens", "loss_tokens"):
            measured = all(name in row for row in rows)
            usage[name] = sum(row[name] for row in rows) if measured else None
            usage[f"zero_credit_{name}"] = sum(row[name] for row in zero) if measured else None
        result.append(usage)
    return result


def token_availability(record):
    """Export compact producer counters, leaving unmeasured history unknown."""
    if not record["candidates"]:
        return []
    result = []
    for task, rows in candidate_groups(record["candidates"]):
        row = {"round": record["round"], "task": task, "candidates": len(rows)}
        for field in ("retained_turns", "excluded_turns", "unavailable_turns"):
            row[field] = (sum(item[field] for item in rows)
                          if all(field in item for item in rows) else None)
        row["candidates_with_training_tokens"] = (
            sum(item["retained_turns"] > 0 for item in rows)
            if row["retained_turns"] is not None else None)
        row["partially_available_candidates"] = (
            sum(item["retained_turns"] > 0 and item["unavailable_turns"] > 0 for item in rows)
            if row["retained_turns"] is not None and row["unavailable_turns"] is not None else None)
        result.append(row)
    return result


def sampling_summary(record):
    """Keep recorded selection/replacement evidence separate from accuracy."""
    selection = record.get("group_selection")
    if selection is None:
        return []
    return [{"round": record["round"], "strategy": selection["strategy"],
        "launched_groups": selection["launched_groups"],
        "launched_candidates": selection["launched_candidates"],
        "selected_groups": len(selection["selected_groups"]),
        "selected_candidates": len(selection["selected_indices"]),
        "selected_task_ids": json.dumps(selection["selected_groups"]),
        "recorded_completion_order": json.dumps(selection["completion_order"]),
        **({"planned_groups": selection["planned_groups"],
            "planned_candidates": selection["planned_candidates"],
            "constant_groups": sum(g["status"] == "constant" for g in selection["observations"]),
            "unavailable_groups": sum(g["status"] == "unavailable" for g in selection["observations"])}
           if "observations" in selection else {}),
        "replacement_attempts": record.get("replacement_attempts"),
        "replaced_slots": record.get("replaced_slots")}]


def group_outcomes(record):
    """Keep filtered complete-group rewards visible; do not call them RTL accuracy."""
    selection = record.get("group_selection") or {}
    return [{"round": record["round"], "group": group["group_id"], "status": group["status"],
        "candidates": len(group["members"]), "rewards": json.dumps(group["rewards"]),
        "mean_reward": (fmean(group["rewards"]) if group["rewards"]
                        and all(r is not None for r in group["rewards"]) else None)}
        for group in selection.get("observations", ())]


def paired_rounds(records):
    """Compare shared tasks, rather than a changing completion-order mixture."""
    pairs = []
    for before, after in pairwise(records):
        common = sorted(before["tasks"].keys() & after["tasks"].keys())
        if not common:
            continue
        row = {"from_round": before["round"], "to_round": after["round"],
            "common_tasks": json.dumps(common), "common_task_count": len(common)}
        for label, record in (("before", before), ("after", after)):
            row[f"candidates_{label}"] = sum(record["tasks"][task]["candidates"] for task in common)
            row[f"execution_failures_{label}"] = sum(bool(candidate["failure_code"])
                for candidate in record["candidates"] if candidate["task"] in common)
            for metric in ("rtl_accuracy", "behavior_accuracy", "mean_reward"):
                row[f"{metric}_{label}"] = fmean(record["tasks"][task][metric] for task in common)
        for metric in ("rtl_accuracy", "behavior_accuracy", "mean_reward"):
            row[f"{metric}_delta"] = row[f"{metric}_after"] - row[f"{metric}_before"]
        pairs.append(row)
    return pairs


def export(run_directory, output):
    root, output = Path(run_directory), Path(output)
    configuration = json.loads((root / "configuration.json").read_text())
    layout = configuration["layout"]
    expected_ranks = layout["actor_nodes"] * layout["actor_gpus_per_node"]
    metrics = [json.loads(path.read_text()) for path in
               sorted((root / "artifacts/plan/rounds").glob("round_*/metrics.json"))]
    intervals = {}
    for before, after in pairwise(metrics):
        if after["round"] == before["round"] + 1:
            next_start = datetime.fromisoformat(after["started_at"])
            intervals[before["round"]] = {
                "cycle_seconds": (next_start - datetime.fromisoformat(before["started_at"])).total_seconds(),
                "producer_end_to_next_start_seconds":
                    (next_start - datetime.fromisoformat(before["finished_at"])).total_seconds()}
    updates = {}
    for path in (root / "artifacts/timing").glob("rank_*.jsonl"):
        with path.open() as stream:
            for line in stream:
                row = json.loads(line)
                updates.setdefault(row["rollout"], {}).setdefault(row["rank"], {})[row["event"]] = row
    rows, tasks, failures = [], [], []
    native_phases, optimization = [], []
    for stage in sorted((root / "artifacts/plan/learning").glob("*")):
        seen, seen_optimization = set(), set()
        for name in ("stderr", "stdout"):
            path = stage / name
            if not path.is_file():
                continue
            # Native records are line-oriented; retain measurements, not the
            # whole stdout/stderr stream (which also contains serving logs).
            with path.open() as stream:
                for line in stream:
                    for phase in native_phase_timings(line):
                        key = (phase["logged_at"], phase["phase"], phase["seconds"])
                        if key not in seen:
                            native_phases.append({"process_stage": stage.name, **phase})
                            seen.add(key)
                    for record in native_training_metrics(line):
                        key = json.dumps(record, sort_keys=True)
                        if key not in seen_optimization:
                            optimization.append({"process_stage": stage.name, **record})
                            seen_optimization.add(key)
    native_phases.sort(key=lambda row: row["logged_at"])
    optimization.sort(key=lambda row: row["logged_at"])
    for record in metrics:
        failure_counts = Counter(candidate["failure_code"] for candidate in record["candidates"]
                                 if candidate["failure_code"])
        row = {"round": record["round"], **record["selected"],
            "execution_failures": sum(failure_counts.values()),
            **{key: record[key] for key in ("started_at", "finished_at", "producer_seconds",
                "preparation_seconds", "training_tokens", "loss_tokens", "training_segments",
                "replacement_attempts", "zero_variance_groups")}}
        row.update(intervals.get(record["round"], {
            "cycle_seconds": None, "producer_end_to_next_start_seconds": None}))
        phases = {"forward_backward_seconds": ("forward_backward_started", "forward_backward_finished"),
                  "optimizer_seconds": ("optimizer_started", "optimizer_finished"),
                  "update_seconds": ("train_step_started", "optimizer_finished")}
        ranks = updates.get(record["round"], {})
        for name, (start, end) in phases.items():
            complete = [events[end]["monotonic_seconds"] - events[start]["monotonic_seconds"]
                        for events in ranks.values() if start in events and end in events]
            row[name] = max(complete) if len(complete) == len(ranks) == expected_ranks else None
        norms = [events["optimizer_finished"]["grad_norm"] for events in ranks.values()
                 if "optimizer_finished" in events]
        row["grad_norm"] = max(norms) if norms else None
        row["update_successful"] = (all(events["optimizer_finished"]["update_successful"]
            for events in ranks.values()) if len(ranks) == expected_ranks
            and all("optimizer_finished" in e for e in ranks.values()) else None)
        rows.append(row)
        tasks.extend({"round": record["round"], "task": task, **values,
                      "execution_failures": sum(bool(c["failure_code"]) for c in record["candidates"]
                                                if c["task"] == task)}
                     for task, values in record["tasks"].items())
        failures.extend({"round": record["round"], "failure_code": code, "candidates": count}
                        for code, count in sorted(failure_counts.items()))
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("rounds", rows), ("tasks", tasks), ("failures", failures),
                       ("comparisons", paired_rounds(metrics)), ("native_phases", native_phases),
                       ("optimization", optimization),
                       ("sampling", [row for record in metrics for row in sampling_summary(record)]),
                       ("group_outcomes", [row for record in metrics for row in group_outcomes(record)]),
                       ("signal", [row for record in metrics for row in credit_usage(record)]),
                       ("token_availability", [row for record in metrics for row in token_availability(record)])):
        if data:
            with (output / f"{name}.csv").open("w", newline="") as stream:
                # New measurements may add fields after a resumed run. Keep old
                # observations empty rather than fabricating historical values.
                columns = list(dict.fromkeys(key for row in data for key in row))
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(data)
    (output / "README.md").write_text(
        "# Training curves\n\n"
        "Accuracy/reward in round r are measured with the generating checkpoint, "
        "before update r. They are training-pool measurements, not held-out evaluation. "
        "Task groups may differ between rounds because of completion-order selection. "
        "Use tasks.csv for same-task comparisons and rounds.csv for throughput. "
        "comparisons.csv uses only tasks observed in both adjacent rounds, with "
        "equal task weights. It reports observed changes, not statistical proof "
        "of improvement or a change on the entire pool. "
        "cycle_seconds spans consecutive rollout starts. "
        "producer_end_to_next_start_seconds includes the update and subsequent "
        "handoff, checkpoint and weight-publication work, including any requested "
        "cold restart. These intervals remain blank until the next round is "
        "recorded; they are not estimates of unfinished work. "
        "Missing optimizer values mean the phase has not been observed complete. "
        "Transport replacement counts are separate from final zero rewards. "
        "Execution failures remain in the accuracy denominator and are also "
        "counted separately; failures.csv lists their recorded causes. "
        "native_phases.csv retains Slime's own exited rank-zero actor_train, "
        "save_model and update_weights timer measurements (0.1-second precision), "
        "with their native logged clock and initial/resume process stage. "
        "It does not assign startup weight publication to a preceding update. "
        "Timer scopes can exit through exceptions, so their end records alone "
        "do not prove successful updates or saves. These are named operation "
        "durations, not extra intervals to add to "
        "the overlapping per-rank update timers or proof of asynchronous-save completion. "
        "optimization.csv preserves native actor loss, clipping, entropy, PPO KL, "
        "rollout/train log-probability difference, gradient norm and learning rate "
        "when logged. These are optimization diagnostics, not accuracy. Native "
        "step numbers are retained rather than assumed to be rollout IDs. "
        "Exporting them adds no model forward or checkpoint reread.\n"
        "sampling.csv records launched/selected group and candidate counts, "
        "selected task IDs, recorded completion order, replacement attempts "
        "and replaced slots. These are producer receipts, not a reconstructed "
        "sampling schedule or inferred timestamps. They expose changes in the "
        "selected task mixture without changing any accuracy denominator.\n"
        "signal.csv counts candidates with exactly zero recorded credit and their "
        "training/loss tokens; a blank task means the entire selected batch. "
        "Historical token counts remain blank when unmeasured. These candidates "
        "remain in all original denominators and were not skipped by the trainer.\n"
        "token_availability.csv separates selected candidates with usable native "
        "tokens, unavailable turns, intentional exclusions and partially available "
        "candidates. It uses compact producer counters, not token-blob rereads. "
        "Missing historical counters stay blank rather than becoming zero.\n"
        + ("\nThis run includes a transport repair. See ../TRANSPORT_REPAIR.md "
           "before attributing an accuracy change to learning.\n"
           if (root / "TRANSPORT_REPAIR.md").exists() else ""))
    return {"rounds": len(rows), "task_rows": len(tasks), "output": str(output)}


def group_class(rewards):
    if any(value is None for value in rewards) or not rewards:
        return "unavailable"
    if len(set(rewards)) > 1:
        return "varying"
    return "full_reward" if rewards[0] == 1 else "other_constant"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(export(args.run, args.output), indent=2))
    return 0


def round_metrics(batch, reader, *, round_id, policy, started_at, finished_at,
                  preparation_seconds, producer_seconds, directory):
    sizes = defaultdict(lambda: {"training_tokens": 0, "loss_tokens": 0,
                                  "max_segment_tokens": 0})
    for payload in batch.payloads:
        size = sizes[payload["rollout_id"]]
        length = len(payload["tokens"])
        size["training_tokens"] += length
        size["loss_tokens"] += sum(payload["loss_mask"])
        size["max_segment_tokens"] = max(size["max_segment_tokens"], length)
    candidates = []
    for item in batch.candidates:
        result = json.loads(reader.read(BlobRef.from_dict(item["result"])))
        judgment = result["judgment"]
        def score(component):
            part = None if judgment is None else judgment.get(component)
            return 0. if part is None or part["score"] is None else float(part["score"])
        candidates.append({**item, **sizes[item["rollout_id"]],
            "task": result["task_id"], "attempt_id": result["attempt_id"],
            "rtl_score": score("rtl"), "behavior_score": score("behavior"),
            "submitted": result["submission"] is not None, "status": result["status"],
            "failure_code": result["failure_code"], "termination": result["termination"]})
    groups = defaultdict(list)
    for row in candidates:
        groups[row["task"]].append(row)

    def aggregate(rows):
        return {"candidates": len(rows),
            "rtl_accuracy": fmean(row["rtl_score"] == 1 for row in rows),
            "behavior_accuracy": fmean(row["behavior_score"] == 1 for row in rows),
            "mean_reward": fmean(row["reward"] for row in rows),
            "reward_std": pstdev(row["reward"] for row in rows),
            "submitted": sum(row["submitted"] for row in rows),
            "training_tokens": sum(row["training_tokens"] for row in rows),
            "loss_tokens": sum(row["loss_tokens"] for row in rows),
            "max_segment_tokens": max(row["max_segment_tokens"] for row in rows)}

    attempts = [json.loads(path.read_text()) for path in
                (Path(directory) / "execution/candidates").glob("*/attempt_*.json")]
    retries = [row for row in attempts if row["attempt"] > 0]
    return {"schema": "behave.learning-round-metrics/1", "round": round_id,
        "measurement": "fresh rollout before this round's optimizer update",
        "generating_policy": policy, "started_at": started_at, "finished_at": finished_at,
        "producer_seconds": producer_seconds, "preparation_seconds": preparation_seconds,
        "selected": aggregate(candidates),
        "tasks": {task: aggregate(rows) for task, rows in sorted(groups.items())},
        "zero_variance_groups": sum(pstdev(row["reward"] for row in rows) == 0 for rows in groups.values()),
        "candidates": candidates, "group_selection": batch.group_selection,
        "retained_turns": sum(row["retained_turns"] for row in candidates),
        "training_segments": len(batch.payloads),
        "training_tokens": sum(len(row["tokens"]) for row in batch.payloads),
        "loss_tokens": sum(sum(row["loss_mask"]) for row in batch.payloads),
        "max_sample_tokens": max(len(row["tokens"]) for row in batch.payloads),
        "replacement_attempts": len(retries),
        "replaced_slots": len({row["directory"].rsplit("/retry_", 1)[0] for row in retries}),
        "attempt_records": attempts}


@dataclass(frozen=True, slots=True)
class PreparedTrainingReport:
    """One in-process verification result, not a persisted token attestation."""

    summary: dict
    plan: TrainingPlan
    examples: tuple[RolloutExamples | None, ...]
    reader: ContentReader | None = None
    captures: dict | None = None


def summarize_rollouts(
    plan: TrainingPlan, reader: ContentReader, report: BatchReport, verifier: TokenVerifier | None = None
) -> dict:
    return prepare_rollouts(plan, reader, report, verifier).summary


def prepare_rollouts(plan, reader, report, verifier=None, *, accounting=None) -> PreparedTrainingReport:
    """Retain the prepared samples while building the ordinary audit summary."""
    rows, examples = [], []
    for row, candidate in _prepared_rows(plan, reader, report, verifier, accounting=accounting):
        rows.append(row)
        examples.append(candidate)
    return PreparedTrainingReport(_summary(plan, report, rows), plan, tuple(examples))


def _prepared_rows(
    plan: TrainingPlan, reader: ContentReader, report: BatchReport, verifier: TokenVerifier | None,
    *, accounting=None,
):
    if report.summary["plan"] != plan.execution.identity.as_dict():
        raise RecordError("training-execution-mismatch", "execution belongs to a different training plan")
    for index, row in enumerate(thaw_json(report.summary)["candidates"]):
        result_ref = None if row["result"] is None else BlobRef.from_dict(row["result"])
        examples = None if result_ref is None else prepare_examples(
            reader, result_ref, plan.requirements, verifier,
            accounting=None if accounting is None else accounting.pop(result_ref))
        yield {
            "index": index,
            "group_id": plan.group_ids[index],
            "task_id": row["task_id"],
            "attempt_id": row["attempt_id"],
            "error": row["error"],
            "examples": None if examples is None else examples.as_dict(),
        }, examples


def _summary(plan: TrainingPlan, report: BatchReport, rows: list[dict]) -> dict:
    return {
        "schema": "behave.training-rollouts/1",
        "plan": BlobRef.of(plan.to_bytes()).as_dict(),
        "execution_head": report.head.as_dict(),
        "candidates": rows,
        "requested": len(rows),
        "rewarded": sum(row["examples"] is not None for row in rows),
        "eligible_turns": sum(
            t["status"] == "eligible" for row in rows if row["examples"] for t in row["examples"]["turns"]
        ),
        "billing": thaw_json(report.summary["billing"]),
    }


def reference_tokens(summary: dict) -> dict:
    """Compact projection for exports whose response records already own tokens."""
    rows = []
    for row in summary["candidates"]:
        saved = row["examples"]
        if saved is not None:
            saved = {**saved, "schema": "behave.rollout-examples/2", "turns": [
                {key: value for key, value in turn.items() if key != "tokens"}
                for turn in saved["turns"]]}
        rows.append({**row, "examples": saved})
    return {**summary, "schema": "behave.training-rollouts/2", "candidates": rows}


def load_training_report(
    reader: ContentReader, head: BlobRef, *, approved_manifest: BlobRef, verifier: TokenVerifier | None = None
) -> dict:
    return prepare_training_report(reader, head, approved_manifest=approved_manifest, verifier=verifier).summary


def prepare_training_report(
    reader: ContentReader, head: BlobRef, *, approved_manifest: BlobRef, verifier: TokenVerifier | None = None
) -> PreparedTrainingReport:
    """Verify membership, rewards, groups, turns and fees without executing a task.

    A recorded verifier name is not evidence of token validity. Reading an export
    with validated tokens requires the same explicitly installed verifier again.
    No model/provider/optimizer is called by this reader.
    """
    events = load_chain(reader, head)
    if len(events) < 2 or events[0].kind != "training_started" or events[-1].kind != "training_finished":
        raise RecordError("training-report-incomplete", "a sealed training run is required")
    start_value = thaw_json(events[0].payload)
    start = fields(start_value, {"plan", "verifier"}
                   | ({"update_groups", "empty_rollout_retries", "active_groups", "selection_requires_tokens"}
                      & start_value.keys()))
    if start["verifier"] != (None if verifier is None else verifier.identity):
        raise RecordError("training-verifier-mismatch", "the recorded token verifier must be supplied explicitly")
    plan = TrainingPlan.from_bytes(read_verified(reader, BlobRef.from_dict(start["plan"])), reader)
    membership = plan.preflight(reader, approved_manifest=approved_manifest)
    verify_chunks(events[1:-1], membership)
    ref = BlobRef.from_dict(fields(thaw_json(events[-1].payload), {"result"})["result"])
    data = read_verified(reader, ref)
    row = fields(
        parse_json(data),
        {"schema", "plan", "execution_head", "candidates", "requested", "rewarded", "eligible_turns", "billing"}
        | ({"group_selection"} if "update_groups" in start else set()),
    )
    accounting = {}
    report = load_batch_report(reader, BlobRef.from_dict(row["execution_head"]), accounting=accounting)
    rows, examples = [], []
    for summary_row, candidate in _prepared_rows(plan, reader, report, verifier, accounting=accounting):
        rows.append(summary_row)
        examples.append(candidate)
    summary = _summary(plan, report, rows)
    if "update_groups" in start:
        from .pool import CompletedGroups, ReplenishingGroups

        selection = (ReplenishingGroups(plan.group_ids, start["update_groups"], start["active_groups"],
            requirements=plan.requirements.as_dict(), verifier=verifier)
            if "active_groups" in start else CompletedGroups(plan.group_ids, start["update_groups"]))
        for event in load_chain(reader, report.head):
            if event.kind == "evaluation_case":
                if "active_groups" in start:
                    selection.observe(event.payload["index"], thaw_json(event.payload["row"]), reader)
                    continue
                if start.get("selection_requires_tokens"):
                    example = examples[event.payload["index"]]
                    if example is None or not example.eligible:
                        continue
                elif start.get("empty_rollout_retries"):
                    from .pool import has_rollout
                    if not has_rollout(event.payload["row"], reader):
                        continue
                selection.complete(event.payload["index"])
        summary["group_selection"] = selection.summary()
    if row["schema"] == "behave.training-rollouts/2":
        summary = reference_tokens(summary)
    if canonical_json(summary) != data:
        # Earlier exports stored the raw nullable judge score. Validate those
        # original bytes too, but return the same final reward view as new runs.
        legacy_rows = [{**item, "examples": None if example is None else {
            **item["examples"], "reward": example.candidate.result.score}}
            for item, example in zip(rows, examples, strict=True)]
        legacy = {**summary, "candidates": legacy_rows, "rewarded": report.summary["scored"]}
        if row["schema"] == "behave.training-rollouts/2":
            legacy = reference_tokens(legacy)
        if canonical_json(legacy) != data:
            raise RecordError("training-report-mismatch", "training summary differs from its sealed execution evidence")
    return PreparedTrainingReport(summary, plan, tuple(examples))
