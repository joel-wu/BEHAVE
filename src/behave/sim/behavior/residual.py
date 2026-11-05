"""Deciding the goals a witness pool left undecided.

Running stimuli shows that a goal is reachable; nothing a run does shows that
one is not. That statement needs a formula covering every path of the step and
every stimulus the contract admits, and then an unsatisfiable query says what
no amount of running could: within the bound, no admissible stimulus reaches
this goal.

The query is per goal and is the same one each time. Unroll the step ``n``
times, chaining the state each leaves into the state the next reads, constrain
the inputs to what the interface admits, and ask whether the goal is marked at
any of the steps. Satisfiable gives a stimulus that reaches it, which is a
witness the caller can replay. Unsatisfiable at the bound is unreachability at
the bound, and it is reported as that rather than as unreachability outright.
Anything else is undecided, and saying so is the point: three answers, and the
third is not folded into either of the others.
"""

from __future__ import annotations

from ..symbolic import validation as validation_module
import math
import time
from dataclasses import dataclass

import z3

from ..symbolic.exact_formula import ExactFormula
from ..core.construction_checkpoint import ConstructionCheckpoint
from ..core import resources
from ..core.query import QueryCall, SolverAnswer
from ..core.query_telemetry import (
    QueryTelemetryScope,
    StageTimings,
    process_peak_rss_bytes,
)
from . import budgets as budgets_mod
from ..symbolic import encoder as encoder_mod
from .budgets import QUERY_MS
from ..symbolic.encoder import validated_width
from ..symbolic.bitvectors import widen
from ..symbolic.validation import EncodingError
from .exact_solver import solve_exact, solve_exact_components

__all__ = ["Residual", "decide_goals", "unroll"]


class _ConstructionExpired(TimeoutError):
    """A bounded-query owner stopped construction, not a solver verdict."""


def _budget_checkpoint(budget):
    def check():
        reason = budget.exhausted()
        if reason:
            raise _ConstructionExpired(reason)
    return check


def _construction_unknown(goals, reason):
    """No incomplete formula has SAT/UNSAT or physical-query authority."""
    return {}, set(), dict.fromkeys(goals, reason), tuple(
        QueryCall((goal,), SolverAnswer.UNKNOWN, reason=reason, solver_calls=0)
        for goal in goals)


class Residual:
    """What the queries decided, goal by goal."""

    __slots__ = ("queries", "reachable", "steps", "undecided", "unreachable")

    def __init__(self, reachable, unreachable, undecided, steps, queries=()):
        self.reachable = dict(reachable)      # goal -> the stimulus reaching it
        self.unreachable = set(unreachable)   # goal -> no stimulus at the bound
        self.undecided = dict(undecided)      # goal -> why it stayed open
        self.steps = steps
        self.queries = tuple(queries)

    def __repr__(self):
        return (f"Residual(reachable={len(self.reachable)}, "
                f"unreachable={len(self.unreachable)}, "
                f"undecided={len(self.undecided)}, steps={self.steps})")


class Unrolling:
    """A run of a fixed number of steps, with failure absorbed.

    A step that fails ends the run. What follows it is not another step: the
    state stands still, nothing is produced, and no goal is reached, for as
    many steps as the bound declares. Traces therefore all have the length the
    bound names, which is what makes a statement about runs of that length a
    statement about one set rather than about several.

    Without this the formula admits what no run does. Chaining the state past
    a failure lets the solver satisfy "the first step failed and the second
    completed", and a goal reached only after such a step would be called
    reachable on the strength of a run that cannot happen.
    """

    __slots__ = (
        "_dependency_cones_cache",
        "_dependency_masks_cache",
        "_dependency_roots_cache",
        "_goal_components_cache",
        "_goal_conditions_cache",
        "constraint",
        "discharged",
        "encodings",
        "error",
        "errors",
        "formula",
        "halted",
        "output_boundaries",
        "query_scope",
        "state_boundaries",
        "steps",
        "transaction_components",
        "transaction_states",
    )

    def __init__(self, steps, encodings, formula, halted, errors,
                 state_payloads, output_payloads, discharged=(),
                 query_scope=None):
        self.steps = steps
        self.encodings = encodings
        if type(formula) is not ExactFormula:
            raise TypeError("an unrolling needs an exact registered formula")
        self.formula = formula
        # Query planning revisits the same public goals and occurrence terms as
        # an unresolved set is refined.  These are views of this immutable
        # unrolling, so derive each view once instead of rebuilding identical
        # Z3 terms and definition closures at every refinement level.
        self._goal_components_cache = {}
        self._goal_conditions_cache = {}
        self._dependency_roots_cache = {}
        self._dependency_cones_cache = {}
        self._dependency_masks_cache = {}
        self.discharged = frozenset(str(goal) for goal in discharged)
        if query_scope is not None:
            query_scope = frozenset(query_scope)
            if any(type(goal) is not str or not goal for goal in query_scope):
                raise TypeError("an unrolling query scope names nonempty strings")
        self.query_scope = query_scope
        self.transaction_components = {}
        self.transaction_states = ()
        constraints = formula.full_constraints
        self.constraint = z3.And(constraints) if constraints else z3.BoolVal(True)
        # ``halted[t]`` is whether the run had already ended before step t, so
        # it has one more entry than there are steps and starts false.
        self.halted = tuple(halted)
        self.errors = tuple(errors)
        self.error = self.errors[-1]  # the code the run halted with, or ""
        # The absorbed state/output encoding used by the paper is represented
        # structurally rather than packed into one bit-vector.  The Boolean tag
        # is disjoint: ``normal=False`` selects the terminal error payload, and
        # ``present=False`` is exactly the output bottom value.  Normal payloads
        # remain allocated after halt only as irrelevant padding terms.
        self.state_boundaries = tuple(
            _AbsorbedStateEncoding(
                encoder_mod._neg(self.halted[position]),
                state_payloads[position],
                self.errors[position])
            for position in range(steps + 1))
        self.output_boundaries = tuple(
            _AbsorbedOutputEncoding(
                encoder_mod._conj(encoder_mod._neg(self.halted[position]),
                                  encodings[position].ok),
                output_payloads[position])
            for position in range(steps))

    @property
    def completed(self):
        """The run reached the bound without failing."""
        return encoder_mod._neg(self.halted[self.steps])

    @property
    def goals(self):
        """The goals every step of this run encodes.

        The intersection and not the union: a disjunction over the steps is only a
        statement about the run if each disjunct is a statement about its step. A goal
        one step encodes and another does not would contribute a silent `False` for the
        second, which is the defect this whole pair of methods exists to close, moved to
        where it is harder to see.
        """
        if not self.encodings:
            return self.discharged
        got = frozenset(self.encodings[0].goals)
        for enc in self.encodings[1:]:
            got &= enc.goals
        return got | self.discharged | self.transaction_components.keys()

    def supports(self, goal):
        return goal in self.transaction_components or goal in self.discharged or (
            bool(self.encodings)
            and all(e.supports(goal) for e in self.encodings))

    def preselected(self, goals):
        """Whether this is exactly the command cone audited for ``goals``.

        This checks admission of the initial question. The complete-selection
        coordinator can then refine subsets against that same formula; other
        queries retain their ordinary definition-cone slicing path.
        """

        goals = frozenset(goals)
        return self.query_scope is not None and goals == self.query_scope

    def goal_condition(self, goal):
        """The run reaches ``goal`` at some step it actually took.

        A step that fails still reaches the goal that reports the failure, so
        what a step's hits are conditioned on is that the run had not ended
        before it, not that it ended well.

        A source-declared goal that lowering explicitly discharged denotes ``False``:
        its invalid branch was proved unreachable on the finite carrier before the
        formula was built.  Any other absent goal still raises.  Unasked is not
        unreachable.
        """
        if not self.supports(goal):
            from ..symbolic.validation import EncodingError

            raise EncodingError(
                f"{goal!r} is not a goal this unrolling encodes at every step, so a "
                f"disjunction over the steps would be a claim about the goals it does "
                f"encode and not about this one",
                kind="goal")
        condition = self._goal_conditions_cache.get(goal)
        if condition is None:
            condition = z3.Or(self.goal_components(goal))
            self._goal_conditions_cache[goal] = condition
        return condition

    def goal_components(self, goal):
        """Occurrence-level disjuncts of one public goal over this run."""

        if goal in self.transaction_components:
            return self.transaction_components[goal]
        if not self.supports(goal):
            raise EncodingError(
                f"{goal!r} is not encoded by every step of this unrolling; "
                "there are no occurrence conditions to assert",
                kind="goal")
        cached = self._goal_components_cache.get(goal)
        if cached is not None:
            return cached
        # Production lowering removes occurrence-local discharges when the
        # same source-site identifier has any live inline occurrence.  Keep
        # that precedence here too for hand-built or older procedures: an
        # encoded occurrence is evidence, so a side-table discharge can
        # denote false only when no encoded occurrence exists.
        if goal in self.discharged and not any(
                encoding.supports(goal) for encoding in self.encodings):
            components = (z3.BoolVal(False),)
        else:
            components = tuple(
                encoder_mod._conj(encoder_mod._neg(self.halted[t]), occurrence)
                for t, enc in enumerate(self.encodings)
                for occurrence in enc.goal_components(goal)
            )
        self._goal_components_cache[goal] = components
        return components

    def dependency_roots(self, target):
        """Cached direct definition roots for one occurrence condition."""

        identity = target.get_id()
        if identity not in self._dependency_roots_cache:
            self._dependency_roots_cache[identity] = \
                self.formula.dependency_roots(target)
        return self._dependency_roots_cache[identity]

    def dependency_cone(self, target):
        """Cached transitive definition cone for one occurrence condition."""

        return self.dependency_cones((target,))[0]

    def dependency_cones(self, targets):
        """Discover missing cones together, sharing their exact adjacency walk."""
        targets = tuple(targets)
        missing = {target.get_id(): target for target in targets
                   if target.get_id() not in self._dependency_cones_cache}
        cones = self.formula.dependency_cones(tuple(missing.values()))
        self._dependency_cones_cache.update(zip(missing, cones))
        return tuple(self._dependency_cones_cache[target.get_id()] for target in targets)

    def dependency_masks(self, targets):
        """Cache packed cones for scheduling without retaining label sets."""
        targets = tuple(targets)
        missing = {target.get_id(): target for target in targets
                   if target.get_id() not in self._dependency_masks_cache}
        masks = self.formula.dependency_masks(tuple(missing.values()))
        self._dependency_masks_cache.update(zip(missing, masks))
        return tuple(self._dependency_masks_cache[target.get_id()] for target in targets)

    def inputs_at(self, step, name):
        return self.encodings[step].pre[name]

    def output_violations(self):
        """Named output-contract violations on steps the run actually takes."""
        return [
            (step, name,
             z3.And(z3.Not(self.halted[step]), encoding.ok,
                    z3.Not(condition)))
            for step, encoding in enumerate(self.encodings)
            for name, condition in encoding.output_checks.items()
        ]

    @property
    def invalid_output(self):
        """Some normally completing step emits outside its task signature."""
        violations = [condition for _step, _name, condition
                      in self.output_violations()]
        return z3.Or(violations) if violations else z3.BoolVal(False)


class _AbsorbedStateEncoding:
    """Tagged symbolic state: a normal valuation or one terminal error."""

    __slots__ = ("error", "normal", "value")

    def __init__(self, normal, value, error):
        self.normal = normal
        self.value = dict(value)
        self.error = error


class _AbsorbedOutputEncoding:
    """Tagged symbolic output: a normal valuation or the bottom output."""

    __slots__ = ("present", "value")

    def __init__(self, present, value):
        self.present = present
        self.value = dict(value)


_PREPARED_UNROLL_TOKEN = object()


