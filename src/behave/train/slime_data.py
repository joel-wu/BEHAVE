"""Raw next-token data hooks for Slime's existing native training loop.

No inference engine, model-generated response, reward or advantage is needed.
Optimization remains upstream SFT. Checkpoint ordering is shared with the Task
learner, while the source cursor is bound to the frozen external corpus.
"""

import ipaddress
import json
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from behave.agent.admission import AdmissionPolicy
from behave.agent.attempt import AttemptSpec
from behave.agent.journal import load_chain
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.providers.native_tokens import NativeChatTokens
from behave.agent.run_store import JournalDirectory
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, FileRef, read_verified
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json, version
from behave.core.storage import DirectoryStore, install_exclusive
from behave.data.published import load_published_task
from behave.data.published.files import task_files
from behave.isolation.service import run_service
from behave.task_execution.batch_plan import BatchPlan
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.simulator_judge import SimulatorJudge
from behave.task_execution.solver import SolverJob

from .checkpoint_storage import retention_policy
from .plan import TrainingManifest, TrainingPlan, resolve_training_seed
from .pool import (
    SamplingPolicy,
    TaskPoolPolicy,
    definition_refs,
    empty_state,
    observe_batch,
    read_pool,
    replenishment_limit,
    select_pool,
)
from .slime_launch import SlimeLayout, SlimeRecipe, import_parent, preset
from .slime_rollout import from_training_report, load_batch, read_binding, round_plan
from .trainer import NativeAnchor, NativeUpdates
from .training_data import RawSegment, TrainingRequirements, load_raw_corpus, native_train_data

SOURCE = "behave.train.slime_data.RawDataSource"
GENERATE = "behave.train.slime_data.generate"
CONVERT = "behave.train.slime_data.convert_samples"
LOG = "behave.train.slime_data.log_data"


def native_contract():
    return {"loss_type": "sft_loss", "calculate_per_token_loss": True,
        "compute_advantages_and_returns": False, "use_critic": False,
        "use_rollout_logprobs": False, "custom_reward_post_process_path": None,
        "custom_convert_samples_to_train_data_path": CONVERT,
        "custom_rollout_log_function_path": LOG, "data_source_path": SOURCE,
        "rollout_function_path": GENERATE, "debug_train_only": True,
        "n_samples_per_prompt": 1, "kl_coef": 0.0, "kl_loss_coef": 0.0,
        "use_kl_loss": False, "entropy_coef": 0.0, "use_opd": False,
        "use_rollout_routing_replay": False}


def arguments():
    return ("--behave-learning-mode", "raw", "--debug-train-only", "--loss-type", "sft_loss",
        "--calculate-per-token-loss", "--disable-compute-advantages-and-returns",
        "--data-source-path", SOURCE, "--rollout-function-path", GENERATE,
        "--custom-convert-samples-to-train-data-path", CONVERT,
        "--custom-rollout-log-function-path", LOG, "--n-samples-per-prompt", "1",
        "--kl-coef", "0", "--kl-loss-coef", "0", "--entropy-coef", "0")


def validate_args(args):
    for key, value in native_contract().items():
        if getattr(args, key, object()) != value:
            raise ValueError(f"Raw continued-learning contract differs: {key}")


def sampling(binding):
    row = fields(binding["sampling"], {"sequences_per_update", "order", "seed"})
    return SamplingPolicy(row["sequences_per_update"], row["order"], row["seed"])


def read_raw_binding(path: Path):
    payload = read_regular_file(path)
    value = parse_json(payload)
    keys = {"schema", "objects", "corpus", "run_directory", "sampling"}
    row = fields(value, keys | ({"parent_checkpoint", "checkpoint_retention"} & value.keys()))
    version(row["schema"], "behave.slime-raw-binding/1")
    for key in ("objects", "run_directory"):
        if type(row[key]) is not str or not Path(row[key]).is_absolute():
            raise ValueError("Raw binding requires explicit absolute storage paths")
    BlobRef.from_dict(row["corpus"])
    if "parent_checkpoint" in row:
        BlobRef.from_dict(row["parent_checkpoint"])
    if "checkpoint_retention" in row:
        from .checkpoint_storage import retention_policy

        retention_policy(row["checkpoint_retention"])
    sampling(row)
    if canonical_json(row) != payload:
        raise RecordError("record-not-canonical", "Raw binding must be canonical")
    return row


@dataclass(frozen=True, slots=True)
class RawBatch:
    corpus: BlobRef
    round: int
    indices: tuple[int, ...]
    segments: tuple[RawSegment, ...]

    def make_samples(self):
        from slime.utils.types import Sample

        first = self.round * len(self.segments)
        return [Sample(index=first + index, rollout_id=first + index,
            tokens=list(segment.tokens), response_length=segment.targets,
            loss_mask=[1] * segment.targets, status=Sample.Status.COMPLETED,
            metadata={"behave_raw": {"document": segment.document.as_dict(), "offset": segment.offset,
                "tokens": BlobRef.of(canonical_json(list(segment.tokens))).as_dict(),
                "corpus": self.corpus.as_dict(), "segment_index": original}})
            for index, (original, segment) in enumerate(zip(self.indices, self.segments, strict=True))]

    def output(self):
        from slime.rollout.base_types import RolloutFnTrainOutput

        return RolloutFnTrainOutput(samples=self.make_samples(), metrics={
            "raw/sequences": len(self.segments), "raw/target_tokens": sum(s.targets for s in self.segments)})


