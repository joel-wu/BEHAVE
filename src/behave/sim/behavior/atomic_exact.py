"""Exact bounded analysis for atomic multi-clock Behavior procedures.

One symbolic position is one :class:`GlobalEventSlot`, not one handler call.
Every invoked ``process_<domain>`` reads the same old state.  Its proposed
state is converted to component writes and all compatible proposals commit at
once; a domain without an invocation holds.  This is the SolverIR instance of
the concrete semantics in :mod:`behave.sim.behavior.multiclock`.

The module deliberately consumes lowered procedures and immutable task facts.
It never loads or executes submitted Python.  Source execution is confined to
``IsolatedAtomicMachine`` and is used only by the declared source audit.
"""

from __future__ import annotations

import ast
import hashlib
import json
import time
from dataclasses import dataclass

import z3

from behave.core import runtime_defaults as defaults
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES, SourceGoalRules

from ..symbolic.exact_formula import ExactFormula
from ..core.audit import AuditCheck, AuditCheckStatus, AuditedWitness
from ..core.audit_plan import AuditPlan
from ..core.catalogue import CatalogueManifest
from ..core.clock_schedule import GlobalSchedule
from ..core.conditional_evidence import TrustedAssumption
from ..core.domain import TraceDomain
from ..core.instance import (
    BoundedInstance,
    Feasible,
    Replay,
    StepOutput,
    endpoint_batch_output,
)
from ..core import resources
from ..core.query import QueryCall, SolverAnswer, decide_setwise
from ..core.query_telemetry import QueryTelemetryScope, StageTimings
from ..core.refusal import Refusal, Refused
from ..symbolic import encoder
from ..symbolic.encoder import (
    decode_value as _decode_value, _signed as _signed, _unsigned as _unsigned,
)
from ..symbolic import interp
from ..symbolic import ranges
from ..symbolic import typecheck as types
from .audit import _audit_model
from .exact_solver import solve_exact
from ..symbolic.functional_array import FunctionalArray
from .multiclock import (
    DomainInvocation,
    DomainOwnership,
    IsolatedAtomicMachine,
    atomic_effect,
    atomic_successor,
    ownership_from_source,
)
from .reference_memory import reset_state
from ..symbolic.state_projection import preserved_on_reset
from ..symbolic.uniform_array import UniformArray
from .value_records import canonical_memory, domain_tree, record_tree

__all__ = [
    "ATOMIC_EXACT_FRONTEND",
    "ATOMIC_EXACT_POLICY",
    "AtomicBehaviorInstance",
    "AtomicComponent",
    "atomic_row_contract",
    "build_atomic_behavior",
]


ATOMIC_EXACT_FRONTEND = "behavior-atomic/20"
ATOMIC_EXACT_POLICY = "global-event-old-state-atomic-commit/1"
_INVOKE = "__invoke__"
_READY_RESULT = "__atomic_ready__"
_FUNCTIONAL_ARRAY_MIN_CELLS = defaults.SIM_BEHAVIOR_FUNCTIONAL_ARRAY_MIN_CELLS


def _row_name(domain, name):
    return f"{domain}.{name}"


def _invoke_name(domain):
    return _row_name(domain, _INVOKE)


def _plain_mapping(value, what):
    try:
        got = dict(value)
    except (TypeError, ValueError):
        raise TypeError(f"{what} must be an exact finite mapping") from None
    if any(type(name) is not str or not name for name in got):
        raise TypeError(f"{what} uses nonempty exact string names")
    return got


def _join_range(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return left.join(right)


def _initial_range(value):
    """A scalar hull for a carried value, retaining implicit memory bounds."""

    bounds = []

    def visit(item):
        if type(item) is bool:
            bounds.append(ranges.Range(int(item), int(item)))
            return
        if type(item) is int:
            bounds.append(ranges.Range(item, item))
            return
        if type(item) is UniformArray:
            if item.base_bounds is None:
                visit(item.default)
            else:
                bounds.append(ranges.Range(*item.base_bounds))
            for _index, child in item.sparse_items():
                visit(child)
            return
        if type(item) is list:
            for child in item:
                visit(child)
            return
        raise Refused(Refusal(
            "behavior-atomic-state-carrier-unsupported",
            "the common construction state is outside SolverIR's finite "
            "integer-array carrier",
            value_type=type(item).__name__))

    visit(value)
    # Empty arrays contain no term.  A dummy scalar carrier is harmless and
    # lets the declared recursive type, rather than one observed element, own
    # their shape.
    return ranges.Range(min((bound.lo for bound in bounds), default=0),
                        max((bound.hi for bound in bounds), default=0))


def _value_zip(left, right, fn, path=()):
    if isinstance(left, FunctionalArray) or isinstance(right, FunctionalArray):
        if not (isinstance(left, FunctionalArray)
                and isinstance(right, FunctionalArray)
                and left.length == right.length):
            raise Refused(Refusal(
                "behavior-atomic-state-shape-changed",
                "one handler changes a functional array state component's "
                "fixed shape",
                path=repr(path)))
        return fn(path, left, right)
    if isinstance(left, list) != isinstance(right, list):
        raise Refused(Refusal(
            "behavior-atomic-state-shape-changed",
            "one handler changes a state component between scalar and array form",
            path=repr(path)))
    if isinstance(left, list):
        if len(left) != len(right):
            raise Refused(Refusal(
                "behavior-atomic-state-shape-changed",
                "one handler changes the fixed length of a state array",
                path=repr(path), before=len(left), after=len(right)))
        return [_value_zip(a, b, fn, path + (index,))
                for index, (a, b) in enumerate(zip(left, right))]
    return fn(path, left, right)


def _value_at(value, path):
    if isinstance(value, FunctionalArray):
        if path:
            raise KeyError(path)
        return value
    for part in path:
        value = value[part]
    return value


def _rebuild_value(template, leaves, path=()):
    if isinstance(template, FunctionalArray):
        return leaves[path]
    if isinstance(template, list):
        return [_rebuild_value(item, leaves, path + (index,))
                for index, item in enumerate(template)]
    return leaves[path]


def _leaves(value, path=()):
    if isinstance(value, FunctionalArray):
        yield path, value
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, path + (index,))
    else:
        yield path, value


def _constant(value, rng, kind, name, *, width=None):
    minimum_width = ranges.encoding_width(rng)
    width = minimum_width if width is None else width
    if type(width) is not int or width < minimum_width:
        raise Refused(Refusal(
            "behavior-atomic-state-carrier-too-narrow",
            "the schedule-wide scalar carrier cannot encode one state range",
            state=name, required=minimum_width, got=width))

    def encode(item, item_kind, path=()):
        if item_kind == types.BOOLEAN:
            if type(item) is not bool:
                raise Refused(Refusal(
                    "behavior-atomic-initial-type-mismatch",
                    "the common initial state disagrees with its SolverIR type",
                    state=name, path=repr(path), expected="Boolean",
                    value_type=type(item).__name__))
            scalar = int(item)
        elif item_kind == types.INTEGER:
            if type(item) is not int:
                raise Refused(Refusal(
                    "behavior-atomic-initial-type-mismatch",
                    "the common initial state disagrees with its SolverIR type",
                    state=name, path=repr(path), expected="Integer",
                    value_type=type(item).__name__))
            scalar = item
        elif isinstance(item_kind, types.Array):
            if type(item) is UniformArray:
                # Use the same typed sparse boundary as ordinary Behavior.
                # In particular, retain an indexed memory base rather than
                # enumerating its address space to encode a selected prefix.
                try:
                    return encoder.encode_concrete_value(
                        item, rng, name, kind=item_kind, width=width)
                except encoder.EncodingError as exc:
                    raise Refused(Refusal(
                        "behavior-atomic-initial-type-mismatch",
                        "the compact state disagrees with its SolverIR carrier",
                        state=name, path=repr(path), detail=str(exc))) from None
            if type(item) is not list or len(item) != item_kind.cells:
                raise Refused(Refusal(
                    "behavior-atomic-initial-type-mismatch",
                    "the common initial array disagrees with its fixed recursive "
                    "SolverIR type",
                    state=name, path=repr(path), expected=item_kind.cells))
            if (item_kind.cells >= _FUNCTIONAL_ARRAY_MIN_CELLS
                    and item_kind.elem in (types.BOOLEAN, types.INTEGER)
                    and item
                    and all(type(child) is type(item[0]) and child == item[0]
                            for child in item[1:])):
                return FunctionalArray.uniform(
                    item_kind.cells,
                    encode(item[0], item_kind.elem, path + (0,)),
                )
            return [encode(child, item_kind.elem, path + (index,))
                    for index, child in enumerate(item)]
        else:
            raise Refused(Refusal(
                "behavior-atomic-state-type-unavailable",
                "a common state component has no SolverIR semantic type",
                state=name))
        if not rng.lo <= scalar <= rng.hi:
            raise Refused(Refusal(
                "behavior-atomic-initial-outside-range",
                "the common initial state lies outside its planned finite carrier",
                state=name, value=repr(item), path=repr(path),
                bounds=[rng.lo, rng.hi]))
        return z3.BitVecVal(scalar, width)

    return encode(value, kind)


def _term_equal(left, right):
    if isinstance(left, FunctionalArray) or isinstance(right, FunctionalArray):
        if not (isinstance(left, FunctionalArray)
                and isinstance(right, FunctionalArray)):
            return z3.BoolVal(False)
        return left.equal(right)
    parts = []

    def one(_path, a, b):
        if not z3.is_bv(a) or not z3.is_bv(b):
            raise TypeError("atomic state leaves are bit-vector terms")
        if a.size() != b.size():
            raise Refused(Refusal(
                "behavior-atomic-state-carrier-mismatch",
                "two handlers encode one state component at different widths",
                first=a.size(), second=b.size()))
        parts.append(a == b)
        return a

    _value_zip(left, right, one)
    return z3.And(parts) if parts else z3.BoolVal(True)


def _term_ite(condition, yes, no):
    if isinstance(yes, FunctionalArray) or isinstance(no, FunctionalArray):
        if not (isinstance(yes, FunctionalArray)
                and isinstance(no, FunctionalArray)):
            raise Refused(Refusal(
                "behavior-atomic-state-carrier-mismatch",
                "a conditional state is functional on only one arm"))
        return FunctionalArray.choose(condition, yes, no)
    return _value_zip(
        yes, no,
        lambda _path, a, b: z3.If(condition, *encoder.widen(a, b)))


def _error_term(term, source_codes, target_codes):
    width = max(1, (len(target_codes) - 1).bit_length())
    out = z3.BitVecVal(0, width)
    source_width = max(1, (len(source_codes) - 1).bit_length())
    for index, name in reversed(tuple(enumerate(source_codes[1:], 1))):
        out = z3.If(
            term == z3.BitVecVal(index, source_width),
            z3.BitVecVal(target_codes.index(name), width), out)
    return out


def _physical_input_bounds(signature):
    """Project logical ValueTypes onto their declared SolverIR variables."""

    out = {}
    for binding in signature.input_bindings:
        leaves = {leaf.path: leaf for leaf in binding.layout.leaves()}
        for logical, solver in binding.leaf_bindings:
            leaf = leaves[logical]
            pair = ((0, 1) if leaf.semantic_type == types.BOOLEAN
                    else leaf.carrier.bounds)
            known = out.get(solver.variable)
            out[solver.variable] = (
                pair if known is None else
                (min(known[0], pair[0]), max(known[1], pair[1])))
    return out


def _semantic_leaves(kind, path=()):
    """Yield the scalar leaves of one recursive SolverIR semantic type."""

    if isinstance(kind, types.Array):
        for index in range(kind.cells):
            yield from _semantic_leaves(kind.elem, path + (index,))
        return
    if kind not in (types.BOOLEAN, types.INTEGER):
        raise Refused(Refusal(
            "behavior-atomic-input-type-unavailable",
            "an atomic input leaf has no Boolean/integer SolverIR type",
            semantic_type=repr(kind)))
    yield path, kind


def _input_cell_name(domain, variable, path):
    suffix = "".join(f"[{index}]" for index in path)
    return _row_name(domain, variable) + suffix


def _value_from_type(kind, leaves, path=()):
    if isinstance(kind, types.Array):
        return [_value_from_type(kind.elem, leaves, path + (index,))
                for index in range(kind.cells)]
    return leaves[path]


