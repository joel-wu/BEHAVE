"""Profile a frozen public baseline invocation, including cocotb Python work.

This uses the same qualifier/entry selection/default baseline as the population
run, not an example-specific case table. Profiling adds overhead. Its diagnostic
deadline never establishes a candidate failure or a passing dataset receipt.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import os
import pstats
import signal
import sys
import threading
import time
import traceback
from pathlib import Path


class DiagnosticTimeout(BaseException):
    """Do not let an inner evaluator normalize away the diagnostic stop."""


def _profile_summary(profiles, output):
    if not profiles:
        return None
    combined = pstats.Stats(*(str(path) for path in profiles))
    binary = output / 'combined.pstat'
    combined.dump_stats(str(binary))
    stream = io.StringIO()
    combined.stream = stream
    combined.strip_dirs().sort_stats('cumulative').print_stats(80)
    text = output / 'cumulative.txt'
    text.write_text(stream.getvalue())
    return {'binary': binary.name, 'text': text.name,
            'python_seconds': combined.total_tt, 'calls': combined.total_calls}


def main(output, root, task, seconds, *, artifact_kind='rtl'):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('diagnostic budget must be finite positive seconds')
    sys.path[:0] = [str(root), str(root / 'src')]
    from tools.dataset_runtime_snapshot import verify
    from tools.qualify_dataset_baseline import worker, write

    manifest = verify(root)
    plan = json.loads((root / 'plan.json').read_text())
    rows = [row for row in plan['tasks'] if row['task'] == task or Path(row['task']).name == task]
    if len(rows) != 1:
        raise ValueError('profile selects one task in the frozen qualification plan')
    output.mkdir(parents=True, exist_ok=False)
    began = time.monotonic()
    report = {'task': rows[0], 'scope': 'diagnostic public baseline; search=0; analysis=false',
              'state': 'running', 'diagnostic_budget_s': seconds,
              'runtime_digest': manifest['digest'], 'artifact_kind': artifact_kind,
              'profile_replay': artifact_kind == 'rtl', 'reused_score': False}
    write(output / 'profile-result.json', report)
    stopped = threading.Event()

    def expired(*_):
        if not stopped.is_set():
            raise DiagnosticTimeout('public command diagnostic budget expired')

    # Reference wrappers own SIGALRM. A distinct signal and a wall-clock
    # watchdog keep this diagnostic deadline independent of their alarms.
    previous = signal.signal(signal.SIGUSR2, expired)
    def watchdog():
        if not stopped.wait(seconds):
            os.kill(os.getpid(),signal.SIGUSR2)
    watcher = threading.Thread(target=watchdog, daemon=True)
    watcher.start()
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        report['qualification'] = worker(root, output / 'run', rows[0], artifact_kind,
                                         profile_replay=artifact_kind == 'rtl')
        report['state'] = 'finished'
    except (Exception, DiagnosticTimeout) as exc:  # noqa: BLE001 -- retain diagnostic failure, never invent a score
        report.update(state='diagnostic-timeout' if isinstance(exc,DiagnosticTimeout) else 'exception',
                      error=traceback.format_exc())
    finally:
        profiler.disable()
        stopped.set()
        watcher.join()
        signal.signal(signal.SIGUSR2, previous)
        try:
            profiler.dump_stats(str(output / 'parent.pstat'))
            report['parent'] = _profile_summary([output / 'parent.pstat'], output)
            profiles = sorted((output / 'run/artifact/profiles').glob('*.pstat'))
            native = output / 'simulator'
            native.mkdir()
            report['simulator'] = _profile_summary(profiles, native)
            report['simulator_profiles'] = [str(path.relative_to(output)) for path in profiles]
        except Exception:  # noqa: BLE001 -- diagnostic publication cannot erase the original workload outcome
            report['profile_error'] = traceback.format_exc()
            if report['state'] == 'finished':
                report['state'] = 'profile-error'
        report['elapsed_s'] = time.monotonic()-began
        write(output / 'profile-result.json', report)
    return int(report['state'] != 'finished')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('task')
    parser.add_argument('seconds', type=float, help='explicit diagnostic wall budget, not scoring policy')
    parser.add_argument('--artifact', choices=('rtl', 'behavior'), default='rtl')
    args = parser.parse_args()
    raise SystemExit(main(args.output, args.snapshot, args.task, args.seconds, artifact_kind=args.artifact))
