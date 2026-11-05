"""Memory-state identity is exact, but need not be serialized at every slot."""

import copy
import hashlib
import pickle
import sys

import pytest

from behave.sim.core import memory
from behave.sim.core.memory import MemoryEnvironmentState, MemoryState
from behave.sim.core.record_value import canonical_json


def _digest(state):
    return hashlib.sha256(canonical_json(state.as_dict()).encode()).hexdigest()


@pytest.mark.parametrize("cells", [(), ((0, 7),), tuple((i, i) for i in range(1024))])
def test_memory_identity_is_computed_only_when_consumed(monkeypatch, cells):
    serialized = []

    def counted(value):
        serialized.append(value)
        return canonical_json(value)

    monkeypatch.setattr(memory, "canonical_json", counted)
    inner = MemoryState(cells=cells, pending={None: [(3, 7, 1)]},
                        responses={None: (1, 5)}, writes=())
    state = MemoryEnvironmentState({"ram": inner})
    same = MemoryEnvironmentState({"ram": inner})
    assert state == same and hash(state) == hash(same)
    assert not serialized
    assert state.state("ram") is inner
    assert state.digest == _digest(state)
    assert state.digest == _digest(state)
    assert len(serialized) == 1


def test_empty_memory_environment_also_avoids_per_slot_serialization(monkeypatch):
    monkeypatch.setattr(memory, "canonical_json", lambda _: pytest.fail("unused identity"))
    assert MemoryEnvironmentState() == MemoryEnvironmentState()


def test_lazy_memory_identity_detaches_inputs_and_roundtrips():
    cells = [[1, 7]]
    pending = {None: [[1, 7, 2]]}
    inner = MemoryState(cells=cells, pending=pending, responses={None: None}, writes=cells)
    rows = [["ram", inner]]
    state = MemoryEnvironmentState(rows)
    expected = _digest(state)
    cells[0][1] = 4
    pending[None][0][1] = 4
    rows[0][:] = ["changed", MemoryState()]
    rows.clear()
    assert state._digest is None
    restored = (copy.copy(state), copy.deepcopy(state), pickle.loads(pickle.dumps(state)),
                MemoryEnvironmentState.from_dict(state.as_dict()),
                MemoryEnvironmentState.from_record(state.as_dict()))
    for other in restored:
        assert other == state and other.digest == expected
        with pytest.raises(AttributeError):
            other.__init__()
    assert state.digest == expected


def test_memory_encoding_errors_still_occur_at_construction():
    with pytest.raises(UnicodeEncodeError):
        MemoryEnvironmentState({"\ud800": MemoryState()})
    with pytest.raises(UnicodeEncodeError):
        MemoryEnvironmentState({"ram": MemoryState(pending={"\ud800": []})})
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(ValueError, match="integer string conversion"):
            MemoryEnvironmentState({"ram": MemoryState(cells={0: 10**1000})})
    finally:
        sys.set_int_max_str_digits(previous)


def test_memory_fallback_values_keep_their_original_identity():
    for name, value in (("内存", 3), ("ram", 1 << 3000)):
        state = MemoryEnvironmentState({name: MemoryState(cells={0: value})})
        assert state._digest is not None
        assert state.digest == _digest(state)


def test_memory_subclass_projection_keeps_constructor_time_identity():
    extra = [1]

    class CustomState(MemoryEnvironmentState):
        def as_dict(self):
            return {**super().as_dict(), "extra": extra}

    state = CustomState()
    expected = _digest(state)
    extra.append(2)
    assert state._digest is not None
    assert state.digest == expected != _digest(state)
