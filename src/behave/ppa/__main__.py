"""Search, common-final validation and read-only reports for independent PPA."""

import argparse
import sys
from copy import copy
from dataclasses import replace
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.file_input import read_regular_file
from behave.core.records import canonical_json, parse_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="one model's search replicate")
    compare = commands.add_parser("compare", help="evaluate archived candidates under the selected experiment, without a model")
    prepare = commands.add_parser("prepare", help="compile one study config to frozen measurement and campaign inputs")
    for name in ("study", "objects", "release", "output"):
        prepare.add_argument("--" + name, required=True)
    prepare.add_argument("--methods", help="JSON methods array wrapper; relative paths use its directory; generates plan.json")
    prepare.add_argument("--liberty")
    prepare.add_argument("--sta")
    report = commands.add_parser("report", help="read final metrics without executing anything")
    report.add_argument("directory", type=Path)
    status = commands.add_parser("status", help="read campaign progress without launching or resuming work")
    status.add_argument("directory", type=Path)
    study_report = commands.add_parser("study-report", help="read archived study evidence and recorded costs without running work")
    study_report.add_argument("--campaign", action="append", required=True, metavar="TASK=DIRECTORY")
    study_report.add_argument("--account", type=Path, required=True)
    study_report.add_argument("--attempt-root", action="append", type=Path, default=[],
                              help="optional parent of abandoned sibling attempts for cost attribution")
    plot = commands.add_parser("plot", help="plot a saved campaign report without running models or EDA")
    plot.add_argument("report", type=Path)
    plot.add_argument("output", type=Path)
    plot.add_argument("--label", nargs=2, action="append", default=[], metavar=("METHOD", "LABEL"))
    plot.add_argument("--title")
    for command in (search, compare):
        for name in ("objects", "release", "task", "experiment", "liberty", "sta", "output"):
            command.add_argument("--" + name, required=True)
        command.add_argument("--judge-policy", help="explicit comparison policy JSON; default is baseline-only")
        command.add_argument("--cpu-endpoint", action="append", help="shared CPU scheduler ingress; repeat for final workers")
        command.add_argument("--cpus", type=int, help="CPU allocation for a standalone run")
    for name in ("provider", "prompt"):
        search.add_argument("--" + name, required=True)
    search.add_argument("--turns", type=int, default=defaults.AGENT_MAX_TURNS)
    search.add_argument("--request-seconds", type=int, default=defaults.AGENT_REQUEST_SECONDS)
    search.add_argument("--testbench-policy", help="optional frozen ordinary cocotb development profile")
    search.add_argument("--behavior-source", choices=("agent", "provided"), default="provided",
                        help="reference for development comparisons; final validation always uses the task golden")
    search.add_argument("--sim-feedback", choices=("enabled", "disabled"), default="enabled",
                        help="disable Sim development feedback while retaining capture, EDA and common final evaluation")
    compare.add_argument("--search", nargs=2, action="append", required=True, metavar=("LABEL", "DIRECTORY"))
    compare.add_argument("--retained-pool", help='trusted JSON object with a "stimuli" array')
    compare.add_argument("--reuse-final", type=Path,
                         help="reuse settled measurements from a stopped, identical final suite")
    compare.add_argument("--cpu-workers", type=int, default=1,
                         help="candidate concurrency for a standalone comparison; campaign uses its shared pool")
    args_in = list(sys.argv[1:] if argv is None else argv)
    # Keep the first pilot's flag-only search invocation usable.
    if args_in and args_in[0].startswith("--") and args_in[0] != "--help":
        args_in.insert(0, "search")
    args = parser.parse_args(args_in)
    if args.command == "prepare" and args.methods and (args.liberty is None or args.sta is None):
        parser.error("--methods requires --liberty and --sta")
    if args.command in {"search", "compare"}:
        endpoints = args.cpu_endpoint or []
        if len(set(endpoints)) != len(endpoints) or (args.command == "search" and len(endpoints) > 1):
            parser.error("each concurrent worker requires its own CPU scheduler ingress")
    if args.command == "compare" and len({label for label, _ in args.search}) != len(args.search):
        parser.error("search labels must be unique")
    if args.command == "plot" and len({label for label, _ in args.label}) != len(args.label):
        parser.error("plot labels must be unique")
    if args.command == "study-report":
        from .study_report import parse_campaigns

        try:
            args.campaign = parse_campaigns(args.campaign)
        except ValueError as error:
            parser.error(str(error))
    from behave.agent.attempt import AttemptInterrupted

    try:
        return _execute(args)
    except (AttemptInterrupted, ValueError, TypeError, OSError) as error:
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "ppa-command-invalid"),
                              "message": str(error)}).decode())
        return 1


