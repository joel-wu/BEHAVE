"""Comparing what came out against what should have, under a declared policy.

The endpoint declares the policy; replay must not silently permit reordering.
The three policies admit different alignments:

* `ORDERED` -- position by position. The default, and the only one that needs no
  declaration, because it assumes nothing.
* `IDENTIFIER` -- correlated by a field the task declares unique among outstanding
  transactions. Reordering and serial reuse are admitted. The matcher pairs successive
  occurrences of one identifier; the protocol monitor, which sees the accepted and
  emitted timeline, checks that two occurrences were not outstanding together.
* `CONTENT` -- as a multiset. Sound only where the task says the order carries nothing,
  and it is returned with the assumption attached so a report can list it among what the
  verdict rests on rather than among what it established.

Every comparison is over values already normalised to their declared finite types. A
model that answers with a mathematical integer outside its field's range has not produced
a value the contract can express, and masking it here would make the two sides agree
about a number neither of them holds.
"""

from __future__ import annotations

from .contract import Direction, Matching, ResetPolicy
from .failure import Broken, FailureRaised, RunFailure
from .frozen import Frozen
from .refusal import Refusal, Refused
from .stimulus import canonical_json

__all__ = ["LEGACY_PATTERN_TOLERANCE", "Comparison", "compare", "comparison_for",
           "normalise", "observed_transfers_of", "transfers_of"]

#: Only this module may build a `Comparison`. See the class.
#:
#: An internal construction guard, not a security boundary. Submitted models execute
#: in isolation; only the matcher may attach reference/trace identities to a result.
_MATCHER = object()

#: How many faults a serialized comparison shows before eliding, with the elided count
#: stated beside them. A report is not a data dump; a silent truncation is a record two
#: different runs can share.
FAULTS_SHOWN = 20

#: That `normalise` admits both the signed value and the raw pattern for one field.
#:
#: True, and it is a debt rather than a design. It exists because this population's
#: behavior models answer with `to_unsigned(x, w)` on signed ports, which is their
#: convention and not the task's; the right place to absorb it is the adapter that reads
#: those models, so that the core holds one type semantics instead of two. Named so the
#: debt is greppable and so a later change that removes it can find what relied on it.
LEGACY_PATTERN_TOLERANCE = True


