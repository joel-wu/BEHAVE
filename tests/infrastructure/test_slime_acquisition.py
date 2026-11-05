"""Real native handoff/recovery, with fixture inference and optimizer writes."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from syn_runtime_fixture import runtime as fixture_runtime
from test_slime_task_pool import completed_checkpoint, mock_scoring, source_at
from test_slime_training_plan import inputs  # noqa: F401

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.train import slime_acquisition as slime_acquisition_module
from behave.train.slime_data import ScoredDataSource
from behave.train.trainer import NativeAnchor


def configured(tmp_path, publication, inputs):  # noqa: F811 -- imported pytest fixture
    study = tmp_path / "study.json"
    study.write_bytes(canonical_json({"stages": [{"after_updates": 1, "runtime": "acquisition.json"}]}))
    profile = ({**inputs[0], "self_improvement": {"study": str(study)}}, *inputs[1:])
    source = source_at(tmp_path / "initial", publication, profile)
    enlarged = source_at(tmp_path / "enlarged", publication, inputs, tasks="abcdef")
    return source, enlarged


def fake_acquisition(monkeypatch, source, enlarged, *, fail_after_commit=False):
    calls = []

    def acquire(argv, **kwargs):
        request = parse_json(Path(argv[-3]).read_bytes())
        endpoint = parse_json(Path(argv[-2]).read_bytes())
        calls.append(request)
        anchor = BlobRef.from_dict(request["parent_checkpoint"])
        assert NativeAnchor.load(source.run.objects, anchor).step == 0
        assert request["binding"]["manifest"] == source.binding["manifest"]
        assert endpoint["worker_urls"]
        result = {"schema": "behave.native-acquisition/1", "completed_updates": 1,
            "parent_checkpoint": anchor.as_dict(), "training_binding": str(enlarged.path),
            "training_manifest": enlarged.binding["manifest"], "objects": enlarged.binding["objects"],
            "experience": None, "construction_history": None}
        Path(argv[-1]).write_bytes(canonical_json(result))
        return SimpleNamespace(status="exited", returncode=1 if fail_after_commit else 0,
            stdout=b"fixture generation", stderr=b"", elapsed_seconds=.1,
            cleanup_state=SimpleNamespace(quiescent=True))

    monkeypatch.setattr(slime_acquisition_module, "run_service", acquire)
    return calls


def test_boundary_adds_tasks_keeps_cooldowns_and_cold_restores(tmp_path, publication, inputs, monkeypatch):  # noqa: F811 -- imported pytest fixture
    source, enlarged = configured(tmp_path, publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    calls = fake_acquisition(monkeypatch, source, enlarged)
    source.load(-1)
    source.generate(0)
    completed_checkpoint(source, 0)
    before = source._pool_state(source.journal.load())
    scoring["version"] += 1
    source.generate(1)
    assert len(calls) == 1
    assert [task for task, _ in source._pool_tasks] == list("abcdef")
    after = source._pool_state(source.journal.load())
    assert source._pool_definitions_published
    # An ordinary subsequent update does not reload/re-publish the same task
    # definitions just because its preceding receipt also names acquisition.
    slime_acquisition_module.restore(source, source.journal.load())
    assert source._pool_definitions_published
    assert after["tasks"]["a"] == before["tasks"]["a"]
    assert after["tasks"]["b"] == before["tasks"]["b"]
    assert after["tasks"]["e"]["last_selected"] == -1
    assert {r["task_id"] for r in scoring["selection"]} == {"c", "d"}
    assert source.identity == BlobRef.of(source.path.read_bytes())
    completed_checkpoint(source, 1)
    cold = ScoredDataSource(source.args)
    cold.load(1)
    assert cold.binding["manifest"] == enlarged.binding["manifest"]
    assert cold._pool_state(cold.journal.load()) == after
    next_scoring = mock_scoring(monkeypatch, cold)
    cold.generate(2)
    assert {r["task_id"] for r in next_scoring["selection"]} == {"e", "f"}
    assert len(calls) == 1


def test_acquisition_committed_before_next_update_survives_interruption(
        tmp_path, publication, inputs, monkeypatch):  # noqa: F811 -- imported pytest fixture
    source, enlarged = configured(tmp_path, publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    calls = fake_acquisition(monkeypatch, source, enlarged, fail_after_commit=True)
    source.generate(0)
    completed_checkpoint(source, 0)
    scoring["version"] += 1
    with pytest.raises(Exception, match="retained acquisition"):
        source.generate(1)
    assert source.journal.load()[-1].kind == "checkpoint_saved"
    cold = ScoredDataSource(source.args)
    cold.load(0)
    mock_scoring(monkeypatch, cold)
    cold.generate(1)
    assert cold.binding["manifest"] == enlarged.binding["manifest"]
    assert len(calls) == 1  # Reuse the saved transition, not another Analyst run.


def test_frozen_legacy_request_is_not_upgraded_during_resume(tmp_path, publication, inputs, monkeypatch):  # noqa: F811 -- imported pytest fixture
    source, enlarged = configured(tmp_path, publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    source.generate(0)
    completed_checkpoint(source, 0)
    retained = []
    install = slime_acquisition_module.install_exclusive

    def install_legacy(directory, name, payload):
        if name == "request.json":
            request = parse_json(payload)
            request.pop("feedback_window", None)
            payload = canonical_json(request)
        return install(directory, name, payload)

    def legacy_interruption(argv, **kwargs):
        path = Path(argv[-3])
        payload = path.read_bytes()
        assert "feedback_window" not in parse_json(payload)
        retained.append((path, payload))
        return SimpleNamespace(status="exited", returncode=1, stdout=b"", stderr=b"fixture interruption",
            elapsed_seconds=0, cleanup_state=SimpleNamespace(quiescent=True))

    scoring["version"] += 1
    # Freeze a legacy request at creation, preserving production immutability.
    with monkeypatch.context() as legacy:
        legacy.setattr(slime_acquisition_module, "install_exclusive", install_legacy)
        legacy.setattr(slime_acquisition_module, "run_service", legacy_interruption)
        with pytest.raises(RecordError, match="retained acquisition"):
            source.generate(1)
    cold = ScoredDataSource(source.args)
    cold.load(0)
    mock_scoring(monkeypatch, cold)
    calls = fake_acquisition(monkeypatch, cold, enlarged)
    monkeypatch.setattr(slime_acquisition_module, "feedback_window", lambda *a, **kw:
        pytest.fail("must not reinterpret frozen legacy inputs"))
    cold.generate(1)
    assert len(calls) == 1 and "feedback_window" not in calls[0]
    assert retained[0][0].read_bytes() == retained[0][1]


def test_optimizer_rollback_reuses_scored_new_pool_once(tmp_path, publication, inputs, monkeypatch):  # noqa: F811 -- imported pytest fixture
    source, enlarged = configured(tmp_path, publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    calls = fake_acquisition(monkeypatch, source, enlarged)
    source.generate(0)
    completed_checkpoint(source, 0)
    scoring["version"] += 1
    source.generate(1)
    expected = source._pool_state(source.journal.load())
    source.native.abandon(reason="fixture interrupted optimizer", workers_stopped=True)
    cold = ScoredDataSource(source.args)
    cold.load(0)
    cold.generate(1)
    assert cold._pool_state(cold.journal.load()) == expected
    assert cold.binding["manifest"] == enlarged.binding["manifest"]
    assert len(calls) == 1 and len(scoring["requests"]) == 2


def test_two_acquisitions_keep_prior_handoff_and_restore_cumulative_pool(
        tmp_path, publication, inputs, monkeypatch):  # noqa: F811 -- imported pytest fixture
    source, enlarged = configured(tmp_path, publication, inputs)
    latest = source_at(tmp_path / "latest", publication, inputs, tasks="abcdefgh")
    Path(source.binding["self_improvement"]["study"]).write_bytes(canonical_json({
        "stages": [{"after_updates": step, "runtime": f"acquisition_{step}.json"}
                   for step in (1, 2)]}))
    calls = []

    def acquire(argv, **kwargs):
        request = parse_json(Path(argv[-3]).read_bytes())
        calls.append(request)
        step = request["completed_updates"]
        assert [item["update"] for item in request["feedback_window"]] == [step]
        target = enlarged if step == 1 else latest
        if step == 1:
            assert request["previous"] is None
        else:
            assert request["previous"]["completed_updates"] == 1
            assert request["previous"]["training_manifest"] == enlarged.binding["manifest"]
            assert request["previous"]["experience"] == BlobRef.of(b"Analyst memory 1").as_dict()
        result = {"schema": "behave.native-acquisition/1", "completed_updates": step,
            "parent_checkpoint": request["parent_checkpoint"], "training_binding": str(target.path),
            "training_manifest": target.binding["manifest"], "objects": target.binding["objects"],
            "experience": BlobRef.of(f"Analyst memory {step}".encode()).as_dict(),
            "construction_history": None}
        Path(argv[-1]).write_bytes(canonical_json(result))
        return SimpleNamespace(status="exited", returncode=0, stdout=b"", stderr=b"",
                               elapsed_seconds=.1, cleanup_state=SimpleNamespace(quiescent=True))

    monkeypatch.setattr(slime_acquisition_module, "run_service", acquire)
    scoring = mock_scoring(monkeypatch, source)
    for step in range(3):
        scoring["version"] = step + 1
        source.generate(step)
        completed_checkpoint(source, step)
    before = source._pool_state(source.journal.load())
    assert set(before["tasks"]) == set("abcdefgh")
    assert before["tasks"]["a"]["cooldown_until"] == 11
    assert before["tasks"]["a"]["observed_groups"] == 1
    assert source.binding["manifest"] == latest.binding["manifest"]

    cold = ScoredDataSource(source.args)
    cold.load(2)
    assert cold._pool_state(cold.journal.load()) == before
    assert cold.binding["manifest"] == latest.binding["manifest"]
    restored = parse_json(cold.run.objects.read(cold._acquisition_state))
    assert restored["experience"] == BlobRef.of(b"Analyst memory 2").as_dict()
    next_scoring = mock_scoring(monkeypatch, cold)
    cold.generate(3)
    assert {row["task_id"] for row in next_scoring["selection"]} == {"g", "h"}
    assert len(calls) == 2  # No repeated construction when resuming after both boundaries.


def test_feedback_window_survives_midwindow_job_restart(tmp_path, publication, inputs, monkeypatch):  # noqa: F811 -- imported pytest fixture
    source = source_at(tmp_path / "first", publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    for step in range(2):
        scoring["version"] = step + 1
        source.generate(step)
        head = completed_checkpoint(source, step)
    cold = source_at(tmp_path / "second", publication, inputs, parent=head, parent_reader=source.run.objects)
    cold.args.load = source.args.save
    cold.load(1)
    mock_scoring(monkeypatch, cold)
    cold.generate(2)
    completed_checkpoint(cold, 2)
    window = slime_acquisition_module.feedback_window(cold, cold.journal.load(), after_updates=0, completed_updates=3)
    assert [item["update"] for item in window] == [1, 2, 3]
    assert len({item["receipt"]["objects"] for item in window}) == 2  # fixture has one store per job
    assert [item["update"] for item in slime_acquisition_module.feedback_window(
        cold, cold.journal.load(), after_updates=2, completed_updates=3)] == [3]
    with pytest.raises(Exception, match="missing committed feedback"):
        slime_acquisition_module.feedback_window(cold, cold.journal.load(), after_updates=0, completed_updates=4)


@pytest.mark.parametrize("saved_after_acquisition", [False, True])
def test_new_job_restores_new_tasks_or_uncommitted_handoff_without_reacquisition(
        tmp_path, publication, inputs, monkeypatch, saved_after_acquisition):  # noqa: F811 -- imported pytest fixture
    source, enlarged = configured(tmp_path, publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    calls = fake_acquisition(monkeypatch, source, enlarged,
                             fail_after_commit=not saved_after_acquisition)
    source.generate(0)
    head = completed_checkpoint(source, 0)
    scoring["version"] += 1
    if saved_after_acquisition:
        source.generate(1)
        head = completed_checkpoint(source, 1)
        from behave.core.storage import DirectoryStore
        from tools.slime_training_job import backup_task_pool

        job = tmp_path / "job_layout"
        (job / "artifacts").mkdir(parents=True)
        (job / "artifacts/plan").symlink_to(source.path.parent, target_is_directory=True)
        destination = DirectoryStore(tmp_path / "backup/objects")
        pool = backup_task_pool(job, source.journal.load(), tmp_path / "backup", destination)
        assert set(pool["tasks"]) == set("abcdef")
        for row in pool["tasks"].values():
            destination.read(BlobRef.from_dict(row["revision"]))
    else:
        with pytest.raises(Exception, match="retained acquisition"):
            source.generate(1)
    step = int(saved_after_acquisition)
    profile = ({**inputs[0], "self_improvement": source.binding["self_improvement"]}, *inputs[1:])
    cold = source_at(tmp_path / "new_job", publication, profile,
                     parent=head, parent_reader=source.run.objects)
    cold.args.load = source.args.save
    cold.load(step)
    mock_scoring(monkeypatch, cold)
    cold.generate(step + 1)
    assert cold.binding["manifest"] == enlarged.binding["manifest"]
    assert len(calls) == 1
    assert cold._pool_state(cold.journal.load())["tasks"]["a"]["cooldown_until"] == (
        source._pool_state(source.journal.load())["tasks"]["a"]["cooldown_until"])


def test_same_profile_connection_can_move_ports_but_not_weights(monkeypatch):
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.improve import training_connection

    endpoint = LocalChatEndpoint("http://127.0.0.1:9000/v1/chat/completions", "fixture", "runtime",
                                 parameters={"temperature": 1})
    profile = {"model": endpoint.model, "runtime_revision": endpoint.runtime_revision,
               "parameters": {"temperature": 1}}
    approved = {"profile": profile, "checkpoint": BlobRef.of(b"weights").as_dict()}
    request = SimpleNamespace(model_policy={"provider": "stable", "model": "fixture"})
    # A tiny dataclass supplies the same immutable policy field as ModelRequest.
    from dataclasses import dataclass

    @dataclass
    class Request:
        model_policy: dict
    request = Request(request.model_policy)
    seen = []
    monkeypatch.setattr(training_connection, "exchange", lambda ep, req: seen.append((ep, req)))
    for port in (9000, 9100):
        ep = replace(endpoint, url=f"http://127.0.0.1:{port}/v1/chat/completions")
        connection = {"checkpoint": approved["checkpoint"], "endpoint": ep.as_dict()}
        training_connection.connected_exchange(connection, approved, request)
        assert seen[-1][1].model_policy["provider"] == ep.identity
    with pytest.raises(ValueError, match="another training checkpoint"):
        training_connection.connected_exchange({**connection, "checkpoint": BlobRef.of(b"other").as_dict()},
                                               approved, request)
    assert request.model_policy["provider"] == "stable"


def test_training_owned_chat_counts_and_generates_on_worker_not_router():
    from test_chat_transport import reply, send, server

    from behave.agent.model import ModelFailure, ModelRequest
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.improve.training_connection import connected_exchange

    def router(handler, requests, stopped):
        send(handler, {"error": "router does not expose tokenize"}, status=404)

    def engine(handler, requests, stopped):
        send(handler, {"count": 10} if handler.path == "/tokenize" else reply())

    with server(router, token_count=None) as (router_url, routed), \
            server(engine, token_count=None) as (worker_url, observed):
        endpoint = LocalChatEndpoint(router_url, "fixture", "test-server/1",
                                     worker_urls=(worker_url,), routing="attempt")
        checkpoint = BlobRef.of(b"weights").as_dict()
        approved = {"checkpoint": checkpoint, "profile": {
            "model": endpoint.model, "runtime_revision": endpoint.runtime_revision, "parameters": {}}}
        request = ModelRequest("analyst:0:0", 0, 0, ({"role": "user", "content": "go"},), (),
                               {"provider": "stable", "model": "fixture"}, 5, context_tokens=131072)
        connection = {"checkpoint": checkpoint, "endpoint": endpoint.as_dict()}
        result = connected_exchange(connection, approved, request)
        assert not isinstance(result, ModelFailure)
        assert [path for path, _, _ in observed] == ["/tokenize", "/v1/chat/completions"]
        assert observed[-1][2]["max_tokens"] == 131072 - 10
        assert not routed
        assert request.model_policy["provider"] == "stable"


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "pending", "failed"])
@pytest.mark.parametrize("windowed", [False, True])
def test_control_handoff_reuses_runtime_and_retains_generation_before_rebinding(
        tmp_path, publication, inputs, monkeypatch, outcome, windowed):  # noqa: F811 -- imported pytest fixture
    from behave.core.storage import DirectoryStore
    from behave.improve import native_acquisition
    from behave.train.plan import TrainingPlan

    source, enlarged = configured(tmp_path, publication, inputs)
    study = tmp_path / "study"
    study.mkdir()
    runtime = fixture_runtime(inputs[0])
    runtime.update(require_license_metadata=False,
                   generation_target={"admitted_tasks": 20, "proposal_tasks": 30, "max_batches": 2},
                   guides={"construction": "construction.md", "acquisition": "acquisition.md"})
    for name in runtime["guides"].values():
        (study / name).write_text("Fixture instructions")
    (study / "acquisition.json").write_bytes(canonical_json(runtime))
    (study / "native_configuration.json").write_bytes(canonical_json(inputs[0]))
    (study / "library").mkdir()
    (study / "library/selection.json").write_bytes(canonical_json({"library": BlobRef.of(b"library").as_dict()}))
    output = tmp_path / "boundary"
    output.mkdir()
    anchor = BlobRef.of(b"checkpoint")
    request = {"study": str(study / "study.json"), "stage": {"runtime": "acquisition.json"},
        "binding": source.binding, "parent_checkpoint": anchor.as_dict(), "completed_updates": 1,
        "native_directory": str(source.args.save), "native_objects": str(source.run.objects.directory),
        "feedback_receipt": {"objects": str(tmp_path / "feedback_source/objects")}, "previous": None}
    if windowed:
        request["feedback_window"] = [{"update": 1, "receipt": request["feedback_receipt"]}]
    request_path, endpoint_path, response_path = (output / name for name in
                                                ("request.json", "endpoint.json", "response.json"))
    request_path.write_bytes(canonical_json(request))
    from behave.agent.providers.local_chat import LocalChatEndpoint

    endpoint = replace(LocalChatEndpoint.from_dict(source.binding["endpoint"]), worker_urls=(
        "http://127.0.0.1:15000/v1/chat/completions", "http://127.0.0.1:15002/v1/chat/completions"))
    endpoint_path.write_bytes(canonical_json(endpoint.as_dict()))
    feedback = {"request": BlobRef.of(b"existing scored feedback").as_dict(),
                "objects": str(tmp_path / "feedback_source/objects"), "source_objects": []}
    prepare_calls = []
    def prepare_feedback(*args):
        prepare_calls.append(args)
        if windowed:
            assert args[0] == ((1, tmp_path / "feedback_source"),)
        return feedback
    monkeypatch.setattr(native_acquisition, "prepare_window" if windowed else "prepare_feedback", prepare_feedback)
    monkeypatch.setattr(native_acquisition, "prepare_feedback" if windowed else "prepare_window",
                        lambda *a: pytest.fail("incorrect feedback preparation path"))
    calls = []

    def check_connection():
        retained = LocalChatEndpoint.from_dict(parse_json((output / "connection.json").read_bytes())["endpoint"])
        assert retained.worker_urls == endpoint.worker_urls
        assert retained.tokens is None

    if outcome == "cancelled":
        # Enter the actual generation coordinator, then stop before model work.
        # A stubbed run_round previously hid an invalid unused Raw parent path.
        with pytest.raises(RecordError, match="saved stage"):
            native_acquisition.execute(request_path, endpoint_path, response_path, cancelled=lambda: True)
        assert len(prepare_calls) == 1
        check_connection()
        from behave.agent.run_store import JournalDirectory

        run = JournalDirectory(output / "round")
        events = run.journal("round").load()
        assert [event.kind for event in events] == ["round_requested"]
        reference = BlobRef.from_dict(thaw_json(events[0].payload["request"]))
        started = parse_json(run.objects.read(reference))
        assert started["parents"]["raw"] == {"checkpoint": None,
            "directory": str(Path(inputs[0]["model_path"]).resolve())}
        assert started["parents"]["task"]["checkpoint"] == anchor.as_dict()
        assert not response_path.exists()
        return

    def generation(**kwargs):
        check_connection()
        calls.append(kwargs)
        plan = kwargs["runtime"]
        assert plan.keep_serving and plan.generation_only
        assert plan.acquired_updates == plan.raw_updates == 0
        assert plan.model_policy["checkpoint"] == anchor.as_dict()
        assert kwargs["parents"]["raw"].record()["directory"] == str(Path(inputs[0]["model_path"]).resolve())
        assert kwargs["feedback_from"].as_dict() == feedback["request"]
        assert all(job.solver_experience is None for job in plan.diagnosis.jobs)
        assert plan.construction.synthesis.plan.rtl is None
        if outcome != "completed":
            return {"status": outcome, "stage": "construction"}
        run = kwargs["run"]
        journal = run.journal("round")
        if not journal.load():
            store = DirectoryStore(enlarged.binding["objects"])
            refs = tuple(BlobRef.of(path.read_bytes()) for path in store.directory.iterdir())
            run.objects.publish_owned(store, refs)
            journal.append((), "round_settled", {"status": "completed"})
        return {"status": "completed", "training_manifest": enlarged.binding["manifest"],
                "experience": None, "construction_history": None, "generation": {"admitted_tasks": 2}}

    monkeypatch.setattr(native_acquisition, "run_round", generation)
    if outcome != "completed":
        monkeypatch.setattr(native_acquisition, "prepare_task_plan",
                            lambda *a, **kw: pytest.fail("unfinished acquisition reached training preparation"))
        with pytest.raises(RecordError, match="saved stage: construction") as caught:
            native_acquisition.execute(request_path, endpoint_path, response_path)
        assert caught.value.code == "acquisition-incomplete"
        assert len(calls) == len(prepare_calls) == 1
        assert not response_path.exists()
        assert not (output / "generation.json").exists()
        assert not (output / "training_plan").exists()
        return
    prepare = native_acquisition.prepare_task_plan
    monkeypatch.setattr(native_acquisition, "prepare_task_plan",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fixture preparation interrupted")))
    with pytest.raises(RuntimeError, match="preparation interrupted"):
        native_acquisition.execute(request_path, endpoint_path, response_path)
    assert (output / "generation.json").is_file() and not response_path.exists()
    monkeypatch.setattr(native_acquisition, "prepare_task_plan", prepare)
    state = native_acquisition.execute(request_path, endpoint_path, response_path)
    assert len(prepare_calls) == 2
    assert state == parse_json(response_path.read_bytes())
    assert state["task_count"] == 6 and state["parent_checkpoint"] == anchor.as_dict()
    assert calls[0]["runtime"] == calls[1]["runtime"]
    binding = parse_json(Path(state["training_binding"]).read_bytes())
    objects = DirectoryStore(binding["objects"])
    plan = TrainingPlan.from_bytes(objects.read(BlobRef.from_dict(binding["plan"])), objects)
    assert set(plan.group_ids) == set("abcdef")
    assert len(plan.execution.jobs) == 6 * inputs[0]["samples_per_task"]
    assert all(job.solver_experience is None for job in plan.execution.jobs)


def test_connection_bootstrap_uses_current_control_python_without_changing_identity(tmp_path):
    import subprocess
    import sys

    from behave.improve.training_connection import model_command

    connection = tmp_path / "connection.json"
    command = model_command(connection, {"model": "fixture"}, BlobRef.of(b"weights").as_dict())
    # Test the real two-process bootstrap, without starting a model or importing
    # GPU libraries. Only the final broker module is a small subprocess fixture.
    for name in ("original", "recovered"):
        package = tmp_path / name
        module = package / "behave/improve/training_connection.py"
        module.parent.mkdir(parents=True)
        (module.parent / "__init__.py").write_text("")
        (module.parent.parent / "__init__.py").write_text("")
        module.write_text("import sys\ndef main():\n    print(sys.argv[1])\n")
        connection.write_bytes(canonical_json({"control_python": sys.executable, "control_package": str(package)}))
        result = subprocess.run(command.argv, capture_output=True, check=True, timeout=10)
        assert result.stdout.decode().strip() == str(connection)
        assert command == model_command(connection, {"model": "fixture"}, BlobRef.of(b"weights").as_dict())


@pytest.mark.parametrize("shared_library", [False, True])
def test_control_handoff_runs_real_controller_and_native_plan_preparation(
        tmp_path, publication, inputs, monkeypatch, shared_library):  # noqa: F811 -- imported pytest fixture
    """Only sampling/optimizer work is a fixture; the full CPU handoff is real."""
    from test_improvement_feedback import report_at
    from test_improvement_library import prepared as source_library

    from behave.agent.run_store import JournalDirectory
    from behave.core.storage import DirectoryStore
    from behave.improve import generation_batch, native_acquisition
    from behave.improve import round as flow
    from behave.improve.library import import_library
    from behave.train.plan import TrainingPlan

    source, _ = configured(tmp_path, publication, inputs)
    mock_scoring(monkeypatch, source)
    source.generate(0)
    anchor = completed_checkpoint(source, 0)
    seed_objects = DirectoryStore(source.binding["objects"])
    seed_plan = TrainingPlan.from_bytes(seed_objects.read(BlobRef.from_dict(source.binding["plan"])), seed_objects)
    # report_at runs its scripted solver in-process, without a model relay.
    seed_plan = replace(seed_plan, execution=replace(seed_plan.execution, model_admission=None))
    feedback_root = tmp_path / "recorded_feedback"
    feedback_objects, report = report_at(feedback_root, publication, seed_plan.execution.jobs[0],
        ("pass", "fault", "pass") * 4, plan=seed_plan.execution)
    plan_ref = feedback_objects.put(seed_plan.to_bytes())
    result_ref = feedback_objects.put(canonical_json({"plan": plan_ref.as_dict(),
                                                      "execution_head": report.head.as_dict()}))
    JournalDirectory(feedback_root).journal("training").append((), "training_finished",
        {"result": result_ref.as_dict()}, (result_ref, plan_ref))

    study = tmp_path / "real_study"
    study.mkdir()
    runtime = fixture_runtime(inputs[0])
    # This fixture deliberately supplies no Analyst templates below; bounded
    # per-Analyst assignment is covered separately with actual templates.
    runtime.pop("acquisition_limits", None)
    runtime.update(require_license_metadata=False,
        generation_target={"admitted_tasks": 20, "proposal_tasks": 30, "max_batches": 2},
        guides={"construction": "construction.md", "acquisition": "acquisition.md"})
    for name in runtime["guides"].values():
        (study / name).write_text("Fixture guidance")
    (study / "acquisition.json").write_bytes(canonical_json(runtime))
    (study / "native_configuration.json").write_bytes(canonical_json(inputs[0]))
    root, _, catalog, _ = source_library(tmp_path / "source_library")
    library = import_library(root, catalog, DirectoryStore(study / "library/objects"))
    (study / "library/selection.json").write_bytes(canonical_json({"library": library.as_dict()}))
    if shared_library:
        owner = tmp_path / "shared-library"
        (study / "library").rename(owner)
        (study / "library").symlink_to(owner, target_is_directory=True)
    output = tmp_path / "real_boundary"
    output.mkdir()
    request_path, endpoint_path, response_path = (output / name for name in
                                                 ("request.json", "endpoint.json", "response.json"))
    request_path.write_bytes(canonical_json({"study": str(study / "study.json"),
        "stage": {"runtime": "acquisition.json"}, "binding": source.binding,
        "parent_checkpoint": anchor.as_dict(), "completed_updates": 1,
        "native_directory": str(source.args.save), "native_objects": str(source.run.objects.directory),
        "feedback_receipt": {"objects": str(feedback_objects.directory)}, "previous": None}))
    endpoint_path.write_bytes(canonical_json(source.binding["endpoint"]))
    # Zero proposed tasks exercises both replenishment batches and the unchanged
    # pool path without inventing a generated task or starting a model.
    monkeypatch.setattr(generation_batch, "assigned_acquisitions", lambda *args: ())
    monkeypatch.setattr(flow, "run_batch", lambda **kwargs: pytest.fail("duplicate diagnostic rollout"))
    monkeypatch.setattr(flow, "learn_round", lambda **kwargs: pytest.fail("nested trainer"))
    state = native_acquisition.execute(request_path, endpoint_path, response_path)
    assert state["task_count"] == 4 and state["generation"]["admitted"] == 0
    assert len(state["generation"]["batches"]) == 2
    assert (output / "generation.json").is_file() and response_path.is_file()
    binding = parse_json(Path(state["training_binding"]).read_bytes())
    objects = DirectoryStore(binding["objects"])
    plan = TrainingPlan.from_bytes(objects.read(BlobRef.from_dict(binding["plan"])), objects)
    assert all(job.solver_experience is None for job in plan.execution.jobs)
    assert native_acquisition.execute(request_path, endpoint_path, response_path) == state
