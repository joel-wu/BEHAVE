"""Compact a proved constant initialization loop, not its runtime behaviour.

The native simulator still sees the original source. In the common compiler
input a local integer induction variable, constant finite bounds, unit stride
and one blocking constant array write may become a readmemh at the same point.
Unrecognized loops are left alone. Slang supplies symbol identities, conversions
and each elaborated layout; differing instance meanings forbid a shared rewrite.
"""

import hashlib
import re
from collections import Counter

from behave.core import runtime_defaults as defaults

from .constant_arrays import _integral, _nodes, _range, concrete_spans
from .source_replacements import Replacement, SourceReplacements
from .source_syntax import children

VERSION = "rtl-bulk-initialization/1"


def initial_loops(root):
    """Only loops reached unconditionally through sequential initial blocks."""
    def visit(node, initial=False):
        if type(node) is dict:
            kind = node.get("kind")
            initial |= kind == "InitialBlock"
            if kind in {"FunctionDeclaration", "TaskDeclaration"}:
                return
            if initial and kind == "ForLoopStatement":
                yield node
                return
            if initial and kind not in {"InitialBlock", "SequentialBlockStatement"}:
                # readmemh is an initialization image in the compiler; do not
                # erase a conditional, repeated or delayed execution context.
                return
        for child in children(node):
            yield from visit(child, initial)
    return tuple(visit(root))


def _integer(expr):
    """Read only an explicit, fully known compiler constant."""
    value = expr.get("constant", "")
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    match = re.fullmatch(r"(\d+)'([sS]?)([dDhHbB])([0-9a-fA-F]+)", value)
    if match:
        width, signed, radix, digits = match.groups()
        width = int(width)
        value = int(digits, {"d": 10, "h": 16, "b": 2}[radix.lower()])
        if signed and value & (1 << (width - 1)):
            value -= 1 << width
        return value
    raise ValueError("not a known integer constant")


def _symbol(expr):
    while expr.get("kind") == "Conversion":
        expr = expr["operand"]
    return expr.get("symbol") if expr.get("kind") == "NamedValue" else None


