"""Source call shapes shared by planning, lowering, and static audits.

Recognising a source operation is an authority boundary: every consumer must
agree on the arguments that operation evaluates.  These predicates deliberately
describe syntax only; lexical resolution remains the resolver's job.
"""

from __future__ import annotations

import ast

__all__ = [
    "CallBindingError",
    "bind_helper_arguments",
    "has_plain_positional_arity",
    "helper_parameters",
    "non_rebound_parameters",
    "is_append_call",
    "is_extend_call",
    "is_plain_append_call",
    "is_plain_extend_call",
    "is_plain_named_call",
    "is_plain_pop_call",
    "is_pop_call",
    "record_copy_source",
    "record_items",
    "record_key",
    "record_update_items",
    "resolve_helper",
    "sequence_delete_target",
    "sequence_method_target",
]


def record_items(node, *, is_builtin=lambda _name: False):
    """Field syntax of a fresh literal or a resolved keyword ``dict`` call.

    This is a view, not an AST rewrite: payload nodes retain their source
    identity and order. Keyword names are plain string metadata, never synthetic
    AST nodes that could be confused with source occurrences by planning caches.
    Positional record copies use ``record_copy_source`` and its separate
    schema/alias semantics; unpacking is not a keyword-only constructor.
    """
    if isinstance(node, ast.Dict):
        return tuple(zip(node.keys, node.values))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "dict" and not node.args
            and all(item.arg is not None for item in node.keywords)
            and len({item.arg for item in node.keywords}) == len(node.keywords)
            and is_builtin("dict")):
        return tuple((item.arg, item.value)
                     for item in node.keywords)
    return None


def record_key(key):
    """Literal field spelling, leaving computed/unpacked keys unresolved."""
    if type(key) is str:
        return key
    if isinstance(key, ast.Constant) and type(key.value) is str:
        return key.value
    return None


def record_copy_source(node, *, is_builtin=lambda _name: False):
    """Operand of a resolved, one-argument ``dict(mapping)`` shallow copy.

    The value planner must still prove a finite record schema. Iterable pairs,
    mapping unpacking and keyword overrides are not implied by this syntax.
    """
    if is_plain_named_call(node, "dict", 1) and is_builtin("dict"):
        return node.args[0]
    return None


def record_update_items(node):
    """Keyword writes to a local mapping, pending proof of its record layout.

    Keep the original keyword/value nodes and evaluation order. Positional
    mappings and unpacking require separate schema and alias semantics.
    """
    if (sequence_method_target(node, "update") is not None
            and isinstance(node.func.value, ast.Name) and not node.args
            and all(item.arg is not None for item in node.keywords)
            and len({item.arg for item in node.keywords}) == len(node.keywords)):
        return tuple(node.keywords)
    return None


