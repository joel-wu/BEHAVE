"""Shared model/tool loop: bounded context, full records, explicit final action.

Recovery replays committed outcomes, not external effects. An unresolved model
or tool request needs operator/backend reconciliation after fencing the old
worker. Transport retries belong to one logical turn. Training exclusions are
recorded on individual replies and never alter recorded history or tool effects.
Context overflow evicts old turns, or resets interactions when explicitly configured.
Local-file attempts replay completed records only, not lost unfinished workspaces.
"""

from __future__ import annotations

import time
from contextlib import nullcontext

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, FileData, read_verified
from behave.core.records import RecordError, canonical_json, metadata, parse_json, thaw_json

from .admission import ResourcePool
from .attempt import AttemptInterrupted, AttemptResult, AttemptSpec
from .journal import AttemptJournal
from .lifetime import AttemptControl, ExecutionWindow, current_window, execution_window
from .model import ModelBackend, ModelFailure, ModelReply, ModelRequest, ToolCall
from .prompting import render
from .replay import Tape
from .tools import Tool, ToolResult, ToolUnavailable, tool_invocation
from .workspace import Workspace


def _model_call(tape: Tape, backend: ModelBackend, request: ModelRequest) -> ModelReply | ModelFailure:
    store = tape.journal.objects
    request_ref = store.put(canonical_json(request.as_dict()))
    if tape.begin("model_requested", {"call_id": request.call_id, "request": request_ref.as_dict()}, (request_ref,)):
        try:
            result = backend.generate(request)
        except Exception as error:  # noqa: BLE001 -- broker boundary; never turns an unknown call into zero
            # No exception message: it may contain a broker URL or credential.
            result = ModelFailure("unknown", f"backend-raised-{type(error).__name__}")
        if type(result) not in (ModelReply, ModelFailure):
            result = ModelFailure("unknown", "backend-outcome-invalid")
        raw = store.put(result.raw)
        record = result.as_dict()
        encoded = store.put(canonical_json(record))
        event = tape.record("model_settled", {
            "call_id": request.call_id, "outcome": "reply" if type(result) is ModelReply else "failure",
            "record": encoded.as_dict(), "raw": raw.as_dict(),
            # Small analysis facts travel with the event, so later acquisition
            # need not reopen a response containing full native token arrays.
            "usage": record.get("usage", {}).get("normalized"),
        }, (encoded, raw))
    else:
        event = tape.take("model_settled")
    row = thaw_json(event.payload)
    if row["call_id"] != request.call_id:
        raise RecordError("attempt-call-mismatch", "model outcome belongs to another call")
    raw = read_verified(store, BlobRef.from_dict(row["raw"]))
    record = parse_json(read_verified(store, BlobRef.from_dict(row["record"])))
    if row["outcome"] == "reply":
        return ModelReply.from_dict(record, raw=raw)
    if row["outcome"] != "failure":
        raise RecordError("attempt-outcome-invalid", "unknown model outcome")
    return ModelFailure.from_dict(record, raw=raw)


def _admitted_call(tape: Tape, backend: ModelBackend, request: ModelRequest,
                   pool: ResourcePool | None) -> ModelReply | ModelFailure:
    window = current_window()
    live = tape.cursor == len(tape.events)
    permit = (pool.acquire(deadline=window.monotonic_deadline,
                           cancelled=window.control.cancelled)
              if pool is not None and live else nullcontext())
    try:
        with permit:
            return _model_call(tape, backend, request)
    except AttemptInterrupted as error:
        if live:
            tape.record("interrupted", {"code": error.code})
        raise


def _backoff(tape: Tape, window: ExecutionWindow, request: ModelRequest, seconds: int) -> str | None:
    if not seconds:
        return None
    if tape.cursor < len(tape.events):
        event = tape.take("model_backoff")
    else:
        event = tape.record("model_backoff", {"call_id": request.call_id, "seconds": seconds,
                                              "until": window.control.wall_clock() + seconds})
    if event.payload["call_id"] != request.call_id:
        raise RecordError("attempt-backoff-mismatch", "backoff belongs to another dispatch")
    # The recorded wait is authoritative even if today's default has changed.
    # Replay skips completed waits; an unfinished wait keeps its absolute end.
    if tape.cursor < len(tape.events):
        return window.checkpoint(tape)
    end = time.monotonic() + max(0., event.payload["until"] - window.control.wall_clock())
    while time.monotonic() < end:
        if reason := window.checkpoint(tape):
            return reason
        time.sleep(min(defaults.AGENT_RETRY_POLL_SECONDS, max(0., end - time.monotonic())))
    return None


