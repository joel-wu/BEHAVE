"""Instantiate the shared ordered monitor without rebuilding its expression DAG.

One bounded product owns one template with fixed configuration and payload sorts.
Every invocation substitutes the complete supplied state and event. No initial-
state assumptions, omitted fields, solver results or cross-product caches enter
this construction.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass

import z3


def _map_terms(value, operation):
    if is_dataclass(value):
        return type(value)(**{
            field.name: _map_terms(getattr(value, field.name), operation)
            for field in fields(value)
        })
    if isinstance(value, Mapping):
        return {key: _map_terms(child, operation) for key, child in value.items()}
    if isinstance(value, tuple):
        return tuple(_map_terms(child, operation) for child in value)
    return operation(value)


def constant_monitor_state(value, shape):
    """Lift an exact native prefix into the query's own sorts and context."""
    if is_dataclass(shape):
        if type(value) is not type(shape):
            raise TypeError('native monitor state changed its record type')
        return type(shape)(**{f.name: constant_monitor_state(getattr(value, f.name),
                             getattr(shape, f.name)) for f in fields(shape)})
    if isinstance(shape, Mapping):
        if not isinstance(value, Mapping) or value.keys() != shape.keys():
            raise ValueError('native monitor state changed its component keys')
        return {key: constant_monitor_state(value[key], term) for key, term in shape.items()}
    if isinstance(shape, tuple):
        if type(value) is not tuple or len(value) != len(shape):
            raise ValueError('native monitor state changed its finite layout')
        return tuple(constant_monitor_state(v, s) for v, s in zip(value, shape, strict=True))
    if z3.is_bool(shape) and type(value) is bool:
        return z3.BoolVal(value, ctx=shape.ctx)
    if z3.is_int(shape) and type(value) is int:
        return z3.IntVal(value, ctx=shape.ctx)
    if z3.is_bv(shape) and type(value) is int and 0 <= value < 1 << shape.size():
        return z3.BitVecVal(value, shape.size(), ctx=shape.ctx)
    raise ValueError('native monitor value is outside its declared carrier')


def define_monitor_state(state, *, prefix='monitor_state'):
    """Name each non-atomic successor with its exact, total definition.

    The next transition consumes these shallow state coordinates rather than
    inlining the complete prior history. Every fresh coordinate has exactly
    one defining equality; no reachable state or latent field is discarded.
    The caller must retain all returned definitions in the complete product.
    """
    definitions = []

    def define(term):
        # Do not recursively simplify each leaf: shared source guards can
        # contain the entire event prefix and would be revisited per cell.
        if z3.is_const(term):
            return term
        atom = z3.FreshConst(term.sort(), prefix=prefix)
        definitions.append(atom == term)
        return atom

    return _map_terms(state, define), definitions


class _DefinitionDependencies:
    """One immutable equation set's syntactic dependencies, not model values.

    Roots keep every memoized AST alive; context participates in each key.
    Bit sets name only owned definition atoms. A frame intersects that set
    with its own coordinates, so earlier/later definitions are not inlined
    or assumed. No instance is shared across independent requests.
    """

    def __init__(self, equations, *, check):
        self.roots = tuple(equations)
        self.equations, self.atoms, self.supports = set(), {}, {}
        for equation in self.roots:
            check()
            self.equations.add(self.key(equation))
            if z3.is_eq(equation):
                lhs = equation.arg(0)
                if z3.is_const(lhs) and lhs.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                    key = self.key(lhs)
                    if key not in self.atoms:
                        self.atoms[key] = 1 << len(self.atoms)

    @staticmethod
    def key(term):
        return term.ctx, term.get_id()

    def support(self, term, *, check):
        pending = [(term, None)]
        while pending:
            check()
            node, children = pending.pop()
            key = self.key(node)
            if key in self.supports:
                continue
            if key in self.atoms:
                self.supports[key] = self.atoms[key]
            elif children is None:
                children = tuple(node.children())
                pending.append((node, children))
                pending.extend((child, None) for child in reversed(children)
                               if self.key(child) not in self.supports)
            else:
                bits = 0
                for child in children:
                    bits |= self.supports[self.key(child)]
                self.supports[key] = bits
        return self.supports[self.key(term)]


