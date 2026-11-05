"""Deadline-bound transport to an explicitly configured trusted model broker.

The broker reads one ModelRequest JSON from stdin and writes one bound outcome
envelope to stdout. It owns provider-specific translation, credentials and
usage normalization, never tools or workspace mutations. No callable/pickle
or candidate-selected module crosses this boundary. An SDK adapter must make
one remote attempt: retries belong to the journalled agent loop.
"""

from __future__ import annotations

import base64
import binascii
import os
import time
from dataclasses import dataclass
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import array, canonical_json, fields, metadata, parse_json, text, version

from .lifetime import current_window
from .model import Charge, ModelFailure, ModelReply, ModelRequest

SCHEMA = "behave.model-command/4"


@dataclass(frozen=True, slots=True)
class ModelCommand:
    """Trusted launch policy. Environment/argv are public, not secret channels.

    The revision identifies the pinned broker program/dependencies. Paths alone
    do not attest installed code. Live credentials need a separate broker-owned
    secret channel; this transport does not discover keys or ambient settings.
    """

    argv: tuple[str, ...]
    revision: str
    environment: tuple[tuple[str, str], ...] = ()
    network: str = "none"

    def __post_init__(self) -> None:
        if (type(self.argv) is not tuple or not self.argv or not Path(self.argv[0]).is_absolute()
                or any(type(arg) is not str or "\x00" in arg for arg in self.argv)):
            raise ValueError("model command requires an absolute executable and NUL-free arguments")
        text(self.revision, "model command revision")
        if self.network not in {"none", "host"}:
            raise ValueError("model command network must be none or host")
        if type(self.environment) is not tuple or any(
                type(pair) is not tuple or len(pair) != 2 or any(type(v) is not str or "\x00" in v for v in pair)
                or not pair[0] or "=" in pair[0] for pair in self.environment):
            raise ValueError("invalid model command environment")
        if len(dict(self.environment)) != len(self.environment):
            raise ValueError("duplicate model command environment key")
        object.__setattr__(self, "environment", tuple(sorted(self.environment)))

    @property
    def identity(self) -> str:
        return BlobRef.of(canonical_json(self.as_dict())).sha256

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA, "argv": self.argv, "revision": self.revision,
            "environment": self.environment, "network": self.network,
        }

    @classmethod
    def from_dict(cls, value: object) -> ModelCommand:
        row = fields(value, {"schema", "argv", "revision", "environment", "network"})
        version(row["schema"], SCHEMA)
        return cls(tuple(array(row["argv"])), row["revision"],
                   tuple(tuple(array(pair)) for pair in array(row["environment"])), row["network"])


def encode_model_outcome(request: ModelRequest, result: ModelReply | ModelFailure) -> bytes:
    """Broker-side reply codec; complete usage-bearing failures are first-class."""
    if type(request) is not ModelRequest or type(result) not in (ModelReply, ModelFailure):
        raise TypeError("expected model request and typed outcome")
    return canonical_json({
        "schema": SCHEMA, "request": BlobRef.of(canonical_json(request.as_dict())).as_dict(),
        "outcome": "reply" if type(result) is ModelReply else "failure", "result": result.as_dict(),
        "raw_base64": base64.b64encode(result.raw).decode("ascii"),
    })


def _bound_row(request: ModelRequest, payload: bytes) -> dict:
    row = fields(parse_json(payload), {"schema", "request", "outcome", "result", "raw_base64"})
    version(row["schema"], SCHEMA)
    if BlobRef.from_dict(row["request"]) != BlobRef.of(canonical_json(request.as_dict())):
        raise ValueError("model response belongs to another request")
    if row["outcome"] not in {"reply", "failure"}:
        raise ValueError("unknown model outcome")
    return row


def _unpack(request: ModelRequest, payload: bytes) -> tuple[dict, bytes]:
    row = _bound_row(request, payload)
    if type(row["raw_base64"]) is not str:
        raise ValueError("model raw response must be base64 text")
    raw = base64.b64decode(row["raw_base64"], validate=True)
    return row, raw


def decode_model_outcome(request: ModelRequest, payload: bytes) -> ModelReply | ModelFailure:
    """Decode the same request-bound envelope over process or local IPC transport."""
    row, raw = _unpack(request, payload)
    if row["outcome"] == "reply":
        return ModelReply.from_dict(row["result"], raw=raw)
    if row["outcome"] == "failure":
        return ModelFailure.from_dict(row["result"], raw=raw)
    raise AssertionError("validated outcome")


