"""Join declared operation rules to the existing typed runtime source closure.

The collector also retains contextual types of eager operand subexpressions.
They need value captures (not additional goals) when a parent is instrumented:
an argument cast around raw arithmetic would change its context-determined
width. Native edits replace operator tokens only, leaving all existing source
events and dependency probes inside their original evaluation positions.
"""

from .source_edits import Replacement
from .source_effects import conflicts
from .source_elaboration import elaboration_environments
from .source_operation_events import _BINARY, _UNARY, OperationCapture, _hdl_type
from .source_operation_types import CaptureTypes

OPERATORS = {
    "Add": "add", "Subtract": "subtract", "Multiply": "multiply",
    "Divide": "divide", "Mod": "remainder", "Power": "power",
    "LogicalShiftLeft": "shift-left", "ArithmeticShiftLeft": "shift-left",
    "LogicalShiftRight": "shift-right", "ArithmeticShiftRight": "shift-right",
    "BinaryAnd": "bit-and", "BinaryOr": "bit-or", "BinaryXor": "bit-xor",
    "Plus": "positive", "Minus": "negative", "BitwiseNot": "bit-not",
    "LogicalNot": "logical-not",
}


class OperationCollector:
    """Private compiler joins; immutable declarations remain the public owner."""

    def __init__(self, rules, symbols, span, children, *, effects, type_witness, capture=True):
        self.rules = {}
        for rule in rules:
            self.rules.setdefault(rule.operator, []).append(rule)
        self.symbols, self.span, self.children = symbols, span, children
        self.capture = capture
        self.captures = {}
        self.effects = effects
        self.type_witness = type_witness
        self.operand_nodes = {}
        self.issues = set()
        self._captured = set()

    def matching(self, node):
        if node.get("kind") not in {"BinaryOp", "UnaryOp"}:
            return ()
        rules = tuple(self.rules.get(OPERATORS.get(node.get("op")), ()))
        if rules and self.capture:
            self.capture_tree(node)
        return rules

    def capture_tree(self, node):
        if id(node) in self._captured:
            return
        self._captured.add(id(node))
        if isinstance(node, dict):
            kind, op = node.get("kind"), node.get("op")
            if kind == "BinaryOp" and op in _BINARY or kind == "UnaryOp" and op in _UNARY:
                operands = ((node["left"], node["right"]) if kind == "BinaryOp" else (node["operand"],))
                summaries = tuple(self.effects(operand) for operand in operands)
                if any(summary is None for summary in summaries):
                    self.issues.add((*self.span(node), "operation operands have unbound source effects"))
                elif len(summaries) == 2 and conflicts(*summaries):
                    self.issues.add((*self.span(node), "operation operands have conflicting source storage effects"))
                capture = OperationCapture.from_node(node, self.symbols)
                self.captures.setdefault(self.span(node), {}).setdefault(capture, set()).add(id(node))
                self.operand_nodes[self.span(node)] = operands
        for _, child in self.children(node):
            self.capture_tree(child)

    def bindings(self, document, children):
        # The same helper text can also execute during construction in an
        # instance with no runtime call. Its computation is still rewritten,
        # so retain those type variants without declaring construction goals.
        if self.captures:
            filename = next(iter(self.operand_nodes.values()))[0]["source_file_start"]

            def visit(node):
                if isinstance(node, dict) and node.get("isUninstantiated"):
                    return
                if (isinstance(node, dict) and node.get("kind") in {"BinaryOp", "UnaryOp"}
                        and node.get("source_file_start") == filename
                        and node.get("source_file_end") == filename
                        and self.span(node) in self.captures):
                    self.capture_tree(node)
                for _, child in children(node):
                    visit(child)

            visit(document["design"])
        selected = {key for variants in self.captures.values() if len(variants) > 1
                    for keys in variants.values() for key in keys}
        environments = elaboration_environments(document, children, self.symbols, selected) if selected else {}
        bindings = []
        for (first, last), variants in sorted(self.captures.items()):
            groups = []
            for capture, keys in sorted(variants.items(), key=lambda item: repr(item[0])):
                contexts = tuple(sorted({environments[key] for key in keys}, key=repr)) if len(variants) > 1 else ()
                groups.append((capture, contexts))
            varying_kind = any(len({(value.signed, value.four_state) for value in column}) > 1 for column in
                zip(*((*capture.operands, capture.result) for capture in variants)))
            witnesses = (tuple(self.type_witness(value) for value in self.operand_nodes[(first, last)])
                         if varying_kind else ())
            bindings.append((first, last, CaptureTypes(tuple(groups), witnesses)))
        return tuple(bindings)


