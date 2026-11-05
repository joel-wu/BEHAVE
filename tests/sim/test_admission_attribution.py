"""Candidate interface facts are not confused with task/capability refusals."""

import pytest

from behave.sim import api

from .test_api_path import contract, realization


@pytest.mark.parametrize("name", ("in_a", "out_ready", "rst", "clk"))
def test_missing_physical_signal_has_candidate_attribution(name):
    original = realization()
    ports = [p for p in original.ports.values() if p.name != name]
    offered = api.Realization("rtl", ports, identity="test-missing-pin")
    with pytest.raises(api.FailureRaised) as caught:
        api.admit(contract(), offered)
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.phase == "candidate-admission"
    assert caught.value.failure.disposition is api.FailureDisposition.SCORE_ZERO


def test_invalid_task_binding_is_checked_before_candidate_mismatch():
    task = contract().as_dict()
    task["endpoints"][1]["fields"][0]["port"] = None
    malformed = api.TaskContract.from_dict(task)
    offered = api.Realization("rtl", (), identity="no-pins")
    with pytest.raises(api.Refused, match="admission-field-unbound"):
        api.admit(malformed, offered)


@pytest.mark.parametrize("domain", ["clk", "compute", "c"])
def test_rtl_realization_translates_clock_labels_using_declared_physical_pin(monkeypatch, domain):
    from types import SimpleNamespace
    from behave.sim.rtl import elaborate

    image = contract().as_dict()
    image["clocks"][0]["name"] = domain
    for endpoint in image["endpoints"]:
        endpoint["clock"] = domain
    bound = api.TaskContract.from_dict(image)
    physical = realization().ports
    schema = SimpleNamespace(top="M", endpoints={name: SimpleNamespace(
        name=name, width=port.width, signed=port.signed,
        direction="input" if port.driven_by == "environment" else "output")
        for name, port in physical.items()})
    monkeypatch.setattr(elaborate, "schema_of_module", lambda *_args, **_kwargs: schema)
    offered = api.realization_of_rtl_source("module M; endmodule", top="M", contract=bound)
    assert offered.ports["in_a"].clock == offered.ports["out_y"].clock == domain
    api.admit(bound, offered)


def test_memory_realization_uses_the_same_domain_label_translation():
    from behave.sim.contract_of import _memory_records

    detected = [{"name": "ram", "addr_width": 4, "data_width": 8,
                 "ports": [{"type": "rw", "id": None}]}]
    clocks = {"": {"clock": "clk", "reset": "rst"}}
    legacy, = _memory_records(detected, clocks, realization=True)
    bound, = _memory_records(detected, clocks, realization=True, domain_labels={"clk": "compute"})
    assert legacy.clock == "clk" and bound.clock == "compute"
    assert bound.as_dict() == {**legacy.as_dict(), "clock": "compute"}
