"""The source-query queue product uses the existing protocol programs verbatim."""

from dataclasses import replace

import pytest
import z3

from behave.sim import api
from behave.sim.core.input_queue import InputQueueState
from behave.sim.core.physical_value import pack_value, packed_width
from behave.sim.core.protocol import adapter_program_for, payload_signals
from behave.sim.core.refusal import Refused
from behave.sim.rtl.protocol_relation import ProtocolBoundary, ProtocolIntervalRelation
from behave.sim.rtl.task_symbolic import Z3Algebra

from .test_apb_protocol import _contract as apb
from .test_axi_lite_protocol import _contract as axil
from .test_axi_lite_protocol import _full_contract as axil_full
from .test_input_framing import contract as framed
from .test_input_framing import nested
from .test_interface_composition import _contract as mixed
from .test_protocol_adapters import _contract as stream
from .test_sampled_protocol import _contract as sampled

BUILDERS = [apb, axil, lambda: axil(False), axil_full, mixed,
            lambda: stream('ready-valid'), lambda: stream('start-done', deadline=2),
            lambda: sampled(input_valid=True, output_valid=True),
            lambda: sampled(input_valid=False, output_valid=False)]


@pytest.mark.parametrize('builder', BUILDERS)
def test_finite_adapter_carrier_preserves_all_states_actions_and_samples(builder):
    from behave.sim.rtl.protocol_relation import _FiniteIndexAlgebra

    context = z3.Context()
    relation = ProtocolIntervalRelation(builder(), context=context)
    for _, _, table in relation.parts.values():
        integer = Z3Algebra(context)
        finite = _FiniteIndexAlgebra(table.state_count, context)
        state = z3.BitVec('state', finite.width, ctx=context)
        reset = z3.Bool('reset', ctx=context)
        presented = {n: z3.Bool('present_'+n, ctx=context) for n in table.presented_names}
        scheduled = {n: z3.Bool('scheduled_'+n, ctx=context) for n in table.scheduled_names}
        observed = {n: (z3.Bool('observed_'+n, ctx=context) if width == 1 else
                       z3.BitVec('observed_'+n, width, ctx=context))
                    for n, width in table.observed_widths.items()}
        before = table.symbolic(z3.BV2Int(state), reset, presented, scheduled, observed, integer)
        after = table.symbolic(state, reset, presented, scheduled, observed, finite)
        differences = [before.state != z3.BV2Int(after.state)]
        for name in ('drive', 'accepted', 'emitted', 'hits'):
            left, right = getattr(before, name), getattr(after, name)
            assert left.keys() == right.keys()
            differences.extend(left[key] != right[key] for key in left)
        differences.extend(table.presentation_allowed(z3.BV2Int(state), name, integer) !=
                           table.presentation_allowed(state, name, finite)
                           for name in table.presented_names)
        old_valid, old_elapsed = table.symbolic_elapse(z3.BV2Int(state), integer)
        new_valid, new_elapsed = table.symbolic_elapse(state, finite)
        solver = z3.Solver(ctx=context)
        solver.add(z3.Or(before.valid != after.valid,
                         z3.And(before.valid, z3.Or(*differences)),
                         old_valid != new_valid,
                         z3.And(old_valid, old_elapsed != z3.BV2Int(new_elapsed))))
        assert solver.check() == z3.unsat


def _packed(contract, queues, context):
    return {e.name: tuple(tuple(
        z3.BoolVal(bool(pack_value(f.type, value)), ctx=context) if packed_width(f.type) == 1 else
        z3.BitVecVal(pack_value(f.type, value), packed_width(f.type), ctx=context)
        for f, value in zip(e.fields, row, strict=True)) for row in queues[e.name])
        for e in contract.inputs()}


def _terms(sample, widths, context):
    return {name: z3.BoolVal(bool(sample[name]), ctx=context) if width == 1 else
            z3.BitVecVal(sample[name], width, ctx=context) for name, width in widths.items()}


def _differences(symbolic, concrete):
    return [term != (bool(concrete[name]) if z3.is_bool(term) else concrete[name])
            for name, term in symbolic.items()]


