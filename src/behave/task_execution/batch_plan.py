"""Shared candidate batch, without evaluation or training aggregation policy."""

from __future__ import annotations

from dataclasses import dataclass

from behave.agent.admission import AdmissionPolicy, validate_policies
from behave.core.artifacts import BlobRef, ContentReader
from behave.core.records import array, canonical_json, fields, parse_json, text, version
from behave.task_execution.solver import SolverJob
from behave.task_execution.testbench import TBPolicy


def validate_execution_options(workers: int, score_on_submit: bool) -> None:
    """Check task-independent options before preparing a batch's inputs."""
    if type(workers) is not int or workers < 1:
        raise ValueError("evaluation workers must be a positive integer")
    if type(score_on_submit) is not bool:
        raise TypeError("score_on_submit must be a boolean")


class _PlanInputs:
    """Share immutable file bytes across candidates while decoding one plan."""

    def __init__(self, reader):
        self.reader, self.contents = reader, {}

    def read(self, reference):
        if reference not in self.contents:
            self.contents[reference] = self.reader.read(reference)
        return self.contents[reference]


@dataclass(frozen=True, slots=True)
class BatchPlan:
    run_id: str
    jobs: tuple[SolverJob, ...]
    judge_backend: str
    workers: int
    verification: AdmissionPolicy
    testbench: TBPolicy | None = None
    model_admission: AdmissionPolicy | None = None
    score_on_submit: bool = True
    recorded_admissions: tuple[tuple[BlobRef, str | None], ...] = ()

    def __post_init__(self) -> None:
        text(self.run_id, "evaluation run id")
        text(self.judge_backend, "judge backend")
        if type(self.jobs) is not tuple or not self.jobs or any(
                type(job) is not SolverJob for job in self.jobs):
            raise TypeError("evaluation requires a nonempty tuple of frozen SolverJobs")
        if len({job.attempt.attempt_id for job in self.jobs}) != len(self.jobs):
            raise ValueError("candidate attempt identifiers must be unique")
        if len({job.task.release for job in self.jobs}) != 1:
            raise ValueError("one evaluation plan must use one pinned task release")
        validate_execution_options(self.workers, self.score_on_submit)
        if type(self.verification) is not AdmissionPolicy:
            raise TypeError("final verification needs an admission policy")
        if self.testbench is not None and type(self.testbench) is not TBPolicy:
            raise TypeError("testbench diagnostics need TBPolicy")
        validate_policies((self.verification, self.model_admission))
        if type(self.recorded_admissions) is not tuple:
            raise TypeError("recorded admissions must be an immutable tuple")
        refs = set()
        for item in self.recorded_admissions:
            if type(item) is not tuple or len(item) != 2 or type(item[0]) is not BlobRef:
                raise TypeError("recorded admission needs a job reference and identity")
            ref, identity = item
            if ref in refs:
                raise ValueError("duplicate recorded admission")
            refs.add(ref)
            if identity is not None and (type(identity) is not str or len(identity) != 64
                                         or any(c not in '0123456789abcdef' for c in identity)):
                raise ValueError("invalid recorded admission identity")
        if refs - {BlobRef.of(job.to_bytes()) for job in self.jobs}:
            raise ValueError("recorded admission belongs to an absent job")

    def admission_identity(self, job: SolverJob) -> str | None:
        ref = BlobRef.of(job.to_bytes())
        recorded = dict(self.recorded_admissions)
        return recorded.get(ref, None if self.model_admission is None else self.model_admission.identity)

    def input_blobs(self) -> tuple[bytes, ...]:
        """Distinct retained inputs, including policies derived during binding."""
        return tuple(dict.fromkeys(data for job in self.jobs for data in job.input_blobs()))

    def to_bytes(self) -> bytes:
        row = {"schema": "behave.evaluation-plan/5", "run_id": self.run_id,
               "jobs": [parse_json(job.to_bytes()) for job in self.jobs],
               "judge_backend": self.judge_backend, "workers": self.workers,
               "verification": self.verification.as_dict(),
               "model_admission": None if self.model_admission is None else self.model_admission.as_dict(),
               "testbench": None if self.testbench is None else parse_json(self.testbench.to_bytes())}
        # Keep existing immediate-scoring plans and their sealed reports readable.
        if not self.score_on_submit:
            row["score_on_submit"] = False
        if self.recorded_admissions:
            row["recorded_admissions"] = [
                {"job": ref.as_dict(), "identity": identity} for ref, identity in self.recorded_admissions]
        return canonical_json(row)

    @property
    def identity(self) -> BlobRef:
        return BlobRef.of(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes, reader: ContentReader) -> BatchPlan:
        row = parse_json(data)
        score_on_submit = row.pop("score_on_submit", True)
        recorded = array(row.pop("recorded_admissions", []))
        recorded = tuple((BlobRef.from_dict(item["job"]), item["identity"])
                         for entry in recorded for item in (fields(entry, {"job", "identity"}),))
        row = fields(row, {"schema", *cls.__dataclass_fields__} - {"score_on_submit", "recorded_admissions"})
        version(row["schema"], "behave.evaluation-plan/5")
        jobs = array(row["jobs"])
        inputs = _PlanInputs(reader)
        return cls(row["run_id"], tuple(SolverJob.from_bytes(canonical_json(job), inputs) for job in jobs),
                   row["judge_backend"], row["workers"],
                   AdmissionPolicy(**fields(row["verification"], set(AdmissionPolicy.__dataclass_fields__))),
                   None if row["testbench"] is None else TBPolicy.from_bytes(canonical_json(row["testbench"])),
                   None if row["model_admission"] is None else AdmissionPolicy(**fields(
                       row["model_admission"], set(AdmissionPolicy.__dataclass_fields__))), score_on_submit, recorded)
