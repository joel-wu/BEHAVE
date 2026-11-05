"""The two results a run produces, and why neither may overwrite the other.

TODO3 T3-03's handoff, as records. `ConcreteEvaluation` is what running the artifact
established: the immutable stimulus set, the score, the raw record per stimulus, the
concrete hits, and the telemetry of how the stimuli were generated. `AnalysisOutcome` is
what the post-run bounded analysis made of that, independently.

**The analysis branch can only change itself.** A missing exact provider, a solver
timeout, a refused encoding: each of those is a fact about the analysis, and none of them
may suppress, filter or recompute the concrete result. The two used to be one status with
an analysis branch that could overwrite the concrete one, which is how a run that scored
and a run that could not be analysed became indistinguishable.

So they are separate records with separate serializations, and the report holds both.
"""

from __future__ import annotations

import enum
import hashlib
import json

from .catalogue import SAFETY, catalogue_of
from .conditional_evidence import AnalysisStopEvidence, ConditionalEvidence
from .failure import FailureDisposition, FailureOrigin
from .frozen import Frozen, freeze
from .refusal import Refusal, Refused
from .run_record import VERIFIED_SAMPLE, RunResult
from .stimulus import Completion, Stimulus, canonical_json

__all__ = ["AnalysisOutcome", "ConcreteEvaluation", "Verdict", "catalogue_image"]


class Verdict(enum.Enum):
    """What the post-run analysis concluded, as a closed set.

    Five, and the last four are not degrees of the first. `REFUSED` is this side unable
    to proceed; `INCONSISTENT` is two readings of one run disagreeing, which is a fault
    in the prover and not a property of any goal; `INFEASIBLE_DOMAIN` is the trace set
    having no member, so every statement about it is vacuous; `FEASIBILITY_UNRESOLVED`
    is nobody having established that either way.
    """

    CLASSIFIED = "classified"
    INCONSISTENT = "inconsistent"
    REFUSED = "refused"
    INFEASIBLE_DOMAIN = "infeasible-domain"
    FEASIBILITY_UNRESOLVED = "feasibility-unresolved"

    @property
    def per_goal(self):
        """Whether a per-goal partition may be published under this verdict."""
        return self is Verdict.CLASSIFIED

    def __bool__(self):
        raise TypeError(
            "Verdict has no truth value; inspect the named analysis outcome")


def catalogue_image(catalogue):
    """The one canonical public image used by concrete and exact records."""
    declared = catalogue_of(catalogue, allow_legacy=False)
    return declared.goals_image()


