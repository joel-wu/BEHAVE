"""Exact ordering of integers and literal positive/negative infinity.

This is a tag/payload carrier, not floating-point arithmetic and not a finite
stand-in for infinity. Unsupported arithmetic or escaping values stay refused.
Conversion/comparison belong to the value; source discovery and local binding
use the explicit lowerer context and its invocation-local cache.
"""
import ast
import math
from dataclasses import dataclass

from ..symbolic import ir, typecheck as value_types
from .extract import LoweringError, _walk_function_scope
from ..symbolic.value_contract import SCALAR


def infinity_literal(node):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'float' and not node.keywords and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str):
        return None
    try:
        value = float(node.args[0].value)
    except ValueError:
        return None
    return (1 if value > 0 else -1) if math.isinf(value) else None


@dataclass(frozen=True)
class OrderedScalar:
    infinity: object  # -1, 0, +1; payload is live exactly for 0.
    payload: object

    @staticmethod
    def coerce(low, value, source):
        if isinstance(value, OrderedScalar):
            return value
        kind = low._source_scalar_type_for_value(source, value)
        if low._shape(value) or kind not in (value_types.INTEGER, value_types.BOOLEAN):
            raise LoweringError(source, 'infinity ordering requires a proved integer operand',
                                code='ordered-scalar-operand-unsupported')
        if kind == value_types.BOOLEAN:
            value = ir.Op('toint', value)
        return OrderedScalar(ir.Lit(0), value)

    @staticmethod
    def compare(low, op, left, right, left_source, right_source):
        left = OrderedScalar.coerce(low, left, left_source)
        right = OrderedScalar.coerce(low, right, right_source)
        same_tag = ir.Op('eq', left.infinity, right.infinity)
        finite = ir.Op('eq', left.infinity, ir.Lit(0))
        equal = ir.Op('land', same_tag, ir.Op('lor', ir.Op('not', finite),
                                             ir.Op('eq', left.payload, right.payload)))
        less = ir.Op('lor', ir.Op('lt', left.infinity, right.infinity),
                     ir.Op('land', same_tag, ir.Op('land', finite,
                                                  ir.Op('lt', left.payload, right.payload))))
        return {'eq': equal, 'ne': ir.Op('not', equal), 'lt': less,
                'le': ir.Op('lor', less, equal), 'gt': ir.Op('not', ir.Op('lor', less, equal)),
                'ge': ir.Op('not', less)}[op]


def is_local(low, spelling):
    # Standalone statement lowering has no enclosing source declaration.
    if low.activation.scope is None:
        return False
    scope = low.activation.scope
    if scope not in low.ordered_locals:
        dependencies = {}
        seeds = set()
        def alternatives(value):
            if isinstance(value, ast.IfExp):
                return (*alternatives(value.body), *alternatives(value.orelse))
            if isinstance(value, ast.BoolOp):
                return tuple(part for item in value.values for part in alternatives(item))
            if isinstance(value, ast.UnaryOp) and isinstance(value.op, (ast.UAdd, ast.USub)):
                return alternatives(value.operand)
            return (value,)
        for node in _walk_function_scope(scope):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                nodes = alternatives(node.value)
                if (low._spelling_is_builtin('float')
                        and any(infinity_literal(item) is not None for item in nodes)):
                    seeds.add(target.id)
                # A direct alias also keeps the tag. Arithmetic containing
                # an infinity remains a separately diagnosed operation.
                dependencies.setdefault(target.id, set()).update(
                    item.id for item in nodes if isinstance(item, ast.Name))
        while True:
            expanded = seeds | {name for name, used in dependencies.items() if used & seeds}
            if expanded == seeds:
                break
            seeds = expanded
        low.ordered_locals[scope] = seeds
    return spelling in low.ordered_locals[scope]


def bind(low, target, source):
    value = low._capture(OrderedScalar.coerce(low, low.expr(source), source))
    name = low._name(target.id)
    if low.storage.carrier(OrderedScalar, name) is None:
        tag, payload = low.storage.fresh(), low.storage.fresh()
        for variable in (tag, payload):
            low.storage.require_shape(variable, (), target)
            low.flow.contracts[variable] = SCALAR
            low._remember_source_scalar_type(variable, value_types.INTEGER)
        low.storage.bind_carrier(name, OrderedScalar(ir.Var(tag), ir.Var(payload)))
    destination = low.storage.require_carrier(OrderedScalar, name)
    low.flow.contracts[name] = SCALAR
    # Neither an integer range nor a source integer type can describe the
    # whole value; ranges belong to its two physical leaves instead.
    low.source_scalar_types.pop(name, None)
    commands = []
    for variable, expression in ((destination.infinity, value.infinity),
                                 (destination.payload, value.payload)):
        low._record_range(variable.name, expression)
        commands.append(ir.Assign(variable.name, expression))
    return low._flush(ir.Seq(*commands))
