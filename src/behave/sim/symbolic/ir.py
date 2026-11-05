"""Shared intermediate representation for Behavior and RTL frontends.

A goal is called unreachable only when a query covering every path of the model
comes back unsatisfiable. Concolic execution cannot supply that: it follows one
path at a time, so its failures say nothing about the paths it did not take.
What can supply it is a formula built from the program's syntax, and that needs
the program in a form small enough to encode exactly. This module is that form.

The representation is deliberately poorer than Python. Expressions are total,
so evaluating one can neither fail nor reach a goal, and the only commands that
do either are ``mark`` and ``fail``. A loop carries one body and a trip-count
expression evaluated once on entry. Its finite maximum is proved separately by
the EncodingPlan, which is what makes a bounded query encodable as one formula
rather than as a set of paths.

Arrays have value semantics: assignment and selection copy their recursive value,
and an indexed store updates only its target value.  The frontend therefore admits
source reference sharing only when it cannot make an array update observable through
another name.  Construction witnesses and transition alias checks enforce that
obligation before a source model becomes a ``Procedure``.
"""

from __future__ import annotations

import copy
import hashlib

from .native_values import freeze as _freeze_value
from .native_values import thaw as _thaw_value
from .primitives import primitive_names
from .state_projection import (
    DynamicSequenceBinding,
    SourceProductBinding,
    StateAbsentBinding,
    StatePresenceBinding,
    StateVariableBinding,
)

__all__ = [
    "Assign",
    "Fail",
    "For",
    "If",
    "Lit",
    "LoopFlow",
    "Mark",
    "Op",
    "Procedure",
    "RepresentationStructureError",
    "Sel",
    "Seq",
    "Skip",
    "Store",
    "Var",
    "command_fingerprint",
    "copy_command",
    "expression_fingerprint",
    "expression_nodes",
    "expressions_equal",
    "normalized",
    "procedure_representation_cost",
    "representation_cost",
    "unlowered",
    "validate_procedure_representation",
    "walk",
]


class RepresentationStructureError(ValueError):
    """A procedure lies outside the finite representation capability."""

    __slots__ = ("code",)

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ── types ────────────────────────────────────────────────────────────

# ── expressions ──────────────────────────────────────────────────────

class Var:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


class Lit:
    __slots__ = ("type", "value")

    def __init__(self, value, type=None):
        # ``bool`` subclasses ``int`` in Python, but it is a distinct SolverIR
        # semantic type.  Coercing here made the type checker incapable of
        # distinguishing ``True`` from integer ``1`` before any primitive or
        # backend saw the literal.  Retain the exact admitted scalar carrier;
        # the encoder may still map both to bit patterns under their static
        # types.
        if value.__class__ not in (bool, int):
            raise TypeError("a SolverIR literal is an exact Boolean or integer")
        self.value = value
        self.type = type

    def __repr__(self):
        return str(self.value)


class Op:
    """An application of a core primitive, total on its argument types."""

    __slots__ = ("args", "op")

    def __init__(self, op, *args):
        self.op = op
        self.args = tuple(args)

    def __repr__(self):
        return f"{self.op}({', '.join(map(repr, self.args))})"


class Sel:
    """Element read. Out-of-range reads have a canonical value, so the
    expression is total and the range check is a command-level guard."""

    __slots__ = ("array", "index")

    def __init__(self, array, index):
        self.array = array
        self.index = index

    def __repr__(self):
        return f"{self.array!r}[{self.index!r}]"


EXPRS = (Var, Lit, Op, Sel)


# ── commands ─────────────────────────────────────────────────────────

class Skip:
    __slots__ = ()

    def __repr__(self):
        return "skip"


class Assign:
    __slots__ = ("expr", "var")

    def __init__(self, var, expr):
        self.var = var
        self.expr = expr

    def __repr__(self):
        return f"{self.var} := {self.expr!r}"


class Store:
    __slots__ = ("expr", "index", "var")

    def __init__(self, var, index, expr):
        self.var = var
        self.index = index
        self.expr = expr

    def __repr__(self):
        return f"{self.var}[{self.index!r}] := {self.expr!r}"


class Seq:
    __slots__ = ("first", "second")

    def __init__(self, first, second):
        self.first = first
        self.second = second

    def __reduce__(self):
        """Pickled as the list of its parts, not as a chain of pairs.

        A sequence is nested one node per command, so the default pickling
        recurses once per command, and the queries send the step to a pool of
        worker processes. Twenty-two models exhausted the interpreter's stack
        inside the pickler, which is the seventh walk over a term in this package
        to have needed a loop and the first that is not our own code.

        Flattening here rather than teaching each caller: a sequence's meaning is
        its parts in order, so the pair structure is a representation detail and
        this is the place that owns it.
        """
        return _sequence, (_parts(self),)

    def __repr__(self):
        """The parts in order, for the same reason as above.

        Printing a step is what one does while working out why it is wrong, so a
        representation that raises on the long steps raises exactly when it is
        wanted. It caught the probe written to check the pickling.
        """
        return "; ".join(repr(part) for part in _parts(self))


def _parts(cmd):
    """The commands a sequence holds, in order, without recursion."""
    out, stack = [], [cmd]
    while stack:
        node = stack.pop()
        if isinstance(node, Seq):
            stack.append(node.second)
            stack.append(node.first)
        else:
            out.append(node)
    return out


def _sequence(parts):
    """A sequence of the given commands, rebuilt without recursion."""
    if not parts:
        return Skip()
    out = parts[0]
    for part in parts[1:]:
        out = Seq(out, part)
    return out


def _balanced_op(op, terms, identity):
    """Combine total associative expressions without a linear-depth spine.

    SolverIR expressions have no evaluation effects, and ``land`` and ``lor``
    are total associative primitives. Finite source operations such as table
    membership may produce hundreds of terms; folding them from the left makes
    representation depth depend on table length even though the formula has no
    such semantic nesting. Pair adjacent terms until one balanced tree remains.
    """
    layer = list(terms)
    if not layer:
        return identity
    while len(layer) > 1:
        following = [
            Op(op, layer[index], layer[index + 1])
            for index in range(0, len(layer) - 1, 2)
        ]
        if len(layer) % 2:
            following.append(layer[-1])
        layer = following
    return layer[0]


class If:
    __slots__ = ("guard", "orelse", "then")

    def __init__(self, guard, then, orelse):
        self.guard = guard
        self.then = then
        self.orelse = orelse

    def __repr__(self):
        return f"if {self.guard!r} then ({self.then!r}) else ({self.orelse!r})"


_LOOP_FLOW_TOKEN = object()


class LoopFlow:
    """Definite-flow facts preserved from one retained loop body's CFG.

    The body term is an execution representation, not a second control-flow
    authority.  In particular, if-conversion can make two mutually exclusive
    definitions look independently optional.  The graph lowerer therefore
    carries the facts it proved before flattening: names required on body entry,
    names defined on every body exit, and the finite trip-count interval in the
    represented source domain.
    """

    __slots__ = (
        "_body_seal",
        "_defined",
        "_entry_defined",
        "_graph",
        "_graph_seal",
        "_maximum",
        "_minimum",
        "_proof_body_seal",
        "_required",
        "_trip_seal",
    )

    def __init__(self, minimum, maximum, required=(), defined=(), *,
                 entry_defined=(), graph=None, graph_seal=None, body_seal=None,
                 trip_seal=None, _token=None):
        if _token is not _LOOP_FLOW_TOKEN:
            raise TypeError(
                "loop-flow facts are constructed from the frontend CFG")
        if type(minimum) is not int or minimum < 0:
            raise ValueError("a loop-flow minimum is a natural number")
        if type(maximum) is not int or maximum < minimum:
            raise ValueError(
                "a loop-flow maximum is an integer no smaller than its minimum")
        required = tuple(sorted(set(required)))
        defined = tuple(sorted(set(defined)))
        entry_defined = tuple(sorted(set(entry_defined)))
        if any(type(name) is not str or not name
               for name in (*required, *defined, *entry_defined)):
            raise TypeError("loop-flow names are nonempty strings")
        object.__setattr__(self, "_minimum", minimum)
        object.__setattr__(self, "_maximum", maximum)
        object.__setattr__(self, "_required", required)
        object.__setattr__(self, "_defined", defined)
        object.__setattr__(self, "_entry_defined", entry_defined)
        object.__setattr__(self, "_graph", graph)
        object.__setattr__(self, "_graph_seal", graph_seal)
        object.__setattr__(self, "_body_seal", body_seal)
        object.__setattr__(self, "_proof_body_seal", None)
        object.__setattr__(self, "_trip_seal", trip_seal)

    def __setattr__(self, name, value):
        raise AttributeError("a loop-flow contract is immutable")

    @property
    def minimum(self):
        return self._minimum

    @property
    def maximum(self):
        return self._maximum

    @property
    def required(self):
        return self._required

    @property
    def defined(self):
        return self._defined

    @property
    def entry_defined(self):
        """Names supplied by loop mechanics at the proved body entry.

        The retained :class:`For` binds an internal counter, while the local
        source CFG was proved with the source loop target already bound.  They
        are deliberately different names; recording the proof entry prevents
        a verifier from silently substituting one for the other.
        """
        return self._entry_defined

    @property
    def graph(self):
        return self._graph

    @property
    def graph_seal(self):
        return self._graph_seal

    @property
    def proof_body_seal(self):
        return self._proof_body_seal

    @property
    def image(self):
        return (
            "loop-flow/3",
            self.minimum,
            self.maximum,
            self.required,
            self.defined,
            self.entry_defined,
            self._graph_seal,
            self._body_seal,
            self._proof_body_seal,
            self._trip_seal,
        )

    def matches(self, body, trip_count):
        return (self._body_seal == command_fingerprint(body)
                and self._trip_seal == expression_fingerprint(trip_count))

    def __reduce__(self):
        return _restore_loop_flow, (
            self.minimum,
            self.maximum,
            self.required,
            self.defined,
            self.entry_defined,
            self.graph,
            self.graph_seal,
            self._body_seal,
            self._proof_body_seal,
            self._trip_seal,
        )


