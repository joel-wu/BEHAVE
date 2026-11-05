"""Measure public RTL or Behavior scoring and search in a bounded worker.

This is measurement orchestration, not a second evaluator. The caller supplies
a byte-frozen checkout and an existing Slurm allocation via the detached job
launcher. No production source, task semantics or comparator is modified.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from unittest.mock import patch


def write(path, record):
    from behave.sim.result_store import write_json_atomically

    write_json_atomically(path, record, record_arrays=True)


def task_configuration(folder):
    """Read task semantics; ordinary score measurement uses runtime policy."""
    from behave.sim.declarations import contract_of_task, declarations_at

    declarations = declarations_at(folder)
    contract = contract_of_task(folder, declarations=declarations)
    return contract, None


def behavior_source(folder, model=None):
    """Capture local modules unchanged; let Sim resolve their import closure."""
    from behave.data.spec_format import behavior_entry
    from behave.sim import api

    model = model or behavior_entry((folder / "spec.md").read_text())
    files = {path.relative_to(folder).as_posix(): path.read_bytes()
             for path in sorted(folder.rglob("*.py")) if "__pycache__" not in path.parts}
    return api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "behavior.py", model))


def candidate_behavior_source(folder, name, model):
    """Submit another Behavior file of the task as an agent would: as behavior.py.

    The golden behavior.py is withheld from the candidate bundle; shared helper
    modules of the task folder stay available, as they are to an agent.
    """
    from behave.sim import api

    files = {path.relative_to(folder).as_posix(): path.read_bytes()
             for path in sorted(folder.rglob("*.py")) if "__pycache__" not in path.parts}
    candidate = files.pop(name)
    files.pop("behavior.py", None)
    files["behavior.py"] = candidate
    return api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "behavior.py", model))


def shared_memory_environment(contract, generation, realization, policy, horizon):
    """The baseline's one external-memory run, expanded to the analysis horizon.

    Every baseline stimulus runs under the same memory initialization and ready
    policy. Analysis declares that run as its finite environment, so its
    verdicts hold for the memory image and ready schedule the score used. The
    record audit expands each concrete run to the analysis bound, so the
    declared environment is expanded to that same ``horizon``.
    """
    from behave.sim import api

    shape = api.build_baseline(contract, generation, realization=realization, policy=policy)
    environments = {}
    for stimulus in shape.stimuli:
        environment = stimulus.memory_environment
        if environment is None:
            raise ValueError("a baseline stimulus of an external-memory task carries no memory environment")
        environments.setdefault(environment.digest, environment)
    if len(environments) != 1:
        raise ValueError(
            f"the baseline uses {len(environments)} memory environments; declare one explicitly")
    return next(iter(environments.values())).materialize(horizon)


def prepare_candidate(args, contract, generation, reference, behavior, phase):
    """Use the same bundle/realization/preparation boundaries as public Eval."""
    from behave.sim import api

    folder = args.checkout / args.task
    if getattr(args, "artifact", "rtl") == "behavior":
        if (getattr(args, 'analysis_clock_schedule', None) is not None
                or getattr(args, 'analysis_memory_environment', None) is not None):
            raise ValueError('declared pin and memory analysis environments require RTL')
        path = args.output / "prepared.py"
        path.write_text(behavior.source)
        for name, data in behavior.dependencies.items():
            dependency = path.parent / name
            dependency.parent.mkdir(parents=True, exist_ok=True)
            dependency.write_bytes(data)
        realization = phase("realization", lambda: api.realization_of_behavior_source(
            behavior.source, contract, file=behavior.file, model=behavior.model))
        admitted = phase("admission", lambda: api.admit(contract, realization, reference=reference))
        return phase("prepare_behavior", lambda: api.prepare_behavior(
            admitted, api.BehaviorConfig(path, file=behavior.file, model=behavior.model), generation,
            bounded_analysis=args.analysis))

    from behave.pipelines.public_inputs import rtl_interface

    top = args.top or rtl_interface((folder / "spec.md").read_text())[0]
    files = {path.relative_to(folder).as_posix(): path.read_bytes()
             for path in sorted(folder.rglob("*"))
             if path.is_file() and path.suffix in {".sv", ".v", ".svh", ".vh"}}
    prepared = phase("rtl_bundle", lambda: api.prepare_rtl_bundle(
        api.bundle_for_entry(files, "rtl.sv")))
    path = args.output / "prepared.sv"
    path.write_text(prepared.source)
    write(args.output / "rtl_source.json", {"top": top, **prepared.as_dict()})
    realization = phase("realization", lambda: api.realization_of_rtl_source(
        prepared.source, folder.name, top=top, contract=contract))
    admitted = phase("admission", lambda: api.admit(contract, realization, reference=reference))
    schedule_path = getattr(args, 'analysis_clock_schedule', None)
    schedule = (None if schedule_path is None else
                api.GlobalSchedule.from_dict(json.loads(schedule_path.read_text())))
    memory_path = getattr(args, 'analysis_memory_environment', None)
    memory = (None if memory_path is None else
              api.MemoryEnvironment.from_dict(json.loads(memory_path.read_text())))
    if memory is None and getattr(args, "analysis", False) and contract.memories:
        memory = phase("analysis_memory_environment", lambda: shared_memory_environment(
            contract, generation, realization, baseline_policy(args), args.analysis_horizon))
        write(args.output / "analysis_memory_environment.json", memory.as_dict())
    return phase("prepare_rtl", lambda: api.prepare_rtl(
        admitted, api.RTLConfig(path, top, args.output / "artifact",
                               timeout_s=args.prepare_seconds), generation,
        analysis_clock_schedule=schedule, analysis_memory_environment=memory))


def search_observation(searches):
    """Describe actual search use, independently of the concrete score.

    Zero new inputs, local UNSAT and budget exhaustion are not capability bugs.
    Conversely, a passing baseline does not erase a refused search direction.
    This is a report of existing receipts, not a second acceptance authority.
    """
    directions = []
    for row in searches:
        budget = row.get("search_budget", {})
        reason = budget.get("stop_reason", row.get("termination"))
        if row.get("provider_refusal") or row.get("refusals") \
                or row.get("outcomes", {}).get("unsupported", 0) or reason in {
                "provider-unavailable", "search-failed"}:
            state = "needs-review"
        elif reason in {"baseline-failed", "baseline-incomplete", "scoring-budget-exhausted"} \
                and not budget.get("probes_completed", row.get("probes", 0)):
            state = "not-exercised"
        elif budget.get("probes_completed", row.get("probes", 0)):
            state = "queried"
        else:
            state = "no-query-completed"
        directions.append({"provider": row["provider"], "state": state,
                           "stop_reason": reason, "outcomes": row.get("outcomes", {}),
                           "refusals": row.get("refusals", {}),
                           "provider_refusal": row.get("provider_refusal"),
                           "targets": len(row.get("provider_targets", ())),
                           "probes_completed": budget.get("probes_completed", row.get("probes", 0)),
                           "candidates_retained": row.get("candidates_retained", 0)})
    return {"directions": directions, "exercised": bool(directions) and
            all(row["state"] == "queried" for row in directions)}


def search_receipt(search):
    """Retain the existing SAT-to-input link, not just aggregate hit counts."""
    record = search.as_dict()
    return {**record["telemetry"], "generated": record["generated"],
            "provenance": record["provenance"]}


def command_receipt(result):
    """Copy the returned decision without walking catalogues or replay traces."""
    from behave.sim import api
    from behave.sim.core.record_value import thaw_record_value

    failure = (result.failure.as_dict() if isinstance(result.failure, api.Refusal)
               else thaw_record_value(result.failure))
    evaluation = result.evaluation
    return {
        "status": result.status.value, "record_path": str(result.record_path),
        "failure": failure or None,
        "decision": None if result.decision is None else result.decision.as_dict(),
        "evaluation": None if evaluation is None else {
            "passed": evaluation.passed, "complete": evaluation.complete,
            "accepted": evaluation.accepted, "mismatch_count": evaluation.mismatch_count,
            "safety_hits": list(evaluation.safety_hits), "runs": len(evaluation.results),
        },
    }


def baseline_policy(args):
    """Use the public baseline defaults unless the operator overrides a field."""
    from behave.sim import api

    if args.default_baseline:
        if args.baseline_cases is not None or args.queue_depth is not None:
            raise ValueError("default baseline cannot be combined with explicit baseline limits")
        return api.BaselinePolicy()
    options = {}
    if args.baseline_cases is not None:
        options['case_limit'] = args.baseline_cases
    if args.queue_depth is not None:
        options['max_queue_depth'] = args.queue_depth
    return api.BaselinePolicy(**options)


def runtime_options(args):
    """Resolve omitted measurement ceilings from the selected Sim runtime."""
    from behave.core import runtime_defaults as defaults

    for name, value in (
        ('seconds', defaults.EVAL_SCORE_SECONDS),
        ('prepare_seconds', defaults.SIM_RTL_TOOL_SECONDS),
    ):
        if getattr(args, name) is None:
            setattr(args, name, value)
    if getattr(args, "analysis", False) and getattr(args, "analysis_seconds", None) is None:
        raise ValueError("--analysis requires an explicit --analysis-seconds budget")
    if getattr(args, "analysis", False):
        horizon = getattr(args, "analysis_horizon", None)
        if type(horizon) is not int or horizon < 1:
            raise ValueError("--analysis requires a positive --analysis-horizon in schedule slots")


def analysis_generation_spec(args, contract, baseline):
    """Reuse public input limits; any finite-domain drain override is explicit."""
    from behave.sim import api

    if not args.analysis:
        return None
    runtime_options(args)
    replay = api.ReplayPolicy.for_contract(contract)
    drain = getattr(args, 'analysis_drain_slots', None)
    if drain is not None and (type(drain) is not int or drain < 0):
        raise ValueError('--analysis-drain-slots must be a non-negative integer')
    return api.GenerationSpec(
        horizon=args.analysis_horizon, queue_bound=baseline.max_queue_depth,
        reset_hold_cycles=replay.reset_hold_cycles,
        drain=replay.drain_slots if drain is None else drain,
        stall_every=replay.stall_every)


def search_policy(args):
    from behave.sim import api

    if args.probe_budget is not None:
        if args.search_seconds is not None:
            raise ValueError("choose a search time or a probe-count experiment")
        return api.SearchPolicy(time_budget_s=None, max_probes=args.probe_budget)
    if args.search_seconds is not None:
        return api.SearchPolicy(time_budget_s=args.search_seconds)
    return api.SearchPolicy()


def goal_hit_summary(catalogue, phase_hits):
    """Partition observed events; keep the full manifest in authority evidence.

    A reporting view needs each public goal, not every elaborated occurrence.
    Re-expanding that occurrence map here can dwarf execution diagnostics even
    though the command has already persisted it in its authoritative record.
    This explicitly versioned view is not a replacement CatalogueManifest.
    """
    from collections import Counter

    base_hits = phase_hits["baseline"]
    new_hits = phase_hits["search"] - base_hits
    continuous_hits = phase_hits["continuous"] - base_hits - new_hits
    all_hits = base_hits | new_hits | continuous_hits
    if all_hits - set(catalogue):
        raise ValueError("measurement contains hits outside its source catalogue")
    families, classes = {}, {}
    for name, goal in catalogue.items():
        counts = {
            "declared": 1,
            "baseline_hit": int(name in base_hits),
            "search_only_hit": int(name in new_hits),
            "continuous_only_hit": int(name in continuous_hits),
            "unhit": int(name not in all_hits),
        }
        families.setdefault((goal.cls, goal.family), Counter()).update(counts)
        classes.setdefault(goal.cls, Counter()).update(counts)
    return {
        "catalogue": {
            "version": "goal-catalogue-view/1", "digest": catalogue.digest(),
            "goals": catalogue.goals_image(), "occurrence_count": len(catalogue.occurrences),
        },
        "classes": [{"class": cls, **counts} for cls, counts in sorted(classes.items())],
        "families": [{"class": cls, "family": family, **counts}
                     for (cls, family), counts in sorted(families.items())],
        "baseline_hits": sorted(base_hits), "search_only_hits": sorted(new_hits),
        "continuous_only_hits": sorted(continuous_hits),
        "unhit": sorted(set(catalogue) - all_hits),
    }


def reference_hit_summary(results, phases, excluded=()):
    """Read the Behavior evidence already used for comparison, never rerun it."""
    from behave.sim.core.reference_observation import ReferenceSourceObservation

    catalogue = None
    hits = {phase: set() for phase in ("baseline", "search", "continuous")}
    unavailable, records = [], []
    for index, (run, phase) in enumerate(zip(results, phases, strict=True)):
        if index in excluded:
            continue
        observed = run.comparison.reference_source if run.was_compared else None
        if not isinstance(observed, ReferenceSourceObservation):
            unavailable.append(index)
            continue
        if catalogue is not None and catalogue != observed.catalogue:
            raise ValueError("reference catalogue changed within the measured command")
        catalogue = observed.catalogue
        hits[phase].update(observed.hits)
        records.append({"run": index, "record": observed.fingerprint})
    return {
        "complete": not unavailable and bool(records),
        "unavailable_runs": unavailable,
        "records": records,
        "goals": None if catalogue is None else goal_hit_summary(catalogue, hits),
    }


def replay_summary(evaluation, baseline):
    """Attribute existing replay evidence, without rerunning or changing any score.

    Trace-judged monitor goals live in telemetry, not slot hit vectors. Include
    both, exclude environment-invalid runs, and check against the authoritative
    aggregate before publishing an isolated baseline/search/continuous split.
    Continuous workloads are a separate execution phase, not searched inputs.
    """
    telemetry = dict(evaluation.telemetry)
    excluded = {index for index, _ in telemetry.get("environment_assumption_violations", ())}
    hits = [set() for _ in evaluation.results]
    for index, run in enumerate(evaluation.results):
        if index in excluded:
            continue
        if run.ran:
            # The immutable trace already owns its packed-bit union. Expanding
            # every goal name again for every slot makes diagnostic export
            # proportional to slots × hits instead of the retained summary.
            hits[index].update(run.trace.goal_summary[1])
        elif run.run_failure.observed is not None:
            # This catalogue contains the observed prefix's hits, not every
            # declared goal. The run remains incomplete and cannot establish a pass.
            hits[index].update(run.run_failure.observed.catalogue)
    for key in ("monitor_hits", "monitor_violations"):
        for index, entry in telemetry.get(key, ()):
            if index not in excluded:
                hits[index].add(dict(entry)["goal"])
    if set().union(*hits) != set(evaluation.hits):
        raise ValueError("measurement hit attribution disagrees with the concrete result")
    members = {stimulus.identity_bytes for stimulus in baseline}
    continuous = set(dict(telemetry.get("replay_suite", ())).get("continuous", ()))
    phase_hits = {phase: set() for phase in ("baseline", "search", "continuous")}
    runs = []
    for index, (run, reached) in enumerate(zip(evaluation.results, hits, strict=True)):
        is_baseline = run.stimulus.identity_bytes in members
        phase = ("continuous" if run.stimulus.fingerprint in continuous else
                 "baseline" if is_baseline else "search")
        phase_hits[phase].update(reached)
        runs.append({
            "stimulus": run.stimulus.fingerprint, "baseline": is_baseline, "phase": phase,
            "stimulus_image": run.stimulus.as_dict(),
            "admissible": index not in excluded, "hits": sorted(reached),
            "queue_depths": {endpoint.name: run.stimulus.depth(endpoint.name)
                             for endpoint in evaluation.task.contract.inputs()},
            "accepted": None if not run.ran else run.trace.accepted_count(
                [endpoint.name for endpoint in evaluation.task.contract.inputs()]),
            "trace_completion": None if not run.ran else run.trace.completion.value,
            "compared": run.was_compared, "outputs_match": run.ok,
            "failure": None if run.refused is None else run.refused.as_dict(),
            "comparison": None if not run.was_compared else run.comparison.as_dict(),
        })
    return {
        **goal_hit_summary(evaluation.catalogue, phase_hits),
        "reference": reference_hit_summary(
            evaluation.results, [row["phase"] for row in runs], excluded),
        "runs": runs,
    }


@contextmanager
def command_stage_timings(rows, publish, *, analysis_input=None):
    """Observe production calls; never rerun a stage to obtain its timing.

    Missing rows mean a stage was not entered, not that it completed for free.
    Replay-assessment rows include baseline before generation and later calls
    that may reuse completed records. They can nest inside generation/score;
    durations are call spans, not additive phase totals.
    This instrumentation is confined to the single-process measurement worker.
    RTL operation spans distinguish admission, record auditing, decode and
    goal queries. Source-trace spans measure execution separately; neither a
    returned transport operation nor a completed span implies a proved goal.
    """
    from behave.sim import command
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
    from behave.sim.rtl.exact_job_context import RTLExactJobContext
    from behave.sim.rtl.analysis_transport import RTLAnalysisSession

    def timed(original, name):
        @wraps(original)
        def call(*args, **kwargs):
            row = {"name": name(*args, **kwargs) if callable(name) else name,
                   "status": "running"}
            rows.append(row)
            publish()
            start = time.monotonic()
            try:
                if name == "analysis_preparation" and analysis_input is not None:
                    # Preserve the existing strict worker projection before
                    # binding can exhaust its budget. This carries the decided
                    # score identity and raw runs, not reconstructed comparisons.
                    from behave.sim.behavior.exact_service import _analysis_subject_image

                    save_started = time.monotonic()
                    write(analysis_input, _analysis_subject_image(args[1]))
                    row["input_record"] = str(analysis_input)
                    row["input_persist_s"] = time.monotonic() - save_started
                result = original(*args, **kwargs)
                row["status"] = "returned"
                return result
            except BaseException:
                row["status"] = "raised"
                raise
            finally:
                row["elapsed_s"] = time.monotonic() - start
                if args and isinstance(args[0], RTLAnalysisSession) and args[0].timings:
                    diagnostic = args[0].timings[-1].get('stderr')
                    if diagnostic is not None:
                        row['worker_diagnostic'] = diagnostic
                publish()
        return call

    stages = (
        (command.evaluation_service.ReplayExecution, "evaluate", "replay_assessment"),
        (command.generation_service, "generate_stimuli", "generation"),
        (command.evaluation_service, "evaluate_generated", "concrete_score"),
        (command, "_analysis_inputs", "analysis_preparation"),
        (command, "analyse_after_decision", "bounded_analysis"),
        (RTLIsolatedAnalysisInstance, "_raw_call",
         lambda _self, operation, **_extra: f"rtl_analysis:{operation}"),
        (RTLExactJobContext, "source_trace", "rtl_source_trace"),
        (RTLAnalysisSession, "_exchange",
         lambda _self, operation, *_args: f"rtl_worker:{operation}"),
    )
    with ExitStack() as stack:
        for owner, attribute, name in stages:
            stack.enter_context(patch.object(owner, attribute, timed(getattr(owner, attribute), name)))
        yield


def _saved_observer_refusal(subject, request):
    """Reject foreign native receipts before paying for whole-domain admission.

    Re-preparing an unchanged RTL file need not reproduce an older observer:
    its identity also binds the tool runtime. Never relabel saved occurrences.
    This is only a compatibility check, not a record or reachability audit.
    """
    from behave.sim.analysis_budget import check_analysis
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.core.refusal import Refusal, Refused
    from behave.sim.rtl.native_events import NativeEventPlan

    checked = set()
    for index, result in enumerate(subject.results):
        check_analysis()
        trace = result.trace
        if trace is None or trace.source_evidence is None:
            continue  # The ordinary audit still decides missing evidence.
        try:
            image = thaw_record_value(trace.source_evidence)
            packed = image['plan']
            if isinstance(packed, str) and packed in checked:
                continue
            plan = (NativeEventPlan.from_dict(packed)
                    if image['version'] == 'rtl-native-observation/1'
                    else NativeEventPlan.from_evidence(packed))
        except (TypeError, ValueError, KeyError, Refused) as exc:
            return Refusal('rtl-analysis-saved-observer-invalid',
                'the saved native observer cannot be decoded', record=index, error=str(exc))
        expected = request.artifact.observation_binding.native_plan
        if plan.identity != expected:
            return Refusal('rtl-analysis-saved-observer-mismatch',
                'saved records and the current analysis request use different native observers; '
                'reuse the original frozen observer or collect matching-version records',
                record=index, expected=expected, actual=plan.identity)
        if isinstance(packed, str):
            checked.add(packed)
    return None


def analyse_saved(command, image, *, wall_seconds):
    """Restore only the strict Analysis view; never construct or publish a score."""
    from behave.sim import analysis_service, command as commands
    from behave.sim.analysis_budget import AnalysisStopped, analysis_scope
    from behave.sim.analysis_service import _coordinator_failure
    from behave.sim.core.analysis import _analysis_subject_from_wire
    from behave.sim.rtl.analysis import RTLAnalysisRequest

    if type(command._analysis_request) is not RTLAnalysisRequest:
        raise TypeError('saved-input measurement currently requires the RTL exact request')
    subject = _analysis_subject_from_wire(command.task, image, command.catalogue)
    policy = command.analysis_policy
    kwargs = dict(feasibility_timeout_ms=policy.feasibility_timeout_ms,
                  query_timeout_ms=policy.query_timeout_ms, batch=policy.batch)
    try:
        with analysis_scope(wall_seconds):
            refusal = _saved_observer_refusal(subject, command._analysis_request)
            exact, request = None, None
            if refusal is None:
                exact, refusal, request = commands._analysis_inputs(command, subject)
            if request is not None:
                raise TypeError('saved RTL analysis cannot use a second frontend request')
            outcome = analysis_service._analyse(subject, exact=exact,
                exact_refusal=refusal, record_audit=command._record_audit,
                goal_policy=command.goal_policy, **kwargs)
    except (Exception, AnalysisStopped) as exc:
        outcome = _coordinator_failure(subject, exc, **kwargs)
    if outcome.concrete != subject.fingerprint:
        raise ValueError('saved analysis returned a different concrete identity')
    return outcome


def worker(args):
    from behave.core.resources import allocated_cpus
    from behave.core.runtime_defaults import EVAL_SCORE_FINALIZE_SECONDS
    from behave.sim import api

    runtime_options(args)
    cpus = allocated_cpus()
    deadline = (args.score_deadline if args.score_deadline is not None
                else time.monotonic() + args.seconds)
    ceiling = deadline + (args.analysis_seconds if args.analysis else 0)
    policy = api.ScoringPolicy(time_budget_s=args.seconds)
    # The launcher owns the disjoint allocation, as the production CPU-job
    # coordinator does. Inside it, use the same public per-invocation admission
    # as SimulatorJudge rather than the standalone, unbrokered fallback.
    with (api.CpuPool(capacity=len(cpus)) as pool,
          api.cpu_scope(pool.endpoint, deadline=ceiling) as usage,
          api.scoring_scope(policy, deadline=deadline - EVAL_SCORE_FINALIZE_SECONDS) as budget):
        return _measure(args, cpus=cpus, cpu_usage=usage, scoring_budget=budget)


def _measure(args, *, cpus, cpu_usage, scoring_budget):
    from behave.sim import api

    began = time.monotonic()
    record = {
        "schema": "behave.search-score-timing/9",
        "task": args.task,
        "artifact": getattr(args, "artifact", "rtl"),
        "started_at": datetime.now(UTC).isoformat(),
        "phases": [],
        "command_stages": [],
        "allocated_cpus": list(cpus),
        "scoring_policy": scoring_budget.policy.as_dict(),
        "diagnostics": {"status": "not-started"},
    }

    def phase(name, call):
        record["phase"] = name
        write(args.output / "progress.json", record)
        start = time.monotonic()
        print(json.dumps({"phase": name, "event": "start", "elapsed_s": start - began}), flush=True)
        result = call()
        record["phases"].append({"name": name, "elapsed_s": time.monotonic() - start})
        write(args.output / "progress.json", record)
        return result

    try:
        from behave.core import runtime_defaults as defaults
        runtime_options(args)
        baseline = baseline_policy(args)
        search = search_policy(args)
        record["baseline_policy"] = baseline.as_dict()
        record["baseline_policy_identity"] = baseline.identity
        # The public Sim runtime owns defaults, not Eval or a historical profile.
        policy = {
            "schema": "behave.search-score-policy/2",
            "command_version": api.COMMAND_VERSION,
            "authority_version": api.RESULT_VERSION,
            "component_timeout_seconds": args.seconds,
            "timeout_seconds": args.prepare_seconds,
            "max_retries": 0,
            "seed": baseline.seed,
            "baseline_cases": baseline.case_limit,
            "queue_depth": baseline.max_queue_depth,
            "probe_budget": args.probe_budget,
            "search": search.as_dict(),
            "local_guidance": search.max_probes != 0 and search.time_budget_s != 0,
            "analysis": ({"analysis_wall_timeout_seconds": args.analysis_seconds,
                          "query_timeout_ms": (defaults.ANALYSIS_QUERY_MS
                                              if args.analysis_timeout_ms is None else args.analysis_timeout_ms),
                          "feasibility_timeout_ms": (defaults.ANALYSIS_FEASIBILITY_MS
                                                    if args.analysis_timeout_ms is None else args.analysis_timeout_ms)}
                         if getattr(args, "analysis", False) else None),
        }
        record["judge_policy"] = policy
        record["search_limits"] = {
            "rtl_prepare_seconds": policy["timeout_seconds"],
            "behavior_worker_seconds": defaults.SIM_BEHAVIOR_WORKER_SECONDS,
            "solver_timeout_ms": defaults.SIM_SEARCH_QUERY_MS,
            "shared_search_policy": search.as_dict(),
        }
        record["scope"] = (
            f"cold {record['artifact']} prepare + "
            + ("shared-budget search + " if policy["local_guidance"] else "baseline only (search disabled) + ")
            + "public score; "
            + ("bounded analysis requested separately" if policy["analysis"] is not None
               else "no bounded analysis"))
        folder = args.checkout / args.task
        contract, generation = phase("contract", lambda: task_configuration(folder))
        if args.analysis:
            generation = analysis_generation_spec(args, contract, baseline)
        record["generation_spec"] = None if generation is None else generation.as_dict()
        behavior = phase("behavior_bundle", lambda: behavior_source(folder, args.class_name))
        write(args.output / "behavior_source.json", behavior.as_dict())
        reference = phase(
            "reference",
            lambda: api.load_reference(
                api.ISOLATED_REFERENCE_ADAPTER,
                {"behavior.py": behavior.source.encode(), **behavior.dependencies},
                config={"class": behavior.model},
                loader="full-search timing",
            ),
        )
        candidate = behavior
        if getattr(args, "candidate_behavior", None):
            candidate = phase("candidate_bundle", lambda: candidate_behavior_source(
                folder, args.candidate_behavior, behavior.model))
            write(args.output / "candidate_source.json", candidate.as_dict())
        handle = prepare_candidate(args, contract, generation, reference, candidate, phase)
        providers = phase(
            "search_providers",
            lambda: handle.generation_providers(
                reference, workdir=args.output / "generation", enable_local_guidance=policy["local_guidance"]
            ),
        )
        command = phase(
            "command_request",
            lambda: handle.command_request(
                run_directory=args.output / "command",
                baseline=baseline,
                providers=providers,
                search_policy=search,
                scoring_policy=scoring_budget.policy,
                run_analysis=policy["analysis"] is not None,
                capture_analysis=getattr(args, "save_analysis_inputs", False),
                **(policy["analysis"] or {}),
                reference=reference,
            ),
        )
        record["baseline_execution"] = {
            "schedule": "runtime-request" if command.baseline.generation_spec is None else "fixed-domain",
            "candidates": command.baseline.policy.case_limit,
            "deduplicated": len(command.baseline.stimuli),
            "queue_depths": {endpoint.name: sorted({seed.depth(endpoint.name)
                             for seed in command.baseline.stimuli})
                             for endpoint in contract.inputs()},
            "generation_spec_applies": command.baseline.generation_spec is not None,
        }
        record["goal_schemas"] = [
            {"schema": selection.schema.name, "status": selection.status,
             "reason": None if selection.reason is None else selection.reason.as_dict(),
             "declared": sum(len(provider.catalogue) for provider in selection.providers),
             "providers": [provider.identity.key for provider in selection.providers]}
            for selection in command.goal_policy.selections
        ]
        saved = getattr(args, 'analysis_inputs', None)
        if saved is not None:
            image = json.loads(saved.read_text())
            record.update(scope='Analysis only over retained baseline/Search records; no new score',
                          analysis_input_record=str(saved.resolve()), concrete_fingerprint=image['concrete'])
            scoring_budget.finish()
            with command_stage_timings(record['command_stages'],
                                       lambda: write(args.output / 'progress.json', record)):
                outcome = phase('saved_analysis', lambda: analyse_saved(
                    command, image, wall_seconds=args.analysis_seconds))
            record.update(status='analysis-only', analysis=outcome.as_dict(),
                          diagnostics={'status': 'complete'})
            return 0
        with command_stage_timings(
                record["command_stages"], lambda: write(args.output / "progress.json", record),
                analysis_input=(args.output / "analysis_input.json"
                                if getattr(args, "save_analysis_inputs", False) else None)):
            if args.profile:
                import cProfile
                import pstats

                profile = cProfile.Profile()
                try:
                    result = phase("command", lambda: profile.runcall(api.handle_command, command))
                finally:
                    profile.dump_stats(str(args.output / "search-score.prof"))
                    with (args.output / "profile.txt").open("w") as stream:
                        pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(80)
            else:
                result = phase("command", lambda: api.handle_command(command))
        record.update(command_receipt(result))
        record.update(phase="diagnostics", diagnostics={"status": "running", "stage": "search-receipts"},
                      command_returned_at=datetime.now(UTC).isoformat(),
                      elapsed_s=time.monotonic() - began)
        # A returned public decision must survive cancellation or failure of the
        # optional, potentially large goal/witness diagnostic projection below.
        # This is a copy of that decision, never a score inferred from a prefix.
        write(args.output / "measurement.json", record)
        if (getattr(args, "save_analysis_inputs", False) and not args.analysis
                and result.evaluation is not None):
            from behave.sim.behavior.exact_service import _analysis_subject_image

            record["diagnostics"] = {"status": "running", "stage": "analysis-input-export"}
            phase("analysis_input_export", lambda: write(
                args.output / "analysis_input.json", _analysis_subject_image(result.evaluation)))
        if result.generated is not None:
            record["searches"] = [search_receipt(search) for search in result.generated.searches]
            record["search_observation"] = search_observation(record["searches"])
            record["search_summary"] = result.generated.search_summary
            record["baseline_members"] = len(result.generated.baseline)
            record["stimulus_members"] = len(result.generated.stimuli)
        # Preserve completed query/native-confirmation receipts before optional
        # tool lineage or full replay projection. An interrupted export must not
        # make a completed search look absent. This does not complete diagnostics
        # or infer any new score; the public decision above is unchanged.
        record["diagnostics"] = {"status": "running", "stage": "tool-lineage-and-replay"}
        write(args.output / "measurement.json", record)
        # Count actual tool executions, not reused references to one build.
        from collections import Counter

        lineage = handle._artifact.tool_lineage() if record["artifact"] == "rtl" else ()
        record["tool_executions"] = dict(Counter(
            "verilator-simulate" if row["role"].startswith("verilator-simulate:") else row["role"]
            for row in lineage))
        evaluation = result.evaluation
        record["analysis"] = None if result.analysis is None else result.analysis.as_dict()
        if evaluation is not None:
            record["evaluation"]["execution_cost"] = evaluation.execution_cost
            record["replay"] = replay_summary(evaluation, result.generated.baseline)
        record["diagnostics"] = {"status": "complete"}
    except api.ScoringStopped as error:
        # Preparation may exhaust the shared budget before a command exists.
        # This expected cancellation is a BaseException, not a candidate error
        # or an empty pass. Completed commands retain their own public decision.
        if record["diagnostics"]["status"] == "running":
            record["diagnostics"] = {**record["diagnostics"], "status": "incomplete",
                                     "error": f"{type(error).__name__}: {error}"}
        else:
            record.update(status="scoring-budget-exhausted", decision=None,
                          error=f"{type(error).__name__}: {error}")
    except Exception as error:  # noqa: BLE001 -- preserve a failed measurement, never a score
        if record["diagnostics"]["status"] == "running":
            record["diagnostics"] = {**record["diagnostics"], "status": "failed",
                                     "error": f"{type(error).__name__}: {error}"}
        else:
            record.update(status="measurement-error", error=f"{type(error).__name__}: {error}")
            failure = getattr(error, "failure", None)
            refusal = getattr(error, "refusal", None)
            if failure is not None:
                record["failure"] = failure.as_dict()
            if refusal is not None:
                record["refusal"] = refusal.as_dict()
        traceback.print_exc()
    finally:
        record["cpu_admission"] = cpu_usage.as_dict()
        record["scoring_budget"] = scoring_budget.receipt()
        record.update(elapsed_s=time.monotonic() - began, finished_at=datetime.now(UTC).isoformat())
        write(args.output / "measurement.json", record)
    print(json.dumps({key: record[key] for key in ("status", "elapsed_s")}), flush=True)
    return 0 if record["status"] == "evaluated" and record["diagnostics"]["status"] == "complete" else 1


def run(args):
    """Run in the caller's staged checkout; retain the complete measurement.

    Staging is the launcher's responsibility. Do not copy that checkout again
    (including earlier outputs), require the retired snapshot manifest, or
    remove its execution evidence when the worker ends.
    """
    began = time.monotonic()
    args.output.mkdir(parents=True, exist_ok=False)
    record = {
        "schema": "behave.search-score-worker/1",
        "wall_seconds": args.seconds,
        "rtl_prepare_seconds": args.prepare_seconds,
        "started_at": datetime.now(UTC).isoformat(),
        "task": args.task,
        "affinity": sorted(os.sched_getaffinity(0)),
    }
    sys.path.insert(0, str(args.checkout))
    sys.path.insert(0, str(args.checkout / 'src'))
    from behave.core import runtime_defaults as defaults
    runtime_options(args)
    score_deadline = began + args.seconds
    ceiling = args.seconds + (args.analysis_seconds if args.analysis else 0)
    record.update(wall_seconds=args.seconds, rtl_prepare_seconds=args.prepare_seconds,
                  worker_seconds=ceiling)
    local = args.checkout.resolve()
    env = dict(os.environ, PYTHONPATH=str(local / "src"), PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(local / "tools/measure_search_score.py"),
            "_worker",
            str(local),
            str(args.output.resolve()),
            args.task,
            *([args.class_name] if args.class_name else []),
            "--artifact", getattr(args, "artifact", "rtl"),
            *(["--candidate-behavior", args.candidate_behavior]
              if getattr(args, "candidate_behavior", None) else []),
            "--seconds",
            str(args.seconds),
            "--score-deadline",
            str(score_deadline),
            "--prepare-seconds",
            str(args.prepare_seconds),
            *(["--top", args.top] if args.top else []),
            *(["--default-baseline"] if args.default_baseline else []),
            *(["--queue-depth", str(args.queue_depth)] if args.queue_depth is not None else []),
            *(["--baseline-cases", str(args.baseline_cases)] if args.baseline_cases is not None else []),
            *(["--probe-budget", str(args.probe_budget)] if args.probe_budget is not None else []),
            *(["--search-seconds", str(args.search_seconds)] if args.search_seconds is not None else []),
            *(["--analysis"] if args.analysis else []),
            *(["--save-analysis-inputs"] if getattr(args, "save_analysis_inputs", False) else []),
            *(["--analysis-inputs", str(args.analysis_inputs.resolve())]
              if getattr(args, 'analysis_inputs', None) is not None else []),
            *(["--analysis-seconds", str(args.analysis_seconds)] if args.analysis else []),
            *(["--analysis-horizon", str(args.analysis_horizon)] if args.analysis else []),
            *(["--analysis-drain-slots", str(args.analysis_drain_slots)]
              if getattr(args, 'analysis_drain_slots', None) is not None else []),
            *(["--analysis-clock-schedule", str(args.analysis_clock_schedule.resolve())]
              if getattr(args, 'analysis_clock_schedule', None) is not None else []),
            *(["--analysis-memory-environment", str(args.analysis_memory_environment.resolve())]
              if getattr(args, 'analysis_memory_environment', None) is not None else []),
            *(["--analysis-timeout-ms", str(args.analysis_timeout_ms)]
              if args.analysis_timeout_ms is not None else []),
            *(["--profile"] if args.profile else []),
        ],
        env=env,
        cwd=local,
        start_new_session=True,
    )
    try:
        code = process.wait(timeout=max(0.0, ceiling - (time.monotonic() - began)))
        record.update(status="completed" if code == 0 else "failed", returncode=code)
    except subprocess.TimeoutExpired:
        record.update(status="timed-out", score=None)
        cleanup_deadline = time.monotonic() + defaults.JOB_CLEANUP_SECONDS
        cleanup_errors = []
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except OSError as error:
                cleanup_errors.append(f"{type(error).__name__}: {error}")
            try:
                process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                continue
            except OSError as error:
                cleanup_errors.append(f"{type(error).__name__}: {error}")
                continue
            break
        try:
            code = process.poll()
        except OSError as error:
            cleanup_errors.append(f"{type(error).__name__}: {error}")
            code = None
        record['cleanup_state'] = ('complete' if code is not None else 'unconfirmed')
        if cleanup_errors:
            record['cleanup_errors'] = cleanup_errors
    record.update(elapsed_s=time.monotonic() - began, finished_at=datetime.now(UTC).isoformat())
    write(args.output / "worker.json", record)
    return 0 if record["status"] == "completed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "_worker"))
    parser.add_argument("checkout", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("task")
    parser.add_argument("class_name", nargs="?", help="defaults to the spec's explicit Behavior entry")
    parser.add_argument("--artifact", choices=("rtl", "behavior"), default="rtl")
    parser.add_argument("--candidate-behavior",
                        help="with --artifact behavior: score this file of the task folder "
                             "(e.g. behavior2.py) against the golden behavior.py")
    parser.add_argument("--top", help="defaults to the spec's explicit RTL entry")
    parser.add_argument("--default-baseline", action="store_true",
                        help="require the public Sim defaults, without explicit baseline overrides")
    parser.add_argument("--queue-depth", type=int,
                        help="override the runtime baseline sequence length")
    parser.add_argument("--baseline-cases", type=int,
                        help="override the runtime baseline candidate count")
    search_options = parser.add_mutually_exclusive_group()
    search_options.add_argument("--search-seconds", type=float,
                                help="shared search time inside the total score deadline; zero disables search")
    search_options.add_argument("--probe-budget", type=int,
                                help="explicit total probe-count experiment instead of the public time budget; zero disables search")
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--score-deadline", type=float, help=argparse.SUPPRESS)
    parser.add_argument(
        "--prepare-seconds",
        type=int,
        help="RTL compilation, relation and layout per-stage limit; not the solver limit",
    )
    parser.add_argument("--analysis", action="store_true",
                        help="request optional post-score bounded analysis; timed separately")
    parser.add_argument("--analysis-seconds", type=int,
                        help="explicit total analysis wall budget, required with --analysis")
    parser.add_argument("--analysis-horizon", type=int,
                        help="explicit execution-slot horizon H; input limit N reuses --queue-depth (default 100)")
    parser.add_argument("--analysis-drain-slots", type=int,
                        help="explicit finite-domain drain override; does not change runtime baseline replay policy")
    parser.add_argument("--analysis-clock-schedule", type=Path,
                        help="declared GlobalSchedule JSON for exact RTL analysis; not inferred from scored runs")
    parser.add_argument("--analysis-memory-environment", type=Path,
                        help="declared MemoryEnvironment JSON for exact RTL analysis; not inferred from scored runs")
    parser.add_argument("--analysis-timeout-ms", type=int,
                        help="per-query/feasibility analysis limit, not a score timeout")
    parser.add_argument(
        "--profile",
        action="store_true",
        help="diagnostic cProfile of search + score; changes timing overhead, not semantics",
    )
    parser.add_argument("--save-analysis-inputs", action="store_true",
                        help="retain the evaluation and raw runs for later Analysis; also works without --analysis")
    parser.add_argument("--analysis-inputs", type=Path,
                        help="analyse retained RTL records only; no generation, comparison or new score")
    arguments = parser.parse_args()
    sys.path[:0] = [str(arguments.checkout / "src"), str(arguments.checkout)]
    from behave.core.process_limits import configure_worker_process_limit
    configure_worker_process_limit()
    if any(value is not None and value < 1 for value in (
           arguments.seconds, arguments.prepare_seconds,
           arguments.queue_depth, arguments.baseline_cases, arguments.analysis_timeout_ms,
           arguments.analysis_seconds, arguments.analysis_horizon)):
        parser.error("time limits must be positive")
    if arguments.analysis_drain_slots is not None and arguments.analysis_drain_slots < 0:
        parser.error("--analysis-drain-slots must be non-negative")
    if arguments.analysis and arguments.analysis_seconds is None:
        parser.error("--analysis requires --analysis-seconds")
    if arguments.analysis and arguments.analysis_horizon is None:
        parser.error("--analysis requires --analysis-horizon in schedule slots")
    if not arguments.analysis and (arguments.analysis_inputs is not None
                                  or arguments.analysis_seconds is not None
                                  or arguments.analysis_timeout_ms is not None
                                  or arguments.analysis_drain_slots is not None
                                  or arguments.analysis_clock_schedule is not None
                                  or arguments.analysis_memory_environment is not None
                                  or arguments.analysis_horizon is not None):
        parser.error("analysis budgets and horizon require --analysis")
    if arguments.candidate_behavior is not None and (
            arguments.artifact != 'behavior' or Path(arguments.candidate_behavior).name != arguments.candidate_behavior
            or arguments.candidate_behavior == 'behavior.py'):
        parser.error('--candidate-behavior names another top-level file and requires --artifact behavior')
    if arguments.artifact != 'rtl' and (arguments.analysis_clock_schedule is not None
                                      or arguments.analysis_memory_environment is not None):
        parser.error('declared pin and memory analysis environments require RTL')
    if arguments.analysis_inputs is not None and (arguments.artifact != 'rtl' or arguments.save_analysis_inputs):
        parser.error('--analysis-inputs requires RTL and cannot be combined with --save-analysis-inputs')
    if arguments.probe_budget is not None and arguments.probe_budget < 0:
        parser.error("probe budget must be nonnegative and explicit baseline limits positive")
    if arguments.default_baseline and (arguments.baseline_cases is not None or arguments.queue_depth is not None):
        parser.error("default baseline cannot be combined with explicit baseline limits")
    raise SystemExit(worker(arguments) if arguments.mode == "_worker" else run(arguments))
