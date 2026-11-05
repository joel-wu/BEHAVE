"""No-Sim development has EDA feedback but the same independent final judge."""

from pathlib import Path

import pytest

from behave.agent.attempt import AttemptSpec
from behave.agent.model import ModelReply, ToolCall
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json
from behave.ppa.finalize import compare_searches
from behave.ppa.prompting import render_objective
from behave.ppa.workflow import run_search
from test_ppa import FakeNative, RTL, experiment, fixture_task


class NoSimBackend:
    def __init__(self, measure=True):
        self.step = 0
        self.measure = measure
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        assert 'compare_rtl' not in {tool['name'] for tool in request.tools}
        assert 'capture_candidate' in {tool['name'] for tool in request.tools}
        assert 'compare_rtl' not in str(request.messages[:2])
        assert 'reference/behavior.py' not in str(request.messages[:2])
        assert 'Write a cocotb testbench' in request.messages[0]['content']
        assert 'asserts that the RTL observations match' in request.messages[0]['content']
        step = self.step
        self.step += 1
        if step == 0:
            name, args = 'write_file', {'path': 'top.sv', 'content': RTL.decode()}
        elif step == 1:
            name, args = 'write_file', {'path': 'model.py', 'content':
                'class Candidate:\n def reset(self): pass\n def process(self,in_a): return {"out_y":0}\n'}
        elif step == 2:
            name, args = 'capture_candidate', {
                'rtl': {'files': ['top.sv'], 'entry_file': 'top.sv', 'entry_symbol': 'M'},
                'behavior': {'files': ['model.py'], 'entry_file': 'model.py', 'entry_symbol': 'Candidate'}}
        elif step == 3:
            content = request.messages[-2]['content']
            assert set(content) == {'candidate', 'status', 'reference_source'}
            assert content['status'] == 'captured'
            name, args = ('measure_ppa', {'candidate': content['candidate']}) if self.measure else ('finish_search', {})
        elif step == 4 and self.measure:
            content = request.messages[-2]['content']
            assert set(content) == {'candidate', 'eda'}
            assert content['eda']['status'] == 'measured'
            assert 'performance' not in content and 'targets_met' not in content
            name, args = 'finish_search', {}
        else:
            raise AssertionError('cold replay must not generate')
        return ModelReply('', (ToolCall(str(step), name, args),))


@pytest.mark.parametrize('measure', [False, True])
def test_capture_without_sim_survives_restart_and_final_uses_golden(tmp_path, measure):
    public, task, _ = fixture_task()

    class Native(FakeNative):
        calls = []
        final = False
        def run(self, inputs, exp, mode, pool, objects):
            self.calls.append(mode)
            if not self.final:
                assert mode == 'eda', 'no development simulation is allowed'
                assert inputs.reference_behavior.artifact.entry_symbol == 'Candidate'
            else:
                assert inputs.reference_behavior == task.reference_behavior
                from behave.ppa.finalize import final_baseline
                from behave.ppa.workflow import _union
                assert pool == _union(s.as_dict() for s in final_baseline(task).stimuli)
            return super().run(inputs, exp, mode, pool, objects)

    native, backend = Native(), NoSimBackend(measure)
    options = dict(public=public, task=task, experiment=experiment(),
        spec=AttemptSpec('no-sim', 'ppa', {'model': 'fixture'}), backend=backend,
        native=native, directory=tmp_path / 'search', behavior_source='agent', sim_feedback=False,
        prompt=render_objective(100, sim_feedback=False))
    result = run_search(**options)
    assert native.calls == (['eda'] if measure else [])
    assert result['measurement_requests'] == int(measure)
    assert result['evaluation_status'] == 'awaiting_final'
    candidate, = result['candidates']
    assert 'score' not in candidate and 'performance' not in candidate
    assert candidate['source_reference']['entry_symbol'] == 'Candidate'
    archive = JournalDirectory(tmp_path / 'search')
    cfg = parse_json(archive.objects.read(BlobRef.from_dict(result['configuration'])))
    assert cfg['sim_feedback'] is False
    assert cfg['development_setting'] == 'joint'
    assert run_search(**options) == result
    assert native.calls == (['eda'] if measure else [])
    with pytest.raises(RecordError, match='replay differs'):
        run_search(**{**options, 'sim_feedback': True})
    native.final = True
    final = compare_searches(searches={'agent-no-sim/r00': tmp_path / 'search'}, task=task,
        experiment=experiment(), native=native, directory=tmp_path / 'final')
    assert final['frontiers']['agent-no-sim/r00'] == [candidate['candidate']]
    assert native.calls.count('eda') == 1
    assert native.calls.count('final') == 1
    from behave.ppa.records import read_document
    final_store = JournalDirectory(tmp_path / 'final')
    manifest = read_document(final_store.objects, BlobRef.from_dict(final['manifest']))
    baseline = read_document(final_store.objects, BlobRef.from_dict(manifest['baseline']))
    assert baseline['stimuli']
    assert baseline['policy']['case_limit'] == 2


