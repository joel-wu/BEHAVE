"""Local proposals over source activations and event-scheduler transitions.

These primitives serve the public source-query worker, not replay authority.
The batch primitive needs explicitly
bound state and activation flags; the interval primitive derives activations
from physical input changes. Both use compiler-resolved storage coordinates.
The boundary adapter maps recorded Driver phases and observations to these
coordinates; a native hit bitmap alone cannot supply the boundary state.
"""

from collections.abc import Iterable, Mapping

from behave.core import runtime_defaults as defaults

from ...symbolic.ranges import Range
from ...rtl.source_batch import SourceBatch
from ...rtl.source_relation import SourceIntervalTemplate
from ...search_policy import remaining_solver_ms
from .. import local_ir, probing


def query_source_batch(
    batch: SourceBatch,
    env: Mapping[str, Range],
    state: Mapping[str, object],
    inputs: Mapping[str, object],
    goals: Iterable[str],
    *,
    mutable_inputs: Iterable[str],
    timeout_ms: int | None = defaults.SIM_SEARCH_QUERY_MS,
) -> probing.ProbeResult:
    """Ask for a public source goal with one batch boundary fixed.

    ``inputs`` includes every activation flag, even for inactive processes.
    Only selected source input storage may vary; flags and the rest of the
    record stay fixed. All occurrences of a requested public goal share the
    same logical query budget. SAT returns source input storage values (without
    activation flags) for the scheduler to turn into a replay proposal. The
    caller must confirm the goal through native replay before crediting it.
    """
    proc = batch.procedure
    mutable = frozenset(mutable_inputs)
    source_inputs = {name for name, field in batch.storage if field.input}

    def unsupported(detail):
        return probing.ProbeResult(
            probing.Outcome.UNSUPPORTED, probing.Scope.RTL_LOCAL_HIT, detail=detail)

    if set(inputs) != set(proc.inputs):
        return unsupported("a source batch query needs every recorded input and activation flag")
    if not mutable <= source_inputs:
        return unsupported("only source input storage, not state or activation flags, may vary")

    targets = frozenset(goals)
    occurrences = tuple((mark, goal) for mark, _key, goal in batch.occurrences
                        if goal in targets)
    if not targets or targets != {goal for _mark, goal in occurrences}:
        return unsupported("a requested source goal has no occurrence in this batch")
    public_goal = dict(occurrences)
    result = local_ir.local_hit_any(
        proc, env, state, tuple(public_goal), timeout_ms=timeout_ms,
        fixed_inputs={name: value for name, value in inputs.items() if name not in mutable})
    candidate = (None if result.candidate is None else
                 {name: value for name, value in result.candidate.items() if name in source_inputs})
    return probing.ProbeResult(
        result.outcome, probing.Scope.RTL_LOCAL_HIT,
        goal=public_goal.get(result.goal), candidate=candidate,
        detail=result.detail, solve_ms=result.solve_ms)


