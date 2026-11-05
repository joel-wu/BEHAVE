"""Public observations from an existing evaluation, never another execution.

Hidden reference source, reference coverage, internal states and private worker
records are not part of this projection. Errors from a visible reference may
quote that reference only when its caller permits it. Display limits belong to runtime;
absent limits retain all failing runs and their interface observations.
"""

from collections.abc import Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING

from behave.core.diagnostics import DiagnosticOptions

if TYPE_CHECKING:
    from .command import CommandResult
    from .core.failure import RunFailure
    from .core.outcome import ConcreteEvaluation


def failure_diagnostic(failure: "RunFailure", *, reference_visible: bool = False) -> dict:
    """Keep attribution even when the failing service handled private sources."""
    row = {"code": failure.kind, "origin": failure.origin.value,
           "phase": failure.phase, "disposition": failure.disposition.value,
           "policy": failure.policy}
    if failure.origin.value == "candidate" or (reference_visible and failure.origin.value == "reference"):
        # Candidate compile/runtime messages may quote the candidate's own code.
        # Reference/verifier tracebacks can quote hidden implementations instead.
        row["reason"] = failure.reason.as_dict()
    elif (failure.origin.value == "verifier" and failure.phase == "candidate-replay"
          and failure.kind in {"multiclock-state-aliased", "multiclock-state-cyclic"}):
        # A state-representation limit is not a candidate counterexample. Expose
        # only the candidate's two conflicting paths, not worker records or a
        # hidden reference traceback. Keep origin and disposition unchanged.
        row["reason"] = {
            "kind": failure.kind,
            "why": "Atomic multiclock replay requires state without shared mutable objects or cycles.",
            "detail": {key: value for key in ("path", "first_path", "value_type")
                       if type(value := failure.reason.detail.get(key)) is str},
        }
    return row


def evaluation_diagnostics(evaluation: "ConcreteEvaluation", *,
                           options: DiagnosticOptions = DiagnosticOptions(),
                           reference_visible: bool = False) -> dict[str, dict]:
    """Return named JSON documents, without serializing the private evaluation.

    Stimuli remain whole, including reset/clock schedules and initial memory.
    Event display limits retain the tail with original slot indices. Missing
    prefixes and omitted cases/faults are explicit; no truncated view is a new
    comparison or a replacement replay record. The trusted caller may disclose
    errors from an agent-authored or otherwise visible reference. Hidden
    reference errors and verifier internals remain redacted by default.
    """
    from .core.record_value import thaw_record_value

    images, runs = {}, []
    safety = set(evaluation.safety_hits)
    affected = 0
    for index, run in enumerate(evaluation.results):
        comparison = run.comparison
        hits = [] if run.trace is None or not safety else [
            {"slot": slot, "goals": sorted(name for name, hit in record.hits if hit and name in safety)}
            for slot, record in enumerate(run.trace.records)
            if any(hit and name in safety for name, hit in record.hits)]
        if run.refused is None and (comparison is None or comparison.ok) and not hits:
            continue
        affected += 1
        if options.max_runs is not None and len(runs) >= options.max_runs:
            continue
        prefix = f"runs/{index}"
        stimulus_path, observations_path = f"{prefix}/stimulus.json", f"{prefix}/observations.json"
        images[stimulus_path] = run.stimulus.as_dict()
        records = () if run.trace is None else run.trace.records
        start = 0 if options.max_events_per_run is None else max(0, len(records) - options.max_events_per_run)
        events = []
        for slot in range(start, len(records)):
            record = records[slot]
            events.append({"slot": slot,
                "accepted": thaw_record_value(record.accepted),
                "signals": thaw_record_value(record.outputs),
                "monitor": thaw_record_value(record.monitor),
                "external_memory": thaw_record_value(dict(record.environment).get("external-memory"))})
        faults = () if comparison is None else comparison.faults
        memory_faults = () if comparison is None else comparison.memory_faults
        shown = options.max_faults_per_run
        images[observations_path] = {
            "stimulus": run.stimulus.fingerprint,
            "trace": None if run.trace is None else run.trace.fingerprint,
            "completion": None if run.trace is None else run.trace.completion.value,
            "events": events, "event_count": len(records), "events_omitted_before": start,
            "comparison": None if comparison is None else {
                "complete": comparison.complete,
                "faults": [{"kind": kind, "position": position,
                            "expected": thaw_record_value(expected), "observed": thaw_record_value(observed)}
                           for kind, position, expected, observed in faults[:shown]],
                "fault_count": len(faults), "faults_omitted": len(faults) - len(faults[:shown]),
                "memory_comparison": "final-state",
                "memory_faults": [{"memory": name, "address": address, "expected": expected, "observed": observed}
                                  for name, address, expected, observed in memory_faults[:shown]],
                "memory_fault_count": len(memory_faults),
                "memory_faults_omitted": len(memory_faults) - len(memory_faults[:shown])},
            "safety_hits": hits,
            "failure": None if run.refused is None else failure_diagnostic(
                run.refused, reference_visible=reference_visible)}
        runs.append({"index": index, "stimulus": stimulus_path, "observations": observations_path})
    images["summary.json"] = {
        "schema": "behave.score-diagnostics/1", "evaluation": evaluation.fingerprint,
        "contract": evaluation.task.contract.digest,
        "endpoints": [{"name": endpoint.name, "direction": endpoint.direction.value,
                       "fields": [field.name for field in endpoint.fields],
                       "matching": endpoint.matching.value, "identifier": endpoint.identifier}
                      for endpoint in evaluation.task.contract.endpoints],
        "options": asdict(options), "run_count": len(evaluation.results),
        "accepted": evaluation.accepted, "complete": evaluation.complete,
        "affected_runs": affected, "runs_omitted": affected - len(runs), "runs": runs,
        "mismatch_count": evaluation.mismatch_count,
        "memory_mismatch_count": evaluation.memory_mismatch_count,
        "safety_hits": [{"id": name, "family": evaluation.catalogue[name].family,
                         "event": evaluation.catalogue[name].event} for name in sorted(safety)]}
    execution = dict(dict(evaluation.telemetry).get("concrete_execution", ()))
    if execution.get("interrupted_stage") is not None:
        images["summary.json"]["interrupted_stage"] = execution["interrupted_stage"]
    if execution.get("interrupted_tool") is not None:
        tool = dict(execution["interrupted_tool"])
        # Host log paths are private tool evidence, not an agent-visible path.
        images["summary.json"]["interrupted_tool"] = {
            name: tool[name] for name in ("role", "wall_seconds", "status") if name in tool}
    return images


