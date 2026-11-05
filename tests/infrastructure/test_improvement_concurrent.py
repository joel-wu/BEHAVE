from threading import Event, Thread

from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.improve.concurrent import run_stage_tasks


def test_task_chains_overlap_across_stages_and_replay_without_reexecution(tmp_path):
    parent = JournalDirectory(tmp_path / "parent")
    tape = Tape(parent.journal("round"))
    second_constructed = Event()
    execution_order = []

    def operation(name, stage):
        for phase in ("scope", "construction"):
            child = JournalDirectory(tmp_path / name / phase)

            def execute():
                if name == "a" and phase == "scope":
                    assert second_constructed.wait(5), "another task's construction was blocked behind this scope"
                execution_order.append((name, phase))
                child.journal(phase).append((), "done", {"name": name})
                if name == "b" and phase == "construction":
                    second_constructed.set()
                return {"status": "pending" if name == "a" else "completed"}

            row = stage(f"{phase}:{name}", execute, child, phase)
        return row["outcome"]

    result = run_stage_tasks(("a", "b"), operation, tape=tape, objects=parent.objects, workers=2)
    assert result == ({"status": "pending"}, {"status": "completed"})
    assert execution_order.index(("b", "construction")) < execution_order.index(("a", "scope"))
    names = [event.payload["name"] for event in tape.events]
    assert sorted(names) == sorted(["scope:a", "construction:a", "scope:b", "construction:b"])
    for name in ("a", "b"):
        assert names.index(f"scope:{name}") < names.index(f"construction:{name}")
    count = len(execution_order)
    assert run_stage_tasks(("a", "b"), operation, tape=Tape(parent.journal("round")),
                           objects=parent.objects, workers=2) == result
    assert len(execution_order) == count


def test_saved_child_is_committed_without_running_it_again(tmp_path):
    parent = JournalDirectory(tmp_path / "parent")
    child = JournalDirectory(tmp_path / "saved")
    child.journal("construction").append((), "done", {"task": "a"})
    head, refs = child.seal("construction")
    parent.objects.publish_owned(child.objects, refs)
    row = {"name": "construction:a", "head": head.as_dict(),
           "outcome": {"status": "review_ready", "task": "a"}}
    executed = []

    def operation(name, stage):
        destination = JournalDirectory(tmp_path / "new" / name)

        def execute():
            assert name == "b", "accepted task must not be rebuilt"
            executed.append(name)
            destination.journal("construction").append((), "done", {"task": name})
            return {"status": "review_ready", "task": name}

        return stage(f"construction:{name}", execute, destination, "construction")["outcome"]

    result = run_stage_tasks(("a", "b"), operation, tape=Tape(parent.journal("round")),
                            objects=parent.objects, workers=2,
                            retained_stages={"construction:a": (row, refs)})
    assert [item["task"] for item in result] == ["a", "b"] and executed == ["b"]
    assert row in [dict(event.payload) for event in parent.journal("round").load()]
    assert run_stage_tasks(("a", "b"), operation, tape=Tape(parent.journal("round")),
                           objects=parent.objects, workers=2) == result
    assert executed == ["b"]


def test_fast_child_is_committed_before_slow_child_finishes(tmp_path):
    parent = JournalDirectory(tmp_path / "parent")
    release_slow, fast_committed = Event(), Event()
    tape = Tape(parent.journal("round"))
    record = tape.record

    def observe(kind, row, closure=()):
        event = record(kind, row, closure)
        if row["name"] == "construction:fast":
            fast_committed.set()
        return event

    tape.record = observe
    def operation(name, stage):
        child = JournalDirectory(tmp_path / name)
        def execute():
            if name == "slow":
                assert release_slow.wait(5)
            child.journal("construction").append((), "done", {"task": name})
            return {"task": name}
        return stage(f"construction:{name}", execute, child, "construction")["outcome"]

    result = []
    worker = Thread(target=lambda: result.extend(run_stage_tasks(("slow", "fast"), operation,
        tape=tape, objects=parent.objects, workers=2)))
    worker.start()
    try:
        assert fast_committed.wait(5), "fast task was waiting for the slow task's commit"
        assert [event.payload["name"] for event in tape.events] == ["construction:fast"]
    finally:
        release_slow.set()
        worker.join(5)
    assert not worker.is_alive()
    assert [row["task"] for row in result] == ["slow", "fast"]
    assert run_stage_tasks(("slow", "fast"), operation, tape=Tape(parent.journal("round")),
                          objects=parent.objects, workers=2) == tuple(result)


