"""Source-storage effects of shared IR commands, before scheduling composition."""

from ..symbolic import ir
from ..symbolic import ranges
from ..symbolic.slicing import reads


def accesses(command, storage, value=None):
    read, written = set(), set()
    for item in ir.walk(command):
        if isinstance(item, ir.Assign):
            reads(item.expr, read)
            written.add(item.var)
        elif isinstance(item, ir.Store):
            read.add(item.var)
            written.add(item.var)
            reads(item.index, read)
            reads(item.expr, read)
        elif isinstance(item, ir.If):
            reads(item.guard, read)
        elif isinstance(item, ir.For):
            reads(item.trip_count, read)
        elif not isinstance(item, (ir.Skip, ir.Seq, ir.Mark, ir.Fail)):
            raise TypeError(f"source effects are not implemented for {type(item).__name__}")
    if value is not None:
        reads(value, read)
    return read & storage, written & storage


def conflicts(first, second):
    read_a, write_a = first
    read_b, write_b = second
    return (write_a & (read_b | write_b)) | (write_b & read_a)


def conditional_accesses(command, storage, environment):
    """Conservative effects in one held-input context, using shared IR flow.

    Only commands visited by the existing range interpreter contribute. A
    guarded body is not recursively rescanned from its parent: doing that
    would put an excluded branch's reads straight back into the result.
    Failure is an effect too; pruning reads must never hide a live failure.
    """
    read, written, failures = set(), set(), set()

    def visit(node, _environment):
        if isinstance(node, ir.Assign):
            reads(node.expr, read)
            written.add(node.var)
        elif isinstance(node, ir.Store):
            read.add(node.var)
            written.add(node.var)
            reads(node.index, read)
            reads(node.expr, read)
        elif isinstance(node, ir.If):
            reads(node.guard, read)
        elif isinstance(node, ir.For):
            reads(node.trip_count, read)
        elif isinstance(node, ir.Fail):
            failures.add(node.goal)
        elif not isinstance(node, (ir.Skip, ir.Seq, ir.Mark)):
            raise TypeError(f"source effects are not implemented for {type(node).__name__}")

    ranges.walk_post(command, dict(environment), visit)
    return frozenset(read & storage), frozenset(written & storage), bool(failures)


def _scalar_local_loops(command, storage, *, failures, storage_writes=False):
    """Validate scalar loops; event callers exclude physical storage writes."""
    pending = [(command, False)]
    while pending:
        node, local = pending.pop()
        if isinstance(node, ir.Seq):
            pending.extend((part, local) for part in ir._parts(node))
        elif isinstance(node, ir.If):
            pending.extend(((node.then, local), (node.orelse, local)))
        elif isinstance(node, ir.For):
            if node.index in storage and not storage_writes:
                return False
            pending.append((node.body, True))
        elif isinstance(node, ir.Assign):
            if local and node.var in storage and not storage_writes:
                return False
        elif not isinstance(node, (ir.Skip, ir.Mark)) and not (
                failures and isinstance(node, ir.Fail)):
            return False
    return True


def _local_loop_inputs(loop, values):
    """Conservative entry dependencies, without expanding the trip count.

    Read-before-write names cover the first iteration; all loop-written
    scalars share those dependencies thereafter. Branch joins retain only
    definite writes, and zero iterations retain every scalar's entry value.
    This value summary grants no event or notification-count certificate.
    """
    incoming, written, marks = set(), {loop.index}, set()

    def use(expr, defined):
        names = set()
        reads(expr, names)
        incoming.update(names - defined)

    def scan(command, defined):
        for node in ir._parts(command):
            if isinstance(node, ir.Assign):
                use(node.expr, defined)
                defined.add(node.var)
                written.add(node.var)
            elif isinstance(node, ir.If):
                use(node.guard, defined)
                defined = scan(node.then, set(defined)) & scan(node.orelse, set(defined))
            elif isinstance(node, ir.For):
                use(node.trip_count, defined)
                written.add(node.index)
                scan(node.body, defined | {node.index})  # May execute zero times.
            elif isinstance(node, ir.Mark):
                marks.add(node.goal)
        return defined

    use(loop.trip_count, set())
    scan(loop.body, {loop.index})
    dependencies = set().union(*(values.get(name, {name}) for name in incoming))
    for name in written:
        values[name] = values.get(name, {name}) | dependencies
    return written, dependencies, marks


