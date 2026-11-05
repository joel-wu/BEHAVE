"""Shared body indexes change traversal cost, never command selection."""

import pytest

from behave.sim.symbolic import ir, slicing
from tests.sim.test_loop_cone import _procedure

pytestmark = pytest.mark.solver


def _uncached_run(proc, goals, steps):
    events = slicing.linearize(proc.body)
    cuts = [cut for goal in goals if (cut := slicing.cone(events, goal)) is not None]
    if not cuts:
        return None
    kept = set().union(*(cut.kept for cut in cuts))
    needed = set().union(*(cut.needs for cut in cuts)) & set(proc.state)
    if steps > 1:
        while True:
            earlier = slicing.cone(events, seed=needed)
            kept.update(earlier.kept)
            discovered = needed | (earlier.needs & set(proc.state))
            if discovered == needed:
                break
            needed = discovered
    return kept


@pytest.mark.parametrize("case", ["suffix-chain", "conditional", "array", "nested", "failure"])
@pytest.mark.parametrize("steps", [1, 3])
def test_indexed_run_and_audit_match_uncached_occurrence_closure(case, steps, monkeypatch):
    proc, _ = _procedure(case)
    goals = tuple(sorted(ir.goals_of(proc.body))) + ("absent",)
    expected = _uncached_run(proc, goals, steps)
    indexed = slicing.index(proc)
    site = next(i for i, (command, _) in enumerate(indexed.events)
                if isinstance(command, ir.Mark) and command.goal == "hit")
    before = slicing.occurrence_run(proc, "hit", (site,), steps-1, steps)

    def rebuilt(_events):
        raise AssertionError("an existing immutable index must not be reconstructed")

    monkeypatch.setattr(slicing, "_loop_parents", rebuilt)
    monkeypatch.setattr(slicing, "_goal_positions", rebuilt)
    assert slicing.for_run(proc, goals, steps, _index=indexed) == expected
    assert slicing.audit(proc, goals, expected, _index=indexed) == ()
    after = slicing.occurrence_run(proc, "hit", (site,), steps-1, steps, _index=indexed)
    assert after.keeps == before.keeps
    assert slicing.audit_occurrence_run(proc, "hit", (site,), steps-1, steps,
                                        after, _index=indexed) == ()


@pytest.mark.parametrize("operation", ["run", "audit", "occurrence"])
def test_call_without_an_index_builds_body_metadata_once(operation, monkeypatch):
    proc, _ = _procedure("failure")
    goals = tuple(sorted(ir.goals_of(proc.body)))
    all_commands = {id(command) for command in ir.walk(proc.body)}
    calls = {"_loop_parents": 0, "_goal_positions": 0}

    def counted(name, original):
        def build(events):
            calls[name] += 1
            return original(events)
        return build

    for name in calls:
        monkeypatch.setattr(slicing, name, counted(name, getattr(slicing, name)))
    if operation == "run":
        slicing.for_run(proc, goals, 3)
    elif operation == "audit":
        assert slicing.audit(proc, goals, all_commands) == ()
    else:
        site = next(i for i, (command, _) in enumerate(slicing.linearize(proc.body))
                    if isinstance(command, ir.Mark) and command.goal == "hit")
        slicing.occurrence_run(proc, "hit", (site,), 2, 3)
    assert calls == {"_loop_parents": 1, "_goal_positions": 1}


def test_occurrence_index_preserves_shared_commands_and_rejects_wrong_goal_positions():
    shared = ir.Mark("hit")
    proc = ir.Procedure((), (), (), (), ir._sequence((shared, ir.Mark("other"), shared)))
    indexed = slicing.index(proc)
    assert indexed.goal_positions == {"hit": (0, 2), "other": (1,)}
    with pytest.raises(ValueError, match="not occurrences"):
        slicing.occurrence_run(proc, "hit", (1,), 0, 1, _index=indexed)