def _search_detail(detail):
    result = {key: detail[key] for key in (
        "file", "line", "column", "end_line", "end_column", "instance", "port",
        "cell", "kind", "count", "error", "detail", "worker_detail", "failure")
        if key in detail and type(detail[key]) in (str, int)}
    first = detail.get("first")
    if isinstance(first, Mapping):
        result.update({key: first[key] for key in ("cell", "kind")
                       if key in first and type(first[key]) is str})
    return result


def _search_reason(code, count, why, detail, *, reference_visible):
    row = {"code": code, "count": count}
    if reference_visible:
        row["why"] = why
        # Source locations and concise compiler messages, not worker records,
        # query terms, source hashes, coverage targets or arbitrary tracebacks.
        row["detail"] = _search_detail(detail)
        cause = detail.get("cause")
        if isinstance(cause, Mapping):
            for key in ("kind", "why"):
                if type(cause.get(key)) is str:
                    row["detail"]["cause_" + key] = cause[key]
            if isinstance(cause.get("detail"), Mapping):
                row["detail"].update(_search_detail(cause["detail"]))
    return row


def command_diagnostics(result: "CommandResult", *,
                        options: DiagnosticOptions = DiagnosticOptions(),
                        reference_visible: bool = False) -> dict[str, dict]:
    """Project concrete observations and search capability independently.

    Search refusals are not mismatches, and completing a search is not a proof.
    This reads the typed command result without replaying or changing its score.
    Source-dependent refusal messages require a visible reference; hidden
    reference internals are never copied into these public documents.
    """
    images = ({} if result.evaluation is None else evaluation_diagnostics(
        result.evaluation, options=options, reference_visible=reference_visible))
    command = result.command
    policy = command.search_policy
    enabled = (command.probe_budget != 0 if policy is None else
               policy.max_probes != 0
               and (policy.time_budget_s is None or policy.time_budget_s > 0))
    searches = () if result.generated is None else result.generated.portfolio.searches
    directions = []
    for index, search in enumerate(searches):
        telemetry = search.telemetry
        budget = telemetry.get("search_budget", {})
        started = budget.get("probes_started", telemetry.get("probes"))
        termination = telemetry.get("termination", "not_recorded")
        counts = telemetry.get("refusals", {})
        whys, details = telemetry.get("refusal_why", {}), telemetry.get("refusal_detail", {})
        refusals = [_search_reason(code, count, whys.get(code, ""), details.get(code, {}),
                                  reference_visible=reference_visible)
                    for code, count in counts.items() if count]
        failure = telemetry.get("failure")
        if failure or termination == "search-failed":
            status = "failed"
        elif termination == "provider-unavailable" or (refusals and started == 0):
            status = "unavailable"
        elif refusals:
            status = "limited"
        elif termination in ("time-budget-exhausted", "scoring-budget-exhausted", "probe-budget-exhausted"):
            status = "budget_exhausted"
        elif termination in ("queue-empty", "goals-covered"):
            status = "completed"
        else:
            status = "stopped"
        kind = search.provider.split("/", 1)[0]
        row = {"direction": index + 1, "provider": kind if kind in ("behavior", "rtl") else "other",
               "status": status, "termination": termination,
               "probes_started": started, "probes_completed": budget.get("probes_completed"),
               "probes_cancelled": budget.get("probes_cancelled"),
               "generated_stimuli": len(search.generated), "refusals": refusals,
               "elapsed_seconds": budget.get("elapsed_s")}
        if failure:
            row["failure"] = _search_reason(failure["kind"], 1, failure.get("why", ""),
                failure.get("detail", {}), reference_visible=reference_visible)
        directions.append(row)
    if not enabled:
        status = "disabled"
    elif not directions or len(directions) != len(command._provider_images):
        status = "not_run" if not directions else "limited"
    elif any(row["status"] in ("failed", "unavailable", "limited", "stopped") for row in directions):
        status = "limited"
    elif any(row["status"] == "budget_exhausted" for row in directions):
        status = "budget_exhausted"
    else:
        status = "completed"
    images["search.json"] = {"schema": "behave.search-diagnostics/1", "status": status,
        "requested_budget_seconds": None if policy is None else policy.time_budget_s,
        "requested_directions": len(command._provider_images), "directions": directions}
    return images
