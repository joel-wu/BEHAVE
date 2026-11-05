"""Structural termination plans for the admitted ``while`` subset.

A source ``while`` has no trip-count expression of its own.  SolverIR retains
one body under either an exact trip expression or a proved finite number of
condition checks.  The bridge is therefore a termination proof, never an
arbitrary number of copied bodies.

The exact plan recognises one monotone scalar recurrence, including the binary
parent recurrence ``x = (x - 1) // 2`` and its captured scalar temporary. The ranked plan
allows data-dependent control to advance different scalar components, while
proving that their summed distance to invariant bounds decreases on every
back-edge.  A separate cutoff plan covers constant-true loops: one scalar
strictly increases on every back-edge, and a preceding invariant upper check
forces a later body visit to break or return.  ``break`` and ``return`` paths
need not advance because they do not take a back-edge; ``continue`` paths do.
A loop ``else`` remains tied to natural condition exhaustion.  Anything
outside these proofs is refused instead of being truncated or turned into a
synthetic model failure.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from behave.core import runtime_defaults as defaults

__all__ = [
    "WhileCutoff",
    "WhileExitPath",
    "WhileExitRanking",
    "WhilePlanError",
    "WhileProgress",
    "WhileRankComponent",
    "WhileRanking",
    "analyse",
    "analyse_exit_ranking",
    "analyse_ranking",
    "exit_ranking_domain",
    "loop_container_writes",
    "loop_written_scalars",
    "loop_preserves_iterable_values",
    "ranking_span",
    "trip_span",
]


class WhilePlanError(ValueError):
    """A source while loop has no proved finite plan in this subset."""


@dataclass(frozen=True)
class WhileProgress:
    """The exact scalar recurrence that determines a while trip count."""

    bound: ast.expr
    inclusive: bool
    name: str
    operation: str
    amount: int
    not_equal: bool = False


@dataclass(frozen=True)
class WhileRankComponent:
    """One distance-to-bound component of a finite while ranking."""

    bound: ast.expr
    inclusive: bool
    name: str
    direction: int


@dataclass(frozen=True)
class WhileRanking:
    """A sum ranking which strictly decreases on every loop back-edge.

    ``direction`` is positive for a value which moves upward to its bound and
    negative for one which moves downward.  A component may stay unchanged on
    one back-edge; at least one component must make progress on every
    back-edge.  Consequently the sum of the remaining integer distances is a
    finite upper bound, even when data-dependent control chooses which
    component advances.
    """

    components: tuple[WhileRankComponent, ...]
    maximum_steps: tuple[int, ...]


@dataclass(frozen=True)
class WhileCutoff:
    """One upper constraint on a loop-head induction value.

    The constraint is ``coefficient * x + offset < bound`` when
    ``inclusive`` is false, and ``<= bound`` otherwise.  Its bound is an
    invariant source expression whose finite task-domain range is supplied by
    the caller.
    """

    coefficient: int
    offset: int
    bound: ast.expr
    inclusive: bool


@dataclass(frozen=True)
class WhileExitPath:
    """One loop back-edge in an exit-dominated termination proof."""

    coefficient: int
    offset: int
    cutoffs: tuple[WhileCutoff, ...]


@dataclass(frozen=True)
class WhileExitRanking:
    """A strict scalar ranking whose finite cutoff precedes every back-edge.

    Each represented path reaches the
    next body visit with ``x' = coefficient * x + offset`` and carries at
    least one invariant upper constraint on ``x``.  Paths ending in ``break``
    or ``return`` are absent.  Domain ranges later prove that every retained
    back-edge strictly increases ``x`` and derive a finite number of body
    visits.
    """

    name: str
    paths: tuple[WhileExitPath, ...]
    guarded: bool = False


def _cutoff_upper(cutoff, bound):
    numerator = bound.hi - cutoff.offset
    if not cutoff.inclusive:
        numerator -= 1
    return numerator // cutoff.coefficient


def exit_ranking_domain(ranking, induction, bounds):
    """Return ``(body visits, induction values at body entry)``.

    ``bounds`` mirrors ``ranking.paths`` and supplies one finite range for
    each path cutoff. Every back-edge advances by a proved affine lower
    envelope and starts no greater than its path's cutoff. One final body
    visit executes the exit which prevents another back-edge. Bounds are
    computed with integer arithmetic, without enumerating the value domain.
    """
    from ..symbolic.ranges import Range

    if not isinstance(ranking, WhileExitRanking):
        raise TypeError("an exit-ranking domain needs WhileExitRanking")
    if not isinstance(induction, Range):
        raise TypeError("an exit-ranking domain needs a finite induction Range")
    bounds = tuple(tuple(row) for row in bounds)
    if len(bounds) != len(ranking.paths) or any(
            len(row) != len(path.cutoffs)
            for path, row in zip(ranking.paths, bounds)):
        raise ValueError("an exit-ranking domain needs every cutoff range")

    largest_cutoff = None
    largest_entry = induction.hi
    minimum_coefficient = minimum_delta = None
    for path, row in zip(ranking.paths, bounds):
        if any(not isinstance(bound, Range) for bound in row):
            raise TypeError("an exit-ranking cutoff needs a finite Range")
        upper = min(
            _cutoff_upper(cutoff, bound)
            for cutoff, bound in zip(path.cutoffs, row))
        if upper < induction.lo:
            continue
        delta = ((path.coefficient - 1) * induction.lo + path.offset)
        if path.coefficient < 1 or delta < 1:
            raise WhilePlanError(
                "a cutoff-ranked while back-edge is not strictly increasing")
        largest_cutoff = (upper if largest_cutoff is None else
                          max(largest_cutoff, upper))
        largest_entry = max(
            largest_entry, path.coefficient * upper + path.offset)
        minimum_coefficient = (path.coefficient if minimum_coefficient is None
                               else min(minimum_coefficient, path.coefficient))
        minimum_delta = (delta if minimum_delta is None
                         else min(minimum_delta, delta))

    back_edges = 0
    if largest_cutoff is not None:
        distance = largest_cutoff - induction.lo
        # Shift the induction origin: y = x - entry.lo >= 0. Every path
        # advances y by at least a*y + d, where a and d are the minima over
        # all live back-edges. This retains branch-dependent affine growth
        # instead of charging one visit per integer below the cutoff.
        a, d = minimum_coefficient, minimum_delta
        if a == 1:
            back_edges = distance // d + 1
        else:
            # y_n >= d*(a**n - 1)/(a - 1). Find the first n strictly
            # beyond the largest cutoff, using integer arithmetic only.
            limit = distance * (a - 1) // d + 1
            low = 1
            high = (limit.bit_length() - 1) // (a.bit_length() - 1) + 1
            while low < high:
                middle = (low + high) // 2
                if pow(a, middle) > limit:
                    high = middle
                else:
                    low = middle + 1
            back_edges = low
    return Range(0 if ranking.guarded else 1, back_edges + 1), Range(induction.lo, largest_entry)


def trip_span(progress, induction, bound):
    """Inclusive interval of natural trip counts for one progress plan.

    A finite upper trip bound needs only the endpoints facing the direction of
    progress: an ascending loop needs an induction lower bound and a limit
    upper bound, and a descending loop needs the converse.  Missing endpoints
    make the lower trip bound zero.  A right-shift loop likewise needs only a
    finite induction upper bound and a nonnegative limit lower bound for its
    maximum; an absent limit upper bound merely makes the minimum trip count
    zero.  ``!=`` loops retain their stricter finite-domain requirement because
    a one-sided bound cannot prove that the recurrence will not step past the
    limit.
    """
    from ..symbolic.ranges import Bounds, Range

    if not isinstance(progress, WhileProgress):
        raise TypeError("a trip span needs WhileProgress")
    induction = Bounds.of(induction)
    bound = Bounds.of(bound)
    if progress.operation == "parent":
        if induction.hi is None \
                or bound.lo != 0 or bound.hi != 0 or progress.inclusive:
            raise WhilePlanError("a binary-parent loop needs a finite index ceiling and zero exit")
        if progress.not_equal and (induction.lo is None or induction.lo < 0):
            raise WhilePlanError("a truth-tested binary-parent loop needs a nonnegative index")
        # With x > 0, negative entry values take the zero-trip path. They
        # cannot invalidate termination of the positive values that enter.
        return Range((max(0, induction.lo or 0) + 1).bit_length() - 1,
                     (max(0, induction.hi) + 1).bit_length() - 1)
    if progress.operation == "lshift":
        if induction.lo is None or induction.lo <= 0 or bound.hi is None:
            raise WhilePlanError(
                "a left-shift while needs a positive induction lower bound "
                "and a finite limit upper bound")
        if progress.not_equal or progress.amount <= 0:
            raise WhilePlanError("an invalid left-shift while plan")

        def trips(value, limit):
            numerator = max(0, limit - int(not progress.inclusive))
            bits = (numerator // value).bit_length()
            return (bits + progress.amount - 1) // progress.amount

        return Range(
            0 if induction.hi is None or bound.lo is None else trips(induction.hi, bound.lo),
            trips(induction.lo, bound.hi),
        )
    if progress.operation == "rshift":
        if induction.hi is None or bound.lo is None \
                or bound.lo < 0:
            raise WhilePlanError(
                "a right-shift while needs a finite upper bound on its induction "
                "and a nonnegative limit lower bound")
        if progress.not_equal and (induction.lo is None or induction.lo < 0):
            raise WhilePlanError("a truth-tested right-shift while needs a nonnegative induction")
        if progress.inclusive or progress.amount <= 0:
            raise WhilePlanError("an invalid right-shift while plan")

        def trips(value, limit):
            quotient = value // (limit + 1)
            bits = quotient.bit_length()
            return (bits + progress.amount - 1) // progress.amount

        return Range(
            (0 if bound.hi is None or induction.lo is None else trips(max(0, induction.lo), bound.hi)),
            trips(max(0, induction.hi), bound.lo),
        )
    if progress.operation != "add" or progress.amount == 0:
        raise WhilePlanError("an invalid additive while plan")

    if progress.not_equal:
        finite_induction = induction.finite()
        finite_bound = bound.finite()
        if finite_induction is None or finite_bound is None:
            raise WhilePlanError(
                "a != while needs finite induction and bound ranges")
        induction, bound = finite_induction, finite_bound
        if progress.amount > 0:
            if induction.hi > bound.lo:
                raise WhilePlanError(
                    "an increasing != while may start beyond its bound")
        elif induction.lo < bound.hi:
            raise WhilePlanError(
                "a decreasing != while may start below its bound")
        if abs(progress.amount) != 1 and (
                induction.lo != induction.hi or bound.lo != bound.hi
                or abs(bound.lo - induction.lo) % abs(progress.amount)):
            raise WhilePlanError(
                "a != while may step past its bound")

    stride = abs(progress.amount)

    def trips(distance):
        numerator = distance + (1 if progress.inclusive else 0)
        return 0 if numerator <= 0 else (numerator + stride - 1) // stride

    if progress.amount > 0:
        if induction.lo is None or bound.hi is None:
            raise WhilePlanError(
                "an increasing while needs an induction lower bound and "
                "a limit upper bound")
        minimum = (0 if induction.hi is None or bound.lo is None else
                   trips(bound.lo - induction.hi))
        maximum = trips(bound.hi - induction.lo)
    else:
        if induction.hi is None or bound.lo is None:
            raise WhilePlanError(
                "a decreasing while needs an induction upper bound and "
                "a limit lower bound")
        minimum = (0 if induction.lo is None or bound.hi is None else
                   trips(induction.lo - bound.hi))
        maximum = trips(induction.hi - bound.lo)
    return Range(minimum, maximum)


def ranking_span(ranking, inductions, bounds):
    """A task-domain interval containing every ranked loop trip count.

    The lower endpoint is zero because the guard may initially be false or an
    entered path may break.  Each back-edge consumes at least one unit of the
    summed distance, so the largest initial distance is an upper endpoint.
    """
    from ..symbolic.ranges import Bounds, Range

    if not isinstance(ranking, WhileRanking):
        raise TypeError("a ranking span needs WhileRanking")
    inductions = tuple(inductions)
    bounds = tuple(bounds)
    if len(inductions) != len(ranking.components) \
            or len(bounds) != len(ranking.components):
        raise ValueError("a ranking span needs one range per component")
    maximum = 0
    for component, induction, bound in zip(
            ranking.components, inductions, bounds):
        induction = Bounds.of(induction)
        bound = Bounds.of(bound)
        extra = 1 if component.inclusive else 0
        if component.direction > 0:
            if induction.lo is None or bound.hi is None:
                raise WhilePlanError(
                    "an increasing ranking component needs an induction "
                    "lower bound and a limit upper bound")
            distance = bound.hi - induction.lo + extra
        else:
            if induction.hi is None or bound.lo is None:
                raise WhilePlanError(
                    "a decreasing ranking component needs an induction "
                    "upper bound and a limit lower bound")
            distance = induction.hi - bound.lo + extra
        maximum += max(0, distance)
    return Range(0, maximum)


def _target_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return f"self.{node.attr}"
    return None


def _affine_target(node):
    """A scalar target plus a fixed additive offset."""
    direct = _target_name(node)
    if direct is not None:
        return direct, 0
    if not isinstance(node, ast.BinOp):
        return None
    left = _target_name(node.left)
    right = _target_name(node.right)
    left_literal = _integer_literal(node.left)
    right_literal = _integer_literal(node.right)
    if left is not None and right_literal is not None:
        if isinstance(node.op, ast.Add):
            return left, right_literal
        if isinstance(node.op, ast.Sub):
            return left, -right_literal
    if right is not None and left_literal is not None \
            and isinstance(node.op, ast.Add):
        return right, left_literal
    return None


def _offset_bound(node, offset, location):
    """Move an induction-side additive offset to the other side."""
    if offset == 0:
        return node
    shifted = ast.BinOp(
        left=node, op=ast.Add(), right=ast.Constant(value=-offset))
    return ast.copy_location(shifted, location)


def _integer_literal(node):
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _integer_literal(node.operand)
        if value is not None:
            return value if isinstance(node.op, ast.UAdd) else -value
    return None


def _update(node, name):
    """One admitted recurrence update, as ``(operation, amount)``."""

    if isinstance(node, ast.AugAssign) and _target_name(node.target) == name:
        value = _integer_literal(node.value)
        if value is None:
            return None
        if isinstance(node.op, ast.Add):
            return ("add", value)
        if isinstance(node.op, ast.Sub):
            return ("add", -value)
        if isinstance(node.op, ast.RShift) and value > 0:
            return ("rshift", value)
        if isinstance(node.op, ast.LShift) and value > 0:
            return ("lshift", value)
        return None
    if not isinstance(node, ast.Assign) or len(node.targets) != 1 \
            or _target_name(node.targets[0]) != name:
        return None
    value = node.value
    if not isinstance(value, ast.BinOp):
        return None
    if (isinstance(value.op, ast.FloorDiv) and _integer_literal(value.right) == 2
            and isinstance(value.left, ast.BinOp) and isinstance(value.left.op, ast.Sub)
            and _target_name(value.left.left) == name
            and _integer_literal(value.left.right) == 1):
        return ("parent", 1)
    literal = _integer_literal(value.right)
    if _target_name(value.left) == name and literal is not None:
        if isinstance(value.op, ast.Add):
            return ("add", literal)
        if isinstance(value.op, ast.Sub):
            return ("add", -literal)
        if isinstance(value.op, ast.RShift) and literal > 0:
            return ("rshift", literal)
        if isinstance(value.op, ast.LShift) and literal > 0:
            return ("lshift", literal)
    literal = _integer_literal(value.left)
    if _target_name(value.right) == name \
            and literal is not None and isinstance(value.op, ast.Add):
        return ("add", literal)
    return None


def _active_scope_nodes(node):
    """Nodes evaluated in the current function activation.

    A nested function's body is not run when its ``def`` statement is run.
    Counting stores and calls in that body as effects of the enclosing while
    both rejects valid loops and, more importantly, confuses lexical code with
    an executed back-edge.  Class statements and lambdas are outside the
    admitted command subset; stop at them for the same reason.  Eager
    comprehensions remain visible because their iterable, filters, and body do
    execute at this point.
    """

    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _comprehension_targets(node):
    """AST identities bound only in a comprehension's private scope."""

    targets = set()
    for current in _active_scope_nodes(node):
        if not isinstance(current, (ast.ListComp, ast.SetComp, ast.DictComp,
                                    ast.GeneratorExp)):
            continue
        for generator in current.generators:
            targets.update(id(part) for part in ast.walk(generator.target))
    return targets


