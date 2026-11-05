"""Finite transaction monitors over concrete or symbolic ordered samples.

Monitor states are a finite Boolean partition in the monitor's declared state
order. The initial partition has one member; total deterministic transitions
preserve it. An unreadable classifier produces the empty partition, which is
absorbing rather than a fabricated default event. No solver is imported here.
"""

from .finite_goal import _value_key


class TransactionMonitorProduct:
    def __init__(self, contract, *, artifact_kind="rtl", goals=None):
        self.endpoints = {endpoint.name: endpoint for endpoint in contract.endpoints}
        self.specs = tuple(spec for spec in contract.transaction_monitors if spec.applicable(artifact_kind)
                           and (goals is None or any(goal.id in goals for goal in spec.declarations())))
        self.goals = tuple(goal for spec in self.specs for goal in spec.declarations())
        self.owners = {spec.goal_id(goal): spec for spec in self.specs for goal in spec.monitor.goals}
        self.bindings = {}
        for spec in self.specs:
            states = {_value_key(value): i for i, value in enumerate(spec.monitor.states)}
            events = {_value_key(value): i for i, value in enumerate(spec.monitor.events)}
            native = spec.bind(contract.endpoints)
            cases = {name: tuple((case, predicate) for case, (_event, predicate) in zip(
                (case for case in spec.events if case.endpoint == name), native[name], strict=True))
                for name in spec.order}
            rows = tuple((states[_value_key(row.state)], events[_value_key(row.event)],
                          states[_value_key(row.next_state)], row.goals)
                         for row in spec.monitor.transitions)
            classifier_events = (events if spec.sampling == "transaction" else
                                 {_value_key(label): i for i, label in enumerate(
                                     sorted({case.event for case in (*spec.events, *spec.schedule_events)}))})
            self.bindings[spec.name] = (states[_value_key(spec.monitor.initial)], classifier_events, cases, rows)

    def initial(self, algebra):
        return {spec.name: tuple(algebra.bool(i == self.bindings[spec.name][0])
                                for i in range(len(spec.monitor.states))) for spec in self.specs}

    def advance(self, state, samples, algebra, *, condition=None, monitor_names=None,
                ticks=None, resets=None, enabled=None):
        """Consume record/endpoint/batch order before aggregating emitted hits.

        The symbolic condition callback returns (readable, value), separately.
        An unreadable earlier priority case must not fall through to the default.
        Concrete classification remains independent of that callback.
        """
        specs = self.specs if monitor_names is None else tuple(
            spec for spec in self.specs if spec.name in monitor_names)
        successor = dict(state)
        hits = {spec.goal_id(goal): algebra.bool(False) for spec in specs for goal in spec.monitor.goals}
        for spec in specs:
            _initial, event_indices, cases, rows = self.bindings[spec.name]
            current = state[spec.name]
            if spec.sampling != "transaction":
                present = [algebra.bool(False) for _ in event_indices]
                entry_readable, active = algebra.bool(True), algebra.bool(False)
                if spec.sampling == "schedule-entry":
                    if ticks is None or resets is None:
                        raise ValueError("schedule-entry queries need the actual clock/reset context")
                    active = algebra.bool(True) if enabled is None else enabled
                    for event in spec.schedule_events:
                        term = (algebra.bool(event.domain in ticks) if event.kind == "clock-edge"
                                else resets.get(event.domain, algebra.bool(False)))
                        index = event_indices[_value_key(event.event)]
                        present[index] = algebra.lor(present[index], term)
            for name in spec.order:
                endpoint = self.endpoints[name]
                for sampled, payload in samples.get(name, ()):
                    if condition is None and not sampled:
                        continue
                    events = [algebra.bool(False) for _ in event_indices]
                    remaining = algebra.bool(True)
                    for case, native in cases[name]:
                        if case.condition is None:
                            readable, value = algebra.bool(True), algebra.bool(True)
                        elif condition is None:
                            result = native(payload, None)
                            readable, value = result is not None, result is True
                        else:
                            readable, value = condition(case.condition, endpoint, payload)
                        eligible = algebra.land(remaining, readable)
                        index = event_indices[_value_key(case.event)]
                        events[index] = algebra.lor(events[index], algebra.land(eligible, value))
                        remaining = algebra.land(eligible, algebra.lnot(value))
                    if spec.sampling != "transaction":
                        active = algebra.lor(active, sampled)
                        entry_readable = algebra.land(entry_readable,
                            algebra.lor(algebra.lnot(sampled), algebra.lor(*events)))
                        present = [algebra.lor(old, algebra.land(sampled, event))
                                   for old, event in zip(present, events, strict=True)]
                    else:
                        current = self._transition(spec, rows, current, events, sampled, algebra, hits)
            if spec.sampling != "transaction":
                events = [algebra.land(entry_readable, *(present[index] if key[1] in event
                                                   else algebra.lnot(present[index])
                                                   for key, index in event_indices.items()))
                          for event in spec.monitor.events]
                current = self._transition(spec, rows, current, events, active, algebra, hits)
            successor[spec.name] = current
        return successor, hits

    @staticmethod
    def _transition(spec, rows, current, events, sampled, algebra, hits):
        next_states = [[] for _ in current]
        for before, event, after, emitted in rows:
            guard = algebra.land(current[before], events[event])
            next_states[after].append(guard)
            for goal in emitted:
                name = spec.goal_id(goal)
                hits[name] = algebra.lor(hits[name], algebra.land(sampled, guard))
        return tuple(algebra.ite(sampled, algebra.lor(*terms), old)
                     for old, terms in zip(current, next_states, strict=True))
