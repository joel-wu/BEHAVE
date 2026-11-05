"""Finite source-goal declarations shared by both frontend transports.

Read rules select the exact spelling of an evaluated scalar read, never a later
signal snapshot. Frontends bind occurrences and types before execution. A rule
matching no source occurrence does not invent one. Operations and reads share
transport ownership but retain distinct events and catalogue schemas.
"""

from dataclasses import dataclass, field

from .goal_predicate import GoalCondition, bind_predicate
from .operation_goal import OperationRule, OperationValue

SOURCE_READ_POLICY = "source-read-predicate/1"


@dataclass(frozen=True, slots=True)
class SourceReadRule:
    symbol: str
    name: str
    condition: GoalCondition
    kind: str = "integer"

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.symbol, self.name)):
            raise TypeError("a source read rule names a source expression and a goal")
        if type(self.condition) is not GoalCondition:
            raise TypeError("a source read rule needs a GoalCondition")
        if self.kind not in ("integer", "boolean"):
            raise ValueError("source read predicates require scalar integer/Boolean values")
        if any(value.previous for value in self.condition.fields()):
            raise ValueError("source read history needs an explicit observer-state binding")
        self.bind()

    def bind(self, value=None):
        return BoundReadPredicate(self, OperationValue("value", self.kind) if value is None else value)

    def as_dict(self):
        return {"symbol": self.symbol, "name": self.name,
                "condition": self.condition.as_dict(), "kind": self.kind}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"symbol", "name", "condition", "kind"}:
            raise TypeError("a source read rule contains symbol, name, condition and kind")
        return cls(value["symbol"], value["name"], GoalCondition.from_dict(value["condition"]), value["kind"])


@dataclass(frozen=True, slots=True)
class BoundReadPredicate:
    rule: SourceReadRule
    value: OperationValue
    _predicate: object = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        value = self.value
        if value.name != "value" or value.kind != self.rule.kind:
            raise ValueError("a read producer supplies its one declared semantic value")

        def field(term):
            if term.name != "value":
                raise ValueError("a source read predicate names only its sampled value")
            kind = bool if value.kind == "boolean" else int
            if term.bit is not None and (kind is bool or
                    value.width is not None and term.bit >= value.width):
                raise ValueError("source read bit tests require an in-range integer bit")

            def read(payload, previous=None):
                item = payload[0]
                if item is None:
                    return None
                if type(item) is not kind:
                    raise TypeError("a sampled value disagrees with its producer type")
                return item if term.bit is None else bool((item >> term.bit) & 1)

            return bool if term.bit is not None else kind, read

        object.__setattr__(self, "_predicate", bind_predicate(self.rule.condition, field))

    @property
    def values(self):
        return (self.value,)

    def observe(self, payload):
        return self._predicate(payload)


@dataclass(frozen=True, slots=True)
class SourceGoalRules:
    operations: tuple[OperationRule, ...] = ()
    reads: tuple[SourceReadRule, ...] = ()

    def __post_init__(self):
        for name, kind, key in (
                ("operations", OperationRule, lambda rule: (rule.operator, rule.goal.name)),
                ("reads", SourceReadRule, lambda rule: (rule.symbol, rule.name))):
            values = getattr(self, name)
            if type(values) not in (list, tuple) or any(type(value) is not kind for value in values):
                raise TypeError(f"source {name} is a finite list of {kind.__name__} records")
            ordered = tuple(sorted(values, key=key))
            if len({key(value) for value in ordered}) != len(ordered):
                raise ValueError(f"source {name} names must be distinct at each selector")
            object.__setattr__(self, name, ordered)

    def __bool__(self):
        return bool(self.operations or self.reads)

    def as_dict(self):
        return {"operations": [rule.as_dict() for rule in self.operations],
                "reads": [rule.as_dict() for rule in self.reads]}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"operations", "reads"} or any(
                type(value[key]) is not list for key in ("operations", "reads")):
            raise TypeError("source goal rules contain finite operations and reads lists")
        return cls(tuple(OperationRule.from_dict(item) for item in value["operations"]),
                   tuple(SourceReadRule.from_dict(item) for item in value["reads"]))


EMPTY_SOURCE_RULES = SourceGoalRules()
