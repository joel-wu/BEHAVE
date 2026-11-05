"""State limitations identify paths without changing atomic scoring or privacy."""

import pytest

from behave.sim.behavior.multiclock import _state_copy
from behave.sim.core.failure import RunFailure
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.diagnostics import failure_diagnostic


@pytest.mark.parametrize("value", [[], {}, set(), bytearray(b"x")])
def test_shared_mutable_state_names_both_paths(value):
    state = {"mem": value, "snapshot": (0, value)}
    with pytest.raises(Refused) as caught:
        _state_copy(state)
    reason = caught.value.refusal
    assert reason.kind == "multiclock-state-aliased"
    assert reason.as_dict()["detail"] == {
        "path": "state['snapshot'][1]", "first_path": "state['mem']", "value_type": type(value).__name__}
    assert state["mem"] is state["snapshot"][1]
    failure = RunFailure.verifier("candidate-replay", reason)
    public = failure_diagnostic(failure)
    assert public["disposition"] == "no-result"
    assert public["origin"] == "verifier"
    assert public["reason"]["detail"] == reason.as_dict()["detail"]


def test_cycle_paths_are_distinct_from_repeated_siblings():
    value = []
    value.append(value)
    with pytest.raises(Refused) as caught:
        _state_copy({"mem": value})
    assert caught.value.refusal.kind == "multiclock-state-cyclic"
    assert caught.value.refusal.detail["path"] == "state['mem'][0]"
    assert caught.value.refusal.detail["first_path"] == "state['mem']"


def test_equal_independent_lists_and_shared_immutable_values_still_copy():
    immutable = (1, b"x")
    original = {"mem": [1], "snapshot": [1], "a": immutable, "b": immutable}
    copied = _state_copy(original)
    assert copied == original
    assert copied["mem"] is not copied["snapshot"]
    assert copied["mem"] is not original["mem"]


def test_state_capability_feedback_does_not_export_arbitrary_details_or_hidden_paths():
    reason = Refusal("multiclock-state-aliased", "hidden source", path="state['a']",
                     first_path="state['b']", traceback="private", execution={"private": True})
    visible = failure_diagnostic(RunFailure.verifier("candidate-replay", reason))
    assert "private" not in str(visible) and "hidden source" not in str(visible)
    for phase in ("reference-replay", "comparison", "prepare"):
        assert "reason" not in failure_diagnostic(RunFailure.verifier(phase, reason))
    assert "reason" not in failure_diagnostic(RunFailure.reference("reference-replay", reason))


@pytest.mark.parametrize("reset", [False, True])
def test_runtime_alias_refusal_does_not_commit_a_partial_atomic_slot(reset):
    from behave.sim.behavior.multiclock import DomainInvocation, DomainOwnership, NativeAtomicMachine
    from behave.sim.core.clock_schedule import GlobalEventSlot

    class Model:
        def __init__(self):
            self.mem = [0]
            self.snapshot = None
            self.count = 0

        def process_a(self):
            self.mem[0] = 1
            self.snapshot = (self.mem,)

        def reset_a(self):
            self.process_a()

        def process_b(self):
            self.count += 1

    machine = NativeAtomicMachine(Model, DomainOwnership({
        "mem": ["a"], "snapshot": ["a"], "count": ["b"]}), ("a", "b"))
    before = {"mem": [0], "snapshot": None, "count": 0}
    invocations = [DomainInvocation("b")]
    if not reset:
        invocations.append(DomainInvocation("a"))
    with pytest.raises(Refused, match="multiclock-state-aliased") as caught:
        machine.step(GlobalEventSlot(0, ("a", "b"), resets=("a",) if reset else ()),
                     invocations)
    assert caught.value.refusal.detail["first_path"] == "state['mem']"
    assert caught.value.refusal.detail["path"] == "state['snapshot'][0]"
    assert machine.kernel.state == before
    assert vars(machine.model) == before
