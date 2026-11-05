"""The CPU carrier changes; complete trace, monitor and failure evidence do not."""

import json
import pickle

import pytest

from behave.sim import api
from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING, HitVectorCodec
from behave.sim.cpu_admission import reserve_cpus
from behave.sim.replay_parallel import _ordered_map
from behave.sim.rtl.execution_policy import execution_policy
from behave.sim.rtl.trace_decoder import TraceDecoder
from behave.sim.rtl.trace_postprocessing import MonitorPlan, TracePlan, TracePostprocessor
from behave.sim.run_assessment import CheckedRun, RunAssessment
from tests.sim.test_api_path import Doubling, FirstOnlyDoubling, contract, realization
from tests.support.device_boundary import run_device


def fixture(*, incomplete=False):
    c = contract(answers=1, quiescent=True)
    task = api.admit(c, realization())
    s = api.reset_then(c, [(3,), (9,)], drain=6, controls={"out_ready": 1})
    device = FirstOnlyDoubling() if incomplete else Doubling()
    trace = run_device(task, s, device)
    return packet(c, s, trace)


def packet(c, s, trace):
    codec = HitVectorCodec(())
    rows = [record.as_dict() for record in trace.records]
    for row in rows:
        row["hits"] = codec.vector(()).as_compact_dict()
    raw = {"records": rows, "structural_refusals": {},
           "hit_encoding": HIT_VECTOR_ENCODING, "hit_catalogue": [],
           "execution_policy": execution_policy(native_events=False)}
    if len(c.clocks) > 1:
        from behave.sim.rtl.replay_plan import ATOMIC_TRACE_VERSION

        raw.update(version=ATOMIC_TRACE_VERSION, artifact=trace.artifact)
    plan = TracePlan(TraceDecoder(api.CatalogueManifest(()), {}), c, trace.artifact)
    return plan, s, {"traces": [raw]}


def semantic(checked):
    if type(checked) is RunAssessment:
        result = checked.result
        return (result.run_failure.as_dict() if result.run_failure is not None else
                result.comparison_failure.as_dict())
    assert type(checked) is CheckedRun
    report = checked.report
    return checked.trace.as_dict(), checked.hits, {
        name: [item.as_dict() for item in getattr(report, name)] for name in (
            "hits", "violations", "assumption_violations", "unresolved")}, (
                report.opportunities, report.unchecked)


@pytest.mark.parametrize("monitor", [None, MonitorPlan()])
@pytest.mark.parametrize("payload", [b'{"traces":[]}', b'not-json'])
def test_failed_trace_keeps_cocotb_evidence_without_changing_attribution(monkeypatch, monitor, payload):
    from types import SimpleNamespace

    from behave.sim.rtl import tool_protocol
    from behave.sim.rtl.trace_postprocessing import RawTrace

    plan, stimulus, _ = fixture()
    report = b'<testsuite><testcase><failure message="driver error"/></testcase></testsuite>'
    execution = SimpleNamespace(outputs={"results.xml": report}, as_dict=lambda: {"tool": "test"})
    monkeypatch.setattr(tool_protocol, "command_stream",
                        lambda actual, command, stream: ("complete " + stream).encode())
    with TracePostprocessor(plan, monitor, 1) as checker:
        original = checker.decode(stimulus, payload, execution.as_dict(), 3)
        got = RawTrace(payload, execution).decode(checker, stimulus, 3)
    before = original if type(original) is api.RunFailure else original.result.run_failure
    after = got if type(got) is api.RunFailure else got.result.run_failure
    assert (after.kind, after.origin, after.disposition, after.observed) == (
        before.kind, before.origin, before.disposition, before.observed)
    assert not after.determines_score
    assert dict(after.reason.detail["simulator_diagnostics"]) == {
        "cocotb_results": report.decode(), "stdout": "complete stdout", "stderr": "complete stderr"}
    if type(got) is RunAssessment:
        assert (got.hits, got.report) == (original.hits, original.report)
        assert [name for name, _ in got.costs] == [name for name, _ in original.costs]
        assert dict(got.costs)["replay"] >= 3