@pytest.mark.parametrize('builder', BUILDERS)
@pytest.mark.parametrize('prefix', [0, 2, 5])
@pytest.mark.parametrize('symbolic_environment', [False, True, 'reset'])
@pytest.mark.parametrize('queue_length', [None, 0, 3])
@pytest.mark.parametrize('compact', [False, True])
def test_changed_future_samples_continue_real_queue_and_adapter_prefix(
        builder, prefix, symbolic_environment, queue_length, compact):
    contract = builder()
    context = z3.Context()
    relation = ProtocolIntervalRelation(contract, context=context, compact_queues=compact)
    domain, = relation.parts
    _, _, table = relation.parts[domain]
    program = adapter_program_for(contract)
    rows = {e.name: [tuple((i+column+1) & ((1<<packed_width(f.type))-1)
                          for column, f in enumerate(e.fields)) for i in range(12)]
            for e in contract.inputs()}
    allocated = rows
    lengths = None
    if queue_length is not None:
        rows = {name: values[:max(0, queue_length-index)]
                for index, (name, values) in enumerate(allocated.items())}
        lengths = {name: z3.Int(f'length_{name}', ctx=context) for name in rows}
    slots = [api.EventSlot(offers=tuple(rows), reset=i == 6,
        controls={name: int(i == 6) if name == 'rst' else int(i%3 != 0)
                  for name in contract.scheduled_controls()}) for i in range(10)]
    stimulus = api.Stimulus(rows, slots)
    states, queues = program.initial_state(contract), InputQueueState.initial(rows)
    previous = ProtocolBoundary.initial(contract).controls
    expected, samples = [], []
    all_signals = [{**{f.port: 0 for e in contract.outputs() for f in e.fields},
                    **{name: (int((index+j)%3 == 0) if width == 1 else index%4)
                       for j, (name, width) in enumerate(table.observed_widths.items())}}
                   for index in range(len(slots))]
    for index, slot in enumerate(slots):
        if index == prefix:
            boundary = ProtocolBoundary({domain: states}, queues, {}, dict(previous))
        prepared = queues.prepare(stimulus, slot,
            releasable=() if slot.reset else rows,
            presentable={name for name in rows if not slot.reset and
                         program.presentation_allowed(contract, states, contract.endpoint(name))})
        try:
            drive = program.drive(contract, states, slot, dict(prepared.presentations), dict(slot.controls))
        except Refused as exc:
            # Plain sampled inputs cannot suppress an edge after queue
            # exhaustion. Check the same excluded domain symbolically, not
            # an invented zero-valued transaction or a skipped test.
            assert exc.refusal.kind == 'stimulus-sampled-input-missing'
            boundary, prefix, expected = ProtocolBoundary.initial(contract), 0, None
            samples = [_terms(row, table.observed_widths, context) for row in all_signals]
            break
        signals = all_signals[index]
        # Independent request channels, stalls and response codes change within
        # the interval. No seed acceptance or presentation is supplied to encode.
        native = program.step(contract, states, slot, drive, signals)
        queues = queues.commit(prepared, dict(native.accepted_inputs))
        states = native.state
        pins = dict(drive.controls)
        for e in contract.inputs():
            pins.update(payload_signals(e, drive.presentations[e.name]))
        previous.update((name, pins[name]) for name in previous)
        if index >= prefix:
            samples.append(_terms(signals, table.observed_widths, context))
            expected.append((pins, dict(queues.accepted), dict(queues.released),
                table.state_index(states),
                {n for n, value in native.accepted_inputs.items() if value is not None},
                set(native.emitted_outputs), {v.goal for v in native.violations}))
    actions = relation.symbolic_actions(len(slots)-prefix) if symbolic_environment else None
    resets = ({domain: tuple(z3.Bool(f'reset_{i}', ctx=context) for i in range(len(slots)-prefix))}
              if symbolic_environment == 'reset' else None)
    request_slots = [api.EventSlot.from_dict({**slot.as_dict(), 'reset': False})
                     for slot in slots[prefix:]] if resets else slots[prefix:]
    enc = relation.encode(boundary, _packed(contract, allocated, context), request_slots, samples,
                          actions=actions, queue_lengths=lengths, reset_terms=resets)
    assert all(z3.is_bool(term) and term.ctx == context for term in enc.constraints)
    assert len(enc.constraint_slots) == len(enc.constraints)
    assert all(-1 <= index < len(request_slots) for index in enc.constraint_slots)
    solver = z3.Solver(ctx=context)
    solver.add(*enc.constraints)
    if resets:
        solver.add(*(term == slot.reset for term, slot in zip(resets[domain], slots[prefix:], strict=True)))
    if lengths is not None:
        solver.add(*(term == len(rows[name]) for name, term in lengths.items()))
    if actions is not None:
        for slot, action in zip(slots[prefix:], actions, strict=True):
            solver.add(*(term == (name in slot.offers) for name, term in action.offers.items()),
                       *(term == bool(dict(slot.controls)[pin]) for pin, term in action.controls.items()))
    if expected is None:
        assert solver.check() == z3.unsat
        return
    assert solver.check() == z3.sat
    differences = []
    for i, (pins, accepted, released, state, fired, outputs, hits) in enumerate(expected):
        differences.extend(_differences(enc.drives[i], pins))
        differences.extend(_differences(enc.pointers[i+1], accepted))
        differences.extend(_differences(enc.released[i+1], released))
        differences.append(enc.adapters[i+1][domain] != state)
        differences.extend(term != (name in fired) for name, term in enc.accepted[i].items())
        differences.extend(term != (name in outputs) for name, term in enc.emitted[i].items())
        differences.extend(term != (name in hits) for name, term in enc.hits[i].items())
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('builder', [*BUILDERS, lambda: framed(nested())])
@pytest.mark.parametrize('private', [False, True])
def test_constraint_slot_metadata_recovers_the_identical_protocol_prefix(builder, private):
    contract = builder()
    context = z3.Context() if private else None
    relation = ProtocolIntervalRelation(contract, context=context)
    initial = ProtocolBoundary.initial(contract)
    slots = tuple(api.EventSlot(controls={pin: 0 for pin in contract.scheduled_controls()})
                  for _ in range(3))
    rows = _packed(contract, {e.name: [tuple(0 for _ in e.fields)] * 2
                              for e in contract.inputs()}, context)
    samples = tuple({pin: z3.Bool(f'sample_{i}_{pin}', ctx=context) if width == 1 else
                    z3.BitVec(f'sample_{i}_{pin}', width, ctx=context)
                    for pin, width in relation.observed_widths.items()} for i in range(3))
    actions = relation.symbolic_actions(3)
    resets = {clock.name: tuple(z3.Bool(f'reset_{clock.name}_{i}', ctx=context) for i in range(3))
              for clock in contract.clocks}
    enabled = tuple(z3.Bool(f'enabled_{i}', ctx=context) for i in range(3))
    sampled = tuple(z3.Bool(f'sampled_{i}', ctx=context) for i in range(3))
    lengths = {name: z3.Int(f'length_{name}', ctx=context) for name in rows}

    def encode(count):
        return relation.encode(initial, rows, slots[:count], samples[:count],
            actions=actions[:count], queue_lengths=lengths,
            reset_terms={name: terms[:count] for name, terms in resets.items()},
            enabled=enabled[:count], sampled=sampled[:count])

    full = encode(3)
    assert len(full.constraint_slots) == len(full.constraints)
    assert {-1, 0, 1, 2} == set(full.constraint_slots)
    assert full.constraint_slots[:3] == (0, 1, 2)
    for count in range(4):
        prefix = tuple(term for term, index in zip(full.constraints, full.constraint_slots, strict=True)
                       if index < count)
        # Metadata must select exactly the original shorter relation, with
        # the same queue capacity and free inputs, not merely some SAT model.
        def expand(roots, definitions):
            # Independently built prefixes own different fresh state names.
            # Substitute their total equations to compare the original public
            # input/sample relation, not arbitrary private model assignments.
            image = z3.And(*roots)
            for frame in reversed(definitions):
                if frame:
                    image = z3.substitute(image, *(tuple(term.children()) for term in frame))
            return image

        short = encode(count)
        solver = z3.Solver(ctx=context)
        solver.add(expand(prefix, full.step_definitions[:count]) !=
                   expand(short.constraints, short.step_definitions))
        assert solver.check() == z3.unsat