class CallBindingError(ValueError):
    """A call outside the fixed source signature, not a guessed binding."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def helper_parameters(function, *, receiver=False):
    """Formal order shared by source summaries and retained IR activations."""
    args = function.args
    positional = (*args.posonlyargs, *args.args)
    return tuple(arg.arg for arg in
                 (*positional[int(receiver):], *args.kwonlyargs))


def non_rebound_parameters(function, *, receiver=False):
    """Parameters whose lexical binding remains the incoming object."""
    rebound = {node.id for node in ast.walk(function)
               if isinstance(node, ast.Name)
               and isinstance(node.ctx, (ast.Store, ast.Del))}
    return frozenset(helper_parameters(function, receiver=receiver)) - rebound


def resolve_helper(resolver, scope, call):
    """Ask the lexical resolver for the ordinary helper this call names."""
    if isinstance(call.func, ast.Name):
        kind, target = resolver.resolve_call(scope, call.func.id)
        return target if kind in ("nested", "module", "alias") else None
    if (isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"):
        kind, target = resolver.resolve_method(scope, call.func.attr)
        return target if kind == "method" else None
    return None


def bind_helper_arguments(function, positional, keywords, *, receiver=False,
                          default):
    """Bind already evaluated arguments, without reordering their execution.

    Values are opaque to this syntactic binder. The two readers supply their
    own value/contract representation and use the same definition-time default
    authority. Fixed starred positional products are expanded by the reader;
    dynamic variadics and mapping unpacking are not silently approximated.
    """
    args = function.args
    if args.vararg is not None or args.kwarg is not None:
        raise CallBindingError("variadic helper parameters are not represented",
                               "call-signature")
    all_positional = (*args.posonlyargs, *args.args)
    names = [arg.arg for arg in all_positional[int(receiver):]]
    params = helper_parameters(function, receiver=receiver)
    if len(positional) > len(names):
        raise CallBindingError("too many positional helper arguments", "call-arity")
    bound = dict(zip(names, positional))
    positional_only = {arg.arg for arg in args.posonlyargs}
    for name, value in keywords:
        if name is None or name not in params or name in positional_only:
            raise CallBindingError("unknown, positional-only or unpacked helper keyword",
                                   "call-argument-form")
        if name in bound:
            raise CallBindingError("multiple values for helper argument " + name,
                                   "call-argument-form")
        bound[name] = value
    defaults = dict(zip(
        (arg.arg for arg in all_positional[len(all_positional) - len(args.defaults):]),
        args.defaults))
    defaults.update((arg.arg, value) for arg, value in
                    zip(args.kwonlyargs, args.kw_defaults) if value is not None)
    for name in params:
        if name not in bound:
            if name not in defaults:
                raise CallBindingError("missing helper argument " + name, "call-arity")
            bound[name] = default(name, defaults[name])
    return params, tuple(bound[name] for name in params)


def has_plain_positional_arity(node, minimum, maximum=None):
    """Whether a call has only the stated number of positional arguments."""
    if not isinstance(node, ast.Call) or node.keywords \
            or any(isinstance(argument, ast.Starred) for argument in node.args):
        return False
    maximum = minimum if maximum is None else maximum
    return minimum <= len(node.args) <= maximum


def is_plain_named_call(node, name, minimum, maximum=None):
    """A direct call to ``name`` with a plain positional source signature."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
            and has_plain_positional_arity(node, minimum, maximum))


def sequence_method_target(node, method):
    """Return the finite-sequence binding targeted by one method call.

    The represented mutable sequences are lexical locals and direct model
    fields.  Returning ``("local", name)`` or ``("state", name)`` here keeps
    planning, alias analysis and retained lowering on exactly that syntax
    boundary; a call through any computed receiver remains an ordinary opaque
    method call.
    """
    if (not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != method):
        return None
    return _sequence_binding(node.func.value)


def sequence_delete_target(node):
    """Binding of an indexed list deletion; slices need a different transfer."""
    if (not isinstance(node, ast.Subscript) or not isinstance(node.ctx, ast.Del)
            or isinstance(node.slice, ast.Slice)):
        return None
    return _sequence_binding(node.value)


def _sequence_binding(receiver):
    # ``self.method`` is resolved as a model helper, not as a collection
    # operation. Direct fields (``self.rows.pop()``) are handled below.
    if isinstance(receiver, ast.Name) and receiver.id != "self":
        return "local", receiver.id
    if (isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == "self"):
        return "state", receiver.attr
    return None


def is_append_call(node):
    """Whether ``node`` targets a represented sequence with ``append``."""
    return sequence_method_target(node, "append") is not None


def is_plain_append_call(node):
    """The one append form represented by the finite-sequence IR."""
    return (is_append_call(node)
            and has_plain_positional_arity(node, 1))


def is_pop_call(node):
    """Whether ``node`` targets a represented sequence with ``pop``."""
    return sequence_method_target(node, "pop") is not None


def is_extend_call(node):
    """Whether ``node`` targets a represented sequence with ``extend``."""
    return sequence_method_target(node, "extend") is not None


def is_plain_extend_call(node):
    """A list extension with one positional iterable."""
    return is_extend_call(node) and has_plain_positional_arity(node, 1)


def is_plain_pop_call(node):
    """A positional ``pop`` with its optional index and no other syntax."""
    return (is_pop_call(node)
            and has_plain_positional_arity(node, 0, 1))
