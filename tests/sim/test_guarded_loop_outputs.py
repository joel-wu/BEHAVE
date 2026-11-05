"""Guarded loop outputs are proved from their sealed source control flow."""

import textwrap

import pytest

from behave.sim.behavior import instance, output_boundary, signature
from behave.sim.symbolic import encoder, interp, ir
from behave.sim.behavior.lowering import LoweringError
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.refusal import Refused
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

pytestmark = pytest.mark.solver


LOOPS = [
    """for i in range(32):
        if i < 4:
            bit = 0
            if a & (1 << i):
                bit = 1
            result |= (bit & 1) << i
""",
    """for i in range(32):
        if i >= 4:
            continue
        bit = 0
        if a & (1 << i):
            bit = 1
        result |= (bit & 1) << i
""",
    """for outer in range(2):
        for inner in range(16):
            i = 16 * outer + inner
            if i < 4:
                bit = 0
                if a & (1 << i):
                    bit = 1
                result |= (bit & 1) << i
""",
    """for i in range(32):
        if i >= 4:
            break
        bit = 0
        if a & (1 << i):
            bit = 1
        result |= (bit & 1) << i
""",
]

INDEXED_LOOP = """for i in range(32):
    if i < 4:
        values = [0, 1, 2, 3]
        bit = 0
        if a < 4:
            bit = values[a]
        if a + 1 < 4:
            bit += values[a + 1]
        result |= (bit & 1) << i
"""


def source(loop, invalid):
    body = "result = 0\n" + textwrap.dedent(loop)
    if invalid:
        body += "result |= 16\n"
    body += "return {'y': result}\n"
    return ("class M:\n"
            "    def reset(self): pass\n"
            "    def process(self, a):\n" + textwrap.indent(body, "        "))


def schema():
    return TransactionSchema([
        Endpoint("a", "input", 4), Endpoint("y", "output", 4)
    ], source="test task", authority=Authority.TASK, reading=Reading.ELABORATED)


def _check_guarded_output(monkeypatch, loop, expected):
    def unexpected(*args, **kwargs):
        pytest.fail("guarded loop output must be proved before whole-step encoding")

    with monkeypatch.context() as patch:
        # Exercise the interval fallback independently of any cheap certificate
        # the source planner can already establish on these small controls.
        patch.setattr(output_boundary, "output_boundary_evidence", lambda _proc: None)
        patch.setattr(encoder, "encode_step", unexpected)
        model = instance.lower(source(loop, False), schema(), bound=16)
    before = ir.command_fingerprint(model.proc.body)
    evidence = signature.output_range_evidence(model.proc, model.env)
    assert evidence["y"]["inferred"] == [0, 15]
    assert ir.command_fingerprint(model.proc.body) == before
    rows = [{"a": a} for a in range(16)]
    audited = model.source_audit((rows,))
    assert audited.status is AuditCheckStatus.AGREED, audited.as_dict()
    for row in rows:
        _state, output, _hits, error = interp.run_step(model.proc, row)
        assert not error
        assert output.transactions == [{"y": expected(row["a"])}]


@pytest.mark.parametrize("loop", LOOPS)
def test_guarded_packing_proves_its_output_without_expanding_a_solver_formula(
        monkeypatch, loop):
    _check_guarded_output(monkeypatch, loop, lambda a: a)


def test_index_checks_inside_a_loop_do_not_forget_its_enclosing_guard(monkeypatch):
    _check_guarded_output(monkeypatch, INDEXED_LOOP, lambda a: 15 if a < 4 else 0)


@pytest.mark.parametrize("loop", [*LOOPS, INDEXED_LOOP])
def test_source_path_output_proof_does_not_hide_an_out_of_domain_return(loop):
    with pytest.raises((Refused, LoweringError)):
        instance.lower(source(loop, True), schema(), bound=16)
