"""Diagnostic shared-process replay and the auxiliary reset probe.

Scored replay stays in RTLArtifact.traces_per_process: each stimulus starts in
its own process. This module deliberately retains state between stimuli to test
reset restoration. Its sampled comparisons are diagnostics, never authority for
a concrete score or a bounded conclusion.
"""

from __future__ import annotations

from ..core.refusal import Refusal, Refused
from ..core.stimulus import leading_reset_slots
from .artifact import (
    DRIVER_PLAN_VERSION,
    _observation_names,
    _sample,
)
from .execution_policy import execution_policy
from .trace_document import read_trace_document


def reset_audit(self, contract, stimuli, budget_s=None):
    """Return the auxiliary first-other-first reset probe as `(state, why)`.

    The repeated stimulus must have a positive reset prefix exactly matching
    this run's effective hold. Equal records yield `reset_probe_reproduced`;
    different records yield `violated`; an unavailable experiment yields
    `unresolved`. Reproduction is not a proof of reset restoration. This probe
    is separate from the trace monitor and cannot change the concrete score.

    A supplied `budget_s` is a cooperative deadline shared by preparation,
    build and replay, capped by any enclosing search deadline. Process cleanup
    has the ordinary runtime allowance; it does not receive execution time."""
    from ..core.refusal import Refusal as _Refusal
    from ..execution_control import check_execution
    from ..search_policy import SearchBudget, SearchPolicy, SearchStopped, active_search, search_scope

    distinct, seen = [], set()
    for stimulus in stimuli:
        if stimulus.fingerprint not in seen:
            seen.add(stimulus.fingerprint)
            distinct.append(stimulus)
        if len(distinct) == 2:
            break
    if len(distinct) < 2:
        return ("unresolved",
                ("the question needs two distinct stimuli to put -- replay one, "
                 "then another, then the first again -- and this set has "
                 f"{len(distinct)}"))
    first, other = distinct
    held = leading_reset_slots(first.slots)
    # Run policy owns the hold; the contract is only the direct-call fallback.
    declared = self.run_policy.get("reset_hold_cycles")
    wanted = (int(declared) if declared is not None
              else max((domain.reset_hold_cycles
                        for domain in (contract.clocks or ())), default=0))
    if wanted <= 0 or held == 0:
        return ("unresolved",
                (f"this run holds reset for {wanted} slots and the stimulus's "
                 f"prefix holds it for {held}, so there is no reset here to put "
                 "the question with; a record that came back would be about state "
                 "carried between stimuli, not about a reset"))
    if held != wanted:
        return ("unresolved",
                (f"the stimulus this would replay twice holds reset for {held} "
                 f"slots and this run holds exactly {wanted}, so its prefix is not "
                 "this run's reset and a record that came back would not be about "
                 "one"))
    if budget_s is None:
        got = self.traces(contract, [first, other, first], verify_batch=False)
    else:
        try:
            # Reuse cooperative tool/preparation cancellation, not an estimated
            # seconds-per-stimulus formula or a minimum admissible experiment.
            lease = SearchBudget(SearchPolicy(time_budget_s=budget_s)).direction(1)
            parent = active_search()
            if parent is not None and parent.deadline is not None:
                lease.deadline = min(lease.deadline, parent.deadline)
            with lease.resources, search_scope(lease):
                check_execution()
                got = self.traces(contract, [first, other, first], verify_batch=False)
                check_execution()
        except SearchStopped as stopped:
            return "unresolved", f"reset diagnostic stopped: {stopped.reason}"
    stopped = [g for g in got if isinstance(g, _Refusal)]
    if stopped:
        return ("unresolved",
                ("the replay that would answer it did not complete: "
                 f"{stopped[0].kind}"))
    if got[0].records == got[2].records:
        return ("reset_probe_reproduced",
                (f"the first stimulus, whose prefix holds reset for {held} slots, "
                 "gave the same record after another had run. One ordered pair: "
                 "the claim was not falsified, and is not thereby established"))
    return ("violated",
            ("the first stimulus gave a different record after another had run, "
             "so the reset prefix between them did not put the design back"))


