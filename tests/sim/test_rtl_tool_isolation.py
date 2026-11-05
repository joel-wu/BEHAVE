from __future__ import annotations

import ast
import base64
import io
import json
import os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from behave.core import runtime_defaults as defaults
from behave.isolation import (
    IsolationRefusal,
    IsolationResult,
    IsolationStatus,
    MaskedPath,
    binary,
    capabilities,
    read_only,
)
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_protocol, tool_runtime
from behave.sim.strict_json import strict_json_loads


@pytest.fixture(autouse=True)
def runtime_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime-batch"))


def _runtime(tmp_path):
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    return ((binary(str(executable), "/runtime/python"),), (), ())


def _completed(
    role="job",
    *,
    commands=(),
    outputs=None,
    trees=None,
):
    return tool_protocol.ToolExecution(
        role,
        IsolationStatus.COMPLETED,
        "1" * 64,
        "backend",
        "2" * 64,
        "3" * 64,
        tuple(MappingProxyType(dict(row)) for row in commands),
        MappingProxyType(dict(outputs or {})),
        MappingProxyType(dict(trees or {})),
    )


def _command_row(identifier, *, returncode=0, timed_out=False, stderr=b""):
    return {
        "id": identifier,
        "returncode": returncode,
        "timed_out": timed_out,
        "output_limited": False,
        "stdout": base64.b64encode(b"").decode("ascii"),
        "stderr": base64.b64encode(stderr).decode("ascii"),
    }


@pytest.mark.parametrize("outcome", ["clean", "timeout", "oom", "worker-error", "bad-wire", "cleanup"])
def test_checkpoint_budget_stop_cannot_hide_backend_failure(tmp_path, monkeypatch, outcome):
    from types import SimpleNamespace

    from behave.sim.scoring_policy import ScoringStopped

    monkeypatch.setattr(tool_runtime, "_runtime", lambda profile: _runtime(tmp_path))
    frozen = tool_runtime.freeze_runtime("simulation")
    groups = (tool_protocol.ToolGroup((tool_protocol.ToolCommand("probe", ("/runtime/python",)),)),)
    failure = ScoringStopped()
    service = SimpleNamespace(failure=failure, run=lambda execute, directory: execute())

    def execute(spec):
        if outcome == "oom":
            return IsolationResult(IsolationStatus.MEMORY_LIMIT, spec.digest, "test")
        if outcome == "timeout":
            return IsolationResult(IsolationStatus.TIMED_OUT, spec.digest, "test")
        row = _command_row("probe", returncode=1 if outcome == "worker-error" else 0)
        wire = {"version": tool_protocol.JOB_VERSION, "commands": [row], "outputs": {}, "trees": {}}
        if outcome == "cleanup":
            row["cleanup_error"] = "not confirmed"
            wire = {"version": tool_protocol.JOB_VERSION, "commands": [row], "cleanup_failed": True}
        payload = b"broken" if outcome == "bad-wire" else json.dumps(wire).encode()
        return IsolationResult(IsolationStatus.COMPLETED, spec.digest, "test", stdout=payload)

    monkeypatch.setattr(tools, "run", execute)
    call = lambda: tools.run_workspace(tmp_path, groups, role="checkpoint",
        runtime_profile="simulation", frozen_runtime=frozen, _checkpoint_service=service)
    if outcome in {"clean", "timeout"}:
        with pytest.raises(ScoringStopped):
            call()
    else:
        result = call()
        if outcome == "oom":
            assert result.status is IsolationStatus.MEMORY_LIMIT
        elif outcome == "worker-error":
            assert result.commands[0]["returncode"] == 1
        else:
            assert result.status is IsolationStatus.REFUSED
            assert result.refusal.code == ("rtl-tool-cleanup-unconfirmed" if outcome == "cleanup"
                                           else "rtl-tool-protocol-invalid")


def test_guest_json_retries_unbuffered_short_writes(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.rtl import _tool_job

    class ShortWriter(io.BytesIO):
        def write(self, payload):
            return super().write(payload[:7])

    document = {"version": _tool_job._PROTOCOL, "outputs": {"trace": "x" * 100},
                "commands": [], "trees": {}}
    # This is the old print-on--u failure, without allocating gigabytes.
    old = ShortWriter()
    text = io.TextIOWrapper(old, write_through=True)
    print(json.dumps(document), file=text, flush=True)
    with pytest.raises(json.JSONDecodeError):
        json.loads(old.getvalue())
    raw = ShortWriter()
    monkeypatch.setattr(_tool_job.sys, "stdout", SimpleNamespace(buffer=raw))
    _tool_job._write_json(document)
    assert raw.getvalue() == json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    assert tool_protocol.decode_protocol(raw.getvalue()) == document


def test_guest_json_does_not_report_success_after_broken_write(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.rtl import _tool_job

    stream = SimpleNamespace(write=lambda _: 0)
    monkeypatch.setattr(_tool_job.sys, "stdout", SimpleNamespace(buffer=stream))
    with pytest.raises(BrokenPipeError):
        _tool_job._write_json({"version": _tool_job._PROTOCOL})


def test_runtime_content_identity_includes_masked_paths(tmp_path, monkeypatch):
    binaries, mounts, _ = _runtime(tmp_path)
    masks = (MaskedPath("/runtime/python/lib/private"),)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda profile: (binaries, mounts, masks))
    first = tool_runtime.freeze_runtime().identity
    assert first == tool_runtime.freeze_runtime().identity
    assert first != tool_runtime.freeze_runtime("source-events").identity
    masks = (MaskedPath("/runtime/python/lib/other"),)
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "updated-runtime"))
    assert first != tool_runtime.freeze_runtime().identity


