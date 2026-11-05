"""Compatibility imports for the shared committed-submission score stream.

Frozen historical snapshots keep their own implementation. New snapshots and
installed consumers use ``behave.eval.score_stream``; this module starts nothing.
"""

from behave.eval.score_stream import (
    committed_cases,
    consolidate,
    publish_subset,
    run,
    score_signature,
    scoring_sources,
)

__all__ = ["committed_cases", "consolidate", "publish_subset", "run", "score_signature", "scoring_sources"]
