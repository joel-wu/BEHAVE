"""Task-declared latency observations, separate from response deadlines.

Physical clock/time measurements belong to timed RTL/device execution. A
transaction-count timebase is also meaningful for untimed Behavior. No finite
bin or field width imposes a limit on the number of pending observed starts.
"""

from dataclasses import dataclass

from .catalogue import COVERAGE, Goal
from .observation_goal import GoalCondition, GoalField, TransactionGoal

POLICY = "transaction-latency/1"


@dataclass(frozen=True, slots=True)
class LatencyBin:
    name: str
    lower: int
    upper: int | None = None

    def __post_init__(self):
        if type(self.name) is not str or not self.name:
            raise TypeError("a latency bin has a nonempty name")
        if type(self.lower) is not int or self.lower < 0:
            raise ValueError("a latency bin lower bound is a nonnegative integer")
        if self.upper is not None and (type(self.upper) is not int or self.upper < self.lower):
            raise ValueError("a latency bin upper bound is inclusive and not below its lower bound")

    def contains(self, elapsed):
        return self.lower <= elapsed and (self.upper is None or elapsed <= self.upper)

    def as_dict(self):
        return {"name": self.name, "lower": self.lower, "upper": self.upper}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {"name", "lower", "upper"}:
            raise TypeError("a latency bin image has name, lower and upper")
        return cls(**image)


@dataclass(frozen=True, slots=True)
class LatencyTimebase:
    """Count named clock edges/transactions, or use global schedule timestamps.

    Transaction counts advance by all samples of the named endpoint in a record;
    starts and ends in that record share the resulting timestamp. This is not a
    hardware-cycle latency. Clock counts include reset edges, not control-only
    entries. Global time uses the actual schedule's integer timestamp unit.
    """

    kind: str
    source: str = ""

    def __post_init__(self):
        if self.kind not in ("clock", "transaction", "global-time"):
            raise ValueError("latency timebase is clock, transaction or global-time")
        if type(self.source) is not str or bool(self.source) != (self.kind != "global-time"):
            raise ValueError("clock/transaction timebases name a source; global-time does not")

    def as_dict(self):
        return {"kind": self.kind, "source": self.source}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {"kind", "source"}:
            raise TypeError("a latency timebase image has kind and source")
        return cls(**image)


@dataclass(frozen=True, slots=True)
class LatencyEvent:
    endpoint: str
    key: tuple[str, ...] = ()
    condition: GoalCondition | None = None

    def __post_init__(self):
        if type(self.endpoint) is not str or not self.endpoint:
            raise TypeError("a latency event names a logical endpoint")
        if type(self.key) is not tuple or any(type(name) is not str or not name for name in self.key):
            raise TypeError("a latency key is a tuple of scalar field names")
        if len(set(self.key)) != len(self.key):
            raise ValueError("latency key fields are distinct")
        if self.condition is not None and (type(self.condition) is not GoalCondition
                or any(field.previous for field in self.condition.fields())):
            raise ValueError("a latency event condition reads only its current payload")

    def bind(self, endpoints):
        condition = self.condition or GoalCondition("eq", True, True)
        endpoint, predicate = TransactionGoal("latency-event", self.endpoint, condition).bind(endpoints)
        fields = {field.name: (index, field.type) for index, field in enumerate(endpoint.fields)}
        indices, types, validators = [], [], []
        for name in self.key:
            if name not in fields:
                raise ValueError("a latency key names an undeclared field")
            index, kind = fields[name]
            # Reuse the shared typed-value reader, including unreadable/out-of-range
            # values. This also rejects a non-scalar identity at task admission.
            _, valid = TransactionGoal("latency-key", self.endpoint,
                GoalCondition("eq", GoalField(name), GoalField(name))).bind(endpoints)
            indices.append(index)
            types.append(kind)
            validators.append(valid)
        return _BoundEvent(endpoint, predicate, tuple(indices), tuple(types), tuple(validators))

    def as_dict(self):
        return {"endpoint": self.endpoint, "key": list(self.key),
                "condition": None if self.condition is None else self.condition.as_dict()}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {"endpoint", "key", "condition"} or type(image["key"]) is not list:
            raise TypeError("a latency event image has endpoint, key and condition")
        return cls(image["endpoint"], tuple(image["key"]),
                   None if image["condition"] is None else GoalCondition.from_dict(image["condition"]))


