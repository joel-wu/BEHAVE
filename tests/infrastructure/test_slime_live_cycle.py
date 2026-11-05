"""Opt-in real 27B Slime cycle; no dependency absence is counted as acceptance."""

import importlib.util
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from support.slime_cycle_evidence import scored_rounds
from support.slime_cycle_resources import export_evidence, release_obsolete_checkpoint, temporary_checkpoints
from support.slime_training_evidence import training_steps
from test_native_torch import native_process

from behave.agent.admission import AdmissionPolicy
from behave.agent.attempt import AttemptSpec
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.providers.native_tokens import NativeChatTokens
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.isolation.service import run_service
from behave.task_execution.batch_plan import BatchPlan
from behave.task_execution.simulator_judge import SimulatorJudge
from behave.task_execution.solver import SolverJob
from behave.train.plan import TrainingManifest, TrainingPlan
from behave.train.slime_launch import SlimeLayout, environment, model_template, preset
from behave.train.training_data import TrainingRequirements

pytestmark = [pytest.mark.native_training, pytest.mark.gpu_training, pytest.mark.isolation]


@pytest.fixture
def short_native_cache():
    from behave.core.lifecycle import CleanupState
    from behave.isolation.scratch import ScratchDirectory

    state = [CleanupState.COMPLETE]
    with ScratchDirectory(prefix="bs-", cleanup=lambda: state[0]) as path:
        yield path, state


@pytest.fixture
def cycle_checkpoints(tmp_path, request, short_native_cache):
    # Checkpoints are an explicit storage boundary, separate from node-local
    # model execution. Installed jobs own a unique durable report directory.
    report = request.config.getoption("xmlpath")
    parent = Path(report).absolute().parent if report else tmp_path
    with temporary_checkpoints(parent, cleanup=lambda: short_native_cache[1][0]) as path:
        (tmp_path / "checkpoint_storage.json").write_bytes(canonical_json({
            "path": str(path), "temporary": True, "release_requires_native_cleanup": True}))
        yield path


@pytest.fixture
def durable_cycle_evidence(tmp_path, request, cycle_checkpoints):
    yield
    report = request.config.getoption("xmlpath")
    if report and (tmp_path / "binding.json").exists():
        name = "slime-live-cycle-" + request.node.callspec.params["algorithm"]
        if request.node.callspec.params["workload"] == "batch64":
            name += "-batch64"
        export_evidence(tmp_path, Path(report).parent / name)


@pytest.mark.parametrize("algorithm,workload", [("grpo", "pair"), ("ppo", "pair"), ("grpo", "batch64")],
                         ids=["grpo", "ppo", "grpo-batch64"])
