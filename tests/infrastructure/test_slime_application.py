"""Application binding, policy identities and fail-closed native continuation."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_native_chat_tokens import native
from test_slime_recipe import arguments
from test_training_rollouts import plan_at

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.train.slime_checkpoint import checkpoint_due
from behave.train.slime_data import ScoredDataSource, engine_versions, require_weight_advance
from behave.train.slime_launch import SlimeLayout, preset
from behave.train.slime_rollout import bind_round


def configured(tmp_path, algorithm="grpo", *, round_seconds=60):
    endpoint, _ = native("sglang")
    endpoint = replace(endpoint, parameters={"max_tokens": 512, "temperature": 1, "top_p": 1, "top_k": -1})
    recipe = preset(algorithm)
    binding = {"schema": "behave.slime-rollout-binding/1", "control_python": "/usr/bin/python3",
        "control_package": "/package", "objects": str(tmp_path / "inputs"),
        "plan": BlobRef.of(b"plan").as_dict(), "manifest": BlobRef.of(b"manifest").as_dict(),
        "endpoint": endpoint.as_dict(), "recipe": recipe.as_dict(),
        "run_directory": str(tmp_path / "run"), "round_seconds": round_seconds}
    path = tmp_path / "binding.json"
    path.write_bytes(canonical_json(binding))
    args = arguments(recipe)
    args.behave_rollout_binding = str(path)
    args.behave_rollout_binding_sha256 = BlobRef.of(path.read_bytes()).sha256
    args.save = str(tmp_path / "checkpoint")
    args.load = args.save
    args.save_interval, args.num_rollout = 1, 5
    return ScoredDataSource(args)


@pytest.mark.parametrize("seconds", [0, -1, True, 1.5, "60"])
def test_round_binding_rejects_invalid_explicit_wall_budget(tmp_path, seconds):
    with pytest.raises(ValueError, match="round_seconds"):
        configured(tmp_path, round_seconds=seconds)


def test_disjoint_actor_and_rollout_devices_use_physical_node_capacity():
    layout = SlimeLayout(colocate=False)
    assert layout.required_gpus == layout.node_gpus == 8
    argv = layout.arguments()
    assert argv[argv.index("--num-gpus-per-node") + 1] == "8"
    with pytest.raises(ValueError, match="capacity"):
        SlimeLayout(colocate=False, gpus_per_node=4)


def test_round_binding_preserves_task_group_and_exclusion_policy(tmp_path, publication):
    plan = plan_at(tmp_path, publication)
    endpoint, _ = native("sglang")
    endpoint = replace(endpoint, parameters={"max_tokens": 512, "temperature": 1, "top_p": 1, "top_k": -1})
    policy = BlobRef.of(b"round-one-weights")
    result = bind_round(plan, endpoint, round_id=1, policy=policy)
    assert result.manifest == plan.manifest and result.group_ids == plan.group_ids
    assert result.requirements.required_signals == plan.requirements.required_signals
    for before, after in zip(plan.execution.jobs, result.execution.jobs, strict=True):
        assert before.task == after.task and before.command_limits == after.command_limits
        assert before.attempt.max_turns == after.attempt.max_turns
        assert before.attempt.attempt_id != after.attempt.attempt_id
        assert after.attempt.model_policy["transport"] == after.model.identity
    assert result.requirements.checkpoint == policy
    with pytest.raises(ValueError, match="sampling"):
        bind_round(plan, replace(endpoint, parameters={"max_tokens": 512, "temperature": 0}), round_id=0, policy=policy)


@pytest.mark.parametrize("testbench", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_scored_training_round_reuses_batch_resource_scheduling(tmp_path, publication, monkeypatch, testbench, cancelled):
    from threading import Event

    from behave.agent.attempt import AttemptInterrupted
    from behave.agent.run_store import JournalDirectory
    from behave.task_execution.testbench import TBPolicy
    from behave.train.slime_rollout import execute_round

    plan = plan_at(tmp_path, publication)
    if testbench:
        plan = replace(plan, execution=replace(plan.execution, testbench=TBPolicy("fixture", "fixture", "line")))
    endpoint, _ = native("sglang")
    endpoint = replace(endpoint, parameters={"temperature": 1, "top_p": 1, "top_k": -1})
    binding = {"objects": str(publication.reader.directory), "manifest": plan.manifest.as_dict(),
               "plan": publication.put(plan.to_bytes()).as_dict(), "run_directory": str(tmp_path / "rounds"),
               "control_python": "/control/bin/python", "control_package": "/control/package",
               "recipe": preset("grpo").as_dict()}
    seen = []
    stop = Event()
    backend_image = {"fixture": "unbound-backend"}

    def backend():
        seen.append("backend")
        return backend_image

    def run(**kwargs):
        assert not kwargs["cancelled"]()
        if cancelled:
            stop.set()
            assert kwargs["cancelled"]()
        assert kwargs["judge"] is backend_image
        assert kwargs["testbench"] is (backend_image if testbench else None)
        assert kwargs["plan"].group_ids == plan.group_ids
        assert kwargs["plan"].execution.jobs[0].attempt.max_turns == plan.execution.jobs[0].attempt.max_turns
        kwargs["run"].journal("training").append((), "fixture_completed", {})
        kwargs["on_prepared"]("prepared fixture")

    from behave.train.slime_rollout import SlimeBatch
    prepared_batch = SlimeBatch((), (), preset("grpo"))
    monkeypatch.setattr("behave.train.slime_rollout.from_prepared_report",
                        lambda *a, **kw: prepared_batch)
    monkeypatch.setattr("behave.train.learning_report.round_metrics",
                        lambda *a, **kw: {"round": kw["round_id"]})

    monkeypatch.setattr("behave.sim.api.CpuPool", lambda **_: pytest.fail("CPU admission belongs to run_batch"))
    monkeypatch.setattr("os.sched_getaffinity", lambda _: set(range(6)))
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", backend)
    monkeypatch.setattr("behave.task_execution.cocotb_worker.CocotbBackend", backend)
    monkeypatch.setattr("behave.train.rollouts.run_rollouts", run)
    if cancelled:
        with pytest.raises(AttemptInterrupted, match="external-workflow-cancelled"):
            execute_round(binding, round_id=0, endpoint=endpoint,
                          policy_record={"weights": "initial"}, cancelled=stop.is_set)
        assert not (tmp_path / "rounds/round_00000000/prepared_batch.json").exists()
    else:
        result = execute_round(binding, round_id=0, endpoint=endpoint,
                               policy_record={"weights": "initial"}, cancelled=stop.is_set)
        assert result["round"] == 0 and result["head"] is not None
        assert Path(result["prepared_batch"]).is_file()
    assert seen == ["backend"] * (2 if testbench else 1)
    assert len(JournalDirectory(tmp_path / "rounds/round_00000000").journal("training").load()) == 1


def test_checkpoint_commit_follows_native_save_and_never_replays_pending_round(tmp_path):
    source = configured(tmp_path)
    source.load(-1)
    with pytest.raises(ValueError, match="no matching"):
        source.load(0)
    with pytest.raises(ValueError, match="verified"):
        source.save(0)
    events = ()
    for kind in ("round_started", "round_scored"):
        event = source.journal.append(events, kind, {"round": 0, "binding": source.identity.as_dict()})
        events += (event,)
        with pytest.raises(ValueError, match="pending"):
            source.load(0)
    from pathlib import Path

    path = Path(source.args.save)
    path.mkdir()
    (path / "latest_checkpointed_iteration.txt").write_text("1")
    with pytest.raises(ValueError, match="not completed"):
        source.save(0)
    (path / "latest_checkpointed_iteration.txt").write_text("0")
    (path / "iter_0000000").mkdir()
    (path / "iter_0000000/state.pt").write_bytes(b"native opaque state")
    source.save(0)
    source.load(0)
    with pytest.raises(ValueError, match="disagree"):
        source.load(-1)
    with pytest.raises(ValueError, match="verified"):
        source.save(0)
    source.args.sglang_router_ip, source.args.sglang_router_port = "127.0.0.1", 9999
    source.args.rollout_num_gpus, source.args.rollout_num_gpus_per_engine = 4, 2
    with pytest.raises(ValueError, match="already used"):
        source.generate(0)
    (path / "iter_0000000/state.pt").write_bytes(b"another native state")
    with pytest.raises(ValueError, match="file inventory differs"):
        source.load(0)


def test_all_rollout_engines_must_agree_before_scoring(monkeypatch):
    from behave.train import slime_data as slime_data_module

    calls = []
    versions = {"http://127.0.0.1:1": "2", "http://127.0.0.1:2": "2"}

    def request(url, route, **kwargs):
        calls.append((url, route))
        if route == "/workers":
            return {"workers": [{"url": u} for u in versions]}
        return {"weight_version": versions[url]}

    monkeypatch.setattr(slime_data_module, "local_request", request)
    assert len(engine_versions("http://127.0.0.1:3", 2, flush=True)) == 2
    assert sum(route == "/flush_cache" for _, route in calls) == 2
    versions["http://127.0.0.1:2"] = "1"
    with pytest.raises(ValueError, match="different weight"):
        engine_versions("http://127.0.0.1:3", 2)
    with pytest.raises(ValueError, match="count"):
        engine_versions("http://127.0.0.1:3", 1)


def test_every_engine_advances_between_rounds_but_cold_start_can_reset_its_counter():
    initial = (("http://127.0.0.1:1", "1"), ("http://127.0.0.1:2", "1"))
    updated = tuple((url, "2") for url, _ in initial)
    require_weight_advance(None, initial)
    require_weight_advance(initial, updated)
    for bad in (initial, (updated[0], initial[1]), (("http://127.0.0.1:3", "2"), updated[1])):
        with pytest.raises(ValueError, match="advance"):
            require_weight_advance(initial, bad)


@pytest.mark.parametrize("interval,rounds,saved", [(1, 4, [0, 1, 2, 3]), (3, 7, [2, 5, 6]),
    (8, 2, [1]), (2, 4, [1, 3])])
def test_checkpoint_cadence_always_includes_the_final_round(interval, rounds, saved):
    args = SimpleNamespace(save_interval=interval, num_rollout=rounds)
    assert [step for step in range(rounds) if checkpoint_due(args, step)] == saved


@pytest.mark.parametrize("interval,rounds", [(None, 3), (0, 3), (-1, 3), (True, 3), (1.5, 3),
    (1, None), (1, 0), (1, True)])
def test_checkpoint_cadence_cannot_disable_durability(interval, rounds):
    with pytest.raises(ValueError, match="positive native save interval"):
        checkpoint_due(SimpleNamespace(save_interval=interval, num_rollout=rounds), 0)


@pytest.mark.parametrize("algorithm", ["grpo", "ppo"])
@pytest.mark.parametrize("interval", [1, 2, 8])
@pytest.mark.parametrize("round_seconds", [None, 60])
def test_live_periodic_checkpoints_keep_all_rounds_and_refuse_cold_unsaved_tails(tmp_path, monkeypatch, publication,
                                                                            algorithm, interval, round_seconds):
    from behave.train import slime_data as slime_data_module

    source = configured(tmp_path, algorithm, round_seconds=round_seconds)
    # The native side now also derives the exact requested task group. Keep a
    # genuine immutable training plan here; only execution/token verification
    # below are substituted for this checkpoint lifecycle test.
    plan = plan_at(tmp_path / "plan_inputs", publication)
    binding = {**source.binding, "objects": str(publication.root),
               "control_timeout_seconds": round_seconds, "cleanup_seconds": round_seconds,
               "round_log_bytes": round_seconds,
               "plan": publication.put(plan.to_bytes()).as_dict(), "manifest": plan.manifest.as_dict()}
    source.path.write_bytes(canonical_json(binding))
    source.args.behave_rollout_binding_sha256 = BlobRef.of(source.path.read_bytes()).sha256
    source = ScoredDataSource(source.args)
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    args = source.args
    args.save_interval, args.num_rollout = interval, 5
    args.sglang_router_ip, args.sglang_router_port = "127.0.0.1", 9999
    args.rollout_num_gpus, args.rollout_num_gpus_per_engine = 4, 2
    state = {"version": 1, "scored": []}
    def versions(*args, **kwargs):
        assert kwargs.get("timeout") == round_seconds
        return tuple((f"http://127.0.0.1:{port}", str(state["version"])) for port in (9001, 9002))

    monkeypatch.setattr(slime_data_module, "engine_versions", versions)

    # Substitute only the expensive scored process and token-data verifier.
    # Exercise the production generate/save/load and journal/file inventory.
    def score(argv, **kwargs):
        import json

        if round_seconds is None:
            assert kwargs["deadline"] is None
        else:
            assert 0 < kwargs["deadline"] - slime_data_module.time.monotonic() <= round_seconds
        assert kwargs["cleanup_seconds"] == round_seconds
        assert kwargs["output_bytes"] == round_seconds
        assert kwargs["environment"]["BEHAVE_SCRATCH_DIR"] == str(tmp_path / "scratch")
        assert kwargs["environment"]["TMPDIR"] == str(tmp_path / "tmp")
        request = json.loads(Path(argv[-2]).read_bytes())
        state["scored"].append(request["round"])
        endpoint = LocalChatEndpoint.from_dict(request["endpoint"])
        policy = BlobRef.of(canonical_json(request["policy"]))
        endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy))
        Path(argv[-1]).write_bytes(canonical_json({"schema": "behave.slime-scored-round/1",
            "round": request["round"], "manifest": source.binding["manifest"], "policy": policy.as_dict(),
            "endpoint": endpoint.as_dict(), "objects": str(tmp_path / "inputs"),
            "head": BlobRef.of(b"verified report substitute").as_dict()}))
        return SimpleNamespace(status="exited", returncode=0, stdout=b"", stderr=b"", elapsed_seconds=1.25,
                               cleanup_state=SimpleNamespace(quiescent=True))

    monkeypatch.setattr(slime_data_module, "run_service", score)
    monkeypatch.setattr(slime_data_module, "from_training_report", lambda *a, **kw:
        SimpleNamespace(payloads=("turn",), candidates=({"retained_turns": 1},),
                        rollout_output=lambda: "verified batch"))
    saved = []
    for step in range(args.num_rollout):
        assert source.generate(step) == "verified batch"
        import json
        timing = json.loads((source.run.directory / f"request_{step:08d}/timing.json").read_text())
        assert timing["round"] == step and timing["rollout_and_scoring_seconds"] == 1.25
        assert timing["started_at"] <= timing["finished_at"]
        with pytest.raises(ValueError, match="already used"):
            source.generate(step)
        # Reconstructing a source never authorizes a scored-but-unsaved tail,
        # even after live engines published an update. Neither last committed
        # iteration nor the pending one can silently replay/drop that tail.
        cold = ScoredDataSource(args)
        for cold_step in (step, saved[-1] if saved else -1):
            with pytest.raises(ValueError, match="pending"):
                cold.load(cold_step)
        due = checkpoint_due(args, step)
        if step + 1 < args.num_rollout:
            with pytest.raises(ValueError, match="already used" if due else "advance"):
                source.generate(step + 1)
            state["version"] += 1
            if due:
                with pytest.raises(ValueError, match="already used"):
                    source.generate(step + 1)
            with pytest.raises(ValueError, match="already used"):
                cold.generate(step + 1)
        if due:
            # Native actor and critic save separately before a joint commit.
            roots = [Path(args.save)]
            if algorithm == "ppo":
                roots.append(roots[0] / "critic")
            for root in roots:
                directory = root / f"iter_{step:07d}"
                directory.mkdir(parents=True)
                (directory / "state.pt").write_bytes(f"opaque state {step}".encode())
                (root / "latest_checkpointed_iteration.txt").write_text(str(step))
            source.save(step)
            cold.load(step)
            saved.append(step)
            # A committed checkpoint permits a genuinely cold source; engine
            # counters can restart only here, not at an unsaved tail.
            source = cold
            state["version"] = 1
    assert state["scored"] == list(range(args.num_rollout))
    events = source.journal.load()
    assert [e.payload["round"] for e in events if e.kind == "checkpoint_saved"] == saved
    assert events[-1].kind == "checkpoint_saved" and saved[-1] == args.num_rollout - 1
    with pytest.raises(ValueError, match="stopping round"):
        source.generate(args.num_rollout)


def test_binding_is_authenticated_before_native_imports(tmp_path):
    source = configured(tmp_path)
    args = SimpleNamespace(**vars(source.args))
    args.behave_rollout_binding_sha256 = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        ScoredDataSource(args)


def test_native_optimizer_is_rejected_before_allocating_models(tmp_path):
    from behave.train.slime_driver import validate

    source = configured(tmp_path)
    source.args.optimizer = "sgd"
    with pytest.raises(ValueError, match="Adam"):
        validate(source.args)


def test_native_router_retry_policy_is_explicit_for_the_separate_parser():
    # Slime merges a separately parsed SGLang namespace after custom defaults.
    # Only an explicit CLI flag survives that merge.
    assert "--router-disable-retries" in preset("grpo").arguments()


@pytest.mark.parametrize("allocated,slots,expected", [(188, None, 188), (188, 32, 32),
    (188, 8, None), (188, 189, None), (188, True, None), (189, 32, None), (True, 32, None)])
def test_ray_worker_slots_do_not_change_numa_cpu_visibility(monkeypatch, allocated, slots, expected):
    from behave.train.slime_driver import ray_cpu_count

    cpus = set(range(188))
    monkeypatch.setattr("behave.train.slime_driver.allocated_cpus", lambda: cpus)
    args = SimpleNamespace(behave_cpus=allocated, behave_ray_cpus=slots)
    if expected is None:
        with pytest.raises(ValueError):
            ray_cpu_count(args, 8)
    else:
        assert ray_cpu_count(args, 8) == expected
    assert cpus == set(range(188)) and args.behave_cpus == allocated


def test_slime_native_wire_is_an_explicit_dialect():
    from test_chat_wire import request

    from behave.agent.providers.native_tokens import decode_tokens

    endpoint, packet = native("sglang")
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, runtime="sglang-slime"))
    del packet["choices"][0]["response_token_ids"]
    assert endpoint.tokens.parameters() == {"return_prompt_token_ids": True, "return_meta_info": True,
                                           "logprobs": True, "top_logprobs": 0}
    assert LocalChatEndpoint.from_dict(endpoint.as_dict()) == endpoint
    trace = decode_tokens(endpoint.tokens, request(endpoint), canonical_json(packet))
    assert trace["response_ids"] == list(range(20, 25))
    assert trace["signals"]["old_log_probs"] == [-.5] * 5
    packet["usage"]["completion_tokens"] += 1
    assert decode_tokens(endpoint.tokens, request(endpoint), canonical_json(packet)) == trace
