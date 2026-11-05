"""Finite procedural-loop proofs for consumers that need static coordinates.

This is deliberately not an HDL constant evaluator. Admission proves a typed
counter, constant bounds, a monotone step, no wraparound, and no writes or
dispatch in the body that can invalidate that enumeration. Source-native
callbacks do not require this proof: they record actual dynamic loop entries.
Compiled-coordinate consumers require constant ranges. Symbolic lowering uses
typed ranking proofs for dynamic headers, retaining each original runtime test.
"""

from bisect import bisect_left
from dataclasses import dataclass, replace
from functools import cached_property
from math import gcd

from behave.core import runtime_defaults as defaults

from .source_syntax import LOOP_STATEMENTS, walk

MAX_EVENTS = defaults.SIM_RTL_SOURCE_EVENTS
_MIN, _MAX = -(1 << 31), (1 << 31) - 1


def loop_transfers(node):
    """Typed transfers owned by this body, excluding nested loop scopes."""
    if isinstance(node, (list, tuple)):
        return set().union(*(loop_transfers(item) for item in node))
    if not isinstance(node, dict):
        return set()
    kind = node.get('kind')
    if kind in {'ForLoop', 'WhileLoop', 'DoWhileLoop', 'RepeatLoop', 'ForeachLoop', 'ForeverLoop'}:
        return set()
    if kind in {'Break', 'Continue'}:
        return {kind}
    return set().union(*(loop_transfers(value) for key, value in node.items() if key != 'type'))


class LoopProofs:
    """Shared finite-header proof inputs, independent of native callback expansion.

    A native source plan only retains these compiler-bound descriptions. Exact
    consumers request proofs later; neither their absence nor their cost changes
    the source catalogue or stops baseline observation.
    """

    def __init__(self, spans, headers, frame_local_calls, event_spans):
        self.spans, self._header_factory = spans, headers
        self.frame_local_calls, self.event_spans = frame_local_calls, event_spans
        self.nodes = {}
        self.proven = {}

    @cached_property
    def headers(self):
        return self._header_factory()

    def add(self, node, scopes):
        self.nodes[self.spans[id(node)]] = node, scopes

    def prove(self, span):
        if span not in self.proven:
            node, scopes = self.nodes[span]
            loop = static_loop(node, scopes, span, spans=self.spans,
                frame_local_calls=self.frame_local_calls,
                headers=None if self.headers is None else self.headers.get(span, ()))
            condition_events = False
            if "stopExpr" in node:
                start, end = self.spans[id(node["stopExpr"])]
                first = bisect_left(self.event_spans, (start,))
                last = bisect_left(self.event_spans, (end,))
                condition_events = any(right <= end for _, right in self.event_spans[first:last])
            self.proven[span] = replace(loop, condition_events=condition_events)
        return self.proven[span]

def _name(node):
    if type(node) is dict and node.get("kind") == "IdentifierName":
        token = node.get("identifier", {})
        if token.get("kind") == "Identifier" and not token.get("text", "").startswith("\\"):
            return token.get("text")
    return None


def _literal(node):
    kind = node.get("kind")
    if kind == "IntegerLiteralExpression":
        text = node["literal"]["text"].replace("_", "")
        if text.isascii() and text.isdecimal():
            value = int(text)
            if value <= _MAX:
                return value
    if kind == "UnaryMinusExpression":
        value = _literal(node["operand"])
        if value is not None:
            return -value
    return None


def _targets(node):
    if type(node) is not dict:
        return set()
    if node.get("kind") in {"IdentifierName", "IdentifierSelectName"}:
        return {node.get("identifier", {}).get("text")}
    if node.get("kind") == "ParenthesizedExpression":
        return _targets(node.get("expression"))
    if node.get("kind") == "ConcatenationExpression":
        return set().union(*(_targets(part) for part in node.get("expressions", [])))
    # Unsupported lvalues cannot establish absence of a counter write.
    return {part["text"] for part in walk(node) if type(part) is dict
            and part.get("kind") == "Identifier"}


@dataclass(frozen=True, slots=True)
class StaticLoop:
    start: int
    end: int
    counter: str
    # Collector coordinate grid across selected elaborations, NOT an execution
    # count when variants exist. Extra grid cells are never synthetic hits.
    values: range
    variants: tuple[tuple[str, range], ...] = ()
    condition_events: bool = False

    @property
    def context(self):
        return ("ForLoopStatement", self.start, self.end)

    def execution_values(self, key):
        if not self.variants:
            return self.values
        for identity, values in self.variants:
            if identity == key:
                return values
        raise ValueError("source loop has no proof for this bound compiler header")

    @property
    def condition_values(self):
        """Body coordinates plus the final test, including a zero-trip entry."""
        ranges = tuple(values for _, values in self.variants) or (self.values,)
        return _coordinate_grid(tuple(range(values.start,
            values.start + (len(values) + 1) * values.step, values.step) for values in ranges))


