"""Compatibility entry for freezing native experiment inputs from this checkout.

Installed users can run ``behave-data freeze-native`` with explicit inputs.
This preserves the historical checkout default, not benchmark approval.
"""

from pathlib import Path

from behave.data import native_release

# Historical Python exports refer to the same implementation.
prepare = native_release.prepare
refresh_simulator = native_release.refresh_simulator
selected_task_ids = native_release.selected_task_ids
selected_tasks = native_release.selected_tasks
api = native_release.api


def main():
    from behave.cli.native_release import main as native_main

    return native_main(repository=Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
