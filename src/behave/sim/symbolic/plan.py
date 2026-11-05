"""Solver-free finite representation planning and encoding-boundary values.

The plan records the finite carriers and array shapes that ``encode_step`` may
use. It is a value: immutable, pickle-stable, and bound to the exact procedure,
range environment, and finite horizon from which it was derived. Supplying a
plan for another boundary is an error detected before solver terms are built.

``EncodingPlan`` describes one step. Finite-run layer construction emits an
unproved draft. ``closure_proof`` owns transition proofs, checked position
plans and their preparation lifetime; this planner never invokes a solver.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..core.catalogue import OccurrenceKey as CatalogueOccurrenceKey
from . import cfg, ir, primitives, typecheck as types
from . import ranges
from .capabilities import SCALAR_BITS, TERM_BUDGET

__all__ = [
    "EncodingPlan",
    "ExpressionPosition",
    "FiniteCarrier",
    "OccurrenceCarriers",
    "PlanMismatch",
    "PositionEncodingPlan",  # noqa: F822 -- lazy export through __getattr__
    "Unresolved",
    "plan_encoding",
    "plan_positions",  # noqa: F822 -- lazy export through __getattr__
    "position_analysis_identities",  # noqa: F822 -- lazy export through __getattr__
    "prepare_positions",  # noqa: F822 -- lazy export through __getattr__
    "procedure_identity",
    "verify",  # noqa: F822 -- lazy export through __getattr__
]


_PLAN_VERSION = "encoding-plan/15"
_NO_BOUND = object()


class PlanMismatch(ValueError):
    """A plan was presented outside the boundary to which it is bound."""


@dataclass(frozen=True, order=True)
class ExpressionPosition:
    """A stable internal expression position within a procedure body.

    This is not a second public occurrence authority.  :meth:`catalogue_key`
    is the sole, injective adapter to the core key used by manifests and
    evidence; the ``"expression"`` path tag keeps these implementation sites
    disjoint from source goal paths owned by the Behavior catalogue.
    """

    command: int
    role: str
    path: tuple[int, ...] = ()

    def __str__(self):
        tail = "".join(f"/{part}" for part in self.path)
        return f"command[{self.command}].{self.role}{tail}"

    def catalogue_key(self):
        _checked_occurrence_key(self)
        return CatalogueOccurrenceKey(
            "behavior", ("expression", self.command, *self.path), self.role)

    @classmethod
    def from_catalogue_key(cls, key):
        if type(key) is not CatalogueOccurrenceKey:
            raise TypeError("an expression position needs a core OccurrenceKey")
        if (key.provider != "behavior" or len(key.path) < 2
                or key.path[0] != "expression"):
            raise ValueError("the core occurrence is not a Behavior expression")
        position = cls(key.path[1], key.role, tuple(key.path[2:]))
        _checked_occurrence_key(position)
        return position


@dataclass(frozen=True)
class Unresolved:
    """One type, shape, range, width, capability, or resource question unanswered."""

    kind: str
    what: object
    detail: str = ""

    def as_dict(self):
        return {
            "kind": self.kind,
            "what": str(self.what),
            "detail": self.detail,
        }

    def __repr__(self):
        return f"{self.kind}: {str(self.what)[:60]}"


class _UniqueUnresolved:
    """Insertion-ordered diagnostics with constant-time duplicate checks."""

    __slots__ = ("_items", "_keys")

    def __init__(self):
        self._items = []
        self._keys = set()

    def append(self, item):
        key = (item.kind, _diagnostic_image(item.what), item.detail)
        if key in self._keys:
            return
        self._keys.add(key)
        self._items.append(item)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]


@dataclass(frozen=True, order=True)
class FiniteCarrier:
    """One finite integer interval and its injective signed encoding.

    The implementation represents a scalar interval rather than enumerating it.
    That is still the complete finite carrier: every integer from ``lo`` through
    ``hi`` is present, and ``decode`` is defined exactly on the image of
    ``encode``.  Recursive arrays use this carrier independently for each leaf;
    their fixed shape remains in the corresponding :class:`EncodingPlan`.
    """

    lo: int
    hi: int
    width: int

    def __post_init__(self):
        if any(type(value) is not int for value in (self.lo, self.hi, self.width)):
            raise TypeError("a finite carrier has integer endpoints and width")
        if self.hi < self.lo:
            raise ValueError("a finite carrier is nonempty")
        expected = ranges.encoding_width(ranges.Range(self.lo, self.hi))
        if self.width < expected:
            raise ValueError(
                f"the carrier [{self.lo},{self.hi}] needs at least {expected} "
                f"bits, not {self.width}")

    @classmethod
    def of(cls, rng):
        if not isinstance(rng, ranges.Range):
            raise TypeError("a finite carrier is derived from a finite Range")
        return cls(int(rng.lo), int(rng.hi), ranges.encoding_width(rng))

    @property
    def range(self):
        return _PlanRange(self.lo, self.hi)

    def encode(self, value):
        """Return the ``width``-bit image of one member."""
        if type(value) is not int or not self.lo <= value <= self.hi:
            raise ValueError(f"{value!r} is outside [{self.lo},{self.hi}]")
        return value & ((1 << self.width) - 1)

    def decode(self, image):
        """Decode an image member, or ``None`` outside the carrier image."""
        if type(image) is not int or not 0 <= image < (1 << self.width):
            return None
        value = image - (1 << self.width) \
            if image & (1 << (self.width - 1)) else image
        return value if self.lo <= value <= self.hi else None


class _PlanRange(ranges.Range):
    """An immutable ``Range`` usable by the existing range transformers."""

    __slots__ = ()

    def __init__(self, lo, hi):
        lo, hi = int(lo), int(hi)
        if hi < lo:
            raise ValueError(f"an interval from {lo} to {hi}")
        object.__setattr__(self, "lo", lo)
        object.__setattr__(self, "hi", hi)

    def __setattr__(self, name, value):
        raise AttributeError("an encoding-plan range is immutable")

    def __reduce__(self):
        return type(self), (self.lo, self.hi)


def _frozen_value(value):
    if type(value) in (str, int, bool, type(None)):
        return value
    if isinstance(value, _PlanRange):
        return value
    if type(value) is ranges._CellRange:
        return ranges._CellRange(
            tuple(_frozen_value(cell) for cell in value.cells))
    if type(value) is ranges._UniformCellRange:
        return ranges._UniformCellRange._from_runs(
            value.length,
            _frozen_value(value.default),
            tuple((start, stop, _frozen_value(cell))
                  for start, stop, cell in value.runs),
        )
    if isinstance(value, ranges.Range):
        return _PlanRange(value.lo, value.hi)
    if type(value) is FiniteCarrier:
        return value
    if isinstance(value, tuple):
        return tuple(_frozen_value(item) for item in value)
    raise TypeError(f"an encoding-plan value of type {type(value).__name__}")


class _FrozenMap(Mapping):
    """A small immutable, pickle-safe mapping with deterministic order."""

    __slots__ = ("_index", "_pairs")

    def __init__(self, values=()):
        values = dict(values)
        pairs = tuple(sorted(
            ((str(key), _frozen_value(value)) for key, value in values.items()),
            key=lambda item: item[0]))
        object.__setattr__(self, "_pairs", pairs)
        object.__setattr__(self, "_index", MappingProxyType(dict(pairs)))

    def __setattr__(self, name, value):
        raise AttributeError("an encoding-plan mapping is immutable")

    def __getitem__(self, key):
        return self._index[key]

    def __iter__(self):
        return (name for name, _ in self._pairs)

    def __len__(self):
        return len(self._pairs)

    def items(self):
        return self._index.items()

    def keys(self):
        return self._index.keys()

    def values(self):
        return self._index.values()

    def __reduce__(self):
        return type(self), (self._pairs,)

    def __repr__(self):
        return repr(dict(self._pairs))

    def __eq__(self, other):
        if type(other) is _FrozenMap:
            return self._pairs == other._pairs
        return isinstance(other, Mapping) \
            and dict(self._pairs) == dict(other.items())

    def __hash__(self):
        return hash(self._pairs)


class OccurrenceCarriers(Mapping):
    """Immutable leaf carriers addressed by stable expression occurrences."""

    __slots__ = ("_index", "_pairs")

    def __init__(self, values=()):
        values = dict(values)
        converted = []
        for key, value in values.items():
            _checked_occurrence_key(key)
            carrier = (value if type(value) is FiniteCarrier
                       else FiniteCarrier.of(value))
            converted.append((key, carrier))
        converted.sort(key=lambda item: (
            item[0].command, item[0].role, item[0].path))
        pairs = tuple(converted)
        object.__setattr__(self, "_pairs", pairs)
        object.__setattr__(self, "_index", MappingProxyType(dict(pairs)))

    def __setattr__(self, name, value):
        raise AttributeError("an occurrence-carrier table is immutable")

    def __getitem__(self, key):
        return self._index[key]

    def __iter__(self):
        return (key for key, _carrier in self._pairs)

    def __len__(self):
        return len(self._pairs)

    def items(self):
        return self._index.items()

    def keys(self):
        return self._index.keys()

    def values(self):
        return self._index.values()

    def __reduce__(self):
        return type(self), (self._pairs,)

    def __repr__(self):
        return repr(dict(self._pairs))

    def __eq__(self, other):
        return (type(other) is OccurrenceCarriers
                and other._pairs == self._pairs)

    def __hash__(self):
        return hash(self._pairs)


def _checked_occurrence_key(key):
    if type(key) is not ExpressionPosition:
        raise TypeError("an occurrence carrier is addressed by an ExpressionPosition")
    if (type(key.command) is not int or key.command < 0
            or type(key.role) is not str or not key.role
            or type(key.path) is not tuple
            or any(type(part) is not int or part < 0 for part in key.path)):
        raise ValueError("a malformed expression occurrence key")
    return key


def _occurrence_layer_image(layer):
    return tuple((key.command, key.role, key.path,
                  carrier.lo, carrier.hi, carrier.width)
                 for key, carrier in layer.items())


def _checked_loop_visits(values):
    """Freeze contextual loop maxima under stable command numbers."""
    converted = {}
    for raw_number, raw_entries in dict(values).items():
        if type(raw_number) is int:
            number = raw_number
        elif (type(raw_number) is str and raw_number.isdecimal()
              and str(int(raw_number)) == raw_number):
            number = int(raw_number)
        else:
            raise TypeError("a loop-visit key is a stable command number")
        if number < 0 or type(raw_entries) not in {tuple, list, dict}:
            raise ValueError("a loop-visit table is finite")
        entries = (raw_entries.items() if type(raw_entries) is dict
                   else raw_entries)
        checked = {}
        for context, maximum in entries:
            if (type(context) is not tuple
                    or any(type(item) is not tuple or len(item) != 2
                           or any(type(part) is not int or part < 0
                                  for part in item)
                           for item in context)):
                raise ValueError(
                    "a loop visit is addressed by outer command/index pairs")
            if (maximum is not None
                    and (type(maximum) is not int or maximum < 0)):
                raise ValueError(
                    "a contextual loop maximum is natural or unresolved")
            if context in checked:
                raise ValueError("a loop context occurs once in its visit table")
            checked[context] = maximum
        converted[str(number)] = tuple(sorted(checked.items()))
    return _FrozenMap(converted)


def _materialization_limit(item):
    return item.kind == "capability" and item.what == "encoding-expansion"


class EncodingPlan:
    """The immutable finite-carrier plan for one encoding boundary."""

    __slots__ = (
        "bound",
        "cells",
        "env",
        "environment_fingerprint",
        "expansion_cost",
        "fingerprint",
        "fixed_inputs",
        "loop_bounds",
        "loop_visits",
        "occurrence_carriers",
        "procedure_fingerprint",
        "shapes",
        "unresolved",
        "widths",
    )

    def __init__(self, widths, cells, shapes, loop_bounds, loop_visits,
                 occurrence_carriers, expansion_cost, bound, unresolved, env,
                 procedure_fingerprint, fixed_inputs=()):
        bound = _checked_bound(bound)
        widths = _FrozenMap(widths)
        cells = _FrozenMap(cells)
        shapes = _FrozenMap(shapes)
        loop_bounds = _FrozenMap(loop_bounds)
        loop_visits = _checked_loop_visits(loop_visits)
        occurrence_carriers = OccurrenceCarriers(occurrence_carriers)
        if type(expansion_cost) is not int or expansion_cost < 0:
            raise ValueError("an encoding expansion cost is a natural number")
        env = _FrozenMap(env)
        fixed_inputs = _fixed_input_map(fixed_inputs)
        unresolved = tuple(_checked_unresolved(item) for item in unresolved)
        environment_fingerprint = _environment_fingerprint(env)
        diagnostics = tuple(
            (item.kind, _diagnostic_image(item.what), item.detail)
            for item in unresolved)
        if any(not _materialization_limit(item) for item in unresolved):
            # A plan without finite carriers is diagnostic evidence. Do not
            # serialize every over-wide carrier merely to name the
            # refusal: that turns a deliberate width/capability answer into another
            # resource failure.  The procedure, input support, horizon and complete
            # diagnostic set are the identity of this unusable value.
            image = (
                _PLAN_VERSION, "incomplete", str(procedure_fingerprint),
                environment_fingerprint, fixed_inputs._pairs, bound, diagnostics)
        else:
            image = (
                _PLAN_VERSION,
                "complete",
                str(procedure_fingerprint),
                environment_fingerprint,
                fixed_inputs._pairs,
                bound,
                widths._pairs,
                cells._pairs,
                shapes._pairs,
                loop_bounds._pairs,
                loop_visits._pairs,
                _occurrence_layer_image(occurrence_carriers),
                expansion_cost,
                diagnostics,
            )
        for name, value in (
                ("widths", widths), ("cells", cells), ("shapes", shapes),
                ("loop_bounds", loop_bounds),
                ("loop_visits", loop_visits),
                ("occurrence_carriers", occurrence_carriers),
                ("expansion_cost", expansion_cost),
                ("bound", bound), ("unresolved", unresolved), ("env", env),
                ("fixed_inputs", fixed_inputs),
                ("procedure_fingerprint", str(procedure_fingerprint)),
                ("environment_fingerprint", environment_fingerprint),
                ("fingerprint", _digest(image))):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError("an EncodingPlan is immutable")

    def __reduce__(self):
        return type(self), (
            self.widths,
            self.cells,
            self.shapes,
            self.loop_bounds,
            self.loop_visits,
            self.occurrence_carriers,
            self.expansion_cost,
            self.bound,
            self.unresolved,
            self.env,
            self.procedure_fingerprint,
            self.fixed_inputs,
        )

    @property
    def complete(self):
        return not self.unresolved

    @property
    def representation_complete(self):
        """Finite carriers exist even if the whole formula cannot be built.

        A query may use fewer observations or computations. Its encoder still
        enforces the same materialization limit on actual retained commands.
        """
        return all(_materialization_limit(item) for item in self.unresolved)

    def validate_binding(self, proc, env, bound=_NO_BOUND, *,
                         _procedure_validation=None):
        """Check this plan's immutable procedure, environment, and layout binding."""

        # Fingerprinting and boundary-layout construction recursively inspect
        # types and values.  The central iterative gate must run first so a
        # hostile deep or cyclic public procedure cannot exhaust Python's
        # stack while merely asking whether this plan belongs to it.
        if _procedure_validation is None:
            ir.validate_procedure_representation(proc)
        else:
            from .validation import _checked_procedure_validation

            _checked_procedure_validation(_procedure_validation, proc)
        got_proc = _procedure_fingerprint(proc)
        if got_proc != self.procedure_fingerprint:
            raise PlanMismatch(
                "the encoding plan belongs to another procedure "
                f"({self.procedure_fingerprint[:12]} != {got_proc[:12]})")
        got_env = _environment_fingerprint(env)
        if got_env != self.environment_fingerprint:
            raise PlanMismatch(
                "the encoding plan belongs to another range environment "
                f"({self.environment_fingerprint[:12]} != {got_env[:12]})")
        _conditioned_environment(proc, env, self.fixed_inputs)
        expected_widths, expected_cells, expected_shapes, _ = \
            _boundary_layout(proc, env)
        if (self.widths != expected_widths
                or self.cells != expected_cells
                or self.shapes != expected_shapes):
            raise PlanMismatch(
                "the encoding plan's finite-carrier layout is not the layout "
                "derived from its procedure and range environment")
        if bound is not _NO_BOUND:
            bound = _checked_bound(bound)
            if self.bound is not None and bound != self.bound:
                raise PlanMismatch(
                    f"the encoding plan covers {self.bound} steps, not {bound}")
        return self

    def validate(self, proc, env, bound=_NO_BOUND):
        """Fully re-derive a public plan before any symbolic term is allocated.

        A finite-bound plan is valid only at that exact horizon. A fixpoint plan
        (``bound is None``) may be used at any positive horizon.
        """
        self.validate_binding(proc, env, bound)
        planning_env = _conditioned_environment(proc, env, self.fixed_inputs)
        command_numbers = _command_numbers(proc.body)
        (expected_loops, _, loop_exceeded, occurrence_envs,
         _) = _loop_plan(proc, planning_env, command_numbers)
        expected_cost, expected_visits = _contextual_expansion(
            proc, planning_env, expected_loops)
        expected_visits = _checked_loop_visits(expected_visits)
        expected_occurrences, _ = _occurrence_carriers(
            proc, command_numbers, occurrence_envs, planning_env,
            exceeded=loop_exceeded)
        if (self.loop_bounds != expected_loops
                or self.loop_visits != expected_visits
                or self.occurrence_carriers != expected_occurrences
                or self.expansion_cost != expected_cost):
            raise PlanMismatch(
                "the encoding plan's loop expansion or occurrence carriers "
                "are not those derived from its procedure and range environment")
        return self

    def replaced(self, *, widths=None, cells=None, shapes=None,
                 loop_bounds=None, loop_visits=None, occurrence_carriers=None,
                 expansion_cost=None, bound=_NO_BOUND, unresolved=None,
                 env=None, fixed_inputs=None):
        """Return a distinct plan value, principally for adversarial tests."""
        return type(self)(
            self.widths if widths is None else widths,
            self.cells if cells is None else cells,
            self.shapes if shapes is None else shapes,
            self.loop_bounds if loop_bounds is None else loop_bounds,
            self.loop_visits if loop_visits is None else loop_visits,
            (self.occurrence_carriers if occurrence_carriers is None
             else occurrence_carriers),
            self.expansion_cost if expansion_cost is None else expansion_cost,
            self.bound if bound is _NO_BOUND else bound,
            self.unresolved if unresolved is None else unresolved,
            self.env if env is None else env,
            self.procedure_fingerprint,
            self.fixed_inputs if fixed_inputs is None else fixed_inputs,
        )

    def __eq__(self, other):
        return type(other) is type(self) and other.fingerprint == self.fingerprint

    def __hash__(self):
        return hash(self.fingerprint)

    def __repr__(self):
        over = f"{self.bound} steps" if self.bound is not None else "any bound"
        return (f"EncodingPlan({len(self.widths)} widths, {len(self.cells)} arrays, "
                f"{len(self.loop_bounds)} loops, {self.expansion_cost} expansion "
                f"work, {over}, {len(self.unresolved)} unresolved, "
                f"id={self.fingerprint[:12]})")


