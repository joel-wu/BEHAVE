"""Shared preparation and concrete replay for an admitted evaluation.

The command freezes its catalogue and mandatory baseline before generation.
Budget expiry retains evidence but does not waive mandatory tests.
A trusted negative witness or terminal no-result failure can stop replay. Search
and scoring share completed attempts within one invocation. Matching derives
both streams from the task-bound reference and trace. This service neither
decides the persisted result nor starts analysis.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from threading import Event
from time import perf_counter

from .core.admission import AdmittedTask
from .core.catalogue import SAFETY, CatalogueManifest, catalogue_of, merge_catalogues
from .core.compare import comparison_for, transfers_of
from .core.failure import CandidateFault, FailureRaised, RunFailure
from .core.observation_monitor import execution_monitor_for
from .core.outcome import ConcreteEvaluation
from .core.reference import LoadedReference, expected_of
from .core.refusal import Refusal, Refused
from .core.run_record import (
    ONE_AT_A_TIME,
    ONE_PROCESS_BATCH,
    PER_PROCESS,
    VERIFIED_EVERY,
    VERIFIED_NOT_NEEDED,
    VERIFIED_SAMPLE,
)
from .core.stimulus import AuditedTrace, Stimulus, canonical_json
from .cpu_admission import cpu_usage
from .generation import GeneratedStimulusSet
from .replay_parallel import ordered_map, replay_workers
from .replay_suite import ReplaySuite, workload_window
from .run_assessment import CheckedRun, RunAssessment, assess_checked, check_run, replay_one
from .scoring_policy import ScoringStopped, active_scoring, check_scoring

compare_run = comparison_for
transfers = transfers_of


class ReplayExecution:
    """One invocation's executions, shared by search and final assessment.

    Only this service produces the retained records. There is no import of a
    historical trace or score. Completed failures are retained just like passes;
    a checked trace awaiting its first comparison does not need another replay.
    """

    def __init__(self, task: AdmittedTask, artifact, reference: LoadedReference | None,
                 catalogue: CatalogueManifest, *, capture_query_boundaries=True,
                 query_boundary_phases=None):
        self.task, self.artifact = task, artifact
        self.reference, self.catalogue = reference, catalogue
        self.capture_query_boundaries = capture_query_boundaries
        # None preserves full capture for standalone replay and exact analysis.
        # A command can name only the phases whose records seed local queries;
        # all phases still retain native events and complete scoring evidence.
        self.query_boundary_phases = (None if query_boundary_phases is None else
                                      frozenset(query_boundary_phases))
        self._checked: dict[bytes, CheckedRun] = {}
        self._assessments: dict[bytes, RunAssessment] = {}
        self._settled: set[bytes] = set()
        self._completion = {}
        self._checkpoint_plans = {}
        self._requested = {}
        self._calls = 0
        self._elapsed = 0.0
        self._stage = None
        self._interrupted_stage = None
        self._interrupted_tool = None
        self._negative = False
        self._witness_blocked = False

    def evaluate(self, stimuli: Sequence[Stimulus] | ReplaySuite, *, telemetry=None,
                 stop_on_failure=False) -> ConcreteEvaluation:
        result = evaluate(self.task, stimuli, self.artifact, self.reference,
                          catalogue=self.catalogue, telemetry=telemetry, _execution=self,
                          stop_on_failure=stop_on_failure)
        # Read the ordinary scorer once per completed call. Search must neither
        # replay nor serialize the accumulated pool to ask whether it can stop.
        if result.failure_witness_blocked:
            self._witness_blocked = True
        if result.failure_witness is not None:
            self._negative = True
        self._settled.update(run.stimulus.identity_bytes for run in result.results)
        return result

    @property
    def has_failure_witness(self):
        """A retained candidate counterexample, not a source-guidance goal hit.

        Reference/verifier defects from any preceding call remain disqualifying;
        evaluating a different subset cannot silently forget those failures.
        """
        return self._negative and not self._witness_blocked

    def release(self) -> None:
        # Deferred providers outlive the command for provenance/analysis. Do not
        # let their callbacks retain discarded search traces after aggregation.
        self._checked.clear()
        self._assessments.clear()
        self._settled.clear()
        self._completion.clear()
        self._checkpoint_plans.clear()
        self._requested.clear()
        self._negative = self._witness_blocked = False

    def traces(self, stimuli: Sequence[Stimulus]) -> tuple[AuditedTrace | Refusal, ...]:
        # Search sees actual observations, not comparison outcomes or symbolic hits.
        stimuli = tuple(stimuli)
        if not stimuli:
            return ()
        unique = {seed.identity_bytes: seed for seed in stimuli}
        missing = tuple(seed for key, seed in unique.items() if key not in self._settled)
        if missing:
            self.evaluate(missing)
        check_scoring()  # An interrupted seed is not a source replay record.
        # Both directions read this invocation's immutable checked records. Asking
        # for traces is not another whole-pool scoring/aggregation operation.
        results = (self._assessments[key].result for key in unique)
        traces = {run.stimulus.identity_bytes: (
            run.trace if run.trace is not None and run.observations_eligible else run.refused.reason)
            for run in results}
        return tuple(traces[seed.identity_bytes] for seed in stimuli)

    def feedback(self, stimuli, source_catalogue):
        """Read this candidate's completed observations, without Select or replay.

        Only candidate guidance is wired to this method. A reference Behavior has
        its own source catalogue and must obtain its own execution evidence.
        """
        from .generation_protocol import (ReplayFeedback)

        self.catalogue.reconcile(source_catalogue, "baseline-feedback", exhaustive=False)
        self.catalogue.reconcile_occurrences(
            source_catalogue.occurrences, "baseline-feedback", exhaustive=False)
        declared = set(source_catalogue)
        feedback = []
        for seed in stimuli:
            assessment = self._assessments[seed.identity_bytes]
            trace = assessment.result.trace
            if not assessment.result.observations_eligible:
                feedback.append(assessment.result.refused.reason)
            else:
                # Identify the already-validated positive observation, not the
                # potentially huge failure logs/native payload a second time.
                record = (trace.fingerprint if trace is not None else
                          hashlib.sha256(canonical_json(
                              assessment.result.run_failure.observed.as_dict()).encode()).hexdigest())
                feedback.append(ReplayFeedback(seed.fingerprint, source_catalogue.digest(),
                    record, assessment.hits & declared))
        return tuple(feedback)

    def reference_feedback(self, stimuli, source_catalogue):
        """Read reference-source hits retained by matching, not candidate hits."""
        from .core.reference_observation import ReferenceSourceObservation
        from .generation_protocol import (ReplayFeedback)

        feedback = []
        for seed in stimuli:
            result = self._assessments[seed.identity_bytes].result
            observed = (result.comparison.reference_source if result.was_compared else None)
            if type(observed) is Refusal:
                feedback.append(observed)
            elif type(observed) is not ReferenceSourceObservation:
                feedback.append(Refusal("reference-source-observation-unavailable",
                                        "this reference run supplied no native source feedback"))
            else:
                source_catalogue.reconcile(observed.catalogue, "reference-feedback")
                source_catalogue.reconcile_occurrences(
                    observed.catalogue.occurrences, "reference-feedback")
                feedback.append(ReplayFeedback(seed.fingerprint, source_catalogue.digest(),
                                               observed.fingerprint, observed.hits))
        return tuple(feedback)


def _parallel_reference(reference):
    """Only the built-in isolated loader proves independent concurrent runs."""
    if reference is None:
        return True
    if type(reference) is not LoadedReference:
        return False
    from .core.reference import _adapter_identity
    from .reference_isolated import ADAPTER, isolated_loader

    return (reference.artifact.adapter == ADAPTER
            and reference.artifact.adapter_impl == _adapter_identity(ADAPTER, isolated_loader))


def evaluate_generated(
    generated: GeneratedStimulusSet,
    artifact,
    reference: LoadedReference | None = None,
    *,
    telemetry: Mapping[str, object] | None = None,
    _execution: ReplayExecution | None = None,
    stop_on_failure=False,
) -> ConcreteEvaluation:
    """Assess the exact task, catalogue and stimulus set frozen by generation.

    Callers may add telemetry but cannot replace the generation record.
    """

    if type(generated) is not GeneratedStimulusSet:
        raise Refused(
            Refusal(
                "evaluation-generated-set-not-typed",
                "concrete evaluation after generation requires the frozen admitted task "
                "and GenerationPortfolio as one GeneratedStimulusSet",
                got=type(generated).__name__,
            )
        )
    attached = dict(telemetry or {})
    if "generation" in attached:
        raise Refused(
            Refusal(
                "evaluation-generation-telemetry-replaced",
                "the generation record comes from the GeneratedStimulusSet itself; a "
                "caller cannot attach a second account of how Xi_A was produced",
            )
        )
    attached["generation"] = generated.portfolio.as_dict()
    suite = generated.replay_suite
    return evaluate(
        generated.task,
        suite if suite is not None else generated.stimuli,
        artifact,
        reference=reference,
        catalogue=generated.catalogue,
        telemetry=attached,
        _execution=_execution,
        stop_on_failure=stop_on_failure,
        required_stimuli=generated.baseline if suite is None else suite.required_stimuli,
    )


def run(task: AdmittedTask, stimulus: Stimulus, artifact, *, expected_outputs=None) -> AuditedTrace | Refusal | RunFailure:
    """Run one stimulus on the artifact bound by admission.

    Dispatch uses the declared artifact kind. Transaction references are consumed
    by matching, never substituted for a signal-level device.
    """
    bound = task.binds(artifact)
    if bound is False:
        raise Refused(
            Refusal(
                "run-artifact-not-the-admitted-one",
                "this artifact is not the one admission checked against the contract, so "
                "every refusal admission made was about a design that is not under test",
                admitted=task.realization.identity,
                running=str(getattr(artifact, "identity", "") or ""),
            )
        )
    if bound is None:
        raise Refused(
            Refusal(
                "run-artifact-identity-unstated",
                "either the realization or the artifact does not say which candidate it is, "
                "so nothing can check that the thing admitted is the thing being run; "
                "'some artifact realizes this contract' is not a statement about this one",
                admitted=task.realization.identity or None,
                running=str(getattr(artifact, "identity", "") or "") or None,
            )
        )
    kind = getattr(artifact, "kind", "reference")
    if kind == "rtl":
        return artifact.trace(task.contract, stimulus, **(
            {} if expected_outputs is None else {"expected_outputs": expected_outputs}))
    if kind == "device":
        # Test devices use the same protocol driver as RTL replay.
        if len(task.contract.clocks) > 1:
            from .core.atomic import run_atomic_in_memory

            return run_atomic_in_memory(task.contract, stimulus, artifact)
        from .core.driver import run_in_memory

        return run_in_memory(task.contract, stimulus, artifact, expected_outputs=expected_outputs)
    if kind == "behavior":
        return artifact.trace(task.contract, stimulus)
    raise Refused(
        Refusal(
            "evaluate-artifact-kind-unsupported",
            "this build runs an RTL design through the simulator's causal driver. A "
            "transaction-level reference is **not** run this way and never was correct to: "
            "it consumes what the artifact accepted, through `expected_for`, because a "
            "reference given the stimulus instead can consume a different set of "
            "transactions than a design that stalls",
            artifact_kind=kind,
        )
    )


def evaluate(
    task: AdmittedTask,
    stimuli: Sequence[Stimulus] | ReplaySuite,
    artifact,
    reference: LoadedReference | None = None,
    *,
    catalogue: CatalogueManifest,
    telemetry: Mapping[str, object] | None = None,
    _execution: ReplayExecution | None = None,
    stop_on_failure=False,
    required_stimuli=None,
) -> ConcreteEvaluation:
    execution = _execution or ReplayExecution(task, artifact, reference, catalogue)
    if (execution.task is not task or execution.artifact is not artifact
            or execution.reference is not reference or execution.catalogue != catalogue):
        raise ValueError("replay records belong to another invocation configuration")
    requested = stimuli.stimuli if type(stimuli) is ReplaySuite else tuple(stimuli)
    if required_stimuli is None and type(stimuli) is ReplaySuite:
        required_stimuli = stimuli.required_stimuli
    execution._requested.update((s.identity_bytes, s) for s in requested)
    prior_completed = set(execution._assessments)
    began = perf_counter()
    try:
        check_scoring()
        return _evaluate(task, stimuli, artifact, reference, catalogue=catalogue,
                         telemetry=telemetry, _execution=execution,
                         stop_on_failure=stop_on_failure, required_stimuli=required_stimuli)
    except ScoringStopped as interruption:
        # All replay workers have joined before cancellation reaches here. Keep
        # their committed assessments, including faults; never rerun or select
        # only successful members. Unfinished output/memory comparisons do not
        # establish passes, but already checked safety violations remain visible.
        from .core.run_record import RunResult

        # Cancellation is control flow, not a failure diagnostic. Its unwound
        # frames otherwise retain the unfinished decoder/comparator payloads
        # while we serialize the already committed evidence.
        interruption.__traceback__ = None
        if execution._interrupted_stage is None:
            execution._interrupted_stage = execution._stage
        if execution._interrupted_tool is None:
            execution._interrupted_tool = interruption.interrupted_tool
        for key, checked in execution._checked.items():
            safety = sorted(g for g in checked.hits if catalogue[g].cls == SAFETY)
            if safety:
                stimulus = execution._requested[key]
                stopped = RunFailure.candidate(
                    "trace-monitor", Refusal("candidate-safety-violated",
                        "a checked safety violation precedes the interrupted comparison", goals=safety))
                execution._assessments[key] = RunAssessment(
                    RunResult.not_compared(stimulus, checked.trace, stopped),
                    checked.hits, checked.report, costs=checked.costs)
        execution._checked.clear()
        checked = None
        retained = [execution._assessments[s.identity_bytes] for s in requested
                    if s.identity_bytes in execution._assessments]
        attached = dict(telemetry or {})
        attached["scoring_budget"] = active_scoring().receipt()
        costs = [{} if row.result.stimulus.identity_bytes in prior_completed else dict(row.costs)
                 for row in retained]
        elapsed = perf_counter() - began
        attached["concrete_execution"] = {
            "wall_seconds": elapsed, "stop_reason": "scoring-budget-exhausted",
            "interrupted_stage": execution._interrupted_stage,
            "interrupted_tool": execution._interrupted_tool,
            "work_seconds": {name: sum(row.get(name, 0) for row in costs)
                             for name in ("replay", "monitor", "comparison")},
            "run_costs": costs,
            "reuse": {"scope": "invocation", "completed_runs": sum(
                row.result.stimulus.identity_bytes in prior_completed for row in retained),
                "preceding_calls": execution._calls, "preceding_wall_seconds": execution._elapsed},
        }
        execution._calls += 1
        execution._elapsed += elapsed
        _attach_rtl_toolchain(attached, artifact)
        if type(stimuli) is ReplaySuite:
            attached["replay_suite"] = stimuli.as_dict()
            attached["workload_windows"] = _completed_workload_windows(
                task, (row.result for row in retained), execution._completion)
        return _assemble(task, retained, catalogue, requested, attached, budget_exhausted=True,
                         required_stimuli=required_stimuli)


def _evaluate(task, stimuli, artifact, reference, *, catalogue, telemetry=None,
              _execution=None, stop_on_failure=False, required_stimuli=None):
    """Complete every run assessment and assemble one ConcreteEvaluation.

    Each new run uses a fresh reference instance. Missing reference evidence
    yields no agreement claim. Scoring may stop dispatch after a checked negative
    witness or a failure that precludes a score; unexecuted members stay explicit.
    Exhaustive diagnostic replay remains available with stop_on_failure=False.
    """
    execution = _execution or ReplayExecution(task, artifact, reference, catalogue)
    execution._stage = "catalogue-validation"
    prior_cpu_usage = cpu_usage()
    prior_reservations = 0 if prior_cpu_usage is None else len(prior_cpu_usage["reservations"])
    suite = stimuli if type(stimuli) is ReplaySuite else None
    if suite is not None:
        if suite.contract != task.contract.digest:
            raise ValueError("the replay suite belongs to another contract")
        stimuli = suite.stimuli
    if reference is not None and not isinstance(reference, LoadedReference):
        raise Refused(
            Refusal(
                "evaluate-reference-not-a-bundle",
                "the reference is the bundle its loader returned, so this can build a "
                "fresh model per run. A live model passed in is one instance shared across "
                "the whole stimulus set, and for a model that accumulates that makes every "
                "run after the first depend on the ones before it",
                got=type(reference).__name__,
            )
        )
    if type(catalogue) is not CatalogueManifest:
        raise TypeError("concrete evaluation requires the exact pre-generation CatalogueManifest")
    # The execution monitor is needed for replay. Reconcile its declarations
    # with the source component below; do not rebuild the preparation policy.
    monitor = execution_monitor_for(task.contract, task.realization.kind)
    monitor_catalogue = (CatalogueManifest(()) if monitor is None
                         else catalogue_of(monitor.goals(), allow_legacy=False))
    # Re-elaborate only through the controlled RTL frontend.  This is the last check
    # before replay and closes the direct public API as well as CommandRequest: the
    # simulator that will emit structural hits must own the frontend component of this
    # exact merged manifest.  A selected Behavior frontend is instead bound through
    # a closed Behavior handle before generation; an arbitrary in-memory device is not
    # allowed to advertise a same-named catalogue method as a second authority.
    from .rtl.artifact import RTLArtifact
    from .rtl.tool_protocol import ToolIsolationError

    if type(artifact) is RTLArtifact:
        try:
            frontend = artifact.frontend_catalogue(expected_identity=task.realization.identity)
            expected = merge_catalogues(frontend, monitor_catalogue)
        except Refused:
            raise
        except ToolIsolationError as exc:
            raise Refused(
                Refusal(exc.code, exc.why, tool_executions=[item.as_dict() for item in exc.executions])
            ) from None
        except Exception as exc:  # noqa: BLE001 - frontend boundary
            raise Refused(
                Refusal(
                    "evaluation-artifact-catalogue-unavailable",
                    "the RTL artifact could not reproduce the structural manifest frozen before generation",
                    error=f"{type(exc).__name__}: {exc}",
                )
            ) from None
        if expected != catalogue:
            raise Refused(
                Refusal(
                    "evaluation-artifact-catalogue-mismatch",
                    "the RTL artifact that would emit structural hits does not own the "
                    "manifest frozen before generation",
                    generated=catalogue.digest(),
                    artifact=expected.digest(),
                )
            )
    from .behavior.artifact import BehaviorArtifact

    if type(artifact) is BehaviorArtifact:
        expected = merge_catalogues(artifact.frontend_catalogue, monitor_catalogue)
        if expected != catalogue:
            raise Refused(
                Refusal(
                    "evaluation-behavior-catalogue-mismatch",
                    "the Behavior runner and concrete evaluation name different frozen frontend catalogues",
                    generated=catalogue.digest(),
                    artifact=expected.digest(),
                )
            )
    # A transaction-level Behavior has no ready/valid timing to monitor.  Its logical
    # output batches are still compared under the same task policy, and its structural
    # goals still come from each slot record. Logical transaction predicates are
    # shared with RTL; signal-level protocol obligations remain RTL-only.
    assumptions = set()

    # Monitor construction is executable state, not a second catalogue authority.  It
    # must reproduce its component of the manifest fixed before generation; a missing
    # or stale task obligation is refused before the first artifact replay.
    if monitor is not None:
        catalogue.reconcile(monitor_catalogue, "monitor", exhaustive=False)

    reset_state_fields = None
    replay_state_names = ()
    replay_state_widths = None
    reset_state_capability = None
    if type(artifact) is RTLArtifact and any(clock.reset_restores is True for clock in task.contract.clocks):
        execution._stage = "reset-state-ownership"
        try:
            ownership = artifact.state_ownership(task.contract, expected_identity=task.realization.identity)
        except Refused as exc:
            # Artifact identity is score authority, so a mismatch or a source change
            # still stops the run.  A state-representation limitation is different:
            # output comparison remains well defined, while the reset obligation is
            # explicitly unresolved because no complete state carrier was observed.
            if exc.refusal.kind in {
                "rtl-state-ownership-artifact-mismatch",
                "rtl-state-ownership-source-changed",
                "rtl-state-ownership-source-changed-during-elaboration",
            }:
                raise
            reset_state_capability = {
                "status": "unresolved",
                "refusal": exc.refusal.as_dict(),
            }
        except ToolIsolationError as exc:
            reset_state_capability = {
                "status": "unresolved",
                "refusal": Refusal(
                    exc.code, exc.why, tool_executions=[item.as_dict() for item in exc.executions]
                ).as_dict(),
            }
        except Exception as exc:  # noqa: BLE001 - frontend capability boundary
            reset_state_capability = {
                "status": "unresolved",
                "refusal": Refusal(
                    "rtl-reset-state-ownership-unavailable",
                    "the RTL frontend could not freeze the complete represented "
                    "state needed by the declared reset-restoration monitor",
                    error=f"{type(exc).__name__}: {exc}",
                ).as_dict(),
            }
        else:
            replay_state_names = ownership.names
            replay_state_widths = dict(ownership.widths)
            reset_state_fields = dict(ownership.by_domain(task.contract)) if len(task.contract.clocks) > 1 else ownership.names
            reset_state_capability = {
                "status": "available",
                "ownership": ownership.digest,
                "fields": len(ownership.fields),
            }

    # Validate the canonical set without silently dropping duplicate evidence.
    execution._stage = "stimulus-validation"
    seen, unique = set(), []
    for stimulus in stimuli:
        if stimulus.fingerprint in seen:
            raise Refused(
                Refusal(
                    "evaluate-stimulus-set-is-not-a-set",
                    "two members of this stimulus set are the same stimulus, so the set's "
                    "size is not the number of runs and every rate over it is wrong. "
                    "Canonicalise and deduplicate before evaluating",
                    repeated=stimulus.fingerprint,
                    offered=len(stimuli),
                )
            )
        seen.add(stimulus.fingerprint)
        unique.append(stimulus)

    # A worker owns replay *and* assessment. Only closed production artifacts
    # and the isolated reference qualify; extensible test devices remain serial.
    parallel = type(artifact) in (RTLArtifact, BehaviorArtifact) and _parallel_reference(reference)
    workers = replay_workers() if parallel else 1
    started = perf_counter()
    prior_completed = {s.identity_bytes for s in unique if s.identity_bytes in execution._assessments}
    prior_checked = {s.identity_bytes for s in unique if s.identity_bytes in execution._checked}
    prior_observed = prior_completed | prior_checked
    stopped = Event()
    assessed = {key: execution._assessments[key] for key in prior_completed}

    def retain(assessment):
        result = assessment.result
        assessed[result.stimulus.identity_bytes] = assessment
        if stop_on_failure and result.refused is not None and not result.determines_score:
            # This invocation retains failed attempts too. More replay cannot
            # repair one, and a new CPU reservation after failed cleanup would
            # replace the original diagnostic with a pool-quarantine error.
            stopped.set()
        if stop_on_failure and (result.determines_score
                                or (result.was_compared and not result.ok)
                                or any(catalogue[goal].cls == SAFETY for goal in assessment.hits)):
            witness = ConcreteEvaluation(task, (result,), hits=assessment.hits,
                                         catalogue=catalogue)
            if witness.failure_witness is not None:
                stopped.set()
        return assessment

    for previous in tuple(assessed.values()):
        retain(previous)
    completion = execution._completion
    completion_failures = {}
    completion_planner = None
    execution._stage = "completion-planning"
    planning_costs = []
    if task.realization.kind != "behavior":
        from .runtime_execution import completion_plan

        def plan_completion(stimulus):
            began = perf_counter()
            try:
                target = completion_plan(task, stimulus, reference)
                if target is not None:
                    completion[stimulus.fingerprint] = target.totals
                    scoring = active_scoring()
                    if scoring is not None and scoring.policy.transaction_checkpoints:
                        execution._checkpoint_plans[stimulus.fingerprint] = target
                return target
            except FailureRaised as exc:
                failure = exc.failure
            except Refused as exc:
                failure = RunFailure.verifier("runtime-completion", exc.refusal)
            finally:
                planning_costs.append(perf_counter() - began)
            # Another concurrent planner may hit the deadline before ordered_map
            # returns. A real reference/verifier error must survive that stop.
            execution._assessments[stimulus.identity_bytes] = check_run(stimulus, failure)
            return failure

        # Reusing a completed run (or a fixed schedule) needs no planning worker.
        # Do not queue for CPUs just to read this invocation's retained evidence.
        # The production RTL lane owns count planning, execution and checking.
        # Unstarted requests must not hold up the first useful replay or consume
        # its reference budget. No extra worker pool or CPU reservation is needed.
        if parallel and type(artifact) is RTLArtifact:
            completion_planner = plan_completion
        to_plan = (() if stopped.is_set() or completion_planner is not None else [s for s in unique
                   if s.identity_bytes not in prior_observed and s.replay_policy is not None])
        for stimulus, target in zip(to_plan, ordered_map(plan_completion, to_plan, workers)):
            if type(target) is RunFailure:
                completion_failures[stimulus.fingerprint] = target
    completion_seconds = perf_counter() - started

    def assess(stimulus, trace, *, replay_seconds=None):
        if type(trace) not in (CheckedRun, RunAssessment):
            local_monitor = (execution_monitor_for(task.contract, task.realization.kind)
                             if parallel and monitor is not None
                             else monitor)
            trace = check_run(stimulus, trace, monitor=local_monitor,
                              reset_state_fields=reset_state_fields,
                              replay_seconds=replay_seconds)
        if type(trace) is CheckedRun:
            execution._checked[stimulus.identity_bytes] = trace
        assessment = assess_checked(task, stimulus, trace, reference, compare=compare_run)
        # Comparison failures are completed attempts too. Keeping only their
        # CheckedRun would silently retry the reference/comparator during final
        # assembly and could replace the original no-result with a pass.
        execution._assessments[stimulus.identity_bytes] = assessment
        execution._checked.pop(stimulus.identity_bytes, None)
        return retain(assessment)

    # These reference/verifier attempts already happened during planning. Keep
    # their failures even if another run stops dispatch before their queue slot.
    for stimulus in unique:
        if stimulus.fingerprint in completion_failures:
            assess(stimulus, completion_failures[stimulus.fingerprint], replay_seconds=0.0)

    # The private RTL continuation runs inside its existing bounded worker, not
    # in a second pool. Build/early admission failures still arrive as raw values.
    from .rtl.trace_postprocessing import MonitorPlan

    monitor_plan = MonitorPlan(reset_state_fields) if parallel and monitor is not None else None
    def replay_group(group, phase):
        if stopped.is_set():
            return [assessed[s.identity_bytes] for s in group if s.identity_bytes in assessed], None
        missing = [s for s in group if s.identity_bytes not in execution._assessments
                   and s.identity_bytes not in execution._checked]
        batched, note = _batched(
            task, missing, artifact,
            state_names=replay_state_names, state_widths=replay_state_widths,
            consume=assess if parallel and type(artifact) is RTLArtifact else None,
            workers=workers if parallel else None,
            monitor_plan=monitor_plan, completion_targets=completion,
            checkpoint_plans=execution._checkpoint_plans,
            completion_planner=completion_planner,
            capture_query_boundaries=((
                execution.query_boundary_phases is None
                or phase in execution.query_boundary_phases) and execution.capture_query_boundaries),
            stop=stopped.is_set if stop_on_failure else None)

        def one(stimulus):
            key = stimulus.identity_bytes
            if key in execution._assessments:
                return retain(execution._assessments[key])
            if key in execution._checked:
                return assess(stimulus, execution._checked[key])
            start = perf_counter()
            trace = replay_one(stimulus, lambda: (
                batched[stimulus.fingerprint] if batched else run(
                    task, stimulus, artifact, expected_outputs=completion.get(stimulus.fingerprint))))
            if type(trace) is RunAssessment:
                execution._assessments[key] = trace
                return retain(trace)
            return assess(stimulus, trace, replay_seconds=None if batched else perf_counter() - start)

        if note is not None:
            # The batch already joined its started workers. Consume every returned
            # record, but do not turn deliberately unstarted members into errors.
            ready = [s for s in group if s.identity_bytes in assessed
                     or s.identity_bytes in execution._checked
                     or s.fingerprint in batched]
            for stimulus in ready:
                one(stimulus)
        else:
            pending = [s for s in group if s.identity_bytes not in assessed]
            ordered_map(one, pending, workers if parallel else 1,
                        stop_when=(lambda _: stopped.is_set()) if stop_on_failure else None)
        return [assessed[s.identity_bytes] for s in group if s.identity_bytes in assessed], note

    assessments, phase_costs, batch_notes = [], [], []
    # Finish the independent runs before starting continuous workloads. The latter
    # are whole requests: no reset, reference restart or idle gap at member boundaries.
    execution._stage = "replay-and-comparison"
    for name, group in suite.phases if suite is not None else (("isolated", unique),):
        if not group:
            continue
        phase_start = perf_counter()
        checked, note = replay_group(group, name)
        assessments.extend(checked)
        phase_costs.append({"phase": name, "runs": len(checked), "requested_runs": len(group),
                            "wall_seconds": perf_counter() - phase_start})
        if note is not None:
            batch_notes.append(note)
    execution._stage = "aggregation"
    elapsed = perf_counter() - started
    results, run_costs = [], []
    for position, assessment in enumerate(assessments):
        result = assessment.result
        results.append(result)
        costs = dict(assessment.costs)
        # Retained evidence keeps its original timings; this call only reports
        # work performed now, not the same replay cost again at aggregation.
        if result.stimulus.identity_bytes in prior_completed:
            costs = {}
        elif result.stimulus.identity_bytes in prior_checked:
            costs = {name: seconds for name, seconds in costs.items() if name == "comparison"}
        run_costs.append(costs)

    telemetry = dict(telemetry or {})
    if batch_notes:
        telemetry["batch"] = batch_notes[0] if len(batch_notes) == 1 else {
            **batch_notes[0], "stimuli": sum(note["stimuli"] for note in batch_notes),
            "verified": (VERIFIED_SAMPLE if any(note["verified"] == VERIFIED_SAMPLE for note in batch_notes)
                         else batch_notes[0]["verified"]),
            "phases": batch_notes}
    if suite is not None:
        telemetry["replay_suite"] = suite.as_dict()
        telemetry["workload_windows"] = _completed_workload_windows(task, results, completion)
    telemetry["concrete_execution"] = {
        "version": "concrete-execution-cost/1",
        "strategy": "bounded-per-run" if parallel else "serial-assessment",
        "workers": workers,
        "wall_seconds": elapsed,
        "completion_planning_seconds": completion_seconds,
        "completion_planning_work_seconds": sum(planning_costs),
        "phases": phase_costs,
        "run_costs": run_costs,
        "work_seconds": {name: sum(row.get(name, 0.0) for row in run_costs)
                         for name in ("replay", "monitor", "comparison")},
    }
    if _execution is not None:
        telemetry["concrete_execution"]["reuse"] = {
            "scope": "invocation",
            "completed_runs": sum(s.identity_bytes in prior_completed for s in unique),
            "checked_traces": sum(s.identity_bytes in prior_checked for s in unique),
            "preceding_calls": execution._calls,
            "preceding_wall_seconds": execution._elapsed,
        }
        execution._calls += 1
        execution._elapsed += elapsed
    resources = cpu_usage()
    if resources is not None:
        resources["reservations"] = resources["reservations"][prior_reservations:]
        telemetry["concrete_execution"]["cpu_admission"] = resources
    telemetry["monitor_unchecked"] = [] if monitor is None else list(monitor.unchecked())
    if reset_state_capability is not None:
        telemetry["reset_state_capability"] = reset_state_capability
    if monitor is not None:
        assumptions.update(monitor.assumptions())
    telemetry["assumptions"] = sorted(assumptions)
    _attach_rtl_toolchain(telemetry, artifact)
    return _assemble(task, assessments, catalogue, unique, telemetry,
                     required_stimuli=required_stimuli)


def _attach_rtl_toolchain(telemetry, artifact):
    """Keep completed tool stages on interruption as well as completion."""
    from .rtl.artifact import RTLArtifact

    if type(artifact) is RTLArtifact:
        telemetry["rtl_toolchain"] = {
            "version": "rtl-tool-lineage/1",
            "executions": list(artifact.tool_lineage()),
        }


def _completed_workload_windows(task, results, completion):
    """Retain measured windows when a later replay exhausts the shared budget."""
    return {
        result.stimulus.fingerprint: workload_window(
            task.contract, result.trace,
            expected_outputs=completion[result.stimulus.fingerprint])
        for result in results
        if result.ran and len(task.contract.clocks) == 1
        and result.stimulus.fingerprint in completion}


def _assemble(task, assessments, catalogue, requested, telemetry, *, budget_exhausted=False,
              required_stimuli=None):
    """Pure aggregation, shared by normal completion and deadline finalization."""
    hits, assumptions = set(), set(telemetry.get("assumptions", ()))
    violations, outside, monitor_hits, unresolved = [], [], [], []
    opportunities = {}
    for position, assessment in enumerate(assessments):
        hits.update(assessment.hits)
        outside.extend((position, item.as_dict()) for item in assessment.outside)
        report = assessment.report
        if report is not None:
            monitor_hits.extend((position, hit.as_dict()) for hit in report.hits)
            violations.extend((position, bad.as_dict()) for bad in report.violations)
            unresolved.extend((position, item.as_dict()) for item in report.unresolved)
            for goal, count in report.opportunities.items():
                opportunities[goal] = opportunities.get(goal, 0) + count
        if assessment.result.comparison is not None:
            assumptions.update(assessment.result.comparison.assumptions)
    telemetry.update(monitor_violations=violations, environment_assumption_violations=outside,
                     monitor_hits=monitor_hits, monitor_unresolved=unresolved,
                     monitor_opportunities=dict(sorted(opportunities.items())),
                     assumptions=sorted(assumptions))
    return ConcreteEvaluation(task, results=[row.result for row in assessments], hits=hits,
                              telemetry=telemetry, catalogue=catalogue,
                              requested_stimuli=requested, budget_exhausted=budget_exhausted,
                              required_stimuli=required_stimuli)


def expected_for(task, reference, trace):
    """Read the reference stream for diagnostics.

    Scoring instead derives it inside the matcher, without an intermediate
    expected-output argument that a caller could substitute.
    """
    if isinstance(reference, LoadedReference):
        reference = reference.instantiate(task.contract)
    return expected_of(task, reference, trace)


def _replay_note(mode, verified, stimuli, why=""):
    """One replay's description, in the shape the evaluation reads and a person reads."""
    return {"mode": mode, "verified": verified, "stimuli": int(stimuli), "why": why}