class RawDataSource:
    def __init__(self, args):
        validate_args(args)
        self.args = args
        self.binding = read_raw_binding(Path(args.behave_rollout_binding))
        self.identity = BlobRef.of(canonical_json(self.binding))
        if self.identity.sha256 != args.behave_rollout_binding_sha256:
            raise ValueError("Raw input binding changed after approval")
        reader = DirectoryStore(self.binding["objects"])
        self.corpus_ref = BlobRef.from_dict(self.binding["corpus"])
        self.corpus = load_raw_corpus(reader, self.corpus_ref)
        self.sampling = sampling(self.binding)
        self.sequence_ids = tuple(str(index) for index in range(len(self.corpus.segments)))
        self.sampling.select(self.sequence_ids, 0)  # Empty rounds need an explicit no-update disposition.
        if args.rollout_batch_size != self.sampling.tasks_per_update:
            raise ValueError("native batch size differs from the frozen Raw sampling policy")
        self.run = JournalDirectory(Path(self.binding["run_directory"]) / "native")
        self.journal = self.run.journal("rounds")
        parent = self.binding.get("parent_checkpoint")
        anchor = None if parent is None else NativeAnchor.load(reader, BlobRef.from_dict(parent))
        self.native = NativeUpdates(args=args, run=self.run, identity=self.identity,
            ready_kind="raw_batch_ready", evidence_key="batch", anchor=anchor,
            retention=self.binding.get("checkpoint_retention"))
        self._last_ready = None

    def __len__(self):
        # Native stopping/cadence use explicit rounds, not an inferred epoch.
        return self.sampling.tasks_per_update

    def get_samples(self, count):
        raise ValueError("Raw batches are selected by their frozen cursor, not prompt resampling")

    def add_samples(self, samples):
        raise ValueError("Raw source segments cannot be replaced through a rollout buffer")

    def load(self, rollout_id=None):
        self.native.load(rollout_id)

    def save(self, rollout_id):
        self.native.save(rollout_id)

    def batch(self, step):
        with self.run.owner():
            events = self.journal.load()
            self.native.require_next(events, step, self._last_ready)
            indices = tuple(int(index) for index in self.sampling.select(self.sequence_ids, step))
            batch = RawBatch(self.corpus_ref, step, indices, tuple(self.corpus.segments[i] for i in indices))
            receipt = self.run.objects.put(canonical_json({"schema": "behave.raw-learning-batch/1",
                "binding": self.identity.as_dict(), "corpus": self.corpus_ref.as_dict(), "round": step,
                "segment_indices": indices, "target_tokens": sum(s.targets for s in batch.segments)}))
            event = self.journal.append(events, "raw_batch_ready", {"round": step,
                "binding": self.identity.as_dict(), "receipt": receipt.as_dict()}, (receipt,))
            self._last_ready = BlobRef.of(event.to_bytes())
            return batch


def generate(args, rollout_id, data_source: RawDataSource, *, evaluation=False):
    if evaluation:
        raise ValueError("Raw material hooks do not evaluate tasks or generate model answers")
    return data_source.batch(rollout_id).output()


def convert_samples(args, samples):
    segments, indices = [], []
    for sample in samples:
        row = fields(sample.metadata["behave_raw"], {"document", "offset", "tokens", "corpus", "segment_index"})
        segment = RawSegment(FileRef.from_dict(row["document"]), row["offset"], tuple(sample.tokens))
        if (row["tokens"] != BlobRef.of(canonical_json(sample.tokens)).as_dict() or
                sample.response_length != segment.targets or sample.loss_mask != [1] * segment.targets or
                sample.reward is not None or sample.rollout_log_probs is not None):
            raise RecordError("raw-native-input-mismatch", "native Raw inputs changed their tokens or learning semantics")
        if type(sample.index) is not int or sample.index < 0 or sample.rollout_id != sample.index:
            raise RecordError("raw-native-input-mismatch", "native Raw samples need distinct sequence identities")
        segments.append(segment)
        indices.append(sample.index)
    if not indices or indices != list(range(indices[0], indices[0] + len(indices))):
        raise RecordError("raw-native-input-mismatch", "native Raw sample order differs from its prepared batch")
    return native_train_data(tuple(segments), first_index=indices[0])


def log_data(rollout_id, args, samples, metrics, elapsed):
    """Use native logging but do not report generation speed or reward for Raw."""
    from slime.observability import logging_utils

    logging_utils.log(args, {"raw/step": rollout_id, "raw/data_seconds": elapsed,
        "raw/sequences": len(samples), "raw/target_tokens": sum(s.response_length for s in samples)},
        step_key="raw/step")
    return True


def _install(output, name, value):
    payload = canonical_json(value)
    if not install_exclusive(output, name, payload) and (output / name).read_bytes() != payload:
        raise RecordError("preparation-input-mismatch", "prepared learning inputs changed; use a new plan directory")


def _begin(output, request):
    # Preparation has no model/optimizer side effects. Immutable files can be
    # filled in after interruption, but an existing unrelated directory is not ours.
    marker = output / "preparation_request.json"
    if output.exists() and not marker.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    _install(output, marker.name, request)


def _write_plan(output, configuration, binding, selection):
    for name, value in (("binding", binding), ("configuration", configuration), ("training_selection", selection)):
        _install(output, f"{name}.json", value)
    return binding