def test_one_frozen_runtime_supplies_identity_and_launch(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda profile: runtime)
    frozen = tool_runtime.freeze_runtime("source-events")
    assert frozen.identity == tool_runtime.freeze_runtime("source-events").identity
    seen = []

    def no_rediscovery(profile):
        pytest.fail("a frozen runtime must not be rediscovered at launch")

    def refused(spec):
        seen.append(spec)
        return IsolationResult(
            IsolationStatus.REFUSED, spec.digest, "unavailable",
            refusal=IsolationRefusal("test-stop", "inspect the submitted runtime"))

    monkeypatch.setattr(tool_runtime, "_runtime", no_rediscovery)
    monkeypatch.setattr(tools, "run", refused)
    groups = (tool_protocol.ToolGroup((tool_protocol.ToolCommand("probe", ("/runtime/python",)),)),)
    execution = tools.run_workspace(tmp_path, groups, role="probe",
                                    runtime_profile="source-events", frozen_runtime=frozen)
    assert execution.refusal.code == "test-stop"
    assert len(seen) == 1 and seen[0].binaries is frozen.binaries
    assert seen[0].masked == frozen.masked
    with pytest.raises(ValueError, match="profile"):
        tools.run_workspace(tmp_path, groups, role="probe", frozen_runtime=frozen)
    with pytest.raises(TypeError, match="FrozenToolRuntime"):
        tools.run_workspace(tmp_path, groups, role="probe", frozen_runtime=object())
    assert len(seen) == 1
    with pytest.raises(AttributeError):
        frozen.profile = "simulation"


@pytest.mark.isolation
@pytest.mark.parametrize("profile", ["eda", "elaboration", "catalogue", "source-catalogue", "source-observation"])
def test_frozen_runtime_still_checks_changed_bytes_at_launch(tmp_path, monkeypatch, profile):
    _needs_namespace()
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda profile: runtime)
    frozen = tool_runtime.freeze_runtime(profile)
    # Bypass discovery after freezing. The actual isolation backend, not a new
    # identity scan or a mocked launcher, must reject these substituted bytes.
    Path(frozen.binaries[0].source).write_text("#!/bin/sh\nprintf changed\n")
    execution = tools.run_workspace(
        tmp_path, (tool_protocol.ToolGroup((tool_protocol.ToolCommand("probe", ("/runtime/python",)),)),),
        role="probe", runtime_profile=profile, frozen_runtime=frozen)
    assert execution.status is IsolationStatus.REFUSED
    assert execution.refusal.code == "isolation-input-changed", execution.as_dict()
    assert not execution.commands


def test_runtime_capability_refusal_is_typed_and_has_no_fallback(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text("module top; endmodule\n")
    monkeypatch.setattr(
        tool_runtime, "_runtime",
        lambda _profile: (_ for _ in ()).throw(FileNotFoundError("yosys")),
    )

    execution = tools.run_workspace(
        workspace,
        (tool_protocol.ToolGroup((tool_protocol.ToolCommand("compile", ("/tools/yosys",)),)),),
        role="compile",
    )

    assert execution.status is IsolationStatus.REFUSED
    assert execution.refusal.code == "rtl-tool-runtime-unavailable"
    with pytest.raises(tool_protocol.ToolIsolationError) as caught:
        tool_protocol.require_completed(execution)
    assert caught.value.execution is execution


def test_identical_shared_loader_is_mounted_once_but_conflicts_remain(tmp_path):
    loader = tmp_path / "loader"
    loader.write_bytes(b"system loader")
    mount = read_only(str(loader), "/lib64/ld.so")
    duplicate = read_only(str(loader), "/lib64/ld.so")
    assert tool_runtime._without_redundant_mounts((mount, duplicate)) == (mount,)
    other = tmp_path / "other-loader"
    other.write_bytes(b"different loader")
    conflict = read_only(str(other), mount.target)
    assert tool_runtime._without_redundant_mounts((mount, conflict)) == (mount, conflict)


def test_a_broad_runtime_tree_replaces_only_its_exact_redundant_mount(tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    exact = library / "libexact.so"
    exact.write_bytes(b"exact")
    other = tmp_path / "other.so"
    other.write_bytes(b"other")
    broad = read_only(str(library), "/lib", allow_symlinks=True)
    redundant = read_only(str(exact), "/lib/libexact.so")
    distinct = read_only(str(other), "/lib/libother.so")

    got = tool_runtime._without_redundant_mounts((redundant, broad, distinct))

    assert got == (broad, distinct)


def test_local_copy_is_redundant_when_parent_supplies_identical_bytes(tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    (library / "libexact.so").write_bytes(b"exact")
    local = tmp_path / "copied.so"
    local.write_bytes(b"exact")
    broad = read_only(str(library), "/lib")
    leaf = read_only(str(local), "/lib/libexact.so")
    assert tool_runtime._without_redundant_mounts((leaf, broad)) == (broad,)
    local.write_bytes(b"different")
    changed = read_only(str(local), leaf.target)
    assert tool_runtime._without_redundant_mounts((changed, broad)) == (changed, broad)


def test_a_digest_bound_in_tree_symlink_can_supply_an_exact_library_leaf(
    tmp_path,
):
    library = tmp_path / "lib"
    library.mkdir()
    implementation = library / "libcrypt.so.1.1.0"
    implementation.write_bytes(b"exact implementation")
    alias = library / "libcrypt.so.1"
    alias.symlink_to(implementation.name)
    broad = read_only(str(library), "/lib", allow_symlinks=True)
    leaf = read_only(str(alias), "/lib/libcrypt.so.1")

    assert tool_runtime._without_redundant_mounts((leaf, broad)) == (broad,)


def test_a_symlink_leaving_a_broad_tree_never_removes_an_exact_leaf(tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    outside = tmp_path / "outside.so"
    outside.write_bytes(b"outside")
    alias = library / "libescape.so"
    alias.symlink_to(outside)
    broad = read_only(str(library), "/lib", allow_symlinks=True)
    leaf = read_only(str(alias), "/lib/libescape.so")

    assert tool_runtime._without_redundant_mounts((leaf, broad)) == (leaf, broad)


def test_backend_capability_refusal_preserves_policy_and_workspace_identity(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text("module top; endmodule\n")
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))

    def refused(spec):
        return IsolationResult(
            IsolationStatus.REFUSED,
            spec.digest,
            "unavailable",
            refusal=IsolationRefusal(
                "isolation-capability-missing", "user namespaces are unavailable"),
        )

    monkeypatch.setattr(tools, "run", refused)
    execution = tools.run_workspace(
        workspace,
        (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
            "compile", ("/runtime/python", "-c", "pass")),)),),
        role="compile",
    )
    assert execution.status is IsolationStatus.REFUSED
    assert execution.refusal.code == "isolation-capability-missing"
    assert execution.policy_digest != execution.job_digest
    assert execution.workspace_digest == tools.content_digest(str(workspace))


