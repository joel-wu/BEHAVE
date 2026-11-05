"""Prepare a fixed IR once without weakening step-by-step source evidence."""

import pytest

pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp, ir, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType

from .test_encoding_plan import instance

SOURCE = """class M:
    def reset(self):
        self.n = 0
        self.buf = [0, 0]
    def process(self, a):
        self.n = (self.n + a) & 255
        self.buf[0] = a
        if a == 0:
            return None
        return [{"y": self.n}, {"y": a}]
"""


def test_source_audit_prepares_once_across_steps_traces_and_resets(monkeypatch):
    model = instance(SOURCE, bound=100)
    namespace, _sites, hits = instrumented.load_source(SOURCE, file=model.file)
    bind = interp.bind_procedure_contract
    calls = []

    def counted(proc):
        calls.append(proc)
        return bind(proc)

    monkeypatch.setattr(interp, "bind_procedure_contract", counted)
    rows = [{"a": index % 5} for index in range(100)]
    observed = []
    assert audit.model_audit(
        model.proc, namespace["M"], (rows, rows), hits,
        reset_indices=((41, 77), (23,)),
        observe_step=lambda *args: observed.append(args),
    ) == []
    assert calls == [model.proc]
    assert len(observed) == 200
    assert observed[0][2]["n"] == observed[41][2]["n"] == 0
    assert observed[100][2]["n"] == observed[123][2]["n"] == 0
    assert model.proc.initial["buf"] == [0, 0]


def test_prepared_steps_retain_states_ordered_batches_hits_and_errors():
    source = SOURCE.replace('return [{"y": self.n}, {"y": a}]',
                            'return [{"y": self.n}, {"y": (a // (a - 3)) & 65535}]')
    model = instance(source, bound=5)
    prepared = interp._prepare_procedure(model.proc)
    original_state = prepared_state = None
    for value in (0, 5, 2, 3):
        original = interp.run_step(model.proc, {"a": value}, original_state)
        repeated = prepared.run_step({"a": value}, prepared_state)
        assert repeated[0] == original[0]
        assert repeated[1].transactions == original[1].transactions
        assert repeated[2:] == original[2:]
        original_state, prepared_state = original[0], repeated[0]
    assert original[3] == "DivZero"
    assert prepared.initial["buf"] == model.proc.initial["buf"] == [0, 0]


@pytest.mark.parametrize("inputs, state, diagnostic", [
    ({"a": True}, None, "declared"),
    ({"a": 256}, None, "outside u8"),
    ({}, None, "input"),
    ({"a": 2}, {"n": True, "buf": [0, 0]}, "state"),
    ({"a": 2}, {"n": 0, "buf": [0]}, "state"),
])
def test_prepared_steps_still_check_each_runtime_boundary(inputs, state, diagnostic):
    model = instance(SOURCE)
    prepared = interp._prepare_procedure(model.proc)
    for step in (lambda row, at: interp.run_step(model.proc, row, at),
                 prepared.run_step):
        with pytest.raises(interp.InterpError, match=diagnostic):
            step(inputs, state)


def test_prepared_steps_still_check_output_domain():
    proc = ir.Procedure(
        (), ("a",), (), ("y",),
        ir.Assign("y", ir.Op("arraylit", ir.Op("add", ir.Var("a"), ir.Lit(1)))),
        types={"a": types.INTEGER, "y": types.Array(1)},
        signature=ProcedureSignature(
            (("a", FiniteType(2)),), (("y", FiniteType(2)),)),
        output_capacity=1,
    )
    prepared = interp._prepare_procedure(proc)
    assert prepared.run_step({"a": 2})[1].transactions == [{"y": 3}]
    with pytest.raises(interp.InterpError, match="outside u2"):
        prepared.run_step({"a": 3})


def test_preparation_does_not_accept_a_stale_bound_contract():
    proc = ir.Procedure((), (), (), (), ir.Fail("First", "first"))
    interp.run_step(proc, {})
    proc.body = ir.Fail("Second", "second")
    with pytest.raises(interp.InterpError, match="changed after"):
        interp._prepare_procedure(proc)


