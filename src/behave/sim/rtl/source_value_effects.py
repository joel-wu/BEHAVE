"""Compiler-bound storage effects for native expression ordering.

Replacing an eager operator with a typed value call must not choose an order
between interfering operands. These summaries include transitive helper reads
and writes, exclude each automatic call frame, and reject unbound aliases or
external control effects. Sets are whole storage symbols: disjoint array slices
are not guessed. This is a preparation-time certificate, not a hit producer,
execution model, or solver query.
"""

from .source_call_effects import _VALUE_SYSTEM_FUNCTIONS

_EMPTY = frozenset(), frozenset()
_INCREMENTS = {"Preincrement", "Predecrement", "Postincrement", "Postdecrement"}
_VALUES_AND_CONTROL = {
    None, "List", "Empty", "ExpressionStatement", "Conditional", "Case",
    "ForLoop", "WhileLoop", "DoWhileLoop", "RepeatLoop", "ForeachLoop",
    "Return", "Break", "Continue", "IntegerLiteral", "RealLiteral",
    "StringLiteral", "UnbasedUnsizedIntegerLiteral", "UnaryOp", "BinaryOp",
    "Conversion", "ConditionalOp", "ElementSelect", "RangeSelect", "MemberAccess",
    "Concatenation", "Replication", "SimpleAssignmentPattern",
    "StructuredAssignmentPattern", "ReplicatedAssignmentPattern", "EmptyArgument",
}


def _union(summaries):
    reads, writes = set(), set()
    for summary in summaries:
        if summary is None:
            return None
        reads.update(summary[0])
        writes.update(summary[1])
    return frozenset(reads), frozenset(writes)


def _targets(node):
    kind = node.get("kind")
    if kind in {"NamedValue", "HierarchicalValue"}:
        return frozenset((node["symbol"].split(" ", 1)[0],))
    if kind in {"ElementSelect", "RangeSelect", "MemberAccess"}:
        return _targets(node["value"])
    if kind == "Concatenation":
        targets = [_targets(part) for part in node["operands"]]
        return None if any(part is None for part in targets) else frozenset().union(*targets)
    return None


class SourceValueEffects:
    def __init__(self, symbols, subroutines, children, nodes):
        self.symbols, self.subroutines = symbols, subroutines
        self.children, self.nodes = children, nodes
        self.expressions, self.calls, self.active = {}, {}, set()

    def __call__(self, node):
        key = id(node)
        if key not in self.expressions:
            self.expressions[key] = self._expression(node)
        return self.expressions[key]

    def _expression(self, node):
        if not isinstance(node, dict):
            return _union(self(child) for _, child in self.children(node))
        kind = node.get("kind")
        if kind in {"NamedValue", "HierarchicalValue"}:
            return frozenset((node["symbol"].split(" ", 1)[0],)), frozenset()
        if kind == "VariableDeclaration":
            symbol = self.symbols[node["symbol"].split(" ", 1)[0]]
            # Static declaration initialization is not executed on each call.
            if symbol.get("lifetime") != "Automatic":
                return _EMPTY
            return self(symbol["initializer"]) if "initializer" in symbol else _EMPTY
        if kind == "Block":
            if node.get("blockKind") != "Sequential":
                return None
        elif kind == "Call":
            return _union((self._call(node["subroutine"]),
                           _union(self(child) for _, child in self.children(node))))
        elif kind == "Assignment":
            if node.get("isNonBlocking") or node.get("op") or "timingControl" in node:
                return None
        elif kind not in _VALUES_AND_CONTROL:
            return None
        # Qualified case checks can terminate before another operand executes.
        if kind == "Case" and node.get("check") not in {None, "None"}:
            return None
        summary = _union(self(child) for _, child in self.children(node))
        target = (node["left"] if kind == "Assignment" else
                  node["operand"] if kind == "UnaryOp" and node.get("op") in _INCREMENTS else None)
        if target is None or summary is None:
            return summary
        written = _targets(target)
        return None if written is None else (summary[0], summary[1] | written)

    def _call(self, target):
        identity = target.split(" ", 1)[0]
        if identity in self.active:
            return None
        if identity in self.calls:
            return self.calls[identity]
        callee = self.subroutines.get(identity)
        if callee is None:
            return _EMPTY if target in _VALUE_SYSTEM_FUNCTIONS else None
        if "body" not in callee or any(arg.get("direction") != "In" for arg in callee.get("arguments", ())):
            return None
        self.active.add(identity)
        try:
            summary = self(callee["body"])
            if summary is not None:
                private = {str(member["addr"]) for member in self.nodes(callee.get("members", ()))
                           if member.get("kind") in {"Variable", "FormalArgument"}
                           and member.get("lifetime") == "Automatic"}
                summary = summary[0] - private, summary[1] - private
        finally:
            self.active.remove(identity)
        self.calls[identity] = summary
        return summary
