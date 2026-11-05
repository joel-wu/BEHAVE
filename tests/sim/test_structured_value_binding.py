"""Explicit logical schemas bound to physical SolverIR leaves."""

from __future__ import annotations

import json
import pickle

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import encoder, interp, typecheck as types
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.exact_service import _decode_replay, _wire_rows
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from behave.sim.behavior.signature import ProcedureContract, ProcedureSignature
from behave.sim.symbolic.value_binding import (
    SolverLeafPath,
    ValueBinding,
    ValueBindingError,
)
from behave.sim.symbolic.value_layout import ValueLayoutError, layout_of_value_type
from behave.sim.core.contract import Field, FiniteType, ValueType
from behave.sim.core.instance import Replay, StepOutput

U4 = ValueType.integer(FiniteType(4))
ROW = ValueType.record({"flag": ValueType.boolean(), "x": U4})
PACKET = ValueType.record({
    "meta": ValueType.record({
        "ok": ValueType.boolean(),
        "tag": ValueType.enumeration(("idle", "run")),
    }),
    "rows": ValueType.array(2, ROW),
})


def _leaf_binding(name, value_type, prefix):
    return ValueBinding(name, value_type, tuple(
        (leaf.path, SolverLeafPath(f"{prefix}{index}"))
        for index, leaf in enumerate(layout_of_value_type(value_type).leaves())
    ))


def _class(source):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    return namespace["M"]


def test_value_type_wire_identity_and_enum_tags_are_explicit_and_stable():
    declared = ValueType.record({
        "mode": ValueType.enumeration(("idle", "run", "fault")),
        "rows": ValueType.array(2, ROW),
    })

    assert declared.fields == tuple(sorted(declared.fields))
    assert dict(dict(declared.fields)["mode"].tag_map) == {
        "idle": 0, "run": 1, "fault": 2}
    assert ValueType.from_dict(declared.as_dict()) == declared
    assert pickle.loads(pickle.dumps(declared)) == declared
    assert Field("payload", declared).type is declared

    image = declared.as_dict()
    image["fields"][0]["type"]["tag_map"] = [
        ["idle", 1], ["run", 0], ["fault", 2]]
    with pytest.raises(ValueError, match="explicit ordinals"):
        ValueType.from_dict(image)
    with pytest.raises(ValueError, match="explicit ordinals"):
        ValueType.enumeration(
            ("idle", "run"), {"idle": 1, "run": 0})
    with pytest.raises(ValueError, match="exactly its members"):
        ValueType.enumeration(
            ("idle", "run"), {"idle": 0, "run": 1, "ghost": 2})
    with pytest.raises(ValueError, match="exact string/integer"):
        ValueType.enumeration(
            ("idle", "run"), (("idle", False), ("run", 1)))

    noncanonical = declared.as_dict()
    noncanonical["fields"].reverse()
    with pytest.raises(ValueError, match="canonical field order"):
        ValueType.from_dict(noncanonical)


def test_binding_keeps_logical_record_paths_separate_from_solver_paths():
    rows = ValueType.array(2, ROW)
    binding = ValueBinding("rows", rows, (
        ((0, "flag"), SolverLeafPath("row_flags", (0,))),
        ((0, "x"), SolverLeafPath("row_x", (0,))),
        ((1, "flag"), SolverLeafPath("row_flags", (1,))),
        ((1, "x"), SolverLeafPath("row_x", (1,))),
    ))
    value = [{"x": 3, "flag": False}, {"flag": True, "x": 7}]

    assert binding.solver_types() == (
        ("row_flags", types.Array(2, types.BOOLEAN)),
        ("row_x", types.Array(2, types.INTEGER)),
    )
    physical = binding.solver_values(value)
    assert physical == {"row_flags": [False, True], "row_x": [3, 7]}
    assert binding.reconstruct(physical) == [
        {"flag": False, "x": 3}, {"flag": True, "x": 7}]
    assert ValueBinding.from_dict(binding.as_dict()) == binding
    assert pickle.loads(pickle.dumps(binding)) == binding

    with pytest.raises(ValueBindingError) as caught:
        ValueBinding("rows", rows, binding.leaf_bindings[:-1])
    assert caught.value.code == "value-binding-leaf-set-mismatch"
    malformed = list(binding.leaf_bindings)
    malformed[-1] = (malformed[-1][0], malformed[0][1])
    with pytest.raises(ValueBindingError) as caught:
        ValueBinding("rows", rows, malformed)
    assert caught.value.code == "value-binding-solver-path-duplicate"

    mixed = list(binding.leaf_bindings)
    mixed[1] = (mixed[1][0], SolverLeafPath("row_flags", (1,)))
    mixed[2] = (mixed[2][0], SolverLeafPath("row_x", (0,)))
    with pytest.raises(ValueBindingError) as caught:
        ValueBinding("rows", rows, mixed)
    assert caught.value.code == "value-binding-solver-array-heterogeneous"


