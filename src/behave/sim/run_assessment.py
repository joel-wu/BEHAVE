"""One trace's monitor/comparison evidence, before ordered set aggregation.

Nothing here mutates a shared accumulator. A caller supplies a run-local
monitor; the matcher constructs and closes a fresh reference execution.
Environment-domain failures contribute neither goal hits nor comparisons.
"""

from dataclasses import dataclass
from time import perf_counter

from .core.compare import comparison_for
from .core.failure import CandidateFault, FailureRaised, RunFailure
from .core.monitor import MonitorReport
from .core.refusal import Refusal, Refused
from .core.run_record import RunResult
from .core.stimulus import AuditedTrace


@dataclass(frozen=True)
class RunAssessment:
    result: RunResult
    hits: frozenset[str] = frozenset()
    report: MonitorReport | None = None
    outside: tuple = ()
    costs: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class CheckedRun:
    """Trusted monitor output awaiting the unchanged reference comparison."""

    trace: AuditedTrace
    hits: frozenset[str]
    report: MonitorReport | None
    costs: tuple[tuple[str, float], ...]


def replay_one(stimulus, execute):
    """Preserve the existing attribution of the shared replay boundary."""
    try:
        trace = execute()
        if isinstance(trace, Refusal):
            raise Refused(trace)
        return trace
    except FailureRaised as exc:
        return exc.failure
    except CandidateFault as exc:
        return RunFailure.candidate("candidate-observation", exc.refusal)
    except Refused as exc:
        return RunFailure.verifier("candidate-replay", exc.refusal)
    except Exception as exc:  # noqa: BLE001 - shared replay boundary
        return RunFailure.verifier(
            "candidate-replay", Refusal(
                "verifier-replay-failed",
                "the replay service failed without an attributed artifact result",
                error=type(exc).__name__, detail=str(exc)))


def assess_run(task, stimulus, trace, reference, *, monitor=None,
               reset_state_fields=None, replay_seconds=None, compare=comparison_for):
    """Assess exactly this run, retaining partial evidence when comparison fails."""
    checked = check_run(stimulus, trace, monitor=monitor,
                        reset_state_fields=reset_state_fields, replay_seconds=replay_seconds)
    return assess_checked(task, stimulus, checked, reference, compare=compare)


def check_run(stimulus, trace, *, monitor=None, reset_state_fields=None, replay_seconds=None,
              _protocol_ledgers=None):
    """Decode-independent half of assessment; no candidate or reference execution."""
    costs = [] if replay_seconds is None else [("replay", replay_seconds)]
    if type(trace) is RunFailure:
        hits = frozenset() if trace.observed is None else frozenset(trace.observed.catalogue)
        return RunAssessment(RunResult.failed_to_run(stimulus, trace),
                             hits=hits, costs=tuple(costs))
    if isinstance(trace, Refusal):
        return RunAssessment(RunResult.failed_to_run(
            stimulus, RunFailure.verifier("candidate-replay", trace)), costs=tuple(costs))
    report = None
    if monitor is not None:
        start = perf_counter()
        failure = None
        try:
            recovered = {} if _protocol_ledgers is None else {"_protocol_ledgers": _protocol_ledgers}
            report = monitor.check(trace.stimulus, trace, reset_state_fields=reset_state_fields,
                                   **recovered)
        except FailureRaised as exc:
            failure = exc.failure
        except Refused as exc:
            failure = RunFailure.verifier("trace-monitor", exc.refusal)
        except Exception as exc:  # noqa: BLE001 - trusted verifier boundary
            failure = RunFailure.verifier(
                "trace-monitor", Refusal(
                    "verifier-monitor-failed", "the trace monitor did not produce a judgment",
                    error=type(exc).__name__))
        costs.append(("monitor", perf_counter() - start))
        if failure is not None:
            return RunAssessment(RunResult.not_compared(
                stimulus, trace, failure, observations_eligible=False), costs=tuple(costs))
        if report.assumption_violations:
            first = report.assumption_violations[0]
            failure = RunFailure.verifier(
                "environment-domain", Refusal(
                    "environment-assumption-violated",
                    "the recorded environment lies outside the task's declared trace domain",
                    slot=first.slot, goal=first.goal))
            return RunAssessment(
                RunResult.not_compared(stimulus, trace, failure, observations_eligible=False),
                outside=tuple(report.assumption_violations), costs=tuple(costs))

    _, reached = trace.goal_summary
    hits = set(reached)
    if report is not None:
        hits.update(hit.goal for hit in report.hits)
        hits.update(bad.goal for bad in report.violations)
    return CheckedRun(trace, frozenset(hits), report, tuple(costs))


def assess_checked(task, stimulus, checked, reference, *, compare=comparison_for):
    """Finish a trusted checked run without decoding or monitoring it again."""
    if type(checked) is RunAssessment:
        return checked
    if type(checked) is not CheckedRun:
        raise TypeError("comparison needs an exact checked run")
    trace, costs = checked.trace, list(checked.costs)
    if (trace.request or trace.stimulus).fingerprint != stimulus.fingerprint:
        raise ValueError("checked trace belongs to another stimulus")
    evidence = {"hits": checked.hits, "report": checked.report}
    # A checked failing prefix already determines failure. Do not run a
    # reference over unfinished traffic or replace its protocol counterexample
    # with a missing-output / incomplete-comparison diagnostic.
    from .core.stimulus import Completion

    if trace.completion is Completion.INCOMPLETE and checked.report is not None \
            and checked.report.violations:
        first = checked.report.violations[0]
        failure = RunFailure.candidate("protocol-monitor", Refusal(
            first.goal, first.why, slot=first.slot, endpoint=first.endpoint))
        return RunAssessment(RunResult.not_compared(stimulus, trace, failure),
                             **evidence, costs=tuple(costs))
    if reference is None:
        failure = RunFailure.reference(
            "reference-selection", Refusal(
                "reference-not-provided", "no reference was given, so output agreement has no basis"))
        return RunAssessment(RunResult.not_compared(stimulus, trace, failure),
                             **evidence, costs=tuple(costs))
    start = perf_counter()
    failure = None
    try:
        compared = compare(task, reference, trace)
    except FailureRaised as exc:
        failure = exc.failure
    except Refused as exc:
        failure = RunFailure.verifier("output-comparison", exc.refusal)
    except Exception as exc:  # noqa: BLE001 - reference execution boundary
        failure = RunFailure.verifier(
            "output-comparison", Refusal(
                "verifier-comparison-failed", "the trusted comparison did not produce a judgment",
                error=type(exc).__name__))
    costs.append(("comparison", perf_counter() - start))
    result = (RunResult.not_compared(stimulus, trace, failure) if failure is not None
              else RunResult.compared(stimulus, trace, compared))
    return RunAssessment(result, **evidence, costs=tuple(costs))