def _concrete_value_from_type(kind, leaves, path=()):
    """Rebuild one typed concrete value from integer trace-row leaves."""

    if isinstance(kind, types.Array):
        return [
            _concrete_value_from_type(kind.elem, leaves, path + (index,))
            for index in range(kind.cells)
        ]
    value = leaves[path]
    if kind == types.BOOLEAN:
        if value not in (0, 1):
            raise Refused(Refusal(
                "behavior-atomic-boolean-input-outside-carrier",
                "a Boolean trace cell is encoded by exactly zero or one",
                value=repr(value)))
        return bool(value)
    if kind == types.INTEGER:
        return value
    raise Refused(Refusal(
        "behavior-atomic-input-type-unavailable",
        "an atomic input leaf has no Boolean/integer SolverIR type",
        semantic_type=repr(kind)))


def _input_cells(component):
    """The canonical scalar row cells of one handler's physical inputs."""

    rows = []
    seen = set()
    for variable, kind in component.proc.semantics.input_types:
        rng = component.env.get(variable)
        if not isinstance(rng, ranges.Range):
            raise Refused(Refusal(
                "behavior-atomic-input-range-unavailable",
                "a domain input has no finite range in its retained plan",
                domain=component.domain, input=variable))
        for path, leaf_kind in _semantic_leaves(kind):
            name = _input_cell_name(component.domain, variable, path)
            if name in seen:
                raise Refused(Refusal(
                    "behavior-atomic-input-cell-collision",
                    "two physical input leaves have the same trace-row name",
                    domain=component.domain, input=variable, path=list(path)))
            seen.add(name)
            rows.append((name, variable, path, leaf_kind, rng))
    return tuple(rows)


def _project_output_batch(components, emitted):
    """Return the injective endpoint-tagged observation of one global slot.

    ``emitted`` contains logical handler rows.  Endpoint declaration order is
    not cross-stream transaction order, so independent endpoints are ordered
    canonically by name while rows within one endpoint retain handler order.
    """

    by_domain = {domain: [] for domain in components}
    for domain, row in emitted:
        if domain not in by_domain or type(row) is not dict:
            raise Refused(Refusal(
                "behavior-atomic-output-row-malformed",
                "an atomic handler output is a logical record for one domain",
                domain=str(domain)))
        by_domain[domain].append(dict(row))
    batch = []
    projections = sorted(
        (endpoint, domain, fields)
        for domain, component in components.items()
        for endpoint, fields in component.output_endpoints)
    for endpoint, domain, fields in projections:
        for row in by_domain[domain]:
            missing = sorted({source for _public, source in fields} - set(row))
            if missing:
                raise Refused(Refusal(
                    "behavior-atomic-output-field-missing",
                    "a domain output omits a task-bound endpoint field",
                    domain=domain, endpoint=endpoint, fields=missing))
            batch.append({endpoint: {
                public: row[source] for public, source in fields}})
    return tuple(batch)


class AtomicComponent:
    """One lowered domain handler and the finite environment that encodes it."""

    __slots__ = (
        "boundary", "domain", "env", "input_endpoints", "output_endpoints",
        "plan", "proc", "ready", "ready_env", "ready_plan", "reset",
        "reset_env", "reset_plan")

    def __init__(self, domain, proc, env, reset=None, *, input_endpoints=(),
                 output_endpoints=(), boundary=None, plan=None,
                 ready=None, ready_env=None, ready_plan=None,
                 reset_env=None, reset_plan=None):
        from ..symbolic.ir import Procedure

        if type(domain) is not str or not domain:
            raise ValueError("an atomic component needs a nonempty domain")
        if type(proc) is not Procedure:
            raise TypeError("an atomic component needs an exact SolverIR Procedure")
        if reset is not None and type(reset) is not Procedure:
            raise TypeError("an atomic reset is absent or an exact SolverIR Procedure")
        if ready is not None and type(ready) is not Procedure:
            raise TypeError(
                "an atomic readiness observation is absent or an exact "
                "SolverIR Procedure")
        self.domain = domain
        self.proc = proc
        self.reset = reset
        self.ready = ready
        self.boundary = boundary
        self.env = _plain_mapping(env, "an atomic component environment")
        self.reset_env = (
            None if reset is None
            else _plain_mapping(
                self.env if reset_env is None else reset_env,
                "an atomic reset environment"))
        self.ready_env = (
            None if ready is None
            else _plain_mapping(
                self.env if ready_env is None else ready_env,
                "an atomic readiness environment"))
        self.input_endpoints = self._endpoints(
            input_endpoints, "input endpoint")
        self.output_endpoints = self._endpoints(
            output_endpoints, "output endpoint")
        self.plan = plan
        self.reset_plan = reset_plan
        self.ready_plan = ready_plan

    @staticmethod
    def _endpoints(values, what):
        out = []
        seen = set()
        for endpoint, fields in values:
            fields = tuple(tuple(field) for field in fields)
            if (type(endpoint) is not str or not endpoint or endpoint in seen
                    or any(
                        len(field) != 2
                        or any(type(name) is not str or not name for name in field)
                        for field in fields)):
                raise TypeError(f"an atomic {what} projection is malformed")
            public = [name for name, _source in fields]
            source = [name for _public, name in fields]
            if len(public) != len(set(public)) or len(source) != len(set(source)):
                raise ValueError(f"an atomic {what} projection has duplicate fields")
            seen.add(endpoint)
            out.append((endpoint, fields))
        return tuple(sorted(out))

    @property
    def inputs(self):
        return tuple(self.proc.inputs)

    @property
    def outputs(self):
        return tuple(self.proc.outputs)

    @property
    def logical_inputs(self):
        signature = self.proc.signature
        return (() if signature is None else tuple(signature.input_names))

    def decode_inputs(self, values):
        """Reconstruct the source handler's logical arguments from IR leaves."""

        values = _plain_mapping(values, "atomic physical inputs")
        signature = self.proc.signature
        if signature is None:
            return values
        out = {}
        for binding in signature.input_bindings:
            out[binding.name] = binding.reconstruct(values)
        return out

    def physical_inputs(self, row):
        """Rebuild recursive physical input values from one flat trace row."""

        leaves = {}
        kinds = dict(self.proc.semantics.input_types)
        for name, variable, path, _leaf_kind, _rng in _input_cells(self):
            leaves.setdefault(variable, {})[path] = row[name]
        return {
            variable: _concrete_value_from_type(kind, leaves[variable])
            for variable, kind in kinds.items()
        }

    def source_args(self, values):
        logical = self.decode_inputs(values)
        return tuple(logical[name] for name in self.logical_inputs)

    def ready_observation(self, state):
        """Evaluate one old-state readiness observation and its structural hits."""

        if self.ready is None:
            return True, ()
        after, output, hits, error = interp.run_step(self.ready, {}, state)
        if error:
            raise Refused(Refusal(
                "behavior-atomic-ready-failed",
                "a domain readiness observation can fail on an admitted old "
                "state",
                domain=self.domain, error=error))
        changed = sorted(
            name for name, value in after.items()
            if name not in state or state[name] != value)
        if changed:
            raise Refused(Refusal(
                "behavior-atomic-ready-mutates-state",
                "a domain readiness observation changes the old state",
                domain=self.domain, states=changed))
        transactions = output.transactions
        if (len(transactions) != 1
                or set(transactions[0]) != {_READY_RESULT}
                or type(transactions[0][_READY_RESULT]) is not bool):
            raise Refused(Refusal(
                "behavior-atomic-ready-result-malformed",
                "a domain readiness observation must return exactly one Boolean",
                domain=self.domain))
        return transactions[0][_READY_RESULT], tuple(sorted(hits))

    def ready_value(self, state):
        """Return only the Boolean part of :meth:`ready_observation`."""

        return self.ready_observation(state)[0]

    def symbolic_output_rows(self, model, encoding, count):
        """Decode the active batch through the shared procedure contract."""

        physical = {}
        for name, kind in self.proc.semantics.output_types:
            buffer = encoding.post.get(name)
            if not isinstance(buffer, list):
                raise Refused(Refusal(
                    "behavior-atomic-output-buffer-shape-mismatch",
                    "a symbolic physical output lacks its declared batch buffer",
                    domain=self.domain, output=name))
            physical[name] = tuple(
                _decode_value(model, cell, kind) for cell in buffer)
        try:
            logical = self.proc.semantics.reconstruct_outputs(
                count, physical,
                capacity=getattr(self.proc, "output_capacity", None))
            return StepOutput(count, logical).transactions
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "behavior-atomic-output-binding-unavailable",
                "the symbolic physical batch does not reconstruct through the "
                "procedure's retained output ValueBindings",
                domain=self.domain, error=str(exc))) from None


def atomic_row_contract(components):
    """The complete integer row consumed by one global event position."""

    contract = {}
    for domain, component in sorted(components.items()):
        contract[_invoke_name(domain)] = [0, 1]
        for name, _variable, _path, leaf_kind, rng in _input_cells(component):
            bounds = ((0, 1) if leaf_kind == types.BOOLEAN
                      else (rng.lo, rng.hi))
            contract[name] = list(bounds)
    return contract


def _shared_scalar_encoding(components):
    """One fixed scalar width and occurrence carriers for the whole schedule.

    The ordinary position plan uses one scalar encoding across all positions so a
    state value produced at ``t`` is already a valid input image at ``t+1``.  An
    atomic position can select different procedures, but needs the same invariant.
    Reuse each procedure's validated one-step carriers and widen only their encoding
    width; their semantic intervals remain unchanged.
    """

    from ..symbolic.plan import FiniteCarrier, OccurrenceCarriers

    width = 1
    for component in components.values():
        width = max(
            width,
            *(ranges.encoding_width(rng) for rng in component.env.values()
              if isinstance(rng, ranges.Range)),
            *(int(item) for item in component.plan.widths.values()),
            *(carrier.width
              for carrier in component.plan.occurrence_carriers.values()),
        )
    occurrences = {
        domain: OccurrenceCarriers({
            key: FiniteCarrier(carrier.lo, carrier.hi, width)
            for key, carrier in component.plan.occurrence_carriers.items()
        })
        for domain, component in components.items()
    }
    return width, occurrences


def _atomic_signatures(source, file, contract, model):
    """Reuse the common typed task boundary for every domain handler."""

    from .task_boundary import task_method_boundary

    out = {}
    for domain in model.steps:
        if domain is None:
            raise Refused(Refusal(
                "behavior-atomic-anonymous-step",
                "a multi-clock source uses process_<domain> handlers only"))
        boundary = task_method_boundary(
            source, file, contract, method=f"process_{domain}", clock=domain,
            cls=model.cls.name)
        out[domain] = boundary
    return out


def _ready_signature():
    """The sole admitted boundary of a ``<domain>_ready`` observation."""

    from ..core.value_type import ValueType
    from ..symbolic.procedure_contract import ProcedureSignature

    return ProcedureSignature((), ((_READY_RESULT, ValueType.boolean()),))


def _lower_ready_procedure(source, file, domain, state_env, dynamic_state,
                           horizon, *, read_initial=None,
                           boundary_initial=None, value_types=None,
                           construction=None, cls=None, source_rules=EMPTY_SOURCE_RULES):
    """Lower one certified readiness method against a common state carrier."""

    from .extract import LoweringError
    from .lowering import lower_procedure
    from .signature import output_contract_evidence

    env = dict(state_env)
    try:
        ready = lower_procedure(
            source, file, f"{domain}_ready", (_READY_RESULT,),
            state_ranges=env, read_initial=read_initial,
            signature=_ready_signature(), dynamic_state=dynamic_state,
            boundary_initial=boundary_initial, value_types=value_types,
            construction=construction, cls=cls, source_rules=source_rules)
        ready.state_ranges = dict(env)
        ready.state_range_bound = horizon
        ready.output_ranges = output_contract_evidence(
            ready, env, bound=horizon)
        return ready, env
    except LoweringError as exc:
        raise Refused(Refusal(
            "behavior-atomic-ready-unsupported",
            "a domain readiness observation is outside the certified zero-input "
            "Boolean subset",
            domain=domain, code=exc.code, error=str(exc))) from None


