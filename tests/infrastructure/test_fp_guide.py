"""Check the formal FP specification against the implementation.

The numerical expectations were independently audited with Berkeley SoftFloat
3e and a nearest-neighbor BF16 oracle. These tests do not claim the library is
available in the model's sandbox; that is tested in test_eval_shell_environment.
"""

import ast
import re
import struct
from importlib.resources import files

import pytest

from behave.agent.prompting import render, role_messages
from behave.sim.behavior import fp_lib

SPEC = render("shared/guides/fp_lib")
VECTORS = re.findall(
    r"^\| `([a-z0-9_]+\([^`]+\))` \| `([^`]+)` \|$",
    SPEC.split("## Conformance examples\n", 1)[1],
    flags=re.MULTILINE,
)
FINITE_ENCODINGS = re.findall(
    r"^\| (FP32|BF16) \| `(0x[0-9A-F]+)` \| `([^`]+)` \|$",
    SPEC,
    flags=re.MULTILINE,
)

@pytest.mark.parametrize(
    ("call", "expected"),
    [pytest.param(call, expected, id=call) for call, expected in VECTORS],
)
def test_fp_spec_implementation_conformance(call, expected):
    node = ast.parse(call, mode="eval").body
    arguments = [ast.literal_eval(arg) for arg in node.args]
    actual = getattr(fp_lib, node.func.id)(*arguments)
    expected = ast.literal_eval(expected)
    assert actual == expected
    assert type(actual) is type(expected)


@pytest.mark.parametrize(("fmt", "encoding", "expression"), FINITE_ENCODINGS)
def test_fp_spec_exact_format_values(fmt, encoding, expression):
    # All these dyadic expressions are exact in binary64. struct independently
    # encodes them as binary32 and also preserves the specified zero sign.
    expected = eval(expression, {"__builtins__": {}})  # trusted repository math
    encoded = struct.unpack(">I", struct.pack(">f", expected))[0]
    assert expected == struct.unpack(">f", struct.pack(">I", encoded))[0]
    assert encoded == int(encoding, 16) << (16 if fmt == "BF16" else 0)


def test_fp_spec_covers_the_public_functions():
    names = {ast.parse(call, mode="eval").body.func.id for call, _ in VECTORS}
    public_functions = {name for name, value in vars(fp_lib).items()
                        if not name.startswith("_") and callable(value)}
    assert names == public_functions
    assert FINITE_ENCODINGS


def test_fp_spec_is_available_on_demand_not_injected_into_messages():
    from behave.task_execution.solver_settings import system_variables

    root = files("behave.agent").joinpath("prompts")
    assert not root.joinpath("drafts/fp_lib.md").is_file()
    assert root.joinpath("shared/guides/fp_lib.md").is_file()
    index = render("solver/joint/environment")
    messages = role_messages("solver", user_template="shared/task/user", system_template="solver/joint/system",
                             **system_variables("joint"),
                             task_id="example", spec_path="spec.md",
                             public_files_section="", environment_docs=index)
    assert "/docs/fp_lib.md" in index
    assert all(SPEC not in message["content"] for message in messages)
