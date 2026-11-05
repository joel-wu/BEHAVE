"""Observation planning and prepared goal models for bounded RTL analysis.

The source compiler owns transition semantics. This module owns observation
selection, request-bound local exclusions and prepared goal formulas. It neither
admits native evidence nor publishes classifications. The caller owns batch
ordering, deadlines and witness confirmation; local exclusions still require a
complete bounded escape check before they can classify a goal.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..symbolic.diagnostics import diagnostic_stage
from ..core.refusal import Refusal, Refused

if TYPE_CHECKING:
    from ..symbolic.exact_solve import SolverPreparation


def goal_union(terms, *, context):
    """Fold literal roots without revisiting shared arithmetic DAGs."""
    import z3

    live = []
    for term in terms:
        if z3.is_true(term):
            return term
        if not z3.is_false(term):
            live.append(term)
    return (z3.BoolVal(False, ctx=context) if not live else
            live[0] if len(live) == 1 else z3.Or(*live))


def source_domain_refusal(model, domain_faults, startup, operations):
    """Describe a full-query escape; it is not an artifact failure or a hit."""
    import z3

    faults = sorted(name for name, term in domain_faults.items()
                    if z3.is_true(model.eval(term, model_completion=True)))
    startup_faults = ([] if startup is None else sorted(
        name for name, term in startup.domain_faults.items()
        if name in faults and z3.is_true(model.eval(term, model_completion=True))))
    return source_domain_failure(faults, startup_faults=startup_faults, operations=operations)


def source_domain_failure(faults, *, startup_faults=(), operations=()):
    """Name unsupported semantics, whether exposed by a model or native replay."""
    faults = sorted(faults)
    if not faults:
        return None
    reset_only = all(name.startswith('monitor.reset-policy-unresolved[') for name in faults)
    return Refusal(
        'rtl-monitor-reset-policy-undeclared' if reset_only else
        'rtl-source-operation-domain-unencoded',
        ('reset can occur with outstanding requests, but the task contract '
         'does not declare whether reset clears or keeps them; declare the '
         'existing task semantics explicitly, rather than assuming a policy'
         if reset_only else
         'the bounded source cover reaches an unencoded operation domain; '
         'this is an Analysis refusal, not an artifact failure'),
        faults=faults, startup_faults=list(startup_faults),
        operations={name: cause.as_dict() for name, cause in operations if name in faults})


def literal_false_goals(source, goals):
    """Source event goals whose every occurrence is literal false in one step.

    The unrestricted one-step template decides this syntactically. No history
    can hit such a goal, so a witness proposal cannot find one; the ordinary
    query still closes the goal together with its domain escapes.
    """
    events = set(source.goal_names).intersection(goals)
    marks = {}
    for mark, _, goal in source.machine.occurrences:
        if goal in events:
            marks.setdefault(goal, set()).add(mark)
    selected = frozenset(mark for group in marks.values() for mark in group)
    if not selected:
        return frozenset()
    inactive = source.template.observations(selected).literal_false_hits(selected)
    return frozenset(goal for goal, group in marks.items() if group <= inactive)


def goal_batches(source, request, goals):
    """Partition observations, without omitting goals or granting verdicts.

    Returns the batches, the protocol batch and the literal-false goals. No
    history hits a literal-false goal, so a witness proposal never searches
    for one; its batch query still closes it together with its escapes.
    """
    if source.template.direct is None:
        return (tuple(goals),), (), frozenset()
    from ..core.reset_monitor import ResetRestorationProvider, reset_state_domains
    from .source_stability import GOAL, COUNT_GOAL

    protocol = {GOAL, COUNT_GOAL}.intersection(goals)
    events = set(source.goal_names).intersection(goals)
    # Only an unrestricted literal-false predicate removes the observation
    # demand. Its goal remains in the control batch with all domain escapes.
    inactive = literal_false_goals(source, events)
    events.difference_update(inactive)
    active_resets = reset_state_domains(source.contract,
        request.slots if request.atomic else None, global_events=request.atomic)
    reset_values = {ResetRestorationProvider.violation_goal(clock.name)
                    for clock in source.contract.clocks if clock.name in active_resets}
    groups = (tuple(goal for goal in goals if goal not in events | reset_values | protocol),
              tuple(goal for goal in goals if goal in events),
              tuple(goal for goal in goals if goal in reset_values))
    return (tuple(group for group in groups if group),
            tuple(goal for goal in goals if goal in protocol), inactive)


@dataclass(frozen=True, slots=True)
class GoalReduction:
    """Local facts for one batch, not a bounded reachability verdict."""

    owner: object
    goals: tuple
    proved: frozenset
    domains: frozenset
    source: object

    @property
    def residual(self):
        return tuple(goal for goal in self.goals if goal not in self.proved)


@dataclass(slots=True)
class LocalProofCost:
    """Batch-local measurements; no retained solver, model or continuation."""

    elapsed_ns: int = 0
    calls: list[dict[str, int | None]] = field(default_factory=list)

    def run(self, operation, argument, *, solve, check):
        def measured_solve(phase, assertions, **options):
            check()
            answer = solve(phase, assertions, **options)
            self.calls.append({key: getattr(answer, key)
                               for key in ('solver_load_ns', 'solve_ns')})
            return answer

        started = time.monotonic_ns()
        value = operation(argument, solve=measured_solve, check=check)
        self.elapsed_ns += time.monotonic_ns() - started
        return value


class LocalGoalProofs:
    """Optional exclusions shared only within one source/request invocation.

    Structural domain facts are shared across observation batches. Stronger
    domain proofs are requested separately, only for an escape-only batch.
    Neither a rejected lemma nor a failed projected-view construction removes
    an original fault. No deadline, query solver or native evidence lives here.
    """

    def __init__(self, source, request):
        self.source, self.request = source, request
        self._transition = self._closure = None

    def require_binding(self, source, request):
        if self.source is not source or self.request is not request:
            raise ValueError('local goal proofs belong to another source/request')

    def prepare(self, goals, *, solve, check):
        import z3
        from ..core.monitor import INPUT_COMPLETION
        from .protocol_relation import SymbolicRequest

        check()
        goals, proved = tuple(goals), frozenset()
        if self.source.template.direct is None:
            return GoalReduction(self, goals, proved, frozenset(), self.source)
        if INPUT_COMPLETION in goals and isinstance(self.request, SymbolicRequest):
            # The terminal hit is completed AND pending AND drain. A calendar
            # with a literal-false drain cannot hit it, regardless of source
            # state or acceptance. This removes only the event demand: every
            # source fault, unfinished history and environment obligation is
            # retained by close_escape and the final bounded query.
            drain = goal_union(self.request.drain_slots, context=self.request.context)
            if z3.is_false(z3.simplify(drain)):
                proved |= frozenset((INPUT_COMPLETION,))
            check()
        from .source_domains import prove_reset_goals, prove_transition_domains, prove_transition_goals
        from .source_stability import prove_reset_restoration, prove_protocol_goals

        if any(goal.startswith('monitor.reset-restoration.') and goal.endswith('.violation')
               for goal in goals):
            try:
                with diagnostic_stage('reset.local', goals=list(goals)):
                    proved |= frozenset(prove_reset_restoration(
                        self.source, self.request, goals, solve=solve, check=check))
            except Refused:
                pass
            check()
        if set(goals).intersection(('monitor.sink-stability', 'monitor.output-without-transaction')):
            try:
                with diagnostic_stage('protocol.local', goals=list(goals)):
                    proved |= frozenset(prove_protocol_goals(
                        self.source, self.request, goals, solve=solve, check=check))
            except Refused:
                pass
            check()
        if self._transition is None:
            self._transition = frozenset(), self.source
            try:
                with diagnostic_stage('transition.domains') as detail:
                    excluded = frozenset(prove_transition_domains(self.source, check=check))
                    view = (self.source._domain_query_view(excluded, check=check)
                            if excluded else self.source)
                    check()
                    self._transition = excluded, view
                    detail['proved'] = sorted(excluded)
            except Refused:
                pass
        check()
        domains, source = self._closure if self._closure is not None else self._transition
        events = set(source.goal_names)
        residual = tuple(goal for goal in goals if goal in events and goal not in proved)
        if residual:
            try:
                with diagnostic_stage('source.control', goals=list(residual)):
                    proved |= frozenset(prove_reset_goals(
                        source, self.request, residual, solve=solve, check=check))
            except Refused:
                pass
            check()
        try:
            with diagnostic_stage('transition.goals', goals=list(goals)):
                proved |= frozenset(prove_transition_goals(source,
                    tuple(goal for goal in goals if goal not in proved), solve=solve, check=check))
        except Refused:
            pass
        check()  # A budget refusal cannot masquerade as an unavailable lemma.
        return GoalReduction(self, goals, proved, domains, source)

    def close_escape(self, reduction, *, solve, check):
        """Refine private faults only after the batch has no live goal left."""
        check()
        if reduction.owner is not self:
            raise ValueError('goal reduction belongs to another proof invocation')
        if reduction.residual:
            raise ValueError('escape-only proof cannot precede this batch\'s live goals')
        if self.source.template.direct is None:
            return reduction
        from .source_domains import prove_reset_domains, prove_transition_domains

        if self._closure is None:
            excluded, view = self._transition
            self._closure = excluded, view
            for phase, prove in (
                    ('closure.transition', lambda source: prove_transition_domains(
                        source, solve=solve, check=check)),
                    ('closure.reset', lambda source: prove_reset_domains(
                        source, self.request, solve=solve, check=check))):
                if not view.machine.domain_checks:
                    break
                try:
                    with diagnostic_stage(phase) as detail:
                        fresh = frozenset(prove(view))
                        updated = view._domain_query_view(fresh, check=check) if fresh else view
                        check()
                        excluded, view = excluded | fresh, updated
                        self._closure = excluded, view
                        detail['proved'] = sorted(fresh)
                except Refused:
                    pass  # Preserve earlier facts and every still-unproved fault.
                check()
        domains, source = self._closure
        return replace(reduction, domains=domains, source=source)


@dataclass(frozen=True, slots=True)
class PreparedGoalQuery:
    """One source/domain/observation view; no native record or SAT model."""

    source: object
    horizon: int
    goals: frozenset
    proved_domains: frozenset
    request: object
    encoding: object
    constraints: tuple
    observations: Mapping
    domain_faults: Mapping
    hits: Mapping
    escape: object
    facts: object
    preparation: SolverPreparation

    def matches(self, source, horizon, goals, proved_domains):
        return (self.source is source and self.horizon == horizon
            and self.goals == frozenset(goals)
            and self.proved_domains == frozenset(proved_domains))


class GoalQueryPrefixes:
    """Batch-local SAT proposals, separate from the complete query's solver.

    Reuse prefix syntax and preparation after a validated member. A failed
    proposal retires both; neither a prefix answer nor its extra constraints
    supplies a full-domain verdict. The coordinator owns the common deadline.
    """

    def __init__(self, model, goals, proved, *, source_goals, check):
        import z3
        from .source_member import source_query_prefixes

        self.model, self.proved = model, proved
        self._prefixes, self._preparation = None, None
        self._cache = []
        encoding, request, goals = model.encoding, model.request, set(goals)
        if not (goals.intersection(source_goals)
                or any(goals.intersection(row) for rows in (
                    encoding.protocol.hits, encoding.monitor_hits,
                    encoding.transaction_hits) for row in rows)
                or any(not z3.is_false(z3.simplify(term))
                       for term in model.domain_faults.values())):
            return
        environment = request.constraints
        if request.atomic or request.settled:
            # A fixed calendar can require later inputs. Fill the proposal's
            # queue only; the complete query remains unrestricted.
            environment = (*environment, *request.full_queue_hint())
        else:
            # A receptive tail permits completion after a short handshake
            # prefix. It is a witness hint, not a whole-domain assumption.
            drain = z3.Or(z3.BoolVal(False, ctx=encoding.alive.ctx), *request.drain_slots)
            environment = (*environment, drain)
        self._prefixes = source_query_prefixes(encoding, environment, check=check)

    def close(self):
        self._prefixes = None
        self._cache.clear()
        preparation, self._preparation = self._preparation, None
        if preparation is not None:
            preparation.clear()

    def _candidates(self):
        yield from self._cache
        for prefix in self._prefixes:
            self._cache.append(prefix)
            yield prefix

    def propose(self, goals, target, *, solve, check):
        """Return a full-root-validated member and measured proposal calls."""
        import z3
        from ..core.query import SolverAnswer
        from ..symbolic.exact_solve import SolverPreparation
        from .source_member import source_member_hint

        member, answers = None, []
        if self._prefixes is None:
            return member, answers
        model = self.model
        context = model.encoding.alive.ctx
        try:
            for roots, images, faults in self._candidates():
                check()
                early = goal_union((images[goal] for goal in goals if goal in images), context=context)
                if model.facts is not None:
                    early = model.facts.constant_target(early)
                if z3.is_false(early) and any(goal not in self.proved for goal in goals):
                    continue  # Prefer goal hits while a goal remains live.
                early = goal_union((early, *(term for name, term in faults.items()
                    if name not in model.proved_domains)), context=context)
                if model.facts is not None:
                    early = model.facts.constant_target(early)
                if z3.is_false(early):
                    continue
                if roots:
                    if self._preparation is None:
                        self._preparation = SolverPreparation(roots)
                    else:
                        self._preparation.extend(roots)
                candidate = solve('query-prefix', (*roots, early),
                                  preparation=self._preparation if roots else None)
                answers.append(candidate)
                if candidate.answer is SolverAnswer.SAT:
                    member = source_member_hint(candidate.model, (model.encoding,),
                        (*model.constraints, target), check=check)
                # Preserve the current policy: one nonconstant proposal, then
                # the complete query. A prefix UNSAT is not unreachability.
                break
            return member, answers
        finally:
            if member is None:
                self.close()


@dataclass(frozen=True, slots=True)
class NativeQueryProposal:
    """Private execution request, deliberately not a SAT/UNSAT verdict."""

    rows: list

    def as_record(self):
        return {'native_proposal': self.rows}


class GrowingGoalQuery:
    """One unrestricted construction, with separately validated SAT proposals.

    Geometric slot cuts share original coordinates and asserted roots. They
    never prove unreachability. Streaming consumers confirm complete native
    evidence; standalone consumers validate a full symbolic member. Failed
    proposals leave the same unrestricted iterator for the full query.
    """

    def __init__(self, source, horizon, goals, excluded):
        import z3

        self.source, self.horizon = source, horizon
        self.goals, self.excluded = frozenset(goals), frozenset(excluded)
        self.request = source.request_coordinates(horizon)
        self.steps = source.bind_request_steps(horizon, goals=self.goals)
        self.bound = None
        request = self.request
        hint = (request.full_queue_hint() if request.atomic or request.settled else
                (z3.Or(*request.drain_slots),))
        self.roots = [*request.constraints, *request.bind_initial(source.initial_binding), *hint]
        self.images = {}
        self.preparation = None
        self.next_boundary, self.at_boundary = 1, False
        self.enabled = True
        self.construction_ns = 0

    def matches(self, source, horizon, goals, excluded):
        return (self.source is source and self.horizon == horizon
                and self.goals == frozenset(goals) and self.excluded == frozenset(excluded))

    def close(self):
        steps, self.steps = self.steps, None
        try:
            if steps is not None:
                steps.close()
        finally:
            if self.preparation is not None:
                self.preparation.clear()
                self.preparation = None

    def _advance(self, check):
        import z3
        from .source_task import SourceTaskFrame

        # The completed-history span only finishes this iterator. Measure its
        # earlier growth too, without walking formulas or changing its cuts.
        with diagnostic_stage('history.advance', horizon=self.horizon,
                              to_slot=min(self.next_boundary, self.horizon)) as detail:
            started = time.monotonic_ns()
            frames = 0
            try:
                while self.steps is not None:
                    check()
                    try:
                        frame = next(self.steps)
                    except StopIteration as done:
                        self.bound, self.steps = done.value, None
                        return False
                    if not isinstance(frame, SourceTaskFrame):
                        continue
                    self.roots.extend(frame.constraints)
                    self.roots.extend(z3.Not(term) for term in frame.environment_assumptions.values())
                    for goal, term in frame.hits.items():
                        self.images.setdefault(goal, []).append(term)
                    frames += 1
                    count = frame.index + 1
                    if count == self.next_boundary or count == self.horizon:
                        self.next_boundary *= 2
                        self.at_boundary = True
                        return True
                return False
            finally:
                self.construction_ns += time.monotonic_ns() - started
                detail.update(frames=frames, roots=len(self.roots), complete=self.steps is None)

    def finish(self, check):
        while self._advance(check):
            pass
        return self.bound

    def propose_request(self, goals, *, solve, check):
        """Propose complete input coordinates; only a full audit can grant hits.

        A caller must either confirm this request or retire the proposal path.
        No source member, negative answer, or altered unrestricted domain is
        published here. Standalone symbolic consumers validate it below.
        """
        import z3
        from ..core.query import SolverAnswer
        from ..symbolic.exact_solve import SolverPreparation

        calls = []
        if not self.enabled:
            return None, calls
        while self.at_boundary or self._advance(check):
            self.at_boundary = False
            check()
            target = goal_union((term for goal in goals for term in self.images.get(goal, ())),
                                context=self.request.context)
            if z3.is_false(target):
                continue
            if self.preparation is None:
                self.preparation = SolverPreparation(tuple(self.roots))
            elif len(self.preparation.base) != len(self.roots):
                # Roots only append. Another goal at the same cut reuses the
                # exact base without scanning that whole prefix again.
                self.preparation.extend(tuple(self.roots))
            roots = self.preparation.base
            candidate = solve('query-prefix', (*roots, target), preparation=self.preparation)
            calls.append(candidate)
            if candidate.answer is SolverAnswer.UNSAT:
                continue  # The next cut, never a full-domain verdict.
            if candidate.answer is not SolverAnswer.SAT:
                break
            self.at_boundary = True
            return candidate, calls
        self.retire_proposals()
        return None, calls

    def retire_proposals(self):
        """Keep unrestricted construction after an unsuccessful proposal."""
        self.enabled = self.at_boundary = False
        if self.preparation is not None:
            self.preparation.clear()
            self.preparation = None

    def propose(self, goals, *, solve, check):
        import z3
        from ..core.query import SolverAnswer
        from .source_member import solve_fixed_source_member

        candidate, calls = self.propose_request(goals, solve=solve, check=check)
        if candidate is not None:
            rows = self.request.rows(candidate.model)
            started = time.monotonic_ns()
            try:
                with diagnostic_stage('query.member.encoding', horizon=self.horizon,
                                      goals=len(self.goals)):
                    _, request, encoding = self.source.bind_request(
                        self.horizon, goals=self.goals, fixed_rows=rows)
                if request is not self.request:
                    raise ValueError('a fixed member must retain the original request owner')
                with diagnostic_stage('query.member.preparation'):
                    fixed = _prepare_goal_query(self.source, self.horizon, self.goals,
                        self.excluded, request, encoding, check=check)
            finally:
                self.construction_ns += time.monotonic_ns() - started
            target = goal_union((fixed.hits[goal] for goal in goals if goal in fixed.hits),
                                context=encoding.alive.ctx)

            def member_solve(phase, assertions, **kwargs):
                result = solve(phase, assertions, **kwargs)
                calls.append(result)
                return result

            try:
                member = solve_fixed_source_member((*fixed.constraints, target, z3.Not(fixed.escape)),
                    (encoding,), (*request.constraints, *request.fix(rows)), member_solve,
                    phase='query-prefix-member', check=check, proposal=candidate.model)
            finally:
                fixed.preparation.clear()
            if member.answer is SolverAnswer.SAT:
                # Other goals can use different inputs at this same cut.
                return fixed, member.model, calls
        self.retire_proposals()  # An invalid tail is not an unreachable goal.
        return None, None, calls


class GoalQueryCache:
    """Own one disposable query image and its solver, never native evidence.

    Replace before building, publish after completion, and retire on failure.
    The compiler's matching cache entry is part of the same lifetime. Reuse
    requires both the nominal view and the actual request/encoding identities.
    """

    def __init__(self):
        self.current: PreparedGoalQuery | None = None
        self.growing: GrowingGoalQuery | None = None

    def clear_solver(self, *, interrupted=False):
        if self.current is not None:
            self.current.preparation.clear(interrupted=interrupted)
        if self.growing is not None and self.growing.preparation is not None:
            self.growing.preparation.clear(interrupted=interrupted)

    def close_construction(self):
        growing, self.growing = self.growing, None
        if growing is not None:
            growing.close()

    def close(self):
        previous, self.current = self.current, None
        try:
            self.close_construction()
        finally:
            if previous is not None:
                try:
                    previous.preparation.clear()
                finally:
                    previous.source.release_query(previous.horizon, previous.encoding)

    def start(self, source, horizon, goals, proved_domains, *, check):
        """Use a source's streaming capability; completed-only providers stay valid."""
        check()
        if not goals or not callable(getattr(source, 'bind_request_steps', None)):
            return None
        if self.current is not None:
            if self.current.matches(source, horizon, goals, proved_domains):
                return None
            self.close()
        if self.growing is not None and not self.growing.matches(source, horizon, goals, proved_domains):
            self.close()
        if self.growing is None:
            self.growing = GrowingGoalQuery(source, horizon, goals, proved_domains)
        return self.growing

    def prepare(self, source, horizon, goals, proved_domains, *, check):
        goals, excluded = tuple(goals), frozenset(proved_domains)
        encoding = None
        try:
            check()
            if self.current is not None and not self.current.matches(source, horizon, goals, excluded):
                self.close()
            if self.growing is not None and not self.growing.matches(source, horizon, goals, excluded):
                self.close()
            with diagnostic_stage('history.construct', goals=list(goals), horizon=horizon):
                if self.growing is None:
                    _, request, encoding = source.bind_request(horizon, goals=goals)
                else:
                    _, request, encoding = self.growing.finish(check)
            check()
            if self.current is not None:
                if self.current.request is request and self.current.encoding is encoding:
                    return self.current
                self.close()
            self.current = _prepare_goal_query(source, horizon, goals, excluded,
                                               request, encoding, check=check)
            return self.current
        except BaseException:
            self.close()
            if encoding is not None:
                source.release_query(horizon, encoding)
            raise


