"""Exact existential goal projections; never projections of complete traces."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.query import QueryCall, SolverAnswer
from ..symbolic import encoder
from ..symbolic import ranges
from ..symbolic import ir, primitives, typecheck as types
from ..symbolic.closure_proof import _PreparedPositionPlan

_UNKNOWN = object()


def _literal_control(expression, values, definitions):
    """Constant Boolean algebra, with no variable/range assumptions.

    Operands use the registered primitive denotation. Unknown operands remain
    unknown, except for Boolean absorbing constants (false AND x, true OR x).
    Only Boolean-result primitives are evaluated: a closed shift or array build
    must not turn this cheap proof into unbounded constant materialization.
    """
    pending = [(expression, False)]
    active = set()
    while pending:
        term, ready = pending.pop()
        if id(term) in values:
            continue
        if isinstance(term, ir.Lit):
            values[id(term)] = term.value
            continue
        writes = definitions.get(term.name) if isinstance(term, ir.Var) else None
        if not isinstance(term, ir.Op) and writes is None:
            values[id(term)] = _UNKNOWN
            continue
        if not ready:
            if id(term) in active:
                values[id(term)] = _UNKNOWN
                continue
            active.add(id(term))
            pending.append((term, True))
            pending.extend((arg, False) for arg in
                           (writes if writes is not None else term.args))
            continue
        active.discard(id(term))
        if writes is not None:
            results = [values[id(expr)] for expr in writes]
            first = results[0]
            values[id(term)] = (first if type(first) in (bool, int) and all(
                type(value) is type(first) and value == first for value in results)
                else _UNKNOWN)
            continue
        args = [values[id(arg)] for arg in term.args]
        value = _UNKNOWN
        if term.op == 'land' and any(arg is False for arg in args):
            value = False
        elif term.op == 'lor' and any(arg is True for arg in args):
            value = True
        elif term.op == 'ite':
            if args[0] is True:
                value = args[1]
            elif args[0] is False:
                value = args[2]
            elif type(args[1]) is bool and args[1] is args[2]:
                value = args[1]
        elif all(arg is not _UNKNOWN for arg in args):
            primitive = primitives.primitive(term.op)
            terms = primitive.signature.terms_for(len(args))
            if primitive.signature.result is primitives.BOOLEAN and terms is not None \
                    and all(kind in (primitives.BOOLEAN, primitives.INTEGER,
                                     primitives.SCALAR, primitives.SAME_SCALAR)
                            for kind in terms):
                value = primitive.denotation(*args)
        values[id(term)] = value
    return values[id(expression)]


def _constant_candidates(proc):
    """All assignments to non-carried names with no other kind of write.

    A defined read is constant if every possible assignment gives the same
    scalar constant, independently of control or iteration. This includes a
    cleared Boolean subsequently assigned false by if-conversion; disagreeing
    assignments and cycles remain unknown. No initial value, input, state or
    loop-index assumption participates in the proof.
    """
    definitions = {}
    # Hand-built procedures may seed a non-carried name at entry. Its first
    # read then need not observe the assignment in this body.
    excluded = set(proc.state) | set(proc.inputs) | set(proc.initial)
    for command in ir.walk(proc.body):
        if isinstance(command, ir.Assign):
            definitions.setdefault(command.var, []).append(command.expr)
        elif isinstance(command, ir.Store):
            excluded.add(command.var)
        elif isinstance(command, ir.For):
            excluded.add(command.index)
    return {name: expr for name, expr in definitions.items() if name not in excluded}


def literal_false_goals(proc):
    """Prove goal predicates false from a validated body's literal control.

    This is not a reachability guess or a change to the catalogue. An occurrence
    beneath a constant-false arm (or a non-positive literal loop count) cannot
    execute in any valuation. A public goal is false only if *all* occurrences
    are blocked. Unknown conditions keep both arms; state, inputs, earlier
    failures and loop iterations cannot revive a blocked occurrence.

    The existing declared-without-occurrence false predicates are retained too.
    Unregistered missing goals are never inferred false. The caller must first
    validate the procedure/plan. Local selection may omit these constant-false
    questions without removing goals or reporting them as hits. Bounded analysis
    still records its own exact UNSAT result; selection is not that result.
    """
    blocked_goals, possible_goals, seen = set(), set(), set()
    values = {}
    definitions = _constant_candidates(proc)
    pending = [(proc.body, False)]
    while pending:
        command, blocked = pending.pop()
        key = id(command), blocked
        if key in seen:
            continue
        seen.add(key)
        if isinstance(command, ir.Seq):
            pending.extend(((command.first, blocked), (command.second, blocked)))
        elif isinstance(command, ir.If):
            guard = _literal_control(command.guard, values, definitions)
            pending.append((command.then, blocked or guard is False))
            pending.append((command.orelse, blocked or guard is True))
        elif isinstance(command, ir.For):
            count = _literal_control(command.trip_count, values, definitions)
            empty = type(count) is int and count <= 0
            pending.append((command.body, blocked or empty))
        elif isinstance(command, (ir.Mark, ir.Fail)) and command.goal:
            (blocked_goals if blocked else possible_goals).add(command.goal)
    registered = set(proc.discharged) | set(proc.impossible)
    return frozenset((blocked_goals | registered) - possible_goals)


def _planned_false_goals(proc, plan, *, checkpoint):
    """Goals blocked at every occurrence by a checked one-step encoding plan.

    The caller owns/binds the plan to this procedure and its complete carrier
    domain. Use exactly the encoder's constant-decision rule before expanding
    preceding computations. Missing carriers are *not* unreachable witnesses;
    they leave both arms possible. Joined loop carriers must cover all dynamic
    entries. A goal with any unblocked occurrence remains a solver question.

    This does not edit declarations or issue a bounded-analysis certificate.
    Incomplete plans supply no shortcut and retain their encoding diagnostic.
    """
    from ..symbolic.plan import ExpressionPosition, _command_numbers, _expression_occurrences

    if plan.unresolved:
        return frozenset()
    numbers = _command_numbers(proc.body)

    def carriers(command, role, expression):
        found = {}
        position = ExpressionPosition(numbers[id(command)], role)
        for key, term in _expression_occurrences(expression, position):
            checkpoint()
            value = plan.occurrence_carriers.get(key)
            if value is None:
                return None
            value = value.range
            previous = found.get(id(term))
            if previous is not None and previous != value:
                return None
            found[id(term)] = value
        return found

    # If-conversion puts branch decisions in Boolean temporaries. Prove each
    # possible assignment at its own occurrence, then use the same all-writes
    # agreement rule as literal control. Input/state/initial names and loop indices
    # stay excluded; a changing or cyclic definition stays unknown.
    candidates = _constant_candidates(proc)
    definitions = {}
    for command in ir.walk(proc.body):
        checkpoint()
        if not isinstance(command, ir.Assign) or command.var not in candidates:
            continue
        expression = command.expr
        if isinstance(expression, ir.Op) and primitives.primitive(expression.op).signature.result \
                is primitives.BOOLEAN:
            values = carriers(command, "rhs", expression)
            truth = (None if values is None else ranges._constant_truth(
                expression, lambda term, values=values: values[id(term)]))
            if truth is not None:
                expression = ir.Lit(truth)
        definitions.setdefault(command.var, []).append(expression)

    constants = {}
    blocked_goals, possible_goals, seen = set(), set(), set()
    pending = [(proc.body, False)]
    while pending:
        checkpoint()
        command, blocked = pending.pop()
        key = id(command), blocked
        if key in seen:
            continue
        seen.add(key)
        if isinstance(command, ir.Seq):
            pending.extend(((command.first, blocked), (command.second, blocked)))
        elif isinstance(command, ir.If):
            truth = _literal_control(command.guard, constants, definitions)
            if type(truth) is not bool:
                values = None if blocked else carriers(command, "guard", command.guard)
                truth = (None if values is None else ranges._constant_truth(
                    command.guard, lambda expression, values=values: values[id(expression)]))
            pending.append((command.then, blocked or truth is False))
            pending.append((command.orelse, blocked or truth is True))
        elif isinstance(command, ir.For):
            values = None if blocked else carriers(command, "trip_count", command.trip_count)
            empty = values is not None and values[id(command.trip_count)].hi <= 0
            pending.append((command.body, blocked or empty))
        elif isinstance(command, (ir.Mark, ir.Fail)) and command.goal:
            (blocked_goals if blocked else possible_goals).add(command.goal)
    return frozenset(blocked_goals - possible_goals)


@dataclass(frozen=True, slots=True)
class _StatelessGoals:
    """A one-step goal question with witnesses in the original N-step domain."""

    steps: int
    inputs: tuple[tuple[str, int, int], ...]
    plan: _PreparedPositionPlan | None

    def lift(self, trace):
        if type(trace) is not list or len(trace) != 1:
            raise ValueError("a stateless projection needs a one-step witness")
        record = trace[0]
        if type(record) is not dict or set(record) != {
                name for name, _, _ in self.inputs}:
            raise ValueError("a projected witness has the wrong input fields")
        if any(type(record[name]) is not int or not lo <= record[name] <= hi
               for name, lo, hi in self.inputs):
            raise ValueError("a projected witness is outside its input domain")
        # Separate records: a consumer mutating one slot must not mutate N.
        return [dict(record) for _ in range(self.steps)]

    def restore(self, result):
        from .residual import Residual

        if type(result) is not Residual or result.steps != 1:
            raise ValueError("a projected result must describe its physical prefix")
        return Residual(
            {goal: self.lift(trace) for goal, trace in result.reachable.items()},
            result.unreachable, result.undecided, self.steps,
            tuple(QueryCall(
                call.goals, call.answer, witness=self.lift(call.witness),
                telemetry=call.telemetry, solver_calls=call.solver_calls)
                  if call.answer is SolverAnswer.SAT else call
                  for call in result.queries))


def stateless_goals(proc, env, steps, initial, plan):
    """Prove that some active step hits a goal iff the first step can hit it.

    This residual API has independent per-step inputs, terminal failure
    absorption, and no cross-step constraints beyond carried state. With empty
    state, an active hitting input can be moved to the first position. Conversely
    repeating a first-step witness fills the original domain; its first hit
    survives even if that step fails. This applies to each goal and disjunction,
    not to arbitrary trace properties or normal-completion queries.

    Admit only the validated integer-input subdomain and a uniform input support.
    This is the existing residual decoder's scalar wire format; other carriers
    keep their original path rather than being coerced during witness lifting.
    Custom initial terms, carried state, or an unfamiliar plan retain the full
    path. A failed premise is never an UNSAT answer or a new typed refusal.
    """
    if type(steps) is not int or steps <= 1 or initial is not None:
        return None
    if proc.state or proc.initial:
        return None
    if plan is not None and type(plan) is not _PreparedPositionPlan:
        return None
    encoder.validate_procedure(proc)
    if proc.semantics.state_types or any(
            kind is not types.INTEGER
            for _, kind in proc.semantics.input_types):
        return None
    inputs = []
    declared = dict(proc.semantics.solver_input_bounds())
    for name in proc.inputs:
        support = env.get(name)
        if type(support) is not ranges.Range or support.lo > support.hi:
            return None
        if name in declared and (support.lo, support.hi) != declared[name]:
            return None
        inputs.append((name, support.lo, support.hi))
    if plan is not None:
        original = plan.checked_for(proc, env, steps)
        if any(layer for layer in original.state_layers):
            return None
        for step in original.steps:
            if any(step.env.get(name) != env[name] for name in proc.inputs):
                return None
        plan = plan.first_step(proc, env, steps)
    return _StatelessGoals(steps, tuple(inputs), plan)