def _execute(args):
    if args.command == "study-report":
        from .study_report import study_snapshot

        print(canonical_json(study_snapshot(args.campaign, args.account, args.attempt_root)).decode())
        return 0
    if args.command == "status":
        from .campaign_report import status

        print(canonical_json(status(args.directory)).decode())
        return 0
    if args.command == "plot":
        from .campaign_report import plot

        plot(parse_json(read_regular_file(args.report)), args.output, labels=dict(args.label), title=args.title)
        return 0
    from .report import summary

    if args.command == "report":
        print(canonical_json(summary(args.directory)).decode())
        return 0
    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.data.published import load_published_task

    from .records import release_reference

    if args.command == "prepare":
        from .planning import prepare_study, read_methods, read_study

        study = read_study(args.study)
        task = load_published_task(DirectoryReader(args.objects), release_reference(args.release), study["task"])
        result = prepare_study(study, task=task, output=args.output, objects=args.objects, release=args.release,
            methods=None if args.methods is None else read_methods(args.methods),
            liberty=args.liberty, sta=args.sta)
        print(canonical_json(result).decode())
        return 0
    if args.command in {"search", "compare"}:
        from .config import Experiment
        from .worker import NativePPA

        task = load_published_task(DirectoryReader(args.objects), release_reference(args.release), args.task)
        from behave.task_execution.judge_policy import JudgePolicy
        from behave.task_execution.sim_tool import ComparisonToolInputs

        policy = (JudgePolicy.from_bytes(read_regular_file(args.judge_policy)).to_bytes()
                  if args.judge_policy else ComparisonToolInputs.baseline(task.judge).judge_policy)
        judging = replace(task.judge, judge_policy=policy)
        experiment = Experiment.from_dict(parse_json(read_regular_file(args.experiment)))
        from behave.agent.cpu_resources import CpuResources
        endpoints = args.cpu_endpoint or []
        resources = endpoints[0] if endpoints else CpuResources(
            args.cpu_workers if args.command == "compare" else 1, cpus=args.cpus)
        native = NativePPA(liberty=Path(args.liberty), sta=Path(args.sta), cpu_resources=resources,
                           technology_name=experiment.mapping.technology)
        if args.command == "compare":
            from behave.core.records import fields

            from .finalize import compare_searches

            searches = {label: Path(path) for label, path in args.search}
            pool = () if args.retained_pool is None else tuple(fields(
                parse_json(read_regular_file(args.retained_pool)), {"stimuli"})["stimuli"])
            workers = None
            if endpoints:
                workers = []
                for endpoint in endpoints:
                    worker = copy(native)
                    worker.cpu_resources = endpoint
                    workers.append(worker)
            compare_searches(searches=searches, task=judging, experiment=experiment, native=native,
                             directory=Path(args.output), retained_pool=pool, reuse_final=args.reuse_final,
                             workers=workers)
        else:
            from behave.agent.attempt import AttemptSpec
            from behave.agent.process_model import ProcessModelBackend
            from behave.agent.providers.hosted import HostedEndpoint
            from behave.agent.providers.local_chat import LocalChatEndpoint
            from behave.sim import api
            from behave.task_execution.development import cocotb_tool
            from behave.task_execution.shell import development_shell
            from behave.task_execution.testbench import TBPolicy

            from .prompting import solver_setting
            from .workflow import run_search

            row = parse_json(read_regular_file(args.provider))
            endpoint = HostedEndpoint.from_dict(row) if "provider" in row else LocalChatEndpoint.from_dict(row)
            # Shared provider accounts index charges by call ID. Independent
            # replicate directories need distinct IDs; cold replay keeps its ID.
            attempt_id = "ppa-" + BlobRef.of(str(Path(args.output).absolute()).encode()).sha256[:24]
            spec = AttemptSpec(attempt_id, "ppa", endpoint.model_policy(), max_turns=args.turns,
                               request_timeout_seconds=args.request_seconds)
            setting = solver_setting(args.behavior_source, sim_feedback=args.sim_feedback == "enabled")
            development = [development_shell(api.development_limits(), setting=setting,
                                             cpu_endpoint=resources).tool()]
            if args.testbench_policy:
                development.append(cocotb_tool(TBPolicy.from_bytes(read_regular_file(args.testbench_policy))))
            run_search(public=task.public, task=judging, experiment=experiment, spec=spec,
                       backend=ProcessModelBackend(endpoint.command()), native=native, directory=Path(args.output),
                       prompt=read_regular_file(args.prompt).decode("utf-8"), development_tools=tuple(development),
                       behavior_source=args.behavior_source, sim_feedback=args.sim_feedback == "enabled")
    print(canonical_json(summary(Path(args.output))).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
