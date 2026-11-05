"""Project recorded facts; never rerun a solver or infer missing token counts."""

from behave.agent.journal import AttemptJournal
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.improve.diagnostics import execution_feedback
from behave.improve.feedback import export_feedback
from test_improvement_feedback import inputs


def test_efficiency_uses_existing_usage_and_public_tools_without_private_context(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "journal", store)
    events = []
    private = store.put(b"private-request-and-raw-response-must-not-be-read")

    def append(kind, payload):
        events.append(journal.append(events, kind, payload))

    for turn, usage in enumerate(({"input_tokens": 100, "output_tokens": 7}, None)):
        record = store.put(canonical_json({"usage": {"normalized": usage},
            "content": "PRIVATE_MODEL_THOUGHTS", "token_data": {"response_ids": [99]}}))
        append("model_requested", {"call_id": str(turn), "request": private.as_dict()})
        append("model_settled", {"call_id": str(turn), "outcome": "reply",
                                 "record": record.as_dict(), "raw": private.as_dict()})
        call = {"call_id": str(turn), "name": "run_command",
                "arguments": {"command": "inspect candidate.py"}, "argument_error": None}
        append("tool_requested", {"turn": turn, "call": call})
        result = store.put(canonical_json({"elapsed_seconds": 2.5 if turn == 0 else .5,
                                          "stdout": "public output"}))
        append("tool_settled", {"turn": turn, "call_id": str(turn), "interrupted": None,
                                "result": result.as_dict()})
        append("context_evicted" if turn == 0 else "context_reset", {"removed_messages": 2})
    append("terminal", {"turns": 2, "reason": "turn_limit"})
    head, _ = journal.seal()

    class PublicReader:
        def read(self, ref):
            assert ref != private
            return store.read(ref)

    facts, files = execution_feedback(PublicReader(), head, verify_artifacts=False)
    assert facts["turns"] == 2 and facts["termination"] == "turn_limit"
    assert facts["model_dispatches"] == facts["model_replies"] == 2
    assert facts["usage_known_dispatches"] == facts["usage_missing_dispatches"] == 1
    assert facts["input_tokens"] == 100 and facts["output_tokens"] == 7
    assert facts["context_resets"] == facts["context_evictions"] == 1
    assert facts["tool_calls"] == {"run_command": 2}
    assert facts["repeated_identical_tool_calls"] == 1
    assert facts["tool_elapsed_seconds"] == 3 and facts["timed_tool_calls"] == 2
    assert facts["pending_tool_calls"] == facts["pending_model_dispatches"] == 0
    assert parse_json(files[0].data) == facts
    assert b"PRIVATE_MODEL_THOUGHTS" not in b"".join(file.data for file in files)
    assert b"response_ids" not in b"".join(file.data for file in files)


def test_efficiency_reaches_analyst_observation_without_changing_reward(tmp_path, publication):
    _, store, _, request = inputs(tmp_path, publication, ("pass", "no_submission", "unavailable"))
    bundle = export_feedback(request, store, approved_manifest=request.training_manifest, recorded_reports=True)
    passed, unsubmitted, unavailable = bundle.facts["observations"]
    assert passed["final_score"] == 1 and unsubmitted["final_score"] == unavailable["final_score"] == 0
    assert passed["efficiency"]["turns"] == 2
    assert passed["efficiency"]["usage_missing_dispatches"] == 2
    assert passed["efficiency"]["output_tokens"] is None
    assert unsubmitted["efficiency"]["turns"] == 1
    assert unavailable["efficiency"] is None
    files = {file.reference.path: file.data for file in bundle.files}
    path = next(p for p in passed["attachments"] if p.endswith("/efficiency.json"))
    assert parse_json(files[path]) == passed["efficiency"]
    assert b"missing usage is explicit, not zero" in files["brief.md"]


def test_absent_solver_history_is_not_zero_cost():
    assert execution_feedback(None, None) == (None, ())


def test_inline_usage_does_not_reread_large_response_tokens(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "journal", store)
    large = store.put(b"large native token record")
    first = journal.append((), "model_requested", {"call_id": "one", "request": large.as_dict()})
    journal.append((first,), "model_settled", {"call_id": "one", "outcome": "reply",
        "record": large.as_dict(), "raw": large.as_dict(),
        "usage": {"input_tokens": 123, "output_tokens": 45}}, (large,))
    head, _ = journal.seal()
    read = store.read

    def without_payload(ref):
        assert ref != large, "efficiency export must reuse the small producer counters"
        return read(ref)

    monkeypatch.setattr(store, "read", without_payload)
    facts, _ = execution_feedback(store, head, verify_artifacts=False)
    assert facts["output_tokens"] == 45 and facts["input_tokens"] == 123
    assert facts["turns"] is None and facts["usage_missing_dispatches"] == 0
