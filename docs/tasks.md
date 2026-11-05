# Tasks

## A task directory

Native task bundles use `data/problems/<dataset>/<task_id>/`:

| File | Role |
| --- | --- |
| `spec.md` | the specification the agent reads |
| `interface.json` | the declared physical and logical interface (`TaskContract`) |
| `behavior.py` | the reference Python behavior model (B1), judge-only |
| `behavior2.py` | optional second model used in construction review |
| `rtl.sv` | optional reference RTL, never shown to the agent |

Include applicable `source.json`, `LICENSE` and `NOTICE` files with derived
tasks. The loader preserves these attachments; it does not verify redistribution
rights or certify reference implementations.

The interface file is `TaskContract.as_dict()`: endpoints with fields, widths
and signedness, clocks and resets, protocol bindings (ready/valid, AXI-Lite,
APB, memory ports) and finite limits. It is the normative interface for both
the simulator and the agent. Nothing is inferred from the specification prose
or from candidate RTL.

```python
from behave.sim import api

declarations = api.declarations_at(task_directory)
contract = api.contract_of_task(task_directory, declarations=declarations)
```

## Datasets and splits

Task files are included under `data/problems`; task-ID lists under `data/splits`
define the training, evaluation and self-improvement seed memberships.
See [datasets](../data/README.md) for counts, sources and a preparation command.

For a self-contained example, run
`uv run python examples/prepare_task.py work/example-task`; no dataset download
is needed. It uses the same native-task preparation path as a dataset bundle.

## Releasing tasks to Eval and Train

Eval and Train read a release, not the task directories:

```bash
uv run behave-data freeze-native releases/local-inputs --repository /path/to/task-bundle --tasks tasks.txt
```

This writes `release.json` and a content store `objects/` holding exactly the
declared task files, including judge-only references and license attachments.
Task IDs must resolve to one directory each; a selection that fails to load is
refused rather than published partially. `--from-release` rebinds an existing
release to the installed simulator version without reading task directories.

Reviewed publication (`behave-release`, `behave-inspect --kind release`) adds
an admission policy, per-task approvals and a corpus guard against cross-split
duplicates. `freeze-native` alone does not establish benchmark approval.

## Constructing new tasks

`behave-data --stage synthesis` runs the construction workflow: an Analyst
proposes tasks from source material, a Builder writes the specification, B1
and interface, an independent reader rebuilds B2 blind, and a Reviewer compares
them with the simulator. The result is a review package; publication is a
separate operator step. The role prompts are in
[`src/behave/agent/prompts/synthesis`](../src/behave/agent/prompts/synthesis).
