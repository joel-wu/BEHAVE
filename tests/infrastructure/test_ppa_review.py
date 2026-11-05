"""Failure attribution and durable evidence at PPA's shared boundaries."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_ppa import REGISTERED_RTL, RTL, FakeNative, Scripted, experiment, file, fixture_task

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, LoadedSource, SourceArtifact, read_verified
from behave.core.records import canonical_json
from behave.ppa.records import read_document
from behave.ppa.worker import execute
from behave.ppa.workflow import SearchArchive, frontier, run_search
from behave.sim import api
from behave.task_execution.comparison import ComparisonInputs, ComparisonRequest
from behave.task_execution.simulator_judge import _SimulatorService


@pytest.mark.parametrize("data,order,code", [
    (b"", None, "candidate-source-empty"),
    (b"\xff", None, "candidate-source-not-utf8"),
    (RTL, b"missing.sv\ntop.sv\n", "candidate-rtl-build-invalid"),
    (RTL, b"\xff", "candidate-rtl-build-invalid"),
])
def test_ppa_and_eval_agree_on_invalid_source_before_compilation(tmp_path, monkeypatch, data, order, code):
    from behave.task_execution import simulator_judge

    _, task, _ = fixture_task()
    files = (file("top.sv", data),) + (() if order is None else (file("rtl.f", order),))
    source = LoadedSource(SourceArtifact(tuple(f.reference for f in files), "top.sv", "M"), files)
    inputs = ComparisonInputs("rtl", source, task.reference_behavior, task.contract, task.run_policy, task.judge_policy)
    monkeypatch.setattr(simulator_judge, "_link_behavior", lambda *args: SimpleNamespace(source="reference", dependencies={}))
    monkeypatch.setattr(api, "load_reference", lambda *args, **kwargs: object())

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input must not launch a compiler")

    monkeypatch.setattr(api, "prepare_rtl_bundle", forbidden)
    ordinary = _SimulatorService().evaluate(ComparisonRequest(inputs, tmp_path), JournalDirectory(tmp_path).objects)
    assert (ordinary.score, ordinary.code, ordinary.origin) == (0, code, "candidate")
    import behave.task_execution.comparison as comparison
    monkeypatch.setattr(comparison, "compare_sources", lambda inputs, **kwargs: ordinary)
    result = execute(inputs, experiment(), "check", (), tmp_path, JournalDirectory(tmp_path).objects,
                     liberty=None, sta=None)
    assert result["judgment"] == ordinary.as_dict()


def archive_with_candidate(tmp_path, native=None):
    _, task, _ = fixture_task()
    run = JournalDirectory(tmp_path)
    source = file("top.sv", RTL)
    run.objects.put(source.data)
    artifact = SourceArtifact((source.reference,), "top.sv", "M")
    candidate = BlobRef.of(canonical_json(artifact.as_dict())).sha256
    archive = SearchArchive(run, task, experiment(), native or FakeNative())
    archive.candidates[candidate] = {"candidate": candidate, "source": artifact.as_dict(), "history": []}
    return archive, candidate


@pytest.mark.parametrize("source", ["agent", "provided"])
def test_measurement_reply_names_the_development_reference(tmp_path, source):
    from behave.task_execution.sim_tool import ComparisonToolInputs

    archive, key = archive_with_candidate(tmp_path)
    archive.comparison = ComparisonToolInputs.baseline(
        archive.task, source=source, reference_visible=source == "provided")
    if source == "agent":
        reference = archive.task.reference_behavior
        archive.candidates[key]["source_reference"] = reference.artifact.as_dict()
        for file in reference.files:
            archive.run.objects.put(file.data)
    exported = {}
    workspace = SimpleNamespace(write=lambda path, data: exported.update({path: data}))
    measure = next(tool for tool in archive.tools() if tool.name == "measure_ppa")
    reply = measure.handler(workspace, {"candidate": key}).content
    assert reply["reference_source"] == source
    assert reply["candidate"] == key and reply["performance"]["source"] == "rtl"
    assert reply["eda"]["report"] in exported


def test_each_measurement_pool_is_a_readable_durable_object(tmp_path):
    archive, key = archive_with_candidate(tmp_path)
    archive.action(key, "check")
    archive.action(key, "eda")
    for item in archive.candidates[key]["history"]:
        reference = BlobRef.from_dict(item["pool"])
        assert isinstance(read_document(archive.run.objects, reference), list)
        assert any(reference in event.artifacts for event in archive.events if event.kind == "settled")
    for event in archive.events:
        if event.kind == "requested":
            reference = BlobRef.from_dict(dict(event.payload["pool"]))
            assert reference in event.artifacts
            read_verified(archive.run.objects, reference)
    # A sealed measurement journal carries its pools without reconstructing them
    # from another run or the original ephemeral worker scratch directory.
    from behave.agent.journal import load_chain
    from behave.core.storage import DirectoryStore

    head, closure = archive.run.seal("measurements")
    copied = DirectoryStore(tmp_path / "copy")
    copied.publish(archive.run.objects, closure)
    assert load_chain(copied, head) == archive.events


@pytest.mark.eda
@pytest.mark.isolation
def test_native_ppa_bad_build_manifest_scores_zero(tmp_path):
    import os
    from pathlib import Path

    from behave.ppa.worker import NativePPA

    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-pilot"))
    if not (root / "sky130.lib").is_file():
        pytest.skip("explicit SKY130 pilot installation required")
    _, task, _ = fixture_task()
    files = (file("top.sv", RTL), file("rtl.f", b"missing.sv\ntop.sv\n"))
    source = LoadedSource(SourceArtifact(tuple(f.reference for f in files), "top.sv", "M"), files)
    inputs = ComparisonInputs("rtl", source, task.reference_behavior, task.contract, task.run_policy, task.judge_policy)
    native = NativePPA(liberty=root / "sky130.lib", sta=root / "opensta/usr/bin/sta")
    result = native.run(inputs, experiment(), "check", (), JournalDirectory(tmp_path).objects)
    assert result["score"] == 0 and result["code"] == "candidate-rtl-build-invalid"


def test_pool_growth_retains_candidates_without_rechecking_during_development(tmp_path):
    calls = []

    class GrowingPool(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            calls.append((mode, pool))
            result = super().run(inputs, exp, mode, pool, objects)
            if mode == "final":
                result["score"] = 0
            return result

    archive, key = archive_with_candidate(tmp_path, GrowingPool())
    eda = {"status": "measured", "timing": {"status": "met"}, "area_um2": 10.}
    archive.candidates[key].update(score=1, mapped_score=1, eda=eda, performance={"score": 1, "time_ns": 80.})
    other = file("other.sv", RTL + b"\n")
    archive.run.objects.put(other.data)
    artifact = SourceArtifact((other.reference,), "other.sv", "M")
    other_key = BlobRef.of(canonical_json(artifact.as_dict())).sha256
    archive.candidates[other_key] = {"candidate": other_key, "source": artifact.as_dict(), "history": [],
        "score": 1, "mapped_score": 1, "eda": eda, "performance": {"score": 1, "time_ns": 100.}}
    assert frontier(archive.candidates.values()) == [key]
    archive.action(other_key, "check")
    assert [mode for mode, _ in calls] == ["check"]
    assert archive.candidates[key]["score"] == 1
    assert archive.candidates[other_key]["score"] == 1
    assert all(row["mapped_score"] == 1 for row in archive.candidates.values())
    assert archive.calls == 1
    restored = SearchArchive(archive.run, archive.task, archive.experiment, GrowingPool())
    assert restored.candidates == archive.candidates and restored.pool == archive.pool
    assert restored.calls == archive.calls


def test_independent_searches_share_a_frozen_final_union_without_dropping_candidates(tmp_path):
    from behave.agent.attempt import AttemptSpec
    from behave.agent.model import ModelReply, ToolCall
    from behave.ppa.finalize import compare_searches, final_baseline
    from behave.ppa.performance import stimulus

    public, task, contract = fixture_task()
    exp = experiment()
    first_pool = stimulus(contract, replace(exp.workloads[0], inputs=((1,),))).as_dict()
    second_pool = stimulus(contract, replace(exp.workloads[0], inputs=((2,),))).as_dict()
    calls = []

    class SecondDesign(Scripted):
        def generate(self, request):
            if self.index == 0:
                self.index += 1
                return ModelReply("", (ToolCall("0", "write_file", {"path": "top.sv", "content": REGISTERED_RTL.decode()}),))
            return super().generate(request)

    class FinalUnion(FakeNative):
        final_directory = None

        def run(self, inputs, exp, mode, pool, objects):
            first = inputs.source.files[0].data == RTL
            if self.final_directory is not None:
                events = JournalDirectory(self.final_directory).journal("workflow").load()
                assert [event.kind for event in events] == ["started", "final_requested"]
                from behave.ppa.workflow import _union
                assert pool == _union((first_pool, second_pool),
                    (s.as_dict() for s in final_baseline(task).stimuli))
                calls.append((first, mode))
            if mode == "check":
                result = super().run(inputs, exp, mode, pool, objects)
                result["pool"] = [first_pool if first else second_pool]
                return result
            if mode == "final":
                score = int(not (first and second_pool in pool))
                result = {"score": score, "pool": list(pool)}
                result["performance"] = {"score": score, "time_ns": 80. if score else None}
                return result
            return super().run(inputs, exp, mode, pool, objects)

    native = FinalUnion()
    searches, before = {}, {}
    for label, backend in (("one", Scripted()), ("two", SecondDesign())):
        searches[label] = tmp_path / label
        before[label] = run_search(public=public, task=task, experiment=exp,
            spec=AttemptSpec(label, "ppa", {"model": "fake"}), backend=backend, native=native,
            directory=searches[label], prompt="fixture only")
        assert before[label]["frontier"] == []
    native.final_directory = tmp_path / "final"
    kwargs = {"searches": searches, "task": task, "experiment": exp, "native": native,
              "directory": native.final_directory}
    result = compare_searches(**kwargs)
    assert result["frontiers"]["one"] == []
    assert len(result["frontiers"]["two"]) == 1
    assert len(result["candidates"]) == 2 and result["final_service_calls"] == len(calls) == 2
    for first in (True, False):
        assert [mode for candidate, mode in calls if candidate is first] == ["final"]
    assert compare_searches(**kwargs) == result
    assert len(calls) == 2  # Cold comparison does not dispatch anything again.