def static_loop(node, scopes, span, *, spans=None, frame_local_calls=(), headers=None):
    """Share native coordinates while retaining each elaboration's exact range."""
    options = {"spans": spans, "frame_local_calls": frame_local_calls}
    if headers is None:
        return _static_loop(node, scopes, span, **options)
    if not headers or any(header is None for header in headers):
        raise ValueError("native loop has no resolved constant-header facts")
    resolved = [(_static_loop(node, scopes, span, header=header, **options), header.key)
                for header in headers]
    variants = tuple(sorted((key, loop.values) for loop, key in resolved))
    grid = _coordinate_grid(tuple(values for _, values in variants))
    return replace(resolved[0][0], values=grid, variants=variants)


def _coordinate_grid(ranges):
    grids = set(ranges)
    nonempty = [values for values in grids if values]
    if len(grids) == 1:
        grid = next(iter(grids))
    elif not nonempty:
        grid = range(0)
    else:
        low = min(min(values[0], values[-1]) for values in nonempty)
        high = max(max(values[0], values[-1]) for values in nonempty)
        stride = 0
        for values in nonempty:
            stride = gcd(stride, gcd(abs(values.step), abs(values.start - low)))
        grid = range(low, high + 1, stride)
    if len(grid) > MAX_EVENTS:
        raise ValueError("native loop coordinate grid exceeds the source-event budget")
    return grid


def invocation_index(first, loops, counters):
    """Encode the existing Cartesian-product order without an enumerated mux.

    Each coordinate is widened with its declared signedness before subtraction; descending
    and strided ranges use the same mixed-radix index. Invalid / X coordinates
    keep the collector's fault ID, exactly like the former case default.
    """
    if any(not loop.values for loop in loops):
        return "32'hffffffff"
    checks, index = [], "64'd0"
    for loop, counter in zip(loops, counters, strict=True):
        values = loop.values
        start = f"64'sd{values.start}" if values.start >= 0 else f"(-64'sd{-values.start})"
        value = f"longint'({counter})"
        distance = f"({value} - {start})" if values.step > 0 else f"({start} - {value})"
        step = abs(values.step)
        checks.append(f"({distance} >= 64'sd0 && {distance} <= 64'sd{abs(values[-1] - values.start)}"
                      f" && {distance} % 64'sd{step} == 0)")
        index = f"({index} * 64'sd{len(values)} + {distance} / 64'sd{step})"
    return f"((({' && '.join(checks)}) === 1'b1) ? int'(64'd{first} + {index}) : 32'hffffffff)"


