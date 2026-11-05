"""Prove partial source-operation domains before publishing an activation.

For standalone translation, interval transfer handles array bounds and nonzero divisors. If joins
lose a relation, one exact query asks whether *any* operation can leave its
domain. Private marks precede the operation, never becoming input assumptions.

Until the first invalid operation the translation has the source semantics. Its
mark is reached even if the totalized IR behaves differently afterwards.
Thus UNSAT proves definedness without relying on the totalization's value.
SAT/UNKNOWN leave standalone translation refused. A local-search owner
retains nonconstant conditions as terminal private obligations, provided it
proves them absent over the entire recorded local domain before querying public
goals. Neither answer is a candidate score or public reachability result.
"""

from __future__ import annotations

import math
import time

from behave.core import runtime_defaults as defaults
from behave.isolation.preparation_control import check_preparation

from ..symbolic import ir
from ..symbolic import ranges
from ..core.refusal import Refusal, Refused
from ..search_policy import remaining_solver_ms, search_phase


def prove_transition_domains(source, *, solve=None, check):
    """Optional UNSAT lemma over the unrestricted source scheduler step.

    No recorded state, reset outcome, target goal or successful-execution
    premise enters this query. Scalar physical storage retains its declared
    width/sign, not an inferred invariant. A mark impossible for every
    scheduler action and physical entry is impossible in a bounded history.
    SAT/UNKNOWN grants no fact: the caller retains the original history faults.
    Source-path transfer first excludes marks using only declared types and
    control flow. With no solver, the remaining pass uses literal facts only;
    it never constructs a speculative SMT lemma before the bounded query.
    An explicit solver retains the standalone lemma operation for the rest.
    """
    check()
    groups = {mark: (mark,) for mark, _cause in source.machine.domain_checks}
    if not groups:
        return frozenset()
    proved = source.machine.program.control_facts({}, events=groups, check=check).absent_events
    remaining = {name: marks for name, marks in groups.items() if name not in proved}
    return proved | _prove_transition_groups(source, remaining, 'operation-domain:transition',
                                             solve=solve, check=check)


def _register_range_candidates(machine, *, check):
    """Comparisons and typed write ranges propose bounds, never establish them.

    Visit the existing expression DAG once, following casts, constant offsets
    and single-assigned local aliases without expanding expressions. Ignoring
    wrap here is only a proposal: the real bit-precise reset and transition
    must prove the resulting bound below.
    """
    registers = {name for process in machine.program.processes
                 for name, blocking, nba in process.write_masks if blocking or nba}
    limits, write_ranges = {}, {}

    def constant(expr):
        value = ranges.of_expression(expr, {})
        return value.lo if value is not None and value.lo == value.hi else None

    def register(expr, aliases, values):
        chain = []
        seen = set()
        while id(expr) not in values:
            if id(expr) in seen:
                values[id(expr)] = None
                break
            seen.add(id(expr))
            item, offset = expr, 0
            if isinstance(expr, ir.Op) and expr.op in {'uwrap', 'swrap', 'toint'}:
                expr = expr.args[0]
            elif isinstance(expr, ir.Var) and expr.name in aliases:
                expr = aliases[expr.name]
            elif isinstance(expr, ir.Sel):
                # A selected element suggests a uniform leaf envelope. This
                # is only a proposal; every cell and writer must satisfy it.
                expr = expr.array
            elif isinstance(expr, ir.Op) and expr.op in {'add', 'sub'}:
                left, right = expr.args
                offset = constant(right)
                if offset is not None:
                    expr = left
                    offset *= -1 if item.op == 'sub' else 1
                elif item.op == 'add' and (offset := constant(left)) is not None:
                    expr = right
                else:
                    values[id(expr)] = None
                    break
            else:
                values[id(expr)] = ((expr.name, 0)
                    if isinstance(expr, ir.Var) and expr.name in registers else None)
                break
            chain.append((item, offset))
        value = values[id(expr)]
        for item, offset in reversed(chain):
            value = None if value is None else (value[0], value[1] + offset)
            values[id(item)] = value
        return value

    for process in machine.program.processes:
        writes = [write for write in process.deferred
                  if write.target in registers and write.whole]
        blocking = {name for name, now, _ in process.write_masks if now}
        if writes or blocking:
            # Helpers often test a copied argument, not the physical register.
            # Their bounded return is visible in the shared source-path
            # transfer. Compute it once for all queues, from unrestricted
            # declared storage; no proposed invariant is an input here.
            queues = {write.valid: write for write in writes if write.array is None}
            arrays = {write.data: write for write in writes if write.array is not None}

            def written(command, env):
                check()
                if isinstance(command, (ir.Assign, ir.Store)) and command.var in blocking:
                    name = command.var
                    # Observe the written value, not the hull of the complete
                    # successor array (which also contains arbitrary old cells).
                    # Partial/nested writes may give a loose proposal; the
                    # existing all-writer preservation check remains decisive.
                    value = ranges.of_expression(command.expr, env)
                elif isinstance(command, ir.Assign) and command.var in arrays:
                    # Array queues are whole-container values. The lowerer's
                    # nested store expression captures the written leaf; its
                    # other cells still contain arbitrary old state. A plain
                    # copy is queue initialization, not an enqueue.
                    expr = command.expr
                    if not isinstance(expr, ir.Op) or expr.op != 'store':
                        return
                    while isinstance(expr, ir.Op) and expr.op == 'store':
                        expr = expr.args[2]
                    name = arrays[command.var].target
                    value = ranges.of_expression(expr, env)
                elif isinstance(command, ir.Assign) and command.var in queues:
                    enabled = ranges.of_expression(command.expr, env)
                    if enabled is not None and enabled.lo == enabled.hi == 0:
                        return
                    # Inactive queue initialization copies arbitrary old state.
                    # Only capture its value when this queue becomes valid.
                    write = queues[command.var]
                    name, value = write.target, env.get(write.data)
                else:
                    return
                value = value or machine.program.kinds[name].domain
                previous = write_ranges.get(name, value)
                write_ranges[name] = ranges.Range(
                    min(previous.lo, value.lo), max(previous.hi, value.hi))

            ranges.procedure_post(process.procedure,
                dict(process.procedure.state_ranges), source_paths=True,
                witness=written)
        aliases, repeated = {}, set()
        locals_ = set(process.procedure.locals)
        for command in ir.walk(process.activation):
            if isinstance(command, ir.Assign) and command.var in locals_:
                if command.var in aliases:
                    repeated.add(command.var)
                aliases[command.var] = command.expr
        aliases = {name: expr for name, expr in aliases.items() if name not in repeated}
        values = {}
        for expr in ir.expression_nodes(process.activation):
            check()
            if not isinstance(expr, ir.Op) or expr.op not in {'eq', 'ne', 'lt', 'le', 'gt', 'ge'}:
                continue
            reverse = {'lt': 'gt', 'le': 'ge', 'gt': 'lt', 'ge': 'le'}
            for (left, right), operator in ((expr.args, expr.op),
                    (expr.args[::-1], reverse.get(expr.op, expr.op))):
                affine = register(left, aliases, values)
                limit = constant(right)
                if affine is None or limit is None:
                    continue
                name, offset = affine
                # For x + stride < end, the next x may be as large as end-1,
                # not just end-stride. Propose that loose inductive envelope;
                # equality sentinels still propose their translated value.
                limit = max(limit - offset, limit - (operator == 'lt')) \
                    if offset > 0 and operator in {'lt', 'le'} else limit - offset
                domain = machine.program.kinds[name].domain
                if domain.lo <= limit < domain.hi:
                    limits[name] = max(limits.get(name, domain.lo), limit)
    # Array domains already carry the hull of their scalar leaves. Keep one
    # uniform candidate per register, not a product proportional to its size;
    # the shared procedure transfer restores the declared recursive shape.
    proposed = {name: ranges.Range(max(machine.program.domain(name).lo, 0)
                    if limit >= 0 else machine.program.domain(name).lo, limit)
                for name, limit in limits.items()}
    for name, value in write_ranges.items():
        domain = machine.program.kinds[name].domain
        if (value.lo, value.hi) != (domain.lo, domain.hi):
            upper = min(value.hi, limits.get(name, value.hi))
            if value.lo <= upper:
                # A signed bounded datapath need not be nonnegative. Preserve
                # the write envelope's lower end instead of counter heuristics.
                proposed[name] = ranges.Range(value.lo, upper)
    return proposed


