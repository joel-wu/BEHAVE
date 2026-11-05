"""Summarize recorded baseline failures without recalculating any score.

Read completed rows from one batch receipt. Keep the public decision, candidate
failures and verifier refusals separate; neither a tool error nor a resource
deadline establishes an output mismatch. Large embedded tool logs stay in the
original per-task receipt.
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def failure_reason(failure):
    reason = failure.get("reason") or failure.get("refusal") or failure
    detail = reason.get("detail") or {}
    return {
        "origin": failure.get("origin"),
        "phase": failure.get("phase"),
        "disposition": failure.get("disposition"),
        "code": reason.get("kind"),
        "why": reason.get("why"),
        "detail": detail.get("detail"),
        "seconds": detail.get("seconds"),
    }


def review_task(result):
    failures = []
    for key in ("refusal", "failure"):
        if result.get(key):
            failures.append(failure_reason(result[key]))
    replay = result.get("replay") or {}
    run_failures = Counter()
    for run in replay.get("runs", ()):
        if run.get("failure"):
            reason = failure_reason(run["failure"])
            reason["execution_phase"] = run["phase"]
            run_failures[json.dumps(reason, sort_keys=True)] += 1
    return {
        "task": result["task"], "status": result["status"],
        "phase": result.get("phase"), "elapsed_s": result["elapsed_s"],
        "error": result.get("error"), "reason": result.get("reason"),
        "wall_seconds": result.get("wall_seconds"), "failures": failures,
        "run_failures": [dict(json.loads(reason), count=count)
                         for reason, count in run_failures.items()],
        "evaluation": result.get("evaluation"),
        "goals": replay.get("classes"),
    }


def review_batch(root, batch):
    receipt = json.loads((root / f"batch-{batch:03}.json").read_text())
    unresolved = []
    for row in receipt["tasks"]:
        if row["status"] == "scored-pass":
            continue
        path = root / "tasks" / row["task"].removeprefix("data/problems/") / "result.json"
        unresolved.append(review_task(json.loads(path.read_text())))
    return {key: receipt[key] for key in ("batch", "expected", "completed", "counts")} | {
        "nonpassing": unresolved,
    }


def execution_gap(result, *, self_comparison=False):
    """Read execution acceptance, independently of whether the candidate is correct.

    This does not produce or override a score. Normal mismatches/safety failures
    are valid scored executions. Resource truncation and unscored runs are not a
    completed baseline gate. A self-comparison mismatch requires investigation;
    this additional check must not be used for independent B1/B2 comparisons.
    """
    if result.get("supervision", {}).get("cleanup") == "unconfirmed":
        return "qualification-cleanup-unconfirmed"
    decision = result.get("decision") or {}
    authority = decision.get("authority") or {}
    evidence = decision.get("evidence") or {}
    if authority.get("authoritative") is not True:
        return "no-authoritative-decision"
    if evidence.get("complete") is not True:
        return "incomplete-baseline"
    for _index, failure in decision.get("failures", ()):
        if failure.get("origin") != "candidate":
            return "non-candidate-execution-failure"
    if self_comparison and (evidence.get("mismatch_count", 0)
                            or evidence.get("memory_mismatch_count", 0)):
        return "self-comparison-mismatch"
    return None


def review_execution_gate(root, batches, *, self_comparison=False):
    """Check exactly the requested frozen tasks, not only the receipts present."""
    root = Path(root)
    plan = json.loads((root / "plan.json").read_text())
    rows = [row for row in plan["tasks"] if row["batch"] in batches]
    counts, gaps = Counter(), []
    for row in rows:
        path = root / "tasks" / row["task"].removeprefix("data/problems/") / "result.json"
        if not path.is_file():
            gaps.append({"task": row["task"], "reason": "missing-receipt"})
            continue
        result = json.loads(path.read_text())
        counts[result["status"]] += 1
        gap = execution_gap(result, self_comparison=self_comparison)
        if gap is not None:
            gaps.append({"task": row["task"], "reason": gap, "status": result["status"]})
    return {"expected": len(rows), "counts": dict(counts), "gaps": gaps,
            "execution_complete": bool(rows) and not gaps}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("batches", type=int, nargs="+")
    args = parser.parse_args()
    print(json.dumps([review_batch(args.root, batch) for batch in args.batches], indent=2))


if __name__ == "__main__":
    main()
