"""Finite-table progress proofs for flag-controlled source loops.

The flag is reset on every visit and can be set only after a strict ordered
replacement of a scalar by an element of an unchanged finite table. Thus at
most ``table_capacity`` visits can make progress, followed by one final visit.
This proves a bound; it does not add a runtime cutoff or rewrite source events.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .while_plan import WhilePlanError
from ..symbolic.value_contract import SCALAR
from .worklist_plan import analyse_worklist
from ..symbolic.ranges import Bounds


@dataclass(frozen=True)
class FiniteProgressDomain:
    maximum: int
    head_bounds: tuple[tuple[ast.expr, Bounds], ...] = ()


def finite_progress_domain(node, *, resolver, owner, contract_of, bounds_of):
    """One domain proof shared by source planning and both executable lowerers."""
    try:
        plan = analyse_finite_descent(node, resolver=resolver, owner=owner)
    except WhilePlanError as descent_error:
        try:
            plan = analyse_worklist(node, resolver=resolver, owner=owner)
            maximum = plan.maximum(bounds_of)
            # Every admission consumes a previously clear visited bit. The
            # bound is for the WHOLE loop, not a fresh supply on every visit.
            # Helper deltas describe complete calls, so use this invariant at
            # the loop head only, never inside a helper's transient updates.
            ceiling = bounds_of(plan.count).hi + sum(plan.admission_sizes)
            return FiniteProgressDomain(maximum, ((plan.count, Bounds(hi=ceiling)),))
        except WhilePlanError as worklist_error:
            raise WhilePlanError(f"{descent_error}; {worklist_error}") from None
    table = contract_of(plan.table)
    if not table.is_sequence or table.element != SCALAR or table.maximum is None:
        raise WhilePlanError("finite-table descent requires a finite scalar sequence")
    return FiniteProgressDomain(
        plan.maximum(table.maximum, bounds_of(ast.Name(id=plan.flag, ctx=ast.Load()))))


@dataclass(frozen=True)
class FiniteDescent:
    flag: str
    rank: str
    table: ast.expr
    direction: type[ast.cmpop]
    indexed: bool = False
    counter: bool = False

    def maximum(self, capacity, counter_range=None):
        if not self.counter:
            return capacity + 1
        if counter_range is None or counter_range.lo is None \
                or counter_range.hi is None or counter_range.lo < 0:
            raise WhilePlanError("finite-table progress needs a nonnegative finite counter")
        # A body visit either strictly improves a table value or decrements
        # the counter. The table is invariant even across counter changes.
        return capacity + counter_range.hi


def _same(left, right):
    return ast.dump(left) == ast.dump(right)


def _assignment(node, name):
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name)


def _replacement(stmt, flag):
    if not (isinstance(stmt, ast.If)
            and isinstance(stmt.test, ast.Compare)
            and len(stmt.test.ops) == len(stmt.test.comparators) == 1
            and isinstance(stmt.test.ops[0], (ast.Lt, ast.Gt))
            and isinstance(stmt.test.left, ast.Subscript) and stmt.body):
        return None
    candidate = stmt.test.left
    old = stmt.test.comparators[0]
    if isinstance(old, ast.Name):
        rank, value = old.id, candidate
    elif (isinstance(old, ast.Subscript) and isinstance(old.slice, ast.Name)
          and _same(candidate.value, old.value)):
        rank, value = old.slice.id, candidate.slice
    else:
        return None
    if not (_assignment(stmt.body[0], rank)
            and _same(stmt.body[0].value, value)):
        return None
    if not (isinstance(candidate.value, ast.Name)
            or (isinstance(candidate.value, ast.Attribute)
                and isinstance(candidate.value.value, ast.Name)
                and candidate.value.value.id == "self")):
        return None
    return FiniteDescent(flag, rank, candidate.value, type(stmt.test.ops[0]),
                         isinstance(old, ast.Subscript))


def analyse_finite_descent(node, *, resolver, owner):
    """Recognise a progress flag guarded by a strict finite-table replacement.

    Arbitrary helper effects, mutation through aliases and writes to the rank
    outside its strict comparison are not a termination proof. The executable
    frontend still establishes finite nested iterators and scalar element types.
    """
    if not isinstance(node.test, ast.Name) or not node.body:
        raise WhilePlanError("finite-table descent needs a scalar progress flag")
    flag = node.test.id
    parts = tuple(part for stmt in node.body for part in ast.walk(stmt))
    candidates = [found for part in parts
                  if (found := _replacement(part, flag)) is not None]
    if not candidates:
        raise WhilePlanError("no strict finite-table replacement controls the flag")
    plan = candidates[0]
    first = node.body[0]
    reset_flag = (_assignment(first, flag) and isinstance(first.value, ast.Constant)
                  and type(first.value.value) in (bool, int) and first.value.value == 0)
    decrement = None
    snapshot = None
    body = node.body
    if not reset_flag:
        # A captured cursor equal at the end means that no strict improvement
        # occurred. This path must decrement the source counter. No copied
        # table values or guessed graph size enter the proof.
        if not (isinstance(first, ast.Assign) and len(first.targets) == 1
                and isinstance(first.targets[0], ast.Name)
                and isinstance(first.value, ast.Name) and first.value.id == plan.rank):
            raise WhilePlanError("finite-table descent needs a flag reset or cursor snapshot")
        snapshot = first.targets[0].id
        last = node.body[-1]
        if not (isinstance(last, ast.If) and not last.orelse
                and isinstance(last.test, ast.Compare) and len(last.test.ops) == 1
                and isinstance(last.test.ops[0], ast.Eq)
                and len(last.test.comparators) == 1
                and isinstance(last.test.left, ast.Name)
                and isinstance(last.test.comparators[0], ast.Name)
                and {last.test.left.id, last.test.comparators[0].id} == {snapshot, plan.rank}
                and len(last.body) == 1):
            raise WhilePlanError("unchanged finite-table cursor must decrement the counter")
        decrement = last.body[0]
        if not (isinstance(decrement, ast.AugAssign)
                and isinstance(decrement.target, ast.Name) and decrement.target.id == flag
                and isinstance(decrement.op, ast.Sub)
                and isinstance(decrement.value, ast.Constant)
                and type(decrement.value.value) is int and decrement.value.value == 1):
            raise WhilePlanError("finite-table counter must decrease by one")
        # This form tracks table[cursor], not a separate scalar with the same
        # spelling as a cursor. Only strict indexed comparisons establish it.
        if any(not isinstance(part.test.comparators[0], ast.Subscript)
               for part in parts if _replacement(part, flag) is not None):
            raise WhilePlanError("finite-table counter needs an indexed rank")
        plan = FiniteDescent(flag, plan.rank, plan.table, plan.direction, True, True)
        body = node.body[1:-1]
    table_name = plan.table.id if isinstance(plan.table, ast.Name) else None
    if plan.rank == flag or table_name in (flag, plan.rank) \
            or (snapshot is not None and snapshot in (flag, plan.rank, table_name)):
        raise WhilePlanError("progress flag, rank and finite table are distinct")

    # Calls are checked by lexical identity, not by a user-chosen function name.
    # No container writes (including through another name) are admitted here.
    for part in parts:
        if isinstance(part, (ast.NamedExpr, ast.Delete, ast.Try, ast.With,
                             ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Lambda)) or (isinstance(part, ast.While) and part is not node):
            raise WhilePlanError("finite-table descent has an unproved control/effect")
        if isinstance(part, (ast.Subscript, ast.Attribute)) and isinstance(
                part.ctx, (ast.Store, ast.Del)):
            raise WhilePlanError("finite-table descent requires an unchanged table")
        if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store) \
                and part.id == table_name:
            raise WhilePlanError("the finite table is rebound in the loop")
        if isinstance(part, ast.AugAssign) and part is not decrement:
            raise WhilePlanError("finite-table descent has an unproved in-place update")
        if isinstance(part, ast.Call) and not (
                isinstance(part.func, ast.Name) and part.func.id in {"range", "len"}
                and resolver is not None and resolver.is_builtin(owner, part.func.id)):
            raise WhilePlanError("finite-table descent has an unproved call effect")
        if plan.counter and isinstance(part, ast.Continue):
            raise WhilePlanError("finite-table counter check can be bypassed")

    def block(statements, improved=False):
        for stmt in statements:
            found = _replacement(stmt, flag)
            if found is not None and found.rank == plan.rank:
                if found.direction is not plan.direction or found.indexed != plan.indexed \
                        or not _same(found.table, plan.table):
                    raise WhilePlanError("finite-table replacements use one strict order")
                block(stmt.body[1:], True)
                block(stmt.orelse, improved)
                continue
            if isinstance(stmt, ast.If):
                block(stmt.body, improved)
                block(stmt.orelse, improved)
                continue
            if isinstance(stmt, ast.For):
                if any(isinstance(part, ast.Name) and part.id in (flag, plan.rank, snapshot)
                       for part in ast.walk(stmt.target)):
                    raise WhilePlanError("iteration rebinds the finite-table proof")
                block(stmt.body, improved)
                block(stmt.orelse, improved)
                continue
            stored = {part.id for part in ast.walk(stmt)
                      if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store)}
            if plan.rank in stored:
                raise WhilePlanError("the rank changes outside a strict replacement")
            if snapshot in stored or (plan.counter and flag in stored):
                raise WhilePlanError("the finite-table counter or snapshot is rebound")
            if flag in stored and not (
                    _assignment(stmt, flag) and isinstance(stmt.value, ast.Constant)
                    and type(stmt.value.value) in (bool, int)
                    and (stmt.value.value == 0 or improved)):
                raise WhilePlanError("the continuation flag lacks strict progress")

    block(body)
    return plan
