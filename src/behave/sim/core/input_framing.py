"""Task-declared nested framing, advanced only by accepted input records.

Levels are outermost first. Each contains 1..max_count children; the innermost
children are payload records. A finite run may be a legal prefix. Reset cancels
the partial frame. This is an input precondition, never a candidate safety goal.
Concrete execution and symbolic products use the same counter transition.
"""

from dataclasses import dataclass

from .ready_valid_product import ConcreteAlgebra
from .refusal import Refusal, Refused


@dataclass(frozen=True, slots=True)
class FrameLevel:
    start: str
    end: str
    max_count: int

    def __post_init__(self):
        if any(type(name) is not str or not name for name in (self.start, self.end)):
            raise TypeError("frame markers are nonempty field names")
        if type(self.max_count) is not int or self.max_count < 1:
            raise ValueError("a frame level needs a positive maximum child count")

    def as_dict(self):
        return {"start": self.start, "end": self.end, "max_count": self.max_count}


@dataclass(frozen=True, slots=True)
class InputFraming:
    levels: tuple[FrameLevel, ...]

    def __post_init__(self):
        object.__setattr__(self, "levels", tuple(self.levels))
        if not self.levels or any(type(level) is not FrameLevel for level in self.levels):
            raise TypeError("input framing needs outer-to-inner FrameLevel records")
        fields = self.fields
        if len(set(fields)) != len(fields):
            raise ValueError("each frame marker names a different payload field")

    @property
    def fields(self):
        return tuple(name for level in self.levels for name in (level.start, level.end))

    @property
    def initial(self):
        return (0,) * len(self.levels)

    def as_dict(self):
        return {"version": "input-framing/1", "levels": [level.as_dict() for level in self.levels]}

    @classmethod
    def from_dict(cls, image):
        if (type(image) is not dict or set(image) != {"version", "levels"}
                or image["version"] != "input-framing/1"):
            raise ValueError("unsupported input framing declaration")
        return cls(tuple(FrameLevel(**level) for level in image["levels"]))

    def step(self, state, values, algebra=ConcreteAlgebra):
        """Return (successor counters, legal); no independent symbolic rule."""
        op = algebra
        ends = tuple(op.eq(values[level.end], 1) for level in self.levels)
        legal, after = [], []
        for index, level in enumerate(self.levels):
            start = op.land(*(op.eq(count, 0) for count in state[index:]))
            legal.append(op.eq(op.eq(values[level.start], 1), start))
            child_end = True if index + 1 == len(state) else ends[index + 1]
            if index + 1 < len(state):
                legal.append(op.lor(op.lnot(ends[index]), child_end))
            count = op.ite(child_end, state[index] + 1, state[index])
            # A full level must close on this record, not start an extra child.
            legal.append(op.lor(op.lnot(child_end), op.lnot(op.gt(count, level.max_count))))
            legal.append(op.lor(op.lnot(op.eq(count, level.max_count)), ends[index]))
            closing = op.lor(*ends[:index + 1])
            after.append(op.ite(closing, 0, count))
        return tuple(after), op.land(*legal)

    def shape(self, rows, fields):
        """Keep pattern data and end choices; make the declared markers legal.

        Baseline members finish a frame, so concatenating whole members remains
        legal. A maximum forces closure; no name-based protocol inference occurs.
        """
        indexes = {name: index for index, name in enumerate(fields)}
        state, result = self.initial, []
        for position, original in enumerate(rows):
            row = list(original)
            ends = [bool(row[indexes[level.end]]) for level in self.levels]
            # Outer ends imply every enclosed end. Propagate forced capacity ends
            # outward, then close their children inward.
            for index in reversed(range(len(self.levels))):
                child_end = index + 1 == len(ends) or ends[index + 1]
                ends[index] |= child_end and state[index] + 1 == self.levels[index].max_count
            for index in range(len(ends)):
                ends[index] |= position + 1 == len(rows) or any(ends[:index])
                level = self.levels[index]
                row[indexes[level.start]] = int(all(count == 0 for count in state[index:]))
                row[indexes[level.end]] = int(ends[index])
            values = {name: row[index] for name, index in indexes.items()}
            state, legal = self.step(state, values)
            if not legal:
                raise AssertionError("framing constructor disagrees with its transition")
            result.append(tuple(row))
        return tuple(result)


class InputDomainCursor:
    """One run's input-precondition state, shared by B/B and B/R replay."""

    def __init__(self, contract):
        self.endpoints = tuple(e for e in contract.inputs() if e.framing is not None)
        self.states = {e.name: e.framing.initial for e in self.endpoints}

    def advance(self, slot, accepted, *, index):
        if not self.endpoints:
            return
        after = dict(self.states)
        for endpoint in self.endpoints:
            if slot.reset or endpoint.clock in slot.resets:
                after[endpoint.name] = endpoint.framing.initial
            elif accepted.get(endpoint.name) is not None:
                payload = accepted[endpoint.name]
                state, legal = endpoint.framing.step(
                    after[endpoint.name], dict(zip((f.name for f in endpoint.fields), payload, strict=True)))
                if not legal:
                    raise Refused(Refusal(
                        "input-framing-violated", "accepted input violates the task's framing precondition",
                        endpoint=endpoint.name, slot=index, state=list(after[endpoint.name])))
                after[endpoint.name] = state
        self.states = after


