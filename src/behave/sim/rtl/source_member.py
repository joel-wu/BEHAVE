"""Reuse source construction order without granting its metadata authority.

Greedy private choices supply an existential member only after full root
validation. Universal literal propagation instead reads actual asserted
equations and never completes unknowns. Neither route constrains the original
domain; unsuccessful attempts retain the owner's complete solver fallback.
"""

from dataclasses import dataclass

import z3

from .monitor_template import _DefinitionDependencies, complete_monitor_model, monitor_frames
from .source_relation import SourceIntervalEncoding
from .symbolic_state import _literal


@dataclass(frozen=True, slots=True)
class SourceMemberPlan:
    initial: tuple
    steps: tuple


def propose_native_phase_order(machine, phases, index, model, expected, prior_hits, *, check):
    """Propose a concrete legal order, not a classification or a new semantics.

    Reuse the source scheduler's ordinary typed interpreter. Only a settled,
    fault-free proposal matching the recorded state and goal image is returned;
    the caller still validates every ORIGINAL whole-history symbolic root and
    observation. Failure supplies no fact about other orders or unseen inputs.
    """
    from graphlib import CycleError, TopologicalSorter
    from ..symbolic import interp
    from ..symbolic.encoder import decode_value
    from .source_direct import NativeMemberSourceCompiler

    check()
    if not 0 < index < len(phases):
        return None  # Startup needs its complete HDL-initialization product.
    procedure, before = machine.procedure, phases[index - 1].state
    if not set(procedure.state) <= set(before):
        return None  # Never invent an omitted projected source component.
    initial = {name: decode_value(model, before[name], procedure.types[name], compact=True)
               for name in procedure.state}
    pins = {driven: decode_value(model, phases[index].inputs[name], procedure.types[driven], compact=True)
            for name, driven in machine.drive_inputs}
    final = {name: decode_value(model, phases[index].state[name], procedure.types[name], compact=True)
             for name in machine.program.fields}
    compiler = NativeMemberSourceCompiler(machine)
    graph = {i: set() for i in range(len(machine.pending))}
    for parent, children in enumerate(compiler.blocking):
        for child in children:
            graph[child].add(parent)
    try:
        topological = tuple(TopologicalSorter(graph).static_order())
    except CycleError:
        topological = tuple(graph)
    check()
    execute = interp._prepare_procedure(procedure).run_step
    finish_rank = {i + 1: p for p, i in enumerate(topological)}
    priorities = ((tuple(reversed(graph)), True), (tuple(reversed(topological)), True),
                  (tuple(graph), False), (tuple(reversed(graph)), False),
                  (tuple(reversed(topological)), False))
    for order, finish_after_hit in dict.fromkeys(priorities):
        check()
        rank = {i + 1: p for p, i in enumerate(order)}
        state, outputs, marks, error = execute({**pins, 'action': 0}, initial)
        hits = set(prior_hits)
        hits.update(goal for mark, _, goal in machine.occurrences if mark in marks)
        actions = []
        accepted = bool(outputs.transactions and outputs.transactions[0]['accepted'])
        while accepted and not error and hits <= expected and not machine.boundary_is_quiet(state):
            check()
            if len(actions) + 1 >= machine.event_bound:
                break  # Only the original finite event bound, no new cap.
            evaluator = interp._Interp({**state, **pins})
            enabled = [i for i, (guard, _) in enumerate(machine.actions)
                       if i and evaluator.boolean(guard)]
            if not enabled:
                break
            selected = finish_rank if finish_after_hit and expected <= hits else rank
            action = min(enabled, key=lambda i: selected.get(i, len(selected) + i))
            state, outputs, marks, error = execute({**pins, 'action': action}, state)
            accepted = bool(outputs.transactions and outputs.transactions[0]['accepted'])
            actions.append(action)
            hits.update(goal for mark, _, goal in machine.occurrences if mark in marks)
        check()
        if (accepted and not error and machine.boundary_is_quiet(state) and hits == expected
                and all(state[name] == value for name, value in final.items())):
            return tuple(actions)
    return None