def prove_reset_domains(source, request, *, solve, check):
    """Optional request-scoped domain proof using one shared reset/clock cycle.

    Unlike the arbitrary-entry lemma, this relies on the codec's mandatory
    first reset sample. Prove startup, reset establishment, inductive closure
    and first-fault absence separately. No successful-exit premise may hide a
    domain fault. Failed/unknown candidates retain the original history.
    The returned marks are valid only for this source and this request codec;
    they must never authorize arbitrary-entry source translation.
    """
    marks = frozenset(mark for mark, _ in source.machine.domain_checks)
    proved = _prove_reset_groups(source, request, marks, public=False,
                                solve=solve, check=check)
    return marks if proved == marks else frozenset()


def prove_reset_goals(source, request, goals, *, solve, check):
    """Request-local event exclusions, using proved clock-boundary ranges.

    Source transfer shares range preservation and failed-prefix observation.
    Only useful exclusions require a symbolic reset/startup relation. Keep
    the event-cover compiler for those checks; the state-only compiler cannot
    replace event observation. No exclusion discharges the history's escapes.
    """
    selected = frozenset(goals)
    groups = frozenset(goal for _, _, goal in source.machine.occurrences if goal in selected)
    return _prove_reset_groups(source, request, groups, public=True, solve=solve, check=check)


