"""Audited elimination of definitions that a solver query cannot observe.

The sound first slicing boundary is narrower than general assertion slicing.
An :class:`ExactFormula` separates hard restrictions from equations that define
fresh internal constants.  A query keeps every hard restriction, then follows
definitions backward from the query target and the restrictions.  Definitions
outside that closure may be omitted: their left-hand constants occur nowhere
observable and an acyclic sequence of total equations can always reconstruct
them.

This module does not guess which assertions are definitions.  The producer must
register each one, every external constant, every external accessor declaration,
and every raw carrier that must not escape its frontend boundary.  An incomplete
or inconsistent registration returns the complete formula with a typed fallback
reason.  It never returns a speculative slice.
"""

from __future__ import annotations

import enum
from types import MappingProxyType

import z3

from ..core.frozen import Frozen
from ..core.construction_checkpoint import ConstructionCheckpoint
from ..core.query_telemetry import FormulaShape

__all__ = [
    "ExactFormula",
    "FormulaDefinition",
    "FormulaSlice",
    "SliceFallback",
    "SliceFallbackCode",
    "SliceMode",
    "measure_formula_shape",
]


# Internal authority for a formula emitted by the closed Behavior encoder.
# Generic ExactFormula callers receive the complete structural audit below.
# The bounded unroller and fresh local-query encoder may use this authority
# because they, rather than an artifact or candidate, create every fresh
# definition in strict execution order and emit only total QF_BV operations.
_GENERATED_QFBV = object()


def _dag_nodes(assertions):
    seen = set()
    pending = list(assertions)
    while pending:
        term = pending.pop()
        identity = term.get_id()
        if identity in seen:
            continue
        seen.add(identity)
        pending.extend(term.children())
    return len(seen)


def _text_bytes(assertions):
    return len("\n".join(term.sexpr() for term in assertions).encode("utf-8"))


def measure_formula_shape(full_assertions, sliced_assertions):
    """Measure the exact Boolean assertion DAGs submitted for one logical call.

    Node counts are unique Z3 AST nodes shared across the assertion tuple.  Text size
    is the UTF-8 size of the assertions' newline-joined S-expressions.  Both tuples
    include the query target, matching the values passed to ``Solver.add``.
    """

    full = tuple(full_assertions)
    sliced = tuple(sliced_assertions)
    if not full or not sliced or any(
            not isinstance(term, z3.ExprRef) or not z3.is_bool(term)
            for term in (*full, *sliced)):
        raise TypeError("formula-shape measurement needs nonempty Boolean assertions")
    return FormulaShape(
        full_assertions=len(full), full_nodes=_dag_nodes(full),
        full_text_bytes=_text_bytes(full),
        sliced_assertions=len(sliced), sliced_nodes=_dag_nodes(sliced),
        sliced_text_bytes=_text_bytes(sliced))


class SliceMode(enum.Enum):
    """How the registered formula was selected for one solver call."""

    SLICED = "sliced"
    COMPLETE = "complete"
    FULL = "full"

    def __bool__(self):
        raise TypeError("a slice mode is not a truth value")


class SliceFallbackCode(enum.Enum):
    """Closed reasons why definitional slicing used the full formula."""

    DUPLICATE_DEFINITION = "duplicate-definition"
    ATOM_ROLE_CONFLICT = "atom-role-conflict"
    CYCLIC_DEFINITIONS = "cyclic-definitions"
    RAW_TERM = "raw-term"
    UNKNOWN_ATOM = "unknown-atom"
    UNSUPPORTED_TERM = "unsupported-term"
    INCOMPLETE_CLOSURE = "incomplete-closure"

    def __bool__(self):
        raise TypeError("a slice fallback code is not a truth value")


def _expr(value, what):
    if not isinstance(value, z3.ExprRef):
        raise TypeError(f"{what} must be a Z3 expression")
    return value


def _independent_constant(value):
    """Whether ``value`` can receive a value independently in a model.

    The extension proof used by this first seam applies to fresh zero-arity
    constants.  An accessor application such as ``field(raw_state)`` is not an
    independent cell merely because it looks like one in the printed formula;
    a frontend must reify such cells before registering their definitions.
    """

    return z3.is_const(value) and value.decl().kind() == z3.Z3_OP_UNINTERPRETED


def _same_context(left, right):
    return left.ctx_ref() == right.ctx_ref()


def _terms_are_qfbv(terms):
    """Whether a finite term DAG uses only Boolean and bit-vector operators."""

    pending = list(terms)
    seen = set()
    while pending:
        term = pending.pop()
        identity = term.get_id()
        if identity in seen:
            continue
        seen.add(identity)
        if z3.is_quantifier(term) or z3.is_var(term):
            return False
        if term.sort().kind() not in {z3.Z3_BOOL_SORT, z3.Z3_BV_SORT}:
            return False
        if not z3.is_app(term):
            return False
        if term.decl().kind() == z3.Z3_OP_UNINTERPRETED \
                and term.decl().arity() != 0:
            return False
        pending.extend(term.children())
    return True


