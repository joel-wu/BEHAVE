# Reviewer Sim checks

Use the existing Bash tool:

```bash
python /tools/behave_sim_check.py review/sim_check.json
```

The supplied request selects B2 as `behavior`, B1 as `reference_behavior`, and
the task's contract and generation policy. If B2 is unavailable, it selects B1
alone for an execution self-check, which does not verify functional correctness.
The `spec` selection checks the public RTL declaration against the contract
first, without implementing or functionally testing RTL. Keep that selection
when checking an interface revision; an interface refusal is not a solver score.

To investigate a hypothesis, copy the request to a scratch JSON file and adjust
the selections. Each selection contains `files`, `entry_file` and `entry_symbol`,
using workspace-relative paths. Include imported support files. Inputs under
`draft/`, `reconstruction/`, `source/` and `review/` are read-only; keep trial
implementations in scratch. No hidden reference is supplied by this command.

- B/B: use `behavior` for the candidate and `reference_behavior` for the reference.
- B/R: use `rtl` for the RTL candidate and `behavior` for its reference. Omit
  `reference_behavior` in this form.
- Self-check: select only `behavior`.
- Explicit replay: add `replay_stimuli` with the path to a JSON object containing
  a `stimuli` array of Sim-native stimulus records. When available,
  `review/replay_stimuli.json` contains the coordinator's recorded inputs in this
  exact format, including after a passing comparison. Copy it to scratch and
  select or modify records there. Alternatively, wrap a linked `stimulus.json`
  record as `{"stimuli": [<record>]}` without changing its schema.
  A record's `queues` contains `[endpoint_name, payload_rows]` pairs; each payload
  row follows that endpoint's declared field order, not a dictionary of pin names.
  Preserve its clock/reset and memory settings. Runtime `replay_policy` records
  and fixed `slots`/`clock_schedule` records are different forms; do not mix them.
  The shared Sim replay path handles the declared interfaces, clocks and memory.

Without explicit replay, Sim generates tests using the task/runtime settings.
Execution budgets stay with runtime. All selected source and stimulus bytes,
results and diagnostic files are retained with the attempt.

The reply points to `report.md` and linked diagnostics under `sim_check/`.
A match exits 0, a mismatch exits 1, and an invalid or unavailable result exits 2.
Unavailable is not a pass. Inspect the report and inputs/observations, not just
the shell exit code. Supplemental checks help support findings but do not
replace the coordinator's comparison or establish intended-function correctness.

`review/behavior_environment.json` describes current public Behavior capabilities.
Reuse `/docs/behavior.md` and the shared numerical/interface guides.
