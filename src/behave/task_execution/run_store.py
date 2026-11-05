"""One local coordinator's run directory, separate phase journals and closure.

The advisory lock prevents simultaneous cooperating owners. It is not remote
worker fencing: after a crash, existing pending dispatches remain unknown under
their phase journals. Never unlink the lock to take ownership or replay work.
"""

from __future__ import annotations

from pathlib import Path

from behave.agent.journal import AttemptJournal
from behave.agent.run_store import JournalDirectory
from behave.core.storage import DirectoryStore


def judgment_directory(directory, name: str) -> Path:
    """Resolve a named scoring pass without creating any files or directories."""
    if (type(name) is not str or not name or name in {".", ".."}
            or "\0" in name or Path(name).name != name):
        raise ValueError("judgment run requires a single directory name")
    return Path(directory) / "judgments" / name


class RunStore(JournalDirectory):
    def __init__(self, directory, *, objects: DirectoryStore | None = None):
        super().__init__(directory)
        if objects is not None:
            self.objects = objects

    def journal(self, phase: str) -> AttemptJournal:
        if phase not in {"task", "solver", "supervision", "provider", "development", "judge", "testbench", "report"}:
            raise ValueError("unknown task execution phase")
        return super().journal(phase)
