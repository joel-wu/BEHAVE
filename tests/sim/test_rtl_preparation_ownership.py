"""Prepared source is shared; replay feedback and worker state have a run owner."""

import json
from types import SimpleNamespace

import pytest

from behave.sim.core.refusal import Refusal, Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import _IsolatedRTLGeneration
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.exact_job_layout import RTLGenerationLayout

from .test_rtl_generation_cache import _context, _context_runtime as _context_runtime  # noqa: F401
from .test_rtl_generation_provider import _seed


@pytest.fixture
def compiler(monkeypatch):
    calls = []
    documents = {
        "source": "module cached; endmodule\n",
        "cst": {"z-last-in-alphabet": [1], "a-first-in-alphabet": [2]},
        "ast": {}, "sensitivity": {},
    }

    def compile_source(artifact, *, expected_identity, timeout_s):
        assert expected_identity == artifact.identity
        calls.append(timeout_s)
        return json.loads(json.dumps(documents))

    monkeypatch.setattr("behave.sim.rtl.elaboration.source_documents", compile_source)
    return SimpleNamespace(calls=calls, documents=documents)


def _service(context):
    return _IsolatedRTLGeneration(
        context.artifact, context.contract, context.generation_spec,
        context.frontend_catalogue, context.complete_catalogue,
        workdir=context.workdir / "search", timeout_ms=1000)


def test_cancelled_preparation_has_no_completed_result_and_can_retry(
        tmp_path, context_runtime, compiler, monkeypatch):
    from behave.isolation.preparation_control import PreparationCancelled

    context = _context(context_runtime.rtl, tmp_path)
    prepare, payload = context.prepare_source, context.source_payload
    calls = []

    def worker(job, _payload, **_kwargs):
        calls.append(job)
        if len(calls) == 1:
            raise PreparationCancelled("owner deadline")
        return RTLGenerationLayout(("out_valid",), ("q",)).as_dict()

    monkeypatch.setattr("behave.sim.worker.run_job_strict", worker)
    with pytest.raises(PreparationCancelled, match="owner deadline"):
        prepare()
    with pytest.raises(RuntimeError, match="not prepared"):
        payload()
    result = prepare()
    assert type(result) is RTLGenerationLayout
    assert prepare() is result
    assert len(calls) == 2  # No automatic retry; explicit new attempt then reuse.
    assert payload()["artifact"] == context.artifact_identity
    assert len(compiler.calls) == 1


def test_failed_preparation_does_not_expose_a_worker_payload(
        tmp_path, context_runtime, compiler, monkeypatch):
    from behave.sim.rtl.exact_job_layout import _refusal_reply

    context = _context(context_runtime.rtl, tmp_path)
    failure = Refusal("unsupported-layout", "no complete layout")
    calls = []

    def worker(*args, **kwargs):
        calls.append(True)
        return _refusal_reply(failure)

    monkeypatch.setattr("behave.sim.worker.run_job_strict", worker)
    prepare, payload = context.prepare_source, context.source_payload
    result = prepare()
    assert result.as_dict() == failure.as_dict()
    assert prepare() is result and calls == [True]
    with pytest.raises(RuntimeError, match="not prepared"):
        payload()


@pytest.mark.parametrize("first", ["search", "analysis"])
def test_search_and_analysis_compile_source_once_in_either_order(
        tmp_path, context_runtime, compiler, monkeypatch, first):
    service = _service(_context(context_runtime.rtl, tmp_path))
    context = service._context
    opened = []

    def source_session(payload, **kwargs):
        opened.append(payload)
        # This test exercises preparation only, not solving or classification.
        return SimpleNamespace(opened={"constant_false_goals": ["test-target"]}, close=lambda: None)

    monkeypatch.setattr("behave.sim.rtl.source_generation.SourceGenerationSession", source_session)

    def search():
        service.begin_search(None)
        try:
            result = service._probe_source(_seed(), SimpleNamespace(targets=("test-target",)), None)
            assert result.outcome is probing.Outcome.LOCAL_UNSAT
            assert result.candidate is None
        finally:
            service.end_search()

    def analysis():
        assert type(context.prepare_source()) is RTLGenerationLayout

    (search if first == "search" else analysis)()
    (analysis if first == "search" else search)()
    assert len(compiler.calls) == 1
    assert len(context_runtime.layouts) == 1
    image = json.loads((context.workdir / "source.json").read_bytes())
    for name, value in compiler.documents.items():
        assert image[name] == opened[0][name] == value
    assert list(image["cst"]) == list(compiler.documents["cst"])


