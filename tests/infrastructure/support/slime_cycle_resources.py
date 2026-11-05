"""Retention of this fixture's own large checkpoints, not a trainer policy."""

import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkdtemp

from behave.agent.run_store import JournalDirectory
from behave.core.lifecycle import CleanupState
from behave.train.slime_checkpoint import iteration


@contextmanager
def temporary_checkpoints(parent: Path, *, cleanup):
    """A fixture-owned checkpoint volume, retained if native cleanup is unknown.

    Full actor/critic Adam states can exceed node-local scratch. The installed
    lane supplies its explicit report directory on NFS; kernels/caches stay local.
    """
    if not parent.is_absolute() or parent.is_symlink() or not parent.is_dir():
        raise ValueError("checkpoint parent must be an existing absolute regular directory")
    path = Path(mkdtemp(prefix="slime-checkpoints-", dir=parent))
    try:
        yield path
    finally:
        state = cleanup()
        if type(state) is not CleanupState:
            raise TypeError("checkpoint release requires a typed cleanup witness")
        if state.quiescent:
            shutil.rmtree(path)


def release_obsolete_checkpoint(root: Path, *, checkpoints: Path | None = None, critic: bool = False) -> None:
    """Keep the committed round-one resume state and all HF weight comparisons."""
    checkpoints = root / "checkpoints" if checkpoints is None else checkpoints
    events = JournalDirectory(root / "rounds/native").journal("rounds").load()
    commits = [e.payload["round"] for e in events if e.kind == "checkpoint_saved"]
    if commits != [0, 1] or iteration(checkpoints) != 1:
        raise ValueError("fixture retention requires completed initial rounds zero and one")
    roots = (checkpoints, checkpoints / "critic") if critic else (checkpoints,)
    # Validate every role before removing any fixture-owned obsolete checkpoint.
    for directory in roots:
        obsolete = directory / "iter_0000000"
        latest = directory / "iter_0000001"
        if (directory.is_symlink() or obsolete.is_symlink() or latest.is_symlink()
                or not obsolete.is_dir() or not latest.is_dir() or iteration(directory) != 1):
            raise ValueError("fixture checkpoints must be owned regular directories at round one")
    for directory in roots:
        shutil.rmtree(directory / "iter_0000000")


def export_evidence(root: Path, destination: Path) -> None:
    """Preserve reports and byte inventories, not hundreds of GB of test weights."""
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("binding.json", "checkpoint_storage.json", "initial.stdout", "initial.stderr", "resume.stdout", "resume.stderr", "training.json", "weights.json", "cycle.json"):
        path = root / name
        if path.exists():
            shutil.copyfile(path, destination / name)
    if (root / "rounds").exists():
        shutil.copytree(root / "rounds", destination / "rounds")
