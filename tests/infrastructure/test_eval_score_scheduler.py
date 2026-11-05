"""Cross-archive dispatch has one candidate queue, not an archive barrier."""

import json
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from tools.eval_score_job import CandidateQueue, run


def configure(tmp_path, monkeypatch, *, workers=2, cancelled=None):
    from behave.eval import scoring as scoring_module

    sources = [str(tmp_path / name) for name in ("first", "second")]
    plan = {"name": "shared", "workers": workers, "shards": [sources], "previous": None,
            "all_submitted": True, "selection": {p: {} for p in sources}, "retry_testbench": False}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr("behave.core.resources.os.sched_getaffinity", lambda _: set(range(workers)))
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", str(workers))
    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", lambda *a: {})
    monkeypatch.setattr("behave.isolation.runtime_environment.runtime_environment", lambda: None)
    if cancelled is not None:
        # Queue-only tests may run their coordinator in a worker thread. The
        # command-policy tests below use real main-thread signal/environment control.
        monkeypatch.setattr(scoring_module, "scoring_environment", lambda **kw: nullcontext(cancelled))
    return sources


def report(source):
    return SimpleNamespace(as_dict=lambda: {"source": str(source)})


@pytest.mark.parametrize("options,expected", [(None, None), ({"isolation_startup_slots": None}, None),
                                            ({"isolation_startup_slots": 4}, 4)])
@pytest.mark.parametrize("inherited", [None, "1"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_score_worker_resolves_gate_from_saved_options_not_parent(
        tmp_path, monkeypatch, options, expected, inherited, outcome):
    from behave.agent.attempt import AttemptInterrupted
    from behave.isolation.startup import startup_slots

    configure(tmp_path, monkeypatch)
    name = "BEHAVE_ISOLATION_STARTUP_SLOTS"
    if inherited is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, inherited)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    if options is not None:
        (tmp_path / "runtime_options.json").write_text(json.dumps(options))
    observed = []

    def score(source, *args, **kwargs):
        observed.append(startup_slots())
        if outcome == "failure":
            raise ValueError("fixture archive failure")
        if outcome == "cancel":
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            assert kwargs["cancelled"]()
            raise AttemptInterrupted("fixture-score-cancelled")
        return report(source)

    monkeypatch.setattr("behave.eval.scoring.score_saved", score)
    if outcome == "success":
        run(tmp_path)
    else:
        with pytest.raises(RuntimeError, match="some archived sources"):
            run(tmp_path)
    assert observed and all(value == expected for value in observed)
    restored = os.environ.get(name)
    assert restored == inherited
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    cpu = json.loads((tmp_path / "cpu-0.json").read_text())
    assert cpu["isolation_startup_slots"] == expected
    assert cpu["capacity"] == cpu["judge_workers"] == 2


@pytest.mark.parametrize("slots", [0, -1, True, "4", "none"])
def test_invalid_score_gate_fails_before_dispatch(tmp_path, monkeypatch, slots):
    configure(tmp_path, monkeypatch)
    (tmp_path / "runtime_options.json").write_text(json.dumps({"isolation_startup_slots": slots}))
    monkeypatch.setattr("behave.eval.scoring.score_saved", lambda *a, **kw: pytest.fail("invalid policy"))
    with pytest.raises(ValueError, match="positive integer or null"):
        run(tmp_path)


def test_scoring_controls_outlive_candidate_shutdown(tmp_path, monkeypatch):
    from behave.eval import scoring as scoring_module
    from behave.isolation.startup import startup_slots

    configure(tmp_path, monkeypatch)
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "1")
    (tmp_path / "runtime_options.json").write_text(json.dumps({"isolation_startup_slots": 4}))
    release, completed = Event(), []
    write = scoring_module.write_json

    def candidate(cancelled):
        assert release.wait(10)
        completed.append((startup_slots(), cancelled()))

    def score(source, name, *, executor, cancelled, **kwargs):
        # Even if an archive returns before its submitted work finishes, the
        # candidate queue must join while its command policy is still active.
        executor.submit(candidate, cancelled)
        return report(source)

    def publish(path, value):
        write(path, value)
        if path.name == "progress-0.json" and len(value) == 2:
            release.set()

    monkeypatch.setattr("behave.eval.scoring.score_saved", score)
    monkeypatch.setattr(scoring_module, "write_json", publish)
    run(tmp_path)
    assert completed == [(4, False), (4, False)]
    assert startup_slots() == 1