def test_tool_failure_telemetry_is_versioned_bounded_and_round_trips(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text("module top; endmodule\n")
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))
    raw_stderr = b"prefix-secret-that-must-not-grow" + b"x" * 9000 + b"\xfftail"

    def exited(spec):
        return IsolationResult(
            IsolationStatus.EXITED,
            spec.digest,
            "backend",
            stderr=raw_stderr,
            exit_code=126,
        )

    monkeypatch.setattr(tools, "run", exited)
    execution = tools.run_workspace(
        workspace,
        (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
            "compile", ("/runtime/python", "-c", "pass")),)),),
        role="compile",
    )
    image = execution.as_dict()

    assert image["version"] == "rtl-tool-execution/5"
    assert image["exit_code"] == 126 and image["signal"] is None
    assert image["stderr"]["sha256"] == __import__("hashlib").sha256(
        raw_stderr).hexdigest()
    assert "prefix-secret" not in image["stderr"]["tail"]
    assert tool_protocol.ToolExecution.validate_image(image) == image
    forged = dict(image, unexpected=True)
    with pytest.raises(ValueError, match="shape or version"):
        tool_protocol.ToolExecution.validate_image(forged)
    malformed_command = dict(image)
    malformed_command["commands"] = [{"id": "compile", "returncode": 0}]
    with pytest.raises(ValueError, match="command rows"):
        tool_protocol.ToolExecution.validate_image(malformed_command)
    escaping_output = dict(image)
    escaping_output["outputs"] = {"../host": "0" * 64}
    with pytest.raises(ValueError, match="outputs"):
        tool_protocol.ToolExecution.validate_image(escaping_output)
    for field, value, message in (
        ("exit_code", -1, "exit code"),
        ("signal", 0, "signal"),
    ):
        malformed_terminal = dict(image)
        malformed_terminal[field] = value
        with pytest.raises(ValueError, match=message):
            tool_protocol.ToolExecution.validate_image(malformed_terminal)


def test_public_realization_preserves_a_typed_isolation_refusal(monkeypatch):
    """Admission must not collapse an isolation capability result into parse failure."""

    from behave.sim import api, contract_of

    execution = tool_protocol.ToolExecution(
        role="contract",
        status=IsolationStatus.REFUSED,
        policy_digest="1" * 64,
        backend_identity="unavailable",
        workspace_digest="2" * 64,
        job_digest="3" * 64,
        refusal=IsolationRefusal(
            "isolation-capability-missing", "user namespaces are unavailable"),
    )

    def unavailable(*_args, **_kwargs):
        raise tool_protocol.ToolIsolationError(
            "isolation-capability-missing",
            "user namespaces are unavailable",
            execution,
        )

    # Fail at elaboration and preserve the original tool evidence publicly.
    monkeypatch.setattr(contract_of, "realization_of_source", unavailable)

    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_rtl_source("module top; endmodule", top="top")
    failure = caught.value.failure
    assert failure.kind == "isolation-capability-missing"
    assert failure.origin is api.FailureOrigin.INFRASTRUCTURE
    assert failure.disposition is api.FailureDisposition.NO_RESULT
    assert failure.reason.as_dict()["detail"]["tool_executions"] == [execution.as_dict()]


def test_protocol_validates_before_replacing_a_declared_output(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / "result.json"
    output.write_text("stale")
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))
    command = tool_protocol.ToolCommand("run", ("/runtime/python", "-c", "pass"))

    def completed(spec):
        assert tuple((device.target, device.writable)
                     for device in spec.devices) == (
            ("/dev/null", True), ("/dev/urandom", False))
        payload = json.dumps({
            "version": "rtl-tool-job/7",
            "commands": [{
                "id": "run", "returncode": 0, "timed_out": False,
                "output_limited": False, "stdout": "", "stderr": "",
            }],
            "outputs": {
                "result.json": base64.b64encode(b"fresh").decode("ascii"),
            },
            "trees": {},
        }).encode()
        return IsolationResult(
            IsolationStatus.COMPLETED, spec.digest, "backend", stdout=payload,
            exit_code=0)

    monkeypatch.setattr(tools, "run", completed)
    execution = tools.run_workspace(
        workspace, (tool_protocol.ToolGroup((command,)),),
        outputs=("result.json",), role="run")
    assert execution.status is IsolationStatus.COMPLETED
    assert output.read_bytes() == b"fresh"
    assert execution.as_dict()["outputs"]["result.json"] == \
        __import__("hashlib").sha256(b"fresh").hexdigest()


def test_simulation_runtime_names_its_frozen_interpreter_for_pygpi(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))
    command = tool_protocol.ToolCommand("simulate", ("/runtime/python", "-c", "pass"))

    def completed(spec):
        environment = dict(spec.environment)
        assert environment["PYGPI_PYTHON_BIN"] == "/runtime/python/bin/python"
        assert environment["LD_LIBRARY_PATH"] == \
            "/runtime/python/lib:/runtime/site/cocotb/libs"
        payload = json.dumps({
            "version": "rtl-tool-job/7",
            "commands": [{
                "id": "simulate", "returncode": 0, "timed_out": False,
                "output_limited": False, "stdout": "", "stderr": "",
            }],
            "outputs": {},
            "trees": {},
        }).encode()
        return IsolationResult(
            IsolationStatus.COMPLETED, spec.digest, "backend", stdout=payload,
            exit_code=0)

    monkeypatch.setattr(tools, "run", completed)
    execution = tools.run_workspace(
        workspace, (tool_protocol.ToolGroup((command,)),), role="simulate",
        runtime_profile="simulation")
    assert execution.status is IsolationStatus.COMPLETED


@pytest.mark.parametrize(
    "payload",
    [
        (b'{"version":"rtl-tool-job/7","commands":[],"outputs":{},'
         b'"outputs":{},"trees":{}}'),
        (b'{"version":"rtl-tool-job/7","commands":[],"outputs":{},'
         b'"trees":{},"extra":0}'),
        (b'{"version":"rtl-tool-job/7","commands":[],"outputs":{},'
         b'"trees":{},"number":NaN}'),
        b'\xff',
    ],
)
def test_rtl_protocol_is_one_strict_exact_json_document(payload):
    with pytest.raises(ValueError, match="malformed|invalid"):
        tool_protocol.decode_protocol(payload)


