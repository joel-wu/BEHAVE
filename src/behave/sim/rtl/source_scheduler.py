"""Finite source-event transitions, encoded in the shared Behavior/RTL IR.

A transition is an external drive, one enabled source activation, or an NBA
commit. The pending work is state, not caller-supplied activation flags. Thus an
input change can change which processes run; a process can run again after a
later write; and an unchanged final value need not mean that no event occurred.

This is the scheduler relation used by the separate recorded-boundary adapter.
External drives require a quiescent boundary. Active-process selection is an
explicit choice among enabled processes; this module does not claim that an
arbitrary selected ordering is the native simulator's ordering.
"""

from dataclasses import dataclass, replace

from ..symbolic import ir, typecheck as types
from ..symbolic import ranges
from ..symbolic.procedure_contract import ProcedureSignature
from ..core.contract import FiniteType
from ..core.physical_value import pack_value
from ..core.refusal import Refusal, Refused
from .source_deferred import DeferredWrite
from .source_deferred_array import ArrayDeferredWrite
from .source_ir import _sequence
from .source_notifications import notify_value
from .source_program import SourceProgram, lower_source_program
from .source_reports import SourceReportQueue
from .source_values import ArrayType, source_type


def _any(terms):
    return ir._balanced_op("lor", terms, ir.Lit(False))


def _all(terms):
    return ir._balanced_op("land", terms, ir.Lit(True))


def _action_dispatch(actions):
    """Select one enabled action without a linear-depth generated if chain.

    Actions are a nonempty, densely numbered table. Leaves retain exact code
    equality: unused values of the full selector type must still do nothing.
    This changes representation depth, not enabled choices or event ordering.
    """
    def select(first, last):
        if last - first == 1:
            enabled, body = actions[first]
            return ir.If(ir.Op("eq", ir.Var("action"), ir.Lit(first)),
                ir.If(enabled, _sequence([ir.Assign("accepted", ir.Lit(True)), body]),
                      ir.Skip()), ir.Skip())
        middle = (first + last) // 2
        return ir.If(ir.Op("lt", ir.Var("action"), ir.Lit(middle)),
                     select(first, middle), select(middle, last))

    return select(0, len(actions))


@dataclass(frozen=True, slots=True)
class Subscription:
    process: int
    storage: str
    mask: int
    edge: str = "change"


def _event_bound(predecessors, deferred, initially_pending, notification_caps=()):
    """Upper-bound all activation orders in the compiler's dependency DAG.

    Each predecessor action can notify a consumer once; blocking execution and
    NBA commit have separate weights. Count a commit for every NBA-producing
    activation, conservatively, plus the drive. A separately proved finite
    notification cap breaks only its counting dependency, never an event edge.
    Any remaining cycle has no such proof.
    """
    counts = _activation_counts(predecessors, initially_pending, notification_caps)
    if len(counts) != len(predecessors):
        return None
    return 1 + sum(count * (1 + deferred[index]) for index, count in counts.items())


def _activation_counts(predecessors, initially_pending, notification_caps=()):
    """Proved counts for the acyclic portion, including capped event edges."""
    caps = {(parent, child): cap for parent, child, cap in notification_caps}
    counts, remaining = {}, set(range(len(predecessors)))
    while remaining:
        ready = {index for index in remaining
                 if all(parent in counts or (parent, index) in caps
                        for parent, _ in predecessors[index])}
        if not ready:
            break
        for index in ready:
            counts[index] = int(index in initially_pending) + sum(
                caps[parent, index] if (parent, index) in caps else counts[parent] * weight
                for parent, weight in predecessors[index])
        remaining.difference_update(ready)
    return counts


