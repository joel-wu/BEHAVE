"""Evaluation view of sealed candidate results and their accounting.
"""

from __future__ import annotations

import json
import shlex
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from behave.core.artifacts import BlobRef, DirectoryReader, read_verified
from behave.core.records import thaw_json, version
from behave.data.published import ReleaseManifest
from behave.runtime.records import write_bytes, write_json
from behave.task_execution.batch import BatchReport as EvaluationReport
from behave.task_execution.batch import case_of, summarize
from behave.task_execution.batch import load_batch_report as load_evaluation_report
from behave.task_execution.judgment import final_score
from behave.task_execution.rejudge import judgment_run
from behave.task_execution.result import TaskResult
from behave.task_execution.run_store import RunStore
from behave.task_execution.solver_settings import submission_components
from behave.task_execution.tb_report import RTLCoverage

__all__ = ["EvaluationReport", "case_of", "component_progress", "export_review", "load_evaluation_report", "summarize"]


def component_progress(part):
    """Present a stored verdict; this function neither runs nor approximates Sim."""
    if part is None:
        return {"status": "not_requested", "score": None, "baseline": {"status": "not_requested"}}
    score, code, origin = part["score"], part["code"], part.get("origin")
    if code == "submission-component-missing":
        status = "not_submitted"
    elif score is not None:
        status = "passed" if score == 1 else "candidate_failed"
    elif origin in {"verifier", "reference"}:
        status = "sim_no_conclusion"
    elif origin in {"infrastructure", "resource-policy"}:
        status = "execution_no_conclusion"
    else:
        status = "unavailable"
    return {"status": status, "score": score, "code": code, "origin": origin,
            "reason": part.get("detail", {}).get("reason"),
            "baseline": part.get("detail", {}).get("baseline", {"status": "not_recorded"})}


