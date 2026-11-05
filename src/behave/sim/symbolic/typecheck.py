"""What each variable holds, as a fact about the graph.

The appendix says well-formed procedures denote functions, and the check that
claimed to say so did not look at types at all. Two things stood in for it. The
encoder decided array-from-value by what it happened to build, `isinstance(list)`
on a Python object, and raised when the two met: "`_t107` is an array on one
branch and a value on the other". And `normalized` refused a store into a
non-array only for state variables whose declared initial value was a list, so
every local and every temporary went unchecked.

Both are the same shape of mistake. A type is a property of the program, and it
was being discovered by running an encoding of the program. So a model with a
type error was refused at encode time, with no site, after the lowering had
already claimed it well-formed; and a model whose type error was in a local was
not refused at all until the values happened to meet.

The rule is that **a variable has one type in the step**, not one type per path.
That is not a simplification, it is what the representation is: the encoder
carries one valuation, so where two paths meet it has to merge what they left,
and an array meeting a value is a merge with no answer. The first version of this
file was flow-sensitive and reported a conflict only where something read it,
reasoning that a name can be an array on a path the use never comes down. It
missed fifty of the encoder's fifty-three type refusals, because the encoder
refuses at the merge and not at the read, and it is right to.

So the sweep is in topological order for the sake of seeing definitions before
uses, but what it builds is one type per name, and a second type for a name is
the error.

The lattice is as small as the semantic representation: a Boolean, an integer,
or a fixed array whose elements have one recursively defined type.  Boolean and
integer are deliberately different even though the exact encoder may assign
them the same one-bit pattern on a particular finite domain.  Representation is
not type identity: ``True`` cannot silently become integer ``1``, and an integer
used for Python truth testing must first pass through the explicit ``truth``
primitive emitted by the frontend.

The array recursion matters for hardware state such as banks of rows:
``Array(4, Array(8))`` is one value with shape ``(4, 8)``, not four unrelated
flat arrays.
"""

from __future__ import annotations

from . import cfg, ir, primitives

__all__ = [
    "BOOLEAN",
    "INTEGER",
    "VALUE",
    "Array",
    "is_type",
    "of_graph",
    "of_procedure",
    "of_shape",
    "of_value",
    "shape_of",
    "type_errors",
]


# Keep existing caller/checkpoint names bound to the shared type identities.
# Graph inference below is not part of the value representation.
from .types import (
    BOOLEAN,
    INTEGER,
    VALUE,
    Array,
    _Boolean as _Boolean,
    _Integer as _Integer,
    _Value,
    is_type,
    of_shape,
    of_value,
    shape_of,
)

_of_value = of_value


def _of_expression(expr, held, out, where):
    """The type of an expression, reporting what it requires and does not get."""
    if isinstance(expr, ir.Lit):
        natural = BOOLEAN if type(expr.value) is bool else INTEGER
        if expr.type is not None:
            if isinstance(expr.type, _Value):
                if expr.type != natural:
                    out.append(
                        f"a literal in {where} declares {expr.type} but carries "
                        f"{natural}")
                    return None
                return natural
            out.append(f"a literal in {where} declares unknown type "
                       f"{expr.type!r}")
            return None
        return natural
    if isinstance(expr, ir.Var):
        got = held.get(expr.name)
        if got is None:
            # Nothing has given it a type yet, which `undefined_uses` reports as
            # what it is. A type for it would be inventing one, so nothing is
            # claimed and the other check owns the message.
            return None
        return got
    if isinstance(expr, ir.Sel):
        array = _of_expression(expr.array, held, out, where)
        index = _of_expression(expr.index, held, out, where)
        if array is not None and not isinstance(array, Array):
            out.append(f"a subscript in {where} reads {array}, "
                       f"which has no cells")
        if index is not None and index != INTEGER:
            out.append(f"a subscript in {where} is indexed by {index}")
        return array.elem if isinstance(array, Array) else None
    if isinstance(expr, ir.Op):
        return _of_operator(expr, held, out, where)
    out.append(f"an expression in {where} of no known kind: "
               f"{type(expr).__name__}")
    return None