@dataclass(frozen=True, slots=True)
class SourceScheduler:
    procedure: ir.Procedure
    program: SourceProgram
    pending: tuple[str, ...]
    drive_inputs: tuple[tuple[str, str], ...]
    startup: tuple[bool, ...]
    input_ranges: dict
    queue_edges: tuple[tuple[DeferredWrite, str, str], ...]
    event_bound: int | None
    subscriptions: tuple[Subscription, ...]
    predecessors: tuple[tuple[tuple[int, int], ...], ...]
    reports: SourceReportQueue | None = None
    report_actions: tuple[tuple[str, object, int], ...] = ()
    notification_caps: tuple[tuple[int, int, int], ...] = ()
    # Shared lowered actions, including write-time notifications. Consumers
    # must not reconstruct activations from settled values.
    actions: tuple = ()
    quiet: object = None

    def phase_event_bounds(self, state, phases, mutable_inputs, limit):
        """Remove only proved quiet padding from a quiescent-boundary query.

        Mutable inputs may change arbitrarily on the first drive, then hold
        the same symbolic value throughout these phases. All other pins follow
        the supplied images. Source processes cannot write Driver inputs, so
        their earlier driven values do not depend on active-process ordering.
        This counts possible activations, not the events of one native trace.
        The caller's original finite limit is never increased.
        """
        if self.reports is not None:
            # The old dependency-DAG bound excludes late actions and their
            # notifications. Keep the whole declared local domain until its
            # report-aware replacement is proved; never trim those events.
            return (limit,) * len(phases)
        deferred = tuple(bool(process.deferred) for process in self.program.processes)
        bounds, previous = [], state
        for phase, pins in enumerate(phases):
            active, changes = set(), {}
            for subscription in self.subscriptions:
                name = subscription.storage
                if name not in pins:
                    continue  # Only the external drive can create initial work.
                if name in mutable_inputs:
                    if phase == 0:
                        active.add(subscription.process)
                    continue  # Later phases hold exactly the same symbolic data.
                if name not in changes:
                    kind = self.program.kinds[name].signature_type
                    before, after = pack_value(kind, previous[name]), pack_value(kind, pins[name])
                    changed = before ^ after
                    changes[name] = {"change": changed, "rise": changed & after,
                                     "fall": changed & before}
                if changes[name][subscription.edge] & subscription.mask:
                    active.add(subscription.process)
            proved = _event_bound(self.predecessors, deferred, active, self.notification_caps)
            bounds.append(limit if proved is None else min(limit, proved))
            previous = pins
        return tuple(bounds)

    @property
    def commit_action(self):
        return len(self.pending) + 1

    @property
    def occurrences(self):
        return tuple(item for process in self.program.processes for item in process.occurrences)

    @property
    def domain_checks(self):
        return tuple(item for process in self.program.processes for item in process.domain_checks)

    def boundary_fields(self, state_names=None):
        """Native storage needed by an exact relation's closed state cone.

        Empty NBA payloads use their target's actual value as before. Requiring
        that target is part of constructing the private boundary, not a new
        symbolic input or a made-up DUT value.
        """
        retained = set(self.procedure.state if state_names is None else state_names)
        if not retained <= set(self.procedure.state):
            raise ValueError("a source boundary can only retain its own scheduler state")
        return (retained.intersection(self.program.fields)
                | {write.target for write, _, _ in self.queue_edges
                   if retained.intersection((write.data, write.first))})

    def boundary(self, values, *, startup=False, state_names=None):
        """Import an explicitly known quiet source image, never invent DUT state.

        The public recorded-boundary adapter establishes this precondition from
        execution. Only scheduler-private empty queues are initialized here.
        ``startup`` additionally schedules the compiler's initial combo work.
        """
        required = self.boundary_fields(state_names)
        if not required <= set(values) <= set(self.program.fields):
            raise ValueError("a source boundary needs every retained compiler-bound storage value")
        retained = set(self.procedure.state if state_names is None else state_names)
        state = dict(values)
        state.update((flag, startup and run) for flag, run in zip(self.pending, self.startup))
        for write, rises, falls in self.queue_edges:
            if retained.intersection((write.data, write.first)):
                current = values[write.target]
                state.update({name: current for name in (write.data, write.first) if name in retained})
            empty = write.empty() if isinstance(write, ArrayDeferredWrite) else 0
            state.update({write.valid: False, write.varied: empty, write.mask: empty, rises: empty, falls: empty})
        if self.reports is not None:
            state.update(self.reports.boundary())
        return state

    def boundary_is_quiet(self, state):
        return (not any(state[flag] for flag in self.pending)
                and not any(state[write.valid] for write, _, _ in self.queue_edges)
                and (self.reports is None or not any(state[self.reports.status])))


def _node_at(document, key, symbols):
    node = document["design"]
    for part in key:
        node = node[part]
        if isinstance(node, str):
            node = symbols[node.split(" ", 1)[0]]
    return node


