"""Reference replay keeps stream state, hit history and failure ownership."""

import pytest

from behave.sim.behavior.artifact import BehaviorArtifact
from behave.sim.behavior.isolated import (
    Broken,
    IsolatedModel,
    Timeout,
    hits_of,
)

SOURCE = """class M:
    def reset(self):
        self.total = 0
    def process(self, a):
        for j in range(64):
            if a > j:
                self.total += 1
        return {"y": self.total}
"""


@pytest.mark.isolation
@pytest.mark.parametrize("strict", [False, True])
def test_hit_transport_is_linear_and_preserves_the_complete_history(strict):
    with IsolatedModel(SOURCE, strict_module=strict) as model:
        model.reset()
        history = hits_of(model)
        start = model._child.stdout.buffer._seen
        assert model.process(a=32) == {"y": 32}
        one_step = list(history)
        assert one_step
        for _ in range(15):
            model.process(a=32)
        middle = model._child.stdout.buffer._seen
        assert history == one_step * 16
        for _ in range(16):
            model.process(a=32)
        end = model._child.stdout.buffer._seen
        assert history == one_step * 32
        assert model.total == 32 * 32
        # Equal chunks transfer equal hit events, not all preceding chunks.
        assert end - middle < 2 * (middle - start)
        assert hits_of(model) is history
        history.clear()
        model.process(a=32)
        assert history == one_step
        model.reset()
        assert history == []
        assert model.total == 0


def test_concrete_hit_reader_consumes_only_the_new_step(monkeypatch):
    from behave.sim.behavior import artifact

    class History(list):
        copied = 0

        def __iter__(self):
            pytest.fail("concrete replay rescanned the complete run's hit log")

        def __getitem__(self, key):
            assert type(key) is slice
            values = super().__getitem__(key)
            self.copied += len(values)
            return values

    history = History()
    monkeypatch.setattr(artifact, "hits_of", lambda _model: history)
    cursor = 0
    for _step in range(256):
        history.extend(["branch:T", "location:entry", "branch:T"])
        delta, cursor = BehaviorArtifact._new_hits(None, cursor)
        assert delta == ("branch:T", "location:entry", "branch:T")
    assert cursor == len(history) == history.copied == 768


@pytest.mark.parametrize("history, before", [([], 1), ([""], 0), ([None], 0)])
def test_incremental_hit_reader_still_rejects_malformed_records(
        monkeypatch, history, before):
    from behave.sim.behavior import artifact
    from behave.sim.core.refusal import Refused

    monkeypatch.setattr(artifact, "hits_of", lambda _model: history)
    with pytest.raises(Refused, match="behavior-concrete-hit-record-malformed"):
        BehaviorArtifact._new_hits(None, before)


def test_reset_opens_a_new_hit_epoch_without_recording_reset_body_marks(monkeypatch):
    from behave.sim.behavior import artifact

    history = ["step:T"] * 9

    class Model:
        def reset(self):
            history[:] = ["reset-only:T"]

    monkeypatch.setattr(artifact, "hits_of", lambda _model: history)
    count = BehaviorArtifact._reset_hit_epoch(Model())
    assert count == 1
    history.append("step:F")
    assert BehaviorArtifact._new_hits(None, count) == (("step:F",), 2)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("failure", [None, Broken, Timeout, RuntimeError])
def test_source_audit_distinguishes_a_native_failure_from_a_missing_execution(
        monkeypatch, failure):
    from behave.sim.behavior.instance import lower
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = '''class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": 1 // a}
'''
    schema = TransactionSchema(
        [Endpoint("a", "input", 1), Endpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower(source, schema, bound=1)
    if failure is not None:
        def unavailable(*_args, **_kwargs):
            raise failure("the replay service did not execute this step")

        monkeypatch.setattr(IsolatedModel, "process", unavailable)
    got = behavior.source_audit(([{"a": 0}],))
    if failure is None:
        assert got.status is AuditCheckStatus.AGREED
    else:
        assert got.status is AuditCheckStatus.REFUSED
        assert got.kind == "behavior-source-audit-run-failed"
        assert "did not execute this step" in got.why
