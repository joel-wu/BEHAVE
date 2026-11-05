"""Read a conditional value under a known execution guard, without changing it.

Only syntactic consequences of encoder-owned Boolean definitions are used:
true conjunctions, false disjunctions and negation. A selected ITE arm is equal
to the original value under that guard. Publication still uses the complete
guarded write. No inferred range, solver answer or additional premise is used.
"""

from dataclasses import dataclass, field
from weakref import WeakValueDictionary

import z3

from .dependency_sets import Support as _Support


_NO_FACTS = _Support()


def _cached_projection(cache, key):
    entry = cache.get(key[0])
    return entry[2] if entry is not None and entry[:2] == key[1:] else None


def _remember_projection(cache, key, value):
    # One exact context per immutable node, not every historical path context.
    # Replacing a memo removes no expression or defining equation. A changed
    # projection is recomputed; unrelated guard facts still reuse the entry.
    cache[key[0]] = key[1], key[2], value


@dataclass(frozen=True, slots=True)
class _Facts:
    """One active guard's bitmap and sparse membership index."""

    atoms: _Support | frozenset
    mask: _Support
    projections: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def of(cls, atoms):
        return cls(atoms, atoms if isinstance(atoms, _Support) else _Support.from_atoms(atoms))

    def __contains__(self, atom):
        return atom in self.atoms