def prepare_task_plan(output: Path, configuration: dict, reader, manifest: TrainingManifest, *,
                      parent_reader=None, parent_checkpoint: BlobRef | None = None,
                      solver_experience: bytes | None = None) -> dict:
    """Prepare exactly the approved training pool, preserving its corpus guard."""
    import behave

    configuration = resolve_training_seed(configuration)
    task_ids = tuple(task_id for task_id, _ in manifest.tasks)
    sampler = None if configuration.get("sampler") is None else SamplingPolicy.from_dict(configuration["sampler"])
    if sampler is not None:
        sampler.select(task_ids, 0)
    curriculum = None
    if configuration.get("curriculum") is not None:
        curriculum = TaskPoolPolicy.from_dict(configuration["curriculum"])
        if sampler is None:
            raise ValueError("task curriculum requires an explicit sampler")
    launch_count = len(task_ids) if sampler is None else sampler.tasks_per_update
    if curriculum is not None and getattr(curriculum, "incomplete_priority_slots", 0) > launch_count:
        raise ValueError("incomplete priority slots must fit the initial task window")
    update_count = configuration.get("update_groups", launch_count)
    if type(update_count) is not int or not 1 <= update_count <= launch_count:
        raise ValueError("update_groups must be between one and the launched task count")
    replenishment = configuration.get("group_replenishment")
    if replenishment is not None:
        from .pool import replenishment_limit

        if sampler is None or "update_groups" not in configuration:
            raise ValueError("group replenishment requires sampler and update_groups")
        reserve_count = replenishment_limit(replenishment, target=update_count,
                                            window=launch_count, pool_size=len(task_ids))
    samples = configuration["samples_per_task"]
    if type(samples) is not int or samples < 1:
        raise ValueError("samples_per_task must be a positive integer")
    model = Path(configuration["model_path"])
    layout = SlimeLayout(**configuration["layout"])
    layout.validate_model(json.loads((model / "config.json").read_text()))
    recipe = preset(configuration["algorithm"])
    published = tuple(load_published_task(reader, manifest.release, task_id) for task_id in task_ids)
    if tuple((task.public.task_id, task.public.revision) for task in published) != manifest.tasks:
        raise RecordError("training-task-forbidden", "approved task revisions differ from their release")
    membership = ()
    if manifest.corpus is not None or "corpus" in published[0].release.metadata:
        from .plan import validate_membership

        membership = validate_membership(reader, manifest, ())

    # A saved release pins the evaluator as well as its tasks. Check the
    # installed scoring runtime before starting inference or allocating an
    # optimizer; never rewrite an approved policy to make it pass this check.
    from behave.sim import api

    for task in published:
        policy = JudgePolicy.from_bytes(task.judge.judge_policy)
        expected = (policy.command_version, policy.authority_version)
        installed = (api.COMMAND_VERSION, api.RESULT_VERSION)
        if expected != installed:
            raise RecordError("training-judge-version-mismatch",
                f"{task.public.task_id}: published judge {expected} differs from installed Sim {installed}")

    output = output.resolve()
    _begin(output, {"route": "task", "configuration": configuration,
        "manifest": BlobRef.of(manifest.to_bytes()).as_dict(),
        "parent": None if parent_checkpoint is None else parent_checkpoint.as_dict(),
        **({"solver_experience": BlobRef.of(solver_experience).as_dict()} if solver_experience is not None else {})})
    objects = DirectoryStore(output / "inputs")
    if solver_experience is not None:
        objects.put(solver_experience)
    objects.publish(reader, (manifest.release, *membership))
    judge_policies = {}
    for task in published:
        record = task.manifest
        files = task_files(record)
        objects.publish(reader, (task.public.revision, task.release.policy, record.run_policy, record.judge_policy,
                                 *(file.content for file in files)))
        judge_policies[record.task_id] = record.judge_policy.as_dict()
    approved = objects.put(manifest.to_bytes())
    initial = objects.put(canonical_json({"model_path": str(model), "revision": configuration["model_revision"]}))
    tokenizer = objects.put((model / "tokenizer.json").read_bytes())
    template = objects.put((model / "tokenizer_config.json").read_bytes())
    continuation = None
    if configuration.get("native_token_continuation", False):
        from behave.agent.providers.native_history import model_continuation

        continuation = model_continuation(json.loads((model / "tokenizer_config.json").read_text()))
    endpoint = LocalChatEndpoint(configuration["endpoint_url"],
        configuration["served_model"], configuration["runtime_revision"], parameters=configuration["sampling"],
        tokens=NativeChatTokens("sglang-slime", initial, tokenizer, template, continuation), routing="attempt")
    jobs = tuple(SolverJob(task.public,
        AttemptSpec(f"{output.name}:{task.public.task_id}:{index}", "solver", endpoint.model_policy(),
                    **configuration["runtime"]), endpoint.command(), setting=configuration["setting"],
                    solver_experience=solver_experience,
                    output_preview_bytes=configuration.get("output_preview_bytes"))
        for task in published for index in range(samples))
    execution = BatchPlan(output.name, jobs, SimulatorJudge.identity, configuration["workers"],
        AdmissionPolicy("training-judge", configuration["judge_workers"]),
        model_admission=AdmissionPolicy("training-model", configuration["workers"]), score_on_submit=True)
    plan = TrainingPlan(execution, approved,
        TrainingRequirements(endpoint.tokens.adapter, initial, tokenizer, template, required_signals=("old_log_probs",)),
        tuple(task_id for task_id in task_ids for _ in range(samples)))
    plan.preflight(objects, approved_manifest=approved)
    binding = {"schema": "behave.slime-rollout-binding/1", "control_python": sys.executable,
        "control_package": str(Path(behave.__file__).resolve().parents[1]), "objects": str(objects.directory),
        "plan": objects.put(plan.to_bytes()).as_dict(), "manifest": approved.as_dict(),
        "endpoint": endpoint.as_dict(), "recipe": recipe.as_dict(), "run_directory": str(output / "rounds")}
    if layout.rollout_excluded_gpus:
        binding["rollout_engine_count"] = layout.rollout_engine_count
    binding.update({name: configuration.get(name) for name in
                    ("round_seconds", "cleanup_seconds", "control_timeout_seconds", "round_log_bytes")})
    binding.update(preparation_workers=configuration.get("preparation_workers", defaults.TRAIN_PREPARATION_WORKERS),
        preparation_start_method=configuration.get("preparation_start_method", defaults.TRAIN_PREPARATION_START_METHOD),
        missing_reward_policy=configuration.get("missing_reward_policy", defaults.TRAIN_MISSING_REWARD_POLICY))
    if sampler is not None:
        binding.update(schema="behave.slime-rollout-binding/2", sampler=sampler.as_dict())
    if curriculum is not None:
        binding["curriculum"] = curriculum.as_dict()
    if "update_groups" in configuration:
        binding["update_groups"] = update_count
    if replenishment is not None:
        binding["group_replenishment"] = replenishment
    if "empty_rollout_retries" in configuration:
        binding["empty_rollout_retries"] = configuration["empty_rollout_retries"]
    if configuration.get("checkpoint_retention") is not None:
        binding["checkpoint_retention"] = retention_policy(configuration["checkpoint_retention"])
    if configuration.get("self_improvement") is not None:
        binding["self_improvement"] = configuration["self_improvement"]
    if parent_checkpoint is not None:
        import_parent(parent_reader, parent_checkpoint, objects, route="task")
        binding.update(schema="behave.slime-rollout-binding/3", parent_checkpoint=parent_checkpoint.as_dict())
        if curriculum is not None:
            from behave.agent.journal import load_chain

            from .pool import definition_refs, read_pool, resume_pool

            before = read_pool(parent_reader, load_chain(parent_reader, parent_checkpoint))
            if before is not None:
                after, changes = resume_pool(before, manifest.tasks, parent_reader, objects)
                # Legacy runs stored definitions in plan/inputs rather than the
                # checkpoint closure. The job handoff supplies them as well.
                refs = definition_refs(parent_reader, (
                    BlobRef.from_dict(row["revision"]) for row in before["tasks"].values()))
                objects.publish(parent_reader, refs)
                binding["parent_task_pool"] = objects.put(canonical_json(after)).as_dict()
                _install(output, "task_pool_resume.json", {
                    "parent_checkpoint": parent_checkpoint.as_dict(),
                    "before": before, "after": after, **changes})
    if configuration.get("rollout_recovery") is not None:
        from .slime_rollout import attach_handoff

        if "self_improvement" in binding:
            from .slime_acquisition import prepare_handoff

            prepare_handoff(binding, configuration["rollout_recovery"], objects)
        attach_handoff(binding, configuration["rollout_recovery"], objects)
    return _write_plan(output, configuration, binding, {"tasks": task_ids,
        "training_manifest": approved.as_dict(), "release": manifest.release.as_dict(),
        "samples_per_task": samples, "tasks_per_update": update_count, "candidates_per_update": samples * update_count,
        **({} if curriculum is None else {"curriculum": curriculum.as_dict()}),
        **({"group_replenishment": replenishment, "active_task_groups": launch_count,
            "reserve_tasks": reserve_count, "reserve_candidates": samples * reserve_count}
           if replenishment is not None else {} if "update_groups" not in configuration else {
            "launched_tasks_per_update": launch_count, "launched_candidates_per_update": samples * launch_count}),
        "testbench_scoring": False, "scoring": "published-task-policy", "judge_policies": judge_policies,
        "native_training_started": False})


