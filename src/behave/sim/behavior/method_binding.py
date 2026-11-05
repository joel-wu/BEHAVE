"""Standard Python method descriptors shared by source and runtime readers.

This identifies the built-in descriptor forms, not arbitrary decorator calls.
The source reader supplies definition-time shadowing; concrete Python performs
the actual binding, so no candidate method is rewritten into another method.
"""

from __future__ import annotations

import ast
import types
from collections.abc import Collection
from typing import Literal

METHOD_DECORATORS = frozenset({"staticmethod", "classmethod", "property"})
PROPERTY_ACCESSORS = {"property": "fget", "property-getter": "fget",
                      "property-setter": "fset", "property-deleter": "fdel"}


def property_accessors(node: ast.Attribute, parent: ast.AST | None) -> tuple[str, ...]:
    """Descriptor operations performed by one source attribute occurrence."""
    if isinstance(parent, ast.AugAssign) and parent.target is node:
        return ("fget", "fset")
    return ({ast.Load: "fget", ast.Store: "fset", ast.Del: "fdel"}[type(node.ctx)],)


def class_member_name(class_name: str, name: str) -> str:
    """Python's runtime spelling of a name compiled in this class body.

    Source sites retain their original spelling. Only namespace lookups use
    the mangled key; changing the source would change its event identities.
    """
    owner = class_name.lstrip("_")
    if owner and name.startswith("__") and not name.endswith("__") and "." not in name:
        return f"_{owner}{name}"
    return name


def method_kind(
    function: ast.FunctionDef, *, shadowed: Collection[str] = (),
) -> Literal["instance", "staticmethod", "classmethod", "property",
             "property-getter", "property-setter", "property-deleter"] | None:
    if not function.decorator_list:
        return "instance"
    if len(function.decorator_list) == 1:
        decorator = function.decorator_list[0]
        if (isinstance(decorator, ast.Name) and decorator.id in METHOD_DECORATORS
                and decorator.id not in shadowed):
            return decorator.id
        if (isinstance(decorator, ast.Attribute) and isinstance(decorator.value, ast.Name)
                and decorator.value.id == function.name
                and decorator.attr in ("getter", "setter", "deleter")):
            return "property-" + decorator.attr
    return None


def method_parameters(function: ast.FunctionDef) -> tuple[str, ...]:
    """Positional arguments exposed by accessing an admitted method on an instance."""
    skip = 0 if method_kind(function) == "staticmethod" else 1
    return tuple(argument.arg for argument in function.args.args[skip:])


def descriptor_function(value: object) -> types.FunctionType | None:
    """Unwrap only exact built-in descriptors, without invoking attribute binding."""
    if type(value) in (staticmethod, classmethod):
        value = value.__func__
    return value if type(value) is types.FunctionType else None