def _class_member_kind(model, name):
    """Return the chosen class body's final direct binding kind for ``name``."""

    from .extract import _statement_bindings

    kind = None
    for statement in model.cls.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            kind = "method"
            continue
        if isinstance(statement, ast.Delete) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in statement.targets):
            kind = None
            continue
        if name in _statement_bindings(statement)[0]:
            kind = "other"
    return kind


def _complete_offer(component, event, *, position=None):
    """Whether one event offers the complete endpoint product of a handler.

    A source procedure has one ordinary argument valuation, not one optional
    valuation per endpoint.  Silence is therefore valid, as is a complete
    same-slot product; a nonempty proper subset has no meaning at this boundary
    and is refused rather than padded with invented values.
    """

    expected = {endpoint for endpoint, _fields in component.input_endpoints}
    if not expected:
        return True
    present = expected & set(event.event.offers)
    if present and present != expected:
        raise Refused(Refusal(
            "behavior-atomic-partial-input-offer",
            "an atomic source handler has no presence binding for a partial "
            "same-slot endpoint product",
            domain=component.domain,
            position=position,
            present=sorted(present),
            missing=sorted(expected - present)))
    return present == expected


def _atomic_frontend_manifest(source_manifest, components):
    """Check all handler/readiness producers against the original declaration."""

    from .catalogue import check_source_producers

    declared = set()
    for component in components.values():
        declared.update(check_source_producers(
            component.proc, source_manifest))
        if component.ready is not None:
            declared.update(check_source_producers(
                component.ready, source_manifest))
    if declared != set(source_manifest):
        raise Refused(Refusal(
            "behavior-atomic-frontend-catalogue-incomplete",
            "the lowered handlers and readiness observations do not account for "
            "the complete source goal catalogue",
            missing=sorted(set(source_manifest) - declared),
            extra=sorted(declared - set(source_manifest))))
    return source_manifest


def _shared_state_environment(components, schedule, initial, external_fields=()):
    """Schedule-aware bounded range closure of the common old-state product."""

    # A handler lowering retains only the projection it reads or writes.  The
    # product, however, starts from the one authenticated construction image.
    # Point ranges for projected-away fields keep that common state explicit;
    # active handlers subsequently widen only the components they can change.
    start = {name: _initial_range(value)
             for name, value in initial.items()}
    for component in components.values():
        for name in initial:
            rng = component.env.get(name)
            if isinstance(rng, ranges.Range):
                start[name] = _join_range(start.get(name), rng)
    initial_ranges = dict(start)
    layer = dict(start)
    joined = dict(start)
    domains = set(components)
    preserved = frozenset(name for component in components.values()
                          for name in preserved_on_reset(component.proc))
    released = set()
    for event in schedule.slots:
        full_reset = (set(event.events) == domains
                      and set(event.resets) == domains)
        if full_reset:
            layer = reset_state(initial_ranges, layer, external_fields,
                                preserved_fields=preserved)
            released.clear()
        else:
            proposals = {name: [rng] for name, rng in layer.items()}
            for domain, component in components.items():
                resetting = domain in event.events and domain in event.resets
                ordinary = domain in event.events and domain not in event.resets
                proc = component.reset if resetting else component.proc
                if not ordinary and not resetting:
                    continue
                if resetting and proc is None:
                    if domain in released:
                        raise Refused(Refusal(
                            "multiclock-independent-reset-unavailable",
                            "the fixed schedule reasserts a reset without a "
                            "domain-local reset transition",
                            domain=domain, time=event.time))
                    continue
                env = dict(layer)
                env.update({name: component.env[name]
                            for name in proc.inputs})
                after = ranges.procedure_post(proc, env)
                for name in initial:
                    if isinstance(after.get(name), ranges.Range):
                        proposals[name].append(after[name])
            layer = {}
            for name, alternatives in proposals.items():
                got = alternatives[0]
                for alternative in alternatives[1:]:
                    got = got.join(alternative)
                layer[name] = got
            released.update(
                domain for domain in event.events if domain not in event.resets)
        for name, rng in layer.items():
            joined[name] = _join_range(joined.get(name), rng)
    return joined


def _physical_product(source_ownership, components):
    """Bind source fields to the SolverIR state product used by all handlers."""

    initial = {}
    for component in components.values():
        for name, value in component.proc.initial.items():
            known = initial.get(name, value)
            if name in initial and known != value:
                raise Refused(Refusal(
                    "behavior-atomic-initial-state-mismatch",
                    "domain lowerings disagree on one physical initial value",
                    state=name))
            initial[name] = value

    owners = {}
    merge = {}
    bindings = {}
    for logical, domains in source_ownership.owners:
        candidates = []
        for component in components.values():
            binding = component.proc.semantics.value_binding(
                "state", logical, required=False)
            if binding is not None:
                candidates.append(binding)
        if candidates and any(binding != candidates[0]
                              for binding in candidates[1:]):
            raise Refused(Refusal(
                "behavior-atomic-state-binding-conflict",
                "domain handlers bind one logical state field differently",
                state=logical))
        binding = candidates[0] if candidates else None
        variables = (() if binding is None and logical not in initial else
                     (logical,) if binding is None else binding.solver_variables)
        if binding is not None:
            bindings[logical] = binding
        for variable in variables:
            if variable not in initial:
                raise Refused(Refusal(
                    "behavior-atomic-state-binding-incomplete",
                    "a logical state binding names an absent physical variable",
                    state=logical, variable=variable))
            known = owners.get(variable)
            if known is not None and known != tuple(domains):
                raise Refused(Refusal(
                    "behavior-atomic-state-binding-overlap",
                    "two logical state fields give one physical variable different "
                    "domain ownership",
                    variable=variable))
            owners[variable] = tuple(domains)
            rule = source_ownership.merge_for(logical)
            if rule is not None:
                merge[variable] = rule
    missing = sorted(set(initial) - set(owners))
    if missing:
        raise Refused(Refusal(
            "behavior-atomic-physical-ownership-incomplete",
            "the lowered state product contains variables with no source-field "
            "ownership binding",
            states=missing))
    return initial, DomainOwnership(owners, merge), bindings


def _prepare_atomic_frontend(source, file, contract, horizon, *,
                             read_initial=None, construction=None,
                             value_types=None, boundary_initial=None, cls=None,
                             prove_outputs=True):
    """Lower the schedule-independent, task-bound domain procedures once."""

    from .construction import ConstructionUnavailable, construction_facts
    from .extract import source_model
    from .lowering import lower_source, lower_with_state_ranges
    from .source_plan import plan_source

    model, source_ownership = ownership_from_source(
        source, file=file, cls=cls, contract=contract)
    cls = model.cls.name
    source_manifest = plan_source(
        model, source, file, source_rules=contract.source_rules).manifest
    if construction is None:
        try:
            construction = construction_facts(
                source, file, cls_name=model.cls.name)
        except ConstructionUnavailable as exc:
            raise Refused(Refusal(
                "behavior-atomic-construction-unavailable",
                "the common atomic entry state could not be established in the "
                "isolated construction service",
                error=str(exc))) from None
    else:
        from .construction import ConstructionFacts

        if type(construction) is not ConstructionFacts:
            raise TypeError(
                "atomic Behavior construction is an exact ConstructionFacts")
    declared_state = {name for name, _domains in source_ownership.owners}
    source_initial = {
        name: value for name, value in construction.exact_values().items()
        if name in declared_state
    }
    source_initial.update(boundary_initial or {})
    missing_initial = sorted(declared_state - set(source_initial))
    if missing_initial:
        raise Refused(Refusal(
            "behavior-atomic-state-carrier-unsupported",
            "the common construction record does not carry every declared state "
            "field exactly",
            states=missing_initial))
    boundaries = _atomic_signatures(source, file, contract, model)
    dynamic_state = frozenset(
        name for name, domains in source_ownership.owners if domains)
    boundary_state = frozenset((boundary_initial or {}).keys())
    owners_by_state = dict(source_ownership.owners)
    handler_dynamic_state = {
        domain: frozenset(
            source_model(
                source, file, step=f"process_{domain}", cls=cls).step_state_reads()
        ) & frozenset(
            name for name, owners in owners_by_state.items()
            if any(owner != domain for owner in owners)
        )
        for domain in boundaries
    }
    ready_dynamic_state = {}
    for domain in boundaries:
        ready_name = f"{domain}_ready"
        if ready_name not in model.helpers:
            if _class_member_kind(model, ready_name) is not None:
                raise Refused(Refusal(
                    "behavior-atomic-ready-not-callable",
                    "a domain readiness member is not a callable source method",
                    domain=domain, member=ready_name))
            ready_dynamic_state[domain] = None
            continue
        ready_model = source_model(source, file, step=ready_name, cls=cls)
        if ready_model.inputs:
            raise Refused(Refusal(
                "behavior-atomic-ready-takes-input",
                "a domain readiness observation must be a zero-input method",
                domain=domain, inputs=list(ready_model.inputs)))
        writes = tuple(ready_model.step_state_contract())
        if writes:
            raise Refused(Refusal(
                "behavior-atomic-ready-mutates-state",
                "a domain readiness observation may write model state",
                domain=domain, states=list(writes)))
        ready_dynamic_state[domain] = frozenset(
            ready_model.step_state_reads()) & dynamic_state
    components = {}
    for domain, boundary in sorted(boundaries.items()):
        signature = boundary.signature
        input_endpoints = tuple(
            (item.endpoint, item.fields) for item in boundary.inputs)
        output_endpoints = tuple(
            (item.endpoint, item.fields) for item in boundary.outputs)
        proc = lower_source(
            source, file=file, method=f"process_{domain}",
            outputs=signature.output_names,
            input_bounds=None, bound=horizon,
            signature=signature, read_initial=read_initial,
            dynamic_state=(handler_dynamic_state[domain] | boundary_state),
            boundary_initial=boundary_initial,
            value_types=value_types, construction=construction, cls=cls,
            source_rules=contract.source_rules, prove_outputs=prove_outputs).proc
        from .task_boundary import bind_input_framing

        bind_input_framing(proc, boundary, contract)
        reset = None
        reset_name = f"reset_{domain}"
        if reset_name in model.helpers:
            empty = type(signature)((), ())
            reset = lower_with_state_ranges(
                source, file=file, method=reset_name, outputs=(),
                input_bounds={}, bound=horizon, signature=empty,
                read_initial=read_initial, dynamic_state=boundary_state,
                boundary_initial=boundary_initial,
                value_types=value_types, construction=construction, cls=cls)
        env = dict(proc.state_ranges)
        env.update({
            name: ranges.Range(*bounds)
            for name, bounds in _physical_input_bounds(signature).items()})
        components[domain] = AtomicComponent(
            domain, proc, env, reset=reset,
            input_endpoints=input_endpoints,
            output_endpoints=output_endpoints, boundary=boundary)
    initial, ownership, state_bindings = _physical_product(
        source_ownership, components)
    # Readiness is evaluated over the same old-state product as the handlers.
    # Its preliminary catalogue therefore uses the union of the handler carriers,
    # not the point range obtained by running a non-mutating predicate in isolation.
    common_ready_env = {
        name: _initial_range(value) for name, value in initial.items()
    }
    for component in components.values():
        for name in initial:
            rng = component.env.get(name)
            if isinstance(rng, ranges.Range):
                common_ready_env[name] = _join_range(
                    common_ready_env.get(name), rng)
    for domain, component in components.items():
        if ready_dynamic_state[domain] is None:
            continue
        component.ready, component.ready_env = _lower_ready_procedure(
            source, file, domain, common_ready_env,
            ready_dynamic_state[domain] | boundary_state, horizon,
            read_initial=read_initial, boundary_initial=boundary_initial,
            value_types=value_types, construction=construction, cls=cls,
            source_rules=contract.source_rules)
    frontend = _atomic_frontend_manifest(source_manifest, components)
    return (
        model, source_ownership, boundaries, handler_dynamic_state,
        ready_dynamic_state,
        components, initial, ownership, state_bindings, frontend,
    )


def atomic_frontend_catalogue(source, file, contract, horizon, *,
                              read_initial=None, construction=None,
                              value_types=None, cls=None):
    """Return the schedule-independent manifest of all domain handlers."""

    if type(horizon) is not int or horizon < 1:
        raise TypeError("an atomic frontend horizon is a positive integer")
    prepared = _prepare_atomic_frontend(
        source, file, contract, horizon, read_initial=read_initial,
        construction=construction, value_types=value_types, cls=cls)
    return prepared[-1]


