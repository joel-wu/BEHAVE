"""Resource ordering/cleanup invariants; no wall-time speedup assertions."""

import multiprocessing
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from behave.sim import api
from behave.sim import cpu_admission as admission
from behave.sim.core.failure import FailureDisposition, FailureOrigin, FailureRaised
from behave.sim.cpu_admission import _encode, _read, reserve_cpus
from behave.sim.replay_parallel import ordered_map, replay_workers
from behave.sim.search_policy import SearchBudget, SearchPolicy, SearchStopped, search_scope


def eventually(predicate):
    until = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < until, "resource transition did not complete"
        threading.Event().wait(0.005)


def test_fifty_rollouts_share_one_limit_and_keep_order():
    release = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    receipts = []

    def work(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            assert release.wait(10)
            return value * 2
        finally:
            with lock:
                active -= 1

    with api.CpuPool(8) as cpu, ThreadPoolExecutor(max_workers=50) as callers:
        def rollout(index):
            with api.cpu_scope(cpu.endpoint) as usage:
                result = ordered_map(work, range(index, index + 3), 2)
            with lock:
                receipts.extend(usage.as_dict()["reservations"])
            return result

        futures = [callers.submit(rollout, i) for i in range(50)]
        try:
            eventually(lambda: cpu.snapshot()["reserved"] == 8 and cpu.snapshot()["queued"] >= 40)
            with lock:
                assert active <= 8
        finally:
            release.set()
        assert [x.result(10) for x in futures] == [[v * 2 for v in range(i, i + 3)] for i in range(50)]
        eventually(lambda: cpu.snapshot()["reserved"] == 0)
        assert cpu.snapshot()["peak_reserved"] == 8
        assert not cpu.snapshot()["quarantined"]
    assert peak <= 8 and active == 0
    assert len(receipts) == 50
    assert all(x["released"] and x["wait_seconds"] >= 0 for x in receipts)


def test_whole_wave_fifo_no_partial_hold_or_small_request_bypass():
    release = threading.Event()
    started = threading.Event()

    with api.CpuPool(4) as cpu, ThreadPoolExecutor(max_workers=3) as callers:
        def wave(width, hold=False):
            with api.cpu_scope(cpu.endpoint), reserve_cpus(width):
                if hold:
                    assert release.wait(10)
                else:
                    started.set()

        first = callers.submit(wave, 2, True)
        try:
            eventually(lambda: cpu.snapshot()["reserved"] == 2)
            large = callers.submit(wave, 3)
            eventually(lambda: cpu.snapshot()["queued"] == 1)
            small = callers.submit(wave, 1)
            eventually(lambda: cpu.snapshot()["queued"] == 2)
            assert cpu.snapshot()["reserved"] == 2  # The 3-wide waiter holds zero.
            assert not started.is_set()  # Nor does the newcomer take the spare CPU.
        finally:
            release.set()
        for future in (first, large, small):
            future.result(10)
        assert cpu.snapshot()["peak_reserved"] <= 4


@pytest.mark.parametrize("stop", ["cancel", "deadline", "search"])
def test_wait_is_cancellable_without_launching_work_or_poisoning_pool(stop):
    cancel = threading.Event()
    release = threading.Event()
    calls = []
    with api.CpuPool(1) as cpu, ThreadPoolExecutor(max_workers=2) as holders:
        def hold():
            with api.cpu_scope(cpu.endpoint), reserve_cpus(1):
                assert release.wait(10)

        holder = holders.submit(hold)
        eventually(lambda: cpu.snapshot()["reserved"] == 1)
        kwargs = {"cancelled": cancel.is_set} if stop == "cancel" else {"deadline": time.monotonic() + 0.1}
        try:
            with api.cpu_scope(cpu.endpoint, **({} if stop == "search" else kwargs)) as usage:
                if stop == "search":
                    lease = SearchBudget(SearchPolicy(time_budget_s=0.1)).direction(1)
                    with search_scope(lease), pytest.raises(SearchStopped):
                        ordered_map(calls.append, [1], 1)
                else:
                    if stop == "cancel":
                        def cancel_waiter():
                            eventually(lambda: cpu.snapshot()["queued"] == 1)
                            cancel.set()

                        cancellation = holders.submit(cancel_waiter)
                    with pytest.raises(FailureRaised) as caught:
                        ordered_map(calls.append, [1], 1)
                    assert caught.value.failure.origin is FailureOrigin.INFRASTRUCTURE
                    assert caught.value.failure.disposition is FailureDisposition.NO_RESULT
                    if stop == "cancel":
                        cancellation.result(10)
            assert not calls
            assert usage.as_dict()["reservations"][0]["status"] != "admitted"
            eventually(lambda: cpu.snapshot()["queued"] == 0)
        finally:
            release.set()
            holder.result(10)
        with api.cpu_scope(cpu.endpoint):
            assert ordered_map(lambda x: x, [7], 1) == [7]
        assert not cpu.snapshot()["quarantined"]


def test_nested_map_reuses_one_permit_serially():
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        outer_threads = []

        def outer(value):
            outer_threads.append(threading.get_ident())
            return ordered_map(lambda x: (x, threading.get_ident()), [value, value + 1], 8)

        rows = ordered_map(outer, [2, 4], 1)
        assert rows == [[(2, outer_threads[0]), (3, outer_threads[0])],
                        [(4, outer_threads[1]), (5, outer_threads[1])]]
        assert len(usage.as_dict()["reservations"]) == 1
        assert cpu.snapshot()["peak_reserved"] == 1
        with pytest.raises(ValueError, match="enclosing"), api.cpu_scope(cpu.endpoint):
            pass


def test_callback_error_releases_only_after_other_active_job_cleanup():
    entered = threading.Barrier(2)
    cleaned = set()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        def work(value):
            try:
                entered.wait(10)
                if value == 0:
                    raise ValueError("original callback failure")
                assert cpu.snapshot()["reserved"] == 2
            finally:
                cleaned.add(value)

        with pytest.raises(ValueError, match="original callback failure"):
            ordered_map(work, [0, 1], 2)
        assert cleaned == {0, 1}
        eventually(lambda: cpu.snapshot()["reserved"] == 0)
        assert not cpu.snapshot()["quarantined"]


def _process_wave(image, active, peak, release, results):
    endpoint = api.CpuPoolEndpoint.from_dict(image)

    def work(value):
        with active.get_lock():
            active.value += 1
            peak.value = max(peak.value, active.value)
        try:
            assert release.wait(15)
            return value
        finally:
            with active.get_lock():
                active.value -= 1

    with api.cpu_scope(endpoint) as usage:
        result = ordered_map(work, [0, 1], 2)
    results.put((result, usage.as_dict()))


@pytest.mark.parametrize("start_method", ["spawn", "fork"])
def test_separate_processes_share_the_same_authority(start_method):
    if start_method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{start_method} is not available on this platform")
    ctx = multiprocessing.get_context(start_method)
    active, peak = ctx.Value("i", 0), ctx.Value("i", 0)
    release, results = ctx.Event(), ctx.Queue()
    with api.CpuPool(4) as cpu:
        processes = [ctx.Process(target=_process_wave, args=(cpu.endpoint.as_dict(), active, peak, release, results))
                     for _ in range(6)]
        try:
            for process in processes:
                process.start()
            eventually(lambda: cpu.snapshot()["reserved"] == 4 and cpu.snapshot()["queued"] >= 2)
            assert active.value <= 4
            release.set()
            for _ in processes:
                result, receipt = results.get(timeout=15)
                assert result == [0, 1]
                assert receipt["reservations"][0]["released"]
            for process in processes:
                process.join(15)
                assert process.exitcode == 0
            assert not cpu.snapshot()["quarantined"]
            assert peak.value <= 4 and active.value == 0
        finally:
            release.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(5)
            results.close()
            results.join_thread()


def test_lost_active_client_quarantines_instead_of_reusing_unclean_resources():
    with api.CpuPool(1) as cpu:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(cpu.endpoint.path)
        data = bytearray()
        try:
            sock.sendall(_encode({"token": cpu.endpoint.token, "workers": 1}))
            assert _read(sock, lambda: None, data) == {"status": "offer"}
            sock.sendall(_encode({"op": "start"}))
            assert _read(sock, lambda: None, data) == {"status": "go"}
        finally:
            sock.close()  # Deliberately no release/cleanup receipt.
        eventually(lambda: cpu.snapshot()["quarantined"])
        calls = []
        with api.cpu_scope(cpu.endpoint), pytest.raises(FailureRaised) as caught:
            ordered_map(calls.append, [0], 1)
        assert caught.value.failure.kind == "cpu-pool-quarantined"
        assert caught.value.failure.disposition is FailureDisposition.NO_RESULT
        assert calls == []


def test_unstarted_offer_can_be_abandoned_without_quarantine():
    with api.CpuPool(1) as cpu:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(cpu.endpoint.path)
            sock.sendall(_encode({"token": cpu.endpoint.token, "workers": 1}))
            assert _read(sock, lambda: None, bytearray()) == {"status": "offer"}
        eventually(lambda: cpu.snapshot()["reserved"] == 0)
        assert not cpu.snapshot()["quarantined"]
        with api.cpu_scope(cpu.endpoint):
            assert ordered_map(lambda x: x, [1], 1) == [1]


def test_actual_wave_width_respects_pool_and_auto_width_uses_its_capacity(monkeypatch):
    monkeypatch.delenv("BEHAVE_REPLAY_WORKERS", raising=False)
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint):
        assert replay_workers() == 1
        # Requested parallelism is an upper bound; a one-item wave needs one CPU.
        assert ordered_map(lambda x: x, [1], 2) == [1]
        with pytest.raises(FailureRaised) as caught:
            ordered_map(lambda x: x, [1, 2], 2)
        assert caught.value.failure.kind == "cpu-request-too-large"
        assert cpu.snapshot()["queued"] == 0


