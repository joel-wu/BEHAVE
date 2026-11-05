"""The task-owned finite external-memory product, independent of any backend."""

import itertools
import json

import pytest

from behave.sim.core.memory import (
    EXTERNAL_MEMORY_PROTOCOL,
    MEMORY_ENVIRONMENT_VERSION,
    MEMORY_FULL_STATE_IMAGE,
    MEMORY_FULL_STATE_MAX_CELLS,
    MemoryEnvironment,
    MemoryEnvironmentState,
    MemoryInterface,
    MemoryPort,
    MemoryRealization,
    MemoryRun,
    advance_memory_environment,
    advance_memory_state,
    drive_memory_environment,
    drive_memory_state,
    materialize_memory_environment_state,
    memory_junk_value,
)
from behave.sim.core.refusal import Refused
from behave.sim.core.schema import Authority, TransactionSchema
from behave.sim.core.schema import Endpoint as SchemaEndpoint


def interface(*ports, width=2, data=3):
    return MemoryInterface(
        "ram", width, data, "clk",
        [MemoryPort(kind, port) for port, kind in ports])


def run(memory, horizon, *, initial=(), latency=1, ready=None):
    if ready is None:
        ready = {port.id: [1] * horizon for port in memory.ports}
    return MemoryRun(
        memory.name, initial=initial, read_latency=latency, ready=ready)


def request(memory, port, *, valid=1, address=0, write=False, value=0):
    prefix = memory.name if port is None else f"{memory.name}__{port}"
    out = {prefix + "_req_valid": valid, prefix + "_req_addr": address}
    declared = memory.port(port)
    if declared.kind == "rw":
        out[prefix + "_req_we"] = int(write)
    if declared.writes:
        out[prefix + "_req_wdata"] = value
    return out


def test_task_and_run_images_are_strict_canonical_and_digest_bound():
    memory = interface((None, "rw"))
    policy = run(memory, 2, initial={1: 6}, ready={None: [1, 0]})
    environment = MemoryEnvironment([policy])

    assert MemoryInterface.from_dict(memory.as_dict()).as_dict() == memory.as_dict()
    assert MemoryEnvironment.from_dict(environment.as_dict()).digest == environment.digest
    tampered = environment.as_dict()
    tampered["runs"][0]["ready"][0][1][1] = 1
    assert MemoryEnvironment.from_dict(tampered).digest != environment.digest
    with pytest.raises(TypeError):
        MemoryEnvironment.from_dict({**environment.as_dict(), "extra": 1})


def test_environment_requires_one_exact_schedule_for_each_declared_port():
    memory = interface(("a", "r"), ("b", "w"))
    missing = MemoryEnvironment([
        MemoryRun("ram", ready={"a": [1, 1]}, read_latency=1)])
    assert missing.faults_against([memory], 2)
    short = MemoryEnvironment([
        MemoryRun("ram", ready={"a": [1], "b": [1]}, read_latency=1)])
    assert "1 ready bits" in " ".join(short.faults_against([memory], 2))
    good = MemoryEnvironment([run(memory, 2)])
    assert good.faults_against([memory], 2) == ()


def test_behavior_reference_memory_uses_the_same_initial_and_junk_values():
    from behave.sim.behavior.reference_memory import ReferenceMemory

    memory = interface((None, "rw"), width=3, data=5)
    reference = ReferenceMemory(
        memory, run(memory, 2, initial={2: 7}))

    assert reference[2] == 7
    assert reference[3] == memory_junk_value("ram", 3, 5)
    reference[3] = 0x7f
    assert reference[3] == 0x1f
    assert reference.writes == {3: 0x1f}
    assert reference[1:4] == [reference[1], 7, 0x1f]


def test_read_first_is_independent_of_port_order():
    for order in itertools.permutations((("r", "r"), ("w", "w"))):
        memory = interface(*order)
        policy = run(memory, 2, initial={1: 2})
        state = MemoryEnvironmentState.initial(
            [memory], MemoryEnvironment([policy])).state("ram")
        outputs = {}
        outputs.update(request(memory, "r", address=1))
        outputs.update(request(memory, "w", address=1, write=True, value=7))
        state = advance_memory_state(memory, policy, state, 0, outputs)
        assert dict(state.cells)[1] == 7
        assert dict(state.responses)["r"] == (1, 2)
        driven = drive_memory_state(memory, policy, state, 1)
        assert driven["ram__r_resp_valid"] == 1
        assert driven["ram__r_resp_rdata"] == 2