def test_binding_assembles_declared_physical_values_without_runtime_inference():
    binding = ValueBinding("rows", ValueType.array(2, ROW), (
        ((0, "flag"), SolverLeafPath("flags", (0,))),
        ((0, "x"), SolverLeafPath("data", (0,))),
        ((1, "flag"), SolverLeafPath("flags", (1,))),
        ((1, "x"), SolverLeafPath("data", (1,))),
    ))
    logical = {
        (0, "flag"): False, (0, "x"): 3,
        (1, "flag"): True, (1, "x"): 7,
    }

    assert binding.assemble_solver_values(logical) == {
        "data": [3, 7], "flags": [False, True]}
    with pytest.raises(ValueBindingError) as caught:
        binding.assemble_solver_values({(0, "flag"): False})
    assert caught.value.code == "value-binding-logical-image-mismatch"


@pytest.mark.parametrize(("paths", "code"), [
    (((), (0,)), "value-binding-solver-shape-overlap"),
    (((0,), (2,)), "value-binding-solver-array-sparse"),
    (((0,), (0, 0)), "value-binding-solver-shape-overlap"),
    (((0, 0), (0, 2)), "value-binding-solver-array-sparse"),
    (((0,), (1, 0)), "value-binding-solver-array-heterogeneous"),
    (((0, 0), (1, 0), (1, 1)), "value-binding-solver-array-heterogeneous"),
    (((0,), (1 << 60,)), "value-binding-solver-array-sparse"),
    (((0, 0), (0, 1 << 60)), "value-binding-solver-array-sparse"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_physical_path_validation_keeps_typed_refusals(paths, code, reverse):
    rows = [((index,), SolverLeafPath("data", path)) for index, path in enumerate(paths)]
    with pytest.raises(ValueBindingError) as caught:
        ValueBinding("rows", ValueType.array(len(paths), U4),
                     reversed(rows) if reverse else rows)
    assert caught.value.code == code
    assert "'data'" in str(caught.value)


@pytest.mark.parametrize(("paths", "code"), [
    (((), (0,), (2,)), "value-binding-solver-shape-overlap"),
    (((0,), (0, 0), (2,)), "value-binding-solver-array-sparse"),
    (((0,), (0, 0), (1,)), "value-binding-solver-shape-overlap"),
    (((0, 0), (0, 2), (1,)), "value-binding-solver-array-sparse"),
])
def test_physical_path_validation_preserves_parent_then_child_error_order(paths, code):
    with pytest.raises(ValueBindingError) as caught:
        ValueBinding("rows", ValueType.array(len(paths), U4), tuple(
            ((index,), SolverLeafPath("data", path)) for index, path in enumerate(paths)))
    assert caught.value.code == code


@pytest.mark.parametrize("fail_at", [None, 1, 2, 3])
def test_array_factory_keeps_physical_cell_order_and_failure_short_circuit(fail_at):
    # Logical leaf order is deliberately unrelated to physical cell order.
    paths = ((1, 1), (0, 1), (1, 0), (0, 0))
    binding = ValueBinding("rows", ValueType.array(4, U4), tuple(
        ((index,), SolverLeafPath("data", path)) for index, path in enumerate(paths)))
    payload = [object() for _ in paths]
    visits = []
    left = (payload[3], payload[1])
    right = (payload[2], payload[0])
    expected = [left, right, (left, right)]

    def array_factory(children):
        assert type(children) is tuple
        visits.append(children)
        if len(visits) == fail_at:
            raise RuntimeError("factory stopped")
        return children

    values = {(index,): value for index, value in enumerate(payload)}
    if fail_at is None:
        assert binding.assemble_solver_values(values, array_factory=array_factory) == {
            "data": (left, right)}
        assert visits == expected
    else:
        with pytest.raises(RuntimeError, match="factory stopped"):
            binding.assemble_solver_values(values, array_factory=array_factory)
        assert visits == expected[:fail_at]


def test_assembly_sorts_variables_but_never_sends_scalars_to_array_factory():
    binding = ValueBinding("rows", ValueType.array(3, U4), (
        ((0,), SolverLeafPath("zeta")),
        ((1,), SolverLeafPath("alpha", (1,))),
        ((2,), SolverLeafPath("alpha", (0,))),
    ))
    visits = []

    def array_factory(children):
        visits.append(children)
        return list(children)

    physical = binding.assemble_solver_values(
        {(0,): 7, (1,): 2, (2,): 1}, array_factory=array_factory)
    assert tuple(physical) == ("alpha", "zeta")
    assert physical == {"alpha": [1, 2], "zeta": 7}
    assert visits == [(1, 2)]


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize(("declaration", "value"), [
    (ValueType.boolean(), True),
    (ValueType.integer(FiniteType(4, signed=True)), -3),
    (ValueType.enumeration(("idle", "run")), "run"),
    (ValueType.array(2, U4), (1, 15)),
    (ValueType.array(0, U4), []),
    (ValueType.array(2, ValueType.array(0, U4)), [[], []]),
    (ValueType.array(2, ValueType.array(2, ValueType.boolean())),
     [[True, False], [False, True]]),
    (ValueType.array(2, ROW), [{"x": 3, "flag": True}, {"x": 15, "flag": False}]),
])
def test_concrete_materialization_uses_declared_paths_and_exact_leaf_types(
        declaration, value, packed):
    leaves = layout_of_value_type(declaration).leaves()
    groups = {}
    rows = []
    for index, leaf in reversed(tuple(enumerate(leaves))):
        if packed:
            name = "bools" if leaf.semantic_type == types.BOOLEAN else "ints"
            slot = groups.get(name, 0)
            groups[name] = slot + 1
            solver = SolverLeafPath(name, (slot,))
        else:
            solver = SolverLeafPath(f"leaf_{index}")
        rows.append((leaf.path, solver))
    binding = ValueBinding("value", declaration, rows)
    physical = binding.solver_values(value)
    assert tuple(physical) == tuple(sorted(physical))
    for path, scalar in binding.flatten(value):
        assert path.read(physical) == scalar
        assert type(path.read(physical)) is type(scalar)
    expected = binding.layout.reconstruct(binding.layout.flatten(value))
    assert binding.reconstruct(physical) == expected
    assert pickle.loads(pickle.dumps(binding)).solver_values(value) == physical
    assert ValueBinding.from_dict(binding.as_dict()).solver_values(value) == physical


@pytest.mark.parametrize(("declaration", "value", "code"), [
    (ValueType.boolean(), 1, "value-layout-boolean-mismatch"),
    (U4, True, "value-layout-integer-mismatch"),
    (U4, 16, "value-layout-integer-outside-carrier"),
    (ValueType.enumeration(("idle", "run")), "ghost", "value-layout-enum-member-invalid"),
    (ValueType.array(2, U4), [1], "value-layout-array-shape-mismatch"),
    (ROW, {"x": 1}, "value-layout-record-schema-mismatch"),
    (ROW, {"x": 1, "flag": 1}, "value-layout-boolean-mismatch"),
])
def test_invalid_concrete_values_are_rejected_before_physical_assembly(
        monkeypatch, declaration, value, code):
    binding = _leaf_binding("value", declaration, "leaf_")

    def unexpected_assembly(*_args, **_kwargs):
        pytest.fail("invalid concrete value reached physical assembly")

    monkeypatch.setattr(ValueBinding, "assemble_solver_values", unexpected_assembly)
    with pytest.raises(ValueLayoutError) as caught:
        binding.solver_values(value)
    assert caught.value.code == code


def test_materialization_reuses_the_assembler_without_retyping_or_array_aliases(monkeypatch):
    binding = ValueBinding.direct("tiles", ValueType.array(2, ValueType.array(2, U4)))
    assemble = ValueBinding.assemble_solver_values
    assembled = []

    def record_assembly(self, logical_values, **kwargs):
        values = dict(logical_values)
        assembled.append(values)
        return assemble(self, values, **kwargs)

    def unexpected_retyping(*_args, **_kwargs):
        pytest.fail("an already validated binding repeated physical type inference")

    monkeypatch.setattr(ValueBinding, "assemble_solver_values", record_assembly)
    monkeypatch.setattr(ValueBinding, "solver_types", unexpected_retyping)
    row = [1, 2]
    value = [row, row]
    first = binding.solver_values(value)
    second = binding.solver_values(value)
    assert assembled == [{(0, 0): 1, (0, 1): 2, (1, 0): 1, (1, 1): 2}] * 2
    first["tiles"][0][0] = 9
    assert first["tiles"] == [[9, 2], [1, 2]]
    assert second["tiles"] == value == [[1, 2], [1, 2]]
    assert first["tiles"][0] is not first["tiles"][1]


@pytest.mark.parametrize("declaration", [
    U4, ValueType.boolean(), ValueType.array(0, U4),
    ValueType.array(3, ValueType.array(2, ValueType.boolean())), ROW, PACKET,
])
def test_binding_metadata_is_retained_and_rebuilt_only_at_transport(monkeypatch, declaration):
    from behave.sim.symbolic import value_binding

    binding = _leaf_binding("value", declaration, "leaf_")
    physical_types = binding.solver_types()
    variables = binding.solver_variables
    image = binding.as_dict()
    clones = [pickle.loads(pickle.dumps(binding)), ValueBinding.from_dict(image)]
    assert binding.__reduce__()[1] == ("value", declaration, binding.leaf_bindings)
    assert all(clone == binding and hash(clone) == hash(binding) for clone in clones)

    def unexpected_inference(*_args, **_kwargs):
        pytest.fail("reading immutable metadata repeated physical path inference")

    monkeypatch.setattr(value_binding, "_fold_solver_paths", unexpected_inference)
    for _ in range(4):
        assert binding.solver_types() is physical_types
        assert binding.solver_variables is variables
        for clone in clones:
            assert clone.solver_types() == physical_types
            assert clone.solver_variables == variables
            assert clone.as_dict() == image


@pytest.mark.parametrize("operation", ["assign", "delete", "reinitialize"])
def test_binding_cannot_invalidate_retained_metadata(operation):
    binding = ValueBinding.direct("value", ValueType.array(2, U4))
    identity = binding.identity
    if operation == "assign":
        with pytest.raises(AttributeError, match="immutable"):
            binding.name = "changed"
    elif operation == "delete":
        with pytest.raises(AttributeError, match="immutable"):
            del binding._solver_types
    else:
        with pytest.raises(AttributeError, match="immutable"):
            binding.__init__("changed", U4, (((), SolverLeafPath("changed")),))
    assert binding.identity == identity
    assert binding.solver_variables == ("value",)
    assert binding.solver_types() == (("value", types.Array(2)),)


@pytest.mark.parametrize("transport", ["original", "pickle", "reconstruct"])
def test_contract_role_groups_are_derived_without_changing_identity(transport):
    bindings = tuple((role, ValueBinding.direct(name, U4)) for role, name in (
        ("output", "y"), ("local", "tmp"), ("input", "z"),
        ("state", "state"), ("input", "a")))
    contract = ProcedureContract(
        (("state", types.INTEGER),), (("a", types.INTEGER), ("z", types.INTEGER)),
        (("tmp", types.INTEGER),), (("y", types.INTEGER),),
        (("a", U4), ("z", U4)), (("y", U4),), value_bindings=bindings)
    identity = contract.identity
    if transport == "pickle":
        contract = pickle.loads(pickle.dumps(contract))
    elif transport == "reconstruct":
        contract = ProcedureContract(*identity)
    assert contract.identity == identity
    assert contract.__reduce__()[1] == identity
    assert tuple(binding.name for binding in contract._input_bindings) == ("a", "z")
    assert tuple(binding.name for binding in contract._output_bindings) == ("y",)
    for _ in range(3):
        assert contract.validate_inputs({"z": 7, "a": 2}) == {"a": 2, "z": 7}
        assert contract.solver_input_bounds() == (("a", (0, 15)), ("z", (0, 15)))
        assert contract.reconstruct_outputs(2, {"y": [2, 7]}, capacity=2) == {"y": (2, 7)}
    with pytest.raises(AttributeError):
        contract._input_bindings = ()


STRUCTURED_INPUT = """\
class M:
    def reset(self):
        pass
    def process(self, row):
        return {"y": row["x"] + int(row["flag"])}
"""


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("compiler_names", [False, True])
def test_procedure_signature_binds_one_logical_record_input_to_leaf_inputs(
        lowerer, compiler_names):
    names = ("_t1", "_t2") if compiler_names else ("row_flag", "row_x")
    row = ValueBinding("row", ROW, (
        (("flag",), SolverLeafPath(names[0])),
        (("x",), SolverLeafPath(names[1])),
    ))
    signature = ProcedureSignature(
        (("row", row),), (("y", FiniteType(5)),))
    proc = lower_with_state_ranges(
        STRUCTURED_INPUT, outputs=["y"], signature=signature, lowerer=lowerer)

    assert tuple(proc.inputs) == names
    assert dict(proc.semantics.input_types) == {
        names[0]: types.BOOLEAN,
        names[1]: types.INTEGER,
    }
    assert proc.semantics.value_binding("input", "row") == row
    _post, output, _hits, error = interp.run_step(
        proc, {"row": {"x": 3, "flag": True}})
    assert error == ""
    assert output.transactions == [{"y": 4}]
    assert signature.as_dict()["inputs"]["row"] == {
        "value_type": ROW.as_dict(), "binding": row.as_dict()}
    assert encoder.verify_encoding(proc, env=proc.state_ranges) == []
    symbolic = encoder.encode_step(proc, env=proc.state_ranges)
    solver = z3.Solver()
    solver.add(symbolic.constraint)
    for value in range(16):
        for flag in (False, True):
            _state, result, _hits, failure = interp.run_step(
                proc, {"row": {"x": value, "flag": flag}})
            assert not failure
            expected = value + int(flag)
            assert result.transactions == [{"y": expected}]
            solver.push()
            solver.add(symbolic.pre[names[0]] == int(flag),
                       symbolic.pre[names[1]] == value)
            assert solver.check() == z3.sat
            solver.add(z3.Or(z3.Not(symbolic.ok),
                             symbolic.post[encoder.COUNT] != 1,
                             symbolic.post["y"][0] != expected))
            assert solver.check() == z3.unsat
            solver.pop()


SCALAR_REBOUND_INPUT = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        a = a + 1
        a += 2
        return {"y": a}
"""


def test_rebound_scalar_input_uses_a_private_typed_local():
    signature = ProcedureSignature(
        (("a", FiniteType(4)),), (("y", FiniteType(5)),))
    proc = lower_with_state_ranges(
        SCALAR_REBOUND_INPUT, outputs=["y"], signature=signature)

    public = proc.semantics.value_binding("input", "a")
    private = proc.semantics.value_binding("local", "a")
    assert set(public.solver_variables).isdisjoint(private.solver_variables)
    assert tuple(proc.inputs) == public.solver_variables
    post, output, _hits, error = interp.run_step(proc, {"a": 3})
    assert error == ""
    assert post == {}
    assert output.transactions == [{"y": 6}]


def test_inlined_helper_formal_does_not_alias_a_same_named_public_input():
    source = """\
def mask(value, bits):
    return value & ((1 << bits) - 1)

class M:
    def reset(self):
        pass
    def process(self, value, bits):
        return {"y": mask(value, 4)}
"""
    signature = ProcedureSignature(
        (("value", FiniteType(8)), ("bits", FiniteType(8))),
        (("y", FiniteType(8)),))
    proc = lower_with_state_ranges(
        source, outputs=["y"], signature=signature)

    native = _class(source)()
    native.reset()
    assert native.process(value=7, bits=1) == {"y": 7}
    _post, output, _hits, error = interp.run_step(
        proc, {"value": 7, "bits": 1})
    assert error == ""
    assert output.transactions == [{"y": 7}]


def test_inlined_record_formal_does_not_alias_a_same_named_public_input():
    source = """\
def field(row):
    return row["x"]

class M:
    def reset(self):
        pass
    def process(self, row):
        return {"y": field({"flag": False, "x": 7})}
"""
    row = ValueBinding("row", ROW, (
        (("flag",), SolverLeafPath("row_flag")),
        (("x",), SolverLeafPath("row_x")),
    ))
    signature = ProcedureSignature(
        (("row", row),), (("y", FiniteType(5)),))
    proc = lower_with_state_ranges(
        source, outputs=["y"], signature=signature)

    native = _class(source)()
    native.reset()
    assert native.process(row={"flag": True, "x": 1}) == {"y": 7}
    _post, output, _hits, error = interp.run_step(
        proc, {"row": {"flag": True, "x": 1}})
    assert error == ""
    assert output.transactions == [{"y": 7}]


COMPOSITE_REBOUND_INPUT = """\
class M:
    def reset(self):
        pass
    def process(self, row, cells):
        row = {"flag": not row["flag"], "x": row["x"] + 1}
        row["x"] += int(row["flag"])
        cells[0] = row["x"]
        cells = [cells[1], cells[0] + 1]
        return {"y": cells[0] + cells[1] + int(row["flag"])}
"""


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("compiler_names", [False, True])
def test_record_and_array_input_writes_share_the_private_view_protocol(
        lowerer, compiler_names):
    names = ("_t1", "_t2") if compiler_names else ("row_flag", "row_x")
    row = ValueBinding("row", ROW, (
        (("flag",), SolverLeafPath(names[0])),
        (("x",), SolverLeafPath(names[1])),
    ))
    cells_type = ValueType.array(2, U4)
    cells = ValueBinding.direct("cells", cells_type)
    signature = ProcedureSignature(
        (("row", row), ("cells", cells)), (("y", FiniteType(6)),))
    proc = lower_with_state_ranges(
        COMPOSITE_REBOUND_INPUT, outputs=["y"], signature=signature, lowerer=lowerer)

    for name in ("row", "cells"):
        public = proc.semantics.value_binding("input", name)
        private = proc.semantics.value_binding("local", name)
        assert public.value_type == private.value_type
        assert set(public.solver_variables).isdisjoint(
            private.solver_variables)
    post, output, _hits, error = interp.run_step(
        proc, {"row": {"flag": True, "x": 3}, "cells": [1, 2]})
    assert error == ""
    assert post == {}
    assert output.transactions == [{"y": 7}]


@pytest.mark.parametrize("name", ["shared", "_t1"])
def test_input_physical_leaf_aliases_are_rejected_independently_of_spelling(name):
    with pytest.raises(ValueBindingError) as caught:
        ValueBinding("row", ROW, (
            (("flag",), SolverLeafPath(name)),
            (("x",), SolverLeafPath(name)),
        ))
    assert caught.value.code == "value-binding-solver-path-duplicate"


STRUCTURED_OUTPUT = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        if a == 0:
            return None
        if a == 1:
            return {
                "packet": {
                    "meta": {"ok": True, "tag": "run"},
                    "rows": [
                        {"flag": True, "x": a},
                        {"flag": False, "x": 3},
                    ],
                }
            }
        return [{
            "packet": {
                "meta": {"ok": True, "tag": "run"},
                "rows": [
                    {"flag": True, "x": a},
                    {"flag": False, "x": 3},
                ],
            }
        }, {
            "packet": {
                "meta": {"tag": "idle", "ok": False},
                "rows": [
                    {"x": 4, "flag": False},
                    {"x": 5, "flag": True},
                ],
            }
        }]
"""


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("prefix", ["packet_leaf_", "_t"])
def test_recursive_output_leaf_product_handles_zero_one_and_many_transactions(
        lowerer, prefix):
    packet = _leaf_binding("packet", PACKET, prefix)
    signature = ProcedureSignature(
        (("a", FiniteType(4)),), (("packet", packet),))
    proc = lower_with_state_ranges(
        STRUCTURED_OUTPUT, outputs=["packet"], signature=signature, lowerer=lowerer)

    assert tuple(proc.outputs) == ("packet",)
    assert set(proc.output_vars) == set(packet.solver_variables)
    assert dict(proc.semantics.output_types) == dict(packet.solver_types())
    assert pickle.loads(pickle.dumps(proc.signature)) == signature
    restored = pickle.loads(pickle.dumps(proc))
    assert restored.signature == signature
    assert restored.output_vars == proc.output_vars
    assert restored.semantics == proc.semantics
    assert encoder.verify_encoding(proc, env=proc.state_ranges) == []

    expected = {
        "meta": {"ok": True, "tag": "run"},
        "rows": [
            {"flag": True, "x": 1},
            {"flag": False, "x": 3},
        ],
    }
    _post, silent, _hits, error = interp.run_step(proc, {"a": 0})
    assert error == "" and silent.transactions == []
    _post, one, _hits, error = interp.run_step(proc, {"a": 1})
    assert error == "" and one.transactions == [{"packet": expected}]
    _post, many, _hits, error = interp.run_step(proc, {"a": 2})
    assert error == ""
    assert many.transactions == [
        {"packet": {**expected, "rows": [
            {"flag": True, "x": 2},
            {"flag": False, "x": 3},
        ]}},
        {"packet": {
            "meta": {"ok": False, "tag": "idle"},
            "rows": [
                {"flag": False, "x": 4},
                {"flag": True, "x": 5},
            ],
        }},
    ]

    symbolic = encoder.encode_step(proc, env=proc.state_ranges)
    solver = z3.Solver()
    solver.add(symbolic.constraint, symbolic.ok, symbolic.pre["a"] == 2)
    assert solver.check() == z3.sat
    model = solver.model()

    def decode(value, kind):
        if isinstance(kind, types.Array):
            return [decode(child, kind.elem) for child in value]
        number = model.eval(value, model_completion=True).as_long()
        if kind == types.BOOLEAN:
            return bool(number)
        return number

    symbolic_count = model.eval(
        symbolic.post[encoder.COUNT], model_completion=True).as_long()
    physical = {
        name: tuple(decode(cell, kind) for cell in symbolic.post[name])
        for name, kind in proc.semantics.output_types
    }
    symbolic_logical = proc.semantics.reconstruct_outputs(
        symbolic_count, physical, capacity=proc.output_capacity)
    assert StepOutput(symbolic_count, symbolic_logical) == many


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ('{"ok": True, "tag": "run"}', '{"ok": True}',
         "outputs-disagree-across-returns"),
        ('"tag": "run"', '"tag": "ghost"',
         "enumeration-member-undeclared"),
    ],
)
def test_malformed_recursive_output_is_typed_at_its_declared_leaf(
        old, new, code):
    packet = _leaf_binding("packet", PACKET, "packet_leaf_")
    signature = ProcedureSignature(
        (("a", FiniteType(4)),), (("packet", packet),))
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            STRUCTURED_OUTPUT.replace(old, new), outputs=["packet"],
            signature=signature, bound=1)
    assert caught.value.code == code


