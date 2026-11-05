"""Whole-group admission; optional replenishment keeps reward-varying groups.
"""

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import canonical_json, fields, parse_json, text, thaw_json, version
from behave.core.storage import DirectoryStore
from behave.data.published import TaskManifest
from behave.task_execution.judge_policy import JudgePolicy

from .plan import TrainingManifest, TrainingPlan
from .recorded import token_status
from .trainer import active_events


class CompletedGroups:
    def __init__(self, group_ids, update_groups):
        self.group_ids = tuple(group_ids)
        self.members = {}
        for index, group in enumerate(self.group_ids):
            if group is None:
                raise ValueError("oversampling requires explicit task groups")
            self.members.setdefault(group, set()).add(index)
        if type(update_groups) is not int or not 1 <= update_groups <= len(self.members):
            raise ValueError("update_groups must be between one and the launched group count")
        self.update_groups = update_groups
        self.completed = set()
        self.order = []
        self.stop = Event()

    def complete(self, index):
        # Called serially, after each case is committed to the batch journal.
        self.completed.add(index)
        group = self.group_ids[index]
        if group not in self.order and self.members[group] <= self.completed:
            self.order.append(group)
            if len(self.order) >= self.update_groups:
                self.stop.set()

    def summary(self):
        if len(self.order) < self.update_groups:
            raise ValueError("not enough completed groups for this update")
        selected = self.order[:self.update_groups]
        return {"strategy": "first-complete-groups", "update_groups": self.update_groups,
                "launched_groups": len(self.members), "launched_candidates": len(self.group_ids),
                "completion_order": list(self.order), "selected_groups": selected,
                "selected_indices": [i for i, group in enumerate(self.group_ids) if group in selected]}


class ReplenishingGroups(CompletedGroups):
    """Stream a fixed approved reserve, with a runtime-sized active group window.

    Selection uses complete groups, never individual candidate rewards. Exhausting
    the declared reserve without the target leaves a report but no optimizer batch.
    """

    def __init__(self, group_ids, update_groups, active_groups, *, requirements=None,
                 verifier=None, token_statuses=None):
        super().__init__(group_ids, update_groups)
        if type(active_groups) is not int or active_groups < 1:
            raise ValueError("active_groups must be positive")
        self.active_groups = active_groups
        self.requirements, self.verifier = requirements, verifier
        self.token_statuses = token_statuses
        self.launched = set()
        self.finished_groups = set()
        self.rewards = {}
        self.observations = []
        self.selected = []

    def admit(self, pending, capacity):
        if self.stop.is_set():
            return ()
        window = set([group for group in self.members if group not in self.finished_groups][:self.active_groups])
        return tuple(index for index in pending if self.group_ids[index] in window)[:capacity]

    def observe(self, index, row, reader):
        import json

        from behave.core.artifacts import BlobRef
        from behave.task_execution.result import final_score_from_record

        from .pool import has_rollout

        if (row.get("error") or {}).get("code") == "candidate-not-admitted":
            return
        eligible = has_rollout(row, reader, token_statuses=self.token_statuses,
                               requirements=self.requirements, verifier=self.verifier)
        result = None if row["result"] is None else json.loads(reader.read(BlobRef.from_dict(row["result"])))
        if result is not None and result.get("failure_code") == "solver-worker-cancelled":
            eligible = False
        self.record(index, None if result is None else final_score_from_record(result), eligible=eligible)

    def record(self, index, reward, *, eligible=True):
        self.launched.add(index)
        if index in self.completed:
            return
        self.completed.add(index)
        if eligible:
            self.rewards[index] = reward
        group = self.group_ids[index]
        members = self.members[group]
        if group in self.finished_groups or not members <= self.completed:
            return
        self.finished_groups.add(group)
        self.order.append(group)
        # Late cleanup/cancellation of surplus work is not a mastery observation.
        if self.stop.is_set():
            return
        indices = sorted(members)
        rewards = [self.rewards.get(i) for i in indices]
        complete = all(value is not None for value in rewards)
        varied = complete and len(set(rewards)) > 1
        self.observations.append({"group_id": group, "members": indices,
            "rewards": rewards, "status": "selected" if varied else "constant" if complete else "unavailable"})
        if varied:
            self.selected.append(group)
            if len(self.selected) == self.update_groups:
                self.stop.set()

    def summary(self):
        return {"strategy": "first-reward-varying-groups",
                "status": "ready" if self.stop.is_set() else "insufficient_groups",
                "update_groups": self.update_groups, "active_groups": self.active_groups,
                "planned_groups": len(self.members), "planned_candidates": len(self.group_ids),
                "launched_groups": len({self.group_ids[i] for i in self.launched}),
                "launched_candidates": len(self.launched), "launched_indices": sorted(self.launched),
                "completion_order": list(self.order), "observations": list(self.observations),
                "selected_groups": list(self.selected),
                "selected_indices": [i for i, group in enumerate(self.group_ids) if group in self.selected]}


