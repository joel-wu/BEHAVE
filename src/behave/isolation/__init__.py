"""Policy-neutral process isolation.

The public surface is deliberately small: freeze an ``IsolationSpec``, inspect the
closed backend capability result, then ``start`` an interactive worker or ``run`` a
bounded command.  There is no direct-subprocess fallback.
"""

from .content import binary, content_digest, native_runtime, python_runtime, read_only
from .linux import IsolationCleanupError, IsolationOutputLimit, SandboxProcess, capabilities, run, start
from .model import (
    BackendCapabilities,
    Binary,
    Device,
    IsolationRefusal,
    IsolationResult,
    IsolationSpec,
    IsolationStatus,
    IsolationUnavailable,
    MaskedPath,
    NetworkMode,
    ReadOnlyMount,
    ResourceLimits,
    WritableRoot,
)
from .package_image import executable_script, python_package, runtime_tree

__all__ = [
    "BackendCapabilities",
    "Binary",
    "Device",
    "IsolationCleanupError",
    "IsolationOutputLimit",
    "IsolationRefusal",
    "IsolationResult",
    "IsolationSpec",
    "IsolationStatus",
    "IsolationUnavailable",
    "MaskedPath",
    "NetworkMode",
    "ReadOnlyMount",
    "ResourceLimits",
    "SandboxProcess",
    "WritableRoot",
    "binary",
    "capabilities",
    "content_digest",
    "executable_script",
    "native_runtime",
    "python_package",
    "python_runtime",
    "read_only",
    "run",
    "runtime_tree",
    "start",
]
