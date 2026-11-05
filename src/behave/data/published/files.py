"""Declared task artifact inventory; no reads, publication or policy changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from behave.core.artifacts import FileRef, file_set
from behave.core.records import array, thaw_json

if TYPE_CHECKING:
    from .records import TaskManifest

def task_archive_files(task: TaskManifest) -> tuple[FileRef, ...]:
    """Validate reserved metadata, including manifests from older snapshots.

    Keep storage inventory independent of convenience properties added to the
    record class. A controller-only repair need not replace frozen readers.
    """
    rows = array(thaw_json(task.metadata.get("archive_files", ())))
    return file_set(tuple(FileRef.from_dict(row) for row in rows))


def task_files(task: TaskManifest) -> tuple[FileRef, ...]:
    """Include public inputs, private references and declared archive attachments.

    This is a trusted storage inventory, never a solver's input view. Different
    artifact namespaces may reuse a path; do not merge them with ``file_set``.
    Only ``archive_files`` explicitly declares metadata attachments. Other
    provenance, policies and manifest/release objects are not inferred here.
    """
    return (task.spec, task.contract, *task.public_files, *task.reference_behavior.files,
            *(() if task.reference_rtl is None else task.reference_rtl.files), *task_archive_files(task))
