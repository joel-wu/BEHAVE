

import pytest

from behave.eval import fleet as fleet_module
from tools import eval_checkpoint_fleet as legacy_fleet
from tools import eval_checkpoint_study as study
from tools.eval_review_trial import write_json


def test_operator_recovery_checks_are_shared_not_reimplemented():
    from tools import eval_checkpoint_fleet as operator

    for name in ('verify_unstarted', 'verify_port_preflight',
                 'terminal', 'generation_blocker'):
        assert getattr(operator, name) is getattr(fleet_module, name)
    assert not hasattr(fleet_module, 'verify_recovery_preflight')
    assert operator.verify_recovery_preflight.__module__ == operator.__name__


def test_fleet_can_use_reviewed_configuration_without_rewriting_history(tmp_path):
    config = {"resources": {}, "checkpoint_policy": {"paired_updates": list(range(3, 61, 3))}}
    write_json(tmp_path / 'reviewed.json', config)
    write_json(tmp_path / 'fleet.json', {
        'archive_root': str(tmp_path / 'history'), 'configuration_file': str(tmp_path / 'reviewed.json'),
        'nodes': {'123': {'generation_cpu_ids': list(range(48))}},
        'rollout_workers_per_gpu': [5] * 8, 'name': 'current', 'code_directory': 'code',
        'isolation_startup_slots': 4, 'serving_probe_request': 'probe'})
    result = fleet_module.measurement_config(tmp_path, {}, {'allocation': 123, 'gpu': 0})
    assert result['checkpoint_policy'] == config['checkpoint_policy']
    assert result['resources']['tensor_parallel_per_engine'] == 1
    assert not (tmp_path / 'history/configuration.json').exists()


def test_append_only_live_queue(tmp_path):
    import copy
    item={"key":"main-5-3", "entry":{"model_line":"main","update":5},
          "seed":3,"directory":"/measurement/main5s3"}
    old={"nodes":{},"measurements":[item]}
    new=copy.deepcopy(old)
    new["measurements"].append({**item,"key":"main-5-42","seed":42,"directory":"/measurement/main5s42"})
    write_json(tmp_path / 'fleet.json',new)
    assert fleet_module.reload_fleet(tmp_path,old)==new
    for invalid in ({**old,'nodes':{'new':{}}}, {**old,'measurements':[]},
                    {**old,'measurements':[item,item]}):
        write_json(tmp_path / 'fleet.json',invalid)
        with pytest.raises(ValueError): fleet_module.reload_fleet(tmp_path,old)
    changed=copy.deepcopy(old);changed['measurements'][0]['entry']['update']=6
    write_json(tmp_path / 'fleet.json',changed)
    with pytest.raises(ValueError):fleet_module.reload_fleet(tmp_path,old)


def test_new_score_ready_waits_for_generation(tmp_path):
    item={'key':'new','directory':str(tmp_path/'measurement'),'score_ready':str(tmp_path/'ready.json')}
    config={'measurements':[item]}
    fleet_module.publish_score_readiness(config)
    assert not (tmp_path/'ready.json').exists()
    write_json(tmp_path/'measurement/generation.json',{'state':'supervising'})
    fleet_module.publish_score_readiness(config)
    assert not (tmp_path/'ready.json').exists()
    write_json(tmp_path/'measurement/generation.json',{'state':'completed'})
    fleet_module.publish_score_readiness(config)
    assert study.read(tmp_path/'ready.json')['source']==str(tmp_path/'measurement/rollout')
    before=(tmp_path/'ready.json').read_bytes()
    fleet_module.publish_score_readiness(config)
    assert (tmp_path/'ready.json').read_bytes()==before


@pytest.mark.parametrize("state", ["submitted", "starting", "supervising", "failed", "missing"])
def test_handoff_waits_for_successful_previous_gpu_owner(monkeypatch, state):
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": state})
    monkeypatch.setattr(fleet_module, "resolve_checkpoint", lambda _: pytest.fail("owner has not finished"))
    assert fleet_module.generation_blocker({"legacy_generation": ["train"], "required_checkpoint": {}})