def _make_loop_flow(minimum, maximum, required, defined, entry_defined, graph,
                    graph_seal, proof_body, body, trip_count):
    """Create the proof object attached to one frontend-retained loop."""
    out = LoopFlow(
        minimum,
        maximum,
        required,
        defined,
        entry_defined=entry_defined,
        graph=graph,
        graph_seal=graph_seal,
        body_seal=command_fingerprint(body),
        trip_seal=expression_fingerprint(trip_count),
        _token=_LOOP_FLOW_TOKEN,
    )
    object.__setattr__(
        out, "_proof_body_seal", command_fingerprint(proof_body))
    return out


def _restore_loop_flow(minimum, maximum, required, defined, entry_defined, graph,
                       graph_seal, body_seal, proof_body_seal, trip_seal):
    out = LoopFlow(
        minimum,
        maximum,
        required,
        defined,
        entry_defined=entry_defined,
        graph=graph,
        graph_seal=graph_seal,
        body_seal=body_seal,
        trip_seal=trip_seal,
        _token=_LOOP_FLOW_TOKEN,
    )
    object.__setattr__(out, "_proof_body_seal", proof_body_seal)
    return out


class For:
    """A finite loop whose trip count is evaluated once on entry.

    The command is semantic: it retains one body and the expression that says
    how many times that body runs.  A frontend-produced ``LoopFlow`` may also
    carry the task-domain trip interval used by the encoding plan; that proof
    metadata does not change the command's execution.

    Integers are accepted as a convenience for hand-built procedures and are
    immediately represented as literals; production lowering supplies an IR
    expression.
    """

    __slots__ = ("_flow", "body", "index", "trip_count")

    def __init__(self, index, trip_count, body, flow=None):
        if flow is not None and type(flow) is not LoopFlow:
            raise TypeError("a retained loop flow is a LoopFlow or None")
        self.index = index
        self.trip_count = (Lit(trip_count) if type(trip_count) is int
                           else trip_count)
        self.body = body
        self._flow = flow

    @property
    def flow(self):
        return self._flow

    @property
    def bound(self):
        """The literal trip count, for legacy diagnostics, or ``None``.

        Consumers must not use this as an encoding bound: a dynamic semantic
        loop intentionally has no literal value here.
        """
        return (self.trip_count.value
                if isinstance(self.trip_count, Lit) else None)

    def __repr__(self):
        return (f"for {self.index} < {self.trip_count!r} "
                f"do ({self.body!r})")


class Mark:
    """Records a nonterminal goal hit."""

    __slots__ = ("goal",)

    def __init__(self, goal):
        self.goal = goal

    def __repr__(self):
        return f"mark({self.goal})"


class Fail:
    """Ends the step with an error code, reaching the goal that reports it.

    The goal is part of the command rather than a ``mark`` written beside it. A
    failure that reached no goal would be a defect invisible to the closure, so
    the two have to happen together; while they were separate commands, keeping
    them together was an obligation on the lowering, and checking it needed an
    analysis over the shape of the program.

    That analysis was the wrong strength and was never going to be right. What
    it asked was whether some mark appears before the failure, and what the
    obligation says is that every path reaching the failure passes one, so a
    mark under a conditional satisfied it. Making them one command retires the
    question: there is no path from the failure back to a program in which the
    goal was not reached.
    """

    __slots__ = ("code", "goal")

    def __init__(self, code, goal=None):
        self.code = code
        self.goal = goal

    def __repr__(self):
        return f"fail({self.code}, {self.goal})"


CMDS = (Skip, Assign, Store, Seq, If, For, Mark, Fail)


def _digest_image(image):
    return hashlib.sha256(repr(image).encode("utf-8")).hexdigest()


def _type_fingerprint(kind):
    if kind is None:
        return None
    return ("type", type(kind).__name__, repr(kind))


def expression_fingerprint(root):
    """Stable structural digest of one SolverIR expression."""
    images = {}
    stack = [(root, False)]
    while stack:
        expr, expanded = stack.pop()
        if expr in images:
            continue
        if isinstance(expr, Op):
            children = expr.args
        elif isinstance(expr, Sel):
            children = (expr.array, expr.index)
        else:
            children = ()
        if not expanded and children:
            stack.append((expr, True))
            for child in reversed(children):
                if child not in images:
                    stack.append((child, False))
            continue
        if isinstance(expr, Var):
            image = ("var", expr.name)
        elif isinstance(expr, Lit):
            image = ("lit", expr.value, _type_fingerprint(expr.type))
        elif isinstance(expr, Op):
            image = ("op", expr.op, tuple(images[arg] for arg in expr.args))
        elif isinstance(expr, Sel):
            image = ("sel", images[expr.array], images[expr.index])
        else:
            raise TypeError(
                f"unknown expression in a structural digest: "
                f"{type(expr).__name__}")
        images[expr] = _digest_image(image)
    return images[root]


def expressions_equal(left, right):
    """Exact structural equality without serializing/hashing either expression.

    Expressions have identity semantics and can be rewritten by frontends, so
    keep this DAG-pair memo local to one comparison. The literal's Python carrier
    and declared semantic type both matter. This is no algebraic equivalence
    guess; unequal trees simply do not trigger a structural simplification.
    """
    pending, seen = [(left, right)], set()
    while pending:
        left, right = pending.pop()
        if left is right or (left, right) in seen:
            continue
        seen.add((left, right))
        if type(left) is not type(right):
            return False
        if isinstance(left, Var):
            if left.name != right.name:
                return False
        elif isinstance(left, Lit):
            if (type(left.value) is not type(right.value) or left.value != right.value
                    or _type_fingerprint(left.type) != _type_fingerprint(right.type)):
                return False
        elif isinstance(left, Op):
            if left.op != right.op or len(left.args) != len(right.args):
                return False
            pending.extend(zip(left.args, right.args))
        elif isinstance(left, Sel):
            pending.extend(((left.array, right.array), (left.index, right.index)))
        else:
            raise TypeError(f"unknown expression in structural comparison: {type(left).__name__}")
    return True


def command_fingerprint(root, *, include_flow=False):
    """Stable structural digest of one SolverIR command.

    Sequence association is normalized because it is not part of execution
    semantics.  ``include_flow`` additionally binds the frontend's checked
    control-flow proof; the default describes only the executable command.
    """
    images = {}
    # Only a sequence's non-sequence children contribute to its normalized
    # image.  Recording those children once avoids computing a fingerprint for
    # every intermediate association node: a left-associated sequence of n
    # commands would otherwise flatten prefixes of lengths 1..n and take
    # quadratic time.
    sequence_parts = {}
    stack = [(root, False)]
    while stack:
        cmd, expanded = stack.pop()
        if cmd in images:
            continue
        if isinstance(cmd, Seq):
            parts = sequence_parts.get(cmd)
            if parts is None:
                flat = []
                pending = [cmd]
                while pending:
                    part = pending.pop()
                    if isinstance(part, Seq):
                        pending.append(part.second)
                        pending.append(part.first)
                    else:
                        flat.append(part)
                parts = sequence_parts[cmd] = tuple(flat)
            children = parts
        elif isinstance(cmd, If):
            children = (cmd.then, cmd.orelse)
        elif isinstance(cmd, For):
            children = (cmd.body,)
        else:
            children = ()
        if not expanded and children:
            stack.append((cmd, True))
            for child in reversed(children):
                if child not in images:
                    stack.append((child, False))
            continue
        if isinstance(cmd, Skip):
            image = ("skip",)
        elif isinstance(cmd, Assign):
            image = ("assign", cmd.var, expression_fingerprint(cmd.expr))
        elif isinstance(cmd, Store):
            image = (
                "store",
                cmd.var,
                expression_fingerprint(cmd.index),
                expression_fingerprint(cmd.expr),
            )
        elif isinstance(cmd, Seq):
            image = (
                "seq",
                tuple(images[part] for part in sequence_parts.pop(cmd)),
            )
        elif isinstance(cmd, If):
            image = (
                "if",
                expression_fingerprint(cmd.guard),
                images[cmd.then],
                images[cmd.orelse],
            )
        elif isinstance(cmd, For):
            flow = (cmd.flow.image if include_flow and cmd.flow is not None
                    else None)
            image = (
                "for",
                cmd.index,
                expression_fingerprint(cmd.trip_count),
                images[cmd.body],
                flow,
            )
        elif isinstance(cmd, Mark):
            image = ("mark", cmd.goal)
        elif isinstance(cmd, Fail):
            image = ("fail", cmd.code, cmd.goal)
        else:
            raise TypeError(
                f"unknown command in a structural digest: "
                f"{type(cmd).__name__}")
        images[cmd] = _digest_image(image)
    return images[root]


