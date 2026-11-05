"""Closed build records and actual judge-owned source preparation."""

import copy
import json
from dataclasses import replace
from types import MappingProxyType

import pytest

from behave.sim.core.catalogue import COVERAGE, CatalogueManifest, Goal, OccurrenceKey
from behave.sim.core.failure import FailureDisposition, FailureOrigin, RunFailure
from behave.sim.core.record_value import canonical_json
from behave.sim.core.refusal import Refused
from behave.sim.rtl import native_preparation as prep
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_protocol, tool_runtime
from behave.sim.rtl.native_events import NativeEventPlan
from behave.sim.rtl.source_bindings import BoundOccurrence, EventBindings

from .test_rtl_tool_isolation import _command_row, _completed, _needs_namespace, _runtime


def _plan():
    entries = (BoundOccurrence(OccurrenceKey("test", [0], "action"), "g0", 0, ()),
               BoundOccurrence(OccurrenceKey("test", [1], "branch"), "g1", 1, (0,)))
    return NativeEventPlan(EventBindings(2, entries), "a" * 64, 100)


def _reply(request="b" * 64):
    plan = _plan()
    source, collector = b"test-only-native-source", plan.collector_source().encode()
    image = {"version": prep.VERSION, "request": request, "status": "prepared",
             "plan": plan.as_dict(), "native_sha256": prep._sha(source),
             "collector_sha256": prep._sha(collector), "boundary_fields": [], "boundary_paths": [], "startup_aliases": []}
    return _completed(commands=(_command_row("prepare-native-events"),), outputs={
        "prepared.json": canonical_json(image).encode(),
        "native.sv": source, "collector.cpp": collector})


def _catalogue(plan):
    return CatalogueManifest(
        [Goal(goal, COVERAGE) for goal in sorted({b.goal for b in plan.bindings.occurrences})],
        occurrences=tuple((b.occurrence, b.goal) for b in plan.bindings.occurrences))


def test_native_plan_requires_the_entire_frontend_occurrence_relation():
    plan = _plan()
    manifest = _catalogue(plan)
    assert plan.check_catalogue(manifest) is None
    entries = plan.bindings.occurrences
    # Same public IDs and counts, but a different occurrence or attribution.
    for changed in (
        (replace(entries[0], occurrence=OccurrenceKey("test", [9], "action")), entries[1]),
        (replace(entries[0], goal="g1"), replace(entries[1], goal="g0")),
    ):
        mutant = replace(plan, bindings=EventBindings(2, changed))
        assert {e.goal for e in changed} == set(manifest)
        with pytest.raises(Refused, match="occurrence"):
            mutant.check_catalogue(manifest)
    # Two occurrences of one public goal must not collapse to one witness.
    same_goal = replace(plan, bindings=EventBindings(2, (
        entries[0], replace(entries[1], goal="g0"))))
    omitted = replace(same_goal, bindings=EventBindings(2, (entries[0],)))
    with pytest.raises(Refused, match="missing-occurrence"):
        omitted.check_catalogue(_catalogue(same_goal))
    with pytest.raises(Refused, match="goal-mismatch"):
        replace(plan, bindings=EventBindings(0, ())).check_catalogue(
            CatalogueManifest([Goal("unbacked", COVERAGE)]))


def test_preparation_request_binds_frontend_schema_and_returns_no_result(tmp_path, monkeypatch):
    runtime = tool_runtime.FrozenToolRuntime("source-events", *_runtime(tmp_path))
    monkeypatch.setattr(tool_runtime, "freeze_runtime", lambda _profile: runtime)
    requests = []

    def run(root, _groups, **_kwargs):
        request = (root / "request.json").read_bytes()
        requests.append(json.loads(request))
        assert (root / prep.SOURCE_FILENAME).read_text() == "source"
        return _reply(prep._sha(request))

    monkeypatch.setattr(tools, "run_workspace", run)
    plan = _plan()
    original = _catalogue(plan)
    changed = CatalogueManifest(
        list(original.values()), occurrences=tuple(
            (OccurrenceKey(key.provider, [*key.path, "changed"], key.role), goal)
            for key, goal in original.occurrences))
    args = {"top": "M", "execution_sha256": "2" * 64, "callback_limit": 100}
    assert type(prep.prepare_source_observer("source", catalogue=original, **args)) \
        is prep.PreparedNativeEvents
    result = prep.prepare_source_observer("source", catalogue=changed, **args)
    _no_result(result)
    assert result.kind == "catalogue-native-events-unknown-occurrence"
    assert [request["catalogue"] for request in requests] == [original.digest(), changed.digest()]
    assert requests[0] != requests[1]


