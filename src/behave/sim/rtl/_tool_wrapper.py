#!/runtime/python/bin/python
"""Relocatable command names used by the isolated RTL build.

The managed EDA distribution ships shell and Perl launchers whose shebangs name
ambient host programs.  These wrappers enter the distribution's ELF binaries
through its own loader and expose cocotb/Python without inheriting a host PATH.
"""

from __future__ import annotations

import os
import sys
from glob import glob
from pathlib import Path


def _eda(program: str) -> None:
    loaders = sorted(glob("/opt/eda/lib/ld-linux*.so.*"))
    if len(loaders) != 1:
        raise SystemExit("the isolated EDA runtime has no unique ELF loader")
    loader = loaders[0]
    binary = f"/opt/eda/libexec/{program}"
    environment = dict(os.environ)
    environment["VERILATOR_ROOT"] = "/opt/eda/share/verilator"
    environment["YOSYS_DATDIR"] = "/opt/eda/share/yosys"
    extra = (["-B", "/opt/eda/lib/ivl", "-pVVP_EXECUTABLE=/tools/vvp"]
             if program == "iverilog" else
             ["-M", "/opt/eda/lib/ivl"] if program == "vvp" else [])
    os.execve(loader, [loader, "--inhibit-cache", "--inhibit-rpath", "",
                       "--library-path", "/opt/eda/lib", binary, *extra, *sys.argv[1:]],
              environment)


def main():
    name = Path(sys.argv[0]).name
    if name in {"verilator", "verilator_coverage", "yosys", "slang", "yosys-abc",
                "iverilog", "vvp", "ivl", "ivlpp"}:
        program = {"verilator": "verilator_bin",
                   "verilator_coverage": "verilator_coverage_bin_dbg",
                   "yosys": "yosys", "slang": "slang", "yosys-abc": "abc",
                   "iverilog": "iverilog", "vvp": "vvp", "ivl": "ivl", "ivlpp": "ivlpp"}[name]
        try:
            _eda(program)
        except (OSError, SystemExit) as exc:
            print(f"tool launch failed: {exc}", file=sys.stderr)
            return 126
    elif name == "cocotb-config":
        from cocotb_tools.config import main as configure

        return configure()
    elif name in {"python", "python3"}:
        os.execve("/runtime/python/bin/python",
                  ["/runtime/python/bin/python", *sys.argv[1:]], dict(os.environ))
    else:
        print(f"unknown isolated tool wrapper {name!r}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