def selected_indices(report):
    selection = report.get("group_selection")
    if selection is not None and selection.get("status") == "insufficient_groups":
        raise ValueError("approved task reserve exhausted or cancelled before enough reward-varying groups; no update")
    return (tuple(range(len(report["candidates"]))) if selection is None
            else tuple(selection["selected_indices"]))


def inherited_handoff(previous, configuration, *, completed):
    previous = Path(previous)
    continuation_path = previous / "continuation.json"
    if not continuation_path.is_file():
        return None
    continuation = json.loads(continuation_path.read_text())
    descriptor = continuation.get("rollout_recovery")
    if descriptor is None:
        return None
    native = JournalDirectory(previous / "artifacts/plan/rounds/native")
    with native.owner():
        # Once any round began, use ordinary rollout recovery instead; the old
        # imported handoff may no longer describe all newly completed work.
        if native.journal("rounds").load(verify_artifacts=False):
            return None
        runtime = json.loads((previous / "runtime.json").read_text())
        phases = runtime.get("phases", [])
        if (runtime.get("state") != "interrupted" or not phases
                or any(row.get("report", {}).get("process", {}).get("cleanup") != "complete" for row in phases)):
            raise ValueError("startup handoff requires settled native processes with complete cleanup")
        from .slime_rollout import validate_execution_change

        validate_execution_change(json.loads((previous / "configuration.json").read_text()), configuration)
        binding = json.loads((previous / "artifacts/plan/binding.json").read_text())
        reader = DirectoryStore(descriptor["objects"])
        record = parse_json(read_verified(reader, BlobRef.from_dict(descriptor["record"])))
        if (record.get("schema") != "behave.rollout-handoff/1" or record.get("workers_stopped") is not True
                or record.get("round") != completed or continuation.get("first_update") != completed
                or record.get("checkpoint") != continuation.get("checkpoint")
                or record.get("checkpoint") != binding.get("parent_checkpoint")
                or record.get("manifest") != binding.get("manifest")
                or record != binding.get("rollout_recovery")):
            raise ValueError("inherited startup handoff differs from the prepared checkpoint/policy")
        # Keep the immutable descriptor and its retained input store. The
        # ordinary attach_handoff validates the policy and candidates again.
        # Do not duplicate hundreds of thousands of evidence objects on NFS.
        return descriptor


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    tasks_per_update: int
    order: str
    seed: str

    def __post_init__(self):
        if type(self.tasks_per_update) is not int or self.tasks_per_update < 1:
            raise ValueError("tasks_per_update must be positive")
        if self.order not in {"cycle", "shuffle"}:
            raise ValueError("task order must be cycle or shuffle")
        text(self.seed, "sampler seed")

    def as_dict(self):
        return {"tasks_per_update": self.tasks_per_update, "order": self.order, "seed": self.seed}

    @classmethod
    def from_dict(cls, value):
        return cls(**fields(value, {"tasks_per_update", "order", "seed"}))

    def select(self, tasks: tuple[str, ...], update: int) -> tuple[str, ...]:
        if type(update) is not int or update < 0:
            raise ValueError("sampler cursor must be a nonnegative update index")
        if len(set(tasks)) != len(tasks) or self.tasks_per_update > len(tasks):
            raise ValueError("a minibatch needs distinct tasks from the declared pool")
        if self.order == "cycle":
            start = update * self.tasks_per_update
            return tuple(tasks[(start + index) % len(tasks)] for index in range(self.tasks_per_update))
        # A seeded hash permutation is stable across processes/Python versions.
        # Each update samples without replacement; tasks can recur in subsequent
        # updates. This is not an epoch-coverage guarantee or a difficulty score.
        return tuple(sorted(tasks, key=lambda task: BlobRef.of(canonical_json({
            "schema": "behave.task-sampling/1", "seed": self.seed, "update": update, "task": task,
        })).sha256)[:self.tasks_per_update])