def _prove_reset_groups(source, request, groups, *, public, solve, check):
    """Share reset establishment/preservation for a batch of observations."""
    from dataclasses import replace
    import z3

    from ..symbolic.step_template import _leaves, _map_tree, _tree
    from ..core.query import SolverAnswer
    from .atomic_request import AtomicRequestCodec
    from .source_direct import DirectSourceCompiler, NativeCoverSourceCompiler
    from .task_request import ReadyValidRequestCodec
    from .task_symbolic import Z3Algebra

    machine, contract = source.machine, source.contract
    if (not groups or source.template.direct is None
            or len(contract.clocks) != 1 or contract.memories):
        return frozenset()
    clock, = contract.clocks
    codec = request.codec
    if type(codec) is ReadyValidRequestCodec:
        reset_first = codec.reset_hold >= 1
    elif type(codec) is AtomicRequestCodec:
        slots = codec.schedule.slots
        # Reuse precisely the same control/active/return cycle. Idle slots or
        # another clock need a different inductive relation, not an assumption.
        reset_first = (bool(slots) and clock.name in slots[0].resets
                       and all(slot.event.ticks == (clock.name,) for slot in slots))
    else:
        reset_first = False
    if not reset_first:
        return frozenset()
    check()
    candidates = _register_range_candidates(machine, check=check)
    if candidates:
        from ..symbolic.slicing import goal_state_dependencies

        # Choosing optional hypotheses is an IR dependency question, not a
        # reason to compile a state-history formula. Reuse the shared reaching-
        # state graph; its conservative support only selects proposals. Reset,
        # preservation and event absence still require the checks below.
        # Private failures have the same dependency graph as public marks;
        # unrelated optional bounds must not recreate dead storage at the cut.
        marks = ({mark for mark, _, goal in machine.occurrences if goal in groups}
                 if public else groups)
        dependencies = goal_state_dependencies(machine.procedure)
        mask = 0
        for mark in marks:
            mask |= dependencies[mark]
        observed = {name for index, name in enumerate(machine.procedure.state)
                    if mask & (1 << index)}
        candidates = {name: bound for name, bound in candidates.items()
                      if name in observed}
    if not candidates:
        return frozenset()

    def control_facts(candidates, groups):
        marks = {mark for mark, _, goal in machine.occurrences if goal in groups}
        facts = machine.program.control_facts(candidates, events=marks, check=check)
        candidates = {name: candidates[name] for name in facts.preserved_ranges}
        groups -= {owner for mark, _, owner in machine.occurrences
                   if owner in groups and mark not in facts.absent_events}
        return candidates, groups

    if public:
        # Decide whether source transfer can help before constructing any
        # SMT cycle. This is a conditional fact, not a reset assumption.
        candidates, groups = control_facts(candidates, groups)
        if not candidates or not groups:
            return frozenset()
    # Domain faults observe failed prefixes too: the successful-only relaxed
    # order used by a payload stability lemma is NOT admissible here.
    ports = {field.port: name for name, field in machine.program.fields.items() if field.port is not None}
    clk, rst = ports[clock.clock], ports[clock.reset]
    local = source.template.relation(machine, tuple(groups) if public else (),
                                    state_reads=set(candidates) | {clk, rst})
    # Match the compiler to the owner's already-closed source/drive boundary.
    # A whole-machine plan can reject an irrelevant latched scratch output;
    # the projected plan still retains every failure and notification edge.
    compiler = DirectSourceCompiler.for_settled_state(machine, observation=local)
    if not compiler.certifies_fixed_points():
        return frozenset()
    # Settled-state compilation deliberately cannot certify source events.
    # The same physical cycle must retain all event orders for public goals.
    local = replace(local, direct=NativeCoverSourceCompiler(machine) if public else compiler)
    context = source.template.context
    free = source.template.initial_state(machine, dict.fromkeys(machine.program.fields),
        declarations=False, tag='domain_range_free', check=check).state
    free = {name: value if name in machine.program.fields else _map_tree(_tree(value),
                lambda term: z3.FreshConst(term.sort(), 'domain_range_private'))
            for name, value in free.items()}
    for name in compiler.quiet_flags:
        free[name] = Z3Algebra.zero_like(free[name])
    boundary = compiler.boundary_condition(free, check=check) if public else z3.BoolVal(True, ctx=context)
    pins = {name: z3.FreshConst(free[name].sort(), 'domain_range_pin')
            for name, _ in machine.drive_inputs}
    pins[clk] = z3.BitVecVal(int(clock.edge == 'falling'), free[clk].size(), ctx=context)
    active = {**pins, clk: z3.BitVecVal(int(clock.edge == 'rising'), free[clk].size(), ctx=context)}
    cycle = local.encode_phases(free, (pins, active, pins),
        event_bounds=(machine.event_bound,) * 3, require_quiescence=False,
        tag='domain_range_cycle', check=check)
    reset = pins[rst] == int(not clock.reset_active_low)
    phase = 'source-goal' if public else 'operation-domain'
    def predicates(state):
        # Both ends are hypotheses, not IR assumptions. The same reset and
        # closure checks establish scalar and uniform array-leaf intervals.
        # Test actual cells: an array's historical value sources may include
        # overwritten old values and are not an exact inductive premise.
        from .source_values import ArrayType

        result = {}
        for name, bound in candidates.items():
            kind = machine.program.kinds[name]
            while isinstance(kind, ArrayType):
                kind = kind.element
            checks = []
            for value in _leaves(state[name]):
                check()
                checks.append(z3.And(value >= bound.lo, value <= bound.hi) if kind.signed else
                              z3.And(z3.UGE(value, bound.lo), z3.ULE(value, bound.hi)))
            result[name] = z3.And(*checks)
        return result

    before, after = predicates(free), predicates(cycle.state)

    def prove_bounds(label, *, inductive):
        # Removing a failed conjunct weakens the inductive premise. Recheck
        # closure after every removal; no rejected bound can re-enter later.
        remaining = set(candidates)
        while remaining:
            check()
            premise = (z3.And(*(before[name] for name in remaining)) if inductive else reset)
            names = tuple(sorted(remaining))
            base = (*cycle.constraints, boundary, cycle.continuable, premise)
            roots = (*base, *(z3.Not(after[name]) for name in names))
            relaxed = _transition_proof_roots(roots, check=check)
            violations = dict(zip(names, relaxed[len(base):], strict=True))
            answer = solve(label, (*relaxed[:len(base)], z3.Or(*violations.values())))
            if answer.answer is SolverAnswer.UNSAT:
                return remaining
            if answer.answer is not SolverAnswer.SAT:
                break
            # A relaxed counterexample only rejects optional hypotheses;
            # it is not a reachable execution or a candidate failure.
            bad = {name for name in remaining if z3.is_true(
                answer.model.eval(violations[name], model_completion=True))}
            if not bad:
                break
            remaining -= bad
        return set()

    established = prove_bounds(phase + ':reset-range', inductive=False)
    if not established:
        return frozenset()
    if public:
        if established != set(candidates):
            # A rejected reset bound weakens every dependent source fact.
            # Recheck preservation and observation under the weaker premise.
            candidates, groups = control_facts(
                {name: candidates[name] for name in established}, groups)
    else:
        candidates = {name: candidates[name] for name in established}
        candidates = {name: candidates[name] for name in prove_bounds(
            phase + ':range-preservation', inductive=True)}
    if not candidates or not groups:
        return frozenset()
    held = z3.And(*(before[name] for name in candidates))
    def observations(encoded):
        # Phase plans omit events belonging only to inactive processes (e.g.
        # initial blocks during a clock cycle), just like their public hits.
        values = encoded.hits if public else encoded.domain_faults
        return {name: values.get(name, z3.BoolVal(False, ctx=context)) for name in groups}
    # Startup runs before reset. It uses the real declaration/initial-process
    # state, not the arbitrary settled boundary of the repeated cycle.
    startup = local.settle(source.initial.state, event_bound=machine.event_bound,
        require_quiescence=False, tag='domain_range_startup', check=check)
    queries = (
        ('startup', startup.constraints, observations(startup)),
        ('reset', (*cycle.constraints, boundary, reset), observations(cycle)),
    )
    if not public:
        queries += (('step', (*cycle.constraints, boundary, held), observations(cycle)),)
    proved = frozenset(groups)
    for label, base, hits in queries:
        check()
        names = tuple(sorted(proved))
        relaxed = _transition_proof_roots((*base, *(hits[name] for name in names)), check=check)
        proved = _prove_group_absence(relaxed[:len(base)],
            dict(zip(names, relaxed[len(base):], strict=True)), phase + ':range-' + label,
            solve=solve, check=check)
        if not proved:
            break
    return proved


