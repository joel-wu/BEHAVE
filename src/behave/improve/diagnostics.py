"""Project execution feedback already visible to the candidate's solver.

Do not traverse tool artifacts or judge evidence: those may contain private
reference state. This export does not execute tools or assign failure causes.
"""

from collections import Counter

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.judgment import SubmissionJudgment


def scoring_diagnostics(reader: ContentReader, judgment: SubmissionJudgment) -> tuple[FileData, ...]:
    """Export only the worker's explicitly public files, never its evidence closure."""
    files = []
    for name in ("rtl", "behavior"):
        part = getattr(judgment, name)
        if part is None:
            continue
        _, projected = comparison_feedback(part, reader, prefix=name)
        files.extend(projected)
    return tuple(files)


def execution_diagnostics(reader: ContentReader, head: BlobRef | None, *,
                          verify_artifacts: bool = True) -> tuple[FileData, ...]:
    return execution_feedback(reader, head, verify_artifacts=verify_artifacts)[1]


def execution_feedback(reader: ContentReader, head: BlobRef | None, *,
                       verify_artifacts: bool = True) -> tuple[dict | None, tuple[FileData, ...]]:
    """Small efficiency facts and public tool output from one journal traversal.

    Usage comes from the recorded normalized provider counts, not tokenizing
    text or rereading raw responses/model requests. Missing usage stays explicit.
    Tool durations include their own execution/cleanup, not model queue time.
    """
    if head is None:
        return None, ()
    records, pending = [], {}
    counts, calls, repeated = Counter(), Counter(), Counter()
    tokens = Counter()
    tool_seconds, timed_tools = 0., 0
    turns, termination = None, None
    for event in load_chain(reader, head, verify_artifacts=verify_artifacts):
        payload = thaw_json(event.payload)
        if event.kind == "model_requested":
            counts["model_dispatches"] += 1
        elif event.kind == "model_settled":
            counts["model_settlements"] += 1
            counts["model_replies" if payload["outcome"] == "reply" else "model_failures"] += 1
            if "usage" in payload:
                usage = payload["usage"]
            else:
                # Older immutable traces predate the small event-level counts.
                response = parse_json(read_verified(reader, BlobRef.from_dict(payload["record"])))
                usage = response.get("usage", {}).get("normalized")
            if usage is not None:
                counts["usage_known_dispatches"] += 1
                for name in ("input_tokens", "output_tokens"):
                    tokens[name] += usage[name]
        elif event.kind in {"context_reset", "context_evicted"}:
            counts[event.kind] += 1
        elif event.kind == "terminal":
            turns, termination = payload["turns"], payload["reason"]
        elif event.kind == "tool_requested":
            pending[(payload["turn"], payload["call"]["call_id"])] = payload["call"]
        elif event.kind == "tool_settled":
            call = pending.pop((payload["turn"], payload["call_id"]))
            calls[call["name"]] += 1
            if call["name"] not in {"run_command", "run_cocotb", "compare_rtl"}:
                continue
            repeated[(call["name"], canonical_json(call["arguments"]))] += 1
            result = (None if payload["result"] is None else
                      parse_json(read_verified(reader, BlobRef.from_dict(payload["result"]))))
            if result is not None and result.get("elapsed_seconds") is not None:
                tool_seconds += result["elapsed_seconds"]
                timed_tools += 1
            records.append({"turn": payload["turn"], "call_id": payload["call_id"],
                "tool": call["name"], "arguments": call["arguments"],
                "argument_error": call["argument_error"], "interrupted": payload["interrupted"],
                "result": result})
    facts = {"schema": "behave.candidate-efficiency/1", "turns": turns, "termination": termination,
        **{key: counts[key] for key in ("model_dispatches", "model_replies", "model_failures",
                                       "usage_known_dispatches")},
        "usage_missing_dispatches": counts["model_settlements"] - counts["usage_known_dispatches"],
        "pending_model_dispatches": counts["model_dispatches"] - counts["model_settlements"],
        "input_tokens": tokens["input_tokens"] if counts["usage_known_dispatches"] else None,
        "output_tokens": tokens["output_tokens"] if counts["usage_known_dispatches"] else None,
        "context_resets": counts["context_reset"], "context_evictions": counts["context_evicted"],
        "tool_calls": dict(sorted(calls.items())), "pending_tool_calls": len(pending),
        "repeated_identical_tool_calls": sum(count - 1 for count in repeated.values()),
        "timed_tool_calls": timed_tools, "tool_elapsed_seconds": tool_seconds if timed_tools else None}
    documents = {"efficiency.json": canonical_json(facts)}
    if records:
        documents["tools.json"] = canonical_json({"schema": "behave.candidate-diagnostics/1", "executions": records})
    return facts, tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in documents.items())
