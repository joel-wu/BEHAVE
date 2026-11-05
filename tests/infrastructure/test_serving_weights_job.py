"""Detached-tool weight handoff and cleanup; no live GPU/model claim."""

import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_serving import load_tool, spec
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401
from test_weight_publication import publish, trained  # noqa: F401

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.serving import ServingFailure
from test_native_chat_tokens import native  # noqa: F401


def weight_input(tmp_path, trained, monkeypatch):  # noqa: F811
    job = load_tool(monkeypatch, "model_service_job")
    inline_worker(job, monkeypatch)
    from model_weight_input import WeightInput

    objects = DirectoryStore(tmp_path / "published")
    publication = publish(trained, objects)
    selected = WeightInput(str(objects.directory), publication, trained[1], trained[2], "serving")
    path = tmp_path / "weights.json"
    path.write_bytes(selected.to_bytes())
    return job, selected, path


def inline_worker(job, monkeypatch):
    """Lifecycle fault injection only; real interpreter isolation has its own test."""
    def execute(command, *, check):
        assert check is False
        source = Path(command[-2])
        options = parse_json(Path(command[-1]).read_bytes())
        return SimpleNamespace(returncode=job._run_local(source, source.parent, **options))

    monkeypatch.setattr(job, "subprocess", SimpleNamespace(run=execute))


def test_weight_input_uses_exact_publication_and_is_pinned_before_run(tmp_path, trained, monkeypatch):  # noqa: F811
    job, selected, path = weight_input(tmp_path, trained, monkeypatch)
    from model_weight_input import WeightInput

    scratch = tmp_path / "configured-local-scratch"
    scratch.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(scratch))
    ref = BlobRef.of(path.read_bytes())
    assert WeightInput.read(path, ref) == selected
    assert "max_bytes" not in parse_json(selected.to_bytes())
    bound = selected.stage(spec())
    try:
        assert Path(bound.model_path).parent == scratch
        assert bound.model_revision == selected.publication.sha256
        assert Path(bound.model_path, "model.safetensors").read_bytes() == trained[4]["model.safetensors"]
        captured = job.native_endpoint(LocalChatEndpoint(
            "http://127.0.0.1:31000/v1/chat/completions", bound.served_model, bound.identity), bound,
            DirectoryStore(tmp_path / "tokens"), checkpoint=selected.publication)
        assert captured.tokens.checkpoint == selected.publication
    finally:
        shutil.rmtree(bound.model_path)
    path.write_bytes(replace(selected, component="model").to_bytes())
    with pytest.raises(ValueError, match="changed after submission"):
        WeightInput.read(path, ref)


@pytest.mark.parametrize("case", ["field", "relative", "component", "endpoints", "invalid-json", "fifo"])
def test_weight_input_refuses_invalid_configuration(tmp_path, trained, monkeypatch, case):  # noqa: F811
    _, selected, path = weight_input(tmp_path, trained, monkeypatch)
    from model_weight_input import WeightInput

    row = parse_json(selected.to_bytes())
    if case == "fifo":
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(ValueError, match="regular file"):
            WeightInput.read(fifo)
        return
    if case == "field":
        row["latest"] = True
    elif case == "relative":
        row["objects"] = "relative-cas"
    elif case == "component":
        row["component"] = ""
    elif case == "endpoints":
        row["endpoints"] = {}
    data = b"not JSON" if case == "invalid-json" else canonical_json(row)
    path.write_bytes(data)
    with pytest.raises((ValueError, TypeError)):
        WeightInput.read(path)


