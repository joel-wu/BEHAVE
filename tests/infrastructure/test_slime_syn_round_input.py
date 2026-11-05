"""Completed Syn learning uses the same native continuation, not a new trainer.

Native worker tensors and model exports are explicit fixtures. No live model or
optimizer is exercised by these operator tests.
"""

from pathlib import Path

import pytest
from test_improvement_learning_round import prepared, worker
from test_slime_training_plan import inputs  # noqa: F401

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.improve.experience import experience_files, experience_snapshot
from behave.improve.learning_round import LearningParent, learn_round
from behave.train.checkpoint_input import prepare_round


@pytest.fixture
def completed_round(tmp_path, monkeypatch, inputs):  # noqa: F811
    previous = tmp_path / "previous"
    options = prepared(tmp_path / "fixture", monkeypatch, inputs)
    options["run"] = JournalDirectory(previous / "round/learning")
    calls = worker(monkeypatch)
    result = learn_round(**options)
    assert len(calls) == 2
    head, _ = options["run"].seal("paired_learning")
    plan = options["run"].directory / "plans/pass_0"
    model = plan / "hf_1"
    model.mkdir()
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (model / name).write_text("{}")
    (model / "weight.safetensors").write_bytes(b"fixture export, not learned weights")
    root = JournalDirectory(previous / "round")
    files = experience_files({"construction": "Check declared output shapes.", "solver": "Check reset."})
    for file in files:
        root.objects.put(file.data)
    memory = root.objects.put(experience_snapshot(files).to_bytes())
    history = root.objects.put(canonical_json({"schema": "behave.construction-history/1", "rounds": []}))
    outcome = {"status": "completed", "round_id": "fixture-first", "learning": head.as_dict(),
        "parents": result["parents"], "training_manifest": options["task_passes"][0][0].as_dict(),
        "experience": memory.as_dict(), "construction_history": history.as_dict()}
    (previous / "result.json").write_bytes(canonical_json(outcome))
    (previous / "configuration.json").write_bytes(canonical_json(inputs[0]))
    return previous, outcome, plan, calls


def test_syn_handoff_preserves_both_parents_pool_and_cursor_without_weight_reads(completed_round, monkeypatch):
    previous, result, plan, calls = completed_round
    original = Path.open

    def no_weight_reads(path, *args, **kwargs):
        assert path.suffix not in {".safetensors", ".pt"}, "metadata handoff must not reread tensors"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_weight_reads)
    handoff = prepare_round(previous)
    assert handoff["step"] == 1
    assert handoff["checkpoint"] == result["parents"]["task"]["checkpoint"]
    assert handoff["checkpoint_directory"] == result["parents"]["task"]["directory"]
    assert handoff["training_manifest"] == result["training_manifest"]
    assert handoff["binding"] == str(plan / "binding.json")
    assert handoff["feedback_round"] == str(plan / "rounds/round_00000001")
    assert handoff["model_path"] == str(plan / "hf_1")
    assert {name: LearningParent.from_record(value).record()
            for name, value in handoff["parents"].items()} == result["parents"]
    assert prepare_round(previous) == handoff and len(calls) == 2


@pytest.mark.parametrize("change", ["unfinished", "generation_only", "parent", "pool", "missing_hf"])
def test_incomplete_or_different_syn_result_cannot_become_a_learning_parent(completed_round, change):
    previous, result, plan, _ = completed_round
    if change == "unfinished":
        result["status"] = "unfinished"
    elif change == "generation_only":
        result["learning"] = None
    elif change == "parent":
        result["parents"]["task"]["checkpoint"] = BlobRef.of(b"other checkpoint").as_dict()
    elif change == "pool":
        result["training_manifest"] = BlobRef.of(b"another pool").as_dict()
    else:
        # Preserve the fixture output while making its expected path unavailable.
        (plan / "hf_1").rename(plan / "retained_hf_1")
    (previous / "result.json").write_bytes(canonical_json(result))
    with pytest.raises((ValueError, FileNotFoundError)):
        prepare_round(previous)
