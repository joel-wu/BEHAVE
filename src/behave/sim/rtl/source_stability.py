"""UNSAT-only ready/valid proofs over arbitrary settled source boundaries.

Local windows can prove stability and an inductive conservation relation can
prove output ownership without expanding a long history. These are candidate
invariants: establishment and preservation use the actual source transition.
A local SAT/UNKNOWN proves nothing about reachability; the caller retains its
original full-history query, including unencoded exits.
"""

from dataclasses import replace

import z3

from ..symbolic.bitvectors import widen
from ..symbolic.step_template import StepTemplate, _map_tree, _tree
from ..core.input_queue import input_is_present
from ..core.monitor import Monitor
from ..core.physical_value import packed_width
from ..core.protocol import READY_VALID
from ..core.query import SolverAnswer
from ..core.refusal import Refused
from ..core.ready_valid_product import (
    CountedReadyValidState, OrderedReadyValidConfig, OrderedReadyValidEvent,
    ordered_ready_valid_initial, ordered_ready_valid_step,
    ready_valid_pointer_step,
)
from .source_values import ArrayType
from .source_direct import DirectSourceCompiler
from .source_effects import accesses
from .task_request import ReadyValidRequestCodec
from .task_symbolic import Z3Algebra
from ..symbolic.diagnostics import diagnostic_stage


GOAL = 'monitor.sink-stability'
COUNT_GOAL = 'monitor.output-without-transaction'


def prove_reset_restoration(source, request, goals, *, solve, check):
    """Exclude reset violations when every complete reset has one state image.

    Compare two independent arbitrary entry states and input histories over
    the declared reset hold. Equality of their complete owned post-slot state
    proves restoration without expanding the intervening ordinary workload.
    This is a sufficient property, not an assumption that reset clears state:
    SAT/UNKNOWN retains the original query. Failed/unfinished source paths and
    all domain faults still require that query's separate escape check.
    """
    from ..core.reset_monitor import ResetRestorationProvider
    from .source_domains import _transition_proof_roots
    from .source_task import SourceTaskRelation

    machine, contract = source.machine, source.contract
    if (source.template.direct is None or type(request.codec) is not ReadyValidRequestCodec
            or len(contract.clocks) != 1 or contract.memories):
        return frozenset()
    clock, = contract.clocks
    goal = ResetRestorationProvider.violation_goal(clock.name)
    if clock.reset_restores is not True or goal not in goals:
        return frozenset()
    check()
    monitors, fields = source.monitor_layout(request)
    monitors = monitors.project((goal,))
    config, = (config for domain, config in monitors.resets if domain == clock.name)
    if not config.state_fields:
        return frozenset((goal,))
    fields = {name: fields[name] for name in config.state_fields}
    context = source.template.context
    view = SourceTaskRelation(source.template, machine, contract, (), context=context,
        monitors=monitors, monitor_fields=fields, query_projection=True)
    local = view.source.relation
    ports = {field.port: name for name, field in machine.program.fields.items()
             if field.port is not None}
    clk, rst = ports[clock.clock], ports[clock.reset]

    def reset_image(tag):
        state = source.template.initial_state(machine, dict.fromkeys(machine.program.fields),
            declarations=False, tag=tag, check=check).state
        state = {name: value if name in machine.program.fields else _map_tree(
            _tree(value), lambda term: z3.FreshConst(term.sort(), tag + '_private'))
            for name, value in state.items()}
        for name in source.template.direct.quiet_flags:
            state[name] = Z3Algebra.zero_like(state[name])
        phases = []
        for _ in range(config.hold_cycles):
            check()
            pins = {name: z3.FreshConst(state[name].sort(), tag + '_pin')
                    for name, _ in machine.drive_inputs}
            pins[rst] = z3.BitVecVal(int(not clock.reset_active_low), state[rst].size(), ctx=context)
            pins[clk] = z3.BitVecVal(int(clock.edge == 'falling'), state[clk].size(), ctx=context)
            active = {**pins, clk: z3.BitVecVal(int(clock.edge == 'rising'), state[clk].size(), ctx=context)}
            phases.extend((pins, active, pins))
        encoded = local.encode_phases(state, tuple(phases),
            event_bounds=(machine.event_bound,) * len(phases), require_quiescence=False,
            tag=tag, check=check)
        return encoded, view.monitor_state(encoded.state)

    with diagnostic_stage('reset.construct', hold_cycles=config.hold_cycles):
        left, first = reset_image('reset_image_left')
        right, second = reset_image('reset_image_right')
    different = z3.Or(*(first[name] != second[name] for name in config.state_fields))
    roots = (*left.constraints, *right.constraints, left.continuable, right.continuable, different)
    answer = solve('reset:constant-image', _transition_proof_roots(roots, check=check))
    return frozenset((goal,)) if answer.answer is SolverAnswer.UNSAT else frozenset()


