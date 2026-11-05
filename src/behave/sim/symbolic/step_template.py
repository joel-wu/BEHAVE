"""Reuse an exact finite transition without re-encoding its command tree.

The encoder's acyclic defining equations are substituted exactly once into a
shared expression DAG. Each instance supplies its own boundary atoms. Only
successor state needs fresh definitions between steps; intermediate assignments
are already represented by the shared DAG. The unrestricted template adds no
range or path premise; specialized views retain their input equalities explicitly.

Only a search/analysis owner constructs this object. It is not a native replay,
source admission check, or persistent result cache.
"""

from collections import Counter
from copy import copy
from dataclasses import dataclass, replace

import z3

from behave.isolation.preparation_control import check_preparation

from ..core.construction_checkpoint import finish_construction
from . import ir, typecheck as types
from . import ranges
from .bitvectors import _wire_value
from .encoder import (
    _encode_step,
    _flatten_tree,
    _symbolic_value,
    encode_concrete_value,
    validated_width,
)
from .validation import EncodingError, _checked_procedure_validation, _procedure_validation
from .functional_array import FunctionalArray
from .plan import plan_encoding
from .template_arrays import TemplateArrays


def _tree(value):
    if isinstance(value, FunctionalArray):
        width = max(1, value.length.bit_length() + 1)
        context = next(iter(value.formula_terms), z3.BoolVal(True)).ctx
        value = [value.select(z3.BitVecVal(index, width, ctx=context)) for index in range(value.length)]
    return [_tree(item) for item in value] if isinstance(value, list) else value


def _leaves(value):
    if isinstance(value, FunctionalArray):
        value = _tree(value)
    if isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


def _map_tree(value, leaf):
    """Map an owned value/layout tree without a self-referencing closure.

    Query-local recursive closures retain their captured native AST arenas
    until cyclic collection, rather than releasing them with the query.
    """
    if isinstance(value, dict):
        return {name: _map_tree(item, leaf) for name, item in value.items()}
    if isinstance(value, list):
        return [_map_tree(item, leaf) for item in value]
    return leaf(value)


def _constant_prefix(value, shared):
    """Read explicit leading bits through wiring and both arms of a mux.

    Decode each needed boundary DAG node once, using its native handle. Stop
    a concatenation at its first unknown bit, and a mux once either arm has
    no prefix. The live boundary owns the handles, as in ``_wire_value``.
    Arithmetic and mux guards are opaque; no solver, range or valuation is
    a premise. Cached triples also retain the node's total bit width.
    """
    from z3 import z3core as native

    known = shared.setdefault(('prefixes', value.ctx), {})
    ctx = value.ctx_ref()
    root = value.get_id()
    pending = [(value.as_ast(), root, None)]
    while pending:
        yield
        term, key, frame = pending.pop()
        if key in known:
            continue
        if frame is None:
            sort = native.Z3_get_sort(ctx, term)
            if native.Z3_get_sort_kind(ctx, sort) != z3.Z3_BV_SORT:
                known[key] = (0, 0, 0)
                continue
            size = native.Z3_get_bv_sort_size(ctx, sort)
            ast_kind = native.Z3_get_ast_kind(ctx, term)
            if ast_kind == z3.Z3_NUMERAL_AST:
                known[key] = int(native.Z3_get_numeral_string(ctx, term)), size, size
                continue
            decl = native.Z3_get_app_decl(ctx, term) if ast_kind == z3.Z3_APP_AST else None
            kind = native.Z3_get_decl_kind(ctx, decl) if decl is not None else None
            if kind not in (z3.Z3_OP_CONCAT, z3.Z3_OP_ZERO_EXT, z3.Z3_OP_SIGN_EXT,
                            z3.Z3_OP_EXTRACT, z3.Z3_OP_ITE):
                known[key] = (0, 0, size)
                continue
            index = 1 if kind == z3.Z3_OP_ITE else 0
            child = native.Z3_get_app_arg(ctx, term, index)
            child_key = native.Z3_get_ast_id(ctx, child)
            frame = (kind, decl, size, index, child_key, 0, 0)
            pending.append((term, key, frame))
            if child_key not in known:
                pending.append((child, child_key, None))
            continue
        kind, decl, size, index, child_key, bits, width = frame
        prefix, count, child_size = known[child_key]
        more = False
        if kind == z3.Z3_OP_ITE:
            if index == 1:
                bits, width = prefix, count
                more = bool(width)
            else:
                common = min(width, count)
                bits >>= width - common
                different = bits ^ (prefix >> (count - common))
                width = common - different.bit_length()
                bits >>= common - width
        elif kind == z3.Z3_OP_CONCAT:
            bits, width = (bits << count) | prefix, width + count
            more = count == child_size and index + 1 < native.Z3_get_app_num_args(ctx, term)
        elif kind == z3.Z3_OP_EXTRACT:
            high = native.Z3_get_decl_int_parameter(ctx, decl, 0)
            low = native.Z3_get_decl_int_parameter(ctx, decl, 1)
            first = child_size - count
            last = max(first, low)
            width = max(0, high - last + 1)
            bits = (prefix >> (last - first)) & ((1 << width) - 1)
        else:
            bits, width = prefix, count
            extension = native.Z3_get_decl_int_parameter(ctx, decl, 0)
            if kind == z3.Z3_OP_ZERO_EXT:
                width += extension
            elif width:
                if bits >> (width - 1):
                    bits |= ((1 << extension) - 1) << width
                width += extension
        if more:
            child = native.Z3_get_app_arg(ctx, term, index + 1)
            child_key = native.Z3_get_ast_id(ctx, child)
            pending.append((term, key, (kind, decl, size, index + 1, child_key, bits, width)))
            if child_key not in known:
                pending.append((child, child_key, None))
        else:
            known[key] = bits, width, size
    return known[root][:2]


