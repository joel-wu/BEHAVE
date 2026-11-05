"""An idle decode worker does not retain the preceding large trace or request."""

import weakref

from behave.sim.rtl import _trace_worker


def test_worker_releases_completed_request_and_result_before_next_receive(monkeypatch):
    class Payload:
        pass

    retained = []
    class Channel:
        received = 0
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def send(self, value):
            if type(value) is tuple:
                assert value[0] is True
                retained.append(weakref.ref(value[1]))
        def recv(self):
            self.received += 1
            if self.received == 1:
                return ()
            if self.received == 2:
                request = Payload()
                retained.append(weakref.ref(request))
                return (request,)
            assert len(retained) == 2
            assert all(reference() is None for reference in retained)
            raise EOFError

    monkeypatch.setattr(_trace_worker.sys, "argv", ["worker", "1"])
    monkeypatch.setattr(_trace_worker.socket, "socket", lambda **_: object())
    monkeypatch.setattr(_trace_worker, "TraceChannel", lambda _: Channel())
    monkeypatch.setattr(_trace_worker, "_initialize", lambda: None)
    monkeypatch.setattr(_trace_worker, "_worker", lambda *_: Payload())
    _trace_worker.main()
