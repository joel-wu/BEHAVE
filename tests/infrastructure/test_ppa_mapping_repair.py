"""Post-map repair is frozen, failure-sensitive, and feeds every EDA export."""
from dataclasses import replace

import pytest
from test_ppa import REGISTERED_RTL, experiment, file, fixture_task

from behave.core.artifacts import LoadedSource, SourceArtifact, FileData
from behave.core.storage import DirectoryStore
from behave.ppa import eda, mapping_repair
from behave.ppa.config import Experiment, MappingPolicy


def source():
    f = file('top.sv', REGISTERED_RTL)
    return LoadedSource(SourceArtifact((f.reference,), 'top.sv', 'M'), (f,))


def test_repair_profile_is_explicit_and_legacy_hash_does_not_gain_a_field():
    old = experiment()
    new = replace(old, mapping=MappingPolicy(repair_design=True))
    assert old.as_dict()['mapping'] == {'driving_cell': 'sky130_fd_sc_hd__buf_2'}
    assert new.identity != old.identity
    assert Experiment.from_dict(new.as_dict()) == new
    assert Experiment.from_dict(old.as_dict()) == old
    assert new.eda_budget.service_seconds == old.eda_budget.service_seconds


@pytest.mark.parametrize('bad', [1, None, 'true'])
def test_repair_flag_is_not_truthiness(bad):
    with pytest.raises(TypeError):
        MappingPolicy(repair_design=bad)


def test_exclusions_roundtrip_and_all_mapping_stages():
    import json
    cells = ('sky130_fd_sc_hd__probe_p_8', 'sky130_fd_sc_hd__lpflow_isobufsrc_1')
    exp = replace(experiment(), mapping=MappingPolicy(repair_design=True, dont_use_cells=cells))
    assert Experiment.from_dict(json.loads(json.dumps(exp.as_dict()))) == exp
    assert exp.identity != experiment().identity
    s = eda.yosys_script(source(), exp.timing, dont_use_cells=cells, repair_design=True)
    for cell in cells:
        assert s.count('-dont_use ' + cell) == 2
    repair = mapping_repair.script('M', exp.timing, dont_use_cells=cells)
    assert repair.index('set_dont_use {') < repair.index('repair_design ')


@pytest.mark.parametrize('cells', [('bad;exec',), ('x', 'x'), ('sky130_fd_sc_hd__buf_2',)])
def test_bad_exclusions_rejected(cells):
    with pytest.raises(ValueError):
        MappingPolicy(dont_use_cells=cells)


def test_placement_profile_and_constraints_are_explicit():
    from behave.ppa.sky130 import DONT_USE_CELLS
    exp = replace(experiment(), timing=replace(experiment().timing, period_ns=12.5),
                  mapping=MappingPolicy(repair_design=True, placement_repair=True,
                                        dont_use_cells=DONT_USE_CELLS))
    assert Experiment.from_dict(exp.as_dict()) == exp
    assert exp.as_dict()['stage'] == 'mapped-synthesis-placement-estimated-interconnect-sta'
    assert exp.identity != experiment().identity
    bad = exp.as_dict()
    bad['stage'] = experiment().as_dict()['stage']
    with pytest.raises(ValueError):
        Experiment.from_dict(bad)
    s = mapping_repair.script('M', exp.timing, placement_repair=True, dont_use_cells=DONT_USE_CELLS)
    assert s.index('estimate_parasitics -placement') < s.index('repair_design\n') < s.index('write_db physical.odb')
    assert s.index('write_db physical.odb') < s.index('write_verilog repaired.v')
    sta = eda.sta_script('M', exp.timing, placement_repair=True)
    assert 'read_db physical.odb' in sta and 'read_verilog mapped.v' not in sta
    assert 'period 12.5' in sta and 'estimate_parasitics -placement' in sta
    with pytest.raises(ValueError):
        MappingPolicy(placement_repair=True)
    with pytest.raises(TypeError):
        MappingPolicy.from_dict({'driving_cell': 'buf', 'dont_use_cells': 'not-a-list'})