def sample_plan(plan: TrainingPlan, policy: SamplingPolicy, update: int) -> TrainingPlan:
    tasks = tuple(dict.fromkeys(job.task.task_id for job in plan.execution.jobs))
    return select_tasks(plan, policy.select(tasks, update))


def task_reserve(tasks, policy, update):
    """Initial sampled tasks followed by a deterministic, duplicate-free reserve."""
    initial = policy.select(tasks, update)
    order = replace(policy, tasks_per_update=len(tasks)).select(tasks, update)
    return initial + tuple(task for task in order if task not in initial)


def replenishment_limit(value, *, target, window, pool_size):
    """None means the approved pool, not an implicit retry or rollout budget."""
    row = fields(value, {"max_groups"})
    limit = row["max_groups"]
    if target is None or not 1 <= target <= window <= pool_size:
        raise ValueError("replenishment requires update_groups <= active groups <= approved pool")
    if limit is not None and (type(limit) is not int or limit < target):
        raise ValueError("group_replenishment.max_groups must cover update_groups or be null")
    return pool_size if limit is None else min(limit, pool_size)


def select_tasks(plan: TrainingPlan, selected: tuple[str, ...]) -> TrainingPlan:
    """Keep entire task groups in the requested order, without changing credit."""
    tasks = {job.task.task_id for job in plan.execution.jobs}
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= tasks:
        raise ValueError("selected tasks must be distinct members of the approved pool")
    indices = tuple(index for task in selected for index, job in enumerate(plan.execution.jobs)
                    if job.task.task_id == task)
    return replace(plan, execution=replace(plan.execution,
        jobs=tuple(plan.execution.jobs[index] for index in indices)),
        group_ids=tuple(plan.group_ids[index] for index in indices))


@dataclass(frozen=True, slots=True)
class TaskPoolPolicy:
    mastery_cooldown_updates: int
    zero_cooldown_updates: int
    incomplete_priority_slots: int = 0
    cooldown_fallback: bool = False

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "cooldown_fallback":
                if type(value) is not bool:
                    raise ValueError("cooldown_fallback must be boolean")
                continue
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")

    def as_dict(self):
        # Keep legacy bindings byte-compatible when the optional policy is off.
        return {name: getattr(self, name) for name in self.__dataclass_fields__
                if name not in {"incomplete_priority_slots", "cooldown_fallback"} or getattr(self, name)}

    @classmethod
    def from_dict(cls, value):
        required = {"mastery_cooldown_updates", "zero_cooldown_updates"}
        return cls(**fields(value, required | ({"incomplete_priority_slots", "cooldown_fallback"} & value.keys())))


def empty_state():
    return {"schema": "behave.training-task-pool/1", "tasks": {}}


def sync_state(state, tasks):
    """Preserve unchanged revisions across learning passes; new revisions start fresh.

    Retain absent tasks' small records for a later explicit replay pool. They are
    never eligible unless the current training manifest includes them.
    """
    version(state["schema"], "behave.training-task-pool/1")
    result = deepcopy(state)
    for task_id, revision in tasks:
        row = result["tasks"].get(task_id)
        if row is None or row["revision"] != revision.as_dict():
            result["tasks"][task_id] = {
                "revision": revision.as_dict(), "last_selected": -1, "last_observed": -1,
                "observed_groups": 0,
                "category": "active", "cooldown_until": 0, "last_rewards": [],
            }
    return result


def select_pool(tasks, state, sampler, update, *, include_reserve=False, policy=None):
    """Rotate eligible tasks, with unseen tasks first and no forced revisits.

    Legacy policies exclude cooling tasks. With cooldown_fallback, they form a
    deterministic tail of the reserve, earliest expiry first. No state is reset.
    """
    state = sync_state(state, tasks)
    ids = tuple(task for task, _ in tasks)
    sampler.select(ids, update)  # Validate the configured launch count.
    order = replace(sampler, tasks_per_update=len(ids)).select(ids, update)
    ranks = {task: i for i, task in enumerate(order)}
    rows = state["tasks"]
    eligible = (task for task in ids if rows[task]["cooldown_until"] <= update)
    ordered = sorted(eligible, key=lambda task: (rows[task]["last_selected"], ranks[task]))
    slots = 0 if policy is None else policy.incomplete_priority_slots
    if slots > sampler.tasks_per_update:
        raise ValueError("incomplete priority slots must fit the initial task window")
    priority = tuple(task for task in ordered if rows[task]["observed_groups"] == 0)[:slots]
    # Only reorder the approved reserve. Completion, cancellation, group credit,
    # cooldowns and the first-eight-valid-groups stop rule remain unchanged.
    ordered = [*priority, *(task for task in ordered if task not in priority)]
    cooling = ()
    if policy is not None and policy.cooldown_fallback:
        cooling = tuple(sorted((task for task in ids if rows[task]["cooldown_until"] > update),
            key=lambda task: (rows[task]["cooldown_until"], rows[task]["last_selected"], ranks[task])))
        ordered.extend(cooling)
    if not include_reserve:
        ordered = ordered[:sampler.tasks_per_update]
    return tuple({"task_id": task,
                  "reason": "cooldown-fallback" if task in cooling else
                            "incomplete-priority" if task in priority else
                            "new" if rows[task]["last_selected"] < 0 else "rotation"}
                 for task in ordered)


