# Training

Training runs the same agent loop and simulator inside a native RL stack
(Slime, SGLang, Megatron-LM) and uses Sim's judgments as rewards. The stack is
experimental and GPU-only; the control environment in `uv.lock` does not
contain it.

## Native stack

Sources and patches are pinned in [`external/manifest.toml`](../external/manifest.toml):

```bash
uv run behave-train runtime sources --repository . --directory /path/to/native-sources --output receipts/sources.json
uv run behave-train runtime check --repository . --configuration configs/train/qwen27b_rl.example.json \
  --machine-profile my-machine.json --output receipts/environment.json
```

`sources` clones the pinned commits and applies each patch once; `check` verifies
an existing environment without importing CUDA. Build the Python environment for
that stack yourself and name it in the machine profile.

## Recipe and machine profile

An experiment recipe (`configs/train/qwen27b_rl.example.json`) owns seeds,
sampling, optimizer, task-pool policy, parallelism and save cadence. A machine
profile (`infra/train/machine.example.json`, copied outside the repository) owns
installed paths, endpoints, caches and worker budgets. The allocation and CPU
count are launch arguments. A run saves the resolved flat `configuration.json`;
continuation reads that file and takes no new overlay.

## Prepare and submit

```bash
uv run behave-train prepare PLAN_DIR --repository . --configuration configs/train/qwen27b_rl.example.json \
  --machine-profile my-machine.json --release releases/local-inputs \
  --train-split data/splits/authored600_v2/train.txt --task TASK_ID ...

uv run behave-train submit RUN_DIR --repository . --allocation JOBID --cpus 144 \
  --configuration configs/train/qwen27b_rl.example.json --machine-profile my-machine.json \
  --release releases/local-inputs --train-split data/splits/authored600_v2/train.txt \
  --tasks-file tasks.txt --updates 60 --training-seed 20260918 --checkpoint-directory /path/to/checkpoints/run
```

`prepare` checks the task list against the split and freezes the plan without
starting anything. `submit` runs the job in an existing Slurm allocation; the
run directory holds frozen code, resolved inputs and progress. `--updates` is
an absolute total. `--resume-from PREVIOUS_RUN` restores native state,
optimizer and task pool; an independent seed is a fresh run instead.

## Checkpoints and reports

The main example saves native state after every update and HF weights every
five updates. Other recipes declare their own cadence. Retention
keeps the latest full state and protected anchors; cold copies are verified
before any hot file is released, and nothing is deleted without a replacement.

The cold-publication helper `behave.runtime.models.publish_model` serializes
writes to each archive. All publishers must use the same shared lock directory
with cross-node `flock` support. For object-storage mounts without that support,
pass `lock_directory` on shared NFS, not node-local temporary storage. Lock files
remain in place; weights are still transferred as whole archives.

```bash
uv run behave-train report RUN_DIR REPORT_DIR
```

exports per-round and per-task rewards, sampling receipts, token availability
and optimizer timing from the run's records; it loads no weights.
`behave-trainer` exposes optimizer-side operations (`initialize`, `update`,
`reconcile`, `export`, `restore`, `publish`). The native setup and launch commands
are described in [the training runtime guide](../infra/train/README.md).

## Self-improvement

`behave-train prepare-study` freezes a study that alternates task construction
and training rounds; `behave.improve` owns the rounds and the Analyst memory.
The Analyst uses recorded training feedback to guide acquisition. Constructed
tasks pass the construction checks before entering the training pool. Role
prompts live in [`agent/prompts/synthesis`](../src/behave/agent/prompts/synthesis).
Study settings control acquisition targets, training intervals and HF exports.
