"""PPA staging ownership and restartable release, never campaign execution.

Callers hold the campaign owner lock. Only a successfully published release
intent can reclaim this boot's exact private directory. Model calls, journals,
spending accounts and old unowned scratch are outside this operation.
"""

import os
import socket
import stat
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.file_input import read_regular_file
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, parse_json
from behave.isolation.scratch import ScratchDirectory
from behave.runtime.models import _transport_host
from behave.runtime.records import publish_json, write_json

from .planning import campaign_slots, validate_campaign, validate_campaign_group

MARKER = ".ppa-staging-owner.json"
OWNER = "behave.ppa-staging-owner/1"
RELEASE = "behave.ppa-scratch-release/2"


def _read(path):
    if path.is_symlink():
        raise ValueError("PPA staging records cannot be symlinks")
    value = parse_json(read_regular_file(path))
    if not isinstance(value, dict):
        raise RecordError("ppa-staging-record-invalid", "PPA staging records must be objects")
    return value


def _directory(path):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("PPA staging requires an explicit unlinked directory")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("PPA staging directory has another owner")
    return [info.st_dev, info.st_ino]


def plans(root):
    """The declared plan inventory only; no object stores or result scans."""
    sources = {}

    def read(path):
        if not path.is_relative_to(root) or path.resolve() != path:
            raise ValueError("staged campaign plan must belong to this archive")
        data = read_regular_file(path)
        sources[str(path)] = BlobRef.of(data).as_dict()
        return parse_json(data)

    group_file = root / "group.json"
    if group_file.exists() or group_file.is_symlink():
        group = validate_campaign_group(read(group_file))
        selected = [(root / "execution/campaigns" / name, Path(path))
                    for name, path in group["plans"].items()]
    else:
        selected = [(root / "execution", root / "plan.json")]
    rows = []
    for execution, path in selected:
        rows.append((execution, validate_campaign(read(path))))
    return rows, sources


def service_cleanup(execution, selected):
    """A coordinator return does not replace its children's cleanup receipts."""
    states = []
    for directory, plan in selected:
        for _, _, replica in campaign_slots(plan, directory):
            for phase in ("search-service", "final-service"):
                path = replica / phase
                if any(parent.is_symlink() for parent in (path, *path.parents)
                       if parent.is_relative_to(execution)):
                    return CleanupState.UNKNOWN
                if not path.exists():
                    continue
                try:
                    if not isinstance(_read(path / "requested.json"), dict):
                        return CleanupState.UNKNOWN
                    states.append(CleanupState(_read(path / "service.json").get("cleanup")))
                except (OSError, ValueError, TypeError, AttributeError):
                    return CleanupState.UNKNOWN
    return CleanupState.combine(*states)


def record_owner(root, local):
    if (root.is_relative_to(local) or local.is_relative_to(root)
            or local.parent == local):
        raise ValueError("PPA staging and archive must be separate")
    owner = {"schema": OWNER, "archive": str(root), "directory": str(local),
             "host": _transport_host(), "identity": _directory(local)}
    publish_json(root / "scratch-owner.json", owner)
    publish_json(local / MARKER, owner)
    return owner


def prepare_release(root, runtime, snapshot, inputs):
    """Commit deletion authority only after success, traces and child cleanup."""
    publish_json(root / "scratch-release.json", {
        "schema": RELEASE, "host": runtime["host"], "local_directory": runtime["local_directory"],
        "execution_directory": runtime["execution_directory"], "source_snapshot": str(root / "snapshot"),
        "snapshot": snapshot, "cleanup": "complete", "exit_code": 0,
        "owner": BlobRef.of(read_regular_file(root / "scratch-owner.json")).as_dict(),
        "plans": inputs})


def release(root):
    """Finish a published release under the caller's campaign owner lock.

    No release intent means no automatic adoption: even quiet legacy/failed
    workers need separate reconciliation. Absence after a committed intent is
    idempotent; a replaced or reappearing private directory is never removed.
    """
    from behave.runtime.snapshots import verify

    receipt = _read(root / "scratch-release.json")
    runtime = _read(root / "runtime.json")
    owner = _read(root / "scratch-owner.json")
    local = Path(owner.get("directory", ""))
    if (receipt.get("schema") != RELEASE or receipt.get("cleanup") != "complete"
            or type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0
            or owner.get("schema") != OWNER or owner.get("archive") != str(root)
            or owner.get("host") != _transport_host()
            or not local.is_absolute() or local.parent == local
            or local.resolve() != local
            or root.is_relative_to(local) or local.is_relative_to(root)
            or receipt.get("local_directory") != str(local)
            or runtime.get("local_directory") != str(local)
            or receipt.get("execution_directory") != str(root / "execution")
            or runtime.get("execution_directory") != str(root / "execution")
            or receipt.get("source_snapshot") != str(root / "snapshot")
            or runtime.get("state") not in {"running", "archived"}
            or runtime.get("storage") != "shared"
            or receipt.get("host") != socket.gethostname() or runtime.get("host") != socket.gethostname()
            or BlobRef.of(read_regular_file(root / "scratch-owner.json")).as_dict() != receipt.get("owner")):
        raise ValueError("PPA scratch release has no matching local ownership and completion receipt")
    selected, inputs = plans(root)
    if inputs != receipt.get("plans"):
        raise ValueError("PPA campaign plans changed; scratch retained")
    if verify(root / "snapshot")["sha256"] != receipt.get("snapshot"):
        raise ValueError("PPA retained snapshot changed; scratch retained")
    service_cleanup(root / "execution", selected).require_quiescent("ppa-staging")
    exists = local.exists() or local.is_symlink()
    if exists:
        if runtime.get("scratch") == "released" or _directory(local) != owner.get("identity"):
            raise ValueError("PPA staging directory was replaced or reappeared; retained")
        marker = local / MARKER
        if marker.exists() or marker.is_symlink():
            if _read(marker) != owner:
                raise ValueError("PPA scratch marker belongs to another owner")
        elif any(local.iterdir()):
            raise ValueError("PPA scratch owner marker is missing; retained")
        # A crash after removing the last marker may leave only the empty,
        # same-inode directory. No nonempty markerless directory is adopted.
    elif runtime.get("scratch") == "released":
        return runtime
    terminal = {**runtime, "state": "archived", "scratch": "release-ready"}
    write_json(root / "runtime.json", terminal, durable=True)
    if exists:
        with ScratchDirectory.retained(local, owner_marker=MARKER, cleanup=lambda: CleanupState.COMPLETE):
            pass
    terminal["scratch"] = "released"
    write_json(root / "runtime.json", terminal, durable=True)
    return terminal