@pytest.mark.parametrize("runtime,gpus", [("sglang", None), ("sglang", 1), ("vllm", 2)])
@pytest.mark.parametrize("uncapped", [False, True])
def test_submission_freezes_the_explicit_input_and_reserves_the_bound_gpus(tmp_path, trained, monkeypatch, runtime, gpus, uncapped):  # noqa: F811
    job, selected, path = weight_input(tmp_path, trained, monkeypatch)
    from model_task_smoke import SMOKE_CONFIG, smoke_config
    from model_weight_input import WeightInput

    from behave.runtime import jobs
    from behave.runtime import snapshots as snapshots_module

    binding = Path(job.__file__).resolve().parents[1] / job.BINDING
    default_smoke = Path(job.__file__).resolve().parents[1] / SMOKE_CONFIG
    settings = smoke_config()
    settings["job"].update(cpus=7, schedule_wait_seconds=9, preparation_seconds=200, archive_seconds=45)
    smoke_path = tmp_path / "operator-smoke.json"
    smoke_path.write_bytes(canonical_json(settings))
    selected_binding = None
    if gpus is not None or uncapped:
        selected_binding = tmp_path / "operator-binding.json"
        values = parse_json(binding.read_bytes())
        values["shared"].update(tensor_parallel=gpus or 8, context_length=1024, startup_seconds=60, lifetime_seconds=120)
        if uncapped:
            values["shared"].update(startup_seconds=None, lifetime_seconds=None, cleanup_seconds=None, log_bytes=None)
        selected_binding.write_bytes(canonical_json(values))
    assert "tools/model_weight_input.py" in snapshots_module.FILES
    monkeypatch.setattr(job, "REPO", tmp_path)
    monkeypatch.setattr(job.sys, "path", list(job.sys.path))
    work = tmp_path / "configured-results"
    monkeypatch.setenv("BEHAVE_WORK_DIR", str(work))

    def freeze(repo, snapshot, **kwargs):
        (snapshot / job.BINDING).parent.mkdir(parents=True)
        shutil.copyfile(binding, snapshot / job.BINDING)
        shutil.copyfile(default_smoke, snapshot / SMOKE_CONFIG)
        return {"sha256": "frozen-fixture"}

    def submit(directory, argv, **kwargs):
        return {"directory": directory, "argv": argv, **kwargs}

    monkeypatch.setattr(snapshots_module, "freeze", freeze)
    monkeypatch.setattr(jobs, "submit", submit)
    before_path = list(job.sys.path)
    launch = job.submit(123, runtime, weights=path, task_rollout=True, binding=selected_binding,
                        smoke_settings=smoke_path if gpus is not None else None)
    assert job.sys.path == before_path
    argv = launch["argv"]
    assert launch['cwd'] == tmp_path
    expected_settings = settings if gpus is not None else smoke_config()
    assert launch["directory"].is_relative_to(work)
    assert parse_json(argv[argv.index("--smoke-settings") + 1].encode()) == expected_settings
    assert launch["metadata"]["smoke_settings"] == expected_settings
    assert f"--cpus-per-task={expected_settings['job']['cpus']}" in argv
    assert f"--immediate={expected_settings['job']['schedule_wait_seconds']}" in argv
    smoke_path.write_text("changed after submission")
    assert parse_json(argv[argv.index("--smoke-settings") + 1].encode()) == expected_settings
    frozen = Path(argv[argv.index("--weight-input") + 1])
    ref = BlobRef.from_dict(launch["metadata"]["weight_input"])
    assert frozen != path and frozen.stat().st_mode & 0o222 == 0
    assert WeightInput.read(frozen, ref) == selected
    assert argv[argv.index("--weight-sha256") + 1] == ref.sha256
    assert argv[argv.index("--weight-size") + 1] == str(ref.size_bytes)
    assert f"--gpus-per-task={gpus or 8}" in argv and "--overlap" not in argv
    bound_path = Path(argv[argv.index("--binding-input") + 1])
    bound_ref = BlobRef.from_dict(launch["metadata"]["binding"])
    _, actual, _ = job.read_binding(bound_path, runtime, bound_ref)
    assert actual.identity == launch["metadata"]["serving"]
    assert actual.tensor_parallel == launch["metadata"]["gpus"] == (gpus or 8)
    job_settings = expected_settings["job"]
    wall = None if uncapped else (actual.lifetime_seconds + actual.cleanup_seconds + job_settings["preparation_seconds"]
                                 + job_settings["archive_seconds"] + job_settings["schedule_wait_seconds"])
    assert launch["wall_seconds"] == wall
    if uncapped:
        assert not any(arg.startswith("--time=") for arg in argv)
    else:
        assert f"--time={(wall + 59) // 60}" in argv
    assert bound_path.stat().st_mode & 0o222 == 0
    assert argv[argv.index("--binding-sha256") + 1] == bound_ref.sha256
    assert argv[argv.index("--binding-size") + 1] == str(bound_ref.size_bytes)
    if selected_binding is not None:
        selected_binding.write_bytes(b"changed after submission")
        assert job.read_binding(bound_path, runtime, bound_ref)[1] == actual
    assert "--native-tokens" in argv and "--task-rollout" in argv
    path.write_bytes(replace(selected, component="model").to_bytes())
    assert WeightInput.read(frozen, ref) == selected


