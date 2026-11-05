"""A local memory query continues an actual prefix, not the cold-start image."""

from types import SimpleNamespace

import pytest
import z3

from behave.sim.core.memory import (
    MemoryEnvironment,
    MemoryEnvironmentState,
    MemoryInterface,
    MemoryPort,
    MemoryRun,
    advance_memory_environment_with_writes,
    drive_memory_environment,
    memory_artifact_signal_set,
    memory_port_prefix,
    memory_port_signal_set,
)
from behave.sim.core.memory_trace import MemoryTraceReader, memory_successor_record
from behave.sim.rtl.memory_exact import MemoryPinBindings, SymbolicMemoryProduct

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('private', [False, True])
def test_empty_memory_product_needs_no_stimulus_environment(private):
    context = z3.Context() if private else z3.main_ctx()
    state = MemoryEnvironmentState.initial((), None)
    assert state.as_dict() == {'memories': []}

    def unused(*args):
        raise AssertionError('an empty memory product has no physical pins')

    product = SymbolicMemoryProduct((), None, MemoryPinBindings(unused, unused), 3,
        tag='empty', initial_state=state, slot_offset=7, context=context)
    solver = z3.Solver(ctx=context)
    solver.add(*product.constraints, *product.validity_constraints)
    assert solver.check() == z3.sat
    assert all(product.state_image(solver.model(), i) == state.as_dict() for i in range(4))
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('r')])
    with pytest.raises(TypeError, match='MemoryEnvironment'):
        MemoryEnvironmentState.initial((memory,), None)
    with pytest.raises(TypeError, match='MemoryEnvironment'):
        SymbolicMemoryProduct((memory,), None, MemoryPinBindings(unused, unused), 3,
            tag='missing', context=context)