def test_all_symbolic_offer_ready_patterns_match_concrete_queue_and_adapter():
    from itertools import product

    contract, context = stream('ready-valid'), z3.Context()
    relation = ProtocolIntervalRelation(contract, context=context)
    domain, = relation.parts
    program = adapter_program_for(contract)
    rows = {e.name: [(7,), (11,)] for e in contract.inputs()}
    endpoint, = rows
    ready, = relation.scheduled_names
    # Empty/blocked placeholders do not restrict the explicitly symbolic actions.
    slots = [api.EventSlot(controls={pin:0 for pin in contract.scheduled_controls()})]*3
    actions = relation.symbolic_actions(len(slots))
    signals = {**{f.port:0 for e in contract.outputs() for f in e.fields},
               **{pin:1 for pin in relation.observed_widths}}
    enc = relation.encode(ProtocolBoundary.initial(contract), _packed(contract, rows, context),
        slots, [_terms(signals, relation.observed_widths, context)]*3, actions=actions)
    solver = z3.Solver(ctx=context)
    solver.add(*enc.constraints)
    # All 64 patterns, including no offers, stalls and resumption. Each query
    # checks every model, not one convenient solver completion.
    for pattern in product((False, True), repeat=6):
        concrete = [api.EventSlot(offers=(endpoint,) if pattern[2*i] else (),
            controls={**dict(slot.controls), ready:int(pattern[2*i+1])}) for i, slot in enumerate(slots)]
        stimulus = api.Stimulus(rows, concrete)
        state, cursors, differences = program.initial_state(contract), InputQueueState.initial(rows), []
        solver.push()
        for i, (slot, action) in enumerate(zip(concrete, actions, strict=True)):
            solver.add(action.offers[endpoint] == pattern[2*i], action.controls[ready] == pattern[2*i+1])
            prepared = cursors.prepare(stimulus, slot, releasable=rows,
                presentable={name for name in rows if program.presentation_allowed(
                    contract, state, contract.endpoint(name))})
            drive = program.drive(contract, state, slot, dict(prepared.presentations), dict(slot.controls))
            step = program.step(contract, state, slot, drive, signals)
            cursors = cursors.commit(prepared, dict(step.accepted_inputs))
            state = step.state
            differences.extend(_differences(enc.pointers[i+1], dict(cursors.accepted)))
            differences.extend(_differences(enc.released[i+1], dict(cursors.released)))
            differences.append(enc.adapters[i+1][domain] != relation.parts[domain][2].state_index(state))
            differences.extend(term != (name in step.emitted_outputs) for name, term in enc.emitted[i].items())
        assert solver.check() == z3.sat
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize('builder', [*BUILDERS, lambda: framed(nested())])
def test_protocol_successors_are_named_with_retained_total_definitions(builder):
    contract, context = builder(), z3.Context()
    relation = ProtocolIntervalRelation(contract, context=context)
    slots = (api.EventSlot(controls={pin: 0 for pin in contract.scheduled_controls()}),) * 12
    rows = _packed(contract, {e.name: [tuple(0 for _ in e.fields)] * 2
                              for e in contract.inputs()}, context)
    samples = tuple({pin: z3.Bool(f'boundary_sample_{i}_{pin}', ctx=context) if width == 1
                    else z3.BitVec(f'boundary_sample_{i}_{pin}', width, ctx=context)
                    for pin, width in relation.observed_widths.items()} for i in range(len(slots)))
    enc = relation.encode(ProtocolBoundary.initial(contract), rows, slots, samples,
                          actions=relation.symbolic_actions(len(slots)))
    assert len(enc.step_definitions) == len(slots)
    original = {term.get_id(): slot for term, slot in zip(enc.constraints, enc.constraint_slots, strict=True)}
    owners = set()
    for slot, frame in enumerate(enc.step_definitions):
        for equation in frame:
            assert original[equation.get_id()] == slot
            lhs, _ = equation.children()
            assert z3.is_const(lhs) and lhs.get_id() not in owners
            owners.add(lhs.get_id())
    # These are now shallow boundaries, not a new copy of the preceding
    # acceptance/adapter history for each later payload or source expression.
    for images in (enc.adapters, enc.pointers, enc.released):
        assert all(z3.is_const(term) for row in images for term in row.values())
    assert all(z3.is_const(term) for row in enc.framing for cells in row.values() for term in cells)


