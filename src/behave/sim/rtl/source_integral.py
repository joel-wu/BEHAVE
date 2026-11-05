"""Slang integral types expressed with the existing SolverIR primitives.

The typed AST has already applied SystemVerilog's sizing and conversion rules.
Keep each resulting width and signedness, including intermediate operations;
wrapping only the final assignment would change comparisons and shifts.
"""

import re
from dataclasses import dataclass

from ..symbolic import ir, primitives
from ..core.refusal import Refusal, Refused

# Total scalar operations shared by command lowering and header range proofs.
# Division, shifts and effectful operators have their own source semantics.
BINARY_PRIMITIVES = {
    "Add": "add", "Subtract": "sub", "Multiply": "mul",
    "BinaryAnd": "and", "BinaryOr": "or", "BinaryXor": "xor", "BinaryXnor": "xor",
    "Equality": "eq", "Inequality": "ne", "LessThan": "lt",
    "LessThanEqual": "le", "GreaterThan": "gt", "GreaterThanEqual": "ge",
}
UNARY_PRIMITIVES = {"Minus": "neg", "BitwiseNot": "inv"}


def binary_value(operation, left, right):
    """Shared scalar denotation; the caller applies the compiler result type."""
    primitive = BINARY_PRIMITIVES[operation]
    value = ir.Op(primitive, left, right)
    if operation == "BinaryXnor":
        value = ir.Op("inv", value)
    elif primitive in {"eq", "ne", "lt", "le", "gt", "ge"}:
        value = ir.Op("toint", value)
    return value


def typed_symbols(document):
    """Index storage AND types; the event walk intentionally skips type trees."""
    symbols = {}

    def visit(node):
        if isinstance(node, dict):
            if "addr" in node:
                symbols[str(node["addr"])] = node
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(document)
    for fact in document.get("integral_types", ()):
        identity = str(fact["type_addr"])
        canonical = str(fact.get("canonical_type_addr", identity))
        # Slang can serialize a type-parameter alias as a reference only. Keep
        # the compiler's link to its canonical layout, not a width-based guess.
        # Facts without that link still only describe a scalar value.
        missing = ({"kind": "TypeAlias", "target": canonical} if canonical != identity
                   else {"kind": "IntegralValueType"})
        symbols[identity] = {**symbols.get(identity, missing),
                             "bitWidth": fact["width"], "isSigned": fact["signed"]}
        if "four_state" in fact:
            symbols[identity]["isFourState"] = fact["four_state"]
    for fact in document.get("parameter_values", ()):
        identity = str(fact["symbol_addr"])
        symbols[identity] = {**symbols[identity], "constant_value": fact["value"]}
    return symbols


@dataclass(frozen=True, slots=True)
class IntegralType:
    width: int
    signed: bool
    left: int = 0
    right: int = 0
    element_width: int = 1

    def wrap(self, value):
        operation = "swrap" if self.signed else "uwrap"
        # The compiler has fixed this width/sign. Preserve literal constants
        # as literals using the same denotation as execution and SMT lowering;
        # a wrapped zero must not become an unknown control predicate.
        if isinstance(value, ir.Lit) and type(value.value) is int:
            return ir.Lit(primitives.primitive(operation).denotation(value.value, self.width))
        return ir.Op(operation, value, ir.Lit(self.width))

    def convert(self, value, source, *, propagated=False):
        if propagated:
            # SV contextual conversion changes signedness at the source width
            # before resizing; explicit/assignment casts extend the source sign.
            value = IntegralType(source.width, self.signed).wrap(value)
        return self.wrap(value)

    @property
    def interval(self):
        return ((-(1 << (self.width - 1)), (1 << (self.width - 1)) - 1)
                if self.signed else (0, (1 << self.width) - 1))

    @property
    def ir_type(self):
        from ..symbolic import typecheck as types
        return types.INTEGER

    @property
    def domain(self):
        from ..symbolic import ranges
        return ranges.Range(*self.interval)

    @property
    def signature_type(self):
        from ..core.contract import FiniteType
        return FiniteType(self.width, self.signed)

    @property
    def observation(self):
        return self.width

    def decode(self, value):
        if type(value) is not int or not 0 <= value < 1 << self.width:
            raise ValueError("a source integral value needs every observed bit")
        return value - (1 << self.width) if self.signed and value & (1 << (self.width - 1)) else value


