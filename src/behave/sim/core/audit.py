"""Typed results for checks comparing two readings of one trace.

An audit has three outcomes, not a list with overloaded absence:

``AGREED``
    the check ran and found no disagreement;
``INCONSISTENT``
    it ran and found two readings that differ; and
``REFUSED``
    it could not run, so it established neither agreement nor disagreement.

The distinction matters at the solver-witness boundary.  A source process that
could not start is a reason to refuse bounded analysis, not evidence that the
source and IR disagree.  Every frontend returns this algebra directly; an
untyped result is itself a typed refusal at the receiving boundary.
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
from copy import deepcopy

from behave.core import runtime_defaults as defaults

from .frozen import Frozen, FrozenMap
from .refusal import Refusal

__all__ = [
    "AUDITED_WITNESS_RECORD_VERSION",
    "AuditCheck",
    "AuditCheckStatus",
    "AuditedWitness",
    "AuditedWitnessLimitError",
    "WitnessInconsistent",
    "audit_check",
    "encode_valuation_trace",
    "witness_replay_check",
]


# A complete replay is evidence, not an unbounded debug dump. Retained node count
# and serialized bytes measure different resources: neither derives its allowance
# from the other. Both frontends retain the same closed witness record before
# bounded analysis can cite it.
AUDITED_WITNESS_MAX_NODES = defaults.SIM_AUDITED_WITNESS_NODES
AUDITED_WITNESS_RECORD_VERSION = "audited-witness/6"


class WitnessInconsistent(RuntimeError):
    """Two completed readings disagree, invalidating the whole analysis.

    Missing or unavailable evidence is a refusal instead. This exception carries
    a discovered contradiction across query and worker boundaries; audit entry
    points expose the same finding as ``AuditCheck.inconsistent``.
    """

    __slots__ = ("detail", "kind", "queries", "why")

    def __init__(self, kind, why, *, queries=(), **detail):
        self.kind = str(kind)
        self.why = str(why)
        self.detail = dict(detail)
        self.queries = tuple(queries)
        super().__init__(f"{self.kind}: {self.why}")

    def as_dict(self):
        out = {"kind": self.kind, "why": self.why}
        if self.detail:
            out["detail"] = dict(self.detail)
        return out


class AuditedWitnessLimitError(ValueError):
    """A complete trace cannot be retained inside the public evidence envelope."""

    def __init__(self, limit):
        self.limit = limit
        super().__init__(
            f"the audited witness exceeds its {limit}-node evidence limit")


class _WitnessBudget:
    __slots__ = ("limit", "used")

    def __init__(self):
        self.limit = AUDITED_WITNESS_MAX_NODES
        self.used = 0

    def consume(self):
        self.used += 1
        if self.used > self.limit:
            raise AuditedWitnessLimitError(self.limit)


def _json_image(value, path="witness", active=None, budget=None):
    """Copy one finite semantic value into an unambiguous JSON tree.

    Witness evidence crosses process and report boundaries.  Letting ``json`` guess
    here would stringify mapping keys and admit NaN, so two different semantic values
    could acquire one report image.  Behavior values are scalars, arrays and records;
    this is exactly that closed carrier.
    """
    budget = _WitnessBudget() if budget is None else budget
    budget.consume()
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, FrozenMap):
        value = dict(value.items())
        kind = dict
    if kind not in (dict, list, tuple):
        raise TypeError(
            f"{path} contains a {kind.__name__}, outside the finite witness carrier")

    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} contains a reference cycle")
    active.add(identity)
    try:
        if kind is dict:
            out = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError(f"{path} has a non-string key {key!r}")
                out[key] = _json_image(
                    item, f"{path}.{key}", active, budget)
            return out
        return [_json_image(item, f"{path}[{index}]", active, budget)
                for index, item in enumerate(value)]
    except RecursionError:
        raise ValueError(f"{path} is nested too deeply") from None
    finally:
        active.discard(identity)


_MISSING = object()


def _normalized_delta(previous, value, path="witness.state", active=None, *, splices=False,
                      sequence_edits=False, validated=None):
    """Return a JSON value, its canonical delta, and its expanded node count.

    State arrays can be large while changing only a few cells per step.  The
    evidence record therefore stores the first state and a structural delta for
    each successor.  This walk still validates every value in every state; it
    merely avoids retaining the unchanged cells repeatedly.

    ``validated`` is trace validation's own map from container identity to
    ``(container, node count)`` for containers this walk already normalized.
    A successor built by ``_apply_shared_delta`` keeps unchanged subtrees as
    those same objects, so they are neither copied nor walked again.
    """

    kind = type(value)
    if value is None or kind in (bool, int, str):
        normalized = value
        same = previous is not _MISSING and type(previous) is kind \
            and previous == normalized
        return normalized, (None if same else ["r", normalized]), 1
    if kind is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        same = previous is not _MISSING and type(previous) is float \
            and previous.hex() == value.hex()
        return value, (None if same else ["r", value]), 1
    if isinstance(value, FrozenMap):
        value = dict(value.items())
        kind = dict
    if kind not in (dict, list, tuple):
        raise TypeError(
            f"{path} contains a {kind.__name__}, outside the finite witness carrier")

    if validated is not None:
        known = validated.get(id(value))
        if known is not None and known[0] is value:
            if value is previous:
                return value, None, known[1]
            if previous is _MISSING:
                # The walk below returns this replacement for any container
                # without a comparable predecessor, e.g. an element of a list
                # whose length changed.
                return value, ["r", value], known[1]

    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} contains a reference cycle")
    active.add(identity)
    try:
        # A child's ["r", x] delta has 2 + (nodes of x) nodes, which the child
        # already returned; only partial deltas are counted again.
        if kind is dict:
            if any(type(key) is not str for key in value):
                bad = next(key for key in value if type(key) is not str)
                raise TypeError(f"{path} has a non-string key {bad!r}")
            compatible = type(previous) is dict \
                and set(previous) == set(value)
            normalized = {}
            changes = {}
            full_nodes = 1
            patch_nodes = 3
            for key in sorted(value):
                child, delta, nodes = _normalized_delta(
                    previous[key] if compatible else _MISSING,
                    value[key], f"{path}.{key}", active, splices=splices,
                    sequence_edits=sequence_edits, validated=validated)
                normalized[key] = child
                full_nodes += nodes
                if delta is not None:
                    changes[key] = delta
                    patch_nodes += 2 + nodes if delta[0] == "r" else _json_nodes(delta)
            if compatible and not changes:
                result = normalized, None, full_nodes
            elif not compatible or patch_nodes >= 2 + full_nodes:
                result = normalized, ["r", normalized], full_nodes
            else:
                result = normalized, ["d", changes], full_nodes
        else:
            result = _normalized_list(previous, value, path, active, splices=splices,
                                      sequence_edits=sequence_edits, validated=validated)
    except RecursionError:
        raise ValueError(f"{path} is nested too deeply") from None
    finally:
        active.discard(identity)
    if validated is not None:
        validated[id(result[0])] = result[0], result[2]
    return result


def _normalized_list(previous, value, path, active, *, splices, sequence_edits, validated):
    compatible = type(previous) is list and len(previous) == len(value)
    normalized = []
    changes = []
    full_nodes = 1
    patch_nodes = 3
    for index, item in enumerate(value):
        child, delta, nodes = _normalized_delta(
            previous[index] if compatible else _MISSING,
            item, f"{path}[{index}]", active, splices=splices,
            sequence_edits=sequence_edits, validated=validated)
        normalized.append(child)
        full_nodes += nodes
        if delta is not None:
            changes.append([index, delta])
            patch_nodes += 2 + (2 + nodes if delta[0] == "r" else _json_nodes(delta))
    if compatible and not changes:
        return normalized, None, full_nodes
    replacement, replacement_nodes = ["r", normalized], 2 + full_nodes
    if splices and type(previous) is list and not compatible:
        # Length changes need not repeat a cumulative log or a queue.
        # Retain the maximal identical prefix/suffix, with strict value
        # types (including signed zero), and encode only the middle edit.
        prefix, suffix = 0, 0
        common = min(len(previous), len(normalized))
        while prefix < common and _same_json_value(previous[prefix], normalized[prefix]):
            prefix += 1
        while suffix < common - prefix and _same_json_value(
                previous[len(previous) - suffix - 1], normalized[len(normalized) - suffix - 1]):
            suffix += 1
        edit = ["s", [prefix, len(previous) - prefix - suffix,
                      normalized[prefix:len(normalized) - suffix]]]
        edit_nodes = _json_nodes(edit)
        if edit_nodes < replacement_nodes:
            replacement, replacement_nodes = edit, edit_nodes
        if sequence_edits:
            edits = _subsequence_edits(previous, normalized)
            if edits is not None and _json_nodes(edits) < replacement_nodes:
                replacement = edits
        return normalized, replacement, full_nodes
    if not compatible or patch_nodes >= replacement_nodes:
        return normalized, replacement, full_nodes
    return normalized, ["l", changes], full_nodes


def _json_nodes(value):
    """Count nodes in an already normalized finite JSON value."""

    if type(value) is dict:
        return 1 + sum(_json_nodes(item) for item in value.values())
    if type(value) is list:
        return 1 + sum(_json_nodes(item) for item in value)
    return 1


def _json_key(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _same_json_value(left, right):
    """Compare strict JSON values without Python's numeric coercions."""
    return _json_key(left) == _json_key(right)


