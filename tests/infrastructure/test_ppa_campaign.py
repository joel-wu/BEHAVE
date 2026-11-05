"""The batch controller must not replace PPA semantics or hide missing runs."""

import importlib.util
import json
from pathlib import Path

import pytest

from behave.ppa import campaign as execution
from behave.ppa import launch as launching

SPEC = importlib.util.spec_from_file_location(
    "ppa_campaign", Path(__file__).resolve().parents[2] / "tools/ppa_campaign.py")
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)


def plan():
    return {"schema": "behave.ppa-campaign/2", "task": "mx",
        **{k: "/inputs/" + k for k in ("objects", "release", "experiment", "liberty", "sta", "retained_pool")},
        "methods": [{"name": name, "provider": "/inputs/" + name + ".json", "prompt": "/inputs/prompt.md", "behavior_source": "provided"}
                    for name in ("claude", "gpt")],
        "replicates": 24, "parallelism": 48, "turns": 40,
        "request_seconds": None, "replicate_seconds": 7200, "final_seconds": 7200}


def test_interleaved_distinct_replicates():
    config = campaign.validate_plan(plan())
    slots = campaign.slots(config, Path("/out"))
    assert len(slots) == len({s[2] for s in slots}) == 48
    assert [s[0]["name"] for s in slots[:4]] == ["claude", "gpt", "claude", "gpt"]


def test_final_only_mode_cannot_fall_back_to_a_new_model_attempt(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="no model dispatched"):
        campaign.selected_search(None, "gpt/r00", tmp_path, final_only=True)
    monkeypatch.setattr("behave.ppa.campaign_recovery.recover_search", lambda *args: None)
    with pytest.raises(RuntimeError, match="no model dispatched"):
        campaign.selected_search({"directory": "/old"}, "gpt/r00", tmp_path, final_only=True)
    assert campaign.selected_search(None, "gpt/r00", tmp_path) is None
    prior = {"ok": True, "search_directory": "/old/search"}
    monkeypatch.setattr("behave.ppa.campaign_recovery.recover_search", lambda *args: prior)
    assert campaign.selected_search({}, "gpt/r00", tmp_path, final_only=True) is prior


def test_remeasurement_is_an_explicit_final_only_group_option():
    group = {"schema": "behave.ppa-campaign-group/1", "plans": {"mx": "/mx.json"}, "cpu_workers": 32}
    assert campaign.validate_group(group) == group
    selected = {**group, "final_only": True, "remeasure_final": True}
    assert campaign.validate_group(selected) == selected
    with pytest.raises(ValueError, match="final-only"):
        campaign.validate_group({**group, "remeasure_final": True})
    with pytest.raises(ValueError, match="boolean"):
        campaign.validate_group({**group, "final_only": "yes"})
    with pytest.raises(ValueError, match="final-only"):
        campaign.run(Path("/not-dispatched"), remeasure_final=True)
    assert campaign.validate_group({**group, "final_only": True, "remeasure_tasks": ["mx"]})["remeasure_tasks"] == ["mx"]
    with pytest.raises(ValueError, match="distinct tasks"):
        campaign.validate_group({**group, "final_only": True, "remeasure_tasks": ["absent"]})
    with pytest.raises(ValueError, match="final-only"):
        campaign.validate_group({**group, "remeasure_tasks": ["mx"]})
    assert campaign.validate_group({**group, "final_only": True, "refresh_native": True})["refresh_native"]
    with pytest.raises(ValueError, match="final-only"):
        campaign.validate_group({**group, "refresh_native": True})
    with pytest.raises(ValueError, match="boolean"):
        campaign.validate_group({**group, "final_only": True, "refresh_native": "yes"})
    with pytest.raises(ValueError, match="final-only"):
        campaign.run(Path("/not-dispatched"), refresh_native=True)


def test_concurrency_is_operator_selected_and_behavior_source_is_required():
    config = campaign.validate_plan({**plan(), "parallelism": 100, "replicates": 100})
    assert config["parallelism"] == config["replicates"] == 100
    config["methods"][0].pop("behavior_source")
    with pytest.raises(ValueError, match="method configuration"):
        campaign.validate_plan(config)


def test_recovery_cannot_change_behavior_source(tmp_path):
    config = plan()
    campaign.publish(tmp_path / "plan.json", config)
    config["methods"][0]["behavior_source"] = "agent"
    with pytest.raises(ValueError, match="Behavior sources"):
        campaign.recovery_binding(config, tmp_path)


def test_no_sim_condition_is_explicit_and_cannot_change_on_recovery(tmp_path):
    config = plan()
    config['methods'][0]['sim_feedback'] = False
    assert campaign.validate_plan(config)['methods'][0]['sim_feedback'] is False
    campaign.publish(tmp_path / 'plan.json', config)
    config['methods'][0]['sim_feedback'] = True
    with pytest.raises(ValueError, match='Sim feedback'):
        campaign.recovery_binding(config, tmp_path)
    config['methods'][0]['sim_feedback'] = 'false'
    with pytest.raises(ValueError, match='boolean'):
        campaign.validate_plan(config)


@pytest.mark.parametrize('field', ['judge_policy', 'final_judge_policy', 'final_experiment'])
def test_recovery_binds_judge_policy_bytes_not_its_copy_path(tmp_path, field):
    config = plan()
    original = tmp_path / "original.json"
    copied = tmp_path / "copy.json"
    original.write_text('{"policy": "baseline"}')
    copied.write_bytes(original.read_bytes())
    # All other public files may share this fixture's bytes.
    for key in ("release", "experiment", "retained_pool"):
        config[key] = str(original)
    for method in config["methods"]:
        method.update(provider=str(original), prompt=str(original))
    config[field] = str(original)
    campaign.publish(tmp_path / "plan.json", config)
    campaign.publish(tmp_path / "inputs.json", {})
    config[field] = str(copied)
    assert campaign.recovery_binding(config, tmp_path)["directory"] == str(tmp_path)
    copied.write_text('{"policy": "search"}')
    with pytest.raises(ValueError, match="preserve publication"):
        campaign.recovery_binding(config, tmp_path)