def _carrier_map(values, *, width=None):
    converted = {}
    for name, value in dict(values).items():
        if type(value) is FiniteCarrier:
            target = value.width if width is None else width
            converted[name] = FiniteCarrier(value.lo, value.hi, target)
        elif isinstance(value, ranges.Range):
            minimum = ranges.encoding_width(value)
            converted[name] = FiniteCarrier(
                int(value.lo), int(value.hi),
                minimum if width is None else width)
        else:
            raise TypeError(
                f"the carrier of {name} is not a FiniteCarrier or Range")
    return _FrozenMap(converted)


def _intern_carrier_layers(layers, width):
    """Convert equal layer objects once and preserve immutable sharing."""
    by_object = {}
    canonical = {}
    out = []
    for layer in layers:
        identity = id(layer)
        converted = by_object.get(identity)
        if converted is None:
            converted = _carrier_map(layer, width=width)
            image = _carrier_layer_image(converted)
            converted = canonical.setdefault(image, converted)
            by_object[identity] = converted
        out.append(converted)
    return tuple(out)


def _widen_occurrences(values, width):
    values = OccurrenceCarriers(values)
    return OccurrenceCarriers({
        key: FiniteCarrier(carrier.lo, carrier.hi, width)
        for key, carrier in values.items()
    })


