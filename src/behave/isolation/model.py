"""Typed, backend-neutral isolation policy and outcomes.

This module contains no process launcher and imports no simulator code.  A role or
judge can therefore freeze a policy before selecting a host backend.  Host paths are
runtime locators; the policy identity binds their declared content digests and guest
paths, never the ambient environment or a discovered search path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType

from behave.core import runtime_defaults as defaults

_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _guest_path(value: object, name: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty path without NUL bytes")
    # POSIX gives a leading ``//`` implementation-defined meaning.  More
    # importantly, the Linux backend maps guest paths with ``lstrip('/')``:
    # ``/work`` and ``//work`` would therefore name one mountpoint while carrying two
    # different policy identities and evading the duplicate-target check.
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError(f"{name} must begin with exactly one slash")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be an absolute normalized guest path")
    normalized = str(path)
    if normalized != value or normalized == "/":
        raise ValueError(f"{name} must be a normalized path below the guest root")
    return normalized


def _host_path(value: object, name: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty path without NUL bytes")
    if not os.path.isabs(value):
        raise ValueError(f"{name} must be absolute")
    return os.path.normpath(value)


@dataclass(frozen=True, slots=True)
class ReadOnlyMount:
    """One exact host object exposed read-only at ``target``.

    ``sha256`` is the digest of the regular file or canonical directory tree.  The
    backend verifies it before mounting unless it is a prepared batch runtime.
    A source path is deliberately not included
    in the semantic policy identity: two hosts may materialize the same bytes at
    different locations.  ``reuse_attestation`` is a host-side performance choice for
    verifier-owned runtime objects.  It does not weaken or change the byte identity and
    is consequently absent from :meth:`identity`. ``prepared_runtime`` is set by
    the trusted batch preparer: installed tools and published images are held
    fixed for that batch, rather than revalidated at each candidate launch.
    Neither host-side flag is accepted from candidate input.
    """

    source: str
    target: str
    sha256: str
    allow_symlinks: bool = False
    reuse_attestation: bool = field(default=False, compare=False, repr=False)
    prepared_runtime: bool = field(default=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _host_path(self.source, "mount source"))
        object.__setattr__(self, "target", _guest_path(self.target, "mount target"))
        if type(self.sha256) is not str or _DIGEST.fullmatch(self.sha256) is None:
            raise ValueError("a read-only mount needs a lowercase SHA-256 digest")
        if type(self.allow_symlinks) is not bool:
            raise TypeError("allow_symlinks must be Boolean")
        if type(self.reuse_attestation) is not bool:
            raise TypeError("reuse_attestation must be Boolean")

    def identity(self) -> dict[str, object]:
        return {
            "target": self.target,
            "sha256": self.sha256,
            "allow_symlinks": self.allow_symlinks,
        }


@dataclass(frozen=True, slots=True)
class Binary:
    """An executable made visible to the isolated command.

    As for :class:`ReadOnlyMount`, reusable attestation is permitted only for a
    verifier-owned runtime object and is not part of the guest-visible policy identity.
    Prepared batch executables follow the same fixed-installation contract.
    """

    source: str
    target: str
    sha256: str
    reuse_attestation: bool = field(default=False, compare=False, repr=False)
    prepared_runtime: bool = field(default=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _host_path(self.source, "binary source"))
        object.__setattr__(self, "target", _guest_path(self.target, "binary target"))
        if type(self.sha256) is not str or _DIGEST.fullmatch(self.sha256) is None:
            raise ValueError("a binary needs a lowercase SHA-256 digest")
        if type(self.reuse_attestation) is not bool:
            raise TypeError("reuse_attestation must be Boolean")

    def identity(self) -> dict[str, str]:
        return {"target": self.target, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class WritableRoot:
    """Private tmpfs, or a coordinator-owned working directory bound writable.

    A bound directory is a runtime locator. Its initial contents belong in the
    caller's workspace snapshot, not in the reusable runtime policy.
    ``bytes`` sizes tmpfs only. A bound directory uses the backing filesystem's
    capacity, with the usual per-file process limit still in force.
    """

    target: str
    bytes: int
    source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _guest_path(self.target, "writable root"))
        _positive_int(self.bytes, "writable-root bytes")
        if self.source is not None:
            object.__setattr__(self, "source", _host_path(self.source, "workspace source"))

    def identity(self) -> dict[str, object]:
        result = {"target": self.target, "bytes": self.bytes}
        if self.source is not None:
            result["storage"] = "workspace"
        return result


@dataclass(frozen=True, slots=True)
class MaskedPath:
    """An empty read-only tmpfs hiding a subtree of a broader trusted mount."""

    target: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _guest_path(self.target, "masked path"))


@dataclass(frozen=True, slots=True)
class Device:
    """One explicitly exposed host device node."""

    source: str
    target: str
    writable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _host_path(self.source, "device source"))
        object.__setattr__(self, "target", _guest_path(self.target, "device target"))
        if type(self.writable) is not bool:
            raise TypeError("device writable must be Boolean")

    def identity(self) -> dict[str, object]:
        return {"target": self.target, "writable": self.writable}


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Hard or supervised limits for one isolated process tree.

    ``rss_bytes`` bounds the tree's resident physical footprint.  The Linux
    backend uses aggregate RSS as a fast trigger and aggregate PSS to avoid
    charging a shared physical page once per process.
    Byte quotas are optional: None permits a stream without a cumulative cap.
    They do not replace the process tree's time and memory limits.
    """

    cpu_seconds: int = defaults.ISOLATION_CPU_SECONDS
    rss_bytes: int = defaults.ISOLATION_RSS_BYTES
    processes: int = defaults.ISOLATION_PROCESSES
    file_bytes: int = defaults.ISOLATION_FILE_BYTES
    open_files: int = defaults.ISOLATION_OPEN_FILES
    stdout_bytes: int | None = defaults.ISOLATION_STDOUT_BYTES
    stderr_bytes: int | None = defaults.ISOLATION_STDERR_BYTES
    protocol_bytes: int | None = defaults.ISOLATION_PROTOCOL_BYTES
    wall_seconds: int = defaults.ISOLATION_WALL_SECONDS

    def __post_init__(self) -> None:
        for name in (
            "cpu_seconds",
            "rss_bytes",
            "processes",
            "file_bytes",
            "open_files",
            "wall_seconds",
        ):
            _positive_int(getattr(self, name), name)
        for name in ("stdout_bytes", "stderr_bytes", "protocol_bytes"):
            value = getattr(self, name)
            if value is not None:
                _positive_int(value, name)

    def as_dict(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class NetworkMode(str, Enum):
    DENIED = "denied"
    PRIVATE_LOOPBACK = "private-loopback"


@dataclass(frozen=True, slots=True)
class IsolationSpec:
    """Complete immutable authority for one isolated command."""

    argv: tuple[str, ...]
    binaries: tuple[Binary, ...]
    read_only: tuple[ReadOnlyMount, ...] = ()
    writable: tuple[WritableRoot, ...] = ()
    masked: tuple[MaskedPath, ...] = ()
    devices: tuple[Device, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    network: NetworkMode = NetworkMode.DENIED
    cwd: str = "/work"
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    backend: str = "linux-namespace/8"

    def __post_init__(self) -> None:
        for name in ("argv", "binaries", "read_only", "writable", "masked", "devices", "environment"):
            value = getattr(self, name)
            if type(value) is not tuple:
                raise TypeError(f"{name} must be a tuple")
        if not self.argv or any(type(arg) is not str or "\x00" in arg for arg in self.argv):
            raise ValueError("argv must contain nonempty NUL-free strings")
        if any(type(item) is not Binary for item in self.binaries):
            raise TypeError("binaries must contain exact Binary values")
        if any(type(item) is not ReadOnlyMount for item in self.read_only):
            raise TypeError("read_only must contain exact ReadOnlyMount values")
        if any(type(item) is not WritableRoot for item in self.writable):
            raise TypeError("writable must contain exact WritableRoot values")
        if any(type(item) is not MaskedPath for item in self.masked):
            raise TypeError("masked must contain exact MaskedPath values")
        if any(type(item) is not Device for item in self.devices):
            raise TypeError("devices must contain exact Device values")
        if type(self.network) is not NetworkMode:
            raise TypeError("network must be an exact NetworkMode")
        if type(self.limits) is not ResourceLimits:
            raise TypeError("limits must be an exact ResourceLimits value")
        if type(self.backend) is not str or not self.backend:
            raise ValueError("backend must be a nonempty identity")
        object.__setattr__(self, "cwd", _guest_path(self.cwd, "working directory"))

        binary_targets = {item.target for item in self.binaries}
        if len(binary_targets) != len(self.binaries):
            raise ValueError("binary guest paths must be unique")
        if self.argv[0] not in binary_targets:
            raise ValueError("argv[0] must name one explicitly mounted binary")

        targets = [item.target for item in self.binaries]
        targets += [item.target for item in self.read_only]
        targets += [item.target for item in self.writable]
        targets += [item.target for item in self.masked]
        targets += [item.target for item in self.devices]
        if len(targets) != len(set(targets)):
            raise ValueError("mount targets must be unique")
        if sum(item.bytes for item in self.writable if item.source is None) > self.limits.file_bytes:
            raise ValueError("writable-root capacity exceeds the process-tree file budget")
        if not any(self.cwd == item.target or self.cwd.startswith(item.target + "/") for item in self.writable):
            raise ValueError("cwd must lie inside an explicitly writable root")

        env: list[tuple[str, str]] = []
        seen: set[str] = set()
        for pair in self.environment:
            if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str or type(pair[1]) is not str:
                raise TypeError("environment entries must be string pairs")
            name, value = pair
            if _ENV_NAME.fullmatch(name) is None or "\x00" in value or name in seen:
                raise ValueError("environment names must be unique portable identifiers")
            seen.add(name)
            env.append((name, value))
        object.__setattr__(self, "environment", tuple(sorted(env)))

    def identity_document(self) -> dict[str, object]:
        return {
            "version": "isolation-policy/3",
            "argv": list(self.argv),
            "binaries": [item.identity() for item in self.binaries],
            "read_only": [item.identity() for item in self.read_only],
            "writable": [item.identity() for item in self.writable],
            "masked": [item.target for item in self.masked],
            "devices": [item.identity() for item in self.devices],
            "environment": [list(item) for item in self.environment],
            "network": self.network.value,
            "cwd": self.cwd,
            "limits": self.limits.as_dict(),
            "backend": self.backend,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.identity_document(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    backend: str
    kernel: str
    features: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.backend) is not str or type(self.kernel) is not str:
            raise TypeError("capability identity fields must be strings")
        if type(self.features) is not tuple or any(type(v) is not str for v in self.features):
            raise TypeError("capability features must be a tuple of strings")
        object.__setattr__(self, "features", tuple(sorted(set(self.features))))

    @property
    def identity(self) -> str:
        payload = json.dumps(
            {"backend": self.backend, "kernel": self.kernel, "features": list(self.features)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class IsolationRefusal:
    code: str
    why: str
    detail: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.code) is not str or not self.code or type(self.why) is not str:
            raise TypeError("an isolation refusal needs string code and explanation")
        if type(self.detail) is not dict:
            raise TypeError("isolation refusal detail must be a plain dictionary")
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "why": self.why, "detail": dict(self.detail)}


class IsolationStatus(str, Enum):
    COMPLETED = "completed"
    EXITED = "exited"
    SIGNALED = "signaled"
    TIMED_OUT = "timed-out"
    CPU_LIMIT = "cpu-limit"
    MEMORY_LIMIT = "memory-limit"
    PROCESS_LIMIT = "process-limit"
    OUTPUT_LIMIT = "output-limit"
    PROTOCOL_LIMIT = "protocol-limit"
    FILE_LIMIT = "file-limit"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class IsolationResult:
    status: IsolationStatus
    policy_digest: str
    backend_identity: str
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int | None = None
    signal: int | None = None
    refusal: IsolationRefusal | None = None

    @property
    def cleanup_confirmed(self) -> bool:
        """Whether the terminal receipt permits releasing the owner's resources.

        Cleanup uncertainty is explicit supervisor evidence, not something a
        caller infers from an execution error code or a diagnostic string.
        """
        return (self.refusal is None or
                self.refusal.detail.get("cleanup", {}).get("state") != "unconfirmed")

    def __post_init__(self) -> None:
        if type(self.status) is not IsolationStatus:
            raise TypeError("status must be an exact IsolationStatus")
        if type(self.policy_digest) is not str or not self.policy_digest \
                or type(self.backend_identity) is not str \
                or not self.backend_identity:
            raise TypeError("an isolation result needs policy and backend identities")
        if type(self.stdout) is not bytes or type(self.stderr) is not bytes:
            raise TypeError("captured output must be bytes")
        if self.exit_code is not None \
                and (type(self.exit_code) is not int or self.exit_code < 0):
            raise TypeError("an isolation exit code must be a nonnegative integer")
        if self.signal is not None \
                and (type(self.signal) is not int or self.signal <= 0):
            raise TypeError("an isolation signal must be a positive integer")
        if self.exit_code is not None and self.signal is not None:
            raise ValueError("an isolation result cannot both exit and be signaled")
        if self.status is IsolationStatus.COMPLETED \
                and self.exit_code not in {None, 0}:
            raise ValueError("a completed isolation result cannot have a failing exit")
        if self.status is IsolationStatus.EXITED and self.exit_code is None:
            raise ValueError("an exited isolation result needs an exit code")
        if self.status is IsolationStatus.SIGNALED and self.signal is None:
            raise ValueError("a signaled isolation result needs its signal")
        if self.status is IsolationStatus.REFUSED:
            if type(self.refusal) is not IsolationRefusal:
                raise ValueError("a refused result needs an exact refusal")
        elif self.refusal is not None:
            raise ValueError("only a refused result may carry a refusal")


class IsolationUnavailable(RuntimeError):
    """The requested boundary cannot be enforced; there is no weaker fallback."""

    def __init__(self, refusal: IsolationRefusal):
        if type(refusal) is not IsolationRefusal:
            raise TypeError("IsolationUnavailable needs an exact IsolationRefusal")
        self.refusal = refusal
        super().__init__(f"{refusal.code}: {refusal.why}")


__all__ = [
    "BackendCapabilities",
    "Binary",
    "Device",
    "IsolationRefusal",
    "IsolationResult",
    "IsolationSpec",
    "IsolationStatus",
    "IsolationUnavailable",
    "MaskedPath",
    "NetworkMode",
    "ReadOnlyMount",
    "ResourceLimits",
    "WritableRoot",
]
