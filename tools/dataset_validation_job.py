"""Dataset-validation CLI; shared process ownership lives in behave.runtime.jobs."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.runtime.jobs import node_heartbeat, status_of, supervise
from behave.runtime.jobs import submit as submit_job

REPO = Path(__file__).resolve().parents[1]
__all__ = ["main", "node_heartbeat", "status_of", "submit", "supervise"]


def submit(directory, argv, *, wall_seconds, metadata,
           cleanup_seconds=...):
    """Compatibility entry for existing callers and archived launch commands."""
    return submit_job(directory, argv, cwd=REPO, wall_seconds=wall_seconds,
                      metadata=metadata, cleanup_seconds=cleanup_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    launch = sub.add_parser('submit')
    launch.add_argument('directory', type=Path)
    launch.add_argument('--job', type=int, required=True)
    launch.add_argument('--cpus', type=int, required=True)
    launch.add_argument('--wall-seconds', type=int, required=True)
    for command in ('status', '_supervise'):
        sub.add_parser(command).add_argument('directory', type=Path)
    argv = list(sys.argv[1:] if argv is None else argv)
    boundary = argv.index('--') if '--' in argv else len(argv)
    extra = argv[boundary + 1:]
    args = parser.parse_args(argv[:boundary])
    if args.action != 'submit' and extra:
        parser.error('only submit accepts validator arguments')
    if args.action == '_supervise':
        supervise(args.directory)
        return
    if args.action == 'status':
        print(json.dumps(status_of(args.directory), indent=2))
        return
    if min(args.job, args.cpus, args.wall_seconds) < 1:
        parser.error('job, cpus and wall-seconds must be positive')
    if '--tasks' not in extra:
        parser.error('name the batch explicitly with --tasks after --')
    validator = REPO / 'tools/validate_dataset_repairs.py'
    if not validator.is_file():
        parser.error('dataset validation requires the dataset review tools, which are not in the code-only release')
    directory = args.directory.resolve()
    python = REPO / '.venv/bin/python'
    environment = [
        'PATH=' + ':'.join(map(str, (python.parent, REPO/'external/oss-cad-suite/bin',
                                    '/usr/bin', '/bin'))),
        'PYTHONPATH=' + str(REPO/'src'), 'PYTHONUNBUFFERED=1']
    argv = ['srun', f'--jobid={args.job}', '--exact', '--cpu-bind=cores', '--gres=none',
            f'--immediate={defaults.JOB_SLURM_WAIT_SECONDS}',
            '-N1', '-n1', f'-c{args.cpus}', '--kill-on-bad-exit=1',
            '--time=' + str(math.ceil(args.wall_seconds / 60)),
            'env', *environment, str(python),
            str(validator),
            str(directory/'validation'), *extra]
    print(json.dumps(submit(directory, argv, wall_seconds=args.wall_seconds,
                            metadata={'job': args.job, 'cpus': args.cpus}), indent=2))


if __name__ == '__main__':
    main()
