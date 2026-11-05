"""A finished watchdog must not keep a private native solver arena alive."""

import gc
import time
import weakref

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim.rtl.query_deadline import QueryDeadline


@pytest.mark.parametrize("exit_kind", ("normal", "error", "expired"))
def test_query_context_is_released_without_cyclic_collection(monkeypatch, exit_kind):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    contexts, watchdogs = [], []

    def query():
        context = z3.Context()
        contexts.append(weakref.ref(context))
        try:
            with QueryDeadline(context, 1000, started=clock[0]) as deadline:
                watchdogs.append(weakref.ref(deadline))
                assert deadline.remaining_ms() == 1000
                if exit_kind == "error":
                    raise ValueError("original query error")
                if exit_kind == "expired":
                    clock[0] += 2.0
        except (ValueError, TimeoutError) as error:
            expected = ValueError if exit_kind == "error" else TimeoutError
            assert type(error) is expected
        else:
            assert exit_kind == "normal"

    # Python's collection thresholds know nothing about a Z3 context's native
    # heap size. Query ownership cannot depend on reaching those thresholds.
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        for _ in range(8):
            query()
            assert contexts[-1]() is None
            assert watchdogs[-1]() is None
    finally:
        if enabled:
            gc.enable()
        gc.collect()
