"""Source-path prefixes for Search, not bounded reachability proofs.

Keep observed loop counts, but merge binding-closed loop-free CFG regions
instead of pinning every interior branch to the seed. At other branches expose
the alternate edge's straight-line prefix, with its original computations.
The encoder shares these regions across target queries without enumerating
source paths or expanding every alternative loop body. Multi-input preparation
retains shared loop-free suffixes, including unexecuted writes.
Target-directed control departures may also propose preparation inputs; only
native replay can turn these into public goal hits.
A persistent array writer can also keep its observed payload while selecting
another legal address. Data-state exploration must not require a branch flip.
Consumer reads can request an unvisited array address. In a joint window the
earlier source writes must establish it; no symbolic initial state is invented.
A miss concerns only this subset; unrestricted questions remain available.
"""

from dataclasses import dataclass
import time

from behave.isolation.preparation_control import check_preparation

from ...symbolic import cfg, ir, ssa, typecheck as types
from ...symbolic import interp
from ...symbolic import slicing
from ...symbolic.values import copy_value


@dataclass(frozen=True)
class PathProjection:
    procedure: object
    targets: frozenset[str]
    commands: int
    decisions: int
    normal_exit: str | None = None
    state: object = None
    preparations: tuple[tuple[str, str], ...] = ()
    frontiers: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class _ReadFrontier:
    array: str
    index: object
    size: int
    observed: int


class _ControlFrontiers:
    """Source target reachability, shared by all native window steps.

    Private marks encode preparation conditions only. They never join the
    public catalogue and a satisfying assignment is not a named goal hit.
    Bit masks avoid enumerating source paths or rescanning loop bodies at
    every concrete iteration.
    """

    def __init__(self, proc, graph, targets):
        self.goals = tuple(sorted(targets))
        bits = {goal: 1 << i for i, goal in enumerate(self.goals)}
        prefix = '_search_preparation:'
        source_goals = ir.goals_of(proc.body)
        while any(goal.startswith(prefix) for goal in source_goals):
            prefix += '_'
        self.markers = {goal: prefix + goal for goal in self.goals}
        self.commands, self.blocks = {}, {}
        roots = ([command for block in graph.blocks.values() for command in block.instrs]
                 if graph is not None else [proc.body])
        pending = [(command, False) for command in roots]
        while pending:
            check_preparation()
            command, ready = pending.pop()
            if id(command) in self.commands:
                continue
            children = ((command.first, command.second) if isinstance(command, ir.Seq) else
                        (command.then, command.orelse) if isinstance(command, ir.If) else
                        (command.body,) if isinstance(command, ir.For) else ())
            if children and not ready:
                pending.append((command, True))
                pending.extend((child, False) for child in children)
                continue
            mask = bits.get(command.goal, 0) if isinstance(command, (ir.Mark, ir.Fail)) else 0
            for child in children:
                mask |= self.commands[id(child)]
            self.commands[id(command)] = mask
        if graph is not None:
            for label in reversed(graph.order()):
                check_preparation()
                block = graph[label]
                mask = bits.get(block.term.goal, 0) if isinstance(block.term, cfg.Fail) else 0
                for command in block.instrs:
                    mask |= self.commands[id(command)]
                for successor in block.term.successors():
                    mask |= self.blocks[successor]
                self.blocks[label] = mask

    def selected(self, mask):
        while mask:
            bit = mask & -mask
            yield self.goals[bit.bit_length() - 1]
            mask ^= bit


def _loop_free_tails(graph):
    """Identify whole loop-free suffixes once, without enumerating paths."""
    eligible = set()
    if graph is not None:
        for label in reversed(graph.order()):
            check_preparation()
            block = graph[label]
            if (all(successor in eligible for successor in block.term.successors())
                    and not any(isinstance(item, ir.For)
                                for command in block.instrs for item in ir.walk(command))):
                eligible.add(label)
    return frozenset(eligible)


@dataclass(frozen=True)
class _SourceTail:
    graph: object
    entry: str
    labels: tuple[str, ...]
    stop: str | None = None


class _LoopFreeRegions:
    """Keep acyclic source alternatives together up to their next barrier.

    One bottom-up source scan, not a set of paths or a region per goal. A
    retained loop or a split leading to different barriers ends a region.
    Its alternatives can share symbolic data updates before reconverging.
    """

    def __init__(self, proc, graph):
        self.free = {}
        roots = ([command for block in graph.blocks.values() for command in block.instrs]
                 if graph is not None else [proc.body])
        pending = [(command, False) for command in roots]
        while pending:
            check_preparation()
            command, ready = pending.pop()
            if id(command) in self.free:
                continue
            children = ((command.first, command.second) if isinstance(command, ir.Seq) else
                        (command.then, command.orelse) if isinstance(command, ir.If) else
                        (command.body,) if isinstance(command, ir.For) else ())
            if children and not ready:
                pending.append((command, True))
                pending.extend((child, False) for child in children)
                continue
            self.free[id(command)] = (not isinstance(command, (ir.For, ir.Fail))
                                      and all(self.free[id(child)] for child in children))
        self.stops = {}
        if graph is not None:
            for label in reversed(graph.order()):
                check_preparation()
                block = graph[label]
                stops = {self.stops[child] for child in block.term.successors()}
                self.stops[label] = (next(iter(stops)) if len(stops) == 1
                    and all(self.free[id(command)] for command in block.instrs) else label)

    def region(self, graph, label):
        stop = self.stops[label]
        if stop == label:
            return None
        reached, labels, todo = set(), [], [label]
        while todo:
            check_preparation()
            at = todo.pop()
            if at == stop or at in reached:
                continue
            reached.add(at)
            labels.append(at)
            todo.extend(graph[at].term.successors())
        # No topological sorting or complete-graph scan per dynamic visit is
        # needed: CFG computes order when the retained region is assembled.
        return _SourceTail(graph, label, tuple(labels), stop)


