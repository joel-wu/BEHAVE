"""Concrete propagation in a Search path, before control-flow conversion.

Only the caller's fixed entry values are assumed. Unknown branches keep both
arms and their joins; unknown stores invalidate the array, not just the cell
visited by the old execution. This specializes a local question, not Analysis.
Arithmetic uses the existing IR primitive denotations.
"""

from behave.isolation.preparation_control import check_preparation

from ...symbolic import cfg, ir, primitives
from ...symbolic.values import zero_value
from ...symbolic.uniform_array import UniformArray


_UNKNOWN = object()


class _Array:
    """Copy-on-write concrete knowledge; never mutate a caller's snapshot."""

    def __init__(self, value, *, shared=True):
        self.value, self.shared = value, shared

    def store(self, index, value):
        if not 0 <= index < len(self.value):
            return self
        if type(self.value) is UniformArray:
            return _Array(self.value.store(index, value))
        result = _Array(list(self.value), shared=False) if self.shared else self
        result.value[index] = value
        return result


def _known(value):
    return _Array(value) if type(value) in (list, UniformArray) else value


def _native(value):
    return value.value if isinstance(value, _Array) else value


def _fork(values):
    for value in values.values():
        if isinstance(value, _Array):
            value.shared = True
    return dict(values)


def _meet(left, right):
    # Array identity is sufficient, not necessary, for equal knowledge. Do not
    # scan entire memories at every join or merge different scalar carriers.
    for name, value in tuple(left.items()):
        other = right.get(name, _UNKNOWN)
        if not (value is other or type(value) is type(other)
                and type(value) in (bool, int) and value == other):
            del left[name]


def _expression(root, values):
    """Rewrite scalars and evaluate fully concrete terms, without recursion."""
    done, pending = {}, [(root, False)]
    while pending:
        check_preparation()
        term, ready = pending.pop()
        if term in done:
            continue
        if isinstance(term, ir.Lit):
            done[term] = term, term.value
            continue
        if isinstance(term, ir.Var):
            value = values.get(term.name, _UNKNOWN)
            done[term] = (ir.Lit(value) if type(value) in (bool, int) else term), value
            continue
        if isinstance(term, ir.Op) and term.op == 'ite':
            guard, yes, no = term.args
            if guard not in done:
                pending.extend(((term, True), (guard, False)))
                continue
            truth = done[guard][1]
            if type(truth) is bool:
                chosen = yes if truth else no
                if chosen not in done:
                    pending.extend(((term, True), (chosen, False)))
                else:
                    done[term] = done[chosen]
                continue  # The other arm may contain an unbound local.
        children = term.args if isinstance(term, ir.Op) else (term.array, term.index)
        if not ready or any(child not in done for child in children):
            pending.append((term, True))
            pending.extend((child, False) for child in reversed(children) if child not in done)
            continue
        expressions = tuple(done[child][0] for child in children)
        constants = tuple(done[child][1] for child in children)
        rewritten = (ir.Op(term.op, *expressions) if isinstance(term, ir.Op)
                     else ir.Sel(*expressions))
        value = _UNKNOWN
        if all(item is not _UNKNOWN for item in constants):
            if isinstance(term, ir.Sel):
                array, index = constants
                cells = _native(array)
                value = _known(cells[index] if 0 <= index < len(cells)
                               else zero_value(cells[0]) if len(cells) else 0)
            else:
                value = _known(primitives.primitive(term.op).denotation(
                    *(_native(item) for item in constants)))
        done[term] = (ir.Lit(value) if type(value) in (bool, int) else rewritten), value
    return done[root]


def specialize(graph, entry):
    """Yield work and return the same path relation under fixed entry values.

    Environments are consumed in CFG order, shared at forks and intersected
    at joins. There is no environment history per instruction. Structured
    commands retain their original semantics and invalidate written facts.
    """
    incoming = {graph.entry: {name: _known(value) for name, value in entry.items()}}
    blocks = {}
    for label in graph.order():
        yield
        check_preparation()
        values = incoming.pop(label, None)
        if values is None:
            continue
        original, instructions = graph[label], []
        for command in original.instrs:
            check_preparation()
            if isinstance(command, ir.Assign):
                expression, value = _expression(command.expr, values)
                if isinstance(value, _Array):
                    value.shared = True  # Assignment has array value semantics.
                if value is _UNKNOWN:
                    values.pop(command.var, None)
                else:
                    values[command.var] = value
                command = ir.Assign(command.var, expression)
            elif isinstance(command, ir.Store):
                index, at = _expression(command.index, values)
                expression, value = _expression(command.expr, values)
                array = values.get(command.var, _UNKNOWN)
                if (isinstance(array, _Array) and type(at) is int
                        and value is not _UNKNOWN):
                    if isinstance(value, _Array):
                        value.shared = True
                    values[command.var] = array.store(at, _native(value))
                else:
                    values.pop(command.var, None)
                command = ir.Store(command.var, index, expression)
            else:
                for part in ir.walk(command):
                    if isinstance(part, (ir.Assign, ir.Store)):
                        values.pop(part.var, None)
                    elif isinstance(part, ir.For):
                        values.pop(part.index, None)
            instructions.append(command)
        term = original.term
        if isinstance(term, cfg.Branch):
            guard, truth = _expression(term.guard, values)
            term = (cfg.Goto(term.then if truth else term.orelse) if type(truth) is bool
                    else cfg.Branch(guard, term.then, term.orelse,
                                    atom=term.atom, positive=term.positive))
        blocks[label] = cfg.Block(label, instructions, term)
        successors = tuple(dict.fromkeys(term.successors()))
        for successor in successors:
            carried = _fork(values) if len(successors) > 1 else values
            if successor in incoming:
                _meet(incoming[successor], carried)
            else:
                incoming[successor] = carried
    return cfg.CFG(blocks, graph.entry)
