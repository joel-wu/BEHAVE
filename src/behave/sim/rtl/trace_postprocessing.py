"""Trusted CPU stage of RTL replay, inside the existing run's CPU reservation.

Workers receive the simulator's original JSON bytes and a parent-owned decode
plan. They only decode and monitor: no candidate/reference source, tools or
child pools run here. Multiprocessing transports trusted Python records back
once; simulator bytes are never unpickled. This is not a persisted wire format.

Each replay thread waits for its own result before comparing or starting another
run. Thus simulation, checking and comparison share one lane, not three pools'
worth of runnable work. Shutdown precedes releasing the enclosing CPU wave.
"""

from __future__ import annotations

import os
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
from typing import TYPE_CHECKING

from ..core.contract import TaskContract
from ..core.driver import check_channel_shape
from ..core.failure import FailureRaised, RunFailure
from ..core.observation_monitor import execution_monitor_for
from ..core.refusal import Refusal, Refused
from ..core.run_record import RunResult
from ..core.stimulus import AuditedTrace
from ..cpu_admission import cpu_cleanup_unconfirmed
from ..run_assessment import RunAssessment, check_run
from ..scoring_policy import active_scoring
from ..search_policy import active_search
from .trace_decoder import TraceDecoder
from .trace_document import read_trace_document
from .trace_process import TraceWorkerPool

if TYPE_CHECKING:
    from .initial_state import RTLInitialState
    from .native_events import NativeEventPlan
    from .observation_binding import ObservationBinding
    from .startup_state import RTLStartupState
    from .tool_protocol import ToolExecution


@dataclass(frozen=True)
class TracePlan:
    decoder: TraceDecoder
    contract: TaskContract
    identity: str
    state_widths: dict | None = None
    native: NativeEventPlan | None = None
    observation: ObservationBinding | None = None
    initial_state: RTLInitialState | None = None
    completion_targets: dict | None = None
    startup_state: RTLStartupState | None = None
    checkpoint_plans: dict | None = None

    def decode(self, stimulus, payload, execution, *, _protocol_ledgers=None):
        """The original response and slot guards, with unchanged attribution."""
        try:
            got, failure = read_trace_document(payload, max_traces=1)
            if failure is not None:
                if self.initial_state is None and self.startup_state is None:
                    raise ValueError("an initial-state refusal is analysis-only")
                return failure
        except (KeyError, TypeError, ValueError):
            return RunFailure.verifier(
                "rtl-trace-collection", Refusal(
                    "rtl-artifact-trace-malformed",
                    "the isolated simulator returned no closed trace document",
                    stimulus=stimulus.fingerprint, tool_execution=execution))
        if not got:
            return RunFailure.verifier(
                "rtl-trace-collection", Refusal(
                    "rtl-artifact-no-trace",
                    "the record this isolated run wrote holds no trace",
                    stimulus=stimulus.fingerprint, tool_execution=execution))
        try:
            if self.startup_state is not None:
                from .startup_state import RTLStartupState

                try:
                    observed_startup = RTLStartupState.from_dict(got[0].get("startup_state"))
                except (TypeError, ValueError):
                    observed_startup = None
                if observed_startup is None or observed_startup.as_dict() != self.startup_state.as_dict():
                    raise Refused(Refusal("rtl-startup-state-unconfirmed",
                        "the native replay did not confirm its pre-construction choice"))
                got[0] = {key: value for key, value in got[0].items() if key != "startup_state"}
            trace = self.decoder.decode(
                self.contract, stimulus, got[0], check_channel_shape, self.identity,
                expected_state_widths=self.state_widths,
                expected_native=self.native, observation=self.observation,
                expected_outputs=(self.completion_targets or {}).get(stimulus.fingerprint),
                checkpoint_plan=(self.checkpoint_plans or {}).get(stimulus.fingerprint),
                initialization=(self.startup_state.as_dict() if self.startup_state is not None else
                                self.initial_state.as_dict() if self.initial_state is not None else None),
                _protocol_ledgers=_protocol_ledgers)
            if isinstance(trace, Refusal):
                return RunFailure.verifier("rtl-trace-validation", trace)
            if self.initial_state is not None:
                self.initial_state.check_observed(dict(trace.initial_state))
        except Refused as exc:
            return RunFailure.verifier("rtl-trace-validation", exc.refusal)
        except Exception as exc:  # noqa: BLE001 - trusted trace decoder
            return RunFailure.verifier(
                "rtl-trace-validation", Refusal(
                    "rtl-artifact-trace-validation-failed",
                    "the trusted RTL trace decoder did not produce an audited trace",
                    error=type(exc).__name__, detail=str(exc),
                    stimulus=stimulus.fingerprint))
        return trace


@dataclass(frozen=True)
class MonitorPlan:
    reset_state_fields: Sequence[str] | Mapping[str, Sequence[str]] | None = None