@pytest.mark.parametrize("payload,position", [
    (b"", 0),
    (b'{"version":', 11),
    (b'{}\n{"private": "hidden-reference"}', 3),
])
def test_malformed_protocol_reports_size_and_position_without_contents(payload, position):
    with pytest.raises(ValueError) as found:
        tool_protocol.decode_protocol(payload)
    message = str(found.value)
    assert f"{len(payload)} bytes; character {position}," in message
    assert "hidden-reference" not in message and "private" not in message


@pytest.mark.parametrize(
    "payload",
    [
        '{"outer":{"value":1,"value":2}}',
        '{"value":NaN}',
        '{"value":Infinity}',
        b'\xff',
    ],
)
def test_submitted_artifact_json_is_strict_at_every_nesting_level(payload):
    with pytest.raises(ValueError):
        strict_json_loads(payload, what="the submitted artifact")


@pytest.mark.parametrize("value", [
    b'{"x":1,"\\u0078":2}', b'NaN', b'Infinity', b'-Infinity',
    b'1e100000', b'{"nested":[{"private_key":0,"private_key":1}]}',
])
def test_host_job_and_tool_readers_reject_the_same_nested_extensions(value):
    from behave.sim.job_isolation import IsolatedJobFailure, _strict_json

    payload = (b'{"version":"rtl-tool-job/7","commands":[],"outputs":{"probe":'
               + value + b'},"trees":{}}')
    with pytest.raises(IsolatedJobFailure) as failed_job:
        _strict_json(payload)
    assert failed_job.value.how == "no-answer"
    with pytest.raises(ValueError) as failed_tool:
        tool_protocol.decode_protocol(payload)
    assert str(failed_tool.value) == "the isolated RTL tool returned malformed JSON"
    assert "private_key" not in str(failed_tool.value)


@pytest.mark.parametrize("value,expected", [
    (b'null', None), (b'true', True), (b'false', False),
    (b'-0.0', -0.0), (b'1e308', 1e308),
    (b'[0,{"x":1,"X":2}]', [0, {"x": 1, "X": 2}]),
    (b'"\\u96ea"', "雪"),
])
def test_host_job_and_tool_readers_preserve_finite_values(value, expected):
    from behave.sim.job_isolation import _strict_json

    payload = (b'{"version":"rtl-tool-job/7","commands":[],"outputs":{"probe":'
               + value + b'},"trees":{}}')
    for decoded in (_strict_json(payload), tool_protocol.decode_protocol(payload)):
        actual = decoded["outputs"]["probe"]
        assert type(actual) is type(expected) and actual == expected


def test_verilator_tree_is_reduced_to_a_closed_port_record_inside_the_judge():
    from behave.sim.rtl import _tool_job

    tree = {
        "nodes": [
            {"type": "BASICDTYPE", "addr": "d8", "range": "7:0",
             "signed": True},
            {"type": "MODULE", "name": "helper", "level": 2,
             "members": [{"type": "VAR", "name": "hidden",
                          "isPrimaryIO": True, "direction": "INPUT",
                          "dtypep": "d8"}]},
            {"type": "MODULE", "name": "top", "level": 1,
             "candidate_secret": "this must not cross the boundary",
             "members": [
                 {"type": "VAR", "name": "a", "isPrimaryIO": True,
                  "direction": "INPUT", "dtypep": "d8"},
                 {"type": "VAR", "name": "y", "isPrimaryIO": True,
                  "direction": "OUTPUT", "dtypep": "d8"},
             ]},
        ],
    }
    reduced = strict_json_loads(
        _tool_job._verilator_ports(
            json.dumps(tree).encode(), {"top": None},
            recursion_limit=defaults.SIM_EDA_NORMALIZATION_RECURSION_LIMIT),
        what="the reduced port record",
    )

    assert reduced == {
        "version": "verilator-ports/1",
        "top": "top",
        "ports": [
            {"name": "a", "direction": "input", "width": 8,
             "signed": True},
            {"name": "y", "direction": "output", "width": 8,
             "signed": True},
        ],
    }
    assert b"candidate_secret" not in json.dumps(reduced).encode()


def test_verilator_normalizer_preserves_an_empty_selected_top():
    from behave.sim.rtl import _tool_job
    from behave.sim.rtl.elaborate import _schema_of_ports

    tree = {"nodes": [
        {"type": "MODULE", "name": "chosen", "members": []},
        {"type": "MODULE", "name": "other", "members": [
            {"type": "VAR", "name": "a", "isPrimaryIO": True, "direction": "INPUT"},
        ]},
    ]}
    record = json.loads(_tool_job._verilator_ports(
        json.dumps(tree).encode(), {"top": "chosen"},
        recursion_limit=defaults.SIM_EDA_NORMALIZATION_RECURSION_LIMIT))
    assert record == {"version": "verilator-ports/1", "top": "chosen", "ports": []}
    schema = _schema_of_ports(record, "candidate", None)
    assert schema.top == "chosen" and schema.endpoints == {}


@pytest.mark.cosim
def test_public_selected_empty_top_has_a_candidate_contract_failure():
    from behave.sim import api
    from tests.sim.test_api_path import contract

    task = contract()
    source = "module chosen; initial $finish; endmodule\nmodule other(input a, output y); assign y=a; endmodule\n"
    realization = api.realization_of_rtl_source(source, top="chosen", contract=task)
    assert not realization.ports
    with pytest.raises(api.FailureRaised) as caught:
        api.admit(task, realization)
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.disposition is api.FailureDisposition.SCORE_ZERO
    assert failure.kind == "admission-missing-payload"


