"""Read-only study evidence and billed-attempt attribution from explicit archives.

Follow recorded recovery links and only inspect named campaigns/attempt roots.
Incomplete, historical and unresolved evidence remains distinguishable. This
reader neither launches work, settles spending nor imports plotting code.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import thaw_json

from .records import read_document
from .report import frontier


def parse_campaigns(values):
    """Parse repeatable TASK=DIRECTORY options without opening archive paths."""
    campaigns, names = [], set()
    for value in values:
        name, separator, directory = value.partition("=")
        if not separator or not name.strip() or not directory.strip():
            raise ValueError("campaign must be TASK=DIRECTORY")
        if name in names:
            raise ValueError(f"duplicate campaign task: {name}")
        names.add(name)
        campaigns.append((name, Path(directory)))
    return campaigns


def ancestors(root):
    """Newest first, following explicit recovery provenance rather than directory names."""
    result, seen = [], set()
    while root is not None:
        root = Path(root).resolve()
        if root in seen:
            raise ValueError("cyclic PPA recovery ancestry")
        seen.add(root)
        result.append(root)
        binding = root / "recovery.json"
        root = json.loads(binding.read_text())["directory"] if binding.exists() else None
    return result


def final_snapshot(directory):
    """Read only a settled final prefix; never promote a requested action to a result."""
    if not (directory / "workflow").is_dir():
        return None
    run = JournalDirectory(directory)
    events = run.journal("workflow").load(verify_artifacts=False)
    if not events or events[0].kind != "started":
        return None
    manifest_ref = BlobRef.from_dict(thaw_json(events[0].payload["manifest"]))
    manifest = read_document(run.objects, manifest_ref)
    complete = events[-1].kind == "finished"
    if complete:
        result_ref = BlobRef.from_dict(thaw_json(events[-1].payload["result"]))
        state = read_document(run.objects, result_ref)
        if state["manifest"] != manifest_ref.as_dict():
            raise ValueError("final report differs from its started manifest")
        head = result_ref
    else:
        if not (directory / "measurements").is_dir():
            return None
        settled = [event for event in run.journal("measurements").load(verify_artifacts=False)
                   if event.kind == "settled"]
        if not settled:
            return None
        head = BlobRef.from_dict(thaw_json(settled[-1].payload["record"]))
        state = read_document(run.objects, head)
    return {"directory": str(directory), "manifest": manifest, "manifest_reference": manifest_ref.as_dict(),
            "complete": complete, "record": head.as_dict(), "candidates": state["candidates"]}


def requested_manifest(directory):
    if not (directory / "workflow").is_dir():
        return None
    events = JournalDirectory(directory).journal("workflow").load(verify_artifacts=False)
    if not events or events[0].kind != "started":
        return None
    return thaw_json(events[0].payload["manifest"])


def final_point(row):
    checked = any(item["final"] and item["mode"] == "final" for item in row["history"])
    return {"candidate": row["candidate"], "checked": checked,
            "score": row.get("score") if checked else None,
            "admitted": checked and bool(frontier((row,))),
            "area_um2": row.get("eda", {}).get("area_um2"),
            "time_ns": row.get("performance", {}).get("time_ns"),
            "timing_met": (row.get("eda", {}).get("timing") or {}).get("status") == "met"}


def unresolved_judgments(candidates, reader):
    """Export committed no-result diagnoses without reading private evidence blobs."""
    result = []
    for key, row in candidates.items():
        final = next((item for item in reversed(row["history"])
                      if item["final"] and item["mode"] == "final"), None)
        if final is None or row.get("score") is not None:
            continue
        record = BlobRef.from_dict(final["record"])
        judgment = read_document(reader, record)["judgment"]
        if judgment["score"] is not None:
            raise ValueError("unresolved final candidate has a concrete judgment")
        detail = judgment.get("detail") or {}
        failure = detail.get("failure") or {}
        reason = failure.get("reason") or detail.get("reason") or {}
        reason_detail = reason.get("detail") or {}
        result.append({"candidate": key, "code": judgment["code"], "origin": judgment.get("origin"),
                       "phase": failure.get("phase"), "reason_kind": reason.get("kind"),
                       "reason": reason.get("why"),
                       "reason_detail": reason_detail.get("detail") if isinstance(reason_detail, dict) else None,
                       "elapsed_seconds": judgment.get("elapsed_seconds"),
                       "cleanup": judgment.get("cleanup"), "retryable": judgment.get("retryable"),
                       "measurement_sha256": record.sha256})
    return result


def task_snapshot(name, root):
    from behave.ppa.config import replica_objective

    chain = ancestors(root)
    plan = json.loads((chain[0] / "plan.json").read_text())
    experiment = json.loads(Path(plan.get("final_experiment") or plan["experiment"]).read_text())
    common = json.loads((chain[0] / "common-pool.json").read_text()) if (chain[0] / "common-pool.json").exists() else None
    expected_pool = None
    if common is not None:
        from behave.core.records import canonical_json
        expected_pool = BlobRef.of(canonical_json(common["stimuli"])).as_dict()
    methods = [method["name"] for method in plan["methods"]]
    points, runs, identities, unresolved = [], [], set(), []
    for method in methods:
        for replica in range(plan["replicates"]):
            label = f"{method}/r{replica:02d}"
            selected = next((found for prior in chain
                             if (found := final_snapshot(prior / "runs" / label / "final")) is not None), None)
            expected_manifest = next((found for prior in chain
                                     if (found := requested_manifest(prior / "runs" / label / "final")) is not None), None)
            objective = replica_objective(experiment, replica)
            bound = objective["target_ns"]
            run = dict(task=name, method=method, replica=replica, label=label,
                       **objective, complete=False, evaluated=0, feasible=0, has_feasible=False,
                       meets_target=False if bound is not None else None,
                       best_area_um2=None, archive=None, historical_evidence=False)
            if selected is not None:
                manifest = selected["manifest"]
                if manifest["experiment"] != experiment:
                    raise ValueError(f"measurement settings differ in {selected['directory']}")
                reader = JournalDirectory(selected["directory"]).objects
                from behave.core.records import canonical_json
                pool = read_document(reader, BlobRef.from_dict(manifest["pool_ref"]))
                identity = (manifest["native"], BlobRef.of(canonical_json(pool)).sha256)
                identities.add(identity)
                if expected_pool is not None and identity[1] != expected_pool["sha256"]:
                    raise ValueError("final evidence does not use the campaign's common suite")
                candidates = [final_point(row) for row in selected["candidates"].values()]
                feasible = [point for point in candidates if point["admitted"]]
                eligible = [point for point in feasible if bound is None or point["time_ns"] <= bound]
                historical = selected["manifest_reference"] != expected_manifest
                unresolved.extend(dict(task=name, method=method, replica=replica,
                                       archive=selected["directory"], historical_evidence=historical, **item)
                                  for item in unresolved_judgments(selected["candidates"], reader))
                run.update(complete=selected["complete"] and not historical,
                           historical_evidence=historical, archive=selected["directory"],
                           record=selected["record"], evaluated=sum(point["checked"] for point in candidates),
                           feasible=len(feasible), has_feasible=bool(feasible),
                           meets_target=bool(eligible) if bound is not None else None,
                           best_area_um2=min((point["area_um2"] for point in eligible), default=None))
                points.extend(dict(task=name, method=method, replica=replica,
                                   historical_evidence=historical, **point) for point in candidates)
            runs.append(run)
    complete = all(run["complete"] for run in runs)
    if complete and len(identities) != 1:
        raise ValueError("completed PPA shards mix evaluator builds or correctness suites")
    return {"task": name, "root": str(chain[0]), "ancestors": list(map(str, chain)),
            "plan": plan, "experiment": experiment, "complete": complete,
            "native_suites": sorted(identities), "runs": runs, "points": points, "unresolved": unresolved}


def call_owners(tasks, attempt_roots=()):
    owners = {}
    for task in tasks:
        directories = {Path(directory) for directory in task["ancestors"]}
        for root in attempt_roots:
            directories.update(Path(root).glob(f"*/execution/campaigns/{task['task']}"))
        for directory in sorted(directories):
            for search in sorted(Path(directory).glob("runs/*/r*/search")):
                if not (search / "agent").is_dir():
                    continue
                label = search.parent.relative_to(Path(directory) / "runs").as_posix()
                owner = (task["task"], label)
                for event in JournalDirectory(search).journal("agent").load(verify_artifacts=False):
                    if event.kind != "model_requested":
                        continue
                    key = event.payload["call_id"]
                    if key in owners and owners[key] != owner:
                        raise ValueError("one billed request belongs to different PPA searches")
                    owners[key] = owner
    return owners


def attribute_cost(calls, owners):
    costs = defaultdict(lambda: {"usd": Decimal(0), "reserved_usd": Decimal(0), "calls": 0, "unresolved": 0})
    unmatched = []
    for key, (reservation, outcome) in calls.items():
        if key not in owners:
            unmatched.append(key)
            continue
        if reservation["call_id"] != key or reservation["ceiling"]["currency"] != "USD":
            raise ValueError("PPA cost panel requires correctly identified USD charges")
        row = costs[owners[key]]
        row["calls"] += 1
        if outcome is None or outcome["amount"] is None:
            row["unresolved"] += 1
            row["reserved_usd"] += Decimal(reservation["ceiling"]["amount"])
        else:
            if outcome["call_id"] != key:
                raise ValueError("settlement names a different request")
            row["usd"] += Decimal(outcome["amount"])
    return {key: {name: str(value) if isinstance(value, Decimal) else value
                  for name, value in row.items()} for key, row in costs.items()}, unmatched


def study_snapshot(campaigns, account, attempt_roots=()):
    tasks = [task_snapshot(name, root) for name, root in campaigns]
    cache = json.loads((account / "spending_cache.json").read_text())
    costs, unmatched = attribute_cost(cache["calls"], call_owners(tasks, attempt_roots))
    for task in tasks:
        for run in task["runs"]:
            run["cost"] = costs.get((task["task"], run["label"]))
    return {"schema": "behave.ppa-study-report/1", "observed_at": datetime.now(UTC).isoformat(),
            "complete": all(task["complete"] for task in tasks), "tasks": tasks,
            "billing": {"account": str(account), "attempt_roots": list(map(str, attempt_roots)),
                        "configuration": cache["configuration"],
                        "head": cache["head"], "unattributed_calls": unmatched}}