# Only errors known to be attributable to a tool's arguments become model feedback.
# Store corruption, sandbox admission failure and unexpected exceptions interrupt
# the attempt; they must not become a normal no-submission zero.
_INPUT_ERRORS = {
    "record-fields-invalid", "record-array-invalid", "record-object-required", "record-json-invalid",
    "record-text-invalid", "record-version-unsupported", "record-key-duplicate", "record-number-invalid",
    "artifact-path-invalid", "artifact-path-conflict", "artifact-entry-missing", "command-args-invalid",
    "workspace-file-missing", "workspace-file-not-text", "workspace-content-not-text",
    "workspace-special-file",
    "workspace-read-only", "submission-limit-exceeded",
}


def _tool_call(tape: Tape, workspace: Workspace, call: ToolCall, tool: Tool | None, *, turn: int,
               mixed_terminal: bool) -> ToolResult:
    before, dependencies = (None, ()) if workspace.local else workspace.checkpoint()
    payload = {"turn": turn, "call": call.as_dict(), "before": None if before is None else before.as_dict()}
    if tape.begin("tool_requested", payload, dependencies):
        interrupted = None
        failure_artifacts = ()
        try:
            if mixed_terminal:
                result = ToolResult({"error": "terminal-action-must-be-the-only-call"})
            elif call.argument_error is not None:
                result = ToolResult({"error": call.argument_error})
            elif tool is None:
                result = ToolResult({"error": "tool-not-available", "name": call.name})
            else:
                with tool_invocation(tape.events[-1]):
                    result = tool.handler(workspace, thaw_json(call.arguments))
                if type(result) is not ToolResult or (result.terminal and not tool.terminal):
                    raise TypeError("tool returned an invalid outcome")
        except (ToolUnavailable, AttemptInterrupted) as error:
            if before is not None:
                workspace.restore(before)
            interrupted = error.code
            failure_artifacts = error.artifacts if isinstance(error, ToolUnavailable) else ()
        except RecordError as error:
            if before is not None:
                workspace.restore(before)
            if error.code in _INPUT_ERRORS:
                result = ToolResult({"error": error.code, "message": str(error)})
            else:
                interrupted = error.code
        except Exception as error:  # noqa: BLE001 -- tool boundary; recorded as interruption, not candidate failure
            if before is not None:
                workspace.restore(before)
            interrupted = f"tool-infrastructure-{type(error).__name__}"
        if interrupted is not None:
            event = tape.record("tool_settled", {
                "turn": turn, "call_id": call.call_id, "interrupted": interrupted,
                "after": None if before is None else before.as_dict(), "result": None, "terminal": False,
            }, (*dependencies, *failure_artifacts))
        else:
            after, closure = ((None, ()) if workspace.local and not result.terminal else workspace.checkpoint())
            result_ref = workspace.store.put(canonical_json(thaw_json(result.content)))
            event = tape.record("tool_settled", {
                "turn": turn, "call_id": call.call_id, "interrupted": None,
                "after": None if after is None else after.as_dict(),
                "result": result_ref.as_dict(), "terminal": result.terminal,
            }, (*closure, result_ref, *result.artifacts))
    else:
        event = tape.take("tool_settled")
    row = thaw_json(event.payload)
    if row["turn"] != turn or row["call_id"] != call.call_id:
        raise RecordError("attempt-call-mismatch", "tool outcome belongs to another call")
    if row["after"] is not None:
        workspace.restore(BlobRef.from_dict(row["after"]))
    if row["interrupted"] is not None:
        raise AttemptInterrupted(row["interrupted"])
    content = parse_json(read_verified(workspace.store, BlobRef.from_dict(row["result"])))
    result = ToolResult(content, event.artifacts, row["terminal"])
    if result.terminal and (tool is None or not tool.terminal or mixed_terminal):
        raise RecordError("attempt-terminal-invalid", "tool is not authorized to finish this attempt")
    return result


