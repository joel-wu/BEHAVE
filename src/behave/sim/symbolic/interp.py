"""Running the representation directly, on ordinary integers.

The chain from a model to a formula is two translations: the source becomes
the representation, and the representation becomes a formula. When a formula
and a run disagree, one of the two is wrong, and nothing in either one says
which. Running the representation itself is the third reading that separates
them: against the model it audits the lowering, and against the formula it
audits the encoding, and it reports the first command where the two part.

Values here are mathematical integers, exactly as the representation says: a
command is total, an operation is total, and nothing wraps unless the source
wrote a mask. That is the same reading the encoder gives at a width injective
on the ranges, so agreement is meaningful and disagreement is a fault in one
of them rather than in how two conventions were chosen.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ..core.instance import StepOutput
from . import ir, primitives
from .values import copy_value, value_matches_type, zero_value
from .procedure_contract import ProcedureContractError
from .validation import bind_procedure_contract
from .uniform_array import UniformArray

__all__ = ["Halt", "InterpError", "run_step", "run_trace_detailed", "trace_step"]


class InterpError(Exception):
    """A representation this cannot run, with the reason."""


class Halt(Exception):
    """The step failed, carrying the code of the failure."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Interp:
    def __init__(self, rho, watch=None):
        self.rho = rho
        # What each command left, in order, for comparing against a formula.
        self.watch = watch

    def value(self, e):
        """Evaluate either a scalar or a recursively nested array value."""
        if isinstance(e, ir.Lit):
            return e.value
        if isinstance(e, ir.Var):
            if e.name not in self.rho:
                raise InterpError(f"{e.name} is read before it is written")
            return copy_value(self.rho[e.name])
        if isinstance(e, ir.Sel):
            return copy_value(self._read(e))
        if isinstance(e, ir.Op):
            return self.primitive(e)
        raise InterpError(f"expression {type(e).__name__}")

    def _read(self, e):
        """Borrow array ancestors during selection, never across a command.

        Expressions do not mutate the environment. Only the selected value
        needs copying: copying every ancestor makes a dense scan quadratic.
        ``value`` still owns its result, including rows selected from matrices;
        primitive arguments and command/watch boundaries keep value semantics.
        """
        if isinstance(e, ir.Var):
            if e.name not in self.rho:
                raise InterpError(f"{e.name} is read before it is written")
            return self.rho[e.name]
        if isinstance(e, ir.Sel):
            cells = self._read(e.array)
            if not isinstance(cells, (list, UniformArray)):
                raise InterpError("an array whose cells are not known")
            index = self.integer(e.index)
            # Out of range reads the canonical value, the range check being a
            # command, exactly as the representation says.
            if 0 <= index < len(cells):
                return cells[index]
            return zero_value(cells[0]) if cells else 0
        return self.value(e)

    def expr(self, e):
        """Evaluate one scalar, preserving its exact semantic carrier."""
        value = self.value(e)
        if isinstance(value, (list, UniformArray)):
            raise InterpError("an array used where a value is expected")
        if type(value) not in (bool, int):
            raise InterpError("a non-SolverIR scalar used as a value")
        return value

    def boolean(self, e):
        value = self.expr(e)
        if type(value) is not bool:
            raise InterpError(
                f"an integer value {value!r} used where a Boolean is required")
        return value

    def integer(self, e):
        value = self.expr(e)
        if type(value) is not int:
            raise InterpError(
                f"a Boolean value {value!r} used where an integer is required")
        return value

    def cells(self, e):
        value = self.value(e)
        if isinstance(value, (list, UniformArray)):
            return value
        raise InterpError("an array whose cells are not known")

    def op(self, e):
        value = self.primitive(e)
        if isinstance(value, (list, UniformArray)):
            raise InterpError("an array used where a value is expected")
        return value

    def primitive(self, expression):
        """Evaluate one operation from its registered denotation."""
        try:
            spec = primitives.primitive(expression.op)
        except primitives.PrimitiveRegistryError as error:
            raise InterpError(str(error)) from None
        terms = spec.signature.terms_for(len(expression.args))
        if terms is None:
            raise InterpError(
                f"operation {expression.op} of {len(expression.args)} arguments")

        # ``ite`` is the one non-strict primitive: the unselected expression is
        # not read.  Its type scheme identifies it without a second name table.
        if (terms == (primitives.BOOLEAN, primitives.SAME, primitives.SAME)
                and spec.signature.result is primitives.SAME):
            condition = self.boolean(expression.args[0])
            selected = self.value(expression.args[1] if condition
                                  else expression.args[2])
            return spec.denotation(condition, selected, selected)

        values = []
        for term, argument in zip(terms, expression.args):
            if term is primitives.BOOLEAN:
                values.append(self.boolean(argument))
            elif term is primitives.INTEGER:
                values.append(self.integer(argument))
            elif term in (primitives.WIDTH, primitives.SHIFT) or term is primitives.ARRAY_LENGTH:
                value = self.integer(argument)
                domain_error = primitives.argument_domain_error(
                    term,
                    literal=isinstance(argument, ir.Lit),
                    value=value,
                )
                if domain_error is not None:
                    raise InterpError(
                        f"operation {expression.op} {domain_error}")
                values.append(value)
            elif term in (primitives.SCALAR, primitives.SAME_SCALAR):
                values.append(self.expr(argument))
            elif term is primitives.ARRAY:
                values.append(self.cells(argument))
            else:
                values.append(self.value(argument))
        return spec.denotation(*values)

    def cmd(self, c, hits):
        if isinstance(c, ir.Skip):
            return
        if isinstance(c, ir.Assign):
            value = self.value(c.expr)
            # An array is a value, so assigning one copies it. Y4's decision, and it is
            # a decision about the IR rather than about this module: binding the list
            # instead made two names hold one object, which reproduced Python's
            # reference semantics here by accident of this interpreter being written in
            # Python, while the encoder holds an array as a list of terms and cannot
            # reproduce it at all. One IR read two ways is the thing to remove.
            #
            # No program that lowers can tell the difference, because the frontend
            # refuses a write through a name another name shares an array with. This
            # keeps the two backends agreeing by construction rather than by nothing
            # happening to exercise it.
            self.rho[c.var] = copy_value(value)
            if self.watch is not None:
                self.watch.append((c, c.var, copy_value(value)))
            return
        if isinstance(c, ir.Store):
            cells = self.rho.get(c.var)
            if not isinstance(cells, (list, UniformArray)):
                raise InterpError(f"a store into {c.var}, not an array")
            index = self.integer(c.index)
            if type(cells) is UniformArray:
                self.rho[c.var] = cells.store(index, self.value(c.expr))
                cells = self.rho[c.var]
            elif 0 <= index < len(cells):
                cells[index] = self.value(c.expr)
            if self.watch is not None:
                self.watch.append((c, c.var, copy_value(cells)))
            return
        if isinstance(c, ir.Mark):
            hits.add(c.goal)
            return
        if isinstance(c, ir.Fail):
            # The goal is reached by the failure, so it is recorded before the
            # run stops. Doing it after would lose it, since nothing after a
            # halt runs, and the goal is the whole reason the failure is
            # reportable.
            if c.goal:
                hits.add(c.goal)
            raise Halt(c.code)
        raise InterpError(f"command {type(c).__name__}")

    def run(self, c, hits):
        """Run a command, with the sequencing on a stack rather than the frame.

        If-conversion can produce one long left-leaning chain of sequences.
        Recursing on those exhausted the interpreter's own stack on a quarter
        of the models, which is a limit of this loop and not of them.
        """
        work = [c]
        while work:
            node = work.pop()
            if isinstance(node, tuple):           # bind a loop index and go on
                self.rho[node[1]] = node[2]
                continue
            if isinstance(node, ir.Seq):
                work.append(node.second)
                work.append(node.first)
                continue
            if isinstance(node, ir.If):
                work.append(node.then if self.boolean(node.guard)
                            else node.orelse)
                continue
            if isinstance(node, ir.For):
                # Capture before the first iteration.  The body may overwrite
                # every variable occurring in the expression, including the
                # loop target itself; Python's range object has already been
                # constructed by then, so none of those writes changes the
                # number of iterations now due.
                trips = max(self.integer(node.trip_count), 0)
                for j in reversed(range(trips)):
                    work.append(node.body)
                    work.append(("index", node.index, j))
                continue
            self.cmd(node, hits)