def test_embedded_sta_errors_are_not_measured_success():
    text = 'BEHAVE_PPA_SETUP_NS 2.0\nBEHAVE_PPA_HOLD_NS 0.1\n[WARNING STA-1234] missing parasitics'
    assert eda.timing_result(text)['status'] == 'unresolved'


def test_repair_precedes_all_final_measurement_exports_without_retiming():
    s = eda.yosys_script(source(), experiment().timing, repair_design=True)
    invocation = s.index('exec /tools/openroad')
    assert s.index('pre_repair.v') < invocation < s.index('read_verilog repaired.v')
    assert invocation < s.index('stat -json') < s.index('write_verilog -noattr simulation.v')
    assert 'yosys @' not in s and 'yosys exec ' not in s
    assert 'retime' not in s
    assert 'openroad' not in eda.yosys_script(source(), experiment().timing)


def test_repair_uses_shared_constraints_and_fails_on_tcl_error():
    s = mapping_repair.script('M', experiment().timing)
    assert 'read_verilog pre_repair.v' in s and 'period 20.0' in s
    assert 'repair_design -buffer_gain 4' in s
    assert 'set_max_fanout 32' in s
    assert 'catch {' in s and 'exit 1' in s and 'BEHAVE_MAPPING_REPAIR_COMPLETE' in s
    assert 'estimate_parasitics' not in s and 'repair_timing' not in s


@pytest.mark.parametrize('technology_name', ['sky130hd', 'nangate45'])
def test_constants_are_mapped_before_openroad_not_patched_after_export(technology_name):
    from behave.ppa.technology import technology
    profile = technology(technology_name)
    s = eda.yosys_script(source(), experiment().timing, repair_design=True,
        technology_name=technology_name)
    tie = f"hilomap -hicell {' '.join(profile.tie_high)} -locell {' '.join(profile.tie_low)}"
    assert s.index('check -assert') < s.index(tie) < s.index('write_json mapped.json')
    assert s.index(tie) < s.index('exec /tools/openroad') < s.index('read_verilog repaired.v')
    assert '-singleton' not in s and 'setundef' not in s
    assert 'hilomap' not in eda.yosys_script(source(), experiment().timing)