def _intern_occurrence_layers(layers, width):
    """Widen one immutable occurrence layer once per semantic image."""
    by_object = {}
    canonical = {}
    out = []
    for layer in layers:
        identity = id(layer)
        converted = by_object.get(identity)
        if converted is None:
            converted = _widen_occurrences(layer, width)
            image = _occurrence_layer_image(converted)
            converted = canonical.setdefault(image, converted)
            by_object[identity] = converted
        out.append(converted)
    return tuple(out)


def _layer_fingerprints(layers, image_of):
    """Merkle image of a layer sequence without copying repeated payloads."""
    by_object = {}
    out = []
    for layer in layers:
        identity = id(layer)
        fingerprint = by_object.get(identity)
        if fingerprint is None:
            fingerprint = _digest(image_of(layer))
            by_object[identity] = fingerprint
        out.append(fingerprint)
    return tuple(out)


def _carrier_layer_image(layer):
    return tuple((name, carrier.lo, carrier.hi, carrier.width)
                 for name, carrier in layer.items())


def _checked_unresolved(item):
    if type(item) is not Unresolved:
        raise TypeError("an unresolved plan item is an Unresolved value")
    if item.kind not in {
            "range", "shape", "type", "width", "capability", "resource"}:
        raise ValueError(f"unknown unresolved-plan kind {item.kind!r}")
    if type(item.what) is not str and type(item.what) is not ExpressionPosition:
        raise TypeError("an unresolved-plan subject is a name or ExpressionPosition")
    if type(item.detail) is not str:
        raise TypeError("an unresolved-plan detail is text")
    if isinstance(item.what, ExpressionPosition):
        key = item.what
        if (type(key.command) is not int or key.command < 0
                or type(key.role) is not str
                or type(key.path) is not tuple
                or any(type(part) is not int or part < 0 for part in key.path)):
            raise ValueError("a malformed expression occurrence key")
    return item


def _checked_bound(bound):
    if bound is None:
        return None
    if type(bound) is not int or bound < 1:
        raise ValueError("an encoding-plan bound is a positive integer or None")
    return bound


def plan_encoding(proc, env, bound=None, *, fixed_inputs=(), _successor_sink=None,
                  _procedure_validation=None, _checkpoint=None):
    """Derive carriers, optionally conditioned on concrete scalar inputs.

    The public input domain and boundary widths do not change. A conditioned
    plan is applicable only when the encoder is supplied those exact literals;
    it is not a plan for arbitrary inputs with the same physical widths.
    """
    planned, successor = _derive_encoding_plan(
        proc, env, bound, fixed_inputs=fixed_inputs,
        _procedure_validation=_procedure_validation,
        _checkpoint=_checkpoint)
    if _checkpoint is not None:
        _checkpoint()
    if _successor_sink is not None:
        _successor_sink(successor)
    return planned


def _derive_encoding_plan(proc, env, bound=None, *, fixed_inputs=(),
                          _procedure_validation=None, _checkpoint=None):
    """Return one public plan and its already-computed normal successor.

    The successor is private derivation evidence. It is neither serialized
    into the plan nor included in its identity; position planning consumes it
    only to avoid interpreting the same source graph twice.
    """
    bound = _checked_bound(bound)
    if _checkpoint is not None:
        _checkpoint()
    env = dict(env)
    fixed_inputs = _fixed_input_map(fixed_inputs)
    if _procedure_validation is None:
        try:
            ir.validate_procedure_representation(proc)
        except ir.RepresentationStructureError as exc:
            # Fingerprinting an over-deep or cyclic procedure would itself
            # traverse the representation this gate just refused.  An
            # incomplete plan is never applicable; bind this diagnostic value
            # to the refusal instead.
            refused = _digest(("invalid-procedure/1", exc.code, str(exc)))
            return (EncodingPlan(
                {}, {}, {}, {}, {}, OccurrenceCarriers(), 0, bound,
                (Unresolved("capability", exc.code, str(exc)),),
                env, refused, fixed_inputs), _NO_BOUND)
        _bind_procedure_semantics(proc)
    else:
        from .validation import _checked_procedure_validation

        _checked_procedure_validation(_procedure_validation, proc)
    if proc.semantics is not None and proc.semantics.input_domains:
        # A task-bound plan cannot narrow or widen the input support through a
        # parallel range environment.  The immutable Procedure contract owns
        # that domain; the environment supplies ranges only for unbound legacy
        # procedures and internal/state values.
        _input_environment(proc, env)
    planning_env = _conditioned_environment(proc, env, fixed_inputs)
    array_types = {
        name: shape for name, shape in _procedure_types(proc).items()
        if hasattr(shape, "cells")
    }
    widths, cells, shapes, out = _boundary_layout(proc, env)
    command_numbers = _command_numbers(proc.body)
    (loop_bounds, loop_faults, loop_expansion_exceeded, occurrence_envs,
     normal_successor) = _loop_plan(
        proc, planning_env, command_numbers, _checkpoint=_checkpoint)
    expansion_cost, loop_visits = _contextual_expansion(
        proc, planning_env, loop_bounds, _checkpoint=_checkpoint)
    occurrence_carriers, occurrence_faults = _occurrence_carriers(
        proc, command_numbers, occurrence_envs, planning_env,
        exceeded=loop_expansion_exceeded, _checkpoint=_checkpoint)
    out.extend(loop_faults)
    out.extend(occurrence_faults)
    if expansion_cost > TERM_BUDGET:
        out.append(Unresolved(
            "capability", "encoding-expansion",
            f"the planned loop expansion needs {expansion_cost} command "
            f"copies, over the reported capability of {TERM_BUDGET}"))

    # Subterm ranges were once stored under process-local object identities. No
    # consumer read them, and the key ceased to identify the occurrence after
    # pickling or slicing. Only unresolved diagnostics remain, addressed by
    # stable paths.
    for cmd in ir.walk(proc.body):
        if _checkpoint is not None:
            _checkpoint()
        if loop_expansion_exceeded:
            break
        command_number = command_numbers.get(id(cmd))
        if command_number is None:
            continue
        at = occurrence_envs.get(command_number)
        if at is None:
            # The command is unreachable in this planned domain (in
            # particular, a retained body below a zero-trip loop).  The
            # encoder emits no copy of it and needs no carrier for it.
            continue
        for role, expr in _expression_roots(cmd):
            occurrences = list(_expression_occurrences(
                expr, ExpressionPosition(command_number, role)))
            for key, sub in occurrences:
                if key in occurrence_carriers:
                    continue
                if not isinstance(sub, ir.Op) or _is_array(sub, array_types):
                    continue
                out.append(Unresolved(
                    "range", key,
                    "an operation the encoder takes a width of has no range"))
            for key, sub in occurrences:
                carrier = occurrence_carriers.get(key)
                if carrier is None:
                    continue
                width = ranges.encoding_width(carrier)
                if width > SCALAR_BITS + 1:
                    out.append(Unresolved(
                        "width", key,
                        f"the range needs {width} signed carrier bits, over the "
                        f"scalar encoding capacity of {SCALAR_BITS + 1}"))
        out.extend(_shape_faults(cmd, array_types))

    return (EncodingPlan(
        widths, cells, shapes, loop_bounds, loop_visits, occurrence_carriers,
        expansion_cost, bound, out, env, _procedure_fingerprint(proc), fixed_inputs),
        normal_successor)


def _fixed_input_map(values):
    values = dict(values)
    if any(type(name) is not str or type(value) is not int
           for name, value in values.items()):
        raise TypeError("fixed inputs map scalar input names to integers")
    return _FrozenMap(values)


def _conditioned_environment(proc, env, fixed_inputs):
    """Apply explicit input bindings without weakening the public domain."""
    out = dict(env)
    declared_types = _procedure_types(proc)
    for name, value in fixed_inputs.items():
        rng = env.get(name)
        if (type(name) is not str or name not in proc.inputs
                or type(value) is not int
                or declared_types.get(name) not in (types.INTEGER, types.BOOLEAN)
                or not isinstance(rng, ranges.Range)
                or not rng.lo <= value <= rng.hi):
            raise PlanMismatch(
                f"fixed input {name!r} must be a scalar integer in its declared support")
        out[name] = ranges.Range(value, value)
    return out


@dataclass(frozen=True, slots=True)
class _PositionLayers:
    """Unproved construction output; deliberately has no completeness claim."""

    state_layers: tuple
    input_layers: tuple
    output_layers: tuple
    occurrence_layers: tuple
    steps: tuple
    normal_layers: tuple
    scalar_width: int
    bound: int
    unresolved: tuple
    inputs: Mapping
    procedure_fingerprint: str
    environments: tuple
    proof_positions: tuple
    certified_outputs: Mapping


