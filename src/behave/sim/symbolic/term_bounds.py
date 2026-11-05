"""Shared proofs of encoded terms, independent of a model or query verdict.

These sufficient bounds and Boolean implications are used by both rewriting
and checked plan preparation. They neither construct a transition model nor
seal a plan, and declined proofs retain the original exact obligation.
"""

from behave.core import runtime_defaults as defaults

from . import ranges

_CONTROL_PROPOSITION_BUDGET = defaults.SIM_BEHAVIOR_CONTROL_PROOF_NODES


def _unsigned_hull(hull, width):
    """Interpret a signed carrier interval as unsigned, conservatively."""
    if hull.lo >= 0:
        return hull
    modulus = 1 << width
    if hull.hi < 0:
        return ranges.Range(hull.lo + modulus, hull.hi + modulus)
    return ranges.Range(0, modulus - 1)


def _unsigned_quotient_hull(left, right, width, *, remainder=False):
    """Bounds for total SMT unsigned division/remainder, read as signed bits.

    Division by zero yields all ones; remainder by zero returns the dividend.
    Retain those values, rather than assuming the source's domain check passed.
    Cross-sign images use the complete signed interval, not unsigned ordering.
    """
    left, right = _unsigned_hull(left, width), _unsigned_hull(right, width)
    modulus, sign = 1 << width, 1 << (width - 1)
    parts = []
    if right.hi:
        low, high = ((0, min(left.hi, right.hi - 1)) if remainder else
                     (left.lo // right.hi, left.hi // max(1, right.lo)))
        parts.append(ranges.Range(low, high))
    if right.lo == 0:
        parts.append(left if remainder else ranges.Range(modulus - 1, modulus - 1))
    signed = []
    for part in parts:
        signed.append(part if part.hi < sign else
                      ranges.Range(part.lo - modulus, part.hi - modulus) if part.lo >= sign else
                      ranges.Range(-sign, sign - 1))
    return ranges.Range(min(part.lo for part in signed), max(part.hi for part in signed))


def _compare_hulls(kind, left, right):
    """A comparison forced by two signed hulls, or None when they do not prove it.

    Callers own how hulls and contextual facts were established. In particular,
    unsigned ordering is not inferred from a signed hull containing negatives.
    This is an encoded-term precheck, independent of source range transfer.
    """
    import z3

    if left is None or right is None:
        return None
    if kind == z3.Z3_OP_EQ:
        if left.lo == left.hi == right.lo == right.hi:
            return True
        return False if left.hi < right.lo or right.hi < left.lo else None
    if kind == z3.Z3_OP_DISTINCT:
        if left.hi < right.lo or right.hi < left.lo:
            return True
        return False if left.lo == left.hi == right.lo == right.hi else None

    unsigned = kind in {
        z3.Z3_OP_ULT, z3.Z3_OP_ULEQ,
        z3.Z3_OP_UGT, z3.Z3_OP_UGEQ,
    }
    if unsigned and (left.lo < 0 or right.lo < 0):
        return None
    relation = {
        z3.Z3_OP_SLT: "lt", z3.Z3_OP_ULT: "lt",
        z3.Z3_OP_SLEQ: "le", z3.Z3_OP_ULEQ: "le",
        z3.Z3_OP_SGT: "gt", z3.Z3_OP_UGT: "gt",
        z3.Z3_OP_SGEQ: "ge", z3.Z3_OP_UGEQ: "ge",
    }[kind]
    if relation == "lt":
        if left.hi < right.lo:
            return True
        if left.lo >= right.hi:
            return False
    elif relation == "le":
        if left.hi <= right.lo:
            return True
        if left.lo > right.hi:
            return False
    elif relation == "gt":
        if left.lo > right.hi:
            return True
        if left.hi <= right.lo:
            return False
    else:
        if left.lo >= right.hi:
            return True
        if left.hi < right.lo:
            return False
    return None


class _StructuralProofLimit(Exception):
    """Decline an optional structural proof, never establish a proposition."""


class _StructuralProofWork:
    """One shared allowance for a closure precheck's recursive proof graph."""

    __slots__ = ("deadline", "remaining", "checkpoint")

    def __init__(self, maximum, deadline, *, checkpoint=None):
        self.remaining = maximum
        self.deadline = deadline
        self.checkpoint = checkpoint

    def charge(self):
        import time

        if self.checkpoint is not None:
            self.checkpoint()
        if ((self.remaining is not None and self.remaining <= 0)
                or time.monotonic() >= self.deadline):
            raise _StructuralProofLimit
        if self.remaining is not None:
            self.remaining -= 1


class _SyntacticClosureProof:
    """Small iterative proofs over one encoded transition DAG.

    Position closure must be independent of the abstract range transformer it
    checks.  It may nevertheless discharge facts that are immediate from the
    actual encoded term, such as constant arithmetic, a mask, or a no-wrap
    addition.  The encoder commonly produces definition chains thousands of
    nodes deep, so both evaluators below use explicit postorder worklists and
    share their memo tables across every state/output leaf in the transition.
    Python's recursion limit is not a frontend capability limit.
    """

    __slots__ = (
        "array_cases", "array_hulls", "array_selections",
        "assumed_false", "assumed_nonzeros", "assumed_ready",
        "assumed_true", "assumption",
        "boolean_formulas", "booleans", "coarse_hulls", "concat_prefixes", "constants",
        "definitions", "guard_nodes", "guard_ranges", "hulls", "implications", "nonzeros", "propositions",
        "proved_hulls", "tautologies", "terms", "truth_active", "work")

    def __init__(self, definitions, assumption=None, *, work=None,
                 array_selections=()):
        from .functional_array import _ArraySelection

        self.array_selections = {}
        for selection in array_selections:
            if type(selection) is not _ArraySelection:
                raise TypeError("array proof sources must be encoded selections")
            self.array_selections[selection.term.get_id()] = selection
        self.array_cases = {}
        self.array_hulls = {}
        self.definitions = dict(definitions)
        self.guard_nodes = {}
        self.guard_ranges = {}
        self.work = work
        self.assumption = assumption
        self.boolean_formulas = {}
        self.booleans = {}
        self.constants = {}
        self.coarse_hulls = {}
        self.concat_prefixes = {}
        self.hulls = {}
        self.implications = {}
        self.nonzeros = {}
        self.propositions = None
        self.tautologies = None
        self.proved_hulls = {}
        # Z3 may recycle an AST id after the last Python reference to that AST
        # disappears.  Every memo below is keyed by that id for speed, so keep
        # the corresponding term alive for the lifetime of the proof.
        self.terms = {}
        self.truth_active = set()
        self.assumed_false = set()
        self.assumed_nonzeros = {}
        self.assumed_ready = False
        self.assumed_true = set()

    def _charge(self):
        if self.work is not None:
            self.work.charge()

    def _prepare_assumption_literals(self):
        """Index direct normal-path facts without per-node SAT queries."""

        if self.assumed_ready:
            return
        self.assumed_ready = True
        if self.assumption is None:
            return
        import z3

        pending = [(self.assumption, True)]
        seen = set()
        while pending:
            current, truth = pending.pop()
            identity = current.get_id()
            key = (identity, truth)
            if key in seen:
                continue
            self._charge()
            seen.add(key)
            self.terms[identity] = current
            (self.assumed_true if truth else self.assumed_false).add(identity)
            tested = self._zero_test(current)
            if tested is not None:
                value, means_nonzero = tested
                nonzero = means_nonzero == truth
                previous = self.assumed_nonzeros.get(value.get_id(), nonzero)
                self.assumed_nonzeros[value.get_id()] = (
                    nonzero if previous == nonzero else None)
                self.terms[value.get_id()] = value
            definition = (self.definitions.get(identity)
                          if z3.is_const(current) else None)
            if definition is not None and z3.is_bool(definition):
                pending.append((definition, truth))
                continue
            kind = current.decl().kind()
            if kind == z3.Z3_OP_AND and truth:
                pending.extend((child, True) for child in current.children())
            elif kind == z3.Z3_OP_OR and not truth:
                pending.extend((child, False) for child in current.children())
            elif kind == z3.Z3_OP_NOT and current.num_args() == 1:
                pending.append((current.arg(0), not truth))

    def _assumed_value(self, term):
        """A truth value stated directly by the normal-completion guard."""

        self._prepare_assumption_literals()
        identity = term.get_id()
        if identity in self.assumed_true:
            return True
        if identity in self.assumed_false:
            return False
        return None

    def _assumed_nonzero(self, term):
        """A zero/nonzero fact stated by the normal-completion guard."""

        import z3

        self._prepare_assumption_literals()
        current = term
        seen = set()
        while z3.is_bv(current):
            identity = current.get_id()
            if identity in self.assumed_nonzeros:
                return self.assumed_nonzeros[identity]
            if identity in seen:
                break
            seen.add(identity)
            if z3.is_const(current) and identity in self.definitions:
                candidate = self.definitions[identity]
                if z3.is_bv(candidate):
                    current = candidate
                    continue
            if current.decl().kind() in {
                    z3.Z3_OP_SIGN_EXT, z3.Z3_OP_ZERO_EXT,
            }:
                current = current.arg(0)
                continue
            break
        return None

    def _known_nonzero(self, term):
        """A zero/nonzero fact from assumptions or an already-proved hull."""

        known = self._assumed_nonzero(term)
        if known is not None:
            return known
        hull = self.hulls.get(term.get_id())
        if hull is None:
            return None
        if hull.lo == hull.hi == 0:
            return False
        if hull.hi < 0 or hull.lo > 0:
            return True
        return None

    def implies(self, atom, truth=True):
        """Whether normal completion propositionally entails one guard."""

        if self.assumption is None:
            return False
        self.terms[atom.get_id()] = atom
        key = (atom.get_id(), bool(truth))
        if key not in self.implications:
            import z3

            normalized_atom = self.boolean_formula(atom)
            consequent = normalized_atom if truth else z3.Not(normalized_atom)
            if self.propositions is None:
                # Normalize the premise without treating its own literals as
                # already true.  Using ``self`` here turns an inconsistent
                # path such as ``g and not g`` into ``True and True`` and
                # loses the vacuous implication that path splitting needs.
                premise = type(self)(self.definitions, work=self.work)
                for identity, hull in self.proved_hulls.items():
                    term = self.terms.get(identity)
                    if term is not None:
                        premise._record_proved_hull(term, hull)
                normalized_assumption = premise.boolean_formula(
                    self.assumption)
                self.propositions = _PropositionalProof(
                    normalized_assumption, self.definitions, work=self.work)
            self.implications[key] = self.propositions.implies(consequent)
        return self.implications[key]

    def assume_boundary_hull(self, term, hull):
        """Record one finite boundary premise already present in the formula.

        Position closure is an induction step: the current state and input are
        assumed to inhabit their declared layer, and the successor must be
        proved to inhabit the next one.  The encoder emits those current-layer
        bounds as restrictions on every fresh scalar leaf.  Retaining the same
        fact here lets the syntactic proof follow copies, stores and arithmetic
        without asking SMT to rediscover hundreds of identical boundary bounds.
        """

        self._record_proved_hull(term, hull)

    def use_certified_hull(self, term, hull):
        """Record a conclusion of a sealed proof bound to this exact body.

        Unlike a current-layer premise, this fact need not occur as a solver
        restriction.  The caller must first validate the independent
        certificate against the procedure body and boundary metadata.
        """

        self._record_proved_hull(term, hull)

    def _record_proved_hull(self, term, hull):
        import z3

        if not z3.is_bv(term) or not isinstance(hull, ranges.Range):
            raise TypeError("a proved hull needs one bit-vector and one Range")
        # Z3 interns literals: the same zero can be an output placeholder, a
        # shift operand and an inactive assignment arm. A containing boundary
        # certificate must not replace that intrinsic singleton by a wide
        # interval and thereby weaken every other use of the shared literal.
        if z3.is_bv_value(term):
            value = term.as_signed_long()
            hull = ranges.Range(value, value)
        identity = term.get_id()
        self.terms[identity] = term
        self.hulls[identity] = hull
        self.proved_hulls[identity] = hull
        # The branch-insensitive proof ignores only control predicates; it
        # must retain the same exact boundary premise.  Otherwise a dynamic
        # array store expands into a nest of ``ite`` terms whose untouched
        # arms look unconstrained, even though every pre-state cell is bounded
        # by the encoded layer restriction.
        self.coarse_hulls[identity] = hull

    def _with_branch_assumption(self, condition, truth):
        """Return a proof for one branch, retaining only sealed premises."""

        import z3

        literal = condition if truth else z3.Not(condition)
        assumption = literal if self.assumption is None \
            else z3.And(self.assumption, literal)
        branch = type(self)(self.definitions, assumption=assumption, work=self.work)
        for identity, hull in self.proved_hulls.items():
            term = self.terms.get(identity)
            if term is not None:
                branch._record_proved_hull(term, hull)
        return branch

    def _first_unresolved_value_guard(self, term):
        """Find one undecided ``ite`` guard in a value's definition cone."""

        import z3

        pending = [term]
        seen = set()
        while pending:
            current = pending.pop()
            identity = current.get_id()
            if identity in seen:
                continue
            self._charge()
            seen.add(identity)
            if z3.is_const(current) and identity in self.definitions:
                pending.append(self.definitions[identity])
                continue
            if not z3.is_bv(current) or z3.is_bv_value(current):
                continue
            if current.decl().kind() == z3.Z3_OP_ITE:
                condition = current.arg(0)
                known = self._known_boolean(condition)
                if known is None:
                    if self.implies(condition):
                        known = True
                    elif self.implies(condition, False):
                        known = False
                if known is None:
                    return condition
                pending.append(current.arg(1 if known else 2))
                continue
            pending.extend(child for child in current.children()
                           if z3.is_bv(child))
        return None

    def proves_carrier(self, term, carrier, *, max_leaves):
        """Prove carrier membership by finite path-sensitive intervals.

        The ordinary hull joins both arms of a conditional.  That join can
        lose exactly the correlation that prevents a later fixed-width add
        from wrapping.  Split only such unresolved value guards, preserve the
        independently established boundary premises, and require every path
        partition to fit.  Exhausting the proof budget merely declines this
        structural proof; the exact solver remains the authority.
        """

        import z3

        if not z3.is_bv(term) or max_leaves < 1:
            return False
        pending = [self]
        leaves = 1
        while pending:
            proof = pending.pop()
            if proof.implies(z3.BoolVal(False, ctx=term.ctx)):
                continue
            hull = proof.signed_hull(term)
            if hull is not None \
                    and carrier.lo <= hull.lo and hull.hi <= carrier.hi:
                continue
            guard = proof._first_unresolved_value_guard(term)
            if guard is None or leaves >= max_leaves:
                return False
            pending.append(proof._with_branch_assumption(guard, False))
            pending.append(proof._with_branch_assumption(guard, True))
            leaves += 1
        return True

    def _constant_dependencies(self, term):
        import z3

        if z3.is_bv_value(term) or z3.is_true(term) or z3.is_false(term):
            return ()
        if z3.is_const(term) and term.get_id() in self.definitions:
            return (self.definitions[term.get_id()],)
        return tuple(term.children())

    def _fold_constant(self, term):
        import z3

        if z3.is_bv_value(term) or z3.is_true(term) or z3.is_false(term):
            return term
        identity = term.get_id()
        if z3.is_const(term) and identity in self.definitions:
            return self.constants.get(self.definitions[identity].get_id())
        arguments = [self.constants.get(child.get_id())
                     for child in term.children()]
        if any(value is None for value in arguments):
            return None
        if term.decl().kind() == z3.Z3_OP_ITE:
            condition, left, right = arguments
            if z3.is_true(condition):
                return left
            if z3.is_false(condition):
                return right
            return left if left.eq(right) else None
        try:
            folded = z3.simplify(term.decl()(*arguments))
        except (TypeError, z3.Z3Exception):
            return None
        return folded if z3.is_bv_value(folded) \
            or z3.is_true(folded) or z3.is_false(folded) else None

    def constant(self, term):
        """Return a Boolean or bit-vector numeral proved by ``term``."""

        root = term.get_id()
        if root in self.constants:
            return self.constants[root]
        active = set()
        stack = [(term, False)]
        while stack:
            current, expanded = stack.pop()
            identity = current.get_id()
            self.terms[identity] = current
            if identity in self.constants:
                continue
            self._charge()
            if expanded:
                active.discard(identity)
                self.constants[identity] = self._fold_constant(current)
                continue
            dependencies = self._constant_dependencies(current)
            if any(child.get_id() in active for child in dependencies):
                # Definition cycles are not a proof of a value.  ExactFormula
                # rejects them independently; this local proof simply declines.
                self.constants[identity] = None
                continue
            active.add(identity)
            stack.append((current, True))
            stack.extend(
                (child, False) for child in reversed(dependencies)
                if child.get_id() not in self.constants)
        return self.constants[root]

    def bitvector_constant(self, term):
        import z3

        value = self.constant(term)
        return value if value is not None and z3.is_bv_value(value) else None

    @staticmethod
    def _formula_fallback(term, nonzero):
        import z3

        return (term != z3.BitVecVal(0, term.size(), ctx=term.ctx)) if nonzero else term

    def _formula_dependencies(self, term, nonzero):
        """Mixed Boolean/nonzero dependencies of one exact control formula."""

        import z3

        if nonzero:
            if z3.is_bv_value(term):
                return ()
            if self._known_nonzero(term) is not None:
                return ()
            identity = term.get_id()
            if z3.is_const(term) and identity in self.definitions:
                definition = self.definitions[identity]
                return ((definition, True),) if z3.is_bv(definition) else ()
            kind = term.decl().kind()
            if kind == z3.Z3_OP_ITE:
                return ((term.arg(0), False),
                        (term.arg(1), True), (term.arg(2), True))
            if kind in {z3.Z3_OP_SIGN_EXT, z3.Z3_OP_ZERO_EXT}:
                return ((term.arg(0), True),)
            return ()

        if z3.is_true(term) or z3.is_false(term):
            return ()
        if self._known_boolean(term) is not None:
            return ()
        identity = term.get_id()
        if z3.is_const(term) and identity in self.definitions:
            definition = self.definitions[identity]
            return ((definition, False),) if z3.is_bool(definition) else ()
        children = tuple(term.children())
        kind = term.decl().kind()
        if kind in {
                z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT,
                z3.Z3_OP_IMPLIES, z3.Z3_OP_XOR, z3.Z3_OP_ITE,
        } and children and all(z3.is_bool(child) for child in children):
            return tuple((child, False) for child in children)
        if kind in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT} \
                and len(children) == 2:
            if all(z3.is_bool(child) for child in children):
                return tuple((child, False) for child in children)
            if all(z3.is_bv(child) for child in children):
                for zero, other in (children, reversed(children)):
                    if z3.is_bv_value(zero) and zero.as_long() == 0:
                        return ((other, True),)
        return ()

    def _finish_formula(self, term, nonzero):
        """Assemble one exact control formula from normalized dependencies."""

        import z3

        if nonzero:
            if z3.is_bv_value(term):
                return z3.BoolVal(term.as_long() != 0, ctx=term.ctx)
            known = self._known_nonzero(term)
            if known is not None:
                return z3.BoolVal(known, ctx=term.ctx)
            identity = term.get_id()
            if z3.is_const(term) and identity in self.definitions:
                definition = self.definitions[identity]
                if z3.is_bv(definition):
                    return self.nonzeros.get(
                        definition.get_id(), self._formula_fallback(term, True))
            kind = term.decl().kind()
            if kind == z3.Z3_OP_ITE:
                condition = self.boolean_formulas.get(term.arg(0).get_id())
                yes = self.nonzeros.get(term.arg(1).get_id())
                no = self.nonzeros.get(term.arg(2).get_id())
                if condition is not None and yes is not None and no is not None:
                    if z3.is_true(yes) and z3.is_false(no):
                        return condition
                    if z3.is_false(yes) and z3.is_true(no):
                        return z3.Not(condition)
                    if z3.eq(yes, no):
                        return yes
                    return z3.If(condition, yes, no)
            if kind in {z3.Z3_OP_SIGN_EXT, z3.Z3_OP_ZERO_EXT}:
                result = self.nonzeros.get(term.arg(0).get_id())
                if result is not None:
                    return result
            # Arithmetic outside this structural grammar stays one exact data
            # predicate.  Propositional reasoning therefore cannot invent an
            # arithmetic consequence that the exact solver has not proved.
            return self._formula_fallback(term, True)

        if z3.is_true(term) or z3.is_false(term):
            return term
        known = self._known_boolean(term)
        if known is not None:
            return z3.BoolVal(known, ctx=term.ctx)
        # A control merge may expose an ordinary arithmetic comparison only
        # after the surrounding 0/1 SSA values have been normalized.  Its
        # operands can nevertheless already have exact finite hulls (boundary
        # premises or results proved while visiting the value DAG).  Preserve
        # that proof in the normalized formula instead of turning the
        # comparison back into an unrelated propositional atom.
        if term.decl().kind() in {
                z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT,
                z3.Z3_OP_SLT, z3.Z3_OP_SLEQ,
                z3.Z3_OP_SGT, z3.Z3_OP_SGEQ,
                z3.Z3_OP_ULT, z3.Z3_OP_ULEQ,
                z3.Z3_OP_UGT, z3.Z3_OP_UGEQ,
        } and all(z3.is_bv(child) for child in term.children()):
            # These comparisons may first become visible while normalizing a
            # 0/1 control value rather than while traversing a data value.
            # Derive their operand hulls from the same encoded DAG before
            # deciding the comparison.  A cyclic definition merely yields a
            # full hull and therefore no fact.
            for child in term.children():
                if not z3.is_bv_value(child) \
                        and child.get_id() not in self.hulls:
                    self.signed_hull(child)
        comparison = self._comparison_from_hulls(term, self.hulls)
        if comparison is not None:
            return z3.BoolVal(comparison, ctx=term.ctx)
        identity = term.get_id()
        if z3.is_const(term) and identity in self.definitions:
            definition = self.definitions[identity]
            if z3.is_bool(definition):
                return self.boolean_formulas.get(definition.get_id(), term)
        children = tuple(term.children())
        kind = term.decl().kind()
        if kind in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT} \
                and len(children) == 2 \
                and all(z3.is_bv(child) for child in children):
            for zero, other in (children, reversed(children)):
                if z3.is_bv_value(zero) and zero.as_long() == 0:
                    result = self.nonzeros.get(other.get_id())
                    if result is not None:
                        return (result if kind == z3.Z3_OP_DISTINCT else
                                z3.Not(result))
        values = [self.boolean_formulas.get(child.get_id())
                  for child in children]
        if any(value is None for value in values):
            return term
        if kind == z3.Z3_OP_AND:
            return z3.And(values)
        if kind == z3.Z3_OP_OR:
            return z3.Or(values)
        if kind == z3.Z3_OP_NOT and len(values) == 1:
            return z3.Not(values[0])
        if kind == z3.Z3_OP_IMPLIES and len(values) == 2:
            return z3.Implies(values[0], values[1])
        if kind == z3.Z3_OP_XOR and len(values) == 2:
            return z3.Xor(values[0], values[1])
        if kind == z3.Z3_OP_ITE and len(values) == 3:
            return z3.If(values[0], values[1], values[2])
        if kind == z3.Z3_OP_EQ and len(values) == 2:
            return values[0] == values[1]
        if kind == z3.Z3_OP_DISTINCT:
            return z3.Distinct(values)
        return term

    def _control_formula(self, term, nonzero):
        """Normalize a mixed Boolean/0-or-1 SSA DAG without recursion."""

        cache = self.nonzeros if nonzero else self.boolean_formulas
        root = term.get_id()
        if root in cache:
            return cache[root]
        active = set()
        stack = [(term, nonzero, False)]
        while stack:
            current, current_nonzero, expanded = stack.pop()
            current_cache = (self.nonzeros if current_nonzero else
                             self.boolean_formulas)
            identity = current.get_id()
            self.terms[identity] = current
            key = (current_nonzero, identity)
            if identity in current_cache:
                continue
            self._charge()
            if expanded:
                active.discard(key)
                current_cache[identity] = self._finish_formula(
                    current, current_nonzero)
                continue
            dependencies = self._formula_dependencies(
                current, current_nonzero)
            if key in active or any(
                    (mode, child.get_id()) in active
                    for child, mode in dependencies):
                current_cache[identity] = self._formula_fallback(
                    current, current_nonzero)
                continue
            active.add(key)
            stack.append((current, current_nonzero, True))
            for child, mode in reversed(dependencies):
                child_cache = self.nonzeros if mode else self.boolean_formulas
                if child.get_id() not in child_cache:
                    stack.append((child, mode, False))
        return cache[root]

    def nonzero(self, term):
        """Expose exact Boolean structure hidden by a bit-vector zero test."""

        import z3

        if not z3.is_bv(term):
            raise TypeError("a nonzero proof needs one bit-vector")
        return self._control_formula(term, True)

    def boolean_formula(self, term):
        """Expose exact Boolean structure hidden behind 0/1 SSA values."""

        import z3

        if not z3.is_bool(term):
            raise TypeError("a Boolean normalization needs one Boolean term")
        return self._control_formula(term, False)

    @staticmethod
    def _full_hull(term):
        lo, hi = _signed_bounds(term.size())
        return ranges.Range(lo, hi)

    def _definition_bv_constant(self, term):
        """Follow only SSA aliases to a literal bit-vector.

        The encoder has already simplified closed arithmetic before naming it.
        This deliberately does not open a data cone: it is used to recognize a
        literal mask or shift amount that can bound its parent independently of
        the other operand.
        """

        import z3

        current = term
        seen = set()
        while True:
            if z3.is_bv_value(current):
                return current
            identity = current.get_id()
            if identity in seen or not z3.is_const(current):
                return None
            seen.add(identity)
            current = self.definitions.get(identity)
            if current is None:
                return None

    def _leading_bit_run(self, term):
        """Summarize a shared concat DAG without flattening repeated prefixes.

        A row owns its source term and the repeated leading one-bit expression.
        Visit only the leading run; an unrelated suffix is never expanded.
        These are syntactic identities, independent of ranges or assumptions.
        """
        import z3

        known = self.concat_prefixes
        pending = [(term, 0, None, 0)]
        while pending:
            node, index, first, width = pending.pop()
            key = node.get_id()
            if key in known:
                continue
            self._charge()
            if node.decl().kind() != z3.Z3_OP_CONCAT:
                known[key] = node, (node if node.size() == 1 else None), int(node.size() == 1)
                continue
            child = node.arg(index)
            if child.get_id() not in known:
                pending.extend(((node, index, first, width), (child, 0, None, 0)))
                continue
            _, bit, count = known[child.get_id()]
            if bit is None or first is not None and not first.eq(bit):
                known[key] = node, first, width
                continue
            first = bit if first is None else first
            width += count
            if count < child.size() or index + 1 == node.num_args():
                known[key] = node, first, width
            else:
                pending.append((node, index + 1, first, width))
        _, first, width = known[term.get_id()]
        return first, width

    def _concat_prefix_hull(self, term):
        """Signed hull of a concat with fixed or repeated high-order bits.

        Once the high bits are a numeral, every possible suffix occupies one
        contiguous signed interval. This structural bound also preserves
        repeated sign-bit relationships that independent child ranges lose.
        """

        import z3

        if term.decl().kind() != z3.Z3_OP_CONCAT or term.num_args() < 2:
            return None
        prefix = self._definition_bv_constant(term.arg(0))
        if prefix is None:
            # Simplification flattens SignExt into repeated high sign bits.
            # Concat(b, ..., b, suffix) is a sign extension of (b, suffix),
            # regardless of any relation between b and the suffix. Recognize
            # that structural fact without reopening the suffix's datapath or
            # assuming input/state ranges. Different high bits do not qualify.
            first, prefix_width = self._leading_bit_run(term)
            if first is None:
                return None
            suffix_width = term.size() - prefix_width
            limit = 1 << suffix_width
            return ranges.Range(-limit, limit - 1)
        suffix_width = term.size() - prefix.size()
        width = term.size()
        low = prefix.as_long() << suffix_width
        high = low | ((1 << suffix_width) - 1)
        sign = 1 << (width - 1)
        if low >= sign:
            modulus = 1 << width
            low -= modulus
            high -= modulus
        return ranges.Range(low, high)

    @staticmethod
    def _is_floor_division(term, numerator, denominator):
        """Whether ``term`` is the encoder's exact Python ``//`` image."""

        import z3

        if not all(z3.is_bv(value)
                   for value in (term, numerator, denominator)) \
                or term.size() != numerator.size() \
                or denominator.size() != numerator.size():
            return False
        # Recognize the actual constructor, including its power-of-two form.
        # Retain the old truncation/remainder form for external formulas too.
        from .bitvectors import _floor_div

        if z3.eq(term, _floor_div(numerator, denominator)):
            return True
        zero = z3.BitVecVal(0, numerator.size(), ctx=numerator.ctx)
        one = z3.BitVecVal(1, numerator.size(), ctx=numerator.ctx)
        quotient = numerator / denominator
        expected = z3.If(
            z3.Or(
                z3.SRem(numerator, denominator) == zero,
                (numerator < zero) == (denominator < zero),
            ),
            quotient,
            quotient - one,
        )
        return z3.eq(term, expected)

    def _floor_mod_hull(self, term):
        """Range of the encoder's exact Python ``%`` image by a constant.

        Python's remainder has the divisor's sign and magnitude below the
        divisor's.  Recognizing the complete encoding is an independent
        theorem about the emitted term; it does not trust the abstract range
        proposed by the planner.
        """

        import z3

        if term.decl().kind() == z3.Z3_OP_BSMOD:
            literal = self._definition_bv_constant(term.arg(1))
            if literal is None or literal.as_signed_long() == 0:
                return None
            value = literal.as_signed_long()
            return (ranges.Range(0, value - 1) if value > 0 else
                    ranges.Range(value + 1, 0))
        # Retain recognition of algebraic floor remainders in externally
        # supplied formulas; the encoder now emits BSMOD directly.
        if term.decl().kind() != z3.Z3_OP_BSUB or term.num_args() != 2:
            return None
        numerator, product = term.children()
        if product.decl().kind() != z3.Z3_OP_BMUL \
                or product.num_args() != 2:
            return None
        for divisor, quotient in (
                product.children(), reversed(product.children())):
            literal = self._definition_bv_constant(divisor)
            if literal is None or literal.as_signed_long() == 0 \
                    or not self._is_floor_division(
                        quotient, numerator, divisor):
                continue
            value = literal.as_signed_long()
            return (ranges.Range(0, value - 1) if value > 0 else
                    ranges.Range(value + 1, 0))
        return None

    def _ssa_value(self, term):
        """Follow same-sort SSA aliases without rewriting an expression DAG."""

        import z3

        current = term
        seen = set()
        while z3.is_const(current) and current.get_id() in self.definitions:
            identity = current.get_id()
            if identity in seen:
                break
            seen.add(identity)
            candidate = self.definitions[identity]
            if candidate.sort() != current.sort():
                break
            current = candidate
        return current

    def _normal_data_condition(self, term):
        """Remove control conjuncts entailed by normal completion.

        A lowered assignment is guarded by both source data and the path that
        reaches it.  For a closure proof, the latter is already an assumption;
        retaining it hides simple data predicates such as one sign-bit test.
        This routine performs propositional elimination only.  It never uses
        an interval fact to choose a source branch.
        """

        import z3

        pending = [term]
        parts = []
        seen = set()
        while pending:
            current = pending.pop()
            identity = current.get_id()
            if identity in seen:
                continue
            seen.add(identity)
            if self.implies(current):
                continue
            definition = (self.definitions.get(identity)
                          if z3.is_const(current) else None)
            expanded = definition if definition is not None else current
            if z3.is_bool(expanded) \
                    and expanded.decl().kind() == z3.Z3_OP_AND:
                pending.extend(reversed(expanded.children()))
                continue
            parts.append(self.boolean_formula(current))
        if not parts:
            return z3.BoolVal(True, ctx=term.ctx)
        return z3.simplify(z3.And(parts))

    @staticmethod
    def _zero_test(term):
        """Return ``(value, nonzero)`` for an exact zero comparison."""

        import z3

        negated = False
        current = term
        if current.decl().kind() == z3.Z3_OP_NOT and current.num_args() == 1:
            negated = True
            current = current.arg(0)
        kind = current.decl().kind()
        if kind not in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT} \
                or current.num_args() != 2:
            return None
        left, right = current.arg(0), current.arg(1)
        if z3.is_bv_value(left) and left.as_long() == 0:
            value = right
        elif z3.is_bv_value(right) and right.as_long() == 0:
            value = left
        else:
            return None
        nonzero = kind == z3.Z3_OP_DISTINCT
        return value, nonzero != negated

    def _signed_wrap_ite_hull(self, term, memo):
        """Recognize the exact two's-complement wrap relation.

        Python models commonly spell a signed-width conversion as masking to
        ``[0, 2**b-1]`` and conditionally subtracting ``2**b`` when bit
        ``b-1`` is set.  The two branches separately have a twice-too-wide
        interval; their shared bit predicate proves the exact signed carrier.
        Recognition is over the encoded relation, not helper names or source
        layout, and is therefore valid for inlined and hand-written forms.
        """

        import z3

        if term.decl().kind() != z3.Z3_OP_ITE:
            return None
        yes, no = term.arg(1), term.arg(2)
        yes_value, no_value = self._ssa_value(yes), self._ssa_value(no)

        def subtraction(candidate, base):
            if candidate.decl().kind() != z3.Z3_OP_BSUB:
                return None
            if not self._ssa_value(candidate.arg(0)).eq(self._ssa_value(base)):
                return None
            constant = self.bitvector_constant(candidate.arg(1))
            return None if constant is None else constant.as_long()

        yes_modulus = subtraction(yes_value, no)
        no_modulus = subtraction(no_value, yes)
        if yes_modulus is not None:
            base, modulus, subtract_when_true = no, yes_modulus, True
        elif no_modulus is not None:
            base, modulus, subtract_when_true = yes, no_modulus, False
        else:
            return None
        if modulus <= 1 or modulus & (modulus - 1):
            return None
        base_hull = memo.get(base.get_id())
        if base_hull is None:
            base_hull = memo.get(self._ssa_value(base).get_id())
        if base_hull is None or base_hull.lo < 0 \
                or base_hull.hi >= modulus:
            return None

        tested = self._zero_test(self._normal_data_condition(term.arg(0)))
        if tested is None:
            return None
        subject, nonzero = tested
        subject = self._ssa_value(subject)
        sign = modulus // 2
        kind = subject.decl().kind()
        if kind == z3.Z3_OP_EXTRACT:
            high, low = subject.decl().params()
            matches = high == low == sign.bit_length() - 1 \
                and self._ssa_value(subject.arg(0)).eq(
                    self._ssa_value(base))
        elif kind == z3.Z3_OP_BAND:
            matches = False
            for index in (0, 1):
                candidate = self._ssa_value(subject.arg(index))
                other = subject.arg(1 - index)
                mask = self.bitvector_constant(other)
                if candidate.eq(self._ssa_value(base)) \
                        and mask is not None and mask.as_long() == sign:
                    matches = True
                    break
        else:
            return None
        if not matches or nonzero != subtract_when_true:
            return None
        return ranges.Range(-sign, sign - 1)

    def _hull_dependencies(self, term, *, coarse=False):
        import z3

        if z3.is_bool(term):
            identity = term.get_id()
            if z3.is_const(term) and identity in self.definitions:
                return (self.definitions[identity],)
            if term.decl().kind() in {
                    z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT,
                    z3.Z3_OP_ITE,
                    z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT,
                    z3.Z3_OP_SLT, z3.Z3_OP_SLEQ,
                    z3.Z3_OP_SGT, z3.Z3_OP_SGEQ,
                    z3.Z3_OP_ULT, z3.Z3_OP_ULEQ,
                    z3.Z3_OP_UGT, z3.Z3_OP_UGEQ,
            }:
                return tuple(term.children())
            return ()
        if not z3.is_bv(term) or z3.is_bv_value(term):
            return ()
        identity = term.get_id()
        selection = self.array_selections.get(identity) if coarse else None
        if selection is not None:
            if selection.array in self.array_hulls:
                return ()
            return tuple(value for _guard, value, _intrinsic in
                         self._selection_cases(selection.array)
                         if value is not None)
        if z3.is_const(term) and identity in self.definitions:
            return (self.definitions[identity],)
        kind = term.decl().kind()
        if kind in {z3.Z3_OP_SIGN_EXT, z3.Z3_OP_ZERO_EXT}:
            return (term.arg(0),)
        if kind == z3.Z3_OP_CONCAT:
            # Keep narrower suffix ranges (notably literal lookup tables).
            return tuple(term.children())
        if kind == z3.Z3_OP_EXTRACT:
            # A low-bit truncation may merely remove redundant sign bits.
            # Share the ordinary memoized operand proof; non-low slices keep
            # their intrinsic carrier without traversing an unrelated circuit.
            return (term.arg(0),) if term.decl().params()[1] == 0 else ()
        if kind == z3.Z3_OP_ITE:
            condition = self._known_boolean(term.arg(0))
            if condition is True:
                return (term.arg(1),)
            if condition is False:
                return (term.arg(2),)
            # A coarse interval needs only the arms.  The precise public proof
            # additionally follows the condition so it can select an arm.
            return ((term.arg(1), term.arg(2)) if coarse else
                    (term.arg(0), term.arg(1), term.arg(2)))
        if kind in {
            z3.Z3_OP_BADD, z3.Z3_OP_BSUB, z3.Z3_OP_BMUL,
            z3.Z3_OP_BSDIV, z3.Z3_OP_BSREM,
            z3.Z3_OP_BUDIV, z3.Z3_OP_BUREM,
            z3.Z3_OP_BOR, z3.Z3_OP_BXOR,
        }:
            return tuple(term.children())
        if kind == z3.Z3_OP_BAND:
            width = term.size()
            if any(
                    (mask := self._definition_bv_constant(term.arg(index)))
                    is not None and mask.as_long() < (1 << (width - 1))
                    for index in range(term.num_args())):
                return ()
            return tuple(term.children())
        if kind in {z3.Z3_OP_BNEG, z3.Z3_OP_BNOT}:
            return (term.arg(0),)
        if kind in {z3.Z3_OP_BSHL, z3.Z3_OP_BASHR, z3.Z3_OP_BLSHR}:
            return term.arg(0), term.arg(1)
        return ()

    def _selection_cases(self, array):
        if array not in self.array_cases:
            self.array_cases[array] = array.range_cases(guarded=False)
        return self.array_cases[array]

    def _selection_hull(self, selection):
        """Prove the union of source values once, including invalid-read zero.

        Ignoring indices and store guards gives a sufficient range proof, not
        a restriction on executions. A narrow obligation still has the normal
        precise term proof and exact solver fallback.
        """
        array = selection.array
        if array not in self.array_hulls:
            hull = ranges.Range(0, 0)
            for _guard, value, intrinsic in self._selection_cases(array):
                part = (ranges.Range(*intrinsic) if intrinsic is not None else
                        self.coarse_hulls.get(value.get_id())
                        or self._full_hull(value))
                hull = hull.join(part)
            self.array_hulls[array] = hull
        return self.array_hulls[array]

    def _ordered_select_hull(self, term, memo):
        """The range of a syntactic min/max, including literal clamps.

        Both arms must be the compared operands, not merely similarly bounded
        expressions. This uses the selector's relation without assuming which
        arm executes. Unsigned comparisons use unsigned hulls before converting
        the result back to the signed carrier used by this range analysis.
        """
        import z3

        condition, yes, no = term.children()
        while z3.is_not(condition):
            condition, yes, no = condition.arg(0), no, yes
        less = {z3.Z3_OP_SLT, z3.Z3_OP_SLEQ, z3.Z3_OP_ULT, z3.Z3_OP_ULEQ}
        greater = {z3.Z3_OP_SGT, z3.Z3_OP_SGEQ, z3.Z3_OP_UGT, z3.Z3_OP_UGEQ}
        kind = condition.decl().kind()
        if kind not in less | greater or condition.num_args() != 2:
            return None
        left, right = (self._ssa_value(arg) for arg in condition.children())
        y, n = self._ssa_value(yes), self._ssa_value(no)
        if y.eq(left) and n.eq(right):
            minimum = kind in less
        elif y.eq(right) and n.eq(left):
            minimum = kind in greater
        else:
            return None
        a, b = memo.get(yes.get_id()), memo.get(no.get_id())
        if a is None or b is None:
            return None
        choose = min if minimum else max
        if kind in {z3.Z3_OP_ULT, z3.Z3_OP_ULEQ, z3.Z3_OP_UGT, z3.Z3_OP_UGEQ}:
            modulus = 1 << term.size()
            a, b = (_unsigned_hull(hull, term.size()) for hull in (a, b))
            lo, hi = choose(a.lo, b.lo), choose(a.hi, b.hi)
            if hi < modulus // 2:
                return ranges.Range(lo, hi)
            if lo >= modulus // 2:
                return ranges.Range(lo - modulus, hi - modulus)
            return self._full_hull(term)
        return ranges.Range(choose(a.lo, b.lo), choose(a.hi, b.hi))

    def _guard_range_node(self, condition, truth):
        """Share necessary-fact DAGs across guards with a common prefix.

        Only true AND, false OR, negation and owned SSA aliases are opened.
        The persistent subject index lets a later arm skip unrelated prefixes;
        it does not copy a growing variable-to-range dictionary at every node.
        Cyclic aliases contribute no facts along their back edge.
        """
        import z3
        from .dependency_sets import Support

        key = condition.get_id(), truth
        pending, active = [(condition, truth, None)], set()
        while pending:
            term, holds, children = pending.pop()
            identity = term.get_id(), holds
            if identity in self.guard_nodes:
                continue
            self._charge()
            self.terms[term.get_id()] = term
            if children is None:
                alias = self.definitions.get(term.get_id()) if z3.is_const(term) else None
                if alias is not None and alias.sort() == term.sort():
                    children = ((alias, holds),)
                elif z3.is_not(term):
                    children = ((term.arg(0), not holds),)
                elif z3.is_and(term) and holds or z3.is_or(term) and not holds:
                    children = tuple((child, holds) for child in term.children())
                else:
                    fact = self._literal_guard_range(term, holds)
                    support = Support() if fact is None else Support.atom(fact[0])
                    self.guard_nodes[identity] = support, (), fact
                    continue
                active.add(identity)
                children = tuple((child, value) for child, value in children
                                 if (child.get_id(), value) not in active)
                pending.append((term, holds, children))
                pending.extend((child, value, None) for child, value in reversed(children))
                continue
            active.discard(identity)
            support = Support()
            keys = tuple((child.get_id(), value) for child, value in children)
            for child in keys:
                support |= self.guard_nodes[child][0]
            self.guard_nodes[identity] = support, keys, None
        return key

    def _literal_guard_range(self, part, holds):
        """One immediate literal comparison, represented in signed halves."""
        import z3

        relations = {z3.Z3_OP_EQ: 'eq', z3.Z3_OP_DISTINCT: 'ne',
            z3.Z3_OP_SLT: 'lt', z3.Z3_OP_SLEQ: 'le', z3.Z3_OP_SGT: 'gt', z3.Z3_OP_SGEQ: 'ge',
            z3.Z3_OP_ULT: 'lt', z3.Z3_OP_ULEQ: 'le', z3.Z3_OP_UGT: 'gt', z3.Z3_OP_UGEQ: 'ge'}
        unsigned = {z3.Z3_OP_ULT, z3.Z3_OP_ULEQ, z3.Z3_OP_UGT, z3.Z3_OP_UGEQ}
        kind = part.decl().kind()
        if kind not in relations or part.num_args() != 2:
            return None
        left, right = (self._ssa_value(arg) for arg in part.children())
        if not z3.is_bv(left) or not z3.is_bv(right):
            return None
        relation = relations[kind]
        if z3.is_bv_value(left):
            left, right = right, left
            relation = {'lt': 'gt', 'le': 'ge', 'gt': 'lt', 'ge': 'le'}.get(relation, relation)
        if not z3.is_bv_value(right) or z3.is_bv_value(left):
            return None
        if not holds:
            relation = {'lt': 'ge', 'le': 'gt', 'gt': 'le', 'ge': 'lt', 'eq': 'ne', 'ne': 'eq'}[relation]
        modulus = 1 << left.size()
        lo, hi = ((0, modulus - 1) if kind in unsigned else _signed_bounds(left.size()))
        number = right.as_long() if kind in unsigned else right.as_signed_long()
        allowed = {'lt': ((lo, number - 1),), 'le': ((lo, number),),
            'gt': ((number + 1, hi),), 'ge': ((number, hi),),
            'eq': ((number, number),), 'ne': ((lo, number - 1), (number + 1, hi))}[relation]
        pieces = []
        for low, high in allowed:
            low, high = max(lo, low), min(hi, high)
            if kind in unsigned:
                if low <= min(high, modulus // 2 - 1):
                    pieces.append(ranges.Range(low, min(high, modulus // 2 - 1)))
                if max(low, modulus // 2) <= high:
                    pieces.append(ranges.Range(max(low, modulus // 2) - modulus, high - modulus))
            elif low <= high:
                pieces.append(ranges.Range(low, high))
        self.terms[left.get_id()] = left
        full = (ranges.Range(-modulus // 2, -1), ranges.Range(0, modulus // 2 - 1))
        return left.get_id(), self._intersect_guard_ranges(full, pieces)

    @staticmethod
    def _intersect_guard_ranges(current, pieces):
        refined = []
        for half in current:
            intersections = [(max(half.lo, piece.lo), min(half.hi, piece.hi))
                             for piece in pieces if max(half.lo, piece.lo) <= min(half.hi, piece.hi)]
            if intersections:
                refined.append(ranges.Range(min(low for low, _ in intersections),
                                            max(high for _, high in intersections)))
        return tuple(refined)

    def _direct_guard_ranges(self, condition, truth, subjects=None):
        """Project necessary facts onto the arm, never enumerate path products.

        Conditional ranges are cached per shared node and subject, not as
        unconditional hulls. Unknown and unrelated tests contribute no fact.
        Each range keeps at most one interval per signed half.
        """
        root = self._guard_range_node(condition, truth)
        support = self.guard_nodes[root][0]
        out = {}
        for subject in support if subjects is None else subjects:
            if subject not in support:
                continue
            pending = [(root, False)]
            while pending:
                node, expanded = pending.pop()
                key = node, subject
                if key in self.guard_ranges:
                    continue
                self._charge()
                _, children, literal = self.guard_nodes[node]
                if literal is not None:
                    self.guard_ranges[key] = literal[1]
                    continue
                relevant = tuple(child for child in children
                                 if subject in self.guard_nodes[child][0])
                if not expanded:
                    pending.append((node, True))
                    pending.extend((child, False) for child in reversed(relevant))
                    continue
                result = self.guard_ranges[relevant[0], subject]
                for child in relevant[1:]:
                    result = self._intersect_guard_ranges(result, self.guard_ranges[child, subject])
                self.guard_ranges[key] = result
            out[subject] = self.guard_ranges[root, subject]
        return out

    def _guarded_arm_hull(self, arm, condition, truth, memo):
        """Refine a direct value/cast plus literal offset under its own guard.

        Do not re-evaluate an arithmetic cone per path. Unknown forms keep the
        ordinary hull, and offsets that may wrap retain the full BV carrier.
        Independent guard facts refine separately, not via a Cartesian product.
        """
        import z3

        best = memo.get(arm.get_id()) or self._full_hull(arm)
        base, offset = self._ssa_value(arm), 0
        kind = base.decl().kind()
        if kind in {z3.Z3_OP_BADD, z3.Z3_OP_BSUB} and base.num_args() == 2:
            left, right = base.children()
            literal = self._definition_bv_constant(right)
            if literal is None and kind == z3.Z3_OP_BADD:
                left, right = right, left
                literal = self._definition_bv_constant(right)
            if literal is not None:
                base = self._ssa_value(left)
                offset = literal.as_signed_long() * (-1 if kind == z3.Z3_OP_BSUB else 1)
        if z3.is_bv_value(base):
            return best
        views = [(base, False)]
        kind = base.decl().kind()
        if kind in {z3.Z3_OP_ZERO_EXT, z3.Z3_OP_SIGN_EXT}:
            views.append((self._ssa_value(base.arg(0)), kind == z3.Z3_OP_ZERO_EXT
                          and base.size() > base.arg(0).size()))
        elif (kind == z3.Z3_OP_CONCAT and base.num_args() == 2
                and z3.is_bv_value(base.arg(0)) and base.arg(0).as_long() == 0):
            views.append((self._ssa_value(base.arg(1)), True))
        facts = self._direct_guard_ranges(condition, truth, (subject.get_id() for subject, _ in views))
        for subject, zero_extend in views:
            hull = memo.get(subject.get_id()) or self._full_hull(subject)
            result = None
            for piece in facts.get(subject.get_id(), ()):
                low, high = max(hull.lo, piece.lo), min(hull.hi, piece.hi)
                if low > high:
                    continue
                narrowed = ranges.Range(low, high)
                if zero_extend:
                    narrowed = _unsigned_hull(narrowed, subject.size())
                narrowed = (_no_wrap_hull(arm.size(), (narrowed.lo + offset, narrowed.hi + offset))
                            or self._full_hull(arm))
                result = narrowed if result is None else result.join(narrowed)
            # An impossible arm needs no new bottom representation. Keep
            # the old overapproximation rather than infer a global fact.
            if result is not None:
                low, high = max(best.lo, result.lo), min(best.hi, result.hi)
                if low <= high:
                    best = ranges.Range(low, high)
        return best

    def _finish_hull(self, term, *, coarse=False):
        import z3

        memo = self.coarse_hulls if coarse else self.hulls

        if not z3.is_bv(term):
            return None
        if z3.is_bv_value(term):
            value = term.as_signed_long()
            return ranges.Range(value, value)
        identity = term.get_id()
        selection = self.array_selections.get(identity) if coarse else None
        if selection is not None:
            return self._selection_hull(selection)
        if z3.is_const(term) and identity in self.definitions:
            return memo.get(self.definitions[identity].get_id()) \
                or self._full_hull(term)

        kind = term.decl().kind()
        if kind == z3.Z3_OP_SIGN_EXT:
            return memo.get(term.arg(0).get_id()) \
                or self._full_hull(term)
        if kind == z3.Z3_OP_ZERO_EXT:
            child = term.arg(0)
            hull = memo.get(child.get_id()) or self._full_hull(child)
            return (hull if child.size() == term.size()
                    else _unsigned_hull(hull, child.size()))
        if kind == z3.Z3_OP_CONCAT:
            low = high = 0
            for child in term.children():
                part = memo.get(child.get_id()) or self._full_hull(child)
                part = _unsigned_hull(part, child.size())
                low = (low << child.size()) | part.lo
                high = (high << child.size()) | part.hi
            modulus = 1 << term.size()
            hull = (ranges.Range(low, high) if high < modulus // 2 else
                    ranges.Range(low - modulus, high - modulus)
                    if low >= modulus // 2 else self._full_hull(term))
            proved = self._concat_prefix_hull(term)
            if proved is not None:
                return ranges.Range(max(hull.lo, proved.lo), min(hull.hi, proved.hi))
            return hull
        if kind == z3.Z3_OP_EXTRACT:
            if term.decl().params()[1] == 0:
                hull = memo.get(term.arg(0).get_id())
                low, high = _signed_bounds(term.size())
                if hull is not None and low <= hull.lo <= hull.hi <= high:
                    return hull
            return self._full_hull(term)
        if kind in {z3.Z3_OP_BSUB, z3.Z3_OP_BSMOD}:
            modulo = self._floor_mod_hull(term)
            if modulo is not None:
                return modulo
        if kind == z3.Z3_OP_ITE:
            wrapped = self._signed_wrap_ite_hull(term, memo)
            if wrapped is not None:
                return wrapped
            condition_term = term.arg(0)
            condition = (self._known_boolean(condition_term) if coarse else
                         self._boolean_value(condition_term))
            if condition is None and not coarse:
                if self.implies(condition_term):
                    condition = True
                elif self.implies(condition_term, False):
                    condition = False
            if coarse and condition is None:
                condition = self._comparison_from_hulls(
                    condition_term, memo)
            if condition is not None:
                arm = term.arg(1) if condition else term.arg(2)
                return memo.get(arm.get_id()) or self._full_hull(term)
            ordered = self._ordered_select_hull(term, memo)
            if ordered is not None:
                return ordered
            left = self._guarded_arm_hull(term.arg(1), condition_term, True, memo)
            right = self._guarded_arm_hull(term.arg(2), condition_term, False, memo)
            return left.join(right)

        if not coarse:
            constant = self.bitvector_constant(term)
            if constant is not None:
                value = constant.as_signed_long()
                return ranges.Range(value, value)

        width = term.size()
        binary = {
            z3.Z3_OP_BADD: lambda left, right: (
                left.lo + right.lo, left.hi + right.hi),
            z3.Z3_OP_BSUB: lambda left, right: (
                left.lo - right.hi, left.hi - right.lo),
            z3.Z3_OP_BMUL: lambda left, right: tuple(
                a * b for a in (left.lo, left.hi)
                for b in (right.lo, right.hi)),
        }
        if kind in binary:
            # Z3 flattens associative addition/multiplication into n-ary
            # nodes. Ignoring later operands would prove an invalid range.
            proved = memo.get(term.arg(0).get_id())
            for argument in term.children()[1:]:
                right = memo.get(argument.get_id())
                if proved is None or right is None:
                    proved = None
                    break
                proved = (_no_wrap_hull(width, binary[kind](proved, right))
                          or self._full_hull(term))
            if proved is not None:
                return proved

        if kind in {z3.Z3_OP_BUDIV, z3.Z3_OP_BUREM}:
            numerator = memo.get(term.arg(0).get_id())
            denominator = memo.get(term.arg(1).get_id())
            if numerator is not None and denominator is not None:
                return _unsigned_quotient_hull(numerator, denominator, width,
                    remainder=kind == z3.Z3_OP_BUREM)

        if kind in {z3.Z3_OP_BSDIV, z3.Z3_OP_BSREM}:
            numerator = memo.get(term.arg(0).get_id())
            denominator = memo.get(term.arg(1).get_id())
            divisor = (denominator.lo if denominator is not None
                       and denominator.lo == denominator.hi != 0 else None)
            if numerator is not None and divisor is not None:
                if kind == z3.Z3_OP_BSDIV:
                    def trunc(value):
                        magnitude = abs(value) // abs(divisor)
                        return (-magnitude if (value < 0) != (divisor < 0)
                                else magnitude)

                    values = (trunc(numerator.lo), trunc(numerator.hi))
                    proved = _no_wrap_hull(width, values)
                    if proved is not None:
                        return proved
                else:
                    magnitude = abs(divisor) - 1
                    return ranges.Range(
                        -magnitude if numerator.lo < 0 else 0,
                        magnitude if numerator.hi > 0 else 0,
                    )

        if kind == z3.Z3_OP_BNOT:
            value = memo.get(term.arg(0).get_id())
            if value is not None:
                # In the signed carrier ~x = -x-1, including both extrema.
                # Unlike negation this is a decreasing bijection without wrap.
                return ranges.Range(-value.hi - 1, -value.lo - 1)

        if kind == z3.Z3_OP_BNEG:
            value = memo.get(term.arg(0).get_id())
            if value is not None:
                proved = _no_wrap_hull(width, (-value.hi, -value.lo))
                if proved is not None:
                    return proved

        if kind == z3.Z3_OP_BAND:
            # With the sign bit clear, every possible result is a
            # nonnegative subset of the mask bits, regardless of the other arm.
            bounds = []
            for index in range(term.num_args()):
                argument = term.arg(index)
                direct = self._definition_bv_constant(argument)
                constant = (direct if direct is not None or coarse else
                            self.bitvector_constant(argument))
                mask = (argument.as_long() if z3.is_bv_value(argument) else
                        constant.as_long() if constant is not None else None)
                if mask is not None and mask < (1 << (width - 1)):
                    bounds.append(mask)
                mask_hull = memo.get(term.arg(index).get_id())
                if mask_hull is not None \
                        and 0 <= mask_hull.lo <= mask_hull.hi \
                        and mask_hull.hi < (1 << (width - 1)):
                    bounds.append(mask_hull.hi)
            # Each operand independently bounds the result. Use their
            # intersection, not the first bound encountered: a wide datapath
            # on the left must not hide the small mask proved on the right.
            if bounds:
                return ranges.Range(0, min(bounds))

        if kind in {z3.Z3_OP_BOR, z3.Z3_OP_BXOR}:
            parts = [memo.get(argument.get_id()) for argument in term.children()]
            if kind == z3.Z3_OP_BOR:
                negative = [part.lo for part in parts if part is not None and part.hi < 0]
                if negative:
                    # A set sign bit stays set under OR. Setting more low bits
                    # can only increase that operand's signed value, up to -1.
                    return ranges.Range(max(negative), -1)
            if all(part is not None and part.lo >= 0 for part in parts):
                # For nonnegative bit patterns, OR and XOR keep the sign bit
                # clear, are no larger than the operand sum, and cannot set a
                # bit above either operand's highest possible bit.  The last
                # bound is important for a loop that packs several fields into
                # the same fixed-width word: adding their independent interval
                # maxima would count the word width once per iteration.
                _signed_lo, signed_hi = _signed_bounds(width)
                bit_ceiling = (1 << max(part.hi.bit_length() for part in parts)) - 1
                return ranges.Range(
                    0, min(signed_hi, sum(part.hi for part in parts), bit_ceiling))

        if kind in {z3.Z3_OP_BSHL, z3.Z3_OP_BASHR, z3.Z3_OP_BLSHR}:
            value = memo.get(term.arg(0).get_id())
            amount_term = term.arg(1)
            amount_hull = memo.get(amount_term.get_id())
            direct_amount = self._definition_bv_constant(amount_term)
            amount_constant = (
                direct_amount if direct_amount is not None or coarse else
                self.bitvector_constant(amount_term))
            shift = (
                amount_term.as_long() if z3.is_bv_value(amount_term) else
                amount_constant.as_long() if amount_constant is not None else
                amount_hull.lo if amount_hull is not None
                and amount_hull.lo == amount_hull.hi
                and amount_hull.lo >= 0 else None)
            if value is not None and shift is not None:
                if shift >= width:
                    if kind in {z3.Z3_OP_BSHL, z3.Z3_OP_BLSHR}:
                        return ranges.Range(0, 0)
                    if value.hi < 0:
                        return ranges.Range(-1, -1)
                    if value.lo >= 0:
                        return ranges.Range(0, 0)
                    return ranges.Range(-1, 0)
                if kind == z3.Z3_OP_BSHL:
                    proved = _no_wrap_hull(
                        width, (value.lo << shift, value.hi << shift))
                    if proved is not None:
                        return proved
                elif kind == z3.Z3_OP_BASHR:
                    return ranges.Range(value.lo >> shift, value.hi >> shift)
                elif shift == 0:
                    return value
                elif value.lo >= 0:
                    return ranges.Range(value.lo >> shift, value.hi >> shift)
                else:
                    return ranges.Range(0, ((1 << width) - 1) >> shift)
            if value is not None and amount_hull is not None \
                    and 0 <= amount_hull.lo <= amount_hull.hi < width:
                if kind == z3.Z3_OP_BSHL and value.lo >= 0:
                    proved = _no_wrap_hull(width, (
                        value.lo << amount_hull.lo,
                        value.hi << amount_hull.hi,
                    ))
                    if proved is not None:
                        return proved
                elif kind == z3.Z3_OP_BLSHR and value.lo >= 0:
                    return ranges.Range(
                        value.lo >> amount_hull.hi,
                        value.hi >> amount_hull.lo,
                    )
            if value is not None and amount_hull is not None:
                # A bit-vector shift reads its count as unsigned.  A negative
                # signed count is therefore at least the term width and gives
                # zero (or sign fill for arithmetic right shift).  For the
                # remaining in-width counts the extrema lie at the rectangle's
                # corners.  Decline the proof when left shift could wrap.
                valid_lo = max(0, amount_hull.lo)
                valid_hi = min(width - 1, amount_hull.hi)
                values = []
                if valid_lo <= valid_hi:
                    if kind == z3.Z3_OP_BSHL:
                        values.extend(
                            endpoint << amount
                            for endpoint in (value.lo, value.hi)
                            for amount in (valid_lo, valid_hi))
                    elif kind == z3.Z3_OP_BASHR or value.lo >= 0:
                        values.extend(
                            endpoint >> amount
                            for endpoint in (value.lo, value.hi)
                            for amount in (valid_lo, valid_hi))
                    else:
                        # Logical shift of a possibly negative bit pattern:
                        # count zero preserves its signed value; positive
                        # counts shift its unsigned image and clear the sign.
                        # Keep these cases even when overshifts also give zero.
                        if valid_lo == 0:
                            values.extend((value.lo, value.hi))
                        if valid_hi > 0:
                            unsigned = _unsigned_hull(value, width)
                            values.extend((unsigned.lo >> valid_hi,
                                           unsigned.hi >> max(1, valid_lo)))
                if amount_hull.lo < 0 or amount_hull.hi >= width:
                    if kind == z3.Z3_OP_BASHR and value.lo < 0:
                        values.append(-1)
                    values.append(0)
                if values:
                    if kind == z3.Z3_OP_BSHL:
                        proved = _no_wrap_hull(width, values)
                        if proved is not None:
                            return proved
                    else:
                        return ranges.Range(min(values), max(values))

        return self._full_hull(term)

    def _known_boolean(self, term):
        """A Boolean already known without opening its arithmetic cone."""

        import z3

        if z3.is_true(term):
            return True
        if z3.is_false(term):
            return False
        assumed = self._assumed_value(term)
        if assumed is not None:
            return assumed
        identity = term.get_id()
        return self.booleans.get(identity) \
            if identity in self.booleans else None

    @staticmethod
    def _comparison_from_hulls(term, memo):
        """Decide a comparison entailed by already-computed operand hulls.

        A value ``ite`` often guards a failure placeholder with the very
        precondition that makes the primitive total.  Its value arms already
        compute the compared operands, so this local check can discard the
        impossible placeholder without traversing the surrounding control
        formula.  Returning ``None`` merely declines the proof.
        """

        import z3

        kind = term.decl().kind()
        if kind not in {
                z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT,
                z3.Z3_OP_SLT, z3.Z3_OP_SLEQ,
                z3.Z3_OP_SGT, z3.Z3_OP_SGEQ,
                z3.Z3_OP_ULT, z3.Z3_OP_ULEQ,
                z3.Z3_OP_UGT, z3.Z3_OP_UGEQ,
        } or term.num_args() != 2 \
                or any(not z3.is_bv(child) for child in term.children()):
            return None

        def hull(value):
            if z3.is_bv_value(value):
                literal = value.as_signed_long()
                return ranges.Range(literal, literal)
            return memo.get(value.get_id())

        left = hull(term.arg(0))
        right = hull(term.arg(1))
        return _compare_hulls(kind, left, right)

    def _finish_boolean(self, term):
        """One local Boolean consequence of constants and finite hulls."""

        import z3

        known = self._known_boolean(term)
        if known is not None:
            return known
        identity = term.get_id()
        if z3.is_const(term) and identity in self.definitions:
            return self.booleans.get(self.definitions[identity].get_id())
        kind = term.decl().kind()
        if kind == z3.Z3_OP_NOT:
            value = self.booleans.get(term.arg(0).get_id())
            return None if value is None else not value
        if kind in {z3.Z3_OP_AND, z3.Z3_OP_OR}:
            children = tuple(term.children())
            values = [self.booleans.get(child.get_id())
                      for child in children]
            decisive = kind != z3.Z3_OP_AND
            if decisive in values:
                return decisive
            if values and all(value is not None for value in values):
                return not decisive
            formulas = tuple(self.boolean_formula(child)
                             for child in children)
            positive = {child.get_id() for child in formulas
                        if child.decl().kind() != z3.Z3_OP_NOT}
            negative = {child.arg(0).get_id() for child in formulas
                        if child.decl().kind() == z3.Z3_OP_NOT
                        and child.num_args() == 1}
            if positive & negative:
                return kind == z3.Z3_OP_OR
            return None
        if kind == z3.Z3_OP_ITE and z3.is_bool(term):
            condition = self.booleans.get(term.arg(0).get_id())
            if condition is not None:
                return self.booleans.get(
                    term.arg(1 if condition else 2).get_id())
            left = self.booleans.get(term.arg(1).get_id())
            right = self.booleans.get(term.arg(2).get_id())
            return left if left is not None and left == right else None
        if kind not in {
                z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT,
                z3.Z3_OP_SLT, z3.Z3_OP_SLEQ,
                z3.Z3_OP_SGT, z3.Z3_OP_SGEQ,
                z3.Z3_OP_ULT, z3.Z3_OP_ULEQ,
                z3.Z3_OP_UGT, z3.Z3_OP_UGEQ,
        } or len(term.children()) != 2:
            return None
        if z3.is_bool(term.arg(0)) and z3.is_bool(term.arg(1)):
            left = self.booleans.get(term.arg(0).get_id())
            right = self.booleans.get(term.arg(1).get_id())
            if left is None or right is None:
                return None
            return (left == right) if kind == z3.Z3_OP_EQ else (left != right)
        left = self.hulls.get(term.arg(0).get_id())
        right = self.hulls.get(term.arg(1).get_id())
        if kind in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT}:
            left_term, right_term = term.arg(0), term.arg(1)
            left_constant = (left_term.as_long()
                             if z3.is_bv_value(left_term) else
                             left.lo if left is not None
                             and left.lo == left.hi else None)
            right_constant = (right_term.as_long()
                              if z3.is_bv_value(right_term) else
                              right.lo if right is not None
                              and right.lo == right.hi else None)
            other = None
            if left_constant == 0:
                other = right_term
            elif right_constant == 0:
                other = left_term
            if other is not None:
                wants_nonzero = kind == z3.Z3_OP_DISTINCT
                known = self._known_nonzero(other)
                nonzero = self.nonzero(other)
                if known is None:
                    known = self._assumed_value(nonzero)
                if known is None and nonzero.get_id() != term.get_id():
                    known = self._boolean_value(nonzero)
                if known is not None:
                    return known == wants_nonzero
        return _compare_hulls(kind, left, right)

    def _boolean_value(self, term):
        """Prove a Boolean constant without recursive Python traversal."""

        import z3

        if not z3.is_bool(term):
            return None
        root = term.get_id()
        def result_with_local_rewrite():
            result = self.booleans[root]
            if result is None:
                # The local evaluator above deliberately keeps arithmetic
                # predicates opaque.  It can still miss a Boolean identity
                # between those predicates (for example, a completion flag
                # reconstructed through several 0/1 ``ite`` values).  Expand
                # only this guard's Boolean DAG, replace facts stated by the
                # fixed normal-completion assumption, and let the rewriter
                # decide a tautology.  This is linear structural reasoning;
                # it does not build the full transition-wide propositional
                # solver that made large arithmetic designs intractable.
                simplified = z3.simplify(self.boolean_formula(term))
                if z3.is_true(simplified):
                    result = True
                elif z3.is_false(simplified):
                    result = False
                elif (nodes := self._bounded_boolean_dag(simplified)) is not None:
                    # Formulas are memoized before all sibling hulls have
                    # necessarily been visited, so a later exact truth may
                    # not have been substituted into this particular image.
                    # State those already-proved local facts explicitly.  A
                    # propositional consequence remains sound for the concrete
                    # bit-vector relation because every arithmetic comparison
                    # not decided by a hull is kept as an independent atom.
                    substitutions = []
                    for source in nodes:
                        value = self.booleans.get(source.get_id())
                        if value is not None:
                            substitutions.append((source, z3.BoolVal(value, ctx=source.ctx)))
                    reduced = z3.simplify(z3.substitute(
                        simplified, *substitutions)) \
                        if substitutions else simplified
                    if z3.is_true(reduced):
                        result = True
                    elif z3.is_false(reduced):
                        result = False
                    else:
                        # Reduced guards share a fixed empty premise. Retain the
                        # abstraction and query results across DAG visits; later
                        # learned facts produce a different reduced expression.
                        if self.tautologies is None:
                            self.tautologies = _PropositionalProof(
                                z3.BoolVal(True, ctx=reduced.ctx), {}, work=self.work)
                        prover = self.tautologies
                        if prover.implies(reduced):
                            result = True
                        elif prover.implies(z3.Not(reduced)):
                            result = False
                self.booleans[root] = result
            return result
        if root in self.booleans:
            return result_with_local_rewrite()
        if root in self.truth_active:
            return None
        self.truth_active.add(root)
        active = set()
        try:
            stack = [(term, False)]
            while stack:
                current, expanded = stack.pop()
                identity = current.get_id()
                self.terms[identity] = current
                if identity in self.booleans:
                    continue
                self._charge()
                if expanded:
                    active.discard(identity)
                    self.booleans[identity] = self._finish_boolean(current)
                    continue
                dependencies = self._hull_dependencies(current)
                if any(child.get_id() in active for child in dependencies):
                    self.booleans[identity] = None
                    continue
                active.add(identity)
                stack.append((current, True))
                for child in reversed(dependencies):
                    child_identity = child.get_id()
                    if z3.is_bool(child):
                        if child_identity not in self.booleans:
                            stack.append((child, False))
                    elif z3.is_bv(child) and child_identity not in self.hulls:
                        # ``signed_hull`` is itself iterative.  At this point no
                        # parent bit-vector is awaiting this Boolean child, so a
                        # nested finite arithmetic cone can be discharged safely.
                        self.signed_hull(child)
            return result_with_local_rewrite()
        finally:
            self.truth_active.discard(root)

    def _bounded_boolean_dag(self, root):
        """Return one small Boolean DAG, or ``None`` when it exceeds the budget."""

        import z3

        logical = {
            z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT,
            z3.Z3_OP_IMPLIES, z3.Z3_OP_XOR, z3.Z3_OP_ITE,
            z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT,
        }
        pending = [root]
        seen = {}
        while pending:
            current = pending.pop()
            identity = current.get_id()
            if identity in seen:
                continue
            self._charge()
            seen[identity] = current
            if len(seen) > _CONTROL_PROPOSITION_BUDGET:
                return None
            children = tuple(current.children())
            if current.decl().kind() in logical and children \
                    and all(z3.is_bool(child) for child in children):
                pending.extend(children)
        return tuple(seen.values())

    def _signed_hull(self, term, *, coarse):
        """Return a sound signed interval, optionally ignoring branch tests."""

        import z3

        if not z3.is_bv(term):
            return None
        memo = self.coarse_hulls if coarse else self.hulls
        root = term.get_id()
        if root in memo:
            return memo[root]
        active = set()
        stack = [(term, False)]
        while stack:
            current, expanded = stack.pop()
            identity = current.get_id()
            self.terms[identity] = current
            if identity in memo:
                continue
            self._charge()
            if expanded:
                active.discard(identity)
                memo[identity] = self._finish_hull(current, coarse=coarse)
                continue
            dependencies = self._hull_dependencies(current, coarse=coarse)
            if any(child.get_id() in active for child in dependencies):
                memo[identity] = self._full_hull(current)
                continue
            active.add(identity)
            stack.append((current, True))
            stack.extend(
                (child, False) for child in reversed(dependencies)
                if child.get_id() not in memo)
        return memo[root]

    def coarse_signed_hull(self, term):
        """A branch-insensitive hull used as the cheap first closure proof."""

        return self._signed_hull(term, coarse=True)

    def signed_hull(self, term):
        """Return a sound precise signed interval proved directly from ``term``."""

        return self._signed_hull(term, coarse=False)


def _signed_bounds(width):
    return -(1 << (width - 1)), (1 << (width - 1)) - 1


def _no_wrap_hull(width, values):
    """The signed interval for mathematical results that cannot wrap."""
    lo, hi = min(values), max(values)
    signed_lo, signed_hi = _signed_bounds(width)
    return ranges.Range(lo, hi) \
        if signed_lo <= lo and hi <= signed_hi else None


class _PropositionalProof:
    """Reusable Boolean abstraction of one normal-completion formula."""

    __slots__ = (
        "answers", "atoms", "definitions", "images", "logical", "positive",
        "solver", "terms", "work")

    def __init__(self, assumption, definitions, *, work=None):
        import z3

        self.work = work
        self.definitions = dict(definitions)
        self.images = {}
        self.answers = {}
        # Z3 AST ids are handles, not ownership.  Retain every source term for
        # as long as its id indexes ``images`` so a later, temporary normalized
        # formula cannot reuse an id after Python releases the earlier wrapper.
        self.terms = {}
        self.atoms = {}
        self.logical = {
            z3.Z3_OP_AND,
            z3.Z3_OP_OR,
            z3.Z3_OP_NOT,
            z3.Z3_OP_IMPLIES,
            z3.Z3_OP_XOR,
            z3.Z3_OP_ITE,
            z3.Z3_OP_EQ,
            z3.Z3_OP_DISTINCT,
        }
        # Keep the common positive-conjunction case allocation-free, and retain
        # the set for every later implication query.
        self.positive = set()
        pending = [assumption]
        while pending:
            current = pending.pop()
            identity = current.get_id()
            if identity in self.positive:
                continue
            if self.work is not None:
                self.work.charge()
            self.positive.add(identity)
            definition = (
                definitions.get(identity) if z3.is_const(current) else None)
            if definition is not None and z3.is_bool(definition):
                pending.append(definition)
            elif current.decl().kind() == z3.Z3_OP_AND:
                pending.extend(current.children())
        abstract = self._abstract(assumption)
        self.solver = z3.Solver()
        self.solver.set(timeout=defaults.SIM_CHEAP_PROOF_QUERY_MS)
        self.solver.add(abstract)

    def _opaque(self, term):
        import z3

        identity = term.get_id()
        value = self.atoms.get(identity)
        if value is None:
            value = self.atoms[identity] = z3.Bool(
                f"@closure-proposition:{identity}")
        return value

    def _image(self, term):
        """Return an existing abstraction, or one fresh opaque atom."""

        value = self.images.get(term.get_id())
        return self._opaque(term) if value is None else value

    def _dependencies(self, term):
        import z3

        if z3.is_true(term) or z3.is_false(term):
            return ()
        identity = term.get_id()
        definition = (
            self.definitions.get(identity) if z3.is_const(term) else None)
        if definition is not None and z3.is_bool(definition):
            return (definition,)
        children = tuple(term.children())
        if term.decl().kind() not in self.logical or not children \
                or any(not z3.is_bool(child) for child in children):
            return ()
        return children

    def _finish(self, term):
        import z3

        if z3.is_true(term):
            return z3.BoolVal(True)
        if z3.is_false(term):
            return z3.BoolVal(False)
        identity = term.get_id()
        definition = (
            self.definitions.get(identity) if z3.is_const(term) else None)
        if definition is not None and z3.is_bool(definition):
            value = self.images.get(definition.get_id())
            return self._opaque(term) if value is None else value
        children = tuple(term.children())
        if any(not z3.is_bool(child) for child in children):
            return self._opaque(term)
        values = [self._image(child) for child in children]
        kind = term.decl().kind()
        if kind == z3.Z3_OP_AND:
            return z3.And(values)
        if kind == z3.Z3_OP_OR:
            return z3.Or(values)
        if kind == z3.Z3_OP_NOT and len(values) == 1:
            return z3.Not(values[0])
        if kind == z3.Z3_OP_IMPLIES and len(values) == 2:
            return z3.Implies(values[0], values[1])
        if kind == z3.Z3_OP_XOR and len(values) == 2:
            return z3.Xor(values[0], values[1])
        if kind == z3.Z3_OP_ITE and len(values) == 3:
            return z3.If(values[0], values[1], values[2])
        if kind == z3.Z3_OP_EQ and len(values) == 2:
            return values[0] == values[1]
        if kind == z3.Z3_OP_DISTINCT:
            return z3.Distinct(values)
        return self._opaque(term)

    def _abstract(self, root):
        active = set()
        stack = [(root, False)]
        while stack:
            current, expanded = stack.pop()
            identity = current.get_id()
            if identity in self.images:
                continue
            if self.work is not None:
                self.work.charge()
            if expanded:
                active.discard(identity)
                self.terms[identity] = current
                self.images[identity] = self._finish(current)
                continue
            children = self._dependencies(current)
            if identity in active or any(
                    child.get_id() in active for child in children):
                self.terms[identity] = current
                self.images[identity] = self._opaque(current)
                continue
            active.add(identity)
            stack.append((current, True))
            stack.extend(
                (child, False) for child in reversed(children)
                if child.get_id() not in self.images)
        return self.images[root.get_id()]

    def implies(self, atom):
        import z3

        identity = atom.get_id()
        if identity in self.positive:
            return True
        if identity in self.answers:
            return self.answers[identity]
        abstract = self._abstract(atom)
        if self.work is not None:
            self.work.charge()
        self.solver.push()
        try:
            self.solver.add(z3.Not(abstract))
            answer = self.solver.check() == z3.unsat
            self.answers[identity] = answer
            return answer
        finally:
            self.solver.pop()


def _syntactic_signed_hull(term, definitions, *, assumption=None):
    """A sound signed interval proved directly from one bit-vector term.

    This is deliberately a small theorem about the encoded term, not another
    reading of SolverIR ranges.  In particular, sign extension preserves the
    signed value of its operand, zero extension is nonnegative, and ``ite``
    can produce a value from either arm.  Following registered definitional
    names lets the proof survive the encoder's SSA boundary without expanding
    an otherwise large arithmetic cone.
    """
    return _SyntacticClosureProof(
        definitions, assumption=assumption).signed_hull(term)
