"""Which definition reaches which use, as a fact about the graph.

Definite assignment was a dataflow analysis over the flattened term, and it was
wrong in the way analyses over flattened terms are wrong: it could not see that
two guards are the same guard, so it reported a read after `if x: return` as a
read of something possibly unassigned. Recovering that needed the interval
analysis, which cost more than the lowering and still did not finish the job,
because every construct loses the information its own way.

On a graph the question is different in kind. A use is fine when every feasible
path from the entry to it passes a definition. Most joins need only bit-set
intersection. A repeated test of an immutable source input additionally keeps
one finite Boolean path fact, so two contradictory outcomes are not mistaken
for a path. Unknown predicates remain conservative, and a bounded partition
falls back to intersection rather than guessing a definition.
"""

from __future__ import annotations

from dataclasses import dataclass

from behave.core import runtime_defaults as defaults

from . import cfg, ir
from .ir import _reads

__all__ = ["ReachingAnalysis", "analyze", "loop_flow_facts", "loop_source_region", "reaching",
           "undefined_names", "undefined_uses"]


_PATH_PARTITION_LIMIT = defaults.SIM_BEHAVIOR_SSA_PATHS
_FLOW_IN_PROGRESS = object()


def _instr_reads(instr):
    if isinstance(instr, ir.Assign):
        yield from _reads(instr.expr)
    elif isinstance(instr, ir.Store):
        # A Store is read-modify-write: every cell except the selected one is
        # copied from the old array.  Treating it as a pure definition admits a
        # first write into an otherwise undefined array.
        yield instr.var
        yield from _reads(instr.index)
        yield from _reads(instr.expr)
    elif isinstance(instr, ir.For):
        yield from _reads(instr.trip_count)


def _instr_writes(instr):
    if isinstance(instr, (ir.Assign, ir.Store)):
        return instr.var
    return None


def _loop_names(loop):
    """Names mentioned by one retained loop body and its count."""
    reads, writes = set(_reads(loop.trip_count)), {loop.index}
    for command in ir.walk(loop.body):
        for expr in ir.expressions(command):
            reads.update(_reads(expr))
        if isinstance(command, (ir.Assign, ir.Store)):
            writes.add(command.var)
    return reads, writes


def _normalise_path_states(states):
    """Bounded disjunction of ``(guard facts, defined names)`` states.

    States with the same definition mask can be joined by retaining only the
    guard facts common to all of them: future definite-assignment questions
    cannot distinguish those paths until another branch defines a name.  If
    distinct masks still exceed the fixed budget, collapse them by
    intersection.  That loses precision and can cause a refusal, but it can
    never invent a reaching definition.
    """
    by_mask = {}
    for facts, mask in states:
        facts = frozenset(facts)
        previous = by_mask.get(mask)
        by_mask[mask] = facts if previous is None else previous & facts
    compact = [(facts, mask) for mask, facts in by_mask.items()]
    if len(compact) <= _PATH_PARTITION_LIMIT:
        return tuple(compact)
    common_facts = compact[0][0]
    common_mask = compact[0][1]
    for facts, mask in compact[1:]:
        common_facts &= facts
        common_mask &= mask
    return ((common_facts, common_mask),)


def _with_guard_fact(state, atom, truth):
    """Refine one path state, or return ``None`` on contradiction."""
    facts, mask = state
    known = dict(facts)
    if atom in known and known[atom] != truth:
        return None
    known[atom] = truth
    return frozenset(known.items()), mask


def _edge_states(graph, predecessor, successor, states):
    """Path states reaching one successor edge of ``predecessor``."""
    term = graph.blocks[predecessor].term
    if not isinstance(term, cfg.Branch) or term.atom is None:
        return tuple(states) if successor in term.successors() else ()
    polarities = []
    if term.then == successor:
        polarities.append(term.positive)
    if term.orelse == successor:
        polarities.append(not term.positive)
    out = []
    for truth in polarities:
        for state in states:
            refined = _with_guard_fact(state, term.atom, truth)
            if refined is not None:
                out.append(refined)
    return tuple(out)


@dataclass(frozen=True)
class ReachingAnalysis:
    """One compact reaching-definition pass over a graph.

    A bit per name makes joins integer intersections rather than copies of a
    growing Python set at every block.  The public accessors materialise sets
    only for callers that actually need them; production entry analysis uses
    the same pass for missing reads and normal-exit definitions.
    """

    names: tuple[str, ...]
    into_masks: dict
    out_masks: dict
    undefined: tuple[tuple[str, str], ...]

    def _names(self, mask):
        return {name for index, name in enumerate(self.names)
                if mask & (1 << index)}

    def into(self, label):
        return self._names(self.into_masks[label])

    def out_of(self, label):
        return self._names(self.out_masks[label])

    @property
    def undefined_names(self):
        return {name for name, _reason in self.undefined}

    @property
    def undefined_uses(self):
        return tuple(reason for _name, reason in self.undefined)


