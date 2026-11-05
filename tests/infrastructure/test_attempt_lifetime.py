"""Turn-bounded attempts, durable cancellation and per-operation timeouts."""

import math
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from behave.agent import AttemptControl, AttemptInterrupted, AttemptJournal, AttemptSpec, run_attempt
from behave.agent.lifetime import current_window
from behave.agent.model import Charge, ModelFailure, ModelReply, ToolCall
from behave.agent.tools import Tool, ToolResult, file_tools, object_schema
from behave.agent.trajectory import trajectory
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore


class Clock:
    value = 1000.

    def now(self):
        return self.value


def setup(tmp_path, **limits):
    return (AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects")),
            AttemptSpec("a", "test", {"model": "fake"}, **limits))


def invoke(journal, spec, backend, control, tools=None):
    return run_attempt(journal=journal, spec=spec, backend=backend, control=control,
                       initial_messages=({"role": "user", "content": "test"},),
                       tools=file_tools() if tools is None else tools)


class Never:
    def generate(self, request):
        pytest.fail("must not call the backend")


def test_elapsed_time_does_not_stop_tools_or_turns_and_replay_is_identical(tmp_path):
    journal, spec = setup(tmp_path, max_turns=2)
    clock = Clock()
    control = AttemptControl(wall_clock=clock.now, monotonic=clock.now)

    class Slow:
        def generate(self, request):
            assert math.isinf(current_window().remaining())
            clock.value += 10000
            return ModelReply("", (ToolCall("write", "write_file", {"path": "x", "content": "x"}),),
                              charge=Charge("0.2", "USD", "fixture"))

    result = invoke(journal, spec, Slow(), control)
    assert result.reason == "turn_limit" and result.turns == 2
    assert len([event for event in journal.load() if event.kind == "tool_settled"]) == 2
    assert not any(event.kind == "lifetime" for event in journal.load())
    assert trajectory(journal).cost_by_currency() == {"USD": "0.4"}
    clock.value += 1000
    assert invoke(journal, spec, Never(), control) == result
    assert current_window() is None


def test_retries_are_not_stopped_by_cumulative_elapsed_time(tmp_path, monkeypatch):
    from behave.agent import loop

    journal, spec = setup(tmp_path)
    clock = Clock()
    control = AttemptControl(wall_clock=clock.now, monotonic=clock.now)
    seen = []

    def sleep(seconds):
        clock.value += seconds

    monkeypatch.setattr(loop, "time", SimpleNamespace(monotonic=clock.now, sleep=sleep))

    class Retry:
        def generate(self, request):
            seen.append(current_window().remaining())
            clock.value += 10000
            return ModelFailure("retryable", "rate-limit") if request.dispatch == 0 else ModelReply("done")

    result = invoke(journal, spec, Retry(), control)
    assert len(seen) == 2 and all(math.isinf(value) for value in seen)
    assert result.reason == "stopped" and result.turns == 1
    assert len([e for e in journal.load() if e.kind == "model_requested"]) == 2
    assert invoke(journal, spec, Never(), control) == result


@pytest.mark.parametrize("delay", [2**63, 10**100])
def test_large_retry_delay_remains_cancellable_without_an_early_retry(tmp_path, monkeypatch, delay):
    from behave.agent import loop

    journal, spec = setup(tmp_path)
    clock = Clock()
    cancelled = Event()
    control = AttemptControl(cancelled.is_set, clock.now, clock.now)
    calls = []

    def sleep(seconds):
        clock.value += seconds
        cancelled.set()

    class Backend:
        def generate(self, request):
            calls.append(request)
            return ModelFailure("retryable", "busy", retry_after_seconds=delay)

    monkeypatch.setattr(loop, "time", SimpleNamespace(monotonic=clock.now, sleep=sleep))
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Backend(), control)
    assert len(calls) == 1
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Never(), control)