class StateContract:
    """The source's state universe and each field's representation, immutable.

    **One object, sealed at lowering** (review 21): `source_state` and
    `representation` were two plain attributes -- a mutable dict shared by
    reference through `derived()` -- so a later pass could reclassify a field in
    place and reopen the very false pass the contract exists to close (x deleted
    from the IR, then relabelled `("constant", 0)`, and the audit over a=0 came
    back empty). Here the keys are checked to equal the fields exactly, the
    kinds are a closed set, constant values are frozen, lookups have **no
    default** (`how` raises on an unknown field), and nothing mutates.
    """

    __slots__ = ("_bindings", "_fields", "_pairs")

    KINDS = ("state", "constant", "folded")

    def __init__(self, fields, representation, projections=()):
        # Re-entering __init__ was the one door left: it writes through
        # `object.__setattr__`, so calling it on a live contract reclassified
        # in place past the setattr guard.
        if getattr(self, "_pairs", None) is not None:
            raise AttributeError("a StateContract is immutable")
        fields = tuple(sorted(str(f) for f in fields))
        rep = dict(representation)
        if set(rep) != set(fields):
            raise ValueError(
                f"the representation must classify exactly the contract's "
                f"fields: fields {sorted(fields)}, classified {sorted(rep)}")
        pairs = []
        for name in fields:
            how = tuple(rep[name])
            if not how or how[0] not in self.KINDS:
                raise ValueError(f"unknown representation kind for {name}: "
                                 f"{how!r}")
            if how[0] == "constant":
                # Exactly two fields (review 22: ("constant",) died on an
                # IndexError and ("constant", 1, 2) was silently truncated),
                # and the value frozen recursively with its types tagged.
                if len(how) != 2:
                    raise ValueError(f"a constant record is exactly "
                                     f"('constant', value), got {how!r}")
                how = ("constant", _freeze_value(how[1]))
            elif len(how) != 1:
                raise ValueError(f"{how[0]!r} takes no value, got {how!r}")
            pairs.append((name, how))
        object.__setattr__(self, "_fields", fields)
        object.__setattr__(self, "_pairs", tuple(pairs))
        bindings = tuple(projections)
        if any(type(binding) not in (DynamicSequenceBinding, SourceProductBinding,
                                    StateVariableBinding, StatePresenceBinding,
                                    StateAbsentBinding)
               for binding in bindings):
            raise TypeError(
                "a state projection is an exact declared state binding")
        names = tuple(binding.name for binding in bindings)
        if len(names) != len(set(names)):
            raise ValueError("a source state field has two projections")
        classified = dict(pairs)
        for binding in bindings:
            if binding.name not in classified:
                raise ValueError(
                    f"the projection for {binding.name!r} is outside the "
                    "source state contract")
            if classified[binding.name] != ("state",):
                raise ValueError(
                    f"the projection for {binding.name!r} is not state")
        object.__setattr__(
            self, "_bindings", tuple(sorted(bindings, key=lambda one: one.name)))

    def __setattr__(self, name, value):
        raise AttributeError("a StateContract is immutable")

    def __reduce__(self):
        # Rebuilt through the constructor -- pickle's slot restore goes through
        # `__setattr__`, which immutability blocks -- and from the **thawed**
        # public form: serialising `_pairs` handed the constructor values that
        # were already frozen, so every process boundary froze them again and a
        # tuple constant came back as ('tuple', (('atom', 1), ...)) -- an honest
        # model failing its own audit after one trip through the step audit's
        # worker pool (review 23).
        return (StateContract,
                (self._fields, dict(self.items()), self._bindings))

    @property
    def fields(self):
        return self._fields

    def how(self, name):
        """The field's representation, constants thawed to fresh copies of
        their original type. **No default**: an unknown field is a `KeyError`,
        because a default of ("state",) is how an audit quietly widens its own
        trust."""
        for field, how in self._pairs:
            if field == name:
                return (("constant", _thaw_value(how[1]))
                        if how[0] == "constant" else how)
        raise KeyError(name)

    def items(self):
        return tuple((name, self.how(name)) for name, _ in self._pairs)

    @property
    def projections(self):
        return self._bindings

    def projection(self, name):
        """The explicit logical view of one represented state field, if any."""
        for binding in self._bindings:
            if binding.name == name:
                return binding
        return None

    def __eq__(self, other):
        return (isinstance(other, StateContract)
                and self._pairs == other._pairs
                and self._bindings == other._bindings)

    def __hash__(self):
        return hash((self._pairs, self._bindings))

    def __repr__(self):
        suffix = (f", projections={self._bindings!r}"
                  if self._bindings else "")
        return f"StateContract({dict(self._pairs)!r}{suffix})"


_CONTROL_FLOW_TOKEN = object()


class _ControlFlowAuthority:
    """Opaque binding made where a frontend owns both graph and final term."""

    __slots__ = ("body_seal", "graph", "graph_seal")

    def __init__(self, graph, graph_seal, body_seal, *, _token=None):
        if _token is not _CONTROL_FLOW_TOKEN:
            raise TypeError("control-flow authority is frontend-constructed")
        object.__setattr__(self, "graph", graph)
        object.__setattr__(self, "graph_seal", graph_seal)
        object.__setattr__(self, "body_seal", body_seal)

    def __setattr__(self, name, value):
        raise AttributeError("a control-flow authority is immutable")


def _make_control_flow_authority(graph, body):
    """Bind the graph and term simultaneously at their trusted producer."""
    from . import cfg

    return _ControlFlowAuthority(
        graph,
        cfg.graph_fingerprint(graph),
        command_fingerprint(body),
        _token=_CONTROL_FLOW_TOKEN,
    )


def _bind_control_flow(proc, authority):
    """Attach an exact frontend authority to a newly built procedure."""
    if authority is None:
        return
    if type(authority) is not _ControlFlowAuthority:
        raise TypeError("a procedure accepts only frontend control-flow authority")
    if proc._graph is not None:
        raise AttributeError("a procedure's control-flow graph is write-once")
    from . import cfg

    if cfg.graph_fingerprint(authority.graph) != authority.graph_seal:
        raise RepresentationStructureError(
            "control-flow-authority-changed",
            "the frontend control-flow graph changed before it was bound")
    if command_fingerprint(proc.body) != authority.body_seal:
        raise RepresentationStructureError(
            "control-flow-body-mismatch",
            "the frontend control-flow authority belongs to another body")
    object.__setattr__(proc, "_graph", authority.graph)
    object.__setattr__(proc, "_graph_seal", authority.graph_seal)
    object.__setattr__(proc, "_graph_body_seal", authority.body_seal)


