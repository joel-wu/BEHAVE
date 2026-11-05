"""Finite deferred-report frames in the shared command/value IR.

This is a local-query representation, not a native runtime queue limit. For a
query of K transitions, the largest proved per-transition emission count E
requires at most K*E frames. Frames are append-only within that query interval;
cancellation and dispatch clear validity but never replace an earlier value.
The scheduler owns source resumption and event-region ordering. This module
owns pending/mature lifetime, typed captures and per-owner dispatch order.

An emission marker is construction-only IR. ``lower`` replaces it with actual
frame stores before encoding; it is not an additional public goal or hit.
Production deferred admission stays closed until the scheduler binds this
relation, assertion controls and native replay to the same source plan.
"""

from dataclasses import dataclass

from ..symbolic import ir, typecheck as types
from ..symbolic import ranges
from ..symbolic.uniform_array import UniformArray
from ..core.refusal import Refusal, Refused


def _all(expressions):
    return ir._balanced_op('land', expressions, ir.Lit(True))


def _any(expressions):
    return ir._balanced_op('lor', expressions, ir.Lit(False))


def _zero(kind):
    return (UniformArray(kind.cells, _zero(kind.elem)) if isinstance(kind, types.Array)
            else False if kind == types.BOOLEAN else 0)


@dataclass(frozen=True, slots=True)
class ReportCapture:
    name: str
    type: object
    domain: object


@dataclass(frozen=True, slots=True)
class SourceReport:
    # Compiler-bound identities, not textual labels or speculative goal IDs.
    owner: object
    site: object
    region: str
    maturity_goal: str | None
    captures: tuple[ReportCapture, ...]
    action: object
    emission: ir.Assign

    def __post_init__(self):
        if self.region not in {'observed', 'final'}:
            raise ValueError('a deferred report has an Observed or Final region')


def _emission_bound(body, markers, entry_ranges):
    """Count original commands under shared, proved finite loop bounds.

    This never estimates from a replay or substitutes a maximum iteration
    count into execution. Mutually exclusive arms use their maximum, sequences
    sum and nested loops multiply. The original IR is not changed by the proof.
    """
    visited, loops = set(), {}

    def at(command, environment):
        visited.add(id(command))
        if isinstance(command, ir.For):
            bound = ranges.loop_trip_range(command, environment)
            if bound is None or bound.hi is None:
                raise Refused(Refusal('rtl-source-report-bound-unproved',
                    'report frames need a finite source emission bound'))
            loops[id(command)] = max(loops.get(id(command), 0), max(0, bound.hi))

    ranges.walk_post(body, dict(entry_ranges), at)
    counts = {}
    for command in reversed(list(ir.walk(body))):
        key = id(command)
        # The shared range walker witnesses executable commands, not Seq
        # containers. An unwitnessed sequence still contains visited emissions.
        if isinstance(command, ir.Seq):
            count = counts[id(command.first)] + counts[id(command.second)]
        elif key not in visited:
            count = 0
        elif key in markers:
            count = 1
        elif isinstance(command, ir.If):
            count = max(counts[id(command.then)], counts[id(command.orelse)])
        elif isinstance(command, ir.For):
            count = loops[key] * counts[id(command.body)]
        else:
            count = 0
        counts[key] = count
    return counts[id(body)]


