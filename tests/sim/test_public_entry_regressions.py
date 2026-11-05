"""Legacy replay's useful cases exercised through the production command."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from tests.support import ASSET_ROOT

from .test_multiclock_authority import _contract as atomic_contract
from .test_multiclock_authority import _reference as atomic_reference

pytestmark = pytest.mark.cosim


def _score(tmp_path, contract, source, top, reference):
    rtl = tmp_path / 'rtl.sv'
    rtl.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(
        source, top=top, contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(rtl, top, tmp_path / 'build'))
    command = handle.command_request(
        run_directory=tmp_path / 'result', reference=reference,
        baseline=api.BaselinePolicy(case_limit=5, max_queue_depth=4),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False)
    result = api.handle_command(command)
    assert result.decision is not None and result.decision.authoritative, (
        result.failure,
        [(index, failure.as_dict()) for index, failure in result.evaluation.no_result_failures]
        if result.evaluation is not None else result.as_dict())
    record = json.loads(Path(result.record_path).read_text())
    assert record['authority']['passed'] is result.decision.passed
    assert result.evaluation.accepted > 0
    return result


@pytest.mark.parametrize('fixture,model,input_name,input_width,input_signed,output_name,output_width,output_signed', [
    ('signed_output_i8', 'SignedOutputI8', 'in_sel', 1, False, 'out_y', 8, True),
    ('signed_input_negate_i8', 'SignedInputNegateI8', 'in_mag', 7, False, 'out_y', 8, True),
    ('signed_input_flag_bad', 'SignedInputFlagBad', 'in_x', 8, True, 'out_neg', 1, False),
])
def test_public_command_preserves_signed_replay(
        tmp_path, fixture, model, input_name, input_width, input_signed,
        output_name, output_width, output_signed):
    directory = ASSET_ROOT / fixture
    contract = api.TaskContract([
        api.Endpoint('in', api.Direction.IN,
                     [api.Field(input_name, api.FiniteType(input_width, signed=input_signed), input_name)],
                     api.ready_valid('in', 'in_valid', 'in_ready'), clock='clk', kind=api.Kind.TRANSACTION),
        api.Endpoint('out', api.Direction.OUT,
                     [api.Field(output_name, api.FiniteType(output_width, signed=output_signed), output_name)],
                     api.ready_valid('out', 'out_valid', 'out_ready'), clock='clk', kind=api.Kind.TRANSACTION),
    ], [api.ClockDomain('clk', 'clk', 'rst')])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': (directory / 'behavior.py').read_bytes()},
        config={'class': model}, loader='public-entry-regression')
    result = _score(tmp_path, contract, (directory / 'rtl.sv').read_text(), fixture, reference)
    assert result.decision.passed
    assert result.evaluation.required_complete and result.evaluation.complete
    assert not result.evaluation.mismatches
    if output_signed:
        assert any(row[0] < 0 for trace in result.evaluation.records
                   for row in api.transfers(trace, 'out'))
    if input_signed:
        assert any(row[0] < 0 for trace in result.evaluation.records
                   for row in api.transfers(trace, 'in'))


@pytest.mark.parametrize('fault', ['none', 'wrong-value', 'spurious-output'])
def test_public_command_preserves_mixed_clock_edges_and_detects_wrong_outputs(tmp_path, fault):
    source = '''module atomic_pair (
    input a_clk, a_rst, a_in_valid, output a_in_ready,
    input [7:0] a_in_data, output a_out_valid, input a_out_ready, output [7:0] a_out_data,
    input b_clk, b_rst, b_in_valid, output b_in_ready,
    input [7:0] b_in_data, output b_out_valid, input b_out_ready, output [7:0] b_out_data
);
    assign a_in_ready = a_out_ready;
    assign a_out_valid = a_in_valid & ~a_rst;
    assign a_out_data = a_in_data * 8'd2;
    assign b_in_ready = b_out_ready;
    assign b_out_valid = (b_in_valid | SPURIOUS) & ~b_rst;
    assign b_out_data = b_in_data * FACTOR;
endmodule
'''.replace('SPURIOUS', "1'b1" if fault == 'spurious-output' else "1'b0").replace(
    'FACTOR', "8'd4" if fault == 'wrong-value' else "8'd3")
    original = atomic_contract(edges={'a': 'rising', 'b': 'falling'})
    endpoints = []
    for endpoint in original.endpoints:
        row = endpoint.as_dict()
        if endpoint.direction is api.Direction.OUT:
            row['answers_per_transaction'] = 1
        endpoints.append(api.Endpoint.from_dict(row))
    contract = api.TaskContract(endpoints, original.clocks, protocol_instances=[
        api.ProtocolInstance(domain, [f'{domain}_in', f'{domain}_out'])
        for domain in ('a', 'b')])
    result = _score(tmp_path, contract, source, 'atomic_pair', atomic_reference())
    assert result.decision.passed is (fault == 'none')
    if fault == 'wrong-value':
        assert result.evaluation.mismatches
    elif fault == 'spurious-output':
        assert result.evaluation.safety_hits or result.evaluation.candidate_failures
    else:
        assert result.evaluation.required_complete and result.evaluation.complete
    for trace in result.evaluation.records:
        schedule = trace.stimulus.clock_schedule
        assert schedule is not None
        assert {timing.name: timing.edge for timing in schedule.timings} == {
            'a': 'rising', 'b': 'falling'}
