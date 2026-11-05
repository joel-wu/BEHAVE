"""Operand hints produce public replay inputs, never narrower task semantics."""

import copy

import pytest

from behave.sim import api
from behave.sim.core.memory_sampling import memory_image_mode, sampled_memory_initial


def memory(profile=True):
    return api.MemoryInterface('ram', 3, 8, 'clk', [api.MemoryPort('rw')],
        sampling=api.MemorySampling([api.MemorySampleRegion(0, 8, [
            api.MemorySampleLane(0, 8, values=[3, 7, 11], edges=[0, 128, 255])])])
        if profile else None)


def contract(profile=True):
    from .test_api_path import contract as base_contract
    base = base_contract()
    return api.TaskContract(base.endpoints, base.clocks, memories=[memory(profile)])


def by_case(baseline):
    return {case: stimulus for stimulus in baseline.stimuli
            for row in baseline.provenance_dict()[stimulus.fingerprint]
            for case in row['cases']}


def test_sampling_wire_identity_and_legacy_contracts():
    current = contract()
    image = current.as_dict()
    assert image['version'] == 'task-contract/17'
    assert api.TaskContract.from_dict(image).as_dict() == image
    assert current.digest != contract(False).digest
    image['version'] = 'task-contract/16'
    with pytest.raises(TypeError, match='task-contract/17'):
        api.TaskContract.from_dict(image)
    del image['memories'][0]['sampling']
    assert api.TaskContract.from_dict(image).memories[0].sampling is None
    assert 'sampling' not in memory(False).as_dict()
    # It is a task fact, not a physical feature the RTL must independently invent.
    physical = api.MemoryRealization('ram', 3, 8, 'clk', [api.MemoryPort('rw')])
    assert set(physical.as_dict()) == {'name', 'addr_width', 'data_width', 'clock', 'ports'}


@pytest.mark.parametrize('build', [
    lambda: api.MemorySampleLane(True, 8),
    lambda: api.MemorySampleLane(0, 0),
    lambda: api.MemorySampleLane(0, 8, values=[256]),
    lambda: api.MemorySampleLane(0, 8, values=[True]),
    lambda: api.MemorySampleLane(0, 8, values=[1, 1]),
    lambda: api.MemorySampleLane(0, 8, values=[1], upper=10),
    lambda: api.MemorySampleLane(0, 8, lower=9, upper=8),
    lambda: api.MemorySampleLane(0, 8, sequence='custom-code'),
    lambda: api.MemorySampleRegion(0, 0, [api.MemorySampleLane(0, 8)]),
    lambda: api.MemorySampleRegion(0, 2, [api.MemorySampleLane(1, 8)]),
    lambda: api.MemorySampleRegion(0, 2, [api.MemorySampleLane(0, 8), api.MemorySampleLane(4, 8)]),
    lambda: api.MemorySampling([]),
    lambda: api.MemorySampling([api.MemorySampleRegion(0, 2, [api.MemorySampleLane(0, 8)])] * 2),
    lambda: api.MemoryInterface('ram', 3, 8, 'clk', [api.MemoryPort('r')],
        sampling=api.MemorySampling([api.MemorySampleRegion(0, 9, [api.MemorySampleLane(0, 8)])])),
    lambda: api.MemoryInterface('ram', 3, 8, 'clk', [api.MemoryPort('r')],
        sampling=api.MemorySampling([api.MemorySampleRegion(0, 8, [api.MemorySampleLane(0, 16)])])),
])
def test_reject_invalid_profiles(build):
    with pytest.raises((TypeError, ValueError)):
        build()


def test_wire_is_strict_and_detached():
    m = memory()
    wire = m.as_dict()
    bad = copy.deepcopy(wire)
    bad['sampling']['version'] = 'memory-sampling/0'
    with pytest.raises(TypeError):
        api.MemoryInterface.from_dict(bad)
    bad = copy.deepcopy(wire)
    bad['sampling']['regions'][0]['lanes'][0]['upper'] = True
    with pytest.raises(ValueError):
        api.MemoryInterface.from_dict(bad)
    wire['sampling']['regions'][0]['lanes'][0]['values'][0] = 99
    assert m.sampling.regions[0].lanes[0].values == (3, 7, 11)
    with pytest.raises(AttributeError):
        m.sampling.regions[0].start = 1


@pytest.mark.parametrize('fixed', [False, True])
def test_common_baseline_has_all_modes_bound_to_actual_memory(fixed):
    c = contract()
    spec = api.GenerationSpec(horizon=20, queue_bound=3, reset_hold_cycles=1, drain=2) if fixed else None
    policy = api.BaselinePolicy(seed='memory-test', case_limit=8, max_queue_depth=3)
    baseline = api.build_baseline(c, spec, policy=policy)
    assert baseline.as_dict() == api.build_baseline(c, spec, policy=policy).as_dict()
    indexed = by_case(baseline)
    for case, stimulus in indexed.items():
        run = stimulus.memory_environment.run('ram')
        cells = dict(run.initial)
        mode = memory_image_mode(case)
        if mode == 'ordinary':
            assert set(cells.values()) <= {3, 7, 11}
            assert len(cells) == 8
        elif mode == 'zero':
            assert cells == dict.fromkeys(range(8), 0)
        elif mode == 'edge':
            assert set(cells.values()) <= {0, 128, 255}
        else:
            assert not cells
        row, = baseline.provenance_dict()[stimulus.fingerprint]
        assert row['memory_environment'] == stimulus.memory_environment.digest
        assert {'case': case, 'memories': {'ram': mode}} in row['memory_images']
        assert not stimulus.memory_environment.faults_against(c.memories, 20 if fixed else None)
    assert indexed[0].memory_environment.digest != indexed[1].memory_environment.digest
    if not fixed:
        # Distinct images cannot accidentally be concatenated into one memory run.
        suite = api.build_replay_suite(c, baseline.stimuli)
        for continuous in suite.continuous:
            matching = [s for s in baseline.stimuli
                        if s.memory_environment.digest == continuous.memory_environment.digest]
            assert continuous.depth('in') == sum(s.depth('in') for s in matching)