def test_retry_wait_uses_shared_poll_interval_and_replay_does_not_sleep(tmp_path, monkeypatch):
    from behave.agent import loop
    from behave.core import runtime_defaults as defaults

    journal, spec = setup(tmp_path)
    clock = Clock()
    control = AttemptControl(wall_clock=clock.now, monotonic=clock.now)
    sleeps = []
    requests = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.value += seconds

    class Backend:
        def generate(self, request):
            requests.append((request.turn, request.dispatch, clock.value))
            if request.dispatch == 0:
                return ModelFailure("retryable", "rate-limit")
            return ModelReply("done")

    monkeypatch.setattr(defaults, "AGENT_RETRY_POLL_SECONDS", 0.2)
    monkeypatch.setattr(loop, "time", SimpleNamespace(monotonic=clock.now, sleep=sleep))
    result = invoke(journal, spec, Backend(), control)
    assert result.turns == 1
    assert requests == [(0, 0, 1000.), (0, 1, 1001.)]
    assert sleeps[0] == 0.2 and all(0 < seconds <= 0.2 for seconds in sleeps)
    assert sum(sleeps) == pytest.approx(1.)
    slept = tuple(sleeps)
    assert invoke(journal, spec, Never(), control) == result
    assert tuple(sleeps) == slept


def test_replay_uses_recorded_backoff_after_default_changes(tmp_path, monkeypatch):
    from behave.agent import loop
    from behave.core import runtime_defaults as defaults

    journal, spec = setup(tmp_path)
    clock = Clock()
    control = AttemptControl(wall_clock=clock.now, monotonic=clock.now)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.value += seconds

    class Backend:
        def generate(self, request):
            return ModelFailure("retryable", "busy") if request.dispatch < 2 else ModelReply("done")

    monkeypatch.setattr(loop, "time", SimpleNamespace(monotonic=clock.now, sleep=sleep))
    result = invoke(journal, spec, Backend(), control)
    events = journal.load()
    slept = tuple(sleeps)
    monkeypatch.setattr(defaults, "AGENT_RETRY_BACKOFF_SECONDS", 1)
    assert invoke(journal, spec, Never(), control) == result
    assert journal.load() == events and tuple(sleeps) == slept


def test_unfinished_backoff_keeps_recorded_end_after_default_changes(tmp_path, monkeypatch):
    from behave.agent import loop
    from behave.core import runtime_defaults as defaults

    journal, spec = setup(tmp_path)
    clock = Clock()
    control = AttemptControl(wall_clock=clock.now, monotonic=clock.now)
    sleeps = []
    append = journal.append

    def sleep(seconds):
        sleeps.append(seconds)
        clock.value += seconds

    class Crash(BaseException):
        pass

    def crash_after_backoff(*args, **kwargs):
        event = append(*args, **kwargs)
        if event.kind == "model_backoff" and event.payload["call_id"] == "a:0:1":
            raise Crash()
        return event

    class Busy:
        def generate(self, request):
            assert request.dispatch < 2
            return ModelFailure("retryable", "busy")

    monkeypatch.setattr(loop, "time", SimpleNamespace(monotonic=clock.now, sleep=sleep))
    monkeypatch.setattr(journal, "append", crash_after_backoff)
    with pytest.raises(Crash):
        invoke(journal, spec, Busy(), control)
    history = journal.load()
    until = history[-1].payload["until"]
    clock.value += 0.5
    remaining = until - clock.value
    sleeps.clear()
    monkeypatch.setattr(journal, "append", append)
    monkeypatch.setattr(defaults, "AGENT_RETRY_BACKOFF_SECONDS", 1)

    class Continued:
        def generate(self, request):
            assert request.dispatch == 2 and clock.value == pytest.approx(until)
            return ModelReply("done")

    result = invoke(journal, spec, Continued(), control)
    assert result.turns == 1 and result.reason == "stopped"
    assert sum(sleeps) == pytest.approx(remaining)
    assert journal.load()[:len(history)] == history


def test_cancel_before_dispatch_is_durable_and_never_no_submission_zero(tmp_path):
    journal, spec = setup(tmp_path)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Never(), AttemptControl(cancelled.is_set))
    cancelled.clear()
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Never(), AttemptControl(cancelled.is_set))
    assert [e.kind for e in journal.load()] == ["initialized", "interrupted"]
    assert trajectory(journal).unresolved_dispatches == 0


def test_cancel_between_tools_preserves_committed_effect_and_billing(tmp_path):
    journal, spec = setup(tmp_path)
    cancelled = Event()

    def stop(workspace, arguments):
        workspace.write("saved", b"committed")
        cancelled.set()
        return ToolResult({"saved": True})

    class Backend:
        def generate(self, request):
            return ModelReply("", (ToolCall("one", "stop", {}), ToolCall("two", "stop", {})),
                              charge=Charge("0.1", "USD", "fixture"))

    tools = (Tool("stop", "test", object_schema({}), stop, "1"),)
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Backend(), AttemptControl(cancelled.is_set), tools)
    assert len([e for e in journal.load() if e.kind == "tool_settled"]) == 1
    assert trajectory(journal).cost_by_currency() == {"USD": "0.1"}
    cancelled.clear()
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Never(), AttemptControl(cancelled.is_set), tools)