def test_sta_keeps_all_checks_and_proves_constants_without_timing_exceptions():
    s = eda.sta_script('M', experiment().timing)
    assert 'sta::check_timing_cmd 1 1 1 1 1 1 1' in s
    assert 'sta::pin_sim_logic_value $pin' in s
    assert 'BEHAVE_PPA_CONSTANT_ENDPOINT' in s
    for forbidden in ('set_false_path', 'set_case_analysis', 'set_disable_timing', 'set_max_delay'):
        assert forbidden not in s
    assert eda.timing_result('BEHAVE_PPA_CONSTANT_ENDPOINT out[7] 0\n'
        'BEHAVE_PPA_SETUP_NS 1.0\nBEHAVE_PPA_HOLD_NS 0.1\n')['status'] == 'met'
    assert eda.timing_result('Warning: There are 1 unconstrained endpoints.\n  dynamic_output\n'
        'BEHAVE_PPA_SETUP_NS 1.0\nBEHAVE_PPA_HOLD_NS 0.1\n')['status'] == 'unresolved'


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize('technology_name', ['sky130hd', 'nangate45'])
def test_native_constant_outputs_survive_repair_and_match_behavior(tmp_path, technology_name):
    from behave.core.artifacts import BlobRef
    from behave.core.paths import repo_root
    from behave.ppa.records import read_document
    from behave.ppa.technology import technology
    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    profile = technology(technology_name)
    _, task, _ = fixture_task()
    # Mixed bus: real registered data with a constant zero and a constant one.
    # The registered data also contains constant bits, exercising internal ties.
    rtl = file('top.sv', REGISTERED_RTL.replace(b'assign out_y = data;',
        b"assign out_y = {1'b0, 1'b1, data[5:0]};").replace(b'data <= in_a;',
        b"data <= {1'b0, 1'b1, in_a[5:0]};"))
    b = file('behavior.py', b'class Identity:\n def reset(self): pass\n'
        b' def process(self,in_a): return {"out_y": (in_a & 63) | 64}\n')
    inputs = ComparisonInputs('rtl',
        LoadedSource(SourceArtifact((rtl.reference,), 'top.sv', 'M'), (rtl,)),
        LoadedSource(SourceArtifact((b.reference,), 'behavior.py', 'Identity'), (b,)),
        task.contract, task.run_policy, task.judge_policy)
    exp = replace(experiment(), mapping=MappingPolicy(technology=profile.name,
        driving_cell='BUF_X1' if profile.name == 'nangate45' else 'sky130_fd_sc_hd__buf_2',
        repair_design=True, placement_repair=True))
    native = NativePPA(liberty=profile.liberty,
        sta=repo_root()/'external/ppa-activity/opensta/usr/bin/sta', technology_name=profile.name)
    store = DirectoryStore(tmp_path/'objects')
    result = native.run(inputs, exp, 'eda', (), store)
    measured = result.get('eda', {})
    assert measured.get('status') == 'measured', result
    assert measured['timing']['status'] == 'met', measured['log']
    outputs = read_document(store, BlobRef.from_dict(measured['evidence']))['outputs']
    for filename in ('pre_repair.v', 'repaired.v', 'mapped.v'):
        netlist = store.read(BlobRef.from_dict(outputs[filename])).decode()
        assert profile.tie_high[0] in netlist and profile.tie_low[0] in netlist, filename
    mapped = SourceArtifact.from_dict(measured['simulation_source'])
    loaded = LoadedSource(mapped, tuple(FileData(f, store.read(f.content)) for f in mapped.files))
    replay = native.run(replace(inputs, source=loaded), exp, 'mapped', (), store)
    assert replay['score'] == 1, replay
    assert replay['performance']['time_ns'] == 100.


@pytest.mark.parametrize('technology_name', ['sky130hd', 'nangate45'])
def test_placed_hold_repair_precedes_all_exports_and_keeps_acceptance(technology_name):
    s = mapping_repair.script('M', experiment().timing, placement_repair=True,
        technology_name=technology_name)
    repair = s.index('repair_timing -hold\n')
    assert s.index('estimate_parasitics -placement') < repair
    assert repair < s.rindex('detailed_placement') < s.rindex('estimate_parasitics -placement')
    assert s.rindex('estimate_parasitics -placement') < s.index('write_db physical.odb')
    assert s.index('write_db physical.odb') < s.index('write_def placed.def') < s.index('write_verilog repaired.v')
    assert s.count('write_db physical.odb') == 1
    assert 'set_clock_uncertainty 0.1 ' in s
    assert '-allow_setup_violations' not in s and 'set_propagated_clock' not in s
    assert 'BEHAVE_HOLD_REPAIR_BEFORE_NS' in s and 'BEHAVE_HOLD_REPAIR_AFTER_NS' in s
    # Running repair is not a pass: either remaining negative slack still fails.
    for setup, hold in ((0.3, -0.001), (-0.001, 0.3)):
        result = eda.timing_result(f'BEHAVE_PPA_SETUP_NS {setup}\nBEHAVE_PPA_HOLD_NS {hold}\n')
        assert result['status'] == 'violated'