def _bindings_available(commands, available, guards=()):
    """Do not import an arm whose locals came from an omitted earlier path."""
    bound, read = set(available), set()
    for root in commands:
        introduced = set()
        for command in ir.walk(root):
            check_preparation()
            slicing._command_reads(command, read)
            if isinstance(command, ir.Assign):
                introduced.add(command.var)
        # A retained structured alternative must not export a new local on
        # just one arm. Later native-path commands may otherwise read it on
        # the other arm; the concrete seed's rho cannot justify that binding.
        if (introduced - bound and isinstance(root, (ir.If, ir.Seq, ir.For))
                and not introduced <= ir._definite(root, bound)):
            return False
        bound.update(introduced)
    for guard in guards:
        slicing.reads(guard, read)
    return read <= bound


def _straight_prefix(commands, targets, available):
    """Keep original bindings before a target; never cross another decision."""
    pending, out, last = list(reversed(tuple(commands))), [], 0
    available = set(available)
    while pending:
        check_preparation()
        command = pending.pop()
        if isinstance(command, ir.Seq):
            pending.extend((command.second, command.first))
        elif isinstance(command, ir.Skip):
            continue
        elif isinstance(command, ir.Mark):
            if command.goal in targets:
                out.append(ir.Mark(command.goal))
                last = len(out)
        elif isinstance(command, ir.Assign):
            reads = set()
            slicing._command_reads(command, reads)
            if not reads <= available:
                break
            out.append(ir.Assign(command.var, command.expr))
            available.add(command.var)
        elif isinstance(command, ir.Store):
            reads = set()
            slicing._command_reads(command, reads)
            if not reads <= available:
                break
            out.append(ir.Store(command.var, command.index, command.expr))
        elif isinstance(command, ir.Fail):
            if command.goal in targets:
                out.append(ir.Fail(command.code, command.goal))
                last = len(out)
            break
        else:
            break
    return out[:last]


def _edge_commands(graph, label):
    while True:
        check_preparation()
        block = graph[label]
        yield from block.instrs
        term = block.term
        if isinstance(term, cfg.Goto):
            label = term.target
        else:
            if isinstance(term, cfg.Fail):
                yield ir.Fail(term.code, term.goal)
            return


def _observations(node, targets):
    """Recognize a state-neutral arm; a failure is not an observation-only arm."""
    pending, out = [node], []
    while pending:
        check_preparation()
        command = pending.pop()
        if isinstance(command, ir.Seq):
            pending.extend((command.second, command.first))
        elif isinstance(command, ir.Mark):
            if command.goal in targets:
                out.append(ir.Mark(command.goal))
        elif not isinstance(command, ir.Skip):
            return None
    return out


def _observation_edge(graph, label, targets):
    out = []
    while True:
        check_preparation()
        block = graph[label]
        if not isinstance(block.term, cfg.Goto) or any(
                not isinstance(command, (ir.Mark, ir.Skip)) for command in block.instrs):
            return label, out
        out.extend(ir.Mark(command.goal) for command in block.instrs
                   if isinstance(command, ir.Mark) and command.goal in targets)
        label = block.term.target


def project(proc, inputs, state, targets, *, validation, checkpoint=check_preparation):
    work = _project(proc, inputs, state, targets, validation=validation)
    while True:
        checkpoint()
        try:
            next(work)
        except StopIteration as done:
            return done.value


