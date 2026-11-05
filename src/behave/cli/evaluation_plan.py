"""Prepare an Eval plan using an explicit local or hosted endpoint.

No model, simulator, server or Slurm job is started by this command.
"""

import argparse
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, fields, parse_json, text
from behave.runtime.records import write_json


def prepare(*, configuration, task_objects, release, tasks, output):
    """Pin an explicit selection; never publish a plan with missing candidates."""
    from behave.eval.generation import build_plan, endpoint_for, load_tasks, validate_settings

    required = {"provider", "endpoint", "attempt", "workers", "model_workers", "judge_workers",
                "setting", "score_on_submit", "output_preview_bytes"}
    settings = dict(fields(configuration, required))
    if not tasks or len(set(tasks)) != len(tasks):
        raise RecordError("evaluation-tasks-invalid", "select at least one task, without duplicate IDs")
    for task in tasks:
        text(task, "task ID")
    output = Path(output).absolute()
    settings.update(task_objects=str(Path(task_objects).absolute()), release=release.as_dict(), tasks=list(tasks))
    endpoint = endpoint_for(output, settings)
    validate_settings(output, settings, endpoint)
    # A new destination protects previous plans, identities and candidate journals.
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "settings.json", settings)
    objects, selected = load_tasks(output, settings)
    plan, recovery = build_plan(output, settings, objects, selected, endpoint)
    assert recovery is None  # The preparation recipe cannot supply a recovery run.
    for data in plan.input_blobs():
        objects.put(data)
    write_json(output / "endpoint.json", endpoint.as_dict())
    # Commit last. All selected public and judge-only inputs are already retained.
    write_json(output / "plan.json", parse_json(plan.to_bytes()))
    return {"status": "prepared", "plan": plan.identity.as_dict(), "candidates": len(plan.jobs),
            "plan_path": str(output / "plan.json"), "task_objects": str(output / "inputs"),
            "score_on_submit": plan.score_on_submit}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True,
                        help="portable local/hosted Eval settings JSON (not a saved run directory)")
    parser.add_argument("--task-objects", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True, help="JSON BlobRef of the published release")
    parser.add_argument("--task", action="append", required=True, help="exact release task ID; repeat to select more")
    parser.add_argument("--output", type=Path, required=True, help="new directory for plan and pinned inputs")
    args = parser.parse_args(argv)
    try:
        result = prepare(configuration=parse_json(read_regular_file(args.configuration)),
                         task_objects=args.task_objects,
                         release=BlobRef.from_dict(parse_json(read_regular_file(args.release))),
                         tasks=args.task, output=args.output)
    except (RecordError, ValueError, TypeError, OSError) as error:
        result = {"status": "unavailable", "code": getattr(error, "code", "evaluation-prepare-invalid"),
                  "message": str(error)}
        print(canonical_json(result).decode())
        return 1
    print(canonical_json(result).decode())
    return 0