def export_review(output, *, judgment=None):
    """Read an existing run, without dispatching a model, tool or judge.

    A separate review.md can be refreshed while the frozen launcher's original
    trace.md export is still running. Raw records are never rewritten.
    """
    from behave.agent.trace_export import export_candidate
    from behave.core.artifacts import BlobRef
    from behave.task_execution.rejudge import judgment_run
    from behave.task_execution.run_store import RunStore

    def record(store, row, destination, key):
        reference = row.get(key)
        if reference is None:
            return None
        value = json.loads(store.objects.read(BlobRef.from_dict(reference)))
        write_json(destination / f"{key}.json", value)
        return value

    def component(part):
        progress = component_progress(part)
        if progress["score"] is None:
            return progress["status"] + (f" ({part['code']})" if part is not None else "")
        return f"{progress['score']} ({progress['status']})"

    def baseline(part):
        progress = component_progress(part)["baseline"]
        if progress.get("generated") is None:
            return progress["status"]
        return f"{progress['status']} ({progress['completed']}/{progress['generated']})"

    output = Path(output)
    inventory = json.loads((output / "trace_index.json").read_text())
    batch_store = RunStore(output / "run")
    if not batch_store.directory.is_dir():
        raise FileNotFoundError(f"saved review run is missing: {batch_store.directory}")
    report_run = batch_store if judgment is None else judgment_run(batch_store, judgment)
    if not report_run.directory.is_dir():
        raise FileNotFoundError(f"recorded judgment is missing: {report_run.directory}")
    outcomes = {}
    for path in sorted((report_run.directory / "report").glob("*.json")):
        event = json.loads(path.read_text())
        if event["kind"] == "evaluation_case":
            row = event["payload"]["row"]
            outcomes[row["run_key"]] = row
    refresh = ["behave-eval", "export", "RUN_DIRECTORY"]
    if judgment is not None:
        refresh += ["--judgment", judgment]
    summary = ["# Eval interactive review\n",
               "Updated: " + datetime.now(UTC).isoformat() + "\n",
               f"Scoring pass: {judgment if judgment is not None else 'original'}.\n",
               ("Each trace includes the opening, assistant text, provider-returned reasoning summaries or thinking, "
                "shell commands, full stdout/stderr, errors and final submission. "
               "The adjacent JSON files retain complete cumulative requests, normalized responses and original "
               "provider payloads. Opaque provider continuation is retained there, not repeated in the readable trace.\n"),
               ("This index is a snapshot. Each readable trace links to its continuously updated original transcript. "
                f"Refresh with `{shlex.join(refresh)}`.\n"),
               ("Baseline completion is execution progress, not correctness. Candidate failure, "
                "Sim no conclusion, execution interruption and no submission are separate outcomes. "
                "Final statistics count unresolved requested scores as zero after any rejudging. "
                "`not_recorded` means this older record did not store baseline progress, not that it skipped baseline.\n"),
               "| Task | Solver | Turns | Tool calls | Overall | Behavior | RTL | Behavior baseline | RTL baseline | Final TB | RTL line coverage |",
               "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |"]
    for item in inventory:
        directory = output / "run/candidates" / item["run_key"]
        trace = Path(item.get("trace", f"traces/{item['task']}/trace.md"))
        destination = output / trace.parent
        progress = export_candidate(directory, destination, markdown="review.md")
        row = outcomes.get(item["run_key"])
        overall, behavior, rtl, tb, coverage = "pending", "—", "—", "—", "—"
        behavior_baseline = rtl_baseline = "—"
        if row:
            store = RunStore(directory) if judgment is None else batch_store
            result = record(store, row, destination, "result")
            testbench = record(store, row, destination, "testbench")
            overall = str(row["score"]) if row["status"] == "scored" else row["status"]
            if result:
                decision = result.get("judgment")
                if decision:
                    behavior, rtl = component(decision["behavior"]), component(decision["rtl"])
                    behavior_baseline, rtl_baseline = baseline(decision["behavior"]), baseline(decision["rtl"])
                    write_json(destination / "assessment.json", {
                        "status": row["status"], "score": row["score"],
                        "behavior": component_progress(decision["behavior"]),
                        "rtl": component_progress(decision["rtl"])})
                else:
                    write_json(destination / "assessment.json", {
                        "status": row["status"], "score": row["score"],
                        "failure_stage": result.get("failure_stage"), "failure_code": result.get("failure_code")})
                if result.get("submission"):
                    submission = json.loads(store.objects.read(BlobRef.from_dict(result["submission"])))
                    write_json(destination / "submission.json", submission)
                    for name in ("rtl", "behavior", "testbench"):
                        if submission[name] is not None:
                            for file in submission[name]["files"]:
                                path = destination / "submitted" / name / file["path"]
                                path.parent.mkdir(parents=True, exist_ok=True)
                                if not path.exists():
                                    path.write_bytes(store.objects.read(BlobRef.from_dict(file["content"])))
            if testbench:
                observation = testbench["observation"]
                tb = observation["comparison"] or observation["code"]
                cov = observation["coverage"]
                if cov is not None:
                    coverage = f"{cov['covered']}/{cov['total']}"
        label = f"{item['setting']}/{item['task']}" if "setting" in item else item["task"]
        link = f"[{label}]({trace.with_name('review.md')})" if progress["status"] != "waiting" else label
        summary.append(f"| {link} | {progress['status']} | {progress['turns']} | {progress['tool_calls']} | "
                       f"{overall} | {behavior} | {rtl} | {behavior_baseline} | {rtl_baseline} | {tb} | {coverage} |")
    write_bytes(output / "REVIEW.md", ("\n".join(summary) + "\n").encode("utf-8"))


def main(argv=None):
    """Export a saved review; never dispatch a model, tool or judgment."""
    import argparse

    from behave.core.records import RecordError, canonical_json

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("output", type=Path, help="saved review with trace_index.json and run records")
    parser.add_argument("--judgment", help="read this named scoring pass instead of the original report")
    args = parser.parse_args(argv)
    try:
        export_review(args.output.resolve(), judgment=args.judgment)
    except (RecordError, ValueError, TypeError, OSError) as error:
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "review-export-invalid"),
                              "message": str(error)}).decode())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


DATASETS = ("verilogeval_v2", "rtllm_v2", "cvdp", "authored600_v1")


def rollout_sources(directory: Path) -> list[Path]:
    """Explicit successive infrastructure retries, oldest first, never best-of."""
    sources = [directory]
    while True:
        retry = sources[-1] / (sources[-1].name + "_infra_retry")
        if not any((retry / name).exists() for name in ("selection.json", "followup_preparation.json")):
            return sources
        sources.append(retry)