@pytest.mark.eda
@pytest.mark.isolation
def test_native_hold_repair_fixes_short_paths_without_changing_function(tmp_path):
    import re
    from behave.core.paths import repo_root
    from behave.ppa.records import read_document
    from behave.ppa.technology import technology
    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs
    from behave.core.artifacts import BlobRef

    profile = technology('nangate45')
    _, task, _ = fixture_task()
    inputs = ComparisonInputs('rtl', source(), task.reference_behavior, task.contract,
        task.run_policy, task.judge_policy)
    native = NativePPA(liberty=profile.liberty,
        sta=repo_root()/'external/ppa-activity/opensta/usr/bin/sta', technology_name=profile.name)
    exp = replace(experiment(), timing=replace(experiment().timing, uncertainty_ns=0.3),
        mapping=MappingPolicy(driving_cell='BUF_X1', technology=profile.name,
            repair_design=True, placement_repair=True))
    store = DirectoryStore(tmp_path/'objects')
    result = native.run(inputs, exp, 'eda', (), store)
    measured = result.get('eda', {})
    assert measured.get('status') == 'measured', result
    assert measured['timing']['status'] == 'met', measured['log']
    assert measured['mapping_repair']['hold_repair'] == 'placement-estimated-ideal-clock'
    before = re.search(r'BEHAVE_HOLD_REPAIR_BEFORE_NS (-?[0-9.]+)', measured['log'])
    after = re.search(r'BEHAVE_HOLD_REPAIR_AFTER_NS (-?[0-9.]+)', measured['log'])
    assert before and after, measured['log']
    assert float(before[1]) < 0 <= float(after[1])
    outputs = read_document(store, BlobRef.from_dict(measured['evidence']))['outputs']
    assert {'physical.odb', 'repaired.v', 'mapped.json', 'simulation.v'} <= outputs.keys()
    mapped = SourceArtifact.from_dict(measured['simulation_source'])
    loaded = LoadedSource(mapped, tuple(FileData(f, store.read(f.content)) for f in mapped.files))
    replay = native.run(replace(inputs, source=loaded), exp, 'mapped', (), store)
    assert replay['score'] == 1, replay
    assert replay['performance']['time_ns'] == 100.


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize('physical', [False, True])
def test_native_repair_exports_measured_netlist_and_preserves_completion(tmp_path, physical):
    from behave.core.paths import repo_root
    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs
    from behave.ppa.records import read_document
    from behave.core.artifacts import BlobRef

    root = repo_root() / 'external/ppa-activity'
    _, task, _ = fixture_task()
    inputs = ComparisonInputs('rtl', source(), task.reference_behavior, task.contract,
                              task.run_policy, task.judge_policy)
    native = NativePPA(liberty=root / 'sky130.lib', sta=root / 'opensta/usr/bin/sta')
    store = DirectoryStore(tmp_path / 'objects')
    from behave.ppa.sky130 import DONT_USE_CELLS
    exp = replace(experiment(), mapping=MappingPolicy(repair_design=True, placement_repair=physical,
        dont_use_cells=DONT_USE_CELLS if physical else ()))
    result = native.run(inputs, exp, 'eda', (), store)
    measured = result.get('eda', {})
    assert measured.get('status') == 'measured', result
    assert measured['mapping_repair']['completed']
    assert measured['area_um2'] > 0
    assert measured['timing']['status'] == 'met'
    outputs = read_document(store, BlobRef.from_dict(measured['evidence']))['outputs']
    assert {'pre_repair.v', 'repaired.v', 'mapped.v', 'simulation.v', 'repair.log'} <= outputs.keys()
    if physical:
        assert {'physical.odb', 'placed.def'} <= outputs.keys()
        assert measured['stage'] == exp.mapping.stage
        assert measured['power']['assumptions']['interconnect'] == 'placement-estimated'
    mapped = SourceArtifact.from_dict(measured['simulation_source'])
    loaded = LoadedSource(mapped, tuple(FileData(f, store.read(f.content)) for f in mapped.files))
    replay = native.run(replace(inputs, source=loaded), exp, 'mapped', (), store)
    assert replay['score'] == 1, replay
    assert replay['performance']['time_ns'] == 100.
