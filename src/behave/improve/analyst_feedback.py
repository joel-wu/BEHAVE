"""Small Analyst entry view; unchanged detailed evidence stays available on demand."""

from collections import Counter

from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.records import canonical_json


def compact_feedback(facts):
    files, summaries = [], []

    def add(path, data):
        files.append(FileData(FileRef(path, BlobRef.of(data)), data))

    for row in facts["observations"]:
        path = f"feedback/observations/{BlobRef.of(row['id'].encode()).sha256}.json"
        add(path, canonical_json(row))
        summary = {k: row[k] for k in ("id", "task_id", "context", "status", "score", "final_score",
                                      "retry_of", "update", "checkpoint") if k in row}
        summary.update({k: row[k] for k in ("rtl", "behavior")})
        summary["details"] = path
        summaries.append(summary)
    excluded = facts.get("excluded_attempts", [])
    details = "feedback/excluded.jsonl"
    add(details, b"".join(canonical_json(row) + b"\n" for row in excluded))
    counts = Counter((row["task_id"], row["context"], row.get("reason", row.get("status", "excluded")))
                     for row in excluded)
    view = {k: v for k, v in facts.items() if k not in ("observations", "excluded_attempts", "schema")}
    view.update(schema="behave.analyst-feedback/1", observations=summaries,
        excluded_summary={"count": len(excluded), "details": details,
            "groups": [{"task_id": task, "context": context, "reason": reason, "count": count}
                       for (task, context, reason), count in sorted(counts.items())],
            "note": "Cancelled/unstarted work is not a scored attempt or evidence of failure."})
    add("feedback.json", canonical_json(view))
    return tuple(files)