@pytest.mark.parametrize("change", [
    {"parallelism": True}, {"replicates": 0}, {"cpu_workers": 0},
    {"objects": "relative"}, {"replicate_seconds": 0},
    {"methods": [{"name": "../escape", "provider": "/p", "prompt": "/q"}]},
])
def test_bad_plan_refused(change):
    with pytest.raises(ValueError):
        campaign.validate_plan({**plan(), **change})


def test_records_are_immutable(tmp_path):
    path = tmp_path / "record.json"
    campaign.publish(path, {"a": 1})
    campaign.publish(path, {"a": 1})
    with pytest.raises(ValueError, match="refusing to replace"):
        campaign.publish(path, {"a": 2})


def test_optional_cumulative_deadlines_do_not_create_a_batch_deadline():
    config = campaign.validate_plan({**plan(), "replicate_seconds": None, "final_seconds": None})
    assert campaign.campaign_wall(config, 3) is None
    assert campaign.campaign_wall({**config, "replicate_seconds": 20}, 3) is None
    assert campaign.campaign_wall(plan(), 3) == 43200


@pytest.mark.parametrize('status', [None, 'timeout', 'success'])
def test_explicit_fenced_stop_restarts_only_incomplete_searches(tmp_path, status):
    from behave.core.artifacts import BlobRef

    campaign.publish(tmp_path / 'plan.json', {})
    campaign.publish(tmp_path / 'interruption.json', {'schema': 'behave.ppa-campaign-stop/1',
        'step': '123.4', 'cleanup': 'complete'})
    campaign.publish(tmp_path / 'runs/gpt/r00/search-service/requested.json', {})
    if status is not None:
        campaign.publish(tmp_path / 'runs/gpt/r00/search-service/service.json', {
            'ok': status == 'success', 'status': 'exited' if status == 'success' else status,
            'cleanup': 'complete'})
    binding = {'directory': str(tmp_path), 'wait_seconds': 0,
        'plan': BlobRef.of((tmp_path / 'plan.json').read_bytes()).as_dict(),
        'interruption': BlobRef.of((tmp_path / 'interruption.json').read_bytes()).as_dict()}
    result = campaign.recover_search(binding, 'gpt/r00', tmp_path / 'replacement')
    assert (result is None) == (status != 'success')
    if result is not None:
        assert result['recovery'] == 'reuse'


def test_recovery_allows_removing_only_cumulative_deadlines(tmp_path):
    config = plan()
    source = tmp_path / 'public.json'
    source.write_text('{}')
    for key in ('release', 'experiment', 'retained_pool'):
        config[key] = str(source)
    for method in config['methods']:
        method.update(provider=str(source), prompt=str(source))
    campaign.publish(tmp_path / 'plan.json', config)
    campaign.publish(tmp_path / 'inputs.json', {})
    changed = {**config, 'replicate_seconds': None, 'final_seconds': None}
    assert campaign.recovery_binding(changed, tmp_path)['directory'] == str(tmp_path)
    with pytest.raises(ValueError, match='original campaign settings'):
        campaign.recovery_binding({**changed, 'turns': 200}, tmp_path)


@pytest.mark.parametrize('location', ['/tmp/account', '/var/tmp/account', '/dev/shm/account'])
def test_paid_campaign_refuses_disposable_account(tmp_path, location):
    config = plan()
    provider = tmp_path / 'provider.json'
    provider.write_text(json.dumps({'spending': {'directory': location}}))
    for method in config['methods']:
        method['provider'] = str(provider)
    with pytest.raises(ValueError, match='durable shared storage'):
        campaign.validate_durable_campaign(Path('/shared/run'), (config,))


def test_paid_campaign_keeps_account_identity_and_requires_durable_run_root(tmp_path):
    config = plan()
    provider = tmp_path / 'provider.json'
    provider.write_text(json.dumps({'spending': {'directory': '/shared/account', 'account': 'original'}}))
    for method in config['methods']:
        method['provider'] = str(provider)
    before = provider.read_bytes()
    campaign.validate_durable_campaign(Path('/shared/run'), (config,))
    assert provider.read_bytes() == before
    with pytest.raises(ValueError, match='durable shared storage'):
        campaign.validate_durable_campaign(Path('/tmp/run'), (config,))


def test_paid_campaign_refuses_account_symlink_into_scratch(tmp_path):
    config = plan()
    account = tmp_path / 'apparently-shared-account'
    account.symlink_to('/tmp/disposable-account')
    provider = tmp_path / 'provider.json'
    provider.write_text(json.dumps({'spending': {'directory': str(account)}}))
    for method in config['methods']:
        method['provider'] = str(provider)
    with pytest.raises(ValueError, match='durable shared storage'):
        campaign.validate_durable_campaign(Path('/shared/run'), (config,))


def test_ambiguous_dispatch_does_not_repeat(tmp_path):
    campaign.publish(tmp_path / "requested.json", {})
    with pytest.raises(RuntimeError, match="unsettled"):
        campaign.service(Path("/snapshot"), tmp_path, [], 100)


def test_settled_service_does_not_dispatch(tmp_path):
    campaign.publish(tmp_path / "service.json", {"ok": False, "status": "timeout"})
    assert campaign.service(Path("/snapshot"), tmp_path, [], 100)["status"] == "timeout"


