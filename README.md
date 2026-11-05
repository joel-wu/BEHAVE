# BEHAVE

Code for **BEHAVE: Functional Behavior Modeling Enables Self-Improving Agents
for Hardware Design and Verification**.

BEHAVE checks RTL and Python implementations against executable behavior models.
The same simulator scores agent evaluations, supplies RL rewards and checks
agent-constructed tasks. This repository contains the code, examples and
[datasets](data/README.md). Model weights are not included.

## Install

```bash
uv sync --locked --python 3.11.15 --extra eda --extra dev
bash scripts/setup_eda.sh        # pinned OSS CAD Suite plus a patched Verilator, into external/
make check                       # lint and the unit lane
```

Tool-backed execution needs a Linux host with user, mount, PID and network
namespaces and libseccomp. See [install](docs/install.md).

## Try the simulator

The self-contained example checks a byte-wide passthrough in both RTL and Python.
It needs the EDA toolchain and isolation backend, but no dataset or model endpoint.

```bash
uv run python examples/public_sim_consumer.py work/sim-example
```

## Evaluate an agent

Prepare the same small task, then configure a model endpoint:

```bash
uv run python examples/prepare_task.py work/example-task
cp configs/eval/local.example.json my-eval.json        # point it at your model endpoint
uv run behave-eval prepare --configuration my-eval.json \
  --task-objects work/example-task/objects --release work/example-task/release.json \
  --task passthrough --output prepared-eval
uv run behave-eval --plan prepared-eval/plan.json --task-objects prepared-eval/inputs --dry-run
uv run behave-eval --plan prepared-eval/plan.json --task-objects prepared-eval/inputs --run-directory prepared-eval/run
uv run behave-eval --score initial --judge-workers 1 --task-objects prepared-eval/inputs --run-directory prepared-eval/run
```

Preparation and `--dry-run` make no model calls. Running the plan calls your
model; `--score` judges its saved submissions. Repeat the run or scoring command
to resume. See [evaluation](docs/eval.md) for other tasks and providers.

## Layout

| Path | Contents |
| --- | --- |
| `data` | task specifications, reference implementations, splits and source notices |
| `src/behave/sim` | the simulator: contracts, generation, replay, scoring, bounded analysis |
| `src/behave/agent`, `task_execution`, `isolation` | the tool-using agent loop, candidate execution and the sandbox |
| `src/behave/eval`, `train`, `improve`, `data`, `ppa` | evaluation, RL training, self-improvement, task construction, PPA studies |
| `src/behave/runtime`, `core`, `cli` | records, jobs, snapshots and the command entries |
| `native`, `external` | RTL frontend, pinned dependencies and patches |
| `configs`, `infra` | experiment recipes and deployment examples |
| `tests`, `tools`, `examples`, `scripts` | checks, operator scripts, the public Sim example and setup |

## Documentation

- [Install](docs/install.md): toolchain, environment variables, checks.
- [Evaluation](docs/eval.md): endpoints, plans, scoring, resuming, costs.
- [Tasks](docs/tasks.md): task directories, the interface contract, releases and splits.
- [Training](docs/training.md): native RL stack, recipes, submission, resume, reports.
- [Simulator example](examples/public_sim_consumer.py): RTL and Behavior scoring through `behave.sim.api`.
