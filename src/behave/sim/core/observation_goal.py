"""Task predicates over logical transactions, independent of source language.

The domain is the declared endpoint field product, not simulator pin patterns.
Predicates are total Boolean combinations of scalar comparisons and bit tests;
there is no Python callback, implicit sampling clock, or enumerated truth table.
``previous`` denotes the preceding observation of this endpoint in the same run,
including across a DUT reset. A new run starts with no previous observation.
"""

from dataclasses import dataclass

from .catalogue import COVERAGE, Goal
from .goal_predicate import GoalCondition, GoalField, bind_predicate

OBSERVATION_GOAL_POLICY = "transaction-observation/1"
__all__ = ["GoalCondition", "GoalField", "TransactionGoal"]


@dataclass(frozen=True, slots=True)
class TransactionGoal:
    name: str
    endpoint: str
    condition: GoalCondition

    def __post_init__(self):
        if any(type(name) is not str or not name for name in (self.name, self.endpoint)):
            raise TypeError("a transaction goal needs a name and endpoint")
        if type(self.condition) is not GoalCondition:
            raise TypeError("a transaction goal needs an exact GoalCondition")

    @property
    def consecutive(self):
        return any(field.previous for field in self.condition.fields())

    @property
    def family(self):
        return "state-activity" if self.consecutive else "value-observation"

    def as_dict(self):
        return {"name": self.name, "endpoint": self.endpoint,
                "condition": self.condition.as_dict()}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"name", "endpoint", "condition"}:
            raise TypeError("a transaction goal image contains name, endpoint and condition")
        return cls(value["name"], value["endpoint"], GoalCondition.from_dict(value["condition"]))

    def declaration(self):
        return Goal("observation:" + self.name, COVERAGE, family=self.family,
                    provider="task-observation", event="logical-transaction",
                    provenance=self.as_dict(), policy=OBSERVATION_GOAL_POLICY,
                    evidence="trace")

    def bind(self, endpoints):
        """Resolve names/types once; the returned predicate reads payload tuples."""
        endpoint = next((e for e in endpoints if e.name == self.endpoint), None)
        if endpoint is None:
            raise ValueError(f"goal {self.name!r} names an undeclared endpoint {self.endpoint!r}")
        fields = {field.name: (i, field.type) for i, field in enumerate(endpoint.fields)}

        def bind_field(term):
            if term.name not in fields:
                raise ValueError(f"goal {self.name!r} names an undeclared field {term.name!r}")
            index, value_type = fields[term.name]
            if value_type.kind not in ("boolean", "integer"):
                raise ValueError("transaction goal fields currently require scalar Boolean/integer types")
            if term.bit is not None and (value_type.kind != "integer"
                    or term.bit >= value_type.carrier.width):
                raise ValueError("a goal bit index must lie in the declared integer width")
            kind = bool if value_type.kind == "boolean" or term.bit is not None else int
            boolean = value_type.kind == "boolean"
            low, high = (0, 1) if boolean else value_type.carrier.bounds

            def read(current, previous):
                payload = previous if term.previous else current
                if payload is None or index >= len(payload):
                    return None
                value = payload[index]
                valid = (type(value) is bool if boolean
                         else type(value) is int and low <= value <= high)
                if not valid:
                    return None
                return value if term.bit is None else bool((value >> term.bit) & 1)

            return kind, read

        return endpoint, bind_predicate(self.condition, bind_field)