def _cases(directory):
    root = RunStore(directory / "run")
    baseline = judgment_run(root, "baseline")
    rows = {}
    for run, summary_path in ((root, directory / "report.json"),
                              (baseline, baseline.directory / "summary.json")):
        runtime = run.directory / "runtime.json"
        if runtime.exists() and json.loads(runtime.read_text())["state"] != "archived":
            continue
        if summary_path.exists():
            # These are the completed BatchReport exports written by the
            # rollout/scoring owner before publishing its archived state.
            # Reuse their candidate references instead of cold-loading every
            # journal event over NFS. Verdicts still come from TaskResult below.
            summary = json.loads(summary_path.read_text())
            version(summary["schema"], "behave.evaluation-summary/2")
            rows.update((row["task_id"], row) for row in summary["candidates"])
            continue
        if (run.directory / "report").exists():
            for event in run.journal("report").load():
                if event.kind == "evaluation_case":
                    row = thaw_json(event.payload)["row"]
                    rows[row["task_id"]] = row
    return root, rows


def task_rows(root: Path) -> list[dict]:
    manifest = json.loads((root / "campaign.json").read_text())
    result = []
    for run in manifest["runs"]:
        directory = root / "runs" / run["name"]
        settings = json.loads((directory / "settings.json").read_text())
        components = submission_components(run["setting"])
        scored_components = tuple(name for name in components if name != "testbench")
        outcomes = {}
        for source in rollout_sources(directory):
            runtime = source / "runtime.json"
            if runtime.exists() and json.loads(runtime.read_text())["state"] != "archived":
                continue
            if (source / "run/report").exists():
                store, cases = _cases(source)
                outcomes.update({task: (store, case, source) for task, case in cases.items()})
        for task in settings["tasks"]:
            row = {"model": run["model"], "setting": run["setting"], "dataset": manifest["datasets"][task],
                   "task_id": task, "rollout_status": "pending", "coverage": None,
                   "coverage_counts": None, "tb_status": "pending", "infra_retry": False,
                   "trace": str(directory / "traces" / task / "trace.md")}
            for artifact in ("rtl", "behavior"):
                row[artifact] = {"status": "pending", "score": None, "baseline": {"status": "not_recorded"}}
            if "behavior" not in components:
                row["behavior"] = component_progress(None)
            if "testbench" not in components:
                row["tb_status"] = "not_requested"
            if task in outcomes:
                store, case, source = outcomes[task]
                row.update(infra_retry=source != directory, trace=str(source / "traces" / task / "trace.md"))
                row["rollout_status"] = case["status"]
                if case["result"] is not None:
                    result_record = TaskResult.from_bytes(read_verified(store.objects, BlobRef.from_dict(case["result"])))
                    value = result_record.as_dict()
                    row["rollout_status"] = result_record.rollout_status
                    decision = value.get("judgment")
                    if decision is not None:
                        row.update(rtl=component_progress(decision["rtl"]),
                                   behavior=component_progress(decision["behavior"]))
                    elif row["rollout_status"] == "no_submission":
                        for artifact in scored_components:
                            row[artifact] = {"status": "not_submitted", "score": 0,
                                             "baseline": {"status": "not_run"}}
                        if "testbench" in components:
                            row.update(coverage=0, tb_status="not_submitted")
                    else:
                        row["failure_stage"] = value.get("failure_stage")
                        row["failure_code"] = value.get("failure_code")
                        if row["failure_stage"] is not None:
                            disposition = ("rollout_unavailable" if row["failure_stage"] == "solver"
                                           else "execution_no_conclusion")
                            for artifact in scored_components:
                                row[artifact] = {"status": disposition, "score": None,
                                    "code": row["failure_code"], "baseline": {"status": "not_recorded"}}
                else:
                    row["rollout_status"] = "rollout_unavailable"
                    row["rollout_error"] = case.get("error")
                    for artifact in scored_components:
                        row[artifact] = {"status": "rollout_unavailable", "score": None,
                                         "baseline": {"status": "not_run"}}
                if case["testbench"] is not None:
                    tb = json.loads(read_verified(store.objects, BlobRef.from_dict(case["testbench"])))
                    observation = tb["observation"]
                    row["tb_status"] = observation["code"]
                    coverage = observation["coverage"]
                    if coverage is not None:
                        measurement = RTLCoverage.from_dict(coverage)
                        row["coverage"] = measurement.fraction
                        row["coverage_counts"] = {"covered": measurement.covered, "total": measurement.total}
                elif case.get("testbench_error") is not None:
                    row["tb_status"] = "execution_no_conclusion"
                    row["tb_error"] = case["testbench_error"]
            result.append(row)
    return result


