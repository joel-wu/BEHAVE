from __future__ import annotations

import subprocess
from dataclasses import replace

import pytest

from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.loop import run_attempt
from behave.agent.model import Charge, ModelFailure, ModelReply, ToolCall
from behave.agent.tools import Tool, ToolResult, ToolUnavailable, file_tools, object_schema
from behave.agent.trajectory import trajectory
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.task_execution.capture import load_submission
from behave.task_execution.solver import run_solver
from behave.task_execution.submission import ArtifactSelection, SubmissionRequest


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if not self.replies:
            pytest.fail("unexpected additional model request")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class Interrupted(BaseException):
    """Simulate coordinator loss, not a model failure."""


def call(name, arguments, key="call"):
    return ModelReply("", (ToolCall(key, name, arguments),))


def setup(tmp_path):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("attempt-1", "test-role", {"model": "fake", "temperature": 0})
    return journal, spec


def local_shell():
    """Trusted fixture commands only; namespace isolation has separate native tests."""
    def execute(workspace, arguments):
        result = subprocess.run(["bash", "-c", arguments["command"]], cwd=workspace.directory,
                                capture_output=True, text=True, check=False)
        return ToolResult({"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr})

    return Tool("run_command", "fixture shell", object_schema({"command": {"type": "string"}}),
                execute, "fixture-shell")


def run(journal, spec, backend, tools=None):
    return run_attempt(journal=journal, backend=backend, spec=spec,
                       initial_messages=({"role": "user", "content": "work"},),
                       tools=file_tools() if tools is None else tools)


def test_attempt_edits_full_history_and_cold_resume_do_not_repeat_calls(tmp_path):
    journal, spec = setup(tmp_path)
    backend = Scripted(call("write_file", {"path": "model.py", "content": "x=1\n"}),
                       call("read_file", {"path": "model.py"}), ModelReply("done"))
    result = run(journal, spec, backend)
    assert result.reason == "stopped" and result.turns == 3
    assert result.final_action is None
    assert len(backend.requests) == 3
    assert len(backend.requests[-1].messages) == 8
    assert backend.requests[-1].messages[-2]["content"]["content"] == "x=1\n"
    cloned_journal = AttemptJournal(journal.directory, DirectoryStore(journal.objects.directory))
    no_calls = Scripted()
    assert run(cloned_journal, spec, no_calls) == result
    assert no_calls.requests == []
    restored = Workspace(journal.objects)
    restored.restore(result.workspace)
    assert restored.read("model.py") == b"x=1\n"


def test_runtime_status_uses_actual_budget_on_first_and_last_turn(tmp_path):
    journal, spec = setup(tmp_path)
    spec = replace(spec, max_turns=2)
    backend = Scripted(ModelReply("", (
        ToolCall("a", "write_file", {"path": "a", "content": "a"}),
        ToolCall("b", "write_file", {"path": "b", "content": "b"}),
    )), ModelReply("done"))
    run(journal, spec, backend)
    for number, request in enumerate(backend.requests, 1):
        status = request.messages[-1]
        assert status["role"] == "user"
        assert f"turn {number} of 2. Model turns remaining (including this turn): {3 - number}." in status["content"]
        assert "Tool calls remaining" not in status["content"]
        assert "wall-time" not in status["content"]
        assert "Make the final submission within the remaining turns." in status["content"]
        assert sum("Runtime status:" in str(m["content"]) for m in request.messages) == number
    # Preserve the previous input verbatim before appending its reply and the
    # next status. Replacing old status messages would invalidate prefix cache.
    first, second = backend.requests
    assert second.messages[:len(first.messages)] == first.messages
    assert run(journal, spec, Scripted()).reason == "stopped"


def test_legacy_completed_requests_without_runtime_status_still_replay(tmp_path):
    from behave.core.records import canonical_json, thaw_json

    journal, spec = setup(tmp_path)
    run(journal, spec, Scripted(ModelReply("done")))
    legacy = AttemptJournal(tmp_path / "legacy-events", journal.objects)
    prefix = []
    for event in journal.load():
        payload, artifacts = thaw_json(event.payload), event.artifacts
        key = {"initialized": "configuration", "model_requested": "request"}.get(event.kind)
        if key is not None:
            old_ref = BlobRef.from_dict(payload[key])
            record = parse_json(journal.objects.read(old_ref))
            if event.kind == "initialized":
                record.pop("runtime_turn_status")
            else:
                record["messages"].pop()
            new_ref = journal.objects.put(canonical_json(record))
            payload[key] = new_ref.as_dict()
            artifacts = tuple(new_ref if ref == old_ref else ref for ref in artifacts)
        prefix.append(legacy.append(prefix, event.kind, payload, artifacts))
    assert run(legacy, spec, Scripted()).reason == "stopped"


def test_transport_retries_are_one_turn_and_all_usage_is_retained(tmp_path):
    journal, spec = setup(tmp_path)
    backend = Scripted(
        ModelFailure("retryable", "rate-limit", {"request_count": 1}, b"retry-raw", Charge("0.01", "USD", "r1")),
        ModelReply("done", usage={"input_tokens": 3}, raw=b"reply-raw", charge=Charge("0.02", "USD", "r1")),
    )
    result = run(journal, spec, backend)
    assert result.turns == 1
    assert [(r.turn, r.dispatch) for r in backend.requests] == [(0, 0), (0, 1)]
    assert backend.requests[0].messages == backend.requests[1].messages
    assert "turn 1 of 60" in backend.requests[1].messages[-1]["content"]
    view = trajectory(journal)
    assert len(view.turns) == 1 and len(view.charges) == 2
    assert view.cost_by_currency()["USD"] == "0.03"
    assert view.unpriced_dispatches == view.unresolved_dispatches == 0
    assert journal.objects.read(view.turns[0].raw_response) == b"reply-raw"
    assert view.turns[0].training_status == "unavailable"  # no invented tokens


def test_context_eviction_keeps_opening_latest_turn_and_full_trace(tmp_path):
    from types import SimpleNamespace

    from behave.agent.providers.wire import history

    journal, spec = setup(tmp_path)
    spec = replace(spec, max_retries=0)
    opening = ({"role": "system", "content": "joint development"},
               {"role": "user", "content": "task and public documents"})
    batch = ModelReply("first", (
        ToolCall("write-a", "write_file", {"path": "a", "content": "a"}),
        ToolCall("write-b", "write_file", {"path": "b", "content": "b"}),
    ), continuation={"reasoning_content": "first reasoning"})
    backend = Scripted(
        batch,
        ModelReply("partial", finish_reason="length", training_exclusion="output-limit"),
        call("read_file", {"path": "b"}, "read-b"),
        ModelFailure("context_limit", "too-long"),
        ModelFailure("context_limit", "still-too-long"),
        ModelReply("done"),
    )
    kwargs = {"journal": journal, "spec": spec, "initial_messages": opening, "tools": file_tools()}
    result = run_attempt(backend=backend, **kwargs)
    assert result.reason == "stopped" and result.turns == 4
    assert [(r.turn, r.dispatch) for r in backend.requests[-3:]] == [(3, 0), (3, 1), (3, 2)]
    latest = backend.requests[-1]
    assert backend.requests[-3].messages[-1] == backend.requests[-2].messages[-1] == latest.messages[-1]
    assert "turn 4 of 60. Model turns remaining (including this turn): 57." in latest.messages[-1]["content"]
    assert latest.messages[:2] == opening
    assert [m["role"] for m in latest.messages] == ["system", "user", "user", "assistant", "tool", "user"]
    assert "Runtime status: turn 3" in latest.messages[2]["content"]
    assert latest.messages[-2]["call_id"] == "read-b"
    assert latest.messages[-2]["content"]["content"] == "b"
    # The real wire validator still accepts every request: no orphaned calls.
    endpoint = SimpleNamespace(identity=None, model="fake")
    for request in backend.requests:
        history(endpoint, request)
    events = [e for e in journal.load() if e.kind == "context_evicted"]
    assert [e.payload["removed_messages"] for e in events] == [4, 3]
    view = trajectory(journal)
    assert [t.turn for t in view.turns] == [0, 1, 2, 3]
    assert view.turns[1].exclusion == "output-limit"
    assert parse_json(journal.objects.read(view.turns[0].response))["continuation"] == {
        "reasoning_content": "first reasoning"}
    assert parse_json(journal.objects.read(view.turns[-1].request)) == latest.as_dict()
    assert not any(e.kind == "model_backoff" for e in journal.load())
    assert run_attempt(backend=Scripted(), **kwargs) == result
    restored = Workspace(journal.objects)
    restored.restore(result.workspace)
    assert restored.read("a") == b"a" and restored.read("b") == b"b"


def test_context_corrections_do_not_consume_transport_retries(tmp_path, monkeypatch):
    from behave.agent import loop

    journal, spec = setup(tmp_path)
    spec = replace(spec, max_retries=1)
    waits = []
    monkeypatch.setattr(loop, "_backoff", lambda tape, window, request, seconds:
                        waits.append((request.dispatch, seconds)))
    backend = Scripted(
        call("write_file", {"path": "a", "content": "a"}, "a"),
        call("read_file", {"path": "a"}, "b"),
        ModelFailure("context_limit", "too-long"),
        ModelFailure("retryable", "rate-limit"),
        ModelReply("done"),
    )
    assert run(journal, spec, backend).reason == "stopped"
    assert waits == [(1, 1)]  # First transport retry, not the second dispatch's backoff.
    assert backend.requests[-1].messages == backend.requests[-2].messages


@pytest.mark.parametrize("with_call", [False, True])
def test_context_limited_reply_evicts_then_continues_without_tool_effects(tmp_path, with_call):
    journal, spec = setup(tmp_path)
    incomplete = (ToolCall("partial", "write_file", {}, raw_arguments="{", argument_error="truncated"),) if with_call else ()
    backend = Scripted(
        call("write_file", {"path": "a", "content": "a"}, "a"),
        ModelReply("partial", incomplete, finish_reason="context_limit", training_exclusion="context-limit"),
        ModelReply("done"),
    )
    assert run(journal, spec, backend).reason == "stopped"
    assert [r.turn for r in backend.requests] == [0, 1, 2]
    assert len(backend.requests[-1].messages) == 5
    assert backend.requests[-1].messages[-2]["role"] == ("tool" if with_call else "user")
    assert [t.exclusion for t in trajectory(journal).turns] == [None, "context-limit", None]
    assert [e.payload["removed_messages"] for e in journal.load() if e.kind == "context_evicted"] == [3]


def test_context_limit_keeps_irreducible_latest_tool_feedback(tmp_path):
    journal, spec = setup(tmp_path)
    backend = Scripted(call("write_file", {"path": "large", "content": "data"}),
                       ModelFailure("context_limit", "too-long"))
    result = run(journal, spec, backend)
    assert result.reason == "context_limit" and result.turns == 1
    assert not any(e.kind == "context_evicted" for e in journal.load())


def test_completed_legacy_context_limit_is_not_reopened(tmp_path):
    journal, spec = setup(tmp_path)
    backend = Scripted(call("write_file", {"path": "a", "content": "a"}),
                       call("read_file", {"path": "a"}),
                       ModelFailure("context_limit", "too-long"), ModelReply("done"))
    run(journal, spec, backend)
    # Historical policy stopped at the same provider rejection. Build that
    # completed fixture without changing the original journal or its objects.
    legacy = AttemptJournal(tmp_path / "legacy-events", journal.objects)
    prefix = []
    for event in journal.load():
        if event.kind == "context_evicted":
            break
        prefix.append(legacy.append(prefix, event.kind, dict(event.payload), event.artifacts))
    terminal = journal.load()[-1]
    payload = {**terminal.payload, "reason": "context_limit", "turns": 2}
    legacy.append(prefix, "terminal", payload, terminal.artifacts)
    result = run(legacy, spec, Scripted())
    assert result.reason == "context_limit" and result.turns == 2


def test_turn_exclusion_preserves_other_turns_history_and_file_effects(tmp_path):
    journal, spec = setup(tmp_path)
    first = replace(call("write_file", {"path": "a", "content": "a"}), token_data={"ids": [1, 2]})
    long = replace(call("write_file", {"path": "b", "content": "b"}), content="long " * 500,
                   token_data={"ids": [3, 4]}, training_exclusion="context-too-long")
    last = ModelReply("done", token_data={"ids": [5]})
    backend = Scripted(first, long, last)
    result = run(journal, spec, backend)
    view = trajectory(journal)
    assert [turn.training_status for turn in view.turns] == ["recorded", "excluded", "recorded"]
    assert backend.requests[-1].messages[-3]["content"] == long.content
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert workspace.read("b") == b"b"
    assert len(view.turns) == result.turns == 3


def test_turn_limit_is_sixty_and_tools_do_not_add_turns(tmp_path):
    journal, spec = setup(tmp_path)
    backend = Scripted(*(call("list_files", {}) for _ in range(60)))
    result = run(journal, spec, backend)
    assert result.reason == "turn_limit" and result.turns == 60
    assert len(trajectory(journal).turns) == 60
    assert len(backend.requests[-1].messages) == 179


def test_last_turn_allows_terminal_action(tmp_path):
    journal, spec = setup(tmp_path)
    end = Tool("end", "finish", object_schema({}), lambda *_: ToolResult({}, terminal=True), "1", terminal=True)
    result = run(journal, replace(spec, max_turns=1), Scripted(call("end", {})), tools=(end,))
    assert result.reason == "final_action"


@pytest.mark.parametrize("kind", ["unknown", "permanent", "retryable"])
def test_infrastructure_failure_never_becomes_a_no_submission_zero(tmp_path, kind):
    journal, spec = setup(tmp_path)
    spec = replace(spec, max_retries=0)
    with pytest.raises(AttemptInterrupted):
        run(journal, spec, Scripted(ModelFailure(kind, "transport")))
    assert journal.load()[-1].kind == "model_settled"
    with pytest.raises(AttemptInterrupted):
        run(journal, spec, Scripted())


def test_unknown_model_call_is_not_reissued_after_restart(tmp_path):
    journal, spec = setup(tmp_path)
    with pytest.raises(Interrupted):
        run(journal, spec, Scripted(Interrupted()))
    assert journal.load()[-1].kind == "model_requested"
    assert trajectory(journal).unresolved_dispatches == 1
    with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
        run(journal, spec, Scripted())


def test_unknown_tool_effect_is_not_reissued_after_restart(tmp_path):
    journal, spec = setup(tmp_path)
    effects = []

    def interrupted(workspace, arguments):
        effects.append(True)
        workspace.write("a", b"modified")
        raise Interrupted()

    tools = (Tool("crash", "crash", object_schema({}), interrupted, "crash/1"),)
    with pytest.raises(Interrupted):
        run(journal, spec, Scripted(call("crash", {})), tools)
    with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
        run(journal, spec, Scripted(), tools)
    assert effects == [True]


@pytest.mark.parametrize("boundary", ["model_settled", "tool_settled"])
def test_recovery_after_durable_response_continues_without_repeating_it(tmp_path, monkeypatch, boundary):
    journal, spec = setup(tmp_path)
    original = journal.append
    crashed = False

    def append(*args, **kwargs):
        nonlocal crashed
        event = original(*args, **kwargs)
        if event.kind == boundary and not crashed:
            crashed = True
            raise Interrupted()
        return event

    monkeypatch.setattr(journal, "append", append)
    backend = Scripted(call("write_file", {"path": "a", "content": "one"}))
    with pytest.raises(Interrupted):
        run(journal, spec, backend)
    monkeypatch.setattr(journal, "append", original)
    recovered_backend = Scripted(ModelReply("done"))
    result = run(journal, spec, recovered_backend)
    assert result.turns == 2 and len(recovered_backend.requests) == 1
    assert sum(event.kind == "tool_requested" for event in journal.load()) == 1


def test_changed_policy_cannot_resume_old_attempt(tmp_path):
    journal, spec = setup(tmp_path)
    run(journal, spec, Scripted(ModelReply("done")))
    with pytest.raises(RecordError, match="replay differs"):
        run(journal, replace(spec, max_turns=61), Scripted())


def test_tool_errors_are_feedback_but_storage_and_sandbox_failures_are_not(tmp_path):
    journal, spec = setup(tmp_path)
    backend = Scripted(call("write_file", {"path": "../escape", "content": "no"}), ModelReply("done"))
    run(journal, spec, backend)
    assert backend.requests[-1].messages[-2]["content"]["error"] == "artifact-path-invalid"
    assert backend.requests[-1].messages[-2]["content"]["message"]

    def broken(workspace, arguments):
        raise RecordError("content-identity-mismatch", "corrupt store")

    journal2 = AttemptJournal(tmp_path / "other-events", journal.objects)
    tools = (Tool("broken", "broken", object_schema({}), broken, "broken/1"),)
    with pytest.raises(AttemptInterrupted, match="content-identity-mismatch"):
        run(journal2, spec, Scripted(call("broken", {})), tools)


def test_tool_argument_feedback_identifies_missing_and_unexpected_fields(tmp_path):
    journal, spec = setup(tmp_path)
    backend = Scripted(call("write_file", {"path": "model.py", "text": "x=1"}), ModelReply("done"))
    run(journal, spec, backend)
    feedback = backend.requests[-1].messages[-2]["content"]
    assert feedback["error"] == "record-fields-invalid"
    assert "Missing: ['content']" in feedback["message"]
    assert "Unexpected: ['text']" in feedback["message"]


@pytest.mark.parametrize("content", [[], {"text": "\ud800"}])
def test_invalid_tool_result_is_infrastructure_not_bad_model_arguments(tmp_path, content):
    journal, spec = setup(tmp_path)
    effects = []

    def broken(workspace, arguments):
        effects.append(True)
        workspace.write("partial", b"must roll back")
        return ToolResult(content)

    tools = (Tool("broken", "broken result", object_schema({}), broken, "fixture/1"),)
    reply = replace(call("broken", {}), charge=Charge("0.1", "USD", "fixture"))
    backend = Scripted(reply, ModelReply("done"))
    with pytest.raises(AttemptInterrupted, match="tool-infrastructure-TypeError"):
        run(journal, spec, backend, tools)
    assert len(backend.requests) == 1
    assert effects == [True]
    settled = journal.load()[-1]
    assert settled.kind == "tool_settled" and settled.payload["result"] is None
    restored = Workspace(journal.objects)
    restored.restore(BlobRef.from_dict(dict(settled.payload["after"])))
    assert not restored.snapshot().files
    assert trajectory(journal).cost_by_currency() == {"USD": "0.1"}
    with pytest.raises(AttemptInterrupted, match="tool-infrastructure-TypeError"):
        run(journal, spec, Scripted(), tools)
    assert effects == [True]


def test_tool_interruption_keeps_its_reason_and_is_not_reexecuted(tmp_path):
    journal, spec = setup(tmp_path)
    effects = []

    def cancelled(workspace, arguments):
        effects.append(True)
        workspace.write("partial", b"must roll back")
        raise AttemptInterrupted("resource-cancelled")

    tools = (Tool("cancelled", "cancelled tool", object_schema({}), cancelled, "fixture/1"),)
    with pytest.raises(AttemptInterrupted, match="^resource-cancelled$"):
        run(journal, spec, Scripted(call("cancelled", {})), tools)
    settled = journal.load()[-1]
    assert settled.kind == "tool_settled" and settled.payload["interrupted"] == "resource-cancelled"
    restored = Workspace(journal.objects)
    restored.restore(BlobRef.from_dict(dict(settled.payload["after"])))
    assert not restored.snapshot().files
    with pytest.raises(AttemptInterrupted, match="^resource-cancelled$"):
        run(journal, spec, Scripted(), tools)
    assert effects == [True]


def test_tool_unavailable_still_retains_its_failure_artifacts(tmp_path):
    journal, spec = setup(tmp_path)
    evidence = journal.objects.put(b"fixture cleanup report")
    effects = []

    def unavailable(workspace, arguments):
        effects.append(True)
        raise ToolUnavailable("fixture-cleanup-incomplete", artifacts=(evidence,))

    tools = (Tool("unavailable", "unavailable tool", object_schema({}), unavailable, "fixture/1"),)
    for backend in (Scripted(call("unavailable", {})), Scripted()):
        with pytest.raises(AttemptInterrupted, match="fixture-cleanup-incomplete"):
            run(journal, spec, backend, tools)
    assert effects == [True]
    assert evidence in journal.load()[-1].artifacts
    assert journal.objects.read(evidence) == b"fixture cleanup report"


def test_terminal_must_stand_alone_and_text_never_implies_submission(tmp_path):
    journal, spec = setup(tmp_path)
    effects = []

    def submit(workspace, arguments):
        effects.append(True)
        return ToolResult({"done": True}, terminal=True)

    tools = (*file_tools(), Tool("submit", "submit", object_schema({}), submit, "submit/1", terminal=True))
    mixed = ModelReply("", (ToolCall("a", "write_file", {"path": "x", "content": "x"}),
                             ToolCall("b", "submit", {})))
    backend = Scripted(mixed, ModelReply("I submitted everything"))
    result = run(journal, spec, backend, tools)
    assert result.reason == "stopped" and effects == []
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert not workspace.snapshot().files


def test_solver_freezes_selected_files_and_missing_tb_does_not_block(publication, tmp_path):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal, spec = setup(tmp_path)
    spec = replace(spec, role="solver")
    request = SubmissionRequest(ArtifactSelection(("model.py", "helper.py"), "model.py", "M"),
                                ArtifactSelection(("top.sv",), "top.sv", "top"))
    backend = Scripted(call("run_command", {"command": "printf 'class M: pass' > model.py"}),
                       call("run_command", {"command": "printf 'module top; endmodule' > top.sv"}),
                       call("submit", request.as_arguments()))
    result = run_solver(task=public, spec=spec, backend=backend, journal=journal, command_tool=local_shell())
    assert [tool["name"] for tool in backend.requests[0].tools] == ["run_command", "submit"]
    assert result.status == "awaiting_judgment" and result.score is None
    captured = load_submission(journal.objects, result.submission)
    assert captured.behavior.missing == ("helper.py",) and captured.behavior.artifact is None
    assert captured.rtl.artifact.entry_symbol == "top"
    assert captured.testbench.selection is None
    assert b"PRIVATE" not in repr(backend.requests).encode()
    assert captured.revision == public.revision
    assert not any(event.kind.startswith("judge") for event in journal.load())
    assert run_solver(task=public, spec=spec, backend=Scripted(), journal=journal, command_tool=local_shell()) == result


@pytest.mark.parametrize("reply,reason,turns", [
    (ModelReply("no submission"), "stopped", 1),
    (ModelFailure("context_limit", "too-long"), "context_limit", 0),
    (ModelReply("partial", finish_reason="context_limit", training_exclusion="context-limit"), "context_limit", 1),
])
def test_solver_normal_no_submission_is_zero_without_fake_component_scores(publication, tmp_path, reply, reason, turns):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal, spec = setup(tmp_path)
    result = run_solver(task=public, spec=replace(spec, role="solver"), backend=Scripted(reply), journal=journal)
    assert result.status == "no_submission" and result.score == 0
    assert result.submission is None
    terminal = journal.load()[-1]
    assert terminal.payload["final_action"] is None
    assert "component_scores" not in terminal.payload
    assert result.attempt.reason == reason and result.attempt.turns == turns


def test_request_persisted_before_dispatch_and_reply_before_next_tool(tmp_path):
    journal, spec = setup(tmp_path)

    class CheckBroker:
        def generate(self, request):
            pending = journal.load()[-1]
            assert pending.kind == "model_requested"
            ref = BlobRef.from_dict(dict(pending.payload["request"]))
            assert parse_json(journal.objects.read(ref))["call_id"] == request.call_id
            return ModelReply("done")

    run(journal, spec, CheckBroker())


def test_records_defensively_copy_nested_broker_data():
    usage, arguments, tokens = {"details": [1]}, {"payload": [2]}, {"ids": [3]}
    reply = ModelReply("", (ToolCall("id", "tool", arguments),), usage, token_data=tokens)
    usage["details"].append(9)
    arguments["payload"].append(9)
    tokens["ids"].append(9)
    assert reply.usage["details"] == (1,)
    assert reply.calls[0].arguments["payload"] == (2,)
    assert reply.token_data["ids"] == (3,)
    with pytest.raises(RecordError):
        ModelReply("", (ToolCall("id", "a", {}), ToolCall("id", "b", {})))


def test_unknown_backend_exception_is_not_free_and_does_not_leak_its_message(tmp_path):
    journal, spec = setup(tmp_path)
    with pytest.raises(AttemptInterrupted, match="unknown"):
        run(journal, spec, Scripted(RuntimeError("Authorization: private-test-key")))
    assert trajectory(journal).unresolved_dispatches == 1
    assert trajectory(journal).unpriced_dispatches == 1
    assert not trajectory(journal).cost_by_currency()
    assert b"private-test-key" not in b"".join(file.read_bytes() for file in journal.objects.directory.iterdir())


def test_broker_reported_parse_failure_keeps_its_usage_raw_bytes_and_charge(tmp_path):
    journal, spec = setup(tmp_path)
    failure = ModelFailure("permanent", "response-not-parseable", {"output_tokens": 17},
                           b"not-json-provider-response", Charge("0.0003", "USD", "fixture-pricing"))
    with pytest.raises(AttemptInterrupted):
        run(journal, spec, Scripted(failure))
    view = trajectory(journal)
    assert not view.turns and view.cost_by_currency()["USD"] == "0.0003"
    assert view.unresolved_dispatches == view.unpriced_dispatches == 0
    settled = journal.load()[-1]
    record = parse_json(journal.objects.read(BlobRef.from_dict(dict(settled.payload["record"]))))
    assert record["usage"]["output_tokens"] == 17
    assert journal.objects.read(BlobRef.from_dict(dict(settled.payload["raw"]))) == failure.raw
