"""Task declarations derived from the trusted Behavior boundary."""

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refused
from behave.sim.declarations import declarations_at
from behave.sim.reference_rules import (
    GENERATOR,
    behavior_digest,
    derive,
    interface_digest,
)

U8 = api.FiniteType(8)


def _endpoint(name, direction, field, port, clock="clk"):
    return api.Endpoint(
        name, direction, [api.Field(field, U8, port)],
        api.ready_valid(direction, f"{name}_valid", f"{name}_ready"),
        clock=clock,
    )


def _contract():
    return api.TaskContract(
        [
            _endpoint("in", api.Direction.IN, "data", "in_data"),
            _endpoint("out", api.Direction.OUT, "data", "out_data"),
        ],
        [api.ClockDomain("clk", "clk", "rst")],
        source="test task",
    )


VARIABLE_CARDINALITY = """\
class Reference:
    def reset(self):
        self.pending = None

    def process(self, in_data):
        if in_data == 0:
            return None
        if in_data == 1:
            return {"out_data": 1}
        return [{"out_data": in_data}, {"out_data": in_data + 1}]
"""


def test_reference_boundary_derives_stream_semantics_not_a_fixed_ratio():
    fields, evidence, missing = derive(VARIABLE_CARDINALITY, _contract())

    assert not missing
    assert fields == {
        "endpoint.in.kind": "transaction",
        "endpoint.out.kind": "transaction",
        "endpoint.out.matching": "ordered",
    }
    assert evidence == {"in": "process", "out": "process"}
    assert not any("answers_per_transaction" in key for key in fields)


def test_reference_boundary_covers_each_clock_domain_without_flattening_it():
    endpoints = []
    for domain in ("w", "r"):
        endpoints.extend((
            _endpoint(f"{domain}_in", api.Direction.IN, "data",
                      f"{domain}_in_data", domain),
            _endpoint(f"{domain}_out", api.Direction.OUT, "data",
                      f"{domain}_out_data", domain),
        ))
    contract = api.TaskContract(
        endpoints,
        [api.ClockDomain("w", "w_clk", "w_rst"),
         api.ClockDomain("r", "r_clk", "r_rst")],
    )
    source = """\
class Reference:
    def reset(self):
        pass
    def process_w(self, w_in_data):
        return {"w_out_data": w_in_data}
    def process_r(self, r_in_data):
        return {"r_out_data": r_in_data}
"""

    fields, evidence, missing = derive(source, contract)

    assert not missing
    assert evidence == {
        "r_in": "process_r", "r_out": "process_r",
        "w_in": "process_w", "w_out": "process_w",
    }
    assert fields["endpoint.r_out.matching"] == "ordered"
    assert fields["endpoint.w_out.matching"] == "ordered"


def test_interface_identity_ignores_provenance_but_not_physical_shape():
    first = _contract()
    same = api.TaskContract(first.endpoints, first.clocks, source="another path")
    wider = api.TaskContract(
        [
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("data", api.FiniteType(9), "in_data")],
                api.ready_valid(api.Direction.IN, "in_valid", "in_ready"),
                clock="clk"),
            first.endpoint("out"),
        ],
        first.clocks,
    )
    falling = api.TaskContract(
        first.endpoints,
        [api.ClockDomain("clk", "clk", "rst", edge="falling")],
    )

    assert interface_digest(first) == interface_digest(same)
    assert interface_digest(first) != interface_digest(wider)
    assert interface_digest(first) != interface_digest(falling)


INTERFACE = """\
# RTL interface
```systemverilog
module dut(
    input logic clk,
    input logic rst,
    input logic [7:0] in_data,
    input logic in_valid,
    output logic in_ready,
    output logic [7:0] out_data,
    output logic out_valid,
    input logic out_ready
);
```
"""


def _task_text(contract, behavior):
    return f"""\
[generated]
generator = {GENERATOR}
behavior_digest = "{behavior_digest(behavior)}"
interface_digest = "{interface_digest(contract)}"

[endpoint.in]
kind = "transaction"

[endpoint.out]
kind = "transaction"
matching = "ordered"
"""


@pytest.mark.parametrize("authority_reader", [False, True])
def test_one_task_read_shares_elaboration_but_not_across_reads(tmp_path, monkeypatch,
                                                             authority_reader):
    from behave.sim import contract_of
    from behave.sim.declarations import _task_facts_at, declared_contract

    base = _contract()
    (tmp_path / "spec.md").write_text(INTERFACE)
    (tmp_path / "behavior.py").write_text(VARIABLE_CARDINALITY)
    manifest = tmp_path / "task.toml"
    manifest.write_text(_task_text(base, VARIABLE_CARDINALITY))
    calls = []

    def elaborate(source, name):
        calls.append((source, name))
        return base

    monkeypatch.setattr(contract_of, "contract_of_spec", elaborate)

    def read():
        if authority_reader:
            declarations, contract = _task_facts_at(tmp_path, require_declarations=True)
            assert declarations.generated["fields_rederived"] == 3
            return contract
        return api.contract_of_task(tmp_path)

    assert read().as_dict() == declared_contract(base, declarations_at(tmp_path)).as_dict()
    # The new path elaborates once; the independent declarations read above once.
    assert calls == [(INTERFACE, tmp_path.name)] * 2
    calls.clear()
    assert read().endpoint("out").matching is api.Matching.ORDERED
    assert calls == [(INTERFACE, tmp_path.name)]

    # Next invocation observes changed source; cached results never cross calls.
    (tmp_path / "spec.md").write_text(INTERFACE + "\nnew revision")
    read()
    assert calls[-1] == (INTERFACE + "\nnew revision", tmp_path.name)
    assert len(calls) == 2
    # Reusing the elaboration does not bypass rederivation of the claimed fields.
    manifest.write_text(_task_text(base, VARIABLE_CARDINALITY).replace(
        'matching = "ordered"', 'matching = "ordered"\nanswers_per_transaction = 1'))
    with pytest.raises(Refused, match="declarations-do-not-match-the-reference"):
        read()