def _stored_names(node):
    """Bindings changed by ``node`` in the containing function scope."""

    private = _comprehension_targets(node)
    names = set()
    for inner in _active_scope_nodes(node):
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            names.add(inner.name)
            continue
        if isinstance(inner, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0]
                         for alias in inner.names)
            continue
        if isinstance(inner, ast.ExceptHandler) and inner.name:
            names.add(inner.name)
        if isinstance(inner, ast.MatchAs) and inner.name:
            names.add(inner.name)
        if isinstance(inner, ast.MatchStar) and inner.name:
            names.add(inner.name)
        if isinstance(inner, ast.MatchMapping) and inner.rest:
            names.add(inner.rest)
        if id(inner) in private:
            continue
        if isinstance(getattr(inner, "ctx", None), (ast.Store, ast.Del)):
            name = _target_name(inner)
            if name is not None:
                names.add(name)
    return names


def _reference(node):
    """One scalar source binding admitted in a while bound."""

    if isinstance(node, ast.Name):
        return ("local", node.id)
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return ("state", node.attr)
    return None


def _references(node, context):
    private = _comprehension_targets(node) if context is ast.Store else set()
    references = set()
    for inner in _active_scope_nodes(node):
        if id(inner) in private:
            continue
        if isinstance(getattr(inner, "ctx", None), context):
            reference = _reference(inner)
            if reference is not None:
                references.add(reference)
        if context is not ast.Store:
            continue
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            references.add(("local", inner.name))
        elif isinstance(inner, (ast.Import, ast.ImportFrom)):
            references.update(
                ("local", alias.asname or alias.name.split(".")[0])
                for alias in inner.names)
        elif (isinstance(inner, ast.ExceptHandler) and inner.name) or (
                isinstance(inner, (ast.MatchAs, ast.MatchStar))
                and inner.name):
            references.add(("local", inner.name))
        elif isinstance(inner, ast.MatchMapping) and inner.rest:
            references.add(("local", inner.rest))
    return references


