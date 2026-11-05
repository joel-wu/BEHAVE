"""Real failure shapes: nested deadlines and large, repeatable PPA histories."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_ppa import FakeNative, experiment
from test_ppa_review import archive_with_candidate

from behave.agent.journal import load_chain
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.isolation import IsolationStatus
from behave.ppa import eda
from behave.ppa.config import EDABudget, Experiment, MappingPolicy
from behave.ppa.records import read_document, read_log, write_document
from behave.ppa.workflow import SearchArchive


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_eda_budget(value):
    with pytest.raises(ValueError):
        EDABudget(synthesis_seconds=value)
    with pytest.raises(ValueError):
        EDABudget(rss_bytes=value)


def test_eda_memory_budget_roundtrips_without_changing_default_or_deadline():
    original = experiment()
    expanded = replace(original, eda_budget=replace(original.eda_budget, rss_bytes=32 << 30))
    assert Experiment.from_dict(expanded.as_dict()) == expanded
    assert expanded.identity != original.identity
    assert expanded.eda_budget.service_seconds == original.eda_budget.service_seconds
    assert original.eda_budget.rss_bytes == defaults.SIM_EDA_RSS_BYTES
    legacy = original.as_dict()
    assert "rss_bytes" not in legacy["eda_budget"]
    assert Experiment.from_dict(legacy) == original
    explicit = {**legacy, "eda_budget": {**legacy["eda_budget"], "rss_bytes": defaults.SIM_EDA_RSS_BYTES}}
    assert Experiment.from_dict(explicit).identity == original.identity


def test_eda_budget_is_explicit_frozen_and_independent_of_replay_deadline():
    exp = replace(experiment(), eda_budget=EDABudget(721, 41, 43, 90))
    assert exp.eda_budget.service_seconds == 895
    assert exp.service_seconds == 240
    assert Experiment.from_dict(exp.as_dict()) == exp
    assert exp.identity != experiment().identity
    assert "log_bytes" not in exp.as_dict()["eda_budget"]


@pytest.mark.parametrize("synthesis_status,expected", [("ok", "measured"), ("timeout", "timeout"),
    ("exit", "failed"), ("output-limit", "output-limit"), ("missing", "failed")])
@pytest.mark.parametrize("repair_status", [None, "ok", "incomplete"])
def test_configured_stage_deadlines_and_synthesis_dependency(tmp_path, monkeypatch, synthesis_status, expected, repair_status):
    from behave.ppa import mapping_repair
    monkeypatch.setattr(mapping_repair, "runtime", lambda **kwargs: SimpleNamespace(
        executable=object(), mounts=(), identity="repair-fixture"))
    budget = EDABudget(721, 41, 43, 90, rss_bytes=32 << 30)
    monkeypatch.setattr(eda, "binary", lambda *a: object())
    monkeypatch.setattr(eda, "executable_script", lambda *a: object())
    monkeypatch.setattr(eda, "read_only", lambda *a, **kw: SimpleNamespace(identity=lambda: "library"))
    monkeypatch.setattr(eda, "yosys_script", lambda *a, **kw: "fixed synthesis")
    monkeypatch.setattr(eda, "mapped_area", lambda *a: 42.)
    monkeypatch.setattr(eda, "timing_result", lambda *a: {"status": "met"})
    monkeypatch.setattr(eda, "command_stream", lambda *a: b"fixture tool log")
    monkeypatch.setattr(eda.os, "sched_getaffinity", lambda pid: {7, 8, 9, 10})
    seen = []

    def execute(directory, groups, *, outputs, role, read_only_mounts, limits):
        command = groups[0].commands[0]
        seen.append(command)
        assert limits.wall_seconds >= command.timeout_s
        assert limits.rss_bytes == 32 << 30
        outputs = {} if synthesis_status == "missing" else {name: b"fixture" for name in outputs}
        if "abc.log" in outputs:
            outputs["abc.log"] = b"[1.250s] + &fraig -x\n"
        if "repair.log" in outputs:
            outputs["repair.log"] = b"BEHAVE_MAPPING_REPAIR_COMPLETE\n" if repair_status == "ok" else b"Error: repair failed\n"
        for name, data in outputs.items():
            (directory / name).write_bytes(data)
        row = {"id": command.identifier, "returncode": -9 if synthesis_status == "timeout" else
               1 if synthesis_status == "exit" else 0,
               "timed_out": synthesis_status == "timeout", "output_limited": synthesis_status == "output-limit"}
        return SimpleNamespace(status=IsolationStatus.COMPLETED, commands=(row,), outputs=outputs,
                               as_dict=lambda: {"commands": [row]})

    prepared = []

    def prepare(profile, *, binaries, mounts):
        prepared.append((profile, binaries, mounts))
        return SimpleNamespace(identity="fixture", execute=execute)

    monkeypatch.setattr(eda, "prepare_tool_environment", prepare)
    library = tmp_path / "cells.lib"
    library.write_bytes(b'library ("sky130_fd_sc_hd__tt_fixture") {\n'
        b'time_unit : "1ns";\ncapacitive_load_unit(1, "pf");\n'
        b'cell ("sky130_fd_sc_hd__buf_2") {\n}\n}\n')
    source = SimpleNamespace(files=(), artifact=SimpleNamespace(entry_symbol="M"))
    result = eda.measure(source, experiment().timing, liberty=library, sta=Path("/fixture/sta"),
        directory=tmp_path / "eda", objects=DirectoryStore(tmp_path / "objects"),
        power_policy=experiment().power, constant_inputs={"rst": 0}, budget=budget,
        mapping=MappingPolicy(repair_design=repair_status is not None))
    if synthesis_status == "ok" and repair_status == "incomplete":
        expected = "failed"
    assert result["status"] == expected
    assert len(prepared) == 1
    assert prepared[0][0] == "eda"
    assert len(prepared[0][1]) == 3 + (repair_status is not None)
    assert seen[0].timeout_s == 721
    assert dict(seen[0].environment)["YOSYS_MAX_THREADS"] == "4"
    if expected != "measured":
        assert len(seen) == 1
        assert result["failed_stage"] == "synthesis"
        assert result["area_um2"] is None
        assert result["stages"]["timing"]["status"] == "not-run"
        assert result["stages"]["power"]["status"] == "not-run"
        if synthesis_status in {"timeout", "exit", "output-limit"}:
            assert result["stages"]["synthesis"]["last_operation"] == "&fraig -x"
            assert "[1.250s] + &fraig -x" in result["log"]
    else:
        assert [command.timeout_s for command in seen] == [721, 41, 43]
        assert result["area_um2"] == 42.
    if repair_status is not None:
        assert "mapping_repair" in result
        if synthesis_status != "missing":
            assert "repair failed" in result["log"] or "BEHAVE_MAPPING_REPAIR_COMPLETE" in result["log"]


def test_large_history_uses_references_and_exports_complete_closure(tmp_path, monkeypatch):
    log = "own synthesis log\n" * 3000
    pool = ({"fixture_input": "payload" * 7000},)

    class LargeNative(FakeNative):
        def run(self, inputs, exp, mode, rows, objects):
            if mode == "eda":
                result = super().run(inputs, exp, mode, rows, objects)
                result["eda"]["log"] = log
                return result
            return {"score": 1, "pool": list(rows)}

    archive, key = archive_with_candidate(tmp_path / "run", LargeNative())
    monkeypatch.setattr(defaults, "PPA_DOCUMENT_CHUNK_BYTES", 8192)
    archive.pool = pool
    for _ in range(8):
        archive.action(key, "final")
        archive.action(key, "eda")
    candidate = archive.candidates[key]
    assert "log" not in candidate["eda"]
    assert read_log(archive.run.objects, candidate["eda"]) == log
    assert all("pool" not in item["result"] and "eda" not in item["result"] for item in candidate["history"])
    latest = BlobRef.from_dict(thaw_json(archive.events[-1].payload)["record"])
    snapshot = read_document(archive.run.objects, latest)
    assert "pool" not in snapshot and "pool_ref" in snapshot
    assert len(canonical_json(snapshot)) < 15000
    restored = SearchArchive(archive.run, archive.task, archive.experiment, LargeNative())
    assert restored.candidates == archive.candidates and restored.pool == pool
    head, closure = archive.run.seal("measurements")
    copied = DirectoryStore(tmp_path / "copy")
    copied.publish(archive.run.objects, closure)
    assert load_chain(copied, head) == archive.events
    for item in candidate["history"]:
        result = read_document(copied, BlobRef.from_dict(item["record"]))
        if "pool_ref" in result:
            assert read_document(copied, BlobRef.from_dict(result["pool_ref"])) == list(pool)
        if "eda" in result:
            assert read_log(copied, result["eda"]) == log


@pytest.mark.parametrize("chunk_bytes", [4 << 20, 4096])
def test_document_verifies_content_and_retains_chunks(tmp_path, monkeypatch, chunk_bytes):
    monkeypatch.setattr(defaults, "PPA_DOCUMENT_CHUNK_BYTES", chunk_bytes)
    objects = DirectoryStore(tmp_path / "objects")
    value = {"text": "\u91cf\u5316" * 8000}
    ref, closure = write_document(objects, value)
    if chunk_bytes == 4 << 20:
        assert len(closure) == 2
    else:
        assert len(closure) > 2
    copied = DirectoryStore(tmp_path / "copied")
    copied.publish(objects, closure)
    assert read_document(copied, ref) == value
    # An altered descriptor must not make valid chunks authenticate other data.
    import json
    descriptor = json.loads(objects.read(ref))
    descriptor["content"]["sha256"] = "0" * 64
    bad = objects.put(canonical_json(descriptor))
    with pytest.raises(ValueError, match="content mismatch"):
        read_document(objects, bad)


def test_preflight_rechecks_remain_outside_search_budget(tmp_path):
    archive, key = archive_with_candidate(tmp_path)
    archive.action(key, "check", final=True)
    assert len(archive.candidates[key]["history"]) == 1
    assert all(item["final"] for item in archive.candidates[key]["history"])
    assert archive.calls == 0


def test_optional_sat_sharing_is_frozen_and_does_not_skip_mapping():
    from test_ppa import RTL, file

    from behave.core.artifacts import LoadedSource, SourceArtifact
    item = file("top.sv", RTL)
    source = LoadedSource(SourceArtifact((item.reference,), "top.sv", "M"), (item,))
    normal = eda.yosys_script(source, experiment().timing)
    bounded = eda.yosys_script(source, experiment().timing, sat_resource_sharing=False)
    assert bounded.replace(" -noshare", "") == normal
    assert "dfflibmap" in bounded and "abc -exe" in bounded and "check -assert" in bounded
    assert "read_verilog -sv -nosynthesis" in bounded
    # Cell-area/STA authority is exported before the independent logical view is
    # flattened. Never resynthesize the area netlist from a simulation shortcut.
    assert bounded.index("write_verilog -noattr -noexpr -simple-lhs mapped.v") < bounded.index("yosys flatten\n")
    assert ("yosys flatten\nyosys rename -hide\nyosys clean -purge\nyosys check -assert\n"
            "yosys write_verilog -noattr simulation.v") in bounded
    assert bounded.index("write_verilog -noattr -noexpr -simple-lhs mapped.v") < bounded.index("rename -hide")
    exp = replace(experiment(), sat_resource_sharing=False)
    assert Experiment.from_dict(exp.as_dict()) == exp
    assert exp.identity != experiment().identity
    with pytest.raises(TypeError):
        replace(exp, sat_resource_sharing=0)


def test_mapping_constraints_use_abc_units_and_enter_experiment_identity():
    from test_ppa import RTL, file

    from behave.core.artifacts import LoadedSource, SourceArtifact

    exp = experiment()
    item = file("top.sv", RTL)
    source = LoadedSource(SourceArtifact((item.reference,), "top.sv", "M"), (item,))
    assert eda.mapping_constraints(exp.timing, exp.mapping) == (
        "set_driving_cell sky130_fd_sc_hd__buf_2\nset_load 10\n")
    script = eda.yosys_script(source, exp.timing)
    assert "-constr abc.constr -D 20000\n" in script
    assert " -dff" not in script
    other = replace(exp, mapping=MappingPolicy("sky130_fd_sc_hd__buf_4"))
    assert Experiment.from_dict(other.as_dict()) == other
    assert exp.identity != other.identity
    timing = replace(exp.timing, period_ns=12.5, output_load_pf=.0125)
    assert "-D 12500\n" in eda.yosys_script(source, timing)
    assert "set_load 12.5\n" in eda.mapping_constraints(timing, exp.mapping)
    with pytest.raises(ValueError):
        MappingPolicy("buf; quit")
    with pytest.raises(TypeError):
        replace(exp, mapping={})


def test_mapping_driver_must_exist_in_trusted_library():
    policy = MappingPolicy("BUF")
    for declaration in (b'cell ("BUF") {\n}\n', b'cell (BUF) {\n}\n'):
        eda.validate_mapping_library(declaration, policy)
        with pytest.raises(ValueError, match="unique Liberty cell"):
            eda.validate_mapping_library(declaration * 2, policy)
    with pytest.raises(ValueError, match="unique Liberty cell"):
        eda.validate_mapping_library(b'cell ("OTHER_BUF") {\n}\n', policy)


def test_sta_type_view_preserves_exact_bit_connections_and_library():
    from copy import deepcopy

    from behave.ppa._netlist_view import structural_view

    library = {"modules": {"BUF": {"ports": {"A": {"bits": [1], "signed": 1}}}}}
    design = {"modules": {**deepcopy(library["modules"]), "M": {
        "ports": {"input": {"bits": [3, 2], "signed": 1, "upto": 1}},
        "netnames": {"value": {"bits": [3, "1", "x"], "signed": 1}},
        "cells": {"buf": {"type": "BUF", "connections": {"A": [3], "Y": [2]},
                          "parameters": {"untouched": "10"}}},
    }}}
    original = deepcopy(design)
    actual = structural_view(design, library)
    expected = deepcopy(design)
    del expected["modules"]["M"]["ports"]["input"]["signed"]
    del expected["modules"]["M"]["netnames"]["value"]["signed"]
    assert actual == expected
    assert design == original
    for bad in ({"memories": {"ram": {}}}, {"processes": {"proc": {}}},
                {"cells": {"add": {"type": "$add"}}}):
        invalid = deepcopy(design)
        invalid["modules"]["M"].update(bad)
        with pytest.raises(ValueError):
            structural_view(invalid, library)


def test_scope_metadata_is_not_an_electrical_cell():
    import json
    from copy import deepcopy

    from behave.ppa._netlist_view import structural_view
    from behave.ppa.eda import mapped_area

    library = {"modules": {"BUF": {"attributes": {"area": 2.0}}}}
    metadata = {"type": "$scopeinfo", "parameters": {"TYPE": "module"},
                "connections": {}, "port_directions": {}}
    design = {"modules": {**deepcopy(library["modules"]), "M": {
        "ports": {}, "netnames": {}, "cells": {
            "buf": {"type": "BUF", "connections": {"A": [1], "Y": [2]}},
            "provenance": metadata,
        }}}}
    stats = {"modules": {"\\M": {"area": 2.0}}}
    encode = lambda value: json.dumps(value).encode()
    assert mapped_area(encode(design), encode(stats), "M", encode(library)) == 2.0
    view = structural_view(design, library)
    assert view["modules"]["M"]["cells"] == {"buf": design["modules"]["M"]["cells"]["buf"]}
    assert "provenance" in design["modules"]["M"]["cells"]
    for change in ({"connections": {"Y": [2]}}, {"port_directions": {"Y": "output"}},
                   {"type": "$add"}):
        invalid = deepcopy(design)
        invalid["modules"]["M"]["cells"]["provenance"].update(change)
        with pytest.raises(ValueError):
            structural_view(invalid, library)
        with pytest.raises(ValueError):
            mapped_area(encode(invalid), encode(stats), "M", encode(library))
