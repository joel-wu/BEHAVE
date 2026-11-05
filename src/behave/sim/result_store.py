"""One invocation's account of itself: one immutable record, and a pointer to it.

**Definition.** A run of `run_cosim` writes exactly one authoritative document,
`runs/<invocation>/result.json`, holding this invocation's identity, its authoritative
record, its report and its phase fields together. `latest.json` names the run whose
result a reader should take. `authority.json` and `cosim_report.json` are **derived
views** of that document, kept because existing readers open them by name.

**Constraints.**

* One writer per record. Every exit goes through `ResultStore.commit`, under a thread
  lock and the problem directory's `flock`.
* One invocation per record. Each run writes only inside its own directory; the two
  derived views and `latest.json` are shared, and a run may only advance them if it is
  not overwriting a **newer** run.
* One state. The phase fields belong to the store and are reached through a recorder;
  there is no dictionary two threads share. They are **values**: an envelope deep-freezes
  what it is given, so a live object put in one is copied and later mutations of the
  original never appear. A caller that needs a live back-channel to another thread passes
  the object to that thread, not through here.
* Immutable. A `RunEnvelope` is deep-frozen, so a holder of one has what was committed.
* No regression. Once a result is decided the envelope is terminal, and the timeout path
  annotates rather than overwrites.

**Refusals.**

* `ValueError` where an update names an invocation this store is not.
* `ValueError` where a report's `result_kind`, its authority summary, or the complete
  authority committed beside it disagree.
* `ValueError` where a report carries a record from another process or another
  `RESULT_VERSION`.
* A commit that cannot take the lock inside its timeout writes nothing and returns
  `False`.
* A commit that would move a shared view backwards onto an older run leaves the shared
  view alone; the invocation's own `result.json` is written either way.

**What the previous shape could not hold**, each reproduced:

* the two files were separately authoritative and separately assembled, so at one
  sequence number `authority.json` could say `why = A` while `cosim_report.json`'s
  authority field said `why = B`;
* `RunEnvelope.authority` was a plain `dict` and could be edited in place through
  `snapshot()`;
* a `flock` orders concurrent writers and says nothing about a **stale** one: an older
  invocation's watchdog, firing after a newer invocation had decided, wrote `partial`
  over `concrete_evaluation`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from behave.core import runtime_defaults as defaults

from .authority import CONCRETE, PARTIAL, REFUSED, Authoritative
from .core.failure import RunFailure
from .core.frozen import Frozen
from .core.record_value import (
    FrozenValueMap,
    _json_mapping,
    canonical_json,
    freeze_record_value,
    same_record_value,
    thaw_record_value,
)
from .core.refusal import Refusal

__all__ = ["CommittedDecision", "ResultStore", "RunEnvelope", "read_record", "refused_early",
           "verdict_line", "write_json_atomically"]

#: The document version. A reader that does not know it must not interpret the fields.
RECORD_VERSION = "run-record/3"

_RUN_ENVELOPE = object()
_COMMITTED_DECISION = object()


class CommittedDecision(Frozen):
    """A lightweight read of the decision already persisted by this invocation.

    ``authoritative`` says whether a score exists. ``passed`` is its stored Boolean,
    or None for a terminal no-result/refusal. Failures retain their run indices and
    source attribution; mismatch and safety evidence stay separate. This view never
    runs the evaluator, opens result JSON, or implies a retry policy.
    """

    __slots__ = ("authority", "evidence", "failures", "invocation", "record_path")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("CommittedDecision is a closed persisted-result view")

    def __init__(self, envelope, record_path, *, _token=None):
        if _token is not _COMMITTED_DECISION:
            raise TypeError("read the committed decision from CommandResult")
        if type(envelope) is not RunEnvelope or not envelope.terminal:
            raise ValueError("a committed decision requires a terminal envelope")
        self.invocation = envelope.invocation
        self.record_path = str(record_path)
        self.authority = FrozenValueMap(
            (key, value) for key, value in envelope.authority.items()
            if key != "evaluation")
        evaluation = envelope.authority.get("evaluation") or {}
        self.failures = tuple(
            (index, RunFailure.from_dict(_plain(failure)))
            for index, failure in evaluation.get("failures", ()))
        self.evidence = FrozenValueMap(
            (key, evaluation[key]) for key in (
                "accepted", "complete", "mismatch_count", "memory_mismatch_count",
                "safety_hits") if key in evaluation)
        self._freeze()

    @property
    def kind(self):
        return self.authority["kind"]

    @property
    def authoritative(self):
        return self.authority["authoritative"]

    @property
    def passed(self):
        return self.authority.get("passed")

    @property
    def evaluation_fingerprint(self):
        return self.authority.get("evaluation_fingerprint")

    def as_dict(self):
        return {
            "version": "committed-decision/1",
            "invocation": self.invocation,
            "record_path": self.record_path,
            "authority": _plain(self.authority),
            "evidence": _plain(self.evidence),
            "failures": [[index, failure.as_dict()]
                         for index, failure in self.failures],
        }


def refused_early(store, kind, why, **detail):
    """The authoritative record for a run that ended before the path could run.

    Every early exit -- both toolchains failing, a port-name mismatch, no top module --
    goes through here rather than assembling a report of its own, because a second writer
    with its own idea of a complete record is how the files came to disagree.
    """
    result = Authoritative(REFUSED, why=kind,
                           refusals=[Refusal(kind, why, **detail)])
    store.decided(result)
    return result


def _deep_frozen(value):
    """``value`` as a structure nothing can edit in place.

    This is the same exact finite carrier used by stimuli and raw traces.  The former
    mapping/list-only recursion left sets, subclasses and arbitrary live objects inside
    an otherwise frozen envelope; it also gave this persistence boundary a different
    definition of a record value from the records it stores.
    """
    return freeze_record_value(value, "run-envelope")


def _plain(value):
    """The inverse of `_deep_frozen`, for `json.dumps` and for a caller building the next
    version of a record."""
    # Values already held by a RunEnvelope crossed `_record_field` and are recursively
    # immutable.  Re-freezing such a value copied a large command or evaluation in full
    # on every phase update.  A caller-supplied value still reaches `_record_field`
    # before it can enter an envelope.
    if type(value) in (FrozenValueMap, tuple):
        return thaw_record_value(value)
    return thaw_record_value(freeze_record_value(value, "run-envelope"))


def _record_field(value, name):
    frozen = _deep_frozen(value)
    if type(frozen) is not FrozenValueMap:
        raise TypeError(f"a RunEnvelope {name} must be an exact finite mapping")
    return frozen


def _same_record_value(left, right):
    """Exact equality for two public or frozen run-record fragments."""
    return same_record_value(_deep_frozen(left), _deep_frozen(right))


def _same_envelope_value(left, right):
    """Compare already-validated envelope fragments without copying them.

    Only `_record_field` and persistent envelope updates supply these values.
    A caller-built FrozenValueMap is not necessarily recursively immutable and
    must still cross `_record_field` before this helper can be used.
    """
    return left is right or same_record_value(left, right)


def _authority_summary(record):
    """The verdict mirror stored in report/progress, without duplicating evidence.

    ``authority`` is the sole complete record and retains the evaluation.  Its mirrors
    carry every verdict field plus the evaluation fingerprint, so they remain bound to
    that exact evidence without embedding it again.
    """
    if type(record) is not dict:
        raise TypeError("an authority summary is derived from an exact dictionary")
    return {key: value for key, value in record.items() if key != "evaluation"}


def _updated_record_field(current, update, name):
    """Return ``current`` with one validated top-level mapping update.

    The nested values in ``current`` are immutable, so unchanged fields can be shared.
    This is a persistent-map update, not a relaxation of the record carrier.
    """
    if update is None:
        return current
    additions = _record_field(update, name)
    if not additions:
        return current
    replacement = dict(additions.items())
    pairs = []
    seen = set()
    for key, value in current.items():
        if key in replacement:
            value = replacement[key]
            seen.add(key)
        pairs.append((key, value))
    pairs.extend((key, value) for key, value in additions.items() if key not in seen)
    # Both inputs were validated frozen mappings and every nested value is immutable.
    return FrozenValueMap(pairs)


class RunEnvelope:
    """One invocation's account of itself at one moment. Immutable, all the way down.

    ``terminal`` marks a decided result: everything after it is telemetry, and the
    timeout path may annotate such a record but not replace its verdict.
    """

    __slots__ = (
        "_frozen",
        "authority",
        "invocation",
        "progress",
        "report",
        "sequence",
        "started",
        "terminal",
    )

    def __init__(self, invocation, started, sequence=0, authority=None, report=None,
                 progress=None, terminal=False):
        if getattr(self, "_frozen", False):
            raise AttributeError("a RunEnvelope cannot be reinitialized")
        invocation = freeze_record_value(invocation, "run-envelope.invocation")
        started = freeze_record_value(started, "run-envelope.started")
        sequence = freeze_record_value(sequence, "run-envelope.sequence")
        terminal = freeze_record_value(terminal, "run-envelope.terminal")
        if type(invocation) is not str:
            raise TypeError("a RunEnvelope invocation must be an exact string")
        if not invocation:
            raise ValueError("a RunEnvelope invocation must be a nonempty string")
        if type(started) is not float:
            raise TypeError("a RunEnvelope start time must be an exact float")
        if type(sequence) is not int:
            raise TypeError("a RunEnvelope sequence must be an exact integer")
        if sequence < 0:
            raise ValueError("a RunEnvelope sequence must be a nonnegative integer")
        if type(terminal) is not bool:
            raise TypeError("a RunEnvelope terminal flag must be an exact Boolean")
        self.invocation = invocation
        self.started = started
        #: Incremented per commit and written into every file, so two records can be
        #: ordered without trusting a modification time on a shared filesystem.
        self.sequence = sequence
        self.authority = None if authority is None else _record_field(
            authority, "authority")
        self.report = None if report is None else _record_field(report, "report")
        self.progress = _record_field(
            {} if progress is None else progress, "progress")
        self.terminal = terminal
        self._frozen = True

    @classmethod
    def _from_frozen(cls, invocation, started, sequence, authority, report,
                     progress, terminal, *, _token=None):
        """Build the next envelope by sharing fields already validated by this module."""
        if _token is not _RUN_ENVELOPE:
            raise TypeError("RunEnvelope frozen construction is private")
        instance = object.__new__(cls)
        for name, value in (
                ("invocation", invocation), ("started", started),
                ("sequence", sequence), ("authority", authority),
                ("report", report), ("progress", progress),
                ("terminal", terminal), ("_frozen", True)):
            object.__setattr__(instance, name, value)
        return instance

    def __setattr__(self, name, value):
        if getattr(self, "_frozen", False):
            raise AttributeError(
                f"a RunEnvelope is one invocation's account of itself at one moment and "
                f"does not change; build the next one with `advance` (setting {name!r})")
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        raise AttributeError("a RunEnvelope cannot be changed; use advance()")

    def __reduce__(self):
        return (RunEnvelope, (self.invocation, self.started, self.sequence,
                              self.authority, self.report, self.progress, self.terminal))

    def advance(self, authority=None, report=None, progress=None, terminal=None,
                report_update=None):
        """The next envelope: this one's fields with these changes on top."""
        if report is not None and report_update is not None:
            raise ValueError("replace a report or update its fields, not both")
        frozen_authority = (self.authority if authority is None else
                            _record_field(authority, "authority"))
        frozen_report = (self.report if report is None else
                         _record_field(report, "report"))
        if report_update is not None:
            frozen_report = _updated_record_field(
                frozen_report or FrozenValueMap(()), report_update, "report update")
        frozen_progress = _updated_record_field(
            self.progress, progress, "progress update")
        frozen_terminal = self.terminal if terminal is None else freeze_record_value(
            terminal, "run-envelope.terminal")
        if type(frozen_terminal) is not bool:
            raise TypeError("a RunEnvelope terminal flag must be an exact Boolean")
        return RunEnvelope._from_frozen(
            self.invocation, self.started, self.sequence + 1,
            frozen_authority, frozen_report, frozen_progress, frozen_terminal,
            _token=_RUN_ENVELOPE)

    def _record_fields(self):
        """Writer-owned view of the already validated immutable envelope."""
        return {"record_version": RECORD_VERSION,
                "run_invocation": self.invocation,
                "run_started_at": self.started,
                "run_sequence": self.sequence,
                "terminal": self.terminal,
                "authority": self.authority if self.authority else None,
                "report": self.report if self.report else None,
                "progress": self.progress}

    def as_record(self):
        """Detached public image, including its exact start time and phase fields."""
        fields = self._record_fields()
        for name in ("authority", "report", "progress"):
            fields[name] = _plain(fields[name])
        return fields

    def _stamped_fields(self, payload):
        return {**payload,
                "run_invocation": self.invocation,
                "run_started_at": self.started,
                "run_sequence": self.sequence,
                "derived_from": f"runs/{self.invocation}/result.json"}

    def stamped(self, payload):
        """``payload`` with this envelope's identity on it, for a derived view.

        `run_invocation`, not `invocation`: `authority.INVOCATION` already means which
        *process* produced a record, and one process runs many invocations.
        """
        return self._stamped_fields(_plain(payload))