def _subsequence_edits(previous, following):
    """Linear, exact insert-only/delete-only edits; never guess an alignment.

    General reorder/replacement falls back to the ordinary middle splice.
    Repeated elements, nested values and strict numeric types remain values,
    not presumed identifiers. Coordinates always refer to the original list.
    """
    inserting = len(previous) < len(following)
    old, new = [_json_key(item) for item in previous], [_json_key(item) for item in following]
    shorter, longer = (old, new) if inserting else (new, old)
    matched, begin, edits = 0, None, []
    for position in range(len(longer) + 1):
        agrees = position < len(longer) and matched < len(shorter) \
            and longer[position] == shorter[matched]
        if agrees or position == len(longer):
            if begin is not None:
                edits.append([matched, 0, following[begin:position]] if inserting else
                             [begin, position - begin, []])
                begin = None
            if agrees:
                matched += 1
        elif begin is None:
            begin = position
    return ["e", edits] if matched == len(shorter) and edits else None


def _apply_delta(value, delta):
    """Apply one trusted canonical state delta and return a detached value."""

    if delta is None:
        return deepcopy(value)
    kind, payload = delta
    if kind == "r":
        return deepcopy(payload)
    result = deepcopy(value)
    if kind == "d":
        for key, child in payload.items():
            result[key] = _apply_delta(result[key], child)
        return result
    if kind == "l":
        for index, child in payload:
            result[index] = _apply_delta(result[index], child)
        return result
    if kind in ("s", "e"):
        edits = [payload] if kind == "s" else payload
        if type(result) is not list or type(edits) is not list or not edits:
            raise ValueError("an audited-witness splice requires a list and finite edits")
        previous_start, previous_end = -1, 0
        for edit in edits:
            if type(edit) is not list or len(edit) != 3:
                raise ValueError("an audited-witness splice requires one finite edit")
            start, removed, inserted = edit
            if (type(start) is not int or type(removed) is not int or type(inserted) is not list
                    or not previous_start < start <= len(result) or start < previous_end
                    or not 0 <= removed <= len(result) - start):
                raise ValueError("an audited-witness splice is outside its ordered original list")
            previous_start, previous_end = start, start + removed
        for start, removed, inserted in reversed(edits):
            result[start:start + removed] = deepcopy(inserted)
        return result
    raise ValueError("an audited-witness state delta has an unknown operation")


