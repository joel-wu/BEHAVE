"""The one rule from a `ConcreteEvaluation` to a result kind.

**Definition.** `verdict(evaluation, dropped, capped, offered, telemetry)` maps one
evaluation to the `Authoritative` result its evidence supports, and nothing else does:
`command.py` coordinates execution, `authority.py` owns result types, and this
module decides what the evidence means. The optional telemetry cannot replace the
policy-owned diagnostics, introduce a post-decision derived view, or affect the result
kind.

**Constraints** (Appendix A.1, and the review ledger's closures).

* A determinate score is a determinate score: an unpaired expected transaction is a
  mismatch, a run with no accepted input scores 0, and neither softens into `partial`.
  Both survive as `diagnostics` telemetry -- `unanswered_at_horizon` (aligned per run,
  per endpoint, per obligation instance) and `accepted`.
* A checked negative run can establish zero before the set is exhausted. Known
  reference/verifier defects, unbound or sample-only evidence prevent that decision;
  an independent infrastructure failure does not erase its counterexample.
* Without such a witness, missing mandatory members or no-result failures are
  `partial`. Search can stop without invalidating a fully checked baseline.
  Dropped/capped membership and sample verification remain partial even if the
  available subset contains a mismatch. A candidate fault instead determines zero.
* Only `concrete_evaluation` carries `passed`, and `Authoritative`'s constructor is the
  type-level lock on what one may be built from.
"""

from __future__ import annotations

from .authority import CONCRETE, PARTIAL, Authoritative
from .core.run_record import VERIFIED_SAMPLE

__all__ = ["verdict"]


def verdict(evaluation, dropped=(), capped=0, offered=0, telemetry=None):
    """The result kind this evaluation's evidence supports. See the module docstring.

    ``telemetry`` carries caller context known when the decision is made.  Derived
    post-decision views such as the secondary witness pool belong in the report and are
    rejected here: otherwise the same view can acquire two persistence locations and a
    reducer once again has to run before the score is durable.
    """
    attached = dict(telemetry or {})
    if "diagnostics" in attached:
        raise ValueError(
            "evaluation-policy telemetry cannot replace the verdict's diagnostics")
    if "secondary_witness_pool" in attached:
        raise ValueError(
            "the secondary witness pool is a post-decision report view and cannot be "
            "placed inside the authoritative verdict")
    if (not dropped and not capped and evaluation.failure_witness is not None):
        # A checked counterexample falsifies the requested set even when other
        # runs are pending or failed independently. It cannot establish a pass.
        attached["diagnostics"] = _diagnostics(evaluation)
        return Authoritative(CONCRETE, evaluation=evaluation, telemetry=attached)
    # A known backend/reference failure is not an empty budget window. Keep the
    # original reason visible even when cancellation also left work unfinished.
    failures = evaluation.no_result_failures
    if failures:
        origins = ", ".join(sorted({failure.origin.value for _, failure in failures}))
        return Authoritative(
            PARTIAL,
            why=(f"{len(failures)} of {len(evaluation.results)} replay attempts "
                 f"have no-result failures ({origins}); no score is established "
                 "for the requested testing scope"),
            evaluation=evaluation, telemetry=attached)
    if not evaluation.results or (evaluation.budget_exhausted and evaluation.accepted == 0):
        return Authoritative(PARTIAL, why="no effective completed test",
                             evaluation=evaluation, telemetry=attached)
    if not evaluation.required_complete:
        return Authoritative(
            PARTIAL, why="mandatory baseline tests remain incomplete without a negative witness",
            evaluation=evaluation, telemetry=attached)
    # An explicit runtime-budget boundary is not permission for callers to drop
    # arbitrary members, in particular known failing ones. Legacy caps remain
    # diagnostic-only even when the available subset contains a counterexample.
    left_out = len(dropped) + capped
    if left_out:
        return Authoritative(
            PARTIAL,
            why=(f"{left_out} of {offered} candidates were not "
                 f"replayed -- {len(dropped)} carried more than this path consumes and "
                 f"{capped} were cut by a caller's bound -- so this is a run over a "
                 f"subset of what the providers produced and not a score over Xi_A"),
            evaluation=evaluation,
            telemetry=attached)

    # **A sample-checked batch is not a score either.**
    #
    # Replaying every stimulus through one simulator process is sound only if each one's
    # reset prefix restores the design, and what stands behind that is a check of three
    # of them. Three of 761 is a spot check: a design that leaks state at a stimulus the
    # sample does not look at passes it. Demonstrated, by the reviewer, with four stimuli
    # of which only the unsampled one differed from its isolated replay -- the sample
    # passed and `verify_every=True` refused.
    #
    # So a run whose records came from a sample-checked batch is `partial`. The records
    # are still evidence and a mismatch in them is still a mismatch; what they cannot
    # carry is `passed`. The sound path is one process per stimulus after one
    # elaboration, which `evaluation._batched` takes wherever the artifact offers it, and which
    # assumes nothing to check.
    # Read off a field, not out of a sentence. `"a sample of three" in note` decided
    # this, so rewording prose written for a reader would have turned a refusal into a
    # pass -- the same defect this check exists to catch, arriving through the telemetry.
    # **Two diagnostics, and neither is a verdict.** Both used to convert the result to
    # `partial`, and Appendix A.1 says otherwise: an unpaired expected transaction is a
    # mismatch, and a run with no accepted input or any mismatch scores 0. A
    # ConcreteEvaluation that is a determinate failure must not come back "no score
    # established" because the wrapper had a kinder reading of the evidence.
    #
    # What each diagnostic is for. `unanswered_at_horizon` is non-zero where **every**
    # fault is a transaction still owed at the end of the run, aligned per run and per
    # obligation instance (`_only_unanswered_at_the_horizon`): a task that keeps failing
    # only this way wants a `response_deadline` declared, and then the monitor decides
    # lateness against a number the task states. `accepted` at zero says the design took
    # nothing the environment offered -- a handshake finding worth reading beside the
    # score of 0, which under the appendix it still is.
    # **An unbound trace is not a failure of anything nameable.** A record whose trace
    # names no artifact or no domain cannot be tied to the design under test, and
    # `ConcreteEvaluation.passed` answers False over one -- so a runner that lost a
    # binding turned into "this design failed", concretely. The production runners bind
    # both (the RTL testbench stamps its build identity, the in-memory driver its
    # device's); an evaluation that still carries an unbound trace is a run this build
    # cannot attribute, and it is partial evidence, not a verdict.
    if evaluation.unbound:
        return Authoritative(PARTIAL, why=(
            f"these records contain traces bound to no "
            f"{' and no '.join(evaluation.unbound)}, so what they establish cannot be "
            f"tied to the artifact and domain under test; a concrete verdict over them "
            f"would fail a design the evidence does not name"),
            evaluation=evaluation,
            telemetry=attached)

    note = dict(dict(evaluation.telemetry).get("batch") or {})
    if note.get("verified") == VERIFIED_SAMPLE:
        return Authoritative(
            PARTIAL,
            why=("these records come from one simulator process shared by every "
                 "stimulus, and what says they are the isolated records they stand in "
                 "for is a check of three of them. A sample cannot establish a property "
                 "that has to hold for each one, so this run carries findings and no "
                 "score"),
            evaluation=evaluation,
            telemetry=attached)
    attached["diagnostics"] = _diagnostics(evaluation)
    return Authoritative(CONCRETE, evaluation=evaluation,
                         telemetry=attached)


