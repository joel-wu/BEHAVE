"""Check reference RTL with the shared comparison and PPA services.

Run on a compute node with a fresh node-local output directory and the chosen
experiment clock. This checks the reference RTL without making model calls.
Success requires functional agreement and timing closure at that clock.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
TASK = 'ai_ml_ssm_deltanet_gated_deltanet_step'
CLASS = 'AiMlSsmDeltanetGatedDeltanetStep'


def stage_passed(stage, result):
    if stage == 'eda':
        eda = result.get('eda', {})
        return eda.get('status') == 'measured' and (eda.get('timing') or {}).get('status') == 'met'
    if stage in ('performance', 'mapped'):
        performance = result.get('performance') or {}
        elapsed = performance.get('time_ns')
        return (result.get('score') == 1 and performance.get('score') == 1
                and type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed > 0)
    return result.get('score') == 1


def workload_rows(length):
    from behave.ppa.planning import gated_deltanet_rows, read_study

    study = read_study(REPO / 'configs/ppa/gated_deltanet_preflight.json')
    return gated_deltanet_rows(length, study['workloads'][0]['sequence_tokens'])


def summary(result):
    row = {key: result.get(key) for key in ('score', 'code', 'performance')}
    if 'eda' in result:
        row['eda'] = {key: result['eda'].get(key) for key in
                      ('status', 'area_um2', 'timing', 'stages', 'failed_stage')}
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--stages', nargs='+', choices=('check', 'performance', 'eda', 'mapped'),
                        default=['check', 'eda', 'performance'])
    parser.add_argument('--study', type=Path, default=REPO / 'configs/ppa/gated_deltanet_preflight.json')
    parser.add_argument('--rtl', type=Path, help='private calibration RTL; task interface and golden stay published')
    parser.add_argument('--liberty', type=Path, help='override the selected technology Liberty')
    parser.add_argument('--sta', type=Path, default=REPO/'external/ppa-activity/opensta/usr/bin/sta')
    parser.add_argument('--clock-period-ns', type=float)
    parser.add_argument('--baseline-cases', type=int)
    parser.add_argument('--sequence-length', type=int, help='override only the correctness-baseline sequence length')
    parser.add_argument('--workload-tokens', type=int, help='override only the performance workload length')
    parser.add_argument('--sequence-tokens', type=int, help='tokens between sequence-start markers')
    parser.add_argument('--service-seconds', type=int)
    parser.add_argument('--synthesis-seconds', type=int)
    parser.add_argument('--objects', type=Path, help='published task object store')
    parser.add_argument('--release', type=Path, help='published task release reference')
    parser.add_argument('--cpus', type=int, help='total CPU reservation for this preflight')
    parser.add_argument('--parallel', action='store_true', help='run independent EDA and source-RTL measurement together')
    args = parser.parse_args()
    if bool(args.objects) != bool(args.release):
        parser.error('--objects and --release must be supplied together')
    if args.parallel and (set(args.stages) != {'eda', 'performance'} or len(args.stages) != 2):
        parser.error('--parallel requires exactly the eda and performance stages')
    if 'mapped' in args.stages and ('eda' not in args.stages or
                                  args.stages.index('eda') > args.stages.index('mapped')):
        parser.error('mapped replay requires an earlier eda stage')

    from behave.core.artifacts import BlobRef, DirectoryReader, FileData, FileRef, LoadedSource, SourceArtifact
    from behave.core.records import canonical_json
    from behave.core.storage import DirectoryStore
    from behave.ppa.planning import build_experiment, read_study
    from behave.ppa.worker import NativePPA
    from behave.sim import api
    from behave.task_execution.comparison import ComparisonInputs
    from behave.task_execution.judge_policy import JudgePolicy

    study = read_study(args.study)
    task_id = study['task']
    if not args.release and task_id != TASK:
        parser.error('other tasks require a published --objects and --release binding')
    if args.clock_period_ns is not None:
        study['measurement']['timing']['period_ns'] = args.clock_period_ns
    if args.baseline_cases is not None:
        study['checks']['baseline_cases'] = args.baseline_cases
    if args.sequence_length is not None:
        study['checks']['sequence_length'] = args.sequence_length
    if args.workload_tokens is not None:
        study['workloads'][0]['tokens'] = args.workload_tokens
    if args.sequence_tokens is not None:
        study['workloads'][0]['sequence_tokens'] = args.sequence_tokens
    if args.service_seconds is not None:
        study['measurement']['service_seconds'] = args.service_seconds
    if args.synthesis_seconds is not None:
        study['measurement']['eda_budget']['synthesis_seconds'] = args.synthesis_seconds
    source = REPO / 'data/problems/ai_ml' / task_id
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    objects = DirectoryStore(output / 'objects')

    def file(name, data):
        return FileData(FileRef(name, BlobRef.of(data)), data)

    def bundle(filename, symbol):
        path = args.rtl if filename == 'rtl.sv' and args.rtl is not None else source / filename
        data = file(filename, path.read_bytes())
        return LoadedSource(SourceArtifact((data.reference,), filename, symbol), (data,))

    published = None
    if args.release:
        from behave.data.published import load_published_task
        from behave.ppa.records import release_reference
        from behave.core.records import parse_json
        published = load_published_task(DirectoryReader(args.objects), release_reference(args.release), task_id)
        contract = api.TaskContract.from_dict(parse_json(published.judge.contract.data))
    else:
        contract = api.contract_of_task(source, declarations=api.declarations_at(source))
    experiment = build_experiment(study, contract)
    judge = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, **study['checks'], search_seconds=0,
                        score_timeout_seconds=experiment.service_seconds, max_retries=0, analysis=None)
    inputs = ComparisonInputs('rtl', bundle('rtl.sv', task_id),
        bundle('behavior.py', CLASS) if published is None else published.judge.reference_behavior,
        file('contract.json', canonical_json(contract.as_dict())) if published is None else published.judge.contract,
        b'null' if published is None else published.judge.run_policy, judge.to_bytes())
    from behave.agent.cpu_resources import CpuResources
    from behave.ppa.technology import technology
    native = NativePPA(liberty=args.liberty or technology(experiment.mapping.technology).liberty,
                       sta=args.sta, technology_name=experiment.mapping.technology,
                       cpu_resources=CpuResources(1, cpus=args.cpus))
    record = {'task': task_id, 'purpose': 'reference-preflight', 'state': 'running', 'input': inputs.as_dict(),
              'study': study,
              'experiment': experiment.as_dict(), 'native': native.identity, 'results': {}}
    if published is not None:
        record['release'] = published.public.release.as_dict()
        record['task_revision'] = published.public.revision.as_dict()
    if args.rtl is not None:
        record['calibration_rtl'] = str(args.rtl.resolve())
    inputs.stage(objects)
    (output/'result.json').write_bytes(canonical_json(record))
    pool = ()
    if args.parallel:
        print(json.dumps({'stages': args.stages, 'state': 'started'}), flush=True)
        try:
            measured = native.run_many(inputs, experiment, tuple(args.stages), pool, objects)
            record['results'] = {stage: result for stage, (result, _) in measured.items()}
            record['elapsed_seconds'] = {stage: elapsed for stage, (_, elapsed) in measured.items()}
            record['state'] = 'finished'
        except Exception as error:
            record.update(state='interrupted', error={'type': type(error).__name__, 'message': str(error)})
            raise
        finally:
            (output/'result.json').write_bytes(canonical_json(record))
        print(json.dumps({'state': record['state'], 'results': {
            stage: summary(row) for stage, row in record['results'].items()}}), flush=True)
        return int(not all(stage_passed(stage, result) for stage, result in record['results'].items()))
    for stage in args.stages:
        print(json.dumps({'stage': stage, 'state': 'started'}), flush=True)
        try:
            stage_inputs = inputs
            if stage == 'mapped':
                image = record['results']['eda'].get('eda', {}).get('simulation_source')
                if image is None:
                    raise ValueError('EDA did not produce a mapped simulation source')
                artifact = SourceArtifact.from_dict(image)
                mapped = LoadedSource(artifact, tuple(
                    FileData(ref, objects.read(ref.content)) for ref in artifact.files))
                stage_inputs = replace(inputs, source=mapped)
            result = native.run(stage_inputs, experiment, stage, pool, objects)
            if stage == 'check':
                pool = tuple(result.get('pool', ()))
        except Exception as error:
            record.update(state='interrupted', failed_stage=stage,
                          error={'type': type(error).__name__, 'message': str(error)})
            (output/'result.json').write_bytes(canonical_json(record))
            raise
        record['results'][stage] = result
        (output/'result.json').write_bytes(canonical_json(record))
        print(json.dumps(dict(stage=stage, state='finished', **summary(result))), flush=True)
    record['state'] = 'finished'
    (output/'result.json').write_bytes(canonical_json(record))
    return int(not all(stage_passed(stage, result) for stage, result in record['results'].items()))


if __name__ == '__main__':
    raise SystemExit(main())
