# Evaluation

`behave-eval` runs an agent against released tasks and scores what it submits.
Preparation is offline; running a plan calls the configured model; scoring
calls the simulator only.

## Endpoint

Copy one example and fill in the endpoint:

- [`configs/eval/local.example.json`](../configs/eval/local.example.json) for a
  server you already run (SGLang or vLLM, Chat Completions). Set the model
  name, the weights revision and the URL. The command never launches a server.
- [`configs/eval/hosted.example.json`](../configs/eval/hosted.example.json) for a
  hosted provider. Set the model, the deployment revision and the credential
  file reference. Create that file once with `uv run behave-infra credentials` from an
  environment variable; the recipe never contains key values or arbitrary URLs.

Sampling, context size, turn limit, tool-output preview and per-request timeout
are explicit fields of the recipe. The examples are a one-worker starting point,
not the paper's settings. Hosted recipes carry `prices` and `spending`; leave
them `null` to run without a budget, or fill in your price snapshot and a
`SpendPolicy` to cap spending across runs.

## Prepare, run, score

```bash
uv run behave-eval prepare --configuration my-eval.json \
  --task-objects releases/local-inputs/objects --release releases/local-inputs/release.json \
  --task TASK_ID --output prepared-eval          # repeat --task for more tasks
uv run behave-eval --plan prepared-eval/plan.json --task-objects prepared-eval/inputs --dry-run
uv run behave-eval --plan prepared-eval/plan.json --task-objects prepared-eval/inputs --run-directory prepared-eval/run
uv run behave-eval --score initial --judge-workers 4 --task-objects prepared-eval/inputs --run-directory prepared-eval/run
uv run behave-eval export prepared-eval/run                # add --judgment NAME for a saved scoring pass
```

- `prepare` copies the selected tasks' inputs into `prepared-eval/inputs` and
  pins the plan. Missing inputs fail preparation; the batch is never silently
  smaller than requested. The output directory must be new.
- `--dry-run` validates the plan without starting a worker.
- The run writes one journal per candidate under the run directory. Rerun the
  same command to resume: committed candidates are not regenerated and
  unsettled model calls go through the recovery checks before any retry.
- `--score NAME` judges saved submissions into `judgments/NAME` without model
  calls. The judge policy comes from the release; `--judge-policy` overrides it
  explicitly. `--rollout-only` saves submissions without scoring; set
  `score_on_submit` in the recipe to score immediately instead.
- `export` renders the sealed results and their accounting as a report; `--judgment NAME`
  reads a saved scoring pass instead of the original report.

The task inputs are released task files, see [tasks](tasks.md). The release
decides what the agent sees (specification, declared interface, any public
support files) and what only the judge sees (reference models, judge-only
references).

## Scores

Each candidate gets a per-component judgment from Sim (and from the testbench
component when the task has one). A missing submission scores zero; a judge
that could not run leaves the candidate unresolved and the report says so. The
report never invents a score for an unresolved candidate.

## Running many models or checkpoints

`behave-eval controller` and `behave-eval followup` drive saved plans across a
Slurm allocation: generation on GPU nodes, scoring on CPU nodes, retries of
infrastructure failures without resampling settled candidates. They are
operator entries and need an existing allocation; the commands above do not.
