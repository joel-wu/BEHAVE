"""Judge one frozen submission, with durable same-input retries and no model calls.

The caller owns and fences this coordinator exactly as for an agent attempt.
A dangling dispatch is unknown, not permission to start a second worker. Use a
separate journal directory from the agent session and keep it on durable storage.
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal
from behave.agent.replay import Tape
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, LoadedSource, read_verified, validate_sha256_digest
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version
from behave.core.storage import DirectoryStore, install_exclusive
from behave.data.published import JudgeInputs

from ._worker_transport import WorkerResponse, exchange, read_record
from .analysis import AnalysisReport
from .capture import FrozenSubmission, load_submission
from .comparison import ComparisonInputs, ComparisonRequest
from .judge_policy import JudgePolicy
from .judgment import ComponentJudgment, SubmissionJudgment
from .score_progress import decision_matches, validate_decision


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    task: JudgeInputs
    submission: BlobRef
    component: str
    source: LoadedSource
    policy: JudgePolicy
    dispatch: int
    workdir: Path
    cancelled: Callable[[], bool] = lambda: False
    on_decided: Callable[[ComponentJudgment], None] | None = None


class JudgeBackend(Protocol):
    """Trusted adapter. Candidate source must execute only through isolation."""

    identity: str

    def evaluate(self, request: JudgeRequest, objects: DirectoryStore) -> ComponentJudgment: ...


def _bind(task: JudgeInputs, submission: FrozenSubmission) -> None:
    if (task.task_id, task.release, task.revision) != (
        submission.task_id, submission.release, submission.revision,
    ):
        raise RecordError("judge-task-mismatch", "submission belongs to another frozen task")


def judge_submission(
    *, task: JudgeInputs, submission: BlobRef, journal: AttemptJournal, backend: JudgeBackend,
    cancelled: Callable[[], bool] = lambda: False, admission: ResourcePool | None = None,
    retained: dict[str, ComponentJudgment] | None = None,
) -> SubmissionJudgment:
    """Score exactly the artifacts requested by the frozen solver setting.

    Joint development takes the mean of RTL and Behavior. RTL-only uses RTL.
    Missing requested components are determinate zeros. A TB is neither executed
    nor interpreted here: its separate report cannot change either score.
    """
    from behave.isolation.scratch import ScratchDirectory

    if type(task) is not JudgeInputs:
        raise TypeError("judging requires the private immutable task view")
    if not callable(cancelled) or (admission is not None and type(admission) is not ResourcePool):
        raise TypeError("invalid judge execution control")
    text(backend.identity, "judge backend identity")
    frozen = load_submission(journal.objects, submission)
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(journal.objects, frozen.workspace))
    _bind(task, frozen)
    policy = JudgePolicy.from_bytes(task.judge_policy)
    # Record the actual inputs as well as the publisher's revision identity.
    references = [task.reference_behavior]
    if task.reference_rtl is not None:
        references.append(task.reference_rtl)
    private = (task.contract, *(file for source in references for file in source.files))
    refs = [journal.objects.put(file.data) for file in private]
    run_ref = journal.objects.put(task.run_policy)
    policy_ref = journal.objects.put(task.judge_policy)
    identity = journal.objects.put(canonical_json({
        "schema": "behave.judge-inputs/2", "backend": backend.identity,
        "admission": None if admission is None else admission.policy.identity,
        "submission": submission.as_dict(), "task_id": task.task_id,
        "release": task.release.as_dict(), "revision": task.revision.as_dict(),
        "contract": task.contract.reference.as_dict(), "run_policy": run_ref.as_dict(),
        "judge_policy": policy_ref.as_dict(), "references": [source.artifact.as_dict() for source in references],
    }))
    tape = Tape(journal)
    tape.record("judge_started", {"inputs": identity.as_dict()},
                (identity, submission, frozen.workspace, run_ref, policy_ref, *refs,
                 *(file.content for file in snapshot.files)))
    outcomes = {}
    from .solver_settings import submission_components

    for name in (name for name in submission_components(frozen.setting) if name != "testbench"):
        component = getattr(frozen, name)
        cleanup = CleanupState.combine(*(item.cleanup for item in outcomes.values()))
        if retained is not None and name in retained:
            result = retained[name]
            tape.record("component_retained", {"component": name, "result": result.as_dict()}, result.closure)
        elif not cleanup.quiescent:
            result = ComponentJudgment(None, f"judge-previous-cleanup-{cleanup.value}", "infrastructure", cleanup=cleanup)
            tape.record("component_unavailable", {"component": name, "result": result.as_dict()})
        elif component.artifact is None:
            result = ComponentJudgment(0, "submission-component-missing", "candidate",
                                       detail={"component": name, "missing": list(component.missing)})
            tape.record("component_missing", {"component": name, "result": result.as_dict()})
        else:
            source = LoadedSource(component.artifact, tuple(
                FileData(file, read_verified(journal.objects, file.content)) for file in component.files))
            for dispatch in range(policy.max_retries + 1):
                live = tape.cursor == len(tape.events)
                permit = admission.acquire(cancelled=cancelled) if admission is not None and live else nullcontext()
                if not live and tape.events[tape.cursor].kind == "component_unavailable":
                    row = thaw_json(tape.take("component_unavailable").payload)
                    if row["component"] != name:
                        raise RecordError("judge-component-mismatch", "unavailability belongs to another component")
                    result = ComponentJudgment.from_dict(row["result"])
                    break
                try:
                    if live and cancelled():
                        raise AttemptInterrupted("judge-cancelled")
                    work_cleanup = CleanupState.COMPLETE
                    with permit, ScratchDirectory(prefix="behave-judge-", cleanup=lambda: work_cleanup) as local:  # noqa: B023 -- exits before the next dispatch
                        request = JudgeRequest(task, submission, name, source, policy, dispatch, local, cancelled)
                        work_cleanup = CleanupState.UNKNOWN if live else CleanupState.COMPLETE
                        result = _dispatch(tape, identity, backend, request, journal, admission)
                        work_cleanup = result.cleanup if live else CleanupState.COMPLETE
                except AttemptInterrupted as error:
                    # A dangling old dispatch is still unknown, not a new result.
                    if not live:
                        raise
                    result = ComponentJudgment(None, error.code, "infrastructure")
                    tape.record("component_unavailable", {"component": name, "result": result.as_dict()})
                if not result.retryable:
                    break
        outcomes[name] = result
    behavior = outcomes.get("behavior")
    terminal = tape.record("judgment_finished", {"submission": submission.as_dict(),
                                                 "behavior": None if behavior is None else behavior.as_dict(),
                                                 "rtl": outcomes["rtl"].as_dict()})
    tape.finish()
    return SubmissionJudgment(submission, behavior, outcomes["rtl"], BlobRef.of(terminal.to_bytes()))


def _dispatch(tape: Tape, identity: BlobRef, backend: JudgeBackend, request: JudgeRequest,
              journal: AttemptJournal, admission: ResourcePool | None) -> ComponentJudgment:
    if tape.begin("judge_requested", {"component": request.component, "dispatch": request.dispatch,
                                       "inputs": identity.as_dict()}):
        decided = None

        def publish_score(result: ComponentJudgment) -> None:
            nonlocal decided
            validate_decision(result)
            if decided is not None or request.policy.analysis is None:
                raise ValueError("unexpected or duplicate score publication")
            # Seal the existing prefix before committing its new score event.
            # Evidence was copied from node-local CAS by the worker transport.
            for event in tape.events:
                journal.objects.put(event.to_bytes())
            event = tape.record("judge_score_decided", {"result": result.as_dict()}, result.closure)
            decided = result
            journal.objects.put(event.to_bytes())

        try:
            result = backend.evaluate(replace(request, on_decided=publish_score), journal.objects)
            if type(result) is not ComponentJudgment:
                raise TypeError("judge backend must return ComponentJudgment")
        except AttemptInterrupted as error:
            # Unlike pre-dispatch cancellation, an exception after entering the
            # backend carries no evidence that its children have stopped.
            result = ComponentJudgment(None, error.code, "infrastructure", cleanup=CleanupState.UNKNOWN)
        except Exception as error:  # noqa: BLE001 -- trusted backend failure is never a candidate zero
            result = ComponentJudgment(None, "judge-backend-failed", "verifier", False,
                                       detail={"error_type": type(error).__name__}, cleanup=CleanupState.UNKNOWN)
        if decided is not None and (not decision_matches(decided, result) or result.analysis is None):
            policy = journal.objects.put(canonical_json(request.policy.analysis.as_dict()))
            result = replace(decided, cleanup=result.cleanup,
                             evidence=decided.evidence + tuple(ref for ref in result.evidence if ref not in decided.evidence),
                             analysis=AnalysisReport(decided.detail["evaluation"], policy,
                                                     "analysis-score-handoff-failed"))
        if admission is not None:
            admission.quarantine(result.cleanup)  # Fence locally even if persistence fails.
        if not result.cleanup.quiescent:
            from behave.isolation.scratch import retention_record

            retained = journal.objects.put(retention_record(request.workdir, result.cleanup))
            result = replace(result, evidence=(*result.evidence, retained))
        tape.record("judge_settled", {"result": result.as_dict()}, result.closure)
    else:
        decided = None
        if tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "judge_score_decided":
            decided = ComponentJudgment.from_dict(thaw_json(tape.take("judge_score_decided").payload)["result"])
            validate_decision(decided)
            if request.policy.analysis is None:
                raise RecordError("judge-score-unexpected", "score checkpoint without an analysis policy")
        result = ComponentJudgment.from_dict(thaw_json(tape.take("judge_settled").payload)["result"])
        if decided is not None and (not decision_matches(decided, result) or result.analysis is None):
            raise RecordError("judge-score-changed", "settlement differs from the durable score")
        if admission is not None:
            admission.quarantine(result.cleanup)
    return result


CHECKPOINT_SCHEMA = "behave.judge-score-checkpoint/3"


@dataclass(frozen=True, slots=True)
class ScoreCheckpoint:
    request: BlobRef
    result: ComponentJudgment
    started_ns: int

    def __post_init__(self) -> None:
        if type(self.request) is not BlobRef or type(self.result) is not ComponentJudgment:
            raise TypeError("score checkpoint requires a bound typed judgment")
        if self.result.analysis is not None:
            raise ValueError("checkpoint must contain only a concrete result")
        validate_sha256_digest(self.result.detail.get("evaluation"))
        if type(self.started_ns) is not int or self.started_ns < 1:
            raise ValueError("checkpoint requires a monotonic phase start")

    def install(self, workdir: Path) -> None:
        data = canonical_json({"schema": CHECKPOINT_SCHEMA, "request": self.request.as_dict(),
                               "result": self.result.as_dict(), "started_ns": self.started_ns})
        if not install_exclusive(workdir, "score.json", data):
            raise ValueError("duplicate score checkpoint")

    @classmethod
    def read(cls, workdir: Path, request: BlobRef) -> ScoreCheckpoint:
        row = fields(read_record(workdir / "score.json"), {"schema", "request", "result", "started_ns"})
        version(row["schema"], CHECKPOINT_SCHEMA)
        result = cls(BlobRef.from_dict(row["request"]), ComponentJudgment.from_dict(row["result"]), row["started_ns"])
        if result.request != request:
            raise ValueError("score checkpoint belongs to another dispatch")
        return result


class PhaseWatch:
    """Called by the existing process supervisor; it owns no timer or process."""

    def __init__(self, workdir: Path, request: BlobRef, policy: JudgePolicy, began: float,
                 cancelled: Callable[[], bool] | None = None,
                 on_decided: Callable[[ComponentJudgment], None] | None = None):
        self.workdir, self.request, self.policy, self.began = workdir, request, policy, began
        self.cancelled = cancelled
        self.on_decided = on_decided
        self.checkpoint: ScoreCheckpoint | None = None
        self.stop_reason: str | None = None

    def refresh(self) -> None:
        if self.checkpoint is not None or self.stop_reason in {
            "judge-checkpoint-invalid", "judge-checkpoint-publication-failed",
        }:
            return
        try:
            checkpoint = ScoreCheckpoint.read(self.workdir, self.request)
            start = checkpoint.started_ns / 1e9
            if not self.began <= start <= time.monotonic():
                raise ValueError("checkpoint phase start is outside this worker invocation")
            if start > self.began + self.policy.score_timeout_seconds:
                raise ValueError("score was decided after its phase deadline")
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError):
            traceback.print_exc()
            self.stop_reason = "judge-checkpoint-invalid"
            return
        try:
            if self.on_decided is not None:
                self.on_decided(checkpoint.result)
        except Exception:  # noqa: BLE001 -- publication may have partially committed; never retry it here
            traceback.print_exc()
            self.stop_reason = "judge-checkpoint-publication-failed"
            return
        # Only a successful publication makes the checkpoint available to the
        # final handoff. Later polls must not repeat a partially failed callback.
        self.checkpoint = checkpoint

    def __call__(self) -> bool:
        if self.cancelled is not None and self.cancelled():
            return True
        self.refresh()
        if self.stop_reason is not None:
            return True
        deadline = self.began + self.policy.score_timeout_seconds
        analyzing = (self.checkpoint is not None and self.checkpoint.result.score is not None
                     and self.policy.analysis is not None)
        if self.checkpoint is not None and not analyzing:
            # The final concrete decision and its evidence have been published.
            # No requested work remains; let the existing supervisor finish
            # namespace cleanup instead of waiting for Python graph teardown or
            # spending the rest of the scoring deadline. The worker consumer
            # still requires that cleanup receipt before returning the decision.
            return True
        if analyzing:
            deadline = self.checkpoint.started_ns / 1e9 + self.policy.analysis.wall_timeout_seconds
        if time.monotonic() >= deadline:
            self.stop_reason = "analysis-timeout" if analyzing else "judge-worker-timeout"
            return True
        return False


WORKER_SCHEMA = "behave.judge-worker/5"


def _stage(request: ComparisonRequest, store: DirectoryStore) -> bytes:
    request.inputs.stage_contents(store)
    return canonical_json({
        "schema": WORKER_SCHEMA, "inputs": request.inputs.as_dict(), "dispatch": request.dispatch,
        "score_deadline": request.score_deadline,
    })


def _restore(packet: bytes, store: DirectoryStore, workdir: Path) -> ComparisonRequest:
    row = fields(parse_json(packet), {"schema", "inputs", "dispatch", "score_deadline"})
    version(row["schema"], WORKER_SCHEMA)
    return ComparisonRequest(ComparisonInputs.from_dict(row["inputs"], store), workdir, row["dispatch"],
                             score_deadline=row["score_deadline"])


def run_worker(request: ComparisonRequest, objects: DirectoryStore) -> ComponentJudgment:
    began = time.monotonic()
    result = _run_worker(request, objects, began)
    return replace(result, elapsed_seconds=time.monotonic() - began)


def _run_worker(request: ComparisonRequest, objects: DirectoryStore, began: float) -> ComponentJudgment:
    request = replace(request, score_deadline=began + request.policy.score_timeout_seconds)
    options = request.policy.analysis
    ceiling = request.policy.score_timeout_seconds + (0 if options is None else options.wall_timeout_seconds)
    deadline = began + ceiling
    local = DirectoryStore(request.workdir / "objects")
    packet = _stage(request, local)
    packet_ref = local.put(packet)
    def publish_score(decided: ComponentJudgment) -> None:
        objects.publish(local, decided.closure)
        # Early workflow publication is the optional-analysis API. A baseline
        # checkpoint is an internal handoff; return it only after supervision
        # has supplied the same cleanup receipt as every other baseline score.
        if request.on_decided is not None and options is not None and decided.score is not None:
            request.on_decided(decided)

    watch = PhaseWatch(request.workdir, packet_ref, request.policy, began, request.cancelled, publish_score)
    got = exchange("judge", packet, workdir=request.workdir, objects=objects, deadline=deadline,
                   timeout_seconds=ceiling, log_bytes=request.policy.worker_log_bytes, cancelled=watch,
                   cpu_pool=request.cpu_pool)
    # Also collect a fast worker which finished between supervisor polls.
    # Publication never asserts cleanup or authorizes an overlapping retry.
    watch.refresh()
    if watch.checkpoint is not None:
        return _after_checkpoint(request, local, objects, watch, got)
    code = got.code if not got.cleanup.quiescent else watch.stop_reason or got.code
    if code is not None:
        origin = "verifier" if code in {"judge-worker-invalid-response", "judge-checkpoint-invalid"} else "infrastructure"
        retryable = got.retryable and watch.stop_reason not in {
            "judge-checkpoint-invalid", "judge-checkpoint-publication-failed",
        }
        return ComponentJudgment(None, code, origin, retryable, got.evidence, cleanup=got.cleanup)
    try:
        result = ComponentJudgment.from_dict(got.result)
        if result.analysis is not None:
            raise ValueError("analysis was returned without a score checkpoint")
        objects.publish(local, result.closure)
    except (OSError, ValueError, TypeError):
        traceback.print_exc()
        return ComponentJudgment(None, "judge-worker-invalid-response", "verifier", True, got.evidence)
    return replace(result, evidence=(*result.evidence, *got.evidence), cleanup=got.cleanup)


def _after_checkpoint(request: ComparisonRequest, local: DirectoryStore, objects: DirectoryStore,
                      watch: PhaseWatch, got: WorkerResponse) -> ComponentJudgment:
    """Retain the exact decision across teardown or optional analysis."""
    decided = watch.checkpoint.result
    # PhaseWatch already published this evidence before accepting the checkpoint.
    options = request.policy.analysis
    if options is None or decided.score is None:
        return replace(decided, evidence=(*decided.evidence, *got.evidence), cleanup=got.cleanup)
    policy = objects.put(canonical_json(options.as_dict()))
    code = got.code
    # Cleanup failure must stay visible; a phase timeout cannot hide live work.
    if got.cleanup.quiescent:
        code = watch.stop_reason or code
    if code is None:
        try:
            final = ComponentJudgment.from_dict(got.result)
            if replace(final, analysis=None) != decided or final.analysis is None:
                raise ValueError("analysis changed the checkpoint score or omitted its result")
            if final.analysis.policy != policy:
                raise ValueError("analysis used a different policy")
            objects.publish(local, final.analysis.closure)
            if final.analysis.outcome is not None:
                from behave.sim import api
                outcome = api.AnalysisOutcome.from_dict(parse_json(read_verified(objects, final.analysis.outcome)))
                if outcome.concrete != final.analysis.concrete:
                    raise ValueError("native analysis belongs to another evaluation")
            return replace(final, evidence=(*final.evidence, *got.evidence), cleanup=got.cleanup)
        except Exception:  # noqa: BLE001 - ancillary decoding cannot erase the checkpoint
            traceback.print_exc()
            code = "analysis-invalid-response"
    report = AnalysisReport(decided.detail["evaluation"], policy, code)
    return replace(decided, analysis=report, evidence=(*decided.evidence, *got.evidence), cleanup=got.cleanup)


def main() -> None:
    # This entry point is fixed by the coordinator, never selected by a task.
    root = Path(sys.argv[1])
    reference = BlobRef(sys.argv[2], int(sys.argv[3]))
    store = DirectoryStore(root / "objects")
    packet = read_verified(store, reference)
    workdir = root / "execution"
    workdir.mkdir()
    request = _restore(packet, store, workdir)
    from .simulator_judge import _SimulatorService

    def on_decided(result):
        # The service wrote its evidence to this CAS. The coordinator verifies
        # it when publishing to persistent storage, before exposing a score.
        ScoreCheckpoint(reference, result, time.monotonic_ns()).install(root)

    try:
        result = _SimulatorService().evaluate(request, store, on_decided=on_decided)
        if type(result) is not ComponentJudgment:
            raise TypeError("judge service must return ComponentJudgment")
    except Exception as error:  # noqa: BLE001 - trusted service exception, never a candidate zero
        traceback.print_exc()
        result = ComponentJudgment(None, "judge-backend-failed", "verifier", True,
                                   detail={"error_type": type(error).__name__})
    response = canonical_json({"schema": WORKER_SCHEMA, "request": reference.as_dict(), "result": result.as_dict()})
    if not install_exclusive(root, "response.json", response):
        raise ValueError("judge response already exists")