class FormulaDefinition(Frozen):
    """One total equation defining an independently assignable internal atom."""

    __slots__ = ("label", "lhs", "rhs")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("FormulaDefinition is a closed exact-formula record")

    def __init__(self, label, lhs, rhs):
        if type(label) is not str or not label:
            raise ValueError("a formula definition needs a nonempty label")
        lhs = _expr(lhs, "a definition lhs")
        rhs = _expr(rhs, "a definition rhs")
        if not _same_context(lhs, rhs):
            raise ValueError("both sides of a definition use one Z3 context")
        if not lhs.sort().eq(rhs.sort()):
            raise ValueError("both sides of a definition have the same sort")
        if not _independent_constant(lhs):
            raise ValueError(
                "a definition lhs must be a fresh zero-arity Z3 constant")
        self.label = label
        self.lhs = lhs
        self.rhs = rhs
        self._freeze()

    @classmethod
    def define(cls, label, symbol, rhs):
        """Create the defining constant in the RHS's own sort and context.

        The encoder owns the symbol's freshness. Unlike accepting a supplied
        LHS, this construction establishes its arity, sort and context directly;
        inspecting the newly built constant to establish them again is redundant.
        """
        if type(label) is not str or not label:
            raise ValueError("a formula definition needs a nonempty label")
        if type(symbol) is not str or not symbol:
            raise ValueError("a defining constant needs a nonempty symbol")
        rhs = _expr(rhs, "a definition rhs")
        definition = cls.__new__(cls)
        definition.label = label
        definition.lhs = z3.Const(symbol, rhs.sort())
        definition.rhs = rhs
        definition._freeze()
        return definition

    @property
    def assertion(self):
        return self.lhs == self.rhs


class SliceFallback(Frozen):
    """Why a slice conservatively returned the complete formula."""

    __slots__ = ("code", "detail")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("SliceFallback is a closed exact-formula record")

    def __init__(self, code, detail):
        if type(code) is not SliceFallbackCode:
            raise TypeError("a slice fallback needs the exact fallback-code enum")
        if type(detail) is not str or not detail:
            raise ValueError("a slice fallback needs a nonempty detail")
        self.code = code
        self.detail = detail
        self._freeze()