def _elide_transition_domains(machine, proved, *, check):
    """Apply freshly proved domain exclusions before dependency closure.

    Internal to the query owner: names alone are not proof evidence. The caller
    must have just proved them on this exact machine over arbitrary entry or
    the complete requested reset domain. A request-scoped view must never be
    reused as an arbitrary-entry relation. All other failures and every public
    occurrence remain.
    Keep dead declarations outside the old guards so the error alphabet stays
    unchanged without retaining those guards' arithmetic dependency cones.
    """
    from dataclasses import replace
    from .source_scheduler import _compose_source_scheduler

    proved = frozenset(proved)
    private = {mark for mark, _cause in machine.domain_checks}
    public = {mark for mark, _key, _goal in machine.occurrences}
    if not proved <= private or proved & public:
        raise ValueError('domain exclusions must be private marks of this source machine')
    if not proved:
        return machine
    if machine.reports is not None:
        raise Refused(Refusal('rtl-source-domain-projection-unsupported',
            'late source reports retain their original complete transition'))

    def project(body):
        check()
        removed = {id(node): node for node in ir.walk(body)
                   if isinstance(node, ir.Fail) and node.goal in proved}
        if not removed:
            return body
        rewritten = ir.copy_command(body, {key: ir.Skip() for key in removed})
        return ir._sequence([rewritten, *(ir.If(ir.Lit(False), node, ir.Skip())
                                         for node in removed.values())])

    processes = []
    for process in machine.program.processes:
        check()
        old = process.procedure
        proc = ir.Procedure(old.state, old.inputs, old.locals, old.outputs,
            project(old.body), types=old.types, initial=old.initial,
            output_vars=old.output_vars, signature=old.signature,
            output_capacity=old.output_capacity)
        proc.state_ranges = old.state_ranges
        processes.append(replace(process, procedure=proc, activation=project(process.activation),
            domain_checks=tuple(pair for pair in process.domain_checks if pair[0] not in proved)))
    result = _compose_source_scheduler(replace(machine.program, processes=tuple(processes)),
                                      machine.subscriptions, machine.startup)
    if (result.procedure.state != machine.procedure.state or
            result.procedure.types != machine.procedure.types or
            result.drive_inputs != machine.drive_inputs or result.pending != machine.pending or
            result.queue_edges != machine.queue_edges or len(result.actions) != len(machine.actions)):
        raise Refused(Refusal('rtl-source-domain-projection-layout-changed',
            'a domain projection must preserve every original physical coordinate'))
    check()
    # Proof-based erasure cannot alter the declared bounded scheduler domain.
    return replace(result, event_bound=machine.event_bound)


def prove_transition_goals(source, goals, *, solve, check):
    """Exclude source events only if ALL occurrences are locally impossible.

    The unrestricted scheduler step includes arbitrary entry state and every
    source action, including events before failure. SAT is not a reachable
    request; UNKNOWN is not a proof. Both keep the original history query.
    Public protocol goals are not source marks and are deliberately untouched.
    The query owner must still discharge every whole-history escape.
    """
    check()
    selected, groups = frozenset(goals), {}
    for mark, _key, goal in source.machine.occurrences:
        if goal in selected:
            groups.setdefault(goal, []).append(mark)
    return _prove_transition_groups(source, groups, 'source-goal:transition',
                                    solve=solve, check=check)


def _nonwrapping_product_hulls(term, proof, operands=None):
    """Use signed linear bounds only when every mathematical product fits."""
    import z3

    if (not z3.is_app_of(term, z3.Z3_OP_BMUL) or term.num_args() != 2
            or any(z3.is_bv_value(arg) for arg in term.children())):
        return None
    left, right = (proof.coarse_signed_hull(arg) for arg in (
        term.children() if operands is None else operands))
    if left is None or right is None:
        return None
    limit = 1 << (term.size() - 1)
    if not all(-limit <= x * y < limit for x in (left.lo, left.hi)
               for y in (right.lo, right.hi)):
        return None
    return left, right


def _product_cut_cases(term, proof):
    """Keep bounds on a shared selector arm without assuming it executes.

    Arbitrary old values on the inactive arm can destroy a narrow product's
    unconditional range. Split only a common, immediate selector: at most two
    cases per product, never a Cartesian expansion of independent guards.
    The original guard remains in each implication and wrapping arms receive
    no linear bound. Restoring the exact product satisfies every case.
    """
    import z3

    hulls = _nonwrapping_product_hulls(term, proof)
    if hulls is not None:
        return ((None, True, tuple(term.children()), hulls),)
    if (not z3.is_app_of(term, z3.Z3_OP_BMUL) or term.num_args() != 2
            or any(z3.is_bv_value(arg) for arg in term.children())):
        return ()
    operands = tuple(term.children())
    selectors = [arg for arg in operands if z3.is_app_of(arg, z3.Z3_OP_ITE)]
    if not selectors or any(not arg.arg(0).eq(selectors[0].arg(0)) for arg in selectors):
        return ()
    guard, cases = selectors[0].arg(0), []
    for truth, index in ((True, 1), (False, 2)):
        arms = tuple(arg.arg(index) if z3.is_app_of(arg, z3.Z3_OP_ITE) else arg
                     for arg in operands)
        hulls = _nonwrapping_product_hulls(term, proof, arms)
        if hulls is not None:
            cases.append((guard, truth, arms, hulls))
    return tuple(cases)


def _product_cut_bounds(value, left, right, hulls):
    """A linear outer approximation, not a new multiplication circuit.

    The caller proved no signed wrap for the original operands. The same
    guarantee covers each endpoint times the other operand, so these bounds
    use the original carrier. Every product below has a literal coefficient.
    Restoring the original expressions satisfies all bounds simultaneously.
    """
    import z3

    for operand, own, other in ((right, hulls[0], hulls[1]),
                                (left, hulls[1], hulls[0])):
        low, high = own.lo * operand, own.hi * operand
        if other.hi <= 0:
            low, high = high, low
        elif other.lo < 0:
            low, high = (z3.If(operand >= 0, low, high),
                         z3.If(operand >= 0, high, low))
        yield value >= low
        yield value <= high