def test_recursive_output_codec_rejects_missing_and_out_of_carrier_leaves():
    packet = _leaf_binding("packet", PACKET, "packet_leaf_")
    signature = ProcedureSignature(
        (("a", FiniteType(4)),), (("packet", packet),))
    proc = lower_with_state_ranges(
        STRUCTURED_OUTPUT, outputs=["packet"], signature=signature, bound=1)
    value = {
        "meta": {"ok": True, "tag": "run"},
        "rows": [
            {"flag": True, "x": 2},
            {"flag": False, "x": 3},
        ],
    }
    one = packet.solver_values(value)
    physical = {name: [cell, cell] for name, cell in one.items()}
    missing = dict(physical)
    missing.pop(next(iter(missing)))
    with pytest.raises(ValueError, match="physical output buffers"):
        proc.semantics.reconstruct_outputs(
            1, missing, capacity=proc.output_capacity)

    path = dict(packet.leaf_bindings)[("rows", 0, "x")]
    invalid = {name: list(cells) for name, cells in physical.items()}
    invalid[path.variable][0] = 16
    with pytest.raises(ValueError, match="outside"):
        proc.semantics.reconstruct_outputs(
            1, invalid, capacity=proc.output_capacity)


def test_recursive_outputs_cross_the_exact_json_service_without_flattening():
    value = {
        "meta": {"ok": False, "tag": "idle"},
        "rows": [
            {"flag": False, "x": 3},
            {"flag": True, "x": 4},
        ],
    }
    assert _wire_rows(({"packet": value},)) == [{"packet": value}]
    replay = Replay(
        (), (StepOutput(1, {"packet": (value,)}),),
        state={"done": True}, steps=1)
    image = json.loads(json.dumps(replay.as_dict()))
    rebuilt = _decode_replay(image)
    assert rebuilt.as_dict() == image
    assert rebuilt.outputs[0].transactions == [{"packet": value}]