def operation_tokens(node, spans, capture, name, *, widths=(), value_types=(), operand_witnesses=()):
    """Token replacement and argument wrappers, composed by SourcePlan's walk.

    Wrappers are installed before/after visiting an operand, not as unordered
    insertions afterwards. Thus a term's original choice/read probe remains
    inside its value argument, even when nested spans share a boundary.
    """
    operands = ((node["left"], node["right"]) if len(capture.operands) == 2
                else (node["operand"],))
    token = node["operatorToken"]
    # ~^ and ^~ are spelling aliases; no other typed/source disagreement is.
    if token["text"] != capture.operator and {token["text"], capture.operator} != {"~^", "^~"}:
        raise ValueError("operation capture does not match its exact source token")
    replacement = Replacement(*spans[id(token)], "," if len(operands) == 2 else " ")
    sizes = widths[:-1] if widths else tuple(str(kind.width) for kind in capture.operands)
    wrappers = []
    for i, (operand, kind, width) in enumerate(zip(operands, capture.operands, sizes)):
        cast = "signed" if kind.signed else "unsigned"
        if value_types and value_types[i]:
            # Set signedness at the operand's self width BEFORE resizing.
            # A direct target-type cast sign-extends a narrow signed source
            # even when the original operation's context is unsigned.
            cast = f"type($bits({operand_witnesses[i]})'({value_types[i]}'(0)))"
        wrappers.append((operand, f"{width}'({cast}'(", "))"))
    return f"{name}(", replacement, wrappers


def operation_functions(capture, name, rules, callback, *, widths=(), variants=(), value_types=()):
    """One typed computation and observer, parameterized by invocation IDs.

    A helper's source body can execute at several call sites, or during initial
    construction. IDs and the enabled bit come from the existing HelperContexts
    binding, not a second call-stack mechanism or a module-scope guess.
    """
    if not rules:
        return capture.function(name, None, widths=widths, value_types=value_types)
    parameters = (("observed", "bit"), *((f"event_{i}", "int unsigned") for i in range(len(rules))))
    kinds = tuple(value.kind for value in capture.goal_values())
    selected = tuple((f"event_{i}", rule.goal) for i, rule in enumerate(rules) if rule.kinds == kinds)
    observer = name + "_observe"
    unknown = name + "_unknown"
    definitions = []
    if variants:
        arguments = ", ".join((*(f"v{i}" for i in range(len(capture.operands))),
                               "result", "scope", *(parameter for parameter, _ in parameters)))
        calls = []
        for index, (guard, variant) in enumerate(variants):
            target = observer + f"_{index}"
            definitions.append(variant.predicate_callback(
                target, selected, hit=callback, unreadable=unknown,
                observer_parameters=parameters, observed="observed"))
            calls.append(f"{'if' if index == 0 else 'else if'} ({guard}) {target}({arguments});")
        fields = [f"input {(value_types[i] if value_types else '') or _hdl_type(value, width)} v{i}"
                  for i, (value, width) in enumerate(zip(capture.operands, widths))]
        fields.extend((f"input {(value_types[-1] if value_types else '') or _hdl_type(capture.result, widths[-1])} result",
                       "input int unsigned scope"))
        fields.extend(f"input {kind} {parameter}" for parameter, kind in parameters)
        definitions.append(f"function automatic void {observer}({', '.join(fields)});\n"
                           + "\n".join(calls) + "\nendfunction\n")
    else:
        definitions.append(capture.predicate_callback(
            observer, selected, hit=callback, unreadable=unknown,
            observer_parameters=parameters, observed="observed"))
    return (f"function automatic void {unknown}(input int unsigned event_id, scope);\n"
            f"  {callback}(32'hffffffff, scope);\nendfunction\n"
            + "".join(definitions)
            + capture.function(name, observer, observer_parameters=parameters, widths=widths,
                               value_types=value_types))