def _bind_successors(value, equalities, shared, tag, *, wires=False, _facts=None):
    """Own exact scalar/array boundaries without recursive closure cycles."""
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            result[name] = yield from _bind_successors(item, equalities, shared, tag, wires=wires, _facts=_facts)
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.append((yield from _bind_successors(item, equalities, shared, tag, wires=wires, _facts=_facts)))
        return result
    # Equal native ASTs can arrive through different ExprRef wrappers (for
    # example two fields selecting the same prepared template expression).
    # Name that computation once per boundary, not once per Python wrapper.
    # FunctionalArray closures retain their own identity; do not infer array
    # equivalence from their shape or from a previously observed valuation.
    key = ('array', id(value)) if isinstance(value, FunctionalArray) else ('ast', value.ctx, value.get_id())
    if key not in shared:
        first = len(equalities)
        if isinstance(value, FunctionalArray):
            successor, definitions = yield from value.bind_state_steps(tag=tag)
            equalities.extend(definitions)
        elif z3.is_const(value) or wires and _wire_value(value, shared):
            successor = value
        else:
            bits, width = (yield from _constant_prefix(value, shared)) if wires else (0, 0)
            # Keep one sign bit in a narrowed computation. Otherwise e.g.
            # 0..2 becomes a signed two-bit payload (-2..1), and reattaching
            # the zeros loses the exact hull as 0..3 in every later step.
            # This is identical wiring, not an additional range premise.
            if (_facts is not None and 0 < width < value.size()
                    and bits in (0, (1 << width) - 1)):
                bits, width = bits >> 1, width - 1
            if width:
                # Keep common constant high bits visible across transitions.
                # Every remaining bit still has its original defining equation.
                successor = z3.BitVecVal(bits, width, ctx=value.ctx)
                if width < value.size():
                    payload = z3.FreshConst(z3.BitVecSort(value.size() - width, ctx=value.ctx), tag + '_state')
                    equalities.append(payload == z3.Extract(value.size() - width - 1, 0, value))
                    successor = z3.Concat(successor, payload)
            else:
                successor = z3.FreshConst(value.sort(), tag + '_state')
                equalities.append(successor == value)
        if _facts is not None and not isinstance(value, FunctionalArray):
            # Only this activation's scalar definitions enter its scoped
            # control proof. Array images remain opaque boundaries; following
            # their packed histories would defeat control-before-data demand.
            for equation in equalities[first:]:
                left, right = equation.children()
                _facts.definitions[left.get_id()] = right
                _facts.terms[left.get_id()] = left
        shared[key] = successor
    return shared[key]


def _bind_tree(old, new):
    if isinstance(old, list):
        for before, after in zip(old, _tree(new), strict=True):
            yield from _bind_tree(before, after)
    else:
        yield old, new


def _inline_definitions(roots, definitions, parameters=()):
    """Expand encoder-owned definitions over their shared, typed application DAG.

    Keep the traversal in native AST handles: wrapping every child as an ExprRef
    repeatedly rediscovers its sort and acquires/releases Python-owned references.
    The original roots/definitions own the input DAG; one AstVector owns every
    rebuilt node until the returned root wrappers take ownership of the result.
    The same traversal propagates positional parameter-dependency masks. No
    second whole-circuit walk, solver or new typing assumption is needed.
    """
    from z3 import z3core as native
    from z3.z3 import _to_expr_ref
    from z3.z3types import Ast

    check_preparation()
    context = roots[0].ctx
    ctx = context.ref()
    owned = z3.AstVector(ctx=context)
    rhs = {item.lhs.get_id(): item.rhs.as_ast() for item in definitions}
    boundary = {term.get_id(): 1 << index for index, term in enumerate(parameters)}
    expanded, dependencies = {}, {}
    for root in roots:
        pending = [(root.as_ast(), root.get_id(), None)]
        while pending:
            check_preparation()
            term, key, children = pending.pop()
            if key in expanded:
                continue
            if native.Z3_get_ast_kind(ctx, term) == z3.Z3_VAR_AST:
                # Normalized templates use positional parameters, not the
                # encoder's named entry atoms. Recompute their dependencies
                # from the rewritten DAG instead of retaining eliminated reads.
                expanded[key] = term
                dependencies[key] = 1 << native.Z3_get_index_value(ctx, term)
                continue
            if children is None:
                values = ((rhs[key],) if key in rhs else tuple(
                    native.Z3_get_app_arg(ctx, term, index)
                    for index in range(native.Z3_get_app_num_args(ctx, term))))
                children = tuple((value, native.Z3_get_ast_id(ctx, value)) for value in values)
                pending.append((term, key, children))
                pending.extend((value, child, None) for value, child in reversed(children)
                               if child not in expanded)
            elif key in rhs:
                expanded[key] = expanded[children[0][1]]
                dependencies[key] = dependencies[children[0][1]]
            else:
                mask = boundary.get(key, 0)
                for _, child in children:
                    mask |= dependencies[child]
                dependencies[key] = mask
                values = [expanded[child] for _, child in children]
                if all(old.value == new.value for (old, _), new in zip(children, values, strict=True)):
                    expanded[key] = term
                else:
                    updated = native.Z3_update_term(ctx, term, len(values), (Ast * len(values))(*values))
                    native.Z3_ast_vector_push(ctx, owned.vector, updated)
                    expanded[key] = updated
    return ([_to_expr_ref(expanded[root.get_id()], context) for root in roots],
            tuple(dependencies[root.get_id()] for root in roots))