@pytest.mark.solver
@pytest.mark.parametrize("reset_slot", [None, 1, 2])
@pytest.mark.parametrize("clock_on_reset", [False, True])
def test_symbolic_memory_trajectory_matches_the_shared_concrete_transition(reset_slot, clock_on_reset):
    import z3

    from behave.sim.rtl.memory_exact import MemoryPinBindings, SymbolicMemoryProduct

    memory = interface((None, "rw"))
    environment = MemoryEnvironment([
        run(memory, 3, initial={1: 2}, latency=2)])
    requests = (
        request(memory, None, address=1, write=False),
        request(memory, None, address=1, write=True, value=7),
        request(memory, None, valid=0, address=0, write=False),
    )

    class Explorer:
        out_vars_current = {
            name: name for name in (
                "ram_req_valid", "ram_req_addr", "ram_req_we",
                "ram_req_wdata")}
        mem_inp_vars = {
            name: name for name in (
                "ram_req_ready", "ram_resp_valid", "ram_resp_rdata")}
        ctl_vars = {"rst": "rst"}

    class Frames:
        def __init__(self):
            self.terms = {}
            for slot, row in enumerate(requests):
                self.terms[("rst", slot)] = z3.BoolVal(slot == reset_slot)
                self.terms[("ram_req_valid", slot)] = z3.BoolVal(
                    bool(row["ram_req_valid"]))
                self.terms[("ram_req_addr", slot)] = z3.BitVecVal(
                    row["ram_req_addr"], 2)
                self.terms[("ram_req_we", slot)] = z3.BoolVal(
                    bool(row["ram_req_we"]))
                self.terms[("ram_req_wdata", slot)] = z3.BitVecVal(
                    row["ram_req_wdata"], 3)
                self.terms[("ram_req_ready", slot)] = z3.Bool(
                    f"ready_{slot}")
                self.terms[("ram_resp_valid", slot)] = z3.Bool(
                    f"response_valid_{slot}")
                self.terms[("ram_resp_rdata", slot)] = z3.BitVec(
                    f"response_data_{slot}", 3)

        def term(self, original, slot):
            return self.terms[(original, slot)]

    active_domains = [() if slot == reset_slot and not clock_on_reset else (memory.clock,)
                      for slot in range(3)]
    product = SymbolicMemoryProduct(
        [memory], environment, MemoryPinBindings.from_frames(Frames(), Explorer(), 3), 3, tag="parity",
        active_domains=active_domains,
        reset_terms={memory.clock: tuple(slot == reset_slot for slot in range(3))})
    solver = z3.Solver()
    solver.add(*product.constraints)
    assert solver.check() == z3.sat
    model = solver.model()

    # Reset cancels a pending or already visible response, never the stored data.
    assert z3.is_true(model.eval(product.pins.drive("ram_resp_valid", 2)[0])) == (reset_slot is None)
    assert model.eval(product.pins.drive("ram_resp_rdata", 2)[0]).as_long() == (2 if reset_slot is None else 0)

    concrete = MemoryEnvironmentState.initial([memory], environment)
    expected = [concrete.as_dict()]
    for slot, row in enumerate(requests):
        concrete = advance_memory_environment(
            [memory], environment, concrete, slot, row,
            active_domains=active_domains[slot],
            reset_domains=(memory.clock,) if slot == reset_slot else ())
        expected.append(concrete.as_dict())
    assert [product.state_image(model, boundary) for boundary in range(4)] \
        == expected
    assert dict(concrete.state("ram").cells)[1] == (2 if reset_slot == 1 else 7)


def test_ready_is_the_second_half_of_request_acceptance():
    memory = interface((None, "rw"))
    policy = run(memory, 2, ready={None: [0, 1]})
    state = MemoryEnvironmentState.initial(
        [memory], MemoryEnvironment([policy])).state("ram")
    write = request(memory, None, address=2, write=True, value=5)
    refused = advance_memory_state(memory, policy, state, 0, write)
    assert dict(refused.cells) == {}
    accepted = advance_memory_state(memory, policy, refused, 1, write)
    assert dict(accepted.cells) == {2: 5}


def test_pending_reads_are_ordered_and_survive_request_deassertion():
    memory = interface((None, "r"))
    policy = run(memory, 5, initial={0: 3, 1: 4}, latency=3)
    state = MemoryEnvironmentState.initial(
        [memory], MemoryEnvironment([policy])).state("ram")
    state = advance_memory_state(
        memory, policy, state, 0, request(memory, None, address=0))
    state = advance_memory_state(
        memory, policy, state, 1, request(memory, None, address=1))
    assert dict(state.responses)[None] is None
    state = advance_memory_state(
        memory, policy, state, 2, request(memory, None, valid=0))
    assert dict(state.responses)[None] == (0, 3)
    state = advance_memory_state(
        memory, policy, state, 3, request(memory, None, valid=0))
    assert dict(state.responses)[None] == (1, 4)


def test_unwritten_cells_use_one_deterministic_total_function():
    memory = interface((None, "r"))
    policy = run(memory, 2)
    state = MemoryEnvironmentState.initial(
        [memory], MemoryEnvironment([policy])).state("ram")
    state = advance_memory_state(
        memory, policy, state, 0, request(memory, None, address=3))
    assert dict(state.responses)[None] == (
        3, memory_junk_value("ram", 3, memory.data_width))


def test_counter_prf_vectors_pin_junk_and_policy_expansion():
    memory = MemoryInterface("ram", 2, 8, "clk", [MemoryPort("r")])
    policy = MemoryRun.from_policy(memory, 8, stall_rate=0.5, seed=11)
    assert memory_junk_value("ram", 3, 8) == 28
    assert dict(policy.ready)[None] == (0, 1, 0, 1, 1, 1, 0, 0)


