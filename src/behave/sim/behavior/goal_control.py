"""Reuse local impossibility along the admitted procedure's control flow.

This module proves implications, not reachability: if a mark never occurs in a
particular local question, a command that must execute after it cannot occur
in that question either. The caller owns the UNSAT evidence and its scope.
"""

from ..symbolic import cfg, ir


class GoalControl:
    """Control graphs of one validated, worker-owned immutable procedure.

    Use the source CFG retained by lowering, before if-conversion obscures its
    edges. Hand-built terms have only their structural graph. Nested loop bodies
    are separate graphs: entering one inherits its enclosing path, but a body
    mark does not dominate code after a possibly empty loop.
    """

    def __init__(self, procedure):
        self.root = procedure._graph or cfg.of_command(procedure.body)
        self.loops = {}
        self.predecessors = {}
        pending = [self.root]
        while pending:
            graph = pending.pop()
            if id(graph) in self.predecessors:
                continue
            order = set(graph.order())
            self.predecessors[id(graph)] = {
                label: tuple(p for p in parents if p in order)
                for label, parents in graph.predecessors().items()
            }
            for label in graph.order():
                for command in graph[label].instrs:
                    if isinstance(command, ir.For):
                        body = (command.flow.graph if command.flow is not None
                                else cfg.of_command(command.body))
                        self.loops[id(command)] = body
                        pending.append(body)

    def blocked_marks(self, impossible):
        """Identities blocked on *every* path, including shared command objects.

        A join is blocked only when every incoming path is blocked. A known
        impossible mark cuts off its suffix; a Fail is never used as a nonterminal
        mark. Missing graph-to-body identities simply grant no shortcut.
        """
        blocked, possible = set(), set()
        pending = [(self.root, False)]
        while pending:
            graph, inherited = pending.pop()
            exits = {}
            for label in graph.order():
                parents = self.predecessors[id(graph)][label]
                dead = inherited or (bool(parents) and all(exits[p] for p in parents))
                for command in graph[label].instrs:
                    if isinstance(command, ir.Mark):
                        dead = dead or command.goal in impossible
                        (blocked if dead else possible).add(id(command))
                    elif isinstance(command, ir.For):
                        pending.append((self.loops[id(command)], dead))
                exits[label] = dead
        return blocked - possible