def test_handoff_requires_committed_checkpoint(monkeypatch):
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": "completed"})
    node = {"legacy_generation": ["train"], "required_checkpoint": {"update": 40}}
    monkeypatch.setattr(fleet_module, "resolve_checkpoint", lambda _: None)
    assert fleet_module.generation_blocker(node) == "waiting for committed checkpoint"
    monkeypatch.setattr(fleet_module, "resolve_checkpoint", lambda _: {"model_path": "hf40"})
    assert fleet_module.generation_blocker(node) is None
    assert fleet_module.generation_blocker({"legacy_generation": []}) is None


def test_overflow_keeps_scoring_owner_and_prefers_primary():
    config = {"nodes": {"123": {}, "456": {"overflow_lanes": ["syn"]}}}
    item = {"allocation": 123, "entry": {"model_line": "syn"}}
    assert fleet_module.generation_slot(config, item, set(), {123, 456}) == {"allocation": 123, "gpu": 0}
    busy = {(123, gpu) for gpu in range(8)} | {(456, 0)}
    assert fleet_module.generation_slot(config, item, busy, {123, 456}) == {"allocation": 456, "gpu": 1}
    assert item == {"allocation": 123, "entry": {"model_line": "syn"}}
    assert fleet_module.generation_slot(config, item, busy, {123}) is None
    assert fleet_module.generation_slot(config, {**item, "entry": {"model_line": "other"}}, busy, {123, 456}) is None
    config["nodes"]["456"]["overflow_lanes"] = ["*"]
    assert fleet_module.generation_slot(config, item, busy, {123, 456})["allocation"] == 456


def test_fleet_handoff_does_not_start_duplicate_scorer(tmp_path, monkeypatch):
    write_json(tmp_path / "fleet.json", {"nodes": {"456": {
        "legacy_generation": [], "legacy_scoring": [], "scoring_enabled": False}}, "measurements": []})
    monkeypatch.setattr(fleet_module, "start_scores", lambda *args, **kwargs: pytest.fail("scoring owner unchanged"))
    class StopLoop(Exception):
        pass
    monkeypatch.setattr(fleet_module.time, "sleep", lambda _: (_ for _ in ()).throw(StopLoop()))
    with pytest.raises(StopLoop):
        fleet_module.run(tmp_path)
    assert study.read(tmp_path / "progress.json")["blocked"] == {}


def test_controller_only_revision_keeps_worker_snapshot(tmp_path, monkeypatch):
    import hashlib
    script = tmp_path / "controller.py"
    script.write_text("def run(root): pass\n")
    config = {"archive_root": str(tmp_path), "code_directory": "old-workers",
              "controller_script": str(script), "controller_sha256": hashlib.sha256(script.read_bytes()).hexdigest()}
    write_json(tmp_path / "fleet.json", config)
    captured = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda control, argv, **kwargs:
                        captured.update(argv=argv, options=kwargs))
    fleet_module.launch(tmp_path, cwd=tmp_path)
    assert captured["options"]["cwd"] == tmp_path
    assert captured["options"]["wall_seconds"] is None
    assert captured["options"]["cleanup_seconds"] == 300
    assert str(tmp_path / "old-workers/snapshot") in captured["argv"]
    assert captured["argv"][-2:] == [str(script), str(tmp_path)]
    script.write_text("changed\n")
    with pytest.raises(ValueError, match="identity changed"):
        fleet_module.launch(tmp_path)


def test_independent_seed_controls_do_not_collide(tmp_path):
    write_json(tmp_path / "configuration.json", {"archive_root": str(tmp_path / "archive"),
        "launch_id": "fleet", "independent_measurement": True})
    a = {"model_line": "main", "update": 5, "inference_seeds": [3]}
    b = {**a, "inference_seeds": [42]}
    assert study.control_directory(tmp_path, a) != study.control_directory(tmp_path, b)
    assert "eval-seed-03/control/fleet" in str(study.control_directory(tmp_path, a))
    with pytest.raises(ValueError, match="exactly one"):
        study.control_directory(tmp_path, {**a, "inference_seeds": [3, 42]})


