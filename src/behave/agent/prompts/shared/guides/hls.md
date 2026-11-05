# HLS implementation guide

Implement the exact kernel.h declaration in kernel.cpp. Inputs and outputs are
packed bit representations described in interface.md, not approximate numeric
conversions. Preserve all output bits, signedness, overflow, rounding and state
specified by the source program. Use ap_uint/ap_int for explicit finite widths;
reinterpret floating-point bits when required rather than numerically converting
their integer encodings.

Reuse compatible source code and installed Vitis HLS headers. Replace host-only
dependencies with synthesizable code of the same functionality: bounded loops
and arrays are supported; host file I/O, subprocesses, dynamic allocation and
runtime imports are not hardware implementations. Do not call the host input
program as a black box.

The checker fixes the top-level interfaces and reset configuration. Retain the
kernel arguments and names; do not introduce AXI, stream or external-memory
interfaces. For a stateful task, keep state across calls and make reset restore
the specified initial state. For a stateless task, each call is independent.

Read check_candidate feedback and its logs. Compilation or synthesis success is
not functional success. Correct the reported issue and check again as needed;
submit selects the current implementation, not a previous successful version.
