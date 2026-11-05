"""Approved continuation bundles retain code and prompts through public snapshots."""

import json

import pytest

from behave.runtime import snapshots


@pytest.mark.parametrize("upgrade", [False, True])
def test_continuation_carries_only_explicit_code_and_prompt_updates(tmp_path, upgrade):
    repository, previous = tmp_path / "current", tmp_path / "parent"
    unchanged = {
        "tools/slime_training_job.py": "# same entry\n",
        "pyproject.toml": "# same package\n",
        "uv.lock": "# same dependencies\n",
        "src/behave/agent/prompts/shared/guides/development.md": "same guide\n",
    }
    changed = {
        "src/behave/task_execution/solver_settings.py": "new composition\n",
        "src/behave/agent/prompts/solver/joint/system.md": "${development}\n",
    }
    added = {"src/behave/agent/prompts/shared/development/joint_testbench.md": "joint instructions\n"}
    for root in (repository, previous):
        for name, content in unchanged.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for name, content in changed.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content if root == repository else "old\n")
    for name, content in added.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    split = previous / "data/splits/fixture"
    split.mkdir(parents=True)
    (split.parent / "current.json").write_text(json.dumps({"authored": "fixture"}))
    for name in ("eval.txt", "external_eval.txt"):
        (split / name).write_text("evaluation-task\n")
    parent = tmp_path / "parent-run/checkout"
    record = snapshots.freeze(previous, parent, files=(*unchanged, "data/splits/current.json"))
    overrides = {name: (repository / name).read_bytes() for name in (*changed, *added)} if upgrade else {}
    child = tmp_path / "child-run/checkout"
    saved = snapshots.freeze(parent, child, overrides=overrides)
    node = tmp_path / "node-local"
    assert snapshots.stage(child, node) == saved
    assert snapshots.verify(parent) == record
    for name, content in unchanged.items():
        assert (node / name).read_text() == content
    for name, content in changed.items():
        assert (node / name).read_text() == (content if upgrade else "old\n")
        assert (parent / name).read_text() == "old\n"
    for name, content in added.items():
        assert (node / name).exists() is upgrade
        if upgrade:
            assert (node / name).read_text() == content
        assert not (parent / name).exists()