class Comparison(Frozen):
    """What a comparison established, and what it rests on.

    **Frozen, and `ok` is derived.** Both were constructible into a contradiction:
    `Comparison(True, faults=(...))` claimed agreement while carrying the faults that
    deny it, and `Comparison(False, faults=())` claimed a failure with nothing behind it.
    A score reads one field and a report reads the other, so a record that can hold both
    answers is a record that will eventually give each reader the one it wants.

    A comparison agrees exactly when it found no fault. Nothing else can make it agree
    and nothing can make it disagree silently.
    """

    __slots__ = (
        "assumptions",
        "cancelled",
        "complete",
        "faults",
        "memory_faults",
        "memory_images",
        "of_reference",
        "of_stimulus",
        "of_trace",
        "policy",
        "reference_source",
        "reordered",
        "why",
    )

    def __init__(self, why="", policy=None, assumptions=(), reordered=0,
                 complete=True, faults=(), of_stimulus="", of_trace="",
                 of_reference="", _token=None, cancelled=(),
                 memory_faults=(), memory_images=(), reference_source=None):
        # **Bound only by the matcher, and only to a run it actually read.**
        #
        # The previous version let the caller state the three digests, through a public
        # `about(stimulus, trace, reference)`. That is not a binding: take the clean
        # comparison of a correct design, call `about` with a *wrong* design's trace
        # fingerprint, and `RunResult` checks the declaration against itself and passes.
        # Reproduced on the previous commit: `passed=True`, `mismatch_count=0`, over a
        # trace whose outputs were never looked at.
        #
        # So a bound comparison can only come out of `comparison_for`, which reads the
        # observed stream **out of the trace it binds to**. There is no way to name a
        # trace this comparison did not consume.
        if (of_stimulus or of_trace or of_reference) and _token is not _MATCHER:
            raise Refused(Refusal(
                "comparison-bound-outside-the-matcher",
                "a comparison says what it is of, and only the matcher that read the "
                "run may say it. A caller-supplied binding is a claim about which run "
                "was compared, checked against itself",
                stimulus=str(of_stimulus), trace=str(of_trace)))
        #: What this comparison is of. Empty on an **unbound** one, which `RunResult`
        #: refuses -- a verdict nobody attached to a run establishes nothing.
        self.of_stimulus = str(of_stimulus)
        self.of_trace = str(of_trace)
        self.of_reference = str(of_reference)
        from .reference_observation import ReferenceSourceObservation

        if reference_source is not None:
            if _token is not _MATCHER:
                raise ValueError("reference observations are supplied by the matcher")
            if type(reference_source) is ReferenceSourceObservation:
                if (reference_source.reference, reference_source.trace) != (
                        self.of_reference, self.of_trace):
                    raise ValueError("reference observation belongs to another comparison")
            elif type(reference_source) is not Refusal:
                raise TypeError("reference observation must retain its evidence or refusal")
        self.reference_source = reference_source
        #: One entry per transaction that disagreed or went unpaired.
        #:
        #: The appendix counts mismatches **per transaction**, and this carried a
        #: boolean and one sentence: expected `[1, 2]` against observed `[9]` is two
        #: mismatches -- one wrong value and one answer that never came -- and could only
        #: be reported as one. A score over a count needs the count.
        #:
        #: Each is `(kind, position, expected, observed)` with `kind` one of `value`,
        #: `missing` or `extra`, so a report can say which of the three a run's
        #: mismatches were without parsing the sentence.
        # **Deeply frozen.** The outer tuple was, and the expected/observed lists inside
        # each fault were not: `comparison.faults[0][2][0] = 99` rewrote the evidence in
        # place. A record whose identity is a digest over its contents has to have
        # contents nobody can change afterwards, all the way down.
        self.faults = tuple(
            (str(kind), position,
             None if expected is None else tuple(expected),
             None if observed is None else tuple(observed))
            for kind, position, expected, observed in faults)
        self.memory_faults = tuple(tuple(row) for row in memory_faults)
        if any(len(row) != 4 or type(row[0]) is not str
               or any(type(v) is not int or v < 0 for v in row[1:])
               for row in self.memory_faults):
            raise TypeError("memory faults name a memory, address and two cell values")
        self.memory_images = tuple(memory_images)
        if self.memory_images:
            from .memory_result import MemoryResult

            if len(self.memory_images) != 2 or any(
                    type(image) is not MemoryResult for image in self.memory_images):
                raise TypeError("memory comparison retains its exact expected and observed images")
        # Each entry is (output endpoint, clearing-reset slot, expected payload).
        # Repeated equal payloads remain repeated cancellations, not a set.
        self.cancelled = tuple((str(endpoint), int(slot), tuple(payload))
                               for endpoint, slot, payload in cancelled)
        #: Whether the horizon let the output stream finish, as **diagnostics only**.
        #:
        #: This was a verdict and it should not have been. An earlier version returned
        #: `ok=True, complete=False` for an observed stream that is a strict prefix of
        #: the expected one, reasoning that a run cannot tell "the design owes more and
        #: the horizon ended" from "the design will never produce them". Appendix A.1
        #: settles it the other way: **every unpaired expected transaction contributes a
        #: mismatch**, and a definition is not something a run gets to soften because it
        #: finds the case ambiguous. Twenty designs were reported as matching on that
        #: reasoning.
        #:
        #: So a short stream is a mismatch, and this says whether a shortage was the
        #: shape of it -- useful for deciding to lengthen a horizon, and never a reason
        #: to call the comparison clean.
        self.complete = bool(complete)
        self.why = str(why)
        self.policy = policy
        #: What this verdict rests on that the comparison did not establish. Empty for
        #: `ORDERED`, which assumes nothing.
        self.assumptions = tuple(assumptions)
        #: How many outputs arrived out of issue order. Telemetry under a policy that
        #: admits reordering, and always zero under `ORDERED`, where it would be a
        #: mismatch instead.
        self.reordered = int(reordered)
        self._freeze()

    @property
    def ok(self):
        """Whether this comparison found nothing wrong. Derived, never assigned."""
        return not self.faults and not self.memory_faults

    @property
    def faults_digest(self):
        """Over **all** the faults, so a truncated display cannot collide."""
        import hashlib

        return hashlib.sha256(canonical_json(
            [_fault_json(f) for f in self.faults]).encode()).hexdigest()

    def as_dict(self):
        # **Every fault, and a marker when there are more than are shown.** This kept
        # the first twenty silently, so two runs with different hundred-fault sets
        # serialised identically and an evidence fingerprint over the record could not
        # tell them apart. The cap stays -- a report is not a data dump -- but the count
        # is exact and the elision is stated.
        import hashlib

        cancellations = [[endpoint, slot, list(payload)]
                         for endpoint, slot, payload in self.cancelled]
        return {"ok": self.ok, "complete": self.complete, "why": self.why,
                "mismatches": len(self.faults),
                "memory_mismatches": len(self.memory_faults),
                "memory_faults": [list(row) for row in self.memory_faults[:FAULTS_SHOWN]],
                "memory_faults_elided": max(0, len(self.memory_faults) - FAULTS_SHOWN),
                "memory_faults_digest": hashlib.sha256(canonical_json(
                    self.memory_faults).encode()).hexdigest(),
                "memory_images": [image.digest for image in self.memory_images],
                "faults": [_fault_json(f) for f in self.faults[:FAULTS_SHOWN]],
                "faults_elided": max(0, len(self.faults) - FAULTS_SHOWN),
                # **A digest over every fault**, not only the ones shown. With the first
                # twenty printed and a count beside them, two runs sharing a prefix and a
                # total but differing at the twenty-first serialised identically -- so
                # an evidence fingerprint could not tell them apart, which is the whole
                # job of an evidence fingerprint. Display truncates; evidence does not.
                "faults_digest": self.faults_digest,
                "cancelled": len(cancellations),
                "cancellations": cancellations[:FAULTS_SHOWN],
                "cancellations_elided": max(0, len(cancellations) - FAULTS_SHOWN),
                "cancellations_digest": hashlib.sha256(
                    canonical_json(cancellations).encode()).hexdigest(),
                "policy": getattr(self.policy, "value", self.policy),
                "assumptions": list(self.assumptions), "reordered": self.reordered,
                "of": {"stimulus": self.of_stimulus, "trace": self.of_trace,
                       "reference": self.of_reference},
                "reference_source": (None if self.reference_source is None
                                     else self.reference_source.as_dict())}

    @property
    def bound(self):
        """Whether this comparison says which run it is of."""
        return bool(self.of_stimulus and self.of_trace and self.of_reference)

    def __repr__(self):
        return f"Comparison({'ok' if self.ok else self.why!r})"