def _static_loop(node, scopes, span, *, spans=None, frame_local_calls=(), header=None) -> StaticLoop:
    """Prove one constant for-loop; never infer iterations from compiler counts.

    Resolve counters in their lexical scope, including function and
    block locals. Constant headers plus a non-suspending body establish each
    invocation's coordinates; earlier/later loops may reuse the same counter.
    """
    def reject():
        raise ValueError("native loop occurrence contexts need constant bounds and a nonwrapping counter")

    def constant(expression):
        while expression.get("kind") == "ParenthesizedExpression":
            expression = expression["expression"]
        if header is None:
            return _literal(expression)
        coordinates = spans.get(id(expression))
        value = None if coordinates is None else header.value_at(coordinates)
        return value if value is not None and _MIN <= value <= _MAX else None

    if node.get("kind") != "ForLoopStatement":
        reject()
    initializers, steps = node.get("initializers", []), node.get("steps", [])
    if len(initializers) != 1 or len(steps) != 1:
        reject()
    init, step = initializers[0], steps[0]
    local = init.get("kind") == "ForVariableDeclaration"
    if local:
        declarator = init.get("declarator", {})
        name = declarator.get("name", {})
        counter = name.get("text") if name.get("kind") == "Identifier" else None
        first = constant(declarator.get("initializer", {}).get("expr", {}))
    elif init.get("kind") == "AssignmentExpression":
        counter = _name(init.get("left"))
        first = constant(init.get("right", {}))
    else:
        reject()
    if not counter or counter.startswith("\\") or first is None:
        reject()
    if local:
        declaration = init
    else:
        binding = []
        for scope in reversed(scopes):
            binding = [(item, d) for item in scope.get("members", scope.get("items", []))
                       if item.get("kind") == "DataDeclaration"
                       for d in item.get("declarators", [])
                       if d.get("name", {}).get("text") == counter]
            if binding:
                break
            if scope.get("kind") in {"FunctionDeclaration", "TaskDeclaration"}:
                # A formal argument may shadow a module name. This literal-loop
                # certificate accepts a declared local, not an inferred formal.
                reject()
        if len(binding) != 1:
            reject()
        declaration, declarator = binding[0]
    dtype = declaration.get("type", {})
    if set(declarator) - {"kind", "name", "initializer"}:
        reject()
    if header is None:
        if dtype.get("kind") not in {"IntegerType", "IntType"} \
                or dtype.get("keyword", {}).get("text") not in {"integer", "int"} \
                or set(dtype) - {"kind", "keyword"}:
            reject()
        counter_low, counter_high = _MIN, _MAX
    else:
        if header.counter_type is None:
            reject()
        counter_low, counter_high = header.counter_type.interval
    if not counter_low <= first <= counter_high:
        reject()
    stop = node.get("stopExpr", {})
    bound = constant(stop.get("right", {}))
    operator = stop.get("operatorToken", {}).get("text")
    if _name(stop.get("left")) != counter or bound is None or operator not in {"<", "<=", ">", ">="}:
        reject()
    delta = None
    if step.get("kind") == "AssignmentExpression" and _name(step.get("left")) == counter:
        rhs = step.get("right", {})
        amount = constant(rhs.get("right", {}))
        if _name(rhs.get("left")) == counter and amount is not None:
            if rhs.get("kind") == "AddExpression":
                delta = amount
            elif rhs.get("kind") == "SubtractExpression":
                delta = -amount
    elif step.get("kind") in {"AddAssignmentExpression", "SubtractAssignmentExpression"} \
            and _name(step.get("left")) == counter:
        amount = constant(step.get("right", {}))
        if amount is not None:
            delta = amount if step["kind"] == "AddAssignmentExpression" else -amount
    elif step.get("kind") in {"UnaryPreincrementExpression", "PostincrementExpression",
                              "UnaryPredecrementExpression", "PostdecrementExpression"} \
            and _name(step.get("operand")) == counter:
        delta = 1 if "increment" in step["kind"] else -1
    if delta is None or delta == 0 or (delta > 0) != (operator in {"<", "<="}):
        reject()
    end = bound + (1 if operator == "<=" else -1 if operator == ">=" else 0)
    values = range(first, end, delta)
    final = values[-1] + delta if values else first
    if len(values) > MAX_EVENTS or not counter_low <= final <= counter_high:
        reject()
    if header is not None:
        low, high = ((0, (1 << header.comparison_width) - 1) if header.comparison_unsigned
                     else (-(1 << (header.comparison_width - 1)), (1 << (header.comparison_width - 1)) - 1))
        if min(first, bound, final) < low or max(first, bound, final) > high:
            reject()  # A lossy comparison conversion is not integer order.
    # Only writes during this loop can invalidate its literal enumeration.
    # Non-suspending execution prevents another process interleaving the body;
    # Break/return truncate this sequence; continue still executes the step.
    # Assertion conditions/actions still undergo the same mutation checks.
    # Other control transfers, unproved calls and timing remain excluded.
    body = tuple(walk(node["statement"]))
    call_names = {id(part) for item in body if isinstance(item, dict)
                  and item.get("kind") == "InvocationExpression" and spans is not None
                  and spans[id(item)] in frame_local_calls for part in walk(item["left"])}
    for item in body:
        if type(item) is not dict:
            continue
        kind = item.get("kind", "")
        if kind == "ImmediateAssertStatement" and "delay" in item:
            reject()
        target = item.get("left", item.get("operand"))
        if ("assignmentexpression" in kind.lower() or "incrementexpression" in kind.lower()
                or "decrementexpression" in kind.lower()) and counter in _targets(target):
            reject()
        # Nested loops do not invalidate this counter's header bound. Their
        # effects are inspected recursively just like the nested-for case;
        # timing/waits and writes to this counter remain disqualifying. This
        # does not assert termination of a nested loop or supply its bound.
        if (kind.endswith("Statement") and kind not in LOOP_STATEMENTS | {
                "SequentialBlockStatement", "EmptyStatement", "ConditionalStatement",
                "CaseStatement", "ExpressionStatement", "ReturnStatement",
                "JumpStatement", "ImmediateAssertStatement"}):
            reject()
        if "InvocationExpression" in kind and (
                spans is None or spans[id(item)] not in frame_local_calls):
            reject()
        if kind == "SystemName" and id(item) not in call_names and (
                spans is None or spans[id(item)] not in frame_local_calls):
            reject()
    return StaticLoop(*span, counter, values)
