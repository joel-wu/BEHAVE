"""Immutable value records used by semantic identities.

``Frozen`` blocks attribute assignment and deletion after construction; subclasses
must validate and freeze nested values before sealing. ``FrozenMap`` adds indexed
read-only lookup while retaining deterministic sorted pairs for serialization.
"""

from __future__ import annotations

import collections.abc
from types import MappingProxyType

__all__ = ["Frozen", "FrozenMap", "freeze", "frozen_map"]


def freeze(value):
    """A value with no mutable part, for a field of a frozen record.

    Mappings become sorted tuples of pairs rather than any mapping type, because two
    records built in different orders have to be one record and a hash over insertion
    order is not an identity. Sets sort for the same reason.
    """
    if isinstance(value, dict):
        return tuple(sorted((k, freeze(v)) for k, v in value.items()))
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(freeze(v) for v in value))
    if type(value) is tuple:
        changed = None
        for index, item in enumerate(value):
            child = freeze(item)
            if changed is None and child is not item:
                changed = list(value[:index])
            if changed is not None:
                changed.append(child)
        return value if changed is None else tuple(changed)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


class Frozen:
    """A record that refuses assignment once it is built.

    Subclasses set their fields inside `__init__` as usual and call `self._freeze()` on
    the last line. Anything after that raises, including from outside the class: there is
    no private-by-convention here, because the reproduction above went through the public
    attribute.
    """

    __slots__ = ("_frozen",)

    def _freeze(self):
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name, value):
        if getattr(self, "_frozen", False):
            raise AttributeError(
                f"{type(self).__name__}.{name} cannot be reassigned: this record's "
                f"digest is its identity, and evidence bound to a digest whose record "
                f"changed afterwards is evidence about something nobody can recover. "
                f"Build a new one.")
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if getattr(self, "_frozen", False):
            raise AttributeError(
                f"{type(self).__name__}.{name} cannot be deleted, for the reason it "
                f"cannot be reassigned")
        object.__delattr__(self, name)


class FrozenMap(collections.abc.Mapping):
    """Frozen values with deterministic ordering and constant-time lookup.

    Sorted pairs define the serialized identity. A private read-only index serves
    mapping operations without rescanning those pairs for every key.
    """

    __slots__ = ("_index", "_pairs")

    def __init__(self, items=()):
        if hasattr(self, "_pairs"):
            raise AttributeError("a FrozenMap cannot be reinitialized")
        if type(items) is FrozenMap:
            object.__setattr__(self, "_pairs", items._pairs)
            object.__setattr__(self, "_index", items._index)
            return
        pairs = tuple(sorted((k, freeze(v)) for k, v in dict(items).items()))
        object.__setattr__(self, "_pairs", pairs)
        object.__setattr__(self, "_index", MappingProxyType(dict(pairs)))

    def __getitem__(self, key):
        return self._index[key]

    def __iter__(self):
        return iter(self._index)

    def __len__(self):
        return len(self._pairs)

    def __setattr__(self, name, value):
        raise AttributeError("a FrozenMap cannot be changed; build a new one")

    def __delattr__(self, name):
        raise AttributeError("a FrozenMap cannot be changed; build a new one")

    def __reduce__(self):
        return type(self), (self._pairs,)

    def keys(self):
        return self._index.keys()

    def items(self):
        return self._index.items()

    def values(self):
        return self._index.values()

    def pairs(self):
        """The sorted `(key, value)` tuples, for a digest."""
        return self._pairs

    def __repr__(self):
        return f"FrozenMap({dict(self._pairs)!r})"


_EMPTY_MAP = FrozenMap()


def frozen_map(items=()):
    """Share exact frozen maps and the empty value; detach all other inputs."""
    if type(items) is FrozenMap:
        return items
    if type(items) in (dict, tuple, list) and not items:
        return _EMPTY_MAP
    return FrozenMap(items)