class _PreparedUnroll:
    """Validated, invocation-local inputs shared by structural query fragments.

    Occurrence decomposition builds several independent formulas for one public
    bounded question.  Rewalking the immutable procedure and revalidating the
    same position plan before every fragment is pure duplicate work.  This
    private value carries exactly those checks; it contains no Z3 terms and
    grants no caller authority to choose a command slice.
    """

    __slots__ = (
        "environment", "plan", "plan_prevalidated", "position_plan",
        "position_steps", "procedure", "slice_index", "steps", "token",
        "validation",
    )

    def __init__(self, procedure, environment, steps, plan, validation,
                 position_plan, position_steps, plan_prevalidated, token):
        if token is not _PREPARED_UNROLL_TOKEN:
            raise TypeError("a prepared unrolling is verifier-owned")
        self.procedure = procedure
        self.environment = dict(environment)
        self.steps = steps
        self.plan = plan
        self.validation = validation
        self.position_plan = position_plan
        self.position_steps = position_steps
        self.plan_prevalidated = plan_prevalidated
        self.slice_index = None
        self.token = token

    def checked_for(self, procedure, environment, steps, plan):
        if self.token is not _PREPARED_UNROLL_TOKEN:
            raise TypeError("the prepared-unrolling seal is invalid")
        if procedure is not self.procedure:
            raise ValueError("a prepared unrolling belongs to another procedure")
        if dict(environment) != self.environment:
            raise ValueError("a prepared unrolling belongs to another environment")
        if steps != self.steps:
            raise ValueError("a prepared unrolling belongs to another horizon")
        if plan is not self.plan:
            raise ValueError("a prepared unrolling belongs to another encoding plan")
        validation_module._checked_procedure_validation(self.validation, procedure)
        return self

    def slicing(self):
        """Return the one dependency index shared by all query fragments."""

        if self.slice_index is None:
            from ..symbolic import slicing

            self.slice_index = slicing.index(self.procedure)
        return self.slice_index.checked_for(self.procedure)


def _prepare_unroll(proc, env, steps, plan, *, _checkpoint=None, _procedure_validation=None):
    """Validate one bounded encoding before constructing any query fragments."""

    if _checkpoint is not None:
        _checkpoint()
    validation = (validation_module._procedure_validation(proc) if _procedure_validation is None
                  else validation_module._checked_procedure_validation(_procedure_validation, proc))
    position_plan = None
    position_steps = None
    plan_prevalidated = False
    if plan is not None:
        from ..symbolic.plan import EncodingPlan
        from ..symbolic.closure_proof import _checked_position_plan, _PreparedPositionPlan

        if type(plan) is EncodingPlan:
            plan.validate(proc, env, bound=steps)
            plan_prevalidated = True
        else:
            if type(plan) is _PreparedPositionPlan:
                position_plan = plan.checked_for(
                    proc, env, steps, _procedure_validation=validation)
            else:
                position_plan = _checked_position_plan(plan, proc, env, steps)
            plan_prevalidated = True
            position_steps = tuple(
                step.validate_binding(
                    proc, position_plan.environment_at(t), bound=steps,
                    _procedure_validation=validation)
                for t, step in enumerate(position_plan.steps))
    if _checkpoint is not None:
        _checkpoint()
    return _PreparedUnroll(
        proc, env, steps, plan, validation, position_plan, position_steps,
        plan_prevalidated, _PREPARED_UNROLL_TOKEN)


@dataclass(frozen=True)
class _RunPrefix:
    """Completed transitions, never a solver answer or a caller deadline.

    A longer exact query can share this prefix only when its source, entry,
    dependency cone, framing and already encoded input restrictions agree.
    Position carrier plans and selected-occurrence queries remain horizon-owned.
    Ordinary all-occurrence questions compare their command cone at each step.
    """

    binding: tuple
    inputs: tuple | None
    encodings: tuple
    boundary_atoms: tuple
    frame_constraints: tuple
    state_payloads: tuple
    output_payloads: tuple
    halted: tuple
    errors: tuple
    keeps: tuple
    boundary_counts: tuple
    frame_layers: tuple

    def reusable(self, binding, inputs, keeps):
        if self.binding[0] is not binding[0] or self.binding[1:] != binding[1:]:
            return 0
        if (inputs is None) != (self.inputs is None):
            return 0
        length = 0
        for position, (old, new) in enumerate(zip(self.keeps, keeps)):
            if old != new or (inputs is not None and self.inputs[position] != inputs[position]):
                break
            length += 1
        return length


class _RunConstruction:
    """One run's suspended steps and completed formula, never a solver answer."""

    def __init__(self, previous=None):
        if previous is not None and type(previous) is not _RunConstruction:
            raise TypeError("a run prefix belongs to another construction owner")
        self.history = None if previous is None else previous.completed
        self.formula = None if previous is None else previous.formula
        self.completed = None
        self.reused_steps = 0
        self.iterator = self.result = self.prefix = None
        self.checkpoint = ConstructionCheckpoint()
        self.plan_checkpoint = ConstructionCheckpoint()

    def advance(self, arguments, checkpoint, plan_checkpoint, on_prefix):
        self.checkpoint.callback = checkpoint
        self.plan_checkpoint.callback = plan_checkpoint
        try:
            if self.result is not None:
                return self.result
            if self.iterator is None:
                self.iterator = _unroll_iter(**arguments,
                    _checkpoint=self.checkpoint,
                    _plan_checkpoint=self.plan_checkpoint,
                    _on_prefix=on_prefix is not None,
                    _history_owner=self)
            while True:
                if self.prefix is not None:
                    on_prefix(*self.prefix)
                    self.prefix = None
                yielded = next(self.iterator)
                if yielded is None:
                    return None
                self.prefix = yielded
        except StopIteration as done:
            self.result, self.iterator = done.value, None
            return self.result
        except encoder_mod._EncodingPaused:
            # The observer also performs resumable formula registration.
            return None
        except BaseException:
            self.iterator = self.prefix = None
            raise
        finally:
            self.checkpoint.callback = self.plan_checkpoint.callback = None


def unroll(proc, env, steps, initial=None, plan=None, *, goals=None,
           _occurrence=None, _prepared=None, _shared_occurrences=None,
           transactions=None, _checkpoint=None, _plan_checkpoint=None,
           _on_prefix=None, _input_values=None, _construction=None, _frame_initial=None):
    """Encode a run, or advance a private Search owner's unfinished run.

    Ordinary callers still receive a complete Unrolling or an exception. A
    private owner may receive None at an encoding quantum boundary; it cannot
    publish a partial run as an exact answer. Its binding belongs to the query.
    """
    if _on_prefix is not None and (goals is None or not callable(_on_prefix)):
        raise TypeError("a Search prefix observer requires explicit query goals")
    arguments = dict(proc=proc, env=env, steps=steps, initial=initial, plan=plan,
        goals=goals, _occurrence=_occurrence, _prepared=_prepared,
        _shared_occurrences=_shared_occurrences, transactions=transactions,
        _input_values=_input_values, _frame_initial=_frame_initial)
    plan_check = _plan_checkpoint if _plan_checkpoint is not None else _checkpoint
    if _construction is not None:
        return _construction.advance(arguments, _checkpoint, plan_check, _on_prefix)
    # Ordinary unrolling keeps absent callbacks absent. It needs no retained
    # owner or extra per-expression checkpoint layer.
    iterator = _unroll_iter(**arguments, _checkpoint=_checkpoint,
                           _plan_checkpoint=plan_check, _on_prefix=_on_prefix is not None)
    try:
        while True:
            prefix = next(iterator)
            if prefix is not None:
                _on_prefix(*prefix)
    except StopIteration as done:
        return done.value
    finally:
        iterator.close()