def test_valid_trace_does_not_load_command_logs(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.rtl import tool_protocol
    from behave.sim.rtl.trace_postprocessing import RawTrace

    plan, stimulus, document = fixture()
    execution = SimpleNamespace(outputs={}, as_dict=dict)
    monkeypatch.setattr(tool_protocol, "command_stream", lambda *_: pytest.fail("unexpected log read"))
    with TracePostprocessor(plan, MonitorPlan(), 1) as checker:
        got = RawTrace(json.dumps(document).encode(), execution).decode(checker, stimulus, 0)
    assert type(got) is CheckedRun


def test_missing_diagnostic_log_does_not_replace_trace_failure(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.rtl import tool_protocol
    from behave.sim.rtl.trace_postprocessing import RawTrace

    def missing(*_):
        raise FileNotFoundError("removed log")

    plan, stimulus, _ = fixture()
    execution = SimpleNamespace(outputs={}, as_dict=dict)
    monkeypatch.setattr(tool_protocol, "command_stream", missing)
    with TracePostprocessor(plan, None, 1) as checker:
        got = RawTrace(b'{"traces":[]}', execution).decode(checker, stimulus, 0)
    assert got.kind == "rtl-artifact-no-trace"
    assert not got.determines_score
    assert "FileNotFoundError" in got.reason.detail["simulator_diagnostics"]["stdout_unavailable"]


@pytest.mark.parametrize("incomplete", [False, True])
def test_spawn_decode_and_monitor_match_serial_complete_evidence(incomplete, monkeypatch):
    plan, stimulus, document = fixture(incomplete=incomplete)
    payload = json.dumps(document).encode()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint) as usage, reserve_cpus(2):
        with TracePostprocessor(plan, MonitorPlan(), 1) as serial:
            expected = serial.decode(stimulus, payload, {}, 0)
        assert type(expected) is CheckedRun
        assert bool(expected.report.violations) is incomplete
        with TracePostprocessor(plan, MonitorPlan(), 2) as parallel:
            results = _ordered_map(lambda _: parallel.decode(stimulus, payload, {}, 0), range(5), 2)
            assert all(semantic(result) == semantic(expected) for result in results)
            # No bulk trace serialization may be deferred into the coordinator's
            # comparison/settlement stage after the supervised worker returns.
            from behave.sim.core import stimulus as stimulus_module

            identity = expected.trace.fingerprint
            monkeypatch.setattr(stimulus_module, "_digest_json_projection",
                lambda *a, **kw: pytest.fail("coordinator re-serialized checked trace"))
            assert all(result.trace.fingerprint == identity for result in results)
            # Transport retains immutable records, including cached identities.
            with pytest.raises(AttributeError):
                results[0].trace.records[0].outputs = ()
            assert cpu.snapshot()["reserved"] == 2
        assert len(usage.as_dict()["reservations"]) == 0  # receipt follows release
    assert len(usage.as_dict()["reservations"]) == 1
    assert all(row["released"] for row in usage.as_dict()["reservations"])


@pytest.mark.parametrize("corrupt", ["extra", "missing", "policy", "slots", "catalogue", "pickle"])
def test_untrusted_packets_keep_same_refusal_and_never_become_pickle(corrupt):
    plan, stimulus, document = fixture()
    if corrupt == "extra":
        document["surprise"] = 1
    elif corrupt == "missing":
        document["traces"] = []
    elif corrupt == "policy":
        document["traces"][0]["execution_policy"] = {}
    elif corrupt == "slots":
        document["traces"][0]["records"].pop()
    elif corrupt == "catalogue":
        document["traces"][0]["hit_catalogue"] = ["spurious"]
    payload = pickle.dumps(document) if corrupt == "pickle" else json.dumps(document).encode()
    with reserve_cpus(2), TracePostprocessor(plan, MonitorPlan(), 2) as parallel:
        result = parallel.decode(stimulus, payload, {"tool": "test"}, 0)
    with TracePostprocessor(plan, MonitorPlan(), 1) as serial:
        expected = serial.decode(stimulus, payload, {"tool": "test"}, 0)
    assert type(result) is RunAssessment
    assert not result.result.determines_score
    assert semantic(result) == semantic(expected)


def test_broken_worker_is_no_result_without_serial_retry():
    plan, stimulus, document = fixture()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint), reserve_cpus(2):
        with TracePostprocessor(plan, MonitorPlan(), 2) as parallel:
            completed = parallel.decode(stimulus, json.dumps(document).encode(), {}, 0)
            assert type(completed) is CheckedRun
            child = parallel.pool._workers[0].process
            child.kill()
            child.wait(timeout=10)
            failure = parallel.decode(stimulus, json.dumps(document).encode(), {}, 0)
            assert type(failure) is api.RunFailure
            assert failure.kind == "rtl-trace-worker-failed"
            assert not failure.determines_score
            assert completed.trace.stimulus.fingerprint == stimulus.fingerprint
        assert not cpu.snapshot()["quarantined"]  # all CPU-only workers joined