def _subscriptions(program, document, sensitivity, *, filename):
    rows = {(row["scope"], row["start"]): row for row in sensitivity if row["file"] == filename}
    result, startup = [], []
    for index, process in enumerate(program.processes):
        if process.initial_process is not None:
            startup.append(True)
            continue
        context = process.context
        if context is None:
            result.extend(Subscription(index, name, (1 << program.kinds[name].width) - 1)
                          for name in process.implicit_reads)
            startup.append(True)
            continue
        node = _node_at(document, context.key, program.compiler.symbols)
        row = rows.get((".".join(context.scope or ()), context.process_start))
        body = node.get("body", {})
        timing = body.get("timing", {}) if body.get("kind") == "Timed" else {}
        implicit = (node["kind"] in {"Net", "ContinuousAssign"}
                    or node.get("procedureKind") in {"AlwaysComb", "AlwaysLatch"}
                    or timing.get("kind") == "ImplicitEvent")
        if implicit:
            if row is None:
                raise Refused(Refusal("rtl-source-scheduler-dependencies-missing",
                    "implicit activation requires the same compiler read ranges as native observation",
                    context=context.key))
            for read in row["reads"]:
                name, low, high = program.storage_range(str(read["symbol_id"]), read["low"], read["high"])
                result.append(Subscription(index, name, ((1 << (high - low + 1)) - 1) << low))
            # Matches the pinned native observer: an empty @* does not wake;
            # always_comb and continuous drivers run once even without reads.
            startup.append(timing.get("kind") != "ImplicitEvent" or bool(row["reads"]))
            continue
        startup.append(False)
        events = timing.get("events", ()) if timing.get("kind") == "EventList" else (timing,)
        for event in events:
            expression = event.get("expr", {})
            if (event.get("kind") != "SignalEvent" or event.get("iff") is not None
                    or expression.get("kind") not in {"NamedValue", "HierarchicalValue"}
                    or event.get("edge") not in {"PosEdge", "NegEdge", "None"}):
                raise Refused(Refusal("rtl-source-scheduler-trigger-unsupported",
                    "this source event expression needs an implemented activation relation",
                    context=context.key))
            identity = expression["symbol"].split(" ", 1)[0]
            edge = {"PosEdge": "rise", "NegEdge": "fall", "None": "change"}[event["edge"]]
            # The pinned native backend initially settles value-event arms,
            # including a mixed value/edge wait. Pure edge waits do not start
            # until an actual transition. Use the validated compiler event,
            # not the body's read set or a fact inferred from a replay.
            startup[-1] |= edge == "change"
            kind = source_type(expression["type"], program.compiler.symbols)
            name, low, high = program.storage_range(identity, 0, kind.width - 1 if edge == "change" else 0)
            mask = ((1 << (high - low + 1)) - 1) << low
            result.append(Subscription(index, name, mask, edge))
    return tuple(result), tuple(startup)