def _bound_is_pure(node):
    """Whether evaluating a bound has no Python-visible dispatch or mutation."""

    allowed = (
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.Attribute,
        ast.Load,
        ast.UnaryOp,
        ast.UAdd,
        ast.USub,
        ast.Invert,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.LShift,
        ast.RShift,
        ast.BitAnd,
        ast.BitOr,
        ast.BitXor,
    )
    return all(
        isinstance(inner, allowed)
        and (not isinstance(inner, ast.Attribute)
             or _reference(inner) is not None)
        for inner in ast.walk(node)
    )


def _advance_paths(nodes, name, incoming, aliases=None):
    """Return ``(back_edges, live_fallthrough)`` recurrence sets.

    A recurrence is relative to the induction value at the loop head. Paths ending
    in ``break`` or ``return`` are deliberately absent: they do not execute the
    next condition.  A ``continue`` is a back-edge immediately.
    """

    live = set(incoming)
    back_edges = set()
    aliases = dict(aliases or {})
    for node in nodes:
        if not live:
            break
        if isinstance(node, (ast.For, ast.While)):
            # A separately certified nested loop is one fall-through command
            # of the outer body.  It may contain its own break/continue, but
            # it cannot alter the outer induction target.  Its own lowering
            # still has to establish a finite trip plan.
            if name in _stored_names(node):
                raise WhilePlanError(
                    f"a nested loop changes the induction variable {name!r}")
            for changed in _stored_names(node):
                aliases.pop(changed, None)
            # Else may continue the *outer* loop before later progress. Keep
            # fall-through as well: a nested break can bypass the else suite.
            nested_back, _ = _advance_paths(node.orelse, name, live, aliases)
            back_edges |= nested_back
            continue
        if isinstance(node, ast.If):
            if name in _stored_names(node.test):
                raise WhilePlanError(
                    f"a conditional test changes the induction variable {name!r}")
            left_back, left_live = _advance_paths(node.body, name, live, aliases)
            right_back, right_live = _advance_paths(node.orelse, name, live, aliases)
            back_edges |= left_back | right_back
            live = left_live | right_live
            changed = _stored_names(node)
            if name in changed:
                aliases.clear()
            else:
                for target in changed:
                    aliases.pop(target, None)
            continue
        if isinstance(node, (ast.Match, ast.Try, ast.With)) \
                and _contains_current_loop_continue(node):
            # These constructs can hide a back-edge which skips statements
            # that follow them.  Until their branches are represented here,
            # treating that path as fall-through could invent progress.
            raise WhilePlanError(
                "unsupported control flow contains a while continue")
        if isinstance(node, ast.Continue):
            back_edges |= live
            live = set()
            continue
        if isinstance(node, (ast.Break, ast.Return)):
            live = set()
            continue
        writes = name in _stored_names(node)
        if not writes:
            # A helper temporary may capture the exact recurrence before an
            # early-exit branch. This is a proof-only substitution: executable
            # lowering and event identities still use the original statements.
            for changed in _stored_names(node):
                aliases.pop(changed, None)
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                value = aliases.get(node.value.id, node.value) if isinstance(node.value, ast.Name) else node.value
                if _update(ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value), name) is not None:
                    aliases[node.targets[0].id] = value
            continue
        proof = node
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                and node.value.id in aliases):
            proof = ast.Assign(targets=node.targets, value=aliases[node.value.id])
        update = _update(proof, name)
        if update is None:
            raise WhilePlanError(
                f"the induction variable {name!r} has an unsupported update")
        changed = set()
        for current in live:
            if current is None:
                changed.add(update)
                continue
            if current[0] != update[0] or update[0] == "parent":
                raise WhilePlanError(
                    f"the induction variable {name!r} mixes recurrence kinds")
            changed.add((current[0], current[1] + update[1]))
        live = changed
        aliases.clear()
    return back_edges, live