class FormulaSlice(Frozen):
    """The audited constraints for one target and their full-formula reference."""

    __slots__ = (
        "constraints",
        "dropped_definitions",
        "fallback",
        "full_constraints",
        "kept_definitions",
        "mode",
        "target",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("FormulaSlice is a closed exact-formula result")

    def __init__(self, *, mode, full_constraints, constraints, target,
                 kept_definitions, dropped_definitions, fallback=None):
        if type(mode) is not SliceMode:
            raise TypeError("a formula slice needs the exact SliceMode enum")
        if fallback is not None and type(fallback) is not SliceFallback:
            raise TypeError("a formula slice fallback has the closed record type")
        if (mode is SliceMode.FULL) != (fallback is not None):
            raise ValueError("only a full-formula result carries a fallback")
        full_constraints = tuple(full_constraints)
        constraints = tuple(constraints)
        if any(not z3.is_bool(term) for term in full_constraints) \
                or any(not z3.is_bool(term) for term in constraints):
            raise TypeError("formula constraints are Boolean Z3 expressions")
        target = _expr(target, "a slice target")
        if not z3.is_bool(target):
            raise TypeError("a slice target is Boolean")
        kept_definitions = tuple(kept_definitions)
        dropped_definitions = tuple(dropped_definitions)
        if any(type(label) is not str or not label
               for label in kept_definitions) \
                or any(type(label) is not str or not label
                       for label in dropped_definitions):
            raise TypeError("definition labels are nonempty strings")
        kept_names = set(kept_definitions)
        if any(label in kept_names for label in dropped_definitions):
            raise ValueError("a definition cannot be both kept and dropped")
        if mode in {SliceMode.COMPLETE, SliceMode.FULL} \
                and constraints != full_constraints:
            raise ValueError("a complete result uses the full formula verbatim")
        self.mode = mode
        self.full_constraints = full_constraints
        self.constraints = constraints
        self.target = target
        self.kept_definitions = kept_definitions
        self.dropped_definitions = dropped_definitions
        self.fallback = fallback
        self._freeze()

    @property
    def query_assertions(self):
        """The selected constraints followed by this query's target."""

        return self.constraints + (self.target,)

class _AuditFailure(Exception):
    __slots__ = ("code", "detail")

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _finish_steps(steps, checkpoint):
    """Check cancellation outside the iterator so its unfinished work survives."""
    while True:
        checkpoint()
        try:
            next(steps)
        except StopIteration as done:
            return done.value


class _FormulaConstruction:
    """Private registration of one owned product; publish only after its audit."""

    def __init__(self, definitions, restrictions, boundary_atoms, *, prefix=None):
        self.checkpoint = ConstructionCheckpoint()
        self._error = None
        self.result = None
        self._pending = object.__new__(ExactFormula)
        self._pending._checkpoint = self.checkpoint
        self._steps = self._pending._initialize(
            definitions=tuple(definitions), restrictions=tuple(restrictions),
            boundary_atoms=tuple(boundary_atoms), _generated=_GENERATED_QFBV,
            _checkpoint=self.checkpoint, _prefix=prefix)

    def advance(self, checkpoint):
        self.checkpoint.callback = checkpoint
        if self._error is not None:
            raise self._error
        while self.result is None:
            # A caller's cancellation leaves the iterator at its last yield.
            # An error *inside* registration closes it and cannot masquerade
            # as successful completion on a later call.
            self.checkpoint()
            try:
                next(self._steps)
            except StopIteration:
                self.result, self._pending = self._pending, None
                self._steps = None
            except BaseException as exc:
                self._error = exc
                self._steps = self._pending = None
                raise
        self.checkpoint()
        return self.result


class ExactFormula(Frozen):
    """Definitions, hard restrictions, and the complete declared atom universe.

    ``boundary_atoms`` are read-only, independently assignable constants such as
    inputs and query parameters.  ``external_functions`` are the exact
    positive-arity uninterpreted declarations emitted by a frontend, such as
    Yosys state accessors.  They are declarations rather than application
    instances, so every use has one shared interpretation.  ``protected_terms``
    are raw carriers that must not escape a frontend boundary.  These roles and
    the definition left-hand sides are closed: any other uninterpreted symbol in
    a target or formula causes full-formula fallback.
    """

    __slots__ = (
        "_acyclic_failure",
        "_assertion_by_id",
        "_boundary_by_id",
        "_boundary_ids",
        "_checkpoint",
        "_context",
        "_definition_by_id",
        "_definition_rank_by_id",
        "_dependency_edges",
        "_external_function_ids",
        "_full_constraints",
        "_generated_qfbv",
        "_ordered_memo",
        "_pre_target_failure",
        "_protected_ids",
        "_restriction_closure",
        "_restriction_dependencies",
        "_sliced_sat_extends",
        "_static_qfbv",
        "boundary_atoms",
        "definitions",
        "external_functions",
        "protected_terms",
        "restrictions",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ExactFormula is a closed exact-formula record")

    @classmethod
    def _from_generated_qfbv(cls, *, definitions=(), restrictions=(),
                             boundary_atoms=(), _checkpoint=None):
        """Register the closed Behavior encoder's ordered QF_BV product.

        The Behavior encoder emits each fresh left-hand atom after the terms
        its right-hand side may read. Registration checks that order, the
        closed atom vocabulary, and every hard restriction.  Together these
        facts establish that any model of an audited slice extends uniquely
        through the dropped definitions.  This is the authority that lets a
        sliced SAT boundary witness stand without reloading the complete
        generated formula.
        """
        return cls(
            definitions=definitions,
            restrictions=restrictions,
            boundary_atoms=boundary_atoms,
            _generated=_GENERATED_QFBV, _checkpoint=_checkpoint)

    def __init__(self, *, definitions=(), restrictions=(), boundary_atoms=(),
                 external_functions=(), protected_terms=(), _generated=None,
                 _checkpoint=None):
        self._checkpoint = _checkpoint
        _finish_steps(self._initialize(
            definitions=definitions, restrictions=restrictions,
            boundary_atoms=boundary_atoms, external_functions=external_functions,
            protected_terms=protected_terms, _generated=_generated,
            _checkpoint=_checkpoint), self._check_progress)

    def _initialize(self, *, definitions=(), restrictions=(), boundary_atoms=(),
                    external_functions=(), protected_terms=(), _generated=None,
                    _checkpoint=None, _prefix=None):
        if _generated is not None and _generated is not _GENERATED_QFBV:
            raise TypeError("an exact formula has no public generated authority")
        generated_qfbv = _generated is _GENERATED_QFBV
        self._checkpoint = _checkpoint
        yield
        definitions = tuple(definitions)
        restrictions = tuple(restrictions)
        boundary_atoms = tuple(boundary_atoms)
        external_functions = tuple(external_functions)
        protected_terms = tuple(protected_terms)
        if any(type(item) is not FormulaDefinition for item in definitions):
            raise TypeError("definitions use the closed FormulaDefinition type")
        labels = tuple(item.label for item in definitions)
        if len(labels) != len(set(labels)):
            raise ValueError("formula definition labels are distinct")
        for term in restrictions:
            _expr(term, "a hard restriction")
            if not z3.is_bool(term):
                raise TypeError("a hard restriction is Boolean")
        for term in (*boundary_atoms, *protected_terms):
            _expr(term, "a declared formula atom")
            if not _independent_constant(term):
                raise ValueError(
                    "boundary and protected atoms are zero-arity Z3 constants")
        for declaration in external_functions:
            if not isinstance(declaration, z3.FuncDeclRef):
                raise TypeError(
                    "an external formula function is a Z3 declaration")
            if declaration.kind() != z3.Z3_OP_UNINTERPRETED \
                    or declaration.arity() < 1:
                raise ValueError(
                    "an external formula function is an uninterpreted "
                    "positive-arity declaration")

        terms = [
            *(item.lhs for item in definitions),
            *(item.rhs for item in definitions),
            *restrictions,
            *boundary_atoms,
            *protected_terms,
        ]
        context = None if not terms else terms[0].ctx
        if context is not None and any(term.ctx != context for term in terms[1:]):
            raise ValueError("an exact formula uses one Z3 context")
        if len({term.get_id() for term in boundary_atoms}) != len(boundary_atoms):
            raise ValueError("boundary atoms are distinct")
        if len({term.get_id() for term in protected_terms}) != len(protected_terms):
            raise ValueError("protected terms are distinct")
        if {term.get_id() for term in boundary_atoms} & {
                term.get_id() for term in protected_terms}:
            raise ValueError("an atom cannot be both boundary and protected")
        if len({item.get_id() for item in external_functions}) \
                != len(external_functions):
            raise ValueError("external formula functions are distinct")
        if context is None and external_functions:
            context = external_functions[0].ctx
        if context is not None and any(
                item.ctx != context for item in external_functions):
            raise ValueError("an exact formula uses one Z3 context")

        self.definitions = definitions
        self.restrictions = restrictions
        self.boundary_atoms = boundary_atoms
        self.external_functions = external_functions
        self.protected_terms = protected_terms
        self._context = context
        self._generated_qfbv = generated_qfbv

        # An ordered product can grow across input boundaries as well as within
        # a step. Old definitions and atom roles must survive unchanged; new
        # inputs and hard restrictions are audited below. This shares completed
        # vocabulary/dependency work, never solver answers or partial audits.
        boundary_ids = frozenset(term.get_id() for term in boundary_atoms)
        restriction_ids = frozenset(term.get_id() for term in restrictions)
        prefix = _prefix
        if prefix is not None and not (
                generated_qfbv and type(prefix) is ExactFormula and prefix.sliced_sat_extends
                and context == prefix._context
                and not external_functions and not protected_terms
                and len(definitions) >= len(prefix.definitions)
                and all(old is new for old, new in zip(prefix.definitions, definitions))
                and {term.get_id() for term in prefix.restrictions} <= restriction_ids
                and prefix._boundary_ids <= boundary_ids):
            prefix = None
        prefix_count = 0 if prefix is None else len(prefix.definitions)
        previous_restrictions = (frozenset() if prefix is None else
                                 frozenset(term.get_id() for term in prefix.restrictions))
        added_restrictions = tuple((i, term) for i, term in enumerate(restrictions)
                                  if term.get_id() not in previous_restrictions)

        # Compile every target-independent property once.  A bounded analysis
        # asks many goals of one immutable formula; rebuilding this index,
        # dependency graph and acyclicity audit per goal makes the frontend
        # O(goals * formula) before the solver sees a query.
        assertions = ([] if prefix is None else
                      list(prefix.full_constraints[:prefix_count]))
        for item in definitions[prefix_count:]:
            yield
            assertions.append(item.assertion)
        assertions = tuple(assertions)
        self._full_constraints = assertions + restrictions
        self._boundary_ids = boundary_ids
        self._protected_ids = frozenset(
            term.get_id() for term in protected_terms)
        self._external_function_ids = frozenset(
            item.get_id() for item in external_functions)
        self._boundary_by_id = MappingProxyType({
            term.get_id(): term for term in boundary_atoms
        })
        self._assertion_by_id = MappingProxyType({
            item.lhs.get_id(): assertion
            for item, assertion in zip(definitions, assertions)
        })

        by_id = {} if prefix is None else dict(prefix._definition_by_id)
        ranks = {} if prefix is None else dict(prefix._definition_rank_by_id)
        edges = {} if prefix is None else dict(prefix._dependency_edges)
        restriction_dependencies = (set() if prefix is None else
                                    set(prefix._restriction_dependencies))
        ordered_memo = {} if prefix is None else dict(prefix._ordered_memo)
        pre_target_failure = None
        acyclic_failure = None
        try:
            for rank, definition in enumerate(definitions[prefix_count:], prefix_count):
                yield
                key = definition.lhs.get_id()
                if key in by_id:
                    other = by_id[key]
                    raise _AuditFailure(
                        SliceFallbackCode.DUPLICATE_DEFINITION,
                        f"{other.label!r} and {definition.label!r} define "
                        f"{definition.lhs.sexpr()}")
                by_id[key] = definition
                ranks[key] = rank
            overlap = set(by_id) & (self._boundary_ids | self._protected_ids)
            if overlap:
                atom = next(item.lhs for item in definitions
                            if item.lhs.get_id() in overlap)
                raise _AuditFailure(
                    SliceFallbackCode.ATOM_ROLE_CONFLICT,
                    f"{atom.sexpr()} has more than one formula role")
            if not generated_qfbv:
                # Generic monitor products share large source DAGs too. Reuse
                # the same vocabulary audit as ordered products, but do NOT
                # impose an ordering or grant sliced-SAT extension authority.
                # Forward references still use the generic cycle check below.
                for definition in definitions:
                    yield from self._ordered_dependency_steps(
                        definition.rhs, by_id, self._boundary_ids,
                        self._protected_ids, self._external_function_ids,
                        ranks, ordered_memo,
                        f"definition {definition.label!r}", edges=edges)
            else:
                # The generated product can contain many definitions whose
                # right-hand sides share most of their Z3 DAG.  Walking that
                # shared subtree afresh from every definition turns a linear
                # formula audit into definitions times DAG size.  For the
                # ordered-product proof we need only the greatest definition
                # rank read by each right-hand side: it must precede the rank
                # being defined.  Cache that scalar summary per Z3 node while
                # still checking every dropped definition for protected,
                # unknown, or unsupported terms.
                for definition in definitions[prefix_count:]:
                    identity = definition.lhs.get_id()
                    ceiling = yield from self._ordered_dependency_steps(
                        definition.rhs, by_id, self._boundary_ids,
                        self._protected_ids, self._external_function_ids,
                        ranks, ordered_memo,
                        f"definition {definition.label!r}", edges=edges)
                    if ceiling >= ranks[identity]:
                        raise _AuditFailure(
                            SliceFallbackCode.CYCLIC_DEFINITIONS,
                            f"generated definition {definition.label!r} reads "
                            "a definition not preceding it")
            # Keep shared expression edges for generic products too. Expanding
            # every RHS into its own definition set repeatedly traverses common
            # source/guard histories. Forward references remain legal only if
            # the complete graph passes the separate cycle audit below.
            edges.update((item.lhs.get_id(), (item.rhs.get_id(),))
                         for item in definitions[prefix_count:])
            for index, restriction in added_restrictions:
                yield from self._ordered_dependency_steps(
                    restriction, by_id, self._boundary_ids,
                    self._protected_ids, self._external_function_ids,
                    ranks, ordered_memo, f"hard restriction {index}", edges=edges)
            # These roots share the same audited vocabulary. Walk their union
            # once instead of revisiting a common prefix per hard restriction.
            added_dependencies = yield from self._walk_registered_steps(
                (term.get_id() for _index, term in added_restrictions), by_id, edges, expand=False)
            restriction_dependencies.update(added_dependencies)
        except _AuditFailure as failure:
            pre_target_failure = (failure.code, failure.detail)

        if pre_target_failure is None and not generated_qfbv:
            try:
                yield from self._audit_acyclic_steps(edges, by_id)
            except _AuditFailure as failure:
                # Target registration is checked before acyclicity in slice(),
                # preserving the original diagnostic ordering.
                acyclic_failure = (failure.code, failure.detail)

        restriction_closure = frozenset() if prefix is None else prefix._restriction_closure
        if pre_target_failure is None:
            try:
                restriction_closure = restriction_closure | (yield from self._walk_registered_steps(
                    added_dependencies, by_id, edges, expand=True))
            except _AuditFailure as failure:
                pre_target_failure = (failure.code, failure.detail)

        self._definition_by_id = MappingProxyType(dict(by_id))
        self._ordered_memo = MappingProxyType(ordered_memo)
        self._definition_rank_by_id = MappingProxyType(dict(ranks))
        self._dependency_edges = MappingProxyType(edges)
        self._restriction_dependencies = frozenset(restriction_dependencies)
        self._restriction_closure = frozenset(restriction_closure)
        self._pre_target_failure = pre_target_failure
        self._acyclic_failure = acyclic_failure
        self._sliced_sat_extends = (
            generated_qfbv and pre_target_failure is None)
        self._static_qfbv = generated_qfbv or (
            not external_functions and _terms_are_qfbv((
                *(item.lhs for item in definitions),
                *(item.rhs for item in definitions),
                *restrictions,
                *boundary_atoms,
                *protected_terms,
            )))
        self._freeze()

    def _check_progress(self):
        # Cancellation is not an audit failure: an unfinished registration
        # must unwind to its owner, never become a full-formula fallback that
        # starts another solver call after the same deadline.
        if self._checkpoint is not None:
            self._checkpoint()

    @property
    def full_constraints(self):
        """Every registered definition followed by every hard restriction."""

        return self._full_constraints

    @property
    def sliced_sat_extends(self):
        """Whether every audited slice model extends to this full formula."""

        return self._sliced_sat_extends

    def is_qfbv_with(self, target):
        """Whether this formula and ``target`` belong to QF_BV."""

        target = _expr(target, "a QF_BV target")
        return self._static_qfbv and _terms_are_qfbv((target,))

    def contains_boundary_atom(self, atom):
        """Whether ``atom`` is one of this formula's exact input coordinates."""

        if not isinstance(atom, z3.ExprRef):
            return False
        registered = self._boundary_by_id.get(atom.get_id())
        return registered is not None \
            and atom.ctx_ref() == registered.ctx_ref()

    def _fallback(self, target, code, detail):
        labels = tuple(item.label for item in self.definitions)
        return FormulaSlice(
            mode=SliceMode.FULL,
            full_constraints=self.full_constraints,
            constraints=self.full_constraints,
            target=target,
            kept_definitions=labels,
            dropped_definitions=(),
            fallback=SliceFallback(code, detail),
        )

    def slice(self, target):
        """Return an audited exact slice, or the complete formula on any doubt."""

        self._check_progress()
        target = _expr(target, "a slice target")
        if not z3.is_bool(target):
            raise TypeError("a slice target is Boolean")
        if self._context is not None and target.ctx != self._context:
            raise ValueError("the slice target uses the formula's Z3 context")

        try:
            return self._slice(target)
        except _AuditFailure as failure:
            return self._fallback(target, failure.code, failure.detail)

    def complete(self, target):
        """Select the complete formula after auditing the target vocabulary.

        A producer may already have reduced its semantics at a stronger,
        structured boundary.  Re-discovering a low-level definition cone then
        adds work without removing semantics from the submitted formula.  This
        path still checks that the target belongs to the registered atom
        universe, but deliberately performs no definitional elimination.
        """

        target = _expr(target, "a complete-formula target")
        if not z3.is_bool(target):
            raise TypeError("a complete-formula target is Boolean")
        if self._context is not None and target.ctx != self._context:
            raise ValueError(
                "the complete-formula target uses the formula's Z3 context")
        if self._pre_target_failure is not None:
            return self._fallback(target, *self._pre_target_failure)
        try:
            self._dependencies(
                target, self._definition_by_id, self._boundary_ids,
                self._protected_ids, self._external_function_ids,
                "query target")
        except _AuditFailure as failure:
            return self._fallback(target, failure.code, failure.detail)
        if self._acyclic_failure is not None:
            return self._fallback(target, *self._acyclic_failure)
        return FormulaSlice(
            mode=SliceMode.COMPLETE,
            full_constraints=self.full_constraints,
            constraints=self.full_constraints,
            target=target,
            kept_definitions=(item.label for item in self.definitions),
            dropped_definitions=(),
        )

    def dependency_cone(self, target):
        """Definition labels needed by ``target``, or ``None`` on fallback.

        This is the lightweight scheduling view of :meth:`slice`.  It does not
        construct an assertion tuple or walk the complete Z3 expression DAG.
        Callers may therefore compare exact cones and combine queries with the
        same definition requirements without making query planning itself
        proportional to ``number-of-goals * formula-size``.  A target that
        cannot be audited is
        deliberately incomparable, so it remains a singleton and the ordinary
        slice path later records its typed fallback.
        """

        return self.dependency_cones((target,))[0]

    def dependency_cones(self, targets):
        """Audit several cones while reading each reached definition once.

        The adjacency memo belongs only to this call. Each target still receives
        its own transitive closure and independent completeness check. No caller
        supplied cache or mutable formula state is used as proof evidence.
        """
        direct = {}
        return tuple(self._dependency_cone(target, direct) for target in targets)

    def _dependency_cone(self, target, direct):
        target = _expr(target, "a dependency-cone target")
        if not z3.is_bool(target):
            raise TypeError("a dependency-cone target is Boolean")
        if self._context is not None and target.ctx != self._context:
            raise ValueError("the dependency-cone target uses the formula's Z3 context")
        try:
            needed = self._needed_definitions(target, direct_cache=direct)
        except _AuditFailure:
            return None
        return frozenset(
            self._definition_by_id[item].label for item in needed)

    def dependency_masks(self, targets):
        """Exact cones as bit sets over this formula's definition order.

        Propagate requester bits through the registered DAG once for the batch,
        rather than allocating a set of labels and revisiting shared definitions
        for each occurrence. Bit ``i`` in a result denotes ``definitions[i]``;
        masks from different formulas must not be combined. Unauditable targets
        return ``None``, as in :meth:`dependency_cones`.
        """
        roots = []
        for target in targets:
            target = _expr(target, "a dependency-mask target")
            if not z3.is_bool(target):
                raise TypeError("a dependency-mask target is Boolean")
            if self._context is not None and target.ctx != self._context:
                raise ValueError("the dependency-mask target uses the formula's Z3 context")
            try:
                if self._pre_target_failure is not None:
                    raise _AuditFailure(*self._pre_target_failure)
                direct = self._dependencies(
                    target, self._definition_by_id, self._boundary_ids,
                    self._protected_ids, self._external_function_ids, "query target")
                if self._acyclic_failure is not None:
                    raise _AuditFailure(*self._acyclic_failure)
                roots.append(direct | self._restriction_dependencies)
            except _AuditFailure:
                roots.append(None)
        # Repeated root sets have exactly the same closure. Only valid root sets
        # receive requester bits; one malformed query cannot certify another.
        indices = {root: i for i, root in enumerate(dict.fromkeys(
            root for root in roots if root is not None))}
        try:
            masks = self._propagate_dependency_masks(indices)
        except _AuditFailure:
            return (None,) * len(roots)
        return tuple(None if root is None else masks[indices[root]] for root in roots)

    def _propagate_dependency_masks(self, indices):
        requests = {}
        for roots, index in indices.items():
            bit = 1 << index
            for root in roots:
                requests[root] = requests.get(root, 0) | bit
        # Postorder the reached graph without recursive Python frames. Recheck
        # graph completeness here; a missing edge record is never an empty cone.
        order, active, done = [], set(), set()
        pending = [(node, False) for node in requests]
        while pending:
            node, exiting = pending.pop()
            if exiting:
                active.remove(node)
                done.add(node)
                order.append(node)
                continue
            if node in done:
                continue
            if node in active:
                raise _AuditFailure(SliceFallbackCode.CYCLIC_DEFINITIONS,
                                    "the registered dependency graph has a cycle")
            children = self._dependency_edges.get(node)
            if children is None:
                raise _AuditFailure(SliceFallbackCode.INCOMPLETE_CLOSURE,
                                    "the registered dependency graph is missing a reached node")
            active.add(node)
            pending.append((node, True))
            pending.extend((child, False) for child in children if child not in done)
        for node in reversed(order):
            mask = requests.get(node, 0)
            for child in self._dependency_edges[node]:
                requests[child] = requests.get(child, 0) | mask
        self._audit_dependency_requests(indices, requests, order)

        # Transpose requester bits into definition bits. Definitions sharing a
        # requester set contribute one packed column, not one Python set entry
        # per (occurrence, definition) pair.
        columns = {}
        width = (len(self.definitions) + 7) // 8
        for node in order:
            rank = self._definition_rank_by_id.get(node)
            if rank is not None:
                request = requests[node]
                if request not in columns:
                    columns[request] = bytearray(width)
                columns[request][rank // 8] |= 1 << (rank % 8)
        masks = [0] * len(indices)
        for request, column in columns.items():
            bits = int.from_bytes(column, "little")
            while request:
                bit = request & -request
                masks[bit.bit_length() - 1] |= bits
                request ^= bit
        return tuple(masks)

    def _audit_dependency_requests(self, indices, requests, order):
        """Check each target's seeds and closure independently of propagation."""
        for roots, index in indices.items():
            if any(not requests.get(root, 0) & (1 << index) for root in roots):
                raise _AuditFailure(SliceFallbackCode.INCOMPLETE_CLOSURE,
                                    "a dependency mask is missing a query root")
        for node in order:
            if any(requests.get(node, 0) & ~requests.get(child, 0)
                   for child in self._dependency_edges[node]):
                raise _AuditFailure(SliceFallbackCode.INCOMPLETE_CLOSURE,
                                    "a dependency mask still reads a dropped definition")

    def dependency_roots(self, target):
        """Direct definition roots read by ``target``, or ``None`` on fallback.

        The formula's restrictions and definition graph are fixed.  Equal
        direct roots therefore imply equal transitive cones, while avoiding a
        full closure allocation for every goal occurrence during scheduling.
        The later slice still derives and audits the complete closure.
        """

        target = _expr(target, "a dependency-root target")
        if not z3.is_bool(target):
            raise TypeError("a dependency-root target is Boolean")
        if self._context is not None and target.ctx != self._context:
            raise ValueError(
                "the dependency-root target uses the formula's Z3 context")
        if self._pre_target_failure is not None:
            return None
        try:
            roots = self._dependencies(
                target, self._definition_by_id, self._boundary_ids,
                self._protected_ids, self._external_function_ids,
                "query target")
        except _AuditFailure:
            return None
        if self._acyclic_failure is not None:
            return None
        return frozenset(
            self._definition_by_id[item].label for item in roots)

    def _slice(self, target):
        needed = self._needed_definitions(target)
        # The target may be identically false after exact Boolean
        # simplification (for example, a source safety occurrence whose
        # invalid predicate was proved false).  Registration and acyclicity
        # were still audited by ``_needed_definitions`` above, but no hard
        # restriction or definition can affect ``F and false``.  Retaining
        # them made each such proof reload an otherwise unrelated whole
        # transition formula.
        simplified = z3.simplify(target)
        if z3.is_false(simplified):
            return FormulaSlice(
                mode=SliceMode.SLICED,
                full_constraints=self.full_constraints,
                constraints=(),
                target=simplified,
                kept_definitions=(),
                dropped_definitions=(
                    item.label for item in self.definitions),
            )
        constraints = []
        kept_labels = []
        dropped_labels = []
        for item in self.definitions:
            self._check_progress()
            identity = item.lhs.get_id()
            if identity in needed:
                constraints.append(self._assertion_by_id[identity])
                kept_labels.append(item.label)
            else:
                dropped_labels.append(item.label)
        return FormulaSlice(
            mode=SliceMode.SLICED,
            full_constraints=self.full_constraints,
            constraints=tuple(constraints) + self.restrictions,
            target=target,
            kept_definitions=kept_labels,
            dropped_definitions=dropped_labels,
        )

    def _needed_definitions(self, target, *, direct_cache=None):
        if self._pre_target_failure is not None:
            raise _AuditFailure(*self._pre_target_failure)

        by_id = self._definition_by_id
        target_dependencies = self._dependencies(
            target, by_id, self._boundary_ids, self._protected_ids,
            self._external_function_ids,
            "query target")

        if self._acyclic_failure is not None:
            raise _AuditFailure(*self._acyclic_failure)

        if direct_cache is None:
            # Registration already read the immutable RHS DAGs. Follow their
            # integer edges, not fresh Python/Z3 walks per definition or query.
            needed = set(self._restriction_closure) | set(self._walk_registered(
                target_dependencies, by_id, self._dependency_edges, expand=True))
            # Independently inspect every retained RHS, stopping at definition
            # atoms this time. A shared expression is still visited only once.
            observed = self._walk_registered(
                (child for item in needed for child in self._dependency_edges[item]),
                by_id, self._dependency_edges, expand=False)
        else:
            # Scheduling several targets can instead reuse a caller-local
            # adjacency memo. Each target retains its own exact closure.
            def direct_of(atom):
                known = direct_cache.get(atom)
                if known is None:
                    known = self._definition_dependencies(atom)
                    direct_cache[atom] = known
                return known

            needed = set(self._restriction_closure) | set(target_dependencies)
            pending = list(target_dependencies)
            while pending:
                atom = pending.pop()
                for dependency in direct_of(atom):
                    if dependency not in needed:
                        needed.add(dependency)
                        pending.append(dependency)
            observed = set()
            for atom in needed:
                observed.update(direct_of(atom))

        # Independent second check of the property on which the extension proof
        # relies.  It intentionally repeats the closure invariant instead of
        # trusting the work-list implementation above.
        observable = set(self._restriction_dependencies) \
            | set(target_dependencies) | set(observed)
        missing = observable - needed
        if missing:
            names = sorted(by_id[item].label for item in missing)
            raise _AuditFailure(
                SliceFallbackCode.INCOMPLETE_CLOSURE,
                f"the retained formula still reads dropped definitions {names}")
        return frozenset(needed)

    def _definition_dependencies(self, atom):
        """Read direct dependencies lazily from the audited shared graph."""
        definition = self._definition_by_id.get(atom)
        if definition is None:
            raise _AuditFailure(
                SliceFallbackCode.UNKNOWN_ATOM,
                "a dependency is not a registered definition")
        if atom not in self._dependency_edges:
            raise _AuditFailure(SliceFallbackCode.INCOMPLETE_CLOSURE,
                                f"definition {definition.label!r} has no dependency record")
        direct = self._walk_registered(self._dependency_edges[atom],
            self._definition_by_id, self._dependency_edges, expand=False)
        rank = self._definition_rank_by_id[atom]
        if self._generated_qfbv and any(
                self._definition_rank_by_id[item] >= rank for item in direct):
            raise _AuditFailure(
                SliceFallbackCode.CYCLIC_DEFINITIONS,
                f"generated definition {definition.label!r} reads a "
                "definition not preceding it")
        return direct

    def _dependencies(self, term, by_id, boundary, protected,
                      external_functions, where):
        found = set()
        visited = set()
        stack = [term]
        while stack:
            self._check_progress()
            current = stack.pop()
            key = current.get_id()
            if key in protected:
                raise _AuditFailure(
                    SliceFallbackCode.RAW_TERM,
                    f"{where} reads raw carrier {current.sexpr()}")
            if key in visited:
                continue
            visited.add(key)
            if key in by_id:
                found.add(key)
                continue
            if key in boundary:
                continue
            if z3.is_quantifier(current) or z3.is_var(current):
                raise _AuditFailure(
                    SliceFallbackCode.UNSUPPORTED_TERM,
                    f"{where} contains unsupported {current.sexpr()}")
            if not z3.is_app(current):
                raise _AuditFailure(
                    SliceFallbackCode.UNSUPPORTED_TERM,
                    f"{where} contains an unsupported Z3 term")
            if current.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                if current.decl().arity() > 0 \
                        and current.decl().get_id() in external_functions:
                    stack.extend(current.children())
                    continue
                # Prefer the more useful carrier diagnostic when an unregistered
                # accessor encloses one.  Such an application is not silently
                # accepted as a field atom.
                descendants = list(current.children())
                if self._contains_protected(descendants, protected):
                    raise _AuditFailure(
                        SliceFallbackCode.RAW_TERM,
                        f"{where} exposes a raw carrier through {current.sexpr()}")
                raise _AuditFailure(
                    SliceFallbackCode.UNKNOWN_ATOM,
                    f"{where} reads undeclared atom {current.sexpr()}")
            stack.extend(current.children())
        return frozenset(found)

    def _walk_registered(self, roots, by_id, edges, *, expand):
        """Read a registered graph once, optionally crossing definition atoms.

        Registration retains the shared expression DAG, avoiding potentially
        quadratic expansion into per-definition dependency sets. No fresh target or
        caller-supplied cache is admitted through this path.
        """
        return _finish_steps(self._walk_registered_steps(
            roots, by_id, edges, expand=expand), self._check_progress)

    def _walk_registered_steps(self, roots, by_id, edges, *, expand):
        found = set()
        visited = set()
        pending = list(roots)
        while pending:
            yield
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            if node in by_id:
                found.add(node)
                if not expand:
                    continue
            children = edges.get(node)
            if children is None:
                raise _AuditFailure(
                    SliceFallbackCode.INCOMPLETE_CLOSURE,
                    "the registered dependency graph is missing a reached node")
            pending.extend(children)
        return frozenset(found)

    def _ordered_dependency_steps(
            self, term, by_id, boundary, protected, external_functions,
            ranks, memo, where, *, edges=None):
        """Audit a generated term DAG and return its greatest definition rank.

        ``memo`` is shared by every definition in one generated product.  A
        negative result means that the subtree contains no definition atom;
        an audit failure is cached as its typed code and local explanation.
        The caller adds the definition in whose right-hand side the subtree
        was observed, preserving a useful diagnostic without revisiting it.
        """

        root = term.get_id()
        pending = [(term, None)]
        while pending:
            yield
            current, children = pending.pop()
            key = current.get_id()
            if key in memo:
                continue
            if children is not None:
                # Entry already audited this immutable node and read its
                # edges.  Exit only combines the children's summaries; asking
                # Z3 for the same structure again doubles the native/Python
                # traversal cost on a large generated transition.
                summaries = tuple(memo[child] for child in children)
                failure = next(
                    (summary for summary in summaries
                     if isinstance(summary, tuple)), None)
                memo[key] = (failure if failure is not None
                             else max(summaries, default=-1))
                continue
            if key in protected:
                memo[key] = (
                    SliceFallbackCode.RAW_TERM,
                    f"reads raw carrier {current.sexpr()}")
                continue
            if key in by_id:
                memo[key] = ranks[key]
                continue
            if key in boundary:
                memo[key] = -1
                if edges is not None:
                    edges[key] = ()
                continue
            if z3.is_quantifier(current) or z3.is_var(current):
                memo[key] = (
                    SliceFallbackCode.UNSUPPORTED_TERM,
                    f"contains unsupported {current.sexpr()}")
                continue
            if not z3.is_app(current):
                memo[key] = (
                    SliceFallbackCode.UNSUPPORTED_TERM,
                    "contains an unsupported Z3 term")
                continue

            children = tuple(current.children())
            if current.decl().kind() == z3.Z3_OP_UNINTERPRETED and (
                    current.decl().arity() == 0
                    or current.decl().get_id() not in external_functions):
                if self._contains_protected(children, protected):
                    memo[key] = (
                        SliceFallbackCode.RAW_TERM,
                        f"exposes a raw carrier through {current.sexpr()}")
                else:
                    memo[key] = (
                        SliceFallbackCode.UNKNOWN_ATOM,
                        f"reads undeclared atom {current.sexpr()}")
                continue

            child_ids = tuple(child.get_id() for child in children)
            if edges is not None:
                edges[key] = child_ids
            pending.append((current, child_ids))
            pending.extend(
                (child, None) for child in reversed(children)
                if child.get_id() not in memo)

        result = memo[root]
        if isinstance(result, tuple):
            code, detail = result
            raise _AuditFailure(code, f"{where} {detail}")
        return result

    @staticmethod
    def _contains_protected(stack, protected):
        stack = list(stack)
        visited = set()
        while stack:
            current = stack.pop()
            key = current.get_id()
            if key in protected:
                return True
            if key in visited:
                continue
            visited.add(key)
            if z3.is_app(current) or z3.is_quantifier(current):
                stack.extend(current.children())
        return False

    @staticmethod
    def _audit_acyclic_steps(edges, by_id):
        """Check every definition, including unused and forward-referenced ones.

        Explicit frames keep long valid histories independent of Python's
        recursion limit. Expression nodes and defining atoms share this graph;
        no generic sliced-SAT authority follows from the faster traversal.
        """
        active, done = set(), set()
        pending = [(atom, False) for atom in by_id]
        while pending:
            yield
            node, exiting = pending.pop()
            if exiting:
                active.remove(node)
                done.add(node)
                continue
            if node in done:
                continue
            if node in active:
                raise _AuditFailure(SliceFallbackCode.CYCLIC_DEFINITIONS,
                                    'the definition dependency graph contains a cycle')
            children = edges.get(node)
            if children is None:
                raise _AuditFailure(SliceFallbackCode.INCOMPLETE_CLOSURE,
                                    'a definition dependency has no registered node')
            active.add(node)
            pending.append((node, True))
            pending.extend((child, False) for child in children if child not in done)
