"""Within-task sampling facts, kept separate from retry outcomes and diagnoses."""

from collections import Counter


def task_groups(observations: list[dict]) -> list[dict]:
    groups = {}
    for row in observations:
        key = row["task_id"], row["revision"]["sha256"], row["context"]
        groups.setdefault(key, []).append(row)
    result = []
    for rows in groups.values():
        first = [row for row in rows if row["retry_of"] is None]
        parts = {}
        for name in ("rtl", "behavior"):
            statuses = Counter(row[name]["status"] for row in first)
            passed = [row["id"] for row in first if row[name]["status"] == "passed"]
            failed = [row["id"] for row in first if row[name]["status"] == "candidate_failed"]
            decided = sum(row[name]["score"] is not None for row in first)
            requested = [row[name] for row in first if row[name].get("requested", name == "rtl"
                         or row[name]["status"] != "not_observed")]
            parts[name] = {"status_counts": dict(sorted(statuses.items())),
                "requested": len(requested),
                "accuracy": None if not requested else sum(part["score"] or 0 for part in requested) / len(requested),
                "passed": passed, "failed": failed,
                "unresolved": [row["id"] for row in first if row[name]["score"] is None
                               and row[name]["status"] != "not_observed"],
                "decided": decided,
                "pass_fraction_on_decided": None if not decided else len(passed) / decided,
                "mixed_outcomes": bool(passed and failed)}
        result.append({"task_id": rows[0]["task_id"], "revision": rows[0]["revision"],
            "context": rows[0]["context"], "samples": len(first),
            "status_counts": dict(sorted(Counter(row["status"] for row in first).items())),
            "observations": [row["id"] for row in first],
            "retries": [row["id"] for row in rows if row["retry_of"] is not None], **parts})
    return result
