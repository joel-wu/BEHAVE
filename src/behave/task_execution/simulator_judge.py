"""Score and optional ancillary analysis through ``behave.sim.api``.

RTL preprocessing and static Python linking precede the shared public handle.
Reference bundles use the same Python linker and isolated loader. No candidate
code runs in this coordinator.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from behave.core.artifacts import FileRef, LoadedSource
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore

from .analysis import AnalysisReport
from .comparison import COMPARISON_BACKEND, ComparisonInputs, ComparisonRequest, compare_sources
from .judge import JudgeRequest
from .judgment import ComponentJudgment


def _unavailable(code, *, origin="verifier", retryable=False, detail=None, evidence=()):
    return ComponentJudgment(None, code, origin, retryable, evidence, detail)


def _with_diagnostics(judgment, objects, project, *, append=False):
    """Ancillary export must not turn an established score into no-result."""
    try:
        files = tuple(FileRef(path, objects.put(canonical_json(image))) for path, image in project().items())
    except Exception as error:  # Only the diagnostic export, never evaluation/replay.
        logging.getLogger(__name__).exception("Score diagnostics could not be retained")
        return replace(judgment, detail={**judgment.detail,
            "diagnostics_status": "export_failed", "diagnostics_error": type(error).__name__})
    if append:
        files = tuple({file.path: file for file in (*judgment.diagnostics, *files)}.values())
    return replace(judgment, diagnostics=files)


def _with_failure_diagnostic(api, judgment, failure, objects, *, reason_visible=False):
    def project():
        image = api.failure_diagnostic(failure)
        # The trusted caller may expose errors in an agent-owned or otherwise
        # public reference. Hidden-reference failures retain Sim's redaction.
        if reason_visible:
            image["reason"] = failure.reason.as_dict()
        return {"failure.json": image}

    return _with_diagnostics(judgment, objects, project)


def _failure(api, failure, *, evidence=(), objects=None, reason_visible=False):
    # No-result alone does not imply retry. Namespace launch failures are a
    # specific infrastructure exception: the supervisor must confirm cleanup
    # before the existing bounded component retry may dispatch again.
    retryable = (failure.disposition is api.FailureDisposition.NO_RESULT
                 and failure.origin is api.FailureOrigin.INFRASTRUCTURE
                 and failure.kind == "isolation-launch-failed")
    judgment = ComponentJudgment(
        0 if failure.disposition is api.FailureDisposition.SCORE_ZERO else None,
        failure.kind, failure.origin.value, retryable=retryable,
        evidence=evidence, detail={"failure": failure.as_dict()},
    )
    return judgment if objects is None else _with_failure_diagnostic(
        api, judgment, failure, objects, reason_visible=reason_visible)


def _link_behavior(api, source):
    return api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {file.reference.path: file.data for file in source.files},
        source.artifact.entry_file, source.artifact.entry_symbol))


def _candidate_source_text(api, source: LoadedSource) -> str:
    """Attribute only entry-byte faults, shared by Eval and PPA preparation."""
    try:
        text = next(file.data for file in source.files
                    if file.reference.path == source.artifact.entry_file).decode("utf-8")
    except UnicodeError as error:
        raise api.FailureRaised(api.RunFailure.candidate("candidate-source",
            api.Refusal("candidate-source-not-utf8", "candidate entry is not UTF-8"))) from error
    if not text.strip():
        raise api.FailureRaised(api.RunFailure.candidate("candidate-source",
            api.Refusal("candidate-source-empty", "candidate entry is empty")))
    return text


def _preparation_evidence(objects, prepared, *, dependencies=()):
    # Keep the actual dependency bytes returned by Sim, not just their report
    # references. Some approved helpers were not part of the submitted bundle.
    return (objects.put(prepared.source.encode()), objects.put(canonical_json(prepared.as_dict())),
            *(objects.put(data) for data in dependencies))


def _baseline_progress(result):
    """Report execution progress from public records, never derive a score."""
    if result.generated is None:
        return {"status": "not_generated", "generated": None, "completed": 0}
    baseline = result.generated.portfolio.baseline
    runs = {} if result.evaluation is None else {
        run.stimulus.fingerprint: run for run in result.evaluation.results}
    completed = sum(
        (run := runs.get(stimulus.fingerprint)) is not None
        and run.comparison is not None and run.comparison.complete
        and run.trace.completion.scores
        for stimulus in baseline)
    required_complete = result.evaluation is not None and result.evaluation.required_complete
    return {"status": "completed" if required_complete else "incomplete",
            "generated": len(baseline), "completed": completed,
            "required_runs": (None if result.evaluation is None
                              else len(result.evaluation.required_stimuli)),
            "required_complete": required_complete,
            "search_generated": len(result.generated.stimuli) - len(baseline)}


def _scoring_scope(api, evaluation):
    """The same completion/progress projection for generated and explicit replay."""
    compared = tuple(evaluation.results[index] for index in evaluation.compared)
    return {
        "scope": evaluation.score_scope,
        "required_runs": len(evaluation.required_stimuli),
        "required_complete": evaluation.required_complete,
        "budget_exhausted": evaluation.budget_exhausted,
        "requested_runs": len(evaluation.requested_stimuli),
        "completed_runs": sum(run.trace.completion.scores for run in compared),
        "checked_transaction_prefixes": sum(
            run.trace.completion is api.Completion.CHECKPOINT for run in compared),
        "unexecuted_runs": len(evaluation.unexecuted),
        "accepted": evaluation.accepted,
        "checked_inputs": evaluation.checked_inputs,
    }


class SimulatorJudge:
    identity = COMPARISON_BACKEND

    def __init__(self, *, cpu_pool: dict | None = None, cpu_capacity: int | None = None):
        if cpu_pool is not None and cpu_capacity is not None:
            raise ValueError("choose shared CPU admission or a reserved per-call capacity")
        self.cpu_pool = cpu_pool
        self.cpu_capacity = cpu_capacity

    def evaluate(self, request: JudgeRequest, objects: DirectoryStore) -> ComponentJudgment:
        from behave.agent.cpu_resources import current_cpu_lease

        task = request.task
        inputs = ComparisonInputs(request.component, request.source, task.reference_behavior,
                                  task.contract, task.run_policy, task.judge_policy)
        if request.policy != inputs.policy:
            raise ValueError("worker policy differs from the frozen task")
        lease = current_cpu_lease()
        capacity = self.cpu_capacity
        if lease is not None:
            capacity = len(lease.cpus) if capacity is None else min(capacity, len(lease.cpus))
        if capacity is not None:
            from behave.sim import api

            # The batch reserves this many CPUs per concurrent judge. A killed
            # worker may poison its own broker, not every unrelated candidate.
            # compare_sources returns only after supervised cleanup. Unknown
            # cleanup still fences the enclosing ResourcePool in judge.py.
            with api.CpuPool(capacity=capacity) as pool:
                return compare_sources(inputs, objects=objects, workdir=request.workdir,
                    dispatch=request.dispatch, cancelled=request.cancelled, on_decided=request.on_decided,
                    cpu_pool=pool.endpoint.as_dict())
        return compare_sources(inputs, objects=objects, workdir=request.workdir,
                               dispatch=request.dispatch, cancelled=request.cancelled, on_decided=request.on_decided,
                               cpu_pool=self.cpu_pool)


def _replay_metadata(objects, stimuli, evaluation):
    """Named replay inputs and Sim-computed workload windows for trusted callers."""
    pool = objects.put(canonical_json({"stimuli": [stimulus.as_dict() for stimulus in stimuli]}))
    frozen = {} if evaluation is None else dict(evaluation.telemetry).get("workload_windows", ())
    windows = {key: dict(window) for key, window in dict(frozen).items()}
    detail = {"replay_pool": pool.as_dict(), "workload_windows": windows}
    suite = None if evaluation is None else dict(evaluation.telemetry).get("replay_suite")
    if suite is not None:
        # These are public execution counts, not another verdict or replay.
        suite = dict(suite)
        requests = {s.fingerprint: s for s in evaluation.stimuli}
        completed = {run.stimulus.fingerprint for run in evaluation.results
                     if run.comparison is not None and run.comparison.complete
                     and run.trace is not None and run.trace.completion.scores}
        detail["replay_phases"] = [
            {"phase": phase, "requested_runs": len(suite[phase]),
             "completed_runs": sum(key in completed for key in suite[phase]),
             "input_transactions": sum(len(queue) for key in suite[phase]
                                       for _, queue in requests[key].queues)}
            for phase in ("isolated", "continuous") if suite[phase]]
    return detail, (pool,)


def _explicit_replay(api, handle, reference, contract, inputs, policy, objects, evidence, scoring_budget,
                     *, on_decided=None):
    stimuli = tuple(api.Stimulus.from_dict(row) for row in parse_json(inputs.replay_stimuli)["stimuli"])
    runtime = tuple(stimulus for stimulus in stimuli if stimulus.replay_policy is not None)
    suite = stimuli
    if runtime:
        suite = api.build_replay_suite(contract, runtime)
        # Fixed schedules remain unchanged. Only runtime requests can be joined
        # into the continuous phase by Sim's public suite builder.
        suite = api.ReplaySuite(suite.contract, stimuli, suite.continuous, suite.members)
    authority = handle.replay(suite, reference=reference, stop_on_failure=True)
    scoring_budget.finish()
    evaluation = authority.evaluation
    detail, refs = _replay_metadata(objects, stimuli, evaluation)
    evidence = (*evidence, *refs, objects.put(canonical_json(authority.as_dict())))
    detail.update(evaluation=evaluation.fingerprint, authority_version=api.RESULT_VERSION,
                  scoring_scope=_scoring_scope(api, evaluation))
    if authority.authoritative:
        judgment = ComponentJudgment(int(authority.passed), "simulator-concrete-score",
                                     evidence=evidence, detail=detail)
    else:
        failure = next((failure for _, failure in evaluation.no_result_failures), None)
        judgment = (_unavailable("judge-concrete-not-authoritative", evidence=evidence, detail=detail)
                    if failure is None else _failure(api, failure, evidence=evidence, objects=objects))
        judgment = replace(judgment, detail={**judgment.detail, **detail})
    judgment = _with_diagnostics(judgment, objects, lambda: api.evaluation_diagnostics(
        evaluation, options=policy.diagnostics or api.DiagnosticOptions(),
        reference_visible=inputs.reference_visible))
    if on_decided is not None:
        on_decided(judgment)
    return judgment


class _SimulatorService:
    identity = SimulatorJudge.identity

    def evaluate(self, request: ComparisonRequest, objects: DirectoryStore, *,
                 on_decided: Callable[[ComponentJudgment], None] | None = None) -> ComponentJudgment:
        import time

        from behave.core import runtime_defaults as defaults
        from behave.sim import api

        deadline = (request.score_deadline if request.score_deadline is not None
                    else time.monotonic() + request.policy.score_timeout_seconds)
        policy = api.ScoringPolicy(time_budget_s=request.policy.score_timeout_seconds)
        with api.scoring_scope(policy, deadline=deadline - defaults.EVAL_SCORE_FINALIZE_SECONDS) as budget:
            try:
                declaration = None
                if request.inputs.spec is not None:
                    from .task_declaration import check_task_declaration

                    declaration = check_task_declaration(request.inputs.spec.data, request.inputs.contract.data,
                        objects=objects, cpu_pool=request.cpu_pool,
                        deadline=deadline - defaults.EVAL_SCORE_FINALIZE_SECONDS,
                        cancelled=request.cancelled)
                    if declaration.score != 1 or not declaration.cleanup.quiescent:
                        return declaration

                def with_declaration(result):
                    return (result if declaration is None else
                            replace(result, evidence=(*declaration.closure, *result.evidence)))

                # Publish the same evidence as the final return, before its
                # evaluation frame can enter expensive teardown. Declaration
                # evidence must not appear only after the checkpoint was sent.
                publish = (None if on_decided is None else
                           lambda result: on_decided(with_declaration(result)))
                result = self._evaluate(request, objects, on_decided=publish, scoring_budget=budget)
                return with_declaration(result)
            except api.ScoringStopped:
                # Preparation produced no complete candidate/reference test. Sim
                # establishes no score; the workflow's existing final_score maps
                # absent components to zero without rewriting this attribution.
                if not budget.cleanup_confirmed:
                    return _unavailable("judge-budget-cleanup-unconfirmed", origin="infrastructure",
                                        detail={"scoring_budget": budget.receipt()})
                return _unavailable("judge-budget-no-effective-tests", origin="resource-policy",
                                    detail={"scoring_budget": budget.receipt()})

    def _evaluate(self, request, objects, *, on_decided, scoring_budget):
        # Import EDA/solver services only in the judge worker, not in the solver role.
        from behave.sim import api

        policy = request.policy
        inputs = request.inputs
        if policy.command_version != api.COMMAND_VERSION or policy.authority_version != api.RESULT_VERSION:
            return _unavailable("judge-simulator-version-mismatch", detail={
                "command": api.COMMAND_VERSION, "authority": api.RESULT_VERSION})
        try:
            contract = api.TaskContract.from_dict(parse_json(inputs.contract.data))
            # JSON null selects Sim's runtime-scheduled baseline. Explicit old
            # GenerationSpec records retain their original finite schedule.
            generation = (None if inputs.run_policy.strip() == b"null"
                          else api.GenerationSpec.from_dict(parse_json(inputs.run_policy)))
        except (ValueError, TypeError, api.Refused) as error:
            logging.getLogger(__name__).exception("Sim task policy could not be loaded")
            return _unavailable("judge-task-policy-invalid", detail={"error_type": type(error).__name__})

        source = inputs.source
        try:
            # Captured artifact byte format is the caller's responsibility;
            # Python/RTL syntax, entry selection and task semantics are Sim's.
            text = _candidate_source_text(api, source)
        except api.FailureRaised as error:
            return _failure(api, error.failure, objects=objects)
        reference_source = inputs.reference_behavior
        preparation_evidence = ()
        reference_files = {file.reference.path: file.data for file in reference_source.files}
        reference_model = reference_source.artifact.entry_symbol
        try:
            # The native reference layout retains its captured fp_lib.py bytes.
            # Normalize other layouts through Sim, without substituting that helper.
            if reference_source.artifact.entry_file != "behavior.py" or not set(reference_files) <= {"behavior.py", "fp_lib.py"}:
                prepared = _link_behavior(api, reference_source)
                reference_files = {"behavior.py": prepared.source.encode(), **prepared.dependencies}
                reference_model = prepared.model
                preparation_evidence += _preparation_evidence(objects, prepared,
                                                             dependencies=prepared.dependencies.values())
            reference = api.load_reference(
                api.ISOLATED_REFERENCE_ADAPTER,
                reference_files,
                config={"class": reference_model},
                loader=self.identity,
            )
        except api.FailureRaised as error:
            failure = error.failure
            if failure.disposition is api.FailureDisposition.NO_RESULT:
                return _failure(api, failure, evidence=preparation_evidence, objects=objects,
                                reason_visible=inputs.reference_visible)
            # The generic bundle preprocessor was given the reference here,
            # not the submission. A fault in it cannot score the candidate zero.
            judgment = _unavailable("judge-reference-unavailable", origin="reference",
                                   detail={"failure": failure.as_dict()}, evidence=preparation_evidence)
            return _with_failure_diagnostic(api, judgment,
                api.RunFailure.reference(failure.phase, failure.reason), objects,
                reason_visible=inputs.reference_visible)
        except api.Refused as error:
            judgment = _unavailable("judge-reference-unavailable", origin="reference",
                                   detail={"reason": error.refusal.as_dict()}, evidence=preparation_evidence)
            return _with_failure_diagnostic(api, judgment,
                api.RunFailure.reference("reference-preparation", error.refusal), objects,
                reason_visible=inputs.reference_visible)

        # Only execution-ready source needs a file in the private /tmp directory.
        # Bundle preparation consumes captured bytes, not a second on-disk copy.
        try:
            if inputs.component == "behavior":
                # The bundle API owns explicit entry selection, including the
                # single-file case. Never fall back to an implicitly chosen class
                # when Sim refuses this bundle's module semantics.
                prepared = _link_behavior(api, source)
                text, file = prepared.source, prepared.file
                path = request.workdir / "prepared.py"
                path.write_text(text)
                preparation_evidence += _preparation_evidence(objects, prepared,
                                                             dependencies=prepared.dependencies.values())
                realization = api.realization_of_behavior_source(text, contract, file=file, model=prepared.model)
                task = api.admit(contract, realization, reference=reference)
                handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=file, model=prepared.model), generation,
                    bounded_analysis=policy.analysis is not None)
            else:
                # A single file may also contain macros or conditional source.
                # All downstream consumers must see the same retained compiler
                # output, not a raw-source shortcut selected by file count.
                bundle = api.bundle_for_entry(
                    {file.reference.path: file.data for file in source.files}, source.artifact.entry_file)
                prepared = api.prepare_rtl_bundle(bundle)
                text = prepared.source
                path = request.workdir / "prepared.sv"
                path.write_text(text)
                preparation_evidence += _preparation_evidence(objects, prepared)
                realization = api.realization_of_rtl_source(text, top=source.artifact.entry_symbol, contract=contract)
                task = api.admit(contract, realization, reference=reference)
                handle = api.prepare_rtl(task, api.RTLConfig(
                    path, source.artifact.entry_symbol, request.workdir / "build"), generation)
            if inputs.replay_stimuli is not None:
                return _explicit_replay(api, handle, reference, contract, inputs, policy, objects,
                                        preparation_evidence, scoring_budget, on_decided=on_decided)
            providers = ()
            if policy.search_seconds > 0:
                providers = handle.generation_providers(
                    reference, workdir=request.workdir / "generation", enable_local_guidance=True)
            command = handle.command_request(
                run_directory=request.workdir / "result", reference=reference,
                baseline=api.BaselinePolicy(seed=policy.seed, case_limit=policy.baseline_cases,
                                            max_queue_depth=policy.sequence_length),
                providers=providers, search_policy=api.SearchPolicy(time_budget_s=policy.search_seconds),
                scoring_policy=scoring_budget.policy,
                run_analysis=False,
            )
            result = api.handle_command(command)
        except api.FailureRaised as error:
            return _failure(api, error.failure, evidence=preparation_evidence, objects=objects)
        except api.Refused as error:
            # A capability refusal is not positive evidence of candidate failure.
            judgment = _unavailable("judge-simulator-refused", detail={"reason": error.refusal.as_dict()},
                                   evidence=preparation_evidence)
            return _with_failure_diagnostic(api, judgment,
                api.RunFailure.verifier("comparison", error.refusal), objects,
                reason_visible=inputs.reference_visible)

        # Sim binds its committed decision to this result. Persist public evidence,
        # but do not reopen result JSON or reconstruct its score/identity checks.
        evaluation = result.evaluation
        evidence = (objects.put(canonical_json({
            "request": command.as_dict(), "result": result.as_dict(),
            "evaluation": None if evaluation is None else evaluation.as_evidence_dict(),
        })), *preparation_evidence)
        decision = result.decision
        progress = {"baseline": _baseline_progress(result)}
        if evaluation is not None:
            progress["evaluation"] = evaluation.fingerprint
            progress["scoring_scope"] = _scoring_scope(api, evaluation)
        if result.generated is not None:
            replay, refs = _replay_metadata(objects, result.generated.stimuli, evaluation)
            progress.update(replay)
            evidence += refs
        def diagnostic(judgment):
            judgment = _with_diagnostics(judgment, objects, lambda: api.command_diagnostics(
                result, options=policy.diagnostics or api.DiagnosticOptions(),
                reference_visible=inputs.reference_visible), append=evaluation is None)
            # Both a score and a typed no-result are final concrete outcomes.
            # Retain either before releasing the large evaluation graph; only
            # an actual score may start analysis or reach a score-only callback.
            if evaluation is not None and on_decided is not None:
                on_decided(judgment)
            return judgment
        if decision is None:
            return diagnostic(_unavailable("judge-command-no-result", evidence=evidence,
                                detail=progress,
                                retryable=result.status is api.CommandStatus.INFRASTRUCTURE_FAILURE))
        if not decision.authoritative:
            for _index, failure in decision.failures:
                if failure.disposition is api.FailureDisposition.NO_RESULT:
                    judgment = _failure(api, failure, evidence=evidence, objects=objects)
                    return diagnostic(replace(judgment, detail={**judgment.detail, **progress}))
            if evaluation is not None and evaluation.budget_exhausted and evaluation.accepted == 0:
                return diagnostic(_unavailable("judge-budget-no-effective-tests", origin="resource-policy",
                                               evidence=evidence, detail=progress))
            return diagnostic(_unavailable("judge-concrete-not-authoritative", evidence=evidence,
                                detail=progress,
                                retryable=result.status is api.CommandStatus.INFRASTRUCTURE_FAILURE))
        judgment = ComponentJudgment(int(decision.passed), "simulator-concrete-score", evidence=evidence,
                                    detail={"evaluation": decision.evaluation_fingerprint,
                                            "authority_version": decision.authority["version"], **progress})
        judgment = diagnostic(judgment)
        if policy.analysis is None:
            return judgment
        # The worker installs its score checkpoint before any lowering/solver work.
        # The original typed result retains the exact pre-generation capability;
        # analysis neither reruns generation/replay nor reconstructs it from JSON.
        options = policy.analysis
        policy_ref = objects.put(canonical_json(options.as_dict()))
        try:
            outcome = api.analyse_command(
                result, feasibility_timeout_ms=options.feasibility_timeout_ms,
                query_timeout_ms=options.query_timeout_ms, batch=options.batch,
                wall_timeout_seconds=options.wall_timeout_seconds)
            if outcome.concrete != evaluation.fingerprint:
                raise ValueError("analysis belongs to another evaluation")
            report = AnalysisReport(evaluation.fingerprint, policy_ref, "analysis-returned",
                                    objects.put(canonical_json(outcome.as_dict())))
        except Exception:  # Ancillary analysis never erases the score.
            logging.getLogger(__name__).exception("Post-score analysis failed; retaining the concrete score")
            report = AnalysisReport(evaluation.fingerprint, policy_ref, "analysis-service-failed")
        return replace(judgment, analysis=report)