def summarize_campaign(rows: list[dict], *, dataset_order: tuple[str, ...] = DATASETS) -> dict:
    groups = {}
    for model, setting in sorted({(row["model"], row["setting"]) for row in rows}):
        datasets = {}
        for dataset in dataset_order:
            selected = [row for row in rows if (row["model"], row["setting"], row["dataset"]) == (model, setting, dataset)]
            if not selected:
                continue
            metrics = {"tasks": len(selected)}
            for artifact in ("rtl", "behavior"):
                values = [row[artifact] for row in selected if row[artifact]["status"] != "not_requested"]
                counts = Counter(value["status"] for value in values)
                passes = sum(value["score"] == 1 for value in values)
                known = sum(value["score"] is not None for value in values)
                metrics[artifact] = {"counts": dict(counts), "passed": passes, "known": known,
                    "unknown": len(values) - known,
                    "confirmed_pass_rate": passes / len(values) if values else None,
                    "pass_rate": final_score(*(value["score"] for value in values)) if values else None}
            tb_rows = [row for row in selected if row.get("tb_status") != "not_requested"]
            covered = [row["coverage"] for row in tb_rows if row["coverage"] is not None]
            undefined = sum(row.get("coverage_counts") is not None and row["coverage_counts"]["total"] == 0
                            for row in tb_rows)
            metrics["coverage"] = {"known": len(covered), "undefined": undefined,
                "unknown": len(tb_rows) - len(covered) - undefined,
                "mean": sum(covered) / len(tb_rows) if tb_rows else None}
            datasets[dataset] = metrics
        averages = {}
        for key, field in (("rtl", "pass_rate"), ("behavior", "pass_rate"), ("coverage", "mean")):
            values = [datasets[dataset][key][field] for dataset in dataset_order if dataset in datasets]
            averages[key] = (sum(values) / len(dataset_order)
                             if len(values) == len(dataset_order) and all(value is not None for value in values) else None)
        groups[f"{model}/{setting}"] = {"datasets": datasets, "macro_average": averages}
    return groups


def table1(summary: dict, models: list[dict], *, include_macro: bool = True,
           dataset_order: tuple[str, ...] = DATASETS) -> str:
    def cell(value):
        return "--" if value is None else f"{100 * value:.1f}"

    lines = ["% Final artifact rates count unresolved outcomes as zero. -- means unavailable/not applicable."]
    for model in models:
        group = summary[model["id"] + "/joint"]
        values = []
        for dataset in dataset_order:
            metrics = group["datasets"][dataset]
            values.extend((metrics["rtl"]["pass_rate"], metrics["behavior"]["pass_rate"], metrics["coverage"]["mean"]))
        if include_macro:
            values.extend(group["macro_average"][key] for key in ("rtl", "behavior", "coverage"))
        label = model["label"].replace("_", r"\_").replace("&", r"\&")
        lines.append(label + " & " + " & ".join(cell(value) for value in values) + r" \\")
    return "\n".join(lines) + "\n"


__all__ = ["archived_arm", "archived_cost", "merge_campaign"]