@dataclass(frozen=True)
class RawTrace:
    payload: bytes
    execution: ToolExecution

    def decode(self, postprocessor, stimulus, replay_seconds):
        """Retain failed-run diagnostics before the owning workspace is removed.

        Successful traces do not read or parse command logs. Only the existing
        no-trace/malformed-document failure is enriched; neither XML nor log
        text chooses a score or changes the original failure's attribution.
        """
        result = postprocessor.decode(stimulus, self.payload, self.execution.as_dict(), replay_seconds)
        failure = (result if type(result) is RunFailure else
                   result.result.run_failure if type(result) is RunAssessment else None)
        if failure is None or failure.phase != "rtl-trace-collection":
            return result
        from .tool_protocol import ToolIsolationError, command_stream

        diagnostics = {"cocotb_results": self.execution.outputs.get("results.xml", b"").decode(
            "utf-8", errors="replace")}
        for stream in ("stdout", "stderr"):
            try:
                diagnostics[stream] = command_stream(
                    self.execution, "verilator-simulate", stream).decode("utf-8", errors="replace")
            except (OSError, ToolIsolationError) as error:
                diagnostics[stream + "_unavailable"] = f"{type(error).__name__}: {error}"
        reason = Refusal(failure.kind, failure.why, **dict(failure.reason.detail),
                         simulator_diagnostics=diagnostics)
        enriched = RunFailure(failure.origin, failure.phase, reason,
                              disposition=failure.disposition, policy=failure.policy,
                              observed=failure.observed)
        return (enriched if type(result) is RunFailure else
                replace(result, result=RunResult.failed_to_run(stimulus, enriched)))


_PLAN = None
_MONITOR = None


def _initialize(plan, monitor):
    global _PLAN, _MONITOR
    # The parent already owns this lane. No descendant can infer a new pool
    # from host affinity. No broker endpoint or held socket is passed to this worker.
    os.environ["BEHAVE_JOB_CPU_LIMIT"] = "1"
    _PLAN, _MONITOR = plan, monitor


def _check(plan, monitor, stimulus, payload, execution, replay_seconds):
    start = perf_counter()
    protocol_ledgers = {}
    trace = plan.decode(stimulus, payload, execution, _protocol_ledgers=protocol_ledgers)
    if type(trace) is AuditedTrace:
        # The matcher needs this identity. Compute it once on the existing
        # supervised lane, not later while the coordinator holds several huge
        # traces and must meet its settlement deadline. The immutable record's
        # cached value crosses the private transport with the same evidence.
        _ = trace.fingerprint
    decoded = perf_counter() - start
    return check_run(
        stimulus, trace, monitor=execution_monitor_for(plan.contract, "rtl"),
        reset_state_fields=monitor.reset_state_fields,
        replay_seconds=replay_seconds + decoded,
        _protocol_ledgers=protocol_ledgers)


def _worker(stimulus, payload, execution, replay_seconds, completion=None):
    plan = _PLAN
    if completion is not None:
        targets, checkpoint = completion
        plan = replace(plan,
            completion_targets={stimulus.fingerprint: targets},
            checkpoint_plans={stimulus.fingerprint: checkpoint} if checkpoint is not None else None)
    return _check(plan, _MONITOR, stimulus, payload, execution, replay_seconds)


class TracePostprocessor:
    """One invocation's CPU workers; callers already hold all their lanes."""

    def __init__(self, plan, monitor, workers):
        if type(plan) is not TracePlan or (monitor is not None and type(monitor) is not MonitorPlan):
            raise TypeError("postprocessing needs closed decode/monitor plans")
        if type(workers) is not int or workers < 1:
            raise ValueError("postprocessing needs a positive admitted width")
        self.plan, self.monitor = plan, monitor
        # A single admitted CPU is still a supervised lane when a deadline is
        # active. Inline JSON decoding/monitoring cannot be interrupted while
        # inside a large C/Python operation. The parent waits; this does not
        # reserve another CPU or start another runnable scoring lane.
        supervised = workers > 1 or active_scoring() is not None or active_search() is not None
        self.pool = (TraceWorkerPool(plan, monitor, workers)
            if monitor is not None and supervised else None)

    def __enter__(self):
        return self

    def __exit__(self, _type, failure, _traceback):
        if self.pool is not None:
            try:
                self.pool.close()
            except BaseException as cleanup:
                cpu_cleanup_unconfirmed()
                if failure is None:
                    raise
                failure.add_note("Trace worker cleanup unconfirmed: "
                                 + "".join(traceback.format_exception_only(cleanup)).rstrip())

    def decode(self, stimulus, payload, execution, replay_seconds):
        if self.monitor is None:
            return self.plan.decode(stimulus, payload, execution)
        if self.pool is None:
            return _check(self.plan, self.monitor, stimulus, payload, execution, replay_seconds)
        try:
            # Counts belong to this admitted request, not to when this reused
            # worker first started. Only the trusted host sends this binding.
            binding = ((self.plan.completion_targets or {}).get(stimulus.fingerprint),
                       (self.plan.checkpoint_plans or {}).get(stimulus.fingerprint))
            return self.pool.check(stimulus, payload, execution, replay_seconds, binding)
        except FailureRaised:
            raise  # caller cancellation/deadline retains its attribution
        except Exception as exc:  # noqa: BLE001 - trusted worker/transport failure
            # Preserve earlier runs. Neither a crashed worker nor a transport
            # error justifies retrying through another execution path.
            return RunFailure.infrastructure(
                "trace-postprocessing", Refusal(
                    "rtl-trace-worker-failed",
                    "the trusted trace worker did not return its complete result",
                    error=type(exc).__name__, detail=str(exc),
                    stimulus=stimulus.fingerprint))
