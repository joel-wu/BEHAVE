"""The shared Behavior/RTL interface for bounded analysis.

Frontends declare goals, replay requests, query reachability, audit independent
readings and check domain feasibility. The closure coordinator consumes these
operations without knowing the source language or symbolic representation.
``BoundedInstance`` fixes the domain and validates provider witnesses against
their requests. Unavailable evidence remains distinct from disagreement and
from a proof of unreachability.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from .audit import AuditCheck, AuditedWitness, WitnessInconsistent
from .catalogue import CatalogueManifest, DomainManifest
from .domain import TraceDomain
from .frozen import Frozen
from .query import FeasibilityCall, QueryCall, _FeasibilityDetailError
from .query_telemetry import AnalysisCacheBinding
from .refusal import Refused
from .record_value import (
    FrozenValueMap,
    freeze_record_value,
    record_pairs,
    same_record_value,
    thaw_record_value,
)

__all__ = [
    "BoundedInstance",
    "Feasible",
    "Instance",
    "Replay",
    "StepOutput",
    "Tagged",
    "Verdict",
    "endpoint_batch_output",
    "endpoint_batch_rows",
]


def endpoint_batch_output(batch):
    """Encode one slot's endpoint-tagged transactions without losing sparsity.

    ``StepOutput`` predates independent endpoint streams: its buffer columns are
    present in every returned row.  Building one column per endpoint and padding the
    other rows with ``None`` therefore changes a sparse batch such as ``a, b`` into
    two dense records that each appear to mention both endpoints.  The atomic
    Behavior and RTL products use this one injective envelope instead.  Every emitted
    transaction has exactly one endpoint tag and one payload record; coincident
    transactions retain multiplicity without acquiring an artificial cross-endpoint
    payload.
    """

    if type(batch) not in (tuple, list):
        raise TypeError("an endpoint batch must be an exact finite sequence")
    endpoints = []
    payloads = []
    for transaction in batch:
        if type(transaction) is not dict or len(transaction) != 1:
            raise TypeError(
                "an endpoint transaction is one exact endpoint/payload mapping")
        endpoint, payload = next(iter(transaction.items()))
        if type(endpoint) is not str or not endpoint:
            raise TypeError("an endpoint transaction needs a nonempty endpoint name")
        if type(payload) is not dict:
            raise TypeError("an endpoint transaction payload must be an exact record")
        endpoints.append(endpoint)
        payloads.append(payload)
    return StepOutput(
        len(endpoints),
        {"@endpoint": tuple(endpoints), "@payload": tuple(payloads)},
    )


def endpoint_batch_rows(output):
    """Decode the exact endpoint envelope produced by :func:`endpoint_batch_output`.

    The inverse is intentionally strict.  A legacy dense buffer containing endpoint
    names, payload placeholders, or another field beside the two reserved envelope
    fields is not silently reinterpreted as the canonical sparse transaction batch.
    """

    if type(output) is not StepOutput:
        raise TypeError("an endpoint batch decoder needs an exact StepOutput")
    rows = output.transactions
    batch = []
    for row in rows:
        if set(row) != {"@endpoint", "@payload"}:
            raise ValueError(
                "a canonical endpoint transaction has only its endpoint and payload")
        endpoint, payload = row["@endpoint"], row["@payload"]
        if type(endpoint) is not str or not endpoint:
            raise TypeError("an endpoint transaction needs a nonempty endpoint name")
        if type(payload) is not dict:
            raise TypeError("an endpoint transaction payload must be an exact record")
        batch.append({endpoint: payload})
    rebuilt = endpoint_batch_output(batch)
    if rebuilt != output:
        raise ValueError("an endpoint batch is not the canonical injective envelope")
    return tuple(batch)


class StepOutput(Frozen):
    """The finite sequence of transactions emitted by one semantic step.

    ``count`` distinguishes silence from one zero-valued transaction.  ``buffers``
    stores one column per transaction field; tuple columns provide one cell per row,
    while a scalar column is the value of the sole row.  The ordinary flattened
    single-endpoint representation therefore remains compact.

    Independent endpoint streams use :func:`endpoint_batch_output`.  Its two reserved
    columns carry an endpoint tag and payload for every row, so simultaneous or
    repeated endpoint emissions stay sparse and round-trip through
    :func:`endpoint_batch_rows` without ``None`` padding or field-name collisions.
    Both frontends must use the same form before their replay histories are compared.
    """

    __slots__ = ("_buffers", "count")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("StepOutput is a closed replay record and cannot be subclassed")

    def __init__(self, count=0, buffers=None):
        if type(count) is not int or count < 0:
            raise ValueError("a step-output count must be an exact nonnegative integer")
        self.count = count
        self._buffers = FrozenValueMap(record_pairs(
            {} if buffers is None else buffers, "step-output buffers"))
        for name, cells in self._buffers.items():
            if self.count > 1 and type(cells) is not tuple:
                raise ValueError(
                    f"step-output buffer {name!r} needs one cell per transaction")
            if type(cells) is tuple and len(cells) < self.count:
                raise ValueError(
                    f"step-output buffer {name!r} has {len(cells)} cells for "
                    f"count {self.count}")
        self._freeze()

    @property
    def buffers(self):
        """A detached JSON-shaped view of the immutable output buffers."""
        return thaw_record_value(self._buffers)

    @property
    def transactions(self):
        """The transactions themselves, which is the buffers cut to the count.

        A list of mappings from endpoint to value, one per transaction. This is
        what a history is compared on; the buffers behind it are how far the
        step could have gone, which is a fact about the lowering rather than
        about the run.
        """
        out = []
        for index in range(self.count):
            row = {}
            for port, cells in self._buffers.items():
                if type(cells) is tuple:
                    row[port] = thaw_record_value(cells[index])
                elif index == 0:
                    row[port] = thaw_record_value(cells)
            out.append(row)
        return out

    def __eq__(self, other):
        if type(other) is not StepOutput:
            return NotImplemented
        return (self.count == other.count
                and same_record_value(self._buffers, other._buffers))

    def as_dict(self):
        return {"count": self.count, "buffers": self.buffers}

    def __reduce__(self):
        return (StepOutput, (self.count, self.buffers))

    def __repr__(self):
        return f"StepOutput(count={self.count}, {self.transactions})"


class Feasible(Enum):
    """Whether the trace set the certificate is about has a member.

    Three answers, because a solver has three. Reporting a solver's ``unknown``
    as "no admissible trace exists" states something much stronger than what was
    learned, and in the direction that reads as a finding about the artifact
    when it is a finding about the budget.

    **It has no truth value, and that is the fix rather than an inconvenience.**
    `NO` and `UNRESOLVED` used to be both falsy, defended on the grounds that a caller
    asking whether feasibility was *established* gets the right answer from
    ``if not feasible``. That is one of the two readings a reader can give that line, and
    the other one -- "the domain is empty" -- is the finding about the artifact that this
    enum exists to keep apart from a finding about the budget. A type whose obvious
    reading is the wrong one is not made safe by a docstring; TODO3 T3-04 asks for an
    explicit result algebra, so the three questions are three names.

    `__bool__` raises rather than being deleted. Deleting it would make every existing
    ``if not feasible`` silently False, since an enum member is truthy by default, which
    turns a wrong branch into a wrong branch nobody sees.
    """

    YES = "feasible"
    NO = "infeasible"
    UNRESOLVED = "unresolved"

    @property
    def established(self):
        """Feasibility was shown. The only answer a conclusion may rest on."""
        return self is Feasible.YES

    @property
    def refuted(self):
        """The domain was shown to be empty. A finding about the artifact."""
        return self is Feasible.NO

    @property
    def unresolved(self):
        """Nobody could tell. A finding about the budget, and not a weaker `NO`."""
        return self is Feasible.UNRESOLVED

    def __bool__(self):
        raise TypeError(
            "Feasible has no truth value: `not feasible` reads as both 'was not "
            "established' and 'the domain is empty', and those are a finding about the "
            "budget and a finding about the artifact. Ask for one of "
            "`.established`, `.refuted`, `.unresolved`.")


class Replay(Frozen):
    """What running a trace on the artifact produced.

    Two ways a replay can end badly, and they are not the same fact. ``error``
    is the artifact halting with a code the semantics models: a division by
    zero is an outcome, it may be the witness for a safety goal, and a pool
    full of them is a pool of legitimate traces. ``broken`` is this side
    failing to carry the replay out: a simulator that died, a trace that could
    not be decoded, a length that came back wrong. Only the second is a reason
    to disbelieve the record.

    Conflating them is how a certificate came to withhold itself whenever a
    pool trace reached a safety goal, which is the one thing a pool is for.
    """

    __slots__ = ("_hits", "_outputs", "_state", "broken", "error", "steps")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Replay is a closed replay record and cannot be subclassed")

    def __init__(self, hits, outputs=(), error="", state=None, broken=None,
                 steps=None):
        if type(hits) not in (tuple, list, set, frozenset):
            raise TypeError("replay hits must be a finite built-in collection")
        if any(type(goal) is not str or not goal for goal in hits):
            raise TypeError("replay hits must contain only nonempty exact strings")
        self._hits = frozenset(hits)
        if type(outputs) not in (tuple, list) or any(
                type(output) is not StepOutput for output in outputs):
            raise TypeError(
                "replay outputs must contain only exact StepOutput records")
        self._outputs = tuple(outputs)
        if type(error) is not str:
            raise TypeError("a modeled replay error must be an exact string")
        self.error = error
        self._state = freeze_record_value(state, "replay state")
        if broken is not None and type(broken) is not str:
            raise TypeError("a replay infrastructure failure must be an exact string")
        self.broken = broken
        if steps is not None and (type(steps) is not int or steps < 0):
            raise ValueError(
                "a replay step count must be an exact nonnegative integer or absent")
        self.steps = steps
        self._freeze()

    @property
    def hits(self):
        """A detached set view of the immutable hit identifiers."""
        return set(self._hits)

    @property
    def outputs(self):
        """A detached list view of the immutable per-step output records."""
        return list(self._outputs)

    @property
    def state(self):
        """A detached JSON-shaped view of the immutable final state."""
        return thaw_record_value(self._state)

    @property
    def halted(self):
        """Whether the artifact stopped early, which is a modeled outcome."""
        return bool(self.error)

    def as_dict(self):
        out = {
            "hits": sorted(self._hits),
            "outputs": [output.as_dict() for output in self._outputs],
            "error": self.error,
            "state": self.state,
            "steps": self.steps,
        }
        if self.broken is not None:
            out["broken"] = self.broken
        return out

    def __reduce__(self):
        return (Replay, (
            self.hits, self.outputs, self.error, self.state,
            self.broken, self.steps))

    def __repr__(self):
        return (f"Replay(hits={len(self.hits)}, error={self.error!r}"
                + (f", broken={self.broken!r}" if self.broken else "") + ")")


class Verdict(Frozen):
    """What the queries decided about a set of goals."""

    __slots__ = ("_reachable", "_undecided", "_unreachable", "queries")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Verdict is a closed query record and cannot be subclassed")

    def __init__(self, reachable, unreachable, undecided, queries=None):
        try:
            self._reachable = FrozenValueMap(record_pairs(
                reachable, "query verdict reachable witnesses"))
        except TypeError as exc:
            if "non-string key" in str(exc):
                raise TypeError(
                    "a query verdict may name only nonempty exact goal strings") \
                    from None
            raise
        try:
            self._undecided = FrozenValueMap(record_pairs(
                undecided, "query verdict unresolved reasons"))
        except TypeError as exc:
            if "non-string key" in str(exc):
                raise TypeError(
                    "a query verdict may name only nonempty exact goal strings") \
                    from None
            raise TypeError(
                "an unresolved query verdict needs a nonempty exact string reason") \
                from None
        if type(unreachable) not in (tuple, list, set, frozenset):
            raise TypeError(
                "query verdict unreachable goals need a finite built-in collection")
        self._unreachable = frozenset(unreachable)
        named = (tuple(self._reachable), tuple(self._unreachable),
                 tuple(self._undecided))
        if any(type(goal) is not str or not goal
               for group in named for goal in group):
            raise TypeError("a query verdict may name only nonempty exact goal strings")
        if any(type(reason) is not str or not reason
               for reason in self._undecided.values()):
            raise TypeError(
                "an unresolved query verdict needs a nonempty exact string reason")
        overlaps = {
            "reachable/unreachable": set(self._reachable) & self._unreachable,
            "reachable/undecided": set(self._reachable) & set(self._undecided),
            "unreachable/undecided": self._unreachable & set(self._undecided),
        }
        overlaps = {name: sorted(goals)
                    for name, goals in overlaps.items() if goals}
        if overlaps:
            raise ValueError(
                "one query answer cannot give two statuses to the same goal: "
                f"{overlaps}")
        if queries is not None and (
                type(queries) not in (tuple, list) or any(
                    type(item) is not QueryCall for item in queries)):
            raise TypeError(
                "a query transcript accepts only a finite sequence of exact "
                "QueryCall records")
        #: ``None`` is an explicitly legacy aggregate with no solver transcript.  The
        #: closure unit tests retain it while the certificate-grade coordinator refuses
        #: to publish it as conditional evidence.
        self.queries = None if queries is None else tuple(queries)
        self._freeze()

    @property
    def reachable(self):
        """A detached goal-to-witness mapping.

        A trace remains a tuple: direct closure providers historically use a tuple as
        their finite trace key.  Nested mappings are copied, so mutating the view still
        cannot change this verdict.
        """
        def trace_view(value):
            if type(value) is FrozenValueMap:
                return {name: trace_view(item) for name, item in value.items()}
            if type(value) is tuple:
                return tuple(trace_view(item) for item in value)
            return value

        return {goal: trace_view(trace)
                for goal, trace in self._reachable.items()}

    @property
    def unreachable(self):
        """A detached set of goals ruled out at this bound."""
        return set(self._unreachable)

    @property
    def undecided(self):
        """A detached goal-to-reason mapping."""
        return thaw_record_value(self._undecided)

    def _answer_image(self, reachable):
        return {
            "reachable": reachable,
            "unreachable": sorted(self._unreachable),
            "undecided": self.undecided,
        }

    def as_dict(self):
        """Native view, retaining tuple traces and optional legacy transcripts."""
        out = self._answer_image(self.reachable)
        if self.queries is not None:
            out["queries"] = [query.as_dict() for query in self.queries]
        return out

    def as_record(self):
        """A complete query transcript in the JSON sequence carrier."""
        out = self._answer_image(thaw_record_value(self._reachable))
        out["queries"] = [query.as_dict() for query in self.queries]
        return out

    @classmethod
    def from_record(cls, value):
        fields = {"reachable", "unreachable", "undecided", "queries"}
        if type(value) is not dict or set(value) != fields \
                or type(value["reachable"]) is not dict \
                or type(value["unreachable"]) is not list \
                or type(value["undecided"]) is not dict \
                or type(value["queries"]) is not list:
            raise TypeError("a query-verdict image has its complete finite fields")
        got = cls(
            value["reachable"], value["unreachable"], value["undecided"],
            queries=tuple(QueryCall.from_dict(row) for row in value["queries"]))
        if got.as_record() != value:
            raise ValueError("a query-verdict image is not canonical")
        return got

    def __reduce__(self):
        return (Verdict, (
            self.reachable, self.unreachable, self.undecided, self.queries))

    def __repr__(self):
        return (f"Verdict(reachable={len(self.reachable)}, "
                f"unreachable={len(self.unreachable)}, "
                f"undecided={len(self.undecided)})")


@runtime_checkable
class Instance(Protocol):
    """One artifact, at one bound, under one contract."""

    def analysis_identity(self) -> str:
        """Stable identity of the transition system this provider queries."""

    def evidence_identity(self, steps) -> dict:
        """Stable representation and encoding identities for this bounded query."""

    def task_audit(self, task) -> AuditCheck:
        """Whether its finite boundary is exactly the admitted task boundary."""

    def goals(self) -> dict:
        """Every goal that may be reported, each with its class."""

    def replay(self, trace) -> Replay:
        """Run ``trace`` on the artifact and report what it reached."""

    def audit_request(self, trace):
        """Validate and audit a decoded solver request into a complete witness."""

    def query(self, goals, steps, timeout_ms=None) -> Verdict:
        """Decide each goal against the admissible traces of ``steps``."""

    def audit(self, traces) -> AuditCheck:
        """Whether the exact readings agree, disagree, or could not be checked."""

    def feasible(self, steps, timeout_ms=None):
        """Whether a trace of ``steps`` exists, and one if it does."""

    def well_formed(self) -> list:
        """Reasons the artifact is not in the shape the rest assumes."""

    def contract(self) -> dict:
        """What the environment may present, which the verdicts are about."""


class BoundedInstance(Frozen):
    """Bind one exact frontend to a fixed trace domain and optional catalogue.
    
    Every analysis request uses this bound, input contract and artifact identity.
    Tagged requests must name the same domain; bare requests are validated and
    replayed here. Decoded solver requests become witnesses only after the
    frontend's independent audits."""

    __slots__ = (
        "analysis_cache", "domain", "domain_manifest", "instance",
        "request_budget",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError(
            "BoundedInstance is a closed semantic binding and cannot be subclassed")

    def __init__(self, instance, domain, domain_manifest=None, *,
                 analysis_cache=None, request_budget=None):
        if type(domain) is not TraceDomain:
            raise TypeError("a bounded instance requires the exact TraceDomain record")
        if domain_manifest is not None:
            if type(domain_manifest) is not DomainManifest:
                raise TypeError(
                    "a bounded instance manifest must be the exact DomainManifest")
            if domain_manifest.domain != domain:
                raise ValueError(
                    "a bounded instance manifest names a different trace domain")
        if analysis_cache is not None \
                and type(analysis_cache) is not AnalysisCacheBinding:
            raise TypeError(
                "a bounded instance cache identity is AnalysisCacheBinding or absent")
        if request_budget is not None:
            from .query_cache import AnalysisRequestBudget

            if type(request_budget) is not AnalysisRequestBudget:
                raise TypeError(
                    "a bounded instance request budget has its closed runtime owner")
        # This class also implements ``__getattr__`` for an explicit passthrough
        # protocol.  Going through ``Frozen.__setattr__`` before ``instance`` exists
        # would ask for ``_frozen``, enter that passthrough, and recursively try to
        # mention the not-yet-set instance in its error.  Construction is the one
        # authorised write; after ``_freeze`` all public writes still go through the
        # ordinary guard.
        object.__setattr__(self, "instance", instance)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "domain_manifest", domain_manifest)
        object.__setattr__(self, "analysis_cache", analysis_cache)
        object.__setattr__(self, "request_budget", request_budget)
        self._freeze()

    def bind_catalogue(self, catalogue):
        """Bind this exact provider to one domain/catalogue evidence identity.

        Domain-only instances remain useful for representation diagnostics.  The
        production analyzer calls this before its first feasibility or goal query.
        """

        if type(catalogue) is not CatalogueManifest:
            raise TypeError(
                "binding a bounded instance needs the exact CatalogueManifest")
        manifest = DomainManifest(self.domain, catalogue)
        if self.domain_manifest is not None and self.domain_manifest != manifest:
            raise ValueError(
                "the bounded instance is already bound to another domain manifest")
        if self.domain_manifest == manifest:
            return self
        return BoundedInstance(
            self.instance, self.domain, manifest,
            analysis_cache=self.analysis_cache,
            request_budget=self.request_budget)

    # ── what a trace of this domain looks like ──

    def validate(self, trace):
        """Reasons ``trace`` is not a trace of this domain, or an empty list.

        Checked before replay rather than after, since a trace that is not of this
        domain is not evidence about it and running it produces hits that would be
        counted.
        """
        rows = trace.steps if isinstance(trace, Tagged) else trace
        if isinstance(trace, Tagged) and trace.domain != self.domain.digest():
            return [(f"the trace belongs to domain {trace.domain} and this is "
                     f"{self.domain.digest()}")]
        try:
            length = len(rows)
        except TypeError:
            return [f"a trace that is not a sequence of steps: {type(rows).__name__}"]
        if self.domain.bound is not None and length != self.domain.bound:
            return [(f"a trace of {length} steps where the bound is "
                     f"{self.domain.bound}")]
        out = []
        contract = self.domain.contract
        if not contract:
            return out
        for index, row in enumerate(rows):
            if not hasattr(row, "items"):
                # A contracted request supplies a complete name-to-value row.
                out.append(f"step {index} is not a name-to-value row but a "
                           f"{type(row).__name__}, and this domain declares a contract "
                           f"over {sorted(contract)[:6]}")
                continue
            for name, value in row.items():
                bounds = contract.get(name)
                if bounds is None:
                    out.append(f"step {index} presents {name}, which the contract "
                               f"does not declare")
                elif type(value) is not int:
                    out.append(
                        f"step {index} presents {name} as a "
                        f"{type(value).__name__}, but this contract declares an "
                        "integer interval")
                elif not (bounds[0] <= value <= bounds[1]):
                    out.append(f"step {index} presents {name}={value}, outside "
                               f"{bounds[0]}..{bounds[1]}")
            for name in sorted(set(contract) - set(row)):
                out.append(
                    f"step {index} omits {name}, so it is not a complete valuation "
                    "of the declared input context")
        return out

    def tag(self, trace):
        """Attach this domain's identity so another bound gate can check it."""
        rows = trace.steps if isinstance(trace, Tagged) else trace
        return Tagged(self.domain.digest(), list(rows))

    # ── the questions, none of which takes a bound ──

    def goals(self):
        if self.domain_manifest is not None:
            catalogue = self.domain_manifest.catalogue
            return {goal_id: catalogue[goal_id] for goal_id in catalogue}
        return self.instance.goals()

    def evidence_identity(self):
        """The finite representation and encoding bound to this domain."""
        return self.instance.evidence_identity(self.domain.bound)

    def replay(self, trace):
        rows = trace.steps if isinstance(trace, Tagged) else trace
        return self.instance.replay(rows)

    def _checked_witness_audit(self, got, rows):
        """Validate one frontend answer before it crosses the bound gate."""
        if type(got) is AuditedWitness:
            try:
                same_inputs = same_record_value(
                    freeze_record_value(got.inputs),
                    freeze_record_value(tuple(rows)))
            except (TypeError, ValueError) as exc:
                return AuditCheck.refused(
                    "witness-audit-request-image-invalid",
                    f"the request has no exact finite value image: {exc}")
            if got.domain != self.domain.digest() or not same_inputs:
                return AuditCheck.inconsistent(
                    "the exact provider returned a witness for another domain or "
                    "another input trace")
            return got
        if type(got) is AuditCheck:
            return got
        return AuditCheck.refused(
            "witness-audit-result-invalid",
            "the exact provider returned neither an AuditedWitness nor an AuditCheck")

    def audit_requests(self, traces):
        """Audit a finite request batch, using a frontend batch seam when present."""

        traces = tuple(traces)
        results = [None] * len(traces)
        pending = []
        for index, trace in enumerate(traces):
            rows = trace.steps if isinstance(trace, Tagged) else trace
            wrong = self.validate(trace)
            if wrong:
                results[index] = AuditCheck.inconsistent([
                    "the decoded solver request is outside its bound domain: "
                    f"{item}" for item in wrong])
            else:
                pending.append((index, rows))
        if not pending:
            return tuple(results)

        batch = getattr(self.instance, "audit_requests", None)
        one = getattr(self.instance, "audit_request", None)
        try:
            if callable(batch):
                got = batch(
                    tuple(rows for _index, rows in pending), self.domain.digest())
                if type(got) not in (tuple, list) or len(got) != len(pending):
                    got = tuple(AuditCheck.refused(
                        "witness-audit-batch-result-invalid",
                        "the exact provider returned no result for every request in "
                        "the witness-audit batch") for _ in pending)
            elif callable(one):
                got = tuple(one(rows, self.domain.digest())
                            for _index, rows in pending)
            else:
                got = tuple(AuditCheck.refused(
                    "witness-audit-unsupported",
                    "this exact provider cannot turn a decoded solver request into "
                    "a complete audited witness") for _ in pending)
        except WitnessInconsistent as exc:
            got = tuple(AuditCheck.inconsistent(str(exc)) for _ in pending)
        except Refused as exc:
            got = tuple(AuditCheck.refused(
                exc.refusal.kind, exc.refusal.why, **exc.refusal.detail) for _ in pending)
        except Exception as exc:                              # noqa: BLE001
            got = tuple(AuditCheck.refused(
                "witness-audit-failed",
                "the exact provider's witness audit raised "
                f"{type(exc).__name__}: {exc}") for _ in pending)
        for (index, rows), item in zip(pending, got):
            results[index] = self._checked_witness_audit(item, rows)
        return tuple(results)

    def audit_request(self, trace):
        """Admit one decoded solver recipe only after all required audits."""
        return self.audit_requests((trace,))[0]

    def query(self, goals, **kw):
        # **No step count.** There was one, optional, refused when it disagreed with the
        # domain. Refusing a disagreement is the right behaviour for a parameter that
        # exists; what TODO3 T3-04 asks is why it exists. The bound is a property of the
        # domain this instance is bound to, so a caller with a second one to offer either
        # agrees, in which case it said nothing, or disagrees, in which case it is asking
        # about a different set of traces and should bind that domain instead.
        feedback = kw.pop("_feedback", None)
        query = self.instance.query
        if feedback is not None:
            from .query_feedback import QueryFeedback

            if type(feedback) is not QueryFeedback:
                raise TypeError("query feedback must be the core-owned protocol")
            feedback.check(self, goals)
            query = self.instance.query_with_feedback
            kw["feedback"] = feedback
        if self.request_budget is not None:
            self.request_budget.charge()
        if self.analysis_cache is None:
            got = query(
                goals, steps=self.domain.bound, **kw)
            if self.request_budget is not None \
                    and type(got) is Verdict and got.queries is not None \
                    and len(got.queries) > 1:
                self.request_budget.charge(len(got.queries) - 1)
        else:
            from .query_cache import cached_query

            timeout_ms = kw.get("timeout_ms")
            got = cached_query(
                self.analysis_cache, self.domain.bound, goals, timeout_ms,
                lambda: query(
                    goals, steps=self.domain.bound, **kw),
                charge_remainder=(
                    None if self.request_budget is None
                    else self.request_budget.charge))
        return got

    def query_steps(self, goals, **kw):
        """Yield completed calls; incomplete streams never seed the answer cache.

        Formula preparation stays with the frontend's bound session. Unlike a
        complete cached answer, each new part is charged and audited before the
        caller resumes the worker. Closing this iterator also closes its cursor.
        """
        from contextlib import closing
        from ..analysis_budget import check_analysis

        pending = set(goals)
        with closing(self.instance.query_steps(
                goals, steps=self.domain.bound, **kw)) as parts:
            while True:
                check_analysis()
                if self.request_budget is not None and pending:
                    self.request_budget.charge()
                try:
                    part = next(parts)
                except StopIteration:
                    return
                if self.request_budget is not None and type(part) is Verdict \
                        and part.queries and len(part.queries) > 1:
                    self.request_budget.charge(len(part.queries) - 1)
                if type(part) is Verdict:
                    pending -= set(part.reachable) | set(part.unreachable) | set(part.undecided)
                yield part

    def audit(self, traces):
        return self.instance.audit([t.steps if isinstance(t, Tagged) else t
                                    for t in traces])

    def feasible(self, **kw):
        # Same as `query`: the bound comes from the domain and there is nowhere else to
        # take one from.
        if self.request_budget is not None:
            self.request_budget.charge()
        if self.analysis_cache is None:
            got = self.instance.feasible(self.domain.bound, **kw)
        else:
            from .query_cache import cached_feasibility

            got = cached_feasibility(
                self.analysis_cache, self.domain.bound, kw.get("timeout_ms"),
                lambda: self.instance.feasible(self.domain.bound, **kw))
        if type(got) is FeasibilityCall:
            return got
        if type(got) not in (tuple, list) or len(got) != 2:
            raise TypeError(
                "an exact feasibility provider must return FeasibilityCall or one "
                "legacy answer/detail pair")
        try:
            return FeasibilityCall(got[0], got[1])
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            if type(got[0]) is Feasible:
                raise _FeasibilityDetailError(
                    got[0], f"{type(exc).__name__}: {exc}") from None
            raise

    #: What a bound instance passes through untouched, by name.
    #:
    #: This used to be a `__getattr__` forwarding everything, defended on the grounds
    #: that listing would make this a second place deciding which questions a frontend
    #: may be asked. The cost of not listing is larger: a wrapper whose job is to be the
    #: gate forwards, unexamined, every question nobody thought to name -- including a
    #: misspelling, which reaches the frontend and raises there as if the frontend were
    #: at fault. TODO3 T3-04 asks for the bypass to go.
    #:
    #: Each of these is a question about the **artifact** that the bound does not change:
    #: what the frontend is, what it can audit against, what it trusts, what it says it
    #: got wrong. The four the bound *does* change -- `goals`, `replay`, `query`,
    #: `feasible` -- are methods above, and `validate` and `tag` are this class's own.
    PASSTHROUGH = ("well_formed", "source_audit", "unaccounted_goals",
                   "ruled_out", "contract", "budgets", "audit_plan",
                   "trusted_assumptions", "task_audit", "analysis_identity",
                   "catalogue_manifest", "state_of", "name", "source")

    def __getattr__(self, name):
        if name in BoundedInstance.PASSTHROUGH:
            return getattr(self.instance, name)
        raise AttributeError(
            f"a bound instance does not pass {name!r} through to "
            f"{type(self.instance).__name__}. The bound changes what `goals`, `replay`, "
            f"`query` and `feasible` mean, and everything else a frontend offers is "
            f"listed in `BoundedInstance.PASSTHROUGH` so that adding one is a decision. "
            f"If this is a real question about the artifact, add it there; if it is a "
            f"typo, it used to reach the frontend and raise as though the frontend were "
            f"at fault")

    def __repr__(self):
        suffix = ("" if self.domain_manifest is None
                  else f", domain_manifest={self.domain_manifest.digest()}")
        return (f"BoundedInstance({self.instance!r}, "
                f"domain={self.domain.digest()}{suffix})")


