"""Paired coordination uses real records and opaque native-worker fixtures.

Acquisition and tokenizer outputs here are named fixtures. Separate process and
native dependency lanes exercise those boundaries; these tests claim no learning.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_improvement_round_data import Unavailable, context, package
from test_slime_stage import native_worker
from test_slime_training_plan import inputs

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.data.published import ReleaseManifest
from behave.data.release_records import ReleaseRequest
from behave.improve import learning_round
from behave.improve.learning_round import LearningParent, LearningRuntime, learn_round, load_learning_round
from behave.improve.materials import FrozenMaterials, MaterialRequest
from behave.improve.round_data import load_round_data, prepare_round_data
from behave.train.plan import TrainingManifest
from behave.train.trainer import NativeAnchor
from behave.train.training_data import RawCorpus, RawEncoding, RawSegment


def prepared(tmp_path, monkeypatch, inputs):
    # These admitted packages use an explicitly named fixture judge. Match its
    # ABI in this coordinator-only lane, rather than pretending to run live Sim.
    monkeypatch.setattr("behave.sim.api.COMMAND_VERSION", "fixture")
    monkeypatch.setattr("behave.sim.api.RESULT_VERSION", "fixture")
    configuration, _, _, _ = inputs
    store, library, material_ref, material = context(tmp_path / "source", monkeypatch)
    item = package(tmp_path / "task", monkeypatch, store, library, next(iter(library.units)))
    data_run = JournalDirectory(tmp_path / "data")
    data = prepare_round_data(materials=material_ref,
        publication=ReleaseRequest("round", item.policy_ref, (item.ref,)), reader=store, run=data_run)
    source_store = store
    store = data_run.objects
    context_record = MaterialRequest.from_bytes(read_verified(store, material.request))
    # The fixture material loader does not read the acquisition/checkpoint proof.
    store.publish(source_store, (context_record.checkpoint,))
    release = ReleaseManifest.from_bytes(read_verified(store, data.release))
    manifest = TrainingManifest(data.release, tuple((task.task_id, task.manifest) for task in release.tasks))
    manifest_ref = store.put(manifest.to_bytes())
    tokenizer = store.put((Path(configuration["model_path"]) / "tokenizer.json").read_bytes())
    encoding = RawEncoding(tokenizer, "tokenizers/fixture", 1, 2, 32)
    raw_config = {**configuration, "layout": {**configuration["layout"], "rollout_gpus": 0, "colocate": False},
        "sampler": {"sequences_per_update": 1, "order": "cycle", "seed": "fixture"},
        "native_arguments": ["--bf16", "--optimizer", "adam", "--lr", "1e-6"]}
    monkeypatch.setattr(learning_round, "load_materials",
                        lambda reader, ref: FrozenMaterials.from_bytes(read_verified(reader, ref)))
    monkeypatch.setattr(learning_round, "tokenize_materials", lambda reader, ref, policy:
        RawCorpus(ref, policy, tuple(RawSegment(file, 0, (1, 3, 2)) for file in material.documents)))
    monkeypatch.setattr("behave.train.slime_data.load_raw_corpus",
                        lambda reader, ref: RawCorpus.from_bytes(read_verified(reader, ref)))
    base = MaterialRequest.from_bytes(read_verified(store, material.request)).checkpoint
    runtime = LearningRuntime(configuration, 4, {"PATH": "/usr/bin:/bin"}, None, 5)
    return {"data": data.head, "base_checkpoint": base, "task_passes": ((manifest_ref, 2),),
        "raw_updates": 2, "encoding": encoding,
        "parents": {route: LearningParent(None, tmp_path / "initial") for route in ("task", "raw")},
        "task_runtime": runtime, "raw_runtime": replace(runtime, configuration=raw_config),
        "reader": store, "run": JournalDirectory(tmp_path / "learning-round")}


def worker(monkeypatch):
    calls = []
    def execute(argv, **kwargs):
        plan = kwargs["cwd"]
        configuration = json.loads((plan / "configuration.json").read_text())
        def argument(name):
            return argv[argv.index(name) + 1]
        options = {"plan": plan, "load": Path(argument("--load")), "save": Path(argument("--save")),
                   "updates": int(argument("--num-rollout"))}
        calls.append(plan)
        return native_worker(options, configuration)(argv, **kwargs)
    monkeypatch.setattr("behave.train.slime_stage.run_service", execute)
    return calls


def test_two_routes_commit_and_export_without_duplicate_updates(tmp_path, monkeypatch, inputs):
    options = prepared(tmp_path, monkeypatch, inputs)
    calls = worker(monkeypatch)
    result = learn_round(**options)
    assert result["status"] == "completed" and len(calls) == 2
    assert result["parents"]["task"]["checkpoint"] != result["parents"]["raw"]["checkpoint"]
    assert learn_round(**{**options, "reader": Unavailable()}) == result
    assert len(calls) == 2
    head, closure = options["run"].seal("paired_learning")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(options["run"].objects, closure)
    assert load_learning_round(exported, head) == result
    with pytest.raises(RecordError, match="replay differs"):
        learn_round(**{**options, "raw_updates": 3})


def test_live_learning_handoff_does_not_repeat_publication_or_final_audits(tmp_path, monkeypatch, inputs):
    options = prepared(tmp_path, monkeypatch, inputs)
    calls = worker(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(learning_round, "load_round_data", lambda *args: pytest.fail("repeated admission audit"))
        patch.setattr(learning_round, "load_learning_round", lambda *args: pytest.fail("repeated final audit"))
        result = learn_round(**options)
        assert learn_round(**{**options, "reader": Unavailable()}) == result
    assert len(calls) == 2
    head, closure = options["run"].seal("paired_learning")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish_owned(options["run"].objects, closure)
    assert load_learning_round(exported, head) == result


def test_task_curriculum_reuses_shared_preparation_without_changing_raw(tmp_path, monkeypatch, inputs):
    from test_training_task_pool import POLICY

    options = prepared(tmp_path, monkeypatch, inputs)
    runtime = options["task_runtime"]
    options["task_runtime"] = replace(runtime, configuration={**runtime.configuration,
        "sampler": {"tasks_per_update": 1, "order": "cycle", "seed": "fixture"},
        "curriculum": POLICY.as_dict()})
    calls = worker(monkeypatch)
    assert learn_round(**options)["status"] == "completed"
    task, raw = [json.loads((plan / "binding.json").read_text()) for plan in calls]
    assert task["curriculum"] == POLICY.as_dict()
    assert "curriculum" not in raw


def test_analyst_notes_do_not_enter_shared_training_plan_or_resume(tmp_path, monkeypatch, inputs):
    from behave.train.plan import TrainingPlan

    options = prepared(tmp_path, monkeypatch, inputs)
    note = b"Check signed widths at arithmetic boundaries; verify reset behavior."
    reference = options["reader"].put(note)
    assert "solver_experience" not in options["task_runtime"].record()
    calls = worker(monkeypatch)
    result = learn_round(**options)
    assert len(calls) == 2
    task, raw = calls
    binding = json.loads((task / "binding.json").read_text())
    store = DirectoryStore(Path(binding["objects"]))
    plan = TrainingPlan.from_bytes(store.read(BlobRef.from_dict(binding["plan"])), store)
    assert all(job.solver_experience is None for job in plan.execution.jobs)
    assert "solver_experience" not in json.loads((task / "preparation_request.json").read_text())
    assert "solver_experience" not in json.loads((raw / "preparation_request.json").read_text())
    assert learn_round(**{**options, "reader": Unavailable()}) == result
    assert len(calls) == 2
    head, closure = options["run"].seal("paired_learning")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(options["run"].objects, closure)
    assert not (exported.directory / reference.sha256).exists()
    assert load_learning_round(exported, head) == result


def test_next_round_advances_both_parents_without_resetting_weights(tmp_path, monkeypatch, inputs):
    options = prepared(tmp_path, monkeypatch, inputs)
    calls = worker(monkeypatch)
    first = learn_round(**options)
    objects = options["run"].objects
    data = load_round_data(objects, options["data"])
    material = FrozenMaterials.from_bytes(read_verified(objects, data.materials))
    request = MaterialRequest.from_bytes(read_verified(objects, material.request))
    parents = {route: LearningParent.from_record(value) for route, value in first["parents"].items()}
    request = replace(request, round_id="round-2", checkpoint=parents["task"].checkpoint)
    material_ref = objects.put(replace(material, request=objects.put(request.to_bytes())).to_bytes())
    # Explicitly no new tasks in this round. Replay remains a supplied approved
    # manifest, while Raw still consumes the newly frozen common material.
    data_run = JournalDirectory(tmp_path / "next-data")
    next_data = prepare_round_data(materials=material_ref, publication=None, reader=objects, run=data_run)
    _, closure = data_run.seal("round_data")
    objects.publish(data_run.objects, closure)
    next_run = JournalDirectory(tmp_path / "next-learning")
    result = learn_round(**{**options, "data": next_data.head, "parents": parents,
                           "reader": objects, "run": next_run})
    assert len(calls) == 4
    for route in ("task", "raw"):
        checkpoint = BlobRef.from_dict(result["parents"][route]["checkpoint"])
        anchor = NativeAnchor.load(next_run.objects, checkpoint)
        assert anchor.route == route and anchor.step == 3
        assert checkpoint != parents[route].checkpoint
    assert learn_round(**{**options, "data": next_data.head, "parents": parents,
                          "reader": Unavailable(), "run": next_run}) == result
    assert len(calls) == 4


def test_resume_after_task_commit_does_not_train_task_twice(tmp_path, monkeypatch, inputs):
    options = prepared(tmp_path, monkeypatch, inputs)
    calls = worker(monkeypatch)
    original = learning_round.run_learning
    class Disconnected(BaseException):
        pass
    def stop(**kwargs):
        if kwargs["plan"].name == "pass_1":
            raise Disconnected()
        return original(**kwargs)
    monkeypatch.setattr(learning_round, "run_learning", stop)
    with pytest.raises(Disconnected):
        learn_round(**options)
    assert len(calls) == 1
    monkeypatch.setattr(learning_round, "run_learning", original)
    assert learn_round(**{**options, "reader": Unavailable()})["status"] == "completed"
    assert len(calls) == 2


def test_explicit_replay_pass_continues_task_cursor_and_raw_is_separate(tmp_path, monkeypatch, inputs):
    options = prepared(tmp_path, monkeypatch, inputs)
    manifest, _ = options["task_passes"][0]
    options["task_passes"] = ((manifest, 1), (manifest, 2))
    calls = worker(monkeypatch)
    result = learn_round(**options)
    assert len(calls) == 3 and result["passes"][1]["parent"] == result["passes"][0]["result"]
    assert result["passes"][2]["parent"]["checkpoint"] is None
    assert learn_round(**{**options, "reader": Unavailable()}) == result and len(calls) == 3


def test_zero_updates_carry_parents_without_fake_learning(tmp_path, monkeypatch, inputs):
    options = prepared(tmp_path, monkeypatch, inputs)
    options.update(task_passes=(), raw_updates=0)
    calls = worker(monkeypatch)
    result = learn_round(**options)
    assert result["status"] == "completed" and not calls
    assert all(parent["checkpoint"] is None for parent in result["parents"].values())
    assert result["passes"] == [{"route": "raw", "parent": options["parents"]["raw"].record(),
                                  "head": None, "result": options["parents"]["raw"].record()}]
