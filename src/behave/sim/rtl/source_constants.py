"""Fold established scalar literals without erasing source events.

Copy folding trusts only already-defined single-writer locals. Guarded source
activations may also supply immutable input values implied by their triggering
edge. Neither path assumes sampled state or a reachability invariant. Even dead
source events retain false-guarded producers in the same catalogue.
"""

from collections import Counter

from ..symbolic import interp
from ..symbolic import ir, primitives
from behave.isolation.preparation_control import check_preparation


def _sequence(commands):
    result = ir.Skip()
    for command in reversed(commands):
        if isinstance(command, ir.Skip):
            continue
        result = command if isinstance(result, ir.Skip) else ir.Seq(command, result)
    return result


def _expression(root, constants):
    rewritten, pending = {}, [(root, False)]
    while pending:
        node, ready = pending.pop()
        if id(node) in rewritten:
            continue
        children = (node.args if isinstance(node, ir.Op) else
                    (node.array, node.index) if isinstance(node, ir.Sel) else ())
        if children and not ready:
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(children))
            continue
        if isinstance(node, ir.Var):
            result = constants.get(node.name, node)
        elif isinstance(node, ir.Op):
            args = tuple(rewritten[id(child)] for child in children)
            result = node if all(a is b for a, b in zip(args, children)) else ir.Op(node.op, *args)
            scalar = primitives.primitive(node.op).signature.result in {
                primitives.BOOLEAN, primitives.INTEGER, primitives.SCALAR,
                primitives.SAME_SCALAR, primitives.SAME}
            if scalar and all(isinstance(child, ir.Lit) for child in args):
                value = interp._Interp({}).value(result)
                if type(value) in (bool, int):
                    result = ir.Lit(value)
        elif isinstance(node, ir.Sel):
            array, index = (rewritten[id(child)] for child in children)
            result = node if array is node.array and index is node.index else ir.Sel(array, index)
        else:
            result = node
        rewritten[id(node)] = result
    return rewritten[id(root)]


def fold_literal_copies(body, locals_):
    """Only already-defined, single-writer local copies can supply constants.

    This runs after source-domain checks/terminal obligations are installed.
    Input and output formals, copied operands and source variables still obey
    their original assignment order. A loop index or ref/copy-out write is a
    write too; no value modified in a helper or later loop turn is propagated.
    """
    commands = list(ir.walk(body))
    writes = Counter(command.index if isinstance(command, ir.For) else command.var
                     for command in commands if isinstance(command, (ir.Assign, ir.Store, ir.For)))
    candidates = {name for name in locals_ if writes[name] == 1}
    constants = {}
    for command in commands:
        check_preparation()
        if isinstance(command, ir.Assign) and command.var in candidates:
            value = _expression(command.expr, constants)
            if isinstance(value, ir.Lit):
                constants[command.var] = value
    if not constants:
        return body

    return substitute_literals(body, constants)


def substitute_literals(body, constants):
    """Fold reads of scalar values established at every use by the caller.

    The caller owns the premise and must keep the activation guard. Preserve
    every dead event/failure producer in the original catalogue.
    """
    commands = list(ir.walk(body))

    def dead(command):
        events = [part for part in ir.walk(command) if isinstance(part, (ir.Mark, ir.Fail))]
        return ir.If(ir.Lit(False), _sequence(events), ir.Skip()) if events else ir.Skip()

    rewritten = {}
    for command in reversed(commands):
        check_preparation()
        if isinstance(command, ir.Seq):
            result = _sequence([rewritten[id(command.first)], rewritten[id(command.second)]])
        elif isinstance(command, ir.If):
            guard = _expression(command.guard, constants)
            yes, no = rewritten[id(command.then)], rewritten[id(command.orelse)]
            result = (_sequence([yes, dead(no)]) if guard.value else _sequence([dead(yes), no])) \
                if isinstance(guard, ir.Lit) and type(guard.value) is bool else ir.If(guard, yes, no)
        elif isinstance(command, ir.For):
            # Source loops have no separate sealed CFG metadata to rewrite.
            result = command if command.flow is not None else ir.For(command.index,
                _expression(command.trip_count, constants), rewritten[id(command.body)])
        elif isinstance(command, ir.Assign):
            result = ir.Assign(command.var, _expression(command.expr, constants))
        elif isinstance(command, ir.Store):
            result = ir.Store(command.var, _expression(command.index, constants),
                              _expression(command.expr, constants))
        else:
            result = command
        rewritten[id(command)] = result
    return rewritten[id(body)]


def coalesce_inactive_occurrences(body, occurrences):
    """Share false producers, not source identities or potentially live events.

    Helper expansion retains a separate occurrence for every call site. When
    literal control blocks several occurrences of the same public goal, their
    predicates are identically false. Keep one real, false-guarded IR producer
    and map every original occurrence to it. A producer seen anywhere outside
    a literal-blocked arm is never shared, nor is a terminal/domain failure.
    This runs on source activations after definedness checks and copy folding.
    """
    counts = Counter(goal for _, _, goal in occurrences)
    repeated = {goal for goal, count in counts.items() if count > 1}
    if not repeated:
        return body, tuple(occurrences)

    blocked, possible, seen = set(), set(), set()
    pending = [(body, False)]
    while pending:
        check_preparation()
        command, inactive = pending.pop()
        if (id(command), inactive) in seen:
            continue
        seen.add((id(command), inactive))
        if isinstance(command, ir.Seq):
            pending.extend(((command.first, inactive), (command.second, inactive)))
        elif isinstance(command, ir.If):
            literal = isinstance(command.guard, ir.Lit) and type(command.guard.value) is bool
            pending.extend(((command.then, inactive or (literal and not command.guard.value)),
                            (command.orelse, inactive or (literal and command.guard.value))))
        elif isinstance(command, ir.For):
            pending.append((command.body, inactive))
        elif isinstance(command, ir.Mark):
            (blocked if inactive else possible).add(command.goal)
        elif isinstance(command, ir.Fail):
            possible.add(command.goal)

    aliases, representatives = {}, {}
    for mark, _key, goal in occurrences:
        if goal in repeated and mark in blocked and mark not in possible:
            representative = representatives.setdefault(goal, mark)
            if mark != representative:
                aliases[mark] = representative
    if not aliases:
        return body, tuple(occurrences)

    rewritten = {}
    for command in reversed(list(ir.walk(body))):
        check_preparation()
        if isinstance(command, ir.Mark) and command.goal in aliases:
            result = ir.Skip()
        elif isinstance(command, ir.Seq):
            result = _sequence([rewritten[id(command.first)], rewritten[id(command.second)]])
        elif isinstance(command, ir.If):
            yes, no = rewritten[id(command.then)], rewritten[id(command.orelse)]
            result = (ir.Skip() if isinstance(yes, ir.Skip) and isinstance(no, ir.Skip)
                      else ir.If(command.guard, yes, no))
        elif isinstance(command, ir.For):
            # Source loops carry no sealed CFG metadata. Keep the loop/index
            # even with an empty body: its final index remains observable.
            result = ir.For(command.index, command.trip_count, rewritten[id(command.body)])
        else:
            result = command
        rewritten[id(command)] = result
    return rewritten[id(body)], tuple((aliases.get(mark, mark), key, goal)
                                     for mark, key, goal in occurrences)
