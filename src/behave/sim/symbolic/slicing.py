"""The commands a goal's query needs, and the ones it does not.

A query asks whether one goal is reached. The formula it is asked against is the
whole step, and most of the step has nothing to do with that goal: a model with
thirty-seven goals encodes the same three hundred commands thirty-seven times,
once in each worker, and each query then reasons about all of them.

What a goal needs is its **cone of influence**, computed backward from every
command that reports it.

*Data.* An assignment is needed when it writes a name the cone needs. Reading it
backward is what makes this cheap: the cone starts as the names the goal's guards
read, and grows only along real dependencies.

*Control.* Keeping a command keeps the `if`s enclosing it, and their guards are
then needed too. A goal inside three branches needs those three conditions and
whatever computes them.

*Failure, which is the rule that decides whether this is worth anything.* A
`fail` before the goal ends the run, so whether the goal is reached at all
depends on it -- and a division, a shift and an index check each carry one. Every
such command is in the cone regardless of what it computes, along with its
guards. This is why a cone is not as small as a data-flow slice would suggest,
and why the number had to be measured rather than assumed.

*Iteration.* A selected command in a loop can be reached again. The body is
therefore closed cyclically: a suffix update or failure can affect the next
visit even though it is textually after the selected occurrence.

**The soundness direction is one-sided and worth naming.** A cone that is too
large costs time. A cone that is too small produces a formula in which the goal
is reachable along a path the real step cannot take, or unreachable because a
definition that reaches it was dropped -- and `unreachable` is a claim over every
admissible trace, so an over-small cone is a soundness fault, not a slow query.
Every rule here therefore errs toward keeping. Tests compare whole and reduced
hit conditions at the same boundary values as well as their overall verdicts.

Branches are walked independently and their entry needs are joined. A write in
one arm does not kill the other arm's incoming value; writes in both arms can
kill an obsolete value. Guards are read at branch entry, before either arm's
writes. Retained loops still use the conservative cyclic closure below.
"""

from __future__ import annotations

from ..core.frozen import Frozen
from . import ir

__all__ = ["OccurrenceRunSlice", "Slice", "SliceIndex", "audit",
           "audit_occurrence", "audit_occurrence_run", "boundary_cone", "cone",
           "for_boundary", "for_occurrence_run", "for_run", "index", "linearize",
           "occurrence_run", "prune", "reads", "sliced", "state_range_graph"]


def reads(expr, out):
    """Add dependencies using the shared, iterative expression-DAG walk."""
    out.update(ir._reads(expr))


def _parts(cmd):
    parts, stack = [], [cmd]
    while stack:
        node = stack.pop()
        if isinstance(node, ir.Seq):
            stack.append(node.second)
            stack.append(node.first)
        else:
            parts.append(node)
    return parts


def _loop_bodies(events):
    """Commands that may execute on another iteration of each loop."""
    bodies = {}
    for command, under in events:
        for outer in under:
            if isinstance(outer, ir.For):
                bodies.setdefault(id(outer), []).append(command)
    return {identity: tuple(commands)
            for identity, commands in bodies.items()}


def _loop_parents(events):
    """All enclosing controls, including shared commands' distinct contexts."""
    parents = {}
    for command, under in events:
        parents.setdefault(id(command), []).extend(under)
    return parents


def _goal_positions(events):
    """Index every occurrence, preserving the linearized execution order."""
    positions = {}
    for site, (command, _under) in enumerate(events):
        if isinstance(command, (ir.Mark, ir.Fail)):
            positions.setdefault(command.goal, []).append(site)
    return {goal: tuple(sites) for goal, sites in positions.items()}


def _command_reads(command, out, cache=None):
    """Add the direct reads of one linearized command to ``out``."""
    if cache is not None:
        identity = id(command)
        if identity not in cache:
            names = set()
            _command_reads(command, names)
            cache[identity] = frozenset(names)
        out.update(cache[identity])
        return
    if isinstance(command, ir.Assign):
        reads(command.expr, out)
    elif isinstance(command, ir.Store):
        out.add(command.var)
        reads(command.index, out)
        reads(command.expr, out)
    elif isinstance(command, ir.If):
        reads(command.guard, out)
    elif isinstance(command, ir.For):
        reads(command.trip_count, out)


def _close_loop(loop, members, parents, kept, needed, retain_failures, reads_cache=None):
    """Close a loop's data/control dependencies without unrolling it.

    Every write of a needed name is retained, regardless of order or branch:
    a suffix write may reach the next iteration, and a conditional write does
    not kill the incoming value. Every failure can prevent a later visit.
    Nested loop indices are writes too. Unrelated computations and marks need
    not be encoded. The finite worklist visits each command at most once.
    """
    writers = {}
    pending = [loop]
    for command in members:
        if isinstance(command, (ir.Assign, ir.Store)):
            writers.setdefault(command.var, []).append(command)
        elif isinstance(command, ir.For):
            writers.setdefault(command.index, []).append(command)
        if id(command) in kept or (
                retain_failures and isinstance(command, ir.Fail)):
            pending.append(command)
    for name in needed:
        pending.extend(writers.get(name, ()))

    visited = set()
    while pending:
        command = pending.pop()
        identity = id(command)
        if identity in visited:
            continue
        visited.add(identity)
        kept.add(identity)
        dependencies = set()
        _command_reads(command, dependencies, reads_cache)
        for name in dependencies - needed:
            pending.extend(writers.get(name, ()))
        needed.update(dependencies)
        pending.extend(parents.get(identity, ()))


def linearize(cmd):
    """Every command in execution order, each with the constructs enclosing it.

    Both arms are recorded, not executed sequentially. Their boundaries let
    the reverse dependency walk fork at the join and merge at branch entry.

    A `for` appears once with its body. If a cone enters it, `cone` closes over
    the body before slicing code outside the loop; unrolling the event list is
    unnecessary.
    """
    events, stack = [], [(cmd, ())]
    while stack:
        node, under = stack.pop()
        if isinstance(node, ir.Seq):
            for part in reversed(_parts(node)):
                stack.append((part, under))
        elif isinstance(node, ir.If):
            events.append((node, under))
            deeper = under + (node,)
            stack.append((node.orelse, deeper))
            stack.append((node.then, deeper))
        elif isinstance(node, ir.For):
            events.append((node, under))
            stack.append((node.body, under + (node,)))
        else:
            events.append((node, under))
    return events


def _branch_bounds(events, stop=None):
    """Index arm boundaries once, without repeatedly linearizing subtrees."""
    sizes, branches = {}, {}
    for position in range(len(events) if stop is None else stop):
        command, _under = events[position]
        if not isinstance(command, ir.If):
            continue
        pending = [(command, False)]
        while pending:
            node, ready = pending.pop()
            if id(node) in sizes:
                continue
            children = ((node.first, node.second) if isinstance(node, ir.Seq) else
                        (node.then, node.orelse) if isinstance(node, ir.If) else
                        (node.body,) if isinstance(node, ir.For) else ())
            if children and not ready:
                pending.append((node, True))
                pending.extend((child, False) for child in children)
            else:
                sizes[id(node)] = int(not isinstance(node, ir.Seq)) + sum(sizes[id(c)] for c in children)
        branches[position] = position + 1 + sizes[id(command.then)], position + sizes[id(command)]
    return branches


