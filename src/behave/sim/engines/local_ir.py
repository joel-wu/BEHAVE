"""Shared local goal queries over typed command IR.

    E_M(s_k, u, s_{k+1}, y, h)  and  h_gamma

Every state variable is pinned to the supplied snapshot. Inputs may be fixed as
well, for example the scheduler's activation flags in an RTL source batch. Only
the remaining inputs are free. Activation-aware IR marks define the hit terms;
the query does not substitute end-of-step predicate sampling for execution.

SAT proposes an input for native replay; it does not credit a hit. LOCAL_UNSAT
applies only to this snapshot and these input restrictions, never to all traces.
"""

from __future__ import annotations

import time

import z3

from behave.core import runtime_defaults as defaults

from ..symbolic import ir, typecheck as types
from ..symbolic import ranges
from ..symbolic import slicing
from ..core.construction_checkpoint import ConstructionCheckpoint
from ..symbolic.encoder import (
    _encode_query_step,
    _encode_step,
    _EncodingPaused,
    _goal_occurrence_keep,
    _materialize_functional,
    _query_procedure,
    _QueryPrefix,
    _StepConstruction,
    encode_concrete_value,
)
from ..symbolic.validation import (
    EncodingError,
    _checked_procedure_validation,
    _procedure_validation,
    validate_procedure,
)
from . import probing
from ..search_policy import remaining_solver_ms

__all__ = ["local_hit", "local_hit_any", "pin_state"]


class _ProbeDeadlineExceeded(TimeoutError):
    """An unfinished formula supplies neither a hit nor an UNSAT result."""


class _LocalQueryConstruction:
    """One exact question's work or encoding diagnostic, never a goal verdict."""

    def __init__(self):
        self.encoding = _StepConstruction()
        self.registration = None
        self.formula = None
        self.previous_formula = None
        self.selected = None
        self.solver = None
        self.failure = None
        self.checkpoint = ConstructionCheckpoint()

    def finish_prefix(self):
        """Discard only this prefix query, retaining the encoding continuation."""
        self.encoding.prefix = None
        self.previous_formula = self.formula
        self.registration = self.formula = self.selected = self.solver = None


class _LocalQueryStructure:
    """Syntax-only facts shared by one worker's snapshot-specific plans."""

    def __init__(self):
        self.validation = None
        self.slice_index = None
        self.cones = {}
        self.derived_cones = {}
        self.cone_work = None
        self.component_batches = {}
        self.literal_false_goals = None
        self.control = None
        self.limits = None
        self.state_dependencies = None
        self.command_preparations = None