def test_eight_gpu_slots_share_no_generation_cpus(tmp_path):
    archive = tmp_path / "archive"
    write_json(archive / "configuration.json", {"resources": {}, "attempt": {"max_turns": 60}})
    write_json(tmp_path / "fleet.json", {"archive_root": str(archive), "code_directory": "code",
        "name": "fleet", "serving_probe_request": "probe.json", "isolation_startup_slots": 4,
        "rollout_workers_per_gpu": [6,6,6,6,5,5,5,5],
        "nodes": {"123": {"generation_cpu_ids": list(range(96, 148))}}})
    seen = set()
    inflight = 0
    ports = set()
    for gpu in range(8):
        config = fleet_module.measurement_config(tmp_path, {}, {"allocation": 123, "gpu": gpu})
        r = config["resources"]
        assert not seen.intersection(r["generation_cpu_ids"])
        seen.update(r["generation_cpu_ids"])
        ports.add(r["port_first"])
        ports.add(r["nccl_port_first"])
        assert r["lease_serving_ports"] is True
        inflight += r["rollout_workers"]
        assert r["device_groups"] == [str(gpu)]
        assert config["attempt"]["max_turns"] == 60
    assert seen == set(range(96,148))
    assert len(ports) == 16 and inflight == 44
    assert all(1024 <= p < 32768 for p in ports)


def test_existing_measurement_owner_cannot_be_overwritten(tmp_path):
    write_json(tmp_path / "generation/main-5-3/configuration.json", {})
    # Check before any immutable launch/measurement record can be rewritten.
    from unittest.mock import patch
    with patch.object(fleet_module, "measurement_config", return_value={}), pytest.raises(ValueError, match="already has"):
        fleet_module.start_measurement(tmp_path, {"key": "main-5-3", "entry": {}, "seed": 3}, {}, {})


def test_score_step_uses_allocation_count_not_highest_cpu_id(tmp_path, monkeypatch):
    cpus = list(range(94)) + list(range(148, 190))
    write_json(tmp_path / "fleet.json", {"archive_root": str(tmp_path), "code_directory": "code",
        "scoring_workers": 24, "scoring_control": "control-retry",
        "nodes": {"123": {"scoring_cpu_ids": cpus, "allocated_cpus": 188}}})
    captured = {}
    monkeypatch.setattr("behave.runtime.jobs.submit",
        lambda control, argv, **kwargs: captured.update(control=control, argv=argv, **kwargs))
    fleet_module.start_scores(tmp_path, 123, cwd=tmp_path)
    assert captured["cwd"] == tmp_path
    assert captured["wall_seconds"] is None and captured["cleanup_seconds"] == 300
    # This bootstrap runs inside the chosen snapshot: retain its historical
    # staging/worker entries even when today's caller uses the shared runtime.
    bootstrap = captured["argv"][captured["argv"].index("-c") + 1]
    assert "from tools.infra_validation_job import stage;" in bootstrap
    assert "from tools.eval_score_stream import run;" in bootstrap
    assert "--cpus-per-task=188" in captured["argv"]
    assert "--cpus-per-task=190" not in captured["argv"]
    assert f"--cpu-bind=mask_cpu:{hex(sum(1 << cpu for cpu in cpus))}" in captured["argv"]
    assert captured["control"].name == "control-retry"
    assert len(captured["metadata"]["cpus"]) == 136


def test_relocated_scorer_keeps_owner_worker_code_and_archive(tmp_path, monkeypatch):
    config = {"archive_root": str(tmp_path), "code_directory": "generation-code",
        "scoring_workers": 24, "nodes": {
            "123": {"scoring_cpu_ids": [0, 1], "scoring_allocation": 456,
                    "scoring_control": "handoff", "scoring_code_directory": "original-score-code"},
            "456": {"allocated_cpu_ids": [0, 1, 2, 3], "allocated_cpus": 4,
                    "generation_cpu_ids": [2, 3], "scoring_enabled": False}}}
    write_json(tmp_path / "fleet.json", config)
    captured = {}
    monkeypatch.setattr("behave.runtime.jobs.submit",
        lambda control, argv, **kwargs: captured.update(control=control, argv=argv, metadata=kwargs["metadata"]))
    fleet_module.start_scores(tmp_path, 123)
    assert "--jobid=456" in captured["argv"]
    assert "--cpus-per-task=4" in captured["argv"]
    assert captured["argv"][-1] == "123"
    assert str(tmp_path / "original-score-code/snapshot") in captured["argv"]
    assert captured["control"] == tmp_path / "scoring/123/handoff"
    config["nodes"]["456"]["generation_cpu_ids"] = [1, 2, 3]
    write_json(tmp_path / "fleet.json", config)
    with pytest.raises(ValueError, match="unoccupied CPU"):
        fleet_module.start_scores(tmp_path, 123)


