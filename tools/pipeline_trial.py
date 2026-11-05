"""Compatibility command for the installed external-method operators."""

from behave.pipelines.__main__ import main
from behave.pipelines.configuration import baseline_policy, method_settings, prepare  # noqa: F401 -- legacy exports
from behave.pipelines.execution import local_run, rollout, score  # noqa: F401 -- legacy export
from behave.runtime.records import write_json  # noqa: F401 -- legacy export

if __name__ == "__main__":
    main()