def _apply_shared_delta(value, delta):
    """``_apply_delta`` that keeps every unchanged subtree as the same object.

    For trace validation only, which never mutates a state it built: the
    successor may then share structure with its predecessor. Containers on a
    changed path are new; every other delta form uses the detached version.
    """
    if delta is None:
        return value
    kind, payload = delta
    if kind == "d" and type(value) is dict and type(payload) is dict:
        result = dict(value)
        for key, child in payload.items():
            result[key] = _apply_shared_delta(result[key], child)
        return result
    if kind == "l" and type(value) is list and type(payload) is list:
        result = list(value)
        for index, child in payload:
            result[index] = _apply_shared_delta(result[index], child)
        return result
    if kind in ("s", "e") and type(value) is list:
        edits = [payload] if kind == "s" else payload
        if type(edits) is not list or not edits:
            raise ValueError("an audited-witness splice requires a list and finite edits")
        previous_start, previous_end = -1, 0
        for edit in edits:
            if type(edit) is not list or len(edit) != 3:
                raise ValueError("an audited-witness splice requires one finite edit")
            start, removed, inserted = edit
            if (type(start) is not int or type(removed) is not int or type(inserted) is not list
                    or not previous_start < start <= len(value) or start < previous_end
                    or not 0 <= removed <= len(value) - start):
                raise ValueError("an audited-witness splice is outside its ordered original list")
            previous_start, previous_end = start, start + removed
        result = list(value)
        for start, removed, inserted in reversed(edits):
            result[start:start + removed] = deepcopy(inserted)
        return result
    return _apply_delta(value, delta)