def _derive_position_layers(proc, env, bound, *, _fail_on_incomplete=False,
                            _validation=None):
    """Propose finite layers without encoding transitions or solving obligations.

    The result is not a PositionEncodingPlan and carries no closure authority.
    The proof owner checks each distinct transition before publishing a usable
    position plan. Exact frontend output certificates remain inputs to the
    abstract construction, not new proofs run by this planner.
    """
    bound = _checked_bound(bound)
    if bound is None:
        raise ValueError("a position-indexed plan needs a positive finite bound")
    if type(_fail_on_incomplete) is not bool:
        raise TypeError("the internal incomplete-plan policy is Boolean")
    # One uninterrupted verifier call validates the public procedure once.
    # Its private, non-serializable evidence is threaded only through the
    # plans and encodings derived below; no public encoder can present it.
    from .validation import _procedure_validation, _checked_procedure_validation

    # Use the same exact persistent-array representation as query templates.
    # Dense guarded writes otherwise duplicate every cell at each loop copy
    # before this preparatory check even reaches its solver.
    procedure_validation = (_procedure_validation(proc, compact_arrays=True)
                            if _validation is None else
                            _checked_procedure_validation(_validation, proc))
    # The frontend's sealed output-boundary authority proves the transaction
    # count and each physical output component independently from interval
    # transfer.  Reuse that proof when present: mutually exclusive returns
    # otherwise look additive after a branch-insensitive join, while modular
    # output arithmetic can make SMT rediscover a fact already established at
    # the source write boundary.
    from .procedure_contract import output_boundary_bounds

    sealed_output_bounds = output_boundary_bounds(proc)
    certified_outputs = {
        name: ranges.Range(*pair)
        for name, pair in (sealed_output_bounds or {}).items()
    }
    certified_count = certified_outputs.get("#count")
    # Output carriers do not feed the following state layer. Reuse a sealed
    # carrier even for state-dependent outputs; narrowing it would introduce
    # a new proof obligation without changing the exact transition relation.
    # State and internal occurrence carriers remain position-specific.
    inputs = _input_environment(proc, env)
    unresolved = _UniqueUnresolved()

    initial = {}
    for name in proc.state:
        rng = _initial_value_range(proc.initial.get(name, _NO_BOUND))
        if rng is None:
            _add_unresolved(unresolved, Unresolved(
                "range", f"state[0].{name}",
                "the exact initial state has no finite integer carrier"))
        else:
            initial[name] = rng

    state_ranges = [initial]
    normal_layers = [True]
    step_envs = []
    after_layers = []
    raw_occurrences = []
    steps = []
    command_numbers = _command_numbers(proc.body)
    required_after = set(proc.state) | set(proc.output_vars.values())
    if proc.count is not None:
        required_after.add(proc.count)
    step_cache = {}
    transition_cache = {}
    for position in range(bound):
        current = state_ranges[position]
        step_env = {**inputs, **current}
        step_envs.append(step_env)
        step_key = _environment_image(step_env)
        cached_step = step_cache.get(step_key)
        if cached_step is None:
            derived_successor = []
            step = plan_encoding(
                proc, step_env, bound=bound,
                _successor_sink=derived_successor.append,
                _procedure_validation=procedure_validation)
            planned_successor = (derived_successor[0]
                                 if derived_successor else _NO_BOUND)
            if _fail_on_incomplete and not step.representation_complete:
                first = next(item for item in step.unresolved
                             if not _materialization_limit(item))
                from .validation import EncodingError

                raise EncodingError(
                    "the position-indexed representation is incomplete at "
                    f"step[{position}].{first.what}: {first.detail}",
                    kind=first.kind)
            assignment_hulls = _assignment_carrier_hulls(
                proc, command_numbers, step, step_env)
            cached_step = step, assignment_hulls, planned_successor
            step_cache[step_key] = cached_step
        step, assignment_hulls, planned_successor = cached_step
        steps.append(step)
        raw_occurrences.append(step.occurrence_carriers)
        for item in step.unresolved:
            if _materialization_limit(item):
                # Retain this estimate on the step, but do not confuse the
                # cost of observing every goal with an invalid finite domain.
                continue
            _add_unresolved(unresolved, Unresolved(
                item.kind, f"step[{position}].{item.what}", item.detail))
        if not normal_layers[position] or set(current) != set(proc.state):
            after = None
        else:
            # The abstract transfer is a pure function of the complete entry
            # environment.  Stateless procedures and state machines at an
            # abstract fixed point therefore have one semantic request even
            # when the finite horizon contains many positions.  Reusing the
            # frozen answer avoids interpreting a large graph once per slot;
            # unlike a cache by position or by a partial state key, this cannot
            # merge distinct layers.
            transition_key = _environment_image(step_env)
            cached = transition_cache.get(transition_key, _NO_BOUND)
            if cached is _NO_BOUND:
                if planned_successor is _NO_BOUND:
                    cached = _normal_position_successor(proc, step_env)
                else:
                    cached = (None if planned_successor is ranges._NO_NORMAL
                              else _FrozenMap(planned_successor))
                transition_cache[transition_key] = cached
            after = None if cached is None else dict(cached)
            if after is not None:
                for name in required_after - after.keys():
                    carrier = assignment_hulls.get(name)
                    if carrier is not None:
                        after[name] = carrier
        after_layers.append(after)
        following = dict(current)
        next_normal = after is not None
        if after is not None:
            following = {}
            for name in proc.state:
                rng = after.get(name)
                if rng is None:
                    _add_unresolved(unresolved, Unresolved(
                        "range", f"state[{position + 1}].{name}",
                        "a normally completing step has no finite next-state "
                        "carrier"))
                else:
                    # A public state layer constrains every array cell by one
                    # scalar carrier. Intra-step cell facts are not encoded at
                    # that boundary, so they cannot narrow the next transfer.
                    following[name] = ranges.Range(rng.lo, rng.hi)
        state_ranges.append(following)
        normal_layers.append(next_normal)

    output_ranges = []
    for position, after in enumerate(after_layers):
        outputs = {}
        if after is not None:
            for port, variable in proc.output_vars.items():
                proposed = after.get(variable)
                certified = certified_outputs.get(port)
                rng = _planned_output_range(proposed, certified)
                if rng is _NO_BOUND:
                    _add_unresolved(unresolved, Unresolved(
                        "range", f"output[{position}].{port}",
                        "the abstract output carrier is disjoint from its "
                        "sealed frontend certificate"))
                    continue
                if rng is None:
                    _add_unresolved(unresolved, Unresolved(
                        "range", f"output[{position}].{port}",
                        "a normally completing step has no finite output carrier"))
                else:
                    outputs[port] = rng
            proposed_count = (ranges.Range(1, 1) if proc.count is None else
                              after.get(proc.count))
            count_rng = _planned_output_range(proposed_count, certified_count)
            if count_rng is _NO_BOUND:
                _add_unresolved(unresolved, Unresolved(
                    "range", f"output[{position}].#count",
                    "the abstract output count is disjoint from its sealed "
                    "frontend certificate"))
                output_ranges.append(outputs)
                continue
            if count_rng is None:
                _add_unresolved(unresolved, Unresolved(
                    "range", f"output[{position}].#count",
                    "the output batch count has no finite carrier"))
            else:
                # The command encoder initializes a batch to the empty prefix
                # and conditionally commits its returned count.  Zero is thus a
                # real boundary representation value even for a source method
                # whose every normal return emits one transaction.  Including
                # it changes no transition constraint; it merely prevents the
                # finite carrier from excluding the encoder's inactive value.
                outputs["#count"] = count_rng.join(ranges.Range(0, 0))
        output_ranges.append(outputs)

    scalar_width = _position_scalar_width(
        state_ranges, inputs, output_ranges, raw_occurrences, steps,
        intrinsic_width=_intrinsic_scalar_width(proc))
    state_layers = tuple(
        _carrier_map(layer, width=scalar_width) for layer in state_ranges)
    input_layer = _carrier_map(inputs, width=scalar_width)
    input_layers = tuple(input_layer for _ in range(bound))
    output_layers = tuple(
        _carrier_map(layer, width=scalar_width) for layer in output_ranges)
    occurrence_layers = _intern_occurrence_layers(
        raw_occurrences, scalar_width)

    return _PositionLayers(
        state_layers, input_layers, output_layers, occurrence_layers,
        tuple(steps), tuple(normal_layers), scalar_width, bound,
        tuple(unresolved), _FrozenMap(inputs),
        _position_procedure_fingerprint(proc),
        tuple(_FrozenMap(env) for env in step_envs),
        tuple(position for position, step in enumerate(steps)
              if step.representation_complete
              and set(state_ranges[position]) == set(proc.state)
              and after_layers[position] is not None),
        _FrozenMap(certified_outputs))


def _planned_output_range(proposed, certified):
    """Combine the abstract layer with an independent output certificate."""

    if proposed is None:
        return certified
    if certified is None:
        return proposed
    if proposed.hi < certified.lo or certified.hi < proposed.lo:
        return _NO_BOUND
    return certified


def _normal_position_successor(proc, env):
    """Frozen abstract successor of one inhabited normal position."""
    env = ranges._typed_entry_ranges(proc, env)
    graph = proc.graph
    got = (ranges.graph_post(graph, dict(env))
           if graph is not None else ranges._post(proc.body, dict(env)))
    return None if got is ranges._NO_NORMAL else _FrozenMap(got)


def _assignment_carrier_hulls(proc, numbers, step, entry):
    """Conservative value hulls from the step's proved occurrences.

    A source CFG can prove that an output is assigned on every normal path
    while an interval join forgets the value because different paths use
    different local names.  The occurrence plan has already assigned every
    encoded right-hand side a finite carrier.  Joining those carriers by
    destination therefore supplies a safe boundary carrier; it neither makes
    an absent path normal nor changes the transition relation.
    """
    out = dict(entry)
    for command in ir.walk(proc.body):
        if not isinstance(command, (ir.Assign, ir.Store)):
            continue
        number = numbers.get(id(command))
        if number is None:
            continue
        carrier = step.occurrence_carriers.get(
            ExpressionPosition(number, "rhs"))
        if carrier is None:
            continue
        value = carrier.range
        previous = out.get(command.var)
        out[command.var] = (
            value if previous is None else previous.join(value))
    return out


def _intrinsic_scalar_width(proc):
    """Include fixed-width primitive results, not only their value hulls.

    ``swrap(x, 32)`` can have a 16-bit value hull while its exact encoder
    still returns 32 bits. Query the shared primitive registry on the full
    operand domain so every layer uses a width that also holds that result.
    Neither a guessed machine word size nor a list of primitive names owns
    this boundary.
    """
    width = 1
    for expression in ir.expression_nodes(proc.body):
        if not isinstance(expression, ir.Op):
            continue
        specification = primitives.primitive(expression.op)
        terms = specification.signature.terms_for(len(expression.args))
        if terms is None or primitives.WIDTH not in terms:
            continue
        parts = [
            ranges.Bounds(argument.value, argument.value)
            if term is primitives.WIDTH and isinstance(argument, ir.Lit)
            else ranges.Bounds(0, None) if term is primitives.SHIFT else None
            for term, argument in zip(terms, expression.args)
        ]
        intrinsic = ranges._bounds_of_op(expression, parts)
        if intrinsic is not None and intrinsic.finite() is not None:
            width = max(width, ranges.encoding_width(intrinsic.finite()))
    return width


def _position_scalar_width(state_layers, inputs, output_layers,
                           occurrence_layers, steps, *, intrinsic_width=1):
    """One signed implementation width shared by current scalar encodings.

    SolverIR distinguishes Boolean and integer semantic types.  The current
    position backend still uses one horizon-wide bit-vector width for both;
    this function is that explicit implementation limitation, not a claim that
    they are one type.  Arrays contribute their scalar leaf carrier.
    """
    largest = intrinsic_width

    def visit_layers(layers, width_of):
        nonlocal largest
        seen = set()
        for layer in layers:
            identity = id(layer)
            if identity in seen:
                continue
            seen.add(identity)
            largest = max(
                largest,
                max((width_of(value) for value in layer.values()), default=1),
            )

    visit_layers(state_layers, ranges.encoding_width)
    largest = max(
        largest,
        max((ranges.encoding_width(rng) for rng in inputs.values()), default=1),
    )
    visit_layers(output_layers, ranges.encoding_width)
    visit_layers(occurrence_layers, lambda carrier: carrier.width)
    seen_steps = set()
    for step in steps:
        identity = id(step)
        if identity in seen_steps:
            continue
        seen_steps.add(identity)
        largest = max(largest, max(step.widths.values(), default=1))
    return largest


