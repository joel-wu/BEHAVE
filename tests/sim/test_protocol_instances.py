"""Explicit interface membership survives public and per-domain boundaries."""

import copy
import hashlib
import pickle

import pytest

from behave.sim import api
from behave.sim.core.atomic import atomic_adapter_programs
from behave.sim.core.monitor import MonitorProduct
from behave.sim.core.protocol import adapter_program_for
from behave.sim.core.stimulus import canonical_json
from behave.sim.declarations import Declarations, declared_contract
from behave.sim.goal_policy import evaluation_goal_policy
from behave.sim.rtl.exact_product import needs_global_schedule_product
from tests.sim.test_protocol_adapters import (
    _contract,
    _evaluate,
    _realization,
    _reference,
    _sd_stimulus,
    _StartDoneDoubling,
)


def _explicit(contract, instances=None):
    return api.TaskContract(
        contract.endpoints, contract.clocks, source=contract.source,
        authority=contract.authority, memories=contract.memories,
        protocol_instances=(
            [api.ProtocolInstance("compute", [e.name for e in contract.endpoints])]
            if instances is None else instances))


def _two_domains():
    endpoints, clocks, instances = [], [], []
    for domain, protocol in (("a", "ready-valid"), ("b", "start-done")):
        original = _contract(protocol)
        names = []
        for endpoint in original.endpoints:
            row = endpoint.as_dict()
            row["name"] = domain + endpoint.name
            row["clock"] = domain
            for field in row["fields"]:
                field["port"] = domain + field["port"]
            for key in ("environment_controls", "artifact_controls"):
                row["adapter"][key] = [domain + pin for pin in row["adapter"][key]]
            endpoints.append(api.Endpoint.from_dict(row))
            names.append(row["name"])
        clocks.append(api.ClockDomain(domain, domain + "clk", domain + "rst"))
        instances.append(api.ProtocolInstance("interface-" + domain, names))
    return api.TaskContract(endpoints, clocks, protocol_instances=instances)


@pytest.mark.parametrize("protocol", ["ready-valid", "start-done"])
def test_wire_pickle_and_declaration_round_trips_preserve_instances(protocol):
    contract = _explicit(_contract(protocol))
    image = contract.as_dict()
    for restored in (api.TaskContract.from_dict(copy.deepcopy(image)),
                     pickle.loads(pickle.dumps(contract)),
                     declared_contract(contract, Declarations())):
        assert restored.protocol_instances == contract.protocol_instances
        assert restored.digest == contract.digest
        assert adapter_program_for(restored) is adapter_program_for(contract)
    image["protocol_instances"][0]["endpoints"].clear()
    assert contract.protocol_instances[0].endpoints == ("in", "out")
    with pytest.raises(AttributeError):
        contract.protocol_instances[0].mapping = "changed/1"


def test_legacy_wire_identity_is_preserved_but_grouping_is_semantic():
    legacy = _contract("ready-valid").as_dict()
    legacy["version"] = "task-contract/5"
    for field in ("protocol_instances", "observation_goals", "transaction_monitors",
                  "latency_goals", "operation_rules", "source_read_rules"):
        legacy.pop(field)
    semantic = {key: value for key, value in legacy.items()
                if key not in ("version", "source")}
    restored = api.TaskContract.from_dict(legacy)
    assert restored.digest == hashlib.sha256(canonical_json(semantic).encode()).hexdigest()
    assert restored.as_dict()["version"] == api.TASK_CONTRACT_VERSION
    grouped = _explicit(restored)
    assert grouped.digest != restored.digest
    reordered = _explicit(restored, [api.ProtocolInstance("compute", ["out", "in"])])
    assert reordered.digest == grouped.digest
    for instance in (api.ProtocolInstance("other", ["in", "out"]),
                     api.ProtocolInstance("compute", ["in", "out"], mapping="frame/1")):
        assert _explicit(restored, [instance]).digest != grouped.digest


@pytest.mark.parametrize("mutate", [
    lambda row: row.pop("protocol_instances"),
    lambda row: row.update(protocol_instances={}),
    lambda row: row["protocol_instances"][0].update(extra=True),
    lambda row: row["protocol_instances"][0].pop("mapping"),
    lambda row: row.update(version="task-contract/5"),
])
def test_wire_boundary_never_silently_discards_grouping(mutate):
    row = _explicit(_contract("start-done")).as_dict()
    mutate(row)
    with pytest.raises(TypeError):
        api.TaskContract.from_dict(row)


