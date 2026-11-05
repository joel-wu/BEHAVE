"""Atomic execution of Behavior handlers in one global event slot.

The public kernel is deliberately independent of Python source syntax.  A domain
handler sees one common pre-state and returns an explicit write set.  All active
handlers are evaluated before any write is committed, ownership is checked, and
the compatible write sets form one successor.  The native adapter at the bottom
turns existing ``process_<domain>`` models into that interface without assigning
an incidental iteration order to coincident events.
"""

from __future__ import annotations

import collections.abc
import math

from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

from ..core.atomic_witness import (
    ATOMIC_GENERATION_POLICY,
    ATOMIC_WITNESS_VERSION,
    validate_atomic_witness_identity,
    witness_invocations,
)
from ..core.clock_schedule import GlobalEventSlot
from ..core.failure import CandidateFault
from ..core.frozen import Frozen, FrozenMap
from ..core.refusal import Refusal, Refused
from ..symbolic import native_values
from ..symbolic.native_values import (
    DEFAULT_WIRE_NODES,
    NativeValueError,
    WireDecoder,
    WireEncoder,
    typed_equal,
)
from ..symbolic.uniform_array import UniformArray

__all__ = [
    "ATOMIC_GENERATION_POLICY",
    "ATOMIC_TRANSITION_VERSION",
    "ATOMIC_WITNESS_VERSION",
    "AtomicKernel",
    "AtomicTransition",
    "DomainEffect",
    "DomainInvocation",
    "DomainOwnership",
    "IsolatedAtomicMachine",
    "NativeAtomicMachine",
    "atomic_effect",
    "atomic_outputs_from_wire",
    "atomic_outputs_to_wire",
    "atomic_successor",
    "atomic_transition_from_record",
    "atomic_transition_record",
    "invocations_from_record",
    "ownership_from_source",
    "validate_atomic_witness_identity",
]


ATOMIC_TRANSITION_VERSION = "atomic-transition/2"
_ISOLATED_TRANSITION_VERSION = "isolated-atomic-transition/2"


_SCALARS = (type(None), bool, int, float, str, bytes)


def _stable_key(value):
    """Whether a mapping key/set member survives componentwise copying."""
    if type(value) is float:
        # NaN is representable as a positional value, but using it as a key or
        # set member makes equality depend on object identity.
        return not math.isnan(value)
    if type(value) in (type(None), bool, int, str, bytes):
        return True
    if type(value) in (tuple, frozenset):
        return all(_stable_key(item) for item in value)
    return False


def _mapping(value, what):
    if type(value) is dict:
        return dict(value)
    if isinstance(value, FrozenMap):
        return dict(value.items())
    raise TypeError(f"{what} must be an exact mapping")


def _clone(value, active=None, seen_mutable=None, path="state"):
    """Copy the closed native carrier without invoking candidate protocols.

    Mutable aliasing across state paths is refused.  The atomic patch format is
    path based; silently duplicating an alias would make a write through one path
    disappear from the other, while preserving it would require an object-graph
    transition rather than the declared component transition.
    """
    active = set() if active is None else active
    seen_mutable = {} if seen_mutable is None else seen_mutable
    if type(value) in _SCALARS:
        return value
    if type(value) is UniformArray:
        # SolverIR uses this closed immutable carrier. Native candidate objects
        # still cross the independent builtin-only wire/value boundary.
        return value
    if type(value) in (bytearray, list, dict, set):
        identity = id(value)
        if identity in active:
            raise Refused(Refusal(
                "multiclock-state-cyclic",
                "the native state contains a mutable cycle, which has no finite "
                "componentwise write image",
                path=path, first_path=seen_mutable[identity], value_type=type(value).__name__))
        if identity in seen_mutable:
            raise Refused(Refusal(
                "multiclock-state-aliased",
                "the native state contains a repeated mutable object, which a "
                "path write set cannot preserve without changing its alias graph",
                path=path, first_path=seen_mutable[identity], value_type=type(value).__name__))
        seen_mutable[identity] = path
    if type(value) is bytearray:
        return bytearray(value)
    if type(value) in (list, dict, set):
        identity = id(value)
        active.add(identity)
        if type(value) is list:
            out = [_clone(item, active, seen_mutable, f"{path}[{index}]")
                   for index, item in enumerate(value)]
        elif type(value) is dict:
            out = {}
            for key, item in value.items():
                if not _stable_key(key):
                    raise Refused(Refusal(
                        "multiclock-state-key-unsupported",
                        "a state mapping key has no stable, protocol-free encoding",
                        key_type=type(key).__name__))
                copied_key = _clone(key, active, seen_mutable)
                out[copied_key] = _clone(item, active, seen_mutable, f"{path}[{key!r}]")
        else:
            if any(not _stable_key(item) for item in value):
                raise Refused(Refusal(
                    "multiclock-state-key-unsupported",
                    "a set member has no stable, protocol-free encoding"))
            out = {_clone(item, active, seen_mutable) for item in value}
        active.remove(identity)
        return out
    if type(value) in (tuple, frozenset):
        # Immutable containers may repeat without creating a mutable alias.
        if type(value) is tuple:
            return tuple(_clone(item, active, seen_mutable, f"{path}[{index}]")
                         for index, item in enumerate(value))
        if any(not _stable_key(item) for item in value):
            raise Refused(Refusal(
                "multiclock-state-key-unsupported",
                "a frozenset member has no stable, protocol-free encoding"))
        return frozenset(_clone(item, active, seen_mutable) for item in value)
    raise Refused(Refusal(
        "multiclock-state-carrier-unsupported",
        "atomic native replay admits only the closed builtin state carrier; "
        "copying an opaque object could execute candidate code or lose identity",
        value_type=type(value).__name__))


def _same(left, right):
    # This is both the diff relation and the meaning of the explicit
    # ``identical`` merge policy.  Python's ordinary equality identifies
    # ``-0.0`` with ``0.0`` and can identify Boolean and integer values; neither
    # is an exact typed transition.  The common native-value authority compares
    # type-tagged images and treats identity-sensitive keys as outside its domain.
    if type(left) is UniformArray or type(right) is UniformArray:
        from ..symbolic.values import value_equal

        return value_equal(left, right)
    return typed_equal(left, right)


def _state_copy(state):
    if type(state) is not dict or any(type(name) is not str for name in state):
        raise TypeError("atomic state is a plain string-keyed dictionary")
    return _clone(state)


def _value_image(value, what):
    """Freeze one public record value without erasing its native type."""
    try:
        return native_values.freeze(_clone(value))
    except NativeValueError as exc:
        raise Refused(Refusal(
            "multiclock-record-value-unsupported",
            f"{what} has no exact finite record",
            error=str(exc))) from None


