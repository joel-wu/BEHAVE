"""Preparation resumes one frozen choice; partial files never authorize a run."""

import json
from dataclasses import replace

import pytest
from test_eval_followup import FAILURE, followup_inputs, inventory

from behave.agent.spending import SpendPolicy
from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.records import RecordError
from behave.data.published import load_published_task
from behave.eval import followup as followup_module
from behave.runtime.records import write_json
from behave.task_execution.judge_policy import JudgePolicy


@pytest.fixture
def inputs(tmp_path, publication, monkeypatch):
    policy = JudgePolicy("original-command", "original-authority", search_seconds=7)
    publication.task = replace(publication.task, judge_policy=publication.put(policy.to_bytes()))
    publication.release, publication.ref = publication.publish()
    source = followup_inputs(tmp_path, publication)

    def forbidden(*args, **kwargs):
        pytest.fail("preparation must not execute a model, judge or process")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("behave.sim.api.prepare_runtime", forbidden)
    return source, tmp_path / "followup"


def read(path):
    return json.loads(path.read_text())


@pytest.mark.parametrize("boundary", ["receipt", "inputs", "selection", "settings", "committed"])
def test_interrupted_preparation_reuses_pinned_selection_settings_and_policy(
        inputs, publication, monkeypatch, boundary):
    source, output = inputs
    publish, task = followup_module.publish_json, followup_module._publish_task

    def interrupted_record(path, value):
        if boundary == "settings" and path.name == "settings.json":
            raise OSError("fixture interrupted preparation")
        publish(path, value)
        if path.name == {"receipt": "followup_preparation.json", "selection": "selection.json",
                         "committed": "settings.json"}.get(boundary):
            raise OSError("fixture interrupted preparation")

    def interrupted_task(*args):
        task(*args)
        if boundary == "inputs":
            raise OSError("fixture interrupted preparation")

    with monkeypatch.context() as fault:
        fault.setattr(followup_module, "publish_json", interrupted_record)
        fault.setattr(followup_module, "_publish_task", interrupted_task)
        with pytest.raises(OSError, match="interrupted preparation"):
            followup_module.prepare(output, [source], reroll=True)
    planned = read(output / "followup_preparation.json")
    assert (output / "settings.json").exists() is (boundary == "committed")
    assert (output / "selection.json").exists() is (boundary in {"selection", "settings", "committed"})
    before = inventory(output)
    existing = {path: (path.stat().st_ino, path.stat().st_mtime_ns)
                for name in ("followup_preparation.json", "selection.json", "settings.json")
                if (path := output / name).exists()}

    # New source evidence and defaults cannot change a previously pinned retry.
    changed = read(source / "settings.json")
    changed["generation_parameters"]["seed"] = 42
    changed["tasks"] = []
    write_json(source / "settings.json", changed)
    write_json(source / "rejudged.json", {publication.task.task_id: {"not": "the pinned outcome"}})

    def no_reselection(*args, **kwargs):
        pytest.fail("resume must not reselect tasks or recompute current defaults")

    monkeypatch.setattr(followup_module, "_preparation_plan", no_reselection)
    monkeypatch.setattr(followup_module, "load_outcomes", no_reselection)
    monkeypatch.setattr("behave.sim.api.COMMAND_VERSION", "later-command")
    monkeypatch.setattr("behave.sim.api.RESULT_VERSION", "later-authority")
    if boundary == "committed":
        monkeypatch.setattr(followup_module, "_publish_preparation", no_reselection)
    assert followup_module.prepare(output, [source], reroll=True) == planned["selection"]
    assert read(output / "settings.json") == planned["settings"]
    assert all((path.stat().st_ino, path.stat().st_mtime_ns) == identity for path, identity in existing.items())
    assert all((output / name).read_bytes() == value for name, value in before.items())
    settings = planned["settings"]
    published = load_published_task(DirectoryReader(settings["task_objects"]),
                                    BlobRef.from_dict(settings["release"]), publication.task.task_id)
    policy = JudgePolicy.from_bytes(published.judge.judge_policy)
    assert policy.command_version != "later-command" and policy.authority_version != "later-authority"
    assert policy.search_seconds == 0 and settings["generation_parameters"]["seed"] == 17