def encode_valuation_trace(values, path, *, check=lambda: None, splices=False, sequence_edits=False):
    """Lossless typed valuations, shared by witness storage and request identity.

    Every coordinate is validated; unchanged values are represented once, not
    omitted from the semantics. Encoding does not establish domain membership.
    """
    previous = _MISSING
    initial = None
    deltas = []
    for index, state in enumerate(values):
        check()
        normalized, delta, _nodes = _normalized_delta(
            previous, state, f"{path}[{index}]", splices=splices, sequence_edits=sequence_edits)
        if type(normalized) is not dict:
            raise TypeError(f"{path}[{index}] is not a name-to-value mapping")
        if index == 0:
            initial = normalized
        else:
            deltas.append(delta)
        previous = normalized
    check()
    return {"initial": initial, "deltas": deltas}


def _validate_trace(trace, path, *, splices=False, sequence_edits=False):
    """Validate the same lossless valuation encoding for states and inputs."""
    if type(trace) is not dict or set(trace) != {"initial", "deltas"} \
            or type(trace["initial"]) is not dict or type(trace["deltas"]) is not list:
        raise TypeError(f"{path} has an initial valuation and deltas")
    current = trace["initial"]
    # Containers already normalized at an earlier step; a successor shares the
    # unchanged ones, so each step costs its changed paths, not the whole state.
    validated = {}
    for index, delta in enumerate(trace["deltas"]):
        following = _apply_shared_delta(current, delta)
        if type(following) is not dict:
            raise TypeError(f"{path} delta {index} does not produce a valuation")
        normalized, canonical_delta, _nodes = _normalized_delta(
            current, following, f"{path}[{index + 1}]", splices=splices,
            sequence_edits=sequence_edits, validated=validated)
        if not _same_json_value(canonical_delta, delta):
            raise ValueError(f"{path} delta {index} is not in canonical form")
        current = normalized
    return len(trace["deltas"]) + 1


def _expand_trace(trace):
    current = deepcopy(trace["initial"])
    yield current
    for delta in trace["deltas"]:
        current = _apply_delta(current, delta)
        yield current


