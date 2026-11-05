"""Read, enumerate and pin complete finite symbolic state images.

Images have string-keyed records, fixed-arity tuples and Boolean/integer/packed
bit-vector leaves. The same layout is used for audit and local-query boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import z3
from z3 import z3core as native
from z3.z3 import _to_expr_ref
from z3.z3core import Z3_substitute
from z3.z3types import Ast


def physical_bits(value, width):
    """Project a signed mathematical IR carrier to compiler-owned HDL bits."""
    if value.size() < width:
        return z3.SignExt(width - value.size(), value)
    return value if value.size() == width else z3.Extract(width - 1, 0, value)


def assertion_conjunction(assertions, *, check=None):
    """Conjoin encoder-owned Boolean ASTs without per-element Python coercion."""
    return _boolean_junction(assertions, native.Z3_mk_and, check=check)


def assertion_disjunction(assertions, *, check=None):
    """Disjoin every Boolean observation, retaining order and duplicates."""
    return _boolean_junction(assertions, native.Z3_mk_or, check=check)


def _boolean_junction(assertions, constructor, *, check):
    if check is None:
        assertions = tuple(assertions)
    else:
        owned = []
        for term in assertions:
            check()
            owned.append(term)
        assertions = tuple(owned)
    context = assertions[0].ctx if assertions else z3.main_ctx()
    if any(not isinstance(term, z3.BoolRef) or term.ctx != context for term in assertions):
        raise ValueError('Boolean assertions must share one Z3 context')
    terms = (Ast * len(assertions))(*(term.as_ast() for term in assertions))
    result = z3.BoolRef(constructor(context.ref(), len(assertions), terms), context)
    if check is not None:
        check()
    return result


def scalar_difference(term, value, literals):
    """Build the same disequality with projection-local typed literal sharing.

    Only Python scalar literals are shared, never observations or answers.
    Non-scalar arguments keep Z3's ordinary coercion path. Context, sort and
    Python type belong to the key (in particular, True is not integer 1).
    """
    if type(value) not in (int, bool):
        return term != value
    sort = term.sort()
    key = (term.ctx, sort.get_id(), type(value), value)
    literal = literals.get(key)
    if literal is None:
        literal = sort.cast(value)
        literals[key] = literal
    terms = (Ast * 2)(term.as_ast(), literal.as_ast())
    return z3.BoolRef(native.Z3_mk_distinct(term.ctx_ref(), 2, terms), term.ctx)


def _scalar_replacements(equations):
    replacements = []
    for constraint in equations:
        if not z3.is_eq(constraint):
            continue
        left, right = constraint.children()
        # Z3's numeral subclasses can reverse ``atom == IntVal/BitVecVal``.
        # Equality orientation is not part of the request's semantics.
        if (z3.is_int_value(left) or z3.is_bv_value(left)
                or z3.is_true(left) or z3.is_false(left)):
            left, right = right, left
        if z3.is_const(left) and left.decl().kind() == z3.Z3_OP_UNINTERPRETED and (
                z3.is_int_value(right) or z3.is_bv_value(right)
                or z3.is_true(right) or z3.is_false(right)):
            replacements.append((left, right))
    return tuple(replacements)


def _literal(term):
    ctx, ast = term.ctx_ref(), term.as_ast()
    kind = native.Z3_get_ast_kind(ctx, ast)
    if kind == z3.Z3_NUMERAL_AST:
        return native.Z3_get_sort_kind(ctx, native.Z3_get_sort(ctx, ast)) in (
            z3.Z3_INT_SORT, z3.Z3_BV_SORT)
    return kind == z3.Z3_APP_AST and native.Z3_get_decl_kind(
        ctx, native.Z3_get_app_decl(ctx, ast)) in (z3.Z3_OP_TRUE, z3.Z3_OP_FALSE)


def _literal_bindings(equations, *, check=None):
    """Exact literal facts, including compound reads and conflicting equations."""
    facts = []
    for equation in equations:
        if check is not None:
            check()
        if not z3.is_eq(equation):
            continue
        left, right = equation.children()
        if _literal(left):
            left, right = right, left
        if _literal(right):
            facts.append((z3.simplify(left), right))
    return tuple(facts)


def fold_fixed_terms(terms, bindings, *, check=None):
    """Substitute a shared DAG once, preserving every typed root and its order.

    This is expression rewriting, not an assertion or proof. The caller owns
    the binding premises. A fresh, unasserted application keeps false roots
    and duplicates intact while Z3 shares substitution and simplification.
    """
    terms = tuple(terms)
    if check is not None:
        check()
    if not terms:
        return ()
    pack = z3.FreshFunction(*(term.sort() for term in terms), z3.BoolSort(ctx=terms[0].ctx))
    image = z3.simplify(z3.substitute(pack(*terms), *bindings))
    if check is not None:
        check()
    return tuple(image.children())


@dataclass(frozen=True, slots=True)
class FixedBindings:
    """Immutable equations and their exact scalar substitutions, not answers.

    A bound seed owns its schedule/queue groups; a query adds a fresh boundary.
    Parsing a retained equation again cannot change its substitution, so groups
    can be combined without repeatedly traversing their Z3 wrappers. Native
    argument vectors and normalized literal facts reuse that representation,
    not query answers or domains. Compound read equalities participate in
    reduction but are not mistaken for independent scalar substitutions.
    """

    equations: tuple
    replacements: tuple
    literal_bindings: tuple | None = field(default=None, repr=False)
    _context: object = field(init=False, repr=False, compare=False)
    _sources: object = field(init=False, repr=False, compare=False)
    _values: object = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if self.literal_bindings is None:
            object.__setattr__(self, 'literal_bindings', _literal_bindings(self.equations))
        # These typed pairs came from Z3 equalities. Keep their native argument
        # arrays with their owning ExprRefs, rather than re-checking every sort
        # and marshalling every pair for both the target and complete formula.
        context = self.replacements[0][0].ctx if self.replacements else None
        if any(left.ctx != context or right.ctx != context
               for left, right in self.replacements):
            raise ValueError("fixed substitutions must share one Z3 context")
        object.__setattr__(self, '_context', context)
        array = Ast * len(self.replacements)
        object.__setattr__(self, '_sources', array(*(left.as_ast() for left, _ in self.replacements)))
        object.__setattr__(self, '_values', array(*(right.as_ast() for _, right in self.replacements)))

    def substitute(self, term):
        """Apply every pair, in order, using Z3's ordinary substitution engine."""
        if not self.replacements:
            return term
        if term.ctx != self._context:
            raise ValueError("fixed substitutions and target must share one Z3 context")
        return _to_expr_ref(Z3_substitute(term.ctx_ref(), term.as_ast(), len(self.replacements),
                                          self._sources, self._values), term.ctx)

    @classmethod
    def of(cls, equations):
        equations = tuple(equations)
        return cls(equations, _scalar_replacements(equations))

    @classmethod
    def combine(cls, groups):
        groups = tuple(groups)
        return cls(tuple(eq for group in groups for eq in group.equations),
                   tuple(pair for group in groups for pair in group.replacements),
                   tuple(pair for group in groups for pair in group.literal_bindings))