def _fault_json(fault):
    kind, position, expected, observed = fault
    return [kind, position,
            None if expected is None else list(expected),
            None if observed is None else list(observed)]


def normalise(endpoint, payload):
    """One payload as the finite **bit patterns** the contract declares, or a refusal.

    Canonical, so that a model computing in unsigned patterns and a simulator reporting
    signed integers are compared as the bits they both put on the wire rather than as
    two numbers that happen to disagree about a sign.

    A reference model computes in Python integers, which are unbounded and signed. The
    contract says each field is so many bits read one way, and a value outside that is
    not a value the endpoint can carry. Two things this refuses to do: mask it, which
    would make the two sides agree about a number neither holds, and pass it through,
    which would compare a Python integer against a bit pattern.

    T3-02 puts it as: normalize every compared field to its declared finite type, or
    refuse an out-of-range model value -- and do not mask only the signed outputs while
    giving the unsigned ones mathematical-integer semantics, which is what happens when
    the masking lives at each call site.
    """
    if len(payload) != len(endpoint.fields):
        raise Refused(Refusal(
            "compare-payload-arity",
            "this payload has a different number of values than the endpoint has "
            "fields, so there is no field for one of them to be compared as",
            endpoint=endpoint.name, values=len(payload),
            fields=[f.name for f in endpoint.fields]))
    out = []
    for field, value in zip(endpoint.fields, payload):
        if field.type.kind != "integer":
            from .physical_value import PhysicalValueError, pack_value

            try:
                out.append(pack_value(field.type, value))
            except PhysicalValueError as exc:
                raise Refused(Refusal(
                    "compare-value-outside-type",
                    "this logical value does not inhabit the field's declared "
                    "recursive type",
                    endpoint=endpoint.name, field=field.name,
                    value=repr(value), reason=exc.code)) from None
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise Refused(Refusal(
                "compare-value-not-an-integer",
                "this field's value is not an integer, so it is not a bit pattern the "
                "declared finite type can hold",
                endpoint=endpoint.name, field=field.name, got=repr(value)))
        low, high = field.type.bounds
        width = field.type.width
        # **To the bit pattern**, which is what the endpoint carries.
        #
        # A field is `w` bits and its signedness is how those bits are *read*. A model
        # that answers `200` on a `signed [7:0]` port has given the pattern for -56, and
        # 41 designs of this population do exactly that -- they compute with
        # `to_unsigned(x, 8)` and let the port's declaration say how to read it. Holding
        # them to the signed interval refused every one, which is a statement about the
        # convention rather than about the design.
        #
        # So both readings are admitted and canonicalised to one, and a value outside
        # **both** is still refused: that is a number the wire cannot carry at all, and
        # masking it would make the two sides agree about something neither holds.
        #
        # **Where this belongs in the end is the legacy adapter**, not here. Admitting
        # two readings means the core carries two type semantics at once -- "a signed
        # value" and "a raw pattern" -- and a later reader cannot tell which one a
        # number in a record is. A `to_unsigned` model is a fact about how this
        # population's behavior models were written; a contract that declared its
        # models' convention would let the adapter normalise on the way in and leave one
        # semantics in here. `LEGACY_PATTERN_TOLERANCE` is the name of that debt.
        if not (low <= value <= high) and not (0 <= value < (1 << width)):
            raise Refused(Refusal(
                "compare-value-outside-type",
                "this value is outside the field's declared type read either way, so it "
                "is not a pattern the endpoint can carry; masking it would make the two "
                "sides agree about a number neither of them holds",
                endpoint=endpoint.name, field=field.name, value=value,
                signed_bounds=[low, high], width=width))
        out.append(value & ((1 << width) - 1))
    return tuple(out)


