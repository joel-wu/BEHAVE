"""Frontend dependency closures preserve the full runtime's semantic outputs."""

import os

import pytest

from behave.sim.rtl import tool_runtime


@pytest.fixture(autouse=True)
def runtime_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime-batch"))


@pytest.fixture
def minimal_install(tmp_path, monkeypatch):
    import behave
    from behave.isolation import MaskedPath

    eda = tmp_path / "eda"
    for name in ("verilator_bin", "yosys", "slang", tool_runtime.NATIVE_FRONTEND_EXECUTABLE):
        path = eda / "libexec" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    for name in ("lib", "share/verilator", "share/yosys"):
        path = eda / name
        path.mkdir(parents=True)
        (path / "resource").write_bytes(b"version 1")
    package = tmp_path / "behave"
    package.mkdir()
    (package / "__init__.py").write_text("")
    monkeypatch.setattr(behave, "__file__", str(package / "__init__.py"))
    masks = (MaskedPath("/runtime/python/private"),)
    monkeypatch.setattr(tool_runtime, "python_runtime", lambda: ((), (), masks))
    monkeypatch.setattr(tool_runtime, "eda_root", lambda: eda)
    # This fixture tests dependency closure, not installation/version checks.
    monkeypatch.setattr(tool_runtime, "checked_sim_verilator", lambda root: tool_runtime.binary(
        str(root / "libexec/verilator_bin"), "/opt/eda/libexec/verilator_bin"))
    monkeypatch.setattr(tool_runtime, "checked_native_frontend", lambda root: tool_runtime.binary(
        str(root / "libexec" / tool_runtime.NATIVE_FRONTEND_EXECUTABLE), "/tools/behave-rtl-frontend"))
    monkeypatch.setattr(tool_runtime, "native_runtime", lambda programs: ())
    monkeypatch.setattr(tool_runtime, "eda_library_mounts", lambda root, programs: (
        tool_runtime.runtime_tree(str(root / "lib"), "/opt/eda/lib", allow_symlinks=True),))

    def unrelated(*args, **kwargs):
        pytest.fail("a frontend must not discover site-packages or a C++ compiler")

    monkeypatch.setattr(tool_runtime, "python_dependency_mounts", unrelated)
    monkeypatch.setattr(tool_runtime, "_compiler_runtime_paths", unrelated)
    return eda, masks


@pytest.mark.parametrize("profile", ["elaboration", "catalogue"])
def test_frontend_closure_contains_only_its_declared_tool_inputs(minimal_install, profile, tmp_path, monkeypatch):
    eda, masks = minimal_install
    runtime = tool_runtime.freeze_runtime(profile)
    binaries = {item.target for item in runtime.binaries}
    mounts = {item.target for item in runtime.mounts}
    assert runtime.masked == masks
    assert mounts >= {"/opt/eda/lib", "/runtime/rtl-tool-job.py"}
    if profile == "elaboration":
        assert binaries == {"/tools/verilator", "/opt/eda/libexec/verilator_bin"}
        assert mounts == {"/opt/eda/lib", "/opt/eda/share/verilator", "/runtime/rtl-tool-job.py"}
    else:
        assert binaries == {"/tools/yosys", "/tools/slang", "/opt/eda/libexec/yosys",
                            "/opt/eda/libexec/slang", "/tools/behave-rtl-frontend"}
        assert mounts == {"/opt/eda/lib", "/opt/eda/share/yosys", "/runtime/rtl-tool-job.py",
                          "/runtime/behave/behave", "/runtime/rtlil-sites.py"}
    # Necessary dependency changes alter the identity; unrelated tools do not.
    unrelated = eda / "libexec" / ("yosys" if profile == "elaboration" else "verilator_bin")
    unrelated.write_text("#!/bin/sh\nexit 1\n")
    assert tool_runtime.freeze_runtime(profile).identity == runtime.identity
    (eda / "lib/resource").write_bytes(b"version 2")
    assert tool_runtime.freeze_runtime(profile).identity == runtime.identity
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "updated-runtime"))
    assert tool_runtime.freeze_runtime(profile).identity != runtime.identity


