"""Keep native source reads alive without changing their value or evaluation site.

The judge's DPI dependency sink has no effects and returns no value. Calling it
inside a type-parameterized identity function keeps an integral read visible to
the native scheduler even when its arithmetic consumer simplifies to a constant.
The original expression is evaluated once. A separate type witness preserves
width, signedness, enum identity and packed layout in each elaborated instance.
Its element indices / indexed-part-select positions are neutral and in range:
the element type does not depend on the position, and no effectful index is copied into a
type query. No signal is read through VPI or at process entry.
"""

from dataclasses import dataclass

READ_CLASS = "__behave_native_event_read"
READ_PACKAGE = "__behave_native_event_reads"
DEPENDENCY = "__behave_native_event_dependency"


def type_witness(node, source, span, resolve_type):
    """An exact type witness that never copies an effectful selection position.

    The element type / indexed slice width is independent of its position. The
    pinned backend can hoist effects even from type queries, so both read and
    write observers use this same neutral form. Increment expressions have the
    type of their operand; their side effect has no place in a type witness.
    """
    start, end = span(node)
    kind = node.get("kind")
    if kind == "UnaryOp" and node.get("op") in {
            "Preincrement", "Predecrement", "Postincrement", "Postdecrement"}:
        return type_witness(node["operand"], source, span, resolve_type)
    if kind == "Assignment":
        return type_witness(node["left"], source, span, resolve_type)
    if kind in {"NamedValue", "HierarchicalValue"}:
        return source[start:end].decode()
    replacements = []
    if kind == "Concatenation":
        replacements.extend((*span(part), type_witness(part, source, span, resolve_type))
                            for part in node["operands"])
    elif kind in {"ElementSelect", "RangeSelect", "MemberAccess"}:
        base = node["value"]
        witness = type_witness(base, source, span, resolve_type)
        replacements.append((*span(base), witness))
        if kind == "ElementSelect":
            index = node["selector"]
            position = '""' if resolve_type(index.get("type", {})).get("kind") == "StringType" else "0"
            base_type = resolve_type(base["type"])
            while base_type.get("kind") == "TypeAlias":
                base_type = resolve_type(base_type["target"])
            if base_type.get("kind") in {"FixedSizeUnpackedArrayType", "PackedArrayType"}:
                # Zero need not be a valid index. Use the elaborated instance's
                # declared bound; never copy a dynamic/effectful source index.
                position = f"$left({witness})"
            replacements.append((*span(index), position))
        elif kind == "RangeSelect" and node.get("selectionKind") in {"IndexedUp", "IndexedDown"}:
            bound = "$low" if node["selectionKind"] == "IndexedUp" else "$high"
            replacements.append((*span(node["left"]), f"{bound}({witness})"))
    else:
        # Index types can themselves contain arithmetic, calls or nested
        # accesses. Neutralize effects recursively, not only a top-level i++.
        def expressions(value):
            if isinstance(value, list):
                for item in value:
                    yield from expressions(item)
            elif isinstance(value, dict):
                if "source_file_start" in value:
                    yield value
                else:
                    for key, child in value.items():
                        if key not in {"type", "elementType", "netType"}:
                            yield from expressions(child)
        for key, value in node.items():
            if key not in {"type", "elementType", "netType"}:
                replacements.extend((*span(child), type_witness(child, source, span, resolve_type))
                                    for child in expressions(value))
    pieces, cursor = [], start
    for first, last, text in sorted(replacements):
        pieces.extend((source[cursor:first].decode(), text))
        cursor = last
    pieces.append(source[cursor:end].decode())
    return "".join(pieces)


@dataclass(frozen=True, order=True, slots=True)
class DependencyRead:
    start: int
    end: int
    type_expression: str


def read_function() -> str:
    return f'''
package {READ_PACKAGE};
import "DPI-C" function void {DEPENDENCY}(input logic value);
class {READ_CLASS} #(type T=logic);
  static function automatic T value(input T v);
    {DEPENDENCY}(^v);
    return v;
  endfunction
endclass
endpackage
'''


def read_wrapper(expression: str) -> tuple[str, str]:
    return f"{READ_PACKAGE}::{READ_CLASS}#(type({expression}))::value(", ")"
