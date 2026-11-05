"""Typed ranking certificates for source loops, not guessed trip caps.

An unconditional scalar update advances every continuing back-edge. An earlier
break/return leaves the loop. A while-body continue may bypass its update;
a for-body continue still executes the header step.
The real condition still runs on entry and after each body. An unchanged limit
can supply its exact entry expression; mutable limits keep their universal
typed cutoff. The lowerer proves invariance over condition, body and back-edge.
The caller proves comparison/update conversions cannot wrap at this entry.
Right-shifting a nonnegative word reaches zero within its typed bit width;
the source test must be false at zero, and still executes after every body.
"""

from dataclasses import dataclass

from ..symbolic import ir
from .source_integral import IntegralType, integral_type
from .source_loop_constants import _integral_interval
from .source_loops import loop_transfers


@dataclass(frozen=True, slots=True)
class LoopProgress:
    counter: dict
    delta: int
    cutoff: int
    minimum: int
    maximum: int
    limit: dict
    inclusive: bool

    def trips(self, start, limit=None, *, minimum=0):
        cutoff = ir.Lit(self.cutoff)
        if limit is not None:
            cutoff = (ir.Op('add', limit, ir.Lit(1 if self.delta > 0 else -1))
                      if self.inclusive else limit)
        distance = (ir.Op('sub', cutoff, start) if self.delta > 0
                    else ir.Op('sub', start, cutoff))
        rounded = ir.Op('fdiv', ir.Op('add', distance, ir.Lit(abs(self.delta)-1)),
                        ir.Lit(abs(self.delta)))
        return ir.Op('ite', ir.Op('gt', distance, ir.Lit(0)), rounded, ir.Lit(minimum))

    def in_domain(self, start, count):
        end = ir.Op('add', start, ir.Op('mul', count, ir.Lit(self.delta)))
        return ir._balanced_op('land', (
            ir.Op('ge', start, ir.Lit(self.minimum)), ir.Op('le', start, ir.Lit(self.maximum)),
            ir.Op('ge', end, ir.Lit(self.minimum)), ir.Op('le', end, ir.Lit(self.maximum))), ir.Lit(True))