def test_state_round_trip_carries_cells_queues_responses_and_write_set():
    memory = interface(("r", "r"), ("w", "w"))
    policy = run(memory, 3, latency=2)
    environment = MemoryEnvironment([policy])
    state = MemoryEnvironmentState.initial([memory], environment)
    outputs = {}
    outputs.update(request(memory, "r", address=1))
    outputs.update(request(memory, "w", address=2, value=6, write=True))
    state = advance_memory_environment(
        [memory], environment, state, 0, outputs, active_domains={"clk"})
    image = state.state("ram").as_dict()
    from behave.sim.core.memory import MemoryState

    assert MemoryState.from_dict(image).as_dict() == image
    assert dict(state.state("ram").writes) == {2: 6}
    product_image = state.as_dict()
    assert MemoryEnvironmentState.from_dict(product_image).digest == state.digest


def test_state_images_fail_closed_against_the_task_and_run_carriers():
    from behave.sim.core.memory import MemoryState

    memory = interface((None, "r"), width=2, data=3)
    policy = run(memory, 3, latency=2)
    bad = MemoryEnvironmentState({
        "ram": MemoryState(
            cells={4: 1},
            pending={None: [(0, 1, 2)]},
            responses={None: (0, 8)},
            writes=()),
    })
    faults = bad.faults_against((memory,), MemoryEnvironment([policy]))
    assert any("out-of-range address" in fault or "address 4" in fault
               for fault in faults)
    assert any("latency 2" in fault for fault in faults)
    assert any("out-of-range value" in fault for fault in faults)
    with pytest.raises(Refused, match="memory-state-outside-interface"):
        materialize_memory_environment_state(
            (memory,), bad, MemoryEnvironment([policy]))


def test_ready_lookup_is_a_typed_finite_boundary():
    memory = interface((None, "r"))
    policy = run(memory, 1)
    with pytest.raises(Refused, match="memory-ready-schedule-exhausted"):
        policy.ready_at(None, 1)
    with pytest.raises(Refused, match="memory-ready-slot-invalid"):
        policy.ready_at(None, -1)
    with pytest.raises(Refused, match="memory-ready-port-missing"):
        policy.ready_at("other", 0)


@pytest.mark.solver
def test_symbolic_product_is_the_same_read_first_one_slot_response_semantics():
    import z3

    from behave.sim.rtl.memory_exact import MemoryPinBindings, SymbolicMemoryProduct

    memory = MemoryInterface(
        "ram", 2, 4, "clk",
        [MemoryPort("r", "r"), MemoryPort("w", "w")])
    environment = MemoryEnvironment([MemoryRun(
        "ram", initial={1: 5}, read_latency=1,
        ready={"r": [1, 1], "w": [1, 1]})])

    class Explorer:
        out_vars_current = {}
        mem_inp_vars = {}

    class Frames:
        terms = {}

        def term(self, original, slot):
            return self.terms[original, slot]

    frames = Frames()
    for port in memory.ports:
        prefix = f"ram__{port.id}"
        outputs = {
            prefix + "_req_valid": 1,
            prefix + "_req_addr": 2,
            **({prefix + "_req_wdata": 4} if port.writes else {}),
        }
        for name, width in outputs.items():
            Explorer.out_vars_current[name] = name
            for slot in range(2):
                frames.terms[name, slot] = (
                    z3.Bool(f"{name}_{slot}") if width == 1 else
                    z3.BitVec(f"{name}_{slot}", width))
        inputs = {prefix + "_req_ready": 1}
        if port.reads:
            inputs.update({prefix + "_resp_valid": 1,
                           prefix + "_resp_rdata": 4})
        for name, width in inputs.items():
            Explorer.mem_inp_vars[name] = name
            for slot in range(2):
                frames.terms[name, slot] = (
                    z3.Bool(f"{name}_{slot}") if width == 1 else
                    z3.BitVec(f"{name}_{slot}", width))

    product = SymbolicMemoryProduct(
        (memory,), environment, MemoryPinBindings.from_frames(frames, Explorer(), 2), 2, tag="tiny")
    solver = z3.Solver()
    solver.add(*product.constraints)
    for port in memory.ports:
        prefix = f"ram__{port.id}"
        solver.add(frames.terms[prefix + "_req_valid", 0])
        solver.add(frames.terms[prefix + "_req_addr", 0] == 1)
        solver.add(z3.Not(frames.terms[prefix + "_req_valid", 1]))
    solver.add(frames.terms["ram__w_req_wdata", 0] == 9)
    assert solver.check() == z3.sat
    model = solver.model()

    assert not z3.is_true(model.eval(
        frames.terms["ram__r_resp_valid", 0], model_completion=True))
    assert z3.is_true(model.eval(
        frames.terms["ram__r_resp_valid", 1], model_completion=True))
    assert model.eval(
        frames.terms["ram__r_resp_rdata", 1],
        model_completion=True).as_long() == 5
    boundary = product.state_image(model, 1)
    state = MemoryEnvironmentState.from_dict(boundary).state("ram")
    assert dict(state.cells)[1] == 9
    assert dict(state.responses)["r"] == (1, 5)

    check = z3.Solver()
    check.add(*product.constraints, *product.state_constraints(1, boundary))
    assert check.check() == z3.sat


