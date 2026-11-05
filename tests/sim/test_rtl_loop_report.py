"""Cycle diagnostics and generated C++ agree with the preceding compiler."""

import os
import subprocess

import pytest

from behave.core.paths import eda_root
from behave.sim.rtl.native_events import VERILATOR_FLAGS


@pytest.mark.eda("verilator")
@pytest.mark.parametrize("count", [3, 128])
def test_loop_report_retains_warnings_and_generated_code(tmp_path, count):
    from behave.sim.rtl.native_dependencies import SIM_VERILATOR_EXECUTABLE

    eda = eda_root()
    previous = eda / "libexec/behave-verilator-graph-path-2"
    if not previous.is_file():
        pytest.skip("preceding qualified compiler is not installed")
    source = f"module M(input [{count-1}:0] a,b,output [{count-1}:0] q);\n"
    for i in range(count):
        source += (f"wire x{i},y{i}; assign x{i}=a[{i}]|y{i}; "
                   f"assign y{i}=b[{i}]&x{i}; assign q[{i}]=x{i};\n")
    source += "endmodule\n"
    results = []
    for label, compiler in (("previous", previous),
                            ("current", eda / "libexec" / SIM_VERILATOR_EXECUTABLE)):
        directory = tmp_path / label
        directory.mkdir()
        (directory / "design.sv").write_text(source)
        run = subprocess.run(
            [str(compiler), "--cc", "--top-module", "M", "--debug-self-test",
             "-Wno-fatal", *VERILATOR_FLAGS, "design.sv"],
            cwd=directory, env=dict(os.environ, VERILATOR_ROOT=str(eda / "share/verilator")),
            capture_output=True, text=True, timeout=90)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "%Warning-UNOPTFLAT" in run.stderr, run.stderr
        generated = {path.name: path.read_bytes() for path in (directory / "obj_dir").iterdir()
                     if path.suffix in {".cpp", ".h"}}
        assert generated
        results.append((run.stderr, generated))
    assert results[0] == results[1]
