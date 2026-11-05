"""Evaluation entry point; shared task execution owns dispatch and recovery."""

from behave.task_execution.batch import run_batch as run_evaluation

__all__ = ["run_evaluation"]