def transfers_of(trace, endpoint):
    """The payloads that crossed ``endpoint``, in the order they crossed.

    Here rather than in `api`, because `comparison_for` has to derive the observed
    stream itself: a comparison that took `observed` from its caller is a comparison of
    whatever the caller handed over, and the binding it then carries names a trace it
    may never have read.
    """
    return [p for r in trace.records for n, p in r.accepted
            if n == endpoint and p is not None]


def observed_transfers_of(task, trace, endpoint):
    """The artifact's logical output stream for one declared endpoint.

    Signal-level artifacts report a transfer in ``accepted`` after their adapter closes
    a handshake.  A Behavior procedure has no pin handshake and one call may emit a
    finite batch, retained under ``SlotRecord.outputs``.  This task-owned projection is
    the only difference between the two backends; matching policy and verdict binding
    remain shared.
    """

    return [payload for slot, record in enumerate(trace.records)
            for payload in observed_in_record(
                record, endpoint, behavior=task.realization.kind == "behavior", slot=slot)]


def observed_in_record(record, endpoint, *, behavior, slot=None):
    """The same logical output projection for matching and reset-boundary offsets."""
    if not behavior:
        return tuple(payload for name, payload in record.accepted
                     if name == endpoint and payload is not None)
    batch = dict(record.outputs).get(endpoint, ())
    if type(batch) is not tuple:
        raise Refused(Refusal(
            "behavior-output-batch-malformed",
            "a Behavior slot output is a finite transaction batch",
            slot=slot, endpoint=endpoint, got=type(batch).__name__))
    for index, payload in enumerate(batch):
        if type(payload) is not tuple:
            raise Refused(Refusal(
                "behavior-output-payload-malformed",
                "a Behavior output batch contains transaction payloads",
                slot=slot, endpoint=endpoint, transaction=index,
                got=type(payload).__name__))
    return batch


def transaction_samples(endpoints, record, *, behavior, slot=None, outputs=None):
    """The shared accepted-input/emitted-output projection for task observers.

    ``outputs`` is used only by the reference observer for the reference's own
    freshly produced batches. It never substitutes candidate predictions.
    """
    accepted = dict(record.accepted)
    samples = {}
    for endpoint in endpoints:
        if endpoint.direction is Direction.IN:
            payload = accepted.get(endpoint.name)
            samples[endpoint.name] = () if payload is None else (payload,)
        elif outputs is not None:
            samples[endpoint.name] = outputs.get(endpoint.name, ())
        else:
            samples[endpoint.name] = observed_in_record(record, endpoint.name, behavior=behavior, slot=slot)
    return samples