def archived_arm(directory, model, method, judgment, lineage):
    """Use the named completed scoring pass; do not choose the best result."""
    directory = Path(directory)
    runtime = directory / "runtime.json"
    if not runtime.exists() or read(runtime)["state"] != "archived":
        return []
    rows = []
    if method == "joint_sim":
        reader = DirectoryReader(directory / "run/objects")
        for case in read(directory / "run/judgments" / judgment / "summary.json")["candidates"]:
            value = (TaskResult.from_bytes(read_verified(reader, BlobRef.from_dict(case["result"]))).as_dict()
                     if case["result"] is not None else None)
            task = case["task_id"]
            if value is not None and value["revision"] != lineage[task]["revision"]:
                raise ValueError(f"task revision differs: {task}")
            rows.append({"task": task, "model": model, "setting": method,
                         "revision": lineage[task]["revision"], "source_run": str(directory),
                         "judgment": judgment, "result": case["result"], **artifact_views(value, method)})
    else:
        summary_file = directory / "judgments" / judgment / "summary.json"
        rollouts = {row["task"]: row for row in read(directory / "rollout.json")}
        for case in read(summary_file):
            task = case["task"]
            rtl, status = case.get("rtl"), case.get("status")
            if rtl is not None:
                view = component_progress(rtl)
            elif status == "evaluation_error":
                view = {"status": "execution_no_conclusion", "score": None,
                        "code": status, "reason": case.get("error")}
            else:
                rollout = rollouts[task]
                code = rollout.get("error") or rollout["status"]
                no_submission = (rollout["status"] == "no_submission"
                                 or code == "external-model-context_limit")
                view = {"status": "not_submitted" if no_submission else "rollout_unavailable",
                        "score": 0 if no_submission else None, "code": code}
            result_file = directory / "judgments" / judgment / task / "result.json"
            rows.append({"task": task, "model": model, "setting": method,
                         "revision": lineage[task]["revision"], "source_run": str(directory),
                         "judgment": judgment, "rtl": view,
                         "summary_file": str(summary_file),
                         "result_file": str(result_file) if result_file.exists() else None})
    return rows


def merge_campaign(panel, root):
    root = Path(root)
    config = read(root / "configuration.json")
    lineage = {row["task"]: row for row in read(root / "task_lineage.json")}
    if set(lineage) != set(panel["tasks"]):
        raise ValueError("campaign and panel contain different task sets")
    for task, row in lineage.items():
        if row["revision"] != panel["lineage"][task]["revision"]:
            raise ValueError(f"campaign revision differs: {task}")
    records = {(row["task"], row["model"], row["setting"]): row for row in panel["records"]}
    # Re-read the exact named external publications, not newer/better attempts.
    # Older panel exports collapsed provider/tool interruptions into missing
    # submissions. Their preserved rollout records supply the actual reason.
    sources = {}
    for key, row in records.items():
        if row["setting"] in {"mage", "chipmate"}:
            source = row["source_run"], row["model"], row["setting"], row["judgment"]
            sources.setdefault(source, []).append(key)
    for (directory, model, method, judgment), keys in sources.items():
        published = {row["task"]: row for row in archived_arm(directory, model, method, judgment, lineage)}
        for key in keys:
            records[key] = {**records[key], **published[key[0]]}
    states = {}
    for model in config["models"]:
        for method in config["workers"]:
            name = f"{model}_{method}"
            directory = root / "runs" / config.get("run_directories", {}).get(name, name)
            rows = archived_arm(directory, model, method, config["judgment"], lineage)
            states[f"{model}/{method}"] = {"records": len(rows), "expected": len(lineage)}
            for row in rows:
                records[row["task"], model, method] = row
    for recovery in config.get("recoveries", []):
        model, method = recovery["model"], recovery["method"]
        directory = root / "runs" / recovery["run_directory"]
        ancestor = root / "runs" / recovery["replaces"]
        selected = set(recovery["tasks"])
        rows = archived_arm(directory, model, method, config["judgment"], lineage)
        if rows and {row["task"] for row in rows} != selected:
            raise ValueError("recovery archive differs from its declared tasks")
        for task in selected:
            key = task, model, method
            if key in records and Path(records[key]["source_run"]).resolve() != ancestor.resolve():
                raise ValueError(f"recovery names a different ancestor: {key}")
            # A declared replacement that has not finished is unavailable, not
            # an opportunity to keep whichever old/new score looks better.
            records.pop(key, None)
        for row in rows:
            records[row["task"], model, method] = row
        states[f"{model}/{method}/{recovery['run_directory']}"] = {
            "records": len(rows), "expected": len(selected), "reason": recovery["reason"]}
    return {**panel, "records": list(records.values()), "pipeline_campaign": str(root),
            "pipeline_publications": states}


