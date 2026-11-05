"""Amortized bounds for a scalar queue count with visited-bit admission.

One item is consumed on every back-edge. A replenishment can happen at most
once for each newly marked bit, so visits <= initial count + possible new bits.
Literal masks/moduli prove invariant bit domains, independently of changing
loop-carried values. The source planner supplies the initial counter range.
"""

import ast
from dataclasses import dataclass

from .while_plan import WhilePlanError, _indirect_state_writes, _integer_literal
from .counter_effects import CounterEffects, state_cell


def _name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _literal(node, value):
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value == value


def _change(node, name, operation):
    return (isinstance(node, ast.AugAssign) and _name(node.target, name)
            and isinstance(node.op, operation) and _literal(node.value, 1))


@dataclass(frozen=True)
class WorklistProgress:
    count: ast.expr
    floor: ast.expr
    admission_sizes: tuple[int, ...]
    truth_test: bool = False

    def maximum(self, bounds_of):
        initial = bounds_of(self.count)
        floor = bounds_of(self.floor)
        if initial is None or initial.hi is None or (self.truth_test and
                (initial.lo is None or initial.lo < 0)):
            raise WhilePlanError("worklist progress needs a finite entry bound; truth tests need nonnegativity")
        if floor is None or floor.lo is None or floor.lo < 0:
            raise WhilePlanError("worklist exit needs an invariant nonnegative lower bound")
        return max(0, initial.hi - floor.lo) + sum(self.admission_sizes)


def _index_domain_size(expression):
    # These bounds hold at *every* visit, regardless of loop-carried inputs.
    # A pre-loop range of a changing index would not prove this invariant.
    if isinstance(expression, ast.BinOp):
        bound = _integer_literal(expression.right)
        if bound is not None and isinstance(expression.op, ast.BitAnd) and bound >= 0:
            return bound + 1
        if bound is not None and isinstance(expression.op, ast.Mod) and bound > 0:
            return bound
    raise WhilePlanError("visited-bit index has no invariant finite domain")


def analyse_worklist(node, *, resolver=None, owner=None):
    # Model methods can change state, but cannot rebind these caller-local
    # integers. Unknown calls and closures with external effects need a
    # separate proof; assuming they are pure would hide counter/marker writes.
    if _indirect_state_writes(node, resolver, owner) is None:
        raise WhilePlanError("worklist calls have unproved external effects")
    test = node.test
    floor = ast.Constant(value=0)
    if isinstance(test, ast.Name):
        count = test
    elif (isinstance(test, ast.Compare)
          and len(test.ops) == len(test.comparators) == 1
          and isinstance(test.ops[0], ast.Gt)
          and isinstance(test.comparators[0], (ast.Name, ast.Constant))):
        count, floor = test.left, test.comparators[0]
    else:
        raise WhilePlanError("worklist progress needs a scalar pending count")
    local = count.id if isinstance(count, ast.Name) else None
    cell = state_cell(count)
    if local is None and cell is None:
        raise WhilePlanError("worklist count needs a local or fixed indexed state cell")
    projection = CounterEffects(cell, resolver) if cell is not None else None
    effects = {}

    def effect(stmt):
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
            return 0
        if id(stmt) not in effects:
            effects[id(stmt)] = (projection.delta(stmt, owner) if projection else
                                -1 if _change(stmt, local, ast.Sub) else
                                1 if _change(stmt, local, ast.Add) else 0)
        return effects[id(stmt)]

    decrements = [stmt for stmt in node.body if effect(stmt) == -1]
    if len(decrements) != 1:
        raise WhilePlanError("worklist progress consumes one item on every visit")
    consumed = decrements[0]
    allowed_count = {id(consumed)}
    markers = []
    domains = []

    def block(statements):
        assignments = {}
        for stmt in statements:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)):
                assignments[stmt.targets[0].id] = stmt.value
            # not ((visited >> index) & 1), immediately followed by the mark.
            condition = stmt.test if isinstance(stmt, ast.If) else None
            unseen = (condition.operand if isinstance(condition, ast.UnaryOp)
                      and isinstance(condition.op, ast.Not) else
                      condition.left if isinstance(condition, ast.Compare)
                      and len(condition.ops) == len(condition.comparators) == 1
                      and isinstance(condition.ops[0], ast.Eq)
                      and _literal(condition.comparators[0], 0) else None)
            if (isinstance(unseen, ast.BinOp) and isinstance(unseen.op, ast.BitAnd)
                    and _literal(unseen.right, 1)):
                shift = unseen.left
                if (isinstance(shift, ast.BinOp) and isinstance(shift.op, ast.RShift)
                        and isinstance(shift.left, ast.Name) and isinstance(shift.right, ast.Name)
                        and shift.right.id in assignments and stmt.body):
                    mark = stmt.body[0]
                    if (isinstance(mark, ast.AugAssign) and _name(mark.target, shift.left.id)
                            and isinstance(mark.op, ast.BitOr) and isinstance(mark.value, ast.BinOp)
                            and isinstance(mark.value.op, ast.LShift)
                            and _literal(mark.value.left, 1) and _name(mark.value.right, shift.right.id)):
                        increases = [p for root in stmt.body[1:] for p in ast.walk(root)
                                     if effect(p) == 1]
                        # A marker can replenish at most once. Nested loops or
                        # a back-edge before the top-level consumption need a
                        # different proof, never an assumed iteration quota.
                        if len(increases) == 1 and not any(
                                isinstance(p, (ast.For, ast.While))
                                for root in stmt.body[1:] for p in ast.walk(root)):
                            allowed_count.add(id(increases[0]))
                            markers.append(mark)
                            domains.append(_index_domain_size(assignments[shift.right.id]))
            if isinstance(stmt, (ast.For, ast.If)):
                block(stmt.body)
                block(stmt.orelse)
            # Only a directly preceding, still-current assignment supplies an
            # index domain. Do not carry facts across control/effectful statements.
            if not isinstance(stmt, ast.Assign):
                assignments.clear()

    block(node.body)
    seen = {mark.target.id for mark in markers}
    if local in seen:
        raise WhilePlanError("worklist count and visited set must be distinct")
    allowed_seen = {id(mark) for mark in markers}
    for root in node.body:
        for part in ast.walk(root):
            if isinstance(part, (ast.While, ast.Continue, ast.NamedExpr, ast.Delete,
                                 ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                 ast.ClassDef, ast.Try, ast.With)):
                raise WhilePlanError("worklist progress has an unproved control effect")
            if effect(part) and id(part) not in allowed_count:
                raise WhilePlanError("worklist changes outside consumption/admission")
            if projection and isinstance(part, (ast.If, ast.For)):
                condition = part.test if isinstance(part, ast.If) else part.iter
                if projection.delta(condition, owner):
                    raise WhilePlanError("worklist control expression changes the count")
            targets = (part.targets if isinstance(part, ast.Assign) else
                       (part.target,) if isinstance(part, (ast.AnnAssign, ast.AugAssign, ast.For)) else ())
            for target in targets:
                names = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)
                         and isinstance(n.ctx, ast.Store)}
                if local in names and id(part) not in allowed_count:
                    raise WhilePlanError("worklist count changes outside consumption/admission")
                if isinstance(floor, ast.Name) and floor.id in names:
                    raise WhilePlanError("worklist exit bound changes in the loop")
                if seen & names and id(part) not in allowed_seen:
                    raise WhilePlanError("visited bits are cleared or rebound in the loop")
    return WorklistProgress(count, floor, tuple(domains), isinstance(test, ast.Name))
