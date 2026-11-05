"""Concrete task-observation binding shared by Behavior and RTL.

Uses the comparator's logical output projection. Signal snapshots are never
treated as transfers; Behavior output batches retain their original order.
"""

from collections import Counter

from .catalogue import CatalogueManifest, catalogue_of, catalogue_subset, merge_catalogues
from .compare import observed_in_record
from .contract import Direction
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
from .monitor import MonitorHit, MonitorReport, Unresolved, _merge_reports, monitor_for
from .observation_goal import OBSERVATION_GOAL_POLICY


class TransactionMonitor:
    def __init__(self, contract, *, behavior):
        self.behavior = behavior
        self._groups = {}
        self._goals = tuple(rule.declaration() for rule in contract.observation_goals)
        for rule, goal in zip(contract.observation_goals, self._goals):
            endpoint, predicate = rule.bind(contract.endpoints)
            self._groups.setdefault(endpoint, []).append((rule.consecutive, goal.id, predicate))

    def goals(self):
        return self._goals

    def goal_providers(self):
        catalogue = CatalogueManifest(self._goals)
        providers = []
        for schema in standard_goal_schemas():
            names = [goal.id for goal in self._goals if goal.family == schema.name]
            if names:
                part = catalogue_subset(catalogue, names)
                bindings = [CapabilityBinding(capability, OBSERVATION_GOAL_POLICY, part)
                            for capability in (DECLARATION, CONCRETE_HIT)]
                if self.behavior:
                    bindings.extend((CapabilityBinding(LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY, part),
                                     CapabilityBinding(EXACT_BOUNDED_HIT, "transaction-history/2", part)))
                else:
                    bindings.extend((CapabilityBinding(LOCAL_GUIDANCE, RTL_LOCAL_GUIDANCE_POLICY, part),
                                     CapabilityBinding(EXACT_BOUNDED_HIT, "transaction-history/2", part)))
                providers.append(GoalProvider.builtin(
                    GoalProviderIdentity("task-observation-" + schema.name, 1), schema, part,
                    tuple(bindings)))
        return tuple(providers)

    def assumptions(self):
        return ()

    def unchecked(self):
        return ()

    def observe_payload(self, previous, endpoint, payload):
        """One logical sample; shared by trace and source-query feedback."""
        outcomes = []
        for consecutive, goal, predicate in self._groups.get(endpoint, ()):
            if not consecutive or endpoint in previous:
                outcomes.append((goal, predicate(payload, previous.get(endpoint))))
        previous[endpoint] = payload
        return outcomes

    def hits_for_streams(self, streams):
        """Union over already observed endpoint streams, without another replay."""
        previous, hits = {}, set()
        for endpoint in self._groups:
            for payload in streams.get(endpoint.name, ()):
                hits.update(goal for goal, hit in self.observe_payload(previous, endpoint, payload)
                            if hit is True)
        return hits

    def check(self, stimulus, trace, **_kwargs):
        previous = {}
        hits, unresolved, opportunities = [], [], Counter()
        for slot, record in enumerate(trace.records):
            accepted = dict(record.accepted)
            for endpoint in self._groups:
                if endpoint.direction is Direction.OUT:
                    payloads = observed_in_record(
                        record, endpoint.name, behavior=self.behavior, slot=slot)
                else:
                    payload = accepted.get(endpoint.name)
                    payloads = () if payload is None else (payload,)
                for payload in payloads:
                    for goal, hit in self.observe_payload(previous, endpoint, payload):
                        opportunities[goal] += 1
                        if hit is None:
                            unresolved.append(Unresolved(
                                goal, slot, endpoint.name, "unreadable-transaction-observation"))
                        elif hit:
                            hits.append(MonitorHit(goal, slot, endpoint.name))
        return MonitorReport(hits=hits, opportunities=opportunities, unresolved=unresolved)


class _ExecutionMonitor:
    def __init__(self, monitors):
        self.monitors = monitors
        self._catalogue = merge_catalogues(*(
            catalogue_of(monitor.goals(), allow_legacy=False) for monitor in monitors))

    def goals(self):
        return self._catalogue

    def goal_providers(self):
        return tuple(provider for monitor in self.monitors for provider in monitor.goal_providers())

    def assumptions(self):
        return tuple(item for monitor in self.monitors for item in monitor.assumptions())

    def unchecked(self):
        return tuple(item for monitor in self.monitors for item in monitor.unchecked())

    def check(self, stimulus, trace, **kwargs):
        return _merge_reports(monitor.check(stimulus, trace, **kwargs) for monitor in self.monitors)


class TransactionHistory:
    """Native logical history shared by ordinary and atomic source selection.

    Snapshots are shallow-copyable: payloads, finite states and latency queues
    are immutable values. The symbolic product never supplies this history.
    """

    def __init__(self, contract):
        from .latency_monitor import LatencyMonitor
        from .transaction_monitor import DeclaredTransactionMonitor

        self.scalars = TransactionMonitor(contract, behavior=True)
        self.sequences = DeclaredTransactionMonitor(contract, behavior=True)
        self.latencies = LatencyMonitor(contract, artifact_kind="behavior")

    def observe(self, previous, samples):
        from .finite_goal import _value_key
        from .transaction_monitor import _UNKNOWN

        hits = set()
        for endpoint in self.scalars._groups:
            for payload in samples.get(endpoint.name, ()):
                hits.update(goal for goal, hit in self.scalars.observe_payload(previous, endpoint, payload)
                            if hit is True)
        for spec, classifiers, table in self.sequences.specs:
            key = ("finite-monitor", spec.name)
            known, state = previous.get(key, (True, spec.monitor.initial))
            for _name, event in self.sequences.events_in_record(spec, classifiers, samples):
                if not known or event is _UNKNOWN:
                    known = False
                    continue
                row = table[_value_key(state), _value_key(event)]
                state = row.next_state
                hits.update(spec.goal_id(goal) for goal in row.goals)
            previous[key] = (known, state)
        hits.update(self.latencies.observe(previous, samples))
        return hits


def execution_monitor_for(contract, artifact_kind):
    from .latency_monitor import LatencyMonitor
    from .transaction_monitor import DeclaredTransactionMonitor

    protocol = () if artifact_kind == "behavior" else (monitor_for(contract),)
    observations = ((TransactionMonitor(contract, behavior=artifact_kind == "behavior"),)
                    if contract.observation_goals else ())
    sequences = ((DeclaredTransactionMonitor(contract, behavior=artifact_kind == "behavior"),)
                 if contract.transaction_monitors else ())
    latencies = ((LatencyMonitor(contract, artifact_kind=artifact_kind),)
                 if any(rule.applicable(artifact_kind) for rule in contract.latency_goals) else ())
    monitors = protocol + observations + sequences + latencies
    if not monitors:
        return None
    return monitors[0] if len(monitors) == 1 else _ExecutionMonitor(monitors)