def test_source_worker_failure_is_owned_by_one_search_scope(
        tmp_path, context_runtime, compiler, monkeypatch):
    service = _service(_context(context_runtime.rtl, tmp_path))
    opened, closed = [], []
    failure = Refusal("test-source-worker-failed", "this invocation failed to start")

    def source_session(payload, **kwargs):
        opened.append(payload)
        if len(opened) == 1:
            raise Refused(failure)
        return SimpleNamespace(opened={"constant_false_goals": ["test-target"]},
                               close=lambda: closed.append(True))

    monkeypatch.setattr("behave.sim.rtl.source_generation.SourceGenerationSession", source_session)
    request = SimpleNamespace(targets=("test-target",))
    service.begin_search(None)
    try:
        for _ in range(2):
            with pytest.raises(Refused) as caught:
                service._probe_source(_seed(), request, None)
            assert caught.value.refusal is failure
        assert len(opened) == 1  # No retry or budget renewal in this invocation.
    finally:
        service.end_search()
    assert service._source_refusal is failure  # Retain the completed diagnostic.

    service.begin_search(None)
    try:
        result = service._probe_source(_seed(), request, None)
        assert result.outcome is probing.Outcome.LOCAL_UNSAT
        assert service._source_refusal is None
    finally:
        service.end_search()
    assert len(opened) == 2 and opened[0] == opened[1]
    assert closed == [True] and len(compiler.calls) == 1


def test_shared_compiler_image_is_detached_but_syntax_order_is_preserved(
        tmp_path, context_runtime, compiler):
    context = _context(context_runtime.rtl, tmp_path)
    first = context.source_documents(timeout_s=7)
    first["cst"]["z-last-in-alphabet"].append(99)
    first["ast"]["invented"] = True
    second = context.source_documents(timeout_s=3)
    assert second == compiler.documents and first != second
    assert list(second["cst"]) == list(compiler.documents["cst"])
    assert compiler.calls == [7]


@pytest.mark.parametrize("when", ["during", "after"])
@pytest.mark.parametrize("what", ["source", "observer"])
def test_shared_compilation_rejects_changed_binding(
        tmp_path, context_runtime, compiler, monkeypatch, when, what):
    context = _context(context_runtime.rtl, tmp_path)

    def replace_binding():
        if what == "source":
            context_runtime.rtl.write_text("module cached; wire changed; endmodule\n")
        else:
            foreign = object()
            monkeypatch.setattr(RTLArtifact, "observation_binding", property(lambda self: foreign))

    if when == "during":
        def change(*args, **kwargs):
            replace_binding()
            return compiler.documents
        monkeypatch.setattr("behave.sim.rtl.elaboration.source_documents", change)
    else:
        context.source_documents()
        replace_binding()
    with pytest.raises(Refused, match=("source" if what == "source" else "observation")
                       + "-changed-after-context-binding"):
        context.source_documents()
    if when == "during":
        assert context._source_documents is None


def test_failed_compile_publishes_no_shared_product(tmp_path, context_runtime, compiler, monkeypatch):
    context = _context(context_runtime.rtl, tmp_path)
    calls = []
    failure = Refusal("test-compile-unavailable", "not a complete compiler image")

    def compile_source(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise Refused(failure)
        return compiler.documents

    monkeypatch.setattr("behave.sim.rtl.elaboration.source_documents", compile_source)
    with pytest.raises(Refused) as caught:
        context.source_documents()
    assert caught.value.refusal is failure and context._source_documents is None
    assert context.source_documents() == compiler.documents and len(calls) == 2


@pytest.mark.parametrize("failing_worker", [None, "source"])
def test_search_cleanup_releases_execution_state_not_preparation(
        tmp_path, context_runtime, compiler, failing_worker):
    service = _service(_context(context_runtime.rtl, tmp_path))
    context = service._context
    context.source_documents()
    prepared = context.prepare_source()
    assert type(prepared) is RTLGenerationLayout
    raw = object()
    context.replay._source_traces["seed"] = raw
    context.replay._selection_traces["seed"] = raw
    context.replay._selection_feedback["seed"] = raw
    service._source_traces["seed"] = raw
    service._schedule_queries["seed"] = raw
    closed = []

    def close(name):
        closed.append(name)
        if name == failing_worker:
            raise RuntimeError("cleanup was not confirmed")

    service.begin_search(None)
    service._source_session = SimpleNamespace(close=lambda: close("source"))
    if failing_worker:
        with pytest.raises(RuntimeError, match="cleanup was not confirmed"):
            service.end_search()
    else:
        service.end_search()
    assert closed == ["source"]
    assert not service._search_active and service._source_session is None
    assert not service._source_traces and not service._schedule_queries
    assert not context.replay._source_traces and not context.replay._selection_traces
    assert not context.replay._selection_feedback
    assert context.prepare_source() is prepared
    assert context.source_documents() == compiler.documents
    assert len(compiler.calls) == len(context_runtime.layouts) == 1


def test_independent_native_replay_never_reads_or_replaces_cached_evidence(
        tmp_path, context_runtime, compiler, monkeypatch):
    context = _context(context_runtime.rtl, tmp_path)
    calls = []

    def run(*args, **kwargs):
        trace = SimpleNamespace(artifact=context.artifact_identity)
        calls.append(trace)
        return trace

    monkeypatch.setattr(RTLArtifact, "exact_trace", run)
    seed = _seed()
    cached = context.source_trace(seed)
    assert context.source_trace(seed) is cached and len(calls) == 1
    assert context.source_trace(seed, reuse=False) is not cached
    assert context.source_trace(seed, reuse=False) is not cached
    assert context.source_trace(seed) is cached and len(calls) == 3
    context.replay.clear()
    assert context.source_trace(seed) is not cached and len(calls) == 4
    assert len(context_runtime.layouts) == 1