def test_final_service_provisions_ingresses_from_shared_capacity_and_candidate_count(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import behave.isolation.runtime_environment as environment
    import behave.isolation.service as service
    from behave.agent.cpu_resources import CpuResources
    from behave.core.lifecycle import CleanupState
    from behave.ppa import records

    pool = CpuResources(2)
    seen = []
    monkeypatch.setattr(environment, 'runtime_environment', lambda: SimpleNamespace(directory=tmp_path))
    monkeypatch.setattr(records, 'finalized_report', lambda path: (None, None, dict(
        candidates=[dict(candidate=str(i)) for i in range(3)])))

    def run(command, **kwargs):
        endpoints = [command[i + 1] for i, arg in enumerate(command) if arg == '--cpu-endpoint']
        assert len(endpoints) == len(set(endpoints)) == pool.snapshot()['slots']
        assert all(Path(path).is_socket() for path in endpoints)
        assert pool.snapshot()['active'] == 0  # Ingresses do not reserve CPUs.
        seen.extend(endpoints)
        return SimpleNamespace(status='exited', returncode=0, cleanup_state=CleanupState.COMPLETE,
                               stdout=b'', stderr=b'', elapsed_seconds=.1)

    monkeypatch.setattr(service, 'run_service', run)
    result = campaign.service(Path('/snapshot'), tmp_path / 'service',
        ['compare', '--search', 'one/r00', '/search'], None, cpu_resources=pool)
    assert result['ok']
    assert all(not Path(path).exists() for path in seen)
    assert pool.snapshot()['active'] == pool.snapshot()['retired'] == 0


def test_plot_empty_results_does_not_invent_a_curve(tmp_path):
    pytest.importorskip("matplotlib")
    config = plan()
    experiment = tmp_path / "experiment.json"
    experiment.write_text(json.dumps({"timing": {"period_ns": 40}}))
    config["experiment"] = str(experiment)
    campaign.plot({"plan": config, "points": [], "frontiers": {"claude": [], "gpt": []},
                   "outcomes": [{"label": "claude/r00", "ok": False}]}, tmp_path / "frontier")
    assert (tmp_path / "frontier.png").stat().st_size > 0
    assert (tmp_path / "frontier.pdf").stat().st_size > 0


def test_plot_uses_admitted_measurements_and_recorded_frontier(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    config = plan()
    experiment = tmp_path / "experiment.json"
    experiment.write_text(json.dumps({"timing": {"period_ns": 40}}))
    config["experiment"] = str(experiment)
    figure, axes = plt.subplots()
    monkeypatch.setattr(plt, "subplots", lambda **kwargs: (figure, axes))
    points = [{"method": "claude", "replica": "r00", "candidate": key,
               "admitted": admitted, "area_um2": area, "performance": {"time_ns": time}}
              for key, admitted, area, time in [("front", True, 12000, 84000),
                  ("dominated", True, 20000, 84000), ("failed", False, 100, 100)]]
    campaign.plot({"plan": config, "points": points,
                   "frontiers": {"claude": ["r00:front"], "gpt": []},
                   "outcomes": [{"label": "claude/r00", "ok": True}]}, tmp_path / "frontier",
                  labels={"claude": "Sonnet"})
    assert axes.collections[0].get_offsets().tolist() == [[12., 84.], [20., 84.]]
    assert list(axes.lines[0].get_xdata()) == [12.]
    assert list(axes.lines[0].get_ydata()) == [84.]
    assert axes.get_yscale() == "log"
    assert axes.lines[0].get_label() == "Sonnet (1/24 final runs)"


@pytest.mark.parametrize('independent_final', [False, True])
@pytest.mark.parametrize('snapshot_kind', ['checkout', 'explicit', 'assets'])
def test_campaign_runs_existing_cli_and_common_final_without_model_retries(
        tmp_path, monkeypatch, independent_final, snapshot_kind):
    from types import SimpleNamespace

    from test_ppa import FakeNative, Scripted
    from test_ppa_cli import published

    import behave.agent.process_model as process
    import behave.ppa.__main__ as cli
    import behave.task_execution.shell as shell
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.agent.tools import file_tools

    common = published(tmp_path)
    config = plan()
    config.update(dict(zip((a[2:] for a in common[::2]), common[1::2])))
    config.update(replicates=2, parallelism=1)
    config.update(liberty=str(tmp_path / "unused.lib"), sta=str(tmp_path / "unused-sta"))
    if independent_final:
        from dataclasses import replace

        from test_ppa import fixture_task

        from behave.task_execution.judge_policy import JudgePolicy
        _, task, _ = fixture_task()
        config['final_judge_policy'] = str(tmp_path / 'final-policy.json')
        policy = replace(JudgePolicy.from_bytes(task.judge_policy), baseline_cases=5,
                         sequence_length=7, search_seconds=0, analysis=None)
        Path(config['final_judge_policy']).write_bytes(policy.to_bytes())
        config['final_experiment'] = str(tmp_path / 'final-experiment.json')
        final_experiment = json.loads(Path(config['experiment']).read_text())
        final_experiment['service_seconds'] = 18000
        Path(config['final_experiment']).write_text(json.dumps(final_experiment))
    retained = tmp_path / "retained.json"
    retained.write_text('{"stimuli": []}')
    config["retained_pool"] = str(retained)
    for method in config["methods"]:
        method.update(provider=str(tmp_path / "provider.json"), prompt=str(tmp_path / "prompt.txt"))
    Path(config["methods"][0]["prompt"]).write_text(
        "target=${primary_target_index}, time=${primary_target_ns}; HDL $signed must stay literal")
    campaign.publish(tmp_path / "plan.json", config)
    campaign.publish(tmp_path / "inputs.json", {})
    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kwargs: FakeNative())
    monkeypatch.setattr(process, "ProcessModelBackend", lambda command: Scripted())
    monkeypatch.setattr(shell, "development_shell", lambda *args, **kwargs:
                        SimpleNamespace(tool=lambda: file_tools()[1]))
    monkeypatch.setattr(LocalChatEndpoint, "from_dict", lambda row: SimpleNamespace(
        model_policy=lambda: {"model": "fake"}, command=lambda: "no-process"))
    calls = []
    monkeypatch.delenv('BEHAVE_REPO_ROOT', raising=False)
    expected_snapshot = Path(__file__).resolve().parents[2]
    options = {}
    if snapshot_kind == 'assets':
        expected_snapshot = tmp_path / 'explicit-assets'
        monkeypatch.setenv('BEHAVE_REPO_ROOT', str(expected_snapshot))
    elif snapshot_kind == 'explicit':
        expected_snapshot = tmp_path / 'frozen-source'
        options['snapshot'] = expected_snapshot
        # Explicit source snapshots take precedence over checkout-layout assets.
        monkeypatch.setenv('BEHAVE_REPO_ROOT', str(tmp_path / 'not-used'))

    def direct(snapshot, directory, arguments, seconds, *, cpu_resources):
        assert snapshot == expected_snapshot
        calls.append(arguments[0])
        if independent_final:
            if arguments[0] == 'compare':
                assert arguments[arguments.index('--judge-policy') + 1] == config['final_judge_policy']
                assert arguments[arguments.index('--experiment') + 1] == config['final_experiment']
            else:
                assert '--judge-policy' not in arguments
                assert arguments[arguments.index('--experiment') + 1] == config['experiment']
        campaign.publish(directory / "requested.json", {"arguments": arguments, "seconds": seconds})
        cli.main(arguments)
        receipt = {"ok": True, "status": "exited", "cleanup": "complete", "returncode": 0}
        campaign.publish(directory / "service.json", receipt)
        return receipt

    monkeypatch.setattr(execution, "service", direct)
    monkeypatch.setattr(execution, "plot", lambda *args: None)
    assert campaign.run(tmp_path, **options) == 0
    assert calls == ["search"] * 4 + ["compare"] * 4
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["outcomes"]) == 4 and len(report["points"]) == 4
    assert all(p["admitted"] for p in report["points"])
    assert set(report["frontiers"]) == {"claude", "gpt"}
    # Every final comparison receives the identical nonempty union, not its own
    # search's provenance-dependent suite. The production reader verifies it.
    pool = json.loads((tmp_path / "common-pool.json").read_text())["stimuli"]
    assert pool
    if independent_final:
        from behave.ppa.finalize import final_baseline
        expected = final_baseline(replace(task, judge_policy=policy.to_bytes()))
        assert all(stimulus.as_dict() in pool for stimulus in expected.stimuli)
    for name in ("claude", "gpt"):
        for replica in range(2):
            prompt = (tmp_path / "runs" / name / f"r{replica:02d}" / "prompt.md").read_text()
            assert f"target={replica % 2}" in prompt and "$signed" in prompt

    # A new campaign can retain all successful searches without paying for any
    # model call. It still revalidates every candidate on one common final pool.
    recovered = tmp_path / "recovered"
    campaign.publish(recovered / "plan.json", config)
    campaign.publish(recovered / "inputs.json", {})
    campaign.publish(recovered / "recovery.json", campaign.recovery_binding(config, tmp_path))
    calls.clear()
    assert campaign.run(recovered, **options) == 0
    assert calls == ["compare"] * 4
    again = json.loads((recovered / "report.json").read_text())
    assert again["points"] == report["points"]
    assert again["common_pool"] == report["common_pool"]
    assert all(row["recovery"] == "reuse" for row in again["recovery"]["searches"])