def _project(proc, inputs, state, targets, *, validation, complete=False, tail_labels=None,
             controls=None, proposal=False, regions=None, context=False, write_locations=None):
    """Restrict an admitted procedure to one native path and its edge goals.

    Loop counts are captured before executing their bodies. Entered iterations
    require that count to exceed their index; continuing after the loop requires
    its observed count. These restrictions are query premises, not source facts.
    A complete prefix retains its final state and identifies its normal exit.
    A loop-free suffix retains all original branches, so preparation may take
    a state-writing arm absent from the seed. Its normal returns can continue;
    failures and departures from the earlier restricted path cannot. Neither
    form is a complete source transition certificate. ``proposal`` instead
    drops preceding path assumptions; even its positive answers must remain
    uncredited input proposals until the original program has replayed them.
    """
    from ...symbolic.validation import _checked_procedure_validation

    if context and (proposal or complete or controls is None):
        raise ValueError('a context frontier needs a strict observed path and its source controls')
    if proposal and complete:
        raise ValueError('a speculative path cannot supply a completed transition')
    _checked_procedure_validation(validation, proc)
    values = proc.semantics.validate_inputs(inputs)
    machine = interp._Interp({**{k: copy_value(v) for k, v in state.items()},
                              **{k: copy_value(v) for k, v in values.items()}})
    targets = frozenset(targets)
    events, found, extra, preparations = [], set(), [], {}
    context_frontiers, extension_frontiers, read_frontiers, read_addresses = [], [], [], {}
    names = set(proc.state) | set(proc.inputs) | set(proc.locals) | set(validation.types)
    last_observation, failed = 0, False

    def context_marks(observations, towards=0):
        immediate = {item.goal for item in observations
                     if isinstance(item, (ir.Mark, ir.Fail)) and item.goal in targets}
        goals = tuple(sorted(immediate | set(controls.selected(towards))))
        markers = []
        if goals:
            # These targets share one preparation predicate, not independent
            # source events. Retain every association without duplicating the
            # same executable Mark once per public goal at every occurrence.
            marker = f'{controls.markers[goals[0]]}:context:{len(events)}'
            preparations[marker] = goals
            markers.append(ir.Mark(marker))
        if markers:
            context_frontiers.append(tuple(mark.goal for mark in markers))
        return markers

    def frontier(guard, truth, observations, towards=0, *, extension=False):
        nonlocal last_observation
        observe_reads(guard)
        if context:
            markers = context_marks(observations, towards)
            if extension and markers:
                extension_frontiers.append(tuple(marker.goal for marker in markers))
            observations = [*markers, *observations]
        elif controls is not None and towards:
            immediate = {item.goal for item in observations if isinstance(item, (ir.Mark, ir.Fail))}
            goals = set(controls.selected(towards)) - immediate
            preparations.update((controls.markers[goal], (goal,)) for goal in goals)
            observations = [*(ir.Mark(controls.markers[goal]) for goal in sorted(goals)),
                            *observations]
        events.append((guard, truth, observations))
        if observations:
            found.update(item.goal for item in observations
                         if isinstance(item, (ir.Mark, ir.Fail)) and item.goal in targets)
            last_observation = len(events)

    def observations(guard, then, orelse):
        nonlocal last_observation
        if context:
            truth = machine.boolean(guard)
            if truth:
                orelse = [*context_marks(orelse), *orelse]
            else:
                then = [*context_marks(then), *then]
            # An observation does not control source execution. Pinning it
            # before the real branch can prohibit exactly the flip being
            # queried (e.g. a short-circuit condition's separate event).
        if then or orelse:
            events.append(ir.If(guard, ir._sequence(then), ir._sequence(orelse)))
            found.update(item.goal for item in (*then, *orelse) if item.goal in targets)
            last_observation = len(events)

    def write_address(var, index_expr, value_expr):
        nonlocal last_observation
        if not context or var not in state or isinstance(index_expr, ir.Lit):
            return
        index, value = machine.integer(index_expr), machine.value(value_expr)
        cells = machine.rho[var]
        if type(value) not in (int, bool) or not 0 <= index < len(cells):
            return
        visited = {index, *((write_locations or {}).get(var, ()))}
        if sum(0 <= i < len(cells) for i in visited) == len(cells):
            return
        # This is a private input proposal, not a new catalogue goal. Preserve
        # the known writer's payload and all preceding source guards. Require
        # an in-range different destination whose value would actually change.
        # Do not constrain continuation to the old address: other control
        # frontiers must retain their original symbolic input domain.
        conditions = (
            *(ir.Op('ne', index_expr, ir.Lit(i)) for i in sorted(visited)),
            ir.Op('ge', index_expr, ir.Lit(0)),
            ir.Op('lt', index_expr, ir.Lit(len(cells))),
            ir.Op('eq', value_expr, ir.Lit(value)),
            ir.Op('ne', ir.Sel(ir.Var(var), index_expr), ir.Lit(value)),
        )
        guard = conditions[0]
        for condition in conditions[1:]:
            guard = ir.Op('land', guard, condition)
        markers = context_marks((), (1 << len(controls.goals)) - 1)
        if markers:
            events.append(ir.If(guard, ir._sequence(markers), ir.Skip()))
            last_observation = len(events)

    def observe_reads(expression):
        """Private address departures, resolved after observing the full path.

        The prefix still contains the original source operations and guards.
        Unlike moving a fixed writer payload, a joint query may synthesize
        the earlier value that makes a consumer access another cell. These
        marks are proposals only, never coverage or reachability evidence.
        """
        nonlocal last_observation
        if not context or not controls.goals:
            return
        pending = [expression]
        while pending:
            check_preparation()
            term = pending.pop()
            if isinstance(term, ir.Sel):
                if isinstance(term.array, ir.Var) and term.array.name in state:
                    name = term.array.name
                    cells = machine.rho[name]
                    index = machine.integer(term.index)
                    if 0 <= index < len(cells):
                        read_addresses.setdefault(name, set()).add(index)
                        if not isinstance(term.index, ir.Lit):
                            events.append(_ReadFrontier(name, term.index, len(cells), index))
                            last_observation = len(events)
                pending.extend((term.array, term.index))
            elif isinstance(term, ir.Op):
                # Follow actual evaluation, including lazy conditionals; an
                # unselected arm may contain an undefined source-local name.
                if term.op == 'ite':
                    pending.extend((term.args[0], term.args[1 if machine.boolean(term.args[0]) else 2]))
                else:
                    pending.extend(reversed(term.args))

    source_graph = validation.graph
    source_regions, source_loops = {}, set()
    graph_controls = {id(source_graph): controls}
    if not proposal and not context and regions is None:
        regions = _LoopFreeRegions(proc, source_graph)
    if complete and tail_labels is None:
        tail_labels = _loop_free_tails(source_graph)
    unmerged = set()
    pending = [('graph', source_graph, source_graph.entry)] if source_graph is not None else [('cmd', proc.body)]
    while pending:
        yield
        check_preparation()
        item = pending.pop()
        if item[0] == 'graph':
            _, graph, label = item
            region = (None if proposal or context or graph is not source_graph or label in unmerged
                      else regions.region(graph, label))
            if region is not None and not _bindings_available(
                    (command for at in region.labels for command in graph[at].instrs),
                    machine.rho, (graph[at].term.guard for at in region.labels
                                  if isinstance(graph[at].term, cfg.Branch))):
                # A rejected region follows the original native path. Do not
                # rescan its successively smaller suffixes at every block;
                # that would make one missing binding quadratic in source size.
                unmerged.update(region.labels)
                region = None
            if region is not None:
                events.append(region)
                for at in region.labels:
                    for command in graph[at].instrs:
                        found.update(part.goal for part in ir.walk(command)
                                     if isinstance(part, ir.Mark) and part.goal in targets)
                    yield
                # Later path restrictions see the original concrete state;
                # the query retains BOTH arms and their guarded updates.
                while label != region.stop:
                    block_ = graph[label]
                    for command in block_.instrs:
                        machine.run(command, set())
                        yield
                    term = block_.term
                    label = (term.target if isinstance(term, cfg.Goto) else
                             term.then if machine.boolean(term.guard) else term.orelse)
                last_observation = len(events)
                pending.append(('graph', graph, label))
                continue
            if (complete and graph is source_graph and label in tail_labels
                    and label not in unmerged):
                # One shared suffix graph, not one copied continuation per
                # branch. Earlier loop/path restrictions remain unchanged.
                reached, tail_targets, todo = set(), set(), [label]
                while todo:
                    check_preparation()
                    at = todo.pop()
                    if at in reached:
                        continue
                    reached.add(at)
                    todo.extend(graph[at].term.successors())
                    for command in graph[at].instrs:
                        tail_targets.update(part.goal for part in ir.walk(command)
                                            if isinstance(part, (ir.Mark, ir.Fail)) and part.goal in targets)
                    term = graph[at].term
                    if isinstance(term, cfg.Fail) and term.goal in targets:
                        tail_targets.add(term.goal)
                    yield
                if not _bindings_available(
                        (command for at in reached for command in graph[at].instrs),
                        machine.rho, (graph[at].term.guard for at in reached
                                      if isinstance(graph[at].term, cfg.Branch))):
                    unmerged.update(reached)
                    pending.append(('graph', graph, label))
                    continue
                found.update(tail_targets)
                events.append(_SourceTail(graph, label,
                    tuple(at for at in graph.order() if at in reached)))
                # Keep the observed post-state for selecting subsequent native
                # paths. Symbolic execution still uses every retained arm.
                while True:
                    block_ = graph[label]
                    try:
                        for command in block_.instrs:
                            machine.run(command, set())
                            yield
                    except interp.Halt:
                        failed = True
                        break
                    term = block_.term
                    if isinstance(term, cfg.Goto):
                        label = term.target
                    elif isinstance(term, cfg.Branch):
                        label = term.then if machine.boolean(term.guard) else term.orelse
                    elif isinstance(term, cfg.Return):
                        break
                    elif isinstance(term, cfg.Fail):
                        failed = True
                        break
                    else:
                        raise ValueError('a concrete path entered a pruned source block')
                    check_preparation()
                    yield
                break
            block = graph[label]
            pending.append(('term', graph, block.term))
            pending.extend(('cmd', command) for command in reversed(block.instrs))
            continue
        if item[0] == 'term':
            _, graph, term = item
            if isinstance(term, cfg.Goto):
                pending.append(('graph', graph, term.target))
            elif isinstance(term, cfg.Branch):
                then, left = _observation_edge(graph, term.then, targets)
                orelse, right = _observation_edge(graph, term.orelse, targets)
                if then == orelse:
                    # Both arms change only observations and rejoin. Keeping
                    # the concrete truth here would artificially pin later
                    # computation (notably a loop's separate entry marker).
                    observations(term.guard, left, right)
                    pending.append(('graph', graph, then))
                    continue
                truth = machine.boolean(term.guard)
                alternate = term.orelse if truth else term.then
                taken = term.then if truth else term.orelse
                local_controls = graph_controls[id(graph)]
                frontier(term.guard, truth,
                         _straight_prefix(_edge_commands(graph, alternate), targets, machine.rho),
                         0 if local_controls is None else
                         local_controls.blocks[alternate] & ~local_controls.blocks[taken])
                pending.append(('graph', graph, taken))
            elif isinstance(term, cfg.Fail):
                events.append(ir.Fail(term.code, term.goal))
                failed = True
                break
            elif isinstance(term, cfg.Pruned):
                raise ValueError('a concrete path entered a pruned source block')
            elif not isinstance(term, cfg.Return):
                raise TypeError(f'unsupported source path terminator: {type(term).__name__}')
            continue
        if item[0] == 'loop':
            _, loop, count, trips, index = item
            if index == count:
                if controls is not None and count:
                    frontier(ir.Op('gt', ir.Var(trips), ir.Lit(count)), False, [],
                             controls.commands[id(loop.body)], extension=True)
                events.append((ir.Op('le' if count == 0 else 'eq', ir.Var(trips), ir.Lit(count)), True))
                continue
            events.append((ir.Op('gt', ir.Var(trips), ir.Lit(index)), True))
            assignment = ir.Assign(loop.index, ir.Lit(index))
            events.append(assignment)
            machine.cmd(assignment, set())
            pending.append(('loop', loop, count, trips, index + 1))
            pending.append(('cmd', loop.body))
            continue
        command = item[1]
        if id(command) in source_regions:
            prefix, graph = source_regions[id(command)]
            pending.append(('graph', graph, graph.entry))
            pending.extend(('cmd', part) for part in reversed(prefix))
        elif isinstance(command, ir.Seq):
            pending.extend((('cmd', command.second), ('cmd', command.first)))
        elif isinstance(command, ir.If):
            if (complete and controls is None and regions is not None
                    and regions.free.get(id(command), False)
                    and _bindings_available((command,), machine.rho)):
                # A state-only prefix owes both loop-free update alternatives,
                # not the old truth of its compiler flags. Retain the actual
                # conditional computation; no speculative state is introduced.
                # An unentered earlier loop may own an arm's local bindings.
                # In that case follow the native control below, rather than
                # importing a dangling read or inventing an initial value.
                events.append(command)
                machine.run(command, set())
                continue
            left = _observations(command.then, targets)
            right = _observations(command.orelse, targets)
            if left is not None and right is not None:
                observations(command.guard, left, right)
                continue
            truth = machine.boolean(command.guard)
            other = command.orelse if truth else command.then
            taken = command.then if truth else command.orelse
            frontier(command.guard, truth, _straight_prefix((other,), targets, machine.rho),
                     0 if controls is None else controls.commands[id(other)] & ~controls.commands[id(taken)])
            pending.append(('cmd', command.then if truth else command.orelse))
        elif isinstance(command, ir.For):
            if id(command) not in source_loops:
                source_loops.add(id(command))
                region = ssa.loop_source_region(command, checkpoint=check_preparation)
                if region is not None:
                    body, prefix, graph = region
                    source_regions[id(body)] = prefix, graph
                    local_controls = (None if controls is None else
                                      _ControlFrontiers(proc, graph, controls.goals))
                    graph_controls[id(graph)] = local_controls
                    if local_controls is not None:
                        controls.commands.update(local_controls.commands)
            count = max(machine.integer(command.trip_count), 0)
            name = f'_search_path_trips_{len(extra)}'
            while name in names:
                name += '_'
            names.add(name)
            extra.append(name)
            capture = ir.Assign(name, command.trip_count)
            observe_reads(command.trip_count)
            events.append(capture)
            machine.cmd(capture, set())
            if not count:
                entered = _straight_prefix(
                    (ir.Assign(command.index, ir.Lit(0)), command.body), targets, machine.rho)
                frontier(ir.Op('gt', ir.Var(name), ir.Lit(0)), False, entered,
                         0 if controls is None else controls.commands[id(command.body)],
                         extension=True)
            pending.append(('loop', command, count, name, 0))
        elif isinstance(command, ir.Assign):
            expression = command.expr
            observe_reads(expression)
            if (isinstance(expression, ir.Op) and expression.op == 'store'
                    and isinstance(expression.args[0], ir.Var)
                    and expression.args[0].name == command.var):
                write_address(command.var, expression.args[1], expression.args[2])
            events.append(ir.Assign(command.var, command.expr))
            machine.cmd(command, set())
        elif isinstance(command, ir.Store):
            observe_reads(command.index)
            observe_reads(command.expr)
            write_address(command.var, command.index, command.expr)
            events.append(ir.Store(command.var, command.index, command.expr))
            machine.cmd(command, set())
        elif isinstance(command, ir.Fail):
            events.append(ir.Fail(command.code, command.goal))
            failed = True
            break
        elif not isinstance(command, (ir.Skip, ir.Mark)):
            raise TypeError(f'unsupported source path command: {type(command).__name__}')

    frontiers = ()
    if context:
        # A control-flow prefix alone can silently replace every previously
        # visited address. Prefer departing from the actual memory-access
        # prefix, while retaining the wider questions below as alternatives.
        # The flag only guards private proposal marks, never source execution.
        read_prefix = '_search_read_prefix'
        while read_prefix in names:
            read_prefix += '_'
        has_reads = any(isinstance(event, _ReadFrontier) for event in events)
        if has_reads:
            events.insert(0, ir.Assign(read_prefix, ir.Lit(True)))
            last_observation += 1
        preserved_frontiers, previous_read = [], False
        read_guards, read_domains = {}, {}
        for position, event in enumerate(events):
            if not isinstance(event, _ReadFrontier):
                continue
            domain = event.array, event.size
            if domain not in read_domains:
                read_domains[domain] = tuple(sorted(index for index in read_addresses[event.array]
                                                   if 0 <= index < event.size))
            observed = read_domains[domain]
            update = ir.Assign(read_prefix, ir.Op('land', ir.Var(read_prefix),
                ir.Op('eq', event.index, ir.Lit(event.observed))))
            if len(observed) == event.size:
                events[position] = update
                previous_read = True
                continue
            key = event.array, event.index, event.size
            if key not in read_guards:
                conditions = (ir.Op('ge', event.index, ir.Lit(0)),
                              ir.Op('lt', event.index, ir.Lit(event.size)),
                              *(ir.Op('ne', event.index, ir.Lit(i)) for i in observed))
                guard = conditions[0]
                for condition in conditions[1:]:
                    guard = ir.Op('land', guard, condition)
                read_guards[key] = guard
            # Do not expand per-goal marks for arrays already fully scanned.
            # Repeated source index expressions share their membership guard.
            markers = (ir.Mark(f'{controls.markers[controls.goals[0]]}:read:{position}'),)
            preparations[markers[0].goal] = controls.goals
            read_frontiers.append(tuple(marker.goal for marker in markers))
            observations = []
            if previous_read:
                preferred = ir.Mark(markers[0].goal + ':prefix')
                preparations[preferred.goal] = controls.goals
                preserved_frontiers.append((preferred.goal,))
                observations.append(ir.If(ir.Op('land', ir.Var(read_prefix), read_guards[key]),
                                          preferred, ir.Skip()))
            else:
                preserved_frontiers.append((markers[0].goal,))
            events[position] = ir._sequence((*observations,
                ir.If(read_guards[key], ir._sequence(markers), ir.Skip()), update))
            previous_read = True
        # A later occurrence does not subsume an earlier one. After a lowered
        # break/while exit, later loop guards can be pinned inactive by the
        # observed prefix. Retain the earlier live departure that can extend
        # it; query later occurrences first, not exclusively.
        # A new address may only replace the last address of an equally short
        # run. First try to extend a reached loop past its observed trip count;
        # these predicates keep the same source prefix and captured trip count.
        # Native replay still decides whether this produces useful new state.
        frontiers = tuple(dict.fromkeys((*reversed(extension_frontiers),
                                         *reversed(preserved_frontiers),
                                         *reversed(read_frontiers),
                                         *reversed(context_frontiers))))
    if not found and not preparations and not complete:
        return None
    preparation_rows = tuple(sorted((marker, goal)
        for marker, goals in preparations.items() for goal in goals))
    if complete:
        last_observation = len(events)
    if proposal:
        # An optimistic candidate, NOT an execution-equivalent path. Preserve
        # observed data operations and each alternate-edge predicate, but omit
        # preceding path assumptions. Their conjunction can be expensive even
        # when a target depends on a small, independent subset of input bits.
        # A candidate may take a different path, so only native replay can say
        # whether it hits anything. This body cannot certify state or outputs.
        commands = []
        for event in events[:last_observation]:
            yield
            if isinstance(event, tuple):
                guard, truth, *observed = event
                if observed and observed[0]:
                    commands.append(ir.If(ir.Op('not', guard) if truth else guard,
                                          ir._sequence(observed[0]), ir.Skip()))
            else:
                commands.append(event)
        graph = cfg.of_command(ir._sequence(commands))
        body = cfg.if_convert(graph)
        private = {cmd.var for cmd in ir.walk(body) if isinstance(cmd, ir.Assign)} - names
        projected = proc.derived(body=body, graph=None, boundary_complete=False,
            locals=[*proc.locals, *extra, *sorted(private)],
            types={**validation.types, **dict.fromkeys(extra, types.INTEGER),
                   **dict.fromkeys(private, types.BOOLEAN)})
        ir._bind_control_flow(projected, ir._make_control_flow_authority(graph, body))
        return PathProjection(projected, frozenset(found), len(commands),
            sum(isinstance(command, ir.If) for command in commands),
            preparations=preparation_rows)
    # A graph keeps the path flat. Nesting one If per decision would make the
    # representation's depth grow with path length despite its linear work.
    blocks, straight, decisions = {}, [], 0

    def block(instructions=(), term=None):
        if instructions and isinstance(instructions[-1], ir.Fail):
            failure = instructions[-1]
            instructions, term = instructions[:-1], cfg.Fail(failure.code, failure.goal)
        label = f'search_path_{len(blocks)}'
        while any(f'#{prefix}:{label}' in names for prefix in ('reached', 'taken')):
            label += '_'
        blocks[label] = cfg.Block(label, instructions, term)
        return label

    end = tail = block()
    normal_exit = None
    if complete and not failed:
        # A path departure must not masquerade as transaction completion.
        normal_exit = tail = block()
    command_count = last_observation
    for event in reversed(events[:last_observation]):
        yield
        check_preparation()
        if isinstance(event, _SourceTail):
            if straight:
                tail = block(list(reversed(straight)), cfg.Goto(tail))
                straight = []
            command_count -= 1  # The retained graph is not one source event.
            labels = {label: block() for label in event.labels}
            if event.stop is not None:
                labels[event.stop] = tail
            for label in event.labels:
                check_preparation()
                original = event.graph[label]
                term = original.term
                command_count += len(original.instrs) + isinstance(term, (cfg.Branch, cfg.Fail))
                if isinstance(term, cfg.Goto):
                    term = cfg.Goto(labels[term.target])
                elif isinstance(term, cfg.Branch):
                    term = cfg.Branch(term.guard, labels[term.then], labels[term.orelse])
                    decisions += 1
                elif isinstance(term, cfg.Return):
                    term = cfg.Goto(tail)
                blocks[labels[label]] = cfg.Block(labels[label],
                    [ir.copy_command(command) for command in original.instrs], term)
                yield
            tail = labels[event.entry]
        elif isinstance(event, tuple):
            if straight:
                tail = block(list(reversed(straight)), cfg.Goto(tail))
                straight = []
            guard, truth, *observations = event
            other = block(observations[0]) if observations and observations[0] else end
            tail = block(term=cfg.Branch(guard, tail if truth else other,
                                         other if truth else tail))
            decisions += 1
        else:
            straight.append(event)
    if straight:
        tail = block(list(reversed(straight)), cfg.Goto(tail))
    # Retained alternatives are structured commands, not opaque CFG
    # instructions. Expose their definitions and joins to the same planner
    # that consumes the rest of this path; do not repair missing ranges later.
    for label in tuple(blocks):
        original = blocks[label]
        if not any(isinstance(command, (ir.If, ir.Seq)) for command in original.instrs):
            continue
        nested = cfg.of_command(ir._sequence(original.instrs))
        labels = {at: label if at == nested.entry else block() for at in nested.order()}
        for at in nested.order():
            check_preparation()
            inner = nested[at]
            term = inner.term
            if isinstance(term, cfg.Goto):
                term = cfg.Goto(labels[term.target])
            elif isinstance(term, cfg.Branch):
                term = cfg.Branch(term.guard, labels[term.then], labels[term.orelse])
            elif isinstance(term, cfg.Return):
                term = original.term
            blocks[labels[at]] = cfg.Block(labels[at], inner.instrs, term)
    graph = cfg.CFG(blocks, tail)
    body = cfg.if_convert(graph)
    private = {cmd.var for cmd in ir.walk(body) if isinstance(cmd, ir.Assign)} - names
    projected = proc.derived(body=body, graph=None, boundary_complete=False,
        locals=[*proc.locals, *extra, *sorted(private)],
        types={**validation.types, **dict.fromkeys(extra, types.INTEGER),
               **dict.fromkeys(private, types.BOOLEAN)})
    ir._bind_control_flow(projected, ir._make_control_flow_authority(graph, body))
    return PathProjection(projected, frozenset(found), command_count, decisions,
                          normal_exit, {name: copy_value(machine.rho[name]) for name in proc.state},
                          preparation_rows, frontiers)