def observation_dependencies(command, storage, *, marks=None, infallible=False):
    """Entry-storage reads controlling marks in a scalar activation.

    Follow saved local values and branch joins, rather than inspecting only
    the spelling of a guard. Unknown locals retain their own names so they
    cannot acquire a stability certificate. This describes event presence,
    not output equivalence; the caller separately checks complete writes.
    A projected query may expose only a subset of marks. Unselected marks
    then add no observation dependency; their control and value effects are
    still scanned and unsupported commands still reject the certificate.
    """
    # Only the caller's range analysis can discharge failure commands. They
    # remain in the executable relation; this flag removes no check or path.
    if not _scalar_local_loops(command, storage, failures=infallible):
        return None
    observed = set()

    def dependencies(expr, values):
        names = set()
        reads(expr, names)
        return set().union(*(values.get(name, {name}) for name in names))

    def scan(command, values, control):
        assigned = set()
        for node in ir._parts(command):
            if isinstance(node, ir.Mark):
                if marks is None or node.goal in marks:
                    observed.update(control)
            elif isinstance(node, ir.Assign):
                values[node.var] = dependencies(node.expr, values)
                assigned.add(node.var)
            elif isinstance(node, ir.For):
                names, inputs, hits = _local_loop_inputs(node, values)
                assigned.update(names)
                if hits and (marks is None or hits.intersection(marks)):
                    observed.update(control | inputs)
            elif isinstance(node, ir.If):
                guard = dependencies(node.guard, values)
                yes, no = dict(values), dict(values)
                branches = scan(node.then, yes, control | guard)
                branches |= scan(node.orelse, no, control | guard)
                for name in branches:
                    values[name] = yes.get(name, {name}) | no.get(name, {name}) | guard
                assigned.update(branches)
        return assigned

    scan(command, {}, set())
    return observed if observed <= storage else None


def input_constant_writes(command, storage, inputs):
    """Storage written once per path with a value depending only on held pins.

    Re-executing such a process within one physical drive cannot keep changing
    that value. Track both control/data dependence and path write counts: equal
    final values alone do not exclude intermediate transitions. Indexed stores
    and loops retain the ordinary scheduler bound, not an assumed proof.
    """
    return {name for name, dependencies in single_write_inputs(command, storage).items()
            if dependencies <= inputs}


def single_write_inputs(command, storage):
    """Entry names determining each scalar written exactly once on every path.

    A read after an unconditional assignment uses the new value, not the
    entry value of that name. Keep branch-control dependence and saved old
    values. This is a value certificate, not an event or failure certificate.
    """
    values, counts = _write_inputs(command, storage)
    return {name: frozenset(values[name]) for name, count in counts.items() if count == (1, 1)}


def total_write_inputs(command, storage, *, kinds=None):
    """Final values assigned on every path through an activation.

    This is NOT a notification-count certificate: intermediate writes may
    still wake readers. A closed settling DAG must separately preserve its
    selected events and exclude notification cycles. Notification-bound
    callers retain single-write checks.
    """
    # Scalar loop writes may depend on their initialized value, but need not
    # depend on the activation's entry value. Keep the loop dependency union;
    # only writes outside the loop establish totality (zero trips are legal).
    values, counts = _write_inputs(command, storage, storage_loops=True, kinds=kinds)
    return {name: frozenset(values[name]) for name, count in counts.items() if count[0] > 0}