@pytest.mark.parametrize("failure,measured,action", [
    ("model-worker-failed", False, "new-attempt"),
    ("budget-account-busy", False, "new-attempt"),
    ("model-worker-failed", True, "keep-failure"),
    ("model-budget-exhausted", False, "keep-failure"),
    ("model-worker-timeout", False, "keep-failure"),
    ("openrouter-http-429", False, "new-attempt"),
    ("openrouter-http-429", True, "new-attempt"),
    ("deepseek-exchange-interrupted", False, "new-attempt"),
    ("deepseek-exchange-interrupted", True, "new-attempt"),
])
def test_recovery_is_new_attempt_only_before_measurement(tmp_path, failure, measured, action):
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json

    previous = tmp_path / "previous"
    directory = previous / "runs/claude/r00"
    campaign.publish(previous / "plan.json", plan())
    campaign.publish(directory / "search-service/requested.json", {})
    campaign.publish(directory / "search-service/service.json",
        {"ok": False, "status": "exited", "cleanup": "complete", "returncode": 1})
    run = JournalDirectory(directory / "search")
    ref = run.objects.put(canonical_json({"kind": 'retryable' if failure == 'openrouter-http-429' else 'unknown',
                                        "code": failure}))
    run.journal("agent").append((), "model_settled",
        {"outcome": "failure", "call_id": "old-paid-call", "record": ref.as_dict()}, (ref,))
    if measured:
        run.journal("measurements").append((), "requested", {"candidate": "keep-this-candidate"})
    binding = {"directory": str(previous), "plan": BlobRef.of((previous / "plan.json").read_bytes()).as_dict(),
               "wait_seconds": 0}
    result = campaign.recover_search(binding, "claude/r00", tmp_path / "new")
    evidence = json.loads((tmp_path / "new/recovery.json").read_text())
    assert evidence["action"] == action
    assert (result is None) == (action == "new-attempt")
    if result is not None:
        assert result["ok"] is False
    # Nothing deletes or resumes the old attempt, including uncertain charge data.
    assert len(run.journal("agent").load()) == 1
    assert len(run.journal("measurements").load()) == int(measured)


