"""Task-declared finite monitors over transactions and execution-entry events.

Each endpoint has an ordered classifier with an explicit unconditional final
case. The task also declares endpoint order within a record; batch order and
record order are never reconstructed from an aggregate set of goal hits.
Timed schedule-entry sampling also consumes real clock/reset events and empty
entries. The finite monitor and transaction classifier remain the same product.
"""

from collections import Counter
from dataclasses import dataclass

from .catalogue import COVERAGE, SAFETY, CatalogueManifest, Goal, catalogue_subset
from .compare import transaction_samples
from .finite_goal import FiniteMonitor, _value_from_image, _value_image, _value_key
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
from .monitor import MonitorHit, MonitorReport, Unresolved, Violation
from .observation_goal import GoalCondition, TransactionGoal

POLICY = "transaction-finite-monitor/2"
SCHEDULE_POLICY = "schedule-entry-monitor/1"
_UNKNOWN = object()


@dataclass(frozen=True, slots=True)
class ScheduleEvent:
    """A named clock edge or active reset at an actual execution entry.

    ``reset-active`` denotes the asserted reset level for that domain at this
    entry, not a fabricated rising edge. Simultaneous labels form one set.
    Empty sets are real observations too in schedule-entry sampling.
    """

    event: str
    kind: str
    domain: str

    def __post_init__(self):
        if type(self.event) is not str or not self.event:
            raise TypeError("a schedule event has a nonempty string label")
        if self.kind not in ("clock-edge", "reset-active"):
            raise ValueError("a schedule event observes clock-edge or reset-active")
        if type(self.domain) is not str or not self.domain:
            raise TypeError("a schedule event names a clock domain")

    def as_dict(self):
        return {"event": self.event, "kind": self.kind, "domain": self.domain}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {"event", "kind", "domain"}:
            raise TypeError("a schedule event image has event, kind and domain")
        return cls(**image)


@dataclass(frozen=True, slots=True)
class TransactionEvent:
    """A classifier case; ``condition=None`` is the explicit final default."""

    event: object
    endpoint: str
    condition: GoalCondition | None = None

    def __post_init__(self):
        _value_key(self.event)
        if type(self.endpoint) is not str or not self.endpoint:
            raise TypeError("a transaction event names an endpoint")
        if self.condition is not None:
            if type(self.condition) is not GoalCondition:
                raise TypeError("an event condition is a GoalCondition or the default None")
            if any(field.previous for field in self.condition.fields()):
                raise ValueError("event classifiers read the current sample; finite monitor state owns history")

    def as_dict(self):
        return {"event": _value_image(self.event), "endpoint": self.endpoint,
                "condition": None if self.condition is None else self.condition.as_dict()}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {"event", "endpoint", "condition"}:
            raise TypeError("a transaction event image has event, endpoint and condition")
        return cls(_value_from_image(image["event"]), image["endpoint"],
                   None if image["condition"] is None else GoalCondition.from_dict(image["condition"]))