def _prepare_goal_query(source, horizon, goals, excluded, request, encoding, *, check):
    """Derive query-local facts without erasing original roots or unproved exits."""
    import z3
    from ..symbolic.exact_solve import SolverPreparation
    from .source_member import source_literal_consequences

    selected = frozenset(goals)
    constraints = (*encoding.constraints,
        *(z3.Not(term) for term in encoding.environment_assumptions.values()))
    # Capture derived history images once for this exact query lifetime.
    # Keep original observations separate from reduced solver targets; decoding
    # must not reassemble the history, nor consume a substituted observation.
    observations = MappingProxyType(dict(encoding.hits))
    domain_faults = MappingProxyType(dict(encoding.domain_faults))
    escape = goal_union((encoding.unfinished,
        *(term for name, term in domain_faults.items() if name not in excluded),
        *encoding.source.memory.conflicts), context=encoding.alive.ctx)
    hits, facts = observations, None
    names = tuple(goal for goal in observations if goal in selected)
    targets = tuple(observations[goal] for goal in names) + (escape,)
    # A completed, literally false query image has nothing left to propagate.
    # Preserve every original premise and escape; this is not a shortened
    # history, a feasibility proof, or permission to drop an unknown exit.
    if not all(z3.is_false(term) for term in targets):
        with diagnostic_stage('history.literals'):
            facts = source_literal_consequences(constraints, encoding,
                context=encoding.alive.ctx, check=check, targets=targets)
            if facts is not None:
                reduced = facts.constant_targets(targets)
                hits = MappingProxyType({**observations, **dict(zip(names, reduced[:-1], strict=True))})
                escape = reduced[-1]
                # Entailed equations aid preprocessing; no original root is erased.
                constraints += facts.literal_constraints()
    check()
    return PreparedGoalQuery(source, horizon, selected, excluded, request, encoding,
        constraints, observations, domain_faults, hits, escape, facts, SolverPreparation(constraints))
