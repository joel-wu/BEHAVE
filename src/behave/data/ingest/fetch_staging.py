"""Private acquisition staging; callers hold the acquisition journal's owner lock.

A retained service receipt permits publication, never another network dispatch.
Only a verified settled acquisition grants scratch release. Public provenance
contains content references, not these local recovery paths.
"""

import os
import stat
from pathlib import Path

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.file_input import read_regular_file
from behave.core.lifecycle import CleanupState
from behave.core.records import parse_json
from behave.runtime.models import _transport_host
from behave.runtime.records import publish_json

OWNER = 'behave.source-fetch-owner/1'
MARKER = '.fetch-owner.json'


def read(path):
    if path.is_symlink():
        raise ValueError('acquisition recovery records cannot be symlinks')
    return parse_json(read_regular_file(path))


def _identity(path):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('acquisition scratch requires an unlinked absolute directory')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('acquisition scratch has a different owner')
    return [info.st_dev, info.st_ino]


def prepare(run, inputs):
    from behave.isolation.scratch import ScratchDirectory

    scratch = ScratchDirectory(prefix='behave-source-fetch-', cleanup=lambda: CleanupState.UNKNOWN,
                               owner_marker=MARKER)
    local = scratch.path.resolve()
    owner = {'schema': OWNER, 'run': str(run.directory.resolve()), 'inputs': inputs,
             'directory': str(local), 'identity': _identity(local), 'host': _transport_host()}
    root = run.directory.resolve()
    if root.is_relative_to(local) or local.is_relative_to(root):
        raise ValueError('acquisition scratch and archive must be separate')
    publish_json(local / MARKER, owner)
    publish_json(run.directory / 'fetch-owner.json', owner)
    return JournalDirectory(local)


def _owner(run, inputs):
    owner = read(run.directory / 'fetch-owner.json')
    local = Path(owner.get('directory', ''))
    root = run.directory.resolve()
    if (owner.get('schema') != OWNER or owner.get('run') != str(root) or owner.get('inputs') != inputs
            or not local.is_absolute() or local.resolve() != local
            or not local.name.startswith('behave-source-fetch-')
            or root.is_relative_to(local) or local.is_relative_to(root)):
        raise ValueError('acquisition scratch does not belong to this run and inputs')
    return owner, local


def reopen(run, inputs):
    try:
        owner, local = _owner(run, inputs)
        if owner.get('host') != _transport_host():
            raise AttemptInterrupted('fetch-dispatch-pending')
        if _identity(local) != owner.get('identity') or read(local / MARKER) != owner:
            raise ValueError('acquisition scratch identity changed')
        # No saved service result means the old dispatch is still ambiguous.
        read(local / 'service.json')
    except FileNotFoundError as error:
        raise AttemptInterrupted('fetch-dispatch-pending') from error
    return JournalDirectory(local)


def release(run, inputs, result):
    """Finish same-boot release after offline verification of the public result."""
    path = run.directory / 'fetch-owner.json'
    if not result.cleanup.quiescent or not (path.exists() or path.is_symlink()):
        return  # Legacy/cold archives have no node-local cleanup authority.
    recorded = read(path)
    if recorded.get('host') != _transport_host() or recorded.get('run') != str(run.directory.resolve()):
        return  # An archive copy can replay, but cannot release its source's scratch.
    owner, local = _owner(run, inputs)
    receipt = {'owner': BlobRef.of(read_regular_file(path)).as_dict(), 'head': result.head.as_dict()}
    released = run.directory / 'fetch-released.json'
    exists = local.exists() or local.is_symlink()
    if released.exists() or released.is_symlink():
        if read(released) != receipt or exists:
            raise ValueError('released acquisition scratch changed or reappeared')
        return
    intent = run.directory / 'fetch-release.json'
    committed = intent.exists() or intent.is_symlink()
    if committed and read(intent) != receipt:
        raise ValueError('acquisition release belongs to another outcome')
    if exists:
        if _identity(local) != owner.get('identity'):
            raise ValueError('acquisition scratch was replaced')
        marker = local / MARKER
        if marker.exists() or marker.is_symlink():
            if read(marker) != owner:
                raise ValueError('acquisition scratch belongs to another owner')
        elif not committed or any(local.iterdir()):
            raise ValueError('acquisition scratch owner is missing')
    elif not committed:
        return  # No scratch remains, but absence alone is not a release receipt.
    publish_json(intent, receipt)
    if exists:
        from behave.isolation.scratch import ScratchDirectory

        with ScratchDirectory.retained(local, cleanup=lambda: CleanupState.COMPLETE, owner_marker=MARKER):
            pass
    publish_json(released, receipt)
