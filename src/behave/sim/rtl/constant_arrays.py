"""Typed, read-only ROM normalization for the shared native Yosys frontend.

Slang supplies both CST ownership and elaborated types. We do not parse SV with
regular expressions or infer widths from literal spelling. The admitted subset
is explicit: body localparams, one fixed unpacked dimension, integral elements,
assignment-pattern initializers, and pure scalar indexes.
Every array reference must be accounted for, including compile-time uses.

The generated packed constant stores the leftmost declared element at offset 0.
Index arithmetic is signed 64-bit, with inputs and declared dimensions checked
below to exclude overflow. Invalid indexes return the original element type's
default (X or zero); this pass never chooses the simulator's undefined policy.
"""

import re
from bisect import bisect_left
from dataclasses import dataclass

from behave.core import runtime_defaults as defaults

from .source_edits import SourceEdits
from .source_replacements import Replacement, SourceReplacements
from .source_syntax import children, concrete_token, walk

VERSION = "rtl-constant-arrays/2"
_TYPE_QUERIES = frozenset({"$size", "$bits", "$left", "$right", "$low", "$high", "$increment",
                           "$dimensions", "$unpacked_dimensions", "$typename"})


class UnsupportedConstantArray(ValueError):
    """Valid source outside this compiler normalization's checked subset."""


def _index32(expression, width=None):
    """Convert a self-determined SV expression to the declared-index domain."""
    width = f"$bits({expression})" if width is None else width
    return f"$signed(32'({width}'({expression})))"


def concrete_spans(source, document):
    trees = document.get("syntaxTrees", ())
    if len(trees) != 1:
        raise UnsupportedConstantArray("constant-array normalization needs one source tree")
    spans, pieces, position = {}, [], 0

    def locate(node):
        nonlocal position
        start = position
        if type(node) is dict and "text" in node:
            trivia, text = concrete_token(node)
            pieces.append(trivia + text)
            start += len(trivia.encode())
            position += len((trivia + text).encode())
        else:
            sub = children(node)
            for child in sub:
                locate(child)
            if sub:
                start = spans[id(sub[0])][0]
        spans[id(node)] = start, position

    root = trees[0]["root"]
    locate(root)
    if "".join(pieces) != source:
        raise UnsupportedConstantArray("compiler tokens do not reproduce the original bytes")
    return root, spans


def array_declarations(root):
    return tuple(node for node in walk(root) if type(node) is dict
                 and node.get("kind") == "ParameterDeclarationStatement"
                 and any(decl.get("dimensions") for decl in node["parameter"]["declarators"]
                         if type(decl) is dict and decl.get("kind") == "Declarator"))


def _nodes(document):
    if type(document) is dict:
        yield document
        for key, value in document.items():
            if key not in {"type", "elementType", "netType"}:
                yield from _nodes(value)
    elif type(document) is list:
        for value in document:
            yield from _nodes(value)


def _range(type_):
    match = re.fullmatch(r"\[(-?\d+):(-?\d+)\]", type_.get("range", ""))
    if not match:
        raise UnsupportedConstantArray("missing fixed compiler array bounds")
    return tuple(map(int, match.groups()))


@dataclass(frozen=True)
class _Integral:
    width: int
    dimensions: int
    two_state: bool


def _integral(type_, aliases):
    """Only metadata actually preserved by detailed Slang JSON is authoritative.

    Predefined integer signedness is deliberately absent here: the serializer
    omits nondefault signs. Generated SV retains that property through its type.
    """
    if type(type_) is str:
        # Slang's detailed JSON represents typedef uses by symbol identity.
        # Resolve the declaration, never infer a type from its printed name.
        try:
            type_ = aliases[type_.split(" ", 1)[0]]["target"]
        except KeyError as exc:
            raise UnsupportedConstantArray("unresolved integral type alias") from exc
        return _integral(type_, aliases)
    kind = type_.get("kind")
    if kind == "PackedArrayType":
        left, right = _range(type_)
        element = _integral(type_["elementType"], aliases)
        return _Integral((abs(left - right) + 1) * element.width,
                         element.dimensions + 1, element.two_state)
    if kind == "ScalarType" and type_.get("name") in {"logic", "reg", "bit"}:
        return _Integral(1, 0, type_["name"] == "bit")
    if kind == "PredefinedIntegerType":
        widths = {"byte": 8, "shortint": 16, "int": 32, "integer": 32, "longint": 64, "time": 64}
        if type_.get("name") in widths:
            return _Integral(widths[type_["name"]], 1, type_["name"] not in {"integer", "time"})
    raise UnsupportedConstantArray("constant-array use needs an integral compiler type")


