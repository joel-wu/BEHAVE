"""Real public scoring has no implicit finite analysis domain, for B/B or B/R."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from tests.sim.test_settled_protocol import contract

SOURCE = """class M:
    def reset(self): pass
    def process(self, in_a):
        return {'out_y': (in_a * 2) & 255}
"""


@pytest.mark.isolation
@pytest.mark.parametrize('kind', ['behavior', pytest.param('rtl', marks=pytest.mark.cosim)])
@pytest.mark.parametrize('wrong', [False, True])
def test_public_runtime_baseline_scores_without_generation_spec(tmp_path, kind, wrong):
    task_contract = contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {'behavior.py': SOURCE.encode()}, config={'class': 'M'})
    if kind == 'behavior':
        source = SOURCE.replace('in_a * 2', 'in_a * 2 + 1') if wrong else SOURCE
        path = tmp_path / 'behavior.py'
        path.write_text(source)
        realization = api.realization_of_behavior_source(source, task_contract, model='M')
        config = api.BehaviorConfig(path, model='M')
        prepare = api.prepare_behavior
    else:
        expression = "(in_a << 1) ^ 8'h01" if wrong else 'in_a << 1'
        source = ('module M(input wire [7:0] in_a, output wire [7:0] out_y); '
                  f'assign out_y = {expression}; endmodule')
        path = tmp_path / 'rtl.sv'
        path.write_text(source)
        realization = api.realization_of_rtl_source(source, top='M', contract=task_contract)
        config = api.RTLConfig(path, 'M', tmp_path / 'artifact')
        prepare = api.prepare_rtl
    task = api.admit(task_contract, realization, reference=reference)
    handle = prepare(task, config)
    assert handle.as_dict()['generation_spec'] is None
    policy = api.BaselinePolicy(case_limit=2)  # Public length default: 100, not a slot cap.
    command = handle.command_request(run_directory=tmp_path / 'command', baseline=policy,
                                    reference=reference, search_policy=api.SearchPolicy(time_budget_s=0),
                                    run_analysis=False)
    assert command.generation_spec is command.analysis_bound is None
    assert all(seed.depth('in') == 100 for seed in command.baseline.stimuli)
    result = api.handle_command(command)
    assert result.decision is not None, result.as_dict()
    assert result.decision.authoritative, result.as_dict()
    assert result.decision.passed is not wrong, result.as_dict()
    # A witnessed mismatch may stop the pool early; only a pass requires the
    # complete baseline. Keep the concrete mismatch assertion for faulty code.
    if not wrong:
        assert result.evaluation.complete, result.as_dict()
    assert (result.evaluation.mismatch_count > 0) is wrong
    assert result.analysis is None
    record = json.loads(Path(result.record_path).read_text())
    assert record['authority']['passed'] is not wrong
    # Optional analysis is still explicit; no default horizon is synthesized.
    with pytest.raises(ValueError, match='explicit GenerationSpec'):
        handle.command_request(run_directory=tmp_path / 'analysis', baseline=policy,
                               reference=reference, search_policy=api.SearchPolicy(time_budget_s=0),
                               run_analysis=True, analysis_wall_timeout_seconds=180)


def test_behavior_analysis_requires_explicit_domain_before_preparation(tmp_path):
    from tests.sim.test_api_path import _ref, realization
    from tests.sim.test_api_path import contract as clocked_contract

    reference = _ref()
    task = api.admit(clocked_contract(), realization(), reference=reference)
    with pytest.raises(ValueError, match='explicit GenerationSpec'):
        api.prepare_behavior(task, api.BehaviorConfig(tmp_path / 'unused.py'), bounded_analysis=True)