class Procedure:
    """One step of a behavior model: state, input, local and output
    variables, and the command relating them."""

    __slots__ = (
        "_contract",
        "_graph",
        "_graph_body_seal",
        "_graph_seal",
        "_output_boundary",
        "_semantics",
        "body",
        "boundary_complete",
        "count",
        "discharged",
        "entry_dead",
        "guessed_initial",
        "impossible",
        "initial",
        "inputs",
        "input_framing",
        "locals",
        "output_capacity",
        "output_ranges",
        "output_vars",
        "outputs",
        "provenance",
        "signature",
        "state",
        "state_range_bound",
        "state_range_result",
        "state_ranges",
        "types",
        "value_bindings",
    )

    def __init__(self, state, inputs, locals, outputs, body, types=None,
                 count=None, impossible=None, initial=None, output_vars=None,
                 provenance=None, signature=None, output_capacity=None,
                 boundary_complete=True, semantic_contract=None,
                 value_bindings=()):
        # Where each goal the lowering created lives, recorded by whatever
        # created it. A reader that wants to print a goal beside its source
        # asks here rather than taking the identifier apart.
        self.provenance = dict(provenance or {})
        # Legacy marker for a source initial value that was unavailable.  The
        # production frontend no longer guesses such values: a missing field is
        # either proved entry-dead below or refused.  The slot remains on the
        # public record while older hand-built procedures are migrated.
        self.guessed_initial = ()
        # The source's state universe and each field's representation, as one
        # immutable `StateContract` sealed by the lowering (reviews 19-22).
        # `None` for a hand-built procedure; the audits walk this rather than
        # `self.state`. **Write-once**: assigning a forged contract, or `None`
        # to reach the proc.state fallback, reopened the deletion false pass,
        # so the binding accepts exactly one real `StateContract` and then
        # refuses.
        self._contract = None
        # The four semantic type contexts, finite boundary domains and error
        # alphabet are a second write-once contract.  It is bound after type
        # inference because compiler locals do not all have types at Procedure
        # construction time; every backend validates it before use.
        self._semantics = None
        # A lowering may seal a local proof that every active output write is
        # in the task carrier and every return count fits its BatchPlan.  This
        # remains separate from the finite exact-encoding plan.
        self._output_boundary = None
        # Safety goals the source declares and this step does not produce, each with
        # the reason the operation is total at that site: an index that folded to a
        # literal in range, a lookup with a default. The declared catalogue is checked
        # against the produced goals *plus* these, so a rule that goes missing is a
        # declared goal in neither rather than a goal nothing declared.
        self.discharged = {}
        # Source fields absent after construction whose entry value the step
        # provably cannot observe.  The lowering still gives the total IR state
        # vector a canonical inhabitant, but records why that value is not a
        # source initial-state claim.  It is excluded from range seeds.
        self.entry_dead = ()
        # The ranges the lowering closed over while resolving bounds. Left here
        # because every caller wants them and computing them is the whole cost
        # of lowering: without this, each one runs the fixpoint again.
        self.state_ranges = {}
        # The total abstract result whose known projection is ``state_ranges``.
        # ``None`` only for hand-built procedures that did not run StatePlan.
        self.state_range_result = None
        # ``None`` denotes an inductive/fixpoint environment.  A finite value
        # records that ``state_ranges`` was derived for exactly that many
        # steps; unlike a fixpoint it need not survive one additional step.
        self.state_range_bound = None
        # Task-owned finite input/output types.  ``None`` for legacy hand-built
        # procedures, which may execute but cannot inherit a task certificate.
        self.signature = signature
        self.input_framing = ()
        # False only for an explicitly sliced analysis procedure.  Such a
        # formula may answer its selected goal, but it no longer contains the
        # complete return boundary and therefore must not pretend to re-prove
        # the task's output contract.
        self.boundary_complete = bool(boundary_complete)
        # Output-carrier evidence proved against that signature, by interval
        # containment or the exact bounded fallback.  Kept so downstream
        # readers can verify that the procedure still has the same proof.
        self.output_ranges = {}
        # The control-flow graph the body was flattened from, when the lowering
        # built one. Questions about control flow are answered on this rather
        # than on a graph read back out of the term: a graph recovered from a
        # flattened program is a reading of the flattening, and the point of
        # building one is that it does not have to be recovered. ``None`` where
        # the term was written directly.
        self._graph = None
        self._graph_seal = None
        self._graph_body_seal = None
        self.state = list(state)
        self.inputs = list(inputs)
        self.locals = list(locals)
        self.outputs = list(outputs)
        self.body = body
        self.types = dict(types or {})
        # Logical source/task values may be eliminated into several SolverIR
        # leaves.  The immutable bindings keep that representation decision on
        # the procedure rather than reconstructing it from names or runtime
        # objects.  Exact validation belongs to ProcedureContract, which is
        # bound after type inference.
        self.value_bindings = tuple(value_bindings)
        # How many output transactions this step produced. What a step produces
        # is a sequence, not a value: a model may answer nothing while it is
        # still consuming and several when one input expands into many, so each
        # output holds the transactions of this step and this says how many of
        # them are there.
        self.count = count
        # Declared goals this step cannot reach by construction, each with the
        # reason. A goal with no producer is either one of these or a rule the
        # lowering is missing, and only the first supports calling it
        # unreachable.
        self.impossible = dict(impossible or {})
        # What the state holds before the first step. A run starts where the
        # harness starts it, so this is part of the step's meaning rather than
        # a convenience: without it a query asks whether some state reaches a
        # goal instead of whether a run does.
        self.initial = dict(initial or {})
        # The variable each output port is held in. Usually the port's own
        # name, and a name of the lowering's own where a field of the step
        # shares it: the two are different things and a port is not a place
        # to keep state.
        self.output_vars = dict(output_vars
                                or {name: name for name in self.outputs})
        # Logical transaction slots a normally completing step may fill.  The
        # representation keeps one padding cell even for capacity zero, so
        # this cannot in general be recovered from a buffer's physical shape.
        # It is consequently part of the procedure, not an encoder guess.
        if output_capacity is not None \
                and (type(output_capacity) is not int or output_capacity < 0):
            raise ValueError("output_capacity must be a natural number or None")
        self.output_capacity = output_capacity
        if semantic_contract is not None:
            self.semantics = semantic_contract

    def derived(self, **overrides):
        """A copy of this procedure with ``overrides`` applied -- and **every**
        metadata field carried.

        The one way a pass may rebuild a procedure. Slicing rebuilt by calling
        the constructor and hand-copying the fields it remembered, so each new
        field silently vanished through it -- `source_state` did (review 20),
        turning the frozen audit contract back into `proc.state` downstream.
        A field a pass wants to drop is dropped by name (`graph=None`), never
        by omission.
        """
        # `contract` is deliberately absent (review 22): a pass that could hand
        # `derived()` a replacement contract -- or None, to reach the
        # proc.state fallback -- could reopen every false pass the seal closed.
        # The contract always rides along, never overridden.
        _known = {"state", "inputs", "locals", "outputs", "body", "types",
                  "count", "impossible", "initial", "output_vars", "provenance",
                  "guessed_initial", "state_ranges", "state_range_bound",
                  "state_range_result",
                  "graph", "discharged",
                  "entry_dead", "output_ranges", "output_capacity",
                  "boundary_complete", "value_bindings"}
        unknown = sorted(set(overrides) - _known)
        if unknown:
            # A typo here ("representaton=") used to be silently ignored, which
            # is a pass believing it overrode something it did not.
            raise TypeError(f"derived() has no fields named {unknown}")
        replacement_body = overrides.get("body", self.body)
        if (self._graph is not None
                and command_fingerprint(replacement_body)
                != command_fingerprint(self.body)
                and overrides.get("graph", self._graph) is not None):
            raise RepresentationStructureError(
                "derived-body-kept-stale-control-flow",
                "a transformed procedure must explicitly drop its old "
                "control-flow authority")
        out = Procedure(overrides.get("state", self.state),
                        overrides.get("inputs", self.inputs),
                        overrides.get("locals", self.locals),
                        overrides.get("outputs", self.outputs),
                        overrides.get("body", self.body),
                        types=overrides.get("types", self.types),
                        count=overrides.get("count", self.count),
                        impossible=overrides.get("impossible", self.impossible),
                        initial=overrides.get("initial", self.initial),
                        output_vars=overrides.get("output_vars",
                                                  self.output_vars),
                        provenance=overrides.get("provenance", self.provenance),
                        signature=self.signature,
                        output_capacity=overrides.get(
                            "output_capacity", self.output_capacity),
                        boundary_complete=overrides.get(
                            "boundary_complete", self.boundary_complete),
                        value_bindings=overrides.get(
                            "value_bindings", self.value_bindings))
        for name in ("guessed_initial", "state_ranges", "state_range_bound",
                     "state_range_result",
                     "discharged", "entry_dead", "output_ranges"):
            value = overrides.get(name, getattr(self, name))
            if name == "output_ranges":
                value = copy.deepcopy(value)
            setattr(out, name, value)
        # Do not evaluate ``self.graph`` when the caller explicitly drops the
        # old graph.  A transformed body deliberately makes that graph stale,
        # and the guarded property must not be read merely as the eager default
        # argument to ``dict.get``.
        try:
            graph = overrides["graph"]
        except KeyError:
            graph = self.graph
        if graph is not None:
            if graph is not self._graph:
                raise TypeError(
                    "derived() can inherit or drop control flow, not replace it")
            object.__setattr__(out, "_graph", graph)
            object.__setattr__(out, "_graph_seal", self._graph_seal)
            object.__setattr__(out, "_graph_body_seal", self._graph_body_seal)
        out._contract = self._contract
        semantic_fields = {
            "state", "inputs", "locals", "outputs", "body", "types",
            "output_vars", "value_bindings",
        }
        if not semantic_fields.intersection(overrides):
            out._semantics = self._semantics
        output_fields = {
            "body", "count", "output_vars", "output_capacity",
            "boundary_complete",
        }
        if (not output_fields.intersection(overrides)
                and out.boundary_complete is True):
            out._output_boundary = self._output_boundary
        elif "output_ranges" not in overrides:
            out.output_ranges = {}
        return out

    @property
    def contract(self):
        return self._contract

    @contract.setter
    def contract(self, value):
        if self._contract is not None:
            raise AttributeError("a procedure's contract is write-once")
        if value is None:
            return
        if type(value) is not StateContract:
            raise TypeError(f"a contract is a StateContract, "
                            f"not {type(value).__name__}")
        self._contract = value

    @property
    def semantics(self):
        """The procedure-owned semantic type/domain/error contract."""
        return self._semantics

    @semantics.setter
    def semantics(self, value):
        if self._semantics is not None:
            raise AttributeError("a procedure's semantic contract is write-once")
        if value is None:
            return
        from .procedure_contract import ProcedureContract

        if type(value) is not ProcedureContract:
            raise TypeError(
                f"procedure semantics is a ProcedureContract, not "
                f"{type(value).__name__}")
        self._semantics = value

    @property
    def graph(self):
        """The source CFG, checked against the executable body on every read."""
        if self._graph is None:
            return None
        from . import cfg

        if cfg.graph_fingerprint(self._graph) != self._graph_seal:
            raise RepresentationStructureError(
                "control-flow-authority-changed",
                "the procedure's sealed control-flow graph changed")
        if command_fingerprint(self.body) != self._graph_body_seal:
            raise RepresentationStructureError(
                "control-flow-body-changed",
                "the procedure body changed after its control flow was sealed")
        return self._graph

    @graph.setter
    def graph(self, value):
        if value is None:
            return
        raise AttributeError(
            "a procedure's control-flow graph is bound by its frontend")

    @property
    def source_state(self):
        """The contract's field tuple, or `None` for a hand-built procedure."""
        return self.contract.fields if self.contract is not None else None

    @property
    def representation(self):
        """The contract's classification as a plain dict view, or `None`.

        A **copy**: mutating it changes nothing, which is the point.
        """
        return (dict(self.contract.items())
                if self.contract is not None else None)

    @property
    def variables(self):
        return (self.state + self.inputs + self.locals
                + list(dict.fromkeys(self.output_vars.values())))

    def __repr__(self):
        return (f"Procedure(state={self.state}, inputs={self.inputs}, "
                f"outputs={self.outputs})")