def _unroll_iter(proc, env, steps, initial=None, plan=None, *, goals=None,
           _occurrence=None, _prepared=None, _shared_occurrences=None,
           transactions=None, _checkpoint=None, _plan_checkpoint=None,
           _on_prefix=None, _input_values=None, _frame_initial=None,
           _history_owner=None):
    """Encode ``steps`` consecutive steps, chained through the state.

    Each step reads the state the one before it left, so a goal reachable only
    after the model has been driven for a while is reachable in the formula
    too. The inputs of each step are its own, since a stimulus is a sequence.

    A legacy :class:`EncodingPlan` fixes one joined carrier for every step.  A
    :class:`PositionEncodingPlan` instead supplies the checked semantic carrier
    at each boundary while using one scalar encoding across the horizon.  A
    harmless in-carrier value pads the component state after an absorbed
    failure.  In either case validation finishes before the run formula is
    allocated; validating a position plan replays its independent closure
    queries.
    """
    check = _checkpoint if _checkpoint is not None else lambda: None
    # An occurrence's short clock owns its formula, while the artifact owns
    # shared planning. Keep absent callbacks absent on the per-expression path.
    plan_check = _plan_checkpoint if _plan_checkpoint is not None else _checkpoint
    yield from encoder_mod._retry_encoding(check)
    if steps < 1:
        raise ValueError("a run of no steps decides nothing")
    if transactions is not None and goals is not None:
        raise ValueError("transaction history requires the complete step relation")
    if _on_prefix and goals is None:
        raise TypeError("a Search prefix observer requires explicit query goals")
    if _input_values is not None:
        _input_values = tuple(dict(row) for row in _input_values)
        if goals is None or len(_input_values) != steps \
                or any(not set(row) <= set(proc.inputs) for row in _input_values):
            raise ValueError("fixed query inputs bind declared inputs at every step")
    # Refuse hostile public procedures before the slicer, plan fingerprint,
    # boundary-layout readers, or Z3 sees them.  Retain invocation-local
    # evidence so the same immutable procedure is not walked again once per
    # encoded step.
    prepared = (_prepare_unroll(proc, env, steps, plan, _checkpoint=plan_check)
                if _prepared is None
                else _prepared.checked_for(proc, env, steps, plan))
    validation = prepared.validation
    query_goals = None
    query_keep = None
    query_keeps = None
    if _occurrence is not None and _shared_occurrences is not None:
        raise TypeError("an occurrence unrolling has one selection authority")
    if (_occurrence is not None or _shared_occurrences is not None) and goals is None:
        raise TypeError("an occurrence unrolling must name its public goal")
    if goals is not None:
        query_goals = tuple(goals)
        if any(type(goal) is not str or not goal for goal in query_goals):
            raise TypeError("an unrolling query names nonempty string goals")
        from ..symbolic import slicing

        slice_index = prepared.slicing()
        events = slice_index.events
        if _shared_occurrences is not None:
            query_keeps = slicing.shared_occurrence_keeps(
                proc, query_goals, steps, _shared_occurrences)
            candidate = None
        elif _occurrence is None:
            query_keeps = slicing.run_positions(proc, query_goals, steps, _index=slice_index)
            candidate = None
        else:
            if len(query_goals) != 1:
                raise TypeError("an occurrence unrolling names exactly one goal")
            if type(_occurrence) is slicing.OccurrenceRunSlice:
                occurrence = _occurrence.checked_for(
                    proc, query_goals[0], steps)
                target_step, sites = occurrence.target_step, occurrence.sites
                query_keeps = occurrence.keeps
                faults = slicing.audit_occurrence_run(
                    proc, query_goals[0], sites, target_step, steps, occurrence,
                    events=events, _index=slice_index)
            else:
                if type(_occurrence) not in (tuple, list) \
                        or len(_occurrence) != 2:
                    raise TypeError(
                        "an occurrence unrolling needs a sealed slice or "
                        "(step, sites)")
                target_step, sites = _occurrence
                query_keeps = slicing.for_occurrence_run(
                    proc, query_goals[0], sites, target_step, steps,
                    events=events, _index=slice_index)
                faults = slicing.audit_occurrence_run(
                    proc, query_goals[0], sites, target_step, steps, query_keeps,
                    events=events, _index=slice_index)
            if faults:
                raise EncodingError(
                    "the occurrence-run cone is not closed: " + faults[0],
                    kind="goal")
            candidate = None
        if query_keeps is None:
            if candidate is None:
                # A frontend may discharge a declared goal without retaining
                # an IR occurrence.  Its exact condition is false, so the empty
                # command cone is the complete query.  An unknown absent goal
                # instead uses the whole step and remains unsupported below.
                discharged = set(proc.discharged) | set(proc.impossible)
                if query_goals and set(query_goals) <= discharged:
                    query_keep = frozenset()
            elif not slicing.audit(
                    proc, query_goals, candidate, events=events,
                    _index=slice_index):
                query_keep = frozenset(candidate)
    position_plan = prepared.position_plan
    position_steps = prepared.position_steps
    plan_prevalidated = prepared.plan_prevalidated
    initial_env = (position_plan.environment_at(0)
                   if position_plan is not None else env)
    reset_origin = initial is None
    if initial is None:
        # A run starts where the harness starts it. Leaving the first step's
        # state free asks whether some state reaches the goal, which is a
        # different question and answers it with stimuli no run can present.
        # An array's cells go in as its cells. They used to be dropped, which
        # left a state variable declared as a list with no cells in the formula:
        # a store into it could not be encoded, so every model with a table in
        # its reset was refused rather than asked about.
        state_types = dict(proc.semantics.state_types)
        initial = {
            name: encoder_mod.encode_concrete_value(
                value, initial_env.get(name), name,
                kind=state_types.get(name),
                width=(position_plan.scalar_width
                       if position_plan is not None else None))
            for name, value in proc.initial.items() if name in initial_env}
    encodings, pre = [], dict(initial)
    boundary_atoms = []
    from ..core.input_framing import symbolic_initial, symbolic_step

    frame_states = [symbolic_initial(framing) for framing, _projection in proc.input_framing]
    if _frame_initial is not None:
        if len(_frame_initial) != len(frame_states):
            raise ValueError("input-window framing entry must cover every framed endpoint")
        for index, ((framing, _projection), values) in enumerate(
                zip(proc.input_framing, _frame_initial, strict=True)):
            if len(values) != len(framing.levels) or any(
                    type(value) is not int or not 0 <= value < level.max_count
                    for value, level in zip(values, framing.levels, strict=True)):
                raise ValueError("input-window framing entry has invalid counters")
            frame_states[index] = tuple(z3.BitVecVal(value, term.size())
                                        for value, term in zip(values, frame_states[index], strict=True))
    frame_constraints = []

    def register_boundary(value):
        from ..symbolic.functional_array import FunctionalArray

        if isinstance(value, FunctionalArray):
            for item in value.formula_terms:
                register_boundary(item)
            return
        if isinstance(value, list):
            for item in value:
                register_boundary(item)
            return
        if (z3.is_const(value)
                and value.decl().kind() == z3.Z3_OP_UNINTERPRETED):
            boundary_atoms.append(value)

    for value in pre.values():
        register_boundary(value)
    boundary_counts = [len(boundary_atoms)]
    frame_layers = [tuple(frame_states)]
    state_payloads = [dict(pre)]
    output_payloads = []
    halted = [z3.BoolVal(False)]
    errors = [encoder_mod._code("", proc.semantics.error_codes)]
    # This owner sees only already-retained reset-origin equations. Preserve
    # their cheap syntax bounds at the next entry instead of replacing masked
    # data by every value of a much wider physical carrier. Public arbitrary
    # initial states and explicit plans keep their original validation path.
    definitions, selections = [], []
    history_binding = None
    if (_history_owner is not None and reset_origin and plan is None
            and query_goals is not None and _occurrence is None and _shared_occurrences is None
            and transactions is None):
        # Extending a horizon may widen its former terminal cone. Retain only
        # the equal leading transitions, not the obsolete tail or its formula.
        from ..symbolic.plan import _environment_image
        from .value_records import domain_tree
        from ..core.record_value import freeze_record_value

        history_binding = (proc, _environment_image(env), query_goals,
                           freeze_record_value(domain_tree(proc.initial)),
                           freeze_record_value(_frame_initial))
        history_keeps = query_keeps if query_keeps is not None else (query_keep,) * steps
        history = _history_owner.history
        reused = 0 if history is None else history.reusable(history_binding, _input_values, history_keeps)
        if reused:
            encodings = list(history.encodings[:reused])
            pre = dict(history.state_payloads[reused])
            boundary_atoms = list(history.boundary_atoms[:history.boundary_counts[reused]])
            boundary_counts = list(history.boundary_counts[:reused + 1])
            frame_layers = list(history.frame_layers[:reused + 1])
            frame_states = list(frame_layers[-1])
            frame_constraints = list(history.frame_constraints[:reused * len(proc.input_framing)])
            state_payloads = list(history.state_payloads[:reused + 1])
            output_payloads = list(history.output_payloads[:reused])
            halted, errors = list(history.halted[:reused + 1]), list(history.errors[:reused + 1])
            definitions = [definition for enc in encodings for definition in enc.definitions]
            selections = [selection for enc in encodings for selection in enc.array_selections]
            _history_owner.reused_steps = reused
        if history is not None and reused != len(history.encodings):
            _history_owner.formula = None
    state_proof = None
    if reset_origin and plan is None:
        from ..symbolic.term_rewrite import _ComparisonRewriter

        state_proof = _ComparisonRewriter(
            definitions, array_selections=selections, checkpoint=_checkpoint)
    for t in range(len(encodings), steps):
        yield from encoder_mod._retry_encoding(check)
        step_env = (position_plan.environment_at(t)
                    if position_plan is not None else env)
        step_plan = position_steps[t] if position_steps is not None else plan
        established = False
        if state_proof is not None:
            bounds = {}
            for name, value in pre.items():
                bounds[name] = (yield from encoder_mod._retry_encoding(
                    state_proof.value_bounds, value)) or encoder_mod._supplied_state_range(value)
            if all(value is not None for value in bounds.values()):
                step_env = {**step_env, **bounds}
                established = True
        # Each step's inputs are its own, since a stimulus is a sequence, and
        # they are named by the step they belong to.
        inputs = {name: z3.BitVec(
                    f"{name}@{t}",
                    (position_plan.scalar_width if position_plan is not None
                     else enc_width(step_env, name)))
                  for name in proc.inputs}
        if _input_values is not None:
            kinds = dict(proc.semantics.input_types)
            for name, value in _input_values[t].items():
                inputs[name] = encoder_mod.encode_concrete_value(
                    value, step_env[name], name, kind=kinds[name],
                    width=position_plan.scalar_width if position_plan is not None else None)
        for value in inputs.values():
            register_boundary(value)
        for index, (framing, projection) in enumerate(proc.input_framing):
            frame_states[index], legal = symbolic_step(framing, frame_states[index], {
                public: inputs[logical] for public, logical in projection
                if public in framing.fields}, accepted=z3.Not(halted[-1]))
            frame_constraints.append(legal)
        selected_keep = (
            query_keeps[t] if query_keeps is not None else query_keep)
        querying = selected_keep is not None
        encode = (encoder_mod._encode_query_step
                  if querying else encoder_mod._encode_step)
        # Complete position plans certify each successor carrier from reset.
        # Reuse that induction for this owned, full state chain even when SSA
        # names hide constant state after a possible failure. Arbitrary initial
        # terms and projected command cones do not establish the same premise.
        encode_kw = ({"keep": selected_keep, "goals": query_goals}
                     if querying else {})
        encode_kw["_state_ranges_established"] = established or (
            not querying and reset_origin and position_plan is not None)
        construction = None
        if querying and _on_prefix:
            construction = encoder_mod._StepConstruction()
            encode_kw.update(_construction=construction, _prefix_goals=True,
                             _defer_constraint=True)
        while True:
            enc = encode(
                proc, pre={**pre, **inputs}, env=step_env, tag=f"@{t}",
                plan=step_plan,
                occurrence_carriers=(
                    position_plan.occurrence_layers[t]
                    if position_plan is not None else None),
                scalar_width=(position_plan.scalar_width
                              if position_plan is not None else None),
                _procedure_validation=validation,
                _plan_prevalidated=plan_prevalidated,
                _checkpoint=_checkpoint, _plan_checkpoint=plan_check,
                **encode_kw)
            if isinstance(enc, encoder_mod._QueryPrefix):
                # Earlier transitions are complete; only this final occurrence
                # is partial. Search may try its exact hit before expanding
                # the unused suffix. A miss resumes the SAME step, preserving
                # all failures, later loop occurrences and successor state.
                yield (tuple(encodings), enc.encoding, halted[-1],
                       tuple(frame_constraints))
                construction.prefix = None
            elif enc is not None:
                break
            yield from encoder_mod._retry_encoding(check)
        if set(enc.state_post) != set(proc.state):
            missing = sorted(set(proc.state) - set(enc.state_post))
            extra = sorted(set(enc.state_post) - set(proc.state))
            raise EncodingError(
                f"the encoded state boundary is incomplete "
                f"(missing={missing}, extra={extra})", kind="shape")
        encodings.append(enc)
        if state_proof is not None:
            definitions.extend(enc.definitions)
            selections.extend(enc.array_selections)
        before = halted[-1]
        # The state stands still once the run has ended, and the error is the
        # one the step that ended it carried.
        # Literal Boolean identities are exact even without range premises.
        # Otherwise a non-failing N-step run retains N nested false prefixes,
        # and every later state/goal dependency walk traverses them again.
        stopped = encoder_mod._disj(before, encoder_mod._neg(enc.ok))
        chained = {}
        if position_plan is not None:
            target = position_plan.state_layers[t + 1]
            for name, term in enc.state_post.items():
                carrier = target.get(name)
                if carrier is None:
                    raise EncodingError(
                        f"the next position has no carrier for state {name}",
                        kind="range")
                converted = _position_value(term, carrier.width, name)
                successor = _position_after(stopped, converted, carrier, name)
                enc.state_post[name] = successor
                enc.post[name] = successor
                chained[name] = successor
            if not querying:
                for name, carrier in position_plan.output_layers[t].items():
                    if name == encoder_mod.COUNT and proc.count is None:
                        continue
                    if name not in enc.post:
                        raise EncodingError(
                            f"position {t} has no encoded output {name}",
                            kind="range")
                    enc.post[name] = _position_value(
                        enc.post[name], carrier.width, name)
        else:
            for name, term in enc.state_post.items():
                old = pre.get(name, enc.pre.get(name))
                if old is None:
                    raise EncodingError(
                        f"the state {name} has no value before this step")
                chained[name] = _held(stopped, old, term, name)
        pre = chained
        output_payloads.append({
            name: enc.post[name]
            for name in tuple(proc.output_vars) + (encoder_mod.COUNT,)
            if not querying and name in enc.post
        })
        errors.append(encoder_mod._choose(before, errors[-1], enc.error))
        halted.append(stopped)
        state_payloads.append(dict(pre))
        boundary_counts.append(len(boundary_atoms))
        frame_layers.append(tuple(frame_states))
    unique_boundary = {
        atom.get_id(): atom for atom in boundary_atoms
    }
    checkpoint = ConstructionCheckpoint()
    checkpoint.callback = _checkpoint
    try:
        from ..symbolic.exact_formula import _FormulaConstruction

        registration = _FormulaConstruction(
            definitions=tuple(
                definition
                for encoding in encodings
                for definition in encoding.definitions
            ),
            restrictions=tuple(
                restriction
                for encoding in encodings
                for restriction in encoding.restrictions
            ) + tuple(frame_constraints),
            boundary_atoms=tuple(unique_boundary.values()),
            prefix=None if _history_owner is None else _history_owner.formula,
        )
        formula = yield from encoder_mod._retry_encoding(registration.advance, checkpoint)
        if _history_owner is not None:
            _history_owner.formula = formula
        run = Unrolling(
            steps, encodings, formula, halted, errors,
            state_payloads, output_payloads,
            discharged=(set(proc.discharged) | set(proc.impossible)),
            query_scope=(query_goals
                         if query_keep is not None or query_keeps is not None
                         else None))
        yield from encoder_mod._retry_encoding(check)
        if transactions is not None:
            run.transaction_states, run.transaction_components = transactions.encode_history(proc, run)
        yield from encoder_mod._retry_encoding(check)
        if history_binding is not None:
            _history_owner.completed = _RunPrefix(
                history_binding, _input_values, tuple(encodings),
                tuple(boundary_atoms), tuple(frame_constraints),
                tuple(state_payloads), tuple(output_payloads), tuple(halted), tuple(errors),
                tuple(history_keeps),
                tuple(boundary_counts), tuple(frame_layers))
        return run
    finally:
        # The completed formula outlives construction; later solving/auditing
        # has its own clock. Never retain this expired callback on its terms.
        checkpoint.callback = None


