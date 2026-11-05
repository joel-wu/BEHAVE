"""Node-local scratch released only on its lifetime owner's cleanup witness.

Unlike TemporaryDirectory, garbage collection never removes a directory still
used by an unconfirmed process. This is retention, not fencing or recovery.
"""

import os
import re
import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from tempfile import mkdtemp

from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json


def _marker_name(value):
    if value is not None and (type(value) is not str or value in {".", ".."}
                             or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) is None):
        raise ValueError("scratch owner marker must be a simple filename")
    return value


class ScratchDirectory:
    def __init__(self, *, prefix: str, cleanup: Callable[[], CleanupState], parent: Path | None = None,
                 owner_marker: str | None = None):
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", prefix) is None or not callable(cleanup):
            raise ValueError("scratch requires a simple prefix and an owner cleanup predicate")
        self._marker = _marker_name(owner_marker)
        if parent is None:
            from behave.core.paths import scratch_root

            parent = scratch_root()
        self.path = Path(mkdtemp(prefix=prefix, dir=parent))
        self._state, self._removed = cleanup, False

    @property
    def name(self) -> str:
        return str(self.path)

    @classmethod
    def retained(cls, path: Path, *, cleanup: Callable[[], CleanupState], owner_marker: str | None = None):
        """Reopen an existing private scratch after its caller validates ownership.

        This does not fence workers or authorize deletion. The caller must check
        its retained-owner receipt before adoption and supply the usual lifetime
        witness; missing or symlinked scratch is never silently recreated.
        """
        path = Path(path)
        if not callable(cleanup) or path.is_symlink() or not path.is_dir():
            raise ValueError("retained scratch requires an existing owned directory")
        owner = cls.__new__(cls)
        owner._marker = _marker_name(owner_marker)
        owner.path, owner._state, owner._removed = path, cleanup, False
        return owner

    def cleanup(self) -> None:
        state = self._state()
        if type(state) is not CleanupState:
            raise TypeError("scratch release requires a typed cleanup witness")
        if state.quiescent and not self._removed:
            # Runtime profiles contain read-only directories. Only the stopped
            # owner's private tree is being removed, never mounted installations.
            for directory, _, _ in os.walk(self.path, followlinks=False):
                path = Path(directory)
                path.chmod(path.stat().st_mode | 0o700)
            if self._marker is None:
                shutil.rmtree(self.path)
            else:
                # A caller-validated marker survives partial child deletion.
                # Remove it last so a later owner can authenticate the remainder.
                for child in self.path.iterdir():
                    if child.name == self._marker:
                        continue
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                (self.path / self._marker).unlink(missing_ok=True)
                self.path.rmdir()
            self._removed = True

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *_):
        self.cleanup()


def retention_record(path: Path, state: CleanupState) -> bytes:
    """Private operational evidence, never a task identity or cleanup authority."""
    if type(state) is not CleanupState or state.quiescent:
        raise ValueError("retention requires an unconfirmed owner")
    return canonical_json({"schema": "behave.retained-scratch/1", "host": socket.gethostname(),
                           "path": str(path), "cleanup": state.value})