@dataclass(frozen=True, slots=True, init=False)
class TransactionMonitorSpec:
    """One finite observer. State resets at run start, not on DUT reset.

    ``order`` explicitly orders endpoints sampled in the same execution record.
    Within an endpoint, the first matching case selects exactly one event.
    ``sampling='transaction'`` consumes that order, one transaction at a time.
    ``sampling='entry-set'`` instead consumes the complete set of classified
    labels in one transaction-bearing execution record. Repeated labels coalesce;
    empty records do not advance it. This is a logical-entry observation, not a
    clock/time monitor, and does not impose timing on untimed Behavior.
    ``sampling='schedule-entry'`` observes every real RTL execution entry,
    including empty label sets, with optional declared clock/reset labels.
    It is not applicable to untimed Behavior.
    ``violations`` selects safety outputs; other outputs are coverage.
    """

    name: str
    monitor: FiniteMonitor
    events: tuple[TransactionEvent, ...]
    order: tuple[str, ...]
    violations: tuple[str, ...]
    sampling: str
    schedule_events: tuple[ScheduleEvent, ...]

    def __init__(self, name, monitor, events, *, order, violations=(), sampling="transaction",
                 schedule_events=()):
        if type(name) is not str or not name or type(monitor) is not FiniteMonitor:
            raise TypeError("a transaction monitor needs a name and an exact FiniteMonitor")
        if type(events) not in (tuple, list) \
                or any(type(event) is not TransactionEvent for event in events):
            raise TypeError("transaction monitor events are a sequence of TransactionEvent records")
        if type(schedule_events) not in (tuple, list) or any(
                type(event) is not ScheduleEvent for event in schedule_events):
            raise TypeError("schedule_events is a finite sequence of ScheduleEvent records")
        if not events and not schedule_events:
            raise ValueError("a monitor observes at least one declared event")
        if type(order) not in (tuple, list) or any(type(name) is not str for name in order):
            raise TypeError("transaction monitor order is an endpoint-name sequence")
        if len(set(order)) != len(order) or set(order) != {event.endpoint for event in events}:
            raise ValueError("monitor order must name each observed endpoint exactly once")
        if sampling not in ("transaction", "entry-set", "schedule-entry"):
            raise ValueError("monitor sampling is transaction, entry-set or schedule-entry")
        if schedule_events and sampling != "schedule-entry":
            raise ValueError("clock/reset events require schedule-entry sampling")
        if sampling == "transaction":
            event_keys = {_value_key(event) for event in monitor.events}
            if any(_value_key(event.event) not in event_keys for event in events):
                raise ValueError("an event classifier names a value outside the monitor alphabet")
        else:
            labels = {event.event for event in (*events, *schedule_events)}
            if any(type(label) is not str or not label for label in labels):
                raise TypeError("entry-set classifiers emit nonempty string labels")
            # Validate the supplied finite alphabet, without constructing another
            # power set. All possible sets remain declared, including
            # ones which a particular artifact can never produce.
            for event in monitor.events:
                if (type(event) is not tuple or (not event and sampling == "entry-set")
                        or any(type(label) is not str for label in event)
                        or tuple(sorted(set(event))) != event or not set(event) <= labels):
                    raise ValueError("entry-set events are sorted sets of declared classifier labels")
            if len(monitor.events) != (1 << len(labels)) - int(sampling == "entry-set"):
                raise ValueError("entry-set alphabet includes all label sets; schedule-entry includes the empty set")
        for endpoint in order:
            cases = tuple(event for event in events if event.endpoint == endpoint)
            if cases[-1].condition is not None or any(case.condition is None for case in cases[:-1]):
                raise ValueError("each endpoint classifier ends with exactly one default case")
        if type(violations) not in (tuple, list) or any(type(goal) is not str for goal in violations):
            raise TypeError("violation outputs are a finite sequence of monitor goal names")
        if len(set(violations)) != len(violations) or not set(violations) <= set(monitor.goals):
            raise ValueError("violation outputs must be distinct declared monitor goals")
        for key, value in (("name", name), ("monitor", monitor), ("events", tuple(events)),
                           ("order", tuple(order)), ("violations", tuple(sorted(violations))),
                           ("sampling", sampling), ("schedule_events", tuple(schedule_events))):
            object.__setattr__(self, key, value)

    def applicable(self, artifact_kind):
        return artifact_kind != "behavior" or self.sampling != "schedule-entry"

    @property
    def policy(self):
        return SCHEDULE_POLICY if self.sampling == "schedule-entry" else POLICY

    def bind_schedule(self, clocks):
        domains = {clock.name for clock in clocks}
        if self.sampling == "schedule-entry" and not domains:
            raise ValueError("physical schedule-entry sampling requires a declared clock domain")
        if any(event.domain not in domains for event in self.schedule_events):
            raise ValueError("a schedule event names an undeclared clock domain")

    def goal_id(self, goal):
        return f"monitor:{self.name}:{goal}"

    def declarations(self):
        coverage = "sequence" if self.sampling == "transaction" else "schedule-interleaving"
        return tuple(Goal(self.goal_id(goal), SAFETY if goal in self.violations else COVERAGE,
            family="monitor-obligation" if goal in self.violations else coverage,
            provider="task-transaction-monitor", event={"transaction": "logical-transaction",
                "entry-set": "logical-entry-event-set", "schedule-entry": "schedule-entry-event-set"}[self.sampling],
            provenance={"monitor": self.as_dict(), "output": goal}, policy=self.policy, evidence="trace")
            for goal in self.monitor.goals)

    def bind(self, endpoints):
        """Bind classifiers once, preserving declared priority and field types."""
        known = {endpoint.name: endpoint for endpoint in endpoints}
        if not set(self.order) <= known.keys():
            raise ValueError("a transaction monitor names an undeclared endpoint")
        return {name: tuple((case.event, None if case.condition is None else
                            TransactionGoal(self.name, name, case.condition).bind(endpoints)[1])
                           for case in self.events if case.endpoint == name)
                for name in self.order}

    def as_dict(self):
        image = {"name": self.name, "monitor": self.monitor.as_dict(),
                "events": [event.as_dict() for event in self.events],
                "order": list(self.order), "violations": list(self.violations)}
        if self.sampling != "transaction":
            image["sampling"] = self.sampling
        if self.schedule_events:
            image["schedule_events"] = [event.as_dict() for event in self.schedule_events]
        return image

    @classmethod
    def from_dict(cls, image):
        fields = {"name", "monitor", "events", "order", "violations"}
        if type(image) is not dict or not fields <= set(image) \
                or set(image) - fields - {"sampling", "schedule_events"}:
            raise TypeError("a transaction monitor image has name, monitor, events, order and violations")
        if type(image["events"]) is not list:
            raise TypeError("transaction event images are a list")
        if "schedule_events" in image and type(image["schedule_events"]) is not list:
            raise TypeError("schedule event images are a list")
        return cls(image["name"], FiniteMonitor.from_dict(image["monitor"]),
                   [TransactionEvent.from_dict(event) for event in image["events"]],
                   order=image["order"], violations=image["violations"],
                   sampling=image.get("sampling", "transaction"),
                   schedule_events=[ScheduleEvent.from_dict(event)
                                    for event in image.get("schedule_events", ())])


