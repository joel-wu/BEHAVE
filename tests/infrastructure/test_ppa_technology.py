"""Technology changes reuse one flow without changing public constraint units."""

from dataclasses import replace

import pytest
from test_ppa import experiment

from behave.ppa import eda, mapping_repair
from behave.ppa.config import Experiment, MappingPolicy
from behave.ppa.technology import technology


@pytest.mark.parametrize('technology_name', ['sky130hd', 'nangate45'])
def test_explicit_assets_root_supplies_same_physical_inputs(tmp_path, monkeypatch, technology_name):
    from behave.core import paths

    monkeypatch.setattr(paths, '__file__', str(tmp_path / 'installed/behave/core/paths.py'))
    monkeypatch.setenv('BEHAVE_REPO_ROOT', str(tmp_path / 'assets'))
    profile = technology(technology_name)
    for relative in (profile.liberty_path, profile.tech_lef_path, profile.cells_lef_path):
        asset = tmp_path / 'assets' / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b'fixture')
    assert profile.liberty == tmp_path / 'assets' / profile.liberty_path
    mounts = profile.lef_mounts
    assert [(m.source, m.target) for m in mounts] == [
        (str(tmp_path / 'assets' / profile.tech_lef_path), '/pdk/technology.lef'),
        (str(tmp_path / 'assets' / profile.cells_lef_path), '/pdk/cells.lef')]
    before = mounts[0].sha256
    (tmp_path / 'assets' / profile.tech_lef_path).write_bytes(b'changed fixture')
    assert profile.lef_mounts[0].sha256 != before


def test_unconfigured_installed_repair_is_optional_not_a_fake_path(tmp_path, monkeypatch):
    from behave.core import paths

    monkeypatch.setattr(paths, '__file__', str(tmp_path / 'installed/behave/core/paths.py'))
    monkeypatch.delenv('BEHAVE_REPO_ROOT', raising=False)
    assert mapping_repair.runtime(required=False) is None
    with pytest.raises(paths.RepositoryUnavailable, match='BEHAVE_REPO_ROOT'):
        mapping_repair.runtime()


def test_optional_repair_does_not_hide_other_path_errors(monkeypatch):
    def broken():
        raise OSError('asset root is inaccessible')

    monkeypatch.setattr(mapping_repair, 'repo_root', broken)
    with pytest.raises(OSError, match='inaccessible'):
        mapping_repair.runtime(required=False)


def test_technology_roundtrip_preserves_default_experiment_identity():
    old = experiment()
    assert old.as_dict()['mapping'] == {'driving_cell': 'sky130_fd_sc_hd__buf_2'}
    new = replace(old, mapping=MappingPolicy(driving_cell='BUF_X1', technology='nangate45',
        repair_design=True, placement_repair=True))
    assert new.identity != old.identity
    assert Experiment.from_dict(new.as_dict()) == new
    assert Experiment.from_dict(old.as_dict()) == old
    with pytest.raises(ValueError, match='unknown PPA technology'):
        MappingPolicy(technology='unknown')


@pytest.mark.parametrize('unit', ['ff', '"ff"'])
def test_native_liberty_units_and_public_pf_load(unit):
    lib = ('library (NangateOpenCellLibrary) {\n time_unit : "1ns";\n'
           f' capacitive_load_unit (1,{unit});\n}}').encode()
    description = eda.library_description(lib)
    technology('nangate45').validate_library(description)
    assert description['capacitance_unit'] == 'fF'
    with pytest.raises(ValueError, match='does not match'):
        technology('sky130hd').validate_library(description)
    timing = replace(experiment().timing, output_load_pf=0.01)
    assert 'set_load 10.0 ' in eda.sta_setup('M', timing, technology_name='nangate45')
    assert 'set_load 0.01 ' in eda.sta_setup('M', timing)
    assert 'set_load 10\n' in eda.mapping_constraints(timing, MappingPolicy(driving_cell='BUF_X1'))


def test_selected_physical_profile_used_by_repair_timing_and_power():
    profile = technology('nangate45')
    repair = mapping_repair.script('M', experiment().timing, placement_repair=True,
        technology_name='nangate45')
    sta = eda.sta_setup('M', experiment().timing, placement_repair=True,
        technology_name='nangate45')
    assert 'FreePDK45_38x28_10R_NP_162NW_34O' in repair
    assert profile.rc in repair and profile.rc in sta
    assert 'read_db physical.odb' in sta
    assert 'sky130' not in repair + sta
    assert 'read_lef /pdk/technology.lef' in repair


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize('technology_name', ['sky130hd', 'nangate45'])
def test_native_shared_flow_with_both_technologies(tmp_path, technology_name):
    from test_ppa import fixture_task
    from test_ppa_mapping_repair import source

    from behave.core.paths import repo_root
    from behave.core.storage import DirectoryStore
    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    profile = technology(technology_name)
    _, task, _ = fixture_task()
    inputs = ComparisonInputs('rtl', source(), task.reference_behavior, task.contract,
        task.run_policy, task.judge_policy)
    mapping = MappingPolicy(technology=technology_name,
        driving_cell='BUF_X1' if technology_name == 'nangate45' else 'sky130_fd_sc_hd__buf_2',
        repair_design=True, placement_repair=True)
    native = NativePPA(liberty=profile.liberty,
        sta=repo_root()/'external/ppa-activity/opensta/usr/bin/sta', technology_name=technology_name)
    result = native.run(inputs, replace(experiment(), mapping=mapping), 'eda', (),
        DirectoryStore(tmp_path/'objects'))
    measured = result.get('eda', {})
    assert measured.get('status') == 'measured', result
    assert measured['area_um2'] > 0
    assert measured['timing']['status'] == 'met'
    assert measured['mapping_repair']['physical_profile'] == profile.profile
