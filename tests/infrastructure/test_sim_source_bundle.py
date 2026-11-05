"""Multi-file artifacts use the same concrete evaluator and analysis boundary."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from test_data_checks import comparison
from test_judge import RTL

from behave.core.artifacts import BlobRef, FileData, FileRef, LoadedSource, SourceArtifact
from behave.core.records import canonical_json, parse_json
from behave.data.checks import check_sources
from behave.sim import api
from behave.task_execution.analysis import AnalysisOptions
from behave.task_execution.comparison import compare_sources


@pytest.mark.parametrize("entry", ["top.sv", "result", "build/top.sv", "generation/source.v"])
@pytest.mark.parametrize("extra_unit", [False, True])
def test_rtl_preprocessing_is_independent_of_file_count(tmp_path, monkeypatch, entry, extra_unit):
    from behave.task_execution.comparison import ComparisonRequest
    from behave.task_execution.simulator_judge import _SimulatorService

    raw = b"`define IDENTITY in_a\n" + RTL.replace(b"assign out_y = in_a;", b"assign out_y = `IDENTITY;")
    inputs, journal = comparison(tmp_path, component="rtl", real=True, rtl=raw)
    files = {entry: raw}
    if extra_unit:
        files["leaf.sv"] = b"module Leaf; endmodule\n"
    loaded = tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in sorted(files.items()))
    source = LoadedSource(SourceArtifact(tuple(file.reference for file in loaded), entry, "M"), loaded)
    inputs = replace(inputs, source=source)
    work = tmp_path / "execution"
    work.mkdir()
    calls = []
    prepared = SimpleNamespace(source=RTL.decode(), as_dict=lambda: {"fixture": "preprocessed"})

    def preprocess(bundle):
        assert dict(bundle.files) == files
        assert bundle.compilation_units[-1] == entry
        calls.append("preprocess")
        return prepared

    def realization(text, **kwargs):
        assert text == prepared.source
        calls.append("realization")
        return object()

    def prepare(task, config, generation):
        # Stop at the backend boundary: the same bytes must reach both consumers,
        # and submitted path names never become coordinator output directories.
        assert (work / "prepared.sv").read_text() == prepared.source
        calls.append("prepare")
        raise api.Refused(api.Refusal("fixture-stop", "backend boundary reached"))

    monkeypatch.setattr(api, "load_reference", lambda *args, **kwargs: object())
    monkeypatch.setattr(api, "prepare_rtl_bundle", preprocess)
    monkeypatch.setattr(api, "realization_of_rtl_source", realization)
    monkeypatch.setattr(api, "admit", lambda *args, **kwargs: object())
    monkeypatch.setattr(api, "prepare_rtl", prepare)
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work, 0), journal.objects)
    assert calls == ["preprocess", "realization", "prepare"]
    assert result.code == "judge-simulator-refused"
    assert journal.objects.read(result.evidence[0]) == RTL
    assert parse_json(journal.objects.read(result.evidence[1])) == prepared.as_dict()
    assert {file.reference.path: file.data for file in source.files} == files
    for name in ("result", "build", "generation"):
        (work / name).mkdir()


def test_rtl_bundle_identity_binds_all_bytes_and_order():
    files = {"a.sv": b"module a; endmodule", "top.sv": b"module top; endmodule", "inc/value.svh": b"x"}
    bundle = api.RTLSourceBundle(files, ("a.sv", "top.sv"), (".", "inc"))
    files["inc/value.svh"] = b"changed"
    assert bundle.files["inc/value.svh"] == b"x"
    assert replace(bundle, files=files).identity != bundle.identity
    assert replace(bundle, compilation_units=("top.sv", "a.sv")).identity != bundle.identity
    assert replace(bundle, include_dirs=("inc", ".")).identity != bundle.identity
    for bad in ("../secret.sv", "/tmp/secret.sv", "a/../../b"):
        with pytest.raises(api.FailureRaised) as error:
            api.RTLSourceBundle({bad: b"x"}, (bad,))
        assert error.value.failure.disposition is api.FailureDisposition.SCORE_ZERO
    with pytest.raises(api.FailureRaised) as error:
        replace(bundle, compilation_units=("missing.sv",))
    assert error.value.failure.disposition is api.FailureDisposition.SCORE_ZERO


def test_rtl_entry_build_order_is_closed_not_arbitrary_flags():
    files = {"top.sv": b"top", "lib.sv": b"lib", "inc/width.svh": b"header",
             "rtl.f": b"# explicit package order\nlib.sv\ntop.sv\n"}
    bundle = api.bundle_for_entry(files, "top.sv")
    assert bundle.compilation_units == ("lib.sv", "top.sv")
    assert bundle.include_dirs == (".", "inc")
    assert api.bundle_for_entry({key: value for key, value in files.items() if key != "rtl.f"},
                                "top.sv").compilation_units == bundle.compilation_units
    with pytest.raises(api.FailureRaised) as error:
        api.bundle_for_entry({**files, "rtl.f": b"-I/etc\ntop.sv"}, "top.sv")
    assert error.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert error.value.failure.kind == "candidate-rtl-build-invalid"


@pytest.mark.parametrize("manifest", [b"", b"missing.sv\ntop.sv", b"top.sv\ntop.sv", b"../x\ntop.sv", b"\xff"])
def test_public_rtl_manifest_errors_are_explicit_candidate_failures(manifest):
    with pytest.raises(api.FailureRaised) as error:
        api.bundle_for_entry({"top.sv": b"module M; endmodule", "rtl.f": manifest}, "top.sv")
    assert error.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert error.value.failure.disposition is api.FailureDisposition.SCORE_ZERO
    assert error.value.failure.kind == "candidate-rtl-build-invalid"


@pytest.mark.parametrize("error,origin", [(ValueError("internal bug"), api.FailureOrigin.VERIFIER),
    (TypeError("internal bug"), api.FailureOrigin.VERIFIER),
    (OSError("service unavailable"), api.FailureOrigin.INFRASTRUCTURE)])
def test_unexpected_rtl_bundle_builder_errors_are_not_source_rejections(monkeypatch, error, origin):
    from behave.sim.rtl import source_bundle

    def fail(*args, **kwargs):
        raise error

    # Inject at construction, after the manifest has been read and validated.
    # Catching constructor ValueError/TypeError as candidate faults fails this test.
    monkeypatch.setattr(source_bundle, "RTLSourceBundle", fail)
    with pytest.raises(api.FailureRaised) as failure:
        api.bundle_for_entry({"top.sv": b"module M; endmodule"}, "top.sv")
    assert failure.value.failure.origin is origin
    assert failure.value.failure.disposition is api.FailureDisposition.NO_RESULT


def multifile_inputs(tmp_path, expression="a"):
    top = RTL.replace(b"assign out_y = in_a;", b"Leaf leaf(in_a, out_y);")
    assert top != RTL
    helper = ("`include \"width.svh\"\nmodule Leaf(input wire [`W-1:0] a, output wire [`W-1:0] y);\n"
              f"assign y = {expression};\nendmodule\n").encode()
    inputs, journal = comparison(tmp_path, component="rtl", real=True, rtl=top)
    files = {"top.sv": top, "lib/leaf.sv": helper, "inc/width.svh": b"`define W 8\n"}
    loaded = tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in sorted(files.items()))
    source = LoadedSource(SourceArtifact(tuple(file.reference for file in loaded), "top.sv", "M"), loaded)
    return replace(inputs, source=source), journal


@pytest.mark.isolation
@pytest.mark.parametrize("manifest", [b"missing.sv\ntop.sv", b"\xff"])
def test_real_bad_rtl_manifest_is_candidate_zero_before_compilation(tmp_path, manifest):
    inputs, journal = multifile_inputs(tmp_path)
    extra = FileData(FileRef("rtl.f", BlobRef.of(manifest)), manifest)
    files = tuple(sorted((*inputs.source.files, extra), key=lambda file: file.reference.path))
    source = LoadedSource(replace(inputs.source.artifact, files=tuple(file.reference for file in files)), files)
    inputs = replace(inputs, source=source)
    report = check_sources(inputs, journal=journal)
    assert report.result.score == 0 and report.result.origin == "candidate", report.as_dict()
    assert report.result.code == "candidate-rtl-build-invalid"
    assert report.result.cleanup.quiescent
    assert check_sources(inputs, journal=journal) == report


@pytest.mark.isolation
@pytest.mark.eda
@pytest.mark.parametrize("expression,score", [("a", 1), ("a ^ 8'hff", 0)])
def test_multifile_rtl_real_score_retains_dependencies(tmp_path, expression, score):
    inputs, journal = multifile_inputs(tmp_path, expression)
    result = check_sources(inputs, journal=journal)
    assert result.result.score == score, canonical_json(result.result.as_dict()).decode()
    native = parse_json(journal.objects.read(result.result.evidence[0]))
    assert native["result"]["decision"]["authority"]["passed"] == bool(score)
    assert check_sources(inputs, journal=journal) == result


@pytest.mark.isolation
@pytest.mark.eda
@pytest.mark.solver
def test_multifile_rtl_analysis_binds_the_same_concrete_result(tmp_path):
    inputs, journal = multifile_inputs(tmp_path)
    policy = replace(inputs.policy, analysis=AnalysisOptions(300, 3000, 3000, 8))
    inputs = replace(inputs, judge_policy=policy.to_bytes())
    with TemporaryDirectory(prefix="behave-bundle-analysis-", dir="/tmp") as workdir:
        result = compare_sources(inputs, objects=journal.objects, workdir=Path(workdir))
    assert result.score == 1, canonical_json(result.as_dict()).decode()
    assert result.analysis is not None and result.analysis.status == "returned", result.as_dict()
    native = parse_json(journal.objects.read(result.evidence[0]))
    assert native["result"]["analysis"] is None
    assert result.analysis.concrete == native["result"]["decision"]["authority"]["evaluation_fingerprint"]
    outcome = parse_json(journal.objects.read(result.analysis.outcome))
    assert outcome["verdict"] == "classified" and outcome["per_goal"], outcome


@pytest.mark.isolation
@pytest.mark.eda
def test_multifile_rtl_missing_header_is_a_source_failure(tmp_path):
    bundle = api.RTLSourceBundle({"top.sv": b'`include "missing.svh"\nmodule M; endmodule'}, ("top.sv",))
    with pytest.raises(api.FailureRaised) as failure:
        api.prepare_rtl_bundle(bundle)
    assert failure.value.failure.disposition is api.FailureDisposition.SCORE_ZERO


@pytest.mark.parametrize("candidate", [False, True])
def test_multifile_frontend_has_the_same_failure_attribution(monkeypatch, candidate):
    from behave.sim.rtl import source_bundle
    from behave.sim.rtl.frontend_error import SourceRejected

    def failed(*args, **kwargs):
        if candidate:
            raise SourceRejected(api.Refusal("rtl-source-rejected", "bad source"))
        raise FileNotFoundError("missing tool")

    monkeypatch.setattr(source_bundle, "prepare_rtl_bundle", failed)
    bundle = api.RTLSourceBundle({"top.sv": b"module M; endmodule"}, ("top.sv",))
    with pytest.raises(api.FailureRaised) as failure:
        api.prepare_rtl_bundle(bundle)
    expected = api.FailureDisposition.SCORE_ZERO if candidate else api.FailureDisposition.NO_RESULT
    assert failure.value.failure.disposition is expected


def python_inputs(tmp_path, *, wrong=False, reference=False):
    inputs, journal = comparison(tmp_path, real=True)
    if reference:
        entry = b"from helpers.ops import f\nclass Identity:\n def reset(self): pass\n def process(self, in_a): return {'out_y': f(in_a)}\n"
        model = "Identity"
    else:
        entry = b"from helpers.ops import f\nclass M:\n def reset(self): pass\n def process(self, a): return {'y': f(a)}\n"
        model = "M"
    # The two-case fixture baseline samples zero, not every possible input.
    # This mutation must disagree there too; otherwise score 1 is correct for
    # that finite pool and says nothing about the unvisited nonzero branch.
    helper = b"K = 1\ndef f(a):\n if a == 0: return K\n return (a ^ K) & 255\n" if wrong else (
        b"K = 255\ndef f(a):\n if a == 0: return 0\n return a & K\n")
    files = {"primary.py": entry, "helpers/ops.py": helper, "helpers/__init__.py": b""}
    loaded = tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in sorted(files.items()))
    source = LoadedSource(SourceArtifact(tuple(file.reference for file in loaded), "primary.py", model), loaded)
    return replace(inputs, **{"reference_behavior" if reference else "source": source}), journal


@pytest.mark.isolation
@pytest.mark.parametrize("wrong", [False, True])
def test_multifile_python_score_and_cold_replay(tmp_path, wrong):
    inputs, journal = python_inputs(tmp_path, wrong=wrong)
    result = check_sources(inputs, journal=journal)
    assert result.result.score == int(not wrong), result.as_dict()
    native = parse_json(journal.objects.read(result.result.evidence[0]))
    assert native["result"]["decision"]["authority"]["passed"] is (not wrong)
    receipt = parse_json(journal.objects.read(result.result.evidence[2]))
    assert receipt["schema"] == "behave.prepared-behavior-source/2"
    assert receipt["model"] == "M"
    assert "helpers/ops.py" in receipt["modules"]
    assert check_sources(inputs, journal=journal) == result


@pytest.mark.isolation
def test_multifile_python_reference_and_explicit_entry(tmp_path):
    inputs, journal = python_inputs(tmp_path, reference=True)
    result = check_sources(inputs, journal=journal)
    assert result.result.score == 1, result.as_dict()
    receipt = parse_json(journal.objects.read(result.result.evidence[2]))
    assert receipt["schema"] == "behave.prepared-behavior-source/2" and receipt["model"] == "Identity"


@pytest.mark.isolation
@pytest.mark.solver
def test_multifile_python_analysis_uses_the_same_concrete_source(tmp_path):
    inputs, journal = python_inputs(tmp_path)
    policy = replace(inputs.policy, analysis=AnalysisOptions(300, 3000, 3000, 8))
    inputs = replace(inputs, judge_policy=policy.to_bytes())
    with TemporaryDirectory(prefix="behave-python-bundle-analysis-", dir="/tmp") as workdir:
        result = compare_sources(inputs, objects=journal.objects, workdir=Path(workdir))
    assert result.score == 1, result.as_dict()
    assert result.analysis is not None and result.analysis.status == "returned", result.as_dict()
    native = parse_json(journal.objects.read(result.evidence[0]))
    assert result.analysis.concrete == native["result"]["decision"]["authority"]["evaluation_fingerprint"]
    outcome = parse_json(journal.objects.read(result.analysis.outcome))
    assert outcome["verdict"] == "classified" and outcome["per_goal"], outcome


@pytest.mark.isolation
@pytest.mark.parametrize("reference", [False, True])
def test_multifile_python_capability_refusal_is_not_score_zero(tmp_path, reference):
    inputs, journal = python_inputs(tmp_path, reference=reference)
    source = inputs.reference_behavior if reference else inputs.source
    loaded = tuple(FileData(FileRef(file.reference.path, BlobRef.of(b"import os\n")), b"import os\n")
                   if file.reference.path == "helpers/ops.py" else file for file in source.files)
    source = replace(source, artifact=replace(source.artifact, files=tuple(file.reference for file in loaded)), files=loaded)
    inputs = replace(inputs, **{"reference_behavior" if reference else "source": source})
    result = check_sources(inputs, journal=journal)
    assert result.result.score is None, result.as_dict()
    assert result.result.code == ("judge-reference-unavailable" if reference else "judge-simulator-refused")