def traces(self, contract, stimuli, verify_batch=True):
    """Replay a sequence in one process, retaining state between reset prefixes.

    Return an AuditedTrace or Refusal at every input position. The testbench uses
    the common Driver and persists each completed trace before the next stimulus.
    When requested, compare first/middle/last (or every) trace with an independent
    process. A failed or unavailable comparison withholds the batch; a successful
    sample does not establish reset restoration.
    """
    import pickle
    import uuid
    from pathlib import Path

    from ..core.driver import Driver, check_channel_shape
    from ..harness import generate_makefile, render_template
    from .tool_isolation import run_verilator_workspace
    from .tool_protocol import ToolIsolationError, command_result, command_stream

    stimuli = list(stimuli)
    if not stimuli:
        return []
    # Refuses a shape this cannot drive, before a simulator is built for it. Every
    # stimulus goes through it, so one unrunnable member is named here rather than
    # taking the batch down inside the testbench.
    identity = str(getattr(self, "identity", "") or "")
    # **One bad stimulus does not veto the batch.** Constructing every `Driver` up
    # front and letting the first refusal propagate meant a single unrunnable member
    # of 756 took the whole set down to the one-at-a-time path -- which is what the
    # first real run of this did, silently turning a batch into 756 elaborations
    # again. The refusal belongs to the stimulus that earned it.
    runnable, refused = [], {}
    for position, stimulus in enumerate(stimuli):
        try:
            Driver(contract, stimulus, artifact_identity=identity)
        except Refused as exc:
            refused[position] = exc.refusal
            continue
        runnable.append((position, stimulus))
    if not runnable:
        return [refused[i] for i in range(len(stimuli))]

    self._replay_observer(contract, None)
    native = self._observation.native
    if native is not None and len(runnable) > 1:
        unavailable = Refusal(
            "rtl-native-shared-process-unsupported",
            "native occurrence evidence is sealed once per simulator process; "
            "the legacy multi-trace reset diagnostic cannot reuse that collector")
        return [refused.get(i, unavailable) for i in range(len(stimuli))]
    watch, record_names = _observation_names(contract)

    # **Nothing appended.** The guard used to replay a sample again at the end of
    # the same batch and compare the two records. That asks whether state survives
    # *inside* one process, which is a proxy; the question is whether a batched
    # record is the record an isolated run produces, and that is asked directly
    # below, against fresh simulator processes.
    order = [s for _, s in runnable]
    driven_set = order

    # **A workspace of this call's own**, for the reason `traces_per_process` has
    # one: `workdir/plan.pickle` is a fixed name, and two invocations sharing a
    # workdir -- which `reset_restores` and the evaluation of one problem do -- write
    # each other's plan, so a trace comes back bound to a stimulus it is not a run of.
    # The per-process path was fixed first because it is the production path; this one
    # is what `reset_restores` still drives, before every scored run.
    work = Path(self.workdir) / f"batch-{uuid.uuid4().hex[:12]}"
    work.mkdir(parents=True, exist_ok=True)
    plan = {
        "version": DRIVER_PLAN_VERSION,
        "contract": contract,
        "stimuli": driven_set,
        "watch": list(watch),
        "record_names": list(record_names),
        "state_names": [],
        "initial_state": None,
        "execution_policy": execution_policy(native_events=native is not None),
    }
    if native is not None:
        plan["native_events"] = native.plan.as_dict()
        (work / "collector.cpp").write_text(native.collector)
    (work / "plan.pickle").write_bytes(pickle.dumps(plan))
    (work / "rtl.sv").write_text(
        native.source if native is not None else Path(self.rtl_path).read_text())
    (work / "driver_tb.py").write_text(render_template(
        "driver_tb.py.tmpl", period_ns=self.period_ns))
    generate_makefile(
        work, self.top_module, tb_module="driver_tb",
        coverage=False, native_events=native is not None)

    budget = self.timeout_s
    try:
        build_execution, execution = run_verilator_workspace(
            work,
            top_module=self.top_module,
            test_module="driver_tb",
            role="verilator-batch",
            outputs=("driven.json", "results.xml", "native_prefix.json", "replay_progress.json"),
            compile_timeout_s=self.timeout_s,
            simulate_timeout_s=budget,
        )
        self._record_tool_execution(build_execution)
        self._record_tool_execution(execution)
    except ToolIsolationError as error:
        for item in error.executions:
            self._record_tool_execution(item)
        late = Refusal(
            error.code, error.why,
            stimuli=len(driven_set),
            tool_executions=[item.as_dict() for item in error.executions])
        return [refused.get(i, late) for i in range(len(stimuli))]
    command_id = "verilator-batch:simulate"
    row = command_result(execution, command_id)
    if row.get("timed_out"):
        # Named, and not raised. A generic exception here is caught upstream as
        # "the batch path raised" and answered by replaying every stimulus on its
        # own, which is slower than the batch that just ran out of time.
        late = Refusal(
            "rtl-batch-timed-out",
            "the diagnostic batch did not finish inside its tool budget; "
            "replaying these one at a time would "
            "take longer, not less",
            seconds=budget, stimuli=len(driven_set), workdir=str(work),
            tool_execution=execution.as_dict())
        return [refused.get(i, late) for i in range(len(stimuli))]
    if "native_prefix.json" in execution.outputs:
        from .replay_interruption import interrupted_replay

        failure = interrupted_replay(
            execution.outputs["native_prefix.json"], order[0], self._frontend_catalogue,
            expected_plan=None if native is None else native.plan, execution=execution.as_dict())
        return [refused.get(i, failure.reason) for i in range(len(stimuli))]
    if "driven.json" not in execution.outputs:
        diagnostic = (
            command_stream(execution, command_id, "stdout")
            + command_stream(execution, command_id, "stderr")
        ).decode("utf-8", errors="replace")
        missing = Refusal(
            "rtl-artifact-no-trace",
            "the simulation produced no record, so there is nothing to read back; "
            "the simulator's own last words are attached rather than summarised",
            returncode=row.get("returncode"), workdir=str(work), output=diagnostic,
            tool_execution=execution.as_dict())
        return [refused.get(i, missing) for i in range(len(stimuli))]
    try:
        got, failure = read_trace_document(
            execution.outputs["driven.json"], max_traces=len(driven_set))
        if failure is not None:
            raise ValueError("a diagnostic batch has no analysis initialization")
    except (KeyError, TypeError, ValueError):
        malformed = Refusal(
            "rtl-artifact-trace-malformed",
            "the isolated batch returned no closed trace document",
            tool_execution=execution.as_dict())
        return [refused.get(i, malformed) for i in range(len(stimuli))]

    out, where = [], {position: i for i, (position, _) in enumerate(runnable)}
    for position, stimulus in enumerate(stimuli):
        if position in refused:
            out.append(refused[position])
            continue
        index = where[position]
        if index >= len(got):
            diagnostic = (
                command_stream(execution, command_id, "stdout")
                + command_stream(execution, command_id, "stderr")
            ).decode("utf-8", errors="replace")
            out.append(Refusal(
                "rtl-artifact-batch-stopped-short",
                "the simulator stopped before reaching this stimulus, so it has no "
                "record; the ones it did finish are kept rather than discarded with "
                "it",
                position=position, finished=len(got), offered=len(runnable),
                output=diagnostic))
            continue
        out.append(self._trace_of(contract, stimulus, got[index],
                                  check_channel_shape, identity,
                                  expected_native=None if native is None else native.plan))

    if verify_batch and len(order) > 1:
        verdict = self._verified_against_isolated(contract, order, out, runnable)
        if verdict is not None:
            return [verdict] * len(stimuli)
    return out