def _no_result(value, origin=FailureOrigin.VERIFIER):
    assert type(value) is RunFailure, value
    assert value.origin is origin and value.disposition is FailureDisposition.NO_RESULT
    assert not value.determines_score


def test_preparation_refusal_keeps_the_complete_diagnostic():
    detail = "source binding is unavailable: " + ", ".join(
        f"instance_{index}.counter" for index in range(200))
    image = {"version": prep.VERSION, "request": "b" * 64, "status": "refused",
             "code": "source-mapping-unavailable", "detail": detail}
    execution = _completed(outputs={"prepared.json": canonical_json(image).encode()})
    result = prep._decode(execution, "b" * 64, 100)
    _no_result(result)
    assert result.kind == "native-events-source-mapping-unavailable"
    assert result.reason.detail["detail"] == detail


def test_runtime_preparation_failure_keeps_the_complete_diagnostic(monkeypatch):
    detail = "missing runtime resources: " + ", ".join(f"resource_{i}" for i in range(200))

    def unavailable(_profile):
        raise ValueError(detail)

    monkeypatch.setattr(tool_runtime, "freeze_runtime", unavailable)
    result = prep.prepare_source_observer(
        "source", top="M", execution_sha256="2" * 64, callback_limit=100)
    _no_result(result, FailureOrigin.INFRASTRUCTURE)
    assert result.reason.detail["detail"] == detail


def test_closed_plan_roundtrip_and_detached_image():
    plan = _plan()
    image = json.loads(json.dumps(plan.as_dict()))
    rebuilt = NativeEventPlan.from_dict(image)
    assert rebuilt == plan and rebuilt.collector_source() == plan.collector_source()
    image["occurrences"][1]["ancestors"].clear()
    assert rebuilt.bindings.decode({0, 1}).goals == {"g0", "g1"}
    with pytest.raises(ValueError, match="identity"):
        NativeEventPlan.from_dict(image)


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=1),
    lambda p: p.update(version="old"),
    lambda p: p.update(event_count=True),
    lambda p: p.update(callback_limit=True),
    lambda p: p.update(build_sha256="not-a-hash"),
    lambda p: p["occurrences"].append(copy.deepcopy(p["occurrences"][0])),
    lambda p: p["occurrences"][0].update(event=True),
    lambda p: p["occurrences"][0].update(event=2),
    lambda p: p["occurrences"][1].update(ancestors=[1]),
    lambda p: p["occurrences"][1].update(ancestors=[0, 0]),
    lambda p: p["occurrences"][1].update(ancestors=[True]),
    lambda p: p["occurrences"][1].update(goal=""),
    lambda p: p["occurrences"][1]["key"].update(path=[False]),
    lambda p: p["occurrences"][1]["key"].update(extra="ignored?"),
])
def test_malformed_plan_never_acquires_decoder(change):
    image = _plan().as_dict()
    change(image)
    with pytest.raises((ValueError, TypeError)):
        NativeEventPlan.from_dict(image)


def test_prepared_outputs_need_request_template_and_all_members():
    good = _reply()
    value = prep._decode(good, "b" * 64, 100)
    assert type(value) is prep.PreparedNativeEvents and value.plan == _plan()
    with pytest.raises(ValueError, match="scope root"):
        prep._decode(good, "b" * 64, 100, scope_root="")
    mutations = []
    for name in good.outputs:
        missing = dict(good.outputs)
        del missing[name]
        mutations.append(missing)
    mutations.append(dict(good.outputs) | {"cst.json": b"{}"})
    mutations.append(dict(good.outputs) | {"native.sv": b"changed"})
    image = json.loads(good.outputs["prepared.json"])
    bad_collector = b"// different callback implementation"
    image["collector_sha256"] = prep._sha(bad_collector)
    mutations.append(dict(good.outputs) | {
        "collector.cpp": bad_collector, "prepared.json": canonical_json(image).encode()})
    mutations.append(dict(good.outputs) | {"prepared.json": b'{"status":0,"status":1}'})
    for outputs in mutations:
        with pytest.raises((ValueError, KeyError)):
            prep._decode(replace(good, outputs=MappingProxyType(outputs)), "b" * 64, 100)
    with pytest.raises(ValueError, match="request"):
        prep._decode(good, "c" * 64, 100)
    with pytest.raises(ValueError, match="budget"):
        prep._decode(good, "b" * 64, 99)