def test_unknown_profile_does_not_start_discovery(monkeypatch):
    def unexpected():
        pytest.fail("invalid profile started runtime discovery")

    monkeypatch.setattr(tool_runtime, "python_runtime", unexpected)
    with pytest.raises(ValueError, match="profile"):
        tool_runtime._runtime("typo")


def _legacy(monkeypatch):
    """Use the previous dependency closure, not another compiler or evaluator."""
    current = tool_runtime._runtime
    seen = []

    def full_runtime(profile):
        seen.append(profile)
        return current({"elaboration": "eda", "catalogue": "source-events"}.get(profile, profile))

    # Changing the test's factory must not reuse the already prepared reduced
    # profile. Also assert discovery happened: two cache hits are not an oracle.
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", os.environ["BEHAVE_RUNTIME_DIR"] + "-full")
    monkeypatch.setattr(tool_runtime, "_runtime", full_runtime)
    return seen


SOURCES = (
    """module M(input signed [7:0] a, output reg signed [8:0] q);
always @* if (a < 0) q = -a; else q = a;
endmodule
""",
    """module L #(parameter K=2)(input clk, input [7:0] a, output reg [7:0] q);
always @(posedge clk) if (a < K) q <= a + K; else q <= 0;
endmodule
module M(input clk, input [7:0] a, output [15:0] q);
L #(2) x(clk,a,q[7:0]); L #(3) y(clk,a,q[15:8]);
endmodule
""",
    """module M(input [2:0] a, output reg signed [4:0] q);
localparam logic signed [4:0] ROM [7:2] = '{1,-1,3,-4,5,6};
always @* if (a == 7) q = ROM[7]; else q = ROM[a];
endmodule
""",
)


@pytest.mark.eda("verilator", "yosys", "slang")
@pytest.mark.isolation
@pytest.mark.parametrize("source", SOURCES, ids=["combinational", "hierarchy", "signed-rom"])
def test_frontend_ports_and_occurrences_match_the_full_runtime(tmp_path, monkeypatch, source):
    from behave.sim.rtl.catalogue import read_sites_with_execution, sites_to_json
    from behave.sim.rtl.elaborate import schema_and_execution_of_module

    path = tmp_path / "candidate.sv"
    path.write_text(source)

    def observe():
        _, ports = schema_and_execution_of_module(source, top="M")
        sites, catalogue = read_sites_with_execution(path, "M")
        assert sites, "the oracle must exercise actual occurrences"
        return ports.outputs, sites_to_json(sites), catalogue.outputs

    reduced = observe()
    with monkeypatch.context() as previous:
        seen = _legacy(previous)
        assert observe() == reduced
        assert set(seen) == {"elaboration", "catalogue"}


@pytest.mark.eda("verilator")
@pytest.mark.isolation
def test_frontend_captured_includes_and_source_rejection_keep_their_meaning(tmp_path, monkeypatch):
    from behave.sim.rtl.frontend_error import SourceRejected
    from behave.sim.rtl.source_bundle import RTLSourceBundle, prepare_rtl_bundle
    from behave.sim.rtl.source_check import source_compile_failure

    bundle = RTLSourceBundle({
        "M.sv": b'`include "defs.svh"\nmodule M(input [`W-1:0] a, output [`W-1:0] q); assign q=a; endmodule',
        "defs.svh": b"`define W 8\n",
    }, ("M.sv",))
    secret = tmp_path / "host-secret.svh"
    secret.write_text("SECRET_DO_NOT_EXPORT\n")
    leaking = RTLSourceBundle({"M.sv": f'`include "{secret}"\n'.encode()}, ("M.sv",))

    def observe():
        prepared = prepare_rtl_bundle(bundle)
        valid, _ = source_compile_failure(prepared.source, "M", timeout_s=30)
        invalid, _ = source_compile_failure("module M; not valid RTL; endmodule", "M", timeout_s=30)
        assert valid is None
        assert invalid is not None
        assert invalid.reason.kind == "rtl-source-build-rejected"
        with pytest.raises(SourceRejected) as caught:
            prepare_rtl_bundle(leaking)
        assert "SECRET_DO_NOT_EXPORT" not in str(caught.value)
        return prepared.source, invalid.origin, invalid.disposition

    reduced = observe()
    with monkeypatch.context() as previous:
        seen = _legacy(previous)
        assert observe() == reduced
        assert seen == ["elaboration"]
