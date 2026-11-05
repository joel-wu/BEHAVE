"""Bounded start/end history; no user-independent in-flight cutoff.

Each potential start in the finite unrolling owns one cell. False, cancelled
and consumed cells are canonical zeroes. A matching end consumes the oldest
live cell with that logical identity. Native replay independently uses deques.
"""

from .latency_monitor import _History
from .observation_goal import GoalCondition, GoalField


class LatencyProduct:
    def __init__(self, contract, *, artifact_kind, goals=None):
        self.rules = tuple(rule for rule in contract.latency_goals if rule.queryable(artifact_kind)
                           and (goals is None or any(goal.id in goals for goal in rule.declarations())))
        self.bindings = {rule.name: rule.bind(contract) for rule in self.rules}
        self.owners = {goal.id: rule for rule in self.rules for goal in rule.declarations()}
        self.goals = tuple(goal for rule in self.rules for goal in rule.declarations())
        self.endpoints = {binding.endpoint.name: binding.endpoint
                          for pair in self.bindings.values() for binding in pair}
        self.endpoints.update((endpoint.name, endpoint) for endpoint in contract.endpoints
                              if any(rule.timebase.kind == "transaction" and rule.timebase.source == endpoint.name
                                     for rule in self.rules))

    def initial(self, algebra):
        return {rule.name: {"known": algebra.bool(True), "counter": algebra.integer(0), "pending": ()}
                for rule in self.rules}

    def prefix(self, rule, snapshot, algebra):
        """Lift a readable audited native prefix, not solver-chosen history."""
        history = _History.from_snapshot(snapshot)
        if not history.known:
            raise ValueError("a latency query needs a readable native prefix")
        cells = sorted((at, key) for key, queue in history.pending.items() for _slot, at in queue)
        return {"known": algebra.bool(True), "counter": algebra.integer(history.counter),
                "pending": tuple((algebra.bool(True), algebra.integer(at), tuple(algebra.literal(value) for value in key))
                    for at, key in cells)}

    @staticmethod
    def _event(event, binding, payload, algebra, condition):
        if condition is None:
            selected, key = binding.read(payload)
            return algebra.bool(selected is not None), algebra.bool(selected is True), key
        readable, selected = ((algebra.bool(True), algebra.bool(True)) if event.condition is None else
                              condition(event.condition, binding.endpoint, payload))
        keys, key_valid = [], []
        for name, index in zip(event.key, binding.indices, strict=True):
            valid, _ = condition(GoalCondition("eq", GoalField(name), GoalField(name)),
                                 binding.endpoint, payload)
            key_valid.append(valid)
            keys.append(payload[index])
        return (algebra.land(readable, algebra.lor(algebra.lnot(selected), algebra.land(*key_valid))),
                selected, tuple(keys))

    def advance(self, state, samples, algebra, *, condition=None, logical=None,
                ticks=(), resets=None, time=None, names=None, enabled=None):
        """Consume one actual entry. Clock/time context is explicit, not inferred."""
        rules = self.rules if names is None else tuple(rule for rule in self.rules if rule.name in names)
        successor, hits = dict(state), {goal.id: algebra.bool(False) for rule in rules for goal in rule.declarations()}
        resets = {} if resets is None else resets
        for rule in rules:
            before = state[rule.name]
            cancel = algebra.lor(*(resets.get(name, algebra.bool(False)) for name in rule.reset_domains))
            if enabled is not None:
                cancel = algebra.land(enabled, cancel)
            known = algebra.lor(cancel, before["known"])
            pending = [self._cell(algebra.land(live, algebra.lnot(cancel)), at, key, algebra)
                       for live, at, key in before["pending"]]
            basis = rule.timebase
            counter = before["counter"]
            if basis.kind == "transaction":
                counter = algebra.add(counter, algebra.count(active for active, _ in samples.get(basis.source, ())))
                now = counter
            elif basis.kind == "clock":
                tick = algebra.integer(int(basis.source in ticks))
                counter = algebra.add(counter, tick if enabled is None else
                                      algebra.ite(enabled, tick, algebra.integer(0)))
                now = counter
            else:
                if time is None:
                    raise ValueError("global-time latency needs the actual timestamped schedule")
                now = algebra.integer(time)
            bindings = self.bindings[rule.name]
            events = ((True, rule.start, bindings[0]), (False, rule.end, bindings[1]))
            if rule.order == "end-first":
                events = events[::-1]
            for begins, event, binding in events:
                for sampled, payload in samples.get(event.endpoint, ()):
                    readable, selected, key = self._event(event, binding, payload, algebra, condition)
                    known = algebra.land(known, algebra.lor(algebra.lnot(sampled), readable))
                    active = algebra.land(sampled, known, selected)
                    # Mathematical integers keep unlike endpoint widths lossless.
                    key = tuple((logical(value) if logical is not None else value)
                                if value is not None else (algebra.bool(False) if kind.kind == "boolean" else algebra.integer(0))
                                for value, kind in zip(key, binding.types, strict=False))
                    if not key and binding.indices:
                        key = tuple(algebra.bool(False) if kind.kind == "boolean" else algebra.integer(0)
                                    for kind in binding.types)
                    if begins:
                        pending.append(self._cell(active, now, key, algebra))
                        continue
                    remaining = active
                    for index, (live, at, start_key) in enumerate(pending):
                        match = algebra.land(remaining, live, *(algebra.eq(a, b)
                                      for a, b in zip(start_key, key, strict=True)))
                        elapsed = algebra.sub(now, at)
                        for bin_ in rule.bins:
                            inside = algebra.lnot(algebra.gt(algebra.integer(bin_.lower), elapsed))
                            if bin_.upper is not None:
                                inside = algebra.land(inside, algebra.lnot(algebra.gt(elapsed, algebra.integer(bin_.upper))))
                            goal = rule.goal_id(bin_.name)
                            hits[goal] = algebra.lor(hits[goal], algebra.land(match, inside))
                        pending[index] = self._cell(algebra.land(live, algebra.lnot(match)), at, start_key, algebra)
                        remaining = algebra.land(remaining, algebra.lnot(match))
            successor[rule.name] = {"known": known, "counter": counter, "pending": tuple(pending)}
        return successor, hits

    @staticmethod
    def _cell(live, at, key, algebra):
        return (live, algebra.ite(live, at, algebra.integer(0)),
                tuple(algebra.ite(live, value, algebra.zero_like(value)) for value in key))

    @staticmethod
    def image(state, read):
        return {name: {"known": read(row["known"]), "counter": read(row["counter"]),
                       "pending": tuple((read(live), read(at), tuple(read(value) for value in key))
                                        for live, at, key in row["pending"])} for name, row in state.items()}