def _position_value(value, width, name):
    """Check one recursive value uses the plan's single scalar encoding."""
    from ..symbolic.functional_array import FunctionalArray

    if isinstance(value, FunctionalArray):
        if value.element_width != width:
            raise EncodingError(
                f"{name} uses {value.element_width} bits inside a {width}-bit "
                "scalar encoding", kind="width")
        return value
    if isinstance(value, list):
        return [_position_value(item, width, name) for item in value]
    if not z3.is_bv(value):
        raise EncodingError(f"{name} is not a bit-vector value", kind="shape")
    if value.size() != width:
        raise EncodingError(
            f"{name} uses {value.size()} bits inside a {width}-bit scalar "
            "encoding", kind="width")
    return value


def _position_after(stopped, value, carrier, name):
    """Next normal encoding, with an irrelevant in-carrier value after halt."""
    from ..symbolic.functional_array import FunctionalArray

    if isinstance(value, FunctionalArray):
        # The sparse store chain is already an exact finite-array value.  In
        # particular, an external memory keeps its address-dependent base;
        # packing that base would allocate every untouched cell merely to pass
        # the state to the next step.
        return value
    if isinstance(value, list):
        return [_position_after(stopped, item, carrier, name)
                for item in value]
    dummy = z3.BitVecVal(carrier.lo, carrier.width)
    return encoder_mod._choose(stopped, dummy, value)


def _held(before, old, new, name=""):
    """What a state variable holds after a step, or before it if the run ended.

    Cell by cell for an array, since a choice between two arrays is a choice per
    cell: the formula carries the cells and there is no term standing for the
    whole of one.

    A refusal rather than a crash where the two disagree. An ill-typed step is a
    step where a name is an array in one place and a value in another, and
    `well_formed` says so with the block it is in; the encoder is not gated on
    that, so it has to say something itself, and zipping a list with a scalar
    said `TypeError: 'BitVecNumRef' object is not iterable`.
    """
    from ..symbolic.functional_array import FunctionalArray

    old_array = isinstance(old, (list, FunctionalArray))
    new_array = isinstance(new, (list, FunctionalArray))
    if old_array != new_array:
        raise EncodingError(
            f"{name or 'a state variable'} is an array before the step and a "
            f"value after it, or the other way about")
    if isinstance(old, FunctionalArray) or isinstance(new, FunctionalArray):
        if not encoder_mod._same_shape(old, new):
            raise EncodingError(
                f"{name or 'a state variable'} changes compact array shape")
        # A uniform initialization and an explicit replacement may use
        # different representations of the same finite scalar array. Convert
        # only the already materialized arm; do not expand the compact history.
        if isinstance(old, list):
            old = FunctionalArray.from_cells(old)
        if isinstance(new, list):
            new = FunctionalArray.from_cells(new)
        width = max(old.element_width, new.element_width)
        return FunctionalArray.choose(before, old.widen(width), new.widen(width))
    if isinstance(old, list):
        if len(old) != len(new):
            raise EncodingError(
                f"{name or 'a state variable'} has {len(new)} cells after the "
                f"step and {len(old)} before it")
        return [_held(before, o, n, name) for o, n in zip(old, new)]
    # Preserve the original common carrier even when one arm is eliminated.
    return encoder_mod._choose(before, *widen(old, new))


def enc_width(env, name):
    """Width of an input, from the range the interface gives it."""
    return validated_width(env.get(name), name)


def _charge_physical_queries(spent, outcome):
    """Validate, charge and return the number of launched solver calls."""

    count = getattr(outcome, "subqueries", 1)
    if type(count) is not int or count < 0:
        raise ValueError("an exact outcome reports a natural query count")
    if spent is not None and count:
        spent.charge(count)
    return count


def _occurrence_work_limit():
    return math.isqrt(budgets_mod.TERM_BUDGET)


def _occurrence_schedule(goals, events):
    """Stable lowered occurrence sites for each public goal."""

    from ..symbolic import ir

    by_goal = {goal: [] for goal in goals}
    for position, (command, _under) in enumerate(events):
        if isinstance(command, (ir.Mark, ir.Fail)) \
                and command.goal in by_goal:
            by_goal[command.goal].append(position)
    return {goal: tuple(sites) for goal, sites in by_goal.items()}