def _control_proof_roots(roots, *, check):
    """Relax data calculations without discarding selection identities.

    Constant strides, offsets and shifts can select a held payload. Cutting
    them into unrelated values loses stability even when the storage and
    selector are unchanged. The existing transition abstraction keeps this
    wiring and linear arithmetic, while sharing cuts for nonlinear regions.
    XOR networks need the same treatment: a protocol hold proof must not
    bit-blast a complete checksum/hash computation just because it contains
    no multiplication. Keep muxes, masks and wiring, and share one arbitrary
    value for each identical XOR term across ALL assumptions and violations.
    Restoring those values to the original terms recovers the original query.
    Walk only the retained DAG; never enter a discarded computation.
    Only UNSAT transfers; SAT/UNKNOWN still needs the original history.
    """
    from .source_domains import _transition_proof_roots

    roots = _transition_proof_roots(roots, check=check)
    rewritten, pending = {}, [(root, False) for root in roots]
    while pending:
        check()
        term, ready = pending.pop()
        key = term.get_id()
        if key in rewritten:
            continue
        if not z3.is_app(term):
            rewritten[key] = term  # Binders keep their original scope.
            continue
        if term.decl().kind() == z3.Z3_OP_BXOR:
            rewritten[key] = z3.FreshConst(term.sort(), prefix='protocol_data')
            continue
        children = term.children()
        if not ready:
            pending.append((term, True))
            pending.extend((child, False) for child in children)
            continue
        args = [rewritten[child.get_id()] for child in children]
        rewritten[key] = (term if all(a.eq(b) for a, b in zip(children, args))
                          else term.decl()(*args))
    return tuple(rewritten[root.get_id()] for root in roots)


def _boundary_candidates(machine):
    """Combinational fixed points feeding ports or sequential control.

    Register data remains arbitrary, but an extracted enable such as
    ``accepted = valid & ready`` must agree with its pins at the cut. Follow
    control temporaries without pulling in an unrelated registered datapath.
    These are only candidate equalities; establishment and preservation must
    still prove them against the complete source transition.
    """
    from ..symbolic import ir
    from ..symbolic.slicing import reads

    storage = set(machine.program.fields)
    needed = {name for name, field in machine.program.fields.items() if field.port is not None}
    for index, process in enumerate(machine.program.processes):
        if process.initial_process is not None or (not process.deferred and machine.startup[index]):
            continue
        controls, definitions = set(), {}
        for node in ir.walk(process.activation):
            if isinstance(node, (ir.If, ir.For)):
                reads(node.guard if isinstance(node, ir.If) else node.trip_count, controls)
            elif isinstance(node, ir.Assign) and node.var not in storage:
                reads(node.expr, definitions.setdefault(node.var, set()))
        pending = list(controls)
        while pending:
            extra = definitions.get(pending.pop(), set()) - controls
            controls.update(extra)
            pending.extend(extra)
        needed.update(controls & storage)
    possible = {index: accesses(process.activation, storage)
                for index, process in enumerate(machine.program.processes)
                if not process.deferred and process.initial_process is None and machine.startup[index]}
    writers = {}
    for index, (_reads, writes) in possible.items():
        for name in writes:
            writers.setdefault(name, set()).add(index)
    selected, pending = set(), list(needed)
    while pending:
        for index in writers.get(pending.pop(), ()):
            if index not in selected:
                selected.add(index)
                pending.extend(possible[index][0] - needed)
                needed.update(possible[index][0])
    return tuple(machine.program.processes[index] for index in sorted(selected))