def test_slime_live_scored_update_refresh_and_resume(algorithm, workload, tmp_path, record_testsuite_property, short_native_cache,
                                                    durable_cycle_evidence, cycle_checkpoints):
    if os.environ.get("BEHAVE_TEST_TRAINING_GPUS") != "8":
        pytest.skip("real Slime cycle requires eight explicitly allocated GPUs")
    root = Path(__file__).resolve().parents[2]
    profile = parse_json((root / "infra/model_runtimes/local-serving.json").read_bytes())["shared"]
    model = Path(profile["model_path"])
    upstream = model.parents[6] / "external/slime-upstream"
    assert upstream.is_dir() and model.is_dir()
    spec = native_process()
    prefix = Path(spec.argv[0]).resolve().parents[1]
    import behave

    objects = DirectoryStore(tmp_path / "inputs")
    fixture_spec = importlib.util.spec_from_file_location("slime_fixture", root / "tools/model_task_smoke.py")
    fixture_module = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture_module)
    large = workload == "batch64"
    tasks = (fixture_module.fixture_group(objects, masks=(17, 37, 53, 71, 101, 139, 193, 229))
             if large else (fixture_module.fixture(objects, mask=37),))
    per_task, concurrency = (8, 8) if large else (2, 2)
    batch_size = len(tasks) * per_task
    context_length = 131072 if large else 32768
    tokenizer = objects.put((model / "tokenizer.json").read_bytes())
    template = objects.put((model / "tokenizer_config.json").read_bytes())
    initial = objects.put(canonical_json({"model": profile["model_revision"], "fixture": True}))
    endpoint = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "behave-slime-cycle",
        "slime-a3f500977f33/sglang-0.5.15.post1/behave-qwen-tools-1", parameters={"max_tokens": 2048, "temperature": 1,
            "top_p": 1, "top_k": -1, **({} if large else {"seed": 17}),
            "chat_template_kwargs": {"enable_thinking": False}},
        tokens=NativeChatTokens("sglang-slime", initial, tokenizer, template), routing="attempt")
    # Explicit fixture budgets exercise an ordinary zero/no-submission candidate
    # alongside a multi-turn candidate. We never overwrite either actual score.
    # The 8x8 workload has equal budgets and stochastic independent draws, not
    # eight copies of one seeded completion or forced score differences.
    jobs = tuple(SolverJob(task, AttemptSpec(f"cycle-{group}-{i}" if large else f"cycle-{i}", "solver", endpoint.model_policy(),
        max_turns=16 if large or i else 1, max_retries=1,
        request_timeout_seconds=120), endpoint.command())
        for group, task in enumerate(tasks) for i in range(per_task))
    # Eight finishing agents can queue behind two scoring workers. Waiting is
    # not part of a candidate's score: budget the four waves of 240-second
    # component ceilings explicitly, instead of the generic 60-second default.
    execution = BatchPlan("slime-cycle", jobs, SimulatorJudge.identity, concurrency,
        AdmissionPolicy("cycle-judge", 2, wait_seconds=960),
        model_admission=AdmissionPolicy("cycle-model", concurrency))
    manifest = objects.put(TrainingManifest(tasks[0].release, tuple((t.task_id, t.revision) for t in tasks)).to_bytes())
    groups = tuple(t.task_id for t in tasks for _ in range(per_task)) if large else ("same-task", "same-task")
    plan = TrainingPlan(execution, manifest, TrainingRequirements(endpoint.tokens.adapter, initial, tokenizer,
        template, required_signals=("old_log_probs",)),
        groups)
    recipe = preset(algorithm)
    binding = {"schema": "behave.slime-rollout-binding/1", "control_python": sys.executable,
        "control_package": str(Path(behave.__file__).resolve().parents[1]), "objects": str(objects.directory),
        "plan": objects.put(plan.to_bytes()).as_dict(), "manifest": manifest.as_dict(),
        "endpoint": endpoint.as_dict(), "recipe": recipe.as_dict(),
        "run_directory": str(tmp_path / "rounds"), "round_seconds": 7200 if large else 900}
    binding_path = tmp_path / "binding.json"
    binding_path.write_bytes(canonical_json(binding))
    if large:
        layout = SlimeLayout(actor_gpus_per_node=8, context_parallel=2, rollout_gpus=8,
                             rollout_gpus_per_engine=4, gpus_per_node=8)
    elif algorithm == "ppo":
        # The pinned hybrid attention is replicated across TP, not sharded.
        # TP4 made each actor rank hold 10.9B parameters; with a separate critic
        # and CPU Adam, cold load hit the 1750-GiB host limit. PP4 distributes
        # whole layers instead, keeping four training + four serving GPUs and
        # the same full model, algorithm, optimizer/RNG state and task budgets.
        layout = SlimeLayout(tensor_parallel=1, pipeline_parallel=4,
                             colocate=False, gpus_per_node=8)
    else:
        layout = SlimeLayout(colocate=False, gpus_per_node=8)
    layout.validate_model(parse_json((model / "config.json").read_bytes()))
    argv = (*model_template(upstream / "scripts/models/qwen3.5-27B.sh",
                 spec=("behave.train.slime_qwen", "get_qwen3_5_spec")), *layout.arguments(), *recipe.arguments(),
        "--hf-checkpoint", str(model), "--save", str(cycle_checkpoints), "--save-interval", "1",
        "--save-hf", str(tmp_path / "hf_{rollout_id}"), "--num-rollout", "2",
        "--rollout-function-path", "behave.train.slime_data.generate_rollout",
        "--data-source-path", "behave.train.slime_data.ScoredDataSource",
        "--rollout-batch-size", str(len(tasks)), "--n-samples-per-prompt", str(per_task),
        "--global-batch-size", str(batch_size),
        "--micro-batch-size", "1", "--bf16", "--seq-length", str(context_length),
        "--max-position-embeddings", str(context_length),
        "--optimizer", "adam", "--lr", "1e-6", "--lr-decay-style", "constant", "--weight-decay", "0.1",
        "--use-checkpoint-opt-param-scheduler",
        "--adam-beta1", "0.9", "--adam-beta2", "0.98", "--optimizer-cpu-offload",
        "--overlap-cpu-optimizer-d2h-h2d", "--use-precision-aware-optimizer",
        "--attention-dropout", "0", "--hidden-dropout", "0", "--attention-backend", "flash",
        "--recompute-granularity", "full", "--recompute-method", "uniform", "--recompute-num-layers", "1",
        "--sglang-served-model-name", endpoint.model, "--sglang-mem-fraction-static", "0.65",
        "--sglang-context-length", str(context_length), "--sglang-disable-cuda-graph",
        "--sglang-tool-call-parser", "behave-qwen3-coder", "--sglang-reasoning-parser", "qwen3",
        "--behave-rollout-binding", str(binding_path), "--behave-rollout-binding-sha256", BlobRef.of(binding_path.read_bytes()).sha256,
        "--behave-native-entrypoint", str(upstream / "train.py"),
        "--behave-native-entrypoint-sha256", BlobRef.of((upstream / "train.py").read_bytes()).sha256,
        "--behave-host-netns", str(os.stat("/proc/self/ns/net").st_ino),
        "--behave-cpus", os.environ["BEHAVE_TEST_TRAINING_CPUS"], "--behave-ray-cpus", "32")
    env = {**spec.environment, **environment(prefix, short_native_cache[0],
               cuda_visible_devices=os.environ["CUDA_VISIBLE_DEVICES"], megatron_source=upstream.parent / "megatron-upstream"),
           "TORCHDYNAMO_DISABLE": "1", "NCCL_SOCKET_IFNAME": "lo", "GLOO_SOCKET_IFNAME": "lo"}
    for name in ("BEHAVE_EDA_ROOT", "BEHAVE_WORK_DIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON"):
        if name in os.environ:
            env[name] = os.environ[name]
    training_evidence = {}
    # Include native Adam checkpoint writes and byte inventories on the
    # explicit checkpoint volume, not just generation/backward time. A measured
    # 27B save alone took 675 s; the old 2700 s phase cut off the second save.
    phase_seconds = 18000 if large else 7200
    for phase, load, extra in (("initial", model, ()),
                               ("resume", cycle_checkpoints, ("--num-rollout", "3"))):
        print(f"BEHAVE_SLIME_CYCLE {phase}", flush=True)
        from behave.core.lifecycle import CleanupState

        short_native_cache[1][0] = CleanupState.UNKNOWN
        result = run_service((spec.argv[0], "-B", "-m", "behave.train.slime_driver", *argv,
            "--load", str(load), *extra), cwd=tmp_path, environment=env, network="none",
            deadline=time.monotonic() + phase_seconds, cleanup_seconds=120, output_bytes=32 << 20)
        short_native_cache[1][0] = result.cleanup_state
        (tmp_path / f"{phase}.stdout").write_bytes(result.stdout)
        (tmp_path / f"{phase}.stderr").write_bytes(result.stderr)
        print(result.stderr[-12000:].decode(errors="replace"), flush=True)
        assert result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent, (
            result.status, result.stdout[-32000:].decode(errors="replace"), result.stderr[-16000:].decode(errors="replace"))
        text = (result.stdout + result.stderr).decode(errors="replace")
        training_evidence[phase] = {role: training_steps(text, role=role)
            for role in (("actor", "critic") if algorithm == "ppo" else ("actor",))}
        (tmp_path / "training.json").write_bytes(canonical_json(training_evidence))
        if phase == "initial":
            # Round one alone is needed for cold resume; all small inventories
            # and the HF weight comparisons remain independent of retention.
            release_obsolete_checkpoint(tmp_path, checkpoints=cycle_checkpoints, critic=algorithm == "ppo")
    check = run_service((spec.argv[0], "-B", str(Path(__file__).with_name("support") / "slime_weight_fixture.py"),
        str(model), *(str(tmp_path / f"hf_{i}") for i in range(3))), cwd=tmp_path,
        environment=env, network="none", deadline=time.monotonic() + 120, output_bytes=1 << 20)
    assert check.status == "exited" and check.returncode == 0 and check.cleanup_state.quiescent, check.stderr.decode()
    weight_evidence = parse_json(check.stdout)
    (tmp_path / "weights.json").write_bytes(canonical_json(weight_evidence))
    assert (cycle_checkpoints / "latest_checkpointed_iteration.txt").read_text().strip() == "2"
    if algorithm == "ppo":
        assert (cycle_checkpoints / "critic/latest_checkpointed_iteration.txt").read_text().strip() == "2"
    # Equal-reward groups are valid batches, not missing results. Learning is
    # established separately by native gradients in each phase and numerical
    # weight changes above; PPO advantages additionally depend on its critic.
    evidence = {**scored_rounds(tmp_path, manifest=manifest, groups=groups, recipe=recipe),
        "tasks": len(tasks), "samples_per_task": per_task, "context_limit": context_length,
        "weights": weight_evidence, "model": profile["model_revision"], "algorithm": algorithm,
        "layout": asdict(layout),
        "training": training_evidence}
    (tmp_path / "cycle.json").write_bytes(canonical_json(evidence))
    record_testsuite_property("slime_live_cycle_" + algorithm + "_" + workload, evidence)
