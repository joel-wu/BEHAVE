# Training runtime

Use the shared `behave-train` interface. Run directories hold resolved inputs,
frozen code and progress, not another training implementation. This native stack
is experimental; local checks do not certify a clean-machine GPU installation.

## Which configuration to use

| Files | Role |
| --- | --- |
| [`qwen27b_rl.example.json`](../../configs/train/qwen27b_rl.example.json) + [`machine.example.json`](machine.example.json) | Main-RL recipe and deployment inputs for a new installation |
| [`qwen27b_acceptance.json`](qwen27b_acceptance.json) | Flat configuration retained for compatibility; replace its example paths before use |
| A run's `configuration.json` | Saved continuation settings; no new machine overlay |
| [`native_runtime.json`](native_runtime.json) | Observed dependency versions, not an installer or binary lock |

Keep historical inputs for replay. The main-RL example's five-update HF cadence
does not replace seed-only/Syn's three-update schedule. Use the reviewed recipe
for the chosen experiment, not a probe or an old run's launcher.

## Experiment and machine profiles

Experiment recipes own seeds, sampling, optimizer, pool policy, parallelism and
save cadence. Machine profiles own installed paths, endpoints, caches and worker
budgets. TP/CP and serving topology are experiment choices, not machine overrides.
Allocation ID and reserved CPUs are explicit launch arguments.

Copy `machine.example.json` outside the repository and replace its `/path/to/`
values. `checkpoint_retention.protected_paths` belongs there; the retention
policy belongs in the experiment. Pool, memory and progress are run state.

Preparation and submission accept `--machine-profile` only with a split recipe.
They save the same flat `configuration.json` used by existing runs. Continuation
uses that saved file without the original recipe or machine profile; flat configs
reject machine overlays.

## Native dependencies

Use the reviewed commits and patches in [`external/manifest.toml`](../../external/manifest.toml),
not the latest upstream releases. Prepare sources into an explicit destination:

```bash
uv run behave-train runtime sources --repository /path/to/behave \
  --directory /path/to/native-sources \
  --output /path/to/receipts/native-sources.json
```

This installs sources only, not Python/CUDA packages. Patch hashes are checked
before cloning. Repeating the command accepts the same patched tree; unknown
edits, partial patches and wrong commits are rejected without resetting files.
Apply each combined patch once; do not reapply Slime's bootstrap patches.

Build a separate native environment and set `native_python`, `slime_source` and
`megatron_source` in the machine profile. Legacy flat configs without the latter
retain the sibling `megatron-upstream` convention. Check the environment once
at setup/review time:

```bash
uv run behave-train runtime check --repository /path/to/behave \
  --configuration configs/train/qwen27b_rl.example.json \
  --machine-profile /path/to/my-machine.json \
  --output /path/to/receipts/native-environment.json
```

The check reads package metadata and verifies editable Slime/SGLang/Megatron
sources using private Git indexes. It does not import CUDA, change the environment
or dispatch jobs. Receipts are never overwritten; mismatches return nonzero.
`matched` does not certify compiled extensions or GPU execution. `uv.lock` covers
the control environment; native binary builds still require separate validation.

## One job entry

Submit to an existing allocation; this does not request or cancel a node:

```bash
uv run behave-train submit RUN_DIRECTORY --repository /path/to/behave \
  --allocation ALLOCATION --cpus 144 \
  --configuration configs/train/qwen27b_rl.example.json \
  --machine-profile /path/to/my-machine.json \
  --release RELEASE --train-split TRAIN_SPLIT --tasks-file TASK_LIST \
  --updates 60 --training-seed 20260918 \
  --checkpoint-directory /path/to/checkpoints/UNIQUE_RUN
```

The repository and run directory must be visible on the selected node. Training
lists are checked against the declared train split and current evaluation tasks.
The main split has [540 training tasks](../../data/splits/authored600_v2/train.txt)
and [60 evaluation tasks](../../data/splits/authored600_v2/eval.txt); independent
seed-only/Syn studies use their approved task lists and source library.

- `--updates` is an absolute total: resuming update 40 with `50` performs 41–50.
  It does not insert a cold-resume probe. Omission retains the saved
  `initial_updates` / `resume_updates` semantics.
- `--resume-from PREVIOUS_RUN` restores full native state, optimizer and pool,
  not just HF weights. Use the parent's saved configuration without
  `--machine-profile`; keep task/release inputs unchanged.
- An independent seed uses a fresh run without `--resume-from`.
  `--training-seed` controls pool ordering and trainer/engine initialization,
  not a fixed seed on every model request.
- Existing runs keep frozen source and Sim, and continuation always runs the
  run's own snapshot.

The saved profile is authoritative for concurrency, replenishment and cooldowns;
see [task-pool semantics](../../docs/training_task_pool.md). Do not reconstruct
those choices in a per-run script. Qwen `xhigh` belongs in
`sampling.chat_template_kwargs`, not the generic SGLang effort field.

### Prepare without launching

```bash
uv run behave-train prepare PLAN_DIRECTORY --repository /path/to/behave \
  --configuration configs/train/qwen27b_rl.example.json \
  --machine-profile /path/to/my-machine.json \
  --release RELEASE --train-split TRAIN_SPLIT --task TASK_ID

uv run behave-train prepare-study --repository /path/to/study-inputs \
  --settings /path/to/reviewed-study.json --output /path/to/new-study
```