def _control_constants(machine, observed, *, check):
    """Source comparisons propose register values, never assume invariants.

    Pair Boolean flags and existing scalar register/constant comparisons with
    output-valid, without enumerating register pairs or multibit value domains.
    A widened or nonzero-encoded FSM needs the same rule as a one-bit flag.
    Rejected proposals only lose an optional proof, not an execution path.
    """
    from ..symbolic import ir

    registers = {name for process in machine.program.processes
                 for name, blocking, _ in process.write_masks if not blocking}
    registers &= set(observed)
    values = set()
    for name in registers:
        kind = machine.program.kinds[name]
        if not isinstance(kind, ArrayType) and kind.width == 1:
            values.update((name, value) for value in (kind.domain.lo, kind.domain.hi))
    for process in machine.program.processes:
        aliases, repeated = {}, set()
        locals_ = set(process.procedure.locals)
        for node in ir.walk(process.activation):
            check()
            if isinstance(node, ir.Assign) and node.var in locals_:
                if node.var in aliases:
                    repeated.add(node.var)
                aliases[node.var] = node.expr
        aliases = {name: expr for name, expr in aliases.items() if name not in repeated}
        resolved = {}

        def register_of(expr):
            path, seen = [], set()
            while id(expr) not in resolved:
                check()
                if id(expr) in seen:
                    resolved[id(expr)] = None
                    break
                seen.add(id(expr))
                path.append(id(expr))
                if isinstance(expr, ir.Op) and expr.op in {'uwrap', 'swrap', 'toint'}:
                    expr = expr.args[0]
                elif isinstance(expr, ir.Var) and expr.name in aliases:
                    expr = aliases[expr.name]
                else:
                    resolved[id(expr)] = (expr.name if isinstance(expr, ir.Var)
                                          and expr.name in registers else None)
            result = resolved[id(expr)]
            for identity in path:
                resolved[identity] = result
            return result

        for expr in ir.expression_nodes(process.activation):
            check()
            if not isinstance(expr, ir.Op) or expr.op not in {'eq', 'ne', 'lt', 'le', 'gt', 'ge'}:
                continue
            for register, literal in (expr.args, expr.args[::-1]):
                if not isinstance(literal, ir.Lit) or type(literal.value) is not int:
                    continue
                name = register_of(register)
                if name is None:
                    continue
                kind = machine.program.kinds[name]
                if not isinstance(kind, ArrayType) and kind.domain.lo <= literal.value <= kind.domain.hi:
                    values.add((name, literal.value))
    return tuple(sorted(values))


def prove_protocol_goals(source, request, goals, *, solve, check):
    """Prove the property before paying for optional boundary refinement.

    The ordinary codec has three physical phases per sample. Other schedules
    and external memory devices retain the unchanged full-history path. The
    caller owns the budget and accounts for every physical solver invocation.
    Omitting extra candidate equalities enlarges the cut-state domain: UNSAT
    remains a proof, while SAT/UNKNOWN supplies no reachability evidence. Only
    properties left unproved retry the original stronger boundary obligations.
    """
    contract, machine = source.contract, source.machine
    if (source.template.direct is None or type(request.codec) is not ReadyValidRequestCodec
            or len(contract.clocks) != 1 or contract.memories
            or len(contract.inputs()) != 1 or len(contract.outputs()) != 1
            or any(endpoint.adapter.name != READY_VALID for endpoint in contract.endpoints)):
        return frozenset()
    requested = frozenset(goals).intersection((GOAL, COUNT_GOAL))
    if not requested:
        return frozenset()
    check()
    # Every observed sample below is guarded by its own continuable bit.
    # Independent actions may reorder *failed prefixes*, not successful cuts;
    # no failed value/error/event from this local view becomes query evidence.
    # Whole-history failure and unencoded-exit checks use the original view.
    boundary = DirectSourceCompiler.for_settled_state(machine, successful_only=True)
    # The compiler's boundary predicate already contains these processes'
    # scalar fixed points and success conditions. Do not compile a second
    # identical template and conjoin the same predicate again below.
    covered = {id(machine.program.processes[index]) for index in boundary._regions()}
    boundary_processes = tuple(process for process in _boundary_candidates(machine)
                               if id(process) not in covered)
    try:
        with diagnostic_stage('protocol.cut', refined=False):
            proved = _prove_protocol_cut(source, request, requested, boundary, (),
                                         solve=solve, check=check)
    except Refused:
        proved = frozenset()  # The original refinement may still be encodable.
    remaining = requested - proved
    if not remaining or not boundary_processes:
        return proved
    check()
    try:
        with diagnostic_stage('protocol.cut', refined=True):
            refined = _prove_protocol_cut(source, request, remaining, boundary,
                boundary_processes, solve=solve, check=check)
    except Refused:
        # A failed refinement cannot erase an already proved weaker-cut fact.
        # The caller still checks the shared deadline and all history escapes.
        return proved
    return proved | refined


