import json

from behave.core.artifacts import BlobRef
from behave.core.storage import DirectoryStore
from behave.train import slime_rollout as slime_rollout_module
from tools.slime_replay_inputs import restore


def test_replay_restore_keeps_all_training_records_and_original_archive(tmp_path, monkeypatch):
    source, destination = tmp_path / "local-plan", tmp_path / "shared-plan"
    objects = source / "rounds/round_00000000/objects"
    objects.mkdir(parents=True)

    def save(value):
        data = json.dumps(value).encode()
        ref = BlobRef.of(data).as_dict()
        (objects / ref["sha256"]).write_bytes(data)
        return ref

    outcomes = [save({"score": value}) for value in (0., 1., None)]
    rows = [{"examples": {"result": ref, "reward": reward}}
            for ref, reward in zip(outcomes, (0., 1., None), strict=True)]
    approved = BlobRef.of(b"manifest")
    plan_ref = save({"manifest": approved.as_dict(), "group_ids": [None] * len(rows)})
    for row in rows:
        row["group_id"] = None
    report = save({"candidates": rows, "plan": plan_ref})
    head = save({"kind": "training_finished", "payload": {"result": report}})
    (objects / "unused-http").write_bytes(b"retained in full archive")
    receipt_path = source / "rounds/native/request_00000000/response.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps({"head": head, "objects": "/tmp/old/plan/rounds/round_00000000/objects"}))
    (source / "binding.json").write_text(json.dumps({"run_directory": "/tmp/old/plan/rounds"}))
    for name in ("configuration.json", "training_selection.json", "source_split.json"):
        (source / name).write_text("{}")
    (source / "inputs").mkdir()
    (source / "inputs/task").write_text("unchanged task")
    archive = tmp_path / "retained.tar.gz"
    archive.write_bytes(b"opaque full archive")
    result = restore(source, destination, archive)
    assert result["candidates"] == 3 and result["scored_candidates"] == 2
    assert result["copied_report_objects"] == 6
    target = destination / "rounds/round_00000000/objects"
    assert {path.name for path in target.iterdir()} == {ref["sha256"] for ref in [head, report, plan_ref, *outcomes]}
    for path in target.iterdir():
        assert path.read_bytes() == (objects / path.name).read_bytes()
    assert (destination / receipt_path.relative_to(source)).read_bytes() == receipt_path.read_bytes()
    assert archive.read_bytes() == b"opaque full archive"
    source.rename(tmp_path / "retired-local-plan")
    assert (target / report["sha256"]).is_file()

    # Exercise the real consumer's metadata reads after the local copy is gone.
    # Packing itself has separate token/credit tests, including no-result cases.
    def read_outcomes(report, reader, **kwargs):
        return [json.loads(reader.read(BlobRef.from_dict(row["examples"]["result"])))
                for row in report["candidates"]]

    monkeypatch.setattr(slime_rollout_module, "pack_report", read_outcomes)
    restored = slime_rollout_module.load_recorded_batch(
        DirectoryStore(target), BlobRef.from_dict(head), approved_manifest=approved,
        expected_plan=BlobRef.from_dict(plan_ref), recipe=None,
        first_rollout=0, first_sample=0, workers=1, start_method="spawn", directory=None)
    assert restored == [{"score": value} for value in (0., 1., None)]