def prepare_raw_plan(output: Path, configuration: dict, objects: Path, corpus: BlobRef, *,
                     parent_reader=None, parent_checkpoint: BlobRef | None = None) -> dict:
    configuration = resolve_training_seed(configuration)
    reader = DirectoryStore(objects.resolve())
    data = load_raw_corpus(reader, corpus)
    model = Path(configuration["model_path"])
    if data.encoding.tokenizer != BlobRef.of((model / "tokenizer.json").read_bytes()):
        raise ValueError("Raw corpus tokenizer differs from the native model tokenizer")
    layout = SlimeLayout(**configuration["layout"])
    if layout.rollout_gpus:
        raise ValueError("Raw learning has no inference GPU allocation")
    layout.validate_model(json.loads((model / "config.json").read_text()))
    binding = {"schema": "behave.slime-raw-binding/1", "objects": str(reader.directory),
        "corpus": corpus.as_dict(), "run_directory": str(output.resolve() / "rounds"),
        "sampling": configuration["sampler"]}
    if configuration.get("checkpoint_retention") is not None:
        binding["checkpoint_retention"] = retention_policy(configuration["checkpoint_retention"])
    policy = sampling(binding)
    policy.select(tuple(str(i) for i in range(len(data.segments))), 0)
    if parent_checkpoint is not None:
        import_parent(parent_reader, parent_checkpoint, reader, route="raw")
        binding["parent_checkpoint"] = parent_checkpoint.as_dict()
    output = output.resolve()
    _begin(output, {"route": "raw", "configuration": configuration,
        "objects": str(reader.directory), "corpus": corpus.as_dict(),
        "parent": None if parent_checkpoint is None else parent_checkpoint.as_dict()})
    return _write_plan(output, configuration, binding, {"materials": data.materials.as_dict(), "corpus": corpus.as_dict(),
        "encoding": data.encoding.as_dict(), "segments": len(data.segments),
        "corpus_target_tokens": sum(segment.targets for segment in data.segments),
        "sequences_per_update": policy.tasks_per_update, "native_training_started": False})