def normalize_initial_loops(source, cst, ast):
    # This optional optimization must not supply a file another source read
    # could discover. Leave file-reading designs to their existing frontend.
    if any(word in source for word in ("$readmem", "$fopen", "__behave_init_")):
        return SourceReplacements(source, ()), {}
    root, spans = concrete_spans(source, cst)
    candidates = {spans[id(node)][0]: node for node in initial_loops(root)}
    if not candidates:
        return SourceReplacements(source, ()), {}
    lines = (0, *(i + 1 for i, byte in enumerate(source.encode()) if byte == 10))
    def point(node):
        return lines[node["source_line_start"] - 1] + node["source_column_start"] - 1

    nodes = tuple(_nodes(ast))
    aliases = {str(n["addr"]): n for n in nodes if n.get("kind") == "TypeAlias"}
    parameters = {str(n["addr"]) + " " + n["name"]: n for n in nodes if n.get("kind") == "Parameter"}
    variables = {str(n["addr"]) + " " + n["name"]: n for n in nodes if n.get("kind") == "Variable"}
    initial_writes = Counter()
    for block in nodes:
        if block.get("kind") != "ProceduralBlock" or block.get("procedureKind") != "Initial":
            continue
        for item in _nodes(block["body"]):
            if item.get("kind") == "Call":
                # An initial helper can write nonlocal arrays. Without an
                # effect proof, keep ordinary compilation for this design.
                return SourceReplacements(source, ()), {}
            if item.get("kind") in {"ProceduralAssign", "ProceduralDeassign"}:
                return SourceReplacements(source, ()), {}
            if item.get("kind") == "Assignment":
                target = item["left"]
            elif item.get("kind") == "UnaryOp" and item.get("op", "").lower().endswith(
                    ("increment", "decrement")):
                target = item["operand"]
            else:
                continue
            while target.get("kind") in {"ElementSelect", "RangeSelect"}:
                target = target["value"]
            symbol = _symbol(target)
            if symbol is None:
                return SourceReplacements(source, ()), {}
            initial_writes[symbol] += 1
    meanings = {start: [] for start in candidates}
    pure = {"IntegerLiteral", "UnbasedUnsizedIntegerLiteral", "NamedValue", "Conversion",
            "UnaryOp", "BinaryOp", "ConditionalOp", "Concatenation", "Replication"}

    def constant(expr):
        for item in _nodes(expr):
            if "type" in item and (item.get("kind") not in pure
                    or item.get("kind") == "NamedValue" and item.get("symbol") not in parameters):
                raise ValueError("not a pure compile-time expression")
        if expr.get("kind") == "NamedValue":
            # Slang publishes a parameter's value on its declaration, not on
            # every reference. Use the bound symbol, never the source spelling.
            return _integer({"constant": parameters[expr["symbol"]]["value"]})
        return _integer(expr)

    def require(condition):
        if not condition:
            raise ValueError("initial loop is outside the proven bulk subset")

    for loop in nodes:
        if loop.get("kind") != "ForLoop" or loop.get("source_file_start") != "design.sv":
            continue
        start = point(loop)
        syntax = candidates.get(start)
        if syntax is None:
            continue
        try:
            # A for-local integer cannot expose its final value outside the loop.
            init, = syntax["initializers"]
            require(init["kind"] == "ForVariableDeclaration")
            require(init["type"]["kind"] in {"IntType", "IntegerType"})
            require(not init["declarator"].get("dimensions"))
            stop = loop["stopExpr"]
            require(stop["kind"] == "BinaryOp" and stop["op"] == "LessThan")
            require(stop["left"]["kind"] == "NamedValue")
            symbol = _symbol(stop["left"])
            var = variables[symbol]
            require(var["name"] == init["declarator"]["name"]["text"])
            require(var.get("lifetime") == "Automatic")
            require(var.get("source_file") == "design.sv")
            require(lines[var["source_line"] - 1] + var["source_column"] - 1
                    == spans[id(init["declarator"]["name"])][0])
            require(var["type"]["kind"] == "PredefinedIntegerType")
            require(var["type"]["name"] in {"int", "integer"})
            first = constant(var["initializer"])
            end = constant(stop["right"])
            require(0 <= first < end < 2**31)
            require(not loop["initializers"])
            step, = loop["steps"]
            require(step["kind"] == "Assignment" and not step["isNonBlocking"])
            require(step["left"]["kind"] == "NamedValue")
            require(_symbol(step["left"]) == symbol)
            increment = step["right"]
            require(increment["kind"] == "BinaryOp" and increment["op"] == "Add")
            require(increment["left"]["kind"] == "NamedValue")
            require(_symbol(increment["left"]) == symbol and constant(increment["right"]) == 1)
            body = loop["body"]
            require(body["kind"] == "ExpressionStatement")
            assignment = body["expr"]
            require(assignment["kind"] == "Assignment" and not assignment["isNonBlocking"])
            left = assignment["left"]
            require(left["kind"] == "ElementSelect" and _symbol(left["selector"]) == symbol)
            require(left["selector"]["kind"] == "NamedValue")
            array = left["value"]
            require(array["kind"] == "NamedValue")
            # Yosys merges readmemh initial images with explicit initial writes
            # using priorities that need not match their procedural order.
            # Optimize only a memory's sole initialization write; competing
            # writes and declaration initializers stay on the ordinary path.
            require(initial_writes[array["symbol"]] == 1)
            require(not variables[array["symbol"]].get("initializer"))
            type_ = array["type"]
            require(type_["kind"] == "FixedSizeUnpackedArrayType")
            lo, hi = sorted(_range(type_))
            require(lo <= first < end <= hi + 1)
            layout = _integral(type_["elementType"], aliases)
            require((end - first) * layout.width <= 16 << 20)
            require((end - first) * ((layout.width + 3) // 4 + 1) <= defaults.SIM_RTL_BULK_INITIALIZATION_BYTES)
            value = constant(assignment["right"]) % (1 << layout.width)
            name = array["symbol"].split(" ", 1)[1]
            require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name))
            # No attributes or delayed/nonblocking assignment can be discarded.
            require(syntax["statement"]["kind"] == "ExpressionStatement")
            require(syntax["statement"]["expr"]["kind"] == "AssignmentExpression")
            require(not any(n.get("kind") in {"AttributeInstance", "TimingControlExpression"}
                           for n in _nodes(syntax)))
            meanings[start].append((name, first, end, layout.width, value))
        except (KeyError, ValueError, TypeError):
            meanings[start].append(None)

    edits, files = [], {}
    for start, rows in sorted(meanings.items()):
        if not rows or rows[0] is None or any(row != rows[0] for row in rows):
            continue
        name, first, end, width, value = rows[0]
        data = (f"{value:0{(width + 3) // 4}x}\n" * (end - first)).encode()
        filename = "__behave_init_" + hashlib.sha256(data).hexdigest() + ".hex"
        files[filename] = data
        lo, hi = spans[id(candidates[start])]
        edits.append(Replacement(lo, hi, f'$readmemh("{filename}", {name}, {first}, {end - 1});'))
    return SourceReplacements(source, tuple(edits)), files