class _NativeState(collections.abc.Mapping):
    """Immutable, type-preserving snapshot exposed by an atomic transition.

    ``core.frozen.freeze`` is intentionally unsuitable here: it canonicalises
    lists, sets and mappings as tuples.  Atomic replay records are evidence about
    the candidate's native state, so their carrier types must survive.  Values
    are stored as exact tagged images and every read reconstructs a fresh value;
    mutating a returned list therefore cannot rewrite the transition record.
    """

    __slots__ = ("_pairs",)

    def __init__(self, state):
        state = _state_copy(state)
        try:
            self._pairs = tuple(
                (name, native_values.freeze(value))
                for name, value in sorted(state.items()))
        except NativeValueError as exc:
            raise Refused(Refusal(
                "multiclock-state-carrier-unsupported",
                "an atomic state value has no exact finite record",
                error=str(exc))) from None

    def __iter__(self):
        return (name for name, _image in self._pairs)

    def __len__(self):
        return len(self._pairs)

    def __getitem__(self, name):
        for got, image in self._pairs:
            if got == name:
                return native_values.thaw(image)
        raise KeyError(name)

    def as_dict(self):
        return {name: native_values.thaw(image) for name, image in self._pairs}

    def __eq__(self, other):
        if type(other) is _NativeState:
            return self._pairs == other._pairs
        if not isinstance(other, collections.abc.Mapping):
            return False
        try:
            return self._pairs == _NativeState(dict(other))._pairs
        except (Refused, TypeError, ValueError):
            return False

    def __repr__(self):
        return f"_NativeState({self.as_dict()!r})"


def _diff(before, after, path=()):
    if type(before) is UniformArray or type(after) is UniformArray:
        if (type(before) not in (list, UniformArray)
                or type(after) not in (list, UniformArray)
                or len(before) != len(after)):
            raise Refused(Refusal(
                "behavior-atomic-state-shape-changed",
                "a compact IR array changes its fixed component shape",
                path=repr(path)))
        if (type(before) is UniformArray and type(after) is UniformArray
                and before.base_image == after.base_image):
            indices = sorted({index for index, _ in before.sparse_items()}
                             | {index for index, _ in after.sparse_items()})
        else:
            indices = range(len(before))
        out = {}
        for index in indices:
            out.update(_diff(before[index], after[index], path + (index,)))
        return out
    if type(before) is not type(after):
        return {path: _clone(after)}
    if type(before) is dict:
        if before.keys() != after.keys():
            return {path: _clone(after)}
        out = {}
        for key in before:
            out.update(_diff(before[key], after[key], path + (key,)))
        return out
    if type(before) in (list, tuple):
        if len(before) != len(after):
            return {path: _clone(after)}
        out = {}
        for index, (old, new) in enumerate(zip(before, after)):
            out.update(_diff(old, new, path + (index,)))
        return out
    if type(before) is bytearray:
        if len(before) != len(after):
            return {path: bytearray(after)}
        return {path + (index,): int(new)
                for index, (old, new) in enumerate(zip(before, after))
                if old != new}
    return {} if _same(before, after) else {path: _clone(after)}


def _normal_outputs(value):
    if value is None or (type(value) is list and not value):
        return ()
    rows = value if type(value) is list else [value]
    out = []
    for row in rows:
        if (type(row) is not dict
                or any(type(name) is not str or not name for name in row)):
            raise CandidateFault(Refusal(
                "multiclock-output-shape",
                "a domain handler must emit a mapping, a list of mappings, or no "
                "transaction",
                got=type(row).__name__))
        out.append(tuple(sorted((name, _clone(item))
                                for name, item in row.items())))
    return tuple(out)


def _path_text(path):
    if not path:
        return "<whole-state>"
    text = str(path[0])
    for item in path[1:]:
        text += f"[{item!r}]"
    return text


class DomainInvocation(Frozen):
    """The arguments offered to one active domain handler in a slot."""

    __slots__ = ("_args", "domain")

    def __init__(self, domain, args=()):
        if type(domain) is not str or not domain:
            raise ValueError("a domain invocation needs a nonempty domain name")
        if type(args) not in (tuple, list):
            raise TypeError("domain invocation arguments must be an exact sequence")
        self.domain = domain
        self._args = tuple(
            _value_image(value, "a domain invocation argument")
            for value in args)
        self._freeze()

    @property
    def args(self):
        return tuple(native_values.thaw(image) for image in self._args)

    def as_record(self):
        return {"domain": self.domain, "args": list(self.args)}

    @classmethod
    def from_record(cls, value):
        if (type(value) is not dict or set(value) != {"domain", "args"}
                or type(value.get("domain")) is not str
                or type(value.get("args")) is not list):
            raise Refused(Refusal(
                "multiclock-invocation-record-malformed",
                "an atomic invocation record has exactly a string domain and "
                "a list of arguments"))
        try:
            return cls(value["domain"], value["args"])
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "multiclock-invocation-record-malformed",
                "an atomic invocation record contains a noncanonical value",
                error=str(exc))) from None


def invocations_from_record(slot, record):
    """Decode an atomic wire action as native-handler invocations."""

    return tuple(DomainInvocation(item.domain, item.args)
                 for item in witness_invocations(slot, record))