def test_host_boundary_exports_no_raw_tree_and_attributes_faults(tmp_path, monkeypatch):
    calls, freezes = [], []
    runtime = tool_runtime.FrozenToolRuntime("source-events", *_runtime(tmp_path))

    def freeze(profile):
        freezes.append(profile)
        return runtime

    monkeypatch.setattr(tool_runtime, "freeze_runtime", freeze)

    def run(root, groups, **kwargs):
        request = (root / "request.json").read_bytes()
        calls.append((groups, kwargs))
        image = prep._request(json.loads(request))
        assert image["source"] == prep._sha(b"source")
        assert image["toolchain"] == runtime.identity
        assert kwargs["frozen_runtime"] is runtime
        return _reply(prep._sha(request))

    monkeypatch.setattr(tools, "run_workspace", run)
    args = {"top": "M", "execution_sha256": "2" * 64, "callback_limit": 100}
    assert type(prep.prepare_source_observer("source", **args)) is prep.PreparedNativeEvents
    assert freezes == ["source-events"]
    groups, config = calls.pop()
    assert config["runtime_profile"] == "source-events"
    assert set(config["outputs"]) == {"prepared.json", "native.sv", "collector.cpp"}
    assert groups[0].commands[0].argv[-1] == "behave.sim.rtl._prepare_native_events"
    for row in (_command_row("prepare-native-events", timed_out=True),
                _command_row("prepare-native-events", returncode=1)):
        monkeypatch.setattr(tools, "run_workspace", lambda *a, row=row, **k: _completed(commands=(row,)))
        _no_result(prep.prepare_source_observer("source", **args))
    monkeypatch.setattr(tools, "run_workspace", lambda *a, **k: _reply())
    _no_result(prep.prepare_source_observer("source", **args))  # Wrong request.
    monkeypatch.setattr(tool_runtime, "freeze_runtime",
                        lambda p: (_ for _ in ()).throw(FileNotFoundError("slang")))
    _no_result(prep.prepare_source_observer("source", **args), FailureOrigin.INFRASTRUCTURE)


@pytest.mark.parametrize("change", [{"top": "M; shell unsafe"}, {"top": "M\nfoo"},
                                    {"callback_limit": 0}, {"timeout_s": 0},
                                    {"execution_sha256": "bad"}, {"catalogue": {}},
                                    {"scope_root": "TOP.M"}, {"scope_root": None},
                                    {"scope_root": 'x"injected'}])
def test_invalid_request_never_enters_tool_judge(monkeypatch, change):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request entered runtime discovery")
    monkeypatch.setattr(tool_runtime, "freeze_runtime", forbidden)
    args = {"top": "M", "execution_sha256": "2" * 64, "callback_limit": 100} | change
    with pytest.raises((ValueError, TypeError)):
        prep.prepare_source_observer("source", **args)


@pytest.mark.isolation
@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("name,code", [("inline", None), ("blocking", None),
                                     ("unrolled", None),
                                     ("helper", "source-mapping-unavailable")])
def test_actual_judge_prepares_or_refuses_complete_mapping(tmp_path, name, code):
    from behave.sim.rtl.artifact import RTLArtifact

    from .test_rtl_source_plan import FIXTURES

    _needs_namespace()
    source = FIXTURES[name]
    # This is the production catalogue, not another compilation
    # with the observer's own recipe. The basename is supplied by that owner.
    original = tmp_path / "candidate.sv"
    original.write_text(source)
    manifest = (RTLArtifact(original, "M", tmp_path / "artifact").frontend_catalogue()
                if code is None else None)
    result = prep.prepare_source_observer(source, top="M", execution_sha256="2" * 64,
                                          callback_limit=1000, catalogue=manifest)
    assert original.read_text() == source
    if code:
        _no_result(result)
        assert result.kind == "native-events-" + code, result.as_dict()
    else:
        assert type(result) is prep.PreparedNativeEvents, result
        assert result.plan.bindings.projections and result.plan.bindings.event_count
        assert result.plan.check_catalogue(manifest) is None
        assert "__behave_native_event_hit" in result.source
        assert result.collector == result.plan.collector_source()
        assert set(result.execution.outputs) == {"prepared.json", "native.sv", "collector.cpp"}
        assert tool_protocol.command_result(result.execution, "prepare-native-events")["returncode"] == 0


