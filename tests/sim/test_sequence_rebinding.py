"""A local sequence has one backing capacity across shrinking rebindings."""

import itertools

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("expression", [
    "bits + [1]", "(bits + [1]) * 2", "(bits + [1])[2:]",
])
@pytest.mark.parametrize("variable", [False, True])
def test_loop_postcondition_reaches_nested_sequence_reads(lowerer, expression, variable):
    guard = "a & (1 << k)" if variable else "True"
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        bits = [0]
        for k in range(8):
            if {guard}:
                bits.append((a >> k) & 1)
        if False:
            bits.append(0)
        result = {expression}
        return {{"y": sum(result), "n": len(result)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y", "n"],
                                   lowerer=lowerer, bound=1)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)
    assert model_audit(proc, namespace["M"], [[{"a": a}] for a in range(16)]) == []


@pytest.mark.parametrize("drop", (1, 2))
def test_shrink_then_append_preserves_one_local_representation(drop):
    source = f"""class M:
    def reset(self):
        self.data = [0, 0, 0, 0]
        self.count = 0

    def process(self, a):
        seq = [self.data[i] for i in range(self.count)]
        if self.count > {drop}:
            seq = seq[{drop}:]
        seq.append(a)
        count = len(seq)
        data = self.data[:]
        for i in range(count):
            data[i] = seq[i]
        self.data = data
        self.count = count
        return {{'y': seq[0], 'n': count}}
"""
    procedure = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y", "n"], bound=4)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    for inputs in itertools.product(range(4), repeat=4):
        native = namespace["M"]()
        native.reset()
        state = None
        for value in inputs:
            expected = native.process(value)
            state, output, _hits, error = interp.run_step(
                procedure, {"a": value}, state)
            assert not error
            assert output.transactions == [expected]
            assert state["data"] == native.data
            assert state["count"] == native.count