def _input_environment(proc, env):
    env = dict(env)
    semantics = proc.semantics
    if semantics is not None:
        from .procedure_contract import ProcedureContractError
        from .validation import bind_procedure_contract

        try:
            semantics = bind_procedure_contract(proc)
        except ProcedureContractError as exc:
            raise PlanMismatch(
                f"the procedure semantic contract is inconsistent: {exc}") \
                from None
    domains = (dict(semantics.solver_input_bounds())
               if semantics is not None else {})
    out = {}
    for name in proc.inputs:
        rng = env.get(name)
        if not isinstance(rng, ranges.Range):
            raise PlanMismatch(f"input {name} has no finite declared support")
        domain = domains.get(name)
        if domain is not None and (rng.lo, rng.hi) != domain:
            raise PlanMismatch(
                f"the plan was presented with another input support: input "
                f"{name} support does not equal its finite procedure domain "
                f"{domain}")
        out[name] = rng
    return out


def _initial_value_range(value):
    """Return the exact scalar carrier hull of one represented value.

    Boolean state is semantically distinct from integer state, but the current
    position backend represents it by the finite carrier ``0..1``.  Preserve
    that distinction in the procedure type while admitting its exact carrier
    here; rejecting ``bool`` made a declared record's Boolean leaf impossible
    to place in the initial layer.
    """
    if value is _NO_BOUND:
        return None
    if type(value) is bool:
        bit = int(value)
        return ranges.Range(bit, bit)
    if type(value) is int:
        return ranges.Range(value, value)
    from .uniform_array import UniformArray
    if type(value) is UniformArray:
        return ranges.range_of_value(value)
    if type(value) is list:
        leaves = []
        stack = list(value)
        while stack:
            item = stack.pop()
            if type(item) is list:
                stack.extend(item)
            elif type(item) is bool:
                leaves.append(int(item))
            elif type(item) is int:
                leaves.append(item)
            else:
                return None
        # A fixed empty array is the unit of the product representation: it
        # has a complete shape and no scalar leaf requiring a carrier.
        return (ranges.Range(min(leaves), max(leaves)) if leaves
                else ranges._CellRange(()))
    return None


class _AssignmentRangeResolver:
    """Finite value hulls for branch-local names lost at a later join.

    The ordinary occurrence environment contains only names bounded on every
    path into a block.  A repeated guard can nevertheless make a one-sided
    local safe to read.  Once the sealed CFG has proved definite assignment,
    joining the ranges of all definitions gives a conservative carrier for
    such a read without inventing a value on the paths where it is absent.
    """

    def __init__(self, proc, graph, numbers, occurrence_envs, entry):
        graph_definitions = {}
        seen_graphs = set()

        def collect_graph(graph):
            if graph is None or id(graph) in seen_graphs:
                return
            seen_graphs.add(id(graph))
            for label in graph.order():
                for command in graph.blocks[label].instrs:
                    if isinstance(command, ir.Assign):
                        graph_definitions.setdefault(command.var, []).append(
                            command)
                    if isinstance(command, ir.For) and command.flow is not None:
                        collect_graph(command.flow.graph)

        collect_graph(graph)
        term_definitions = {}
        for command in ir.walk(proc.body):
            if isinstance(command, ir.Assign):
                term_definitions.setdefault(command.var, []).append(command)
        self.definitions = {}
        for name in set(graph_definitions) | set(term_definitions):
            commands = graph_definitions.get(name, term_definitions.get(name, ()))
            rows = []
            for command in commands:
                number = numbers.get(id(command))
                at = None if number is None else occurrence_envs.get(number)
                if at is not None:
                    rows.append((number, command.expr, at))
            if rows:
                self.definitions[name] = tuple(sorted(
                    rows, key=lambda row: row[0]))
        self.entry = dict(entry)
        self.variable_types = _procedure_types(proc)
        self.memo = {}
        self.active = set()

    def range_of(self, name, before):
        key = (name, before)
        if key in self.memo:
            return self.memo[key]
        if key in self.active:
            return None
        definitions = self.definitions.get(name, ())
        result = self.entry.get(name)
        if result is None and self.variable_types.get(name) == types.BOOLEAN:
            result = ranges.Range(0, 1)
        self.active.add(key)
        try:
            for number, expression, entering in definitions:
                if number >= before:
                    break
                environment = dict(entering)
                for dependency in ranges._expression_names(expression):
                    if dependency in environment:
                        continue
                    resolved = self.range_of(dependency, number)
                    if resolved is None:
                        self.memo[key] = None
                        return None
                    environment[dependency] = resolved
                found = ranges._value_range(expression, environment)
                if found is None:
                    self.memo[key] = None
                    return None
                result = found if result is None else result.join(found)
        finally:
            self.active.remove(key)
        self.memo[key] = result
        return result

    def supplement(self, expression, environment, before):
        supplemented = dict(environment)
        for name in ranges._expression_names(expression):
            if name in supplemented:
                continue
            found = self.range_of(name, before)
            if found is not None:
                supplemented[name] = found
        return supplemented


def _occurrence_carriers(
        proc, numbers, occurrence_envs, entry, *, exceeded, _checkpoint=None):
    """Every expression leaf carrier, using the encoder's command numbering."""
    if exceeded:
        return OccurrenceCarriers(), ()
    out = {}
    faults = []
    array_types = {
        name: kind for name, kind in _procedure_types(proc).items()
        if hasattr(kind, "cells")
    }
    variable_types = _procedure_types(proc)
    from . import ssa

    start = set(proc.state) | set(proc.inputs)
    graph = proc.graph
    assignment_ranges = (
        _AssignmentRangeResolver(proc, graph, numbers, occurrence_envs, entry)
        if graph is not None and not ssa.undefined_uses(graph, start)
        else None
    )
    for cmd in ir.walk(proc.body):
        if _checkpoint is not None:
            _checkpoint()
        command_number = numbers.get(id(cmd))
        if command_number is None:
            continue
        at = occurrence_envs.get(command_number)
        if at is None:
            continue
        for role, expr in _expression_roots(cmd):
            # One postorder table covers the complete expression DAG.  Calling
            # ``of_expression`` for every nested occurrence walks each suffix
            # again and makes generated expressions quadratic in their size.
            carrier_env = (
                at if assignment_ranges is None else
                assignment_ranges.supplement(expr, at, command_number)
            )
            range_table = ranges._ranges_of_tree(
                expr, carrier_env, carrier_env)
            for key, subterm in _expression_occurrences(
                    expr, ExpressionPosition(command_number, role)):
                rng = range_table.get(id(subterm))
                if rng is None and isinstance(subterm, ir.Var) \
                        and variable_types.get(subterm.name) == types.BOOLEAN:
                    # A Boolean's semantic type is already its complete finite
                    # carrier.  A control-flow join may forget a more precise
                    # singleton interval for a compiler temporary, but it
                    # cannot make that value cease to be Boolean.  Do not
                    # extend this fallback to integers: their type alone has
                    # no finite mathematical range.
                    rng = ranges.Range(0, 1)
                if rng is None and _is_array(subterm, array_types):
                    # The scalar range walker deliberately does not assign one
                    # interval to an aggregate expression.  The encoder still
                    # needs the aggregate's component hull when a whole-array
                    # assignment updates its abstract environment, so derive
                    # that product from the same per-cell facts here.
                    rng = ranges._value_range(subterm, carrier_env)
                if rng is None:
                    capability = None
                    if isinstance(subterm, ir.Op):
                        diagnostic = primitives.primitive(
                            subterm.op).range_capability_error
                        if diagnostic is not None:
                            parts = tuple(
                                range_table.get(id(argument))
                                for argument in subterm.args)
                            capability = diagnostic(*(
                                None if part is None else
                                primitives.PrimitiveInterval(part.lo, part.hi)
                                for part in parts))
                    faults.append(Unresolved(
                        "capability" if capability is not None else "range",
                        key,
                        capability or
                        "an expression occurrence has no finite leaf carrier"))
                    continue
                out[key] = FiniteCarrier.of(rng)
    return OccurrenceCarriers(out), tuple(faults)


def _add_unresolved(items, item):
    items.append(item)


def _merge_occurrence(into, number, at, names=None):
    """Join one command-entry fact into a private mutable accumulator."""

    if names is not None:
        at = {name: at[name] for name in names if name in at}
    previous = into.get(number)
    if previous is None:
        into[number] = dict(at)
        return
    for name in tuple(previous):
        if name not in at:
            del previous[name]
            continue
        old = previous[name]
        other = at[name]
        if old is not other:
            previous[name] = old.join(other)