class GuardedReads:
    def __init__(self):
        # Sparse facts and compact relative dependency bitmaps preserve the
        # same union/intersection and cache-key meaning. No guard or defining
        # equation is removed.
        self._definitions = {}
        self._facts = {}
        self._atoms = {}
        self._denied_conjunctions = {}
        self._required_disjunctions = {}
        self._denied_atoms = _NO_FACTS
        self._required_atoms = _NO_FACTS
        self._logical = {}
        self._conditions = {}
        self._choices = {}
        self._reads = {}
        self._impossible_guards = {}
        self._compound_checks = {}
        self._supports = WeakValueDictionary()
        self._active_facts = None

    def _support(self, value):
        # Different conditional values often have the same dependency set.
        # Share its immutable representation, not a query answer. Weak values
        # let replaced/unused histories disappear with their last real owner.
        key = value._root
        retained = self._supports.get(key)
        if retained is None:
            self._supports[key] = value
            return value
        return retained

    def _atom(self, term):
        return self._atoms.setdefault(term.get_id(), (term, len(self._atoms)))[1]

    def define(self, name, term):
        # Called only for a newly emitted definition, not a simplifier's alias.
        # Registering x = simplify(x+0) here would invent a cyclic definition.
        if z3.is_bool(term):
            self._definitions[name.get_id()] = self._literal_choice_test(term)
        elif z3.is_app_of(term, z3.Z3_OP_ITE):
            self._definitions[name.get_id()] = term

    def _literal_choice_test(self, term):
        """Recover a Boolean guard hidden by a source integer flag.

        Lowering may name ``ite(p, 1, 0)`` and later test that name against
        zero. Treating the comparison as an unrelated atom loses the known
        path p. Only owned choices with literal arms are opened; arithmetic
        and general conditional histories remain named. Original SSA equations
        are untouched, and no range or solver premise is needed.
        """
        negated = z3.is_not(term)
        test = term.arg(0) if negated else term
        if z3.is_distinct(test) and test.num_args() == 2:
            negated = not negated
        elif not z3.is_eq(test):
            return term
        left, right = test.arg(0), test.arg(1)
        if z3.is_bv_value(left):
            left, right = right, left
        if not z3.is_bv_value(right):
            return term
        choice = self._definitions.get(left.get_id(), left)
        if not z3.is_app_of(choice, z3.Z3_OP_ITE):
            return term
        yes, no = choice.arg(1), choice.arg(2)
        if not (z3.is_bv_value(yes) and z3.is_bv_value(no)):
            return term
        first, second = yes.eq(right), no.eq(right)
        answer = (z3.BoolVal(first, ctx=term.ctx) if first == second else
                  choice.arg(0) if first else z3.Not(choice.arg(0)))
        return z3.Not(answer) if negated else answer

    def _known(self, guard):
        pending = [(guard, True, None)]
        while pending:
            term, truth, children = pending.pop()
            key = term.get_id(), truth
            if key in self._facts:
                continue
            if children is None:
                alias = self._definitions.get(term.get_id())
                if alias is not None:
                    children = ((alias, truth),)
                elif z3.is_not(term):
                    children = ((term.arg(0), not truth),)
                elif (truth and z3.is_and(term)) or (not truth and z3.is_or(term)):
                    children = tuple((child, truth) for child in term.children())
                else:
                    children = ()
                pending.append((term, truth, children))
                pending.extend((child, holds, None) for child, holds in reversed(children)
                               if (child.get_id(), holds) not in self._facts)
                continue
            # The terms are retained with their atom positions: Z3 cannot reuse
            # an AST identity while this encoding's fact index owns it.
            definition = self._definitions.get(term.get_id(), term)
            decomposed = (z3.is_not(definition)
                          or (truth and z3.is_and(definition))
                          or (not truth and z3.is_or(definition)))
            # These connective facts are already implied by their children.
            # Do not duplicate every intermediate path-prefix SSA name in all
            # later guards. False AND and true OR remain atomic facts: their
            # individual children are not determined.
            index = self._atom(term)  # Own even decomposed roots used as cache keys.
            # Appending a branch shares the previous guard's immutable words;
            # retaining all prefixes must not copy a growing Python set each
            # time. Use the same exact set representation as dependencies.
            atom = _NO_FACTS if decomposed else _Support.atom(index)
            # False AND / true OR are atomic facts, but their children can
            # contradict that fact under a later, stronger execution guard.
            # Keep only these compound atoms, not every positive path prefix.
            if not truth and z3.is_and(definition):
                self._denied_conjunctions[index] = tuple(definition.children())
                self._denied_atoms |= atom
            elif truth and z3.is_or(definition):
                self._required_disjunctions[index] = tuple(definition.children())
                self._required_atoms |= atom
            yes, no = (atom, _NO_FACTS) if truth else (_NO_FACTS, atom)
            for child, holds in children:
                child_yes, child_no = self._facts[child.get_id(), holds]
                yes |= child_yes
                no |= child_no
            self._facts[key] = yes, no
        return self._facts[guard.get_id(), True]

    def _index_condition(self, term):
        """Index Boolean connectives, leaving arithmetic predicates named."""
        pending = [(term, None)]
        while pending:
            current, children = pending.pop()
            key = current.get_id()
            if key in self._logical:
                continue
            definition = self._definitions.get(key, current)
            if children is None:
                children = (tuple(definition.children()) if
                            z3.is_and(definition) or z3.is_or(definition) or z3.is_not(definition)
                            else ())
                if children:
                    pending.append((current, children))
                    pending.extend((child, None) for child in reversed(children)
                                   if child.get_id() not in self._logical)
                    continue
            support = _Support.atom(self._atom(current))
            for child in children:
                support |= self._logical[child.get_id()][3]
            self._logical[key] = current, definition, children, self._support(support)
        return self._logical[term.get_id()][3]

    def _condition(self, term, yes, no):
        """Substitute known facts through owned AND/OR/NOT definitions.

        This does not inspect arithmetic or enumerate paths. The support set
        makes unrelated execution guards reuse the same reduced condition.
        """
        self._index_condition(term)
        cache = self._conditions

        def key(current):
            support = self._logical[current.get_id()][3]
            return (current.get_id(), self._support(support.project(yes)),
                    self._support(support.project(no)))

        pending = [(term, False)]
        while pending:
            current, ready = pending.pop()
            identity = key(current)
            if _cached_projection(cache, identity) is not None:
                continue
            _, definition, children, support = self._logical[current.get_id()]
            atom = self._atoms[current.get_id()][1]
            if atom in yes or atom in no:
                result = z3.BoolVal(atom in yes, ctx=current.ctx)
            elif not children or not (support.intersects(yes) or support.intersects(no)):
                result = current
            elif not ready:
                pending.append((current, True))
                pending.extend((child, False) for child in reversed(children)
                               if _cached_projection(cache, key(child)) is None)
                continue
            else:
                values = tuple(_cached_projection(cache, key(child)) for child in children)
                result = (current if all(a.eq(b) for a, b in zip(values, children, strict=True))
                          else z3.simplify(definition.decl()(*values)))
            _remember_projection(cache, identity, result)
        return _cached_projection(cache, key(term))

    def _choose(self, condition, left, right):
        """Fold repeated/complementary ITEs locally, without walking a history.

        After reducing guards under g, two complete writes can have the form
        ite(c, a, ite(!c, b, old)). Its old arm is impossible, even though c
        itself is unknown. A partial write still retains that old arm.
        """
        def selected(value, truth):
            while True:
                definition = self._definitions.get(value.get_id(), value)
                if not z3.is_app_of(definition, z3.Z3_OP_ITE):
                    return value
                inner = definition.arg(0)
                if inner.eq(condition):
                    holds = truth
                elif ((z3.is_not(inner) and inner.arg(0).eq(condition)) or
                      (z3.is_not(condition) and condition.arg(0).eq(inner))):
                    holds = not truth
                else:
                    return value
                value = definition.arg(1 if holds else 2)

        left, right = selected(left, True), selected(right, False)
        if left.eq(right):
            return left
        # Adjacent writes under p&q and p&!q cover p. Factoring that common
        # parent lets the same local rule handle nested if/else, without
        # enumerating paths or repeatedly simplifying an entire value DAG.
        inner = self._definitions.get(right.get_id(), right)
        if z3.is_and(condition) and z3.is_app_of(inner, z3.Z3_OP_ITE) and z3.is_and(inner.arg(0)):
            outer_terms = {term.get_id(): term for term in condition.children()}
            inner_terms = {term.get_id(): term for term in inner.arg(0).children()}
            outer_only, inner_only = outer_terms.keys() - inner_terms.keys(), inner_terms.keys() - outer_terms.keys()
            if len(outer_only) == len(inner_only) == 1:
                a, b = outer_terms[next(iter(outer_only))], inner_terms[next(iter(inner_only))]
                if ((z3.is_not(a) and a.arg(0).eq(b)) or
                        (z3.is_not(b) and b.arg(0).eq(a))):
                    common = tuple(term for key, term in outer_terms.items() if key in inner_terms)
                    condition = common[0] if len(common) == 1 else z3.And(*common)
                    left = z3.If(a, left, inner.arg(1))
                    right = selected(inner.arg(2), False)
        return left if left.eq(right) else z3.If(condition, left, right)

    def _index_choices(self, term):
        """Index one immutable choice, not both of its historical subtrees."""
        key = term.get_id()
        if key not in self._choices:
            definition = self._definitions.get(key, term)
            if z3.is_app_of(definition, z3.Z3_OP_ITE):
                condition = definition.arg(0)
                self._choices[key] = (term, condition,
                    (definition.arg(1), definition.arg(2)),
                    self._index_condition(condition))
            else:
                self._choices[key] = term, None, (), _NO_FACTS
        return self._choices[key]

    def read(self, value, guard):
        if guard is None:
            return value
        cache = self._reads

        def select(term):
            if isinstance(term, list):
                return [select(child) for child in term]
            # Persistent arrays keep their original representation. Their
            # scalar selection can still use this same exact rule afterwards.
            if not z3.is_expr(term):
                return term
            if self._index_choices(term)[1] is None:
                return term
            # Literals, inputs and named arithmetic need no path-fact work.
            # Construct facts lazily, only when a conditional value needs them;
            # retain one active guard, not every historical projection index.
            if self._active_facts is None or self._active_facts[0] != guard.get_id():
                yes, no = self._known(guard)
                self._active_facts = guard.get_id(), _Facts.of(yes), _Facts.of(no)
            _, yes, no = self._active_facts

            def remembered(current):
                entry = cache.get(current.get_id())
                if entry is not None:
                    _, support, positive, negative, _ = entry
                    if (support.project(yes), support.project(no)) == (positive, negative):
                        return entry
                return None

            def remember(current, support, result):
                # The support includes every condition inspected, including an
                # unknown condition that kept both arms. An unvisited arm is
                # irrelevant only while these exact facts remain unchanged.
                # One context per node; retain AST owners, never bare native IDs.
                cache[current.get_id()] = (current, self._support(support),
                    self._support(support.project(yes)),
                    self._support(support.project(no)), result)

            # Evaluate choices on demand under this guard. Even below an
            # unknown outer condition, a known inner write cuts off its old
            # history before indexing it. Unknown conditions retain both arms.
            # This is exact Boolean substitution, not a path/solver guess.
            pending = [(term, None, ())]
            while pending:
                current, condition, children = pending.pop()
                if remembered(current) is not None:
                    continue
                _, original, arms, support = self._index_choices(current)
                if not children:
                    if original is None:
                        remember(current, _NO_FACTS, current)
                        continue
                    condition = self._condition(original, yes, no)
                    if z3.is_true(condition):
                        children = arms[:1]
                    elif z3.is_false(condition):
                        children = arms[1:]
                    else:
                        children = arms
                    pending.append((current, condition, children))
                    pending.extend((child, None, ()) for child in reversed(children)
                                   if remembered(child) is None)
                    continue
                entries = tuple(remembered(child) for child in children)
                values = tuple(entry[4] for entry in entries)
                for entry in entries:
                    support |= entry[1]
                if len(values) == 1:
                    rewritten = values[0]
                elif (condition.eq(original) and
                      all(a.eq(b) for a, b in zip(values, children, strict=True))):
                    rewritten = current
                else:
                    rewritten = self._choose(condition, *values)
                remember(current, support, rewritten)
            return cache[term.get_id()][4]

        return select(value)

    def condition(self, condition, guard):
        """The same Boolean under a branch's entry, using only owned facts."""
        yes, no = self._known(guard)
        return self._condition(condition, _Facts.of(yes), _Facts.of(no))

    def impossible(self, guard):
        """An owned guard entails both an atom and its negation.

        This is a syntactic contradiction certificate, not a range premise or
        a solver-based reachability guess. In particular it handles a guarded
        failure's absorbed suffix even when SSA names hide the contradiction.
        """
        identity = guard.get_id()
        if identity not in self._impossible_guards:
            # One immutable SSA guard controls many commands. _known owns its
            # terms and defining Boolean DAG; later SSA names cannot change
            # that DAG. Reuse this exact check within the current encoding.
            self._impossible_guards[identity] = self._contradiction(guard)
        return self._impossible_guards[identity]

    def _contradiction(self, guard):
        yes, no = self._known(guard)
        if yes & no:
            return True
        denied = self._denied_atoms & no
        required = self._required_atoms & yes
        if not denied and not required:
            return False
        positive, negative = _Facts.of(yes), _Facts.of(no)
        # Test children, not the compound itself: looking up the latter would
        # just return the asserted polarity. This is Boolean substitution over
        # owned definitions, with no arithmetic reasoning or solver query.
        return any(
            self._compound_contradiction(index, table[index], check, positive, negative)
            for indices, table, check in (
                (denied, self._denied_conjunctions, z3.is_true),
                (required, self._required_disjunctions, z3.is_false))
            for index in indices)

    def _compound_contradiction(self, index, children, check, yes, no):
        """Reuse a compound check while its deciding facts remain unchanged.

        A false AND conflicts only if every child is true; a true OR only if
        every child is false. One undecided/opposite child therefore suffices
        to keep the check negative, independently of the other children.
        Retain that child's support, or all inspected support for a positive
        result. This avoids repeatedly reducing growing path prefixes under
        unrelated guards. One entry per immutable compound replaces its old
        context; neither a new premise nor a history of answers is retained.
        """
        previous = self._compound_checks.get(index)
        if previous is not None:
            support, positive, negative, result = previous
            if (support.project(yes), support.project(no)) == (positive, negative):
                return result
        support, result = _NO_FACTS, True
        for child in children:
            value = self._condition(child, yes, no)
            dependency = self._logical[child.get_id()][3]
            if not check(value):
                support, result = dependency, False
                break
            support |= dependency
        self._compound_checks[index] = (self._support(support),
            self._support(support.project(yes)), self._support(support.project(no)), result)
        return result