class PathQuery:
    """A strict path query or replay-only proposal, with separate result scopes."""

    def __init__(self, instance, occurrence, goals, *, validation, fixed_inputs,
                 input_constraints, proposal=False, context=False, write_locations=None):
        self.instance, self.occurrence = instance, occurrence
        self.proposal, self.context = proposal, context
        self.env = instance.env
        self.goals = frozenset(goals)
        self.fixed_inputs, self.input_constraints = fixed_inputs, input_constraints
        def build():
            controls = _ControlFrontiers(instance.proc, validation.graph, goals)
            return (yield from _project(instance.proc, occurrence['input'], occurrence['state'],
                                       goals, validation=validation, controls=controls,
                                       proposal=proposal, context=context, write_locations=write_locations))
        self.work = build()
        self.projection = self.preparation = None
        self.constructions, self.proved = {}, set()

    def probe(self, goals, timeout_ms):
        from ..local_ir import _LocalQueryPreparation
        from ..probing import Outcome, ProbeResult, Scope

        goals = frozenset(goals)
        if not goals <= self.goals:
            raise ValueError('a retained path query may only lose targets')
        started = time.monotonic()
        deadline = started + timeout_ms / 1000

        def result(outcome, **kwargs):
            # No negative result of a relaxed model classifies the original
            # path. Keep even a completed miss distinct from PATH_PREFIX UNSAT.
            if (self.proposal or self.context) and outcome is Outcome.LOCAL_UNSAT:
                outcome = Outcome.UNKNOWN
                kwargs['detail'] = 'speculative path produced no candidate; no reachability verdict'
            return ProbeResult(outcome, Scope.STATE_PREPARATION if self.proposal or self.context else Scope.PATH_PREFIX,
                               solve_ms=(time.monotonic() - started) * 1000, **kwargs)

        while self.work is not None:
            check_preparation()
            if time.monotonic() >= deadline:
                return result(Outcome.CONSTRUCTING, detail='native path construction paused')
            try:
                next(self.work)
            except StopIteration as done:
                self.projection, self.work = done.value, None
        selected = goals & self.projection.targets if self.projection is not None else frozenset()
        if self.context:
            selected = frozenset()  # Public hits do not answer a new state-context question.
        intermediate = (frozenset(marker for marker, goal in self.projection.preparations if goal in goals)
                        if self.projection is not None else frozenset())
        if not selected and not intermediate:
            return result(Outcome.LOCAL_UNSAT,
                          detail='no requested goal on this path frontier; other paths remain')
        if self.preparation is None:
            self.preparation = _LocalQueryPreparation(self.projection.procedure, self.env)
        # A disjunction over all departures lets an easy early branch erase
        # the useful prefix. Context exploration asks the latest feasible
        # departure first; a local miss advances to the preceding frontier.
        # All questions share the same projection and source preparation.
        candidates = (tuple(frozenset(row) & intermediate for row in self.projection.frontiers)
                      if self.context and self.projection is not None else (intermediate,))
        for targets, preparation in ((selected, False), *((row, True) for row in candidates)):
            if not targets:
                continue
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining < 1:
                return result(Outcome.CONSTRUCTING, detail='native path query construction paused')
            answer = self.preparation.probe(self.occurrence['state'], tuple(sorted(targets)),
                timeout_ms=remaining, fixed_inputs=self.fixed_inputs,
                input_constraints=self.input_constraints,
                _constructions=self.constructions, _proved_unsat=self.proved)
            if answer.outcome is Outcome.LOCAL_UNSAT:
                continue
            if self.proposal and answer.outcome is Outcome.SAT:
                return result(Outcome.PROPOSED, candidate=answer.candidate,
                    detail='relaxed path predicate; native replay required; no goal verdict')
            if preparation:
                if answer.outcome is Outcome.SAT:
                    return ProbeResult(Outcome.PROPOSED, Scope.STATE_PREPARATION,
                        candidate=answer.candidate,
                        detail='target-directed control frontier; native replay required; no goal verdict',
                        solve_ms=(time.monotonic() - started) * 1000)
                return result(answer.outcome, detail='control-frontier preparation: ' + answer.detail)
            return result(answer.outcome, goal=answer.goal, candidate=answer.candidate,
                          detail=answer.detail)
        return result(Outcome.LOCAL_UNSAT, detail='no hit or preparation on this path frontier; other paths remain')