def test_nested_admission_uses_inline_check_not_another_process_pool(monkeypatch):
    from behave.sim.rtl import trace_postprocessing as module

    plan, stimulus, document = fixture()
    monkeypatch.setattr(module, "TraceWorkerPool", lambda *_: pytest.fail("nested pool"))
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint), reserve_cpus(1), reserve_cpus(8) as width:
        assert width == 1
        with TracePostprocessor(plan, MonitorPlan(), width) as checker:
            assert type(checker.decode(stimulus, json.dumps(document).encode(), {}, 0)) is CheckedRun


def test_single_cpu_budget_cancels_inflight_monitor_and_preserves_prior_result():
    import os
    import signal
    import time

    plan, stimulus, document = fixture()
    payload = json.dumps(document).encode()
    with (api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint), reserve_cpus(1),
          api.scoring_scope(api.ScoringPolicy()) as budget):
        with TracePostprocessor(plan, MonitorPlan(), 1) as checker:
            first = checker.decode(stimulus, payload, {}, 0)
            assert type(first) is CheckedRun
            assert cpu.snapshot()["reserved"] == 1
            child = checker.pool._workers[0].process
            os.kill(child.pid, signal.SIGSTOP)
            budget.deadline = time.monotonic() + 0.1
            with pytest.raises(api.ScoringStopped):
                checker.decode(stimulus, payload, {}, 0)
            # Another already-running replay may next acquire the interrupted
            # lane. Its budget stop must not become an infrastructure failure.
            with pytest.raises(api.ScoringStopped):
                checker.decode(stimulus, payload, {}, 0)
        assert child.poll() is not None
        assert budget.cleanup_confirmed
        assert not cpu.snapshot()["quarantined"]
        assert first.trace.stimulus.fingerprint == stimulus.fingerprint


def test_postprocessor_shutdown_failure_quarantines_cpu_wave(monkeypatch):
    from behave.sim.rtl import trace_postprocessing as module
    from tests.sim.test_cpu_admission import eventually

    class LostCleanup:
        def close(self):
            raise RuntimeError("lost cleanup")

    monkeypatch.setattr(module, "TraceWorkerPool", lambda *_: LostCleanup())
    plan, _, _ = fixture()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        with pytest.raises(RuntimeError, match="lost cleanup"), reserve_cpus(2), (
            TracePostprocessor(plan, MonitorPlan(), 2)
        ):
            pass
        eventually(lambda: cpu.snapshot()["quarantined"])


