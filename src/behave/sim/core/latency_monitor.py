"""Native start/end pairing over the existing B/R transaction projection."""

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from .catalogue import CatalogueManifest, catalogue_subset
from .compare import transaction_samples
from .goal_provider import (
    BEHAVIOR_LOCAL_GUIDANCE_POLICY,
    CONCRETE_HIT,
    DECLARATION,
    EXACT_BOUNDED_HIT,
    LOCAL_GUIDANCE,
    RTL_LOCAL_GUIDANCE_POLICY,
    CapabilityBinding,
    GoalProvider,
    GoalProviderIdentity,
    standard_goal_schemas,
)
from .latency_goal import POLICY
from .monitor import MonitorHit, MonitorReport, Unresolved


@dataclass(slots=True)
class _History:
    pending: dict = field(default_factory=lambda: defaultdict(deque))
    counter: int = 0
    known: bool = True

    def snapshot(self):
        return self.known, self.counter, tuple((key, tuple(queue)) for key, queue in self.pending.items())

    @classmethod
    def from_snapshot(cls, snapshot=None):
        if snapshot is None:
            return cls()
        known, counter, pending = snapshot
        return cls(defaultdict(deque, ((key, deque(queue)) for key, queue in pending)), counter, known)

    def advance(self, rule, start, end, samples, now, slot):
        """Yield actual matched ends or unreadable selected events, in order."""
        events = ((True, start), (False, end)) if rule.order == "start-first" else ((False, end), (True, start))
        for begins, binding in events:
            for payload in samples.get(binding.endpoint.name, ()):
                selected, key = binding.read(payload)
                if selected is False:
                    continue
                if selected is None or now is None or not self.known:
                    self.known = False
                    yield binding.endpoint.name, None
                elif begins:
                    self.pending[key].append((slot, now))
                else:
                    queue = self.pending.get(key)
                    if queue:
                        _start_slot, at = queue.popleft()
                        if not queue:
                            del self.pending[key]
                        yield binding.endpoint.name, now - at


class LatencyMonitor:
    """FIFO within each identity, without a guessed outstanding-request bound.

    Empty keys give ordinary FIFO pairing. Reordered distinct keys and serial
    key reuse preserve the actual issue/end history. Duplicate live identifiers
    are not diagnosed by a coverage monitor; any required uniqueness obligation
    remains the protocol monitor's responsibility.
    """

    def __init__(self, contract, *, artifact_kind):
        self.contract = contract
        self.behavior = artifact_kind == "behavior"
        self.bindings = tuple((rule, *rule.bind(contract)) for rule in contract.latency_goals
                              if rule.applicable(artifact_kind))
        names = {event.endpoint.name for _rule, start, end in self.bindings for event in (start, end)}
        names.update(rule.timebase.source for rule, _start, _end in self.bindings
                     if rule.timebase.kind == "transaction")
        self.endpoints = tuple(endpoint for endpoint in contract.endpoints if endpoint.name in names)
        self.catalogue = CatalogueManifest(tuple(goal for rule, _start, _end in self.bindings
                                                for goal in rule.declarations()))

    def goals(self):
        return self.catalogue

    def goal_providers(self):
        if not self.catalogue:
            return ()
        schema = next(schema for schema in standard_goal_schemas() if schema.name == "latency")
        providers = []
        artifact = "behavior" if self.behavior else "rtl"
        for queryable in (True, False):
            names = [goal.id for rule, _start, _end in self.bindings if rule.queryable(artifact) == queryable
                     for goal in rule.declarations()]
            if not names:
                continue
            part = catalogue_subset(self.catalogue, names)
            capabilities = tuple(CapabilityBinding(kind, POLICY, part) for kind in (DECLARATION, CONCRETE_HIT))
            if queryable:
                capabilities += (
                    CapabilityBinding(LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY if self.behavior
                                      else RTL_LOCAL_GUIDANCE_POLICY, part),
                    CapabilityBinding(EXACT_BOUNDED_HIT, "transaction-latency-history/1", part))
            providers.append(GoalProvider.builtin(GoalProviderIdentity(
                "task-latency" if queryable else "task-latency-observation-only", 1), schema, part, capabilities))
        return tuple(providers)

    def assumptions(self):
        return ()

    def unchecked(self):
        return ()

    def check(self, stimulus, trace, **_kwargs):
        stimulus = stimulus if stimulus is not None else getattr(trace, "stimulus", None)
        histories = {rule.name: _History() for rule, _start, _end in self.bindings}
        hits, unresolved, opportunities = [], [], Counter()
        for slot, record in enumerate(trace.records):
            if dict(record.monitor).get("padding", False):
                break
            samples = transaction_samples(self.endpoints, record, behavior=self.behavior, slot=slot)
            event = None if stimulus is None else stimulus.slots[slot]
            resets = set() if event is None else set(event.resets)
            if event is not None and event.reset:
                resets.update(clock.name for clock in self.contract.clocks)
            for rule, start, end in self.bindings:
                history = histories[rule.name]
                if event is None and rule.reset_domains:
                    history.known = False
                if resets.intersection(rule.reset_domains):
                    history.pending.clear()
                    history.known = True
                now = self._time(rule, history, stimulus, slot, samples)
                for endpoint, elapsed in history.advance(rule, start, end, samples, now, slot):
                    for bin_ in rule.bins:
                        goal = rule.goal_id(bin_.name)
                        if elapsed is None:
                            unresolved.append(Unresolved(goal, slot, endpoint, "unreadable-latency-pairing"))
                        else:
                            opportunities[goal] += 1
                            if bin_.contains(elapsed):
                                hits.append(MonitorHit(goal, slot, endpoint))
        return MonitorReport(hits=hits, opportunities=opportunities, unresolved=unresolved)

    def observe(self, previous, samples):
        """Native source-prefix feedback for the untimed queryable subset."""
        hits = set()
        for rule, start, end in self.bindings:
            if not rule.queryable("behavior"):
                continue
            key = ("latency", rule.name)
            history = _History.from_snapshot(previous.get(key))
            history.counter += len(samples.get(rule.timebase.source, ()))
            for _endpoint, elapsed in history.advance(rule, start, end, samples, history.counter, 0):
                if elapsed is not None:
                    hits.update(rule.goal_id(bin_.name) for bin_ in rule.bins if bin_.contains(elapsed))
            previous[key] = history.snapshot()
        return hits

    def _time(self, rule, history, stimulus, slot, samples):
        basis = rule.timebase
        if basis.kind == "transaction":
            history.counter += len(samples.get(basis.source, ()))
            return history.counter
        if stimulus is None:
            return None
        if basis.kind == "global-time":
            return None if stimulus.clock_schedule is None else stimulus.clock_schedule.slots[slot].time
        if stimulus.clock_schedule is None:
            # Ordinary single-clock replay defines one edge per slot. Multiple
            # domains instead require their actual timestamped global schedule.
            if len(self.contract.clocks) != 1:
                return None
            history.counter += 1
        else:
            history.counter += int(basis.source in stimulus.slots[slot].ticks)
        return history.counter
