"""Explicit fixed-width operations available to Behavior source models.

Use only direct imports, for example::

    from behave_intrinsics import ssat, uwrap, slt

The frontend binds these spellings to the immutable primitive registry.  This
module is the native reading of the same contract; ordinary Python arithmetic
remains mathematical and never wraps implicitly.
"""

from behave.sim.symbolic import primitives as _primitives


def _apply(symbol, arguments):
    spec = _primitives.source_intrinsic(symbol)
    terms = spec.signature.terms_for(len(arguments))
    if terms is None:
        raise TypeError(
            f"{symbol} takes {len(spec.signature.arguments)} arguments, "
            f"not {len(arguments)}")
    for term, value in zip(terms, arguments):
        if term in (_primitives.INTEGER, _primitives.WIDTH,
                    _primitives.SHIFT) and type(value) is not int:
            raise TypeError(f"{symbol} requires integer arguments")
        if term in (_primitives.WIDTH, _primitives.SHIFT):
            error = _primitives.argument_domain_error(
                term, literal=True, value=value)
            if error is not None:
                raise ValueError(f"{symbol} {error}")
    return spec.denotation(*arguments)


def uwrap(value, width):
    return _apply("uwrap", (value, width))


def swrap(value, width):
    return _apply("swrap", (value, width))


def usat(value, width):
    return _apply("usat", (value, width))


def ssat(value, width):
    return _apply("ssat", (value, width))


def ushl(value, amount, width):
    return _apply("ushl", (value, amount, width))


def sshl(value, amount, width):
    return _apply("sshl", (value, amount, width))


def ulshr(value, amount, width):
    return _apply("ulshr", (value, amount, width))


def sashr(value, amount, width):
    return _apply("sashr", (value, amount, width))


def ult(left, right, width):
    return _apply("ult", (left, right, width))


def ule(left, right, width):
    return _apply("ule", (left, right, width))


def ugt(left, right, width):
    return _apply("ugt", (left, right, width))


def uge(left, right, width):
    return _apply("uge", (left, right, width))


def slt(left, right, width):
    return _apply("slt", (left, right, width))


def sle(left, right, width):
    return _apply("sle", (left, right, width))


def sgt(left, right, width):
    return _apply("sgt", (left, right, width))


def sge(left, right, width):
    return _apply("sge", (left, right, width))


__all__ = tuple(sorted(_primitives.source_intrinsic_names()))

# A registry addition must deliberately add its native boundary function too.
if set(__all__) != {
        "uwrap", "swrap", "usat", "ssat",
        "ushl", "sshl", "ulshr", "sashr",
        "ult", "ule", "ugt", "uge", "slt", "sle", "sgt", "sge"}:
    raise RuntimeError(
        "the Behavior intrinsic module and primitive registry disagree")