def test_simultaneous_writes_have_no_silent_iteration_order_priority():
    memory = interface(("a", "w"), ("b", "w"))
    policy = run(memory, 1)
    state = MemoryEnvironmentState.initial(
        [memory], MemoryEnvironment([policy])).state("ram")
    outputs = {}
    outputs.update(request(memory, "a", address=2, value=3, write=True))
    outputs.update(request(memory, "b", address=2, value=4, write=True))
    with pytest.raises(Refused, match="memory-simultaneous-write-conflict"):
        advance_memory_state(memory, policy, state, 0, outputs)


@pytest.mark.solver
def test_symbolic_multiwrite_matches_the_protocol_without_port_priority():
    """Distinct writes commit together; an undeclared same-address winner does not."""

    import z3

    from behave.sim.rtl.memory_exact import MemoryPinBindings, SymbolicMemoryProduct

    memory = interface(("a", "w"), ("b", "w"))
    environment = MemoryEnvironment([run(memory, 1)])

    class Explorer:
        out_vars_current = {}
        mem_inp_vars = {}

    class Frames:
        terms = {}

        def term(self, original, slot):
            return self.terms[original, slot]

    frames = Frames()
    for port in memory.ports:
        prefix = f"ram__{port.id}"
        for name, width in {
                prefix + "_req_valid": 1,
                prefix + "_req_addr": memory.addr_width,
                prefix + "_req_wdata": memory.data_width,
        }.items():
            Explorer.out_vars_current[name] = name
            frames.terms[name, 0] = (
                z3.Bool(name) if width == 1 else z3.BitVec(name, width))
        ready = prefix + "_req_ready"
        Explorer.mem_inp_vars[ready] = ready
        frames.terms[ready, 0] = z3.Bool(ready)

    product = SymbolicMemoryProduct(
        (memory,), environment, MemoryPinBindings.from_frames(frames, Explorer(), 1), 1, tag="multiwrite")

    def solve(left_address, right_address):
        solver = z3.Solver()
        # Providers add the named protocol-validity relation beside the mechanical
        # state equations so they can report the collision precisely.
        solver.add(*product.constraints, *product.validity_constraints)
        for port, address, value in (
                ("a", left_address, 3), ("b", right_address, 5)):
            prefix = f"ram__{port}"
            solver.add(frames.terms[prefix + "_req_valid", 0])
            solver.add(
                frames.terms[prefix + "_req_addr", 0] == address,
                frames.terms[prefix + "_req_wdata", 0] == value,
            )
        return solver

    distinct = solve(1, 2)
    assert distinct.check() == z3.sat
    state = MemoryEnvironmentState.from_dict(
        product.state_image(distinct.model(), 1)).state("ram")
    assert dict(state.cells)[1] == 3
    assert dict(state.cells)[2] == 5

    assert solve(1, 1).check() == z3.unsat


def test_memory_semantic_versions_are_pinned_at_every_public_boundary():
    from behave.sim.core.contract import TASK_CONTRACT_VERSION
    from behave.sim.rtl.memory_exact import EXACT_MEMORY_PRODUCT

    # The task wire version already changed when ``memories`` became a strict public
    # field.  The remaining identities distinguish the run record, the finite full-
    # state representation and each exact provider that now consumes that product.
    assert TASK_CONTRACT_VERSION == "task-contract/17"
    assert EXTERNAL_MEMORY_PROTOCOL.endswith("/index-mix-junk/cancel-on-domain-reset/3")
    assert MEMORY_ENVIRONMENT_VERSION == "memory-environment/3"
    assert MEMORY_FULL_STATE_IMAGE == "memory-full-state-image/1"
    assert MEMORY_FULL_STATE_MAX_CELLS == 4096
    assert EXACT_MEMORY_PRODUCT.endswith(
        "memory-sparse-implicit-junk/2/cancel-on-domain-reset/sparse-overrides/6")


def test_environment_product_only_advances_on_its_declared_clock():
    memory = interface((None, "w"))
    policy = run(memory, 2)
    environment = MemoryEnvironment([policy])
    state = MemoryEnvironmentState.initial([memory], environment)
    write = request(memory, None, address=1, value=7, write=True)
    held = advance_memory_environment(
        [memory], environment, state, 0, write, active_domains={"other"})
    assert held.as_dict() == state.as_dict()
    advanced = advance_memory_environment(
        [memory], environment, state, 0, write, active_domains={"clk"})
    assert dict(advanced.state("ram").cells) == {1: 7}
    assert drive_memory_environment([memory], environment, advanced, 1)[
        "ram_req_ready"] == 1


@pytest.mark.parametrize("bad", [2, -1, "1", None])
def test_malformed_request_bits_and_values_are_typed_refusals(bad):
    memory = interface((None, "rw"))
    policy = run(memory, 1)
    state = MemoryEnvironmentState.initial(
        [memory], MemoryEnvironment([policy])).state("ram")
    outputs = request(memory, None, address=0)
    outputs["ram_req_valid"] = bad
    with pytest.raises(Refused, match="memory-request-valid-not-bit"):
        advance_memory_state(memory, policy, state, 0, outputs)