@pytest.mark.parametrize("slots", [None, 4])
def test_new_score_pass_pins_startup_policy_and_rejects_rebinding(tmp_path, monkeypatch, slots):
    from behave.eval import scoring as scoring_module

    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: None)
    out = tmp_path / "score"
    args = {"repository": tmp_path, "workers": 2, "isolation_startup_slots": slots}
    first = scoring_module.prepare(out, [tmp_path / "source"], **args)
    assert json.loads((out / "runtime_options.json").read_text()) == {"isolation_startup_slots": slots}
    assert scoring_module.prepare(out, [tmp_path / "source"], **args) == first
    with pytest.raises(ValueError, match="startup policy changed"):
        scoring_module.prepare(out, [tmp_path / "source"], **{**args, "isolation_startup_slots": 8})
    assert json.loads((out / "runtime_options.json").read_text()) == {"isolation_startup_slots": slots}


def test_archive_tail_does_not_block_next_archive_and_queue_is_shared(tmp_path, monkeypatch):
    sources = configure(tmp_path, monkeypatch, cancelled=lambda: False)
    slow_started, release, second_completed = Event(), Event(), Event()
    queues = []

    def score(source, name, *, executor, on_case, **kwargs):
        queues.append(executor)
        assert kwargs["cpu_resources"].snapshot()["capacity"] == 2 and kwargs["cpu_pool"] is None
        if str(source) == sources[0]:
            def slow():
                slow_started.set()
                assert release.wait(10)
            long = executor.submit(slow)
            executor.submit(lambda: None).result(timeout=10)
            long.result(timeout=10)
        else:
            assert slow_started.wait(10)
            executor.submit(second_completed.set).result(timeout=10)
        on_case({"status": "scored", "score": 1})
        return report(source)

    monkeypatch.setattr("behave.eval.scoring.score_saved", score)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        running = coordinator.submit(run, tmp_path)
        try:
            assert second_completed.wait(10), "next archive waited for the old archive's slow tail"
            assert not running.done()
        finally:
            release.set()
        running.result(timeout=10)
    assert len(queues) == 2 and queues[0] is queues[1]
    live = json.loads((tmp_path / "live-0.json").read_text())
    assert live["peak_active"] == 2
    assert live["active"] == live["queued"] == 0
    assert live["finished"] == 3
    assert all(x["state"] == "completed" for x in live["sources"].values())


def test_source_preparation_failure_does_not_cancel_other_archives(tmp_path, monkeypatch):
    sources = configure(tmp_path, monkeypatch)
    done = []

    def score(source, name, *, executor, **kwargs):
        if str(source) == sources[0]:
            raise ValueError("fixture archive unavailable")
        executor.submit(done.append, str(source)).result(timeout=10)
        return report(source)

    monkeypatch.setattr("behave.eval.scoring.score_saved", score)
    with pytest.raises(RuntimeError, match="some archived sources"):
        run(tmp_path)
    assert done == [sources[1]]
    progress = {x["source"]: x for x in json.loads((tmp_path / "progress-0.json").read_text())}
    assert progress[sources[0]]["state"] == "failed"
    assert progress[sources[1]]["state"] == "completed"
    assert "score" not in progress[sources[0]]


def test_committed_case_moves_live_source_out_of_preparing(tmp_path, monkeypatch):
    from behave.eval import scoring as scoring_module

    sources = configure(tmp_path, monkeypatch, cancelled=lambda: False)
    observed, release = Event(), Event()
    write = scoring_module.write_json
    monkeypatch.setattr("behave.core.runtime_defaults.JOB_HEARTBEAT_SECONDS", .01)

    def score(source, name, *, on_case, **kwargs):
        on_case({"status": "scored"})
        assert release.wait(10)
        return report(source)

    def snapshot(path, value):
        write(path, value)
        if path.name == "live-0.json" and all(
                value["sources"][source]["state"] == "scoring" for source in sources):
            observed.set()

    monkeypatch.setattr("behave.eval.scoring.score_saved", score)
    monkeypatch.setattr(scoring_module, "write_json", snapshot)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run, tmp_path)
        try:
            assert observed.wait(10)
            assert not future.done()
        finally:
            release.set()
        future.result(timeout=10)
    live = json.loads((tmp_path / "live-0.json").read_text())
    assert all(item["state"] == "completed" and item["returned"] == 1 for item in live["sources"].values())


def test_cancel_before_admission_does_not_dispatch_an_archive(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, cancelled=lambda: True)
    monkeypatch.setattr("behave.eval.scoring.score_saved", lambda *a, **kw: pytest.fail("cancelled"))
    with pytest.raises(RuntimeError, match="some archived sources"):
        run(tmp_path)
    live = json.loads((tmp_path / "live-0.json").read_text())
    assert live["peak_active"] == live["finished"] == 0
    assert all(x["returned"] == 0 for x in live["sources"].values())