def test_training_node_cannot_receive_new_generation(tmp_path, monkeypatch):
    write_json(tmp_path / "fleet.json", {"nodes": {"123": {
        "generation_enabled": False, "legacy_generation": [], "legacy_scoring": [],
        "scoring_enabled": False}}, "measurements": [
            {"key": "main-5-3", "allocation": 123, "seed": 3, "entry": {"model_line": "main", "update": 5},
             "directory": str(tmp_path / "measurement")}]})
    monkeypatch.setattr(fleet_module, "start_measurement", lambda *args, **kwargs: pytest.fail("training owns GPUs"))
    class StopLoop(Exception):
        pass
    monkeypatch.setattr(fleet_module.time, "sleep", lambda _: (_ for _ in ()).throw(StopLoop()))
    with pytest.raises(StopLoop):
        fleet_module.run(tmp_path)
    progress = study.read(tmp_path / "progress.json")
    assert progress["generation_busy_gpus"] == 0
    assert progress["waiting_measurements"] == 1


def failed_startup(directory):
    write_json(directory / "generation.json", {"state": "interrupted", "cleanup_complete": True})
    write_json(directory / "rollout/cleanup.json", {"cleanup_complete": True,
        "services": [{"ready": False, "cleanup_complete": True}]})
    write_json(directory / "rollout/settings.json", {"attempt_namespace": str(directory)})


def port_preflight_failure(directory, monkeypatch):
    control = directory / "control/first"
    control.mkdir(parents=True)
    (control / "run.log").write_text("in serving_port_lease\n sock.bind(\n"
        "OSError: [Errno 98] Address already in use\n")
    for name in ("settings.json", "sampling.json", "trained/configuration.json"):
        write_json(directory / "rollout" / name, {})
    monkeypatch.setattr("behave.runtime.jobs.status_of",
        lambda _: {"state": "failed", "returncode": 1})
    return control


def test_port_preflight_retry_accepts_only_exact_unexecuted_preparation(tmp_path, monkeypatch):
    control = port_preflight_failure(tmp_path, monkeypatch)
    fleet_module.verify_port_preflight(tmp_path, control)
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": "supervising"})
    with pytest.raises(ValueError, match="exited"):
        fleet_module.verify_port_preflight(tmp_path, control)


@pytest.mark.parametrize("artifact", ["node_status.json", "generation.json", "scoring.json",
    "rollout/runtime.json", "rollout/run", "rollout/serving", "rollout/trained/unexpected.json"])
def test_port_preflight_retry_rejects_any_execution_evidence(tmp_path, monkeypatch, artifact):
    control = port_preflight_failure(tmp_path, monkeypatch)
    write_json(tmp_path / artifact, {})
    with pytest.raises(ValueError, match="artifacts"):
        fleet_module.verify_port_preflight(tmp_path, control)


def test_port_preflight_retry_rejects_different_failure(tmp_path, monkeypatch):
    control = port_preflight_failure(tmp_path, monkeypatch)
    (control / "run.log").write_text("CUDA out of memory")
    with pytest.raises(ValueError, match="explicit port"):
        fleet_module.verify_port_preflight(tmp_path, control)


def test_startup_guard_accepts_only_unstarted_clean_failure(tmp_path):
    failed_startup(tmp_path)
    fleet_module.verify_unstarted(tmp_path)


def recovery_preflight_failure(directory, monkeypatch):
    control = directory / "control/original"
    control.mkdir(parents=True)
    failed_startup(directory)
    write_json(directory / "rollout/cleanup.json", {"cleanup_complete": True,
        "services": [{"ready": True, "cleanup_complete": True}]})
    write_json(directory / "rollout/settings.json", {"generation_recovery": {"source": "original"}})
    (directory / "generation.log").write_text("in recover_jobs\n"
        "TypeError: BatchPlan.__init__() got an unexpected keyword argument 'recorded_admissions'\n")
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": "failed"})
    return control