def integral_type(node, symbols):
    """Resolve compiler type references, never infer a width from source text."""
    while isinstance(node, str):
        node = symbols[node.split(" ", 1)[0]]
    node = symbols.get(str(node.get("addr")), node)
    kind = node.get("kind")
    if kind == "TypeAlias":
        return integral_type(node["target"], symbols)
    if kind == "ScalarType":
        return IntegralType(1, node["isSigned"])
    if kind == "PredefinedIntegerType":
        if "bitWidth" not in node or "isSigned" not in node:
            raise Refused(Refusal("rtl-source-ir-type-facts-missing",
                                  "predefined integers need the source frontend's resolved width and signedness"))
        width = node["bitWidth"]
        return IntegralType(width, node["isSigned"], width - 1, 0)
    if kind == "PackedArrayType":
        element = integral_type(node["elementType"], symbols)
        left, right = map(int, node["range"][1:-1].split(":"))
        return IntegralType((abs(left - right) + 1) * element.width,
                            element.signed, left, right, element.width)
    if kind == "EnumType":
        return integral_type(node["baseType"], symbols)
    raise Refused(Refusal("rtl-source-ir-type-unsupported",
                          "source execution needs an implemented typed value", node_kind=kind))


def integral_value_facts(node, symbols):
    """Compiler scalar facts, without inventing a packed storage layout."""
    while isinstance(node, str):
        node = symbols[node.split(" ", 1)[0]]
    node = symbols.get(str(node.get("addr")), node)
    if "isFourState" not in node:
        raise Refused(Refusal("rtl-operation-type-facts-missing",
                              "native operation capture needs the compiler's two/four-state type fact"))
    if node.get("kind") == "IntegralValueType":
        return node["bitWidth"], node["isSigned"], node["isFourState"]
    value = integral_type(node, symbols)
    return value.width, value.signed, node["isFourState"]


def four_state_literal(value):
    """Whether Slang serialized an integral constant containing X/Z bits."""
    match = re.fullmatch(r"-?\d+'[sS]?[bBoOdDhH]([0-9a-fA-F_xXzZ?]+)", value)
    return match is not None and any(bit in match[1].lower() for bit in 'xz?')


def integer_literal(value):
    """Read Slang's value serialization, rejecting X/Z rather than inventing bits."""
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    match = re.fullmatch(r"(-?)(\d+)'([sS]?)([bBoOdDhH])([0-9a-fA-F_]+)", value)
    if match:
        negative, width, signed, radix, digits = match.groups()
        result = int(digits.replace("_", ""), {"b": 2, "o": 8, "d": 10, "h": 16}[radix.lower()])
        # Slang serializes negative SVInt values as e.g. -4'sd2,
        # including the minimum signed value as -4'sd8. These are a
        # signed magnitude serialization, not a second source unary op.
        if negative:
            return -result
        if signed and result & (1 << (int(width) - 1)):
            result -= 1 << int(width)
        return result
    raise Refused(Refusal("rtl-source-ir-literal-unsupported",
                          "the source literal has no two-state integral value", value=value))


def native_assignment_literal(value):
    """An X literal stored by the pinned Verilator two-state execution profile.

    This is NOT an expression-level X substitution. Verilator folds constant
    expressions before materializing their unknown bits: X+1 and a stored X
    followed by +1 need not agree. Callers must establish a literal-to-variable
    assignment boundary. Net resolution, arithmetic and unknown conditions do
    not acquire a denotation from this helper.
    """
    from .native_events import VERILATOR_FLAGS

    # The qualified default ('fast') and explicit zero profile materialize
    # these bits as zero. Never silently retain that rule under unique/one.
    flags = tuple(VERILATOR_FLAGS)
    for index, flag in enumerate(flags):
        if flag == '--x-assign' and (index + 1 >= len(flags) or flags[index + 1] not in {'0', 'fast'}):
            return None
    if any(flag.startswith('--x-assign=') for flag in flags):
        return None
    # Z/? may invoke tristate lowering even for a procedural output variable.
    # Its native resolution/observer correspondence is a different obligation.
    match = re.fullmatch(r"(\d+)'([sS]?)([bBoOhH])([0-9a-fA-F_xX]+)", value)
    if match is None:
        return None
    width, signed, radix, digits = match.groups()
    filled = ''.join('0' if bit in 'xX' else bit for bit in digits)
    return integer_literal(f"{width}'{signed}{radix}{filled}")
