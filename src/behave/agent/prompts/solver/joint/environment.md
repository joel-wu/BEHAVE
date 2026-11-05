Development references, available to read with shell commands:

- `/docs/development.md`: installed tools, build logs, cocotb simulation, and RTL line coverage.
- `/docs/behavior.md`: the Python verification model's interface, state and language scope.
- `/docs/fp_lib.md`: the installed `fp_lib` module's numerical interfaces and exact value semantics.
- `/docs/memory.md`: using `MemoryDriver` in cocotb for external-memory initialization, request/response timing and result inspection.

Import the public library with `from fp_lib import ...`. Its read-only source is
available at `/runtime/site/fp_lib.py`. It is also installed for the final TB
rerun and supported by Behavior evaluation, so it need not be copied into a submission.

The public `memory_driver` is also installed for the final TB rerun. It uses
the same memory transition as Sim and need not be copied into a submission.

After submission, reported TB line coverage is measured by recompiling the
submitted RTL and rerunning the submitted testbench, not from development reports.
Development test results and TB coverage are separate from Sim's artifact scores.
