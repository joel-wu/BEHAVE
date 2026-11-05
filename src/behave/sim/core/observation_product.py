"""Finite transaction history shared by concrete and symbolic task products.

Only fields referenced by declared predicates are retained. Each endpoint owns
its previous sample; silence and DUT reset do not erase it. A fresh run does.
The caller supplies actual accepted/emitted samples, not pin snapshots.
"""

from .compare import observed_in_record
from .contract import Direction
from .latency_product import LatencyProduct
from .observation_goal import GoalField
from .transaction_product import TransactionMonitorProduct


def _fields(term):
    if type(term) is GoalField:
        return {term.name}
    if type(term) in (bool, int):
        return set()
    return set().union(*(_fields(arg) for arg in term.args))


class TransactionProduct:
    def __init__(self, contract, *, artifact_kind="rtl", goals=None):
        # These products only observe execution; they do not restrict its
        # domain. Retain entire automata for selected goals, including their
        # predecessor states, rather than cutting individual transitions.
        goals = None if goals is None else frozenset(goals)
        rules = tuple(rule for rule in contract.observation_goals
                      if goals is None or rule.declaration().id in goals)
        self.groups = {}
        self.monitors = TransactionMonitorProduct(contract, artifact_kind=artifact_kind, goals=goals)
        self.latency = LatencyProduct(contract, artifact_kind=artifact_kind, goals=goals)
        self.goals = (tuple(rule.declaration() for rule in rules)
                      + self.monitors.goals + self.latency.goals)
        for rule in rules:
            endpoint, predicate = rule.bind(contract.endpoints)
            self.groups.setdefault(endpoint, []).append((rule, "observation:" + rule.name, predicate))
        self.fields = {}
        for endpoint, rules in self.groups.items():
            used = set().union(*(_fields(rule.condition) for rule, _, _ in rules))
            self.fields[endpoint] = tuple((index, field) for index, field in enumerate(endpoint.fields)
                                          if field.name in used)
        # Payload projection includes monitor-only endpoints, but previous-value
        # storage remains limited to scalar/consecutive predicates that use it.
        for spec in self.monitors.specs:
            for name in spec.order:
                self.groups.setdefault(self.monitors.endpoints[name], [])
        for endpoint in self.latency.endpoints.values():
            self.groups.setdefault(endpoint, [])

    def initial(self, algebra, *, zero=None):
        state = {"observations": {endpoint.name: {
            "seen": algebra.bool(False),
            "payload": tuple(zero(field.type) if zero is not None else
                             algebra.bool(False) if field.type.kind == "boolean" else
                             algebra.integer(0) for _, field in fields),
        } for endpoint, fields in self.fields.items()}, "automata": self.monitors.initial(algebra)}
        if self.latency.rules:
            state["latency"] = self.latency.initial(algebra)
        return state

    @staticmethod
    def image(state, read=lambda value: value):
        image = {"observations": {name: {"seen": read(previous["seen"]),
                       "payload": tuple(read(value) for value in previous["payload"])}
                for name, previous in state["observations"].items()},
                "automata": {name: tuple(read(value) for value in partition)
                             for name, partition in state["automata"].items()}}
        if "latency" in state:
            image["latency"] = LatencyProduct.image(state["latency"], read)
        return image

    def advance(self, state, samples, algebra, *, predicate=None, condition=None,
                logical=None, ticks=(), resets=None, time=None, enabled=None):
        """Consume ordered ``(sampled, logical payload)`` pairs per endpoint.

        ``sampled`` can be symbolic. A false transfer leaves all history intact.
        The symbolic encoder supplies the same typed predicate over its scalars;
        the concrete side uses the declaration's independent native evaluator.
        ``enabled`` denotes reaching the observation boundary, not merely an
        absent transfer. A cut-off source prefix must not tick time or reset
        history monitors. Omission preserves ordinary completed-record replay.
        """
        if enabled is not None:
            samples = {name: tuple((algebra.land(enabled, sampled), payload) for sampled, payload in rows)
                       for name, rows in samples.items()}
        successor, hits = dict(state["observations"]), {}
        for endpoint, rules in self.groups.items():
            if not rules:
                continue
            fields = self.fields[endpoint]
            previous = state["observations"][endpoint.name]
            for _, goal, _ in rules:
                hits[goal] = algebra.bool(False)
            for sampled, payload in samples.get(endpoint.name, ()):
                prior = [None] * len(endpoint.fields)
                for (index, _), value in zip(fields, previous["payload"]):
                    prior[index] = value
                for rule, goal, native in rules:
                    value = (native(payload, prior) if predicate is None else
                             predicate(rule, endpoint, payload, prior))
                    if value is None:
                        value = algebra.bool(False)
                    reached = algebra.land(sampled, value,
                        previous["seen"] if rule.consecutive else algebra.bool(True))
                    hits[goal] = algebra.lor(hits[goal], reached)
                previous = {
                    "seen": algebra.lor(previous["seen"], sampled),
                    "payload": tuple(algebra.ite(sampled, payload[index], old)
                                     for (index, _), old in zip(fields, previous["payload"])),
                }
            successor[endpoint.name] = previous
        automata, monitor_hits = self.monitors.advance(state["automata"], samples, algebra,
            condition=condition, ticks=ticks, resets=resets, enabled=enabled)
        successor = {"observations": successor, "automata": automata}
        hits.update(monitor_hits)
        if self.latency.rules:
            successor["latency"], latency_hits = self.latency.advance(state["latency"], samples, algebra,
                condition=condition, logical=logical, ticks=ticks, resets=resets, time=time, enabled=enabled)
            hits.update(latency_hits)
        return successor, hits

    def record_samples(self, record):
        accepted = dict(record.accepted)
        samples = {}
        for endpoint in self.groups:
            if endpoint.direction is Direction.IN:
                payload = accepted.get(endpoint.name)
                payloads = () if payload is None else (payload,)
            else:
                payloads = observed_in_record(record, endpoint.name, behavior=False)
            samples[endpoint.name] = tuple((True, payload) for payload in payloads)
            if self.latency.rules and not payloads:
                # RTL task products have one potential endpoint transfer per
                # slot. Retain the false cell as well for exact boundary pins.
                samples[endpoint.name] = ((False, tuple(False if field.type.kind == "boolean" else 0
                                                        for field in endpoint.fields)),)
        return samples