def test_verilator_normalizer_refuses_an_ambiguous_implicit_top():
    from behave.sim.rtl import _tool_job

    tree = {
        "nodes": [
            {"type": "MODULE", "name": "left", "level": 1,
             "members": [{"type": "VAR", "name": "a", "isPrimaryIO": True,
                          "direction": "INPUT"}]},
            {"type": "MODULE", "name": "right", "level": 1,
             "members": [{"type": "VAR", "name": "b", "isPrimaryIO": True,
                          "direction": "INPUT"}]},
        ],
    }

    with pytest.raises(ValueError, match="no unique elaborated top"):
        _tool_job._verilator_ports(json.dumps(tree).encode(), {"top": None},
                                  recursion_limit=defaults.SIM_EDA_NORMALIZATION_RECURSION_LIMIT)

    chosen = strict_json_loads(
        _tool_job._verilator_ports(json.dumps(tree).encode(), {"top": "right"},
                                  recursion_limit=defaults.SIM_EDA_NORMALIZATION_RECURSION_LIMIT),
        what="the selected port record",
    )
    assert chosen["top"] == "right"
    assert [port["name"] for port in chosen["ports"]] == ["b"]


def test_verilator_redundant_member_names_preserve_the_port_record():
    from behave.sim.rtl import _tool_job

    payload = b'''{"nodes":[
        {"type":"MEMBERDTYPE","name":"re","name":"re"},
        {"type":"MEMBERDTYPE","name":"im","name":"im"},
        {"type":"BASICDTYPE","addr":"d32","range":"31:0"},
        {"type":"MODULE","name":"top","level":1,"members":[
          {"type":"VAR","name":"a","isPrimaryIO":true,
           "direction":"INPUT","dtypep":"d32"}]}]}'''
    reduced = _tool_job._verilator_ports(
        payload, {"top": "top"},
        recursion_limit=defaults.SIM_EDA_NORMALIZATION_RECURSION_LIMIT)
    assert json.loads(reduced)["ports"] == [
        {"name": "a", "direction": "input", "width": 32, "signed": False}]
    with pytest.raises(ValueError):
        _tool_job._strict_json(payload, "another tool format")


@pytest.mark.parametrize("members", [
    b'"name":"re","name":"im"',
    b'"name":1,"name":1',
    b'"range":"7:0","range":"7:0"',
    b'"range":"7:0","range":"15:0"',
])
def test_verilator_normalization_does_not_hide_ambiguous_or_other_duplicates(members):
    from behave.sim.rtl import _tool_job

    payload = b'{"nodes":[{"type":"MEMBERDTYPE",' + members + b'}]}'
    with pytest.raises(ValueError):
        _tool_job._verilator_ports(
            payload, {"top": "top"},
            recursion_limit=defaults.SIM_EDA_NORMALIZATION_RECURSION_LIMIT)


def test_output_normalizer_is_versioned_and_must_name_a_declared_output(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))
    command = tool_protocol.ToolCommand("run", ("/runtime/python", "-c", "pass"))
    normalizer = tool_protocol.OutputNormalizer(
        "tree.json", "verilator-ports/1", (("top", None),))

    with pytest.raises(ValueError, match="declared output"):
        tools.run_workspace(
            workspace, (tool_protocol.ToolGroup((command,)),), outputs=(),
            output_normalizers=(normalizer,), role="run")
    with pytest.raises(ValueError, match="unknown RTL output normalizer"):
        tool_protocol.OutputNormalizer("tree.json", "unknown/1", (("top", None),))
    tool_protocol.OutputNormalizer(
        "coverage.dat", "verilator-coverage-points/1")
    with pytest.raises(ValueError, match="takes no arguments"):
        tool_protocol.OutputNormalizer(
            "coverage.dat", "verilator-coverage-points/1", (("source", "x"),))


def test_rtlil_is_reduced_to_closed_site_rows_inside_the_judge():
    from behave.sim.rtl import _tool_job

    rtlil = rb'''module \top
  wire width 1 \candidate_secret
  process $proc
    attribute \src "design.sv:3.1-3.8"
    switch \a
      attribute \src "design.sv:4.1-4.8"
      case 1'1
      attribute \src "design.sv:5.1-5.8"
      case
    end
    sync always
      update \y \y
  end
end
'''
    reduced = _tool_job._rtlil_sites(rtlil, {})
    document = strict_json_loads(reduced, what="the reduced RTLIL site record")

    assert document["version"] == "rtlil-sites/9"
    assert [row["outcome"] for row in document["sites"]] == ["1'1", "default"]
    assert all(document["labels"][row["siblings"]] == ["1'1", "default"]
               for row in document["sites"])
    assert b"candidate_secret" not in reduced
    tool_protocol.OutputNormalizer("design.rtlil", "rtlil-sites/9")
    with pytest.raises(ValueError, match="takes no arguments"):
        tool_protocol.OutputNormalizer(
            "design.rtlil", "rtlil-sites/9", (("top", "top"),))


def test_rtlil_reader_adds_no_match_only_without_a_default():
    from behave.sim.rtl import _tool_job

    rtlil = rb'''module \top
  process $proc
    switch \a
      case 2'00
      case 2'01
    end
    switch \b
      case 1'1
      case
    end
    sync always
      update \y \y
  end
end
'''
    document = strict_json_loads(
        _tool_job._rtlil_sites(rtlil, {}), what="the RTLIL site record")
    by_signal = {}
    for row in document["sites"]:
        by_signal.setdefault(row["signal"], []).append(row)
    assert {row["outcome"] for row in by_signal["\\a"]} == {
        "2'00", "2'01", "no-match"}
    assert {row["outcome"] for row in by_signal["\\b"]} == {
        "1'1", "default"}


@pytest.mark.parametrize("input_width, output_width, controls", [(8, 9, False), (17, 19, True)])
def test_public_realization_uses_closed_ports_without_reparsing_the_header(
    monkeypatch, input_width, output_width, controls,
):
    from behave.sim import api
    from behave.sim.core.schema import Endpoint, TransactionSchema
    from behave.sim.rtl import elaborate

    source = "not a header: only the isolated elaborator may interpret this"
    calls = []

    def elaborate_once(source, name, *, top):
        calls.append((source, name, top))
        ports = [Endpoint("a", "input", input_width, True),
                 Endpoint("y", "output", output_width, False)]
        if controls:
            ports.extend((Endpoint("clk", "input", 1), Endpoint("in_valid", "input", 1)))
        return TransactionSchema(ports, top=top)

    monkeypatch.setattr(elaborate, "schema_of_module", elaborate_once)
    got = api.realization_of_rtl_source(source, "candidate", top="top")

    assert calls == [(source, "candidate", "top")]
    assert (got.ports["a"].width, got.ports["a"].signed,
            got.ports["a"].driven_by) == (input_width, True, "environment")
    assert (got.ports["y"].width, got.ports["y"].signed,
            got.ports["y"].driven_by) == (output_width, False, "artifact")
    assert dict(got.channel_ports) == {"in": ("a",), "out": ("y",)}
    assert set(got.ports) == ({"a", "y", "clk", "in_valid"} if controls else {"a", "y"})


