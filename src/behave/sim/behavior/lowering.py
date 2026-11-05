"""Translate admitted Python Behavior into typed SolverIR.

Source planning supplies lexical bindings, recursive value contracts and finite
capacities. Lowering preserves evaluation order, emits guarded partial operations,
and accounts for every declared goal as produced or discharged. Unsupported
operations raise a site-specific ``LoweringError``.

``GraphLowerer`` supplies production control flow; ``_Lowerer`` also serves the
secondary term oracle. Both share the return/value builders in ``lower_returns``.
"""

from __future__ import annotations

import ast
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field

from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

from ..core.catalogue import CatalogueManifest
from . import (
    flow_facts,
    goals,
    lower_bytes,
    lower_checks,
    lower_context,
    lower_mappings,
    lower_ordered_scalars,
    lower_prepare,
    lower_returns,
    lower_state,
    lower_static_refs,
    source_intrinsics,
)
from ..symbolic import ranges
from ..symbolic import ir, primitives
from ..symbolic import typecheck as value_types
from .budgets import (
    SEMANTIC_IR_NODES,
    helper_activation_exceeds,
)
from .declared import safety_goal_id
from .extract import (
    LoweringError,
    _approved_primitives,
    _def_time_pure,
    _may_break,
    _may_continue,
    _target_name,
    _walk_function_scope,
    module_bound_names,
    source_model,
)
from .goals import goal_id
from .lower_mappings import MappingBuilder
from .lower_ordered_scalars import OrderedScalar, infinity_literal
from .lower_returns import (
    OptionSequence,
    _batch_form_code,
    _BatchBuilder,
    _BatchValue,
    _NamedReturn,
    _OptionBuilder,
    _PositionalBuilder,
    _PositionalValue,
    _RecordBuilder,
    _StructuredValue,
    _ValueReturn,
)
from .lower_static_refs import (
    _STATIC_SCALAR,
    _StaticIssue,
    _StaticRef,
    _TupleKey,
)
from ..symbolic.procedure_contract import OutputBoundaryError
from .output_boundary import bind_output_boundary
from ..symbolic.procedure_contract import ProcedureContractError
from ..symbolic.procedure_contract import OutputContractError
from .signature import output_contract_evidence
from ..symbolic.validation import bind_procedure_contract
from .source_batches import (
    BatchContractError,
    BatchForm,
    BatchPlan,
    batch_value_contract,
    plan_batch_expression,
    record_batch_payload,
)
from .source_bytes import BYTE_CARRIERS, is_from_bytes
from .source_calls import (
    CallBindingError,
    bind_helper_arguments,
    has_plain_positional_arity,
    is_append_call,
    is_extend_call,
    is_plain_append_call,
    is_plain_extend_call,
    is_plain_pop_call,
    is_pop_call,
    non_rebound_parameters,
    record_copy_source,
    record_items,
    record_key,
    record_update_items,
    resolve_helper,
    sequence_delete_target,
    sequence_method_target,
)
from ..symbolic.value_contract import (
    optional_sequence,
    INCOMPATIBLE,
    NONE,
    SCALAR,
    UNKNOWN,
    ValueContract,
    contract_contains,
    contract_is_deeply_immutable,
    contract_of_collection_lookup,
    contract_of_value,
    fixed_positional_contracts,
    join_contracts,
    sequence_elements_are_deeply_immutable,
)
from .source_options import OptionValue, none_test
from .source_records import (
    RecordPlanError,
    RecordValue,
    record_from_leaves,
    record_leaves,
    record_value,
)
from .source_records import leaf_contracts as record_leaf_contracts
from .source_records import literal_entries as record_literal_entries
from .source_sequences import (
    static_iterable,
    static_value,
)
from .source_syntax import annotation_target_expressions, comprehension_tail
from .source_values import (
    SourceValueBudget,
    SourceValueBudgetExceeded,
    _record_items_receiver,
    contract_of_expression,
    contracts_for_target,
    static_truth,
)
from ..symbolic.value_layout import layout_of_value_type
from .while_plan import (
    WhilePlanError,
    loop_container_writes,
    loop_preserves_iterable_values,
    loop_written_scalars,
    ranking_span,
)
from .while_plan import analyse as analyse_while
from .while_plan import analyse_exit_ranking as analyse_while_exit_ranking
from .while_plan import analyse_ranking as analyse_while_ranking

__all__ = ["LoweringError", "StatePlan", "lower_expression", "lower_procedure",
           "lower_with_state_ranges", "plan_state"]

#: How a statement can finish. ``NORMAL`` is falling through to whatever is
#: written next, ``RETURNED`` is the procedure being done, ``LEFT`` is the
#: enclosing loop being broken, and ``CONTINUED`` skips the rest of this
#: iteration.
#:
#: A failure is deliberately not one of these. It ends the step rather than the
#: block, and both readings carry that themselves: the interpreter stops and
#: the encoder conditions everything after it on the step having completed. A
#: block does not have to guard against it, so modelling it here would be a
#: fourth case nothing consumes.
NORMAL, RETURNED, LEFT, CONTINUED = (
    "normal", "returned", "left", "continued")


def _target_spellings(target):
    """Names bound by one comprehension target."""
    return {
        node.id for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _expression_reads_binding(node, spelling, bound=frozenset()):
    """Whether evaluating ``node`` reads an outer lexical binding."""
    if isinstance(node, ast.Name):
        return (isinstance(node.ctx, ast.Load)
                and node.id == spelling and node.id not in bound)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp,
                         ast.DictComp)):
        local = set(bound)
        for generator in node.generators:
            if _expression_reads_binding(generator.iter, spelling, local):
                return True
            local.update(_target_spellings(generator.target))
            if any(_expression_reads_binding(condition, spelling, local)
                   for condition in generator.ifs):
                return True
        values = ((node.key, node.value)
                  if isinstance(node, ast.DictComp) else (node.elt,))
        return any(_expression_reads_binding(value, spelling, local)
                   for value in values)
    if isinstance(node, ast.Lambda):
        defaults = (*node.args.defaults,
                    *(value for value in node.args.kw_defaults
                      if value is not None))
        if any(_expression_reads_binding(value, spelling, bound)
               for value in defaults):
            return True
        arguments = [*node.args.posonlyargs, *node.args.args,
                     *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        parameters = {argument.arg for argument in arguments}
        return _expression_reads_binding(
            node.body, spelling, set(bound) | parameters)
    return any(_expression_reads_binding(child, spelling, bound)
               for child in ast.iter_child_nodes(node))


@dataclass(frozen=True)
class _IteratorBinding:
    """The object held by an iterator after its source name is replaced.

    Before replacement, reads use the live binding.  At its first replacement
    we save that object's value and length, not the replacement's value.  The
    shared source alias analysis rules out later writes through another name
    to an object retained only by this value image.
    """

    source: str
    saved: object
    detached: str
    lengths: tuple

    def read(self, live, saved):
        return ir.Op("ite", ir.Var(self.detached), saved, live)


@dataclass(frozen=True)
class _IterationPlan:
    """One finite source iterable lowered to a semantic loop domain."""

    count: object
    bound: int
    value: object
    element: object
    available: object = None
    binding: object = None

    def activation(self, count):
        """Whether the first next() can enter the source loop body."""
        return (count if self.available is None else
                ir.Op("ite", self.available(ir.Lit(0)), count, ir.Lit(0)))


def _boundary_record_contains(actual, planned):
    """Whether one lowered transaction refines its source boundary plan.

    Source planning owns the fixed key set.  A field whose contract is
    ``UNKNOWN`` is deliberately deferred to lowering (not guessed scalar),
    which is required for a value read from state first created in this step.
    Every known field remains an ordinary recursive refinement check.
    """
    if not (actual.is_record and planned.is_record) \
            or set(actual.field_contracts) != set(planned.field_contracts):
        return False
    return all(
        expected is UNKNOWN or contract_contains(
            actual.field_contracts[name], expected)
        for name, expected in planned.field_contracts.items()
    )


def _fill_shape(shape, value):
    """Fill a recursive SolverIR array shape with one scalar expression."""
    shape = tuple(shape)
    if not shape:
        return value
    child = _fill_shape(shape[1:], value)
    return ir.Op("arrayfill", child, ir.Lit(shape[0]))


def _fixed_expression_type(expr, known=None):
    """A result type fixed by an expression's own IR constructor.

    A caller may supply the partial type facts of surrounding variables; an
    absent or conflicting fact returns ``None``.  This is used only to carry a
    proved source scalar type across compiler-created bindings.  The completed
    procedure type pass still checks every operand and definition.
    """

    # Lowered arithmetic can be thousands of nodes deep even though the
    # source itself contains a small finite loop.  Type discovery is a graph
    # walk, not host-language recursion: Python's recursion limit is unrelated
    # to the admitted IR and must not turn a valid model into a census crash.
    resolved = {}
    active = set()
    stack = [(expr, False)]

    def dependencies(node):
        if isinstance(node, ir.Sel):
            return (node.array,)
        if not isinstance(node, ir.Op):
            return ()
        try:
            spec = primitives.primitive(node.op)
        except primitives.PrimitiveRegistryError:
            return ()
        if spec.signature.result in (primitives.BOOLEAN, primitives.INTEGER):
            return ()
        return node.args

    while stack:
        node, finishing = stack.pop()
        identity = id(node)
        if finishing:
            active.discard(identity)
            if identity in resolved:
                continue
            if isinstance(node, ir.Sel):
                array = resolved.get(id(node.array))
                resolved[identity] = (
                    array.elem if isinstance(array, value_types.Array)
                    else None)
                continue
            if not isinstance(node, ir.Op):
                resolved[identity] = None
                continue
            try:
                spec = primitives.primitive(node.op)
            except primitives.PrimitiveRegistryError:
                resolved[identity] = None
                continue
            result = spec.signature.result
            if result is primitives.BOOLEAN:
                resolved[identity] = value_types.BOOLEAN
                continue
            if result is primitives.INTEGER:
                resolved[identity] = value_types.INTEGER
                continue
            terms = spec.signature.terms_for(len(node.args))
            args = tuple(resolved.get(id(arg)) for arg in node.args)
            if terms is None:
                resolved[identity] = None
                continue
            same = None
            array_argument = None
            valid = True
            for term, got in zip(terms, args):
                concrete = {
                    primitives.BOOLEAN: value_types.BOOLEAN,
                    primitives.INTEGER: value_types.INTEGER,
                    primitives.WIDTH: value_types.INTEGER,
                    primitives.SHIFT: value_types.INTEGER,
                }.get(term)
                if concrete is not None:
                    valid = valid and (got is None or got == concrete)
                    continue
                if term is primitives.SCALAR:
                    valid = valid and got in (
                        None, value_types.BOOLEAN, value_types.INTEGER)
                    continue
                if got is None:
                    valid = False
                    continue
                if term is primitives.ARRAY:
                    if not isinstance(got, value_types.Array):
                        valid = False
                        continue
                    array_argument = got
                    got = got.elem
                elif term is primitives.SAME_SCALAR and got not in (
                        value_types.BOOLEAN, value_types.INTEGER):
                    valid = False
                    continue
                if same is None:
                    same = got
                elif same != got:
                    valid = False
            if not valid:
                resolved[identity] = None
            elif result in (primitives.SAME, primitives.SAME_SCALAR):
                resolved[identity] = same
            elif spec.signature.variadic:
                resolved[identity] = value_types.Array(
                    len(args), value_types.INTEGER if same is None else same)
            else:
                resolved[identity] = array_argument
            continue

        if identity in resolved:
            continue
        if isinstance(node, ir.Lit):
            resolved[identity] = value_types.of_value(node.value)
            continue
        if isinstance(node, ir.Var):
            resolved[identity] = None if known is None else known(node.name)
            continue
        if identity in active:
            # Compiler-built expressions are acyclic.  Do not loop forever if
            # a malformed internal caller violates that invariant; the full
            # representation validator will name the structural fault.
            resolved[identity] = None
            continue
        active.add(identity)
        stack.append((node, True))
        stack.extend((child, False)
                     for child in reversed(dependencies(node)))
    return resolved.get(id(expr))


def _intrinsic_source_scalar_type(node):
    """Scalar type fixed by syntax alone, without a lexical environment."""
    if isinstance(node, ast.Constant):
        if type(node.value) is bool:
            return value_types.BOOLEAN
        if type(node.value) is int:
            return value_types.INTEGER
        return None
    if isinstance(node, ast.Compare):
        return value_types.BOOLEAN
    if isinstance(node, ast.UnaryOp):
        return (value_types.BOOLEAN if isinstance(node.op, ast.Not)
                else value_types.INTEGER)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            left = _intrinsic_source_scalar_type(node.left)
            right = _intrinsic_source_scalar_type(node.right)
            if left == right == value_types.BOOLEAN:
                return value_types.BOOLEAN
            if value_types.INTEGER in {left, right}:
                return value_types.INTEGER
            return None
        return value_types.INTEGER
    if isinstance(node, ast.BoolOp):
        kinds = {_intrinsic_source_scalar_type(value)
                 for value in node.values}
        return kinds.pop() if len(kinds) == 1 else None
    if isinstance(node, ast.IfExp):
        left = _intrinsic_source_scalar_type(node.body)
        right = _intrinsic_source_scalar_type(node.orelse)
        return left if left is not None and left == right else None
    return None


def _needs_positional_return(fn, arity):
    """Whether direct unpacking must preserve heterogeneous field types.

    Homogeneous helper tuples retain the existing SolverIR-array path, which
    supports indexing, nesting and forwarding through later calls.  A tuple
    whose own syntax proves distinct scalar component types cannot enter that
    array and therefore uses the short-lived positional product instead.
    """
    for node in _walk_function_scope(fn):
        if not isinstance(node, ast.Return) \
                or not isinstance(node.value, (ast.Tuple, ast.List)) \
                or len(node.value.elts) != arity:
            continue
        kinds = {_intrinsic_source_scalar_type(item)
                 for item in node.value.elts}
        kinds.discard(None)
        if len(kinds) > 1:
            return True
    return False


def _loop_ways(body_ways):
    """How a loop can finish, given how its body can.

    A loop always finishes normally, since running out of iterations is a way
    to end. A return inside it leaves the procedure and so escapes the loop; a
    break and continue are consumed by the loop, while a return escapes it.
    """
    return {NORMAL} | (body_ways & {RETURNED})


def _loop_else_ways(body_ways, else_ways, *, breaks, exhausts):
    """Ways escaping a loop whose normal exhaustion executes ``else``.

    A break consumes the loop and reaches its successor normally.  Exhaustion
    does so only when it is reachable and the else body itself can complete;
    returns from either body escape the complete procedure.  In particular, a
    loop with no break whose body or else always returns has no fictitious
    normal path to the statement written after it. Break/continue in else
    escape to the enclosing loop, rather than being consumed by this loop.
    """
    out = body_ways & {RETURNED}
    if exhausts:
        out |= else_ways - {NORMAL}
    if breaks or (exhausts and NORMAL in else_ways):
        out.add(NORMAL)
    return out


class _BudgetExceeded(Exception):
    """Raised inside lowering when the step's term outgrows its budget."""


class _UnpackingTargetError(Exception):
    """One fixed unpacking shape the construct boundary rejects."""

    def __init__(self, node, what):
        super().__init__(what)
        self.node = node
        self.what = what


@dataclass(slots=True, eq=False)
class _Activation:
    """Lexical working state for one source call, shared by both lowerers.

    The parent retains the caller's exact objects while a callee is active.
    Representation declarations, flow facts and the aggregate lowering budget
    belong to the procedure, not to this frame. A short-circuit expression may
    open a smaller hoisting region inside the same activation.
    """

    scope: ast.AST | None = None
    summary: object = None
    parent: _Activation | None = field(default=None, repr=False)
    renames: dict = field(default_factory=dict)
    defined: set = field(default_factory=set)
    substitutions: dict = field(default_factory=dict)
    substitution_contracts: dict = field(default_factory=dict)
    destination: object = None
    return_facts: list = field(default_factory=list)
    hoisted: list = field(default_factory=list)

    def frames(self):
        """Active calls, nearest first; lexical lookup selects their owners."""
        frame = self
        while frame is not None:
            yield frame
            frame = frame.parent

    @property
    def root(self):
        frame = self
        while frame.parent is not None:
            frame = frame.parent
        return frame


@dataclass(slots=True, eq=False)
class _LoopFrame:
    """One retained loop's controls, source indices and outgoing flow facts.

    Term lowering uses Boolean running/continuing controls; graph lowering
    uses an exit label and the owning return destination. They share only
    lifetime and fact ownership, not their control-flow representation.
    """

    running: str | None
    continuing: str | None = None
    exit_label: str | None = None
    return_destination: object = None
    indices: tuple = ()
    parent: _LoopFrame | None = field(default=None, repr=False)
    breaks: list = field(default_factory=list)
    continues: list = field(default_factory=list)

    def contains_index(self, name):
        loop = self
        while loop is not None:
            if name in loop.indices:
                return True
            loop = loop.parent
        return False


class _Lowerer:
    """Translate expressions and statements while preserving evaluation order.

    Expression rules emit prerequisite commands through ``before``. ``mark`` and
    ``hoisted_since`` delimit them; ``_flush`` attaches them to their consumer.
    The term lowerer queues these commands, while ``GraphLowerer`` places them
    directly in the current block. ``activation`` owns lexical call state and
    ``flow`` owns branch-local facts. ``storage`` owns procedure-wide allocation
    and representation invariants. Construction budgets remain shared across
    paths and activations.
    """

    def __init__(self, sites, primitives=(), functions=None,
                 constants=None, env=None, arrays=None, tables=None,
                 appends=None, named=None, fixed=None, file="behavior.py",
                 lib_named=None, sequence_plan=None,
                 source_value_budget=None, reserved_names=()):
        # Two sites can share a position: ``if a or b`` puts the decision and
        # its first operand at the same column. They are different goals, so
        # the caller says which family it is asking for.  File ownership is
        # equally semantic: approved library routines are inlined into this
        # procedure, but their coincident line/column positions are not sites
        # in the model source's structural-coverage catalogue.
        self.sites = {
            (s.file, s.line, s.col, s.family): s for s in sites
        }
        self.operation_goals = {}
        self.read_goals = {}
        # No table of goals this file says are impossible. Every declared goal
        # owes a `mark`, and a goal with neither a mark nor a rule is a rule this
        # file is missing, which `_check_producers` refuses. The table existed so
        # that a construct could discharge the obligation by asserting instead of
        # producing, and nothing asserts any more.
        # Where each goal this file creates lives. Recorded by whatever creates
        # it, so that nothing downstream has to take an identifier apart to
        # find out: an identifier is a name, and the only things promised about
        # it are that it is stable and unique.
        self.provenance = {}
        #: Safety goals the declaration owes and this lowering does not produce, with
        #: the reason the operation is total at that site. Written by whatever made the
        #: decision, at the moment it made it, so that the audit compares a declaration
        #: against a producer plus a stated reason rather than against a producer alone.
        self.discharged = {}
        #: The partial operations this lowering actually reached, by node identity. A
        #: site inside a loop that unrolls zero times, or in a helper nothing calls, is
        #: an operation no bounded execution performs: it owes no goal, and it is not a
        #: rule that went missing. The two are told apart by this and by nothing else.
        self.reached = set()
        #: Names that currently hold the same array, in groups. See the block above
        #: `_rebind`: a binding records, and a write through a shared name is refused.
        self.shared = {}
        # Source call edges whose formals use the caller's array storage.
        # Alias validation must distinguish these from copied arguments.
        self.borrowed_arguments = {}
        self._iterator_bindings = []
        #: The helper definitions actually inlined, which is what the declaration is
        #: made over. A helper the model defines and never calls is not lowered.
        self.inlined = []
        #: Helpers entered only through source paths proved unreachable in this
        #: bounded plan.  Their public goals remain in the source-stable
        #: catalogue and are represented by false terms or discharged safety
        #: conditions rather than silently disappearing with the path.
        self.pruned_functions = []
        # Inlining reuses the same source AST.  A helper with an unreachable
        # suffix therefore asks for the same explicit false-goal terms at every
        # call site.  Walking that suffix, its reachable helpers, and the complete
        # site catalogue again is analysis duplication rather than call-site
        # semantics: the public goal identities are source-occurrence identities.
        # Keep the immutable command per exact tuple of source roots for this one
        # lowering invocation.  The first construction also records every pruned
        # function, so a cache hit has no missing side effect.
        self._false_goal_cache = {}
        self.file = file
        self.primitives = set(primitives)
        self.functions = dict(functions or {})
        self.constants = dict(constants or {})
        # Fixed integers a plain name stands for, kept apart from the model's
        # parameters so that only a name the source never assigns folds. The
        # model's module constants and the library's are separate owners
        # (review 51): a name defined in both folds to the model's value in
        # model code and the library's inside an inlined library routine.
        self.named = dict(named or {})
        self.lib_named = dict(lib_named or {})
        #: The same for a name the class body binds and no method assigns, read
        #: through `self`. Kept apart from `named` because the two are reached by
        #: different syntax and only one of them can be a field of the step.
        self.fixed = dict(fixed or {})
        self.tables = dict(tables or {})
        # Finite immutable source references.  A direct selector is held only
        # while lowering its expression; a reference assigned to a local is
        # carried by a finite integer tag in SolverIR.
        self.static_fields = {}
        self._static_tag_ids = {}
        # Read-only module sequences are immutable ROM values of the
        # procedure.  ``lower_procedure`` materialises each representable one
        # once at entry and records its local here; reads then select from that
        # one value instead of rebuilding a linear conditional tree at every
        # source occurrence.
        self.table_arrays = {}
        # Sequences the step grows: how many cells each may need, the length
        # variable each carries, and how many appends the source writes to it.
        self.appends = dict(appends or {})
        self.sequence_plan = sequence_plan
        self.activation = _Activation(
            summary=None if sequence_plan is None else sequence_plan.root_context)
        self._expression_length_images = {}
        # A selected nested dynamic sequence can have an exact logical
        # contract at this activation (including length zero) while occupying
        # the wider backing store owned by its container.  Keep that physical
        # contract beside the transient expression; conflating it with the
        # logical contract either loses the length carrier or claims padding
        # is live source data.
        self._expression_storage_contracts = {}
        # Source discovery is lexical, unlike the procedure's physical carriers.
        self.ordered_locals = {}
        # Physical width of every returned transaction buffer.  Individual
        # builders may prove a tighter append bound, but all return arms must
        # assign arrays of one IR type at their join.
        self.output_capacity = 1
        # Present only for the task-owned return boundary.  Helper batches use
        # the same storage mechanics but are not public output claims.
        self.output_boundary = None
        self.storage = lower_state.ProcedureStorage(
            arrays, reserved_names=set(reserved_names) | set(dict(env or {})))
        # One scope owns every control and outgoing fact of the active loop.
        # Source indices retain their resolved IR identities, not spellings.
        self.loop = None
        self.flow = flow_facts.FlowState.seeded(env)
        # The production entry replaces these with the resolved procedure
        # before lowering a body.  Their explicit empty state also lets an
        # individual statement rule be exercised without a synthetic model.
        self.resolver = None
        self.shadowed = frozenset()
        self._nodes = 0
        # Exact scalar source types proved from construction, task inputs and
        # assignments seen while lowering.  This is deliberately a partial
        # map: an unknown or conflicting binding is never guessed.  It exists
        # only to place Python's Boolean-to-integer promotion at source numeric
        # boundaries without weakening the IR's strict assignment boundary.
        self.source_scalar_types = {}
        self._source_scalar_type_unproved = set()
        # Source loop occurrence -> (minimum, maximum) iterations proved by
        # the same range authority that decides the IR unrolling.  Analyses
        # which must preserve per-iteration object identity consume this map;
        # re-reading the AST with a second bound rule would make a fresh
        # allocation in two iterations look like one shared object.
        self.source_loop_spans = {}
        # Source planning and lowering ask the same recursive contract
        # questions at different occurrences.  The planner already has one
        # aggregate budget; the lowerer must not turn that bound into a
        # per-query allowance by constructing a fresh budget for every call.
        self._source_value_budget = (source_value_budget if
                                     source_value_budget is not None else
                                     SourceValueBudget())
        if not isinstance(self._source_value_budget, SourceValueBudget):
            raise TypeError("source_value_budget must be a SourceValueBudget")
        # Scalar/None occurrence contracts are immutable within one frozen
        # source activation.  Cache only that closed subset after the local
        # projection and source plan agree; recursive carrier diagnostics must
        # still be derived at their precise use site.
        self._source_contract_cache = {}
        self._source_literal_contracts = {}
        # Read positions depend on the frozen lexical body, not on an inlined
        # activation's values. Build them once even when a helper is expanded
        # many times under different source contracts.
        self._local_truth_uses = {}
        self._direct_local_statements = {}

    # ── helpers ──

    @contextmanager
    def _loop_scope(self, running, continuing=None, *, indices=(), exit_label=None):
        """Restore the complete enclosing loop on every body exit."""
        parent = self.loop
        loop = _LoopFrame(
            running, continuing, exit_label, self.activation.destination,
            tuple(indices), parent)
        self.loop = loop
        try:
            yield loop
        finally:
            self.loop = parent

    @contextmanager
    def _call_activation(self, fn, renames, summary, destination):
        """Enter a helper once; restore its caller on every exit, including cancellation."""
        caller = self.activation
        self.activation = _Activation(
            scope=fn, summary=summary, parent=caller,
            renames=renames, destination=destination)
        try:
            yield self.activation
        finally:
            self.activation = caller

    def _spend(self, n=1):
        """Charge ``n`` nodes against the step's budget."""
        self._nodes += n
        if self._nodes > SEMANTIC_IR_NODES:
            raise _BudgetExceeded()

    def _source_name(self, name):
        """Bind a qualified source-planner name to its current IR identity."""
        return (self.storage.state_name(name[5:]) if name.startswith("self.")
                else self._name(name))

    def _source_binding_name(self, node):
        if isinstance(node, ast.Name):
            return self._name(node.id)
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            return self.storage.state_name(node.attr)
        return None

    def _remember_source_scalar_type(self, name, kind):
        """Retain one exact source scalar type, or permanently forget it."""
        name = str(name)
        if name in self._source_scalar_type_unproved:
            return
        if kind not in {value_types.BOOLEAN, value_types.INTEGER}:
            self.source_scalar_types.pop(name, None)
            self._source_scalar_type_unproved.add(name)
            return
        previous = self.source_scalar_types.get(name)
        if previous is None:
            self.source_scalar_types[name] = kind
        elif previous != kind:
            self.source_scalar_types.pop(name, None)
            self._source_scalar_type_unproved.add(name)

    def _record_range(self, name, value, *, join=False):
        """Record the range assigned to ``name`` at this source point.

        An inlined helper may return from several paths, so its result is the
        join of every return expression.  If any arm has no proved range, the
        whole result remains unproved; a later arm must not accidentally make
        it bounded again.
        """
        got = ranges.bounds_of_expression(value, self.flow.bounds)
        if not join:
            self.flow.unknown_ranges.discard(name)
            if got is None:
                self.flow.ranges.pop(name, None)
                self.flow.bounds.pop(name, None)
            else:
                self.flow.bounds[name] = got
                finite = got.finite()
                if finite is None:
                    self.flow.ranges.pop(name, None)
                else:
                    self.flow.ranges[name] = finite
            return
        if name in self.flow.unknown_ranges:
            return
        if got is None:
            self.flow.ranges.pop(name, None)
            self.flow.bounds.pop(name, None)
            self.flow.unknown_ranges.add(name)
            return
        was = self.flow.bounds.get(name)
        joined = got if was is None else was.join(got)
        self.flow.bounds[name] = joined
        finite = joined.finite()
        if finite is None:
            self.flow.ranges.pop(name, None)
        else:
            self.flow.ranges[name] = finite

    def _value_range_candidates(self, value, source=None):
        """Independent finite ranges proved at one source occurrence."""
        found = []
        plan = self.sequence_plan
        scope = self.activation.scope
        context = self.activation.summary
        if plan is not None and scope is not None \
                and isinstance(source, ast.AST):
            planned = plan.expression_range(scope, source, context)
            finite = (None if planned is None else
                      ranges.Bounds.of(planned).finite())
            if finite is not None:
                found.append(finite)
        for environment in (self.flow.bounds, self.storage.carrier_ranges):
            got = ranges.bounds_of_expression(value, environment)
            finite = None if got is None else got.finite()
            if finite is not None:
                found.append(finite)
        return found

    @staticmethod
    def _range_intersection(candidates):
        """Intersect sound range facts, or return ``None`` on disagreement."""
        if not candidates:
            return None
        result = candidates[0]
        for candidate in candidates[1:]:
            intersection = ranges.Bounds.of(result).meet(candidate)
            if intersection is None:
                return None
            result = intersection.finite()
        return result

    def _output_value_range(self, value, source=None):
        """Sound local hull of one value at its public output write."""
        found = self._value_range_candidates(value, source)
        if not found:
            return None
        result = found[0]
        for candidate in found[1:]:
            intersection = ranges.Bounds.of(result).meet(candidate)
            if intersection is None:
                raise OutputBoundaryError(
                    "output-range-inconsistent",
                    "independent frontend range facts for one output are "
                    "inconsistent",
                )
            result = intersection.finite()
        return result

    def _partial_operand_is_safe(self, value, source, domain):
        """Whether this occurrence proves a partial primitive's operand valid.

        This is deliberately occurrence-local.  One source operation can be
        inlined under several argument ranges; proving one copy safe must not
        discharge the shared public goal for another copy.
        """
        interval = self._range_intersection(
            self._value_range_candidates(value, source))
        if interval is None:
            return False
        if domain is primitives.SourceDomain.RIGHT_NONZERO:
            return interval.hi < 0 or interval.lo > 0
        if domain is primitives.SourceDomain.RIGHT_NONNEGATIVE:
            return interval.lo >= 0
        return False

    # ── Y4: an array is a value, and where that could be observed ──
    #
    # The defect this answers is that `ir.Assign` had no defined meaning when the value
    # is an array, and the three things that run the IR each guessed a different one.
    # Python holds a list by reference. The interpreter binds the same Python list and
    # so reproduces that, by accident of being written in Python rather than by a rule.
    # The encoder holds an array as a list of terms and refuses the assignment outright,
    # from inside a formula, with "array t used where a value is expected".
    #
    # The rule is that **an array is a fixed-size value and assigning one copies**. That
    # is a decision about the IR and both backends implement it, which is why
    # `interp.run_step` copies a list on assignment rather than binding it.
    #
    # Value semantics and Python's semantics differ on exactly one thing: a write
    # through a name after the array has been bound to a second name. Not the binding.
    # `self.stack = new_stack`, a step writing back an array it built itself, binds an
    # array to a second name and is the ordinary way to write state; nothing observes
    # anything. So what is refused is the write, and the binding only records that a
    # write from here on would be observable.
    #
    # Conservative in one direction, deliberately: a name whose other holder is dead by
    # the time of the write is refused too, since knowing otherwise is a liveness
    # analysis. Rebinding a name to a freshly built array clears its sharing, which is
    # the common case and needs no analysis at all.

    def _rebind(self, name, value_node):
        """Record what ``name`` now shares its array with, having been assigned.

        Sharing is transitive and symmetric: `u = self.t` then `v = u` puts all three in
        one group, because a write through any of them is a write the others see.
        """
        source = None
        if isinstance(value_node, ast.Name):
            source = self._name(value_node.id)
        elif (isinstance(value_node, ast.Attribute)
              and isinstance(value_node.value, ast.Name)
              and value_node.value.id == "self"):
            source = self.storage.state_name(value_node.attr)
        # Whatever it shared before, it no longer does: it holds a different value now.
        # Over a copy, since the members of a group share one set object and discarding
        # from it is discarding from the thing being iterated.
        for other in list(self.shared.get(name, ())):
            if other != name:
                self.shared[other].discard(name)
        self.shared.pop(name, None)
        if source is None or source == name:
            return                      # a freshly built array shares with nothing
        group = self.shared.setdefault(source, {source})
        group.add(name)
        self.shared[name] = group

    def _check_not_shared(self, name, node, how):
        """Refuse a write through an array another name also holds."""
        group = self.shared.get(name)
        if not group or len(group) < 2:
            return
        others = sorted(n for n in group if n != name)
        raise LoweringError(
            node, f"{how} {name}, which is the same array as "
                  f"{', '.join(others)}: an array is a value here and Python holds it "
                  f"by reference, so this write is visible through "
                  f"{others[0]} on the host and not here",
            code="array-assigned-by-reference")

    def _preserve_iterator_bindings(self, name, source, command):
        """Preserve held objects immediately before a source rebind commits."""
        if (isinstance(source, ast.Name) and self._name(source.id) == name
                or isinstance(source, ast.Attribute)
                and isinstance(source.value, ast.Name)
                and source.value.id == "self" and self.storage.state_name(source.attr) == name):
            return command
        before = []
        for binding in self._iterator_bindings:
            if binding.source != name:
                continue
            save = [ir.Assign(binding.saved.name, ir.Var(name))]
            save.extend(ir.Assign(target, value)
                        for target, value in binding.lengths)
            save.append(ir.Assign(binding.detached, ir.Lit(True)))
            before.append(ir.If(
                ir.Var(binding.detached), ir.Skip(), ir._sequence(save)))
        return ir._sequence([*before, command]) if before else command

    def _with_iterator_binding(self, binding, lower):
        """Keep a held binding live in its argument/body evaluation region."""
        if binding is None:
            return lower()
        bindings = binding if isinstance(binding, tuple) else (binding,)
        self._iterator_bindings.extend(bindings)
        try:
            return lower()
        finally:
            if bindings:
                del self._iterator_bindings[-len(bindings):]

    def _discharge(self, goal, why):
        """This site owes ``goal`` and the operation is total here, for ``why``.

        The other half of `_guard`. A partial operation is partial because of what it
        is, so the declaration is made from the source and cannot see that this
        particular subscript has an index that folded to a literal in range. Saying so
        is what lets the audit be exact: `declared == produced + discharged`, and a rule
        that goes missing is a declared goal that is in neither.
        """
        self.discharged.setdefault(goal, why)

    def _source_file(self, node):
        """Resolved source owner of ``node``, including inlined libraries."""
        resolver = self.resolver
        if resolver is None:
            return self.file
        scope = self.activation.scope if self.activation.scope else node
        return resolver.occurrence_file(scope)

    def _safety_goal(self, node, kind):
        return safety_goal_id(self._source_file(node), node, kind)

    def _read_state_attribute(self, node):
        if node.attr not in self.storage.state_names:
            return
        goal = self._safety_goal(node, "attributeerror")
        present = self.storage.state_presence.get(node.attr)
        if present is None:
            self._discharge(goal, "the source field exists after construction")
        else:
            self._guard(ir.Op("not", ir.Var(present)), goal, "AttributeError", node)

    def _define_state_attribute(self, target, command):
        if (isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name) and target.value.id == "self"
                and target.attr in self.storage.state_presence):
            return ir.Seq(command, ir.Assign(self.storage.state_presence[target.attr], ir.Lit(True)))
        return command

    def _guard(self, cond, goal, code, node=None):
        """Emit a safety-marked failure when ``cond`` holds.

        Source location is explicit provenance, never parsed from the goal ID.
        """
        if node is not None and goal not in self.provenance:
            self.provenance[goal] = {
                "file": self._source_file(node),
                "line": getattr(node, "lineno", None),
                "col": getattr(node, "col_offset", None), "kind": code}
        self.before(ir.If(cond, ir.Fail(code, goal), ir.Skip()))
        # The next source operation is reached only when the failure predicate
        # is false.  Keep that fact in the structural domain; the failing arm
        # has no normal successor and must not be joined back as an unknown.
        current = self._flow_facts()
        normal = flow_facts.assume(current, cond, False)
        if normal is not None:
            self._set_flow_facts(normal)

    # ── what a value needs run first ──

    def before(self, cmd):
        """``cmd`` has to have run before the value being built can be read."""
        self.activation.hoisted.append(cmd)

    def mark(self):
        """A token for "here", to ask `hoisted_since` about later."""
        return len(self.activation.hoisted)

    def hoisted_since(self, mark):
        """Whether anything was hoisted since ``mark``.

        The question `_atom` asks, and asking it about a mark rather than about
        emptiness is the point: what matters is whether *this* expression needed
        something, not whether anything is queued at all.
        """
        return len(self.activation.hoisted) > mark

    def _flush(self, cmd):
        out = cmd
        for guard in reversed(self.activation.hoisted):
            out = ir.Seq(guard, out)
        self.activation.hoisted = []
        return out

    def _scoped(self, lower):
        """Lower something into a hoisting region of its own.

        What an expression needs to run before it is read belongs where the
        expression is evaluated. One list for the whole statement puts it
        before the statement instead, which is the same place only when the
        expression is always evaluated. It is not for the arms of a
        conditional expression or for the operands after the first of a short
        circuit, and hoisting a division guard out of an arm makes the step
        fail on an operand the source never evaluates.

        Returns the commands the expression needed and the value it denotes.
        """
        activation = self.activation
        outer, activation.hoisted = activation.hoisted, []
        try:
            value = lower()
            before = self._flush(ir.Skip())
        finally:
            activation.hoisted = outer
        return before, value

    def _atom_of(self, node):
        """Lower ``node`` to something usable in an expression position.

        Expressions in the IR are pure, so an expression that needed something
        hoisted before it cannot be used where one is expected: what it hoisted
        would have nowhere to run. Binding the value to a variable puts it
        somewhere, and every expression position then holds an expression.

        The mark is taken before the rule runs, so the question is whether *this*
        expression hoisted anything. Asking whether anything was queued at all is a
        different question with the same answer only in the term lowering, where the
        queue is emptied at every statement; in the graph the same words mean "is the
        block being filled non-empty", which is true almost always and made every
        condition in a loop body take a name it did not need.
        """
        mark = self.mark()
        expr = self._truth_expr(node)
        if not self.hoisted_since(mark):
            return None, expr
        tmp = self.storage.fresh()
        return self._flush(ir.Assign(tmp, expr)), ir.Var(tmp)

    def _stable_guard(self, before, guard):
        """Freeze a branch condition whose abstract facts may outlive it.

        A loop reuses its index variable in later unrolled copies.  Keeping an
        expression involving that variable as the selector of a compile-time
        reference would make an earlier branch retroactively follow the last
        index value.  A literal is already stable; every other guard gets one
        SSA-like local at the point Python evaluates it.
        """
        if isinstance(guard, ir.Lit):
            return before, guard
        name = self.storage.fresh()
        assign = ir.Assign(name, guard)
        before = assign if before is None else ir.Seq(before, assign)
        return before, ir.Var(name)

    def _path_guard_atom(self, source):
        """Return proof metadata for a repeatable scalar predicate.

        The certified form is a truth test, possibly negated, of an unwritten
        parameter.  Its resolved IR name identifies this activation, so two
        inlined calls cannot share a path fact accidentally.  The metadata is
        used only to discard contradictory CFG paths; execution still uses
        the lowered guard.
        """
        positive = True
        while isinstance(source, ast.UnaryOp) \
                and isinstance(source.op, ast.Not):
            positive = not positive
            source = source.operand
        if not isinstance(source, ast.Name):
            return None
        scope = self.activation.scope
        resolver = getattr(self, "resolver", None)
        if scope is None or resolver is None:
            return None
        owner = scope
        if not resolver.is_read_only_parameter(owner, source.id):
            return None
        if self._fixed_source_scalar_type(source) not in {
                value_types.BOOLEAN, value_types.INTEGER}:
            return None
        return self._name(source.id), positive

    def _truth_value(self, source, value):
        """Lower Python truth-value testing to an explicit Boolean operation."""
        if isinstance(value, OrderedScalar):
            return ir.Op('lor', ir.Op('ne', value.infinity, ir.Lit(0)),
                         ir.Op('truth', value.payload))
        if isinstance(value, OptionSequence):
            return ir.Lit(bool(value.contract.exact_length))
        if isinstance(value, MappingBuilder):
            return ir.Op("ne", ir.Var(value.count), ir.Lit(0))
        if isinstance(value, _StaticRef):
            return lower_static_refs.disjunction([active for active, item in value.alternatives if bool(item)])
        if isinstance(value, _BatchValue):
            nonempty = ir.Op(
                "ne", ir.Var(value.builder.count), ir.Lit(0))
            # A mapping is always true.  A list follows its run-time length;
            # None is false.  Current internal batch contracts use only the
            # latter two forms, but retaining the mapping case makes the tag a
            # complete boundary carrier rather than a presence-bit shortcut.
            return ir.Op(
                "ite",
                ir.Op("eq", value.form, ir.Lit(
                    _batch_form_code(BatchForm.MAPPING))),
                ir.Lit(True),
                ir.Op("land", ir.Op("ne", value.form, ir.Lit(
                    _batch_form_code(BatchForm.NONE))), nonempty))
        if isinstance(value, OptionValue):
            if value.contract.is_none:
                return ir.Lit(False)
            if value.payload is None:
                raise LoweringError(
                    source, "an optional value has no payload representation",
                    code="optional-payload-representation-unsupported")
            return ir.Op(
                "land", ir.Op("truth", value.present),
                self._truth_value(source, value.payload))
        if isinstance(value, RecordValue):
            raise LoweringError(
                source, "truth testing a fixed-schema record would observe "
                        "its mapping carrier",
                code="record-operation-unsupported")
        if not self._shape(value):
            # Source truthiness is not the IR guard rule.  The conversion is a
            # registered primitive, so the type checker can distinguish this
            # deliberate operation from an integer silently used as a guard.
            return ir.Op("truth", value)
        contract = self._contract_for_value(source, value)
        if not contract.is_sequence:
            raise LoweringError(
                source, "truth testing an array with no sequence contract",
                code="sequence-truth-contract-unproven")
        dynamic = self._dynamic_sequence(value)
        if dynamic is not None:
            _name, length, _capacity = dynamic
            return ir.Op("ne", length, ir.Lit(0))
        if contract.exact_length is not None:
            return ir.Lit(contract.exact_length != 0)
        raise LoweringError(
            source, "truth testing a sequence whose logical length is not "
                    "available", code="dynamic-sequence-truth-unsupported")

    def _dynamic_sequence(self, value):
        """Return the logical-length authority carried by a lowered value.

        Source syntax cannot answer this after a helper, conditional, or
        other lowering boundary has produced a fresh variable.  The lowered
        value is authoritative: a variable in ``grown`` denotes a fixed
        physical array together with its run-time logical length.
        """
        if isinstance(value, ir.Var):
            contract = self.flow.contracts.get(value.name, UNKNOWN)
            if (contract.is_sequence
                    and contract.exact_length is not None
                    and value.name not in self.storage.mutable_sequence_lengths
                    and contract.physical_shape == self._shape(value)):
                return None
        image = self._length_image(value)
        if () not in image:
            return None
        shape = self._shape(value)
        if not shape:
            return None
        name = value.name if isinstance(value, ir.Var) else None
        return (name, image[()], shape[0])

    def _length_image(self, value):
        """Logical-length expressions carried by one lowered value."""
        if isinstance(value, RecordValue):
            image = {}
            for field, child in value.fields:
                prefix = (("field", field),)
                image.update({prefix + path: length
                              for path, length
                              in self._length_image(child).items()})
            return image
        if isinstance(value, OptionValue) and value.payload is not None:
            return {(('payload',),) + path: length
                    for path, length
                    in self._length_image(value.payload).items()}
        if isinstance(value, ir.Var):
            return {
                path: ir.Var(holder)
                for path, holder in self.storage.length_images.get(
                    value.name, {}).items()
            }
        return dict(self._expression_length_images.get(value, {}))

    def _storage_contract(self, value, fallback=UNKNOWN):
        """Return the physical contract carried by one lowered value."""
        if isinstance(value, ir.Var):
            return self.storage.storage_contracts.get(
                value.name,
                self.storage.declaration_contracts.get(
                    value.name,
                    self.flow.contracts.get(value.name, fallback)))
        return self._expression_storage_contracts.get(value, fallback)

    def _bind_expression_lengths(self, value, image, storage=None):
        """Attach logical lengths and physical storage to a transient value."""
        if image:
            self._expression_length_images[value] = dict(image)
        if isinstance(storage, ValueContract) and storage.is_sequence:
            self._expression_storage_contracts[value] = storage
        return value

    def _logical_length(self, source, value, contract=None):
        """The source sequence length denoted by one lowered value."""
        contract = (self._contract_for_value(source, value)
                    if contract is None else contract)
        if not contract.is_sequence:
            return None
        if isinstance(value, _BatchValue):
            return ir.Var(value.builder.count)
        dynamic = self._dynamic_sequence(value)
        if dynamic is not None:
            return dynamic[1]
        if contract.exact_length is not None:
            return ir.Lit(contract.exact_length)
        raise LoweringError(
            source, "a sequence has a run-time logical length but its "
                    "lowered value carries no length",
            code="dynamic-sequence-length-unavailable")

    def _binding_contract(self, name, occurrence=None):
        scope = self.activation.scope
        if self.sequence_plan is None or scope is None:
            return UNKNOWN
        context = self.activation.summary
        return self.sequence_plan.binding_contract(
            scope, name, context, occurrence=occurrence)

    def _binding_layout_contract(self, name):
        """Joined source contract usable to allocate one lexical local."""
        scope = self.activation.scope
        if self.sequence_plan is None or scope is None:
            return UNKNOWN
        context = self.activation.summary
        return self.sequence_plan.binding_layout_contract(
            scope, name, context)

    def _binding_batch_contract(self, name):
        """The source batch algebra attached to one local binding."""
        scope = self.activation.scope
        if self.sequence_plan is None or scope is None:
            return None
        context = self.activation.summary
        return self.sequence_plan.binding_batch_contract(
            scope, name, context)

    def _represented_batch_contract(self, name):
        """The one physical value contract used by a represented batch.

        ``BatchContract`` is authoritative for a local whose Python carrier
        may vary between ``None``, one mapping, and a list of mappings.  The
        ordinary value domain cannot join all three carriers, whereas their
        IR representation is always one count plus fixed field buffers.  A
        legacy single-carrier plan may lack the batch-domain entry; only then
        is its ordinary value contract a valid fallback.
        """
        source_batch = self._binding_batch_contract(name)
        if source_batch is not None:
            contract = batch_value_contract(source_batch)
            if record_batch_payload(contract) is not None:
                return contract
            # An always-empty batch has no source-observed transaction value,
            # but its task-declared boundary may still provide the record
            # schema needed to allocate the count-and-fields carrier.
            spec = self._sequence_spec(name)
            if (spec is not None and spec.is_batch
                    and spec.element is not None and spec.element.is_record):
                return ValueContract.sequence(
                    "list", source_batch.minimum, source_batch.maximum,
                    spec.element)
            return contract
        contract = self._binding_contract(name)
        return (contract if record_batch_payload(contract) is not None else
                UNKNOWN)

    def _length_target(self, name, capacity, node):
        """Stable companion variable for assignments to sequence ``name``."""
        if name in self.storage.lengths:
            if self.storage.capacities[name] != capacity:
                raise LoweringError(
                    node, f"the sequence capacity of {name} changes from "
                          f"{self.storage.capacities[name]} to {capacity}",
                    code="sequence-plan-inconsistent")
            self._register_dynamic_sequence(
                name, self.storage.lengths[name], capacity)
            return self.storage.lengths[name]
        length = self.storage.fresh()
        self._register_dynamic_sequence(name, length, capacity)
        return length

    def _register_dynamic_sequence(self, name, length, capacity):
        """Bind a physical sequence to its finite logical-length scalar.

        Every dynamic sequence is a fixed-capacity array plus a companion
        length maintained by its constructors and updates.  Consequently that
        companion is intrinsically in ``[0, capacity]``.  Recording the fact at
        the representation boundary makes it available uniformly to loops,
        slicing and the position plan; no consumer has to rediscover it from
        the source syntax.
        """
        intrinsic = self.storage.register_sequence(name, length, capacity)
        known = self.flow.bounds.get(length)
        bounded = intrinsic if known is None else known.meet(intrinsic)
        if bounded is None:
            raise ValueError(
                f"the logical length of {name} lies outside its capacity")
        self.flow.bounds[length] = bounded
        # A companion length is created by the representation plan itself.
        # Once the plan has proved its finite capacity, an earlier syntactic
        # failure to range the fresh name must not continue to veto it.
        self.flow.unknown_ranges.discard(length)
        finite = bounded.finite()
        if finite is not None:
            self.flow.ranges[length] = finite

    def _ensure_length_layout(self, name, contract, node):
        """Allocate every logical-length carrier required by ``contract``."""
        holders = self.storage.length_images.setdefault(name, {})
        for path, shape, capacity in lower_context.dynamic_length_specs(contract):
            holder = holders.get(path)
            if holder is None:
                if not path:
                    holder = self._length_target(name, capacity, node)
                else:
                    holder = self.storage.fresh()
                    holders[path] = holder
                    self.storage.require_shape(holder, shape, node)
                    self.flow.contracts[holder] = (
                        ValueContract.fixed(shape) if shape else SCALAR)
                    if shape:
                        self.flow.lengths[holder] = shape
                    else:
                        self._remember_source_scalar_type(
                            holder, value_types.INTEGER)
        return dict(holders)

    def _register_dynamic_state(self, name, contract, node):
        """Give bounded source-state sequences persistent length cells."""
        holders = self._ensure_length_layout(name, contract, node)
        self.storage.state_length_variables.update(holders.values())
        return holders

    def _length_value_at(self, value, contract, path, shape, node):
        """The encoded logical length at ``path`` in one lowered value."""
        carried = self._length_image(value).get(path)
        if carried is not None:
            if self._shape(carried) != tuple(shape):
                raise LoweringError(
                    node, "a logical-length carrier has the wrong recursive "
                          "shape", code="sequence-plan-inconsistent")
            return carried
        selected = lower_state.contract_at_dynamic_path(contract, path)
        if (selected.is_sequence and selected.exact_length is not None):
            return _fill_shape(shape, ir.Lit(selected.exact_length))
        raise LoweringError(
            node, "a bounded recursive value is missing a logical-length "
                  "carrier", code="nested-dynamic-sequence-unsupported")

    def _assign_length_layout(self, target, value, actual, storage, node):
        """Assign all compiler-owned length cells beside one data value."""
        holders = self._ensure_length_layout(target, storage, node)
        specs = {
            path: shape for path, shape, _capacity
            in lower_context.dynamic_length_specs(storage)
        }
        command = None
        for path, holder in holders.items():
            source = self._length_value_at(
                value, actual, path, specs[path], node)
            one = ir.Assign(holder, source)
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _initialize_length_layout(self, target, contract, node):
        """Set every logical length in one inactive physical value to zero."""
        holders = self._ensure_length_layout(target, contract, node)
        specs = {
            path: shape for path, shape, _capacity
            in lower_context.dynamic_length_specs(contract)
        }
        command = None
        for path, holder in holders.items():
            one = ir.Assign(holder, _fill_shape(specs[path], ir.Lit(0)))
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _select_sequence_element(self, value, index, contract, node):
        """Select data and every nested logical length from one sequence."""
        if (not contract.is_sequence or contract.element is None
                or contract.physical_shape is None):
            raise LoweringError(
                node, "a selected value has no finite sequence layout",
                code="source-plan-inconsistent")
        selected = ir.Sel(value, index)
        nested = {}
        for path, length_value in self._length_image(value).items():
            if path and path[0] is lower_state.SEQUENCE_ELEMENT:
                nested[path[1:]] = ir.Sel(length_value, index)
        return self._bind_expression_lengths(
            selected, nested, storage=contract.element)

    def _store_sequence_element(self, target, index, value, actual,
                                container, node):
        """Store one logical element and its nested length metadata."""
        if not container.is_sequence or container.element is None:
            raise LoweringError(
                node, "a stored value has no finite sequence layout",
                code="source-plan-inconsistent")
        expected = container.element
        value = self._coerce_sequence_storage(
            node, value, actual, expected)
        wanted = expected.physical_shape
        if wanted is None or self._shape(value) != wanted:
            raise LoweringError(
                node, "a stored sequence element changes its physical shape",
                code="store-value-shape-mismatch")
        metadata = self._store_element_lengths(
            target, index, value, actual, container, node)
        return ir.Seq(ir.Store(target, index, value), metadata)

    def _initialize_element_lengths(self, target, values, contracts,
                                    storage, node):
        """Initialize per-element length arrays for a sequence literal."""
        holders = self._ensure_length_layout(target, storage, node)
        specs = {
            path: shape for path, shape, _capacity
            in lower_context.dynamic_length_specs(storage)
        }
        command = None
        for path, holder in holders.items():
            if not path or path[0] is not lower_state.SEQUENCE_ELEMENT:
                continue
            shape = specs[path]
            child_shape = shape[1:]
            cells = [
                self._length_value_at(
                    value, contract, path[1:], child_shape, node)
                for value, contract in zip(values, contracts)
            ]
            cells.extend(
                lower_returns.zero_shape(child_shape)
                for _ in range(shape[0] - len(cells)))
            one = ir.Assign(holder, ir.Op("arraylit", *cells))
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _store_element_lengths(self, target, index, value, actual,
                               storage, node):
        """Store a value's nested lengths beside one sequence element."""
        holders = self._ensure_length_layout(target, storage, node)
        specs = {
            path: shape for path, shape, _capacity
            in lower_context.dynamic_length_specs(storage)
        }
        command = None
        for path, holder in holders.items():
            if not path or path[0] is not lower_state.SEQUENCE_ELEMENT:
                continue
            source = self._length_value_at(
                value, actual, path[1:], specs[path][1:], node)
            one = ir.Store(holder, index, source)
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _store_nested_lengths(self, target, indices, value, actual,
                              storage, node):
        """Update metadata below a nested subscript assignment."""
        prefix = (lower_state.SEQUENCE_ELEMENT,) * len(indices)
        holders = self._ensure_length_layout(target, storage, node)
        specs = {
            path: shape for path, shape, _capacity
            in lower_context.dynamic_length_specs(storage)
        }
        command = None
        for path, holder in holders.items():
            if path[:len(prefix)] != prefix:
                continue
            relative = path[len(prefix):]
            shape = specs[path][len(indices):]
            source = self._length_value_at(
                value, actual, relative, shape, node)
            updated = source
            current = ir.Var(holder)
            frames = []
            for index in indices:
                frames.append((current, index))
                current = ir.Sel(current, index)
            for array_value, index in reversed(frames):
                updated = ir.Op("store", array_value, index, updated)
            one = ir.Assign(holder, updated)
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _coerce_sequence_storage(self, source, value, actual, storage):
        """Place one logical sequence in a binding's stable backing shape.

        Source control flow may bind the same local to intervals with
        different maxima.  SolverIR variables have one type, so the joined
        source contract owns one backing capacity; shorter values are padded
        and a value whose backing is wider is projected only after its live
        length is proved to fit.  Padding is unobservable because every read
        continues to use the companion logical length.
        """
        if not isinstance(storage, ValueContract) or not storage.is_sequence:
            return value
        if (not isinstance(actual, ValueContract) or not actual.is_sequence
                or not contract_contains(storage, actual)):
            raise LoweringError(
                source, "a sequence binding does not contain the value "
                        "assigned to it",
                code="source-value-contract-disagrees")
        wanted = storage.physical_shape
        shape = self._shape(value)
        empty = actual.exact_length == 0
        if wanted is None or not shape:
            raise LoweringError(
                source, "a dynamic sequence has no stable physical shape",
                code="source-value-contract-disagrees")
        if shape == wanted:
            return value
        if actual.maximum is None or actual.maximum > wanted[0]:
            raise LoweringError(
                source, "a sequence value can exceed its binding capacity",
                code="sequence-plan-too-small")

        def resize(data, contract, current, target):
            if current == target:
                return data
            if contract.is_sequence and contract.exact_length == 0:
                return lower_returns.zero_shape(target)
            if (not contract.is_sequence or not current or not target
                    or contract.maximum is None or contract.maximum > target[0]):
                raise LoweringError(
                    source, "a nested sequence value can exceed its binding capacity",
                    code="sequence-plan-too-small")
            cells = [resize(ir.Sel(data, ir.Lit(index)), contract.component(index),
                            current[1:], target[1:])
                     for index in range(min(current[0], target[0], contract.maximum))]
            cells.extend(lower_returns.zero_shape(target[1:])
                         for _ in range(target[0] - len(cells)))
            return ir.Op("arraylit", *cells)

        data = resize(value, actual, shape, wanted)
        result = self.storage.fresh()
        lengths = (self._initialize_length_layout(result, storage, source)
                   if empty else self._assign_length_layout(
                       result, value, actual, storage, source))
        self.before(ir.Seq(
            ir.Assign(result, data), lengths))
        self.storage.require_shape(result, wanted, source)
        self.flow.lengths[result] = wanted
        self.flow.contracts[result] = (
            actual.with_interval(0, 0, storage.element) if empty else actual)
        self.storage.storage_contracts[result] = storage
        if actual.carrier is not None:
            self.flow.carriers[result] = actual.carrier
        return ir.Var(result)

    def _effectful(self, node):
        """Whether evaluating this subtree can change what another one reads.

        Conservative on purpose. An inlined call may write to the state and an
        append does, so either counts; a registered primitive and ``len`` are
        reads and do not. Getting this wrong in the safe direction costs a
        temporary, and getting it wrong in the other direction is the fault
        below.

        This is a lexical query, independent of the flow facts produced while
        lowering an operand. Sibling expressions share one scan through
        ``_last_effectful_operand``; no per-node cache retains their ASTs.
        """
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        use_resolver = resolver is not None and scope is not None
        for inner in ast.walk(node):
            if isinstance(inner, ast.NamedExpr):
                return True
            if isinstance(inner, ast.Call):
                name = (getattr(inner.func, "id", None)
                        or getattr(inner.func, "attr", None))
                is_method = (isinstance(inner.func, ast.Attribute)
                             and isinstance(inner.func.value, ast.Name)
                             and inner.func.value.id == "self")
                # Purity is decided by the shared lexical resolver, not by a
                # spelling whitelist.  Its recursive summary accepts only
                # calls with no caller-visible write or opaque call; a small
                # arithmetic indexing helper is consequently a read, while a
                # helper that mutates state or an argument remains effectful.
                if use_resolver:
                    if resolver.call_has_no_external_effect(scope, inner):
                        continue
                    return True
                # Legacy path, no resolver: the old spelling whitelist.
                if is_method:
                    if name not in ("len", "get"):
                        return True
                    continue
                if name not in self.primitives and name not in ("len", "get"):
                    return True
        return False

    def _capture(self, value):
        """Bind a value now, so an effect evaluated later cannot change it.

        An array is copied, cell by cell, which is the whole of the array discipline
        this subset is built on: **fixed-size value arrays, with no observable
        aliasing.** Python's rule is that a subscript evaluates its primary first and
        obtains the object, so a call in the index that rebinds the attribute does not
        change which object is read: `self.t[self.bump()]` reads the list `self.t`
        named before `bump` replaced it.

        This declined to copy, and the reason it gave was that a list is held by
        reference, so a later effect is visible to an earlier read. That confuses
        rebinding the name with mutating the object. Rebinding is not visible, which
        is what the copy expresses; mutating the same object would be, and the
        discipline is that a source doing so is not in the subset. The host said 1 and
        both lowerings said 9.

        The real obstacle was the one the old comment named second: a fresh name has
        no shape recorded, so the subscript rule loses the length it checks the index
        against. That is fixed by recording it, not by declining to copy. The copy is
        `arraylit` over the cells, which is a value copy the encoder and the
        interpreter both already build, and whose shape is its own arity.

        Only where something later in the expression can write, so an expression with
        no effects in it pays nothing.  The copy follows the complete recursive
        shape; a selected inner array is itself copied before a later effect runs.
        """
        if isinstance(value, OrderedScalar):
            return OrderedScalar(self._capture(value.infinity), self._capture(value.payload))
        if isinstance(value, OptionSequence):
            return OptionSequence(self._capture(value.present), self._capture(value.payload),
                                  value.contract)
        if isinstance(value, MappingBuilder):
            raise LoweringError(None, "a map receiver cannot be captured as an IR value copy",
                                code="mapping-alias-unsupported")
        if isinstance(value, _TupleKey):
            return _TupleKey(tuple(self._capture(item) for item in value.items))
        if isinstance(value, _StaticRef):
            command, bound = value.bind(self, self.storage.fresh(), None)
            self.before(command)
            return bound
        if isinstance(value, _BatchValue):
            count = self._capture(ir.Var(value.builder.count))
            columns = {field: self._capture(ir.Var(variable)).name
                       for field, variable in value.builder.buffers.items()}
            return _BatchValue(
                _BatchBuilder(count.name, columns, value.builder.capacity),
                self._capture(value.form), value.contract)
        if isinstance(value, OptionValue):
            if value.contract.is_none:
                return value
            return OptionValue(
                self._capture(value.present), self._capture(value.payload),
                value.contract)
        if isinstance(value, RecordValue):
            return self._capture_product(value)
        if isinstance(value, _PositionalValue):
            return _PositionalValue(tuple(self._capture(item) for item in value.items),
                                    value.contracts, value.scalar_types, value.contract)
        if isinstance(value, ir.Lit):
            return value            # nothing can change what a literal denotes
        shape = self._shape(value)
        tmp = self.storage.fresh()
        self.storage.require_shape(tmp, shape, value)
        # A capture retains its occurrence's bounds, including the leaf hull
        # of an array. Otherwise a saved iterable loses its element ranges,
        # or a saved range step loses its proved sign, despite unchanged values.
        self._record_range(tmp, value)
        if isinstance(value, ir.Var) and value.name in self.flow.contracts:
            self.flow.contracts[tmp] = self.flow.contracts[value.name]
        else:
            self.flow.contracts[tmp] = (
                ValueContract.fixed(shape) if shape else SCALAR)
        if shape:
            def copied_at(array, dimensions):
                return ir.Op(
                    "arraylit",
                    *[(copied_at(ir.Sel(array, ir.Lit(j)), dimensions[1:])
                       if len(dimensions) > 1
                       else ir.Sel(array, ir.Lit(j)))
                      for j in range(dimensions[0])])

            copied = copied_at(value, shape)
            command = ir.Assign(tmp, copied)
            # The length travels with the name, which is what let the copy be
            # refused before: without it `_extent` has nothing to answer with and
            # the subscript rule refuses an array whose length is not static.
            self.flow.lengths[tmp] = shape
            if isinstance(value, ir.Var) and value.name in self.flow.carriers:
                self.flow.carriers[tmp] = self.flow.carriers[value.name]
            for path, length_value in self._length_image(value).items():
                captured_length = self.storage.fresh()
                length_shape = self._shape(length_value)
                command = ir.Seq(
                    command, ir.Assign(captured_length, length_value))
                self.storage.require_shape(
                    captured_length, length_shape, value)
                self.flow.contracts[captured_length] = (
                    ValueContract.fixed(length_shape)
                    if length_shape else SCALAR)
                if length_shape:
                    self.flow.lengths[captured_length] = length_shape
                if not path:
                    self._register_dynamic_sequence(
                        tmp, captured_length, shape[0])
                else:
                    self.storage.length_images.setdefault(tmp, {})[path] = \
                        captured_length
            self.before(command)
        else:
            self._remember_source_scalar_type(
                tmp, _fixed_expression_type(
                    value, self.source_scalar_types.get))
            self.before(ir.Assign(tmp, value))
        return ir.Var(tmp)

    def _last_effectful_operand(self, nodes):
        """Only operands before the last possible write need capturing.

        Scan backwards once in the caller's lexical scope. Lowering still runs
        left to right; nested helper calls restore that scope on return. Index
        zero needs no query because no operand precedes it.
        """
        for index in range(len(nodes) - 1, 0, -1):
            if self._effectful(nodes[index]):
                return index
        return 0

    def _in_order(self, *nodes, lower=None):
        """Lower sibling expressions in the order the source evaluates them.

        Python evaluates left to right, so a subexpression read before an
        effect denotes the value from before that effect. Lowering every
        sibling first and only then running the commands they queued denotes
        the value from after it: ``self.x + self.bump()`` read the bumped
        ``x``, and so did the first argument of ``f(self.x, self.bump())``.

        The fault was not in any one rule. It was that no rule owned the order,
        so every compound form got it right or wrong on its own. Order lives
        here now, and each form asks for it.

        A value is bound to a name of its own only when something later in the
        same expression can write, since a temporary at every operator would
        cost a definition per node for the many expressions that have no
        effects at all.
        """
        lower = self.expr if lower is None else lower
        last_effect = self._last_effectful_operand(nodes)
        out = []
        for index, node in enumerate(nodes):
            value = lower(node)
            if index < last_effect:
                value = self._capture(value)
            out.append(value)
        return out

    def _integer_context_expr(self, node):
        """Lower one source scalar at a Python numeric-use boundary.

        Python keeps ``bool`` distinct at assignment and I/O boundaries, but
        promotes it to ``int`` when an arithmetic operation consumes it.  The
        SolverIR boundary states that promotion explicitly with ``toint``.
        Conditional and short-circuit expressions need the requested result
        type while they are lowered: converting their result afterwards is too
        late, because their join temporary would already have mixed Boolean and
        integer definitions.
        """
        contract = self._contract_ast(node)
        if (contract is INCOMPATIBLE
                and isinstance(node, (ast.BoolOp, ast.IfExp))
                and self.sequence_plan is not None and self.activation.scope):
            # The stateless contract walk cannot narrow an optional operand
            # under a preceding guard. Use only this activation's proved
            # scalar result; lowering still checks each selected operand.
            context = self.activation.summary
            planned = self.sequence_plan.expression_contract(
                self.activation.scope, node, context)
            if planned == SCALAR:
                contract = planned
        if contract.kind != "scalar":
            return self.expr(node)
        if isinstance(node, ast.BoolOp):
            return self._boolop(node, result_kind="integer")
        if isinstance(node, ast.IfExp):
            return self._ifexp(node, result_kind="integer")
        value = (self._call(node, result_kind="integer")
                 if isinstance(node, ast.Call) else self.expr(node))
        if isinstance(value, (_BatchValue, OptionValue, RecordValue)) \
                or self._shape(value):
            raise LoweringError(
                node, "a Python numeric operation consumes a non-scalar value",
                code="numeric-value-not-scalar")
        source_kind = self._fixed_source_scalar_type(node)
        lowered_kind = _fixed_expression_type(
            value, self.source_scalar_types.get)
        return (ir.Op("toint", value)
                if value_types.BOOLEAN in {source_kind, lowered_kind}
                else value)

    @staticmethod
    def _reaches_truth_test(node, parents, coercions=()):
        """Whether this value flows to a truth test or proved scalar coercion.

        An operand of ``and`` or ``or`` is tested, but it may also be the
        value returned by the whole expression.  It is therefore safe to
        forget the operand's exact Boolean-versus-integer type only when the
        complete enclosing Boolean expression is itself used as a condition.
        Conditional-expression arms have the same value-flow property.
        """
        while node in parents:
            parent = parents[node]
            if (isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id in coercions
                    and has_plain_positional_arity(parent, 1)
                    and parent.args[0] is node):
                return True
            if (isinstance(parent, ast.UnaryOp)
                    and isinstance(parent.op, ast.Not)
                    and parent.operand is node):
                return True
            if (isinstance(parent, (ast.If, ast.While, ast.IfExp))
                    and parent.test is node):
                return True
            if isinstance(parent, ast.Assert) and parent.test is node:
                return True
            if isinstance(parent, ast.comprehension) \
                    and node in parent.ifs:
                return True
            if isinstance(parent, ast.match_case) \
                    and parent.guard is node:
                return True
            if (isinstance(parent, ast.BoolOp)
                    and node in parent.values):
                node = parent
                continue
            if (isinstance(parent, ast.IfExp)
                    and node in (parent.body, parent.orelse)):
                node = parent
                continue
            return False
        return False

    def _local_has_only_truth_uses(self, spelling, *, include_coercions=False):
        """Prove that one source local cannot expose its Python scalar type.

        Numeric equality makes ``False`` and ``0`` observationally equal, but
        the frontend keeps public/state types exact.  A mixed local therefore
        receives the common integer carrier only when every read is a truth
        test or an explicit builtin int/bool conversion. Nested scopes and
        shadowing are conservatively outside this
        small proof; they retain the ordinary strict refusal.
        """
        if self.activation.scope is None:
            return False
        owner = self.activation.scope
        key = owner, include_coercions
        if key in self._local_truth_uses:
            return self._local_truth_uses[key].get(spelling, False)
        parents = {}
        stack = list(reversed(owner.body))
        while stack:
            parent = stack.pop()
            for child in ast.iter_child_nodes(parent):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Lambda, ast.ClassDef)):
                    continue
                parents[child] = parent
                stack.append(child)
        uses = {}
        resolver = getattr(self, "resolver", None)
        coercions = tuple(name for name in ("int", "bool")
                          if include_coercions and resolver is not None
                          and resolver.is_builtin(owner, name))
        for candidate in parents:
            if (isinstance(candidate, ast.Name)
                    and isinstance(candidate.ctx, ast.Load)):
                uses[candidate.id] = (uses.get(candidate.id, True)
                                     and self._reaches_truth_test(
                                         candidate, parents, coercions))
        self._local_truth_uses[key] = uses
        return uses.get(spelling, False)

    def _local_value_expr(self, node, spelling):
        """Lower one lexical-local value with its proved fixed IR carrier."""
        # This is an internal semantic abstraction, not a Python type
        # coercion: if every read of the binding reaches only a truth test, an
        # integer carrier preserves all observations even when a short-circuit
        # expression can return either an Integer or a Boolean.  Public output
        # and state assignments never pass through this local-only boundary.
        promote = (self._contract_ast(node).kind == "scalar"
                   and (self._local_has_only_truth_uses(spelling)
                        or (self._fixed_source_scalar_type(node) is None
                            and self._local_has_only_truth_uses(
                                spelling, include_coercions=True))))
        # Normalize every assignment to this binding, including a plain bool
        # on one path and an integer on another. Looking only at this RHS's
        # kinds leaves the shared local with incompatible definitions.
        return self._integer_context_expr(node) if promote else self.expr(node)

    def _direct_local_assignment(self, node, target):
        """A binding that dominates the rest of this function activation.

        Branch and loop writes need a joined representation; they must not
        change a lexical rename while lowering just one arm or iteration.
        """
        if not isinstance(target, ast.Name) or self.activation.scope is None:
            return False
        owner = self.activation.scope
        if owner not in self._direct_local_statements:
            self._direct_local_statements[owner] = frozenset(owner.body)
        return (node in self._direct_local_statements[owner]
                and self.resolver.local_owner(owner, target.id) is owner)

    def _split_scalar_local(self, node, target, value):
        """Separate consecutive, differently typed scalar live ranges.

        The RHS has already been evaluated using the old binding. Only this
        activation's subsequent reads change names; the public input, earlier
        captures, and persistent state retain their original types and values.
        """
        if not self._direct_local_assignment(node, target):
            return
        previous = self.source_scalar_types.get(self._name(target.id))
        current = _fixed_expression_type(value, self.source_scalar_types.get)
        scalar_types = (value_types.BOOLEAN, value_types.INTEGER)
        if previous not in scalar_types or current not in scalar_types \
                or previous == current:
            return
        owner = self.activation.scope
        for frame in self.activation.frames():
            if frame.scope is owner:
                name = self.storage.fresh()
                frame.renames[target.id] = name
                self._remember_source_scalar_type(name, current)
                return

    def _truth_expr(self, node):
        """Lower one expression for Python truth testing, not as a value.

        ``and`` and ``or`` return an operand in value position.  In a guard,
        however, only that selected operand's truth is observable.  Lowering
        directly to a Boolean avoids inventing a temporary that is Boolean on
        one path and integer on another while preserving short-circuit effects
        and condition goals.
        """
        if isinstance(node, ast.BoolOp):
            return self._boolop(node, result_kind="boolean")
        if isinstance(node, ast.IfExp):
            return self._ifexp(node, result_kind="boolean")
        if isinstance(node, ast.Call) and self._contract_ast(node).kind == "scalar":
            return self._truth_value(node, self._call(node, result_kind="boolean"))
        return self._truth_value(node, self.expr(node))

    def _fixed_source_scalar_type(self, node):
        """One exact scalar type proved at this source occurrence, or ``None``.

        Literals and operators carry their source type directly.  Names use
        only construction, declared-input and assignment facts retained above;
        an unknown or conflicting binding permanently removes that proof.
        This matters for Python's operand-dependent bitwise result: two
        Booleans produce a Boolean, while an Integer operand produces an
        Integer.  An unproved case remains subject to the strict IR checker.
        """
        structured = self._structured_type_at(node)
        if structured is not None:
            if structured.kind == "boolean":
                return value_types.BOOLEAN
            if structured.kind == "integer":
                return value_types.INTEGER
            if structured.kind == "enumeration":
                return None
        if isinstance(node, ast.Constant):
            if type(node.value) is bool:
                return value_types.BOOLEAN
            if type(node.value) is int:
                return value_types.INTEGER
            return None
        if isinstance(node, ast.Name):
            return self.source_scalar_types.get(self._name(node.id))
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            return self.source_scalar_types.get(self.storage.state_name(node.attr))
        if isinstance(node, ast.Compare):
            return value_types.BOOLEAN
        if isinstance(node, ast.UnaryOp):
            return (value_types.BOOLEAN if isinstance(node.op, ast.Not)
                    else value_types.INTEGER)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if (node.func.id in {"bool", "all", "any"}
                    and self._spelling_is_builtin(node.func.id)):
                return value_types.BOOLEAN
            if node.func.id == "int" and self._spelling_is_builtin("int"):
                return value_types.INTEGER
        if isinstance(node, ast.BoolOp):
            kinds = {self._fixed_source_scalar_type(value)
                     for value in node.values}
            return kinds.pop() if len(kinds) == 1 else None
        if isinstance(node, ast.IfExp):
            left = self._fixed_source_scalar_type(node.body)
            right = self._fixed_source_scalar_type(node.orelse)
            return left if left is not None and left == right else None
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
                left = self._fixed_source_scalar_type(node.left)
                right = self._fixed_source_scalar_type(node.right)
                if left == right == value_types.BOOLEAN:
                    return value_types.BOOLEAN
                if value_types.INTEGER in {left, right}:
                    return value_types.INTEGER
                return None
            return value_types.INTEGER
        return None

    def _structured_type_at(self, node):
        """Declared ValueType selected by one source expression, if any."""
        selectors = []
        root = node
        while isinstance(root, ast.Subscript):
            selectors.append(root.slice)
            root = root.value
        selectors.reverse()
        binding = None
        if isinstance(root, ast.Name):
            binding = self._structured_input_binding(root.id)
        elif (isinstance(root, ast.Attribute)
              and isinstance(root.value, ast.Name)
              and root.value.id == "self"):
            binding = self.storage.structured_bindings.get(root.attr)
        if binding is None:
            return None
        selected = binding.value_type
        for selector in selectors:
            if selected.kind == "record":
                if (not isinstance(selector, ast.Constant)
                        or type(selector.value) is not str):
                    return None
                selected = dict(selected.fields).get(selector.value)
                if selected is None:
                    return None
            elif selected.kind == "array":
                selected = selected.element
            else:
                return None
        return selected

    def _source_scalar_type_for_value(self, source, value):
        """Exact scalar type at one source-to-IR value boundary, if proved.

        Source syntax is the first authority.  A helper call or a compiler
        temporary does not itself disclose its result type, so the lowered
        value may carry the same fact from an earlier proved binding.  This is
        a partial fact only: disagreement or absence remains unproved and the
        procedure-wide type checker still decides admissibility.
        """
        # Python's + and * also produce sequences.  Their proved array shape
        # takes precedence over the scalar syntax rule; otherwise a helper's
        # array return receives an integer placeholder at its entry block.
        if self._shape(value):
            return _fixed_expression_type(value, self.source_scalar_types.get)
        kind = self._fixed_source_scalar_type(source)
        return (kind if kind is not None else
                _fixed_expression_type(value, self.source_scalar_types.get))

    def _lower_payload_groups(self, groups):
        """Lower record payloads in their complete source evaluation order.

        A return or batch may contain several fields and several records.
        Python evaluates that whole flattened sequence from left to right;
        lowering each record independently lets a later record's effect change
        a value read by an earlier one.  Values already represented by an IR
        expression come from an immutable captured record and need no source
        evaluation.
        """
        groups = [list(group) for group in groups]
        sources = []
        positions = []
        for group_index, group in enumerate(groups):
            for pair_index, (_name, value, _contract) in enumerate(group):
                if isinstance(value, ast.AST):
                    sources.append(value)
                    positions.append((group_index, pair_index))
        lowered = self._in_order(*sources)
        for (group_index, pair_index), value in zip(positions, lowered):
            name, source, _contract = groups[group_index][pair_index]
            contract = self._contract_for_value(source, value)
            groups[group_index][pair_index] = (name, value, contract)
        return groups

    def _site_goal(self, node, outcome, family=goals.DECISION):
        """Identifier of the enumerated site at ``node``, in ``family``.

        Only an outcome the source site declares has an identifier. Both loop
        activation outcomes remain declared even when a fixed trip count makes
        one unreachable.

        A position is not an identity either. The condition sites of a
        decision are the leaves of its boolean tree, and an inner node of that
        tree carries the position of its own first operand, so looking one up
        by position finds the leaf and marks it with the value of everything
        the operator combines. That is how ``a and (b or c)`` came to report
        ``b`` as true whenever ``b or c`` was.
        """
        if family == goals.CONDITION and isinstance(node, ast.BoolOp):
            return None
        resolver = getattr(self, "resolver", None)
        source_file = (
            resolver.occurrence_file(node)
            if resolver is not None else self.file
        )
        site = self.sites.get(
            (source_file, node.lineno, node.col_offset, family))
        if site is None or outcome not in site.outcomes:
            return None
        return goal_id(site, outcome)

    def _location_mark(self, node):
        """The public entry event for one admitted source statement."""

        goal = self._site_goal(node, True, goals.LOCATION)
        return ir.Mark(goal) if goal is not None else ir.Skip()

    def _operation_bindings(self, node):
        if not self.operation_goals:
            return ()
        from .source_operations import OperationSite

        file = self.resolver.occurrence_file(node) if hasattr(self, "resolver") else self.file
        return self.operation_goals.get(OperationSite.of(node, file), ())

    def _observe_operation(self, node, operands, result, source_types):
        """Sample once, at completion, before a surrounding expression can write.

        Failure guards are already before this command. No mark can escape a
        failed operation. Only explicitly bound sites pay for these snapshots.
        """
        bindings = self._operation_bindings(node)
        if not bindings:
            return result
        from ..symbolic.operation_goals import predicate_expression
        # Python numeric-use lowering promotes bool operands. Preserve source
        # values in the observation instead of reporting those promoted ints.
        actual = []
        for value, kind in zip(operands, source_types[:-1], strict=True):
            actual.append(ir.Op("ne", value, ir.Lit(0)) if kind == value_types.BOOLEAN
                          and _fixed_expression_type(value, self.source_scalar_types.get) != value_types.BOOLEAN
                          else value)
        kinds = tuple("boolean" if kind == value_types.BOOLEAN else
                      "integer" if kind == value_types.INTEGER else
                      "sequence" if kind == "sequence" else None for kind in source_types)
        active = []
        for binding in bindings:
            declared = tuple(value.kind for value in binding.predicate.values)
            if binding.typed_variant and any(kind is not None and kind != expected
                                             for kind, expected in zip(kinds, declared, strict=True)):
                # The rule selects this source operation's typed variant. A
                # proved different variant cannot hit; an unknown one is not
                # evidence of absence and still requires a supported query.
                self.before(ir.If(ir.Lit(False), ir.Mark(binding.id), ir.Skip()))
                continue
            if declared != kinds:
                raise LoweringError(node, "operation predicate types do not match proved source values",
                                    code="operation-goal-types-unproved")
            active.append(binding)
        if not active:
            return result
        result = self._capture(result)
        for binding in active:
            condition = predicate_expression(binding.predicate, (*actual, result))
            self.before(ir.If(condition, ir.Mark(binding.id), ir.Skip()))
        return result

    def _false_goal_terms(self, roots):
        """Explicit false hit terms for source expressions that cannot run.

        Static short-circuiting may omit an operand before lowering it, but its
        declared goal still needs a formula.  Omitting the mark would let a
        missing lowering rule masquerade as unreachability.  A mark behind a
        literal-false guard keeps the goal in the encoded catalogue while
        stating the exact hit condition for this source occurrence.
        """
        roots = tuple(roots)
        key = tuple(id(root) for root in roots)
        cached = self._false_goal_cache.get(key)
        if cached is not None:
            return cached
        resolver = getattr(self, "resolver", None)
        positions = {
            ((resolver.occurrence_file(node)
              if resolver is not None else self.file),
             getattr(node, "lineno", None),
             getattr(node, "col_offset", None))
            for root in roots for node in ast.walk(root)
        }
        owners = set()
        pruned = ()
        resolver = getattr(self, "resolver", None)
        if resolver is not None:
            pruned = resolver.reachable_functions_from_occurrences(roots)
            source_function_ids = {
                id(node) for node in ast.walk(resolver.model.tree)
                if isinstance(node, ast.FunctionDef)
            }
            for function in pruned:
                if function not in self.pruned_functions:
                    self.pruned_functions.append(function)
                if id(function) in source_function_ids:
                    owners.add((function.name, function.lineno,
                                function.col_offset))
                    if self.operation_goals or self.read_goals:
                        positions.update((resolver.occurrence_file(node), node.lineno, node.col_offset)
                                         for node in resolver.execution_nodes(function)
                                         if hasattr(node, "lineno"))
        # The alias walk uses the same source loops, including ones beneath
        # a proved-empty arm/helper. Give it their zero-body fact rather than
        # forcing it to re-prove an unlowered loop. A span already recorded by
        # another live activation always wins over this pruned occurrence.
        for root in (*roots, *pruned):
            for node in ast.walk(root):
                if isinstance(node, (ast.For, ast.While)):
                    self.source_loop_spans.setdefault(id(node), (0, 0))
        sites = sorted(
            (site for site in self.sites.values()
             if ((site.file, site.line, site.col) in positions
                 or site.owner in owners)),
            key=lambda site: site.ordinal)
        command = ir.Skip()
        for site in sites:
            for outcome in site.outcomes:
                command = ir.Seq(
                    command,
                    ir.If(ir.Lit(False), ir.Mark(goal_id(site, outcome)),
                          ir.Skip()))
        for site, bindings in (*self.operation_goals.items(), *self.read_goals.items()):
            if (site.file, site.line, site.col) in positions:
                for binding in bindings:
                    command = ir.Seq(command, ir.If(ir.Lit(False), ir.Mark(binding.id), ir.Skip()))
        self._false_goal_cache[key] = command
        return command

    # There is no rule here for "this declared outcome cannot occur". There was
    # one, and nothing has called it since the loop rule stopped deciding its own
    # outcomes: a loop now emits the outcome as a test of its trip count, so the
    # goal has a site and a condition and the query decides it.
    #
    # It is deleted rather than left unused. What it did was put a goal in the
    # unreachable set on this side's word, and the core counts such a claim apart
    # and notes it on the certificate, which is the right thing for a frontend
    # that genuinely has one. But an unused rule that would be believed-with-a-
    # note the moment someone called it is a trap, and the shape of the trap is
    # that a second opinion could not have helped: a goal with no site has a hit
    # condition that is false by construction, so a query would have agreed for
    # the same reason this side did rather than an independent one. What was
    # needed was not a second opinion but a first one, and that is what emitting
    # the outcome as a test gives.

    # ── expressions ──

    def _name(self, name):
        """The name to use, after any renaming an inlined body installed.

        The owner of a name is lexical (review 48): the innermost function
        on the current scope's chain of enclosing functions that binds it,
        and the frame consulted is that owner's nearest activation. The
        step's own locals are unrenamed except where the root frame reserves
        them, and a name no chain scope binds is module-scope and stays
        itself."""
        resolver = self.resolver
        active = self.activation
        if resolver is None or active.scope is None:
            return active.renames.get(name, name)
        fn = active.scope
        while fn is not None:
            names = None
            for frame in active.frames():
                if frame.scope is fn:
                    names = frame.renames
                    break
            if names is not None and name in names:
                return names[name]
            if fn is active.root.scope:
                return name
            fn = resolver._parent.get(id(fn))
        return name

    def _require_def_seen(self, node, name, target):
        """Refuse a call to a nested def whose def statement has not yet
        executed at this program point (review 50): the owning scope's
        set of seen defs must already contain it, or Python would read an
        unbound cell (a NameError) where the IR would inline."""
        resolver = self.resolver
        if self.activation.scope is None or resolver is None:
            return
        for frame in self.activation.frames():
            if frame.scope is None:
                continue
            resolver._scope(frame.scope)
            if resolver._nested[id(frame.scope)].get(name) is target:
                if name not in frame.defined:
                    raise LoweringError(
                        node, f"call to {name} before its def has run at "
                              f"this point", code="call-before-def")
                return

    def _name_is_local(self, name):
        """Whether ``name`` is a local of the current lexical scope, so it
        is neither a foldable module constant nor the model's self."""
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        if resolver is None or scope is None:
            return False
        return resolver.is_local(scope, name)

    def _model_module_name(self, node):
        """Whether a plain-name occurrence denotes the model module.

        Every special collection path calls this before consulting
        ``self.tables``.  Otherwise a local shadow, or a free name inside an
        inlined library routine, can silently select a model-module table.
        """
        if not isinstance(node, ast.Name) or node.id in self.activation.substitutions:
            return False
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        return bool(resolver is not None and scope is not None
                    and resolver.resolves_model_module(scope, node.id))

    def expr(self, node):
        value = self._expr(node)
        if not self.read_goals or not isinstance(node, (ast.Name, ast.Attribute)):
            return value
        from ..symbolic.operation_goals import predicate_expression
        from .source_reads import ReadSite

        file = self.resolver.occurrence_file(node) if hasattr(self, "resolver") else self.file
        bindings = self.read_goals.get(ReadSite.of(node, file), ())
        if not bindings:
            return value
        kind = self._source_scalar_type_for_value(node, value)
        if kind not in (value_types.INTEGER, value_types.BOOLEAN):
            raise LoweringError(node, "source read predicates require a proved scalar value",
                                code="read-goal-types-unproved")
        value = self._capture(value)
        for binding in bindings:
            expected = value_types.BOOLEAN if binding.predicate.value.kind == "boolean" else value_types.INTEGER
            condition = predicate_expression(binding.predicate, (value,)) if kind == expected else ir.Lit(False)
            self.before(ir.If(condition, ir.Mark(binding.id), ir.Skip()))
        return value

    def _expr(self, node):
        if isinstance(node, ast.Name):
            if node.id in self.activation.substitutions:
                return self.activation.substitutions[node.id]
            structured = self._structured_value(node.id, inputs=True)
            if structured is not None:
                return structured
            optional = self._option_for_name(node.id, node)
            if optional is not None:
                return optional
            product = self._product_for_name(node.id, node)
            if product is not None:
                return product
            resolved = self._name(node.id)
            if self.storage.carrier(OrderedScalar, resolved) is not None:
                return self.storage.require_carrier(OrderedScalar, resolved)
            if self.storage.carrier(OptionSequence, resolved) is not None:
                return self.storage.require_carrier(OptionSequence, resolved)
            positional = self.storage.carrier(_PositionalBuilder, resolved)
            if positional is not None and self.flow.contracts.get(resolved) == positional.contract:
                values = tuple(self.flow.static_values.get(name) for name in positional.names)
                for status in values:
                    if isinstance(status, _StaticIssue):
                        raise LoweringError(node, "a positional field has no consistent source value",
                                            code=status.code)
                return _PositionalValue(
                    tuple(value if isinstance(value, _StaticRef) else ir.Var(name)
                          for name, value in zip(positional.names, values)),
                    positional.contracts, positional.scalar_types, positional.contract)
            batch = self.storage.carrier(_BatchBuilder, resolved)
            carrier_contract = (self._represented_batch_contract(node.id)
                                if batch is not None else UNKNOWN)
            if batch is not None and record_batch_payload(
                    carrier_contract) is None:
                carrier_contract = self.flow.contracts.get(
                    resolved, UNKNOWN)
            if batch is not None and record_batch_payload(carrier_contract) \
                    is not None:
                form = (ir.Var(batch.form) if batch.form is not None else
                        ir.Lit(_batch_form_code(BatchForm.LIST)))
                return _BatchValue(batch, form, carrier_contract)
            status = self.flow.static_values.get(self._name(node.id))
            if isinstance(status, _StaticIssue):
                raise LoweringError(
                    node, "a static collection reference is not defined "
                          "consistently on every reaching path",
                    code=status.code)
            if (isinstance(status, _StaticRef)
                    or (self._model_module_name(node)
                        and node.id in self.tables)):
                source_values = lower_static_refs.possible_values(self, node)
                if isinstance(status, _StaticRef) and source_values and all(
                        type(value) is str for value in source_values):
                    return status
                if isinstance(status, _StaticRef) and source_values:
                    scalar_type = type(source_values[0])
                    if scalar_type in (bool, int) and all(
                            type(value) is scalar_type for value in source_values):
                        # A presence guard can eliminate the None alternative
                        # of a finite lookup. Read its scalar payload, not the
                        # hidden reference tag, without evaluating the key again.
                        return lower_static_refs.lower_reference(self, node)
                if source_values and all(
                        type(value) is dict and value
                        and all(type(field) is str for field in value)
                        for value in source_values):
                    product = lower_static_refs.lower_reference(self, node)
                    if isinstance(product, RecordValue):
                        return product
                raise LoweringError(
                    node, "a finite source collection escapes the closed "
                          "lookup subset",
                    code="static-collection-escapes")
            table = (self.table_arrays.get(node.id)
                     if self._model_module_name(node) else None)
            if table is not None:
                return ir.Var(table)
            resolver = getattr(self, "resolver", None)
            scope = self.activation.scope
            if resolver is not None and scope is not None:
                kind, value = resolver.resolve_const(scope, node.id)
                if kind == "const":
                    return ir.Lit(value)
                # A remaining module binding has no scalar representation.
                # Indexed reads are deferred to the collection-shape rule so
                # a static-collection gap is reported as such, rather than as
                # an unrelated namespace failure.
                if (kind == "bound" and node.id not in self.tables
                        and node.id not in resolver.module_collections
                        and not resolver.is_collection_base(node)):
                    raise LoweringError(
                        node, f"the name {node.id} is bound at module level "
                              f"to a value the frontend cannot read here",
                        code="name-not-a-value")
            return ir.Var(self._name(node.id))
        if isinstance(node, ast.Constant):
            if node.value is None:
                return OptionValue(ir.Lit(False), None, NONE)
            if isinstance(node.value, bool):
                return ir.Lit(node.value)
            if isinstance(node.value, int):
                return ir.Lit(node.value)
            if type(node.value) is bytes:
                return ir.Op("arraylit", *(ir.Lit(item) for item in node.value))
            if type(node.value) is str:
                return _StaticRef(((ir.Lit(True), node.value),))
            raise LoweringError(node, f"literal {type(node.value).__name__}", code="literal-unsupported")
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                # `self` may be a comprehension target or other local rather
                # than the model here (review 49: [self.x for self in [0]]);
                # a rebound self is not the model.
                if "self" in self.activation.substitutions or self._name_is_local("self"):
                    raise LoweringError(
                        node, "attribute of a rebound self, which is not the "
                              "model", code="self-rebound")
                self._read_state_attribute(node)
                if node.attr in self.static_fields:
                    if type(self.static_fields[node.attr]) is str:
                        return _StaticRef(((ir.Lit(True), self.static_fields[node.attr]),))
                    raise LoweringError(
                        node, "a finite source collection escapes the closed lookup subset",
                        code="static-collection-escapes")
                # A constant the class body binds is the number it binds, not a
                # variable of the step. `self.STATE_A` reads a state machine's
                # state name, and treating it as a field made it state the step
                # carries and `reset` never assigns.
                if node.attr in self.fixed:
                    return ir.Lit(self.fixed[node.attr])
                structured = self._structured_value(node.attr)
                if structured is not None:
                    return structured
                product = self.storage.state_products.get(node.attr)
                if self.storage.carrier(OptionSequence, self.storage.state_name(node.attr)) is not None:
                    return self.storage.require_carrier(OptionSequence, self.storage.state_name(node.attr))
                if product is not None and product.contract.is_mapping:
                    return self.storage.require_carrier(MappingBuilder, self.storage.state_name(node.attr))
                if product is not None and product.contract.is_none:
                    return OptionValue(ir.Lit(False), None, NONE)
                if product is not None and product.contract.is_record:
                    return self.storage.require_carrier(_RecordBuilder, self.storage.state_name(node.attr)).value
                if product is not None and product.contract.is_sequence:
                    return _BatchValue(
                        self.storage.require_carrier(_BatchBuilder, self.storage.state_name(node.attr)),
                        ir.Lit(_batch_form_code(BatchForm.LIST)), product.contract)
                optional = self._option_for_binding(self.storage.state_name(node.attr), node)
                if optional is not None:
                    return optional
                return ir.Var(self.storage.state_name(node.attr))
            fixed = self.resolver.class_attribute(
                self.activation.scope, node, shadowed=self.activation.substitutions)
            if type(fixed) in (int, bool):
                return ir.Lit(fixed)
            raise LoweringError(node, "attribute of something other than self", code="attribute-not-self")
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.UnaryOp):
            spec = primitives.source_primitive(
                primitives.SourceFamily.UNARY, type(node.op))
            if spec is None:
                raise LoweringError(node, "unary operator", code="unary-operator-unsupported")
            op = spec.name
            if isinstance(node.op, ast.Not):
                if self._operation_bindings(node):
                    value = self.expr(node.operand)
                    return self._observe_operation(
                        node, (value,), ir.Op(op, self._truth_value(node.operand, value)),
                        (self._source_scalar_type_for_value(node.operand, value), value_types.BOOLEAN))
                return ir.Op(op, self._truth_expr(node.operand))
            value = self._integer_context_expr(node.operand)
            if isinstance(value, OrderedScalar):
                if isinstance(node.op, ast.UAdd):
                    return value
                if isinstance(node.op, ast.USub):
                    return OrderedScalar(ir.Op('neg', value.infinity), ir.Op('neg', value.payload))
                raise LoweringError(node, 'infinity has no integer unary operation',
                                    code='ordered-scalar-operation-unsupported')
            if isinstance(value, OptionValue):
                raise LoweringError(
                    node, "a numeric unary operation consumes an optional "
                          "value without unwrapping it",
                    code="optional-value-not-unwrapped")
            if isinstance(value, RecordValue):
                raise LoweringError(
                    node, "a unary operation on a fixed-schema record",
                    code="record-operation-unsupported")
            if self._shape(value):
                raise LoweringError(
                    node, "a numeric unary operator applied to a sequence",
                    code="unary-sequence-unsupported")
            return self._observe_operation(
                node, (value,), ir.Op(op, value),
                (self._fixed_source_scalar_type(node.operand), value_types.INTEGER))
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Subscript):
            return self._subscript(node)
        if isinstance(node, ast.IfExp):
            return self._ifexp(node)
        if isinstance(node, ast.BoolOp):
            return self._boolop(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, (ast.List, ast.Tuple)):
            values = self._in_order(*node.elts)
            if any(isinstance(value, OptionValue) for value in values):
                return OptionSequence.literal(self, node, values)
            if any(isinstance(value, _StaticRef) for value in values):
                raise LoweringError(
                    node, "a runtime sequence mixes numeric storage with "
                          "finite source references",
                    code="static-collection-escapes")
            if any(isinstance(value, RecordValue) for value in values):
                raise LoweringError(
                    node, "a sequence contains fixed-schema records, which "
                          "need a structure-of-arrays representation",
                    code="sequence-element-record-unsupported")
            shapes = [self._shape(value) for value in values]
            if shapes and any(shape != shapes[0] for shape in shapes[1:]):
                raise LoweringError(
                    node, "an array literal whose elements have different "
                          "recursive shapes",
                    code="array-elements-shape-inconsistent")
            value = ir.Op("arraylit", *values)
            if not any(self._length_image(item) for item in values):
                return value
            contract = self._contract_ast(node)
            if contract.is_sequence and contract.physical_shape is not None:
                # A literal can contain bounded sequences. Their lengths are
                # part of the values, not recoverable from padded IR arrays.
                specs = tuple(lower_context.dynamic_length_specs(contract))
                if specs:
                    actual = [self._contract_for_value(source, item)
                              for source, item in zip(node.elts, values)]
                    image = {
                        path: ir.Op("arraylit", *[
                            self._length_value_at(item, item_contract,
                                                  path[1:], shape[1:], source)
                            for source, item, item_contract
                            in zip(node.elts, values, actual)])
                        for path, shape, _capacity in specs
                        if path and path[0] is lower_state.SEQUENCE_ELEMENT
                    }
                    value = self._bind_expression_lengths(
                        value, image, storage=contract)
            return value
        if isinstance(node, ast.ListComp):
            return self._listcomp(node)
        if isinstance(node, ast.DictComp):
            return self._record_dict_comprehension(node)
        if isinstance(node, ast.Dict):
            return self._record_literal(node)
        raise LoweringError(node, f"expression {type(node).__name__}", code="expression-unsupported")

    def _slice(self, node):
        """Lower a finite slice, preserving Python's clipping and stride."""
        if (not isinstance(node.value, ast.Constant)
                and lower_static_refs.is_reference(self, node.value)):
            contract = self._contract_ast(node.value)
            if not (contract.is_sequence
                    and sequence_elements_are_deeply_immutable(contract)):
                raise LoweringError(node, "a closed slice may retain mutable elements",
                                    code="sequence-slice-alias-unsupported")
            array = lower_static_refs.lower_reference(self, node.value)
        else:
            array = self.expr(node.value)
        sl = node.slice
        # A bound may rebind the primary. Preserve the already evaluated
        # object using the same effect/capture rule as sibling expressions;
        # the shared alias analysis rejects mutations of that held object.
        if any(bound is not None and self._effectful(bound)
               for bound in (sl.lower, sl.upper, sl.step)):
            array = self._capture(array)
        dynamic = self._dynamic_sequence(array)
        logical_length = None
        if dynamic is not None or sl.step is not None:
            contract = self._contract_for_value(node.value, array)
            if contract.exact_length is not None:
                # A comprehension uses a length companion while it is being
                # built.  Once its source contract proves an exact completed
                # length, slicing is ordinary fixed-length slicing even if
                # that internal companion remains available to later writes.
                logical_length = contract.exact_length
                dynamic = None
        if dynamic is not None or sl.step is not None:
            contract = self._contract_for_value(node.value, array)
            if not sequence_elements_are_deeply_immutable(contract):
                raise LoweringError(
                    node, "a sequence slice may retain a mutable element "
                          "reference",
                    code="sequence-slice-alias-unsupported")
            shape = self._shape(array)
            if not shape or (dynamic is not None and shape[0] != dynamic[2]):
                raise LoweringError(
                    node, "a dynamic sequence disagrees with its physical "
                          "capacity",
                    code="sequence-plan-inconsistent")
            result_contract = self._contract_ast(node)
            result_shape = result_contract.physical_shape
            if (not result_contract.is_sequence
                    or result_contract.element is None
                    or result_shape is None
                    or result_shape[1:] != shape[1:]
                    or result_shape[0] > shape[0]):
                raise LoweringError(
                    node, "a dynamic slice has no consistent finite source "
                          "representation",
                    code="sequence-plan-inconsistent")

            sequence_length = (dynamic[1] if dynamic is not None else
                               ir.Lit(shape[0] if logical_length is None
                                      else logical_length))
            start, width, step = self._dynamic_slice_span(node, sequence_length)
            cells = []
            element = contract.element
            for index in range(result_shape[0]):
                active = ir.Op("lt", ir.Lit(index), width)
                source_index = ir.Op("add", start, ir.Lit(index * step))
                safe_index = ir.Op(
                    "ite", active, source_index, ir.Lit(0))
                cells.append(self._select_sequence_element(
                    array, safe_index, contract, node))
            result, length = self.storage.fresh(), self.storage.fresh()
            value = ir.Op("arraylit", *cells)
            self.flow.lengths[result] = result_shape
            carrier = self._carrier(array, node.value)
            if carrier is not None:
                self.flow.carriers[result] = carrier
            self.flow.contracts[result] = result_contract
            self._register_dynamic_sequence(
                result, length, result_shape[0])
            nested_lengths = self._initialize_element_lengths(
                result, cells, (element,) * len(cells),
                result_contract, node)
            self.before(ir.Seq(
                ir.Assign(result, value),
                ir.Seq(ir.Assign(length, width), nested_lengths)))
            self.storage.require_shape(result, result_shape, node)
            return ir.Var(result)
        shape = self._shape(array)
        if not shape:
            raise LoweringError(node, "slice of something with no static length", code="slice-length-not-static")
        length = shape[0] if logical_length is None else logical_length
        if length > shape[0]:
            raise LoweringError(
                node, "a sequence contract exceeds its physical capacity",
                code="sequence-plan-inconsistent")
        start, width = self._slice_span(node, length)
        return ir.Op("arraylit", *[
            ir.Sel(array, ir.Op("add", start, ir.Lit(index)))
            for index in range(width)])

    def _dynamic_slice_span(self, node, length):
        """Normalize finite integer endpoints for a proved nonzero stride.

        Omitted bounds differ from explicit -1 for a negative step. They are
        defaults, not values to shift relative to the sequence length.
        """
        sl = node.slice
        step = 1 if sl.step is None else self._static_int(sl.step)
        if step is None or step == 0:
            raise LoweringError(
                node, "slice step is not a proved nonzero integer",
                code="slice-step-not-static")
        bound_nodes = tuple(
            bound for bound in (sl.lower, sl.upper, sl.step) if bound is not None)
        lowered = iter(self._in_order(
            *bound_nodes, lower=self._integer_context_expr))
        low = ir.Lit(0 if step > 0 else -1)
        high = length if step > 0 else ir.Op("sub", length, ir.Lit(1))

        def clipped(value):
            shifted = ir.Op("add", length, value)
            negative = ir.Op("ite", ir.Op("lt", shifted, low), low, shifted)
            positive = ir.Op("ite", ir.Op("lt", high, value), high, value)
            return ir.Op("ite", ir.Op("lt", value, ir.Lit(0)),
                         negative, positive)

        start = ((low if step > 0 else high) if sl.lower is None else
                 clipped(next(lowered)))
        stop = ((high if step > 0 else low) if sl.upper is None else
                clipped(next(lowered)))
        distance = (ir.Op("sub", stop, start) if step > 0 else
                    ir.Op("sub", start, stop))
        width = ir.Op("ite", ir.Op("lt", distance, ir.Lit(0)),
                      ir.Lit(0), distance)
        if abs(step) != 1:
            width = ir.Op("fdiv", ir.Op("add", width, ir.Lit(abs(step) - 1)),
                          ir.Lit(abs(step)))
        return start, width, step

    def _slice_span(self, node, length):
        """Return the evaluated start and fixed width of one source slice."""
        sl = node.slice
        if sl.step is not None:
            raise LoweringError(
                node, "slice with a step", code="slice-with-step")
        lo = None if sl.lower is None else self._static_int(sl.lower)
        hi = None if sl.upper is None else self._static_int(sl.upper)
        if (sl.lower is None or lo is not None) \
                and (sl.upper is None or hi is not None):
            # Python owns negative-index and clipping semantics.  Using its
            # rule here also gives slice assignment the same selected span.
            start, stop, _step = slice(lo, hi).indices(length)
            return ir.Lit(start), max(0, stop - start)

        # A retained source loop can make the bounds non-constant while the
        # selected width stays exact, as in ``xs[2*i:2*i+2]``.  Correlation is
        # proved by the shared affine form; interval bounds separately prove
        # that Python cannot clip or reinterpret either endpoint.
        bound_nodes = [candidate for candidate in (sl.lower, sl.upper)
                       if candidate is not None]
        lowered = iter(self._in_order(*bound_nodes))
        lower = ir.Lit(0) if sl.lower is None else next(lowered)
        upper = ir.Lit(length) if sl.upper is None else next(lowered)
        lower_form = ranges.affine_form(lower)
        upper_form = ranges.affine_form(upper)
        width_form = ranges.affine_form(ir.Op("sub", upper, lower))
        width = (None if width_form is None or width_form[1]
                 else width_form[0])
        lower_bounds = ranges.bounds_of_expression(lower, self.flow.bounds)
        upper_bounds = ranges.bounds_of_expression(upper, self.flow.bounds)
        if (lower_form is None or upper_form is None or width is None
                or width < 0 or lower_bounds is None
                or lower_bounds.lo is None or lower_bounds.lo < 0
                or lower_bounds.hi is None or lower_bounds.hi > length
                or upper_bounds is None or upper_bounds.lo is None
                or upper_bounds.lo < 0 or upper_bounds.hi is None
                or upper_bounds.hi > length):
            raise LoweringError(
                node, "slice whose affine bounds may clip or reverse",
                code="slice-bounds-not-static")
        return lower, width

    def _ifexp(self, node, *, result_kind=None):
        """A conditional expression becomes a conditional command over a fresh
        local, so that each outcome has a place to mark its goal.

        Each arm is lowered in its own scope, since only one of them is
        evaluated and whatever the other needed to run must not.
        """
        # A closed module collection is not a first-class mapping value.  Its
        # apparent record contract describes the Python object at the source
        # level, while ``_StaticRef`` owns the lowering-time representation and
        # preserves the guarded path through that object.  Resolve that path
        # before the ordinary record/product dispatcher can claim it.
        if result_kind not in {None, "boolean", "integer"}:
            raise ValueError(f"unknown conditional result kind {result_kind!r}")
        result_contract = (self._contract_ast(node)
                           if result_kind is None else None)
        if (result_kind is None and lower_static_refs.is_reference(self, node)
                and lower_static_refs.requires_reference(self, node)):
            return lower_static_refs.lower_reference(self, node, preserve_reference=True)
        # Merely *using* a static collection in an arm does not make the
        # conditional's result a collection reference.  A table selection may
        # already have produced an ordinary scalar (for example
        # ``-ROM[-k] if k < 0 else ROM[k]``); route that through the normal
        # value join.  The static-reference join is needed only while the
        # result itself still has collection structure that cannot enter IR.
        if (result_kind is None and result_contract.kind != "scalar"
                and lower_static_refs.collection_result(self, node)):
            return lower_static_refs.lower_reference(self, node)
        if result_kind is None and result_contract.is_record:
            return self._record_ifexp(node)
        if result_kind is None and result_contract.is_option:
            return self._option_ifexp(node)
        tmp = self.storage.fresh()
        guard = self._truth_expr(node.test)
        lower_value = {
            None: self.expr,
            "boolean": self._truth_expr,
            "integer": self._integer_context_expr,
        }[result_kind]

        def scalar_type(source, value):
            if result_kind == "boolean":
                return value_types.BOOLEAN
            if result_kind == "integer":
                return value_types.INTEGER
            return self._source_scalar_type_for_value(source, value)
        truth = self._source_static_truth(node.test)
        entering = self._flow_facts()
        then_entry = self._assumed_source_flow_facts(
            entering, node.test, guard, True)
        else_entry = self._assumed_source_flow_facts(
            entering, node.test, guard, False)
        selected = truth
        if selected is None:
            if then_entry is None and else_entry is not None:
                selected = False
            elif else_entry is None and then_entry is not None:
                selected = True

        # The source-summary planner and the lowerer must agree on which
        # helper activations exist.  A declaration-proved test has only one
        # executable arm; lowering the other would ask the plan for a context
        # that Python can never enter.  The unreachable arm's goals still get
        # explicit false formulas, just as for a statically decisive Boolean
        # operand.
        if selected is not None:
            chosen = node.body if selected else node.orelse
            omitted = node.orelse if selected else node.body
            chosen_entry = then_entry if selected else else_entry
            self._set_range_facts(
                entering if chosen_entry is None else chosen_entry)
            pre, value = self._scoped(lambda: lower_value(chosen))
            shape = self._shape(value)
            contract = (self._contract_for_value(chosen, value)
                        if result_kind is None else SCALAR)
            self.storage.require_shape(tmp, shape, node)
            self._remember_source_scalar_type(
                tmp, None if shape else scalar_type(chosen, value))
            self.flow.contracts[tmp] = contract
            assign = ir.Assign(tmp, value)
            if shape:
                self.flow.lengths[tmp] = shape
                if contract.is_sequence and contract.carrier is not None:
                    self.flow.carriers[tmp] = contract.carrier
                if contract.is_sequence and contract.exact_length is None:
                    length_name = self.storage.fresh()
                    self._register_dynamic_sequence(
                        tmp, length_name, shape[0])
                    assign = ir.Seq(
                        assign,
                        ir.Assign(length_name, self._logical_length(
                            chosen, value, contract)))
            account_test = self._marked_if(
                node.test, guard, ir.Skip(), ir.Skip())
            command = ir.Seq(
                account_test,
                ir.Seq(self._false_goal_terms([omitted]),
                       ir.Seq(pre, assign)))
            self._record_range(tmp, value)
            self.before(command)
            return ir.Var(tmp)

        self._set_range_facts(then_entry)
        then_pre, then_value = self._scoped(
            lambda: lower_value(node.body))
        self._record_range(tmp, then_value)
        then_facts = self._flow_facts()

        self._set_range_facts(else_entry)
        else_pre, else_value = self._scoped(
            lambda: lower_value(node.orelse))
        self._record_range(tmp, else_value)
        else_facts = self._flow_facts()
        live = []
        if then_entry is not None:
            live.append((guard, then_facts))
        if else_entry is not None:
            live.append((ir.Op("not", guard), else_facts))
        self._set_range_facts(
            self._joined_branch_flow_facts(guard, live))
        then_shape, else_shape = self._shape(then_value), self._shape(else_value)
        if then_shape != else_shape:
            raise LoweringError(
                node, "conditional-expression arms with different recursive "
                      "representation shapes",
                code="conditional-value-shape-disagrees")
        then_contract = (self._contract_for_value(node.body, then_value)
                         if result_kind is None else SCALAR)
        else_contract = (self._contract_for_value(node.orelse, else_value)
                         if result_kind is None else SCALAR)
        contract = (join_contracts(then_contract, else_contract)
                    if result_kind is None else SCALAR)
        if contract is INCOMPATIBLE:
            raise LoweringError(
                node, "conditional-expression arms with incompatible source "
                      "value contracts",
                code="conditional-value-contract-disagrees")
        self.storage.require_shape(tmp, then_shape, node)
        then_kind = scalar_type(node.body, then_value)
        else_kind = scalar_type(node.orelse, else_value)
        self._remember_source_scalar_type(
            tmp, (then_kind if not then_shape and then_kind == else_kind
                  else None))
        self.flow.contracts[tmp] = contract
        length_name = None
        if then_shape:
            self.flow.lengths[tmp] = then_shape
            if contract.is_sequence and contract.carrier is not None:
                self.flow.carriers[tmp] = contract.carrier
            if contract.is_sequence and contract.exact_length is None:
                length_name = self.storage.fresh()
                self._register_dynamic_sequence(
                    tmp, length_name, then_shape[0])
        then_assign = ir.Assign(tmp, then_value)
        else_assign = ir.Assign(tmp, else_value)
        if length_name is not None:
            then_assign = ir.Seq(
                then_assign,
                ir.Assign(length_name, self._logical_length(
                    node.body, then_value, then_contract)))
            else_assign = ir.Seq(
                else_assign,
                ir.Assign(length_name, self._logical_length(
                    node.orelse, else_value, else_contract)))
        then = ir.Seq(then_pre, then_assign)
        orelse = ir.Seq(else_pre, else_assign)
        self.before(self._marked_if(node.test, guard, then, orelse))
        return ir.Var(tmp)

    def _option_ifexp(self, node):
        """Join ``None`` and payload arms as one tagged local product."""
        contract = self._contract_ast(node)
        guard = self._truth_expr(node.test)
        truth = self._source_static_truth(node.test)
        entering = self._flow_facts()
        then_entry = self._assumed_source_flow_facts(
            entering, node.test, guard, True)
        else_entry = self._assumed_source_flow_facts(
            entering, node.test, guard, False)
        selected = truth
        if selected is None:
            if then_entry is None and else_entry is not None:
                selected = False
            elif else_entry is None and then_entry is not None:
                selected = True
        if selected is not None:
            chosen = node.body if selected else node.orelse
            omitted = node.orelse if selected else node.body
            entry = then_entry if selected else else_entry
            self._set_range_facts(entering if entry is None else entry)
            pre, value = self._scoped(lambda: self.expr(chosen))
            option = _OptionBuilder.coerce(self, value, chosen, contract)
            self.before(ir.Seq(
                self._marked_if(node.test, guard, ir.Skip(), ir.Skip()),
                ir.Seq(self._false_goal_terms([omitted]), pre)))
            return option

        target = self.storage.fresh()
        self._set_range_facts(then_entry)
        then_pre, then_value = self._scoped(lambda: self.expr(node.body))
        then_facts = self._flow_facts()
        then_bind = self._bind_option(
            target, then_value, node.body, contract, source=node.body)
        self._set_range_facts(else_entry)
        else_pre, else_value = self._scoped(lambda: self.expr(node.orelse))
        else_facts = self._flow_facts()
        else_bind = self._bind_option(
            target, else_value, node.orelse, contract, source=node.orelse)
        live = []
        if then_entry is not None:
            live.append((guard, then_facts))
        if else_entry is not None:
            live.append((ir.Op("not", guard), else_facts))
        self._set_range_facts(
            self._joined_branch_flow_facts(guard, live))
        self.before(self._marked_if(
            node.test, guard,
            ir.Seq(then_pre, then_bind),
            ir.Seq(else_pre, else_bind)))
        return self.storage.require_carrier(_OptionBuilder, target).value

    def _record_ifexp(self, node):
        """Join a conditional product by assigning each field in its arm."""
        guard = self._truth_expr(node.test)
        truth = self._source_static_truth(node.test)
        entering = self._flow_facts()
        then_entry = self._assumed_source_flow_facts(
            entering, node.test, guard, True)
        else_entry = self._assumed_source_flow_facts(
            entering, node.test, guard, False)
        selected = truth
        if selected is None:
            if then_entry is None and else_entry is not None:
                selected = False
            elif else_entry is None and then_entry is not None:
                selected = True
        if selected is not None:
            chosen = node.body if selected else node.orelse
            omitted = node.orelse if selected else node.body
            chosen_entry = then_entry if selected else else_entry
            self._set_range_facts(
                entering if chosen_entry is None else chosen_entry)
            pre, value = self._scoped(lambda: self.expr(chosen))
            if not isinstance(value, RecordValue):
                raise LoweringError(
                    chosen, "a conditional record arm is not a record value",
                    code="record-value-shape-mismatch")
            command = ir.Seq(
                self._marked_if(node.test, guard, ir.Skip(), ir.Skip()),
                ir.Seq(self._false_goal_terms([omitted]), pre))
            self.before(command)
            return value

        self._set_range_facts(then_entry)
        then_pre, then_value = self._scoped(lambda: self.expr(node.body))
        then_facts = self._flow_facts()
        self._set_range_facts(else_entry)
        else_pre, else_value = self._scoped(lambda: self.expr(node.orelse))
        else_facts = self._flow_facts()
        live = []
        if then_entry is not None:
            live.append((guard, then_facts))
        if else_entry is not None:
            live.append((ir.Op("not", guard), else_facts))
        self._set_range_facts(self._joined_branch_flow_facts(guard, live))
        if not isinstance(then_value, RecordValue) \
                or not isinstance(else_value, RecordValue):
            raise LoweringError(
                node, "conditional record arms do not both produce records",
                code="record-value-shape-mismatch")
        contract = join_contracts(then_value.contract, else_value.contract)
        if contract is INCOMPATIBLE:
            raise LoweringError(
                node, "conditional record arms have different schemas or "
                      "field contracts",
                code="record-value-shape-mismatch")
        left = dict(record_leaves(then_value))
        right = dict(record_leaves(else_value))
        left_contracts = dict(record_leaf_contracts(then_value.contract))
        right_contracts = dict(record_leaf_contracts(else_value.contract))
        fields = []
        then_cmd, else_cmd = then_pre, else_pre
        for path, leaf_contract in record_leaf_contracts(contract):
            left_value = left[path]
            right_value = right[path]
            shape = leaf_contract.physical_shape
            if (shape is None or self._shape(left_value) != shape
                    or self._shape(right_value) != shape):
                raise LoweringError(
                    node, f"record field path {path!r} changes representation",
                    code="record-value-shape-mismatch")
            tmp = self.storage.fresh()
            self.storage.require_shape(tmp, shape, node)
            self.flow.contracts[tmp] = leaf_contract
            if shape:
                self.flow.lengths[tmp] = shape
            self._ensure_length_layout(tmp, leaf_contract, node)
            then_cmd = ir.Seq(
                then_cmd,
                ir.Seq(
                    ir.Assign(tmp, left_value),
                    self._assign_length_layout(
                        tmp, left_value, left_contracts[path],
                        leaf_contract, node.body)))
            else_cmd = ir.Seq(
                else_cmd,
                ir.Seq(
                    ir.Assign(tmp, right_value),
                    self._assign_length_layout(
                        tmp, right_value, right_contracts[path],
                        leaf_contract, node.orelse)))
            fields.append((path, ir.Var(tmp)))
        self.before(self._marked_if(
            node.test, guard, then_cmd, else_cmd))
        order = (then_value.order
                 if then_value.order == else_value.order else None)
        return record_from_leaves(contract, fields, order=order)

    def _under_short_circuit_assumptions(
            self, preceding, continuation_truth, lower):
        """Lower an operand under presence facts proved by earlier operands.

        Reaching a later operand of ``and`` proves every earlier operand true;
        reaching one of ``or`` proves them false.  A direct ``x is None`` test
        therefore unwraps a represented local Option or narrows a finite static
        reference on precisely the path where Python evaluates the later
        operand. This is a compile-time occurrence fact only: the tag remains
        in the generated IR and
        the ordinary short-circuit command still decides the run-time path.

        Numeric predicate refinement remains owned by the statement and
        conditional-expression flow machinery.  Here we retain only the
        identity fact that can be read from source without evaluating an
        operand twice.
        """
        missing = object()
        saved = {}
        saved_static = {}
        narrowed_static = {}
        changed = set()
        consistent = True
        for source in preceding:
            for operand, wants_none in self._source_none_assumptions(
                    source, continuation_truth):
                name = self._source_binding_name(operand)
                if name is None:
                    continue
                reference = self.flow.static_values.get(name)
                if isinstance(reference, _StaticRef):
                    alternatives = tuple(
                        (active, value) for active, value in reference.alternatives
                        if (value is None) == wants_none)
                    if not alternatives:
                        consistent = False
                        break
                    saved_static.setdefault(name, reference)
                    narrowed_static[name] = _StaticRef(alternatives, reference.tag)
                    self.flow.static_values[name] = narrowed_static[name]
                builder = self.storage.carrier(_OptionBuilder, name)
                if builder is None or builder.contract.payload is None:
                    continue
                desired = NONE if wants_none else builder.contract.payload
                current = self.flow.contracts.get(name, builder.contract)
                if current.is_none:
                    compatible = wants_none
                elif current.is_option or current is UNKNOWN:
                    compatible = True
                else:
                    compatible = not wants_none and contract_contains(
                        builder.contract.payload, current)
                if not compatible:
                    consistent = False
                    break
                if name not in saved:
                    saved[name] = self.flow.contracts.get(name, missing)
                self.flow.contracts[name] = desired
                changed.add(name)
            if not consistent:
                break

        def restore():
            for name in changed:
                previous = saved[name]
                if previous is missing:
                    self.flow.contracts.pop(name, None)
                else:
                    self.flow.contracts[name] = previous
            for name, previous in saved_static.items():
                # Keep an actual rebinding performed by this operand; only
                # the temporary presence refinement is scoped to the operand.
                if self.flow.static_values.get(name) is narrowed_static[name]:
                    self.flow.static_values[name] = previous

        if not consistent:
            restore()
            return lower()
        try:
            return lower()
        finally:
            restore()

    def _boolop(self, node, *, result_kind=None):
        """``and`` / ``or`` short-circuit, so they become nested conditionals
        over a fresh local and each operand keeps its own outcome goals.

        The last operand decides the result without a branch of its own, so
        its outcomes are marked by testing the value where it is evaluated.
        It is a condition of the decision like the others, and a goal only the
        others declare would make the last one unreportable.
        """
        if result_kind not in {None, "boolean", "integer"}:
            raise ValueError(f"unknown Boolean result kind {result_kind!r}")
        is_and = isinstance(node.op, ast.And)
        lower_value = {
            None: self.expr,
            "boolean": self._truth_expr,
            "integer": self._integer_context_expr,
        }[result_kind]
        continuation_truth = is_and
        operand_ranges = []
        operand_truths = []

        def lower_operand(operand):
            known_truth = self._source_static_truth(operand)
            raw = lower_value(operand)
            shape = self._shape(raw)
            kind = (value_types.BOOLEAN if result_kind == "boolean" else
                    value_types.INTEGER if result_kind == "integer" else
                    self._source_scalar_type_for_value(operand, raw))
            contract = (self._contract_for_value(operand, raw)
                        if result_kind is None else SCALAR)
            truth = self._truth_value(operand, raw)
            if known_truth is None:
                # Predicate feasibility depends only on its free variables;
                # do not copy the entire lowering environment per operand.
                predicate_bounds = {name: self.flow.bounds[name]
                                    for name in ir._reads(truth) if name in self.flow.bounds}
                if ranges.assume(truth, True, predicate_bounds) is None:
                    known_truth = False
                elif ranges.assume(truth, False, predicate_bounds) is None:
                    known_truth = True
            operand_truths.append(known_truth)
            boolean_operand = (kind == value_types.BOOLEAN or
                               self._fixed_source_scalar_type(operand) == value_types.BOOLEAN)
            operand_ranges.append(
                ranges.Bounds(int(known_truth), int(known_truth))
                if known_truth is not None and (boolean_operand or
                    known_truth is False and kind == value_types.INTEGER) else
                ranges.bounds_of_expression(raw, self.flow.bounds))
            length = (self._logical_length(operand, raw, contract)
                      if shape else None)
            return raw, shape, kind, contract, truth, length

        # Construction changes source-state metadata too. Capture each
        # operand's truth/length facts before lowering later helper effects;
        # reverse construction incorrectly applied a later clear to an
        # earlier len()/truth test. The commands remain short-circuited below.
        operands = []
        values = []
        for operand in node.values:
            operands.append(self._scoped(
                lambda o=operand, prefix=tuple(values):
                self._under_short_circuit_assumptions(
                    prefix, continuation_truth, lambda: lower_operand(o))))
            values.append(operand)
            # Decide only after this operand's once-only evaluation. Later
            # operands must not require contracts on a source path that the
            # fixed value or current numeric facts already prove empty.
            if operand_truths[-1] is not None and operand_truths[-1] != is_and:
                break

        tmp = self.storage.fresh()
        last = values[-1]
        pre, (value, result_shape, kind, result_contract, truth, length) = operands[-1]
        scalar_kinds = [kind]
        self.storage.require_shape(tmp, result_shape, node)
        self.flow.contracts[tmp] = result_contract
        result_length = self.storage.fresh() if result_shape else None
        if result_shape:
            self.flow.lengths[tmp] = result_shape
            if result_contract.is_sequence and result_contract.carrier is not None:
                self.flow.carriers[tmp] = result_contract.carrier
        assign_last = ir.Assign(tmp, value)
        if result_length is not None:
            assign_last = ir.Seq(
                assign_last,
                ir.Assign(result_length, length))
        cmd = ir.Seq(pre, assign_last)
        cmd = self._marked_atom(last, cmd, truth)
        for operand_index in reversed(range(len(values) - 1)):
            operand = values[operand_index]
            guard_pre, (raw, shape, kind, contract, truth, length) = operands[operand_index]
            if operand_truths[operand_index] is continuation_truth:
                # This operand is evaluated and tested, but cannot be the
                # returned value. Retain its effects and both condition goals
                # without inventing a differently typed, unreachable assignment
                # to the result (e.g. False or an integer).
                account = self._marked_atom(operand, guard_pre, truth)
                cmd = ir.Seq(account, cmd)
                continue
            # The operand decides the result and is the result. Python's `and`
            # and `or` are not boolean operators: they yield one of their
            # operands, so `5 or 7` is 5. Writing a truth value here made the
            # representation compute something the source does not, and neither
            # reading could see it, since both were written from this rule.
            #
            # Bound to a name of its own so that the test and the result are
            # one evaluation. Reading the operand twice would evaluate an
            # effectful operand twice.
            held = self.storage.fresh()
            scalar_kinds.append(kind)
            if shape != result_shape:
                raise LoweringError(
                    node, "Boolean operands with different recursive "
                          "representation shapes",
                    code="boolean-value-shape-disagrees")
            result_contract = (join_contracts(result_contract, contract)
                               if result_kind is None else SCALAR)
            if result_contract is INCOMPATIBLE:
                raise LoweringError(
                    node, "Boolean operands with incompatible source value "
                          "contracts", code="boolean-value-contract-disagrees")
            self.storage.require_shape(held, shape, operand)
            self.flow.contracts[held] = contract
            if shape:
                self.flow.lengths[held] = shape
                if contract.is_sequence and contract.carrier is not None:
                    self.flow.carriers[held] = contract.carrier
            keep = ir.Assign(held, raw)
            short = ir.Assign(tmp, ir.Var(held))
            if result_length is not None:
                short = ir.Seq(
                    short,
                    ir.Assign(result_length, length))
            inner = (self._marked_if(operand, truth, cmd, short,
                                     goals.CONDITION) if is_and else
                     self._marked_if(operand, truth, short, cmd,
                                     goals.CONDITION))
            inner = ir.Seq(keep, inner)
            cmd = ir.Seq(guard_pre, inner)
        self.storage.require_shape(tmp, result_shape, node)
        self._remember_source_scalar_type(
            tmp,
            (scalar_kinds[0]
             if not result_shape
             and all(kind is not None and kind == scalar_kinds[0]
                     for kind in scalar_kinds)
             else None))
        self.flow.contracts[tmp] = result_contract
        if result_shape:
            self.flow.lengths[tmp] = result_shape
            if result_contract.is_sequence and result_contract.carrier is not None:
                self.flow.carriers[tmp] = result_contract.carrier
            if result_contract.is_sequence \
                    and result_contract.exact_length is None:
                self._register_dynamic_sequence(
                    tmp, result_length, result_shape[0])
        omitted = node.values[len(values):]
        if omitted:
            cmd = ir.Seq(self._false_goal_terms(omitted), cmd)
        self.before(cmd)
        # A Boolean expression returns an operand. Preserve its value hull
        # across the temporary, using facts captured before later operands
        # can mutate their sources. In particular a decisive False remains
        # false when assigned and then used as a short-circuit guard.
        if all(interval is not None for interval in operand_ranges):
            interval = operand_ranges[0]
            for other in operand_ranges[1:]:
                interval = interval.join(other)
            self.flow.bounds[tmp] = interval
            finite = interval.finite()
            if finite is not None:
                self.flow.ranges[tmp] = finite
        return ir.Var(tmp)

    def _while_activation(self, node):
        """Initial, first-body and zero-body marks for a data-dependent while."""
        entered = self._site_goal(node, True, goals.LOOP)
        empty = self._site_goal(node, False, goals.LOOP)
        if entered is None and empty is None:
            return ir.Skip(), ir.Skip(), ir.Skip()
        seen = self.storage.fresh()
        first = ir.Op("not", ir.Var(seen))
        return (
            ir.Assign(seen, ir.Lit(False)),
            ir.If(first, ir.Seq(
                ir.Mark(entered) if entered else ir.Skip(),
                ir.Assign(seen, ir.Lit(True))), ir.Skip()),
            ir.If(first, ir.Mark(empty) if empty else ir.Skip(), ir.Skip()),
        )

    def _loop_goals(self, node, count):
        """The outcomes a loop reports, as a test of its trip count.

        A loop ran when its count is positive and did not when it is zero, and
        that is what the representation says: the outcome is a conditional on
        the count, whether or not the count is written as a number. When it is a
        number the encoder folds the test and the query proves the other outcome
        unsatisfiable, which is the same answer arrived at by something that can
        be checked.

        What this replaces was the lowering deciding, and recording the decision
        in `impossible` for the closure to believe. That was the frontend
        certifying its own unreachability, and asking the query about it could
        not help: a goal with no site has a hit condition that is false by
        construction, so the query would agree for the same reason rather than
        for an independent one. Now the goal has a site and a condition, and the
        query is doing work.
        """
        entered = self._site_goal(node, True, goals.LOOP)
        empty = self._site_goal(node, False, goals.LOOP)
        if entered is None and empty is None:
            return None
        # The count arrives as a literal, as an expression, or as a plain
        # integer, since two callers reach here by different routes. A plain
        # integer put straight into an operator is not an expression the
        # interpreter has a rule for, and eight models started failing their
        # audit on exactly that.
        if isinstance(count, int):
            ran = ir.Lit(count > 0)
        elif isinstance(count, ir.Lit):
            ran = ir.Lit(count.value > 0)
        else:
            ran = ir.Op("gt", count, ir.Lit(0))
        return ir.If(ran,
                     ir.Mark(entered) if entered else ir.Skip(),
                     ir.Mark(empty) if empty else ir.Skip())

    def _marked_atom(self, node, cmd, value):
        """Mark the outcomes of a condition evaluated without a branch."""
        gt = self._site_goal(node, True, goals.CONDITION)
        gf = self._site_goal(node, False, goals.CONDITION)
        if not gt and not gf:
            return cmd
        return ir.Seq(cmd, ir.If(value,
                                 ir.Mark(gt) if gt else ir.Skip(),
                                 ir.Mark(gf) if gf else ir.Skip()))

    def _unpacking_target_values(self, target, value, contract, *, construct):
        """Decompose one proved fixed sequence as Python unpacking does.

        The recursive source contract proves every selector and the exact
        arity.  One starred target receives the corresponding freshly
        allocated fixed list; dynamically sized unpacking remains outside the
        admitted source subset.  Comprehensions and ordinary loops share this
        value rule while retaining their own refusal names and storage rules.
        """
        if isinstance(target, ast.Name):
            return {target.id: (value, contract)}
        if not isinstance(target, (ast.Tuple, ast.List)) \
                or not contract.is_sequence \
                or contract.exact_length is None \
                or (contract.element is None and contract.components is None):
            raise _UnpackingTargetError(
                target, f"a {construct} target has no fixed tuple "
                        "destructuring contract")
        starred = [index for index, item in enumerate(target.elts)
                   if isinstance(item, ast.Starred)]
        if len(starred) > 1:
            raise _UnpackingTargetError(
                target, f"a {construct} target has more than one starred "
                        "binding")
        fixed = len(target.elts) - len(starred)
        if (not starred and contract.exact_length != len(target.elts)) \
                or starred and contract.exact_length < fixed:
            raise _UnpackingTargetError(
                target, f"a {construct} target has the wrong fixed arity")
        positional = isinstance(value, _PositionalValue)
        shape = (len(value.items),) if positional else self._shape(value)
        if not shape or shape[0] != contract.exact_length:
            raise _UnpackingTargetError(
                target, f"a {construct} item has no matching fixed tuple "
                        "representation")
        result = {}
        for index, child in enumerate(target.elts):
            if starred and index == starred[0]:
                suffix = len(target.elts) - index - 1
                stop = contract.exact_length - suffix
                fields = tuple(value.items[cell] if positional else
                               ir.Sel(value, ir.Lit(cell)) for cell in range(index, stop))
                contracts = tuple(contract.component(cell) for cell in range(index, stop))
                item_contract = ValueContract.positional("list", contracts)
                item = (self._positional_value(fields, contracts, item_contract)
                        if item_contract.components is not None else
                        ir.Op("arraylit", *fields))
                child = child.value
            else:
                source_index = (index if not starred or index < starred[0]
                                else contract.exact_length
                                - (len(target.elts) - index))
                item = (value.items[source_index] if positional else
                        ir.Sel(value, ir.Lit(source_index)))
                item_contract = contract.component(source_index)
            nested = self._unpacking_target_values(
                child, item, item_contract,
                construct=construct)
            overlap = set(result) & set(nested)
            if overlap:
                raise _UnpackingTargetError(
                    child, f"a {construct} target binds one name twice")
            result.update(nested)
        return result

    def _comprehension_target_values(self, target, value, contract):
        """Return the fixed leaf bindings of one comprehension target."""
        try:
            return self._unpacking_target_values(
                target, value, contract, construct="comprehension")
        except _UnpackingTargetError as exc:
            raise LoweringError(
                exc.node, exc.what,
                code="comprehension-target-shape-unsupported") from None

    def _loop_target_values(self, target, value, contract):
        """Return the fixed leaf bindings of one ordinary loop target."""
        try:
            return self._unpacking_target_values(
                target, value, contract, construct="loop")
        except _UnpackingTargetError as exc:
            raise LoweringError(
                exc.node, exc.what,
                code="loop-target-shape-unsupported") from None

    def _prepare_comprehension_target(self, target, sample, contract):
        """Install representation facts for one implicit-scope target."""
        proved = contracts_for_target(target, contract)
        values = self._comprehension_target_values(target, sample, contract)
        if proved is None or set(proved) != set(values):
            raise LoweringError(
                target, "a comprehension target has no fixed tuple "
                        "destructuring contract",
                code="comprehension-target-shape-unsupported")
        missing = object()
        saved = {}
        physical = {}
        for name, child in proved.items():
            saved[name] = (
                self.activation.substitutions.get(name, missing),
                self.activation.substitution_contracts.get(name, missing))
            holder = self.storage.fresh()
            physical[name] = holder
            self.activation.substitution_contracts[name] = holder
            self.flow.contracts[holder] = child
            shape = child.fixed_shape
            if child.components is not None:
                # This source binding is substituted by parallel values, never
                # allocated as an invalid heterogeneous SolverIR array.
                self._new_positional_binding(holder, values[name][0], target)
                continue
            if shape is None:
                raise LoweringError(
                    target, "a comprehension target contains a dynamically "
                            "sized value",
                    code="comprehension-target-shape-unsupported")
            self.storage.require_shape(holder, shape, target)
            if shape:
                self.flow.lengths[holder] = shape
                if child.carrier is not None:
                    self.flow.carriers[holder] = child.carrier
        return missing, saved, physical

    def _set_comprehension_target(self, target, value, contract):
        for name, (item, _child) in self._comprehension_target_values(
                target, value, contract).items():
            self.activation.substitutions[name] = item

    def _restore_comprehension_target(self, missing, saved, physical):
        for name, (old, old_contract) in saved.items():
            if old is missing:
                self.activation.substitutions.pop(name, None)
            else:
                self.activation.substitutions[name] = old
            if old_contract is missing:
                self.activation.substitution_contracts.pop(name, None)
            else:
                self.activation.substitution_contracts[name] = old_contract
        for holder in physical.values():
            builder = self.storage.retire_carrier(_PositionalBuilder, holder)
            for name in (holder,) + (() if builder is None else builder.names):
                self.flow.contracts.pop(name, None)
                self.flow.lengths.pop(name, None)
                self.flow.carriers.pop(name, None)

    def _listcomp(self, node):
        """Lower one finite list comprehension.

        Each generator uses the ordinary loop authority, so a helper parameter
        known to equal four is as static as the literal ``4``.  Nested
        generators are expanded in Python order.  When the source plan proves
        only a finite maximum, a temporary array plus its run-time length
        carries the result just like a named growing sequence.
        """
        contract = self._contract_ast(node)
        if (contract.is_sequence and contract.exact_length is None
                and contract.maximum is not None):
            result = self.storage.fresh()
            command = self._new_comprehension_sequence(
                result, node, planned=contract)
            self.before(command)
            return ir.Var(self._name(result))

        if any(gen.ifs for gen in node.generators):
            raise LoweringError(
                node, "comprehension with a run-time-selected cardinality",
                code="comprehension-filter")

        elems = []

        def expand(index):
            if index == len(node.generators):
                elems.append(self.expr(node.elt))
                return
            gen = node.generators[index]
            iteration = self._dynamic_bound(gen, node)
            count, value = iteration.count, iteration.value
            iterable_contract = self._contract_ast(gen.iter)
            length = iterable_contract.exact_length
            trip_range = ranges.of_expression(count, self.flow.ranges)
            if length is None:
                if trip_range is None or trip_range.lo != trip_range.hi:
                    raise LoweringError(
                        gen.iter, "comprehension whose length is not static",
                        code="comprehension-length-not-static")
                length = max(0, trip_range.lo)
            elif (trip_range is not None
                  and not (max(0, trip_range.lo) <= length
                           <= max(0, trip_range.hi))):
                raise LoweringError(
                    gen.iter, "the source length contract disagrees with "
                              "the lowered iterable range",
                    code="source-plan-inconsistent")
            loop_goal = self._loop_goals(gen.iter, count)
            if loop_goal is not None:
                self.before(loop_goal)
            # Python evaluates the iterable but never binds the target or
            # evaluates the element on an empty iteration.  Consequently an
            # empty iterable needs no element representation contract.
            if length == 0:
                return
            element_contract = (iterable_contract.element
                                if iterable_contract.is_sequence else None)
            if element_contract is None:
                raise LoweringError(
                    gen.iter, "a comprehension iterable has no element "
                              "contract",
                    code="comprehension-target-shape-unsupported")

            sample = (ir.Lit(0) if value is None else value(ir.Lit(0)))
            missing, saved, physical = self._prepare_comprehension_target(
                gen.target, sample, element_contract)
            try:
                for step in range(length):
                    self._spend()
                    j = ir.Lit(step)
                    item = j if value is None else value(j)
                    self._set_comprehension_target(
                        gen.target, item, element_contract)
                    expand(index + 1)
            finally:
                self._restore_comprehension_target(
                    missing, saved, physical)

        expand(0)
        return ir.Op("arraylit", *elems)

    def _return_comprehension(self, node, comp, result, *, reset=True):
        """Lower a list comprehension as an ordered transaction batch.

        A transaction batch is not a Python list encoded as one SolverIR
        value.  It is a count plus one fixed array per output field.  The
        generators decide which slots are filled, in source order, and filters
        decide whether the current candidate advances the count.
        """
        # A bounded representation only translates executions beginning in
        # its planned state layers.  If the source interpreter proves this
        # return unreachable there, its comprehension is never evaluated and
        # needs no output storage.  Keep the command total with the empty
        # result; a reached return must still carry an explicit BatchPlan and
        # pass the capacity check below.
        if (isinstance(node, ast.Return)
                and self.sequence_plan is not None
                and not self.sequence_plan.return_reached(node)):
            empty = (ir.Assign(result.count, ir.Lit(0))
                     if reset else ir.Skip())
            return ir.Seq(self._false_goal_terms([comp]), empty)
        contract = self._contract_ast(comp)
        if not contract.is_sequence:
            raise LoweringError(
                comp, "an output comprehension whose finite transaction "
                      "count is not proved",
                code="output-capacity-unproved")
        # An element behind a source-proved empty comprehension is never
        # evaluated and therefore contributes neither a transaction schema nor
        # a representation obligation.  The shared comprehension lowering
        # below still evaluates the reached iterables and filters.
        if contract.maximum > 0 and (contract.element is None
                                     or not contract.element.is_record):
            raise LoweringError(
                comp.elt, "an output comprehension element that is not a mapping",
                code="output-batch-element-unsupported")
        if contract.maximum > result.capacity:
            raise LoweringError(
                comp, f"an output comprehension can emit {contract.maximum} "
                      f"transactions but its plan carries {result.capacity}",
                code="output-capacity-underplanned")

        def transaction():
            return result._append_record(self, comp.elt)

        body = self._comprehension_command(comp, transaction)
        return (ir.Seq(ir.Assign(result.count, ir.Lit(0)), body)
                if reset else body)

    def _return_dict_comprehension(self, node, comp, result, *, reset=True):
        """Lower a source-fixed computed mapping as one transaction.

        The representation plan has already enumerated its key set from finite
        declaration-time iterables.  Payload expressions still run here, in
        Python's generator order; only the port names are compile-time facts.
        """
        keys = (None if self.sequence_plan is None else
                self.sequence_plan.computed_keys(comp))
        if keys is None:
            raise LoweringError(
                comp, "a computed output mapping with no finite key plan",
                code="output-fields-not-static")
        record_items = self._return_record_items_comprehension(
            comp, result, keys, reset=reset)
        if record_items is not None:
            return record_items
        cursor = 0
        seen = set()

        def field():
            nonlocal cursor
            if cursor >= len(keys):
                raise LoweringError(
                    comp, "the computed output plan has fewer keys than its loops",
                    code="output-key-plan-inconsistent")
            name = keys[cursor]
            cursor += 1
            if name not in result.held:
                raise LoweringError(
                    comp.key, f"computed output {name} is not declared",
                    code="outputs-disagree-across-returns")
            seen.add(name)
            physical = result._physical_value(
                self, name, comp.value, comp.value)
            return result._store_group(
                self, physical, ir.Var(result.count))

        body = self._comprehension_command(comp, field)
        if cursor != len(keys):
            raise LoweringError(
                comp, "the computed output plan has more keys than its loops",
                code="output-key-plan-inconsistent")
        if seen != set(result.names):
            missing = sorted(set(result.names) - seen)
            extra = sorted(seen - set(result.names))
            detail = (f"missing {', '.join(missing)}" if missing else
                      f"unexpected {', '.join(extra)}")
            raise LoweringError(
                comp, f"computed outputs disagree with the declaration: {detail}",
                code="outputs-disagree-across-returns")
        advance = ir.Assign(
            result.count,
            ir.Op("add", ir.Var(result.count), ir.Lit(1)))
        command = ir.Seq(body, advance)
        return (ir.Seq(ir.Assign(result.count, ir.Lit(0)), command)
                if reset else command)

    def _return_record_items_comprehension(
            self, comp, result, keys, *, reset):
        """Eliminate ``{k: f(v) for k, v in record.items()}``.

        A fixed-schema record is already a product of typed IR values; making
        its ``items`` view a run-time array would erase those types and invent
        a string-key carrier solely to unpack it again.  This boundary rule
        instead evaluates the receiver once and emits one field command in
        the record's proved Python insertion order.

        ``None`` means that the comprehension is not this source form.  Once
        the form matches, every unsupported observation is refused here rather
        than falling through to the homogeneous-sequence lowering.
        """
        if len(comp.generators) != 1:
            return None
        gen = comp.generators[0]
        receiver = _record_items_receiver(gen.iter)
        if receiver is None:
            return None
        if (not isinstance(gen.target, (ast.Tuple, ast.List))
                or len(gen.target.elts) != 2
                or not all(isinstance(item, ast.Name)
                           for item in gen.target.elts)):
            raise LoweringError(
                gen.target, "a record-items target that is not a key/value pair",
                code="comprehension-target-shape-unsupported")
        key_target, value_target = gen.target.elts
        if key_target.id == value_target.id:
            raise LoweringError(
                gen.target, "a record-items target that binds one name twice",
                code="comprehension-target-shape-unsupported")
        if not (isinstance(comp.key, ast.Name)
                and comp.key.id == key_target.id):
            raise LoweringError(
                comp.key, "a record-items output key that is not its fixed key",
                code="output-name-not-static")
        observed_key = any(
            isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Load)
            and child.id == key_target.id
            for source in (comp.value, *gen.ifs)
            for child in ast.walk(source)
        )
        if observed_key:
            raise LoweringError(
                comp, "a record-items key used as a run-time value",
                code="record-key-value-unsupported")
        if gen.ifs:
            raise LoweringError(
                gen.ifs[0], "a filtered record-items output mapping",
                code="output-fields-data-dependent")

        record = self.expr(receiver)
        if not isinstance(record, RecordValue):
            raise LoweringError(
                receiver, "a record-items receiver with no product value",
                code="record-operation-unsupported")
        if record.order is None:
            raise LoweringError(
                receiver, "a record whose insertion order is path-dependent",
                code="record-iteration-order-unproved")
        schema = tuple(name for name, _value in record.fields)
        if len(keys) != len(schema) or set(keys) != set(schema):
            raise LoweringError(
                comp, "the record-items key plan and product schema disagree",
                code="output-key-plan-inconsistent")
        if set(schema) != set(result.names):
            raise LoweringError(
                comp, "record-item outputs disagree with the declaration",
                code="outputs-disagree-across-returns")

        item_contract = None
        for _field, child in record.contract.fields:
            item_contract = (child if item_contract is None else
                             join_contracts(item_contract, child))
        if item_contract in (None, INCOMPATIBLE, UNKNOWN):
            raise LoweringError(
                receiver, "record fields with no common iteration value type",
                code="record-items-value-contract-disagrees")

        missing = object()
        name = value_target.id
        saved_value = self.activation.substitutions.get(name, missing)
        saved_contract = self.activation.substitution_contracts.get(name, missing)
        holder = self.storage.fresh()
        self.activation.substitution_contracts[name] = holder
        self.flow.contracts[holder] = item_contract
        body = None
        try:
            fields = record.by_name
            for field in record.order:
                self._spend()
                self.activation.substitutions[name] = fields[field]

                def emit(field=field):
                    physical = result._physical_value(
                        self, field, comp.value, comp.value)
                    return result._store_group(
                        self, physical, ir.Var(result.count))

                before, command = self._scoped(emit)
                command = ir.Seq(before, command)
                body = command if body is None else ir.Seq(body, command)
        finally:
            if saved_value is missing:
                self.activation.substitutions.pop(name, None)
            else:
                self.activation.substitutions[name] = saved_value
            if saved_contract is missing:
                self.activation.substitution_contracts.pop(name, None)
            else:
                self.activation.substitution_contracts[name] = saved_contract
            self.flow.contracts.pop(holder, None)

        loop_goal = self._loop_goals(gen.iter, ir.Lit(len(record.order)))
        if loop_goal is not None:
            body = loop_goal if body is None else ir.Seq(loop_goal, body)
        if body is None:
            body = ir.Skip()
        command = ir.Seq(
            body,
            ir.Assign(result.count, ir.Op(
                "add", ir.Var(result.count), ir.Lit(1))))
        return (ir.Seq(ir.Assign(result.count, ir.Lit(0)), command)
                if reset else command)

    def _comprehension_command(self, comp, emit, *, active=None):
        """Lower a finite comprehension to ordered, guarded copies.

        ``emit`` builds the command for one element after every generator
        target has been bound and every filter has passed.  This is shared by
        ordinary variable-length lists and returned transaction batches, so
        their iteration, filter, goal and evaluation-order semantics cannot
        drift apart. A short-circuit reducer supplies ``active`` so filters,
        nested iterables and elements stop together once its result is known.
        """

        def generators(index):
            if index == len(comp.generators):
                return emit()
            gen = comp.generators[index]
            iteration = self._dynamic_bound(gen, comp)
            count, bound, value = iteration.count, iteration.bound, iteration.value
            if bound <= 0:
                loop_goal = self._loop_goals(gen.iter, count)
                skipped = self._false_goal_terms(comprehension_tail(comp, index))
                return skipped if loop_goal is None else ir.Seq(loop_goal, skipped)
            iterable_contract = self._contract_ast(gen.iter)
            element_contract = (iterable_contract.element
                                if iterable_contract.is_sequence else None)
            if element_contract is None:
                raise LoweringError(
                    gen.iter, "a comprehension iterable has no element "
                              "contract",
                    code="comprehension-target-shape-unsupported")
            sample = (ir.Lit(0) if value is None else value(ir.Lit(0)))
            missing, saved, physical = self._prepare_comprehension_target(
                gen.target, sample, element_contract)
            pieces = None
            try:
                for step in range(max(0, bound)):
                    self._spend()
                    j = ir.Lit(step)
                    item_value = j if value is None else value(j)
                    self._set_comprehension_target(
                        gen.target, item_value, element_contract)
                    # A source-proved false filter prevents every later
                    # filter, inner generator and the element from being
                    # evaluated.  Determine that boundary before constructing
                    # the continuation; constructing it first made dead
                    # elements impose schemas and lowering obligations.
                    reached_filters = []
                    blocked = False
                    for condition in gen.ifs:
                        reached_filters.append(condition)
                        if self._source_static_truth(condition) is False:
                            blocked = True
                            break
                    if blocked:
                        item = self._false_goal_terms(comprehension_tail(
                            comp, index, len(reached_filters)))
                    else:
                        pre, item = self._scoped(
                            lambda i=index + 1: generators(i))
                        item = ir.Seq(pre, item)
                    # Filters are evaluated in order, and a false filter skips
                    # this candidate without skipping later candidates.
                    for condition in reversed(reached_filters):
                        cond_pre, guard = self._scoped(
                            lambda c=condition: self._truth_expr(c))
                        filtered = self._marked_if(
                            condition, guard, item, ir.Skip(),
                            goals.DECISION)
                        item = ir.Seq(cond_pre, filtered)
                    if not isinstance(count, ir.Lit):
                        item = ir.If(ir.Op("lt", j, count), item, ir.Skip())
                    elif step >= max(0, count.value):
                        continue
                    if active is not None:
                        item = ir.If(active, item, ir.Skip())
                    pieces = item if pieces is None else ir.Seq(pieces, item)
            finally:
                self._restore_comprehension_target(
                    missing, saved, physical)
            loop_goal = self._loop_goals(gen.iter, count)
            body = pieces if pieces is not None else ir.Skip()
            return body if loop_goal is None else ir.Seq(loop_goal, body)

        entering = self._flow_facts()
        before, body = self._scoped(lambda: generators(0))
        command = ir.Seq(before, body)
        # This lowerer constructs commands rather than emitting them through
        # the graph transfer. Carry the completed comprehension's effects,
        # including its selected length, into subsequent source expressions.
        self._set_joined_flow_facts(
            [self._flow_facts()], ranges.post(command, entering.ranges))
        return command

    def _structural_bindings(self, node=None, *, facts=None):
        """Source names whose values are proved fixed at this occurrence.

        Unrolled indices live in ``activation.substitutions``.  Ordinary locals instead remain
        in SolverIR and acquire abstract ranges as assignments execute.  A
        singleton range, together with an exact scalar type, is equally good
        evidence for a structural source question: after ``n = self.N``, for
        example, ``[0] * n`` and ``range(n)`` must see the same fixed ``n`` as
        the range analysis.  Project only names used by this expression; the
        physical IR names themselves are deliberately not source bindings.
        """
        finite = self.flow.ranges if facts is None else facts.ranges
        unknown = (self.flow.unknown_ranges if facts is None else
                   facts.unknown_ranges)
        bindings = {}
        for name, value in self.activation.substitutions.items():
            known = ranges.of_expression(value, finite)
            if known is not None and known.lo == known.hi:
                bindings[name] = known.lo
        if node is None or isinstance(
                node, (ast.Lambda, ast.ListComp, ast.SetComp,
                       ast.DictComp, ast.GeneratorExp)):
            return bindings

        # A nested expression scope owns its own binders.  Structural scalar
        # questions never need facts from inside one, and descending there
        # could mistake a comprehension target for an outer local with the
        # same spelling.
        pending = [node]
        while pending:
            current = pending.pop()
            if current is not node and isinstance(
                    current, (ast.Lambda, ast.ListComp, ast.SetComp,
                              ast.DictComp, ast.GeneratorExp)):
                continue
            if isinstance(current, ast.Name) \
                    and isinstance(current.ctx, ast.Load) \
                    and current.id not in bindings:
                physical = self._name(current.id)
                known = finite.get(physical)
                kind = self.source_scalar_types.get(physical)
                if (known is not None and known.lo == known.hi
                        and physical not in unknown):
                    if kind == value_types.INTEGER:
                        bindings[current.id] = known.lo
                    elif (kind == value_types.BOOLEAN
                          and known.lo in (0, 1)):
                        bindings[current.id] = bool(known.lo)
            pending.extend(ast.iter_child_nodes(current))
        return bindings

    def _planned_dynamic_refinement_fits(self, node, current, planned):
        """Whether a source postcondition fits one bounded list carrier.

        A retained loop is lowered once, so the lowering-local contract table
        describes one abstract visit rather than the loop's final logical
        length.  The source plan owns that postcondition.  It may refine the
        length only when the lowered name already has a run-time length and a
        backing array large enough for the complete planned contract; carriers
        and recursive element contracts still have to agree.
        """
        if not isinstance(node, ast.Name) \
                or not current.is_sequence or not planned.is_sequence:
            return False
        name = self._name(node.id)
        if name not in self.storage.lengths:
            return False
        dynamic = self._dynamic_sequence(ir.Var(name))
        if dynamic is None:
            return False
        _name, _length, capacity = dynamic
        storage = current.with_interval(0, capacity, current.element)
        return (storage.physical_shape == self._shape(ir.Var(name))
                and contract_contains(storage, planned))

    def _contract_ast(self, node, bindings=None, contracts=None):
        """The shared source-value contract at this exact occurrence."""

        declared = self._structured_type_at(node)
        if declared is not None:
            return lower_state.source_contract_of_value_type(declared)

        scope = self.activation.scope
        resolver = getattr(self, "resolver", None)
        context = self.activation.summary
        cache_key = ((context, node)
                     if bindings is None and contracts is None
                     and context is not None else None)
        if cache_key is not None and cache_key in self._source_contract_cache:
            return self._source_contract_cache[cache_key]

        active_bindings = self._structural_bindings(node)
        active_bindings.update(bindings or {})

        def binding_contract(candidate, name):
            current = self.flow.contracts.get(name, UNKNOWN)
            if (current.is_sequence and isinstance(candidate, ast.Name)
                    and name in self.storage.lengths
                    and self.sequence_plan is not None and scope is not None):
                planned = self.sequence_plan.expression_contract(
                    scope, candidate, context)
                if self._planned_dynamic_refinement_fits(
                        candidate, current, planned):
                    # A retained loop's postcondition applies to reads inside
                    # compound expressions too. Refining only the outer AST
                    # leaves concatenation/slicing with a one-visit length.
                    return planned
            return current

        def name_contract(candidate, bindings):
            if isinstance(candidate, ast.Name) and candidate.id in bindings:
                return contract_of_value(bindings[candidate.id])
            if (self.storage.carrier(_OptionBuilder, self._source_binding_name(candidate)) is not None
                    and self.sequence_plan is not None and scope is not None):
                planned = self.sequence_plan.expression_contract(
                    scope, candidate, context)
                if planned is not UNKNOWN:
                    return planned
            if (isinstance(candidate, ast.Name)
                    and self.storage.carrier(_BatchBuilder, self._name(candidate.id)) is not None
                    and self.sequence_plan is not None and scope is not None):
                # A batch builder has one stable physical list carrier, but
                # the source occurrence can denote None, one mapping, or a
                # list of mappings.  Reads inside the procedure need that
                # logical occurrence contract; the physical contract is used
                # only to size the parallel buffers.
                planned = self.sequence_plan.expression_contract(
                    scope, candidate, context)
                if planned is not UNKNOWN:
                    return planned
            if (isinstance(candidate, ast.Name)
                    and candidate.id in self.activation.substitution_contracts):
                return binding_contract(
                    candidate, self.activation.substitution_contracts[candidate.id])
            if (isinstance(candidate, ast.Attribute)
                    and isinstance(candidate.value, ast.Name)
                    and candidate.value.id == "self"
                    and self.storage.state_name(candidate.attr) in self.storage.declaration_contracts):
                return self.storage.declaration_contracts[self.storage.state_name(candidate.attr)]
            if (isinstance(candidate, ast.Name)
                    and resolver is not None
                    and resolver.resolves_model_module(scope, candidate.id)
                    and candidate.id in resolver.table_source_values):
                return contract_of_value(
                    resolver.table_source_values[candidate.id])
            shape = self._shape_of_name(candidate)
            name = None
            if isinstance(candidate, ast.Name):
                name = self._name(candidate.id)
            elif (isinstance(candidate, ast.Attribute)
                  and isinstance(candidate.value, ast.Name)
                  and candidate.value.id == "self"):
                name = self.storage.state_name(candidate.attr)
            if name in self.flow.contracts:
                return binding_contract(candidate, name)
            if shape:
                carrier = self.flow.carriers.get(name)
                if name in self.storage.lengths:
                    child = ValueContract.fixed(shape[1:])
                    return ValueContract.sequence(
                        carrier or "list", 0, self.storage.capacities[name], child)
                return ValueContract.fixed(shape, carrier)
            if name is None:
                return UNKNOWN
            parameters = (() if scope is None else
                          tuple(arg.arg for arg in scope.args.args))
            known_scalar = name in self.flow.ranges or name in self.fixed
            if isinstance(candidate, ast.Name):
                known_scalar |= (name in self.constants
                                 or candidate.id in parameters)
            if known_scalar:
                return SCALAR
            return UNKNOWN

        def call_contract(call, call_bindings, call_contracts):
            return self._planned_call_contract(
                call, bindings=call_bindings, contracts=call_contracts)

        def remember_scalar_contract(occurrence, answer):
            if (cache_key is not None and answer.kind in {"scalar", "none"}
                    and self.sequence_plan is not None and scope is not None):
                planned = self.sequence_plan.expression_contract(scope, occurrence, context)
                if planned.kind == answer.kind:
                    self._source_contract_cache[context, occurrence] = answer
            return answer

        def child_query(child, _bindings, _contracts, evaluate):
            # A literal's raw value contract is independent of every helper
            # activation and lexical binding. Keep the AST object so temporary
            # lowered nodes cannot collide after Python reuses an object id.
            if isinstance(child, ast.Constant):
                if child not in self._source_literal_contracts:
                    self._source_literal_contracts[child] = evaluate()
                return self._source_literal_contracts[child]
            # Reuse the existing activation-proved scalar contract for child
            # occurrences too. A surrounding list/call otherwise rewalks the
            # same arithmetic already queried on its own. Explicit binding
            # overrides still bypass this cache; mutable shapes and unknown
            # contracts are never reused by this rule.
            if cache_key is not None:
                cached = self._source_contract_cache.get((context, child))
                if cached is not None:
                    return cached
            return remember_scalar_contract(child, evaluate())

        owner = self._owner()

        def select_contract(candidate, select_bindings, _contracts):
            if isinstance(candidate.slice, ast.Slice):
                # A slice keeps the sequence carrier; collection lookup below
                # describes one element, not a freshly copied sequence.
                return UNKNOWN
            # Follow the same finite static-reference path as value lowering.
            # This matters for nested ROM reads: the outer selector no longer
            # has a module Name as its immediate base, yet its selected source
            # values still have an exact recursive contract.
            selected = lower_static_refs.possible_values(self, candidate)
            if selected:
                result = contract_of_value(selected[0])
                for value in selected[1:]:
                    result = join_contracts(
                        result, contract_of_value(value))
                return result
            base = candidate.value
            if (not isinstance(base, ast.Name)
                    or base.id in select_bindings
                    or resolver is None
                    or not resolver.resolves_model_module(scope, base.id)
                    or base.id not in resolver.table_source_values):
                return UNKNOWN
            key = static_value(
                candidate.slice, owner, select_bindings,
                budget=self._source_value_budget)
            return contract_of_collection_lookup(
                resolver.table_source_values[base.id], key,
                key_known=type(key) in (bool, int, str))

        try:
            got = contract_of_expression(
                node,
                resolve=name_contract,
                call=call_contract,
                select=select_contract,
                static_value=lambda candidate, bindings: static_value(
                    candidate, owner, bindings,
                    budget=self._source_value_budget),
                static_iterable=lambda candidate, bindings: static_iterable(
                    candidate, owner, bindings,
                    is_builtin=lambda name: (name not in bindings and
                                             self._spelling_is_builtin(name)),
                    budget=self._source_value_budget),
                is_builtin=lambda name, bindings: (
                    name not in bindings and self._spelling_is_builtin(name)),
                bindings=active_bindings, contracts=contracts,
                budget=self._source_value_budget, child_query=child_query)
            # Prefer the lowering-local contract when it is conclusive: it
            # can retain a sharper failure such as a tuple/list carrier
            # mismatch.  The source planner is authoritative only for facts
            # omitted from IR-emission substitutions, notably a fixed local
            # derived from immutable construction fields.
            incomplete = (got is UNKNOWN or (
                isinstance(got, ValueContract)
                and got.is_sequence
                and (got.element is None or got.physical_shape is None)))
            answer = got
            if (self.sequence_plan is not None and scope is not None
                    and got is not INCOMPATIBLE):
                planned = self.sequence_plan.expression_contract(
                    scope, node, context)
                # The source plan may prove a tighter logical postcondition
                # than the one-body abstraction used for a retained loop.  It
                # can refine an ordinary contract directly, or a bounded
                # dynamic carrier whose physical storage contains it.
                if (planned is not UNKNOWN
                        and lower_static_refs.is_reference(self, node)):
                    # StaticRef intentionally normalizes list and tuple
                    # carriers into immutable tuples for finite IR
                    # selection.  That representation choice is not a source
                    # contract: an inlined callee still observes the original
                    # Python carrier.  The source plan retains it exactly.
                    answer = planned
                elif (planned is not UNKNOWN
                        and (incomplete or (
                            isinstance(got, ValueContract)
                            and (contract_contains(got, planned)
                                 or self._planned_dynamic_refinement_fits(
                                     node, got, planned))))):
                    answer = planned
            return remember_scalar_contract(node, answer)
        except SourceValueBudgetExceeded as exc:
            raise LoweringError(
                exc.node,
                "source value planning exceeded the procedure budget during "
                "lowering",
                code="source-plan-budget-exceeded") from None

    def _source_static_truth(self, node, *, facts=None):
        """A declaration-proved truth value under one flow environment."""
        bindings = self._structural_bindings(node, facts=facts)
        owner = self._owner()
        try:
            return static_truth(
                node,
                value_of=lambda candidate, active: static_value(
                    candidate, owner, active,
                    budget=self._source_value_budget),
                bindings=bindings, budget=self._source_value_budget)
        except SourceValueBudgetExceeded as exc:
            raise LoweringError(
                exc.node,
                "source value planning exceeded the procedure budget during "
                "lowering",
                code="source-plan-budget-exceeded") from None

    def _source_fixed_value(self, node, bindings=None):
        """One fixed source value charged to this procedure's analysis."""
        try:
            if lower_static_refs.is_reference(self, node):
                values = lower_static_refs.possible_values(self, node)
                if values is not None and len(values) == 1:
                    return values[0]
            return static_value(
                node, self._owner(),
                self._structural_bindings(node)
                if bindings is None else bindings,
                budget=self._source_value_budget,
                is_builtin=self._spelling_is_builtin)
        except SourceValueBudgetExceeded as exc:
            raise LoweringError(
                exc.node,
                "source value planning exceeded the procedure budget during "
                "lowering",
                code="source-plan-budget-exceeded") from None

    def _source_call_arguments(self, node, bindings, contracts=None):
        """Bind source contracts and fixed facts in the callee's formal order.

        The source planner keys a helper activation by the arguments after
        Python has expanded ``*value``.  Lowering must query that same key,
        rather than keying the call by its shorter syntactic argument list.
        """
        arguments = []
        fixed = []
        for argument in node.args:
            source = (argument.value if isinstance(argument, ast.Starred)
                      else argument)
            contract = self._contract_ast(source, bindings, contracts)
            value = self._source_fixed_value(source, bindings)
            if not isinstance(argument, ast.Starred):
                arguments.append(contract)
                fixed.append(value)
                continue
            elements = fixed_positional_contracts(contract)
            if elements is None:
                return None
            if type(value) in (list, tuple) and len(value) == len(elements):
                values = tuple(value)
            else:
                values = (None,) * len(elements)
            arguments.extend(elements)
            fixed.extend(values)
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        target = None if resolver is None else resolve_helper(resolver, scope, node)
        if target is None:
            return None if node.keywords else (tuple(arguments), tuple(fixed))
        keywords = [(keyword.arg, (
            self._contract_ast(keyword.value, bindings, contracts),
            self._source_fixed_value(keyword.value, bindings)))
            for keyword in node.keywords]

        def default(name, expression):
            source = resolver.helper_default(target, name, expression)
            if source is None:
                raise CallBindingError("default has no frozen scalar representation",
                                       "call-default-unrepresented")
            return contract_of_value(source.value), source.value

        try:
            _params, bound = bind_helper_arguments(
                target, tuple(zip(arguments, fixed)), keywords,
                receiver=id(target) in resolver._method_ids, default=default)
        except CallBindingError:
            return None
        return tuple(item[0] for item in bound), tuple(item[1] for item in bound)

    def _planned_call_contract(self, node, arguments=None, bindings=None,
                               contracts=None, fixed_arguments=None):
        """The source return contract for this inlined activation.

        A call occurrence inside a helper can execute under several caller
        contracts.  Asking the plan by AST identity alone conflates those
        activations; use the same argument and fixed-value context that keyed
        the source summary.  Callers that already lowered the arguments pass
        their reconciled contracts to avoid deriving them twice.
        """
        if (self.sequence_plan is None or not isinstance(node, ast.Call)
                or not self.sequence_plan.has_call(node)):
            return UNKNOWN
        active_bindings = self._structural_bindings(node)
        active_bindings.update(bindings or {})
        source_arguments = None
        if arguments is None or fixed_arguments is None:
            source_arguments = self._source_call_arguments(
                node, active_bindings, contracts)
            if source_arguments is None:
                return UNKNOWN
        arguments = (source_arguments[0] if arguments is None
                     else tuple(arguments))
        fixed = (source_arguments[1] if fixed_arguments is None
                 else tuple(fixed_arguments))
        contract, _context = self._planned_call_activation(
            node, arguments, fixed)
        return contract

    def _lexical_name(self, owner_id, name):
        """IR name for ``name`` in the nearest active frame of ``owner``."""
        for frame in self.activation.frames():
            if frame.scope is not None and id(frame.scope) == owner_id:
                return frame.renames.get(name, name)
        return None

    def _lexical_contract(self, owner_id, name):
        resolved = self._lexical_name(owner_id, name)
        if resolved is None:
            return None, None
        contract = self.flow.contracts.get(resolved, UNKNOWN)
        if contract is UNKNOWN:
            shape = self._shape(ir.Var(resolved))
            if shape:
                carrier = self.flow.carriers.get(resolved)
                if resolved in self.storage.lengths:
                    contract = ValueContract.sequence(
                        carrier or "list", 0, self.storage.capacities[resolved],
                        ValueContract.fixed(shape[1:]))
                else:
                    contract = ValueContract.fixed(shape, carrier)
            elif resolved in self.flow.ranges or resolved in self.fixed:
                contract = SCALAR
        if contract.is_sequence and resolved in self.storage.lengths:
            length = self.flow.ranges.get(self.storage.lengths[resolved])
            if length is not None:
                lower = max(0, int(length.lo))
                upper = min(int(length.hi), self.storage.capacities[resolved])
                if lower <= upper:
                    contract = contract.with_interval(
                        lower, upper, contract.element)
        return contract, self.flow.ranges.get(resolved)

    def _planned_call_activation(self, node, contracts, fixed):
        return self.sequence_plan.select_call(
            node, contracts, fixed, self.activation.summary,
            capture_fact=self._lexical_contract)

    def _planned_callee_context(self, node, arguments, fixed_arguments=None):
        """Summary context entered by this exact inlined source call."""
        if self.sequence_plan is None:
            return None
        bindings = self._structural_bindings(node)
        if fixed_arguments is None:
            source_arguments = self._source_call_arguments(
                node, bindings)
            if source_arguments is None:
                return None
            fixed = source_arguments[1]
        else:
            fixed = tuple(fixed_arguments)
        _contract, context = self._planned_call_activation(
            node, tuple(arguments), fixed)
        return context

    def _contract_for_value(self, source, value):
        """Reconcile the source contract with the value lowering produced.

        The source analysis owns Python-only facts such as list versus tuple
        and logical length.  The lowered value owns its concrete IR shape.
        Neither may silently overwrite the other: a disagreement is a
        frontend error, while a fact known on only one side is retained.
        """
        source_contract = self._contract_ast(source)
        if isinstance(value, OrderedScalar):
            return SCALAR
        if isinstance(value, OptionSequence):
            return value.contract
        if isinstance(value, MappingBuilder):
            return value.contract
        if isinstance(value, _PositionalValue):
            if source_contract is not UNKNOWN and not contract_contains(
                    value.contract, source_contract):
                raise LoweringError(source, "source and positional value contracts disagree",
                                    code="source-value-contract-disagrees")
            return value.contract
        if (source_contract is INCOMPATIBLE
                and isinstance(value, ir.Var)
                and self.flow.contracts.get(value.name, UNKNOWN) == SCALAR
                and self.sequence_plan is not None
                and self.activation.scope):
            # The lowering-local expression walk is intentionally stateless.
            # It therefore reports ``scalar | unknown`` as incompatible even
            # when the source plan proves that the unknown arm is unreachable
            # in this activation.  Accept that refinement only when the
            # path-sensitive source plan and the independently lowered value
            # both say scalar; a sequence or optional disagreement still
            # fails closed below.
            context = self.activation.summary
            planned = self.sequence_plan.expression_contract(
                self.activation.scope, source, context)
            if planned == SCALAR:
                source_contract = SCALAR
        if isinstance(value, _BatchValue):
            lowered_contract = value.contract
            if source_contract is UNKNOWN:
                return lowered_contract
            if (source_contract is INCOMPATIBLE
                    or not contract_contains(
                        lowered_contract, source_contract)):
                raise LoweringError(
                    source, "the source expression and lowered batch value "
                            "have different contracts",
                    code="source-value-contract-disagrees")
            return source_contract
        if isinstance(value, RecordValue):
            lowered_contract = value.contract
            if source_contract is UNKNOWN:
                contract = lowered_contract
            elif (source_contract is INCOMPATIBLE
                  or not source_contract.is_record
                  or not contract_contains(lowered_contract,
                                           source_contract)):
                raise LoweringError(
                    source, "the source expression and lowered record have "
                            "different contracts",
                    code="source-value-contract-disagrees")
            else:
                contract = source_contract
            lower_checks.check_value_lengths(
                source, contract, self._length_image(value), record=True)
            return contract
        if isinstance(value, OptionValue):
            lowered_contract = value.contract
            if source_contract is INCOMPATIBLE:
                raise LoweringError(
                    source, "the source expression has incompatible value "
                            "contracts",
                    code="source-value-contract-disagrees")
            if source_contract is UNKNOWN:
                return lowered_contract
            if (lowered_contract.is_none and source_contract.is_none):
                return NONE
            if (lowered_contract.is_option
                    and contract_contains(lowered_contract,
                                          source_contract)):
                return source_contract
            raise LoweringError(
                source, "the source expression and lowered optional value "
                        "have different contracts",
                code="source-value-contract-disagrees")
        lowered_contract = (
            self.flow.contracts.get(value.name, UNKNOWN)
            if isinstance(value, ir.Var) else
            self._expression_storage_contracts.get(value, UNKNOWN))
        if source_contract is INCOMPATIBLE \
                or lowered_contract is INCOMPATIBLE:
            raise LoweringError(
                source, "the source expression has incompatible value "
                        "contracts", code="source-value-contract-disagrees")
        if source_contract is UNKNOWN:
            contract = lowered_contract
        elif lowered_contract is UNKNOWN:
            contract = source_contract
        else:
            if (not contract_contains(lowered_contract, source_contract)
                    and not self._planned_dynamic_refinement_fits(
                        source, lowered_contract, source_contract)):
                raise LoweringError(
                    source, "the source expression and lowered value have "
                            f"different contracts: {source_contract} is not "
                            f"represented by {lowered_contract}",
                    code="source-value-contract-disagrees")
            # A definitely empty source occurrence has no element from which
            # to infer a schema.  Its finite backing store nevertheless uses
            # the element contract proved for the binding as a whole (for
            # example, an initially empty pipeline queue that later appends a
            # tuple).  Carry that representation fact without changing the
            # occurrence's logical length.
            if (source_contract.is_sequence
                    and source_contract.maximum == 0
                    and source_contract.element is None
                    and lowered_contract.is_sequence
                    and lowered_contract.element is not None):
                contract = source_contract.with_interval(
                    source_contract.minimum, source_contract.maximum,
                    lowered_contract.element)
            else:
                contract = source_contract

        shape = self._shape(value)
        image = self._length_image(value)
        lower_checks.check_value_storage(
            source, contract, shape=shape, image=image,
            storage=self._storage_contract(value), shape_of=self._shape)

        if contract is UNKNOWN:
            carrier = (self.flow.carriers.get(value.name)
                       if isinstance(value, ir.Var) else None)
            contract = (ValueContract.fixed(shape, carrier)
                        if shape else SCALAR)
        return contract

    def _remember_value_contract(self, name, source, value):
        """Bind one lowered name to the reconciled recursive contract."""
        contract = self._contract_for_value(source, value)
        self.flow.contracts[name] = contract
        return contract

    def _length(self, node):
        """Number of items an iterable yields, when that is exact."""
        return self._contract_ast(node).exact_length

    def _carrier_ast(self, node):
        """The proved Python sequence carrier of ``node``, if any."""
        contract = self._contract_ast(node)
        return contract.carrier if contract.is_sequence else None

    def _carrier(self, value, source):
        """The carrier proved by evaluation, with syntax as the base case.

        Most sequence expressions disclose their carrier in their syntax.  An
        inlined helper does not: its call expression says only which function
        ran.  In that case the fresh result variable carries the contract
        merged from the helper's return arms.
        """
        if isinstance(value, ir.Var) and value.name in self.flow.carriers:
            return self.flow.carriers[value.name]
        return self._carrier_ast(source)

    def _flow_facts(self):
        """Snapshot the facts used to justify later lowering decisions."""
        return self.flow.snapshot()

    def _fact_name_snapshot(self):
        """Names visible before entering one inlined source activation."""
        return {
            "flow": self.flow.names(),
            "source_scalar_types": frozenset(self.source_scalar_types),
            "shared": frozenset(self.shared),
        }


    def _discard_inlined_local_facts(self, before, retained):
        """Drop facts whose source lifetime ended with an inlined call.

        The emitted IR still declares and types those compiler locals from its
        assignments.  They must not, however, remain in the caller's abstract
        environment: copying every dead local at every later branch makes a
        finite loop of helper calls quadratic in the amount of emitted IR.
        """
        self.flow.retain_names(before["flow"], retained)
        keep = before["source_scalar_types"] | retained
        for name in tuple(self.source_scalar_types):
            if name not in keep:
                del self.source_scalar_types[name]
        # Readonly formal aliases die with their activation. Caller-visible
        # names, state and returned carriers remain live, including a helper
        # that first creates their sharing relation during this call.
        live = set().union(*before["flow"].values(), before["source_scalar_types"],
                           before["shared"], retained, self.storage.state_names.values())
        for name, group in tuple(self.shared.items()):
            if name not in live:
                del self.shared[name]
            else:
                group.intersection_update(live)

    def _set_flow_facts(self, facts):
        """Continue lowering from exactly ``facts``."""
        self.flow.restore(facts)

    def _set_range_facts(self, facts):
        """Install expression-local numeric and static-reference facts.

        Conditional expressions historically build source-value contracts for
        both arms in one expression scope.  Their numeric environments still
        fork, but resetting unrelated contract tables between the arms would
        turn compiler temporaries absent from one arm into incompatible source
        values.  Static references are also control-flow facts: after
        ``x is None`` the selected arm must see only the corresponding finite
        alternatives.  Statement branches use the full flow boundary above.
        """
        self.flow.restore_expression(facts, self.storage.carrier_names(_OptionBuilder))

    @classmethod
    def _source_none_assumptions(cls, source, truth):
        """Presence facts guaranteed by one source predicate outcome.

        A false ``a or b`` means both operands are false, and a true
        ``a and b`` means both are true.  The opposite polarities are
        disjunctions and establish no operand fact by themselves.  This small
        source rule complements the numeric IR range domain, which cannot
        represent ``None``.
        """
        identity = none_test(source)
        if identity is not None:
            return ((identity.operand,
                     identity.none_when_true == truth),)
        if isinstance(source, ast.UnaryOp) \
                and isinstance(source.op, ast.Not):
            return cls._source_none_assumptions(source.operand, not truth)
        if isinstance(source, ast.BoolOp):
            conjunctive = (
                isinstance(source.op, ast.And) and truth
                or isinstance(source.op, ast.Or) and not truth)
            if conjunctive:
                return tuple(
                    fact
                    for operand in source.values
                    for fact in cls._source_none_assumptions(operand, truth))
        return ()

    def _assumed_source_flow_facts(self, facts, source, predicate, truth):
        """Refine one branch using both IR ranges and source presence.

        The numeric range domain does not describe ``None``.  For a represented
        option, the selected arm nevertheless has an exact value contract:
        ``None`` on the absent arm and the payload contract on the present arm.
        A lowering-only static reference similarly has no source object in
        SolverIR, so retain only the alternatives selected by the same exact
        comparison.
        """
        # The source planner can prove a predicate from construction values and
        # capture-sensitive helper facts that have no single scalar counterpart
        # in the IR range domain.  That proof already selects expression arms
        # and return plans elsewhere in this lowerer; use the same authority at
        # statement branches so an unreachable body is not lowered under a
        # source activation that does not exist.
        # ``facts`` is the arm's common entry, not necessarily the lowerer's
        # ambient environment.  The true arm may already have been lowered
        # when the false arm is queried.  Reading ``self.flow.ranges`` here would then
        # use facts established only by the true arm to declare the false arm
        # unreachable.
        known = self._source_static_truth(source, facts=facts)
        if known is not None and known is not truth:
            return None
        if self.sequence_plan is not None and self.activation.scope:
            context = self.activation.summary
            feasible = self.sequence_plan.branch_feasible(
                self.activation.scope, source, truth, context)
            if feasible is False:
                return None
        narrowed = flow_facts.assume(facts, predicate, truth)
        if narrowed is None:
            return None
        contracts = dict(narrowed.contracts)
        static_values = dict(narrowed.static_values)
        for operand, wants_none in self._source_none_assumptions(
                source, truth):
            name = self._source_binding_name(operand)
            if name is None:
                continue
            builder = self.storage.carrier(_OptionBuilder, name)
            if builder is not None:
                current = contracts.get(name, builder.contract)
                if current.is_none:
                    if not wants_none:
                        return None
                elif current.is_option or current is UNKNOWN:
                    contracts[name] = (
                        NONE if wants_none else builder.contract.payload)
                elif (wants_none
                      or not contract_contains(builder.contract.payload,
                                               current)):
                    return None
            elif self.storage.carrier(_BatchBuilder, name) is not None:
                # Internal transaction batches use count-plus-field storage,
                # while source control observes the original Python union.
                source_batch = self._binding_batch_contract(operand.id)
                if source_batch is not None:
                    admits_none = BatchForm.NONE in source_batch.forms
                    admits_value = bool(
                        source_batch.forms - {BatchForm.NONE})
                    if (wants_none and not admits_none) \
                            or (not wants_none and not admits_value):
                        return None
                else:
                    # Legacy single-carrier plans have no batch-domain entry.
                    current = contracts.get(name, UNKNOWN)
                    if current.is_none:
                        if not wants_none:
                            return None
                    elif current.is_option and current.payload is not None:
                        contracts[name] = (
                            NONE if wants_none else current.payload)
                    elif current.is_known and wants_none:
                        return None
            reference = static_values.get(name)
            if isinstance(reference, _StaticRef):
                alternatives = tuple(
                    (active, value)
                    for active, value in reference.alternatives
                    if (value is None) == wants_none)
                if not alternatives:
                    return None
                static_values[name] = _StaticRef(
                    alternatives, reference.tag)
        return flow_facts.FlowFacts(
            narrowed.lengths, narrowed.carriers, contracts,
            narrowed.ranges, narrowed.bounds, narrowed.unknown_ranges,
            static_values)

    def _set_joined_flow_facts(self, states, ranges_override=None):
        """Install a control-flow join, optionally with authoritative ranges."""
        self.flow.merge(states, ranges_override)

    def _joined_branch_flow_facts(self, guard, arms):
        """Join labelled conditional arms, retaining compatible references."""
        states = [facts for _condition, facts in arms]
        joined = flow_facts.join(states)
        if len(arms) < 2:
            return joined
        names = set().union(*(facts.static_values for _, facts in arms))
        static_values = dict(joined.static_values)
        for name in names:
            values = [facts.static_values.get(name) for _, facts in arms]
            if values[0] is not None and all(
                    value == values[0] for value in values):
                continue
            if all(isinstance(value, _StaticRef) for value in values):
                merged = _StaticRef.merge_bound(values)
                if merged is not None:
                    static_values[name] = merged
                    continue
                self._spend(max(
                    1, sum(len(value.alternatives) for value in values)))
                alternatives = []
                for (condition, _facts), value in zip(arms, values):
                    alternatives.extend(
                        (ir.Op("land", condition, active), payload)
                        for active, payload in value.alternatives)
                static_values[name] = _StaticRef(tuple(alternatives))
        return flow_facts.FlowFacts(
            joined.lengths, joined.carriers, joined.contracts,
            joined.ranges, joined.bounds, joined.unknown_ranges, static_values)

    @staticmethod
    def _reject_loop_carried_static_references(node, entering, exits, maximum):
        """Refuse a lowering-only reference that a retained loop can change.

        Static collection references are frontend facts, not IR state.  A
        retained loop executes its body repeatedly at run time, whereas the
        frontend visits that body once.  Carrying a changed reference into a
        second iteration would therefore give the next iteration the wrong
        frontend fact.  Until finite collection choices have an explicit IR
        tag, changing one across a loop with more than one possible trip is a
        typed subset boundary rather than an inferred value.
        """
        if maximum is not None and maximum <= 1:
            return
        states = [entering, *exits]
        names = set().union(*(state.static_values for state in states))
        for name in sorted(names):
            values = [state.static_values.get(name) for state in states]
            if not any(isinstance(value, (_StaticRef, _StaticIssue))
                       for value in values):
                continue
            refs = [value for value in values if isinstance(value, _StaticRef)]
            # An encoded local is genuine IR state within this procedure step;
            # the semantic loop carries its tag just like any scalar local.
            if (refs and all(ref.tag == name for ref in refs)
                    and all(value is None or isinstance(value, _StaticRef)
                            for value in values)):
                continue
            if any(value != values[0] for value in values[1:]):
                raise LoweringError(
                    node,
                    f"the static collection reference {name} changes across "
                    "a retained loop",
                    code="static-reference-loop-carried")

    @classmethod
    def _loop_exit_facts(cls, entering, body, ways, breaks, continues,
                         *, may_be_empty):
        """Abstract states by which a loop can reach its following command."""
        iteration_states = list(continues)
        if NORMAL in ways:
            iteration_states.append(body)
        exits = list(breaks)
        if may_be_empty:
            exits.append(entering)
        if iteration_states:
            exits.append(flow_facts.join(iteration_states))
        return exits

    def _owner(self):
        """The one owner-aware constant authority for structural analysis in
        the current scope (review 51): the same resolution the value fold
        uses, honouring the current substitution, so a bare name folds its
        module's constant, a `self.X` the chosen class's, and a
        comprehension or loop local nothing."""
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        if resolver is not None and scope is not None:
            resolved = resolver.owner_of(scope, self.activation.substitutions)

            def owner(node):
                # ``fixed`` contains authenticated construction values for
                # instance fields no step can write. It is already the
                # authority used by ordinary expression lowering; structural
                # consumers must see that same fact.
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "self"
                        and node.attr in self.fixed):
                    return self.fixed[node.attr]
                return resolved(node)

            return owner
        consts = self.constants

        def owner(node):
            if isinstance(node, ast.Attribute) \
                    and isinstance(node.value, ast.Name) \
                    and node.value.id == "self":
                if node.attr in self.fixed:
                    return self.fixed[node.attr]
                return consts.get(node.attr)
            if isinstance(node, ast.Name):
                return None if node.id in self.activation.substitutions \
                    else consts.get(node.id)
            return None

        return owner

    def _static_int(self, node):
        """An integer fixed at this source occurrence, or nothing.

        Declaration constants come from the owner-aware authority.  Loop and
        comprehension indices are fixed by the current unrolled copy and live
        in ``activation.substitutions`` instead.  Structural consumers such as slice bounds and
        range strides need both sources through one rule.
        """
        value = self._source_fixed_value(node)
        return value if type(value) is int else None

    def _shape_of_name(self, node):
        """Dimensions of what a name stands for, at this point in the step."""
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                return self.flow.lengths.get(self.storage.state_name(node.attr), ())
            return ()
        name = self._name(node.id)
        table = self.table_arrays.get(node.id)
        if table is not None:
            return self.flow.lengths.get(table, ())
        if name in self.flow.lengths:
            return self.flow.lengths[name]
        if name in self.storage.capacities:
            return (self.storage.capacities[name],)
        return ()

    def _sequence_table_expr(self, value):
        """A uniform finite sequence ROM as a SolverIR array value.

        Nested uniform sequences remain nested local values; only nested
        *state* is outside the current state encoding.  Heterogeneous rows use
        the guarded static-collection path instead, which can keep their
        different shapes separate until a scalar or flat record is selected.
        """
        if type(value) in (bool, int):
            return ir.Lit(value)
        if not isinstance(value, tuple):
            return None
        elements = [self._sequence_table_expr(item) for item in value]
        if any(element is None for element in elements):
            return None
        shapes = [self._shape(element) for element in elements]
        if shapes and any(shape != shapes[0] for shape in shapes):
            return None
        return ir.Op("arraylit", *elements)

    def _marked_if(self, test_node, guard, then, orelse,
                   family=goals.DECISION):
        """A conditional whose arms mark the goals of the site at ``test_node``."""
        gt = self._site_goal(test_node, True, family)
        gf = self._site_goal(test_node, False, family)
        if gt:
            then = ir.Seq(ir.Mark(gt), then)
        if gf:
            orelse = ir.Seq(ir.Mark(gf), orelse)
        return ir.If(guard, then, orelse)

    def _grows(self, name):
        """Whether the step appends to ``name`` anywhere."""
        scope = self.activation.scope
        if self.sequence_plan is not None and scope is not None:
            context = self.activation.summary
            return self.sequence_plan.grows(scope, name, context)
        return self.appends.get(name, 0) > 0

    def _sequence_mutates(self, name):
        """Whether append/pop can change ``name`` after its construction."""

        scope = self.activation.scope
        if self.sequence_plan is not None and scope is not None:
            context = self.activation.summary
            return self.sequence_plan.mutates(scope, name, context)
        return self.appends.get(name, 0) > 0

    def _sequence_spec(self, name):
        scope = self.activation.scope
        context = self.activation.summary
        return (None if self.sequence_plan is None or scope is None
                else self.sequence_plan.spec(scope, name, context))

    def _new_sequence(self, name, literal, *, planned=None, element_zero=None):
        """Allocate a finite source list and its exact run-time length.

        Capacity is a source-plan upper bound.  Length is what this execution
        has actually appended, so reads and loops do not mistake reserved cells
        for source elements.
        """
        if planned is not None and (
                not planned.is_sequence or planned.maximum is None
                or planned.carrier != "list"):
            raise LoweringError(
                literal, "a planned comprehension does not denote one "
                         "finite list representation",
                code="dynamic-helper-contract-unplanned")
        spec = None if planned is not None else self._sequence_spec(name)
        capacity = (planned.maximum if planned is not None else
                    spec.capacity if spec is not None else self.appends[name])
        target = self._name(name)
        if self._sequence_mutates(name):
            self.storage.mutable_sequence_lengths.add(target)
        if target in self.storage.lengths:
            length = self.storage.lengths[target]
            if self.storage.capacities[target] != capacity:
                raise LoweringError(
                    literal, f"the finite plan for {name} changes from "
                             f"{self.storage.capacities[target]} to {capacity}",
                    code="sequence-plan-inconsistent")
        else:
            length = self.storage.fresh()
            self._register_dynamic_sequence(target, length, capacity)
        fields = (() if planned is not None or self.sequence_plan is None else
                  self.sequence_plan.batch_fields(
                      self.activation.scope, name))
        # Every finite list of records needs the same structure-of-arrays
        # carrier, even when it is an internal algorithmic value rather than
        # a transaction-boundary batch.  ``is_batch`` records source carrier
        # alternatives (None/mapping/list); it is not a representation gate.
        if (not fields and spec is not None and spec.element is not None
                and spec.element.is_record):
            fields = tuple(spec.element.field_contracts)
        if fields:
            if self.storage.carrier(_BatchBuilder, target) is not None:
                builder = self.storage.require_carrier(_BatchBuilder, target)
                if set(builder.buffers) != set(fields):
                    raise LoweringError(
                        literal, f"the transaction fields of {name} change",
                        code="outputs-disagree-across-returns")
            else:
                builder = _BatchBuilder(
                    length, {field: self.storage.fresh() for field in fields},
                    capacity)
                self.storage.bind_carrier(target, builder)
            contract = self._represented_batch_contract(name)
            if record_batch_payload(contract) is None:
                contract = ValueContract.sequence(
                    "list", 0, capacity, spec.element)
            self.flow.contracts[target] = contract
            cmd = builder.initialize(self, spec.element, literal)
            if literal.elts:
                for element in literal.elts:
                    cmd = ir.Seq(cmd, self._append_batch_value(
                        target, element, literal, guard=False))
            return cmd

        if self.storage.carrier(_BatchBuilder, target) is not None:
            raise LoweringError(
                literal, f"{name} changes between transactions and scalar values",
                code="sequence-element-kind-changed")

        values = self._in_order(*literal.elts)
        if len(values) > capacity:
            raise LoweringError(
                literal, f"a list starts with {len(values)} elements but its "
                         f"finite plan carries {capacity}",
                code="sequence-plan-too-small")
        planned_element = (planned.element if planned is not None else
                           None if spec is None else spec.element)
        planned_shape = (None if planned_element is None else
                         planned_element.physical_shape)
        actual_shapes = [self._shape(value) for value in values]
        if actual_shapes and any(shape != actual_shapes[0]
                                 for shape in actual_shapes[1:]):
            raise LoweringError(
                literal, f"the elements of {name} have different shapes",
                code="sequence-element-shape-disagrees")
        elem_shape = (planned_shape if planned_shape is not None
                      else actual_shapes[0] if actual_shapes else ())
        if any(shape != elem_shape for shape in actual_shapes):
            raise LoweringError(
                literal, f"the finite plan for {name} has element shape "
                         f"{elem_shape}, but its initializer has "
                         f"{actual_shapes[0] if actual_shapes else ()}",
                code="sequence-element-shape-disagrees")
        actual_element = None
        actual_contracts = []
        for source, value in zip(literal.elts, values):
            got = self._contract_for_value(source, value)
            actual_contracts.append(got)
            actual_element = (got if actual_element is None else
                              join_contracts(actual_element, got))
        if actual_element is INCOMPATIBLE:
            raise LoweringError(
                literal, f"the elements of {name} have incompatible source "
                         "value contracts",
                code="sequence-element-shape-disagrees")
        if (planned_element is not None and actual_element is not None
                and not contract_contains(planned_element, actual_element)):
            raise LoweringError(
                literal, f"the initializer of {name} disagrees with its "
                         "planned element contract",
                code="source-value-contract-disagrees")
        # An empty initializer has no observed element.  The scalar placeholder
        # is a physical default only: every later append/comprehension emission
        # is checked against it before the cell is stored, so an array element
        # cannot be admitted by this fallback.
        element = planned_element or actual_element or SCALAR

        storage_capacity = max(capacity, 1)
        previous_shape = self.flow.lengths.get(target)
        full_shape = (storage_capacity,) + elem_shape
        if previous_shape is not None and previous_shape != full_shape:
            raise LoweringError(
                literal, f"the finite plan for {name} changes shape from "
                         f"{previous_shape} to {full_shape}",
                code="sequence-element-shape-disagrees")
        self.storage.require_shape(target, full_shape, literal)
        self.flow.lengths[target] = full_shape
        self.flow.carriers[target] = "list"
        sequence_contract = ValueContract.sequence(
            "list", len(values), capacity, element)
        self.flow.contracts[target] = sequence_contract
        self._ensure_length_layout(target, sequence_contract, literal)
        if (element_zero is None and not elem_shape and values
                and all(_fixed_expression_type(value, self.source_scalar_types.get)
                        == value_types.BOOLEAN for value in values)):
            element_zero = ir.Lit(False)
        command = ir.Assign(
            target,
            ir.Op(
                "arrayfill", (lower_returns.zero_shape(elem_shape) if element_zero is None
                              else element_zero),
                ir.Lit(storage_capacity)))
        for index, value in enumerate(values):
            command = ir.Seq(
                command, ir.Store(target, ir.Lit(index), value))
        command = ir.Seq(command, ir.Assign(length, ir.Lit(len(values))))
        nested = self._initialize_element_lengths(
            target, values, actual_contracts, sequence_contract, literal)
        # Capacity is the representation envelope, not the length at this
        # program point. Transfer the actual constructor assignment just as
        # append/pop transfer their length updates.
        self._record_range(length, ir.Lit(len(values)))
        self._rebind(target, literal)
        return self._preserve_iterator_bindings(
            target, literal, ir.Seq(command, nested))

    def _new_comprehension_sequence(self, name, comp, *, planned=None,
                                    convert_element=None):
        """Build a finite sequence whose exact length a comprehension decides."""
        empty = ast.List(elts=[], ctx=ast.Load())
        ast.copy_location(empty, comp)
        # Padding is not a logical element, but its IR type must agree with
        # every stored element. Comparisons and other proved Boolean source
        # expressions must not be initialized with integer zero.
        element_zero = (ir.Lit(0) if convert_element is not None else
                        ir.Lit(False) if self._fixed_source_scalar_type(comp.elt)
                        == value_types.BOOLEAN else None)
        init = self._new_sequence(name, empty, planned=planned,
                                  element_zero=element_zero)
        target = self._name(name)

        if self.storage.carrier(_BatchBuilder, target) is not None:
            def emit():
                return self._append_batch_value(
                    target, comp.elt, comp, guard=False)
        else:
            length = self.storage.lengths[target]

            def emit():
                value = self.expr(comp.elt)
                if convert_element is not None:
                    value = convert_element(value)
                actual = self._contract_for_value(comp.elt, value)
                expected = self.flow.lengths[target][1:]
                if self._shape(value) != expected:
                    raise LoweringError(
                        comp.elt, "a comprehension changes the recursive "
                                  "element shape of its sequence",
                        code="sequence-element-shape-disagrees")
                nested = self._store_element_lengths(
                    target, ir.Var(length), value, actual,
                    self.flow.contracts[target], comp.elt)
                return ir.Seq(
                    ir.Store(target, ir.Var(length), value),
                    ir.Seq(nested, ir.Assign(
                        length,
                        ir.Op("add", ir.Var(length), ir.Lit(1)))))

        return ir.Seq(init, self._comprehension_command(comp, emit))

    def _batch_builder(self, name, node):
        """The parallel-array representation of a planned output value."""
        target = self._name(name)
        spec = self._sequence_spec(name)
        if spec is None or not spec.is_batch or not spec.fields:
            raise LoweringError(
                node, f"{name} has no finite output-value plan",
                code="output-value-plan-missing")
        if self.storage.carrier(_BatchBuilder, target) is None:
            self.storage.bind_carrier(target, _BatchBuilder(
                self.storage.fresh(),
                {field: self.storage.fresh() for field in spec.fields},
                spec.capacity, form=self.storage.fresh()))
        return target, self.storage.require_carrier(_BatchBuilder, target)

    def _record_fields(self, name):
        scope = self.activation.scope
        context = self.activation.summary
        return (() if self.sequence_plan is None or scope is None else
                self.sequence_plan.record_fields(scope, name, context))

    def _structured_input_binding(self, name):
        """The root procedure input denoted by ``name`` at this activation.

        Input declarations belong to the root procedure's parameters, not to
        every lexical binding with the same spelling.  In particular, an
        inlined helper parameter named like a public input is its own local;
        a nested closure that captures the root parameter still denotes the
        input.  Keep that ownership decision beside the binding lookup so
        reads, writes and type queries cannot disagree.
        """
        binding = self.storage.structured_input_views.get(
            name, self.storage.structured_input_bindings.get(name))
        if binding is None or name in self.activation.substitutions:
            return None
        if (binding.value_type.kind in ("boolean", "integer")
                and binding.leaf_bindings[0][1].indices == ()
                and name in self.storage.structured_input_views
                and self._name(name) != binding.leaf_bindings[0][1].variable):
            # A scalar parameter has been rebound into a new lexical live
            # range. Its entry snapshot remains a typed local, not the value
            # or declared type of the new source binding.
            return None
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        if resolver is None or scope is None:
            return binding
        return (binding if resolver.local_owner(scope, name) is self.activation.root.scope
                else None)

    def _structured_storage_value(self, name, *, inputs=False):
        """Return every physical leaf of one declared logical value."""
        if inputs:
            binding = self._structured_input_binding(name)
        else:
            binding = self.storage.structured_bindings.get(name)
        if binding is None:
            return None

        def read(path):
            value = ir.Var(path.variable)
            for index in path.indices:
                value = ir.Sel(value, ir.Lit(index))
            return value

        return _StructuredValue(
            binding.value_type,
            tuple((logical, ir.Var(solver.variable))
                  if not solver.indices else (logical, read(solver))
                  for logical, solver in binding.leaf_bindings),
        )

    def _structured_value(self, name, *, inputs=False):
        """Return the source expression represented by a declared state value.

        Records and arrays remain products while a selector path is being
        lowered. Boolean and Integer roots expose their scalar leaves;
        enumeration leaves retain finite source strings, never ordinal meaning.
        """
        value = self._structured_storage_value(name, inputs=inputs)
        if value is None:
            return None
        if value.value_type.kind in ("boolean", "integer", "enumeration"):
            return self._structured_scalar(value.value_type, value.by_path[()])
        return value

    @staticmethod
    def _structured_scalar(value_type, value):
        """Decode an enumeration leaf to the existing finite-reference carrier."""
        if value_type.kind == "enumeration":
            return _StaticRef(tuple(
                (ir.Op("eq", value, ir.Lit(tag)), member)
                for member, tag in value_type.tag_map))
        return value

    @staticmethod
    def _structured_root(node):
        root = node
        while isinstance(root, ast.Subscript):
            root = root.value
        if (isinstance(root, ast.Attribute)
                and isinstance(root.value, ast.Name)
                and root.value.id == "self"):
            return root.attr
        return None

    def _structured_input_root(self, node):
        root = node
        while isinstance(root, ast.Subscript):
            root = root.value
        return (root.id if isinstance(root, ast.Name)
                and self._structured_input_binding(root.id) is not None
                else None)

    @classmethod
    def _structured_project(cls, value, part):
        kind = value.value_type
        if kind.kind == "record":
            children = dict(kind.fields)
            if type(part) is not str or part not in children:
                return None
            child = children[part]
            prefix = (part,)
        elif kind.kind == "array" and type(part) is int:
            if not 0 <= part < kind.cells:
                return None
            child = kind.element
            prefix = (part,)
        else:
            return None
        rows = tuple((path[1:], expr) for path, expr in value.leaves
                     if path[:1] == prefix)
        if child.kind in ("boolean", "integer", "enumeration"):
            return cls._structured_scalar(child, dict(rows)[()])
        return _StructuredValue(child, rows)

    def _structured_select(self, node, value):
        """Select one declared record field or fixed-array element."""
        value_type = value.value_type
        if value_type.kind == "record":
            if (not isinstance(node.slice, ast.Constant)
                    or type(node.slice.value) is not str):
                raise LoweringError(
                    node.slice, "a declared record selection needs one fixed "
                                "string field name",
                    code="record-key-not-static")
            field = node.slice.value
            selected = self._structured_project(value, field)
            goal = self._safety_goal(node, "indexerror")
            if selected is None:
                self._guard(ir.Lit(True), goal, "KeyError", node)
                return ir.Lit(0)
            self._discharge(goal, f"{field!r} is a field of the declared record")
            return selected
        if value_type.kind != "array":
            raise LoweringError(
                node, "a scalar declared value is subscripted",
                code="structured-value-not-indexable")
        raw = self._integer_context_expr(node.slice)
        # Reuse the one Python-index rule and its safety goal.  The dummy value
        # supplies only the declared fixed extent; no runtime contents are
        # inspected to obtain the shape.
        dummy = ir.Op("arraylit", *[ir.Lit(0)] * value_type.cells)
        index = self._index(node, dummy, raw)
        child_type = value_type.element
        child_paths = tuple(
            leaf.path for leaf in layout_of_value_type(child_type).leaves())
        rows = []
        values = value.by_path
        for child_path in child_paths:
            selected = values[(value_type.cells - 1,) + child_path] \
                if value_type.cells else None
            for position in reversed(range(max(0, value_type.cells - 1))):
                selected = ir.Op(
                    "ite", ir.Op("eq", index, ir.Lit(position)),
                    values[(position,) + child_path], selected)
            if selected is None:
                # A zero-cell array cannot be selected on a normally completing
                # path; keep a well-typed unreachable image after the guard.
                leaf = next(leaf for leaf in
                            layout_of_value_type(child_type).leaves()
                            if leaf.path == child_path)
                selected = ir.Lit(
                    False if leaf.semantic_type == value_types.BOOLEAN else 0)
            rows.append((child_path, selected))
        if child_type.kind in ("boolean", "integer", "enumeration"):
            return self._structured_scalar(child_type, dict(rows)[()])
        return _StructuredValue(child_type, tuple(rows))

    def _structured_literal(self, value_type, node):
        """Lower one expression against an already declared recursive schema."""
        if value_type.kind == "record":
            items = record_items(node, is_builtin=self._spelling_is_builtin)
            if items is None:
                got = self.expr(node)
                if isinstance(got, _StructuredValue) \
                        and got.value_type == value_type:
                    raise LoweringError(
                        node, "assigning one mutable structured state object "
                              "to another would create an alias SolverIR cannot "
                              "represent",
                        code="structured-state-alias-unsupported")
                raise LoweringError(
                    node, "a declared record value is not a fixed mapping literal",
                    code="declared-value-shape-mismatch")
            raw = []
            seen = set()
            for key, item in items:
                name = record_key(key)
                if name is None or name in seen:
                    raise LoweringError(
                        key if isinstance(key, ast.AST) else node,
                        "a declared record literal has unique fixed "
                                     "string fields",
                        code="declared-value-shape-mismatch")
                seen.add(name)
                raw.append((name, item))
            children = dict(value_type.fields)
            if set(seen) != set(children):
                raise LoweringError(
                    node, "a declared record literal does not have exactly its "
                          "declared fields",
                    code="declared-value-shape-mismatch")
            by_name = {}
            # Literal order is source evaluation order even though logical leaf
            # order is canonical.
            for field, source in raw:
                by_name[field] = self._structured_literal(children[field], source)
            rows = []
            for field, child in value_type.fields:
                lowered = by_name[field]
                if isinstance(lowered, _StructuredValue):
                    rows.extend(((field,) + path, expr)
                                for path, expr in lowered.leaves)
                else:
                    rows.append(((field,), lowered))
            return _StructuredValue(value_type, tuple(rows))
        if value_type.kind == "array":
            if not isinstance(node, (ast.List, ast.Tuple)) \
                    or len(node.elts) != value_type.cells:
                raise LoweringError(
                    node, f"a declared fixed array needs exactly "
                          f"{value_type.cells} source cells",
                    code="declared-value-shape-mismatch")
            rows = []
            for position, source in enumerate(node.elts):
                lowered = self._structured_literal(value_type.element, source)
                if isinstance(lowered, _StructuredValue):
                    rows.extend(((position,) + path, expr)
                                for path, expr in lowered.leaves)
                else:
                    rows.append(((position,), lowered))
            return _StructuredValue(value_type, tuple(rows))
        if value_type.kind == "enumeration":
            reference = lower_static_refs.lower_reference(self, node, preserve_reference=True)
            if reference is None:
                reference = self.expr(node)
            return self._encode_enumeration(value_type, reference, node)
        lowered = self.expr(node)
        if isinstance(lowered, (RecordValue, _StructuredValue, OptionValue)):
            raise LoweringError(
                node, "a declared scalar leaf is assigned a structured value",
                code="declared-value-shape-mismatch")
        # The complete right-hand side of a Python assignment is evaluated
        # before any target is changed.  Pin every nonliteral leaf here, in
        # source evaluation order, so a later record field effect or an earlier
        # physical-leaf write cannot change what this expression denoted.
        return self._capture(lowered)

    def _encode_enumeration(self, value_type, reference, node):
        """Encode an evaluated source label in the destination's own tag map."""
        tags = dict(value_type.tag_map)
        if not isinstance(reference, _StaticRef) or any(
                type(member) is not str or member not in tags
                for _active, member in reference.alternatives):
            raise LoweringError(
                node, "an enumeration value is one declared string member",
                code="enumeration-member-undeclared")
        encoded = ir.Lit(0)
        for active, member in reversed(reference.alternatives):
            encoded = ir.Op("ite", active, ir.Lit(tags[member]), encoded)
        return self._capture(encoded)

    def _structured_assignment(self, target, source, *, reference=None):
        """Lower a write through one explicitly declared value schema."""
        root_name = self._structured_root(target)
        inputs = root_name is None
        if inputs:
            root_name = self._structured_input_root(target)
            binding = self._structured_input_binding(root_name)
        else:
            binding = self.storage.structured_bindings.get(root_name)
        if binding is None:
            return None
        current = self._structured_storage_value(root_name, inputs=inputs)
        selectors = []
        cursor = target
        while isinstance(cursor, ast.Subscript):
            selectors.append(cursor.slice)
            cursor = cursor.value
        selectors.reverse()
        candidates = [((), ir.Lit(True))]
        selected_type = binding.value_type
        for selector in selectors:
            if selected_type.kind == "record":
                if not isinstance(selector, ast.Constant) \
                        or type(selector.value) is not str \
                        or selector.value not in dict(selected_type.fields):
                    raise LoweringError(
                        selector, "a declared record write needs one existing "
                                  "fixed string field",
                        code="record-key-not-static")
                field = selector.value
                candidates = [(prefix + (field,), guard)
                              for prefix, guard in candidates]
                selected_type = dict(selected_type.fields)[field]
                continue
            if selected_type.kind != "array":
                raise LoweringError(
                    selector, "a structured write subscripts a scalar leaf",
                    code="structured-value-not-indexable")
            raw = self.expr(selector)
            dummy = ir.Op(
                "arraylit", *[ir.Lit(0)] * selected_type.cells)
            index = self._index(target, dummy, raw)
            expanded = []
            for prefix, guard in candidates:
                for position in range(selected_type.cells):
                    expanded.append((
                        prefix + (position,),
                        ir.Op("land", guard,
                              ir.Op("eq", index, ir.Lit(position))),
                    ))
            candidates = expanded
            selected_type = selected_type.element
        if reference is None:
            lowered = self._structured_literal(selected_type, source)
        else:
            if selected_type.kind != "enumeration":
                raise LoweringError(source, "a finite label is assigned to a non-enum value",
                                    code="declared-value-shape-mismatch")
            lowered = self._encode_enumeration(selected_type, reference, source)
        rhs = (lowered.by_path if isinstance(lowered, _StructuredValue)
               else {(): lowered})
        updates = {}
        existing = current.by_path
        for prefix, guard in candidates:
            for relative, expression in rhs.items():
                path = prefix + relative
                old = updates.get(path, existing[path])
                updates[path] = (expression
                                 if isinstance(guard, ir.Lit)
                                 and guard.value is True else
                                 ir.Op("ite", guard, expression, old))
        # Dynamic writes use the old value in their false arms.  Snapshot all
        # resulting logical leaves before changing any physical variable; this
        # also gives whole-record assignment its ordinary simultaneous product
        # semantics when one right-hand field reads another left-hand field.
        updates = {path: self._capture(expression)
                   for path, expression in updates.items()}

        def stored_at(array, indices, expression):
            index = ir.Lit(indices[0])
            if len(indices) == 1:
                child = expression
            else:
                child = stored_at(
                    ir.Sel(array, index), indices[1:], expression)
            return ir.Op("store", array, index, child)

        physical = {}
        solver = dict(binding.leaf_bindings)
        for path in (leaf.path for leaf in binding.layout.leaves()):
            if path not in updates:
                continue
            destination = solver[path]
            old = physical.get(
                destination.variable, ir.Var(destination.variable))
            physical[destination.variable] = (
                updates[path] if not destination.indices else
                stored_at(old, destination.indices, updates[path]))

        command = None
        for variable, expression in physical.items():
            self._record_range(variable, expression)
            one = ir.Assign(variable, expression)
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _record_contract(self, name):
        scope = self.activation.scope
        context = self.activation.summary
        return (UNKNOWN if self.sequence_plan is None or scope is None else
                self.sequence_plan.record_contract(scope, name, context))

    def _is_record_builder(self, name):
        scope = self.activation.scope
        context = self.activation.summary
        return (False if self.sequence_plan is None or scope is None else
                self.sequence_plan.is_record_builder(
                    scope, name, context))

    def _product_for_name(self, name, node):
        """Read a record binding from its canonical recursive leaves."""
        resolved = self._name(name)
        contract = self.flow.contracts.get(resolved, UNKNOWN)
        builder = self.storage.carrier(_RecordBuilder, resolved)
        if not contract.is_record or builder is None:
            return None
        expected = tuple(path for path, _child in
                         record_leaf_contracts(contract))
        if builder.contract != contract or tuple(sorted(builder.leaves)) != expected:
            raise LoweringError(
                node, "a record binding disagrees with its source contract",
                code="record-value-shape-mismatch")
        return builder.value

    def _option_for_name(self, name, node):
        """Read a local Option, unwrapping only on a proved present path."""
        return self._option_for_binding(self._name(name), node)

    def _option_for_binding(self, resolved, node):
        """Read a bound Option using the same presence facts for state/locals."""
        builder = self.storage.carrier(_OptionBuilder, resolved)
        if builder is None:
            return None
        # A surrounding ``x is None`` / ``x is not None`` branch writes the
        # selected contract into the ordinary flow-fact table.  Prefer that
        # exact program-point fact to the whole-procedure source summary.
        planned = self._contract_ast(node)
        at_occurrence = self.flow.contracts.get(resolved, planned)
        if (at_occurrence.is_option and planned is not UNKNOWN
                and contract_contains(at_occurrence, planned)):
            # A saved Boolean predicate can carry a presence proof through
            # source planning without being a syntactic None test here.
            at_occurrence = planned
        return _OptionBuilder.refine(builder.value, at_occurrence, node)

    def _seed_record_builder_ranges(self, source_name, builder):
        """Attach source-planned scalar ranges to record leaf carriers."""
        if self.sequence_plan is None:
            return
        scope = self.activation.scope
        if scope is None:
            return
        context = self.activation.summary
        planned = self.sequence_plan.record_field_ranges(
            scope, source_name, context)
        for field, interval in planned.items():
            if interval is None:
                continue
            holder = builder.leaves.get((field,))
            if type(holder) is not str:
                # A range summarizes one scalar expression.  Recursive
                # products and arrays retain their existing structural plans.
                continue
            self.storage.widen_carrier_range(holder, interval)

    def _bind_option(self, name, value, node, contract, *, source=None, actual=None):
        """Snapshot one internal Option into stable tag and payload locals."""
        source = node if source is None else source
        target = self._name(name)
        if target in self.storage.arrays or target in self.storage.declaration_contracts:
            raise LoweringError(
                node, "an optional value is assigned to persistent state",
                code="optional-state-unsupported")
        builder = self.storage.carrier(_OptionBuilder, target)
        if builder is None:
            builder = _OptionBuilder.allocate(self, contract, node)
            self.storage.bind_carrier(target, builder)
        elif builder.contract != contract:
            if not (contract_contains(builder.contract, contract)
                    and contract_contains(contract, builder.contract)):
                raise LoweringError(
                    node, "an optional binding changes its payload contract",
                    code="optional-payload-contract-disagrees")
            contract = builder.contract
        if actual is None:
            actual = self._contract_for_value(source, value)
        command = builder.assign(self, value, source, actual=actual)
        # The builder retains the whole Option carrier, but at this program
        # point the source binding has exactly the contract just assigned.
        # Keeping the union here loses path facts across a nonempty loop and
        # can leak an OptionValue into a public scalar output even though every
        # reaching source execution has assigned the payload.
        self.flow.contracts[target] = actual
        return command

    def _record_literal(self, node, *, boundary=False):
        """Lower one fixed-schema mapping in source evaluation order."""
        try:
            raw = record_literal_entries(node, is_builtin=self._spelling_is_builtin)
        except RecordPlanError as exc:
            raise LoweringError(
                exc.node, exc.message, code=exc.code) from None
        lowered = self._in_order(*(value for _field, value in raw))
        fields = []
        contracts = {}
        for (field, source), value in zip(raw, lowered):
            contract = self._contract_for_value(source, value)
            if contract.is_sequence and not contract_is_deeply_immutable(
                    contract):
                shape = contract.physical_shape
                if shape is None or self._shape(value) != shape:
                    raise LoweringError(
                        source, "a record field contains a sequence with no "
                                "finite physical representation",
                        code="record-field-representation-unsupported")
            if not contract.is_known:
                raise LoweringError(
                    source, "a record field has no supported finite value "
                            "representation",
                    code="record-field-representation-unsupported")
            fields.append((field, value))
            contracts[field] = contract
        product = record_value(fields, contracts)
        planned = self._contract_ast(node)
        agrees = (_boundary_record_contains(product.contract, planned)
                  if boundary else contract_contains(
                      product.contract, planned))
        if planned is not UNKNOWN and (
                not planned.is_record or not agrees):
            raise LoweringError(
                node, "the lowered record disagrees with its source schema",
                code="source-value-contract-disagrees")
        # A record expression can be reached only on a failing path.  The
        # source analysis then has no normal value from which to infer the
        # whole-record contract, even though the literal still fixes its
        # field layout and every lowered field has a representation.  Keep
        # that concrete product contract here; the failure command, rather
        # than an UNKNOWN metadata value, decides whether the product is ever
        # observed.
        return RecordValue(
            product.fields,
            product.contract if planned is UNKNOWN else planned,
            product.order)

    def _record_dict_comprehension(self, node):
        """Lower one finite fixed-schema dictionary comprehension.

        The source contract owns the complete key set.  The shared eager
        comprehension lowering owns Python's generator/filter order; each
        reached value overwrites the corresponding recursive product leaves,
        so repeated keys have ordinary last-write semantics.
        """
        contract = self._contract_ast(node)
        if not contract.is_record:
            raise LoweringError(
                node, "a dictionary comprehension has no proved finite "
                      "record schema",
                code="record-key-not-static")
        builder = _RecordBuilder.allocate(self, contract, node)
        initialize = builder.initialize(self, node)
        expected = contract.field_contracts

        def write():
            if self._effectful(node.key):
                raise LoweringError(
                    node.key, "a computed record key has source-visible "
                              "effects",
                    code="record-key-not-static")
            key = self._source_fixed_value(node.key)
            if type(key) is not str or key not in expected:
                raise LoweringError(
                    node.key, "a computed record key is not one fixed "
                              "schema field",
                    code="record-key-not-static")
            value = self.expr(node.value)
            actual = self._contract_for_value(node.value, value)
            return builder.assign_field(self, key, value, actual, node.value, join=True)

        body = self._comprehension_command(node, write)
        self.before(ir.Seq(initialize, body))
        return builder.value

    def _capture_product(self, value):
        return record_from_leaves(
            value.contract,
            tuple((path, self._capture(item))
                  for path, item in record_leaves(value)),
            order=value.order)

    def _bind_product(self, name, value, node):
        """Snapshot a record into parallel locals at one binding site."""
        if not isinstance(value, RecordValue):
            raise TypeError("a product binding needs a product value")
        target = self._name(name)
        if target in self.storage.arrays or target in self.storage.declaration_contracts:
            raise LoweringError(
                node, "a record is assigned to persistent model state",
                code="record-state-unsupported")
        previous = self.flow.contracts.get(target)
        if previous is not None and previous is not UNKNOWN:
            merged = join_contracts(previous, value.contract)
            if merged is INCOMPATIBLE:
                raise LoweringError(
                    node, "a record binding has incompatible field contracts",
                    code="record-value-shape-mismatch")
            contract = merged
        else:
            contract = value.contract
        planned = self._record_contract(name)
        if planned.is_record and contract_contains(planned, contract):
            contract = planned
        builder = self.storage.carrier(_RecordBuilder, target)
        prepare = None
        if builder is None:
            builder = _RecordBuilder.allocate(self, contract, node, order=value.order)
            self.storage.bind_carrier(target, builder)
        elif builder.contract != contract:
            raise LoweringError(
                node, "a record binding changes its recursive value layout",
                code="record-value-shape-mismatch")
        else:
            # Field writes are sequential; the source binding evaluates every
            # old-field read first, including swaps and recursive payloads.
            # A loop binder returns plain IR, so stage in the existing product
            # carrier instead of hoisting captures or embedding graph regions.
            stage = _RecordBuilder.allocate(self, contract, node, order=value.order)
            prepare = stage.assign(self, value, node)
            value = stage.value
        self.flow.contracts[target] = contract
        command = builder.assign(self, value, node)
        return command if prepare is None else ir.Seq(prepare, command)

    def _set_record_value(self, name, value):
        """Capture one immutable record or initialize a proved record builder.

        A mutable builder may begin with only some of its eventual fields.  Its
        source summary proves the complete schema at every observation; the IR
        can therefore allocate that product once, initialize its unobservable
        leaves, and write only the fields present at this assignment.  An
        ordinary immutable record still has to define its entire schema here.
        """
        fields = self._record_fields(name)
        if not fields:
            raise LoweringError(
                value, f"{name} is not assigned its planned record value",
                code="record-value-shape-mismatch")
        is_builder = self._is_record_builder(name)
        planned = self._record_contract(name)
        items = record_items(value, is_builtin=self._spelling_is_builtin)
        if items is None:
            if not is_builder:
                raise LoweringError(
                    value, f"{name} is not assigned its planned record value",
                    code="record-value-shape-mismatch")
            product = self.expr(value)
            if not isinstance(product, RecordValue):
                raise LoweringError(
                    value, "a record builder initializer has no fieldwise "
                           "representation",
                    code="record-value-shape-mismatch")
            actual_fields = product.contract.field_contracts
            planned_fields = (planned.field_contracts
                              if planned.is_record else {})
            if (not planned.is_record
                    or set(actual_fields) - set(fields) or any(
                    not contract_contains(planned_fields[field], contract)
                    for field, contract in actual_fields.items())):
                raise LoweringError(
                    value, "a record builder initializer disagrees with its "
                           "finite source plan",
                    code="source-value-contract-disagrees")
            target = self._name(name)
            contract = planned
            raw = [(field, value) for field, _item in product.fields]
            values = tuple(item for _field, item in product.fields)
        else:
            raw = []
            for key, item in items:
                field = record_key(key)
                if field not in fields:
                    raise LoweringError(
                        value, f"the fields assigned to {name} disagree with its "
                               "finite record plan",
                        code="record-value-shape-mismatch")
                raw.append((field, item))
            if {key for key, _ in raw} != set(fields) and not is_builder:
                raise LoweringError(
                    value, f"the fields assigned to {name} disagree with its "
                           "finite record plan",
                    code="record-value-shape-mismatch")
            values = self._in_order(*(item for _, item in raw))
            target = self._name(name)
            actual = self._contract_ast(value)
            contract = planned if planned.is_record else actual
            if not contract.is_record:
                raise LoweringError(
                    value, f"the record representation of {name} has no exact "
                           "source contract",
                    code="source-plan-inconsistent")
            actual_fields = None

        if is_builder:
            builder = self.storage.carrier(_RecordBuilder, target)
            if builder is None:
                builder = _RecordBuilder.allocate(self, contract, value)
                self.storage.bind_carrier(target, builder)
            elif builder.contract != contract:
                raise LoweringError(
                    value, "a record builder changes its recursive layout",
                    code="record-value-shape-mismatch")
            else:
                # Rebinding may read the old carrier. Snapshot the whole RHS
                # before initialization or a preceding field write destroys it.
                values = tuple(self._capture(item) for item in values)
            self._seed_record_builder_ranges(name, builder)
            self.flow.contracts[target] = contract
            command = builder.initialize(self, value)
            for (field, source), item in zip(raw, values):
                if actual_fields is None:
                    actual = self._contract_for_value(source, item)
                    if not actual.is_known:
                        raise LoweringError(
                            source, "a record builder field has no finite value "
                                    "contract",
                            code="record-field-representation-unsupported")
                else:
                    actual = actual_fields[field]
                command = ir.Seq(command, builder.assign_field(
                    self, field, item, actual, source))
            return command

        product_fields = []
        product_contracts = {}
        for (field, source), item in zip(raw, values):
            child = self._contract_for_value(source, item)
            if not child.is_known:
                raise LoweringError(
                    source, "a record builder field has no finite value "
                            "contract",
                    code="record-field-representation-unsupported")
            product_fields.append((field, item))
            product_contracts[field] = child
        product = record_value(product_fields, product_contracts)
        if not contract_contains(contract, product.contract):
            raise LoweringError(
                value, "a record builder disagrees with its source contract",
                code="source-value-contract-disagrees")
        return self._bind_product(
            name, RecordValue(product.fields, contract, product.order), value)

    def _record_builder_store(self, target, value, source,
                              *, actual_contract=None):
        """Write one source-planned field of a mutable record local."""
        name = target.value.id
        resolved = self._name(name)
        builder = self.storage.carrier(_RecordBuilder, resolved)
        if builder is None:
            raise LoweringError(
                target, f"the record builder {name} is written before it is "
                        "initialized",
                code="record-builder-uninitialized")
        scope = self.activation.scope
        context = self.activation.summary
        cases = (None if self.sequence_plan is None else
                 self.sequence_plan.output_key_cases(
                     target, scope, context))
        if not cases:
            raise LoweringError(
                target.slice, "a record field write has no finite source plan",
                code="source-plan-inconsistent")
        if any(field not in builder.contract.field_contracts
               for field, _selector in cases):
            raise LoweringError(
                target.slice, "a record field plan names an unallocated field",
                code="source-plan-inconsistent")
        actual_contract = (self._contract_for_value(source, value)
                           if actual_contract is None else actual_contract)

        alternatives = {}
        for field, selector in cases:
            tests = [
                ir.Op("eq", ir.Var(self._name(variable)), ir.Lit(expected))
                for variable, expected in selector
            ]
            guard = ir._balanced_op("land", tests, ir.Lit(True))
            alternatives.setdefault(field, []).append(guard)
        commands = []
        for field, guards in alternatives.items():
            selected = builder.assign_field(self, field, value, actual_contract, source)
            commands.append((lower_static_refs.disjunction(guards), selected))
        _last, command = commands[-1]
        for guard, selected in reversed(commands[:-1]):
            command = ir.If(guard, selected, command)
        self.reached.add(id(target))
        self._discharge(
            self._safety_goal(target, "indexerror"),
            "assignment to a statically planned record field")
        return command

    def _batch_field_store(self, target, value, source,
                           *, actual_contract=None):
        """Write ``batch[index][field]`` through the batch representation.

        A source transaction batch is represented by one logical length and
        one fixed-capacity array per record field.  Reading ``batch[index]``
        already reconstructs a record from those parallel arrays; mutation
        must update the corresponding field array rather than lower the field
        name as an ordinary string-valued array index.

        Return ``None`` when ``target`` is not rooted at a represented batch,
        so the ordinary recursive-array store can handle it.
        """
        row = target.value
        if (not isinstance(row, ast.Subscript)
                or not isinstance(row.value, ast.Name)):
            return None
        resolved = self._name(row.value.id)
        builder = self.storage.carrier(_BatchBuilder, resolved)
        if builder is None:
            return None

        sequence = record_batch_payload(
            self.flow.contracts.get(resolved, UNKNOWN))
        record = None if sequence is None else sequence.element
        if record is None or not record.is_record:
            raise LoweringError(
                row.value, "a transaction batch has no mutable record "
                           "element contract",
                code="output-batch-write-unsupported")
        if (not isinstance(target.slice, ast.Constant)
                or type(target.slice.value) is not str):
            raise LoweringError(
                target.slice, "a transaction field write needs a fixed "
                              "string field name",
                code="record-key-not-static")

        # Assignment evaluates the RHS before the target.  The caller has
        # already lowered ``value``.  A side effect in the index would also
        # require retaining the pre-effect list object, which the no-alias
        # batch carrier cannot express; use the same boundary as ordinary
        # recursive array stores.
        if self._effectful(row.slice):
            raise LoweringError(
                row.slice, "a transaction-batch index with source-visible "
                           "effects",
                code="subscript-target-effectful")
        self.reached.add(id(row))
        self.reached.add(id(target))
        raw_index = self._integer_context_expr(row.slice)
        index = self._sequence_index(
            row, None, raw_index, length=ir.Var(builder.count))

        field = target.slice.value
        key_goal = self._safety_goal(target, "indexerror")
        expected = record.field_contracts.get(field)
        if expected is None or field not in builder.buffers:
            # ``mapping[key] = value`` adds an absent key; it does not raise
            # ``KeyError``.  The parallel batch representation has one array
            # per planned field, so adding a field would change that physical
            # schema.  Refuse that source form until the source plan proves and
            # allocates the enlarged schema instead of inventing a run-time
            # failure that Python would not produce.
            raise LoweringError(
                target, f"transaction field {field!r} is not in the planned "
                        "record schema",
                code="record-schema-mutation-unsupported")
        self._discharge(
            key_goal, f"{field!r} is a field of the fixed transaction record")

        if isinstance(value, (_BatchValue, OptionValue, RecordValue)):
            raise LoweringError(
                source, "a transaction field has no fixed value "
                        "representation",
                code="record-field-representation-unsupported")
        actual = (self._contract_for_value(source, value)
                  if actual_contract is None else actual_contract)
        return builder.store_field(self, field, index, value, actual, source)

    def _batch_record_store(self, target, value, source,
                            *, actual_contract=None):
        """Replace one complete record in a represented record sequence.

        Transaction batches use a structure-of-arrays carrier: one logical
        record position is the corresponding position in every field buffer.
        Whole-element assignment therefore writes every field at the same
        checked index.  The caller has already evaluated and captured the
        right-hand record, which preserves Python's simultaneous-assignment
        rule when two batch elements are swapped.

        Return ``None`` when ``target`` is not an element of a represented
        batch, so the ordinary recursive-array store remains authoritative for
        all other subscript targets.
        """
        if (not isinstance(target, ast.Subscript)
                or not isinstance(target.value, ast.Name)):
            return None
        resolved = self._name(target.value.id)
        builder = self.storage.carrier(_BatchBuilder, resolved)
        if builder is None:
            return None
        if not isinstance(value, RecordValue):
            raise LoweringError(
                source, "a transaction-batch element is replaced by a value "
                        "that is not a fixed-schema record",
                code="record-value-shape-mismatch")

        sequence = record_batch_payload(
            self.flow.contracts.get(resolved, UNKNOWN))
        expected = None if sequence is None else sequence.element
        actual = (value.contract if actual_contract is None else
                  actual_contract)
        if (expected is None or not expected.is_record
                or not actual.is_record
                or not contract_contains(expected, actual)
                or set(value.by_name) != set(expected.field_contracts)
                or set(builder.buffers) != set(expected.field_contracts)):
            raise LoweringError(
                source, "a replacement transaction changes the batch's "
                        "fixed record layout",
                code="record-value-shape-mismatch")
        if self._effectful(target.slice):
            raise LoweringError(
                target.slice, "a transaction-batch index with source-visible "
                              "effects",
                code="subscript-target-effectful")

        self.reached.add(id(target))
        raw_index = self._integer_context_expr(target.slice)
        index = self._sequence_index(
            target, None, raw_index, length=ir.Var(builder.count))
        command = None
        for field, field_contract in expected.fields:
            item = value.by_name[field]
            item_contract = actual.field_contracts[field]
            if isinstance(item, (_BatchValue, OptionValue, RecordValue)):
                raise LoweringError(
                    source, f"transaction field {field!r} has no physical "
                            "replacement representation",
                    code="record-field-representation-unsupported")
            one = builder.store_field(
                self, field, index, item, item_contract, source)
            command = one if command is None else ir.Seq(command, one)
        return ir.Skip() if command is None else command

    def _set_batch_value(self, name, value, target):
        """Materialize a source batch before rebinding its local or state slot."""
        persistent = isinstance(target, ast.Attribute)
        if persistent:
            resolved = self.storage.state_name(name)
            builder = self.storage.require_carrier(_BatchBuilder, resolved)
            contract = self.storage.state_products[name].contract
            record_contract = contract.element
        else:
            resolved, builder = self._batch_builder(name, value)
            contract = self._represented_batch_contract(name)
            spec = self._sequence_spec(name)
            record_contract = None if spec is None else spec.element
        scope = self.activation.scope
        context = self.activation.summary
        plan = (None if self.sequence_plan is None or scope is None else
                self.sequence_plan.binding_batch_plan(
                    target, scope, context))
        if plan is None:
            # The bounded source execution may prove this assignment unreachable
            # while graph lowering still retains its guarded arm.  Such an arm needs
            # a physical batch layout, but it must not revive the old CFG-based
            # reachability oracle: that oracle could erase a genuinely dynamic arm.
            # Build only the RHS's syntax-directed batch plan.  If its value contract
            # is not independently exact, the ordinary typed refusal below remains.
            try:
                plan = plan_batch_expression(
                    value, value_contract=self._contract_ast,
                    is_builtin=self._spelling_is_builtin)
            except BatchContractError as exc:
                raise LoweringError(
                    exc.node or value, exc.what, code=exc.code) from None
        if not isinstance(plan, BatchPlan):
            raise LoweringError(
                value, "a batch-valued assignment has no exact source plan",
                code="source-plan-inconsistent")
        # Zero-capacity carriers already reserve one sentinel cell.  A
        # retained guarded arm may use that cell even when bounded source
        # execution proves the arm unreachable; larger plans still require a
        # representation plan.
        if builder.capacity == 0 and plan.contract.maximum == 1:
            builder.capacity = 1
        if plan.contract.maximum > builder.capacity:
            raise LoweringError(
                value, "a batch-valued assignment exceeds its allocated "
                       "capacity",
                code="source-plan-inconsistent")
        if record_batch_payload(contract) is None:
            raise LoweringError(
                target, "a planned batch binding has no list-of-records "
                        "source contract",
                code="source-plan-inconsistent")

        # Python evaluates the complete RHS before rebinding the target.  A
        # plan such as ``batch = batch + more`` may therefore read the old
        # target while it is being replaced.  Always materialize into a fresh
        # carrier, then commit once; resetting the destination first would
        # silently turn the old value into an empty batch.
        stage = _BatchBuilder(
            self.storage.fresh(),
            {field: self.storage.fresh() for field in builder.buffers},
            builder.capacity, form=self.storage.fresh())
        prepare_stage = stage.initialize(self, record_contract, value)
        stage_sink = _NamedReturn(
            None, stage.count, tuple(stage.buffers), stage.capacity,
            stage.buffers)
        evaluate = stage_sink._emit_plan(
            self, value, plan, form_target=stage.form)

        prepare_target = builder.initialize(self, record_contract, target)
        target_sink = _NamedReturn(
            None, builder.count, tuple(builder.buffers), builder.capacity,
            builder.buffers)
        staged_value = _BatchValue(stage, ir.Var(stage.form), contract)
        commit = target_sink._copy_builder(
            self, plan, staged_value, form_target=builder.form)
        # The binding fact belongs after the RHS as well.  Lowering an inlined
        # helper may install the flow facts of one of its return paths; exposing
        # the new target contract before that work lets such a path erase it.
        # Keeping the old fact live through ``evaluate`` also matches Python for
        # a rebinding whose RHS reads the previous batch value.
        self.flow.contracts[resolved] = contract
        return ir.Seq(
            prepare_stage,
            ir.Seq(evaluate, ir.Seq(prepare_target, commit)))

    def _capacity_guard(self, node, length, capacity):
        goal = self._safety_goal(node, "capacity")
        self._guard(ir.Op("ge", ir.Var(length), ir.Lit(capacity)), goal,
                    "CapacityExceeded", node)

    def _batch_pairs(self, target, value):
        """The declared fields and unevaluated payloads of one transaction."""
        builder = self.storage.require_carrier(_BatchBuilder, target)
        items = record_items(value, is_builtin=self._spelling_is_builtin)
        record = (self.storage.carrier(_RecordBuilder, self._name(value.id))
                  if isinstance(value, ast.Name) else None)
        if record is not None:
            values = record.value.by_name
            contracts = record.contract.field_contracts
            pairs = [(name, values[name], contracts[name])
                     for name in builder.buffers]
        elif items is None:
            raise LoweringError(
                value, "an output batch element that is not a mapping",
                code="output-batch-element-unsupported")
        else:
            raw_pairs = []
            for key, item in items:
                field = record_key(key)
                if field not in builder.buffers:
                    raise LoweringError(
                        value, "an output transaction naming different fields",
                        code="outputs-disagree-across-returns")
                raw_pairs.append((field, item, None))
            pairs = raw_pairs
        if {name for name, _value, _contract in pairs} != set(builder.buffers):
            raise LoweringError(
                value, "an output transaction naming different fields",
                code="outputs-disagree-across-returns")
        return pairs

    def _append_batch_pairs(self, target, pairs, node, guard=True):
        """Store one already ordered transaction in its parallel buffers."""
        builder = self.storage.require_carrier(_BatchBuilder, target)
        if guard:
            self._capacity_guard(node, builder.count, builder.capacity)
        cmd = None
        for name, item, actual in pairs:
            if name not in builder.buffers:
                raise LoweringError(
                    node, "an output transaction naming different fields",
                    code="outputs-disagree-across-returns")
            one = builder.store_field(
                self, name, ir.Var(builder.count), item, actual, node)
            cmd = one if cmd is None else ir.Seq(cmd, one)
        next_count = ir.Op("add", ir.Var(builder.count), ir.Lit(1))
        self._record_range(builder.count, next_count)
        advance = ir.Assign(builder.count, next_count)
        return advance if cmd is None else ir.Seq(cmd, advance)

    def _append_batch_value(self, target, value, node, guard=True):
        groups = self._lower_payload_groups(
            [self._batch_pairs(target, value)])
        return self._append_batch_pairs(target, groups[0], node, guard=guard)

    def _append(self, node):
        """Growing a sequence writes one cell and advances its length.

        A sequence whose length the data decides is a fixed-capacity array and
        a length variable. The capacity is not guessed: a step appends at most
        as many times as the appends written in it, each multiplied by the
        bounds of the loops around it. Exceeding representation capacity is
        an analysis limit, not an artifact safety violation.
        """
        parent = (None if self.resolver is None else
                  self.resolver._parents.get(id(node)))
        if not (isinstance(parent, ast.Expr) and parent.value is node):
            raise LoweringError(
                node, "the result of append is used as a value",
                code="append-result-used")
        target = sequence_method_target(node, "append")
        if target is None:
            raise LoweringError(
                node, "append through an unrepresented receiver",
                code="append-target-unsupported")
        kind, source_name = target
        name = (self._name(source_name)
                if kind == "local" else self.storage.state_name(source_name))
        self._check_not_shared(name, node, "appending to")
        if self.storage.carrier(_BatchBuilder, name) is not None:
            self.before(self._append_batch_value(name, node.args[0], node))
            return ir.Lit(0)
        if name not in self.storage.lengths:
            if kind == "local":
                raise LoweringError(
                    node, f"append to {name}, which is not a sequence this "
                          "step created", code="append-target-not-local")
            raise LoweringError(
                node, f"the dynamic state {source_name!r} has no logical "
                      "length representation",
                code="source-plan-inconsistent")
        length = self.storage.lengths[name]
        capacity = self.storage.capacities[name]
        value = self.expr(node.args[0])
        item_contract = self._contract_for_value(node.args[0], value)
        expected = self.flow.lengths[name][1:]
        spec = (self._sequence_spec(source_name)
                if kind == "local" else None)
        expected_contract = (
            self.storage.declaration_contracts.get(name, UNKNOWN).element
            if kind == "state" else
            None if spec is None else spec.element)
        current_contract = self.flow.contracts.get(name)
        if current_contract is not None and current_contract.carrier == "bytearray":
            lower_bytes.check_store(self, node, value)
            value = ir.Op("toint", value)
        if expected_contract is None and current_contract is not None \
                and current_contract.is_sequence:
            expected_contract = current_contract.element
        if (expected_contract is not None
                and not contract_contains(expected_contract, item_contract)):
            raise LoweringError(
                node.args[0], f"the element appended to {name} disagrees with "
                              "its finite source value contract",
                code="source-value-contract-disagrees")
        value = self._coerce_sequence_storage(
            node.args[0], value, item_contract, expected_contract)
        if self._shape(value) != expected:
            raise LoweringError(
                node.args[0], f"the element appended to {name} has shape "
                              f"{self._shape(value)}, not {expected}",
                code="sequence-element-shape-disagrees")
        post_contract = self._binding_contract(source_name, node)
        if post_contract is UNKNOWN:
            # Direct lowerer clients need not carry a source plan.  In that
            # case the run-time length fact is the exact transfer authority.
            known = self.flow.bounds.get(length, ranges.Bounds(0, capacity))
            element = (item_contract if current_contract is None
                       or current_contract.element is None else
                       join_contracts(current_contract.element,
                                      item_contract))
            if element is INCOMPATIBLE:
                raise LoweringError(
                    node.args[0], "append changes the sequence element "
                                  "contract",
                    code="source-value-contract-disagrees")
            carrier = (current_contract
                       if current_contract is not None
                       and current_contract.is_sequence else
                       ValueContract.sequence("list", 0, capacity, element))
            post_contract = carrier.with_interval(
                known.lo + 1, known.hi + 1, element)
        if (not post_contract.is_sequence
                or post_contract.carrier not in {"list", "bytearray"}
                or post_contract.maximum > capacity
                or post_contract.element is None
                or not contract_contains(post_contract.element,
                                         item_contract)):
            raise LoweringError(
                node, "the source append contract disagrees with its finite "
                      "sequence representation",
                code="source-plan-inconsistent")
        self._capacity_guard(node, length, capacity)
        # The companion is part of the sequence representation, so its
        # compile-time flow fact must take the same transfer as the command
        # emitted below.  Closure summaries are selected at the exact call
        # point: retaining the initializer's length here made a definitely
        # reached ``append`` look absent and could reject the matching lexical
        # activation.  Branch and loop joins already carry these numeric facts
        # through ``FlowFacts``; recording the elementary transfer here keeps
        # straight-line and compound control flow under that one authority.
        self._record_range(
            length, ir.Op("add", ir.Var(length), ir.Lit(1)))
        nested = self._store_element_lengths(
            name, ir.Var(length), value, item_contract,
            current_contract, node.args[0])
        self.flow.contracts[name] = post_contract
        self.before(ir.Seq(
            ir.Store(name, ir.Var(length), value),
            ir.Seq(nested, ir.Assign(
                length, ir.Op("add", ir.Var(length), ir.Lit(1))))))
        return ir.Lit(0)                      # append returns None

    def _extend(self, node):
        """Append a finite eager sequence, preserving its evaluation image.

        There is no synthetic source loop: copies are representation commands,
        not extra goals. Alias analysis checks that neither receiver identity
        nor shared mutable children are lost by the value-array representation.
        Lazy iterators need interleaved consumption and remain a separate
        capability, not an eager approximation.
        """
        parent = (None if self.resolver is None else
                  self.resolver._parents.get(id(node)))
        if not (isinstance(parent, ast.Expr) and parent.value is node):
            raise LoweringError(node, "the result of extend is used as a value",
                                code="extend-result-used")
        kind, source_name = sequence_method_target(node, "extend")
        name = (self._name(source_name) if kind == "local"
                else self.storage.state_name(source_name))
        self._check_not_shared(name, node, "extending")
        if name not in self.storage.lengths or self.storage.carrier(_BatchBuilder, name) is not None:
            raise LoweringError(
                node, "extend requires an ordinary bounded list representation",
                code="extend-target-unsupported")
        source = node.args[0]
        value = self.expr(source)
        items = self._contract_for_value(source, value)
        if (not items.is_sequence
                or items.carrier not in {"list", "tuple", "range"}):
            raise LoweringError(
                source, "extend requires a represented finite sequence",
                code="extend-iterable-unsupported")
        post = self._binding_contract(source_name, node)
        capacity = self.storage.capacities[name]
        if (not post.is_sequence or post.carrier != "list"
                or post.maximum > capacity
                or (items.maximum and (items.element is None
                    or post.element is None
                    or not contract_contains(post.element, items.element)))):
            raise LoweringError(
                node, "extend disagrees with its finite source contract",
                code="source-plan-inconsistent")
        if items.maximum == 0:
            self.flow.contracts[name] = post
            return ir.Lit(0)
        # Capture before writing, even for self-extension. Both the element
        # data and all nested logical lengths use this same pre-store image.
        count = self._capture(self._logical_length(source, value, items))
        value = self._capture(value)
        length = self.storage.lengths[name]
        total = ir.Op("add", ir.Var(length), count)
        self._guard(ir.Op("gt", total, ir.Lit(capacity)),
                    self._safety_goal(node, "capacity"), "CapacityExceeded", node)
        self._spend(items.maximum)
        for offset in range(items.maximum):
            index = ir.Lit(offset)
            item = self._select_sequence_element(value, index, items, source)
            store = self._store_sequence_element(
                name, ir.Op("add", ir.Var(length), index), item,
                items.element, post, source)
            self.before(ir.If(ir.Op("lt", index, count), store, ir.Skip()))
        self._record_range(length, total)
        self.before(ir.Assign(length, total))
        self.flow.contracts[name] = post
        return ir.Lit(0)  # the discarded Python result is None

    @staticmethod
    def _shifted_sequence_value(value, index, length, capacity):
        """The fixed-capacity image after removing one logical element."""
        last = ir.Op("sub", length, ir.Lit(1))
        cells = []
        for offset in range(capacity):
            here = ir.Lit(offset)
            move = ir.Op(
                "land", ir.Op("ge", here, index),
                ir.Op("lt", here, last))
            retained = ir.Sel(value, here)
            following = (ir.Sel(value, ir.Lit(offset + 1))
                         if offset + 1 < capacity else retained)
            cells.append(ir.Op("ite", move, following, retained))
        return ir.Op("arraylit", *cells)

    def _pop(self, node):
        """Remove and return one element of a bounded mutable sequence."""
        target = sequence_method_target(node, "pop")
        if target is None:
            raise LoweringError(
                node, "pop through an unrepresented receiver",
                code="pop-target-unsupported")
        return self._remove_sequence_item(
            node, target, node.args[0] if node.args else None,
            return_removed=True)

    def _remove_sequence_item(self, node, target, index_source, *, return_removed):
        """Remove one logical element, retaining the original source safety site."""
        kind, source_name = target
        name = (self._name(source_name)
                if kind == "local" else self.storage.state_name(source_name))
        self._check_not_shared(name, node, "popping from")
        if name not in self.storage.lengths:
            raise LoweringError(
                node, f"pop from {source_name!r}, which has no bounded "
                      "logical-length representation",
                code="pop-target-not-dynamic")
        if self.storage.carrier(_BatchBuilder, name) is not None:
            raise LoweringError(
                node, "pop from a represented transaction batch",
                code="output-batch-pop-unsupported")
        contract = self.flow.contracts.get(name, UNKNOWN)
        if (not contract.is_sequence or contract.carrier != "list"
                or contract.element is None):
            raise LoweringError(
                node, "pop from a value not proved to be a finite mutable "
                      "sequence", code="pop-target-not-sequence")

        length_name = self.storage.lengths[name]
        length = ir.Var(length_name)
        capacity = self.storage.capacities[name]
        if capacity < 1:
            raise LoweringError(
                node, "pop from a statically empty sequence",
                code="index-out-of-range")
        raw = (ir.Lit(-1) if index_source is None else
               self._integer_context_expr(index_source))
        index = self._capture(self._sequence_index(node, name, raw, length=length))

        # ``pop`` returns the pre-mutation value.  Capture both its data and
        # recursive logical-length metadata before replacing the backing
        # array, then attach the exact source element contract to the result.
        result = None
        if return_removed:
            selected = self._select_sequence_element(
                ir.Var(name), index, contract, node)
            result = self._capture(selected)
            if not isinstance(result, ir.Var):
                raise LoweringError(
                    node, "a popped sequence element has no stable value "
                          "representation", code="source-plan-inconsistent")
            self.flow.contracts[result.name] = contract.element
            if contract.element.is_sequence and contract.element.carrier:
                self.flow.carriers[result.name] = contract.element.carrier

        update = ir.Assign(
            name, self._shifted_sequence_value(
                ir.Var(name), index, length, capacity))
        for path, holder in self.storage.length_images.get(name, {}).items():
            if not path or path[0] is not lower_state.SEQUENCE_ELEMENT:
                continue
            shape = self._shape(ir.Var(holder))
            if not shape or shape[0] != capacity:
                raise LoweringError(
                    node, "a popped element's logical-length layout "
                          "disagrees with its sequence capacity",
                    code="sequence-plan-inconsistent")
            update = ir.Seq(
                update,
                ir.Assign(holder, self._shifted_sequence_value(
                    ir.Var(holder), index, length, capacity)))

        # On every normal pop the old length is at least one.  Retain that
        # consequence before transferring the companion to ``length - 1``;
        # the failing empty-list execution has already terminated at the
        # guard emitted by ``_sequence_index``.
        known = self.flow.bounds.get(length_name, ranges.Bounds(0, capacity))
        successful = known.meet(ranges.Bounds(1, capacity))
        if successful is None:
            raise LoweringError(
                node, "pop from a sequence proved empty on this path",
                code="index-out-of-range")
        self.flow.bounds[length_name] = successful
        finite = successful.finite()
        if finite is not None:
            self.flow.ranges[length_name] = finite
        next_length = ir.Op("sub", length, ir.Lit(1))
        self._record_range(length_name, next_length)
        post_contract = self._binding_contract(source_name, node)
        if post_contract is UNKNOWN:
            post_contract = contract.with_interval(
                successful.lo - 1, successful.hi - 1,
                contract.element)
        if (not post_contract.is_sequence
                or post_contract.carrier != "list"
                or post_contract.maximum > capacity
                or post_contract.element is None):
            raise LoweringError(
                node, "the source pop contract disagrees with its finite "
                      "sequence representation",
                code="source-plan-inconsistent")
        self.flow.contracts[name] = post_contract
        self.before(ir.Seq(
            update, ir.Assign(length_name, next_length)))
        return result

    def _call(self, node, *, result_kind=None):
        """A call whose source we have is inlined; one we do not is an
        operation, and then something has to encode it.

        Inlining comes first. A routine of the approved library is ordinary
        bit-level source, so lowering it costs no encoding and no obligation,
        whereas leaving it opaque would put both behind every routine.
        """
        updates = record_update_items(node)
        if updates is not None and self._is_record_builder(node.func.value.id):
            builder = self.storage.carrier(_RecordBuilder, self._name(node.func.value.id))
            if builder is None:
                raise LoweringError(node, "record update precedes its initialization",
                                    code="record-builder-uninitialized")
            values = []
            for item in updates:
                value = self.expr(item.value)
                contract = self._contract_for_value(item.value, value)
                values.append((item, self._capture(value), contract))
            for item, value, contract in values:
                self.before(builder.assign_field(
                    self, item.arg, value, contract, item.value))
            return OptionValue(ir.Lit(False), None, NONE)
        if isinstance(node.func, ast.Attribute):
            mapping = lower_mappings.receiver(self, node.func.value)
            if mapping is not None:
                return mapping.call(self, node)

        def is_static_argument(value):
            if not lower_static_refs.is_reference(self, value):
                return False
            results = lower_static_refs.possible_values(self, value)
            return results is None or any(type(item) not in (bool, int) for item in results)

        static_arguments = any(is_static_argument(value) for value in [
            *node.args, *(keyword.value for keyword in node.keywords)])

        def reject_static_arguments():
            if not static_arguments:
                return
            raise LoweringError(
                node, "a finite source collection is passed across a call "
                      "boundary",
                code="static-collection-escapes")
        # One lexical resolution for every consumer (review 47): a bare
        # name resolves through the current scope by Python's rules, a
        # self-call through the class with instance-attribute shadowing
        # respected, and a builtin spelling means the builtin only when
        # nothing binds it.
        is_method = (isinstance(node.func, ast.Attribute)
                     and isinstance(node.func.value, ast.Name)
                     and node.func.value.id == "self")
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        # A name bound to a value here -- a comprehension target, a loop
        # variable -- is a value being called, not a function to resolve
        # (review 49: [helper(a) for helper in [0]] called the module
        # helper); a rebound self is not the model either.
        if isinstance(node.func, ast.Name) and node.func.id in self.activation.substitutions:
            declared_intrinsics = getattr(
                getattr(self, "resolver", None), "declared_intrinsics", {})
            if node.func.id in declared_intrinsics:
                raise LoweringError(
                    node, f"the imported Behavior intrinsic {node.func.id} "
                          "is shadowed at this call site",
                    code="source-intrinsic-shadowed")
            raise LoweringError(
                node, f"call to {node.func.id}, which is bound to a value "
                      f"here, not a function", code="call-unlowered")
        if is_method and ("self" in self.activation.substitutions
                          or self._name_is_local("self")):
            raise LoweringError(
                node, "method call on a rebound self, which is not the "
                      "model", code="self-rebound")
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        builtin_ok = resolver is None
        if resolver is not None and scope is not None:
            if is_method:
                kind, target = resolver.resolve_method(scope, name)
                if kind == "method":
                    return self._inline(node, target, result_kind=result_kind)
                reject_static_arguments()
                raise LoweringError(
                    node, f"call to {name}, which nothing this scope can "
                          f"prove is the class's method",
                    code="call-unlowered")
            if isinstance(node.func, ast.Name):
                kind, target = resolver.resolve_call(scope, name)
                if kind == "nested":
                    self._require_def_seen(node, name, target)
                    return self._inline(node, target, result_kind=result_kind)
                if kind in {"module", "alias"}:
                    return self._inline(node, target, result_kind=result_kind)
                if kind == "intrinsic":
                    reject_static_arguments()
                    return self._intrinsic_call(node, target)
                if kind == "intrinsic-shadowed":
                    reject_static_arguments()
                    raise LoweringError(
                        node, f"the imported Behavior intrinsic {name} is "
                              "shadowed at this call site",
                        code="source-intrinsic-shadowed")
                if kind != "builtin":
                    reject_static_arguments()
                    raise LoweringError(
                        node, f"call to {name}, which resolves to no "
                              f"function this scope can inline",
                        code="call-unlowered")
                builtin_ok = True
        elif is_method and name in getattr(self, "methods", {}):
            return self._inline(node, self.methods[name], result_kind=result_kind)
        elif not is_method and isinstance(node.func, ast.Name) \
                and name in self.functions:
            return self._inline(node, self.functions[name], result_kind=result_kind)
        if name in self.primitives:
            # An approved name with no body found. Refused rather than made into an
            # operation of its own, because the two readings of the library in this
            # file contradict and only one of them is the design: the docstring above
            # says its routines are inlined precisely so that nothing has to carry a
            # hand-written encoding and a proof obligation per routine, and an
            # `ir.Op(name)` here is exactly that obligation, owed by an operation no
            # layer declares. T10's closure check is what has no room for it.
            #
            # Measured before removing it: of 207 names imported from the library
            # across 64 models, 204 resolve to a routine and are inlined, and the three
            # that did not were aliases whose targets exist -- so this branch fired on
            # nothing that was ever meant to reach it.
            raise LoweringError(
                node, f"{name} is imported from the approved library and no routine "
                      f"of that name is there, so there is no body to inline", code="library-routine-missing")
        if name == "dict" and builtin_ok and isinstance(node.func, ast.Name):
            copied = record_copy_source(node, is_builtin=self._spelling_is_builtin)
            if copied is not None:
                value = self.expr(copied)
                if not isinstance(value, RecordValue):
                    raise LoweringError(
                        node, "dict copy needs a finite fixed-schema record",
                        code="record-value-shape-mismatch")
                # Capture at the call, before later expressions can replace
                # source fields. Alias admission preserves shallow semantics.
                return self._capture_product(value)
            if record_items(node, is_builtin=self._spelling_is_builtin) is None:
                raise LoweringError(
                    node, "dict needs fixed keyword fields or one finite record",
                    code="record-value-shape-mismatch")
            return self._record_literal(node)
        if builtin_ok and isinstance(node.func, ast.Name):
            if name in BYTE_CARRIERS:
                return lower_bytes.construct(self, node, name)
            if name == "chr":
                return lower_static_refs.character(self, node)
            if name == "ord":
                return lower_static_refs.ordinal(self, node)
        if is_from_bytes(node, self._spelling_is_builtin):
            return lower_bytes.from_bytes(self, node)
        if builtin_ok and isinstance(node.func, ast.Name) and name == 'float':
            sign = infinity_literal(node)
            if sign is not None:
                return OrderedScalar(ir.Lit(sign), ir.Lit(0))
        if isinstance(node.func, ast.Attribute) and name == "to_bytes":
            return lower_bytes.to_bytes(self, node)
        if isinstance(node.func, ast.Attribute) and name == "copy":
            contract = self._contract_ast(node.func.value)
            if not (has_plain_positional_arity(node, 0) and contract.is_sequence
                    and contract.carrier in {"list", "bytearray"}):
                raise LoweringError(node, "copy needs a represented list or bytearray",
                                    code="sequence-copy-unsupported")
            sliced = ast.copy_location(ast.Subscript(
                value=node.func.value, slice=ast.Slice(), ctx=ast.Load()), node)
            return self._slice(sliced)
        if name == "len" and builtin_ok and isinstance(node.func, ast.Name):
            if node.keywords:
                raise LoweringError(
                    node, "builtin len with keyword arguments",
                    code="call-argument-form")
            if len(node.args) != 1:
                raise LoweringError(
                    node, "builtin len with wrong arity", code="call-arity")
            arg = node.args[0]
            table_path = lower_static_refs.table_path(self, arg)
            if table_path is not None:
                table, parts = table_path
                return lower_static_refs.read_path(self, table, parts, length=True)
            if lower_static_refs.is_reference(self, arg):
                reference = lower_static_refs.lower_reference(self, arg, preserve_reference=True)
                if isinstance(reference, _StaticRef):
                    return reference.length(self, arg)
            value = self.expr(arg)
            if isinstance(value, MappingBuilder):
                return ir.Var(value.count)
            if isinstance(value, RecordValue):
                raise LoweringError(
                    node, "len observes a fixed-schema record as a mapping",
                    code="record-operation-unsupported")
            contract = self._contract_for_value(arg, value)
            if contract.is_sequence:
                # Fixed and run-time lengths have one source contract.  The
                # latter is carried beside the physical array; the former is
                # returned as its exact literal.  Leaving either as an IR
                # ``len`` would defer a source representation question to a
                # primitive that intentionally does not exist.
                return self._logical_length(arg, value, contract)
            return ir.Op("len", value)
        module_table_get = (
            name == "get"
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and self._model_module_name(node.func.value)
            and node.func.value.id in self.tables
            and len(node.args) == 2)
        static_mapping_get = (
            name == "get"
            and isinstance(node.func, ast.Attribute)
            and lower_static_refs.is_reference(self, node.func.value)
            and not module_table_get)
        if static_mapping_get:
            value = lower_static_refs.lower_reference(
                self, node, preserve_collection=True,
                preserve_reference=lower_static_refs.requires_reference(self, node))
            if value is None:
                raise LoweringError(
                    node, "a finite static mapping lookup has no exact "
                          "result representation",
                    code="static-get-result-unsupported")
            return value
        if isinstance(node.func, ast.Attribute) and name == "count":
            counted = self._binary_digit_count(node)
            if counted is not None:
                return counted
        if not module_table_get and not (
                builtin_ok and isinstance(node.func, ast.Name) and name == "bool"):
            reject_static_arguments()
        if (builtin_ok and isinstance(node.func, ast.Name)
                and name in {"abs", "bool", "int", "list", "tuple", "range", "reversed",
                             "min", "max", "sum", "all", "any", "sorted", "divmod", "pow"}):
            return self._builtin_call(node, name)
        if (isinstance(node.func, ast.Attribute)
                and name in {"bit_count", "bit_length"}):
            return self._integer_method(node, name)
        if is_append_call(node):
            if not is_plain_append_call(node):
                raise LoweringError(
                    node, "append with anything other than one positional "
                          "argument", code="call-argument-form")
            return self._append(node)
        if is_pop_call(node):
            if not is_plain_pop_call(node):
                raise LoweringError(
                    node, "pop with anything other than one optional "
                          "positional index", code="call-argument-form")
            return self._pop(node)
        if is_extend_call(node):
            if not is_plain_extend_call(node):
                raise LoweringError(
                    node, "extend requires one positional iterable",
                    code="call-argument-form")
            return self._extend(node)

        if (name == "get" and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and resolver is not None
                and node.func.value.id in resolver.invalid_tables):
            raise LoweringError(
                node.func.value,
                f"the module table {node.func.value.id} is not a closed "
                f"read-only binding", code="module-table-not-static")
        if name == "get" and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and self._model_module_name(node.func.value) \
                and node.func.value.id in self.tables and len(node.args) == 2:
            if node.keywords:
                raise LoweringError(
                    node, "mapping get with keyword arguments",
                    code="call-argument-form")
            fixed_key, lowered_key = lower_static_refs.selector(self, node.args[0])
            key = fixed_key if fixed_key is not None else lowered_key
            if fixed_key is None and self._effectful(node.args[1]):
                key = self._capture(key)
            defaults = (lower_static_refs.possible_values(self, node.args[1])
                        if lower_static_refs.is_reference(self, node.args[1]) else None)
            if (defaults is not None and len(defaults) == 1
                    and type(defaults[0]) is dict and defaults[0]
                    and all(type(field) is str for field in defaults[0])):
                # A closed module record has no run-time evaluation effect, but
                # cannot be passed through ``expr`` as a Python mapping.  Keep
                # the key/default evaluation order and eliminate both mappings
                # into a fieldwise product inside the table lookup.
                return lower_static_refs.lookup(
                    self, node.func.value.id, key, node, default_source=defaults[0])
            default = self.expr(node.args[1])
            return lower_static_refs.lookup(self, node.func.value.id, key, node, default)
        raise LoweringError(node, f"call to {name or 'an expression'}", code="call-unlowered")

    def _intrinsic_call(self, node, symbol):
        """Lower one explicit ``behave_intrinsics`` call to its registry op."""
        try:
            spec = primitives.source_intrinsic(symbol)
        except primitives.PrimitiveRegistryError as exc:
            raise LoweringError(
                node, str(exc), code="source-intrinsic-unregistered") from None
        if node.keywords or any(isinstance(arg, ast.Starred)
                                for arg in node.args):
            raise LoweringError(
                node, f"{symbol} accepts positional arguments only",
                code="source-intrinsic-argument-form")
        terms = spec.signature.terms_for(len(node.args))
        if terms is None:
            raise LoweringError(
                node, f"{symbol} takes {len(spec.signature.arguments)} "
                      f"arguments, not {len(node.args)}",
                code="source-intrinsic-arity")

        fixed_arguments = {}
        for index, (term, argument) in enumerate(zip(terms, node.args)):
            literal = None
            if (isinstance(argument, ast.Constant)
                    and type(argument.value) is int):
                literal = argument.value
            elif term is primitives.WIDTH:
                # A construction parameter is as static as a source literal
                # once this particular Behavior instance has been bound.  Use
                # the same occurrence-aware fixed-value authority as loop and
                # slice planning, then materialise the registry's required IR
                # literal.  A run-time input still has no fixed value and is
                # refused below.
                literal = self._static_int(argument)
            elif (term is primitives.SHIFT
                  and isinstance(argument, ast.UnaryOp)
                  and isinstance(argument.op, (ast.UAdd, ast.USub))
                  and isinstance(argument.operand, ast.Constant)
                  and type(argument.operand.value) is int):
                literal = argument.operand.value
                if isinstance(argument.op, ast.USub):
                    literal = -literal
            if term is primitives.WIDTH:
                error = primitives.argument_domain_error(
                    term, literal=literal is not None, value=literal)
            elif term is primitives.SHIFT and literal is not None:
                error = primitives.argument_domain_error(
                    term, literal=True, value=literal)
            else:
                continue
            if error is not None:
                raise LoweringError(
                    argument, f"{symbol} {error}",
                    code="source-intrinsic-argument-domain")
            if term is primitives.WIDTH:
                fixed_arguments[index] = literal

        # ``expr`` preserves Python's left-to-right argument evaluation and
        # the registry remains the only semantic authority after this point.
        # Fixed width expressions are compile-time parameters and enter the
        # IR in the literal form required by that registry.
        return ir.Op(spec.name, *(
            ir.Lit(fixed_arguments[index])
            if index in fixed_arguments else self.expr(argument)
            for index, argument in enumerate(node.args)))

    def _builtin_call(self, node, name):
        """Finite, typed meanings of the scalar/sequence builtins we admit."""
        if name == "sorted":
            return self._builtin_sorted(node)
        if name == "range":
            return self._builtin_range(node)
        if node.keywords:
            raise LoweringError(
                node, f"builtin {name} with keyword arguments",
                code="call-argument-form")
        if name in {"abs", "bool", "int", "reversed", "all", "any"} \
                and len(node.args) != 1:
            raise LoweringError(node, f"builtin {name} with wrong arity",
                                code="call-arity")

        if name == "divmod":
            if len(node.args) != 2:
                raise LoweringError(node, "builtin divmod with wrong arity",
                                    code="call-arity")
            left, right = self._in_order(
                *node.args, lower=self._integer_context_expr)
            if any(isinstance(value, (_BatchValue, OptionValue, RecordValue))
                   or self._shape(value) for value in (left, right)):
                raise LoweringError(
                    node, "divmod consumes non-scalar values",
                    code="builtin-type-unsupported")
            proved_safe = self._partial_operand_is_safe(
                right, node.args[1], primitives.SourceDomain.RIGHT_NONZERO)
            self.reached.add(id(node))
            self._guard(
                ir.Lit(False) if proved_safe else ir.Op("eq", right, ir.Lit(0)),
                self._safety_goal(node, "divzero"), "DivZero", node)
            # One source call evaluates both operands once and owes one
            # failure occurrence. The existing floor-division/remainder
            # primitives preserve Python's signs for negative operands too.
            return ir.Op("arraylit", ir.Op("fdiv", left, right),
                         ir.Op("fmod", left, right))

        if name == "pow":
            from . import modular_power

            if len(node.args) != 3:
                raise LoweringError(
                    node, "pow lowering requires three integer arguments",
                    code="pow-form-unsupported")
            # Decide representation before evaluating operands, but execute
            # every admitted operand once in Python's left-to-right order.
            exponent = self._source_fixed_value(node.args[1])
            modulus = self._source_fixed_value(node.args[2])
            if type(exponent) not in (int, bool) or type(modulus) not in (int, bool):
                raise LoweringError(
                    node, "modular pow needs source-fixed exponent and modulus",
                    code="pow-parameters-not-static")
            values = self._in_order(*node.args, lower=self._integer_context_expr)
            if any(isinstance(value, (_BatchValue, OptionValue, RecordValue))
                   or self._shape(value) for value in values):
                raise LoweringError(node, "pow consumes non-scalar values",
                                    code="builtin-type-unsupported")
            base = values[0]
            self.reached.add(id(node))
            invalid = ir.Lit(modulus == 0)
            if exponent < 0 and modulus:
                base, invalid = modular_power.inverse(base, abs(modulus), self._capture)
            self._guard(invalid, self._safety_goal(node, "valueerror"), "ValueError", node)
            return (modular_power.power(base, abs(exponent), int(modulus), self._capture)
                    if modulus else ir.Lit(0))

        if name == "abs":
            value = self.expr(node.args[0])
            if isinstance(value, RecordValue):
                raise LoweringError(
                    node, "abs of a fixed-schema record",
                    code="record-operation-unsupported")
            if self._shape(value):
                raise LoweringError(
                    node, "abs of an array", code="builtin-type-unsupported")
            return ir.Op(
                "ite", ir.Op("lt", value, ir.Lit(0)), ir.Op("neg", value), value)

        if name == "bool":
            arg = node.args[0]
            return self._truth_expr(arg)

        if name == "int":
            value = self._integer_context_expr(node.args[0])
            if isinstance(value, RecordValue):
                raise LoweringError(
                    node, "int of a fixed-schema record",
                    code="record-operation-unsupported")
            if self._shape(value):
                raise LoweringError(
                    node, "int of an array", code="builtin-type-unsupported")
            # ``int`` is an explicit source conversion even when the local
            # type proof cannot see through an array selection.  The total
            # primitive is identity on integers and maps Booleans to their
            # integer 0/1 values.
            return ir.Op("toint", value)

        if name in {"min", "max"}:
            if not node.args:
                raise LoweringError(node, f"{name} with no arguments", code="call-arity")
            count = None
            if len(node.args) == 1:
                # Share the finite iterator's count and item semantics with
                # loops/sorted. Padding is not an element; evaluate the source
                # only once and preserve the first item on an equal comparison.
                iterator = ast.comprehension(
                    target=ast.Name(id="__extreme_item", ctx=ast.Store()),
                    iter=node.args[0], ifs=[], is_async=0)
                ast.copy_location(iterator, node.args[0])
                iteration = self._dynamic_bound(iterator, node.args[0])
                count, bound, at = iteration.count, iteration.bound, iteration.value
                minimum, _maximum = self.source_loop_spans[id(iterator)]
                if minimum < 1:
                    raise LoweringError(
                        node, f"{name} over a possibly empty iterable needs "
                              "the empty-sequence exception representation",
                        code="builtin-empty-iterable-unsupported")
                values = [ir.Lit(index) if at is None else at(ir.Lit(index))
                          for index in range(bound)]
            else:
                values = self._in_order(*node.args)
            for value in values:
                if isinstance(value, (_BatchValue, OptionValue, RecordValue)) \
                        or self._shape(value):
                    raise LoweringError(
                        node, f"{name} over a non-scalar value",
                        code="builtin-type-unsupported")
            result = values[0]
            comparison = "lt" if name == "min" else "gt"
            for index, value in enumerate(values[1:], 1):
                selected = ir.Op(
                    "ite", ir.Op(comparison, value, result), value, result)
                result = (selected if count is None or isinstance(count, ir.Lit)
                          else ir.Op("ite", ir.Op("lt", ir.Lit(index), count),
                                     selected, result))
            return result

        if name in {"list", "tuple"}:
            if len(node.args) > 1:
                raise LoweringError(node, f"builtin {name} with wrong arity",
                                    code="call-arity")
            if not node.args:
                return ir.Op("arraylit")
            arg = node.args[0]
            if isinstance(arg, ast.GeneratorExp):
                contract = self._contract_ast(node)
                if not contract.is_sequence or contract.maximum is None:
                    raise LoweringError(
                        arg, "a consumed generator has no finite value contract",
                        code="comprehension-length-not-static")
                result = self.storage.fresh()
                planned = ValueContract.sequence(
                    "list", contract.minimum, contract.maximum, contract.element)
                command = self._new_comprehension_sequence(
                    result, arg, planned=planned)
                self.before(command)
                target = self._name(result)
                self.flow.contracts[target] = contract
                self.flow.carriers[target] = name
                return ir.Var(target)
            value = self.expr(arg)
            dynamic = self._dynamic_sequence(value)
            if dynamic is not None:
                _source, length, capacity = dynamic
                result = self.storage.fresh()
                result_length = self.storage.fresh()
                copied = ir.Op(
                    "arraylit", *[ir.Sel(value, ir.Lit(index))
                                  for index in range(capacity)])
                self.before(ir.Seq(
                    ir.Assign(result, copied),
                    ir.Assign(result_length, length)))
                self._register_dynamic_sequence(
                    result, result_length, capacity)
                self.flow.lengths[result] = (capacity,)
                return ir.Var(result)
            shape = self._shape(value)
            if len(shape) != 1:
                raise LoweringError(
                    node, f"{name} of a value with no fixed one-dimensional shape",
                    code="list-source-shape-unsupported")
            return ir.Op(
                "arraylit", *[ir.Sel(value, ir.Lit(i))
                              for i in range(shape[0])])

        if name == "reversed":
            arg = node.args[0]
            value = self.expr(arg)
            contract = self._contract_for_value(arg, value)
            shape = self._shape(value)
            if not contract.is_sequence or not shape:
                raise LoweringError(
                    node, "reversed of a value with no finite sequence "
                          "representation",
                    code="reversed-source-shape-unsupported")
            dynamic = self._dynamic_sequence(value)
            if dynamic is None:
                return ir.Op(
                    "arraylit", *[
                        ir.Sel(value, ir.Lit(index))
                        for index in reversed(range(shape[0]))
                    ])

            _source, length, capacity = dynamic
            child_shape = shape[1:]
            cells = []
            for index in range(capacity):
                source_index = ir.Op(
                    "sub", ir.Op("sub", length, ir.Lit(1)), ir.Lit(index))
                cells.append(ir.Op(
                    "ite", ir.Op("lt", ir.Lit(index), length),
                    ir.Sel(value, source_index), lower_returns.zero_shape(child_shape)))
            result, result_length = self.storage.fresh(), self.storage.fresh()
            self.before(ir.Seq(
                ir.Assign(result, ir.Op("arraylit", *cells)),
                ir.Assign(result_length, length)))
            self.storage.require_shape(result, shape, node)
            self.flow.lengths[result] = shape
            self.flow.contracts[result] = contract
            if contract.carrier is not None:
                self.flow.carriers[result] = contract.carrier
            self._register_dynamic_sequence(
                result, result_length, capacity)
            return ir.Var(result)

        if name == "sum":
            return self._builtin_sum(node)

        if name in {"all", "any"}:
            return self._builtin_truth_reduce(node, name)

        raise AssertionError(name)

    def _builtin_range(self, node):
        """Materialize the same finite range domain used by source loops."""
        if not has_plain_positional_arity(node, 1, 3):
            raise LoweringError(node, "range requires one to three positional arguments",
                                code="call-argument-form")
        iterator = ast.comprehension(
            target=ast.Name(id="__range_item", ctx=ast.Store()),
            iter=node, ifs=[], is_async=0)
        ast.copy_location(iterator, node)
        iteration = self._dynamic_bound(iterator, node)
        count, bound, at = iteration.count, iteration.bound, iteration.value
        contract = self._contract_ast(node)
        if not contract.is_sequence or contract.carrier != "range":
            raise LoweringError(node, "range has no finite source contract",
                                code="range-source-shape-unsupported")
        # Preserve closed element domains so membership keeps the same exact
        # interval refinement as a literal tuple. Argument effects have
        # already executed through the shared iteration plan.
        if contract.exact_length is not None:
            values = [ir.Lit(index) if at is None else at(ir.Lit(index))
                      for index in range(contract.exact_length)]
            fixed = [ranges.of_expression(value, self.flow.ranges) for value in values]
            if all(item is not None and item.lo == item.hi for item in fixed):
                return ir.Op("arraylit", *(ir.Lit(item.lo) for item in fixed))
        count = ir.Op("ite", ir.Op("gt", count, ir.Lit(0)), count, ir.Lit(0))
        result, length = self.storage.fresh(), self.storage.fresh()
        capacity = max(1, bound)
        cells = [ir.Op("ite", ir.Op("lt", ir.Lit(index), count),
                       ir.Lit(index) if at is None else at(ir.Lit(index)), ir.Lit(0))
                 for index in range(capacity)]
        self.before(ir.Seq(ir.Assign(result, ir.Op("arraylit", *cells)),
                           ir.Assign(length, count)))
        self.flow.lengths[result] = (capacity,)
        self.flow.contracts[result] = contract
        self.flow.carriers[result] = "range"
        self._register_dynamic_sequence(result, length, capacity)
        return ir.Var(result)

    def _builtin_truth_reduce(self, node, name):
        """Reduce a finite scalar sequence or consumed generator by Python truth.

        Constructing the argument precedes reduction, even when an early item
        determines the answer. Padding in a bounded live-length carrier is not
        an item. Generator filters and elements instead stop at the first
        decisive value, using the same finite-iteration lowering.
        """
        source = node.args[0]
        identity = ir.Lit(name == "all")
        if isinstance(source, ast.GeneratorExp):
            result = self.storage.fresh()
            # Later joins and numeric consumers must see the reducer's
            # Boolean type even though its value is carried by a temporary.
            self._remember_source_scalar_type(result, value_types.BOOLEAN)
            active = (ir.Var(result) if name == "all" else
                      ir.Op("not", ir.Var(result)))

            def emit():
                return ir.Assign(result, self._truth_expr(source.elt))

            command = self._comprehension_command(source, emit, active=active)
            self.before(ir.Seq(ir.Assign(result, identity), command))
            return ir.Var(result)
        value = self.expr(source)
        shape = self._shape(value)
        if len(shape) != 1:
            raise LoweringError(
                source, f"{name} of a value without a finite scalar sequence",
                code="truth-reduction-source-shape-unsupported")
        dynamic = self._dynamic_sequence(value)
        length = None if dynamic is None else dynamic[1]
        result = identity
        operator = "land" if name == "all" else "lor"
        for index in range(shape[0]):
            item = ir.Op("truth", ir.Sel(value, ir.Lit(index)))
            if length is not None:
                item = ir.Op("ite", ir.Op("lt", ir.Lit(index), length),
                             item, identity)
            result = ir.Op(operator, result, item)
        return result

    def _builtin_sorted(self, node):
        """Stable sorting of a finite sequence with an optional finite key.

        The iterable is consumed once and the key is evaluated once per
        present item in source order.  A stable adjacent-exchange network then
        sorts the fixed representation.  For a run-time-length sequence, both
        key evaluation and exchanges are guarded by its exact length, so its
        padding is never treated as an item.
        """
        if len(node.args) != 1:
            raise LoweringError(node, "sorted with wrong arity", code="call-arity")
        keywords = {}
        for keyword in node.keywords:
            if keyword.arg not in {"key", "reverse"} or keyword.arg in keywords:
                raise LoweringError(
                    keyword, "sorted with an unsupported or repeated keyword",
                    code="call-argument-form")
            keywords[keyword.arg] = keyword.value

        reverse_node = keywords.get("reverse")
        if reverse_node is None:
            reverse = False
        elif isinstance(reverse_node, ast.Constant) \
                and type(reverse_node.value) is bool:
            reverse = reverse_node.value
        else:
            raise LoweringError(
                reverse_node, "sorted with a non-constant reverse flag",
                code="sorted-reverse-not-static")

        key = keywords.get("key")
        if isinstance(key, ast.Constant) and key.value is None:
            key = None
        if key is not None:
            if not isinstance(key, ast.Lambda):
                raise LoweringError(
                    key, "sorted key that is not an inline lambda",
                    code="sorted-key-unsupported")
            args = key.args
            if (len(args.args) != 1 or args.posonlyargs or args.kwonlyargs
                    or args.vararg is not None or args.kwarg is not None
                    or args.defaults or args.kw_defaults):
                raise LoweringError(
                    key, "sorted key lambda with anything but one parameter",
                    code="sorted-key-signature")
            if self._effectful(key.body):
                raise LoweringError(
                    key.body, "sorted key with source-visible side effects",
                    code="sorted-key-effectful")

        # The loop iterable rule already owns the exact count, finite upper
        # bound, and item-at-index semantics.  Sorting consumes the same finite
        # iterable and therefore uses that single authority.
        target = ast.Name(id="__sorted_item", ctx=ast.Store())
        iterator = ast.comprehension(
            target=target, iter=node.args[0], ifs=[], is_async=0)
        ast.copy_location(iterator, node.args[0])
        iteration = self._dynamic_bound(iterator, node.args[0])
        count, bound, at = iteration.count, iteration.bound, iteration.value
        if bound == 0 and key is not None:
            self.before(self._false_goal_terms((key.body,)))
        values = [ir.Lit(index) if at is None else at(ir.Lit(index))
                  for index in range(bound)]
        item_shapes = [self._shape(value) for value in values]
        item_shape = item_shapes[0] if item_shapes else ()
        if any(shape != item_shape for shape in item_shapes):
            raise LoweringError(
                node, "sorted iterable with differently shaped elements",
                code="sorted-element-shape-inconsistent")

        result = self.storage.fresh()
        materialize = ir.Assign(result, ir.Op("arraylit", *values))
        result_shape = (bound,) + item_shape
        self.flow.lengths[result] = result_shape
        dynamic = not isinstance(count, ir.Lit)
        if dynamic:
            result_length = self.storage.fresh()
            materialize = ir.Seq(
                materialize, ir.Assign(result_length, count))
            self._register_dynamic_sequence(result, result_length, bound)
        self.before(materialize)

        # Materialise key values before sorting.  Besides matching Python's
        # one-call-per-item rule, this prevents later swaps from changing what
        # an already computed key denotes.
        key_values = []
        parameter = key.args.args[0].arg if key is not None else None
        old = self.activation.substitutions.get(parameter) if parameter is not None else None
        had_old = parameter in self.activation.substitutions if parameter is not None else False
        try:
            for index, value in enumerate(values):
                if parameter is not None:
                    self.activation.substitutions[parameter] = value
                    before, got = self._scoped(lambda: self.expr(key.body))
                else:
                    before, got = ir.Skip(), value
                if dynamic:
                    active = ir.Op("lt", ir.Lit(index), count)
                    self.before(ir.If(active, before, ir.Skip()))
                else:
                    self.before(before)
                key_values.append(got)
        finally:
            if parameter is not None:
                if had_old:
                    self.activation.substitutions[parameter] = old
                else:
                    self.activation.substitutions.pop(parameter, None)

        key_shapes = [self._shape(value) for value in key_values]
        key_shape = key_shapes[0] if key_shapes else ()
        if len(key_shape) > 1 or any(shape != key_shape for shape in key_shapes):
            raise LoweringError(
                key or node, "sorted key with no uniform scalar or tuple shape",
                code="sorted-key-shape-unsupported")
        components = key_shape[0] if key_shape else 1
        key_arrays = []
        for component in range(components):
            name = self.storage.fresh()
            cells = [(ir.Sel(value, ir.Lit(component)) if key_shape else value)
                     for value in key_values]
            self.before(ir.Assign(name, ir.Op("arraylit", *cells)))
            self.flow.lengths[name] = (bound,)
            key_arrays.append(name)

        def lexicographic(left, right):
            prefix = ir.Lit(True)
            order = ir.Lit(False)
            relation = "lt" if reverse else "gt"
            for name in key_arrays:
                array = ir.Var(name)
                first = ir.Sel(array, ir.Lit(left))
                second = ir.Sel(array, ir.Lit(right))
                order = ir.Op(
                    "lor", order,
                    ir.Op("land", prefix,
                          ir.Op(relation, first, second)))
                prefix = ir.Op(
                    "land", prefix, ir.Op("eq", first, second))
            return order

        def choose(condition, when_true, when_false, shape):
            if not shape:
                return ir.Op("ite", condition, when_true, when_false)
            return ir.Op(
                "arraylit", *[
                    choose(condition,
                           ir.Sel(when_true, ir.Lit(index)),
                           ir.Sel(when_false, ir.Lit(index)),
                           shape[1:])
                    for index in range(shape[0])])

        def exchange(name, left, right, shape, swap):
            array = ir.Var(name)
            cells = []
            for index in range(bound):
                if index == left:
                    cells.append(choose(
                        swap, ir.Sel(array, ir.Lit(right)),
                        ir.Sel(array, ir.Lit(left)), shape))
                elif index == right:
                    cells.append(choose(
                        swap, ir.Sel(array, ir.Lit(left)),
                        ir.Sel(array, ir.Lit(right)), shape))
                else:
                    cells.append(ir.Sel(array, ir.Lit(index)))
            self.before(ir.Assign(name, ir.Op("arraylit", *cells)))

        self._spend(bound * max(1, bound - 1) // 2)
        for stop in range(bound - 1, 0, -1):
            for left in range(stop):
                right = left + 1
                condition = lexicographic(left, right)
                if dynamic:
                    condition = ir.Op(
                        "land", ir.Op("lt", ir.Lit(right), count), condition)
                held = self.storage.fresh()
                self.before(ir.Assign(held, condition))
                swap = ir.Var(held)
                exchange(result, left, right, item_shape, swap)
                for name in key_arrays:
                    exchange(name, left, right, (), swap)
        return ir.Var(result)

    def _builtin_sum(self, node):
        """Sum a fixed array or a bounded generator in source order."""
        if not 1 <= len(node.args) <= 2:
            raise LoweringError(node, "sum with wrong arity", code="call-arity")
        source = node.args[0]
        start_node = node.args[1] if len(node.args) == 2 else None
        if isinstance(source, ast.GeneratorExp):
            # Constructing a generator evaluates its outer iterable before
            # evaluating ``start``.  The bounded IR consumes it eagerly; an
            # effectful outer iterable together with an explicit start would
            # therefore need a suspended-generator representation.
            if start_node is not None and self._effectful(
                    source.generators[0].iter):
                raise LoweringError(
                    node, "sum(generator, start) with an effectful iterable",
                    code="generator-evaluation-order-unsupported")
            result = self.storage.fresh()
            start = ir.Lit(0) if start_node is None else self.expr(start_node)

            def emit():
                return ir.Assign(
                    result, ir.Op("add", ir.Var(result),
                                  self._integer_context_expr(source.elt)))

            command = self._comprehension_command(source, emit)
            self.before(ir.Seq(ir.Assign(result, start), command))
            return ir.Var(result)

        values = self._in_order(*node.args)
        array = values[0]
        start = values[1] if len(values) == 2 else ir.Lit(0)
        dynamic = self._dynamic_sequence(array)
        if dynamic is not None:
            _name, length, capacity = dynamic
            terms = [ir.Op(
                "ite", ir.Op("lt", ir.Lit(i), length),
                ir.Op("toint", ir.Sel(array, ir.Lit(i))), ir.Lit(0))
                     for i in range(capacity)]
        else:
            shape = self._shape(array)
            if len(shape) != 1:
                raise LoweringError(
                    node, "sum of a value with no fixed one-dimensional shape",
                    code="sum-source-shape-unsupported")
            terms = [ir.Op("toint", ir.Sel(array, ir.Lit(i)))
                     for i in range(shape[0])]
        # Array construction and start have already executed in source order.
        # The remaining terms are pure mathematical integers, so balancing
        # addition preserves the result without a capacity-deep expression.
        return ir._balanced_op("add", (start, *terms), ir.Lit(0))

    def _binary_digit_count(self, node):
        """Count binary digits without materializing a Python string in IR.

        Builtin ``bin`` formats the integer magnitude, including for negative
        integers. Its ``0b`` prefix contributes one zero; zero itself has one
        additional zero digit. Other string operations retain their normal
        support boundary, and a shadowed ``bin`` is never this intrinsic.
        """
        counted = source_intrinsics.binary_digit_count(
            node, is_builtin=self._spelling_is_builtin)
        if counted is None:
            return None
        operand, digit = counted
        value = self._integer_context_expr(operand)
        if self._shape(value):
            raise LoweringError(
                operand, "bin of an array", code="builtin-type-unsupported")
        ones = ir.Op("popcount", value)
        if digit == "1":
            return ones
        return ir.Op(
            "ite", ir.Op("eq", value, ir.Lit(0)), ir.Lit(2),
            ir.Op("add", ir.Op("sub", ir.Op("bitlen", value), ones), ir.Lit(1)))

    def _integer_method(self, node, name):
        """Exact finite lowering of ``int.bit_count`` and ``int.bit_length``."""
        if node.args or node.keywords:
            raise LoweringError(node, f"{name} with arguments", code="call-arity")
        value = self.expr(node.func.value)
        if self._shape(value):
            raise LoweringError(
                node, f"{name} of an array", code="builtin-type-unsupported")
        primitive_name = "popcount" if name == "bit_count" else "bitlen"
        return ir.Op(primitives.primitive(primitive_name).name, value)

    def _spelling_is_builtin(self, name):
        """Whether ``name`` still means the builtin in the current scope."""
        if name in self.activation.substitutions:
            return False
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        if resolver is None or scope is None:
            return (name not in self.functions
                    and name not in getattr(self, "shadowed", ()))
        return resolver.is_builtin(scope, name)

    @staticmethod
    def _command_sequence(*commands):
        """Sequence nonempty semantic commands without manufacturing nodes."""

        result = None
        for command in commands:
            if command is None or isinstance(command, ir.Skip):
                continue
            result = command if result is None else ir.Seq(result, command)
        return ir.Skip() if result is None else result

    def _fixed_width_helper_events(self, function, summary, wrapped):
        """Preserve every source goal while replacing a certified data path.

        ``_false_goal_terms`` gives every declared occurrence an explicit term.
        The compact commands below then add the conditions of occurrences the
        certified helper actually reaches.  Duplicate false terms are harmless
        under the catalogue's disjunctive hit semantics and, importantly, a
        future goal family added inside these source forms remains accounted
        for rather than disappearing behind the summary.
        """

        body = function.body
        if summary.form == "return":
            live = self._location_mark(body[0])
        elif summary.form == "if":
            branch = body[1]
            conditional = self._marked_if(
                branch.test,
                ir.Op("lt", wrapped, ir.Lit(0)),
                self._location_mark(branch.body[0]),
                ir.Skip(),
            )
            live = self._command_sequence(
                self._location_mark(body[0]),
                self._location_mark(branch),
                conditional,
                self._location_mark(body[2]),
            )
        elif summary.form == "ifexp":
            returned = body[1]
            conditional = self._marked_if(
                returned.value.test,
                ir.Op("lt", wrapped, ir.Lit(0)),
                ir.Skip(),
                ir.Skip(),
            )
            live = self._command_sequence(
                self._location_mark(body[0]),
                self._location_mark(returned),
                conditional,
            )
        else:  # the certificate type is closed in source_intrinsics
            raise AssertionError(f"unknown fixed-width helper form {summary.form}")
        return self._command_sequence(live, self._false_goal_terms(body))

    def _fixed_width_helper_call(self, node, function, summary):
        """Lower a certified canonical wrap, or decline to summarize it."""

        if (node.keywords or len(node.args) != 2
                or any(isinstance(argument, ast.Starred)
                       for argument in node.args)):
            return None
        width = self._static_int(node.args[1])
        if (type(width) is not int
                or primitives.argument_domain_error(
                    primitives.WIDTH, literal=True, value=width) is not None):
            # The ordinary body remains the semantic fallback.  In particular,
            # a dynamic or nonpositive width can execute source failure paths
            # that no fixed-width primitive claims to represent.
            return None
        # The source body uses its arguments arithmetically. Retain Python's
        # Boolean-to-integer promotion, including mixed conditional operands,
        # before constructing the integer-only primitive.
        value, _evaluated_width = self._in_order(
            *node.args, lower=self._integer_context_expr)
        wrapped = ir.Op(summary.symbol, value, ir.Lit(width))
        if function not in self.inlined:
            self.inlined.append(function)
        self.before(self._fixed_width_helper_events(
            function, summary, wrapped))
        return wrapped

    def _inline(self, node, fn, *, result_kind=None):
        """Inline a call: bind the arguments, lower the body, take the return.

        Ordinary helpers stay inside the source semantics.  The canonical
        fixed-width wraps are summarized only after their complete signature
        and body prove equality to a registered primitive; every other body
        follows this ordinary path.
        """
        summary = source_intrinsics.certified_fixed_width_helper(fn)
        if summary is not None:
            value = self._fixed_width_helper_call(node, fn, summary)
            if value is not None:
                return value
        resolver = getattr(self, "resolver", None)
        # `self` is the receiver only for a model method; a nested or module
        # function keeps a parameter spelled self (review 49).
        is_model_method = (resolver is not None
                           and id(fn) in resolver._method_ids)
        actuals = []
        fixed_arguments = []
        keywords = []
        # Evaluate positional expressions (including fixed star expansion)
        # before keywords, each in written order. Only then bind formals.
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        last_effect = self._last_effectful_operand(arguments)
        for index, argument in enumerate(arguments):
            source = (argument.value if isinstance(argument, ast.Starred)
                      else argument)
            fixed_value = self._source_fixed_value(source)
            static_argument = False
            if lower_static_refs.is_reference(self, source):
                # A selected scalar has an ordinary IR representation.  A
                # remaining collection reference may cross this boundary only
                # because the callee is inlined and therefore remains under
                # the same mutation/identity/escape checks as the caller.
                value = lower_static_refs.lower_reference(
                    self, source, preserve_collection=True,
                    preserve_reference=lower_static_refs.requires_reference(self, source))
                if value is None:
                    raise LoweringError(
                        source, "a finite source collection has no exact "
                                "call argument representation",
                        code="static-collection-escapes")
                static_argument = isinstance(value, _StaticRef)
            else:
                value = self.expr(source)
            later_effect = index < last_effect

            if not isinstance(argument, ast.Starred):
                if later_effect:
                    value = (self._capture_product(value)
                             if isinstance(value, RecordValue)
                             else self._capture(value))
                actual = (
                    value, source,
                    self._contract_ast(source) if static_argument else None,
                    None)
                if index < len(node.args):
                    actuals.append(actual)
                    fixed_arguments.append(fixed_value)
                else:
                    keywords.append((node.keywords[index - len(node.args)].arg,
                                     (actual, fixed_value)))
                continue

            if static_argument:
                raise LoweringError(
                    source, "a static collection cannot supply a variable "
                            "number of call arguments",
                    code="call-starred-argument-not-fixed")

            if isinstance(value, _PositionalValue):
                contract = value.contract
                elements = fixed_positional_contracts(contract)
                if elements is None or len(elements) != len(value.items):
                    raise LoweringError(
                        source, "a starred positional product has no fixed "
                                "immutable element contract",
                        code="call-starred-argument-not-fixed")
                values = tuple(value.items)
                scalar_types = tuple(value.scalar_types)
                if later_effect:
                    values = tuple(self._capture(item) for item in values)
            else:
                contract = self._contract_for_value(source, value)
                elements = fixed_positional_contracts(contract)
                if elements is None:
                    raise LoweringError(
                        source, "a starred call argument is not a fixed "
                                "list or tuple of immutable values",
                        code="call-starred-argument-not-fixed")
                expected = contract.fixed_shape
                shape = self._shape(value)
                if expected is None or shape != expected:
                    raise LoweringError(
                        source, "a starred call argument has no matching "
                                "fixed value-array representation",
                        code="call-starred-argument-not-fixed")
                if later_effect:
                    value = self._capture(value)
                values = tuple(ir.Sel(value, ir.Lit(item))
                               for item in range(len(elements)))
                scalar_types = tuple(
                    None if element.fixed_shape else
                    _fixed_expression_type(
                        item, self.source_scalar_types.get)
                    for item, element in zip(values, elements))

            if type(fixed_value) in (list, tuple) \
                    and len(fixed_value) == len(elements):
                element_fixed = tuple(fixed_value)
            else:
                element_fixed = (None,) * len(elements)
            actuals.extend(
                (item, source, contract, scalar_type)
                for item, contract, scalar_type in zip(
                    values, elements, scalar_types))
            fixed_arguments.extend(element_fixed)

        def default(name, expression):
            source = (resolver.helper_default(fn, name, expression)
                      if resolver is not None else
                      expression if isinstance(expression, ast.Constant) else None)
            if source is None:
                raise CallBindingError(
                    f"default for {fn.name}.{name} has no frozen scalar representation",
                    "call-default-unrepresented")
            value = self.expr(source)
            return ((value, source, contract_of_value(source.value), None),
                    source.value)

        try:
            params, bound = bind_helper_arguments(
                fn, tuple(zip(actuals, fixed_arguments)), keywords,
                receiver=is_model_method, default=default)
        except CallBindingError as exc:
            raise LoweringError(node, str(exc), code=exc.code) from None
        actuals = [value for value, _fixed in bound]
        fixed_arguments = tuple(fixed for _value, fixed in bound)
        # Namespace-renaming frames are an implementation detail: the root
        # frame may now exist solely because a source parameter is rebound.
        # Count actual inlined source activations so that harmless root
        # renaming cannot consume one level of the certified call depth.
        depth = max(0, sum(frame.scope is not None
                           for frame in self.activation.frames()) - 1)
        if helper_activation_exceeds(depth + 1):
            raise LoweringError(node, f"call to {fn.name} nested too deeply", code="call-inlining-too-deep")
        if resolver is not None and resolver.scope_rebinds_self(fn):
            raise LoweringError(
                node, f"{fn.name} rebinds self, so nothing in it may be "
                      f"read as the model", code="self-rebound")
        return self._inline_scoped(
            node, fn, params, actuals, tuple(fixed_arguments), result_kind=result_kind)

    def _bind_inline_argument(self, target, value, source, contract=None,
                              scalar_kind=None):
        """Bind one evaluated actual to an inlined formal representation."""
        if isinstance(value, _StaticRef):
            command, bound = value.bind(self, target, source)
            self.flow.static_values[target] = bound
            # Static discriminators such as source strings intentionally have
            # no SolverIR value contract.  The encoded local is only a tag;
            # keep a source contract when the planner has one, but do not
            # invent a scalar contract for that tag.
            if (isinstance(contract, ValueContract)
                    and contract not in {UNKNOWN, INCOMPATIBLE}):
                self.flow.contracts[target] = contract
            else:
                self.flow.contracts.pop(target, None)
                contract = UNKNOWN
            self.flow.lengths.pop(target, None)
            self.flow.carriers.pop(target, None)
            self.flow.ranges.pop(target, None)
            self.flow.bounds.pop(target, None)
            self.flow.unknown_ranges.discard(target)
            return command, contract
        if isinstance(value, OptionValue):
            if not value.contract.is_option:
                raise LoweringError(
                    source, "a None-only helper argument has no finite "
                            "payload contract",
                    code="optional-payload-contract-unresolved")
            command = self._bind_option(
                target, value, source, value.contract, source=source)
            return command, value.contract
        if isinstance(value, RecordValue):
            builder = _RecordBuilder.allocate(
                self, value.contract, source, order=value.order)
            self.storage.bind_carrier(target, builder)
            self.flow.contracts[target] = value.contract
            return builder.assign(self, value, source), value.contract

        command = ir.Assign(target, value)
        self._record_range(target, value)
        contract = (self._contract_for_value(source, value)
                    if contract is None else contract)
        shape = self._shape(value)
        storage = self._storage_contract(value, contract)
        represented_sequence = (
            isinstance(contract, ValueContract) and contract.is_sequence
            and isinstance(storage, ValueContract) and storage.is_sequence
            and storage.physical_shape == shape
            and contract_contains(storage, contract))
        if (not isinstance(contract, ValueContract)
                or contract in {UNKNOWN, INCOMPATIBLE}
                or contract.has_dynamic_descendant
                or (contract.physical_shape is not None
                    and contract.physical_shape != shape
                    and not represented_sequence)):
            raise LoweringError(
                source, "an expanded positional argument disagrees with its "
                        "lowered representation",
                code="source-value-contract-disagrees")
        self.flow.contracts[target] = contract
        self._remember_source_scalar_type(
            target,
            (None if shape else scalar_kind if scalar_kind is not None else
             self._source_scalar_type_for_value(source, value)))
        self.storage.require_shape(target, shape, source)
        if shape:
            self.flow.lengths[target] = shape
            carrier = contract.carrier
            if carrier is not None:
                self.flow.carriers[target] = carrier
            self._rebind(target, source)
            if represented_sequence:
                # A guarded prefix can be shorter than its fixed backing.
                # Carry the same storage and logical lengths across the call,
                # rather than turning padding into helper-visible elements.
                self.storage.storage_contracts[target] = storage
                command = ir.Seq(
                    command, self._assign_length_layout(
                        target, value, contract, storage, source))
            elif contract.is_sequence and contract.exact_length is None:
                length = self._length_target(target, shape[0], source)
                command = ir.Seq(command, ir.Assign(
                    length, self._logical_length(source, value, contract)))
        else:
            self.flow.lengths.pop(target, None)
            self.flow.carriers.pop(target, None)
        return command, contract

    def _inline_scoped(self, node, fn, params, actuals, fixed_arguments, *, result_kind=None):
        # Recorded here rather than read off `self.functions`, which holds every helper
        # the model defines. What the declared catalogue is made over is the code that
        # is lowered, and a helper nothing calls is not lowered.
        if fn not in self.inlined:
            self.inlined.append(fn)

        caller_facts = self._fact_name_snapshot()

        # Every name the body uses is renamed, so an inlined body can neither
        # read nor overwrite a variable of the caller.
        rename = {}
        for p in params:
            rename[p] = self.storage.fresh()
        for n in ast.walk(fn):
            if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                    and n.id not in rename):
                rename[n.id] = self.storage.fresh()

        bind = None
        argument_contracts = []
        # A non-rebound formal may name an owned caller-local array directly.
        # Copying it and rejecting its writes loses ordinary Python helper
        # semantics. Do not borrow state, an aliased object, a captured argument
        # from before later effects, or a closure's binding.
        borrowed = {}
        if (self.resolver is not None and id(fn) in self.resolver._method_ids
                and not any(isinstance(argument, ast.Starred) for argument in node.args)):
            retained = non_rebound_parameters(fn, receiver=True)
            argument_counts = Counter(
                arg.name for arg, _written, _contract, _kind in actuals
                if isinstance(arg, ir.Var))
            for parameter, (arg, written, contract, _kind) in zip(params, actuals):
                if (parameter in retained and isinstance(written, ast.Name)
                        and isinstance(arg, ir.Var) and arg.name == self._name(written.id)
                        and argument_counts[arg.name] == 1
                        and arg.name not in self.storage.state_names.values()
                        and len(self.shared.get(arg.name, ())) <= 1):
                    represented = (self._contract_for_value(written, arg)
                                   if contract is None else contract)
                    if (represented.is_sequence and represented.carrier in {"list", "bytearray"}
                            and represented.element == SCALAR and represented.exact_length is not None):
                        rename[parameter] = arg.name
                        borrowed[parameter] = represented
        bindings = {p: written.id for p, (_arg, written, _contract, _kind)
                    in zip(params, actuals) if p in borrowed}
        previous = self.borrowed_arguments.get(id(node))
        self.borrowed_arguments[id(node)] = (
            bindings if previous is None else
            {p: name for p, name in previous.items() if bindings.get(p) == name})
        # The source argument beside the lowered one, because `_rebind` below asks which
        # name the value came from and that is a question about the syntax.
        for p, (arg, written, contract, scalar_kind) in zip(
                params, actuals):
            if p in borrowed:
                one, contract = ir.Skip(), borrowed[p]
            else:
                one, contract = self._bind_inline_argument(
                    rename[p], arg, written, contract, scalar_kind)
            argument_contracts.append(contract)
            bind = one if bind is None else ir.Seq(bind, one)

        planned_return = self._planned_call_contract(
            node, argument_contracts, fixed_arguments=fixed_arguments)
        callee_context = self._planned_callee_context(
            node, argument_contracts, fixed_arguments)
        if self.sequence_plan is not None and callee_context is None:
            raise LoweringError(
                node, "the source plan has no activation matching this "
                      "helper call and its lexical environment",
                code="source-summary-context-missing")
        if self.sequence_plan is not None:
            planned_ranges = self.sequence_plan.initial_ranges(
                fn, callee_context)
            for parameter in params:
                interval = planned_ranges.get(parameter)
                if interval is None:
                    continue
                target = rename[parameter]
                planned = ranges.Bounds.of(interval)
                current = self.flow.bounds.get(target)
                selected = (planned if current is None else
                            current.meet(planned))
                if selected is None:
                    raise LoweringError(
                        node, "the retained helper argument range does not "
                              "overlap its source activation plan",
                        code="source-range-plan-inconsistent")
                # The source planner follows the same exact call activation,
                # including branch and helper-return ranges.  It can therefore
                # be tighter than the local IR interval calculation without
                # being a different semantic authority.
                self.flow.bounds[target] = selected
                finite = selected.finite()
                if finite is None:
                    self.flow.ranges.pop(target, None)
                else:
                    self.flow.ranges[target] = finite

        # A helper called for its effect on the state returns nothing, and the
        # inlining is the same either way: the body runs, and the result
        # variable is simply never read.
        result, done = self.storage.fresh(), self.storage.fresh()
        batch_payload = record_batch_payload(planned_return)
        if batch_payload is not None:
            element = batch_payload.element
            fields = (() if element is None else
                      tuple(element.field_contracts))
            self.storage.bind_carrier(result, _BatchBuilder(
                self.storage.fresh(),
                {field: self.storage.fresh() for field in fields},
                batch_payload.maximum, form=self.storage.fresh()))
            self.flow.contracts[result] = planned_return
        elif planned_return.is_record:
            self.storage.bind_carrier(result, _RecordBuilder.allocate(self, planned_return, node))
            self.flow.contracts[result] = planned_return
        elif planned_return.is_option:
            self.storage.bind_carrier(result, _OptionBuilder.allocate(self, planned_return, node))
            self.flow.contracts[result] = planned_return
        # Validate the complete return carrier before entering the callee.
        # These source-plan checks may refuse; no caller frame may be replaced
        # until they succeed. Fresh result names do not depend on lexical scope.
        result_length = (self.storage.fresh()
                         if planned_return.is_sequence
                         and planned_return.exact_length is None
                         and batch_payload is None else None)
        parent = (None if self.resolver is None else
                  self.resolver._parents.get(id(node)))
        positional_arity = None
        if (isinstance(parent, ast.Assign) and parent.value is node
                and len(parent.targets) == 1
                and isinstance(parent.targets[0], (ast.Tuple, ast.List))
                and not any(isinstance(item, ast.Starred)
                            for item in parent.targets[0].elts)
                and _needs_positional_return(
                    fn, len(parent.targets[0].elts))):
            positional_arity = len(parent.targets[0].elts)
            if (not planned_return.is_sequence
                    or planned_return.exact_length != positional_arity
                    or planned_return.carrier not in {"list", "tuple"}):
                raise LoweringError(
                    node, "a directly unpacked helper has no matching fixed "
                          "positional return contract",
                    code="helper-return-shape-disagrees")
        result_discarded = (isinstance(parent, ast.Expr)
                            and parent.value is node)
        value_return = _ValueReturn(
            done, result, result_length, required=not result_discarded,
            planned=planned_return, positional_arity=positional_arity,
            result_kind=result_kind)
        with self._call_activation(fn, rename, callee_context, value_return):
            body = self._inlined(node, fn, bind)
        self._discard_inlined_local_facts(
            caller_facts,
            self.storage.returned_names(result, result_length))
        if body is not None:
            self.before(body)
        return value_return.publish(self, node, helper_name=fn.name)

    def _inlined(self, node, fn, bind):
        """The helper's body, as a command to run before the caller reads it.

        The seam between the two lowerings, and it is here because this is the
        one thing about inlining that differs: a term wants the body as a
        command it can put in front of the read, and a graph wants it emitted
        into blocks of its own. Everything above is about argument binding and
        renaming, which is the same either way. Returning ``None`` says the body
        has already been placed.
        """
        inner, ways = self.body(fn.body)
        # A helper that can finish without returning falls off its end, and
        # Python's answer to that is None, which this semantics has no
        # value for. Refused rather than given a number, and the completion
        # set decides it exactly rather than a walk over the source.
        if NORMAL in ways and self.activation.destination.required \
                and not self.activation.destination.planned.is_option:
            raise LoweringError(
                node, f"{fn.name} can finish without returning, and a "
                      f"helper that falls off its end returns None", code="helper-may-not-return")
        reaching = list(self.activation.return_facts)
        if NORMAL in ways:
            reaching.append(self._flow_facts())
        if reaching:
            self._set_joined_flow_facts(reaching)
        # The result is bound where it is introduced, before anything
        # conditional writes it. Returns inside the body are predicated on
        # a flag, so the writes land in branches that are complementary and
        # do not look it: nothing in the representation relates `if x` to
        # the `if not done` after it, and the caller's read is then a read
        # the definite-assignment analysis cannot account for. Binding here
        # makes the read dominated by a definition as a matter of shape.
        #
        # The zero is never the value read, and that is what the refusal
        # above buys: every path through the body assigns over it.
        #
        # Predicated term returns need a dominating definition.  Its recursive
        # type comes from the return contract collected while lowering the body;
        # searching only for a literal assignment misses ``return self.array``.
        initialize = self.activation.destination.initialize(self, node)
        body = ir.Seq(initialize, inner)
        return body if bind is None else ir.Seq(bind, body)

    def _sequence_repeat(self, node, sequence, sequence_source, shape,
                         count_source, count_value):
        """Repeat a finite sequence without changing observable aliasing."""
        contract = self._contract_for_value(sequence_source, sequence)
        count_bounds = ranges.bounds_of_expression(count_value, self.flow.bounds)
        count_range = (None if count_bounds is None else
                       count_bounds.finite())
        if count_range is None:
            raise LoweringError(
                count_source, "sequence repetition whose count has no finite "
                              "range at this source occurrence",
                code="sequence-repeat-count-unbounded")
        minimum_count = max(0, count_range.lo)
        maximum_count = max(0, count_range.hi)
        if (contract.carrier not in {"list", "tuple", *BYTE_CARRIERS}
                or contract.physical_shape != shape):
            raise LoweringError(
                sequence_source, "sequence repetition without one proved "
                                 "finite Python carrier and physical layout "
                                 f"(contract {contract.carrier!r}/"
                                 f"{contract.physical_shape!r}, lowered "
                                 f"{shape!r})",
                code="sequence-repeat-contract-unproven")
        # Python reuses references to the repeated elements while SolverIR
        # copies values.  They agree exactly for scalars and recursively
        # immutable tuples, but not for a list hidden at any depth.
        if not sequence_elements_are_deeply_immutable(contract):
            raise LoweringError(
                sequence_source, "sequence repetition may duplicate a "
                                 "mutable element reference",
                code="sequence-repeat-alias-unsupported")
        size = shape[0] * maximum_count
        child_shape = shape[1:]
        if size == 0 and child_shape:
            raise LoweringError(
                node, "an empty nested sequence has no element from which "
                      "SolverIR can recover its recursive type",
                code="nested-empty-sequence-untyped")
        self._spend(size)
        source_dynamic = self._dynamic_sequence(sequence)
        source_length = (source_dynamic[1] if source_dynamic is not None
                         else ir.Lit(contract.exact_length))
        effective_count = (
            ir.Lit(maximum_count)
            if minimum_count == maximum_count else
            ir.Op("ite", ir.Op("lt", count_value, ir.Lit(0)),
                  ir.Lit(0), count_value))
        cells = []
        if shape[0]:
            for index in range(size):
                source_index = ir.Op(
                    "fmod", ir.Lit(index), source_length)
                cells.append(ir.Sel(sequence, source_index))
        value = ir.Op("arraylit", *cells)
        result_contract = contract.with_interval(
            contract.minimum * minimum_count,
            contract.maximum * maximum_count,
            contract.element)
        if result_contract.exact_length is not None:
            return value
        result, length = self.storage.fresh(), self.storage.fresh()
        self.before(ir.Seq(
            ir.Assign(result, value),
            ir.Assign(length, ir.Op(
                "mul", source_length, effective_count))))
        result_shape = (size,) + child_shape
        self.flow.lengths[result] = result_shape
        self.flow.carriers[result] = contract.carrier
        self.flow.contracts[result] = result_contract
        self._register_dynamic_sequence(result, length, size)
        self.storage.require_shape(result, result_shape, node)
        return ir.Var(result)

    def _sequence_concat(self, node, left, right, left_shape, right_shape):
        """Concatenate finite sequences with one proved recursive layout."""
        source_carriers = (self._carrier(left, node.left),
                           self._carrier(right, node.right))
        if all(carrier is not None for carrier in source_carriers) \
                and source_carriers[0] != source_carriers[1] \
                and not all(carrier in BYTE_CARRIERS for carrier in source_carriers):
            raise LoweringError(
                node, "sequence concatenation with different Python carriers",
                code="sequence-concat-carrier-mismatch")
        if any(carrier not in {"list", "tuple", *BYTE_CARRIERS}
               for carrier in source_carriers):
            raise LoweringError(
                node, "sequence concatenation whose Python carriers are not "
                      "proved equal",
                code="sequence-concat-carrier-unproven")
        left_contract = self._contract_for_value(node.left, left)
        right_contract = self._contract_for_value(node.right, right)
        carriers = (left_contract.carrier, right_contract.carrier)
        if any(carrier not in {"list", "tuple", *BYTE_CARRIERS} for carrier in carriers):
            raise LoweringError(
                node, "sequence concatenation whose Python carriers are not "
                      "proved equal",
                code="sequence-concat-carrier-unproven")
        left_child, right_child = left_shape[1:], right_shape[1:]
        if left_child == right_child:
            child_shape = left_child
        elif left_shape[0] == 0 and left_contract.maximum == 0:
            child_shape = right_child
        elif right_shape[0] == 0 and right_contract.maximum == 0:
            child_shape = left_child
        else:
            raise LoweringError(
                node, "sequence concatenation with different recursive "
                      "element shapes",
                code="sequence-element-shape-disagrees")
        result_contract = self._contract_ast(node)
        if result_contract is INCOMPATIBLE or not result_contract.is_sequence:
            raise LoweringError(
                node, "sequence concatenation with incompatible recursive "
                      "element contracts",
                code="sequence-element-shape-disagrees")
        left_dynamic = self._dynamic_sequence(left)
        right_dynamic = self._dynamic_sequence(right)
        left_padded = (left_dynamic is None
                       and left_contract.exact_length != left_shape[0])
        right_padded = (right_dynamic is None
                        and right_contract.exact_length != right_shape[0])
        if (left_dynamic is None and right_dynamic is None
                and not left_padded and not right_padded):
            return ir.Op(
                "arraylit",
                *[ir.Sel(left, ir.Lit(index))
                  for index in range(left_shape[0])],
                *[ir.Sel(right, ir.Lit(index))
                  for index in range(right_shape[0])])

        left_length = (left_dynamic[1] if left_dynamic is not None
                       else ir.Lit(left_contract.exact_length))
        right_length = (right_dynamic[1] if right_dynamic is not None
                        else ir.Lit(right_contract.exact_length))
        capacity = result_contract.maximum
        if (capacity is None or capacity < 0
                or capacity > left_shape[0] + right_shape[0]):
            raise LoweringError(
                node, "sequence concatenation has no consistent finite "
                      "capacity",
                code="sequence-plan-inconsistent")
        cells = []
        for slot in range(capacity):
            cell = lower_returns.zero_shape(child_shape)
            for index in reversed(range(right_shape[0])):
                present = ir.Op(
                    "land",
                    ir.Op("lt", ir.Lit(index), right_length),
                    ir.Op("eq", ir.Lit(slot), ir.Op(
                        "add", left_length, ir.Lit(index))))
                cell = ir.Op(
                    "ite", present, ir.Sel(right, ir.Lit(index)), cell)
            if slot < left_shape[0]:
                cell = ir.Op(
                    "ite", ir.Op("lt", ir.Lit(slot), left_length),
                    ir.Sel(left, ir.Lit(slot)), cell)
            cells.append(cell)
        result, length = self.storage.fresh(), self.storage.fresh()
        self.before(ir.Seq(
            ir.Assign(result, ir.Op("arraylit", *cells)),
            ir.Assign(length, ir.Op("add", left_length, right_length))))
        result_shape = (capacity,) + child_shape
        self.flow.lengths[result] = result_shape
        self.flow.carriers[result] = carriers[0]
        self.flow.contracts[result] = result_contract
        self._register_dynamic_sequence(result, length, capacity)
        self.storage.require_shape(result, result_shape, node)
        return ir.Var(result)

    def _binop(self, node):
        kind = type(node.op)
        bitwise = kind in {ast.BitAnd, ast.BitOr, ast.BitXor}
        source_types = (
            self._fixed_source_scalar_type(node.left),
            self._fixed_source_scalar_type(node.right),
        )
        boolean_bitwise = bitwise and all(
            candidate == value_types.BOOLEAN for candidate in source_types)
        numeric_boundary = (not bitwise or
                            value_types.INTEGER in source_types)
        if self._operation_bindings(node) or (bitwise and not numeric_boundary):
            # Observe the actual Python operands, before numeric promotion.
            # This also obtains helper-return types from their lowered values;
            # names/syntax alone need not expose the return type.
            left, right = self._in_order(node.left, node.right)
            source_types = tuple(self._source_scalar_type_for_value(source, value)
                                 for source, value in ((node.left, left), (node.right, right)))
            boolean_bitwise = bitwise and all(kind == value_types.BOOLEAN for kind in source_types)
            if not bitwise or value_types.INTEGER in source_types:
                left, right = tuple(ir.Op("toint", value) if kind == value_types.BOOLEAN else value
                                    for value, kind in zip((left, right), source_types))
        else:
            left, right = self._in_order(
                node.left, node.right,
                lower=(self._integer_context_expr
                       if numeric_boundary else self.expr))
        if isinstance(left, OrderedScalar) or isinstance(right, OrderedScalar):
            raise LoweringError(node, 'infinity arithmetic is not an integer operation',
                                code='ordered-scalar-operation-unsupported')
        if isinstance(left, _StaticRef) or isinstance(right, _StaticRef):
            raise LoweringError(
                node, "a finite source reference has no numeric operator semantics",
                code="static-reference-operation-unsupported")
        if isinstance(left, _BatchValue) or isinstance(right, _BatchValue):
            raise LoweringError(
                node, "a transaction-batch value is used by an ordinary "
                      "binary operation",
                code="output-batch-read-unsupported")
        if isinstance(left, OptionValue) or isinstance(right, OptionValue):
            raise LoweringError(
                node, "a binary operation consumes an optional value without "
                      "a path proving it is present",
                code="optional-value-not-unwrapped")
        if isinstance(left, MappingBuilder) or isinstance(right, MappingBuilder):
            raise LoweringError(node, "a mapping has no numeric operator semantics",
                                code="mapping-operation-unsupported")
        if isinstance(left, RecordValue) or isinstance(right, RecordValue):
            raise LoweringError(
                node, "a binary operation on a fixed-schema record",
                code="record-operation-unsupported")
        left_shape, right_shape = self._shape(left), self._shape(right)
        if left_shape or right_shape:
            if kind is ast.Mult and bool(left_shape) != bool(right_shape):
                sequence, sequence_source, shape, count_source = (
                    (left, node.left, left_shape, node.right)
                    if left_shape else
                    (right, node.right, right_shape, node.left))
                count_value = right if left_shape else left
                result = self._sequence_repeat(
                    node, sequence, sequence_source, shape, count_source,
                    count_value)
            elif kind is ast.Add and left_shape and right_shape:
                result = self._sequence_concat(
                    node, left, right, left_shape, right_shape)
            else:
                raise LoweringError(
                    node, "an array operation other than proved sequence "
                          "concatenation or repetition",
                    code="array-operator-unsupported")
            return self._observe_operation(
                node, (left, right), result,
                ("sequence" if left_shape else source_types[0],
                 "sequence" if right_shape else source_types[1], "sequence"))
        if boolean_bitwise:
            if kind is ast.BitAnd:
                result = ir.Op("land", left, right)
            elif kind is ast.BitOr:
                result = ir.Op("lor", left, right)
            else:
                result = ir.Op("ne", left, right)
            return self._observe_operation(node, (left, right), result, (*source_types, value_types.BOOLEAN))
        spec = primitives.source_primitive(
            primitives.SourceFamily.BINARY, kind)
        if spec is not None and spec.source_error is None:
            return self._observe_operation(node, (left, right), ir.Op(spec.name, left, right),
                                           (*source_types, value_types.INTEGER))
        if spec is not None:
            op, code = spec.name, spec.source_error
            proved_safe = self._partial_operand_is_safe(
                right, node.right, spec.source_domain)
            invalid = (ir.Lit(False) if proved_safe else {
                primitives.SourceDomain.RIGHT_NONZERO:
                    ir.Op("eq", right, ir.Lit(0)),
                primitives.SourceDomain.RIGHT_NONNEGATIVE:
                    ir.Op("lt", right, ir.Lit(0)),
            }[spec.source_domain])
            self.reached.add(id(node))
            goal = self._safety_goal(node, code.lower())
            self._guard(invalid, goal, code, node)
            return self._observe_operation(node, (left, right), ir.Op(op, left, right),
                                           (*source_types, value_types.INTEGER))
        raise LoweringError(node, f"operator {kind.__name__}", code="operator-unsupported")

    def _value_equality(self, left, right, left_contract, right_contract, node):
        """Python equality for the admitted finite recursive value carriers."""
        if left_contract.is_none or right_contract.is_none:
            other = right if left_contract.is_none else left
            return (ir.Op("not", other.present)
                    if isinstance(other, OptionValue) else ir.Lit(False))
        if left_contract.is_option or right_contract.is_option:
            left_tag = left.present if isinstance(left, OptionValue) else ir.Lit(True)
            right_tag = right.present if isinstance(right, OptionValue) else ir.Lit(True)
            payload_equal = self._value_equality(
                left.payload if isinstance(left, OptionValue) else left,
                right.payload if isinstance(right, OptionValue) else right,
                left_contract.payload if left_contract.is_option else left_contract,
                right_contract.payload if right_contract.is_option else right_contract,
                node)
            return ir.Op("lor", ir.Op("land", ir.Op("not", left_tag),
                                      ir.Op("not", right_tag)),
                         ir.Op("land", left_tag, ir.Op("land", right_tag, payload_equal)))
        if left_contract.is_record or right_contract.is_record:
            if not (left_contract.is_record and right_contract.is_record):
                return ir.Lit(False)
            left_fields, right_fields = left.by_name, right.by_name
            right_contracts = right_contract.field_contracts
            if left_fields.keys() != right_fields.keys():
                return ir.Lit(False)
            return ir._balanced_op("land", [
                self._value_equality(left_fields[name], right_fields[name],
                                     contract, right_contracts[name], node)
                for name, contract in left_contract.fields
            ], ir.Lit(True))
        if left_contract.is_sequence != right_contract.is_sequence:
            return ir.Lit(False)
        if not left_contract.is_sequence:
            return lower_static_refs.numeric_equal(left, right)
        if left_contract.carrier is None or right_contract.carrier is None:
            raise LoweringError(
                node, "sequence equality whose Python carriers are not proved",
                code="sequence-comparison-carrier-unproven")
        if left_contract.carrier != right_contract.carrier and not (
                left_contract.carrier in BYTE_CARRIERS
                and right_contract.carrier in BYTE_CARRIERS):
            return ir.Lit(False)
        left_length = left_contract.exact_length
        right_length = right_contract.exact_length
        if left_length is None or right_length is None:
            raise LoweringError(
                node, "equality of sequences with run-time logical lengths",
                code="dynamic-sequence-comparison-unsupported")
        if left_length != right_length:
            return ir.Lit(False)
        if left_length == 0:
            return ir.Lit(True)
        left_element = left_contract.element
        right_element = right_contract.element
        if left_element is None or right_element is None:
            raise LoweringError(
                node, "sequence equality with an unknown element contract",
                code="sequence-comparison-element-unproven")
        terms = []
        for index in range(left_length):
            terms.append(self._value_equality(
                left.at(ir.Lit(index)) if isinstance(left, OptionSequence) else ir.Sel(left, ir.Lit(index)),
                right.at(ir.Lit(index)) if isinstance(right, OptionSequence) else ir.Sel(right, ir.Lit(index)),
                left_element, right_element, node))
        return ir._balanced_op("land", terms, ir.Lit(True))

    def _comparison(self, op, left, right, left_source, right_source, node):
        if isinstance(left, OptionSequence) or isinstance(right, OptionSequence):
            if op not in {'eq', 'ne'}:
                raise LoweringError(node, 'ordered optional-sequence comparison is not represented',
                                    code='sequence-ordering-unsupported')
            equal = self._value_equality(left, right, self._contract_for_value(left_source, left),
                                         self._contract_for_value(right_source, right), node)
            return equal if op == 'eq' else ir.Op('not', equal)
        if isinstance(left, OrderedScalar) or isinstance(right, OrderedScalar):
            return OrderedScalar.compare(self, op, left, right, left_source, right_source)
        if isinstance(left, MappingBuilder) or isinstance(right, MappingBuilder):
            raise LoweringError(node, "whole-map comparison is not represented",
                                code="mapping-operation-unsupported")
        if isinstance(left, _StaticRef) or isinstance(right, _StaticRef):
            if op not in {"eq", "ne"}:
                raise LoweringError(
                    node, "ordered comparison of finite source references",
                    code="static-discriminator-comparison-unsupported")
            same = lower_static_refs.equal_values(self, left, right, left_source, right_source, node)
            return same if op == "eq" else ir.Op("not", same)
        if isinstance(left, _BatchValue) or isinstance(right, _BatchValue):
            raise LoweringError(
                node, "comparison observes a transaction-batch carrier",
                code="output-batch-read-unsupported")
        if isinstance(left, OptionValue) or isinstance(right, OptionValue):
            if op in {"eq", "ne"}:
                same = self._value_equality(
                    left, right, self._contract_for_value(left_source, left),
                    self._contract_for_value(right_source, right), node)
                return same if op == "eq" else ir.Op("not", same)
            raise LoweringError(
                node, "a comparison consumes an optional value without a "
                      "path proving it is present",
                code="optional-value-not-unwrapped")
        if isinstance(left, RecordValue) or isinstance(right, RecordValue):
            if op in {"eq", "ne"}:
                same = self._value_equality(
                    left, right, self._contract_for_value(left_source, left),
                    self._contract_for_value(right_source, right), node)
                return same if op == "eq" else ir.Op("not", same)
            raise LoweringError(
                node, "comparison observes a fixed-schema record as a "
                      "mapping object",
                code="record-operation-unsupported")
        left_shape, right_shape = self._shape(left), self._shape(right)
        if not left_shape and not right_shape:
            if op in {"eq", "ne"}:
                source_types = {
                    self._fixed_source_scalar_type(left_source),
                    self._fixed_source_scalar_type(right_source),
                }
                numeric_mix = source_types == {
                    value_types.BOOLEAN, value_types.INTEGER}
                same = (lower_static_refs.numeric_equal(left, right)
                        if numeric_mix else ir.Op("eq", left, right))
                return same if op == "eq" else ir.Op("not", same)
            return ir.Op(op, left, right)
        if op not in {"eq", "ne"}:
            raise LoweringError(
                node, "an ordered comparison involving a sequence",
                code="sequence-ordering-unsupported")
        left_contract = self._contract_for_value(left_source, left)
        right_contract = self._contract_for_value(right_source, right)
        same = self._value_equality(
            left, right, left_contract, right_contract, node)
        return same if op == "eq" else ir.Op("not", same)

    def _compare(self, node):
        identity = none_test(node)
        if identity is not None:
            if lower_static_refs.is_reference(self, identity.operand):
                is_none = lower_static_refs.is_none(self, identity.operand)
                if is_none is None:
                    raise LoweringError(
                        identity.operand,
                        "a finite static reference has no presence tag",
                        code="static-none-test-unsupported")
            else:
                value = self.expr(identity.operand)
                if isinstance(value, _BatchValue):
                    is_none = ir.Op(
                        "eq", value.form,
                        ir.Lit(_batch_form_code(BatchForm.NONE)))
                elif isinstance(value, OptionValue):
                    is_none = (ir.Lit(True) if value.contract.is_none else
                               ir.Op("not", value.present))
                else:
                    # A represented non-optional value is necessarily present.
                    is_none = ir.Lit(False)
            return (is_none if identity.none_when_true else
                    ir.Op("not", is_none))
        if (len(node.ops) == 1
                and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
                and any(lower_static_refs.requires_reference(self, part)
                        for part in (node.left, node.comparators[0]))):
            equal = lower_static_refs.equal_sources(self, node.left, node.comparators[0], node)
            if equal is None:
                raise LoweringError(
                    node, "a finite static discriminator has no exact "
                          "equality representation",
                    code="static-discriminator-comparison-unsupported")
            return (equal if isinstance(node.ops[0], ast.Eq)
                    else ir.Op("not", equal))
        if any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops) \
                and any(lower_static_refs.is_reference(self, part)
                        for part in [node.left, *node.comparators]):
            raise LoweringError(
                node, "identity observation of a compile-time collection "
                      "reference",
                code="static-collection-identity-observed")
        operators = []
        for source_op in node.ops:
            spec = primitives.source_primitive(
                primitives.SourceFamily.COMPARISON, type(source_op))
            op = None if spec is None else spec.name
            if isinstance(source_op, ast.In):
                op = "in"
            elif isinstance(source_op, ast.NotIn):
                op = "not-in"
            elif op is None:
                raise LoweringError(
                    node, f"comparison {type(source_op).__name__}",
                    code="comparison-unsupported")
            operators.append(op)

        # A chained comparison stops at its first false pair.  When a pair is
        # declaration-proved false, later comparators are not source
        # occurrences in this run and must not demand helper summaries or
        # contribute effects.  Keep the false pair itself: its right operand
        # is evaluated before the chain stops.
        effective = len(operators)
        previous_source = node.left
        for index, (source_op, comparator) in enumerate(
                zip(node.ops, node.comparators)):
            pair = ast.Compare(
                left=previous_source, ops=[source_op],
                comparators=[comparator])
            if self._source_static_truth(pair) is False:
                effective = index + 1
                break
            previous_source = comparator
        comparators = list(node.comparators[:effective])
        operators = operators[:effective]
        omitted = list(node.comparators[effective:])

        if len(operators) == 1:
            if operators[0] in {"in", "not-in"}:
                if lower_static_refs.is_reference(self, comparators[0]) and isinstance(node.left, ast.Tuple):
                    _fixed, left = lower_static_refs.selector(self, node.left)
                else:
                    left = self._integer_context_expr(node.left)
                if self._effectful(comparators[0]):
                    left = self._capture(left)
                right = self._membership_rhs(comparators[0])
                result = self._membership(
                    left, right, comparators[0],
                    negate=operators[0] == "not-in", needle_source=node.left)
            else:
                left, right = self._in_order(
                    node.left, comparators[0],
                    lower=self._integer_context_expr)
                result = self._comparison(
                    operators[0], left, right, node.left, comparators[0],
                    node)
            if omitted:
                self.before(self._false_goal_terms(omitted))
            return result

        # Python evaluates the middle operand once, reuses it as the next
        # comparison's left operand, and stops at the first false comparison.
        # A conjunction of independently lowered comparisons gets both facts
        # wrong when an operand has effects.
        left = self._integer_context_expr(node.left)
        if any(self._effectful(part) for part in comparators):
            left = self._capture(left)
        result = self.storage.fresh()

        def chain(index, previous, previous_source):
            before, raw = self._scoped(
                lambda i=index: self._membership_rhs(comparators[i])
                if operators[i] in {"in", "not-in"}
                else self._integer_context_expr(comparators[i]))
            shape = self._shape(raw)
            contract = (UNKNOWN if isinstance(raw, _StaticRef) else
                        self._contract_for_value(comparators[index], raw))
            # A scalar name/literal remains the same value when subsequent
            # comparators cannot write. Keep that operand visible to the CFG's
            # guard analysis (e.g. 0 <= shift <= limit). Effectful continuations
            # still capture the middle value exactly once before they run.
            if isinstance(raw, _StaticRef):
                bind, current = raw.bind(self, self.storage.fresh(), comparators[index])
            elif (contract.kind == "scalar" and isinstance(raw, (ir.Var, ir.Lit))
                    and not any(self._effectful(part)
                                for part in comparators[index + 1:])):
                current, bind = raw, ir.Skip()
            else:
                held = self.storage.fresh()
                current, bind = ir.Var(held), ir.Assign(held, raw)
                self.storage.require_shape(
                    held, shape, comparators[index])
                self.flow.contracts[held] = contract
                if shape:
                    self.flow.lengths[held] = shape
                    if contract.is_sequence and contract.carrier is not None:
                        self.flow.carriers[held] = contract.carrier
            if operators[index] in {"in", "not-in"}:
                condition = self._membership(
                    previous, current, comparators[index],
                    negate=operators[index] == "not-in", needle_source=previous_source)
            else:
                condition = self._comparison(
                    operators[index], previous, current,
                    previous_source, comparators[index], node)
            if index + 1 == len(operators):
                tail = ir.Assign(result, condition)
            else:
                next_before, next_cmd = self._scoped(
                    lambda i=index + 1: chain(
                        i, current, comparators[index]))
                tail = ir.If(
                    condition, ir.Seq(next_before, next_cmd),
                    ir.Assign(result, ir.Lit(False)))
            return ir.Seq(before, ir.Seq(bind, tail))

        command = chain(0, left, node.left)
        if omitted:
            command = ir.Seq(self._false_goal_terms(omitted), command)
        self.before(command)
        return ir.Var(result)

    def _membership_rhs(self, node):
        """Lower the finite collection on the right of ``in``.

        A closed module mapping is represented by its keys because Python
        membership over a mapping asks about keys, not values.  Every other
        admitted collection is a fixed array or a run-time-length sequence
        with a finite capacity.
        """
        if isinstance(node, ast.Name):
            resolver = getattr(self, "resolver", None)
            if resolver is not None and node.id in resolver.invalid_tables:
                raise LoweringError(
                    node, f"membership in the module table {node.id}, which "
                          f"is not a closed read-only binding",
                    code="module-table-not-static")
        if lower_static_refs.is_reference(self, node):
            return lower_static_refs.lower_reference(self, node, preserve_reference=True)
        return self.expr(node)

    def _membership(self, needle, haystack, source, *, negate=False, needle_source=None):
        """Exact membership in a finite scalar sequence or mapping key set."""
        if isinstance(haystack, MappingBuilder):
            found = haystack.contains(self, needle, needle_source)
            return ir.Op("not", found) if negate else found
        if isinstance(haystack, _StaticRef):
            from .source_rom import is_source_key

            mappings = all(type(value) is dict for _active, value in haystack.alternatives)
            if mappings and not isinstance(needle, (_StaticRef, _TupleKey)) \
                    and (isinstance(needle, _PositionalValue) or self._shape(needle)):
                contract = self._contract_for_value(needle_source, needle)
                needle = lower_static_refs.tuple_key(self, needle, contract, source)
            if (mappings and isinstance(needle, _StaticRef)
                    and any(type(value) is tuple for _active, value in needle.alternatives)
                    and (needle_source is None or not contract_is_deeply_immutable(
                        self._contract_ast(needle_source)))):
                raise LoweringError(source, "a mapping key has no immutable source carrier",
                                    code="static-collection-key-unsupported")
            terms = []
            for active, value in haystack.alternatives:
                if type(value) not in (dict, tuple) or any(
                        (not is_source_key(key) if mappings else
                         type(key) not in (bool, int, str)) for key in value):
                    raise LoweringError(
                        source, "membership needs finite scalar keys or elements",
                        code="membership-source-shape-unsupported")
                found = (lower_static_refs.integer_membership(needle, value)
                         if not isinstance(needle, (_StaticRef, _TupleKey))
                         and all(type(key) in (bool, int) for key in value)
                         else lower_static_refs.disjunction([
                             lower_static_refs.key_equal(needle, key, source) for key in value]))
                terms.append(ir.Op("land", active, found))
            self._spend(max(1, sum(len(value) for _active, value in haystack.alternatives)))
            result = lower_static_refs.disjunction(terms)
            return ir.Op("not", result) if negate else result
        if isinstance(needle, _StaticRef):
            # A represented runtime sequence has numeric elements. Retain
            # Boolean/integer equality; finite string alternatives cannot match.
            terms = [ir.Op("land", active, self._membership(ir.Lit(value), haystack, source))
                     for active, value in needle.alternatives if type(value) in (bool, int)]
            if any(type(value) not in (bool, int, str) for _active, value in needle.alternatives):
                raise LoweringError(source, "membership with a non-scalar finite value",
                                    code="membership-source-shape-unsupported")
            result = lower_static_refs.disjunction(terms)
            return ir.Op("not", result) if negate else result
        if (isinstance(haystack, ir.Op) and haystack.op == "arraylit"
                and all(isinstance(item, ir.Lit) and type(item.value) in (bool, int)
                        for item in haystack.args)):
            # The RHS has already been evaluated in source order. Closed
            # literal elements have the same exact domain as a static table;
            # reuse its interval union instead of hiding it behind selects.
            result = lower_static_refs.integer_membership(needle, (item.value for item in haystack.args))
            return ir.Op("not", result) if negate else result
        dynamic = self._dynamic_sequence(haystack)
        if dynamic is not None:
            _name, length, extent = dynamic
            terms = [ir.Op(
                "land", ir.Op("lt", ir.Lit(index), length),
                lower_static_refs.numeric_equal(needle, ir.Sel(haystack, ir.Lit(index))))
                     for index in range(extent)]
        else:
            shape = self._shape(haystack)
            if len(shape) != 1:
                raise LoweringError(
                    source, "membership in a value with no fixed "
                            "one-dimensional shape",
                    code="membership-source-shape-unsupported")
            terms = [lower_static_refs.numeric_equal(needle, ir.Sel(haystack, ir.Lit(index)))
                     for index in range(shape[0])]
        result = ir._balanced_op("lor", terms, ir.Lit(False))
        return ir.Op("not", result) if negate else result

    def _extent(self, array):
        """How many cells a lowered array expression has, or nothing."""
        shape = self._shape(array)
        return shape[0] if shape else None

    def _shape(self, array):
        """Dimensions of a lowered expression, outermost first.

        Every way an array can be written down is covered here, because this
        is what decides whether a subscript can be given a meaning at all: a
        literal has its elements, a write leaves what it replaced into, a
        choice has what either arm has when they agree, reading an element
        drops the outermost dimension, and a name has what was recorded when
        it was bound or declared.
        """
        if isinstance(array, ir.Op):
            if array.op == "arrayfill" and isinstance(array.args[1], ir.Lit):
                return (array.args[1].value,) + self._shape(array.args[0])
            if array.op == "arraylit":
                inner = [self._shape(a) for a in array.args]
                same = inner[0] if inner and all(s == inner[0] for s in inner) \
                    else ()
                return (len(array.args),) + same
            if array.op == "store":
                return self._shape(array.args[0])
            if array.op == "ite":
                left = self._shape(array.args[1])
                return left if left == self._shape(array.args[2]) else ()
        if isinstance(array, ir.Sel):
            inner = self._shape(array.array)
            return inner[1:] if inner else ()
        if isinstance(array, ir.Var):
            if array.name in self.flow.lengths:
                return self.flow.lengths[array.name]
            if array.name in self.storage.shapes:
                return self.storage.shapes[array.name]
            if array.name in self.storage.capacities:
                return (self.storage.capacities[array.name],)
        return ()

    def _index(self, node, array, index):
        """What a subscript means, said once and in core operations.

        An index in ``[-n, n)`` reads a cell, counting from the end when it is
        negative, and any other index is an error. Emitting an opaque range
        check instead would leave the meaning of a subscript to be decided
        again by whatever consumes the representation, and two decisions that
        have to agree are two decisions that can differ.
        """
        n = self._extent(array)
        if n is None:
            raise LoweringError(node, "subscript of an array whose length is "
                                      "not static", code="subscript-length-not-static")
        goal = self._safety_goal(node, "indexerror")
        if isinstance(index, ir.Lit):
            # A literal index either reads a cell or is an error of the source
            # rather than of a run, except when a comprehension iteration was
            # substituted by the compact term lowerer.  That occurrence is
            # still data-dependent at the source boundary: the generated copy
            # is guarded by the captured trip count, and an entered copy must
            # fail exactly as Python would.
            if not -n <= index.value < n:
                substituted = any(
                    isinstance(part, ast.Name)
                    and isinstance(part.ctx, ast.Load)
                    and part.id in self.activation.substitutions
                    for part in ast.walk(node.slice)
                )
                if substituted and n > 0:
                    self._guard(ir.Lit(True), goal, "IndexError", node)
                    return ir.Lit(0)
                raise LoweringError(node, f"index {index.value} outside an "
                                          f"array of {n}", code="index-out-of-range")
            # It is literal here and not always in the source: a name the class body
            # binds folds to one, and a loop or comprehension index is replaced by one
            # as it unrolls. 2072 of the 2117 index sites in the population are this,
            # so it is the ordinary case and not an exception.
            self._discharge(goal, f"index folded to {index.value}, an in-range cell "
                                  f"of an array of {n}")
            return ir.Lit(index.value if index.value >= 0
                          else index.value + n)
        self._guard(ir.Op("lor",
                          ir.Op("lt", index, ir.Lit(-n)),
                          ir.Op("ge", index, ir.Lit(n))),
                    goal, "IndexError", node)
        return ir.Op("ite", ir.Op("lt", index, ir.Lit(0)),
                     ir.Op("add", index, ir.Lit(n)), index)

    def _sequence_index(self, node, name, index, *, length=None):
        """Index a grown list against its run-time length, not its capacity."""
        length = (ir.Var(self.storage.lengths[name]) if length is None else length)
        goal = self._safety_goal(node, "indexerror")
        outside = ir.Op(
            "lor", ir.Op("lt", index, ir.Op("neg", length)),
            ir.Op("ge", index, length))
        # The companion expression is the run-time length authority.  A source
        # contract can describe an earlier binding of a nested list (for
        # example ``rows = [[1, 2]]; rows[0] = xs``), so using that contract to
        # remove this guard can turn an empty ``xs[0]`` into a padded-cell read.
        # Elide the guard only when the represented length itself has a proved
        # positive lower bound containing the complete index interval.
        length_bounds = ranges.bounds_of_expression(length, self.flow.bounds)
        index_bounds = ranges.bounds_of_expression(index, self.flow.bounds)
        proved_inside = (
            length_bounds is not None
            and length_bounds.lo is not None
            and index_bounds is not None
            and index_bounds.lo is not None
            and index_bounds.hi is not None
            and index_bounds.lo >= -length_bounds.lo
            and index_bounds.hi < length_bounds.lo)
        self._guard(
            ir.Lit(False) if proved_inside else outside,
            goal, "IndexError", node)
        return ir.Op("ite", ir.Op("lt", index, ir.Lit(0)),
                     ir.Op("add", index, length), index)

    def _select_record_field(self, node, record, *, discharge=True):
        """Apply Python's fixed-string mapping selection to one product."""
        if (not isinstance(node.slice, ast.Constant)
                or type(node.slice.value) is not str):
            raise LoweringError(
                node.slice, "a record selection needs a fixed string field "
                            "name",
                code="record-key-not-static")
        field = node.slice.value
        values = record.by_name
        goal = self._safety_goal(node, "indexerror")
        if field not in values:
            self._guard(ir.Lit(True), goal, "KeyError", node)
            return ir.Lit(0)
        if discharge:
            self._discharge(goal, f"{field!r} is a field of the fixed record")
        value = values[field]
        if isinstance(value, OptionValue):
            return _OptionBuilder.refine(value, self._contract_ast(node), node)
        return value

    def _subscript(self, node):
        if isinstance(node.slice, ast.Slice):
            return self._slice(node)
        self.reached.add(id(node))
        mapping = lower_mappings.receiver(self, node.value)
        if mapping is not None:
            return mapping.subscript(self, node)
        resolver = getattr(self, "resolver", None)
        if (isinstance(node.value, ast.Name) and resolver is not None
                and node.value.id in resolver.invalid_tables):
            raise LoweringError(
                node.value,
                f"the module table {node.value.id} is not a closed "
                f"read-only binding", code="module-table-not-static")
        table_path = lower_static_refs.table_path(self, node)
        if table_path is not None:
            table, parts = table_path
            return lower_static_refs.read_path(self, table, parts)
        if lower_static_refs.is_reference(self, node.value):
            selected = lower_static_refs.lower_reference(self, node)
            if selected is None or isinstance(selected, _StaticRef):
                raise LoweringError(
                    node, "a static collection read does not end in an "
                          "encodable value",
                    code="table-value-shape-unsupported")
            return selected
        structured_root = self._structured_root(node.value)
        structured_input = self._structured_input_root(node.value)
        if (structured_root in self.storage.structured_bindings
                or structured_input in self.storage.structured_input_bindings):
            structured = self.expr(node.value)
            if not isinstance(structured, _StructuredValue):
                raise LoweringError(
                    node.value, "a declared structured state value lost its "
                                "leaf binding",
                    code="value-binding-lost")
            return self._structured_select(node, structured)
        array = self.expr(node.value)
        if isinstance(array, OptionSequence):
            if self._effectful(node.slice):
                array = self._capture(array)
            index = self._index(node, array.present, self._integer_context_expr(node.slice))
            return array.at(index)
        if isinstance(array, _PositionalValue):
            index = self._static_int(node.slice)
            if index is None:
                raise LoweringError(node, "a positional product needs a proved fixed index",
                                    code="positional-index-unsupported")
            if not -len(array.items) <= index < len(array.items):
                self._guard(ir.Lit(True), self._safety_goal(node, "indexerror"),
                            "IndexError", node)
                return ir.Lit(0)
            self._discharge(self._safety_goal(node, "indexerror"),
                            "fixed index within a positional product")
            return array.items[index]
        if isinstance(array, _BatchValue):
            if self._effectful(node.slice):
                array = self._capture(array)
            occurrence = self._contract_ast(node.value)
            if occurrence is UNKNOWN:
                occurrence = (
                    self.flow.contracts.get(
                        self._name(node.value.id), UNKNOWN)
                    if isinstance(node.value, ast.Name) else array.contract)
            sequence = record_batch_payload(array.contract)
            record_contract = None if sequence is None else sequence.element
            mapping_contract = (
                occurrence.payload
                if occurrence.is_option and occurrence.payload is not None
                and occurrence.payload.is_record
                else occurrence if occurrence.is_record else None)
            if (mapping_contract is not None
                    and isinstance(node.slice, ast.Constant)
                    and type(node.slice.value) is str):
                guarded = not occurrence.is_record
                if guarded:
                    self._guard(
                        ir.Op("ne", array.form, ir.Lit(
                            _batch_form_code(BatchForm.MAPPING))),
                        self._safety_goal(node, "indexerror"),
                        "TypeError", node)
                record = array.record_at(self, ir.Lit(0), node.value)
                return self._select_record_field(
                    node, record, discharge=not guarded)
            sequence_view = (
                occurrence.payload
                if occurrence.is_option and occurrence.payload is not None
                and occurrence.payload.is_sequence
                else occurrence)
            if sequence_view.is_sequence and record_contract is not None:
                if occurrence.is_option:
                    self._guard(
                        ir.Op("ne", array.form, ir.Lit(
                            _batch_form_code(BatchForm.LIST))),
                        self._safety_goal(node, "indexerror"),
                        "TypeError", node)
                raw = self._integer_context_expr(node.slice)
                index = self._sequence_index(
                    node, None, raw,
                    length=ir.Var(array.builder.count))
                return array.record_at(self, index, node)
            raise LoweringError(
                node.value, "a transaction batch is read without a path "
                            "proving its mapping or list carrier",
                code="output-batch-read-unsupported")
        if isinstance(array, RecordValue):
            return self._select_record_field(node, array)
        if isinstance(array, OptionValue):
            if (isinstance(array.payload, RecordValue)
                    and isinstance(node.slice, ast.Constant)
                    and type(node.slice.value) is str):
                self._guard(
                    ir.Op("not", array.present),
                    self._safety_goal(node, "indexerror"),
                    "TypeError", node)
                return self._select_record_field(
                    node, array.payload, discharge=False)
            raise LoweringError(
                node.value, "an optional record is selected without a path "
                            "proving it is present",
                code="optional-value-not-unwrapped")
        if self._effectful(node.slice):
            array = self._capture(array)
        array_contract = self._storage_contract(
            array, self._contract_ast(node.value))
        raw = self._integer_context_expr(node.slice)
        dynamic = self._dynamic_sequence(array)
        index = (self._sequence_index(
                    node, dynamic[0], raw, length=dynamic[1])
                 if dynamic is not None else self._index(node, array, raw))
        selected = ir.Sel(array, index)
        nested = {}
        for path, length_value in self._length_image(array).items():
            if path and path[0] is lower_state.SEQUENCE_ELEMENT:
                nested[path[1:]] = ir.Sel(length_value, index)
        storage = (array_contract.element
                   if array_contract.is_sequence else None)
        return self._bind_expression_lengths(
            selected, nested, storage=storage)

    # ── commands ──

    def _live(self):
        """The condition that control has not already left this point.

        A terminator does not end the command in a predicated representation, it
        records that it fired, so every statement that could run after one is
        guarded by nothing having fired. Which flags count is the whole content
        of the rule: a return means the procedure is done, and a break means
        this iteration is.

        Only the innermost loop's flag, since that is the loop a break in this
        block leaves. An outer loop's flag says nothing about whether the
        statement beside this one runs.
        """
        parts = []
        if self.activation.destination is not None:
            parts.append(ir.Op("eq", ir.Var(self.activation.destination.done), ir.Lit(0)))
        if self.loop is not None and self.loop.running is not None:
            parts.append(ir.Var(self.loop.running))
        if self.loop is not None and self.loop.continuing is not None:
            parts.append(ir.Var(self.loop.continuing))
        if not parts:
            return None
        cond = parts[0]
        for more in parts[1:]:
            cond = ir.Op("land", cond, more)
        return cond

    def body(self, nodes):
        """Lower a block, guarding what follows a possible terminator.

        Predication is what keeps the representation's sequencing straight-line
        while the source may leave early, and it makes a trailing return the
        degenerate case of the same rule rather than a special one. What the
        rule has to cover is every way control leaves, not one of them: a break
        used to stop the iterations that followed and nothing stopped the
        statement written beside it.

        Whether a statement can leave is now the lowering's own answer rather
        than a second walk over the source. Two mechanisms had to agree about
        it: a syntactic predicate said which statements could terminate, and
        `_live` said which flags to test, and nothing tied them together. The
        statement says how it can finish, and this reads that.

        Returns the command and how the block can finish, since its caller has
        the same question about it that it had about each statement.
        """
        out = None
        acc = {NORMAL}
        for index, node in enumerate(nodes):
            if NORMAL not in acc:
                # No source execution reaches the remainder of this block.  Its
                # goal sites still need explicit false terms, but lowering its
                # operations would invent representation obligations for code
                # that cannot run.
                dead = self._false_goal_terms(nodes[index:])
                out = dead if out is None else ir.Seq(out, dead)
                return out, acc
            if acc != {NORMAL}:
                # Something before this can have left. What follows runs only
                # if nothing did, and it is one conditional over the rest of
                # the block rather than one per statement.
                #
                # That is not tidiness. Guarding each statement separately puts
                # a write and a read of the same local under two different
                # conditionals that nothing relates, so `if a: return` followed
                # by `t = 5; return t` has `t` written under one guard and read
                # under another, and no analysis of the result can see that the
                # two are the same condition. One conditional over the rest
                # puts them in one branch, where the definition dominates the
                # use as a matter of shape. It is also what if-conversion of a
                # control-flow graph produces: after a join, one block.
                rest, rest_ways = self.body(nodes[index:])
                guarded = ir.If(self._live(), rest, ir.Skip())
                out = guarded if out is None else ir.Seq(out, guarded)
                return out, (acc - {NORMAL}) | rest_ways
            cmd, ways = self.stmt(node)
            location = self._location_mark(node)
            if not isinstance(location, ir.Skip):
                cmd = ir.Seq(location, cmd)
            out = cmd if out is None else ir.Seq(out, cmd)
            acc = (acc - {NORMAL}) | ways
        return (out if out is not None else ir.Skip()), acc

    def stmt(self, node):
        """Lower one statement, and say how it can finish.

        The second half is the point. What a block does with a statement
        depends on whether control can leave at it, and that used to be
        answered by walking the source a second time, in a predicate that had
        to agree with the flags `_live` tests without anything holding the two
        together.
        """
        if isinstance(node, ast.AnnAssign) and node.value is None:
            for expression in annotation_target_expressions(node.target):
                if (isinstance(expression, ast.Name) and expression.id == "self"
                        and not self._name_is_local("self")):
                    # The admitted method receiver needs no SolverIR value.
                    continue
                self.expr(expression)
            return self._flush(ir.Skip()), {NORMAL}
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            return self._assign(node), {NORMAL}
        if isinstance(node, ast.Delete):
            for item in node.targets:
                mapping = (lower_mappings.receiver(self, item.value)
                           if isinstance(item, ast.Subscript) else None)
                if mapping is not None:
                    self.reached.add(id(item))
                    mapping.subscript(self, item, remove=True)
                    continue
                target = sequence_delete_target(item)
                if target is None:
                    raise LoweringError(
                        item, "deletion needs an indexed finite list binding",
                        code="delete-target-unsupported")
                self.reached.add(id(item))
                self._remove_sequence_item(
                    item, target, item.slice, return_removed=False)
            return self._flush(ir.Skip()), {NORMAL}
        if isinstance(node, ast.AugAssign):
            # The declaration owes `x //= y` the same goal as `x = x // y`, and names
            # it after this statement, since that is the position the synthesised
            # `BinOp` below copies. Recorded here because the node `_binop` sees is
            # built fresh, so its identity is not the one the declaration holds, and
            # without this a division guard deleted from `_binop` would look to the
            # audit like an operation nothing reached.
            if isinstance(node.op, (ast.FloorDiv, ast.Mod)):
                self.reached.add(id(node))
            if isinstance(node.target, ast.Subscript):
                target_parts = [node.target.value]
                if isinstance(node.target.slice, ast.Slice):
                    target_parts.extend(part for part in (
                        node.target.slice.lower, node.target.slice.upper,
                        node.target.slice.step) if part is not None)
                else:
                    target_parts.append(node.target.slice)
                if self._effectful(node.value) or any(
                        self._effectful(part) for part in target_parts):
                    raise LoweringError(
                        node, "augmented subscript assignment whose saved "
                              "location crosses a source-visible effect",
                        code="augmented-subscript-effectful")
            # Preserve the source namespace on the read half.  Desugaring
            # ``self.x += y`` through ``Name("x")`` loses that it is an
            # instance field; a same-named module constant can then be folded
            # in its place.  Augmented assignment reads the *same target* it
            # writes, only with Load context.
            if isinstance(node.target, ast.Name):
                target = ast.Name(id=node.target.id, ctx=ast.Load())
            elif isinstance(node.target, ast.Attribute):
                target = ast.Attribute(value=node.target.value,
                                       attr=node.target.attr, ctx=ast.Load())
            elif isinstance(node.target, ast.Subscript):
                target = ast.Subscript(value=node.target.value,
                                       slice=node.target.slice, ctx=ast.Load())
            else:
                raise LoweringError(
                    node.target, "augmented assignment target",
                    code="assignment-target-unsupported")
            # The synthetic read is the source target occurrence.  Keep that
            # span so an index failure is attributed to the declared
            # subscript goal; only the synthetic arithmetic operation belongs
            # to the full augmented-assignment span.
            ast.copy_location(target, node.target)
            binop = ast.BinOp(left=target, op=node.op, right=node.value)
            ast.copy_location(binop, node)
            return self._assign(ast.Assign(targets=[node.target], value=binop,
                                           lineno=node.lineno,
                                           col_offset=node.col_offset)), \
                {NORMAL}
        if isinstance(node, ast.If):
            return self._if(node)
        if isinstance(node, ast.Assert):
            return self._assert(node)
        if isinstance(node, ast.For):
            return self._for(node)
        if isinstance(node, ast.While):
            return self._while(node)
        if isinstance(node, ast.Break):
            if self.loop is None or self.loop.running is None:
                raise LoweringError(node, "break outside a loop", code="break-outside-loop")
            self.loop.breaks.append(self._flow_facts())
            return ir.Assign(self.loop.running, ir.Lit(False)), {LEFT}
        if isinstance(node, ast.Continue):
            if self.loop is None or self.loop.continuing is None:
                raise LoweringError(
                    node, "continue outside a loop", code="continue-outside-loop")
            self.loop.continues.append(self._flow_facts())
            return ir.Assign(
                self.loop.continuing, ir.Lit(False)), {CONTINUED}
        if isinstance(node, ast.Return):
            return self._return(node), {RETURNED}
        if isinstance(node, ast.Pass):
            return ir.Skip(), {NORMAL}
        if isinstance(node, ast.FunctionDef):
            # The binding itself is handled at resolution; what the def
            # statement *computes* -- decorators, defaults, annotations --
            # has nowhere to go, so only a def that computes nothing may be
            # skipped (review 48: a walrus default bound a local).
            if not _def_time_pure(node):
                raise LoweringError(
                    node, f"defining {node.name} computes its decorators, "
                          f"defaults or annotations, which the lowering "
                          f"cannot run", code="nested-def-effects")
            # The def statement binds the name here, at this program point,
            # so a call to it before this line reads an unbound cell
            # (review 50). Record it as available from now on.
            if self.activation.scope is not None:
                self.activation.defined.add(node.name)
            return ir.Skip(), {NORMAL}
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant):
                return ir.Skip(), {NORMAL}        # a docstring
            # An expression used as a statement is evaluated for its effect on
            # the state. Lowering it and discarding the value is the same rule
            # as anywhere else: the effects are the commands it hoisted.
            self.expr(node.value)
            return self._flush(ir.Skip()), {NORMAL}
        raise LoweringError(node, f"statement {type(node).__name__}", code="statement-unsupported")

    def _return(self, node):
        """A return assigns the result and sets the flag the block guards on."""
        if self.activation.destination is None:
            raise LoweringError(node, "return outside a procedure", code="return-outside-procedure")
        cmd = self.activation.destination.assign(self, node)
        if not isinstance(self.activation.destination, _NamedReturn):
            self.activation.return_facts.append(self._flow_facts())
        return self._flush(ir.Seq(cmd, ir.Assign(self.activation.destination.done, ir.Lit(1))))

    def _for(self, node):
        """A loop unrolls a known number of times, which is what keeps a step a
        finite term. A literal bound gives that number directly; a bound that
        depends on a value gives it through the range analysis, and the loop
        then runs to the largest count the bound can take with each iteration
        guarded by whether it is really due."""
        loop_input = self._flow_facts()

        # What the variable takes and how many times the loop runs come from
        # one rule, whether or not the count is written as a literal. Reading
        # the count off the source and leaving the variable to the fast path
        # would bind it to the iteration number, which is the value only when
        # the loop counts from zero by one.
        mark = self.mark()
        iteration_plan = self._dynamic_bound(node)
        count, bound = iteration_plan.count, iteration_plan.bound
        if isinstance(count, ir.Lit):
            trip_count = ir.Lit(max(count.value, 0))
            before = self._flush(ir.Skip()) if self.hoisted_since(mark) else None
            if bound == 0:
                outcome = self._loop_goals(node.iter, iteration_plan.activation(trip_count))
                command = self._false_goal_terms(node.body)
                if outcome is not None:
                    command = ir.Seq(outcome, command)
                if before is not None:
                    command = ir.Seq(before, command)
                if node.orelse:
                    otherwise, ways = self.body(node.orelse)
                    command = ir.Seq(command, otherwise)
                    return command, ways
                return command, {NORMAL}
            body_indices, bind_target = self._prepare_loop_target(
                node.target, iteration_plan, node.iter)
            running, continuing = self._loop_controls(node)
            with self._loop_scope(running, continuing, indices=body_indices) as loop:
                breaks, continues = loop.breaks, loop.continues
                body_entry = self._loop_source_body_entry(
                    node, self._flow_facts())
                self._set_flow_facts(body_entry)
                body, ways = self._with_iterator_binding(
                    iteration_plan.binding, lambda: self.body(node.body))
                body_facts = self._flow_facts()
            # The outcome is a test of the count and sits before the loop,
            # whether or not the count is a number. It used to be marked inside
            # the body when the count was positive, so that the mark happened
            # only if the body ran, and the other outcome was recorded as
            # impossible for the closure to take on the lowering's word.
            outcome = self._loop_goals(node.iter, iteration_plan.activation(trip_count))
            counter = self.storage.fresh()
            body = ir.Seq(bind_target(ir.Var(counter)), body)
            iterator_entry, body = self._live_iterator_body(
                iteration_plan, ir.Var(counter), body)
            if continuing is not None:
                body = ir.Seq(ir.Assign(continuing, ir.Lit(True)), body)
            alive = self._iteration_guard(running)
            if alive is not None:
                # Leaving early stops the iterations that follow, whether or
                # not the count was written as a literal, and whether it was a
                # break or a return that left.
                body = ir.If(alive, body, ir.Skip())
            # A literal count below zero is an empty loop by the same rule.
            body = ir.For(counter, max(count.value, 0), body)
            body = ir.Seq(iterator_entry, body)
            if running is not None:
                body = ir.Seq(ir.Assign(running, ir.Lit(True)), body)
            if outcome is not None:
                body = ir.Seq(outcome, body)
            command = body if before is None else ir.Seq(before, body)
            return self._finish_loop_else(
                node, command, loop_input, body_entry, body_facts,
                ways, breaks, continues, running,
                may_be_empty=count.value <= 0)

        trips = self.storage.fresh()
        # A count below zero is no iterations, not iterations counted backwards:
        # `range(-1)` is empty. Clamping here is what makes the trip count the
        # number of iterations, so the guard below and the outcome goal are
        # both about the same quantity. Without it a negative count reported the
        # loop as entered, since it tested the count against zero for equality
        # and a negative number is not equal to zero.
        pre = self._flush(ir.Assign(trips, ir.Op(
            "ite", ir.Op("lt", count, ir.Lit(0)), ir.Lit(0), count)))
        # The same rule as a literal count, asked of the clamped trip count.
        # Both paths through this function now say what they mean in the
        # representation rather than one of them saying it in a side table.
        outcome = self._loop_goals(node.iter, iteration_plan.activation(ir.Var(trips)))
        if outcome is not None:
            pre = ir.Seq(pre, outcome)
        if bound == 0:
            command = ir.Seq(pre, self._false_goal_terms(node.body))
            if node.orelse:
                otherwise, ways = self.body(node.orelse)
                return ir.Seq(command, otherwise), ways
            return command, {NORMAL}
        empty_entry = self._flow_facts()
        body_indices, bind_target = self._prepare_loop_target(
            node.target, iteration_plan, node.iter)
        body_entry = self._loop_source_body_entry(
            node, self._flow_facts())
        running, continuing = self._loop_controls(node)
        with self._loop_scope(running, continuing, indices=body_indices) as loop:
            breaks, continues = loop.breaks, loop.continues
            self._set_flow_facts(body_entry)
            inner, ways = self._with_iterator_binding(
                iteration_plan.binding, lambda: self.body(node.body))
            body_facts = self._flow_facts()
        counter = self.storage.fresh()
        inner = ir.Seq(bind_target(ir.Var(counter)), inner)
        iterator_entry, inner = self._live_iterator_body(
            iteration_plan, ir.Var(counter), inner)
        pre = ir.Seq(pre, iterator_entry)
        if continuing is not None:
            inner = ir.Seq(ir.Assign(continuing, ir.Lit(True)), inner)
        if running is not None:
            pre = ir.Seq(ir.Assign(running, ir.Lit(True)), pre)
        # Leaving early stops the iterations that follow.  Whether an
        # iteration is due is already the semantic loop's trip count; only the
        # source control-exit flag remains as a body guard.
        alive = self._iteration_guard(running)
        if alive is not None:
            inner = ir.If(alive, inner, ir.Skip())
        # The semantic IR retains the captured trip count and one body.  The
        # proved ``bound`` above establishes that this occurrence is finite;
        # it is consumed by EncodingPlan, not written into the program by
        # copying or guarding a maximum number of iterations here.
        command = ir.Seq(pre, ir.For(counter, ir.Var(trips), inner))
        return self._finish_loop_else(
            node, command, loop_input, body_entry, body_facts,
            ways, breaks, continues, running, may_be_empty=True,
            empty_entry=empty_entry)

    def _live_iterator_body(self, plan, counter, body):
        """An exhausted list iterator stays exhausted; this is not a break.

        The retained domain bounds the checks. A mutable list's current
        length still decides whether next() can enter its body. Keep this
        flag separate from source break/continue so normal exhaustion runs
        a loop's else suite, and a reverse iterator cannot resume after a
        failed index merely because its next index would be smaller.
        """
        if plan.available is None:
            return ir.Skip(), body
        active = self.storage.fresh()
        self._record_range(active, ir.Lit(True))
        return (ir.Assign(active, ir.Lit(True)),
                ir.If(ir.Var(active), ir.If(
                    plan.available(counter), body,
                    ir.Assign(active, ir.Lit(False))), ir.Skip()))

    def _finish_loop_else(
            self, node, command, loop_input, body_entry, body_facts,
            ways, breaks, continues, running, *, may_be_empty,
            empty_entry=None):
        """Attach a loop ``else`` to the loop's non-break exits.

        ``break`` and exhaustion are different source exits even though both
        reach the statement after the loop.  The loop control already records
        that distinction in ``running``; the abstract facts are kept separate
        here for the same reason.  Consequently the else body is neither run
        after a break nor planned from a state reachable only by a break.
        """
        normal_exits = self._loop_exit_facts(
            body_entry, body_facts, ways, (), continues,
            may_be_empty=False)
        if may_be_empty:
            normal_exits.append(
                body_entry if empty_entry is None else empty_entry)
        if not node.orelse:
            exits = [*breaks, *normal_exits]
            self._set_joined_flow_facts(
                exits, ranges.post(command, loop_input.ranges))
            return command, _loop_ways(ways)

        else_ways = set()
        final_normal = []
        if normal_exits:
            self._set_joined_flow_facts(normal_exits)
            otherwise, else_ways = self.body(node.orelse)
            else_facts = self._flow_facts()
            if NORMAL in else_ways:
                final_normal.append(else_facts)
            active = self._iteration_guard(running)
            if active is None:
                command = ir.Seq(command, otherwise)
            else:
                command = ir.Seq(
                    command, ir.If(active, otherwise, ir.Skip()))
        else:
            # The exact loop body always breaks or returns before exhaustion.
            # Preserve catalogue accounting without inventing representation
            # obligations for a source-unreachable else body.
            command = ir.Seq(
                command, self._false_goal_terms(node.orelse))

        exits = [*breaks, *final_normal]
        self._set_joined_flow_facts(
            exits, ranges.post(command, loop_input.ranges))
        return command, _loop_else_ways(
            ways, else_ways, breaks=breaks, exhausts=bool(normal_exits))

    def _prepare_loop_target(self, target, iteration, iterable):
        """Bind one retained-loop item to a scalar or unpacking target.

        The iterable is evaluated once outside the loop.  Each iteration then
        constructs its logical item and performs Python's fixed-arity target
        binding before the body.  Tuple and starred targets reuse the same
        recursive value-contract rule as comprehensions; the difference is
        that ordinary ``for`` targets remain visible after the loop.
        """
        self._require_writable_target(target)

        def require_plain(item):
            if isinstance(item, ast.Starred):
                require_plain(item.value)
                return
            if isinstance(item, (ast.Tuple, ast.List)):
                for child in item.elts:
                    require_plain(child)
                return
            self._require_plain_rebinding(item, "a loop target")

        require_plain(target)
        if isinstance(target, ast.Name):
            index = self._name(target.id)
            sample = None if iteration.value is None else iteration.value(ir.Lit(0))
            if isinstance(sample, _StaticRef):
                index = self._written(target)
                _command, bound = sample.bind(self, index, target)
                bound.install(self, index)

                def bind(counter):
                    return iteration.value(counter).bind(self, index, target)[0]

                return (index,), bind
            if isinstance(sample, _PositionalValue):
                builder = self._new_positional_binding(index, sample, target)

                def bind(counter):
                    item = iteration.value(counter)
                    return ir._sequence([field.bind(self, name, target)[0]
                                         if isinstance(field, _StaticRef) else ir.Assign(name, field)
                                         for name, field in zip(builder.names, item.items)])

                return builder.names, bind
            if isinstance(sample, OptionValue):
                contract = (sample.contract if sample.contract.is_option
                            else ValueContract.option(SCALAR))
                cursor = self.storage.fresh()
                self.flow.bounds[cursor] = ranges.Bounds(0, iteration.bound - 1)
                self.flow.ranges[cursor] = ranges.Range(0, iteration.bound - 1)
                command = self._bind_option(
                    target.id, iteration.value(ir.Var(cursor)), target, contract)
                holders = tuple(sorted({part.var for part in ir.walk(command)
                                        if isinstance(part, (ir.Assign, ir.Store))}))

                def bind(counter):
                    return self._bind_option(target.id, iteration.value(counter), target, contract)

                return holders, bind
            if isinstance(sample, RecordValue):
                # A loop record has the same recursive local representation
                # as an ordinary assignment; it is not one scalar IR name.
                # Seed ranges from an arbitrary visit, not just item zero.
                cursor = self.storage.fresh()
                self.flow.bounds[cursor] = ranges.Bounds(0, iteration.bound - 1)
                self.flow.ranges[cursor] = ranges.Range(0, iteration.bound - 1)
                command = self._bind_product(target.id, iteration.value(ir.Var(cursor)), target)
                holders = tuple(sorted({part.var for part in ir.walk(command)
                                        if isinstance(part, (ir.Assign, ir.Store))}))

                def bind(counter):
                    return self._bind_product(target.id, iteration.value(counter), target)

                return holders, bind
            if index not in self.storage.temporaries:
                self.storage.temporaries.append(index)
            self._bind_iteration_contract(
                index, iteration.value, iterable,
                bound=iteration.bound, element=iteration.element)

            def bind(counter):
                value = (counter if iteration.value is None else
                         iteration.value(counter))
                return ir.Assign(index, value)

            return (index,), bind

        if not isinstance(target, (ast.Tuple, ast.List)) \
                or iteration.value is None \
                or not isinstance(iteration.element, ValueContract):
            raise LoweringError(
                target, "a loop target has no fixed tuple destructuring "
                        "contract",
                code="loop-target-shape-unsupported")
        sample = iteration.value(ir.Lit(0))
        values = self._loop_target_values(
            target, sample, iteration.element)
        if not values:
            raise LoweringError(
                target, "a loop target binds no names",
                code="loop-target-shape-unsupported")
        physical = {name: self._name(name) for name in values}
        for name, (expression, contract) in values.items():
            holder = physical[name]
            if holder not in self.storage.temporaries:
                self.storage.temporaries.append(holder)
            if isinstance(expression, _StaticRef):
                _command, bound = expression.bind(self, holder, target)
                bound.install(self, holder)
                continue
            shape = contract.fixed_shape
            if shape is None:
                raise LoweringError(
                    target, "a loop target contains a dynamically sized value",
                    code="loop-target-shape-unsupported")
            self.storage.require_shape(holder, shape, target)
            self.flow.contracts[holder] = contract
            if shape:
                self.flow.lengths[holder] = shape
                if contract.carrier is not None:
                    self.flow.carriers[holder] = contract.carrier
            else:
                self.flow.lengths.pop(holder, None)
                self.flow.carriers.pop(holder, None)

        # Give each target leaf the envelope of every iteration, just as the
        # scalar loop-target rule does without enumerating the body.
        counter = f"#loop-target:{id(target)}"
        prior_bound = self.flow.bounds.get(counter)
        prior_range = self.flow.ranges.get(counter)
        self.flow.bounds[counter] = ranges.Bounds(0, iteration.bound - 1)
        finite = self.flow.bounds[counter].finite()
        if finite is not None:
            self.flow.ranges[counter] = finite
        try:
            item = iteration.value(ir.Var(counter))
            ranged = self._loop_target_values(
                target, item, iteration.element)
            for name, (expression, _contract) in ranged.items():
                holder = physical[name]
                if isinstance(expression, _StaticRef):
                    continue
                self._record_range(holder, expression)
                if not self.storage.shapes[holder]:
                    self._remember_source_scalar_type(
                        holder,
                        _fixed_expression_type(
                            expression, self.source_scalar_types.get))
        finally:
            if prior_bound is None:
                self.flow.bounds.pop(counter, None)
            else:
                self.flow.bounds[counter] = prior_bound
            if prior_range is None:
                self.flow.ranges.pop(counter, None)
            else:
                self.flow.ranges[counter] = prior_range

        def bind(counter_value):
            item = iteration.value(counter_value)
            fields = self._loop_target_values(
                target, item, iteration.element)
            command = None
            for name, (expression, _contract) in fields.items():
                one = (expression.bind(self, physical[name], target)[0]
                       if isinstance(expression, _StaticRef)
                       else ir.Assign(physical[name], expression))
                command = one if command is None else ir.Seq(command, one)
            return ir.Skip() if command is None else command

        return tuple(physical.values()), bind

    def _positional_value(self, items, contracts, contract):
        """A source-only product of independently typed IR projections."""
        return _PositionalValue(
            tuple(items), tuple(contracts),
            tuple(_fixed_expression_type(item, self.source_scalar_types.get)
                  if child.kind == "scalar" else None
                  for item, child in zip(items, contracts)), contract)

    def _new_positional_binding(self, name, sample, node):
        """Allocate the existing parallel-field layout for one loop binding."""
        shapes = tuple(child.fixed_shape for child in sample.contracts)
        if any(shape is None for shape in shapes):
            raise LoweringError(node, "a positional loop binding needs fixed component shapes",
                                code="loop-target-shape-unsupported")
        previous = self.storage.carrier(_PositionalBuilder, name)
        if previous is not None:
            if (previous.shapes != shapes or previous.contract != sample.contract
                    or previous.scalar_types != sample.scalar_types):
                raise LoweringError(node, "a positional binding changes its component layout",
                                    code="value-representation-changes")
            builder = previous
        else:
            builder = _PositionalBuilder(tuple(self.storage.fresh() for _ in sample.items),
                                         shapes, sample.contracts, sample.scalar_types,
                                         sample.contract)
        self.storage.bind_carrier(name, builder)
        self.flow.contracts[name] = sample.contract
        for holder, shape, child, kind in zip(builder.names, shapes, sample.contracts,
                                             sample.scalar_types):
            self.storage.require_shape(holder, shape, node)
            self.flow.contracts[holder] = child
            self._remember_source_scalar_type(holder, kind)
            if shape:
                self.flow.lengths[holder] = shape
                if child.carrier is not None:
                    self.flow.carriers[holder] = child.carrier
        for holder, item in zip(builder.names, sample.items):
            if isinstance(item, _StaticRef):
                _command, bound = item.bind(self, holder, node)
                bound.install(self, holder)
        return builder

    def _binding_fields(self, name, value):
        """IR assignments for a scalar/array binding or a positional product."""
        if isinstance(value, _PositionalValue):
            return tuple(zip(self.storage.require_carrier(_PositionalBuilder, name).names, value.items))
        return ((name, value),)

    def _bind_iteration_contract(
            self, index, value, iterable, bound=None, element=None):
        """Record the shape and carrier yielded by one iterable element.

        The loop command binds the element at run time; lowering its body needs
        the same fact before it can type a subscript of that element.  Scalar
        ranges have ``value is None``.  A sequence iterator supplies a selector,
        whose shape is exactly the outer sequence shape with one dimension
        removed.
        """
        if element is None:
            contract = self._contract_ast(iterable)
            element = (contract.element if contract.is_sequence else None)
        shape = () if value is None else self._shape(value(ir.Lit(0)))
        self.storage.require_shape(index, shape, iterable)
        if shape:
            self.flow.lengths[index] = shape
        else:
            self.flow.lengths.pop(index, None)
        carrier = None if element is None else element.carrier
        if carrier is None:
            self.flow.carriers.pop(index, None)
        else:
            self.flow.carriers[index] = carrier
        if element is None:
            self.flow.contracts.pop(index, None)
        else:
            self.flow.contracts[index] = element

        # The compact lowerer translates one body for every finite iteration.
        # Its structural decisions must therefore see the envelope of values
        # the source target can take, not the target's value before the loop.
        # Graph lowering records each copy separately and obtains the same
        # answer as a singleton envelope.
        if bound is None or bound <= 0:
            self.flow.ranges.pop(index, None)
            self.flow.bounds.pop(index, None)
            return
        if value is None:
            got = ranges.Bounds(0, bound - 1)
            self.flow.bounds[index] = got
            self.flow.ranges[index] = got.finite()
            return
        # The frontend retains one body, so deriving the target envelope must
        # not enumerate the encoding maximum either.  Give the semantic
        # iteration number its proved interval and transform the mapping once.
        counter = f"#loop-bound:{id(iterable)}"
        counter_bounds = dict(self.flow.bounds)
        counter_bounds[counter] = ranges.Bounds(0, bound - 1)
        got = ranges.bounds_of_expression(
            value(ir.Var(counter)), counter_bounds)
        if got is None:
            self.flow.ranges.pop(index, None)
            self.flow.bounds.pop(index, None)
            return
        self.flow.bounds[index] = got
        finite = got.finite()
        if finite is None:
            self.flow.ranges.pop(index, None)
        else:
            self.flow.ranges[index] = finite

    def _iteration_guard(self, running):
        """The condition an iteration of a loop runs under.

        Both flags, and the return flag even when the body cannot break: a
        return inside a loop leaves the procedure, so the iterations after it
        must not run. Testing only the loop's own flag is how a loop whose first
        iteration returned went on to run the rest of them.
        """
        parts = []
        if self.activation.destination is not None:
            parts.append(ir.Op("eq", ir.Var(self.activation.destination.done), ir.Lit(0)))
        if running is not None:
            parts.append(ir.Var(running))
        if not parts:
            return None
        cond = parts[0]
        for more in parts[1:]:
            cond = ir.Op("land", cond, more)
        return cond

    def _loop_controls(self, node):
        """Allocate the distinct break and per-iteration continue controls."""
        running = self.storage.fresh() if _may_break(node) else None
        continuing = self.storage.fresh() if _may_continue(node) else None
        return running, continuing

    def _while_trip_count(self, node):
        """The exact entry-time trip expression proved for one source while.

        A ``while`` is admitted only when every back-edge advances one scalar
        local or state induction variable by the same constant toward an
        invariant bound.
        The resulting expression is the number of natural iterations, not a
        frontend ceiling.  ``break`` and ``return`` use the existing loop-exit
        controls to suppress later copies.
        """
        try:
            scope = self.activation.scope
            progress = analyse_while(
                node, resolver=getattr(self, "resolver", None), owner=scope)
        except WhilePlanError as exc:
            raise LoweringError(
                node, str(exc), code="while-trip-count-unproved") from None

        # The first source test precedes all trip-count arithmetic.  In
        # particular, a reversed comparison must fail at its left operand
        # before reading the induction on the right.  Ranking expressions
        # below are bookkeeping, not additional source occurrences.
        self._truth_expr(node.test)
        start = ir.Var(self._source_name(progress.name))
        stop = self._while_plan_value(progress.bound)
        if progress.not_equal:
            body_guard = ir.Op("ne", start, stop)
        elif progress.operation in {"rshift", "parent"} or progress.amount < 0:
            body_guard = ir.Op(
                "ge" if progress.inclusive else "gt", start, stop)
        else:
            body_guard = ir.Op(
                "le" if progress.inclusive else "lt", start, stop)

        if progress.operation == "lshift":
            start_bounds = ranges.bounds_of_expression(start, self.flow.bounds)
            if start_bounds is None or start_bounds.lo is None or start_bounds.lo <= 0:
                raise LoweringError(
                    node, "a left-shift while has no proved positive induction domain",
                    code="while-trip-plan-unavailable")
            numerator = stop if progress.inclusive else ir.Op("sub", stop, ir.Lit(1))
            nonnegative = ir.Op("ite", ir.Op("gt", numerator, ir.Lit(0)), numerator, ir.Lit(0))
            quotient = ir.Op("fdiv", nonnegative, start)
            bits = ir.Op("bitlen", quotient)
            trips = ir.Op("fdiv", ir.Op("add", bits, ir.Lit(progress.amount - 1)),
                          ir.Lit(progress.amount))
        elif progress.operation == "parent":
            nonnegative = ir.Op("ite", ir.Op("gt", start, ir.Lit(0)), start, ir.Lit(0))
            trips = ir.Op("sub", ir.Op("bitlen", ir.Op("add", nonnegative, ir.Lit(1))), ir.Lit(1))
        elif progress.operation == "rshift":
            start_bounds = ranges.bounds_of_expression(start, self.flow.bounds)
            stop_bounds = ranges.bounds_of_expression(stop, self.flow.bounds)
            if (stop_bounds is None or stop_bounds.lo is None or stop_bounds.lo < 0
                    or (progress.not_equal and (start_bounds is None
                        or start_bounds.lo is None or start_bounds.lo < 0))):
                raise LoweringError(
                    node, "a right-shift while has no proved nonnegative "
                          "induction and bound domain",
                    code="while-trip-plan-unavailable")
            nonnegative = ir.Op("ite", ir.Op("gt", start, ir.Lit(0)), start, ir.Lit(0))
            quotient = ir.Op(
                "fdiv", nonnegative, ir.Op("add", stop, ir.Lit(1)))
            bits = ir.Op("bitlen", quotient)
            trips = ir.Op(
                "fdiv",
                ir.Op("add", bits, ir.Lit(progress.amount - 1)),
                ir.Lit(progress.amount),
            )
        else:
            ascending = progress.amount > 0
            distance = (ir.Op("sub", stop, start) if ascending
                        else ir.Op("sub", start, stop))
            # ``x <= b`` has one more admissible integer point than ``x < b``.
            numerator = (ir.Op("add", distance, ir.Lit(1))
                         if progress.inclusive else distance)
            stride = abs(progress.amount)
            rounded = ir.Op(
                "fdiv",
                ir.Op("add", numerator, ir.Lit(stride - 1)),
                ir.Lit(stride),
            )
            trips = ir.Op(
                "ite", ir.Op("gt", numerator, ir.Lit(0)), rounded, ir.Lit(0))
        scope = self.activation.scope
        context = self.activation.summary
        planned = (None if self.sequence_plan is None or scope is None else
                   self.sequence_plan.loop_span(node, scope, context))
        if planned is None:
            raise LoweringError(
                node, "the while has no finite task-domain trip plan from "
                      "the source sequence plan",
                code="while-trip-plan-unavailable")
        # This occurrence plan is the task-domain range authority.  In the
        # first state-range pass the executable expression may mention state
        # whose finite range is precisely what that pass is constructing; a
        # second range inference here would therefore create a circular and
        # path-insensitive gate.  The expression above still determines the
        # run-time trip count.  ``planned`` only bounds its representation.
        maximum = max(0, planned.hi)
        self.source_loop_spans[id(node)] = (
            max(0, planned.lo), maximum)
        return trips, maximum, progress, body_guard

    def _while_plan_value(self, node):
        """IR arithmetic for a proved pure ranking, without source events.

        Ranking bounds can occur in short-circuited tests or after a body
        exit.  Inspecting their ranges must not execute those source reads,
        emit guards, or manufacture locations.  The real condition/body is
        lowered normally and owns all safety and observation events.
        """
        if isinstance(node, ast.Constant) and type(node.value) in (int, bool):
            return ir.Lit(node.value)
        if isinstance(node, ast.Name):
            if node.id in self.activation.substitutions:
                return self.activation.substitutions[node.id]
            if self.resolver is not None and self.activation.scope:
                kind, value = self.resolver.resolve_const(self.activation.scope, node.id)
                if kind == "const":
                    return ir.Lit(value)
            return ir.Var(self._name(node.id))
        if isinstance(node, ast.Attribute):
            return (ir.Lit(self.fixed[node.attr]) if node.attr in self.fixed
                    else ir.Var(self.storage.state_name(node.attr)))
        if isinstance(node, ast.UnaryOp):
            spec = primitives.source_primitive(
                primitives.SourceFamily.UNARY, type(node.op))
            if spec is not None:
                return ir.Op(spec.name, self._while_plan_value(node.operand))
        if isinstance(node, ast.BinOp):
            spec = primitives.source_primitive(
                primitives.SourceFamily.BINARY, type(node.op))
            if spec is not None:
                return ir.Op(spec.name, self._while_plan_value(node.left),
                             self._while_plan_value(node.right))
        raise LoweringError(node, "the while ranking is not scalar arithmetic",
                            code="while-trip-plan-unavailable")

    def _while_ranking_bound(self, node):
        """Prove a scalar sum rank or finite-table/worklist progress bound.

        The latter returns no scalar ranking: its body uses ordinary widened
        source facts, not invented induction ranges.
        """
        scope = self.activation.scope
        try:
            ranking = analyse_while_ranking(
                node, resolver=getattr(self, "resolver", None), owner=scope)
        except WhilePlanError as exc:
            context = self.activation.summary
            domain = (None if self.sequence_plan is None or scope is None else
                      self.sequence_plan.loop_domain(node, scope, context))
            if domain is None or domain[0] != 'finite-progress':
                raise LoweringError(
                    node, str(exc), code="while-trip-count-unproved") from None
            span = self.sequence_plan.loop_span(node, scope, context)
            self.source_loop_spans[id(node)] = (span.lo, span.hi)
            return span.hi, None, ()

        inductions, bounds = [], []
        for component in ranking.components:
            start = ir.Var(self._source_name(component.name))
            stop = self._while_plan_value(component.bound)
            start_bounds = ranges.bounds_of_expression(start, self.flow.bounds)
            stop_bounds = ranges.bounds_of_expression(stop, self.flow.bounds)
            start_range = None if start_bounds is None else start_bounds.finite()
            stop_range = None if stop_bounds is None else stop_bounds.finite()
            if start_range is None or stop_range is None:
                raise LoweringError(
                    node, "the ranked while has no finite task-domain "
                          "value range",
                    code="while-trip-plan-unavailable")
            inductions.append(start_range)
            bounds.append(stop_range)
        span = ranking_span(ranking, inductions, bounds)
        context = self.activation.summary
        planned = (None if self.sequence_plan is None or scope is None else
                   self.sequence_plan.loop_span(node, scope, context))
        if planned is None:
            raise LoweringError(
                node, "the ranked while has no source sequence plan",
                code="while-trip-plan-unavailable")
        if span.hi > planned.hi:
            raise LoweringError(
                node, "source sequence planning does not contain the "
                      f"ranked while bound {span!r} in {planned!r}",
                code="while-trip-plan-inconsistent")
        maximum = max(0, span.hi)
        self.source_loop_spans[id(node)] = (0, maximum)
        return maximum, ranking, tuple(bounds)

    def _while_exit_bound(self, node):
        """Return the finite body-visit bound of an exit-dominated while."""

        scope = self.activation.scope
        try:
            ranking = analyse_while_exit_ranking(
                node, resolver=getattr(self, "resolver", None), owner=scope)
        except WhilePlanError as exc:
            raise LoweringError(
                node, str(exc), code="while-trip-count-unproved") from None

        context = self.activation.summary
        domain = (None if self.sequence_plan is None or scope is None else
                  self.sequence_plan.loop_domain(node, scope, context))
        if domain is None or domain[0] != 'cutoff' or domain[1] is None:
            raise LoweringError(
                node, "the cutoff-ranked while has no source sequence plan",
                code="while-trip-plan-unavailable")
        span = self.sequence_plan.loop_span(node, scope, context)
        self.source_loop_spans[id(node)] = (span.lo, span.hi)
        return span.hi, ranking, domain[1]

    def _loop_source_writes(self, node):
        """Bindings and mutable sequence values carried across body visits."""
        scope = self.activation.scope
        resolver = getattr(self, "resolver", None)
        written = loop_written_scalars(node, resolver=resolver, owner=scope)
        # In-place sequence changes do not have a Store-context Name. Reuse
        # the sequence plan's mutation authority, including helper effects,
        # rather than treating a pre-loop length as an invariant scalar.
        names = {part.id for root in node.body for part in ast.walk(root)
                 if isinstance(part, ast.Name)}
        sequences = {name for name in names if self._sequence_mutates(name)}
        # The length plan sees append/pop only. An element store keeps the
        # length but can change the element values the pre-loop facts describe.
        elements = loop_container_writes(node, resolver=resolver, owner=scope)
        return written | sequences | elements

    def _loop_numeric_body_entry(self, node, entering=None):
        """Forget pre-loop numeric facts changed by a retained body.

        The frontend lowers a semantic loop body once.  A value assigned in
        that body can therefore have a different value on a later visit; its
        pre-loop interval cannot justify pruning a branch in the retained
        body.  Induction variables are reintroduced below from their separate
        termination proof.  Other loop-carried scalars remain unknown until
        an assignment inside the body proves a new fact.
        """

        entering = self._flow_facts() if entering is None else entering
        finite = dict(entering.ranges)
        bounds = dict(entering.bounds)
        unknown = set(entering.unknown_ranges)
        scope = self.activation.scope
        written = self._loop_source_writes(node)
        if isinstance(node, ast.For):
            # The source target is assigned from the iterator at every body
            # entry. Its entry envelope is valid only when those yielded
            # values cannot change as the body modifies a live collection.
            targets = {name.id for name in ast.walk(node.target)
                       if isinstance(name, ast.Name)}
            scalar_names = {name for name in written
                            if entering.contracts.get(
                                self._source_name(name)) == SCALAR}
            stable = (scope is not None and loop_preserves_iterable_values(
                node, resolver=self.resolver, owner=scope,
                scalar_names=scalar_names,
                is_flat_sequence=lambda expression:
                    self._contract_ast(expression).element == SCALAR))
            written = (written - targets if stable else written | targets)
        for source_name in written:
            name = self._source_name(source_name)
            for variable in (name, *self.storage.length_images.get(name, {}).values()):
                if variable not in finite and variable not in bounds:
                    continue
                finite.pop(variable, None)
                bounds.pop(variable, None)
                unknown.add(variable)
        return flow_facts.FlowFacts(
            dict(entering.lengths), dict(entering.carriers),
            dict(entering.contracts), finite, bounds,
            frozenset(unknown), dict(entering.static_values))

    def _loop_source_body_entry(self, node, entering):
        """Source contracts valid at an arbitrary retained-loop visit.

        A retained loop has one IR body for every iteration.  A local written
        in that body can therefore enter a later visit with any contract the
        source fixed point assigned to it, even when its pre-loop value is
        narrower.  Numeric induction ranges remain under the loop rules; this
        method widens only the recursive source-value contract needed to
        choose a representation inside the shared body.
        """
        # A retained body represents every semantic iteration.  Start by
        # forgetting numeric facts changed by the body; otherwise a fact true
        # only before the first trip can prune a path required by later trips.
        # Recursive value contracts are widened separately below because they
        # describe representation shape rather than one numeric value.
        carried = self._loop_numeric_body_entry(node, entering)
        written = self._loop_source_writes(node)
        contracts = dict(carried.contracts)
        for source_name in written:
            if source_name.startswith("self."):
                continue
            name = self._name(source_name)
            planned = self._binding_layout_contract(source_name)
            if not planned.is_known:
                continue
            current = contracts.get(name, UNKNOWN)
            widened = (planned if current is UNKNOWN else
                       join_contracts(current, planned))
            if widened is not INCOMPATIBLE:
                contracts[name] = widened
        return flow_facts.FlowFacts(
            dict(carried.lengths), dict(carried.carriers), contracts,
            dict(carried.ranges), dict(carried.bounds),
            frozenset(carried.unknown_ranges),
            dict(carried.static_values))

    def _ranking_body_entry(self, node, ranking, bound_ranges):
        """Facts at any body visit of a ranked while.

        The body is retained once.  Each ranking comparison bounds its
        induction value whenever that body is entered, so expose the union of
        those values instead of the first-iteration range.
        """
        if ranking is None:
            return self._loop_source_body_entry(node, self._flow_facts())
        carried = self._loop_numeric_body_entry(node)
        updates = {}
        for component, stop in zip(ranking.components, bound_ranges):
            name = self._source_name(component.name)
            current = updates.get(name, carried.bounds.get(name))
            if current is None:
                continue
            if component.direction > 0:
                upper = stop.hi - (0 if component.inclusive else 1)
                widened = ranges.Bounds(current.lo, upper)
            else:
                lower = stop.lo + (0 if component.inclusive else 1)
                widened = ranges.Bounds(lower, current.hi)
            updates[name] = widened
        return flow_facts.with_bounds(carried, updates)

    def _ranking_test_entry(self, node, ranking, bound_ranges):
        """Facts at every condition check of a ranked while.

        A body-entry envelope ends immediately before the final update.  The
        next condition check also sees that update, and a following command
        may need its range.  The ranking records the largest one-edge advance
        of each component, so include exactly that terminal fringe without
        treating it as another body visit.
        """

        if ranking is None:
            return self._loop_source_body_entry(node, self._flow_facts())
        entering = self._flow_facts()
        carried = self._loop_numeric_body_entry(node)
        updates = {}
        for component, stop, step in zip(
                ranking.components, bound_ranges, ranking.maximum_steps):
            name = self._source_name(component.name)
            current = entering.bounds.get(name)
            if current is None:
                continue
            if component.direction > 0:
                last_body = stop.hi - (0 if component.inclusive else 1)
                widened = ranges.Bounds(current.lo, last_body + step)
            else:
                last_body = stop.lo + (0 if component.inclusive else 1)
                widened = ranges.Bounds(last_body - step, current.hi)
            updates[name] = widened
        return flow_facts.with_bounds(carried, updates)

    def _exit_ranking_body_entry(self, node, ranking, entry_range):
        """Numeric facts valid on every visit to a cutoff-ranked body."""

        carried = self._loop_numeric_body_entry(node)
        finite = dict(carried.ranges)
        bounds = dict(carried.bounds)
        unknown = set(carried.unknown_ranges)
        name = self._source_name(ranking.name)
        finite[name] = entry_range
        bounds[name] = ranges.Bounds.of(entry_range)
        unknown.discard(name)
        return flow_facts.FlowFacts(
            dict(carried.lengths), dict(carried.carriers),
            dict(carried.contracts), finite, bounds,
            frozenset(unknown), dict(carried.static_values))

    def _while_body_entry(self, node, progress, maximum):
        """Facts valid at every visit to a retained while body.

        The body is lowered once, so a structural decision made while lowering
        it must see the envelope of *all* semantic iterations.  Keeping the
        pre-loop value of the induction variable here makes a later-iteration
        ``break`` or ``continue`` look unreachable and deletes its control
        effect.  The progress proof says every back-edge changes the variable
        by exactly ``step``; applying that change at most ``maximum`` times
        gives a sound finite envelope without copying the body.
        """
        entering = self._flow_facts()
        carried = self._loop_numeric_body_entry(node)
        induction = self._source_name(progress.name)
        current = entering.bounds.get(induction)
        if current is None or maximum == 0:
            return carried
        if progress.operation in {"rshift", "parent"}:
            widened = ranges.Bounds(
                0 if current.lo is not None and current.lo >= 0 else None,
                current.hi,
            )
        elif progress.operation == "lshift":
            bound = ranges.bounds_of_expression(
                self._while_plan_value(progress.bound), entering.bounds)
            upper = None if bound is None or bound.hi is None or current.hi is None else max(
                current.hi, max(0, bound.hi - int(not progress.inclusive)) << progress.amount)
            widened = ranges.Bounds(current.lo, upper)
        elif maximum is None:
            widened = ranges.Bounds(None, None)
        else:
            shift = progress.amount * maximum
            widened = ranges.Bounds(
                None if current.lo is None else
                current.lo + min(0, shift),
                None if current.hi is None else
                current.hi + max(0, shift),
            )
        return flow_facts.with_bounds(carried, {induction: widened})

    def _while(self, node):
        """Lower an exact while, or a separately proved ranked while."""
        try:
            return self._exact_while(node)
        except LoweringError as exc:
            if exc.code != "while-trip-count-unproved":
                raise
        try:
            return self._bounded_while(node)
        except LoweringError as exc:
            if exc.code != "while-trip-count-unproved":
                raise
        return self._exit_bounded_while(node)

    def _empty_while_marks(self, node):
        """The source test ran once; a proved zero-trip body never runs."""
        yes, no = self._site_goal(node.test, True), self._site_goal(node.test, False)
        return ir.Seq(
            self._loop_goals(node, ir.Lit(0)) or ir.Skip(),
            ir.If(ir.Lit(False), ir.Mark(yes) if yes else ir.Skip(),
                  ir.Mark(no) if no else ir.Skip()))

    def _exact_while(self, node):
        """Lower a proved monotone while to the one semantic loop command.

        The former rule copied the body to a fixed frontend ceiling and then
        emitted ``Fail('Unwound')``.  That failure was neither a Python result nor a
        source goal.  Here the structural proof supplies the exact trip-count
        expression; the ordinary EncodingPlan proves its finite domain and
        owns any guarded expansion.
        """
        mark = self.mark()
        trip_count, maximum, progress, body_guard = self._while_trip_count(node)
        trip_count = self._capture(trip_count)
        before = self._flush(ir.Skip()) if self.hoisted_since(mark) else None
        if maximum == 0:
            empty = ir.Seq(self._empty_while_marks(node),
                           self._false_goal_terms(node.body))
            otherwise, ways = self.body(node.orelse)
            return ir.Seq(before or ir.Skip(), ir.Seq(empty, otherwise)), ways
        loop_input = self._flow_facts()
        body_entry = self._while_body_entry(node, progress, maximum)
        self._set_flow_facts(body_entry)
        condition_pre, _ = self._scoped(lambda: self._truth_expr(node.test))
        self._set_flow_facts(loop_input)
        # The retained body is visited only when the source condition is true.
        # Expose that path fact to nested structural decisions (for example a
        # dynamic ``range`` stride that is the positive induction variable).
        narrowed = flow_facts.assume(body_entry, body_guard, True)
        if narrowed is not None:
            body_entry = narrowed

        running = self.storage.fresh() if _may_break(node) else None
        continuing = self.storage.fresh() if _may_continue(node) else None
        counter = self.storage.fresh()
        with self._loop_scope(running, continuing) as loop:
            breaks, continues = loop.breaks, loop.continues
            self._set_flow_facts(body_entry)
            body, ways = self.body(node.body)
            body_facts = self._flow_facts()

        entered = body
        if continuing is not None:
            entered = ir.Seq(ir.Assign(continuing, ir.Lit(True)), entered)
        gt = self._site_goal(node.test, True)
        gf = self._site_goal(node.test, False)
        if gt:
            entered = ir.Seq(ir.Mark(gt), entered)
        # The entry test already ran before capturing the trip count.  Later
        # tests still own their read/operation goals, even though the progress
        # proof has replaced their Boolean control with a semantic For.
        entered = ir.Seq(ir.If(ir.Op("gt", ir.Var(counter), ir.Lit(0)),
                               condition_pre, ir.Skip()), entered)
        alive = self._iteration_guard(running)
        if alive is not None:
            entered = ir.If(alive, entered, ir.Skip())
        loop = ir.For(counter, trip_count, entered)
        if running is not None:
            loop = ir.Seq(ir.Assign(running, ir.Lit(True)), loop)
        after = ir.Seq(ir.If(ir.Op("gt", trip_count, ir.Lit(0)),
                            condition_pre, ir.Skip()),
                       ir.Mark(gf) if gf else ir.Skip())
        if alive is not None:
            after = ir.If(alive, after, ir.Skip())
        command = ir.Seq(loop, after)
        command = ir.Seq(self._loop_goals(node, trip_count) or ir.Skip(), command)
        if before is not None:
            command = ir.Seq(before, command)

        return self._finish_loop_else(
            node, command, loop_input, body_entry, body_facts,
            ways, breaks, continues, running,
            may_be_empty=(maximum == 0
                          or self.source_loop_spans[id(node)][0] == 0),
            empty_entry=loop_input)

    def _bounded_while_domain(self, node):
        """One source proof domain for either guarded-loop representation."""
        if self.sequence_plan is not None and self.activation.scope:
            context = self.activation.summary
            if self.sequence_plan.branch_feasible(
                    self.activation.scope, node.test, True, context) is False:
                self.source_loop_spans.setdefault(id(node), (0, 0))
                entry = self._flow_facts()
                return 0, entry, entry
        try:
            maximum, ranking, bound_ranges = self._while_ranking_bound(node)
        except LoweringError as exc:
            if exc.code != "while-trip-count-unproved":
                raise
            maximum, ranking, entry_range = self._while_exit_bound(node)
            if not ranking.guarded:
                raise
            entry = self._exit_ranking_body_entry(node, ranking, entry_range)
            return maximum, entry, entry
        return (maximum, self._ranking_body_entry(node, ranking, bound_ranges),
                self._ranking_test_entry(node, ranking, bound_ranges))

    def _bounded_while(self, node):
        """Retain a data-dependent while under a proved finite ranking.

        Unlike the exact recurrence rule, this loop reevaluates the original
        condition.  The ranking only limits how many true body visits are
        possible; it never predicts which branch advances which component.
        One additional retained iteration performs the final false test.
        """
        maximum, body_entry, test_entry = self._bounded_while_domain(node)
        if maximum == 0:
            condition, _ = self._scoped(lambda: self._truth_expr(node.test))
            empty = ir.Seq(condition, ir.Seq(self._empty_while_marks(node),
                                            self._false_goal_terms(node.body)))
            otherwise, ways = self.body(node.orelse)
            return ir.Seq(empty, otherwise), ways
        activation, first_body, zero_body = self._while_activation(node)
        loop_input = self._flow_facts()
        self._set_flow_facts(test_entry)
        condition_pre, condition = self._scoped(
            lambda: self._truth_expr(node.test))
        tested = self._flow_facts()
        true_facts = self._assumed_source_flow_facts(
            tested, node.test, condition, True)
        false_facts = self._assumed_source_flow_facts(
            tested, node.test, condition, False)
        if true_facts is None:
            true_facts = body_entry
        if false_facts is None:
            false_facts = test_entry

        running, continuing = self._loop_controls(node)
        with self._loop_scope(running, continuing) as loop:
            breaks, continues = loop.breaks, loop.continues
            self._set_flow_facts(true_facts)
            body, ways = self.body(node.body)
            body_facts = self._flow_facts()

        active = self.storage.fresh()
        entered = ir.Seq(first_body, body)
        if continuing is not None:
            entered = ir.Seq(ir.Assign(continuing, ir.Lit(True)), entered)
        gt = self._site_goal(node.test, True)
        gf = self._site_goal(node.test, False)
        if gt:
            entered = ir.Seq(ir.Mark(gt), entered)
        exhausted = ir.Seq(zero_body, ir.Assign(active, ir.Lit(False)))
        if gf:
            exhausted = ir.Seq(ir.Mark(gf), exhausted)
        decision = ir.Seq(
            condition_pre,
            ir.If(condition, entered, exhausted),
        )
        alive = self._iteration_guard(running)
        guard = ir.Var(active)
        if alive is not None:
            guard = ir.Op("land", alive, guard)
        decision = ir.If(guard, decision, ir.Skip())
        counter = self.storage.fresh()
        loop = ir.For(counter, ir.Lit(maximum + 1), decision)
        prefix = ir.Assign(active, ir.Lit(True))
        if running is not None:
            prefix = ir.Seq(ir.Assign(running, ir.Lit(True)), prefix)
        command = ir.Seq(activation, ir.Seq(prefix, loop))

        # Static references are lowering facts rather than IR values.  A
        # changed one cannot be carried through a retained body.
        self._reject_loop_carried_static_references(
            node, body_entry, [body_facts, *breaks, *continues], maximum)
        exit_ways = {NORMAL} | (ways & {RETURNED})
        return self._finish_loop_else(
            node, command, loop_input, body_entry, false_facts,
            exit_ways, breaks, (), running, may_be_empty=False)

    def _exit_bounded_while(self, node):
        """Retain a constant-true loop under a cutoff-ranking proof."""

        visits, ranking, entry_range = self._while_exit_bound(node)
        body_entry = self._exit_ranking_body_entry(
            node, ranking, entry_range)
        running, continuing = self._loop_controls(node)
        with self._loop_scope(running, continuing) as loop:
            breaks, continues = loop.breaks, loop.continues
            self._set_flow_facts(body_entry)
            body, ways = self.body(node.body)
            body_facts = self._flow_facts()

        entered = body
        if continuing is not None:
            entered = ir.Seq(ir.Assign(continuing, ir.Lit(True)), entered)
        true_goal = self._site_goal(node.test, True)
        if true_goal:
            entered = ir.Seq(ir.Mark(true_goal), entered)
        alive = self._iteration_guard(running)
        if alive is not None:
            entered = ir.If(alive, entered, ir.Skip())
        counter = self.storage.fresh()
        loop = ir.For(counter, ir.Lit(visits), entered)
        command = (loop if running is None else
                   ir.Seq(ir.Assign(running, ir.Lit(True)), loop))
        command = ir.Seq(self._loop_goals(node, visits) or ir.Skip(), command)
        false_goal = self._site_goal(node.test, False)
        if false_goal:
            # The source condition is the literal ``True``.  Its false
            # outcome remains in the public goal catalogue, but no execution
            # can hit it.  Keep that fact explicit in the IR so a missing
            # producer cannot be confused with a proved-unreachable goal.
            command = ir.Seq(
                command,
                ir.If(ir.Lit(False), ir.Mark(false_goal), ir.Skip()),
            )
        self._reject_loop_carried_static_references(
            node, body_entry, [body_facts, *breaks, *continues], visits)

        if node.orelse:
            command = ir.Seq(command, self._false_goal_terms(node.orelse))
        self._set_joined_flow_facts(breaks)
        result_ways = ({NORMAL} if breaks else set())
        if RETURNED in ways:
            result_ways.add(RETURNED)
        return command, result_ways

    def _iterator_binding_only_changes(self, loop, source):
        """Prove a loop may replace this name but cannot mutate held objects.

        A value capture is an exact iterator reference in this case. Calls use
        the ordinary lexical effect authority; subscript writes, deletion and
        non-scalar augmented assignment do not establish this proof. Mutable
        iterators are not snapshots and must keep their separate live behavior.
        """
        if not isinstance(loop, ast.For) or not isinstance(source, ast.Name):
            return False
        nodes = [part for stmt in loop.body for part in ast.walk(stmt)]
        if not any(isinstance(part, ast.Name)
                   and isinstance(part.ctx, ast.Store)
                   and part.id == source.id for part in nodes):
            return False
        if self._effectful(loop):
            return False
        for part in nodes:
            if isinstance(part, ast.Delete):
                return False
            if isinstance(part, ast.Subscript) and isinstance(part.ctx, ast.Store):
                return False
            if isinstance(part, ast.AugAssign) \
                    and self._contract_ast(part.target) != SCALAR:
                return False
        return True

    def _iterator_binding_may_change(self, loop, source):
        if not isinstance(loop, ast.For):
            return False
        for statement in [loop.iter, *loop.body]:
            for part in ast.walk(statement):
                same_name = (isinstance(part, ast.Name)
                             and isinstance(source, ast.Name)
                             and part.id == source.id)
                same_field = (isinstance(part, ast.Attribute)
                              and isinstance(source, ast.Attribute)
                              and part.attr == source.attr
                              and isinstance(part.value, ast.Name)
                              and isinstance(source.value, ast.Name)
                              and part.value.id == source.value.id == "self")
                if (same_name or same_field) and isinstance(part.ctx, ast.Store):
                    return True
                if (isinstance(part, ast.Call) and self._effectful(part)
                        and sequence_method_target(part, "append") is None
                        and sequence_method_target(part, "pop") is None):
                    return True
        return False

    def _dynamic_bound(self, node, where=None):
        return self._iterable_plan(node, node.iter, node if where is None else where)

    def _iterable_plan(self, node, it, where):
        """The trip count of a loop and the value its index takes.

        ``range`` with a start or a step gives an index that is not the
        iteration number, and iterating a sequence gives one that is an element
        of it. Both are the iteration number mapped through an expression, so
        one rule covers them: the loop runs over ``j`` and the source's variable
        is assigned from ``j`` at the top of the body.

        ``where`` is the node a refusal is reported against, for callers whose
        iterable belongs to a node that carries no position of its own.
        """
        if (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and self._spelling_is_builtin(it.func.id)
                and it.func.id in {"enumerate", "range", "reversed", "zip"}):
            if it.keywords or any(
                    isinstance(argument, ast.Starred)
                    for argument in it.args):
                raise LoweringError(
                    it, f"builtin {it.func.id} with keyword or unpacked "
                        "arguments", code="call-argument-form")
            if it.func.id == "reversed" and len(it.args) != 1:
                raise LoweringError(
                    it, "builtin reversed with wrong arity",
                    code="call-arity")
            if it.func.id == "enumerate" and len(it.args) not in {1, 2}:
                raise LoweringError(
                    it, "builtin enumerate with wrong arity",
                    code="call-arity")
        sequence_node = it
        backwards_sequence = False
        dynamic_span = None
        enumerate_start = None
        available = None
        binding = None
        if (isinstance(it, ast.Call)
                and isinstance(it.func, ast.Name)
                and it.func.id == "enumerate"
                and self._spelling_is_builtin("enumerate")):
            sequence_node = it.args[0]
            enumerate_start = it.args[1] if len(it.args) == 2 else ast.Constant(0)
            ast.copy_location(enumerate_start, it)
        if (isinstance(it, ast.Call)
                and isinstance(it.func, ast.Name)
                and it.func.id == "reversed" and len(it.args) == 1
                and self._spelling_is_builtin("reversed")):
            sequence_node = it.args[0]
            backwards_sequence = True
        if (isinstance(sequence_node, ast.Call)
                and getattr(sequence_node.func, "id", None) == "zip"
                and self._spelling_is_builtin("zip")):
            if sequence_node.keywords or any(
                    isinstance(argument, ast.Starred) for argument in sequence_node.args):
                raise LoweringError(sequence_node,
                    "builtin zip with keyword or unpacked arguments",
                    code="call-argument-form")
            if backwards_sequence:
                raise LoweringError(sequence_node, "zip is not reversible",
                                    code="call-unlowered")
            plans, bindings, spans = [], [], []
            for argument in sequence_node.args:
                # Arguments evaluate left-to-right. Earlier held objects must
                # survive rebinding while evaluating a later argument too.
                plan = self._with_iterator_binding(
                    tuple(bindings), lambda argument=argument: self._iterable_plan(
                        node, argument, where))
                plans.append(plan)
                spans.append(self.source_loop_spans[id(node)])
                if plan.binding is not None:
                    bindings.extend(plan.binding if isinstance(plan.binding, tuple)
                                    else (plan.binding,))
            binding = tuple(bindings) or None
            dynamic_span = (min((span[0] for span in spans), default=0),
                            min((span[1] for span in spans), default=0))
            counts = [ir.Lit(max(plan.count.value, 0)) if isinstance(plan.count, ir.Lit)
                      else ir.Op("ite", ir.Op("lt", plan.count, ir.Lit(0)),
                                 ir.Lit(0), plan.count) for plan in plans]
            count = counts[0] if counts else ir.Lit(0)
            for other in counts[1:]:
                count = (ir.Lit(min(count.value, other.value))
                         if isinstance(count, ir.Lit) and isinstance(other, ir.Lit)
                         else ir.Op("ite", ir.Op("lt", count, other), count, other))
            components = tuple(plan.element for plan in plans)
            element = ValueContract.positional("tuple", components)
            if dynamic_span[1] and any(not child.is_known for child in components):
                raise LoweringError(sequence_node, "zip over elements without a known representation",
                                    code="zip-element-unsupported")

            def value(j, plans=tuple(plans), element=element, components=components):
                items = tuple(j if plan.value is None else plan.value(j) for plan in plans)
                return (ir.Op("arraylit", *items) if element.components is None
                        and not any(isinstance(item, _StaticRef) for item in items) else
                        self._positional_value(items, components, element))

            availability = tuple(plan.available for plan in plans if plan.available is not None)
            if availability:
                def available(j):
                    result = availability[0](j)
                    for check in availability[1:]:
                        result = ir.Op("land", result, check(j))
                    return result
        elif isinstance(sequence_node, ast.Call) \
                and getattr(sequence_node.func, "id", None) == "range" \
                and self._spelling_is_builtin("range"):
            if sequence_node.keywords or any(
                    isinstance(argument, ast.Starred)
                    for argument in sequence_node.args):
                raise LoweringError(
                    sequence_node,
                    "builtin range with keyword or unpacked arguments",
                    code="call-argument-form")
            args = sequence_node.args
            if len(args) == 1:
                count = self._capture(self._integer_context_expr(args[0]))
                value = None
            elif len(args) in (2, 3):
                # Preserve source-proved constants before value capture turns
                # an expression such as -1 into a temporary. Read each fact
                # at its own evaluation point: later operands may have effects.
                start_fixed = self._static_int(args[0])
                start = self._capture(self._integer_context_expr(args[0]))
                stop_fixed = self._static_int(args[1])
                stop = self._capture(self._integer_context_expr(args[1]))
                # A step written as a model parameter is as fixed as one
                # written as a number, so it is read through the constants.
                step_value = (1 if len(args) < 3
                              else self._static_int(args[2]))
                step = (None if step_value is not None else
                        self._capture(self._integer_context_expr(args[2])))
                step_bounds = (None if step is None else
                               ranges.bounds_of_expression(step, self.flow.bounds))
                if step_value == 0 or (step_value is None and (
                        step_bounds is None
                        or step_bounds.lo is None or step_bounds.hi is None
                        or not (step_bounds.lo > 0 or step_bounds.hi < 0))):
                    raise LoweringError(
                        where, "loop whose step can be zero or change sign",
                        code="loop-step-not-constant")
                positive = (step_value > 0 if step_value is not None else
                            step_bounds.lo > 0)
                step_term = (ir.Lit(step_value)
                             if step_value is not None else step)
                # A step is a direction as much as a size. Counting down spans
                # start to stop, counting up spans stop to start, and the
                # iteration number is mapped by the same expression either way.
                span = (ir.Op("sub", stop, start) if positive
                        else ir.Op("sub", start, stop))
                stride = (step_term if positive else
                          ir.Op("neg", step_term))
                count = (span if step_value in {1, -1} else
                         ir.Op("fdiv",
                               ir.Op("add", span,
                                     ir.Op("sub", stride, ir.Lit(1))),
                               stride))
                # Fold source-proved or literal constants, not input/state
                # range bounds. Operand evaluation and its source events above
                # remain intact, as do both zero/nonzero loop goal sites.
                if isinstance(start, ir.Lit) and type(start.value) in (bool, int):
                    start_fixed = int(start.value)
                if isinstance(stop, ir.Lit) and type(stop.value) in (bool, int):
                    stop_fixed = int(stop.value)
                if start_fixed is not None and stop_fixed is not None and step_value is not None:
                    distance = (stop_fixed - start_fixed if positive
                                else start_fixed - stop_fixed)
                    stride_value = abs(step_value)
                    count = ir.Lit((distance + stride_value - 1) // stride_value)
                value = lambda j: ir.Op(
                    "add", start, ir.Op("mul", j, step_term))
            else:
                raise LoweringError(where, "range with too many arguments", code="range-arity")
            if backwards_sequence:
                reverse_index = lambda j: ir.Op(
                    "sub", ir.Op("sub", count, ir.Lit(1)), j)
                forward = value
                value = (reverse_index if forward is None else
                         lambda j, forward=forward: forward(reverse_index(j)))
            element = SCALAR
        elif lower_static_refs.is_reference(self, sequence_node):
            ref = lower_static_refs.lower_reference(self, sequence_node, preserve_collection=True)
            if not isinstance(ref, _StaticRef):
                raise LoweringError(
                    sequence_node, "a static iterable was reduced to a "
                                   "non-collection value",
                    code="static-collection-depth-unsupported")
            # Iterators keep the original immutable object, even when the
            # source name is rebound in the body or a later zip argument.
            select, count, dynamic_span = self._capture(ref).iterable(self, sequence_node)
            if backwards_sequence:
                value = lambda j: select(
                    ir.Op("sub", ir.Op("sub", count, ir.Lit(1)), j))
            else:
                value = select
            sequence_contract = self._contract_ast(sequence_node)
            element = (sequence_contract.element
                       if sequence_contract.is_sequence else None)
            if element is None and all(
                    isinstance(item, (str, bytes, dict)) for _active, item in ref.alternatives):
                element = SCALAR
        elif optional_sequence(self._contract_ast(sequence_node)):
            seq = self.expr(sequence_node)
            if self._iterator_binding_only_changes(node, sequence_node):
                seq = self._capture(seq)
            elif self._iterator_binding_may_change(node, sequence_node):
                raise LoweringError(sequence_node, 'optional sequence iteration needs a live object',
                                    code='optional-sequence-iterator-rebinding-unsupported')
            count = ir.Lit(seq.contract.exact_length)
            element = seq.contract.element
            value = (lambda j: seq.at(ir.Op('sub', ir.Op('sub', count, ir.Lit(1)), j))) \
                    if backwards_sequence else seq.at
        elif record_batch_payload(self._contract_ast(sequence_node)) is not None:
            seq = self.expr(sequence_node)
            if not isinstance(seq, _BatchValue):
                raise LoweringError(sequence_node, "record iterable has no batch carrier",
                                    code="source-plan-inconsistent")
            if self._iterator_binding_only_changes(node, sequence_node):
                seq = self._capture(seq)
            elif self._iterator_binding_may_change(node, sequence_node):
                raise LoweringError(
                    sequence_node, "record iteration with object rebinding needs a live iterator",
                    code="output-batch-iterator-rebinding-unsupported")
            element = record_batch_payload(seq.contract).element
            live_length = ir.Var(seq.builder.count)
            start_length = self._capture(live_length)
            count = start_length if backwards_sequence else ir.Lit(seq.builder.capacity)
            index = ((lambda j: ir.Op("sub", ir.Op("sub", start_length, ir.Lit(1)), j))
                     if backwards_sequence else (lambda j: j))
            available = lambda j: ir.Op(
                "land", ir.Op("ge", index(j), ir.Lit(0)), ir.Op("lt", index(j), live_length))
            value = lambda j: seq.record_at(self, index(j), sequence_node)
            dynamic_span = (0, seq.builder.capacity)
        else:
            seq = self.expr(sequence_node)
            sequence_contract = self._contract_ast(sequence_node)
            # Evaluate a constructed iterable once. Rebinding a source name
            # also cannot replace an existing iterator's object. When the
            # shared effect rules prove that object's contents stay unchanged,
            # a value capture preserves its identity without a mutable alias.
            if (not isinstance(seq, ir.Var)
                    or self._iterator_binding_only_changes(node, sequence_node)):
                seq = self._capture(seq)
            elif self._iterator_binding_may_change(node, sequence_node):
                saved = self._capture(seq)
                detached = self.storage.fresh()
                self.before(ir.Assign(detached, ir.Lit(False)))
                self.flow.ranges[detached] = ranges.Range(0, 1)
                self.flow.bounds[detached] = ranges.Bounds(0, 1)
                live_lengths = self._length_image(seq)
                saved_lengths = self._length_image(saved)
                binding = _IteratorBinding(
                    seq.name, saved, detached,
                    tuple((saved_lengths[path].name, value)
                          for path, value in live_lengths.items()))
            read_sequence = (seq if binding is None else
                             binding.read(seq, binding.saved))
            element = (sequence_contract.element
                       if sequence_contract.is_sequence else None)
            dynamic = self._dynamic_sequence(seq)
            if dynamic is not None:
                name, count, _capacity = dynamic
                if binding is not None:
                    saved_count = self._length_image(binding.saved)[()]
                    count = binding.read(count, saved_count)
                if self.storage.carrier(_BatchBuilder, name) is not None:
                    raise LoweringError(
                        where, "iterating transaction records inside the model",
                        code="output-batch-read-unsupported")
                contract = self.flow.contracts.get(name, UNKNOWN)
                if contract.is_sequence and contract.maximum is not None:
                    dynamic_span = (
                        max(0, contract.minimum),
                        min(_capacity, contract.maximum),
                    )
                else:
                    dynamic_span = (0, _capacity)
                if isinstance(node, ast.For) and sequence_contract.carrier == "list":
                    live_length = count
                    # A reverse iterator saves its starting index, not the
                    # length used to validate each later next() call.
                    start_length = self._capture(count)
                    # Forward next() advances against the object's live
                    # length, including newly appended elements. Capacity is
                    # only the finite representation envelope: append guards
                    # explicitly report an analysis limit if it is exceeded.
                    # A reverse iterator instead fixes its initial cursor.
                    count = (start_length if backwards_sequence else
                             ir.Lit(_capacity))
                    index = ((lambda j: ir.Op(
                        "sub", ir.Op("sub", start_length, ir.Lit(1)), j))
                        if backwards_sequence else (lambda j: j))
                    available = lambda j: ir.Op(
                        "land", ir.Op("ge", index(j), ir.Lit(0)),
                        ir.Op("lt", index(j), live_length))
                value = ((lambda j: ir.Sel(
                    read_sequence, ir.Op("sub", ir.Op("sub", count, ir.Lit(1)), j)))
                    if backwards_sequence else
                    (lambda j: ir.Sel(read_sequence, j)))
            else:
                length = self._length(sequence_node)
                if length is None:
                    raise LoweringError(
                        where, "loop over a sequence of unknown length",
                        code="loop-sequence-unknown-length")
                # Reversing takes the same cells in the other order, so it is
                # the same rule with the index mapped. One place, because the
                # iteration number is mapped through an expression here for
                # every iterable that is not `range` counting up by one.
                backwards = (isinstance(it, ast.Call)
                             and getattr(it.func, "id", None) == "reversed"
                             and self._spelling_is_builtin("reversed")
                             and len(it.args) == 1)
                count = ir.Lit(length)
                if backwards:
                    value = lambda j: ir.Sel(
                        read_sequence, ir.Op("sub", ir.Lit(length - 1), j))
                else:
                    value = lambda j: ir.Sel(read_sequence, j)

        # ``enumerate`` is a wrapper around any admitted finite iterable.  Its
        # first argument has already been evaluated above; evaluate ``start``
        # next, then pair the source element with the visible index.  Keeping
        # this outside the range/sequence split also covers enumerate(range()).
        if enumerate_start is not None:
            # Python captures the starting integer once, before the first
            # body entry; assigning its source variable later cannot change it.
            start = self._with_iterator_binding(
                binding, lambda: self._capture(
                    self._integer_context_expr(enumerate_start)))

        if dynamic_span is None:
            rng = ranges.trip_range(count, self.flow.bounds)
            if rng is None:
                scope = self.activation.scope
                context = self.activation.summary
                planned = (
                    None if self.sequence_plan is None or scope is None else
                    self.sequence_plan.iteration_span(
                        node, sequence_node, scope, context))
                if planned is None:
                    raise LoweringError(
                        where, "loop whose bound has no known range",
                        code="loop-bound-unbounded")
                rng = planned
            # ``range`` executes a natural number of iterations even when its
            # arithmetic count is negative.  The retained loop and its
            # encoding plan therefore share the clamped semantic trip span.
            span = (max(0, rng.lo), max(0, rng.hi))
        else:
            span = dynamic_span
        if available is not None:
            # Boolean carriers alone are usually [0, 1], even when the
            # occurrence bounds prove this first fetch succeeds. Preserve
            # that proof for the loop target's definite assignment. Later
            # body visits may shorten the list, so only the first is owed.
            first_is_due = ranges.assume(
                available(ir.Lit(0)), False, self.flow.bounds) is None
            span = (min(1, span[0]) if first_is_due else 0, span[1])
        if isinstance(node, ast.For) and self.sequence_plan is not None and self.activation.scope:
            context = self.activation.summary
            planned = self.sequence_plan.loop_span(node, self.activation.scope, context)
            if planned is not None and planned.hi == 0:
                # Capacity is storage, not a body visit. The source loop fact
                # includes iterator argument evaluation and all entry paths.
                span = (0, 0)
        self.source_loop_spans[id(node)] = span
        if enumerate_start is not None and span[1]:
            # Empty enumeration still evaluates start, but it never binds a
            # pair. Do not demand a type for an element that cannot exist.
            if element is None or not element.is_known:
                raise LoweringError(
                    sequence_node,
                    "enumerate over elements without a known value representation",
                    code="enumerate-element-unsupported")
            selected = value if value is not None else (lambda j: j)
            components = (SCALAR, element)
            element = ValueContract.positional("tuple", components)
            if element.components is None and not isinstance(selected(ir.Lit(0)), _StaticRef):
                value = lambda j, selected=selected, start=start: ir.Op(
                    "arraylit", ir.Op("add", start, j), selected(j))
            else:
                value = lambda j, selected=selected, start=start: self._positional_value(
                    (ir.Op("add", start, j), selected(j)), components, element)
        return _IterationPlan(
            count, span[1], value,
            UNKNOWN if element is None else element, available, binding)

    def _assign(self, node):
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            self._require_writable_target(target)
        resolver = getattr(self, "resolver", None)
        scope = self.activation.scope
        if resolver is not None and scope is not None:
            helper = resolver.call_alias_assignment(scope, node)
            if helper is not None:
                self._require_def_seen(node, helper.name, helper)
                return self._flush(ir.Skip())
        if len(targets) != 1:
            return self._assign_many(node)
        target = targets[0]
        if isinstance(target, ast.Name):
            if lower_ordered_scalars.is_local(self, target.id):
                return lower_ordered_scalars.bind(self, target, node.value)
            layout = self._binding_layout_contract(target.id)
            resolved = self._name(target.id)
            if optional_sequence(layout) or self.storage.carrier(OptionSequence, resolved) is not None:
                if self.storage.carrier(OptionSequence, resolved) is None:
                    self.storage.bind_carrier(resolved, OptionSequence.allocate(self, layout, target))
                value = self.expr(node.value)
                self.flow.contracts[resolved] = self.storage.require_carrier(OptionSequence, resolved).contract
                return self._flush(self.storage.require_carrier(OptionSequence, resolved).assign(
                    self, value, node.value))
        if isinstance(target, (ast.Tuple, ast.List)):
            return self._destructure(node, target)
        scalar_local = (self._direct_local_assignment(node, target)
                        and (binding := self._structured_input_binding(target.id))
                        is not None
                        and binding.value_type.kind in ("boolean", "integer")
                        and binding.leaf_bindings[0][1].indices == ())
        if (self._structured_root(target) in self.storage.structured_bindings
                or (self._structured_input_root(target)
                    in self.storage.structured_input_views and not scalar_local)):
            command = self._structured_assignment(target, node.value)
            if command is None:
                raise LoweringError(
                    target, "a declared structured assignment has no binding",
                    code="value-binding-lost")
            return self._flush(self._define_state_attribute(target, command))
        if (isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name) and target.value.id == "self"
                and target.attr in self.storage.state_products):
            physical = self.storage.state_name(target.attr)
            if self.storage.carrier(OptionSequence, physical) is not None:
                return self._flush(self._define_state_attribute(target,
                    self.storage.require_carrier(OptionSequence, physical).assign(
                        self, self.expr(node.value), node.value)))
            if self.storage.state_products[target.attr].contract.is_mapping:
                return self._flush(lower_mappings.replace(self, target, node.value))
            if self.storage.state_products[target.attr].contract.is_sequence:
                return self._flush(self._define_state_attribute(
                    target, self._set_batch_value(target.attr, node.value, target)))
            value = self.expr(node.value)
            actual = self._contract_for_value(node.value, value)
            return self._flush(lower_state.assign_product(
                self, target, value, node.value, actual))
        if isinstance(target, ast.Name) and isinstance(node.value, ast.List) \
                and self._grows(target.id):
            return self._flush(self._new_sequence(target.id, node.value))
        if (isinstance(target, ast.Name)
                and isinstance(node.value, ast.ListComp)
                and self._grows(target.id)
                and not _expression_reads_binding(node.value, target.id)):
            command = self._new_comprehension_sequence(
                target.id, node.value)
            # The builder needs its live length while the comprehension is
            # executing.  Once complete, its planned contract describes the
            # value now bound to the source name.
            target_name = self._name(target.id)
            planned = self._binding_contract(target.id, target)
            current = self.flow.contracts.get(target_name, UNKNOWN)
            if planned.is_sequence:
                if (not current.is_sequence
                        or not contract_contains(current, planned)):
                    raise LoweringError(
                        node.value, "the comprehension builder disagrees with "
                                    "its completed source value contract",
                        code="source-value-contract-disagrees")
                self.flow.contracts[target_name] = planned
            return self._flush(command)
        # A local may hold a lowering-only reference into a closed module ROM.
        # Establish that fact before the generic source-value contract sees a
        # sequence of records and mistakes an internal lookup table for a
        # transaction batch.  The reference itself never enters SolverIR; a
        # later complete selector path materialises only its scalar or flat
        # tuple payload.
        static_value = None
        preserve_reference = (isinstance(target, ast.Name)
                              and lower_static_refs.requires_reference(self, node.value))
        if isinstance(target, ast.Name) and (
                preserve_reference or lower_static_refs.collection_result(self, node.value)):
            static_value = lower_static_refs.lower_reference(
                self, node.value, preserve_collection=True,
                preserve_reference=preserve_reference)
            if isinstance(static_value, _StaticRef):
                actual = self._contract_ast(node.value)
                layout = self._binding_layout_contract(target.id)
                if (actual.carrier == layout.carrier == "tuple"
                        and actual.exact_length is not None
                        and actual.exact_length == layout.exact_length
                        and actual.element == layout.element == SCALAR
                        and static_value.alternatives
                        and all(lower_static_refs.payload_shape(value) is not None
                                for _active, value in static_value.alternatives)):
                    # A selected immutable numeric tuple has the same value
                    # layout as a computed tuple on another branch. Eliminate
                    # the lookup reference here, using its already evaluated
                    # guards; do not evaluate the key or helper a second time.
                    static_value = lower_static_refs.choose_payload(self, static_value.alternatives, node.value)
                else:
                    return static_value.assign(self, target, node.value)

        if isinstance(target, ast.Name):
            spec = self._sequence_spec(target.id)
            if spec is not None and spec.is_batch:
                return self._flush(self._set_batch_value(
                    target.id, node.value, target))
            source_contract = self._contract_ast(node.value)
            if (source_contract.is_sequence
                    and source_contract.element is not None
                    and source_contract.element.is_record):
                raise LoweringError(
                    node.value, "an ordinary sequence contains record values "
                                "but has no transaction-batch plan",
                    code="sequence-element-record-unsupported")

        if isinstance(target, ast.Name):
            if self._record_fields(target.id):
                return self._flush(
                    self._set_record_value(target.id, node.value))
            if self._contract_ast(node.value).is_record:
                product = self.expr(node.value)
                if not isinstance(product, RecordValue):
                    raise LoweringError(
                        node.value, "a record expression has no fieldwise "
                                    "lowered representation",
                        code="record-value-shape-mismatch")
                return self._flush(
                    self._bind_product(target.id, product, target))
            resolved = self._name(target.id)
            source_contract = self._contract_ast(node.value)
            builder = self.storage.carrier(_OptionBuilder, resolved)
            # A payload-only arm still writes the binding's shared Option
            # carrier when another arm assigns None. Waiting for that arm to
            # allocate the carrier leaves the payload undefined on this one.
            # This is storage selection, not a demand that every distinct
            # lifetime of the lexical name have the same source carrier.
            planned = self._binding_layout_contract(target.id)
            if (builder is not None or planned.is_option
                    or source_contract.is_none or source_contract.is_option):
                contract = (builder.contract if builder is not None else
                            planned if planned.is_option else
                            source_contract)
                if contract.is_none:
                    # No payload exists in this source activation. Evaluate
                    # the expression for effects, then reuse the existing
                    # finite None reference instead of inventing a payload
                    # type from a later/unreachable call.
                    value = self.expr(node.value)
                    if not isinstance(value, OptionValue):
                        raise LoweringError(node.value, 'a None-only expression lost its carrier',
                                            code='source-value-contract-disagrees')
                    return _StaticRef(((ir.Lit(True), None),)).assign(self, target, node.value)
                if not contract.is_option:
                    raise LoweringError(
                        node.value, "None reaches a binding without a finite "
                                    "payload contract",
                        code="optional-payload-contract-unresolved")
                value = self.expr(node.value)
                if isinstance(value, _StaticRef):
                    # A helper can return a finite collection-or-None tag.
                    # Keep that existing reference carrier; it is not an
                    # optional numeric/array payload to materialise in IR.
                    return value.assign(self, target, node.value)
                return self._flush(self._bind_option(
                    target.id, value, target, contract,
                    source=node.value))
        value = (static_value if static_value is not None else
                 self._local_value_expr(node.value, target.id)
                 if isinstance(target, ast.Name) else
                 self.expr(node.value))
        inherited_storage = self._storage_contract(value)
        if isinstance(value, MappingBuilder):
            raise LoweringError(node.value, "a persistent map is aliased through a local binding",
                                code="mapping-alias-unsupported")
        if isinstance(value, _StaticRef):
            if not isinstance(target, ast.Name):
                raise LoweringError(
                    target, "a finite static reference may be bound only to "
                            "a lexical local",
                    code="static-collection-escapes")
            return value.assign(self, target, node.value)
        if isinstance(target, ast.Subscript):
            return self._flush(self._store(
                target, value, node.value,
                actual_contract=self._contract_for_value(node.value, value)))
        if isinstance(value, OptionValue):
            raise LoweringError(
                target, "an optional value may be bound only to a local name",
                code="optional-state-unsupported")
        if isinstance(value, RecordValue):
            raise LoweringError(
                target, "a record may be bound only to a local value name",
                code="record-state-unsupported")
        else:
            self._split_scalar_local(node, target, value)
            name = self._written(target)
            if (isinstance(target, ast.Name)
                    and self._sequence_mutates(target.id)):
                self.storage.mutable_sequence_lengths.add(name)
            if isinstance(target, ast.Name):
                self.flow.static_values[name] = _STATIC_SCALAR
            actual_contract = self._contract_for_value(node.value, value)
            storage_contract = (
                self._binding_layout_contract(target.id)
                if isinstance(target, ast.Name) else
                self.storage.declaration_contracts.get(name, UNKNOWN))
            if (isinstance(inherited_storage, ValueContract)
                    and inherited_storage.is_sequence
                    and inherited_storage.physical_shape == self._shape(value)
                    and contract_contains(
                        inherited_storage, actual_contract)
                    and (not isinstance(storage_contract, ValueContract)
                         or not storage_contract.is_sequence
                         or (storage_contract.physical_shape !=
                             inherited_storage.physical_shape
                             and contract_contains(
                                 inherited_storage, storage_contract)))):
                # A local selected from nested dynamic state inherits the
                # container element's backing store even when every source
                # activation reaching this assignment has one exact logical
                # length.  Preserve that distinction so padding remains
                # unobservable and subsequent operations read the carried
                # length rather than the backing capacity. Inheritance must
                # contain the whole binding plan, not just this occurrence:
                # a shorter slice cannot shrink a local's stable backing store.
                storage_contract = inherited_storage
            value = self._coerce_sequence_storage(
                node.value, value, actual_contract, storage_contract)
            if (isinstance(storage_contract, ValueContract)
                    and storage_contract.is_sequence):
                self.storage.storage_contracts[name] = storage_contract
            else:
                self.storage.storage_contracts.pop(name, None)
            # Representation facts follow the value produced by lowering.
            # Usually that agrees with the source syntax; an inlined helper is
            # the important exception, because its call node has no shape of
            # its own while its returned temporary does.
            shape = self._shape(value)
            self.storage.require_shape(name, shape, node)
            self._remember_source_scalar_type(
                name,
                (None if shape else
                 self._source_scalar_type_for_value(node.value, value)))
            contract = self._remember_value_contract(
                name, node.value, value)
            carrier = contract.carrier if contract.is_sequence else None
            # A state array carries the shape it was declared with, so a step
            # that writes it a different one is refused here rather than
            # leaving the declaration to be wrong somewhere later.
            declared = self.storage.arrays.get(name)
            if declared and shape and shape != declared:
                raise LoweringError(
                    node, f"{name} was declared with shape {declared} and is "
                          f"assigned shape {shape}", code="shape-mismatch")
            if shape:
                self._rebind(name, node.value)
            if shape:
                self.flow.lengths[name] = shape
                if carrier is None:
                    self.flow.carriers.pop(name, None)
                else:
                    self.flow.carriers[name] = carrier
            else:
                self.flow.lengths.pop(name, None)
                self.flow.carriers.pop(name, None)
            planned = (self._binding_contract(target.id, target)
                       if isinstance(target, ast.Name) else
                       self.storage.declaration_contracts.get(name, UNKNOWN))
            layout_contract = (
                storage_contract
                if (isinstance(storage_contract, ValueContract)
                    and storage_contract.is_sequence) else
                planned if planned.is_sequence else contract)
            needs_length = bool(
                shape and tuple(lower_context.dynamic_length_specs(layout_contract)))
            length_cmd = None
            if needs_length:
                length_cmd = self._assign_length_layout(
                    name, value, actual_contract, layout_contract, target)
            elif name in self.storage.lengths and not self._sequence_mutates(
                    target.id if isinstance(target, ast.Name) else name):
                # A later scalar/fixed binding cannot retain an earlier
                # dynamic sequence's companion as its meaning.
                self.storage.retire_root_length(name)
            # What this name can hold from here on, so that a loop bound naming a
            # local has a range. `n = a - 1; for i in range(n)` did not lower
            # where `for i in range(a - 1)` did, because the analysis closed over
            # the state and the inputs and a local was neither: 21 models were
            # refused for writing the bound down before using it.
            #
            # Recorded where the assignment is lowered, since that is where the
            # value is known, and dropped where it is not: a name assigned
            # something this cannot bound has no bound, and keeping the one from
            # before the assignment would be answering about the wrong value.
            self._record_range(name, value)
            cmd = ir.Assign(name, value)
            if length_cmd is not None:
                cmd = ir.Seq(cmd, length_cmd)
            cmd = self._preserve_iterator_bindings(name, node.value, cmd)
        return self._flush(self._define_state_attribute(target, cmd))

    def _assign_many(self, node):
        """Lower ``a = b = value`` with Python's one-evaluation rule.

        Chained assignment first evaluates the right-hand side once and then
        binds the targets from left to right.  The admitted form keeps targets
        to plain names or model fields: a subscript or destructuring target can
        itself execute source operations while it is being assigned, which
        needs the richer target-order relation rather than this scalar rule.

        Arrays are refused here as well.  Python would bind every target to one
        mutable object while SolverIR copies arrays by value.  The two agree
        only until one alias is mutated, and the IR deliberately carries no
        run-time alias topology.  Scalar chained assignment has no such gap.
        """
        if not all(isinstance(target, (ast.Name, ast.Attribute))
                   for target in node.targets):
            raise LoweringError(
                node, "chained assignment with a compound target",
                code="multiple-assignment-target-form")
        for target in node.targets:
            self._require_plain_rebinding(target, "a chained assignment")
        value = (self._integer_context_expr(node.value)
                 if all(isinstance(target, ast.Name)
                        and self._local_has_only_truth_uses(target.id)
                        for target in node.targets)
                 and self._contract_ast(node.value).kind == "scalar"
                 else self.expr(node.value))
        shape = self._shape(value)
        contract = self._contract_for_value(node.value, value)
        if isinstance(value, OptionValue):
            if (not all(isinstance(target, ast.Name) for target in node.targets)
                    or (contract.is_option and contract.payload != SCALAR)):
                raise LoweringError(
                    node, "chained optional assignment requires scalar local payloads",
                    code="multiple-assignment-value-form")
            value = self._capture(value)
            cmd = ir.Skip()
            for target in node.targets:
                planned = self._binding_contract(target.id)
                if not planned.is_option:
                    raise LoweringError(
                        target, "a chained optional binding has no payload contract",
                        code="optional-payload-contract-unresolved")
                cmd = ir.Seq(cmd, self._bind_option(
                    target.id, value, target, planned, source=node.value))
            return self._flush(cmd)
        if shape:
            raise LoweringError(
                node, "chained assignment of one mutable array to several names",
                code="multiple-array-assignment")
        temporary = self.storage.fresh()
        scalar_kind = self._source_scalar_type_for_value(node.value, value)
        self._remember_source_scalar_type(temporary, scalar_kind)
        cmd = ir.Assign(temporary, value)
        self._record_range(temporary, value)
        for target in node.targets:
            name = self._written(target)
            self.storage.require_shape(name, (), target)
            self._remember_source_scalar_type(
                name, scalar_kind)
            self.flow.lengths.pop(name, None)
            self.flow.carriers.pop(name, None)
            self.flow.contracts[name] = contract
            self._record_range(name, ir.Var(temporary))
            cmd = ir.Seq(cmd, self._define_state_attribute(
                target, ir.Assign(name, ir.Var(temporary))))
        return self._flush(cmd)

    def _store(self, target, value, source, *, actual_contract=None):
        """Write ``value`` through a subscript, however deeply it nests.

        Arrays are values, so replacing an element of an inner one is building
        a new inner array and writing that back. Writing through two subscripts
        is therefore writing through one, twice, and needs no rule of its own.
        """
        resolved = self._source_binding_name(target.value)
        if self.storage.carrier(OptionSequence, resolved) is not None:
            return self.storage.require_carrier(OptionSequence, resolved).store(self, target, value, source)
        mapping = lower_mappings.receiver(self, target.value)
        if mapping is not None:
            return mapping.store(self, target, value, source)
        if (isinstance(target.value, ast.Name)
                and self._is_record_builder(target.value.id)):
            return self._record_builder_store(
                target, value, source, actual_contract=actual_contract)
        batch_record_store = self._batch_record_store(
            target, value, source, actual_contract=actual_contract)
        if batch_record_store is not None:
            return batch_record_store
        batch_store = self._batch_field_store(
            target, value, source, actual_contract=actual_contract)
        if batch_store is not None:
            return batch_store
        if any(isinstance(part.slice, ast.Slice)
               for part in self._subscript_path(target)):
            return self._store_slice(
                target, value, source, actual_contract=actual_contract)
        path = []
        root = target
        while isinstance(root, ast.Subscript):
            if isinstance(root.slice, ast.Slice):
                raise LoweringError(
                    root, "assignment through a slice",
                    code="slice-assignment-unsupported")
            path.append(root)
            root = root.value
        path.reverse()
        if not isinstance(root, (ast.Name, ast.Attribute)):
            raise LoweringError(
                root, "subscript assignment through a temporary object",
                code="assignment-target-unsupported")
        if (isinstance(root, ast.Name)
                and self._product_for_name(root.id, root) is not None):
            raise LoweringError(
                target, "mutation of a fixed-schema record is outside its "
                        "fieldwise value representation",
                code="record-mutation-unsupported")
        if (isinstance(root, ast.Attribute)
                and isinstance(root.value, ast.Name) and root.value.id == "self"
                and root.attr in self.storage.state_products
                and self.storage.state_products[root.attr].contract.is_record):
            raise LoweringError(
                target, "persistent record fields require a fieldwise store; "
                        "only whole-record replacement is represented here",
                code="record-mutation-unsupported")
        if (isinstance(root, ast.Name)
                and isinstance(self.flow.static_values.get(self._name(root.id)),
                               _StaticRef)):
            raise LoweringError(
                root, "mutation through a compile-time collection reference",
                code="static-collection-mutated")
        location_nodes = [root] + [part.slice for part in path]
        if any(self._effectful(part) for part in location_nodes):
            raise LoweringError(
                target, "a subscript target whose location evaluation has "
                        "source-visible effects",
                code="subscript-target-effectful")
        lowered = self._in_order(*location_nodes)
        array, raw_indices = lowered[0], lowered[1:]
        name = self._written(root)
        frames = []
        current = array
        for offset, (part, raw) in enumerate(zip(path, raw_indices)):
            index = (self._sequence_index(part, name, raw)
                     if offset == 0 and name in self.storage.lengths
                     else self._index(part, current, raw))
            frames.append((current, index))
            if offset + 1 < len(path):
                current = ir.Sel(current, index)
        if (isinstance(root, ast.Attribute)
                and isinstance(root.value, ast.Name)
                and root.value.id == "self"):
            container_contract = self.storage.declaration_contracts.get(
                name, self._contract_ast(root))
        elif isinstance(root, ast.Name):
            planned_container = self._binding_contract(root.id)
            container_contract = (
                planned_container if planned_container is not UNKNOWN else
                self._contract_ast(root))
        else:
            container_contract = self._contract_ast(root)
        expected_contract = container_contract
        for _part in path:
            if (not expected_contract.is_sequence
                    or expected_contract.element is None):
                raise LoweringError(
                    target, "the element contract of a subscript assignment "
                            "is not known",
                    code="store-value-contract-unknown")
            if expected_contract.carrier not in {"list", "bytearray"}:
                raise LoweringError(
                    target, "subscript assignment through a sequence not "
                            "proved to be a mutable list",
                    code="store-target-immutable")
            if expected_contract.carrier == "bytearray":
                lower_bytes.check_store(self, target, value)
                value = ir.Op("toint", value)
            expected_contract = expected_contract.element
        if actual_contract is None:
            actual_contract = self._contract_for_value(source, value)
        value = self._coerce_sequence_storage(
            source, value, actual_contract, expected_contract)
        expected = self._shape(current)[1:]
        actual = self._shape(value)
        if actual != expected:
            raise LoweringError(
                target, f"subscript assignment stores shape {actual} into "
                        f"an element of shape {expected}",
                code="store-value-shape-mismatch")
        if not contract_contains(expected_contract, actual_contract):
            raise LoweringError(
                source, "the value stored through a subscript disagrees with "
                        "the containing sequence's recursive value contract",
                code="store-value-contract-mismatch")
        self._check_not_shared(name, target, "assigning through a subscript of")
        updated = value
        for array_value, index in reversed(frames):
            updated = ir.Op("store", array_value, index, updated)
        # Flow-sensitive branch pruning consumes the same abstract range map
        # as ordinary assignments.  A subscript assignment is an assignment
        # to its root array too; leaving the old range here can make a later
        # selected value look constant and prune a feasible branch.
        self._record_range(name, updated)
        metadata = self._store_nested_lengths(
            name, tuple(index for _array, index in frames), value,
            actual_contract, container_contract, source)
        return ir.Seq(ir.Assign(name, updated), metadata)

    @staticmethod
    def _subscript_path(target):
        """Subscript nodes from the outer target down to its root."""
        path = []
        current = target
        while isinstance(current, ast.Subscript):
            path.append(current)
            current = current.value
        return tuple(path)

    def _store_slice(self, target, value, source, *, actual_contract=None):
        """Replace one proved fixed-width list slice without changing length."""
        if not isinstance(target.slice, ast.Slice):
            raise LoweringError(
                target, "assignment through a value produced by a slice",
                code="slice-assignment-unsupported")

        path = []
        root = target.value
        while isinstance(root, ast.Subscript):
            if isinstance(root.slice, ast.Slice):
                raise LoweringError(
                    root, "assignment through a value produced by a slice",
                    code="slice-assignment-unsupported")
            path.append(root)
            root = root.value
        path.reverse()
        if not isinstance(root, (ast.Name, ast.Attribute)):
            raise LoweringError(
                root, "slice assignment through a temporary object",
                code="assignment-target-unsupported")

        location_nodes = [root] + [part.slice for part in path]
        location_nodes.extend(part for part in (
            target.slice.lower, target.slice.upper, target.slice.step)
                              if part is not None)
        if any(self._effectful(part) for part in location_nodes):
            raise LoweringError(
                target, "a slice target whose location evaluation has "
                        "source-visible effects",
                code="subscript-target-effectful")

        lowered = self._in_order(root, *(part.slice for part in path))
        current, raw_indices = lowered[0], lowered[1:]
        name = self._written(root)
        frames = []
        for offset, (part, raw) in enumerate(zip(path, raw_indices)):
            index = (self._sequence_index(part, name, raw)
                     if offset == 0 and name in self.storage.lengths
                     else self._index(part, current, raw))
            frames.append((current, index))
            current = ir.Sel(current, index)

        expected = self._contract_ast(root)
        for _part in path:
            if (not expected.is_sequence or expected.element is None
                    or expected.carrier != "list"):
                raise LoweringError(
                    target, "slice assignment through a value not proved to "
                            "be a mutable list",
                    code="store-target-immutable")
            expected = expected.element
        if not expected.is_sequence or expected.carrier != "list" \
                or expected.exact_length is None:
            raise LoweringError(
                target, "slice assignment needs a fixed-length mutable list",
                code="slice-assignment-unsupported")

        shape = self._shape(current)
        length = expected.exact_length
        if not shape or length > shape[0]:
            raise LoweringError(
                target, "a slice target disagrees with its physical capacity",
                code="sequence-plan-inconsistent")
        start, width = self._slice_span(target, length)

        if actual_contract is None:
            actual_contract = self._contract_for_value(source, value)
        actual_shape = self._shape(value)
        actual_length = (actual_contract.exact_length
                         if actual_contract.is_sequence else None)
        if actual_length != width:
            raise LoweringError(
                source, "slice assignment changes the target list length",
                code="slice-assignment-length-mismatch")
        if (not actual_shape or actual_shape[0] < width
                or actual_shape[1:] != shape[1:]):
            raise LoweringError(
                source, "slice assignment stores a different element shape",
                code="store-value-shape-mismatch")
        if (width and (expected.element is None
                       or actual_contract.element is None
                       or not contract_contains(
                           expected.element, actual_contract.element))):
            raise LoweringError(
                source, "the slice value disagrees with the target elements",
                code="store-value-contract-mismatch")

        self._check_not_shared(name, target, "assigning through a slice of")
        updated = current
        for index in range(width):
            updated = ir.Op(
                "store", updated,
                ir.Op("add", start, ir.Lit(index)),
                ir.Sel(value, ir.Lit(index)))
        for array_value, index in reversed(frames):
            updated = ir.Op("store", array_value, index, updated)
        self._record_range(name, updated)
        return ir.Assign(name, updated)

    def _written(self, target):
        """Resolve source locals and persistent attributes in separate scopes."""
        name = _target_name(target)
        if isinstance(target, ast.Attribute) and name in self.storage.state_products:
            builder = self.storage.carrier(_OptionBuilder, self.storage.state_name(name))
            if builder is not None and type(builder.payload) is str:
                return builder.payload
            raise LoweringError(target, "mutation needs a represented scalar or sequence payload",
                                code="optional-payload-mutation-unsupported")
        return (self._name(name) if isinstance(target, ast.Name)
                else self.storage.state_name(name))

    def _require_writable_target(self, target):
        """Enforce the source contract before a target reaches any IR rule.

        The internal ``for`` counter is not a source variable. Scalar source
        targets are ordinary locals, rebound from the iterator on every trip.
        Writes through an iterable's mutable elements still require object
        alias semantics; the value-array representation cannot model those.
        Public IR inputs are read-only too, but source parameters are ordinary
        Python locals: ``lower_procedure`` copies each one into a private local
        before the body runs.  Rebinding a source parameter therefore never
        writes the boundary input.  Compound targets are checked at their
        roots so a mutable element cannot bypass that rule via a subscript.
        """
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._require_writable_target(element)
            return
        if isinstance(target, ast.Starred):
            self._require_writable_target(target.value)
            return
        root = target
        while isinstance(root, (ast.Subscript, ast.Attribute)):
            if isinstance(root, ast.Attribute):
                if not (isinstance(root.value, ast.Name)
                        and root.value.id == "self"):
                    raise LoweringError(
                        root, "attribute assignment requires the model instance",
                        code="assignment-target-unsupported")
                if "self" in self.activation.substitutions or self._name_is_local("self"):
                    raise LoweringError(
                        root, "attribute assignment through a rebound self",
                        code="self-rebound")
            root = root.value
        if not isinstance(root, ast.Name):
            return
        resolved = self._name(root.id)
        if self.loop is not None and self.loop.contains_index(resolved):
            if (isinstance(target, ast.Name)
                    and self.storage.shapes.get(resolved) == ()):
                return
            raise LoweringError(
                target, f"writing the structured loop element {root.id} "
                        "requires object alias semantics",
                code="loop-index-assignment")

    def _require_plain_rebinding(self, target, operation):
        """Refuse a binder that bypasses a name's representation protocol.

        Dynamic sequences carry a separate logical length; transaction
        batches and records carry parallel field buffers.  Ordinary
        assignment has representation-aware rules for those values, but a
        loop target, destructuring target or chained target writes only the
        visible IR name.  Letting one do so retains stale hidden state.  Until
        those binders implement the same protocol, they are outside the
        admitted source subset.
        """
        if not isinstance(target, ast.Name):
            return
        source_name = target.id
        name = self._name(source_name)
        spec = self._sequence_spec(source_name)
        represented = (
            name in self.storage.lengths
            or self.storage.carrier(_BatchBuilder, name) is not None
            or self.storage.carrier(_RecordBuilder, name) is not None
            or bool(self._record_fields(source_name))
            or (spec is not None and (spec.is_growing or spec.is_batch)))
        structured = self._structured_input_binding(source_name)
        if structured is not None:
            leaves = structured.leaf_bindings
            plain_scalar = (
                structured.value_type.kind
                in ("boolean", "integer", "enumeration")
                and len(leaves) == 1 and leaves[0][0] == ()
                and leaves[0][1].indices == ())
            represented = represented or not plain_scalar
        if represented:
            raise LoweringError(
                target, f"{operation} rebinds {source_name}, whose finite "
                        "representation has hidden length or field state",
                code="represented-name-rebound")

    def _destructure(self, node, target):
        """Lower flat unpacking with Python's two-phase assignment order.

        The complete right side is evaluated and snapshotted before any
        target is visited.  This includes recursive record products: their
        leaves are captured before a target can replace a record in the same
        parallel batch carrier.  Targets are then evaluated and written from
        left to right.  Nested and starred patterns require a recursive unpack
        relation and remain outside this rule.
        """
        value = node.value
        scalar_types = None
        if not all(isinstance(item, (ast.Name, ast.Attribute, ast.Subscript))
                   for item in target.elts):
            raise LoweringError(
                target, "nested or starred destructuring target",
                code="destructuring-target-form")
        for item in target.elts:
            self._require_writable_target(item)
            self._require_plain_rebinding(item, "a destructuring target")
        if isinstance(value, (ast.Tuple, ast.List)):
            if len(value.elts) != len(target.elts):
                raise LoweringError(node, "destructuring of a different length", code="destructuring-length")
            sources = self._in_order(*value.elts)
            contracts = [self._contract_for_value(source, lowered)
                         for source, lowered in zip(value.elts, sources)]
            scalar_types = [
                None if self._shape(lowered) else
                self._source_scalar_type_for_value(source, lowered)
                for source, lowered in zip(value.elts, sources)
            ]
        else:
            # Anything else has to be a sequence value, and reading its
            # elements is the same rule as reading any array's, so a call that
            # returns several values needs no rule of its own.
            seq = self.expr(value)
            if isinstance(seq, _PositionalValue):
                if len(seq.items) != len(target.elts):
                    raise LoweringError(
                        value, f"destructuring {len(seq.items)} values into "
                               f"{len(target.elts)} targets",
                        code="destructuring-length")
                sources = list(seq.items)
                contracts = list(seq.contracts)
                scalar_types = list(seq.scalar_types)
                sequence_contract = seq.contract
            else:
                sequence_contract = self._contract_for_value(value, seq)
            if not sequence_contract.is_sequence:
                raise LoweringError(
                    value, "destructuring a value not proved to be a sequence",
                    code="destructuring-source-not-sequence")
            length = sequence_contract.exact_length
            if length is None:
                raise LoweringError(
                    value, "destructuring a sequence whose logical length is "
                           "decided at run time",
                    code="destructuring-length-not-static")
            if length != len(target.elts):
                raise LoweringError(
                    value, f"destructuring {length} values into "
                           f"{len(target.elts)} targets",
                    code="destructuring-length")
            if not isinstance(seq, _PositionalValue):
                sources = [ir.Sel(seq, ir.Lit(j))
                           for j in range(len(target.elts))]
                contracts = [sequence_contract.element or UNKNOWN
                             for _ in target.elts]
                scalar_types = [None for _ in target.elts]
        if scalar_types is None:
            scalar_types = [None for _ in target.elts]
        # `_capture` is the one value-snapshot protocol used by expression
        # order elsewhere.  Applying it to every source before flushing any
        # target gives destructuring Python's simultaneous RHS semantics for
        # scalars, arrays, records and options alike.
        captured = [self._capture(source) for source in sources]
        cmd = self._flush(ir.Skip())
        for dst, tmp, contract, scalar_kind in zip(
                target.elts, captured, contracts, scalar_types):
            product_assignment = lower_state.assign_product(
                self, dst, tmp, value, contract)
            if product_assignment is not None:
                cmd = ir.Seq(cmd, self._flush(product_assignment))
                continue
            if isinstance(tmp, _StaticRef):
                one = self._structured_assignment(dst, value, reference=tmp)
                if one is not None:
                    one = self._flush(one)
                elif isinstance(dst, ast.Name):
                    one = tmp.assign(self, dst, value)
                else:
                    raise LoweringError(dst, "a finite reference has no target representation",
                                        code="static-collection-escapes")
                cmd = one if cmd is None else ir.Seq(cmd, one)
                continue
            if isinstance(dst, ast.Subscript):
                one = self._flush(self._store(
                    dst, tmp, value, actual_contract=contract))
                cmd = one if cmd is None else ir.Seq(cmd, one)
                continue
            if isinstance(tmp, RecordValue):
                if not isinstance(dst, ast.Name):
                    raise LoweringError(
                        dst, "a record destructuring value is assigned to "
                             "persistent model state",
                        code="record-state-unsupported")
                one = self._flush(self._bind_product(
                    dst.id, tmp, dst))
                cmd = one if cmd is None else ir.Seq(cmd, one)
                continue
            if isinstance(tmp, OptionValue):
                if not isinstance(dst, ast.Name):
                    raise LoweringError(
                        dst, "an optional destructuring value is assigned to "
                             "persistent model state",
                        code="optional-state-unsupported")
                one = self._flush(self._bind_option(
                    dst.id, tmp, dst, contract, source=value, actual=contract))
                cmd = one if cmd is None else ir.Seq(cmd, one)
                continue
            if isinstance(tmp, _BatchValue):
                raise LoweringError(
                    dst, "a destructuring target rebinds a represented "
                         "transaction batch",
                    code="represented-name-rebound")
            written = self._written(dst)
            shape = self._shape(tmp)
            self.storage.require_shape(written, shape, dst)
            self._remember_source_scalar_type(written, scalar_kind)
            self.flow.contracts[written] = contract
            declared = self.storage.arrays.get(written)
            if declared and shape and shape != declared:
                raise LoweringError(
                    dst, f"{written} was declared with shape {declared} and "
                         f"is assigned shape {shape}", code="shape-mismatch")
            if shape:
                self.flow.lengths[written] = shape
                if contract.is_sequence and contract.carrier is not None:
                    self.flow.carriers[written] = contract.carrier
                else:
                    self.flow.carriers.pop(written, None)
            else:
                self.flow.lengths.pop(written, None)
                self.flow.carriers.pop(written, None)
            self._record_range(written, tmp)
            one = self._preserve_iterator_bindings(
                written, None, ir.Assign(written, tmp))
            one = self._define_state_attribute(dst, one)
            cmd = one if cmd is None else ir.Seq(cmd, one)
        return cmd if cmd is not None else ir.Skip()

    def _assert(self, node):
        """Evaluate the test once, then the message only on the failing path.

        A message expression can itself fail; the following AssertionError is
        absorbed in that case. Only the successful test has a normal successor,
        so message-side writes cannot pollute that successor's range facts.
        """
        self.reached.add(id(node))
        before, predicate = self._atom_of(node.test)
        before, guard = self._stable_guard(before, predicate)
        entering = self._flow_facts()
        success = self._assumed_source_flow_facts(entering, node.test, predicate, True)
        failure = self._assumed_source_flow_facts(entering, node.test, predicate, False)

        def fail():
            if failure is None:
                self.before(self._false_goal_terms(() if node.msg is None else (node.msg,)))
            else:
                self._set_flow_facts(failure)
                # Literal messages have no effects and are not part of the
                # error-code/occurrence contract. No string carrier is needed.
                if node.msg is not None and not isinstance(node.msg, ast.Constant):
                    self.expr(node.msg)
            self._guard(ir.Lit(True), self._safety_goal(node, "assertionerror"),
                        "AssertionError", node)
            return ir.Lit(0)

        # Both term and graph lowering keep this region behind the test.
        failing, _ = self._scoped(fail)
        self._set_flow_facts(success if success is not None else entering)
        command = ir.If(guard, ir.Skip(), failing)
        return (command if before is None else ir.Seq(before, command),
                {NORMAL} if success is not None else set())

    def _if(self, node):
        """A conditional marks the outcome it takes, so the branch goals of
        the enumeration have a producer in the lowered program."""
        before, predicate = self._atom_of(node.test)
        before, guard = self._stable_guard(before, predicate)
        entering = self._flow_facts()
        then_entry = self._assumed_source_flow_facts(
            entering, node.test, predicate, True)
        if then_entry is None:
            # This arm has no source execution under the facts at this point.
            # Keep every declared goal in the formula as a false term, but do
            # not lower operations Python cannot reach (and therefore cannot
            # owe representation facts or failure guards).
            then = self._false_goal_terms(node.body)
            then_ways = set()
            then_facts = entering
        else:
            self._set_flow_facts(then_entry)
            then, then_ways = self.body(node.body)
            then_facts = self._flow_facts()
        else_entry = self._assumed_source_flow_facts(
            entering, node.test, predicate, False)
        if else_entry is None:
            orelse = self._false_goal_terms(node.orelse)
            else_ways = set()
            else_facts = entering
        else:
            self._set_flow_facts(else_entry)
            if node.orelse:
                orelse, else_ways = self.body(node.orelse)
                else_facts = self._flow_facts()
            else:
                # An absent else falls through when that outcome is feasible.
                orelse, else_ways = ir.Skip(), {NORMAL}
                else_facts = self._flow_facts()
        live_facts = []
        if then_entry is not None and NORMAL in then_ways:
            live_facts.append((guard, then_facts))
        if else_entry is not None and NORMAL in else_ways:
            live_facts.append((ir.Op("not", guard), else_facts))
        self._set_flow_facts(
            self._joined_branch_flow_facts(guard, live_facts))
        gt = self._site_goal(node.test, True)
        gf = self._site_goal(node.test, False)
        if gt:
            then = ir.Seq(ir.Mark(gt), then)
        if gf:
            orelse = ir.Seq(ir.Mark(gf), orelse)
        out = ir.If(guard, then, orelse)
        return (out if before is None else ir.Seq(before, out),
                then_ways | else_ways)


def lower_expression(source):
    """Lower a single expression, for tests and for inspecting a rule."""
    tree = ast.parse(source, mode="eval")
    low = _Lowerer([])
    return low.expr(tree.body), low.activation.hoisted


def lower_procedure(source, file="behavior.py", method="process",
                    outputs=None, input_widths=None, state_ranges=None,
                    lowerer=None, read_initial=None, signature=None,
                    value_types=None, dynamic_state=(), bound=None,
                    boundary_initial=None, construction=None,
                    _analysis_cache=None, cls=None, operation_goals=(), source_rules=EMPTY_SOURCE_RULES):
    """Lower one model step using the shared source, type and state contracts.

    Production uses ``GraphLowerer``: control flow stays explicit until
    ``cfg.if_convert`` constructs the predicated body. The term lowerer is a
    secondary control-flow regression oracle. Both share expression lowering,
    so their agreement cannot replace the independent native-source audit.
    """
    if lowerer is None:
        # Imported here because `lower_graph` imports this module, and a subclass cannot
        # be named by its base at module level.
        from .lower_graph import GraphLowerer

        lowerer = GraphLowerer
    # Source parsing, declaration evaluation, representation planning and
    # value lowering are one analysis of one procedure.  One aggregate budget
    # crosses all four stages; a wrapper must not reset the capability merely
    # because the next question is asked through another module.
    source_value_budget = SourceValueBudget()
    entry_plan = lower_prepare.prepare_lowering(
        source, file, method, outputs=outputs, input_widths=input_widths,
        state_ranges=state_ranges, read_initial=read_initial, signature=signature,
        value_types=value_types, dynamic_state=dynamic_state, bound=bound,
        boundary_initial=boundary_initial, construction=construction,
        _analysis_cache=_analysis_cache, cls=cls, operation_goals=operation_goals,
        source_rules=source_rules, source_value_budget=source_value_budget)
    prepared = entry_plan.source
    declarations, planned = entry_plan.declarations, entry_plan.representation
    model = prepared.model
    tree, fn = model.tree, model.step
    resolver, goal_plan = prepared.resolver, prepared.goal_plan
    constants, construction = prepared.constants, prepared.construction
    state_names = declarations.state_names
    boundary_initial = declarations.boundary_initial
    fixed = entry_plan.fixed
    params, outputs = planned.params, planned.outputs
    low = (lowerer or _Lowerer)(
        goal_plan.coverage_sites, _approved_primitives(tree), None,
        constants, planned.env,
        {state_names.get(name, name): shape for name, shape in planned.model_arrays.items()},
        resolver.tables,
        {}, resolver.model_consts,
        fixed=fixed, file=file, lib_named=resolver.lib_consts,
        sequence_plan=planned.sequence_plan,
        source_value_budget=source_value_budget,
        reserved_names=entry_plan.reserved_names)
    inherited = lower_state.bind_entry(
        low, prepared, declarations, planned,
        fixed=fixed, static_fields=entry_plan.static_fields, state_ranges=state_ranges)
    low.resolver = resolver
    for owner in [fn] + [m for m in (model.reset, model.constructor)
                         if m is not None]:
        if low.resolver.scope_rebinds_self(owner):
            raise LoweringError(
                owner, f"{owner.name} rebinds self, so nothing in it may "
                       f"be read as the model", code="self-rebound")
    low.activation.scope = fn
    low.shadowed = frozenset(module_bound_names(tree))
    output_entry = lower_returns.initialize_outputs(
        low, prepared, planned, signature=signature, fixed=fixed)
    count = output_entry.count
    output_vars = output_entry.output_vars
    attributes = output_entry.attributes
    entry = output_entry.entry
    # A closed module sequence is a constant ROM of this procedure.  Put one
    # copy at the boundary rather than expanding one conditional per cell at
    # every read.  Dictionaries and nonuniform rows retain the guarded finite
    # collection representation, which does not require one nested IR type.
    rom_initializers = []
    for name, value in sorted(low.tables.items()):
        if not isinstance(value, tuple):
            continue
        literal = low._sequence_table_expr(value)
        if literal is None:
            continue
        local = low.storage.fresh()
        low.table_arrays[name] = local
        low.flow.lengths[local] = low._shape(literal)
        rom_initializers.append(ir.Assign(local, literal))
    for initializer in reversed(rom_initializers):
        entry = ir.Seq(initializer, entry)
    entry, root_renames = lower_state.bind_input_views(
        low, fn, params, outputs, attributes, entry)
    low.activation.renames = root_renames
    try:
        # The entry's assignments go in first either way. A lowerer that builds
        # blocks has them in its first block; one that builds a term has them at
        # the front of it, and `as_command` is where the two meet.
        if hasattr(low, "as_command"):
            low.emit(entry)
            low.body(fn.body)
        else:
            body = ir.Seq(entry, low.body(fn.body)[0])
        reachable_bodies = [fn] + list(low.inlined)
        uncalled_captures = {}
        for owner in reachable_bodies:
            for node in resolver.execution_nodes(owner):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                    continue
                helper = resolver.call_alias_assignment(resolver.scope_of(node), node)
                if helper is not None and helper not in reachable_bodies:
                    uncalled_captures[id(helper)] = helper
        # Every admitted invocation of these nonescaping captures would have
        # inlined its body. Retain uncalled goals as false at the lowerer's
        # normal pruning boundary, before freezing its graph and final term.
        roots = tuple(uncalled_captures.values())
        if hasattr(low, "as_command"):
            low._account_pruned_goals(roots)
            body = low.as_command()
        elif roots:
            body = ir.Seq(body, low._false_goal_terms(roots))
    except _BudgetExceeded:
        raise LoweringError(
            fn,
            f"step larger than {SEMANTIC_IR_NODES} semantic IR nodes",
            code="semantic-ir-budget-exceeded") \
            from None
    # And the step itself, which is what the budget is a budget on. `_spend` is an
    # early stop during construction, charged where a comprehension expands so
    # that a term nothing could hold is not built before it can be measured; it is
    # not the budget, and while it was the only charge the budget was a gate with
    # nothing behind it. The measurement is here, once, on what was built.
    spent = ir.cost(body)
    if spent > SEMANTIC_IR_NODES:
        raise LoweringError(
            fn, f"step of {spent} retained semantic commands, over the "
                f"budget of {SEMANTIC_IR_NODES}",
            code="semantic-ir-budget-exceeded")

    contexts = lower_context.collect_contexts(
        low, prepared, declarations, body=body, reachable_bodies=reachable_bodies,
        fixed=fixed, inherited=inherited, output_vars=output_vars)
    state = contexts.state
    inputs = contexts.inputs
    locals_ = contexts.locals_
    active_structured = contexts.active_structured
    active_products = contexts.active_products
    discharged = lower_checks.check_goals(fn, goal_plan, low, body)
    initial, declared_types = lower_context.initial_image(
        low, prepared, declarations, planned, contexts)
    proc = ir.Procedure(state, inputs, locals_, outputs, body,
                        types=declared_types,
                        count=count,
                        initial=initial, output_vars=output_vars,
                        provenance=low.provenance,
                        signature=signature,
                        output_capacity=low.output_capacity,
                        value_bindings=(
                            tuple(("state", active_structured[name])
                                  for name in sorted(active_structured))
                            + tuple(("local", low.storage.structured_input_views[name])
                                    for name in sorted(
                                        low.storage.structured_input_views))))
    # Missing entry values are classified only after the final representation
    # types and CFG are available.  Until then they are neither zero nor a
    # source-state claim.
    proc.guessed_initial = ()
    # What the audit above accepted, carried so a reader can see why a declared goal is
    # absent from the step rather than having to trust that it was checked.
    proc.discharged = discharged
    # The graph the body was flattened from, where there was one. Carried so
    # that the questions about control flow are asked of the structure that has
    # them rather than of one recovered from the flattening.
    ir._bind_control_flow(proc, getattr(low, "control_flow", None))
    lower_checks.finalize_entry_state(
        proc, model, low, fn=fn, initial=initial,
        active_products=active_products, state_names=state_names, inherited=inherited)
    lower_checks.check_array_state_types(proc, model)
    lower_checks.check_step_aliases(
        proc, low, prepared, declarations, planned, contexts,
        root_renames=root_renames, source_value_budget=source_value_budget,
        analysis_cache=_analysis_cache)
    lower_checks.check_construction_sharing(
        proc, model, construction, state_names=state_names,
        active_structured=active_structured, active_products=active_products,
        boundary_initial=boundary_initial)
    lower_context.bind_source_state(
        proc, low, prepared, declarations, planned, contexts)
    try:
        bind_procedure_contract(proc, proc.types)
    except ProcedureContractError as exc:
        raise LoweringError(
            fn,
            f"the lowered procedure has no complete semantic contract: {exc}",
            code=exc.code) from None
    if low.output_boundary is not None:
        try:
            bind_output_boundary(proc, low.output_boundary)
        except OutputBoundaryError as exc:
            raise LoweringError(
                exc.node or fn, str(exc), code=exc.code) from None
    return proc


# `_may_terminate` and `_leaves_loop` were here: a walk over the source that
# answered, for each statement, whether control could leave at it. They are
# gone because `stmt` answers the same question as it lowers, and having two
# answers to one question meant nothing checked that they agreed. `_may_break`
# stays, since it is asked of a loop before its body is lowered and is a fact
# about that one loop rather than about a block.


class StatePlan:
    """What has to be known about a step before the step can be lowered for real.

    The frontend needs finite state and input ranges before it can type and
    lower every source operation.  Those ranges are themselves derived from a
    rough lowering, so the two-pass boundary and its provenance are explicit
    in this object.  A loop remains one semantic IR body in both passes; its
    domain-relative encoding maximum is derived later by EncodingPlan.

    Holding the answer rather than passing a bare dict is what lets the second pass be
    told where its inputs came from. `informed` is the difference between input ranges
    the contract states and input ranges guessed from a width, and it decides which loop
    bounds resolve, so a certificate that rests on the second pass rests on this.

    It is deliberately not `EncodingPlan`, which is M3's and holds widths and
    representations as well, computed against a bound. This holds what the *frontend*
    needs to finish, which is a smaller thing available earlier.
    """

    __slots__ = ("bound", "informed", "inputs", "ranges")

    def __init__(self, env, inputs, informed, bound=None, *, universe=None):
        #: The number of steps the ranges cover, or `None` where none was known and the
        #: fixpoint answered instead. It belongs on the plan because a range is a
        #: property of a *bounded* instance and not of the step in general: over ten
        #: steps from a known start `self.x = self.x + 1` is `[0,10]`, and over
        #: unbounded time it has no interval at all.
        self.bound = bound
        #: Total abstract result.  Every variable in the rough procedure's finite
        #: universe is present and maps to either a settled ``Range`` or explicit
        #: ``UNKNOWN``.  ``env`` below is only the known projection consumed by
        #: encoders; dictionary absence no longer carries an abstract meaning.
        universe = tuple(env) if universe is None else tuple(universe)
        self.ranges = ranges.AbstractRangeMap(universe, env)
        #: The input ranges the analysis ran under.
        self.inputs = dict(inputs)
        #: Whether those came from the contract (True) or from a width (False).
        self.informed = bool(informed)

    @property
    def env(self):
        """Known finite ranges, projected for the existing encoders."""

        return self.ranges.known

    def __repr__(self):
        how = "contract" if self.informed else "widths"
        over = f"{self.bound} steps" if self.bound is not None else "no bound"
        return (f"StatePlan({len(self.ranges) - len(self.ranges.unknown)} settled, "
                f"{len(self.ranges.unknown)} unknown, {how}, {over})")


def plan_state(source, file="behavior.py", method="process", outputs=None,
               input_widths=None, input_bounds=None, read_initial=None,
               bound=None, lowerer=None, signature=None, value_types=None,
               dynamic_state=(), boundary_initial=None,
               construction=None,
               _analysis_cache=None, cls=None, operation_goals=(), source_rules=EMPTY_SOURCE_RULES,
               local_guidance=False):
    """Derive the ranges of a step's state, by lowering it once roughly.

    The first of the two passes. The step is lowered without state ranges, the ranges are
    closed over that lowering, and `lower_with_state_ranges` lowers it again with them in
    hand. The rough lowering may fail on exactly the bounds the second pass resolves, so
    its failure is not reported: an empty plan, and the second pass says what is wrong.

    ``bound`` is how many steps the ranges have to cover, and with one the answer is
    `ranges.bounded`: R(0) = Init, R(t+1) = post#(R(t)), joined. Without one it is
    `ranges.fixpoint`, which asks the harder question of what interval is closed under
    the step for all time. The fixpoint is the fallback and not the intent -- a range is
    a property of a bounded instance, and the bound is known by the time one exists.

    ``local_guidance`` returns only the validated input domain, with no claimed
    state-range bound. Source representation still covers the requested horizon;
    the local query later pins state from a native-audited execution position.

    **The rough pass runs on the same lowerer as the pass whose output is kept**
    (``lowerer``, defaulting like `lower_procedure` to the graph one). For one release
    it stayed on the term lowerer for cost, which meant the ranges deciding the graph
    program's range facts were computed over a *different lowering* of the step -- the
    term planner inside the production graph path, exactly the two-authorities shape
    todo3 T3-11 names, held together only by a population-level agreement measurement
    with no per-run check behind it. The cost of the graph twice is real, measured,
    and used to be dominated on the largest models by the graph path's eager
    loop copying.  Loops are now structural; the remaining cost is measured by
    the next tracked census rather than hidden by a larger timeout.  The term
    lowerer survives only as a
    **secondary** differential (`test_lower_graph`; it shares this module's
    expression lowering with the graph, so a shared fault passes it); the oracle the
    chain leans on is the native instrumented execution (`test_native_oracle`),
    which stands outside both lowerings.
    """
    model = source_model(source, file, step=method, cls=cls)
    source_state = model.state_contract()
    input_widths = lower_prepare.validate_input_widths(model.step, input_widths)
    if input_bounds is not None:
        input_bounds = lower_prepare.validate_input_bounds(model.step, input_bounds)
    env = {}
    informed = False
    if input_bounds is not None:
        env = {name: ranges.Range(lo, hi)
               for name, (lo, hi) in input_bounds.items()}
        informed = True
    elif input_widths is not None:
        names = tuple(
            argument.arg for argument in model.step.args.args
            if argument.arg != "self")
        env = {
            name: ranges.Range(0, (1 << width) - 1)
            for name, width in zip(names, input_widths)
        }
    if signature is not None:
        from ..symbolic import typecheck as types_mod

        declared = {}
        for binding in signature.input_bindings:
            leaves = {leaf.path: leaf for leaf in binding.layout.leaves()}
            for logical, solver in binding.leaf_bindings:
                leaf = leaves[logical]
                pair = ((0, 1)
                        if leaf.semantic_type == types_mod.BOOLEAN else
                        leaf.carrier.bounds)
                known = declared.get(solver.variable)
                declared[solver.variable] = (
                    pair if known is None else
                    (min(known[0], pair[0]), max(known[1], pair[1])))
        for name, pair in declared.items():
            value = ranges.Range(*pair)
            if name in env and env[name] != value:
                raise LoweringError(
                    model.step,
                    f"the supplied range of physical input {name!r} "
                    "disagrees with its ValueType carrier",
                    code="input-bounds-disagree-with-signature")
            env[name] = value
        informed = True
    if local_guidance:
        # Local queries pin a source-audited concrete pre-state. They need the
        # task's input domain here, not an IR range closure of every possible
        # preceding input sequence. Source representation planning below still
        # retains the complete requested horizon and mutable state shapes.
        return StatePlan(env, env, informed, None,
                         universe=tuple(dict.fromkeys((*source_state, *env))))
    try:
        rough = lower_procedure(
            source, file, method, outputs, input_widths,
            state_ranges=env, lowerer=lowerer,
            read_initial=read_initial, signature=signature,
            value_types=value_types, dynamic_state=dynamic_state,
            bound=bound, boundary_initial=boundary_initial,
            construction=construction,
            _analysis_cache=_analysis_cache, cls=cls, operation_goals=operation_goals,
            source_rules=source_rules)
    except LoweringError as exc:
        if exc.code.startswith("input-") or exc.code == "source-plan-budget-exceeded":
            # No state envelope was produced. Repeating the same source
            # planning request with a fresh quota cannot supply missing facts;
            # it only doubles preparation time before the same refusal.
            raise
        # The second pass still knows the task-declared input carrier even when the
        # rough pass cannot settle state.  Dropping it here made a direct output such
        # as ``return {"y": a}`` have no width in the exact output-contract check.
        universe = tuple(dict.fromkeys((*source_state, *env)))
        return StatePlan(env, env, informed, bound, universe=universe)
    if not env:
        for name, width in zip(rough.inputs, input_widths or []):
            env[name] = ranges.Range(0, (1 << width) - 1)
    state_names = set(rough.state)
    state_is_written = any(
        isinstance(command, (ir.Assign, ir.Store))
        and command.var in state_names
        for command in ir.walk(rough.body)
    )
    if not state_is_written:
        # The state transition is the identity, so its finite cover is exactly
        # the admitted initial support at every horizon.  Executing a large
        # combinational body to rediscover that fact makes state planning scale
        # with output computation even though no output can move the state.
        settled = {**env, **ranges.initial_ranges(rough, products=True)}
        universe = tuple(dict.fromkeys(
            (*source_state, *rough.variables, *env)))
        return StatePlan(
            settled, env, informed, bound, universe=universe)
    from ..symbolic.slicing import state_range_graph

    state_graph = state_range_graph(rough)
    settled = (ranges.bounded(
                   rough, env, bound, _graph=state_graph,
                   _state_planning=True, _retain_products=True)
               if bound is not None else
               ranges.fixpoint(
                   rough, env, _graph=state_graph,
                   _state_planning=True, _retain_products=True))
    # ``bounded``/``fixpoint`` return facts discovered by stepping the procedure; a
    # read-only input need not occur in that post-state image.  StatePlan.env is the
    # complete typing/range environment for the retained lowering, so preserve the
    # task-owned input ranges explicitly rather than requiring a dataflow use to echo
    # them back. A normally completing postcondition can narrow an input after
    # a partial operation (for example a != 0 after division). That fact cannot
    # replace the input's entry domain and erase the operation's failure path.
    universe = tuple(dict.fromkeys((*source_state, *rough.variables, *env)))
    return StatePlan(
        {**settled, **env}, env, informed, bound, universe=universe)


@dataclass(frozen=True, slots=True)
class SourceLowering:
    """One lowered procedure paired with its independently declared catalogue."""

    proc: ir.Procedure
    catalogue: CatalogueManifest


def lower_source(source, file="behavior.py", method="process",
                 outputs=None, input_widths=None,
                 input_bounds=None, lowerer=None,
                 read_initial=None, bound=None, signature=None,
                 value_types=None, dynamic_state=(),
                 boundary_initial=None, construction=None, cls=None, operation_goals=(),
                 source_rules=EMPTY_SOURCE_RULES, prove_outputs=True,
                 local_guidance=False):
    """Return the informed lowering and the declaration fixed before its passes.

    Both passes bind safety sites to one read-only source AST and independently
    check the same declaration. An identical source-representation request reuses
    its occurrence-bound plan; a different range/domain request recomputes it.
    Factories retain the original manifest, not a third reconstruction from
    source or a catalogue inferred from emitted IR marks.

    Output admission can be deferred independently. ``local_guidance`` also
    omits IR whole-history state-range closure: source representation planning
    still covers ``bound``, but one-step encoding obtains state carriers from
    the audited concrete pre-state. No reset-state envelope is substituted.
    Its missing state-range bound and output proof prevent the local instance
    from supplying ordinary bounded-analysis evidence.
    """
    if local_guidance and prove_outputs:
        raise ValueError("local guidance cannot certify the whole-domain output contract")
    analysis_cache = lower_prepare.PreparationCache()
    plan = plan_state(source, file, method, outputs, input_widths,
                      input_bounds, read_initial, bound, lowerer=lowerer,
                      signature=signature, value_types=value_types,
                      dynamic_state=dynamic_state,
                      boundary_initial=boundary_initial,
                      construction=construction,
                      _analysis_cache=analysis_cache, cls=cls, operation_goals=operation_goals,
                      source_rules=source_rules, local_guidance=local_guidance)
    proc = lower_procedure(source, file, method, outputs, input_widths,
                           state_ranges=plan.env, lowerer=lowerer,
                           read_initial=read_initial, signature=signature,
                           value_types=value_types,
                           dynamic_state=dynamic_state, bound=bound,
                           boundary_initial=boundary_initial,
                           construction=construction,
                           _analysis_cache=analysis_cache, cls=cls, operation_goals=operation_goals,
                           source_rules=source_rules)
    proc.state_ranges = dict(plan.env)
    proc.state_range_result = plan.ranges
    proc.state_range_bound = plan.bound
    if prove_outputs:
        try:
            proc.output_ranges = output_contract_evidence(
                proc, plan.ranges.products, bound=bound)
        except OutputContractError as exc:
            # The informed pass is the first one whose environment covers the
            # bound. Proving this in the rough pass would turn an absent input
            # or state range into an empty plan and make a legal output unknown.
            raise LoweringError(proc, str(exc), code=exc.code) from None
    from .catalogue import _project_source_frontend

    catalogue = _project_source_frontend(
        proc, analysis_cache.goal_manifest, complete=True)
    return SourceLowering(proc, catalogue)


def lower_with_state_ranges(source, file="behavior.py", method="process",
                            outputs=None, input_widths=None,
                            input_bounds=None, lowerer=None,
                            read_initial=None, bound=None, signature=None,
                            value_types=None, dynamic_state=(),
                            boundary_initial=None, construction=None, cls=None,
                            source_rules=EMPTY_SOURCE_RULES):
    """Procedure-only view for IR consumers; source factories use lower_source."""
    return lower_source(
        source, file, method, outputs, input_widths,
        input_bounds=input_bounds, lowerer=lowerer, read_initial=read_initial,
        bound=bound, signature=signature, value_types=value_types,
        dynamic_state=dynamic_state, boundary_initial=boundary_initial,
        construction=construction, cls=cls, source_rules=source_rules).proc