class _LocalQueryPreparation:
    """Private preparation for one worker-owned, read-only procedure and carrier.

    The worker does not mutate its lowered procedure after creating this owner.
    This is not a cache over arbitrary caller-owned mutable IR: the public
    one-shot entry creates a new owner on every call. Only static cone/plan/
    control facts are retained, not concrete state, formulas or solver answers.
    Position-specific plans keep their own bindings.
    """

    def __init__(self, proc, env):
        self._proc, self._env = proc, dict(env)
        self._structure = _LocalQueryStructure()
        self._queries = {}
        self._plan = None
        self._unrestricted_plan = None
        self._plan_inputs = ()
        self._plan_false_goals = None
        self._snapshot_preparation = None
        self._prefix_preparation = None
        self._preparation_frontier = None

    def validation(self):
        if self._structure.validation is None:
            # Use the same exact persistent arrays as transition templates.
            # The validation token selects that encoder representation too;
            # this is not a cheaper cost estimate for a still-dense formula.
            self._structure.validation = _procedure_validation(self._proc, compact_arrays=True)
        return self._structure.validation

    def events(self):
        if self._structure.slice_index is None:
            self._structure.slice_index = slicing.index(self._proc)
        return self._structure.slice_index.events

    def limits(self):
        if self._structure.limits is None:
            codes = self._proc.semantics.analysis_errors
            self._structure.limits = tuple(dict.fromkeys(
                command.goal for command, _under in self.events()
                if isinstance(command, ir.Fail) and command.code in codes))
        return self._structure.limits

    def state_dependencies(self):
        """One syntax-only grouping index, shared by all seed selections."""
        if self._structure.state_dependencies is None:
            self.validation()
            self._structure.state_dependencies = slicing.goal_state_dependencies(self._proc)
        return self._structure.state_dependencies

    def command_preparations(self):
        """Search-only command effects, shared across this source's seeds."""
        if self._structure.command_preparations is None:
            from .behavior.prefix_generation import command_preparations

            self.validation()
            self._structure.command_preparations = command_preparations(self._proc)
        return self._structure.command_preparations

    def cone(self, goal, site):
        key = site, goal
        if key not in self._structure.cones:
            derived = self._structure.derived_cones.get(key)
            if derived is None:
                derived = _goal_occurrence_keep(
                    self._proc, goal, site, self.events(), _index=self._structure.slice_index)
            else:
                missing = self._structure.slice_index.binding_faults(derived)
                if missing:
                    raise EncodingError("the occurrence cone is not closed: " + str(missing[0]),
                                        kind="goal")
            self._structure.cones[key] = derived
        return self._structure.cones[key]

    def prepare_cones(self, occurrences, checkpoint):
        """Share source-only dependency work; each cone still gets its audit."""
        self.events()
        indexed, structure = self._structure.slice_index, self._structure
        if indexed.loop_bodies:
            return
        while True:
            if structure.cone_work is None:
                missing = tuple(key for key in occurrences
                                if key not in structure.cones and key not in structure.derived_cones)
                if not missing:
                    return
                structure.cone_work = indexed.occurrence_batch(missing)
            checkpoint()
            try:
                row = next(structure.cone_work)
            except StopIteration:
                structure.cone_work = None
                continue
            if row is not None:
                key, cut = row
                structure.derived_cones[key] = frozenset(cut.kept)

    def components(self, occurrences, checkpoint):
        """Resume compact, syntax-only planning for this exact target selection."""
        self.events()
        key = tuple(occurrences)
        batches = self._structure.component_batches
        if key not in batches:
            computations = _computation_ids(self.events(), self.validation().graph)
            work = self._structure.slice_index.occurrence_components(key, computations)
            batches[key] = [work, []]
        batch = batches[key]
        while batch[0] is not None:
            checkpoint()
            try:
                row = next(batch[0])
            except StopIteration:
                batch[0] = None
                break
            if row is not None:
                batch[1].append(row)
        return batch[1]

    def for_state(self, state):
        """Plan the source CFG at its validated, fixed local entry state.

        A local question does not need an invariant for every possible state.
        Check original declared carriers before specializing a CFG plan, whose
        retained loops otherwise enumerate states this question cannot enter.
        Term-only cones keep their shared plan and need only missing carriers.
        Inputs are unchanged; neither form constrains later state updates to
        their entry bounds. Retain only the latest snapshot-specific plan.
        """
        inferred = {name: ranges._state_product_range(state[name])
                    for name in self._proc.state
                    if self._env.get(name) is None and name in state}
        if self.validation().graph is not None:
            # This checks the original domain and recursive shape. The later
            # pin uses the specialized representation width, not this domain's
            # width; it must not replace this original admission obligation.
            pin_state(self._proc, {**self._env, **inferred}, state,
                      _validation=self.validation())
            # This is a concrete native entry, not an arbitrary array with
            # the same min/max. Preserve per-cell facts using the existing
            # compact product; otherwise one large cell widens every counter
            # and dispatch lookup before the first command is encoded.
            inferred.update((name, ranges._state_product_range(state[name]))
                            for name in self._proc.state)
        if not inferred:
            return self
        env = {**self._env, **inferred}
        if env == self._env:
            return self
        owner = self._snapshot_preparation
        if owner is None or owner._env != env:
            owner = self._snapshot_preparation = _LocalQueryPreparation(self._proc, env)
            owner._structure = self._structure
        return owner

    def for_query(self, goals):
        """Prepare the audited goal dependency body, not unrelated computation.

        The original procedure is admitted first. All occurrences and earlier
        failures are retained by the shared slicer; the clone keeps its full
        input/state contract but cannot certify a complete output transition.
        Each immutable selection owns its plan and resumable command identities.
        """
        validation = self.validation()
        # Plan the immutable dependency union once for this selection, including
        # retained source loops. Different hit tests share this prepared body;
        # a changed target set cannot inherit a narrower projection's answers.
        key = frozenset(goals)
        if key not in self._queries:
            events = self.events()
            keep = slicing.for_run(self._proc, key, steps=1, events=events)
            owner = self
            if (keep is not None
                    and not slicing.audit(self._proc, key, keep, events=events)
                    and any(id(command) not in keep for command, _under in events
                            if not isinstance(command, ir.Skip))):
                projected = _query_procedure(self._proc, keep, validation)
                owner = _LocalQueryPreparation(projected, self._env)
            self._queries[key] = owner
        return self._queries[key]

    def plan(self, validation, *, fixed_inputs=()):
        from ..symbolic.plan import ExpressionPosition, plan_encoding

        # A local question may pin command/data inputs before encoding. Its
        # range/loop plan must use the same bindings, not all possible commands.
        # Keep the common unrestricted plan and the latest specialized one,
        # not a history of every fixed payload. Changing bindings also clears
        # literal-false goals derived from the preceding specialized plan.
        fixed_inputs = tuple(sorted(dict(fixed_inputs).items()))
        if fixed_inputs != self._plan_inputs:
            self._plan = None if fixed_inputs else self._unrestricted_plan
            self._plan_false_goals = None
            self._plan_inputs = fixed_inputs
        if self._plan is None:
            plan = plan_encoding(self._proc, self._env, fixed_inputs=fixed_inputs,
                                 _procedure_validation=validation)
            # Expression diagnostics are complete facts about this fixed
            # procedure/carrier, not interrupted preparation. Audited cones
            # may omit their commands; rebuilding the whole plan cannot repair
            # them. Incomplete boundary/loop/resource planning is not retained.
            if all(isinstance(item.what, ExpressionPosition)
                   and item.what.role != "trip_count" and item.kind != "resource"
                   for item in plan.unresolved):
                self._plan = plan
                if not fixed_inputs:
                    self._unrestricted_plan = plan
            return plan
        return self._plan

    def probe(self, state, goals, timeout_ms=defaults.SIM_SEARCH_QUERY_MS, tag="x", **options):
        return _local_hit_any(self._proc, self._env, state, goals, timeout_ms, tag,
                              _preparation=self, **options)

    def false_goals(self, plan, checkpoint):
        from ..behavior.goal_projection import _planned_false_goals

        if plan is self._plan and self._plan_false_goals is not None:
            return self._plan_false_goals
        proved = _planned_false_goals(self._proc, plan, checkpoint=checkpoint)
        if plan is self._plan:
            self._plan_false_goals = proved
        return proved

    def literal_false_goals(self):
        from ..behavior.goal_projection import literal_false_goals

        if self._structure.literal_false_goals is None:
            self._structure.literal_false_goals = literal_false_goals(self._proc)
        return self._structure.literal_false_goals

    def blocked_marks(self, impossible):
        from ..behavior.goal_control import GoalControl

        if self._structure.control is None:
            # validation() already checked the body and retained loop/source
            # graphs. They belong to this immutable worker preparation.
            self._structure.control = GoalControl(self._proc)
        return self._structure.control.blocked_marks(impossible)