def _reverse_cones(events, first, sites, needed, *, roots=(), active=0,
                   retain_failures=True, bodies=None, parents=None, reads_cache=None,
                   branches=None):
    """One branch-aware reverse walk, with one bit per independent query.

    A branch receives the same suffix needs in both arms. New needs inside one
    arm cannot select definitions in its sibling. Joining their entry needs
    retains a missing arm's old value, and guards are read only at the header.
    Loops retain the existing cyclic closure, including zero-iteration support.
    Variable/query pairs form one immutable bit set: branch snapshots share it
    instead of copying every live variable into a dict at every nesting level.
    """
    branches = _branch_bounds(events, first + 1) if branches is None else branches
    ends, middles, entries = {}, {}, {}
    for start, (middle, end) in branches.items():
        if start >= first:
            continue
        end, middle = min(end, first + 1), min(middle, first + 1)
        ends.setdefault(end, []).append((start, middle))
        if middle < end:
            middles[middle] = start
        entries[start + 1] = start
    frames, kept, offsets = {}, {}, {}
    width = max(active, *sites.values(), *needed.values(), 1).bit_length()
    field_mask, live = (1 << width) - 1, 0

    def require(name, mask):
        nonlocal live
        if mask:
            offset = offsets.setdefault(name, len(offsets) * width)
            live |= mask << offset

    def users(name):
        offset = offsets.get(name)
        return 0 if offset is None else (live >> offset) & field_mask

    for name, mask in needed.items():
        require(name, mask)
    terminal_needs = live

    def read(command, mask):
        if mask:
            names = set()
            _command_reads(command, names, reads_cache)
            for name in names:
                require(name, mask)

    def keep(command, under, mask):
        if mask:
            for node in (command, *under):
                kept[id(node)] = kept.get(id(node), 0) | mask

    for position in range(first, -1, -1):
        yield None
        boundary = position + 1
        if boundary in entries:
            _suffix, right = frames.pop(entries[boundary])
            live |= right
        if boundary in middles:
            frame = frames[middles[boundary]]
            frame[1], live = live, frame[0]
        # Shared ends enter outer scopes first. A cut inside a then arm has
        # an empty else prefix, which still owes the unchanged suffix needs.
        for start, middle in ends.get(boundary, ()):
            frames[start] = [live, live if middle == boundary else None]
        command, under = events[position]
        rooted = 1 if id(command) in roots else 0
        selected = sites.get(position, 0)
        active |= selected
        keep(command, under, selected | rooted)
        if isinstance(command, (ir.Assign, ir.Store)):
            mask = users(command.var) | rooted
            if mask:
                if isinstance(command, ir.Assign) and not any(isinstance(c, ir.For) for c in under):
                    if command.var in offsets:
                        live &= ~(field_mask << offsets[command.var])
                read(command, mask)
                keep(command, under, mask)
        elif isinstance(command, ir.If):
            read(command, kept.get(id(command), 0))
        elif isinstance(command, ir.Fail) and retain_failures:
            keep(command, under, active)
            # A requested end-state value is also observed at an early halt.
            # Later assignments cannot overwrite the value on that prefix.
            # Goal-only cones have no terminal value roots to reintroduce.
            live |= terminal_needs
        elif isinstance(command, ir.For):
            keep(command, under, users(command.index))
            mask = kept.get(id(command), 0)
            while mask:
                bit = mask & -mask
                names = {name for name in offsets if users(name) & bit}
                commands = {key for key, users in kept.items() if users & bit}
                _close_loop(command, bodies.get(id(command), ()), parents,
                            commands, names, retain_failures, reads_cache)
                for name in names:
                    require(name, bit)
                for key in commands:
                    kept[key] = kept.get(key, 0) | bit
                mask ^= bit
    return kept, {name: mask for name in offsets if (mask := users(name))}


_SLICE_INDEX_TOKEN = object()