@pytest.mark.solver
def test_concrete_and_exact_paths_never_default_an_omitted_request_pin():
    import z3

    from behave.sim.rtl.memory_exact import MemoryPinBindings, SymbolicMemoryProduct

    memory = interface((None, "r"))
    policy = run(memory, 1)
    environment = MemoryEnvironment([policy])
    state = MemoryEnvironmentState.initial([memory], environment).state("ram")
    outputs = request(memory, None, address=1)
    del outputs["ram_req_addr"]
    with pytest.raises(Refused, match="memory-request-pin-missing"):
        advance_memory_state(memory, policy, state, 0, outputs)

    class Explorer:
        out_vars_current = {"ram_req_valid": "ram_req_valid"}
        mem_inp_vars = {
            "ram_req_ready": "ram_req_ready",
            "ram_resp_valid": "ram_resp_valid",
            "ram_resp_rdata": "ram_resp_rdata",
        }

    class Frames:
        terms = {
            ("ram_req_valid", 0): z3.Bool("missing_pin_valid"),
            ("ram_req_ready", 0): z3.Bool("missing_pin_ready"),
            ("ram_resp_valid", 0): z3.Bool("missing_pin_response_valid"),
            ("ram_resp_rdata", 0): z3.BitVec("missing_pin_response_data", 3),
        }

        def term(self, original, slot):
            return self.terms[original, slot]

    with pytest.raises(ValueError, match="no artifact memory output 'ram_req_addr'"):
        SymbolicMemoryProduct(
            (memory,), environment, MemoryPinBindings.from_frames(Frames(), Explorer(), 1), 1,
            tag="missing_pin")


def test_a_near_memory_interface_is_not_silently_reclassified_as_payload():
    from behave.sim.memory import detect_memory_interfaces

    with pytest.raises(Refused, match="memory-interface-malformed"):
        detect_memory_interfaces(
            {"ram_req_ready": 1},
            {"ram_req_valid": 1, "ram_req_addr": 2},
            strict=True)
    with pytest.raises(Refused, match="memory-interface-malformed"):
        detect_memory_interfaces(
            {"ram_req_ready": 1}, {"ram_req_addr": 2}, strict=True)
    with pytest.raises(Refused, match="memory-interface-malformed"):
        detect_memory_interfaces(
            {"ram_req_valid": 1, "ram_req_ready": 1,
             "ram_resp_valid": 1, "ram_resp_rdata": 3},
            {"ram_req_addr": 2}, strict=True)


def memory_schema(*, data_width=3, address_width=2):
    ports = [
        SchemaEndpoint("clk", "input", 1),
        SchemaEndpoint("rst", "input", 1),
        SchemaEndpoint("in_valid", "input", 1),
        SchemaEndpoint("in_ready", "output", 1),
        SchemaEndpoint("in_data", "input", 4),
        SchemaEndpoint("out_valid", "output", 1),
        SchemaEndpoint("out_ready", "input", 1),
        SchemaEndpoint("out_data", "output", 4),
        SchemaEndpoint("ram_req_valid", "output", 1),
        SchemaEndpoint("ram_req_addr", "output", address_width),
        SchemaEndpoint("ram_req_ready", "input", 1),
        SchemaEndpoint("ram_resp_valid", "input", 1),
        SchemaEndpoint("ram_resp_rdata", "input", data_width),
    ]
    return TransactionSchema(ports, source="declared task", authority=Authority.TASK)


def memory_realization(contract, *, data_width=None):
    from behave.sim.core.admission import Port, Realization
    from behave.sim.core.memory import MemoryRealization

    task_memory = contract.memories[0]
    width = task_memory.data_width if data_width is None else data_width
    schema = memory_schema(
        data_width=width, address_width=task_memory.addr_width)
    physical = [
        Port(endpoint.name, endpoint.width,
             driven_by=("environment" if endpoint.direction == "input"
                        else "artifact"))
        for endpoint in schema.endpoints.values()
    ]
    return Realization(
        "rtl", physical,
        channel_ports={"in": ["in_data"], "out": ["out_data"]},
        identity="candidate",
        memories=[MemoryRealization(
            "ram", task_memory.addr_width, width, "clk", [MemoryPort("r")])])


def test_task_import_and_candidate_realization_are_independent_authorities():
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.admission import admit
    from behave.sim.core.contract import Kind, TaskContract

    contract = contract_of_schema(memory_schema(), assume_kind=Kind.TRANSACTION)
    assert [memory.as_dict() for memory in contract.memories] == [
        MemoryInterface("ram", 2, 3, "clk", [MemoryPort("r")]).as_dict()]
    assert "ram_resp_rdata" not in {
        field.port for endpoint in contract.endpoints for field in endpoint.fields}
    assert admit(contract, memory_realization(contract)).checks[
        "external_memory_realization"] is True

    with pytest.raises(Refused, match="admission-memory-shape"):
        admit(contract, memory_realization(contract, data_width=4))

    undeclared = TaskContract(contract.endpoints, contract.clocks)
    with pytest.raises(Refused, match="admission-memory-extra"):
        admit(undeclared, memory_realization(contract))