def comparison_for(task, reference, trace):
    """Compare output streams and final external memory from one reference run.

    A fresh model from the frozen reference consumes the artifact's accepted
    input trace. Observations come from that same trace; callers cannot supply
    either stream or substitute its task, reference or artifact identity.
    """
    # Local import: core.reference uses this module's normalization helpers.
    from .reference import LoadedReference, expected_run_of
    from .reference_progress import ReferenceProgress

    # Exact type prevents subclasses from replacing reference construction while
    # retaining the admitted bundle's identity.
    if type(reference) is not LoadedReference:
        raise Refused(Refusal(
            "comparison-reference-not-a-bundle",
            "the matcher runs the reference itself, so it needs the bundle its loader "
            "froze -- not a subclass of it, not a live model, and not an expected stream "
            "a caller computed. None of those can be checked against the digest the task "
            "admitted",
            got=type(reference).__name__))
    stimulus = trace.stimulus
    progress = ReferenceProgress(task.contract, behavior=task.realization.kind == "behavior")
    try:
        expected, expected_memory = expected_run_of(task, reference, trace, progress=progress)
    except FailureRaised:
        raise
    except Refused as exc:
        raise FailureRaised(RunFailure.reference(
            "reference-execution", exc.refusal)) from None
    except Exception as exc:  # noqa: BLE001 - isolated reference boundary
        details = {"error": type(exc).__name__, "message": str(exc)}
        if getattr(exc, "__notes__", None):
            details["notes"] = list(exc.__notes__)
        if type(exc) is Broken:
            details["detail"] = exc.detail
        if type(exc) is Broken and exc.code:
            raise FailureRaised(RunFailure.reference(
                "reference-execution", Refusal(
                    exc.code, "the frozen reference's isolation boundary ended the run",
                    **details))) from None
        raise FailureRaised(RunFailure.reference(
            "reference-execution",
            Refusal(
                "reference-runtime-failed",
                "the frozen reference raised while deriving expected transactions",
                **details))) from None
    progress.observe_transactions(trace, expected)
    outputs = task.contract.outputs()
    compared = []
    for endpoint in outputs:
        try:
            observed = observed_transfers_of(task, trace, endpoint.name)
            got_stream = [normalise(endpoint, payload) for payload in observed]
        except FailureRaised:
            raise
        except Refused as exc:
            raise FailureRaised(RunFailure.candidate(
                "candidate-output", exc.refusal)) from None
        except Exception as exc:  # noqa: BLE001 - candidate trace projection
            raise FailureRaised(RunFailure.candidate(
                "candidate-output",
                Refusal(
                    "candidate-output-malformed",
                    "the candidate trace cannot be projected to declared output "
                    "transactions",
                    endpoint=endpoint.name,
                    error=type(exc).__name__))) from None
        try:
            want_stream = [
                normalise(endpoint, payload)
                for payload in expected.get(endpoint.name, ())
            ]
        except FailureRaised:
            raise
        except Refused as exc:
            raise FailureRaised(RunFailure.reference(
                "reference-output", exc.refusal)) from None
        except Exception as exc:  # noqa: BLE001 - reference value boundary
            raise FailureRaised(RunFailure.reference(
                "reference-output",
                Refusal(
                    "reference-output-malformed",
                    "the reference produced no stream in the declared output type",
                    endpoint=endpoint.name,
                    error=type(exc).__name__))) from None
        try:
            result = _match_epochs(
                endpoint, want_stream, got_stream, progress.cuts.get(endpoint.name, ()))
        except Refused as exc:
            raise FailureRaised(RunFailure.verifier(
                "output-matching", exc.refusal)) from None
        compared.append((endpoint, result))
    if len(compared) == 1:
        got = compared[0][1]
        complete = got.complete
    elif not compared:
        got, complete = Comparison(), True
    else:
        # Qualify faults when several output streams coexist.
        faults, assumptions, reasons, cancelled = [], set(), [], []
        reordered, complete = 0, True
        for endpoint, result in compared:
            faults.extend(
                (kind, f"{endpoint.name}:{position}", wanted, observed)
                for kind, position, wanted, observed in result.faults)
            assumptions.update(result.assumptions)
            reordered += result.reordered
            complete = complete and result.complete
            cancelled.extend(result.cancelled)
            if result.why:
                reasons.append(f"{endpoint.name}: {result.why}")
        policies = tuple(endpoint.matching.value for endpoint, _ in compared)
        got = Comparison(
            "; ".join(reasons), policy=policies,
            assumptions=tuple(sorted(assumptions)), reordered=reordered,
            complete=complete, faults=tuple(faults), cancelled=cancelled)
    memory_mismatches, memory_images = (), ()
    if task.contract.memories:
        from .memory_result import memory_faults, trace_memory_result

        try:
            observed_memory = trace_memory_result(
                task.contract, trace, behavior=task.realization.kind == "behavior")
            memory_mismatches = memory_faults(
                task.contract.memories, expected_memory, observed_memory)
            memory_images = (expected_memory, observed_memory)
        except (KeyError, TypeError, ValueError, Refused) as exc:
            raise FailureRaised(RunFailure.verifier(
                "final-memory-comparison", Refusal(
                    "memory-result-unavailable",
                    "the replay lacks a valid final memory image; output agreement "
                    "alone cannot establish this task's result",
                    error=type(exc).__name__))) from None
    why = got.why
    if memory_mismatches:
        why = "; ".join(filter(None, (why, f"{len(memory_mismatches)} final memory cells differ")))
    return Comparison(
        why, got.policy, got.assumptions, got.reordered,
        complete=complete, faults=got.faults,
        of_stimulus=stimulus.fingerprint, of_trace=trace.fingerprint,
        of_reference=str(task.reference), _token=_MATCHER, cancelled=got.cancelled,
        memory_faults=memory_mismatches, memory_images=memory_images,
        reference_source=progress.source_observation)