@pytest.mark.parametrize("cleanup", [None, "incomplete"])
def test_recovery_requires_confirmed_old_process_cleanup(tmp_path, cleanup):
    from behave.core.artifacts import BlobRef

    campaign.publish(tmp_path / "plan.json", {})
    campaign.publish(tmp_path / "runs/gpt/r00/search-service/requested.json", {})
    if cleanup is not None:
        campaign.publish(tmp_path / "runs/gpt/r00/search-service/service.json",
                         {"ok": False, "cleanup": cleanup})
    binding = {"directory": str(tmp_path), "plan": BlobRef.of((tmp_path / "plan.json").read_bytes()).as_dict(),
               "wait_seconds": 0}
    with pytest.raises(RuntimeError, match="unsettled|cleanup unconfirmed"):
        campaign.recover_search(binding, "gpt/r00", tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_recovery_preserves_search_limits(tmp_path):
    config = plan()
    campaign.publish(tmp_path / "plan.json", config)
    with pytest.raises(ValueError, match="original campaign settings"):
        campaign.recovery_binding({**config, "turns": 99}, tmp_path)


def test_recovery_chain_reuses_original_without_restarting_paid_search(tmp_path):
    from behave.core.artifacts import BlobRef

    config = plan()
    source = tmp_path / 'public.json'
    source.write_text('{}')
    for key in ('release', 'experiment', 'retained_pool'):
        config[key] = str(source)
    for method in config['methods']:
        method.update(provider=str(source), prompt=str(source))
    original, prior = tmp_path / 'original', tmp_path / 'prior'
    for root in (original, prior):
        campaign.publish(root / 'plan.json', config)
        campaign.publish(root / 'inputs.json', {})
        campaign.publish(root / 'interruption.json', {'schema': 'behave.ppa-campaign-stop/1',
            'step': '123.4', 'cleanup': 'complete'})
    campaign.publish(original / 'runs/gpt/r00/search-service/requested.json', {})
    campaign.publish(original / 'runs/gpt/r00/search-service/service.json', {
        'ok': True, 'status': 'exited', 'cleanup': 'complete'})
    parent = campaign.recovery_binding(config, original)
    campaign.publish(prior / 'recovery.json', parent)
    binding = campaign.recovery_binding(config, prior)
    assert binding['ancestor'] == BlobRef.of((prior / 'recovery.json').read_bytes()).as_dict()
    result = campaign.recover_search(binding, 'gpt/r00', tmp_path / 'next')
    assert result['search_directory'] == str(original / 'runs/gpt/r00/search')
    assert result['recovery'] == 'reuse'
    campaign.publish(prior / 'runs/gpt/r00/search-service/requested.json', {})
    assert campaign.recover_search(binding, 'gpt/r00', tmp_path / 'interrupted') is None
    campaign.publish(prior / 'runs/gpt/r00/search-service/service.json', {
        'ok': False, 'status': 'exited', 'returncode': 2, 'cleanup': 'complete'})
    failed = campaign.recover_search(binding, 'gpt/r00', tmp_path / 'failed')
    assert failed['recovery'] == 'keep-failure'
    assert failed['search_directory'] == str(prior / 'runs/gpt/r00/search')
    tampered = {**binding, 'ancestor': BlobRef.of(b'changed').as_dict()}
    with pytest.raises(ValueError, match='ancestor changed'):
        campaign.recover_search(tampered, 'gpt/r00-new', tmp_path / 'tampered')


def test_recovery_rejects_cycles(tmp_path):
    campaign.publish(tmp_path / 'plan.json', plan())
    campaign.publish(tmp_path / 'recovery.json', {'directory': str(tmp_path)})
    with pytest.raises(ValueError, match='cyclic'):
        campaign.recovery_binding(plan(), tmp_path)


def test_final_recovery_uses_latest_settled_archive_not_dangling_request(tmp_path):
    from behave.agent.replay import Tape
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef

    pool = ({'input': 7},)
    original, prior = tmp_path / 'original', tmp_path / 'prior'
    ancestor = {'directory': str(original), 'interruption': {}}
    campaign.publish(prior / 'recovery.json', ancestor)
    binding = {'directory': str(prior), 'interruption': {},
        'ancestor': BlobRef.of((prior / 'recovery.json').read_bytes()).as_dict()}
    for root in (original, prior):
        campaign.publish(root / 'common-pool.json', {'stimuli': list(pool)})
        final = root / 'runs/gpt/r00/final'
        Tape(JournalDirectory(final).journal('workflow')).record('started', {})
    old_final = original / 'runs/gpt/r00/final'
    new_final = prior / 'runs/gpt/r00/final'
    Tape(JournalDirectory(old_final).journal('measurements')).record('settled', {})
    assert campaign.recover_final(binding, 'gpt/r00', pool) == old_final
    Tape(JournalDirectory(new_final).journal('measurements')).record('settled', {})
    assert campaign.recover_final(binding, 'gpt/r00', pool) == new_final
    assert campaign.recover_final(binding, 'gpt/r00', ({'input': 8},)) is None


@pytest.mark.parametrize('current_native', ['current-build', 'replacement-build'])
def test_native_refresh_reuses_matching_final_without_resurrecting_ancestors(tmp_path, current_native):
    from behave.agent.replay import Tape
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.ppa.records import write_document

    pool = ({'input': 7},)
    older, latest = tmp_path / 'older', tmp_path / 'latest'
    campaign.publish(latest / 'recovery.json', {'directory': str(older), 'interruption': {}})
    binding = {'directory': str(latest), 'interruption': {},
        'ancestor': BlobRef.of((latest / 'recovery.json').read_bytes()).as_dict()}
    for root, identity in ((older, current_native), (latest, 'current-build')):
        campaign.publish(root / 'common-pool.json', {'stimuli': list(pool)})
        run = JournalDirectory(root / 'runs/gpt/r00/final')
        manifest, closure = write_document(run.objects, {'native': identity})
        Tape(run.journal('workflow')).record('started', {'manifest': manifest.as_dict()}, closure)
        Tape(run.journal('measurements')).record('settled', {})
    selected = latest / 'runs/gpt/r00/final'
    assert campaign.recover_final(binding, 'gpt/r00', pool) == selected
    assert campaign.recover_final(binding, 'gpt/r00', pool, native_identity=current_native) == (
        selected if current_native == 'current-build' else None)


def test_recovery_selects_provider_but_only_changes_transport_rate(tmp_path):
    from dataclasses import replace

    from test_hosted_providers import endpoint

    from behave.agent.providers.rate_limit import RequestRate

    config = plan()
    same = tmp_path / 'same.json'
    same.write_text('{}')
    for key in ('release', 'experiment', 'retained_pool'):
        config[key] = str(same)
    old_profile = tmp_path / 'old-profile.json'
    ep = endpoint('openrouter')
    old_profile.write_text(json.dumps(ep.as_dict()))
    for method in config['methods']:
        method.update(provider=str(old_profile), prompt=str(same))
    campaign.publish(tmp_path / 'plan.json', config)
    campaign.publish(tmp_path / 'inputs.json', {})
    new_profile = tmp_path / 'new-profile.json'
    paced = replace(ep, rate_limit=RequestRate(str(tmp_path / 'gate'), 20))
    new_profile.write_text(json.dumps(paced.as_dict()))
    selected = {**config, 'parallelism': 1, 'methods': [dict(config['methods'][0], provider=str(new_profile))]}
    assert campaign.recovery_binding(selected, tmp_path)['directory'] == str(tmp_path)
    new_profile.write_text(json.dumps(replace(paced, parameters={'temperature': 1.1}).as_dict()))
    with pytest.raises(ValueError, match='model parameters'):
        campaign.recovery_binding(selected, tmp_path)


@pytest.mark.parametrize("score", [0, 1, None])
def test_preflight_gate_requires_concrete_and_workload_success(tmp_path, score):
    private = tmp_path / "private"
    campaign.publish(tmp_path / "preflight-gate.json", {"directory": str(private)})
    report = {"excluded_from_comparison": True, "native": "native",
        "candidate": {"history": [{"mode": m, "result": {"score": score}}
                                  for m in ("check", "final")],
            "performance": {"score": score, "time_ns": 80, "source": "rtl"},
            "eda": {"status": "measured", "area_um2": 10}}}
    campaign.publish(private / "results/preflight.json", report)
    if score == 1:
        campaign.await_preflight(tmp_path)
        assert (tmp_path / "preflight-passed.json").exists()
    else:
        with pytest.raises(RuntimeError, match="no model dispatch"):
            campaign.await_preflight(tmp_path)
        assert not (tmp_path / "preflight-passed.json").exists()


@pytest.mark.parametrize("state", ["failed", "timed-out", "launch-error"])
def test_failed_preflight_does_not_dispatch(tmp_path, state):
    private = tmp_path / "private"
    campaign.publish(tmp_path / "preflight-gate.json", {"directory": str(private)})
    campaign.publish(private / "supervision/status.json", {"state": state})
    with pytest.raises(RuntimeError, match="no model dispatched"):
        campaign.await_preflight(tmp_path)


def test_freeze_inputs_copies_one_publication_without_editing_it(tmp_path):
    from test_ppa_cli import published

    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.data.published import load_published_task

    original = tmp_path / "original"
    original.mkdir()
    common = published(original)
    config = plan()
    config.update(dict(zip((a[2:] for a in common[::2]), common[1::2])))
    config["retained_pool"] = str(original / "retained.json")
    Path(config["retained_pool"]).write_text('{"stimuli": []}')
    config['final_experiment'] = str(original / 'final-experiment.json')
    Path(config['final_experiment']).write_bytes(Path(config['experiment']).read_bytes())
    for method in config["methods"]:
        method.update(provider=str(original / "provider.json"), prompt=str(original / "prompt.txt"))
    frozen = campaign.freeze_inputs(config, tmp_path / "frozen")
    assert frozen['final_experiment'] != config['final_experiment']
    assert Path(frozen['final_experiment']).read_bytes() == Path(config['final_experiment']).read_bytes()
    Path(config["methods"][0]["prompt"]).write_text("later unrelated edit")
    assert Path(frozen["methods"][0]["prompt"]).read_text() == "fixture-only draft"
    loaded = load_published_task(DirectoryReader(frozen["objects"]),
        BlobRef.of(Path(frozen["release"]).read_bytes()), frozen["task"])
    assert loaded.judge.reference_behavior.files


def test_detached_submit_refuses_ephemeral_parent_step_before_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_STEP_ID", "7")
    with pytest.raises(ValueError, match="login host"):
        campaign.submit(tmp_path / "not-read.json", tmp_path / "not-created", 1, 8)
    assert not (tmp_path / "not-created").exists()


def test_group_submit_refuses_ephemeral_parent_before_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_STEP_ID", "7")
    with pytest.raises(ValueError, match="login host"):
        campaign.submit_group(tmp_path / "missing.json", tmp_path / "not-created", 1, 8)
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("final_policy", [False, True, "refresh"])
def test_group_reuses_campaigns_with_one_disjoint_cpu_pool(tmp_path, monkeypatch, fails, final_policy):
    from threading import Barrier

    from behave.agent import cpu_resources as scheduling
    from behave.core.lifecycle import CleanupState

    monkeypatch.setattr(scheduling, "allocated_cpus", lambda cpus=None: (2, 3, 4, 5))
    sources = {}
    for name in ("mx", "delta"):
        source = tmp_path / (name + ".json")
        source.write_text(json.dumps(plan()))
        sources[name] = str(source)
    group = {"schema": "behave.ppa-campaign-group/1", "plans": sources, "cpu_workers": 2}
    if final_policy == "refresh":
        group.update(final_only=True, refresh_native=True)
    elif final_policy:
        group.update(final_only=True, remeasure_tasks=["mx"])
    monkeypatch.setattr(execution, "freeze_plan", lambda config, root: root.mkdir(parents=True))
    barrier = Barrier(2)
    seen, exported = {}, []

    def run(root, *, snapshot, cpu_resources, final_only=False, remeasure_final=False, refresh_native=False):
        assert snapshot == tmp_path / "snapshot"
        assert final_only == bool(final_policy)
        assert remeasure_final == (final_policy is True and root.name == "mx")
        assert refresh_native == (final_policy == "refresh")
        lease = cpu_resources.acquire(lambda: False)
        seen[root.name] = (cpu_resources, lease.cpus)
        try:
            barrier.wait(timeout=5)
            (root / "original-trace.json").write_text("retained")
            if fails and root.name == "mx":
                raise RuntimeError("injected task failure")
            return 0
        finally:
            cpu_resources.release(lease, CleanupState.COMPLETE)

    monkeypatch.setattr(execution, "run", run)
    monkeypatch.setattr(execution, "export_traces", lambda root: exported.append(root.name))
    root = tmp_path / "execution"
    assert campaign.run_group(root, snapshot=tmp_path / "snapshot", group=group) == int(fails)
    assert seen["mx"][0] is seen["delta"][0]
    assert set(seen["mx"][1]).isdisjoint(seen["delta"][1])
    assert sorted(exported) == ["delta", "mx"]
    rows = {row["name"]: row for row in json.loads((root / "group-results.json").read_text())}
    assert rows["delta"]["returncode"] == 0
    assert rows["mx"]["returncode"] == int(fails)
    assert json.loads((root / "cpu-resources.json").read_text())["available_cpus"] == 4
    assert (root / "campaigns/mx/original-trace.json").is_file()


def test_group_does_not_allow_conflicting_resource_settings(tmp_path, monkeypatch):
    source = tmp_path / "plan.json"
    source.write_text(json.dumps({**plan(), "cpu_workers": 8}))
    group = {"schema": "behave.ppa-campaign-group/1", "plans": {"mx": str(source)}, "cpu_workers": 2}
    with pytest.raises(ValueError, match="override shared CPU"):
        campaign.run_group(tmp_path / "execution", snapshot=tmp_path / "snapshot", group=group)
    assert not (tmp_path / "execution").exists()


@pytest.mark.parametrize("change", [{"cpu_workers": 0}, {"plans": {}},
    {"plans": {"../escape": "/plan.json"}}, {"plans": {"mx": "relative.json"}}])
def test_group_rejects_invalid_input(change):
    with pytest.raises(ValueError, match="group"):
        campaign.validate_group({"schema": "behave.ppa-campaign-group/1",
                                 "plans": {"mx": "/plan.json"}, "cpu_workers": 2, **change})


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("grouped", [False, True])
def test_staged_campaign_writes_durable_execution_before_exit(tmp_path, monkeypatch, fails, grouped):
    from behave.core import process_limits
    from behave.isolation import scratch
    from behave.runtime import snapshots as snapshots_module

    root = tmp_path / "archive"
    root.mkdir()
    local = tmp_path / "node-local"
    local.mkdir()
    config = plan()
    experiment = tmp_path / "experiment.json"
    experiment.write_text('{"timing":{"period_ns":20}}')
    for key in ("release", "experiment", "liberty", "sta", "retained_pool"):
        config[key] = str(experiment)
    for method in config["methods"]:
        method.update(provider=str(experiment), prompt=str(experiment))
    campaign.publish(root / "plan.json", config)
    if grouped:
        campaign.publish(root / 'group.json', {'schema': 'behave.ppa-campaign-group/1',
            'plans': {'mx': str(root / 'plan.json')}, 'cpu_workers': 2})
    campaign.publish(root / "inputs.json", {})
    campaign.publish(root / "launch-settings.json", {"runtime_directory": "/tmp/shared-ppa-runtime",
                                                    'cpu_offset': 2 if grouped else 0})
    affinity = [{2, 3, 4, 5}]
    monkeypatch.setattr(campaign.os, 'sched_getaffinity', lambda pid: set(affinity[0]))
    monkeypatch.setattr(campaign.os, 'sched_setaffinity', lambda pid, cpus: affinity.__setitem__(0, set(cpus)))
    monkeypatch.setattr(scratch, "mkdtemp", lambda **kwargs: str(local))
    monkeypatch.setattr(process_limits, "configure_worker_process_limit", lambda: {})
    staged = []
    def stage(source, target):
        staged.append((source, target))
        target.mkdir()
        (target / "external").symlink_to(tmp_path / "unavailable-tools")
    monkeypatch.setattr(snapshots_module, "stage", stage)
    monkeypatch.setattr("behave.ppa.campaign_inputs.freeze_inputs", lambda config, path: config)
    original_runtime = campaign.os.environ.get("BEHAVE_RUNTIME_DIR")
    def run(path, *, snapshot):
        assert path == root / "execution"
        assert snapshot == local / "snapshot"
        assert campaign.os.environ["BEHAVE_RUNTIME_DIR"] == "/tmp/shared-ppa-runtime"
        assert affinity[0] == ({4, 5} if grouped else {2, 3, 4, 5})
        (path / "kept-trace.txt").write_text("all original evidence")
        assert (root / "execution/kept-trace.txt").read_text() == "all original evidence"
        assert not (local / "execution").exists()
        if fails:
            raise RuntimeError("model transport failed")
        return 1  # Completed campaign with unsuccessful candidate(s) still archives.
    monkeypatch.setattr(launching, "run", run)
    if grouped:
        def run_group(path, *, snapshot, group):
            assert group['plans'] == {'mx': str(root / 'plan.json')}
            child = path / 'campaigns/mx'
            child.mkdir(parents=True)
            (child / 'original-trace.json').write_text('retained group evidence')
            return run(path, snapshot=snapshot)
        monkeypatch.setattr(launching, 'run_group', run_group)
    if fails:
        with pytest.raises(RuntimeError, match="transport failed"):
            campaign.run_staged(root)
    else:
        assert campaign.run_staged(root) == 1
    assert staged == [(root / "snapshot", local / "snapshot")]
    assert (root / "execution/kept-trace.txt").read_text() == "all original evidence"
    if grouped:
        assert (root / 'execution/campaigns/mx/original-trace.json').read_text() == 'retained group evidence'
    assert not (root / "execution/snapshot").exists()
    runtime = json.loads((root / "runtime.json").read_text())
    assert runtime['storage'] == 'shared'
    assert runtime['local_directory'] == str(local)
    assert runtime['execution_directory'] == str(root / 'execution')
    assert json.loads((root / "runtime.json").read_text())["state"] == ("failed-retained" if fails else "archived")
    assert campaign.os.environ.get("BEHAVE_RUNTIME_DIR") == original_runtime
    assert affinity[0] == {2, 3, 4, 5}
    with pytest.raises(RuntimeError, match="already dispatched"):
        campaign.run_staged(root)


def test_plot_archived_report_does_not_need_original_tmp_experiment(tmp_path):
    pytest.importorskip("matplotlib")
    config = plan()
    campaign.plot({"plan": config, "experiment": {"timing": {"period_ns": 20}},
                   "points": [], "frontiers": {"claude": [], "gpt": []}, "outcomes": []},
                  tmp_path / "archived-frontier")
    assert (tmp_path / "archived-frontier.pdf").is_file()


@pytest.mark.parametrize("journal", ["solver", "agent", ("revisions", "r0", "author")])
def test_shared_trace_export_keeps_complete_command_and_result(tmp_path, journal):
    from behave.agent.run_store import JournalDirectory
    from behave.core.records import canonical_json
    from tools.eval_review_trial import export_candidate

    store = JournalDirectory(tmp_path / "run")
    events = store.journal(*((journal,) if isinstance(journal, str) else journal))
    command = "printf 'one\\ntwo\\n'"
    first = events.append((), "tool_requested", {"turn": 0, "call": {
        "name": "run_command", "arguments": {"command": command}}})
    ref = store.objects.put(canonical_json({"stdout": "one\ntwo\n", "stderr": "", "exit_code": 0}))
    events.append((first,), "tool_settled", {"turn": 0, "result": ref.as_dict()}, (ref,))
    result = export_candidate(store.directory, tmp_path / "trace", journal=journal)
    assert result["tool_calls"] == 1 and result["events"] == 2
    text = (tmp_path / "trace/trace.md").read_text()
    assert command in text and "one\ntwo\n" in text
    assert (tmp_path / "trace/0001_tool_result.json").is_file()


def test_status_missing_directory_creates_nothing(tmp_path):
    root = tmp_path / 'not-launched'
    assert campaign.status(root)['state'] == 'not-created'
    assert not root.exists()


def test_status_keeps_unsettled_tools_and_unjudged_candidates_distinct(tmp_path):
    from behave.agent.run_store import JournalDirectory
    from behave.ppa.records import write_document

    config = plan()
    config.update(replicates=1, methods=config['methods'][:1])
    campaign.publish(tmp_path / 'plan.json', config)
    root = tmp_path / 'runs/claude/r00'
    search = JournalDirectory(root / 'search')
    journal = search.journal('agent')
    event = journal.append((), 'model_settled', {'outcome': 'reply'})
    journal.append((event,), 'tool_requested', {'call': {'name': 'measure_ppa'}})
    final = JournalDirectory(root / 'final')
    candidates = {key: {'history': [] if score == 'pending' else [
        {'mode': 'final', 'final': True, 'result': {'score': score}}]}
        for key, score in [('pass', 1), ('fail', 0), ('none', None), ('next', 'pending')]}
    ref, closure = write_document(final.objects, {'candidates': candidates})
    event = final.journal('measurements').append((), 'settled', {'record': ref.as_dict()}, closure)
    final.journal('measurements').append((event,), 'requested', {'candidate': 'next'})
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))
    result = campaign.status(tmp_path)
    task = result['tasks'][tmp_path.name]
    row = task['attempts'][0]
    assert task['planned'] == 1 and task['search_complete'] == task['final_complete'] == 0
    assert row['model_replies'] == 1 and row['awaiting'] == 'tool' and row['tool'] == 'measure_ppa'
    assert row['terminal_reason'] is None
    assert row['final_measurements'] == {'candidates_known': 4, 'requested': 1,
        'final_attempts_completed': 3, 'final_pass': 1, 'final_fail': 1,
        'final_no_conclusion': 1, 'unsettled_measurement': True}
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*')) == before