def _pure_index(node, queries):
    allowed = {"NamedValue", "IntegerLiteral", "UnbasedUnsizedIntegerLiteral", "Conversion",
               "UnaryOp", "BinaryOp", "ConditionalOp", "Concatenation", "Replication",
               "ElementSelect", "RangeSelect"}
    unary = {"Plus", "Minus", "BitwiseNot", "BitwiseAnd", "BitwiseOr", "BitwiseXor",
             "BitwiseNand", "BitwiseNor", "BitwiseXnor", "LogicalNot"}
    for expr in _nodes(node):
        if expr.get("kind") == "Call" and (id(expr) in queries or expr.get("subroutine") in {
                "$signed", "$unsigned", "$clog2", "$countones", "$countbits", "$onehot", "$onehot0",
                "$isunknown"}):
            continue
        if "type" in expr and (expr.get("kind") not in allowed
                               or expr.get("kind") == "UnaryOp" and expr.get("op") not in unary):
            raise UnsupportedConstantArray("array index has an unaccounted evaluation effect")


@dataclass(frozen=True)
class _Array:
    point: int
    name: str
    width: str
    left: str
    right: str
    element: str
    count: str
    prefix: str
    declarator: dict
    type_syntax: dict

    def distance(self, index):
        return f"((({index})-{self.left})*(({self.left}<={self.right})?64'sd1:-64'sd1))"

    def declaration(self, emit, two_state):
        """Elaboration-only packing; instance parameters are evaluated by SV."""
        w, n, element = self.width, self.count, self.element
        dimension = self.declarator["dimensions"][0]["specifier"]["selector"]
        prefix = (f"typedef {emit(self.type_syntax)} {element}; "
                  f"localparam {element} {self.prefix}_zero = '0; "
                  f"localparam {w} = $bits({self.prefix}_zero); "
                  f"localparam signed [63:0] {self.left} = {_index32(emit(dimension['left']))}, "
                  f"{self.right} = {_index32(emit(dimension['right']))}; "
                  f"localparam {n} = (({self.left}<={self.right})?"
                  f"({self.right}-{self.left}+1):({self.left}-{self.right}+1)); ")
        coerce = self.prefix + "_coerce"
        if two_state:
            # Yosys treats bit/int storage as four-state. Make the SV assignment
            # conversion explicit during constant initialization, including X/Z.
            prefix += (f"function automatic [{w}-1:0] {coerce}(input [{w}-1:0] v); integer j; begin "
                       f"for (j=0;j<{w};j=j+1) {coerce}[j]=(v[j]===1'b1); end endfunction ")

        def cast(value):
            sized = f"{element}'({w}'({value}))"
            return f"{coerce}({sized})" if two_state else sized

        pattern = self.declarator["initializer"]["expr"].get("pattern", {})
        kind = pattern.get("kind")
        if kind in {"SimpleAssignmentPattern", "ReplicatedAssignmentPattern"}:
            words = [item for item in pattern["items"] if item.get("kind") != "Comma"]
            packed = "{" + ",".join(cast(emit(word)) for word in reversed(words)) + "}"
            if kind == "ReplicatedAssignmentPattern":
                packed = "{" + emit(pattern["countExpr"]) + "{" + packed + "}}"
        elif kind == "StructuredAssignmentPattern":
            # Slang checks coverage, duplicate keys and element conversions.
            # One assignment per element selects its key or default. This avoids
            # repeated partial writes to a function result in the native parser.
            function, index = self.prefix + "_init", self.prefix + "_i"
            defaults, stores = [], []
            for item in pattern["items"]:
                if item.get("kind") == "Comma":
                    continue
                value = cast(emit(item['expr']))
                if item["key"]["kind"] == "DefaultPatternKeyExpression":
                    defaults.append(value)
                else:
                    key = _index32(emit(item['key']))
                    stores.append((self.distance(key), value))
            # Without a default, Slang requires explicit keys to cover every
            # element, so this seed is overwritten in full before it is read.
            default = defaults[0] if defaults else f"{w}'(0)"
            for position, value in reversed(stores):
                default = f"(({index}=={position})?({value}):({default}))"
            prefix += (f"function automatic [{w}*{n}-1:0] {function}(); integer {index}; begin "
                       f"for ({index}=0; {index}<{n}; {index}={index}+1) "
                       f"{function}[{index}*{w} +: {w}] = {default}; end endfunction ")
            packed = function + "()"
        else:
            raise UnsupportedConstantArray("ROM initializer is not a supported assignment pattern")
        return prefix + f"localparam [{w}*{n}-1:0] {self.name} = {packed};"