# ── traversal and well-formedness ────────────────────────────────────

def unlowered(cmd):
    """Operations in ``cmd`` that the backend has no business seeing, with their sites.

    T10's check. A `len` that reaches a lowered step is a shape the frontend did not
    work out, and the honest place to say so is here rather than three stages later,
    where the encoder refuses with a message about a length and a reader has to know
    that a length is a frontend question.

    Returns pairs of (operation, the expression it heads), so a caller can report more
    than a name. An operation in neither set is also returned: a set that is not closed
    is the fault this exists for, and an operation nobody declared is the clearest case
    of it.
    """
    out = []

    def visit(expr):
        if isinstance(expr, Op):
            if expr.op not in primitive_names():
                out.append((expr.op, expr))
            for arg in expr.args:
                visit(arg)
        elif isinstance(expr, Sel):
            visit(expr.array)
            visit(expr.index)

    for node in walk(cmd):
        if isinstance(node, Assign):
            visit(node.expr)
        elif isinstance(node, Store):
            visit(node.index)
            visit(node.expr)
        elif isinstance(node, If):
            visit(node.guard)
        elif isinstance(node, For):
            visit(node.trip_count)
    return out


def walk(cmd):
    """Every command of ``cmd``, itself included, in preorder.

    Iterative. A step's commands nest as deeply as the program is long once its
    loops are unrolled, and a sequence of a few thousand is ordinary; the
    recursive version exhausted the interpreter's stack on the terms that
    if-conversion produces, which are one long left-leaning chain. That is a
    limit of the traversal rather than of the programs, and every caller here
    walks with this one.
    """
    stack = [cmd]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, Seq):
            stack.append(node.second)
            stack.append(node.first)
        elif isinstance(node, If):
            stack.append(node.orelse)
            stack.append(node.then)
        elif isinstance(node, For):
            stack.append(node.body)


def cost(cmd):
    """How many command nodes the semantic step retains.

    This is the structural measure governed by ``SEMANTIC_IR_NODES``.  It is
    independent of :func:`procedure_representation_cost`, which belongs to a
    finite exact-encoding plan and additionally charges recursive value leaves.

    A loop retains one trip-count expression and one body.  The work of making
    guarded copies for a particular finite domain is charged by its
    :class:`EncodingPlan`, not by this structural measure.

    Iterative, and it has to be: the terms this is asked about are the long ones.
    """
    total, active = 0, set()
    stack = [(cmd, 1, False)]
    while stack:
        node, times, leaving = stack.pop()
        key = id(node)
        if leaving:
            active.remove(key)
            continue
        if key in active:
            raise ValueError("an IR command contains itself")
        active.add(key)
        stack.append((node, times, True))
        total += times
        if isinstance(node, Seq):
            stack.append((node.second, times, False))
            stack.append((node.first, times, False))
        elif isinstance(node, If):
            stack.append((node.orelse, times, False))
            stack.append((node.then, times, False))
        elif isinstance(node, For):
            stack.append((node.body, times, False))
    return total