def _has_abstractable_operation(roots, nonlinear, data_selectors, check):
    """Inspect each native DAG node once; no wrappers or algebraic rewrites.

    An empty cut frontier needs no sign normalization or formula rebuilding.
    Stop at binders, just as the abstraction itself does.
    """
    import z3
    from z3 import z3core as native

    if not roots:
        return False
    ctx = roots[0].ctx_ref()
    seen, pending = set(), [root.as_ast() for root in roots]
    while pending:
        check()
        term = pending.pop()
        key = native.Z3_get_ast_id(ctx, term)
        if key in seen:
            continue
        seen.add(key)
        if native.Z3_get_ast_kind(ctx, term) != z3.Z3_APP_AST:
            continue
        kind = native.Z3_get_decl_kind(ctx, native.Z3_get_app_decl(ctx, term))
        children = tuple(native.Z3_get_app_arg(ctx, term, i)
                         for i in range(native.Z3_get_app_num_args(ctx, term)))
        if kind in nonlinear and (kind != z3.Z3_OP_BMUL or sum(
                native.Z3_get_ast_kind(ctx, child) != z3.Z3_NUMERAL_AST
                for child in children) > 1):
            return True
        if (data_selectors and kind == z3.Z3_OP_ITE
                and native.Z3_get_sort_kind(ctx, native.Z3_get_sort(ctx, term)) == z3.Z3_BV_SORT
                and any(native.Z3_get_ast_kind(ctx, child) != z3.Z3_NUMERAL_AST
                        for child in children[1:])):
            return True
        pending.extend(children)
    return False


def _comparison_control_values(roots, check, *, children_of=None):
    """Keep selector/affine values that feed a numeric control comparison.

    Cutting such a selector loses the relationship between its activation and
    its update, even when each arm has a sound unconditional range. Follow the
    shared DAG once, without entering binders or expanding Boolean paths.
    Bit wiring (including truncation) preserves that dependency. Nonlinear
    arithmetic and other data computations stop the backward slice; their
    existing abstraction remains available.
    """
    import z3

    if children_of is None:
        children_of = z3.ExprRef.children
    comparisons = {z3.Z3_OP_SLT, z3.Z3_OP_SLEQ, z3.Z3_OP_SGT, z3.Z3_OP_SGEQ,
                   z3.Z3_OP_ULT, z3.Z3_OP_ULEQ, z3.Z3_OP_UGT, z3.Z3_OP_UGEQ,
                   z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT}
    seen, pending, operands = set(), list(roots), []
    while pending:
        check()
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        if not z3.is_app(term):
            continue
        children = children_of(term)
        if term.decl().kind() in comparisons and children and z3.is_bv(children[0]):
            operands.extend(children)
        pending.extend(children)
    kept = set()
    linear = {z3.Z3_OP_BADD, z3.Z3_OP_BSUB, z3.Z3_OP_BNEG,
              z3.Z3_OP_ZERO_EXT, z3.Z3_OP_SIGN_EXT, z3.Z3_OP_CONCAT, z3.Z3_OP_EXTRACT}
    while operands:
        check()
        term = operands.pop()
        if term.get_id() in kept:
            continue
        kept.add(term.get_id())
        if not z3.is_app(term):
            continue
        kind, children = term.decl().kind(), children_of(term)
        if kind == z3.Z3_OP_ITE:
            operands.extend(children[1:])
        elif kind in linear or (kind == z3.Z3_OP_BMUL and sum(
                not z3.is_bv_value(child) for child in children) <= 1):
            operands.extend(children)
    return kept