def test_public_elaboration_preserves_parameterized_and_continued_port_types():
    from behave.sim import api

    source = """module top #(parameter W = 17)(
      input logic signed [W-1:0] a, b,
      output logic [W+1:0] y);
      assign y = a + b;
    endmodule"""
    got = api.realization_of_rtl_source(source, top="top")
    assert {name: (port.width, port.signed, port.driven_by)
            for name, port in got.ports.items()} == {
                "a": (17, True, "environment"),
                "b": (17, True, "environment"),
                "y": (19, False, "artifact"),
            }
    assert dict(got.channel_ports) == {"in": ("a", "b"), "out": ("y",)}


def test_coverage_normalizer_preserves_all_uncovered_points():
    from behave.sim.rtl import _tool_job

    raw = "".join(f"C '\x01f\x02/work/rtl.sv\x01l\x02{index}\x01t\x02line' 0\n"
                  for index in range(1, 66))
    points = json.loads(_tool_job._verilator_coverage_points(raw.encode(), {}))["points"]
    assert sorted(row["line"] for row in points) == list(range(1, 66))
    assert all(not row["covered"] for row in points)


@pytest.mark.parametrize("symlink", [False, True])
def test_coverage_locations_cannot_select_or_open_a_host_file(tmp_path, monkeypatch, symlink):
    from behave.sim.rtl import _tool_job

    secret = tmp_path / "secret"
    secret.write_text("DO_NOT_DISCLOSE\n")
    source = secret
    if symlink:
        source = tmp_path / "linked.sv"
        source.symlink_to(secret)
    raw = (
        "C '\x01f\x02" + str(source) + "\x01l\x021\x01t\x02line' 0\n"
        "C '\x01f\x02/work/rtl.sv\x01l\x022\x01t\x02line' 0\n"
    ).encode()

    def no_open(*args, **kwargs):
        raise AssertionError("coverage normalization must not read source files")

    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", no_open)
        patch.setattr(Path, "open", no_open)
        patch.setattr("os.open", no_open)
        normalized = _tool_job._verilator_coverage_points(raw, {})

    points = json.loads(normalized)["points"]
    assert len(points) == 2
    assert {row["line"] for row in points} == {None, 2}
    assert all(set(row) == {"id", "type", "line", "covered"} for row in points)
    assert str(source).encode() not in normalized
    assert b"DO_NOT_DISCLOSE" not in normalized


@pytest.mark.parametrize("source,line", [
    ("rtl.sv", 2), ("/work/rtl.sv", 2), ("../rtl.sv", None), ("/host/rtl.sv", None),
])
def test_coverage_guest_source_name_maps_only_to_the_declared_rtl(source, line):
    from behave.sim.rtl import _tool_job

    raw = f"C '\x01f\x02{source}\x01l\x022\x01t\x02line' 0\n".encode()
    document = json.loads(_tool_job._verilator_coverage_points(raw, {}))
    assert document["version"] == "verilator-coverage-points/1"
    assert len(document["points"]) == 1
    assert document["points"][0]["line"] == line


def test_coverage_normalizer_unions_duplicate_points_without_raw_names():
    from behave.sim.rtl import _tool_job

    normalized = _tool_job._verilator_coverage_points(
        b"C '\x01f\x02/work/rtl.sv\x01l\x021\x01t\x02line\x01n\x02secret-a' 0\n"
        b"C '\x01f\x02rtl.sv\x01l\x021\x01t\x02line\x01n\x02secret-a' 4\n"
        b"C '\x01f\x02/work/rtl.sv\x01l\x022\x01t\x02line\x01n\x02secret-b' 0\n", {})
    points = json.loads(normalized)["points"]
    assert len(points) == 2
    assert {row["line"]: row["covered"] for row in points} == {1: True, 2: False}
    assert [row["id"] for row in points] == sorted(row["id"] for row in points)
    assert b"secret-a" not in normalized and b"secret-b" not in normalized


def test_rtl_protocol_rejects_extra_outputs_and_malformed_command_rows(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text("module top; endmodule\n")
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))
    command = tool_protocol.ToolCommand("run", ("/runtime/python", "-c", "pass"))

    replies = iter((
        {
            "version": "rtl-tool-job/7",
            "commands": [{
                "id": "run", "returncode": 0, "timed_out": False,
                "output_limited": False, "stdout": "", "stderr": "",
            }],
            "outputs": {"result.json": None, "undeclared": None},
            "trees": {},
        },
        {
            "version": "rtl-tool-job/7",
            "commands": [{"id": "run", "returncode": 0}],
            "outputs": {"result.json": None},
            "trees": {},
        },
    ))

    def completed(spec):
        payload = json.dumps(next(replies), separators=(",", ":")).encode()
        return IsolationResult(
            IsolationStatus.COMPLETED, spec.digest, "backend", stdout=payload,
            exit_code=0)

    monkeypatch.setattr(tools, "run", completed)
    for _case in range(2):
        execution = tools.run_workspace(
            workspace, (tool_protocol.ToolGroup((command,)),),
            outputs=("result.json",), role="run")
        assert execution.status is IsolationStatus.REFUSED
        assert execution.refusal.code == "rtl-tool-protocol-invalid"