class DomainEffect(Frozen):
    """One handler's proposed writes and observations from the common pre-state."""

    __slots__ = ("_outputs", "_writes", "domain", "hits")

    def __init__(self, domain, writes=None, outputs=(), hits=()):
        if type(domain) is not str or not domain:
            raise ValueError("a domain effect needs a nonempty domain name")
        self.domain = domain
        writes = {} if writes is None else _mapping(
            writes, "a domain effect's write set")
        got = []
        for path, value in writes.items():
            if type(path) is not tuple:
                raise TypeError("a domain-effect write path must be an exact tuple")
            if not path or type(path[0]) is not str:
                raise ValueError("a write path begins with a state-component name")
            if any(not _stable_key(part) for part in path):
                raise TypeError(
                    "a domain-effect write path contains an unsupported component")
            got.append((
                path,
                _value_image(value, "a domain-effect write value"),
            ))
        self._writes = tuple(sorted(got, key=lambda pair: repr(pair[0])))
        if type(outputs) is not tuple:
            raise TypeError("domain-effect outputs must be an exact tuple of rows")
        normal_outputs = []
        for row in outputs:
            if type(row) is not tuple:
                raise TypeError("a domain-effect output row must be an exact tuple")
            normal_row = []
            for field in row:
                if (type(field) is not tuple or len(field) != 2
                        or type(field[0]) is not str or not field[0]):
                    raise TypeError(
                        "a domain-effect output field is a (name, value) tuple")
                normal_row.append((
                    field[0],
                    _value_image(field[1], "a domain-effect output value"),
                ))
            names = [name for name, _value in normal_row]
            if names != sorted(set(names)):
                raise ValueError(
                    "a domain-effect output row needs sorted unique field names")
            normal_outputs.append(tuple(normal_row))
        if type(hits) is not tuple or any(
                type(hit) is not str or not hit for hit in hits):
            raise TypeError("domain-effect hits must be exact goal identifiers")
        self._outputs = tuple(normal_outputs)
        self.hits = tuple(sorted(set(hits)))
        self._freeze()

    @property
    def writes(self):
        return tuple((path, native_values.thaw(image))
                     for path, image in self._writes)

    @property
    def outputs(self):
        return tuple(
            tuple((name, native_values.thaw(image)) for name, image in row)
            for row in self._outputs)

    def as_record(self):
        return {
            "domain": self.domain,
            "writes": [[list(path), value] for path, value in self.writes],
            "outputs": [[list(field) for field in row] for row in self.outputs],
            "hits": list(self.hits),
        }

    @classmethod
    def from_record(cls, value):
        required = {"domain", "writes", "outputs", "hits"}
        if (type(value) is not dict or set(value) != required
                or type(value.get("domain")) is not str
                or type(value.get("writes")) is not list
                or type(value.get("outputs")) is not list
                or type(value.get("hits")) is not list):
            raise Refused(Refusal(
                "multiclock-effect-record-malformed",
                "an atomic effect record has exactly domain, writes, outputs and hits"))
        writes = {}
        paths = []
        for pair in value["writes"]:
            if (type(pair) is not list or len(pair) != 2
                    or type(pair[0]) is not list):
                raise Refused(Refusal(
                    "multiclock-effect-record-malformed",
                    "an atomic write is a [path, value] pair"))
            path = tuple(pair[0])
            if (not path or type(path[0]) is not str or not path[0]
                    or any(not _stable_key(part) for part in path)):
                raise Refused(Refusal(
                    "multiclock-effect-record-malformed",
                    "an atomic write path begins with a state name and contains "
                    "only stable components"))
            if path in writes:
                raise Refused(Refusal(
                    "multiclock-effect-record-malformed",
                    "an atomic effect contains one write per path",
                    path=_path_text(path)))
            writes[path] = pair[1]
            paths.append(path)
        if paths != sorted(paths, key=repr):
            raise Refused(Refusal(
                "multiclock-effect-record-malformed",
                "atomic write paths are in canonical order"))
        outputs = []
        for row in value["outputs"]:
            if type(row) is not list:
                raise Refused(Refusal(
                    "multiclock-effect-record-malformed",
                    "an atomic output row is a list of [name, value] fields"))
            fields = []
            for field in row:
                if type(field) is not list or len(field) != 2:
                    raise Refused(Refusal(
                        "multiclock-effect-record-malformed",
                        "an atomic output field is [name, value]"))
                fields.append(tuple(field))
            outputs.append(tuple(fields))
        if (any(type(hit) is not str or not hit for hit in value["hits"])
                or value["hits"] != sorted(set(value["hits"]))):
            raise Refused(Refusal(
                "multiclock-effect-record-malformed",
                "atomic hit identifiers are sorted unique nonempty strings"))
        try:
            return cls(value["domain"], writes, tuple(outputs), tuple(value["hits"]))
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "multiclock-effect-record-malformed",
                "an atomic effect record contains a noncanonical value",
                error=str(exc))) from None


class DomainOwnership(Frozen):
    """Which domains may write each state component and how collisions merge.

    ``merge`` may name ``"identical"`` for a component: several authorised
    domains may write it at one instant only if they propose the same value.
    Absent a merge rule, overlapping writes are refused.  This is intentionally
    a declaration rather than a rule inferred from the values that happened to
    occur in one run.
    """

    __slots__ = ("merge", "owners")

    def __init__(self, owners, merge=None):
        owners = _mapping(owners, "domain ownership")
        normal = []
        for component, domains in owners.items():
            if type(component) is not str or not component:
                raise ValueError("a state component needs a nonempty string name")
            if type(domains) not in (tuple, list, set, frozenset):
                raise ValueError("component owners are nonempty domain names")
            domains = tuple(domains)
            if any(type(domain) is not str or not domain for domain in domains):
                raise ValueError("component owners are nonempty domain names")
            names = tuple(sorted(set(domains)))
            normal.append((component, names))
        components = {component for component, _domains in normal}
        rules = []
        merge = {} if merge is None else _mapping(
            merge, "domain merge policy")
        for component, rule in merge.items():
            if component not in components:
                raise ValueError("a merge policy must name a declared component")
            if rule != "identical":
                raise ValueError("the built-in merge policy is 'identical'")
            rules.append((component, rule))
        self.owners = tuple(sorted(normal))
        self.merge = tuple(sorted(rules))
        self._freeze()

    def domains_for(self, component):
        for name, domains in self.owners:
            if name == component:
                return domains
        return ()

    def merge_for(self, component):
        for name, rule in self.merge:
            if name == component:
                return rule
        return None

    def check_state(self, state):
        declared = {name for name, _domains in self.owners}
        given = set(state)
        if declared != given:
            raise Refused(Refusal(
                "multiclock-ownership-state-mismatch",
                "domain ownership and the atomic state must name exactly the same "
                "components",
                missing=sorted(declared - given), extra=sorted(given - declared)))

    def as_record(self):
        return {
            "owners": [[component, list(domains)]
                       for component, domains in self.owners],
            "merge": [[component, rule] for component, rule in self.merge],
        }

    @classmethod
    def from_record(cls, value):
        if (type(value) is not dict or set(value) != {"owners", "merge"}
                or type(value.get("owners")) is not list
                or type(value.get("merge")) is not list):
            raise Refused(Refusal(
                "multiclock-ownership-record-malformed",
                "an ownership record has exactly owners and merge lists"))

        def pairs(rows, what):
            out = {}
            for row in rows:
                if (type(row) is not list or len(row) != 2
                        or type(row[0]) is not str or not row[0]
                        or row[0] in out):
                    raise Refused(Refusal(
                        "multiclock-ownership-record-malformed",
                        f"{what} uses unique [component, value] rows"))
                out[row[0]] = row[1]
            if [row[0] for row in rows] != sorted(out):
                raise Refused(Refusal(
                    "multiclock-ownership-record-malformed",
                    f"{what} rows are sorted by component"))
            return out

        owners = pairs(value["owners"], "ownership")
        merge = pairs(value["merge"], "merge policy")
        if any(type(domains) is not list
               or any(type(domain) is not str or not domain
                      for domain in domains)
               or domains != sorted(set(domains))
               for domains in owners.values()):
            raise Refused(Refusal(
                "multiclock-ownership-record-malformed",
                "owner domains are sorted unique nonempty strings"))
        if any(rule != "identical" for rule in merge.values()):
            raise Refused(Refusal(
                "multiclock-ownership-record-malformed",
                "the only declared merge rule is identical"))
        try:
            return cls(owners, merge=merge)
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "multiclock-ownership-record-malformed",
                "an ownership record contains an invalid declaration",
                error=str(exc))) from None