def _renamed_graph(graph, rename, prefix, continuation=None, normal_exit=None):
    """Alpha-rename one flat path; only its real normal exit may continue."""
    expressions = {}

    def expression(root):
        pending = [(root, False)]
        while pending:
            check_preparation()
            term, ready = pending.pop()
            if term in expressions:
                continue
            children = (term.args if isinstance(term, ir.Op) else
                        (term.array, term.index) if isinstance(term, ir.Sel) else ())
            if not ready and children:
                pending.append((term, True))
                pending.extend((child, False) for child in reversed(children))
                continue
            expressions[term] = (ir.Var(rename(term.name)) if isinstance(term, ir.Var) else
                term if isinstance(term, ir.Lit) else
                ir.Op(term.op, *(expressions[x] for x in term.args)) if isinstance(term, ir.Op) else
                ir.Sel(expressions[term.array], expressions[term.index]))
        return expressions[root]

    def command(item):
        if isinstance(item, ir.Assign):
            return ir.Assign(rename(item.var), expression(item.expr))
        if isinstance(item, ir.Store):
            return ir.Store(rename(item.var), expression(item.index), expression(item.expr))
        if isinstance(item, ir.If):
            return ir.If(expression(item.guard), command(item.then), command(item.orelse))
        if isinstance(item, ir.Seq):
            return ir._sequence([command(part) for part in ir._parts(item)])
        if isinstance(item, (ir.Mark, ir.Fail, ir.Skip)):
            return ir.copy_command(item)
        raise TypeError('a projected path contains only flat native commands')

    blocks = {}
    for label in graph.order():
        check_preparation()
        original = graph[label]
        term = original.term
        if label == normal_exit and continuation is not None:
            term = cfg.Goto(continuation)
        elif isinstance(term, cfg.Goto):
            term = cfg.Goto(prefix + term.target)
        elif isinstance(term, cfg.Branch):
            term = cfg.Branch(expression(term.guard), prefix + term.then, prefix + term.orelse)
        blocks[prefix + label] = cfg.Block(prefix + label,
            [command(item) for item in original.instrs], term)
    return blocks