def _failed_evidence(request: ModelRequest, payload: bytes, code: str, evidence: bytes) -> ModelFailure:
    # A bound envelope may contain billable usage even if its calls are malformed,
    # or its process subsequently overruns. Neither fact licenses its tool calls.
    try:
        row = _bound_row(request, payload)
        data = row["result"]
        try:
            charge = None if data["charge"] is None else Charge(**fields(
                data["charge"], {"amount", "currency", "pricing_revision"}))
        except (ValueError, TypeError, KeyError):
            charge = None  # Invalid pricing does not erase independently valid usage.
        try:
            usage = metadata(data["usage"])
        except (ValueError, TypeError, KeyError):
            usage = metadata({})
        return ModelFailure("unknown", code, usage, evidence, charge)
    except (ValueError, TypeError, KeyError, binascii.Error):
        return ModelFailure("unknown", code, raw=evidence)


class ProcessModelBackend:
    def __init__(self, command: ModelCommand, *, local_worker_url: str | None = None):
        if type(command) is not ModelCommand:
            raise TypeError("process model backend requires a ModelCommand")
        self.command = command
        self.local_worker_url = local_worker_url
        self.cleanup_state = CleanupState.COMPLETE
        self.retained_workspace: Path | None = None

    def generate(self, request: ModelRequest) -> ModelReply | ModelFailure:
        from behave.isolation.scratch import ScratchDirectory
        from behave.isolation.service import ServiceResult, run_service

        self.cleanup_state.require_quiescent("model-worker")
        if request.model_policy.get("transport") != self.command.identity:
            return ModelFailure("permanent", "model-transport-mismatch")
        packet = canonical_json(request.as_dict())
        window = current_window()
        budget = request.timeout_seconds
        if window is not None and window.monotonic_deadline is not None:
            budget = window.remaining() if budget is None else min(budget, window.remaining())
        deadline = None if budget is None else time.monotonic() + budget
        exception_type = None
        with ScratchDirectory(prefix="behave-model-", cleanup=lambda: self.cleanup_state) as scratch:
            try:
                self.cleanup_state = CleanupState.UNKNOWN
                try:
                    got = run_service(
                        self.command.argv, cwd=scratch,
                        environment={"PATH": os.defpath, "TMPDIR": str(scratch.parent),
                                     "BEHAVE_SCRATCH_DIR": str(scratch.parent), **dict(self.command.environment),
                                     **({"BEHAVE_LOCAL_CHAT_WORKER_URL": self.local_worker_url}
                                        if self.local_worker_url is not None else {})},
                        deadline=deadline, input_data=packet, output_bytes=None,
                        network=self.command.network, cancelled=None if window is None else window.control.cancelled,
                    )
                except Exception as error:  # noqa: BLE001 -- preserve unknown ownership, not exception payloads
                    exception_type = type(error).__name__
                    got = ServiceResult("supervisor-error", None, b"", b"", 0.)
                self.cleanup_state = got.cleanup_state
            finally:
                self.retained_workspace = None if self.cleanup_state.quiescent else scratch
        if got.status == "exited" and got.returncode == 0:
            try:
                return decode_model_outcome(request, got.stdout)
            except (ValueError, TypeError, binascii.Error):
                code = "model-worker-invalid-response"
        else:
            code = "model-worker-" + (got.status if got.status != "exited" else "failed")
        # Stopping the local client cannot cancel a remote request retroactively.
        # Even valid-looking stdout is not a result after a resource violation.
        # Do not expose arbitrary stderr/exception messages as model feedback.
        evidence = {"schema": "behave.model-command-failure/4", "code": code,
                    "status": got.status, "returncode": got.returncode,
                    "execution_status": got.execution_status,
                    "cleanup": got.cleanup_state.value,
                    "retained_workspace": None if self.retained_workspace is None else str(self.retained_workspace),
                    "budget_seconds": budget, "elapsed_seconds": got.elapsed_seconds,
                    "stdout_base64": base64.b64encode(got.stdout).decode("ascii")}
        if exception_type is not None:
            evidence["exception_type"] = exception_type
        return _failed_evidence(request, got.stdout, code, canonical_json(evidence))
