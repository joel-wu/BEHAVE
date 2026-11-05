"""Qualify frozen B/R or B/B pairs through Sim's public baseline command.

No task repairs, search, bounded analysis, or saved-score reuse. Batches only
schedule work; all comparison, goal observation, and score decisions belong to
Sim. Execution stays node-local; compact receipts are published after each task.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def task_order(root):
    """Round-robin categories, stable lexical order within each category."""
    groups = [sorted(p for p in domain.iterdir() if p.is_dir())
              for domain in sorted(root.iterdir()) if domain.is_dir()]
    return [group[index] for index in range(max(map(len, groups), default=0))
            for group in groups if index < len(group)]


def snapshot(root, destination, batch_size, *, rtl_entries=None,
             task_paths=None, behavior_entry='behavior.py', runtime_contracts=None):
    from tools.dataset_runtime_snapshot import freeze

    tasks = task_order(root / 'data/problems')
    if Path(behavior_entry).name != behavior_entry or not behavior_entry.endswith('.py'):
        raise ValueError('behavior entry must be a task-local Python filename')
    if task_paths is not None:
        requested = set(task_paths)
        known = {task.relative_to(root).as_posix() for task in tasks}
        if not requested or requested - known:
            raise ValueError(f'unknown or empty task selection: {sorted(requested - known)}')
        tasks = [task for task in tasks if task.relative_to(root).as_posix() in requested]
    selections = ['src/behave', 'pyproject.toml',
                  'native/rtl_frontend', 'scripts/setup_rtl_frontend.sh',
                  'tools/qualify_dataset_baseline.py', 'tools/dataset_runtime_snapshot.py',
                  'tools/profile_dataset_command.py',
                  'tools/measure_search_score.py']
    entries = {}
    if runtime_contracts is not None:
        relative = Path(runtime_contracts)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('runtime contracts must be a repository-relative JSON file')
        contracts = json.loads((root / relative).read_text())
        selected = {task.relative_to(root).as_posix() for task in tasks}
        if type(contracts) is not dict or set(contracts) != selected:
            raise ValueError('runtime contracts must cover exactly the selected tasks')
        if any(type(image) is not dict for image in contracts.values()):
            raise ValueError('each runtime contract must be a complete TaskContract image')
        selections.append(relative.as_posix())
    if rtl_entries is not None:
        relative = Path(rtl_entries)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('RTL entries must be a repository-relative JSON file')
        entries = json.loads((root / relative).read_text())
        if type(entries) is not dict or any(type(top) is not str or not top for top in entries.values()):
            raise ValueError('RTL entries map task paths to explicit, nonempty top symbols')
        unknown = set(entries) - {task.relative_to(root).as_posix() for task in tasks}
        if unknown:
            raise ValueError(f'RTL entries name tasks outside this snapshot: {sorted(unknown)}')
        selections.append(relative.as_posix())
    rows = []
    for index, task in enumerate(tasks):
        required = tuple(dict.fromkeys(('spec.md', 'behavior.py', 'rtl.sv',
                                       behavior_entry)))
        files = [p for p in task.rglob('*') if p.is_file() and '__pycache__' not in p.parts and
                 (p.name in (*required, 'task.toml', 'interface.json') or
                  p.suffix in ('.py', '.sv', '.v', '.svh', '.vh', '.mem', '.hex'))]
        selections.extend(p.relative_to(root).as_posix() for p in sorted(files))
        rows.append({'task': task.relative_to(root).as_posix(),
                     'category': task.parent.name, 'batch': index // batch_size + 1,
                     'behavior_entry': behavior_entry,
                     'missing': [name for name in required if not (task / name).is_file()]})
        if rows[-1]['task'] in entries:
            rows[-1]['rtl_top'] = entries[rows[-1]['task']]
        if runtime_contracts is not None:
            rows[-1]['runtime_contract_file'] = str(runtime_contracts)
    manifest = freeze(root, destination, tuple(selections))
    for row in rows:
        prefix = row['task'] + '/'
        row['files'] = {p.removeprefix(prefix): digest for p, digest in manifest['files'].items()
                        if p.startswith(prefix)}
        if runtime_contracts is not None:
            row['runtime_contract_file_sha256'] = manifest['files'][str(runtime_contracts)]
    plan = {'schema': 'dataset-baseline-plan/2', 'created_at': datetime.now(UTC).isoformat(),
            'runtime_digest': manifest['digest'], 'batch_size': batch_size,
            'rtl_entries': None if rtl_entries is None else str(rtl_entries),
            'categories': dict(Counter(row['category'] for row in rows)), 'tasks': rows,
            'scope': 'Frozen task sources; public B/R or B/B baseline; search=0; analysis=false'}
    write(destination / 'plan.json', plan)
    return plan


def frozen_runtime_contract(checkout, row):
    """Read task-owned authority, never infer it from candidate behavior/RTL.

    Explicit runtime qualification does not consult legacy task.toml. A missing,
    changed or invalid contract is an error, not permission to fall back to it.
    """
    from behave.sim import api

    relative = Path(row['runtime_contract_file'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('runtime contract file must remain inside the frozen checkout')
    raw = (checkout / relative).read_bytes()
    if hashlib.sha256(raw).hexdigest() != row['runtime_contract_file_sha256']:
        raise ValueError('frozen runtime contract file changed')
    return api.TaskContract.from_dict(json.loads(raw)[row['task']])


def decision_status(decision):
    if decision is None or not decision.authoritative:
        return 'no-result'
    return 'scored-pass' if decision.passed else 'scored-fail'


def behavior_bundle(directory, entry):
    """Use declared entry and the same module linker as public Eval."""
    from behave.data.spec_format import behavior_entry
    from behave.sim import api

    model = behavior_entry((directory / 'spec.md').read_text())
    files = {path.relative_to(directory).as_posix(): path.read_bytes()
             for path in sorted(directory.rglob('*.py')) if '__pycache__' not in path.parts}
    return api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, entry, model))


def worker(checkout, work, row, artifact_kind='rtl', *, profile_replay=False):
    """One fresh public invocation. No evaluator-specific error-code guessing."""
    from behave.pipelines.public_inputs import rtl_interface
    from behave.sim import api
    from behave.sim.rtl.tool_protocol import ToolIsolationError
    from tools.measure_search_score import replay_summary

    began = time.monotonic()
    record = dict(row, schema='dataset-baseline-result/2', status='running', phases=[], artifact_kind=artifact_kind,
                  search_seconds=0, run_analysis=False, profile_replay=profile_replay,
                  command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION,
                  allocated_cpus=sorted(os.sched_getaffinity(0)), scoring_policy=api.ScoringPolicy().as_dict())
    work.mkdir(parents=True, exist_ok=False)

    def phase(name, operation):
        record['phase'] = name
        write(work / 'progress.json', record)
        start = time.monotonic()
        value = operation()
        record['phases'].append({'phase': name, 'seconds': time.monotonic() - start})
        return value

    try:
        missing = [name for name in row['missing']
                   if artifact_kind == 'rtl' or name != 'rtl.sv']
        if missing:
            record['status'] = 'incomplete-task'
            return record
        task_dir = checkout / row['task']
        if 'runtime_contract_file' in row:
            record['contract_source'] = 'frozen-runtime-contract'
            contract = phase('contract', lambda: frozen_runtime_contract(checkout, row))
        else:
            record['contract_source'] = 'task-interface'
            declarations = phase('declarations', lambda: api.declarations_at(task_dir))
            contract = phase('contract', lambda: api.contract_of_task(task_dir, declarations=declarations))
        record['generation_spec'] = None
        record['contract_digest'] = contract.digest
        reference_bundle = phase('reference-bundle', lambda: behavior_bundle(task_dir, 'behavior.py'))
        reference_sources = {'behavior.py': reference_bundle.source.encode(), **reference_bundle.dependencies}
        reference = phase('reference', lambda: api.load_reference(
            api.ISOLATED_REFERENCE_ADAPTER, reference_sources, config={'class': reference_bundle.model},
            loader='dataset baseline qualification'))
        if artifact_kind == 'rtl':
            files = {path.relative_to(task_dir).as_posix(): path.read_bytes()
                     for path in sorted(task_dir.rglob('*'))
                     if path.is_file() and path.suffix in {'.sv', '.v', '.svh', '.vh'}}
            prepared = phase('rtl-bundle', lambda: api.prepare_rtl_bundle(api.bundle_for_entry(files, 'rtl.sv')))
            source = prepared.source
            top = phase('rtl-entry', lambda: row.get('rtl_top') or rtl_interface((task_dir / 'spec.md').read_text())[0])
            record['rtl_entry_selection'] = 'explicit' if 'rtl_top' in row else 'task-specification'
            record['rtl_top'] = top
            candidate = work / 'prepared.sv'
            candidate.write_text(source)
            realization = phase('realization', lambda: api.realization_of_rtl_source(
                source, task_dir.name, top=top, contract=contract))
            config = api.RTLConfig(candidate, top, work / 'artifact', profile_replay=profile_replay)
            prepare = api.prepare_rtl
        else:
            entry = row.get('behavior_entry', 'behavior.py')
            record['candidate_entry'] = entry
            prepared = phase('candidate-bundle', lambda: behavior_bundle(task_dir, entry))
            candidate = work / 'prepared.py'
            candidate.write_text(prepared.source)
            for name, data in prepared.dependencies.items():
                dependency = work / name
                dependency.parent.mkdir(parents=True, exist_ok=True)
                dependency.write_bytes(data)
            realization = phase('realization', lambda: api.realization_of_behavior_source(
                prepared.source, contract, file=prepared.file, model=prepared.model))
            config = api.BehaviorConfig(candidate, file=prepared.file, model=prepared.model)
            prepare = api.prepare_behavior
        admitted = phase('admission', lambda: api.admit(contract, realization, reference=reference))
        handle = phase('prepare', lambda: prepare(admitted, config))
        policy = api.BaselinePolicy()
        record['baseline_policy'] = policy.as_dict()
        command = phase('baseline', lambda: handle.command_request(
            run_directory=work / 'command', baseline=policy, reference=reference,
            search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
        record['baseline'] = {
            'requested': policy.case_limit, 'deduplicated': len(command.baseline.stimuli),
            'schedule': 'runtime-request' if command.baseline.generation_spec is None else 'fixed-domain',
            'queue_depths': {endpoint.name: sorted({seed.depth(endpoint.name)
                for seed in command.baseline.stimuli}) for endpoint in contract.inputs()},
        }
        record['baseline']['requested_length_satisfied'] = all(
            depths == [policy.max_queue_depth] for depths in record['baseline']['queue_depths'].values())
        result = phase('score', lambda: api.handle_command(command))
        record.update(status=decision_status(result.decision),
                      decision=None if result.decision is None else result.decision.as_dict(),
                      command_result_file='command-result.json.gz')
        record['elapsed_s'] = time.monotonic() - began
        record['diagnostics'] = 'exporting'
        write(work / 'result.json', record)
        # Keep full returned evidence, but do not make every progress/review read
        # parse it or publish its expanded JSON across NFS. Compression is local;
        # this is storage layout, not an evidence quota or score recomputation.
        with gzip.open(work / record['command_result_file'], 'wt', encoding='utf-8') as stream:
            json.dump(result.as_dict(), stream, separators=(',', ':'))
        if result.evaluation is not None:
            evaluation = result.evaluation
            record['evaluation'] = {'complete': evaluation.complete, 'passed': evaluation.passed,
                'accepted': evaluation.accepted, 'mismatch_count': evaluation.mismatch_count,
                'safety_hits': list(evaluation.safety_hits), 'runs': len(evaluation.results)}
            record['replay'] = replay_summary(evaluation, command.baseline.stimuli)
        if result.record_path and Path(result.record_path).is_file():
            with Path(result.record_path).open('rb') as src, gzip.open(work / 'command.json.gz', 'wb') as dst:
                shutil.copyfileobj(src, dst)
        record['diagnostics'] = 'complete'
    except api.ScoringStopped:
        record.update(status='no-result', reason='scoring-budget-exhausted-before-decision')
    except (api.Refused, api.FailureRaised) as error:
        record.update(status='no-result', error=str(error))
        for name in ('failure', 'refusal'):
            value = getattr(error, name, None)
            if value is not None:
                record[name] = value.as_dict()
        # Pre-command candidate-failure evidence is retained, not promoted into
        # an invented completed score by this diagnostic tool.
    except ToolIsolationError as error:
        # A status label such as "rtl-tool-exited" does not contain the tool's
        # original stderr/refusal. Preserve that evidence, without inventing a
        # public score for a call which failed before returning a decision.
        record.update(status='harness-error', error=traceback.format_exc(),
                      tool_failure={'code': error.code, 'why': error.why,
                                    'evidence_file': 'tool-failure.json.gz'})
        with gzip.open(work / 'tool-failure.json.gz', 'wt', encoding='utf-8') as stream:
            json.dump([execution.as_dict() for execution in error.executions], stream,
                      separators=(',', ':'))
    except Exception:  # noqa: BLE001 -- diagnostic failures must not become scores
        if 'decision' in record:
            record.update(diagnostics='failed', diagnostic_error=traceback.format_exc())
        else:
            record.update(status='harness-error', error=traceback.format_exc())
    finally:
        record['elapsed_s'] = time.monotonic() - began
        write(work / 'result.json', record)
    return record


def run_one(checkout, work_root, row, seconds, cpus, artifact_kind='rtl'):
    from behave.agent.cpu_resources import current_cpu_lease
    from behave.core import runtime_defaults as defaults
    from behave.isolation.service import run_service

    work = work_root / row['task'].removeprefix('data/problems/')
    work.parent.mkdir(parents=True, exist_ok=True)
    log = work.with_suffix('.log')
    env = {name: os.environ[name] for name in ('PATH', 'BEHAVE_EDA_ROOT', 'BEHAVE_RUNTIME_DIR',
           'BEHAVE_SCRATCH_DIR') if name in os.environ}
    env.update(PYTHONPATH=str(checkout / 'src') + ':' + str(checkout),
               SLURM_CPUS_PER_TASK=str(cpus), PYTHONDONTWRITEBYTECODE='1', LC_ALL='C.UTF-8')
    began = time.monotonic()
    deadline = began + seconds
    got = run_service((sys.executable, '-B', str(checkout / 'tools/qualify_dataset_baseline.py'),
        '_worker', str(checkout), str(work), row['task'], '--artifact', artifact_kind,
        '--score-deadline', str(deadline)), cwd=checkout, environment=env,
        deadline=deadline, output_bytes=None, cleanup_seconds=defaults.SERVICE_CLEANUP_SECONDS)
    lease = current_cpu_lease()
    if lease is not None:
        lease.cleanup = got.cleanup_state
    with log.open('xb') as stream:
        stream.write(got.stdout)
        stream.write(got.stderr)
    result_path = work / 'result.json'
    result = (json.loads(result_path.read_text()) if result_path.exists() else
              dict(row, status='no-result', reason='qualification-wall-timeout' if got.status == 'timeout'
                   else 'worker-exited-without-result', elapsed_s=time.monotonic() - began))
    result['supervision'] = {'reason': got.status, 'wall_seconds': seconds,
        'returncode': got.returncode, 'cleanup': 'complete' if got.cleanup_state.quiescent else 'unconfirmed',
        'elapsed_s': got.elapsed_seconds, 'cleanup_elapsed_s': got.cleanup_elapsed_seconds}
    write(result_path, result)
    return work, log


@dataclass(frozen=True)
class PreparedQualification:
    plan: dict
    runtime_directory: Path
    native_frontend_sha256: str


def _prepare_qualification(checkout):
    """Verify and prepare once, before starting either lane's workers."""
    from behave.core.process_limits import configure_worker_process_limit
    from tools.dataset_runtime_snapshot import verify

    configure_worker_process_limit()
    verify(checkout)
    from behave.sim import api

    plan = json.loads((checkout / 'plan.json').read_text())
    runtime = api.prepare_runtime(profiles=(), directory=(
        os.environ.get('BEHAVE_RUNTIME_DIR') or checkout.parent / 'runtime'))
    tool = Path(os.environ['BEHAVE_EDA_ROOT']) / 'libexec/behave-rtl-frontend'
    return PreparedQualification(plan, runtime.directory, hashlib.sha256(tool.read_bytes()).hexdigest())