def _match_epochs(endpoint, want, got, cuts):
    """Only unmatched expectations may be canceled at a declared clearing reset.

    Each epoch uses the existing ordered/identifier/content matcher. Wrong values
    and extra outputs are never erased; the final epoch still owes every reply.
    Boundary offsets come from reference execution and observed transfer timing,
    never from matching values or a candidate-supplied cancellation count.
    """
    faults, cancelled, assumptions = [], [], set()
    reordered, want_start, got_start = 0, 0, 0
    for reset_slot, want_end, got_end, policy in cuts:
        part = _match_normalised(endpoint, want[want_start:want_end], got[got_start:got_end])
        missing = [fault for fault in part.faults if fault[0] == "missing"]
        if missing and policy is not ResetPolicy.CLEARS:
            raise Refused(Refusal(
                "reference-reset-policy-undeclared",
                "reset interrupts unmatched responses without one declared "
                "cancellation policy for this output's input group",
                endpoint=endpoint.name, slot=reset_slot, unmatched=len(missing)))
        cancelled.extend((endpoint.name, reset_slot, payload)
                         for _kind, _position, payload, _observed in missing)
        faults.extend((kind, f"before-reset:{reset_slot}:{position}", expected, observed)
                      for kind, position, expected, observed in part.faults
                      if kind != "missing")
        assumptions.update(part.assumptions)
        reordered += part.reordered
        want_start, got_start = want_end, got_end
    last = _match_normalised(endpoint, want[want_start:], got[got_start:])
    faults.extend((kind, f"after-reset:{cuts[-1][0]}:{position}" if cuts else position,
                   expected, observed)
                  for kind, position, expected, observed in last.faults)
    assumptions.update(last.assumptions)
    return _summarise(
        endpoint, faults, assumptions=tuple(sorted(assumptions)),
        reordered=reordered + last.reordered, cancelled=cancelled,
        complete=len(got) - got_start >= len(want) - want_start)


def compare(endpoint, expected, observed):
    """The two output streams, under ``endpoint``'s declared matching policy.

    Returns an **unbound** comparison: it says what the two streams did and not which
    run they came from, because it was not given one. `RunResult` refuses an unbound
    comparison, so this is the matcher's own answer and never a verdict about a run.
    """
    want = [normalise(endpoint, p) for p in expected]
    got = [normalise(endpoint, p) for p in observed]

    return _match_normalised(endpoint, want, got)


