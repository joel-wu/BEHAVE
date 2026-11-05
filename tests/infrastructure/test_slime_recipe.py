"""Candidate statistics and GPU arithmetic, without importing torch or Slime."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_training_examples import FixtureResult, FixtureVerifier, fixture

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, metadata
from behave.train.slime_launch import ALGORITHMS, GROUPED, REWARD_HOOK, SlimeLayout, SlimeRecipe, model_template, preset
from behave.train.slime_rollout import post_process_rewards, prefix_cache_usage, prepare_batch
from behave.train.training_data import prepare_examples


def arguments(recipe):
    return SimpleNamespace(
        advantage_estimator=recipe.algorithm, loss_type="policy_loss", custom_reward_post_process_path=REWARD_HOOK,
        rewards_normalization=False, grpo_std_normalization=False, use_rollout_logprobs=True,
        gamma=1., lambd=1., kl_coef=0., eps_clip=recipe.clip_low, eps_clip_high=recipe.clip_high,
        rollout_temperature=1., rollout_top_p=1., rollout_top_k=-1,
        kl_loss_coef=recipe.kl_loss, use_kl_loss=bool(recipe.kl_loss), entropy_coef=recipe.entropy,
        kl_loss_type="low_var_kl", calculate_per_token_loss=False, normalize_advantages=recipe.normalize_advantages,
        custom_advantage_function_path=None,
        compute_advantages_and_returns=True, eps_clip_c=None,
        use_opsm=False, use_tis=False, use_opd=False, get_mismatch_metrics=False,
        custom_pg_loss_reducer_function_path=None,
        global_batch_size=1,
    )


def examples(tmp_path, monkeypatch):
    store, ref, policy, trace, turn, candidate = fixture(tmp_path, monkeypatch)
    trace = replace(trace, signals={"old_log_probs": [-1., -2.]})
    turn = replace(turn, token_data=metadata(trace.as_dict()))
    candidate = replace(candidate, trajectory=replace(candidate.trajectory, turns=(turn,)))
    from behave.train import training_data as module

    monkeypatch.setattr(module, "load_candidate", lambda *_: candidate)
    return prepare_examples(store, ref, policy, FixtureVerifier())


def candidate(base, identity, reward, count=1):
    result = FixtureResult(**{**base.candidate.result.__dict__, "attempt_id": identity, "score": reward})
    turns = tuple(replace(base.turns[0], turn=i) for i in range(count))
    return replace(base, candidate=replace(base.candidate, result=result,
                   result_ref=BlobRef.of(identity.encode())), turns=turns)


def samples(batch):
    result = []
    for p in batch.payloads:
        item = SimpleNamespace(**p)
        item.get_reward_value = lambda _, reward=p["reward"]: reward
        result.append(item)
    return result


@pytest.mark.parametrize("cached", [0, 7, 10])
def test_cache_metrics_use_actual_response_counts(cached):
    assert prefix_cache_usage({"usage": {"prompt_tokens_details": {"cached_tokens": cached}}}, 10) == {
        "cached_tokens": cached, "total_prompt_tokens": 10}


@pytest.mark.parametrize("body", [{}, {"usage": None}, {"usage": {}},
    *({"usage": {"prompt_tokens_details": {"cached_tokens": value}}} for value in (None, True, -1, 11, "3"))])
def test_missing_or_invalid_cache_telemetry_is_not_zero(body):
    assert prefix_cache_usage(body, 10) is None


def test_native_cache_metadata_when_openai_cache_report_is_disabled():
    body = {"usage": {"prompt_tokens_details": None},
            "choices": [{"meta_info": {"prompt_tokens": 10, "cached_tokens": 7}}]}
    assert prefix_cache_usage(body, 10) == {"cached_tokens": 7, "total_prompt_tokens": 10}
    assert prefix_cache_usage(body, 11) is None
    body["usage"]["prompt_tokens_details"] = {"cached_tokens": -1}
    assert prefix_cache_usage(body, 10) is None


@pytest.mark.parametrize("algorithm", (*ALGORITHMS, "grpo_no_std"))
def test_recipe_round_trip_and_native_flags(algorithm):
    recipe = preset(algorithm)
    assert SlimeRecipe.from_dict(recipe.as_dict()) == recipe
    recipe.validate_args(arguments(recipe))
    argv = recipe.arguments()
    assert argv[argv.index("--advantage-estimator") + 1] == recipe.algorithm
    assert argv[argv.index("--router-policy") + 1] == "consistent_hashing"
    assert "--calculate-per-token-loss" not in argv
    assert ("--normalize-advantages" in argv) == algorithm.startswith("reinforce_plus_plus")
    if algorithm == "cispo":
        assert recipe.clip_low == 1 and recipe.clip_high == 4


def test_statistics_count_candidates_not_turns_including_zero_trainable_candidates(tmp_path, monkeypatch):
    base = examples(tmp_path, monkeypatch)
    group = (candidate(base, "a", 1, 3), candidate(base, "b", 0, 1), candidate(base, "c", 0, 0))
    batch = prepare_batch(group, group_ids=("g",) * 3, recipe=preset("grpo_no_std"), first_rollout=8)
    assert len(batch.payloads) == 4 and len(batch.candidates) == 3
    assert [p["rollout_id"] for p in batch.payloads] == [8, 8, 8, 9]
    assert [p["index"] for p in batch.payloads] == [0, 1, 2, 3]
    assert [r["credit"] for r in batch.candidates] == pytest.approx([2/3, -1/3, -1/3])
    raw, credit = post_process_rewards(arguments(batch.recipe), samples(batch)[::-1])
    assert raw == [0, 1, 1, 1] and credit == pytest.approx([-1/3, 2/3, 2/3, 2/3])


def test_distinct_groups_sample_std_and_zero_variance(tmp_path, monkeypatch):
    base = examples(tmp_path, monkeypatch)
    group = tuple(candidate(base, str(i), r) for i, r in enumerate((1, 0, 1, 1)))
    batch = prepare_batch(group, group_ids=("a", "a", "b", "b"), recipe=preset("grpo"))
    scale = 2 ** -.5 + 1e-6
    assert [r["credit"] for r in batch.candidates] == pytest.approx([.5/scale, -.5/scale, 0, 0])


@pytest.mark.parametrize("algorithm", (*ALGORITHMS, "grpo_no_std"))
@pytest.mark.parametrize("reward", [0., 1.])
def test_equal_rewards_preserve_candidates_and_turns(tmp_path, monkeypatch, algorithm, reward):
    base = examples(tmp_path, monkeypatch)
    batch = prepare_batch((candidate(base, "a", reward, 3), candidate(base, "b", reward, 1)),
                          group_ids=("g", "g"), recipe=preset(algorithm))
    # Group-relative estimators receive zero. PPO/REINFORCE++ receive raw
    # terminal rewards; native value estimation/whitening is not done here.
    credit = 0. if batch.recipe.algorithm in GROUPED else reward
    assert [c["reward"] for c in batch.candidates] == [reward, reward]
    assert [c["credit"] for c in batch.candidates] == [credit, credit]
    assert [p["rollout_id"] for p in batch.payloads] == [0, 0, 0, 1]
    assert post_process_rewards(arguments(batch.recipe), samples(batch)) == ([reward] * 4, [credit] * 4)


def test_native_scheduler_must_not_drop_candidates_after_turn_exclusion(tmp_path, monkeypatch):
    base = examples(tmp_path, monkeypatch)
    candidates = tuple(candidate(base, str(i), i % 2, 0 if i == 3 else 1) for i in range(4))
    batch = prepare_batch(candidates, group_ids=("g",) * 4, recipe=preset("grpo"))
    args = arguments(batch.recipe)
    args.global_batch_size = 2
    with pytest.raises(ValueError, match="trailing candidates"):
        post_process_rewards(args, samples(batch))
    args.global_batch_size = 3
    assert len(post_process_rewards(args, samples(batch))[0]) == 3


@pytest.mark.parametrize("algorithm", ["ppo", "reinforce_plus_plus"])
def test_nongrouped_recipes_keep_raw_terminal_reward(tmp_path, monkeypatch, algorithm):
    base = examples(tmp_path, monkeypatch)
    batch = prepare_batch((candidate(base, "a", 1, 2),), group_ids=(None,), recipe=preset(algorithm))
    assert post_process_rewards(arguments(batch.recipe), samples(batch)) == ([1., 1.], [1., 1.])


@pytest.mark.parametrize("change", ["missing_probs", "missing_group", "duplicate", "different_task", "unavailable"])
def test_unsafe_batch_never_reaches_optimizer(tmp_path, monkeypatch, change):
    base = examples(tmp_path, monkeypatch)
    a, b = candidate(base, "a", 1), candidate(base, "b", 0)
    group_ids = ("g", "g")
    if change == "missing_probs":
        b = replace(b, turns=(replace(b.turns[0], tokens=replace(b.turns[0].tokens, signals={})),))
    elif change == "missing_group":
        group_ids = (None, None)
    elif change == "duplicate":
        b = a
    elif change == "different_task":
        b.candidate.result.task_id = "another-task"
    elif change == "unavailable":
        b = replace(b, turns=(replace(b.turns[0], status="unavailable"),))
    with pytest.raises((ValueError, RecordError)):
        prepare_batch((a, b), group_ids=group_ids, recipe=preset("grpo"))


@pytest.mark.parametrize("key,value", [("gamma", .9), ("lambd", .95), ("kl_coef", .1),
    ("rollout_top_p", .95), ("rollout_temperature", .8),
    ("calculate_per_token_loss", True), ("normalize_advantages", True), ("rewards_normalization", True),
    ("use_opsm", True), ("use_tis", True), ("use_opd", True), ("compute_advantages_and_returns", False),
    ("custom_pg_loss_reducer_function_path", "custom.reduction"), ("eps_clip_c", 3.)])
def test_unimplemented_semantics_cannot_be_silently_selected(key, value):
    recipe = preset("ppo")
    args = arguments(recipe)
    setattr(args, key, value)
    with pytest.raises(ValueError, match=key):
        recipe.validate_args(args)


def test_layout_does_not_confuse_four_gpu_actor_with_upstream_32_gpu_template(tmp_path):
    layout = SlimeLayout()
    layout.validate_model({"text_config": {"hidden_size": 5120, "num_attention_heads": 24,
                          "num_key_value_heads": 4, "intermediate_size": 17408, "num_hidden_layers": 64}})
    assert layout.required_gpus == 4 and replace(layout, colocate=False).required_gpus == 8
    assert layout.arguments()[layout.arguments().index("--num-gpus-per-node") + 1] == "4"
    with pytest.raises(ValueError, match="TP"):
        replace(layout, pipeline_parallel=2, context_parallel=4)
    with pytest.raises(ValueError, match="engine"):
        replace(layout, rollout_gpus_per_engine=3)
    path = tmp_path / "model.sh"
    path.write_text('# harmless comment\nMODEL_ARGS=(\n --hidden-size 5120\n --spec plugin fn\n)\n')
    assert model_template(path) == ("--hidden-size", "5120", "--spec", "plugin", "fn")
    assert model_template(path, spec=("behave.train.slime_qwen", "get_qwen3_5_spec")) == (
        "--hidden-size", "5120", "--spec", "behave.train.slime_qwen", "get_qwen3_5_spec")
    path.write_text('MODEL_ARGS=( --hidden-size $(evil) )')
    with pytest.raises(ValueError, match="expansions"):
        model_template(path)


def test_pipeline_layout_partitions_layers_without_adding_training_gpus():
    layout = SlimeLayout(tensor_parallel=1, pipeline_parallel=4, colocate=False, gpus_per_node=8)
    config = {"hidden_size": 5120, "num_attention_heads": 24, "num_key_value_heads": 4,
              "intermediate_size": 17408, "num_hidden_layers": 64}
    layout.validate_model(config)
    assert layout.actor_gpus == 4 and layout.required_gpus == 8
    argv = layout.arguments()
    assert argv[argv.index("--tensor-model-parallel-size") + 1] == "1"
    assert argv[argv.index("--pipeline-model-parallel-size") + 1] == "4"
    assert "--sequence-parallel" not in argv and "--colocate" not in argv
    with pytest.raises(ValueError, match="pipeline"):
        layout.validate_model({**config, "num_hidden_layers": 63})


def test_native_runtime_paths_are_explicit_not_login_environment(tmp_path, monkeypatch):
    from behave.train.slime_launch import environment

    prefix = tmp_path / "prefix"
    for part in ("bin", "lib/python3.1/site-packages", "lib/python3.12/site-packages/torch/lib", "lib/python3.12/site-packages/nvidia/cudnn/lib"):
        (prefix / part).mkdir(parents=True)
    (prefix / "bin/python").touch()
    (prefix / "lib/python3.13").symlink_to("python3.12", target_is_directory=True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/wrong-runtime")
    monkeypatch.setenv("PROVIDER_KEY", "must-not-copy")
    result = environment(prefix, tmp_path / "scratch", cuda_visible_devices="3,5,6,7",
                         cpu_threads=5, build_jobs=11)
    assert result["OMP_NUM_THREADS"] == result["MKL_NUM_THREADS"] == "5"
    assert result["MAX_JOBS"] == "11"
    assert "nvidia/cudnn/lib" in result["LD_LIBRARY_PATH"]
    assert result["CUDA_VISIBLE_DEVICES"] == "3,5,6,7" and result["CUDA_HOME"] == str(prefix)
    assert Path(result["FLASHINFER_WORKSPACE_BASE"]).is_relative_to(tmp_path / "scratch")
    assert Path(result["TVM_FFI_CACHE_DIR"]).is_relative_to(tmp_path / "scratch")
    assert Path(result["TVM_FFI_CACHE_DIR"]).is_dir()
    assert "/wrong-runtime" not in result["LD_LIBRARY_PATH"] and "PROVIDER_KEY" not in result
    with pytest.raises(ValueError):
        environment(prefix, tmp_path, cuda_visible_devices="")


def test_tokenize_workers_are_explicit_and_preserve_other_plugins():
    from behave.train.slime_launch import tokenize_environment

    original = {"SGLANG_PLUGINS": "behave_qwen_tools", "PATH": "/native/bin"}
    assert tokenize_environment(original, None) == original
    changed = tokenize_environment(original, 2)
    assert changed == {**original, "SGLANG_PLUGINS": "behave_qwen_tools,behave_tokenize_workers",
                       "BEHAVE_TOKENIZE_WORKERS": "2"}
    assert tokenize_environment(changed, 3)["SGLANG_PLUGINS"] == changed["SGLANG_PLUGINS"]
    assert "BEHAVE_TOKENIZE_WORKERS" not in original
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            tokenize_environment(original, invalid)


def test_native_cuda_loader_hints_do_not_scan_entire_conda_prefix(tmp_path, monkeypatch):
    from behave.train.slime_launch import environment

    prefix = tmp_path / "native"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").touch()
    site = prefix / "lib/python3.12/site-packages"
    (site / "torch/lib").mkdir(parents=True)
    for lib, package in (("cudnn", "cudnn"), ("nvrtc", "cuda_nvrtc"), ("curand", "curand")):
        directory = site / "nvidia" / package / "lib"
        directory.mkdir(parents=True)
        (directory / f"lib{lib}.so.12").touch()
        monkeypatch.setenv(f"{lib.upper()}_HOME", "/unrelated/toolchain")
    result = environment(prefix, tmp_path / "scratch", cuda_visible_devices="0,1,2,3")
    for lib, package in (("cudnn", "cudnn"), ("nvrtc", "cuda_nvrtc"), ("curand", "curand")):
        assert result[f"{lib.upper()}_HOME"] == str(site / "nvidia" / package / "lib")
    assert result["CUDA_HOME"] == str(prefix)
    (site / "nvidia/curand/lib/libcurand.so.12").unlink()
    assert "CURAND_HOME" not in environment(prefix, tmp_path / "scratch", cuda_visible_devices="0")


def test_kernel_cache_reuse_does_not_reuse_process_scratch(tmp_path):
    from behave.train.slime_launch import environment

    prefix = tmp_path / "native"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").touch()
    (prefix / "lib/python3.12/site-packages/torch/lib").mkdir(parents=True)
    cache = tmp_path / "kernels"
    first = environment(prefix, tmp_path / "run-a", cuda_visible_devices="0", kernel_cache=cache)
    second = environment(prefix, tmp_path / "run-b", cuda_visible_devices="0", kernel_cache=cache)
    for key in ("TRITON_CACHE_DIR", "TORCH_EXTENSIONS_DIR", "TORCHINDUCTOR_CACHE_DIR",
                "CUDA_CACHE_PATH", "NUMBA_CACHE_DIR", "TVM_FFI_CACHE_DIR", "FLASHINFER_WORKSPACE_BASE"):
        assert first[key] == second[key]
        assert Path(first[key]).is_relative_to(cache)
    for key in ("TMPDIR", "BEHAVE_SCRATCH_DIR", "RAY_TMPDIR", "XDG_CACHE_HOME"):
        assert first[key] != second[key]
        assert Path(second[key]).is_relative_to(tmp_path / "run-b")


def test_native_driver_linker_paths_are_explicit_and_never_toolkit_stubs(tmp_path, monkeypatch):
    from behave.train import slime_launch as slime_launch_module

    prefix = tmp_path / "native"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").touch()
    (prefix / "lib/python3.12/site-packages/torch/lib").mkdir(parents=True)
    stubs = prefix / "lib/stubs"
    stubs.mkdir()
    (stubs / "libcuda.so").touch()
    driver = tmp_path / "driver"
    driver.mkdir()
    monkeypatch.setattr(slime_launch_module, "_DRIVER_LIBRARY_DIRS", (driver, tmp_path / "absent"))
    monkeypatch.setenv("LIBRARY_PATH", "/wrong-runtime")
    kwargs = {"cuda_visible_devices": "0"}
    assert "LIBRARY_PATH" not in slime_launch_module.environment(prefix, tmp_path / "scratch", **kwargs)
    (driver / "libcuda.so").touch()
    env = slime_launch_module.environment(prefix, tmp_path / "scratch", **kwargs)
    assert env["LIBRARY_PATH"] == str(driver)
    assert env["LD_LIBRARY_PATH"].split(":")[-1] == str(driver)
    assert str(stubs) not in env["LD_LIBRARY_PATH"]


@pytest.mark.parametrize("name,value", [("temperature", None), ("top_p", None), ("top_k", None),
    ("top_k", 20), ("temperature", .8), ("top_p", .95), ("temperature", True)])
def test_sampling_never_inherits_model_defaults(name, value):
    from behave.train.slime_launch import require_sampling_profile

    parameters = {"temperature": 1, "top_p": 1., "top_k": -1}
    require_sampling_profile(parameters)
    if value is None:
        del parameters[name]
    else:
        parameters[name] = value
    with pytest.raises(ValueError, match="sampling"):
        require_sampling_profile(parameters)
