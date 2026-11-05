"""Development EDA uses one coordinator pool and a durable invocation identity."""

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event as Signal

import pytest
from test_cocotb_development import POLICY, workspace
from test_model_relay import wait_for

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.lifetime import AttemptControl, ExecutionWindow, current_window, execution_window
from behave.agent.local_rpc import exchange, send_frame
from behave.agent.model import ToolCall
from behave.agent.tools import ToolUnavailable, current_invocation, tool_invocation
from behave.core.artifacts import BlobRef, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json
from behave.task_execution.cocotb_worker import CocotbRun
from behave.task_execution.development import SCHEMA, CocotbRelayClient, DevelopmentRelay, job_from_snapshot
from behave.task_execution.tb_report import TBObservation


def fixture(tmp_path, *, pool=None):
    work, args = workspace(tmp_path)
    solver = AttemptJournal(tmp_path / "solver", work.store)
    journal = AttemptJournal(tmp_path / "development", work.store)
    origin, closure = work.checkpoint()
    call = ToolCall("tb-1", "run_cocotb", args)
    event = solver.append((), "tool_requested", {"turn": 0, "call": call.as_dict(), "before": origin.as_dict()}, closure)
    job = job_from_snapshot(origin, args, POLICY, work.store)
    packet = canonical_json({"schema": SCHEMA, "invocation": work.store.put(event.to_bytes()).as_dict(),
                             "job": work.store.put(job.to_bytes()).as_dict()})
    configuration = {"spec": AttemptSpec("solver", "solver", {"model": "fixture"}), "policy": POLICY,
                     "solver": solver, "journal": journal, "pool": pool or ResourcePool(AdmissionPolicy("eda", 1)),
                     "deadline": time.monotonic() + 30}
    return configuration, event, job, packet


def decode(configuration, response):
    ref = BlobRef.from_dict(parse_json(response)["result"])
    return CocotbRun.from_dict(parse_json(read_verified(configuration["journal"].objects, ref)))


def client(relay, event, job, *, cancelled=lambda: False):
    window = ExecutionWindow(AttemptControl(cancelled=cancelled), time.time() + 25, time.monotonic() + 25)
    with tool_invocation(event), execution_window(window):
        return CocotbRelayClient(relay.endpoint)(job, relay.objects)


@pytest.mark.isolation
def test_development_receipt_is_idempotent_bound_and_recoverable(tmp_path, monkeypatch):
    configuration, event, job, packet = fixture(tmp_path)
    calls = []
    def execute(job, objects):
        calls.append(job)
        console = objects.put(b"candidate assertion failed")
        return CocotbRun(TBObservation("failed", "tb-execution-failed", "failed", evidence=(console,)), console)
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb", execute)
    relay = DevelopmentRelay(**configuration)
    with relay:
        got = client(relay, event, job)
        assert got.observation.status == "failed"
        assert client(relay, event, job) == got
        # No private 4 KiB relay cap on an otherwise valid request.
        padded = exchange(relay.endpoint, packet + b" " * 5000,
                          deadline=time.monotonic() + 5)
        assert decode(configuration, padded) == got
    assert current_invocation() is None and current_window() is None
    assert configuration["pool"].counts == (0, 0) and len(calls) == 1
    events = configuration["journal"].load()
    assert [e.kind for e in events] == ["development_initialized", "development_queued",
                                       "development_requested", "development_settled"]
    assert got.console in events[-1].artifacts
    reopened = DevelopmentRelay(**configuration)
    assert decode(configuration, reopened.respond(packet, cancelled=lambda: False)) == got
    assert len(calls) == 1
    changed = {**parse_json(packet), "job": relay.objects.put(replace(job, policy=replace(POLICY, seed=2)).to_bytes()).as_dict()}
    with pytest.raises(RecordError) as error:
        reopened.respond(canonical_json(changed), cancelled=lambda: False)
    assert error.value.code == "development-request-conflict"