class DeclaredTransactionMonitor:
    """Concrete record consumer shared by B and RTL; no pin snapshots."""

    def __init__(self, contract, *, behavior):
        self.contract = contract
        self.behavior = behavior
        self.specs = tuple((spec, spec.bind(contract.endpoints), {
            (_value_key(row.state), _value_key(row.event)): row for row in spec.monitor.transitions})
            for spec in contract.transaction_monitors if spec.applicable("behavior" if behavior else "rtl"))
        self.has_schedule_events = any(spec.sampling == "schedule-entry" for spec, _cases, _table in self.specs)
        observed = {name for spec, _cases, _table in self.specs for name in spec.order}
        self.endpoints = {endpoint.name: endpoint for endpoint in contract.endpoints if endpoint.name in observed}
        self.catalogue = CatalogueManifest(tuple(goal for spec, _cases, _table in self.specs
                                                for goal in spec.declarations()))

    def goals(self):
        return self.catalogue

    def goal_providers(self):
        providers = []
        policy = SCHEDULE_POLICY if self.has_schedule_events else POLICY
        for schema in standard_goal_schemas():
            names = [goal.id for goal in self.catalogue.values() if goal.family == schema.name]
            if names:
                part = catalogue_subset(self.catalogue, names)
                providers.append(GoalProvider.builtin(
                    GoalProviderIdentity("task-transaction-" + schema.name, 1), schema, part,
                    tuple(CapabilityBinding(capability, policy, part)
                          for capability in (DECLARATION, CONCRETE_HIT)) + (
                        CapabilityBinding(LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY if self.behavior
                                          else RTL_LOCAL_GUIDANCE_POLICY, part),
                        CapabilityBinding(EXACT_BOUNDED_HIT, "transaction-history/3", part))))
        return tuple(providers)

    def assumptions(self):
        return ()

    def unchecked(self):
        return ()

    @staticmethod
    def classify(cases, payload):
        for event, predicate in cases:
            selected = True if predicate is None else predicate(payload, None)
            if selected is None:
                return _UNKNOWN
            if selected:
                return event
        raise AssertionError("a bound classifier has an unconditional final case")

    @classmethod
    def events_in_record(cls, spec, classifiers, samples, *, ticks=None, resets=None):
        """Native event grouping, shared with the independent source audit.

        An unreadable member makes the whole set unknown; it is not omitted or
        replaced by a default. Endpoint order is meaningful only in transaction
        sampling. The set mode never serializes simultaneous events.
        """
        labels = set()
        unknown = False
        if spec.sampling == "schedule-entry":
            if ticks is None or resets is None:
                unknown = True
            else:
                labels.update(event.event for event in spec.schedule_events
                              if event.domain in (ticks if event.kind == "clock-edge" else resets))
        for name in spec.order:
            for payload in samples.get(name, ()):
                event = cls.classify(classifiers[name], payload)
                if spec.sampling == "transaction":
                    yield name, event
                elif event is _UNKNOWN:
                    unknown = True
                else:
                    labels.add(event)
        if spec.sampling != "transaction" and (labels or unknown or spec.sampling == "schedule-entry"):
            yield "", _UNKNOWN if unknown else tuple(sorted(labels))

    def new_run(self):
        return _MonitorRun(self)

    def check(self, stimulus, trace, **_kwargs):
        from .stimulus import uses_global_events

        stimulus = stimulus if stimulus is not None else getattr(trace, "stimulus", None)
        explicit = stimulus is not None and uses_global_events(self.contract, stimulus)
        run = self.new_run()
        for slot, record in enumerate(trace.records):
            if dict(record.monitor).get("padding", False):
                break
            ticks = resets = None
            if self.has_schedule_events and stimulus is not None:
                event = stimulus.slots[slot]
                ticks = event.ticks if explicit else tuple(clock.name for clock in self.contract.clocks)
                resets = event.resets if explicit else ticks if event.reset else ()
            run.observe(record, slot, ticks=ticks, resets=resets)
        return run.report()


