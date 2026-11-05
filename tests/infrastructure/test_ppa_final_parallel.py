"""Candidate-level final admission, single-writer settlement and cold recovery."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier, Lock, current_thread

import pytest
from test_ppa import RTL, FakeNative, experiment, fixture_task

from behave.agent.attempt import AttemptSpec
from behave.agent.cpu_resources import CpuResources, cpu_job
from behave.agent.model import ModelReply, ToolCall
from behave.agent.run_store import JournalDirectory
from behave.core.lifecycle import CleanupState
from behave.ppa.finalize import compare_searches, finalized_report
from behave.ppa.workflow import SearchArchive, run_search


def captured_search(path, count=4):
    class Designs:
        turn = 0

        def generate(self, request):
            turn = self.turn
            self.turn += 1
            design, phase = divmod(turn, 3)
            if design == count:
                name, args = 'finish_search', {}
            elif phase == 0:
                name, args = 'write_file', {'path': 'top.sv', 'content': RTL.decode() + f'\n// design {design}\n'}
            elif phase == 1:
                name, args = 'compare_rtl', {'rtl': {'files': ['top.sv'], 'entry_file': 'top.sv', 'entry_symbol': 'M'}}
            else:
                name, args = 'measure_ppa', {'candidate': request.messages[-2]['content']['candidate']}
            return ModelReply('', (ToolCall(str(turn), name, args),))

    public, task, _ = fixture_task()
    run_search(public=public, task=task, experiment=experiment(), native=FakeNative(),
               backend=Designs(), spec=AttemptSpec('parallel-fixture', 'ppa', {'model': 'fake'}),
               directory=path, prompt='develop')
    return task


@pytest.mark.parametrize('remote', [False, True])
def test_candidates_share_cpu_pool_without_overlap_and_commit_on_main_thread(tmp_path, monkeypatch, remote):
    if len(os.sched_getaffinity(0)) < 2:
        pytest.skip('needs two CPUs')
    task = captured_search(tmp_path / 'search')
    resources = CpuResources(2, cpus=2)
    barrier, lock = Barrier(2), Lock()
    active, overlaps, calls = set(), [], []
    writer = current_thread().ident
    original = SearchArchive.record

    def record(self, *args, **kwargs):
        assert current_thread().ident == writer
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SearchArchive, 'record', record)

    class Scheduled(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            with cpu_job(self.cpu_resources) as lease:
                with lock:
                    overlaps.append(bool(active.intersection(lease.cpus)))
                    active.update(lease.cpus)
                    calls.append(inputs.source.artifact)
                barrier.wait(timeout=5)
                result = super().run(inputs, exp, mode, pool, objects)
                with lock:
                    active.difference_update(lease.cpus)
                lease.cleanup = CleanupState.COMPLETE
                return result

    with ExitStack() as stack:
        native = Scheduled()
        native.cpu_resources = resources
        workers = None
        if remote:
            workers = []
            for _ in range(2):
                relay = stack.enter_context(resources.relay())
                relay.worker_cleanup = CleanupState.COMPLETE
                worker = Scheduled()
                worker.cpu_resources = relay.endpoint
                workers.append(worker)
        options = {'searches': {'one/r00': tmp_path / 'search'}, 'task': task, 'experiment': experiment(),
                   'native': native, 'workers': workers, 'directory': tmp_path / 'final'}
        result = compare_searches(**options)
        assert len(calls) == len(set(calls)) == 4
        assert not any(overlaps)
        assert len(result['candidates']) == 4
        assert all(row['score'] == 1 for row in result['candidates'].values())
        assert finalized_report(tmp_path / 'final')[2] == result
        assert compare_searches(**options) == result
        assert len(calls) == 4
        assert resources.snapshot()['active'] == resources.snapshot()['retired'] == 0
        assert resources.snapshot()['available_cpus'] == 2


def test_parallel_failure_settles_other_returned_result_and_recovery_skips_it(tmp_path):
    task = captured_search(tmp_path / 'search')
    barrier, lock = Barrier(2), Lock()
    calls = []

    class Interrupted(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            with lock:
                position = len(calls)
                calls.append(inputs.source.artifact)
            barrier.wait(timeout=5)
            if position == 0:
                raise RuntimeError('interrupted candidate')
            stop = time.monotonic() + 5
            while not self.cancelled():
                assert time.monotonic() < stop
                time.sleep(.005)
            result = super().run(inputs, exp, mode, pool, objects)
            result['score'] = result['performance']['score'] = 0
            return result

    native = Interrupted()
    options = {'searches': {'one/r00': tmp_path / 'search'}, 'task': task, 'experiment': experiment()}
    old = tmp_path / 'old'
    with pytest.raises(RuntimeError, match='interrupted candidate'):
        compare_searches(**options, native=native, workers=[native, native], directory=old)
    assert len(calls) == 2  # No new candidate dispatched after failure.
    assert JournalDirectory(old).journal('measurements').load()[-1].kind == 'settled'

    retried = []

    class Recovery(FakeNative):
        def run(self, inputs, *args):
            retried.append(inputs.source.artifact)
            return super().run(inputs, *args)

    result = compare_searches(**options, native=Recovery(), directory=tmp_path / 'replacement', reuse_final=old)
    assert len(retried) == 3 and calls[1] not in retried
    assert result['reused_service_calls'] == 1
    assert sorted(row['score'] for row in result['candidates'].values()) == [0, 1, 1, 1]


def test_cancelled_remote_admission_does_not_wait_for_unknown_peer_cleanup(tmp_path):
    resources = CpuResources(1, cpus=1)
    held = resources.acquire(lambda: False)
    from threading import Event
    cancelled = Event()
    from behave.ppa.worker import NativePPA
    native = NativePPA.__new__(NativePPA)
    native.cancelled = cancelled.is_set
    with resources.relay() as relay, ThreadPoolExecutor(1) as executor:
        native.cpu_resources = relay.endpoint
        future = executor.submit(native.run, None, None, 'final', (), None)
        try:
            stop = time.monotonic() + 5
            while resources.snapshot()['queued'] != 1:
                assert time.monotonic() < stop
                time.sleep(.005)
            cancelled.set()
            with pytest.raises(InterruptedError):
                future.result(timeout=5)
        finally:
            resources.release(held, CleanupState.COMPLETE)
    assert resources.snapshot()['active'] == 0