@pytest.mark.parametrize("mutation", ["policy", "snapshot", "foreign", "settled"])
def test_development_rejects_unbound_or_modified_inputs_before_dispatch(tmp_path, monkeypatch, mutation):
    configuration, event, job, packet = fixture(tmp_path)
    objects, solver = configuration["journal"].objects, configuration["solver"]
    if mutation == "policy":
        job = replace(job, policy=replace(POLICY, seed=2))
    elif mutation == "snapshot":
        job = replace(job, origin=objects.put(b"different source selection"))
    elif mutation == "foreign":
        event = replace(event, payload={**dict(event.payload), "turn": 3})
    else:
        solver.append(solver.load(), "tool_settled", {})
    packet = canonical_json({"schema": SCHEMA, "invocation": objects.put(event.to_bytes()).as_dict(),
                             "job": objects.put(job.to_bytes()).as_dict()})
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb",
                        lambda *a: pytest.fail("invalid request must not execute"))
    relay = DevelopmentRelay(**configuration)
    with pytest.raises(RecordError) as error:
        relay.respond(packet, cancelled=lambda: False)
    assert error.value.code == ("development-input-mismatch" if mutation in {"policy", "snapshot"}
                                else "development-invocation-unbound")
    assert len(configuration["journal"].load()) == 1


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_development_unknown_never_repeats_after_crash(tmp_path, monkeypatch, after_dispatch):
    configuration, _, _, packet = fixture(tmp_path)
    class Crash(BaseException):
        pass
    from behave.agent.replay import Tape
    original = Tape.record
    def record(self, kind, *a, **kw):
        if not after_dispatch and kind == "development_requested":
            raise Crash()
        return original(self, kind, *a, **kw)
    monkeypatch.setattr(Tape, "record", record)
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb",
                        lambda *a: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        DevelopmentRelay(**configuration).respond(packet, cancelled=lambda: False)
    monkeypatch.setattr(Tape, "record", original)
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb",
                        lambda *a: pytest.fail("unknown work cannot dispatch again"))
    configuration["pool"] = ResourcePool(configuration["pool"].policy)
    reopened = DevelopmentRelay(**configuration)
    result = decode(configuration, reopened.respond(packet, cancelled=lambda: False))
    assert result.observation.code == "development-outcome-unknown"
    assert result.observation.status == "unavailable"
    assert configuration["journal"].load()[-1].kind == (
        "development_requested" if after_dispatch else "development_queued")
    assert configuration["pool"].counts == (0, 0)
    expected = CleanupState.UNKNOWN if after_dispatch else CleanupState.COMPLETE
    assert result.observation.cleanup is reopened.child_cleanup is expected
    if after_dispatch:
        with pytest.raises(AttemptInterrupted, match="resource-cleanup-unknown"), configuration["pool"].acquire():
            pytest.fail("restart cannot free resources without a child completion receipt")


def test_post_dispatch_interruption_is_not_a_completed_admission_decline(tmp_path, monkeypatch):
    configuration, _, _, packet = fixture(tmp_path)

    def interrupted(*args):
        raise AttemptInterrupted("execution-interrupted")

    monkeypatch.setattr("behave.task_execution.development.execute_cocotb", interrupted)
    relay = DevelopmentRelay(**configuration)
    with pytest.raises(AttemptInterrupted, match="execution-interrupted"):
        relay.respond(packet, cancelled=lambda: False)
    assert [event.kind for event in configuration["journal"].load()] == [
        "development_initialized", "development_queued", "development_requested"]
    assert relay.child_cleanup is CleanupState.UNKNOWN
    with pytest.raises(AttemptInterrupted, match="resource-cleanup-unknown"), configuration["pool"].acquire():
        pytest.fail("interrupted execution cannot certify child exit")


@pytest.mark.parametrize("cleanup", [CleanupState.COMPLETE, CleanupState.INCOMPLETE, CleanupState.UNKNOWN])
def test_recovered_result_preserves_child_cleanup_and_shared_pool(tmp_path, monkeypatch, cleanup):
    configuration, _, _, packet = fixture(tmp_path)
    result = CocotbRun(TBObservation("unavailable", "service-result", cleanup=cleanup))
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb", lambda *a: result)
    first = DevelopmentRelay(**configuration)
    assert decode(configuration, first.respond(packet, cancelled=lambda: False)) == result
    configuration["pool"] = ResourcePool(configuration["pool"].policy)
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb",
                        lambda *a: pytest.fail("stored result must not execute again"))
    reopened = DevelopmentRelay(**configuration)
    assert reopened.child_cleanup is cleanup
    assert decode(configuration, reopened.respond(packet, cancelled=lambda: False)) == result
    if cleanup.quiescent:
        with configuration["pool"].acquire():
            assert configuration["pool"].counts == (1, 0)
    else:
        with pytest.raises(AttemptInterrupted, match="resource-cleanup-"), configuration["pool"].acquire():
            pytest.fail("a restored unresolved child must quarantine shared admission")


def test_executor_error_keeps_traceback_without_claiming_cleanup(tmp_path, monkeypatch, capsys):
    configuration, _, _, packet = fixture(tmp_path)

    def failing_executor(*args):
        raise RuntimeError("fixture executor diagnostic")

    monkeypatch.setattr("behave.task_execution.development.execute_cocotb", failing_executor)
    relay = DevelopmentRelay(**configuration)
    result = decode(configuration, relay.respond(packet, cancelled=lambda: False))
    assert result.observation.code == "development-service-failed"
    assert result.observation.cleanup is CleanupState.UNKNOWN
    stderr = capsys.readouterr().err
    assert "failing_executor" in stderr and "RuntimeError: fixture executor diagnostic" in stderr
    assert "fixture executor diagnostic" not in repr(result.as_dict())


