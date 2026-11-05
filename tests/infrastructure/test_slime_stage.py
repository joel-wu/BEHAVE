"""Stage identity/recovery, with an opaque native-worker fixture and no model.

This tests the production stage coordinator and native checkpoint inventory.
It is not evidence of optimization or learned weights.
"""

import json
from pathlib import Path

import pytest
from test_slime_raw_plan import raw_inputs
from test_slime_training_plan import inputs

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.isolation.service import ServiceResult
from behave.train.slime_stage import _native, load_learning_report, run_learning
from tools.slime_raw_plan import prepare as raw_prepare
from tools.slime_training_plan import prepare as task_prepare


def prepared(tmp_path, monkeypatch, inputs, route):
    output = tmp_path / "plan"
    if route == "task":
        config, release, split, task_id = inputs
        task_prepare(output, config, release, [task_id], split)
    else:
        config, store, ref, _ = raw_inputs(tmp_path, monkeypatch, inputs)
        raw_prepare(output, config, store.directory, ref)
    return {"plan": output, "run": JournalDirectory(tmp_path / "stage"), "updates": 2,
        "load": tmp_path / "initial", "save": tmp_path / "checkpoints", "cpus": 16,
        "environment": {"PATH": "/usr/bin:/bin"}}, config


def native_worker(options, config, *, missing_update=False):
    """Test-only opaque checkpoint writes; does not claim a real optimizer."""
    def execute(argv, **kwargs):
        assert "behave.train.slime_driver" in argv
        assert kwargs["network"] == "none" and kwargs["deadline"] is None
        binding = json.loads((options["plan"] / "binding.json").read_text())
        native = _native(binding, config, load=options["load"], save=options["save"], updates=options["updates"])
        marker = options["load"] / "latest_checkpointed_iteration.txt"
        first = int(marker.read_text()) + 1 if marker.exists() else 0
        for step in range(first + int(missing_update), options["updates"]):
            events = native.journal.load()
            native.journal.append(events, native.ready_kind, {"round": step, "binding": native.identity.as_dict()})
            directory = options["save"] / f"iter_{step:07d}"
            directory.mkdir(parents=True)
            (directory / "state.pt").write_bytes(f"opaque worker state {step}".encode())
            (options["save"] / "latest_checkpointed_iteration.txt").write_text(str(step))
            native.save(step)
        return ServiceResult("exited", 0, b"supervisor stdout", b"", .1)
    return execute


@pytest.mark.parametrize("route", ["task", "raw"])
def test_learning_stage_cold_replay_and_continuation(tmp_path, monkeypatch, inputs, route):
    options, config = prepared(tmp_path, monkeypatch, inputs, route)
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(options, config))
    report = run_learning(**options)
    assert report["status"] == "completed" and report["route"] == route
    assert (report["first_update"], report["stop_update"]) == (0, 2)
    head, _ = options["run"].seal("learning")
    assert load_learning_report(options["run"].objects, head) == report
    next_options = {**options, "run": JournalDirectory(tmp_path / "continuation"),
                    "load": options["save"], "updates": 3}
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(next_options, config))
    continued = run_learning(**next_options)
    assert (continued["first_update"], continued["stop_update"]) == (2, 3)
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **kw: pytest.fail("cold redispatch"))
    # A later saved update does not invalidate the earlier immutable receipt.
    assert run_learning(**options) == report
    assert run_learning(**next_options) == continued
    with pytest.raises(RecordError, match="replay differs"):
        run_learning(**{**options, "cpus": 17})


@pytest.mark.parametrize("route", ["task", "raw"])
def test_shared_learning_stage_applies_tokenize_runtime(tmp_path, monkeypatch, inputs, route):
    inputs[0]["tokenize_workers"] = 2
    options, config = prepared(tmp_path, monkeypatch, inputs, route)
    options["environment"]["SGLANG_PLUGINS"] = "behave_qwen_tools"
    worker = native_worker(options, config)

    def execute(argv, **kwargs):
        assert kwargs["environment"]["BEHAVE_TOKENIZE_WORKERS"] == "2"
        assert kwargs["environment"]["SGLANG_PLUGINS"] == "behave_qwen_tools,behave_tokenize_workers"
        return worker(argv, **kwargs)

    monkeypatch.setattr("behave.train.slime_stage.run_service", execute)
    assert run_learning(**options)["status"] == "completed"