def _state_preparation(proc, validation):
    """Search-only state transfer, not a completed-execution certificate.

    Earlier invocations owe a later trigger their persistent state, not their
    old output-only branch choices. Reuse the audited boundary cone; it may
    erase failures, so a resulting input still requires whole-source replay.
    No public hit or negative reachability answer comes from this projection.
    """
    from ...symbolic.validation import _procedure_validation

    graph = slicing.state_range_graph(proc)
    if graph is not None:
        # Preserve source joins, but not output-only loops or their old path.
        # Reuse the same next-state slice as the state-range planner.
        body = cfg.if_convert(graph)
        keep = frozenset(id(command) for command in ir.walk(body)
                         if not isinstance(command, ir.Mark))
        body, rewritten = slicing._prune(body, keep)
        graph = slicing._observation_graph(graph, keep, rewritten)
        projected = proc.derived(body=body, graph=None, boundary_complete=False)
        ir._bind_control_flow(projected, ir._make_control_flow_authority(graph, body))
        return projected, _procedure_validation(projected)
    keep = slicing.for_boundary(proc, proc.state)
    if keep is None:
        return proc, validation
    projected = proc.derived(body=slicing.prune(proc.body, keep), graph=None,
                             boundary_complete=False)
    return projected, _procedure_validation(projected)