class AuditedWitness(Frozen):
    """One complete semantic replay, bound to one finite trace domain.

    A pair ``(initial state, inputs)`` is a solver recipe, not yet a witness.  This
    record exists only after replay and carries the complete observation needed to
    check it again: ``N + 1`` states, ``N`` input valuations, ``N`` output batches and
    ``N`` per-step hit sets and separate initialization hits. A modeled failure
    is retained as the terminal error; the remaining steps stutter, just as the
    finite trace semantics does.

    The only stored value is canonical strict JSON.  Its wire form stores the first
    state/input and exact structural deltas for their successors. Input coordinates
    can repeat a whole transaction queue at each physical step; retain it once,
    without dropping any coordinate, step or value type. This is an encoding of
    the same complete trace, not a projection. Every property decodes a fresh view,
    so callers cannot mutate evidence after it has been admitted.
    """

    __slots__ = ("_canonical", "_fingerprint")

    def __init_subclass__(cls, **kwargs):
        raise TypeError(
            "AuditedWitness is a closed evidence record and cannot be subclassed")

    def __init__(self, *, domain, states, inputs, outputs, hit_sets,
                 terminal_error="", initial_hits=()):
        if type(domain) is not str or not domain:
            raise ValueError("an audited witness needs a nonempty trace-domain identity")
        if type(terminal_error) is not str:
            raise TypeError("an audited witness terminal error must be a string")
        if type(inputs) not in (tuple, list):
            raise TypeError("an audited witness input trace must be a finite sequence")
        if type(states) not in (tuple, list):
            raise TypeError("an audited witness state trace must be a finite sequence")
        if type(outputs) not in (tuple, list):
            raise TypeError("an audited witness output trace must be a finite sequence")
        if type(hit_sets) not in (tuple, list):
            raise TypeError("an audited witness hit trace must be a finite sequence")
        steps = len(inputs)
        if steps < 1:
            raise ValueError("an audited witness needs at least one finite step")
        if len(states) != steps + 1:
            raise ValueError(
                f"an {steps}-step witness needs {steps + 1} states, got {len(states)}")
        if len(outputs) != steps or len(hit_sets) != steps:
            raise ValueError(
                "an audited witness needs one output batch and one hit set per input "
                f"step (inputs={steps}, outputs={len(outputs)}, "
                f"hits={len(hit_sets)})")

        def valuation(value, path):
            if type(value) is not dict and not isinstance(value, FrozenMap):
                raise TypeError(f"{path} is not a name-to-value mapping")
            return dict(value.items())

        normalized_states = [
            valuation(value, f"witness.states[{index}]")
            for index, value in enumerate(states)
        ]
        normalized_inputs = [
            valuation(value, f"witness.inputs[{index}]")
            for index, value in enumerate(inputs)
        ]
        normalized_outputs = []
        for step, batch in enumerate(outputs):
            if type(batch) not in (tuple, list):
                raise TypeError(f"witness.outputs[{step}] is not a transaction batch")
            normalized_outputs.append([
                valuation(transaction,
                          f"witness.outputs[{step}][{index}]")
                for index, transaction in enumerate(batch)
            ])

        normalized_hits = []
        for index, hits in enumerate((initial_hits, *hit_sets)):
            if type(hits) not in (set, frozenset, tuple, list):
                raise TypeError(f"witness hit set {index} is not a finite collection")
            if any(type(goal) is not str or not goal for goal in hits):
                raise ValueError(
                    f"witness hit set {index} contains a non-identifier")
            normalized_hits.append(sorted(set(hits)))

        body = _json_image({
            "version": AUDITED_WITNESS_RECORD_VERSION,
            "domain": domain,
            "state_trace": encode_valuation_trace(normalized_states, "witness.states",
                                                   splices=True, sequence_edits=True),
            "inputs": encode_valuation_trace(normalized_inputs, "witness.inputs",
                                              splices=True, sequence_edits=True),
            # Each member is the ordered transaction batch emitted by that step.
            "outputs": normalized_outputs,
            "initial_hits": normalized_hits[0],
            "hit_sets": normalized_hits[1:],
            "terminal_error": terminal_error,
        })
        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
        self._canonical = canonical
        self._fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        self._freeze()

    @classmethod
    def from_record(cls, value):
        """Decode and canonicalize the compact public evidence record."""

        if type(value) is not dict:
            raise TypeError("an audited-witness record must be a mapping")
        legacy = {
            "domain", "states", "inputs", "outputs", "hit_sets",
            "terminal_error",
        }
        if set(value) in (legacy, legacy | {"initial_hits"}):
            got = cls(
                domain=value["domain"], states=value["states"],
                inputs=value["inputs"], outputs=value["outputs"],
                hit_sets=value["hit_sets"],
                initial_hits=value.get("initial_hits", ()),
                terminal_error=value["terminal_error"])
            image = got.as_dict()
            if "initial_hits" not in value:
                image.pop("initial_hits")
            if image != value:
                raise ValueError("an audited-witness image is not canonical")
            return got

        fields = {
            "version", "domain", "state_trace", "inputs", "outputs",
            "hit_sets", "terminal_error",
        }
        version = value.get("version")
        previous = version == "audited-witness/2"
        if (set(value) != (fields if previous else fields | {"initial_hits"})
                or version not in ("audited-witness/2", "audited-witness/3",
                                   "audited-witness/4", "audited-witness/5", AUDITED_WITNESS_RECORD_VERSION)):
            raise TypeError(
                "an audited-witness record has its versioned complete field set")
        body = _json_image(value)
        trace = body["state_trace"]
        options = dict(splices=version in ("audited-witness/5", AUDITED_WITNESS_RECORD_VERSION),
                       sequence_edits=version == AUDITED_WITNESS_RECORD_VERSION)
        states = _validate_trace(trace, "witness.states", **options)
        if version in ("audited-witness/4", "audited-witness/5", AUDITED_WITNESS_RECORD_VERSION):
            steps = _validate_trace(body["inputs"], "witness.inputs", **options)
        else:
            if type(body["inputs"]) is not list or not body["inputs"] \
                    or any(type(row) is not dict for row in body["inputs"]):
                raise TypeError("an audited-witness input trace contains valuations")
            steps = len(body["inputs"])
        if type(body["outputs"]) is not list \
                or type(body["hit_sets"]) is not list \
                or states != steps + 1 \
                or len(body["outputs"]) != steps \
                or len(body["hit_sets"]) != steps:
            raise ValueError(
                "an audited-witness record has one state successor, output and hit "
                "set per input step")
        if type(body["domain"]) is not str or not body["domain"] \
                or type(body["terminal_error"]) is not str:
            raise TypeError("an audited-witness record has a domain and terminal error")
        if any(type(batch) is not list or any(
                type(row) is not dict for row in batch)
                for batch in body["outputs"]):
            raise TypeError("an audited-witness output trace contains transaction batches")
        if any(type(hits) is not list or hits != sorted(set(hits)) or any(
                type(goal) is not str or not goal for goal in hits)
                for hits in (body.get("initial_hits", []), *body["hit_sets"])):
            raise ValueError("an audited-witness hit trace is canonical")

        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
        got = object.__new__(cls)
        got._canonical = canonical
        got._fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        got._freeze()
        return got

    def __eq__(self, other):
        if type(other) is not AuditedWitness:
            return NotImplemented
        return self._canonical == other._canonical

    def __hash__(self):
        return hash(self._canonical)

    def _field(self, name):
        return json.loads(self._canonical)[name]

    @property
    def domain(self):
        return self._field("domain")

    @property
    def states(self):
        return tuple(_expand_trace(self._field("state_trace")))

    @property
    def inputs(self):
        body = json.loads(self._canonical)
        if body["version"] in ("audited-witness/4", "audited-witness/5", AUDITED_WITNESS_RECORD_VERSION):
            return tuple(_expand_trace(body["inputs"]))
        return tuple(body["inputs"])

    def matches_inputs(self, rows):
        """Compare every typed coordinate without expanding a repeated queue."""
        if type(rows) not in (tuple, list) or not rows:
            return False
        body = json.loads(self._canonical)
        image = (encode_valuation_trace(rows, "witness.inputs",
                                       splices=body["version"] in ("audited-witness/5", AUDITED_WITNESS_RECORD_VERSION),
                                       sequence_edits=body["version"] == AUDITED_WITNESS_RECORD_VERSION)
                 if body["version"] in ("audited-witness/4", "audited-witness/5", AUDITED_WITNESS_RECORD_VERSION)
                 else _json_image(rows))
        return _same_json_value(body["inputs"], image)

    @property
    def outputs(self):
        return tuple(tuple(batch) for batch in self._field("outputs"))

    @property
    def hit_sets(self):
        return tuple(frozenset(hits) for hits in self._field("hit_sets"))

    @property
    def initial_hits(self):
        # Reading a persisted v2 record keeps its canonical bytes/fingerprint.
        # That version had no separate initialization evidence.
        return frozenset(json.loads(self._canonical).get("initial_hits", ()))

    @property
    def initial_state(self):
        return deepcopy(self._field("state_trace")["initial"])

    @property
    def final_state(self):
        trace = self._field("state_trace")
        current = deepcopy(trace["initial"])
        for delta in trace["deltas"]:
            current = _apply_delta(current, delta)
        return current

    @property
    def hits(self):
        return self.initial_hits | frozenset(goal for step in self.hit_sets for goal in step)

    @property
    def terminal_error(self):
        return self._field("terminal_error")

    @property
    def halted(self):
        return bool(self.terminal_error)

    @property
    def fingerprint(self):
        return self._fingerprint

    def as_dict(self):
        return {
            "domain": self.domain,
            "states": list(self.states),
            "inputs": list(self.inputs),
            "outputs": [list(batch) for batch in self.outputs],
            "hit_sets": [sorted(hits) for hits in self.hit_sets],
            "initial_hits": sorted(self.initial_hits),
            "terminal_error": self.terminal_error,
        }

    def as_record(self):
        """Return the compact, complete JSON record used at evidence boundaries."""

        return json.loads(self._canonical)

    def __reduce__(self):
        return (AuditedWitness.from_record, (self.as_record(),))