def test_status_does_not_read_another_hosts_same_tmp_path(tmp_path):
    root, foreign = tmp_path / 'archive', tmp_path / 'other-node-tmp'
    foreign.mkdir()
    campaign.publish(root / 'runtime.json', {'state': 'running', 'host': 'some-other-host',
                                            'local_directory': str(foreign)})
    result = campaign.status(root)
    assert result['execution_visible'] is False
    assert result['query_on_host'] == 'some-other-host'
    assert 'tasks' not in result


def test_status_reads_staged_group_without_creating_unstarted_runs(tmp_path):
    root, local = tmp_path / 'archive', tmp_path / 'local'
    campaign.publish(root / 'runtime.json', {'state': 'running', 'host': campaign.socket.gethostname(),
                                            'local_directory': str(local)})
    for name in ('mx', 'delta'):
        campaign.publish(local / 'campaigns' / name / 'plan.json', plan())
    result = campaign.status(root)
    assert result['view'] == 'node-local'
    assert set(result['tasks']) == {'mx', 'delta'}
    assert all(t['planned'] == 48 and t['search_complete'] == 0 for t in result['tasks'].values())
    assert not list(local.rglob('runs'))


def test_shared_execution_is_readable_after_losing_original_node(tmp_path):
    root = tmp_path / 'archive'
    campaign.publish(root / 'runtime.json', {'state': 'running', 'host': 'gone-node',
        'local_directory': '/tmp/gone-scratch', 'storage': 'shared',
        'execution_directory': str(root / 'execution')})
    campaign.publish(root / 'execution/campaigns/mx/plan.json', plan())
    result = campaign.status(root)
    assert result['execution_visible'] is True
    assert result['view'] == 'shared'
    assert result['tasks']['mx']['planned'] == 48