class AtomicTransition(Frozen):
    """The one successor and observations produced by one atomic slot."""

    __slots__ = ("after", "before", "effects", "hits", "slot")

    def __init__(self, slot, before, after, effects):
        if type(slot) is not GlobalEventSlot:
            raise TypeError("an atomic transition needs an exact global event slot")
        if type(effects) not in (tuple, list) or any(
                type(effect) is not DomainEffect for effect in effects):
            raise TypeError(
                "an atomic transition needs exact domain-effect records")
        effect_domains = [effect.domain for effect in effects]
        if effect_domains != sorted(set(effect_domains)):
            raise ValueError(
                "an atomic transition needs one effect per domain in canonical order")
        if "__all_reset__" in effect_domains:
            if effect_domains != ["__all_reset__"] or not slot.resets:
                raise ValueError(
                    "the synthetic full-reset effect is the only effect of a reset slot")
        elif any(domain not in set(slot.events)
                 for domain in effect_domains):
            raise ValueError(
                "an atomic effect must belong to a domain with an edge in this slot")
        self.slot = slot
        self.before = (before if type(before) is _NativeState
                       else _NativeState(before))
        self.after = (after if type(after) is _NativeState
                      else _NativeState(after))
        self.effects = tuple(effects)
        self.hits = tuple(sorted({hit for effect in self.effects
                                  for hit in effect.hits}))
        self._freeze()

    @property
    def outputs(self):
        return tuple(
            (effect.domain, effect.outputs) for effect in self.effects
            if effect.outputs)


def _atomic_outputs_record(outputs):
    """Canonical native record for the per-domain transactions of one slot."""
    if type(outputs) is not tuple:
        raise TypeError("atomic outputs must be an exact domain tuple")
    rows = []
    domains = []
    for pair in outputs:
        if (type(pair) is not tuple or len(pair) != 2
                or type(pair[0]) is not str or not pair[0]):
            raise TypeError("an atomic output is a (domain, rows) tuple")
        domain, domain_rows = pair
        # DomainEffect is the one shape validator for transaction rows.  Its
        # empty write/hit sets are not serialized into the witness.
        effect = DomainEffect(domain, outputs=domain_rows)
        rows.append([domain, [[list(field) for field in row]
                              for row in effect.outputs]])
        domains.append(domain)
    if domains != sorted(set(domains)):
        raise ValueError("atomic output domains are sorted and unique")
    return rows


def atomic_outputs_to_wire(transition):
    """JSON-native exact output evidence for one validated atomic transition."""
    if type(transition) is not AtomicTransition:
        raise TypeError("only an AtomicTransition has atomic output evidence")
    return WireEncoder(DEFAULT_WIRE_NODES).encode(
        _atomic_outputs_record(transition.outputs))


def atomic_outputs_from_wire(value):
    """Validate output evidence before a replay or testbench consumes it."""
    try:
        record = WireDecoder(DEFAULT_WIRE_NODES).decode(value)
    except NativeValueError as exc:
        raise Refused(Refusal(
            "multiclock-output-record-malformed",
            "an atomic output record has no exact finite wire image",
            error=str(exc))) from None
    if type(record) is not list:
        raise Refused(Refusal(
            "multiclock-output-record-malformed",
            "an atomic output record is a canonical domain list"))
    outputs = []
    try:
        for pair in record:
            if (type(pair) is not list or len(pair) != 2
                    or type(pair[0]) is not str
                    or type(pair[1]) is not list):
                raise TypeError(
                    "an atomic output row is [domain, transaction-rows]")
            domain, raw_rows = pair
            domain_rows = []
            for raw_row in raw_rows:
                if type(raw_row) is not list:
                    raise TypeError("a transaction row is a field list")
                domain_rows.append(tuple(
                    tuple(field) if type(field) is list else field
                    for field in raw_row))
            effect = DomainEffect(domain, outputs=tuple(domain_rows))
            outputs.append((domain, effect.outputs))
        outputs = tuple(outputs)
        canonical = _atomic_outputs_record(outputs)
        if canonical != record:
            raise ValueError("the atomic output record is not canonical")
        if WireEncoder(DEFAULT_WIRE_NODES).encode(canonical) != value:
            raise ValueError("the atomic output wire image is not canonical")
        return outputs
    except (NativeValueError, TypeError, ValueError) as exc:
        raise Refused(Refusal(
            "multiclock-output-record-malformed",
            "an atomic output record has invalid or noncanonical fields",
            error=str(exc))) from None


def atomic_transition_record(transition):
    """Exact native record sent by the isolated atomic worker."""
    if type(transition) is not AtomicTransition:
        raise TypeError("only an AtomicTransition has an atomic wire record")
    return {
        "version": ATOMIC_TRANSITION_VERSION,
        "slot": transition.slot.as_dict(),
        "before": transition.before.as_dict(),
        "after": transition.after.as_dict(),
        "effects": [effect.as_record() for effect in transition.effects],
    }


def atomic_transition_from_record(value):
    """Validate one worker reply before exposing it as replay evidence."""
    if type(value) is not dict:
        raise Refused(Refusal(
            "multiclock-transition-record-malformed",
            "an atomic transition record is an exact mapping"))
    if value.get("version") != ATOMIC_TRANSITION_VERSION:
        raise Refused(Refusal(
            "multiclock-transition-version-unsupported",
            "an atomic transition must name the wire version this build decodes",
            got=value.get("version"), supported=ATOMIC_TRANSITION_VERSION))
    required = {"version", "slot", "before", "after", "effects"}
    if (set(value) != required
            or type(value.get("before")) is not dict
            or type(value.get("after")) is not dict
            or type(value.get("effects")) is not list):
        raise Refused(Refusal(
            "multiclock-transition-record-malformed",
            "an atomic transition record has exactly version, slot, before, after "
            "and effects"))
    try:
        slot = GlobalEventSlot.from_dict(value["slot"])
        effects = tuple(DomainEffect.from_record(row)
                        for row in value["effects"])
        return AtomicTransition(slot, value["before"], value["after"], effects)
    except Refused:
        raise
    except (TypeError, ValueError) as exc:
        raise Refused(Refusal(
            "multiclock-transition-record-malformed",
            "an atomic transition record contains an invalid value",
            error=str(exc))) from None


def _isolated_transition_record(transition):
    """Compact exact record for the persistent isolated-worker channel.

    The verifier already owns the committed pre-state.  Domain effects are a
    complete patch over that state, so sending either the pre-state or the
    derived successor would duplicate information on every slot.  The parent
    reconstructs both states with the same versioned transition semantics.
    """
    if type(transition) is not AtomicTransition:
        raise TypeError("only an AtomicTransition has an isolated wire record")
    return {
        "version": _ISOLATED_TRANSITION_VERSION,
        "slot": transition.slot.as_dict(),
        "effects": [effect.as_record() for effect in transition.effects],
    }


