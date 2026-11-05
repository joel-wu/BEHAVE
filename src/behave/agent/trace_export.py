"""Readable projections of committed agent journals, independent of Eval.

Exports are observations, never completion receipts or execution inputs.
The source journal and its objects remain authoritative and are not modified.
"""

import json

from behave.runtime.records import write_bytes, write_json

def reasoning_blocks(continuation):
    """Display provider-returned reasoning without decoding opaque state."""
    for item in continuation.get("items", ()):
        if item["type"] == "reasoning":
            summaries = [part["text"] for part in item.get("summary", ())
                         if part["type"] == "summary_text" and part["text"]]
            for text in summaries:
                yield "reasoning summary", text
            if not summaries:
                yield "reasoning", "No readable reasoning was returned. Native state is retained in JSON."
        elif item["type"] == "thinking" and item["thinking"]:
            yield "thinking", item["thinking"]
        elif item["type"] == "redacted_thinking":
            yield "thinking", "The provider redacted this thinking block. Native state is retained in JSON."
    details = continuation.get("reasoning_details", ())
    for item in details:
        if item.get("text"):
            yield "reasoning", item["text"]
        elif item.get("summary"):
            yield "reasoning summary", item["summary"]
        else:
            yield "reasoning", "Opaque reasoning state is retained in JSON."
    for name in (() if details else ("reasoning_content", "reasoning")):
        if continuation.get(name):
            yield "reasoning", continuation[name]


def export_candidate(directory, destination, *, markdown="trace.md", journal="solver"):
    """Export complete committed model requests, replies and tool results.

    Raw provider payloads are separate files. The Markdown contains all visible
    messages, calls and tool output, without repeating every cumulative request.
    """
    from behave.agent.run_store import JournalDirectory

    parts = (journal,) if isinstance(journal, str) else tuple(journal)
    if not directory.joinpath(*parts).is_dir():
        return {"status": "waiting", "turns": 0, "tool_calls": 0}
    store = JournalDirectory(directory)
    events = store.journal(*parts).load(verify_artifacts=False)
    return _export_events(store, events, destination, markdown=markdown)


def _export_events(store, events, destination, *, markdown):
    """Render the same lossless trace for one-shot and incremental observers."""
    from behave.core.artifacts import BlobRef
    from behave.core.records import thaw_json

    destination.mkdir(parents=True, exist_ok=True)
    transcript = ["# Interactive trace\n"]
    if markdown != "trace.md":
        transcript.append("This readable export is a snapshot. During execution, the "
                          "[original transcript](trace.md) is updated continuously.\n")
    seen, turns, calls = 0, 0, 0
    status = "running"
    runtime_turn_status = False

    def load(ref):
        return json.loads(store.objects.read(BlobRef.from_dict(ref)))

    def block(title, value, *, language=""):
        text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
        fence = "`" * (max((len(word) for word in text.split() if set(word) == {"`"}), default=2) + 2)
        transcript.append(f"\n## {title}\n\n{fence}{language}\n{text}\n{fence}\n")

    for event in events:
        row = thaw_json(event.payload)
        prefix = f"{event.sequence:04d}"
        path = destination / (prefix + "_" + event.kind + ".json")
        if not path.exists():
            write_json(path, json.loads(event.to_bytes()))
        if event.kind == "initialized":
            config = load(row["configuration"])
            runtime_turn_status = config.get("runtime_turn_status", False)
            write_json(destination / "opening.json", config["messages"])
            write_json(destination / "tools.json", config["tools"])
            for message in config["messages"]:
                block(message["role"], message["content"])
        elif event.kind == "model_requested":
            request = load(row["request"])
            seen = request["turn"] + 1
            if not (destination / f"{prefix}_request.json").exists():
                write_json(destination / f"{prefix}_request.json", request)
            if runtime_turn_status:
                block(f"Turn {seen}: runtime status", request["messages"][-1]["content"])
        elif event.kind == "model_settled":
            reply = load(row["record"])
            if not (destination / f"{prefix}_reply.json").exists():
                write_json(destination / f"{prefix}_reply.json", reply)
            # Reply and raw payload can be interrupted independently. A size
            # check also repairs legacy partial raw writes without rereading
            # complete payloads. These projections are not integrity receipts.
            raw_path = destination / f"{prefix}_provider_raw.json"
            raw_ref = BlobRef.from_dict(row["raw"])
            try:
                exported_size = raw_path.stat().st_size
            except FileNotFoundError:
                exported_size = None
            if exported_size != raw_ref.size_bytes:
                write_bytes(raw_path, store.objects.read(raw_ref))
            turns += row["outcome"] == "reply"
            if row["outcome"] == "reply":
                for label, text in reasoning_blocks(reply.get("continuation", {})):
                    block(f"Turn {seen}: {label}", text)
                if reply["content"]:
                    block(f"Turn {seen}: assistant", reply["content"])
                block(f"Turn {seen}: response metadata", {
                    key: reply[key] for key in ("finish_reason", "usage", "charge", "training_exclusion")})
                # The complete native continuation, including opaque encrypted
                # reasoning, remains in reply.json and provider_raw.json.
            else:
                block(f"Turn {seen}: assistant ({row['outcome']})", reply)
        elif event.kind == "tool_requested":
            calls += 1
            call = row["call"]
            arguments = call["arguments"]
            command = arguments.get("command")
            field = "command"
            # Older recorded runs used Bash argv. Keep their display readable,
            # without accepting that format in the current shell tool.
            args = arguments.get("args")
            if command is None and isinstance(args, list) and len(args) == 2 and args[0] == "-c":
                command, field = args[1], "args"
            if call["name"] == "run_command" and isinstance(command, str):
                block(f"Turn {row['turn'] + 1}: run_command shell", command, language="bash")
                extra = {key: value for key, value in arguments.items() if key != field}
                if extra:
                    block("Additional tool arguments", extra)
            else:
                block(f"Turn {row['turn'] + 1}: tool call", call)
        elif event.kind == "tool_settled":
            result = load(row["result"]) if row["result"] is not None else row
            if not (destination / f"{prefix}_tool_result.json").exists():
                write_json(destination / f"{prefix}_tool_result.json", result)
            block(f"Turn {row['turn'] + 1}: tool result", {
                key: value for key, value in result.items() if key not in {"stdout", "stderr"}})
            for stream in ("stdout", "stderr"):
                if result.get(stream):
                    block(stream, result[stream])
        elif event.kind in {"terminal", "interrupted", "model_backoff", "context_evicted"}:
            block(event.kind, row)
            if event.kind in {"terminal", "interrupted"}:
                status = row.get("reason", row.get("code", event.kind))
    write_bytes(destination / markdown, "\n".join(transcript).encode("utf-8"))
    return {"status": status, "turns": turns, "tool_calls": calls, "events": len(events)}
