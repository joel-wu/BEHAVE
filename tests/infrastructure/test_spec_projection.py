"""RTL-only uses labelled source sections, never keyword deletion."""

import pytest

from behave.data.spec_format import without_behavior_interface


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_projection_preserves_rtl_protocol_function_and_reset(newline):
    prefix = "# Example\n## Interface\n### RTL interface\nRTL pins.\n"
    model = "### Behavior interface\nclass Model: ...\n#### State\nModel state.\n"
    suffix = ("### Transaction protocol\nWait for ready.\n## Modules\nTop.\n"
              "## Functional Behavior\nAdd modulo 256.\n## Reset Behavior\nClear state.\n")
    text = (prefix + model + suffix).replace("\n", newline)
    assert without_behavior_interface(text) == (prefix + suffix).replace("\n", newline)


def test_projection_does_not_interpret_headings_inside_code():
    prefix = "# RTL\n~~~~text\n### Behavior interface\n~~~\n## Still code\n~~~~\n"
    model = "### Behavior interface\n```python\n## Not the end\nclass Model: ...\n```\n"
    suffix = "## Functional Behavior\nCompute the result.\n"
    assert without_behavior_interface(prefix + model + suffix) == prefix + suffix


def test_projection_handles_last_section_and_multiple_explicit_sections():
    text = ("# Design\n## BEHAVIOR INTERFACE ##\nHidden here.\n"
            "### Behavior interface\nNested model notes.\n"
            "## Functional Behavior\nKeep this.\n### Behavior interface\nHidden too.")
    assert without_behavior_interface(text) == "# Design\n## Functional Behavior\nKeep this.\n"


def test_no_section_is_a_noop_and_scattered_prose_is_not_guessed_away():
    text = "# Design\n## Reset Behavior\nHardware state clears. Model.reset() has no effect.\n"
    assert without_behavior_interface(text) == text