def run(checkout, work_root, output, batches, workers, cpus, seconds, *, artifact_kind='rtl', resume=False):
    prepared = _prepare_qualification(checkout)
    _run_lane(checkout, work_root, output, batches, workers, cpus, seconds,
              prepared=prepared, artifact_kind=artifact_kind, resume=resume)


def _run_lane(checkout, work_root, output, batches, workers, cpus, seconds, *,
              prepared, artifact_kind, resume):
    from behave.agent.cpu_resources import CpuResources, cpu_job

    resources = CpuResources(workers, cpus=workers * cpus)
    plan = prepared.plan
    output.mkdir(parents=True, exist_ok=True)
    prior_plan = output / 'plan.json'
    if prior_plan.exists():
        if not resume:
            raise FileExistsError('qualification exists; use --resume with the same frozen snapshot')
        previous = json.loads(prior_plan.read_text())
        launch = json.loads((output / 'launch.json').read_text())
        if (previous != plan or launch['artifact_kind'] != artifact_kind):
            raise ValueError('resume requires the same frozen plan and candidate kind')
        shutil.copyfile(output / 'launch.json', output / f'launch-before-resume-{uuid.uuid4().hex}.json')
    write(output / 'plan.json', plan)
    write(output / 'launch.json', {'workers': workers, 'cpus_per_task': cpus,
        'wall_seconds_per_task': seconds, 'batches': batches, 'artifact_kind': artifact_kind,
        'tool_root': os.environ.get('BEHAVE_EDA_ROOT'), 'work_root': str(work_root),
        'native_frontend_sha256': prepared.native_frontend_sha256,
        'affinity': sorted(os.sched_getaffinity(0)), 'runtime_digest': plan['runtime_digest'],
        'resume': resume, 'scheduling': 'cpu-resources-disjoint-leases-across-report-batches',
        'runtime_directory': str(prepared.runtime_directory)})
    write(output / 'resources.json', resources.snapshot())
    selected = [row for row in plan['tasks'] if row['batch'] in batches]
    expected = Counter(row['batch'] for row in selected)
    completed = {batch: [] for batch in batches}

    def record(row, result):
        batch = row['batch']
        summary = {'task': row['task'], 'status': result['status'],
                   'elapsed_s': result['elapsed_s'], 'phase': result.get('phase')}
        completed[batch].append(summary)
        write(output / f'batch-{batch:03}.json', {
            'batch': batch, 'expected': expected[batch], 'completed': len(completed[batch]),
            'counts': dict(Counter(r['status'] for r in completed[batch])), 'tasks': completed[batch]})
        return summary

    todo = []
    for row in selected:
        destination = output / 'tasks' / row['task'].removeprefix('data/problems/')
        result_path = destination / 'result.json'
        if resume and result_path.is_file() and (destination / 'worker.log').is_file():
            result = json.loads(result_path.read_text())
            # These are receipts from this same frozen qualification, not a cross-run score cache.
            record(row, result)
        else:
            if destination.exists():
                destination.rename(destination.with_name(destination.name + '.interrupted-' + uuid.uuid4().hex))
            todo.append(row)
    print(json.dumps({'artifact_kind': artifact_kind, 'remaining': len(todo),
                      'retained_receipts': len(selected) - len(todo), 'workers': workers}), flush=True)
    # Batches are reporting groups, not barriers. A long task cannot hold up the next group.
    def execute(row):
        with cpu_job(resources):
            return run_one(checkout, work_root, row, seconds, cpus, artifact_kind)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(execute, row): row for row in todo}
        for future in as_completed(pending):
            row = pending[future]
            work, log = future.result()
            destination = output / 'tasks' / row['task'].removeprefix('data/problems/')
            stage = destination.with_name('.' + destination.name + '.publishing-' + uuid.uuid4().hex)
            stage.mkdir(parents=True)
            for name in ('result.json', 'progress.json', 'command.json.gz', 'command-result.json.gz',
                         'tool-failure.json.gz'):
                if (work / name).is_file():
                    shutil.copyfile(work / name, stage / name)
            shutil.copyfile(log, stage / 'worker.log')
            stage.rename(destination)  # Publish the complete receipt directory atomically.
            result = json.loads((work / 'result.json').read_text())
            print(json.dumps(record(row, result)), flush=True)
            write(output / 'resources.json', resources.snapshot())