def _transition_proof_roots(roots, *, check, bounds=None, data_selectors=False):
    """A shared arithmetic-region abstraction for optional local UNSAT.

    Keep control, comparisons and all roots. Replace each distinct BV
    control-free region containing nonlinear arithmetic by a fresh value of
    the same sort. Giving each value its original expression recovers the exact
    formula. Thus UNSAT transfers; SAT/UNKNOWN supplies no transition fact.
    Identical DAG terms share one value. Do not introduce uninterpreted
    functions: their cross-application congruence can add quadratic work to
    a prepass that is supposed to avoid expensive arithmetic.

    This is NOT a witness relation. Inconclusive groups go straight to the
    unchanged whole-history query, not a second expensive local arithmetic
    attempt. Region selection and range transfer each memoize the shared DAG;
    no task names, width caps or time caps enter this operation.

    Source-goal prefilters can additionally cut BV-valued data selectors.
    Bitwise floating-point helpers contain large nested selector networks
    even after multiplication/division is abstracted. A local SAT here grants
    no reachability fact, so do not solve that datapath just to defer it to the
    exact history. Boolean control, numeric-control updates and literal-only
    selectors remain intact. Inactive default values must not be substituted
    for an active counter update merely because their joined range allows it.
    Source-domain and count-conservation proofs retain this abstraction;
    stability separately relaxes data computations in its control-only proof.
    Bit extraction, concatenation and extension remain exact wiring. Giving
    a product and its low/sign bits unrelated cut values loses their identity
    and creates spurious operation-domain failures without removing arithmetic.
    Nonwrapping binary products additionally retain linear operand bounds.
    Their operands use this same shared cut DAG; restoring exact expressions
    satisfies the bounds, but a relaxed SAT still grants no transition fact.
    A common immediate selector may retain guarded bounds for either arm;
    inactive old values must not erase the active arm's proved finite range.
    No independent guards are cross-expanded or assumed true.
    """
    import z3
    from ..symbolic.term_bounds import _SyntacticClosureProof

    class Work:
        def charge(self):
            check()

    hulls = _SyntacticClosureProof({}, work=Work()) if bounds is not None else None

    nonlinear = {z3.Z3_OP_BMUL, z3.Z3_OP_BUDIV, z3.Z3_OP_BSDIV,
                 z3.Z3_OP_BUREM, z3.Z3_OP_BSREM, z3.Z3_OP_BSMOD,
                 z3.Z3_OP_BUDIV_I, z3.Z3_OP_BSDIV_I,
                 z3.Z3_OP_BUREM_I, z3.Z3_OP_BSREM_I}
    wiring = {z3.Z3_OP_EXTRACT, z3.Z3_OP_CONCAT, z3.Z3_OP_SIGN_EXT, z3.Z3_OP_ZERO_EXT}
    roots = tuple(roots)
    if not _has_abstractable_operation(roots, nonlinear, data_selectors, check):
        return roots
    # Control selection, sign/region analysis and reconstruction inspect the
    # same immutable DAG. Decode each adjacency once, retaining its owner so
    # native IDs cannot be recycled. This index dies with this abstraction;
    # no fact or rewritten value is reused in another proof.
    nodes = {}

    def children_of(term):
        key = term.get_id()
        if key not in nodes:
            nodes[key] = term, tuple(term.children())
        return nodes[key][1]

    control = (_comparison_control_values(roots, check, children_of=children_of)
               if data_selectors else set())
    # Preserve selectors/comparisons rather than hiding a control decision in
    # a data cut. Cut a whole straight-line sum of products, not every product
    # followed by a redundant network of wide adders. Constant multiplication
    # stays exact unless it belongs to a larger nonlinear region.
    regions, normal, operators = {}, {}, set()
    pending = [(root, False) for root in roots]
    while pending:
        check()
        term, ready = pending.pop()
        key = term.get_id()
        if key in regions:
            continue
        if not z3.is_app(term):
            regions[key] = False, False
            normal[key] = 1, term
            continue
        children = children_of(term)
        if not ready:
            pending.append((term, True))
            pending.extend((child, False) for child in children)
            continue
        kind = term.decl().kind()
        literal_product = kind == z3.Z3_OP_BMUL and sum(
            not z3.is_bv_value(c) for c in children) <= 1
        nonlinear_here = (kind in nonlinear and not literal_product) or (
            data_selectors and z3.is_bv(term) and kind == z3.Z3_OP_ITE and key not in control
            and not all(z3.is_bv_value(c) for c in children[1:]))
        if nonlinear_here:
            operators.add(key)
        pure = z3.is_bv(term) and kind != z3.Z3_OP_ITE and all(
            regions[c.get_id()][0] for c in children)
        regions[key] = pure, nonlinear_here or any(regions[c.get_id()][1] for c in children)
        # Keep the algebraic identity between a region and its negation. A
        # lowered subtraction often spells -sum(products) as sum(-products).
        # Cutting those into unrelated values invents negative max-minus-item
        # distances. Factor only existing signs; never distribute products or
        # expand a polynomial. This adds at most one node per original node.
        sign, canonical = 1, term
        parts = [normal[c.get_id()] for c in children]
        if z3.is_bv_value(term) and term.as_signed_long() < 0:
            sign, canonical = -1, z3.BitVecVal(-term.as_signed_long(), term.size(), ctx=term.ctx)
        elif kind == z3.Z3_OP_BNEG:
            sign, canonical = -parts[0][0], parts[0][1]
        elif kind == z3.Z3_OP_BMUL:
            sign = math.prod(s for s, _ in parts)
            args = [v for _, v in parts if not (z3.is_bv_value(v) and v.as_long() == 1)]
            canonical = (term.decl()(*args) if len(args) > 1 else args[0] if args else
                         z3.BitVecVal(1, term.size(), ctx=term.ctx))
        elif kind == z3.Z3_OP_BADD and all(s == parts[0][0] for s, _ in parts):
            sign = parts[0][0]
            canonical = term.decl()(*(v for _, v in sorted(parts, key=lambda p: p[1].get_id())))
        normal[key] = sign, canonical

    def cut_region(term):
        key = term.get_id()
        pure, contains_nonlinear = regions[key]
        if not (pure and contains_nonlinear or key in operators):
            return False
        # Sign normalization can turn -Concat(...) or -1*SignExt(...) into
        # the underlying wire. Cutting that alias while retaining the wire
        # itself disconnects two views of the same value. Traverse both;
        # their shared arithmetic child, if any, remains the cut boundary.
        return (term.decl().kind() not in wiring
                and normal[key][1].decl().kind() not in wiring)

    # Register the cut frontier before rewriting any root. A sum first seen
    # through its expanded subtraction must share a later root's region too.
    cuts, products, cut_inputs = {}, {}, []
    seen, pending = set(), list(roots)
    while pending:
        check()
        term = pending.pop()
        key = term.get_id()
        if key in seen:
            continue
        seen.add(key)
        if cut_region(term):
            _, canonical = normal[key]
            if canonical.get_id() not in cuts:
                cuts[canonical.get_id()] = z3.FreshConst(term.sort(), prefix='local_arithmetic')
            cases = () if hulls is None else _product_cut_cases(term, hulls)
            if cases:
                # Retain operand relationships only through the same shared
                # abstraction. Never inline their nonlinear definitions or
                # compare all pairs of cuts. Each DAG node is visited once.
                products[key] = cases
                for guard, _truth, operands, _hulls in cases:
                    inputs = operands if guard is None else (*operands, guard)
                    cut_inputs.extend(inputs)
                    pending.extend(inputs)
        elif z3.is_app(term):
            pending.extend(children_of(term))
    rewritten = {}
    pending = [(root, False) for root in (*roots, *cut_inputs)]
    while pending:
        check()
        term, ready = pending.pop()
        key = term.get_id()
        if key in rewritten:
            continue
        if not z3.is_app(term):
            rewritten[key] = term  # No traversal underneath binders.
            continue
        if cut_region(term):
            # A one-transition existential cut, not a shared function across
            # histories. Only observed or explicitly bounded operands survive.
            sign, canonical = normal[key]
            value = cuts[canonical.get_id()]
            if sign < 0:
                value = -value
            rewritten[key] = value
            if hulls is not None:
                hull = hulls.coarse_signed_hull(term)
                limit = 1 << (term.size() - 1)
                if hull is not None and hull.lo > -limit:
                    bounds.append(value >= z3.BitVecVal(hull.lo, term.size(), ctx=term.ctx))
                if hull is not None and hull.hi < limit - 1:
                    bounds.append(value <= z3.BitVecVal(hull.hi, term.size(), ctx=term.ctx))
            continue
        children = children_of(term)
        if not ready:
            pending.append((term, True))
            pending.extend((child, False) for child in children)
            continue
        args = [rewritten[child.get_id()] for child in children]
        if term.decl().kind() == z3.Z3_OP_BADD:
            # Recover a previously cut sum from a flattened subtraction:
            # control + (-p) + (-q) -> control - cut(p+q). Grouping existing
            # operands by sign is linear apart from sorting; no subset search
            # or polynomial expansion. Unmatched groups remain unchanged.
            grouped = {1: [], -1: []}
            for child, replacement in zip(children, args):
                sign, canonical = normal[child.get_id()]
                grouped[sign].append((canonical, replacement))
            joined = []
            for sign, parts in grouped.items():
                combined = (term.decl()(*(v for v, _ in sorted(parts, key=lambda p: p[0].get_id())))
                            if len(parts) > 1 else None)
                shared = None if combined is None else cuts.get(combined.get_id())
                joined.extend((shared if sign > 0 else -shared,) if shared is not None
                              else (v for _, v in parts))
            args = joined
        value = (term if all(a.eq(b) for a, b in zip(children, args))
                 and len(children) == len(args)
                 else term.decl()(*args))
        rewritten[key] = value
    if bounds is not None:
        for key, cases in products.items():
            for guard, truth, operands, product_hulls in cases:
                check()
                value = rewritten[key]
                constraints = list(_product_cut_bounds(value,
                    *(rewritten[arg.get_id()] for arg in operands), product_hulls))
                if guard is None:
                    bounds.extend(constraints)
                else:
                    endpoints = [x * y for x in (product_hulls[0].lo, product_hulls[0].hi)
                                 for y in (product_hulls[1].lo, product_hulls[1].hi)]
                    constraints.extend((value >= min(endpoints), value <= max(endpoints)))
                    condition = rewritten[guard.get_id()]
                    bounds.append(z3.Implies(condition if truth else z3.Not(condition),
                                             z3.And(*constraints)))
        # Another root can expose a product separately from a cut sum that
        # contains it (notably after subtraction is flattened). Keep their
        # cheap linear relationship; otherwise abstraction invents independent
        # values for a sum and its already-observed operands. Nonlinear
        # definitions are never reintroduced by these linking equations.
        owners = {term.get_id(): term for _, term in normal.values()}
        used, pending = set(), [*(rewritten[root.get_id()] for root in roots), *bounds]
        while pending:
            check()
            term = pending.pop()
            if term.get_id() in used:
                continue
            used.add(term.get_id())
            if z3.is_app(term):
                pending.extend(children_of(term))
        for key, value in cuts.items():
            check()
            if value.get_id() not in used:
                continue
            term = owners[key]
            children = children_of(term)
            linear = term.decl().kind() in {z3.Z3_OP_BADD, z3.Z3_OP_BSUB, z3.Z3_OP_BNEG}
            if not linear or not any(c.get_id() in cuts and cuts[c.get_id()].get_id() in used
                                     for c in children):
                continue
            if all(c.get_id() in cuts or z3.is_const(c) for c in children):
                bounds.append(value == term.decl()(*(cuts.get(c.get_id(), c) for c in children)))
    return tuple(rewritten[root.get_id()] for root in roots)


