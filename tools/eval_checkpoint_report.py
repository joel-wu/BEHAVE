"""Read-only paired checkpoint results; never run, retry or rescore candidates."""

import argparse
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from behave.eval.report import artifact_views, report_rows
from behave.runtime.records import write_json
from behave.task_execution.judgment import final_score
from behave.task_execution.solver_settings import submission_components


def arm_records(directory, tasks, setting):
    cases = {row["task_id"]: row for row in report_rows(directory / "run", directory / "report.json").values()}
    requested = tuple(artifact for artifact in ("rtl", "behavior") if artifact in submission_components(setting))
    rows = []
    for task in tasks:
        case = cases.get(task)
        value, result_path = None, None
        if case is not None and case["result"] is not None:
            name = case["result"]["sha256"]
            result_path = directory / "run/candidates" / case["run_key"] / "objects" / name
            if not result_path.is_file():
                result_path = directory / "run/objects" / name
            # Read only the small outcome, not token payloads or evidence closure.
            value = json.loads(result_path.read_bytes())
        views = artifact_views(value, setting)
        if case is not None and value is None:
            for artifact in requested:
                views[artifact] = {"status": "rollout_unavailable", "score": None, "code": case["error"]}
        settled = case is not None and case["status"] != "pending_judgment"
        scores = {artifact: (final_score(views[artifact]["score"]) if settled else None)
                  for artifact in requested}
        rows.append({"task": task, "status": "pending" if case is None else case["status"],
            "settled": settled, **views,
            "rtl_final_score": scores["rtl"], "behavior_final_score": scores.get("behavior"),
            "joint_final_reward": (final_score(*scores.values()) if settled else None),
            "failure_code": (case.get("error") if value is None and case is not None
                             else None if value is None else value.get("failure_code")),
            "result_path": None if result_path is None else str(result_path),
            "trace": str(directory / "traces" / task / "trace.md")})
    return rows


def comparison(root):
    experiment = json.loads((root / "experiment.json").read_bytes())
    configuration = json.loads((root / "configuration.json").read_bytes())
    tasks = experiment["tasks"]
    records, summaries = {}, {}
    for arm in experiment["arms"]:
        directory = root / arm
        rows = records[arm] = arm_records(directory, tasks, configuration["setting"])
        runtime = json.loads((directory / "runtime.json").read_bytes()) if (directory / "runtime.json").is_file() else {}
        settled = sum(row["settled"] for row in rows)
        complete = runtime.get("state") == "completed" and settled == len(tasks)
        passed = sum(row["rtl_final_score"] == 1 for row in rows)
        summaries[arm] = {"state": runtime.get("state", "not_started"), "complete": complete,
            "denominator": len(tasks), "settled": settled, "pending": len(tasks) - settled,
            "statuses": dict(Counter(row["status"] for row in rows)), "rtl_passes": passed,
            "rtl_accuracy": passed / len(tasks) if complete else None,
            "joint_mean_reward": (sum(row["joint_final_reward"] for row in rows) / len(tasks)
                                  if complete else None)}
    pairs = Counter()
    for baseline, trained in zip(records["baseline"], records["trained"], strict=True):
        if not baseline["settled"] or not trained["settled"]:
            pairs["pending"] += 1
        else:
            left, right = baseline["rtl_final_score"] == 1, trained["rtl_final_score"] == 1
            pairs["both_pass" if left and right else "baseline_only" if left else
                  "trained_only" if right else "neither_pass"] += 1
    return {"observed_at": datetime.now(UTC).isoformat(), "root": str(root),
        "completed_training_updates": experiment["updates"], "tasks": tasks,
        "complete": all(row["complete"] for row in summaries.values()),
        "scoring": "Recorded terminal unresolved scores and missing submissions settle to zero; "
                   "uncompleted attempts remain pending. RTL accuracy uses every requested task.",
        "arms": summaries, "paired_rtl": dict(pairs), "records": records}


def export(root, output):
    report = comparison(root)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "report.json", report)
    fields = ("arm", "task", "status", "rtl_final_score", "behavior_final_score", "joint_final_reward",
              "rtl_status", "behavior_status", "failure_code", "result_path", "trace")
    with (output / "cases.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for arm, rows in report["records"].items():
            for row in rows:
                flat = {**row, "arm": arm, "rtl_status": row["rtl"]["status"],
                        "behavior_status": row["behavior"]["status"]}
                writer.writerow({key: flat[key] for key in fields})
    lines = ["# Baseline / checkpoint comparison", "",
        f"Checkpoint: {report['completed_training_updates']} completed optimizer updates.", "",
        f"Observed: {report['observed_at']}. Complete: {report['complete']}.", "", report["scoring"], "",
        "Incomplete arms do not receive a final accuracy or mean reward.", "",
        "| Arm | Settled / requested | RTL passes | Final RTL accuracy | Final Joint mean reward |",
        "| --- | ---: | ---: | ---: | ---: |"]
    for arm, row in report["arms"].items():
        accuracy = "pending" if row["rtl_accuracy"] is None else f"{100 * row['rtl_accuracy']:.1f}%"
        reward = "pending" if row["joint_mean_reward"] is None else f"{row['joint_mean_reward']:.4f}"
        lines.append(f"| {arm} | {row['settled']} / {row['denominator']} | {row['rtl_passes']} | {accuracy} | {reward} |")
    lines += ["", "Paired RTL outcomes (pending pairs are not treated as failures):", ""]
    lines += [f"- {name}: {count}" for name, count in report["paired_rtl"].items()]
    lines += ["", "See cases.csv for raw dispositions, settled component scores and original result/trace paths.",
              "This projection makes no model call and does not change historical results.", ""]
    (output / "README.md").write_text("\n".join(lines))
    return {key: value for key, value in report.items() if key not in {"records", "tasks"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    print(json.dumps(export(root, args.output or root / "comparison"), indent=2))


if __name__ == "__main__":
    main()