def read_component(model, term):
    """Read a scalar without erasing its Boolean sort."""
    value = model.eval(term, model_completion=True)
    return z3.is_true(value) if z3.is_bool(term) else value.as_long()


def equal_component(term, value, where):
    """Exact equality; malformed values must not be coerced or truncated."""
    if z3.is_bool(term):
        if type(value) not in (bool, int) or value not in (False, True, 0, 1):
            raise TypeError(f"{where} is not a Boolean value")
        return term == z3.BoolVal(bool(value))
    if z3.is_int(term):
        if type(value) is not int:
            raise TypeError(f"{where} is not an exact integer")
        return term == z3.IntVal(value)
    if z3.is_bv(term):
        if type(value) is not int:
            raise TypeError(f"{where} is not an exact bit-vector value")
        if not 0 <= value < 1 << term.size():
            raise ValueError(f"{where} is outside its packed bit-vector range")
        return term == z3.BitVecVal(value, term.size())
    raise TypeError(f"{where} has unsupported symbolic sort {term.sort()}")


def state_mapping(value, where):
    """Accept an ordinary record or its lossless sequence-of-pairs transport."""
    if type(value) is dict and all(type(name) is str for name in value):
        return dict(value)
    if type(value) in (tuple, list):
        items = tuple(value)
        if all(type(item) in (tuple, list) and len(item) == 2
               and type(item[0]) is str for item in items):
            names = tuple(item[0] for item in items)
            if len(set(names)) != len(names):
                raise ValueError(f"{where} names one state field more than once")
            return {name: child for name, child in items}
    raise TypeError(f"{where} is not a finite string-keyed mapping")


def image_constraints(symbolic, concrete, where):
    """Pin every scalar, rejecting missing/extra fields and arity mismatches."""
    if type(symbolic) is dict:
        concrete = state_mapping(concrete, where)
        if set(concrete) != set(symbolic):
            raise ValueError(f"{where} has different state fields")
        return [term for name, value in symbolic.items()
                for term in image_constraints(value, concrete[name], f"{where}.{name}")]
    if type(symbolic) is tuple:
        if type(concrete) not in (tuple, list) or len(concrete) != len(symbolic):
            raise ValueError(f"{where} has different finite arity")
        return [term for index, (left, right) in enumerate(zip(symbolic, concrete))
                for term in image_constraints(left, right, f"{where}[{index}]")]
    return [equal_component(symbolic, concrete, where)]


def image_terms(image):
    """Enumerate exactly the leaves audited and fixed by this state layout."""
    if type(image) is dict:
        for name in sorted(image):
            yield from image_terms(image[name])
    elif type(image) is tuple:
        for value in image:
            yield from image_terms(value)
    elif isinstance(image, z3.ExprRef):
        yield image
    else:
        raise TypeError("a symbolic state image contains only records, tuples and terms")