@dataclass(frozen=True, slots=True)
class TransitionEncoding:
    pre: dict
    state_post: dict
    post: dict
    hits: dict
    constraint: object
    ok: object
    error: object
    definitions: tuple = ()


def _normalize_shared(image):
    """Fold shared regions without distributing their users through the DAG.

    Temporary names protect each multiply-used expression during rewriting.
    Restore every definition before returning: no new variable, premise or
    equation reaches an instance. Two iterative DAG walks visit each original
    node/edge once; native simplification operates between shared boundaries.
    This also folds literal/identity regions before recomputing dependencies.
    """
    from z3 import z3core as native
    from z3.z3 import _to_expr_ref
    from z3.z3types import Ast
    from ..symbolic.exact_formula import FormulaDefinition

    context, ctx = image.ctx, image.ctx_ref()
    # The image owns the original DAG; the vector owns rebuilt native nodes.
    # Wrap only shared regions that need simplification, not every input edge.
    owned = z3.AstVector(ctx=context)
    uses, children, order = Counter(), {}, []
    root = image.get_id()
    pending, done = [(image.as_ast(), root, False)], set()
    while pending:
        check_preparation()
        term, key, ready = pending.pop()
        if key in done:
            continue
        if key not in children:
            args = (tuple(native.Z3_get_app_arg(ctx, term, i)
                          for i in range(native.Z3_get_app_num_args(ctx, term)))
                    if native.Z3_get_ast_kind(ctx, term) == z3.Z3_APP_AST else ())
            children[key] = tuple((arg, native.Z3_get_ast_id(ctx, arg)) for arg in args)
            uses.update(child for _, child in children[key])
        if ready:
            order.append((term, key))
            done.add(key)
        else:
            pending.append((term, key, True))
            pending.extend((arg, child, False) for arg, child in reversed(children[key]))
    rewritten, definitions, names = {}, [], {}
    for term, key in order:
        check_preparation()
        args = children[key]
        values = tuple(rewritten[child] for _, child in args)
        value = term
        if any(old.value != new.value for (old, _), new in zip(args, values, strict=True)):
            value = native.Z3_update_term(ctx, term, len(values), (Ast * len(values))(*values))
            native.Z3_ast_vector_push(ctx, owned.vector, value)
        if uses[key] > 1 and args:
            folded = z3.simplify(_to_expr_ref(value, context), flat=False)
            # Keep negation visible so users can fold complementary guards.
            # Protect its operand, including when it is shared only through
            # this negation; otherwise cancelling NOT could expose a big cone.
            negative = z3.is_not(folded)
            region = folded.arg(0) if negative else folded
            if not z3.is_const(region) and not z3.is_var(region):
                other = names.get(region.get_id())
                if other is None:
                    other = z3.FreshConst(region.sort(), 'shared_normalization')
                    definitions.append(FormulaDefinition('temporary-rewrite', other, region))
                    names[region.get_id()] = other
                folded = z3.Not(other) if negative else other
            value = folded.as_ast()
            native.Z3_ast_vector_push(ctx, owned.vector, value)
        rewritten[key] = value
    normalized = z3.simplify(_to_expr_ref(rewritten[root], context), flat=False)
    return _inline_definitions((normalized,), definitions)[0][0]


def _template_validation(procedure, validation):
    if validation is None:
        return _procedure_validation(procedure, compact_arrays=True)
    validation = _checked_procedure_validation(validation, procedure)
    if not validation.compact_arrays:
        raise TypeError('transition templates require compact-array validation')
    return validation


def template_state_dependencies(templates, *, outputs, hits=None, state_reads=()):
    """Close state dependencies across prepared transitions without rebuilding views.

    Follow prepared dependency masks, visiting each parameter owner once per
    view. A retained state's successor dependencies are followed in *every*
    view, including one which does not itself observe that state. This avoids
    repeated whole-state scans and repeated projections of alternating views.
    Constraints, failure roots and explicit caller reads remain mandatory.
    ``hits=None`` observes each view's own hit catalogue, allowing physical
    phases to retain different private failure events in the same history.
    """
    templates = tuple(templates)
    outputs = tuple(outputs)
    hits = None if hits is None else tuple(hits)
    states, indexes = set(state_reads), []
    pending, visited = list(states), [0] * len(templates)

    def reads(view, shapes):
        mask = 0
        for shape in shapes:
            for index in _leaves(shape):
                mask |= view._dependencies[index]
        return mask

    def follow(index, mask):
        mask &= ~visited[index]
        while mask:
            check_preparation()
            name, offset, count = indexes[index][(mask & -mask).bit_length() - 1]
            span = ((1 << count) - 1) << offset
            visited[index] |= span
            mask &= ~span
            if name in templates[index]._layout['state_post'] and name not in states:
                states.add(name)
                pending.append(name)

    for index, view in enumerate(templates):
        check_preparation()
        layout, owners = view._layout, []
        for name, value in view._parameters.items():
            count = sum(1 for _ in _leaves(value))
            owners.extend([(name, len(owners), count)] * count)
        indexes.append(owners)
        roots = [layout[key] for key in ('constraint', 'ok', 'error')]
        roots += [layout['post'][name] for name in outputs]
        roots += [layout['hits'][mark] for mark in (layout['hits'] if hits is None else hits)]
        follow(index, reads(view, roots))
    # Queue names, not dense dependency masks. Keep one visited bitmap per
    # view rather than one growing-width bitmap per parameter/state.
    while pending:
        check_preparation()
        name = pending.pop()
        for index, view in enumerate(templates):
            follow(index, reads(view, [view._layout['state_post'][name]]))
    return frozenset(states)


