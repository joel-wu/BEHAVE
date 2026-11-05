"""The domain-local baseline through the public, isolated RTL scoring path."""

import json
from pathlib import Path

import pytest

from behave.sim import api

RTL = """
module plain_pair (
    input logic a_clk, a_rst, b_clk, b_rst,
    input logic [3:0] a_in_data, b_in_data,
    output logic [3:0] a_out_data, b_out_data
);
    always @(posedge a_clk or posedge a_rst)
        if (a_rst) a_out_data <= 0;
        else a_out_data <= a_in_data;
    always @(negedge b_clk or posedge b_rst)
        if (b_rst) b_out_data <= 0;
        else b_out_data <= b_in_data;
endmodule
"""

REFERENCE = b"""
class Reference:
    def __init__(self):
        self.reset()

    def reset(self):
        self.a = 0
        self.b = 0

    def process_a(self, a_in_data):
        old = self.a
        self.a = a_in_data
        return {"a_out_data": old}

    def process_b(self, b_in_data):
        old = self.b
        self.b = b_in_data
        return {"b_out_data": old}
"""


@pytest.mark.cosim
@pytest.mark.parametrize('runtime', (False, True), ids=('finite', 'runtime'))
@pytest.mark.parametrize('mutant', (False, True), ids=('honest', 'constant-output'))
def test_plain_multiclock_baseline_public_score(tmp_path, mutant, runtime):
    contract = api.TaskContract([
        api.Endpoint(f'{domain}_{direction}', direction,
                     [api.Field('data', api.FiniteType(4), f'{domain}_{direction}_data')],
                     api.sampled_edge(direction), clock=domain,
                     kind=api.Kind.TRANSACTION)
        for domain in ('a', 'b') for direction in ('in', 'out')],
        [api.ClockDomain('a', 'a_clk', 'a_rst'),
         api.ClockDomain('b', 'b_clk', 'b_rst', edge='falling')])
    source = RTL.replace('a_out_data <= a_in_data;', 'a_out_data <= 0;') if mutant else RTL
    rtl = tmp_path / 'pair.sv'
    rtl.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': REFERENCE}, config={'class': 'Reference'}, loader='baseline regression')
    task = api.admit(contract, api.realization_of_rtl_source(
        source, 'plain pair', top='plain_pair', contract=contract), reference=reference)
    generation = api.GenerationSpec(horizon=16, queue_bound=16,
        reset_hold_cycles=3, drain=0, stall_every=0)
    handle = api.prepare_rtl(task, api.RTLConfig(
        rtl, 'plain_pair', tmp_path / 'rtl', timeout_s=120), generation)
    policy = api.BaselinePolicy(case_limit=3, max_queue_depth=16)
    baseline = policy if runtime else api.build_baseline(
        contract, generation, policy=policy, realization=task.realization)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / 'command',
        baseline=baseline,
        providers=handle.generation_providers(reference, workdir=tmp_path / 'generation',
                                               enable_local_guidance=False),
        probe_budget=0, run_analysis=False, reference=reference))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    evaluation = result.evaluation
    assert result.decision.authoritative
    assert not evaluation.no_result_failures
    if mutant:
        # A checked negative witness may terminate the remaining pool early.
        assert evaluation.failure_witness is not None
    else:
        assert evaluation.complete and evaluation.required_complete, evaluation.as_dict()
    assert evaluation.passed is not mutant, evaluation.as_dict()
    assert bool(evaluation.mismatch_count) is mutant
    assert evaluation.results
    for run in evaluation.results:
        assert run.ran
        # Runtime requests carry queues, not precomputed clock slots. Check the
        # schedule actually executed and retained in the native trace.
        slots = run.trace.stimulus.slots
        assert slots
        # The explicit finite schedule releases each domain independently;
        # runtime sampled windows hold all domains until a common body start.
        assert any(slot.resets and slot.offers for slot in slots) is not runtime
        for endpoint in contract.inputs():
            assert run.trace.accepted_count([endpoint.name]) == run.stimulus.depth(endpoint.name)
            assert run.trace.accepted_count([endpoint.name]) == sum(
                endpoint.clock in slot.ticks and endpoint.clock not in slot.resets
                for slot in slots)
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted['authority']['passed'] is not mutant