def _contains_current_loop_continue(node):
    """Whether ``node`` contains a continue for the surrounding loop."""

    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Continue):
            return True
        if isinstance(current, (ast.For, ast.While)):
            stack.extend(current.orelse)
            continue
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))
    return False


def _indirect_state_writes(node, resolver, owner):
    """State fields an executed call in ``node`` may write.

    The direct-store check below is not enough for an invariant bound: a call to
    ``self.advance()`` may change ``self.limit`` without an assignment appearing in
    the loop body itself.  Use the same lexical resolver and source-model call graph
    as lowering.  Unknown Python dispatch has no trustworthy effect summary, so it
    invalidates every state-bound proof rather than being assumed pure.

    ``None`` means that at least one call has an unknown state effect.  Builtins and
    registered intrinsics are the only bare calls known not to write model state.
    """

    if resolver is None or owner is None:
        return None if any(isinstance(part, ast.Call)
                           for part in _active_scope_nodes(node)) \
            else frozenset()
    writes = set()
    for call in (part for part in _active_scope_nodes(node)
                 if isinstance(part, ast.Call)):
        if (isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "self"):
            kind, callee = resolver.resolve_method(owner, call.func.attr)
            if kind != "method" or callee is None:
                return None
            writes.update(resolver.model.reachable_state_writes([callee]))
            continue
        if isinstance(call.func, ast.Name):
            kind, callee = resolver.resolve_call(owner, call.func.id)
            if kind in {"builtin", "intrinsic"}:
                continue
            if kind == "alias" and callee in resolver.methods.values():
                writes.update(resolver.model.reachable_state_writes([callee]))
                continue
            if kind in {"nested", "module", "alias"} \
                    and resolver.call_has_no_external_effect(owner, call):
                continue
        return None
    return frozenset(writes)


def _private_readonly_flat_sequence(owner, name, resolver):
    """A fresh flat local whose object never escapes or receives a write.

    Only iteration may read this binding. Its scalar elements cannot carry
    aliases, so mutations of unrelated collections cannot change its values.
    Other source uses need the ordinary effect proof below.
    """
    if any(arg.arg == name for arg in (
            *owner.args.posonlyargs, *owner.args.args, *owner.args.kwonlyargs)):
        return False
    parents = {child: parent for parent in ast.walk(owner)
               for child in ast.iter_child_nodes(parent)}
    initialized = False
    for part, parent in parents.items():
        if not isinstance(part, ast.Name) or part.id != name:
            continue
        if isinstance(part.ctx, ast.Load):
            value = part
            while (isinstance(parent, ast.Call)
                   and isinstance(parent.func, ast.Name)
                   and parent.func.id in {"enumerate", "reversed"}
                   and resolver.is_builtin(owner, parent.func.id)
                   and parent.args and parent.args[0] is value):
                value, parent = parent, parents[parent]
            if not isinstance(parent, (ast.For, ast.comprehension)) \
                    or parent.iter is not value:
                return False
        elif isinstance(part.ctx, ast.Store):
            if (not isinstance(parent, (ast.Assign, ast.AnnAssign))
                    or not isinstance(parent.value, (ast.List, ast.Tuple, ast.ListComp))
                    or (isinstance(parent, ast.Assign) and len(parent.targets) != 1)):
                return False
            initialized = True
        else:
            return False
    return initialized