def test_behavior_realization_uses_the_task_owned_memory_wrapper():
    """A logical Behavior and RTL realize one declared memory protocol."""

    from behave.sim import api
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.contract import Kind

    contract = contract_of_schema(memory_schema(), assume_kind=Kind.TRANSACTION)
    source = """\
class M:
    def reset(self):
        self.ram = [0] * 4

    def process(self, in_data):
        return {"out_data": self.ram[in_data & 3]}
"""
    realization = api.realization_of_behavior_source(
        source, contract, file="memory_behavior.py")
    admitted = api.admit(contract, realization)

    assert admitted.checks["external_memory_realization"] is True
    assert [memory.as_dict() for memory in realization.memories] == [
        MemoryRealization(
            "ram", 2, 3, "clk", [MemoryPort("r")]).as_dict()
    ]
    assert set(realization.ports) >= {
        "ram_req_valid", "ram_req_addr", "ram_req_ready",
        "ram_resp_valid", "ram_resp_rdata",
    }


def test_exact_behavior_binds_the_run_owned_memory_image():
    from behave.sim.behavior.task_boundary import lower_task_behavior
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.contract import Kind

    contract = contract_of_schema(memory_schema(), assume_kind=Kind.TRANSACTION)
    environment = MemoryEnvironment([
        run(contract.memories[0], 4, initial={2: 6})])
    source = """\
class M:
    def reset(self):
        self.ram = [0] * 4

    def process(self, in_data):
        return {"out_data": self.ram[in_data & 3]}
"""
    instance, _boundary = lower_task_behavior(
        source, "memory_behavior.py", contract, 1,
        memory_environment=environment, memory_horizon=4)

    replay = instance.bind(1).replay([{"in_data": 2}])
    assert not replay.broken
    assert replay.outputs[0].transactions == [{"out_data": 6}]
    audit = instance.source_audit(([{"in_data": 2}],))
    assert audit.status is AuditCheckStatus.AGREED, audit.as_dict()


def test_exact_memory_binding_does_not_exempt_other_state_from_alias_audit():
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.task_boundary import lower_task_behavior
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.contract import Kind

    contract = contract_of_schema(memory_schema(), assume_kind=Kind.TRANSACTION)
    environment = MemoryEnvironment([
        run(contract.memories[0], 4, initial={2: 6})])
    source = """\
class M:
    def reset(self):
        self.ram = [0] * 4
        self.left = [0]
        self.right = self.left

    def process(self, in_data):
        self.left[0] = in_data
        return {"out_data": self.right[0]}
"""

    with pytest.raises(LoweringError) as caught:
        lower_task_behavior(
            source, "memory_alias.py", contract, 1,
            memory_environment=environment, memory_horizon=4)

    assert caught.value.code == "state-aliased-in-construction"


def test_exact_behavior_refuses_a_pool_with_two_memory_environments():
    from behave.sim.behavior.memory_binding import (
        memory_environment_for_stimuli,
    )
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.contract import Kind
    from behave.sim.core.stimulus import Stimulus

    contract = contract_of_schema(memory_schema(), assume_kind=Kind.TRANSACTION)
    memory = contract.memories[0]
    first = MemoryEnvironment([run(memory, 4, initial={1: 2})])
    second = MemoryEnvironment([run(memory, 4, initial={1: 3})])
    stimuli = (
        Stimulus(queues={}, slots=(), memory_environment=first),
        Stimulus(queues={}, slots=(), memory_environment=second),
    )

    with pytest.raises(
            Refused, match="behavior-exact-memory-environment-not-unique"):
        memory_environment_for_stimuli(contract, stimuli, 4)


def test_witness_memory_choices_become_one_exact_stimulus_environment():
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.contract import Kind
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    contract = contract_of_schema(memory_schema(), assume_kind=Kind.TRANSACTION)
    raw, dropped, origin = stimuli_from_witnesses(
        contract,
        [{"inputs": [[5]], "source": "memory-provider",
          "mem_init": {"ram": {"2": 6}},
          "mem_policy": {"ram": {
              "read_latency": 2, "stall_rate": 0.5, "seed": 11}}}],
        ["in_data"], 1, stall_every=0, reset_hold_cycles=0,
        queue_bound=2, horizon=4)
    assert not dropped

    class Declared:
        drain = 0
        stall_every = 0
        horizon = 4
        reset_hold_cycles = 0
        queue_bound = 2

    stimuli, _merged, _provenance = canonical_set(
        raw, origin, contract, Declared())
    assert len(stimuli) == 1
    stimulus = stimuli[0]
    assert stimulus.memory_policy == ()
    memory_run = stimulus.memory_environment.run("ram")
    assert memory_run.initial == ((2, 6),)
    assert len(dict(memory_run.ready)[None]) == 4
    assert not stimulus.faults_against(contract)
    decoded = type(stimulus).from_dict(stimulus.as_dict())
    assert decoded.fingerprint == stimulus.fingerprint