def test_recovery_preflight_can_retry_only_before_new_candidate_batch(tmp_path, monkeypatch):
    control = recovery_preflight_failure(tmp_path, monkeypatch)
    legacy_fleet.verify_recovery_preflight(tmp_path, control)
    write_json(tmp_path / "rollout/plan.json", {})
    with pytest.raises(ValueError, match="candidate or scoring"):
        legacy_fleet.verify_recovery_preflight(tmp_path, control)


def test_recovery_preflight_recognizes_old_decoder_rejecting_new_field(tmp_path, monkeypatch):
    control = recovery_preflight_failure(tmp_path, monkeypatch)
    (tmp_path / "generation.log").write_text("in recover_jobs\nBatchRecovery.from_events\n"
        "batch_plan.py\nMissing: []. Unexpected: ['recorded_admissions'].\n")
    legacy_fleet.verify_recovery_preflight(tmp_path, control)
    (tmp_path / "generation.log").write_text("in recover_jobs\nBatchRecovery.from_events\n"
        "batch_plan.py\nMissing: ['jobs']. Unexpected: ['recorded_admissions'].\n")
    with pytest.raises(ValueError, match="explicit frozen"):
        legacy_fleet.verify_recovery_preflight(tmp_path, control)


@pytest.mark.parametrize("artifact", ["rollout/run", "rollout/report.json", "rollout/trace_index.json",
                                    "rollout/progress.json", "scoring"])
def test_recovery_preflight_never_replays_candidates(tmp_path, monkeypatch, artifact):
    control = recovery_preflight_failure(tmp_path, monkeypatch)
    write_json(tmp_path / artifact, {})
    with pytest.raises(ValueError, match="candidate or scoring"):
        legacy_fleet.verify_recovery_preflight(tmp_path, control)


def test_recovery_preflight_rejects_other_failures_or_live_owner(tmp_path, monkeypatch):
    control = recovery_preflight_failure(tmp_path, monkeypatch)
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": "supervising"})
    with pytest.raises(ValueError, match="exited"):
        legacy_fleet.verify_recovery_preflight(tmp_path, control)
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": "failed"})
    (tmp_path / "generation.log").write_text("CUDA out of memory")
    with pytest.raises(ValueError, match="explicit frozen"):
        legacy_fleet.verify_recovery_preflight(tmp_path, control)


@pytest.mark.parametrize("artifact", ["rollout/run", "rollout/progress.json", "rollout/report.json",
                                    "rollout/trace_index.json", "scoring.json", "scoring"])
def test_startup_guard_never_replays_candidate_or_score(tmp_path, artifact):
    failed_startup(tmp_path)
    write_json(tmp_path / artifact, {})
    with pytest.raises(ValueError, match="possible rollout"):
        fleet_module.verify_unstarted(tmp_path)
    assert (tmp_path / artifact).exists()


@pytest.mark.parametrize("file,field,value", [
    ("generation.json", "state", "running"), ("generation.json", "state", "completed"),
    ("generation.json", "cleanup_complete", False), ("rollout/cleanup.json", "cleanup_complete", False),
    ("rollout/cleanup.json", "services", [{"ready": True, "cleanup_complete": True}]),
    ("rollout/cleanup.json", "services", [])])
def test_startup_guard_rejects_unknown_cleanup_or_started_service(tmp_path, file, field, value):
    failed_startup(tmp_path)
    record = study.read(tmp_path / file)
    write_json(tmp_path / file, {**record, field: value})
    with pytest.raises(ValueError):
        fleet_module.verify_unstarted(tmp_path)