@pytest.mark.isolation
@pytest.mark.eda("slang", "yosys")
def test_native_hierarchy_uses_the_production_catalogue(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    from .test_rtl_source_plan import FIXTURES

    _needs_namespace()
    source = FIXTURES["instances"]
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    manifest = RTLArtifact(path, "M", tmp_path / "artifact").frontend_catalogue()
    arguments = {"top": "M", "execution_sha256": "2" * 64, "callback_limit": 1000}
    diagnostic = prep.prepare_source_observer(source, **arguments)
    assert type(diagnostic) is prep.PreparedNativeEvents
    assert diagnostic.plan.bindings.coordinates
    prepared = prep.prepare_source_observer(source, catalogue=manifest, **arguments)
    assert type(prepared) is prep.PreparedNativeEvents, prepared
    assert prepared.plan.check_catalogue(manifest) is None
    # Same physical callbacks; the source policy and old action diagnostic have
    # intentionally different public projections, not interchangeable goals.
    assert prepared.plan.bindings.coordinates == diagnostic.plan.bindings.coordinates
    assert prepared.plan.bindings.event_count == diagnostic.plan.bindings.event_count
    assert prepared.plan.bindings.projections
    assert not prepared.plan.bindings.occurrences
    assert diagnostic.plan.bindings.occurrences
    # A missing instance must still refuse, even if its public goals survive.
    changed = CatalogueManifest(list(manifest.values()), occurrences=manifest.occurrences[1:])
    refused = prep.prepare_source_observer(source, catalogue=changed, **arguments)
    _no_result(refused)
    assert refused.kind == "catalogue-native-events-unknown-occurrence"
    assert path.read_text() == source


@pytest.mark.eda("slang", "verilator", "yosys")
def test_source_observation_does_not_require_a_top_level_edge_alias(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    _needs_namespace()
    source = """module M(input logic clk,rst0,rst1,a,output logic q);
      wire reset_all=rst0 | rst1;
      always @(posedge clk or posedge reset_all)
        if(reset_all) q<=0; else q<=a;
    endmodule"""
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    manifest = RTLArtifact(path, "M", tmp_path / "artifact").frontend_catalogue()
    arguments = {"top": "M", "execution_sha256": "2" * 64, "callback_limit": 1000,
                 "allowed_triggers": frozenset({("always", ""), ("posedge", "clk")})}
    native = prep.prepare_source_observer(source, catalogue=manifest, **arguments)
    assert type(native) is prep.PreparedNativeEvents, native
    assert native.plan.check_catalogue(manifest) is None
    assert "posedge reset_all" in native.source
    # This does not make the direct-edge RTLIL abstraction sound for reset_all.
    diagnostic = prep.prepare_source_observer(source, **arguments)
    _no_result(diagnostic)
    assert diagnostic.kind == "native-events-unscheduled-trigger"


@pytest.mark.isolation
@pytest.mark.eda("slang", "yosys")
def test_parser_cannot_read_host_include(tmp_path, monkeypatch):
    _needs_namespace()
    run = tools.run_workspace
    diagnostics = []

    def capture(*args, **kwargs):
        execution = run(*args, **kwargs)
        diagnostics.append(tool_protocol.command_stream(execution, "prepare-native-events", "stderr"))
        return execution

    monkeypatch.setattr(tools, "run_workspace", capture)
    header = tmp_path / "private.svh"
    header.write_text("module M(input clk,a, output reg q); always @(posedge clk) q<=a; endmodule")
    result = prep.prepare_source_observer(f'`include "{header}"\n', top="M",
                                          execution_sha256="2" * 64, callback_limit=1000)
    _no_result(result)
    assert result.kind == "native-events-source-parse-failed", result.as_dict()
    assert header.read_text().startswith("module M")
    diagnostic, = diagnostics
    assert header.name.encode() in diagnostic and b"error:" in diagnostic
