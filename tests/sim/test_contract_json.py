"""The task contract has one strict JSON boundary for isolated workers."""

import copy
import hashlib

import pytest

from behave.sim.core.contract import (
    TASK_CONTRACT_VERSION,
    Adapter,
    ClockDomain,
    Direction,
    Endpoint,
    Field,
    FiniteType,
    Kind,
    Matching,
    ResetPolicy,
    TaskContract,
    ready_valid,
)
from behave.sim.core.memory import MemoryInterface, MemoryPort
from behave.sim.core.physical_value import PHYSICAL_VALUE_LAYOUT_VERSION
from behave.sim.core.refusal import Refused
from behave.sim.core.stimulus import canonical_json
from behave.sim.declarations import Declarations, declarations_at, declared_contract


def _contract():
    source = Endpoint(
        "request", Direction.IN,
        (Field("tag", FiniteType(4), "in_tag"),
         Field("value", FiniteType(9, signed=True), "in_value")),
        ready_valid(Direction.IN, "in_valid", "in_ready"), "core",
        matching=Matching.IDENTIFIER, identifier="tag", max_outstanding=3,
        response_deadline=7, answers_per_transaction=1,
        terminal_quiescence=True, kind=Kind.TRANSACTION,
        reset_policy=ResetPolicy.CLEARS,
    )
    sink = Endpoint(
        "response", Direction.OUT,
        (Field("tag", FiniteType(4), "out_tag"),
         Field("value", FiniteType(9, signed=True), "out_value")),
        ready_valid(Direction.OUT, "out_valid", "out_ready"), "core",
        kind=Kind.TRANSACTION,
    )
    return TaskContract(
        (source, sink),
        (ClockDomain("core", "clk", "rst", reset_active_low=True,
                     reset_hold_cycles=4, reset_restores=False),),
        source="tests/spec.md", authority="task",
    )


def _direct_endpoint(**overrides):
    values = {
        "name": "request",
        "direction": Direction.IN,
        "fields": [Field("value", FiniteType(8), "in_value")],
        "adapter": ready_valid(Direction.IN, "in_valid", "in_ready"),
        "clock": "core",
        "matching": Matching.ORDERED,
        "kind": Kind.TRANSACTION,
    }
    values.update(overrides)
    return Endpoint(**values)


def test_contract_json_round_trip_preserves_the_complete_identity():
    contract = _contract()

    record = contract.as_dict()
    decoded = TaskContract.from_dict(record)

    assert record["version"] == TASK_CONTRACT_VERSION
    assert record["physical_value_layout"] == PHYSICAL_VALUE_LAYOUT_VERSION
    assert decoded.as_dict() == contract.as_dict()
    assert decoded.digest == contract.digest
    assert decoded is not contract


def test_direct_constructors_accept_the_documented_enum_strings_and_type_keyword():
    field = Field(name="value", type=FiniteType(8), port="in_value")
    endpoint = Endpoint(
        name="request", direction="in", fields=[field],
        adapter=ready_valid("in", "in_valid", "in_ready"), clock="core",
        matching="ordered", kind="transaction", reset_policy="clears")
    contract = TaskContract(
        endpoints=[endpoint], clocks=[ClockDomain("core", "clk", "rst")],
        source="", authority="task")

    assert endpoint.direction is Direction.IN
    assert endpoint.matching is Matching.ORDERED
    assert endpoint.kind is Kind.TRANSACTION
    assert endpoint.reset_policy is ResetPolicy.CLEARS
    assert TaskContract.from_dict(contract.as_dict()) == contract


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: FiniteType("8"), "contract-type-width-not-integer"),
        (lambda: FiniteType(8, signed=1),
         "contract-type-signedness-invalid"),
        (lambda: Field(1, FiniteType(8)), "contract-field-name-invalid"),
        (lambda: Field("value", object()), "contract-field-type-invalid"),
        (lambda: Field("value", FiniteType(8), port=1),
         "contract-field-port-invalid"),
        (lambda: Adapter(1, 1), "contract-adapter-name-invalid"),
        (lambda: Adapter("ready-valid", "1"),
         "contract-adapter-version-invalid"),
        (lambda: Adapter(
            "ready-valid", 1, environment_controls=(name for name in ("valid",))),
         "contract-adapter-signal-invalid"),
        (lambda: _direct_endpoint(direction=1), "contract-direction-invalid"),
        (lambda: _direct_endpoint(fields=(object(),)),
         "contract-endpoint-fields-invalid"),
        (lambda: _direct_endpoint(adapter=object()),
         "contract-endpoint-adapter-invalid"),
        (lambda: _direct_endpoint(matching="unknown"),
         "contract-matching-invalid"),
        (lambda: _direct_endpoint(kind="unknown"), "contract-kind-invalid"),
        (lambda: _direct_endpoint(reset_policy="unknown"),
         "contract-reset-policy-invalid"),
        (lambda: _direct_endpoint(max_outstanding=True),
         "contract-count-not-integer"),
        (lambda: _direct_endpoint(response_deadline=True),
         "contract-response-deadline-invalid"),
        (lambda: _direct_endpoint(answers_per_transaction=True),
         "contract-answer-ratio-invalid"),
        (lambda: _direct_endpoint(terminal_quiescence=1),
         "contract-terminal-quiescence-invalid"),
        (lambda: ClockDomain("core", "clk", "rst", reset_active_low=1),
         "contract-reset-polarity-invalid"),
        (lambda: TaskContract(
            (endpoint for endpoint in ()),
            [ClockDomain("core", "clk", "rst")]),
         "contract-endpoints-invalid"),
        (lambda: TaskContract(
            [object()], [ClockDomain("core", "clk", "rst")]),
         "contract-endpoints-invalid"),
        (lambda: TaskContract([], [object()]), "contract-clocks-invalid"),
        (lambda: TaskContract(
            [], [ClockDomain("core", "clk", "rst")], source=1),
         "contract-source-invalid"),
        (lambda: TaskContract(
            [], [ClockDomain("core", "clk", "rst")], authority=""),
         "contract-authority-invalid"),
    ],
)
def test_direct_contract_constructors_do_not_coerce_task_facts(build, reason):
    with pytest.raises(Refused, match=reason):
        build()