def observe_batch(state, plan, candidates, policy, update, *, launched_indices=None):
    """Record one fresh update; reward settlement and GRPO credit stay upstream."""
    tasks = tuple(dict.fromkeys((job.task.task_id, job.task.revision) for job in plan.execution.jobs))
    result = sync_state(state, tasks)
    groups = {}
    for index, (job, group) in enumerate(zip(plan.execution.jobs, plan.group_ids, strict=True)):
        groups.setdefault(group, []).append(job.task.task_id)
        if launched_indices is None or index in launched_indices:
            result["tasks"][job.task.task_id]["last_selected"] = update
    observed = {}
    for candidate in candidates:
        observed.setdefault(candidate["group_id"], []).append(candidate["reward"])
    for group, rewards in observed.items():
        members = groups[group]
        if len(rewards) != len(members) or len(set(members)) != 1:
            raise ValueError("task-pool observations require a complete single-task group")
        row = result["tasks"][members[0]]
        all_correct = all(reward == 1 for reward in rewards)
        all_zero = all(reward == 0 for reward in rewards)
        row.update(last_observed=update, observed_groups=row["observed_groups"] + 1,
                   last_rewards=list(rewards), category="active", cooldown_until=0)
        if all_correct:
            row.update(category="mastered", cooldown_until=update + policy.mastery_cooldown_updates + 1)
        elif all_zero:
            row.update(category="zero", cooldown_until=update + policy.zero_cooldown_updates + 1)
    return result


def read_pool(reader, events):
    for event in reversed(active_events(events)):
        if event.kind == "round_scored" and "task_pool" in event.payload:
            return parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(event.payload["task_pool"]))))
    return None


def definition_refs(reader, revisions):
    """Only task manifests and judge settings, not referenced source contents."""
    result = []
    for revision in dict.fromkeys(revisions):
        task = TaskManifest.from_bytes(read_verified(reader, revision))
        result.extend((revision, task.judge_policy))
    return tuple(dict.fromkeys(result))


def resume_pool(state, tasks, old_reader, new_reader):
    """Retain observations and absolute cooldowns for Sim-version-only revisions.

    Other task changes still start fresh through sync_state. The original state
    remains in the parent checkpoint and the continuation's before/after file.
    """
    result = deepcopy(state)
    carried, changed = [], []
    policies = {}

    def policy(reader, reference):
        # A task pool usually shares a handful of immutable scoring policies.
        # Keep only parsed policy metadata for this call, separately per reader:
        # a good old copy must not hide a corrupt copy in the new input store.
        key = (id(reader), reference)
        if key not in policies:
            policies[key] = JudgePolicy.from_bytes(read_verified(reader, reference))
        return policies[key]

    for task_id, revision in tasks:
        row = result["tasks"].get(task_id)
        if row is None or row["revision"] == revision.as_dict():
            continue
        old = TaskManifest.from_bytes(read_verified(old_reader, BlobRef.from_dict(row["revision"])))
        new = TaskManifest.from_bytes(read_verified(new_reader, revision))
        old_policy = policy(old_reader, old.judge_policy)
        new_policy = policy(new_reader, new.judge_policy)
        same_task = replace(old, judge_policy=new.judge_policy) == new
        same_settings = replace(old_policy, command_version=new_policy.command_version,
                                authority_version=new_policy.authority_version) == new_policy
        if same_task and same_settings:
            row["revision"] = revision.as_dict()
            carried.append(task_id)
        else:
            changed.append(task_id)
    return result, {"sim_version_only": carried, "changed_tasks": changed}


