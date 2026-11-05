"""Byte operations use the common finite-array and logical-length carrier.

These source operations receive an explicit lowerer; they do not add mixin
methods or invent separate evaluation, guard or storage semantics.
"""

import ast

from ..symbolic import ir
from ..symbolic import ranges
from .extract import LoweringError
from .lower_static_refs import _StaticRef
from .source_calls import has_plain_positional_arity
from .source_bytes import to_bytes_arguments
from ..symbolic.value_contract import SCALAR, ValueContract


def _mode(low, source):
    mode = low.expr(source)
    if isinstance(mode, ir.Lit):
        return mode.value
    if isinstance(mode, _StaticRef) and mode.alternatives and all(
            item == mode.alternatives[0][1] for _, item in mode.alternatives):
        return mode.alternatives[0][1]
    raise LoweringError(source, "byte conversion mode is not source-fixed",
                        code="byte-conversion-mode-unproved")


def _checked_element(low, node, value, *, active=None):
    if not isinstance(value, ir.EXPRS) or low._shape(value):
        raise LoweringError(node, "byte elements need integer values",
                            code="byte-element-type-unsupported")
    value = ir.Op("toint", value)
    invalid = ir.Op("lor", ir.Op("lt", value, ir.Lit(0)),
                    ir.Op("gt", value, ir.Lit(255)))
    if active is not None:
        invalid = ir.Op("land", active, invalid)
    low._guard(invalid, low._safety_goal(node, "valueerror"), "ValueError", node)
    return value


def _result(low, node, contract, cells, count):
    name, length = low.storage.fresh(), low.storage.fresh()
    low.storage.require_shape(name, (contract.maximum,), node)
    low.flow.lengths[name] = (contract.maximum,)
    low.flow.carriers[name] = contract.carrier
    low.flow.contracts[name] = contract
    low._register_dynamic_sequence(name, length, contract.maximum)
    low._record_range(name, ir.Op("arraylit", *cells))
    low.before(ir.Seq(ir.Assign(name, ir.Op("arraylit", *cells)),
                       ir.Assign(length, count)))
    return ir.Var(name)


def construct(low, node, carrier):
    if not has_plain_positional_arity(node, 0, 1):
        raise LoweringError(node, "byte construction needs zero or one positional value",
                            code="byte-constructor-form-unsupported")
    contract = low._contract_ast(node)
    if not contract.is_sequence or contract.carrier != carrier:
        raise LoweringError(node, "byte construction needs a finite source length",
                            code="byte-length-unproved")
    if not node.args:
        low._discharge(low._safety_goal(node, "valueerror"), "empty byte construction is total")
        return _result(low, node, contract, (), ir.Lit(0))
    arg = node.args[0]
    if isinstance(arg, ast.GeneratorExp):
        result = low.storage.fresh()
        planned = ValueContract.sequence("list", contract.minimum, contract.maximum, SCALAR)
        command = low._new_comprehension_sequence(
            result, arg, planned=planned,
            convert_element=lambda value: _checked_element(low, node, value))
        low.before(command)
        target = low._name(result)
        low.flow.contracts[target] = contract
        low.flow.carriers[target] = carrier
        if contract.maximum == 0:
            low._discharge(low._safety_goal(node, "valueerror"),
                            "the finite generator yields no byte")
        return ir.Var(target)
    value = low.expr(arg)
    shape = low._shape(value)
    if not shape:
        count = low._capture(ir.Op("toint", value))
        low._guard(ir.Op("lt", count, ir.Lit(0)), low._safety_goal(node, "valueerror"),
                    "ValueError", node)
        cells = [ir.Lit(0)] * contract.maximum
    else:
        actual = low._contract_for_value(arg, value)
        if len(shape) != 1 or not actual.is_sequence:
            raise LoweringError(node, "byte construction needs a flat integer iterable",
                                code="byte-element-type-unsupported")
        value = low._capture(value)
        count = low._capture(low._logical_length(arg, value, actual))
        cells = [_checked_element(low, node, ir.Sel(value, ir.Lit(i)),
                                   active=ir.Op("lt", ir.Lit(i), count))
                 for i in range(contract.maximum)]
        if contract.maximum == 0:
            low._discharge(low._safety_goal(node, "valueerror"), "the byte iterable is empty")
    return _result(low, node, contract, cells, count)