@pytest.mark.parametrize("route", ["task", "raw"])
@pytest.mark.parametrize("setting", [{}, {"isolation_startup_slots": None}, {"isolation_startup_slots": 4}])
def test_native_training_and_rollout_share_explicit_startup_policy(tmp_path, monkeypatch, inputs, route, setting):
    from behave.isolation.startup import startup_slots

    inputs[0].update(setting)
    options, config = prepared(tmp_path, monkeypatch, inputs, route)
    options["environment"]["BEHAVE_ISOLATION_STARTUP_SLOTS"] = "8"
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "1")
    worker = native_worker(options, config)

    def execute(argv, **kwargs):
        expected = setting.get("isolation_startup_slots")
        assert startup_slots() == expected
        assert kwargs["environment"]["BEHAVE_ISOLATION_STARTUP_SLOTS"] == ("none" if expected is None else "4")
        return worker(argv, **kwargs)

    monkeypatch.setattr("behave.train.slime_stage.run_service", execute)
    assert run_learning(**options)["status"] == "completed"
    assert options["environment"]["BEHAVE_ISOLATION_STARTUP_SLOTS"] == "8"
    assert run_learning(**options)["status"] == "completed"


def test_unknown_dispatch_does_not_launch_another_update(tmp_path, monkeypatch, inputs):
    options, _ = prepared(tmp_path, monkeypatch, inputs, "task")
    def disconnected(*args, **kwargs):
        raise AttemptInterrupted("fixture-controller-disconnected")
    monkeypatch.setattr("behave.train.slime_stage.run_service", disconnected)
    with pytest.raises(AttemptInterrupted):
        run_learning(**options)
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **kw: pytest.fail("duplicate native update"))
    with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
        run_learning(**options)


def test_recorded_cpu_preparation_precedes_native_dispatch(tmp_path, monkeypatch, inputs):
    options, config = prepared(tmp_path, monkeypatch, inputs, "task")
    order = []
    worker = native_worker(options, config)

    def prepare_batch(plan):
        assert plan == options["plan"]
        order.append("prepare")

    def dispatch(*args, **kwargs):
        assert order == ["prepare"]
        order.append("dispatch")
        return worker(*args, **kwargs)

    monkeypatch.setattr("behave.train.slime_rollout.prepare_recorded_batches", prepare_batch)
    monkeypatch.setattr("behave.train.slime_stage.run_service", dispatch)
    assert run_learning(**options)["status"] == "completed"
    assert order == ["prepare", "dispatch"]


def test_successful_exit_cannot_hide_a_missing_update(tmp_path, monkeypatch, inputs):
    options, config = prepared(tmp_path, monkeypatch, inputs, "raw")
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(options, config, missing_update=True))
    with pytest.raises(RecordError, match="skips or repeats"):
        run_learning(**options)
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **kw: pytest.fail("known dead process redispatched"))
    with pytest.raises(RecordError, match="skips or repeats"):
        run_learning(**options)


def test_interrupted_process_is_not_a_learning_result_and_replays(tmp_path, monkeypatch, inputs):
    options, _ = prepared(tmp_path, monkeypatch, inputs, "task")
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **kw: ServiceResult("timeout", None, b"", b"", .1))
    report = run_learning(**options)
    assert report["status"] == "interrupted" and report["checkpoint"] is None
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **kw: pytest.fail("cold retry"))
    assert run_learning(**options) == report


def test_learning_lifetime_is_supplied_by_runtime_and_recorded(tmp_path, monkeypatch, inputs):
    options, _ = prepared(tmp_path, monkeypatch, inputs, "task")
    options.update(seconds=7200, cleanup_seconds=37)
    seen = {}

    def interrupted(*args, **kwargs):
        seen.update(kwargs)
        return ServiceResult("timeout", None, b"", b"", .1)

    monkeypatch.setattr("behave.train.slime_stage.time.monotonic", lambda: 100)
    monkeypatch.setattr("behave.train.slime_stage.run_service", interrupted)
    run_learning(**options)
    assert seen["deadline"] == 7300
    assert seen["cleanup_seconds"] == 37
    import resource
    assert seen["process_limit"] == resource.getrlimit(resource.RLIMIT_NPROC)[1]
    request = options["run"].journal("learning").load()[0].payload
    assert request["seconds"] == 7200 and request["cleanup_seconds"] == 37
    with pytest.raises(RecordError, match="replay differs"):
        run_learning(**{**options, "cleanup_seconds": 38})