class AuditCheckStatus(enum.Enum):
    AGREED = "agreed"
    INCONSISTENT = "inconsistent"
    REFUSED = "refused"

    def __bool__(self):
        raise TypeError(
            "AuditCheckStatus has no truth value; inspect the named audit result")


def _audit_check_from_parts(status, findings, kind, why, detail=None):
    return AuditCheck(status, findings=findings, kind=kind, why=why, detail=detail)


class AuditCheck(Frozen):
    """One completed, inconsistent, or unavailable semantic audit."""

    __slots__ = ("findings", "refusal", "status")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("AuditCheck is a closed audit result and cannot be subclassed")

    def __init__(self, status, *, findings=(), kind="", why="", detail=None):
        if type(status) is not AuditCheckStatus:
            raise TypeError(
                "an audit-check status must be the exact AuditCheckStatus enum")
        self.status = status
        self.findings = tuple(findings)
        if any(type(item) is not str or not item for item in self.findings):
            raise TypeError("an audit finding must be a nonempty string")
        if type(kind) is not str or type(why) is not str:
            raise TypeError("an audit refusal kind and explanation must be strings")
        if detail is not None and type(detail) is not dict:
            raise TypeError("an audit refusal detail must be a dictionary")
        self.refusal = None
        if self.status is AuditCheckStatus.AGREED:
            if self.findings or kind or why or detail:
                raise ValueError("an agreed audit carries no adverse finding")
        elif self.status is AuditCheckStatus.INCONSISTENT:
            if not self.findings or kind or why or detail:
                raise ValueError(
                    "an inconsistent audit carries one or more findings and no "
                    "refusal")
        elif not kind or not why or self.findings:
            raise ValueError(
                "a refused audit carries a stable kind and reason, not findings")
        else:
            self.refusal = Refusal(kind, why, **(detail or {}))
        self._freeze()

    @property
    def kind(self):
        return "" if self.refusal is None else self.refusal.kind

    @property
    def why(self):
        return "" if self.refusal is None else self.refusal.why

    @classmethod
    def agreed(cls):
        return cls(AuditCheckStatus.AGREED)

    @classmethod
    def inconsistent(cls, findings):
        if isinstance(findings, str):
            findings = (findings,)
        return cls(AuditCheckStatus.INCONSISTENT, findings=findings)

    @classmethod
    def refused(cls, kind, why, **detail):
        return cls(AuditCheckStatus.REFUSED, kind=kind, why=why, detail=detail)

    def as_dict(self):
        out = {"status": self.status.value}
        if self.findings:
            out["findings"] = list(self.findings)
        if self.status is AuditCheckStatus.REFUSED:
            out["refusal"] = self.refusal.as_dict()
        return out


    def __reduce__(self):
        return (_audit_check_from_parts, (
            self.status, self.findings, self.kind, self.why,
            None if self.refusal is None else self.refusal.as_dict().get("detail")))