RECORD_STATE = """\
class M:
    def reset(self):
        self.raw = {"x": 1, "flag": False}
    def process(self, a):
        self.raw["x"] = self.raw["x"] + a
        self.raw["flag"] = not self.raw["flag"]
        return {"y": self.raw["x"] + int(self.raw["flag"])}
"""


def test_declared_record_state_runs_through_leaf_state_and_native_audit():
    proc = lower_with_state_ranges(
        RECORD_STATE, input_widths=[4], outputs=["y"],
        value_types={"raw": ROW})
    binding = proc.semantics.value_binding("state", "raw")

    assert "raw" not in proc.state
    assert set(binding.solver_variables) <= set(proc.state)
    assert binding.reconstruct(proc.initial) == {"flag": False, "x": 1}
    post, output, _hits, error = interp.run_step(proc, {"a": 3})
    assert error == ""
    assert binding.reconstruct(post) == {"flag": True, "x": 4}
    assert output.transactions == [{"y": 5}]
    post, _output, _hits, error = interp.run_step(proc, {"a": 2}, post)
    assert error == ""
    assert binding.reconstruct(post) == {"flag": False, "x": 6}
    assert model_audit(
        proc, _class(RECORD_STATE), [[{"a": 3}, {"a": 2}]]) == []


ARRAY_RECORD_STATE = """\
class M:
    def reset(self):
        self.rows = [
            {"x": 1, "flag": False},
            {"flag": True, "x": 2},
        ]
    def process(self, a):
        i = a & 1
        self.rows[i] = {
            "flag": not self.rows[i]["flag"],
            "x": a,
        }
        return {"y": self.rows[i]["x"] + int(self.rows[i]["flag"])}
"""