def _semantics_of(proc):
    try:
        return bind_procedure_contract(proc)
    except ProcedureContractError as exc:
        raise InterpError(
            f"the procedure has no complete semantic contract: {exc}") from None


@dataclass(frozen=True)
class _PreparedProcedure:
    """Owned execution image for one source audit, not a global IR cache.

    The mutable compiler procedure is checked once and its execution fields
    copied together. Native calls and observation callbacks cannot change that
    image by retaining the original procedure. Per-step value/domain checks
    still run in the same interpreter used by the single-step entrypoint.
    """

    state: tuple
    inputs: tuple
    body: object
    initial: dict
    output_vars: dict
    count: str | None
    output_capacity: int | None
    semantics: object
    contract: object
    input_framing: tuple
    signature: object

    def run_step(self, inputs, state=None):
        return _run_step(self, self.semantics, inputs, state)


def _prepare_procedure(proc):
    semantics = _semantics_of(proc)
    memo = {}
    ir.copy_command(proc.body, memo)
    return _PreparedProcedure(*deepcopy((
        tuple(proc.state), tuple(proc.inputs), proc.body, proc.initial,
        proc.output_vars, proc.count, proc.output_capacity, semantics,
        proc.contract, proc.input_framing, proc.signature,
    ), memo))


def run_step(proc, inputs, state=None, watch=None):
    """Run one step, returning the state left, the outputs and the hits.

    Without a state the step starts where the harness starts it, which is what
    ``reset`` fixed. Starting anywhere else would be running something other
    than the model.

    A failure is not an exception of this module's making: the step halts with
    the code the representation carries, and the caller reads it off the
    result rather than from a traceback.
    """
    return _run_step(proc, _semantics_of(proc), inputs, state, watch)