def loop_preserves_iterable_values(node, *, resolver, owner, scalar_names=(),
                                  is_flat_sequence=lambda _node: False):
    """Whether entry element bounds remain valid at every iterator visit.

    Range values are captured integers. Collection iterators instead read a
    live object: element stores, in-place collection updates and effectful
    calls can invalidate its entry envelope, including through aliases. Use
    the shared lexical call summary rather than a method-name whitelist.
    """
    iterable = node.iter
    while (isinstance(iterable, ast.Call)
           and isinstance(iterable.func, ast.Name)
           and resolver.is_builtin(owner, iterable.func.id)):
        if iterable.func.id == "range":
            return True
        if iterable.func.id not in {"enumerate", "reversed"} or not iterable.args:
            break
        iterable = iterable.args[0]
    if is_flat_sequence(iterable):
        # An anonymous scalar collection cannot expose its container through
        # an iteration target. Calls in the body may modify other objects.
        if isinstance(iterable, (ast.List, ast.Tuple, ast.ListComp)):
            return True
        if (isinstance(iterable, ast.Name)
                and _private_readonly_flat_sequence(owner, iterable.id, resolver)):
            return True
    for root in node.body:
        for part in _active_scope_nodes(root):
            if (isinstance(part, (ast.Subscript, ast.Attribute))
                    and isinstance(part.ctx, (ast.Store, ast.Del))):
                return False
            if (isinstance(part, ast.AugAssign)
                    and _target_name(part.target) not in scalar_names):
                return False
            if (isinstance(part, ast.Call)
                    and not resolver.call_has_no_external_effect(owner, part)):
                return False
    return True


def loop_written_scalars(node, *, resolver=None, owner=None):
    """Scalar source bindings that may change during one loop body visit.

    Direct stores are read from the current lexical activation.  Resolved
    model-method calls contribute their transitive state writes.  If a call
    has no trustworthy state-effect summary, every model state field is
    returned: a lowering may forget a numeric fact conservatively, but it may
    not retain a pre-loop value across an unknown effect.
    """

    if not isinstance(node, (ast.For, ast.While)):
        raise TypeError("loop-written scalars require a source loop")
    written = set().union(
        *(_stored_names(stmt) for stmt in node.body)) if node.body else set()
    indirect = _indirect_state_writes(node, resolver, owner)
    if indirect is None:
        if resolver is not None:
            written.update(
                f"self.{name}" for name in resolver.model.state_contract())
    else:
        written.update(f"self.{name}" for name in indirect)
    return frozenset(written)


def loop_container_writes(node, *, resolver=None, owner=None):
    """Containers whose elements may change during one loop body visit.

    An element store keeps a container's binding and its length, so neither
    ``loop_written_scalars`` nor a length plan reports it, yet a pre-loop fact
    about the elements can be false on the next visit.  Count direct element
    stores, deletes and augmented assignments, and the receiver and arguments
    of every call that is not known to have no external effect.
    """

    if not isinstance(node, (ast.For, ast.While)):
        raise TypeError("loop container writes require a source loop")
    written = set()
    for statement in node.body:
        for inner in _active_scope_nodes(statement):
            if isinstance(inner, ast.Subscript) \
                    and isinstance(inner.ctx, (ast.Store, ast.Del)):
                root = inner.value
                while isinstance(root, ast.Subscript):
                    root = root.value
                name = _target_name(root)
                if name is not None:
                    written.add(name)
            elif isinstance(inner, ast.Call) and not (
                    resolver is not None and owner is not None
                    and resolver.call_has_no_external_effect(owner, inner)):
                operands = [*inner.args, *(item.value for item in inner.keywords)]
                if isinstance(inner.func, ast.Attribute):
                    operands.append(inner.func.value)
                written.update(name for name in map(_target_name, operands)
                               if name is not None and name != "self")
    return frozenset(written)


