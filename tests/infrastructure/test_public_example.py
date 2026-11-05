"""The README example prepares a task without the benchmark corpus."""

import importlib
import json
from pathlib import Path

import pytest

from behave.core.artifacts import BlobRef
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.data.spec_format import validate


@pytest.fixture
def example(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "examples"))
    return importlib.import_module("prepare_task")


def test_public_example_spec(example):
    validate(example.SPEC)


def test_dataset_job_reports_missing_review_tools_before_submit(tmp_path, monkeypatch, capsys):
    from tools import dataset_validation_job

    monkeypatch.setattr(dataset_validation_job, "REPO", tmp_path)
    monkeypatch.setattr(dataset_validation_job, "submit", lambda *a, **kw: pytest.fail("must not submit"))
    with pytest.raises(SystemExit) as error:
        dataset_validation_job.main(["submit", str(tmp_path / "job"), "--job", "1",
                                     "--cpus", "1", "--wall-seconds", "60", "--", "--tasks", "tasks.txt"])
    assert error.value.code == 2
    assert "requires the dataset review tools" in capsys.readouterr().err
    assert not (tmp_path / "job").exists()


def test_public_example_prepares_without_corpus(tmp_path, monkeypatch, example):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BEHAVE_DATA_DIR", str(tmp_path / "missing-data"))
    output = tmp_path / "release"
    result = example.prepare_example(output)
    assert result["tasks"] == 1
    reader = DirectoryStore(output / "objects")
    ref = BlobRef.from_dict(json.loads((output / "release.json").read_text()))
    task = load_published_task(reader, ref, "passthrough")
    assert task.judge.reference_behavior.artifact.entry_symbol == "Identity"
    assert {file.reference.path for file in task.public.files} == {"spec.md", "contract.json"}
    assert not (tmp_path / "missing-data").exists()
    with pytest.raises(FileExistsError):
        example.prepare_example(output)