def _unpriced_events(reader, heads):
    events = []
    for head in heads:
        while head is not None:
            event = json.loads(read_verified(reader, BlobRef.from_dict(head)))
            head = event["parent"]
            if event["kind"] != "model_settled":
                continue
            payload = event["payload"]
            record = json.loads(read_verified(reader, BlobRef.from_dict(payload["record"])))
            if record["charge"] is not None:
                continue
            category = "unpriced"
            if payload["outcome"] == "failure" and record["code"] == "context-input-full":
                category = "local_context_rejection"
            elif payload["outcome"] == "failure" and record["code"] == "openai-http-400":
                raw = json.loads(read_verified(reader, BlobRef.from_dict(payload["raw"])))
                if raw.get("error", {}).get("type") == "invalid_request_error":
                    category = "request_rejected"
            events.append({"call_id": payload["call_id"], "record": payload["record"],
                           "raw": payload["raw"], "category": category,
                           "code": record.get("code")})
    return events


def archived_cost(row, cache):
    """Use the same rollout as its verdict, including every role/candidate call."""
    directory = Path(row["source_run"])
    external = row["setting"] in {"mage", "chipmate"}
    path = directory / ("rollout.json" if external else "report.json")
    if path not in cache:
        cache[path] = read(path)
    cases = cache[path] if external else cache[path]["candidates"]
    if external:
        matches = [case for case in cases if case["task"] == row["task"]]
    elif row.get("run_key") is not None:
        matches = [case for case in cases if case["run_key"] == row["run_key"]]
    else:
        matches = [case for case in cases if case["task_id"] == row["task"]]
    if len(matches) != 1:
        raise ValueError(f"ambiguous/missing rollout billing: {row['task']}/{row['setting']}")
    case = matches[0]
    billing = case if external else case["billing"]
    amounts = billing["cost"] if external else billing["known_estimates_by_currency"]
    if set(amounts) - {"USD"}:
        raise ValueError("figure cost requires USD accounting")
    unpriced = billing["unpriced_dispatches"]
    unresolved = billing["unresolved_dispatches"]
    audit = []
    if unpriced:
        reader = DirectoryReader(directory / ("objects" if external else "run/objects"))
        if external:
            heads = [role["head"] for role in case["roles"]]
        else:
            accounting = json.loads(read_verified(reader, BlobRef.from_dict(billing["accounting"])))
            heads = [accounting["heads"]["provider"]]
        audit = _unpriced_events(reader, heads)
        if len(audit) != unpriced:
            raise ValueError(f"billing and dispatch audit differ: {row['task']}/{row['setting']}")
    return {"recorded_usd": amounts.get("USD", "0"),
            "unpriced_dispatches": unpriced, "unresolved_dispatches": unresolved,
            "unknown_cost_dispatches": sum(event["category"] == "unpriced" for event in audit),
            "billing_file": str(path), "unpriced_events": audit,
            "accounting": None if external else billing.get("accounting")}


def read(path):
    return json.loads(Path(path).read_text())


def report_rows(directory, summary):
    if summary.exists():
        return {row["run_key"]: row for row in read(summary)["candidates"]}
    rows = {}
    for path in sorted((directory / "report").glob("*.json")):
        event = read(path)
        if event["kind"] == "evaluation_case":
            row = event["payload"]["row"]
            rows[row["run_key"]] = row
    return rows


def artifact_views(value, setting):
    requested = submission_components(setting)
    views = {}
    for artifact in ("rtl", "behavior"):
        if artifact not in requested:
            views[artifact] = component_progress(None)
        elif value is None:
            views[artifact] = {"status": "pending", "score": None}
        elif value["judgment"] is not None:
            views[artifact] = component_progress(value["judgment"][artifact])
        elif value["failure_stage"] is not None:
            views[artifact] = {"status": "rollout_unavailable" if value["failure_stage"] == "solver"
                               else "execution_no_conclusion", "score": None,
                               "code": value["failure_code"]}
        elif value["submission"] is None:
            views[artifact] = {"status": "not_submitted", "score": 0}
        else:
            views[artifact] = {"status": "pending_judgment", "score": None}
    return views


