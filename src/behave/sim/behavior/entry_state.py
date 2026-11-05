"""Prove when a source field needs no value at the step boundary.

Python permits an attribute to be created by ``process`` rather than by the
constructor or ``reset``.  SolverIR, by contrast, has a total state vector at
every boundary.  Filling such a field with zero merely because the vector needs
a value is unsound: the source may read the absent field first, or may complete
a step without ever creating it.

An *entry-dead* field is the precise case in which a representation filler is
irrelevant.  No path reads the field before defining it, and every normally
completing path defines it.  The first condition protects the current step; the
second makes the source and IR post-state agree.  Paths ending in a failure need
not define it because a failed step has no normal successor state.

This module answers that control-flow question and constructs the one canonical
value belonging to an already inferred SolverIR type.  It does not infer types,
ranges, or source values, and therefore cannot become a second authority for
any of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..symbolic import cfg, ssa, typecheck as types

__all__ = ["EntryStatePlan", "plan"]


@dataclass(frozen=True)
class EntryStatePlan:
    """Classification of construction-absent state fields.

    ``values`` contains representation-only canonical fillers.  ``unsafe``
    names fields whose old value can be observed, or which can remain absent
    after normal completion.  ``untyped`` names fields for which no canonical
    SolverIR value exists because type inference did not settle their type.
    """

    values: tuple[tuple[str, object], ...]
    unsafe: tuple[str, ...]
    untyped: tuple[str, ...]
    undefined_uses: tuple[str, ...] = ()

    @property
    def entry_dead(self) -> tuple[str, ...]:
        return tuple(name for name, _value in self.values)


def _canonical(kind):
    """The canonical inhabitant of one proved SolverIR representation type."""
    if kind == types.BOOLEAN:
        return False
    if kind == types.INTEGER:
        return 0
    if isinstance(kind, types.Array):
        return [_canonical(kind.elem) for _ in range(kind.cells)]
    return None


def _normal_exit_definitions(graph, analysis):
    """Names defined on every reachable normally completing path.

    A graph may have several ``Return`` terminators (early returns) and several
    ``Fail`` terminators.  Only the former establish a next state.  With no
    normal exit the intersection is deliberately ``None``: read safety still
    matters before the failure, but no post-state has to be constructed.
    """
    exits = [
        analysis.out_of(label)
        for label in graph.order()
        if (label in analysis.out_masks
            and isinstance(graph.blocks[label].term, cfg.Return))
    ]
    return set.intersection(*(set(names) for names in exits)) if exits else None


def plan(proc, missing) -> EntryStatePlan:
    """Classify the state names in ``missing`` at ``proc``'s entry.

    The production lowering supplies its original CFG.  The term lowerer is a
    differential oracle and supplies no graph, so it is checked through the
    structured command's CFG.  That reconstruction may be conservative after
    predication; it can refuse an oracle case but cannot admit a pre-definition
    read or a missing normal post-state.
    """
    missing = set(missing)
    graph = proc.graph
    if graph is None:
        graph = cfg.of_command(proc.body)
    start = (set(proc.state) - missing) | set(proc.inputs)
    analysis = ssa.analyze(graph, start)
    undefined = analysis.undefined_names
    on_normal_exit = _normal_exit_definitions(graph, analysis)

    unsafe = set(missing) & undefined
    if on_normal_exit is not None:
        unsafe |= missing - on_normal_exit

    values = []
    untyped = []
    for name in sorted(missing - unsafe):
        kind = proc.types.get(name)
        if kind is None:
            untyped.append(name)
            continue
        value = _canonical(kind)
        if value is None:
            untyped.append(name)
            continue
        values.append((name, value))
    return EntryStatePlan(
        tuple(values), tuple(sorted(unsafe)), tuple(untyped),
        analysis.undefined_uses)