def analyse(node, *, resolver=None, owner=None):
    """Return an exact monotone progress plan for ``node``.

    Additive recurrences admit ``x < b``, ``x <= b``, ``x > b``, ``x >= b``
    and aligned ``x != b`` (with operands optionally reversed). Integer truth
    tests ``while x`` use the same zero-exit plan as ``while x != 0``. A right-shift
    recurrence admits ``x > b`` and ``x != 0``; a positive left-shift
    recurrence admits ``x < b`` and ``x <= b``. ``x`` is a scalar local or
    model field. Every path which reaches the next condition must apply the
    same recurrence toward exit.
    """

    if not isinstance(node, ast.While):
        raise TypeError("a while progress plan requires ast.While")
    test = node.test
    if _target_name(test) is not None:
        # A proof-only view: the original truth test still owns execution and
        # source events. The caller must establish the scalar domain and the
        # existing recurrence/range checks must prove that zero is reached.
        test = ast.copy_location(ast.Compare(
            left=test, ops=[ast.NotEq()], comparators=[ast.Constant(value=0)]), test)
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 \
            or len(test.comparators) != 1:
        raise WhilePlanError(
            "a while condition must be one monotone scalar comparison")
    left, right = test.left, test.comparators[0]
    op = test.ops[0]
    affine = _affine_target(left)
    name = None if affine is None else affine[0]
    bound = right if affine is None else _offset_bound(
        right, affine[1], test)
    not_equal = False
    if isinstance(op, ast.NotEq):
        if name is not None:
            not_equal = True
        else:
            affine = _affine_target(right)
            if affine is not None:
                name = affine[0]
                bound = _offset_bound(left, affine[1], test)
                not_equal = True
    elif name is None:
        affine = _affine_target(right)
        name = None if affine is None else affine[0]
        bound = (left if affine is None else
                 _offset_bound(left, affine[1], test))
        op = {
            ast.Lt: ast.Gt,
            ast.LtE: ast.GtE,
            ast.Gt: ast.Lt,
            ast.GtE: ast.LtE,
        }.get(type(op), lambda: None)()
    if name is None or (not not_equal and not isinstance(
            op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))):
        raise WhilePlanError(
            "a while condition must compare one scalar induction variable")
    if not _bound_is_pure(bound):
        raise WhilePlanError(
            "a while bound must be a side-effect-free scalar expression")
    bound_references = _references(bound, ast.Load)
    progress_reference = (("state", name[5:]) if name.startswith("self.")
                          else ("local", name))
    if progress_reference in bound_references:
        raise WhilePlanError("a while bound depends on its induction variable")
    body_stores = set().union(
        *(_references(stmt, ast.Store) for stmt in node.body)) \
        if node.body else set()
    changed_bounds = sorted(bound_references & body_stores)
    if changed_bounds:
        kind, spelling = changed_bounds[0]
        shown = f"self.{spelling}" if kind == "state" else spelling
        raise WhilePlanError(
            f"the while bound changes in the body: {shown!r}")
    state_bounds = {
        spelling for kind, spelling in bound_references if kind == "state"
    }
    state_progress = ({name[5:]} if name.startswith("self.") else set())
    if state_bounds or state_progress:
        indirect = _indirect_state_writes(node, resolver, owner)
        if indirect is None:
            raise WhilePlanError(
                "a call in the while body has no proved state-effect summary")
        changed_progress = sorted(state_progress & set(indirect))
        if changed_progress:
            raise WhilePlanError(
                "the while induction is changed by a called helper: "
                f"self.{changed_progress[0]}")
        changed = sorted(state_bounds & set(indirect))
        if changed:
            raise WhilePlanError(
                "the while bound is changed by a called helper: "
                f"self.{changed[0]}")

    back_edges, fallthrough = _advance_paths(node.body, name, {None})
    recurrences = back_edges | fallthrough
    if not recurrences:
        # Every entered path breaks, returns, or fails before another test.  A
        # one-step upper plan is exact when guarded by the existing exit flag,
        # but there is no recurrence to synthesise from the source condition.
        raise WhilePlanError("the while has no progressing back-edge")
    if len(recurrences) != 1:
        raise WhilePlanError(
            "different while back-edges advance the induction variable differently")
    recurrence = next(iter(recurrences))
    if recurrence is None or recurrence[1] == 0:
        raise WhilePlanError(
            f"a while back-edge does not advance {name!r}")
    operation, amount = recurrence
    ascending = (amount > 0 if not_equal else
                 isinstance(op, (ast.Lt, ast.LtE)))
    if operation == "add" and ((ascending and amount < 0)
                               or (not ascending and amount > 0)):
        raise WhilePlanError(
            f"the while moves {name!r} away from its exit condition")
    if (operation == "rshift" and not not_equal and isinstance(op, ast.GtE)
            and _integer_literal(bound) is not None):
        # On integers x >= c is x > c - 1, the exclusive form below. The
        # trip plan still refuses c - 1 < 0, where the shift never exits.
        bound = ast.copy_location(
            ast.Constant(value=_integer_literal(bound) - 1), bound)
        op = ast.Gt()
    if operation == "rshift" and (
            (not not_equal and not isinstance(op, ast.Gt))
            or (not_equal and _integer_literal(bound) != 0)
            or amount <= 0):
        raise WhilePlanError(
            "a right-shift while must decrease toward an exclusive lower bound")
    if operation == "parent" and (
            (not not_equal and not isinstance(op, ast.Gt))
            or _integer_literal(bound) != 0):
        raise WhilePlanError("a binary-parent loop must decrease toward zero")
    if operation == "lshift" and (
            not_equal or not isinstance(op, (ast.Lt, ast.LtE)) or amount <= 0):
        raise WhilePlanError(
            "a left-shift while must increase toward an upper bound")
    return WhileProgress(
        bound=bound,
        inclusive=isinstance(op, (ast.LtE, ast.GtE)),
        name=name,
        operation=operation,
        amount=amount,
        not_equal=not_equal,
    )


_DEPENDENT = object()
_EXIT_PATH_LIMIT = defaults.SIM_BEHAVIOR_LOOP_EXIT_PATHS


@dataclass
class _ExitState:
    values: dict
    cutoffs: tuple[WhileCutoff, ...] = ()

    def copy(self):
        return _ExitState(dict(self.values), self.cutoffs)


def _depends_on_exit_induction(value):
    return value is _DEPENDENT or (
        isinstance(value, tuple) and value[0] != 0)


def _exit_affine(node, values):
    """Affine value in the loop-head induction, an opaque dependency, or none."""

    literal = _integer_literal(node)
    if literal is not None:
        return (0, literal)
    name = _target_name(node)
    if name is not None:
        return values.get(name)
    if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)):
        value = _exit_affine(node.operand, values)
        if value is _DEPENDENT or value is None:
            return value
        return value if isinstance(node.op, ast.UAdd) else (-value[0], -value[1])
    if isinstance(node, ast.BinOp):
        left = _exit_affine(node.left, values)
        right = _exit_affine(node.right, values)
        if left is _DEPENDENT or right is _DEPENDENT:
            return _DEPENDENT
        if isinstance(node.op, (ast.Add, ast.Sub)):
            sign = 1 if isinstance(node.op, ast.Add) else -1
            if left is not None and right is not None:
                return (left[0] + sign * right[0],
                        left[1] + sign * right[1])
            known = left if right is None else right
            return (_DEPENDENT if _depends_on_exit_induction(known) else None)
        if isinstance(node.op, ast.Mult):
            if left is not None and left[0] == 0 and right is not None:
                return (left[1] * right[0], left[1] * right[1])
            if right is not None and right[0] == 0 and left is not None:
                return (right[1] * left[0], right[1] * left[1])
        if isinstance(node.op, ast.LShift) and right is not None \
                and right[0] == 0 and right[1] >= 0 and left is not None:
            factor = 1 << right[1]
            return (factor * left[0], factor * left[1])
        return (_DEPENDENT if _depends_on_exit_induction(left)
                or _depends_on_exit_induction(right) else None)
    children = [_exit_affine(child, values)
                for child in ast.iter_child_nodes(node)]
    return (_DEPENDENT if any(_depends_on_exit_induction(child)
                              for child in children) else None)