def source_records(source):
    directory = Path(source["path"])
    settings = read(directory / "settings.json")
    state = read(directory / "runtime.json") if (directory / "runtime.json").exists() else {}
    inventory = (read(directory / "trace_index.json") if (directory / "trace_index.json").exists()
                 else source.get("planned_cases", []))
    progress = {r["run_key"]: r for r in read(directory / "progress.json")} if (directory / "progress.json").exists() else {}
    progress_by_case = {(r["task"], r.get("setting", settings.get("setting", "joint"))): r for r in progress.values()}
    objects = Path(source["objects"])
    reader = DirectoryReader(objects)
    release = ReleaseManifest.from_bytes(read_verified(reader, BlobRef.from_dict(settings["release"])))
    revisions = {task.task_id: task.manifest.as_dict() for task in release.tasks}
    store = RunStore(directory / "run")
    # Live traces and objects are node-local. Read the small published progress
    # until the owning process has finished publishing the complete archive.
    archived = state.get("state") == "archived"
    versions = [("on_submit", report_rows(store.directory, directory / "report.json"))] if archived else []
    for name in source.get("judgments", []):
        judgment = store.directory / "judgments" / name
        runtime = judgment / "runtime.json"
        if runtime.exists() and read(runtime)["state"] == "archived":
            versions.append((name, report_rows(judgment, judgment / "summary.json")))
    result = []
    for item in inventory:
        key = item["run_key"]
        task, setting = item["task"], item.get("setting", settings.get("setting", "joint"))
        live = progress.get(key, progress_by_case.get((task, setting), {}))
        row = {"source_id": source["id"], "cohort": source["cohort"], "model": source["model"],
               "provider": settings.get("provider"), "model_id": settings["model"],
               "setting": setting, "task": task, "sample": source.get("sample", 0),
               "dataset": source.get("datasets", {}).get(task, source.get("dataset", "unspecified")),
               "release": settings["release"], "revision": revisions[task],
               "run_key": key, "source_run": str(directory), "trace": str(directory / item["trace"]),
               "archive_state": state.get("state", "not_started"),
               "rollout_status": live.get("status", "not_started"),
               "turns": live.get("turns"), "attempt_id": None,
               "submission": None, "failure_code": None, "billing": None, "judgments": []}
        value = None
        for name, cases in versions:
            case = cases.get(key)
            if case is None:
                continue
            if name == "on_submit":
                row["billing"] = case.get("billing")
            if case["result"] is None:
                row["rollout_status"] = "rollout_unavailable"
                row["failure_code"] = case.get("error")
                continue
            record = TaskResult.from_bytes(read_verified(store.objects, BlobRef.from_dict(case["result"])))
            value = record.as_dict()
            row.update(attempt_id=record.attempt_id, submission=value["submission"],
                       rollout_status=record.rollout_status, revision=value["revision"],
                       failure_code=record.failure_code, result=case["result"], judgment=name)
            row["judgments"].append({"name": name, "result": case["result"], **artifact_views(value, setting)})
        row.update(artifact_views(value, setting))
        if value is None and row["rollout_status"] == "rollout_unavailable":
            for artifact in submission_components(setting):
                if artifact in ("rtl", "behavior"):
                    row[artifact] = {"status": "rollout_unavailable", "score": None,
                                     "code": row["failure_code"]}
        result.append(row)
    return result


def current_records(records, sources):
    ancestors = {source["id"]: source.get("retry_of") for source in sources}
    current = {}
    for row in records:
        key = (row["cohort"], row["model"], row["setting"], row["task"], row["revision"]["sha256"], row["sample"])
        previous = current.get(key)
        if previous is not None:
            ancestor = ancestors[row["source_id"]]
            while ancestor is not None and ancestor != previous["source_id"]:
                ancestor = ancestors[ancestor]
            if ancestor is None:
                raise ValueError(f"overlapping independent attempts need distinct sample IDs: {key[:4]}")
        current[key] = row
    return list(current.values())


def issue_records(records):
    issues = []
    for row in records:
        for artifact in ("rtl", "behavior"):
            value = row[artifact]
            if value["status"] not in {"sim_no_conclusion", "execution_no_conclusion", "unavailable"}:
                continue
            issues.append({key: row.get(key) for key in ("cohort", "dataset", "model", "setting", "task",
                          "source_id", "source_run", "run_key", "trace", "judgment", "result")}
                          | {"artifact": artifact, **value})
    return issues


def _missing_exports(trace: Path) -> list[str]:
    """Report absent exported records without changing a live run's outcome."""
    present = {path.name for path in trace.iterdir()}
    expected = {"opening.json", "tools.json", "trace.md"}
    for event, exports in (
        ("model_requested", ("request",)),
        ("model_settled", ("reply", "provider_raw")),
        ("tool_settled", ("tool_result",)),
    ):
        suffix = f"_{event}.json"
        for name in present:
            if name.endswith(suffix):
                expected.update(f"{name.removesuffix(suffix)}_{export}.json" for export in exports)
    return sorted(expected - present)