def _array_iteration(loop, kinds):
    """Project an independent, complete array traversal to one scalar cell.

    This is a dependency summary only, never executable replacement IR.
    Each touched array must use the same bijective index on every access.
    Saved index expressions are followed in program order; branches and
    nested loops invalidate aliases they may change. No cell is enumerated.
    """
    from .source_values import ArrayType

    count = loop.bound
    if type(count) is not int or count <= 0:
        return None
    written = {node.var for node in ir.walk(loop.body)
               if isinstance(node, (ir.Assign, ir.Store))}
    arrays = {name for name in written if isinstance(kinds.get(name), ArrayType)}
    if not arrays or loop.index in written or any(
            kinds[name].cells != count or isinstance(kinds[name].element, ArrayType)
            for name in arrays):
        return None
    indices = {}
    bounds = {loop.index: ranges.Range(0, count - 1)}

    def index_value(expr, aliases):
        if isinstance(expr, ir.Var):
            return aliases.get(expr.name, expr)
        if isinstance(expr, ir.Lit):
            return expr
        if not isinstance(expr, ir.Op):
            return None
        args = tuple(index_value(arg, aliases) for arg in expr.args)
        if any(arg is None for arg in args):
            return None
        return ranges.value_preserving_operand(ir.Op(expr.op, *args), bounds)

    def cell(name, index, aliases):
        value = index_value(index, aliases)
        form = None if value is None else ranges.affine_form(value, env=bounds)
        complete = ((0, {}),) if count == 1 else ((0, {loop.index: 1}), (count - 1, {loop.index: -1}))
        if form not in complete:
            raise ValueError('array traversal is not a complete bijection')
        if indices.setdefault(name, form) != form:
            raise ValueError('array access crosses iteration cells')

    def expression(expr, aliases):
        if isinstance(expr, ir.Sel) and isinstance(expr.array, ir.Var) and expr.array.name in arrays:
            cell(expr.array.name, expr.index, aliases)
            return ir.Var(expr.array.name)
        if isinstance(expr, ir.Var):
            if expr.name in arrays:
                raise ValueError('whole-array use in a cell summary')
            return expr
        if isinstance(expr, ir.Op):
            return ir.Op(expr.op, *(expression(arg, aliases) for arg in expr.args))
        if isinstance(expr, ir.Sel):
            return ir.Sel(expression(expr.array, aliases), expression(expr.index, aliases))
        return expr

    def project(command, aliases):
        result = []
        for node in ir._parts(command):
            if isinstance(node, ir.Assign):
                if node.var in arrays:
                    raise ValueError('whole-array assignment in a cell summary')
                result.append(ir.Assign(node.var, expression(node.expr, aliases)))
                aliases[node.var] = index_value(node.expr, aliases)
            elif isinstance(node, ir.Store):
                if node.var not in arrays:
                    raise ValueError('untyped array store')
                cell(node.var, node.index, aliases)
                result.append(ir.Assign(node.var, expression(node.expr, aliases)))
            elif isinstance(node, ir.If):
                guard = expression(node.guard, aliases)
                yes, no = dict(aliases), dict(aliases)
                result.append(ir.If(guard, project(node.then, yes), project(node.orelse, no)))
                for name in yes.keys() | no.keys():
                    left, right = yes.get(name, ir.Var(name)), no.get(name, ir.Var(name))
                    aliases[name] = (left if left is not None and right is not None
                                     and ir.expressions_equal(left, right) else None)
            elif isinstance(node, ir.For):
                trip = expression(node.trip_count, aliases)
                changed = {part.var if isinstance(part, (ir.Assign, ir.Store)) else part.index
                           for part in ir.walk(node) if isinstance(part, (ir.Assign, ir.Store, ir.For))}
                inner = {**aliases, **{name: None for name in changed}}
                result.append(ir.For(node.index, trip, project(node.body, inner)))
                aliases.update((name, None) for name in changed)
            elif isinstance(node, (ir.Skip, ir.Mark, ir.Fail)):
                result.append(node)
            else:
                raise ValueError('unsupported array dependency command')
        return ir._sequence(result)

    try:
        body = project(loop.body, {})
    except ValueError:
        return None
    return body, arrays


