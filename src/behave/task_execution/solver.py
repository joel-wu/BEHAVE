"""Shared Eval/Train solver: shell development followed by final submission.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from dataclasses import fields as dataclass_fields
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptResult, AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.lifetime import AttemptControl
from behave.agent.local_workspace import LocalWorkspace
from behave.agent.loop import run_attempt
from behave.agent.model import ModelBackend
from behave.agent.process_model import ModelCommand, ProcessModelBackend
from behave.agent.process_worker import WorkerChannels, settled_attempt, supervise_worker
from behave.agent.prompting import render, role_messages
from behave.agent.tools import Tool
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.paths import scratch_root
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, version
from behave.core.storage import DirectoryStore
from behave.data.published import PublicTaskInputs
from behave.data.spec_format import without_behavior_interface

from .capture import load_submission, submission_tool
from .sim_tool import ComparisonToolInputs, comparison_tool
from .solver_settings import (
    comparison_source,
    guide_names,
    prompt_path,
    render_prompt,
    submission_components,
    system_variables,
    task_variables,
)

if TYPE_CHECKING:
    from behave.isolation.model import ResourceLimits
SCHEMA = "behave.solver-job/4"


def _shell_limits() -> ResourceLimits:
    from behave.sim import api

    return api.development_limits()


@dataclass(frozen=True, slots=True)
class SolverJob:
    task: PublicTaskInputs
    attempt: AttemptSpec
    model: ModelCommand
    command_limits: ResourceLimits | None = field(default_factory=_shell_limits)
    log_bytes: int = defaults.LOG_RETAIN_BYTES
    setting: str = "joint"
    comparison: ComparisonToolInputs | None = None
    solver_experience: bytes | None = None
    output_preview_bytes: int | None = defaults.SHELL_OUTPUT_PREVIEW_BYTES

    def __post_init__(self) -> None:
        from behave.agent.commands import validate_output_preview_bytes

        validate_output_preview_bytes(self.output_preview_bytes)
        guide_names(self.setting)
        source = None if self.comparison is None else self.comparison.source
        if source != comparison_source(self.setting):
            raise ValueError("solver setting and development comparison source differ")
        if type(self.task) is not PublicTaskInputs or type(self.attempt) is not AttemptSpec:
            raise TypeError("solver job requires public task inputs and AttemptSpec")
        if type(self.model) is not ModelCommand or self.attempt.role != "solver":
            raise TypeError("solver job requires a model command and solver role")
        if self.attempt.model_policy.get("transport") != self.model.identity:
            raise ValueError("solver attempt does not bind this model transport")
        if self.command_limits is not None:
            from behave.isolation.model import ResourceLimits

            if type(self.command_limits) is not ResourceLimits:
                raise TypeError("Shell development requires ResourceLimits")
        if type(self.log_bytes) is not int or self.log_bytes < 1:
            raise ValueError("solver log limit must be a positive integer")

    def to_bytes(self) -> bytes:
        return canonical_json({
            "schema": SCHEMA, "task": {
                "task_id": self.task.task_id, "release": self.task.release.as_dict(),
                "revision": self.task.revision.as_dict(), "spec_path": self.task.spec_path,
                "contract_path": self.task.contract_path,
                "files": [file.reference.as_dict() for file in self.task.files],
            },
            "attempt": self.attempt.as_dict(), "model": self.model.as_dict(),
            "command_limits": None if self.command_limits is None else self.command_limits.as_dict(),
            "log_bytes": self.log_bytes,
            **({"output_preview_bytes": self.output_preview_bytes} if self.output_preview_bytes is not None else {}),
            **({"setting": self.setting} if self.setting != "joint" else {}),
            **({"comparison": self.comparison.as_dict()} if self.comparison is not None else {}),
            **({"solver_experience": BlobRef.of(self.solver_experience).as_dict()}
               if self.solver_experience is not None else {}),
        })

    def input_blobs(self) -> tuple[bytes, ...]:
        """Complete job inputs for both live workers and offline report readers."""
        return (*(file.data for file in self.task.files),
                *(() if self.comparison is None else self.comparison.input_blobs()),
                *(() if self.solver_experience is None else (self.solver_experience,)))

    @classmethod
    def from_bytes(cls, data: bytes, reader: ContentReader) -> SolverJob:
        from behave.core.artifacts import BlobRef

        row = parse_json(data)
        setting = row.pop("setting", "joint")
        comparison = row.pop("comparison", None)
        experience = row.pop("solver_experience", None)
        preview = row.pop("output_preview_bytes", None)
        row = fields(row, {"schema", "task", "attempt", "model", "command_limits", "log_bytes"})
        version(row["schema"], SCHEMA)
        task = fields(row["task"], {"task_id", "release", "revision", "spec_path", "contract_path", "files"})
        refs = tuple(FileRef.from_dict(file) for file in array(task["files"]))
        spec = AttemptSpec.from_dict(row["attempt"])
        public = PublicTaskInputs(task["task_id"], BlobRef.from_dict(task["release"]),
                                  BlobRef.from_dict(task["revision"]), task["spec_path"], task["contract_path"],
                                  tuple(FileData(ref, read_verified(reader, ref.content)) for ref in refs))
        commands = None
        if row["command_limits"] is not None:
            from behave.isolation.model import ResourceLimits

            commands = ResourceLimits(**fields(row["command_limits"], {item.name for item in dataclass_fields(ResourceLimits)}))
        return cls(public, spec, ModelCommand.from_dict(row["model"]), commands, row["log_bytes"], setting,
                   None if comparison is None else ComparisonToolInputs.from_dict(comparison, reader),
                   None if experience is None else read_verified(reader, BlobRef.from_dict(experience)), preview)


def solver_input_files(task: PublicTaskInputs, *, setting: str = "joint") -> tuple[FileData, ...]:
    """Spec and auxiliary files shared by solver development and submitted TB.

    The machine-readable contract stays in the coordinator's task record for
    Sim. It is not a second specification supplied to the solver.
    """
    public = tuple(file for file in task.files if file.reference.path != task.contract_path)
    if "behavior" not in submission_components(setting):
        def project(file: FileData) -> FileData:
            if file.reference.path != task.spec_path:
                return file
            source = without_behavior_interface(file.data.decode("utf-8")).encode("utf-8")
            return FileData(FileRef(file.reference.path, BlobRef.of(source)), source)

        return tuple(project(file) for file in public)
    return public


def bind_solver_policy(task: PublicTaskInputs, spec: AttemptSpec) -> AttemptSpec:
    """Protect every supplied public file in addition to caller-declared paths."""
    return replace(spec, read_only_paths=tuple(sorted({
        *spec.read_only_paths, *(file.reference.path for file in solver_input_files(task))})))


@dataclass(frozen=True, slots=True)
class SolverOutcome:
    attempt: AttemptResult
    submission: BlobRef | None

    @property
    def status(self) -> str:
        return "no_submission" if self.submission is None else "awaiting_judgment"

    @property
    def score(self) -> int | None:
        # Only normal no-submission is decided here. No simulated judge result.
        return 0 if self.submission is None else None


def run_solver(*, task: PublicTaskInputs, spec: AttemptSpec, backend: ModelBackend,
               journal: AttemptJournal, command_tool: Tool | None = None,
               control: AttemptControl | None = None,
               model_admission: ResourcePool | None = None,
               workspace_parent: Path | None = None,
               environment_docs: str = "", setting: str = "joint",
               comparison: ComparisonToolInputs | None = None, comparison_cpu_pool=None,
               cpu_endpoint=None, solver_experience: bytes | None = None) -> SolverOutcome:
    if type(task) is not PublicTaskInputs or spec.role != "solver":
        raise TypeError("run_solver requires public task inputs and the solver role")
    spec = bind_solver_policy(task, spec)
    source = None if comparison is None else comparison.source
    if source != comparison_source(setting):
        raise ValueError("solver setting and development comparison source differ")
    tools = (submission_tool(task, setting=setting),)
    if comparison is not None:
        tools = (comparison_tool(comparison, cpu_pool=comparison_cpu_pool, cpu_resources=cpu_endpoint), *tools)
    if command_tool is not None:
        tools = (command_tool, *tools)
    initial_files = solver_input_files(task, setting=setting)
    if solver_experience is not None:
        note = FileData(FileRef("experience/solver.md", BlobRef.of(solver_experience)), solver_experience)
        initial_files += (note,)
        spec = replace(spec, read_only_paths=tuple(sorted({*spec.read_only_paths, note.reference.path})))
        environment_docs = "\n\n".join(filter(None, (environment_docs, render("shared/development/experience"))))
    if comparison is not None:
        supplied = comparison.public_files()
        initial_files += supplied
        spec = replace(spec, read_only_paths=tuple(sorted({*spec.read_only_paths,
                            *(file.reference.path for file in supplied)})))
    with TemporaryDirectory(prefix="behave-rollout-", dir=workspace_parent or scratch_root()) as directory:
        workspace = LocalWorkspace(journal.objects, Path(directory), read_only_paths=spec.read_only_paths)
        result = run_attempt(journal=journal, backend=backend, spec=spec, tools=tools,
                             initial_messages=role_messages(
                                 "solver", user_template="shared/task/user", system_template=prompt_path(setting, "system"),
                                 **system_variables(setting),
                                 **task_variables(task, initial_files, environment_docs=environment_docs)),
                             initial_files=initial_files, workspace=workspace,
                             control=control, model_admission=model_admission)
    return solver_outcome(task, journal, result, setting=setting)


def solver_outcome(task: PublicTaskInputs, journal: AttemptJournal, result: AttemptResult,
                   *, setting: str = "joint") -> SolverOutcome:
    """Bind a completed attempt to this task without invoking its judge."""
    if result.reason not in {"final_action", "stopped", "turn_limit",
                             "context_limit", "wall_time_limit"}:
        raise RecordError("solver-terminal-invalid", "unknown solver termination")
    if result.reason != "final_action":
        if result.final_action is not None:
            raise RecordError("solver-terminal-invalid", "non-submission termination carries a final action")
        return SolverOutcome(result, None)
    if result.final_action is None:
        raise RecordError("solver-terminal-invalid", "submission termination has no final action")
    reference = BlobRef.from_dict(dict(result.final_action["submission"]))
    submission = load_submission(journal.objects, reference)
    if (submission.task_id, submission.release, submission.revision) != (task.task_id, task.release, task.revision):
        raise RecordError("submission-task-mismatch", "submission belongs to another task revision")
    if submission.setting != setting:
        raise RecordError("submission-setting-mismatch", "submission belongs to another solver setting")
    return SolverOutcome(result, reference)


def run_solver_process(
    job: SolverJob, *, journal: AttemptJournal, supervision: AttemptJournal,
    cancelled: Callable[[], bool] | None = None,
    model_pool: ResourcePool | None = None, provider: AttemptJournal | None = None,
    comparison_cpu_pool: dict | None = None,
    cpu_resources=None,
) -> SolverOutcome:
    """Run the same solver loop in a killable namespace; final scoring is separate.

    Phase journals and their object store must be private to the coordinator.
    Durable records and final submissions survive node loss. An unfinished
    rollout cannot resume once its local files are gone. Only mutable execution/
    build scratch belongs under core.paths.scratch_root(); this method never
    deletes journals.
    """
    if type(job) is not SolverJob:
        raise TypeError("solver process requires SolverJob")
    channels = WorkerChannels(journal, supervision, model_pool, provider)
    # The shared worker ABI retains an empty construction-development endpoint.
    ingresses = (channels.model_ingress(job.attempt, job.model), None)
    packet = job.to_bytes()
    store = journal.objects
    inputs = tuple(store.put(data) for data in job.input_blobs())
    reference = store.put(packet)

    def launch():
        with (nullcontext() if cpu_resources is None else
              cpu_resources.relay(cancelled=cancelled or (lambda: False))) as cpu:
            arguments = ()
            if comparison_cpu_pool is not None or cpu is not None:
                arguments = ("" if comparison_cpu_pool is None else canonical_json(comparison_cpu_pool).decode(),)
            if cpu is not None:
                arguments += (cpu.endpoint,)
            receipt, logs = supervise_worker(namespace="solver", module=__name__,
                attempt=job.attempt, model=job.model, journal=journal, reference=reference,
                log_bytes=job.log_bytes, cancelled=cancelled, ingresses=ingresses,
                runtime_arguments=arguments)
            if cpu is not None:
                cpu.worker_cleanup = CleanupState(receipt["cleanup"])
            return receipt, logs

    result = settled_attempt(namespace="solver", spec=job.attempt, journal=journal, supervision=supervision,
                             payload=channels.payload(reference), dependencies=(reference, *inputs),
                             launch=launch)
    return solver_outcome(job.task, journal, result, setting=job.setting)


def worker_main() -> None:
    try:
        root, objects, digest, size, endpoint, _development_endpoint, *runtime_arguments = sys.argv[1:]
        comparison_cpu_pool = parse_json(runtime_arguments[0].encode()) if runtime_arguments and runtime_arguments[0] else None
        cpu_endpoint = runtime_arguments[1] if len(runtime_arguments) > 1 else None
        store = DirectoryStore(objects)
        packet = read_verified(store, BlobRef(digest, int(size)))
        job = SolverJob.from_bytes(packet, store)
        journal = AttemptJournal(root, store)

        from behave.agent.relay_wire import RelayModelBackend

        command_tool = None
        environment_docs = ""
        if job.command_limits is not None:
            from .shell import development_shell

            command_tool = development_shell(job.command_limits, setting=job.setting, cpu_endpoint=cpu_endpoint,
                                             output_preview_bytes=job.output_preview_bytes).tool()
            environment_docs = render_prompt(job.setting, "environment")
        backend = ProcessModelBackend(job.model) if not endpoint else RelayModelBackend(endpoint)
        run_solver(task=job.task, spec=job.attempt, backend=backend,
                   journal=journal, command_tool=command_tool,
                   workspace_parent=Path.cwd(),
                   environment_docs=environment_docs, setting=job.setting, comparison=job.comparison,
                   comparison_cpu_pool=comparison_cpu_pool, cpu_endpoint=cpu_endpoint,
                   solver_experience=job.solver_experience)
    except AttemptInterrupted as error:
        sys.stdout.buffer.write(canonical_json({"interruption": error.code}))
        raise SystemExit(2) from None
    except Exception as error:  # noqa: BLE001 -- no credential-bearing exception messages
        sys.stdout.buffer.write(canonical_json({"interruption": f"solver-worker-{type(error).__name__}"}))
        raise SystemExit(2) from None