def _prepare_atomic_inputs(source, file, contract, schedule, *,
                          artifact=None, task_contract=None,
                          complete_catalogue=None, read_initial=None,
                          construction=None, value_types=None,
                          memory_environment=None, memory_horizon=None, cls=None,
                          prove_outputs=True):
    """Prepare shared finite components, without a whole-schedule formula.

    Local execution/source audit and optional bounded analysis consume the same
    lowering, carriers, ownership and catalogue. Only bounded analysis requires
    universal handler-output admission. Local guidance checks its native trace;
    neither preparation alone proves product-wide totality. Readiness remains
    certified against its Boolean control signature in both modes.
    """

    from .lowering import lower_procedure
    from .signature import output_contract_evidence
    from .task_boundary import bind_input_framing


    if type(source) is not str or type(file) is not str or not file:
        raise TypeError("atomic Behavior construction needs frozen source and file")
    if type(schedule) is not GlobalSchedule:
        raise TypeError("atomic Behavior construction needs a GlobalSchedule")
    horizon = len(schedule.slots)
    memory_initial = None
    if memory_environment is None:
        if memory_horizon is not None:
            raise ValueError(
                "a memoryless atomic Behavior product has no memory horizon")
    else:
        from .task_boundary import _memory_initial_image

        if memory_horizon != horizon:
            raise Refused(Refusal(
                "behavior-atomic-memory-horizon-mismatch",
                "the atomic clock schedule and memory environment have different "
                "horizons",
                schedule=horizon, memory=memory_horizon))
        memory_initial = _memory_initial_image(
            source, file, contract, memory_environment, memory_horizon,
            construction=construction, cls=cls)
    (
        _model, source_ownership, boundaries, handler_dynamic_state,
        ready_dynamic_state,
        components, initial, ownership, state_bindings, prepared_frontend,
    ) = _prepare_atomic_frontend(
        source, file, contract, horizon, read_initial=read_initial,
        construction=construction, value_types=value_types,
        boundary_initial=memory_initial, cls=cls, prove_outputs=prove_outputs)
    cls = _model.cls.name

    external_fields = tuple(
        variable for memory in (contract.memories if memory_environment is not None else ())
        for variable in (state_bindings[memory.name].solver_variables
                         if memory.name in state_bindings else (memory.name,)))
    shared = _shared_state_environment(components, schedule, initial, external_fields)
    # Retain one common state carrier in every component.  Re-lowering against
    # it also resolves loops/widths from the actual interleaving rather than from
    # a fictitious run containing only that domain.
    rebuilt = {}
    for domain, component in components.items():
        boundary = boundaries[domain]
        signature = boundary.signature
        input_endpoints = tuple(
            (item.endpoint, item.fields) for item in boundary.inputs)
        output_endpoints = tuple(
            (item.endpoint, item.fields) for item in boundary.outputs)
        env = dict(shared)
        env.update({name: ranges.Range(*bounds)
                    for name, bounds in _physical_input_bounds(signature).items()})
        proc = lower_procedure(
            source, file, f"process_{domain}", signature.output_names,
            state_ranges=env, read_initial=read_initial,
            signature=signature,
            dynamic_state=(handler_dynamic_state[domain]
                           | frozenset((memory_initial or {}).keys())),
            boundary_initial=memory_initial, value_types=value_types,
            construction=construction, cls=cls, source_rules=contract.source_rules)
        bind_input_framing(proc, boundary, contract)
        proc.state_ranges = dict(env)
        proc.state_range_bound = horizon
        if prove_outputs:
            proc.output_ranges = output_contract_evidence(
                proc, env, bound=horizon)
        reset = component.reset
        if reset is not None:
            reset_env = dict(shared)
            reset = lower_procedure(
                source, file, f"reset_{domain}", (), state_ranges=reset_env,
                read_initial=read_initial, signature=type(signature)((), ()),
                dynamic_state=frozenset((memory_initial or {}).keys()),
                boundary_initial=memory_initial,
                value_types=value_types, construction=construction, cls=cls)
            reset.state_ranges = dict(reset_env)
            reset.state_range_bound = horizon
        ready = None
        ready_env = None
        ready_plan = None
        if ready_dynamic_state[domain] is not None:
            ready, ready_env = _lower_ready_procedure(
                source, file, domain, shared,
                (ready_dynamic_state[domain]
                | frozenset((memory_initial or {}).keys())),
                horizon, read_initial=read_initial,
                boundary_initial=memory_initial, value_types=value_types,
                construction=construction, cls=cls, source_rules=contract.source_rules)
        from ..symbolic.plan import plan_encoding

        plan = plan_encoding(proc, env, bound=horizon)
        if not plan.complete:
            raise Refused(Refusal(
                "behavior-atomic-encoding-plan-incomplete",
                "a domain handler has no complete finite encoding plan",
                domain=domain,
                unresolved=[item.detail for item in plan.unresolved]))
        reset_plan = None
        if reset is not None:
            reset_plan = plan_encoding(reset, reset_env, bound=horizon)
            if not reset_plan.complete:
                raise Refused(Refusal(
                    "behavior-atomic-reset-plan-incomplete",
                    "a domain-local reset has no complete finite encoding plan",
                    domain=domain,
                    unresolved=[item.detail
                                for item in reset_plan.unresolved]))
        if ready is not None:
            ready_plan = plan_encoding(ready, ready_env, bound=horizon)
            if not ready_plan.complete:
                raise Refused(Refusal(
                    "behavior-atomic-ready-plan-incomplete",
                    "a domain readiness observation has no complete finite "
                    "encoding plan",
                    domain=domain,
                    unresolved=[item.detail
                                for item in ready_plan.unresolved]))
        rebuilt[domain] = AtomicComponent(
            domain, proc, env, reset=reset,
            input_endpoints=input_endpoints,
            output_endpoints=output_endpoints,
            boundary=boundary,
            plan=plan, reset_env=(reset_env if reset is not None else None),
            reset_plan=reset_plan, ready=ready, ready_env=ready_env,
            ready_plan=ready_plan)
    components = rebuilt
    frontend = _atomic_frontend_manifest(prepared_frontend, components)
    return dict(
        components=components, schedule=schedule, ownership=ownership,
        source=source, file=file,
        source_ownership=source_ownership, state_bindings=state_bindings,
        frontend_catalogue=frontend, catalogue=complete_catalogue,
        artifact=artifact, task_contract=task_contract,
        observation_contract=contract,
        initial=initial,
        memory_interfaces=(contract.memories
                           if memory_environment is not None else ()),
        memory_environment=memory_environment,
        memory_horizon=memory_horizon)


def prepare_atomic_context(source, file, contract, schedule, **options):
    """Prepare execution and source audit, not an exact-analysis instance.

    The real schedule and its horizon remain intact. This context deliberately
    has no bounded query/bind API or product-totality certificate.
    """
    prepared = _prepare_atomic_inputs(
        source, file, contract, schedule, prove_outputs=False, **options)
    return _AtomicContext(**prepared)


def build_atomic_behavior(source, file, contract, schedule, **options):
    """Prepare the shared components and establish the bounded atomic product."""
    prepared = _prepare_atomic_inputs(
        source, file, contract, schedule, prove_outputs=True, **options)
    return AtomicBehaviorInstance(**prepared)


class _SymbolicSlot:
    __slots__ = (
        "encodings", "halted_before", "hits", "inputs", "outputs",
        "unsigned_inputs")

    def __init__(self, inputs, halted_before, encodings, hits, outputs,
                 unsigned_inputs):
        self.inputs = dict(inputs)
        self.halted_before = halted_before
        self.encodings = tuple(encodings)
        self.hits = dict(hits)
        self.outputs = tuple(outputs)
        self.unsigned_inputs = frozenset(unsigned_inputs)


def _symbolic_ready_value(component, encoding):
    """Return the Boolean term of one certified readiness observation."""

    if tuple(component.ready.semantics.output_types) != (
            (_READY_RESULT, types.BOOLEAN),):
        raise Refused(Refusal(
            "behavior-atomic-ready-result-malformed",
            "a lowered readiness boundary is not one Boolean output",
            domain=component.domain))
    buffer = encoding.post.get(_READY_RESULT)
    if not isinstance(buffer, list) or len(buffer) != 1 \
            or not z3.is_bv(buffer[0]):
        raise Refused(Refusal(
            "behavior-atomic-ready-result-malformed",
            "a lowered readiness boundary does not carry one Boolean cell",
            domain=component.domain))
    count = encoding.post.get(encoder.COUNT)
    if count is not None:
        if not z3.is_bv(count):
            raise Refused(Refusal(
                "behavior-atomic-ready-result-malformed",
                "a lowered readiness boundary has a non-scalar result count",
                domain=component.domain))
        return z3.And(count == 1, buffer[0] == 1)
    return buffer[0] == 1


@dataclass(frozen=True)
class _AtomicBoundary:
    """Everything carried between slots, including non-Behavior history.

    A local query must pin this entire boundary from its audited prefix. Merely
    pinning the model fields would lose frame position, transaction monitors,
    terminal failure and which domains have already left reset.
    """

    state: dict
    halted: z3.BoolRef
    error: z3.BitVecRef
    released: frozenset
    framing: dict
    transactions: object

    @classmethod
    def initial(cls, instance):
        from ..core.input_framing import symbolic_initial

        return cls(
            state={name: _constant(
                value, instance.state_env[name], instance.state_types[name], name,
                width=instance.scalar_width)
                for name, value in instance.initial.items()},
            halted=z3.BoolVal(False),
            error=z3.BitVecVal(0, max(1, (len(instance.error_codes) - 1).bit_length())),
            released=frozenset(),
            framing={domain: tuple(symbolic_initial(framing)
                                   for framing, _ in component.proc.input_framing)
                     for domain, component in instance.components.items()},
            transactions=(instance.observations.initial()
                          if instance.observations is not None else None))


@dataclass(frozen=True)
class _AtomicStep:
    after: _AtomicBoundary
    slot: _SymbolicSlot
    definitions: tuple
    restrictions: tuple
    invalid: z3.BoolRef

    @property
    def formula(self):
        # Local queries audit one step. A whole-schedule fold instead combines
        # the raw terms and audits once, avoiding a growing-prefix audit per slot.
        return ExactFormula(definitions=self.definitions, restrictions=self.restrictions,
                            boundary_atoms=self.slot.inputs.values())


def _merge_atomic_leaf(old, writes, *, identical, halted, invalid):
    """Commit the same per-cell differences as the native atomic kernel."""
    if isinstance(old, FunctionalArray):
        indices = {}
        for _active, value in writes:
            for index in old.difference_indices(value):
                indices.setdefault(index.get_id(), index)
        result = old
        for index in indices.values():
            previous = old.select(index)
            cells = [(z3.And(active, z3.Not(_term_equal(value.select(index), previous))),
                      value.select(index)) for active, value in writes]
            cell = _merge_atomic_leaf(previous, cells, identical=identical,
                                      halted=halted, invalid=invalid)
            result = result.store(index, cell, z3.Or([active for active, _ in cells]))
        return result
    for index, (left, left_value) in enumerate(writes):
        for right, right_value in writes[index + 1:]:
            invalid.append(z3.And(z3.Not(halted), left, right,
                z3.Not(_term_equal(left_value, right_value)) if identical else z3.BoolVal(True)))
    result = old
    for active, value in writes:
        result = _term_ite(active, value, result)
    return result