def check_input_trace(contract, stimulus, records):
    cursor = InputDomainCursor(contract)
    if cursor.endpoints:
        for index, (slot, record) in enumerate(zip(stimulus.slots, records, strict=True)):
            cursor.advance(slot, dict(record.accepted), index=index)


def symbolic_initial(framing):
    import z3

    return tuple(z3.BitVecVal(0, level.max_count.bit_length() + 2)
                 for level in framing.levels)


def symbolic_step(framing, state, values, *, reset=False, accepted=True):
    """QF_BV counter product; no unconstrained auxiliary state or int encoding."""
    import z3

    class Algebra(ConcreteAlgebra):
        land = staticmethod(z3.And)
        lor = staticmethod(z3.Or)
        lnot = staticmethod(z3.Not)
        ite = staticmethod(z3.If)

    after, legal = framing.step(state, values, Algebra)
    return (tuple(z3.If(reset, 0, z3.If(accepted, new, old))
                  for old, new in zip(state, after)),
            z3.Or(reset, z3.Not(accepted), legal))


class LocalInputDomain:
    """A selected trace's fixed input domain, with one accepted record mutable.

    The suffix matters: changing an end flag can invalidate a later start flag.
    A local query may vary data and markers, but cannot silently repair the rest
    of its seed. Reset cuts that dependency. Timelines are built once per Select.
    """

    def __init__(self, contract, stimulus, records, projections):
        self.timelines = []
        for endpoint in contract.inputs():
            framing = endpoint.framing
            if framing is None or endpoint.name not in projections:
                continue
            fields = tuple(field.name for field in endpoint.fields)
            timeline = tuple(
                (slot.reset or endpoint.clock in slot.resets,
                 None if accepted.get(endpoint.name) is None else
                 {field: value for field, value in zip(fields, accepted[endpoint.name])
                  if field in framing.fields})
                for slot, record in zip(stimulus.slots, records, strict=True)
                for accepted in (dict(record.accepted),))
            self.timelines.append((endpoint.name, framing, projections[endpoint.name], timeline))

    def entry_states(self):
        """Native framing states at every slot, in endpoint projection order.

        A stateful Search window may start inside a frame. Compute its actual
        entry counters in one forward pass, not by rescanning the whole prefix
        for each overlapping window. These are concrete states, not proofs
        that a changed marker sequence would still be legal.
        """
        entries = {}
        for _name, framing, _projection, timeline in self.timelines:
            state = framing.initial
            for position, (reset, row) in enumerate(timeline):
                if reset:
                    state = framing.initial
                entries.setdefault(position, []).append(state)
                if not reset and row is not None:
                    state, legal = framing.step(state, row)
                    if not legal:
                        raise ValueError("selected trace violates its declared input domain")
        return {position: tuple(states) for position, states in entries.items()}

    def _contexts(self, position):
        """Fixed prefix state and suffix of each mutable input domain.

        Idle entries add no framing constraint; reset cuts the suffix. Keep the
        remaining marker records, not the selected record's mutable values.
        Symbolic construction and exact-question comparison share this view.
        """
        for name, framing, projection, timeline in self.timelines:
            resetting, record = timeline[position]
            if resetting or record is None:
                continue
            before = framing.initial
            for reset, row in timeline[:position]:
                if reset:
                    before = framing.initial
                elif row is not None:
                    before, legal = framing.step(before, row)
                    if not legal:
                        raise ValueError("selected trace violates its declared input domain")
            suffix = []
            for reset, row in timeline[position + 1:]:
                if reset:
                    break
                if row is not None:
                    suffix.append(row)
            yield name, framing, projection, before, tuple(suffix)

    def context_key(self, position):
        """Exact local constraints within this fixed contract/projection owner."""
        return tuple((name, before, suffix) for name, _framing, _projection, before, suffix
                     in self._contexts(position))

    def constraints(self, position, inputs):
        import z3

        for _name, framing, projection, before, suffix in self._contexts(position):
            state = tuple(z3.BitVecVal(value, initial.size())
                          for value, initial in zip(before, symbolic_initial(framing)))
            markers = {public: inputs[logical] for public, logical in projection
                       if public in framing.fields}
            state, legal = symbolic_step(framing, state, markers)
            yield legal
            yield from self._suffix(framing, ((False, row) for row in suffix), state)

    def suffix_constraints(self, position, states):
        """Reuse a slot relation's framing successor, without re-encoding its prefix."""
        for name, framing, _projection, timeline in self.timelines:
            resetting, record = timeline[position]
            if not resetting and record is not None:
                yield from self._suffix(framing, timeline[position + 1:], states[name])

    @staticmethod
    def _suffix(framing, timeline, state):
        for reset, row in timeline:
            if reset:
                break
            if row is not None:
                state, legal = symbolic_step(framing, state, row)
                yield legal