def _product(interfaces, environment, rows, *, initial, start, active, resets, context):
    outputs, inputs, widths = {}, {}, {}
    for memory in interfaces:
        artifact = memory_artifact_signal_set(memory)
        for port in memory.ports:
            for pin in memory_port_signal_set(memory.name, port.id, port.kind):
                (outputs if pin in artifact else inputs)[pin] = pin
                widths[pin] = (memory.addr_width if pin.endswith("_addr") else
                               memory.data_width if pin.endswith(("_wdata", "_rdata")) else 1)
    # Three physical phases share one pre-slot memory drive. The first is the
    # request sample; extra clock phases must not consume additional responses.
    terms = {(name, slot): (value if z3.is_expr(value) else
              z3.BitVecVal(value, widths[name], ctx=context))
             for slot, row in enumerate(rows) for name, value in row.items()}
    terms.update({(name, slot): z3.BitVec(f"{name}_{slot}", widths[name], ctx=context)
                  for name in inputs for slot in range(len(rows))})
    frames = SimpleNamespace(term=lambda name, phase: terms[name, phase // 3])
    pins = MemoryPinBindings.from_frames(frames,
        SimpleNamespace(out_vars_current=outputs, mem_inp_vars=inputs), len(rows),
        frame_cycles=tuple(tuple(range(3*i, 3*i+3)) for i in range(len(rows))))
    product = SymbolicMemoryProduct(interfaces, environment, pins, len(rows), tag="continued",
        active_domains=active,
        reset_terms={memory.clock: tuple(memory.clock in reset for reset in resets)
                     for memory in interfaces},
        initial_state=initial, slot_offset=start, context=context)
    return product, terms


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('latency', [1, 3])
def test_growing_product_never_samples_future_slots(private, latency):
    from behave.sim.core.construction_checkpoint import finish_construction

    context = z3.Context() if private else z3.main_ctx()
    memory = MemoryInterface('ram', 2, 8, 'clk', [MemoryPort('rw')])
    environment = MemoryEnvironment([MemoryRun('ram', initial={1: 9}, read_latency=latency,
                                               ready={None: [1] * 8})])
    initial = MemoryEnvironmentState.initial((memory,), environment)
    rows = [{'ram_req_valid': 1, 'ram_req_addr': 1, 'ram_req_we': int(i == 1),
             'ram_req_wdata': 201} for i in range(6)]
    active, resets = [('clk',)] * 6, [(), (), (), ('clk',), (), ()]
    whole, _ = _product((memory,), environment, rows, initial=initial, start=2,
                       active=active, resets=resets, context=context)
    current, calls = [-1], []

    def sample(pin, index):
        assert index == current[0]
        calls.append(index)
        return whole.pins.sample(pin, index)

    def drive(pin, index):
        assert index == current[0]
        return whole.pins.drive(pin, index)

    growing = finish_construction(SymbolicMemoryProduct.begin_steps((memory,), environment,
        MemoryPinBindings(sample, drive), len(rows), tag='growing', initial_state=initial,
        slot_offset=2, context=context, active_domains=active,
        reset_terms={'clk': tuple(bool(row) for row in resets)}))
    assert growing.built_slots == 0 and not calls
    solver = z3.Solver(ctx=context)
    solver.add(*growing.constraints, *whole.constraints)
    prior = tuple(growing.constraints)
    for index in range(len(rows)):
        current[0] = index
        construction = growing.advance_steps()
        next(construction)  # Suspend before the slot's memory transition.
        assert growing.built_slots == index
        assert len(growing.constraints) == len(prior)
        finish_construction(construction)
        assert growing.built_slots == index + 1
        assert set(calls) == set(range(index + 1))
        assert all(a is b for a, b in zip(prior, growing.constraints))
        solver.add(*growing.constraints[len(prior):])
        prior = tuple(growing.constraints)
        assert solver.check() == z3.sat
        model = solver.model()
        expected = whole.state_image(model, index + 1)
        assert growing.state_image(model, index + 1) == expected
        solver.push()
        solver.add(z3.Not(z3.And(*growing.state_constraints(index + 1, expected))))
        assert solver.check() == z3.unsat
        solver.pop()
        if index + 1 < len(rows):
            with pytest.raises(ValueError, match='completed'):
                growing.state_image(model, index + 2)
    with pytest.raises(ValueError, match='idle, unfinished'):
        finish_construction(growing.advance_steps())


def test_discarded_memory_advance_cannot_be_restarted_or_published():
    from behave.sim.core.construction_checkpoint import finish_construction

    memory = MemoryInterface('ram', 2, 8, 'clk', [MemoryPort('r')])
    environment = MemoryEnvironment([MemoryRun('ram', ready={None: [1]})])

    def unused(*args):
        raise AssertionError('the discarded step must not sample anything')

    growing = finish_construction(SymbolicMemoryProduct.begin_steps((memory,), environment,
        MemoryPinBindings(unused, unused), 1, tag='discarded'))
    step = growing.advance_steps()
    next(step)
    with pytest.raises(ValueError, match='idle, unfinished'):
        finish_construction(growing.advance_steps())
    step.close()
    with pytest.raises(ValueError, match='idle, unfinished'):
        finish_construction(growing.advance_steps())
    with pytest.raises(ValueError, match='completed'):
        growing.state_constraints(1, growing.initial_state.as_dict())
    assert growing.built_slots == 0


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('address_width', [2, 32])
def test_sparse_cells_are_quantifier_free_and_incrementally_queryable(private, address_width):
    from behave.sim.symbolic.memory_terms import memory_junk_term

    context = z3.Context() if private else z3.main_ctx()
    memory = MemoryInterface('ram', address_width, 8, 'clk', [MemoryPort('w')])
    environment = MemoryEnvironment([MemoryRun('ram', initial={1: 0}, ready={None: [1]})])
    initial = MemoryEnvironmentState.initial((memory,), environment)
    address = z3.BitVec('write_address', address_width, ctx=context)
    value = z3.BitVec('write_value', 8, ctx=context)
    product, _ = _product((memory,), environment, [
        {'ram_req_valid': 1, 'ram_req_addr': address, 'ram_req_wdata': value}],
        initial=initial, start=0, active=[('clk',)], resets=[()], context=context)
    # No whole-array Lambda equality; address width does not enumerate RAM.
    stack, seen = list(product.constraints), set()
    while stack:
        term = stack.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        assert not z3.is_quantifier(term)
        stack.extend(term.children())
    solver = z3.Solver(ctx=context)
    solver.add(*product.constraints, *product.validity_constraints)
    for written in (201, 202, 0, 255):
        solver.push()
        solver.add(address == 2, value == written)
        assert solver.check() == z3.sat
        model = solver.model()
        assert model.eval(product.read_cell(memory, 1, address)).as_long() == written
        assert dict(product.state_image(model, 1)['memories'][0][1]['cells']) == {1: 0, 2: written}
        solver.pop()
    # Universal address check against the original total-memory semantics,
    # including explicit zero, unwritten junk and writes equal to that junk.
    probe = z3.BitVec('probe_address', address_width, ctx=context)
    before = z3.If(probe == 1, z3.BitVecVal(0, 8, ctx=context),
                   memory_junk_term('ram', probe, 8))
    after = z3.If(probe == address, value, before)
    solver.add(z3.Or(product.read_cell(memory, 0, probe) != before,
                     product.read_cell(memory, 1, probe) != after))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("kinds", [("r",), ("w",), ("rw",), ("r", "w"), ("rw", "rw")])
@pytest.mark.parametrize("latency", [1, 2, 4])
@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("start", [2, 5])
def test_recorded_prefix_continuation_matches_every_drive_and_successor(kinds, latency, private, start):
    memory = MemoryInterface("ram", 4, 8, "clk", [MemoryPort(kind, str(i))
                                                  for i, kind in enumerate(kinds)])
    environment = MemoryEnvironment([MemoryRun("ram", initial={3: 77}, read_latency=latency,
        ready={str(i): [int((slot+i) % 3 != 0) for slot in range(10)]
               for i in range(len(kinds))})])
    rows = []
    for slot in range(10):
        row = {}
        for i, port in enumerate(memory.ports):
            prefix = memory_port_prefix(memory.name, port.id)
            row.update({prefix+"_req_valid": 1, prefix+"_req_addr": (slot+i) % 7})
            if port.writes:
                row[prefix+"_req_wdata"] = slot*3
            if port.kind == "rw":
                row[prefix+"_req_we"] = int(slot % 2 == 1)
        rows.append(row)
    active = [() if slot in (4, 6) else ("clk",) for slot in range(10)]
    resets = [("clk",) if slot == 6 else () for slot in range(10)]
    state = MemoryEnvironmentState.initial((memory,), environment)
    reader, states, drives = MemoryTraceReader(state), [state], []
    for slot, row in enumerate(rows):
        drives.append(drive_memory_environment((memory,), environment, state, slot,
                                               reset_domains=resets[slot]))
        assert reader.drive((memory,), environment, slot, reset_domains=resets[slot]) == drives[-1]
        state, writes = advance_memory_environment_with_writes((memory,), environment,
            state, slot, row, active_domains=active[slot], reset_domains=resets[slot])
        reader.consume(memory_successor_record(state, writes))
        assert reader.snapshot() == state
        states.append(reader.snapshot())
    context = z3.Context() if private else z3.main_ctx()
    product, terms = _product((memory,), environment, rows[start:], initial=states[start],
        start=start, active=active[start:], resets=resets[start:], context=context)
    solver = z3.Solver(ctx=context)
    solver.add(*product.constraints, *product.validity_constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    assert [product.state_image(model, i) for i in range(len(rows)-start+1)] == [
        state.as_dict() for state in states[start:]]
    for slot, driven in enumerate(drives[start:]):
        assert {name: model.eval(terms[name, slot]).as_long() for name in driven} == driven
    # Fixing the boundary is not a free-state relaxation.
    wrong = states[start].as_dict()
    image = wrong["memories"][0][1]
    image["cells"][0][1] ^= 1
    address, value = image["cells"][0]
    for written in image["writes"]:
        if written[0] == address:
            written[1] = value
    solver.add(*product.state_constraints(0, wrong))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("policy", [False, True])
def test_changed_read_request_changes_future_response_not_the_recorded_seed(policy):
    memory = MemoryInterface("ram", 4, 8, "clk", [MemoryPort("rw")])
    run = (MemoryRun.from_policy(memory, None, initial={1: 9, 2: 37}, read_latency=2)
           if policy else MemoryRun("ram", initial={1: 9, 2: 37}, read_latency=2,
                                    ready={None: [1]*8}))
    environment = MemoryEnvironment([run])
    state = MemoryEnvironmentState.initial((memory,), environment)
    for slot in range(2):
        state, _ = advance_memory_environment_with_writes((memory,), environment, state, slot,
            {"ram_req_valid": 1, "ram_req_addr": 1, "ram_req_we": int(slot == 0), "ram_req_wdata": 11})
    assert dict(state.state("ram").cells)[1] == 11  # different from original image
    assert state.state("ram").pending[0][1]  # an earlier read is still pending
    ctx = z3.Context()
    address = z3.BitVec("changed_request", 4, ctx=ctx)
    rows = [{"ram_req_valid": int(i == 0), "ram_req_addr": address,
             "ram_req_we": 0, "ram_req_wdata": 0} for i in range(4)]
    product, terms = _product((memory,), environment, rows, initial=state, start=2,
                              active=[("clk",)]*4, resets=[()]*4, context=ctx)
    solver = z3.Solver(ctx=ctx)
    solver.add(*product.constraints, z3.Or(address == 1, address == 2),
               terms["ram_resp_valid", 2] == 1, terms["ram_resp_rdata", 2] == 37)
    assert solver.check() == z3.sat
    model = solver.model()
    assert model.eval(address).as_long() == 2
    concrete = state
    for i, row in enumerate(rows):
        got = {key: model.eval(value).as_long() if z3.is_expr(value) else value
               for key, value in row.items()}
        concrete, _ = advance_memory_environment_with_writes((memory,), environment,
                                                             concrete, 2+i, got)
        assert product.state_image(model, i+1) == concrete.as_dict()
    assert dict(concrete.state("ram").writes) == {1: 11}


def test_private_context_retains_simultaneous_write_conflict():
    memory = MemoryInterface("ram", 4, 8, "clk", [MemoryPort("w", p) for p in ("a", "b")])
    environment = MemoryEnvironment([MemoryRun("ram", ready={"a": [1]*3, "b": [1]*3})])
    ctx = z3.Context()
    row = {"ram__"+port+suffix: value for port in ("a", "b") for suffix, value in
           (("_req_valid", 1), ("_req_addr", 3), ("_req_wdata", 7))}
    product, _ = _product((memory,), environment, [row],
        initial=MemoryEnvironmentState.initial((memory,), environment), start=2,
        active=[("clk",)], resets=[()], context=ctx)
    solver = z3.Solver(ctx=ctx)
    solver.add(*product.constraints, product.conflict)
    assert solver.check() == z3.sat
    solver.add(*product.validity_constraints)
    assert solver.check() == z3.unsat


def test_two_domain_continuation_preserves_independent_memories():
    memories = tuple(MemoryInterface(name, 3, 8, domain, [MemoryPort("rw")])
                     for name, domain in (("fast_ram", "fast"), ("slow_ram", "slow")))
    environment = MemoryEnvironment([MemoryRun(memory.name, initial={1: 42}, read_latency=2,
        ready={None: [1, 1, 0, 1, 1, 1]}) for memory in memories])
    rows = [{memory.name+suffix: value for memory in memories for suffix, value in
             (("_req_valid", 1), ("_req_addr", 1), ("_req_we", int(i % 2 == 0)),
              ("_req_wdata", i+10))} for i in range(6)]
    active = [("fast", "slow"), ("fast", "slow"), ("fast",), ("slow",), ("fast", "slow"), ("slow",)]
    resets = [(), (), ("slow",), (), (), ()]  # reset without a slow tick
    state = MemoryEnvironmentState.initial(memories, environment)
    states, drives = [state], []
    for slot, row in enumerate(rows):
        drives.append(drive_memory_environment(memories, environment, state, slot,
                                               reset_domains=resets[slot]))
        state, _ = advance_memory_environment_with_writes(memories, environment, state, slot, row,
                                                         active_domains=active[slot], reset_domains=resets[slot])
        states.append(state)
    ctx = z3.Context()
    product, terms = _product(memories, environment, rows[2:], initial=states[2], start=2,
                              active=active[2:], resets=resets[2:], context=ctx)
    solver = z3.Solver(ctx=ctx)
    solver.add(*product.constraints, *product.validity_constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    assert [product.state_image(model, i) for i in range(5)] == [s.as_dict() for s in states[2:]]
    for slot, driven in enumerate(drives[2:]):
        assert {pin: model.eval(terms[pin, slot]).as_long() for pin in driven} == driven
