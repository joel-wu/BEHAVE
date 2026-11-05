"""Reduce a local target using equalities already asserted by its full product.

This is not cone slicing or existential elimination: no equation is assumed
to define an independent variable. A false reduced target proves the complete
conjunction false; a live target still goes to the original complete solver.
"""

from __future__ import annotations

from collections import deque

import z3
from z3 import z3core as native
from z3.z3 import _to_expr_ref

from .symbolic_state import _literal


def _kind(ctx, term):
    if native.Z3_get_ast_kind(ctx, term) == z3.Z3_APP_AST:
        return native.Z3_get_decl_kind(ctx, native.Z3_get_app_decl(ctx, term))
    return None


def _bit_equality(term):
    if not z3.is_eq(term):
        return None
    left, right = term.children()
    if z3.is_bv_value(left):
        left, right = right, left
    if z3.is_bv(left) and left.size() == 1 and z3.is_bv_value(right):
        return left, right
    return None


class _LiteralDAG:
    """Fold known values without specializing a still-symbolic arithmetic DAG.

    Keys are native AST handles, owned by retained expressions in one context.
    Re-reading Z3 numeric IDs on every edge adds no identity information.
    Needed syntax is indexed once. Ordinary arithmetic waits at its first
    unknown operand; new facts resume only its users. Unknown results remain
    shared source expressions, never model completions.
    """

    def __init__(self, literal, check):
        self.literal, self.check = literal, check
        self.nodes, self.values, self.parents, self.remaining = {}, {}, {}, {}
        self.absorbing = {}
        self.deferred = {}
        self.pending, self.queued = deque(), set()
        self.notifications = None
        self.request = None

    def value(self, term):
        found = self.values.get(term.as_ast().value)
        return term if found is None else found[1]

    def _queue(self, key):
        if key not in self.queued:
            self.queued.add(key)
            self.pending.append(key)

    def publish(self, term, value):
        key = term.as_ast().value
        old = self.value(term)
        if old.eq(value):
            return
        was_literal, is_literal = self.literal(old), self.literal(value)
        self.values[key] = term, value
        if self.notifications is not None:
            waiting, ready = self.notifications
            ready.update(waiting.pop(key, ()))
        for parent in self.parents.get(key, ()):
            if is_literal and not was_literal:
                self.remaining[parent] -= 1
            kind = self.nodes[parent][1]
            if (kind == z3.Z3_OP_AND and z3.is_false(value)
                    or kind == z3.Z3_OP_OR and z3.is_true(value)):
                self.absorbing[parent] = value
            self._queue(parent)

    def _watch(self, term, parent):
        users = self.parents.setdefault(term.as_ast().value, set())
        if parent not in users:
            users.add(parent)
            self.remaining[parent] += int(not self.literal(self.value(term)))

    def _fold(self, key):
        term, kind, children = self.nodes[key]
        if self.literal(self.value(term)):
            return
        if key in self.deferred:
            return
        if key in self.absorbing:
            self.publish(term, self.absorbing[key])
            return
        # Wide nodes do not rescan every operand after each single new fact.
        # Ground operations fold once; AND/OR also expose their last unknown.
        special = kind in {z3.Z3_OP_ITE, z3.Z3_OP_NOT, z3.Z3_OP_IMPLIES,
                           z3.Z3_OP_SELECT} or (
            kind in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT} and len(children) == 2)
        if self.remaining[key] and not special and not (
                kind in {z3.Z3_OP_AND, z3.Z3_OP_OR} and self.remaining[key] <= 1):
            return
        values = tuple(self.value(child) for child in children)
        if children and all(self.literal(value) for value in values):
            folded = z3.simplify(term.decl()(*values))
            if self.literal(folded):
                self.publish(term, folded)
                return
        result = term
        if kind == z3.Z3_OP_ITE:
            if z3.is_true(values[0]) or z3.is_false(values[0]):
                result = values[1 if z3.is_true(values[0]) else 2]
            elif values[1].eq(values[2]):
                result = values[1]
        elif kind in {z3.Z3_OP_AND, z3.Z3_OP_OR}:
            neutral = z3.is_true if kind == z3.Z3_OP_AND else z3.is_false
            live = tuple({value.as_ast().value: value for value in values if not neutral(value)}.values())
            if not live:
                result = z3.BoolVal(kind == z3.Z3_OP_AND, ctx=term.ctx)
            elif len(live) == 1:
                result = live[0]
        elif kind in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT} and len(values) == 2 and values[0].eq(values[1]):
            result = z3.BoolVal(kind == z3.Z3_OP_EQ, ctx=term.ctx)
        elif kind == z3.Z3_OP_IMPLIES:
            if z3.is_false(values[0]) or z3.is_true(values[1]):
                result = z3.BoolVal(True, ctx=term.ctx)
            elif z3.is_true(values[0]):
                result = values[1]
        elif kind == z3.Z3_OP_SELECT and len(values) == 2:
            if z3.is_const_array(values[0]):
                result = values[0].arg(0)
            else:
                read = z3.Select(*values)
                result = self.value(read)
                if read.as_ast().value != key:
                    # Track this exact cell, never invent an array default.
                    self.values.setdefault(read.as_ast().value, (read, read))
                    self._watch(read, key)
                    if self.request is not None:
                        self.request(read)
        self.publish(term, result)

    def evaluate(self, term):
        from ..core.construction_checkpoint import finish_construction

        return finish_construction(self.evaluate_steps(term))

    def evaluate_steps(self, term):
        """Suspend for newly demanded facts before opening dependent branches."""
        self.check()
        stack = [(term, None)]
        while stack or self.pending:
            self.check()
            if self.request is not None and self.notifications[1]:
                yield
                # A dependency evaluation may have drained shared wakeups.
                if not stack and not self.pending:
                    break
            if not stack:
                key = self.pending.popleft()
                self.queued.remove(key)
                position = self.deferred.get(key)
                current, kind, children = self.nodes[key]
                if position is not None and self.literal(self.value(children[position])):
                    stack.append((current, (kind, children, position)))
                else:
                    self._fold(key)
                continue
            current, continuation = stack.pop()
            key = current.as_ast().value
            if self.literal(self.value(current)):
                continue
            if continuation is None:
                if self.request is not None:
                    self.request(current)
                    if self.notifications[1]:
                        yield
                    if self.literal(self.value(current)):
                        continue
                if key in self.nodes:
                    continue
                children = tuple(current.children()) if z3.is_app(current) else ()
                kind = _kind(current.ctx_ref(), current.as_ast())
                # Ordinary arithmetic only folds when every operand is known.
                # Index one dependency at a time, suspending on the first
                # unknown instead of traversing the rest of its datapath.
                ground_only = kind not in {
                    z3.Z3_OP_ITE, z3.Z3_OP_NOT, z3.Z3_OP_IMPLIES,
                    z3.Z3_OP_SELECT, z3.Z3_OP_AND, z3.Z3_OP_OR,
                } and not (kind in {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT} and len(children) == 2)
                if children and ground_only:
                    stack.extend(((current, (kind, children, 0)), (children[0], None)))
                    continue
                demanded = children
                if kind in {z3.Z3_OP_ITE, z3.Z3_OP_IMPLIES}:
                    guard = self.value(children[0])
                    # Establish the control before indexing either datapath.
                    # Unknown controls still demand both original branches.
                    if (not z3.is_true(guard) and not z3.is_false(guard)
                            and children[0].as_ast().value not in self.nodes):
                        stack.extend(((current, None), (children[0], None)))
                        continue
                    if kind == z3.Z3_OP_ITE:
                        if z3.is_true(guard) or z3.is_false(guard):
                            demanded = (children[1 if z3.is_true(guard) else 2],)
                    elif z3.is_false(guard) or z3.is_true(self.value(children[1])):
                        demanded = ()
                elif kind in {z3.Z3_OP_AND, z3.Z3_OP_OR}:
                    absorbing = z3.is_false if kind == z3.Z3_OP_AND else z3.is_true
                    if any(absorbing(self.value(child)) for child in children):
                        demanded = ()
                    elif children:
                        # A child may become absorbing only after evaluation.
                        # Visit siblings on demand instead of opening every
                        # arithmetic/control cone before seeing that result.
                        stack.extend(((current, (kind, children, 0)), (children[0], None)))
                        continue
                stack.append((current, (kind, children, -1)))
                stack.extend((child, None) for child in reversed(demanded))
                continue
            kind, children, position = continuation
            if position >= 0:
                value = self.value(children[position])
                logical = kind in {z3.Z3_OP_AND, z3.Z3_OP_OR}
                absorbed = (kind == z3.Z3_OP_AND and z3.is_false(value)
                            or kind == z3.Z3_OP_OR and z3.is_true(value))
                if not logical and not self.literal(value):
                    self.deferred[key] = position
                elif not absorbed and position + 1 < len(children):
                    stack.extend(((current, (kind, children, position + 1)),
                                  (children[position + 1], None)))
                    continue
                else:
                    self.deferred.pop(key, None)
            # Watch all immediate operands even when an irrelevant subtree
            # was not traversed. A later fact wakes a suspended dependency;
            # resumption shares this work stack, not Python recursion.
            if key not in self.nodes:
                self.nodes[key] = current, kind, children
                self.remaining[key] = 0
                for child in children:
                    self._watch(child, key)
                    value = self.value(child)
                    if (kind == z3.Z3_OP_AND and z3.is_false(value)
                            or kind == z3.Z3_OP_OR and z3.is_true(value)):
                        self.absorbing[key] = value
            self._fold(key)
        return self.value(term)