def test_relay_reuses_worker_validated_result_without_another_byte_budget(tmp_path, monkeypatch):
    from behave.task_execution import development as development_module

    configuration, event, job, _ = fixture(tmp_path)
    console = configuration["journal"].objects.put(b"worker validated console")
    result = CocotbRun(TBObservation("completed", "tb-completed", "passed", evidence=(console,)), console)
    monkeypatch.setattr(development_module, "execute_cocotb", lambda *a: result)
    relay = DevelopmentRelay(**configuration)
    assert not hasattr(relay, "request_bytes") and not hasattr(relay, "response_bytes")
    relay.endpoint = "fixture"
    reads = []

    def record_read(store, ref):
        reads.append(ref)
        return read_verified(store, ref)

    def respond(endpoint, packet, **kwargs):
        assert endpoint == "fixture" and "response_bytes" not in kwargs
        response = relay.respond(packet, cancelled=lambda: False)
        reads.clear()  # Only count reads by the client after the worker returns.
        return response

    monkeypatch.setattr(development_module, "read_verified", record_read)
    monkeypatch.setattr(development_module, "exchange", respond)
    assert client(relay, event, job) == result
    assert len(reads) == 1 and reads[0] != console


@pytest.mark.isolation
def test_development_shared_pool_and_cancelled_waiter_do_not_dispatch_twice(tmp_path, monkeypatch):
    pool = ResourcePool(AdmissionPolicy("shared-eda", 1))
    first, event1, job1, _ = fixture(tmp_path / "a", pool=pool)
    second, event2, job2, _ = fixture(tmp_path / "b", pool=pool)
    reached, release, cancelled = Signal(), Signal(), Signal()
    calls = []
    def execute(job, objects):
        calls.append(job)
        reached.set()
        assert release.wait(10)
        return CocotbRun(TBObservation("completed", "fixture", "passed"))
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb", execute)
    with DevelopmentRelay(**first) as a, DevelopmentRelay(**second) as b, ThreadPoolExecutor(max_workers=2) as workers:
        one = workers.submit(client, a, event1, job1)
        try:
            assert reached.wait(5)
            two = workers.submit(client, b, event2, job2, cancelled=cancelled.is_set)
            wait_for(lambda: pool.counts == (1, 1))
            cancelled.set()
            with pytest.raises(ToolUnavailable):
                two.result(5)
            wait_for(lambda: pool.counts == (1, 0))
            assert len(calls) == 1
        finally:
            release.set()
        assert one.result(5).observation.comparison == "passed"
    assert pool.counts == (0, 0)
    assert [e.kind for e in second["journal"].load()] == [
        "development_initialized", "development_queued", "development_declined"]


@pytest.mark.isolation
def test_development_disconnect_cancels_running_service_and_keeps_its_receipt(tmp_path, monkeypatch):
    configuration, _, _, packet = fixture(tmp_path)
    reached = Signal()
    def execute(job, objects):
        reached.set()
        wait_for(current_window().control.cancelled)
        evidence = objects.put(b"cancelled simulator tree was reaped")
        return CocotbRun(TBObservation("unavailable", "testbench-worker-cancelled", evidence=(evidence,)))
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb", execute)
    with DevelopmentRelay(**configuration) as relay:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(relay.endpoint)
            send_frame(stream, packet, deadline=time.monotonic() + 5)
            assert reached.wait(5)
        wait_for(lambda: configuration["journal"].load()[-1].kind == "development_settled")
    event = configuration["journal"].load()[-1]
    result = CocotbRun.from_dict(parse_json(read_verified(relay.objects, BlobRef.from_dict(dict(event.payload["result"])))))
    assert result.observation.code == "testbench-worker-cancelled"
    assert result.observation.evidence[0] in event.artifacts
    assert not relay._thread.is_alive() and not relay._listener.fileno() >= 0
    assert configuration["pool"].counts == (0, 0)


def test_development_store_failure_is_not_a_settled_execution(tmp_path, monkeypatch):
    configuration, _, _, packet = fixture(tmp_path)
    def fail(*args):
        raise RecordError("store-broken", "lost storage")
    monkeypatch.setattr("behave.task_execution.development.execute_cocotb", fail)
    with pytest.raises(RecordError, match="lost storage"):
        DevelopmentRelay(**configuration).respond(packet, cancelled=lambda: False)
    assert configuration["journal"].load()[-1].kind == "development_requested"
    assert configuration["pool"].counts == (0, 0)


@pytest.mark.isolation
def test_service_ledger_failure_stops_rpc_owner_not_just_connection(tmp_path, monkeypatch):
    configuration, event, job, _ = fixture(tmp_path)
    def fail(*args):
        raise RecordError('store-broken', 'cannot settle this dispatch')
    monkeypatch.setattr('behave.task_execution.development.execute_cocotb', fail)
    relay = DevelopmentRelay(**configuration)
    # A dispatch raised without returning its lifetime receipt. A stopped relay
    # thread cannot certify the potentially still-running EDA service.
    with pytest.raises(RecordError, match='not quiescent'), relay, pytest.raises(ToolUnavailable):
        client(relay, event, job)
    assert not relay._thread.is_alive() and configuration['pool'].counts == (0, 0)
    assert configuration['journal'].load()[-1].kind == 'development_requested'


# Solver no longer has a cocotb relay. Real shell repair/submission is covered
# by test_eval_shell_environment; construction relay integration is covered
# by test_role_process and test_synthesis_process.
