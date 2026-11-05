"""Pure tests for the common population-record boundary."""

import json

import pytest

from tools.measurement_records import framed_sha256, write_json_exclusive

from .test_wave1_closure import _tool_module


def test_framed_identity_binds_full_logical_paths_presence_and_bytes():
    base = [("suite/a/spec.md", b"ab"), ("suite/a/task.toml", None)]
    digest = framed_sha256(base)

    assert len(digest) == 64
    assert framed_sha256(reversed(base)) == digest
    assert framed_sha256([("suite/b/spec.md", b"ab"),
                          ("suite/a/task.toml", None)]) != digest
    assert framed_sha256([("suite/a/spec.md", b"a"),
                          ("suite/a/task.toml", b"b")]) != digest
    assert framed_sha256([("suite/a/spec.md", b"ab"),
                          ("suite/a/task.toml", b"")]) != digest

    with pytest.raises(ValueError, match="duplicate measurement member"):
        framed_sha256([("same", b"one"), ("same", b"two")])


def test_completed_measurement_records_are_atomic_and_never_replaced(tmp_path):
    path = tmp_path / "record.json"
    first = {"schema": "measurement-record/1", "invocation": "one"}
    write_json_exclusive(path, first)
    assert json.loads(path.read_text()) == first

    write_json_exclusive(path, first)  # exact retry is harmless
    with pytest.raises(RuntimeError, match="refusing to replace"):
        write_json_exclusive(path, {**first, "invocation": "two"})
    assert json.loads(path.read_text()) == first
    assert [item.name for item in tmp_path.iterdir()] == ["record.json"]


def test_frontend_population_uses_frozen_suite_relative_inputs(
        monkeypatch, tmp_path):
    data = tmp_path / "data"
    root = data / "problems"
    monkeypatch.setenv("BEHAVE_DATA_DIR", str(data))
    paths = []
    for suite in ("one", "two"):
        task = root / suite / "same"
        task.mkdir(parents=True)
        behavior = task / "behavior.py"
        behavior.write_bytes(b"class M: pass\n")
        (task / "spec.md").write_bytes(b"spec\n")
        paths.append(behavior)

    population = _tool_module("population")
    frozen = population._freeze(paths)
    assert [row["path"] for row in frozen] == ["one/same", "two/same"]
    digest = population.dataset_digest(contents=frozen)
    assert len(digest) == 64
    paths[0].write_bytes(b"changed after freeze\n")
    assert population.dataset_digest(contents=frozen) == digest
    assert population.dataset_digest(paths) != digest


def test_population_provenance_uses_versioned_full_digest():
    population = _tool_module("population")
    current = population.provenance(
        [{"model": "m", "path": "suite/m", "behavior": b"b", "spec": b"s"}],
        4, 30, 2, {"commit": "f" * 40, "branch": None,
                   "detached": True, "dirty_files": []})
    assert current["record_schema"] == "measurement-record/1"
    assert current["detached"] is True
    assert current["branch"] is None
    assert len(current["dataset_digest"]) == 64
