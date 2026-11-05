"""Native training export from already recorded solver replies and results.

The serving adapter captured the actual tokens once, at the model boundary.
Training consumes those records, not the raw HTTP exchange or reconstructed
billing history. Explicit historical exclusions remain exclusions.
"""

import json

from behave.core.artifacts import BlobRef
from behave.core.records import thaw_json
from behave.task_execution.result import final_score_from_record

from .learning_report import PreparedTrainingReport, _summary
from .training_data import prefix_cache_usage


def token_status(data, exclusion, requirements, verifier):
    """Compact eligibility from a reply already decoded by the producer."""
    if exclusion is not None:
        return "excluded", exclusion
    if data is None:
        return "unavailable", "token-data-missing"
    if data.get("schema") == "behave.native-token-unavailable/1":
        return "unavailable", data["reason"]
    if data.get("schema") != "behave.token-trace/1":
        return "unavailable", "token-schema-unsupported"
    if any(data[name] != requirements[name] for name in ("adapter", "checkpoint", "tokenizer", "template")):
        return "unavailable", "token-provenance-mismatch"
    if not set(requirements["required_signals"]) <= set(data["signals"]):
        return "unavailable", "training-signals-missing"
    if verifier is None:
        return "unavailable", "token-verifier-unavailable"
    return ("eligible", None) if any(data["response_mask"]) else ("excluded", "no-eligible-response-tokens")


def capture_token_statuses(reference, trajectory, target, requirements, verifier):
    # Retain small statuses, not cumulative token arrays or decoded replies.
    target[reference.sha256] = {turn.turn: token_status(turn.token_data, turn.exclusion,
        requirements, verifier) for turn in trajectory.turns}


def record_rollouts(plan, reader, report, verifier=None, *, accounting=None, token_statuses=None):
    rows, captures = [], {}
    requirements = plan.requirements.as_dict()
    for index, row in enumerate(thaw_json(report.summary["candidates"])):
        saved = None
        if row["result"] is not None:
            result_ref = BlobRef.from_dict(row["result"])
            result = json.loads(reader.read(result_ref))
            account = json.loads(reader.read(BlobRef.from_dict(result["accounting"])))
            known = None if token_statuses is None else token_statuses.get(result_ref.sha256)
            turns = []
            for turn in account["turns"]:
                if known is not None:
                    status, reason = known[turn["turn"]]
                elif turn["exclusion"] is not None:
                    status, reason = "excluded", turn["exclusion"]
                else:
                    # Cold replay has no producer cache. Native packing still
                    # reads the actual tokens once in its parallel workers.
                    reply = json.loads(reader.read(BlobRef.from_dict(turn["response"])))
                    data = reply.get("token_data")
                    status, reason = token_status(data, turn["exclusion"], requirements, verifier)
                    if status == "eligible":
                        cache = prefix_cache_usage({"usage": reply["usage"].get("raw")}, len(data["input_ids"]))
                        if cache is not None:
                            captures[(result_ref.sha256, turn["turn"])] = {"prefix_cache": cache}
                turns.append({"turn": turn["turn"], "request": turn["request"], "response": turn["response"],
                              "status": status, "reason": reason})
            saved = {"schema": "behave.rollout-examples/2", "result": result_ref.as_dict(),
                     "reward": final_score_from_record(result), "requirements": requirements, "turns": turns}
        rows.append({"index": index, "group_id": plan.group_ids[index], "task_id": row["task_id"],
                     "attempt_id": row["attempt_id"], "error": thaw_json(row["error"]), "examples": saved})
    summary = {**_summary(plan, report, rows), "schema": "behave.training-rollouts/2"}
    return PreparedTrainingReport(summary, plan, (), reader, captures)
