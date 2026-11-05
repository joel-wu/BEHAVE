"""Everything a slot can change, named once, so nothing can change unnoticed.

TODO3 T3-07 asks for one deterministic closed-loop executor whose exact product state
holds the artifact's registers and memories, the driver's queues and pointers, the memory
environment's state and current responses, and the finite monitor's -- and, the part that
matters, that **completeness follow from the constructed type and a transition-owner
reconciliation rather than from a backend marking components captured**.

The difference is the whole design. A backend that says "I captured everything" is
attesting to its own completeness, so a component added later is missed in exactly the
way nobody notices: the attestation still says yes. Here every component is declared with
an owner, a step is asked what it changed, and a change no owner declared is a refusal.
Adding an unrecorded mutable component makes the reconciliation fail, which is the
acceptance clause stated as a mechanism.

**A projection for search is not this.** A novelty key may hash a subset of the product
state so a frontier can call two states alike, and it may never justify a query or an
evidence claim -- so `NoveltyKey` is derived from a `ProductState` and cannot be built
without one.
"""

from __future__ import annotations

import hashlib

from .frozen import Frozen, FrozenMap, freeze
from .record_value import _immutable_identity_value
from .refusal import Refusal, Refused
from .stimulus import canonical_json

__all__ = ["Component", "NoveltyKey", "ProductLayout", "ProductState", "reconcile"]


def _state_digest(manifest, values):
    return hashlib.sha256(canonical_json({
        "manifest": [c.as_dict() for c in manifest],
        "values": [list(p) for p in values],
    }).encode()).hexdigest()


class Component(Frozen):
    """One part of the product state, and who owns it.

    ``owner`` is which participant may change it: `environment`, `artifact`, `driver`,
    `adapter`, `memory` or `monitor`. Declared rather than inferred, because the
    reconciliation's question is "did anything change that this owner was not entitled
    to change", and an owner recovered from what changed cannot answer it.

    ``in_novelty`` is whether a search may project this component into a novelty key. Off
    by default: a counter that ticks every cycle makes every state novel, and a frontier
    keyed on one explores nothing but the counter.
    """

    __slots__ = ("_stable_identity", "in_novelty", "name", "owner", "why")

    OWNERS = ("environment", "artifact", "driver", "memory", "monitor",
              "adapter")

    def __init__(self, name, owner, in_novelty=False, why=""):
        self.name = str(name)
        if owner not in self.OWNERS:
            raise Refused(Refusal(
                "product-unknown-owner",
                "a component's owner is which participant may change it, and this is "
                "not one of them",
                component=self.name, owner=str(owner), known=list(self.OWNERS)))
        self.owner = str(owner)
        self.in_novelty = bool(in_novelty)
        #: What this component is, for a reader of the manifest. Carried, never parsed.
        self.why = str(why)
        # These declaration fields never change during a run. Establish their
        # lazy-identity eligibility once, not twice for every product transition.
        self._stable_identity = _immutable_identity_value(
            (self.name, self.owner, self.in_novelty, self.why))
        self._freeze()

    def as_dict(self):
        return {"name": self.name, "owner": self.owner,
                "in_novelty": self.in_novelty, "why": self.why}

    def __repr__(self):
        return f"Component({self.name} of {self.owner})"


class ProductLayout(Frozen):
    """Declaration-only layout shared by the states of one product.

    No values, transitions or reconciliation results live here. ``declarations``
    retains the caller's order for detecting edits to a Driver's manifest;
    ``manifest`` and ``names`` retain the canonical serialized state order.
    """

    __slots__ = ("declarations", "manifest", "names", "name_set", "positions",
                 "components", "immutable", "stable_identity")

    def __init__(self, declarations):
        self.declarations = tuple(declarations)
        self.manifest = tuple(sorted(self.declarations, key=lambda c: c.name))
        first = {}
        for component in self.manifest:
            # Preserve the existing first-declaration ownership lookup, even
            # when a diagnostic caller supplies repeated component names.
            first.setdefault(component.name, component)
        self.names = tuple(first)
        self.name_set = frozenset(first)
        self.positions = FrozenMap((name, index) for index, name in enumerate(first))
        self.components = FrozenMap(first)
        self.immutable = all(type(c) is Component for c in self.manifest)
        self.stable_identity = self.immutable and all(c._stable_identity for c in self.manifest)
        self._freeze()


