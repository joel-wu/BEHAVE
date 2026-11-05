"""Shared lexical bindings for the finite byte-sequence operations."""

import ast

BYTE_CARRIERS = frozenset({"bytes", "bytearray"})


def is_to_bytes(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "to_bytes")


def to_bytes_arguments(node):
    """Named operands in Python evaluation order, or an unsupported call form."""
    if len(node.args) > 2 or any(isinstance(arg, ast.Starred) for arg in node.args):
        return None
    arguments = list(zip(("length", "byteorder"), node.args))
    seen = {name for name, _ in arguments}
    for keyword in node.keywords:
        if keyword.arg not in {"length", "byteorder", "signed"} or keyword.arg in seen:
            return None
        seen.add(keyword.arg)
        arguments.append((keyword.arg, keyword.value))
    return tuple(arguments)


def is_from_bytes(node, is_builtin):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "from_bytes"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "int"
            and is_builtin("int"))


def contains_byte_sequence(contract):
    return contract.is_sequence and (
        contract.carrier in BYTE_CARRIERS
        or contract.element is not None and contains_byte_sequence(contract.element))