@pytest.mark.parametrize("kind", ["plain", "valid-only", "clockless", "atomic"])
def test_same_process_stage_supports_other_clock_and_sampling_forms(kind):
    from behave.sim.core.atomic import run_atomic_in_memory
    from behave.sim.core.driver import run_in_memory
    from behave.sim.stimulus_set import multiclock_stimulus_from_witness
    from tests.sim.test_multiclock_authority import _AtomicDevice, _witness
    from tests.sim.test_multiclock_authority import _contract as atomic_contract
    from tests.sim.test_sampled_protocol import _contract, _Doubling
    from tests.sim.test_settled_protocol import contract as clockless_contract

    if kind == "atomic":
        c = atomic_contract()
        s = multiclock_stimulus_from_witness(c, _witness())
        trace = run_atomic_in_memory(c, s, _AtomicDevice())
    else:
        c = (clockless_contract() if kind == "clockless" else _contract(
            input_valid=kind == "valid-only", output_valid=kind == "valid-only"))
        s = api.reset_then(c, [(3,), (3,), (5,)], drain=0)
        trace = run_in_memory(c, s, _Doubling())
    plan, s, document = packet(c, s, trace)
    body = json.dumps(document).encode()
    with TracePostprocessor(plan, MonitorPlan(), 1) as serial:
        expected = serial.decode(s, body, {}, 0)
    assert type(expected) is CheckedRun
    with reserve_cpus(2), TracePostprocessor(plan, MonitorPlan(), 2) as parallel:
        got = parallel.decode(s, body, {}, 0)
    assert semantic(expected) == semantic(got)


@pytest.mark.parametrize("structural", [False, True])
def test_spawn_preserves_values_and_rejects_unwitnessed_structural_hits(structural):
    from dataclasses import replace

    plan, s, document = fixture()
    raw = document["traces"][0]
    if structural:
        catalogue = api.CatalogueManifest([api.Goal("covered", "coverage"),
                                          api.Goal("unreadable", "coverage")])
        plan = replace(plan, decoder=TraceDecoder(catalogue, {}))
        codec = HitVectorCodec(tuple(sorted(catalogue)))
        raw["hit_catalogue"] = list(codec.catalogue)
        raw["structural_refusals"] = {"unreadable": {"kind": "test-unreadable"}}
    for index, row in enumerate(raw["records"]):
        if structural:
            row["hits"] = codec.vector(("covered",) if index % 2 else (), ("unreadable",)).as_compact_dict()
        row["state"].append(["recursive", {"bool": True, "int": 1, "items": [[3], []]}])
    body = json.dumps(document).encode()
    with TracePostprocessor(plan, MonitorPlan(), 1) as serial:
        expected = serial.decode(s, body, {}, 0)
    if structural:
        assert type(expected) is RunAssessment
        assert expected.result.run_failure.kind == "rtl-native-evidence-required"
        assert not expected.result.determines_score
    else:
        assert type(expected) is CheckedRun
    with reserve_cpus(2), TracePostprocessor(plan, MonitorPlan(), 2) as parallel:
        got = parallel.decode(s, body, {}, 0)
    assert semantic(expected) == semantic(got)


def test_worker_does_not_import_or_reexecute_an_unguarded_caller(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    marker = tmp_path / "caller-runs.txt"
    script = tmp_path / "caller.py"
    script.write_text(f"""
from pathlib import Path
import json
from tests.sim.test_trace_postprocessing import fixture
from behave.sim.rtl.trace_postprocessing import TracePostprocessor, MonitorPlan
from behave.sim.run_assessment import CheckedRun
with Path({str(marker)!r}).open('a') as stream:
    stream.write('caller\\n')
plan, stimulus, document = fixture()
with TracePostprocessor(plan, MonitorPlan(), 2) as checker:
    for _ in range(2):
        result = checker.decode(stimulus, json.dumps(document).encode(), {{}}, 0)
        assert type(result) is CheckedRun, result
    assert len(checker.pool._workers) == 1  # reuse an idle lane's interpreter
""")
    result = subprocess.run([sys.executable, str(script)], cwd=root,
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(
                                map(str, (root / "src", root)))),
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "caller\n"


def test_cancellation_before_next_check_sends_no_more_work():
    import threading

    plan, stimulus, document = fixture()
    cancelled = threading.Event()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint, cancelled=cancelled.is_set):
        with reserve_cpus(2), TracePostprocessor(plan, MonitorPlan(), 2) as checker:
            assert type(checker.decode(stimulus, json.dumps(document).encode(), {}, 0)) is CheckedRun
            cancelled.set()
            with pytest.raises(api.FailureRaised) as stopped:
                checker.decode(stimulus, json.dumps(document).encode(), {}, 0)
            assert stopped.value.failure.kind == "cpu-wait-cancelled"
            assert len(checker.pool._workers) == 1
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]


