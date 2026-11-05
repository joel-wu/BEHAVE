"""Run one configured acquisition or construction stage; dry-run checks inputs.

Synthesis produces a review package, not publication or benchmark approval.
Other stages return their own source, selection or scope records. This command
does not orchestrate the five-round learning experiment. Dry-run never calls a model.
Model commands are trusted operator configuration and may incur provider costs.
Use ``behave-data ingest --help`` to import an explicitly selected local source.
Use ``behave-data freeze-native --help`` to freeze explicit native experiment inputs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.runtime.records import command_scope


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["freeze-native"]:
        from behave.cli.native_release import main as native_release_main

        return native_release_main(argv[1:])
    if argv[:1] == ["ingest"]:
        from .ingest.__main__ import main as ingest_main

        return ingest_main(argv[1:])
    if argv[:1] == ["fetch"]:
        from .ingest.fetch_cli import main as fetch_main

        return fetch_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("synthesis", "scope", "construction", "acquisition"), default="synthesis")
    parser.add_argument("--configuration", required=True, type=Path)
    parser.add_argument("--source-objects", required=True, type=Path)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="validate the closed configuration and input content only")
    parser.add_argument("--continue-from", type=Path,
                        help="synthesis/construction: JSON BlobRef of a settled synthesis run to continue a check, review or repair; "
                             "extend configuration max_revisions when the prior repair budget was exhausted")
    args = parser.parse_args(argv)
    if not args.dry_run and args.run_directory is None:
        parser.error("--run-directory is required unless --dry-run is selected")
    if args.continue_from is not None and args.stage not in {"synthesis", "construction"}:
        parser.error("--continue-from belongs to synthesis or construction")
    from behave.agent.attempt import AttemptInterrupted

    try:
        continuation_from = (None if args.continue_from is None else
                             BlobRef.from_dict(parse_json(read_regular_file(args.continue_from))))
        payload = read_regular_file(args.configuration)
        if args.stage == "acquisition":
            from behave.improve.acquisition import AcquisitionConfiguration

            config = AcquisitionConfiguration.from_bytes(payload)
        elif args.stage == "construction":
            from .supervisor import ConstructionConfiguration

            config = ConstructionConfiguration.from_bytes(payload)
        elif args.stage == "scope":
            from .scope import ScopeConfiguration

            config = ScopeConfiguration.from_bytes(payload)
        else:
            from .synthesis import SynthesisConfiguration

            config = SynthesisConfiguration.from_bytes(payload)
        reader = DirectoryReader(args.source_objects)
        if args.dry_run:
            config.preflight(reader)
            if continuation_from is not None:
                from .continuation import load_continuation, validate_plan

                validate_plan(load_continuation(reader, continuation_from),
                              config.plan.initial if args.stage == "construction" else config.plan)
            output = {"status": "validated", "configuration": BlobRef.of(payload).as_dict()}
            if args.stage == "construction":
                output.update(run_id=config.run_id, max_reopens=config.max_reopens)
            elif args.stage == "acquisition":
                output.update(attempt_id=config.job.attempt.attempt_id, max_turns=config.job.attempt.max_turns)
            else:
                output["max_revisions"] = config.plan.max_revisions
                output["run_id" if args.stage == "scope" else "task_id"] = (
                    config.plan.run_id if args.stage == "scope" else config.plan.task_id)
        else:
            from behave.agent.lifetime import AttemptControl
            from behave.agent.run_store import JournalDirectory

            # On recovery the coordinator uses its verified local closure; do
            # not re-preflight the upstream store or silently fetch lost inputs.
            with command_scope() as cancelled:
                run = JournalDirectory(args.run_directory)
                control = AttemptControl(cancelled=cancelled)
                if args.stage == "acquisition":
                    from behave.improve.acquisition import acquire_materials

                    output = acquire_materials(configuration=config, reader=reader, run=run, control=control)
                    print(canonical_json(output).decode())
                    return 0
                if args.stage == "construction":
                    from .supervisor import construct

                    output = construct(plan=config.plan, reader=reader, run=run, control=control, **config.pools(),
                                       **({"continuation_from": continuation_from} if continuation_from is not None else {}))
                    print(canonical_json(output).decode())
                    return 0
                if args.stage == "scope":
                    from behave.agent.admission import ResourcePool

                    from .scope import load_scope_preparation, prepare_scope

                    pool = None if config.model_admission is None else ResourcePool(config.model_admission)
                    result = prepare_scope(plan=config.plan, reader=reader, run=run, control=control, model_pool=pool)
                    head, _ = run.seal("scope")
                    _, billing = load_scope_preparation(run.objects, head)
                    output = {"scope_head": head.as_dict(), "scope": None if result.status != "review_ready"
                              else result.scopes[-1].as_dict()}
                else:
                    from .process_evidence import synthesis_billing
                    from .release_records import ReviewPackage
                    from .synthesis import seal_review_package, synthesize

                    result = synthesize(plan=config.plan, reader=reader, run=run, control=control,
                                        continuation_from=continuation_from, **config.pools())
                    package_ref = seal_review_package(run)
                    package = ReviewPackage.from_bytes(run.objects.read(package_ref))
                    billing = synthesis_billing(run.objects, package.synthesis_head)
                    output = {"review_package": package_ref.as_dict(), "synthesis_head": package.synthesis_head.as_dict()}
                    if result.rtl_head is not None:
                        from .rtl import rtl_summary

                        output["auxiliary_rtl"] = rtl_summary(run.objects, result.rtl_head)
                output.update(status=result.status, code=result.code, result=BlobRef.of(result.to_bytes()).as_dict(),
                              billing={"cost_by_currency": thaw_json(billing.cost_by_currency()),
                                       "unpriced_dispatches": billing.unpriced_dispatches,
                                       "unresolved_dispatches": billing.unresolved_dispatches})
                if args.stage == "synthesis":
                    from .rtl import construction_status

                    output["status"], output["code"] = construction_status(run.objects, result)
    except (RecordError, AttemptInterrupted, TypeError, ValueError, OSError) as error:
        # Keep stdout machine-readable; show the operator why the command failed.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "synthesis-command-invalid")}).decode())
        return 1
    print(canonical_json(output).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