@pytest.mark.cosim
@pytest.mark.eda
@pytest.mark.isolation
def test_native_multiclock_replay_uses_the_canonical_memory_transition(tmp_path):
    from behave.sim import api
    from behave.sim.core.clock_schedule import ClockTiming, GlobalSchedule
    from behave.sim.rtl.artifact import RTLArtifact

    rtl = tmp_path / "rtl.sv"
    rtl.write_text("""
module memory_writer(
  input logic a_clk, input logic a_rst,
  input logic b_clk, input logic b_rst,
  input logic write, input logic [2:0] data,
  output logic ram_req_valid,
  output logic [1:0] ram_req_addr,
  output logic [2:0] ram_req_wdata,
  input logic ram_req_ready
);
  assign ram_req_valid = !a_rst && write;
  assign ram_req_addr = 2'd1;
  assign ram_req_wdata = data;
endmodule
""".lstrip())
    memory = MemoryInterface("ram", 2, 3, "a", [MemoryPort("w")])
    # The retired harness allowed memory-only fixtures. The scored native Driver
    # requires an actual endpoint; make each tested write an accepted transaction.
    contract = api.TaskContract([api.Endpoint(
        "in", "in", [api.Field("value", api.FiniteType(3), "data")],
        api.sampled_edge("in", valid="write"), "a", kind=api.Kind.TRANSACTION)], [
        api.ClockDomain("a", "a_clk", "a_rst"),
        api.ClockDomain("b", "b_clk", "b_rst"),
    ], memories=[memory])
    schedule = GlobalSchedule.with_reset_prefix(
        [
            ClockTiming("a", 2, phase=2, clock="a_clk", reset="a_rst"),
            ClockTiming("b", 3, phase=3, clock="b_clk", reset="b_rst"),
        ],
        4, reset_edges={"a": 1, "b": 1})
    environment = MemoryEnvironment([MemoryRun.from_policy(
        memory, len(schedule.slots), stall_rate=0.0)])
    events = [api.EventSlot(
        ticks=slot.events, resets=slot.resets,
        offers=("in",) if "a" in slot.events and "a" not in slot.resets else (),
        controls={f"{name}_rst": int(name in slot.resets) for name in ("a", "b")})
        for slot in schedule.slots]
    schedule = GlobalSchedule(schedule.timings, [
        api.GlobalEventSlot(slot.time, event=event)
        for slot, event in zip(schedule.slots, events, strict=True)])
    writes = sum(bool(event.offers) for event in events)
    stimulus = api.Stimulus(queues={"in": [(5,)] * writes}, slots=events, clock_schedule=schedule,
        memory_environment=environment, source="multiclock-memory-fixture")
    assert not stimulus.faults_against(contract)
    trace = RTLArtifact(str(rtl), "memory_writer", tmp_path / "sim").trace(contract, stimulus)
    assert type(trace) is api.AuditedTrace, trace
    assert trace.completion is api.Completion.NORMAL
    assert len(trace.records) == len(schedule.slots)
    assert trace.accepted_count(["in"]) == writes > 0
    final = dict(trace.records[-1].environment)["external-memory"]
    assert dict(dict(final["memories"])["ram"]["writes"]) == {1: 5}
    # Coincident edges remain atomic; ticking only b cannot write memory owned by a.
    images = [dict(row.environment)["external-memory"] for row in trace.records]
    for index, slot in enumerate(schedule.slots):
        if index and "a" not in slot.events:
            assert images[index] == images[index - 1]


@pytest.mark.cosim
def test_public_rtl_replay_uses_the_same_single_port_memory_product(tmp_path):
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.contract import Kind
    from behave.sim.core.refusal import Refusal
    from behave.sim.core.stimulus import EventSlot, Stimulus
    from behave.sim.rtl.artifact import RTLArtifact

    contract = contract_of_schema(
        memory_schema(data_width=4, address_width=16),
        assume_kind=Kind.TRANSACTION)
    assert contract.memories[0].depth > MEMORY_FULL_STATE_MAX_CELLS
    policy = MemoryRun(
        "ram", initial={2: 9}, read_latency=1, ready={None: [1] * 4})
    stimulus = Stimulus(
        queues={"in": [(2,)]},
        slots=[
            EventSlot(reset=True, controls={"rst": 1, "out_ready": 0}),
            EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
            EventSlot(controls={"rst": 0, "out_ready": 1}),
            EventSlot(controls={"rst": 0, "out_ready": 1}),
        ],
        memory_environment=MemoryEnvironment([policy]))
    rtl = tmp_path / "reader.sv"
    rtl.write_text("""
module reader(
  input logic clk, input logic rst,
  input logic in_valid, output logic in_ready,
  input logic [3:0] in_data,
  output logic out_valid, input logic out_ready,
  output logic [3:0] out_data,
  output logic ram_req_valid, output logic [15:0] ram_req_addr,
  input logic ram_req_ready,
  input logic ram_resp_valid, input logic [3:0] ram_resp_rdata
);
  assign ram_req_valid = in_valid;
  assign ram_req_addr = {12'b0, in_data};
  assign in_ready = ram_req_ready;
  assign out_valid = ram_resp_valid;
  assign out_data = ram_resp_rdata;
endmodule
""".lstrip())

    got = RTLArtifact(str(rtl), "reader", tmp_path / "sim").trace(
        contract, stimulus)
    assert not isinstance(got, Refusal), got
    emitted = [dict(record.accepted).get("out") for record in got.records]
    assert emitted == [None, None, (9,), None]
    assert dict(got.records[0].environment)["external-memory"][
        "memories"][0][0] == "ram"


