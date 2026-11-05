"""Bounded native event transport, used inside the RTL judge only.

One compiled collector belongs to one simulator process; there is no reset/rebind
operation. Public admission selects it and authenticates the complete mapping;
direct diagnostics may still opt in explicitly.
The caller brackets every native evaluation interval and seals the collector
before publishing a trace. Transport faults are verifier failures, not DUT safety
violations, empty hit vectors or candidate scores.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from functools import cached_property
from itertools import groupby
from string import Template

from .. import PACKAGE_DIR
from ..core.catalogue import CatalogueManifest, OccurrenceKey
from ..core.failure import FailureRaised, RunFailure
from ..core.record_value import _encode_json_projection, canonical_json
from ..core.refusal import Refusal, Refused
from .native_evidence_codec import events_from_bytes, pack, unpack
from .source_bindings import (
    MAX_EVENTS,
    BoundOccurrence,
    EventBindings,
    EventCoordinate,
    NativeHits,
    bind_declared_source_events,
    bind_source_events,
    bind_source_goals,
)
from .source_plan import VERSION as SOURCE_PLAN_VERSION
from .source_plan import SourcePlan
from .source_projection import SourceOutcome

VERSION = "rtl-native-events/11"
# Preserve each process as the callback's scheduling unit. Splitting a
# combinational body can hoist its input-independent callback into startup.
# This is an observation requirement, not a runtime performance preference.
# Timing support executes the compiler-derived event waits before callbacks.
# The qualified compiler preserves impure equal-arm conditions. Its ordinary
# early constant pass must remain enabled: it also establishes alias-safe
# concatenation assignments such as {x[j],x[j+1]}={x[j+1],x[j]}.
# Keep ordinary inlining (observer helpers access their owning scope), but do
# not expand helper bodies before that pass. Eager expansion of instrumented
# helpers can multiply the compiler's control-flow work before simplification.
# This neither disables constant folding nor changes source event boundaries.
# Verilator 5.051's concat-splitting optimization asserts on nested observed
# predicates with mixed-width ternary arms (V3FuncOpt: Inconsistent assignment).
# Keep the original expressions and callbacks; omit only this C++ optimization.
# The pinned compiler's gate deduplication repeatedly walks shared observer
# fan-in; statement reordering then rebuilds dense dependency graphs. Both can
# dominate compilation of guarded datapaths. Preserve the emitted assignments
# and their order instead; these are optimizations, not language/safety checks.
VERILATOR_FLAGS = ("-fno-split", "--timing", "-fno-inline-funcs-eager",
                   "-fno-func-opt-split-cat", "-fno-dedup", "-fno-reorder")
_MAX_COUNTER = (1 << 63) - 1
_TEMPLATE = PACKAGE_DIR / "templates" / "native_events.cpp.tmpl"
_U64 = ctypes.c_uint64
_ERRORS = {1: "event-outside-interval", 2: "unknown-event",
           3: "callback-budget-exceeded", 4: "interval-protocol", 5: "unknown-context"}


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("native event identities must be SHA256 digests")
    return value


def _fail(code, detail=""):
    raise FailureRaised(RunFailure.verifier("rtl-native-events", Refusal(
        "native-events-" + code, "the native observation interval is not complete",
        detail=detail)))


def check_callback_limit(value: object) -> int | None:
    if value is not None and (type(value) is not int or not 1 <= value <= _MAX_COUNTER):
        raise ValueError("native callback limit must be None or a finite positive ABI counter")
    return value


def check_scope_root(value: object) -> str:
    """The launcher's root instance name, not part of the HDL hierarchy."""
    if type(value) is not str or value and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value) is None:
        raise ValueError("native scope root must be empty or one identifier")
    return value