def source_query_prefixes(history, environment, *, check, cut_constraints=None):
    """Cheap existential proposals from one history, never shorter proofs.

    Reuse already constructed frames at geometrically growing event-interval
    boundaries, joining protocol/monitor events after each complete slot.
    Terminal whole-request goals are not prefix events.
    Metadata can only propose a model: the caller must complete
    and validate EVERY original root, including the full-horizon target and
    escape checks. Prefix UNSAT/UNKNOWN supplies no whole-history fact.
    """
    from .source_relation import _union_images

    plan = getattr(history, 'member_plan', None)
    if type(plan) is not SourceMemberPlan:
        return
    slots = getattr(getattr(history, 'source', None), 'slots', ())
    slot_ends = {id(slot.phases[-1]): index for index, slot in enumerate(slots)}
    monitor_roots = iter(getattr(history, 'monitor_constraints', ()))
    monitor_sizes = getattr(history, 'monitor_frame_sizes', ())
    observations = (getattr(getattr(history, 'protocol', None), 'hits', ()),
                    getattr(history, 'monitor_hits', ()),
                    getattr(history, 'transaction_hits', ()))
    monitor_faults = getattr(history, 'monitor_faults', ())
    if any(rows and len(rows) != len(slots)
           for rows in (*observations, monitor_faults, monitor_sizes)):
        # Aggregate/partial images still belong to the full query, but do not
        # supply a per-slot proposal. Never guess their physical alignment.
        slot_ends = {}
    roots = [*environment, *(a == b for a, b in plan.initial)]
    images, faults = [], []
    count, boundary = 0, 1
    total = sum(type(step) is SourceIntervalEncoding for step in plan.steps)
    for step in plan.steps:
        check()
        if type(step) is not SourceIntervalEncoding:
            roots.extend(a == b for a, b in step)
            continue
        roots.extend(step.constraints)
        roots.extend(term for frame in step.step_definitions for term in frame)
        if cut_constraints is not None:
            roots.extend(cut_constraints.get(id(step), ()))
        images.append(step.hits)
        faults.append(step.domain_faults)
        if id(step) in slot_ends:
            index = slot_ends[id(step)]
            # Monitor samples and post-slot reset observations must not be
            # proposed before their physical slot has completed. Reuse the
            # original frame equations, not a second monitor implementation.
            if monitor_sizes:
                roots.extend(next(monitor_roots) for _ in range(monitor_sizes[index]))
            images.extend(rows[index] for rows in observations if rows)
            if monitor_faults:
                faults.append(monitor_faults[index])
        count += 1
        if count == boundary or count == total:
            # Faults are separate from public goals. A prefix can propose an
            # unsupported execution as well as a hit, but neither is evidence
            # until it satisfies the complete original history below.
            yield tuple(roots), _union_images(images), _union_images(faults)
            boundary *= 2


def reduce_fixed_source_defect(assertions, history, target, *, check):
    """Use construction order to derive consequences, never to choose a run.

    Only actual asserted equations supply values. In particular, private
    scheduler choices, table defaults and observed native values are not facts.
    An unresolved result leaves the caller's complete proof path unchanged.
    """
    facts = source_literal_consequences(assertions, history, context=target.ctx,
        check=check)
    return target if facts is None else facts.evaluate(target)