def _isolated_transition_from_record(value, before, ownership):
    """Reconstruct and validate an isolated transition from owned evidence."""
    if type(value) is not dict:
        raise Refused(Refusal(
            "multiclock-transition-record-malformed",
            "an isolated atomic transition record is an exact mapping"))
    if value.get("version") != _ISOLATED_TRANSITION_VERSION:
        raise Refused(Refusal(
            "multiclock-transition-version-unsupported",
            "an isolated atomic transition must name the wire version this "
            "build decodes",
            got=value.get("version"), supported=_ISOLATED_TRANSITION_VERSION))
    if (set(value) != {"version", "slot", "effects"}
            or type(value.get("effects")) is not list):
        raise Refused(Refusal(
            "multiclock-transition-record-malformed",
            "an isolated atomic transition record has exactly version, slot "
            "and effects"))
    if type(ownership) is not DomainOwnership:
        raise TypeError("isolated transition decoding needs DomainOwnership")
    try:
        slot = GlobalEventSlot.from_dict(value["slot"])
        effects = tuple(DomainEffect.from_record(row)
                        for row in value["effects"])
        prior = (before if type(before) is _NativeState
                 else _NativeState(before))
        if not effects:
            ownership.check_state(prior)
            return AtomicTransition(slot, prior, prior, ())
        after = _effects_successor(
            prior.as_dict(), effects, ownership, slot)
        return AtomicTransition(slot, prior.as_dict(), after, effects)
    except Refused:
        raise
    except (TypeError, ValueError) as exc:
        raise Refused(Refusal(
            "multiclock-transition-record-malformed",
            "an isolated atomic transition record contains an invalid value",
            error=str(exc))) from None


def _overlap(left, right):
    n = min(len(left), len(right))
    return left[:n] == right[:n]


def _replace(root, path, value):
    if not path:
        return _clone(value)
    head, rest = path[0], path[1:]
    if type(root) is UniformArray:
        return root.store(head, _replace(root[head], rest, value))
    if type(root) is list:
        out = list(root)
        out[head] = _replace(out[head], rest, value)
        return out
    if type(root) is tuple:
        out = list(root)
        out[head] = _replace(out[head], rest, value)
        return tuple(out)
    if type(root) is bytearray:
        out = bytearray(root)
        if rest:
            raise Refused(Refusal(
                "multiclock-write-path-invalid",
                "a bytearray element is scalar and has no child path"))
        out[head] = int(value)
        return out
    if type(root) is dict:
        out = dict(root)
        if head not in out:
            raise Refused(Refusal(
                "multiclock-write-path-invalid",
                "a write path names a mapping entry absent from the common pre-state",
                key=repr(head)))
        out[head] = _replace(out[head], rest, value)
        return out
    raise Refused(Refusal(
        "multiclock-write-path-invalid",
        "a write path descends through a scalar state value",
        path=repr(path), carrier=type(root).__name__))


def _effects_successor(before, effects, ownership, slot):
    """Validate an effect set and compute its one atomic successor.

    This is shared by the child-side kernel and the parent-side reply checker.
    A worker reply therefore cannot merely assert an unrelated ``after`` state:
    its typed writes must reconstruct that state under the declared ownership and
    merge policy.
    """
    before = _state_copy(before)
    ownership.check_state(before)
    common_reset = tuple(effect.domain for effect in effects) == (
        "__all_reset__",)
    writes = []
    for effect in effects:
        for path, value in effect.writes:
            component = path[0]
            if (not common_reset
                    and effect.domain not in ownership.domains_for(component)):
                raise Refused(Refusal(
                    "multiclock-write-outside-domain",
                    "a handler proposes a write to a component its typed domain "
                    "ownership does not permit",
                    domain=effect.domain, component=component,
                    path=_path_text(path),
                    owners=list(ownership.domains_for(component))))
            writes.append((effect.domain, path, value))

    # Pairwise overlap is checked before any commit.  Different list/dict
    # elements are disjoint; a parent replacement overlaps every child.
    accepted = []
    for domain, path, value in writes:
        merged = False
        for other_domain, other_path, other_value in accepted:
            if not _overlap(path, other_path):
                continue
            component = path[0]
            if (path == other_path
                    and ownership.merge_for(component) == "identical"
                    and _same(value, other_value)):
                merged = True
                break
            raise Refused(Refusal(
                "multiclock-write-collision",
                "coincident handlers propose overlapping writes and the component "
                "has no total merge policy for those values",
                time=slot.time, component=component,
                first_domain=other_domain, first_path=_path_text(other_path),
                second_domain=domain, second_path=_path_text(path),
                merge=ownership.merge_for(component)))
        if not merged:
            accepted.append((domain, path, value))

    after = _state_copy(before)
    for _domain, path, value in sorted(accepted, key=lambda row: repr(row[1])):
        component, rest = path[0], path[1:]
        after[component] = _replace(after[component], rest, value)
    ownership.check_state(after)
    return after


def atomic_effect(domain, before, after, *, outputs=(), hits=()):
    """Return the exact component write-set between two finite IR states.

    Native replay and SolverIR replay use the same atomic-commit authority.  The
    latter already has concrete before/after valuations, so exposing this small
    value boundary avoids a second, subtly different implementation of nested
    array writes in the exact analyser.
    """
    return DomainEffect(
        domain, _diff(_state_copy(before), _state_copy(after)),
        outputs=outputs, hits=hits)


def atomic_successor(before, effects, ownership, slot):
    """Validate domain effects and compute their one atomic successor.

    This is the public pure form of :class:`AtomicKernel`'s commit.  It executes
    no submitted code and applies exactly the ownership, overlap and declared
    merge rules used by native atomic replay.
    """
    if type(slot) is not GlobalEventSlot:
        raise TypeError("an atomic successor needs a GlobalEventSlot")
    if type(ownership) is not DomainOwnership:
        raise TypeError("an atomic successor needs DomainOwnership")
    if type(effects) not in (tuple, list) or any(
            type(effect) is not DomainEffect for effect in effects):
        raise TypeError("atomic successor effects are exact DomainEffect records")
    domains = [effect.domain for effect in effects]
    if domains != sorted(set(domains)):
        raise ValueError("atomic successor effects use canonical unique domains")
    return _effects_successor(before, effects, ownership, slot)


