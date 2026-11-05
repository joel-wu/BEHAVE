"""The shared Behavior source vocabulary and registered intrinsics.

The exact contract is intentionally small: a model imports named operations from
``behave_intrinsics`` at module scope.  The binding may be aliased, but it must
still resolve to that import at the call site.  Bare module objects, wildcard
imports and implicit recognition of helper spellings are outside the exact subset.
Concrete library imports are listed separately and imply no symbolic encoding.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cache

from ..symbolic import primitives
from .method_binding import METHOD_DECORATORS

MODULE = "behave_intrinsics"
#: The complete import boundary of the lowered source language.  ``fp_lib``
#: is trusted source that is inlined; this module is the typed primitive
#: registry.  Host modules are not implicit primitives merely because Python
#: can import them.
APPROVED_MODULES = frozenset({"fp_lib", MODULE})

# Concrete Python execution can use numerical/byte-packing libraries without
# claiming symbolic encodings. The exact frontend uses APPROVED_MODULES.
CONCRETE_MODULES = APPROVED_MODULES | frozenset({"struct", "math", "dataclasses", "typing"})

# Resolution and concrete execution share these names. Individual lowering rules
# still decide which argument forms and value domains have exact IR semantics.
BUILTIN_NAMES = METHOD_DECORATORS | frozenset({
    "range", "reversed", "len", "enumerate", "zip", "min", "max", "abs",
    "int", "bool", "sum", "sorted", "list", "tuple", "set", "dict",
    "float", "str", "frozenset", "bytearray", "all", "any", "bin", "hex",
    "oct", "round", "divmod", "pow", "ord", "chr", "iter", "next", "getattr", "setattr",
    "map", "bytes", "object",
})


def binary_digit_count(node, *, is_builtin):
    """The closed ``bin(integer).count('0' | '1')`` source operation.

    Return its integer operand and requested digit. Binding ownership belongs
    to the caller; no string value or arbitrary ``count`` method is admitted.
    """
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "count"
            and len(node.args) == 1 and not node.keywords
            and isinstance(node.args[0], ast.Constant)
            and type(node.args[0].value) is str
            and node.args[0].value in {"0", "1"}):
        return None
    formatted = node.func.value
    if not (isinstance(formatted, ast.Call)
            and isinstance(formatted.func, ast.Name)
            and formatted.func.id == "bin" and is_builtin("bin")
            and len(formatted.args) == 1 and not formatted.keywords
            and not isinstance(formatted.args[0], ast.Starred)):
        return None
    return formatted.args[0], node.args[0].value


@cache
def concrete_exports(module):
    """Python star-import names from a registered, trusted runtime library.

    This never imports captured candidate modules. Their namespaces are owned by
    the static linker. Name availability is not a promise of exact IR lowering.
    """
    if module not in CONCRETE_MODULES:
        raise ValueError("exports require a registered concrete library")
    if module == "fp_lib":
        from . import fp_lib as library
    elif module == MODULE:
        from . import behave_intrinsics as library
    else:
        import importlib
        library = importlib.import_module(module)
    return tuple(getattr(library, "__all__", tuple(name for name in vars(library) if not name.startswith("_"))))


def concrete_import_aliases(node):
    """Resolve approved wildcard imports once for linking and source consumers."""
    if node.level or node.module not in CONCRETE_MODULES:
        return tuple(node.names)
    aliases = []
    for alias in node.names:
        if alias.name == "*":
            aliases.extend(ast.alias(name=name) for name in concrete_exports(node.module))
        else:
            aliases.append(alias)
    return tuple(aliases)


@dataclass(frozen=True, slots=True)
class FixedWidthHelper:
    """An ordinary source helper proved equal to one registered wrap.

    This is a syntactic certificate, not recognition by spelling.  The
    accepted bodies below are complete small programs whose Python integer
    denotation is exactly ``uwrap`` or ``swrap`` for every positive fixed
    width.  A body with any additional statement, call, default, decorator or
    annotation is not summarized and follows the ordinary inlining path.
    """

    symbol: str
    form: str


def _plain_parameters(function):
    arguments = function.args
    if (function.decorator_list or function.returns is not None
            or function.type_comment is not None
            or arguments.posonlyargs or arguments.vararg is not None
            or arguments.kwonlyargs or arguments.kw_defaults
            or arguments.kwarg is not None or arguments.defaults
            or len(arguments.args) != 2
            or any(argument.annotation is not None
                   for argument in arguments.args)):
        return None
    return arguments.args[0].arg, arguments.args[1].arg


def _name(node, spelling):
    return isinstance(node, ast.Name) and node.id == spelling


def _integer(node, value):
    return isinstance(node, ast.Constant) and type(node.value) is int \
        and node.value == value


def _power_of_two(node, width, *, minus_one=False):
    if minus_one:
        return (isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Sub)
                and _power_of_two(node.left, width)
                and _integer(node.right, 1))
    return (isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.LShift)
            and _integer(node.left, 1)
            and _name(node.right, width))


def _sign_bit(node, width):
    return (isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.LShift)
            and _integer(node.left, 1)
            and isinstance(node.right, ast.BinOp)
            and isinstance(node.right.op, ast.Sub)
            and _name(node.right.left, width)
            and _integer(node.right.right, 1))


def _unsigned_value(node, value, width):
    return (isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.BitAnd)
            and _name(node.left, value)
            and _power_of_two(node.right, width, minus_one=True))


def _mask_assignment(node, value, width):
    if isinstance(node, ast.AugAssign):
        return (isinstance(node.op, ast.BitAnd)
                and _name(node.target, value)
                and _power_of_two(node.value, width, minus_one=True))
    return (isinstance(node, ast.Assign)
            and node.type_comment is None
            and len(node.targets) == 1
            and _name(node.targets[0], value)
            and _unsigned_value(node.value, value, width))


def _sign_test(node, value, width):
    return (isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.BitAnd)
            and _name(node.left, value)
            and _sign_bit(node.right, width))


def _signed_value(node, value, width):
    return (isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Sub)
            and _name(node.left, value)
            and _power_of_two(node.right, width))


def _signed_assignment(node, value, width):
    if isinstance(node, ast.AugAssign):
        return (isinstance(node.op, ast.Sub)
                and _name(node.target, value)
                and _power_of_two(node.value, width))
    return (isinstance(node, ast.Assign)
            and node.type_comment is None
            and len(node.targets) == 1
            and _name(node.targets[0], value)
            and _signed_value(node.value, value, width))


def certified_fixed_width_helper(function):
    """Return the registered wrap exactly denoted by ``function``, if any.

    The certificate deliberately covers only the canonical integer helpers
    prevalent in generated Behavior sources.  Since the body, signature and
    evaluation order are checked in full, renaming either the function or its
    parameters does not matter; changing any operation falls back to ordinary
    source lowering.
    """

    if not isinstance(function, ast.FunctionDef):
        return None
    parameters = _plain_parameters(function)
    if parameters is None:
        return None
    value, width = parameters
    body = function.body
    if (len(body) == 1 and isinstance(body[0], ast.Return)
            and _unsigned_value(body[0].value, value, width)):
        return FixedWidthHelper("uwrap", "return")
    if len(body) < 2 or not _mask_assignment(body[0], value, width):
        return None
    if len(body) == 3:
        branch, returned = body[1:]
    else:
        branch, returned = None, None
    if (branch is not None and isinstance(branch, ast.If)
            and _sign_test(branch.test, value, width)
            and len(branch.body) == 1
            and _signed_assignment(branch.body[0], value, width)
            and not branch.orelse
            and isinstance(returned, ast.Return)
            and _name(returned.value, value)):
        return FixedWidthHelper("swrap", "if")
    returned = body[1] if len(body) == 2 else None
    if (isinstance(returned, ast.Return)
            and isinstance(returned.value, ast.IfExp)
            and _sign_test(returned.value.test, value, width)
            and _signed_value(returned.value.body, value, width)
            and _name(returned.value.orelse, value)):
        return FixedWidthHelper("swrap", "ifexp")
    return None


def names():
    """The source symbols authenticated by the primitive registry."""
    return primitives.source_intrinsic_names()


def declared_bindings(tree):
    """Valid intrinsic imports in ``tree``, including ones later shadowed.

    This is used only to diagnose a call through a shadowed binding.  The
    executable binding is computed by the module's ordered binding pass.
    """
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != MODULE:
            continue
        for alias in concrete_import_aliases(node):
            if alias.name in names():
                out[alias.asname or alias.name] = alias.name
    return out


def top_level_import(node, tree):
    return isinstance(tree, ast.Module) and node in tree.body