def _loop_plan(proc, env, command_numbers, *, _checkpoint=None):
    """Finite loop maxima and each command's entry environment."""
    # Repeating the entry hull in the declared shape is the same boundary
    # domain, but permits exact-index writes to replace only their own cell.
    # Derivation and public-plan verification share this transfer boundary.
    env = ranges._typed_entry_ranges(proc, env)
    graph = proc.graph
    flow_validations = {}
    loop_posts = {}
    syntax = {}

    def transfer_cache():
        return ranges._range_transfer_cache(
            flow_validations, loop_posts=loop_posts, syntax=syntax)

    loop_footprints = {}
    taken_decisions = {}
    loop_body_commands = set()
    occurrence_names = {}
    graph_results = {}
    for command in ir.walk(proc.body):
        if _checkpoint is not None:
            _checkpoint()
        number = command_numbers.get(id(command))
        if number is not None:
            names = set()
            for _role, expression in _expression_roots(command):
                names.update(ranges._expression_names(expression))
            # A store reads the previous aggregate even though its target is
            # syntactically an lvalue. A retained loop's sealed CFG declares
            # every additional live-in needed to refine that local graph.
            if isinstance(command, ir.Store):
                names.add(command.var)
            if isinstance(command, ir.For) and command.flow is not None:
                names.update(command.flow.required)
            occurrence_names[number] = frozenset(names)
        if isinstance(command, ir.For):
            loop_body_commands.update(
                command_numbers[id(nested)]
                for nested in ir.walk(command.body)
                if not isinstance(nested, ir.Seq))
        if not isinstance(command, ir.Assign) \
                or not command.var.startswith("#taken:"):
            continue
        # Conditional decisions have an initial false clear. The nonliteral
        # write evaluates the block's source guard; unconditional decisions
        # need only that write.
        if isinstance(command.expr, ir.Lit) and command.expr.value is False:
            continue
        label = command.var.removeprefix("#taken:")
        taken_decisions[label] = command_numbers[id(command)]

    def merge(into, number, at):
        _merge_occurrence(
            into, number, at, occurrence_names.get(number, ()))

    def graph_entries(graph, seed):
        # A retained loop can revisit the same source CFG with exactly the
        # same abstract entry, including every array cell. Its entries and
        # successor are identical; collect() still merges them at every visit.
        # Keep complete results only, within this one immutable planning call.
        key = id(graph), tuple(sorted(seed.items()))
        if key in graph_results:
            return graph_results[key]
        expected = {
            command_numbers[id(instruction)]
            for label in graph.order()
            for instruction in graph.blocks[label].instrs
            if id(instruction) in command_numbers
        }
        expected.update(
            number
            for label in graph.order()
            if isinstance(graph.blocks[label].term, cfg.Branch)
            if (number := taken_decisions.get(label)) is not None
        )

        exact = {}
        unreachable = {}

        def observe_graph(into):
            def observe(cmd, at):
                if _checkpoint is not None:
                    _checkpoint()
                number = command_numbers.get(id(cmd))
                if number is not None:
                    merge(into, number, at)
            return ranges._DirectCommandWitness(observe)

        def observe_terminator(into):
            def observe(label, terminator, at):
                if not isinstance(terminator, cfg.Branch):
                    return
                number = taken_decisions.get(label)
                if number is not None:
                    merge(into, number, at)
            return observe

        normal_successor = ranges.graph_post(
            graph, dict(seed),
            observe_graph(exact), observe_terminator(exact),
            _flows=transfer_cache(),
            _unreachable_witness=observe_graph(unreachable),
            _unreachable_terminator_witness=observe_terminator(unreachable))
        # Reachable occurrences keep the exact predecessor environment.  A
        # wholly unreachable occurrence has no semantic value in this layer,
        # but if-conversion still materialises its expression and the encoder
        # must assign that expression a finite representation.  Supplying the
        # syntactic predecessor only in that case cannot enlarge the semantic
        # postcondition or turn an unknown reachable value into a guessed one.
        if not expected <= exact.keys():
            for number, at in unreachable.items():
                exact.setdefault(number, at)
        result = exact, normal_successor
        graph_results[key] = result
        return result

    def collect(refinements=None):
        """Walk the retained term, optionally restoring exact CFG entries.

        If-conversion can lose a local range after a source join even though
        the CFG has the exact predecessor environment for the next command.
        ``refinements`` maps those shared command objects to CFG environments.
        Updating the term environment at that command preserves the fact for
        following compiler-generated expressions and nested retained loops;
        merely replacing the reported source-command environment is too late.
        """
        maxima, faults, occurrence_envs = {}, [], {}
        loop_entries = {}
        exceeded = False

        def observe(cmd, at):
            if _checkpoint is not None:
                _checkpoint()
            if isinstance(cmd, ir.Seq):
                return
            number = command_numbers[id(cmd)]
            if refinements is not None and number in refinements:
                # The graph environment is the exact abstract predecessor for
                # this source command.  Synthetic term variables are retained;
                # graph-known values supersede their weaker term counterparts.
                # Local CFGs below are seeded only with loop-invariant facts
                # and the all-visit bindings established by loop mechanics.
                # Their bounds therefore hold on every visit, including after
                # early-return guards. Loop-carried values without such a
                # bound keep the term walk's actual accumulated interval.
                at.update(refinements[number])
            merge(occurrence_envs, number, at)
            if not isinstance(cmd, ir.For):
                return
            occurrence = ExpressionPosition(number, "trip_count")
            got, _flow = ranges._loop_trip_range(
                cmd, at, transfer_cache())
            if got is None:
                fault = Unresolved(
                    "range", occurrence,
                    "the loop trip count has no proved finite maximum")
                if fault not in faults:
                    faults.append(fault)
                return
            key = str(occurrence)
            maximum = max(0, int(got.hi))
            maxima[key] = max(maxima.get(key, 0), maximum)

        def source_graph(graph, seed):
            # These are the actual abstract entry values for this loop visit,
            # including carried values. Join source predecessors across visits
            # separately from the weaker if-converted term environments.
            entries, successor = graph_entries(graph, seed)
            for number, at in entries.items():
                merge(loop_entries, number, at)
            return successor

        observe.source_graph = source_graph
        ranges.walk_post(
            proc.body, dict(env), observe,
            _flows=transfer_cache())
        occurrence_envs.update(loop_entries)
        return maxima, faults, exceeded, occurrence_envs

    def loops_by_depth(root):
        """Retained loops grouped by their lexical nesting depth."""
        grouped = {}
        stack = [(root, 0)]
        while stack:
            command, depth = stack.pop()
            if isinstance(command, ir.Seq):
                stack.append((command.second, depth))
                stack.append((command.first, depth))
            elif isinstance(command, ir.If):
                stack.append((command.orelse, depth))
                stack.append((command.then, depth))
            elif isinstance(command, ir.For):
                grouped.setdefault(depth, []).append(
                    (command_numbers[id(command)], command))
                stack.append((command.body, depth + 1))
        return grouped

    graph_read_cache = {}

    def graph_reads(graph):
        """Names whose entry values can affect one sealed local graph."""
        cached = graph_read_cache.get(id(graph))
        if cached is not None and cached[0] is graph:
            return cached[1]
        names = set()
        for label in graph.order():
            block = graph.blocks[label]
            for instruction in block.instrs:
                for _role, expression in _expression_roots(instruction):
                    names.update(ranges._expression_names(expression))
                if isinstance(instruction, ir.Store):
                    names.add(instruction.var)
            if isinstance(block.term, cfg.Branch):
                names.update(ranges._expression_names(block.term.guard))
        result = frozenset(names)
        graph_read_cache[id(graph)] = graph, result
        return result

    loop_levels = loops_by_depth(proc.body)

    def mechanical_entry_range(loop, flow, name, trip):
        """Range of a source target copied directly from the loop counter.

        ``entry_defined`` certifies that loop mechanics establish the name at
        every local-CFG entry. Recognize the common direct binding only when it
        is the sole write outside that sealed graph; all richer iterable
        bindings retain the term-seed fallback below.
        """
        graph_commands = {
            id(instruction)
            for label in flow.graph.order()
            for instruction in flow.graph.blocks[label].instrs
        }
        writes = [
            command for command in ir.walk(loop.body)
            if id(command) not in graph_commands
            and isinstance(command, (ir.Assign, ir.Store))
            and command.var == name
        ]
        if len(writes) != 1 or not isinstance(writes[0], ir.Assign):
            return None
        expression = writes[0].expr
        if not isinstance(expression, ir.Var) \
                or expression.name != loop.index:
            return None
        return ranges.Range(0, trip.hi - 1)

    def refine_local_graphs(outer, fallback):
        """Add exact retained-loop entries, reporting if a term seed is needed."""
        refinements = dict(outer)
        changed = False
        needs_term_seed = False
        for depth in sorted(loop_levels):
            for number, loop in loop_levels[depth]:
                flow = loop.flow
                occurrence = refinements.get(number, fallback.get(number))
                if flow is None or occurrence is None:
                    continue
                trip, _checked = ranges._loop_trip_range(
                    loop, occurrence,
                    transfer_cache())
                if trip is None or trip.hi <= 0:
                    continue
                # The entry environment describes the first iteration, not
                # every iteration. Forget all values this body can overwrite
                # before deriving an all-visit source fact. Fresh assignments
                # and source guards recover local bounds without imposing a
                # first-iteration bound on loop-carried state. The finite
                # bindings supplied by loop mechanics are restored below.
                _reads, written = ranges._range_footprint(loop, loop_footprints)
                seed = {name: value for name, value in occurrence.items()
                        if name not in written}
                read_names = graph_reads(flow.graph)
                for name in flow.entry_defined:
                    values = []
                    for label in flow.graph.order():
                        for instruction in flow.graph.blocks[label].instrs:
                            command = command_numbers.get(id(instruction))
                            entry = refinements.get(
                                command, fallback.get(command))
                            if entry is not None and name in entry:
                                values.append(entry[name])
                    if not values:
                        if name == loop.index:
                            seed[name] = ranges.Range(0, trip.hi - 1)
                        elif (mechanical := mechanical_entry_range(
                                loop, flow, name, trip)) is not None:
                            seed[name] = mechanical
                        elif name in read_names:
                            # The retained binding prefix, rather than an outer
                            # CFG edge, establishes this source target. One term
                            # pass is required to recover its finite carrier.
                            needs_term_seed = True
                        continue
                    value = values[0]
                    for other in values[1:]:
                        value = value.join(other)
                    seed[name] = value
                if needs_term_seed and not fallback:
                    continue
                local, _ = graph_entries(flow.graph, seed)
                for command, entry in local.items():
                    if refinements.get(command) != entry:
                        refinements[command] = entry
                        changed = True
        return refinements, changed, needs_term_seed

    if graph is None:
        graph_occurrences, normal_successor = {}, _NO_BOUND
        maxima, faults, exceeded, occurrence_envs = collect()
    else:
        graph_occurrences, normal_successor = graph_entries(graph, env)
        refinements, _changed, needs_term_seed = refine_local_graphs(
            graph_occurrences, {})
        if needs_term_seed:
            # Only a source target established by the retained binding prefix
            # requires this preliminary term pass. Ordinary counted loops can
            # refine all local CFGs directly from their sealed entry facts.
            preliminary = collect(graph_occurrences)
            maxima, faults, exceeded, occurrence_envs = preliminary
            if not exceeded:
                refinements, changed, _ = refine_local_graphs(
                    graph_occurrences, occurrence_envs)
                if changed:
                    maxima, faults, exceeded, occurrence_envs = collect(
                        refinements)
        else:
            maxima, faults, exceeded, occurrence_envs = collect(refinements)

        # The converted term owns synthetic reached/taken commands and loop
        # bodies. The source CFG owns outer source-command predecessors; keep
        # those exact facts instead of joining them with predication loss.
        occurrence_envs.update(
            (number, at)
            for number, at in refinements.items()
            if number not in loop_body_commands)
    return maxima, faults, exceeded, occurrence_envs, normal_successor


