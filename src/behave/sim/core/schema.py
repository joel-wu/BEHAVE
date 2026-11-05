"""What the endpoints carry, which belongs to the task and not to either side.

A behavior model and an RTL design are two artifacts that are supposed to
realise the same interface. What that interface is, therefore, is a fact about
the task: how many bits each endpoint carries and whether they are read as
signed. Both artifacts reference it and neither defines it.

It was read out of the candidate RTL text. That is the wrong direction and not
a small one: the candidate is the thing under test, and letting it say how wide
the inputs are lets it define the input space that the behavior model's
verdicts are relative to. A candidate declaring a port one bit narrower than
the task does makes the contract narrower, and narrowing the contract can only
enlarge the set of goals reported unreachable. The artifact under test would be
choosing its own denominator.

So this module defines the types and nothing else. It does not parse
SystemVerilog: reading a candidate's ports is the RTL frontend's job, and its
result is a claim to be checked against the declared schema rather than the
schema itself. `sim/rtl/interface.py` does that reading and says so.
"""

from __future__ import annotations

import enum

__all__ = ["Authority", "Endpoint", "InterfaceError", "Reading",
           "TransactionSchema"]


class InterfaceError(Exception):
    """An interface that cannot be read, or that does not match the schema."""


class Endpoint:
    """One port of the interface: its direction, its width, its signedness."""

    __slots__ = ("direction", "name", "signed", "width")

    def __init__(self, name, direction, width, signed=False):
        self.name = name
        self.direction = direction        # "input", "output" or "inout"
        if type(width) is not int or width < 1:
            raise InterfaceError(
                f"endpoint {name} has width {width!r}, not a positive integer")
        self.width = width
        self.signed = bool(signed)

    @property
    def bounds(self):
        """The ends of the interval this endpoint's values lie in.

        The pair rather than any frontend's interval type. What an interface
        says is how many bits a port has and whether they are read as signed,
        and each frontend makes of that whatever it computes with; handing back
        one side's type would be this module knowing which side asked.
        """
        if self.signed:
            return -(1 << (self.width - 1)), (1 << (self.width - 1)) - 1
        return 0, (1 << self.width) - 1

    def __eq__(self, other):
        return (isinstance(other, Endpoint) and self.name == other.name
                and self.direction == other.direction
                and self.width == other.width and self.signed == other.signed)

    def __repr__(self):
        sign = "signed " if self.signed else ""
        return f"{self.direction} {sign}[{self.width}] {self.name}"


class Authority(enum.Enum):
    """Whose declaration this schema is.

    The task's is the one a certificate should rest on. One recovered from the artifact
    under test is a reading of the thing being judged, so a candidate that narrows a
    port narrows the set of traces every verdict is about.
    """

    TASK = "task"
    CANDIDATE = "candidate"
    #: Neither stated. Treated as a candidate wherever it matters, because assuming the
    #: task declared it is the assumption that costs a reader something.
    UNKNOWN = "unknown"


class Reading(enum.Enum):
    """How the widths were got out of that declaration.

    Elaborated means a width written over the module's parameters is a number. A regular
    expression over the header refuses one it cannot parse and misreads one written any
    way the expression does not cover, and 91 designs in the population declare a port
    whose width is not a literal.
    """

    ELABORATED = "elaborated"
    HEADER_REGEX = "header-regex"
    UNKNOWN = "unknown"


class TransactionSchema:
    """The endpoints of one task, which both artifacts must realise."""

    __slots__ = ("authority", "endpoints", "reading", "source", "top")

    def __init__(self, endpoints, source=None, authority=None, reading=None, top=""):
        self.endpoints = {e.name: e for e in endpoints}
        #: Which top these ports were read from, where the reader knows. A file can hold
        #: several and a caller that has to *name* one -- yosys, the testbench generator
        #: -- otherwise guesses at the text, which picks a helper for any design written
        #: from pieces. Empty where the reading cannot say.
        #:
        #: A name, carried. Nothing here reads anyone's syntax to produce it: the
        #: elaborator answers and this holds the answer, which is the boundary
        #: `tests/sim/test_schema` is about.
        self.top = str(top)
        #: Where this schema came from, **for a reader**. Prose, and nothing decides
        #: anything by looking at it: `instance.blockers` used to, with
        #: `"candidate" in source or "read from" in source`, so two separate facts were
        #: carried in one sentence and rewording the sentence changed whether a
        #: certificate was withheld. B5's fix is the two fields below.
        self.source = source
        #: Whose declaration it is, and how it was read. Two facts because they are
        #: independent: the task's declaration can be read by a regular expression and
        #: a candidate's can be elaborated.
        self.authority = authority or Authority.UNKNOWN
        self.reading = reading or Reading.UNKNOWN

    def __contains__(self, name):
        return name in self.endpoints

    def __getitem__(self, name):
        try:
            return self.endpoints[name]
        except KeyError:
            raise InterfaceError(f"{name} is not a declared endpoint") from None

    def inputs(self):
        return {n: e for n, e in self.endpoints.items()
                if e.direction == "input"}

    def outputs(self):
        """The declared outputs, by name.

        Input bounds were already handed to the Behavior frontend, while the
        output names were rediscovered from Python return syntax.  A computed
        mapping (for example ``{f"out_{i}": ... for i in range(8)}``) then had
        no names even though the task had declared all eight.  The schema owns
        both directions; exposing the symmetric projection lets lowering
        validate a source return against the contract instead of replacing it.
        """
        return {n: e for n, e in self.endpoints.items()
                if e.direction == "output"}

    def signedness(self):
        """Port name -> whether its bits are read as signed.

        A projection of this record rather than a fact anyone else keeps. The RTL
        transition relation had no signedness at all and sign-extended every value it
        read out of a model, so a two-bit unsigned output holding 3 was reported as -1
        and the relation disagreed with the simulator on 5 of 10 steps of one design --
        while agreeing on all 10 compared as bits. Nothing there was miscomputed; the
        reading had no declaration behind it.
        """
        return {n: e.signed for n, e in self.endpoints.items()}

    def bounds(self, names):
        """What each of ``names`` may carry, as the pair of ends of its range.

        A name the schema does not declare is refused rather than given a
        default, since the point of declaring an interface is that no width is
        invented.
        """
        out = {}
        for name in names:
            endpoint = self[name]
            if endpoint.direction != "input":
                raise InterfaceError(f"{name} is declared as an output")
            out[name] = endpoint.bounds
        return out

    def realises(self, other):
        """Reasons ``other`` does not realise this schema, endpoint by endpoint.

        What an RTL frontend owes: not a schema of its own, but a demonstration
        that the ports it elaborated are the ones the task declared. Written
        here because the comparison is between two schemas and belongs to
        neither side.
        """
        out = []
        for name, want in self.endpoints.items():
            got = other.endpoints.get(name)
            if got is None:
                out.append(f"{name} is declared and not realised")
            elif got != want:
                out.append(f"{name} is {got}, and the schema says {want}")
        for name in other.endpoints:
            if name not in self.endpoints:
                out.append(f"{name} is realised and not declared")
        return out

    def __repr__(self):
        return (f"TransactionSchema({len(self.endpoints)} endpoints, "
                f"from {self.source})")