class SourceReportQueue:
    """Compile report operations; all state and actions remain ordinary IR.

    ``bodies`` must contain each source activation that can emit a report;
    report action bodies are included here as well. ``transition_bound`` is
    the local query's existing finite micro-transition domain, not a new knob.
    An emitting transition uses ``allocation_guard``. From an empty boundary,
    the K*E proof guarantees that guard for every one of the first K transitions;
    it cannot eliminate a source path within that declared query domain.
    """

    PENDING = 1
    MATURE = 2

    def __init__(self, reports, bodies, entry_ranges, *, transition_bound, namespace='reports_'):
        if type(transition_bound) is not int or transition_bound < 1:
            raise ValueError('report frames require the positive local transition domain')
        self.reports = tuple(reports)
        self.markers = {id(report.emission): index for index, report in enumerate(self.reports)}
        if len(self.markers) != len(self.reports):
            raise ValueError('each source report needs its own bound emission command')
        self.transition_bound = transition_bound
        domains = dict(entry_ranges)
        for report in self.reports:
            domains.update((capture.name, capture.domain) for capture in report.captures)
        roots = (*bodies, *(report.action for report in self.reports))
        self.per_transition = max((_emission_bound(body, self.markers, domains) for body in roots), default=0)
        # A one-cell inert carrier also represents a proved empty queue; it
        # grants no executable emission and is not a user-visible queue bound.
        self.capacity = max(1, transition_bound * self.per_transition)
        self.namespace, self.next_temp = namespace, 0
        self.used, self.status, self.tag = (namespace + name for name in ('used', 'status', 'tag'))
        self.types = {self.used: types.INTEGER,
                      self.status: types.Array(self.capacity), self.tag: types.Array(self.capacity)}
        self.domains = {self.used: ranges.Range(0, self.capacity),
            self.status: ranges._UniformCellRange(self.capacity, ranges.Range(0, self.MATURE)),
            self.tag: ranges._UniformCellRange(self.capacity, ranges.Range(0, max(0, len(self.reports)-1)))}
        self.payloads = []
        for index, report in enumerate(self.reports):
            fields = []
            for position, capture in enumerate(report.captures):
                name = f'{namespace}value{index}_{position}'
                self.types[name] = types.Array(self.capacity, capture.type)
                # Empty cells are private carriers. Their zero initialization
                # must fit the encoding without assuming an unqueued payload.
                carrier = capture.domain.join(ranges.range_of_value(_zero(capture.type)))
                self.domains[name] = ranges._UniformCellRange(self.capacity, carrier)
                fields.append(name)
            self.payloads.append(tuple(fields))

    def temp(self, *, kind=types.INTEGER):
        name = f'{self.namespace}tmp{self.next_temp}'
        self.next_temp += 1
        self.types[name] = kind
        return name

    def boundary(self):
        return {name: _zero(self.types[name]) for name in self.domains}

    @property
    def allocation_guard(self):
        return ir.Op('le', ir.Var(self.used), ir.Lit(self.capacity-self.per_transition))

    def _matches(self, index, state, selected):
        return _all((ir.Op('eq', ir.Sel(ir.Var(self.status), index), ir.Lit(state)),
            _any(ir.Op('eq', ir.Sel(ir.Var(self.tag), index), ir.Lit(tag)) for tag in selected)))

    def available(self, region, *, owner=None, mature=False):
        selected = tuple(index for index, report in enumerate(self.reports)
                         if report.region == region and (owner is None or report.owner == owner))
        state = self.MATURE if mature else self.PENDING
        return _any(self._matches(ir.Lit(index), state, selected) for index in range(self.capacity))

    @property
    def empty(self):
        return ir.Op('not', _any(ir.Op('truth', ir.Sel(ir.Var(self.status), ir.Lit(index)))
                                for index in range(self.capacity)))

    def reset(self):
        """Reclaim an already quiet interval; the caller must require empty."""
        return ir.Assign(self.used, ir.Lit(0))

    def _enqueue(self, tag):
        report = self.reports[tag]
        index = ir.Var(self.used)
        # No last-value overwrite, truncation or fake safety failure on a full
        # buffer. Allocation is guaranteed by the enclosing finite-domain proof.
        return ir._sequence([
            *(ir.Store(field, index, ir.Var(capture.name))
              for field, capture in zip(self.payloads[tag], report.captures, strict=True)),
            ir.Store(self.tag, index, ir.Lit(tag)),
            ir.Store(self.status, index, ir.Lit(self.PENDING)),
            ir.Assign(self.used, ir.Op('add', index, ir.Lit(1)))])

    def lower(self, body):
        rewritten = {}
        for command in reversed(list(ir.walk(body))):
            key = id(command)
            if key in self.markers:
                result = self._enqueue(self.markers[key])
            elif isinstance(command, ir.Seq):
                result = ir.Seq(rewritten[id(command.first)], rewritten[id(command.second)])
            elif isinstance(command, ir.If):
                result = ir.If(command.guard, rewritten[id(command.then)], rewritten[id(command.orelse)])
            elif isinstance(command, ir.For):
                result = ir.For(command.index, command.trip_count, rewritten[id(command.body)])
            else:
                result = command
            rewritten[key] = result
        return rewritten[id(body)]

    def mature(self, region):
        """Publish every surviving outcome before any action can terminate."""
        index = self.temp()
        dispatch = []
        for tag, report in enumerate(self.reports):
            if report.region != region:
                continue
            mark = ir.Skip() if report.maturity_goal is None else ir.Mark(report.maturity_goal)
            dispatch.append(ir.If(self._matches(ir.Var(index), self.PENDING, (tag,)),
                ir.Seq(ir.Store(self.status, ir.Var(index), ir.Lit(self.MATURE)), mark), ir.Skip()))
        return ir.For(index, ir.Var(self.used), ir._sequence(dispatch))

    def cancel(self, target, *, by='owner'):
        """A linked owner/site cancels pending frames only; no future disable."""
        if by not in {'owner', 'site'}:
            raise ValueError('a report cancellation binds a source owner or assertion site')
        selected = tuple(index for index, report in enumerate(self.reports)
                         if getattr(report, by) == target)
        index = self.temp()
        return ir.For(index, ir.Var(self.used), ir.If(
            self._matches(ir.Var(index), self.PENDING, selected),
            ir.Store(self.status, ir.Var(index), ir.Lit(0)), ir.Skip()))

    def dispatch(self, owner, region):
        """Execute the oldest mature frame of this source owner and region.

        Owners may be selected independently by the scheduler. Dispatch is not
        source resumption and must not cancel newly pending reports of its owner.
        """
        index, done = self.temp(), self.temp(kind=types.BOOLEAN)
        choices = []
        for tag, report in enumerate(self.reports):
            if report.owner != owner or report.region != region:
                continue
            restore = [ir.Assign(capture.name, ir.Sel(ir.Var(field), ir.Var(index)))
                       for field, capture in zip(self.payloads[tag], report.captures, strict=True)]
            choices.append(ir.If(_all((ir.Op('not', ir.Var(done)),
                self._matches(ir.Var(index), self.MATURE, (tag,)))), ir._sequence([
                    ir.Assign(done, ir.Lit(True)),
                    ir.Store(self.status, ir.Var(index), ir.Lit(0)),
                    *restore, self.lower(report.action)]), ir.Skip()))
        return ir.Seq(ir.Assign(done, ir.Lit(False)),
                      ir.For(index, ir.Var(self.used), ir._sequence(choices)))