def _encode_atomic_slot(instance, before, event, *, position):
    """Encode one global event without expanding a prefix or running a solver.

    ``position`` preserves the actual schedule occurrence and term namespace;
    this is not a one-slot replacement schedule. Invalid ownership/readiness
    remains a separate obligation, never a restriction pruning legal inputs.
    """
    from ..core.input_framing import symbolic_step

    state = before.state
    halted = before.halted
    released = set(before.released)
    framing_states = {domain: list(states) for domain, states in before.framing.items()}
    definitions, restrictions, invalid = [], [], []
    row_terms, invoke_terms, domain_inputs = {}, {}, {}
    unsigned_inputs = set()
    for domain, component in instance.components.items():
        invoke = z3.BitVec(f"invoke.{domain}@{position}", instance.scalar_width)
        row_terms[_invoke_name(domain)] = invoke
        unsigned_inputs.add(_invoke_name(domain))
        invoke_terms[domain] = invoke == 1
        restrictions.append(z3.Or(invoke == 0, invoke == 1))
        input_leaves = {}
        for name, variable, path, leaf_kind, rng in _input_cells(component):
            term = z3.BitVec(f"input.{name}@{position}", instance.scalar_width)
            row_terms[name] = term
            if leaf_kind == types.BOOLEAN:
                unsigned_inputs.add(name)
            input_leaves.setdefault(variable, {})[path] = term
            low, high = ((0, 1) if leaf_kind == types.BOOLEAN else (rng.lo, rng.hi))
            restrictions.extend((term >= low, term <= high))
        domain_inputs[domain] = {
            variable: _value_from_type(kind, input_leaves[variable])
            for variable, kind in component.proc.semantics.input_types}
    for domain, component in instance.components.items():
        for index, (framing, projection) in enumerate(component.proc.input_framing):
            markers = {public: domain_inputs[domain][logical]
                       for public, logical in projection if public in framing.fields}
            framing_states[domain][index], legal = symbolic_step(
                framing, framing_states[domain][index], markers,
                reset=domain in event.resets,
                accepted=z3.And(z3.Not(halted), invoke_terms[domain]))
            restrictions.append(legal)

    full_reset = (set(event.events) == set(instance.components)
                  and set(event.resets) == set(instance.components))
    encoded, output_terms, failures = [], [], []
    hit_terms = {}
    proposals = {name: [] for name in state}
    if full_reset:
        for domain in instance.components:
            restrictions.append(z3.Not(invoke_terms[domain]))
        successor = {name: old if name in instance.reset_preserved_fields else _term_ite(
            z3.Not(halted),
            _constant(instance.initial[name], instance.state_env[name],
                      instance.state_types[name], name, width=instance.scalar_width),
            old) for name, old in state.items()}
        released.clear()
    else:
        for domain, component in instance.components.items():
            ordinary = domain in event.events and domain not in event.resets
            resetting = domain in event.events and domain in event.resets
            if resetting:
                restrictions.append(z3.Not(invoke_terms[domain]))
                reset = component.reset
                if reset is None:
                    if domain in released:
                        raise Refused(Refusal(
                            "multiclock-independent-reset-unavailable",
                            "a fixed schedule reasserts a domain reset but "
                            "the Behavior source has no reset_<domain> step",
                            domain=domain, time=event.time))
                    continue
                active = z3.BoolVal(True)
                proc, inputs = reset, {}
            elif ordinary:
                active = invoke_terms[domain]
                offered = _complete_offer(component, event, position=position)
                ready = z3.BoolVal(True)
                if component.ready is not None:
                    ready_proc = component.ready
                    ready_pre = {name: state[name] for name in ready_proc.state if name in state}
                    ready_encoding = encoder.encode_step(
                        ready_proc, pre=ready_pre, env=component.ready_env,
                        tag=f"@{position}.{domain}.ready", plan=component.ready_plan,
                        occurrence_carriers=instance.ready_occurrence_carriers[domain],
                        scalar_width=instance.scalar_width)
                    definitions.extend(ready_encoding.definitions)
                    restrictions.extend(ready_encoding.restrictions)
                    changed = []
                    for name, value in ready_encoding.state_post.items():
                        if name not in state:
                            raise Refused(Refusal(
                                "behavior-atomic-ready-state-outside-product",
                                "a readiness observation refers to state outside the common product",
                                domain=domain, state=name))
                        changed.append(z3.Not(_term_equal(value, state[name])))
                    invalid.append(z3.And(z3.Not(halted), z3.Or(
                        z3.Not(ready_encoding.ok), z3.Not(ready_encoding.output_valid), *changed)))
                    ready = _symbolic_ready_value(component, ready_encoding)
                    for goal, condition in ready_encoding.hits.items():
                        hit_terms[goal] = z3.Or(hit_terms.get(goal, z3.BoolVal(False)),
                                                z3.And(z3.Not(halted), condition))
                enabled = z3.And(z3.BoolVal(offered), ready)
                if component.boundary.inputs:
                    # Offers permit presentation, but the input queue may be empty.
                    restrictions.append(z3.Implies(active, enabled))
                else:
                    # Clock-only handlers have no transaction-presence choice.
                    restrictions.append(active == enabled)
                proc, inputs = component.proc, domain_inputs[domain]
            else:
                restrictions.append(z3.Not(invoke_terms[domain]))
                continue

            pre = {name: state[name] for name in proc.state if name in state}
            enc = encoder.encode_step(
                proc, pre={**pre, **inputs},
                env=component.reset_env if resetting else component.env,
                tag=f"@{position}.{domain}",
                plan=component.reset_plan if resetting else component.plan,
                occurrence_carriers=(instance.reset_occurrence_carriers[domain]
                                     if resetting else instance.occurrence_carriers[domain]),
                scalar_width=instance.scalar_width)
            definitions.extend(enc.definitions)
            restrictions.extend(enc.restrictions)
            encoded.append((domain, active, enc))
            failed = z3.And(active, z3.Not(enc.ok))
            failures.append((failed, _error_term(enc.error, enc.error_codes, instance.error_codes)))
            if ordinary:
                for goal, condition in enc.hits.items():
                    hit_terms[goal] = z3.Or(hit_terms.get(goal, z3.BoolVal(False)),
                                           z3.And(z3.Not(halted), active, condition))
                # Reset is a state transition, not an endpoint invocation.
                output_terms.append((domain, active, enc))
            for name, new in enc.state_post.items():
                if resetting and name in instance.external_fields:
                    continue
                if name not in state:
                    raise Refused(Refusal(
                        "behavior-atomic-state-outside-common-product",
                        "a handler writes state absent from the common product",
                        domain=domain, state=name))
                for path, old_leaf in _leaves(state[name]):
                    new_leaf = _value_at(new, path)
                    owned = domain in instance.ownership.domains_for(name)
                    # Owned compact arrays are diffed once, cellwise, by the
                    # commit below. A whole-array equality here would duplicate
                    # that work and cannot establish per-address conflicts.
                    changed = (z3.And(active, enc.ok) if owned and isinstance(old_leaf, FunctionalArray)
                               else z3.And(active, enc.ok, z3.Not(_term_equal(new_leaf, old_leaf))))
                    if not owned:
                        # Held fields are not writes. A real out-of-owner write is
                        # non-total, not grounds to exclude the input valuation.
                        invalid.append(z3.And(z3.Not(halted), changed))
                        continue
                    proposals[name].append((path, changed, new_leaf, domain))

        # The terminal carrier has one error code; do not invent a failure winner.
        for index, (left, _left_error) in enumerate(failures):
            for right, _right_error in failures[index + 1:]:
                invalid.append(z3.And(z3.Not(halted), left, right))

        any_failure = z3.Or([flag for flag, _ in failures])
        normal_state = dict(state)
        for name, writes in proposals.items():
            leaves = dict(_leaves(state[name]))
            for path, old_value in tuple(leaves.items()):
                same_path = [(active, value) for at, active, value, _domain in writes if at == path]
                leaves[path] = _merge_atomic_leaf(
                    old_value, same_path, identical=instance.ownership.merge_for(name) == "identical",
                    halted=halted, invalid=invalid)
            normal_state[name] = _rebuild_value(state[name], leaves)
        successor = {name: _term_ite(z3.Or(halted, any_failure), old, normal_state[name])
                     for name, old in state.items()}
        released.update(domain for domain in event.events if domain not in event.resets)

    any_failure = z3.Or([flag for flag, _ in failures])
    next_error = before.error
    for flag, term in failures:
        next_error = z3.If(z3.And(z3.Not(halted), flag), term, next_error)
    visible_outputs = tuple((domain, z3.And(active, z3.Not(any_failure)), encoding)
                            for domain, active, encoding in output_terms)
    history = before.transactions
    if instance.observations is not None:
        history, observed = instance.observations.symbolic_step(
            history, output_terms, visible_outputs, halted)
        hit_terms.update(observed)
    return _AtomicStep(
        after=_AtomicBoundary(
            state=successor, halted=z3.Or(halted, any_failure), error=next_error,
            released=frozenset(released),
            framing={domain: tuple(states) for domain, states in framing_states.items()},
            transactions=history),
        slot=_SymbolicSlot(row_terms, halted, encoded, hit_terms, visible_outputs, unsigned_inputs),
        definitions=tuple(definitions), restrictions=tuple(restrictions),
        invalid=z3.Or(invalid))


class _AtomicFormula:
    """Fold the same single-slot relation over a fixed bounded schedule."""

    __slots__ = (
        "boundaries", "constraint", "error", "error_codes", "formula", "goals",
        "halted", "invalid", "slots", "states", "transaction_states")

    def __init__(self, instance):
        # No active occurrence means False, not an unsupported catalogue entry.
        self.goals = frozenset(instance._frontend_catalogue)
        if instance.observations is not None:
            self.goals |= frozenset(instance.observations.catalogue)
        self.error_codes = instance.error_codes
        boundaries = [_AtomicBoundary.initial(instance)]
        slots, definitions, restrictions, boundary_atoms, invalid = [], [], [], [], []
        for position, event in enumerate(instance.schedule.slots):
            step = _encode_atomic_slot(instance, boundaries[-1], event, position=position)
            boundaries.append(step.after)
            slots.append(step.slot)
            definitions.extend(step.definitions)
            restrictions.extend(step.restrictions)
            boundary_atoms.extend(step.slot.inputs.values())
            invalid.append(step.invalid)
        self.boundaries = tuple(boundaries)
        self.slots = tuple(slots)
        self.states = [boundary.state for boundary in boundaries]
        self.halted = [boundary.halted for boundary in boundaries]
        self.transaction_states = ([boundary.transactions for boundary in boundaries]
                                   if instance.observations is not None else [])
        self.error = boundaries[-1].error
        self.formula = ExactFormula(definitions=definitions, restrictions=restrictions,
                                    boundary_atoms=boundary_atoms)
        self.constraint = z3.And(self.formula.full_constraints)
        self.invalid = z3.Or(invalid)

    def supports(self, goal):
        return goal in self.goals

    def goal_condition(self, goal):
        if not self.supports(goal):
            raise Refused(Refusal(
                "behavior-atomic-goal-not-encoded",
                "the atomic component encodings do not contain this goal",
                goal=goal))
        terms = [slot.hits[goal] for slot in self.slots if goal in slot.hits]
        return z3.Or(terms) if terms else z3.BoolVal(False)

    def decode_rows(self, model):
        return [
            {
                name: (_unsigned(model, term)
                       if name in slot.unsigned_inputs else _signed(model, term))
                for name, term in slot.inputs.items()
            }
            for slot in self.slots]

    def constrain_rows(self, rows):
        return z3.And([
            term == row[name]
            for slot, row in zip(self.slots, rows)
            for name, term in slot.inputs.items()
        ])

    def decode_witness(self, model, instance, rows, domain):
        states = [
            {name: _decode_value(model, value, instance.state_types[name], compact=True)
             for name, value in state.items()}
            for state in self.states]
        hit_sets = []
        outputs = []
        for slot in self.slots:
            hit_sets.append(tuple(sorted(
                goal for goal, term in slot.hits.items()
                if z3.is_true(model.eval(term, model_completion=True)))))
            emitted = []
            for component_domain, active, enc in slot.outputs:
                if not z3.is_true(model.eval(
                        z3.And(z3.Not(slot.halted_before), active, enc.ok),
                        model_completion=True)):
                    continue
                count = _signed(model, enc.post[encoder.COUNT])
                output_rows = instance.components[
                    component_domain].symbolic_output_rows(model, enc, count)
                for row in output_rows:
                    emitted.append((
                        component_domain,
                        row,
                    ))
            outputs.append(_project_output_batch(instance.components, emitted))
        error_index = _unsigned(model, self.error)
        terminal = (self.error_codes[error_index]
                    if 0 <= error_index < len(self.error_codes) else "")
        return AuditedWitness(
            domain=domain, states=instance._record_states(states), inputs=rows, outputs=outputs,
            hit_sets=hit_sets, terminal_error=terminal)