def _run_step(proc, semantics, inputs, state=None, watch=None):
    # Preserve the direct scalar/array diagnostic before logical binding.  A
    # structured input has different public and SolverIR names and therefore
    # skips this branch; a legacy same-named input still reports its semantic
    # type (Boolean versus Integer) rather than a later layout symptom.
    if set(inputs) == set(proc.inputs):
        for name, kind in semantics.input_types:
            if not value_matches_type(inputs[name], kind):
                raise InterpError(
                    f"the input {name!r} does not carry its declared {kind}")
    try:
        inputs = semantics.validate_inputs(inputs)
    except ValueError as exc:
        raise InterpError(
            f"the input boundary violates its procedure domain: {exc}") from None
    if set(inputs) != set(proc.inputs):
        missing = sorted(set(proc.inputs) - set(inputs))
        extra = sorted(set(inputs) - set(proc.inputs))
        raise InterpError(
            f"the bound input leaves are incomplete "
            f"(missing={missing}, extra={extra})")
    for name, kind in semantics.input_types:
        if not value_matches_type(inputs[name], kind):
            raise InterpError(
                f"the input {name!r} does not carry its declared {kind}")

    # An array is a value here, and the state handed in belongs to the caller.
    # Copying the cells is what keeps a run from writing into the state it was
    # started from: without it, the first run leaves the declared initial state
    # holding what it computed, and every run after it starts somewhere else.
    rho = {k: copy_value(v)
           for k, v in (proc.initial if state is None else state).items()}
    for name, kind in semantics.state_types:
        if name not in rho or not value_matches_type(rho[name], kind):
            raise InterpError(
                f"the state {name!r} does not carry its declared {kind}")
    rho.update({name: copy_value(value) for name, value in inputs.items()})
    interp = _Interp(rho, watch)
    hits: set = set()
    error = ""
    try:
        interp.run(proc.body, hits)
    except Halt as halt:
        error = halt.code
        if error not in semantics.error_codes:
            raise InterpError(
                f"the step raised undeclared error code {error!r}") from None
    post = {n: copy_value(rho[n]) for n in proc.state if n in rho}
    # The buffers and how many of them this step filled. Reporting the buffers
    # alone made `return None` and `return 0` the same observation, since the
    # cells hold what they held either way and only the count says which of
    # them the step meant.
    #
    # A step that failed produced nothing: the count is whatever it was when
    # the failure cut the step short, and a halted step has not returned.
    physical_buffers = {port: copy_value(rho[var])
                        for port, var in proc.output_vars.items()
                        if var in rho}
    count = 0 if error else _count_of(proc, rho)
    if not error:
        try:
            buffers = semantics.reconstruct_outputs(
                count, physical_buffers,
                capacity=getattr(proc, "output_capacity", None))
        except ValueError as exc:
            raise InterpError(
                f"the output boundary violates its procedure domain: {exc}") \
                from None
    else:
        buffers = {}
    return post, StepOutput(count, buffers), hits, error


