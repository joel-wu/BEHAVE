"""One declaration for predicates on completed, typed source operations.

Frontends own operation sites, evaluation order and value capture. This module
owns only the predicate: it neither re-executes arithmetic nor derives synthetic
overflow / rounding flags. A flag is usable only if the producer declares it.
The same bound declaration is consumed by native observation and exact queries.
"""

from dataclasses import dataclass, field

from .catalogue import COVERAGE, Goal
from .goal_predicate import GoalCondition, bind_predicate

OPERATION_GOAL_POLICY = "operation-predicate/1"
OPERATION_ARITY = {
    "add": 2, "subtract": 2, "multiply": 2, "divide": 2, "floor-divide": 2,
    "remainder": 2, "power": 2, "shift-left": 2, "shift-right": 2,
    "bit-and": 2, "bit-or": 2, "bit-xor": 2,
    "positive": 1, "negative": 1, "bit-not": 1, "logical-not": 1,
}


@dataclass(frozen=True, slots=True)
class OperationValue:
    """A producer's semantic scalar, not a type guessed from one test value.

    Unbounded Python integers have no width. RTL integers carry their effective
    expression width and signedness, including any contextual conversion.
    """

    name: str
    kind: str = "integer"
    width: int | None = None
    signed: bool = True

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("an operation value needs a name")
        if self.kind not in ("integer", "boolean"):
            raise ValueError("operation predicates require integer/Boolean values")
        if self.width is not None and (type(self.width) is not int or self.width < 1):
            raise ValueError("a finite operation value needs a positive width")
        if self.kind == "boolean" and self.width is not None:
            raise ValueError("a semantic Boolean has no integer width")
        if type(self.signed) is not bool:
            raise TypeError("operation signedness is Boolean")

    def as_dict(self):
        return {"name": self.name, "kind": self.kind, "width": self.width, "signed": self.signed}


@dataclass(frozen=True, slots=True)
class OperationGoal:
    name: str
    condition: GoalCondition

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("an operation goal needs a name")
        if type(self.condition) is not GoalCondition:
            raise TypeError("an operation goal needs a GoalCondition")
        if any(value.previous for value in self.condition.fields()):
            raise ValueError("an operation predicate reads one occurrence, not previous observations")

    def as_dict(self):
        return {"name": self.name, "condition": self.condition.as_dict()}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"name", "condition"}:
            raise TypeError("an operation goal image contains name and condition")
        return cls(value["name"], GoalCondition.from_dict(value["condition"]))

    def bind(self, values):
        return BoundOperationGoal(self, tuple(values))


@dataclass(frozen=True, slots=True)
class OperationRule:
    """Instantiate a typed predicate at every matching source operation site.

    ``kinds`` fixes a semantic type pattern for operands followed by result.
    An occurrence of a different type (e.g. list concatenation at a Python +)
    does not hit this rule. This type condition is part of the declared event,
    not a guess from a baseline sample or an assumption added by the solver.
    Positions and concrete RTL widths belong to the frontend, not the caller.
    """

    operator: str
    goal: OperationGoal
    kinds: tuple[str, ...] = ()

    def __post_init__(self):
        if self.operator not in OPERATION_ARITY:
            raise ValueError("unknown operation-rule operator")
        if type(self.goal) is not OperationGoal:
            raise TypeError("an operation rule needs an OperationGoal")
        arity = OPERATION_ARITY[self.operator]
        kinds = tuple(self.kinds) or ("integer",) * arity + (
            "boolean" if self.operator == "logical-not" else "integer",)
        if len(kinds) != arity + 1 or any(kind not in ("integer", "boolean") for kind in kinds):
            raise ValueError("operation kinds specify every operand and the result")
        object.__setattr__(self, "kinds", kinds)
        self.bind()  # Check the declaration once, before artifact execution.

    def bind(self):
        names = tuple(f"operand{i}" for i in range(len(self.kinds) - 1)) + ("result",)
        return self.goal.bind(tuple(OperationValue(name, kind) for name, kind in zip(names, self.kinds)))

    def as_dict(self):
        return {"operator": self.operator, "goal": self.goal.as_dict(), "kinds": list(self.kinds)}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"operator", "goal", "kinds"} or type(value["kinds"]) is not list:
            raise TypeError("an operation rule image contains operator, goal and kinds")
        return cls(value["operator"], OperationGoal.from_dict(value["goal"]), tuple(value["kinds"]))


@dataclass(frozen=True, slots=True)
class BoundOperationGoal:
    rule: OperationGoal
    values: tuple[OperationValue, ...]
    _predicate: object = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        fields = {value.name: (index, value) for index, value in enumerate(self.values)}
        if len(fields) != len(self.values):
            raise ValueError("an operation producer declares each value once")

        def bind_field(term):
            if term.name not in fields:
                raise ValueError(f"operation producer does not declare {term.name!r}")
            index, value = fields[term.name]
            kind = bool if value.kind == "boolean" else int
            if term.bit is not None and (kind is bool or
                    value.width is not None and term.bit >= value.width):
                raise ValueError("operation bit tests require an in-range integer bit")

            def read(payload, previous=None):
                item = payload[index]
                if item is None:  # E.g. an RTL X/Z value: never credit a hit.
                    return None
                if type(item) is not kind:
                    raise TypeError(f"operation value {value.name!r} disagrees with its producer type")
                return item if term.bit is None else bool((item >> term.bit) & 1)

            return bool if term.bit is not None else kind, read

        object.__setattr__(self, "_predicate", bind_predicate(self.rule.condition, bind_field))

    def observe(self, payload):
        """True / false / unreadable, called only after actual completion."""
        return self._predicate(payload)

    def declaration(self, goal_id, *, provider, provenance):
        return Goal(goal_id, COVERAGE, family="operation-event", provider=provider,
                    event="completed-operation", policy=OPERATION_GOAL_POLICY,
                    provenance={"site": provenance, "predicate": self.rule.as_dict(),
                                "values": [value.as_dict() for value in self.values]})

    def symbolic_hit(self, completed, payload):
        # Keep solver imports out of concrete execution.
        from .operation_symbolic import hit_term

        return hit_term(self, completed, payload)