class _SchedulerBuilder:
    def __init__(self, program, subscriptions):
        self.program = program
        self.pending = tuple(f"pending{i}" for i in range(len(program.processes)))
        self.types = {name: program.kinds[name].ir_type for name in program.fields}
        self.types.update((name, types.BOOLEAN) for name in self.pending)
        self.domains = {name: program.domain(name) for name in program.fields}
        self.domains.update((name, ranges.Range(0, 1)) for name in self.pending)
        self.subscribers = {}
        for subscription in subscriptions:
            self.subscribers.setdefault(subscription.storage, []).append(subscription)
        self.queues = []
        writers = {}
        self.predecessors = [{} for _ in program.processes]
        for index, process in enumerate(program.processes):
            self.types.update(process.procedure.types)
            blocking = {name: mask for name, mask, _ in process.write_masks if mask}
            deferred = {name: mask for name, _, mask in process.write_masks if mask}
            for name, immediate, pending in process.write_masks:
                mask = immediate | pending
                for owner, before, queued in writers.get(name, ()):
                    if not (before | queued) & mask:
                        continue
                    if not (process.initial_process or program.processes[owner].initial_process):
                        raise Refused(Refusal("rtl-source-scheduler-competing-writers",
                            "overlapping source writers require their own ordering semantics",
                            storage=name, source=program.fields[name].name))
                    if queued & pending:
                        raise Refused(Refusal("rtl-source-scheduler-competing-initial-nba",
                            "overlapping NBA producers need cross-process enqueue ordering",
                            storage=name, source=program.fields[name].name))
                if program.fields[name].input:
                    raise Refused(Refusal("rtl-source-scheduler-input-written",
                        "a source process cannot own a Driver input", storage=name))
                # Initial blocking writes participate in the same active-order
                # relation as runtime startup work. No ordering is selected here.
                writers.setdefault(name, []).append((index, immediate, pending))
            # One activation can notify a consumer directly and again at NBA
            # commit. Within either action pending flags coalesce notifications.
            for at_commit, targets in ((False, blocking), (True, deferred)):
                consumers = {subscription.process for name, mask in targets.items()
                             for subscription in self.subscribers.get(name, ())
                             if subscription.mask & mask and (at_commit or subscription.process != index)}
                for consumer in consumers:
                    predecessors = self.predecessors[consumer]
                    predecessors[index] = predecessors.get(index, 0) + 1
            for write in process.deferred:
                rises, falls = ((write.rises, write.falls) if isinstance(write, ArrayDeferredWrite) else
                                (f"nba_rise{len(self.queues)}", f"nba_fall{len(self.queues)}"))
                self.queues.append((write, rises, falls))
                mask_type = write.array.ir_type if write.array is not None else types.INTEGER
                self.types.update({rises: mask_type, falls: mask_type})
                for name in (write.data, write.first):
                    self.domains[name] = program.domain(write.target)
                for name in (write.varied, write.mask, rises, falls):
                    self.domains[name] = (write.mask_kind.domain if isinstance(write, ArrayDeferredWrite)
                                          else ranges.Range(0, (1 << write.width) - 1))
                self.domains[write.valid] = ranges.Range(0, 1)
        self.queue_by_data = {write.data: (write, rises, falls) for write, rises, falls in self.queues}
        self.scratch = 0

    def temp(self, *, kind=types.INTEGER):
        name = f"event_tmp{self.scratch}"
        self.scratch += 1
        self.types[name] = kind
        return name

    def changes(self, old, new, width):
        different = ir.Op("uwrap", ir.Op("xor", old, new), ir.Lit(width))
        return {"change": different, "rise": ir.Op("and", different, new),
                "fall": ir.Op("and", different, old)}

    def notify_value(self, target, kind, old, new, running=None, *, offset=None):
        subscribers = tuple(row for row in self.subscribers.get(target, ()) if row.process != running)
        return notify_value(kind, old, new, subscribers, self.pending, self.temp, offset=offset)

    def notify(self, target, changes, running=None):
        conditions = {}
        for subscriber in self.subscribers.get(target, ()):
            if subscriber.process == running:
                continue
            conditions.setdefault(subscriber.process, []).append(
                ir.Op("truth", ir.Op("and", changes[subscriber.edge], ir.Lit(subscriber.mask))))
        return [ir.If(_any(terms), ir.Assign(self.pending[index], ir.Lit(True)), ir.Skip())
                for index, terms in conditions.items()]

    def activation(self, command, running):
        """Insert notifications at writes, not by comparing final process state."""
        # A lowered source process may contain a linear-depth sequence even
        # when its source has no nested control. Visit occurrences left to
        # right, preserving scratch allocation and every transient write.
        # Shared command objects are not merged: each occurrence owns its
        # notifications just as in the recursive rule.
        pending, completed = [(command, False)], []
        while pending:
            node, expanded = pending.pop()
            if isinstance(node, (ir.Seq, ir.If, ir.For)):
                if not expanded:
                    pending.append((node, True))
                    if isinstance(node, ir.Seq):
                        children = (node.first, node.second)
                    elif isinstance(node, ir.If):
                        children = (node.then, node.orelse)
                    else:
                        children = (node.body,)
                    pending.extend((child, False) for child in reversed(children))
                    continue
                last = completed.pop()
                if isinstance(node, ir.Seq):
                    result = ir.Seq(completed.pop(), last)
                elif isinstance(node, ir.If):
                    result = ir.If(node.guard, completed.pop(), last)
                else:
                    result = ir.For(node.index, node.trip_count, last)
            else:
                result = self._activation_write(node, running)
            completed.append(result)
        return completed[0]

    def _activation_write(self, command, running):
        if isinstance(command, ir.Assign) and command.var in self.program.fields:
            if command.var not in self.subscribers:
                return command
            kind = self.program.kinds[command.var]
            old = self.temp(kind=kind.ir_type)
            return _sequence([ir.Assign(old, ir.Var(command.var)), command,
                *self.notify_value(command.var, kind, ir.Var(old), ir.Var(command.var), running)])
        if isinstance(command, ir.Store) and command.var in self.program.fields:
            if command.var not in self.subscribers:
                return command
            kind = self.program.kinds[command.var]
            if not isinstance(kind, ArrayType):
                raise TypeError("a source indexed store needs recursive array storage")
            old, index = self.temp(kind=kind.element.ir_type), self.temp()
            selected = ir.Sel(ir.Var(command.var), ir.Var(index))
            offset = ir.Op("mul", ir.Var(index), ir.Lit(kind.element.width))
            return _sequence([ir.Assign(index, command.index), ir.Assign(old, selected), command,
                *self.notify_value(command.var, kind.element, ir.Var(old), selected, running, offset=offset)])
        if isinstance(command, ir.Assign) and command.var in self.queue_by_data:
            write, rises, falls = self.queue_by_data[command.var]
            if isinstance(write, ArrayDeferredWrite):
                return command  # Enqueue records direction history at the selected leaf.
            # Only previously written bits have an earlier queued value. The
            # first write to another bit is compared with the value at commit.
            changes = {event: ir.Op("and", bits, ir.Var(write.mask)) for event, bits in
                       self.changes(ir.Var(write.data), command.expr, write.width).items()}
            history = _sequence([ir.Assign(rises, ir.Op("or", ir.Var(rises), changes["rise"])),
                                 ir.Assign(falls, ir.Op("or", ir.Var(falls), changes["fall"]))])
            return ir.Seq(ir.If(ir.Var(write.valid), history, ir.Skip()), command)
        return command

    def commit(self):
        commands = []
        for write, rises, falls in self.queues:
            if isinstance(write, ArrayDeferredWrite):
                commands.append(ir.If(ir.Var(write.valid), _sequence([
                    *write.notifications(self.subscribers.get(write.target, ()), self.pending, self.temp),
                    ir.Assign(write.target, write.committed_value(ir.Var(write.target))),
                    ir.Assign(write.valid, ir.Lit(False)), *write.clear_masks()]), ir.Skip()))
                continue
            before, current = write.current_bits(self.temp)
            first = {event: ir.Op("and", bits, ir.Var(write.mask)) for event, bits in
                     self.changes(current, ir.Var(write.first), write.width).items()}
            changes = {"change": ir.Op("or", first["change"], ir.Var(write.varied)),
                       "rise": ir.Op("or", first["rise"], ir.Var(rises)),
                       "fall": ir.Op("or", first["fall"], ir.Var(falls))}
            commands.append(ir.If(ir.Var(write.valid), _sequence([
                *before, *self.notify(write.target, changes),
                *write.assign_bits(write.committed_value(current), self.temp),
                ir.Assign(write.valid, ir.Lit(False)), ir.Assign(write.varied, ir.Lit(0)),
                ir.Assign(write.mask, ir.Lit(0)),
                ir.Assign(rises, ir.Lit(0)), ir.Assign(falls, ir.Lit(0))]), ir.Skip()))
        return _sequence(commands)