def local_request(base: str, route: str, *, post: bool = False, json_response: bool = True, timeout=None):
    parts = urlsplit(base)
    if (parts.scheme != "http" or not parts.hostname or not ipaddress.ip_address(parts.hostname).is_loopback
            or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/")):
        raise ValueError("Slime management endpoints must be private loopback addresses")
    with urlopen(Request(base.rstrip("/") + route, method="POST" if post else "GET"),
                 timeout=timeout) as response:
        data = response.read()
    return parse_json(data) if json_response else None


def engine_versions(base: str, count: int, *, flush: bool = False, timeout=None) -> tuple:
    workers = local_request(base, "/workers", timeout=timeout)["workers"]
    urls = sorted(worker["url"] for worker in workers)
    if len(urls) != count or len(set(urls)) != count:
        raise ValueError("Slime router worker count differs from the allocated layout")
    result = []
    for url in urls:
        if flush:
            local_request(url, "/flush_cache", post=True, json_response=False, timeout=timeout)
        value = local_request(url, "/get_weight_version", timeout=timeout)["weight_version"]
        if type(value) not in (str, int) or not str(value).isdigit() or int(value) < 1:
            raise ValueError("rollout engine has no actor-published weight version")
        result.append((url, str(value)))
    if len({v for _, v in result}) != 1:
        raise ValueError("rollout engines carry different weight versions")
    return tuple(result)


def require_weight_advance(previous: tuple | None, current: tuple) -> None:
    if previous is None:
        return  # Cold startup authenticates the native checkpoint in load().
    old, new = dict(previous), dict(current)
    if old.keys() != new.keys() or any(int(new[url]) <= int(value) for url, value in old.items()):
        raise ValueError("all owned rollout engines must advance after the preceding update")


def select_frozen_pool(tasks, state, sampler, update, *, include_reserve, policy):
    """Recover old runs without upgrading their frozen sampler implementation."""
    options = {"include_reserve": include_reserve}
    if hasattr(policy, "incomplete_priority_slots"):
        options["policy"] = policy
    return select_pool(tasks, state, sampler, update, **options)


class ScoredDataSource:
    """One immutable binding, one durable round history, no prompt resampling."""

    def __init__(self, args):
        self.args = args
        self.path = Path(args.behave_rollout_binding)
        self.binding = read_binding(self.path)
        self.identity = BlobRef.of(canonical_json(self.binding))
        if self.identity.sha256 != args.behave_rollout_binding_sha256:
            raise ValueError("Slime rollout binding changed after approval")
        self.recipe = SlimeRecipe.from_dict(self.binding["recipe"])
        self.recipe.validate_args(args)
        self.run = JournalDirectory(Path(self.binding["run_directory"]) / "native")
        self.journal = self.run.journal("rounds")
        parent = self.binding.get("parent_checkpoint")
        anchor = None if parent is None else NativeAnchor.load(DirectoryStore(self.binding["objects"]), BlobRef.from_dict(parent))
        self.native = NativeUpdates(args=args, run=self.run, identity=self.identity,
            ready_kind="round_scored", evidence_key="score", critic=self.recipe.algorithm == "ppo", anchor=anchor,
            retention=self.binding.get("checkpoint_retention"))
        self._previous_versions = None
        self._last_scored = None
        self._acquisition_state = None
        self.curriculum = None if "curriculum" not in self.binding else TaskPoolPolicy.from_dict(self.binding["curriculum"])
        self._parent_pool = empty_state()
        if self.curriculum is not None:
            inputs = DirectoryStore(self.binding["objects"])
            self._pool_tasks = TrainingManifest.from_bytes(
                read_verified(inputs, BlobRef.from_dict(self.binding["manifest"]))).tasks
            if parent is not None:
                self._parent_pool = self._read_pool(inputs, load_chain(inputs, BlobRef.from_dict(parent))) or empty_state()
            if "parent_task_pool" in self.binding:
                self._parent_pool = parse_json(read_verified(inputs, BlobRef.from_dict(self.binding["parent_task_pool"])))
            revisions = tuple(ref for _, ref in self._pool_tasks) + tuple(
                BlobRef.from_dict(row["revision"]) for row in self._parent_pool["tasks"].values())
            self._pool_definitions = definition_refs(inputs, revisions)
            self._pool_definitions_published = False

    def __len__(self):
        # Slime receives an explicit num_rollout; this is not a shuffled dataset.
        return 1

    def get_samples(self, num_samples):
        raise ValueError("BEHAVE generates complete approved candidate plans, not sampled prompt rows")

    def add_samples(self, samples):
        raise ValueError("BEHAVE candidate groups cannot be refilled or silently resampled")

    def load(self, rollout_id=None):
        self.native.load(rollout_id)
        if "self_improvement" in self.binding:
            from .slime_acquisition import restore

            restore(self, self.journal.load())

    def save(self, rollout_id):
        self.native.save(rollout_id)

    def _require_next_round(self, events, rollout_id):
        self.native.require_next(events, rollout_id, self._last_scored)

    @staticmethod
    def _read_pool(reader, events):
        return read_pool(reader, events)

    def _pool_state(self, events):
        return self._read_pool(self.run.objects, events) or self._parent_pool

    def _pool_commit(self, events, rollout_id, plan, batch, *, fresh=True):
        acquisition = getattr(self, "_acquisition_state", None)
        extra = {} if acquisition is None else {"acquisition": acquisition.as_dict()}
        extra_refs = () if acquisition is None else (acquisition,)
        if "self_improvement" in self.binding:
            from .slime_acquisition import retain_feedback_parent

            inherited, references = retain_feedback_parent(self, events)
            extra.update(inherited)
            extra_refs += references
        if self.curriculum is None:
            return extra, extra_refs
        state = self._pool_state(events)
        if acquisition is not None:
            from .pool import sync_state

            state = sync_state(state, self._pool_tasks)
        if fresh:
            selection = batch.group_selection or {}
            if selection.get("strategy") == "first-reward-varying-groups":
                observations = tuple({"group_id": group["group_id"], "reward": reward}
                    for group in selection["observations"] if group["status"] in {"constant", "selected"}
                    for reward in group["rewards"])
                state = observe_batch(state, plan, observations, self.curriculum, rollout_id,
                                      launched_indices=set(selection["launched_indices"]))
            else:
                state = observe_batch(state, plan, batch.candidates, self.curriculum, rollout_id)
        reference = self.run.objects.put(canonical_json(state))
        if not self._pool_definitions_published:
            self.run.objects.publish(DirectoryStore(self.binding["objects"]), self._pool_definitions)
            self._pool_definitions_published = True
        # Native save/parent export already carry ready-event artifacts. No
        # second checkpoint format or self-improvement sampler is needed.
        return {"task_pool": reference.as_dict(), **extra}, (reference, *self._pool_definitions, *extra_refs)

    def generate(self, rollout_id):
        if "recorded_rounds" in self.binding:
            return self._generate_recorded(rollout_id)
        events = self.journal.load()
        if (events and events[-1].kind == "round_abandoned"
                and events[-1].payload.get("reuse_receipt") is not None):
            return self._generate_recorded(rollout_id, recovered=events[-1].payload["reuse_receipt"])
        handoff = self.binding.get("rollout_recovery")
        if handoff is not None and handoff["round"] == rollout_id and handoff.get("scored_receipt") is not None:
            # OOM after scoring must not redo the first-complete-group race or
            # alter pool observations. Reuse the existing whole-batch path.
            receipt = BlobRef.from_dict(handoff["scored_receipt"])
            self.run.objects.publish_owned(DirectoryStore(self.binding["objects"]),
                (receipt, BlobRef.from_dict(handoff["policy"])))
            row = parse_json(read_verified(self.run.objects, receipt))
            if row.get("acquisition") is not None:
                self.run.objects.publish_owned(DirectoryStore(self.binding["objects"]),
                    (BlobRef.from_dict(row["acquisition"]),))
            return self._generate_recorded(rollout_id, recovered=receipt.as_dict())
        base = f"http://{self.args.sglang_router_ip}:{self.args.sglang_router_port}"
        count = self.binding.get("rollout_engine_count",
            self.args.rollout_num_gpus // self.args.rollout_num_gpus_per_engine)
        with self.run.owner():
            events = self.journal.load()
            self._require_next_round(events, rollout_id)
            versions = engine_versions(base, count, flush=True, timeout=self.binding.get("control_timeout_seconds"))
            require_weight_advance(self._previous_versions, versions)
            endpoint = replace(LocalChatEndpoint.from_dict(self.binding["endpoint"]),
                               url=base + "/v1/chat/completions", routing="attempt",
                               worker_urls=tuple(url + "/v1/chat/completions" for url, _ in versions))
            if "self_improvement" in self.binding:
                from .slime_acquisition import before_rollout

                before_rollout(self, rollout_id, events, endpoint)
            generation = sum(event.kind == "round_abandoned" for event in events)
            policy = {"schema": "behave.slime-policy/1", "binding": self.identity.as_dict(),
                      "round": rollout_id, "engine_versions": versions,
                      "previous": None if not events else BlobRef.of(events[-1].to_bytes()).as_dict(),
                      **({"recovery": generation} if generation else {})}
            handoff = self.binding.get("rollout_recovery")
            handoff = handoff if handoff is not None and handoff["round"] == rollout_id else None
            if handoff is not None:
                # Restored actor weights were checked in load(). A new engine
                # address/version counter is not a new generating policy.
                policy = parse_json(read_verified(DirectoryStore(self.binding["objects"]),
                    BlobRef.from_dict(handoff["policy"])))
            elif self.curriculum is not None:
                replenishing = "group_replenishment" in self.binding
                policy["task_selection"] = select_frozen_pool(self._pool_tasks, self._pool_state(events),
                    SamplingPolicy.from_dict(self.binding["sampler"]), rollout_id,
                    include_reserve=replenishing, policy=self.curriculum)
                if replenishing:
                    limit = replenishment_limit(self.binding["group_replenishment"],
                        target=self.binding["update_groups"], window=self.binding["sampler"]["tasks_per_update"],
                        pool_size=len(self._pool_tasks))
                    policy["task_selection"] = policy["task_selection"][:limit]
                required = (self.binding["update_groups"] if replenishing
                            else self.binding["sampler"]["tasks_per_update"])
                available = len(policy["task_selection"])
                if available < required:
                    self.journal.append(events, "task_pool_insufficient", {
                        "round": rollout_id, "binding": self.identity.as_dict(), "available_groups": available,
                        "required_groups": required, "approved_groups": len(self._pool_tasks),
                        "task_selection": policy["task_selection"],
                    })
                    raise RecordError("training-task-pool-insufficient",
                        f"{available} eligible groups, {required} required; no rollout or update; cooldowns unchanged")
            policy_ref = self.run.objects.put(canonical_json(policy))
            event = self.journal.append(events, "round_started", {"round": rollout_id,
                "binding": self.identity.as_dict(), "policy": policy_ref.as_dict()}, (policy_ref,))
            events += (event,)
            round_dir = self.run.directory / (f"request_{rollout_id:08d}" +
                                              (f"_recovery_{generation}" if generation else ""))
            round_dir.mkdir()
            request, response = round_dir / "request.json", round_dir / "response.json"
            active_binding_path = self.path
            if "self_improvement" in self.binding:
                from .slime_acquisition import rollout_binding

                active_binding_path = rollout_binding(self, round_dir)
            with request.open("xb") as stream:
                stream.write(canonical_json({"round": rollout_id, "endpoint": endpoint.as_dict(), "policy": policy}))
            bootstrap = "import sys; sys.path.insert(0,sys.argv.pop(1)); from behave.train.slime_rollout import main; raise SystemExit(main())"
            # The process has the control ABI's dependencies, never the native
            # Torch ABI's site-packages. It shares only this private loopback.
            environment = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
            import os

            for name in ("BEHAVE_EDA_ROOT", "BEHAVE_WORK_DIR", "BEHAVE_SCRATCH_DIR", "TMPDIR",
                         "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON"):
                if name in os.environ:
                    environment[name] = os.environ[name]
            # Turns bound each agent attempt. An entire scored round has no
            # additional time budget unless the operator explicitly supplies one.
            seconds = self.binding.get("round_seconds")
            started_at = datetime.now(UTC).isoformat()
            result = run_service((self.binding["control_python"], "-I", "-B", "-c", bootstrap,
                self.binding["control_package"], str(active_binding_path), str(request), str(response)),
                cwd=round_dir, environment=environment, network="host",
                output_bytes=self.binding.get("round_log_bytes"),
                deadline=None if seconds is None else time.monotonic() + seconds,
                cleanup_seconds=self.binding.get("cleanup_seconds"))
            (round_dir / "stdout.log").write_bytes(result.stdout)
            (round_dir / "stderr.log").write_bytes(result.stderr)
            (round_dir / "timing.json").write_bytes(canonical_json({
                "round": rollout_id, "started_at": started_at,
                "finished_at": datetime.now(UTC).isoformat(),
                "rollout_and_scoring_seconds": result.elapsed_seconds,
                "status": result.status, "returncode": result.returncode}))
            if result.status != "exited" or result.returncode != 0 or not result.cleanup_state.quiescent:
                raise RecordError("slime-rollout-unavailable", "scored rollout failed; no update is authorized")
            value = parse_json(response.read_bytes())
            row = fields(value, {"schema", "round", "objects", "head", "manifest", "endpoint", "policy"}
                         | ({"prepared_batch", "task_selection"} & value.keys()))
            version(row["schema"], "behave.slime-scored-round/1")
            if row["manifest"] != self.binding["manifest"]:
                raise ValueError("scored response changed the approved training manifest")
            if row["round"] != rollout_id or row["policy"] != policy_ref.as_dict():
                raise ValueError("scored response differs from the requested policy/round")
            if canonical_json(row.get("task_selection")) != canonical_json(policy.get("task_selection")):
                raise ValueError("scored response changed the selected task groups")
            if engine_versions(base, count, timeout=self.binding.get("control_timeout_seconds")) != versions:
                raise ValueError("rollout engines changed weights during candidate generation")
            verified_endpoint = LocalChatEndpoint.from_dict(row["endpoint"])
            if verified_endpoint != replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy_ref)):
                raise ValueError("control application changed the generating endpoint")
            reader = DirectoryStore(row["objects"])
            expected = round_plan(self.binding, DirectoryStore(self.binding["objects"]),
                round_id=rollout_id, endpoint=verified_endpoint, policy=policy_ref,
                task_selection=policy.get("task_selection"))
            if "prepared_batch" in row:
                batch = load_batch(row["prepared_batch"], head=BlobRef.from_dict(row["head"]),
                    manifest=BlobRef.from_dict(self.binding["manifest"]), plan=BlobRef.of(expected.to_bytes()),
                    recipe=self.recipe)
            else:
                from .slime_rollout import handoff_verifier

                batch = from_training_report(reader, BlobRef.from_dict(row["head"]),
                    approved_manifest=BlobRef.from_dict(self.binding["manifest"]),
                    verifier=handoff_verifier(self.binding, rollout_id, verified_endpoint), recipe=self.recipe,
                    missing_reward_policy=self.binding.get("missing_reward_policy", defaults.TRAIN_MISSING_REWARD_POLICY),
                    expected_plan=BlobRef.of(expected.to_bytes()),
                    preparation_workers=self.binding.get("preparation_workers", defaults.TRAIN_PREPARATION_WORKERS),
                    preparation_start_method=self.binding.get("preparation_start_method", defaults.TRAIN_PREPARATION_START_METHOD),
                    preparation_directory=self.run.directory / "preparation")
            if self._acquisition_state is not None:
                row["acquisition"] = self._acquisition_state.as_dict()
            receipt = self.run.objects.put(canonical_json(row))
            pool_fields, pool_refs = self._pool_commit(events, rollout_id, expected, batch)
            scored = self.journal.append(events, "round_scored", {"round": rollout_id, "binding": self.identity.as_dict(),
                "receipt": receipt.as_dict(),
                "retained_turns": sum(row["retained_turns"] for row in batch.candidates),
                "training_segments": len(batch.payloads), **pool_fields}, (receipt, *pool_refs))
            self._last_scored = BlobRef.of(scored.to_bytes())
            self._previous_versions = versions
            return batch.rollout_output()

    def _generate_recorded(self, rollout_id, *, recovered=None):
        """Replay explicitly selected scored evidence, without generating or rescoring.

        Native optimization, group credit, packing and checkpoint commits are
        unchanged. Repeated receipts are explicit offline reuse, never fresh
        on-policy rollouts. Preserve their original policy/log-prob identities.
        """
        with self.run.owner():
            events = self.journal.load()
            self._require_next_round(events, rollout_id)
            receipts = self.binding.get("recorded_rounds", ())
            reference = BlobRef.from_dict(receipts[rollout_id - self.native.first_step]
                                         if recovered is None else thaw_json(recovered))
            inputs = DirectoryStore(self.binding["objects"])
            row = parse_json(read_verified(inputs if recovered is None else self.run.objects, reference))
            if row.get("acquisition") is not None:
                from .slime_acquisition import activate

                activate(self, BlobRef.from_dict(row["acquisition"]))
                inputs = DirectoryStore(self.binding["objects"])
            version(row["schema"], "behave.slime-scored-round/1")
            if row["manifest"] != self.binding["manifest"]:
                raise ValueError("recorded round differs from the approved task manifest")
            receipt = self.run.objects.put(canonical_json(row))
            started = self.journal.append(events, "round_started", {
                "round": rollout_id, "binding": self.identity.as_dict(),
                "recorded_receipt": receipt.as_dict()}, (receipt,))
            events += (started,)
            endpoint = LocalChatEndpoint.from_dict(row["endpoint"])
            expected = round_plan(self.binding, inputs, round_id=row["round"],
                endpoint=endpoint, policy=BlobRef.from_dict(row["policy"]),
                task_selection=row.get("task_selection"))
            began = time.monotonic()
            prepared = (row.get("prepared_batch") if recovered is not None else
                        self.binding.get("prepared_batches", {}).get(reference.sha256))
            if prepared is not None:
                batch = load_batch(prepared, head=BlobRef.from_dict(row["head"]),
                    manifest=BlobRef.from_dict(self.binding["manifest"]), plan=BlobRef.of(expected.to_bytes()),
                    recipe=self.recipe)
            else:
                from .slime_rollout import handoff_verifier

                batch = from_training_report(DirectoryStore(row["objects"]), BlobRef.from_dict(row["head"]),
                    approved_manifest=BlobRef.from_dict(self.binding["manifest"]),
                    verifier=handoff_verifier(self.binding, row["round"], endpoint), recipe=self.recipe,
                    missing_reward_policy=self.binding.get("missing_reward_policy", defaults.TRAIN_MISSING_REWARD_POLICY),
                    expected_plan=BlobRef.of(expected.to_bytes()),
                    preparation_workers=self.binding.get("preparation_workers", defaults.TRAIN_PREPARATION_WORKERS),
                    preparation_start_method=self.binding.get("preparation_start_method", defaults.TRAIN_PREPARATION_START_METHOD),
                    preparation_directory=self.run.directory / "preparation")
            # A recovered uncommitted batch is observed once after rollback.
            # Repeated offline optimization is not fresh evidence of mastery.
            pool_fields, pool_refs = self._pool_commit(events, rollout_id, expected, batch, fresh=recovered is not None)
            scored = self.journal.append(events, "round_scored", {
                "round": rollout_id, "binding": self.identity.as_dict(), "receipt": receipt.as_dict(),
                "recorded_round": row["round"], "preparation_seconds": time.monotonic() - began,
                "retained_turns": sum(candidate["retained_turns"] for candidate in batch.candidates),
                "training_segments": len(batch.payloads), **pool_fields}, (receipt, *pool_refs))
            self._last_scored = BlobRef.of(scored.to_bytes())
            return batch.rollout_output()


def generate_rollout(args, rollout_id, data_source, *, evaluation=False):
    if evaluation or type(data_source) is not ScoredDataSource:
        raise ValueError("use the separate BEHAVE evaluation application for held-out evaluation")
    return data_source.generate(rollout_id)
