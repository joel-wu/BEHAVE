"""Shared saved-review operators, independent of checkout launch scripts."""

import json
import signal
from pathlib import Path

import pytest
import test_rejudge

from behave.eval import report as report_module
from behave.eval import scoring as scoring_module

local_rollout = test_rejudge.local_rollout


@pytest.mark.parametrize('failure', [False, True])
def test_score_wrapper_preserves_shared_resources_selection_and_cancellation(tmp_path, monkeypatch, failure):
    events = []
    original_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    monkeypatch.setattr('behave.core.process_limits.configure_worker_process_limit', lambda: events.append('limits'))
    monkeypatch.setattr('behave.isolation.runtime_environment.runtime_environment', lambda: events.append('runtime'))
    pool, resources, selection = object(), object(), {'selected': 'existing candidate'}

    def saved(output, name, **options):
        assert events == ['limits', 'runtime']
        assert output == tmp_path and name == 'retry'
        assert options['cpu_pool'] is pool and options['cpu_resources'] is resources
        assert options['selection'] is selection and options['retry_testbench'] is False
        assert options['previous'] == 'original' and options['all_submitted'] is True
        assert options['retry_codes'] == ('fixture-infrastructure',)
        assert options['workers'] == 7 and options['cpu_capacity'] == 3
        assert options['stage_from'] == tmp_path / 'local'
        assert options['judge_policy'] == tmp_path / 'policy.json'
        assert not options['cancelled']()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert options['cancelled']()
        if failure:
            raise RuntimeError('fixture score failure')
        return 'preserved report'

    monkeypatch.setattr(scoring_module, 'score_saved', saved)
    options = {'workers': 7, 'previous': 'original', 'judge_policy': tmp_path / 'policy.json', 'cpu_pool': pool,
               'stage_from': tmp_path / 'local', 'all_submitted': True, 'retry_codes': ('fixture-infrastructure',),
               'cpu_capacity': 3, 'selection': selection, 'retry_testbench': False, 'cpu_resources': resources}
    if failure:
        with pytest.raises(RuntimeError, match='fixture score failure'):
            scoring_module.score_review(tmp_path, 'retry', **options)
    else:
        assert scoring_module.score_review(tmp_path, 'retry', **options) == 'preserved report'
    assert original_handlers == {sig: signal.getsignal(sig) for sig in original_handlers}