class ConcreteEvaluation(Frozen):
    """What running the artifact established, and nothing else.

    **One list of `RunResult`, not five parallel ones.** It kept a stimulus list, a
    records list, a comparisons dict, a mismatches list, an incomplete list and an
    infrastructure list, and a reader had to line them up by index -- which they did not:
    `evaluate` appended only successful traces to `records` while keeping every stimulus,
    so a single infrastructure failure shifted every later index and a comparison filed
    under the stimulus it was of pointed at a different trace. Parallel arrays cannot
    express a run that failed.

    Every count below is derived from the results. Nothing is stored twice, so nothing
    can disagree with itself: replacing an entry changes the score, the mismatch list and
    the fingerprint together, which is what makes the record a record.
    """

    __slots__ = (
        "_fingerprint",
        "budget_exhausted",
        "catalogue",
        "hits",
        "requested_stimuli",
        "required_stimuli",
        "results",
        "task",
        "telemetry",
        "unbound",
    )

    def __init__(self, task, results=(), hits=(), telemetry=None, catalogue=None,
                 requested_stimuli=None, *, budget_exhausted=False, required_stimuli=None):
        if type(budget_exhausted) is not bool:
            raise TypeError("budget exhaustion must be Boolean")
        self.budget_exhausted = budget_exhausted
        self.task = task
        self.results = tuple(results)
        # **Typed before anything reads them.** Every check and every derived count below
        # reads attributes off these -- the stimulus, whether it ran, its trace, its
        # faults -- so an object carrying those attributes was a run. Exact type, because
        # a subclass inherits the checks and overrides the answers. First, because the
        # hits aggregation immediately below reaches into `r.trace.records`, so a
        # malformed impostor crashed there with an `AttributeError` rather than being
        # told what was wrong with it.
        for result in self.results:
            if type(result) is not RunResult:
                raise Refused(Refusal(
                    "evaluation-result-not-a-run-record",
                    "this is not a `RunResult`, so nothing that makes one evidence -- "
                    "the closed set of states, the binding to a trace and a comparison, "
                    "the frozen fields -- has been established for it",
                    got=type(result).__name__))
        # **Hits come from the runs.** They were an aggregate the caller passed in
        # beside the results, so the two could disagree about what a run reached and
        # nothing compared them: a caller could report a hit no slot recorded, or omit
        # one every slot did. Every `SlotRecord` carries its own, so the union over the
        # traces is the fact, and a caller's aggregate is checked against it rather than
        # believed.
        # A compact vector iterates only true and unreadable positions; its catalogue
        # nevertheless declares every name represented by the record, including false
        # positions.  Shape validation must use that complete universe rather than the
        # sparse iterator, or an all-false foreign vector could look empty here.
        recorded_names, observed = set(), set()
        for result in self.results:
            if not result.ran:
                continue
            names, reached = result.trace.goal_summary
            recorded_names.update(names)
            # Raw catalogue shape is checked even outside the domain. Only
            # eligible observations establish hits; a cached union is no waiver.
            if result.observations_eligible:
                observed.update(reached)
        #: The declaration denominator is already a frozen, canonical manifest.  It is
        #: retained rather than copied into another mapping shape, so occurrence and
        #: schema identity cannot disappear between evaluation and bounded analysis.
        self.catalogue = catalogue_of(
            {} if catalogue is None else catalogue, allow_legacy=False)
        # Interrupted execution has no complete slot trace, but its validated
        # positive observations remain evidence. Never infer these from error text.
        for result in self.results:
            failure = result.refused
            if failure is not None and failure.observed is not None:
                failure.observed.check_catalogue(self.catalogue)
                recorded_names.update(failure.observed.catalogue)
                if result.observations_eligible:
                    observed.update(failure.observed.catalogue)
        self.hits = tuple(sorted(set(hits)))
        self.telemetry = freeze(dict(telemetry or {}))
        seen, unbound = set(), set()
        for result in self.results:
            # `Xi_A` is a set: two producers reaching one stimulus found one stimulus,
            # and replaying it twice would count one test twice.
            if result.stimulus.fingerprint in seen:
                raise Refused(Refusal(
                    "evaluation-duplicate-stimulus",
                    "this stimulus appears twice, so one test would be counted twice "
                    "and a search that rediscovers its own witnesses would look "
                    "thorough",
                    stimulus=result.stimulus.fingerprint))
            seen.add(result.stimulus.fingerprint)
            # Against the task's frozen reference, not against the entry's own -- the
            # check this replaces read the expected value off the thing being checked.
            result.check(task.reference)
            # And against the task itself: the contract the trace ran under and the
            # artifact that produced it. A trace naming neither is unverifiable and the
            # evaluation says so rather than treating silence as agreement.
            for name in result.check_task(task) or ():
                unbound.add(name)
        # **Every hit names a goal the catalogue declares.** A hit whose goal is not in
        # the catalogue is either a goal nobody enumerated -- so the denominator any rate
        # is over does not contain it -- or a stale identifier from a different policy.
        # `safety_hits` filters by the catalogue's class, so such a hit was silently
        # dropped from the classification while still counting as a hit.
        #: Which of a trace's bindings the runners could not supply. Recorded, because
        #: "the check did not run" and "the check passed" are different facts and the
        #: fingerprint has to be able to tell two evaluations apart on it.
        self.unbound = tuple(sorted(unbound))
        self.requested_stimuli = (tuple(r.stimulus for r in self.results)
                                  if requested_stimuli is None else tuple(requested_stimuli))
        if any(type(s) is not Stimulus for s in self.requested_stimuli):
            raise TypeError("requested replay members must be Stimulus records")
        requested = [s.identity_bytes for s in self.requested_stimuli]
        if len(set(requested)) != len(requested):
            raise ValueError("requested replay members must be distinct")
        self.required_stimuli = (self.requested_stimuli if required_stimuli is None
                                 else tuple(required_stimuli))
        if any(type(s) is not Stimulus for s in self.required_stimuli):
            raise TypeError("required replay members must be Stimulus records")
        required = [s.identity_bytes for s in self.required_stimuli]
        if (len(set(required)) != len(required) or not set(required).issubset(requested)
                or (requested and not required)):
            raise ValueError("required replay members must be a nonempty distinct subset of the pool")
        completed = [r.stimulus.identity_bytes for r in self.results]
        completed_set = set(completed)
        if [key for key in requested if key in completed_set] != completed:
            raise ValueError("completed runs must retain their requested membership and order")
        stray = sorted(set(self.hits) - set(self.catalogue))
        if stray:
            raise Refused(Refusal(
                "evaluation-hit-outside-the-catalogue",
                "these goals were reached and the catalogue this evaluation is over "
                "does not declare them, so nothing can say what class they are or what "
                "denominator they belong to",
                hits=stray, declared=sorted(self.catalogue)))
        # **And a hit a run should have recorded, was recorded.** Hits arrived as an
        # aggregate the caller passed in beside the results, so the two could disagree
        # about what a run reached and nothing compared them.
        #
        # Which hits the traces can be expected to carry is the catalogue's own answer:
        # a goal whose evidence lives in a slot record must appear in one or in
        # a validated interrupted-run observation. Trace-
        # judged goals (the monitor's) are exempt by their declared evidence location
        # -- it was `provider == "monitor"`, a string that conflated who enumerated a
        # goal with where its evidence lives. (A third location, `set`, existed for one
        # release and review 10 removed it with the obligation-provider seam.)
        expected_in_traces = {
            goal for goal in self.hits
            if getattr(self.catalogue.get(goal), "evidence",
                       "slot-record") == "slot-record"}
        invented = sorted(expected_in_traces - observed)
        if invented:
            raise Refused(Refusal(
                "evaluation-hit-no-run-recorded",
                "these goals are reported as reached and no slot of any trace recorded "
                "them, and their provider is one that writes its hits into the record, "
                "so nothing in this evaluation is evidence that they were reached",
                hits=invented, observed=sorted(observed)))
        # **And the other direction, which was not checked at all.** Only inflation was:
        # a hit with no record refused, a record with no hit passed. The asymmetry is the
        # dangerous way round. Where a hit is a violation, a recorded one missing from
        # the aggregate is a fault the runs observed and the evaluation does not carry --
        # and `passed` is computed from what the evaluation carries, so the run scores
        # clean over a trace that recorded the violation. Where a hit is coverage, the
        # rate is understated and the goal reads as never reached.
        #
        # Same exemption, for the same reason: a `monitor` provider's goals are judged
        # over the trace rather than written into it, so a record does not carry them.
        #
        # The catalogue question comes first, because a recorded goal nobody enumerated
        # would otherwise be reported as merely unreported -- true, and not the finding.
        uncatalogued = sorted(recorded_names - set(self.catalogue))
        if uncatalogued:
            raise Refused(Refusal(
                "evaluation-recorded-hit-outside-the-catalogue",
                "these goal-bit names occur in a slot record and the catalogue this "
                "evaluation is over does not declare them, so even a false bit would "
                "make the record and catalogue describe different goal sets",
                hits=uncatalogued, declared=sorted(self.catalogue)))
        unreported = sorted(
            {goal for goal in observed
             if getattr(self.catalogue.get(goal), "evidence",
                        "slot-record") == "slot-record"}
            - set(self.hits))
        if unreported:
            raise Refused(Refusal(
                "evaluation-hit-recorded-but-not-reported",
                "a slot of some trace recorded these goals as reached and this "
                "evaluation's hits do not carry them, so what the runs observed and "
                "what the score is computed from disagree",
                hits=unreported, reported=sorted(self.hits)))
        # The fingerprint is read at every phase boundary.  It includes the complete
        # catalogue manifest, so recomputing it repeatedly is expensive for a large but
        # valid elaborated RTL catalogue.  Cache the exact same public-image digest once
        # while constructing this immutable record.
        self._fingerprint = (None if not self.results else hashlib.sha256(
            canonical_json(self.as_evidence_dict()).encode()).hexdigest())
        self._freeze()

    # ── what happened ──

    @property
    def stimuli(self):
        return self.requested_stimuli

    @property
    def unexecuted(self):
        completed = {r.stimulus.identity_bytes for r in self.results}
        return tuple(s for s in self.requested_stimuli if s.identity_bytes not in completed)

    @property
    def failure_witness_blocked(self):
        """Known evidence defects that invalidate even an independent negative."""
        return bool(self.unbound or any(
            failure.origin in (FailureOrigin.REFERENCE, FailureOrigin.VERIFIER)
            for _, failure in self.failures) or
            dict(dict(self.telemetry).get("batch") or {}).get("verified") == VERIFIED_SAMPLE)

    @property
    def failure_witness(self):
        """Index of an independently sufficient, fully attributed negative run.

        A zero accepted-input count in one run is not a counterexample to the
        whole pool's nonempty-input requirement. Nor is an unfinished trace a
        missing-output proof. Resource failures qualify only when their existing
        typed policy explicitly determines score zero.
        """
        if self.failure_witness_blocked:
            return None
        safety = set(self.safety_hits)
        for index, result in enumerate(self.results):
            if result.check_task(self.task):
                continue
            if result.determines_score:
                return index
            if (result.was_compared and (result.trace.completion.scores
                    or result.trace.completion is Completion.CHECKPOINT)
                    and not result.ok):
                return index
            # A reached, catalogue-bound failure is positive evidence even if
            # the source stopped and its remaining slots are padding. This
            # does not make missing outputs or an unobserved tail conclusive.
            if (safety and result.ran and result.observations_eligible
                    and not safety.isdisjoint(result.trace.goal_summary[1])):
                return index
        return None

    @property
    def records(self):
        return tuple(r.trace for r in self.results if r.ran)

    @property
    def infrastructure(self):
        """Failures owned specifically by the execution infrastructure."""
        return tuple(
            (i, r.refused) for i, r in enumerate(self.results)
            if r.refused is not None
            and r.refused.origin is FailureOrigin.INFRASTRUCTURE)

    @property
    def failures(self):
        """Every attributed failure, without collapsing its origin or disposition."""
        return tuple((i, r.refused) for i, r in enumerate(self.results)
                     if r.refused is not None)

    @property
    def candidate_failures(self):
        """Determinate candidate faults, each of which contributes score zero."""
        return tuple(
            (i, failure) for i, failure in self.failures
            if failure.origin is FailureOrigin.CANDIDATE)

    @property
    def no_result_failures(self):
        """Failures that leave the evaluation without a numerical result."""
        return tuple(
            (i, failure) for i, failure in self.failures
            if failure.disposition is FailureDisposition.NO_RESULT)

    @property
    def unrun(self):
        """Stimuli with no trace, separately from runs nothing compared."""
        return tuple((i, r.run_failure) for i, r in enumerate(self.results)
                     if r.run_failure is not None)

    @property
    def uncompared(self):
        """Runs that happened and were not compared, with the reason."""
        return tuple((i, r.comparison_failure) for i, r in enumerate(self.results)
                     if r.comparison_failure is not None)

    @property
    def compared(self):
        return tuple(i for i, r in enumerate(self.results) if r.was_compared)

    @property
    def mismatches(self):
        return tuple((i, r.comparison.why) for i, r in enumerate(self.results)
                     if r.was_compared and not r.ok)

    @property
    def incomplete(self):
        """Runs whose output stream ran out before the reference's did. Diagnostics:
        each is already a mismatch, and this says a longer horizon may be the fix."""
        return tuple((i, "the observed stream ran out before the reference's did")
                     for i, r in enumerate(self.results)
                     if r.was_compared and not r.comparison.complete)

    @property
    def mismatch_count(self):
        """Mismatches **per transaction**, as the appendix counts them."""
        return sum(len(r.faults) for r in self.results)

    @property
    def memory_mismatch_count(self):
        """Unequal final external-memory cells; not output transactions or safety hits."""
        return sum(len(r.comparison.memory_faults)
                   for r in self.results if r.was_compared)

    @property
    def accepted(self):
        """Accepted input-endpoint transfers. The score's count."""
        inputs = {e.name for e in self.task.contract.inputs()}
        return sum(r.trace.accepted_count(inputs) for r in self.results if r.ran)

    @property
    def checked_inputs(self):
        """Input transfers in complete comparisons at retained run boundaries.

        Unlike ``accepted``, this excludes uncompleted reference comparisons
        and arbitrary truncated/padded traces. Mismatching comparisons still
        count as checked work. This is progress, not another passing criterion.
        Trace input counts reuse the same immutable per-endpoint counters.
        """
        inputs = {e.name for e in self.task.contract.inputs()}
        return sum(r.trace.accepted_count(inputs) for r in self.results
                   if r.was_compared and r.comparison.complete
                   and r.trace.completion in (Completion.NORMAL, Completion.CHECKPOINT))

    @property
    def safety_hits(self):
        """Declared safety obligations the run broke, by goal identifier.

        Kept separate from output mismatches so a report preserves why the design
        failed.  Both kinds feed the single success decision.  Which goals are safety
        goals comes from the catalogue, not from the spelling of the identifier.
        """
        return tuple(sorted(
            g for g in self.hits
            if getattr(self.catalogue.get(g), "cls", None) == SAFETY))

    @property
    def complete(self):
        """Whether every stimulus produced either a scoreable run or score-zero fault."""
        return bool(self.results) and not self.unexecuted and all(
            r.determines_score
            or (r.ran and r.was_compared and r.trace.completion.scores)
            for r in self.results)

    @property
    def required_complete(self):
        """Every mandatory test finished; a checked prefix never substitutes for it."""
        completed = {r.stimulus.identity_bytes for r in self.results
                     if r.determines_score or (r.was_compared and r.comparison.complete
                                               and r.trace.completion.scores)}
        return bool(self.required_stimuli) and all(
            s.identity_bytes in completed for s in self.required_stimuli)

    @property
    def passed(self):
        """Agreement and safety over the declared concrete testing scope.

        All mandatory tests must finish. Additional search tests retain their
        evidence and missing members, but cannot weaken that mandatory scope.
        Each scored stimulus ran and was compared against the frozen reference or
        ended in a determinate candidate fault; the accepted-input count is nonzero,
        no transaction or final external-memory cell mismatched, and no declared
        artifact safety obligation was violated. A candidate fault makes this false, while a reference,
        verifier, infrastructure or no-result resource failure leaves no score.
        """
        return (self.required_complete and self.accepted > 0 and self.mismatch_count == 0
                and self.memory_mismatch_count == 0
                and not self.safety_hits
                and not self.no_result_failures
                # **An unverifiable binding is not a passing one.** A trace naming
                # neither the contract it ran under nor the artifact that produced it was
                # recorded in `unbound` and then scored anyway, so a correct-looking run
                # of an unknown design under an unknown contract passed.
                and not self.unbound
                and len(self.compared) == len(self.results))

    @property
    def score_scope(self):
        if self.complete:
            return "requested-pool"
        return "required-baseline" if self.required_complete else "incomplete-baseline"

    def _image(self, *, include_manifest, include_legacy_catalogue):
        out = {"contract": self.task.contract.digest,
               "reference": self.task.reference,
                # **Which candidate this is about.** The persisted result named the
                # contract and the reference and not the design, so two different
                # artifacts under one contract produced the same fingerprint -- and a
                # record that cannot tell them apart cannot be evidence about either.
                "realization": self.task.realization.as_dict(),
                "unbound_trace_fields": list(self.unbound),
                # **The whole of each goal, typed, and its provenance keys with it.**
                #
                # Two rounds of this. First it was `sorted(self.catalogue)` -- the IDs
                # alone -- so a catalogue whose goal changed class from coverage to
                # safety, or was produced under a different policy, kept the fingerprint,
                # and the policy is what a goal count is relative to. Then it was
                # `[str(part) for part in identity]`, which fixed the first and
                # introduced two more: `str()` collapses `1` and `"1"` onto one encoding,
                # and `str()` of the provenance pairs threw away which key each value was
                # under. JSON already distinguishes those, so nothing needs stringifying.
                # ``catalogue`` is retained for report compatibility.  The manifest is
                # the authority: it also carries occurrence identity and the version
                # whose full digest bounded evidence uses.
               "catalogue_digest": self.catalogue.digest(),
               "results": [r.as_dict() for r in self.results],
               "hits": list(self.hits),
               "safety_hits": list(self.safety_hits),
               "accepted": self.accepted,
               "complete": self.complete,
               "required_complete": self.required_complete,
               "budget_exhausted": self.budget_exhausted,
               "score_scope": self.score_scope,
               "requested_stimuli": [s.fingerprint for s in self.requested_stimuli],
               "required_stimuli": [s.fingerprint for s in self.required_stimuli],
               "unexecuted_stimuli": [s.fingerprint for s in self.unexecuted],
               "failure_witness": self.failure_witness,
               "passed": self.passed,
               "mismatch_count": self.mismatch_count,
               "memory_mismatch_count": self.memory_mismatch_count,
               "failures": [
                   [index, failure.as_dict()]
                   for index, failure in self.failures
               ],
               "telemetry": [list(p) for p in self.telemetry]}
        if include_manifest:
            out["catalogue_manifest"] = self.catalogue.as_dict()
        if include_legacy_catalogue:
            out["catalogue"] = catalogue_image(self.catalogue)
        return out

    def as_dict(self):
        """The public compatibility view, including the legacy goal-only list."""
        return self._image(include_manifest=True, include_legacy_catalogue=True)

    def as_evidence_dict(self):
        """The canonical authority evidence, with the manifest represented once."""
        return self._image(include_manifest=True, include_legacy_catalogue=False)

    @property
    def execution_cost(self):
        """A detached public cost report, or None for older/no-timing records.

        Worker durations overlap; only ``wall_seconds`` is elapsed pipeline
        time. These measurements are telemetry, never score or proof evidence.
        """
        frozen = dict(self.telemetry).get("concrete_execution")
        if frozen is None:
            return None
        report = dict(frozen)
        report["work_seconds"] = dict(report["work_seconds"])
        report["run_costs"] = [dict(row) for row in report["run_costs"]]
        if "phases" in report:
            report["phases"] = [dict(row) for row in report["phases"]]
        if "reuse" in report:
            report["reuse"] = dict(report["reuse"])
        if "cpu_admission" in report:
            resources = dict(report["cpu_admission"])
            resources["reservations"] = [dict(row) for row in resources["reservations"]]
            report["cpu_admission"] = resources
        return report

    def as_record_reference(self):
        """The evaluation image under a result that carries the manifest once."""
        return self._image(include_manifest=False, include_legacy_catalogue=False)

    @property
    def fingerprint(self):
        if self._fingerprint is None:
            return hashlib.sha256(
                canonical_json(self.as_evidence_dict()).encode()).hexdigest()
        return self._fingerprint

    def __repr__(self):
        return (f"ConcreteEvaluation({len(self.results)} runs, "
                f"{self.accepted} accepted, passed={self.passed})")