def representation_cost(cmd, variable_types, limit=None, *, compact_arrays=False):
    """Work needed to materialize the command's recursively typed values.

    ``cost`` counts the semantic control nodes, including one retained node per
    loop.  That is the right early construction guard, but one command can
    carry a matrix: encoding
    an assignment, store, selection or array choice visits its scalar leaves.
    This second measure counts those visits.  It saturates at ``limit + 1`` so
    the guard itself never expands a hostile product.

    ``compact_arrays`` is the transition-template representation: scalar
    integer arrays are persistent values with constant-cost copies/stores and
    deferred reads, not eagerly traversed vectors. Aggregate equality and
    explicitly enumerated literals still pay for their actual finite work.
    The ordinary encoder keeps the dense accounting by default.
    """
    from . import typecheck as value_types

    if limit is not None:
        if type(limit) is not int or limit < 0:
            raise ValueError(f"a representation-cost limit is a nonnegative "
                             f"integer, not {limit!r}")
        # ``limit + 1`` is the one sentinel for "over the limit".  Keeping the
        # sentinel distinct matters to a future caller that uses the measure as
        # a gate: a term whose cost is exactly the limit is still admissible.
        ceiling = limit + 1
    else:
        ceiling = None

    def plus(*values):
        total = 0
        for value in values:
            if ceiling is not None and value >= ceiling - total:
                return ceiling
            total += value
        return total

    def times(left, right):
        if left == 0 or right == 0:
            return 0
        if ceiling is not None:
            if left >= ceiling or right >= ceiling:
                return ceiling
            # Test before multiplying.  A hostile nest of large loop bounds or
            # array lengths must not make the *measurement* allocate an enormous
            # bigint merely to discover that it is over the caller's limit.
            if left > (ceiling - 1) // right:
                return ceiling
        return left * right

    leaf_cache = {}

    def leaves(root):
        """Nodes of one recursive value representation, iteratively.

        Array carriers cost work even when they contain one cell: readers
        allocate or traverse the container as well as its scalar leaves.  A
        leaf-only product made an arbitrarily deep chain of one-cell arrays
        cost one, then handed that depth to recursive type and encoder walks.
        """
        root_key = id(root)
        if root_key in leaf_cache:
            return leaf_cache[root_key]
        active = set()
        stack = [(root, False)]
        while stack:
            kind, ready = stack.pop()
            key = id(kind)
            if key in leaf_cache:
                continue
            if not isinstance(kind, value_types.Array):
                leaf_cache[key] = 1
                continue
            if compact_arrays and kind.cells > 0 and kind.elem == value_types.INTEGER:
                leaf_cache[key] = 1
                continue
            if ready:
                active.discard(key)
                leaf_cache[key] = plus(
                    1, times(kind.cells, leaf_cache[id(kind.elem)]))
                continue
            if key in active:
                raise ValueError("a representation type contains itself")
            active.add(key)
            stack.append((kind, True))
            stack.append((kind.elem, False))
        return leaf_cache[root_key]

    def children(expr):
        if isinstance(expr, Sel):
            return (expr.array, expr.index)
        if isinstance(expr, Op):
            return expr.args
        return ()

    expression_cache = {}
    kind_cache = {}

    def expression(root):
        """Return ``(cost, result type)`` without recursing on source depth."""
        root_key = id(root)
        if root_key in expression_cache:
            return expression_cache[root_key], kind_cache[root_key]
        active = {root_key}
        # A cursor per node, rather than pushing every child at once.  IR
        # expressions are DAGs in normal use (one captured value may occur in
        # several operands); scheduling duplicate children concurrently would
        # mistake that sharing for a cycle.
        stack = [(root, 0)]
        while stack:
            node, next_child = stack[-1]
            key = id(node)
            if key in expression_cache:
                active.discard(key)
                stack.pop()
                continue
            parts = children(node)
            if next_child < len(parts):
                child = parts[next_child]
                stack[-1] = (node, next_child + 1)
                child_key = id(child)
                if child_key in expression_cache:
                    continue
                if child_key in active:
                    raise ValueError("an IR expression contains itself")
                active.add(child_key)
                stack.append((child, 0))
                continue
            active.discard(key)
            stack.pop()
            part_costs = [expression_cache[id(part)] for part in parts]
            part_kinds = [kind_cache[id(part)] for part in parts]
            if isinstance(node, Lit):
                kind, own = value_types.INTEGER, 1
            elif isinstance(node, Var):
                kind = variable_types.get(node.name, value_types.INTEGER)
                own = leaves(kind)
            elif isinstance(node, Sel):
                array_kind = part_kinds[0]
                kind = (array_kind.elem if isinstance(
                    array_kind, value_types.Array) else value_types.INTEGER)
                # Selection enumerates the outer cells and chooses the result
                # componentwise.  The child costs account for reading the
                # array and index; this accounts for constructing the choice.
                own = leaves(array_kind)
            elif isinstance(node, Op) and node.op == "arraylit":
                elem = part_kinds[0] if part_kinds else value_types.INTEGER
                kind = value_types.Array(len(parts), elem)
                own = 1
            elif isinstance(node, Op) and node.op == "arrayfill":
                elem = part_kinds[0] if part_kinds else value_types.INTEGER
                length = node.args[1] if len(node.args) == 2 else None
                cells = (length.value if isinstance(length, Lit)
                         and type(length.value) is int
                         and length.value >= 0 else 0)
                kind = value_types.Array(cells, elem)
                own = 1
            elif isinstance(node, Op) and node.op == "store":
                kind = (part_kinds[0] if part_kinds
                        else value_types.Array(0))
                own = leaves(kind)
            elif isinstance(node, Op) and node.op == "ite":
                kind = (part_kinds[1] if len(part_kinds) > 1
                        else value_types.INTEGER)
                own = leaves(kind) if isinstance(
                    kind, value_types.Array) else 1
            elif isinstance(node, Op) and node.op in ("umerge", "smerge"):
                kind = part_kinds[0]
                own = leaves(kind)
            elif isinstance(node, Op) and node.op in ("eq", "ne") and any(
                    isinstance(kind, value_types.Array) for kind in part_kinds):
                # Extensional comparisons still consume the finite value.
                kind = value_types.INTEGER
                own = 1
                for operand in part_kinds:
                    copies = 1
                    while isinstance(operand, value_types.Array):
                        copies = times(copies, operand.cells)
                        operand = operand.elem
                    own = plus(own, copies)
            else:
                kind, own = value_types.INTEGER, 1
            expression_cache[key] = plus(own, *part_costs)
            kind_cache[key] = kind
        return expression_cache[root_key], kind_cache[root_key]

    total, active = 0, set()
    stack = [(cmd, 1, False)]
    while stack:
        node, multiplier, leaving = stack.pop()
        key = id(node)
        if leaving:
            active.remove(key)
            continue
        if key in active:
            raise ValueError("an IR command contains itself")
        active.add(key)
        stack.append((node, multiplier, True))
        total = plus(total, multiplier)
        if ceiling is not None and total >= ceiling:
            return ceiling
        if isinstance(node, Seq):
            stack.append((node.second, multiplier, False))
            stack.append((node.first, multiplier, False))
        elif isinstance(node, If):
            total = plus(total, times(multiplier, expression(node.guard)[0]))
            stack.append((node.orelse, multiplier, False))
            stack.append((node.then, multiplier, False))
        elif isinstance(node, For):
            total = plus(
                total,
                times(multiplier, expression(node.trip_count)[0]))
            stack.append((node.body, multiplier, False))
        elif isinstance(node, Assign):
            value_cost = expression(node.expr)[0]
            target = leaves(variable_types.get(node.var, value_types.INTEGER))
            total = plus(total, times(multiplier, plus(value_cost, target)))
        elif isinstance(node, Store):
            read_cost = plus(expression(node.index)[0],
                             expression(node.expr)[0])
            target = leaves(variable_types.get(node.var, value_types.INTEGER))
            total = plus(total, times(multiplier, plus(read_cost, target)))
    return total


