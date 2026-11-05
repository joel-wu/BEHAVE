# Development environment

The environment provides Python, cocotb, Verilator, GNU Make, a C/C++ compiler
and Yosys with ABC. Use `python`, `cocotb-config`, `verilator`, `make`, `gcc`,
`g++` and `yosys` directly.
Use Verilator for HDL simulation. Icarus Verilog (`iverilog` and `vvp`) is
not installed.
To inspect the installed versions, run `python --version`,
`cocotb-config --version`, `verilator --version` and `yosys -V`.

For large files and logs, use `grep -n` to find relevant sections and `sed -n`
to read selected line ranges.

Command output enters the conversation. For a verbose build or test, save the
log in the workspace and return the relevant portion. For example:

```bash
make > build.log 2>&1
build_status=$?
tail -n 80 build.log
exit "$build_status"
```

This preserves the build's exit status rather than returning the status of
`tail`. Read other sections of `build.log` in later calls without rerunning
the build. Small outputs can be returned directly.

## Cocotb simulation

A cocotb test entry point is an `async def test_name(dut)` function decorated
with `@cocotb.test()`. Helper functions that do not wait for simulation events can
use ordinary `def`. The installed cocotb uses `unit="ns"` for time units,
for example `Timer(10, unit="ns")`. Observe signals at the phase specified
by the protocol. `ReadOnly()` can be used for settled observations, and
writes must happen in a writable phase. After `await ReadOnly()`, advance to
a later simulation time before writing any signal, including reset. Await a
future clock edge or a positive `Timer`. Awaiting `ReadWrite()` or another
`ReadOnly()` does not leave the current read-only phase and raises an error.

For example, for a register that captures `data_in` on the rising edge,
with a running clock and reset already released:

```python
from cocotb.triggers import FallingEdge, RisingEdge, ReadOnly

for value in values:
    await FallingEdge(dut.clk)
    dut.data_in.value = value
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.data_out.value) == value
```

The next iteration waits for a falling edge before writing again. Adapt the
edges and observations to the task. This example checks a registered output,
not a ready/valid transfer. Determine accepted transfers using the handshake
values at the specified sampling boundary, not values changed by that transfer.

Use cocotb's `Clock` for periodic clocks required by the task. Bind each driver
and monitor to its clock domain, following the task's active edges and timing
constraints. Measure cycle-based constraints in the domain that defines them.
If the task supplies clock levels as explicit input records, drive those
records rather than starting periodic clocks. `MemoryDriver` generates its
bound clock, so use it as that domain's clock source as described in
`/docs/memory.md`.

Apply resets with the specified polarity, synchronous or asynchronous
semantics, and release sequence.

The following Makefile runs cocotb with top module `top`, sources `rtl.sv`
and `helper.sv`, and tests in `testbench.py`. Replace these filenames and the
top module with the actual design. Omit `helper.sv` if not used.

```make
SIM = verilator
TOPLEVEL_LANG = verilog
VERILOG_SOURCES = $(PWD)/rtl.sv $(PWD)/helper.sv
COCOTB_TOPLEVEL = top
COCOTB_TEST_MODULES = testbench
COMPILE_ARGS += --coverage-line

include $(shell cocotb-config --makefiles)/Makefile.sim
```

Run it with `make`. Python imports can refer to modules in the workspace.
The module name is the importable testbench name without `.py`. Check the
cocotb log and `results.xml` for test results. You can edit the sources and
run `make` again. Keep build directories distinct when running multiple
builds concurrently within one workspace.

Before testing an operation, confirm that its initialization and readiness
conditions have been reached. Count cycle-based delays using the specified
active clock edges.

Exercise the key combinations required by the specification, not only isolated
random values. Where applicable, include independently arriving channels,
state changes during backpressure, reset interrupting an operation, and further
updates after saturation. Check the specified result for each sequence.

After the last source edit or generator run, rerun the relevant checks on the
exact files you will submit. A passing run from before those changes does not
check the final version.

## Inspect RTL line coverage

With `--coverage-line`, the Verilator cocotb harness writes `coverage.dat`
when the simulation exits normally. For development feedback, convert the
file using Verilator's coverage utility:

```bash
verilator_coverage --write-info coverage.info coverage.dat
```

The result is LCOV text. `SF:` identifies the RTL file, `DA:line,count`
records a source line and its hit count. Count `DA` lines with a nonzero hit
count against all reported `DA` lines for your design's source entries.
Only lines instrumented by Verilator enter this report. This is line
coverage, not toggle coverage.
Line coverage shows that code was executed, not that the specification's
semantic combinations were checked.
You can also request annotated source with:

```bash
verilator_coverage --annotate cov_report coverage.dat
```