class _AtomicContext:
    """Shared compiled execution and source-audit context for a real schedule.

    This is not a partially initialized analysis instance: it cannot bind or
    answer bounded queries. The exact subclass adds the formula, totality proof
    and analysis API. Both use the same concrete semantics and slot relation.
    """

    def __init__(self, components, schedule, ownership, *, source=None,
                 file="behavior.py", frontend_catalogue=None, catalogue=None,
                 artifact=None, task_contract=None, initial=None,
                 source_ownership=None, state_bindings=None,
                 memory_interfaces=(), memory_environment=None,
                 memory_horizon=None, observation_contract=None):
        components = _plain_mapping(components, "atomic Behavior components")
        if len(components) < 2 or any(
                type(component) is not AtomicComponent
                or component.domain != domain
                for domain, component in components.items()):
            raise TypeError(
                "atomic Behavior analysis needs two or more domain components")
        if type(schedule) is not GlobalSchedule:
            raise TypeError("atomic Behavior analysis needs an exact GlobalSchedule")
        if type(ownership) is not DomainOwnership:
            raise TypeError("atomic Behavior analysis needs DomainOwnership")
        if set(components) != {timing.name for timing in schedule.timings}:
            raise Refused(Refusal(
                "behavior-atomic-schedule-domain-mismatch",
                "the schedule and lowered handler product name different domains",
                handlers=sorted(components),
                schedule=sorted(timing.name for timing in schedule.timings)))
        self.components = dict(sorted(components.items()))
        self.observation_contract = observation_contract
        self.source_rules = (SourceGoalRules() if observation_contract is None
                                else observation_contract.source_rules)
        self.observations = None
        if observation_contract is not None and (
                observation_contract.observation_goals or observation_contract.transaction_monitors
                or any(rule.queryable("behavior") for rule in observation_contract.latency_goals)):
            from .atomic_transactions import AtomicTransactionObservations

            self.observations = AtomicTransactionObservations(observation_contract, self.components,
                                                              steps=len(schedule.slots))
        self.domains = tuple(self.components)
        self.schedule = schedule
        self.ownership = ownership
        self.source_ownership = (
            ownership if source_ownership is None else source_ownership)
        if type(self.source_ownership) is not DomainOwnership:
            raise TypeError("source ownership is an exact DomainOwnership")
        self.state_bindings = _plain_mapping(
            {} if state_bindings is None else state_bindings,
            "atomic state bindings")
        self.source = source
        self.file = file
        self.artifact = artifact
        self.task_contract_identity = task_contract
        from ..core.memory import MemoryEnvironment, MemoryInterface

        memory_interfaces = tuple(memory_interfaces)
        if any(type(memory) is not MemoryInterface
               for memory in memory_interfaces):
            raise TypeError(
                "an atomic Behavior instance's memories are MemoryInterface records")
        if memory_environment is None:
            if memory_interfaces or memory_horizon is not None:
                raise ValueError(
                    "an atomic Behavior instance binds memory interfaces, "
                    "environment and horizon together")
        elif type(memory_environment) is not MemoryEnvironment \
                or not memory_interfaces \
                or type(memory_horizon) is not int or memory_horizon < 1:
            raise TypeError(
                "an atomic external-memory instance needs its interfaces, exact "
                "environment and positive schedule horizon")
        self.memory_interfaces = memory_interfaces
        self.memory_environment = memory_environment
        self.memory_horizon = memory_horizon
        self.external_fields = frozenset(
            variable for memory in memory_interfaces
            for variable in (self.state_bindings[memory.name].solver_variables
                             if memory.name in self.state_bindings else (memory.name,)))
        self.reset_preserved_fields = self.external_fields | frozenset(
            name for component in self.components.values()
            for name in preserved_on_reset(component.proc))
        initials = [dict(component.proc.initial)
                    for component in self.components.values()]
        self.initial = dict(initials[0] if initial is None else initial)
        if any(any(name not in self.initial or self.initial[name] != value
                   for name, value in got.items())
               for got in initials):
            raise Refused(Refusal(
                "behavior-atomic-initial-state-mismatch",
                "a domain lowering disagrees with the common reset state"))
        ownership.check_state(self.initial)

        self.state_types = {}
        for component in self.components.values():
            for name, kind in component.proc.semantics.state_types:
                known = self.state_types.get(name)
                if known is not None and known != kind:
                    raise Refused(Refusal(
                        "behavior-atomic-state-type-conflict",
                        "two domain handlers assign different SolverIR types to "
                        "one common state component",
                        state=name, first=repr(known), second=repr(kind)))
                self.state_types[name] = kind
        for name, value in self.initial.items():
            if name in self.state_types:
                continue
            inferred = types.of_value(value)
            if inferred is None:
                raise Refused(Refusal(
                    "behavior-atomic-state-type-unavailable",
                    "a common construction field has no recursive Boolean/"
                    "integer SolverIR type",
                    state=name, value_type=type(value).__name__))
            self.state_types[name] = inferred

        self.state_env = {}
        for component in self.components.values():
            for name in self.initial:
                rng = component.env.get(name)
                if isinstance(rng, ranges.Range):
                    self.state_env[name] = _join_range(
                        self.state_env.get(name), rng)
        missing = sorted(set(self.initial) - set(self.state_env))
        if missing:
            raise Refused(Refusal(
                "behavior-atomic-state-range-unavailable",
                "the common atomic state has components without finite carriers",
                states=missing))
        for component in self.components.values():
            for name, rng in self.state_env.items():
                component.env[name] = rng

        self.scalar_width, self.occurrence_carriers = \
            _shared_scalar_encoding(self.components)
        from ..symbolic.plan import FiniteCarrier, OccurrenceCarriers

        for component in self.components.values():
            for plan in (component.reset_plan, component.ready_plan):
                if plan is None:
                    continue
                self.scalar_width = max(
                    self.scalar_width,
                    *(int(item) for item in plan.widths.values()),
                    *(carrier.width
                      for carrier in plan.occurrence_carriers.values()),
                )
        # Reset/readiness plans may widen the product after ordinary occurrence
        # carriers were built.  Re-encode every unchanged semantic interval at
        # the final common width.
        self.occurrence_carriers = {
            domain: OccurrenceCarriers({
                key: FiniteCarrier(carrier.lo, carrier.hi, self.scalar_width)
                for key, carrier in component.plan.occurrence_carriers.items()
            })
            for domain, component in self.components.items()
        }
        self.ready_occurrence_carriers = {
            domain: (None if component.ready_plan is None else
                     OccurrenceCarriers({
                         key: FiniteCarrier(
                             carrier.lo, carrier.hi, self.scalar_width)
                         for key, carrier
                         in component.ready_plan.occurrence_carriers.items()
                     }))
            for domain, component in self.components.items()
        }
        self.reset_occurrence_carriers = {
            domain: (None if component.reset_plan is None else
                     OccurrenceCarriers({
                         key: FiniteCarrier(
                             carrier.lo, carrier.hi, self.scalar_width)
                         for key, carrier
                         in component.reset_plan.occurrence_carriers.items()
                     }))
            for domain, component in self.components.items()
        }

        errors = {""}
        for component in self.components.values():
            errors.update(component.proc.semantics.error_codes)
            if component.reset is not None:
                errors.update(component.reset.semantics.error_codes)
        self.error_codes = ("",) + tuple(sorted(errors - {""}))
        self._contract = atomic_row_contract(self.components)
        if frontend_catalogue is None:
            frontend_catalogue = CatalogueManifest(())
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError("the atomic frontend catalogue is a CatalogueManifest")
        self._frontend_catalogue = frontend_catalogue
        self._catalogue = frontend_catalogue if catalogue is None else catalogue
        if type(self._catalogue) is not CatalogueManifest:
            raise TypeError("the atomic complete catalogue is a CatalogueManifest")
        if self.observations is not None:
            if catalogue is None:
                from ..core.catalogue import merge_catalogues

                self._catalogue = merge_catalogues(frontend_catalogue, self.observations.catalogue)
            self._catalogue.reconcile(self.observations.catalogue, "behavior-atomic-transactions", exhaustive=False)
        self._catalogue.reconcile(
            self._frontend_catalogue, "behavior-atomic-frontend", exhaustive=False)
        self._identity = self._make_identity()
        self._domain = TraceDomain(
            len(schedule.slots), contract=self._contract,
            initial=domain_tree(self.initial),
            schema={
                "schedule": schedule.as_dict(),
                "schedule_digest": schedule.digest,
                "ownership": ownership.as_record(),
                "source_ownership": self.source_ownership.as_record(),
                "endpoints": {
                    domain: {
                        "inputs": [[endpoint, [list(row) for row in fields]]
                                   for endpoint, fields
                                   in component.input_endpoints],
                        "outputs": [[endpoint, [list(row) for row in fields]]
                                    for endpoint, fields
                                    in component.output_endpoints],
                    }
                    for domain, component in self.components.items()
                },
                "step": "accepted-domain-invocations",
            },
            frontend=ATOMIC_EXACT_FRONTEND,
            policy=ATOMIC_EXACT_POLICY,
            artifact=artifact,
            task_contract=task_contract)

    def _make_identity(self):
        from ..symbolic.plan import procedure_identity

        body = {
            "frontend": ATOMIC_EXACT_FRONTEND,
            "schedule": self.schedule.digest,
            "ownership": self.ownership.as_record(),
            "source_ownership": self.source_ownership.as_record(),
            "endpoints": {
                domain: {
                    "inputs": component.input_endpoints,
                    "outputs": component.output_endpoints,
                }
                for domain, component in self.components.items()
            },
            "components": {
                domain: {
                    "step": procedure_identity(component.proc),
                    "ready": (None if component.ready is None else
                              procedure_identity(component.ready)),
                    "reset": (None if component.reset is None else
                              procedure_identity(component.reset)),
                }
                for domain, component in self.components.items()},
            "catalogue": self._catalogue.digest(),
        }
        return hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _physical_state(self, source_state):
        """Project an isolated source state through retained state bindings."""

        source_state = _plain_mapping(source_state, "atomic source state")
        physical = {}
        for logical, _domains in self.source_ownership.owners:
            binding = self.state_bindings.get(logical)
            if binding is None:
                if logical in self.initial:
                    if logical not in source_state:
                        raise Refused(Refusal(
                            "behavior-atomic-source-state-field-missing",
                            "the source replay omits one carried state field",
                            state=logical))
                    physical[logical] = source_state[logical]
                continue
            if logical not in source_state:
                raise Refused(Refusal(
                    "behavior-atomic-source-state-field-missing",
                    "the source replay omits one bound logical state field",
                    state=logical))
            materialized = binding.solver_values(source_state[logical])
            overlap = set(physical) & set(materialized)
            if overlap:
                raise Refused(Refusal(
                    "behavior-atomic-source-state-binding-overlap",
                    "two logical source fields materialize the same physical state",
                    states=sorted(overlap)))
            physical.update(materialized)
        if set(physical) != set(self.initial):
            raise Refused(Refusal(
                "behavior-atomic-source-state-binding-incomplete",
                "the source state does not project to the complete physical product",
                missing=sorted(set(self.initial) - set(physical)),
                extra=sorted(set(physical) - set(self.initial))))
        return self._canonical_state(physical)

    def _canonical_state(self, state):
        return {name: canonical_memory(value, self.initial[name])
                if (name in self.external_fields
                    and type(self.initial[name]) is UniformArray
                    and not self.initial[name].is_uniform) else value
                for name, value in state.items()}

    def _record_states(self, states):
        return [record_tree(self._canonical_state(state)) for state in states]

    @property
    def identity(self):
        return f"{ATOMIC_EXACT_FRONTEND}:{self._identity}"

    def goals(self):
        return self._catalogue

    def frontend_catalogue_manifest(self):
        return self._frontend_catalogue

    def catalogue_manifest(self):
        return self._catalogue

    def contract(self):
        return {name: list(bounds) for name, bounds in self._contract.items()}

    def ruled_out(self):
        out = {}
        for component in self.components.values():
            out.update(component.proc.impossible)
            out.update(component.proc.discharged)
            if component.ready is not None:
                out.update(component.ready.impossible)
                out.update(component.ready.discharged)
        return out

    def _validate_rows(self, rows, *, enforce_invocations=True):
        if type(rows) not in (tuple, list) or len(rows) != len(self.schedule.slots):
            raise Refused(Refusal(
                "behavior-atomic-trace-length-mismatch",
                "an atomic trace has exactly one row per global event slot",
                expected=len(self.schedule.slots),
                got=(len(rows) if type(rows) in (tuple, list) else None)))
        names = set(self._contract)
        out = []
        for position, row in enumerate(rows):
            row = _plain_mapping(row, f"atomic row {position}")
            if set(row) != names:
                raise Refused(Refusal(
                    "behavior-atomic-row-shape-mismatch",
                    "each atomic row is a complete valuation of the fixed product",
                    position=position,
                    missing=sorted(names - set(row)),
                    extra=sorted(set(row) - names)))
            for name, value in row.items():
                lo, hi = self._contract[name]
                if type(value) is not int or not lo <= value <= hi:
                    raise Refused(Refusal(
                        "behavior-atomic-row-value-outside-contract",
                        "an atomic input value lies outside its finite declaration",
                        position=position, field=name, value=repr(value),
                        bounds=[lo, hi]))
            event = self.schedule.slots[position]
            if not enforce_invocations:
                out.append(row)
                continue
            for domain, component in self.components.items():
                invoke = row[_invoke_name(domain)]
                ordinary = domain in event.events and domain not in event.resets
                if invoke and not ordinary:
                    raise Refused(Refusal(
                        "multiclock-invocation-without-edge",
                        "a trace invokes a handler outside its active non-reset edge",
                        position=position, domain=domain, time=event.time))
                if not ordinary:
                    continue
                offered = _complete_offer(
                    component, event, position=position)
                if invoke and not offered:
                    raise Refused(Refusal(
                        "behavior-atomic-invocation-without-complete-offer",
                        "a trace invokes a handler without its complete same-slot "
                        "input endpoint product",
                        position=position, domain=domain, time=event.time))
            out.append(row)
        return out

    @staticmethod
    def _invocation_enabled(component, event, state, *, position):
        ordinary = (component.domain in event.events
                    and component.domain not in event.resets)
        if not ordinary:
            return False, ()
        offered = _complete_offer(component, event, position=position)
        ready, hits = component.ready_observation(state)
        return bool(offered and ready), hits

    def _semantic_witness(self, rows, domain, *, derive_invocations=False,
                          observed_invocations=None, observe_step=None):
        rows = self._validate_rows(
            rows, enforce_invocations=not derive_invocations)
        if observed_invocations is not None:
            if (type(observed_invocations) not in (tuple, list)
                    or len(observed_invocations) != len(rows)):
                raise TypeError(
                    "atomic observed invocations need one mapping per slot")
            observed_invocations = [
                _plain_mapping(row, "atomic observed invocations")
                for row in observed_invocations]
        state = dict(self.initial)
        states = [dict(state)]
        outputs = []
        hit_sets = []
        observation_records = [] if self.observations is not None else None
        error = ""
        observed_slots = 0
        released = set()
        framing_states = {name: [framing.initial for framing, _ in component.proc.input_framing]
                          for name, component in self.components.items()}
        for position, event in enumerate(self.schedule.slots):
            row = rows[position]
            if error:
                if derive_invocations:
                    for component_domain in self.components:
                        row[_invoke_name(component_domain)] = 0
                states.append(dict(state))
                outputs.append([])
                hit_sets.append([])
                if observation_records is not None:
                    observation_records.append(self.observations.record({}, ()))
                continue
            observed_slots = position + 1
            for component_domain in event.resets:
                framing_states[component_domain] = [
                    framing.initial for framing, _ in self.components[component_domain].proc.input_framing]
            full_reset = (
                set(event.events) == set(self.components)
                and set(event.resets) == set(self.components))
            if full_reset:
                if derive_invocations:
                    for component_domain in self.components:
                        row[_invoke_name(component_domain)] = 0
                state = reset_state(self.initial, state,
                                    preserved_fields=self.reset_preserved_fields)
                released.clear()
                states.append(dict(state))
                outputs.append([])
                hit_sets.append([])
                if observation_records is not None:
                    observation_records.append(self.observations.record({}, ()))
                continue
            effects = []
            failures = []
            emitted = []
            slot_hits = set()
            accepted_inputs = {} if observation_records is not None else None
            for component_domain, component in self.components.items():
                resetting = (component_domain in event.events
                             and component_domain in event.resets)
                ordinary = (component_domain in event.events
                            and component_domain not in event.resets)
                if ordinary:
                    invocation_enabled, ready_hits = self._invocation_enabled(
                        component, event, state, position=position)
                    slot_hits.update(ready_hits)
                    observed = (None if observed_invocations is None else
                                observed_invocations[position].get(
                                    component_domain))
                    if observed is not None and type(observed) is not bool:
                        raise TypeError(
                            "an observed atomic invocation is Boolean or absent")
                    if observed is not None and observed and not invocation_enabled:
                        raise Refused(Refusal(
                            "behavior-atomic-record-acceptance-mismatch",
                            "a raw input batch was accepted without a complete "
                            "offer and true old-state readiness observation",
                            position=position, domain=component_domain,
                            enabled=invocation_enabled, observed=observed))
                    if derive_invocations:
                        # An input transfer is observable in the concrete record.
                        # With no input endpoint there is no such bit, so the active
                        # edge and readiness predicate determine the clock-only call.
                        row[_invoke_name(component_domain)] = int(
                            invocation_enabled if observed is None else observed)
                    elif bool(row[_invoke_name(component_domain)]) \
                            and not invocation_enabled:
                        raise Refused(Refusal(
                            "behavior-atomic-invocation-readiness-mismatch",
                            "a trace invokes a handler without a complete offer and "
                            "true old-state readiness observation",
                            position=position, domain=component_domain,
                            enabled=invocation_enabled,
                            got=bool(row[_invoke_name(component_domain)])))
                elif derive_invocations:
                    row[_invoke_name(component_domain)] = 0
                if resetting:
                    proc = component.reset
                    if proc is None:
                        if component_domain in released:
                            raise Refused(Refusal(
                                "multiclock-independent-reset-unavailable",
                                "a trace reasserts a reset with no domain-local "
                                "reset transition",
                                domain=component_domain, time=event.time))
                        continue
                    values = {}
                elif ordinary and row[_invoke_name(component_domain)]:
                    proc = component.proc
                    physical = component.physical_inputs(row)
                    values = component.decode_inputs(physical)
                    if accepted_inputs is not None:
                        accepted_inputs[component_domain] = values
                    for index, (framing, projection) in enumerate(proc.input_framing):
                        markers = {public: values[logical] for public, logical in projection
                                   if public in framing.fields}
                        successor, legal = framing.step(framing_states[component_domain][index], markers)
                        if not legal:
                            raise Refused(Refusal(
                                "input-framing-violated", "atomic input violates its declared framing",
                                slot=position, domain=component_domain))
                        framing_states[component_domain][index] = successor
                else:
                    continue
                after, step_output, hits, step_error = interp.run_step(
                    proc, values, state)
                if ordinary:
                    slot_hits.update(hits)
                if step_error:
                    failures.append(step_error)
                    continue
                proposed = dict(state)
                proposed.update(after)
                if resetting:
                    proposed = reset_state(proposed, state, self.external_fields)
                domain_rows = tuple(
                    tuple(sorted(transaction.items()))
                    for transaction in step_output.transactions)
                effects.append(atomic_effect(
                    component_domain, state, proposed,
                    outputs=domain_rows,
                    hits=tuple(hits) if ordinary else ()))
                emitted.extend((component_domain, transaction)
                               for transaction in step_output.transactions)
            if len(failures) > 1:
                raise Refused(Refusal(
                    "behavior-atomic-error-collision",
                    "two coincident handlers fail but the terminal state carries "
                    "only one error code",
                    time=event.time, errors=sorted(failures)))
            if failures:
                error = failures[0]
                states.append(dict(state))
                outputs.append([])
                hit_sets.append(tuple(sorted(slot_hits)))
                if observation_records is not None:
                    observation_records.append(self.observations.record(accepted_inputs, ()))
                continue
            effects.sort(key=lambda effect: effect.domain)
            state = atomic_successor(state, effects, self.ownership, event)
            released.update(
                domain_name for domain_name in event.events
                if domain_name not in event.resets)
            states.append(dict(state))
            outputs.append(_project_output_batch(self.components, emitted))
            hit_sets.append(tuple(sorted(slot_hits)))
            if observation_records is not None:
                observation_records.append(self.observations.record(accepted_inputs, outputs[-1]))
        if observation_records is not None:
            hit_sets = self.observations.observe(observation_records, hit_sets)
        if observe_step is not None:
            for position in range(observed_slots):
                observe_step(0, position, states[position],
                             frozenset(hit_sets[position]), outputs[position])
        return AuditedWitness(
            domain=domain, states=self._record_states(states), inputs=rows, outputs=outputs,
            hit_sets=hit_sets, terminal_error=error)

    def rows_from_record(self, rows, observed_invocations):
        """Bind observable transfers and derive clock-only invocations.

        An observed input transfer must be enabled by the schedule and old-state
        readiness.  Their converse need not hold because an input queue can be empty.
        A clock-only handler has no presence bit, so its invocation is derived.
        """

        witness = self._semantic_witness(
            rows, self._domain.digest(), derive_invocations=True,
            observed_invocations=observed_invocations)
        return [dict(row) for row in witness.inputs]

    def replay_witness(self, rows, domain):
        try:
            witness = self._semantic_witness(rows, domain)
            step_outputs = [endpoint_batch_output(batch)
                            for batch in witness.outputs]
            replayed = Replay(
                set().union(*(set(row) for row in witness.hit_sets)),
                step_outputs, error=witness.terminal_error,
                state=witness.final_state, steps=len(rows))
            return replayed, witness
        except Refused as exc:
            return Replay(
                (), broken=f"{exc.refusal.kind}: {exc.refusal.why}",
                steps=(len(rows) if type(rows) in (tuple, list) else None)), None
        except Exception as exc:  # noqa: BLE001 - Replay's typed broken branch
            return Replay(
                (), broken=f"{type(exc).__name__}: {exc}",
                steps=(len(rows) if type(rows) in (tuple, list) else None)), None

    def replay(self, rows):
        return self.replay_witness(rows, self._domain.digest())[0]

    def source_audit(self, traces, *, observe_step=None):
        """Check native state/output/hits and optionally publish audited slots.

        Like the ordinary Behavior audit, the callback receives a pre-state and
        that step's hits/outputs. Here a step is the actual global slot, including
        reset and silent slots. The failed slot is retained but its absorbed tail
        is not published. Callers must discard feedback if the audit disagrees.
        """
        if not traces or self.source is None:
            return AuditCheck.refused(
                "behavior-atomic-source-audit-unavailable",
                "the atomic source audit needs frozen source and a finite trace")
        from .atomic_failure import source_failure_evidence
        from .budgets import source_audit_limits

        findings = []
        for trace_index, rows in enumerate(traces):
            try:
                rows = self._validate_rows(rows)
                expected = self._semantic_witness(rows, self._domain.digest())
                ready_mismatch = False
                with IsolatedAtomicMachine(
                        self.source, self.source_ownership, self.domains,
                        file=self.file,
                        limits=source_audit_limits(),
                        memory_interfaces=self.memory_interfaces,
                        memory_environment=self.memory_environment,
                        memory_horizon=self.memory_horizon,
                        source_rules=self.source_rules) as machine:
                    states = [self._physical_state(machine.state)]
                    outputs = []
                    hit_sets = []
                    terminal_error = ""
                    observed_slots = 0
                    observation_records = [] if self.observations is not None else None
                    for position, event in enumerate(self.schedule.slots):
                        if terminal_error:
                            states.append(states[-1])
                            outputs.append([])
                            hit_sets.append([])
                            if observation_records is not None:
                                observation_records.append(self.observations.record({}, ()))
                            continue
                        observed_slots = position + 1
                        source_ready_hits = set()
                        for component in self.components.values():
                            ordinary = (
                                component.domain in event.events
                                and component.domain not in event.resets)
                            if not ordinary:
                                continue
                            offered = _complete_offer(
                                component, event, position=position)
                            source_ready, ready_hits = (
                                machine.ready_observation(component.domain))
                            source_ready_hits.update(ready_hits)
                            source_enabled = bool(offered and source_ready)
                            invoked = bool(rows[position][
                                _invoke_name(component.domain)])
                            disagrees = (
                                invoked != source_enabled
                                if not component.boundary.inputs
                                else invoked and not source_enabled
                            )
                            if disagrees:
                                findings.append(
                                    f"atomic trace {trace_index} domain "
                                    f"{component.domain!r} has different source "
                                    f"and IR readiness at slot {position}")
                                ready_mismatch = True
                                break
                        if ready_mismatch:
                            break
                        invocations = tuple(
                            DomainInvocation(
                                domain,
                                component.source_args(
                                    component.physical_inputs(rows[position])))
                            for domain, component in self.components.items()
                            if rows[position][_invoke_name(domain)])
                        try:
                            transition = machine.step(event, invocations)
                        except Refused as exc:
                            failure = source_failure_evidence(exc, self._frontend_catalogue)
                            if failure is None:
                                raise
                            if len(failure.failures) != 1:
                                raise Refused(Refusal("behavior-atomic-error-collision",
                                    "the atomic exact error carrier cannot represent coincident failures")) from None
                            fault = failure.failures[0]
                            if fault.operation != "handler" or fault.domain not in {call.domain for call in invocations}:
                                raise Refused(Refusal("behavior-atomic-source-failure-occurrence",
                                    "the attributed failure is not from an invoked handler")) from None
                            terminal_error = encoder.EXCEPTION_CODES.get(fault.error)
                            # Python uses ValueError for both negative shifts and
                            # other partial operations. The independently attributed
                            # source goal disambiguates them without an IR guess.
                            if (fault.error == "ValueError"
                                    and self._frontend_catalogue[fault.goal].family == "negativeshift"):
                                terminal_error = "NegativeShift"
                            if terminal_error is None:
                                raise Refused(Refusal("behavior-atomic-source-failure-code",
                                    "the source exception has no corresponding exact error code",
                                    error=fault.error)) from None
                            emitted, source_hits = [], failure.hits
                        else:
                            emitted = [(domain, dict(row)) for domain, domain_rows in transition.outputs
                                       for row in domain_rows]
                            source_hits = transition.hits
                        states.append(self._physical_state(machine.state))
                        outputs.append(_project_output_batch(
                            self.components, emitted))
                        hit_sets.append(sorted(
                            source_ready_hits | set(source_hits)))
                        if observation_records is not None:
                            inputs = {call.domain: dict(zip(self.components[call.domain].logical_inputs,
                                                            call.args, strict=True)) for call in invocations}
                            observation_records.append(self.observations.record(inputs, outputs[-1]))
                if ready_mismatch:
                    continue
                if observation_records is not None:
                    hit_sets = self.observations.observe(observation_records, hit_sets)
                actual = AuditedWitness(
                    domain=self._domain.digest(), states=self._record_states(states), inputs=rows,
                    outputs=outputs, hit_sets=hit_sets, terminal_error=terminal_error)
                if actual != expected:
                    findings.append(
                        f"atomic trace {trace_index} differs between source and IR")
                elif observe_step is not None:
                    for position in range(observed_slots):
                        observe_step(
                            trace_index, position, states[position],
                            frozenset(actual.hit_sets[position]), actual.outputs[position])
            except Refused as exc:
                return AuditCheck.refused(exc.refusal.kind, exc.refusal.why)
            except Exception as exc:  # noqa: BLE001 - typed audit branch
                return AuditCheck.refused(
                    "behavior-atomic-source-audit-failed",
                    f"the isolated atomic source audit failed: "
                    f"{type(exc).__name__}: {exc}")
        return AuditCheck.inconsistent(findings) if findings else AuditCheck.agreed()