def test_symbolic_environment_cannot_override_reset_or_omit_endpoint():
    from behave.sim.rtl.protocol_relation import ProtocolActions

    contract = stream('ready-valid')
    relation = ProtocolIntervalRelation(contract)
    slot = api.EventSlot(reset=True, controls={pin:int(pin=='rst') for pin in contract.scheduled_controls()})
    action, = relation.symbolic_actions(1)
    samples = [_terms({pin:1 for pin in relation.observed_widths}, relation.observed_widths, None)]
    rows = _packed(contract, {e.name:[(1,)] for e in contract.inputs()}, None)
    encoded = relation.encode(ProtocolBoundary.initial(contract), rows, [slot], samples, actions=[action])
    solver = z3.Solver()
    solver.add(*encoded.constraints, *action.offers.values(), *action.controls.values())
    assert solver.check() == z3.sat
    solver.add(z3.Or(*(term != 0 for term in encoded.released[-1].values()),
                     z3.Not(encoded.drives[0]['rst'])))
    assert solver.check() == z3.unsat
    for invalid in (ProtocolActions({}, action.controls),
                    ProtocolActions(action.offers, {**action.controls, 'rst': z3.Bool('reset')})):
        with pytest.raises(ValueError, match='exactly the declared'):
            relation.encode(ProtocolBoundary.initial(contract), rows, [slot], samples, actions=[invalid])


