"""The single execution boundary for RTL compilers and simulators.

Candidate RTL is data to trusted EDA programs, but parser/compiler bugs and the
generated simulator still make that process tree untrusted.  Every production
tool invocation therefore runs through :mod:`behave.isolation`: frozen read-only
inputs and runtimes, an empty declared environment, denied networking, a private
tmpfs workspace, and one supervised PID tree.  There is no direct-subprocess
fallback here.

One job may contain sequential or parallel command groups. Compilation and
simulation use separate namespaces, joined by a validated read-only product.
Record validation belongs to tool_protocol; safe artifact publication belongs
to tool_outputs. This module owns admission and process supervision.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import stat
import tempfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from behave.core import runtime_defaults as defaults
from behave.isolation import (
    Device,
    IsolationRefusal,
    IsolationSpec,
    IsolationStatus,
    ReadOnlyMount,
    ResourceLimits,
    WritableRoot,
    content_digest,
    read_only,
    run,
)

from ...core.paths import scratch_root
from . import tool_outputs, tool_protocol, tool_runtime
from .tool_protocol import (
    OutputNormalizer,
    ToolCommand,
    ToolExecution,
    ToolGroup,
    ToolIsolationError,
    ToolOutputLimit,
    command_result,
    command_stream,
    require_completed,
)

_SV_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def default_limits(*, wall_seconds: int = defaults.SIM_EDA_WALL_SECONDS) -> ResourceLimits:
    """Release defaults for one compiler/simulator tree."""

    return ResourceLimits(
        cpu_seconds=max(defaults.SIM_EDA_CPU_FLOOR_SECONDS,
                        wall_seconds * defaults.SIM_EDA_CPU_MULTIPLIER),
        rss_bytes=defaults.SIM_EDA_RSS_BYTES,
        processes=defaults.SIM_EDA_PROCESSES,
        file_bytes=defaults.SIM_EDA_FILE_BYTES,
        open_files=defaults.SIM_EDA_OPEN_FILES,
        stdout_bytes=defaults.SIM_EDA_STDOUT_BYTES,
        stderr_bytes=defaults.SIM_EDA_STDERR_BYTES,
        protocol_bytes=defaults.SIM_EDA_PROTOCOL_BYTES,
        wall_seconds=wall_seconds,
    )


def run_workspace(
    workspace: str | Path,
    groups: tuple[ToolGroup, ...],
    *,
    outputs: tuple[str, ...] = (),
    output_trees: tuple[str, ...] = (),
    preserve_outputs: tuple[str, ...] = (),
    output_normalizers: tuple[OutputNormalizer, ...] = (),
    read_only_mounts: tuple[ReadOnlyMount, ...] = (),
    role: str,
    limits: ResourceLimits | None = None,
    runtime_profile: str = "eda",
    frozen_runtime: tool_runtime.FrozenToolRuntime | None = None,
    output_root: str | Path | None = None,
    _checkpoint_service=None,
) -> ToolExecution:
    """Admit one tool job before freezing or launching any of its commands.

    Sequential groups reuse the same reservation. Inside a replay worker all
    groups run serially under that worker's existing permit; they never wait
    for a second reservation while holding the first. Queue time is outside
    the tool's execution timeout, but inside an enclosing search deadline.
    """
    from time import perf_counter

    from ..cpu_admission import cpu_capacity, reserve_cpus
    from ..scoring_policy import ScoringStopped

    if type(groups) is not tuple or any(type(group) is not ToolGroup for group in groups):
        raise TypeError("groups must contain exact ToolGroup values")
    width = max((min(group.parallel, len(group.commands)) for group in groups), default=1)
    # Group parallelism is a maximum, not a minimum gang size. A smaller
    # allocation still executes every command, in the original result order.
    width = min(width, cpu_capacity() or width)
    started = perf_counter()
    try:
        with reserve_cpus(width) as admitted:
            groups = tuple(replace(group, parallel=min(group.parallel, admitted)) for group in groups)
            return _run_workspace(
                workspace, groups, outputs=outputs, output_trees=output_trees,
                preserve_outputs=preserve_outputs, output_normalizers=output_normalizers,
                read_only_mounts=read_only_mounts, role=role, limits=limits,
                runtime_profile=runtime_profile,
                frozen_runtime=frozen_runtime, output_root=output_root,
                _checkpoint_service=_checkpoint_service)
    except ScoringStopped as error:
        # Include admission and preparation, not merely child CPU time. Keep
        # a receipt retained below, including logs, when the backend returned.
        error.interrupted_tool = {
            "role": role, "wall_seconds": perf_counter() - started,
            **(error.interrupted_tool or {}),
        }
        raise


def _run_workspace(
    workspace: str | Path,
    groups: tuple[ToolGroup, ...],
    *,
    outputs: tuple[str, ...],
    output_trees: tuple[str, ...],
    preserve_outputs: tuple[str, ...],
    output_normalizers: tuple[OutputNormalizer, ...],
    read_only_mounts: tuple[ReadOnlyMount, ...],
    role: str,
    limits: ResourceLimits | None,
    runtime_profile: str,
    frozen_runtime: tool_runtime.FrozenToolRuntime | None,
    output_root: str | Path | None,
    _checkpoint_service=None,
) -> ToolExecution:
    """Run a frozen workspace and atomically materialize declared output files.

    Use the supplied runtime or obtain the profile from the shared batch.
    Ordinary input declarations retain their launch-time byte checks; prepared
    runtime resources use the fixed batch declaration. The profile must agree.
    """

    from ..execution_control import check_execution, remaining_timeout

    check_execution()
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ValueError("the RTL tool workspace must be an existing directory")
    if type(groups) is not tuple or any(type(group) is not ToolGroup for group in groups):
        raise TypeError("groups must contain exact ToolGroup values")
    if type(outputs) is not tuple:
        raise TypeError("outputs must be a tuple")
    outputs = tuple(tool_protocol.relative_path(value, "job output") for value in outputs)
    if len(outputs) != len(set(outputs)):
        raise ValueError("job output paths must be unique")
    if type(output_trees) is not tuple:
        raise TypeError("output_trees must be a tuple")
    output_trees = tuple(
        tool_protocol.relative_path(value, "job output tree") for value in output_trees)
    if len(output_trees) != len(set(output_trees)) \
            or set(outputs) & set(output_trees):
        raise ValueError("job output files and trees must be unique and disjoint")
    if not tool_outputs.paths_are_disjoint((*outputs, *output_trees)):
        raise ValueError("declared RTL outputs may not contain one another")
    if type(preserve_outputs) is not tuple:
        raise TypeError("preserve_outputs must be a tuple")
    preserve_outputs = tuple(
        tool_protocol.relative_path(value, "preserved job output") for value in preserve_outputs)
    if len(preserve_outputs) != len(set(preserve_outputs)) \
            or not set(preserve_outputs) <= set(outputs):
        raise ValueError("preserved outputs must be unique declared outputs")
    if type(output_normalizers) is not tuple or any(
        type(item) is not OutputNormalizer for item in output_normalizers
    ):
        raise TypeError(
            "output_normalizers must contain exact OutputNormalizer records")
    normalized = tuple(item.output for item in output_normalizers)
    if len(normalized) != len(set(normalized)) \
            or not set(normalized) <= set(outputs):
        raise ValueError(
            "each output normalizer must name one unique declared output")
    if type(role) is not str or not role:
        raise ValueError("an RTL tool job needs a role")
    limits = limits or default_limits()
    if type(limits) is not ResourceLimits:
        raise TypeError("limits must be an exact ResourceLimits")
    if type(read_only_mounts) is not tuple or any(
        type(item) is not ReadOnlyMount for item in read_only_mounts
    ):
        raise TypeError("read_only_mounts must contain exact ReadOnlyMount values")
    output_root = workspace if output_root is None else Path(output_root).resolve()
    if not output_root.is_dir():
        raise ValueError("the RTL tool output root must be an existing directory")

    identifiers = [
        command.identifier
        for group in groups
        for command in group.commands
    ]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("tool command identifiers must be unique within a job")
    cleanup_seconds = defaults.SIM_EDA_COMMAND_CLEANUP_SECONDS
    if type(cleanup_seconds) is not int or cleanup_seconds <= 0:
        raise ValueError("tool cleanup budget must be a positive integer")
    recursion_limit = defaults.SIM_EDA_NORMALIZATION_RECURSION_LIMIT
    if type(recursion_limit) is not int or recursion_limit <= 0:
        raise ValueError("tool normalization recursion budget must be a positive integer")
    job = {
        "version": tool_protocol.JOB_VERSION,
        "groups": [group.image() for group in groups],
        "outputs": list(outputs),
        "output_trees": list(output_trees),
        "preserve_outputs": list(preserve_outputs),
        "normalizers": [item.image() for item in output_normalizers],
        "cleanup_seconds": cleanup_seconds,
        "normalization_recursion_limit": recursion_limit,
        # Base64 plus the command envelope must fit the backend's stdout bound.
        "protocol_bytes": (None if limits.stdout_bytes is None
                           else limits.stdout_bytes * 3 // 4),
    }
    job_bytes = json.dumps(
        job, sort_keys=True, separators=(",", ":")).encode("utf-8")
    job_digest = hashlib.sha256(job_bytes).hexdigest()
    if runtime_profile not in tool_runtime.RUNTIME_PROFILES:
        raise ValueError("unknown RTL runtime profile")
    if frozen_runtime is not None:
        if type(frozen_runtime) is not tool_runtime.FrozenToolRuntime:
            raise TypeError("frozen_runtime must be an exact FrozenToolRuntime")
        if frozen_runtime.profile != runtime_profile:
            raise ValueError("the frozen runtime does not match the requested profile")
    try:
        frozen_runtime = frozen_runtime or tool_runtime.freeze_runtime(runtime_profile)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        refusal = IsolationRefusal(
            "rtl-tool-runtime-unavailable",
            "the declared RTL compiler/simulator runtime cannot be frozen",
            {"error": f"{type(error).__name__}: {error}",
             "profile": runtime_profile},
        )
        attempted = hashlib.sha256(json.dumps({
            "version": "rtl-tool-policy-unbuilt/1",
            "profile": runtime_profile,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ToolExecution(
            role,
            IsolationStatus.REFUSED,
            attempted,
            "unavailable",
            content_digest(str(workspace)),
            job_digest,
            refusal=refusal,
        )
    binaries, runtime, masked = (frozen_runtime.binaries, frozen_runtime.mounts,
                                frozen_runtime.masked)
    with tempfile.TemporaryDirectory(prefix="behave-rtl-job-", dir=scratch_root()) as temporary:
        job_path = Path(temporary) / "job.json"
        job_path.write_bytes(job_bytes)
        log_source = Path(temporary) / "logs"
        log_source.mkdir()
        checkpoint_mounts = ()
        if _checkpoint_service is not None:
            from .replay_checkpoint import MOUNT

            checkpoint_source = Path(temporary) / "checkpoints"
            checkpoint_source.mkdir(mode=0o700)
            checkpoint_mounts = (WritableRoot(MOUNT, limits.file_bytes,
                                              source=str(checkpoint_source)),)
        workspace_mount = read_only(str(workspace), "/input/tree")
        loader_environment = (
            (
                ("LD_LIBRARY_PATH",
                 "/runtime/python/lib:/runtime/site/cocotb/libs"),
                ("PYGPI_PYTHON_BIN", "/runtime/python/bin/python"),
            )
            if runtime_profile in {"simulation", "testbench"} else ()
        )
        spec = IsolationSpec(
            argv=(binaries[0].target, "-S", "-u", "/runtime/rtl-tool-job.py"),
            binaries=binaries,
            read_only=(
                *runtime,
                *read_only_mounts,
                workspace_mount,
                read_only(str(job_path), "/input/job.json"),
            ),
            writable=(WritableRoot("/work", limits.file_bytes),
                      WritableRoot("/logs", limits.file_bytes, source=str(log_source)),
                      *checkpoint_mounts),
            masked=masked,
            devices=(
                Device("/dev/null", "/dev/null", writable=True),
                Device("/dev/urandom", "/dev/urandom"),
            ),
            environment=(
                ("AR", "/tools/ar"),
                ("CC", "/usr/bin/gcc"),
                ("CXX", "/usr/bin/g++"),
                ("PATH", "/tools:/usr/bin"),
                *loader_environment,
                ("PYTHONHOME", "/runtime/python"),
                ("PYTHONPATH", "/runtime/site" if runtime_profile in {
                    "development", "testbench-build", "testbench"
                } else "/runtime/behave:/runtime/site"),
                ("PYTHONDONTWRITEBYTECODE", "1"),
                ("PYTHONHASHSEED", "0"),
                ("SHELL", "/bin/sh"),
                ("VERILATOR_ROOT", "/opt/eda/share/verilator"),
            ),
            limits=replace(limits, wall_seconds=max(1, math.ceil(
                remaining_timeout(limits.wall_seconds)))),
        )
        try:
            result = (run(spec) if _checkpoint_service is None else
                      _checkpoint_service.run(lambda: run(spec), checkpoint_source))
        except BaseException as error:
            # No terminal backend receipt: do not return CPUs to another job
            # while a compiler/simulator tree could still be alive.
            from behave.isolation.preparation_control import PreparationCancelled

            from ..cpu_admission import cpu_cleanup_unconfirmed

            if not isinstance(error, PreparationCancelled) or not error.cleanup_confirmed:
                cpu_cleanup_unconfirmed()
            raise
        if result.status is IsolationStatus.REFUSED and not result.cleanup_confirmed:
            from ..cpu_admission import cpu_cleanup_unconfirmed

            cpu_cleanup_unconfirmed()
        log_directory = tool_outputs.retain_command_logs(log_source, output_root, groups)
        # An unconfirmed cleanup is a real service failure, not an ordinary
        # budget stop that may settle earlier completed tests as passing.
        if result.cleanup_confirmed and result.status is IsolationStatus.TIMED_OUT:
            if _checkpoint_service is not None and _checkpoint_service.failure is not None:
                raise _checkpoint_service.failure
            from ..scoring_policy import ScoringStopped

            try:
                check_execution()
            except ScoringStopped as error:
                error.interrupted_tool = {
                    "role": role, "status": result.status.value,
                    "log_directory": log_directory,
                }
                raise

    if result.status is not IsolationStatus.COMPLETED:
        return ToolExecution(
            role,
            result.status,
            result.policy_digest,
            result.backend_identity,
            workspace_mount.sha256,
            job_digest,
            refusal=result.refusal,
            **tool_protocol.execution_diagnostic(result, log_directory),
        )
    try:
        protocol = tool_protocol.decode_protocol(result.stdout)
        if "error" in protocol:
            raise ValueError(str(protocol["error"]))
        command_rows = protocol.get("commands")
        if type(command_rows) is not list:
            raise ValueError("the isolated RTL tool result has an invalid shape")
        command_rows = [tool_protocol.validated_command_row(row) for row in command_rows]
        returned_identifiers = [row.get("id") for row in command_rows]
        if returned_identifiers != identifiers:
            raise ValueError("the isolated RTL command ledger does not match the job")
        cleanup_failed = any("cleanup_error" in row for row in command_rows)
        if "cleanup_failed" in protocol or cleanup_failed:
            if protocol.get("cleanup_failed") is not True or not cleanup_failed:
                raise ValueError("the isolated RTL cleanup status disagrees with its command ledger")
            return ToolExecution(
                role, IsolationStatus.REFUSED, result.policy_digest,
                result.backend_identity, workspace_mount.sha256, job_digest,
                commands=tuple(MappingProxyType(row) for row in command_rows),
                refusal=IsolationRefusal(
                    "rtl-tool-cleanup-unconfirmed",
                    "a compiler/simulator command did not confirm cleanup; its original execution is retained"),
                **tool_protocol.execution_diagnostic(result, log_directory),
            )
        if "output_limit" in protocol:
            exceeded = ToolOutputLimit.from_image(protocol["output_limit"])
            if exceeded.limit_bytes != job["protocol_bytes"] \
                    or exceeded.output not in (*outputs, *output_trees):
                raise ValueError("the isolated RTL output limit does not match the job")
            return ToolExecution(
                role, IsolationStatus.PROTOCOL_LIMIT, result.policy_digest,
                result.backend_identity, workspace_mount.sha256, job_digest,
                commands=tuple(MappingProxyType(row) for row in command_rows),
                output_limit=exceeded, **tool_protocol.execution_diagnostic(result, log_directory),
            )
        encoded_outputs = protocol.get("outputs")
        encoded_trees = protocol.get("trees")
        if type(encoded_outputs) is not dict or type(encoded_trees) is not dict:
            raise ValueError("the isolated RTL tool result has an invalid shape")
        if set(encoded_outputs) != set(outputs) or set(encoded_trees) != set(output_trees):
            raise ValueError("the isolated RTL output ledger does not match the job")
        decoded = {}
        decoded_bytes = 0
        for name in outputs:
            value = encoded_outputs.get(name)
            if value is None:
                continue
            if type(value) is not str:
                raise ValueError("an isolated RTL output is not base64 text")
            payload = base64.b64decode(value, validate=True)
            decoded_bytes += len(payload)
            if job["protocol_bytes"] is not None and decoded_bytes > job["protocol_bytes"]:
                raise ValueError("isolated RTL outputs exceed the protocol budget")
            decoded[name] = payload
        tree_archives: dict[str, tuple[bytes, str]] = {}
        for name in output_trees:
            value = encoded_trees.get(name)
            if value is None:
                continue
            if type(value) is not dict or set(value) != {"archive", "sha256"} \
                    or type(value["archive"]) is not str \
                    or type(value["sha256"]) is not str \
                    or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None:
                raise ValueError("an isolated RTL output tree has an invalid shape")
            archive = base64.b64decode(value["archive"], validate=True)
            decoded_bytes += len(archive)
            if job["protocol_bytes"] is not None and decoded_bytes > job["protocol_bytes"]:
                raise ValueError("isolated RTL outputs exceed the protocol budget")
            tree_archives[name] = (archive, value["sha256"])
    except (ValueError, TypeError, binascii.Error) as error:
        refusal = IsolationRefusal(
            "rtl-tool-protocol-invalid",
            "the isolated compiler/simulator returned no trustworthy result",
            {"error": str(error)},
        )
        return ToolExecution(
            role,
            IsolationStatus.REFUSED,
            result.policy_digest,
            result.backend_identity,
            workspace_mount.sha256,
            job_digest,
            refusal=refusal,
            **tool_protocol.execution_diagnostic(result, log_directory),
        )

    # Nothing is written back before the entire protocol, every file and every
    # directory archive validate.  A crash cannot certify stale build output.
    try:
        with tempfile.TemporaryDirectory(
            prefix="behave-rtl-tree-", dir=workspace.parent
        ) as temporary:
            staged_trees: dict[str, tuple[Path, str]] = {}
            for index, (name, (archive, promised)) in enumerate(
                    sorted(tree_archives.items())):
                staged = Path(temporary) / str(index)
                staged.mkdir()
                tool_outputs.unpack_tree(archive, staged, file_limit=limits.file_bytes)
                actual = content_digest(str(staged))
                if promised != actual:
                    raise ValueError(
                        "an isolated RTL output tree changed across its archive")
                staged_trees[name] = (staged, actual)

            for name in outputs:
                if name not in decoded:
                    tool_outputs.remove_output(output_root, name)
                    continue
                tool_outputs.write_output(output_root, name, decoded[name])
            for name in output_trees:
                if name not in staged_trees:
                    continue
                staged, _digest = staged_trees[name]
                tool_outputs.install_tree(output_root, name, staged)
            tree_digests = {
                name: digest for name, (_staged, digest) in staged_trees.items()
            }
    except (OSError, ValueError) as error:
        refusal = IsolationRefusal(
            "rtl-tool-output-invalid",
            "the isolated compiler/simulator returned no trustworthy output tree",
            {"error": str(error)},
        )
        return ToolExecution(
            role,
            IsolationStatus.REFUSED,
            result.policy_digest,
            result.backend_identity,
            workspace_mount.sha256,
            job_digest,
            refusal=refusal,
            **tool_protocol.execution_diagnostic(result, log_directory),
        )
    # A normal checkpoint stop is control flow only after validating the
    # backend receipt, command cleanup and output publication. OOM, malformed
    # output and nonzero worker exits must supersede an earlier good prefix.
    if (_checkpoint_service is not None and _checkpoint_service.failure is not None
            and result.cleanup_confirmed
            and all(row.get("returncode") == 0 or row.get("timed_out") is True for row in command_rows)):
        raise _checkpoint_service.failure
    return ToolExecution(
        role,
        result.status,
        result.policy_digest,
        result.backend_identity,
        workspace_mount.sha256,
        job_digest,
        tuple(MappingProxyType(dict(row)) for row in command_rows),
        MappingProxyType(dict(decoded)),
        MappingProxyType(dict(tree_digests)),
        **tool_protocol.execution_diagnostic(result, log_directory),
    )


def verilator_product_mount(
    root: Path, execution: ToolExecution, command_id: str,
) -> ReadOnlyMount | None:
    """Mount only the exported executable, never compiler intermediates.

    Collecting a regular output deliberately strips executable permission. Restore
    it only for this fixed simulator entry after checking the completed command
    and its staged bytes. Shared libraries remain in the frozen simulation runtime;
    collectors and initial-state helpers are linked into Vtop.
    """
    row = command_result(execution, command_id)
    payload = execution.outputs.get("build/Vtop")
    executable = root / "build/Vtop"
    if (row.get("timed_out") or row.get("output_limited")
            or row.get("returncode") != 0 or payload is None):
        return None
    try:
        if (not stat.S_ISREG(executable.lstat().st_mode)
                or executable.read_bytes() != payload):
            return None
    except FileNotFoundError:
        return None
    executable.chmod(0o500)
    return read_only(str(executable), "/build/Vtop")


def run_verilator_workspace(
    workspace: str | Path,
    *,
    top_module: str,
    test_module: str,
    role: str,
    outputs: tuple[str, ...],
    environment: tuple[tuple[str, str], ...] = (),
    preserve_outputs: tuple[str, ...] = (),
    compile_timeout_s: int = defaults.SIM_EDA_COMMAND_SECONDS,
    simulate_timeout_s: int = defaults.SIM_EDA_COMMAND_SECONDS,
    verilog_sources: tuple[str, ...] = ("rtl.sv",),
    python_paths: tuple[str, ...] = (".",),
    test_filter: str = "",
    simulation_cwd: str = ".",
    simulation_args: tuple[str, ...] = (),
) -> tuple[ToolExecution, ToolExecution]:
    """Compile once, then execute the frozen simulator in a fresh namespace.

    The compiler's writable root is mounted ``noexec``.  Its output therefore
    cannot become executable merely because an untrusted compiler wrote it.  The
    regular-file build tree is first exported through the bounded protocol,
    re-hashed on the host, and then mounted read-only in a second namespace whose
    only writable state is the private replay workspace.  No simulator process
    shares writable state with the compiler or another replay.
    """

    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ValueError("the Verilator workspace must be an existing directory")
    if type(top_module) is not str or _SV_IDENTIFIER.fullmatch(top_module) is None:
        raise ValueError("the Verilator top must be a simple SystemVerilog identifier")
    if type(test_module) is not str or not all(part.isidentifier() for part in test_module.split(".")):
        raise ValueError("the cocotb test module must be a dotted Python module name")
    if type(role) is not str or not role:
        raise ValueError("an isolated Verilator run needs a role")
    if type(environment) is not tuple:
        raise TypeError("the simulator environment must be a tuple")
    # Paths enter a make variable, not merely argv. Reject make/shell syntax.
    if type(verilog_sources) is not tuple or not verilog_sources or any(
        type(path) is not str or re.fullmatch(r"[A-Za-z0-9_./-]+", path) is None
        or tool_protocol.relative_path(path, "Verilog source") != path for path in verilog_sources
    ):
        raise ValueError("Verilog sources must be explicit make-safe relative paths")
    if type(python_paths) is not tuple or not python_paths or any(
        type(path) is not str or ":" in path or tool_protocol.relative_path(path, "Python path") != path
        for path in python_paths
    ):
        raise ValueError("Python search roots must be relative workspace paths")
    if type(test_filter) is not str or "\x00" in test_filter:
        raise ValueError("invalid cocotb test filter")
    tool_protocol.relative_path(simulation_cwd, "simulation working directory")
    if type(simulation_args) is not tuple or any(type(arg) is not str or "\x00" in arg for arg in simulation_args):
        raise ValueError("simulator arguments must be NUL-free strings")

    # Submitted tests need the public development libraries, not the internal
    # replay implementation. Both paths keep the same build/process supervisor.
    submitted = role == "submitted-tb"
    with tempfile.TemporaryDirectory(
        prefix="behave-verilator-product-", dir=scratch_root()
    ) as temporary:
        product = Path(temporary)
        build = require_completed(run_workspace(
            workspace,
            (ToolGroup((ToolCommand(
                f"{role}:compile",
                (
                    "/tools/make",
                    "SIM=verilator",
                    "SIM_BUILD=/work/build",
                    "VERILOG_SOURCES=" + " ".join("/work/" + path for path in verilog_sources),
                    "/work/build/Vtop",
                ),
                timeout_s=compile_timeout_s,
            ),)),),
            outputs=("build/Vtop",),
            output_root=product,
            role=f"{role}:compile",
            limits=default_limits(wall_seconds=compile_timeout_s + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS),
            runtime_profile="testbench-build" if submitted else "eda",
        ))
        build_mount = verilator_product_mount(product, build, f"{role}:compile")
        if build_mount is None:
            diagnostic = (
                command_stream(build, f"{role}:compile", "stdout")
                + command_stream(build, f"{role}:compile", "stderr")
            ).decode("utf-8", errors="replace")
            raise ToolIsolationError(
                "rtl-tool-compile-failed",
                "the isolated compiler produced no executable simulator: "
                + (diagnostic or "it said nothing"),
                build,
            )

        inherited_python = ":".join(
            [str(PurePosixPath("/work") / path) for path in python_paths]
            + (["/runtime/site"] if submitted else ["/runtime/behave", "/runtime/site"]))
        simulation_environment = (
            # Cocotb uses pytest for assertion rewriting, not as a pytest
            # session. Installed runner plugins (xdist, hypothesis, etc.) are
            # not part of a submitted TB and must not consume its deadline.
            ("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1"),
            ("COCOTB_RESULTS_FILE", "results.xml"),
            ("COCOTB_TESTCASE", ""),
            ("COCOTB_TEST_FILTER", test_filter),
            ("COCOTB_TEST_MODULES", test_module),
            ("COCOTB_TOPLEVEL", top_module),
            ("PYTHONPATH", inherited_python),
            ("TOPLEVEL_LANG", "verilog"),
            *environment,
        )
        names = [name for name, _value in simulation_environment]
        if len(names) != len(set(names)):
            raise ValueError("the simulator environment overrides a boundary-owned name")
        simulation_raw = run_workspace(
            workspace,
            (ToolGroup((ToolCommand(
                f"{role}:simulate",
                ("/build/Vtop", *simulation_args),
                cwd=simulation_cwd,
                environment=simulation_environment,
                timeout_s=simulate_timeout_s,
            ),)),),
            outputs=outputs,
            preserve_outputs=preserve_outputs,
            output_normalizers=tuple(OutputNormalizer(path, "verilator-coverage-points/1")
                                     for path in outputs if role != "submitted-tb"
                                     and PurePosixPath(path).name == "coverage.dat"),
            read_only_mounts=(build_mount,),
            role=f"{role}:simulate",
            limits=default_limits(wall_seconds=simulate_timeout_s + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS),
            runtime_profile="testbench" if submitted else "simulation",
        )
        try:
            simulation = require_completed(simulation_raw)
        except ToolIsolationError as error:
            raise ToolIsolationError(
                error.code, error.why, error.execution, prior=(build,)) from None
        return build, simulation


__all__ = [
    "default_limits", "run_verilator_workspace", "run_workspace", "verilator_product_mount",
]
