import json
import socket

import pytest

from test_ppa_campaign import plan
from tools.ppa_campaign import publish, slots
from tools.ppa_recovery_watch import archive_if_ready
from tools.ppa_recovery_watch import reference_released, selected_methods


def setup(tmp_path, *, close=True, cleanup='complete', host=None):
    source, local = tmp_path / 'archive', tmp_path / 'local'
    config = plan()
    config['replicates'] = 1
    publish(source / 'campaigns/task/plan.json', config)
    publish(source / 'runtime.json', {'host': host or socket.gethostname(), 'local_directory': str(local)})
    selected = config['methods'][0]['name']
    for method, replica, origin in slots(config, local / 'campaigns/task'):
        if method['name'] != selected:
            continue
        publish(origin / 'search/evidence.json', {'unchanged': True})
        if close:
            publish(origin / 'search-service/service.json', {'ok': True, 'cleanup': cleanup})
    return source, local, selected


def test_live_attempt_is_not_archived_or_restarted(tmp_path):
    source, _, selected = setup(tmp_path, close=False)
    assert archive_if_ready(source, selected)['state'] == 'waiting-original'
    assert not (source / 'campaigns/task/runs').exists()


def test_closed_selected_attempt_copies_without_unrelated_live_attempt(tmp_path):
    source, local, selected = setup(tmp_path)
    result = archive_if_ready(source, selected)
    assert result == {'state': 'ready', 'attempts': 1, 'original_successes': 1}
    origin = local / 'campaigns/task/runs' / selected / 'r00/search/evidence.json'
    target = source / 'campaigns/task/runs' / selected / 'r00/search/evidence.json'
    assert target.read_bytes() == origin.read_bytes()
    assert archive_if_ready(source, selected) == result


def test_unknown_cleanup_blocks_replacement(tmp_path):
    source, _, selected = setup(tmp_path, cleanup='unknown')
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        archive_if_ready(source, selected)
    assert not (source / 'campaigns/task/runs').exists()


def test_wrong_node_cannot_read_some_other_tmp(tmp_path):
    source, _, selected = setup(tmp_path, host='not-this-node')
    with pytest.raises(ValueError, match='owning node'):
        archive_if_ready(source, selected)


def test_shared_closed_attempt_can_be_read_without_original_node(tmp_path):
    from tools.eval_review_trial import write_json

    source, local, selected = setup(tmp_path, host='gone-node')
    local.rename(source / 'execution')
    runtime = json.loads((source / 'runtime.json').read_text())
    runtime['storage'] = 'shared'
    write_json(source / 'runtime.json', runtime)
    assert archive_if_ready(source, selected) == {
        'state': 'ready', 'attempts': 1, 'original_successes': 1}


def test_profiles_are_routed_without_replacing_the_other_provider(tmp_path):
    methods = [{'name': 'qwen-provided', 'behavior_source': 'provided'},
               {'name': 'deepseek-agent', 'behavior_source': 'agent'},
               {'name': 'terra-agent', 'behavior_source': 'agent'}]
    result = selected_methods(methods, ['qwen-', 'deepseek-'],
                              [tmp_path / 'qwen.json', tmp_path / 'deepseek.json'])
    assert [row['name'] for row in result] == ['qwen-provided', 'deepseek-agent']
    assert result[0]['provider'] == str(tmp_path / 'qwen.json')
    assert result[1]['provider'] == str(tmp_path / 'deepseek.json')
    with pytest.raises(ValueError, match='overlap'):
        selected_methods(methods, ['qwen-', 'qwen'], [tmp_path, tmp_path])


@pytest.mark.parametrize('state,ready', [('supervising', False), ('starting', False),
                                        ('completed', True), ('failed', True)])
def test_reference_wait_is_not_a_task_deadline(state, ready):
    assert reference_released({'state': state}) is ready


def test_reference_timeout_requires_confirmed_cleanup():
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        reference_released({'state': 'timed-out', 'cleanup_state': 'unconfirmed'})
    assert reference_released({'state': 'timed-out', 'cleanup_state': 'complete'})