def test_host_output_materialisation_never_follows_a_symlink_parent(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text("module top; endmodule\n")
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output_root / "nested").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))
    command = tool_protocol.ToolCommand("run", ("/runtime/python", "-c", "pass"))

    def completed(spec):
        payload = json.dumps({
            "version": "rtl-tool-job/7",
            "commands": [{
                "id": "run", "returncode": 0, "timed_out": False,
                "output_limited": False, "stdout": "", "stderr": "",
            }],
            "outputs": {
                "nested/result.json": base64.b64encode(b"fresh").decode("ascii"),
            },
            "trees": {},
        }, separators=(",", ":")).encode()
        return IsolationResult(
            IsolationStatus.COMPLETED, spec.digest, "backend", stdout=payload,
            exit_code=0)

    monkeypatch.setattr(tools, "run", completed)
    execution = tools.run_workspace(
        workspace, (tool_protocol.ToolGroup((command,)),),
        outputs=("nested/result.json",), output_root=output_root, role="run")

    assert execution.status is IsolationStatus.REFUSED
    assert execution.refusal.code == "rtl-tool-output-invalid"
    assert execution.as_dict()["commands"] == []
    assert not (outside / "result.json").exists()


def test_declared_rtl_outputs_cannot_contain_one_another(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="may not contain"):
        tools.run_workspace(
            workspace,
            (),
            outputs=("result", "result/inside"),
            role="invalid",
        )


def _needs_namespace():
    available = capabilities()
    if type(available) is IsolationRefusal:
        pytest.skip(
            f"namespace isolation unavailable: {available.code}: {available.why}")


@pytest.mark.isolation
@pytest.mark.parametrize("pid_probe", ["caller", "local_alias"])
def test_production_tool_seam_scrubs_and_contains_a_generated_process(
    tmp_path, monkeypatch, pid_probe,
):
    """Exercise the RTL process boundary, not a policy mock.

    A generated simulator is native submitted code.  It may use every Python OS API
    in this fixture's stand-in, but none of those APIs should recover host paths,
    environment, network, or processes outside the namespace.
    """

    _needs_namespace()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = tmp_path / "host-marker"
    secret = "must-not-cross-the-tool-boundary"
    monkeypatch.setenv("BEHAVE_RTL_TOOL_SECRET", secret)
    caller_namespace = os.readlink("/proc/self/ns/pid")
    target_pid = str(os.getpid()) if pid_probe == "caller" else "os.getpid()"
    probe = f'''
import json, os, socket, sys

result = {{
    "secret": os.environ.get("BEHAVE_RTL_TOOL_SECRET"),
    "host_path_visible": os.path.exists({str(marker)!r}),
    "host_sys_path": any(path.startswith("/home/") for path in sys.path),
    "pid_namespace_isolated": os.readlink("/proc/self/ns/pid") != {caller_namespace!r},
}}

def denied(name, operation):
    try:
        operation()
    except OSError:
        result[name] = "denied"
    else:
        result[name] = "escaped"

denied("open", lambda: open({str(marker)!r}, "w").write("escaped"))
denied("openat", lambda: os.open({str(marker)!r}, os.O_WRONLY | os.O_CREAT, 0o600))
denied("network", lambda: socket.socket())
# PID numbers are namespace-relative. A successful kill(pid, 0) may refer to
# a different local task, especially when pytest itself is in a PID namespace.
# Compare kernel namespace identity instead of calling such an alias an escape.
try:
    os.kill({target_pid}, 0)
    target_namespace = os.readlink("/proc/%d/ns/pid" % ({target_pid}))
except OSError:
    result["host_process_visible"] = False
else:
    result["host_process_visible"] = target_namespace == {caller_namespace!r}
open("/work/local", "w").write("local")
denied("rename", lambda: os.rename("/work/local", {str(marker)!r}))
os.symlink({str(marker)!r}, "/work/link")
denied("symlink", lambda: open("/work/link", "w").write("escaped"))

child = os.fork()
if child == 0:
    open("/work/child", "w").write("inside")
    os._exit(0)
os.waitpid(child, 0)
result["child"] = "contained" if os.path.exists("/work/child") else "missing"
open("/work/probe.json", "w").write(json.dumps(result, sort_keys=True))
'''
    (workspace / "probe.py").write_text(probe)
    command = tool_protocol.ToolCommand(
        "generated-simulator",
        ("/runtime/python/bin/python", "/work/probe.py"),
        timeout_s=10,
    )
    execution = tool_protocol.require_completed(tools.run_workspace(
        workspace,
        (tool_protocol.ToolGroup((command,)),),
        outputs=("probe.json",),
        role="adversarial-generated-simulator",
        limits=tools.default_limits(wall_seconds=20),
        runtime_profile="simulation",
    ))
    row = tool_protocol.command_result(execution, command.identifier)
    assert row["returncode"] == 0 and row["timed_out"] is False
    result = json.loads(execution.outputs["probe.json"])
    assert result == {
        "child": "contained",
        "host_path_visible": False,
        "host_process_visible": False,
        "pid_namespace_isolated": True,
        "host_sys_path": False,
        "network": "denied",
        "open": "denied",
        "openat": "denied",
        "rename": "denied",
        "secret": None,
        "symlink": "denied",
    }
    assert not marker.exists()


@pytest.mark.isolation
@pytest.mark.parametrize("file_bytes", [1 << 20, tools.default_limits().file_bytes])
def test_production_tool_runner_survives_exit_timeout_and_output_flood(tmp_path, file_bytes):
    _needs_namespace()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "exit.py").write_text("import os; os._exit(0)\n")
    (workspace / "hang.py").write_text("while True: pass\n")
    (workspace / "flood.py").write_text(
        "import sys; sys.stdout.buffer.write(b'x' * (10 << 20) + b'FINAL=42\\n'); "
        "sys.stderr.buffer.write(b'e' * (10 << 20) + b'DONE\\n')\n")
    commands = (
        tool_protocol.ToolCommand(
            "exit", ("/runtime/python/bin/python", "/work/exit.py"), timeout_s=5),
        tool_protocol.ToolCommand(
            "hang", ("/runtime/python/bin/python", "/work/hang.py"), timeout_s=1),
        tool_protocol.ToolCommand(
            "flood", ("/runtime/python/bin/python", "/work/flood.py"), timeout_s=5),
    )
    execution = tool_protocol.require_completed(tools.run_workspace(
        workspace,
        tuple(tool_protocol.ToolGroup((command,)) for command in commands),
        role="adversarial-resource-tree",
        limits=replace(tools.default_limits(wall_seconds=15), file_bytes=file_bytes),
        runtime_profile="simulation",
    ))
    exit_row = tool_protocol.command_result(execution, "exit")
    hang_row = tool_protocol.command_result(execution, "hang")
    flood_row = tool_protocol.command_result(execution, "flood")
    assert exit_row["returncode"] == 0
    assert hang_row["timed_out"] is True and hang_row["returncode"] != 0
    assert flood_row["returncode"] == 0 and not flood_row["timed_out"]
    assert "output_limited" not in flood_row
    assert tool_protocol.command_stream(execution, "flood", "stdout") == b"x" * (10 << 20) + b"FINAL=42\n"
    assert tool_protocol.command_stream(execution, "flood", "stderr") == b"e" * (10 << 20) + b"DONE\n"
    assert len(json.dumps(execution.as_dict())) < 4096
    assert not tuple(workspace.glob("*.stdout"))