def test_missing_declarations_authority_does_not_elaborate(tmp_path, monkeypatch):
    from behave.sim import contract_of
    from behave.sim.declarations import _task_facts_at

    def unexpected(*args):
        pytest.fail("a declarations-required call must stop before elaboration")

    monkeypatch.setattr(contract_of, "contract_of_spec", unexpected)
    assert _task_facts_at(tmp_path, require_declarations=True) == (None, None)


def test_task_read_rechecks_interface_identity(tmp_path, monkeypatch):
    from behave.sim import contract_of

    base = _contract()
    (tmp_path / "spec.md").write_text(INTERFACE)
    (tmp_path / "behavior.py").write_text(VARIABLE_CARDINALITY)
    (tmp_path / "task.toml").write_text(_task_text(base, VARIABLE_CARDINALITY))
    monkeypatch.setattr(contract_of, "contract_of_spec", lambda *args: base)
    api.contract_of_task(tmp_path)
    changed = api.TaskContract(base.endpoints,
        [api.ClockDomain("clk", "clk", "rst", edge="falling")])
    monkeypatch.setattr(contract_of, "contract_of_spec", lambda *args: changed)
    with pytest.raises(Refused, match="declarations-interface-changed"):
        api.contract_of_task(tmp_path)


@pytest.mark.cosim
def test_runtime_rederives_reference_manifest_without_reading_spec_prose(tmp_path):
    from behave.sim.contract_of import contract_of_spec

    behavior = VARIABLE_CARDINALITY
    contract = contract_of_spec(INTERFACE, "reference-rule-test")
    (tmp_path / "spec.md").write_text(INTERFACE)
    (tmp_path / "behavior.py").write_text(behavior)
    (tmp_path / "task.toml").write_text(_task_text(contract, behavior))

    got = declarations_at(tmp_path)
    assert got.for_endpoint("out")["matching"] is api.Matching.ORDERED
    assert got.for_endpoint("out").get("answers_per_transaction") is None

    # Prose is not an authority for these fields.  The explicit interface is unchanged.
    (tmp_path / "spec.md").write_text(
        "This prose falsely claims one answer per request.\n\n" + INTERFACE)
    assert declarations_at(tmp_path).for_endpoint("out")["matching"] \
        is api.Matching.ORDERED

    task = (tmp_path / "task.toml").read_text()
    (tmp_path / "task.toml").write_text(
        task.replace('matching = "ordered"',
                     'matching = "ordered"\nanswers_per_transaction = 1'))
    with pytest.raises(Refused, match="declarations-do-not-match-the-reference"):
        declarations_at(tmp_path)


@pytest.mark.cosim
def test_reference_manifest_binds_behavior_interface_and_exact_provenance(tmp_path):
    from behave.sim.contract_of import contract_of_spec

    behavior = VARIABLE_CARDINALITY
    contract = contract_of_spec(INTERFACE, "reference-rule-identity-test")
    task = _task_text(contract, behavior)
    (tmp_path / "spec.md").write_text(INTERFACE)
    (tmp_path / "behavior.py").write_text(behavior)
    (tmp_path / "task.toml").write_text(task)

    (tmp_path / "behavior.py").write_text(behavior + "\n# changed\n")
    with pytest.raises(Refused, match="declarations-behavior-changed"):
        declarations_at(tmp_path)

    (tmp_path / "behavior.py").write_text(behavior)
    (tmp_path / "spec.md").write_text(INTERFACE.replace("[7:0]", "[8:0]"))
    with pytest.raises(Refused, match="declarations-interface-changed"):
        declarations_at(tmp_path)

    (tmp_path / "spec.md").write_text(INTERFACE)
    (tmp_path / "task.toml").write_text(
        task.replace(f"generator = {GENERATOR}",
                     f"generator = {GENERATOR}\npolicy_digest = \"forged\""))
    with pytest.raises(Refused, match="declarations-unknown-provenance-field"):
        declarations_at(tmp_path)

    (tmp_path / "task.toml").write_text(
        task + "\n[clock]\nreset_restores = true\n")
    with pytest.raises(Refused, match="declarations-reset-claim-not-derived"):
        declarations_at(tmp_path)