def project_templates(templates, groups, *, outputs=None, state_reads=()):
    """Project once per view, after closing their shared history dependencies."""
    templates = tuple(templates)
    groups = {name: tuple(dict.fromkeys(members)) for name, members in groups.items()}
    states = set(state_reads)
    if outputs is not None:
        outputs = tuple(outputs)
        states = template_state_dependencies(templates, outputs=outputs,
            hits=(mark for marks in groups.values() for mark in marks), state_reads=states)
    return tuple(view._project(groups, outputs, states) for view in templates)


class StepTemplate:
    """One verifier-built procedure/environment; fresh independent instances.

    Symbolic pre-state is supplied to the encoder explicitly. In particular,
    the template must not assume its inferred state ranges: a supplied successor
    in ordinary unrolling is not restricted by those proposed invariants either.
    Declared input restrictions and recursive semantic type checks remain.
    """

    @classmethod
    def selected(cls, procedure, env, marks, *, outputs=(), state_reads=(),
                 include_controlled=False, successors=True, _validation=None):
        """Close an observation's IR dependencies BEFORE planning/encoding.

        Keep all failures and their control/data dependencies, every selected
        mark occurrence, requested outputs, and the transitive predecessors of
        retained state. This is one reusable transition for arbitrary-length
        histories, not a slice specialized to a sampled state or one step.
        Full source validation precedes pruning. Unselected marks and values
        are absent observations, never constant-false answers. Optionally retain
        other marks whose EVERY occurrence already has its controlling commands
        in the closed cone. These observations need no additional computation.
        With successors=False, observe just this activation: keep all failures
        and requested outputs, but do not construct state for a later step.
        That view cannot supply successor state to a composed history.
        """
        from . import slicing
        from .encoder import _query_procedure

        check_preparation()
        marks, outputs, state_reads = frozenset(marks), tuple(outputs), frozenset(state_reads)
        if not successors and state_reads:
            raise ValueError('a local observation cannot request successor state')
        if not state_reads <= set(procedure.state) or not set(outputs) <= set(procedure.output_vars):
            raise ValueError('transition observations must name declared outputs and state')
        validation = _template_validation(procedure, _validation)
        indexed = slicing.index(procedure)
        if not marks <= indexed.goal_positions.keys():
            raise ValueError('transition observations must name declared marks')
        keep = slicing.for_run(procedure, sorted(marks), steps=2 if successors else 1,
                               _index=indexed) or set()
        needs = set(state_reads)
        seeds = {procedure.output_vars[name] for name in outputs}
        while True:
            check_preparation()
            cut = slicing.cone(indexed.events, seed=seeds | needs,
                _bodies=indexed.loop_bodies, _parents=indexed.parents,
                _reads=indexed.command_reads, _branches=indexed.branches)
            keep.update(cut.kept)
            if not successors:
                break
            retained = needs | (cut.needs & set(procedure.state))
            if retained == needs:
                break
            needs = retained
        # for_run closes selected hits; the boundary closure above closes
        # requested values and ALL failures, including failures after a hit.
        faults = slicing._binding_faults(procedure, keep, indexed.events, indexed.command_reads)
        if faults:
            raise EncodingError('; '.join(faults), kind='goal')
        requested = marks
        if include_controlled:
            observed = set(marks)
            for goal, positions in indexed.goal_positions.items():
                check_preparation()
                if goal is None:
                    continue  # An anonymous failure remains a failure, not a goal.
                sites = [indexed.events[position] for position in positions]
                if all((isinstance(command, ir.Mark) or id(command) in keep)
                       and all(id(parent) in keep for parent in parents)
                       for command, parents in sites):
                    observed.add(goal)
                    keep.update(id(command) for command, _parents in sites)
            marks = observed
        projected = _query_procedure(procedure, keep, validation)
        try:
            template = cls(projected, env)
        except EncodingError:
            # Extra observations inside loops can themselves exceed the
            # encoder's reported capacity. They are optional reuse, never a
            # reason to reject a representable requested cone. Cancellation
            # still propagates; this retry has the same preparation deadline.
            if include_controlled and marks != requested:
                return cls.selected(procedure, env, requested,
                                    outputs=outputs, state_reads=state_reads,
                                    successors=successors, _validation=validation)
            raise
        # Both procedures share the complete declaration/type/parameter domain.
        # Retain that owner identity for source binding; only the observation
        # image was compiled from a dependency-closed command selection.
        template.procedure = procedure
        groups = {mark: (mark,) for mark in sorted(marks)}
        return (template.project(groups, outputs=outputs, state_reads=state_reads) if successors
                else template.project_local(groups, outputs=outputs))

    def __init__(self, procedure, env, *, _validation=None):
        check_preparation()
        self.procedure, self.environment = procedure, dict(env)
        validation = _template_validation(procedure, _validation)
        self._arrays = TemplateArrays()
        self._array_lengths = {}
        self._uses_array_parameters = False
        needs_plan = any(isinstance(command, ir.For) for command in ir.walk(procedure.body))
        kinds = dict(procedure.semantics.state_types)
        widths = {name: validated_width(env.get(name), name) for name in procedure.state}
        while True:
            check_preparation()
            # Supplied state is unrestricted within its signed carrier. Plan
            # this domain, not a proposed invariant: otherwise the plan may
            # omit branches that this reusable transition still has to encode.
            # Re-plan after widening; declared input domains stay unchanged.
            entry_env = {**env, **{
                name: ranges.Range(-(1 << (width - 1)), (1 << (width - 1)) - 1)
                for name, width in widths.items()}}
            plan = (plan_encoding(procedure, entry_env, _procedure_validation=validation,
                                  _checkpoint=check_preparation)
                    if needs_plan else None)
            boundary = {name: self._boundary_value(name, kinds[name], widths[name])
                        for name in procedure.state}
            raw = _encode_step(procedure, pre=boundary, env=entry_env, tag='_template', plan=plan,
                               _procedure_validation=validation, _plan_prevalidated=True,
                               _defer_constraint=True, _checkpoint=check_preparation,
                               _plan_checkpoint=check_preparation, _array_recipes=self._arrays)
            check_preparation()
            # A mathematical operation can keep its operands' wider carrier
            # even when its result range is small (e.g. signed x & mask).
            # Reusable transitions need a closed representation at every state
            # boundary. Widen the entry, never truncate a successor or assume
            # its proposed range. Fixed IR operations bound these widths by
            # their planned carriers and explicit fixed-width parameters.
            enlarged = {name: max((value.size() for value in _flatten_tree(raw.state_post[name])),
                                  default=widths[name])
                        for name in procedure.state}
            enlarged = {name: width for name, width in enlarged.items() if width > widths[name]}
            if not enlarged:
                break
            widths.update(enlarged)
        # This representation consumes the registered defining RHSs below, not
        # their equation ASTs or the complete SSA conjunction. Do not allocate
        # that second representation just to discard it. All comparison
        # rewrites, validation, boundary restrictions and event roots remain.
        roots, self._layout = [], {}

        def layout(value):
            value = value if self._uses_array_parameters else _tree(value)
            def append(item):
                index = len(roots)
                roots.append(self._arrays.term(item) if self._uses_array_parameters else item)
                return index
            return _map_tree(value, append)

        self._layout['constraint'] = layout(z3.And(*raw.restrictions))
        for name in ('ok', 'error'):
            self._layout[name] = layout(getattr(raw, name))
        for name in ('state_post', 'post', 'hits'):
            self._layout[name] = {key: layout(value) for key, value in getattr(raw, name).items()}
        def parameter(value):
            if isinstance(value, FunctionalArray):
                self._array_lengths[str(value._base.value.decl().name())] = value.length
            return self._arrays.term(value)
        self._parameters = _map_tree(raw.pre, parameter)
        # Immutable type metadata belongs to the prepared template, not each
        # action in each history. Context translation changes AST ownership,
        # not these carrier widths or finite array lengths.
        self._carriers = _map_tree(self._parameters, lambda term:
            (self._array_lengths[str(term.decl().name())], term.range().size())
            if z3.is_array(term) else term.size())
        parameters = [term for value in self._parameters.values() for term in _leaves(value)]
        roots, self._dependencies = _inline_definitions(roots, raw.definitions, parameters)
        # One temporary AST container lets Z3 substitute the shared DAG once,
        # instead of once per output/state/goal. The application itself is never
        # asserted: only its original typed arguments reach the solver.
        bundle = z3.Function('_step_fields', *(term.sort() for term in roots), z3.BoolSort())(*roots)
        # Keep the shared DAG. Whole-datapath simplification can expand shared
        # terms before any query exists and is not needed to define a template.
        # Rewriting belongs to the selected query's budgeted solver.
        # Formal positions are local to this quantifier-free template. Replace
        # the encoder's named boundary atoms once; instances then use native
        # positional substitution, not a fresh arbitrary-expression map over
        # the full circuit on every step. Neither form is itself asserted.
        self._image = z3.substitute(bundle, *(
            (term, z3.Var(index, term.sort())) for index, term in enumerate(parameters)))
        check_preparation()
        self._array_image = None
        self._scalar_image = None
        self._normalize_instances = False
        self._arrays.freeze()

    def _boundary_value(self, name, kind, width):
        check_preparation()
        # A recursive constructor closure retains the template and its native
        # DAG after its last consumer. Recursing through a method owns no cycle.
        if isinstance(kind, types.Array) and kind.cells and kind.elem == types.INTEGER:
            self._uses_array_parameters = True
            return FunctionalArray.parameter(kind.cells, name + '_template', width)
        if isinstance(kind, types.Array):
            return [self._boundary_value(f'{name}[{i}]', kind.elem, width) for i in range(kind.cells)]
        return _symbolic_value(name, kind, width, '_template')

    @property
    def context(self):
        return self._image.ctx

    @property
    def state_names(self):
        """State carried by this view; a projection may omit unrelated fields."""
        return tuple(self._layout['state_post'])

    @property
    def hit_names(self):
        """Complete observations encoded by this view, not inferred false hits."""
        return tuple(self._layout['hits'])

    def normalized(self):
        """Normalize dependencies and preserve exact wiring at bound instances.

        Only unconditional identities are used. In particular no inferred
        entry range, sampled value or solver result is an assumption. The
        dependency index must be rebuilt: keeping pre-rewrite reads would
        retain payload/state cones even for predicates reduced to constants.
        Instances also fold supplied constants before naming successor state;
        computation stays shared instead of being inlined across the history.
        """
        check_preparation()
        view = copy(self)
        # Flattening each observed prefix repeats its operands, turning a
        # shared linear circuit into quadratic edges before any query exists.
        # Keep nested associative nodes; constant and control folding remain.
        view._image = _normalize_shared(self._image)
        _, view._dependencies = _inline_definitions(list(view._image.children()), ())
        view._array_image = None
        view._scalar_image = None
        view._normalize_instances = True
        return view

    def literal_false_hits(self, names):
        """Event roots already false in this exact template, without a query.

        This inspects existing formulas only: no state sample, proposed range
        invariant, solver answer or additional simplification participates.
        The caller still owns whether a private image may be omitted; this
        operation does not remove public declarations or award coverage.
        """
        return frozenset(name for name in names
                         if z3.is_false(self._image.arg(self._layout['hits'][name])))

    def abstract_state_functions(self, names, *, check=None, min_nodes=64):
        """A proof-only overapproximation of large pure scalar computations.

        Replace each selected BV/Boolean term by a fresh function of *every*
        positional variable it reads. Interpreting that function as the old
        term embeds every exact transition, for all states and inputs. Shared
        functions preserve congruence across histories; different terms never
        share an invented function. Only UNSAT may discharge a universal
        obligation: an abstract SAT model is neither a witness nor a refusal.

        No constraint, event or state root is dropped. Scheduler parameters
        outside ``names`` remain concrete. Arrays, free constants, existing
        functions and quantifiers are not abstracted. The threshold is only a
        proof-cost heuristic, never a domain or solver-budget restriction.
        """
        names = frozenset(names)
        if not names <= set(self.procedure.state):
            raise ValueError('proof abstraction names declared state only')
        if type(min_nodes) is not int or min_nodes < 1:
            raise ValueError('proof abstraction threshold is positive')
        parameters, allowed = [], 0
        for name, shape in self._parameters.items():
            for term in _leaves(shape):
                if name in names and (z3.is_bv(term) or z3.is_bool(term)):
                    allowed |= 1 << len(parameters)
                parameters.append(term)
        metadata, pending = {}, [(self._image, False)]
        arithmetic = 0
        nonlinear = False
        expensive = {z3.Z3_OP_BMUL, z3.Z3_OP_BUDIV, z3.Z3_OP_BUREM,
                     z3.Z3_OP_BSDIV, z3.Z3_OP_BSREM, z3.Z3_OP_BSMOD}
        while pending:
            if check is not None:
                check()
            term, visited = pending.pop()
            key = term.get_id()
            if key in metadata:
                continue
            if z3.is_var(term):
                metadata[key] = (1 << z3.get_var_index(term), 1, z3.is_bv(term) or z3.is_bool(term))
            elif not z3.is_app(term):
                metadata[key] = (0, 1, False)
            elif not visited:
                pending.append((term, True))
                pending.extend((child, False) for child in term.children())
            else:
                mask, size = 0, 1
                pure = (z3.is_bv(term) or z3.is_bool(term)) and term.decl().kind() != z3.Z3_OP_UNINTERPRETED
                for child in term.children():
                    reads, count, total = metadata[child.get_id()]
                    mask |= reads
                    size = min(min_nodes + 1, size + count)
                    pure &= total
                metadata[key] = mask, size, pure
                if pure and mask and not mask & ~allowed and z3.is_bv(term):
                    kind = term.decl().kind()
                    arithmetic += kind in expensive or kind in {z3.Z3_OP_BADD, z3.Z3_OP_BSUB}
                    costly = (kind in expensive and term.size() >= min_nodes // 4
                              and all(metadata[child.get_id()][0] for child in term.children()))
                    if costly:
                        nonlinear = True
                        metadata[key] = mask, min_nodes + 1, pure
        # Small control circuits usually benefit more from the exact finite
        # solver than from a second function-valued product. Select using only
        # compiler formula structure, never task names, values or verdicts.
        if arithmetic < max(1, min_nodes // 4) and not nonlinear:
            return None
        pairs, seen, pending = [], set(), list(self._image.children())
        while pending:
            if check is not None:
                check()
            term = pending.pop()
            key = term.get_id()
            if key in seen:
                continue
            seen.add(key)
            mask, size, pure = metadata[key]
            if pure and size > min_nodes and mask and not mask & ~allowed:
                args = [z3.Var(i, value.sort()) for i, value in enumerate(parameters) if mask & (1 << i)]
                function = z3.FreshFunction(*(arg.sort() for arg in args), term.sort())
                pairs.append((term, function(*args)))
            elif z3.is_app(term):
                pending.extend(term.children())
        if not pairs:
            return None
        view = copy(self)
        view._image = z3.substitute(self._image, *pairs)
        view._array_image = None
        view._scalar_image = None
        return view

    def specialize_inputs(self, values):
        """Substitute declared constant inputs once, retaining their exact premises.

        This view is the original relation AND the supplied input equalities,
        not a relation that silently ignores those inputs. In particular an
        incompatible later binding is unsatisfiable. No state invariant or
        scheduler choice is inferred. Dependency masks remain conservative;
        the added equalities are roots for projection as well as solving.
        """
        if not set(values) <= set(self.procedure.inputs):
            raise ValueError('transition specialization binds inputs, not state or unknown names')
        kinds = dict(self.procedure.semantics.input_types)
        fixed = {name: _tree(encode_concrete_value(value, self.environment[name], name, kind=kinds[name]))
                 for name, value in values.items()}
        arguments, premises, mask = [], [], 0
        for name, shape in self._parameters.items():
            replacements = tuple(_leaves(fixed[name])) if name in fixed else tuple(_leaves(shape))
            for parameter, value in zip(_leaves(shape), replacements, strict=True):
                variable = z3.Var(len(arguments), parameter.sort())
                if name not in fixed:
                    arguments.append(variable)
                    continue
                if value.size() > parameter.size():
                    raise EncodingError(f'{name} exceeds the prepared transition carrier', kind='width')
                value = value if value.ctx == self.context else value.translate(self.context)
                value = z3.SignExt(parameter.size() - value.size(), value)
                premises.append(variable == value)
                mask |= 1 << len(arguments)
                arguments.append(value)
        image = z3.substitute_vars(self._image, *arguments)
        roots = list(image.children())
        constraint = self._layout['constraint']
        roots[constraint] = z3.And(roots[constraint], *premises)
        view = copy(self)
        view._image = image.decl()(*roots)
        view._array_image = None
        view._scalar_image = None
        # An omitted specialized input is already fixed, not another fresh
        # coordinate for a model constructor to guess. Keep the equality in
        # the image: an explicit incompatible binding must still be UNSAT.
        view._fixed_inputs = {**getattr(self, '_fixed_inputs', {}), **fixed}
        dependencies = list(self._dependencies)
        dependencies[constraint] |= mask
        view._dependencies = tuple(dependencies)
        return view

    def translate(self, context):
        """Move one prepared transition, not a fully unrolled query.

        A physical query can own its context from construction through decode.
        Transfer the immutable template and boundary types once; all of that
        query's instances then stay in the destination's memory lifetime.
        """
        terms = z3.AstVector(ctx=self.context)
        terms.push(self._image)
        for value in self._parameters.values():
            for term in _leaves(value):
                terms.push(term)
        moved = iter(terms.translate(context))
        view = copy(self)
        view._image = next(moved)
        view._array_image = None
        view._scalar_image = None

        view._parameters = _map_tree(self._parameters, lambda _value: next(moved))
        return view

    def project(self, groups, *, outputs=None, state_reads=()):
        """Group exact occurrences and optionally close an observation cone.

        Source goals can occur in several helper calls or unrolled iterations.
        Their public hit is the disjunction of those exact occurrence roots.
        With ``outputs=None`` every output and successor is retained. Otherwise
        retain the selected outputs and the conservative state dependency closure of
        those outputs, requested hits, *all* restrictions, success/error roots
        and the caller's explicit inter-step state reads. Unobserved total
        successor definitions cannot constrain this closed relation and have
        a unique completion. No execution path or scheduler choice is removed.

        The result is an internal query view, not a complete replay state. The
        caller must name every state read it makes between transitions.
        Dependency masks follow this prepared image. ``normalized()`` rebuilds
        them after simplification; an unnormalized view may retain extra reads
        but cannot omit a predecessor.
        """
        return project_templates((self,), groups, outputs=outputs, state_reads=state_reads)[0]

    def project_local(self, groups, *, outputs=()):
        """Exact observations of ONE activation, without successor-state work.

        Unlike a history projection, this view is not composed across steps.
        All entry restrictions, success/error roots and selected occurrence
        expressions remain unchanged. Unobserved successor names are fresh,
        total definitions and have a unique extension for every legal entry;
        neither their equations nor their transitive next-state dependencies
        belong in a query that observes no successor. All entry parameters
        remain available, including arbitrary state and scheduler choices.
        """
        check_preparation()
        groups = {name: tuple(dict.fromkeys(members)) for name, members in groups.items()}
        return self._project(groups, tuple(outputs), set())

    def _project(self, groups, outputs, states):
        """Select immutable roots after the shared dependency closure is complete."""
        layout = self._layout
        if outputs is not None:
            post = {name: layout['post'][name] for name in outputs}
            layout = {**layout, 'post': post,
                      'state_post': {name: value for name, value in layout['state_post'].items()
                                     if name in states}}

        def disjunction(members):
            terms = [self._image.arg(self._layout['hits'][member]) for member in members]
            return (terms[0] if len(terms) == 1 else z3.Or(*terms)) if terms else z3.BoolVal(False, ctx=self.context)

        hit_dependencies = {}
        for name, members in groups.items():
            mask = 0
            for member in members:
                mask |= self._dependencies[self._layout['hits'][member]]
            hit_dependencies[name] = mask
        return self._with_hits({name: disjunction(members) for name, members in groups.items()},
                               layout, hit_dependencies)

    def _with_hits(self, hits, layout, hit_dependencies):
        roots, dependencies, indices = [], [], {}

        def append(term, mask):
            key = term.get_id()
            if key not in indices:
                indices[key] = len(roots)
                roots.append(term)
                dependencies.append(mask)
            else:
                dependencies[indices[key]] |= mask
            return indices[key]

        def project(shape):
            return append(self._image.arg(shape), self._dependencies[shape])

        view = copy(self)
        view._layout = _map_tree(
            {name: shape for name, shape in layout.items() if name != 'hits'}, project)
        view._layout['hits'] = {}
        for name, term in hits.items():
            view._layout['hits'][name] = append(term, hit_dependencies[name])
        view._image = z3.Function('_step_fields', *(term.sort() for term in roots),
                                  z3.BoolSort(ctx=self.context))(*roots)
        view._array_image = None
        view._scalar_image = None
        view._dependencies = tuple(dependencies)
        return view

    def successor_deltas(self):
        """Omit only literal identity successors, retaining all other roots.

        This transition view must be composed by merging ``state_post`` into
        the supplied boundary. No state/input restriction or event is removed.
        Identity is equality to the SAME typed positional entry parameter,
        never a sampled value or an inferred invariant. Unlike observation
        projection, this needs no multi-step dependency closure.
        """
        positions, offset = {}, 0
        for name, shape in self._parameters.items():
            leaves = tuple(_leaves(shape))
            positions[name] = tuple(z3.Var(offset + i, term.sort()) for i, term in enumerate(leaves))
            offset += len(leaves)
        state = {}
        for name, shape in self._layout['state_post'].items():
            check_preparation()
            roots = tuple(self._image.arg(index) for index in _leaves(shape))
            if not all(z3.eq(root, entry) for root, entry in zip(roots, positions[name], strict=True)):
                state[name] = shape
        return self._with_hits(
            {name: self._image.arg(index) for name, index in self._layout['hits'].items()},
            {**self._layout, 'state_post': state},
            {name: self._dependencies[index] for name, index in self._layout['hits'].items()})

    def encode(self, pre=None, *, tag='step', check=None):
        return finish_construction(self.encode_steps(pre, tag=tag), check)

    def encode_steps(self, pre=None, *, tag='step', _facts=None):
        fields = yield from self.instantiate_steps(pre, tag=tag, _facts=_facts)
        equalities = []
        successors = yield from _bind_successors(fields.state_post, equalities, {}, tag,
                                                   wires=self._normalize_instances, _facts=_facts)
        return replace(fields, state_post=successors,
                       constraint=z3.And(fields.constraint, *equalities),
                       definitions=tuple(equalities))

    def instantiate(self, pre=None, *, tag='step', check=None):
        """Read this activation's exact expressions without naming successors.

        Boundary predicates need expressions over the entry state, not fresh
        successor equations immediately substituted away again. This shares
        encode's complete instantiation and restrictions; ordinary histories
        still use encode to keep computations named across transitions.
        """
        return finish_construction(self.instantiate_steps(pre, tag=tag), check)

    def _instantiate_scalar(self, arguments):
        """Fold constant parameters before inserting this boundary's DAG.

        The normalized image is already simplified. Rewriting it again after
        binding symbolic state repeatedly traverses the same datapath (and
        possibly its predecessors). Keep only the last literal specialization;
        it contains positional parameters, never a model or symbolic boundary.
        A copied/projected/translated view must match its own image as well.
        """
        constants = tuple((index, value.as_long()) for index, value in enumerate(arguments)
                          if z3.is_bv_value(value))
        image = self._image
        if constants:
            cached = self._scalar_image
            if cached is not None and cached[0] is image and cached[1] == constants:
                image = cached[2]
            else:
                image = z3.substitute(image, *((z3.Var(index, arguments[index].sort()), arguments[index])
                                              for index, _value in constants))
                check_preparation()
                image = _normalize_shared(image)
                check_preparation()
                self._scalar_image = self._image, constants, image
        image = z3.substitute_vars(image, *arguments)
        check_preparation()
        return list(image.children())

    def instantiate_steps(self, pre=None, *, tag='step', _facts=None):
        yield
        supplied = {**getattr(self, '_fixed_inputs', {}), **(pre or {})}

        def fresh(value):
            if z3.is_array(value):
                # A caller without recorded state still denotes every cell.
                # Shape metadata, not the address carrier, owns the length.
                length = self._array_lengths[str(value.decl().name())]
                width = value.range().size()
                result = FunctionalArray.symbolic(length, str(z3.FreshConst(z3.BitVecSort(1), tag)), width,
                    -(1 << (width - 1)), (1 << (width - 1)) - 1)
                return result.translate(self.context)
            return z3.FreshConst(value.sort(), tag)

        def boundary(value):
            if isinstance(value, FunctionalArray):
                return value.translate(self.context)
            return value if value.ctx == self.context else value.translate(self.context)

        arguments = {name: _map_tree(supplied[name], boundary) if name in supplied
                     else _map_tree(value, fresh)
                     for name, value in self._parameters.items()}
        substitutions = []
        for name, shape in self._carriers.items():
            for carrier, new in _bind_tree(shape, arguments[name]):
                if isinstance(carrier, tuple):
                    length, width = carrier
                    if isinstance(new, list):
                        new = FunctionalArray.from_cells(new)
                    if not isinstance(new, FunctionalArray) or new.length != length:
                        raise EncodingError(f'{name} has another array shape', kind='shape')
                    if new.element_width > width:
                        raise EncodingError(f'{name} exceeds the prepared transition carrier', kind='width')
                    substitutions.append(new.widen(width))
                    continue
                width = new.size()
                if carrier < width:
                    raise EncodingError(f'{name} exceeds the prepared transition carrier', kind='width')
                if carrier > width:
                    new = (z3.BitVecVal(new.as_signed_long(), carrier, ctx=new.ctx)
                           if z3.is_bv_value(new) else z3.SignExt(carrier - width, new))
                substitutions.append(new)
        # Instantiate one shared DAG. Ordinary views leave ground rewriting to
        # the solver; normalized views fold the supplied boundary constants.
        # Successor equations still name computations between transitions.
        if (_facts is None and self._normalize_instances and not self._arrays.constructors
                and all(z3.is_expr(value) and not z3.is_array(value) for value in substitutions)):
            image = self._instantiate_scalar(substitutions)
        elif (_facts is not None or self._normalize_instances or self._arrays.constructors
                or any(isinstance(value, FunctionalArray) for value in substitutions)):
            if self._array_image is None:
                self._array_image = self._arrays.prepare(self._image)
            # The prepared interpreter folds closed template regions before
            # inserting symbolic arguments and binds finite arrays lazily;
            # those array recipes cannot use the scalar native substitution.
            image = yield from self._array_image.instantiate_steps(substitutions, _facts=_facts)
        else:
            image = z3.substitute_vars(self._image, *substitutions)
            image = list(image.children())
        yield

        fields = _map_tree(self._layout, image.__getitem__)
        fields['pre'] = arguments
        return TransitionEncoding(**fields)
