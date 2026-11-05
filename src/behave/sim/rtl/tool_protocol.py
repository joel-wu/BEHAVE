"""Tool job records, wire validation and retained-result access.

This module never discovers runtimes, reserves CPUs, launches processes or
publishes guest outputs. Historic receipts keep their original failure meaning.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from behave.core import runtime_defaults as defaults
from behave.isolation import IsolationRefusal, IsolationStatus

from ..strict_json import strict_json_loads

JOB_VERSION = "rtl-tool-job/7"
EXECUTION_VERSION = "rtl-tool-execution/5"
_DIAGNOSTIC_BYTES = defaults.SIM_EDA_DIAGNOSTIC_BYTES
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def relative_path(value: object, name: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError(f"{name} must be a normalized relative path")
    return value


@dataclass(frozen=True, slots=True)
class ToolCommand:
    """One command in the guest workspace."""

    identifier: str
    argv: tuple[str, ...]
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    timeout_s: int = defaults.SIM_EDA_COMMAND_SECONDS

    def __post_init__(self) -> None:
        if type(self.identifier) is not str or not self.identifier:
            raise ValueError("a tool command needs a nonempty identifier")
        if (type(self.argv) is not tuple or not self.argv
                or any(type(item) is not str or "\x00" in item
                       for item in self.argv)
                or not self.argv[0].startswith("/")):
            raise ValueError("tool argv must begin with an absolute guest executable")
        object.__setattr__(self, "cwd", relative_path(self.cwd, "command cwd"))
        if type(self.environment) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2
            or type(pair[0]) is not str or type(pair[1]) is not str
            for pair in self.environment
        ):
            raise TypeError("command environment must contain string pairs")
        names = [pair[0] for pair in self.environment]
        if len(names) != len(set(names)):
            raise ValueError("command environment names must be unique")
        if any(_ENVIRONMENT_NAME.fullmatch(name) is None
               or "\x00" in value for name, value in self.environment):
            raise ValueError("command environment must be portable and NUL-free")
        if type(self.timeout_s) is not int or self.timeout_s <= 0:
            raise ValueError("command timeout must be a positive integer")

    def image(self) -> dict[str, object]:
        return {
            "id": self.identifier,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "environment": dict(self.environment),
            "timeout_s": self.timeout_s,
        }


@dataclass(frozen=True, slots=True)
class ToolGroup:
    """Commands sharing a stage; later groups observe earlier build outputs."""

    commands: tuple[ToolCommand, ...]
    parallel: int = 1
    when_executable_glob: str | None = None

    def __post_init__(self) -> None:
        if type(self.commands) is not tuple or not self.commands or any(
            type(command) is not ToolCommand for command in self.commands
        ):
            raise TypeError("a tool group contains nonempty exact ToolCommand values")
        if type(self.parallel) is not int or self.parallel <= 0:
            raise ValueError("tool group parallelism must be positive")
        if self.when_executable_glob is not None:
            object.__setattr__(
                self,
                "when_executable_glob",
                relative_path(self.when_executable_glob, "group executable glob"),
            )

    def image(self) -> dict[str, object]:
        return {
            "parallel": self.parallel,
            "commands": [command.image() for command in self.commands],
            "when_executable_glob": self.when_executable_glob,
        }


@dataclass(frozen=True, slots=True)
class OutputNormalizer:
    """A trusted, versioned reduction applied before an output leaves the judge.

    Compiler output is still untrusted data.  Returning a multi-megabyte syntax tree
    and parsing it in the verifier defeats the point of placing the compiler in the
    namespace.  A normalizer runs in that same supervised namespace and returns only
    the closed record the verifier actually consumes.
    """

    output: str
    kind: str
    arguments: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", relative_path(
            self.output, "normalizer output"))
        if self.kind not in {
            "verilator-ports/1",
            "rtlil-sites/9",
            "rtl-state-clock-ownership/1",
            "rtl-source-state/1",
            "rtl-clock-data/1",
            "verilator-coverage-points/1",
        }:
            raise ValueError(f"unknown RTL output normalizer {self.kind!r}")
        if type(self.arguments) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2
            or type(pair[0]) is not str or not pair[0]
            for pair in self.arguments
        ):
            raise TypeError("normalizer arguments must be named exact pairs")
        names = tuple(name for name, _value in self.arguments)
        if len(names) != len(set(names)):
            raise ValueError("normalizer argument names must be unique")
        values = dict(self.arguments)
        if self.kind == "verilator-ports/1":
            if set(values) != {"top"} or (
                values["top"] is not None
                and (type(values["top"]) is not str
                     or not values["top"].isidentifier())
            ):
                raise ValueError(
                    "verilator-ports/1 needs exactly one optional identifier named top")
        elif self.kind in {"rtl-state-clock-ownership/1", "rtl-source-state/1", "rtl-clock-data/1"}:
            if set(values) != {"top"} or type(values["top"]) is not str \
                    or re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_$]*", values["top"]) is None:
                raise ValueError(
                    "rtl-state-clock-ownership/1 needs one identifier named top")
        elif values:
            raise ValueError(f"{self.kind} takes no arguments")

    def image(self) -> dict[str, object]:
        return {
            "output": self.output,
            "kind": self.kind,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class ToolOutputLimit:
    """Cumulative payload bytes at the first exceeded collection limit."""

    output: str
    limit_bytes: int
    observed_bytes: int

    def __post_init__(self) -> None:
        relative_path(self.output, "limited tool output")
        if (type(self.limit_bytes) is not int or self.limit_bytes <= 0
                or type(self.observed_bytes) is not int
                or self.observed_bytes <= self.limit_bytes):
            raise ValueError("a tool output limit needs observed bytes above its budget")

    def as_dict(self) -> dict[str, object]:
        return {"output": self.output, "limit_bytes": self.limit_bytes,
                "observed_bytes": self.observed_bytes}

    @classmethod
    def from_image(cls, value: object) -> ToolOutputLimit:
        if type(value) is not dict or set(value) != {
            "output", "limit_bytes", "observed_bytes"
        }:
            raise ValueError("a tool output limit has an invalid shape")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """Typed tool result plus the boundary identities a report must retain."""

    role: str
    status: IsolationStatus
    policy_digest: str
    backend_identity: str
    workspace_digest: str
    job_digest: str
    commands: tuple[MappingProxyType, ...] = ()
    outputs: MappingProxyType = field(
        default_factory=lambda: MappingProxyType({}))
    trees: MappingProxyType = field(
        default_factory=lambda: MappingProxyType({}))
    refusal: IsolationRefusal | None = None
    exit_code: int | None = None
    signal: int | None = None
    stderr_sha256: str = hashlib.sha256(b"").hexdigest()
    stderr_tail: str = ""
    output_limit: ToolOutputLimit | None = None
    log_directory: str | None = None

    def __post_init__(self) -> None:
        if type(self.role) is not str or not self.role:
            raise ValueError("a tool execution needs a nonempty role")
        if type(self.status) is not IsolationStatus:
            raise TypeError("tool status must be an exact IsolationStatus")
        for name in ("policy_digest", "backend_identity", "workspace_digest",
                     "job_digest"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise TypeError(f"{name} must be a nonempty string")
        if type(self.commands) is not tuple:
            raise TypeError("tool commands must be an immutable tuple")
        if self.log_directory is not None and (
            type(self.log_directory) is not str or not Path(self.log_directory).is_absolute()
        ):
            raise ValueError("tool log directory must be an absolute path")
        commands = []
        for command in self.commands:
            if type(command) is not MappingProxyType:
                raise TypeError("tool command rows must be immutable mappings")
            commands.append(MappingProxyType(
                validated_command_row(dict(command))))
        if type(self.outputs) is not MappingProxyType:
            raise TypeError("tool outputs must be an immutable mapping")
        if any(type(name) is not str or type(payload) is not bytes
               for name, payload in self.outputs.items()):
            raise TypeError("tool outputs must map names to exact bytes")
        for name in self.outputs:
            relative_path(name, "tool output name")
        if type(self.trees) is not MappingProxyType:
            raise TypeError("tool trees must be an immutable mapping")
        if any(type(name) is not str or type(digest) is not str
               or re.fullmatch(r"[0-9a-f]{64}", digest) is None
               for name, digest in self.trees.items()):
            raise TypeError("tool trees must map names to SHA-256 digests")
        for name in self.trees:
            relative_path(name, "tool tree name")
        _check_cleanup_result(self.commands, self.status, self.outputs, self.trees)
        if self.status is IsolationStatus.REFUSED:
            if type(self.refusal) is not IsolationRefusal:
                raise ValueError("a refused tool execution needs a refusal")
        elif self.refusal is not None:
            raise ValueError("only a refused tool execution may carry a refusal")
        if self.output_limit is not None and (
            type(self.output_limit) is not ToolOutputLimit
            or self.status is not IsolationStatus.PROTOCOL_LIMIT
            or self.outputs or self.trees
        ):
            raise ValueError("an output limit requires protocol-limit status and no outputs")
        if self.exit_code is not None and (
            type(self.exit_code) is not int or self.exit_code < 0
        ):
            raise TypeError("tool exit_code must be a nonnegative integer or None")
        if self.signal is not None and (
            type(self.signal) is not int or self.signal <= 0
        ):
            raise TypeError("tool signal must be a positive integer or None")
        if self.exit_code is not None and self.signal is not None:
            raise ValueError("a tool execution cannot both exit and be signaled")
        if type(self.stderr_sha256) is not str \
                or re.fullmatch(r"[0-9a-f]{64}", self.stderr_sha256) is None:
            raise ValueError("tool stderr_sha256 must be a SHA-256 digest")
        if type(self.stderr_tail) is not str \
                or len(self.stderr_tail.encode("utf-8")) > _DIAGNOSTIC_BYTES * 3:
            raise ValueError("tool stderr_tail exceeds its bounded diagnostic budget")
        object.__setattr__(self, "commands", tuple(commands))
        object.__setattr__(
            self, "outputs", MappingProxyType(dict(self.outputs)))
        object.__setattr__(self, "trees", MappingProxyType(dict(self.trees)))

    def as_dict(self) -> dict[str, object]:
        return {
            "version": EXECUTION_VERSION,
            "role": self.role,
            "status": self.status.value,
            "policy_digest": self.policy_digest,
            "backend_identity": self.backend_identity,
            "workspace_digest": self.workspace_digest,
            "job_digest": self.job_digest,
            "commands": [dict(command) for command in self.commands],
            "log_directory": self.log_directory,
            "outputs": {
                name: hashlib.sha256(payload).hexdigest()
                for name, payload in sorted(self.outputs.items())
            },
            "trees": dict(sorted(self.trees.items())),
            "refusal": None if self.refusal is None else self.refusal.as_dict(),
            "output_limit": (None if self.output_limit is None
                             else self.output_limit.as_dict()),
            "exit_code": self.exit_code,
            "signal": self.signal,
            "stderr": {
                "sha256": self.stderr_sha256,
                "tail": self.stderr_tail,
            },
        }

    @staticmethod
    def validate_image(value: object) -> dict[str, object]:
        """Validate and copy one persisted execution image.

        This is a telemetry record, not an executable ``ToolExecution``: output files
        have intentionally become digests.  The strict round-trip prevents a report
        consumer from silently accepting a record written under another schema.
        """

        fields = {
            "version", "role", "status", "policy_digest", "backend_identity",
            "workspace_digest", "job_digest", "commands", "outputs", "trees",
            "refusal", "exit_code", "signal", "stderr", "output_limit", "log_directory",
        }
        if type(value) is not dict or set(value) != fields \
                or value.get("version") != EXECUTION_VERSION:
            raise ValueError("a tool execution image has the wrong shape or version")
        if value["log_directory"] is not None and (
            type(value["log_directory"]) is not str
            or not Path(value["log_directory"]).is_absolute()
        ):
            raise ValueError("a tool execution image has an invalid log directory")
        if type(value["role"]) is not str or not value["role"] \
                or value["status"] not in {status.value for status in IsolationStatus}:
            raise ValueError("a tool execution image has an invalid role or status")
        for name in (
            "policy_digest", "backend_identity", "workspace_digest", "job_digest"
        ):
            if type(value[name]) is not str or not value[name]:
                raise ValueError(f"a tool execution image has invalid {name}")
        if type(value["commands"]) is not list \
                or any(type(row) is not dict for row in value["commands"]):
            raise ValueError("a tool execution image has invalid command rows")
        try:
            for row in value["commands"]:
                validated_command_row(row)
        except (TypeError, ValueError, binascii.Error):
            raise ValueError(
                "a tool execution image has invalid command rows") from None
        for name in ("outputs", "trees"):
            rows = value[name]
            if type(rows) is not dict or any(
                type(key) is not str or type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for key, digest in rows.items()
            ):
                raise ValueError(f"a tool execution image has invalid {name}")
            try:
                for key in rows:
                    relative_path(key, f"tool execution {name} name")
            except ValueError:
                raise ValueError(
                    f"a tool execution image has invalid {name}") from None
        _check_cleanup_result(value["commands"], IsolationStatus(value["status"]),
                              value["outputs"], value["trees"])
        if value["exit_code"] is not None and (
            type(value["exit_code"]) is not int or value["exit_code"] < 0
        ):
            raise ValueError("a tool execution image has an invalid exit code")
        if value["signal"] is not None and (
            type(value["signal"]) is not int or value["signal"] <= 0
        ):
            raise ValueError("a tool execution image has an invalid signal")
        if value["exit_code"] is not None and value["signal"] is not None:
            raise ValueError("a tool execution image has two terminal statuses")
        stderr = value["stderr"]
        if type(stderr) is not dict or set(stderr) != {"sha256", "tail"} \
                or type(stderr["sha256"]) is not str \
                or re.fullmatch(r"[0-9a-f]{64}", stderr["sha256"]) is None \
                or type(stderr["tail"]) is not str \
                or len(stderr["tail"].encode("utf-8")) > _DIAGNOSTIC_BYTES * 3:
            raise ValueError("a tool execution image has an invalid stderr record")
        refusal = value["refusal"]
        if refusal is not None and (
            type(refusal) is not dict
            or set(refusal) != {"code", "why", "detail"}
            or type(refusal["code"]) is not str
            or type(refusal["why"]) is not str
            or type(refusal["detail"]) is not dict
        ):
            raise ValueError("a tool execution image has an invalid refusal")
        if (value["status"] == IsolationStatus.REFUSED.value) != (refusal is not None):
            raise ValueError("a tool execution image disagrees with its refusal")
        if value["output_limit"] is not None:
            ToolOutputLimit.from_image(value["output_limit"])
            if value["status"] != IsolationStatus.PROTOCOL_LIMIT.value \
                    or value["outputs"] or value["trees"]:
                raise ValueError("a tool execution image disagrees with its output limit")
        # JSON is the persisted language.  This rejects nonfinite or nonserializable
        # command/refusal detail while returning a deep plain-value copy.
        try:
            return json.loads(json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError):
            raise ValueError("a tool execution image is not finite JSON") from None


class ToolIsolationError(RuntimeError):
    """A tool job for which no trustworthy output exists."""

    def __init__(
        self,
        code: str,
        why: str,
        execution: ToolExecution,
        *,
        prior: tuple[ToolExecution, ...] = (),
    ):
        if type(prior) is not tuple or any(
            type(item) is not ToolExecution for item in prior
        ):
            raise TypeError("prior tool executions must be exact ToolExecution values")
        self.code = code
        self.why = why
        self.execution = execution
        self.executions = (*prior, execution)
        super().__init__(f"{code}: {why}")


def execution_diagnostic(result, log_directory: str | None = None) -> dict[str, object]:
    """Bound untrusted helper diagnostics before they enter durable telemetry."""

    payload = result.stderr
    return {
        "log_directory": log_directory,
        "exit_code": result.exit_code,
        "signal": result.signal,
        "stderr_sha256": hashlib.sha256(payload).hexdigest(),
        "stderr_tail": payload[-_DIAGNOSTIC_BYTES:].decode(
            "utf-8", errors="replace"),
    }


def decode_protocol(payload: bytes) -> dict[str, object]:
    """Decode the guest's one answer without Python-JSON extensions.

    The guest runner is trusted code, but every compiler and generated simulator in
    its process tree is not.  Treat the boundary as hostile in both directions: a
    duplicate member must not let a later value replace the value that was audited,
    and NaN/Infinity are not values in the protocol.
    """

    try:
        text = payload.decode("utf-8", errors="strict")
        got = strict_json_loads(text, what="the isolated RTL tool response")
    except (UnicodeError, ValueError, OverflowError, RecursionError) as error:
        # The shared decoder retains the parser exception as its cause. Keep
        # positions, never its document or duplicate-member text: a tool response
        # may include hidden-reference artifacts.
        syntax = error.__cause__
        if isinstance(syntax, json.JSONDecodeError):
            raise ValueError(  # noqa: TRY004 - a payload syntax error, not an argument type error
                "the isolated RTL tool returned malformed JSON "
                f"({len(payload)} bytes; character {syntax.pos}, line {syntax.lineno}, "
                f"column {syntax.colno}: {syntax.msg})") from None
        raise ValueError("the isolated RTL tool returned malformed JSON") from None
    if type(got) is not dict or got.get("version") != JOB_VERSION:
        raise ValueError("the isolated RTL tool returned the wrong protocol version")
    if set(got) not in (
        {"version", "commands", "outputs", "trees"},
        {"version", "commands", "output_limit"},
        {"version", "commands", "cleanup_failed"},
        {"version", "error"},
    ):
        raise ValueError("the isolated RTL tool returned an invalid protocol envelope")
    return got


def validated_command_row(row: object) -> dict[str, object]:
    """Return one exact ledger row, rejecting shape-changing tool output."""

    if type(row) is not dict:
        raise ValueError("the isolated RTL command ledger is malformed")
    if set(row) == {"id", "skipped"}:
        if type(row["id"]) is not str or not row["id"] \
                or type(row["skipped"]) is not str:
            raise ValueError("an isolated RTL skipped-command row is malformed")
        return row
    legacy_fields = {
        "id", "returncode", "timed_out", "output_limited", "stdout", "stderr"
    }
    fields = {"id", "returncode", "timed_out", "stdout_file", "stderr_file"}
    legacy = "output_limited" in row
    if legacy:
        fields = legacy_fields
    if set(row) not in (fields, fields | {"cleanup_error"}):
        raise ValueError("an isolated RTL command row has an invalid shape")
    if "cleanup_error" in row and (type(row["cleanup_error"]) is not str or not row["cleanup_error"]):
        raise ValueError("an isolated RTL command cleanup error is malformed")
    if (type(row["id"]) is not str or not row["id"]
            or (type(row["returncode"]) is not int
                and not (row["returncode"] is None and "cleanup_error" in row))
            or type(row["timed_out"]) is not bool):
        raise ValueError("an isolated RTL command row has invalid field types")
    if legacy:
        # Old receipts may describe a command killed by the removed quota.
        # Retain that failure, never reinterpret it as a successful execution.
        if type(row["output_limited"]) is not bool:
            raise ValueError("an isolated RTL command row has invalid field types")
        for stream in ("stdout", "stderr"):
            if type(row[stream]) is not str:
                raise ValueError("an isolated RTL command stream is malformed")
            base64.b64decode(row[stream], validate=True)
    else:
        for stream in ("stdout", "stderr"):
            name = row[stream + "_file"]
            if type(name) is not str or re.fullmatch(r"[0-9]+-[0-9]+\." + stream, name) is None:
                raise ValueError("an isolated RTL command log path is malformed")
    return row


def _check_cleanup_result(commands, status, outputs, trees):
    if any("cleanup_error" in row for row in commands) and (
        status is not IsolationStatus.REFUSED or outputs or trees
    ):
        raise ValueError("unconfirmed command cleanup requires a refusal without published outputs")


def require_completed(execution: ToolExecution) -> ToolExecution:
    """Return a completed execution or raise its typed refusal/status."""

    if type(execution) is not ToolExecution:
        raise TypeError("require_completed needs an exact ToolExecution")
    if execution.status is IsolationStatus.COMPLETED:
        return execution
    if execution.refusal is not None:
        raise ToolIsolationError(
            execution.refusal.code, execution.refusal.why, execution)
    if execution.output_limit is not None:
        limit = execution.output_limit
        raise ToolIsolationError(
            "rtl-tool-output-protocol-limit",
            f"collecting {limit.output!r} requires {limit.observed_bytes} bytes; "
            f"the declared output budget is {limit.limit_bytes} bytes",
            execution,
        )
    raise ToolIsolationError(
        f"rtl-tool-{execution.status.value}",
        f"the isolated RTL tool tree ended with {execution.status.value}",
        execution,
    )


def command_result(execution: ToolExecution, identifier: str) -> dict[str, object]:
    rows = [dict(row) for row in execution.commands if row.get("id") == identifier]
    if len(rows) != 1:
        raise ToolIsolationError(
            "rtl-tool-command-result-missing",
            f"the isolated job returned {len(rows)} results for {identifier!r}",
            execution,
        )
    return rows[0]


def command_log(execution: ToolExecution, identifier: str, stream: str) -> Path | None:
    """Locate a retained complete log (legacy inline receipts have no file)."""
    if stream not in {"stdout", "stderr"}:
        raise ValueError("a command stream is stdout or stderr")
    row = command_result(execution, identifier)
    if stream + "_file" not in row:
        return None
    if execution.log_directory is None:
        raise ToolIsolationError(
            "rtl-tool-command-stream-missing", "command logs were not retained", execution)
    return Path(execution.log_directory) / row[stream + "_file"]


def command_stream(
    execution: ToolExecution,
    identifier: str,
    stream: str,
) -> bytes:
    """Read a complete command log; no execution quota is applied to its size."""

    path = command_log(execution, identifier, stream)
    if path is not None:
        return path.read_bytes()
    row = command_result(execution, identifier)
    value = row.get(stream)
    if type(value) is not str:
        raise ToolIsolationError(
            "rtl-tool-command-stream-missing",
            f"the isolated job returned no {stream} for {identifier!r}",
            execution,
        )
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        raise ToolIsolationError(
            "rtl-tool-command-stream-invalid",
            f"the isolated job returned malformed {stream} for {identifier!r}",
            execution,
        ) from None