def _prove_transition_groups(source, groups, phase, *, solve, check):
    import z3

    from .source_integral import IntegralType

    check()
    if not groups:
        return frozenset()
    template = source.template.local_observations(mark for marks in groups.values() for mark in marks)
    false = template.literal_false_hits(mark for marks in groups.values() for mark in marks)
    proved = frozenset(name for name, marks in groups.items() if set(marks) <= false)
    pending = {name: groups[name] for name in sorted(groups) if name not in proved}
    if not pending or solve is None:
        return proved
    # This query observes one arbitrary-entry activation, not a history.
    # Next-state dependencies can contain a large unrelated datapath; their
    # total definitions need not be constructed or solved to observe a hit.
    view = template.project_local(pending)
    encoded = view.encode(tag='source_transition_lemma', check=check)
    physical = []
    for name, kind in source.machine.program.kinds.items():
        check()
        if not isinstance(kind, IntegralType) or name not in encoded.pre:
            continue
        # StepTemplate deliberately permits the full mathematical carrier;
        # its inferred state ranges are NOT invariants. This owner additionally
        # knows actual HDL storage types. In particular, an unsigned n-bit
        # field cannot take the negative half of its n+1-bit signed carrier.
        # Arrays and scheduler-private state remain conservative overapproximations.
        value = encoded.pre[name]
        width = max(value.size(), kind.width + int(not kind.signed))
        value = z3.SignExt(width - value.size(), value)
        lo, hi = kind.interval
        physical.extend((value >= lo, value <= hi))
    constraint = z3.And(encoded.constraint, *physical) if physical else encoded.constraint
    original = (constraint, *(encoded.hits[name] for name in pending))
    bounds = []
    constraint, *terms = _transition_proof_roots(original, check=check, bounds=bounds,
        data_selectors=phase == 'source-goal:transition')
    if any(not left.eq(right) for left, right in zip(original, (constraint, *terms))):
        phase += ':arithmetic-cut'
    if bounds:
        constraint = z3.And(constraint, *bounds)
    hits = dict(zip(pending, terms))
    return proved | _prove_group_absence((constraint,), hits, phase, solve=solve, check=check)


def _prove_group_absence(base, hits, phase, *, solve, check):
    """One shared solver; SAT only removes candidates from this local lemma."""
    import z3
    from ..symbolic.exact_solve import SolverPreparation
    from ..core.query import SolverAnswer

    pending = dict(hits)
    preparation = SolverPreparation(tuple(base))
    try:
        while pending:
            check()
            target = z3.Or(*(hits[name] for name in pending))
            answer = solve(phase, (*base, target), preparation=preparation)
            check()
            if answer.answer is SolverAnswer.UNSAT:
                return frozenset(pending)
            if answer.answer is not SolverAnswer.SAT:
                break
            # A local SAT is not a reachable request. It only removes those
            # groups from this optional proof attempt; the full history keeps
            # them. Reuse the SAME unconstrained transition and solver base,
            # never pin its model or assume the witnessed groups are absent.
            # The disjunction can simplify to True, leaving irrelevant entry
            # variables absent from the SAT model. Complete those variables
            # only to choose which groups leave this optional attempt; none
            # of these choices constrains a later query or proves reachability.
            possible = {name for name in pending if z3.is_true(
                answer.model.eval(hits[name], model_completion=True))}
            if not possible:
                break
            pending = {name: term for name, term in pending.items() if name not in possible}
        return frozenset()
    finally:
        preparation.clear()


class DomainProofBudget:
    """One activation shares its existing allowance across modular attempts."""

    def __init__(self, milliseconds=defaults.SIM_RTL_SOURCE_DOMAIN_PROOF_MS):
        self.remaining_ms = milliseconds

    def prove(self, procedure, state_ranges, accesses, *, divisors=None, predicates=None,
              defer=False):
        started = time.monotonic()
        try:
            return prove_source_domains(procedure, state_ranges, accesses,
                divisors=divisors, predicates=predicates, timeout_ms=self.remaining_ms,
                defer=defer)
        finally:
            self.remaining_ms -= math.ceil((time.monotonic() - started) * 1000)