class _Recorder:
    """The phase fields, writable by the caller and owned by the store.

    Keeps the `progress["phase_reached"] = ...` call sites and takes the ownership away:
    every write goes through the store's lock and produces a new envelope, so there is no
    object a second thread can read half-written.

    Deliberately not a `dict` subclass: a `dict` that is also something else gets copied
    somewhere, and the copy is the shared mutable namespace again one remove away.
    """

    __slots__ = ("_store",)

    def __init__(self, store):
        self._store = store

    def __setitem__(self, key, value):
        if type(key) is not str:
            raise TypeError("a progress-field name must be an exact string")
        self._store.record(**{key: value})

    def __getitem__(self, key):
        return self._store.snapshot().progress[key]

    def get(self, key, default=None):
        return self._store.snapshot().progress.get(key, default)

    def __contains__(self, key):
        return key in self._store.snapshot().progress

    def __iter__(self):
        return iter(self._store.snapshot().progress)

    def __bool__(self):
        # `if progress and progress.get("fallback")` asks whether a destination was
        # passed, not whether it holds anything yet.
        return True

    def update(self, other=None, **kw):
        fields = {} if other is None else _plain(
            _record_field(other, "progress update"))
        fields.update(kw)
        if fields:
            self._store.record(**fields)

    def items(self):
        return self._store.snapshot().progress.items()

    def keys(self):
        return self._store.snapshot().progress.keys()

    def as_dict(self):
        return _plain(self._store.snapshot().progress)