class Tagged(Frozen):
    """A trace together with the digest of the domain it was produced in.

    Only for traces that cross a domain boundary. Inside one closure the traces are
    the instance's own and the tag would be noise; between two, as in cross-replay,
    the tag is what lets the receiving side refuse a trace of a different bound,
    contract, policy or frontend rather than counting its hits.
    """

    __slots__ = ("_steps", "domain")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Tagged is a closed trace record and cannot be subclassed")

    def __init__(self, domain, steps):
        if type(domain) is not str or not domain:
            raise ValueError("a tagged trace needs a nonempty exact domain identity")
        if type(steps) not in (tuple, list):
            raise TypeError("a tagged trace needs a finite built-in step sequence")
        self.domain = domain
        frozen = freeze_record_value(steps, "tagged trace steps")
        if type(frozen) is not tuple:
            raise TypeError("a tagged trace did not freeze to a finite step sequence")
        self._steps = frozen
        self._freeze()

    @property
    def steps(self):
        """A detached tuple of finite step values."""
        return tuple(thaw_record_value(step) for step in self._steps)

    def __len__(self):
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)

    def __repr__(self):
        return f"Tagged({self.domain}, {len(self.steps)} steps)"

    def __reduce__(self):
        return (Tagged, (self.domain, self.steps))
