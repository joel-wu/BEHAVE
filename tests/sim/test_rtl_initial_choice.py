"""Selected state is a closed analysis record, not a pin-input or score override."""

import asyncio
import pickle
import sys
from types import ModuleType

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.rtl.initial_state import RTLInitialState
from behave.sim.rtl.replay_startup import install_initial_state

ARTIFACT = "a" * 64


def test_choice_is_complete_immutable_and_roundtrips():
    widths, values = {"q": 3}, {"q": 6}
    choice = RTLInitialState.from_values(ARTIFACT, widths, values)
    widths["q"], values["q"] = 9, 99
    wire = choice.as_dict()
    assert RTLInitialState.from_dict(wire).as_dict() == wire
    assert pickle.loads(pickle.dumps(choice)).as_dict() == wire
    wire["fields"][0][2] = 0
    assert choice.values() == {"q": 6}
    for name, value in (("fields", ()), ("artifact", "b" * 64)):
        with pytest.raises(AttributeError):
            setattr(choice, name, value)
    with pytest.raises(AttributeError):
        choice.__init__(ARTIFACT, [])
    with pytest.raises(TypeError):
        type("Forged", (RTLInitialState,), {})


@pytest.mark.parametrize("fields", [
    [["q", True, 0]], [["q", 3, True]], [["q", 0, 0]],
    [["q", 3, -1]], [["q", 3, 8]], [["", 3, 0]],
    [["q", 3]], [["q", 3, 0], ["q", 3, 1]],
    [["z", 3, 0], ["a", 3, 0]],
])
def test_malformed_choices_never_reach_simulator(fields):
    with pytest.raises((TypeError, ValueError)):
        RTLInitialState(ARTIFACT, fields)


def test_choice_checks_full_layout_artifact_and_readback():
    choice = RTLInitialState(ARTIFACT, [["q", 3, 6]])
    choice.check_binding(ARTIFACT, ("q",), {"q": 3})
    for artifact, names, widths, code in [
        ("b" * 64, ("q",), {"q": 3}, "artifact-mismatch"),
        (ARTIFACT, (), {}, "layout-mismatch"),
        (ARTIFACT, ("q",), {"q": 2}, "layout-mismatch"),
    ]:
        with pytest.raises(Refused) as failure:
            choice.check_binding(artifact, names, widths)
        assert failure.value.refusal.kind == "rtl-initial-state-" + code
    for values in ({}, {"q": 5}, {"q": 6, "extra": 0}, {"q": True}):
        with pytest.raises(Refused) as failure:
            choice.check_observed(values)
        assert failure.value.refusal.kind == "rtl-initial-state-readback-mismatch"
    choice.check_observed({"q": 6})


class Handle:
    def __init__(self, width, *, ignores_writes=False):
        self.width, self.ignores_writes = width, ignores_writes
        self.stored, self.writes = 0, []

    def __len__(self):
        return self.width

    @property
    def value(self):
        return self.stored

    @value.setter
    def value(self, value):
        self.writes.append(value)
        if not self.ignores_writes:
            self.stored = value


def test_installer_preflights_all_fields_and_requires_readback(monkeypatch):
    from behave.sim.rtl import register_init

    async def tick(*args, **kwargs):
        pass

    triggers = ModuleType("cocotb.triggers")
    triggers.Timer = triggers.ReadOnly = tick
    monkeypatch.setitem(sys.modules, "cocotb.triggers", triggers)
    choice = RTLInitialState(ARTIFACT, [["a", 3, 6], ["b", 2, 2]])
    a, b = Handle(3), Handle(1)
    def deposit(top, selected):
        assert top == "top"
        for name, _width, value in selected.fields:
            {"a": a, "b": b}[name].value = value

    monkeypatch.setattr(register_init, "deposit_registers", deposit)
    with pytest.raises(Refused) as failure:
        asyncio.run(install_initial_state(choice, {"a": a, "b": b}, top="top"))
    assert failure.value.refusal.kind == "rtl-initial-state-field-unavailable"
    assert a.writes == b.writes == []
    b = Handle(2, ignores_writes=True)
    with pytest.raises(Refused) as failure:
        asyncio.run(install_initial_state(choice, {"a": a, "b": b}, top="top"))
    assert failure.value.refusal.kind == "rtl-initial-state-readback-mismatch"
    b = Handle(2)
    asyncio.run(install_initial_state(choice, {"a": a, "b": b}, top="top"))
    assert (a.value, b.value) == (6, 2)


def test_native_adapter_preflights_every_register_before_writing(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.rtl import register_init

    class Operation:
        def __init__(self):
            self.calls = []
            self.reject = True

        def __call__(self, top, name, width, bits):
            self.calls.append((top, name, width, bits))
            return 2 if name == b"b" and self.reject else 0

    operation = Operation()
    monkeypatch.setattr(register_init.ctypes, "CDLL", lambda _: SimpleNamespace(
        behave_initial_state_v1=operation))
    choice = RTLInitialState(ARTIFACT, [["a", 3, 6], ["b", 2, 2]])
    with pytest.raises(Refused, match="rtl-initial-state-register-unavailable"):
        register_init.deposit_registers("top", choice)
    assert [bits for _, _, _, bits in operation.calls] == [None, None]
    operation.calls.clear()
    operation.reject = False
    register_init.deposit_registers("top", choice)
    assert [bits for _, _, _, bits in operation.calls] == [None, None, b"110", b"10"]


def test_initial_coordinates_are_complete_constant_and_exactly_typed():
    from behave.sim.rtl.initial_request import InitialStateCoordinates

    initial = InitialStateCoordinates(ARTIFACT, {"a": 3, "b": 2})
    row = {"schedule.offer.in": 1, **initial.encode({"a": 6, "b": 2})}
    rows = [dict(row), dict(row)]
    assert initial.decode(rows).values() == {"a": 6, "b": 2}
    assert dict(initial.bounds) == {"initial.rtl.a": (0, 7), "initial.rtl.b": (0, 3)}
    for changed in ({"initial.rtl.a": 5}, {"initial.rtl.b": True}):
        with pytest.raises(Refused, match="request-changed"):
            initial.decode([row, {**row, **changed}])
    for changed in ({"initial.rtl.a": 8}, {"initial.rtl.a": -1},
                    {"initial.rtl.a": True}):
        with pytest.raises(Refused, match="request-invalid"):
            initial.decode([{**row, **changed}])
    missing = dict(row)
    del missing["initial.rtl.b"]
    with pytest.raises(Refused, match="request-incomplete"):
        initial.decode([missing])
    assert InitialStateCoordinates(ARTIFACT, {}).decode([{}]).values() == {}