def _array_loop_inputs(loop, values, storage, kinds):
    """Final-cell dependencies plus conservative scalar loop-carried closure."""
    projected = _array_iteration(loop, kinds)
    if projected is None:
        return None
    body, arrays = projected
    transfer, counts = _write_inputs(body, storage | arrays, storage_loops=True)
    if not arrays <= counts.keys() or any(counts[name][0] == 0 for name in arrays):
        return None  # A conditional initialization may leave an old cell intact.
    transfer[loop.index] = set()
    # Scalar temporaries can carry information between different cells. Keep
    # both their entry dependency and every body successor until closed. Array
    # entry names instead denote this iteration's untouched cell, not the
    # preceding iteration's result. This closure depends on syntax, not N.
    carried = {name: set(values.get(name, {name})) for name in transfer if name not in arrays}

    def resolve(inputs):
        return set().union(*(carried.get(name, values.get(name, {name})) for name in inputs))

    carried[loop.index] = set()
    pending = set(carried)
    readers = {name: set() for name in carried}
    for target in carried:
        for name in transfer[target] & carried.keys():
            readers[name].add(target)
    while pending:
        name = pending.pop()
        extra = resolve(transfer[name]) - carried[name]
        if extra:
            carried[name].update(extra)
            pending.update(readers[name])
    final = {name: resolve(transfer[name]) for name in arrays}
    values.update(carried)
    values.update(final)
    return arrays


def _write_inputs(command, storage, *, storage_loops=False, kinds=None):
    if kinds is None and not _scalar_local_loops(
            command, storage, failures=True, storage_writes=storage_loops):
        return {}, {}

    def dependencies(expr, values):
        names = set()
        reads(expr, names)
        return set().union(*(values.get(name, {name}) for name in names))

    def scan(command, values):
        counts, assigned = {}, set()
        for node in ir._parts(command):
            if isinstance(node, ir.Assign):
                values[node.var] = dependencies(node.expr, values)
                assigned.add(node.var)
                if node.var in storage:
                    low, high = counts.get(node.var, (0, 0))
                    counts[node.var] = low + 1, high + 1
            elif isinstance(node, ir.Store) and kinds is not None:
                # A partial store preserves untouched cells. It cannot create
                # a total write, but must not discard one already established
                # by a full assignment/traversal. Retain both address and data
                # dependencies; failure and notification checks are separate.
                values[node.var] = (values.get(node.var, {node.var})
                    | dependencies(node.index, values) | dependencies(node.expr, values))
                assigned.add(node.var)
                if node.var in storage:
                    low, high = counts.get(node.var, (0, 0))
                    counts[node.var] = low, high + 1
            elif isinstance(node, ir.For):
                if kinds is not None and any(isinstance(part, ir.Store) for part in ir.walk(node.body)):
                    arrays = _array_loop_inputs(node, values, storage, kinds)
                    if arrays is None:
                        raise ValueError('array loop has no complete value summary')
                    assigned.update(values)
                    for name in arrays:
                        low, high = counts.get(name, (0, 0))
                        counts[name] = low + 1, high + 2  # Total, never single-write.
                else:
                    names, _, _ = _local_loop_inputs(node, values)
                    assigned.update(names)
            elif isinstance(node, ir.If):
                control = dependencies(node.guard, values)
                yes, no = dict(values), dict(values)
                yes_counts, yes_assigned = scan(node.then, yes)
                no_counts, no_assigned = scan(node.orelse, no)
                for name in yes_assigned | no_assigned:
                    values[name] = yes.get(name, {name}) | no.get(name, {name}) | control
                assigned.update(yes_assigned | no_assigned)
                for name in yes_counts.keys() | no_counts.keys():
                    low, high = counts.get(name, (0, 0))
                    left, right = yes_counts.get(name, (0, 0)), no_counts.get(name, (0, 0))
                    counts[name] = low + min(left[0], right[0]), high + max(left[1], right[1])
            elif not isinstance(node, (ir.Skip, ir.Mark, ir.Fail)):
                raise ValueError('unsupported value dependency command')
        return counts, assigned

    values = {}
    try:
        counts, _ = scan(command, values)
    except ValueError:
        return {}, {}
    return values, counts