def _occurrence_fragments(proc, goal, sites, steps, slice_index, *, _checkpoint=None):
    """Cone-bounded pre-unroll fragments for one public goal.

    A source goal may have many lowered occurrences after inlining.  Batch them by
    the work in their independently audited command cones, not merely by their
    count: distant occurrences can have almost disjoint cones, so a count-based
    batch can reconstruct the full formula it was meant to avoid.  Measure the
    actual union for a batch and bisect only while that union exceeds the square
    root of the hard representation capability.  A single larger cone remains one
    indivisible exact question.  The sealed result is reused by encoding rather
    than computed a second time.  Every bounded step remains a possible target.
    """

    from ..symbolic import slicing

    if not sites:
        return ()
    slice_index = slice_index.checked_for(proc)
    events = slice_index.events
    work_limit = _occurrence_work_limit()

    def split(step, selected, measured=None):
        if _checkpoint is not None:
            _checkpoint()
        fragment = measured or slicing.occurrence_run(
            proc, goal, selected, step, steps, events=events,
            _index=slice_index)
        if fragment.work <= work_limit or len(selected) == 1:
            return (fragment,)
        # The measured union tells us approximately how many target-sized
        # pieces are needed.  Splitting only in half builds every internal node
        # of a binary tree, so a 276-occurrence public goal scans a 68k-command
        # body hundreds of extra times before encoding starts.  Partition once
        # by the measured work and refine only an individually oversized piece.
        count = min(
            len(selected),
            max(2, (fragment.work + work_limit - 1) // work_limit),
        )
        width, remainder = divmod(len(selected), count)
        start = 0
        children = []
        for index in range(count):
            stop = start + width + (1 if index < remainder else 0)
            part = split(step, selected[start:stop])
            # A loop-closed child can be the entire parent query. Splitting
            # then adds work without reducing the largest indivisible cone.
            # Keep the parent (all requested sites), whose selection/formula
            # is exactly equal to that already-admitted child. This is not a
            # subset approximation or permission to enlarge a physical query.
            if any(child.keeps == fragment.keeps for child in part):
                return (fragment,)
            children.extend(part)
            start = stop
        return tuple(children)

    fragments = []
    seen_keeps = set()
    for step in range(steps):
        for fragment in split(step, tuple(sites)):
            # With procedure, goal, horizon and plan fixed, this ordered tuple
            # is the encoder's entire command selection. Site metadata only
            # audits the cut; the hit predicate includes all retained goal
            # occurrences. Equal tuples therefore need one physical question.
            if fragment.keeps not in seen_keeps:
                seen_keeps.add(fragment.keeps)
                fragments.append(fragment)
    return tuple(fragments)


def _preunroll_split(proc, goals, steps, slice_index, schedule):
    """Whether one joined formula exceeds the structural query granularity.

    ``TERM_BUDGET`` remains the hard capability bound.  Its square root is only
    an execution granularity: below it the established set-wise path is cheaper;
    above it exact occurrence fragments avoid making solver preprocessing
    quadratic in one large joined command cone.  Either route asks the same
    bounded question.
    """

    from ..symbolic import slicing

    slice_index = slice_index.checked_for(proc)
    fragments = sum(
        len(schedule[goal]) * steps if schedule[goal] else 1
        for goal in goals)
    if fragments < 2:
        return False
    keep = slicing.for_run(
        proc, goals, steps=steps, events=slice_index.events,
        _index=slice_index)
    return keep is not None \
        and len(keep) * steps > math.isqrt(budgets_mod.TERM_BUDGET)


def _add_timings(total, timings):
    for name, duration in timings.as_dict().items():
        total[name] = total.get(name, 0) + duration


def _fragmented_telemetry(scope, goal, answer, timings):
    if scope is None:
        return None
    try:
        settled = (goal,) if answer is not SolverAnswer.SAT else ()
        return scope.fragment(
            (goal,), timings=StageTimings(timings or {"total_ns": 0})
        ).finalize(outcome=answer.value, goals_settled=settled)
    except Exception:  # noqa: BLE001 - instrumentation cannot own semantics
        return None


def _decide_occurrence_goals(
        proc, env, goals, steps, initial, plan, prepared, schedule, slice_index,
        timeout_ms, spent, *, telemetry_scope=None, query_memory_mb=None,
        _progress=None):
    """Answer large bounded questions before their command cones are joined.

    Each physical formula denotes selected occurrences at one target step, with
    exact predecessor state and failure cones.  SAT in any fragment is SAT for
    the public goal.  UNSAT is published only when every occurrence at every
    bounded step is UNSAT; one UNKNOWN therefore keeps the public goal open.
    """

    from .goal_projection import literal_false_goals
    from .occurrence_queries import GroupBudget, complete_groups

    artifact_check = _budget_checkpoint(spent)
    reachable, unreachable, undecided, queries = {}, set(), {}, []
    # Literal-dead occurrences need no arithmetic/state cone. Ask their exact
    # false disjunction once before building the live occurrence selections.
    # The prepared object has already validated the whole procedure and plan;
    # the structural proof covers every occurrence, not only the selected one.
    proved_false = literal_false_goals(proc)
    false_goals = tuple(g for g in goals if g in proved_false)
    answered = set()
    if false_goals and not spent.exhausted():
        formula = ExactFormula()
        outcome = _solve_with_memory(
            formula, z3.BoolVal(False), spent.for_query(timeout_ms),
            query_memory_mb, complete=True)
        _charge_physical_queries(spent, outcome)
        if outcome.answer is SolverAnswer.SAT:
            raise EncodingError("a literal-false goal query returned SAT", kind="goal")
        queries.append(QueryCall(
            false_goals, outcome.answer,
            reason=outcome.reason if outcome.answer is SolverAnswer.UNKNOWN else "",
            solver_calls=getattr(outcome, "subqueries", 1),
            telemetry=_measured_telemetry(
                telemetry_scope, false_goals, outcome, answer=outcome.answer)))
        if outcome.answer is SolverAnswer.UNSAT:
            unreachable.update(false_goals)
            answered.update(false_goals)
        # UNKNOWN gets its original per-goal path/refinement; no proof is
        # inferred from the cheap query's intended result.
    work_by_goal = {}
    for goal in goals:
        if goal in answered:
            continue
        # Planning is within the artifact clock too. An exhausted invocation
        # must not construct the remaining selections just to report UNKNOWN.
        if spent.exhausted():
            break
        sites = schedule[goal]
        try:
            work_by_goal[goal] = (_occurrence_fragments(
                proc, goal, sites, steps, slice_index,
                _checkpoint=artifact_check) if sites else ())
        except _ConstructionExpired:
            break
    groups = complete_groups(work_by_goal)
    remaining_fragments = sum(max(1, len(work)) for work in work_by_goal.values())

    for goal_index, goal in enumerate(goals):
        if goal in answered:
            continue
        if _progress is not None and goal in _progress.reached:
            answered.add(goal)
            remaining_fragments = max(
                0, remaining_fragments - max(1, len(work_by_goal.get(goal, ()))))
            continue
        exhausted = spent.exhausted()
        if exhausted:
            for pending in goals[goal_index:]:
                if pending in answered:
                    continue
                undecided[pending] = exhausted
                queries.append(QueryCall(
                    (pending,), SolverAnswer.UNKNOWN,
                    reason=exhausted, solver_calls=0))
            break
        work = work_by_goal[goal]
        group = groups.get(goal)
        if group is not None:
            if _progress is not None:
                group = tuple(_progress.remaining(group))
            # Every member has one complete fragment, and all selections are
            # equal. Encode that transition once with separate hit predicates;
            # the established set-wise coordinator retains SAT witnesses,
            # UNSAT covers, UNKNOWN refinements and physical-call accounting.
            group_budget = GroupBudget(spent, timeout_ms)
            try:
                run = unroll(
                    proc, env, steps, initial, plan=plan, goals=group,
                    _shared_occurrences=tuple(work_by_goal[g][0] for g in group),
                    _prepared=prepared, _checkpoint=_budget_checkpoint(group_budget),
                    _plan_checkpoint=artifact_check)
            except _ConstructionExpired as exc:
                found, ruled, open_, calls = _construction_unknown(group, str(exc))
            except MemoryError:
                reason = "memory ceiling reached while building a shared occurrence formula"
                found, ruled = {}, set()
                open_ = dict.fromkeys(group, reason)
                calls = tuple(QueryCall(
                    (g,), SolverAnswer.UNKNOWN, reason=reason, solver_calls=0) for g in group)
            else:
                found, ruled, open_, calls = decide_open(
                    run, proc, group, timeout_ms, group_budget,
                    telemetry_scope=telemetry_scope, query_memory_mb=query_memory_mb,
                    _complete_selection=True, _progress=_progress)
            reachable.update(found)
            unreachable.update(ruled)
            undecided.update(open_)
            queries.extend(calls)
            answered.update(group)
            remaining_fragments -= len(group)
            continue
        # A discharged source goal has no lowered occurrence.  Its registered
        # false condition still goes through the ordinary exact path.  An absent
        # non-discharged goal remains unsupported rather than becoming UNSAT.
        if not work:
            goal_budget = GroupBudget(spent, timeout_ms)
            try:
                run = unroll(
                    proc, env, steps, initial, plan=plan, goals=(goal,),
                    _prepared=prepared, _checkpoint=_budget_checkpoint(goal_budget),
                    _plan_checkpoint=artifact_check)
            except _ConstructionExpired as exc:
                verdict, detail, outcome, decode_ns = "unknown", str(exc), None, None
            else:
                verdict, detail, outcome, decode_ns = _decide_one_exact(
                    run, proc, goal, timeout_ms, goal_budget,
                    participants=max(1, remaining_fragments),
                    query_memory_mb=query_memory_mb)
            remaining_fragments -= 1
            if verdict == "sat":
                reachable[goal] = detail
                answer = SolverAnswer.SAT
            elif verdict == "unsat":
                unreachable.add(goal)
                answer = SolverAnswer.UNSAT
            else:
                undecided[goal] = detail
                if verdict == "unsupported":
                    continue
                answer = SolverAnswer.UNKNOWN
            queries.append(QueryCall(
                (goal,), answer,
                witness=detail if answer is SolverAnswer.SAT else None,
                reason=str(detail) if answer is SolverAnswer.UNKNOWN else "",
                telemetry=(None if outcome is None else _measured_telemetry(
                    telemetry_scope, (goal,), outcome, answer=answer,
                    decode_ns=decode_ns)),
                solver_calls=getattr(outcome, "subqueries", 0)))
            if _progress is not None:
                _progress.observe(queries[-1])
            continue

        goal_started = time.monotonic()

        def goal_check(goal_started=goal_started):
            artifact_check()
            if math.ceil((time.monotonic() - goal_started) * 1000) >= timeout_ms:
                raise _ConstructionExpired(
                    f"the goal's exact-query clock of {timeout_ms} ms is spent")

        goal_fragment_base = remaining_fragments
        timings = {}
        solver_calls = 0
        first_unknown = None
        witness = None
        for fragment in work:
            exhausted = spent.exhausted()
            if exhausted:
                first_unknown = first_unknown or exhausted
                break
            elapsed_ms = math.ceil((time.monotonic() - goal_started) * 1000)
            goal_left = timeout_ms - elapsed_ms
            if goal_left < 1:
                first_unknown = first_unknown or (
                    f"the goal's exact-query clock of {timeout_ms} ms is spent")
                break

            encode_started = time.monotonic_ns()
            try:
                run = unroll(
                    proc, env, steps, initial, plan=plan, goals=(goal,),
                    _occurrence=fragment, _prepared=prepared,
                    _checkpoint=goal_check, _plan_checkpoint=artifact_check)
            except _ConstructionExpired as exc:
                first_unknown = first_unknown or str(exc)
                break
            except MemoryError:
                first_unknown = first_unknown or (
                    "the artifact's memory ceiling was reached while building "
                    "an occurrence formula")
                break
            finally:
                timings["encode_unroll_ns"] = (
                    timings.get("encode_unroll_ns", 0)
                    + time.monotonic_ns() - encode_started)

            exhausted = spent.exhausted()
            if exhausted:
                first_unknown = first_unknown or exhausted
                break
            elapsed_ms = math.ceil((time.monotonic() - goal_started) * 1000)
            goal_left = timeout_ms - elapsed_ms
            if goal_left < 1:
                first_unknown = first_unknown or (
                    f"the goal's exact-query clock of {timeout_ms} ms is spent")
                break
            query_ms = spent.for_query(
                max(1, goal_left), participants=max(1, remaining_fragments))
            outcome = _solve_with_memory(
                run.formula, run.goal_condition(goal), query_ms,
                query_memory_mb, complete=True)
            solver_calls += _charge_physical_queries(spent, outcome)
            remaining_fragments -= 1
            _add_timings(timings, outcome.timings)

            if outcome.answer is SolverAnswer.SAT:
                decode_started = time.monotonic_ns()
                model = outcome.model
                witness = [
                    {
                        name: model.eval(
                            encoding.pre[name], model_completion=True
                        ).as_signed_long()
                        for name in proc.inputs
                    }
                    for encoding in run.encodings
                ]
                timings["decode_ns"] = (
                    timings.get("decode_ns", 0)
                    + time.monotonic_ns() - decode_started)
                break
            if outcome.answer is SolverAnswer.UNKNOWN:
                first_unknown = first_unknown or outcome.reason

        # Fragments skipped after SAT or a logical deadline are no longer future
        # participants when later goals divide the remaining artifact clock.
        remaining_fragments = max(0, goal_fragment_base - len(work))

        if witness is not None:
            reachable[goal] = witness
            answer = SolverAnswer.SAT
        elif first_unknown is None:
            unreachable.add(goal)
            answer = SolverAnswer.UNSAT
        else:
            undecided[goal] = first_unknown
            answer = SolverAnswer.UNKNOWN
        queries.append(QueryCall(
            (goal,), answer, witness=witness,
            reason=first_unknown if answer is SolverAnswer.UNKNOWN else "",
            telemetry=_fragmented_telemetry(
                telemetry_scope, goal, answer, timings),
            solver_calls=solver_calls))
        if _progress is not None:
            _progress.observe(queries[-1])

    if _progress is not None:
        _progress.merge(reachable, unreachable, undecided, goals)
    return reachable, unreachable, undecided, _canonical_queries(queries, goals)


def _decide_one_exact(run, proc, goal, timeout_ms, spent=None, *,
                      participants=1, query_memory_mb=None):
    """The verdict on one goal, with a stimulus when there is one.

    A solver of its own, holding the step and this one goal, and the repetition
    is the point rather than the waste it looks like. What the goals share is the
    step, which is the whole formula, so asserting it once and asking each goal
    in a scope on top of it is the obvious saving and it was tried: the step in
    once, `push`, the goal, `check`, `pop`. Over the population that made the
    term lowering's total query time 1.5 times worse and the graph's 2.1 times
    worse, and twelve models stopped finishing inside ninety seconds.

    The reason is that scopes put the solver in incremental mode, where it gives
    up the preprocessing a one-shot solve does. So the repeated assertion is not
    the cost; the preprocessing it pays for each time is what makes each query
    fast, and paying it a hundred and thirty times beats paying it once and
    searching without it.
    """
    # A goal this unrolling does not encode is **undecided**, not unreachable. Asserting
    # the condition would have asserted `False` and read the resulting `unsat` back as a
    # proof that no run reaches it, which is a claim about a query nobody posed. `_each`
    # puts a verdict it does not recognise into `undecided`, which is where this belongs.
    if not run.supports(goal):
        return "unsupported", (
            f"{goal!r} is not encoded by every step of this unrolling, so no query was "
            f"posed about it"), None, None
    out_of = spent.exhausted() if spent is not None else None
    if out_of:
        return "unknown", out_of, None, None
    # The smaller of this goal's ceiling and what the artifact has left. Shortening
    # z3's own timeout is the only way an artifact budget can bind a solve at all.
    query_ms = (spent.for_query(timeout_ms, participants=participants)
                if spent else timeout_ms)
    query_slots = spent.remaining_queries() if spent is not None else None
    outcome = (
        _solve_with_memory(
            run.formula, run.goal_condition(goal), query_ms,
            query_memory_mb, complete=True)
        if run.preselected((goal,)) else
        solve_exact_components(
            run.formula, run.goal_components(goal), query_ms,
            memory_mb=query_memory_mb,
            max_groups_per_query=_PUBLIC_COMPONENT_GROUPS_PER_QUERY,
            max_subqueries=query_slots,
            _solve=solve_exact)
    )
    _charge_physical_queries(spent, outcome)
    if outcome.answer is SolverAnswer.SAT:
        decode_started = time.monotonic_ns()
        model = outcome.model
        trace = [
            {name: model.eval(enc.pre[name],
                              model_completion=True).as_signed_long()
             for name in proc.inputs}
            for enc in run.encodings]
        return "sat", trace, outcome, time.monotonic_ns() - decode_started
    if outcome.answer is SolverAnswer.UNSAT:
        return "unsat", None, outcome, None
    return "unknown", outcome.reason, outcome, None


def decide_one(run, proc, goal, timeout_ms, spent=None, *,
               query_memory_mb=None):
    """Compatibility pair for one exact goal; transcript paths keep measurements."""

    verdict, detail, _outcome, _decode_ns = _decide_one_exact(
        run, proc, goal, timeout_ms, spent,
        query_memory_mb=query_memory_mb)
    return verdict, detail


def _balanced_refinements(batch):
    """Split an unresolved goal set into two stable exact subqueries."""

    batch = tuple(batch)
    if len(batch) < 2:
        return None
    middle = (len(batch) + 1) // 2
    return batch[:middle], batch[middle:]


def _dependency_partitions(run, batch):
    """Partition a goal set before posing avoidably repeated cone queries.

    A model for one dependency cone says nothing about a disjoint cone.  Putting
    both into the first disjunction therefore makes a SAT-heavy workload revisit
    the untouched cone after every witness.  Stable partitions avoid that repeated
    work.  A public goal remains indivisible; its occurrence solver handles any
    independent components inside the goal.  ``None`` means the set has no useful
    structural partition and should retain one set-wise query.
    """

    batch = tuple(batch)
    if len(batch) < 2:
        return None
    keyed = []
    largest_goal = 0
    for goal in batch:
        keys = set()
        auditable = True
        for occurrence in run.goal_components(goal):
            roots = run.dependency_roots(occurrence)
            if roots is None:
                auditable = False
                break
            keys.add(roots)
        frozen = frozenset(keys) if auditable else None
        keyed.append((goal, frozen))
        if frozen is not None:
            largest_goal = max(largest_goal, len(frozen))

    # Use the same physical batch scale as the component solver will use for
    # the most occurrence-rich indivisible goal.  A strict multi-goal query is
    # therefore never larger merely because several public names were grouped.
    limit = math.isqrt(max(0, largest_goal - 1)) + 1
    groups = []
    current = []
    current_keys = set()

    def flush():
        nonlocal current, current_keys
        if current:
            groups.append(tuple(current))
            current = []
            current_keys = set()

    for goal, keys in keyed:
        if keys is None:
            flush()
            groups.append((goal,))
            continue
        combined = current_keys | keys
        if current and len(combined) > limit:
            flush()
            combined = set(keys)
        current.append(goal)
        current_keys = combined
    flush()
    result = tuple(groups)
    if len(result) < 2:
        return None
    return result


def _dependency_refinements(run, batch):
    """Children of an unresolved goal-set query.

    Prefer the same dependency partition used by the initial schedule.  Equal
    cones still need a stable binary split after UNKNOWN so refinement always
    makes progress to the required singleton evidence.
    """

    return (_dependency_partitions(run, batch)
            or _balanced_refinements(batch))


# One public goal may expand to hundreds of syntactically distinct occurrences.
# The component solver packs initial batches against a square-root dependency-
# cone budget, then refines only batches that actually return UNKNOWN.  ``None``
# leaves that scale to the solver instead of imposing a population-specific
# count ceiling.
_PUBLIC_COMPONENT_GROUPS_PER_QUERY = None


def _query_plan(run, batch):
    """Return one exact disjunction and the children created only by UNKNOWN."""

    batch = tuple(batch)
    if not batch:
        raise ValueError("an exact query batch is nonempty")
    if len(batch) == 1:
        return batch, None
    return batch, _dependency_refinements(run, batch)


def _canonical_queries(queries, goals):
    """Order logical evidence independently of its worker execution schedule."""

    goal_rank = {goal: index for index, goal in enumerate(goals)}

    def evidence_order(call):
        ranks = tuple(goal_rank[goal] for goal in call.goals)
        return min(ranks), -len(ranks), ranks

    return tuple(sorted(queries, key=evidence_order))


def _solve_with_memory(formula, target, timeout_ms, memory_mb, *, complete=False):
    """Call the exact solver, adding its optional resource policy when present."""

    options = {"complete": True} if complete else {}
    if memory_mb is not None:
        options["memory_mb"] = memory_mb
    return solve_exact(formula, target, timeout_ms, **options)


def _measured_telemetry(scope, batch, outcome, *, answer, decode_ns=None):
    if scope is None:
        return None
    if type(scope) is not QueryTelemetryScope:
        raise TypeError("Behavior query telemetry uses QueryTelemetryScope")
    try:
        timings = outcome.timings
        if decode_ns is not None:
            timings = timings.merge(StageTimings({"decode_ns": decode_ns}))
        settled = batch if answer is SolverAnswer.UNSAT \
            or answer is SolverAnswer.UNKNOWN and len(batch) == 1 else ()
        return scope.fragment(
            batch, shape=outcome.shape, timings=timings).finalize(
                outcome=answer.value, goals_settled=settled)
    except Exception:  # noqa: BLE001 - instrumentation cannot own solver semantics
        # Measurement is evidence beside the answer, never part of its semantics.
        return None


def _sat_goals(run, batch, outcome):
    """Public goals established by this SAT model.

    A complete set query contains every condition in ``batch``.  A decomposed
    query instead contains only the occurrence components named by its result;
    evaluating a dropped component with model completion would invent a value.
    Restricting the readback to checked components makes the decomposition an
    execution schedule, never a new source of goal evidence.
    """

    model = outcome.model
    checked = getattr(outcome, "sat_components", None)
    if checked is None:
        return [
            goal for goal in batch
            if z3.is_true(model.eval(
                run.goal_condition(goal), model_completion=True))
        ]
    identities = {item.get_id() for item in checked}
    reached = []
    for goal in batch:
        components = tuple(
            item for item in run.goal_components(goal)
            if item.get_id() in identities)
        if components and any(z3.is_true(model.eval(
                item, model_completion=True)) for item in components):
            reached.append(goal)
    return reached


def decide_open(run, proc, goals, timeout_ms, spent=None, *,
                telemetry_scope=None, _defer_singletons=None,
                _observe_outcome=None, query_memory_mb=None,
                _complete_selection=False, _progress=None):
    """Decide the open set, then refine only an unresolved set.

    One satisfiable query settles every goal reached by its model; one
    unsatisfiable query settles the complete open set.  A satisfiable answer
    removes every goal reached by its witness and asks the remaining set again.
    On UNKNOWN, the set is divided into balanced exact subqueries until an
    answer settles a subset or a singleton remains unresolved.  An unresolved
    set is never treated as evidence about its members.  The unrolling already
    contains the command cone audited for the requested goals. Ordinary queries
    use dependency-cone batches. An audited complete selection instead shares
    its full formula across the initial question and its refinements, without
    repeating occurrence decomposition. All calls share one artifact clock.
    """
    if _defer_singletons is not None and not callable(_defer_singletons):
        raise TypeError("a singleton deferral hook must be callable")
    if _observe_outcome is not None and not callable(_observe_outcome):
        raise TypeError("an exact-query observer must be callable")
    if type(_complete_selection) is not bool:
        raise TypeError("the complete-selection policy is Boolean")
    if _complete_selection and not run.preselected(goals):
        raise ValueError("a complete-selection query must name the audited goal scope")
    reachable, unreachable, undecided = {}, set(), {}
    queries = []
    spent = spent or budgets_mod.Spent(ms=None, max_queries=None)
    # Goals this unrolling does not encode never enter a query. In the disjunction they
    # would contribute `False`, so an `unsat` over the batch would be read as "no run
    # reaches any of them" -- settling, in one stroke, goals that were never asked about.
    # That is the whole set-at-once saving turned into a set-at-once wrong answer.
    askable = [g for g in goals if run.supports(g)]
    askable_set = set(askable)
    for goal in goals:
        if goal not in askable_set:
            undecided[goal] = (
                f"{goal!r} is not encoded by every step of this unrolling, so no query "
                f"was posed about it")
    # Keep one set-wise question wherever the goals share a useful cone.  Where
    # dependency analysis proves that the first query would only concatenate
    # independent cones, start with those exact partitions instead.  This changes
    # only the execution schedule: every asked subset remains an exact disjunction,
    # SAT witnesses are replay-audited, and UNSAT is attributed only to the subset
    # the solver actually checked.
    # Equal complete command selections already share one exact formula.
    # Rediscovering expression-level cones here would undo that preparation
    # sharing. Refinements may retain extra definitions: they remain constrained,
    # not existentially dropped, and each question keeps its own hit predicate.
    def plan_batch(batch):
        if _complete_selection:
            return tuple(batch), _balanced_refinements(tuple(batch))
        return _query_plan(run, batch)

    initial = (_dependency_partitions(run, askable)
               if askable and not _complete_selection else None)
    initial = initial or ((tuple(askable),) if askable else ())
    frontier = [plan_batch(group) for group in initial]
    deferred_participants = 0
    deferred_goals = set()

    def budget_unknown(batch, reason):
        """Record exact singleton attempts that policy could not launch."""

        for goal in batch:
            undecided.setdefault(goal, reason)
            queries.append(QueryCall(
                (goal,), SolverAnswer.UNKNOWN, reason=reason, solver_calls=0))

    def defer(batch):
        nonlocal deferred_participants
        for goal in batch:
            if goal in deferred_goals:
                raise ValueError(
                    f"singleton refinement deferred {goal!r} twice")
            deferred_goals.add(goal)
            deferred_participants += 1

    while frontier:
        next_frontier = []
        for index, (batch, refinements) in enumerate(frontier):
            if _progress is not None:
                remaining = _progress.remaining(batch)
                if not remaining:
                    continue
                if len(remaining) != len(batch):
                    batch, refinements = plan_batch(remaining)
            # Initial structural partitioning may itself expose singleton public
            # goals.  In the parallel path those are the exact work units the
            # parent asked this prefix worker to return; querying them here would
            # both serialize the work and make the later singleton call a repeat.
            if len(batch) == 1 and _defer_singletons is not None:
                defer(batch)
                continue

            # A goal left undecided because the artifact ran out is undecided with a
            # reason; a goal whose worker was killed externally has no answer at all.
            out_of = spent.exhausted()
            if out_of:
                budget_unknown(batch, out_of)
                continue

            # Share the remaining clock among work that actually exists.  Reserving
            # an entire hypothetical refinement tree before its parent answers can
            # reduce every real query below formula-setup cost.  UNKNOWN creates the
            # next level; those children then participate like every other real query.
            pending = len(frontier) - index
            participants = deferred_participants + pending + len(next_frontier)
            query_ms = spent.for_query(timeout_ms, participants=participants)
            query_slots = spent.remaining_queries()
            if _complete_selection:
                outcome = _solve_with_memory(
                    run.formula, z3.Or([run.goal_condition(goal) for goal in batch]),
                    query_ms, query_memory_mb, complete=True)
            else:
                components = tuple(
                    occurrence
                    for goal in batch
                    for occurrence in run.goal_components(goal)
                )
                outcome = solve_exact_components(
                    run.formula, components, query_ms,
                    memory_mb=query_memory_mb,
                    max_groups_per_query=_PUBLIC_COMPONENT_GROUPS_PER_QUERY,
                    max_subqueries=query_slots,
                    _solve=solve_exact,
                    _dependency_roots=run.dependency_roots,
                    _dependency_masks=run.dependency_masks,
                )
            physical_queries = _charge_physical_queries(spent, outcome)
            if _observe_outcome is not None:
                _observe_outcome(outcome)

            if outcome.answer is SolverAnswer.SAT:
                decode_started = time.monotonic_ns()
                model = outcome.model
                trace = [
                    {
                        name: model.eval(
                            enc.pre[name], model_completion=True
                        ).as_signed_long()
                        for name in proc.inputs
                    }
                    for enc in run.encodings
                ]
                hit = _sat_goals(run, batch, outcome)
                telemetry = _measured_telemetry(
                    telemetry_scope, batch, outcome, answer=SolverAnswer.SAT,
                    decode_ns=time.monotonic_ns() - decode_started,
                )
                queries.append(QueryCall(
                    batch, SolverAnswer.SAT, witness=trace,
                    telemetry=telemetry, solver_calls=physical_queries,
                ))
                if _progress is not None:
                    _progress.observe(queries[-1])
                for goal in hit:
                    reachable[goal] = trace
                rest = [goal for goal in batch if goal not in set(hit)]
                if _progress is not None:
                    rest = _progress.remaining(rest)
                if not hit and rest:
                    # Model completion did not identify which disjunct held.  The
                    # public questions now genuinely are the singleton children.
                    if _defer_singletons is None:
                        next_frontier.extend(
                            plan_batch((goal,)) for goal in rest
                        )
                    else:
                        defer(rest)
                elif rest:
                    # At least one goal left, so this derived question is strictly
                    # smaller than its parent and the level-wise schedule terminates.
                    if len(rest) == 1 and _defer_singletons is not None:
                        defer(rest)
                    else:
                        next_frontier.append(plan_batch(rest))
            elif outcome.answer is SolverAnswer.UNSAT:
                queries.append(QueryCall(
                    batch, SolverAnswer.UNSAT,
                    telemetry=_measured_telemetry(
                        telemetry_scope, batch, outcome,
                        answer=SolverAnswer.UNSAT,
                    ), solver_calls=physical_queries,
                ))
                unreachable.update(batch)
            else:
                reason = outcome.reason
                queries.append(QueryCall(
                    batch, SolverAnswer.UNKNOWN, reason=reason,
                    telemetry=_measured_telemetry(
                        telemetry_scope, batch, outcome,
                        answer=SolverAnswer.UNKNOWN,
                    ), solver_calls=physical_queries,
                ))
                if len(batch) == 1:
                    # This call already is the required singleton evidence.
                    undecided[batch[0]] = reason
                elif refinements is not None:
                    for group in refinements:
                        if len(group) == 1 and _defer_singletons is not None:
                            defer(group)
                        else:
                            next_frontier.append(plan_batch(group))
                elif _defer_singletons is None:
                    next_frontier.extend(
                        plan_batch((goal,)) for goal in batch
                    )
                else:
                    defer(batch)
        frontier = next_frontier
    if _defer_singletons is not None and deferred_goals:
        # Refinement groups are scheduled by formula structure, but the parent
        # receives singleton work in the caller's stable catalogue order.
        _defer_singletons(
            tuple(goal for goal in askable if goal in deferred_goals))
    # A transcript is an immutable, ordered record of calls.  Returning a tuple also
    # distinguishes it from the mutable working list above: callers may retain the
    # transcript as evidence without observing later mutation.
    if _progress is not None:
        _progress.merge(reachable, unreachable, undecided, goals)
    return reachable, unreachable, undecided, _canonical_queries(queries, goals)


def _each(run, proc, batch, timeout_ms, spent, reachable, unreachable,
          undecided, queries, *, telemetry_scope=None,
          reserved_participants=0, query_memory_mb=None):
    """Ask each goal of an unsettled batch on its own.

    What the disjunction could not settle in one question. Asking the goals is
    the bound on the whole scheme: a batch costs its one disjunction and then at
    most its goals, so nothing is ever more than one query worse than asking
    each goal outright.
    """
    if type(reserved_participants) is not int or reserved_participants < 0:
        raise ValueError("reserved query participants is a natural integer")
    for index, goal in enumerate(batch):
        out_of = spent.exhausted() if spent is not None else None
        if out_of:
            undecided.setdefault(goal, out_of)
            queries.append(QueryCall(
                (goal,), SolverAnswer.UNKNOWN, reason=out_of, solver_calls=0))
            continue
        verdict, detail, outcome, decode_ns = _decide_one_exact(
            run, proc, goal, timeout_ms, spent,
            participants=reserved_participants + len(batch) - index,
            query_memory_mb=query_memory_mb)
        # ``unsupported`` means no formula was posed.  Recording it as an UNKNOWN
        # QueryCall would manufacture a solver response and let a missing hit term
        # become a publishable unresolved goal.  The aggregate keeps the diagnostic;
        # the certificate-grade closure notices that no singleton call supports it and
        # refuses the run.
        if verdict != "unsupported":
            answer = {
                "sat": SolverAnswer.SAT,
                "unsat": SolverAnswer.UNSAT,
                "unknown": SolverAnswer.UNKNOWN,
            }.get(verdict)
            if answer is None:
                raise ValueError(f"the worker returned solver answer {verdict!r}")
            queries.append(QueryCall(
                (goal,), answer,
                witness=detail if answer is SolverAnswer.SAT else None,
                reason=(str(detail)
                        if answer is SolverAnswer.UNKNOWN else ""),
                telemetry=_measured_telemetry(
                    telemetry_scope, (goal,), outcome, answer=answer,
                    decode_ns=decode_ns),
                solver_calls=getattr(outcome, "subqueries", 1)))
        if verdict == "sat":
            reachable[goal] = detail
        elif verdict == "unsat":
            unreachable.add(goal)
        else:
            undecided[goal] = detail


def _limit_worker_memory(memory_mb):
    """Apply the declared address-space ceiling in one solver worker."""

    if not memory_mb:
        return
    import resource

    cap = int(memory_mb) * 1024 * 1024
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard == resource.RLIM_INFINITY or cap < hard:
        resource.setrlimit(resource.RLIMIT_AS, (cap, hard))


def _rows(reachable, unreachable, undecided):
    out = [(goal, "sat", trace) for goal, trace in reachable.items()]
    out += [(goal, "unsat", None) for goal in unreachable]
    out += [(goal, "unknown", why) for goal, why in undecided.items()]
    return out


def _memory_rows(goals, memory_mb):
    why = (f"the artifact's memory ceiling of {memory_mb} MB was reached "
           f"while building the formula")
    return [(goal, "unknown", why) for goal in goals]


def _observed_setup_reserve_ms(duration_ns):
    """Conservative wall reserve for repeating measured formula setup work."""

    if type(duration_ns) is not int or duration_ns < 0:
        raise ValueError("observed formula setup time is natural nanoseconds")
    measured_ms = (duration_ns + 999_999) // 1_000_000
    # A later worker rebuilds the same immutable unrolling, but host scheduling and
    # allocator costs still vary.  Keep 25% above the directly observed duration;
    # this is a scheduling reserve, not a semantic timeout or a population constant.
    return max(1, (measured_ms * 5 + 3) // 4)


def _refinement_budget_reason(*, remaining_ms, required_ms):
    return (
        f"the artifact had {remaining_ms} ms left, below the {required_ms} ms "
        "reserve derived from this formula's measured refinement setup"
    )


def _decide_prefix_chunk(work):
    """Run the set-wise query prefix in one formula process.

    The encoding is rebuilt here rather than sent, since a formula is not a
    value that crosses a process boundary.  After an unresolved non-singleton
    batch it also runs singleton refinement when the measured memory envelope
    permits only this one process; otherwise the parent receives the goals it
    may distribute.
    """
    proc, env, steps, initial, goals, timeout_ms, artifact_ms, memory_mb, \
        plan, telemetry_scope, started = work
    _limit_worker_memory(memory_mb)
    query_memory_mb = resources.solver_memory_mb(memory_mb)
    spent = budgets_mod.Spent(
        ms=artifact_ms, max_queries=None, started=started)
    check = _budget_checkpoint(spent)
    unroll_started = time.monotonic_ns()
    try:
        prepared = _prepare_unroll(proc, env, steps, plan, _checkpoint=check)
        slice_index = prepared.slicing()
        events = slice_index.events
        schedule = _occurrence_schedule(goals, events)
        split = _preunroll_split(proc, goals, steps, slice_index, schedule)
        check()
        if split:
            found, ruled, open_, queries = _decide_occurrence_goals(
                proc, env, goals, steps, initial, plan, prepared, schedule,
                slice_index, timeout_ms, spent, telemetry_scope=telemetry_scope,
                query_memory_mb=query_memory_mb)
            return (
                _rows(found, ruled, open_), queries, (),
                process_peak_rss_bytes(),
                time.monotonic_ns() - unroll_started, 0,
            )
        run = unroll(
            proc, env, steps, initial, plan=plan, goals=goals,
            _prepared=prepared, _checkpoint=check)
    except _ConstructionExpired as exc:
        found, ruled, open_, calls = _construction_unknown(goals, str(exc))
        return (_rows(found, ruled, open_), calls, (), process_peak_rss_bytes(),
                time.monotonic_ns() - unroll_started, 0)
    except MemoryError:
        return _memory_rows(goals, memory_mb), (), (), 0, 0, 0
    unroll_ns = time.monotonic_ns() - unroll_started
    deferred = []
    unknown_setup_ns = []

    def observe(outcome):
        # Retain scalar timing only.  Keeping every complete outcome alive here
        # also kept its SAT model and native solver allocations alive across all
        # set-wise rounds; large formulas then reached the aggregate memory
        # ceiling even though each singleton fit independently.
        if outcome.answer is SolverAnswer.UNKNOWN:
            timings = outcome.timings.as_dict()
            unknown_setup_ns.append(
                timings.get("slice_ns", 0)
                + timings.get("solver_load_ns", 0))

    reachable, unreachable, undecided, queries = decide_open(
        run, proc, goals, timeout_ms, spent,
        telemetry_scope=telemetry_scope,
        _defer_singletons=deferred.extend,
        _observe_outcome=observe,
        query_memory_mb=query_memory_mb)
    queries = list(queries)
    query_setup_ns = 0
    if deferred:
        # Some singletons are exposed directly by the audited structural
        # partition, rather than by refining an UNKNOWN set query.  They carry
        # no prior query-setup cost; the later worker still reserves the
        # measured unrolling cost.  When UNKNOWN queries did run, retain their
        # largest observed setup cost as before.
        query_setup_ns = max(unknown_setup_ns, default=0)
        # If the measured formula leaves room for only one worker, keep using
        # this exact unrolling.  Returning it to the parent only to rebuild the
        # same formula in another one-process pool doubles setup time and peak
        # memory without adding isolation: both executions already occur in
        # this bounded child.
        peak = process_peak_rss_bytes()
        reservation = budgets_mod.reservation_for_peak(peak)
        safe_workers = budgets_mod.workers_for(
            deferred, reservation_mb=reservation, total_mb=memory_mb)
        if safe_workers <= 1:
            _each(
                run, proc, tuple(deferred), timeout_ms, spent,
                reachable, unreachable, undecided, queries,
                telemetry_scope=telemetry_scope,
                query_memory_mb=query_memory_mb)
            deferred.clear()
    return (
        _rows(reachable, unreachable, undecided), tuple(queries), tuple(deferred),
        process_peak_rss_bytes(), unroll_ns, query_setup_ns,
    )


def _decide_singleton_chunk(work):
    """Ask this worker's singleton queries and report its measured peak RSS."""

    proc, env, steps, initial, goals, timeout_ms, artifact_ms, memory_mb, plan, \
        telemetry_scope, started, query_setup_reserve_ms, \
        reserved_participants = work
    if type(reserved_participants) is not int or reserved_participants < 0:
        raise ValueError("reserved query participants is a natural integer")
    _limit_worker_memory(memory_mb)
    query_memory_mb = resources.solver_memory_mb(memory_mb)
    spent = budgets_mod.Spent(ms=artifact_ms, max_queries=None, started=started)
    if spent.exhausted():
        why = spent.exhausted()
        return (
            [(goal, "unknown", why) for goal in goals],
            tuple(QueryCall(
                (goal,), SolverAnswer.UNKNOWN, reason=why, solver_calls=0)
                  for goal in goals),
            process_peak_rss_bytes(),
        )
    check = _budget_checkpoint(spent)
    try:
        prepared = _prepare_unroll(proc, env, steps, plan, _checkpoint=check)
        run = unroll(
            proc, env, steps, initial, plan=plan, goals=goals,
            _prepared=prepared, _checkpoint=check)
    except _ConstructionExpired as exc:
        found, ruled, open_, calls = _construction_unknown(goals, str(exc))
        return _rows(found, ruled, open_), calls, process_peak_rss_bytes()
    except MemoryError:
        return _memory_rows(goals, memory_mb), (), process_peak_rss_bytes()
    remaining_ms = spent.remaining_ms()
    if remaining_ms is not None and remaining_ms <= query_setup_reserve_ms:
        why = _refinement_budget_reason(
            remaining_ms=remaining_ms, required_ms=query_setup_reserve_ms)
        return (
            [(goal, "unknown", why) for goal in goals],
            tuple(QueryCall(
                (goal,), SolverAnswer.UNKNOWN, reason=why, solver_calls=0)
                  for goal in goals),
            process_peak_rss_bytes(),
        )
    reachable, unreachable, undecided, queries = {}, set(), {}, []
    _each(
        run, proc, goals, timeout_ms, spent,
        reachable, unreachable, undecided, queries,
        telemetry_scope=telemetry_scope,
        query_memory_mb=query_memory_mb,
        reserved_participants=reserved_participants)
    return (
        _rows(reachable, unreachable, undecided), tuple(queries),
        process_peak_rss_bytes(),
    )


def _chunks(values, count):
    """Stable, balanced chunks; execution order never becomes evidence order."""

    count = min(len(values), count)
    if count < 1:
        return ()
    return tuple(tuple(values[index::count]) for index in range(count))


def _process_pool(max_workers):
    """Create a fresh-process pool without forking a live Z3 runtime.

    Exact construction may load and invoke Z3 before refinement decides it
    needs workers.  Forking that process copies native solver state at an
    unsupported point and can make an otherwise valid public analysis lose a
    worker nondeterministically.  ``spawn`` reconstructs only the explicit,
    pickle-checked work record in each child.
    """

    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    return ProcessPoolExecutor(
        max_workers=max_workers, mp_context=get_context("spawn"))


def decide_goals(proc, env, goals, steps=1, initial=None, timeout_ms=QUERY_MS,
                 report=None, workers=None, artifact_ms=budgets_mod.ARTIFACT_MS,
                 max_queries=budgets_mod.ARTIFACT_QUERIES,
                 memory_mb=budgets_mod.ARTIFACT_MEMORY_MB, plan=None,
                 telemetry_scope=None, artifact_started=None, _progress=None,
                 transactions=None):
    """Decide each of ``goals`` against a run of ``steps`` steps.

    The complete open set shares a query.  An unresolved set is refined through
    balanced exact subqueries down to the singleton questions still needed.
    ``report`` is called as goals are settled.  A finite physical-query budget
    is consumed by one ordered coordinator, so its attribution cannot depend on
    process scheduling.  With no query-count ceiling, a positive ``workers``
    value may move singleton refinement into child processes; it never changes
    a question or its answer.
    """
    goals = list(goals)
    if _progress is not None:
        from .query_progress import QueryProgress

        if type(_progress) is not QueryProgress:
            raise TypeError("bounded query progress uses the typed audit protocol")
    if workers is not None \
            and (type(workers) is not int or workers < 1):
        raise ValueError("workers must be a positive integer or None")
    if artifact_started is not None \
            and type(artifact_started) not in (int, float):
        raise TypeError("an artifact clock starts at a monotonic number or is absent")
    if telemetry_scope is not None \
            and type(telemetry_scope) is not QueryTelemetryScope:
        raise TypeError("query telemetry uses the closed QueryTelemetryScope")
    if plan is not None:
        from ..symbolic.plan import EncodingPlan
        from ..symbolic.closure_proof import _prepare_supplied_positions

        if type(plan) is EncodingPlan:
            # A one-step diagnostic plan has no prepared production wrapper.
            plan.validate(proc, env, bound=steps)
        else:
            # Fully validate a public raw position plan once, then send the
            # prepared immutable value through local and worker paths.  A plan
            # already prepared by bind takes only the cheap boundary check.
            plan = _prepare_supplied_positions(plan, proc, env, steps)
    if not goals:
        # Nothing to decide, and building the formula to decide nothing costs
        # the whole unrolling.
        return Residual({}, set(), {}, steps, ())
    initial_worker_ceiling = budgets_mod.workers_for(
        goals,
        reservation_mb=budgets_mod.PARALLEL_WORKER_RESERVATION_MB,
        # ``memory_mb`` is the complete named artifact's aggregate ceiling.  The
        # batch allocation outside that artifact cannot authorise a larger pool.
        total_mb=memory_mb)
    requested_workers = workers
    workers = (initial_worker_ceiling if workers is None else
               min(workers, initial_worker_ceiling))
    # A process-shared counter can prevent a numerical overrun, but whichever
    # worker wins its final permit would then decide which catalogue goal gets
    # an answer.  The declared artifact would no longer be reproducible across
    # worker counts.  Keep the finite global budget in catalogue order instead;
    # callers that explicitly leave query count unbounded may still parallelise
    # under the independent wall-clock and memory ceilings below.
    if max_queries is not None:
        workers = 1
    if workers > 1:
        # A process-local checkpoint cannot cross a solver worker boundary.
        # Preserve the existing parallel policy and final batched audit there.
        _progress = None
    reachable, unreachable, undecided = {}, set(), {}
    def record(goal, verdict, detail):
        if verdict == "sat":
            reachable[goal] = detail
        elif verdict == "unsat":
            unreachable.add(goal)
        else:
            undecided[goal] = detail
        if report is not None:
            report(goal, verdict)

    started = time.monotonic() if artifact_started is None else artifact_started
    if transactions is not None:
        # These predicates depend on accepted/emitted history, not just a source
        # occurrence. Keep the full horizon even for a stateless model. The same
        # set-wise solver, witness decoding and artifact budgets still apply.
        spent = budgets_mod.Spent(ms=artifact_ms, max_queries=max_queries, started=started)
        exhausted = spent.exhausted()
        if exhausted:
            return Residual({}, set(), {goal: exhausted for goal in goals}, steps,
                tuple(QueryCall((goal,), SolverAnswer.UNKNOWN, reason=exhausted,
                                solver_calls=0) for goal in goals))
        try:
            run = unroll(
                proc, env, steps, initial, plan=plan, transactions=transactions,
                _checkpoint=_budget_checkpoint(spent))
        except _ConstructionExpired as exc:
            found, ruled, open_, queries = _construction_unknown(goals, str(exc))
        else:
            found, ruled, open_, queries = decide_open(
                run, proc, goals, timeout_ms, spent, telemetry_scope=telemetry_scope,
                query_memory_mb=resources.solver_memory_mb(memory_mb), _progress=_progress)
        if report is not None:
            for goal in goals:
                report(goal, "sat" if goal in found else "unsat" if goal in ruled else "unknown")
        return Residual(found, ruled, open_, steps, queries)
    # This is an existential goal-query projection, not a shorter trace model.
    # Feasibility, complete unrolling, domain identity and replay still use N.
    # The physical prefix reuses the same planner/solver (including fragments);
    # all returned SAT records are lifted before leaving this provider.
    if not budgets_mod.Spent(
            ms=artifact_ms, max_queries=max_queries, started=started).exhausted():
        from .goal_projection import stateless_goals

        projection = stateless_goals(proc, env, steps, initial, plan)
        if projection is not None:
            prefix = decide_goals(
                proc, env, goals, steps=1, timeout_ms=timeout_ms,
                report=report, workers=requested_workers,
                artifact_ms=artifact_ms, max_queries=max_queries,
                memory_mb=memory_mb, plan=projection.plan,
                telemetry_scope=telemetry_scope, artifact_started=started,
                **({"_progress": _progress.projected(projection)}
                   if _progress is not None else {}))
            return projection.restore(prefix)
    if workers <= 1:
        query_memory_mb = resources.solver_memory_mb(memory_mb)
        spent = budgets_mod.Spent(
            ms=artifact_ms, max_queries=max_queries, started=started)
        # Formula construction is part of the artifact budget.  In particular,
        # ``close_over(..., batch=k)`` may call this entry point again after an
        # earlier batch spent the shared wall clock.  Building the same unrolling
        # before consulting that clock made every later batch pay the dominant
        # setup cost even though it could not launch a solver query.  Preserve the
        # exact UNKNOWN transcript, but do not perform work the declared policy
        # has already ruled out.
        exhausted = spent.exhausted()
        if exhausted:
            calls = tuple(
                QueryCall(
                    (goal,), SolverAnswer.UNKNOWN,
                    reason=exhausted, solver_calls=0)
                for goal in goals
            )
            return Residual(
                {}, set(), {goal: exhausted for goal in goals}, steps, calls)
        check = _budget_checkpoint(spent)
        try:
            prepared = _prepare_unroll(proc, env, steps, plan, _checkpoint=check)
            slice_index = prepared.slicing()
            events = slice_index.events
            schedule = _occurrence_schedule(goals, events)
            split = _preunroll_split(proc, goals, steps, slice_index, schedule)
            check()
        except _ConstructionExpired as exc:
            found, ruled, open_, queries = _construction_unknown(goals, str(exc))
            for goal, why in open_.items():
                record(goal, "unknown", why)
            return Residual(found, ruled, open_, steps, queries)
        if split:
            found, ruled, open_, queries = _decide_occurrence_goals(
                proc, env, goals, steps, initial, plan, prepared, schedule,
                slice_index, timeout_ms, spent, telemetry_scope=telemetry_scope,
                query_memory_mb=query_memory_mb, _progress=_progress)
            for goal, trace in found.items():
                record(goal, "sat", trace)
            for goal in ruled:
                record(goal, "unsat", None)
            for goal, why in open_.items():
                record(goal, "unknown", why)
            return Residual(reachable, unreachable, undecided, steps, queries)

        try:
            run = unroll(
                proc, env, steps, initial, plan=plan, goals=goals,
                _prepared=prepared, _checkpoint=check)
        except _ConstructionExpired as exc:
            found, ruled, open_, queries = _construction_unknown(goals, str(exc))
        else:
            found, ruled, open_, queries = decide_open(
                run, proc, goals, timeout_ms, spent,
                telemetry_scope=telemetry_scope,
                query_memory_mb=query_memory_mb, _progress=_progress)
        for goal, trace in found.items():
            record(goal, "sat", trace)
        for goal in ruled:
            record(goal, "unsat", None)
        for goal, why in open_.items():
            record(goal, "unknown", why)
        return Residual(reachable, unreachable, undecided, steps, queries)

    # Worker count is an execution choice, never a logical partition.  A lone
    # prefix worker first poses the set-wise queries.  Starting the
    # full pool here used to allocate one formula process per *possible* goal
    # before we knew whether any singleton refinement was needed.  Under an
    # 8-GiB aggregate job ceiling, that made a 90-goal model kill itself before
    # asking its first question.
    with _process_pool(max_workers=1) as pool:
        assert max_queries is None
        prefix = pool.submit(
            _decide_prefix_chunk,
            (proc, env, steps, initial, goals, timeout_ms,
             artifact_ms, memory_mb, plan, telemetry_scope,
             started))
        prefix_rows, prefix_queries, deferred, prefix_peak, unroll_ns, \
            query_setup_ns = prefix.result()
        all_rows = list(prefix_rows)
        all_queries = list(prefix_queries)

        selected = tuple(deferred)

    # Refinement repeats the same immutable unrolling and its query setup.  If the
    # remaining absolute budget is already below that work's measured reserve, do
    # not start workers that can only expire before posing a singleton query.
    setup_reserve_ms = _observed_setup_reserve_ms(
        unroll_ns + query_setup_ns)
    query_setup_reserve_ms = _observed_setup_reserve_ms(query_setup_ns)
    remaining_ms = budgets_mod.Spent(
        ms=artifact_ms, max_queries=None, started=started).remaining_ms()
    if selected and remaining_ms is not None and remaining_ms <= setup_reserve_ms:
        why = _refinement_budget_reason(
            remaining_ms=remaining_ms, required_ms=setup_reserve_ms)
        all_rows.extend((goal, "unknown", why) for goal in selected)
        all_queries.extend(QueryCall(
            (goal,), SolverAnswer.UNKNOWN, reason=why, solver_calls=0)
                           for goal in selected)
        selected = ()

    singleton_rows = {}
    singleton_queries = {}

    def merge_singleton_result(rows, calls):
        for goal, verdict, detail in rows:
            if goal in singleton_rows:
                raise ValueError(
                    f"singleton refinement answered {goal!r} twice")
            singleton_rows[goal] = (verdict, detail)
        for call in calls:
            if type(call) is not QueryCall or len(call.goals) != 1:
                raise ValueError(
                    "a singleton worker returned a non-singleton query call")
            goal = call.goals[0]
            if goal in singleton_queries:
                raise ValueError(
                    f"singleton refinement queried {goal!r} twice")
            singleton_queries[goal] = call

    # Prefix memory measures unrolling and the shared batch questions, but a
    # target-specific singleton solve may bit-blast substantially more.  Run one
    # real singleton in a lone process before opening the remaining pool.  Its
    # answer is retained as evidence rather than repeated; its observed peak
    # determines how many further copies fit inside the aggregate artifact.
    remaining_selected = selected
    measured_peak = prefix_peak
    if remaining_selected:
        pilot = remaining_selected[:1]
        with _process_pool(max_workers=1) as pool:
            future = pool.submit(
                _decide_singleton_chunk,
                (proc, env, steps, initial, pilot, timeout_ms,
                 artifact_ms, memory_mb, plan, telemetry_scope, started,
                 query_setup_reserve_ms,
                 len(remaining_selected) - len(pilot)))
            rows, calls, pilot_peak = future.result()
        merge_singleton_result(rows, calls)
        measured_peak = max(measured_peak, pilot_peak)
        remaining_selected = remaining_selected[1:]

    # The pilot consumed real artifact time.  Do not rebuild more formulas when
    # the measured setup can no longer fit in the remaining absolute budget.
    remaining_ms = budgets_mod.Spent(
        ms=artifact_ms, max_queries=None, started=started).remaining_ms()
    if remaining_selected and remaining_ms is not None \
            and remaining_ms <= setup_reserve_ms:
        why = _refinement_budget_reason(
            remaining_ms=remaining_ms, required_ms=setup_reserve_ms)
        merge_singleton_result(
            [(goal, "unknown", why) for goal in remaining_selected],
            tuple(QueryCall(
                (goal,), SolverAnswer.UNKNOWN, reason=why, solver_calls=0)
                  for goal in remaining_selected))
        remaining_selected = ()

    measured_reservation = budgets_mod.reservation_for_peak(measured_peak)
    safe_singleton_workers = budgets_mod.workers_for(
        remaining_selected, reservation_mb=measured_reservation,
        total_mb=memory_mb)
    singleton_workers = min(len(remaining_selected), safe_singleton_workers)
    if requested_workers is not None:
        singleton_workers = min(singleton_workers, requested_workers)
    if singleton_workers:
        with _process_pool(max_workers=singleton_workers) as pool:
            futures = [
                pool.submit(
                    _decide_singleton_chunk,
                    (proc, env, steps, initial, chunk, timeout_ms,
                     artifact_ms, memory_mb, plan, telemetry_scope, started,
                     query_setup_reserve_ms, 0))
                for chunk in _chunks(remaining_selected, singleton_workers)
            ]
            for future in futures:
                rows, calls, _peak = future.result()
                merge_singleton_result(rows, calls)
    if set(singleton_rows) != set(selected):
        raise ValueError(
            "singleton refinement did not answer its exact goal set")
    all_rows.extend(
        (goal, *singleton_rows[goal]) for goal in selected)
    all_queries.extend(
        singleton_queries[goal] for goal in selected
        if goal in singleton_queries)
    # Physical workers finish out of order, while the published evidence must
    # not depend on the chosen worker count.  Canonical preorder puts a parent
    # set before its refinements and otherwise follows catalogue order, which
    # is the same order produced by the one-worker depth-first schedule.
    all_queries = list(_canonical_queries(all_queries, goals))

    by_goal = {}
    for goal, verdict, detail in all_rows:
        if goal in by_goal:
            raise ValueError(f"the exact query path answered {goal!r} twice")
        by_goal[goal] = (verdict, detail)
    if set(by_goal) != set(goals):
        raise ValueError("the exact query path did not answer its complete goal set")
    for goal in goals:
        verdict, detail = by_goal[goal]
        record(goal, verdict, detail)
    return Residual(
        reachable, unreachable, undecided, steps, tuple(all_queries))
