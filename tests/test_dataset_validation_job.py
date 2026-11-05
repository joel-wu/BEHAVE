"""Short real subprocess checks; run on the compute allocation, without EDA."""
import json
import sys
import time

import pytest

from behave.runtime import jobs as runtime_jobs
from tools import dataset_validation_job as jobs


@pytest.fixture
def review_tools(tmp_path, monkeypatch):
    repository = tmp_path / 'checkout'
    validator = repository / 'tools/validate_dataset_repairs.py'
    validator.parent.mkdir(parents=True)
    validator.write_text('# validator fixture\n')
    monkeypatch.setattr(jobs, 'REPO', repository)
    return validator


def test_cli_requests_exclusive_cpu_resources_and_runtime_queue_wait(tmp_path, monkeypatch, review_tools):
    launches = []
    monkeypatch.setattr(jobs.defaults, "JOB_SLURM_WAIT_SECONDS", 23)
    monkeypatch.setattr(jobs, "submit", lambda directory, argv, **kw:
                        launches.append((argv, kw)) or {})
    jobs.main(["submit", str(tmp_path / "job"), "--job", "17", "--cpus", "8",
               "--wall-seconds", "125", "--", "--tasks", "fixture"])
    argv, options = launches[0]
    assert "--overlap" not in argv
    assert {"--exact", "--cpu-bind=cores", "--gres=none", "--immediate=23", "-c8"} <= set(argv)
    assert "--time=3" in argv and options["wall_seconds"] == 125


def wait_for(directory, terminal=False):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        state = jobs.status_of(directory)
        expected = {'completed', 'failed', 'timed-out', 'launch-error'} if terminal else {'supervising'}
        if state['state'] in expected:
            return state
        time.sleep(.05)
    pytest.fail(f'no expected status: {state}')


def test_detached_submission_logs_and_survives_the_submit_call(tmp_path):
    directory = tmp_path/'run'
    release = tmp_path/'release'
    program = (
        'import pathlib,time; print("started",flush=True); '
        f'p=pathlib.Path({str(release)!r})\n'
        'while not p.exists(): time.sleep(.05)\n'
        'print("finished",flush=True)')
    submitted = jobs.submit(directory, [sys.executable, '-c', program],
                            wall_seconds=10, metadata={'test': True})
    assert submitted['state'] == 'submitted'
    assert not release.exists()
    running = wait_for(directory)
    assert running['validation_passed'] is None
    release.touch()
    done = wait_for(directory, terminal=True)
    assert done['state'] == 'completed'
    assert done['returncode'] == 0
    assert done['validation_passed'] is None  # exit zero is not a task PASS
    assert (directory/'run.log').read_text().splitlines() == ['started', 'finished']


def test_submission_freezes_the_runtime_cleanup_allowance(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(jobs.defaults, "JOB_CLEANUP_SECONDS", 19)
    monkeypatch.setattr(jobs.defaults, "JOB_HEARTBEAT_SECONDS", 23)
    monkeypatch.setattr(runtime_jobs.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=123))
    directory = tmp_path / "run"
    jobs.submit(directory, ["/bin/true"], wall_seconds=10, metadata={})
    plan = json.loads((directory / "launch.json").read_text())
    assert plan["cleanup_seconds"] == 19 and plan["wall_seconds"] == 10
    assert plan["heartbeat_seconds"] == 23