def loop_flow_facts(instr, *, _cache=None):
    """Validate and recompute the CFG facts carried by a retained loop.

    ``_cache`` is scoped to one synchronous analysis transaction. Nested
    retained loops can otherwise revalidate the same sealed graph once for
    every enclosing query. Public calls start with a fresh cache, so a later
    mutation is still detected rather than hidden by process-global state.
    """
    flow = instr.flow
    if flow is None:
        return None
    cache = {} if _cache is None else _cache
    cached = cache.get(id(instr))
    if cached is not None and cached[0] is instr:
        if cached[1] is _FLOW_IN_PROGRESS:
            raise ir.RepresentationStructureError(
                "loop-flow-cycle",
                "a retained loop recursively contains its own flow proof")
        return cached[1]
    if type(flow) is not ir.LoopFlow or not flow.matches(
            instr.body, instr.trip_count):
        raise ir.RepresentationStructureError(
            "loop-flow-command-mismatch",
            "a retained loop's flow proof does not describe its command")
    if cfg.graph_fingerprint(flow.graph) != flow.graph_seal:
        raise ir.RepresentationStructureError(
            "loop-flow-graph-changed",
            "a retained loop's sealed control-flow graph changed")
    converted = cfg.if_convert(flow.graph)
    if ir.command_fingerprint(converted) != flow.proof_body_seal:
        raise ir.RepresentationStructureError(
            "loop-flow-proof-mismatch",
            "a retained loop's graph does not flatten to its proved body")

    cache[id(instr)] = (instr, _FLOW_IN_PROGRESS)
    try:
        checked = analyze(
            flow.graph, set(flow.entry_defined), _flow_cache=cache)
        exits = [
            set(checked.out_of(label)) - set(flow.entry_defined)
            for label in flow.graph.order()
            if (label in checked.out_masks
                and isinstance(flow.graph.blocks[label].term, cfg.Return))
        ]
        defined = set.intersection(*exits) if exits else set()
        required = checked.undefined_names
        if (tuple(sorted(required)) != flow.required
                or tuple(sorted(defined)) != flow.defined):
            raise ir.RepresentationStructureError(
                "loop-flow-facts-mismatch",
                "a retained loop's stated flow facts disagree with its graph")
    except BaseException:
        cache.pop(id(instr), None)
        raise
    cache[id(instr)] = (instr, flow)
    return flow


def loop_source_region(loop, *, checkpoint=None):
    """Locate an admitted loop's sealed source body, retaining its wrappers.

    Search and range transfer must agree on where the original CFG replaces
    its if-converted term. Target bindings, iterator checks and break/return
    guards remain outside that region. Sequence association is not semantic;
    an unrecognised body keeps the executable-term fallback. The caller owns
    the loop-flow validation, as it does for the surrounding procedure.
    """
    flow = loop.flow
    if flow is None:
        return None
    count = len(ir._parts(cfg.if_convert(flow.graph)))
    pending = [loop.body]
    while pending:
        if checkpoint is not None:
            checkpoint()
        node = pending.pop()
        parts = ir._parts(node)
        if len(parts) >= count and ir.command_fingerprint(
                ir._sequence(parts[-count:])) == flow.proof_body_seal:
            return node, tuple(parts[:-count]), flow.graph
        for part in parts:
            if isinstance(part, ir.If):
                pending.extend((part.orelse, part.then))
    return None