def _contextual_expansion(proc, env, loop_bounds, *, _checkpoint=None):
    """Count semantic visits and retain exact outer-loop correlations.

    Occurrence carriers deliberately join all visits to one source position.
    Formula allocation cannot multiply those joined maxima: values from
    different outer iterations are mutually exclusive.  This walk keeps the
    pointwise contexts used by both the resource preflight and the encoder.
    """
    env = ranges._typed_entry_ranges(proc, env)
    flow_validations = {}

    class ExpansionExceeded(Exception):
        pass

    command_numbers = _command_numbers(proc.body)
    class Observer:
        requires_complete_loop_visits = True
        requires_pointwise_loop_indices = True

        def __init__(self):
            self.context = []
            self.expanded = 0
            self.loop_bounds = dict(loop_bounds)
            self.visits = {}

        def __call__(self, command, at):
            if _checkpoint is not None:
                _checkpoint()
            if isinstance(command, (ir.Seq, ir.Skip)):
                return
            self.expanded += 1
            if self.expanded > TERM_BUDGET:
                self.expanded = TERM_BUDGET + 1
                raise ExpansionExceeded
            if not isinstance(command, ir.For):
                return
            number = command_numbers.get(id(command))
            trip, _flow = ranges._loop_trip_range(
                command, at,
                ranges._range_transfer_cache(flow_validations))
            maximum = None if trip is None else max(0, trip.hi)
            if maximum is not None:
                maximum = self.certified_loop_maximum(command, maximum)
            table = self.visits.setdefault(number, {})
            context = tuple(self.context)
            if context not in table:
                table[context] = maximum
            elif table[context] is None or maximum is None:
                table[context] = None
            else:
                table[context] = max(table[context], maximum)

        def enter_loop_iteration(self, loop, turn):
            number = command_numbers.get(id(loop))
            if number is None:
                raise ValueError("a contextual loop has no stable command key")
            self.context.append((number, turn))

        def certified_loop_maximum(self, loop, inferred):
            """Cap a weak term range by the CFG-backed occurrence envelope."""
            number = command_numbers.get(id(loop))
            if number is None:
                raise ValueError("a contextual loop has no stable command key")
            occurrence = str(ExpressionPosition(number, "trip_count"))
            maximum = self.loop_bounds.get(occurrence)
            return inferred if maximum is None else min(inferred, maximum)

        def leave_loop_iteration(self, loop, turn):
            number = command_numbers.get(id(loop))
            expected = (number, turn)
            if not self.context or self.context[-1] != expected:
                raise ValueError("the contextual loop stack is unbalanced")
            self.context.pop()

    observe = Observer()
    try:
        ranges.walk_post(
            proc.body, dict(env), observe,
            _flows=ranges._range_transfer_cache(flow_validations))
    except ExpansionExceeded:
        pass
    return observe.expanded, {
        number: tuple(sorted(table.items()))
        for number, table in observe.visits.items()
    }


def _command_numbers(root):
    """Canonical preorder numbers, including normalized sequence nodes.

    ``Seq`` association is an implementation detail and is normalized while a
    procedure crosses the worker pickle boundary.  Number semantic commands as
    though every sequence were right-associated, while still charging the
    virtual sequence nodes that the full IR walk contains.  This preserves the
    full-command numbering convention without making occurrence identities
    depend on the in-memory association of a sequence tree.
    """
    numbers = {}
    next_number = 0
    stack = [("command", root)]
    while stack:
        kind, value = stack.pop()
        if kind == "sequence":
            parts, offset = value
            next_number += 1  # the canonical Seq node itself
            if offset + 2 < len(parts):
                stack.append(("sequence", (parts, offset + 1)))
            else:
                stack.append(("command", parts[offset + 1]))
            stack.append(("command", parts[offset]))
            continue
        command = value
        if isinstance(command, ir.Seq):
            parts = []
            pending = [command]
            while pending:
                part = pending.pop()
                if isinstance(part, ir.Seq):
                    pending.append(part.second)
                    pending.append(part.first)
                else:
                    parts.append(part)
            # Keep one flattened sequence and advance its cursor. Copying the
            # remaining suffix at every virtual Seq made numbering quadratic.
            stack.append(("sequence", (parts, 0)))
            continue
        numbers[id(command)] = next_number
        next_number += 1
        if isinstance(command, ir.If):
            stack.append(("command", command.orelse))
            stack.append(("command", command.then))
        elif isinstance(command, ir.For):
            stack.append(("command", command.body))
    return numbers


def _boundary_layout(proc, env):
    """The plan fields that ``encode_step`` actually consumes."""
    env = dict(env)
    boundary = tuple(proc.state) + tuple(proc.inputs)
    semantic_types = _procedure_types(proc)
    array_types = {
        name: semantic_types[name] for name in boundary
        if name in semantic_types and hasattr(semantic_types[name], "cells")
    }
    shapes = {name: _type_shape(shape) for name, shape in array_types.items()}
    widths, cells, unresolved = {}, {}, []
    for name in boundary:
        rng = env.get(name)
        if rng is None:
            unresolved.append(Unresolved(
                "range", name,
                "a state variable or input has no finite interval"))
            continue
        width = ranges.encoding_width(rng)
        if width > SCALAR_BITS + 1:
            unresolved.append(Unresolved(
                "width", name,
                f"the range needs {width} signed carrier bits, over the "
                f"scalar encoding capacity of {SCALAR_BITS + 1}"))
            continue
        widths[name] = width
        if name in array_types:
            cells[name] = array_types[name].cells
    return widths, cells, shapes, unresolved


def _type_shape(kind):
    if not hasattr(kind, "cells"):
        return ()
    return (kind.cells,) + _type_shape(kind.elem)


def _expression_roots(cmd):
    if isinstance(cmd, ir.Assign):
        return (("rhs", cmd.expr),)
    if isinstance(cmd, ir.Store):
        return (("index", cmd.index), ("rhs", cmd.expr))
    if isinstance(cmd, ir.If):
        return (("guard", cmd.guard),)
    if isinstance(cmd, ir.For):
        return (("trip_count", cmd.trip_count),)
    return ()


def _expression_occurrences(root, key):
    stack = [(key, root)]
    while stack:
        current_key, expr = stack.pop()
        yield current_key, expr
        children = tuple(ranges._children(expr))
        for index in range(len(children) - 1, -1, -1):
            stack.append((ExpressionPosition(
                current_key.command,
                current_key.role,
                current_key.path + (index,)), children[index]))


def _is_array(expr, arrays):
    if isinstance(expr, ir.Var):
        return expr.name in arrays
    return isinstance(expr, ir.Op) and expr.op in (
        "arraylit", "arrayfill", "store")


def _shape_faults(cmd, arrays):
    if isinstance(cmd, ir.Store) and cmd.var not in arrays:
        return [Unresolved(
            "shape", cmd.var,
            "a store target has no declared recursive array shape")]
    return []


def _environment_image(env):
    """Collision-free canonical image of one complete range environment."""
    pairs = []
    for name, rng in sorted(dict(env).items()):
        if not isinstance(rng, ranges.Range):
            raise TypeError(f"the range of {name} is not a finite Range")
        pairs.append((str(name), _range_image(rng)))
    return tuple(pairs)


def _environment_fingerprint(env):
    return _digest(("range-environment/3", _environment_image(env)))


def _range_image(value):
    """A canonical image that preserves private per-cell range precision."""
    if type(value) is ranges._CellRange:
        return ("cells", tuple(_range_image(cell) for cell in value.cells))
    if type(value) is ranges._UniformCellRange:
        return (
            "uniform-cell-runs",
            value.length,
            _range_image(value.default),
            tuple((start, stop, _range_image(cell))
                  for start, stop, cell in value.runs),
        )
    if isinstance(value, ranges.Range):
        return ("interval", int(value.lo), int(value.hi))
    raise TypeError(f"{type(value).__name__} is not a finite range")


def _procedure_types(proc):
    """The procedure-owned variable contexts, with a legacy fallback.

    Lowered procedures bind :class:`ProcedureContract` before planning.  Its
    contexts are the authority consumed by the interpreter and encoder, so the
    plan must not independently rediscover boundary shapes from ``proc.types``.
    Hand-built legacy procedures without a bound contract retain the old map
    only until the backend binds them.
    """
    semantics = proc.semantics
    if semantics is None:
        return dict(proc.types)
    out = {
        name: kind
        for context in (
            semantics.state_types,
            semantics.input_types,
            semantics.local_types,
        )
        for name, kind in context
    }
    # Public output types describe one active transaction.  The executable IR
    # writes those values into representation-owned batch buffers, whose outer
    # capacity dimension is intentionally absent from ``output_types``.  Keep
    # that physical layout from the procedure's checked inference map; it is
    # not a fifth semantic namespace.
    for variable in proc.output_vars.values():
        if variable in proc.types:
            out[variable] = proc.types[variable]
    return out


def _bind_procedure_semantics(proc):
    """Validate the same Procedure contract before any plan identity is read."""
    from .procedure_contract import ProcedureContractError
    from .validation import bind_procedure_contract

    try:
        return bind_procedure_contract(proc)
    except ProcedureContractError as exc:
        raise PlanMismatch(
            f"the procedure semantic contract is inconsistent: {exc}") from None


def _semantic_contract_image(proc):
    """Canonical plan identity image of the procedure semantic authority."""
    semantics = proc.semantics
    if semantics is None:
        return None

    def context(values):
        return tuple((name, _type_image(kind)) for name, kind in values)

    def domains(values):
        return tuple((name, value_type.identity)
                     for name, value_type in values)

    return (
        "procedure-contract/2",
        context(semantics.state_types),
        context(semantics.input_types),
        context(semantics.local_types),
        context(semantics.output_types),
        domains(semantics.input_domains),
        domains(semantics.output_domains),
        tuple(semantics.artifact_errors),
        tuple(semantics.analysis_errors),
        tuple((role, binding.identity)
              for role, binding in semantics.value_bindings),
    )


def _graph_fingerprint(proc):
    graph = proc.graph
    return None if graph is None else cfg.graph_fingerprint(graph)