Repeat `--task` for selected tasks. `prepare` retains split/corpus guards and
`source_split.json`; `prepare-study` freezes seed/acquisition stages, HF milestones,
role guides and the selected library. The explicit repository resolves their
inputs, not the package installation directory. Neither command starts inference,
acquisition or training; the native trainer owns subsequent acquisition callbacks.
If the study's `training_configuration` names a split experiment, pass the same
`--machine-profile` option. Preparation saves resolved flat configs; subsequent
training/acquisition does not reload that profile. Flat study configs remain valid
without it, and study HF milestones still follow the acquisition schedule.

Native `prepare` reads recipes, split lists and input receipts as regular files
(symlinks to regular files are allowed), rejects duplicate JSON fields, and opens
parent checkpoint objects read-only. Input failures return JSON with a code and
message and exit status 1; preparation never launches training.

## Recorded learning metrics

```bash
uv run behave-train report /path/to/run /path/to/report
```

This exports recorded per-round/task scores, reward groups, sampling receipts,
token availability and optimizer/timing evidence. It does not load weights,
replay rollouts, score candidates or modify the run. Use an explicit active or
staged run directory; it does not discover or restore cold archives. Missing
measurements stay blank, including empty reward groups and incomplete optimizer
ranks. These are training-pool observations, not benchmark evaluation results.
Native stdout/stderr and rank timing logs are read sequentially, one line at a
time; only parsed measurements are retained, not full serving logs.

`train.learning_report` owns these calculations; `behave-train report RUN OUTPUT`
writes them.

## Storage and runtime boundaries

| Location | Contents |
| --- | --- |
| Node-local scratch | Mutable builds, sandbox execution, staged weights and caches |
| Configured fast shared run directory | Control records, pool/memory, journals and committed feedback |
| Explicit checkpoint destination | Full native checkpoints and permanent HF exports |
| Cold archive | Verified payloads and inventories, not a small-file working directory |

`--checkpoint-directory` sets the large-weight destination. An optional local
`infra/train/checkpoint_storage.json` can supply site storage defaults. Keep
active run state on fast storage. If relocation is configured, `BEHAVE_RUNTIME_LOCATIONS` must resolve
to the intended destinations; preflight rejects mapping the new cold save root
back onto NFS. Changing a profile never redirects an already running writer.

The maintained profile keeps the latest full state and protected anchors, retains
HF exports, and enables inventoried deletion. Legacy `mode: archive` only moves
files and does not free space. Deletion requires a committed replacement and
never targets HF, pool journals or traces. `retire_parent: true` is an explicit
ownership transfer after a child commits recovery state. Protect shared forks
with absolute `protected_paths` or `retention_pins.json`, e.g. `{"steps": [2]}`
for human update 3 (native indices are zero-based).

Cold publication and eviction use `runtime.model_archive`: retain ownership and
verification receipts, fence consumers, then release only inventoried hot files
whose retained copy still matches. Legacy receipts may require one sequential
verification. Failed verification retains the hot data. Private staged weights
are released only after confirmed worker quiescence; installation/kernel caches
and interrupted scratch are not swept automatically.

New production snapshots omit tests; validation opts in. Historical manifests
remain authoritative on replay. Immutable publication uses bounded I/O queues;
Syn resolves referenced objects without scanning an entire historical store.
Small control-record I/O retries do not retry model calls, launches or updates.
`launch-error` records distinguish startup from supervision faults; local process
cleanup alone does not prove remote-worker quiescence.

Sandbox startup has no extra throttle when `isolation_startup_slots` is `null`;
only a positive integer enables one. Actor-constructor readiness is bounded by
`--behave-actor-startup-seconds` (default 1,800); `BEHAVE_ACTOR_STARTUP` identifies
pending ranks. These checks do not change sampling or communication deadlines.

## Recovery

### Moving an interrupted rollout to another node

Fence the old trainer, engines and candidate workers, then use a fresh run and
allocation with `--resume-from RUN --resume-rollouts --workers-stopped`.
The last flag confirms external fencing; it does not stop remote workers.

The handoff imports completed candidates only from the first uncommitted rollout
under the last saved weights (or original base before the first checkpoint).
Scores, zero outcomes, tokens and old log probabilities stay unchanged; missing
candidate slots restart, not individual turns. Group selection and pool commits
preserve cooldown/history. Unsaved later weight versions cannot be reused with
an older checkpoint. Acquisition recovery additionally requires its own binding
and ownership checks in `train.acquisition_fence`.

Sampling, seeds, task semantics, prompts, Sim and optimizer recipe must match.
Explicit serving changes use `--rollout-gpus-per-engine`, `--exclude-rollout-gpu`
(TP1 only) and `--workers`. Actor changes use `--actor-gpus-per-node` and
`--context-parallel`; supported CP resharding retains TP/PP/DP. Excluding a rollout
GPU does not exclude an actor rank. Verify physical placement and memory on the
node; topology/concurrency changes do not promise identical outputs or RNG streams.

## Implementation and compatibility

| Owner | Responsibility |
| --- | --- |
| `behave.runtime` | Supervision, receipts, snapshots, environment and model publication/staging |
| `behave.train` | Preparation, submission, execution, checkpoint/pool state and recovery |
| `behave.improve` | Study preparation, acquisition, library and publication orchestration |
| Machine/site configuration | Allocation policy, installed paths, budgets and storage mounts |

`behave-infra train` uses the same commands. Existing `tools.slime_training_job`,
`slime_training_plan`, `training_runtime` and `synthesis_pilot_plan` entries delegate
to package code and keep their checkout defaults. Checkpoint helper aliases and
the historical standalone watcher remain compatible. `train.job_stop` is the
stdlib-only authenticated stop helper; the old script delegates to it without
requiring BEHAVE on the node's system Python.

Existing jobs continue from their frozen source; editing a checkout does not
upgrade a running experiment.