@pytest.mark.cosim
@pytest.mark.isolation
def test_adversarial_rtl_include_cannot_read_an_ambient_host_file(tmp_path):
    """The real compiler sees candidate RTL through the same minimal root."""

    _needs_namespace()
    secret = tmp_path / "host_secret.svh"
    secret.write_text("localparam HOST_SECRET_TOKEN = 91;\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text(
        f'`include "{secret}"\nmodule top(output logic y); assign y = 1; endmodule\n')
    command = tool_protocol.ToolCommand(
        "adversarial-yosys",
        ("/tools/yosys", "-p", "read_verilog -sv /work/rtl.sv; prep -top top"),
        timeout_s=30,
    )
    execution = tool_protocol.require_completed(tools.run_workspace(
        workspace,
        (tool_protocol.ToolGroup((command,)),),
        role="adversarial-rtl-include",
        limits=tools.default_limits(wall_seconds=45),
    ))
    row = tool_protocol.command_result(execution, command.identifier)
    diagnostic = (
        tool_protocol.command_stream(execution, command.identifier, "stdout")
        + tool_protocol.command_stream(execution, command.identifier, "stderr")
    )
    assert row["returncode"] != 0
    assert b"HOST_SECRET_TOKEN" not in diagnostic
    assert secret.read_text() == "localparam HOST_SECRET_TOKEN = 91;\n"


@pytest.mark.parametrize("limit", [None, "timed_out", "output_limited"])
@pytest.mark.parametrize("test_filter", ["", "component_" * 256], ids=["all", "long-filter"])
def test_compile_product_is_read_only_input_to_a_separate_simulator_namespace(
    tmp_path, monkeypatch, limit, test_filter,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text("module top; endmodule\n")
    (workspace / "Makefile").write_text("generated")
    calls = []

    def fake_run_workspace(where, groups, **kwargs):
        calls.append((Path(where), groups, kwargs))
        command = groups[0].commands[0]
        if kwargs["role"].endswith(":compile"):
            assert kwargs["outputs"] == ("build/Vtop",)
            assert not kwargs.get("output_trees")
            product = Path(kwargs["output_root"]) / "build"
            product.mkdir()
            executable = product / "Vtop"
            executable.write_text("binary")
            executable.chmod(0o644)  # Regular output collection strips execute bits.
            return _completed(
                kwargs["role"],
                commands=({"id": command.identifier, "returncode": 0,
                           "timed_out": limit == "timed_out", "output_limited": limit == "output_limited",
                           "stdout": "", "stderr": ""},),
                outputs={"build/Vtop": b"binary"},
            )
        assert kwargs["runtime_profile"] == "simulation"
        mounts = kwargs["read_only_mounts"]
        assert len(mounts) == 1 and mounts[0].target == "/build/Vtop"
        assert Path(mounts[0].source).stat().st_mode & 0o777 == 0o500
        assert groups[0].commands[0].argv == ("/build/Vtop",)
        assert dict(command.environment)["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert dict(command.environment)["COCOTB_TEST_FILTER"] == test_filter
        assert "COCOTB_REWRITE_ASSERTION_FILES" not in dict(command.environment)
        return _completed(
            kwargs["role"],
            commands=({"id": command.identifier, "returncode": 0,
                       "timed_out": False, "output_limited": False,
                       "stdout": "", "stderr": ""},),
            outputs={"trace.json": b"{}"},
        )

    monkeypatch.setattr(tools, "run_workspace", fake_run_workspace)
    if limit is not None:
        with pytest.raises(tool_protocol.ToolIsolationError) as error:
            tools.run_verilator_workspace(
                workspace, top_module="top", test_module="driver_tb", role="public",
                outputs=("trace.json", "coverage.dat"), test_filter=test_filter)
        assert error.value.code == "rtl-tool-compile-failed"
        assert len(calls) == 1  # Exit zero and a binary cannot override an exceeded limit.
        return
    build, simulation = tools.run_verilator_workspace(
        workspace, top_module="top", test_module="driver_tb", role="public",
        outputs=("trace.json", "coverage.dat"), test_filter=test_filter)
    assert build.role == "public:compile"
    assert simulation.role == "public:simulate"
    assert len(calls) == 2
    assert calls[0][2]["runtime_profile"] == "eda"
    assert calls[1][2]["output_normalizers"] == (
        tool_protocol.OutputNormalizer(
            "coverage.dat", "verilator-coverage-points/1"),
    )


def test_production_rtl_modules_do_not_launch_the_toolchain_directly():
    root = Path(__file__).resolve().parents[2] / "src" / "behave" / "sim"
    production = [
        root / "harness.py",
        root / "rtl" / "artifact.py",
        root / "rtl" / "catalogue.py",
        root / "rtl" / "elaborate.py",
    ]
    for path in production:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            if isinstance(called, ast.Attribute) \
                    and isinstance(called.value, ast.Name):
                assert (called.value.id, called.attr) not in {
                    ("subprocess", "run"), ("subprocess", "Popen"),
                    ("os", "system"),
                }, path
            assert not any(keyword.arg == "shell" for keyword in node.keywords), path


def test_concurrent_run_roles_and_workspace_identities_are_distinct(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "plan").write_text("one")
    (second / "plan").write_text("two")
    a = _completed("simulate:a")
    b = _completed("simulate:b")
    assert a.role != b.role
    assert tools.content_digest(str(first)) != tools.content_digest(str(second))
