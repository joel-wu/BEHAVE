"""Shared local ownership and journal closure, independent of application roles.

The advisory lock excludes cooperating coordinators, not old remote effects.
A dangling call still requires reconciliation/fencing under its own journal.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.storage import DirectoryStore, shared_owned_store

from .attempt import AttemptInterrupted
from .journal import AttemptJournal


class JournalDirectory:
    def __init__(self, directory: str | Path, *, objects: DirectoryStore | None = None):
        from behave.core.runtime_locations import runtime_path
        self.directory = runtime_path(directory)
        self.objects = (DirectoryStore(self.directory / "objects") if objects is None
                        else shared_owned_store(self.directory / "objects", objects))

    def journal(self, *parts: str) -> AttemptJournal:
        if not parts or any(type(p) is not str or re.fullmatch(r"[a-z][a-z0-9_]*", p) is None for p in parts):
            raise ValueError("journal names must be lowercase identifiers")
        if parts[0] == "objects":
            raise ValueError("reserved journal directory")
        return AttemptJournal(self.directory.joinpath(*parts), self.objects)

    @contextmanager
    def owner(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptor = None
        try:
            descriptor = os.open("owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                 0o600, dir_fd=directory)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("run ownership needs a regular lock file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AttemptInterrupted("run-already-owned") from error
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)

    def seal(self, *parts: str) -> tuple[BlobRef | None, tuple[BlobRef, ...]]:
        """Export using this directory's phase policy.

        For a journal opened with an explicit override, use journal.seal() so
        that its configuration is retained without reopening a default view.
        """
        return self.journal(*parts).seal()