def query_source_interval(machine, env, state, inputs, goals, *, mutable_inputs,
                          event_bound, following_inputs=(),
                          timeout_ms=defaults.SIM_SEARCH_QUERY_MS, _relation=None):
    """Propose held data for explicit quiescent-boundary drives and their events.

    ``event_bound`` counts micro-transitions including the initial drive, not
    RTL clocks or transaction depth. The query covers all enabled activation
    selections within that explicit local domain and requires a quiet endpoint.
    Each drive has the same explicit event bound; a smaller compiler-proved
    activation bound may omit only quiet padding. Early quiescence is padded by
    unchanged drives with no source events. ``following_inputs`` models actual
    control/clock phases: immutable pins follow those images; mutable data share
    one solver value across all phases, as one Driver presentation does. UNSAT
    applies only to these phases/bounds, never to global unreachability.
    A designated terminal failure ends this interval instead: preserve its
    prefix hits, require no quiescent successor, and emit no events afterwards.

    State and pins use compiler storage coordinates. Translating a native task
    boundary to them, and a proposal back to a task stimulus, remains the public
    adapter's responsibility. A SAT answer still needs native replay confirmation.
    """
    import time

    import z3

    from ...symbolic.exact_solve import z3_check_qfbv
    from ...symbolic.encoder import encode_concrete_value
    from ...symbolic.validation import EncodingError
    from ...core.query import SolverAnswer
    from ...rtl.query_deadline import QueryDeadline, is_solver_cancellation

    if type(event_bound) is not int or event_bound < 1:
        raise ValueError("a source local domain needs a positive event bound")
    timeout_ms = remaining_solver_ms(timeout_ms)
    started = time.monotonic()
    elapsed = lambda: (time.monotonic() - started) * 1000

    def result(outcome, **kwargs):
        return probing.ProbeResult(outcome, probing.Scope.RTL_LOCAL_HIT, solve_ms=elapsed(), **kwargs)

    proc = machine.procedure
    drive = dict(machine.drive_inputs)
    phases = (inputs, *following_inputs)
    mutable, requested = frozenset(mutable_inputs), frozenset(goals)
    if (any(set(pins) != set(drive) for pins in phases) or not mutable <= set(drive)
            or not set(state) <= set(proc.state) or not requested):
        return result(probing.Outcome.UNSUPPORTED, detail="incomplete source state, pins or goal binding")
    if machine.reports is not None and event_bound > machine.reports.transition_bound:
        return result(probing.Outcome.UNSUPPORTED,
            detail="the query exceeds its compiled finite report-frame domain")
    try:
        # The worker selects one closed relation before binding native state;
        # standalone callers prepare the identical relation inside this budget.
        selected = (_relation if _relation is not None else
                    SourceIntervalTemplate(proc, env).relation(machine, requested))
        if (selected.source.procedure is not proc or selected.source.environment != env
                or set(selected.goal_names) != requested):
            return result(probing.Outcome.UNSUPPORTED, detail="source template has another procedure or carrier layout")
        if not set(selected.source.state_names) <= set(state):
            return result(probing.Outcome.UNSUPPORTED, detail="incomplete retained source state")
        if not machine.boundary_is_quiet(state):
            return result(probing.Outcome.UNSUPPORTED, detail="a local input change needs a quiescent source boundary")
        phase_bounds = machine.phase_event_bounds(state, phases, mutable, event_bound)
        # Own one native context for the entire physical query. Moving the
        # single-step template first avoids copying the expanded multi-event
        # circuit afterwards, while still releasing all solver arenas per query.
        context = z3.Context()
        # The same budget covers native construction, solving and decoding.
        # Interrupt only this query's private context, never the prepared one.
        with QueryDeadline(context, timeout_ms, started=started) as deadline:
            deadline.remaining_ms()
            # The closed cone retains legality/termination and every transitive
            # predecessor, without instantiating unobserved total successors.
            relation = selected.translate(context)
            state_types = dict(proc.semantics.state_types)
            pre = {name: encode_concrete_value(state[name], env[name], name, kind=state_types[name],
                                                context=context)
                   for name in relation.source.state_names}
            input_types = dict(proc.semantics.input_types)
            input_bounds = dict(proc.semantics.solver_input_bounds())
            alive = z3.BoolVal(True, ctx=context)
            pin_images = tuple({name: None if name in mutable else
                encode_concrete_value(pins[name], env[driven], driven, kind=input_types[driven],
                                      context=context)
                for name, driven in drive.items()} for pins in phases)
            enc = relation.encode_phases(pre, pin_images, event_bounds=phase_bounds,
                                         held_inputs=mutable, alive=alive, check=deadline.remaining_ms)
            domain_faults = enc.domain_faults
            if domain_faults:
                domain = z3_check_qfbv((*enc.constraints, z3.Or(*domain_faults.values())),
                    deadline.remaining_ms(), _owned_context=context)
                if domain.answer is SolverAnswer.UNKNOWN:
                    return result(probing.Outcome.UNKNOWN,
                        detail=f"source operation-domain proof: {domain.reason}")
                if domain.answer is SolverAnswer.SAT:
                    causes = dict(machine.domain_checks)
                    mark = next(mark for mark, condition in domain_faults.items()
                                if z3.is_true(domain.model.eval(condition)))
                    cause = causes[mark]
                    return result(probing.Outcome.UNSUPPORTED,
                        detail=f"{cause.kind}: {cause.why}; invalid operation in this local input domain")
                # This definedness proof is local, not a public goal answer.
            targets, first = enc.hits, enc.phases[0].inputs
            constraints = (*enc.constraints, z3.Or(*targets.values()))
            # Keep the exact typed formula and every requested occurrence.
            answer = z3_check_qfbv(constraints, deadline.remaining_ms(), _owned_context=context)
            if answer.answer is SolverAnswer.UNSAT:
                return result(probing.Outcome.LOCAL_UNSAT,
                              detail=f"fixed source boundary; {len(phases)} drives, "
                                     f"at most {event_bound} event transitions per drive")
            if answer.answer is SolverAnswer.UNKNOWN:
                return result(probing.Outcome.UNKNOWN, detail=answer.reason)
            model = answer.model
            candidate = {name: local_ir._model_value(model, first[name], input_types[driven],
                                                   input_bounds[driven]) for name, driven in drive.items()}
            goal = next(goal for goal, term in targets.items() if z3.is_true(model.eval(term)))
            return result(probing.Outcome.SAT, goal=goal, candidate=candidate)
    except TimeoutError as exc:
        return result(probing.Outcome.UNKNOWN, detail=str(exc))
    except z3.Z3Exception as exc:
        if not is_solver_cancellation(exc):
            raise
        return result(probing.Outcome.UNKNOWN, detail="native solver canceled this local query")
    except (EncodingError, KeyError, TypeError, ValueError) as exc:
        return result(probing.Outcome.UNSUPPORTED, detail=str(exc))