def is_upgrade(old, new):
    before, after = (row.get("curriculum", {}).get("cooldown_fallback", False) for row in (old, new))
    return before is False and after is True


def extension_for(binding, policy, original, state, reader, curriculum):
    """Keep every old slot in place; append only previously absent pool tasks."""
    if "group_replenishment" not in binding or not curriculum.cooldown_fallback:
        raise ValueError("cooldown recovery requires replenishment and explicit fallback")
    tasks = TrainingManifest.from_bytes(read_verified(reader, BlobRef.from_dict(binding["manifest"]))).tasks
    sampler = SamplingPolicy.from_dict(binding["sampler"])
    reserve = select_pool(tasks, state, sampler, policy["round"], include_reserve=True, policy=curriculum)
    old = tuple(dict.fromkeys(original.group_ids))
    selected = tuple(row["task_id"] for row in policy["task_selection"])
    if old[:len(selected)] != selected or not set(old) <= {task for task, _ in tasks}:
        raise ValueError("saved task reserve is not an append-only policy extension")
    limit = replenishment_limit(binding["group_replenishment"], target=binding["update_groups"],
        window=sampler.tasks_per_update, pool_size=len(tasks))
    if len(old) > limit:
        raise ValueError("saved reserve exceeds the configured group limit")
    added = [row for row in reserve if row["task_id"] not in old][:limit-len(old)]
    prior_rows = {row["task_id"]: row for row in binding.get("rollout_recovery", {}).get("task_selection_extension", [])}
    prior = [prior_rows[task] for task in old[len(selected):]]
    return [*prior, *added]


def inspect_extension(info, configuration):
    if not configuration.get("curriculum", {}).get("cooldown_fallback", False):
        return None
    previous = info["binding"].get("rollout_recovery", {})
    inherited = (previous.get("task_selection_extension")
                 if previous.get("round") == info["step"] + 1 else None)
    if info["scored_receipt"] is not None:
        return inherited
    from .plan import TrainingPlan
    reader = info["run"].objects
    original = TrainingPlan.from_bytes(read_verified(reader, info["original_plan"]), reader)
    state = read_pool(info["native"].objects, info["events"])
    if state is None:
        from behave.core.storage import DirectoryStore
        inputs = DirectoryStore(info["binding"]["objects"])
        ref = info["binding"].get("parent_task_pool")
        if ref is not None:
            state = parse_json(read_verified(inputs, BlobRef.from_dict(ref)))
    if state is None:
        raise ValueError("cooldown recovery requires retained pool observations")
    return extension_for(info["binding"], info["policy"], original, state, reader,
                         TaskPoolPolicy.from_dict(configuration["curriculum"]))


def extended_selection(binding, round_id, task_selection):
    record = binding.get("rollout_recovery", {})
    extension = record.get("task_selection_extension") if record.get("round") == round_id else None
    if extension is None:
        return task_selection
    if not binding.get("curriculum", {}).get("cooldown_fallback", False):
        raise ValueError("appended reserve needs explicit cooldown fallback")
    result = [*task_selection, *extension]
    if len({row["task_id"] for row in result}) != len(result):
        raise ValueError("duplicate task in appended reserve")
    return result


def retry_empty_rollout(result, reader):
    if result.failure_stage != "solver" or not result.failure_code.startswith(
            ("model-unknown:", "model-retryable:")):
        return False
    account = json.loads(reader.read(result.accounting))
    return not account["turns"]


def has_rollout(row, reader, *, token_statuses=None, requirements=None, verifier=None):
    if row["result"] is None:
        return False
    ref = BlobRef.from_dict(row["result"])
    if token_statuses is not None and ref.sha256 in token_statuses:
        return any(status == "eligible" for status, _ in token_statuses[ref.sha256].values())
    result = json.loads(reader.read(ref))
    account = json.loads(reader.read(BlobRef.from_dict(result["accounting"])))
    if requirements is None:
        return any(turn["exclusion"] is None for turn in account["turns"])
    # Cold replay has no live callback. Read eligibility once and retain the
    # same compact statuses for final export; do not audit token payloads.
    statuses = {}
    for turn in account["turns"]:
        data = (None if turn["exclusion"] is not None else
                json.loads(reader.read(BlobRef.from_dict(turn["response"]))).get("token_data"))
        statuses[turn["turn"]] = token_status(data, turn["exclusion"], requirements, verifier)
    if token_statuses is not None:
        token_statuses[ref.sha256] = statuses
    return any(status == "eligible" for status, _ in statuses.values())