class _MonitorRun:
    """One mutable accumulator; immutable reports are constructed only at exit."""

    def __init__(self, binding):
        self.binding = binding
        self.states = {spec.name: spec.monitor.initial for spec, _cases, _table in binding.specs}
        self.hits, self.violations, self.unresolved = [], [], []
        self.opportunities = Counter()

    def observe(self, record, slot, *, outputs=None, ticks=None, resets=None):
        samples = transaction_samples(self.binding.endpoints.values(), record,
            behavior=self.binding.behavior, slot=slot, outputs=outputs)
        for spec, classifiers, table in self.binding.specs:
            state = self.states[spec.name]
            for name, event in self.binding.events_in_record(spec, classifiers, samples, ticks=ticks, resets=resets):
                for goal in spec.monitor.goals:
                    self.opportunities[spec.goal_id(goal)] += 1
                if state is _UNKNOWN or event is _UNKNOWN:
                    state = _UNKNOWN
                    self.unresolved.extend(Unresolved(spec.goal_id(goal), slot, name,
                        "unreadable-schedule-monitor-history" if spec.sampling == "schedule-entry"
                        else "unreadable-transaction-monitor-history") for goal in spec.monitor.goals)
                    continue
                step = table[_value_key(state), _value_key(event)]
                state = step.next_state
                for goal in step.goals:
                    identity = spec.goal_id(goal)
                    if goal in spec.violations:
                        self.violations.append(Violation(identity, slot, name, "transaction-monitor-violation"))
                    else:
                        self.hits.append(MonitorHit(identity, slot, name))
            self.states[spec.name] = state

    def report(self):
        return MonitorReport(hits=self.hits, violations=self.violations,
                             opportunities=self.opportunities, unresolved=self.unresolved)
