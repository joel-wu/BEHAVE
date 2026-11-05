"""Scheduled native intervals and their durable, occurrence-complete evidence.

The Driver supplies phase boundaries, never event IDs or predicted hits. Startup
is separate. A complete step includes control settling, the active clock batch,
and the clock return: a negative-edge process is real execution, not a gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.catalogue import CatalogueManifest
from ..core.failure import FailureRaised, RunFailure
from ..core.hit_vector import HitVectorCodec
from ..core.refusal import Refusal
from .execution_policy import execution_policy
from .native_events import NativeEventCollector, NativeEventPlan, NativeInterval
from .native_evidence_codec import event_bytes, events_from_bytes, pack
from .source_bindings import NativeHits

VERSION = "rtl-native-observation/3"
PREFIX_VERSION = "rtl-native-prefix/3"
CHECKPOINT_VERSION = "rtl-native-checkpoint/1"


def _fail(detail):
    raise FailureRaised(RunFailure.verifier("rtl-native-observation", Refusal(
        "native-events-phase-incomplete", "native observation has no complete scheduled trace",
        detail=detail)))


def _events(value, count):
    return events_from_bytes(event_bytes(value, count))


def _legacy_events(value, count):
    """Read persisted v1 evidence; new executions only emit the compact v2 form."""
    return events_from_bytes(_legacy_bytes(value, count))


def _legacy_bytes(value, count):
    if type(value) is not str or len(value) != 2 * ((count + 7) // 8):
        raise ValueError("native event bitset has the wrong width")
    data = bytes.fromhex(value)
    if data.hex() != value or count % 8 and data[-1] >> (count % 8):
        raise ValueError("native event bitset is not canonical")
    return data


def _union(left: NativeHits, right: NativeHits) -> NativeHits:
    if not left.occurrences and not left.goals:
        return right
    if not right.occurrences and not right.goals or left is right:
        return left
    return NativeHits(left.occurrences | right.occurrences, left.goals | right.goals)


def _union_bits(left: bytes, right: bytes) -> bytes:
    if len(left) != len(right):
        raise ValueError("native phases have different goal bitmap widths")
    if left == right:
        return left
    # Fixed-width byte strings are the public carrier. Do the word-wise OR in
    # native integer code rather than iterating every catalogue byte in Python.
    return (int.from_bytes(left, "little") | int.from_bytes(right, "little")).to_bytes(
        len(left), "little")


class NativeObservation:
    """One process, one stimulus, one non-resettable phase machine.

    Construct before the first simulator yield, then call start_slots, sampled,
    clocked, successor, next_slot in order. A successor exists only after the
    inactive edge settles. No evidence is publishable before the final fence.
    """

    def __init__(self, plan: NativeEventPlan, stimulus, *, library=None):
        self.plan = plan
        self.stimulus = stimulus
        self._collector = NativeEventCollector(plan, library=library)
        self._collector.begin()
        self._phase = "startup"
        self._slot = 0
        self._rows = []
        self._last_rows = {}
        self._control = None
        self._codec = HitVectorCodec(plan.bindings.goal_catalogue)
        self._last_goal_bits = None
        self._last_vector = None

    @property
    def catalogue(self):
        return self._codec.catalogue

    @property
    def active(self):
        return self._phase in {"startup", "control", "clock", "advance"}

    @property
    def interruptible(self):
        return self.active or self._phase == "between"

    def _end(self, phase) -> NativeInterval:
        if self._phase != phase:
            self._phase = "failed"
            _fail("out-of-order " + phase)
        # A transport/decoding failure also poisons this Python phase machine.
        self._phase = "failed"
        result = self._collector.end()
        previous = self._last_rows.get(phase)
        if previous is not None and previous[:2] == (result.bits, result.callbacks):
            row = previous[2]
        else:
            row = {"phase": phase, "callbacks": result.callbacks,
                   "events": pack(result.bits)}
            self._last_rows[phase] = (result.bits, result.callbacks, row)
        # Rows are never mutated after construction. Retain every interval in
        # order, but repeated phase evidence need not allocate/encode it again.
        # The collector above still samples and validates each actual interval.
        self._rows.append(row)
        return result

    def _begin(self, phase):
        self._collector.begin()
        self._phase = phase

    def start_slots(self):
        if not self.stimulus.slots and self.stimulus.replay_policy is None:
            _fail("an empty stimulus has no first slot")
        self._end("startup")
        self._begin("control")

    def sampled(self):
        self._control = self._end("control").goal_bits
        self._begin("clock")

    def clocked(self):
        self._control = _union_bits(self._control, self._end("clock").goal_bits)
        self._begin("advance")

    def successor(self):
        hits = _union_bits(self._control, self._end("advance").goal_bits)
        self._slot += 1
        self._phase = "between"
        # Every phase was sampled above and its occurrence evidence retained.
        # Only the immutable goal bitmap can be shared with an identical one
        # from the previous slot; it is not a substitute for observing this slot.
        if hits != self._last_goal_bits:
            self._last_vector = self._codec.from_bits(hits)
            self._last_goal_bits = hits
        return self._last_vector

    def next_slot(self):
        if self._phase != "between":
            self._phase = "failed"
            _fail("the previous step has no settled successor")
        if self.stimulus.replay_policy is None and self._slot >= len(self.stimulus.slots):
            self._phase = "failed"
            _fail("more slots than the admitted stimulus")
        self._begin("control")

    def finish(self, *, resolved=None):
        if self.stimulus.replay_policy is not None:
            if resolved is None or resolved.replay_policy is not None:
                _fail("a runtime request needs its actual finite schedule")
            self.stimulus = resolved
        elif resolved is not None and resolved.fingerprint != self.stimulus.fingerprint:
            _fail("the completed schedule differs from the admitted stimulus")
        if self._slot != len(self.stimulus.slots):
            self._phase = "failed"
            _fail("missing scheduled slots")
        if not self._slot:
            self._end("startup")
        elif self._phase != "between":
            self._phase = "failed"
            _fail("the final step has no settled successor")
        self._collector.finish()
        self._phase = "finished"
        return self._image(VERSION)

    def interrupt(self):
        """Seal actual events on simulator shutdown, without inventing a successor.

        completed_steps distinguishes an interrupted clock return from a fully
        settled step. A transport fault still prevents publication.
        """
        if not self.interruptible:
            _fail("no active interval to interrupt")
        if self._phase != "between":
            self._end(self._phase)
        self._collector.finish()
        self._phase = "interrupted"
        return self._image(PREFIX_VERSION)

    def checkpoint(self, resolved):
        """Fence a complete physical prefix without resetting/sealing execution."""
        if self._phase != "between" or self._slot != len(resolved.slots):
            _fail("checkpoint has no settled successor")
        self._collector.checkpoint()
        image = self._image(CHECKPOINT_VERSION)
        image["stimulus"] = resolved.fingerprint
        return image

    def _image(self, version):
        return {"version": version, "plan": self.plan.evidence_image,
                "execution": execution_policy(native_events=True), "stimulus": self.stimulus.fingerprint,
                "completed_steps": self._slot, "intervals": list(self._rows)}


def read_native_evidence(image, stimulus, catalogue: CatalogueManifest, *,
                         expected_plan: NativeEventPlan | None = None) -> tuple[NativeHits, ...]:
    """Validate a sealed record, retaining every occurrence, not just goal ORs.

    The response reader supplies its judge-prepared expected plan. Later record
    audits can check the persisted mapping against the frozen catalogue; that
    shape check alone does not authenticate an execution or compiler run.
    """
    return _read_steps(image, stimulus, catalogue, expected_plan=expected_plan, bitmap=False)


def read_native_goal_bits(image, stimulus, catalogue: CatalogueManifest, *,
                          expected_plan: NativeEventPlan | None = None,
                          checkpoint=False) -> tuple[bytes, ...]:
    """The same complete interval audit, projected without per-slot name sets.

    This view only verifies the recorded goal bitmaps. The unchanged source
    evidence retains all event coordinates and mappings for occurrence audits;
    it is not replaced by the goal union, nor by a cached previous trace.
    """
    return _read_steps(image, stimulus, catalogue, expected_plan=expected_plan,
                       bitmap=True, checkpoint=checkpoint)


def _read_steps(image, stimulus, catalogue, *, expected_plan, bitmap, checkpoint=False):
    intervals = _read_intervals(image, stimulus, catalogue, expected_plan=expected_plan,
        prefix=False, bitmap=bitmap, checkpoint=checkpoint)
    return _step_hits(intervals, bitmap=bitmap)


def _step_hits(intervals, *, bitmap=False):
    """Project already validated phases, without parsing their evidence again."""
    hits, control = [], None
    union = _union_bits if bitmap else _union
    for phase, observed in intervals:
        if phase == "control":
            control = observed
        elif phase == "clock":
            control = union(control, observed)
        elif phase == "advance":
            hits.append(union(control, observed))
    return tuple(hits)


@dataclass(frozen=True)
class NativePrefix:
    """Completed steps plus the separate interval interrupted by shutdown."""

    startup: NativeHits
    completed: tuple[NativeHits, ...]
    phase: str
    pending: NativeHits


def read_native_prefix(image, stimulus, catalogue: CatalogueManifest, *,
                       expected_plan: NativeEventPlan) -> NativePrefix:
    """Read diagnostic evidence, not a complete trace or failure attribution.

    An interrupted step keeps all its executed phases in ``pending``; it supplies
    no successor. Startup remains separate from scheduled steps.
    The request identity is retained even for a runtime-resolved schedule.
    """
    if type(expected_plan) is not NativeEventPlan:
        raise TypeError("an interrupted observation requires its prepared collector")
    completed, control = [], None
    whole_cycle = image.get("version") == PREFIX_VERSION if type(image) is dict else False
    for index, (phase, pending) in enumerate(_read_intervals(
        image, stimulus, catalogue, expected_plan=expected_plan, prefix=True
    )):
        if phase == "startup":
            startup = pending
        elif phase == "control":
            control = pending
        elif phase == "clock":
            pending = _union(control, pending)
            control = pending
            if not whole_cycle and index < len(image["intervals"]) - 1:
                completed.append(pending)
        elif phase == "advance" and whole_cycle:
            pending = _union(control, pending)
            if len(completed) < image["completed_steps"]:
                completed.append(pending)
                pending = NativeHits(frozenset(), frozenset())
                phase = "between"
    return NativePrefix(startup, tuple(completed), phase, pending)


def _read_intervals(image, stimulus, catalogue, *, expected_plan, prefix, bitmap=False,
                    checkpoint=False):
    version = CHECKPOINT_VERSION if checkpoint else PREFIX_VERSION if prefix else VERSION
    stem = "rtl-native-prefix" if prefix else "rtl-native-observation"
    versions = (version,) if checkpoint else (version, stem + "/2", stem + "/1")
    if type(image) is not dict or image.get("version") not in versions:
        raise ValueError("native evidence uses an unsupported schema")
    whole_cycle = image["version"] == version
    fields = {
        "version", "plan", "execution", "stimulus", "intervals",
    } | ({"completed_steps"} if whole_cycle else set())
    policy = execution_policy(native_events=True)
    if not whole_cycle:
        # Read old receipts under their original boundary, never relabel them as
        # a new execution. Legacy clock-return intervals were required empty.
        policy.update(version="rtl-scheduled-execution-native/3",
                      clock_return="inactive-edge-settles-before-next-drive",
                      structural_sample="native-control-and-clock-events-with-occurrence-evidence")
        policy["phases"] = policy["phases"][:5] + ["record-successor"]
    if set(image) != fields or image["execution"] != policy \
            or image["stimulus"] != stimulus.fingerprint:
        raise ValueError("native evidence is not bound to this stimulus and execution")
    legacy = image["version"] == stem + "/1"
    plan = (NativeEventPlan.from_dict(image["plan"]) if legacy else
            NativeEventPlan.from_evidence(image["plan"], expected=expected_plan))
    if expected_plan is not None and plan != expected_plan:
        raise ValueError("native evidence names a different prepared collector")
    plan.check_catalogue(catalogue)
    rows = image["intervals"]
    horizon = 1 + 3 * len(stimulus.slots)
    if type(rows) is not list or not rows:
        raise ValueError("native evidence needs at least a startup interval")
    if whole_cycle:
        completed = image["completed_steps"]
        if type(completed) is not int or completed < 0 or not (
                0 <= len(rows) - (1 + 3 * completed) <= (3 if prefix else 0)):
            raise ValueError("native evidence has an invalid completed-step count")
    if (not prefix and len(rows) != horizon) or (
        prefix and stimulus.replay_policy is None and len(rows) > horizon
    ):
        raise ValueError("native evidence has an incomplete phase horizon")
    for index, row in enumerate(rows):
        phase = "startup" if index == 0 else ("control", "clock", "advance")[(index - 1) % 3]
        if type(row) is not dict or set(row) != {"phase", "callbacks", "events"} \
                or row["phase"] != phase or type(row["callbacks"]) is not int:
            raise ValueError("native evidence has a malformed phase")
        data = (_legacy_bytes if legacy else event_bytes)(row["events"], plan.bindings.event_count)
        count = int.from_bytes(data, "little").bit_count()
        if (not count <= row["callbacks"] <= plan.callback_count_max
                or bool(count) != bool(row["callbacks"])):
            raise ValueError("native evidence has an invalid callback count")
        observed = (plan.bindings.decode_bitmap(data) if bitmap else
                    plan.bindings.decode(events_from_bytes(data)))
        if not whole_cycle and phase == "advance" and row["callbacks"] and not (prefix and index == len(rows) - 1):
            raise ValueError("native evidence contains unscheduled runtime activity")
        yield phase, observed