def test_unprofiled_memory_preserves_original_initial_and_stalls():
    from behave.sim.baseline import _number
    policy = api.BaselinePolicy(case_limit=8, max_queue_depth=3)
    c = contract(False)
    baseline = api.build_baseline(c, policy=policy)
    expected = api.MemoryRun.from_policy(c.memories[0], None, initial=(),
        read_latency=policy.as_dict()['memory_read_latency'],
        stall_rate=policy.as_dict()['memory_stall_rate'],
        seed=_number(policy, 'external-memory:ram', 1 << 63))
    assert all(s.memory_environment.run('ram').as_dict() == expected.as_dict()
               for s in baseline.stimuli)
    assert len(api.build_replay_suite(c, baseline.stimuli).continuous) == 1


def test_packed_lanes_partial_regions_and_distinct_coordinates():
    m = api.MemoryInterface('ram', 7, 16, 'clk', [api.MemoryPort('r')],
        sampling=api.MemorySampling([api.MemorySampleRegion(3, 67, [
            api.MemorySampleLane(0, 6, lower=0, upper=63, sequence='counter'),
            api.MemorySampleLane(6, 10, values=[100, 200])])]))
    cells = dict(sampled_memory_initial(m, seed='one', case=0))
    assert set(cells) == set(range(3, 67))
    assert {v & 63 for v in cells.values()} == set(range(64))
    assert {v >> 6 for v in cells.values()} == {100, 200}
    assert cells != dict(sampled_memory_initial(m, seed='two', case=0))


def test_sampling_hints_do_not_restrict_replay_legality():
    c = contract()
    # 99 is outside all ordinary/edge choices, but still legal uint8 memory.
    environment = api.MemoryEnvironment([api.MemoryRun.from_policy(
        c.memories[0], None, initial={0: 99})])
    assert environment.faults_against(c.memories, None) == ()
    request = api.Stimulus(queues={'in': [(0,)]}, memory_environment=environment,
                          replay_policy=api.ReplayPolicy.for_contract(c))
    assert not request.faults_against(c)


@pytest.mark.isolation
@pytest.mark.parametrize('kind', ['behavior', pytest.param('rtl', marks=pytest.mark.eda)])
@pytest.mark.parametrize('wrong', [False, True])
def test_public_score_checks_sampled_arithmetic(tmp_path, wrong, kind):
    from behave.sim.reference_isolated import ADAPTER
    source = '''class M:
    def reset(self):
        self.ram = [0] * 8

    def process(self, in_a):
        return {"out_y": (self.ram[in_a & 7] + in_a) & 255}
'''
    reference = api.load_reference(ADAPTER, {'behavior.py': source.encode()}, config={'class': 'M'})
    c = contract()
    if kind == 'behavior':
        candidate = source.replace('self.ram[in_a & 7]', '0') if wrong else source
        path = tmp_path / 'candidate.py'
        path.write_text(candidate)
        task = api.admit(c, api.realization_of_behavior_source(candidate, c, file=str(path)), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=str(path)),
            api.GenerationSpec(64, 3, 1, 4), bounded_analysis=False)
    else:
        candidate = '''module reader(input clk,rst,input [7:0] in_a,
          input in_valid,output in_ready,output reg [7:0] out_y,
          output out_valid,input out_ready,
          output ram_req_valid,output [2:0] ram_req_addr,output ram_req_we,
          output [7:0] ram_req_wdata,input ram_req_ready,ram_resp_valid,
          input [7:0] ram_resp_rdata);
          reg [1:0] state; reg [7:0] a;
          assign in_ready = state==0 && !rst;
          assign out_valid = state==3 && !rst;
          assign ram_req_valid = state==1 && !rst;
          assign ram_req_addr = a[2:0];
          assign ram_req_we=0; assign ram_req_wdata=0;
          always @(posedge clk) begin
            if(rst) begin state<=0; a<=0; out_y<=0; end
            else case(state)
              0: if(in_valid) begin a<=in_a; state<=1; end
              1: if(ram_req_ready) state<=2;
              2: if(ram_resp_valid) begin out_y<=VALUE + a; state<=3; end
              3: if(out_ready) state<=0;
            endcase
          end
        endmodule'''.replace('VALUE', "8'd0" if wrong else 'ram_resp_rdata')
        path = tmp_path / 'candidate.sv'
        path.write_text(candidate)
        task = api.admit(c, api.realization_of_rtl_source(candidate, 'reader', top='reader', contract=c),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, 'reader', tmp_path / 'rtl'),
                                 api.GenerationSpec(64, 3, 1, 4))
    request = handle.command_request(run_directory=tmp_path / 'command', reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=3),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False)
    result = api.handle_command(request)
    assert result.evaluation is not None, result.as_dict()
    assert result.decision.authoritative, result.as_dict()
    import json
    assert result.decision.passed is not wrong, json.dumps(result.as_dict(), indent=2)
    assert bool(result.evaluation.mismatch_count) is wrong
    if not wrong:
        assert result.evaluation.complete
