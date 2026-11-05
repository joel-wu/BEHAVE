"""Portable PPA input freezing, separate from model/cluster dispatch."""

from copy import deepcopy
from pathlib import Path

import pytest

from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.records import RecordError, canonical_json, parse_json
from behave.data.published import load_published_task
from behave.ppa import campaign_inputs as inputs
from behave.ppa.records import release_reference
from test_ppa_campaign import campaign  # noqa: F401


def input_plan(tmp_path, publication, *, pointer=False):
    source = tmp_path / "source"
    source.mkdir()
    files = {
        "release": (canonical_json(publication.ref.as_dict()) if pointer else publication.release.to_bytes()),
        "experiment": b'{"fixture": "measurement"}',
        "retained_pool": b'{"stimuli": []}',
        "judge_policy": b'{"fixture": "development"}',
        "final_judge_policy": b'{"fixture": "validation"}',
        "final_experiment": b'{"fixture": "final-measurement"}',
        "liberty": b"fixture liberty", "sta": b"fixture tool",
        "provider": b'{"credential_path": "/must-not-read/credentials.env"}',
        "prompt": b"fixture prompt",
    }
    for name, data in files.items():
        (source / name).write_bytes(data)
    return {
        "schema": "behave.ppa-campaign/2", "task": publication.task.task_id,
        "objects": str(publication.root),
        **{key: str(source / key) for key in files if key not in {"provider", "prompt"}},
        "methods": [{"name": "fixture", "provider": str(source / "provider"),
                     "prompt": str(source / "prompt"), "behavior_source": "provided"}],
        "replicates": 2, "parallelism": 2, "turns": 40,
        "request_seconds": None, "replicate_seconds": None, "final_seconds": None,
    }


@pytest.mark.parametrize("pointer", [False, True])
def test_identical_freeze_can_repeat_without_rewriting(tmp_path, publication, pointer):
    plan = input_plan(tmp_path, publication, pointer=pointer)
    original = deepcopy(plan)
    root = tmp_path / "frozen"
    frozen = inputs.freeze_inputs(plan, root)
    before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
              for p in (root / "configuration").iterdir()}
    assert inputs.freeze_inputs(plan, root) == frozen
    assert plan == original
    assert before == {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in before}


def test_interrupted_freeze_can_finish_without_overwriting(tmp_path, publication, monkeypatch):
    plan = input_plan(tmp_path, publication)
    root = tmp_path / "frozen"
    read = Path.read_bytes

    def interrupted(path):
        if path == Path(plan["experiment"]):
            raise OSError("fixture interrupted read")
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", interrupted)
        with pytest.raises(OSError, match="interrupted read"):
            inputs.freeze_inputs(plan, root)
    assert (root / "configuration/release.json").is_file()
    frozen = inputs.freeze_inputs(plan, root)
    task = load_published_task(DirectoryReader(frozen["objects"]),
                               release_reference(frozen["release"]), frozen["task"])
    assert task.manifest == publication.task


