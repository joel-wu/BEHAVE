"""Shared solver-task interfaces, independent of evaluation and training apps."""

from .submission import ArtifactSelection, SubmissionRequest

__all__ = ["ArtifactSelection", "SubmissionRequest"]
