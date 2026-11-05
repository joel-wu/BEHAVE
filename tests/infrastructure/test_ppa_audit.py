"""Boundary regressions: retained evidence and measurement dependencies."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_ppa import FakeNative, Scripted, experiment, fixture_task
from test_ppa_review import archive_with_candidate

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import load_chain
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.ppa.finalize import compare_searches
from behave.ppa.performance import stimulus
from behave.ppa.records import read_document, write_document
from behave.ppa.workflow import frontier, run_search
from behave.task_execution._worker_transport import WorkerResponse
from behave.task_execution.comparison import ComparisonInputs


def transport_native(technology_name="sky130hd"):
    """Only the native transport is exercised; no EDA tool is launched."""
    return SimpleNamespace(liberty=Path("unused.lib"), sta=Path("unused-sta"),
        library_ref=BlobRef.of(b"library"), sta_ref=BlobRef.of(b"sta"),
        abc_ref=BlobRef.of(b"abc"), runtime_identity="fixture-not-executed",
        technology_name=technology_name)


@pytest.mark.parametrize("failed,dependency", [(False, "pool"), (True, "pool"), (False, "eda")])
def test_native_transport_retains_its_declared_dependencies(tmp_path, monkeypatch, failed, dependency):
    from behave.ppa import worker

    _, task, _ = fixture_task()
    inputs = ComparisonInputs("rtl", task.reference_behavior, task.reference_behavior,
                              task.contract, task.run_policy, task.judge_policy)
    native = transport_native()
    refs = {}

    def exchange(kind, packet, *, workdir, objects, **kwargs):
        assert kind == "ppa"
        local = DirectoryStore(workdir / "objects")
        refs["pool"] = BlobRef.from_dict(parse_json(packet)["pool"])
        assert read_document(local, refs["pool"]) == {"stimuli": []}
        assert kwargs["timeout_seconds"] == experiment().eda_budget.service_seconds
        receipt = objects.put(packet)
        if failed:
            return WorkerResponse("ppa-worker-failed", False, None, (receipt,))
        refs["eda"] = local.put(canonical_json({"scripts": "fixed EDA evidence"}))
        record, closure = write_document(local, {"eda": {"evidence": refs["eda"].as_dict(), "closure": []}})
        return WorkerResponse(None, False, {"record": record.as_dict(),
            "closure": [*(ref.as_dict() for ref in closure), refs["eda"].as_dict()]}, (receipt,))

    monkeypatch.setattr(worker, "exchange", exchange)
    store = DirectoryStore(tmp_path / "private")
    row = worker.NativePPA._run(native, inputs, experiment(), "eda", (), store)
    retained = tuple(BlobRef.from_dict(ref) for ref in row["evidence"])
    assert refs[dependency] in retained
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(store, retained)
    read_verified(exported, refs[dependency])


def test_native_transport_rejects_wrong_technology_before_staging(tmp_path, monkeypatch):
    from behave.ppa import worker

    _, task, _ = fixture_task()
    inputs = ComparisonInputs("rtl", task.reference_behavior, task.reference_behavior,
                              task.contract, task.run_policy, task.judge_policy)
    monkeypatch.setattr(ComparisonInputs, "stage", lambda *a, **kw: pytest.fail("mismatch staged inputs"))
    monkeypatch.setattr(worker, "exchange", lambda *a, **kw: pytest.fail("mismatch launched a worker"))
    store = DirectoryStore(tmp_path / "not-created")
    with pytest.raises(ValueError, match="runtime technologies differ"):
        worker.NativePPA._run(transport_native("nangate45"), inputs, experiment(), "eda", (), store)
    assert not store.directory.exists()


@pytest.mark.parametrize("failed", [False, True])
def test_new_mapping_preserves_captured_rtl_time_and_invalidates_mapped_diagnostics(tmp_path, failed):
    class ReMeasure(FakeNative):
        def run(self, *args):
            return {"code": "ppa-worker-timeout"} if failed else super().run(*args)

    archive, key = archive_with_candidate(tmp_path, ReMeasure())
    row = archive.candidates[key]
    row.update(score=1, mapped_score=1, performance={"score": 1, "time_ns": 80., "source": "rtl"},
               eda={"status": "measured", "timing": {"status": "met"}, "area_um2": 10.})
    assert frontier([row]) == [key]
    archive.action(key, "eda")
    assert row["mapped_score"] is None
    assert row["performance"] == {"score": 1, "time_ns": 80., "source": "rtl"}
    assert frontier([row]) == ([] if failed else [key])


def test_final_export_retains_search_configuration_and_measurement_history(tmp_path):
    public, task, _ = fixture_task()
    search = tmp_path / "search"
    result = run_search(public=public, task=task, experiment=experiment(),
        spec=AttemptSpec("audit", "ppa", {"model": "fake"}), backend=Scripted(),
        native=FakeNative(), directory=search, prompt="fixture only")
    common = tmp_path / "common"
    final = compare_searches(searches={"one": search}, task=task, experiment=experiment(),
                             native=FakeNative(), directory=common)
    run = JournalDirectory(common)
    head, closure = run.seal("workflow")
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(run.objects, closure)
    events = load_chain(exported, head)
    manifest = read_document(exported, BlobRef.from_dict(final["manifest"]))
    search_ref = BlobRef.from_dict(manifest["searches"]["one"])
    assert read_document(exported, search_ref) == result
    configuration = BlobRef.from_dict(result["configuration"])
    assert parse_json(read_verified(exported, configuration))["prompt"] == "fixture only"
    assert events[-1].kind == "finished"
    for name, ref in manifest["search_journals"]["one"].items():
        chain = load_chain(exported, None if ref is None else BlobRef.from_dict(ref))
        assert chain == JournalDirectory(search).journal(name).load()
    for row in result["candidates"]:
        for item in row["history"]:
            read_verified(exported, BlobRef.from_dict(item["pool"]))
    assert thaw_json(events[0].payload)["manifest"] == final["manifest"]
    for name, ref in thaw_json(events[-1].payload)["journals"].items():
        assert load_chain(exported, BlobRef.from_dict(ref)) == run.journal(name).load()


@pytest.mark.parametrize("dependency", ["pool", "policies"])
def test_empty_search_retains_final_pool_and_private_input_bytes(tmp_path, dependency):
    from behave.agent.model import ModelReply

    class Stop:
        def generate(self, request):
            return ModelReply("", ())

    public, task, contract = fixture_task()
    pool = (stimulus(contract, experiment().workloads[0]).as_dict(),)
    run_search(public=public, task=task, experiment=experiment(), final_pool=pool,
        spec=AttemptSpec("empty", "ppa", {"model": "fake"}), backend=Stop(),
        native=FakeNative(), directory=tmp_path / "search", prompt="fixture")
    run = JournalDirectory(tmp_path / "search")
    head, closure = run.seal("workflow")
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(run.objects, closure)
    chain = load_chain(exported, head)
    if dependency == "pool":
        start = next(event for event in chain if event.kind == "started")
        config = parse_json(read_verified(exported, BlobRef.from_dict(thaw_json(start.payload)["configuration"])))
        assert "final_pool" not in config
        assert read_document(exported, BlobRef.from_dict(config["final_pool_ref"])) == list(pool)
        request = next(event for event in chain if event.kind == "final_requested")
        ref = BlobRef.from_dict(thaw_json(request.payload)["pool"])
        assert read_document(exported, ref) == list(pool)
    else:
        for data in (task.run_policy, task.judge_policy, *(f.data for f in task.reference_behavior.files)):
            assert read_verified(exported, BlobRef.of(data)) == data


@pytest.mark.parametrize("score", [0, None])
@pytest.mark.parametrize("mode", ["final", "performance"])
def test_early_performance_failure_preserves_known_verdict(tmp_path, score, mode):
    class EarlyFailure(FakeNative):
        def run(self, *args):
            return {"score": score, "code": "fixture-early-failure"}

    archive, key = archive_with_candidate(tmp_path, EarlyFailure())
    row = archive.candidates[key]
    row["eda"] = {"simulation_source": row["source"]}
    row["performance"] = {"score": 1, "time_ns": 80.}
    archive.action(key, mode)
    assert row["performance"] == {"score": score, "time_ns": None, "source": "rtl"}


def test_unconfirmed_worker_cleanup_retains_request_dependencies(tmp_path, monkeypatch):
    from behave.agent.tools import ToolUnavailable
    from behave.core.lifecycle import CleanupState
    from behave.ppa import worker

    _, task, _ = fixture_task()
    inputs = ComparisonInputs("rtl", task.reference_behavior, task.reference_behavior,
                              task.contract, task.run_policy, task.judge_policy)
    native = transport_native()
    requests = []

    @contextmanager
    def scratch(*, prefix, cleanup):
        path = tmp_path / "scratch"
        path.mkdir()
        try:
            yield path
        finally:
            assert cleanup() is CleanupState.UNKNOWN

    def exchange(kind, packet, *, objects, **kwargs):
        requests.append(parse_json(packet))
        return WorkerResponse("ppa-worker-cleanup-unconfirmed", False, None,
                              (objects.put(packet),), CleanupState.UNKNOWN)

    monkeypatch.setattr(worker, "ScratchDirectory", scratch)
    monkeypatch.setattr(worker, "exchange", exchange)
    store = DirectoryStore(tmp_path / "private")
    with pytest.raises(ToolUnavailable, match="ppa-cleanup-unconfirmed") as caught:
        worker.NativePPA._run(native, inputs, experiment(), "eda", (), store)
    assert len(requests) == 1
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(store, caught.value.artifacts)
    for name in ("inputs", "pool"):
        read_verified(exported, BlobRef.from_dict(requests[0][name]))
    for data in (task.contract.data, task.run_policy, task.judge_policy,
                 *(f.data for f in task.reference_behavior.files)):
        read_verified(exported, BlobRef.of(data))


def test_unknown_measurement_stops_live_attempt_and_cold_resume(tmp_path):
    from behave.agent.attempt import AttemptInterrupted
    from behave.agent.tools import ToolUnavailable

    class Unknown(FakeNative):
        calls = 0

        def run(self, *args):
            self.calls += 1
            raise ToolUnavailable("ppa-cleanup-unconfirmed")

    public, task, _ = fixture_task()
    native = Unknown()
    kwargs = {"public": public, "task": task, "experiment": experiment(),
        "spec": AttemptSpec("unknown", "ppa", {"model": "fake"}), "native": native,
        "directory": tmp_path / "search", "prompt": "fixture only"}
    with pytest.raises(AttemptInterrupted, match="ppa-cleanup-unconfirmed"):
        run_search(backend=Scripted(), **kwargs)
    with pytest.raises(AttemptInterrupted, match="ppa-measurement-outcome-unknown"):
        run_search(backend=Scripted(), **kwargs)
    assert native.calls == 1
    run = JournalDirectory(kwargs["directory"])
    assert run.journal("measurements").load()[-1].kind == "requested"
    assert all(event.kind != "finished" for event in run.journal("workflow").load())


@pytest.mark.parametrize("failed_mode", ["final", "eda"])
def test_common_final_failure_never_reuses_search_feasibility(tmp_path, failed_mode):
    class FinalFailure(FakeNative):
        fail = False

        def run(self, inputs, exp, mode, pool, objects):
            if (self.fail or failed_mode == "eda") and mode == failed_mode:
                return {"score": None, "code": "fixture-service-failed", "pool": list(pool)}
            return super().run(inputs, exp, mode, pool, objects)

    public, task, _ = fixture_task()
    native = FinalFailure()
    search = tmp_path / "search"
    result = run_search(public=public, task=task, experiment=experiment(),
        spec=AttemptSpec("final-failure", "ppa", {"model": "fake"}), backend=Scripted(),
        native=native, directory=search, prompt="fixture only")
    assert result["frontier"] == []
    native.fail = True
    final = compare_searches(searches={"one": search}, task=task, experiment=experiment(),
                             native=native, directory=tmp_path / "final")
    assert final["frontiers"]["one"] == []
    assert len(final["candidates"]) == 1
    assert final["final_service_calls"] == (2 if failed_mode == "eda" else 1)