def test_closed_pool_is_no_result_and_endpoint_round_trips():
    with api.CpuPool(1) as cpu:
        endpoint = api.CpuPoolEndpoint.from_dict(cpu.endpoint.as_dict())
        assert endpoint == cpu.endpoint
        assert endpoint.token not in repr(endpoint)
    with api.cpu_scope(endpoint), pytest.raises(FailureRaised) as caught:
        ordered_map(lambda x: x, [1], 1)
    assert caught.value.failure.kind == "cpu-admission-unavailable"
    assert caught.value.failure.disposition is FailureDisposition.NO_RESULT


@pytest.mark.parametrize("phase", ["offer", "go"])
def test_cancellation_racing_grant_releases_without_starting_work(monkeypatch, phase):
    cancel = threading.Event()
    original = admission._read

    def racing_read(sock, checkpoint, data):
        reply = original(sock, checkpoint, data)
        if reply == {"status": phase}:
            cancel.set()
        return reply

    monkeypatch.setattr(admission, "_read", racing_read)
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint, cancelled=cancel.is_set) as usage:
        calls = []
        with pytest.raises(FailureRaised) as caught:
            ordered_map(calls.append, [1], 1)
        assert caught.value.failure.kind == "cpu-wait-cancelled"
        assert not calls
        eventually(lambda: cpu.snapshot()["reserved"] == 0)
        assert not cpu.snapshot()["quarantined"]
        assert usage.as_dict()["reservations"][0]["released"]