def source_literal_consequences(assertions, history, *, context, check, targets=None):
    """Share one exact constant propagation across this goal batch's roots.

    Only asserted equations and proved guards supply literals. Source/member
    metadata supplies traversal order, never values or feasible executions.
    Unknown inputs, private choices and nonconstant expressions stay unknown.
    Index the asserted boundary equations once; coordinates and targets use
    the same demanded-dependency worklist. Unused array reads do not trigger
    an eager whole-history warmup before those roots are requested.
    Extra derived literals are still consequences of the original roots;
    neither the solver constraints nor its targets are removed.
    """
    from .fixed_target import LiteralConsequences

    check()
    plan = getattr(history, 'member_plan', None)
    if type(plan) is not SourceMemberPlan:
        return None
    facts = LiteralConsequences(assertions, context=context, check=check)
    if targets is not None:
        facts.derive_targets(targets)
        return facts
    facts.derive(lhs for lhs, _ in plan.initial)
    def coordinates():
        for step in plan.steps:
            if type(step) is SourceIntervalEncoding:
                for equations in step.step_definitions:
                    yield from (eq.arg(0) for eq in equations)
            else:
                yield from (lhs for lhs, _ in step)

    # Share the dependency DAG across the ordered history, publishing proved
    # literals before visiting their successors. Unknown subgraphs are indexed
    # once; newly established controls can avoid inactive datapaths entirely.
    facts.derive(coordinates())
    # Protocol validity is an asserted predicate, sometimes a compound
    # comparison rather than a named bit. Source guards are now available;
    # propagate these facts before the monitor consumes accepted transfers.
    facts.derive_predicates()
    facts.derive(eq.arg(0) for eq in getattr(history, 'monitor_constraints', ()))
    return facts


def solve_fixed_source_member(assertions, histories, environment, solve, *, phase, check,
                              proposal=None, prefixes=()):
    """Try a fixed request before solving its whole source history from scratch.

    Only a candidate satisfying every original root supplies a SAT member.
    A prior query model is only a proposal, including across projections.
    Missing plans, unsuccessful hints and non-SAT environment answers retain
    the original monitor/source solver path under the same owner's budget.
    This establishes neither native correspondence nor any universal property.
    """
    from ..symbolic.exact_solve import ExactCheckResult
    from ..core.query import SolverAnswer
    from .monitor_template import solve_monitor_member

    check()
    histories = tuple(histories)
    if histories and all(type(getattr(history, 'member_plan', None)) is SourceMemberPlan
                         for history in histories):
        if proposal is not None and all(term.ctx == proposal.ctx for term in assertions):
            model = source_member_hint(proposal, histories, assertions, check=check)
            if model is not None:
                return ExactCheckResult(SolverAnswer.SAT, model=model)
        candidate = solve(phase + '-environment', environment)
        check()
        if candidate.answer is SolverAnswer.SAT and candidate.model is not None:
            model = source_member_hint(candidate.model, histories, assertions, check=check)
            if model is not None:
                return ExactCheckResult(SolverAnswer.SAT, model=model,
                    solver_load_ns=candidate.solver_load_ns, solve_ns=candidate.solve_ns)
        # Recorded-prefix constraints can choose a compatible private event
        # order where the greedy constructor chose another legal order. These
        # remain proposals: even prefix UNSAT/UNKNOWN gives no full verdict.
        for roots in prefixes:
            check()
            candidate = solve(phase + '-prefix', roots)
            check()
            if candidate.answer is not SolverAnswer.SAT or candidate.model is None:
                break
            model = source_member_hint(candidate.model, histories, assertions, check=check)
            if model is not None:
                return ExactCheckResult(SolverAnswer.SAT, model=model)
    check()
    return solve_monitor_member(assertions, histories, solve, phase=phase, check=check)


