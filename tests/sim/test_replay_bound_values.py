"""Replay shares fixed values, never observations or transitions across slots."""
import io
import pickle
import random

import pytest

from behave.sim import api
from behave.sim.behavior.reference_memory import ReferenceMemory
from behave.sim.core import record_value
from behave.sim.core.frozen import FrozenMap, freeze, frozen_map
from behave.sim.core.input_queue import InputQueueState
from behave.sim.core.memory import MemoryState
from behave.sim.core.protocol import AdapterDrive, AdapterTransition, _directions
from behave.sim.memory_driver import _protocol
from .test_protocol_adapters import _contract


def test_contract_endpoint_binding_does_not_change_wire_identity():
    contract = _contract("ready-valid")
    image = contract.as_dict()
    assert contract.inputs() is contract.inputs()
    assert contract.outputs() is contract.outputs()
    assert _directions(contract) == (contract.inputs(), contract.outputs())
    restored = api.TaskContract.from_dict(image)
    assert restored.as_dict() == image
    assert restored.digest == contract.digest


def test_frozen_sharing_preserves_detachment_and_subclass_boundaries():
    row = (1, (2, 3))
    assert freeze(row) is row
    mutable = [2]
    source = (row, mutable)
    value = freeze(source)
    mutable.append(3)
    assert value == (row, (2,)) and value[0] is row
    mapping = FrozenMap({"x": source})
    assert frozen_map(mapping) is mapping
    assert FrozenMap(mapping)._pairs is mapping._pairs
    assert frozen_map({}) is frozen_map(())
    with pytest.raises(AttributeError):
        frozen_map({}).__init__({"bad": 1})

    class Derived(FrozenMap):
        def items(self):
            return (("x", [7]),)

    derived = Derived({"original": 3})
    copied = frozen_map(derived)
    assert type(copied) is FrozenMap and copied == FrozenMap(derived)
    assert copied._pairs is not derived._pairs


def test_adapter_values_still_validate_before_sharing():
    controls = FrozenMap({"valid": 1})
    presentations = FrozenMap({"in": (7, False)})
    drive = AdapterDrive(controls, presentations)
    assert drive.controls is controls and drive.presentations is presentations
    state = FrozenMap({"pending": (1, 2)})
    transition = AdapterTransition(state=state)
    assert transition.state is state
    assert transition.monitor is transition.emitted_outputs
    with pytest.raises(TypeError):
        AdapterDrive(FrozenMap({"valid": True}), presentations)
    with pytest.raises(TypeError):
        AdapterTransition(state=FrozenMap({"bad": object()}))
    with pytest.raises(ValueError):
        AdapterDrive(controls, FrozenMap({1: (7,)}))


def test_queue_idle_stall_and_saturated_release_share_only_unchanged_values():
    stimulus = api.Stimulus(queues={"in": [(7,)]})
    queue = InputQueueState.initial(("in",))
    idle = queue.prepare(stimulus, api.EventSlot())
    assert idle.released is queue.released
    assert queue.commit(idle, {}) is queue
    offer = queue.prepare(stimulus, api.EventSlot(offers=("in",)))
    assert offer.presentations["in"] == (7,)
    stalled = queue.commit(offer, {"in": None})
    assert stalled.accepted is queue.accepted and stalled.released is offer.released
    again = stalled.prepare(stimulus, api.EventSlot(offers=("in",)))
    assert again.released is stalled.released  # full queue, no extra release
    done = stalled.commit(again, {"in": (7,)})
    assert done.accepted["in"] == 1 and stalled.accepted["in"] == 0
    drained = done.prepare(stimulus, api.EventSlot())
    assert done.commit(drained, {"in": None}) is done
    with pytest.raises(ValueError, match="no pending presentation"):
        done.commit(drained, {"in": (8,)})
    assert done.accepted["in"] == 1


@pytest.mark.parametrize("width", (1, 8, 16, 32, 64, 255, 256, 257, 512))
@pytest.mark.parametrize("name", ("ram", "other", "内存"))
def test_bound_junk_matches_original_formula_and_round_trip(name, width):
    params = _protocol.memory_junk_parameters(name, width)
    first, second, offset, shift = params
    mask = (1 << width) - 1
    read = pickle.loads(pickle.dumps(_protocol.memory_junk_reader(name, width)))
    rng = random.Random(591)
    for address in (0, 1, 255, 256, 2**80, *[rng.getrandbits(100) for _ in range(25)]):
        value = ((address & mask) ^ offset) * first & mask
        value ^= value >> shift
        expected = (value * second + offset) & mask
        assert read(address) == expected == _protocol.memory_junk_value(name, address, width)
    for address in (-1, True, 1.5, "1", None):
        with pytest.raises(ValueError):
            read(address)


def test_bound_memory_computes_constants_once_and_never_for_stored_reads(monkeypatch):
    interface = api.MemoryInterface("ram", 4, 8, "clk", [api.MemoryPort("r")])
    run = api.MemoryRun("ram", initial={1: 77}, ready={None: [1] * 5})
    parameters = _protocol.memory_junk_parameters
    calls = []

    def counted(*args):
        calls.append(args)
        return parameters(*args)

    monkeypatch.setattr(_protocol, "memory_junk_parameters", counted)
    behavior = ReferenceMemory(interface, run)
    runtime = _protocol.RuntimeState(MemoryState.initial(interface, run), interface)
    assert calls == [("ram", 8)] * 2
    for address in (2, 3, 2, 4):
        assert behavior[address] == runtime.read(interface, address)
    for slot in range(5):
        updates = runtime.prepare(interface, 1, {"ram_req_ready": 1},
                                  {"ram_req_valid": 1, "ram_req_addr": 3}, slot=slot)
        runtime.commit(updates)
    assert len(calls) == 2
    behavior._default = lambda _: pytest.fail("stored read computed a default")
    assert behavior[1] == 77
    behavior[3] = 91
    assert behavior[3] == 91


def test_shared_rows_are_validated_once_per_adjacent_group(monkeypatch):
    original = record_value._project
    row = {"values": [(1, 2, 3)]}
    visits = []

    def counted(value, *args, **kwargs):
        if value is row:
            visits.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(record_value, "_project", counted)
    value = {"rows": [row] * 1000}
    stream = io.StringIO()
    record_value.write_canonical_json(value, stream, record_paths=(("rows",),))
    assert len(visits) == 1
    assert stream.getvalue() == record_value.canonical_json(value)
    # There is no cross-call memo: mutation must be checked on the next write.
    row["values"].append(float("nan"))
    stream = io.StringIO()
    with pytest.raises(ValueError, match="non-finite"):
        record_value.write_canonical_json(value, stream, record_paths=(("rows",),))
    assert not stream.getvalue()


def test_repeated_valid_rows_do_not_hide_a_later_cycle():
    row = {"ok": [1]}
    cycle = []
    cycle.append(cycle)
    stream = io.StringIO()
    with pytest.raises(ValueError, match="cycle"):
        record_value.write_canonical_json([row] * 1000 + [cycle], stream)
    assert not stream.getvalue()


def test_validation_initial_value_cannot_hide_an_invalid_first_item():
    stream = io.StringIO()
    with pytest.raises(TypeError, match="outside the finite record carrier"):
        record_value.write_canonical_json([record_value._UNINDEXED], stream)
    assert not stream.getvalue()