def pin_state(proc, env, state, tag="", *, width=None, _validation=None):
    """The snapshot as terms the encoder can read, or a refusal.

    Every state variable of the procedure has to be in ``state``. A missing one
    would be left free by `encode_step`, and a free state variable changes the
    question from "does an input reach this goal from here" to "does some state
    reach it" -- which has more answers, so the provider would report a candidate
    that no run can present.
    """
    # This helper creates concrete Z3 leaves itself.  Use the same solver-free
    # capability gate as the encoder before doing so.
    if _validation is None:
        validate_procedure(proc)
    else:
        _checked_procedure_validation(_validation, proc)
    state_types = dict(proc.semantics.state_types)
    pinned = {}
    for name in proc.state:
        if name not in state:
            raise EncodingError(
                f"the snapshot does not say what {name} holds, and a state "
                f"variable left free asks a different question", kind="shape")
        rng = env.get(name)
        if rng is None:
            raise EncodingError(
                f"{name} has no range, so it has no width", kind="range")
        pinned[name] = encode_concrete_value(
            state[name], rng, name, kind=state_types.get(name), width=width)
    return pinned


def _model_value(model, term, kind, bounds):
    if isinstance(kind, types.Array):
        term = _materialize_functional(term)
        if type(term) is not list or len(term) != kind.cells:
            raise ValueError("an exact input model has another array shape")
        return [_model_value(model, item, kind.elem, bounds) for item in term]
    numeral = model.eval(term, model_completion=True)
    raw = numeral.as_long()
    if kind == types.BOOLEAN:
        if raw not in (0, 1):
            raise ValueError("the exact model assigned a non-Boolean value")
        return bool(raw)
    lo, hi = bounds
    width = term.size()
    value = raw - (1 << width) if lo < 0 and raw >= (1 << (width - 1)) else raw
    if not lo <= value <= hi:
        raise ValueError("the exact model decoded outside its input carrier")
    return value


def _logical_candidate(proc, physical):
    bindings = tuple(binding for role, binding in proc.semantics.value_bindings
                     if role == "input")
    logical = ({binding.name: binding.reconstruct(physical) for binding in bindings}
               if bindings else dict(physical))
    proc.semantics.validate_inputs(logical)
    return logical


def _computation_ids(events, graph):
    commands = (cmd for cmd, _under in events) if graph is None else (
        cmd for block in graph.blocks.values() for instruction in block.instrs
        for cmd in ir.walk(instruction))
    # CFG reachability flags alone must not join independent computations.
    return {id(cmd) for cmd in commands if isinstance(cmd, (ir.Assign, ir.Store, ir.For))}