def _verified_against_isolated(self, contract, order, out, runnable):
    """Return a refusal for any unavailable or disagreeing isolated comparison."""
    from pathlib import Path

    indices = (range(len(order)) if self.verify_every
               else _sample(len(order)))
    for index in indices:
        batched = out[runnable[index][0]]
        if isinstance(batched, Refusal):
            # This stimulus has no batched record to check, which is already its own
            # refusal in `out`. Nothing to compare, and nothing hidden.
            continue
        # `isolated{index}` beside the workdir was another fixed name shared by
        # every invocation that checks the same position. The inner `traces` gives
        # itself a workspace now, so this only has to not collide on the parent.
        isolated = type(self)(
            self.rtl_path, self.top_module,
            Path(self.workdir).parent / f"isolated-{index}",
            period_ns=self.period_ns, timeout_s=self.timeout_s)
        if self._frontend_catalogue is not None:
            isolated.frontend_catalogue(expected_identity=self.identity)
        alone = isolated.traces(
            contract, [order[index]], verify_batch=False)[0]
        if isinstance(alone, Refusal):
            return Refusal(
                "rtl-batch-unverifiable",
                "this stimulus could not be replayed on its own, so whether the "
                "batch reproduces an isolated run is unknown for it -- and an "
                "unknown is not a pass. The whole batch is withheld rather than "
                "returned as verified",
                stimulus=order[index].fingerprint,
                isolated_refusal=alone.kind, batch=len(order))
        if alone.records != batched.records:
            return Refusal(
                "rtl-batch-order-dependent",
                "this stimulus gives a different record in the batch than it gives "
                "run on its own, so the design carries state across the reset "
                "prefix that separates them and a batched run is not the isolated "
                "run it stands in for",
                stimulus=order[index].fingerprint,
                checked=len(list(indices)) if self.verify_every else len(
                    _sample(len(order))),
                every=self.verify_every, batch=len(order))
    return None
