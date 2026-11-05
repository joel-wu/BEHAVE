# Install

## Python environment

```bash
uv sync --locked --python 3.11.15 --extra eda --extra dev
```

Extras: `eda` adds cocotb, z3, hypothesis and numpy for simulation and
scoring; `dev` adds pytest, pytest-xdist and ruff; `pipelines` adds pydantic and
rich for the external-method adapters; `solver` alone gives the z3 lane without
the RTL tools. The base package (Jinja2 only) can read records and prepare Eval
plans. The native training stack is installed separately, see [training](training.md).

## EDA toolchain

```bash
bash scripts/setup_eda.sh
```

This installs one pinned [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build)
release (verilator, yosys, iverilog, slang) under `external/oss-cad-suite`,
builds the patched Verilator that Sim uses for native observation and the
Slang client in `native/rtl_frontend`, and records the release in
`external/eda.lock`. Building needs GCC/G++, make, autoconf, flex, bison, m4,
Perl, CMake 3.28 or newer and a C++20 compiler. Rerunning the script installs
the locked release; `--check` only verifies.

Point another checkout at an existing installation with
`BEHAVE_EDA_ROOT=/path/to/oss-cad-suite`.

## Isolation

Every candidate, judge and tool runs in fresh user, mount, PID, network, IPC and
UTS namespaces with seccomp. The host needs unprivileged user namespaces,
`unshare` and libseccomp. There is no unisolated fallback: when the backend is
unavailable the command refuses, and tests that need it are skipped. Most HPC
compute nodes and plain Linux VMs qualify; containers must allow nested
namespaces.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `BEHAVE_EDA_ROOT` | `external/oss-cad-suite` of the checkout | the toolchain installation |
| `BEHAVE_SCRATCH_DIR` | `/tmp` | node-local scratch for builds, sandboxes and caches |
| `BEHAVE_DATA_DIR` | `data` of the checkout | task corpus root for installed wheels |
| `BEHAVE_WORK_DIR` | `work` of the checkout | run outputs for installed wheels |

Installed wheels never infer a checkout; set the variables that the command needs.

Run the checkout examples from the repository root. They use `uv run` to select
its virtual environment; no shell activation is required.

## Checks

```bash
make lint           # syntax errors, undefined names, unused imports
make check          # lint plus the unit lane: no simulator, solver or namespaces needed
make check-node     # lanes that need the EDA toolchain and the isolation backend
make check-pipelines # optional external-method tests (setup below)
make check-install  # install a wheel, prepare the example, validate an Eval plan and run Sim
```

Run the node lanes on a machine with the toolchain and namespaces. `tests/sim`
holds the simulator's own suite; `tests/infrastructure` covers everything else.
The install check needs those same capabilities, but no dataset or model server.
It runs outside the checkout and does not make model calls.

ChipMate/MAGE tests are separate from the default checks. Install their optional
dependencies and pinned sources explicitly:

```bash
uv sync --locked --python 3.11.15 --extra eda --extra dev --extra pipelines
uv run python -m tools.setup_pipeline_sources chipmate mage
make check-pipelines
```

This target refuses missing dependencies or sources before running tests.
Its sandbox tests also require the EDA toolchain and isolation backend.

## PPA tools (optional)

The PPA flows (`python -m behave.ppa`) read two tool trees next to the
EDA toolchain. Neither is built by `scripts/setup_eda.sh`:

| Path | Contents | How it is produced |
| --- | --- | --- |
| `external/ppa-activity` | OpenSTA 3.1.0 and the SKY130 HD Liberty subset | `tools/ppa_opensta_build.py external/ppa-activity --jobs 8` |
| `external/ppa-netlist-repair` | OpenROAD with the SKY130 technology and cell LEF files | assembled from the pinned Ubuntu packages listed in its `dependency_receipt.json`; no installer in this repository |

Both may be symbolic links to a shared installation; the release ignores the
links. Without them the PPA tests fail with `ppa-worker-failed` and the PPA
commands refuse to start.