def _definition_layers(frame, *, check, dependencies=None):
    """Topologically group definitions using one shared syntactic DAG index.

    A source action can name its successors and then a boundary using those
    names. Evaluating both together reads stale/default model coordinates.
    Cyclic or multiply defined coordinates provide no completion plan; the
    complete original formula remains the fallback authority.
    """
    frame = tuple(frame)
    definitions = {}
    for equation in frame:
        check()
        if not z3.is_eq(equation):
            return None
        lhs, rhs = equation.children()
        if not z3.is_const(lhs) or lhs.decl().kind() != z3.Z3_OP_UNINTERPRETED \
                or lhs.get_id() in definitions:
            return None
        definitions[lhs.get_id()] = lhs, rhs
    if dependencies is None or any(dependencies.key(equation) not in dependencies.equations
                                   for equation in frame):
        dependencies = _DefinitionDependencies(frame, check=check)
    by_bit = {dependencies.atoms[dependencies.key(lhs)]: key
              for key, (lhs, _) in definitions.items()}
    mask = sum(by_bit)
    graph = {}
    for key, (_, rhs) in definitions.items():
        check()
        bits = dependencies.support(rhs, check=check) & mask
        children = []
        while bits:
            bit = bits & -bits
            children.append(by_bit[bit])
            bits ^= bit
        graph[key] = tuple(children)
    levels, visiting = {}, set()
    for key in definitions:
        stack = [(key, False)]
        while stack:
            check()
            key, expanded = stack.pop()
            if key in levels:
                continue
            children = graph[key]
            if expanded:
                levels[key] = max((levels[child] for child in children), default=0) + 1
                visiting.remove(key)
            else:
                if key in visiting:
                    return None
                visiting.add(key)
                stack.append((key, True))
                stack.extend((child, False) for child in reversed(children)
                             if child not in levels)
    groups = {}
    for key, pair in definitions.items():
        groups.setdefault(levels[key], []).append(pair)
    return tuple(tuple(groups[level]) for level in sorted(groups))


class _MonitorFrames:
    """Parsed immutable equations, not values, models or proof results."""

    def __init__(self, definitions, sizes, *, check, dependencies=None, proposal_layers=()):
        self.definitions = tuple(definitions)
        definitions = self.definitions
        self.omitted = frozenset(term.get_id() for term in definitions)
        self.frames = None
        self.proposal_only = False
        sizes = sizes or (1,) * len(definitions)
        if any(type(size) is not int or size < 0 for size in sizes) or sum(sizes) != len(definitions):
            return
        if dependencies is None and not proposal_layers:
            dependencies = _DefinitionDependencies(definitions, check=check)
        offset, constructors, frames = 0, {}, []
        for index, size in enumerate(sizes):
            check()
            frame = definitions[offset:offset + size]
            offset += size
            if not frame:
                continue
            layers = None
            if len(proposal_layers) == len(sizes):
                proposed = proposal_layers[index]
                flat = tuple(term for group in proposed for term in group)
                # Metadata can be stale after replacing an interval's equations.
                # Only use the grouping of these exact original definitions.
                if len(flat) == len(frame) and all(a.eq(b) for a, b in zip(flat, frame)):
                    pairs, owned = [], set()
                    for group in proposed:
                        batch = []
                        for equation in group:
                            check()
                            if not z3.is_eq(equation):
                                return
                            lhs, rhs = equation.children()
                            if not z3.is_const(lhs) or lhs.decl().kind() != z3.Z3_OP_UNINTERPRETED \
                                    or lhs.get_id() in owned:
                                return
                            owned.add(lhs.get_id())
                            batch.append((lhs, rhs))
                        if batch:
                            pairs.append(tuple(batch))
                    layers = tuple(pairs)
                    self.proposal_only = True
            if layers is None:
                layers = _definition_layers(frame, check=check, dependencies=dependencies)
            if layers is None:
                return
            packed = []
            for layer in layers:
                coordinates, values = zip(*layer, strict=True)
                sorts = tuple(value.sort() for value in values)
                key = tuple(sort.get_id() for sort in sorts)
                if key not in constructors:
                    name = str(z3.FreshBool('monitor_frame', ctx=values[0].ctx).decl().name())
                    _, constructors[key], _ = z3.TupleSort(name, sorts, ctx=values[0].ctx)
                packed.append((tuple(atom.decl() for atom in coordinates), constructors[key](*values)))
            frames.append(tuple(packed))
        check()
        self.frames = tuple(frames)