class AtomicKernel:
    """Evaluate active handlers on one pre-state and commit one successor."""

    def __init__(self, state, ownership, handlers, reset_handlers=None):
        self.state = _state_copy(state)
        self._state_image = _NativeState(self.state)
        if type(ownership) is not DomainOwnership:
            raise TypeError("ownership must be a DomainOwnership")
        ownership.check_state(self.state)
        self.ownership = ownership
        self.handlers = _mapping(handlers, "atomic handlers")
        self.reset_handlers = _mapping(
            {} if reset_handlers is None else reset_handlers,
            "atomic reset handlers")
        for label, table in (("handler", self.handlers),
                             ("reset handler", self.reset_handlers)):
            if any(type(domain) is not str or not domain
                   or not callable(handler)
                   for domain, handler in table.items()):
                raise TypeError(
                    f"every atomic {label} needs a nonempty domain and callable")

    def _effect(self, domain, handler, args, before):
        got = handler(_state_copy(before), tuple(_clone(value) for value in args))
        if type(got) is not DomainEffect:
            raise TypeError(f"handler {domain!r} did not return a DomainEffect")
        if got.domain != domain:
            raise Refused(Refusal(
                "multiclock-effect-domain-mismatch",
                "a handler returned an effect under another domain's name",
                invoked=domain, returned=got.domain))
        return got

    def step(self, slot, invocations=()):
        if type(slot) is not GlobalEventSlot:
            raise TypeError("AtomicKernel.step needs a GlobalEventSlot")
        if type(invocations) not in (tuple, list):
            raise TypeError("atomic invocations must be an exact sequence")
        by_domain = {}
        for invocation in invocations:
            if type(invocation) is not DomainInvocation:
                raise TypeError("invocations must be DomainInvocation records")
            if invocation.domain in by_domain:
                raise Refused(Refusal(
                    "multiclock-two-invocations",
                    "one domain has two invocations in one event slot; its handler "
                    "semantics says one step per active event",
                    domain=invocation.domain, time=slot.time))
            if invocation.domain not in slot.events:
                raise Refused(Refusal(
                    "multiclock-invocation-without-edge",
                    "a handler is invoked in a slot where its domain has no active edge",
                    domain=invocation.domain, time=slot.time,
                    events=list(slot.events)))
            if invocation.domain in slot.resets:
                raise Refused(Refusal(
                    "multiclock-invocation-during-reset",
                    "a reset event cannot also invoke the domain's ordinary handler",
                    domain=invocation.domain, time=slot.time))
            by_domain[invocation.domain] = invocation

        before_image = self._state_image
        active_resets = set(slot.events) & set(slot.resets)
        if not by_domain and not active_resets:
            return AtomicTransition(slot, before_image, before_image, ())

        before = before_image.as_dict()
        effects = []
        failures = []
        # ``slot.resets`` records levels, not additional events.  A reset state
        # transition happens only when that domain also has its active edge in
        # this slot; an asserted reset on an idle domain merely remains asserted.
        for domain in slot.events:
            if domain in slot.resets:
                handler = self.reset_handlers.get(domain)
                if handler is None:
                    raise Refused(Refusal(
                        "multiclock-independent-reset-unavailable",
                        "this slot resets one domain but no domain-local reset "
                        "transition is declared for it",
                        domain=domain, time=slot.time))
                effects.append(self._effect(domain, handler, (), before))
            elif domain in by_domain:
                handler = self.handlers.get(domain)
                if handler is None:
                    raise Refused(Refusal(
                        "multiclock-handler-missing",
                        "an active invocation names a domain with no transition handler",
                        domain=domain))
                try:
                    effects.append(self._effect(domain, handler, by_domain[domain].args, before))
                except CandidateFault as exc:
                    from .atomic_failure import SINGLE_FAILURE

                    if exc.refusal.kind != SINGLE_FAILURE:
                        raise
                    failures.append(exc)
            # No invocation is an inactive semantic handler: its owned state holds.

        if failures:
            from .atomic_failure import combined_failure

            raise combined_failure(failures, effects)
        after = _effects_successor(before, effects, self.ownership, slot)
        self.state = after
        self._state_image = _NativeState(after)
        return AtomicTransition(
            slot, before_image, self._state_image, effects)


def ownership_from_source(source, *, file="behavior.py", cls=None, contract=None):
    """Derive typed field ownership from each parsed ``process_<domain>`` body.

    A field reached from one domain has that owner.  A field reached from several
    domains records all of them but receives no implicit merge rule; simultaneous
    overlapping writes are therefore a typed collision until the task declares a
    total policy.
    """
    from .extract import source_model

    model = source_model(source, file=file, cls=cls)
    if contract is not None:
        from .task_boundary import task_clock_domains

        model = model.for_domains(task_clock_domains(
            contract, (domain for domain in model.domains if domain is not None)))
    if len(model.steps) < 2 or any(domain is None for domain in model.steps):
        raise Refused(Refusal(
            "multiclock-source-shape",
            "typed multi-clock ownership requires at least two process_<domain> "
            "handlers and no competing anonymous process method"))
    owners = {field: set() for field in model.state_contract()}
    for domain, method in model.steps.items():
        for field in model.reachable_state_writes([method]):
            owners.setdefault(field, set()).add(domain)
    return model, DomainOwnership(owners)


