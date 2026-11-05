"""Shell and Python execution through the shared process isolation boundary."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from time import monotonic

from behave.agent.prompting import render
from behave.core import runtime_defaults as defaults
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, fields
from behave.isolation import (
    IsolationSpec,
    IsolationStatus,
    ResourceLimits,
    WritableRoot,
    binary,
    native_runtime,
    python_runtime,
    read_only,
    run,
)

from .cpu_resources import cpu_job
from .tools import Tool, ToolResult, ToolUnavailable, object_schema
from .workspace import Workspace


def validate_output_preview_bytes(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError("output_preview_bytes must be a positive integer or None")


class CommandRunner:
    def __init__(self, template: IsolationSpec, *, writable_workspace: bool = False, cpu_endpoint=None,
                 output_preview_bytes: int | None = None):
        if type(template) is not IsolationSpec:
            raise TypeError("command runner requires a pinned IsolationSpec")
        targets = [item.target for item in (*template.read_only, *template.writable, *template.binaries,
                                            *template.masked, *template.devices)]
        if not writable_workspace and any(target == "/workspace" or target.startswith("/workspace/") for target in targets):
            raise ValueError("/workspace is reserved for the current source snapshot")
        self.template = template
        self.writable_workspace = writable_workspace
        self.cpu_endpoint = cpu_endpoint
        validate_output_preview_bytes(output_preview_bytes)
        self.output_preview_bytes = output_preview_bytes

    def execute(self, workspace: Workspace, arguments: dict) -> ToolResult:
        try:
            if self.writable_workspace:
                command = fields(arguments, {"command"})["command"]
                if type(command) is not str or "\x00" in command:
                    raise RecordError("command-args-invalid", "command must be a NUL-free string")
                args = ("-c", command)
            else:
                args = fields(arguments, {"args"})["args"]
                if type(args) is not list or any(type(arg) is not str or "\x00" in arg for arg in args):
                    raise RecordError("command-args-invalid", "args must be a list of NUL-free strings")
        except RecordError as error:
            # This is before materializing files or starting a process.
            return ToolResult({"error": error.code, "message": str(error), "executed": False})
        base = self.template
        with workspace.materialize() as sources:
            if self.writable_workspace:
                roots = {"/workspace": str(sources), "/tmp": str(workspace.temporary_directory)}
                spec = replace(base, argv=(*base.argv, *args),
                    writable=tuple(replace(root, source=roots[root.target]) if root.target in roots else root
                                   for root in base.writable),
                    read_only=(*base.read_only, *(read_only(str(sources / path), f"/workspace/{path}")
                                                 for path in workspace.read_only_paths)))
            else:
                spec = replace(base, argv=(*base.argv, *args),
                               read_only=(*base.read_only, read_only(str(sources), "/workspace")))
            with cpu_job(self.cpu_endpoint) as lease:
                started = monotonic()
                got = run(spec)
                elapsed_seconds = monotonic() - started
                if lease is not None:
                    lease.cleanup = CleanupState.COMPLETE if got.cleanup_confirmed else CleanupState.UNKNOWN
            if self.writable_workspace and not workspace.local and got.status is not IsolationStatus.REFUSED:
                # Nonzero exits and timeouts still leave useful edits, just as
                # an ordinary shell does. run() has already reaped descendants.
                workspace.import_directory(sources)
        stdout = workspace.store.put(got.stdout)
        stderr = workspace.store.put(got.stderr)
        content = {
            "status": got.status.value, "exit_code": got.exit_code, "signal": got.signal,
            "elapsed_seconds": elapsed_seconds,
        }
        # Keep exact output bytes and execution identities in the journal's
        # artifacts, not the model's feedback. Do not duplicate log text here.
        diagnostics = {
            **content,
            "stdout_bytes": stdout.as_dict(), "stderr_bytes": stderr.as_dict(),
            "isolation_policy": got.policy_digest, "backend": got.backend_identity,
        }
        if got.status is IsolationStatus.REFUSED:
            diagnostics["refusal"] = got.refusal.as_dict()
        evidence = workspace.store.put(canonical_json(diagnostics))
        artifacts = (stdout, stderr, evidence)
        if got.status is IsolationStatus.REFUSED:
            raise ToolUnavailable(got.refusal.code, artifacts=artifacts)
        return ToolResult({**content, **self._output(workspace, got.stdout, got.stderr, stdout, stderr)}, artifacts)

    def _output(self, workspace, stdout, stderr, stdout_ref, stderr_ref):
        limit = self.output_preview_bytes
        if limit is None:
            return {"stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace")}
        # Share the budget, leaving stderr room even when stdout is verbose.
        stdout_budget = min(len(stdout), max(limit - len(stderr), limit // 2))
        output, streams = {}, {}
        for name, data, reference, budget in (
            ("stdout", stdout, stdout_ref, stdout_budget),
            ("stderr", stderr, stderr_ref, limit - stdout_budget),
        ):
            prefix = data[:budget].decode("utf-8", errors="replace")
            preview = prefix.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
            truncated = len(data) > budget or preview != prefix
            output[name] = preview
            streams[name] = {"bytes": len(data), "truncated": truncated}
            if truncated:
                # The journal already retains these bytes. Also expose them
                # as an ordinary file for later Bash calls in this attempt.
                path = f".command-output/{reference.sha256}.{name}.log"
                workspace.write(path, data)
                streams[name]["path"] = f"/workspace/{path}"
        if any(stream["truncated"] for stream in streams.values()):
            output["output_preview"] = {"limit_bytes": limit, **streams}
        return output

    def tool(self, name: str = "run_command") -> Tool:
        parameters = ({"command": {"type": "string"}} if self.writable_workspace else
                      {"args": {"type": "array", "items": {"type": "string"}}})
        preview = ("Command output is included in the conversation. For large files or verbose commands, "
                   "save output to a workspace log and inspect the relevant sections.")
        if self.output_preview_bytes is not None:
            preview = (f"The combined stdout/stderr preview is limited to {self.output_preview_bytes} UTF-8 bytes. "
                       "Truncated streams are saved in full under /workspace/.command-output/; output_preview "
                       "reports byte counts, truncation and file paths. This does not stop the command. "
                       "Use Bash to inspect selected fields or byte ranges. A line limit alone does not bound "
                       "a single-line JSON file.")
        values = {"wall_seconds": str(self.template.limits.wall_seconds), "output_preview": preview} if self.writable_workspace else {}
        revision = f"command/4:{self.template.digest}:{self.writable_workspace}"
        if self.output_preview_bytes is not None:
            revision = f"command/5:{self.template.digest}:{self.writable_workspace}:{self.output_preview_bytes}"
        return Tool(name, render("shared/tools/run_command" if self.writable_workspace else "shared/tools/run_python", **values),
                    object_schema(parameters), self.execute,
                    revision)


def shell_commands(limits: ResourceLimits | None = None, *, output_preview_bytes: int | None = None) -> CommandRunner:
    """Bash, ordinary text/file utilities and Python in one writable workspace."""
    limits = limits if limits is not None else ResourceLimits(processes=defaults.SHELL_PROCESSES)
    python = python_commands(limits).template
    tools = []
    for name in defaults.SHELL_TOOLS:
        source = shutil.which(name, path=os.defpath)
        if source is None:
            raise RuntimeError(f"shell runtime requires {name}")
        tools.append(binary(source, f"/bin/{name}", reuse_attestation=True))
    tools.extend((replace(tools[0], target="/bin/sh"),
                  replace(python.binaries[0], target="/bin/python"),
                  replace(python.binaries[0], target="/bin/python3")))
    mounts = {mount.target: mount for mount in (*python.read_only, *native_runtime(tuple(t.source for t in tools)))}
    first = limits.file_bytes // 2
    return CommandRunner(IsolationSpec(
        argv=("/bin/bash", "--noprofile", "--norc"), binaries=(*python.binaries, *tools),
        read_only=tuple(mounts.values()), masked=python.masked,
        writable=(WritableRoot("/workspace", first), WritableRoot("/tmp", limits.file_bytes - first)),
        cwd="/workspace", environment=(*python.environment, ("PATH", "/bin")), limits=limits,
    ), writable_workspace=True, output_preview_bytes=output_preview_bytes)


def python_commands(limits: ResourceLimits | None = None) -> CommandRunner:
    binaries, mounts, masked = python_runtime()
    limits = limits if limits is not None else ResourceLimits(
        cpu_seconds=defaults.PYTHON_CPU_SECONDS, rss_bytes=defaults.PYTHON_RSS_BYTES,
        processes=defaults.PYTHON_PROCESSES, file_bytes=defaults.PYTHON_FILE_BYTES,
        wall_seconds=defaults.PYTHON_WALL_SECONDS,
        stdout_bytes=defaults.PYTHON_STDOUT_BYTES, stderr_bytes=defaults.PYTHON_STDERR_BYTES)
    first = limits.file_bytes // 2
    return CommandRunner(IsolationSpec(
        argv=(binaries[0].target, "-B", "-S"), binaries=binaries, read_only=mounts, masked=masked,
        writable=(WritableRoot("/work", first), WritableRoot("/tmp", limits.file_bytes - first)),
        environment=(("PYTHONHOME", "/runtime/python"), ("PYTHONHASHSEED", "0"),
                     ("PYTHONPATH", "/workspace"), ("TMPDIR", "/tmp")), limits=limits,
    ))