def test_task_contract_wire_version_is_not_part_of_the_semantic_digest():
    contract = _contract()
    semantic = dict(contract.as_dict())
    semantic.pop("version")
    semantic.pop("source")
    # No explicit grouping adds no semantic fact to a legacy v5 contract.
    assert semantic.pop("protocol_instances") == []
    assert semantic.pop("observation_goals") == []
    assert semantic.pop("transaction_monitors") == []
    assert semantic.pop("latency_goals") == []
    assert semantic.pop("operation_rules") == []
    assert semantic.pop("source_read_rules") == []

    assert contract.digest == hashlib.sha256(
        canonical_json(semantic).encode()).hexdigest()


def test_physical_value_layout_is_semantic_and_cannot_be_tampered():
    record = copy.deepcopy(_contract().as_dict())
    record["physical_value_layout"] = "packed-value/0"

    with pytest.raises(Refused, match="contract-physical-value-layout-unsupported"):
        TaskContract.from_dict(record)


@pytest.mark.parametrize("version", [None, "task-contract/1"])
def test_contract_json_refuses_an_unversioned_or_old_wire_image(version):
    record = copy.deepcopy(_contract().as_dict())
    if version is None:
        record.pop("version")
    else:
        record["version"] = version

    with pytest.raises(Refused, match="contract-version-unsupported"):
        TaskContract.from_dict(record)


def test_clock_active_edge_is_part_of_the_exact_contract_identity():
    rising = _contract()
    clock = rising.clocks[0]
    falling = TaskContract(
        rising.endpoints,
        (ClockDomain(
            clock.name, clock.clock, clock.reset,
            reset_active_low=clock.reset_active_low,
            reset_hold_cycles=clock.reset_hold_cycles,
            reset_restores=clock.reset_restores,
            edge="falling"),),
        source=rising.source, authority=rising.authority)

    assert ClockDomain.from_dict(falling.clocks[0].as_dict()).edge == "falling"
    assert rising.digest != falling.digest
    assert rising.as_dict()["clocks"][0]["edge"] == "rising"
    assert falling.as_dict()["clocks"][0]["edge"] == "falling"


def test_task_declarations_bind_each_domain_edge_without_reading_the_rtl():
    base = TaskContract(
        (),
        (ClockDomain("a", "a_clk", "a_rst"),
         ClockDomain("b", "b_clk", "b_rst")),
        source="declared interface")
    got = declared_contract(
        base, Declarations(clock_edges={"a": "rising", "b": "falling"}))

    assert {clock.name: clock.edge for clock in got.clocks} == {
        "a": "rising", "b": "falling"}
    assert got.digest != base.digest

    with pytest.raises(Refused, match="declarations-clock-not-in-contract"):
        declared_contract(base, Declarations(clock_edges={"unknown": "falling"}))


def test_task_declarations_preserve_undeclared_contract_semantics():
    base = TaskContract(
        (),
        (ClockDomain("core", "clk", "rst", reset_restores=True),),
        source="declared interface",
        memories=(MemoryInterface(
            "ram", 4, 8, "core", (MemoryPort("rw"),)),),
    )

    got = declared_contract(base, Declarations(clock_edges={"core": "falling"}))

    assert got.memories == base.memories
    assert got.clocks[0].reset_restores is True
    assert got.as_dict()["memories"] == base.as_dict()["memories"]


def test_task_manifest_has_an_explicit_per_domain_edge_boundary(tmp_path):
    manifest = """
[written_by_hand]
who = "test"
why = "exercise the task-owned active-edge declaration"

[clock.edges]
a = "rising"
b = "falling"
""".lstrip()
    (tmp_path / "task.toml").write_text(manifest)

    assert dict(declarations_at(tmp_path).clock_edges.items()) == {
        "a": "rising", "b": "falling"}

    (tmp_path / "task.toml").write_text(
        manifest.replace('b = "falling"', 'b = "either"'))
    with pytest.raises(Refused, match="declarations-clock-edge-invalid"):
        declarations_at(tmp_path)


def test_generated_provenance_cannot_certify_an_underived_clock_edge(
        tmp_path, monkeypatch):
    from behave.sim import declarations

    monkeypatch.setattr(
        declarations, "_generated_provenance",
        lambda *_args, **_kwargs: {"written_by": "tools/declare_tasks.py"})
    (tmp_path / "task.toml").write_text("""
[generated]
generator = 4
spec_digest = "test"

[clock.edges]
core = "falling"
""".lstrip())

    with pytest.raises(Refused, match="declarations-clock-edge-not-derived"):
        declarations_at(tmp_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row.update(extra=True), "exact fields"),
        (lambda row: row["clocks"][0].update(reset_hold_cycles=True),
         "polarity/hold"),
        (lambda row: row["endpoints"][0]["fields"][0]["type"].update(width=True),
         "width/signed"),
        (lambda row: row["endpoints"][0]["adapter"].update(version=True),
         "name/version"),
        (lambda row: row["endpoints"][0].update(max_outstanding=True),
         "max_outstanding"),
    ],
)
def test_contract_json_refuses_unknown_fields_and_python_coercions(mutate, message):
    row = copy.deepcopy(_contract().as_dict())
    mutate(row)

    with pytest.raises(TypeError, match=message):
        TaskContract.from_dict(row)
