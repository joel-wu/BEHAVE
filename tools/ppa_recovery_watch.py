"""Wait for fenced provider attempts, then reuse the standard PPA group runner."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'src'), str(REPO)]

from behave.ppa.campaign_inputs import publish
from behave.ppa.planning import campaign_slots as slots
from behave.ppa.planning import validate_campaign as validate_plan
from behave.runtime.records import archive_run, write_json
from tools.ppa_campaign import submit_group


def archive_if_ready(source, prefix):
    """Copy only closed attempts; never take over a live sandbox or edit its tape."""
    prefix = prefix if isinstance(prefix, str) else tuple(prefix)
    runtime = json.loads((source / 'runtime.json').read_text())
    if runtime.get('storage') == 'shared':
        local = source / 'execution'
    elif runtime['host'] != socket.gethostname():
        raise ValueError('read the original node-local execution on its owning node')
    else:
        local = Path(runtime['local_directory'])
    if not local.exists():
        local = source / 'execution'
    selected = []
    for path in sorted((source / 'campaigns').glob('*/plan.json')):
        plan = validate_plan(json.loads(path.read_text()))
        for method, replica, target in slots(plan, path.parent):
            if not method['name'].startswith(prefix):
                continue
            origin = local / 'campaigns' / path.parent.name / 'runs' / method['name'] / f'r{replica:02d}'
            receipt = origin / 'search-service/service.json'
            if not receipt.exists():
                return {'state': 'waiting-original', 'pending': str(receipt)}
            result = json.loads(receipt.read_text())
            if result.get('cleanup') != 'complete':
                raise RuntimeError(f'original cleanup unconfirmed: {receipt}')
            selected.append((origin, target, result))
    if not selected:
        raise ValueError('no matching original searches')
    for origin, target, _ in selected:
        for name in ('search-service', 'search'):
            if (origin / name).exists():
                archive_run(origin / name, target / name)
    return {'state': 'ready', 'attempts': len(selected),
            'original_successes': sum(row['ok'] for _, _, row in selected)}


def reference_released(state):
    if state.get('state') == 'timed-out':
        if state.get('cleanup_state') != 'complete':
            raise RuntimeError('reference process cleanup unconfirmed; do not reuse its CPUs')
        return True
    if state.get('state') == 'launch-error':
        raise RuntimeError('reference launch failed; inspect its process ownership before recovery')
    return state.get('state') in {'completed', 'failed'}


def selected_methods(methods, prefixes, profiles):
    if not prefixes or len(prefixes) != len(profiles):
        raise ValueError('supply one profile for each provider prefix')
    selected = []
    for method in methods:
        matches = [profile for prefix, profile in zip(prefixes, profiles)
                   if method['name'].startswith(prefix)]
        if len(matches) > 1:
            raise ValueError('provider prefixes overlap')
        if matches:
            selected.append(dict(method, provider=str(matches[0].resolve())))
    return selected


def watch(args):
    source, queue = args.source.resolve(), args.queue.resolve()
    groups = {}
    for path in sorted((source / 'campaigns').glob('*/plan.json')):
        plan = validate_plan(json.loads(path.read_text()))
        selected = selected_methods(plan['methods'], args.prefix, args.profile)
        if not selected:
            continue
        plan.update(methods=selected, parallelism=args.parallelism)
        target = queue / 'prepared' / path.parent.name / 'plan.json'
        publish(target, plan)
        groups[path.parent.name] = str(target)
    group_path = queue / 'group.json'
    publish(group_path, {'schema': 'behave.ppa-campaign-group/1',
                         'plans': groups, 'cpu_workers': args.cpu_workers})
    while True:
        supervision = args.wait_supervision / 'status.json'
        state = json.loads(supervision.read_text()) if supervision.exists() else {}
        if reference_released(state):
            break
        write_json(queue / 'status.json', {'state': 'waiting-reference-cpus', 'reference': state.get('state')})
        time.sleep(30)
    while True:
        command = ['srun', f'--jobid={args.allocation}', '--overlap', '--nodes=1', '--ntasks=1',
                   '--cpus-per-task=1', '--time=00:10:00', sys.executable, '-B', __file__,
                   'archive-if-ready', '--source', str(source),
                   *[arg for prefix in args.prefix for arg in ('--prefix', prefix)]]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError('recovery observation/archive failed: ' + result.stderr[-4000:])
        observation = json.loads(result.stdout)
        write_json(queue / 'status.json', observation)
        if observation['state'] == 'ready':
            break
        time.sleep(30)
    launched = submit_group(group_path, args.output.resolve(), args.allocation, args.cpus,
                           runtime_directory=args.runtime_directory, cpu_offset=args.cpu_offset,
                           recover_from=source / 'campaigns')
    write_json(queue / 'status.json', {'state': 'submitted', 'original': observation, 'launch': launched})
    print(json.dumps(launched), flush=True)


def main():
    from behave.core.process_limits import configure_worker_process_limit
    configure_worker_process_limit()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['watch', 'archive-if-ready'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--prefix', action='append', required=True)
    parser.add_argument('--queue', type=Path)
    parser.add_argument('--profile', type=Path, action='append')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--wait-supervision', type=Path)
    parser.add_argument('--allocation', type=int)
    parser.add_argument('--cpus', type=int)
    parser.add_argument('--cpu-offset', type=int)
    parser.add_argument('--cpu-workers', type=int)
    parser.add_argument('--parallelism', type=int)
    parser.add_argument('--runtime-directory')
    args = parser.parse_args()
    if args.action == 'archive-if-ready':
        print(json.dumps(archive_if_ready(args.source.resolve(), args.prefix)))
    else:
        for key in ('queue', 'profile', 'output', 'wait_supervision', 'allocation', 'cpus',
                    'cpu_offset', 'cpu_workers', 'parallelism', 'runtime_directory'):
            if getattr(args, key) is None:
                parser.error(f'watch requires --{key.replace("_", "-")}')
        try:
            watch(args)
        except Exception as error:
            write_json(args.queue / 'status.json', {'state': 'failed', 'error': f'{type(error).__name__}: {error}'})
            raise


if __name__ == '__main__':
    main()