@dataclass(frozen=True, slots=True)
class NativeEventPlan:
    """Judge-owned compile parameters, not a candidate-provided manifest."""
    bindings: EventBindings
    build_sha256: str
    callback_limit: int | None
    scope_root: str = field(default="TOP", kw_only=True)
    identity: str = field(init=False)
    _template: str = field(init=False, repr=False)
    _evidence: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self):
        if type(self.bindings) is not EventBindings:
            raise TypeError("a native collector needs exact event bindings")
        count = self.bindings.event_count
        if type(count) is not int or not 0 <= count <= MAX_EVENTS:
            raise ValueError("native event count exceeds the collector storage bound")
        coordinates = self.bindings.coordinates
        if type(coordinates) is not tuple or coordinates and (
            len(coordinates) != count or any(
                type(c) is not EventCoordinate or type(c.scope) is not str
                or re.fullmatch(r"TOP(?:\.[A-Za-z_][A-Za-z0-9_$]*(?:\[-?\d+\])?)+", c.scope) is None
                or type(c.source_event) is not int or not 0 <= c.source_event < MAX_EVENTS
                for c in coordinates)
        ):
            raise ValueError("native event coordinates are malformed")
        pairs = [(c.scope, c.source_event) for c in coordinates]
        if pairs != sorted(set(pairs)):
            raise ValueError("native event coordinates must be ordered and unique")
        check_callback_limit(self.callback_limit)
        check_scope_root(self.scope_root)
        entries = self.bindings.occurrences
        if type(entries) is not tuple or any(
            type(item) is not BoundOccurrence
            or type(item.occurrence) is not OccurrenceKey
            or type(item.goal) is not str or not item.goal
            or type(item.event) is not int or not 0 <= item.event < count
            or type(item.ancestors) is not tuple
            or any(type(parent) is not int or not 0 <= parent < item.event
                   for parent in item.ancestors)
            or len(set(item.ancestors)) != len(item.ancestors)
            for item in entries
        ):
            raise ValueError("native occurrence bindings are malformed")
        if len({item.occurrence for item in entries}) != len(entries):
            raise ValueError("native occurrence bindings contain duplicate keys")
        if coordinates and any(coordinates[parent].scope != coordinates[item.event].scope
                               for item in entries for parent in item.ancestors):
            raise ValueError("native occurrence ancestry crosses module contexts")
        projections = self.bindings.projections
        if type(projections) is not tuple or any(
            type(item) is not SourceOutcome or type(item.occurrence) is not OccurrenceKey
            or type(item.goal) is not str or not item.goal or item.issue is not None
            or type(item.events) is not tuple
            or any(type(event) is not int or not 0 <= event < count for event in item.events)
            or tuple(sorted(set(item.events))) != item.events
            or type(item.ancestors) is not tuple or len(item.ancestors) != len(item.events)
            or any(type(parents) is not tuple or len(set(parents)) != len(parents)
                   or any(type(parent) is not int or not 0 <= parent < event for parent in parents)
                   for event, parents in zip(item.events, item.ancestors)) for item in projections
        ):
            raise ValueError("native source projections are incomplete or malformed")
        catalogue_pairs = self.bindings.catalogue_pairs
        if len({key for key, _ in catalogue_pairs}) != len(catalogue_pairs):
            raise ValueError("native source projections contain duplicate occurrence keys")
        if coordinates and any(coordinates[parent].scope != coordinates[event].scope
                               for item in projections
                               for event, parents in zip(item.events, item.ancestors)
                               for parent in parents):
            raise ValueError("native source projection ancestry crosses module contexts")
        _digest(self.build_sha256)
        template = _TEMPLATE.read_text()
        # All fields above have been checked, and this projection is constructed
        # here from exact immutable records. Encode it directly: projecting it
        # again copies the entire occurrence map without adding a check.
        identity = _sha(_encode_json_projection({
            "version": VERSION, "build": self.build_sha256,
            "collector": _sha(template), "callback_limit": self.callback_limit,
            "event_count": count,
            "coordinates": pairs, "scope_root": self.scope_root,
            "occurrences": [{"key": item.occurrence.as_dict(), "goal": item.goal,
                             "event": item.event, "ancestors": item.ancestors}
                            for item in self.bindings.occurrences],
            "projections": [_projection_image(item) for item in projections],
        }))
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "_template", template)

    @property
    def word_count(self):
        return max(1, (self.bindings.event_count + 63) // 64)

    @property
    def callback_count_max(self) -> int:
        """Representation ceiling, or a smaller explicitly requested quota."""
        return _MAX_COUNTER if self.callback_limit is None else self.callback_limit

    def collector_source(self):
        # TOP is the binding language's logical root. C++ launchers commonly
        # instantiate it as "TOP"; cocotb instantiates it as "". Translate only
        # that explicit root, never an arbitrary prefix/suffix of a callback.
        prefix = self.scope_root + "." if self.scope_root else ""
        scopes, offset = [], 0
        for name, members in groupby(self.bindings.coordinates, key=lambda coordinate: coordinate.scope):
            end = offset + sum(1 for _ in members)
            scopes.append("    {" + json.dumps(prefix + name.removeprefix("TOP."))
                          + f", {offset}, {end}" + "}")
            offset = end
        return Template(self._template).substitute(
            identity=self.identity, event_count=self.bindings.event_count,
            callback_limit=self.callback_count_max,
            has_contexts=int(bool(self.bindings.coordinates)),
            scope_count=len(scopes), scope_rows=",\n".join(scopes),
            source_events=", ".join(str(c.source_event) for c in self.bindings.coordinates))

    def check_catalogue(self, catalogue: CatalogueManifest) -> None:
        """Bind to the original frontend, not a new instrumented denominator.

        Equal public goals alone do not establish equal elaborated occurrences.
        In particular, flattening or source-file renaming must not be repaired by
        relabeling a decoder after compilation.
        """
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("native observation needs an exact frontend catalogue")
        pairs = self.bindings.catalogue_pairs
        catalogue.reconcile_occurrences(pairs, "native-events", exhaustive=True)
        if {goal for _key, goal in pairs} != set(catalogue):
            raise Refused(Refusal(
                "native-events-catalogue-goal-mismatch",
                "every declared structural goal needs an observed occurrence"))

    def as_dict(self) -> dict:
        """Closed data carried from the judge; never a pickle or a syntax tree."""
        return {
            "version": VERSION, "identity": self.identity,
            "build_sha256": self.build_sha256, "callback_limit": self.callback_limit,
            "event_count": self.bindings.event_count,
            "coordinates": [[c.scope, c.source_event] for c in self.bindings.coordinates],
            "scope_root": self.scope_root,
            "occurrences": [{"key": item.occurrence.as_dict(), "goal": item.goal,
                             "event": item.event, "ancestors": list(item.ancestors)}
                            for item in self.bindings.occurrences],
            "projections": [_projection_image(item) for item in self.bindings.projections],
        }

    @property
    def evidence_image(self) -> str:
        """Self-contained mapping, encoded once per immutable prepared plan."""
        if self._evidence is None:
            object.__setattr__(self, "_evidence", pack(_encode_json_projection(self.as_dict()).encode()))
        return self._evidence

    @classmethod
    def from_evidence(cls, image: str, *, expected: NativeEventPlan | None = None) -> NativeEventPlan:
        # Compare the entire mapping, not just an identity supplied by the child.
        # Equal bytes let the host reuse its already validated plan and indexes.
        if expected is not None and image == expected.evidence_image:
            return expected
        data = unpack(image)
        if expected is not None:
            if data != _encode_json_projection(expected.as_dict()).encode():
                raise ValueError("native evidence names a different prepared collector")
            return expected
        value = json.loads(data)
        if canonical_json(value).encode() != data:
            raise ValueError("native plan evidence is not canonical JSON")
        plan = cls.from_dict(value)
        # The complete image was just validated. Retain it so the subsequent
        # interval reader can reuse this plan without encoding the map again.
        object.__setattr__(plan, "_evidence", image)
        return plan

    @classmethod
    def from_dict(cls, value: object) -> NativeEventPlan:
        """Validate shape and local collector identity, not compiler correctness.

        The caller must authenticate the tool execution and request that produced
        this image. A self-consistent candidate-provided image is not authority.
        """
        if type(value) is not dict or set(value) != {
            "version", "identity", "build_sha256", "callback_limit", "event_count",
            "occurrences", "coordinates", "scope_root", "projections",
        } or value["version"] != VERSION or type(value["occurrences"]) is not list:
            raise ValueError("invalid native plan image")
        coordinates = value["coordinates"]
        if type(coordinates) is not list or any(type(c) is not list or len(c) != 2 for c in coordinates):
            raise ValueError("invalid native event coordinates")
        entries = []
        for row in value["occurrences"]:
            if type(row) is not dict or set(row) != {"key", "goal", "event", "ancestors"}:
                raise ValueError("invalid native occurrence image")
            key = row["key"]
            if type(key) is not dict or set(key) != {"provider", "path", "role"} \
                    or type(key["path"]) is not list or type(row["ancestors"]) is not list:
                raise ValueError("invalid native occurrence key or ancestry")
            entries.append(BoundOccurrence(OccurrenceKey(**key), row["goal"],
                                           row["event"], tuple(row["ancestors"])))
        if type(value["projections"]) is not list:
            raise ValueError("invalid native source projections")
        projections = []
        for row in value["projections"]:
            if type(row) is not dict or set(row) != {"key", "goal", "events", "ancestors"} \
                    or type(row["key"]) is not dict or set(row["key"]) != {"provider", "path", "role"} \
                    or type(row["key"]["path"]) is not list or type(row["events"]) is not list \
                    or type(row["ancestors"]) is not list \
                    or any(type(parents) is not list for parents in row["ancestors"]):
                raise ValueError("invalid native source projection")
            projections.append(SourceOutcome(OccurrenceKey(**row["key"]), row["goal"], tuple(row["events"]),
                                             ancestors=tuple(tuple(parents) for parents in row["ancestors"])))
        plan = cls(EventBindings(value["event_count"], tuple(entries),
                                 tuple(EventCoordinate(*c) for c in coordinates), tuple(projections)),
                   value["build_sha256"], value["callback_limit"], scope_root=value["scope_root"])
        if plan.identity != value["identity"]:
            raise ValueError("native plan identity does not match its image/runtime")
        return plan


def prepare_native_events(plan: SourcePlan, original: str, annotated: str, *,
                          filename: str, toolchain_sha256: str,
                          execution_sha256: str, callback_limit: int | None,
                          catalogue_sha256: str | None = None,
                          scoped: str | None = None, scope_root: str = "TOP",
                          source_goals: bool = False, source_schemas=None) -> NativeEventPlan:
    """Authenticate the full mapping before generating a collector.

    The toolchain digest names the locked compiler/runtime environment; the
    execution digest names the scheduled execution policy. Both are supplied by
    the judge, not inferred from the candidate's HDL or a native counter dump.
    A supplied catalogue digest also binds its schema and policy. Without it,
    preparation is unbound diagnostic evidence, not production admission.
    """
    bindings = bind_source_events(plan, original, annotated, filename=filename, scoped=scoped)
    if source_goals:
        bindings = bind_source_goals(plan, bindings, filename=filename, schemas=source_schemas)
    return _native_plan(plan, bindings, filename=filename,
                        toolchain_sha256=toolchain_sha256, execution_sha256=execution_sha256,
                        callback_limit=callback_limit, catalogue_sha256=catalogue_sha256,
                        scope_root=scope_root, witness={
                            "rtlil": _sha(original), "compiler_witness": _sha(annotated),
                            "context_witness": None if scoped is None else _sha(scoped),
                            "source_goals": source_goals,
                            "source_schemas": None if source_schemas is None else sorted(source_schemas)})


def prepare_declared_native_events(plan: SourcePlan, *, filename: str,
                                  toolchain_sha256: str, execution_sha256: str,
                                  callback_limit: int | None, catalogue_sha256: str | None = None,
                                  scope_root: str = "TOP", source_schemas=None) -> NativeEventPlan:
    """Prepare source observation without requesting a synthesis relation."""
    bindings = bind_declared_source_events(plan, filename=filename, schemas=source_schemas)
    return _native_plan(plan, bindings, filename=filename,
                        toolchain_sha256=toolchain_sha256, execution_sha256=execution_sha256,
                        callback_limit=callback_limit, catalogue_sha256=catalogue_sha256,
                        scope_root=scope_root, witness={"declarations": asdict(plan.declarations),
                            "source_schemas": None if source_schemas is None else sorted(source_schemas)})


def _native_plan(plan, bindings, *, filename, toolchain_sha256, execution_sha256,
                 callback_limit, catalogue_sha256, scope_root, witness):
    image = {
        "version": VERSION, "source_plan": SOURCE_PLAN_VERSION,
        "coordinates": "iterations" if plan.enumerate_iterations else "source-occurrences",
        "verilator_flags": VERILATOR_FLAGS,
        "source": plan.source_sha256, "native": _sha(plan.native(scoped=bool(bindings.coordinates)).text),
        "witness": witness,
        "filename": filename,
        "toolchain": _digest(toolchain_sha256), "execution": _digest(execution_sha256),
        "catalogue": None if catalogue_sha256 is None else _digest(catalogue_sha256),
        "events": [asdict(event) for event in plan.events],
    }
    return NativeEventPlan(bindings, _sha(canonical_json(image)), callback_limit, scope_root=scope_root)


def _projection_image(item):
    return {"key": item.occurrence.as_dict(), "goal": item.goal, "events": list(item.events),
            "ancestors": [list(parents) for parents in item.ancestors]}


@dataclass(frozen=True)
class NativeInterval:
    sequence: int
    callbacks: int
    bits: bytes
    goal_bits: bytes
    _bindings: EventBindings = field(repr=False)

    @cached_property
    def events(self) -> frozenset[int]:
        return events_from_bytes(self.bits)

    @cached_property
    def hits(self) -> NativeHits:
        # Named views are for diagnostics, not the live bitmap path. The same
        # ancestor checks already ran in decode_bitmap before publication.
        return self._bindings.decode(self.events)


class NativeEventCollector:
    """One decoder for both Driver layouts; no simulator or DUT access here.

    ``library`` is an already loaded, judge-built library. Production uses the
    current simulator process. Supplying a library is useful for isolated ABI
    tests; this class never compiles or loads candidate-provided paths.
    """

    def __init__(self, plan: NativeEventPlan, *, library=None):
        if type(plan) is not NativeEventPlan:
            raise TypeError("a collector requires its exact native event plan")
        self.plan = plan
        self._sequence = 0
        self._active = False
        self._finished = False
        self._failed = False
        try:
            self._library = ctypes.CDLL(None) if library is None else library
            identity = self._library.behave_native_events_identity_v1
            identity.argtypes, identity.restype = [], ctypes.c_char_p
            self._begin = self._library.behave_native_events_begin_v1
            self._begin.argtypes, self._begin.restype = [_U64], ctypes.c_int
            self._end = self._library.behave_native_events_end_v1
            self._end.argtypes = [_U64, ctypes.POINTER(_U64), _U64, ctypes.POINTER(_U64)]
            self._end.restype = ctypes.c_int
            self._finish = self._library.behave_native_events_finish_v1
            self._finish.argtypes, self._finish.restype = [], ctypes.c_int
            self._checkpoint = self._library.behave_native_events_checkpoint_v1
            self._checkpoint.argtypes, self._checkpoint.restype = [_U64], ctypes.c_int
        except (OSError, AttributeError) as exc:
            _fail("collector-unavailable", str(exc))
        if identity() != plan.identity.encode("ascii"):
            _fail("collector-identity-mismatch")
        self._words = (_U64 * plan.word_count)()

    def _check(self, status):
        if status:
            self._failed = True
            _fail(_ERRORS.get(status, "unknown-collector-status"))

    def _require(self, condition):
        if self._failed or not condition:
            self._failed = True
            _fail("interval-protocol")

    def begin(self):
        self._require(not self._active and not self._finished)
        self._check(self._begin(self._sequence))
        self._active = True

    def end(self) -> NativeInterval:
        self._require(self._active and not self._finished)
        callbacks = _U64()
        self._check(self._end(self._sequence, self._words, len(self._words),
                              ctypes.byref(callbacks)))
        data = (bytes(self._words) if sys.byteorder == "little" else
                b"".join(word.to_bytes(8, "little") for word in self._words))
        word = int.from_bytes(data, "little")
        if (not word.bit_count() <= callbacks.value <= self.plan.callback_count_max
                or bool(word) != bool(callbacks.value)):
            self._failed = True
            _fail("invalid-callback-count")
        try:
            # Check the entire ABI buffer before slicing its word padding.
            if word.bit_length() > self.plan.bindings.event_count:
                raise ValueError("unknown native source event")
            bits = data[:(self.plan.bindings.event_count + 7) // 8]
            goal_bits = self.plan.bindings.decode_bitmap(bits)
        except ValueError as exc:
            self._failed = True
            _fail("invalid-event-vector", str(exc))
        result = NativeInterval(self._sequence, callbacks.value, bits, goal_bits,
                                self.plan.bindings)
        self._sequence += 1
        self._active = False
        return result

    def finish(self):
        """Seal after the last settled interval, before publishing any trace."""
        self._require(not self._active and not self._finished)
        self._check(self._finish())
        self._finished = True

    def checkpoint(self):
        """Check the settled fence without ending the collector's lifetime."""
        self._require(not self._active and not self._finished)
        self._check(self._checkpoint(self._sequence))
