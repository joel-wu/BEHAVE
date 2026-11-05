"""Two-state RTLIL case matching shared by replay and symbolic goals.

An arm is a union of constant/masked patterns, excluding all earlier arms.
Masks stay compact: a pattern with 127 don't-care bits is not enumerated.
Only RTLIL's explicit ``-`` marker is a wildcard here; X/Z remain unsupported.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from behave.core import runtime_defaults as defaults

from ..core.frozen import Frozen, FrozenMap

Pattern = tuple[int | None, int | None, int]  # width, care mask, value
_DEFAULTS = ("", "default", "no-match")


@lru_cache(maxsize=defaults.SIM_RTL_SWITCH_CACHE_ENTRIES)
def match_identity(siblings: tuple[str, ...], activation: tuple[tuple[str, ...], ...]):
    """Short occurrence suffix and full SHA-256 of the same ordered match image.

    Complete labels stay in the executable site table. Manifests name their
    identity instead of copying the table into every occurrence. The bounded
    cache contains only immutable value-derived identities, never query answers.
    """
    payload = json.dumps((siblings, activation), ensure_ascii=True,
                         separators=(",", ":")).encode("utf-8")
    return (hashlib.blake2s(payload, digest_size=6).hexdigest(),
            hashlib.sha256(payload).hexdigest())


def match_schema(label, siblings):
    """Versioned arm coordinate into the complete ordered switch, not its prefix."""
    siblings = tuple(siblings)
    return ["ordered-switch/1", {
        "match_sha256": match_identity(siblings, ())[1],
        "arm": siblings.index(label),
    }]


def invalid_label(siblings):
    """Validate each written pattern once when a complete switch is declared."""
    for label in siblings:
        if label.strip() not in _DEFAULTS and _patterns(label) is None:
            return label
    return None


def _patterns(label: str) -> tuple[Pattern, ...] | None:
    groups = label.split(",")
    if any(not group.strip() for group in groups):
        return None
    patterns = []
    for item in (item for group in groups for item in group.split()):
        if "'" not in item:
            if not item.isdecimal():
                return None
            patterns.append((None, None, int(item)))
            continue
        size, _, bits = item.partition("'")
        if (not size.isdecimal() or int(size) <= 0 or len(bits) != int(size)
                or any(bit not in "01-" for bit in bits)):
            return None
        value = int(bits.replace("-", "0"), 2)
        mask = int("".join("0" if bit == "-" else "1" for bit in bits), 2) \
            if "-" in bits else None
        patterns.append((int(size), mask, value))
    return tuple(dict.fromkeys(patterns)) or None


def match_rule(label, siblings):
    """Return ``(kind, (accepted, excluded), None)`` or ``(None, None, why)``.

    ``accepted=None`` is an unconditional/default arm. Exclusions encode priority,
    including a preceding default. Synthetic no-match excludes every written arm.
    The returned immutable value is also recorded in the catalogue identity.
    """
    label = str(label).strip()
    siblings = tuple(str(other).strip() for other in siblings)
    default = label in _DEFAULTS
    accepted = None if default else _patterns(label)
    if not default and accepted is None:
        return None, None, f"the label {label!r} is not a plain constant or masked pattern"
    try:
        position = siblings.index(label)
    except ValueError:
        if not default:
            return None, None, "the arm is absent from its switch's closed arm list"
        position = len(siblings)
    excluded = []
    for other in siblings[:position]:
        if other in _DEFAULTS:
            return "never", None, None
        patterns = _patterns(other)
        if patterns is None:
            return None, None, (
                f"an earlier sibling label {other!r} is not a plain constant or "
                "masked pattern, so the priority rule would be incomplete")
        excluded.extend(patterns)
    if accepted is None and not excluded:
        return "always", None, None
    return "patterns", (accepted, tuple(dict.fromkeys(excluded))), None


def _matches(pattern: Pattern, value: int) -> bool:
    width, mask, wanted = pattern
    if mask is None:
        return value == wanted
    return 0 <= value < (1 << width) and value & mask == wanted


def _term(pattern: Pattern, var, width):
    import z3

    size, mask, wanted = pattern
    if z3.is_bool(var):
        return z3.Or(*(
            var == z3.BoolVal(bool(value))
            for value in (0, 1) if _matches(pattern, value)))
    if z3.is_int(var):
        if mask is None:
            return var == wanted
        return z3.And(
            var >= 0, var < (1 << size),
            z3.Int2BV(var, size) & z3.BitVecVal(mask, size)
            == z3.BitVecVal(wanted, size))
    if wanted >= (1 << width):
        return z3.BoolVal(False)
    if mask is None:
        return var == z3.BitVecVal(wanted, width)
    # Valid RTLIL labels have the switch width. Zero-extension also keeps the
    # renderer consistent on narrower labels supplied through the direct API.
    if size < width:
        mask |= ((1 << width) - 1) ^ ((1 << size) - 1)
    return var & z3.BitVecVal(mask, width) == z3.BitVecVal(wanted, width)


def _condition(var, width, label, siblings):
    """Render the shared rule as a Boolean solver term."""
    import z3

    kind, payload, why = match_rule(label, siblings)
    if kind is None:
        return None, why
    if kind in ("always", "never"):
        return z3.BoolVal(kind == "always"), None
    accepted, excluded = payload
    take = z3.BoolVal(True) if accepted is None else z3.Or(*(
        _term(pattern, var, width) for pattern in accepted))
    prior = z3.Or(*(_term(pattern, var, width) for pattern in excluded))
    return z3.And(take, z3.Not(prior)), None


def matches(label, siblings, value):
    """Evaluate the shared rule on one concrete, two-state signal value."""
    kind, payload, why = match_rule(label, siblings)
    if kind is None:
        return None, why
    if not isinstance(value, int):
        return None, f"the sampled value {value!r} is not a number"
    if kind in ("always", "never"):
        return kind == "always", None
    accepted, excluded = payload
    take = accepted is None or any(_matches(pattern, value) for pattern in accepted)
    return take and not any(_matches(pattern, value) for pattern in excluded), None


class _ArmSelection(Frozen):
    """A read-only arm lookup sharing one switch's label-to-rule table."""

    __slots__ = ('_positions', '_winner')

    def __init__(self, positions, winner):
        self._positions, self._winner = positions, winner
        self._freeze()

    def __contains__(self, label):
        return label in self._positions

    def __getitem__(self, label):
        return self._positions[label] == self._winner, None