class AnalysisOutcome(Frozen):
    """What the bounded analysis made of a concrete result, independently.

    ``per_goal`` is populated only under `Verdict.CLASSIFIED`. Under any other verdict it
    is empty by construction rather than by convention: an `INCONSISTENT` run has no
    publishable partition, and a record that carried one anyway would be read as a
    partition somebody could use.
    """

    __slots__ = ("_evidence", "_per_goal", "concrete", "verdict", "why")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("AnalysisOutcome is a closed evidence record and cannot be subclassed")

    def __init__(self, verdict, why="", per_goal=None, evidence=None, concrete=None):
        if type(verdict) is not Verdict:
            raise TypeError("an analysis outcome verdict must be the exact Verdict enum")
        self.verdict = verdict
        if type(why) is not str:
            raise TypeError("an analysis outcome explanation must be an exact string")
        self.why = why
        proposed = dict(per_goal or {})
        if proposed:
            raise ValueError(
                "per-goal evidence is derived from the typed evidence record and "
                "cannot be supplied beside it")
        if self.verdict.per_goal:
            if type(evidence) is not ConditionalEvidence:
                raise TypeError(
                    "a classified outcome requires the exact ConditionalEvidence "
                    "record")
            if self.why:
                raise ValueError("a classified outcome has no run-level stop reason")
            goals = evidence.goals
        else:
            if type(evidence) is not AnalysisStopEvidence:
                raise TypeError(
                    "an unclassified outcome requires the exact AnalysisStopEvidence "
                    "record")
            if evidence.kind.value != self.verdict.value:
                raise ValueError("the stop evidence and analysis verdict disagree")
            goals = {}
        if not self.verdict.per_goal and not self.why:
            raise ValueError(
                f"{self.verdict.value} must explain why no classification was issued")
        if not self.verdict.per_goal:
            supported = (evidence.issue.why if evidence.issue is not None
                         else evidence.feasibility.detail)
            if self.why != supported:
                raise ValueError("the outcome explanation and its typed evidence disagree")
        self._per_goal = _outcome_image(goals)
        self._evidence = evidence
        #: The fingerprint of the `ConcreteEvaluation` this is about. Carried so the two
        #: halves of a report cannot be paired across runs.
        if concrete is None:
            concrete = ""
        if type(concrete) is not str:
            raise TypeError(
                "an analysis outcome concrete fingerprint must be an exact string")
        self.concrete = concrete
        if self.concrete:
            if len(self.concrete) != 64 or any(
                    char not in "0123456789abcdef" for char in self.concrete):
                raise ValueError(
                    "an analysis outcome must name the full SHA-256 fingerprint of "
                    "its ConcreteEvaluation")
        elif not (self.verdict is Verdict.REFUSED
                  and evidence.issue is not None
                  and evidence.issue.kind == "analysis-concrete-record-invalid"):
            raise ValueError("an analysis outcome must be bound to a concrete result")
        self._freeze()

    @property
    def per_goal(self):
        return json.loads(self._per_goal)

    @property
    def evidence(self):
        return _outcome_plain(self._evidence.as_dict())

    @property
    def evidence_record(self):
        """The immutable typed record underlying the public JSON view."""
        return self._evidence

    @classmethod
    def from_dict(cls, value):
        """Decode a strict worker/report image into the closed typed record."""

        if type(value) is not dict or set(value) != {
                "verdict", "why", "per_goal", "evidence", "concrete"}:
            raise ValueError(
                "an analysis-outcome image has unknown or missing fields")
        try:
            verdict = Verdict(value["verdict"])
        except ValueError as exc:
            raise ValueError("an analysis-outcome image has an unknown verdict") \
                from exc
        if verdict is Verdict.CLASSIFIED:
            evidence = ConditionalEvidence.from_dict(value["evidence"])
        else:
            from .conditional_evidence import AnalysisStopKind

            evidence = AnalysisStopEvidence.from_dict(
                value["evidence"], kind=AnalysisStopKind(verdict.value))
        got = cls(
            verdict, why=value["why"], evidence=evidence,
            concrete=value["concrete"])
        if got.as_dict() != value:
            raise ValueError("an analysis-outcome image is not canonical")
        return got

    def as_dict(self):
        return {"verdict": self.verdict.value, "why": self.why,
                "per_goal": self.per_goal, "evidence": self.evidence,
                "concrete": self.concrete}

    def __repr__(self):
        return f"AnalysisOutcome({self.verdict.value}, {len(self.per_goal)} goals)"


def _outcome_image(value):
    """Canonical, detached JSON for one public analysis-result field."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise TypeError(
            f"an AnalysisOutcome field has no finite JSON image: {exc}") from None


def _outcome_plain(value):
    """A fresh strict-JSON view of a typed outcome record."""
    return json.loads(_outcome_image(value))