@dataclass(frozen=True, slots=True)
class _BoundEvent:
    endpoint: object
    predicate: object
    indices: tuple
    types: tuple
    validators: tuple

    def read(self, payload):
        active = self.predicate(payload, None)
        if active is not True:
            return active, ()
        if any(valid(payload, None) is not True for valid in self.validators):
            return None, ()
        return True, tuple(payload[index] for index in self.indices)


@dataclass(frozen=True, slots=True)
class LatencyGoal:
    name: str
    start: LatencyEvent
    end: LatencyEvent
    bins: tuple[LatencyBin, ...]
    timebase: LatencyTimebase
    order: str = "start-first"
    reset_domains: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.name) is not str or not self.name:
            raise TypeError("a latency goal has a nonempty name")
        if type(self.start) is not LatencyEvent or type(self.end) is not LatencyEvent:
            raise TypeError("latency starts and ends are LatencyEvent records")
        if type(self.bins) is not tuple or not self.bins or any(type(row) is not LatencyBin for row in self.bins):
            raise TypeError("latency bins are a nonempty tuple of LatencyBin records")
        if len({row.name for row in self.bins}) != len(self.bins):
            raise ValueError("latency bin names are distinct")
        if type(self.timebase) is not LatencyTimebase:
            raise TypeError("a latency goal declares an exact timebase")
        if self.order not in ("start-first", "end-first"):
            raise ValueError("latency simultaneous-event order is start-first or end-first")
        if type(self.reset_domains) is not tuple or any(type(name) is not str or not name for name in self.reset_domains):
            raise TypeError("latency reset cancellation names clock domains")
        if len(set(self.reset_domains)) != len(self.reset_domains):
            raise ValueError("latency reset domains are distinct")

    @property
    def pairing(self):
        return "identifier" if self.start.key else "fifo"

    def applicable(self, artifact_kind):
        # Untimed Behavior has no physical clock latency. Its explicitly declared
        # transaction-count measurements do not acquire a cycle interpretation.
        return artifact_kind != "behavior" or self.timebase.kind == "transaction"

    def queryable(self, artifact_kind):
        # The untimed source-query boundary has transactions, not physical reset
        # entries. Native replay can still observe a declared reset cancellation.
        return self.applicable(artifact_kind) and (artifact_kind != "behavior" or not self.reset_domains)

    def bind(self, contract):
        start, end = self.start.bind(contract.endpoints), self.end.bind(contract.endpoints)
        if tuple(kind.kind for kind in start.types) != tuple(kind.kind for kind in end.types):
            raise ValueError("latency start/end identities must have the same scalar kinds and arity")
        clocks = {clock.name for clock in contract.clocks}
        if not set(self.reset_domains) <= clocks:
            raise ValueError("latency cancellation names an undeclared reset domain")
        if self.timebase.kind == "clock" and self.timebase.source not in clocks:
            raise ValueError("latency timebase names an undeclared clock")
        if self.timebase.kind == "transaction" and self.timebase.source not in {e.name for e in contract.endpoints}:
            raise ValueError("latency timebase names an undeclared transaction endpoint")
        return start, end

    def goal_id(self, bin_name):
        return f"latency:{self.name}:{bin_name}"

    def declarations(self):
        return tuple(Goal(self.goal_id(row.name), COVERAGE, family="latency", provider="task-latency",
                          event="matched-transaction-end", provenance={"latency": self.as_dict(), "bin": row.name},
                          policy=POLICY, evidence="trace") for row in self.bins)

    def as_dict(self):
        return {"name": self.name, "start": self.start.as_dict(), "end": self.end.as_dict(),
                "bins": [row.as_dict() for row in self.bins], "timebase": self.timebase.as_dict(),
                "order": self.order, "reset_domains": list(self.reset_domains)}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {"name", "start", "end", "bins", "timebase", "order", "reset_domains"}:
            raise TypeError("a latency goal image has name, start, end, bins, timebase, order and reset_domains")
        if type(image["bins"]) is not list or type(image["reset_domains"]) is not list:
            raise TypeError("latency bins and reset domains are finite lists")
        return cls(image["name"], LatencyEvent.from_dict(image["start"]), LatencyEvent.from_dict(image["end"]),
                   tuple(LatencyBin.from_dict(row) for row in image["bins"]),
                   LatencyTimebase.from_dict(image["timebase"]), image["order"], tuple(image["reset_domains"]))
