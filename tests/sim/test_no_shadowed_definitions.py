"""No module may define one name twice at the top level.

A review found `expected_for` and `reset_then` defined **twice** in `sim/api.py`, with
the later, stale copies winning. Everything written about the new ones was true of code
that never ran: the tests passed, a population sweep of 836 designs ran to completion,
and both used the old implementation, because for the paths those exercised the two
copies happened to agree.

That is the worst shape a defect can take -- it is invisible to every check that asks
whether the code works, because the code that works is not the code that was read. So
the check has to ask a different question, and this is it.

**What it covers, exactly.** A top-level `def` or `class` in a module whose name another
top-level `def` or `class` in the same module already bound. That is the case that
occurred and it is the case this catches.

**What it does not.** A `def` shadowing an earlier assignment or import; a name bound in
a conditional block; a method defined twice in one class body. Each of those is a real
shadowing and none is checked here, so this is a guard against one shape rather than a
proof that no name is ever rebound -- a distinction worth keeping, because a test whose
name promises more than it delivers is the same defect one level up.

Parsed rather than imported. An import gives the surviving binding and says nothing
about what it replaced, which is exactly the blindness being fixed.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src" / "behave"


def _modules():
    return sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.name))
def test_no_top_level_name_is_defined_twice(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    seen, twice = {}, []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        # A conditional redefinition is a different thing -- this walks the module body
        # only, so a `def` inside `if TYPE_CHECKING:` or a `try/except ImportError`
        # fallback is not counted.
        if node.name in seen:
            twice.append(f"{node.name} at lines {seen[node.name]} and {node.lineno}")
        seen[node.name] = node.lineno
    assert not twice, (
        f"{path.relative_to(ROOT.parent)} defines these names twice at the top level, "
        f"so the earlier one is dead code that a reader will still read: {twice}")