def _with_domain_marks(body, obligations, *, terminal=False):
    """Copy control structure, preserving command order and evaluated operands."""
    rewritten, pending = {}, [(body, False)]
    while pending:
        check_preparation()
        command, ready = pending.pop()
        if not ready:
            pending.append((command, True))
            children = ((command.first, command.second) if isinstance(command, ir.Seq)
                        else (command.then, command.orelse) if isinstance(command, ir.If)
                        else (command.body,) if isinstance(command, ir.For) else ())
            pending.extend((child, False) for child in reversed(children))
            continue
        if isinstance(command, ir.Seq):
            result = ir.Seq(rewritten[id(command.first)], rewritten[id(command.second)])
        elif isinstance(command, ir.If):
            result = ir.If(command.guard, rewritten[id(command.then)], rewritten[id(command.orelse)])
        elif isinstance(command, ir.For):
            result = ir.For(command.index, command.trip_count, rewritten[id(command.body)])
        else:
            result = command
        if id(command) in obligations:
            invalid, goal = obligations[id(command)]
            event = ir.Fail("SourceDomain", goal) if terminal else ir.Mark(goal)
            result = ir.Seq(ir.If(invalid, event, ir.Skip()), result)
        rewritten[id(command)] = result
    return rewritten[id(body)]


def prove_source_domains(procedure, state_ranges, accesses, *, divisors=None, predicates=None,
                         timeout_ms=defaults.SIM_RTL_SOURCE_DOMAIN_PROOF_MS, defer=False):
    """Keep the complete catalogue and source IR; prove only translation domains."""
    unresolved = {}

    def check_interval(command, environment):
        check_preparation()
        predicate = None if predicates is None else predicates.get(id(command))
        if predicate is not None:
            condition, refusal = predicate
            bound = ranges.bounds_of_expression(condition, environment)
            constant = bound is not None and bound.lo == bound.hi == 1
            # Boolean result carriers commonly remain [0, 1] even when the
            # operand ranges prove the comparison. Reuse the same branch
            # transfer as planning; refute the false edge, never assume the
            # required predicate true or narrow the activation's input domain.
            if not constant and ranges.assume(condition, False, environment) is not None:
                unresolved[id(command)] = (ir.Op('not', condition), refusal)
        obligation = accesses.get(id(command))
        if obligation is not None:
            offset, width, extent, refusal = obligation
            bound = ranges.bounds_of_expression(offset, environment)
            if (bound is None or bound.lo is None or bound.hi is None
                    or bound.lo < 0 or bound.hi + width > extent):
                invalid = ir.Op("lor", ir.Op("lt", offset, ir.Lit(0)),
                                ir.Op("gt", offset, ir.Lit(extent - width)))
                unresolved[id(command)] = (invalid, refusal)
        divisor = None if divisors is None else divisors.get(id(command))
        if divisor is not None:
            value, refusal = divisor
            bound = ranges.bounds_of_expression(value, environment)
            if not (bound is not None and (
                    (bound.lo is not None and bound.lo > 0)
                    or (bound.hi is not None and bound.hi < 0))):
                unresolved[id(command)] = (ir.Op("eq", value, ir.Lit(0)), refusal)

    if defer:
        # Transfer actual writes from unknown entry values. In particular,
        # complete helper return masks need not become history-wide queries.
        # Never seed this pass with declaration ranges or a recorded state.
        seen = set()

        def check_local(command, environment):
            seen.add(id(command))
            check_interval(command, environment)
            if isinstance(command, ir.For) and ranges.loop_trip_range(command, environment) is None:
                # The range walker forgets writes but cannot visit this body.
                # A previous outer iteration may have visited it with a known
                # bound: retain checks for this unknown occurrence as well.
                for nested in ir.walk(command.body):
                    check_interval(nested, {})

        ranges.walk_post(procedure.body, {}, check_local)
        for command in ir.walk(procedure.body):
            if id(command) not in seen:
                check_interval(command, {})
        return unresolved or None

    ranges.walk_post(procedure.body, dict(state_ranges), check_interval)
    if not unresolved:
        return
    with search_phase("source-operation-domain-proof"):
        _prove_exact(procedure, state_ranges, unresolved, timeout_ms)


def _prove_exact(procedure, state_ranges, unresolved, timeout_ms):
    import z3

    from ..core import resources
    from ..symbolic import encoder

    first = next(iter(unresolved.values()))[1]

    def refuse(answer, detail, cause=first):
        raise Refused(Refusal(cause.kind, cause.why,
                             **cause.detail, domain_proof=answer,
                             domain_proof_detail=detail))

    deadline = time.monotonic() + timeout_ms / 1000

    def remaining():
        check_preparation()
        milliseconds = math.ceil((deadline - time.monotonic()) * 1000)
        if milliseconds <= 0:
            refuse("unknown", "source operation-domain proof deadline expired")
        return remaining_solver_ms(milliseconds)

    marks = {identity: (row[0], f"@source-domain:{index}")
             for index, (identity, row) in enumerate(unresolved.items())}
    goals = tuple(row[1] for row in marks.values())
    remaining()
    proof = ir.Procedure(procedure.state, procedure.inputs, procedure.locals,
                         procedure.outputs, _with_domain_marks(procedure.body, marks),
                         types=procedure.types)
    try:
        # This is one activation from the complete declared entry domain, not
        # an invariant of repeated clock steps. The encoder transfers locals
        # at their program points. A whole-procedure fixpoint repeats that
        # work and can bring later assignments into an entry-domain question.
        encoded = encoder.encode_goal_query(proof, goals, env=state_ranges,
                                            tag="@source-domain", _checkpoint=remaining)
        remaining()
        solver = z3.Solver()
        solver.add(encoded.constraint, z3.Or([encoded.hits[goal] for goal in goals]))
        solver.set(timeout=remaining())
        memory_mb = resources.solver_memory_mb()
        if memory_mb is not None:
            solver.set(max_memory=memory_mb)
        answer = solver.check()
        remaining()
    except (encoder.EncodingError, z3.Z3Exception) as exc:
        refuse("unavailable", str(exc))
    if answer == z3.sat:
        model = solver.model()
        cause = next(row[1] for goal, row in zip(goals, unresolved.values())
                     if z3.is_true(model.eval(encoded.hits[goal], model_completion=True)))
        refuse("sat", "the source activation admits an operation outside its defined domain", cause)
    elif answer != z3.unsat:
        refuse(str(answer), solver.reason_unknown())
