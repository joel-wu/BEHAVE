"""Load a task-owned interface using the existing, strict contract wire schema.

Native task directories read interface.json directly. Legacy TOML descriptors
remain readable for historical inputs. Both use the same contract decoder.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .core.contract import TaskContract
from .core.refusal import Refusal, Refused
from .strict_json import strict_json_loads

__all__ = ["read_task_interface", "read_task_interface_file"]


def read_task_interface(directory: Path, block: object) -> tuple[TaskContract, dict[str, str]]:
    """Read ``[interface] version=1, file=..., sha256=...`` inside one task."""
    if (type(block) is not dict or set(block) != {"version", "file", "sha256"}
            or type(block["version"]) is not int or block["version"] != 1
            or type(block["file"]) is not str or not block["file"]
            or type(block["sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", block["sha256"]) is None):
        raise Refused(Refusal(
            "declarations-interface-invalid",
            "interface needs version=1, a relative file and its lowercase SHA-256"))
    name = Path(block["file"])
    payload = _interface_payload(directory, name)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != block["sha256"]:
        raise Refused(Refusal(
            "declarations-interface-changed",
            "the interface bytes do not match the task manifest",
            recorded=block["sha256"], found=digest))
    return _decode_interface(payload), {"interface_file": str(name), "interface_sha256": digest}


def read_task_interface_file(directory: Path) -> tuple[TaskContract, dict[str, str]]:
    """Read the complete native interface without a second declaration file."""
    name = Path("interface.json")
    return _decode_interface(_interface_payload(directory, name)), {"interface_file": str(name)}


def _interface_payload(directory: Path, name: Path) -> bytes:
    if name.is_absolute() or ".." in name.parts:
        raise Refused(Refusal(
            "declarations-interface-path-invalid",
            "the interface file must stay inside its task directory"))
    try:
        root = Path(directory).resolve(strict=True)
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root):
            raise Refused(Refusal(
                "declarations-interface-path-invalid",
                "the interface file must stay inside its task directory"))
        if not path.is_file():
            raise Refused(Refusal(
                "declarations-interface-unreadable",
                "the interface must be a regular file, not a directory or stream"))
        payload = path.read_bytes()
    except Refused:
        raise
    except (OSError, RuntimeError) as error:
        raise Refused(Refusal(
            "declarations-interface-unreadable",
            "the declared interface file could not be read", file=str(name),
            error=type(error).__name__)) from None
    return payload


def _decode_interface(payload: bytes) -> TaskContract:
    try:
        contract = TaskContract.from_dict(strict_json_loads(payload, what="task interface"))
    except (TypeError, ValueError) as error:
        raise Refused(Refusal(
            "declarations-interface-invalid",
            "the interface must be a complete strict task-contract JSON image",
            error=str(error))) from None
    if contract.authority != "task":
        raise Refused(Refusal(
            "declarations-interface-not-task-owned",
            "a task interface cannot borrow candidate or inferred authority"))
    return contract