def _read_operands(node, selected, constant):
    """Conservative operand bit projections shared by both read analyses."""
    if isinstance(node, ir.Sel):
        return ((node.array, None), (node.index, None))
    if isinstance(node, ir.Lit):
        return ()
    if not isinstance(node, ir.Op):
        raise TypeError(f'unknown source expression {type(node).__name__}')
    op, args = node.op, node.args
    if op in {'uwrap', 'swrap'} and (width := constant(args[1])) is not None:
        full = (1 << width) - 1
        mask = full if selected is None else selected & full
        if op == 'swrap' and selected is not None and selected & ~full:
            mask |= 1 << (width - 1)
        return ((args[0], mask),)
    if op in {'shr', 'shl', 'ushl', 'ulshr'} and (
            (amount := constant(args[1])) is not None and amount >= 0):
        if op in {'ushl', 'ulshr'}:
            width = constant(args[2])
            if width is None:
                return tuple((arg, None) for arg in args)
            full = (1 << width) - 1
            selected = full if selected is None else selected & full
        mask = (None if selected is None else selected << amount
                if op in {'shr', 'ulshr'} else selected >> amount)
        if op == 'ulshr':
            mask &= full
        return ((args[0], mask),)
    if op in {'and', 'or', 'xor', 'inv'}:
        result = []
        for index, arg in enumerate(args):
            mask = selected
            if op in {'and', 'or'}:
                other = constant(args[1 - index])
                if other is not None:
                    other = other if op == 'and' else ~other
                    mask = other if mask is None else mask & other
            result.append((arg, mask))
        return tuple(result)
    return tuple((arg, None) for arg in args)


def read_masks(command, kinds):
    """Overapproximate physical reads in all expressions, including guards.

    Every local definition and branch is visited, so reading a saved local
    cannot hide its original storage reads. No path or iteration is expanded;
    a dynamic slice conservatively keeps the complete source word. This mask
    can narrow a separately established entry-name dependency, not replace it.
    """
    from .source_values import ArrayType

    env = {name: kind.domain for name, kind in kinds.items()}

    def constant(expr):
        bound = ranges.bounds_of_expression(expr, env)
        return bound.lo if bound is not None and bound.lo == bound.hi else None

    pending, seen, result = [], set(), {}
    for node in ir.walk(command):
        if isinstance(node, (ir.Assign, ir.Store)):
            pending.append((node.expr, None))
        if isinstance(node, ir.Store):
            pending.extend(((ir.Var(node.var), None), (node.index, None)))
        elif isinstance(node, ir.If):
            pending.append((node.guard, None))
        elif isinstance(node, ir.For):
            pending.append((node.trip_count, None))
    while pending:
        node, selected = pending.pop()
        key = id(node), selected
        if selected == 0 or key in seen:
            continue
        seen.add(key)
        if isinstance(node, ir.Var):
            kind = kinds.get(node.name)
            if kind is not None:
                full = (1 << kind.width) - 1
                mask = full if selected is None or isinstance(kind, ArrayType) else selected & full
                if (not isinstance(kind, ArrayType) and kind.signed
                        and selected is not None and selected & ~full):
                    mask |= 1 << (kind.width - 1)
                result[node.name] = result.get(node.name, 0) | mask
        else:
            pending.extend(_read_operands(node, selected, constant))
    return result


