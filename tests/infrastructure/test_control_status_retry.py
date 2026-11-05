import errno

import pytest

from behave.runtime import jobs as control


def test_transient_status_read_reopens_without_relaunch(monkeypatch):
    observed = []
    sleeps = []
    def read(path):
        observed.append(path)
        if len(observed) < 3:
            raise OSError(errno.ESTALE, 'stale file handle')
        return {'state':'supervising', 'heartbeat_overdue':False}
    monkeypatch.setattr(control, '_status_of', read)
    monkeypatch.setattr(control.time, 'sleep', sleeps.append)
    monkeypatch.setattr(control, 'submit', lambda *a, **k: pytest.fail('read cannot launch'))
    assert control.status_of('/same/control')['state'] == 'supervising'
    assert observed == ['/same/control']*3
    assert sleeps == [.05, .1]


@pytest.mark.parametrize('code,attempts', [(errno.ESTALE,8),(521,8),(errno.EACCES,1),(errno.ENOENT,1)])
def test_status_read_does_not_hide_failure(monkeypatch, code, attempts):
    calls = []
    def read(path):
        calls.append(path)
        raise OSError(code, 'failure')
    monkeypatch.setattr(control, '_status_of', read)
    monkeypatch.setattr(control.time, 'sleep', lambda _:None)
    with pytest.raises(OSError) as caught:
        control.status_of('/same/control')
    assert caught.value.errno == code
    assert len(calls) == attempts