class SwitchMatcher(Frozen):
    """A closed ordered switch, parsed once and sampled without copied prefixes."""

    __slots__ = ('_arms', '_rules', '_unavailable', '_literal_index', '_selections')

    def __init__(self, siblings):
        rules, arms, positions = [], [], {}
        # The first invalid predecessor or default decides the remaining prefix.
        # An invalid current label still takes precedence over either diagnostic.
        prior_error, prior_default = None, False
        for original in siblings:
            label = str(original).strip()
            if label not in positions:
                patterns = None if label in _DEFAULTS else _patterns(label)
                bad = patterns is None and label not in _DEFAULTS
                why = (f"the label {label!r} is not a plain constant or masked pattern"
                       if bad else prior_error)
                positions[label] = len(rules)
                rules.append((patterns, prior_default, why))
                if prior_error is None and not prior_default:
                    if bad:
                        prior_error = (
                            f"an earlier sibling label {label!r} is not a plain constant or "
                            "masked pattern, so the priority rule would be incomplete")
                    elif patterns is None:
                        prior_default = True
            arms.append((original, positions[label]))
        self._rules, self._arms = tuple(rules), tuple(arms)
        self._unavailable = None
        self._literal_index, self._selections = None, ()
        if any(type(label) is not str for label, _index in arms):
            # Preserve the legacy evaluator for caller-supplied label carriers.
            self._freeze()
            return
        # None denotes an unavailable reading, never a miss. Its per-arm
        # diagnostics depend only on this closed switch, not on a signal or slot.
        self._unavailable = FrozenMap({
            label: (None, rules[index][2] or 'the sampled value None is not a number')
            for label, index in arms})
        if all(why is None and (patterns is None or all(mask is None
                for _width, mask, _value in patterns)) for patterns, _never, why in rules):
            # Compile equality-only arms. Earlier arms own duplicate values;
            # the first default owns every remaining value. Do not enumerate a
            # bit-vector domain or expand masks. Absent/default is the -1 entry.
            winners, default = {}, -1
            for index, (patterns, never, _why) in enumerate(rules):
                if never:
                    continue
                if patterns is None:
                    default = index
                else:
                    for _width, _mask, value in patterns:
                        winners.setdefault(value, index)
            positions = FrozenMap(arms)
            self._literal_index = FrozenMap(winners)
            self._selections = tuple(_ArmSelection(positions, index)
                                     for index in (*range(len(rules)), default))
        self._freeze()

    def decision(self, value):
        """Read-only outcomes without materializing every arm for each reading.

        Selection always uses the current value. These are compiled pure rules,
        not cached signal readings or goal hits. Other values and masked/error
        rules retain the ordered evaluator, including its diagnostics.
        """
        if value is None and self._unavailable is not None:
            return self._unavailable
        if self._literal_index is not None and type(value) in (int, bool):
            return self._selections[self._literal_index.get(value, -1)]
        return self.sample(value)

    def sample(self, value):
        outcomes, prior = [], False
        unreadable = None if isinstance(value, int) else (
            f"the sampled value {value!r} is not a number")
        for patterns, never, why in self._rules:
            if why is not None or unreadable is not None:
                outcomes.append((None, why or unreadable))
                continue
            take = not never and not prior and (patterns is None or any(
                _matches(pattern, value) for pattern in patterns))
            outcomes.append((take, None))
            prior |= take
        return {label: outcomes[index] for label, index in self._arms}


def switch_matches(siblings, value):
    """Evaluate all arms, preserving the per-arm rule's exact refusal reasons."""
    return SwitchMatcher(siblings).sample(value)