class ResultStore:
    """Everything one invocation writes, and the only thing that writes it."""

    __slots__ = ("_envelope", "_gate", "_lock", "invocation", "problem_dir", "started")

    def __init__(self, problem_dir, progress=None):
        self.problem_dir = Path(problem_dir)
        self.invocation = uuid.uuid4().hex
        self.started = time.time()
        # Two locks, two ways to collide. The thread lock keeps this process's writers --
        # the main thread and the watchdog -- out of each other's transaction; it says
        # nothing about a second *process*, which a sweep running one task twice is. The
        # file lock is over the directory, so it is the same lock for everybody.
        # Reentrant: a transition takes it and then calls `commit`, which takes it again.
        self._lock = threading.RLock()
        self._gate = self.problem_dir / ".result-store.lock"
        self._envelope = RunEnvelope(self.invocation, self.started,
                                     progress={} if progress is None else progress)

    # ── where things live ──

    @property
    def workspace(self):
        """This invocation's directory. Nothing else writes in it."""
        where = self.problem_dir / "runs" / self.invocation
        where.mkdir(parents=True, exist_ok=True)
        return where

    @property
    def result_path(self):
        """The one authoritative document."""
        return self.workspace / "result.json"

    @property
    def pointer_path(self):
        return self.problem_dir / "latest.json"

    @property
    def authority_path(self):
        """A derived view, kept for readers that open it by name."""
        return self.problem_dir / "authority.json"

    @property
    def report_path(self):
        """A derived view, kept for readers that open it by name."""
        return self.problem_dir / "cosim_report.json"

    @property
    def progress(self):
        return _Recorder(self)

    # ── state ──

    def snapshot(self):
        """The current envelope. Immutable, so holding it is safe without the lock."""
        with self._lock:
            return self._envelope

    def standing_authority(self):
        """The authoritative record this run currently holds, as a plain dictionary.

        What a report must carry. A caller that proposed a refusal cannot assume its own
        is the one that stands: a decision latches, so a second refusal -- the multi-clock
        path's, after the admission gate has already refused for want of declarations --
        is telemetry beside the first. A report built from the proposal instead of from
        this is a record holding two versions of itself, which `_admits` refuses.
        """
        with self._lock:
            return _plain(self._envelope.authority or {})

    def standing_authority_summary(self):
        """The report/progress mirror of the authority that currently stands."""
        with self._lock:
            return {key: _plain(value)
                    for key, value in self._envelope.authority.items()
                    if key != "evaluation"}

    def committed_decision(self):
        """Read the latched decision, or None if persistence never reached it."""
        with self._lock:
            if not self._envelope.terminal:
                return None
            path = self.problem_dir / "runs" / self.invocation / "result.json"
            return CommittedDecision(
                self._envelope, path, _token=_COMMITTED_DECISION)

    def standing_report(self):
        """The current report view without expanding the separate authority evidence."""
        with self._lock:
            return _plain(self._envelope.report or {})

    def attach_analysis(self, outcome, timeout=None):
        """Attach one typed bounded-analysis result beside a decided score.

        Analysis is deliberately not a verdict transition.  It may classify goals or
        refuse for want of an exact provider, but it cannot change ``authority``,
        ``result_kind`` or ``passed``.  The outcome names the SHA-256 fingerprint of
        the serialized :class:`ConcreteEvaluation` already held by this invocation.
        Once attached it is latched just like the decision: a rerun is a separate
        observation, not permission to rewrite the first one.
        """
        from .core.outcome import AnalysisOutcome

        if type(outcome) is not AnalysisOutcome:
            raise TypeError(
                "attach_analysis() takes the exact typed AnalysisOutcome")
        got = (self._lock.acquire() if timeout is None
               else self._lock.acquire(timeout=timeout))
        if not got:
            return False
        try:
            current = self._envelope
            if not current.terminal:
                raise ValueError(
                    "bounded analysis may be attached only after the concrete result "
                    "is decided")
            image = outcome.as_dict()
            previous = (current.report or {}).get("analysis")
            if previous is not None:
                if _same_envelope_value(previous, _deep_frozen(image)):
                    return True
                raise ValueError(
                    "this run already carries a different bounded-analysis outcome")
            return self._commit(
                report_update={"analysis": image}, timeout=timeout,
                invocation=self.invocation,
                _analysis_outcome=outcome)
        finally:
            self._lock.release()

    def record(self, **fields):
        """Advance the phase fields. Nothing reaches disk.

        Through `_admits` like every other advance: the progress fields carry the
        fingerprint-bound verdict summary the watchdog reads, and an unchecked summary
        would be a second authority.
        """
        with self._lock:
            nxt = self._envelope.advance(progress=fields)
            self._admits(self._envelope, nxt)
            self._envelope = nxt
            return nxt

    def begin(self):
        """This invocation's mark on its record, before anything can conclude."""
        authority = {"kind": REFUSED, "why": "the run has not finished",
                     "refusals": [], "authoritative": False}
        self._commit(
            authority=authority,
            report={"result_kind": REFUSED, "authority": authority,
                    "in_progress": True,
                    "why": "this invocation started and has not written a result yet"})

    # ── writing ──

    def commit(self, report=None, progress=None, timeout=None, invocation=None,
               report_update=None):
        """Annotate this invocation's record. The **only** public write besides the
        transitions.

        A report or telemetry, never a verdict: there is no `authority` and no
        `terminal` here, because the states are reached through the named transitions --
        `begin`, `decided`, `timeout_if_undecided` -- and a caller that could pass an
        authoritative record as a dictionary could pass one nothing produced. That was
        the reproduction: a hand-built `{"kind": "concrete_evaluation", "passed": True}`
        with no version, no invocation and no evaluation became a terminal concrete
        result, because the machine checked fields only where they were present.

        The report still has to say what the record says -- `_admits` compares it, whole,
        against the standing authority inside the lock -- so an annotation cannot smuggle
        a different verdict in through its summary.

        ``timeout`` is for a caller that must not block -- the watchdog, whose whole
        purpose is that the main thread may be stuck. Without the lock it writes nothing.

        ``invocation``, where given, is the identity the caller believes it is updating;
        a mismatch is a `ValueError` rather than a quiet `False`.

        ``report_update`` changes selected top-level report fields under the same
        lock, sharing unchanged immutable evidence. It is mutually exclusive with
        full ``report`` replacement and has exactly the same admission checks.
        """
        return self._commit(report=report, progress=progress, timeout=timeout,
                            invocation=invocation, report_update=report_update)

    def _commit(self, authority=None, report=None, progress=None, timeout=None,
                invocation=None, terminal=None, _analysis_outcome=None,
                report_update=None):
        """The write primitive every transition uses. Private: `_admits` guards what a
        legal successor envelope is, and the named transitions are the only producers of
        `authority` and `terminal`."""
        if invocation is not None and type(invocation) is not str:
            raise TypeError("an invocation identity must be an exact string")
        if invocation is not None and invocation != self.invocation:
            raise ValueError(
                f"this update names run {invocation[:8]} and this store is "
                f"{self.invocation[:8]}: a record belongs to the invocation that opened "
                f"it, and one invocation does not write another's")

        got = (self._lock.acquire() if timeout is None
               else self._lock.acquire(timeout=timeout))
        if not got:
            return False
        try:
            with self._across_processes(timeout) as held:
                if not held:
                    return False
                nxt = self._envelope.advance(authority=authority, report=report,
                                             progress=progress, terminal=terminal,
                                             report_update=report_update)
                # A JSON object with the public fields of an AnalysisOutcome is not
                # evidence that the typed record was ever constructed.  The generic
                # report writer used to be able to introduce such an object directly;
                # matching the concrete fingerprint was enough to persist an empty,
                # fabricated classification.  Only `attach_analysis` carries the exact
                # closed record across this boundary.  Later report annotations may
                # preserve the already-latched image but cannot introduce another one.
                previous_analysis = (self._envelope.report or {}).get("analysis")
                next_analysis = (nxt.report or {}).get("analysis")
                if previous_analysis is None and next_analysis is not None:
                    from .core.outcome import AnalysisOutcome

                    if type(_analysis_outcome) is not AnalysisOutcome \
                            or not _same_envelope_value(
                                _deep_frozen(_analysis_outcome.as_dict()), next_analysis):
                        raise ValueError(
                            "a bounded-analysis record may be introduced only by "
                            "attach_analysis() with the exact typed AnalysisOutcome")
                elif _analysis_outcome is not None:
                    raise ValueError(
                        "the typed analysis handoff is only for the first attached "
                        "bounded-analysis record")
                # **Checked in the lock, over the whole next envelope.** Comparing only
                # the two arguments of this call left everything a call did not mention
                # unchecked: `commit(report=B)` after a decided `A` wrote one record
                # holding `authority = A` and `report.authority = B`, and a second
                # `decided` replaced a terminal verdict outright.
                self._admits(self._envelope, nxt)
                # The invocation's own document first: it is the authoritative one, it is
                # inside a directory only this run writes, and a crash after it leaves a
                # complete record whatever the shared views say.
                write_json_atomically(self.result_path, nxt._record_fields(),
                                      default=_json_mapping, record_arrays=True)
                self._envelope = nxt
                self._advance_shared(nxt)
                return True
        finally:
            self._lock.release()

    def _admits(self, current, nxt):
        """Whether `nxt` is a legal successor of `current`. Raises `ValueError` if not.

        The state machine. Every rule is over the **whole next envelope**, in the lock --
        a rule that reads only what one call mentioned cannot see what the call left
        behind -- and the rules are fail-closed: a terminal record is refused for what it
        is missing, not only for what it contradicts. "Check the fields where present"
        was the reproduction: a hand-built authority with no version, no invocation and
        no evaluation satisfied every conditional check and became a terminal concrete
        result.

        1. **Terminal latches.** False to true once, never back.
        2. **A decided record is fixed.** An identical write is idempotent; any other is
           refused. The report may still gain telemetry.
        3. **A terminal authority is complete.** It names a kind this build has, carries
           this build's `RESULT_VERSION` and this process's `INVOCATION`, states
           `authoritative` as the kind defines it, and carries `passed` exactly where it
           is authoritative.
        4. **One record, not two.** Wherever an authority stands, a report carries
           `result_kind` and its fingerprint-bound summary, and both equal it -- and the
           progress summary, which exists for the watchdog, equals it too on a terminal
           record.
        5. **This process, this build.** A record naming another `RESULT_VERSION` or
           another process's `INVOCATION` is not this run's to write.
        6. **The score appears exactly where there is one, and it is the evidence's.**
           A terminal authoritative record carries its serialized evaluation, over at
           least one run, with `passed` a boolean equal to the evaluation's own; a
           concrete record's report and progress carry `passed` equal to the record's;
           any other kind carries none at all, in any namespace.
        7. **A terminal record is whole.** It carries its report; a verdict with no
           account of the run behind it is refused.
        """
        from .authority import INVOCATION, RESULT_VERSION, Outcome

        # The envelope has already validated/detached every field. Read that
        # immutable graph directly: a small progress update must not thaw and
        # refreeze the entire evaluation and all analysis witnesses to inspect
        # their headers. Independently supplied replacements still enter via
        # `_record_field` and undergo full value comparison and fingerprinting.
        empty = FrozenValueMap(())
        outer = nxt.authority or empty
        report = nxt.report or empty
        inner = report.get("authority") or empty
        progress = nxt.progress
        expected_inner = FrozenValueMap(
            (key, value) for key, value in outer.pairs() if key != "evaluation")
        # A telemetry-only successor shares the exact immutable authority
        # already admitted by this store. Its evidence bytes and fingerprint
        # cannot have changed. New/replaced authorities still cross the full
        # carrier and fingerprint checks below.
        fingerprint = (outer.get("evaluation_fingerprint")
                       if current.terminal and outer is current.authority else None)

        # Bounded analysis is a report annotation bound to this exact evaluation.  It
        # never gets a verdict namespace of its own and, once present, cannot be removed
        # or replaced by a later full-report commit.
        previous_analysis = (current.report or {}).get("analysis")
        analysis = report.get("analysis")
        if previous_analysis is not None and not _same_envelope_value(
                analysis, previous_analysis):
            raise ValueError(
                "a bounded-analysis outcome is already attached to this run and a "
                "later report may not replace or remove it")
        if analysis is not None:
            expected_keys = {"verdict", "why", "per_goal", "evidence", "concrete"}
            if type(analysis) is not FrozenValueMap or set(analysis) != expected_keys:
                raise ValueError(
                    "the report's analysis field is not a serialized AnalysisOutcome")
            from .core.outcome import Verdict

            try:
                Verdict(analysis.get("verdict"))
            except (TypeError, ValueError):
                raise ValueError(
                    "the report's analysis field names no analysis verdict this "
                    "build defines") from None
            if type(analysis.get("why")) is not str \
                    or type(analysis.get("per_goal")) is not FrozenValueMap \
                    or type(analysis.get("evidence")) is not FrozenValueMap:
                raise ValueError(
                    "the report's analysis field has the wrong public record shape")
            evaluation = outer.get("evaluation")
            if type(evaluation) is not FrozenValueMap:
                raise ValueError(
                    "bounded analysis must be attached beside the concrete evaluation "
                    "it names")
            if fingerprint is None:
                fingerprint = hashlib.sha256(
                    canonical_json(evaluation).encode()).hexdigest()
            if analysis.get("concrete") != fingerprint:
                raise ValueError(
                    "the bounded-analysis outcome names a different concrete "
                    "evaluation")

        # 1. terminal latches
        if current.terminal and not nxt.terminal:
            raise ValueError(
                "this run's result is decided, and a decided result does not become "
                "undecided; the timeout path annotates such a record instead")
        # 2. a decided record is fixed
        if current.terminal and not _same_envelope_value(
                outer, current.authority or empty):
            was = current.authority or empty
            differ = sorted(k for k in set(was) | set(outer)
                            if was.get(k) != outer.get(k))
            raise ValueError(
                f"this run's result is already decided as {was.get('kind')!r} and this "
                f"commit would change it to {outer.get('kind')!r} (differing on "
                f"{differ[:4]}). A second decision is telemetry beside the first, not a "
                f"replacement for it")
        # 3. a terminal authority is complete -- required, not checked-where-present
        if nxt.terminal:
            try:
                kind = Outcome(outer.get("kind"))
            except ValueError:
                raise ValueError(
                    f"a terminal record names a kind this build has, and "
                    f"{outer.get('kind')!r} is not one") from None
            if outer.get("version") != RESULT_VERSION:
                raise ValueError(
                    f"a terminal record carries the build version that produced it; "
                    f"this one carries {outer.get('version')!r} and this build states "
                    f"{RESULT_VERSION!r}")
            if outer.get("invocation") != INVOCATION:
                raise ValueError(
                    f"a terminal record carries the process that produced it; this one "
                    f"carries {outer.get('invocation')!r} and this process is "
                    f"{INVOCATION!r}")
            if outer.get("authoritative") is not kind.authoritative:
                raise ValueError(
                    f"a record of kind {kind.value!r} "
                    f"{'is' if kind.authoritative else 'is not'} authoritative, and "
                    f"this one says {outer.get('authoritative')!r}")
            if kind.authoritative:
                # The score is the evaluation's, serialized beside it. "Carries a
                # `passed`" was satisfiable by a record with no evaluation at all --
                # correct version, correct invocation, a bare boolean where the
                # evidence should be.
                evidence = outer.get("evaluation")
                if type(evidence) is not FrozenValueMap or not evidence.get("results"):
                    raise ValueError(
                        "an authoritative record carries the evaluation that "
                        "establishes it, over at least one run; this one carries "
                        "a score and no evidence")
                if type(outer.get("passed")) is not bool \
                        or type(evidence.get("passed")) is not bool \
                        or outer["passed"] != evidence.get("passed"):
                    raise ValueError(
                        f"an authoritative record's score is its evaluation's: the "
                        f"record says passed={outer.get('passed')!r} and the "
                        f"evaluation inside it says {evidence.get('passed')!r}")
                if fingerprint is None:
                    fingerprint = hashlib.sha256(
                        canonical_json(evidence).encode()).hexdigest()
                if outer.get("evaluation_fingerprint") != fingerprint:
                    raise ValueError(
                        "an authoritative record's evaluation fingerprint must name "
                        "the complete evaluation stored beside it")
        # 4. one record, not two. The report's own two statements first -- they have to
        # agree even before `begin` has put a standing record here -- then both against
        # the record beside them, wherever one stands.
        if report and inner and report.get("result_kind") != inner.get("kind"):
            raise ValueError(
                f"this report says {report.get('result_kind')!r} and the authoritative "
                f"record inside it says {inner.get('kind')!r}")
        if outer and report:
            if report.get("result_kind") != outer.get("kind"):
                raise ValueError(
                    f"this report says {report.get('result_kind')!r} and the run's "
                    f"authoritative record says {outer.get('kind')!r}")
            if not _same_envelope_value(inner, expected_inner):
                differ = sorted(k for k in set(expected_inner) | set(inner)
                                if expected_inner.get(k) != inner.get(k))
                raise ValueError(
                    f"the authoritative record and the summary inside this report "
                    f"differ on {differ[:4]}; the summary must name the exact same "
                    f"verdict and evaluation fingerprint")
        if nxt.terminal and outer:
            if "result_kind" in progress \
                    and progress["result_kind"] != outer.get("kind"):
                raise ValueError(
                    f"the progress fields say {progress['result_kind']!r} and the "
                    f"record says {outer.get('kind')!r}; the copy the watchdog reads "
                    f"is not a second authority")
            progress_authority = progress.get("authority")
            if progress_authority is not None and not _same_envelope_value(
                    progress_authority, expected_inner):
                raise ValueError(
                    "the progress fields carry an authority summary that is not the "
                    "run's; the copy the watchdog reads is not a second authority")
        # 6a. and a record that is not authoritative does not carry a score **itself**,
        # terminal or not: `as_dict` never writes one, so a `passed` inside a partial or
        # refused authority is a hand that has been in the record.
        if outer and not outer.get("authoritative") and "passed" in outer:
            raise ValueError(
                f"a {outer.get('kind')!r} record carries no score, and this one "
                f"carries passed={outer['passed']!r}")
        # 6. the score appears exactly where there is one, and it is the record's.
        # `result_kind` and the authority copy were compared and the top-level `passed`
        # beside them was not -- and the top level is what `sweep_dataset`,
        # `measure_authority` and `verdict_line` actually read. A real concrete pass
        # committed with `report["passed"] = False` was accepted whole, and the record
        # then said PASS in its authority and FAIL on every consumer's screen.
        if outer:
            score = outer.get("passed")
            if outer.get("authoritative"):
                if report and (
                        type(report.get("passed")) is not bool
                        or report["passed"] != score):
                    raise ValueError(
                        f"a concrete report carries the record's score: the record "
                        f"says passed={score!r} and this report says "
                        f"{report.get('passed', '<absent>')!r}")
                if nxt.terminal and (
                        type(progress.get("passed")) is not bool
                        or progress["passed"] != score):
                    raise ValueError(
                        f"the progress fields say passed="
                        f"{progress.get('passed', '<absent>')!r} and the record says "
                        f"{score!r}; the copy the watchdog reads is not a second score")
            else:
                if report and "passed" in report:
                    raise ValueError(
                        f"only a concrete evaluation carries a score, and this "
                        f"{outer.get('kind')!r} report carries "
                        f"passed={report['passed']!r}")
                if "passed" in progress:
                    raise ValueError(
                        f"only a concrete evaluation carries a score, and the progress "
                        f"fields of this {outer.get('kind')!r} record carry one")
        # 7. a terminal record is whole: its report is in it. The primitive could be
        # driven straight to a terminal state with no `begin` and no report, and the
        # record then had a verdict and no account of the run it concluded.
        if nxt.terminal and not report:
            raise ValueError(
                "a terminal record carries the run's report beside its verdict; one "
                "with a verdict and no report is not an account of anything")
        # 5. this process, this build
        for got in (outer, inner):
            if got.get("version") and got["version"] != RESULT_VERSION:
                raise ValueError(
                    f"this record was written under {got['version']!r} and this build "
                    f"states {RESULT_VERSION!r}")
            if got.get("invocation") and got["invocation"] != INVOCATION:
                raise ValueError(
                    "this record was produced by another process "
                    f"({got['invocation']} not {INVOCATION})")

    def _advance_shared(self, envelope):
        """Advance shared views in start-time order, under both locks.

        Preserve the timestamp's full precision in every record and comparison.
        UUID order breaks only an exact timestamp tie; rounding would let a late
        writer supersede a distinct later invocation within the same millisecond.
        """
        current = _read_json(self.pointer_path)
        if current:
            theirs = (float(current.get("run_started_at") or 0.0),
                      str(current.get("run_invocation") or ""))
            mine = (envelope.started, envelope.invocation)
            if theirs > mine:
                return False
        pointer = {"record_version": RECORD_VERSION,
                   "run_invocation": envelope.invocation,
                   "run_started_at": envelope.started,
                   "run_sequence": envelope.sequence,
                   "result": f"runs/{envelope.invocation}/result.json"}
        write_json_atomically(self.pointer_path, pointer)
        if envelope.authority is not None:
            write_json_atomically(self.authority_path,
                                  envelope._stamped_fields(envelope.authority),
                                  default=_json_mapping, record_arrays=True)
        if envelope.report is not None:
            write_json_atomically(self.report_path, envelope._stamped_fields(envelope.report),
                                  default=_json_mapping, record_arrays=True)
        return True

    @contextlib.contextmanager
    def _across_processes(self, timeout=None):
        """The problem directory's own lock, held for one transaction.

        `flock`, so it is the same lock for a second process, and the kernel releases it
        if this process dies holding it. A caller that must not block takes it without
        waiting and is told it did not get it.
        """
        import fcntl

        self.problem_dir.mkdir(parents=True, exist_ok=True)
        with open(self._gate, "w") as handle:
            if timeout is None:
                fcntl.flock(handle, fcntl.LOCK_EX)
                held = True
            else:
                deadline = time.time() + float(timeout)
                held = False
                while time.time() < deadline:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        held = True
                        break
                    except OSError:
                        time.sleep(defaults.SIM_RESULT_LOCK_POLL_SECONDS)
            yield held
            if held:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def decided(self, authoritative, timeout=None):
        """One authoritative result, into this run's record and both views at once.

        The envelope becomes terminal here: everything after a decided result is
        telemetry, and `timeout_if_undecided` is the other half of that rule.

        Takes the `Authoritative` object, never a dictionary: `as_dict` is what stamps
        the record with this build's `RESULT_VERSION` and this process's `INVOCATION`,
        and a caller that could hand over a dictionary could hand over one nothing
        produced. `_admits` re-checks the stamped record anyway -- two locks on one door,
        because this is the door.
        """
        # Exact type, and the type is sealed (`Authoritative.__init_subclass__`). The
        # two are one rule from two sides: `isinstance` took a subclass, and a normally
        # initialised subclass over a genuinely failing evaluation persisted
        # `passed: True` by overriding `passed` and `as_dict` -- the checks inherited,
        # the answers rewritten.
        if type(authoritative) is not Authoritative:
            raise TypeError(
                f"decided() takes the Authoritative result itself, not "
                f"{type(authoritative).__name__}: the record it writes is stamped with "
                f"the build version and process identity by that exact type, and "
                f"anything else carries whatever its author put in it")
        record = authoritative.as_dict()
        summary = _authority_summary(record)
        progress = {"result_kind": authoritative.kind,
                    "authority": summary,
                    "run_invocation": self.invocation,
                    "score_decided_before_any_timeout": True}
        if authoritative.authoritative:
            progress["passed"] = authoritative.passed
        got = (self._lock.acquire() if timeout is None
               else self._lock.acquire(timeout=timeout))
        if not got:
            return False
        try:
            current = self._envelope
            if current.terminal:
                # **A second decision is telemetry beside the first, not a replacement.**
                # Reachable from production: the admission gate decides a refusal, and
                # with `smoke_test=True` the run continues, so a later toolchain or port
                # failure reaches `refused_early` and here again. An identical record is
                # nothing to do; a different one is recorded under its own key, where a
                # reader can see that something else also went wrong without the run's
                # decided result having moved.
                # Both sides normalised the same way: the standing record went
                # through the envelope's freeze (tuples) and back (lists), and a
                # freshly serialised one has not -- comparing them raw made an
                # identical decision look like a second one.
                if _same_record_value(
                        _plain(current.authority or {}), record):
                    return True
                later = list(_plain(current.report or {}).get("later_decisions") or ())
                later.append({"kind": authoritative.kind, "why": authoritative.why,
                              "refusals": [r.as_dict() for r in authoritative.refusals]})
                report = _plain(current.report or {})
                report["later_decisions"] = later
                self._commit(report=report,
                             progress={"later_decisions": len(later)},
                             timeout=timeout, invocation=self.invocation)
                return False
            report = _plain(current.report or {})
            report.pop("in_progress", None)
            report.update({k: v for k, v in progress.items()
                           if k != "score_decided_before_any_timeout"})
            report["why"] = authoritative.why
            return self._commit(authority=record, report=report, progress=progress,
                                timeout=timeout, terminal=True)
        finally:
            self._lock.release()

    def timeout_if_undecided(self, run_invocation, findings,
                           timeout=defaults.SIM_RESULT_LOCK_SECONDS):
        """The wall clock expired. One atomic transition. `(wrote, state)`.

        The caller passes **no view of the run**: only what the kill measured and the run
        identifier it thinks it is killing. Everything else is read here, under the lock,
        at the moment of writing -- the previous shape took `dict(_progress)` outside the
        lock and committed a record assembled before a decision that happened in between.

        States: ``"partial"`` where the run had not concluded and this record says it was
        cut short; ``"decided"`` where it had, and the expiry is recorded as an
        annotation beside a verdict that stands; ``"not-mine"`` where the lock could not
        be had inside ``timeout``.
        """
        if type(run_invocation) is not str:
            raise TypeError("a timeout invocation identity must be an exact string")
        if run_invocation != self.invocation:
            raise ValueError(
                f"this timeout names run {run_invocation[:8]} and this store is "
                f"{self.invocation[:8]}")
        got = self._lock.acquire(timeout=timeout)
        if not got:
            return (False, "not-mine")
        try:
            current = self._envelope
            fields = _plain(_record_field(findings, "timeout findings"))
            fields["hard_timeout"] = True
            if current.terminal:
                fields["partial"] = False
                fields["interrupted_after_the_result"] = True
                wrote = self._commit(
                    report_update={k: v for k, v in fields.items()
                                   if k not in ("authority", "result_kind")},
                    progress=fields, timeout=timeout, invocation=self.invocation)
                return (bool(wrote), "decided")
            fields["partial"] = True
            fields["result_kind"] = PARTIAL
            # A real `Authoritative`, so the record carries the version and invocation
            # stamps every terminal record must -- the hand-edited placeholder this
            # replaces carried neither, and the completeness rule would now refuse it.
            cut = Authoritative(PARTIAL, why=(
                "the wall clock expired before this run reached a result"))
            inner = cut.as_dict()
            fields["authority"] = inner
            report = {**_plain(current.progress), **fields}
            report["result_kind"] = PARTIAL
            report["authority"] = inner
            # Terminal: the invocation is about to be killed, and nothing legitimate
            # writes after its own death. It was left non-terminal, which meant a still
            # later writer could replace what the kill recorded.
            wrote = self._commit(authority=inner, report=report, progress=fields,
                                 timeout=timeout, invocation=self.invocation,
                                 terminal=True)
            return (bool(wrote), "partial")
        finally:
            self._lock.release()


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def read_record(problem_dir):
    """The run a reader should take: `(authority, report)`, or `None`.

    Through `latest.json` and the one document it names, so what comes back is one
    invocation's record and the two halves cannot straddle a commit. The derived views
    are read only as a fallback, for a directory written by a build that predates the
    record: there the two files are separate renames, so a mismatched `run_sequence`
    means the pair was caught mid-commit and is not a readable result yet. This
    reader does not poll; any wait belongs to its caller's lifecycle.
    """
    root = Path(problem_dir)
    pointer_path = root / "latest.json"
    pointer = _read_json(pointer_path)
    if pointer is not None or pointer_path.exists():
        if pointer and pointer.get("result"):
            record = _read_json(root / str(pointer["result"]))
            if record and record.get("authority") is not None:
                return (record["authority"], record.get("report") or {})
        # This directory has a current-format owner. An unreadable result is
        # not permission to substitute a coherent but older pair of views.
        return None
    authority = _read_json(root / "authority.json")
    report = _read_json(root / "cosim_report.json")
    if authority is None or report is None:
        return None
    if authority.get("run_sequence") == report.get("run_sequence") \
            and authority.get("run_invocation") == report.get("run_invocation"):
        return (authority, report)
    return None