def normalize_constant_arrays(source, cst, ast):
    root, spans = concrete_spans(source, cst)
    declarations = array_declarations(root)
    if not declarations:
        return SourceReplacements(source, ())
    positions = SourceEdits(source, ())
    lines = positions._source_lines

    def offset(line, col):
        if not 1 <= line <= len(lines) or col < 1:
            raise UnsupportedConstantArray("typed node has no original source coordinate")
        result = lines[line - 1] + col - 1
        if positions.original_position(result) != (line, col):
            raise UnsupportedConstantArray("typed node is outside the source line")
        return result

    def area(node):
        if node.get("source_file_start") != "design.sv" or node.get("source_file_end") != "design.sv":
            raise UnsupportedConstantArray("array use is outside the original source")
        return (offset(node["source_line_start"], node["source_column_start"]),
                offset(node["source_line_end"], node["source_column_end"]))

    by_position, identifiers = {}, set()
    if any(type(token) is dict and token.get("kind") == "Identifier"
           and token.get("text", "").lstrip("\\").startswith("__behave_rom_") for token in walk(root)):
        raise UnsupportedConstantArray("source uses the compiler ROM normalization namespace")
    for ordinal, statement in enumerate(declarations):
        declaration = statement["parameter"]
        if declaration["keyword"]["kind"] != "LocalParamKeyword":
            raise UnsupportedConstantArray("ROM normalization needs body localparams")
        for number, decl in enumerate(declaration["declarators"]):
            if decl.get("kind") != "Declarator" or not decl.get("dimensions"):
                continue
            if len(decl["dimensions"]) != 1 or decl["dimensions"][0].get("specifier", {}).get(
                    "selector", {}).get("kind") != "SimpleRangeSelect":
                raise UnsupportedConstantArray("ROM needs one explicitly bounded array dimension")
            prefix = f"__behave_rom_{ordinal}_{number}"
            name = decl["name"]["text"]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) is None:
                raise UnsupportedConstantArray("ROM needs an ordinary source identifier")
            point = spans[id(decl["name"])][0]
            entry = _Array(point, name, prefix + "_width", prefix + "_left", prefix + "_right",
                           prefix + "_element", prefix + "_count", prefix, decl, declaration["type"])
            by_position[point] = entry
            identifiers.add(name)
    nodes = tuple(_nodes(ast))
    aliases = {str(node["addr"]): node for node in nodes if node.get("kind") == "TypeAlias"}
    symbols, checked, layouts = {}, set(), {}
    for node in nodes:
        if node.get("kind") != "Parameter" or node.get("source_file") != "design.sv":
            continue
        point = offset(node["source_line"], node["source_column"])
        if point not in by_position:
            continue
        entry = by_position[point]
        ty = node["type"]
        if type(ty) is not dict or ty.get("kind") != "FixedSizeUnpackedArrayType":
            raise UnsupportedConstantArray("ROM declaration lacks its elaborated unpacked type")
        left, right = _range(ty)
        elem = ty["elementType"]
        layout = _integral(elem, aliases)
        if layout.dimensions != 1:
            raise UnsupportedConstantArray("native frontend requires one packed or predefined integer element dimension")
        if not 1 <= layout.width <= defaults.SIM_RTL_ROM_ELEMENT_BITS \
                or any(not -(1 << 31) <= bound < (1 << 31) for bound in (left, right)):
            raise UnsupportedConstantArray("ROM type/initializer exceeds the normalized layout")
        if not node.get("isLocal") or not node.get("isBody"):
            raise UnsupportedConstantArray("ROM binding is not a body localparam")
        initializer = node.get("initializer", {})
        if initializer.get("kind") not in {"SimpleAssignmentPattern", "ReplicatedAssignmentPattern",
                                            "StructuredAssignmentPattern"} \
                or initializer.get("typeSetters"):
            raise UnsupportedConstantArray("ROM initializer needs positional, repeated or index/default entries")
        if point in layouts and (layouts[point].dimensions, layouts[point].two_state) != (
                layout.dimensions, layout.two_state):
            raise UnsupportedConstantArray("ROM instances disagree on element kind")
        layouts[point] = layout
        symbols[str(node["addr"]) + " " + node["name"]] = entry
        checked.add(point)
    if checked != set(by_position):
        raise UnsupportedConstantArray("not every ROM declaration has an elaborated type witness")
    replacements = {}
    consumed = set()
    queries = set()

    # Render source token spans after their children. This composes queries,
    # nested reads and declaration initializers without overlapping edits or
    # duplicating comments/newlines in generated expressions.
    tokens = tuple(sorted((spans[id(token)], token["text"]) for token in walk(root)
                          if type(token) is dict and "text" in token))
    token_starts = tuple(key[0] for key, _ in tokens)
    edits_at = {}

    def render(region):
        lo, hi = region
        parts, index = [], bisect_left(token_starts, lo)
        while index < len(tokens):
            (start, end), token = tokens[index]
            if end > hi:
                break
            edit = next(((stop, value) for stop, value in edits_at.get(start, ()) if stop <= hi), None)
            if edit is not None:
                stop, value = edit
                parts.append(value)
                index = bisect_left(token_starts, stop)
            else:
                parts.append(token)
                index += 1
        return " ".join(parts)

    def emit(node):
        return render(spans[id(node)])

    def replace(key, value):
        if key in replacements and replacements[key] != value:
            raise UnsupportedConstantArray("one ROM use has incompatible elaborated meanings")
        replacements[key] = value
        edits_at[key[0]] = sorted({**dict(edits_at.get(key[0], ())), key[1]: value}.items(), reverse=True)
    for node in nodes:
        if node.get("kind") != "Call":
            continue
        arguments = node.get("arguments", ())
        references = any(value.get("symbol") in symbols for value in _nodes(arguments))
        if node.get("subroutine") in _TYPE_QUERIES and references and len(arguments) != 1:
            raise UnsupportedConstantArray("ROM type queries need one directly typed array or element")
        if len(arguments) != 1:
            continue
        argument = arguments[0]
        element = symbols.get(argument.get("value", {}).get("symbol")) \
            if argument.get("kind") == "ElementSelect" else None
        if element is not None and node.get("subroutine") in {
                "$size", "$bits", "$left", "$right", "$low", "$high", "$increment",
                "$dimensions", "$unpacked_dimensions"}:
            # Type queries never evaluate the read. Query the same element type
            # at declaration scope, not the canonical vector used to return it.
            replace(area(node), f"{node['subroutine']}({element.prefix}_zero)")
            consumed.update(area(value) for value in _nodes(argument) if value.get("symbol") in symbols)
            queries.add(id(node))
            continue
        if node.get("subroutine") == "$typename" and any(
                value.get("symbol") in symbols for value in _nodes(argument)):
            raise UnsupportedConstantArray("ROM type-name reflection requires original type identity")
        entry = symbols.get(argument.get("symbol"))
        if entry is None or argument.get("kind") != "NamedValue":
            if node.get("subroutine") in _TYPE_QUERIES and references:
                raise UnsupportedConstantArray("ROM type queries need one directly typed array or element")
            continue
        left, right = entry.left, entry.right
        count = f"((({left})<=({right}))?(({right})-({left})+1):(({left})-({right})+1))"
        expression = {"$size": count, "$bits": f"({entry.width}*{count})",
                      "$left": left, "$right": right,
                      "$low": f"(({left})<({right})?({left}):({right}))",
                      "$high": f"(({left})>({right})?({left}):({right}))",
                      "$increment": f"(({left})>=({right})?1:-1)",
                      "$dimensions": str(layouts[entry.point].dimensions + 1),
                      "$unpacked_dimensions": "1"}.get(node.get("subroutine"))
        if expression is None:
            continue
        # These SV array-query functions return int (signed 32-bit).
        replace(area(node), f"$signed(32'({expression}))")
        consumed.add(area(argument))
        queries.add(id(node))
    reads = [node for node in nodes if node.get("kind") == "ElementSelect"
             and node.get("value", {}).get("symbol") in symbols
             and area(node["value"]) not in consumed]
    for node in nodes:
        value = node.get("value")
        if node.get("kind") in {"ElementSelect", "RangeSelect"} and type(value) is dict \
                and value.get("kind") == "ElementSelect" and value.get("value", {}).get("symbol") in symbols:
            raise UnsupportedConstantArray("ROM element subselection needs a packed-bound-preserving rewrite")
    index_widths = {}
    for node in reads:
        index_widths.setdefault(area(node), set()).add(_integral(node["selector"]["type"], aliases).width)
    for node in sorted(reads, key=lambda node: area(node)[1] - area(node)[0]):
        value = node.get("value", {})
        entry = symbols.get(value.get("symbol"))
        if entry is None:
            continue
        if value.get("kind") != "NamedValue":
            raise UnsupportedConstantArray("hierarchical ROM reads require explicit binding retention")
        index = node["selector"]
        _pure_index(index, queries)
        if _integral(index["type"], aliases).width > 32:
            raise UnsupportedConstantArray("ROM indexes wider than 32 bits need a wider arithmetic plan")
        expression = render(area(index))
        # The inner size cast fixes the original expression width before the
        # 32-bit conversion, preserving overflow and signedness. Use a literal
        # only when every elaborated instance agrees; otherwise $bits keeps the
        # per-instance width without evaluating the expression.
        widths = index_widths[area(node)]
        width = str(next(iter(widths))) if len(widths) == 1 else f"$bits({expression})"
        index_value = f"64'({_index32(expression, width)})"
        distance = entry.distance(index_value)
        # Negative distances become large unsigned values, hence one comparison
        # checks both bounds (and does not duplicate the complete index tree).
        valid = f"($unsigned({distance})<{entry.count})===1'b1"
        selected = f"{entry.name}[({distance}*{entry.width}) +: {entry.width}]"
        invalid = "'0" if layouts[entry.point].two_state else "'x"
        # Yosys drops typedef-cast signs, and Slang JSON omits the nondefault
        # sign of predefined integers. A same-width typed zero makes the
        # conditional signed iff the original element is signed. Its constant
        # true arm preserves even partially unknown / high-impedance words.
        word = f"$signed({entry.width}'(({valid})?({selected}):{entry.width}'({invalid})))"
        replace(area(node), f"(1'b1 ? ({word}) : {entry.prefix}_zero)")
        consumed.add(area(value))
    # Missing references are refusals, including whole-array and elaboration-time
    # operations. No surviving use may accidentally see the packed replacement.
    for node in nodes:
        if node.get("symbol") in symbols and area(node) not in consumed:
            raise UnsupportedConstantArray("ROM has an unsupported whole-array or compile-time use")
    for token in walk(root):
        if type(token) is dict and token.get("kind") == "Identifier" and token.get("text") in identifiers:
            start, end = spans[id(token)]
            if start in by_position or any(lo <= start and end <= hi for lo, hi in consumed):
                continue
            raise UnsupportedConstantArray("ROM identifier has no unique typed read witness")
    for statement in declarations:
        declaration = statement["parameter"]
        parts = []
        for decl in declaration["declarators"]:
            if decl.get("kind") != "Declarator":
                continue
            entry = by_position.get(spans[id(decl["name"])][0])
            parts.append(entry.declaration(emit, layouts[entry.point].two_state) if entry else
                         f"localparam {emit(declaration['type'])} {emit(decl)};")
        replace(spans[id(statement)], " ".join(parts))
    outermost, end = [], -1
    for (lo, hi), value in sorted(replacements.items(), key=lambda item: (item[0][0], -item[0][1])):
        if hi <= end:
            continue
        if lo < end:
            raise UnsupportedConstantArray("ROM compiler spans overlap without syntactic containment")
        outermost.append(Replacement(lo, hi, value))
        end = hi
    return SourceReplacements(source, tuple(outermost))
