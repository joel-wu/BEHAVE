"""Compatibility entry for repository-side training split checks."""

from pathlib import Path

from behave.train import plan

ROOT = Path(__file__).resolve().parents[1]


def current_path(kind: str = "authored", *, repository: Path = ROOT) -> Path:
    return plan.current_path(kind, repository=repository)


def check_training_selection(task_ids, train_split: Path, *, repository: Path = ROOT) -> bytes:
    return plan.check_training_selection(task_ids, train_split, repository=repository)
