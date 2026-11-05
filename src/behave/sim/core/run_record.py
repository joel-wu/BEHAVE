"""Evidence for one stimulus, including unsuccessful runs.

RunResult binds the stimulus, trace or run failure, and comparison or comparison
failure in one immutable record. Task, artifact and reference identities prevent
evidence from being attributed to a different evaluation.
"""

from __future__ import annotations

from .failure import RunFailure
from .frozen import Frozen
from .refusal import Refusal, Refused
from .stimulus import contract_of_domain

__all__ = ["RunResult"]

# Replay provenance is record data shared by execution and verdict policy.
PER_PROCESS = "one-process-per-stimulus"
ONE_PROCESS_BATCH = "one-process-for-all"
ONE_AT_A_TIME = "one-at-a-time"
VERIFIED_NOT_NEEDED = "not-needed"
VERIFIED_EVERY = "every-stimulus"
VERIFIED_SAMPLE = "a-sample"

#: Only the three named constructors may build one. See `RunResult`.
_AUTHORISED = object()


class RunResult(Frozen):
    """What one stimulus produced, and what comparing it established.

    **A closed structure with three ways in, and no fourth.** The previous version took
    `(stimulus, trace=None, comparison=None, reference="", refused="")` and checked a
    couple of pairs, which left the interesting combinations constructible:
    `RunResult(stimulus_A, trace=trace_of_B, comparison=Comparison())` is a clean pass
    assembled out of another run's trace and a comparison of nothing, and
    `RunResult(s, trace=t, comparison=c, refused="failed")` both compared and did not.
    A record whose fields can contradict each other is not evidence about anything.

    So the constructor is private and the three states are named:

    * `failed_to_run(stimulus, failure)` -- no trace, with typed attribution.
    * `not_compared(stimulus, trace, failure)` -- a trace, and the attributed reason
      nothing was compared.
    * `compared(stimulus, trace, comparison)` -- both, with the comparison **bound** to
      this stimulus, this trace and the reference it was made against.

    The binding is the third one's whole point: the comparison carries the digests of
    what it is of, and they are checked here against what this record holds. A
    comparison of some other run can no longer be filed under this one.
    """

    __slots__ = (
        "comparison",
        "comparison_failure",
        "observations_eligible",
        "reference",
        "run_failure",
        "stimulus",
        "trace",
    )

    def __init__(self, stimulus, trace, comparison, reference,
                 run_failure, comparison_failure, observations_eligible, _token=None):
        if _token is not _AUTHORISED:
            raise Refused(Refusal(
                "run-result-not-from-a-constructor",
                "a run result is one of three states -- did not run, ran and was not "
                "compared, ran and was compared -- and assembling the fields directly "
                "is how a trace of one run came to sit beside a comparison of another. "
                "Use `failed_to_run`, `not_compared` or `compared`",
                stimulus=getattr(stimulus, "fingerprint", "")))
        self.stimulus = stimulus
        self.trace = trace
        self.comparison = comparison
        self.reference = str(reference)
        #: Why there is no trace. ``None`` when there is one.
        self.run_failure = run_failure
        #: Why there is no comparison, given a trace. ``None`` when compared.
        self.comparison_failure = comparison_failure
        if type(observations_eligible) is not bool:
            raise TypeError("observation eligibility is an exact Boolean")
        self.observations_eligible = observations_eligible
        for name, value in (("run", run_failure),
                            ("comparison", comparison_failure)):
            if value is not None and type(value) is not RunFailure:
                raise TypeError(
                    f"a {name} failure must be the exact RunFailure record")
            if value is not None and value.observed is not None \
                    and value.observed.stimulus != stimulus.fingerprint:
                raise ValueError("failure observations belong to another stimulus")
        self._check()
        self._freeze()

    # ── the three states ──

    @classmethod
    def failed_to_run(cls, stimulus, failure):
        """This side could not produce a trace; ``failure`` fixes what that means."""
        if type(failure) is not RunFailure:
            raise TypeError("failed_to_run needs an exact RunFailure")
        return cls(stimulus, None, None, "", failure, None, failure.observed is not None,
                   _token=_AUTHORISED)

    @classmethod
    def not_compared(cls, stimulus, trace, failure, *, observations_eligible=True):
        """The run happened; an attributed failure prevented comparison.

        **Not the same as a comparison that found nothing.** The two used to be one
        `None` in a dictionary, and a run nobody compared reads as a run that agreed.
        Monitor/domain failures set ``observations_eligible=False`` at their
        origin. The raw trace remains diagnostic evidence, not a valid goal hit.
        """
        if trace is None:
            raise Refused(Refusal(
                "run-result-no-trace-to-not-compare",
                "this state is for a run that happened and was not compared; a run "
                "that did not happen is `failed_to_run`",
                stimulus=stimulus.fingerprint))
        if type(failure) is not RunFailure:
            raise TypeError("not_compared needs an exact RunFailure")
        return cls(stimulus, trace, None, "", None, failure, observations_eligible,
                   _token=_AUTHORISED)

    @classmethod
    def compared(cls, stimulus, trace, comparison):
        """The run happened and was compared. The comparison says what it is of.

        **The comparison must be one.** This read `.of_stimulus`, `.of_trace`, `.bound`
        and `.ok` off whatever it was handed, so an object with those four attributes was
        a verdict: a dozen lines of duck type gave `passed=True, mismatch_count=0` over a
        design whose outputs disagreed. Every guarantee here rests on the matcher having
        produced the thing -- the token, the derivation of both streams, the freezing --
        and an interface check tests for none of it.

        Exact type, not `isinstance`. A subclass inherits the checks and overrides the
        answers, which is the same hole one level up.
        """
        from .compare import Comparison

        if type(comparison) is not Comparison:
            raise Refused(Refusal(
                "run-result-comparison-not-a-comparison",
                "this is not a `Comparison` the matcher produced, so the binding, the "
                "derivation of both streams and the freezing that a verdict rests on "
                "have not happened -- only an object that answers the same questions",
                stimulus=stimulus.fingerprint,
                got=type(comparison).__name__))
        if trace is None or comparison is None:
            raise Refused(Refusal(
                "run-result-compared-without-both",
                "a compared result needs the trace it is of and the comparison it "
                "established",
                stimulus=stimulus.fingerprint))
        return cls(stimulus, trace, comparison, comparison.of_reference, None, None, True,
                   _token=_AUTHORISED)

    def _check(self):
        """Every field against every other. One place, run on the way in."""
        if self.trace is None:
            if self.run_failure is None:
                raise Refused(Refusal(
                    "run-result-silent-failure",
                    "this run produced no trace and gives no reason",
                    stimulus=self.stimulus.fingerprint))
            if self.comparison is not None or self.comparison_failure is not None \
                    or self.reference:
                raise Refused(Refusal(
                    "run-result-failure-state-incoherent",
                    "a run that produced no trace cannot also carry comparison state",
                    stimulus=self.stimulus.fingerprint))
            return
        if self.run_failure is not None:
            raise Refused(Refusal(
                "run-result-failure-state-incoherent",
                "a run cannot carry both a trace and a run failure",
                stimulus=self.stimulus.fingerprint))
        # **The trace is of this stimulus**, whether or not anything compared it. A run
        # record holding stimulus A beside a trace of B is wrong before any comparison
        # is involved, and checking it only on the compared branch left the uncompared
        # one able to say a stimulus was run when a different one was.
        trace_request = self.trace.request or self.trace.stimulus
        if trace_request.fingerprint != self.stimulus.fingerprint:
            raise Refused(Refusal(
                "run-result-trace-of-another-stimulus",
                "this trace records a run of a different stimulus than the one this "
                "result is filed under",
                stimulus=self.stimulus.fingerprint,
                trace_of=self.trace.stimulus.fingerprint))
        if self.comparison is None:
            if self.comparison_failure is None or self.reference:
                raise Refused(Refusal(
                    "run-result-comparison-state-incoherent",
                    "an uncompared trace needs exactly one attributed comparison "
                    "failure",
                    stimulus=self.stimulus.fingerprint))
            return
        if self.comparison_failure is not None:
            raise Refused(Refusal(
                "run-result-comparison-state-incoherent",
                "a compared trace cannot also carry a comparison failure",
                stimulus=self.stimulus.fingerprint))
        # ── the comparison is of **this** run ──
        #
        # Three digests, each checked against what this record actually holds. Without
        # them a comparison is a free-floating verdict and the record around it is
        # decoration: the old check was `entry.check(..., entry.reference)`, an entry's
        # reference against its own, which passes for every possible value.
        wrong = {}
        if not self.comparison.bound:
            wrong["binding"] = ["a comparison of this run", "an unbound one"]
        if self.comparison.of_stimulus != self.trace.stimulus.fingerprint:
            wrong["stimulus"] = [self.trace.stimulus.fingerprint[:12],
                                 self.comparison.of_stimulus[:12]]
        if self.comparison.of_trace != self.trace.fingerprint:
            wrong["trace"] = [self.trace.fingerprint[:12],
                              self.comparison.of_trace[:12]]
        if wrong:
            raise Refused(Refusal(
                "run-result-comparison-of-another-run",
                "this comparison states that it is of a different stimulus, a "
                "different trace or no reference at all, so filing it here would "
                "attribute what it established to a run it is not about",
                stimulus=self.stimulus.fingerprint, disagrees=wrong))

    # ── what it says ──

    @property
    def ran(self):
        return self.trace is not None

    @property
    def was_compared(self):
        return self.comparison is not None

    @property
    def refused(self):
        """Whichever of the two failures happened, or ``None``. For a reader that only
        wants to know whether this stimulus established anything."""
        return self.run_failure or self.comparison_failure

    @property
    def determines_score(self):
        """Whether this row is a determinate candidate failure (score zero)."""
        return self.refused is not None and self.refused.determines_score

    @property
    def ok(self):
        """Agreement, and only where it was actually established."""
        return self.was_compared and self.comparison.ok

    @property
    def faults(self):
        return self.comparison.faults if self.was_compared else ()

    def check_task(self, task):
        """Refuse a result whose trace was not produced by **this task's** run.

        Three bindings, and the record carried none of them. A trace says what stimulus
        it is of, and used to say nothing about which contract it ran under or which
        artifact produced it -- so a trace built with `domain="another-domain"` filed
        under this task passed, and admitting artifact A while running artifact B and
        filing B's trace under A passed too. Every refusal admission made was then about
        something that was not under test.

        An empty binding on the trace is **unverifiable**, not agreement: a runner that
        cannot say which artifact it drove leaves the check unable to run, and that is
        reported rather than waved through.
        """
        if not self.ran:
            return ()
        wrong = {}
        # **The contract half of the domain.** A record's domain is the interface *and*
        # the run policy now (`core/stimulus.trace_domain`), because two runs of one
        # interface at different horizons under different back-pressure are not one
        # domain. The binding this refusal is about is still the interface, and the
        # identity keeps it as its prefix so this check reads exactly what it did before.
        if self.trace.domain \
                and contract_of_domain(self.trace.domain) != task.contract.digest:
            wrong["domain"] = [task.contract.digest[:12], self.trace.domain[:12]]
        declared = str(getattr(task.realization, "identity", "") or "")
        if self.trace.artifact and declared and self.trace.artifact != declared:
            wrong["artifact"] = [declared, self.trace.artifact]
        if wrong:
            raise Refused(Refusal(
                "run-result-trace-of-another-run",
                "this trace was not produced by the task this result is filed under: "
                "it names a different contract, or a different artifact than the one "
                "admission checked",
                stimulus=self.stimulus.fingerprint, disagrees=wrong))
        unbound = sorted(
            name for name, value in (("domain", self.trace.domain),
                                     ("artifact", self.trace.artifact))
            if not value)
        return unbound

    def check(self, reference):
        """Refuse a result whose comparison is not against the reference the task froze.

        Takes the expected digest rather than reading it off the entry. The internal
        binding above establishes that the comparison is of *this* run; this
        establishes that the run is against the reference the *task* declared, which is
        a different statement and the one the score rests on.
        """
        if self.was_compared and self.reference != str(reference):
            raise Refused(Refusal(
                "run-result-wrong-reference",
                "this comparison was made against a different reference than the task "
                "froze, so what it establishes is not about the model the task declares",
                frozen=str(reference), used=self.reference))
        return self

    def as_dict(self):
        return {"stimulus": self.stimulus.fingerprint,
                "trace": self.trace.fingerprint if self.trace is not None else None,
                "observations_eligible": self.observations_eligible,
                "reference": self.reference,
                "run_failure": (None if self.run_failure is None
                                else self.run_failure.as_dict()),
                "comparison_failure": (
                    None if self.comparison_failure is None
                    else self.comparison_failure.as_dict()),
                "comparison": (self.comparison.as_dict() if self.was_compared
                               else None)}

    def __repr__(self):
        state = ("not run" if not self.ran
                 else "not compared" if not self.was_compared
                 else "ok" if self.ok else "mismatch")
        return f"RunResult({state}, {self.stimulus.fingerprint[:8]})"