def _procedure_fingerprint(proc):
    """Identity of everything that can change the encoded step formula."""
    image = (
        "behavior-procedure/5",
        _value_image([(framing.as_dict(), projection) for framing, projection in proc.input_framing]),
        tuple(proc.state),
        tuple(proc.inputs),
        tuple(proc.locals),
        tuple(proc.outputs),
        _command_image(proc.body),
        tuple(sorted((name, _type_image(kind))
                     for name, kind in proc.types.items())),
        _value_image(proc.count),
        _value_image(proc.initial),
        _value_image(proc.output_vars),
        _value_image(getattr(proc, "output_capacity", None)),
        _value_image(proc.signature.as_dict()
                     if getattr(proc, "signature", None) is not None else None),
        _value_image(getattr(proc, "boundary_complete", True)),
        _semantic_contract_image(proc),
        _graph_fingerprint(proc),
    )
    return _digest(image)


def _position_procedure_fingerprint(proc):
    """Semantic procedure identity stable across ``Seq`` pickling."""
    image = (
        "behavior-position-procedure/4",
        _value_image([(framing.as_dict(), projection) for framing, projection in proc.input_framing]),
        tuple(proc.state),
        tuple(proc.inputs),
        tuple(proc.locals),
        tuple(proc.outputs),
        _command_image(proc.body),
        tuple(sorted((name, _type_image(kind))
                     for name, kind in proc.types.items())),
        _value_image(proc.count),
        _value_image(proc.initial),
        _value_image(proc.output_vars),
        _value_image(getattr(proc, "output_capacity", None)),
        _value_image(proc.signature.as_dict()
                     if getattr(proc, "signature", None) is not None else None),
        _value_image(getattr(proc, "boundary_complete", True)),
        _semantic_contract_image(proc),
        _graph_fingerprint(proc),
    )
    return _digest(image)


def procedure_identity(proc):
    """Stable structural identity of one admitted Behavior procedure.

    This is the procedure component already bound into every EncodingPlan,
    exposed so trace-domain and evidence records can identify the executable
    semantics rather than the source file that happened to contain it.  A
    caller should pair it with the frontend semantic version when identities
    must remain distinct across frontend releases.
    """
    ir.validate_procedure_representation(proc)
    return _procedure_fingerprint(proc)


def _command_image(root):
    """Canonical command digest in work linear in the command tree.

    A lowered body is commonly a left-deep ``Seq`` with one node per command.
    Computing a flattened image independently for every nested ``Seq`` walks
    the remaining suffix at every node and is quadratic.  Only maximal
    sequences contribute an image: flatten each such sequence once, while
    nested sequences below branches and loops are handled as their own roots.
    """

    def children(command):
        if isinstance(command, ir.Seq):
            parts = []
            pending = [command]
            while pending:
                part = pending.pop()
                if isinstance(part, ir.Seq):
                    pending.append(part.second)
                    pending.append(part.first)
                else:
                    parts.append(part)
            return tuple(parts)
        return _command_children(command)

    images = {}
    stack = [(root, False)]
    while stack:
        cmd, expanded = stack.pop()
        if cmd in images:
            continue
        if not expanded:
            stack.append((cmd, True))
            for child in reversed(children(cmd)):
                if child not in images:
                    stack.append((child, False))
            continue
        if isinstance(cmd, ir.Skip):
            image = ("skip",)
        elif isinstance(cmd, ir.Assign):
            image = ("assign", cmd.var, _expression_image(cmd.expr))
        elif isinstance(cmd, ir.Store):
            image = ("store", cmd.var, _expression_image(cmd.index),
                     _expression_image(cmd.expr))
        elif isinstance(cmd, ir.Seq):
            image = ("seq", tuple(images[part] for part in children(cmd)))
        elif isinstance(cmd, ir.If):
            image = ("if", _expression_image(cmd.guard),
                     images[cmd.then], images[cmd.orelse])
        elif isinstance(cmd, ir.For):
            image = ("for", cmd.index, _expression_image(cmd.trip_count),
                     images[cmd.body],
                     _value_image(cmd.flow.image
                                  if cmd.flow is not None else None))
        elif isinstance(cmd, ir.Mark):
            image = ("mark", _value_image(cmd.goal))
        elif isinstance(cmd, ir.Fail):
            image = ("fail", _value_image(cmd.code), _value_image(cmd.goal))
        else:
            raise TypeError(f"unknown command in a plan: {type(cmd).__name__}")
        # Store a digest rather than the recursively nested image.  A lowered
        # procedure is commonly one left-leaning Seq per command; retaining the
        # tree here would merely move its recursion failure into fingerprinting.
        images[cmd] = _digest(image)
    return images[root]


def _command_children(cmd):
    if isinstance(cmd, ir.Seq):
        return (cmd.first, cmd.second)
    if isinstance(cmd, ir.If):
        return (cmd.then, cmd.orelse)
    if isinstance(cmd, ir.For):
        return (cmd.body,)
    return ()


def _expression_image(root):
    images = {}
    stack = [(root, False)]
    while stack:
        expr, expanded = stack.pop()
        if expr in images:
            continue
        children = tuple(ranges._children(expr))
        if not expanded:
            stack.append((expr, True))
            for child in reversed(children):
                if child not in images:
                    stack.append((child, False))
            continue
        if isinstance(expr, ir.Var):
            image = ("var", expr.name)
        elif isinstance(expr, ir.Lit):
            image = ("lit", expr.value, _type_image(expr.type))
        elif isinstance(expr, ir.Op):
            image = ("op", expr.op,
                     tuple(images[child] for child in expr.args))
        elif isinstance(expr, ir.Sel):
            image = ("sel", images[expr.array], images[expr.index])
        else:
            raise TypeError(f"unknown expression in a plan: {type(expr).__name__}")
        # If-conversion can make expressions as deep as a long command list.
        # Merkle-style node digests keep the identity iterative and bounded in
        # Python stack depth without losing occurrence or order information.
        images[expr] = _digest(image)
    return images[root]


def _type_image(kind):
    if kind is None:
        return None
    if hasattr(kind, "cells") and hasattr(kind, "elem"):
        return ("array", int(kind.cells), _type_image(kind.elem))
    if kind == types.BOOLEAN:
        return ("boolean",)
    if kind == types.INTEGER:
        return ("integer",)
    raise TypeError(f"unknown type in an encoding plan: {type(kind).__name__}")


def _value_image(value):
    from .uniform_array import UniformArray

    if type(value) is UniformArray:
        return (
            "compact-array",
            value.length,
            _value_image(value.base_image),
            tuple((index, _value_image(item))
                  for index, item in value.sparse_items()),
        )
    if value is None:
        return ("none",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is str:
        return ("str", value)
    if type(value) is bytes:
        return ("bytes", value.hex())
    if type(value) is list:
        return ("list", tuple(_value_image(item) for item in value))
    if type(value) is tuple:
        return ("tuple", tuple(_value_image(item) for item in value))
    if type(value) is dict:
        parts = [(_value_image(key), _value_image(item))
                 for key, item in value.items()]
        return ("dict", tuple(sorted(parts, key=repr)))
    raise TypeError(
        f"a procedure identity cannot encode {type(value).__name__}")


def _diagnostic_image(value):
    if isinstance(value, ExpressionPosition):
        return ("expression-position", value.command, value.role, value.path)
    return _value_image(value)


def _write_digest_image(write, value):
    """Write one collision-free, interpreter-independent identity image.

    JSON's integer encoder first converts an integer to decimal text.  Python caps
    that conversion by process policy, so an *incomplete* range plan with a very wide
    intermediate could crash while trying to identify the diagnostic that should
    refuse it.  This binary framing accepts every finite Python integer and hashes it
    in space proportional to its binary representation.

    Type tags and fixed-width lengths make the encoding injective over the closed
    carrier used by plan identities.  Mapping entries are sorted by their encoded key,
    so insertion order cannot become semantic identity.
    """

    def sized(tag, payload):
        write(tag)
        write(struct.pack(">Q", len(payload)))
        write(payload)

    kind = type(value)
    if value is None:
        write(b"n")
    elif kind is bool:
        write(b"b1" if value else b"b0")
    elif kind is int:
        magnitude = abs(value)
        payload = magnitude.to_bytes(
            max(1, (magnitude.bit_length() + 7) // 8), "big")
        sized(b"i-" if value < 0 else b"i+", payload)
    elif kind is str:
        sized(b"s", value.encode("utf-8"))
    elif kind is bytes:
        sized(b"y", value)
    elif kind is float:
        sized(b"f", struct.pack(">d", value))
    elif kind in {tuple, list}:
        write(b"t" if kind is tuple else b"l")
        write(struct.pack(">Q", len(value)))
        for item in value:
            _write_digest_image(write, item)
    elif isinstance(value, Mapping):
        encoded = []
        for key, item in value.items():
            key_image = bytearray()
            _write_digest_image(key_image.extend, key)
            encoded.append((bytes(key_image), item))
        encoded.sort(key=lambda row: row[0])
        write(b"d")
        write(struct.pack(">Q", len(encoded)))
        for key_image, item in encoded:
            sized(b"k", key_image)
            _write_digest_image(write, item)
    else:
        raise TypeError(
            f"a plan identity cannot encode {type(value).__name__}")


def _digest(image):
    digest = hashlib.sha256()
    _write_digest_image(digest.update, image)
    return digest.hexdigest()


def reuse_encoding_plan(validation, env, *, fixed_inputs=(), _checkpoint=None):
    """Reuse a derived plan only for this procedure and exact entry ranges.

    Goal fragments and bounded steps share this invocation-local owner.
    Supplied state carriers have already adjusted ``env`` before this call;
    a wider successor therefore selects a different plan. The canonical
    range image includes per-cell precision and owns its numeric values,
    rather than retaining mutable caller Range objects as cache keys.
    No formula, concrete state or solver answer is shared here.
    """
    from .validation import _checked_procedure_validation

    _checked_procedure_validation(validation, validation._procedure)
    fixed_inputs = _fixed_input_map(fixed_inputs)
    key = _environment_image(env), fixed_inputs
    planned = validation._implicit_plans.get(key)
    if planned is None:
        planned = plan_encoding(validation._procedure, env, fixed_inputs=fixed_inputs,
                                _procedure_validation=validation,
                                _checkpoint=_checkpoint)
        planned.validate_binding(validation._procedure, env, _procedure_validation=validation)
        if _checkpoint is not None:
            _checkpoint()
        validation._implicit_plans[key] = planned
    return planned


def __getattr__(name):
    """Retain old imports/checkpoint globals, not a second proof implementation."""
    if name in (
        'PositionEncodingPlan', '_PreparedPositionPlan', '_restore_prepared_positions',
        'plan_positions', 'position_analysis_identities', 'prepare_positions', 'verify',
    ):
        from . import closure_proof

        return getattr(closure_proof, name)
    raise AttributeError(name)