class SliceIndex:
    """One reusable dependency index for an immutable procedure body.

    Goal decomposition asks many occurrence questions of the same large IR.
    Rebuilding the linear event stream and, especially, every loop-membership
    list for each question retains gigabytes of allocator arenas on large
    procedures.  This invocation-local value computes those body facts once;
    all-goal and multi-step consumers also reuse its parent and occurrence
    tables. It contains no query answer and grants no authority to choose a slice.
    """

    __slots__ = ("_bindings", "_commands", "_name_bits", "branches", "command_reads",
                 "events", "goal_positions", "loop_bodies", "parents", "procedure", "token")

    def __init__(self, procedure, token):
        if token is not _SLICE_INDEX_TOKEN:
            raise TypeError("a slice index is verifier-owned")
        self.procedure = procedure
        self.events = tuple(linearize(procedure.body))
        self.loop_bodies = _loop_bodies(self.events)
        self.parents = _loop_parents(self.events) if self.loop_bodies else {}
        self.goal_positions = _goal_positions(self.events)
        self.branches = _branch_bounds(self.events)
        # Names read by immutable commands are independent of the queried goal,
        # loop visit and concrete state. This index owns their command objects.
        self.command_reads = {}
        self._commands = {id(command): command for command, _under in self.events}
        self._name_bits, self._bindings = {}, {}
        self.token = token

    def checked_for(self, procedure):
        if self.token is not _SLICE_INDEX_TOKEN:
            raise TypeError("the slice-index seal is invalid")
        if procedure is not self.procedure:
            raise ValueError("a slice index belongs to another procedure")
        return self

    def _occurrence_masks(self, occurrences):
        """Run acyclic occurrence walks together, with one bit per question.

        Each bit obeys the unchanged reverse ``cone`` rules: introduce the
        goal at its own occurrence, preserve conditional reaching definitions,
        and keep every preceding failure. Shared command/control reads are
        processed once for all active questions. Yield checkpoints retain the
        unfinished batch; no incomplete selection is published. Retained loops
        continue to use the existing cyclic closure instead of this method.
        """
        if self.loop_bodies:
            raise ValueError("retained loops require cyclic occurrence closure")
        occurrences = tuple(occurrences)
        sites = {}
        for index, (site, goal) in enumerate(occurrences):
            if (type(site) is not int or not 0 <= site < len(self.events)
                    or not isinstance(self.events[site][0], (ir.Mark, ir.Fail))
                    or self.events[site][0].goal != goal or site in sites):
                raise ValueError("an occurrence batch names distinct reporting sites")
            sites[site] = 1 << index
        kept, needed = yield from _reverse_cones(self.events, max(sites, default=-1),
            sites, {}, reads_cache=self.command_reads, branches=self.branches)
        return occurrences, kept, needed

    def occurrence_batch(self, occurrences):
        """Materialize individual cones from the shared reverse walk."""
        occurrences, kept, needed = yield from self._occurrence_masks(occurrences)
        cuts = [set() for _ in occurrences]
        needs = [set() for _ in occurrences]
        for values, outputs in ((kept, cuts), (needed, needs)):
            for value, mask in values.items():
                yield None
                while mask:
                    bit = mask & -mask
                    outputs[bit.bit_length() - 1].add(value)
                    mask ^= bit
        for key, cut, names in zip(occurrences, cuts, needs):
            yield key, Slice(key[1], cut, len(self.events), names)

    def occurrence_components(self, occurrences, computations):
        """Union overlapping cones without expanding every command/question pair.

        Only real computations connect questions, not shared control ancestry.
        The masks also perform each singleton's read/bound audit independently:
        another member's writes cannot conceal a missing definition. Returned
        groups are exact unions of ``occurrence_batch`` selections, not proofs.
        """
        occurrences, kept, _needed = yield from self._occurrence_masks(occurrences)
        parents = list(range(len(occurrences)))
        members = [1 << index for index in parents]

        def root(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        for identity, mask in kept.items():
            yield None
            if identity not in computations or not mask:
                continue
            first = root((mask & -mask).bit_length() - 1)
            mask &= ~members[first]
            while mask:
                yield None
                other = root((mask & -mask).bit_length() - 1)
                # Union by size bounds parent depth; erase a whole component
                # from the remaining question mask, not one bit at a time.
                if members[first].bit_count() < members[other].bit_count():
                    first, other = other, first
                parents[other] = first
                members[first] |= members[other]
                mask &= ~members[first]

        groups, cuts = {}, {}
        for index, key in enumerate(occurrences):
            owner = root(index)
            groups.setdefault(owner, []).append(key)
            cuts.setdefault(owner, set())
        reads, writes = {}, {}
        for identity, mask in kept.items():
            yield None
            command = self._commands[identity]
            names = set()
            _command_reads(command, names, self.command_reads)
            for name in names:
                reads[name] = reads.get(name, 0) | mask
            if isinstance(command, (ir.Assign, ir.Store)):
                writes[command.var] = writes.get(command.var, 0) | mask
            while mask:
                owner = root((mask & -mask).bit_length() - 1)
                cuts[owner].add(identity)
                mask &= ~members[owner]
        bound = set(self.procedure.state) | set(self.procedure.inputs)
        faults = {}
        for name, mask in reads.items():
            yield None
            mask = 0 if name in bound else mask & ~writes.get(name, 0)
            while mask:
                owner = root((mask & -mask).bit_length() - 1)
                faults.setdefault(owner, set()).add(name)
                mask &= ~members[owner]
        merged = {}
        for owner, keys in groups.items():
            yield None
            keep = frozenset(cuts[owner])
            selected, missing = merged.setdefault(keep, ([], set()))
            selected.extend(keys)
            missing.update(faults.get(owner, ()))
        for keep, (keys, missing) in merged.items():
            yield keep, tuple(sorted(keys)), tuple(sorted(missing))

    def binding_faults(self, keep):
        """Independent read/bound audit using immutable per-command masks."""
        def mask(names):
            value = 0
            for name in names:
                if name not in self._name_bits:
                    self._name_bits[name] = 1 << len(self._name_bits)
                value |= self._name_bits[name]
            return value

        bound = mask((*self.procedure.state, *self.procedure.inputs))
        read = 0
        for identity in keep:
            command = self._commands.get(identity)
            if command is None:
                continue
            if identity not in self._bindings:
                names = set()
                _command_reads(command, names, self.command_reads)
                written = ((command.var,) if isinstance(command, (ir.Assign, ir.Store)) else
                           (command.index,) if isinstance(command, ir.For) else ())
                self._bindings[identity] = mask(names), mask(written)
            reads, writes = self._bindings[identity]
            read |= reads
            bound |= writes
        missing = read & ~bound
        return tuple(sorted(name for name, bit in self._name_bits.items() if bit & missing))


def index(procedure):
    """Build the reusable dependency index of one immutable procedure."""

    return SliceIndex(procedure, _SLICE_INDEX_TOKEN)


def goal_state_dependencies(procedure):
    """Ordered reaching-state supports for query grouping, never slicing.

    Build one shared version graph, not one backward body walk per goal.
    Assignments replace their reaching value; branches merge their writes,
    loops have explicit entry/backedge joins, and persistent state connects
    successive transactions. Array stores retain the previous array and
    index dependencies. The finite bit propagation below is conservative;
    these masks organize work, never authorize pruning or a solver verdict.
    """
    from collections import ChainMap, deque
    from behave.isolation.preparation_control import check_preparation

    dependencies, goal_reads = [set()], {}

    def node(sources=(), *, join=False):
        sources = set(sources) - {0}
        if not join and len(sources) <= 1:
            return next(iter(sources), 0)
        identity = len(dependencies)
        dependencies.append(sources)
        return identity

    entry = {name: node(join=True) for name in procedure.state}
    environment = ChainMap(dict(entry))
    expression_reads = {}

    def value(expression, reaching):
        identity = id(expression)
        if identity not in expression_reads:
            names = set()
            reads(expression, names)
            expression_reads[identity] = names
        return node(reaching.get(name, 0) for name in expression_reads[identity])

    # The index records nested loop members once, independently of goals.
    bodies = _loop_bodies(linearize(procedure.body))
    pending = [("visit", procedure.body, environment, 0)]
    while pending:
        check_preparation()
        action, *arguments = pending.pop()
        if action == "branch":
            reaching, left, right, control = arguments
            for name in left.keys() | right.keys():
                reaching[name] = node((control, left.get(name, reaching.get(name, 0)),
                                      right.get(name, reaching.get(name, 0))))
            continue
        if action == "loop":
            reaching, body, joins, control = arguments
            for name, header in joins.items():
                dependencies[header].add(body.get(name, 0))
                reaching[name] = node((header, control))
            continue
        command, reaching, control = arguments
        if isinstance(command, ir.Seq):
            pending.extend((("visit", command.second, reaching, control),
                            ("visit", command.first, reaching, control)))
        elif isinstance(command, ir.If):
            guarded = node((control, value(command.guard, reaching)))
            left, right = reaching.new_child(), reaching.new_child()
            pending.extend((("branch", reaching, left.maps[0], right.maps[0], guarded),
                            ("visit", command.orelse, right, guarded),
                            ("visit", command.then, left, guarded)))
        elif isinstance(command, ir.For):
            guarded = node((control, value(command.trip_count, reaching)))
            written = {command.index}
            for member in bodies[id(command)]:
                if isinstance(member, (ir.Assign, ir.Store)):
                    written.add(member.var)
                elif isinstance(member, ir.For):
                    written.add(member.index)
            joins = {name: node((reaching.get(name, 0),), join=True)
                     for name in written}
            body = reaching.new_child(dict(joins))
            body[command.index] = guarded
            pending.extend((("loop", reaching, body, joins, guarded),
                            ("visit", command.body, body, guarded)))
        elif isinstance(command, ir.Assign):
            reaching[command.var] = node((control, value(command.expr, reaching)))
        elif isinstance(command, ir.Store):
            reaching[command.var] = node((control, reaching.get(command.var, 0),
                value(command.index, reaching), value(command.expr, reaching)))
        elif isinstance(command, (ir.Mark, ir.Fail)) and command.goal is not None:
            goal_reads.setdefault(command.goal, set()).add(control)

    for name, boundary in entry.items():
        dependencies[boundary].add(environment[name])

    readers = {}
    for written, used in enumerate(dependencies):
        for source in used:
            readers.setdefault(source, set()).add(written)
    masks = {entry[name]: 1 << index for index, name in enumerate(procedure.state)}
    queue, queued = deque(masks), set(masks)
    while queue:
        check_preparation()
        name = queue.popleft()
        queued.remove(name)
        for written in readers.get(name, ()):
            old = masks.get(written, 0)
            new = old | masks[name]
            if new != old:
                masks[written] = new
                if written not in queued:
                    queue.append(written)
                    queued.add(written)
    result = {}
    for goal, used in goal_reads.items():
        mask = 0
        for name in used:
            mask |= masks.get(name, 0)
        result[goal] = mask
    return result


class Slice:
    """Which commands one goal's query needs, and how much of the step that is.

    ``needs`` is what is still live at the step's entry, which is how a cone
    crosses a step boundary: a goal at the last step of a run depends on state
    the earlier steps wrote, and those steps are sliced against these names.
    """

    __slots__ = ("goal", "kept", "needs", "total")

    def __init__(self, goal, kept, total, needs=()):
        self.goal = goal
        self.kept = kept            # ids of the commands in the cone
        self.total = total          # commands in the whole step
        self.needs = frozenset(needs)

    def __contains__(self, cmd):
        return id(cmd) in self.kept

    @property
    def share(self):
        return len(self.kept) / self.total if self.total else 1.0

    def __repr__(self):
        return (f"Slice({self.goal!r}, {len(self.kept)}/{self.total} = "
                f"{self.share:.1%})")


def cone(events, goal=None, seed=(), total=None, *, _retain_failures=True,
         _bodies=None, _parents=None, _site=None, _sites=None, _goal_positions=None,
         _reads=None, _roots=(), _branches=None):
    """The cone of ``goal`` over a linearized body, or ``None`` if it is absent.

    ``events`` comes from `linearize`, and is computed once for a body and reused
    for every goal in it: the walk is the cost, and doing it per goal is how a
    slicer comes to cost more than the queries it saves.

    With ``goal=None`` the whole body is behind the cut and ``seed`` is what the
    step after this one needs from it. That is the earlier step of a run: it has
    no goal of its own to serve, and what it owes the query is the state it
    leaves plus every failure it could have halted on.
    """
    # **Every** site, and the cut goes after the last of them. One goal is
    # reported by more than one command whenever the lowering duplicates the
    # source that reports it -- an inlined call is the ordinary case, and a
    # comprehension expansion is another -- and the encoder's hit is the
    # disjunction over all of them: `hits[g] = Or(seen, reached)`.
    #
    # Cutting at the first site was this module's real bug, and it failed in the
    # direction that matters. On `DivSqrtRecF64_mulAddZ31`, 14 of 92 goals have
    # two sites, and three of them came back **`unsat` sliced against `sat`
    # whole**: the trace that reaches the goal reaches it at the second site,
    # which the cone had cut away. A goal called unreachable that is reachable is
    # the fault this whole module has to not have.
    if _site is not None and _sites is not None:
        raise TypeError("select one occurrence or one occurrence set, not both")
    if (_site is not None or _sites is not None) and goal is None:
        raise TypeError("an occurrence cut needs the goal reported there")
    if _site is not None and (type(_site) is not int or _site < 0):
        raise TypeError("a goal occurrence is a nonnegative event position")
    if goal is None:
        sites = []
        at = len(events)
    else:
        if (_site is not None and _site < len(events)
                and isinstance(events[_site][0], (ir.Mark, ir.Fail))
                and events[_site][0].goal == goal):
            # A known occurrence needs no whole-body search for its siblings.
            sites = [_site]
        elif _goal_positions is not None:
            sites = _goal_positions.get(goal, ())
        else:
            sites = [i for i, (c, _) in enumerate(events)
                     if isinstance(c, (ir.Mark, ir.Fail)) and c.goal == goal]
        if not sites:
            return None
        if _sites is not None:
            if type(_sites) not in (tuple, list):
                raise TypeError("a selected occurrence set is a finite sequence")
            selected = tuple(_sites)
            if not selected or len(selected) != len(set(selected)) \
                    or any(type(site) is not int or site < 0
                           for site in selected):
                raise ValueError(
                    "selected occurrences are distinct nonnegative positions")
            missing = sorted(set(selected) - set(sites))
            if missing:
                raise ValueError(
                    f"events {missing[:4]} are not occurrences of goal {goal!r}")
            sites = list(selected)
        elif _site is not None:
            if _site not in sites:
                raise ValueError(
                    f"event {_site} is not an occurrence of goal {goal!r}")
            sites = [_site]
        at = max(sites)

    roots = frozenset(_roots)
    if roots:
        last_root = max((i for i, (cmd, _under) in enumerate(events)
                         if id(cmd) in roots), default=-1)
        initial_cut = at if goal is not None else (len(events) - 1 if seed else -1)
        at = max(initial_cut, last_root)
    bodies = _loop_bodies(events) if _bodies is None else _bodies
    parents = (_loop_parents(events) if bodies else {}) if _parents is None else _parents
    # Activate an occurrence's dependencies only when the backwards walk
    # reaches that occurrence.  Seeding every occurrence before the walk is
    # subtly wrong: an unconditional definition between two occurrences then
    # kills a need belonging to the earlier occurrence.  The earlier reaching
    # definition is dropped, so the reduced formula can read the entry value
    # where the complete step reads the assigned value.
    sites = frozenset(sites)
    first = at if sites or roots else at - 1
    from ..core.construction_checkpoint import finish_construction

    kept, needed = finish_construction(_reverse_cones(events, first,
        {position: 1 for position in sites}, {name: 1 for name in seed}, roots=roots,
        active=1, retain_failures=_retain_failures, bodies=bodies, parents=parents,
        reads_cache=_reads, branches=_branches))
    return Slice(goal, set(kept), total if total is not None else len(events), needed)


def state_range_graph(proc):
    """The sealed CFG projected to commands that can affect next state.

    Range planning needs a sound cover of normal state successors.  It does not
    need output-only computation.  Work backward from every normal return,
    retaining assignments that feed a state field and the guards controlling
    them.  A structured command is retained as a whole when it may write a live
    name; this errs only toward more work.

    The source CFG is essential here.  Pruning the if-converted term would lose
    the relation between a branch predicate and the state range it refines.  A
    procedure without a sealed graph therefore returns ``None`` and its caller
    uses the complete term.
    """

    graph = proc.graph
    if graph is None:
        return None

    from . import cfg
    from . import ranges

    live_at = {}
    kept_at = {}
    changed = False
    state = set(proc.state)
    for label in reversed(graph.order()):
        block = graph.blocks[label]
        term = block.term
        if isinstance(term, cfg.Return):
            live = set(state)
        elif isinstance(term, cfg.Goto):
            live = set(live_at.get(term.target, ()))
        elif isinstance(term, cfg.Branch):
            live = (set(live_at.get(term.then, ())) |
                    set(live_at.get(term.orelse, ())))
            reads(term.guard, live)
        else:
            # Fail and Pruned have no normal successor.
            live = set()

        kept = []
        for command in reversed(block.instrs):
            retain = False
            if isinstance(command, ir.Assign):
                if command.var in live:
                    retain = True
                    live.discard(command.var)
                    reads(command.expr, live)
            elif isinstance(command, ir.Store):
                if command.var in live:
                    retain = True
                    live.add(command.var)
                    reads(command.index, live)
                    reads(command.expr, live)
            else:
                footprint, written = ranges._range_footprint(command)
                if written & live:
                    retain = True
                    live.update(footprint)
            if retain:
                kept.append(command)
            else:
                changed = True
        kept_at[label] = tuple(reversed(kept))
        live_at[label] = frozenset(live)

    if not changed:
        return graph
    return cfg.CFG({
        label: cfg.Block(
            label, kept_at.get(label, ()), graph.blocks[label].term)
        for label in graph.blocks
    }, graph.entry)


def _binding_faults(proc, keep, events, reads_cache=None):
    """Names read by a retained command but absent from its procedure boundary."""

    bound = set(proc.state) | set(proc.inputs)
    read = set()
    for cmd, _under in events:
        if id(cmd) not in keep:
            continue
        if isinstance(cmd, ir.Assign):
            bound.add(cmd.var)
        elif isinstance(cmd, ir.Store):
            bound.add(cmd.var)
        elif isinstance(cmd, ir.For):
            # The loop binds its own index, which the encoder substitutes per
            # iteration rather than reading from the state.
            bound.add(cmd.index)
        _command_reads(cmd, read, reads_cache)
    return tuple(sorted(read - bound))


def _omitted_fault(events, required, keep, what):
    omitted = set(required) - set(keep)
    if not omitted:
        return ()
    positions = {
        id(command): (index, type(command).__name__)
        for index, (command, _under) in enumerate(events)
    }
    sample = sorted(positions[identity] for identity in omitted)[:4]
    return (f"the slice omits {len(omitted)} command(s) required {what}: {sample}",)


def audit(proc, goals, keep, events=None, *, _index=None):
    """Why this slice would not answer what the whole step answers, or ``()``.

    Retained commands must be closed under three obligations: every read is
    bound, every occurrence of each requested goal is present, and each
    occurrence retains its own reaching definitions. The last is temporal: a
    write after an earlier occurrence cannot satisfy its read. Any diagnostic
    makes the caller use the complete step.
    """
    if _index is not None:
        indexed = _index.checked_for(proc)
        if events is not None and tuple(events) != indexed.events:
            raise ValueError("events and slice index describe different bodies")
        events, bodies, parents = indexed.events, indexed.loop_bodies, indexed.parents
        positions = indexed.goal_positions
    else:
        events = events if events is not None else linearize(proc.body)
        bodies = _loop_bodies(events)
        parents = _loop_parents(events) if bodies else {}
        positions = _goal_positions(events)
    missing = _binding_faults(proc, keep, events)
    if missing:
        return missing
    # And the goals themselves: a slice that dropped a site would answer a
    # different question, so it is not enough that the cone meant to keep them.
    for goal in goals:
        sites = positions.get(goal, ())
        if sites and not all(id(events[site][0]) in keep for site in sites):
            return ((f"goal {goal} is reported at {len(sites)} sites and the "
                     "slice keeps only some of them"),)
    # A global read/bound set cannot establish temporal reaching definitions:
    # a later write has the same name as the definition an earlier occurrence
    # needs.  Recompute each occurrence-aware cone and require this candidate
    # to cover it.  This is also the fail-closed check for a stored or otherwise
    # externally supplied command set.
    required = set()
    branches = indexed.branches if _index is not None else _branch_bounds(events)
    for goal in goals:
        cut = cone(events, goal, total=len(events), _bodies=bodies,
                   _parents=parents, _goal_positions=positions, _branches=branches)
        if cut is not None:
            required.update(cut.kept)
    return _omitted_fault(
        events, required, keep, "before a goal occurrence")


def audit_occurrence(proc, goal, site, keep, events=None, *, _index=None):
    """Why ``keep`` cannot answer one lowered occurrence, or ``()``.

    This is the candidate-generation counterpart of :func:`audit`.  A public
    goal may be reported by several inlined commands.  Searching one command is
    useful for finding a witness, but a negative answer has authority only after
    every occurrence has been searched.  This audit therefore requires exactly
    the dependencies of ``site``; it deliberately does not require the other
    commands reporting the same public goal.
    """

    if _index is not None:
        indexed = _index.checked_for(proc)
        if events is not None and events is not indexed.events and tuple(events) != indexed.events:
            raise ValueError("events and slice index describe different bodies")
        events, bodies, parents = indexed.events, indexed.loop_bodies, indexed.parents
    else:
        events = events if events is not None else linearize(proc.body)
        bodies, parents = _loop_bodies(events), None
    try:
        cut = cone(
            events, goal, total=len(events), _bodies=bodies,
            _parents=parents, _site=site,
            _branches=indexed.branches if _index is not None else None)
    except (TypeError, ValueError) as exc:
        return (str(exc),)
    if cut is None:
        return (f"goal {goal!r} has no lowered occurrence",)
    # Reuse the structural check without asking the all-occurrence audit to
    # require the sites this candidate query intentionally leaves for later.
    missing = _binding_faults(proc, keep, events)
    if missing:
        return missing
    selected = events[site][0]
    if id(selected) not in keep:
        return (f"the slice omits occurrence {site} of goal {goal!r}",)
    return _omitted_fault(
        events, cut.kept, keep,
        f"before occurrence {site} of goal {goal!r}")


_OCCURRENCE_RUN_TOKEN = object()


class OccurrenceRunSlice(Frozen):
    """A sealed command cone for one occurrence-fragment query.

    Planning a large public goal can be more expensive than encoding it when the
    same lowered source site was inlined hundreds of times.  This value carries the
    verifier-computed cone from planning to encoding, so neither the encoder nor an
    audit recomputes it.  Callers can inspect its finite work but cannot construct or
    rebind a slice with authority of their own.
    """

    __slots__ = (
        "goal", "keeps", "procedure", "sites", "steps", "target_step", "token",
    )

    def __init__(self, procedure, goal, sites, target_step, steps, keeps, token):
        if token is not _OCCURRENCE_RUN_TOKEN:
            raise TypeError("an occurrence-run slice is verifier-owned")
        self.procedure = procedure
        self.goal = goal
        self.sites = tuple(sites)
        self.target_step = target_step
        self.steps = steps
        self.keeps = tuple(frozenset(keep) for keep in keeps)
        self.token = token
        self._freeze()

    @property
    def work(self):
        return sum(len(keep) for keep in self.keeps)

    def checked_for(self, procedure, goal, steps):
        if self.token is not _OCCURRENCE_RUN_TOKEN:
            raise TypeError("the occurrence-run slice seal is invalid")
        if procedure is not self.procedure:
            raise ValueError("an occurrence-run slice belongs to another procedure")
        if goal != self.goal or steps != self.steps:
            raise ValueError("an occurrence-run slice belongs to another query")
        return self


def occurrence_run(proc, goal, sites, target_step, steps, events=None, *,
                   _index=None):
    """Per-step cones for selected occurrences of one bounded-run goal.

    ``sites`` are occurrences in the target step.  That step retains their
    reaching definitions and earlier failures.  Each preceding step retains
    exactly the state computation and failures needed to reach the next one.
    Commands after the target step cannot change whether the goal was already
    reached, so those steps are the identity relation.  Their input variables
    remain in the run formula, which still decodes a trace of exactly ``steps``
    rows for the bound domain.

    Splitting a public goal this way changes only how its disjunction is
    executed.  SAT in any batch is a witness for the public goal; UNSAT has
    authority only after every site at every target step has been checked.
    """

    if type(goal) is not str or not goal:
        raise TypeError("an occurrence run names one nonempty goal")
    if type(steps) is not int or steps < 1:
        raise ValueError("an occurrence run has a positive step bound")
    if type(target_step) is not int or not 0 <= target_step < steps:
        raise ValueError("an occurrence run targets a step inside its bound")
    if type(sites) not in (tuple, list):
        raise TypeError("an occurrence run names a finite site sequence")
    sites = tuple(sites)
    if not sites or len(sites) != len(set(sites)) \
            or any(type(site) is not int or site < 0 for site in sites):
        raise ValueError("an occurrence run names distinct nonnegative sites")

    if _index is not None:
        indexed = _index.checked_for(proc)
        if events is not None and tuple(events) != indexed.events:
            raise ValueError("events and slice index describe different bodies")
        events, bodies, parents = indexed.events, indexed.loop_bodies, indexed.parents
        positions = indexed.goal_positions
    else:
        events = linearize(proc.body) if events is None else events
        bodies = _loop_bodies(events)
        parents = _loop_parents(events) if bodies else {}
        positions = _goal_positions(events)
    branches = indexed.branches if _index is not None else _branch_bounds(events)
    target_cut = cone(
        events, goal, total=len(events), _bodies=bodies, _sites=sites,
        _parents=parents, _goal_positions=positions, _branches=branches)
    # ``cone`` raises for a position that does not report ``goal``. Keep the
    # explicit guard for a future implementation that reports absence.
    if target_cut is None:
        raise ValueError(f"goal {goal!r} has no occurrence at a selected site")

    keeps = [frozenset() for _ in range(steps)]
    keeps[target_step] = frozenset(target_cut.kept)
    needed = set(target_cut.needs) & set(proc.state)
    for step in range(target_step - 1, -1, -1):
        predecessor = cone(
            events, seed=needed, total=len(events), _bodies=bodies, _parents=parents,
            _branches=branches)
        keeps[step] = frozenset(predecessor.kept)
        needed = set(predecessor.needs) & set(proc.state)
    return OccurrenceRunSlice(
        proc, goal, sites, target_step, steps, keeps, _OCCURRENCE_RUN_TOKEN)


def for_occurrence_run(proc, goal, sites, target_step, steps, events=None, *,
                       _index=None):
    """Compatibility view of :func:`occurrence_run`'s per-step command sets."""

    return occurrence_run(
        proc, goal, sites, target_step, steps, events=events,
        _index=_index).keeps


def shared_occurrence_keeps(proc, goals, steps, fragments):
    """Validate equal sealed selections before encoding distinct hit predicates.

    This proves only that the selected fragment questions share a transition
    formula. The caller still owns each public goal's complete occurrence cover.
    No subset union or caller-supplied command set gains authority here.
    """
    if type(fragments) is not tuple or not fragments or len(fragments) != len(goals):
        raise TypeError("shared occurrence queries need one sealed fragment per goal")
    if len(set(goals)) != len(goals):
        raise ValueError("shared occurrence queries name distinct goals")
    keeps = None
    for goal, fragment in zip(goals, fragments):
        if type(fragment) is not OccurrenceRunSlice:
            raise TypeError("shared occurrence queries require sealed slices")
        fragment.checked_for(proc, goal, steps)
        if keeps is None:
            keeps = fragment.keeps
        elif fragment.keeps != keeps:
            raise ValueError("shared occurrence queries have different command selections")
    return keeps


def audit_occurrence_run(
        proc, goal, sites, target_step, steps, keeps, events=None, *,
        _index=None):
    """Why per-position occurrence cones cannot answer their prefix query."""

    if type(keeps) is OccurrenceRunSlice:
        try:
            keeps.checked_for(proc, goal, steps)
        except (TypeError, ValueError) as exc:
            return (str(exc),)
        if keeps.target_step != target_step or keeps.sites != tuple(sites):
            return ("an occurrence-run slice names another occurrence fragment",)
        return ()

    if _index is not None:
        indexed = _index.checked_for(proc)
        if events is not None and tuple(events) != indexed.events:
            raise ValueError("events and slice index describe different bodies")
        events = indexed.events
    else:
        events = linearize(proc.body) if events is None else events
    try:
        expected = for_occurrence_run(
            proc, goal, sites, target_step, steps, events=events,
            _index=_index)
    except (TypeError, ValueError) as exc:
        return (str(exc),)
    if type(keeps) not in (tuple, list) or len(keeps) != steps:
        return ("an occurrence run does not supply one cone per step",)
    for step, (candidate, required) in enumerate(zip(keeps, expected)):
        try:
            candidate = frozenset(candidate)
        except TypeError:
            return (f"the cone for step {step} is not a finite command set",)
        if any(type(identity) is not int for identity in candidate):
            return (f"the cone for step {step} contains a non-command identity",)
        missing = _binding_faults(proc, candidate, events)
        if missing:
            return missing
        omitted = _omitted_fault(
            events, required, candidate,
            f"by occurrence-run step {step}")
        if omitted:
            return omitted
    return ()


def _goal_union(events, goals, positions, *, bodies, parents, branches, reads_cache):
    """Union observations at their own positions, sharing one dependency walk."""
    from ..core.construction_checkpoint import finish_construction

    goals = frozenset(goals)
    sites = {position: 1 for goal in goals for position in positions.get(goal, ())}
    if not sites and None not in goals:
        return None
    # Do not seed all reads at the end: intervening overwrites would erase
    # dependencies belonging to earlier occurrences. Branch/loop/failure
    # transfer is the same implementation used by each independent cone.
    first = len(events) - 1 if None in goals else max(sites)
    kept, needs = finish_construction(_reverse_cones(events, first, sites, {},
        active=1, bodies=bodies, parents=parents, reads_cache=reads_cache,
        branches=branches))
    return Slice(None, set(kept), len(events), needs)


def for_run(proc, goals, steps=1, events=None, *, _index=None):
    """One slice covering ``goals`` over a run of ``steps`` steps, or ``None``.

    Three things make this more than the union of `cone` calls.

    *A goal is asked about the whole run*, not about one step: `Unrolling`'s hit
    is the disjunction over the steps, so a command the goal needs at step 3 is
    needed at every step, since every step is the same body.

    *An earlier step owes the later ones its state.* What the cone still needs at
    the step's entry (`Slice.needs`) is what the step before it has to compute, so
    the slice grows by re-slicing against those names until it stops growing.
    Two rounds settle it on almost everything, and the iteration is bounded by the
    number of names.

    *A share of goals shares one slice.* Each worker rebuilds the formula once for
    the goals it was given, so the unit that matters is a share and not a goal --
    which is what makes this pay at all, since the cones of two goals are almost
    never equal (measured: 2500 distinct cones over 2500 goals) but their union is
    still about half the body.

    Returns the ids to keep, so a caller can `prune` with it.
    """
    if _index is not None:
        indexed = _index.checked_for(proc)
        if events is not None and tuple(events) != indexed.events:
            raise ValueError("events and slice index describe different bodies")
        events, bodies, parents = indexed.events, indexed.loop_bodies, indexed.parents
        positions = indexed.goal_positions
    else:
        events = events if events is not None else linearize(proc.body)
        bodies = _loop_bodies(events)
        parents = _loop_parents(events) if bodies else {}
        positions = _goal_positions(events)
    branches = indexed.branches if _index is not None else _branch_bounds(events)
    reads_cache = indexed.command_reads if _index is not None else {}
    cut = _goal_union(events, goals, positions, bodies=bodies, parents=parents,
                      branches=branches, reads_cache=reads_cache)
    if cut is None:
        return None
    kept = cut.kept
    state = set(proc.state)
    needs = set(cut.needs) & state
    if steps > 1:
        # An earlier step can prevent every later one even when the selected
        # goal reads no state: a failure after the goal's last site in that
        # earlier step still ends the run.  Take one predecessor cone
        # unconditionally, then continue only while it discovers new state
        # dependencies.  Skipping the first round made a two-step slice admit
        # ``goal false; fail`` followed by a fictitious second step.
        earlier = cone(
            events, seed=needs, total=len(events), _bodies=bodies, _parents=parents,
            _branches=branches, _reads=reads_cache)
        kept |= earlier.kept
        discovered = (needs | earlier.needs) & state
        seen = set(needs)
        needs = discovered
        while needs - seen:
            seen |= needs
            earlier = cone(
                events, seed=needs, total=len(events), _bodies=bodies, _parents=parents,
                _branches=branches, _reads=reads_cache)
            kept |= earlier.kept
            needs = (needs | earlier.needs) & state
    return kept


def run_positions(proc, goals, steps, *, _index=None):
    """Exact all-occurrence goal cones, separately for each transaction.

    A step owes its own goal occurrences and the state needed by its successor.
    The final step owes no successor state. Unlike the run-wide fixed point,
    this does not copy future preparation into every position. Earlier failures
    remain relevant even when their values are dead. Equal live-state supports
    share their predecessor walk; work scales with distinct supports, not the
    number of goal/position pairs.
    """
    if type(steps) is not int or steps < 1:
        raise ValueError("a positional query has a positive step bound")
    indexed = index(proc) if _index is None else _index.checked_for(proc)
    options = dict(_bodies=indexed.loop_bodies, _parents=indexed.parents,
                   _goal_positions=indexed.goal_positions, _reads=indexed.command_reads,
                   _branches=indexed.branches)
    cut = _goal_union(indexed.events, goals, indexed.goal_positions,
        bodies=indexed.loop_bodies, parents=indexed.parents,
        branches=indexed.branches, reads_cache=indexed.command_reads)
    if cut is None:
        return None
    base = frozenset(cut.kept)
    state = frozenset(proc.state)
    required = cut.needs & state
    needed = required
    keeps = [base] * steps
    predecessors = {}
    for step in range(steps - 2, -1, -1):
        if needed not in predecessors:
            previous = cone(indexed.events, seed=needed, **options)
            predecessors[needed] = (base | previous.kept,
                                    required | (previous.needs & state))
        keeps[step], needed = predecessors[needed]
    if any(_binding_faults(proc, keep, indexed.events, indexed.command_reads)
           for keep in set(keeps)):
        return None  # Preserve the full step if a projection has an unbound read.
    return tuple(keeps)


def boundary_cone(proc, names, events=None):
    """The checked failure-erased cone of selected end-of-step values.

    Failures are deliberately omitted.  Removing an early halt can only add
    executions reaching a later write, so the resulting boundary relation is
    an over-approximation of normally completing source executions.  Proving a
    carrier closed over this relation therefore proves it for the original
    step.  A caller that cannot discharge the over-approximation must fall back
    to the complete step; it must not treat the broader counterexample as a
    source counterexample.

    This reuses the same backward dependency closure as multi-step goal
    slicing.  Unlike a goal cone, its cut is the end of the command and its
    seeds are representation variable names.
    """

    names = frozenset(names)
    if any(type(name) is not str or not name for name in names):
        raise TypeError("a boundary cone names nonempty representation variables")
    events = linearize(proc.body) if events is None else events
    cut = cone(
        events, seed=names, total=len(events),
        _retain_failures=False, _bodies=_loop_bodies(events))
    # Generated cones still pass the cheap structural audit.  This catches a
    # future dependency rule that retains a read without its binding and makes
    # the optimization fall back rather than blessing a malformed formula.
    if audit(proc, (), cut.kept, events=events):
        return None
    return cut


def for_boundary(proc, names, events=None):
    """Commands needed to compute selected end-of-step values."""

    cut = boundary_cone(proc, names, events=events)
    return None if cut is None else cut.kept


def _query_keep_with_loop_flows(proc, keep):
    """Close a query's dependencies while retaining its CFG control skeleton.

    The source graph owns joins and loop-entry facts. Keep the corresponding
    compiler flags, loop-target bindings and exits, not every computation in
    its blocks. Their dependencies use the same occurrence-aware slicer and
    cyclic closure as goal queries. Newly needed loops add their own controls.
    The projection below recomputes flow facts for the resulting bodies.
    """
    events = linearize(proc.body)
    bodies = _loop_bodies(events)
    parents = _loop_parents(events) if bodies else {}
    kept, expanded = set(keep), set()
    branches = _branch_bounds(events)
    frames = [(proc.body, proc.graph)] if proc.graph is not None else []
    while True:
        pending = [command for command, _under in events
                   if isinstance(command, ir.For) and command.flow is not None
                   and id(command) in kept and id(command) not in expanded]
        if not pending and not frames:
            return frozenset(kept)
        for loop in pending:
            expanded.add(id(loop))
            frames.append((loop.body, loop.flow.graph))
        for body, graph in frames:
            source = {id(command) for block in graph.blocks.values()
                      for command in block.instrs}
            stack = [body]
            while stack:
                command = stack.pop()
                if id(command) in source:
                    continue
                if isinstance(command, ir.Seq):
                    stack.extend((command.second, command.first))
                else:
                    kept.add(id(command))
                    if isinstance(command, ir.If):
                        stack.extend((command.orelse, command.then))
                    elif isinstance(command, ir.For):
                        stack.append(command.body)
        frames.clear()
        kept.update(cone(events, _roots=kept, _bodies=bodies,
                         _parents=parents, _branches=branches).kept)


def prune(cmd, keep):
    """``cmd`` with everything outside ``keep`` replaced by `skip`.

    Rebuilt rather than edited, since the tree is shared: `linearize` holds the
    same nodes and a caller may slice a body for several goals.

    A dropped `if` is one with nothing kept inside it, because `cone` keeps an
    enclosing `if` whenever it keeps anything within -- so dropping it drops both
    its branches and its guard together, and nothing that survives is left
    referring to a condition that is no longer computed.
    """
    return _prune(cmd, keep)[0]


def _observation_graph(graph, keep, rewritten, *, computation=False):
    """Project instructions without changing CFG edges or their predicates."""
    from . import cfg

    return cfg.CFG({
        label: cfg.Block(label, [
            rewritten.get(id(instruction), instruction)
            for instruction in block.instrs
            if id(instruction) in keep
            or not computation and not isinstance(instruction, ir.Mark)
        ], block.term)
        for label, block in graph.blocks.items()
    }, graph.entry)


def _query_projection(proc, keep, types):
    """Project audited computations, retaining and rebinding source controls."""
    graph = proc.graph
    keep = _query_keep_with_loop_flows(proc, keep)
    body, rewritten = _prune(proc.body, keep, _project_flow=True)
    projected = proc.derived(body=body, types=types, graph=None,
                             boundary_complete=False)
    if graph is not None:
        graph = _observation_graph(graph, keep, rewritten, computation=True)
        ir._bind_control_flow(projected, ir._make_control_flow_authority(graph, body))
    return projected


def _prune(cmd, keep, *, _project_flow=False):
    # Iterative, and it has to be, for the reason `ir.walk` and `ir.cost` give:
    # a step's sequence is one long left-leaning chain once its loops are
    # unrolled, and a recursive rebuild exhausts the interpreter's stack on it.
    # Found the ordinary way -- `conwaylife` and `arm_correlate_q15` raised
    # `RecursionError` here while the probe that measured this module did not,
    # because the probe had raised the limit and the tests had not.
    done = {}
    flow_cache = {}
    stack = [(cmd, False)]
    while stack:
        node, built = stack.pop()
        key = id(node)
        if key in done:
            continue
        if isinstance(node, ir.Seq):
            # A sequence is never in `keep`: it is not a command anything reports
            # or reads, only the order of the ones that are.
            parts = _parts(node)
            if not built:
                stack.append((node, True))
                for part in reversed(parts):
                    stack.append((part, False))
                continue
            kept = [done[id(p)] for p in parts]
            done[key] = ir._sequence([p for p in kept
                                      if not isinstance(p, ir.Skip)])
        elif key not in keep:
            done[key] = ir.Skip()
        elif isinstance(node, ir.If):
            if not built:
                stack.append((node, True))
                stack.append((node.orelse, False))
                stack.append((node.then, False))
                continue
            done[key] = ir.If(node.guard, done[id(node.then)],
                              done[id(node.orelse)])
        elif isinstance(node, ir.For):
            if node.flow is not None and all(
                    id(command) in keep or isinstance(command, (ir.Seq, ir.Skip))
                    for command in ir.walk(node.body)):
                # No body rewrite: retain its original sealed CFG/trip proof.
                done[key] = node
                continue
            if not built:
                stack.append((node, True))
                stack.append((node.body, False))
                continue
            body = done[id(node.body)]
            flow = None
            if node.flow is not None and (_project_flow or all(
                    id(command) in keep
                    or isinstance(command, (ir.Seq, ir.Skip, ir.Mark))
                    for command in ir.walk(node.body))):
                # Keep the control graph paired with its exact rebuilt body.
                # Observation-only pruning reuses the original flow facts;
                # a computational query projection derives its own facts.
                from . import cfg, ssa

                original = ssa.loop_flow_facts(node, _cache=flow_cache)
                graph = _observation_graph(original.graph, keep, done,
                                           computation=_project_flow)
                required, defined = original.required, original.defined
                if _project_flow:
                    analysis = ssa.analyze(graph, set(original.entry_defined))
                    required = analysis.undefined_names
                    exits = [set(analysis.out_of(label)) - set(original.entry_defined)
                             for label in graph.order()
                             if label in analysis.out_masks
                             and isinstance(graph.blocks[label].term, cfg.Return)]
                    defined = set.intersection(*exits) if exits else set()
                flow = ir._make_loop_flow(
                    original.minimum, original.maximum,
                    required, defined, original.entry_defined,
                    graph, cfg.graph_fingerprint(graph), cfg.if_convert(graph),
                    body, node.trip_count)
            done[key] = ir.For(node.index, node.trip_count, body, flow=flow)
        else:
            done[key] = node
    return done[id(cmd)], done


def sliced(proc, goals, steps=1, refused=None):
    """``proc`` with everything no goal in ``goals`` needs replaced by `skip`.

    The procedure is rebuilt around the pruned body and keeps everything else --
    the state, the inputs, the types, the output variables. A slice changes what
    the step computes, not what it is about, and a caller that asks a query
    against it is asking the same question of a smaller formula.

    Returns ``proc`` unchanged when nothing can be dropped, and **when the slice
    does not pass `audit`**. That second case is the one that matters: a slice this
    module cannot vouch for is not a slice to answer a query with, and the whole
    step is always available and always right. ``refused`` is called with the
    audit's reasons when that happens, so a caller can report a slice that was
    computed and not used rather than losing it silently.
    """
    events = linearize(proc.body)
    keep = for_run(proc, goals, steps, events=events)
    if keep is None:
        return proc
    why = audit(proc, goals, keep, events=events)
    if why:
        # Faster and wrong is worse than slow, and there is no third option to
        # weigh: the unsliced step answers the same question.
        if refused is not None:
            refused(why)
        return proc
    # Everything but the body, and `initial` is the one that would be silent:
    # without it a run starts from a free state, which asks whether *some* state
    # reaches the goal rather than whether a run does. That question has more
    # answers, so the slice would come back reachable where the step is not --
    # a defect no test of the body's shape would find.
    # Through the one clone the metadata survives (review 20: rebuilding via the
    # constructor and hand-copying dropped `source_state`, so the audit's frozen
    # contract silently reverted to `proc.state` after a slice). The graph is
    # dropped **by name**: it describes the body this was sliced from, and a
    # reader asking control-flow questions of it would get answers about
    # commands the sliced body no longer has.
    return proc.derived(body=prune(proc.body, keep), graph=None,
                        boundary_complete=False)