def partial_after_timeout(progress):
    """Whether a record cut short by the watchdog is short of anything that decides.

    A run whose score was computed and written before the deadline is not missing its
    score; what the kill interrupted is the smoke chain, which decides nothing. The
    store's terminal envelope is the mechanism now; this remains for a reader asking the
    same question of a record on disk.
    """
    return not progress.get("score_decided_before_any_timeout", False)


def _write_record_arrays(value, stream, encode):
    """Stream a finite record using its array elements as encoding units.

    Catalogue occurrences and run results are records, not individual JSON
    punctuation tokens. Encoding each item in one standard-library call uses
    the C encoder without collecting a whole catalogue's encoded bytes. Maps
    retain their insertion order, and no item or evidence field is omitted.
    """
    if type(value) in (dict, FrozenValueMap):
        stream.write("{")
        for index, (key, item) in enumerate(value.items()):
            if index:
                stream.write(",")
            stream.write(encode(key))
            stream.write(":")
            _write_record_arrays(item, stream, encode)
        stream.write("}")
    elif type(value) in (list, tuple):
        stream.write("[")
        for index, item in enumerate(value):
            if index:
                stream.write(",")
            stream.write(encode(item))
        stream.write("]")
    else:
        stream.write(encode(value))


def write_json_atomically(path, payload, default=None, *, record_arrays=False):
    """Through a temporary file in the same directory, then rename.

    A record written in place is one a reader can catch half-finished, and a process
    killed partway leaves a truncated file under the name of a complete one -- worse
    than none, because it parses.

    The temporary name is unique per **call**, not per process: the two writers a run has
    are one process, and on a shared name whichever renamed first took the file out from
    under the other, whose `os.replace` then raised.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.partial")
    try:
        # Stream the document.  Building the entire JSON string first temporarily
        # doubled the resident size of large, but valid, structural catalogues.
        with tmp.open("w", encoding="utf-8") as handle:
            # Run records are machine evidence, not hand-edited configuration.  Compact
            # JSON keeps a large finite catalogue near its canonical size instead of
            # tripling it with indentation on every atomic rewrite.
            if record_arrays:
                # Only envelope projections use this path: their recursively
                # validated mappings have exact string keys.
                encoder = json.JSONEncoder(default=default, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False)
                _write_record_arrays(payload, handle, encoder.encode)
            else:
                json.dump(
                    payload, handle, default=default, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def verdict_line(report):
    """One line for a human: the kind, and a score only where there is one.

    `PASS`/`FAIL` under every outcome is what made a refused run and a wrong design look
    the same on a terminal.
    """
    kind = report.get("result_kind", REFUSED)
    if kind == CONCRETE:
        return "PASS" if report.get("passed") else "FAIL"
    smoke = report.get("heuristic_smoke_test") or {}
    tail = ("" if "passed" not in smoke
            else f" (smoke test: {'clean' if smoke['passed'] else 'found a mismatch'})")
    why = (report.get("authority") or {}).get("why", "")
    return f"{kind.upper()}{': ' + why if why else ''}{tail}"