@dataclass(frozen=True, slots=True)
class ShiftProgress:
    counter: dict
    amount: int
    minimum: int
    maximum: int
    limit: None = None

    def trips(self, start, limit=None, *, minimum=0):
        # Each proved update discards this many significant bits. This is a
        # type-derived certificate, not a runtime cap or a guessed iteration count.
        return ir.Lit(max(minimum, (max(0, self.maximum).bit_length()+self.amount-1)//self.amount))

    def in_domain(self, start, count):
        # Every later value is between zero and start. This also makes signed
        # arithmetic shift agree with division by a positive power of two.
        return ir.Op('land', ir.Op('ge', start, ir.Lit(max(0, self.minimum))),
                     ir.Op('le', start, ir.Lit(self.maximum)))


@dataclass(frozen=True, slots=True)
class ModuloProgress:
    counter: dict
    modulus: int
    delta: int
    minimum: int
    maximum: int
    limit: None = None

    def trips(self, start, limit=None, *, minimum=0):
        # Divisibility by a positive constant is independent of the sign rule
        # for remainder. Floor-mod gives the distance to the next zero residue
        # in either direction, including negative counters. A post-tested loop
        # beginning at a multiple needs a FULL period, not just one iteration.
        distance = ir.Op('fmod', ir.Op('mul', start, ir.Lit(-self.delta)),
                         ir.Lit(self.modulus))
        return (ir.Op('ite', ir.Op('eq', distance, ir.Lit(0)),
                      ir.Lit(self.modulus), distance) if minimum else distance)

    def in_domain(self, start, count):
        end = ir.Op('add', start, ir.Op('mul', count, ir.Lit(self.delta)))
        return ir._balanced_op('land', (
            ir.Op('ge', start, ir.Lit(self.minimum)), ir.Op('le', start, ir.Lit(self.maximum)),
            ir.Op('ge', end, ir.Lit(self.minimum)), ir.Op('le', end, ir.Lit(self.maximum))), ir.Lit(True))


def while_progress(node, symbols, *, bound_ranges=None):
    """Recognize a typed decreasing back-edge; effects are checked on IR."""
    return _condition_progress(node['cond'], node['body'], symbols, bound_ranges)


def _condition_progress(condition, body, symbols, bound_ranges=None):
    pending, reasons = [condition], []
    while pending:
        condition = pending.pop()
        if condition.get('kind') == 'BinaryOp' and condition.get('op') == 'LogicalAnd':
            # Every entered iteration satisfies both conjuncts. Either can
            # certify termination; the complete condition still executes.
            pending.extend((condition['right'], condition['left']))
            continue
        try:
            return _comparison_progress(condition, body, symbols, bound_ranges)
        except ValueError as exc:
            reasons.append(str(exc))
    raise ValueError('; '.join(dict.fromkeys(reasons)))


def for_progress(node, symbols, *, bound_ranges=None):
    """The for-step is unconditional on continuing back-edges, even continue.

    Reuse the same typed update/comparison proof as while. Initialization and
    body effects remain the lowerer's responsibility; they are not rewritten
    into a while body, where continue would have different semantics.
    """
    steps = {'kind': 'List', 'list': [
        {'kind': 'ExpressionStatement', 'expr': step} for step in node['steps']]}
    return _condition_progress(node['stopExpr'], steps, symbols, bound_ranges)


def _comparison_progress(condition, body, symbols, bound_ranges=None):
    domains = []

    def unwrap(expr):
        while True:
            kind = integral_type(expr['type'], symbols)
            domains.append(kind.interval)
            if expr.get('kind') == 'Call' and expr.get('subroutine') in {'$signed', '$unsigned'}:
                # These standard casts preserve a mathematical value only in
                # the intersection of operand and result carriers. Keep both
                # domains in the same entry proof used for ordinary casts.
                expr, = expr['arguments']
                continue
            if expr.get('kind') != 'Conversion':
                return expr
            if expr.get('conversionKind') == 'Propagated':
                source = integral_type(expr['operand']['type'], symbols)
                domains.append(IntegralType(source.width, kind.signed).interval)
            expr = expr['operand']

    op = condition.get('op')
    if condition.get('kind') != 'BinaryOp' or op not in {
            'LessThan', 'LessThanEqual', 'GreaterThan', 'GreaterThanEqual', 'Inequality'}:
        raise ValueError('loop needs a typed scalar ranking comparison')
    counter = unwrap(condition['left'])
    modulus = None
    if op == 'Inequality' and counter.get('kind') == 'BinaryOp' and counter.get('op') == 'Mod':
        low, high = _integral_interval(counter['right'], symbols)
        if low is None or low != high or low <= 0 \
                or _integral_interval(condition['right'], symbols) != (0, 0):
            raise ValueError('modulo ranking needs a positive constant divisor and zero residue exit')
        modulus = low
        counter = unwrap(counter['left'])
    if counter.get('kind') not in {'NamedValue', 'HierarchicalValue'}:
        raise ValueError('loop ranking needs a bound scalar counter')
    identity = counter['symbol'].split(' ', 1)[0]

    def is_counter(expr, *, reference=False):
        leaf = unwrap(expr)
        return ((reference and leaf.get('kind') == 'LValueReference')
                or (leaf.get('kind') in {'NamedValue', 'HierarchicalValue'}
                    and leaf['symbol'].split(' ', 1)[0] == identity))

    def linear_statements(node):
        if node.get('kind') == 'Block':
            yield from linear_statements(node['body'])
        elif node.get('kind') == 'List':
            for item in node['list']:
                yield from linear_statements(item)
        else:
            yield node

    statements = tuple(linear_statements(body))
    candidates = []
    for index, statement in enumerate(statements):
        expression = statement.get('expr', {}) if statement.get('kind') == 'ExpressionStatement' else {}
        target = expression.get('left', {}) if expression.get('kind') == 'Assignment' else (
            expression.get('operand', {}) if expression.get('kind') == 'UnaryOp' else {})
        if target.get('symbol', '').split(' ', 1)[0] == identity:
            candidates.append((index, expression))
    if len(candidates) != 1:
        raise ValueError('loop needs one unconditional scalar back-edge update')
    index, step = candidates[0]
    if 'Continue' in loop_transfers(statements[:index]):
        raise ValueError('while continue can bypass its proved back-edge update')
    delta = None
    if step.get('kind') == 'UnaryOp' and step.get('op') in {
            'Preincrement', 'Postincrement', 'Predecrement', 'Postdecrement'}:
        if is_counter(step['operand']):
            delta = 1 if step['op'].endswith('increment') else -1
    elif step.get('kind') == 'Assignment' and not step.get('isNonBlocking') \
            and is_counter(step['left']):
        value = unwrap(step['right'])
        if value.get('kind') == 'BinaryOp' and value.get('op') in {
                'LogicalShiftRight', 'ArithmeticShiftRight'} \
                and is_counter(value['left'], reference=bool(step.get('op'))):
            low, high = _integral_interval(condition['right'], symbols, symbol_ranges=bound_ranges)
            exits_at_zero = ((op == 'GreaterThan' and low is not None and low >= 0)
                             or (op == 'GreaterThanEqual' and low is not None and low > 0)
                             or (op == 'Inequality' and low == high == 0))
            shift_low, shift_high = _integral_interval(value['right'], symbols)
            if not exits_at_zero or shift_low is None or shift_low != shift_high or shift_low <= 0:
                raise ValueError('right-shift ranking needs a positive constant shift and exit at zero')
            return ShiftProgress(counter, shift_low,
                                 max(low for low, _ in domains), min(high for _, high in domains))
        if value.get('kind') == 'BinaryOp' and value.get('op') in {'Add', 'Subtract'} \
                and is_counter(value['left'], reference=bool(step.get('op'))):
            lo, hi = _integral_interval(value['right'], symbols)
            if lo == hi and lo is not None:
                delta = lo if value['op'] == 'Add' else -lo
    if modulus is not None:
        if delta not in {-1, 1}:
            raise ValueError('modulo ranking needs a unit scalar back-edge update')
        return ModuloProgress(counter, modulus, delta,
                              max(low for low, _ in domains), min(high for _, high in domains))
    if op == 'Inequality':
        raise ValueError('loop needs a typed scalar ranking comparison')
    ascending = op in {'LessThan', 'LessThanEqual'}
    if not delta or (delta > 0) != ascending:
        raise ValueError('loop back-edge must advance toward its bound')
    low, high = _integral_interval(condition['right'], symbols, symbol_ranges=bound_ranges)
    cutoff = high if ascending else low
    if cutoff is None:
        raise ValueError('loop has no finite typed cutoff')
    cutoff += 1 if op == 'LessThanEqual' else -1 if op == 'GreaterThanEqual' else 0
    return LoopProgress(counter, delta, cutoff,
                         max(low for low, _ in domains), min(high for _, high in domains),
                         condition['right'], op in {'LessThanEqual', 'GreaterThanEqual'})