def lower_source_scheduler(source, document, plan, sensitivity, *, filename, defer_domains=False,
                           initialization=False, native_assignments=False):
    """Compile source state, compiler subscriptions, and legal event transitions.

    Whole, identically typed wire ports collapse into their compiler-bound net;
    variable/computed ports use typed propagation transitions. Portless child
    storage already has shared ownership and needs no transfer. Existing native
    baseline support is independent of this optional query representation.
    """
    program = lower_source_program(source, document, plan, filename=filename,
                                   defer_domains=defer_domains, connect_ports=True,
                                   initialization=initialization, native_assignments=native_assignments)
    for identity, field in program.bindings.items():
        if field.input or initialization:
            # Whole-history construction owns all physical storage, including
            # declarations not referenced by any selected source occurrence.
            program.storage_name(identity)
    subscriptions, startup = _subscriptions(program, document, sensitivity, filename=filename)
    return _compose_source_scheduler(program, subscriptions, startup)


def _notification_caps(program, subscriptions, predecessors):
    """Bound proved value changes without removing any event notifications.

    A single owner assigning one held-input function once per path can notify
    each subscriber at most once per physical drive. Several such fields give
    the sum of their caps. Other writes, NBA updates and genuine feedback keep
    the original activation-count dependency. The transition relation and all
    notifications/goal occurrences are unchanged. Unconditional constant-cell
    writes also admit a finite value-dependence proof: one initial change plus
    the changes of their entry reads. Resolve those facts only from already
    proved counts, so a genuine value-feedback cycle cannot prove itself.
    """
    from .source_effects import input_constant_writes, single_write_dependencies

    inputs = {name for name, field in program.fields.items() if field.input}
    subscribers = {}
    for subscription in subscriptions:
        subscribers.setdefault(subscription.storage, []).append(subscription)
    owners, deferred, writers = {}, set(), {}
    for index, process in enumerate(program.processes):
        for name, immediate, pending in process.write_masks:
            owners.setdefault(name, set()).add(index)
            writers.setdefault(name, []).append((index, immediate, pending))
            if pending:
                deferred.add(name)
    certificates, caps = {}, {}
    for index, process in enumerate(program.processes):
        fixed = input_constant_writes(process.activation, set(program.fields), inputs)
        fixed = {name for name in fixed if owners[name] == {index} and name not in deferred}
        for (name, mask), dependencies in single_write_dependencies(
                process.activation, program.kinds,
                {name: immediate for name, immediate, _ in process.write_masks}).items():
            certificates[index, name, mask] = dependencies
        for name in fixed:
            certificates[index, name, (1 << program.kinds[name].width) - 1] = set()

    changes = {}

    def changed_bits(index, name, mask):
        covered, total = 0, 0
        for (owner, field, bits), count in changes.items():
            if owner == index and field == name and mask & bits:
                covered |= bits
                total += count
        return total if mask & ~covered == 0 else None

    def read_changes(name, mask, activations):
        total = 0
        for owner, immediate, pending in writers.get(name, ()):
            bits = mask & (immediate | pending)
            if not bits:
                continue
            choices = []
            if owner in activations:
                # Activations and NBA commits are atomic scheduler actions.
                # This bounds entry-value changes, not intermediate wakeups.
                choices.append(activations[owner] * (1 + bool(pending & bits)))
            if not pending & bits:
                proved = changed_bits(owner, name, bits)
                if proved is not None:
                    choices.append(proved)
            if not choices:
                return None
            total += min(choices)
        return total  # Held pins and unwritten storage do not change mid-drive.

    while True:
        previous = len(changes), len(caps)
        activations = _activation_counts(predecessors, set(range(len(program.processes))),
            tuple((*edge, count) for edge, count in caps.items()))
        for target, dependencies in certificates.items():
            if target in changes:
                continue
            counts = [read_changes(name, mask, activations) for name, mask in dependencies]
            if all(count is not None for count in counts):
                changes[target] = 1 + sum(counts)
        for index, process in enumerate(program.processes):
            edges, unbounded = {}, set()
            for name, immediate, pending in process.write_masks:
                for subscription in subscribers.get(name, ()):
                    consumer = subscription.process
                    if pending & subscription.mask:
                        unbounded.add(consumer)
                    if consumer != index and immediate & subscription.mask:
                        bits = edges.setdefault(consumer, {})
                        bits[name] = bits.get(name, 0) | (immediate & subscription.mask)
            for consumer, fields in edges.items():
                if consumer in unbounded or (index, consumer) in caps:
                    continue
                counts = [changed_bits(index, name, mask) for name, mask in fields.items()]
                if all(count is not None for count in counts):
                    caps[index, consumer] = sum(counts)
        if (len(changes), len(caps)) == previous:
            return tuple((*edge, count) for edge, count in sorted(caps.items()))


