"""The same compare call evaluates frozen candidates with new measurement inputs."""

from dataclasses import replace

import pytest

from behave.agent.attempt import AttemptSpec
from behave.core.artifacts import BlobRef
from behave.ppa.finalize import compare_searches, finalized_report
from behave.ppa.config import Experiment
from behave.ppa.records import read_document
from behave.ppa.report import summary
from behave.ppa.workflow import run_search
from test_ppa import FakeNative, Scripted, experiment, fixture_task


class Measured(FakeNative):
    def __init__(self):
        self.calls = []

    def run(self, inputs, exp, mode, pool, objects):
        self.calls.append((mode, exp.identity))
        result = super().run(inputs, exp, mode, pool, objects)
        if mode == 'final':
            result['performance']['time_ns'] = len(exp.workloads[0].inputs) * exp.timing.period_ns
        return result


def search(tmp_path):
    public, task, _ = fixture_task()
    result = run_search(public=public, task=task, experiment=experiment(),
        spec=AttemptSpec('search', 'ppa', {'model': 'fake'}), backend=Scripted(), native=FakeNative(),
        directory=tmp_path / 'search', prompt='develop')
    return task, result


def test_new_workload_reuses_eda_but_rechecks_all_candidates_and_preserves_search(tmp_path):
    task, old = search(tmp_path)
    before = finalized_report(tmp_path / 'search')[1]
    exp = experiment()
    changed = replace(exp, workloads=(replace(exp.workloads[0], inputs=((1,), (2,), (3,), (4,), (5,), (6,))),),
                      targets_ns=(110., 130.))
    native = Measured()
    args = dict(searches={'one': tmp_path / 'search'}, task=task, experiment=changed,
                native=native, directory=tmp_path / 'new-final')
    result = compare_searches(**args)
    assert native.calls == [('final', changed.identity)]
    assert len(result['candidates']) == len(old['candidates'])
    assert summary(tmp_path / 'new-final')['methods']['one']['targets'][0]['candidate'] is None
    assert summary(tmp_path / 'new-final')['methods']['one']['targets'][1]['time_ns'] == 120
    run, _, final = finalized_report(tmp_path / 'new-final')
    manifest = read_document(run.objects, BlobRef.from_dict(final['manifest']))
    assert Experiment.from_dict(manifest['experiment']) == changed
    assert manifest['source_configurations']['one'] == old['configuration']
    assert finalized_report(tmp_path / 'search')[1] == before
    assert compare_searches(**args) == result
    assert native.calls == [('final', changed.identity)]


@pytest.mark.parametrize('change', ['clock', 'library', 'mapping', 'power', 'native'])
def test_eda_configuration_changes_invalidate_reuse(tmp_path, change):
    task, _ = search(tmp_path)
    exp, native = experiment(), Measured()
    if change == 'clock':
        exp = replace(exp, timing=replace(exp.timing, period_ns=10.))
    elif change == 'mapping':
        exp = replace(exp, sat_resource_sharing=not exp.sat_resource_sharing)
    elif change == 'power':
        exp = replace(exp, power=replace(exp.power, input_activity=.2))
    elif change == 'native':
        native.identity = 'new-service'
    else:
        class NewLibrary(Measured):
            technology = {'library': 'another-library'}
        native = NewLibrary()
    compare_searches(searches={'one': tmp_path / 'search'}, task=task, experiment=exp,
                     native=native, directory=tmp_path / 'final')
    assert sorted(mode for mode, _ in native.calls) == ['eda', 'final']


def test_new_judge_policy_is_recorded_but_task_revision_must_match(tmp_path):
    from behave.task_execution.judge_policy import JudgePolicy

    task, _ = search(tmp_path)
    policy = replace(JudgePolicy.from_bytes(task.judge_policy), seed='new-fixed-baseline')
    changed = replace(task, judge_policy=policy.to_bytes())
    result = compare_searches(searches={'one': tmp_path / 'search'}, task=changed, experiment=experiment(),
                             native=Measured(), directory=tmp_path / 'new-policy')
    run, _, _ = finalized_report(tmp_path / 'new-policy')
    manifest = read_document(run.objects, BlobRef.from_dict(result['manifest']))
    selected = replace(policy, score_timeout_seconds=experiment().service_seconds).to_bytes()
    assert run.objects.read(BlobRef.from_dict(manifest['evaluation']['judge_policy'])) == selected
    with pytest.raises(ValueError, match='task, contract or reference'):
        compare_searches(searches={'one': tmp_path / 'search'},
                         task=replace(task, revision=BlobRef.of(b'changed-spec')), experiment=experiment(),
                         native=Measured(), directory=tmp_path / 'wrong-task')


def test_interrupted_final_reuses_settled_failure_and_retries_only_dangling_candidate(tmp_path):
    from behave.agent.model import ModelReply, ToolCall
    from test_ppa import RTL

    class SecondDesign(Scripted):
        def generate(self, request):
            if self.index == 0:
                self.index += 1
                return ModelReply('', (ToolCall('0', 'write_file', {
                    'path': 'top.sv', 'content': RTL.decode() + '\n// second candidate\n'}),))
            return super().generate(request)

    public, task, _ = fixture_task()
    searches = {}
    for name, backend in (('one', Scripted()), ('two', SecondDesign())):
        searches[name] = tmp_path / name
        run_search(public=public, task=task, experiment=experiment(),
            spec=AttemptSpec(name, 'ppa', {'model': 'fake'}), backend=backend,
            native=FakeNative(), directory=searches[name], prompt='develop')

    class Interrupted(Measured):
        def run(self, inputs, exp, mode, pool, objects):
            if self.calls:
                raise RuntimeError('operator stopped old worker')
            result = super().run(inputs, exp, mode, pool, objects)
            result['score'] = result['performance']['score'] = 0
            return result

    old = tmp_path / 'interrupted'
    args = dict(searches=searches, task=task, experiment=experiment())
    with pytest.raises(RuntimeError, match='operator stopped'):
        compare_searches(**args, native=Interrupted(), directory=old)
    native = Measured()
    result = compare_searches(**args, native=native, directory=tmp_path / 'replacement', reuse_final=old)
    assert len(native.calls) == 1
    assert result['reused_service_calls'] == 1
    assert sorted(row['score'] for row in result['candidates'].values()) == [0, 1]
    assert compare_searches(**args, native=native, directory=tmp_path / 'replacement', reuse_final=old) == result
    assert len(native.calls) == 1
    again = Measured()
    completed = compare_searches(**args, native=again, directory=tmp_path / 'all-reused',
                                reuse_final=tmp_path / 'replacement')
    assert again.calls == []
    assert completed['candidates'] == result['candidates']
    with pytest.raises(ValueError, match='identical suite'):
        compare_searches(**{**args, 'experiment': replace(experiment(), targets_ns=(999.,))},
                         native=Measured(), directory=tmp_path / 'changed', reuse_final=old)