def test_cancel_start_messages_may_coalesce_on_stream_socket():
    with api.CpuPool(1) as cpu, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        sock.connect(cpu.endpoint.path)
        data = bytearray()
        sock.sendall(_encode({"token": cpu.endpoint.token, "workers": 1}))
        assert _read(sock, lambda: None, data) == {"status": "offer"}
        sock.sendall(_encode({"op": "start"}) + _encode({"op": "release"}))
        assert _read(sock, lambda: None, data) == {"status": "go"}
        assert _read(sock, lambda: None, data) == {"status": "released"}
        assert not cpu.snapshot()["quarantined"]


def test_interrupted_worker_join_does_not_release_uncertain_permits(monkeypatch):
    original = ThreadPoolExecutor.shutdown

    def interrupted_join(pool, *args, **kwargs):
        original(pool, *args, **kwargs)  # No real leaked thread in this regression.
        raise KeyboardInterrupt("worker join was interrupted")

    monkeypatch.setattr(ThreadPoolExecutor, "shutdown", interrupted_join)
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        with pytest.raises(KeyboardInterrupt, match="join was interrupted"):
            ordered_map(lambda x: x, [1, 2], 2)
        eventually(lambda: cpu.snapshot()["quarantined"])
        receipt = usage.as_dict()["reservations"][0]
        assert receipt["status"] == "cpu-cleanup-unconfirmed"
        assert not receipt["released"]


@pytest.mark.parametrize("values", [[1], [1, 2]])
def test_unconfirmed_cleanup_stops_dispatch_even_if_worker_returns_a_value(values):
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        calls = []

        def work(value):
            calls.append(value)
            admission.cpu_cleanup_unconfirmed()
            return value

        with pytest.raises(FailureRaised) as caught:
            ordered_map(work, values, 1)
        assert caught.value.failure.kind == "cpu-cleanup-unconfirmed"
        assert calls == [1]
        eventually(lambda: cpu.snapshot()["quarantined"])
        assert not usage.as_dict()["reservations"][0]["released"]


def _inherited_client_descriptors(sockets, result):
    result.send([sock.fileno() for sock in sockets])
    result.close()


def test_forked_descendant_cannot_keep_its_parent_permit_connection_alive():
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("fork is not available on this platform")
    ctx = multiprocessing.get_context("fork")
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint), reserve_cpus(1):
        sockets = tuple(admission._CLIENT_SOCKETS)
        assert len(sockets) == 1
        reader, writer = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_inherited_client_descriptors, args=(sockets, writer))
        try:
            process.start()
            writer.close()
            assert reader.poll(10)
            assert reader.recv() == [-1]
            process.join(10)
            assert process.exitcode == 0
            assert all(sock.fileno() >= 0 for sock in sockets)
            assert not cpu.snapshot()["quarantined"]
        finally:
            if process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(5)
            reader.close()
            writer.close()