def test_declared_array_of_records_supports_dynamic_read_and_whole_row_write():
    rows = ValueType.array(2, ROW)
    proc = lower_with_state_ranges(
        ARRAY_RECORD_STATE, input_widths=[4], outputs=["y"],
        value_types={"rows": rows})
    binding = proc.semantics.value_binding("state", "rows")

    at = dict(proc.initial)
    for value, expected, y in (
            (0, [{"flag": True, "x": 0}, {"flag": True, "x": 2}], 1),
            (3, [{"flag": True, "x": 0}, {"flag": False, "x": 3}], 3),
            (2, [{"flag": False, "x": 2}, {"flag": False, "x": 3}], 2)):
        at, output, _hits, error = interp.run_step(proc, {"a": value}, at)
        assert error == ""
        assert binding.reconstruct(at) == expected
        assert output.transactions == [{"y": y}]
    assert model_audit(
        proc, _class(ARRAY_RECORD_STATE),
        [[{"a": 0}, {"a": 3}, {"a": 2}]]) == []


DIRECT_ARRAY_STATE = """\
class M:
    def reset(self):
        self.cells = [1, 2]
    def process(self, a):
        self.cells = [self.cells[1], self.cells[0]]
        return {"y": self.cells[a & 1]}
"""


def test_declared_plain_array_keeps_the_legacy_direct_solver_path():
    cells = ValueType.array(2, U4)
    proc = lower_with_state_ranges(
        DIRECT_ARRAY_STATE, input_widths=[4], outputs=["y"],
        value_types={"cells": cells})
    binding = proc.semantics.value_binding("state", "cells")

    assert binding.solver_variables == ("cells",)
    assert tuple(solver.indices for _logical, solver
                 in binding.leaf_bindings) == ((0,), (1,))
    post, output, _hits, error = interp.run_step(proc, {"a": 0})
    assert error == ""
    assert post["cells"] == [2, 1]
    assert output.transactions == [{"y": 2}]


