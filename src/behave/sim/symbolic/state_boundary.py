"""Small exact certificates for normal state successors.

The range transformer proposes a finite next-state carrier.  Usually the
symbolic step independently checks that proposal.  Some large combinational
procedures nevertheless have a very small state transition: every normal exit
writes a flag to the same literal, or copies a value already present at the
step boundary.  Re-encoding the whole datapath to prove that local fact is
unnecessary.

This module proves only those exact source relations.  It follows every
syntactic normal CFG path, joins paths by equality, and declines a field at the
first operation it does not model.  A missed proof therefore costs time; it
cannot widen an admitted state carrier.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ir

__all__ = ["StateSource", "normal_state_sources"]


@dataclass(frozen=True, slots=True)
class StateSource:
    """An exact scalar source at every normal exit of one step."""

    kind: str
    value: object

    def __post_init__(self):
        if self.kind == "entry":
            if type(self.value) is not str or not self.value:
                raise TypeError("an entry state source names one variable")
        elif self.kind == "literal":
            if type(self.value) not in {bool, int}:
                raise TypeError("a literal state source is Boolean or integer")
        else:
            raise ValueError("a state source is entry or literal")


def _join(states):
    def same(left, right):
        if left is None or right is None:
            return left is right
        if left.kind != right.kind:
            return False
        if left.kind == "literal":
            return type(left.value) is type(right.value) \
                and left.value == right.value
        return left.value == right.value

    names = set().union(*(state.keys() for state in states))
    joined = {}
    for name in names:
        values = [state.get(name) for state in states]
        first = values[0]
        joined[name] = first if all(same(value, first) for value in values) \
            else None
    return joined


def _structured_state_writes(proc):
    """Index state writes below retained structured commands in one walk."""

    structured = {
        id(command): command
        for block in proc.graph.blocks.values()
        for command in block.instrs
        if isinstance(command, (ir.Seq, ir.If, ir.For))
    }
    if not structured:
        return {}

    state_names = frozenset(proc.state)
    writes = {identity: set() for identity in structured}
    active = []
    found = set()
    stack = [(proc.body, False)]
    while stack:
        command, leaving = stack.pop()
        identity = id(command)
        if leaving:
            active.pop()
            continue
        if identity in structured:
            active.append(identity)
            found.add(identity)
            stack.append((command, True))
        if (isinstance(command, (ir.Assign, ir.Store))
                and command.var in state_names):
            for owner in active:
                writes[owner].add(command.var)
        if isinstance(command, ir.Seq):
            stack.append((command.second, False))
            stack.append((command.first, False))
        elif isinstance(command, ir.If):
            stack.append((command.orelse, False))
            stack.append((command.then, False))
        elif isinstance(command, ir.For):
            stack.append((command.body, False))

    # A frontend-produced graph shares its source commands with ``proc.body``.
    # Keep a conservative fallback for a hand-built graph that does not.
    for identity in structured.keys() - found:
        writes[identity].update(
            nested.var
            for nested in ir.walk(structured[identity])
            if isinstance(nested, (ir.Assign, ir.Store))
            and nested.var in state_names
        )
    return {identity: frozenset(names) for identity, names in writes.items()}


def _transfer(command, state, structured_writes):
    """Apply the exact fragment, invalidating every unmodelled write."""

    if isinstance(command, ir.Assign):
        if command.var not in state:
            return
        expression = command.expr
        if isinstance(expression, ir.Lit) \
                and type(expression.value) in {bool, int}:
            source = StateSource("literal", expression.value)
        elif isinstance(expression, ir.Var) and expression.name in state:
            source = state.get(expression.name)
        else:
            source = None
        state[command.var] = source
        return
    if isinstance(command, ir.Store):
        if command.var in state:
            state[command.var] = None
        return
    if isinstance(command, (ir.Skip, ir.Mark)):
        return
    if isinstance(command, (ir.Seq, ir.If, ir.For)):
        # The command's complete subtree was indexed once above.  Rewalking it
        # at every enclosing CFG occurrence makes nested retained loops
        # quadratic while adding no semantic information.
        for name in structured_writes.get(id(command), state):
            state[name] = None
        return
    # Decline the certificate for command forms outside the exact fragment.
    for name in state:
        state[name] = None


def normal_state_sources(proc):
    """Return exact scalar sources shared by every syntactic normal exit.

    The sealed source CFG is acyclic.  Branch feasibility is intentionally not
    used: following both arms is an over-approximation, so equal sources across
    all syntactic normal exits are also equal across all feasible ones.
    """

    graph = proc.graph
    if graph is None or not proc.state:
        return {}

    from . import cfg

    initial = {name: StateSource("entry", name) for name in proc.state}
    structured_writes = _structured_state_writes(proc)
    incoming = {graph.entry: [initial]}
    exits = []
    for label in graph.order():
        alternatives = incoming.get(label, ())
        if not alternatives:
            continue
        current = _join(alternatives)
        block = graph.blocks[label]
        for command in block.instrs:
            _transfer(command, current, structured_writes)
        terminator = block.term
        if isinstance(terminator, cfg.Goto):
            incoming.setdefault(terminator.target, []).append(dict(current))
        elif isinstance(terminator, cfg.Branch):
            incoming.setdefault(terminator.then, []).append(dict(current))
            incoming.setdefault(terminator.orelse, []).append(dict(current))
        elif isinstance(terminator, cfg.Return):
            exits.append(current)

    if not exits:
        return {}
    result = _join(exits)
    return {
        name: result[name]
        for name in proc.state
        if result.get(name) is not None
    }