class NativeAtomicMachine:
    """Execute an existing multi-clock Behavior model through ``AtomicKernel``.

    Candidate-defined copying hooks are never called.  Each active handler runs on
    a raw instance whose closed builtin field graph was copied from the common
    pre-state; its post-state is diffed into a write set and committed only after
    every coincident handler has completed.
    """

    def __init__(self, model_cls, ownership, domains, *, model=None,
                 invariant=None, hit_log=None, failure_attribution=None,
                 external_fields=()):
        self.model_cls = model_cls
        self.external_fields = tuple(external_fields)
        if invariant is not None and not callable(invariant):
            raise TypeError("an atomic runtime invariant must be callable")
        self._invariant = invariant
        if failure_attribution is not None and not callable(failure_attribution):
            raise TypeError("atomic failure attribution must be callable")
        self._failure_attribution = failure_attribution
        if hit_log is not None and (type(hit_log) is not list or any(
                type(hit) is not str or not hit for hit in hit_log)):
            raise TypeError("an atomic hit log is an exact list of goal identifiers")
        self._hit_log = hit_log
        self._hit_baseline = ()
        self._hit_union = set()
        if type(domains) not in (tuple, list, set, frozenset) or any(
                type(domain) is not str or not domain for domain in domains):
            raise TypeError("atomic domains must be exact nonempty strings")
        self.domains = tuple(sorted(set(domains)))
        if len(self.domains) != len(domains):
            raise ValueError("atomic domain names must be unique")
        if len(self.domains) < 2:
            raise Refused(Refusal(
                "multiclock-domain-count",
                "the atomic multi-clock adapter requires at least two domains",
                domains=list(self.domains)))
        if model is None:
            model = self._call_source(model_cls, operation="construction")
        elif type(model) is not model_cls:
            raise TypeError("an atomic model instance must have the exact model class")
        reset = getattr(model, "reset", None)
        if callable(reset):
            self._reset(model, reset)
        self._check_invariant()
        self.model = model
        state = self._checked_state(model)
        handlers = {domain: self._native_handler(domain)
                    for domain in self.domains}
        self._domain_resets = frozenset(
            domain for domain in self.domains
            if callable(getattr(model, f"reset_{domain}", None)))
        self._released_domains = set()
        resets = {
            domain: (self._native_reset_handler(domain)
                     if domain in self._domain_resets
                     else self._initial_reset_hold(domain))
            for domain in self.domains
        }
        self.kernel = AtomicKernel(state, ownership, handlers, resets)

    def _reset(self, instance, callback, *, domain=""):
        from .reference_memory import preserve_external_memory

        with preserve_external_memory(
                object.__getattribute__(instance, "__dict__"), self.external_fields):
            return self._call_source(callback, operation="reset", domain=domain)

    def _call_source(self, callback, *args, operation, domain="",
                     baseline=None, completed=None):
        """Attribute only source execution; preparation and checks stay outside."""
        try:
            return callback(*args)
        except BaseException as error:  # noqa: BLE001 - source callback boundary
            if operation in {"handler", "ready"}:
                attributed = self._semantic_failure(
                    error, domain=domain,
                    operation="readiness" if operation == "ready" else operation,
                    baseline=baseline, completed=completed)
                if attributed is not None:
                    raise CandidateFault(attributed) from None
            raise CandidateFault(Refusal(
                f"multiclock-{operation}-failed",
                "a native Behavior callback raised during atomic execution",
                domain=domain, error=f"{type(error).__name__}: {error}")) from None

    def _semantic_failure(
            self, error, *, domain, operation, baseline=None, completed=None):
        """Preserve a source-attributed partial failure across atomic rollback."""

        if self._failure_attribution is None:
            return None
        goal = self._failure_attribution(error)
        if goal is None:
            return None
        if type(goal) is not str or not goal:
            raise Refused(Refusal(
                "multiclock-failure-attribution-malformed",
                "the trusted source failure attribution returned no goal identifier"))
        baseline = self._hit_baseline if baseline is None else tuple(baseline)
        reached = set(self._hit_union if completed is None else completed)
        if self._hit_log is not None:
            if (any(type(hit) is not str or not hit for hit in self._hit_log)
                    or tuple(self._hit_log[:len(baseline)]) != baseline):
                raise Refused(Refusal(
                    "multiclock-hit-record-malformed",
                    "the failing handler changed the pre-slot goal history"))
            reached.update(self._hit_log[len(baseline):])
        reached.add(goal)
        return Refusal(
            "behavior-semantic-failure",
            "an atomic Behavior handler reached a declared partial operation",
            domain=domain, operation=operation, goal=goal,
            hits=sorted(reached), error=type(error).__name__)

    def _check_invariant(self):
        if self._invariant is not None:
            self._invariant()

    def _checked_state(self, model):
        if type(model) is not self.model_cls:
            raise CandidateFault(Refusal(
                "multiclock-instance-class-changed",
                "a domain handler changed the runtime class of its private state "
                "image; that effect is not an instance-field write"))
        return self._raw_state(model)

    def _branch(self, operation, *, record_hits=True):
        """Run one handler with the slot's common pre-hit set.

        Instrumentation stores marks in one module list.  That list is a
        sanctioned mutable hook and is therefore excluded from the module image
        guard; without this branch scope, a coincident handler could observe a
        preceding handler's marks even though instance state is atomic.
        Reset transitions use the same isolation but discard marks because they
        are schedule boundaries rather than admitted Behavior steps.
        """
        if self._hit_log is None:
            return operation(), ()
        self._hit_log[:] = self._hit_baseline
        got = operation()
        if any(type(hit) is not str or not hit for hit in self._hit_log):
            raise CandidateFault(Refusal(
                "multiclock-hit-record-malformed",
                "a domain handler produced a non-string goal identifier"))
        if tuple(self._hit_log[:len(self._hit_baseline)]) != self._hit_baseline:
            raise CandidateFault(Refusal(
                "multiclock-hit-record-malformed",
                "a domain handler changed the pre-slot goal history"))
        added = tuple(sorted(set(self._hit_log[len(self._hit_baseline):])))
        if record_hits:
            self._hit_union.update(added)
            return got, added
        return got, ()

    def _begin_slot(self):
        if self._hit_log is None:
            self._hit_baseline = ()
        else:
            if any(type(hit) is not str or not hit for hit in self._hit_log):
                raise Refused(Refusal(
                    "multiclock-hit-record-malformed",
                    "the atomic hit log contains a non-string goal identifier"))
            self._hit_baseline = tuple(self._hit_log)
        self._hit_union = set()

    def _finish_slot(self, *, commit):
        if self._hit_log is not None:
            self._hit_log[:] = list(self._hit_baseline)
            if commit:
                prior = set(self._hit_baseline)
                self._hit_log.extend(
                    hit for hit in sorted(self._hit_union) if hit not in prior)

    @staticmethod
    def _raw_state(model):
        try:
            raw = object.__getattribute__(model, "__dict__")
        except (AttributeError, TypeError):
            raise Refused(Refusal(
                "multiclock-instance-state-unavailable",
                "a native atomic model must expose plain instance-backed state")) \
                from None
        if type(raw) is not dict or any(type(name) is not str for name in raw):
            raise Refused(Refusal(
                "multiclock-instance-state-unavailable",
                "the model's instance field table is not a plain string-keyed dict"))
        return _state_copy(raw)

    def _instance(self, state):
        try:
            instance = object.__new__(self.model_cls)
            raw = object.__getattribute__(instance, "__dict__")
        except (TypeError, AttributeError):
            raise Refused(Refusal(
                "multiclock-instance-clone-unavailable",
                "the model cannot be reconstructed from its plain state without "
                "calling candidate copy or construction protocols")) from None
        raw.update(_state_copy(state))
        return instance

    def _native_handler(self, domain):
        def handler(before, args):
            instance = self._instance(before)
            method = getattr(instance, f"process_{domain}", None)
            if not callable(method):
                raise CandidateFault(Refusal(
                    "multiclock-handler-missing",
                    "the source declared a domain with no callable runtime handler",
                    domain=domain))
            try:
                result, new_hits = self._branch(lambda: self._call_source(
                    method, *args, domain=domain, operation="handler"))
            finally:
                # A partial failure must not leak module/class mutations into a
                # later coincident callback, just as a successful call must not.
                self._check_invariant()
            # No coincident handler may observe module/class state written by a
            # preceding handler.  Instance writes remain an explicit patch over
            # the common pre-state and are committed only by AtomicKernel.
            after = self._checked_state(instance)
            return DomainEffect(domain, _diff(before, after),
                                outputs=_normal_outputs(result), hits=new_hits)
        return handler

    def _native_reset_handler(self, domain):
        def handler(before, _args):
            instance = self._instance(before)
            reset = getattr(instance, f"reset_{domain}")
            self._branch(lambda: self._reset(
                instance, reset, domain=domain), record_hits=False)
            self._check_invariant()
            after = self._checked_state(instance)
            # Domain-local reset is part of the schedule boundary, not an
            # admitted Behavior step.  Its state transition is checked, but
            # source marks in that helper do not enter the step catalogue.
            return DomainEffect(domain, _diff(before, after))
        return handler

    @staticmethod
    def _initial_reset_hold(domain):
        """Hold a domain that has never left the common reset state.

        Construction plus ``reset()`` establishes every domain's initial state.
        Clock schedules may release those domains on different edge counts.  A
        still-asserted domain therefore holds even when the source has no
        ``reset_<domain>`` transition.  Reassertion after release is different:
        it needs that explicit local transition and is refused in ``step``.
        """
        return lambda _before, _args: DomainEffect(domain)

    def _install(self):
        raw = object.__getattribute__(self.model, "__dict__")
        raw.clear()
        raw.update(_state_copy(self.kernel.state))

    def ready_observation(self, domain):
        """Read one optional readiness predicate and its structural hits.

        Readiness is a combinational observation, not a transition.  A predicate
        that mutates state is refused rather than smuggling a write outside the
        atomic slot.  Its hits belong to the event slot that asked whether the
        handler accepts, but the shared instrumentation log is restored so another
        coincident predicate still observes the same pre-slot hit state.
        """
        if domain not in self.domains:
            raise Refused(Refusal(
                "multiclock-ready-unknown-domain",
                "a readiness query names a domain absent from this machine",
                domain=str(domain), domains=list(self.domains)))
        before = _state_copy(self.kernel.state)
        instance = self._instance(before)
        predicate = getattr(instance, f"{domain}_ready", None)
        if predicate is None:
            return True, ()
        if not callable(predicate):
            raise CandidateFault(Refusal(
                "multiclock-ready-not-callable",
                "the domain readiness member exists but is not callable",
                domain=domain))
        baseline = None if self._hit_log is None else tuple(self._hit_log)
        try:
            got = self._call_source(
                predicate, domain=domain, operation="ready",
                baseline=baseline, completed=())
            self._check_invariant()
            if self._hit_log is not None and any(
                    type(hit) is not str or not hit for hit in self._hit_log):
                raise CandidateFault(Refusal(
                    "multiclock-hit-record-malformed",
                    "a readiness predicate produced a non-string goal identifier",
                    domain=domain))
            if (self._hit_log is not None
                    and tuple(self._hit_log[:len(baseline)]) != baseline):
                raise CandidateFault(Refusal(
                    "multiclock-hit-record-malformed",
                    "a readiness predicate changed the pre-slot goal history",
                    domain=domain))
            added = (() if self._hit_log is None else tuple(sorted(
                set(self._hit_log[len(baseline):]))))
            after = self._checked_state(instance)
            if _diff(before, after):
                raise CandidateFault(Refusal(
                    "multiclock-ready-mutates-state",
                    "a readiness predicate changed state outside an atomic event slot",
                    domain=domain))
            if type(got) is not bool:
                raise CandidateFault(Refusal(
                    "multiclock-ready-not-boolean",
                    "a readiness predicate must return a Boolean",
                    domain=domain, got_type=type(got).__name__))
            return got, added
        finally:
            if self._hit_log is not None:
                self._hit_log[:] = baseline

    def ready(self, domain):
        """Return only the Boolean part of :meth:`ready_observation`."""

        return self.ready_observation(domain)[0]

    def step(self, slot, invocations=()):
        # A simultaneous full reset has one model-level meaning and is evaluated
        # once.  A proper subset needs explicit reset_<domain> transitions and is
        # handled by the kernel above.
        self._begin_slot()
        try:
            if (set(slot.events) == set(self.domains)
                    and set(slot.resets) == set(self.domains)):
                invocations = tuple(invocations)
                if invocations:
                    raise Refused(Refusal(
                        "multiclock-invocation-during-reset",
                        "a full-reset slot cannot also invoke domain handlers",
                        time=slot.time,
                        domains=[invocation.domain for invocation in invocations]))
                instance = self._instance(self.kernel.state)
                reset = getattr(instance, "reset", None)
                if not callable(reset):
                    raise CandidateFault(Refusal(
                        "multiclock-reset-missing",
                        "all domains reset together but the model declares no reset"))
                before = self.kernel._state_image
                self._branch(lambda: self._reset(
                    instance, reset), record_hits=False)
                self._check_invariant()
                after = self._checked_state(instance)
                self.kernel.ownership.check_state(after)
                transition = AtomicTransition(
                    slot, before, after,
                    [DomainEffect(
                        "__all_reset__", _diff(before.as_dict(), after))])
                self.kernel.state = transition.after.as_dict()
                self.kernel._state_image = transition.after
                self._released_domains.clear()
            else:
                unavailable = sorted(
                    domain for domain in set(slot.events) & set(slot.resets)
                    if domain not in self._domain_resets
                    and domain in self._released_domains)
                if unavailable:
                    raise Refused(Refusal(
                        "multiclock-independent-reset-unavailable",
                        "a domain-local reset was reasserted after that domain left "
                        "the common reset state, but the Behavior source declares no "
                        "reset_<domain> transition",
                        domains=unavailable, time=slot.time))
                transition = self.kernel.step(slot, invocations)
                self._released_domains.update(
                    domain for domain in slot.events
                    if domain not in slot.resets)
            if any(effect.writes for effect in transition.effects):
                self._install()
        except BaseException:
            self._finish_slot(commit=False)
            raise
        self._finish_slot(commit=True)
        return transition


