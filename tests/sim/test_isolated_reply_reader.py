"""Reply readiness belongs to the pipe, not a separately scheduled thread."""

import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from behave.isolation.linux import IsolationOutputLimit, _BoundedBinaryReader
from behave.sim.behavior import isolated
from behave.sim.scoring_policy import ScoringPolicy, ScoringStopped, scoring_scope
from behave.sim.search_policy import SearchBudget, SearchPolicy, SearchStopped, search_scope


@contextmanager
def reply_pipe(*, wall_seconds=1, output_limit=None):
    read_fd, write_fd = os.pipe()
    closed, limited = [], []
    with os.fdopen(read_fd, 'rb') as raw, os.fdopen(write_fd, 'wb', buffering=0) as writer:
        stream = _BoundedBinaryReader(raw, output_limit,
            SimpleNamespace(_output_limited=limited.append), 'stdout')
        model = object.__new__(isolated.IsolatedModel)
        model._limits = SimpleNamespace(wall_seconds=wall_seconds)
        model._child = SimpleNamespace(stdout=SimpleNamespace(buffer=stream))
        model._end_child = lambda: closed.append(True)
        # Test fixture owns these actual pipes, not a sandbox child.
        model.close = lambda: None
        yield model, writer, closed, limited


def test_ready_reply_does_not_start_a_reader_thread(monkeypatch):
    import threading

    monkeypatch.setattr(threading.Thread, 'start', lambda _: pytest.fail('no reader thread needed'))
    with reply_pipe(wall_seconds=0) as (model, writer, closed, _limited):
        writer.write(b'{"ok":true}\n')
        assert model._read_with_deadline() == b'{"ok":true}\n'
        assert not closed


def test_coalesced_replies_retain_framing_and_count_each_message(monkeypatch):
    monkeypatch.setattr(isolated, '_MAX_PROTOCOL_BYTES', 4)
    with reply_pipe() as (model, writer, closed, _limited):
        writer.write(b'one\ntwo\n')
        assert model._read_with_deadline() == b'one\n'
        assert model._read_with_deadline() == b'two\n'
        assert not closed


def test_partial_ready_reply_does_not_renew_the_deadline():
    with reply_pipe(wall_seconds=0) as (model, writer, closed, _limited):
        writer.write(b'unterminated')
        with pytest.raises(isolated.Timeout):
            model._read_with_deadline()
        assert closed == [True]


def test_shared_budget_cancellation_is_not_reference_timeout():
    with reply_pipe() as (model, _writer, closed, _limited), scoring_scope(ScoringPolicy(0)):
        with pytest.raises(ScoringStopped):
            model._read_with_deadline()
        assert closed == [True]


def test_scoped_replies_use_remaining_budget_without_renewing_it(monkeypatch):
    now, waits = [100.0], []
    monkeypatch.setattr(isolated.time, 'monotonic', lambda: now[0])

    class ReadyPoll:
        def register(self, *_args):
            pass

        def poll(self, milliseconds):
            waits.append(milliseconds)
            return [(0, isolated.select.POLLIN)]

    monkeypatch.setattr(isolated.select, 'poll', ReadyPoll)
    with reply_pipe(wall_seconds=1) as (model, writer, _closed, _limited), scoring_scope(ScoringPolicy(30)):
        writer.write(b'first\n')
        assert model._read_with_deadline() == b'first\n'
        now[0] += 5
        writer.write(b'second\n')
        assert model._read_with_deadline() == b'second\n'
        lease = SearchBudget(SearchPolicy(time_budget_s=12)).direction(1)
        with search_scope(lease):
            writer.write(b'third\n')
            assert model._read_with_deadline() == b'third\n'
            now[0] += 3
            writer.write(b'fourth\n')
            assert model._read_with_deadline() == b'fourth\n'
    assert waits == [30000, 25000, 12000, 9000]


@pytest.mark.parametrize('owner', ['score', 'search', 'analysis'])
def test_scoped_wait_exhaustion_is_control_flow_not_a_model_fault(monkeypatch, owner):
    from behave.sim.analysis_budget import AnalysisStopped, analysis_scope

    now, waits = [100.0], []
    monkeypatch.setattr(isolated.time, 'monotonic', lambda: now[0])

    class ExpiringPoll:
        def register(self, *_args):
            pass

        def poll(self, milliseconds):
            waits.append(milliseconds)
            now[0] += milliseconds / 1000
            return []

    monkeypatch.setattr(isolated.select, 'poll', ExpiringPoll)
    scope = {'score': lambda: scoring_scope(ScoringPolicy(30)),
             'search': lambda: search_scope(SearchBudget(SearchPolicy(time_budget_s=30)).direction(1)),
             'analysis': lambda: analysis_scope(30)}[owner]
    stopped = {'score': ScoringStopped, 'search': SearchStopped, 'analysis': AnalysisStopped}[owner]
    with reply_pipe(wall_seconds=1) as (model, _writer, closed, _limited), scope():
        with pytest.raises(stopped):
            model._read_with_deadline()
        assert waits == [30000]
        assert closed == [True]