def run_pairs(checkout, work_root, output, batches, *, rtl_workers, behavior_workers,
              rtl_cpus, behavior_cpus, seconds, resume=False):
    """Both public paths, one prepared runtime, explicitly shared allocation."""
    lanes = (('rtl', rtl_workers, rtl_cpus), ('behavior', behavior_workers, behavior_cpus))
    requested = sum(workers * cpus for _, workers, cpus in lanes)
    available = sorted(os.sched_getaffinity(0))
    if requested > len(available):
        raise ValueError(f'paired qualification requests {requested} CPUs, allocation exposes {len(available)}')
    prepared = _prepare_qualification(checkout)
    output.mkdir(parents=True, exist_ok=True)
    scope = output / 'scope.json'
    if scope.exists():
        if not resume:
            raise FileExistsError('paired qualification exists; use --resume with the same frozen snapshot')
        previous = json.loads(scope.read_text())
        if previous['runtime_digest'] != prepared.plan['runtime_digest'] or previous['batches'] != batches:
            raise ValueError('paired resume requires the same frozen qualification and batches')
        scope.rename(output / f'scope-before-resume-{uuid.uuid4().hex}.json')
    write(scope, {
        'schema': 'paired-baseline-qualification/1',
        'snapshot_root': str(checkout), 'runtime_digest': prepared.plan['runtime_digest'],
        'runtime_directory': str(prepared.runtime_directory), 'work_root': str(work_root),
        'batches': batches, 'tasks': sum(row['batch'] in batches for row in prepared.plan['tasks']),
        'affinity': available, 'requested_cpus': requested,
        'lanes': [{'kind': kind, 'workers': workers, 'cpus_per_task': cpus}
                  for kind, workers, cpus in lanes],
        'search_seconds': 0, 'analysis': False, 'resume_same_qualification': resume,
        'behavior_reference': 'behavior.py',
        'behavior_candidate': prepared.plan['tasks'][0].get('behavior_entry', 'behavior.py'),
    })
    def lane(kind, workers, cpus, assigned):
        before = os.sched_getaffinity(0)
        try:
            os.sched_setaffinity(0, assigned)
            return _run_lane(checkout, work_root / kind, output / kind, batches, workers, cpus,
                             seconds, prepared=prepared, artifact_kind=kind, resume=resume)
        finally:
            os.sched_setaffinity(0, before)

    with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        futures = {}
        offset = 0
        for kind, workers, cpus in lanes:
            assigned = available[offset:offset + workers * cpus]
            offset += len(assigned)
            futures[pool.submit(lane, kind, workers, cpus, assigned)] = kind
        for future in as_completed(futures):
            future.result()
    write(output / 'lanes.json', [{'artifact_kind': kind, 'completed': True} for kind, _, _ in lanes])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    stage = sub.add_parser('snapshot')
    stage.add_argument('root', type=Path)
    stage.add_argument('destination', type=Path)
    stage.add_argument('--batch-size', type=int, default=50)
    stage.add_argument('--tasks', nargs='+', help='Explicit repository-relative task directories')
    stage.add_argument('--behavior-entry', default='behavior.py',
                       help='B/B candidate file, distinct from the behavior.py reference when specified')
    stage.add_argument('--rtl-entries', type=Path,
                       help='Repository-relative JSON mapping task paths to explicit RTL top symbols; '
                            'frozen with source bytes, not guessed from module order.')
    stage.add_argument('--runtime-contracts', type=Path,
                       help='Repository-relative JSON mapping every selected task to its reviewed '
                            'TaskContract image. Frozen task authority replaces legacy declarations; '
                            'invalid or missing images do not fall back to task.toml.')
    execute = sub.add_parser('run')
    execute.add_argument('checkout', type=Path)
    execute.add_argument('work', type=Path)
    execute.add_argument('output', type=Path)
    execute.add_argument('--batches', type=int, nargs='+', required=True)
    execute.add_argument('--workers', type=int, required=True)
    execute.add_argument('--cpus-per-task', type=int, required=True)
    execute.add_argument('--artifact', choices=('rtl', 'behavior'), default='rtl',
                         help='Candidate kind; both use a separately executed task Behavior reference. '
                              'The behavior lane uses the frozen plan\'s behavior entry. '
                              'The default behavior.py entry checks only self-consistency.')
    execute.add_argument('--resume', action='store_true',
                         help='Retain completed receipts of this exact frozen qualification; '
                              'use a fresh work directory for unfinished tasks.')
    execute.add_argument('--task-seconds', type=int,
                         help='Whole-task diagnostic deadline from runtime defaults; '
                              'expiry is no result, not a score.')
    paired = sub.add_parser('run-pairs')
    paired.add_argument('checkout', type=Path)
    paired.add_argument('work', type=Path)
    paired.add_argument('output', type=Path)
    paired.add_argument('--batches', type=int, nargs='+', required=True)
    for name in ('rtl-workers', 'behavior-workers', 'rtl-cpus', 'behavior-cpus'):
        paired.add_argument('--' + name, type=int, required=True)
    paired.add_argument('--task-seconds', type=int,
                        help='Whole-task diagnostic deadline; defaults to the selected Sim runtime')
    paired.add_argument('--resume', action='store_true')
    single = sub.add_parser('_worker')
    single.add_argument('checkout', type=Path)
    single.add_argument('work', type=Path)
    single.add_argument('task')
    single.add_argument('--artifact', choices=('rtl', 'behavior'), default='rtl')
    single.add_argument('--score-deadline', type=float)
    args = parser.parse_args()
    if args.action == 'snapshot' and args.batch_size < 1:
        parser.error('batch size must be positive')
    root = args.root if args.action == 'snapshot' else args.checkout
    sys.path[:0] = [str(root), str(root / 'src')]
    if args.action in {'run', 'run-pairs'}:
        # Import only after choosing the frozen runtime. Importing behave above
        # this bootstrap could bind the live worktree package for an old snapshot.
        from behave.core import runtime_defaults

        if args.task_seconds is None:
            args.task_seconds = runtime_defaults.SIM_BASELINE_QUALIFICATION_SECONDS
        resources = ([args.workers, args.cpus_per_task] if args.action == 'run' else
                     [args.rtl_workers, args.behavior_workers, args.rtl_cpus, args.behavior_cpus])
        if min(*resources, args.task_seconds, *args.batches) < 1:
            parser.error('batch numbers, workers, CPUs and the diagnostic deadline must be positive')
    if args.action == 'snapshot':
        plan = snapshot(root, args.destination, args.batch_size, rtl_entries=args.rtl_entries,
                        task_paths=args.tasks, behavior_entry=args.behavior_entry,
                        runtime_contracts=args.runtime_contracts)
        print(json.dumps({'tasks': len(plan['tasks']), 'categories': plan['categories'],
                          'runtime_digest': plan['runtime_digest']}))
    elif args.action == 'run':
        run(args.checkout, args.work, args.output, args.batches,
            args.workers, args.cpus_per_task, args.task_seconds, artifact_kind=args.artifact,
            resume=args.resume)
    elif args.action == 'run-pairs':
        run_pairs(args.checkout, args.work, args.output, args.batches,
                  rtl_workers=args.rtl_workers, behavior_workers=args.behavior_workers,
                  rtl_cpus=args.rtl_cpus, behavior_cpus=args.behavior_cpus,
                  seconds=args.task_seconds, resume=args.resume)
    else:
        from behave.core import runtime_defaults
        from behave.core.resources import allocated_cpus
        from behave.sim import api

        plan = json.loads((args.checkout / 'plan.json').read_text())
        row = next(row for row in plan['tasks'] if row['task'] == args.task)
        policy = api.ScoringPolicy()
        deadline = min(time.monotonic() + policy.time_budget_s,
                       args.score_deadline if args.score_deadline is not None else float('inf'))
        with (api.CpuPool(capacity=len(allocated_cpus())) as pool,
              api.cpu_scope(pool.endpoint, deadline=deadline),
              api.scoring_scope(policy, deadline=deadline - runtime_defaults.EVAL_SCORE_FINALIZE_SECONDS)):
            worker(args.checkout, args.work, row, args.artifact)


if __name__ == '__main__':
    main()