class LiteralConsequences:
    """Propagate only proved literals from an immutable set of asserted roots.

    Construction metadata may order the coordinates, but supplies no facts.
    Implication consequents become usable only after every guard is proved true.
    The partial interpretation never completes an unknown symbol; array point
    equalities never choose a default for the remaining cells. This is sufficient
    reduction, not a feasibility check or a substitute for the original solver.
    """

    def __init__(self, assertions, *, context, check):
        self.check = check
        self.context = z3.main_ctx() if context is None else context
        self._equations, self._values, self._ground = {}, {}, {}
        self._decoded = {}
        self._dag = _LiteralDAG(self._literal, lambda: self.check())
        self._predicates = []
        # Own the complete roots while indexing native references. Wrapping
        # every RHS eagerly repeats Z3 sort discovery for unused definitions;
        # only requested equations need their typed Python expression views.
        self._roots = []
        for root in assertions:
            check()
            if not isinstance(root, z3.BoolRef) or root.ctx != self.context:
                raise ValueError('literal assertions must be Boolean in the owned context')
            self._roots.append(root)
        truth, falsehood = (z3.BoolVal(value, ctx=self.context) for value in (True, False))
        pending = [(root.as_ast(), ()) for root in self._roots]
        self._roots.extend((truth, falsehood))
        ctx = self.context.ref()
        seen = set()
        while pending:
            check()
            term, guards = pending.pop()
            key = term.value, tuple(g.value for g in guards)
            if key in seen:
                continue
            seen.add(key)
            kind = _kind(ctx, term)
            if kind == z3.Z3_OP_AND:
                pending.extend((native.Z3_get_app_arg(ctx, term, i), guards)
                               for i in range(native.Z3_get_app_num_args(ctx, term)))
            elif kind == z3.Z3_OP_IMPLIES:
                pending.append((native.Z3_get_app_arg(ctx, term, 1),
                                (*guards, native.Z3_get_app_arg(ctx, term, 0))))
            elif kind == z3.Z3_OP_EQ:
                a, b = (native.Z3_get_app_arg(ctx, term, i) for i in (0, 1))
                self._index(a, b, guards)
                self._index(b, a, guards)
            elif kind == z3.Z3_OP_UNINTERPRETED and not native.Z3_get_app_num_args(ctx, term):
                self._index(term, truth.as_ast(), guards)
            else:
                child = native.Z3_get_app_arg(ctx, term, 0) if kind == z3.Z3_OP_NOT else None
                if child is not None and _kind(ctx, child) == z3.Z3_OP_UNINTERPRETED \
                        and not native.Z3_get_app_num_args(ctx, child):
                    self._index(child, falsehood.as_ast(), guards)
                elif kind is not None:
                    self._predicates.append((_to_expr_ref(term, self.context),
                        tuple(_to_expr_ref(g, self.context) for g in guards)))
        # Index point equalities without evaluating every read in the history.
        # Literal cells were published by _index; derived cells use the same
        # demanded-equation worklist as scalar coordinates. A read on an unused
        # equality's RHS must not force its whole array history to be visited.

    def _literal(self, term):
        self.check()
        key = term.as_ast().value
        known = self._ground.get(key)
        if known is not None:
            return known[1]
        # Explicit stack also handles large immutable Store chains. Retained
        # owners prevent Z3 from recycling the identities used by this cache.
        pending = [term]
        while pending:
            self.check()
            value = pending[-1]
            key = value.as_ast().value
            if key in self._ground:
                pending.pop()
                continue
            kind = _kind(value.ctx_ref(), value.as_ast())
            children = value.children() if kind in (z3.Z3_OP_CONST_ARRAY, z3.Z3_OP_STORE) else ()
            missing = [child for child in children if child.as_ast().value not in self._ground]
            if missing:
                pending.extend(missing)
                continue
            ground = _literal(value) or bool(children) and all(
                self._ground[child.as_ast().value][1] for child in children)
            self._ground[key] = value, ground
        return self._ground[term.as_ast().value][1]

    def _add(self, lhs, rhs, guards):
        guards = tuple(guards)
        if any(term.ctx != self.context for term in (lhs, rhs, *guards)):
            raise ValueError('literal equations must share the owned context')
        # Dynamic additions and the one-bit bridge can create syntax not owned
        # by an original assertion. Retain it before publishing native handles.
        self._roots.extend((lhs, rhs, *guards))
        self._index(lhs.as_ast(), rhs.as_ast(), tuple(g.as_ast() for g in guards))

    def _index(self, lhs, rhs, guards):
        ctx = self.context.ref()
        kind = _kind(ctx, lhs)
        if kind == z3.Z3_OP_SELECT or (kind == z3.Z3_OP_UNINTERPRETED
                and not native.Z3_get_app_num_args(ctx, lhs)):
            key = lhs.value
            self._equations.setdefault(key, []).append((lhs, rhs, guards))
            self._decoded.pop(key, None)
            if not guards and (_kind(ctx, rhs) in (z3.Z3_OP_TRUE, z3.Z3_OP_FALSE,
                    z3.Z3_OP_CONST_ARRAY, z3.Z3_OP_STORE)
                    or native.Z3_get_ast_kind(ctx, rhs) == z3.Z3_NUMERAL_AST):
                value = _to_expr_ref(rhs, self.context)
                if self._literal(value):
                    self._remember(_to_expr_ref(lhs, self.context), value)
        elif kind == z3.Z3_OP_EQ:
            bit = _bit_equality(_to_expr_ref(lhs, self.context))
            if bit is not None:
                term, value = bit
                self._add(term, z3.If(_to_expr_ref(rhs, self.context), value,
                    z3.BitVecVal(1 - value.as_long(), 1, ctx=value.ctx)),
                    tuple(_to_expr_ref(g, self.context) for g in guards))

    def _definitions(self, key):
        if key not in self._equations:
            return ()
        if key not in self._decoded:
            self._decoded[key] = tuple((_to_expr_ref(lhs, self.context),
                _to_expr_ref(rhs, self.context),
                tuple(_to_expr_ref(g, self.context) for g in guards))
                for lhs, rhs, guards in self._equations[key])
        return self._decoded[key]

    def _remember(self, lhs, rhs):
        if lhs.as_ast().value in self._values:
            return
        self._values[lhs.as_ast().value] = lhs, rhs
        self._dag.publish(lhs, rhs)

    def evaluate(self, term):
        if term.ctx != self.context:
            raise ValueError('literal facts and their targets must share one context')
        return self._dag.evaluate(term)

    def constant_target(self, term):
        """Fold a proved Boolean; otherwise retain the shared original DAG.

        Partial model evaluation can specialize a large arithmetic circuit.
        Keeping that nonconstant copy alongside the original constraints costs
        a second circuit without settling the goal. Only a proved true/false
        target replaces the original; no assertion or unknown exit is removed.
        """
        if not z3.is_bool(term):
            raise TypeError('a query target must be Boolean')
        value = self.evaluate(term)
        return value if z3.is_true(value) or z3.is_false(value) else term

    def constant_targets(self, terms):
        """Share the dependency index, retaining every nonconstant target."""
        terms = tuple(terms)
        if not terms:
            return ()
        if any(not z3.is_bool(term) or term.ctx != self.context for term in terms):
            raise TypeError('query targets must be Boolean in the owned context')
        values = self._evaluate_many(terms)
        return tuple(value if z3.is_true(value) or z3.is_false(value) else term
                     for term, value in zip(terms, values, strict=True))

    def _evaluate_many(self, terms):
        """Visit shared nodes once, rather than pack a giant model evaluation."""
        return tuple(self.evaluate(term) for term in terms)

    def literal_constraints(self):
        """Publish only equations already entailed by the original roots.

        Hoisting guarded/derived constants lets the solver simplify the shared
        history and its targets together. These are redundant consequences,
        not a partial model used to choose unknown inputs or array defaults.
        """
        return tuple(lhs == rhs for lhs, rhs in self._values.values())

    def derive(self, coordinates):
        """Use the same dependency worklist for coordinates and goal roots."""
        self.derive_targets(coordinates)

    def derive_targets(self, targets):
        """Demand definitions through the existing literal DAG, not every frame.

        Asserted predicates and demanded equations share one worklist. A
        guarded predicate waits until its consequent is requested; inspecting
        every guard first would open unrelated histories before the targets.
        Unknown guards suspend their equations; facts wake only their users.
        Cycles without an asserted fact stay unknown. All original constraints
        remain with the solver, so this is reduction, not dependency slicing.
        """
        jobs = [(None, term, guards) for term, guards in self._predicates]
        waiting, ready, done, requested = {}, set(), set(), set()
        predicates = {}
        for index, (_, term, guards) in enumerate(jobs):
            self.check()
            if not guards:
                ready.add(index)  # Already asserted; no history needs checking.
            else:
                part = term.arg(0) if z3.is_not(term) else term
                predicates.setdefault(part.as_ast().value, []).append(index)
        expanded = set()

        def demand(term):
            key = term.as_ast().value
            if key in requested or key not in predicates and key not in self._equations:
                return
            requested.add(key)
            ready.update(predicates.get(key, ()))
            for row in self._definitions(key):
                ready.add(len(jobs))
                jobs.append(row)

        def evaluate(term):
            if term.ctx != self.context:
                raise ValueError('literal facts and their targets must share one context')
            return (yield from self._dag.evaluate_steps(term))

        def derive(index):
            self.check()
            if index in done:
                return
            lhs, rhs, guards = jobs[index]
            if lhs is not None and lhs.as_ast().value in self._values:
                done.add(index)
                return
            for guard in guards:
                value = yield from evaluate(guard)
                if not z3.is_true(value):
                    waiting.setdefault(guard.as_ast().value, set()).add(index)
                    return
            if lhs is None:
                done.add(index)
                for part, value in self._predicate_literals(rhs, expanded):
                    self._remember(part, value)
            else:
                value = yield from evaluate(rhs)
                if self._literal(value):
                    done.add(index)
                    self._remember(lhs, value)
                else:
                    waiting.setdefault(rhs.as_ast().value, set()).add(index)

        previous = self._dag.notifications, self._dag.request
        self._dag.notifications, self._dag.request = (waiting, ready), demand
        # One iterative worklist owns both expression continuations and their
        # asserted definitions. Resolve a requested control before resuming its
        # consumer; unknown/cyclic definitions still leave that control free.
        work = [evaluate(term) for term in reversed(tuple(targets))]
        try:
            for term, _kind, _children in tuple(self._dag.nodes.values()):
                demand(term)
            while work or ready or self._dag.pending:
                self.check()
                if ready:
                    work.extend(derive(index) for index in sorted(ready, reverse=True))
                    ready.clear()
                if not work:
                    work.append(evaluate(z3.BoolVal(True, ctx=self.context)))
                try:
                    next(work[-1])
                except StopIteration:
                    work.pop()
        finally:
            for continuation in work:
                continuation.close()
            self._dag.notifications, self._dag.request = previous

    def _predicate_literals(self, term, expanded):
        # This predicate is already asserted (and its guards proved). Reading
        # its known image is sufficient: evaluating it first opens unrelated
        # arithmetic histories just to rediscover a fact supplied by a premise.
        # Target/equation demand still evaluates dependencies normally. Keep
        # disjunctions whole; a true OR never chooses an arm or a free value.
        parts = [self._dag.value(term)]
        while parts:
            self.check()
            part = parts.pop()
            if z3.is_and(part):
                key = part.as_ast().value
                if key not in expanded:
                    expanded.add(key)
                    parts.extend(part.children())
            elif not z3.is_true(part) and not z3.is_false(part):
                lhs, rhs = (part.arg(0), z3.BoolVal(False, ctx=part.ctx)) \
                    if z3.is_not(part) else (part, z3.BoolVal(True, ctx=part.ctx))
                if lhs.as_ast().value not in self._values:
                    yield lhs, rhs

    def derive_predicates(self):
        """Use asserted compound facts after their source guards become known.

        A legal adapter row can assert a comparison or an entire disjunction,
        not just a Boolean variable. Keep that predicate whole: knowing an OR
        is true never chooses one of its arms. Like point-read equalities,
        these are substitutions justified by the original roots, which remain
        in the complete query. This does not establish domain feasibility.
        """
        pending = dict(enumerate(self._predicates))
        waiting, ready = {}, set(pending)
        previous = self._dag.notifications
        self._dag.notifications = waiting, ready
        try:
            while ready:
                current = sorted(ready)
                ready.clear()
                additions, guards_proved = {}, {}
                expanded = set()
                for index in current:
                    self.check()
                    if index not in pending:
                        continue
                    term, guards = pending[index]
                    for guard in guards:
                        key = guard.as_ast().value
                        if key not in guards_proved:
                            guards_proved[key] = z3.is_true(self.evaluate(guard))
                        if not guards_proved[key]:
                            waiting.setdefault(key, set()).add(index)
                            break
                    else:
                        del pending[index]
                        for lhs, rhs in self._predicate_literals(term, expanded):
                            additions.setdefault(lhs.as_ast().value, (lhs, rhs))
                # Keep each round's guard cache immutable. The expression DAG
                # already knows which guards depend on these newly proved facts;
                # wake their predicates, not every remaining history predicate.
                for lhs, rhs in additions.values():
                    self.check()
                    self._remember(lhs, rhs)
                self._dag.evaluate(z3.BoolVal(True, ctx=self.context))
        finally:
            self._dag.notifications = previous


