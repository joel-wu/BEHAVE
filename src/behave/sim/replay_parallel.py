"""Bounded run scheduling shared by concrete Behavior and RTL replay.

One worker owns a complete run, including its postprocessing. There is no
second pool or queue of completed traces waiting for CPU processing. Submitted
jobs are bounded too; final results retain input order, not completion order.
"""

import os
from collections.abc import Sized
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context

from behave.core import runtime_defaults as defaults

from .cpu_admission import check_cpu_work, cpu_capacity, cpu_cleanup_unconfirmed, reserve_cpus
from .execution_control import check_execution


def replay_workers(requested=None):
    """Resolve the public environment budget once per replay invocation."""
    if requested is None:
        configured = os.environ.get("BEHAVE_REPLAY_WORKERS")
        if configured is not None:
            try:
                requested = int(configured)
            except ValueError:
                raise ValueError("BEHAVE_REPLAY_WORKERS must be a positive integer") from None
        else:
            try:
                cpus = len(os.sched_getaffinity(0))
            except (AttributeError, OSError):
                cpus = os.cpu_count() or 1
            requested = min(defaults.SIM_REPLAY_AUTO_WORKERS, max(1, cpus),
                            cpu_capacity() or defaults.SIM_REPLAY_AUTO_WORKERS)
    if type(requested) is not int or requested < 1:
        raise ValueError("replay workers must be a positive exact integer")
    return requested


def ordered_map(function, values, workers, *, stop_when=None):
    """Run at most ``workers`` jobs, with invocation context and ordered output.

    An unexpected coordinator error cancels work not yet running and waits for
    active jobs' normal cleanup before propagating. It never retries a job.
    Candidate/reference failures are result values, not coordinator exceptions.
    ``stop_when`` stops admitting new jobs; all started jobs are joined and their
    results remain in input order.
    """
    workers = replay_workers(workers)
    if isinstance(values, Sized):
        if not len(values):
            return []
        workers = min(workers, len(values))
    with reserve_cpus(workers) as admitted:
        return _ordered_map(function, values, admitted, stop_when=stop_when)


def _ordered_map(function, values, workers, *, stop_when=None):
    if workers == 1:
        def serial():
            for value in values:
                check_execution()
                check_cpu_work()
                result = function(value)
                yield result
                if stop_when is not None and stop_when(result):
                    break
        return list(serial())
    source = iter(enumerate(values))
    results, pending = {}, {}
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="behave-replay")
    try:
        def submit_one():
            check_execution()
            check_cpu_work()
            item = next(source, None)
            if item is None:
                return
            index, value = item
            context = copy_context()
            pending[pool.submit(context.run, function, value)] = index

        try:
            for _ in range(workers):
                submit_one()
            stopping = False
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                completed_count = len(completed)
                # Read all completed jobs before submitting replacements: an error
                # cannot enqueue more work merely because another job also finished.
                while completed:
                    future = completed.pop()
                    index = pending.pop(future)
                    try:
                        result = future.result()
                    finally:
                        # A failed Future owns the traceback of this frame. Do
                        # not let that frame own the Future in turn, retaining
                        # the whole replay batch until cyclic GC at shutdown.
                        future = None
                    results[index] = result
                    if stop_when is not None and stop_when(result):
                        stopping = True
                if not stopping:
                    for _ in range(completed_count):
                        submit_one()
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    finally:
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        except BaseException:
            cpu_cleanup_unconfirmed()
            raise
    return [results[index] for index in range(len(results))]