def monitor_frames(history, *, check):
    """Only the immutable history may retain its parsed definition layout."""
    check()
    prepared = getattr(history, '_monitor_frames', None)
    if prepared is not None:
        return prepared(check=check)
    return _MonitorFrames(getattr(history, 'monitor_constraints', ()),
                          getattr(history, 'monitor_frame_sizes', ()), check=check)


def complete_monitor_model(model, definitions, assertions, *, check, frame_sizes=(), _frames=None):
    """Validate a candidate full assignment, never assume dropped equations.

    A source-only SAT hint can fill the monitor's total successor definitions
    in construction order. Acceptance still requires every original assertion
    to evaluate to true. Invalid definitions, conflicting assignments or a
    non-ground result give no witness; the caller keeps its full-query fallback.
    """
    from .symbolic_state import assertion_conjunction

    check()
    model = model.translate(model.ctx)
    plans = (_MonitorFrames(definitions, frame_sizes, check=check),) if _frames is None else _frames
    if any(plan.frames is None for plan in plans):
        return None
    for plan in plans:
        for frame in plan.frames:
            for coordinates, packed in frame:
                check()
                # One independent layer; this tuple never becomes a root.
                evaluated = model.eval(packed, model_completion=True)
                for coordinate, value in zip(coordinates, evaluated.children(), strict=True):
                    check()
                    model.update_value(coordinate, value)
    complete = assertion_conjunction(assertions, check=check)
    valid = z3.is_true(model.eval(complete, model_completion=True))
    check()
    return model if valid else None


def solve_monitor_member(assertions, histories, solve, *, phase, check):
    """Solve the source, then validate a complete monitor assignment.

    This is only an existential hint. Neither a partial model nor a failed
    completion establishes anything: both fall back to the original query.
    Used by domain admission and fixed native-record membership alike.
    """
    from ..symbolic.exact_solve import ExactCheckResult
    from ..core.query import SolverAnswer

    check()
    histories = tuple(histories)
    plans = tuple(monitor_frames(history, check=check) for history in histories)
    if any(plan.definitions for plan in plans):
        omitted = frozenset().union(*(plan.omitted for plan in plans))
        roots = tuple(term for term in assertions if term.get_id() not in omitted)
        context = next(plan.definitions[0].ctx for plan in plans if plan.definitions)
        candidate = solve(phase + '-source', roots or (z3.BoolVal(True, ctx=context),))
        check()
        if candidate.answer is SolverAnswer.SAT:
            model = complete_monitor_model(candidate.model, (), assertions,
                                           check=check, _frames=plans)
            if model is not None:
                return ExactCheckResult(SolverAnswer.SAT, model=model,
                    solver_load_ns=candidate.solver_load_ns, solve_ns=candidate.solve_ns)
    check()
    return solve(phase, assertions)


def reduce_monitor_defect(defect, definitions, bindings, *, frame_sizes=(), check, _frames=None):
    """Evaluate only consequences of proved literals and asserted definitions.

    Unlike existential model completion, this interpreter never supplies a
    value for an unknown symbol. Each stored coordinate is a literal obtained
    from its defining equation using already-proved facts. A false result is
    therefore a sufficient universal proof; a live expression proves nothing.
    The owner still establishes feasibility on the complete original product.
    """
    from .symbolic_state import FixedBindings, _literal

    plan = _MonitorFrames(definitions, frame_sizes, check=check) if _frames is None else _frames
    if plan.frames is None or plan.proposal_only:
        return defect
    # These are proved compound observations as well as request atoms. Batch
    # substitution cuts their source histories once per frame, not per cell.
    known = tuple(bindings.literal_bindings)
    literals = FixedBindings((), known, known)
    interpretation = z3.Model(ctx=defect.ctx)
    for frame in plan.frames:
        for coordinates, packed in frame:
            check()
            evaluated = interpretation.eval(literals.substitute(packed), model_completion=False)
            for coordinate, value in zip(coordinates, evaluated.children(), strict=True):
                check()
                if _literal(value):
                    interpretation.update_value(coordinate, value)
    result = interpretation.eval(literals.substitute(defect), model_completion=False)
    check()
    return result


def _terms(value):
    if is_dataclass(value):
        children = (getattr(value, field.name) for field in fields(value))
    elif isinstance(value, dict):
        children = value.values()
    elif isinstance(value, tuple):
        children = value
    else:
        yield value
        return
    for child in children:
        yield from _terms(child)