def _exit_cutoff(test, truth, state):
    """An upper constraint implied by one branch, when one is explicit."""

    if not isinstance(test, ast.Compare) or len(test.ops) != 1 \
            or len(test.comparators) != 1:
        return None
    left, right = test.left, test.comparators[0]
    op = test.ops[0]
    if not truth:
        op = {
            ast.Lt: ast.GtE,
            ast.LtE: ast.Gt,
            ast.Gt: ast.LtE,
            ast.GtE: ast.Lt,
        }.get(type(op), lambda: None)()
    affine = _exit_affine(left, state.values)
    bound = right
    if not isinstance(affine, tuple) or affine[0] <= 0:
        affine = _exit_affine(right, state.values)
        bound = left
        op = {
            ast.Lt: ast.Gt,
            ast.LtE: ast.GtE,
            ast.Gt: ast.Lt,
            ast.GtE: ast.LtE,
        }.get(type(op), lambda: None)()
    if not isinstance(affine, tuple) or affine[0] <= 0 \
            or not isinstance(op, (ast.Lt, ast.LtE)):
        return None
    if _depends_on_exit_induction(_exit_affine(bound, state.values)) \
            or not _bound_is_pure(bound):
        return None
    return WhileCutoff(
        affine[0], affine[1], bound, isinstance(op, ast.LtE))


def _assign_exit_target(state, target, value):
    name = (_target_name(target) if isinstance(
        getattr(target, "ctx", None), ast.Store) else None)
    if name is not None:
        state.values[name] = value
        return
    if isinstance(target, ast.Starred):
        _assign_exit_target(state, target.value, _DEPENDENT)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for inner in target.elts:
            _assign_exit_target(state, inner, _DEPENDENT)


def _exit_states(statements, states):
    """Symbolically follow straight-line/conditional paths to loop back-edges."""

    live = list(states)
    back_edges = []
    for statement in statements:
        if not live:
            break
        if isinstance(statement, ast.If):
            if _stored_names(statement.test):
                raise WhilePlanError(
                    "a cutoff-ranked while condition changes a local value")
            joined = []
            for state in live:
                for truth, branch in (
                        (True, statement.body), (False, statement.orelse)):
                    child = state.copy()
                    cutoff = _exit_cutoff(statement.test, truth, child)
                    if cutoff is not None:
                        child.cutoffs += (cutoff,)
                    branch_back, branch_live = _exit_states(
                        branch, [child])
                    back_edges.extend(branch_back)
                    joined.extend(branch_live)
            live = joined
        elif isinstance(statement, ast.Continue):
            back_edges.extend(live)
            live = []
        elif isinstance(statement, (ast.Break, ast.Return)):
            live = []
        elif isinstance(statement, ast.Assign):
            for state in live:
                value = _exit_affine(statement.value, state.values)
                for target in statement.targets:
                    _assign_exit_target(state, target, value)
        elif isinstance(statement, ast.AnnAssign):
            for state in live:
                value = (None if statement.value is None else
                         _exit_affine(statement.value, state.values))
                _assign_exit_target(state, statement.target, value)
        elif isinstance(statement, ast.AugAssign):
            for state in live:
                name = _target_name(statement.target)
                left = (None if name is None else
                        state.values.get(name))
                synthetic = ast.BinOp(
                    left=statement.target, op=statement.op,
                    right=statement.value)
                value = (_exit_affine(synthetic, state.values)
                         if left is not None else None)
                _assign_exit_target(state, statement.target, value)
        elif isinstance(statement, (ast.For, ast.While, ast.Try, ast.With,
                                    ast.Match)):
            raise WhilePlanError(
                "a cutoff-ranked while body has unsupported nested control")
        else:
            changed = _stored_names(statement)
            for state in live:
                for name in changed:
                    state.values[name] = _DEPENDENT
        if len(live) + len(back_edges) > _EXIT_PATH_LIMIT:
            raise WhilePlanError(
                "a cutoff-ranked while has too many control paths")
    return back_edges, live


def _block_has_exit(statements):
    """Whether this loop body has a direct break or function return."""

    stack = list(statements)
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.Break, ast.Return)):
            return True
        if isinstance(current, (ast.For, ast.While, ast.FunctionDef,
                                ast.AsyncFunctionDef, ast.Lambda,
                                ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))
    return False


def analyse_exit_ranking(node, *, resolver=None, owner=None):
    """Prove finite execution using guard- or exit-dominated affine progress.

    The proof follows affine scalar aliases through assignments and ordinary
    conditionals.  Every path reaching a back-edge must strictly advance one
    candidate and must retain an invariant upper constraint established by a
    preceding branch.  The task-domain ranges discharge strict progress and
    turn those constraints into a concrete visit bound later.
    """

    if not isinstance(node, ast.While):
        raise TypeError("an exit ranking requires ast.While")
    constant_true = (isinstance(node.test, ast.Constant)
                     and type(node.test.value) is bool and node.test.value)
    if constant_true and not _block_has_exit(node.body):
        raise WhilePlanError(
            "a cutoff-ranked while needs a reachable break or return")
    if not constant_true and (not isinstance(node.test, ast.Compare)
                              or not _bound_is_pure(node.test.left)
                              or any(not _bound_is_pure(value) for value in node.test.comparators)):
        raise WhilePlanError("a cutoff-ranked while needs a pure affine comparison or True")

    candidates = sorted(set().union(
        *(_stored_names(statement) for statement in node.body)))
    admitted = []
    for candidate in candidates:
        try:
            entry = _ExitState({candidate: (1, 0)})
            if not constant_true:
                cutoff = _exit_cutoff(node.test, True, entry)
                if cutoff is None:
                    continue
                entry.cutoffs = (cutoff,)
            back_edges, live = _exit_states(
                node.body, [entry])
            states = back_edges + live
            paths = []
            for state in states:
                update = state.values.get(candidate)
                if (not isinstance(update, tuple) or update[0] < 1
                        or (update[0] == 1 and update[1] <= 0)):
                    raise WhilePlanError(
                        "a cutoff-ranked back-edge has no affine progress")
                if not state.cutoffs:
                    raise WhilePlanError(
                        "a cutoff-ranked back-edge has no preceding upper bound")
                paths.append(WhileExitPath(
                    update[0], update[1], state.cutoffs))
            if not paths:
                continue

            bound_references = set().union(*(
                _references(cutoff.bound, ast.Load)
                for path in paths for cutoff in path.cutoffs))
            body_stores = set().union(
                *(_references(stmt, ast.Store) for stmt in node.body))
            if bound_references & body_stores:
                raise WhilePlanError(
                    "a cutoff-ranked while changes an exit bound")
            state_names = {
                spelling for kind, spelling in bound_references
                if kind == "state"
            }
            if candidate.startswith("self."):
                state_names.add(candidate[5:])
            indirect = _indirect_state_writes(node, resolver, owner)
            if indirect is None or state_names & set(indirect):
                raise WhilePlanError(
                    "a cutoff-ranked while has an unknown state effect")
            admitted.append(WhileExitRanking(candidate, tuple(paths), not constant_true))
        except WhilePlanError:
            continue
    if len(admitted) != 1:
        raise WhilePlanError(
            "a while needs one proved affine cutoff ranking")
    return admitted[0]