@pytest.mark.parametrize("pointer", [False, True])
@pytest.mark.parametrize("attachments", [False, True])
def test_freeze_keeps_selected_closure_without_scanning_or_reading_credentials(
        tmp_path, publication, monkeypatch, pointer, attachments):
    if attachments:
        from test_publication_attachments import archived_task

        publication.task, _ = archived_task(publication)
        publication.release, publication.ref = publication.publish()
    plan = input_plan(tmp_path, publication, pointer=pointer)
    unrelated = publication.put(b"another task's data must not be copied")
    original = deepcopy(plan)
    read = Path.read_bytes
    listing = Path.iterdir

    def guarded_read(path):
        assert path != publication.root / unrelated.sha256
        assert path != Path("/must-not-read/credentials.env")
        return read(path)

    def guarded_listing(path):
        assert path != publication.root
        return listing(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", guarded_read)
        patch.setattr(Path, "iterdir", guarded_listing)
        frozen = inputs.freeze_plan(plan, tmp_path / "frozen")
    assert plan == original
    assert frozen["liberty"] == plan["liberty"]
    assert frozen["sta"] == plan["sta"]
    assert not (Path(frozen["objects"]) / unrelated.sha256).exists()
    # The retained publication remains readable with the original store gone.
    publication.root.rename(tmp_path / "unavailable-original")
    task = load_published_task(DirectoryReader(frozen["objects"]),
                               release_reference(frozen["release"]), frozen["task"])
    assert task.manifest == publication.task
    assert task.release == publication.release
    assert task.judge.reference_rtl is not None
    assert len(task.judge.reference_behavior.files) == 2
    assert task.public.files
    assert b"PRIVATE" not in b"".join(f.data for f in task.public.files)
    assert len(task.manifest.archive_files) == (4 if attachments else 0)
    for file in task.manifest.archive_files:
        assert b"PRIVATE" in DirectoryReader(frozen["objects"]).read(file.content)
    inventory = parse_json((tmp_path / "frozen/inputs.json").read_bytes())
    paths = {frozen[k] for k in ("release", "experiment", "retained_pool", "liberty", "sta",
                               "judge_policy", "final_judge_policy", "final_experiment")}
    paths.update(frozen["methods"][0][key] for key in ("provider", "prompt"))
    assert inventory == {p: BlobRef.of(Path(p).read_bytes()).sha256 for p in paths}


@pytest.mark.parametrize("record", ["inputs.json", "plan.json"])
def test_plan_commit_retries_partial_preparation(tmp_path, publication, monkeypatch, record):
    plan = input_plan(tmp_path, publication)
    root = tmp_path / "frozen"
    publish = inputs.publish

    def interrupted(path, value):
        if path.name == record:
            raise OSError("fixture interrupted commit")
        return publish(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(inputs, "publish", interrupted)
        with pytest.raises(OSError, match="interrupted commit"):
            inputs.freeze_plan(plan, root)
    assert not (root / "plan.json").exists()
    frozen = inputs.freeze_plan(plan, root)
    records = {name: (root / name).stat() for name in ("inputs.json", "plan.json")}
    assert inputs.freeze_plan(plan, root) == frozen
    assert all((root / name).stat().st_mtime_ns == stat.st_mtime_ns for name, stat in records.items())
    assert parse_json((root / "plan.json").read_bytes()) == frozen


@pytest.mark.parametrize("changed", ["experiment", "retained_pool", "judge_policy", "final_judge_policy",
                                     "final_experiment", "liberty", "sta", "provider", "prompt"])
def test_changed_input_cannot_replace_committed_plan(tmp_path, publication, changed):
    plan = input_plan(tmp_path, publication)
    root = tmp_path / "frozen"
    frozen = inputs.freeze_plan(plan, root)
    records = {name: (root / name).read_bytes() for name in ("inputs.json", "plan.json")}
    source = plan["methods"][0][changed] if changed in {"provider", "prompt"} else plan[changed]
    Path(source).write_bytes(b"changed source")
    with pytest.raises(ValueError, match="different experiment record"):
        inputs.freeze_plan(plan, root)
    assert all((root / name).read_bytes() == data for name, data in records.items())
    assert parse_json((root / "plan.json").read_bytes()) == frozen


def test_invalid_plan_rejected_before_publication(tmp_path, publication):
    plan = input_plan(tmp_path, publication)
    plan["methods"][0]["name"] = "../../escape"
    root = tmp_path / "frozen"
    with pytest.raises(ValueError, match="method configuration"):
        inputs.freeze_plan(plan, root)
    assert not root.exists()


def test_existing_corrupt_object_is_not_accepted_on_retry(tmp_path, publication):
    plan = input_plan(tmp_path, publication)
    root = tmp_path / "frozen"
    frozen = inputs.freeze_inputs(plan, root)
    target = Path(frozen["objects"]) / publication.task.spec.content.sha256
    target.chmod(0o600)
    target.write_bytes(b"corrupted")
    with pytest.raises(RecordError):
        inputs.freeze_plan(plan, root)
    assert not (root / "plan.json").exists()
    assert target.read_bytes() == b"corrupted"  # Never silently repair retained evidence.