def _of_operator(expr, held, out, where):
    """Instantiate the registered type scheme of one primitive."""
    args = [_of_expression(arg, held, out, where) for arg in expr.args]
    try:
        spec = primitives.primitive(expr.op)
    except primitives.PrimitiveRegistryError:
        out.append(f"{expr.op} in {where} is not a registered primitive")
        return None
    terms = spec.signature.terms_for(len(args))
    if terms is None:
        out.append(f"{expr.op} in {where} has {len(args)} operands, not "
                   f"{len(spec.signature.arguments)}")
        return None

    same = None
    disagrees = False
    array_argument = None
    missing_same = False
    for term, got, argument in zip(terms, args, expr.args):
        concrete = {
            primitives.BOOLEAN: BOOLEAN,
            primitives.INTEGER: INTEGER,
            primitives.WIDTH: INTEGER,
            primitives.SHIFT: INTEGER,
            primitives.ARRAY_LENGTH: INTEGER,
        }.get(term)
        if concrete is not None:
            if got is not None and got != concrete:
                out.append(f"{expr.op} in {where} requires {concrete} but is "
                           f"applied to {got}")
                disagrees = True
            elif got == INTEGER:
                literal = isinstance(argument, ir.Lit)
                domain_error = primitives.argument_domain_error(
                    term,
                    literal=literal,
                    value=argument.value if literal else None,
                )
                if domain_error is not None:
                    out.append(f"{expr.op} in {where} {domain_error}")
                    disagrees = True
            continue
        if term is primitives.SCALAR:
            if got is not None and not isinstance(got, _Value):
                out.append(f"{expr.op} in {where} requires a scalar but is "
                           f"applied to {got}")
                disagrees = True
            continue
        if got is None:
            missing_same = True
            continue
        if term is primitives.INTEGER_TREE:
            leaf = got
            while isinstance(leaf, Array):
                leaf = leaf.elem
            if leaf != INTEGER:
                out.append(f"{expr.op} in {where} requires integer leaves, not {got}")
                disagrees = True
        if term is primitives.ARRAY:
            if not isinstance(got, Array):
                out.append(f"{expr.op} in {where} writes into {got}, "
                           "which has no cells")
                disagrees = True
                continue
            array_argument = got
            got = got.elem
        elif term is primitives.SAME_SCALAR and not isinstance(got, _Value):
            out.append(f"{expr.op} in {where} requires like-typed scalars but "
                       f"is applied to {got}")
            disagrees = True
            continue
        if same is None:
            same = got
        elif same != got:
            out.append(f"{expr.op} in {where} combines {same} and {got}")
            disagrees = True

    if disagrees:
        return None
    if spec.signature.result is primitives.BOOLEAN:
        return BOOLEAN
    if spec.signature.result is primitives.INTEGER:
        return INTEGER
    if spec.signature.result is primitives.SCALAR:
        return None
    if spec.signature.result in (primitives.SAME, primitives.SAME_SCALAR):
        return None if missing_same else same
    if spec.signature.result is primitives.ARRAY_OF_SAME:
        length = expr.args[-1]
        if not isinstance(length, ir.Lit) or type(length.value) is not int \
                or length.value < 0:
            return None
        return Array(length.value, INTEGER if same is None else same)
    if spec.signature.variadic:
        return Array(len(args), INTEGER if same is None else same)
    return array_argument


def of_graph(graph, start):
    """One type per name over the whole step, and every disagreement.

    ``start`` is what a run holds before it does anything: the inputs are values
    and a state variable is an array exactly when it was declared as one.

    Topological order, so that a definition is seen before the uses it reaches.
    The map is not per block: a name gets a type once and keeping a second one
    would be claiming the step can carry two, which is the thing the encoder
    cannot do.
    """
    held, out = dict(start), []
    for label in graph.order():
        block = graph.blocks[label]
        for instr in block.instrs:
            _of_instruction(instr, held, out, label)
        term = block.term
        if isinstance(term, cfg.Branch):
            got = _of_expression(term.guard, held, out, label)
            if got is not None and got != BOOLEAN:
                out.append(f"the branch in {label} is decided by {got}")
    return held, out


def _give(name, got, held, out, where):
    """Record that ``name`` holds ``got``, or say it already held something else.

    The whole of the rule that a variable has one type. A second type is not a
    join to be computed, because there is no type an array and a value are both:
    it is a program the representation cannot carry, and the encoder used to say
    so from inside a formula with no site to point at.
    """
    if got is None:
        return
    was = held.get(name)
    if was is None:
        held[name] = got
    elif was != got:
        out.append(f"{name} is {got} in {where} and {was} elsewhere")