def test_completed_attempt_ignores_later_cancellation_and_elapsed_time(tmp_path):
    journal, spec = setup(tmp_path)
    clock = Clock()

    class Done:
        def generate(self, request):
            return ModelReply("done")

    result = invoke(journal, spec, Done(), AttemptControl(wall_clock=clock.now, monotonic=clock.now))
    clock.value += 10000
    assert invoke(journal, spec, Never(), AttemptControl(lambda: True, clock.now, clock.now)) == result
    with pytest.raises(RecordError, match="replay differs"):
        invoke(journal, replace(spec, max_turns=spec.max_turns + 1), Never(), AttemptControl())


def test_recovery_after_settled_tool_can_continue_after_long_elapsed_time(tmp_path, monkeypatch):
    journal, spec = setup(tmp_path)
    clock = Clock()
    control = AttemptControl(wall_clock=clock.now, monotonic=clock.now)
    append = journal.append

    class Crash(BaseException):
        pass

    def crash_after_commit(*args, **kwargs):
        event = append(*args, **kwargs)
        if event.kind == "tool_settled":
            raise Crash()
        return event

    class Backend:
        def generate(self, request):
            return ModelReply("", (ToolCall("one", "list_files", {}),))

    monkeypatch.setattr(journal, "append", crash_after_commit)
    with pytest.raises(Crash):
        invoke(journal, spec, Backend(), control)
    assert current_window() is None
    monkeypatch.setattr(journal, "append", append)
    clock.value += 10000

    class Continued:
        def generate(self, request):
            assert request.turn == 1 and math.isinf(current_window().remaining())
            return ModelReply("done")

    result = invoke(journal, spec, Continued(), control)
    assert result.reason == "stopped" and result.turns == 2
    assert len([e for e in journal.load() if e.kind == "model_requested"]) == 2
    assert len([e for e in journal.load() if e.kind == "tool_settled"]) == 1


def test_recovery_obeys_cancellation_without_rewriting_history(tmp_path, monkeypatch):
    journal, spec = setup(tmp_path)
    clock = Clock()
    control = AttemptControl(wall_clock=clock.now, monotonic=clock.now)
    append = journal.append

    class Crash(BaseException):
        pass

    def crash_after_commit(*args, **kwargs):
        event = append(*args, **kwargs)
        if event.kind == "tool_settled":
            raise Crash()
        return event

    class First:
        def generate(self, request):
            return ModelReply("", (ToolCall("one", "list_files", {}),))

    monkeypatch.setattr(journal, "append", crash_after_commit)
    with pytest.raises(Crash):
        invoke(journal, spec, First(), control)
    history = journal.load()
    monkeypatch.setattr(journal, "append", append)
    clock.value += 1

    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Never(), replace(control, cancelled=lambda: True))
    assert journal.load()[:len(history)] == history
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        invoke(journal, spec, Never(), control)


def test_operation_deadline_survives_wall_clock_rollback_and_nested_window_restores():
    from behave.agent.lifetime import ExecutionWindow, execution_window

    wall, mono = Clock(), Clock()
    control = AttemptControl(wall_clock=wall.now, monotonic=mono.now)
    window = ExecutionWindow(control, wall.now() + 10, mono.now() + 10)
    wall.value -= 100
    mono.value += 4
    assert window.remaining() == 6
    nested = replace(window, monotonic_deadline=mono.value + 1)
    with execution_window(window):
        with pytest.raises(KeyboardInterrupt), execution_window(nested):
            assert current_window() is nested
            raise KeyboardInterrupt
        assert current_window() is window
    assert current_window() is None
    mono.value += 6
    assert window.remaining() == 0


def test_retired_wall_budget_is_readable_but_not_mutated_or_serialized(tmp_path):
    _, spec = setup(tmp_path)
    old = {**spec.as_dict(), "wall_timeout_seconds": 3600}
    assert AttemptSpec.from_dict(old) == spec
    assert old["wall_timeout_seconds"] == 3600
    assert "wall_timeout_seconds" not in spec.as_dict()