@pytest.mark.parametrize('archived', [False, True])
@pytest.mark.parametrize('settled', [False, True])
def test_status_uses_terminal_job_receipt_without_rewriting_runtime(tmp_path, monkeypatch, archived, settled):
    from types import SimpleNamespace

    from behave.agent.jobs import JobControl

    runtime = {'state': 'running', 'host': 'other-node', 'local_directory': '/tmp/foreign-run'}
    campaign.publish(tmp_path / 'runtime.json', runtime)
    campaign.publish(tmp_path / 'supervision/launch.json', {'metadata': {'job_key': 'exact-key'}})
    (tmp_path / 'job/job').mkdir(parents=True)
    if archived:
        (tmp_path / 'execution').mkdir()
    receipt = {'state': 'settled' if settled else 'started', 'cancel_requested': True,
               'cleanup': 'complete' if settled else 'unknown',
               'result': {'status': 'cancelled', 'returncode': -9} if settled else None}

    def inspect(control, key):
        assert key == 'exact-key'
        return SimpleNamespace(as_dict=lambda: receipt)

    monkeypatch.setattr(JobControl, 'inspect', inspect)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))
    result = campaign.status(tmp_path)
    assert result['state'] == ('cancelled' if settled else 'running')
    assert result['job'] == receipt
    assert result['execution_visible'] is archived
    assert json.loads((tmp_path / 'runtime.json').read_text()) == runtime
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*')) == before
