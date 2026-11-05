Development references, available to read with shell commands:

- `/docs/development.md`: installed tools, build logs, cocotb simulation, and RTL line coverage.
- `/docs/fp_lib.md`: the installed `fp_lib` module's numerical interfaces and exact value semantics.
- `/docs/memory.md`: using `MemoryDriver` in cocotb for external-memory initialization, request/response timing and result inspection.

Import the public library with `from fp_lib import ...`. Its read-only source is
available at `/runtime/site/fp_lib.py`.