@pytest.mark.parametrize('complete', [True, False])
def test_host_descheduling_preserves_a_delivered_reply_not_a_partial_one(monkeypatch, complete):
    now = [0.0]
    monkeypatch.setattr(isolated.time, 'monotonic', lambda: now[0])

    class DelayedPoll:
        def register(self, *_args):
            pass

        def poll(self, _timeout):
            now[0] = 30.0  # The host resumes after its work deadline.
            return [(0, isolated.select.POLLIN)]

    monkeypatch.setattr(isolated.select, 'poll', DelayedPoll)
    with reply_pipe() as (model, writer, closed, _limited), scoring_scope(ScoringPolicy(5)):
        reply = b'{"raised":"ValueError"}' + (b'\n' if complete else b'')
        writer.write(reply)
        if complete:
            assert model._read_with_deadline() == reply
            assert not closed  # The caller must interpret this arrived failure.
        else:
            with pytest.raises(ScoringStopped):
                model._read_with_deadline()
            assert closed == [True]


def test_no_reply_keeps_the_standalone_timeout():
    with reply_pipe(wall_seconds=0) as (model, _writer, closed, _limited):
        model._pending_call = '_bind_external_memory'
        with pytest.raises(isolated.Timeout, match='during _bind_external_memory'):
            model._read_with_deadline()
        assert closed == [True]


@pytest.mark.parametrize('code', ['isolation-timed-out', 'isolation-cpu-limit',
                                  'isolation-memory-limit', ''])
def test_supervisor_eof_at_budget_stop_preserves_actual_failure_ownership(monkeypatch, code):
    now = [100.0]
    monkeypatch.setattr(isolated.time, 'monotonic', lambda: now[0])

    class TerminalPoll:
        def register(self, *_args):
            pass

        def poll(self, _milliseconds):
            now[0] = 130.0
            return [(0, isolated.select.POLLHUP)]

    monkeypatch.setattr(isolated.select, 'poll', TerminalPoll)
    with reply_pipe() as (model, writer, closed, _limited), scoring_scope(ScoringPolicy(30)):
        writer.close()
        model._broken_transport = lambda: isolated.Broken('supervisor receipt', code=code)
        model.close = lambda: closed.append(True)
        if code == 'isolation-timed-out':
            with pytest.raises(ScoringStopped):
                model._hear()
        else:
            with pytest.raises(isolated.Broken) as error:
                model._hear()
            assert error.value.code == code
        assert closed == [True]


@pytest.mark.parametrize('body', [b'', b'partial'])
def test_eof_cannot_turn_a_partial_frame_into_an_answer(body):
    with reply_pipe() as (model, writer, closed, _limited):
        writer.write(body)
        writer.close()
        if body:
            with pytest.raises(isolated.Broken, match='terminating newline'):
                model._read_with_deadline()
            assert closed == [True]
        else:
            assert model._read_with_deadline() == b''


def test_protocol_and_stdout_quotas_remain_distinct(monkeypatch):
    monkeypatch.setattr(isolated, '_MAX_PROTOCOL_BYTES', 4)
    with reply_pipe() as (model, writer, closed, limited):
        writer.write(b'too-long\n')
        with pytest.raises(isolated.Broken, match='protocol budget'):
            model._read_with_deadline()
        assert closed == [True] and not limited
    with reply_pipe(output_limit=4) as (model, writer, _closed, limited):
        writer.write(b'too-long\n')
        assert model._read_with_deadline() == b''
        assert limited == ['stdout']


def test_large_reply_is_not_one_chunk_or_a_character_quota():
    import threading

    body = ('\u4e2d' * (2 * isolated.io.DEFAULT_BUFFER_SIZE) + '\n').encode()
    with reply_pipe() as (model, writer, closed, _limited):
        producer = threading.Thread(target=writer.write, args=(body,))
        producer.start()
        try:
            assert model._read_with_deadline() == body
        finally:
            producer.join()
        assert not closed


def test_read1_still_enforces_stream_accounting():
    with reply_pipe(output_limit=2) as (model, writer, _closed, limited):
        writer.write(b'123')
        with pytest.raises(IsolationOutputLimit):
            model._child.stdout.buffer.read1(3)
        assert limited == ['stdout']