def test_export_cli_reports_missing_input_without_creating_a_run(tmp_path, capsys):
    output = tmp_path / 'absent'
    assert report_module.main([str(output)]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error['code'] == 'review-export-invalid' and 'trace_index.json' in error['message']
    assert not output.exists()


@pytest.mark.parametrize('missing', ['run', 'judgment'])
def test_export_rejects_missing_records_without_replacing_an_existing_review(tmp_path, capsys, missing):
    (tmp_path / 'trace_index.json').write_text('[]')
    (tmp_path / 'REVIEW.md').write_text('previous recorded review')
    args = [str(tmp_path)]
    if missing == 'judgment':
        (tmp_path / 'run').mkdir()
        args += ['--judgment', 'misspelled']
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert report_module.main(args) == 1
    error = json.loads(capsys.readouterr().out)
    assert error['code'] == 'review-export-invalid'
    assert ('misspelled' if missing == 'judgment' else 'run') in error['message']
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    assert not (tmp_path / 'run/judgments').exists()


@pytest.mark.parametrize('judgment', [None, 'in-progress'])
def test_export_allows_a_real_incomplete_run_without_starting_it(tmp_path, judgment):
    (tmp_path / 'trace_index.json').write_text(json.dumps([{'task': 'pending', 'run_key': 'candidate'}]))
    (tmp_path / 'run').mkdir()
    if judgment is not None:
        (tmp_path / 'run/judgments' / judgment).mkdir(parents=True)
    before = set((tmp_path / 'run').rglob('*'))
    report_module.export_review(tmp_path, judgment=judgment)
    assert '| pending | waiting | 0 | 0 | pending |' in (tmp_path / 'REVIEW.md').read_text()
    assert set((tmp_path / 'run').rglob('*')) == before


def test_interrupted_review_publication_keeps_the_previous_index(tmp_path, monkeypatch):
    (tmp_path / 'trace_index.json').write_text('[]')
    (tmp_path / 'run').mkdir()
    review = tmp_path / 'REVIEW.md'
    review.write_text('previous complete review')
    replace = Path.replace

    def publish(source, target):
        if target == review:
            assert source != review and source.read_text().startswith('# Eval interactive review')
            raise OSError('fixture review publication failed')
        return replace(source, target)

    monkeypatch.setattr(Path, 'replace', publish)
    with pytest.raises(OSError, match='review publication failed'):
        report_module.export_review(tmp_path)
    assert review.read_text() == 'previous complete review'
    assert not list(tmp_path.glob('.REVIEW.md.*.tmp'))


def test_export_selects_the_recorded_judgment_without_rejudging_or_rewriting(tmp_path, publication,
                                                                           local_rollout, monkeypatch):
    from test_eval_followup_recovery import review_inputs
    from test_testbench_reports import ReportBackend, measured

    from behave.task_execution.rejudge import rejudge_batch

    source, root, original = review_inputs(tmp_path, publication)
    rows = [{'task': row['task_id'], 'run_key': row['run_key'],
             'trace': f"traces/{row['task_id']}/trace.md"} for row in original.summary['candidates']]
    (source / 'trace_index.json').write_text(json.dumps(rows))
    judge = test_rejudge.Judge(unavailable=False)
    rejudge_batch(directory=root.directory, name='fixed', reader=publication.reader, judge=judge,
                  testbench=ReportBackend(measured, measured))
    before = {p: p.read_bytes() for p in root.directory.rglob('*') if p.is_file()}
    calls = list(local_rollout)
    monkeypatch.setattr(judge, 'evaluate', lambda *_: pytest.fail('export does not judge'))
    monkeypatch.setattr('behave.task_execution.workflow.run_solver_process',
                        lambda *a, **kw: pytest.fail('export does not generate'))
    for judgment, expected in ((None, None), ('fixed', 1), (None, None)):
        report_module.export_review(source, judgment=judgment)
        readable = (source / 'REVIEW.md').read_text()
        assert f"Scoring pass: {judgment if judgment is not None else 'original'}." in readable
        assert ('--judgment fixed' in readable) == (judgment == 'fixed')
        for row in rows:
            assessment = json.loads((source / 'traces' / row['task'] / 'assessment.json').read_text())
            assert assessment['rtl']['score'] == assessment['behavior']['score'] == expected
        assert {p: p.read_bytes() for p in root.directory.rglob('*') if p.is_file()} == before
    assert local_rollout == calls


def test_eval_export_routes_to_the_same_report_operator(tmp_path, monkeypatch):
    from behave.eval.__main__ import main

    calls = []
    monkeypatch.setattr(report_module, 'main', lambda args: calls.append(args) or 17)
    assert main(['export', str(tmp_path), '--judgment', 'recorded']) == 17
    assert calls == [[str(tmp_path), '--judgment', 'recorded']]


REVIEW_INSTALL_PROBE = '''
import importlib.abc
import json
import sys
from pathlib import Path
class ReadOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'tools', 'torch', 'sglang', 'slime', 'ray'} or fullname in {
            'behave.agent.loop', 'behave.eval.runner', 'behave.eval.generation',
            'behave.eval.followup'} or fullname.startswith('behave.sim'):
            raise AssertionError('report imported execution: ' + fullname)
sys.meta_path.insert(0, ReadOnly())
from behave.eval.report import export_review
source = Path(sys.argv[1])
originals = {p: p.read_bytes() for p in (source / 'run').rglob('*') if p.is_file()}
export_review(source)
index = json.loads((source / 'trace_index.json').read_text())
assert len(index) == 2
assert (source / 'REVIEW.md').exists()
for item in index:
    target = source / Path(item['trace']).parent
    assessment = json.loads((target / 'assessment.json').read_text())
    assert assessment['behavior']['status'] == assessment['rtl']['status'] == 'sim_no_conclusion'
    assert assessment['behavior']['score'] is None and assessment['rtl']['score'] is None
    assert (target / 'review.md').exists()
    assert (target / 'submission.json').exists()
assert {p: p.read_bytes() for p in (source / 'run').rglob('*') if p.is_file()} == originals
review = (source / 'REVIEW.md').read_bytes()
try:
    export_review(source, judgment='missing-recorded-pass')
except FileNotFoundError as error:
    assert 'missing-recorded-pass' in str(error)
else:
    raise AssertionError('missing judgment must not look like a pending review')
assert (source / 'REVIEW.md').read_bytes() == review
assert not (source / 'run/judgments/missing-recorded-pass').exists()
assert {p: p.read_bytes() for p in (source / 'run').rglob('*') if p.is_file()} == originals
'''