def single_write_dependencies(command, kinds, write_masks=None):
    """Entry-storage dependence of unconditional scalar/constant-cell writes.

    A value written once is a fixed function of its entry reads. Repeating the
    activation without changing those reads cannot keep producing transitions.
    Retain compiler array positions and bit masks, not whole-array sensitivity:
    that sensitivity still owns scheduling, but is too coarse for this proof.
    Compiler write footprints exclude the preserved part of a packed store.
    Bitwise projections can narrow reads; other operations retain all reads.

    Conditional/multiple writes, loops, and reads after a storage write need
    stronger proofs. Return no certificate for those forms; no IR is rewritten.
    """
    from .source_values import ArrayType

    parts = ir._parts(command)
    for node in parts:
        if isinstance(node, ir.If):
            # Observations and terminal domain checks cannot change a written
            # value. Keep them in executable IR; they can only shorten the
            # history bounded here. A conditional value write is not covered.
            if any(not isinstance(child, (ir.Skip, ir.Seq, ir.If, ir.Mark, ir.Fail))
                   for child in ir.walk(node)):
                return {}
        elif not isinstance(node, (ir.Skip, ir.Assign, ir.Store, ir.Mark, ir.Fail)):
            return {}
    values, views, written, result = {}, {}, {}, {}
    env = {name: kind.domain for name, kind in kinds.items()}
    definitions, versions = {}, {}

    def projection(expr):
        if isinstance(expr, ir.Var):
            if expr.name in kinds:
                return expr.name, kinds[expr.name], 0
            return views.get(expr.name)
        if isinstance(expr, ir.Sel):
            base = projection(expr.array)
            at = ranges.bounds_of_expression(expr.index, env)
            if (base is not None and isinstance(base[1], ArrayType)
                    and at is not None and at.lo is not None and at.lo == at.hi
                    and 0 <= at.lo < base[1].cells):
                name, kind, offset = base
                return name, kind.element, offset + at.lo * kind.element.width
        return None

    def constant(expr):
        bound = ranges.bounds_of_expression(expr, env)
        return bound.lo if bound is not None and bound.lo == bound.hi else None

    def dependencies(expr, selected=None):
        deps, seen, stack = set(), set(), [(expr, selected)]
        while stack:
            node, selected = stack.pop()
            key = id(node), selected
            if selected == 0 or key in seen:
                continue
            seen.add(key)
            view = projection(node)
            if view is not None:
                name, kind, offset = view
                full = (1 << kind.width) - 1
                mask = full if selected is None else selected & full
                if (not isinstance(kind, ArrayType) and kind.signed
                        and selected is not None and selected & ~full):
                    mask |= 1 << (kind.width - 1)  # Integer sign extension.
                mask <<= offset
                if mask & written.get(name, 0):
                    return None
                if mask:
                    deps.add((name, mask))
            elif isinstance(node, ir.Var):
                if node.name not in values:
                    return None
                value_range = env.get(node.name)
                full = None
                if (value_range is not None and value_range.lo is not None
                        and value_range.hi is not None):
                    width = max(0, value_range.hi).bit_length()
                    if value_range.lo < 0:
                        width = max(width, (-value_range.lo - 1).bit_length()) + 1
                    full = (1 << width) - 1
                if selected is None or full is not None and selected & full == full:
                    # The whole saved value already has its entry-read set.
                    # Re-expanding every full-width temporary would make a
                    # straight-line chain quadratic in source length.
                    deps.update(values[node.name])
                    continue
                expression, captured = definitions[node.name]
                if all(versions.get(name, 0) == version for name, version in captured.items()):
                    # Project through an unchanged local definition. A copy of
                    # a packed merge must not reintroduce its preserved bits.
                    stack.append((expression, selected))
                else:
                    # Reassigned operands no longer denote this saved value.
                    # Keep its already captured entry reads, never substitute
                    # the newer expression or infer a narrower certificate.
                    deps.update(values[node.name])
            else:
                stack.extend(_read_operands(node, selected, constant))
        return deps

    for node in parts:
        if not isinstance(node, (ir.Assign, ir.Store)):
            continue
        if node.var not in kinds:
            if isinstance(node, ir.Store):
                return {}
            deps = dependencies(node.expr)
            if deps is None:
                return {}
            view = projection(node.expr)
            value = ranges.bounds_of_expression(node.expr, env)
            names = set()
            reads(node.expr, names)
            definitions[node.var] = node.expr, {name: versions.get(name, 0) for name in names}
            values[node.var], views[node.var] = deps, view
            versions[node.var] = versions.get(node.var, 0) + 1
            env.pop(node.var, None)
            if value is not None:
                env[node.var] = value
            continue
        target = (ir.Sel(ir.Var(node.var), node.index)
                  if isinstance(node, ir.Store) else ir.Var(node.var))
        view = projection(target)
        if view is None or isinstance(view[1], ArrayType):
            return {}
        name, kind, offset = view
        mask = ((1 << kind.width) - 1) << offset
        if write_masks is not None:
            mask &= write_masks[name]
        deps = dependencies(node.expr, mask >> offset)
        if deps is None:
            return {}
        if written.get(name, 0) & mask:
            return {}
        written[name] = written.get(name, 0) | mask
        versions[name] = versions.get(name, 0) + 1
        result[name, mask] = deps
    return result