def test_candidate_queue_releases_slots_after_failure():
    with CandidateQueue(2) as candidates:
        def fail():
            raise ValueError("candidate fixture")
        failed = candidates.submit(fail)
        assert candidates.submit(lambda: 42).result(timeout=10) == 42
        with pytest.raises(ValueError, match="candidate fixture"):
            failed.result(timeout=10)
    activity = candidates.activity()
    assert activity["active"] == activity["queued"] == 0
    assert activity["finished"] == 2 and 1 <= activity["peak_active"] <= 2


@pytest.mark.parametrize("shutdown", [False, True])
def test_queued_cancellation_releases_activity_without_executing(shutdown):
    entered, release = Event(), Event()
    called = []

    def blocking():
        entered.set()
        assert release.wait(10)

    with CandidateQueue(1) as queue:
        first = queue.submit(blocking)
        try:
            assert entered.wait(10)
            assert not first.cancel()  # Running work must remain active.
            pending = [queue.submit(called.append, i) for i in range(3)]
            if shutdown:
                queue.shutdown(wait=False, cancel_futures=True)
            else:
                for future in pending:
                    assert future.cancel()
            assert all(future.cancelled() for future in pending)
            assert all(future.cancel() for future in pending)  # No double count.
            assert queue.activity() == {
                'queued': 0, 'active': 1, 'finished': 0, 'peak_active': 1, 'cancelled': 3}
        finally:
            release.set()
        first.result(timeout=10)
    assert not called
    assert queue.activity() == {
        'queued': 0, 'active': 0, 'finished': 1, 'peak_active': 1, 'cancelled': 3}


def test_rejected_submission_does_not_leak_queue_activity():
    queue = CandidateQueue(1)
    queue.shutdown()
    with pytest.raises(RuntimeError, match="shutdown"):
        queue.submit(lambda: None)
    assert queue.activity() == {
        'queued': 0, 'active': 0, 'finished': 0, 'peak_active': 0, 'cancelled': 0}


def test_scoring_cpu_mask_keeps_generation_cpus_disjoint(tmp_path, monkeypatch):
    from tools.eval_score_job import launch

    (tmp_path / "plan.json").write_text(json.dumps({"shards": [[]]}))
    captured = {}

    def submit(directory, argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return {"state": "submitted"}

    monkeypatch.setattr("behave.runtime.jobs.submit", submit)
    ids = list(range(148, 188))
    assert launch(tmp_path, 123, 40, cpu_ids=ids, allocated_cpus=188)["state"] == "submitted"
    assert "--cpus-per-task=188" in captured["argv"]
    assert f"--cpu-bind=mask_cpu:{hex(sum(1 << cpu for cpu in ids))}" in captured["argv"]
    assert captured["metadata"]["cpu_ids"] == ids
    assert captured["metadata"]["cpus"] == 40
    assert "--gres=none" in captured["argv"]
    from tools import eval_score_job
    assert captured['cwd'] == Path(eval_score_job.__file__).resolve().parents[1]
    assert captured['wall_seconds'] is None


@pytest.mark.parametrize("ids", [[0, 0], [0], [0, -1], [0, True]])
def test_scoring_cpu_mask_rejects_invalid_budget(tmp_path, ids):
    from tools.eval_score_job import launch

    (tmp_path / "plan.json").write_text(json.dumps({"shards": [[]]}))
    with pytest.raises(ValueError, match="CPU IDs"):
        launch(tmp_path, 123, 2, cpu_ids=ids, allocated_cpus=188)


@pytest.mark.parametrize("entrypoint", ["operator", "package"])
def test_scoring_binding_overrides_single_cpu_controller(tmp_path, monkeypatch, entrypoint):
    from behave.eval import scoring as scoring_module
    from tools import eval_score_job

    (tmp_path / "plan.json").write_text(json.dumps({"shards": [[]]}))
    monkeypatch.setenv("SLURM_CPU_BIND", "quiet,mask_cpu:1")
    monkeypatch.setenv("SLURM_CPU_BIND_LIST", "1")
    calls = []
    monkeypatch.setattr("behave.runtime.jobs.submit",
                        lambda *a, **kw: calls.append((a, kw)) or {"state": "submitted"})
    launch = eval_score_job.launch if entrypoint == "operator" else scoring_module.launch
    assert launch(tmp_path, 123, 90)["state"] == "submitted"
    command = calls[0][0][1]
    assert "--cpus-per-task=90" in command
    assert [arg for arg in command if arg.startswith("--cpu-bind=")] == ["--cpu-bind=threads"]


@pytest.mark.parametrize("cpus", [0, -1, True, 1.5, "90"])
def test_scoring_rejects_invalid_cpu_count_without_mask(tmp_path, cpus):
    from behave.eval.scoring import launch

    (tmp_path / "plan.json").write_text(json.dumps({"shards": [[]]}))
    with pytest.raises(ValueError, match="scoring CPUs"):
        launch(tmp_path, 123, cpus)