def audit_traces(directory: Path) -> dict:
    index = directory / "trace_index.json"
    traces = ([directory / row["trace"] for row in json.loads(index.read_text())]
              if index.exists() else [])
    folders = ([path.parent for path in traces] if index.exists()
               else sorted((directory / "traces").glob("*")))
    tasks = []
    for trace in folders:
        if not trace.is_dir():
            continue
        counts, failures, destinations, tool_errors = Counter(), Counter(), Counter(), Counter()
        violations = []
        max_total = 0
        for path in sorted(trace.glob("*_reply.json")):
            reply = json.loads(path.read_text())
            counts["responses"] += 1
            if "code" in reply:
                failures[reply["code"]] += 1
            else:
                counts["assistant_text"] += bool(reply["content"].strip())
                counts["tool_calls"] += len(reply["calls"])
                counts["empty_tool_arguments"] += sum(not call["arguments"] for call in reply["calls"])
                counts["complete_tool_responses"] += bool(reply["calls"])
                continuation = reply.get("continuation", {})
                items = continuation.get("items", ())
                readable = bool(continuation.get("reasoning_content") or continuation.get("reasoning"))
                details = continuation.get("reasoning_details", ())
                readable |= any(item.get("text") or item.get("summary") for item in details)
                readable |= any(item.get("thinking") or any(part.get("text") for part in item.get("summary", ()))
                                for item in items if item["type"] in {"thinking", "reasoning"})
                counts["readable_reasoning"] += bool(readable)
                counts["opaque_reasoning"] += (bool(details) or any(item["type"] in {"reasoning", "redacted_thinking"}
                                                   for item in items)) and not readable
                counts["output_truncations"] += reply["finish_reason"] == "length"
                counts["context_exhaustions"] += reply["finish_reason"] == "context_limit"
            usage = reply.get("usage", {})
            budget = usage.get("context_budget")
            normalized = usage.get("normalized")
            if budget is not None:
                # This explicit preflight result is produced before generation.
                # Runtime may evict old turns and retry. Count the rejection,
                # not an oversized request sent to the model.
                rejected_input = reply.get("code") == "context-input-full"
                counts["input_preflight_rejections" if rejected_input else "counted_requests"] += 1
                if not rejected_input and budget["input_tokens"] + budget["max_output_tokens"] > budget["limit"]:
                    violations.append({"file": path.name, "kind": "requested_context_exceeded"})
                if normalized is not None:
                    total = normalized["input_tokens"] + normalized["output_tokens"]
                    max_total = max(max_total, total)
                    if total > budget["limit"]:
                        violations.append({"file": path.name, "kind": "returned_context_exceeded", "tokens": total})
            if usage.get("routing") is not None:
                destinations[usage["routing"]["url"]] += 1
        for path in sorted(trace.glob("*_tool_result.json")):
            reply = json.loads(path.read_text())
            if reply.get("error") is not None:
                tool_errors[str(reply["error"])] += 1
        tasks.append({"task": trace.relative_to(directory / "traces").as_posix(),
                      "counts": dict(counts), "failures": dict(failures),
                      "tool_errors": dict(tool_errors),
                      "destinations": dict(destinations), "max_returned_total_tokens": max_total,
                      "context_violations": violations, "missing_exports": _missing_exports(trace)})
    totals, failures, destinations, tool_errors = Counter(), Counter(), Counter(), Counter()
    for task in tasks:
        totals.update(task["counts"])
        failures.update(task["failures"])
        destinations.update(task["destinations"])
        tool_errors.update(task["tool_errors"])
    return {"run": directory.name, "tasks": tasks, "counts": dict(totals), "failures": dict(failures),
            "tool_errors": dict(tool_errors),
            "destinations": dict(destinations),
            "affinity_violations": [task["task"] for task in tasks if len(task["destinations"]) > 1],
            "context_violations": [{"task": task["task"], **item} for task in tasks for item in task["context_violations"]],
            "missing_exports": [{"task": task["task"], "file": name}
                                for task in tasks for name in task["missing_exports"]]}