@pytest.mark.parametrize("changed", ["input", "checkpoint"])
def test_worker_rejects_wrong_input_or_lineage_before_inspection_and_launch(tmp_path, trained, monkeypatch, changed):  # noqa: F811
    job, selected, path = weight_input(tmp_path, trained, monkeypatch)

    from behave import serving
    from behave.serving import environment

    binding = Path(job.__file__).resolve().parents[1] / job.BINDING
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(job, "mkdtemp", lambda **kwargs: str(scratch))

    def stage(snapshot, source):
        (source / job.BINDING).parent.mkdir(parents=True)
        shutil.copyfile(binding, source / job.BINDING)
        return {"sha256": "frozen-fixture"}

    def must_not_start(*args, **kwargs):
        pytest.fail("invalid weights reached inspection or serving instead of being refused")

    monkeypatch.setattr('behave.runtime.snapshots.stage', stage)
    monkeypatch.setattr(environment, "environment", must_not_start)
    monkeypatch.setattr(serving, "ServingSession", must_not_start)
    reference = BlobRef.of(path.read_bytes())
    path.write_bytes(replace(selected, checkpoint=BlobRef.of(b"other committed head")).to_bytes())
    if changed == "checkpoint":
        reference = BlobRef.of(path.read_bytes())
    output = tmp_path / "result"
    assert job.run(tmp_path / "snapshot", output, "sglang", weight_input=path,
                   weight_reference=reference.as_dict()) == 1
    report = parse_json((output / "result.json").read_bytes())
    assert not report["passed"] and report["failure_phase"] == "weight-staging"
    assert "installation" not in report and not scratch.exists()


@pytest.mark.parametrize("failure", [None, "installation", "starting", "sampling", "tool-smoke", "score", "score-error",
                                      "server-death", "cleanup", "cleanup-unknown", "archive"])