def _shared_dependency_queries(groups, events, graph=None, *, prefixes=False):
    """Encode connected data/loop cones once; leave independent cones separate.

    Shared ancestry alone (Seq/If/Mark) does not justify a larger formula.
    Components share actual assignments, stores or loop expansion. Their union
    retains every audited dependency and every occurrence's own hit predicate.
    Original groups remain available if the larger encoding is unsupported.
    """
    components, owners = {}, {}
    computations = _computation_ids(events, graph)
    parents = list(range(len(groups)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, keep in enumerate(groups):
        for command in keep & computations:
            previous = owners.setdefault(command, index)
            parents[root(index)] = root(previous)
    for index, (keep, members) in enumerate(groups.items()):
        components.setdefault(root(index), []).append((keep, members, False))
    queries, refinements = [], {}

    def append(children):
        keep = frozenset().union(*(child[0] for child in children))
        members = sorted(member for child in children for member in child[1])
        queries.append((keep, members, False))
        if len(children) > 1:
            refinements[keep, tuple(members)] = children

    for children in components.values():
        if prefixes:
            # An owned encoder yields each early goal before continuing its
            # suffix. Share this component's prefix instead of rebuilding it
            # for minimal cones and then again for their downstream union.
            # Independent components and unsupported-union fallbacks remain.
            append(children)
            continue
        # Try proper dependency subsets first: an easy hit must not wait for
        # all downstream arithmetic/loops. Compare computation, not Mark IDs,
        # which necessarily differ even when the underlying data cone is tiny.
        footprints = [child[0] & computations for child in children]
        minimal = {i for i, footprint in enumerate(footprints)
                   if not any(other < footprint for other in footprints)}
        if len(minimal) < len(children):
            for i in sorted(minimal):
                append([children[i]])
            append([child for i, child in enumerate(children) if i not in minimal])
        else:
            append(children)
    return queries, refinements


def _local_hit_any(proc, env, state, goals, timeout_ms=defaults.SIM_SEARCH_QUERY_MS, tag="x", *,
                  position_plan=None, position=0, fixed_inputs=None, input_constraints=None,
                  observation_query=None, _preparation=None, _proved_unsat=None,
                  _constructions=None):
    """A local miss is a proof only when the representation also completed.

    An artifact exception is a real terminal source execution. Capacity and
    unwinding limits are different: the source may still reach a requested
    goal after the represented prefix ends. Keep that second query under the
    original state, legal inputs, position and remaining time. A SAT goal still
    proposes a native replay; a reachable representation limit is not a goal
    verdict or a candidate failure.
    """
    timeout_ms = remaining_solver_ms(timeout_ms)
    if _preparation is None:
        _preparation = _LocalQueryPreparation(proc, env)
    options = dict(
        position_plan=position_plan, position=position,
        fixed_inputs=fixed_inputs, input_constraints=input_constraints,
        observation_query=observation_query, _preparation=_preparation,
        _proved_unsat=_proved_unsat, _constructions=_constructions)
    answer = _query_local_hit_any(
        proc, env, state, goals, timeout_ms, tag, **options)
    if answer.outcome is not probing.Outcome.LOCAL_UNSAT:
        return answer
    limits = _preparation.limits() if proc.semantics.analysis_errors else ()
    if not limits:
        return answer
    if any(not isinstance(goal, str) or not goal for goal in limits):
        return probing.ProbeResult(
            probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
            detail="a representation limit has no queryable occurrence",
            solve_ms=answer.solve_ms)
    remaining = int(timeout_ms - answer.solve_ms)
    if remaining < 1:
        return probing.ProbeResult(
            probing.Outcome.UNKNOWN, probing.Scope.EXACT_LOCAL_HIT,
            detail="the local budget ended before representation completion was checked",
            solve_ms=answer.solve_ms)
    closure = _query_local_hit_any(
        proc, env, state, limits, remaining, tag + "_limits", **options)
    elapsed = answer.solve_ms + closure.solve_ms
    if closure.outcome is probing.Outcome.SAT:
        return probing.ProbeResult(
            probing.Outcome.UNKNOWN, probing.Scope.EXACT_LOCAL_HIT,
            detail=f"the representation can stop at {closure.goal!r} before source completion",
            solve_ms=elapsed)
    result = answer if closure.outcome is probing.Outcome.LOCAL_UNSAT else closure
    return probing.ProbeResult(
        result.outcome, result.scope, detail=result.detail, solve_ms=elapsed)


def _query_local_hit_any(proc, env, state, goals, timeout_ms=defaults.SIM_SEARCH_QUERY_MS, tag="x", *,
                  position_plan=None, position=0, fixed_inputs=None, input_constraints=None,
                  observation_query=None, _preparation=None, _proved_unsat=None,
                  _constructions=None):
    """Find one input reaching any requested goal from one concrete state.

    Lowering may copy one source goal into many IR occurrences.  Occurrences are
    grouped when their audited command selections share computation, and groups
    are visited in first-occurrence order. One union encoding and disjunctive query
    serve every member, retaining every dependency. SAT at any occurrence supplies
    a candidate for concrete replay.  LOCAL_UNSAT is returned only after every
    occurrence of every requested goal is UNSAT; an unsupported or timed-out
    occurrence therefore keeps the logical request open.

    A completed all-occurrence proof can also answer marks necessarily reached
    after that goal in the admitted control-flow graph. That implication is
    scoped to the same local question; it neither removes catalogue entries nor
    proves anything about another state or input domain.

    ``timeout_ms`` is one wall-clock budget for formula construction and every
    physical solver call.  Structural validation and binding of the prepared
    representation happen before that query budget begins; the isolated worker's
    outer deadline bounds those admission checks.  The query budget is not renewed
    per occurrence.

    A private selection may retain construction for the identical state,
    targets, input domain and observation history. A retained construction pause
    returns CONSTRUCTING so the scheduler resumes this question within its next
    existing quantum. A genuine solver UNKNOWN still advances the worklist.
    This neither extends the shared search lease nor reuses a solver answer.

    ``fixed_inputs`` pins physical IR input names, before formula construction.
    It must not contain state or local names. This preserves the declared input
    domain while restricting the query (including inactive source processes).
    The returned candidate retains the fixed values.
    """
    goals = tuple(goals)
    if not goals or any(type(goal) is not str or not goal for goal in goals):
        raise TypeError("a local exact query names nonempty string goals")
    if len(goals) != len(set(goals)):
        raise ValueError("a local exact query names each goal once")
    timeout_ms = remaining_solver_ms(timeout_ms)
    try:
        if proc.input_framing and input_constraints is None:
            raise EncodingError("a framed local query needs its accepted-input prefix and suffix",
                                kind="shape")
        if _preparation is None:
            _preparation = _LocalQueryPreparation(proc, env)
        # Explicit position plans are bound to the full procedure. Output
        # observations likewise require that transition, not a hit projection.
        # Input-only observations precede computation and retain their separate
        # framing/history constraints below.
        if position_plan is None:
            _preparation.validation()
            _preparation = _preparation.for_state(state)
            env = _preparation._env
            observed = (() if observation_query is None else
                        tuple(goal for goal in goals if goal in observation_query.goals))
            if all(observation_query.input_only(goal) for goal in observed):
                _preparation = _preparation.for_query(
                    tuple(goal for goal in goals if goal not in observed))
                proc = _preparation._proc
        validation = _preparation.validation()
        if position_plan is None:
            step_env = env
            pre = pin_state(
                proc, step_env, state, tag=tag, _validation=validation)
            step_plan = occurrence_carriers = scalar_width = None
            plan_prevalidated = False
        else:
            from ..symbolic.closure_proof import _PreparedPositionPlan

            if type(position_plan) is not _PreparedPositionPlan:
                raise TypeError(
                    "a local position query needs a prepared position plan")
            plan = position_plan.checked_for(
                proc, env, position_plan.bound,
                _procedure_validation=validation)
            if not 0 <= position < plan.bound:
                raise EncodingError(
                    "the accepted source position exceeds the prepared plan",
                    kind="range")
            step_env = plan.environment_at(position)
            scalar_width = plan.scalar_width
            pre = pin_state(
                proc, plan.state_layers[position], state, tag=tag,
                width=scalar_width, _validation=validation)
            step_plan = plan.steps[position]
            occurrence_carriers = plan.occurrence_layers[position]
            plan_prevalidated = True
        input_types = dict(proc.semantics.input_types)
        for name, value in (fixed_inputs or {}).items():
            if name not in proc.inputs:
                raise EncodingError(
                    f"{name!r} is not a procedure input", kind="shape")
            pre[name] = encode_concrete_value(
                value, step_env[name], name, kind=input_types[name],
                width=scalar_width)
    except (EncodingError, KeyError, TypeError, ValueError) as exc:
        return probing.ProbeResult(
            probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
            detail=str(exc), solve_ms=0.0)

    # The representation above is admitted once under the worker deadline.  This
    # clock measures the logical query itself: building occurrence formulae and
    # asking the solver.  Counting a repeated integrity walk against a five-second
    # solver budget made large, already-prepared procedures answer UNKNOWN before
    # a single formula reached Z3.
    started = time.perf_counter()
    elapsed = lambda: (time.perf_counter() - started) * 1000.0

    def checkpoint():
        if elapsed() >= timeout_ms:
            raise _ProbeDeadlineExceeded

    def construction_checkpoint():
        if elapsed() >= timeout_ms:
            raise _EncodingPaused

    # Use the same all-occurrence literal proof as bounded closure, after the
    # procedure, position and concrete state have been checked. A false goal
    # contributes nothing to the disjunction; it needs no dependency cone. This
    # remains a local generation answer, never a bounded reachability certificate.
    ruled_out = _preparation.literal_false_goals()
    active = tuple(goal for goal in goals if goal not in ruled_out)
    if not active:
        return probing.ProbeResult(
            probing.Outcome.LOCAL_UNSAT, probing.Scope.EXACT_LOCAL_HIT,
            detail="every requested hit predicate is identically false",
            solve_ms=elapsed())

    requested = set(active)
    events = _preparation.events()
    occurrences = [
        (site, command.goal)
        for site, (command, _under) in enumerate(events)
        if isinstance(command, (ir.Mark, ir.Fail))
        and command.goal in requested
    ]
    present = {goal for _site, goal in occurrences}
    observed = tuple(goal for goal in active if observation_query is not None
                     and goal in observation_query.goals)
    missing = tuple(goal for goal in active if goal not in present and goal not in observed)
    unknown = []
    construction_pending = False
    unsupported = [f"{goal!r} has no lowered occurrence" for goal in missing]
    input_bounds = dict(proc.semantics.solver_input_bounds())

    def singletons(members):
        _preparation.prepare_cones(members, checkpoint)
        groups = {}
        for site, goal in members:
            checkpoint()
            try:
                keep = _preparation.cone(goal, site)
            except (EncodingError, KeyError, TypeError, ValueError) as exc:
                unsupported.append(f"{goal!r} occurrence {site}: {exc}")
                continue
            groups.setdefault(keep, []).append((site, goal))
        return groups

    try:
        compact = _constructions is not None and not _preparation._structure.slice_index.loop_bodies
        if compact:
            components = _preparation.components(occurrences, checkpoint)
            # A failed singleton audit cannot be hidden by another member's
            # writes. Preserve the original per-occurrence diagnostics/fallback.
            compact = not any(faults for _keep, _members, faults in components)
        if compact:
            queries = [(keep, list(members), False) for keep, members, _faults in components]
            # Independent fallback cones are materialized only if this union
            # cannot be encoded. Their exact questions and audits are unchanged.
            refinements = {(keep, tuple(members)): None for keep, members, _ in queries
                           if len(members) > 1}
        else:
            queries, refinements = _shared_dependency_queries(
                singletons(occurrences), events, validation.graph,
                prefixes=_constructions is not None)
    except _ProbeDeadlineExceeded:
        return probing.ProbeResult(
            probing.Outcome.CONSTRUCTING if _constructions is not None else probing.Outcome.UNKNOWN,
            probing.Scope.EXACT_LOCAL_HIT,
            detail="shared occurrence planning paused", solve_ms=elapsed())
    if observed:
        inputs = [(None, goal) for goal in observed if observation_query.input_only(goal)]
        outputs = [(None, goal) for goal in observed if not observation_query.input_only(goal)]
        # Accepted-input predicates depend only on declared inputs and the fixed
        # observation prefix. The procedure body cannot revoke that acceptance,
        # even if it subsequently fails. Retain input-domain/frame constraints,
        # but do not expand unrelated computation to answer this question.
        if inputs:
            queries.append((frozenset(), inputs, True))
        if outputs:
            queries.append((None, outputs, True))

    # Source CFG joins carry guard facts even without retained loops. Use
    # their occurrence plan instead of rediscovering weaker widths from the
    # flattened predicated term. The private owner derives it only once.
    needs_plan = step_plan is None and (
        validation.graph is not None or any(isinstance(cmd, ir.For) for cmd, _under in events))
    plan_false_goals = None
    proved = set() if _proved_unsat is None else _proved_unsat
    sites_by_goal = {}
    for site, goal in occurrences:
        sites_by_goal.setdefault(goal, set()).add(site)
    proved_sites = {site for _keep, members, transaction in proved if not transaction
                    for site, _goal in members}
    control_goals, blocked_marks = set(), set()
    for ordinal, (keep, members, transaction_query) in enumerate(queries):
        def refine():
            key = keep, tuple(members)
            if key not in refinements:
                return False
            children = refinements[key]
            if children is None:
                try:
                    groups = singletons(members)
                except _ProbeDeadlineExceeded:
                    return None
                children = [(cut, sites, False) for cut, sites in groups.items()
                            if (cut, tuple(sites)) != key]
            del refinements[key]
            if not children:
                return False
            queries[ordinal + 1:ordinal + 1] = children
            return True

        # The private caller binds this set to one complete audited local
        # question (state, targets, legal inputs and observation history).
        # A prior UNKNOWN can contain completed physical UNSAT queries, but no
        # unfinished occurrence, missing hit term or SAT answer is reusable.
        question = keep, tuple(members), transaction_query
        if question in proved:
            continue
        if not transaction_query and proved_sites:
            impossible = {goal for goal, sites in sites_by_goal.items()
                          if sites <= proved_sites}
            if impossible != control_goals:
                blocked_marks = _preparation.blocked_marks(impossible)
                control_goals = impossible
            if all(id(events[site][0]) in blocked_marks for site, _goal in members):
                proved.add(question)
                proved_sites.update(site for site, _goal in members)
                if _constructions is not None:
                    _constructions.pop(question, None)
                continue
        label = ", ".join(f"{goal!r} occurrence {site}" for site, goal in members)
        group_goals = tuple(dict.fromkeys(goal for _site, goal in members))
        remaining = timeout_ms - elapsed()
        if remaining <= 0:
            construction_pending = _constructions is not None
            unknown.append("the logical probe exhausted its wall-clock budget")
            break
        construction = None
        is_prefix = False
        try:
            if needs_plan:
                # All cones in this call use the same admitted procedure and
                # input/state carrier environment. Derive their common plan
                # once; a private worker owner may retain that complete plan
                # across queries. Keep each formula and hit test separate.
                # Read actual encoded scalar literals so structured inputs and
                # their public carrier definitions remain unchanged.
                bound_inputs = {
                    name: pre[name].as_signed_long() for name in proc.inputs
                    if name in pre and z3.is_bv_value(pre[name])
                    and isinstance(step_env.get(name), ranges.Range)
                    and step_env[name].lo <= pre[name].as_signed_long() <= step_env[name].hi
                }
                step_plan = _preparation.plan(validation, fixed_inputs=bound_inputs)
                plan_prevalidated = True
                needs_plan = False
                checkpoint()
            if not transaction_query and step_plan is not None:
                if plan_false_goals is None:
                    plan_false_goals = _preparation.false_goals(step_plan, checkpoint)
                if all(goal in plan_false_goals for goal in group_goals):
                    continue
            encode = _encode_step if keep is None else _encode_query_step
            scope = ({"_derived_plan": False} if keep is None else
                     {"keep": keep, "goals": () if transaction_query else group_goals})
            if _constructions is not None:
                construction = _constructions.get(question)
                if construction is None:
                    construction = _constructions[question] = _LocalQueryConstruction()
                elif construction.failure is not None:
                    refined = refine()
                    if refined is None:
                        raise _ProbeDeadlineExceeded
                    if refined:
                        continue
                    unsupported.append(f"{label}: {construction.failure}")
                    continue
                construction.checkpoint.callback = checkpoint
                scope["_construction"] = construction.encoding
                if keep is not None and not transaction_query:
                    scope["_prefix_goals"] = True
            enc = encode(
                proc, pre=pre, env=step_env, **scope,
                tag=f"{tag}_o{ordinal}", plan=step_plan,
                occurrence_carriers=occurrence_carriers,
                scalar_width=scalar_width,
                _procedure_validation=validation,
                _plan_prevalidated=plan_prevalidated,
                _checkpoint=(checkpoint if _constructions is None else construction_checkpoint),
                _defer_constraint=True)
            if enc is None:
                construction_pending = construction is not None
                unknown.append(f"formula construction paused at {label}")
                break
            if isinstance(enc, _QueryPrefix):
                is_prefix, enc = True, enc.encoding
        except (_ProbeDeadlineExceeded, _EncodingPaused):
            # The shared carrier plan can finish after this slice, before an
            # encoding checkpoint exists. It is still retained work. Genuine
            # earlier solver UNKNOWNs remain UNKNOWN in the final join below.
            construction_pending = _constructions is not None
            unknown.append(
                f"formula construction exhausted the logical probe budget at {label}")
            break
        except (EncodingError, KeyError, TypeError, ValueError) as exc:
            if _constructions is not None:
                _constructions.pop(question, None)
                if construction is not None and isinstance(exc, EncodingError) and exc.kind in {
                        "range", "shape", "type", "capability", "goal"}:
                    # This owner is reset when state, targets, legal input
                    # domain or observation history changes. A completed
                    # representation diagnostic cannot improve by rebuilding
                    # the identical question. Resource/interruption failures
                    # remain retryable; no solver answer or goal proof is made.
                    rejected = _LocalQueryConstruction()
                    rejected.failure = str(exc)
                    _constructions[question] = rejected
            refined = refine()
            if refined is None:
                construction_pending = _constructions is not None
                unknown.append(f"fallback occurrence planning paused at {label}")
                break
            if refined:
                continue
            unsupported.append(f"{label}: {exc}")
            continue
        remaining = timeout_ms - elapsed()
        if remaining <= 0:
            construction_pending = construction is not None
            unknown.append(
                f"formula construction exhausted the logical probe budget at "
                f"{label}")
            break
        try:
            hit_terms = (observation_query.conditions(enc, group_goals) if transaction_query else {})
        except (EncodingError, KeyError, TypeError, ValueError) as exc:
            unsupported.append(f"{label}: {exc}")
            continue
        for goal in group_goals:
            if goal in hit_terms:
                continue
            if enc.supports(goal):
                hit_terms[goal] = enc.goal_condition(goal)
            elif not is_prefix:
                unsupported.append(f"{goal!r} in {label} has no exact hit term")
        if not hit_terms:
            continue

        try:
            selected = None if construction is None else construction.selected
            if selected is None:
                domain = (() if input_constraints is None
                          else tuple(input_constraints(enc.pre)))
                target = z3.And(z3.Or(tuple(hit_terms.values())), *domain)
                # Command-level slicing retains guarded writes and failure state
                # needed to encode an occurrence. Its SSA product can still carry
                # definitions that this query cannot observe. Eliminate only total,
                # acyclic definitions outside the hit AND legal-domain closure;
                # every hard restriction remains. A foreign term falls back to the
                # full relation. SAT still proposes a fresh replay, not a goal hit.
                formula = None if construction is None else construction.formula
                if formula is None:
                    if construction is None:
                        formula = enc._query_formula(checkpoint=checkpoint)
                    else:
                        if construction.registration is None:
                            construction.registration = enc._start_query_formula(
                                checkpoint=construction.checkpoint,
                                prefix=construction.previous_formula)
                        formula = construction.registration.advance(construction.checkpoint)
                        construction.formula = formula
                selected = formula.slice(target)
                if construction is not None:
                    construction.selected = selected
            checkpoint()
            solver = None if construction is None else construction.solver
            if solver is None:
                solver = z3.Solver()
                solver.add(*selected.query_assertions)
                if construction is not None:
                    construction.solver = solver
        except _ProbeDeadlineExceeded:
            construction_pending = construction is not None
            unknown.append(
                f"predicate/domain construction exhausted the logical probe budget at {label}")
            break
        except (EncodingError, KeyError, TypeError, ValueError) as exc:
            unsupported.append(f"{label}: {exc}")
            continue
        remaining = timeout_ms - elapsed()
        if remaining <= 0:
            construction_pending = construction is not None
            unknown.append(f"predicate/domain construction exhausted the logical probe budget at {label}")
            break
        solver.set("timeout", max(1, int(remaining)))
        answer = solver.check()
        if is_prefix and answer != z3.sat:
            # This query says nothing about unencoded occurrences. Resume the
            # same cone, with its unchanged full-step proof obligations. Even
            # UNKNOWN here cannot turn into an early LOCAL_UNSAT answer.
            construction.finish_prefix()
            queries.insert(ordinal + 1, (keep, members, transaction_query))
            continue
        if answer == z3.unsat:
            if _constructions is not None:
                _constructions.pop(question, None)
            if set(hit_terms) == set(group_goals):
                proved.add(question)
                if not transaction_query:
                    proved_sites.update(site for site, _goal in members)
            continue
        if answer == z3.unknown:
            # Keep the immutable encoding and selected assertions, not a
            # cancelled solver. Z3 4.16 can return an invalid model when its
            # interrupted preprocessing state is reused by a later check.
            if construction is not None:
                construction.solver = None
            unknown.append(
                f"{label}: {solver.reason_unknown()}")
            continue

        model = solver.model()
        goal = next((goal for goal, term in hit_terms.items()
                     if z3.is_true(model.eval(term, model_completion=True))), None)
        if goal is None:
            unsupported.append(
                f"{label}: the satisfiable model does not "
                "identify its hit")
            continue
        try:
            physical = {
                name: _model_value(
                    model, enc.pre[name], input_types[name], input_bounds[name])
                for name in proc.inputs
            }
            candidate = _logical_candidate(proc, physical)
        except (KeyError, TypeError, ValueError) as exc:
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
                goal=goal, detail=f"structured candidate decode: {exc}",
                solve_ms=elapsed())
        if _constructions is not None:
            _constructions.pop(question, None)
        return probing.ProbeResult(
            probing.Outcome.SAT, probing.Scope.EXACT_LOCAL_HIT,
            goal=goal, candidate=candidate, solve_ms=elapsed())

    if unknown:
        # An earlier incomplete answer (notably solver UNKNOWN) must not be
        # retried just because a later occurrence paused during construction.
        outcome = (probing.Outcome.CONSTRUCTING if construction_pending and len(unknown) == 1
                   else probing.Outcome.UNKNOWN)
        return probing.ProbeResult(
            outcome, probing.Scope.EXACT_LOCAL_HIT,
            detail=unknown[0], solve_ms=elapsed())
    if unsupported:
        return probing.ProbeResult(
            probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
            detail=unsupported[0], solve_ms=elapsed())
    return probing.ProbeResult(
        probing.Outcome.LOCAL_UNSAT, probing.Scope.EXACT_LOCAL_HIT,
        detail="no input reaches a requested goal from this snapshot",
        solve_ms=elapsed())


def local_hit_any(proc, env, state, goals, timeout_ms=defaults.SIM_SEARCH_QUERY_MS, tag="x", *,
                  position_plan=None, position=0, fixed_inputs=None, input_constraints=None,
                  observation_query=None):
    """One fresh exact local query over caller-owned IR, with no retained state.

    Every occurrence of every requested goal is considered in source order,
    sharing overlapping computation in audited cones. ``timeout_ms`` covers their combined
    planning, formula construction and solver work. SAT requires native replay;
    local UNSAT is not a whole-domain unreachability claim.
    """
    return _local_hit_any(proc, env, state, goals, timeout_ms, tag,
                          position_plan=position_plan, position=position,
                          fixed_inputs=fixed_inputs, input_constraints=input_constraints,
                          observation_query=observation_query)


def local_hit(proc, env, state, goal, timeout_ms=defaults.SIM_SEARCH_QUERY_MS, tag="x", *,
              fixed_inputs=None):
    """Whether some admissible input reaches ``goal`` from ``state``.

    Returns a `ProbeResult` at `Scope.EXACT_LOCAL_HIT`. On SAT the candidate is a
    mapping from input name to value, ready to be replayed -- and it must be
    replayed, since a candidate is evidence only once something has run it.
    """
    return local_hit_any(
        proc, env, state, (goal,), timeout_ms=timeout_ms, tag=tag,
        fixed_inputs=fixed_inputs)