def procedure_representation_cost(proc, limit=None, *, compact_arrays=False):
    """Representation work for one complete procedure.

    The command measure above accounts for every recursively represented value
    constructed while the step runs.  A symbolic step also starts by
    materialising its state and input boundary, so their scalar leaves are part
    of the procedure measure even when the body merely forwards them. Dense
    output construction is charged at assignments. A compact template instead
    counts array handles there and charges public output materialization below.

    This function selects no admission policy.  ``limit`` lets both the
    production gate and measurement tools compute the same quantity without
    first constructing a hostile product; the caller owns the ceiling.
    A compact template parameter costs one array handle; ordinary external
    input arrays retain their component-domain boundary costs.
    """
    if limit is not None:
        if type(limit) is not int or limit < 0:
            raise ValueError(f"a representation-cost limit is a nonnegative "
                             f"integer, not {limit!r}")
        ceiling = limit + 1
    else:
        ceiling = None

    from . import typecheck as value_types

    remaining = None if limit is None else limit
    body = representation_cost(proc.body, proc.types, limit=remaining, compact_arrays=compact_arrays)
    if ceiling is not None and body >= ceiling:
        return ceiling

    total = body
    boundaries = [(name, compact_arrays) for name in proc.state]
    boundaries.extend((name, False) for name in proc.inputs)
    if compact_arrays:
        # Public output buffers still materialize their declared components.
        boundaries.extend((name, False) for name in proc.output_vars.values())
    for name, compact_boundary in boundaries:
        # Use the same iterative type traversal as the command metric.  The
        # boundary materialises every carrier node and scalar leaf, not only
        # the product of the leaf cardinalities.
        kind = proc.types.get(name, value_types.INTEGER)
        cells, copies = 0, 1
        seen = set()
        while isinstance(kind, value_types.Array):
            if compact_boundary and kind.cells > 0 and kind.elem == value_types.INTEGER:
                break
            key = id(kind)
            if key in seen:
                raise ValueError("a representation type contains itself")
            seen.add(key)
            if ceiling is not None and copies >= ceiling - cells:
                cells = ceiling
            else:
                cells += copies
            if ceiling is not None:
                if copies == 0 or kind.cells == 0:
                    copies = 0
                elif (copies >= ceiling or kind.cells >= ceiling
                      or copies > (ceiling - 1) // kind.cells):
                    copies = ceiling
                else:
                    copies *= kind.cells
            else:
                copies *= kind.cells
            kind = kind.elem
        if ceiling is not None and copies >= ceiling - cells:
            cells = ceiling
        else:
            cells += copies
        if ceiling is not None and cells >= ceiling - total:
            return ceiling
        total += cells
    return total


def validate_procedure_representation(proc, *, limit=None, max_depth=None, compact_arrays=False):
    """Validate the complete representation before a recursive reader runs.

    The aggregate cost and structural depth are different capabilities.  A
    one-cell array nested many times has small cardinality but can still exhaust
    a recursive type or value walk.  Conversely, a shallow million-cell array
    has little depth and too much aggregate work.  Compute both iteratively,
    treating shared subgraphs as DAGs and a back-edge as a cycle.

    Returns the aggregate representation cost on success.  The function is the
    policy seam shared by lowering, planning, encoding and bounded queries; it
    does not allocate a solver term.
    """
    from . import capabilities as budgets
    from . import typecheck as value_types

    limit = budgets.TERM_BUDGET if limit is None else limit
    max_depth = (budgets.REPRESENTATION_DEPTH
                 if max_depth is None else max_depth)
    if type(limit) is not int or limit < 0:
        raise ValueError("a representation limit must be a nonnegative integer")
    if type(max_depth) is not int or max_depth < 1:
        raise ValueError("a representation depth must be a positive integer")

    try:
        spent = procedure_representation_cost(proc, limit=limit, compact_arrays=compact_arrays)
    except ValueError as exc:
        raise RepresentationStructureError(
            "representation-cycle", str(exc)) from None
    if spent > limit:
        raise RepresentationStructureError(
            "representation-budget-exceeded",
            f"the procedure needs more than {limit} representation operations")

    def children(node):
        if isinstance(node, Seq):
            return (node.first, node.second)
        if isinstance(node, If):
            return (node.guard, node.then, node.orelse)
        if isinstance(node, For):
            return (node.trip_count, node.body)
        if isinstance(node, Assign):
            return (node.expr,)
        if isinstance(node, Store):
            return (node.index, node.expr)
        if isinstance(node, Sel):
            return (node.array, node.index)
        if isinstance(node, Op):
            return node.args
        if isinstance(node, value_types.Array):
            return (node.elem,)
        if type(node) is list:
            # The validation walk never mutates concrete values.  Returning the
            # list itself keeps indexed traversal linear: materialising
            # ``tuple(node)`` here rebuilt the complete array for every cursor
            # position and made an n-cell initial memory cost O(n^2).
            return node
        return ()

    roots = [("command", proc.body, False)]
    roots.extend((f"type of {name}", kind, False)
                 for name, kind in getattr(proc, "types", {}).items())
    roots.extend((f"initial value of {name}", value, True)
                 for name, value in getattr(proc, "initial", {}).items()
                 if type(value) is list)

    # ``height`` memoizes completed DAG nodes.  A child reached again through
    # another parent reuses its height; the parent's max still accounts for the
    # longer path, while an edge to an active node is a real cycle.
    height, expanded = {}, {}
    objects = {}
    for label, root, check_expansion in roots:
        root_key = id(root)
        if root_key in height and objects.get(root_key) is root:
            if height[root_key] > max_depth:
                raise RepresentationStructureError(
                    "representation-depth-exceeded",
                    f"the {label} is nested {height[root_key]} levels, over "
                    f"the reported capability of {max_depth}")
            if check_expansion and expanded[root_key] > limit:
                raise RepresentationStructureError(
                    "representation-budget-exceeded",
                    f"the {label} expands past {limit} representation nodes")
            continue
        active = {root_key}
        objects[root_key] = root
        stack = [(root, 0)]
        while stack:
            node, cursor = stack[-1]
            key = id(node)
            parts = children(node)
            if cursor < len(parts):
                child = parts[cursor]
                stack[-1] = (node, cursor + 1)
                child_key = id(child)
                if child_key in height and objects.get(child_key) is child:
                    continue
                if child_key in active:
                    raise RepresentationStructureError(
                        "representation-cycle",
                        f"the {label} contains itself")
                active.add(child_key)
                objects[child_key] = child
                stack.append((child, 0))
                continue
            active.remove(key)
            stack.pop()
            # A Seq is the IR's list spine.  Every production reader flattens
            # it iteratively, and ordinary unrolled steps contain thousands of
            # such nodes.  It therefore contributes work (already charged
            # above) but no recursive nesting.  If/For, expression, type and
            # concrete-value edges do enter recursive readers and add a level.
            depth = 1
            if parts:
                depth = max(height[id(child)] for child in parts)
                if not isinstance(node, Seq):
                    depth += 1
            height[key] = depth
            total = 1
            for child in parts:
                child_cost = expanded[id(child)]
                if child_cost > limit - total:
                    total = limit + 1
                    break
                total += child_cost
            expanded[key] = total
            if depth > max_depth:
                raise RepresentationStructureError(
                    "representation-depth-exceeded",
                    f"the {label} is nested {depth} levels, over the reported "
                    f"capability of {max_depth}")
        if check_expansion and expanded[root_key] > limit:
            raise RepresentationStructureError(
                "representation-budget-exceeded",
                f"the {label} expands past {limit} representation nodes")

    # Bind every separate control-flow authority after structural cycles have
    # been excluded.  The outer graph and executable body are a sealed pair;
    # each retained loop additionally carries the local CFG from which its
    # definite-flow facts were derived.  Planning is a trust boundary, so it
    # must reject a changed graph or stale fact before computing an identity.
    _ = proc.graph
    from . import ssa
    pending = [proc.body]
    while pending:
        command = pending.pop()
        if isinstance(command, Seq):
            pending.extend((command.second, command.first))
        elif isinstance(command, If):
            pending.extend((command.orelse, command.then))
        elif isinstance(command, For):
            if command.flow is None:
                pending.append(command.body)
            else:
                # Validating this local graph recursively validates every
                # retained loop it contains.  Descending through the flattened
                # body as well would re-prove each nested loop once per ancestor.
                ssa.loop_flow_facts(command)
    return spent


def goals_of(cmd):
    """Every goal this command can report.

    Two commands report one: a ``mark`` reports the outcome of a site, and a
    ``fail`` reports the safety goal of the operation it guards. Collecting
    only from ``mark`` is how the whole safety class went missing from a
    report the moment the failure started carrying its own goal, so what counts
    as a goal-producing command is stated once, here.
    """
    return {c.goal for c in walk(cmd)
            if isinstance(c, (Mark, Fail)) and c.goal}


def _fails_name_a_goal(cmd):
    """Reasons a failure would be invisible to the closure.

    What this replaces was an analysis: it walked the program asking whether
    some mark appears before each failure, and the obligation is that every
    path reaching the failure passes one. A mark under a conditional before a
    failure satisfied it and should not have. Now that the failure carries its
    goal, the check is that it carries one.
    """
    return [f"fail({c.code}) reaches no goal" for c in walk(cmd)
            if isinstance(c, Fail) and not c.goal]


def _goal_contract_errors(cmd):
    """Malformed or ambiguously classified goal producers.

    ``Mark`` and ``Fail`` define the coverage/safety partition of a hand-built
    SolverIR procedure.  Letting one public identifier occur in both classes
    makes that partition depend on which later consumer happens to inspect it;
    letting a producer carry a non-string identifier makes the catalogue fail
    even later while sorting or serialising it.  Both are properties of the IR,
    so reject them at the common well-formedness boundary.
    """
    marked, failed = set(), set()
    out = []
    for command in walk(cmd):
        if isinstance(command, Mark):
            if type(command.goal) is not str or not command.goal:
                out.append("mark names no nonempty string goal")
            else:
                marked.add(command.goal)
        elif isinstance(command, Fail):
            if type(command.code) is not str or not command.code:
                out.append("fail has no nonempty string error code")
            if type(command.goal) is not str or not command.goal:
                # Preserve the established diagnostic used by callers and
                # tests for the ordinary missing-goal case.
                continue
            failed.add(command.goal)
    for goal in sorted(marked & failed):
        out.append(
            f"goal {goal!r} is emitted by both mark and fail, so it has no "
            "single coverage/safety class")
    return out




def expressions(cmd):
    """Every expression a command holds, at its root."""
    if isinstance(cmd, Assign):
        yield cmd.expr
    elif isinstance(cmd, Store):
        yield cmd.index
        yield cmd.expr
    elif isinstance(cmd, If):
        yield cmd.guard
    elif isinstance(cmd, For):
        yield cmd.trip_count


def expression_nodes(cmd):
    """Every expression node held by ``cmd``, without recursive descent.

    Lowered expressions are normally DAGs: a captured value can be reused by
    several commands or operands.  This traversal visits an object once and
    therefore bounds validation work by the representation that is actually
    held.  Command cycles are rejected by :func:`walk`'s downstream structural
    gates; expression cycles are simply not followed twice here and are
    rejected by the representation-cost authority before encoding.
    """
    seen = set()
    for command in walk(cmd):
        for root in reversed(tuple(expressions(command))):
            yield from _subterms(root, seen=seen)


def copy_command(cmd, memo=None):
    """Copy an IR DAG without making Python stack depth its semantic limit.

    Allocate command/expression images first, then use ordinary deepcopy for
    their fields with that shared memo. Nested branches and shared operands
    now resolve by identity, while types and loop proof metadata retain their
    existing copy rules. No mutable IR node is shared with its original.
    The caller may reuse the memo when copying procedure metadata.
    """
    from itertools import chain

    if memo is None:
        memo = {}
    originals = []
    for node in chain(walk(cmd), expression_nodes(cmd)):
        if type(node) not in CMDS + EXPRS:
            raise TypeError("an owned IR copy needs a known command or expression")
        if id(node) not in memo:
            memo[id(node)] = object.__new__(type(node))
            originals.append(node)
    for node in originals:
        image = memo[id(node)]
        for name in type(node).__slots__:
            object.__setattr__(image, name, copy.deepcopy(getattr(node, name), memo))
    return memo[id(cmd)]


def _subterms(expr, *, seen=None):
    """Expression objects once, in preorder; never expand DAG sharing as a tree.

    This is a context-free syntax walk, not evaluation. Command occurrences
    and reaching definitions must still be visited at each program position.
    The memo is local to a walk, so later IR edits cannot leave stale reads.
    """
    seen = set() if seen is None else seen
    stack = [expr]
    while stack:
        node = stack.pop()
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        yield node
        if isinstance(node, Op):
            stack.extend(reversed(node.args))
        elif isinstance(node, Sel):
            stack.extend((node.index, node.array))


def _pure(cmd):
    """Reasons a command holds something other than an expression.

    Expressions are pure, so a command sequence in an expression position has
    nowhere to run: whatever built it has to bind the value to a variable and
    put the commands before the command that reads it.
    """
    for root in expressions(cmd):
        for e in _subterms(root):
            if not isinstance(e, EXPRS):
                yield (f"{type(cmd).__name__} holds a "
                       f"{type(e).__name__} where an expression belongs")


def _reads(expr):
    """Variable dependencies of an expression, not operand occurrence counts."""
    for node in _subterms(expr):
        if isinstance(node, Var):
            yield node.name


# `_settles` was here: it asked the range analysis whether a guard is decided,
# so that a conditional could contribute what one branch assigns rather than
# what both do. It is gone, and so is the reason for it.
#
# The reason was that predication had already thrown away which paths exist. A
# loop whose trip count is a value has its body wrapped in `if counter < trips`
# and a return leaves a flag that the next conditional tests, so two guards
# that cannot both fail look independent. Recovering that with an interval
# analysis is answering a question the lowering knew and discarded, it costs
# more than the lowering itself on an unrolled step, and it is never finished:
# each construct loses the information its own way.
#
# What replaces it is not a better analysis. It is not throwing the information
# away: a control-flow graph, where a join is a join, and static single
# assignment over it, where a use either has a definition reaching it or does
# not. Then this is a property of the graph rather than something to infer from
# the term the graph was flattened into.


def _definite(cmd, held, loops=(), env=None):
    """What is certainly assigned after ``cmd``, given what was before it.

    The analysis "well-formed procedures denote functions" needs and did not
    have. A procedure denotes a function when the same inputs give the same
    outputs; a variable read on a path that did not assign it reads whatever
    was there, and the two readings of the representation are then free to fill
    that hole differently. They would agree with each other while both being
    wrong, which is the one class of fault the step audit cannot see.

    The same walk that reports the reads, with the reports thrown away. Two
    functions computing the same thing is two things that have to agree, and
    this file has already been through what that costs.
    """
    return _walk_assigned(cmd, held, loops, [], env)


def _reads_before_assignment(cmd, held, loops, out, env=None):
    """Reasons a variable is read on a path that has not assigned it.

    One pass. The first version asked ``_definite`` again for every step of a
    sequence, which recomputes the whole prefix at each step and is quadratic
    in the length of the program; on a model whose loops unroll to thousands of
    commands it did not finish. Reporting and computing what is assigned are
    the same walk, so it is one walk, and ``_definite`` is what a caller that
    only wants the set gets by discarding the reports.
    """
    _walk_assigned(cmd, held, loops, out, env)


def _walk_assigned(cmd, held, loops, out, env):
    """Report reads of unassigned names, and return what is assigned after."""
    if isinstance(cmd, Seq):
        # The parts in order, threading what is assigned through them. Written
        # as a loop for the reason every walk over a term here is: the nesting
        # is one node per command, so the depth would follow the length of the
        # unrolled step. `tests/sim/test_long_step.py` is what says so once
        # rather than a ninth model saying it again.
        for part in _parts(cmd):
            held = _walk_assigned(part, held, loops, out, env)
        return held
    if isinstance(cmd, If):
        for name in _reads(cmd.guard):
            if name not in held:
                out.append(f"{name} is read by a guard before it is assigned")
        after_then = _walk_assigned(cmd.then, held, loops, out, env)
        after_else = _walk_assigned(cmd.orelse, held, loops, out, env)
        return after_then & after_else
    if isinstance(cmd, For):
        for name in _reads(cmd.trip_count):
            if name not in held:
                out.append(
                    f"{name} is read by a loop count before it is assigned")
        if cmd.flow is not None:
            from . import ssa

            flow = ssa.loop_flow_facts(cmd)
            for name in flow.required:
                if name not in held:
                    out.append(
                        f"{name} is read in a retained loop before it is assigned")
            if flow.minimum > 0:
                return held | set(flow.defined) | (set(flow.entry_defined) - {cmd.index})
            return held
        # The body may see what an earlier iteration left.  It is checked once
        # with its entry definitions and once with the definitions one turn
        # can establish.  Only a positive literal proves that body-only
        # definitions exist after the loop; a dynamic count may be zero.
        inner = _walk_assigned(cmd.body, held | {cmd.index}, loops, [], env)
        _walk_assigned(cmd.body, held | {cmd.index} | inner,
                       loops + (cmd.index,), out, env)
        definitely_runs = (isinstance(cmd.trip_count, Lit)
                           and cmd.trip_count.value > 0)
        return (inner - {cmd.index}) if definitely_runs else held
    for expr in expressions(cmd):
        for name in _reads(expr):
            if name not in held:
                out.append(f"{name} is read before it is assigned")
    if isinstance(cmd, Assign):
        if cmd.var in loops:
            out.append(f"the loop index {cmd.var} is assigned in its own body")
        return held | {cmd.var}
    if isinstance(cmd, Store):
        if cmd.var not in held:
            out.append(f"{cmd.var} is read by a store before it is assigned")
        if cmd.var in loops:
            out.append(f"the loop index {cmd.var} is stored into")
        # Read-modify-write still leaves the array assigned after the command;
        # the read above is what requires the old value to exist first.
        return held | {cmd.var}
    return held


def normalized(proc, env=None):
    """Reasons ``proc`` is not in the shape the encoder expects.

    An empty list is the precondition of every later stage: the encoder builds
    one formula per command form, so a form outside this list would have no
    encoding and a failure outside this shape would have no goal.

    It is also what the appendix means by well-formed, and for a long time it
    checked much less than that. What it checked was shape: known commands,
    declared targets, pure expression positions. What "well-formed procedures
    denote functions" needs is that a run is determined by its inputs, and that
    needs definite assignment: an output left unassigned on some path reads
    whatever was there, and the interpreter and the encoder are then each free
    to fill the hole their own way. They would agree with each other while both
    being wrong, and the step audit compares exactly those two.
    """
    out = []
    names = set(proc.variables)
    if len(names) != len(proc.variables):
        out.append("a variable is declared in more than one role")

    for c in walk(proc.body):
        if not isinstance(c, CMDS):
            out.append(f"unknown command {type(c).__name__}")
            continue
        if isinstance(c, For) and not isinstance(c.trip_count, EXPRS):
            out.append("a loop trip count is not an expression")
        if isinstance(c, Assign) and c.var not in names:
            out.append(f"assignment to undeclared {c.var}")
        if isinstance(c, Store) and c.var not in names:
            out.append(f"store to undeclared {c.var}")
        if isinstance(c, Assign) and c.var in proc.inputs:
            out.append(f"input {c.var} is assigned")
        if isinstance(c, Store) and c.var in proc.inputs:
            out.append(f"input {c.var} is stored into")
        # A store is an element write, so its target has to be something with
        # elements. A scalar target would be a write the encoder has no rule
        # for and the interpreter would silently make into a list.
        if isinstance(c, Store) and c.var in proc.state \
                and not isinstance(proc.initial.get(c.var), list):
            out.append(f"store into {c.var}, which is not an array")
        out.extend(_pure(c))
        # Every name an expression reads must be declared. Reading an
        # undeclared name is not a scoping nicety here: the encoder would
        # invent a free variable for it, and a free variable makes any query
        # satisfiable for a reason the artifact does not have.
        for expr in expressions(c):
            for name in _reads(expr):
                if name not in names:
                    out.append(f"undeclared {name} is read")

    out.extend(_fails_name_a_goal(proc.body))
    out.extend(_goal_contract_errors(proc.body))

    # A run starts with the state and the inputs in hand and nothing else.
    start = set(proc.state) | set(proc.inputs)
    certain = _definite(proc.body, start, (), env)
    for port, var in proc.output_vars.items():
        if var not in certain:
            out.append(f"output {port} is not assigned on every path")
    # Shape and definite assignment are not the whole SolverIR contract.  Keep
    # the semantic checker on the same public well-formedness seam so an
    # integer guard cannot be called normalized and then refused only when a
    # backend consumes it.
    from . import typecheck as value_types

    out.extend(value_types.type_errors(proc))
    if proc.semantics is not None:
        # A lowered/admitted procedure already owns its immutable semantic
        # authority.  Well-formedness must validate that same object rather
        # than silently recomputing a parallel view; legacy hand-built test IR
        # remains bindable by the interpreter/encoder on first consumption.
        from .procedure_contract import ProcedureContractError
        from .validation import bind_procedure_contract

        try:
            bind_procedure_contract(proc)
        except ProcedureContractError as exc:
            out.append(f"the procedure's semantic contract is inconsistent: {exc}")
    return out


def may_read_unassigned(proc, env=None):
    """Variables read on some path that does not assign them.

    Kept apart from ``normalized`` and reported as what it is. The analysis is
    path-insensitive: it takes a conditional to assign what both branches
    assign and a loop to assign nothing, so a read under a guard that is always
    true when the write ran is reported here anyway. On the models that lower,
    it fires on about a quarter of them, and treating that as a well-formedness
    failure would be reporting the analysis's weakness as the artifact's.

    It is still worth having. What it points at is a temporary written inside a
    branch and read outside it, and whether that is safe depends on a guard
    nothing has checked. The interpreter would raise on a genuinely unassigned
    read and the encoder would invent a free variable, so the two would not
    even fail the same way.
    """
    out = []
    start = set(proc.state) | set(proc.inputs)
    _reads_before_assignment(proc.body, start, (), out, env)
    return out