def from_bytes(low, node):
    if not 1 <= len(node.args) <= 2 or any(isinstance(arg, ast.Starred) for arg in node.args):
        raise LoweringError(node, "int.from_bytes needs a finite iterable and byte order",
                            code="byte-conversion-form-unsupported")
    keywords = {keyword.arg: keyword.value for keyword in node.keywords}
    if (len(keywords) != len(node.keywords) or not set(keywords) <= {"byteorder", "signed"}
            or (len(node.args) == 2 and "byteorder" in keywords)):
        raise LoweringError(node, "int.from_bytes keyword arguments are not represented",
                            code="byte-conversion-form-unsupported")
    arg = node.args[0]
    value = low._capture(low.expr(arg))
    # Capture the iterable first, then evaluate each mode in Python order.
    # A helper returning a fixed string can still have state/goal effects.
    modes = [("byteorder", mode) for mode in node.args[1:]]
    modes.extend((keyword.arg, keyword.value) for keyword in node.keywords)
    values = {"byteorder": "big", "signed": False}
    for name, source in modes:
        values[name] = _mode(low, source)
    order, signed = values["byteorder"], values["signed"]
    if type(order) is not str or order not in {"little", "big"} or type(signed) is not bool:
        raise LoweringError(node, "byte order and signedness must be source-fixed",
                            code="byte-conversion-mode-unproved")
    contract = low._contract_for_value(arg, value)
    if not contract.is_sequence or len(low._shape(value)) != 1:
        raise LoweringError(node, "int.from_bytes needs a flat represented iterable",
                            code="byte-conversion-source-unsupported")
    count = low._capture(low._logical_length(arg, value, contract))
    terms = []
    for i in range(contract.maximum):
        active = ir.Op("lt", ir.Lit(i), count)
        item = _checked_element(low, node, ir.Sel(value, ir.Lit(i)), active=active)
        position = (ir.Lit(i) if order == "little" else
                    ir.Op("ite", active, ir.Op("sub", count, ir.Lit(i + 1)), ir.Lit(0)))
        terms.append(ir.Op("ite", active,
                           ir.Op("shl", item, ir.Op("mul", ir.Lit(8), position)), ir.Lit(0)))
    if contract.maximum == 0:
        low._discharge(low._safety_goal(node, "valueerror"), "the byte iterable is empty")
    result = ir._balanced_op("add", terms, ir.Lit(0))
    if signed:
        modulus = ir.Op("shl", ir.Lit(1), ir.Op("mul", ir.Lit(8), count))
        negative = ir.Op("ge", ir.Op("mul", ir.Lit(2), result), modulus)
        result = ir.Op("ite", negative, ir.Op("sub", result, modulus), result)
    return result


def to_bytes(low, node):
    arguments = to_bytes_arguments(node)
    if arguments is None:
        raise LoweringError(node, "int.to_bytes arguments are not represented",
                            code="byte-conversion-form-unsupported")
    receiver = node.func.value
    if low._contract_ast(receiver).kind != "scalar":
        raise LoweringError(receiver, "to_bytes needs an integer receiver",
                            code="byte-conversion-source-unsupported")
    value = low._capture(low._integer_context_expr(receiver))
    count = ir.Lit(1)
    modes = {"byteorder": "big", "signed": False}
    for name, source in arguments:
        if name == "length":
            count = low._capture(low._integer_context_expr(source))
        else:
            modes[name] = _mode(low, source)
    order, signed = modes["byteorder"], modes["signed"]
    if type(order) is not str or order not in {"little", "big"} or type(signed) is not bool:
        raise LoweringError(node, "byte order and signedness must be source-fixed",
                            code="byte-conversion-mode-unproved")
    contract = low._contract_ast(node)
    if not contract.is_sequence or contract.carrier != "bytes":
        raise LoweringError(node, "int.to_bytes needs a finite length",
                            code="byte-length-unproved")
    low._guard(ir.Op("lt", count, ir.Lit(0)), low._safety_goal(node, "valueerror"),
                "ValueError", node)
    bits = ir.Op("mul", ir.Lit(8), count)
    if signed:
        # Python also represents 0 and -1 with zero bytes when signed.
        exponent = ir.Op("ite", ir.Op("gt", count, ir.Lit(0)),
                         ir.Op("sub", bits, ir.Lit(1)), ir.Lit(0))
        limit = ir.Op("shl", ir.Lit(1), exponent)
        minimum, maximum = ir.Op("neg", limit), ir.Op("sub", limit, ir.Lit(1))
    else:
        minimum = ir.Lit(0)
        maximum = ir.Op("sub", ir.Op("shl", ir.Lit(1), bits), ir.Lit(1))
    low._guard(ir.Op("lor", ir.Op("lt", value, minimum), ir.Op("gt", value, maximum)),
                low._safety_goal(node, "overflowerror"), "OverflowError", node)
    cells = []
    for i in range(contract.maximum):
        active = ir.Op("lt", ir.Lit(i), count)
        position = (ir.Lit(i) if order == "little" else
                    ir.Op("ite", active, ir.Op("sub", count, ir.Lit(i + 1)), ir.Lit(0)))
        byte = ir.Op("and", ir.Op("shr", value, ir.Op("mul", ir.Lit(8), position)), ir.Lit(255))
        cells.append(ir.Op("ite", active, byte, ir.Lit(0)))
    return _result(low, node, contract, cells, count)


def check_store(low, node, value):
    """Only total byte writes use the generic sequence store path.

    A potentially failing byte mutation needs its own source-owned failure
    site and partial-update semantics; do not silently accept it as a list.
    """
    bound = ranges.bounds_of_expression(value, low.flow.bounds)
    finite = None if bound is None else bound.finite()
    if finite is None or finite.lo < 0 or finite.hi > 255:
        raise LoweringError(node, "a byte write may leave the range 0..255",
                            code="byte-mutation-range-unproved")