def test_learning_does_not_supply_hidden_stage_or_cleanup_timeouts(tmp_path, monkeypatch, inputs):
    options, _ = prepared(tmp_path, monkeypatch, inputs, "task")
    seen = {}

    def interrupted(*args, **kwargs):
        seen.update(kwargs)
        return ServiceResult("cancelled", None, b"", b"", .1)

    monkeypatch.setattr("behave.train.slime_stage.run_service", interrupted)
    run_learning(**options)
    assert seen["deadline"] is seen["cleanup_seconds"] is None
    assert seen["output_bytes"] is None
    request = options["run"].journal("learning").load()[0].payload
    assert request["seconds"] is request["cleanup_seconds"] is None


@pytest.mark.parametrize("route", ["task", "raw"])
def test_new_prepared_round_imports_parent_and_exports_its_lineage(tmp_path, monkeypatch, inputs, route):
    before, config = prepared(tmp_path, monkeypatch, inputs, route)
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(before, config))
    report = run_learning(**before)
    parent = BlobRef.from_dict(report["checkpoint"])
    plan = tmp_path / "next-plan"
    parent_args = {"parent_reader": before["run"].objects, "parent_checkpoint": parent}
    if route == "task":
        _, release, split, task_id = inputs
        binding = task_prepare(plan, config, release, [task_id], split, **parent_args)
        assert binding["schema"] == "behave.slime-rollout-binding/3"
    else:
        old = json.loads((before["plan"] / "binding.json").read_text())
        binding = raw_prepare(plan, config, Path(old["objects"]), BlobRef.from_dict(old["corpus"]), **parent_args)
    assert binding["parent_checkpoint"] == parent.as_dict()
    after = {**before, "plan": plan, "run": JournalDirectory(tmp_path / "next-round"),
             "load": before["save"], "save": tmp_path / "next-checkpoints", "updates": 3}
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(after, config))
    continued = run_learning(**after)
    assert (continued["first_update"], continued["stop_update"]) == (2, 3)
    head, closure = after["run"].seal("learning")
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(after["run"].objects, closure)
    assert load_learning_report(exported, head) == continued
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **kw: pytest.fail("repeated cross-round update"))
    assert run_learning(**after) == continued


@pytest.mark.parametrize("route", ["task", "raw"])
def test_explicit_rollback_then_new_stage_keeps_one_logical_update_per_round(tmp_path, monkeypatch, inputs, route):
    from behave.train.slime_recovery import abandon_round

    options, config = prepared(tmp_path, monkeypatch, inputs, route)
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(options, config))
    original = run_learning(**options)
    binding = json.loads((options["plan"] / "binding.json").read_text())
    native = _native(binding, config, load=options["save"], save=options["save"], updates=3)
    native.journal.append(native.journal.load(), native.ready_kind,
                          {"round": 2, "binding": native.identity.as_dict()})
    result = abandon_round(options["plan"], load=options["save"], updates=3,
                           reason="workers exited before save", workers_stopped=True)
    assert result["next_round"] == 2
    after = {**options, "run": JournalDirectory(tmp_path / "recovered-stage"),
             "load": options["save"], "updates": 3}
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(after, config))
    recovered = run_learning(**after)
    assert (recovered["first_update"], recovered["stop_update"]) == (2, 3)
    head, _ = after["run"].seal("learning")
    assert load_learning_report(after["run"].objects, head) == recovered
    # Earlier accepted stages are still readable, and raw history keeps both
    # attempts even though the logical update history counts round 2 once.
    assert run_learning(**options) == original
    assert sum(e.kind == native.ready_kind and e.payload["round"] == 2
               for e in native.journal.load()) == 2
