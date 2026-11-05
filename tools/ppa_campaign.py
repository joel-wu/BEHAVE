"""Compatibility CLI for the shared PPA campaign launcher.

Preparation, dispatch and staging live in ``behave.ppa.launch``; execution and
read-only reporting use the existing package modules. This entry retains its
historical commands and supplies the checkout path explicitly to the launcher.
"""

from __future__ import annotations

import argparse
import json
import os  # noqa: F401 -- historical site-script access
import socket  # noqa: F401 -- historical site-script access
from pathlib import Path

from behave.ppa import launch as _launch
from behave.ppa.campaign import (
    await_preflight,  # noqa: F401 -- historical operator-script import
    export_traces,  # noqa: F401 -- historical operator-script import
    run,
    run_group,  # noqa: F401 -- historical operator-script import
    run_phase,  # noqa: F401 -- historical operator-script import
    service,  # noqa: F401 -- historical operator-script import
    service_bootstrap,  # noqa: F401 -- historical operator-script import
)
from behave.ppa.campaign_inputs import (
    freeze_inputs,  # noqa: F401 -- historical operator-script import
    freeze_plan,  # noqa: F401 -- historical operator-script import
    publish,  # noqa: F401 -- historical operator-script import
    validate_durable_campaign,  # noqa: F401 -- historical operator-script import
)
from behave.ppa.campaign_recovery import (
    recover_final,  # noqa: F401 -- historical operator-script import
    recover_search,  # noqa: F401 -- historical operator-script import
    recovery_ancestor,  # noqa: F401 -- historical operator-script import
    recovery_binding,  # noqa: F401 -- historical operator-script import
    selected_search,  # noqa: F401 -- historical operator-script import
)
from behave.ppa.campaign_report import collect_final, plot, status  # noqa: F401 -- historical operator-script import
from behave.ppa.launch import release_staged, run_staged  # noqa: F401 -- historical entry points
from behave.ppa.planning import (
    campaign_arguments as common_arguments,  # noqa: F401 -- historical operator-script import
)
from behave.ppa.planning import campaign_slots as slots  # noqa: F401 -- historical operator-script import
from behave.ppa.planning import campaign_wall  # noqa: F401 -- historical operator-script import
from behave.ppa.planning import validate_campaign as validate_plan  # noqa: F401 -- historical operator-script import
from behave.ppa.planning import (
    validate_campaign_group as validate_group,  # noqa: F401 -- historical operator-script import
)


# Keep the historical Python/CLI signatures; the package never infers a checkout.
def submit_group(group_path, root, allocation, cpus, *, runtime_directory=None, cpu_offset=0,
                 recover_from=None, repository=None):
    return _launch.submit_group(group_path, root, allocation, cpus,
        repository=Path(__file__).resolve().parents[1] if repository is None else repository,
        runtime_directory=runtime_directory, cpu_offset=cpu_offset, recover_from=recover_from)


def submit(plan_path, root, allocation, cpus, preflight=None, recover_from=None, *, runtime_directory=None,
           repository=None):
    return _launch.submit(plan_path, root, allocation, cpus, preflight, recover_from,
        repository=Path(__file__).resolve().parents[1] if repository is None else repository,
        runtime_directory=runtime_directory)


def dispatch(root, allocation, cpus, wall, frozen, *, repository=None):
    return _launch.dispatch(root, allocation, cpus, wall, frozen,
                            repository=Path(__file__).resolve().parents[1] if repository is None else repository)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("submit")
    start.add_argument("plan", type=Path)
    start.add_argument("output", type=Path)
    start.add_argument("--allocation", type=int, required=True)
    start.add_argument("--cpus", type=int, required=True)
    start.add_argument("--repository", type=Path, help="source checkout to freeze; default is this script's checkout")
    start.add_argument("--runtime-directory", help="shared node-local prepared runtime directory")
    start.add_argument("--preflight", type=Path, help="wait for a successful same-package private preflight before any API call")
    start.add_argument("--recover-from", type=Path,
                       help="retain successful searches; replace only fenced, pre-measurement infrastructure failures")
    group = sub.add_parser("submit-group", help="run several task campaigns on one shared CPU pool")
    group.add_argument("group", type=Path)
    group.add_argument("output", type=Path)
    group.add_argument("--allocation", type=int, required=True)
    group.add_argument("--cpus", type=int, required=True)
    group.add_argument("--repository", type=Path, help="checkout to freeze for the campaign")
    group.add_argument("--runtime-directory")
    group.add_argument('--recover-from', type=Path,
                       help='reuse completed selected searches; replace fenced infrastructure failures only')
    group.add_argument("--cpu-offset", type=int, default=0,
                       help="leave this many leading CPUs of the allocated step unused")
    sub.add_parser("run").add_argument("output", type=Path)
    sub.add_parser("run-node").add_argument("output", type=Path)
    sub.add_parser("release-scratch", help="finish a committed local staging release; never run models").add_argument(
        "output", type=Path)
    sub.add_parser("status", help="read progress without invoking models or evaluators").add_argument("output", type=Path)
    chart = sub.add_parser("plot", help="plot an existing report without dispatching experiments")
    chart.add_argument("report", type=Path)
    chart.add_argument("output", type=Path)
    chart.add_argument("--label", nargs=2, action="append", default=[], metavar=("METHOD", "LABEL"))
    chart.add_argument("--title")
    args = parser.parse_args()
    if args.action == 'status':
        print(json.dumps(status(args.output), indent=2))
        return 0
    if args.action == "release-scratch":
        print(json.dumps(release_staged(args.output), indent=2))
        return 0
    if args.action == "plot":
        plot(json.loads(args.report.read_text()), args.output, labels=dict(args.label), title=args.title)
        return 0
    if args.action == "submit":
        print(json.dumps(submit(args.plan.resolve(), args.output.resolve(), args.allocation, args.cpus,
                                args.preflight.resolve() if args.preflight else None,
                                args.recover_from.resolve() if args.recover_from else None,
                                runtime_directory=args.runtime_directory, repository=args.repository), indent=2))
        return 0
    if args.action == "submit-group":
        print(json.dumps(submit_group(args.group.resolve(), args.output.resolve(), args.allocation,
                                     args.cpus, runtime_directory=args.runtime_directory,
                                     repository=args.repository,
                                     cpu_offset=args.cpu_offset,
                                     recover_from=args.recover_from.resolve() if args.recover_from else None), indent=2))
        return 0
    if args.action == "run-node":
        return run_staged(args.output.resolve())
    return run(args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