def _of_instruction(instr, held, out, where):
    """Type one instruction, recording what it leaves."""
    if isinstance(instr, ir.For):
        got = _of_expression(instr.trip_count, held, out, where)
        if got is not None and got != INTEGER:
            out.append(f"the loop in {where} has a trip count of type {got}")
        _give(instr.index, INTEGER, held, out, where)
        # Type is a procedure-wide invariant rather than a path fact.  Visit
        # the one retained body once; nested control changes reachability, not
        # the representation a name may hold.
        for command in ir.walk(instr.body):
            if isinstance(command, ir.If):
                guard = _of_expression(command.guard, held, out, where)
                if guard is not None and guard != BOOLEAN:
                    out.append(f"a conditional in {where} is decided by {guard}")
            elif isinstance(command, ir.For):
                nested = _of_expression(
                    command.trip_count, held, out, where)
                if nested is not None and nested != INTEGER:
                    out.append(
                        f"a nested loop in {where} has a trip count of type "
                        f"{nested}")
                _give(command.index, INTEGER, held, out, where)
            elif isinstance(command, (ir.Assign, ir.Store)):
                _of_instruction(command, held, out, where)
        return
    if isinstance(instr, ir.Assign):
        _give(instr.var, _of_expression(instr.expr, held, out, where),
              held, out, where)
        return
    if isinstance(instr, ir.Store):
        target = held.get(instr.var)
        if target is None:
            # A store is the only thing that says what its target must be, and
            # nothing else has said. So it says it: the target has cells. How
            # many is not known here, and `Array(None)` would be a second kind
            # of array, so the length is left to whatever declared it and the
            # discipline checked is the one this instruction owns.
            out.append(f"a store into {instr.var} in {where}, which nothing "
                       f"declares as having cells")
        elif not isinstance(target, Array):
            out.append(f"a store into {instr.var} in {where}, which is "
                       f"{target} and has no cells")
        index = _of_expression(instr.index, held, out, where)
        value = _of_expression(instr.expr, held, out, where)
        if index is not None and index != INTEGER:
            out.append(f"the store into {instr.var} in {where} uses "
                       f"{index} as an index")
        if (isinstance(target, Array) and value is not None
                and value != target.elem):
            out.append(f"the store into {instr.var} in {where} writes "
                       f"{value} into cells of {target.elem}")


def of_procedure(proc, graph=None):
    """The type of each of the step's variables, and every disagreement.

    What the encoder needs and was inferring for itself. A state variable
    declared as a list in ``reset`` is an array of that many cells, and the
    encoder was giving it one bit-vector because nothing told it otherwise: the
    cells were dropped from the initial state and a store into it then failed for
    want of an array. The type is a fact about the program, so it is computed
    where the program is built and carried on it.
    """
    authority = proc.graph
    if graph is None:
        graph = authority if authority is not None else cfg.of_command(proc.body)
    # ``proc.types`` may carry declaration-owned state types into this pass.
    # They are hypotheses to check against every definition, not a completed
    # result to trust.  Without them a read-only array whose construction value
    # is intentionally unavailable has no defining command from which this
    # step-local pass could recover its cells.
    start = dict(proc.types)
    for name in proc.inputs:
        start.setdefault(name, INTEGER)
    initial_faults = []
    for name in proc.state:
        declared = proc.initial.get(name)
        if isinstance(declared, list):
            got = of_value(declared)
            if got is not None:
                _give(name, got, start, initial_faults, "the initial state")
            else:
                initial_faults.append(
                    f"the initial state {name} is a jagged or heterogeneous array")
        elif declared is not None:
            got = of_value(declared)
            if got is not None:
                _give(name, got, start, initial_faults, "the initial state")
        # A state variable with no construction value is left without an entry
        # type here.  A later definition may still establish its one procedure
        # type; ``entry_state`` separately proves whether that definition makes
        # the absent boundary value unobservable.
    held, faults = of_graph(graph, start)
    if authority is not None and graph is authority:
        # The frontend graph owns source control flow, while ``proc.body`` is
        # the executable if-converted image and consequently owns compiler
        # locals such as ``#reached:*`` and ``#taken:*``.  A procedure type
        # context must cover both.  Checking only the source graph made those
        # Boolean carriers invisible even though they were declared in
        # ``proc.locals`` and consumed by both backends.
        #
        # Seed the executable pass with the source result.  Repeated source
        # assignments then check against the same one-type-per-name invariant,
        # while the predication locals acquire their exact Boolean type from
        # their definitions.  This is one coherent procedure context, not a
        # name-prefix exception in the contract builder.
        held, executable_faults = of_graph(cfg.of_command(proc.body), held)
        faults.extend(executable_faults)
    return held, initial_faults + faults


def type_errors(proc, graph=None):
    """Reasons the step does not type, or an empty list.

    A conjunct of well-formedness rather than an exception from the encoder, so
    that a model with a type error is refused where it is written down and with
    the block it is in, instead of when two values of different kinds happen to
    meet inside an encoding.
    """
    return of_procedure(proc, graph)[1]