def test_cancellation_during_large_send_discards_frame_and_reaps_worker():
    import os
    import signal
    import threading

    from behave.sim.rtl import trace_process

    plan, stimulus, document = fixture()
    payload = json.dumps(document).encode()
    cancelled = threading.Event()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint, cancelled=cancelled.is_set) as usage:
        with reserve_cpus(2), TracePostprocessor(plan, MonitorPlan(), 2) as checker:
            assert type(checker.decode(stimulus, payload, {}, 0)) is CheckedRun
            worker = checker.pool._workers[0]
            os.kill(worker.process.pid, signal.SIGSTOP)
            checks = 0

            def cancel_in_io():
                nonlocal checks
                checks += 1
                if checks >= 4:
                    cancelled.set()
                trace_process._check_control()

            worker.connection.check = cancel_in_io
            try:
                with pytest.raises(api.FailureRaised) as stopped:
                    checker.decode(stimulus, b" " * (8 << 20)+payload, {}, 0)
                assert stopped.value.failure.kind == "cpu-wait-cancelled"
                assert not stopped.value.failure.determines_score
                # Even if a consumer catches cancellation, never send another
                # request through a channel containing an interrupted frame.
                cancelled.clear()
                with pytest.raises(api.FailureRaised) as again:
                    checker.decode(stimulus, payload, {}, 0)
                assert again.value.failure == stopped.value.failure
                assert not again.value.failure.determines_score
                assert len(checker.pool._workers) == 1
            finally:
                os.kill(worker.process.pid, signal.SIGCONT)
        assert worker.process.poll() is not None
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]
    assert all(row["released"] for row in usage.as_dict()["reservations"])


def test_forty_rollouts_queue_before_starting_cpu_workers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock

    from behave.sim.rtl import trace_process
    from tests.sim.test_cpu_admission import eventually

    plan, stimulus, document = fixture()
    body = json.dumps(document).encode()
    with TracePostprocessor(plan, MonitorPlan(), 1) as serial:
        expected = semantic(serial.decode(stimulus, body, {}, 0))
    lock = Lock()
    live, peak, started = set(), 0, 0
    initialize, reap = trace_process._Worker.__init__, trace_process._Worker.reap

    def tracked_initialize(worker, *args):
        nonlocal peak, started
        initialize(worker, *args)
        with lock:
            started += 1
            live.add(worker.process.pid)
            peak = max(peak, len(live))

    def tracked_reap(worker, deadline):
        reap(worker, deadline)
        if worker.process is not None:
            assert worker.process.poll() is not None  # reap precedes permit release
            with lock:
                live.discard(worker.process.pid)

    monkeypatch.setattr(trace_process._Worker, "__init__", tracked_initialize)
    monkeypatch.setattr(trace_process._Worker, "reap", tracked_reap)
    with api.CpuPool(8) as cpu, ThreadPoolExecutor(max_workers=40) as callers:
        def rollout():
            with (api.cpu_scope(cpu.endpoint) as usage, reserve_cpus(2) as width,
                  TracePostprocessor(plan, MonitorPlan(), width) as checker):
                results = _ordered_map(
                    lambda _: checker.decode(stimulus, body, {}, 0), range(3), width)
            assert all(semantic(result) == expected for result in results)
            receipts = usage.as_dict()["reservations"]
            assert len(receipts) == 1  # decode/monitor never reserves a nested wave
            assert receipts[0]["workers"] == 2 and receipts[0]["released"]

        with api.cpu_scope(cpu.endpoint), reserve_cpus(8):
            futures = [callers.submit(rollout) for _ in range(40)]
            eventually(lambda: cpu.snapshot()["queued"] == 40)
            with lock:
                assert started == 0  # waiters have not launched checking processes
        for future in futures:
            future.result(timeout=120)
        assert cpu.snapshot()["reserved"] == 0
        assert cpu.snapshot()["peak_reserved"] == 8
        assert not cpu.snapshot()["quarantined"]
    assert 0 < peak <= 8 and not live