def audit_check(value, name="audit"):
    """Require the closed audit algebra and fail closed on every other shape."""
    if type(value) is AuditCheck:
        return value
    return AuditCheck.refused(
        f"{name}-result-invalid",
        f"the {name.replace('-', ' ')} returned {type(value).__name__}, outside "
        "the typed audit algebra")


def witness_replay_check(witness, replay):
    """Compare a complete witness with an independently requested replay.

    ``audit_request`` enriches a decoded input recipe with states, outputs and hits.
    That record cannot authenticate itself: the ordinary replay operation must answer
    the same question independently before the witness supports feasibility or a goal
    status.  Shape failures are refusals; two complete readings that differ are an
    inconsistency.
    """
    # Imported lazily because ``instance`` imports this module for the two record types.
    from .instance import Replay, StepOutput, endpoint_batch_rows

    if type(witness) is not AuditedWitness:
        return AuditCheck.refused(
            "witness-replay-witness-invalid",
            "the replay check requires the exact complete AuditedWitness record")
    if type(replay) is not Replay:
        return AuditCheck.refused(
            "witness-replay-result-invalid",
            "the independent replay returned something other than the exact Replay "
            "record")
    if replay.broken is not None and type(replay.broken) is not str:
        return AuditCheck.refused(
            "witness-replay-failure-invalid",
            "the independent replay's infrastructure failure is not an exact string")
    if replay.broken:
        return AuditCheck.refused(
            "witness-replay-failed",
            f"the independent replay could not complete: {replay.broken}")

    steps = len(witness.inputs)
    if type(replay.steps) is not int or replay.steps != steps:
        return AuditCheck.refused(
            "witness-replay-length-invalid",
            "the independent replay did not report the complete finite horizon "
            f"(witness={steps}, replay={replay.steps!r})")
    if type(replay.error) is not str:
        return AuditCheck.refused(
            "witness-replay-error-invalid",
            "the independent replay's terminal error is not an exact string")
    if type(replay.outputs) is not list or len(replay.outputs) != steps:
        return AuditCheck.refused(
            "witness-replay-outputs-incomplete",
            "the independent replay did not return one output batch per finite step")
    if type(replay.hits) is not set or any(
            type(goal) is not str or not goal for goal in replay.hits):
        return AuditCheck.refused(
            "witness-replay-hits-invalid",
            "the independent replay's hit set is not a finite set of goal IDs")

    batches = []
    for output in replay.outputs:
        if type(output) is not StepOutput or output.count < 0:
            return AuditCheck.refused(
                "witness-replay-output-invalid",
                "the independent replay returned a malformed step output",
            )
        try:
            # ``StepOutput`` uses a tagged envelope when one step can emit on
            # independent endpoints.  The witness retains the semantic sparse
            # batch, so remove that transport envelope before comparing them.
            batches.append(
                endpoint_batch_rows(output)
                if set(output.buffers) == {"@endpoint", "@payload"}
                else output.transactions)
        except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
            return AuditCheck.refused(
                "witness-replay-output-unreadable",
                "the independent replay's output batch could not be read: "
                f"{type(exc).__name__}: {exc}")

    try:
        observed = _json_image({
            "outputs": batches,
            "hits": sorted(replay.hits),
            "terminal_error": replay.error,
            "final_state": replay.state,
        }, "independent replay")
        recorded = _json_image({
            "outputs": witness.outputs,
            "hits": sorted(witness.hits),
            "terminal_error": witness.terminal_error,
            "final_state": witness.final_state,
        }, "audited witness")
    except (TypeError, ValueError) as exc:
        return AuditCheck.refused(
            "witness-replay-image-invalid",
            f"the witness/replay comparison has no exact finite image: {exc}")

    findings = [
        f"{name}: witness {recorded[name]!r} != replay {observed[name]!r}"
        for name in ("outputs", "hits", "terminal_error", "final_state")
        if not _same_json_value(recorded[name], observed[name])
    ]
    return (AuditCheck.inconsistent(findings)
            if findings else AuditCheck.agreed())
