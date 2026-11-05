"""The schema belongs to the task, and the core does not read SystemVerilog.

What the endpoints carry is a fact about the task that both artifacts are
supposed to realise. It was read out of the candidate RTL text, which lets the
thing under test define the input space the other artifact's verdicts are
relative to: a candidate declaring a port narrower than the task narrows the
contract, and narrowing the contract can only enlarge the set of goals reported
unreachable.
"""
import pytest

from behave.sim.core.schema import Endpoint, InterfaceError, TransactionSchema
from behave.sim.rtl.interface import read_ports

RTL = ("module m(input logic [7:0] a, input logic b,\n"
       "         output logic [7:0] y);\nendmodule\n")


def test_the_core_does_not_parse_systemverilog():
    """A boundary that lives only in where the code sits is one that decays, so
    it is read off the module itself.

    The prose is skipped and only the code is read, since the docstring's whole
    subject is that this module does not do the thing being searched for.
    """
    import ast
    import inspect

    from behave.sim.core import schema

    tree = ast.parse(inspect.getsource(schema))
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""       # a docstring is not code
    code = ast.unparse(tree)
    assert "re." not in code, "the core does not read anyone's syntax"
    assert "module" not in code.lower()
    assert not hasattr(schema, "port_widths")


def test_a_candidate_reading_says_it_is_one():
    got = read_ports(RTL)
    assert "read from" in got.source
    assert got["a"].width == 8 and got["b"].width == 1


def test_a_schema_says_what_an_endpoint_carries():
    task = TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("b", "input", 1),
         Endpoint("y", "output", 8)], source="declared by the task")
    assert task.bounds(["a"]) == {"a": (0, 255)}
    with pytest.raises(InterfaceError):
        task.bounds(["y"])          # an output is not something to drive
    with pytest.raises(InterfaceError):
        task.bounds(["nothing"])    # and an undeclared name is not invented


def test_realising_is_a_comparison_and_not_a_definition():
    """What an RTL frontend owes: not a schema of its own, but that the ports
    it elaborated are the ones the task declared."""
    task = TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("b", "input", 1),
         Endpoint("y", "output", 8)])
    assert task.realises(read_ports(RTL)) == []

    narrow = RTL.replace("[7:0] a", "[3:0] a")
    complaints = task.realises(read_ports(narrow))
    assert len(complaints) == 1 and "a is" in complaints[0]


def test_a_contract_taken_from_the_candidate_retains_its_authority():
    from behave.sim.behavior import instance as behavior_instance

    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a, b):\n'
           '        return {"y": a}\n')
    # By code, since B5 made the decision a field rather than a phrase. The note is
    # prose for a reader now and matching on it is what this used to do.
    from behave.sim.core.schema import Authority, Reading

    from_candidate = behavior_instance.lower(src, RTL)
    assert from_candidate.schema.authority is not Authority.TASK
    assert from_candidate.schema.reading is Reading.HEADER_REGEX

    task = TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("b", "input", 1),
         Endpoint("y", "output", 8)], source="declared by the task",
        authority=Authority.TASK, reading=Reading.ELABORATED)
    declared = behavior_instance.lower(src, task)
    assert declared.schema.authority is Authority.TASK
    assert declared.schema.reading is Reading.ELABORATED


def test_a_narrower_candidate_would_narrow_the_contract():
    """The reason this matters, made concrete: the same model driven through a
    candidate that declares a narrower port is a model whose verdicts are about
    a smaller set of traces."""
    from behave.sim.behavior import instance as behavior_instance

    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a, b):\n'
           '        return {"y": a}\n')
    wide = behavior_instance.lower(src, RTL)
    narrow = behavior_instance.lower(src, RTL.replace("[7:0] a", "[3:0] a"))
    assert wide.contract()["a"] == [0, 255]
    assert narrow.contract()["a"] == [0, 15]