def _match_normalised(endpoint, want, got):
    """Match two streams already converted to the endpoint's finite type."""

    policy = endpoint.matching

    if policy is Matching.ORDERED:
        return _ordered(endpoint, want, got)
    if policy is Matching.CONTENT:
        return _content(endpoint, want, got)
    if policy is Matching.IDENTIFIER:
        return _identifier(endpoint, want, got)
    raise Refused(Refusal(
        "compare-policy-unknown",
        "this endpoint declares a matching policy this build does not implement, and "
        "comparing it under another would apply a rule the task did not declare",
        endpoint=endpoint.name, matching=getattr(policy, "value", policy)))


def _pairing_faults(want, got):
    """Unpaired transactions on either side, as one fault each.

    `missing` is a transaction the reference issues and nothing answered; `extra` is one
    that arrived and the reference does not issue. Counting them per transaction rather
    than as one "the lengths differ" is what the appendix asks for and what a score over
    a mismatch count needs.
    """
    out = []
    for i in range(len(got), len(want)):
        out.append(("missing", i, list(want[i]), None))
    for i in range(len(want), len(got)):
        out.append(("extra", i, None, list(got[i])))
    return out


def _summarise(endpoint, faults, **kw):
    if not faults:
        return Comparison(policy=endpoint.matching, **kw)
    kinds = {}
    for kind, *_ in faults:
        kinds[kind] = kinds.get(kind, 0) + 1
    first = faults[0]
    detail = (f"output {first[1]}: {first[3]} where the reference gives {first[2]}"
              if first[0] == "value" else
              f"output {first[1]}: the reference issues {first[2]} and nothing "
              f"answered it" if first[0] == "missing" else
              f"output {first[1]}: {first[3]} arrived and the reference issues nothing")
    counts = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    return Comparison(f"{len(faults)} mismatches ({counts}); {detail}",
                      endpoint.matching, faults=faults, **kw)


def _ordered(endpoint, want, got):
    faults = [("value", i, list(a), list(b))
              for i, (a, b) in enumerate(zip(want, got)) if a != b]
    return _summarise(endpoint, faults + _pairing_faults(want, got))


def _content(endpoint, want, got):
    from collections import Counter

    left, right = Counter(want), Counter(got)
    faults = [("missing", i, list(v), None)
              for i, v in enumerate(sorted((left - right).elements()))]
    faults += [("extra", i, None, list(v))
               for i, v in enumerate(sorted((right - left).elements()))]
    reordered = sum(1 for a, b in zip(want, got) if a != b)
    return _summarise(
        endpoint, faults, reordered=reordered,
        assumptions=((
            f"the order of {endpoint.name}'s outputs carries nothing, which "
            "this comparison assumes and does not establish"
        ),))


def _identifier(endpoint, want, got):
    from collections import defaultdict, deque

    names = [f.name for f in endpoint.fields]
    index = names.index(endpoint.identifier)

    # Output histories do not contain the accepted-input timeline, so they cannot say
    # whether two equal identifiers were ever outstanding together.  Preserve only the
    # fact they *do* contain: occurrences of one identifier complete in issue order when
    # the identifier is unique among outstanding transactions.  The protocol monitor
    # checks that premise on the timed trace.
    expected = defaultdict(deque)
    for position, payload in enumerate(want):
        expected[payload[index]].append((position, payload))

    matched_positions, faults = [], []
    for arrived, payload in enumerate(got):
        key = payload[index]
        queue = expected.get(key)
        if not queue:
            faults.append(("extra", arrived, None, list(payload)))
            continue
        position, want_payload = queue.popleft()
        matched_positions.append(position)
        if payload != want_payload:
            faults.append(("value", position, list(want_payload), list(payload)))
    for queue in expected.values():
        faults.extend(("missing", position, list(payload), None)
                      for position, payload in queue)

    reordered = sum(position != issued
                    for issued, position in enumerate(matched_positions))
    return _summarise(
        endpoint, sorted(faults, key=lambda f: f[1]), reordered=reordered,
        assumptions=((
            f"{endpoint.identifier} is unique among {endpoint.name}'s "
            "outstanding transactions, which the task declares and this "
            "comparison relies on; the timed protocol monitor checks it"
        ),))
