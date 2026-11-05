"""One bounded cocotb worker for development checks and submitted-TB reports.
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from behave.core.artifacts import BlobRef, FileData, FileRef, SourceArtifact, read_verified, relative_paths
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, version
from behave.core.storage import DirectoryStore, install_exclusive

from ._worker_transport import exchange
from .solver import solver_input_files
from .tb_report import RTLCoverage, TBObservation
from .testbench import TBPolicy, TBRequest

BACKEND = "behave.cocotb-testbench/5"
COLLECTOR = "verilator-lcov"
METRIC = "rtl-line-coverage"
SCHEMA = "behave.testbench-worker/4"
RECEIPT = "behave.cocotb-receipt/3"


@dataclass(frozen=True, slots=True)
class CocotbJob:
    purpose: str
    origin: BlobRef
    files: tuple[FileData, ...]
    rtl: SourceArtifact
    testbench: SourceArtifact
    policy: TBPolicy
    dispatch: int = 0

    def __post_init__(self) -> None:
        if self.purpose not in ("development", "submission") or type(self.origin) is not BlobRef:
            raise ValueError("cocotb execution needs a purpose and frozen origin")
        if type(self.files) is not tuple or any(type(file) is not FileData for file in self.files):
            raise TypeError("cocotb files must be immutable FileData values")
        relative_paths(tuple(file.reference.path for file in self.files))
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda file: file.reference.path)))
        refs = {file.reference.path: file.reference for file in self.files}
        for source in (self.rtl, self.testbench):
            if type(source) is not SourceArtifact:
                raise TypeError("cocotb entries must be SourceArtifact values")
            if any(refs.get(file.path) != file for file in source.files):
                raise ValueError("cocotb source selection does not match its frozen files")
        if type(self.policy) is not TBPolicy or self.policy.backend != BACKEND:
            raise ValueError("cocotb execution requires its pinned backend policy")
        if type(self.dispatch) is not int or not 0 <= self.dispatch <= self.policy.max_retries:
            raise ValueError("invalid cocotb dispatch")
        if self.purpose == "development" and self.dispatch != 0:
            raise ValueError("development calls do not silently retry")

    def to_bytes(self) -> bytes:
        return canonical_json({
            "schema": SCHEMA, "purpose": self.purpose, "origin": self.origin.as_dict(),
            "files": [file.reference.as_dict() for file in self.files],
            "rtl": self.rtl.as_dict(), "testbench": self.testbench.as_dict(),
            "policy": parse_json(self.policy.to_bytes()), "dispatch": self.dispatch,
        })

    @classmethod
    def from_bytes(cls, payload: bytes, store: DirectoryStore) -> CocotbJob:
        row = fields(parse_json(payload), {"schema", "purpose", "origin", "files", "rtl", "testbench", "policy", "dispatch"})
        version(row["schema"], SCHEMA)
        refs = tuple(FileRef.from_dict(value) for value in array(row["files"]))
        return cls(row["purpose"], BlobRef.from_dict(row["origin"]),
                   tuple(FileData(ref, read_verified(store, ref.content)) for ref in refs),
                   SourceArtifact.from_dict(row["rtl"]), SourceArtifact.from_dict(row["testbench"]),
                   TBPolicy.from_bytes(canonical_json(row["policy"])), row["dispatch"])


@dataclass(frozen=True, slots=True)
class CocotbRun:
    observation: TBObservation
    console: BlobRef | None = None

    def __post_init__(self) -> None:
        if type(self.observation) is not TBObservation:
            raise TypeError("cocotb run requires TBObservation")
        if self.console is not None and (type(self.console) is not BlobRef
                                         or self.console not in self.observation.evidence):
            raise ValueError("cocotb console must belong to the execution evidence")

    def as_dict(self) -> dict:
        return {"observation": self.observation.as_dict(),
                "console": None if self.console is None else self.console.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> CocotbRun:
        row = fields(value, {"observation", "console"})
        return cls(TBObservation.from_dict(row["observation"]),
                   None if row["console"] is None else BlobRef.from_dict(row["console"]))


def submitted_job(request: TBRequest) -> CocotbJob:
    """Project public and selected files; no golden or score enters the worker."""
    files = {}
    for file in (*solver_input_files(request.task), *(file for source in (request.behavior, request.rtl, request.testbench)
                                       for file in source.files)):
        previous = files.setdefault(file.reference.path, file)
        if previous != file:
            raise RecordError("tb-source-path-conflict", "conflicting public and submitted source paths")
    return CocotbJob("submission", request.submission, tuple(files.values()), request.rtl.artifact,
                     request.testbench.artifact, request.policy, request.dispatch)


def _stage(job: CocotbJob, store: DirectoryStore) -> bytes:
    packet = job.to_bytes()
    for file in job.files:
        store.put(file.data)
    store.put(packet)
    return packet


def run_cocotb(job: CocotbJob, *, objects: DirectoryStore, workdir: Path,
               cancelled: Callable[[], bool] | None = None, cpu_pool: dict | None = None) -> CocotbRun:
    """Run once. Retry decisions belong to the report journal or the agent."""
    if type(job) is not CocotbJob:
        raise TypeError("cocotb execution requires a frozen CocotbJob")
    if (job.policy.collector, job.policy.metric) != (CocotbBackend.collector, CocotbBackend.metric):
        return CocotbRun(TBObservation("unavailable", "tb-collector-policy-unsupported"))
    ceiling = job.policy.worker_timeout_seconds
    deadline = time.monotonic() + ceiling
    store = DirectoryStore(workdir / "objects")
    packet = _stage(job, store)
    got = exchange("testbench", packet, workdir=workdir, objects=objects, deadline=deadline,
                   timeout_seconds=ceiling, log_bytes=job.policy.log_bytes, cancelled=cancelled, cpu_pool=cpu_pool)
    if got.code is not None:
        return CocotbRun(TBObservation("unavailable", got.code, retryable=got.retryable,
                                       evidence=got.evidence, cleanup=got.cleanup))
    try:
        result = CocotbRun.from_dict(got.result)
        if result.console is not None and result.console.size_bytes > job.policy.log_bytes:
            raise ValueError("cocotb console exceeds its pinned limit")
        objects.publish(store, result.observation.closure)
    except (OSError, TypeError, ValueError):
        return CocotbRun(TBObservation("unavailable", "tb-worker-invalid-response", retryable=True, evidence=got.evidence))
    return replace(result, observation=replace(result.observation,
                                               evidence=(*result.observation.evidence, *got.evidence), cleanup=got.cleanup))


class CocotbBackend:
    identity = BACKEND
    collector = COLLECTOR
    metric = METRIC

    def __init__(self, *, cpu_pool: dict | None = None):
        self.cpu_pool = cpu_pool

    def observe(self, request: TBRequest, objects: DirectoryStore) -> TBObservation:
        from behave.agent.cpu_resources import current_cpu_lease
        from behave.sim import api

        try:
            job = submitted_job(request)
        except RecordError as error:
            if error.code != "tb-source-path-conflict":
                raise
            return TBObservation("unavailable", error.code)
        lease = current_cpu_lease()
        if lease is not None:
            with api.CpuPool(capacity=len(lease.cpus)) as pool:
                return run_cocotb(job, objects=objects, workdir=request.workdir, cancelled=request.cancelled,
                                 cpu_pool=pool.endpoint.as_dict()).observation
        return run_cocotb(job, objects=objects, workdir=request.workdir, cancelled=request.cancelled,
                          cpu_pool=self.cpu_pool).observation


def _execute(job: CocotbJob, objects: DirectoryStore, workdir: Path) -> CocotbRun:
    from behave.sim import api

    result = api.run_cocotb_test(job.files, rtl=job.rtl, testbench=job.testbench, workdir=workdir,
                               timeout_seconds=job.policy.timeout_seconds, log_bytes=job.policy.log_bytes,
                               seed=job.policy.seed, compile_timeout_seconds=job.policy.compile_timeout_seconds)
    evidence = {name: objects.put(data) for name, data in result.evidence}
    coverage = None
    coverage_status = "missing"
    if result.line_coverage is not None:
        rtl = objects.put(canonical_json(job.rtl.as_dict()))
        covered, total = result.line_coverage
        coverage = RTLCoverage(rtl, job.policy.collector, job.policy.metric, covered, total,
                               evidence["submitted-output/coverage.info"])
        coverage_status = "measured"
    receipt = objects.put(canonical_json({
        "schema": RECEIPT, "runner": api.COCOTB_RUN_VERSION,
        "purpose": job.purpose, "origin": job.origin.as_dict(), "dispatch": job.dispatch,
        "inputs": BlobRef.of(job.to_bytes()).as_dict(),
        "artifacts": {"rtl": job.rtl.as_dict(), "testbench": job.testbench.as_dict()},
        "policy": parse_json(job.policy.to_bytes()), "coverage_assurance": "submitted-process-diagnostic",
        "coverage_status": coverage_status,
        "outputs": {name: ref.as_dict() for name, ref in evidence.items()},
    }))
    # A failed assertion can still have measured coverage. Neither status nor
    # these explicitly diagnostic counters can affect the official score.
    observation = TBObservation(result.status, result.code, result.comparison,
                                coverage=coverage, evidence=(receipt, *evidence.values()))
    return CocotbRun(observation, evidence.get("console.txt"))


def main() -> None:
    root = Path(sys.argv[1])
    reference = BlobRef(sys.argv[2], int(sys.argv[3]))
    store = DirectoryStore(root / "objects")
    packet = read_verified(store, reference)
    job = CocotbJob.from_bytes(packet, store)
    try:
        result = _execute(job, store, root / "execution")
    except Exception:  # noqa: BLE001 - service failures are not candidate failures
        # The shared supervisor retains stderr; do not create a second error log.
        traceback.print_exc()
        result = CocotbRun(TBObservation("unavailable", "tb-service-failed", retryable=True))
    response = canonical_json({"schema": SCHEMA, "request": reference.as_dict(), "result": result.as_dict()})
    if not install_exclusive(root, "response.json", response):
        raise ValueError("duplicate cocotb response")