def _count_of(proc, rho):
    """How many transactions the step said it produced.

    Absent where the lowering did not build a count, which is a step whose
    returns are all a single transaction, and then one is the honest answer.
    """
    if proc.count is None:
        return 1
    value = rho.get(proc.count)
    return 0 if value is None else int(value)


def trace_step(proc, inputs, state=None):
    """Run one step and keep what each command left, in order."""
    watch: list = []
    return run_step(proc, inputs, state, watch) + (watch,)


def run_trace_detailed(proc, stimulus, state=None, *, observe_step=None):
    """Run a finite trace and retain every state, output batch and hit set.

    The returned state sequence has length ``len(stimulus) + 1``.  A modeled halt
    enters a terminal state: later finite-horizon steps stutter, emit no transactions
    and hit no new goals.  Keeping that structure here avoids reconstructing a witness
    from the aggregate ``run_trace`` result, which has already discarded when a goal
    was hit and which state followed each step.
    """
    hit_sets = []
    outputs = []
    state = {name: copy_value(value) for name, value in
             (proc.initial if state is None else state).items()}
    states = [{name: copy_value(value) for name, value in state.items()}]
    halted = ""
    frame_states = [framing.initial for framing, _projection in proc.input_framing]
    for inputs in stimulus:
        if halted:
            outputs.append(StepOutput(0, {}))
            hit_sets.append(set())
            states.append({name: copy_value(value)
                           for name, value in state.items()})
            continue
        for index, (framing, projection) in enumerate(proc.input_framing):
            frame_states[index], legal = framing.step(frame_states[index], {
                public: inputs[logical] for public, logical in projection
                if public in framing.fields})
            if not legal:
                raise ValueError("input-framing-violated")
        state, out, got, error = run_step(proc, inputs, state)
        outputs.append(out)
        observed = set(got)
        if observe_step is not None:
            observed.update(observe_step(inputs, out.transactions))
        hit_sets.append(observed)
        states.append({name: copy_value(value) for name, value in state.items()})
        halted = error
    return states, outputs, hit_sets, halted


def run_trace(proc, stimulus, state=None):
    """Run a sequence of steps, chaining the state, and collect the hits.

    This is what confirms a stimulus a solver produced. A model of a formula
    says a goal is reachable; running the stimulus it names says the same
    thing in the language everything else is measured in, and the two
    disagreeing means the formula is wrong rather than the model surprising.
    """
    states, outputs, hit_sets, halted = run_trace_detailed(
        proc, stimulus, state)
    return states[-1], outputs, set().union(*hit_sets), halted