def _compose_source_scheduler(program, subscriptions, startup, *, reports=(), transition_bound=None):
    """Compose typed activations, not another simulator or public admission.

    A report-bearing composition needs its existing finite local-query domain.
    Production source lowering still refuses deferred assertions: declaration
    binding and control semantics must be qualified before using this path.
    """
    builder = _SchedulerBuilder(program, subscriptions)
    pending = builder.pending
    queue_empty = ir.Op("not", _any(ir.Var(write.valid) for write, _, _ in builder.queues))
    active_empty = ir.Op("not", _any(ir.Var(flag) for flag in pending))
    settled = _all((active_empty, queue_empty))
    report_queue = None
    if reports:
        # Late action writes notify even their own source process. Executing
        # an action is not resuming that source process or flushing its queue.
        reports = tuple(replace(report, action=builder.activation(report.action, None)) for report in reports)
        report_queue = SourceReportQueue(reports, tuple(p.activation for p in program.processes),
            builder.domains, transition_bound=transition_bound)
        builder.domains.update(report_queue.domains)
    quiet = settled if report_queue is None else _all((settled, report_queue.empty))
    drive_inputs = tuple((name, f"drive_{name}") for name, field in program.fields.items() if field.input)
    input_kinds = {driven: program.kinds[name].signature_type for name, driven in drive_inputs}
    input_domains = {driven: program.domain(name) for name, driven in drive_inputs}
    builder.types.update((driven, program.kinds[name].ir_type) for name, driven in drive_inputs)
    builder.types["action"] = types.INTEGER
    builder.types["accepted"] = types.BOOLEAN
    drive = []
    for name, driven in drive_inputs:
        drive.extend(builder.notify_value(name, program.kinds[name], ir.Var(name), ir.Var(driven)))
    drive.extend(ir.Assign(name, ir.Var(driven)) for name, driven in drive_inputs)
    if report_queue is not None:
        drive.insert(0, report_queue.reset())
    actions = [(quiet, _sequence(drive))]
    for index, process in enumerate(program.processes):
        body = builder.activation(process.activation, index)
        enabled, prefix = ir.Var(pending[index]), []
        if report_queue is not None:
            enabled = _all((enabled, report_queue.allocation_guard))
            prefix.append(report_queue.cancel(index))
            body = report_queue.lower(body)
        actions.append((enabled, _sequence([
            *prefix, ir.Assign(pending[index], ir.Lit(False)), body])))
    actions.append((_all((active_empty, ir.Op("not", queue_empty))), builder.commit()))
    report_actions = []
    if report_queue is not None:
        for region in ('observed', 'final'):
            ready = settled
            if region == 'final':
                ready = _all((ready, ir.Op('not', report_queue.available('observed')),
                    ir.Op('not', report_queue.available('observed', mature=True))))
            report_actions.append((region, None, len(actions)))
            actions.append((_all((ready, report_queue.available(region))), report_queue.mature(region)))
            owners = dict.fromkeys(report.owner for report in reports if report.region == region)
            for owner in owners:
                report_actions.append((region, owner, len(actions)))
                actions.append((_all((ready, ir.Op('not', report_queue.available(region)),
                    report_queue.available(region, owner=owner, mature=True), report_queue.allocation_guard)),
                    report_queue.dispatch(owner, region)))
        builder.types.update(report_queue.types)
    input_kinds["action"] = FiniteType(max(1, (len(actions) - 1).bit_length()))
    # Unused selector codes are not source transitions: retain the full typed
    # domain and leave their accepted output false.
    input_domains["action"] = ranges.Range(0, (1 << input_kinds["action"].width) - 1)
    dispatch = _action_dispatch(actions)
    buffers = {name: f"{name}_buffer" for name in ("accepted", "quiescent")}
    builder.types.update((name, types.Array(1, types.INTEGER)) for name in buffers.values())
    pack = [ir.Assign(buffers["accepted"], ir.Op("arraylit", ir.Op("toint", ir.Var("accepted")))),
            ir.Assign(buffers["quiescent"], ir.Op("arraylit", ir.Op("toint", quiet)))]
    state = list(builder.domains)
    proc = ir.Procedure(state, list(input_kinds),
        [name for name in builder.types if name not in {*state, *input_kinds}], list(buffers),
        _sequence([ir.Assign("accepted", ir.Lit(False)), dispatch, *pack]), types=builder.types,
        output_vars=buffers, output_capacity=1,
        signature=ProcedureSignature(tuple(input_kinds.items()), tuple((name, FiniteType(1)) for name in buffers)))
    proc.state_ranges = builder.domains
    predecessors = tuple(tuple(sorted(row.items())) for row in builder.predecessors)
    deferred = tuple(bool(p.deferred) for p in program.processes)
    initially_pending = set(range(len(program.processes)))
    caps = ()
    bound = None if report_queue is not None else _event_bound(predecessors, deferred, initially_pending)
    if bound is None and report_queue is None:
        caps = _notification_caps(program, subscriptions, predecessors)
        bound = _event_bound(predecessors, deferred, initially_pending, caps)
    return SourceScheduler(proc, program, pending, drive_inputs, startup, input_domains,
                           tuple(builder.queues), bound, subscriptions, predecessors,
                           report_queue, tuple(report_actions), caps, tuple(actions), quiet)