def test_round_evidence_accepts_completion_order_but_keeps_stage_dependencies():
    from behave.improve.round_evidence import _stage_order_matches

    groups = (["diagnosis"], ["acquisition:0", "acquisition:1"], ["experience"],
              ["scope:a", "construction:a", "scope:b", "construction:b"], ["data", "learning"])
    actual = ["diagnosis", "acquisition:1", "acquisition:0", "experience",
              "scope:b", "construction:b", "scope:a", "construction:a", "data", "learning"]
    assert _stage_order_matches(actual, *groups)
    assert _stage_order_matches([name for group in groups for name in group], *groups)
    broken = list(actual)
    broken[4:6] = reversed(broken[4:6])
    assert not _stage_order_matches(broken, *groups)
    broken = list(actual)
    broken[3], broken[4] = broken[4], broken[3]
    assert not _stage_order_matches(broken, *groups)
    assert not _stage_order_matches(actual[:-1], *groups)
    assert not _stage_order_matches([*actual, "learning"], *groups)


def test_replay_preserves_legacy_input_order_and_leaves_next_stage(tmp_path):
    parent = JournalDirectory(tmp_path / "parent")
    original = Tape(parent.journal("round"))
    for name in ("slow", "fast"):
        child = JournalDirectory(tmp_path / name)
        child.journal("construction").append((), "done", {"task": name})
        head, closure = child.seal("construction")
        parent.objects.publish_owned(child.objects, closure)
        original.record("round_stage", {"name": f"construction:{name}",
            "head": head.as_dict(), "outcome": {"task": name}}, closure)
    original.record("round_stage", {"name": "data"})
    release_slow = Event()

    def operation(name, stage):
        if name == "slow":
            assert release_slow.wait(5)
        else:
            release_slow.set()
        def execute():
            raise AssertionError("old completed task must not execute again")
        return stage(f"construction:{name}", execute, None, "construction")["outcome"]

    replay = Tape(parent.journal("round"))
    results = run_stage_tasks(("slow", "fast"), operation, tape=replay,
                             objects=parent.objects, workers=2)
    assert [row["task"] for row in results] == ["slow", "fast"]
    assert replay.cursor == 2 and replay.take("round_stage").payload["name"] == "data"
    assert replay.events == original.events


def test_target_stops_dispatch_and_settles_inflight_before_returning(tmp_path):
    parent = JournalDirectory(tmp_path / "parent")
    goal = Event()
    started = []

    def operation(name, stage):
        started.append(name)
        child = JournalDirectory(tmp_path / name)
        def execute():
            if name == "slow":
                assert goal.wait(5), "target was blocked behind the slow child"
            child.journal("construction").append((), "done", {"task": name})
            return {"task": name, "status": "paused" if name == "slow" else "include"}
        return stage(f"construction:{name}", execute, child, "construction")["outcome"]

    selected = []
    def observe(row):
        if row["outcome"]["status"] == "include" and not goal.is_set():
            selected.append(row["name"])
            goal.set()

    result = run_stage_tasks(("slow", "fast", "unstarted"), operation,
        tape=Tape(parent.journal("round")), objects=parent.objects, workers=2,
        should_start=lambda _: not goal.is_set(), on_stage=observe)
    assert set(started) == {"slow", "fast"}
    assert selected == ["construction:fast"]
    assert [(row["task"], row["status"]) for row in result] == [("slow", "paused"), ("fast", "include")]
    assert [e.payload["name"] for e in parent.journal("round").load()] == ["construction:fast", "construction:slow"]