ENUM_STATE = """\
class M:
    def reset(self):
        self.mode = "idle"
    def process(self, a):
        self.mode = "run"
        return {"y": a}
"""


def test_declared_enumeration_state_uses_its_recorded_ordinal_map():
    mode = ValueType.enumeration(("idle", "run"))
    proc = lower_with_state_ranges(
        ENUM_STATE, input_widths=[4], outputs=["y"],
        value_types={"mode": mode})
    binding = proc.semantics.value_binding("state", "mode")

    assert binding.reconstruct(proc.initial) == "idle"
    post, _output, _hits, error = interp.run_step(proc, {"a": 2})
    assert error == ""
    assert binding.reconstruct(post) == "run"


@pytest.mark.parametrize("predicate", [
    "self.mode", "self.mode == 0", "self.mode != 1",
    'self.mode == "idle"', 'self.mode != "run"', "self.mode == a",
    "bool(self.mode)", "not self.mode", "self.mode is None",
])
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_enumeration_storage_tags_do_not_replace_source_string_semantics(predicate, lowerer):
    source = f'''class M:
    def reset(self):
        self.mode = "idle"
    def process(self, a):
        y = 1 if {predicate} else 0
        self.mode = "run"
        return {{"y": y}}
'''
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"],
        lowerer=lowerer,
        value_types={"mode": ValueType.enumeration(("idle", "run"))})
    assert model_audit(
        proc, _class(source), [[{"a": a} for a in (0, 1, 2, 0)]]) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("expression", [
    'self.mode == self.change()',
    'self.mode == self.change_number()',
    'self.same(self.mode, self.change())',
    'self.same(self.mode, self.change_number())',
])
def test_enum_value_is_captured_before_later_helper_effects(expression, lowerer):
    source = f'''class M:
    def reset(self):
        self.mode = "idle"
    def change(self):
        self.mode = "run"
        return self.mode
    def change_number(self):
        self.mode = "run"
        return 0
    def same(self, left, right):
        return left == right
    def process(self, a):
        self.mode = "idle"
        return {{"y": 1 if {expression} else 0}}
'''
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer,
        value_types={"mode": ValueType.enumeration(("idle", "run"))})
    assert model_audit(proc, _class(source), [[{"a": 0}, {"a": 1}]]) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_enum_aliases_and_copies_remap_different_ordinal_layouts(lowerer):
    source = '''class M:
    def reset(self):
        self.mode = "idle"
        self.saved = "run"
    def process(self, a):
        old = self.mode
        self.mode = "run" if a else "idle"
        self.saved = old
        y = 1 if self.saved == self.mode else 0
        return {"y": y}
'''
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer,
        value_types={"mode": ValueType.enumeration(("idle", "run")),
                     "saved": ValueType.enumeration(("run", "idle"))})
    assert model_audit(
        proc, _class(source), [[{"a": a} for a in (0, 1, 1, 0)]]) == []


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_enum_leaves_in_recursive_state_keep_string_meaning(lowerer):
    source = '''class M:
    def reset(self):
        self.cells = [{"mode": "idle"}, {"mode": "run"}]
    def process(self, a):
        y = 1 if self.cells[a & 1]["mode"] == "idle" else 0
        self.cells[a & 1]["mode"] = self.cells[(a + 1) & 1]["mode"]
        return {"y": y}
'''
    schema = ValueType.array(2, ValueType.record({
        "mode": ValueType.enumeration(("idle", "run"))}))
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer,
        value_types={"cells": schema})
    assert model_audit(
        proc, _class(source), [[{"a": a} for a in (0, 1, 2, 3)]]) == []


