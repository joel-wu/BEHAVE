"""OS-isolated execution policy for named simulator jobs.

``sim.worker`` is a JSON protocol, not an isolation boundary.  This module gives that
protocol its one production launcher: every submitted source/formula named by a job is
frozen as an exact read-only input, the trusted Python/package closure is explicit, and
the worker runs under :mod:`behave.isolation` with an empty environment, no network and
one supervised process tree.  There is deliberately no direct-process fallback here.

The result of a job that executes submitted code is still an *observation*.  In
particular, generated witnesses are replayed by the authority before they can affect a
score.  Isolation protects the verifier and host; it does not turn a byte stream written
by submitted code into certificate evidence.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
from pathlib import Path
from typing import NamedTuple

from behave.core import runtime_defaults as defaults
from behave.isolation import (
    Device,
    IsolationResult,
    IsolationSpec,
    IsolationStatus,
    ResourceLimits,
    WritableRoot,
    native_runtime,
    python_package,
    python_runtime,
    read_only,
    run,
)
from behave.isolation.preparation_control import PreparationCancelled

from .cpu_admission import cpu_capacity, cpu_cleanup_unconfirmed, reserve_cpus
from .strict_json import strict_json_loads

__all__ = ["IsolatedJobFailure", "run_isolated_job"]


class IsolatedJobFailure(RuntimeError):
    """A named job did not produce one valid JSON response.

    ``code`` carries a refusal kind when the isolation layer made a typed refusal.
    It is deliberately separate from ``detail``: callers may propagate the kind, but
    must never recover it by parsing explanatory prose.
    """

    def __init__(self, how: str, detail: str = "", *, code: str = ""):
        self.how = str(how)
        self.detail = str(detail)
        self.code = str(code)
        super().__init__(f"{self.how}: {self.detail}" if detail else self.how)


class _JobPolicy(NamedTuple):
    """Declared input closure: host-input path fields and trusted packages."""

    path_fields: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()


# One row owns each job's host-input fields and trusted package names. Arbitrary
# payload strings never select ambient paths or dependencies. Session jobs share
# this policy with one-shot dispatch; executable handlers remain in their workers.
_JOB_POLICIES = {
    "echo": _JobPolicy(),
    "rtl_generation_layout": _JobPolicy(("relation_path",), ("z3",)),
    "rtl_source_generation_session": _JobPolicy(packages=("z3",)),
    "behavior_generation_layout": _JobPolicy(packages=("z3",)),
    "behavior_generation_select": _JobPolicy(packages=("z3",)),
    "behavior_generation_probe": _JobPolicy(packages=("z3",)),
    "behavior_generation_session": _JobPolicy(packages=("z3",)),
    "behavior_source_facts": _JobPolicy(),
    "behavior_realization": _JobPolicy(),
    "behavior_exact_analysis": _JobPolicy(packages=("z3",)),
    "rtl_exact_analysis": _JobPolicy(("relation_path",), ("z3",)),
    "rtl_analysis_session": _JobPolicy(("relation_path",), ("z3",)),
    "classify_seeds": _JobPolicy(("behavior_path",)),
}
# Unknown names gain no inputs. The existing worker dispatcher owns their refusal.
_EMPTY_POLICY = _JobPolicy()


def _package_mount(module_name: str):
    from dataclasses import asdict

    from behave.isolation import ReadOnlyMount
    from behave.isolation.runtime_environment import runtime_environment

    return runtime_environment().prepare(
        ["job-package", module_name], lambda: _prepare_package_mount(module_name),
        encode=lambda value: None if value is None else asdict(value),
        decode=lambda row: None if row is None else ReadOnlyMount(**row))


def _prepare_package_mount(module_name: str):
    """Freeze one trusted top-level import as a mount below ``/runtime/packages``."""
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        raise IsolatedJobFailure(
            "refused", f"trusted job dependency {module_name!r} is unavailable")
    if spec.submodule_search_locations:
        locations = tuple(spec.submodule_search_locations)
        if len(locations) != 1:
            raise IsolatedJobFailure(
                "refused", f"trusted package {module_name!r} has an ambiguous origin")
        source = Path(locations[0]).resolve()
        target = f"/runtime/packages/{module_name.replace('.', '/')}"
        return python_package(str(source), target)
    elif spec.origin and spec.origin not in {"built-in", "frozen"}:
        source = Path(spec.origin).resolve()
        target = f"/runtime/packages/{source.name}"
    else:
        # Builtin/frozen modules are already in the declared CPython runtime.
        return None
    return read_only(str(source), target, reuse_attestation=True)


def _dependency_mounts(name: str, payload: dict[str, object]):
    # Only the job's trusted policy selects packages; payload strings never do.
    modules = set(_JOB_POLICIES.get(name, _EMPTY_POLICY).packages)
    mounts = []
    targets = set()
    libraries = []
    for module in sorted(modules):
        mount = _package_mount(module)
        if mount is not None and mount.target not in targets:
            mounts.append(mount)
            targets.add(mount.target)
            source = Path(mount.source)
            # Native extensions and ctypes libraries belong to their declared
            # package, not to whichever unrelated solver also happens to be used.
            libraries.extend(sorted(source.rglob("*.so")) if source.is_dir() else
                             (source,) if source.suffix == ".so" else ())
    if libraries:
        for mount in native_runtime(tuple(str(path) for path in libraries)):
            if mount.target not in targets:
                mounts.append(mount)
                targets.add(mount.target)
    return tuple(mounts)


def _freeze_inputs(name: str, payload: dict[str, object]):
    """Return a payload using guest paths and the exact mounts that make them exist."""
    frozen = copy.deepcopy(payload)
    mounts = []
    for index, field in enumerate(_JOB_POLICIES.get(name, _EMPTY_POLICY).path_fields):
        raw = frozen.get(field)
        if type(raw) is not str or not raw:
            raise IsolatedJobFailure(
                "refused", f"job {name!r} needs a nonempty path field {field!r}")
        offered = Path(raw)
        # A submitted job input is the file named by the policy, not whatever a
        # symlink happens to name at launch time.  Resolve only after rejecting the
        # link itself; the resulting regular path and digest are rechecked by the
        # namespace launcher immediately before it mounts the file read-only.
        if offered.is_symlink():
            raise IsolatedJobFailure(
                "refused", f"job input {field!r} is a symlink: {offered}")
        source = offered.resolve()
        if not source.is_file():
            raise IsolatedJobFailure(
                "refused", f"job input {field!r} is not a regular file: {source}")
        guest = f"/inputs/{index}/{source.name}"
        mounts.append(read_only(str(source), guest))
        frozen[field] = guest
    return frozen, tuple(mounts)


def _strict_json(payload: bytes) -> dict[str, object]:
    if type(payload) is not bytes:
        raise IsolatedJobFailure("no-answer", "the isolated response is not bytes")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise IsolatedJobFailure("no-answer", f"the response is not UTF-8: {error}") \
            from None

    try:
        got = strict_json_loads(text, what="the isolated response")
    except (UnicodeError, ValueError, OverflowError, RecursionError) as error:
        # Keep parser positions and resource-failure detail retained by the
        # shared decoder; callers still receive the same typed job failure.
        detail = error.__cause__ or error
        raise IsolatedJobFailure(
            "no-answer", f"the isolated response is not one strict JSON value: {detail}") \
            from None
    if type(got) is not dict:
        raise IsolatedJobFailure("no-answer", "the isolated response is not an object")
    return got


def _limits(timeout_s: float, *, rss_bytes: int = defaults.SIM_JOB_RSS_BYTES,
            wire_bytes: int | None = defaults.SIM_JOB_WIRE_BYTES) -> ResourceLimits:
    if type(timeout_s) not in {int, float} or not math.isfinite(timeout_s) \
            or timeout_s <= 0:
        raise IsolatedJobFailure("refused", "a job timeout must be finite and positive")
    if type(rss_bytes) is not int or rss_bytes <= 0:
        raise IsolatedJobFailure(
            "refused", "a job memory limit must be an exact positive byte count")
    if wire_bytes is not None and (type(wire_bytes) is not int or wire_bytes <= 0):
        raise IsolatedJobFailure(
            "refused", "a job wire limit must be an exact positive byte count")
    wall = max(1, math.ceil(timeout_s))
    processes = defaults.SIM_JOB_PROCESSES
    return ResourceLimits(
        # The supervisor also enforces an aggregate CPU budget.  Permit a bounded pool
        # to use its declared parallelism for the wall budget rather than accidentally
        # turning a 60-second, 32-worker solve into a two-second aggregate-CPU run.
        cpu_seconds=max(defaults.SIM_JOB_CPU_FLOOR_SECONDS, wall * processes),
        rss_bytes=rss_bytes,
        processes=processes,
        file_bytes=defaults.SIM_JOB_FILE_BYTES,
        open_files=defaults.SIM_JOB_OPEN_FILES,
        stdout_bytes=wire_bytes,
        stderr_bytes=defaults.SIM_JOB_STDERR_BYTES,
        protocol_bytes=wire_bytes,
        wall_seconds=wall,
    )


def _memory_limit_for(name: str) -> int:
    """Return the trusted per-job memory ceiling.

    Behavior exact analysis owns a stricter artifact budget.  The named outer
    worker enforces it for lowering, formula construction and solving together;
    a nested solver process is neither needed nor able to return a reliable
    protocol answer after the kernel kills it.
    """

    if name == "behavior_exact_analysis":
        from .behavior.budgets import ARTIFACT_MEMORY_MB

        return ARTIFACT_MEMORY_MB << 20
    if name in {"rtl_exact_analysis", "rtl_analysis_session"}:
        return defaults.SIM_RTL_ANALYSIS_RSS_BYTES
    return defaults.SIM_JOB_RSS_BYTES


def _wire_limit_for(name: str) -> int | None:
    """Return the request/response ceiling for one named worker protocol."""

    if name == "behavior_exact_analysis":
        from .behavior.budgets import ARTIFACT_WIRE_MB

        return None if ARTIFACT_WIRE_MB is None else ARTIFACT_WIRE_MB << 20
    return defaults.SIM_JOB_WIRE_BYTES


def _failure_of(result: IsolationResult) -> IsolatedJobFailure:
    if result.status is IsolationStatus.REFUSED:
        refusal = result.refusal
        detail = refusal.code if refusal is not None else "isolation refused"
        if refusal is not None and refusal.why:
            detail = f"{detail}: {refusal.why}"
        if refusal is not None and refusal.detail:
            detail += f"; detail={dict(refusal.detail)!r}"
        return IsolatedJobFailure(
            "refused", detail,
            code=refusal.code if refusal is not None else "")
    if result.status in {
        IsolationStatus.TIMED_OUT,
        IsolationStatus.CPU_LIMIT,
        IsolationStatus.MEMORY_LIMIT,
        IsolationStatus.PROCESS_LIMIT,
    }:
        return IsolatedJobFailure("timeout", result.status.value)
    diagnostic = result.stderr.decode("utf-8", errors="replace")
    return IsolatedJobFailure(
        "crashed", f"isolated worker {result.status.value}: {diagnostic}")


def run_isolated_job(
    name: str,
    payload: dict[str, object],
    *,
    timeout_s: float,
) -> object:
    """Run one named JSON job inside the declared Linux namespace boundary."""
    from .execution_control import check_execution

    check_execution()
    if type(name) is not str or type(payload) is not dict:
        raise IsolatedJobFailure("refused", "a job needs a string name and plain payload")
    # Source Analysis can fan out conditional order proofs during admission.
    # Its parent reserves the same ceiling passed to the isolated child.
    requested = 1
    if name == 'rtl_exact_analysis' and payload.get('source_layout') is True:
        from .core.resources import available_cpus
        from .cpu_admission import cpu_work_width

        requested = cpu_work_width(available_cpus())
    requested = min(requested, cpu_capacity() or requested)
    with reserve_cpus(requested) as admitted:
        return _run_admitted_job(name, payload, timeout_s=timeout_s, cpus=admitted)


def _run_admitted_job(name, payload, *, timeout_s, cpus):
    from .execution_control import check_execution, remaining_timeout

    frozen_payload, inputs = _freeze_inputs(name, payload)
    try:
        document = json.dumps(
            {"job": name, "payload": frozen_payload},
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise IsolatedJobFailure(
            "refused", f"this job's payload is not finite JSON: {error}") from None
    wire_bytes = _wire_limit_for(name)
    if wire_bytes is not None and len(document) > wire_bytes:
        raise IsolatedJobFailure(
            "refused", "the job document exceeds its declared wire budget")

    spec = _job_spec(name, payload, inputs, timeout_s=remaining_timeout(timeout_s),
                     wire_bytes=wire_bytes, cpus=cpus)
    try:
        result = run(spec, stdin=document)
    except BaseException as error:
        # A pre-launch stop or an explicitly reaped cancellation has no live
        # owner. Other missing receipts must still quarantine the reservation.
        if not isinstance(error, PreparationCancelled) or not error.cleanup_confirmed:
            cpu_cleanup_unconfirmed()
        raise
    if result.status is IsolationStatus.REFUSED and not result.cleanup_confirmed:
        cpu_cleanup_unconfirmed()
    check_execution()
    if result.status is not IsolationStatus.COMPLETED:
        raise _failure_of(result)
    got = _strict_json(result.stdout)
    if got.get("ok") is not True:
        raise IsolatedJobFailure("crashed", str(got.get("error", "")))
    if set(got) != {"ok", "result"}:
        raise IsolatedJobFailure(
            "no-answer", "a successful response must contain exactly ok and result")
    return got.get("result")


def _job_spec(name, payload, inputs, *, timeout_s, wire_bytes, module=None, cpus=1):
    """The shared capability policy for one-shot jobs and interactive jobs."""
    if type(cpus) is not int or cpus < 1:
        raise ValueError("a named job needs a positive exact CPU ceiling")
    binaries, runtime, masked = python_runtime()
    from behave.isolation.runtime_environment import runtime_environment

    with runtime_environment().activate():
        package = python_package(
            str(Path(__file__).resolve().parents[1]),
            "/runtime/behave/behave")
    dependencies = _dependency_mounts(name, payload)
    # CPython and optional packages may share a libc/libm/loader dependency.  One guest
    # path has one authority; equal-target duplicates are removed before the immutable
    # policy validates mount uniqueness.
    declared = []
    targets = set()
    for mount in (*runtime, package, *dependencies, *inputs):
        if mount.target not in targets:
            declared.append(mount)
            targets.add(mount.target)
    spec = IsolationSpec(
        argv=(binaries[0].target, "-S", "-u", "-m",
              module or "behave.sim.worker", "--stdio"),
        binaries=binaries,
        read_only=tuple(declared),
        # ``ProcessPoolExecutor`` uses POSIX semaphores below ``/dev/shm``.  The job
        # policy permits a bounded process tree, so it must also provide that tree a
        # private IPC filesystem; omitting it made every parallel dual engine fail at
        # pool construction with ``FileNotFoundError``.  This is a fresh tmpfs inside
        # the job's IPC/mount namespaces, never the host's shared memory.
        writable=(
            WritableRoot("/work", defaults.SIM_JOB_WORK_BYTES),
            WritableRoot("/dev/shm", defaults.SIM_JOB_SHM_BYTES),
        ),
        masked=masked,
        devices=(
            Device("/dev/null", "/dev/null", writable=True),
            Device("/dev/urandom", "/dev/urandom"),
        ),
        environment=(
            ("HOME", "/work"),
            ("PYTHONHOME", "/runtime/python"),
            ("PYTHONPATH", "/runtime/behave:/runtime/packages"),
            ("PYTHONDONTWRITEBYTECODE", "1"),
            ("PYTHONHASHSEED", "0"),
            ("BEHAVE_JOB_CPU_LIMIT", str(cpus)),
            ("TMPDIR", "/work"),
            # Core services intentionally ignore ambient TMPDIR. Their scratch
            # root must name the guest's writable mount, not the host's /tmp.
            ("BEHAVE_SCRATCH_DIR", "/work"),
        ),
        limits=_limits(
            timeout_s,
            rss_bytes=_memory_limit_for(name),
            wire_bytes=wire_bytes,
        ),
    )
    return spec
