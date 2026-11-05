"""Frame-local call effects from resolved HDL symbols.

A literal loop may call these helpers without invalidating its counter's
enumeration. This is a sufficient effect proof, not a claim that arbitrary
functions are pure. Unknown calls, reference arguments, nonlocal writes and
external timing/control effects have no certificate. Reads may be nonlocal.
The optional local-read proof additionally closes the transitive value inputs.
The copy-output variant certifies a retained native frame, not a pure call:
output/inout copy-back remains an effect in its caller. Neither variant widens
the loop-counter or deferred-write admission rules.
"""


# Value/type functions have no storage, randomness, I/O or timing effect.
# Argument expressions still need their own read/write check. This certificate
# is for native evaluation ordering, not symbolic support for real arithmetic.
_VALUE_SYSTEM_FUNCTIONS = frozenset({
    "$signed", "$unsigned", "$bits", "$clog2",
    "$itor", "$rtoi", "$realtobits", "$bitstoreal", "$shortrealtobits", "$bitstoshortreal",
    "$ln", "$log10", "$exp", "$sqrt", "$pow", "$floor", "$ceil",
    "$sin", "$cos", "$tan", "$asin", "$acos", "$atan", "$atan2", "$hypot",
    "$sinh", "$cosh", "$tanh", "$asinh", "$acosh", "$atanh",
})


class FrameLocalCalls:
    def __init__(self, subroutines, nodes, *, local_reads=False, constants=(), copy_outputs=False,
                 retained_frame=False):
        self.subroutines = subroutines
        self.nodes = nodes
        self.local_reads = local_reads
        self.constants = frozenset(constants)
        self.copy_outputs = copy_outputs
        self.retained_frame = retained_frame
        self.proved = {}
        self.active = set()
        self._reads = {}

    def nonlocal_reads(self, target, *, copy_outputs=False):
        """Transitive storage reads of a certified frame-local call.

        Actual arguments are traversed by the caller. Include declaration
        initializers and nested callees; excluding either could incorrectly
        allow an NBA selector to move across a write to its hidden input.
        """
        if not self._prove(target, copy_outputs=copy_outputs):
            raise ValueError("NBA selector capture needs resolved independent expression effects")
        identity = target.split(" ", 1)[0]
        if identity in self._reads:
            return self._reads[identity]
        callee = self.subroutines.get(identity)
        if callee is None:  # Certified value-only system function.
            return frozenset()
        symbols = [node for node in self.nodes(callee.get("members", ()))
                   if node.get("kind") in {"Variable", "FormalArgument"}]
        local = {str(node["addr"]) for node in symbols if node.get("lifetime") == "Automatic"}
        reads = set()
        expressions = [callee["body"], *(node.get("initializer", {}) for node in symbols)]
        for expression in expressions:
            for node in self.nodes(expression):
                if node.get("kind") in {"NamedValue", "HierarchicalValue"}:
                    reads.add(node["symbol"].split(" ", 1)[0])
                elif node.get("kind") == "Call":
                    reads.update(self.nonlocal_reads(node.get("subroutine", ""), copy_outputs=True))
        result = frozenset(reads - local)
        self._reads[identity] = result
        return result

    def __call__(self, target):
        return self._prove(target, copy_outputs=self.copy_outputs)

    def _prove(self, target, *, copy_outputs):
        identity = target.split(" ", 1)[0]
        key = identity, copy_outputs
        if identity in self.active:
            return False
        if key in self.proved:
            return self.proved[key]
        callee = self.subroutines.get(identity)
        if callee is None:
            # These system functions operate only on their value/type operands.
            # Their argument expressions are checked separately, including ++.
            return target in _VALUE_SYSTEM_FUNCTIONS
        directions = {"In", "Out", "InOut"} if copy_outputs else {"In"}
        if callee.get("defaultLifetime") != "Automatic" or any(
                arg.get("direction") not in directions for arg in callee.get("arguments", ())):
            return False
        symbols = [node for node in self.nodes(callee.get("members", ()))
                   if node.get("kind") in {"Variable", "FormalArgument"}]
        local = {str(node["addr"]) for node in symbols
                 if node.get("lifetime") == "Automatic"}
        readable = local | self.constants

        def local_target(node):
            kind = node.get("kind")
            if kind == "NamedValue":
                return node.get("symbol", "").split(" ", 1)[0] in local
            if kind in {"ElementSelect", "RangeSelect"}:
                return local_target(node["value"])
            if kind == "Concatenation":
                return all(local_target(part) for part in node["operands"])
            return False

        def check(node):
            kind = node.get("kind")
            if self.retained_frame and kind == "Case":
                # This certificate also permits the native retained-frame
                # optimization. The backend hoists a case selector temporary
                # out of that frame, which makes the non-inlined helper impure.
                # Keep these bodies inline (transitively); their ordinary call
                # effects and source observation remain supported.
                return False
            if self.local_reads and kind in {"NamedValue", "HierarchicalValue"}:
                return node.get("symbol", "").split(" ", 1)[0] in readable
            if kind == "Assignment":
                if self.retained_frame and node["left"].get("kind") == "Concatenation":
                    # Simultaneous local writes can acquire a module-scoped
                    # __Vconcswap temporary in Verilator. The source is valid;
                    # only retaining this frame (and its callers) is unsafe.
                    return False
                return (not node.get("isNonBlocking") and "timingControl" not in node
                        and local_target(node["left"]))
            if kind == "UnaryOp" and node.get("op") in {
                    "Preincrement", "Predecrement", "Postincrement", "Postdecrement"}:
                return local_target(node["operand"])
            if kind == "Call":
                # Slang represents copy-back actuals as assignments. The same
                # traversal checks their targets below this call, just like
                # ordinary writes; no second argument-direction parser is needed.
                # Copy-back to this caller's automatic locals does not escape
                # its frame. The actual Assignment nodes are checked below;
                # a module target, ref argument or recursive call still fails.
                return self._prove(node.get("subroutine", ""), copy_outputs=True)
            if kind == "Block":
                return node.get("blockKind") == "Sequential"
            # Closed statement/expression vocabulary: an unfamiliar effect must
            # not accidentally be certified by walking only its children.
            return kind is None or kind in {
                "List", "Empty", "VariableDeclaration", "ExpressionStatement",
                "Conditional", "Case", "ForLoop", "Return", "Break", "Continue",
                "IntegerLiteral", "RealLiteral", "StringLiteral", "UnbasedUnsizedIntegerLiteral",
                "NamedValue", "HierarchicalValue", "UnaryOp", "BinaryOp", "Conversion",
                "ConditionalOp", "ElementSelect", "RangeSelect", "Concatenation", "Replication",
                "SimpleAssignmentPattern", "StructuredAssignmentPattern", "ReplicatedAssignmentPattern",
                "EmptyArgument",
            }

        self.active.add(identity)
        try:
            result = "body" in callee and all(check(node) for node in self.nodes(callee["body"]))
            # Declaration initializers are stored on their symbols, not in the
            # VariableDeclaration statement that refers to them.
            result = result and all(check(node) for symbol in symbols
                                    for node in self.nodes(symbol.get("initializer", {})))
        finally:
            self.active.remove(identity)
        self.proved[key] = result
        return result