@pytest.mark.parametrize('reaped', [False, True])
def test_cleanup_does_not_overwrite_job_timeout_or_signal_a_reaped_group(tmp_path, monkeypatch, reaped):
    import signal
    import subprocess
    from types import SimpleNamespace

    class Process:
        pid = 123
        returncode = None
        calls = 0

        def wait(self, timeout):
            self.calls += 1
            # Initial supervision observes the original wall deadline.
            if self.calls == 1 or not reaped:
                raise subprocess.TimeoutExpired('test-command', timeout)
            self.returncode = -signal.SIGTERM
            return self.returncode

        def poll(self):
            return self.returncode

    process = Process()
    signals = []
    monkeypatch.setattr(runtime_jobs.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(runtime_jobs.os, 'killpg', lambda pid, sig: signals.append(sig))
    # Expire in the supervise loop, before launching a second execution wait.
    ticks = iter([0, 2])
    monkeypatch.setattr(runtime_jobs, 'time', SimpleNamespace(monotonic=lambda: next(ticks, 2)))
    (tmp_path / 'launch.json').write_text(json.dumps({
        'argv': ['/bin/unused'], 'cwd': str(tmp_path), 'wall_seconds': 1,
        'cleanup_seconds': 7, 'heartbeat_seconds': 5}))
    # The initial wait is bypassed by the already-expired deadline.
    process.calls = 1
    jobs.supervise(tmp_path)
    status = json.loads((tmp_path / 'status.json').read_text())
    assert status['state'] == 'timed-out'
    assert status['cleanup_state'] == ('complete' if reaped else 'unconfirmed')
    assert signals == ([signal.SIGTERM] if reaped else [signal.SIGTERM, signal.SIGKILL])


@pytest.mark.parametrize('allowance', [1, 7, 60])
def test_cleanup_signals_share_one_deadline(tmp_path, monkeypatch, allowance):
    import subprocess
    from types import SimpleNamespace

    now, waits = [0], []

    class Process:
        pid = 123

        def wait(self, timeout):
            waits.append(timeout)
            now[0] += timeout
            raise subprocess.TimeoutExpired('test-command', timeout)

        def poll(self):
            return None

    def launch(*args, **kwargs):
        now[0] = 2
        return Process()

    monkeypatch.setattr(runtime_jobs.subprocess, 'Popen', launch)
    monkeypatch.setattr(runtime_jobs.os, 'killpg', lambda *args: None)
    monkeypatch.setattr(runtime_jobs, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    (tmp_path / 'launch.json').write_text(json.dumps({
        'argv': ['/bin/unused'], 'cwd': str(tmp_path), 'wall_seconds': 1,
        'cleanup_seconds': allowance, 'heartbeat_seconds': 5}))
    jobs.supervise(tmp_path)
    assert waits == [allowance, 0]
    status = json.loads((tmp_path / 'status.json').read_text())
    assert (status['state'], status['cleanup_state']) == ('timed-out', 'unconfirmed')
    assert status['elapsed_s'] == 2 + allowance


@pytest.mark.parametrize('failed_operation', ['signal', 'wait', 'poll'])
def test_cleanup_os_error_remains_secondary(tmp_path, monkeypatch, failed_operation):
    import subprocess
    from types import SimpleNamespace

    now = [0]

    class Process:
        pid = 123

        def wait(self, timeout):
            if failed_operation == 'wait':
                raise OSError('cleanup wait failed')
            now[0] += timeout
            raise subprocess.TimeoutExpired('test-command', timeout)

        def poll(self):
            if failed_operation == 'poll':
                raise OSError('cleanup poll failed')

    def launch(*args, **kwargs):
        now[0] = 2
        return Process()

    def signal_group(*args):
        if failed_operation == 'signal':
            raise OSError('cleanup signal failed')

    monkeypatch.setattr(runtime_jobs.subprocess, 'Popen', launch)
    monkeypatch.setattr(runtime_jobs.os, 'killpg', signal_group)
    monkeypatch.setattr(runtime_jobs, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    (tmp_path / 'launch.json').write_text(json.dumps({
        'argv': ['/bin/unused'], 'cwd': str(tmp_path), 'wall_seconds': 1,
        'cleanup_seconds': 7, 'heartbeat_seconds': 5}))
    jobs.supervise(tmp_path)
    status = json.loads((tmp_path / 'status.json').read_text())
    assert (status['state'], status['cleanup_state']) == ('timed-out', 'unconfirmed')
    assert any(f'cleanup {failed_operation} failed' in text for text in status['cleanup_errors'])


def test_total_timeout_and_nonzero_exit_are_recorded(tmp_path):
    slow = tmp_path/'slow'
    jobs.submit(slow, [sys.executable, '-c', 'import time; time.sleep(30)'],
                wall_seconds=1, metadata={})
    assert wait_for(slow, terminal=True)['state'] == 'timed-out'
    failed = tmp_path/'failed'
    jobs.submit(failed, [sys.executable, '-c', 'raise SystemExit(7)'],
                wall_seconds=5, metadata={})
    result = wait_for(failed, terminal=True)
    assert (result['state'], result['returncode'], result['validation_passed']) == ('failed', 7, None)


def test_no_overwrite_and_no_unbounded_budget(tmp_path):
    with pytest.raises(FileExistsError):
        jobs.submit(tmp_path, [], wall_seconds=1, metadata={})
    for budget in (0, -1, True):
        with pytest.raises(ValueError):
            jobs.submit(tmp_path/'new', [], wall_seconds=budget, metadata={})
    assert not (tmp_path/'new').exists()


def test_status_partial_results_are_not_a_pass(tmp_path):
    (tmp_path/'launch.json').write_text(json.dumps({'metadata': {'job': 26992}}))
    target = tmp_path/'validation/task'
    target.mkdir(parents=True)
    (target/'result.json').write_text('{')
    state = jobs.status_of(tmp_path)
    assert state['completed_tasks'] == 0 and state['validation_passed'] is None
    assert state['unreadable_results'] == [str(target/'result.json')]
    (target/'result.json').write_text(json.dumps({'task_id': 'task', 'passed': False}))
    state = jobs.status_of(tmp_path)
    assert state['completed_tasks'] == 1 and state['failed_tasks'] == ['task']
    assert state['validation_passed'] is None


def test_submit_cli_constructs_a_bounded_slurm_step_without_wait(monkeypatch, tmp_path, review_tools):
    captured = {}
    def submit(directory, argv, **kwargs):
        captured.update(directory=directory, argv=argv, **kwargs)
        return {'state': 'submitted'}
    monkeypatch.setattr(jobs, 'submit', submit)
    jobs.main(['submit', str(tmp_path/'run'), '--job', '26992', '--cpus', '4',
               '--wall-seconds', '121', '--', '--workers', '2', '--tasks', 'task-a'])
    command = captured['argv']
    assert str(review_tools) in command
    assert command[:2] == ['srun', '--jobid=26992']
    assert '--immediate=15' in command and '--time=3' in command
    assert command[-4:] == ['--workers', '2', '--tasks', 'task-a']
    assert captured['wall_seconds'] == 121