def _failed_batch(stimuli, failure, mode):
    """Preserve one service failure for every affected stimulus; never retry it away."""
    return (
        {stimulus.fingerprint: failure for stimulus in stimuli},
        _replay_note(mode, VERIFIED_NOT_NEEDED, len(stimuli),
                     f"{failure.kind}: {failure.why}"),
    )


def _batched(task, stimuli, artifact, *, state_names=(), state_widths=None,
             consume=None, workers=None, monitor_plan=None, completion_targets=None, stop=None,
             capture_query_boundaries=True, checkpoint_plans=None, completion_planner=None):
    """Replay a set through the artifact's batch service when available.

    Per-process replay shares only the build. A shared-process batch records
    whether every stimulus, or only a sample, was checked against isolated runs.
    Evaluation policy refuses a score from sample-only evidence. Exceptions retain
    their attribution and cannot trigger a retry through another execution path.
    An explicitly withheld order-dependent batch may be replayed individually.
    """
    from .rtl.artifact import RTLArtifact

    per_process = getattr(artifact, "traces_per_process", None)
    if per_process is not None and stimuli and task.binds(artifact) is True:
        try:
            if type(artifact) is RTLArtifact:
                options = {} if consume is None else {
                    "_consume": consume, "workers": workers, "_monitor_plan": monitor_plan}
                options["_capture_query_boundaries"] = capture_query_boundaries
                if completion_targets:
                    options["_completion_targets"] = completion_targets
                if checkpoint_plans is not None:
                    options["_checkpoint_plans"] = checkpoint_plans
                if completion_planner is not None:
                    options["_completion_planner"] = completion_planner
                if stop is not None:
                    options["_stop"] = stop
                got = per_process(task.contract, list(stimuli), state_names=state_names,
                                  state_widths=state_widths, **options)
            else:
                got = per_process(task.contract, list(stimuli))
        except FailureRaised as exc:
            return _failed_batch(stimuli, exc.failure, PER_PROCESS)
        except CandidateFault as exc:
            return _failed_batch(stimuli, RunFailure.candidate(
                "candidate-observation", exc.refusal), PER_PROCESS)
        except Refused as exc:
            stopped = RunFailure.verifier("batched-replay-admission", exc.refusal)
            return (
                {stimulus.fingerprint: stopped for stimulus in stimuli},
                _replay_note(PER_PROCESS, VERIFIED_NOT_NEEDED, len(stimuli), f"{stopped.kind}: {stopped.why}"),
            )
        except Exception as exc:  # noqa: BLE001
            # The production RTL path has one declared isolation boundary.  A
            # capability/setup failure is evidence that no run was produced, not an
            # invitation to execute the same tool tree through a weaker path.
            if type(artifact) is RTLArtifact:
                stopped = RunFailure.infrastructure(
                    "rtl-replay-service",
                    Refusal(
                        "rtl-isolated-replay-failed",
                        "the declared per-stimulus RTL isolation path did not "
                        "complete; there is no production compatibility fallback",
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                )
                return (
                    {stimulus.fingerprint: stopped for stimulus in stimuli},
                    _replay_note(PER_PROCESS, VERIFIED_NOT_NEEDED, len(stimuli), stopped.why),
                )
            return _failed_batch(stimuli, RunFailure.verifier(
                "batched-replay", Refusal(
                    "verifier-batch-failed", "the per-process replay service failed",
                    error=type(exc).__name__)), PER_PROCESS)
        if len(got) != len(stimuli):
            stopped = RunFailure.verifier(
                "rtl-replay-coordination",
                Refusal(
                    "rtl-isolated-replay-cardinality",
                    "the isolated RTL runner did not return one result per stimulus",
                    offered=len(stimuli),
                    returned=len(got),
                ),
            )
            return (
                {stimulus.fingerprint: stopped for stimulus in stimuli},
                _replay_note(PER_PROCESS, VERIFIED_NOT_NEEDED, len(stimuli), stopped.why),
            )
        return (
            {s.fingerprint: g for s, g in zip(stimuli, got)
             if not (g is None and stop is not None and stop())},
            _replay_note(
                PER_PROCESS,
                VERIFIED_NOT_NEEDED,
                sum(g is not None for g in got),
                "one elaboration, a simulator process each, so every run "
                "is an isolated run and there is nothing to verify",
            ),
        )

    run_batch = getattr(artifact, "traces", None)
    if run_batch is None or len(stimuli) < 2:
        return {}, None
    bound = task.binds(artifact)
    if bound is not True:
        # `run` reports this per stimulus, by name, and it is not this function's to
        # pre-empt: falling through leaves the refusal where a reader expects it.
        return {}, None
    try:
        got = run_batch(task.contract, stimuli)
    except FailureRaised as exc:
        return _failed_batch(stimuli, exc.failure, ONE_PROCESS_BATCH)
    except CandidateFault as exc:
        return _failed_batch(stimuli, RunFailure.candidate(
            "candidate-observation", exc.refusal), ONE_PROCESS_BATCH)
    except Refused as exc:
        return _failed_batch(stimuli, RunFailure.verifier(
            "batched-replay-admission", exc.refusal), ONE_PROCESS_BATCH)
    except Exception as exc:  # noqa: BLE001
        return _failed_batch(stimuli, RunFailure.verifier(
            "batched-replay", Refusal(
                "verifier-batch-failed", "the shared-process replay service failed",
                error=type(exc).__name__)), ONE_PROCESS_BATCH)
    if len(got) != len(stimuli):
        return _failed_batch(stimuli, RunFailure.verifier(
            "batched-replay", Refusal(
                "replay-cardinality-mismatch",
                "the batch service did not return one result per stimulus",
                offered=len(stimuli), returned=len(got))), ONE_PROCESS_BATCH)
    withheld = [
        g for g in got if isinstance(g, Refusal) and g.kind in ("rtl-batch-order-dependent", "rtl-batch-unverifiable")
    ]
    if withheld:
        return {}, _replay_note(
            ONE_AT_A_TIME,
            VERIFIED_NOT_NEEDED,
            len(stimuli),
            f"the batch was withheld ({withheld[0].kind}), so every stimulus was "
            f"replayed on its own: {withheld[0].why[:120]}",
        )
    # The verdict reads this structured verification label, not the prose note.
    verified = VERIFIED_EVERY if getattr(artifact, "verify_every", False) else VERIFIED_SAMPLE
    return (
        {s.fingerprint: g for s, g in zip(stimuli, got)},
        _replay_note(
            ONE_PROCESS_BATCH,
            verified,
            len(stimuli),
            "one simulator run for all of them, checked against isolated "
            "replays for " + ("every stimulus" if verified == VERIFIED_EVERY else "a sample of three"),
        ),
    )