class IsolatedAtomicMachine:
    """Atomic multi-clock machine whose candidate executes only in its child.

    The verifier owns the global schedule and consumes ownership from the named
    frontend judge.  The child owns construction, readiness, handler execution and the
    one atomic commit.
    Every reply crosses the exact native-value wire and is rebuilt as the typed
    records above; there is no serial-call fallback.
    """

    def __init__(self, source, ownership, domains, *, file="behavior.py",
                 cls=None, limits=None, path=(), config=None, recorded=None,
                 memory_interfaces=(), memory_environment=None,
                 memory_horizon=None, source_rules=EMPTY_SOURCE_RULES):
        if type(ownership) is not DomainOwnership:
            raise TypeError("isolated atomic replay needs DomainOwnership")
        from .isolated import Broken, IsolatedModel, Timeout

        self._model = None
        try:
            self._model = IsolatedModel(
                source, file=file, cls=cls, limits=limits, path=path,
                config=config, recorded=recorded, strict_module=True,
                source_rules=source_rules)
            if memory_environment is not None:
                self._model._bind_external_memory(
                    memory_interfaces, memory_environment, memory_horizon,
                    representation="array")
            elif memory_interfaces or memory_horizon is not None:
                raise ValueError(
                    "atomic external-memory replay binds interfaces, environment "
                    "and horizon together")
            self._model.atomic_init(ownership, domains)
        except (Broken, Timeout) as exc:
            if self._model is not None:
                self._model.close()
            raise Refused(Refusal(
                "multiclock-isolated-worker-broken",
                "the namespace-contained atomic Behavior worker stopped before "
                "it produced a validated initial state",
                error=f"{type(exc).__name__}: {exc}")) from None
        except BaseException:
            if self._model is not None:
                self._model.close()
            raise

    def _invoke(self, operation):
        from .isolated import Broken, Timeout

        try:
            return operation()
        except (Broken, Timeout) as exc:
            self.close()
            raise Refused(Refusal(
                "multiclock-isolated-worker-broken",
                "the namespace-contained atomic Behavior worker stopped before "
                "it produced a validated reply",
                error=f"{type(exc).__name__}: {exc}")) from None

    def ready(self, domain):
        return self._invoke(lambda: self._model.atomic_ready(domain))

    @property
    def state(self):
        """The last validated native commit, including the initial source state."""
        return self._model._atomic_state

    def ready_observation(self, domain):
        """Return the isolated old-state Boolean and its source goal hits."""

        return self._invoke(
            lambda: self._model.atomic_ready_observation(domain))

    def step(self, slot, invocations=()):
        return self._invoke(lambda: self._model.atomic_step(slot, invocations))

    def close(self):
        if self._model is not None:
            self._model.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