@pytest.mark.isolation
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_enum_state_residual_queries_have_native_goal_and_state_agreement(lowerer):
    from behave.sim.behavior.instance import BehaviorInstance

    source = '''class M:
    def reset(self):
        self.mode = "idle"
    def process(self, a):
        if self.mode == "run":
            y = 1 if a == 3 else 0
        else:
            y = 0
        self.mode = "run" if a == 7 else "idle"
        return {"y": y}
'''
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer,
        value_types={"mode": ValueType.enumeration(("idle", "run"))})
    instance = BehaviorInstance(proc, proc.state_ranges, source=source)
    instance.bind(2)
    baseline_hits = set()
    state = proc.initial
    for _ in range(2):
        state, _out, hits, error = interp.run_step(proc, {"a": 0}, state)
        assert not error
        baseline_hits.update(hits)
    remaining = set(instance.goals()) - baseline_hits
    assert remaining
    verdict = instance.query(remaining, 2)
    assert not verdict.undecided
    assert {g for g in remaining if g.startswith("branch:")} <= set(verdict.reachable)
    assert instance.source_audit(tuple(verdict.reachable.values())).status.value == "agreed"


@pytest.mark.parametrize("operation", ["+", "*", "&"])
def test_enum_ordinal_is_never_used_for_numeric_source_operations(operation):
    source = ENUM_STATE.replace('return {"y": a}',
                                f'return {{"y": self.mode {operation} a}}')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"],
            value_types={"mode": ValueType.enumeration(("idle", "run"))})
    assert caught.value.code == "static-reference-operation-unsupported"


