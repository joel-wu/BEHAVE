"""Invocation-local native replay and feedback, separate from prepared RTL models.

Only completed typed traces are memoized here. Exact Analysis requests independent
execution with reuse=False; neither cache content nor clearing it grants a proof.
Preparation and frozen source documents belong to the bound job context.
"""

from ..core.failure import RunFailure
from ..core.refusal import Refusal, Refused


class RTLReplaySession:
    """Native execution state for one bound context, with no solver/model cache."""

    __slots__ = ("context", "_source_traces", "_selection_traces", "_selection_feedback")

    def __init__(self, context):
        self.context = context
        self._source_traces = {}
        self._selection_traces = {}
        self._selection_feedback = {}

    def clear(self):
        """Release execution records without invalidating prepared source/products."""
        self._source_traces.clear()
        self._selection_traces.clear()
        self._selection_feedback.clear()

    def source_trace(self, stimulus, *, initial_state=None, startup_state=None, reuse=True):
        """Replay a native, settled-register or pre-HDL construction request.

        These are distinct domains, even when their register values coincide.
        Construction also binds physical pins; its cache key must retain both
        the initialization phase and every chosen pin, not only the stimulus.
        Exact evidence consumers use reuse=False for independent execution.
        """

        self.context._check_artifact_identity()
        from ..generation_protocol import (stimulus_bytes)

        state_widths = None
        from .initial_state import RTLInitialState
        from .startup_state import RTLStartupState

        if initial_state is not None and startup_state is not None:
            raise ValueError("a source replay has one initialization phase, not two")
        if startup_state is not None and type(startup_state) is not RTLStartupState:
            raise TypeError("a construction replay has an exact startup choice")
        # Every replay observes the compiler-owned physical storage and output
        # ports of the source image; no synthesis product names them.
        layout = self.context.prepare_source()
        if isinstance(layout, Refusal):
            raise Refused(layout)
        choice = initial_state if startup_state is None else startup_state.registers
        if choice is not None:
            if type(choice) is not RTLInitialState:
                raise TypeError("an exact source replay has a typed initial choice")
            state_widths = {name: width for name, width, _ in choice.fields}
            choice.check_binding(
                self.context.artifact_identity, layout.state_names, state_widths)
        if startup_state is not None:
            startup_state.check_binding(
                self.context.artifact_identity, layout.state_names, state_widths, self.context.contract)
        key = (stimulus_bytes(stimulus),
               None if choice is None else choice.fields,
               None if startup_state is None else startup_state.pins.fields)
        cached = self._source_traces.get(key) if reuse else None
        if cached is not None:
            return cached
        self.context._check_artifact_identity()
        trace = self.context.artifact.exact_trace(
            self.context.contract, stimulus,
            observe_names=layout.outputs, state_names=layout.state_names,
            state_widths=state_widths, initial_state=initial_state,
            **({"startup_state": startup_state} if startup_state is not None else {}))
        if trace.artifact != self.context.artifact_identity:
            raise Refused(Refusal(
                "rtl-exact-source-trace-artifact-mismatch",
                "the independent source trace names a different artifact than the "
                "frozen exact relation context",
                frozen=self.context.artifact_identity, trace=trace.artifact))
        if reuse:
            self._source_traces[key] = trace
        return trace

    def selection_traces(self, stimuli, *, workers=None):
        """Replay seeds for Select without constructing the exact relation.

        Select needs only the accepted-transfer sequence.  Batch replay shares one
        Verilator build while every seed still runs in its own process; exact internal
        state is requested later, and only for a seed that reaches Probe.
        """

        from ..core.stimulus import AuditedTrace
        from ..generation_protocol import (stimulus_bytes)

        self.context._check_artifact_identity()
        stimuli = tuple(stimuli)
        keys = tuple(stimulus_bytes(stimulus) for stimulus in stimuli)
        answers = {key: self._selection_traces[key]
                   for key in keys if key in self._selection_traces}
        missing, missing_keys = [], []
        scheduled = set()
        for key, stimulus in zip(keys, stimuli):
            if key not in self._selection_traces and key not in scheduled:
                missing.append(stimulus)
                missing_keys.append(key)
                scheduled.add(key)
        if missing:
            before = self.context._check_artifact_identity()
            ownership = self.context._selection_state_ownership(missing)
            traces = self.context.artifact.traces_per_process(
                self.context.contract, missing, workers=workers,
                state_names=() if ownership is None else ownership.names,
                state_widths=None if ownership is None else dict(ownership.widths))
            if len(traces) != len(missing):
                raise Refused(Refusal(
                    "rtl-generation-source-batch-incomplete",
                    "the concrete selection replay did not answer every seed",
                    seeds=len(missing), traces=len(traces)))
            if self.context._check_artifact_identity() != before:
                raise Refused(Refusal(
                    "rtl-generation-source-changed-during-selection",
                    "the RTL source changed during concrete selection replay"))
            for key, trace in zip(missing_keys, traces):
                if type(trace) is RunFailure:
                    answers[key] = Refusal(
                        "rtl-generation-selection-failed",
                        "concrete selection replay failed before producing a complete trace",
                        failure=trace.as_dict())
                    continue
                if isinstance(trace, Refusal):
                    # Infrastructure failure is not a semantic cache entry.  A later
                    # call may retry the same immutable seed.
                    answers[key] = trace
                    continue
                if type(trace) is not AuditedTrace:
                    answers[key] = Refusal(
                        "rtl-generation-source-trace-not-typed",
                        "the concrete selection runner returned no AuditedTrace")
                    continue
                if trace.artifact != self.context.artifact_identity:
                    answers[key] = Refusal(
                        "rtl-generation-source-artifact-mismatch",
                        "the concrete selection trace names another RTL artifact",
                        frozen=self.context.artifact_identity, trace=trace.artifact)
                    continue
                self._selection_traces[key] = trace
                answers[key] = trace
        return tuple(answers[key] for key in keys)

    def selection_feedback(self, stimulus, trace):
        """Run the ordinary concrete monitor once when no assessment is supplied.

        Runtime scoring supplies its already-checked feedback instead. This path
        serves standalone fixed-schedule callers; it never executes the RTL again.
        """
        from ..core.observation_monitor import execution_monitor_for
        from ..generation_protocol import (ReplayFeedback)
        from ..run_assessment import CheckedRun, check_run

        key = trace.fingerprint
        if key not in self._selection_feedback:
            ownership = self.context._selection_state_ownership((stimulus,))
            fields = None
            if ownership is not None:
                fields = (dict(ownership.by_domain(self.context.contract)) if len(self.context.contract.clocks) > 1
                          else ownership.names)
            checked = check_run(stimulus, trace, monitor=execution_monitor_for(self.context.contract, "rtl"),
                                reset_state_fields=fields)
            self._selection_feedback[key] = (ReplayFeedback(
                stimulus.fingerprint, self.context.complete_catalogue.digest(), key, checked.hits)
                if type(checked) is CheckedRun else checked.result.run_failure.reason)
        return self._selection_feedback[key]

    def selection_trace(self, stimulus):
        """The single-seed form of :meth:`selection_traces`."""

        return self.selection_traces((stimulus,), workers=1)[0]