@pytest.mark.parametrize('native_shell', [False, pytest.param(True, marks=pytest.mark.isolation)])
def test_no_sim_mode_routes_through_normal_cli(tmp_path, monkeypatch, capsys, native_shell):
    from types import SimpleNamespace
    from test_ppa_cli import published
    import behave.ppa.__main__ as cli
    import behave.agent.process_model as process
    import behave.task_execution.shell as shell
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.agent.tools import file_tools

    common = published(tmp_path)
    Path(tmp_path / 'prompt.txt').write_text(render_objective(100, sim_feedback=False))
    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kwargs: FakeNative())
    class ShellBackend(NoSimBackend):
        def generate(self, request):
            reply = super().generate(request)
            call, = reply.calls
            if call.name != 'write_file':
                return reply
            code = ('from pathlib import Path; import fp_lib, memory_driver; '
                "assert Path('/docs/development.md').is_file(); "
                "assert Path('/docs/behavior.md').is_file(); "
                "assert not Path('reference/behavior.py').exists(); "
                f"Path({call.arguments['path']!r}).write_text({call.arguments['content']!r})")
            return ModelReply('', (ToolCall(call.call_id, 'run_command', {
                'command': "python3 - <<'PY'\n" + code + "\nPY"}),))
    monkeypatch.setattr(process, 'ProcessModelBackend',
                        lambda command: ShellBackend() if native_shell else NoSimBackend())
    monkeypatch.setattr(LocalChatEndpoint, 'from_dict', lambda row: SimpleNamespace(
        model_policy=lambda: {'model': 'fixture'}, command=lambda: 'not-executed'))
    if not native_shell:
        def development_shell(*args, **kwargs):
            assert kwargs['setting'] == 'joint'
            return SimpleNamespace(tool=lambda: file_tools()[1])
        monkeypatch.setattr(shell, 'development_shell', development_shell)
    assert cli.main(['search', *common, '--provider', str(tmp_path / 'provider.json'),
        '--prompt', str(tmp_path / 'prompt.txt'), '--behavior-source', 'agent',
        '--sim-feedback', 'disabled', '--output', str(tmp_path / 'search')]) == 0
    assert parse_json(capsys.readouterr().out.encode())['evaluation_status'] == 'awaiting_final'


@pytest.mark.eda
@pytest.mark.isolation
def test_real_final_rejects_no_sim_rtl_specialized_to_public_workload(tmp_path):
    import os
    from behave.agent.cpu_resources import CpuResources
    from behave.core.artifacts import FileData, LoadedSource, SourceArtifact
    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    root = Path(os.environ.get('BEHAVE_PPA_ROOT', Path(__file__).resolve().parents[2] / 'external/ppa-activity'))
    if not (root / 'sky130.lib').is_file():
        pytest.skip('explicit SKY130 installation required')
    public, task, _ = fixture_task()
    limited = RTL.replace(b'assign out_y = in_a;',
        b"assign out_y = (in_a==3 || in_a==7 || in_a==11 || in_a==19) ? in_a : 8'hff;")

    class Backend(NoSimBackend):
        def generate(self, request):
            reply = super().generate(request)
            call, = reply.calls
            if self.step == 1:
                return ModelReply('', (ToolCall(call.call_id, 'write_file',
                    {'path': 'top.sv', 'content': limited.decode()}),))
            return reply

    native = NativePPA(liberty=root / 'sky130.lib', sta=root / 'opensta/usr/bin/sta',
                       cpu_resources=CpuResources(1, cpus=2))
    result = run_search(public=public, task=task, experiment=experiment(),
        spec=AttemptSpec('no-sim-overfit-fixture', 'ppa', {'model': 'scripted'}),
        backend=Backend(measure=False), native=native, directory=tmp_path / 'search',
        behavior_source='agent', sim_feedback=False, prompt=render_objective(100, sim_feedback=False))
    candidate, = result['candidates']
    store = JournalDirectory(tmp_path / 'search')
    source = SourceArtifact.from_dict(candidate['source'])
    loaded = LoadedSource(source, tuple(FileData(f, store.objects.read(f.content)) for f in source.files))
    inputs = ComparisonInputs('rtl', loaded, task.reference_behavior, task.contract,
                              task.run_policy, task.judge_policy)
    perf = native.run(inputs, experiment(), 'performance', (), store.objects)
    assert perf['score'] == 1, perf
    final = compare_searches(searches={'no-sim': tmp_path / 'search'}, task=task,
        experiment=experiment(), native=native, directory=tmp_path / 'final')
    assert final['candidates'][candidate['candidate']]['score'] == 0
    assert final['frontiers']['no-sim'] == []