@pytest.mark.cosim
@pytest.mark.parametrize(('declared', 'query_ms'), [(False, 1), (True, 1)],
                         ids=['unbound', 'query-budget'])
def test_public_single_clock_exact_product_carries_memory_state(tmp_path, declared, query_ms):
    """Memory record admission survives an independently budgeted goal query."""
    from pathlib import Path

    from behave.sim import api
    from behave.sim.behavior.reference_memory import ReferenceMemory
    from behave.sim.contract_of import contract_of_schema
    from behave.sim.core.contract import Kind

    from .test_rtl_public_analysis_path import _BaselineOnly

    contract = contract_of_schema(
        memory_schema(data_width=4, address_width=16),
        assume_kind=Kind.TRANSACTION)
    assert contract.memories[0].depth > MEMORY_FULL_STATE_MAX_CELLS
    generation = api.GenerationSpec(
        horizon=6, queue_bound=1, reset_hold_cycles=1, drain=3)
    baseline = api.build_baseline(
        contract, generation,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    stimulus, = baseline.stimuli
    environment = stimulus.memory_environment
    rtl = tmp_path / "reader_exact.sv"
    rtl.write_text("""
module reader_exact(
  input logic clk, input logic rst,
  input logic in_valid, output logic in_ready,
  input logic [3:0] in_data,
  output logic out_valid, input logic out_ready,
  output logic [3:0] out_data,
  output logic ram_req_valid, output logic [15:0] ram_req_addr,
  input logic ram_req_ready,
  input logic ram_resp_valid, input logic [3:0] ram_resp_rdata
);
  logic stalled;
  initial stalled = 1'b0;
  // The explicit initial process is intentional. Use an ordinary clocked
  // process rather than always_ff's single-writer declaration.
  always @(posedge clk) begin
    if (rst) stalled <= 1'b0;
    else if (out_ready) stalled <= stalled;
    else stalled <= ~stalled;
  end
  assign ram_req_valid = in_valid;
  assign ram_req_addr = {12'b0, in_data};
  assign in_ready = ram_req_ready;
  assign out_valid = ram_resp_valid;
  assign out_data = ram_resp_rdata ^ {4{stalled}};
endmodule
""".lstrip())
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {
        'behavior.py': b'''class M:
    def reset(self):
        self.ram = [0] * 65536

    def process(self, in_data):
        return {"out_data": self.ram[in_data]}
'''}, config={'class': 'M'})
    task = api.admit(contract, api.realization_of_rtl_source(
        rtl.read_text(), 'public memory analysis fixture', top='reader_exact',
        contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(
        rtl, 'reader_exact', tmp_path / 'sim-exact', timeout_s=300), generation,
        analysis_memory_environment=environment if declared else None)
    request = handle.command_request(
        run_directory=tmp_path / 'memory-invocation', baseline=baseline,
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True,
        reference=reference, feasibility_timeout_ms=30_000, query_timeout_ms=query_ms,
        analysis_wall_timeout_seconds=180)
    result = api.handle_command(request)

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert result.evaluation.required_complete
    trace = result.evaluation.results[0].trace
    address = dict(stimulus.queues)['in'][0][0]
    expected = ReferenceMemory(contract.memories[0], environment.run('ram'))[address]
    assert [dict(row.accepted)['out'] for row in trace.records
            if 'out' in dict(row.accepted)] == [(expected,)]
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted['authority']['passed'] is True
    assert persisted['report']['analysis'] == json.loads(json.dumps(result.analysis.as_dict()))
    if not declared:
        assert result.analysis.verdict is api.Verdict.REFUSED
        assert 'rtl-exact-memory-environment-unbound' in json.dumps(result.analysis.as_dict())
        return
    if query_ms is None:
        assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    audit = result.analysis.evidence['record_audits'][0]
    assert audit['disposition'] == 'admitted', audit
    assert result.analysis.evidence['binding']['representation'].startswith('rtl-source:')
    witness = api.AuditedWitness.from_record(audit['witness'])
    expected_memory = MemoryEnvironmentState.initial(contract.memories, environment).as_dict()
    assert witness.states[0]['memory.environment'] == expected_memory
    assert witness.outputs[2] == ({'out': {'out_data': expected}},)
    assert result.analysis.evidence['feasibility']['established_by'] == 'audited-pool'
    if query_ms is None:
        goals = result.analysis.as_dict()['per_goal']
        assert goals and all(row['status'] != 'unresolved' for row in goals.values())
    elif result.analysis.verdict is not api.Verdict.CLASSIFIED:
        # Record admission is not a claim that every goal query finishes. Keep
        # the exact query failure and the already-checked memory witness separate.
        assert result.analysis.verdict is api.Verdict.REFUSED, result.analysis.as_dict()
        assert result.analysis.evidence['refusal']['kind'] in {
            'analysis-budget-exhausted', 'rtl-source-analysis-budget-exhausted'}