def test_preparation_owns_body_initial_mapping_and_types():
    model = instance(SOURCE)
    proc = model.proc
    prepared = interp._prepare_procedure(proc)
    expected = prepared.run_step({"a": 1})
    for command in ir.walk(proc.body):
        if isinstance(command, ir.Mark):
            command.goal = "mutated"
    proc.initial["buf"][0] = 99
    proc.output_vars.clear()
    proc.body = ir.Fail("Changed", "changed")
    proc.types["buf"] = types.Array(3)
    actual = prepared.run_step({"a": 1})
    assert actual[0] == expected[0]
    assert actual[1].transactions == expected[1].transactions
    assert actual[2:] == expected[2:]
    assert prepared.initial["buf"] == [0, 0]


def test_native_code_cannot_change_the_audited_program_through_its_owner():
    proc = ir.Procedure((), (), (), ("y",), ir.Assign("y", ir.Lit(1)))

    class MutatingModel:
        def reset(self):
            proc.body.expr.value = 2

        def process(self):
            return {"y": 2}

    findings = audit.model_audit(proc, MutatingModel, ([{}],))
    assert any("y of transaction" in finding.what for finding in findings)


def test_preparation_keeps_long_sequence_support():
    body = ir.Assign("y", ir.Lit(0))
    for _ in range(2000):
        body = ir.Seq(body, ir.Assign("y", ir.Op("add", ir.Var("y"), ir.Lit(1))))
    proc = ir.Procedure((), (), (), ("y",), body)
    assert interp._prepare_procedure(proc).run_step({})[1].transactions == [{"y": 2000}]


@pytest.mark.parametrize("depth", [80, 120])
@pytest.mark.parametrize("branch_kind", ["conditional", "loop"])
def test_preparation_keeps_nested_command_support(depth, branch_kind):
    body = ir.Assign("y", ir.Lit(7))
    for index in range(depth):
        nested = (ir.If(ir.Var("enabled"), body, ir.Assign("y", ir.Lit(3)))
                  if branch_kind == "conditional" else ir.For(f"i{index}", 1, body))
        body = ir.Seq(ir.Mark(f"level-{index}"), nested)
    proc = ir.Procedure((), ("enabled",), (), ("y",), body,
                        types={"enabled": types.BOOLEAN})
    prepared = interp._prepare_procedure(proc)
    for enabled in (False, True):
        expected = interp.run_step(proc, {"enabled": enabled})
        actual = prepared.run_step({"enabled": enabled})
        assert actual[0] == expected[0]
        assert actual[1].transactions == expected[1].transactions
        assert actual[2:] == expected[2:]


def test_owned_ir_copy_preserves_shared_nodes_without_sharing_originals():
    value = ir.Lit(7)
    expr = ir.Op("add", value, value)
    branch = ir.Assign("y", expr)
    body = ir.If(ir.Lit(True), branch, branch)
    memo = {}
    image = ir.copy_command(body, memo)
    assert image.then is image.orelse
    assert image.then.expr.args[0] is image.then.expr.args[1]
    assert image.then.expr.args[0] is not value
    assert memo[id(expr)] is image.then.expr
    value.value = 99
    body.then.var = "changed"
    assert image.then.var == "y"
    assert image.then.expr.args[0].value == 7


def test_owned_ir_copy_handles_deep_expression_dags():
    value = ir.Var("a")
    for _ in range(1200):
        value = ir.Op("add", value, ir.Lit(1))
    body = ir.Assign("y", value)
    image = ir.copy_command(body)
    assert ir.command_fingerprint(image) == ir.command_fingerprint(body)
    assert not ({id(node) for node in ir.expression_nodes(image)}
                & {id(node) for node in ir.expression_nodes(body)})


def test_owned_ir_copy_preserves_sealed_loop_proofs():
    from behave.sim.symbolic import ssa
    from .test_semantic_loops import _one_sealed_loop, _sealed_loop_proc

    proc = _sealed_loop_proc()
    prepared = interp._prepare_procedure(proc)
    original = _one_sealed_loop(proc)
    copied, = [node for node in ir.walk(prepared.body)
               if isinstance(node, ir.For) and node.flow is not None]
    assert ssa.loop_flow_facts(copied).image == original.flow.image
    assert copied.flow.graph is not original.flow.graph
    for value in range(4):
        expected = interp.run_step(proc, {'a': value})
        actual = prepared.run_step({'a': value})
        assert actual[1].transactions == expected[1].transactions
        assert actual[2:] == expected[2:]