def _ranking_terms(test):
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        terms = []
        for value in test.values:
            terms.extend(_ranking_terms(value))
        return terms
    return [test]


def _ranking_component(test):
    """One monotone comparison used as a ranking component."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 \
            or len(test.comparators) != 1:
        raise WhilePlanError(
            "a ranked while condition must be a conjunction of scalar "
            "comparisons")
    left, right = test.left, test.comparators[0]
    op = test.ops[0]
    affine = _affine_target(left)
    if affine is not None:
        name, offset = affine
        bound = _offset_bound(right, offset, test)
    else:
        affine = _affine_target(right)
        if affine is None:
            raise WhilePlanError(
                "a ranked while comparison must contain one scalar variable")
        name, offset = affine
        bound = _offset_bound(left, offset, test)
        op = {
            ast.Lt: ast.Gt,
            ast.LtE: ast.GtE,
            ast.Gt: ast.Lt,
            ast.GtE: ast.LtE,
        }.get(type(op), lambda: None)()
    if not isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        raise WhilePlanError(
            "a ranked while comparison must have an ordered exit bound")
    if not _bound_is_pure(bound):
        raise WhilePlanError(
            "a ranked while bound must be a side-effect-free scalar expression")
    return WhileRankComponent(
        bound=bound,
        inclusive=isinstance(op, (ast.LtE, ast.GtE)),
        name=name,
        direction=1 if isinstance(op, (ast.Lt, ast.LtE)) else -1,
    )


def _ranking_back_edges(nodes, names, incoming):
    """Accumulated additive deltas on all back-edges through ``nodes``."""
    positions = {name: index for index, name in enumerate(names)}
    live = set(incoming)
    back_edges = set()
    for node in nodes:
        if not live:
            break
        if isinstance(node, (ast.For, ast.While)):
            changed = set(names) & _stored_names(node)
            if changed:
                raise WhilePlanError(
                    "a nested loop changes a ranked induction variable")
            nested_back, _ = _ranking_back_edges(node.orelse, names, live)
            back_edges |= nested_back
            continue
        if isinstance(node, ast.If):
            if set(names) & _stored_names(node.test):
                raise WhilePlanError(
                    "a conditional test changes a ranked induction variable")
            left_back, left_live = _ranking_back_edges(
                node.body, names, live)
            right_back, right_live = _ranking_back_edges(
                node.orelse, names, live)
            back_edges |= left_back | right_back
            live = left_live | right_live
            continue
        if isinstance(node, (ast.Match, ast.Try, ast.With)) \
                and _contains_current_loop_continue(node):
            raise WhilePlanError(
                "unsupported control flow contains a ranked while continue")
        if isinstance(node, ast.Continue):
            back_edges |= live
            live = set()
            continue
        if isinstance(node, (ast.Break, ast.Return)):
            live = set()
            continue
        stores = _stored_names(node)
        changed = [name for name in names if name in stores]
        if not changed:
            continue
        updates = {}
        for name in changed:
            update = _update(node, name)
            if update is None or update[0] != "add":
                raise WhilePlanError(
                    f"the ranked induction variable {name!r} has an "
                    "unsupported update")
            updates[name] = update[1]
        advanced = set()
        for current in live:
            delta = list(current)
            for name, amount in updates.items():
                delta[positions[name]] += amount
            advanced.add(tuple(delta))
        live = advanced
    return back_edges, live


def analyse_ranking(node, *, resolver=None, owner=None):
    """Prove a finite sum ranking for a data-dependent ``while``.

    Every conjunct contributes its integer distance to an invariant bound.
    A back-edge may advance any subset of the induction variables, but none
    may move away from its bound and at least one must move toward it.  This is
    deliberately an upper-bound proof: the original condition is evaluated by
    the retained loop, so branch-dependent progress is not approximated as an
    exact trip count.
    """
    if not isinstance(node, ast.While):
        raise TypeError("a while ranking requires ast.While")
    components = tuple(
        _ranking_component(term) for term in _ranking_terms(node.test))
    names = tuple(component.name for component in components)
    if len(set(names)) != len(names):
        raise WhilePlanError(
            "a ranked while condition repeats an induction variable")

    progress_references = {
        (("state", name[5:]) if name.startswith("self.") else
         ("local", name))
        for name in names
    }
    bound_references = set()
    for component in components:
        bound_references.update(_references(component.bound, ast.Load))
    if progress_references & bound_references:
        raise WhilePlanError(
            "a ranked while bound depends on an induction variable")
    body_stores = set().union(
        *(_references(stmt, ast.Store) for stmt in node.body)) \
        if node.body else set()
    changed_bounds = sorted(bound_references & body_stores)
    if changed_bounds:
        kind, spelling = changed_bounds[0]
        shown = f"self.{spelling}" if kind == "state" else spelling
        raise WhilePlanError(
            f"the ranked while bound changes in the body: {shown!r}")

    state_names = {
        spelling for kind, spelling in
        (bound_references | progress_references) if kind == "state"
    }
    if state_names:
        indirect = _indirect_state_writes(node, resolver, owner)
        if indirect is None:
            raise WhilePlanError(
                "a call in the ranked while has no proved state-effect summary")
        changed = sorted(state_names & set(indirect))
        if changed:
            raise WhilePlanError(
                "a ranked while value is changed by a called helper: "
                f"self.{changed[0]}")

    zero = (0,) * len(components)
    back_edges, fallthrough = _ranking_back_edges(
        node.body, names, {zero})
    deltas = back_edges | fallthrough
    if not deltas:
        raise WhilePlanError("the ranked while has no back-edge")
    for delta in deltas:
        progress = [
            component.direction * amount
            for component, amount in zip(components, delta)
        ]
        if any(amount < 0 for amount in progress):
            raise WhilePlanError(
                "a ranked while back-edge moves away from an exit bound")
        if not any(amount > 0 for amount in progress):
            raise WhilePlanError(
                "a ranked while back-edge makes no progress")
    maximum_steps = tuple(
        max(component.direction * delta[index] for delta in deltas)
        for index, component in enumerate(components)
    )
    return WhileRanking(components, maximum_steps)
