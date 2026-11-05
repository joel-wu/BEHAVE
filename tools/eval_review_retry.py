"""Compatibility entry for saved Eval follow-up selection and reporting.

Installed users can run `behave-eval followup`. Cluster launch and execution
remain explicit; preparing or reporting a follow-up does not dispatch models.
"""

from behave.eval.followup import (
    component_progress,
    load_outcomes,
    main,
    prepare,
    run_rejudgments,
    select_retries,
    summarize,
)

__all__ = [
    "component_progress", "load_outcomes", "main", "prepare", "run_rejudgments",
    "select_retries", "summarize",
]


if __name__ == "__main__":
    main()