def analyze(graph, start, *, _flow_cache=None):
    """Compute feasible reaching definitions and undefined uses.

    One topological sweep carries a bounded partition of definition masks.
    Branches certified with the same immutable source atom retain its truth,
    so contradictory combinations are not paths.  Unlabelled branches remain
    conservative.  The entry sees ``start``: state and inputs available before
    the step executes.
    """
    flow_cache = {} if _flow_cache is None else _flow_cache
    order = tuple(graph.order())
    preds = graph.predecessors()
    names = set(start)
    loop_facts = {}
    for label in order:
        block = graph.blocks[label]
        for instr in block.instrs:
            names.update(_instr_reads(instr))
            if isinstance(instr, ir.For):
                loop_reads, loop_writes = _loop_names(instr)
                names.update(loop_reads)
                names.update(loop_writes)
                flow = loop_flow_facts(instr, _cache=flow_cache)
                loop_facts[id(instr)] = flow
                if flow is not None:
                    names.update(flow.required)
                    names.update(flow.defined)
            written = _instr_writes(instr)
            if written is not None:
                names.add(written)
        if isinstance(block.term, cfg.Branch):
            names.update(_reads(block.term.guard))
    names = tuple(sorted(names))
    bits = {name: 1 << index for index, name in enumerate(names)}
    start_mask = 0
    for name in start:
        start_mask |= bits[name]

    into, out_of, undefined = {}, {}, []
    path_out = {}
    for label in order:
        if label == graph.entry:
            states = ((frozenset(), start_mask),)
        else:
            coming = []
            for predecessor in dict.fromkeys(preds[label]):
                if predecessor in path_out:
                    coming.extend(_edge_states(
                        graph, predecessor, label, path_out[predecessor]))
            states = _normalise_path_states(coming)
        if not states:
            continue
        held = states[0][1]
        for _facts, mask in states[1:]:
            held &= mask
        into[label] = held
        block = graph.blocks[label]
        for instr in block.instrs:
            if isinstance(instr, ir.For):
                for name in _reads(instr.trip_count):
                    if any(not mask & bits[name]
                           for _facts, mask in states):
                        undefined.append((
                            name, (f"{name} is read by a loop count in {label} "
                                   "with no definition reaching it")))
                flow = loop_facts[id(instr)]
                if flow is not None:
                    for name in flow.required:
                        if any(not mask & bits[name]
                               for _facts, mask in states):
                            undefined.append((
                                name, (f"{name} is required on entry to the loop "
                                       f"retained by {label}")))
                    if flow.minimum > 0:
                        added = 0
                        # A nonempty source loop leaves its target bound even
                        # when the body never assigns it. The local body proof
                        # receives those bindings from the loop mechanics;
                        # they are not counted again in ``flow.defined``.
                        for name in set(flow.defined) | (set(flow.entry_defined) - {instr.index}):
                            added |= bits[name]
                        states = tuple(
                            (facts, mask | added) for facts, mask in states)
                else:
                    available = {name for name in names if held & bits[name]}
                    reasons = []
                    ir._reads_before_assignment(
                        instr.body, available | {instr.index}, (), reasons)
                    for reason in reasons:
                        name = reason.split(" ", 1)[0]
                        undefined.append((
                            name, f"{reason} in the loop retained by {label}"))
                    if (isinstance(instr.trip_count, ir.Lit)
                            and instr.trip_count.value > 0):
                        defined = ir._definite(
                            instr.body, available | {instr.index})
                        added = 0
                        for name in defined - {instr.index}:
                            added |= bits[name]
                        states = tuple(
                            (facts, mask | added) for facts, mask in states)
                held = states[0][1]
                for _facts, mask in states[1:]:
                    held &= mask
                continue
            for name in _instr_reads(instr):
                if any(not mask & bits[name]
                       for _facts, mask in states):
                    undefined.append((
                        name, (f"{name} is read in {label} with no definition "
                               "reaching it")))
            written = _instr_writes(instr)
            if written is not None:
                bit = bits[written]
                states = tuple(
                    (facts, mask | bit) for facts, mask in states)
                held |= bit
        if isinstance(block.term, cfg.Branch):
            for name in _reads(block.term.guard):
                if any(not mask & bits[name]
                       for _facts, mask in states):
                    undefined.append((
                        name, (f"{name} is branched on in {label} with no "
                               "definition reaching it")))
        out_of[label] = held
        path_out[label] = _normalise_path_states(states)
    return ReachingAnalysis(names, into, out_of, tuple(undefined))


def reaching(graph, start):
    """What is defined on entry and exit of each block.

    This compatibility view materialises sets. New production callers should
    use :func:`analyze` so diagnostics and exit facts share one pass.
    """
    result = analyze(graph, start)
    return ({label: result.into(label) for label in result.into_masks},
            {label: result.out_of(label) for label in result.out_masks})


def _undefined(graph, start):
    """Pairs of undefined names and their explanations."""
    return list(analyze(graph, start).undefined)


def undefined_names(graph, start):
    """Names read on a graph path before a definition reaches the read."""
    return {name for name, _reason in _undefined(graph, start)}


def undefined_uses(graph, start):
    """Uses that some path through the graph reaches without a definition.

    Exact about the graph. Each is reported with the block it is in, so that a
    reader can see which path is at issue rather than being told a name.
    """
    return [reason for _name, reason in _undefined(graph, start)]