@pytest.mark.parametrize("change", ["source", "reroll", "failure_codes"])
def test_partial_preparation_rejects_another_request_without_touching_it(inputs, monkeypatch, change):
    source, output = inputs
    monkeypatch.setattr(followup_module, "_publish_preparation", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        followup_module.prepare(output, [source], failure_codes=[FAILURE])
    before = inventory(output)
    args = {"sources": [source], "failure_codes": [FAILURE]}
    if change == "source":
        args["sources"] = [source / "different"]
    elif change == "reroll":
        args.update(reroll=True, failure_codes=[])
    else:
        args["failure_codes"] = ["different"]
    with pytest.raises(ValueError, match="different follow-up"):
        followup_module.prepare(output, **args)
    assert inventory(output) == before


def test_complete_preparation_does_not_require_the_original_sources(inputs, publication, monkeypatch):
    source, output = inputs
    selection = followup_module.prepare(output, [source], reroll=True)
    source.rename(source.with_name("moved-source"))
    publication.root.rename(publication.root.with_name("moved-inputs"))
    before = inventory(output)
    assert followup_module.prepare(output, [source], reroll=True) == selection
    assert inventory(output) == before


@pytest.mark.parametrize("complete", [False, True])
def test_legacy_preparation_is_not_silently_reconstructed(inputs, monkeypatch, complete):
    source, output = inputs
    selection = followup_module.prepare(output, [source], reroll=True)
    (output / "followup_preparation.json").rename(output.parent / "retained-preparation.json")
    if not complete:
        (output / "settings.json").rename(output.parent / "retained-settings.json")
    source.rename(source.with_name("moved-source"))
    before = inventory(output)
    if complete:
        assert followup_module.prepare(output, [source], reroll=True) == selection
    else:
        with pytest.raises(ValueError, match="incomplete legacy follow-up"):
            followup_module.prepare(output, [source], reroll=True)
    assert inventory(output) == before


def test_unowned_output_is_not_adopted(inputs):
    source, output = inputs
    write_json(output / "settings.json", {"belongs": "to another experiment"})
    before = (output / "settings.json").read_bytes()
    with pytest.raises(ValueError, match="unowned follow-up"):
        followup_module.prepare(output, [source], reroll=True)
    assert (output / "settings.json").read_bytes() == before
    assert not (output / "followup_preparation.json").exists()


@pytest.mark.parametrize("name", ["settings", "selection"])
def test_changed_committed_record_is_not_overwritten(inputs, name):
    source, output = inputs
    followup_module.prepare(output, [source], reroll=True)
    write_json(output / (name + ".json"), {"changed": True})
    before = inventory(output)
    with pytest.raises(ValueError, match="different experiment record"):
        followup_module.prepare(output, [source], reroll=True)
    assert inventory(output) == before


def test_invalid_input_cannot_become_a_runnable_preparation(inputs, publication, monkeypatch):
    source, output = inputs
    with monkeypatch.context() as fault:
        fault.setattr(followup_module, "_publish_preparation", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(OSError):
            followup_module.prepare(output, [source], reroll=True)
    payload = publication.root / publication.task.reference_behavior.files[0].content.sha256
    payload.write_bytes(b"corrupted reference")
    with pytest.raises(RecordError):
        followup_module.prepare(output, [source], reroll=True)
    assert not (output / "selection.json").exists() and not (output / "settings.json").exists()


def test_settings_publish_only_after_all_inputs_and_selection(inputs, publication, monkeypatch):
    source, output = inputs
    publish = followup_module.publish_json
    commits = []

    def check(path, value):
        if path.name == "settings.json":
            assert read(output / "selection.json")["rollout"] == value["tasks"]
            for task_id in value["tasks"]:
                task = load_published_task(DirectoryReader(value["task_objects"]),
                                           BlobRef.from_dict(value["release"]), task_id)
                assert task.manifest.reference_behavior == publication.task.reference_behavior
            commits.append(path)
        publish(path, value)

    monkeypatch.setattr(followup_module, "publish_json", check)
    followup_module.prepare(output, [source], reroll=True)
    assert commits == [output / "settings.json"]


def test_concurrent_preparation_is_excluded(inputs, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from behave.agent.attempt import AttemptInterrupted

    source, output = inputs
    entered, finish = Event(), Event()
    publish = followup_module._publish_preparation

    def paused(*args):
        entered.set()
        assert finish.wait(10)
        return publish(*args)

    monkeypatch.setattr(followup_module, "_publish_preparation", paused)
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(followup_module.prepare, output, [source], reroll=True)
        try:
            assert entered.wait(10)
            with pytest.raises(AttemptInterrupted, match="run-already-owned"):
                followup_module.prepare(output, [source], reroll=True)
            assert not (output / "settings.json").exists()
        finally:
            finish.set()
        assert future.result()["rollout"]


@pytest.mark.parametrize("change", [None, "limit", "directory", "account", "omitted"])
def test_supplemental_spending_is_pinned_before_inputs(inputs, monkeypatch, change):
    source, output = inputs
    spending = SpendPolicy(str(output.parent / "account"), "supplement", "USD", "50", 131072)
    with monkeypatch.context() as fault:
        fault.setattr(followup_module, "_publish_preparation", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(OSError):
            followup_module.prepare(output, [source], failure_codes=[FAILURE], spending=spending)
    plan = read(output / "followup_preparation.json")
    assert plan["selection"]["spending"] == {
        "budget_usd": "50", "spending_directory": spending.directory, "spending_account": "supplement"}
    before = inventory(output)
    request = spending
    if change == "omitted":
        request = None
    elif change is not None:
        request = replace(spending, **{change: {"limit": "100", "directory": str(output.parent / "other"),
                                               "account": "other"}[change]})
    if change is not None:
        with pytest.raises(ValueError, match="different spending request"):
            followup_module.prepare(output, [source], failure_codes=[FAILURE], spending=request)
        assert inventory(output) == before
        return
    assert followup_module.prepare(output, [source], failure_codes=[FAILURE], spending=request) == plan["selection"]
    settings = read(output / "settings.json")
    assert settings == plan["settings"]
    original = read(source / "settings.json")
    for key in ("attempt", "generation_parameters", "release", "score_on_submit"):
        assert settings[key] == original[key]
    assert not (output.parent / "account").exists()  # No funding or account IO during planning.
    before = inventory(output)
    followup_module.prepare(output, [source], failure_codes=[FAILURE], spending=request)
    assert inventory(output) == before


@pytest.mark.parametrize("valid", [False, True])
def test_completed_legacy_supplement_uses_existing_binding_without_rewrite(inputs, valid):
    source, output = inputs
    selection = followup_module.prepare(output, [source], failure_codes=[FAILURE])
    (output / "followup_preparation.json").rename(output.parent / "retained-preparation.json")
    spending = SpendPolicy(str(output.parent / "account"), "supplement", "USD", "50", 131072)
    settings = read(output / "settings.json")
    settings.update(budget_usd=spending.limit, spending_directory=spending.directory,
                    spending_account=spending.account if valid else "other")
    write_json(output / "settings.json", settings)
    before = inventory(output)
    if valid:
        assert followup_module.prepare(output, [source], failure_codes=[FAILURE], spending=spending) == selection
    else:
        with pytest.raises(ValueError, match="different spending request"):
            followup_module.prepare(output, [source], failure_codes=[FAILURE], spending=spending)
    assert inventory(output) == before


@pytest.mark.parametrize("currency", [None, "EUR"])
def test_invalid_spending_binding_does_not_create_output(inputs, currency):
    source, output = inputs
    spending = {} if currency is None else SpendPolicy(str(output.parent / "account"), "a", currency, "1", 1)
    with pytest.raises(ValueError, match="explicit USD SpendPolicy"):
        followup_module.prepare(output, [source], spending=spending)
    assert not output.exists()
