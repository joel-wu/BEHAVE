"""Cross-job partial recovery preserves acquired tasks and completed candidates."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_slime_rollout_handoff import partial_run
from test_slime_task_pool import source_at
from test_slime_training_plan import inputs  # noqa: F401

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.train import slime_acquisition
from behave.train.slime_acquisition import committed_plan
from behave.train.slime_rollout import export_handoff, inspect_handoff, read_binding, round_plan
from tools.slime_training_plan import prepare


def acquired_partial(tmp_path, publication, inputs):
    study = tmp_path / "study.json"
    study.write_bytes(canonical_json({"stages": [{"after_updates": 1}]}))
    original_task = publication.task
    publication.task = replace(original_task,
        spec=publication.file("spec.md", b"A newly acquired task with distinct inputs."),
        reference_behavior=replace(original_task.reference_behavior,
            files=(publication.file("model.py", b"ACQUIRED_PRIVATE_REFERENCE = 42"),)),
        run_policy=publication.put(b"ACQUIRED_PRIVATE_RUN_POLICY"))
    try:
        enlarged = source_at(tmp_path / "enlarged", publication, inputs, tasks="abcdef")
    finally:
        publication.task = original_task
    configured = ({**inputs[0], "self_improvement": {"study": str(study)}}, *inputs[1:])
    state = {}

    def activate(binding, native):
        parent = BlobRef.of(native.journal("rounds").load(verify_artifacts=False)[-1].to_bytes())
        response = study.parent / "acquisitions/update_00000001" / parent.sha256 / "response.json"
        response.parent.mkdir(parents=True)
        row = {"schema": "behave.native-acquisition/1", "completed_updates": 1,
               "parent_checkpoint": parent.as_dict(), "training_binding": str(enlarged.path),
               "training_manifest": enlarged.binding["manifest"]}
        response.write_bytes(canonical_json(row))
        state.update(parent=parent, response=response, row=row)
        return {**binding, **{k: enlarged.binding[k] for k in ("objects", "plan", "manifest")}}

    values = partial_run(tmp_path, configured, saved=True, activate=activate)
    return values, state, enlarged


def test_partial_acquired_manifest_survives_new_job_and_activation(tmp_path, publication, inputs, monkeypatch):
    values, state, enlarged = acquired_partial(tmp_path, publication, inputs)
    root, config, release, split, task, active, old_plan, policy = values
    info = inspect_handoff(root, config, release, [task], workers_stopped=True)
    assert info["binding"]["manifest"] == enlarged.binding["manifest"]
    assert info["recovery"].indices == frozenset(range(5))
    descriptor = export_handoff(info, tmp_path / "handoff")
    # The public job entry exports the committed journal prefix before prepare.
    for event in info["parent_events"]:
        info["parent_reader"].put(event.to_bytes())
    new_config = {**config, "rollout_recovery": descriptor,
                  "endpoint_url": "http://127.0.0.1:25000/v1/chat/completions"}
    path = tmp_path / "new/plan"
    binding = prepare(path, new_config, release, [task], split,
                      parent_reader=info["parent_reader"], parent_checkpoint=info["parent"])
    assert binding["manifest"] == active["manifest"]
    assert binding["objects"] != active["objects"]
    endpoint = LocalChatEndpoint.from_dict(binding["endpoint"])
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy))
    plan = round_plan(binding, DirectoryStore(binding["objects"]), round_id=1, endpoint=endpoint, policy=policy)
    assert plan.execution.jobs[:5] == old_plan.execution.jobs[:5]
    assert plan.group_ids == old_plan.group_ids
    assert {job.task.task_id for job in plan.execution.jobs} == set("abcdef")
    # Metadata alone is insufficient: a fresh candidate enters the real
    # publication loader before its solver can start. Require the acquired
    # release and private reference inputs in the destination as well.
    from behave.data.published import load_published_task
    for job in plan.execution.jobs:
        task_inputs = load_published_task(DirectoryStore(binding["objects"]),
                                         job.task.release, job.task.task_id)
        assert task_inputs.public == job.task
    source = SimpleNamespace(binding=binding, path=path / "binding.json", _acquisition_state=None,
                             run=info["native"], _parent_pool={"tasks": {}})
    reference = source.run.objects.put(canonical_json(state["row"]))
    slime_acquisition.activate(source, reference)
    assert source.binding["objects"] == binding["objects"]
    assert len(source._pool_tasks) == 6
    # Activation retains the imported plan/policy/token closure, not just tasks.
    assert DirectoryStore(source.binding["objects"]).read(policy)
    assert read_binding(root / "artifacts/plan/binding.json")["manifest"] != active["manifest"]
    # Exercise the ordinary round executor too: five retained candidates never
    # run again; all other slots use the new endpoint and produce native tokens.
    from test_slime_rollout_handoff import candidate

    from behave.agent.run_store import JournalDirectory
    from behave.task_execution.batch import BatchRecovery
    from behave.task_execution.result import TaskReport
    from behave.task_execution.simulator_judge import SimulatorJudge
    from behave.train.rollouts import run_rollouts
    from behave.train.training_data import NativeChatVerifier
    calls = []

    def execute(**kwargs):
        job, run = kwargs["job"], kwargs["run"]
        assert load_published_task(kwargs["reader"], job.task.release, job.task.task_id).public == job.task
        index = plan.execution.jobs.index(job)
        calls.append(index)
        result, ref, _, closure = candidate(job, run.objects, plan.requirements, index)
        event = run.journal("task").append((), "fixture", {"result": ref.as_dict()}, closure)
        return TaskReport(result, ref, BlobRef.of(event.to_bytes()))

    monkeypatch.setattr("behave.task_execution.batch.execute_task", execute)
    monkeypatch.setattr("behave.task_execution.batch.require_task_quiescent", lambda *a, **kw: None)
    reader = DirectoryStore(binding["objects"])
    recovery = BatchRecovery.load(reader, BlobRef.from_dict(binding["rollout_recovery"]["case_head"]))
    result = run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=reader,
        run=JournalDirectory(tmp_path / "replayed"), judge=SimulatorJudge(),
        verifier=NativeChatVerifier(endpoint), recovery=recovery)
    assert sorted(calls) == list(range(5, len(plan.execution.jobs)))
    assert len(result["candidates"]) == len(plan.execution.jobs)


@pytest.mark.parametrize("change", ["missing", "parent", "manifest", "active", "settings"])
def test_acquired_partial_requires_committed_matching_state(tmp_path, publication, inputs, change):
    values, state, enlarged = acquired_partial(tmp_path, publication, inputs)
    root, config, release, split, task, active, old_plan, policy = values
    if change == "missing":
        state["response"].rename(state["response"].with_suffix(".uncommitted"))
    elif change in ("parent", "manifest"):
        row = dict(state["row"])
        row["parent_checkpoint" if change == "parent" else "training_manifest"] = BlobRef.of(b"wrong").as_dict()
        state["response"].write_bytes(canonical_json(row))
    else:
        path = root / "artifacts/plan/rounds/native/request_00000001/binding.json"
        row = parse_json(path.read_bytes())
        if change == "active":
            row["manifest"] = read_binding(root / "artifacts/plan/binding.json")["manifest"]
        else:
            row["recipe"] = {**row["recipe"], "learning_rate": .5}
        path.write_bytes(canonical_json(row))
    with pytest.raises(ValueError):
        inspect_handoff(root, config, release, [task], workers_stopped=True)


def test_checkpoint_carried_acquisition_recovery(tmp_path, publication, inputs, monkeypatch):
    from test_slime_acquisition import configured, fake_acquisition
    from test_slime_task_pool import completed_checkpoint, mock_scoring

    source, enlarged = configured(tmp_path, publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    fake_acquisition(monkeypatch, source, enlarged)
    source.generate(0)
    completed_checkpoint(source, 0)
    scoring["version"] += 1
    source.generate(1)
    parent = completed_checkpoint(source, 1)
    state, active = committed_plan(source.binding, parent, source.run.objects)
    assert state["completed_updates"] == 1
    assert active["manifest"] == enlarged.binding["manifest"]


@pytest.mark.parametrize('wrong_acquisition', [False, True])
def test_scored_acquired_handoff_replays_without_new_rollouts(
        tmp_path, publication, inputs, monkeypatch, wrong_acquisition):
    from test_slime_recipe import arguments

    from behave.agent.run_store import JournalDirectory
    from behave.train.slime_data import ScoredDataSource
    from behave.train.slime_launch import preset
    from behave.train.slime_rollout import SlimeBatch, save_batch
    from tools.eval_review_trial import write_json

    values, state, enlarged = acquired_partial(tmp_path, publication, inputs)
    root, config, release, split, task, active, plan, policy = values
    native = JournalDirectory(Path(active['run_directory']) / 'native')
    original = JournalDirectory(Path(active['run_directory']) / 'round_00000001')
    head = original.objects.put(b'fixture scored report')
    batch = SlimeBatch(({'tokens': [10, 11, 12], 'rollout_log_probs': [-.5, -.25]},),
                      ({'retained_turns': 2, 'reward': .5},), preset('grpo'),
                      group_selection={'selected_indices': [0], 'launched_indices': [0, 1, 2],
                                       'completion_order': ['original'], 'observations': []})
    prepared = original.directory / 'prepared_batch.json'
    save_batch(prepared, batch, head=head, manifest=plan.manifest, plan=BlobRef.of(plan.to_bytes()))
    original_bytes = prepared.read_bytes()
    endpoint = LocalChatEndpoint.from_dict(active['endpoint'])
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy))
    acquisition = native.objects.put(canonical_json(state['row']))
    row = {'schema': 'behave.slime-scored-round/1', 'round': 1,
           'manifest': plan.manifest.as_dict(), 'policy': policy.as_dict(),
           'head': head.as_dict(), 'objects': str(original.objects.directory),
           'endpoint': endpoint.as_dict(), 'prepared_batch': str(prepared),
           'acquisition': (BlobRef.of(b'wrong') if wrong_acquisition else acquisition).as_dict()}
    receipt = native.objects.put(canonical_json(row))
    native.journal('rounds').append(native.journal('rounds').load(verify_artifacts=False),
        'round_scored', {'round': 1, 'receipt': receipt.as_dict(),
                         'acquisition': acquisition.as_dict()}, (receipt, acquisition))
    if wrong_acquisition:
        with pytest.raises(ValueError, match='committed acquisition'):
            inspect_handoff(root, config, release, [task], workers_stopped=True)
        return
    info = inspect_handoff(root, config, release, [task], workers_stopped=True)
    descriptor = export_handoff(info, tmp_path / 'scored_handoff')
    for event in info['parent_events']:
        info['parent_reader'].put(event.to_bytes())
    new_root = tmp_path / 'scored_resume'
    new_dir = new_root / 'artifacts/plan'
    changed = {**config, 'endpoint_url': 'http://127.0.0.1:28000/v1/chat/completions'}
    write_json(new_root / 'configuration.json', changed)
    write_json(new_root / 'selection.json', {'tasks': [task], 'release': str(release)})
    write_json(new_root / 'continuation.json', {'load': info['load']})
    binding = prepare(new_dir, {**changed, 'rollout_recovery': descriptor}, release, [task], split,
                      parent_reader=info['parent_reader'], parent_checkpoint=info['parent'])
    args = arguments(preset('grpo'))
    args.behave_rollout_binding = str(new_dir / 'binding.json')
    args.behave_rollout_binding_sha256 = BlobRef.of(canonical_json(binding)).sha256
    args.load = info['load']; args.save = str(tmp_path / 'new_checkpoints')
    args.save_interval, args.num_rollout = 1, 4
    source = ScoredDataSource(args)
    source.load(0)
    for name in ('engine_versions', 'run_service', 'from_training_report'):
        monkeypatch.setattr(f'behave.train.slime_data.{name}',
                            lambda *a, **kw: pytest.fail('must not generate or rescore'))
    monkeypatch.setattr(SlimeBatch, 'rollout_output', lambda self: self)
    assert source.generate(1) == batch
    assert source._acquisition_state == acquisition
    assert source.binding['manifest'] == enlarged.binding['manifest']
    assert prepared.read_bytes() == original_bytes
    assert source.journal.load(verify_artifacts=False)[-1].payload['acquisition'] == acquisition.as_dict()
    again = inspect_handoff(new_root, changed, release, [task], workers_stopped=True)
    assert again['scored_receipt'] == receipt.as_dict()
    assert again['policy_ref'] == policy
    with pytest.raises(ValueError):
        source.generate(1)
