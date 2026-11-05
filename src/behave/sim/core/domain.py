"""The set of traces every statement in a certificate is about.

A verdict is never about an artifact on its own. "No admissible trace reaches
this goal" is a statement about a bound, a contract, a set of initial states and
a schema saying what the endpoints carry. Change any of them and the same
artifact gives a different answer, and narrowing the contract can only enlarge
the set called unreachable.

Those semantic facts travelled separately: the bound was an argument, the
contract came from one method, the initial state was inside the lowered program
where nothing could see it, and the schema was read out of the candidate RTL
text. A reader wanting to know what a verdict was relative to had to collect
them from several places and trust that they described one run.

So they are one record, produced once by the frontend and carried by the
certificate. That is what lets two certificates be compared: same domain, and
the numbers mean the same thing; different domain, and they do not, whatever
the numbers say.

The versions are here for the same reason. A goal set is what a policy decided
is worth reporting, and a frontend that changes what it lowers changes what is
reachable; a record that does not name them is a claim nobody can reproduce.
"""

from __future__ import annotations

import hashlib
import json
import math

from .frozen import Frozen, FrozenMap

__all__ = ["TraceDomain"]


class TraceDomain(Frozen):
    """One immutable, canonical snapshot of a set of traces.

    The public collection properties deliberately return fresh ordinary Python
    values.  Callers historically consume dictionaries and lists here; exposing
    an immutable wrapper would change that protocol, while exposing the stored
    dictionaries would let a caller change the domain after evidence had been
    tagged with its digest.  The only stored representation is canonical JSON,
    so neither the constructor's inputs nor a returned view can alias it.
    """

    __slots__ = ("_canonical", "_digest")

    def __init__(self, bound, contract=None, initial=None, schema=None,
                 frontend=None, policy=None, artifact=None, task_contract=None):
        if type(bound) is not int or bound < 1:
            raise ValueError(
                f"a bounded trace domain needs a positive integer horizon, got "
                f"{bound!r}")
        if task_contract is not None \
                and (type(task_contract) is not str or not task_contract):
            raise ValueError(
                "a task-contract identity must be a nonempty string or absent")
        body = {
            # How many steps a trace has. Exactly this many: a run that fails
            # stutters to the length rather than stopping short.
            "bound": bound,
            # What the environment may present at each step.
            "contract": _mapping(contract, "contract"),
            # Where a run may start. A query over states the artifact never
            # reaches is a different and weaker question than whether a run
            # does.
            "initial": _mapping(initial, "initial"),
            # What each endpoint carries.
            "schema": _mapping(schema, "schema"),
            "frontend": frontend,
            "policy": policy,
            # Whatever identifies the artifact this is a domain over.
            "artifact": artifact,
            # The task-owned interface/environment contract whose admissible
            # traces this exact instance represents.  It is separate from the
            # finite input-row contract above: the latter validates semantic
            # steps, while this digest prevents an exact model for one task
            # from being attached to another task's concrete evaluation.
            "task_contract": task_contract,
        }
        body = _json_snapshot(body)
        try:
            # Keep deterministic whitespace and ASCII choices.  Semantic fields
            # deliberately change the digest; construction order does not.
            # ``allow_nan=False`` and the validation above make this strict JSON.
            canonical = json.dumps(body, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise TypeError(
                f"a TraceDomain has no finite canonical JSON image: {exc}") \
                from None
        self._canonical = canonical
        # This value is an evidence identity, not a display abbreviation.  A
        # truncated prefix cannot support the implication used at the evidence
        # boundary (equal identity => equal canonical domain), so retain the
        # complete SHA-256 image.  Callers may abbreviate it only when rendering
        # non-authoritative text.
        self._digest = hashlib.sha256(canonical.encode()).hexdigest()
        self._freeze()

    def _field(self, name):
        # ``_canonical`` was produced by this class from strict JSON. Loading it
        # gives the caller a deep copy and cannot execute caller-owned code.
        return json.loads(self._canonical)[name]

    @property
    def bound(self):
        return self._field("bound")

    @property
    def contract(self):
        return self._field("contract")

    @property
    def initial(self):
        return self._field("initial")

    @property
    def schema(self):
        return self._field("schema")

    @property
    def frontend(self):
        return self._field("frontend")

    @property
    def policy(self):
        return self._field("policy")

    @property
    def artifact(self):
        return self._field("artifact")

    @property
    def task_contract(self):
        return self._field("task_contract")

    def as_dict(self):
        decoded = json.loads(self._canonical)
        # Preserve the record's documented field order for human-facing
        # serialisation; nested mappings are already canonicalised by the JSON
        # snapshot and equality does not depend on insertion order.
        return {name: decoded[name] for name in (
            "bound", "contract", "initial", "schema",
            "frontend", "policy", "artifact", "task_contract")}

    def digest(self):
        """The stable SHA-256 identity of this set of traces.

        So that a trace, a query or a pool can carry which domain it belongs to
        without carrying the whole record, and a mismatch is one comparison rather
        than a walk over every semantic field. Two records with the same digest describe the
        same traces, which is what makes it usable as provenance.

        It is the complete SHA-256 digest of the canonical JSON of ``as_dict``.
        Keeping the complete digest matters because this identity is carried by
        evidence and checked without walking the whole domain record.
        """
        return self._digest

    def same_as(self, other):
        """Whether two records describe the same set of traces.

        What makes two certificates comparable. The versions count: a frontend
        that changed what it lowers changed what is reachable, so numbers from
        either side of that change are not numbers about the same thing even
        when the bound and the contract match.
        """
        if not isinstance(other, TraceDomain):
            return False
        return self._canonical == other._canonical

    def __repr__(self):
        return (f"TraceDomain(bound={self.bound}, "
                f"{len(self.contract)} inputs, frontend={self.frontend})")


def _mapping(value, field):
    """Copy one top-level mapping before the recursive snapshot is taken."""
    if value is None:
        return {}
    if type(value) is dict:
        return dict(value)
    if type(value) is FrozenMap:
        return dict(value.items())
    raise TypeError(
        f"TraceDomain.{field} must be an exact built-in mapping or FrozenMap")


def _json_snapshot(value, path="TraceDomain", active=None):
    """Return a detached tree in the exact, finite JSON value domain.

    JSON's permissive encoder silently stringifies non-string mapping keys and
    accepts NaN and infinities.  Both make identities ambiguous: ``1`` and
    ``"1"`` can become the same member, and non-finite numbers are not JSON at
    all.  Validate before encoding so the canonical bytes are a faithful image
    rather than a best-effort rendering.
    """
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if kind not in (dict, list, tuple):
        raise TypeError(
            f"{path} contains a {kind.__name__}, which has no canonical JSON image")

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
                    raise TypeError(
                        f"{path} has a non-string mapping key {key!r}")
                out[key] = _json_snapshot(
                    item, f"{path}.{key}", active)
            return out
        return [
            _json_snapshot(item, f"{path}[{index}]", active)
            for index, item in enumerate(value)
        ]
    except RecursionError:
        raise ValueError(f"{path} is nested too deeply") from None
    finally:
        active.discard(identity)
