"""Explicit trainer operations over the existing checkpoint APIs.

Only ``update`` can start the operator-selected executable. Inspection, export,
restore, failure abort, receipt reconciliation and publication never run an optimizer.
This is not an agent tool, algorithm selector, scheduler or remote fencing API.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from behave.agent.attempt import AttemptInterrupted
from behave.cli.diagnostics import report_error
from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, parse_json
from behave.runtime.records import command_scope

if TYPE_CHECKING:
    from behave.train.checkpoints import Checkpoint, PendingUpdate


def _read(path: Path) -> bytes:
    return read_regular_file(path)


def _checkpoint(checkpoint: Checkpoint, pending: PendingUpdate | None) -> dict[str, object]:
    return {
        "checkpoint": checkpoint.reference.as_dict(),
        "state": checkpoint.state_ref.as_dict(),
        "policy": checkpoint.state.policy.as_dict(),
        "pending": None if pending is None else {
            name: None if getattr(pending, name) is None else getattr(pending, name).as_dict()
            for name in ("reference", "invocation", "receipt")
        },
    }


def _execute(args: argparse.Namespace) -> dict[str, object]:
    from behave.agent.run_store import JournalDirectory
    from behave.core.storage import DirectoryStore
    from behave.train.checkpoints import TrainingLedger, load_checkpoint, restore_ledger
    from behave.train.update_records import TrainerContract

    contract = TrainerContract.from_bytes(_read(args.contract))
    verifier = None
    if args.native_endpoint:
        from behave.train.training_data import NativeChatVerifier

        verifier = NativeChatVerifier.from_files(args.native_endpoint)
    reader = DirectoryReader(args.objects) if hasattr(args, "objects") else None
    if args.action in {"inspect", "restore", "publish"}:
        head = BlobRef.from_dict(parse_json(_read(args.head)))
        if args.action == "publish":
            from behave.train.checkpoint_storage import publish_weights

            publication = publish_weights(reader, head, contract=contract, component=args.component, rank=args.rank,
                                          objects=DirectoryStore(args.destination), verifier=verifier)
            return {"status": "published", "publication": publication.as_dict(),
                    "checkpoint": head.as_dict(), "component": args.component}
        if args.action == "restore":
            checkpoint, pending = restore_ledger(reader, head, run=JournalDirectory(args.run_directory),
                                                 contract=contract, verifier=verifier)
        else:
            checkpoint, pending = load_checkpoint(reader, head, contract=contract, verifier=verifier)
        return {"status": "restored" if args.action == "restore" else "verified",
                "head": head.as_dict(), **_checkpoint(checkpoint, pending)}

    # Decode all operator files before opening a mutable run. Native code is
    # reached solely through execute_update's fresh, durable dispatch authority.
    state = request = process = None
    if args.action == "initialize":
        from behave.train.checkpoints import TrainingState

        state = TrainingState.from_bytes(_read(args.state))
    elif args.action in {"update", "reconcile", "abort"}:
        from behave.train.update_records import UpdateRequest

        request = UpdateRequest.from_bytes(_read(args.request))
        if args.action == "update":
            from behave.train.trainer import TrainerProcess

            process = TrainerProcess.from_dict(parse_json(_read(args.process)))

    run = JournalDirectory(args.run_directory)
    with TrainingLedger(run, contract, verifier=verifier) as ledger:
        checkpoint = ledger.checkpoint
        if args.action == "initialize":
            checkpoint = ledger.initialize(state, reader)
        elif args.action == "update":
            from behave.train.trainer import execute_update

            with command_scope() as cancelled:
                checkpoint = execute_update(ledger, request, reader, process, cancelled=cancelled)
        elif args.action == "reconcile":
            from behave.train.trainer import commit_recorded_update

            checkpoint = commit_recorded_update(ledger, BlobRef.of(request.to_bytes()))
        elif args.action == "abort":
            checkpoint = ledger.abort(BlobRef.of(request.to_bytes()))
        head, closure = ledger.seal()
        if args.action == "export":
            DirectoryStore(args.destination).publish(run.objects, closure)
        return {"status": "exported" if args.action == "export" else "aborted" if args.action == "abort" else "committed",
                "head": head.as_dict(), **_checkpoint(checkpoint, ledger.pending)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--contract", type=Path, required=True, help="independently approved TrainerContract JSON")
    common.add_argument("--native-endpoint", type=Path, action="append",
                        help="approved original token-enabled endpoint JSON; repeat for history")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("initialize", "update", "reconcile", "abort", "inspect", "export", "restore", "publish"):
        command = sub.add_parser(action, parents=[common])
        if action not in {"inspect", "publish"}:
            command.add_argument("--run-directory", type=Path, required=True)
        if action in {"initialize", "update", "inspect", "restore", "publish"}:
            command.add_argument("--objects", type=Path, required=True, help="private input CAS, not a model folder")
        if action in {"inspect", "restore", "publish"}:
            command.add_argument("--head", type=Path, required=True, help="JSON BlobRef of the exact sealed head")
        if action in {"export", "publish"}:
            command.add_argument("--destination", type=Path, required=True, help="private destination CAS")
        if action in {"update", "reconcile", "abort"}:
            command.add_argument("--request", type=Path, required=True, help="canonical UpdateRequest JSON")
        if action == "initialize":
            command.add_argument("--state", type=Path, required=True, help="canonical TrainingState JSON")
        elif action == "update":
            command.add_argument("--process", type=Path, required=True, help="explicit trusted TrainerProcess JSON")
        elif action == "restore":
            command.add_argument("--fenced", action="store_true",
                                 help="operator confirms the old owner is stopped/fenced; does not perform fencing")
        elif action == "publish":
            command.add_argument("--component", required=True, help="explicit committed inference component")
            command.add_argument("--rank", type=int, help="fixed-world rank holding a complete backend inference export")
    args = parser.parse_args(argv)
    if args.action == "restore" and not args.fenced:
        parser.error("restore requires --fenced after independently stopping/fencing the old owner")
    try:
        result = _execute(args)
    except (RecordError, AttemptInterrupted, ValueError, TypeError, OSError) as error:
        report_error(error)
        result = {"status": "unavailable", "code": getattr(error, "code", "trainer-command-invalid")}
        # A retained path is reconciliation evidence, not permission to delete
        # it or retry an unresolved update. Never print command/env/state bytes.
        receipt = getattr(error, "receipt", None)
        workspace = getattr(error, "workspace", None)
        if receipt is not None:
            result["receipt"] = receipt.as_dict()
        if workspace is not None:
            result["retained_workspace"] = str(workspace)
        print(canonical_json({"schema": "behave.trainer-command/1", "action": args.action, **result}).decode())
        return 1
    print(canonical_json({"schema": "behave.trainer-command/1", "action": args.action, **result}).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