def source_member_hint(model, histories, assertions, *, check):
    """Return only a model satisfying EVERY original root, or no hint.

    Private initial bits and legal action choices are existential here. Their
    values never become facts about other executions. Frame completion and the
    final check use the original formula, not the greedy plan's projections.
    """
    check()
    histories = tuple(histories)
    plans = tuple(getattr(history, 'member_plan', None) for history in histories)
    if not plans or any(type(plan) is not SourceMemberPlan for plan in plans):
        return None
    # All fresh coordinates are known before traversing any RHS. Share the
    # syntactic support walk across uncached intervals of this request; only
    # immutable frame layouts survive, never the index or candidate model.
    definitions = []
    for plan in plans:
        for step in plan.steps:
            check()
            if type(step) is SourceIntervalEncoding and step._member_frames_cache is None:
                if len(step.constraints) != len(step.actions) or (
                        step.step_definitions and len(step.step_definitions) != len(step.actions)):
                    return None
                if not step.member_layers:
                    definitions.extend(term for row in step.step_definitions for term in row)
    dependencies = _DefinitionDependencies(definitions, check=check)
    candidate = model.translate(model.ctx)

    def assign(pairs):
        for atom, expression in pairs:
            check()
            if _literal(atom):
                if not z3.is_true(candidate.eval(atom == expression, model_completion=True)):
                    return False
                continue
            if not z3.is_const(atom) or atom.decl().kind() != z3.Z3_OP_UNINTERPRETED:
                return False
            value = candidate.eval(expression, model_completion=True)
            if not (z3.is_array(value) or z3.is_bv_value(value) or z3.is_int_value(value)
                    or z3.is_true(value) or z3.is_false(value)):
                return False
            candidate.update_value(atom.decl(), value)
        return True

    for plan in plans:
        if not assign(plan.initial):
            return None
        for step in plan.steps:
            check()
            if type(step) is not SourceIntervalEncoding:
                if not assign(step):
                    return None
                continue
            if len(step.constraints) != len(step.actions):
                return None
            definitions = step.step_definitions or ((),) * len(step.actions)
            if len(definitions) != len(step.actions):
                return None
            frames = step._member_frames(check=check, dependencies=dependencies).frames
            if frames is None:
                return None
            frames = iter(frames)
            for action, predicate, equations in zip(step.actions, step.constraints, definitions, strict=True):
                check()
                frame = next(frames) if equations else None

                def complete_frame():
                    # Recompute owned successor definitions for EACH action.
                    # Pack one simultaneous successor instead of rewalking its
                    # shared circuit after each scalar model update. This is the
                    # same frame completion used for monitor states; every
                    # original equality remains in the final checked formula.
                    if frame is not None:
                        for coordinates, packed in frame:
                            check()
                            values = candidate.eval(packed, model_completion=True).children()
                            for coordinate, value in zip(coordinates, values, strict=True):
                                check()
                                if not _literal(value):
                                    return False
                                candidate.update_value(coordinate, value)
                    return True

                if z3.is_bv_value(action):
                    # This order is fixed, so there is no alternative action
                    # to choose here. Validate its predicate with ALL original
                    # roots at the end, once over their shared DAG. Rechecking
                    # each growing prefix here can make history completion
                    # quadratic; it grants no additional witness authority.
                    if not complete_frame():
                        return None
                    continue
                if not z3.is_const(action) or not z3.is_bv(action):
                    return None
                # The encoded predicate retains acceptance, liveness, failure
                # and unused selector codes. An invalid action cannot pass it.
                # A prefix solver may already have chosen a legal order that
                # hits its target. Preserve that proposal when possible; this
                # is not a premise about any other execution. Unassigned or
                # invalid selectors retain the ordinary legal-order search.
                preferred = candidate.eval(action, model_completion=False)
                first = preferred.as_long() if z3.is_bv_value(preferred) else None
                codes = range(1 << action.size())
                if first is not None:
                    from itertools import chain
                    codes = chain((first,), (code for code in codes if code != first))
                for code in codes:
                    check()
                    candidate.update_value(action.decl(), z3.BitVecVal(code, action.size(), ctx=action.ctx))
                    if complete_frame() and z3.is_true(candidate.eval(predicate, model_completion=True)):
                        break
                else:
                    return None
    frames = tuple(monitor_frames(history, check=check) for history in histories)
    return complete_monitor_model(candidate, (), assertions, check=check, _frames=frames)
