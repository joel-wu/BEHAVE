"""The real namespace supervisor's exit cause survives a missing JSON reply."""

from dataclasses import replace

import pytest

from behave.sim import api, job_session
from behave.sim.job_isolation import IsolatedJobFailure


@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("frame", [b"", b'{"ok":'])
@pytest.mark.parametrize("diagnostic", [b"", b"last worker stage\n"])
def test_native_session_eof_is_not_an_answer_or_a_candidate_failure(monkeypatch, exit_code, frame, diagnostic):
    original = job_session._job_spec

    def spec(*args, **kwargs):
        result = original(*args, **kwargs)
        # Trusted fixture, same namespace/runtime/CPU owner as production.
        source = ("import os, sys\n"
                  "sys.stdin.buffer.readline()\n"
                  f"os.write(2, {diagnostic!r})\n"
                  f"os.write(1, {frame!r})\n"
                  f"os._exit({exit_code})\n")
        return replace(result, argv=(result.argv[0], "-S", "-u", "-c", source))

    monkeypatch.setattr(job_session, "_job_spec", spec)
    timings = []
    with api.CpuPool(1) as pool, api.cpu_scope(pool.endpoint):
        with pytest.raises(IsolatedJobFailure) as caught:
            job_session.JobSession("echo", {}, module="fixture", version="fixture/1",
                                   timeout_s=30, wall_s=30, exchanges=None, timings=timings)
        assert not pool.snapshot()["quarantined"]
    assert caught.value.how == "no-answer"
    assert timings[0]["status"] == "failed"
    terminal = {"status": "completed" if exit_code == 0 else "exited", "returncode": exit_code}
    if diagnostic:
        terminal['stderr'] = {'bytes': len(diagnostic), 'truncated': False,
                              'tail': diagnostic.decode()}
        assert timings[0]['stderr'] == terminal['stderr']
    assert timings[0]["worker_exit"] == terminal