@pytest.mark.parametrize("initial, declaration, code", [
    ({"x": 1}, ROW, "declared-state-value-mismatch"),
    ([{"x": 1, "flag": False}], ValueType.array(2, ROW),
     "declared-state-value-mismatch"),
])
def test_malformed_declared_state_shape_typed_refuses(initial, declaration, code):
    source = f'''\
class M:
    def reset(self):
        self.raw = {initial!r}
    def process(self, a):
        return {{"y": a + int(bool(self.raw))}}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"],
            value_types={"raw": declaration})
    assert caught.value.code == code


def test_inferred_record_field_mutation_still_requires_an_explicit_binding():
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            RECORD_STATE, input_widths=[4], outputs=["y"])
    # Inferred fixed records have a shared carrier for whole-record replacement;
    # fieldwise mutation still needs the explicit ValueBinding tested above.
    assert caught.value.code == "record-mutation-unsupported"


def test_undeclared_enum_member_typed_refuses_at_the_assignment():
    source = ENUM_STATE.replace('self.mode = "run"', 'self.mode = "fault"')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"],
            value_types={
                "mode": ValueType.enumeration(("idle", "run"))})
    assert caught.value.code == "enumeration-member-undeclared"


@pytest.mark.parametrize('array_storage', [False, True])
def test_partial_output_proof_respects_recursive_physical_binding(monkeypatch, array_storage):
    from behave.sim.behavior.output_boundary import (
        OutputBoundaryBuilder,
        output_boundary_bounds,
        output_boundary_evidence,
    )

    original = OutputBoundaryBuilder.record_values

    def partial_ranges(self, binding, values, range_of, source=None):
        if binding.name == 'packet':
            unproved = dict(values)[(1,)]
            original_range_of = range_of
            range_of = lambda expr: None if expr is unproved else original_range_of(expr)
        return original(self, binding, values, range_of, source)

    monkeypatch.setattr(OutputBoundaryBuilder, 'record_values', partial_ranges)
    packet = ValueBinding('packet', ValueType.array(2, U4), tuple(
        ((index,), SolverLeafPath('cells', (index,)) if array_storage else
         SolverLeafPath(f'cell{index}')) for index in range(2)))
    signature = ProcedureSignature((('a', FiniteType(4)),),
                                   (('packet', packet), ('y', FiniteType(4))))
    source = '''class M:
    def reset(self): pass
    def process(self, a):
        return {'packet': [1, a], 'y': a}
'''
    proc = lower_with_state_ranges(source, outputs=['packet', 'y'], signature=signature)
    expected = {'#count': (0, 1), 'y': (0, 15)}
    if not array_storage:
        expected['cell0'] = (0, 1)
    for value in (proc, pickle.loads(pickle.dumps(proc))):
        assert output_boundary_evidence(value) is None
        assert output_boundary_bounds(value) == expected
