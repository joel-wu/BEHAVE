"""Reference calibration uses published semantics and the normal PPA backend."""

import importlib.util
from pathlib import Path
import sys

import pytest

from behave.core.records import canonical_json, parse_json
from behave.task_execution.judge_policy import JudgePolicy
from test_ppa import FakeNative, RTL, experiment
from test_ppa_cli import published


SPEC = importlib.util.spec_from_file_location('ppa_reference_preflight',
    Path(__file__).resolve().parents[2] / 'tools/ppa_gated_deltanet_preflight.py')
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


@pytest.mark.parametrize('parallel', [False, True])
@pytest.mark.parametrize('final_baseline', [False, True])
@pytest.mark.parametrize('explicit_rtl', [False, True])
def test_reference_preflight_binds_published_behavior_and_cpu_budget(tmp_path, monkeypatch, capsys, parallel, final_baseline, explicit_rtl):
    published(tmp_path)
    source = tmp_path / 'data/problems/ai_ml/identity'
    source.mkdir(parents=True)
    (source / 'rtl.sv').write_bytes(RTL.replace(b'module M(', b'module identity('))
    alternative = tmp_path / 'private-variant.sv'
    variant_bytes = (source / 'rtl.sv').read_bytes() + b'\n// private calibration variant\n'
    if explicit_rtl:
        alternative.write_bytes(variant_bytes)
    # No local behavior.py: its dependency bundle must come from the release.
    measurement = experiment().as_dict()
    measurement.pop('schema')
    measurement.pop('workloads')
    study = {'schema': 'behave.ppa-study/1', 'task': 'identity',
        'workloads': [{'name': 'stream', 'generator': 'explicit', 'inputs': [[3], [7], [11], [19]]}],
        'measurement': measurement,
        'checks': {'baseline_cases': 4, 'sequence_length': 16},
        'search': {'replicates': 1, 'parallelism': 1, 'turns': 60,
                   'request_seconds': None, 'replicate_seconds': 14400, 'final_seconds': 14400}}
    (tmp_path / 'study.json').write_bytes(canonical_json(study))
    calls, capacities = [], []

    class Native(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            calls.append(mode)
            assert inputs.reference_behavior.artifact.entry_symbol == 'Identity'
            assert inputs.source.artifact.entry_symbol == 'identity'
            assert inputs.source.files[0].data.startswith(b'module identity(')
            assert inputs.source.files[0].data == (variant_bytes if explicit_rtl else (source / 'rtl.sv').read_bytes())
            policy = JudgePolicy.from_bytes(inputs.judge_policy)
            assert (policy.baseline_cases, policy.sequence_length, policy.search_seconds) == (
                (50, 100, 0) if final_baseline else (4, 16, 0))
            assert exp.workloads[0].inputs == ((3,), (7,), (11,), (19,))
            return super().run(inputs, exp, mode, pool, objects)

    def native(**kwargs):
        capacities.append(kwargs['cpu_resources'].snapshot()['capacity'])
        return Native()

    import behave.ppa.worker as worker
    monkeypatch.setattr(worker, 'NativePPA', native)
    monkeypatch.setattr(preflight, 'REPO', tmp_path)
    args = ['preflight', str(tmp_path / 'out'), '--study', str(tmp_path / 'study.json'),
            '--objects', str(tmp_path / 'inputs'), '--release', str(tmp_path / 'release.json'),
            '--stages', 'eda', 'performance', '--cpus', '1']
    if final_baseline:
        args += ['--baseline-cases', '50', '--sequence-length', '100']
    if explicit_rtl:
        args += ['--rtl', str(alternative)]
    monkeypatch.setattr(sys, 'argv', args + (['--parallel'] if parallel else []))
    assert preflight.main() == 0
    assert calls == ['eda', 'performance'] and capacities == [1]
    record = parse_json((tmp_path / 'out/result.json').read_bytes())
    assert record['state'] == 'finished'
    assert record['release'] and record['task_revision']
    assert record.get('calibration_rtl') == (str(alternative) if explicit_rtl else None)
    assert record['study']['workloads'] == study['workloads']
    assert record['results']['eda']['eda']['log'] == 'yosys'
    assert 'yosys' not in capsys.readouterr().out


def test_failed_clock_is_not_a_pass_despite_correct_workload():
    assert not preflight.stage_passed('eda', {'eda': {
        'status': 'measured', 'area_um2': 10, 'timing': {'status': 'violated'}}})
    assert preflight.stage_passed('performance', {'score': 1,
        'performance': {'score': 1, 'time_ns': 1300}})