def _diagnostics(evaluation):
    return {"unanswered_at_horizon": _only_unanswered_at_the_horizon(evaluation),
            "accepted": evaluation.accepted}


def _only_unanswered_at_the_horizon(evaluation):
    """How many faults there are, if **every** one is an answer that had not arrived.

    `0` where there are none, or where any fault says something the horizon cannot
    explain away: a wrong value, an output the reference never expected, or a `missing`
    with no obligation outstanding **in its own run** to account for it.

    **The evidence is aligned, not aggregated.** The previous version flattened every
    fault of every run into one list, then asked whether *any* unresolved row anywhere in
    the evaluation mentioned "unanswered", and excused the lot on the strength of it. So a
    design that answered stimulus 3 wrongly-by-omission and left stimulus 7 in flight had
    both forgiven by one row belonging to neither, and a run with ten missing outputs and
    one transaction outstanding was excused ten times over by one obligation. Neither is a
    reading of the evidence; both are a count matched against a boolean.

    What an excuse has to line up with, and each part is a separate way to be wrong:

    * **the run.** A row carries the index of the result it came from, and only that
      result's faults may be excused by it.
    * **the endpoint.** The row names the sink it is about; the comparison is over one
      output endpoint, and a row about another endpoint explains nothing here.
    * **the obligation instance.** `_terminal` emits one row per transaction still in
      flight, carrying `about.owed` -- how many answers that transaction is still owed.
      Each owed answer excuses at most one missing output. A run with more missing
      outputs than answers outstanding has a fault the horizon does not cover, and that
      run is not excused at all.
    * **the matching policy.** Only `ordered` matching gives a `missing` fault a position
      in the stream, and only there is a missing fault by construction the tail the run
      did not reach. Under `content` a missing fault is an index into a multiset
      difference, which no obligation instance corresponds to; such a run is not excused.

    A truncated run's row carries `owed: None` -- it did not reach its horizon, so what
    was outstanding at the end is not something it observed -- and excuses nothing.
    """
    from .core.contract import Matching

    unresolved = dict(evaluation.telemetry).get("monitor_unresolved") or ()
    owed_by_run = {}
    for index, row in unresolved:
        row = dict(row)
        if str(row.get("goal", "")) != "monitor.unanswered-at-horizon":
            continue
        owed = dict(row.get("about") or {}).get("owed")
        if owed is None:
            # A truncated run, or a row from a build that did not carry the instance.
            # Either way there is no instance to align against, and the safe reading of
            # "I cannot tell which fault this is about" is that it is about none of them.
            continue
        owed_by_run.setdefault((index, str(row.get("endpoint", ""))), 0)
        owed_by_run[(index, str(row.get("endpoint", "")))] += int(owed)

    total = 0
    for index, result in enumerate(evaluation.results):
        if not result.ran or result.comparison is None:
            continue
        got = result.comparison.as_dict()
        if got.get("memory_mismatches", 0):
            return 0
        faults = list(got.get("faults") or ())
        if not faults:
            continue
        policy = got.get("policy")
        policy = getattr(policy, "value", policy)
        if policy != Matching.ORDERED.value:
            return 0
        if any((fault[0] if fault else "") != "missing" for fault in faults):
            return 0
        endpoint = _sink_name(evaluation)
        available = owed_by_run.get((index, endpoint), 0)
        if len(faults) > available:
            # More outputs missing than answers this run had outstanding: at least one of
            # them is not a late answer, and which one is not something to guess at.
            return 0
        total += len(faults)
    return total


def _sink_name(evaluation):
    """The output endpoint the comparisons in this evaluation are over.

    `core/compare.comparison_for` compares `contract.outputs()[0]`, so there is one, and
    the monitor's rows name the same endpoint. Read from the task rather than assumed to
    match, so a build that grows a second output endpoint fails to align rather than
    aligning against the wrong one.
    """
    outputs = evaluation.task.contract.outputs()
    return outputs[0].name if outputs else ""