def test_private_context_empty_collections_and_packed_enumerations():
    from behave.sim.rtl.physical_symbolic import valid_packed_pattern

    context = z3.Context()
    a = Z3Algebra(context)
    scalar = api.ValueType.enumeration(('a', 'b', 'c'))
    term = z3.BitVec('enum', 2, ctx=context)
    solver = z3.Solver(ctx=context)
    solver.add(a.land(), a.lnot(a.lor()), a.count(()) == 0,
               valid_packed_pattern(term, scalar), term == 3)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('length', [-1, 0, 1, 2])
def test_literal_queue_domain_constraints_are_typed_and_not_discarded(length):
    contract = stream('ready-valid')
    context = z3.Context()
    relation = ProtocolIntervalRelation(contract, context=context)
    rows = _packed(contract, {endpoint.name:[(1,)] for endpoint in contract.inputs()}, context)
    slot = api.EventSlot(controls={pin:0 for pin in contract.scheduled_controls()})
    samples = [_terms({pin:0 for pin in relation.observed_widths}, relation.observed_widths, context)]
    encoded = relation.encode(ProtocolBoundary.initial(contract), rows, (slot,), samples,
                              queue_lengths=dict.fromkeys(rows, length))
    assert all(z3.is_bool(term) and term.ctx == context for term in encoded.constraints)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints)
    assert solver.check() == (z3.sat if 0 <= length <= 1 else z3.unsat)


def test_retained_bus_request_cannot_be_mutated_after_partial_transfer():
    contract = axil()
    context = z3.Context()
    relation = ProtocolIntervalRelation(contract, context=context)
    domain, = relation.parts
    _, program, table = relation.parts[domain]
    rows = {'in': [(5, 7, 1)]}
    slot = api.EventSlot(offers=('in',), controls={'rst':0, 'bready':1})
    state = program.initial_state(contract)
    drive = program.drive(contract, state, slot, {'in':rows['in'][0]}, dict(slot.controls))
    sampled = {'awready':1, 'wready':0, 'bvalid':0, 'bresp':0}
    state = program.step(contract, state, slot, drive, sampled).state
    initial = ProtocolBoundary.initial(contract)
    from behave.sim.core.frozen import FrozenMap

    boundary = replace(initial, adapters={domain:state},
        queues=InputQueueState(FrozenMap({'in':0}), FrozenMap({'in':1})))
    cells = _packed(contract, rows, context)
    address = z3.BitVec('address', 32, ctx=context)
    cells['in'] = ((address, *cells['in'][0][1:]),)
    enc = relation.encode(boundary, cells, [api.EventSlot(controls={'rst':0,'bready':1})],
                          [_terms(sampled, table.observed_widths, context)])
    solver = z3.Solver(ctx=context)
    solver.add(*enc.constraints, address != 5)
    assert solver.check() == z3.unsat


def test_nested_frame_prefix_and_terminal_sample_do_not_advance_on_stall():
    contract = framed(nested())
    context = z3.Context()
    relation = ProtocolIntervalRelation(contract, context=context)
    initial = ProtocolBoundary.initial(contract)
    rows = {'in':[(5,1,0,1,0), (7,0,1,0,1)]}
    slots = [api.EventSlot(offers=('in',), controls={'rst':0,'out_ready':1}) for _ in range(4)]
    samples = [_terms({'ready':int(i != 1), 'out_valid':0}, relation.observed_widths, context)
               for i in range(4)]
    length = z3.Int('frame_length', ctx=context)
    enc = relation.encode(initial, _packed(contract, rows, context), slots, samples,
        enabled=[z3.BoolVal(i<3, ctx=context) for i in range(4)], queue_lengths={'in': length})
    solver = z3.Solver(ctx=context)
    solver.add(*enc.constraints)
    for count in (0, 1, 2):
        solver.push()
        solver.add(length == count)
        assert solver.check() == z3.sat  # A legal incomplete frame is in the domain.
        frame = (0, 1) if count == 1 else (0, 0)
        solver.add(z3.Or(enc.pointers[1]['in'] != min(1, count),
            enc.pointers[2]['in'] != min(1, count),
            enc.pointers[3]['in'] != count, enc.pointers[4]['in'] != count,
            *(term != value for term, value in zip(enc.framing[-1]['in'], frame, strict=True))))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize('prefix', [0, 1, 3, 4, 5])
