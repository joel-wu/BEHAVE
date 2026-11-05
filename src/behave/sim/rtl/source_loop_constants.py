"""Join selected loop headers to Slang constants and conservative typed ranges.

Range propagation uses the shared scalar IR, not a separate HDL evaluator.
A key includes resolved
symbol identities and original coordinates, so another instance or a shadowed
name cannot supply a bound. Keys are internal joins, never public goal IDs.
"""

from dataclasses import dataclass

from ..symbolic import ir
from ..symbolic import ranges
from ..core.record_value import canonical_json
from ..core.refusal import Refused
from .source_edits import SourceEdits
from .source_integral import (
    BINARY_PRIMITIVES,
    UNARY_PRIMITIVES,
    IntegralType,
    binary_value,
    integer_literal,
    integral_type,
    typed_symbols,
)


def loop_key(node):
    def without_constants(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "constant":
                    continue
                if key == "type":
                    # The serializer expands a type once, then emits links.
                    item = str(item["addr"]) if isinstance(item, dict) else item.split(" ", 1)[0]
                result[key] = without_constants(item)
            return result
        if isinstance(value, list):
            return [without_constants(item) for item in value]
        return value

    return canonical_json(without_constants(node.get("stopExpr")))


@dataclass(frozen=True, slots=True)
class LoopHeader:
    key: str
    constants: tuple[tuple[tuple[int, int], int], ...]
    comparison_unsigned: bool
    comparison_width: int
    counter_type: IntegralType | None = None

    def value_at(self, span):
        return dict(self.constants).get(span)


def _integral_interval(node, symbols, *, symbol_ranges=None):
    """Typed bounds using the same scalar IR and range transformers.

    Every intermediate result retains SV sizing/conversion. Unsupported value
    forms contribute their full compiler type domain, never sampled values or
    a guessed invariant. A caller may supply proved invariant symbol ranges;
    conversions still apply at every intermediate expression. This does not
    authorize effects in loop tests.
    """
    domains = {}

    def expression(value):
        target = integral_type(value["type"], symbols)
        if "constant" in value:
            return target.wrap(ir.Lit(integer_literal(value["constant"])))
        kind, op = value.get("kind"), value.get("op")
        if kind in {"IntegerLiteral", "UnbasedUnsizedIntegerLiteral"}:
            return target.wrap(ir.Lit(integer_literal(value["value"])))
        if kind in {"NamedValue", "HierarchicalValue"}:
            identity = value["symbol"].split(" ", 1)[0]
            symbol = symbols[identity]
            if symbol["kind"] in {"Parameter", "EnumValue", "Genvar"}:
                return target.wrap(ir.Lit(integer_literal(symbol["value"])))
            if symbol_ranges is not None and identity in symbol_ranges:
                name = f"bound{len(domains)}"
                domains[name] = symbol_ranges[identity]
                return target.wrap(ir.Var(name))
        if kind == "Conversion":
            source = integral_type(value["operand"]["type"], symbols)
            conversion = value.get("conversionKind")
            if conversion in {"Implicit", "Explicit", "Propagated"} or (
                    conversion is None and (source.signed == target.signed or source.width >= target.width)):
                return target.convert(expression(value["operand"]), source,
                                      propagated=conversion == "Propagated")
        if kind == "BinaryOp" and op in BINARY_PRIMITIVES:
            result = binary_value(op, expression(value["left"]), expression(value["right"]))
            return target.wrap(result)
        if kind == "UnaryOp" and op in UNARY_PRIMITIVES:
            return target.wrap(ir.Op(UNARY_PRIMITIVES[op], expression(value["operand"])))
        if kind == "ConditionalOp":
            # The condition may change each iteration; both branches contribute.
            name = f"condition{len(domains)}"
            domains[name] = ranges.Range(0, 1)
            return target.wrap(ir.Op("ite", ir.Op("truth", ir.Var(name)),
                                     expression(value["left"]), expression(value["right"])))
        name = f"value{len(domains)}"
        domains[name] = target.domain
        return ir.Var(name)

    result = ranges.of_expression(expression(node), domains)
    return (integral_type(node["type"], symbols).interval if result is None
            else (result.lo, result.hi))


def loop_headers(source, document, *, filename):
    positions = SourceEdits(source, ())
    symbols = typed_symbols(document)
    result = {}
    for header in document.get("loop_headers", ()):
        condition = header.get("stopExpr", {})
        if condition.get("kind") != "BinaryOp":
            continue
        try:
            comparison_type = integral_type(condition["left"]["type"], symbols)
            counter = condition["left"]
            while counter.get("kind") == "Conversion":
                counter = counter["operand"]
            counter_type = (integral_type(counter["type"], symbols)
                            if counter.get("kind") in {"NamedValue", "HierarchicalValue"} else None)
        except Refused:
            continue
        constants = {}

        def collect(node, constants=constants):
            if isinstance(node, dict):
                if "constant" in node and node.get("source_file_start") == filename \
                        and node.get("source_file_end") == filename:
                    coordinates = tuple(positions._source_lines[node[f"source_line_{side}"]-1]
                                        + node[f"source_column_{side}"]-1 for side in ("start", "end"))
                    try:
                        value = integer_literal(node["constant"])
                    except Refused:
                        pass  # X/Z or nonintegral values do not prove an integer bound.
                    else:
                        # Implicit conversions share their operand's source span.
                        # The outermost, context-sized value is the one executed.
                        constants.setdefault(coordinates, value)
                for key, child in node.items():
                    if key != "type":
                        collect(child)
            elif isinstance(node, list):
                for child in node:
                    collect(child)

        collect(header)
        key = loop_key(header)
        resolved = LoopHeader(key, tuple(sorted(constants.items())),
                              not comparison_type.signed, comparison_type.width,
                              counter_type)
        if key in result and result[key] != resolved:
            raise ValueError("one bound loop condition has conflicting compiler constants")
        result[key] = resolved
    return result
