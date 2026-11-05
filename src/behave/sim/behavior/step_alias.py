"""Source-level points-to authority for step-minted mutable aliases.

The lowering IR has value semantics for arrays, while admitted Python source can
share one mutable object through several names.  This module finds the first
source step that could observe that mismatch.  It consumes source facts,
already-derived array shapes, and capability bounds; it deliberately does not
import the lowering or IR modules.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass

from .budgets import UNROLL_LIMIT, helper_activation_exceeds
from .extract import LoweringError, ScopeResolver, SourceModel
from .source_calls import (
    CallBindingError,
    bind_helper_arguments,
    has_plain_positional_arity,
    is_plain_append_call,
    is_plain_extend_call,
    is_plain_pop_call,
    record_copy_source,
    record_items,
)
from .source_syntax import annotation_target_expressions, local_annotations
from ..symbolic.value_contract import contract_is_deeply_immutable
from .source_values import static_truth

__all__ = ["AliasHazard", "alias_query_identity", "step_alias_hazard"]


@dataclass(frozen=True)
class AliasHazard:
    """One reason the source-to-value-IR alias boundary is not exact."""

    kind: str
    written: str
    other: str


def alias_query_identity(model, array_names, loop_spans, array_types, *,
                         state_contracts=None, borrowed_arguments=None):
    """Stable semantic identity of one source alias-analysis request.

    The frontend lowers twice only because the first procedure supplies state
    ranges for the second.  Alias freedom may be reused across those passes
    exactly when the source, chosen step, array types and every proved loop
    span agree.  AST object identities cannot establish that: each pass parses
    a fresh tree, so the identity uses source structure and loop order instead.
    It is deliberately local to one frontend operation, not a persistent cache
    key for materialised dependencies.
    """
    if type(model) is not SourceModel:
        raise TypeError("an alias query needs one parsed SourceModel")
    loops = []
    for node in ast.walk(model.step):
        if not isinstance(node, (ast.For, ast.While)):
            continue
        span = loop_spans.get(id(node))
        loops.append(None if span is None else tuple(span))
    return (
        "source-alias/v4",
        model.file,
        model.cls.name,
        model.step.name,
        ast.dump(model.tree, annotate_fields=True, include_attributes=False),
        tuple(sorted(array_names)),
        tuple(sorted(array_types.items())),
        tuple(sorted((state_contracts or {}).items())),
        tuple(loops),
        tuple(tuple(sorted((borrowed_arguments or {}).get(id(node), {}).items()))
              for node in ast.walk(model.tree) if isinstance(node, ast.Call)),
    )


def step_alias_hazard(model, array_names, resolver=None, *, loop_spans=None,
                       array_types=None, state_contracts=None,
                       source_value_budget=None, borrowed_arguments=None):
    """The first step-minted alias that a later in-place write may observe.

    A points-to analysis, not pairwise edges (review 41: rebinding one name
    deleted its edges and the sharing the other two names still had went
    with them). Every allocation site is a token; a name points to a set of
    tokens; a bare bind copies the set and a rebind replaces only that
    name's; branches -- statements and conditional expressions alike --
    fork the environment and join by per-name union; break, continue and
    return carry their own exit states, so nothing after them is analyzed
    and a loop's else belongs to normal exhaustion only; loops run to a
    fixpoint over the joined head; helpers and module functions inline with
    parameters in their own scope and returns carried back; a call nothing
    resolves passes its argument tokens through, a known-pure builtin
    builds fresh. The hazard is an in-place write through a name whose
    tokens some other live name shares.  Construction attests an alias-free
    entry state.  The transfer also requires every normal exit to retain an
    alias-free state when mutation is possible, making that entry fact
    inductive without replaying earlier object graphs. A step with no mutable
    writes may retain sharing: reading and rebinding alone cannot observe it.
    """
    from ..symbolic import typecheck as types_mod
    from .source_sequences import static_iterable

    resolver = resolver or ScopeResolver(model)
    unevaluated = {id(node) for annotation in local_annotations(model.tree)
                   for node in ast.walk(annotation)}
    loop_spans = dict(loop_spans or {})
    array_types = dict(array_types or {})
    state_contracts = state_contracts or {}
    borrowed_arguments = borrowed_arguments or {}
    borrowed_roots = {}

    def storage_root(root):
        while root in borrowed_roots:
            root = borrowed_roots[root]
        return root

    #: Builtins by what their result carries (review 46: reversed(x) is a
    #: fresh iterator whose elements are still x's).
    SCALAR_CALLS = frozenset({
        "range", "len", "sum", "abs", "int", "bool", "float", "str",
        "bin", "hex", "oct", "round", "divmod", "pow", "ord", "chr",
        "all", "any"})
    CARRIER_CALLS = frozenset({
        "reversed", "sorted", "list", "tuple", "set", "frozenset",
        "bytes", "bytearray", "enumerate", "zip", "iter"})

    class _Hazard(Exception):
        def __init__(self, written, other, *, kind="unverified"):
            super().__init__(written)
            self.result = AliasHazard(kind, written, other)

    def field_of(node):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            return node.attr
        return None

    # Points-to keys carry lexical identity (review 42: a step local named
    # like a field overwrote the field's entry and the sharing was
    # forgotten). A field is ("field", name); a local is ("local", frame,
    # name), with one frame per step or inlined call, covering every local
    # binding and cleaned up structurally.
    _frame_counter = [0]
    #: The lexical owner (a FunctionDef) of each frame, and its enclosing
    #: frame, so a free name in an inlined callee resolves to the enclosing
    #: function's binding rather than being lost (review 49: a nested
    #: function returning an enclosing local that aliased a field).
    frame_owner: dict = {}
    frame_parent: dict = {}
    #: A comprehension frame's own locals are its targets; every other name
    #: is a free variable resolved in the enclosing frame (review 50: a
    #: comprehension lost an enclosing local that aliased a field).
    frame_comp_targets: dict = {}
    #: Each lexical function's current activation frame, so an inlined
    #: callee's free names resolve through its lexical enclosing function's
    #: activation, not the dynamic caller's frame (review 51: a closure
    #: called from inside a comprehension read the comprehension's target).
    activation: dict = {}

    def new_frame(owner=None, parent=None):
        _frame_counter[0] += 1
        f = _frame_counter[0]
        if owner is not None:
            frame_owner[f] = owner
            frame_parent[f] = parent
        return f

    def frame_key(name, frame):
        """The ``("local", frame, name)`` key at which ``name`` lives,
        following the lexical frame chain for a closure-captured free
        name. Falls back to the current frame where no owner is known."""
        f = frame
        while True:
            if f in frame_comp_targets:
                if name in frame_comp_targets[f]:
                    return ("local", f, name)
                nxt = frame_parent.get(f)
                if nxt is None:
                    break
                f = nxt
                continue
            if f in frame_owner:
                own = frame_owner[f]
                resolver._scope(own)
                if name in resolver._locals[id(own)]:
                    return ("local", f, name)
                nxt = frame_parent.get(f)
                if nxt is None:
                    break
                f = nxt
                continue
            break
        return ("local", frame, name)

    _field_reads_memo = {}

    def field_reads_of(fn, seen=None):
        """Fields ``fn`` may read, transitively through known callees."""
        if id(fn) in _field_reads_memo:
            return _field_reads_memo[id(fn)]
        seen = seen or set()
        if id(fn) in seen:
            return frozenset()
        seen.add(id(fn))
        out = set()
        for node in ast.walk(fn):
            if id(node) in unevaluated:
                continue
            name = field_of(node) if isinstance(node, ast.Attribute) else None
            if name is not None and isinstance(node.ctx, ast.Load):
                out.add(("field", name))
            if isinstance(node, ast.Call):
                callee = None
                if isinstance(node.func, ast.Attribute):
                    method = field_of(node.func)
                    if method is not None:
                        mkind, callee = resolver.resolve_method(fn, method)
                        if mkind != "method":
                            callee = None
                elif isinstance(node.func, ast.Name):
                    rkind, callee = resolver.resolve_call(fn, node.func.id)
                    if rkind not in ("nested", "module", "alias"):
                        callee = None
                if callee is not None:
                    out |= field_reads_of(callee, seen)
        _field_reads_memo[id(fn)] = frozenset(out)
        return _field_reads_memo[id(fn)]

    def reads_of(stmt, frame, owner):
        """Keys ``stmt`` may read: its own frame's locals, fields, and the
        field closures of everything it calls, resolved lexically."""
        out = set()
        for node in ast.walk(stmt):
            if id(node) in unevaluated:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                    and node.id != "self":
                out.add(frame_key(node.id, frame))
            name = field_of(node) if isinstance(node, ast.Attribute) else None
            if name is not None and isinstance(node.ctx, ast.Load):
                out.add(("field", name))
            if isinstance(node, ast.Call):
                callee = None
                if isinstance(node.func, ast.Attribute):
                    method = field_of(node.func)
                    if method is not None:
                        mkind, callee = resolver.resolve_method(owner, method)
                        if mkind != "method":
                            callee = None
                elif isinstance(node.func, ast.Name):
                    rkind, callee = resolver.resolve_call(owner,
                                                          node.func.id)
                    if rkind not in ("nested", "module", "alias"):
                        callee = None
                if callee is not None:
                    out |= field_reads_of(callee)
        return frozenset(out)

    #: Object graph of the source values.  ``positions[obj][slot]`` is the
    #: set of mutable object identities the slot may contain.  Keeping slots
    #: is essential: ``[row, row]`` has two observable paths to one object,
    #: whereas ``[row]`` has one.  Collapsing both to a set was the remaining
    #: nested-array false accept.
    positions = {}
    # Reverse edges are the exact transpose of ``positions``.  Mutation
    # checks ask whether one object has another live source occurrence; walking
    # from that object to its few ancestors is substantially cheaper than
    # re-walking every descendant of every live root after each store.
    parents = {}
    mutable_tokens = set()
    append_slots = {}
    append_next_slot = {}
    # The object graph grows monotonically.  Loop fixed-point detection needs
    # to know whether one transfer added a fact, but copying the complete graph
    # on every iteration makes a finite nested loop quadratic in everything
    # discovered before it.  All graph writes go through the helpers below;
    # one revision number is therefore an exact change detector.
    graph_revision = [0]

    def graph_changed():
        graph_revision[0] += 1

    def remember_mutable(token):
        if token not in mutable_tokens:
            mutable_tokens.add(token)
            graph_changed()

    def remember_mutables(tokens):
        unseen = set(tokens) - mutable_tokens
        if unseen:
            mutable_tokens.update(unseen)
            graph_changed()

    # Exact scalar loop targets are a small, separate fact from points-to
    # sets.  A finite ``for i in range(...)`` assigns a concrete index on each
    # abstract iteration; retaining only the name makes an indexed state store
    # appear to write every row and falsely aliases distinct fresh rows.
    exact_indices = {}
    _missing_index = object()
    #: The old unordered view remains useful for iteration over a value whose
    #: concrete index is not known.  It is derived from the positional graph,
    #: never a second authority.
    contents = {}
    #: Positional element tokens for a starless tuple or list literal, so a
    #: destructure through one known allocation binds element-wise
    #: (review 47's fallout: `sa, ea, ma = _fp32_fields(a)` through the
    #: library's `return sign, exp, mant` gave the three targets one
    #: shared set, and a later `e_out += 1` read as a write the others
    #: observe). A site revisited with a different arity, or reached
    #: through a Starred, drops back to the unordered union.
    ordered_contents = {}

    def remember_at(token, slot, ends):
        ends = frozenset(ends)
        cells = positions.setdefault(token, {})
        before = cells.get(slot, frozenset())
        after = before | ends
        if after != before:
            cells[slot] = after
            for child in after - before:
                parents.setdefault(child, set()).add((token, slot))
            graph_changed()
        if ends:
            before = contents.get(token, frozenset())
            after = before | ends
            if after != before:
                contents[token] = after
                graph_changed()

    def remember(token, ends):
        # An operation that preserves elements but whose exact slot map is
        # unavailable still contributes one wildcard occurrence.  Exact
        # literals, subscripts and appends use ``remember_at`` instead.
        if ends:
            remember_at(token, ("*",), ends)

    def remember_occurrences(token, groups, label):
        """Retain distinct, index-unknown occurrences in a shallow copy.

        Python sequence operations copy references, not their referents.  An
        exact numeric layout is unnecessary for the alias proof, but distinct
        source occurrences must not collapse into one set: ``[row] * 2`` has
        two observable paths to ``row``.  Slots whose first component is
        ``"*"`` are conservatively selectable by every source index.
        """
        for index, ends in enumerate(groups):
            remember_at(token, ("*", label, index), ends)

    def occurrence_groups(tokens):
        """Element occurrences of one possible source sequence.

        Slots of one known carrier are simultaneous and remain distinct.
        Several carrier tokens denote alternative control-flow values, so
        their elements are joined into one possible occurrence instead of
        being mistaken for simultaneously live aliases.
        """
        tokens = tuple(tokens)
        if len(tokens) != 1:
            joined = elements_of(tokens)
            return [] if not joined else [joined]
        return [children for _slot, children in _slot_items(tokens[0])
                if children]

    def literal_repeat_count(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if (isinstance(node, ast.UnaryOp)
                and isinstance(node.op, (ast.UAdd, ast.USub))
                and isinstance(node.operand, ast.Constant)
                and type(node.operand.value) is int):
            value = node.operand.value
            return value if isinstance(node.op, ast.UAdd) else -value
        return None

    def remember_ordered(token, ordered):
        known = ordered_contents.get(token)
        if known is False:
            return
        if known is None:
            ordered_contents[token] = [frozenset(e) for e in ordered]
            graph_changed()
        elif len(known) == len(ordered):
            joined = [have | frozenset(new_ends)
                      for have, new_ends in zip(known, ordered)]
            if joined != known:
                ordered_contents[token] = joined
                graph_changed()
        else:
            ordered_contents[token] = False
            graph_changed()
        for index, ends in enumerate(ordered):
            remember_at(token, index, ends)

    def elements_of(ends):
        out = set()
        for token in ends:
            out.update(contents.get(token, ()))
        return frozenset(out)

    def _slot_items(token):
        return tuple(positions.get(token, {}).items())

    def forget_removed_positions(receivers):
        """A surviving child may have shifted to a different logical index.

        Keep the monotone may-points-to graph, but make those children visible
        through every subsequent index. Keeping only the old numbered slots
        misses a write to a moved child through an existing external alias.
        Ordered destructuring also cannot reuse the pre-removal positions.
        """
        for token in receivers:
            remember(token, elements_of((token,)))
            if ordered_contents.get(token) is not False:
                ordered_contents[token] = False
                graph_changed()

    def _all_occurrences(root, roots):
        """Every object occurrence reachable from one live source name."""
        out = []
        # Keep the ancestor set per path, not one global visited set.  A DAG
        # deliberately contributes two occurrences of its shared child; a
        # cycle contributes the closing occurrence once and is not followed
        # forever.  Construction-time cycles are already rejected by the
        # runtime witness, but a step can create one before mutating it.
        stack = [(token, (), frozenset()) for token in roots]
        while stack:
            token, path, ancestors = stack.pop()
            out.append((root, path, token))
            if token in ancestors:
                continue
            below = ancestors | {token}
            for slot, children in _slot_items(token):
                for child in children:
                    stack.append((child, path + (slot,), below))
        return out

    def _direct_roots(env, roots):
        direct = {}
        for root in roots:
            for token in env.get(root, ()):
                direct.setdefault(token, []).append(root)
        return direct

    def _live_occurrences(token, direct):
        """Yield every live ``(root, path)`` occurrence of ``token``.

        This is the reverse of :func:`_all_occurrences`.  Ancestors are kept
        per path so two paths through a DAG remain two observable aliases,
        while a cycle contributes its closing occurrence once and terminates.
        """
        stack = [(token, (), frozenset())]
        while stack:
            current, suffix, ancestors = stack.pop()
            for root in direct.get(current, ()):
                yield root, suffix
            if current in ancestors:
                continue
            above = ancestors | {current}
            for parent, slot in parents.get(current, ()):
                stack.append((parent, (slot,) + suffix, above))

    def _index_slots(node, token, frame):
        cells = positions.get(token, {})
        wildcard = tuple(
            slot for slot in cells
            if isinstance(slot, tuple) and slot and slot[0] == "*")
        index = _missing_index
        if isinstance(node, ast.Constant) and type(node.value) is int:
            index = node.value
        elif isinstance(node, ast.Name):
            index = exact_indices.get(
                frame_key(node.id, frame), _missing_index)
        if type(index) is int:
            numeric = sorted(slot for slot in cells if type(slot) is int)
            if index < 0 and numeric:
                index += max(numeric) + 1
            selected = []
            if index in cells:
                selected.append(index)
            # A wildcard denotes an element whose exact slot was lost, not an
            # element that fixed-index reads may ignore.  Concatenation and
            # other carrier-preserving operations use this form; omitting it
            # let a subsequent ``joined[0][i] = ...`` mutate a source alias
            # without the points-to authority seeing the shared object.
            selected.extend(wildcard)
            return tuple(selected)
        # A dynamic index may select any represented slot.  Wildcards are
        # retained because an opaque carrier conversion may be the only fact
        # available, and fail-closed is preferable to inventing freshness.
        return tuple(cells)

    def _origins(node, env, frame):
        """Possible ``(root, path, object)`` occurrences denoted by ``node``.

        This is intentionally side-effect free.  The ordinary evaluator has
        already visited the expression in Python order; this walk only relates
        its returned object identities to source locations.
        """
        if isinstance(node, ast.Name):
            root = frame_key(node.id, frame)
            return [(root, (), token) for token in env.get(root, ())]
        name = field_of(node)
        if name is not None:
            root = ("field", name)
            return [(root, (), token) for token in env.get(root, ())]
        if isinstance(node, ast.Subscript) and not isinstance(
                node.slice, ast.Slice):
            out = []
            for root, path, token in _origins(node.value, env, frame):
                for slot in _index_slots(node.slice, token, frame):
                    for child in positions.get(token, {}).get(slot, ()):
                        out.append((root, path + (slot,), child))
            return out
        if isinstance(node, ast.IfExp):
            return (_origins(node.body, env, frame)
                    + _origins(node.orelse, env, frame))
        if isinstance(node, ast.BoolOp):
            return [item for value in node.values
                    for item in _origins(value, env, frame)]
        return []

    def join_env(*envs):
        accumulated = {}
        for env in envs:
            for name, tokens in env.items():
                accumulated.setdefault(name, set()).update(tokens)
        return {name: frozenset(tokens)
                for name, tokens in accumulated.items()}

    def abstract_state(env):
        """A value snapshot for detecting a loop transfer fixed point.

        The environment alone is insufficient: an iteration can append another
        reference to a retained container without changing which token names
        that container.  The object graph is monotone and every mutation bumps
        ``graph_revision``, so the revision captures the same fact without
        copying the graph accumulated by all enclosing iterations.
        """
        return dict(env), graph_revision[0]

    comparison_truth = {}

    def fixed_comparison_truth(left, operator, right):
        """Static truth of one source comparison, charged once per occurrence.

        A retained loop revisits the same AST nodes under many abstract
        iterations.  Static truth here has no environment-dependent callback,
        so recomputing it cannot improve the answer; it only spends the shared
        source-value budget again.
        """
        key = (id(left), id(operator), id(right))
        if key not in comparison_truth:
            pair = ast.Compare(
                left=left, ops=[operator], comparators=[right])
            comparison_truth[key] = static_truth(
                pair, budget=source_value_budget)
        return comparison_truth[key]

    def span_of(node, owner):
        """The production loop span, with literal-sequence test support."""
        known = loop_spans.get(id(node))
        if known is not None:
            return known
        iterable = getattr(node, "iter", None)
        # The production caller supplies every span proved while lowering.
        # Keep this analysis sound and usable on its own as well: a literal
        # sequence has an exact trip count regardless of the values (and
        # effects) of its elements.  Falling back to the global unroll ceiling
        # for ``for _ in []`` used to manufacture 256 impossible iterations,
        # quadratically growing the points-to environment and obscuring the
        # zero-iteration path the analysis was meant to preserve.
        if isinstance(iterable, (ast.List, ast.Tuple)):
            length = len(iterable.elts)
            return (length, length)
        if isinstance(node, ast.While):
            raise _Hazard("while", "trip-plan-unavailable")
        return (0, UNROLL_LIMIT)

    def detached_key(key):
        return ("iterator-detached", key)

    def reattached_key(key):
        return ("iterator-reattached", key)

    has_mutation = False

    def check_mut(name, env, live, occurrences=None):
        """Refuse exactly when a mutable object has another live occurrence."""
        nonlocal has_mutation
        tokens = env.get(name, frozenset()) if name is not None else frozenset()
        writes = list(occurrences or
                      [(name, (), token) for token in tokens])
        if not writes:
            return
        live_roots = {
            key for key in env
            if key[0] in {"field", "eval"} or key in live or key == name
        }
        for key in env:
            if key[0] != "iterator":
                continue
            source = key[1]
            # A live iterator reads the original binding until its first
            # replacement. Afterwards the IR holds a value image: a write
            # through a surviving/re-attached alias would make it stale.
            if source is None or env.get(detached_key(key)):
                live_roots.add(key)
            else:
                live_roots.add(source)
        direct = _direct_roots(env, live_roots)
        for root, path, token in writes:
            if token not in mutable_tokens:
                continue
            has_mutation = True
            def observes_write(item):
                if item[1] == path and storage_root(item[0]) == storage_root(root):
                    return False
                observer = item[0]
                if (observer[0] == "iterator" and root == observer[1]
                        and not env.get(reattached_key(observer))):
                    # Joining a replaced and an unreplaced binding must not
                    # pair the latter's object with the former's saved image.
                    # A write through the original name still uses live
                    # storage unless it has actually been rebound to a held
                    # object via an alias. Other aliases never get this rule.
                    return False
                return True

            other = next((item for item in _live_occurrences(token, direct)
                          if observes_write(item)), None)
            if other is not None:
                written = name[-1] if name is not None else "an array"
                raise _Hazard(
                    written, other[0][-1], kind="mutation")

    _eval_counter = [0]

    def hold_for_evaluation(env, tokens, label):
        """Keep a source reference live while later operands are evaluated.

        SolverIR arrays are values, whereas Python's evaluation stack holds an
        object reference.  The lowerer may copy an earlier operand before a
        later source operation runs; that is exact for rebinding, but not when
        the later operation mutates the held object.  Giving the temporary
        reference a points-to root lets the ordinary mutation check distinguish
        those two cases without a spelling-specific effect rule.
        """
        if not tokens:
            return None
        _eval_counter[0] += 1
        key = ("eval", _eval_counter[0], label)
        env[key] = frozenset(tokens)
        return key

    def release_evaluation(env, key):
        if key is not None:
            env.pop(key, None)

    def written_name(target, frame):
        node = target
        while isinstance(node, ast.Subscript):
            node = node.value
        if node is target:
            return None
        if isinstance(node, ast.Name):
            return frame_key(node.id, frame)
        name = field_of(node)
        return ("field", name) if name is not None else None

    def mutate_target(target, value_ends, env, depth, frame, live, ctx,
                      owner, *, record=True, deleting=False):
        """Evaluate and record one subscript assignment target.

        Python evaluates the right-hand side before the target primary and
        index.  The caller has already evaluated the RHS; this function now
        visits the target in source order, checks the actual container object
        being mutated, and records an array-valued replacement in its slot.
        """
        if not isinstance(target, ast.Subscript):
            return False
        receiver = ends_of(target.value, env, depth, frame, live, ctx, owner)
        origins = _origins(target.value, env, frame)
        parts = ([target.slice.lower, target.slice.upper, target.slice.step]
                 if isinstance(target.slice, ast.Slice) else [target.slice])
        for part in parts:
            if part is not None:
                ends_of(part, env, depth, frame, live, ctx, owner)
        if deleting and set(origins) != set(_origins(target.value, env, frame)):
            raise _Hazard("delete receiver", "index rebinding", kind="mutation")
        root_name = written_name(target, frame)
        check_mut(root_name, env, live, origins or [
            (None, (), token) for token in receiver])
        if deleting:
            forget_removed_positions(receiver)
            return True
        if not record:
            return True
        if isinstance(target.slice, ast.Slice):
            # Slice assignment changes a run-time-selected interval.  The
            # lowering currently admits only shapes it can make exact; the
            # alias authority conservatively records the RHS at a wildcard
            # slot rather than pretending which old positions survive.
            for token in receiver:
                remember_at(token, ("slice", id(target)), value_ends)
            return True

        placements = [
            (container, slot)
            for _root, _path, container in (origins or [
                (None, (), item) for item in receiver])
            for slot in _index_slots(target.slice, container, frame)
        ]

        def unaliased_flat_value(token):
            """Whether a fresh flat carrier exists only on the value stack.

            A store through a dynamic index chooses one destination.  A plain
            may-points-to union would put the *same* allocation token in every
            possible slot and later mistake those mutually exclusive worlds
            for aliases.  For a fresh carrier with no mutable descendants and
            no source-visible alias, give each destination alternative its own
            abstract identity.  A named value, a retained value, or a nested
            mutable value keeps its identity and therefore remains subject to
            the ordinary alias checks.
            """
            if token not in mutable_tokens or contents.get(token):
                return False
            visible = {root for root in env if root[0] != "eval"}
            return next(_live_occurrences(
                token, _direct_roots(env, visible)), None) is None

        split = (len(placements) > 1 and value_ends
                 and all(unaliased_flat_value(token)
                         for token in value_ends))
        for container, slot in placements:
            stored = value_ends
            if split:
                stored = frozenset(
                    ("choice", token, id(target), container, slot)
                    for token in value_ends)
                remember_mutables(stored)
            remember_at(container, slot, stored)
        return True

    def address_key(node, frame):
        """Structural identity of one side-effect-free indexed location."""
        if isinstance(node, ast.Name):
            return ("root", frame_key(node.id, frame))
        name = field_of(node)
        if name is not None:
            return ("root", ("field", name))
        if not isinstance(node, ast.Subscript) \
                or isinstance(node.slice, ast.Slice):
            return None
        # A call or assignment expression can return a different index on the
        # two evaluations.  Such a spelling is not a proved permutation even
        # when its syntax happens to repeat on the other side.
        if any(isinstance(part, (ast.Call, ast.Await, ast.Yield,
                                 ast.YieldFrom, ast.NamedExpr))
               for part in ast.walk(node.slice)):
            return None
        base = address_key(node.value, frame)
        if base is None:
            return None
        return ("slot", base,
                ast.dump(node.slice, annotate_fields=True,
                         include_attributes=False))

    def alias_neutral_permutation(targets, values, env, frame):
        """Whether a parallel assignment only permutes private subtrees.

        Python evaluates every right-hand side before writing any target.  A
        bijection such as ``a[i], a[j] = a[j], a[i]`` changes positions but
        cannot create sharing.  The points-to authority may therefore retain
        its old, alpha-equivalent names when the moved subtrees have no live
        occurrence outside the permuted locations.  This avoids turning a
        row swap into two may-writes that place both rows in both slots.

        External aliases make object identity observable and deliberately
        disable this rule; the ordinary transfer then remains fail-closed.
        """
        target_keys = [address_key(node, frame) for node in targets]
        value_keys = [address_key(node, frame) for node in values]
        if any(key is None for key in target_keys + value_keys) \
                or Counter(target_keys) != Counter(value_keys):
            return False

        origins = [item for node in values
                   for item in _origins(node, env, frame)]
        if not origins:
            return False
        permitted = {(root, path) for root, path, _token in origins}
        direct = _direct_roots(
            env, {root for root in env if root[0] != "eval"})

        reachable = set()
        stack = [token for _root, _path, token in origins]
        while stack:
            token = stack.pop()
            if token in reachable:
                continue
            reachable.add(token)
            for _slot, children in _slot_items(token):
                stack.extend(children)

        def inside_permuted_subtree(root, path):
            return any(root == allowed_root
                       and path[:len(allowed_path)] == allowed_path
                       for allowed_root, allowed_path in permitted)

        return all(
            inside_permuted_subtree(root, path)
            for token in reachable
            for root, path in _live_occurrences(token, direct)
        )

    def bound_name(target, frame):
        if isinstance(target, ast.Name):
            # a binding is always to the current frame's own local, never a
            # captured enclosing one (Python needs `nonlocal` for that, and
            # a nonlocal write is refused elsewhere)
            return ("local", frame, target.id)
        name = field_of(target)
        return ("field", name) if name is not None else None

    def bind_points(env, name, tokens, *, preserves=False):
        """Bind one source name and forget a superseded exact index fact."""
        tokens = frozenset(tokens)
        for key in tuple(env):
            if key[0] == "iterator" and key[1] == name and not preserves:
                env[detached_key(key)] = frozenset({("detached",)})
                if tokens & env[key]:
                    env[reattached_key(key)] = frozenset({("reattached",)})
        env[name] = tokens
        exact_indices.pop(name, None)

    def ends_of(expr, env, depth, frame, live, ctx, owner, *, consumed=False):
        if expr is None or isinstance(expr, ast.Constant):
            return frozenset()
        if isinstance(expr, ast.Name):
            return env.get(frame_key(expr.id, frame), frozenset())
        name = field_of(expr)
        if name is not None:
            return env.get(("field", name), frozenset())
        if isinstance(expr, ast.IfExp):
            ends_of(expr.test, env, depth, frame, live, ctx, owner)
            taken, skipped = dict(env), dict(env)
            out = (ends_of(expr.body, taken, depth, frame, live, ctx, owner)
                   | ends_of(expr.orelse, skipped, depth, frame, live, ctx, owner))
            merged = join_env(taken, skipped)
            env.clear()
            env.update(merged)
            return out
        if isinstance(expr, ast.BoolOp):
            # `a or b` returns an operand, never a Boolean (review 43), and
            # the later operands run only on some paths.
            out = ends_of(expr.values[0], env, depth, frame, live, ctx, owner)
            for value in expr.values[1:]:
                fork = dict(env)
                out = out | ends_of(value, fork, depth, frame, live, ctx, owner)
                merged = join_env(env, fork)
                env.clear()
                env.update(merged)
            return out
        if isinstance(expr, ast.Call):
            held = []
            try:
                return call_ends(
                    expr, env, depth, frame, live, ctx, owner, held)
            finally:
                for key in reversed(held):
                    release_evaluation(env, key)
        if isinstance(expr, ast.Subscript):
            # children run through the evaluator itself, in Python's order:
            # scanning them for calls executed a short-circuited effect
            # unconditionally (review 44)
            base_ends = ends_of(expr.value, env, depth, frame, live, ctx, owner)
            held = hold_for_evaluation(
                env, base_ends, "an earlier subscript primary")
            parts = ([expr.slice.lower, expr.slice.upper, expr.slice.step]
                     if isinstance(expr.slice, ast.Slice) else [expr.slice])
            try:
                for part in parts:
                    if part is not None:
                        ends_of(part, env, depth, frame, live, ctx, owner)
            finally:
                release_evaluation(env, held)
            if isinstance(expr.slice, ast.Slice):
                # A slice is a fresh outer carrier whose elements are the
                # selected source objects.  Exact slice indices are a shape
                # concern owned by lowering; aliasing needs only know that the
                # copy does not copy its mutable children.
                token = ("s", ctx, id(expr))
                remember_mutable(token)
                remember(token, elements_of(base_ends))
                return frozenset({token})
            # An index returns the selected object, not a fresh one.  Preserve
            # exact slots where the spelling fixes the index and otherwise
            # join every possible slot.
            out = set()
            for token in base_ends:
                for slot in _index_slots(expr.slice, token, frame):
                    out.update(positions.get(token, {}).get(slot, ()))
            return frozenset(out)
        if isinstance(expr, (ast.List, ast.Set, ast.Tuple)):
            token = ("s", ctx, id(expr))
            if not isinstance(expr, ast.Tuple):
                remember_mutable(token)
            container = hold_for_evaluation(
                env, frozenset({token}), "an array literal under construction")
            ordered = None if isinstance(expr, ast.Set) else []
            try:
                for element in expr.elts:
                    if isinstance(element, ast.Starred):
                        spread = ends_of(element.value, env, depth, frame,
                                         live, ctx, owner)
                        remember(token, spread | elements_of(spread))
                        ordered = None
                    else:
                        got = ends_of(element, env, depth, frame, live, ctx,
                                      owner)
                        if ordered is not None:
                            # Install the element before evaluating the next
                            # one.  Python's partially built container already
                            # holds this reference; delaying every slot until
                            # the literal completed hid a mutation performed
                            # by a later element (for example, an earlier
                            # output record carrying ``self.x`` followed by a
                            # helper that mutates ``self.x``).
                            remember_at(token, len(ordered), got)
                            ordered.append(got)
                        else:
                            remember(token, got)
            finally:
                release_evaluation(env, container)
            if ordered is not None:
                remember_ordered(token, ordered)
            return frozenset({token})
        if isinstance(expr, ast.Dict):
            token = ("s", ctx, id(expr))
            remember_mutable(token)
            container = hold_for_evaluation(
                env, frozenset({token}), "a mapping literal under construction")
            try:
                for index, (key, value) in enumerate(
                        zip(expr.keys, expr.values)):
                    if key is not None:
                        ends_of(key, env, depth, frame, live, ctx, owner)
                    remember_at(token, index, ends_of(
                        value, env, depth, frame, live, ctx, owner))
            finally:
                release_evaluation(env, container)
            return frozenset({token})
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.DictComp,
                             ast.GeneratorExp)):
            if isinstance(expr, ast.GeneratorExp) and not consumed:
                # creation runs only the outermost iterable
                ends_of(expr.generators[0].iter, env, depth, frame, live,
                        ctx, owner)
                return frozenset({("s", ctx, id(expr))})
            # A comprehension is its nested loops, not one visit to its body.
            # Unroll by the exact same finite spans used by lowering so a
            # fresh allocation in iteration 0 is not the object allocated in
            # iteration 1.  Filters retain both outcomes after evaluating
            # their effects; nested generators retain their zero-yield path.
            token = ("s", ctx, id(expr))
            remember_mutable(token)
            container = hold_for_evaluation(
                env, frozenset({token}),
                "a comprehension result under construction")
            comp_frame = new_frame()
            frame_parent[comp_frame] = frame
            frame_comp_targets[comp_frame] = {
                n.id for gen in expr.generators
                for n in ast.walk(gen.target) if isinstance(n, ast.Name)}
            next_slot = [0]
            def generators(position, entering, ordinals):
                gen = expr.generators[position]
                if getattr(gen, "is_async", False):
                    raise _Hazard("comprehension", "async")
                gen_ctx = ctx + (("comp", id(gen), tuple(ordinals)),)
                iterable = ends_of(gen.iter, entering, depth, comp_frame,
                                   live, gen_ctx, owner)
                minimum, maximum = span_of(gen, owner)
                minimum = max(0, int(minimum))
                maximum = max(minimum, int(maximum))
                head = dict(entering)
                exits = []
                for iteration in range(maximum):
                    if iteration >= minimum:
                        exits.append(dict(head))
                    work = dict(head)
                    item_ctx = ctx + (("comp", id(gen),
                                       tuple(ordinals + [iteration])),)
                    bound = bound_name(gen.target, comp_frame)
                    if bound is not None:
                        work[bound] = elements_of(iterable)
                    stopped = []
                    for cond in gen.ifs:
                        ends_of(cond, work, depth, comp_frame, live,
                                item_ctx, owner)
                        stopped.append(dict(work))
                    if position + 1 < len(expr.generators):
                        completed = generators(
                            position + 1, work, ordinals + [iteration])
                    else:
                        slot = next_slot[0]
                        next_slot[0] += 1
                        if isinstance(expr, ast.DictComp):
                            ends_of(expr.key, work, depth, comp_frame,
                                    live, item_ctx, owner)
                            value = ends_of(expr.value, work, depth,
                                            comp_frame, live, item_ctx, owner)
                        else:
                            value = ends_of(expr.elt, work, depth,
                                            comp_frame, live, item_ctx, owner)
                        remember_at(token, slot, value)
                        completed = work
                    head = join_env(completed, *stopped)
                exits.append(head)
                return join_env(*exits)

            try:
                head = generators(0, dict(env), [])
                merged = {key: tokens for key, tokens in head.items()
                          if not (key[0] == "local"
                                  and key[1] == comp_frame)}
                env.clear()
                env.update(merged)
            finally:
                release_evaluation(env, container)
            return frozenset({token})
        if isinstance(expr, ast.BinOp):
            left = ends_of(expr.left, env, depth, frame, live, ctx, owner)
            held = hold_for_evaluation(
                env, left, "an earlier binary operand")
            try:
                right = ends_of(
                    expr.right, env, depth, frame, live, ctx, owner)
            finally:
                release_evaluation(env, held)
            # Sequence addition and repetition build a fresh outer carrier,
            # but shallow-copy the contained objects.  Keep separate source
            # occurrences so a later indexed mutation can see sharing.
            if isinstance(expr.op, ast.Add) and (left or right):
                token = ("s", ctx, id(expr))
                remember_mutable(token)
                groups = occurrence_groups(left) + occurrence_groups(right)
                remember_occurrences(token, groups, "concat")
                return frozenset({token})
            if isinstance(expr.op, ast.Mult) and (left or right):
                token = ("s", ctx, id(expr))
                remember_mutable(token)
                left_elements = elements_of(left)
                source = left if left_elements else right
                repeated = occurrence_groups(source)
                count_source = expr.right if left_elements else expr.left
                count = literal_repeat_count(count_source)
                copies = max(0, count) if count is not None else 2
                if copies == 1:
                    remember_occurrences(token, repeated, "repeat")
                elif copies >= 2 and repeated:
                    # Two occurrences prove every alias hazard caused by
                    # repetition.  More copies cannot change that answer and
                    # need not expand the analysis graph.
                    remember_occurrences(
                        token, repeated + repeated, "repeat")
                return frozenset({token})
            return frozenset()
        if isinstance(expr, ast.Compare):
            left = ends_of(expr.left, env, depth, frame, live, ctx, owner)
            previous_source = expr.left
            for operator, comparator in zip(expr.ops, expr.comparators):
                held = hold_for_evaluation(
                    env, left, "an earlier comparison operand")
                try:
                    left = ends_of(
                        comparator, env, depth, frame, live, ctx, owner)
                finally:
                    release_evaluation(env, held)
                if fixed_comparison_truth(
                        previous_source, operator, comparator) is False:
                    break
                previous_source = comparator
            return frozenset()
        if isinstance(expr, ast.Starred):
            return ends_of(expr.value, env, depth, frame, live, ctx, owner)
        # No unmodeled expression may claim to be fresh (review 43: BoolOp
        # returned an operand while carrying a fresh token): the default is
        # to propagate whatever the children may denote.
        out = frozenset()
        for child in ast.iter_child_nodes(expr):
            if isinstance(child, ast.expr):
                out = out | ends_of(child, env, depth, frame, live, ctx, owner)
        return out

    def call_ends(call, env, depth, frame, live, ctx, owner, held):
        def evaluated(expr, label, *, consumed=False):
            got = ends_of(expr, env, depth, frame, live, ctx, owner,
                          consumed=consumed)
            key = hold_for_evaluation(env, got, label)
            if key is not None:
                held.append(key)
            return got

        callee = None
        if isinstance(call.func, ast.Attribute):
            method = field_of(call.func)
            if method is not None:
                mkind, callee = resolver.resolve_method(owner, method)
                if mkind != "method":
                    callee = None
            else:
                before_receiver = len(held)
                recv = evaluated(
                    call.func.value, "the receiver of a pending call")
                receiver_hold = (held[-1]
                                 if len(held) > before_receiver else None)
                recv_origins = _origins(call.func.value, env, frame)
                args = [evaluated(arg, "an earlier call argument")
                        for arg in call.args]
                for keyword in call.keywords:
                    args.append(evaluated(
                        keyword.value, "an earlier call argument"))
                # The receiver must stay live while the arguments are
                # evaluated.  Once the call begins, however, it is the very
                # path through which the method is allowed to mutate the
                # object, not an independent observer of that mutation.
                # Earlier operands and argument aliases remain in ``held``
                # and are still checked below.
                if receiver_hold is not None:
                    release_evaluation(env, receiver_hold)
                    held.remove(receiver_hold)
                if call.func.attr == "copy" and has_plain_positional_arity(call, 0):
                    token = ("s", ctx, id(call))
                    remember_mutable(token)
                    remember_occurrences(token, occurrence_groups(recv), "sequence-copy")
                    return frozenset({token})
                mapping_contract = state_contracts.get(field_of(call.func.value))
                if mapping_contract is not None and mapping_contract.is_mapping:
                    if call.func.attr == "get" and has_plain_positional_arity(call, 1, 2):
                        return elements_of(recv) | (args[1] if len(args) == 2 else frozenset())
                    if ((call.func.attr == "pop" and has_plain_positional_arity(call, 1, 2))
                            or (call.func.attr == "clear" and has_plain_positional_arity(call, 0))):
                        check_mut(None, env, live, recv_origins)
                        return (frozenset() if call.func.attr == "clear" else
                                elements_of(recv) | (args[1] if len(args) == 2 else frozenset()))
                if is_plain_append_call(call):
                    check_mut(None, env, live, recv_origins)
                    for token in recv:
                        remember_mutable(token)
                        # The evaluation context includes the unrolled loop
                        # ordinal, so repeated appends of one stable row occupy
                        # distinct slots while a row allocated each iteration
                        # has a distinct identity.
                        occurrence = (token, ctx, id(call))
                        if occurrence not in append_slots:
                            # Each abstract append occurrence needs a distinct
                            # slot.  Scanning every preceding occurrence made
                            # a loop that builds a large nested sequence
                            # quadratic in the number of appends; the next
                            # slot is the complete per-owner summary of that
                            # scan.
                            slot = append_next_slot.get(token, 0)
                            append_slots[occurrence] = slot
                            append_next_slot[token] = slot + 1
                            graph_changed()
                        remember_at(token, append_slots[occurrence], args[0])
                    return frozenset()       # append returns None
                if is_plain_pop_call(call):
                    if set(recv_origins) != set(_origins(call.func.value, env, frame)):
                        raise _Hazard("pop receiver", "index rebinding", kind="mutation")
                    check_mut(None, env, live, recv_origins)
                    forget_removed_positions(recv)
                    # The precise physical shift belongs to retained value
                    # lowering.  Alias safety needs the source object that
                    # may escape through the result; the union is conservative
                    # for a dynamic index and exact enough to prevent a popped
                    # mutable child from being mistaken for a fresh value.
                    return elements_of(recv)
                if is_plain_extend_call(call):
                    # A finite eager sequence is snapshotted before the first
                    # store, including xs.extend(xs). Its argument reference
                    # is not an independent observer during those stores.
                    for key in tuple(held):
                        release_evaluation(env, key)
                        held.remove(key)
                    if set(recv_origins) != set(_origins(call.func.value, env, frame)):
                        raise _Hazard("extend receiver", "argument rebinding",
                                      kind="mutation")
                    check_mut(None, env, live, recv_origins)
                    groups = occurrence_groups(args[0])
                    for token in recv:
                        remember_mutable(token)
                        remember_occurrences(token, groups, (ctx, id(call)))
                    return frozenset()
                # a method on a value may write it in place
                check_mut(None, env, live, recv_origins or [
                    (None, (), token) for token in recv])
                return recv
        elif isinstance(call.func, ast.Name):
            called = call.func.id
            rkind, callee = resolver.resolve_call(owner, called)
            if rkind not in ("nested", "module", "alias"):
                callee = None
            if rkind == "intrinsic":
                for arg in list(call.args) \
                        + [k.value for k in call.keywords]:
                    evaluated(arg, "an earlier call argument")
                return frozenset()
            if callee is None and rkind == "builtin":
                # a builtin contract applies only to an unshadowed spelling
                copied = record_copy_source(call, is_builtin=lambda _name: True)
                items = record_items(call, is_builtin=lambda _name: True)
                if copied is not None or items is not None:
                    token = ("s", ctx, id(call))
                    remember_mutable(token)
                    key = hold_for_evaluation(
                        env, frozenset({token}), "a mapping under construction")
                    if key is not None:
                        held.append(key)
                    if copied is not None:
                        source = evaluated(copied, "the copied mapping")
                        remember_occurrences(
                            token, occurrence_groups(source), "record-copy")
                    else:
                        for index, (_field, value) in enumerate(items):
                            remember_at(token, index, evaluated(
                                value, "an earlier mapping field"))
                    return frozenset({token})
                if called in SCALAR_CALLS:
                    for arg in list(call.args) \
                            + [k.value for k in call.keywords]:
                        evaluated(arg, "an earlier call argument")
                    return frozenset()
                if called in CARRIER_CALLS:
                    # fresh outside, the arguments' elements inside
                    token = ("s", ctx, id(call))
                    if called in {"sorted", "list", "set", "bytearray"}:
                        remember_mutable(token)
                    for arg in list(call.args) \
                            + [k.value for k in call.keywords]:
                        # Eager constructors consume filters, elements and
                        # nested iterables now. Retain their aliases/effects
                        # through the same finite comprehension walk; treating
                        # this as generator creation would hide shared rows.
                        got = evaluated(
                            arg, "an earlier call argument",
                            consumed=(called in {"list", "tuple", "bytes", "bytearray"}
                                      and isinstance(arg, ast.GeneratorExp)))
                        remember(token, elements_of(got))
                    return frozenset({token})
                if called == "next":
                    out = frozenset()
                    for arg in list(call.args) \
                            + [k.value for k in call.keywords]:
                        out = out | elements_of(
                            evaluated(arg, "an earlier call argument"))
                    return out
        else:
            evaluated(call.func, "the callable of a pending call")
        arg_ends = [evaluated(arg, "an earlier call argument")
                    for arg in call.args]
        for keyword in call.keywords:
            arg_ends.append(evaluated(
                keyword.value, "an earlier call argument"))
        if callee is None:
            return frozenset().union(frozenset(), *arg_ends)
        if helper_activation_exceeds(depth + 1):
            raise LoweringError(
                call,
                f"call to {getattr(callee, 'name', '?')} nested too deeply",
                code="call-inlining-too-deep")
        # The callee's enclosing scope is lexical: a nested def closes over
        # its enclosing function's activation, a module or class-level
        # function over nothing (review 51). The dynamic caller's frame is
        # not the closure parent.
        lexical_parent = resolver._parent.get(id(callee))
        parent_frame = activation.get(id(lexical_parent)) \
            if lexical_parent is not None else None
        inner = new_frame(callee, parent_frame)
        is_method = id(callee) in resolver._method_ids
        params = [a.arg for a in callee.args.args
                  if not (is_method and a.arg == "self")]
        if not any(isinstance(arg, ast.Starred) for arg in call.args):
            try:
                params, arg_ends = bind_helper_arguments(
                    callee, arg_ends[:len(call.args)],
                    [(keyword.arg, ends) for keyword, ends in
                     zip(call.keywords, arg_ends[len(call.args):])],
                    receiver=is_method, default=lambda _name, _node: frozenset())
            except CallBindingError as exc:
                raise LoweringError(call, str(exc), code=exc.code) from None
        for param, ends in zip(params, arg_ends):
            env[("local", inner, param)] = ends
        # Evaluation holds protect earlier arguments while later ones run.
        # At callee entry those references have become formal bindings; they
        # are not additional observers. Outer expression holds remain live.
        for key in tuple(held):
            release_evaluation(env, key)
            held.remove(key)
        borrowed = borrowed_arguments.get(id(call), {})
        for param, actual in borrowed.items():
            borrowed_roots[("local", inner, param)] = frame_key(actual, frame)
        prev_activation = activation.get(id(callee))
        activation[id(callee)] = inner
        try:
            norm, brks, conts, rets = walk(callee.body, env, depth + 1, inner,
                                           live, ctx + (id(call),), callee)
        finally:
            for param in borrowed:
                borrowed_roots.pop(("local", inner, param))
            if prev_activation is None:
                activation.pop(id(callee), None)
            else:
                activation[id(callee)] = prev_activation
        if brks or conts:
            raise _Hazard("control", callee.name)
        merged = join_env(*([norm] if norm is not None else []),
                          *[state for state, _ in rets])
        out = frozenset().union(frozenset(),
                                *[toks for _, toks in rets])
        env.clear()
        env.update({key: tokens for key, tokens in merged.items()
                    if not (key[0] == "local" and key[1] == inner)})
        return out

    def walk(body, env, depth, frame, tail, ctx, owner):
        """-> (normal env | None, break envs, continue envs, returns)."""
        brks, conts, rets = [], [], []
        suffix = [frozenset(tail)]
        for stmt in reversed(body):
            suffix.append(suffix[-1] | reads_of(stmt, frame, owner))
        suffix.reverse()
        for index, stmt in enumerate(body):
            live = suffix[index + 1]
            if isinstance(stmt, ast.Return):
                toks = ends_of(stmt.value, env, depth, frame, live, ctx, owner)
                rets.append((dict(env), toks))
                return None, brks, conts, rets
            if isinstance(stmt, ast.Break):
                brks.append(dict(env))
                return None, brks, conts, rets
            if isinstance(stmt, ast.Continue):
                conts.append(dict(env))
                return None, brks, conts, rets
            if isinstance(stmt, ast.Delete):
                for target in stmt.targets:
                    # No replacement object is introduced. Retain the old
                    # may-alias children conservatively, as pop already does.
                    mutate_target(target, (), env, depth, frame, live, ctx,
                                  owner, record=False, deleting=True)
                continue
            if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
                for expression in annotation_target_expressions(stmt.target):
                    ends_of(expression, env, depth, frame, live, ctx, owner)
                continue
            if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                value = getattr(stmt, "value", None)
                targets = (stmt.targets if isinstance(stmt, ast.Assign)
                           else [stmt.target])
                # Destructuring binds element-wise (review 43): the whole
                # right side evaluates first, in order, then each target
                # takes its own element's tokens -- one shared set gave
                # every target a token for the tuple itself.
                even = (isinstance(stmt, ast.Assign) and len(targets) == 1
                        and isinstance(targets[0], (ast.Tuple, ast.List))
                        and isinstance(value, (ast.Tuple, ast.List))
                        and len(targets[0].elts) == len(value.elts)
                        and not any(isinstance(e, ast.Starred)
                                    for e in targets[0].elts))
                if even:
                    element_ends = [ends_of(e, env, depth, frame, live, ctx, owner)
                                    for e in value.elts]
                    permutation = alias_neutral_permutation(
                        targets[0].elts, value.elts, env, frame)
                    rhs_hold = hold_for_evaluation(
                        env,
                        frozenset().union(frozenset(), *element_ends),
                        "an assignment value awaiting its targets")
                    try:
                        for element, ends in zip(
                                targets[0].elts, element_ends):
                            if mutate_target(element, ends, env, depth, frame,
                                             live, ctx, owner,
                                             record=not permutation):
                                continue
                            bound = bound_name(element, frame)
                            if bound is not None:
                                bind_points(env, bound, ends)
                    finally:
                        release_evaluation(env, rhs_hold)
                    continue
                ends = ends_of(value, env, depth, frame, live, ctx, owner)
                rhs_hold = hold_for_evaluation(
                    env, ends, "an assignment value awaiting its targets")
                try:
                    if (isinstance(stmt, ast.Assign) and len(targets) == 1
                            and isinstance(targets[0], (ast.Tuple, ast.List))
                            and not any(isinstance(e, ast.Starred)
                                        for e in targets[0].elts)
                            and len(ends) == 1):
                        # one known allocation on the right: bind each target
                        # its own element's tokens, exactly as the runtime does
                        per_element = ordered_contents.get(next(iter(ends)))
                        if (per_element not in (None, False)
                                and len(per_element) == len(targets[0].elts)):
                            for element, elem_ends in zip(targets[0].elts,
                                                          per_element):
                                if mutate_target(element, elem_ends, env, depth,
                                                 frame, live, ctx, owner):
                                    continue
                                bound = bound_name(element, frame)
                                if bound is not None:
                                    bind_points(env, bound, elem_ends)
                                continue
                            continue
                    for target in targets:
                        flat = (target.elts if isinstance(
                            target, (ast.Tuple, ast.List)) else [target])
                        for element in flat:
                            if mutate_target(element, ends, env, depth, frame,
                                             live, ctx, owner):
                                continue
                            bound = bound_name(element, frame)
                            if bound is None:
                                continue
                            if isinstance(target, (ast.Tuple, ast.List)) \
                                    and not isinstance(stmt, ast.AugAssign):
                                bind_points(
                                    env, bound, ends | elements_of(ends))
                                continue
                            if isinstance(stmt, ast.AugAssign):
                                if isinstance(stmt.op, (ast.Add, ast.Mult)):
                                    # += and *= are the two operators a sequence
                                    # survives, in place; every other augmented
                                    # operator crashes a sequence, so reaching
                                    # it means a scalar rebind (the loss of this
                                    # rule refused 45 models whose inlined
                                    # helpers mask with &= and shift with >>=).
                                    check_mut(bound, env, live)
                                else:
                                    bind_points(env, bound, ())
                                continue
                            bind_points(env, bound, ends,
                                        preserves=bound_name(value, frame) == bound)
                finally:
                    release_evaluation(env, rhs_hold)
                continue
            if isinstance(stmt, ast.Expr):
                ends_of(stmt.value, env, depth, frame, live, ctx, owner)
                continue
            if isinstance(stmt, ast.If):
                ends_of(stmt.test, env, depth, frame, live, ctx, owner)
                taken, skipped = dict(env), dict(env)
                n1, b1, c1, r1 = walk(stmt.body, taken, depth, frame, live, ctx, owner)
                n2, b2, c2, r2 = walk(stmt.orelse, skipped, depth, frame, live, ctx, owner)
                brks += b1 + b2
                conts += c1 + c2
                rets += r1 + r2
                alive = [n for n in (n1, n2) if n is not None]
                if not alive:
                    return None, brks, conts, rets
                merged = join_env(*alive)
                env.clear()
                env.update(merged)
                continue
            if isinstance(stmt, (ast.For, ast.While)):
                iter_elements = frozenset()
                loop_bound = None
                exact_iteration_values = None
                iterator_key = None
                if isinstance(stmt, ast.For):
                    source = stmt.iter
                    while (isinstance(source, ast.Call)
                           and isinstance(source.func, ast.Name)
                           and source.func.id in {"enumerate", "reversed"}
                           and resolver.is_builtin(owner, source.func.id)
                           and source.args):
                        source = source.args[0]
                    source_bound = bound_name(source, frame)
                    # The first argument's object is acquired before an
                    # enumerate start expression can replace its binding.
                    held = env.get(source_bound, frozenset())
                    key = ("iterator", source_bound, ctx, frame, id(stmt),
                           "the object held by a live iterator")
                    if held:
                        iterator_key = key
                        env[iterator_key] = held
                    iterable_tokens = ends_of(
                        stmt.iter, env, depth, frame, live, ctx, owner)
                    iter_elements = elements_of(iterable_tokens)
                    if source_bound is None and iterable_tokens:
                        iterator_key = key
                        env[key] = iterable_tokens
                    loop_bound = bound_name(stmt.target, frame)
                    values = static_iterable(
                        stmt.iter,
                        resolver.owner_of(owner),
                        is_builtin=lambda name: resolver.is_builtin(
                            owner, name),
                    )
                    if (loop_bound is not None and values is not None
                            and all(type(value) is int for value in values)):
                        exact_iteration_values = tuple(values)
                minimum, maximum = span_of(stmt, owner)
                minimum = max(0, int(minimum))
                maximum = max(minimum, int(maximum))
                head = dict(env)
                loop_brks = []
                ordinary_exits = []
                wrap = live | frozenset().union(
                    frozenset(),
                    *(reads_of(inner, frame, owner) for inner in stmt.body),
                    *(reads_of(inner, frame, owner) for inner in stmt.orelse))
                for iteration in range(maximum):
                    before = abstract_state(head)
                    # A data-dependent loop may stop before this iteration;
                    # the proved lower bound says when that first becomes a
                    # real path.  Fresh allocations in the body carry the
                    # ordinal, so one object per iteration never collapses to
                    # one allocation-site token.
                    if iteration >= minimum:
                        ordinary_exits.append(dict(head))
                    work = dict(head)
                    iteration_ctx = ctx + (("iter", id(stmt), iteration),)
                    if isinstance(stmt, ast.While):
                        ends_of(stmt.test, work, depth, frame, live,
                                iteration_ctx, owner)
                    if isinstance(stmt, ast.For) and loop_bound is not None:
                        work[loop_bound] = iter_elements
                        if (exact_iteration_values is not None
                                and iteration < len(exact_iteration_values)):
                            exact_indices[loop_bound] = exact_iteration_values[iteration]
                        else:
                            exact_indices.pop(loop_bound, None)
                    n3, b3, c3, r3 = walk(
                        stmt.body, work, depth, frame, wrap,
                        iteration_ctx, owner)
                    loop_brks += b3
                    rets += r3
                    continuing = ([n3] if n3 is not None else []) + c3
                    if not continuing:
                        head = None
                        break
                    head = join_env(*continuing)
                    # Retained loops describe repetition, not an obligation to
                    # spend work on identical abstract states.  Once the complete
                    # points-to state (including container contents) is unchanged,
                    # every remaining iteration has the same transfer and cannot
                    # reveal a new alias.  This keeps large scalar loops linear in
                    # their semantic effects rather than in their declared trip
                    # count, without hiding repeated appends or fresh allocations.
                    if abstract_state(head) == before:
                        break
                if loop_bound is not None:
                    # A loop target is a Python function local, but after a
                    # data-dependent break or zero-iteration path it has no
                    # single exact post-loop value.  The exact fact is needed
                    # only while analysing the corresponding iteration.
                    exact_indices.pop(loop_bound, None)
                if head is not None:
                    ordinary_exits.append(dict(head))
                # Loop else belongs only to ordinary exhaustion.
                exhausted = join_env(*ordinary_exits) \
                    if ordinary_exits else None
                # Iterator lifetime ends at exhaustion/break/return, before
                # loop-else executes. Keep outer iterator roots untouched.
                if iterator_key is not None:
                    exits = [*loop_brks, *(e for e, _value in rets)]
                    if exhausted is not None:
                        exits.append(exhausted)
                    for exit_env in exits:
                        exit_env.pop(iterator_key, None)
                        exit_env.pop(detached_key(iterator_key), None)
                        exit_env.pop(reattached_key(iterator_key), None)
                else_norm, b4, c4, r4 = (None, [], [], [])
                if exhausted is not None:
                    else_norm, b4, c4, r4 = walk(
                        stmt.orelse, exhausted, depth, frame, live, ctx, owner)
                brks += b4
                conts += c4
                rets += r4
                after = ([else_norm] if else_norm is not None else []) \
                    + loop_brks
                if not after:
                    return None, brks, conts, rets
                merged = join_env(*after)
                env.clear()
                env.update(merged)
                continue
            if isinstance(stmt, ast.Assert):
                ends_of(stmt.test, env, depth, frame, live, ctx, owner)
                continue
            if isinstance(stmt, (ast.Global, ast.Nonlocal)):
                raise _Hazard(type(stmt).__name__.lower(), stmt.names[0])
            if isinstance(stmt, (ast.Pass, ast.Import, ast.ImportFrom)):
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # a nested definition binds a callable this analysis will
                # not resolve; calling it later passes arguments through
                continue
            raise _Hazard(type(stmt).__name__, "unmodeled-statement")
        return env, brks, conts, rets

    fields = [name for name in model.state_contract()
              if name in array_names]
    field_tokens = {("field", name): frozenset({("f", name)})
                    for name in fields}

    def seed_type(token, kind, path=()):
        """Give an attested state tree one distinct object per array path."""
        remember_mutable(token)
        if not isinstance(kind, types_mod.Array):
            return
        if isinstance(kind.elem, types_mod.Array):
            for index in range(kind.cells):
                child = ("fcell", token, path + (index,))
                remember_at(token, index, frozenset({child}))
                seed_type(child, kind.elem, path + (index,))

    def seed_contract(token, contract):
        """Distinct entry objects for recursive source products, not IR leaves."""
        if contract.is_option:
            seed_contract(token, contract.payload)
            return
        if contract.is_record or contract.is_mapping or contract.carrier in {"list", "bytearray"}:
            remember_mutable(token)
        if (contract.is_sequence and contract.components is None
                and (contract.element is None
                     or contract_is_deeply_immutable(contract.element))):
            # Scalar arrays have a mutable outer object, not one mutable
            # child per cell. Do not enumerate their (possibly large) extent.
            return
        children = (contract.fields if contract.is_record else
                    ((index, contract.component(index))
                     for index in range(contract.maximum))
                    if contract.is_sequence else ())
        for slot, child_contract in children:
            if contract_is_deeply_immutable(child_contract):
                continue
            child = ("fcell", token, slot)
            remember_at(token, slot, frozenset({child}))
            seed_contract(child, child_contract)

    for name in fields:
        if name in state_contracts:
            seed_contract(("f", name), state_contracts[name])
        else:
            seed_type(("f", name), array_types.get(name))
    try:
        env = dict(field_tokens)
        step_frame = new_frame(model.step, None)
        activation[id(model.step)] = step_frame
        norm, brks, conts, rets = walk(model.step.body, env, 0,
                                       step_frame,
                                       frozenset(), (), model.step)
        if conts:
            raise _Hazard("continue", "outside-loop")
        finals = ([norm] if norm is not None else []) \
            + brks + [state for state, _ in rets]
        merged = join_env(*finals) if finals else {}

        # All reached helpers and body alternatives use check_mut. If none
        # writes a mutable value, sharing is observationally irrelevant on
        # every call; rebinding a field changes only that field in Python too.
        # Each individual published field still needs a tree-shaped native
        # image. Sharing across distinct fields is harmless without mutation;
        # repeated mutable descendants within one field cannot cross that
        # existing snapshot boundary.

        # The construction witness establishes this property at the first
        # boundary.  Re-establish it at every normal exit, including sharing
        # through two paths inside one nested state value.  One transfer is
        # then sufficient for every invocation by induction.  Reinterpreting
        # the step and retaining its local object graph made an allocation
        # from the *previous abstract run* appear to exist before its source
        # statement in the next one.
        owner_of = {}
        for root in field_tokens:
            # One field denotes one of its possible outer objects, never all
            # of them simultaneously. Rebinding a list to a shallow concat
            # retains its old elements, but the replaced outer list is not a
            # second live state location. Check duplicate paths within each
            # alternative, and sharing across distinct fields independently.
            for outer in merged.get(root, ()):
                paths = {}
                for _root, path, token in _all_occurrences(root, (outer,)):
                    if token not in mutable_tokens:
                        continue
                    prior = owner_of.get(token)
                    if ((token in paths and paths[token] != path)
                            or (has_mutation and prior is not None and prior != root)):
                        return AliasHazard("retained", root[-1],
                                           root[-1] if prior is None else prior[-1])
                    paths[token] = path
                    owner_of[token] = root
        return None
    except _Hazard as hazard:
        return hazard.result