def _prove_protocol_cut(source, request, goals, boundary, boundary_processes, *, solve, check):
    """One unchanged source transition under the selected cut invariants."""
    contract, machine = source.contract, source.machine
    context = source.template.context
    candidates = []
    for process in boundary_processes:
        check()
        environment = {**process.procedure.state_ranges, **machine.input_ranges,
                       **{name: kind.domain for name, kind in machine.program.kinds.items()}}
        template = StepTemplate(process.procedure, environment).normalized()
        # Scalar boundary equalities suffice for these optional lemmas. A
        # memory-dependent protocol still executes every array read/write;
        # omitting an array fixed-point candidate only enlarges the cut domain.
        names = tuple(name for name, blocking, _ in process.write_masks
                      if blocking and not isinstance(machine.program.kinds[name], ArrayType))
        candidates.append((template, names))

    # State-only cuts have an explicit combinational fixed-point predicate.
    # Establish and preserve it below; quiet scheduler flags alone do not make
    # an arbitrary wire image settled. This view cannot observe source events.
    # The local proof reads ports and the established boundary predicates,
    # not every hidden register's successor. Use the SAME dependency-closed
    # relation as ordinary history queries; it retains all failures, scheduler
    # obligations and predecessors across drive/event views. Include every
    # boundary candidate's reads and writes so no invariant is weakened by
    # the projection (including pure-region predicates owned by the compiler).
    storage = set(machine.program.fields)
    reads = {name for name, field in machine.program.fields.items() if field.port is not None}
    reads.update(boundary.quiet_flags)
    for process in boundary_processes:
        observed, written = accesses(process.activation, storage)
        reads.update(observed | written)
    for index in boundary._regions():
        observed, written = boundary.effects[index]
        reads.update(observed | written)
    local = source.template.relation(machine, (), context=context, state_reads=reads)
    local = replace(local, direct=boundary)

    def run(method, *args, **options):
        nonlocal local
        check()
        try:
            return getattr(local, method)(*args, **options)
        except Refused as error:
            if local.direct is None or error.refusal.kind not in (
                    'rtl-direct-coactive-dependency', 'rtl-direct-state-boundary-open'):
                raise
            check()
            local = replace(local, direct=None)
            return getattr(local, method)(*args, **options)

    def invariant(state):
        roots = [boundary.boundary_condition(state, check=check)]
        for template, names in candidates:
            check()
            # Read expressions over the entry directly. Naming every successor
            # and substituting the complete definition map once per field
            # needlessly repeats work quadratically in the boundary size.
            encoded = template.instantiate(state, check=check)
            roots.append(encoded.ok)
            for name in names:
                # Range-narrowed constants and entry storage can have distinct
                # signed IR carriers. Compare VALUES using the encoder's
                # common-width operation, never truncate either operand.
                before, after = widen(state[name], encoded.state_post[name])
                roots.append(before == after)
        return z3.And(*roots) if roots else z3.BoolVal(True, ctx=context)

    def proof_roots(label, roots):
        check()
        from .source_domains import _transition_proof_roots

        # Both control stability and count induction retain exact linear
        # updates and selections. Nonlinear cuts admit every original model;
        # only UNSAT transfers. SAT/UNKNOWN leaves the whole history intact.
        # Abstract constraints and the negated property TOGETHER to preserve
        # repeated terms; never weaken only the assumptions or only the goal.
        roots = tuple(roots)
        control = label.startswith('stability:')
        abstract = (_control_proof_roots(roots, check=check) if control
                    else _transition_proof_roots(roots, check=check))
        if any(not before.eq(after) for before, after in zip(roots, abstract)):
            label += ':data-cut' if control else ':arithmetic-cut'
        return label, abstract

    def proof(label, roots):
        label, abstract = proof_roots(label, roots)
        return solve(label, abstract)

    def proves(label, roots):
        return proof(label, roots).answer is SolverAnswer.UNSAT

    def encode(state, pins, label):
        with diagnostic_stage('protocol.construct', phase=label):
            return run('encode', state, pins, event_bound=machine.event_bound,
                require_quiescence=False, tag='stability_' + label, check=check)

    def compiled_boundary():
        # A fallback scheduler cannot borrow the direct compiler's certificate.
        # Additional proposed invariants and shared writers also retain SMT.
        return not candidates and local.direct is boundary and boundary.certifies_fixed_points()

    def certified_plan(state, pins):
        # The certificate depends on the validated compiler plan, not its
        # instantiated arithmetic. Reuse encoding's exact eligibility checks;
        # a fallback scheduler or another proposed invariant gains no fact.
        if not compiled_boundary():
            return False
        check()
        try:
            boundary.interval_plan(state, pins, event_bound=machine.event_bound)
        except Refused:
            return False  # Let the unchanged encoder/fallback own this case.
        check()
        return True

    if (candidates or boundary._regions()) and not certified_plan(source.initial.state, None):
        startup = encode(source.initial.state, None, 'startup')
        if not compiled_boundary() and not proves('boundary:establishment', (*startup.constraints,
                startup.continuable, z3.Not(invariant(startup.state)))):
            return frozenset()

    # Arbitrary *physical* storage, with arbitrary scheduler-private payloads.
    # A successful source interval empties its pending flags; no other private
    # field, reset outcome or register value may be assumed at this cut.
    free = source.template.initial_state(machine, dict.fromkeys(machine.program.fields),
        declarations=False, tag='stability_free', check=check).state
    free = {name: value if name in machine.program.fields else _map_tree(
                _tree(value), lambda term: z3.FreshConst(term.sort(), 'stability_private'))
            for name, value in free.items()}
    for name in source.template.direct.quiet_flags:
        free[name] = Z3Algebra.zero_like(free[name])
    ports = {field.port: name for name, field in machine.program.fields.items() if field.port is not None}
    clock, = contract.clocks
    clk = ports[clock.clock]
    inactive = z3.BitVecVal(int(clock.edge == 'falling'), free[clk].size(), ctx=context)
    active = z3.BitVecVal(int(clock.edge == 'rising'), free[clk].size(), ctx=context)

    if candidates or boundary._regions():
        for phase in ('control', 'active', 'return'):
            check()
            state = dict(free)
            # The first control phase may start from either startup clock
            # level. Later phases have the codec's fixed edge/return levels.
            if phase != 'control':
                state[clk] = active if phase == 'return' else inactive
            pins = {name: z3.FreshConst(state[name].sort(), 'stability_pin') if phase == 'control'
                    else state[name] for name, _ in machine.drive_inputs}
            pins[clk] = active if phase == 'active' else inactive
            if certified_plan(state, pins):
                continue
            step = encode(state, pins, phase)
            if not compiled_boundary() and not proves('boundary:preservation:' + phase, (*step.constraints, invariant(state),
                    step.continuable, z3.Not(invariant(step.state)))):
                return frozenset()

    source_endpoint, sink = contract.inputs()[0], contract.outputs()[0]
    iv, = source_endpoint.adapter.environment_controls
    ir, = source_endpoint.adapter.artifact_controls
    ov, = sink.adapter.artifact_controls
    ready, = sink.adapter.environment_controls
    algebra = Z3Algebra(context)

    def bit(state, pin):
        return z3.Extract(0, 0, state[ports[pin]]) == 1

    def reset(state):
        value = bit(state, clock.reset)
        return z3.Not(value) if clock.reset_active_low else value

    def window(state, phases, label):
        with diagnostic_stage('protocol.construct', phase=label, phases=len(phases)):
            return run('encode_phases', state, phases,
                event_bounds=(machine.event_bound,) * len(phases),
                require_quiescence=False, tag='protocol_' + label, check=check)

    def control_relation(extra=()):
        # A dependency-closed image, not a payload-free assumption: data-
        # dependent enables and every source failure remain in this cone.
        reads = {ports[pin] for pin in (clock.clock, clock.reset, iv, ir, ov, ready)}
        reads.update(boundary.quiet_flags)
        reads.update(extra)
        for process in boundary_processes:
            observed, written = accesses(process.activation, storage)
            reads.update(observed | written)
        return replace(source.template.relation(machine, (), context=context,
            state_reads=reads), direct=boundary)

    proved = set()
    config = Monitor(contract).finite_channel_config(source.queue_bound)
    if (COUNT_GOAL in goals and request.codec.reset_hold >= 1
            and config is not None and config.counted):
        # Candidate conservation relation, NOT a presumed FIFO implementation:
        # each currently offered output must be backed by an accepted request.
        # The lifetime driver pointer also proves that the finite monitor never
        # drops an acceptance at capacity. Resets may clear its epoch count,
        # but never rewind the driver's queue pointer.
        config = replace(config, payload_fields=0)
        count_state = CountedReadyValidState(
            z3.FreshInt('accepted', ctx=context), z3.FreshInt('outstanding', ctx=context),
            algebra.bool(False), ())
        pointer = z3.FreshInt('input_pointer', ctx=context)

        def conservation(state, monitor, pointer):
            return z3.And(0 <= monitor.outstanding, monitor.outstanding <= monitor.accepted_count,
                monitor.accepted_count <= pointer, pointer <= config.capacity,
                monitor.outstanding >= z3.If(bit(state, ov), 1, 0))

        def count_step(state, monitor, pointer, *, first):
            pins = {name: z3.FreshConst(state[name].sort(), 'count_pin')
                    for name, _ in machine.drive_inputs}
            pins[clk] = inactive
            released = z3.FreshInt('released', ctx=context)
            if first:
                pins[ports[clock.reset]] = z3.BitVecVal(int(not clock.reset_active_low),
                    state[ports[clock.reset]].size(), ctx=context)
            present = input_is_present(pointer, released, z3.Not(reset(pins)), algebra)
            pins[ports[iv]] = z3.If(present,
                z3.BitVecVal(1, state[ports[iv]].size(), ctx=context),
                z3.BitVecVal(0, state[ports[iv]].size(), ctx=context))
            encoded = window(state, (pins, {**pins, clk: active}, pins), 'count')
            sample = encoded.phases[0]
            accepted, successor = ready_valid_pointer_step(pointer,
                z3.And(z3.Not(reset(sample.state)), bit(sample.state, iv)), bit(sample.state, ir), algebra)
            step = ordered_ready_valid_step(config, monitor, OrderedReadyValidEvent(
                reset(sample.state), 0, accepted, bit(sample.state, ov),
                bit(sample.state, ready), ()), algebra)
            roots = (*encoded.constraints, invariant(state), pointer <= released,
                released <= config.capacity, sample.continuable)
            # A later clock/return failure cannot erase a bad pre-clock sample.
            failure = z3.Or(step.hits[COUNT_GOAL], z3.And(encoded.continuable,
                z3.Not(conservation(encoded.state, step.state, successor))))
            if not first:
                roots += (conservation(state, monitor, pointer),)
            return proves('count:' + ('establishment' if first else 'preservation'), (*roots, failure))

        # Conservation observes handshake/control, not output payload values.
        # Keep the shared boundary proof above, but close this window's own
        # state cone before encoding it. The ordinary relation retains all
        # failures and dependencies across phases; payload-derived readiness
        # therefore remains, while an unrelated datapath need not enter SMT.
        # Extra proposed boundary predicates still require their full reads.
        shared = local
        local = control_relation()
        state = {name: value for name, value in free.items() if name in local.source.state_names}
        state[clk] = inactive
        initial = ordered_ready_valid_initial(config, algebra, ())
        try:
            if count_step(state, initial, algebra.integer(0), first=True) and count_step(
                    state, count_state, pointer, first=False):
                proved.add(COUNT_GOAL)
        finally:
            # Stability still observes the complete requested payload. Never
            # let a count-only projection become another goal's proof owner.
            local = shared

    if GOAL not in goals:
        return frozenset(proved)

    # Begin *before* either sample from any invariant state. Arbitrary input
    # fields, reset and ready are retained. Only the real input driver's hold
    # rule connects the samples; there is no assumed DUT readiness/progress.
    phases = []
    for _ in range(2):
        pins = {name: z3.FreshConst(free[name].sort(), 'stability_window_pin')
                for name, _ in machine.drive_inputs}
        pins[clk] = inactive
        phases.extend((pins, {**pins, clk: active}, pins))
    # The first sample includes the startup-to-inactive case too.
    # Stop at the second sample; its future is not a proof premise.
    phases = phases[:4]

    # Prove candidate control relations before asking the payload window.
    # Otherwise an impossible arbitrary control state can send the entire
    # datapath to SMT just to obtain the SAT that used to trigger refinement.
    # Candidate generation is unchanged. Their own dependency-closed source
    # transition proves reset establishment and preservation; no relation is
    # presumed from register names, code shape or observed execution.
    control_facts = ()
    controls = (_control_constants(machine, set(local.source.state_names) - {ports[ov]},
                                  check=check) if request.codec.reset_hold >= 1 else ())
    if controls:
        shared = local
        local = control_relation(name for name, _ in controls)
        state = {name: value for name, value in free.items() if name in local.source.state_names}
        try:
            prefix = window(state, phases[:3], 'control')
            premise = (*prefix.constraints, invariant(state), prefix.continuable)
            before = {item: z3.Implies(bit(state, ov), state[item[0]] == item[1])
                      for item in controls}
            after = {item: z3.Implies(bit(prefix.state, ov), prefix.state[item[0]] == item[1])
                     for item in controls}
            # Abstract once for the whole candidate batch, not per predicate.
            label, abstract = proof_roots('stability:control', (*premise,
                reset(prefix.phases[0].state), *(before[name] for name in controls),
                *(z3.Not(after[name]) for name in controls)))
            base, reset_root = abstract[:len(premise)], abstract[len(premise)]
            offset = len(premise) + 1
            hypotheses = dict(zip(controls, abstract[offset:offset+len(controls)], strict=True))
            violations = dict(zip(controls, abstract[offset+len(controls):], strict=True))
            suffix = label.removeprefix('stability:control')
            remaining = set(controls)
            for phase in ('establishment', 'preservation'):
                while remaining:
                    check()
                    names = tuple(sorted(remaining))
                    held = (reset_root if phase == 'establishment' else
                            z3.And(*(hypotheses[name] for name in names)))
                    result = solve('stability:control-' + phase + suffix,
                        (*base, held, z3.Or(*(violations[name] for name in names))))
                    if result.answer is SolverAnswer.UNSAT:
                        break
                    if result.answer is not SolverAnswer.SAT:
                        remaining.clear()
                        break
                    bad = {name for name in names if z3.is_true(
                        result.model.eval(violations[name], model_completion=True))}
                    if not bad:
                        remaining.clear()
                        break
                    # Removing a candidate weakens the induction premise;
                    # recheck it before retaining any of the other relations.
                    remaining -= bad
            control_facts = tuple(before[name] for name in sorted(remaining))
        finally:
            local = shared

    encoded = window(free, phases, 'stability')
    first, second = encoded.phases[0].state, encoded.phases[3].state

    def payload(sample, endpoint):
        return tuple(z3.Extract(packed_width(field.type) - 1, 0, sample[ports[field.port]])
                     for field in endpoint.fields)

    hold = z3.Implies(z3.And(bit(first, iv), z3.Not(bit(first, ir)), z3.Not(reset(second))),
        z3.And(bit(second, iv), *(left == right for left, right in
            zip(payload(first, source_endpoint), payload(second, source_endpoint), strict=True))))
    config = OrderedReadyValidConfig(capacity=1, payload_fields=len(sink.fields),
                                    answers_per_transaction=None)
    monitor = ordered_ready_valid_initial(config, algebra,
        tuple(algebra.zero_like(value) for value in payload(first, sink)))
    for slot, sample in enumerate((first, second)):
        step = ordered_ready_valid_step(config, monitor, OrderedReadyValidEvent(
            reset(sample), slot, algebra.bool(False), bit(sample, ov),
            bit(sample, ready), payload(sample, sink)), algebra)
        monitor = step.state
    roots = (*encoded.constraints, invariant(free), *control_facts, hold,
             encoded.phases[0].continuable, encoded.phases[3].continuable, step.hits[GOAL])
    answer = proof('stability:window', roots)
    if answer.answer is SolverAnswer.UNSAT:
        proved.add(GOAL)
    return frozenset(proved)


def prove_stability(source, request, *, solve, check):
    return GOAL in prove_protocol_goals(source, request, (GOAL,), solve=solve, check=check)
