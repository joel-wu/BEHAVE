# Training task pool

The optional `curriculum` runtime field changes **which tasks launch next**.
The optional `group_replenishment` field selects complete reward-varying groups
within an update. Neither changes final scores, within-group GRPO credit, native
token evidence or loss masks.

## Runtime

`configs/train/qwen27b_rl.example.json` declares the shared task-pool policy.
Task training and self-improvement use the same implementation:

```json
{
  "training_seed": 20260918,
  "sampler": {"tasks_per_update": 12, "order": "shuffle", "seed": "20260918"},
  "update_groups": 8,
  "group_replenishment": {"max_groups": null},
  "curriculum": {
    "mastery_cooldown_updates": 10,
    "zero_cooldown_updates": 2,
    "incomplete_priority_slots": 2,
    "cooldown_fallback": true
  }
}
```

`samples_per_task` defines group size. Cooldowns count subsequent optimizer
updates: full success at update 0 cools a task through update 10; an all-zero
group cools it through update 2. With `cooldown_fallback`, cooling tasks follow
all eligible tasks in the reserve and may be used before expiry. Without it,
they are excluded until expiry. Expiry never makes a task mandatory.

## Same-update replenishment

With `group_replenishment` enabled, `sampler.tasks_per_update` is the active
unfinished-group window, not the final number of tasks attempted. `workers`
separately caps active whole conversations, including tool time. The maintained
example machine profile uses 32 workers; the recipe selects eight groups of
eight candidates from a 12-group window.

The shared Eval/Train batch executor admits candidates lazily. As a complete
group finishes, equal rewards (including all 0, all 0.5 or all 1) cause another
approved task to enter the window. A mixed group such as 7/8 remains intact.
The first `update_groups` complete reward-varying groups enter training. After
that, surplus work is cancelled and cleaned up through the existing executor.
No within-group candidate is replaced according to its score.

`max_groups: null` permits one sweep of the approved reserve for that update,
including its cooling tail when `cooldown_fallback` is enabled.
An integer caps that reserve explicitly. No held-out task is added and no task
is silently resampled after reserve exhaustion. If the pool cannot supply the
target, the run retains its report with `insufficient_groups` and does not run
a shortened optimizer update.

Already-finalized missing scores remain zero. A group lacking usable native
token evidence cannot supply a complete training group and is marked
`unavailable`. Unstarted reserve slots are `candidate-not-admitted`, not failed
samples. Reports distinguish planned, launched and selected counts. Accuracy
and learning curves must not use the full reserve as their denominator.

## Selection and observations

- One complete group with every reward equal to 1 triggers the runtime success
  cooldown. This is an estimate from sampled attempts, not a correctness proof.
- One complete all-zero group triggers the shorter runtime zero cooldown.
  All other rewards remain eligible, including an all-0.5 group. The latter is
  still filtered from that update because it has no relative GRPO signal.
  A 7/8 success group remains active and keeps its usual GRPO advantage.
- Eligible tasks share one least-recently-launched ordering. Unseen tasks come
  first; the configured cycle/shuffle order breaks ties deterministically.
  Expired tasks receive no separate priority or reserved slot.
- Optional `curriculum.incomplete_priority_slots` moves up to that many tasks
  with no complete usable observation to the front of the initial window.
  These tasks use the same least-recently-launched ordering and seeded tie-break,
  so a cancelled priority task retains eligibility without monopolizing retries.
  The maintained profile reserves two priority positions, not two guaranteed
  completions. Remaining positions follow ordinary rotation and may also contain
  unobserved tasks. This priority applies only to eligible tasks.
- With `curriculum.cooldown_fallback`, cooling tasks form the reserve's tail,
  ordered by earliest expiry, then last launch and seeded tie-break. Cooldown
  timestamps are not reset. Without this option, cooling tasks stay excluded.
- If the resulting reserve has fewer groups than the required update target,
  the native journal records
  `task_pool_insufficient` and reports the counts before launching a rollout.
  No optimizer update or artificial cursor advance occurs. A run
  that exhausts its pool needs an explicit operator decision.
- Replenishment may therefore use a reserve smaller than the approved pool or
  active window, provided it still contains enough groups for the update.
  No task is deleted or permanently retired.

With replenishment, all complete usable groups observed before the target is
met update curriculum state, including constant groups filtered from training.
Otherwise an all-correct task would never become eligible for cooldown.
All actually launched tasks advance their rotation timestamp. Unstarted reserve
tasks and late cancelled surplus groups do not become all-zero observations.
Without replenishment, the earlier first-complete-group behavior is unchanged.

Priority changes only launch order. Reaching the required number of reward-varying
groups still cancels surplus immediately, including unfinished priority tasks.
No cross-update partial groups are combined, and no extra waiting is introduced.

`group_selection.observations` retains each observed group's members, rewards
and disposition. Selected-only training metrics are not an unbiased measurement
of overall task accuracy: the selection deliberately removes constant groups.
`behave-train report` exports these observations as `group_outcomes.csv`,
alongside planned/launched/selected and filtered counts in `sampling.csv`.

## Persistence and reuse

`ScoredDataSource` records the explicit selection and reasons in the policy
record and scored receipt. Both sides of the control/native bridge use that
same list. `round_scored.task_pool` references the resulting small state record,
attached to the existing native checkpoint closure. It stores revision,
last launch/observation, category, cooldown and latest group rewards.

Cold recovery restores the state paired with the saved checkpoint. Explicit
rollback removes the unsaved tail from the logical state. Reusing its first
complete scored batch observes that batch once, not twice. Deliberately training
again on recorded offline samples does not supply new evidence of mastery.

`training_seed` is an optional top-level configuration field. Preparation resolves
it to both `sampler.seed` and native `--seed`; Slime derives each SGLang engine's
initial RNG seed from that native seed and rank. It does not reset every request
to the same seed. Old configurations without this field remain unchanged.
Independent-seed experiments start from the same base model with new optimizer
and pool state, in separate output directories. Do not confuse a random seed
with selection of a different seed-task set. Async execution is not guaranteed
to reproduce an identical token stream even with the same seed.

Self-improvement already uses this entry chain:

```text
approved new/replay task manifest
  -> improve.learning_round.learn_round
  -> train.slime_data.prepare_task_plan
  -> train.slime_stage.run_learning
  -> ScoredDataSource -> shared rollout/scoring -> native Slime/Megatron update
```

Pass the parent checkpoint and the next approved task manifest. Unchanged
revisions inherit task state; new or revised tasks start fresh. Retained records
for absent tasks do not authorize sampling them. Replay membership remains
explicit, and evaluation tasks cannot be added through the sampler. Acquisition,
construction and task approval remain self-improvement's additional stages.
Raw-text learning keeps its separate data objective and is not given task
mastery statistics or a duplicate task sampler.

## Validation

Run the focused scheduler and integration tests with:

```bash
.venv/bin/python -m pytest -q tests/infrastructure/test_training_task_pool.py tests/infrastructure/test_slime_task_pool.py tests/infrastructure/test_improvement_learning_round.py
```

The tests cover full/mixed/constant rewards, group completeness, surplus
cancellation, deterministic rotation, checkpoint recovery, rollback, parent
adoption, and the shared self-improvement entry. GPU save/resume checks require
the native training environment.
