"""Explicit operator binding admission; no runtime or GPU dispatch."""

import os
import shutil
from pathlib import Path

import pytest
from test_serving import load_tool

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json


@pytest.mark.parametrize("mutation", ["invalid-json", "fifo", "directory", "field", "tp", "lifetime", "changed"])
def test_binding_input_is_regular_closed_and_pinned(tmp_path, monkeypatch, mutation):
    job = load_tool(monkeypatch, "model_service_job")
    path = tmp_path / "binding.json"
    data = parse_json((Path(job.__file__).resolve().parents[1] / job.BINDING).read_bytes())
    expected = None
    if mutation == "invalid-json":
        path.write_bytes(b"not JSON")
    elif mutation == "fifo":
        os.mkfifo(path)
    elif mutation == "directory":
        path.mkdir()
    else:
        if mutation == "field":
            data["trust_remote_code"] = True
        elif mutation == "tp":
            data["shared"]["tensor_parallel"] = True
        elif mutation == "lifetime":
            data["shared"]["lifetime_seconds"] = 1
        elif mutation == "changed":
            expected = BlobRef.of(canonical_json(data))
            data["shared"]["tensor_parallel"] = 1
        path.write_bytes(canonical_json(data))
    with pytest.raises((ValueError, TypeError, OSError)):
        job.read_binding(path, "sglang", expected)


def test_large_valid_binding_keeps_operator_settings(tmp_path, monkeypatch):
    job = load_tool(monkeypatch, "model_service_job")
    values = parse_json((Path(job.__file__).resolve().parents[1] / job.BINDING).read_bytes())
    values["provenance"] = "operator note " * (32 << 10)
    payload = canonical_json(values)
    path = tmp_path / "binding.json"
    path.write_bytes(payload)
    binding, _, encoded = job.read_binding(path, "sglang", BlobRef.of(payload))
    assert encoded == payload and binding == values


@pytest.mark.parametrize("supplied", ["path", "reference", "changed", "missing"])
def test_worker_never_falls_back_after_an_invalid_explicit_binding(tmp_path, monkeypatch, supplied):
    job = load_tool(monkeypatch, "model_service_job")
    from test_serving_weights_job import inline_worker

    inline_worker(job, monkeypatch)

    from behave import serving
    from behave.serving import environment

    original = Path(job.__file__).resolve().parents[1] / job.BINDING
    selected = tmp_path / "binding.json"
    selected.write_bytes(original.read_bytes())
    expected = BlobRef.of(selected.read_bytes()).as_dict()
    options = {"binding_input": selected, "binding_reference": expected}
    if supplied == "path":
        options.pop("binding_reference")
    elif supplied == "reference":
        options.pop("binding_input")
    elif supplied == "changed":
        data = parse_json(selected.read_bytes())
        data["shared"]["tensor_parallel"] = 1
        selected.write_bytes(canonical_json(data))
    else:
        selected.unlink()

    def stage(snapshot, source):
        (source / job.BINDING).parent.mkdir(parents=True)
        shutil.copyfile(original, source / job.BINDING)  # A valid default must not mask the bad explicit input.
        return {"sha256": "fixture"}

    monkeypatch.setattr('behave.runtime.snapshots.stage', stage)
    monkeypatch.setattr(environment, "environment", lambda *a, **kw: pytest.fail("invalid binding reached provisioning"))
    monkeypatch.setattr(serving, "ServingSession", lambda *a, **kw: pytest.fail("invalid binding reached serving"))
    output = tmp_path / "result"
    if supplied in {"path", "reference"}:
        with pytest.raises(ValueError, match="pinned submission reference"):
            job.run(tmp_path / "snapshot", output, "sglang", **options)
        assert not output.exists()
    else:
        assert job.run(tmp_path / "snapshot", output, "sglang", **options) == 1
        report = parse_json((output / "result.json").read_bytes())
        assert report["failure_phase"] == "binding" and report["passed"] is False
        assert "installation" not in report