@pytest.mark.parametrize("instances,code", [
    ([api.ProtocolInstance("one", ["in"])], "membership-incomplete"),
    ([api.ProtocolInstance("one", ["in", "missing"])], "membership-invalid"),
    ([api.ProtocolInstance("one", ["in", "out"]),
      api.ProtocolInstance("two", ["out"])], "membership-invalid"),
    ([api.ProtocolInstance("one", ["in"]),
      api.ProtocolInstance("one", ["out"])], "name-duplicate"),
    ([object()], "interfaces-invalid"),
])
def test_explicit_instances_are_a_complete_disjoint_partition(instances, code):
    with pytest.raises(api.Refused, match=code):
        _explicit(_contract("ready-valid"), instances)


@pytest.mark.parametrize("mapping", [True, "direct", "direct/0", "direct/01", "direct/１"])
def test_mapping_identity_is_typed_and_versioned(mapping):
    with pytest.raises(api.Refused, match="contract-interface-mapping-invalid"):
        api.ProtocolInstance("compute", ["in", "out"], mapping=mapping)


def test_unsupported_assembly_cannot_be_flattened():
    base = _contract("ready-valid")
    for instances, reason in (
        ([api.ProtocolInstance("compute", ["in", "out"], mapping="frame/1")],
         "admission-interface-mapping-unsupported"),
    ):
        task = _explicit(base, instances)
        with pytest.raises(api.Refused, match=reason):
            api.admit(task, _realization("ready-valid", "rv-device"))
        with pytest.raises(api.Refused, match=reason):
            evaluation_goal_policy(task, api.CatalogueManifest(()))


def test_clock_projection_preserves_instances_and_resolves_each_adapter():
    contract = _two_domains()
    assert needs_global_schedule_product(contract)
    programs = atomic_adapter_programs(contract)
    for domain, (view, program) in programs.items():
        assert view.protocol_instances == contract.protocol_instances_for(domain)
        assert view.protocol_instances[0] is contract.protocol_instances_for(domain)[0]
        assert {endpoint.clock for endpoint in view.endpoints} == {domain}
        assert program.name == ("ready-valid" if domain == "a" else "start-done")
    monitors = MonitorProduct(contract)
    for domain, projected, _monitor in monitors._components:
        assert projected.protocol_instances == contract.protocol_instances_for(domain)


def test_one_instance_cannot_cross_clock_domains():
    with pytest.raises(api.Refused, match="contract-interface-clock-conflict"):
        _explicit(_two_domains())


@pytest.mark.parametrize("faulty", [False, True])
def test_explicit_group_uses_the_same_driver_score_and_phase_capabilities(monkeypatch, faulty):
    from behave.sim.core import protocol

    # Remove only symbolic capability in a fixture, not concrete semantics. This
    # tests phase separation on a real registered protocol, without a fake scorer.
    class ReplayOnly(type(adapter_program_for(_contract("start-done")))):
        exact_control_product = False
        exact_control_product_policy = None

    monkeypatch.setitem(protocol._REGISTRY, ("start-done", 1), ReplayOnly())
    contract = _explicit(_contract("start-done"))
    policy = evaluation_goal_policy(contract, api.CatalogueManifest(()))
    before = policy.manifest.digest()
    assert policy.validate(api.CONCRETE_PHASE) is policy
    assert policy.enabled_providers(api.LOCAL_GUIDANCE) == ()
    with pytest.raises(api.Refused, match="goal-provider-enabled-capability-missing"):
        policy.validate(api.EXACT_ANALYSIS_PHASE)
    assert policy.manifest.digest() == before
    reference = _reference()
    task = api.admit(contract, _realization("start-done", "sd-device"),
                     reference=reference)
    stimulus = _sd_stimulus(
        api.EventSlot(offers=["in"], controls={"rst": 0}),
        api.EventSlot(controls={"rst": 0}))
    result = _evaluate(task, stimulus, _StartDoneDoubling(spurious=faulty), reference)
    assert result.passed is (not faulty), result.as_dict()
    if faulty:
        assert "monitor.start-done.done-without-outstanding" in result.safety_hits
    else:
        assert api.transfers(result.records[0], "out") == [(6,)]