def run_attempt(*, journal: AttemptJournal, backend: ModelBackend, spec: AttemptSpec,
                initial_messages: tuple[dict, ...], initial_files: tuple[FileData, ...] = (),
                tools: tuple[Tool, ...] = (), control: AttemptControl | None = None,
                model_admission: ResourcePool | None = None,
                workspace: Workspace | None = None,
                runtime_turn_status: bool = True) -> AttemptResult:
    """Run or recover one attempt, with exclusive ownership supplied by the caller.

    A model message without tool calls ends normally. Only a registered terminal
    tool can submit; this layer never scores or submits the current directory.
    """
    from .retry import backoff_seconds, may_retry

    if type(tools) is not tuple or any(type(tool) is not Tool for tool in tools):
        raise TypeError("tools require a tuple of Tool")
    registry = {tool.name: tool for tool in tools}
    if spec.model_policy.get("admission") != (None if model_admission is None else model_admission.policy.identity):
        raise ValueError("model admission differs from the recorded policy")
    if len(registry) != len(tools):
        raise ValueError("tool names must be unique")
    declarations = tuple(metadata(tool.declaration()) for tool in tools)
    messages = [metadata(message) for message in initial_messages]
    tape = Tape(journal)
    native_objects = str(journal.objects.directory) if spec.model_policy.get("native_continuation") else None
    native_prefix = None
    # Preserve the exact request format when replaying an older completed run.
    # The ordinary solver renders its budget in every model input. External
    # workflows can retain their original prompts and own iteration protocol.
    if tape.events:
        recorded = parse_json(read_verified(journal.objects, BlobRef.from_dict(
            thaw_json(tape.events[0].payload["configuration"]))))
        runtime_turn_status = recorded.get("runtime_turn_status", False)
        native_objects = recorded.get("native_objects", native_objects)
    workspace = workspace if workspace is not None else Workspace(journal.objects, read_only_paths=spec.read_only_paths)
    workspace.seed(initial_files)
    initial, closure = workspace.checkpoint()
    configuration = journal.objects.put(canonical_json({
        "schema": "behave.attempt/5", "spec": spec.as_dict(),
        "messages": [thaw_json(message) for message in messages],
        "tools": [thaw_json(tool) for tool in declarations], "workspace": initial.as_dict(),
        **({"workspace_storage": "local-final-only"} if workspace.local else {}),
        **({"runtime_turn_status": True} if runtime_turn_status else {}),
        **({"native_objects": native_objects} if native_objects is not None else {}),
    }))
    tape.record("initialized", {"configuration": configuration.as_dict()}, (configuration, *closure))
    if workspace.local and len(tape.events) > 1 and tape.events[-1].kind != "terminal":
        # No command is repeated to recreate lost local effects. A new candidate
        # needs its own attempt identity; completed attempts still replay below.
        last = tape.events[-1]
        if last.kind == "tool_settled" and last.payload["interrupted"] is not None:
            raise AttemptInterrupted(last.payload["interrupted"])
        raise AttemptInterrupted("local-workspace-unavailable")
    window = ExecutionWindow(control or AttemptControl())

    def finish(reason: str, turns: int, final: ToolResult | None = None) -> AttemptResult:
        if workspace.local and final is None:
            workspace.archive(())
        snapshot, dependencies = workspace.checkpoint()
        payload = {"reason": reason, "turns": turns, "workspace": snapshot.as_dict(),
                   "final_action": None if final is None else thaw_json(final.content)}
        event = tape.record("terminal", payload, (*dependencies, *(() if final is None else final.artifacts)))
        tape.finish()
        return AttemptResult(reason, turns, snapshot, None if final is None else final.content,
                             BlobRef.of(event.to_bytes()))

    def make_context_room(request: ModelRequest, turn_status=None) -> bool:
        nonlocal native_prefix
        # Completed attempts from the earlier stop-on-overflow policy stay
        # completed during recovery. Do not generate beyond their terminal.
        if tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "terminal":
            return False
        start = len(initial_messages)
        if spec.context_reset_message is not None:
            # An oversized opening/reminder cannot be fixed by resetting again.
            # Tool effects and sampled token records live outside this history.
            if not any(message["role"] == "assistant" for message in messages[start:]):
                return False
            tape.record("context_reset", {
                "call_id": request.call_id, "removed_messages": len(messages) - start,
            })
            messages[start:] = [metadata({"role": "user", "content": spec.context_reset_message})]
            if turn_status is not None:
                messages.append(turn_status)
            native_prefix = None
            return True
        # Pin the opening and retain the latest interaction, including its tool
        # feedback. The runtime status belongs to the following assistant turn,
        # so remove it with that turn, not with the preceding tool results.
        assistants = [i for i in range(start, len(messages)) if messages[i]["role"] == "assistant"]
        if len(assistants) < 2:
            return False
        end = assistants[1] - int(runtime_turn_status)
        tape.record("context_evicted", {
            "call_id": request.call_id, "removed_messages": end - start,
        })
        del messages[start:end]
        native_prefix = None
        return True

    for turn in range(spec.max_turns):
        turn_status = None
        if runtime_turn_status:
            # Append once per logical turn. Keep it in subsequent history so
            # the previous request/reply prefix remains cacheable. Retries do
            # not duplicate it, and eviction removes it with its own turn.
            turn_status = metadata({"role": "user", "content": render(
                "shared/runtime/turn_status", current_turn=str(turn + 1), max_turns=str(spec.max_turns),
                remaining_turns=str(spec.max_turns - turn),
            )})
            messages.append(turn_status)
        dispatch = retries = 0
        while True:
            if reason := window.checkpoint(tape):
                return finish(reason, turn)
            request = ModelRequest(f"{spec.attempt_id}:{turn}:{dispatch}", turn, dispatch,
                                   tuple(messages), declarations, spec.model_policy, spec.request_timeout_seconds,
                                   spec.context_tokens, native_prefix)
            with execution_window(window):
                reply = _admitted_call(tape, backend, request, model_admission)
            if type(reply) is ModelReply:
                break
            if reply.kind == "context_limit":
                if not make_context_room(request, turn_status):
                    return finish("context_limit", turn)
                dispatch += 1
                continue
            if (not may_retry(reply, request, uncertain_generation=spec.retry_uncertain_generation)
                    or retries == spec.max_retries):
                raise AttemptInterrupted(f"model-{reply.kind}:{reply.code}")
            if reason := _backoff(tape, window, request, backoff_seconds(reply, request, retry_index=retries)):
                return finish(reason, turn)
            retries += 1
            dispatch += 1
        messages.append(metadata(reply.message()))
        if native_objects is not None:
            tokens = reply.token_data
            native_prefix = ({"objects": native_objects,
                "response": thaw_json(tape.events[tape.cursor - 1].payload["record"]),
                "message_count": len(messages)} if tokens is not None
                and tokens.get("schema") == "behave.token-trace/1" else None)
        if reason := window.checkpoint(tape):
            return finish(reason, turn + 1)
        reset_after_reply = reply.finish_reason == "context_limit" and spec.context_reset_message is not None
        if reply.finish_reason == "context_limit" and not reset_after_reply and not make_context_room(request):
            return finish("context_limit", turn + 1)
        if not reply.calls:
            if reply.finish_reason in {"length", "context_limit"}:
                if reset_after_reply:
                    make_context_room(request)
                else:
                    messages.append(metadata({"role": "user", "content":
                        render("shared/runtime/output_limit")}))
                continue
            return finish("stopped", turn + 1)
        mixed_terminal = len(reply.calls) != 1 and any(
            call.name in registry and registry[call.name].terminal for call in reply.calls)
        for call in reply.calls:
            if reason := window.checkpoint(tape):
                return finish(reason, turn + 1)
            with execution_window(window):
                result = _tool_call(tape, workspace, call, registry.get(call.name), turn=turn,
                                    mixed_terminal=mixed_terminal)
            messages.append(metadata({"role": "tool", "call_id": call.call_id,
                                      "content": thaw_json(result.content)}))
            if result.terminal:
                return finish("final_action", turn + 1, result)
        if reset_after_reply:
            # Settle tool feedback before discarding the exchange. The reply
            # protocol marks clipped calls invalid; this does not authorize
            # them. Completed writes from earlier turns remain in the workspace.
            make_context_room(request)
    return finish("turn_limit", spec.max_turns)
