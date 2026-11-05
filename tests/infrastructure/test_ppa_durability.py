"""PPA records must survive losing the worker before its finally block runs."""

import json
import multiprocessing
import os

from test_hosted_providers import endpoint, request
from test_ppa_campaign import campaign, plan
from test_spending import CEILING, account, charged

from behave.agent.run_store import JournalDirectory
from behave.core.records import canonical_json
from behave.ppa import launch as launching


def test_hard_worker_exit_keeps_trace_sources_and_account(tmp_path, monkeypatch):
    from behave.core import process_limits
    from behave.isolation import scratch as scratch_module
    from behave.runtime import snapshots as snapshots_module

    root, scratch = tmp_path / 'shared', tmp_path / 'node-scratch'
    root.mkdir()
    scratch.mkdir()
    config = plan()
    campaign.publish(root / 'plan.json', config)
    campaign.publish(root / 'inputs.json', {})
    campaign.publish(root / 'launch-settings.json', {})
    monkeypatch.setattr(scratch_module, 'mkdtemp', lambda **kwargs: str(scratch))
    monkeypatch.setattr(process_limits, 'configure_worker_process_limit', dict)
    monkeypatch.setattr(snapshots_module, 'stage', lambda source, target: target.mkdir())
    monkeypatch.setattr(launching, 'freeze_plan',
                        lambda config, path: campaign.publish(path / 'plan.json', config))
    req = request(endpoint())

    def interrupted(execution, *, snapshot):
        assert execution == root / 'execution'
        assert snapshot == scratch / 'snapshot'
        assert campaign.os.environ['BEHAVE_RUNTIME_DIR'] == str(scratch / 'runtime')
        budget = account(root / 'account', '2')
        outcome = budget.dispatch(req, CEILING, lambda _: charged('0.2'))
        run = JournalDirectory(execution / 'runs/test/r00/search')
        response = run.objects.put(canonical_json(outcome.as_dict()))
        source = run.objects.put(b'module top; endmodule\n')
        run.journal('agent').append((), 'model_settled',
            {'record': response.as_dict()}, (response,))
        run.journal('candidates').append((), 'captured',
            {'source': source.as_dict()}, (source,))
        os._exit(75)  # Deliberately bypass all finalizers and trace exporters.

    monkeypatch.setattr(launching, 'run', interrupted)
    child = multiprocessing.get_context('fork').Process(target=campaign.run_staged, args=(root,))
    child.start()
    child.join(20)
    if child.is_alive():
        child.kill()
        child.join(5)
        raise AssertionError('hard-exit test worker did not finish')
    assert child.exitcode == 75
    assert json.loads((root / 'runtime.json').read_text())['state'] == 'running'
    scratch.rename(tmp_path / 'retained-but-unavailable-scratch')
    run = JournalDirectory(root / 'execution/runs/test/r00/search')
    assert len(run.journal('agent').load()) == 1
    assert len(run.journal('candidates').load()) == 1
    cold = account(root / 'account', '2')
    assert cold.summary()['spent'] == '0.2'
    assert cold.summary()['pending'] == 0

    def must_not_repeat(_):
        raise AssertionError('paid request was repeated after worker loss')

    assert cold.dispatch(req, CEILING, must_not_repeat) == charged('0.2')