class WindowPathQuery(PathQuery):
    """Jointly vary a native prefix and the path frontier of every input.

    Compose source commands before encoding, with fresh input/local names per
    transaction and shared state names. The existing local encoder then slices
    and batches related goals once. No second solver or arbitrary initial state
    is introduced, and every negative answer remains path-local.
    """

    def __init__(self, instance, window, goals, *, validation, mutable_fields, input_domain,
                 context=False, write_locations=None):
        from ...symbolic.procedure_contract import ProcedureSignature
        from ...symbolic.value_binding import SolverLeafPath, ValueBinding

        self.instance, self.window = instance, tuple(window)
        self.proposal, self.context = False, context
        self.write_locations = write_locations
        if not self.window or any(b['position'] != a['position'] + 1
                                  for a, b in zip(self.window, self.window[1:])):
            raise ValueError('a path window cannot cross a reset or skip an accepted input')
        self.occurrence = self.window[0]
        self.goals = frozenset(goals)
        self.projection = self.preparation = None
        self.constructions, self.proved = {}, set()
        proc = instance.proc
        prefix = '_search_window_'
        names = set(validation.types) | {b.name for _, b in proc.value_bindings}
        while any(name.startswith(prefix) for name in names):
            prefix += '_'
        self.names = tuple({name: f'{prefix}{i}_{name}' for name in item['input']}
                           for i, item in enumerate(window))
        mappings = tuple({name: name if name in proc.state else f'{prefix}{i}_{name}'
                          for name in validation.types} for i in range(len(window)))
        bindings = []
        for i, mapping in enumerate(mappings):
            for binding in proc.signature.input_bindings:
                bindings.append(ValueBinding(self.names[i][binding.name], binding.value_type,
                    ((path, SolverLeafPath(mapping[leaf.variable], leaf.indices))
                     for path, leaf in binding.leaf_bindings)))
        # Output bindings name transaction fields, not the local variables
        # holding their buffers. Only ``output_vars`` is alpha-renamed below.
        outputs = proc.signature.output_bindings
        signature = ProcedureSignature(((b.name, b) for b in bindings),
                                       ((b.name, b) for b in outputs))
        self.env = {name: instance.env[name] for name in proc.state if name in instance.env}
        self.env.update({mapping[name]: instance.env[name]
                         for mapping in mappings for name in proc.inputs})
        mutable = {field.coordinate for field in mutable_fields}
        self.fixed_inputs = {self.names[i][name]: item['input'][name]
            for i, item in enumerate(window) for name, field in item['bindings']
            if field.coordinate not in mutable}
        self.fixed_inputs.update({self.names[i][name]: value
            for i, item in enumerate(window) for name, value in item.get('prepared_inputs', {}).items()})

        def constraints(inputs):
            return tuple(term for i, item in enumerate(self.window)
                         for term in input_domain.constraints(item['rank'],
                            {name: inputs[mappings[i][name]] for name in proc.inputs}))

        self.input_constraints = constraints
        self.work = self._compose(validation, prefix, mappings, signature, bindings, outputs)

    def _compose(self, validation, prefix, mappings, signature, bindings, outputs):
        from ...symbolic.validation import _procedure_validation

        proc, state = self.instance.proc, self.window[0]['state']
        tail_labels = _loop_free_tails(validation.graph)
        controls = _ControlFrontiers(proc, validation.graph, self.goals)
        regions = _LoopFreeRegions(proc, validation.graph)
        setup_proc, setup_validation = (_state_preparation(proc, validation)
                                       if self.context else (proc, validation))
        setup_tails = _loop_free_tails(setup_validation.graph)
        setup_regions = _LoopFreeRegions(setup_proc, setup_validation.graph)
        paths, all_types, locals_ = [], {}, []
        for i, item in enumerate(self.window):
            values = {**item['input'], **item.get('prepared_inputs', {})}
            # A window asks about every invocation, not just its last one.
            # Earlier frontier hits may terminate the restricted path without
            # reaching its normal exit; they remain valid source-prefix hits.
            context_tail = self.context and i == len(self.window) - 1
            setup = self.context and not context_tail
            path = yield from _project(setup_proc if setup else proc, values, state,
                () if self.context and not context_tail else self.goals,
                validation=setup_validation if setup else validation,
                complete=not context_tail, tail_labels=setup_tails if setup else tail_labels,
                controls=None if self.context and not context_tail else controls,
                regions=setup_regions if setup else regions, context=context_tail,
                write_locations=self.write_locations)
            if path is None or (i != len(self.window)-1 and path.normal_exit is None):
                return None
            # Generated path locals include loop counters and if-conversion
            # flags. Rename every one; no value may leak from an earlier call.
            resolved = _procedure_validation(path.procedure).types
            mapping = mappings[i]
            mapping.update({name: name if name in proc.state else f'{prefix}{i}_{name}'
                            for name in resolved})
            all_types.update({mapping[name]: kind for name, kind in resolved.items()})
            # Earlier transactions' output buffers/counts are ordinary
            # locals of the composed query, not its final output boundary.
            locals_.extend(mapping[name] for name in resolved
                           if name not in proc.state and name not in proc.inputs)
            paths.append(path)
            state = path.state
            yield
        blocks, next_entry = {}, None
        for i in range(len(paths)-1, -1, -1):
            path, label_prefix = paths[i], f'{prefix}{i}_'
            blocks.update(_renamed_graph(path.procedure.graph, mappings[i].__getitem__, label_prefix,
                                        next_entry, path.normal_exit))
            next_entry = label_prefix + path.procedure.graph.entry
            yield
        graph = cfg.CFG(blocks, next_entry)
        if self.context:
            from .path_specialization import specialize

            graph = yield from specialize(graph, {**self.window[0]['state'], **self.fixed_inputs})
        body = cfg.if_convert(graph)
        private = {cmd.var for cmd in ir.walk(body) if isinstance(cmd, ir.Assign)} - set(all_types)
        all_types.update(dict.fromkeys(private, types.BOOLEAN))
        combined = proc.derived(body=body, graph=None, boundary_complete=False,
            inputs=[variable for binding in bindings for variable in binding.solver_variables],
            locals=[*locals_, *sorted(private)], types=all_types, initial=self.window[0]['state'],
            output_vars={name: mappings[-1][var] for name, var in proc.output_vars.items()},
            count=None if proc.count is None else mappings[-1][proc.count],
            value_bindings=[*(row for row in proc.value_bindings if row[0] == 'state'),
                            *(('input', binding) for binding in bindings),
                            *(('output', binding) for binding in outputs)],
            state_ranges={}, state_range_result=None, state_range_bound=None)
        combined.signature = signature
        ir._bind_control_flow(combined, ir._make_control_flow_authority(graph, body))
        present = ir.goals_of(body)
        return PathProjection(combined, frozenset().union(*(path.targets for path in paths)) & present,
                              sum(path.commands for path in paths), sum(path.decisions for path in paths),
                              preparations=tuple(sorted({pair for path in paths for pair in path.preparations
                                                         if pair[0] in present})),
                              frontiers=tuple(tuple(goal for goal in row if goal in present)
                                              for row in paths[-1].frontiers) if self.context else ())

    def probe(self, goals, timeout_ms):
        from ..probing import Outcome, ProbeResult

        answer = super().probe(goals, timeout_ms)
        if answer.outcome not in (Outcome.SAT, Outcome.PROPOSED):
            return answer
        values = [{name: answer.candidate[renamed] for name, renamed in names.items()}
                  for names in self.names]
        return ProbeResult(answer.outcome, answer.scope, goal=answer.goal, candidate=values,
                           detail=answer.detail, solve_ms=answer.solve_ms)