@pytest.mark.parametrize('kind', ['ready-valid', 'start-done'])
def test_clock_projection_preserves_inactive_time_and_boundary_controls(prefix, kind):
    from behave.sim.core.frozen import FrozenMap
    from behave.sim.generation_trace import protocol_trace_steps

    from .test_protocol_timebase import _run

    contract, stimulus, trace, report, states = _run(kind, 1, reset_at=(3,))
    context = z3.Context()
    relation = ProtocolIntervalRelation(contract, context=context)
    initial = ProtocolBoundary.initial(contract)
    pointers, released = {'in':0}, {'in':0}
    controls = dict(initial.controls)
    replayed = {row.index: row for _domain, row in protocol_trace_steps(contract, stimulus, trace)}
    for index, record in enumerate(trace.records[:prefix]):
        if index in replayed:
            controls.update((name, replayed[index].drive.controls[name]) for name in controls)
        pointers['in'] += int(dict(record.accepted).get('in') is not None)
        released['in'] = int(index >= 0)  # this fixed stimulus offers once at zero
    boundary = replace(initial, adapters={'a':states[prefix-1]} if prefix else initial.adapters,
        queues=InputQueueState(FrozenMap(pointers), FrozenMap(released)), controls=controls)
    samples = [_terms(dict(record.outputs), relation.observed_widths, context)
               for record in trace.records[prefix:]]
    enc = relation.encode(boundary, _packed(contract, dict(stimulus.queues), context),
                          stimulus.slots[prefix:], samples, atomic=True)
    solver = z3.Solver(ctx=context)
    solver.add(*enc.constraints)
    assert solver.check() == z3.sat
    table = relation.parts['a'][2]
    differences = []
    previous_controls = dict(controls)
    for local, index in enumerate(range(prefix, len(trace.records))):
        differences.append(enc.adapters[local+1]['a'] != table.state_index(states[index]))
        if index in replayed:
            row = replayed[index]
            previous_controls.update((name, row.drive.controls[name]) for name in controls)
            before, cursors = row.state, row.queues
            expected_before = (table.initial_state if index == 0 else
                               table.state_index(states[index-1]))
            assert table.state_index(before) == expected_before
            assert cursors.accepted['in'] == sum(dict(record.accepted).get('in') is not None
                                                for record in trace.records[:index])
        differences.extend(term != bool(previous_controls[name])
                           for name, term in enc.drives[local].items() if name in controls)
        differences.extend(term != any(v.slot == index and v.goal == goal for v in report.violations)
                           for goal, term in enc.hits[local].items())
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


def test_optional_replay_boundaries_preserve_actual_held_data_and_release_cursors():
    from behave.sim.core.driver import Driver

    contract = axil()
    stimulus = api.Stimulus({'in':[(5,7,1),(9,11,3)]}, [
        api.EventSlot(offers=('in',) if i < 2 else (), controls={'rst':0,'bready':1})
        for i in range(4)])
    driver = Driver(contract, stimulus)
    for i in range(4):
        driver.observe(i, driver.presentations(i), {
            'awready':int(i == 0), 'wready':int(i == 2),
            'bvalid':int(i == 3), 'bresp':0})
    program = adapter_program_for(contract)
    trace = driver.trace()
    normal = program.replay(contract, stimulus, trace)
    retained = program.replay(contract, stimulus, trace, retain_boundaries=True)
    assert all(row.boundary is None for row in normal)
    held, cursors = retained[2].boundary.state, retained[2].boundary.queues
    assert held['adapter.request'] == (5,7,1)
    assert cursors.accepted['in'] == 0 and cursors.released['in'] == 2
    assert all(dict(a.presented_inputs) == dict(b.presented_inputs)
               for a,b in zip(normal, retained, strict=True))