class AtomicBehaviorInstance(_AtomicContext):
    """The complete fixed-schedule product, including its totality obligation."""

    def __init__(self, *args, **kwargs):
        from ..analysis_budget import check_analysis, remaining_query_ms

        super().__init__(*args, **kwargs)
        self._formula = _AtomicFormula(self)
        totality = z3.Solver()
        totality.add(self._formula.constraint, self._formula.invalid)
        remaining = remaining_query_ms(None)
        if remaining is not None:
            totality.set(timeout=remaining)
        answer = totality.check()
        check_analysis()
        if answer == z3.sat:
            raise Refused(Refusal(
                "behavior-atomic-transition-not-total",
                "some admitted input causes coincident writes or failures for "
                "which the declared atomic policy has no unique result",
                witness=self._formula.decode_rows(totality.model())))
        if answer != z3.unsat:
            raise Refused(Refusal(
                "behavior-atomic-totality-unresolved",
                "the exact analyser could not establish that every admitted "
                "atomic input has a unique transition",
                reason=str(totality.reason_unknown() or "unknown")))

    def bind(self):
        return BoundedInstance(self, self._domain)

    def analysis_identity(self):
        return self.artifact or self.identity

    def evidence_identity(self, steps):
        if steps != len(self.schedule.slots):
            raise ValueError("atomic evidence is bound to its fixed schedule")
        return {
            "representation": self.identity,
            "encoding": f"{ATOMIC_EXACT_POLICY}:{self.schedule.digest}",
        }

    def well_formed(self):
        return []

    def unaccounted_goals(self):
        return []

    def budgets(self):
        from . import budgets
        return budgets.as_dict()

    def audit_plan(self):
        return AuditPlan.all()

    def trusted_assumptions(self):
        return (
            TrustedAssumption(
                "source-to-ir-fidelity",
                "each domain lowering preserves its source handler on every "
                "admitted input",
                scope="frontend-semantics"),
            TrustedAssumption(
                "atomic-schedule-binding",
                "the frozen timestamped schedule is the environment used by the "
                "concrete run and the bounded formula",
                scope="trace-domain"),
            TrustedAssumption(
                "solver",
                "the solver answers the fixed atomic bounded formula correctly",
                scope="bounded-solver"),
        )

    def task_audit(self, task):
        contract = getattr(getattr(task, "contract", None), "digest", None)
        if self.task_contract_identity is not None \
                and contract != self.task_contract_identity:
            return AuditCheck.inconsistent(
                "the atomic Behavior instance and task name different contracts")
        return AuditCheck.agreed()

    def _symbolic_witness(self, rows, domain):
        rows = self._validate_rows(rows)
        solver = z3.Solver()
        solver.add(self._formula.constraint)
        solver.add(self._formula.constrain_rows(rows))
        model = _audit_model(solver)
        return (None if model is None else
                self._formula.decode_witness(model, self, rows, domain))

    def audit(self, traces):
        if not traces:
            return AuditCheck.refused(
                "behavior-atomic-step-audit-no-traces",
                "the atomic step audit needs at least one finite trace")
        findings = []
        for index, rows in enumerate(traces):
            try:
                semantic = self._semantic_witness(rows, self._domain.digest())
                symbolic = self._symbolic_witness(rows, self._domain.digest())
            except Refused as exc:
                return AuditCheck.refused(exc.refusal.kind, exc.refusal.why)
            if symbolic is None:
                findings.append(
                    f"the atomic formula admits no run for trace {index}, "
                    "although the IR replay produces a validated trace")
                continue
            if semantic != symbolic:
                findings.append(
                    f"atomic trace {index} differs between IR replay and its formula")
        return AuditCheck.inconsistent(findings) if findings else AuditCheck.agreed()

    def audit_request(self, rows, domain):
        replayed, witness = self.replay_witness(rows, domain)
        if replayed.broken or witness is None:
            return AuditCheck.refused(
                "behavior-atomic-witness-replay-failed",
                replayed.broken or "no complete atomic witness was produced")
        for check in (self.audit((rows,)), self.source_audit((rows,))):
            if check.status is not AuditCheckStatus.AGREED:
                return check
        return witness

    def feasible(self, steps, timeout_ms=None):
        if steps != len(self.schedule.slots):
            raise ValueError("atomic feasibility is bound to one fixed schedule")
        from ..symbolic.exact_solve import z3_check_qfbv
        from . import budgets as budgets_mod

        checked = z3_check_qfbv(
            (self._formula.constraint,), timeout_ms,
            budgets_mod.SOLVER_MEMORY_MB)
        if checked.answer is SolverAnswer.SAT:
            return Feasible.YES, self._formula.decode_rows(checked.model)
        if checked.answer is SolverAnswer.UNSAT:
            return Feasible.NO, "the fixed atomic trace domain is empty"
        return Feasible.UNRESOLVED, checked.reason

    def query(self, goals, steps, timeout_ms=None, *, telemetry_scope=None,
              artifact_ms=None, artifact_started=None,
              memory_mb=None, workers=None):
        if steps != len(self.schedule.slots):
            raise ValueError("atomic goal queries are bound to one fixed schedule")
        if workers is not None and (type(workers) is not int or workers < 1):
            raise ValueError("atomic Behavior query workers must be a positive integer")
        requested = tuple(goals)
        unknown = sorted(set(requested) - set(self._catalogue))
        if unknown:
            raise Refused(Refusal(
                "behavior-atomic-goal-outside-catalogue",
                "an atomic query names goals outside the frozen manifest",
                goals=unknown))
        unsupported = sorted(
            goal for goal in requested if not self._formula.supports(goal))
        if unsupported:
            raise Refused(Refusal(
                "behavior-atomic-monitor-state-unavailable",
                "the handler product does not encode these trace-monitor goals",
                goals=unsupported))
        if telemetry_scope is not None \
                and type(telemetry_scope) is not QueryTelemetryScope:
            raise TypeError("atomic Behavior telemetry uses QueryTelemetryScope")
        from . import budgets as budgets_mod

        artifact_memory_mb = (budgets_mod.ARTIFACT_MEMORY_MB
                              if memory_mb is None else memory_mb)
        query_memory_mb = resources.solver_memory_mb(artifact_memory_mb)
        spent = budgets_mod.Spent(
            ms=artifact_ms, max_queries=budgets_mod.ARTIFACT_QUERIES,
            started=artifact_started)
        def telemetry(batch, outcome, *, settled=(), timings=None):
            if telemetry_scope is None:
                return None
            try:
                return telemetry_scope.fragment(
                    batch, shape=outcome.shape,
                    timings=outcome.timings if timings is None else timings,
                ).finalize(outcome=outcome.answer.value,
                           goals_settled=settled)
            except Exception:  # noqa: BLE001 - measurement cannot change an answer
                return None
        def solve(batch):
            exhausted = spent.exhausted()
            if exhausted:
                return QueryCall(
                    batch, SolverAnswer.UNKNOWN, reason=exhausted), ()
            per_query_ms = (budgets_mod.QUERY_MS
                            if timeout_ms is None else timeout_ms)
            effective_timeout_ms = spent.for_query(per_query_ms)
            spent.charge()
            conditions = {
                goal: self._formula.goal_condition(goal) for goal in batch
            }
            outcome = solve_exact(
                self._formula.formula,
                z3.Or(*conditions.values()),
                effective_timeout_ms,
                memory_mb=query_memory_mb)
            if outcome.answer is SolverAnswer.SAT:
                decode_started = time.monotonic_ns()
                model = outcome.model
                rows = self._formula.decode_rows(model)
                hits = tuple(
                    goal for goal, condition in conditions.items()
                    if z3.is_true(model.eval(condition, model_completion=True)))
                timings = outcome.timings.merge(StageTimings({
                    "decode_ns": time.monotonic_ns() - decode_started}))
                return QueryCall(
                    batch, SolverAnswer.SAT, witness=rows,
                    telemetry=telemetry(batch, outcome, timings=timings)), hits
            if outcome.answer is SolverAnswer.UNSAT:
                return QueryCall(
                    batch, SolverAnswer.UNSAT,
                    telemetry=telemetry(batch, outcome, settled=batch)), ()
            return QueryCall(
                batch, SolverAnswer.UNKNOWN,
                reason=outcome.reason,
                telemetry=telemetry(
                    batch, outcome,
                    settled=batch if len(batch) == 1 else ())), ()

        return decide_setwise(requested, solve)