def retry_fixture(tmp_path, monkeypatch):
    from tests.infrastructure.test_eval_checkpoint_study import fixture
    lane = tmp_path / "generation/task"
    fixture(lane)
    config = study.read(lane / "configuration.json")
    config.update(archive_root=str(tmp_path / "archive"), code_directory="code",
                  independent_measurement=True, launch_id="original")
    write_json(lane / "configuration.json", config)
    write_json(tmp_path / "archive/code/snapshot.json", {"sha256": "old-code"})
    write_json(tmp_path / "archive/code-new/snapshot.json", {"sha256": "new-code"})
    entry = {"model_line": "seed-only", "update": 12, "inference_seeds": [3]}
    handoff = {"model_path": "/fixture/weights", "model_revision": "v1"}
    write_json(lane / "queue.json", {"checkpoints": [entry]})
    write_json(study.checkpoint_directory(lane, entry) / "checkpoint.json", handoff)
    directory = study.prepare_measurement(lane, entry, 3, handoff).parent
    before = study.read(directory / "manifest.json")
    failed_startup(directory)
    control = study.control_directory(lane, entry)
    write_json(control / "launch.json", {"original": True})
    old = {"allocation": 123, "gpu": 4, "key": "task", "control": str(control)}
    other = {"allocation": 123, "gpu": 3, "key": "other", "control": "/unrelated"}
    write_json(tmp_path / "assignments.json", [other, old])
    write_json(tmp_path / "fleet.json", {"measurements": [{"key": "task", "seed": 3,
               "entry": entry, "directory": str(directory)}]})
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": "failed"})
    monkeypatch.setattr(fleet_module, "measurement_config", lambda *a, **kwargs: {
        "code_directory": "code-new", "resources": {"port_first": 24128,
        "nccl_port_first": 25128, "lease_serving_ports": True}})
    return lane, config, directory, before, control, other


@pytest.mark.parametrize('interrupted', [False, True])
@pytest.mark.parametrize('preflight', ['default', 'operator', 'rejected', 'conflicting'])
def test_explicit_startup_retry_keeps_identity_and_other_owners(tmp_path, monkeypatch, interrupted, preflight):
    lane, config, directory, before, control, other = retry_fixture(tmp_path, monkeypatch)
    calls = []
    def submit(*args, **kwargs):
        calls.append(args)
        if interrupted:
            raise RuntimeError('controller lost after submission')
        return {"state": "submitted"}
    monkeypatch.setattr(fleet_module, "submit_checkpoint", submit)
    checks = []
    def verify(selected, previous_control):
        assert selected == directory and previous_control == str(control)
        assert not (directory / 'startup_failures').exists()
        checks.append((selected, previous_control))
        if preflight == 'rejected':
            raise ValueError('operator preflight denied')
        fleet_module.verify_unstarted(selected)
    options = {} if preflight == 'default' else {'preflight_check': verify}
    if preflight == 'conflicting':
        options['port_preflight'] = True
    if preflight in {'rejected', 'conflicting'}:
        retained = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
        reason = 'preflight denied' if preflight == 'rejected' else 'exactly one'
        with pytest.raises(ValueError, match=reason):
            fleet_module.retry_startup(tmp_path, 'task', 'retry-1', **options)
        assert calls == [] and len(checks) == int(preflight == 'rejected')
        assert not (directory / 'startup_failures').exists()
        assert {p: p.read_bytes() for p in tmp_path.rglob('*.json')} == retained
        return
    if interrupted:
        with pytest.raises(RuntimeError, match='controller lost'):
            fleet_module.retry_startup(tmp_path, "task", "retry-1", **options)
        assert len(checks) == int(preflight == 'operator')
        restored = fleet_module.reconcile_assignments(tmp_path, study.read(tmp_path / 'fleet.json'),
                                               study.read(tmp_path / 'assignments.json'))
        assert restored[0] == other
        assert restored[1]['control'] == str(directory / 'control/retry-1')
        assert restored[1]['launch_state'] == 'prepared'
        assert len(calls) == 1
        assert study.read(control / 'launch.json') == {'original': True}
        return
    result = fleet_module.retry_startup(tmp_path, "task", "retry-1", **options)
    assert len(checks) == int(preflight == 'operator')
    assert result["control"] == str(directory / "control/retry-1")
    assert len(calls) == 1
    assert study.read(tmp_path / "assignments.json")[0] == other
    assert study.read(control / "launch.json") == {"original": True}
    archive = directory / "startup_failures/retry-1"
    assert (archive / "generation.json").exists()
    assert (archive / "rollout/cleanup.json").exists()
    assert not (directory / "generation.json").exists()
    after = study.read(directory / "manifest.json")
    assert after == {**before, "code": "new-code"}
    resumed_config = study.read(lane / "configuration.json")
    for key in ("attempt", "setting", "model", "inputs"):
        assert resumed_config[key] == config[key]
    with pytest.raises(ValueError):
        fleet_module.retry_startup(tmp_path, "task", "retry-1")