class ProductState(Frozen):
    """One state of the closed loop, over a declared set of components.

    Built against a manifest -- the components a run declares it has -- so a value with
    no component is refused on the way in rather than discovered on the way out. That is
    the direction that catches things: a backend that grows a field and does not declare
    it fails here, where one that only checked the other direction would carry the new
    field along and never mention it.
    """

    __slots__ = ("_digest", "_layout", "manifest", "values")

    def __init__(self, manifest, values):
        layout = manifest if isinstance(manifest, ProductLayout) else ProductLayout(manifest)
        self._layout = layout
        self.manifest = layout.manifest
        values = dict(values)
        known = layout.name_set
        if values.keys() != known:
            undeclared = sorted(values.keys() - known)
            if undeclared:
                raise Refused(Refusal(
                    "product-undeclared-component",
                    "this state carries components the manifest does not declare, so "
                    "nothing says who may change them or whether they belong in an identity",
                    undeclared=undeclared, declared=sorted(known)))
            missing = sorted(known - values.keys())
            raise Refused(Refusal(
                "product-missing-component",
                "the manifest declares components this state does not carry, so the "
                "state is not a whole state and a comparison against another would be "
                "over different things",
                missing=missing))
        self.values = tuple((name, freeze(values[name])) for name in layout.names)
        # Driver ownership reconciliation reads the complete state, not its
        # hash. Defer hashing only when neither the manifest nor any value can
        # change its JSON projection. Other carriers keep the original eager
        # validation/digest semantics, including constructor-time refusals.
        lazy = (type(self) is ProductState
                and layout.stable_identity
                and _immutable_identity_value(self.values))
        self._digest = None if lazy else _state_digest(self.manifest, self.values)
        self._freeze()

    def __getitem__(self, name):
        return self.values[self._layout.positions[name]][1]

    def owner_of(self, name):
        if not self._layout.immutable:
            return next((c.owner for c in self.manifest if c.name == name), None)
        component = self._layout.components.get(name)
        return None if component is None else component.owner

    @property
    def digest(self):
        if self._digest is None:
            object.__setattr__(self, "_digest", _state_digest(self.manifest, self.values))
        return self._digest

    def as_dict(self):
        return {"manifest": [c.as_dict() for c in self.manifest],
                "values": [list(p) for p in self.values]}

    def __eq__(self, other):
        return isinstance(other, ProductState) and self.digest == other.digest

    def __hash__(self):
        return hash(self.digest)

    def __repr__(self):
        return f"ProductState({len(self.manifest)} components, {self.digest[:8]})"


class NoveltyKey(Frozen):
    """A projection of a product state, for a search that wants states to be alike.

    Derived from a `ProductState` and impossible to build without one, because the thing
    it must never become is an independent notion of state that a query or an evidence
    claim could rest on. It is telemetry: two states with one key are two states.
    """

    __slots__ = ("key", "of")

    def __init__(self, state):
        chosen = {c.name for c in state.manifest if c.in_novelty}
        self.of = state.digest
        self.key = hashlib.sha256(canonical_json(
            [[k, v] for k, v in state.values if k in chosen]).encode()).hexdigest()
        self._freeze()

    def __eq__(self, other):
        return isinstance(other, NoveltyKey) and self.key == other.key

    def __hash__(self):
        return hash(self.key)

    def __repr__(self):
        return f"NoveltyKey({self.key[:8]} of {self.of[:8]})"


def reconcile(before, after, changed_by):
    """Refuse a step that changed something its owner was not entitled to change.

    ``changed_by`` maps an owner to what it says it changed. Two ways to fail and they
    are different findings:

    * a component changed and **no owner claimed it** -- something mutated state outside
      the loop, which is the case a backend's self-attestation cannot see;
    * an owner claimed a component that did not change -- the claim is wrong, and a
      report that trusts these claims is reporting a step that did not happen.

    Both refuse. The second may look pedantic until a claim is what a later reader uses
    to decide which components a query has to pin.
    """
    if before.manifest != after.manifest:
        raise Refused(Refusal(
            "product-manifest-changed",
            "the two states declare different components, so they are not two states of "
            "one product and a difference between them is not a transition",
            before=[c.name for c in before.manifest],
            after=[c.name for c in after.manifest]))

    if (type(before) is ProductState and type(after) is ProductState
            and before._layout.immutable and after._layout.immutable):
        # Construction enforces the same complete, sorted name set for equal
        # manifests. Compare each actual value once rather than searching the
        # before-state's pairs again for every component.
        moved = {name for (name, old), (_, new) in zip(before.values, after.values)
                 if old != new}
    else:
        moved = {name for name, value in after.values if before[name] != value}
    claimed = {}
    for owner, names in dict(changed_by or {}).items():
        for name in names:
            if name in claimed:
                raise Refused(Refusal(
                    "product-two-owners-claim",
                    "two participants claim to have changed one component. The manifest "
                    "puts it under one of them, so at most one claim is true -- and "
                    "taking the last one written makes the check's answer depend on "
                    "dictionary order",
                    component=str(name),
                    claimed_by=sorted([claimed[name], str(owner)])))
            claimed[name] = owner

    unclaimed = sorted(moved - set(claimed))
    if unclaimed:
        raise Refused(Refusal(
            "product-unclaimed-change",
            "these components changed and no owner declared changing them, so something "
            "mutated the product state outside the loop -- which is exactly what a "
            "backend attesting to its own completeness cannot report",
            components=unclaimed,
            owners={n: before.owner_of(n) for n in unclaimed}))

    phantom = sorted(set(claimed) - moved)
    if phantom:
        raise Refused(Refusal(
            "product-phantom-change",
            "these components were claimed as changed and did not change; a later "
            "reader uses these claims to decide what a query must pin, so a claim that "
            "is not true is worse than no claim",
            components=phantom, claimed_by={n: claimed[n] for n in phantom}))

    wrong = sorted(n for n in moved
                   if before.owner_of(n) != claimed[n])
    if wrong:
        raise Refused(Refusal(
            "product-wrong-owner",
            "these components were changed by a participant the manifest does not put "
            "them under",
            components=wrong,
            declared={n: before.owner_of(n) for n in wrong},
            claimed={n: claimed[n] for n in wrong}))
    return tuple(sorted(moved))