@pytest.mark.parametrize("custom_binding", [False, True])
def test_worker_stages_before_launch_and_retains_files_until_confirmed_cleanup(
    tmp_path, trained, monkeypatch, failure, custom_binding, capsys,  # noqa: F811
):
    job, selected, path = weight_input(tmp_path, trained, monkeypatch)
    import model_task_smoke

    from behave import serving
    from behave.serving import environment

    scratch_root = tmp_path / "configured-node-local"
    scratch_root.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(scratch_root))
    source_binding = Path(job.__file__).resolve().parents[1] / job.BINDING
    selected_binding = tmp_path / "operator-binding.json"
    values = parse_json(source_binding.read_bytes())
    if custom_binding:
        values["shared"].update(tensor_parallel=2, context_length=1024, model_path="/must/not/fall/back/to/cache")
    selected_binding.write_bytes(canonical_json(values))
    sessions, stages, observed, preparation_deadlines = [], [], [], []
    settings = model_task_smoke.smoke_config()
    settings["tool_parameters"].update(temperature=.4, seed=51, chat_template_kwargs={"enable_thinking": True})
    settings["scored_parameters"].update(temperature=.5, seed=52, chat_template_kwargs={"enable_thinking": True})
    if failure == "sampling":
        settings["sampling_check"] = {"seeds": [17, 17], "parameters": {"temperature": 1}}
        monkeypatch.setattr(job, "sampling_smoke", lambda *a: {"passed": False})
    from model_weight_input import WeightInput

    original_stage = WeightInput.stage

    def record_stage(self, s):
        bound = original_stage(self, s)
        stages.append(Path(bound.model_path))
        return bound

    monkeypatch.setattr(WeightInput, "stage", record_stage)

    def stage(_snapshot, source):
        (source / "infra/model_runtimes").mkdir(parents=True)
        shutil.copyfile(source_binding, source / job.BINDING)
        return {"sha256": "frozen-fixture"}

    monkeypatch.setattr('behave.runtime.snapshots.stage', stage)
    monkeypatch.setattr(environment, "environment", lambda *a, **kw: {})
    def headers(*args, deadline, **kwargs):
        assert preparation_deadlines == [deadline]
        return ()

    monkeypatch.setattr(environment, "headers", headers)

    def inspect(s, scratch, env, **kwargs):
        preparation_deadlines.append(kwargs["deadline"])
        assert Path(s.model_path) == stages[0] and s.model_revision == selected.publication.sha256
        assert s.tensor_parallel == (2 if custom_binding else 8)
        if failure == "installation":
            raise ValueError("installation fixture")
        return {"fixture": True}

    monkeypatch.setattr(environment, "inspect_installation", inspect)

    class Session:
        def __init__(self, s, *, scratch, environment, records):
            assert s.tensor_parallel == (2 if custom_binding else 8)
            self.spec, self.scratch = s, scratch
            sessions.append(self)
            records.mkdir()

        def start(self):
            if failure == "starting":
                raise ServingFailure("fixture-start-failed")
            return LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions",
                                     self.spec.served_model, self.spec.identity)

        def check_alive(self):
            assert stages[0].is_dir()
            if failure == "server-death" and observed == ["tools", "score"]:
                raise ServingFailure("fixture-server-death")

        def close(self):
            assert self.scratch.is_dir() and stages[0].is_dir()
            if failure == "cleanup":
                raise ServingFailure("cleanup-incomplete")
            return {"cleanup_complete": failure != "cleanup-unknown", "service_status": "cancelled"}

    monkeypatch.setattr(serving, "ServingSession", Session)

    def smoke(endpoint, records, **kwargs):
        assert endpoint.parameters == settings["tool_parameters"]
        assert kwargs["settings"] == settings
        assert kwargs["checkpoint"] == selected.publication
        assert kwargs["native_spec"].model_revision == selected.publication.sha256
        if failure == "tool-smoke":
            raise ValueError("smoke fixture")
        observed.append("tools")
        return {"passed": True}

    def score(endpoint, records, **kwargs):
        assert endpoint.parameters == settings["scored_parameters"]
        assert kwargs["settings"] == settings
        assert endpoint.tokens.checkpoint == selected.publication
        provenance = DirectoryStore(sessions[0].scratch / "evidence/provenance")
        template = parse_json(provenance.read(endpoint.tokens.template))
        assert template["kwargs"] == settings["scored_parameters"]["chat_template_kwargs"]
        observed.append("score")
        if failure == "score-error":
            raise ValueError("scoring fixture")
        return {"passed": failure != "score", "reward": 0 if failure == "score" else 1}

    monkeypatch.setattr(job, "tool_smoke", smoke)
    monkeypatch.setattr(model_task_smoke, "scored_rollout", score)
    output = tmp_path / "evidence"
    kwargs = {"native_tokens": True, "task_rollout": True, "weight_input": path, "settings": settings,
              "weight_reference": BlobRef.of(path.read_bytes()).as_dict()}
    if custom_binding:
        kwargs.update(binding_input=selected_binding,
                      binding_reference=BlobRef.of(selected_binding.read_bytes()).as_dict())
    if failure == "archive":
        def no_copy(*a, **kw):
            raise OSError("archive unavailable")
        monkeypatch.setattr(job.shutil, "copytree", no_copy)
        with pytest.raises(OSError, match="archive unavailable"):
            job.run(tmp_path / "snapshot", output, "sglang", **kwargs)
        retained = sessions[0].scratch
        try:
            assert retained.parent == scratch_root and stages[0].parent == scratch_root
            assert retained.is_dir() and not stages[0].exists()
            assert (retained / "evidence/result.json").is_file()
            events = [parse_json(line.encode()) for line in capsys.readouterr().out.splitlines()]
            retained_event, = [row for row in events if row['phase'] == 'evidence-retained']
            assert retained_event["path"] == str(retained / "evidence")
            assert events[-1] == {'schema': 'behave.model-service-progress/1',
                'phase': 'worker-scratch-retained', 'path': str(retained), 'destination': str(output)}
        finally:
            # Fixture-owned local evidence; no actual server or failed NFS mount.
            if retained.exists():
                shutil.rmtree(retained)
        return
    result = job.run(tmp_path / "snapshot", output, "sglang", **kwargs)
    report = parse_json((output / "result.json").read_bytes())
    assert result == (0 if failure is None else 1)
    assert report["passed"] == (failure is None)
    assert report["smoke_settings"] == settings
    assert report["weights"]["publication"] == selected.publication.as_dict()
    archived_binding = (output / "binding.json").read_bytes()
    assert BlobRef.of(archived_binding).as_dict() == report["binding"]
    assert parse_json(archived_binding) == values
    if failure is None or failure in {"score", "score-error", "server-death", "cleanup", "cleanup-unknown"}:
        assert observed == ["tools", "score"]
    if failure in {"score-error", "server-death"}:
        assert report["failure_phase"] == "scored-rollout"
    if failure == "sampling":
        assert report["failure_phase"] == "sampling-check" and not observed
        assert report["sampling"] == {"passed": False}
    if failure in {"cleanup", "cleanup-unknown"}:
        # There was no real process; the fixture itself owns final disposal.
        retained = [Path(report["retained_scratch"]), Path(report["retained_weights"])]
        assert all(p.is_dir() for p in retained)
        for directory in retained:
            shutil.rmtree(directory)
    else:
        assert "retained_scratch" not in report and not stages[0].exists()
        assert not sessions or not sessions[0].scratch.exists()
