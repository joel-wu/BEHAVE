"""Bind a total scalar predicate, independently of its sampling point.

The producer resolves each field to a semantic type and a reader accepting
``(current, previous)``. It owns when the sample exists, what unreadable means,
and whether a previous sample is permitted. This module owns only the common
condition grammar; it neither reads a design nor advances observation history.
Symbolic encoders remain independent consumers of that same declaration.
"""

import operator
from dataclasses import dataclass

COMPARISONS = {"eq": operator.eq, "ne": operator.ne, "lt": operator.lt,
               "le": operator.le, "gt": operator.gt, "ge": operator.ge}


@dataclass(frozen=True, slots=True)
class GoalField:
    name: str
    previous: bool = False
    bit: int | None = None

    def __post_init__(self):
        if type(self.name) is not str or not self.name:
            raise TypeError("a goal field names a declared sampled value")
        if type(self.previous) is not bool:
            raise TypeError("previous is an exact Boolean")
        if self.bit is not None and (type(self.bit) is not int or self.bit < 0):
            raise TypeError("a bit observation uses a nonnegative integer index")

    def as_dict(self):
        return {"field": self.name, "previous": self.previous, "bit": self.bit}


@dataclass(frozen=True, slots=True, init=False)
class GoalCondition:
    op: str
    args: tuple

    def __init__(self, op, *args):
        if type(op) is not str or op not in {*COMPARISONS, "and", "or", "not"}:
            raise ValueError("unsupported goal condition operator")
        arity = 2 if op in COMPARISONS else 1 if op == "not" else None
        if (arity is not None and len(args) != arity) or (arity is None and len(args) < 2):
            raise ValueError("goal condition operator has the wrong arity")
        if any(type(arg) not in (GoalField, GoalCondition, int, bool) for arg in args):
            raise TypeError("goal conditions contain fields, conditions and scalar literals")
        object.__setattr__(self, "op", op)
        object.__setattr__(self, "args", args)

    def fields(self):
        for arg in self.args:
            if type(arg) is GoalField:
                yield arg
            elif type(arg) is GoalCondition:
                yield from arg.fields()

    def as_dict(self):
        return {"op": self.op, "args": [
            arg.as_dict() if type(arg) in (GoalField, GoalCondition) else arg
            for arg in self.args]}

    @classmethod
    def from_dict(cls, value):
        def decode(arg):
            if type(arg) in (int, bool):
                return arg
            if type(arg) is dict and set(arg) == {"field", "previous", "bit"}:
                return GoalField(arg["field"], arg["previous"], arg["bit"])
            return cls.from_dict(arg)

        if type(value) is not dict or set(value) != {"op", "args"} \
                or type(value["args"]) is not list:
            raise TypeError("a goal condition image contains exactly op and args")
        return cls(value["op"], *(decode(arg) for arg in value["args"]))


def bind_predicate(condition, bind_field):
    """Compile once; return a reader without Python truthiness or short circuit.

    Every referenced value is read. An unreadable value makes the complete
    predicate unreadable even when another clause would determine its truth.
    Producer errors are not swallowed or converted into missing goal hits.
    """
    def compile_term(term):
        if type(term) in (bool, int):
            return type(term), lambda current, previous=None: term
        if type(term) is GoalField:
            return bind_field(term)
        children = tuple(compile_term(arg) for arg in term.args)
        kinds = tuple(kind for kind, _ in children)
        if term.op in COMPARISONS:
            if kinds[0] is not kinds[1] or (kinds[0] is bool and term.op not in ("eq", "ne")):
                raise TypeError("goal comparisons require matching types; Booleans use eq/ne")
            action = COMPARISONS[term.op]
            left, right = (read for _, read in children)

            def compare(current, previous=None):
                # Read both before testing readability. Do not mask a producer
                # error on the right when the left sample is unreadable.
                first, second = left(current, previous), right(current, previous)
                return None if first is None or second is None else action(first, second)

            return bool, compare
        if any(kind is not bool for kind in kinds):
            raise TypeError("logical goal conditions require Boolean operands")
        if term.op == "not":
            read = children[0][1]

            def negate(current, previous=None):
                value = read(current, previous)
                return None if value is None else not value

            return bool, negate
        action = all if term.op == "and" else any
        readers = tuple(read for _, read in children)

        def evaluate(current, previous=None):
            values = tuple(read(current, previous) for read in readers)
            return None if any(value is None for value in values) else action(values)

        return bool, evaluate

    _kind, predicate = compile_term(condition)
    return predicate
