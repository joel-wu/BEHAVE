"""Token capabilities and turn exclusions do not prescribe a training algorithm."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.agent.model import ModelRequest
from behave.agent.trajectory import CostLedger, RecordedTurn, Trajectory
from behave.core.records import RecordError, canonical_json, metadata
from behave.core.storage import DirectoryStore
from behave.task_execution.judgment import final_score
from behave.train import training_data as module
from behave.train.slime_rollout import make_samples, sample_payloads
from behave.train.training_data import TokenTrace, TrainingCandidate, TrainingRequirements, prepare_examples


class FixtureResult(SimpleNamespace):
    @property
    def final_score(self):
        return final_score(self.score)


class FixtureVerifier:
    identity = "fixture-token-wire/1"

    def verify(self, trace, request, raw_response):
        if (raw_response != b"native-fixture" or trace.input_ids != (10, 11) or trace.response_ids != (12, 13)
                or trace.response_mask != (1, 1)):
            raise RecordError("fixture-token-mismatch", "tokens are not the actual generated response")


def fixture(tmp_path, monkeypatch, reward=1):
    store = DirectoryStore(tmp_path / "objects")
    ref = store.put(b"provenance")
    policy = TrainingRequirements(FixtureVerifier.identity, ref, ref, ref)
    request = ModelRequest("run:0:0", 0, 0, (), (), {}, 5)
    request_ref = store.put(canonical_json(request.as_dict()))
    trace = TokenTrace(policy.adapter, request_ref, ref, ref, ref, (10, 11), (12, 13), (1, 1), {})
    turn = RecordedTurn(0, request_ref, store.put(b"normalized"), store.put(b"native-fixture"), metadata(trace.as_dict()), None)
    result = FixtureResult(score=reward, task_id="task", attempt_id="candidate", release=ref)
    candidate = TrainingCandidate(ref, result, Trajectory((turn,), (), 0, 0), CostLedger((), 0, 0))
    monkeypatch.setattr(module, "load_candidate", lambda *_: candidate)
    return store, ref, policy, trace, turn, candidate


def test_evicted_history_uses_each_turns_actual_input_tokens_for_training(tmp_path, monkeypatch):
    from behave.agent.attempt import AttemptSpec
    from behave.agent.journal import AttemptJournal
    from behave.agent.loop import run_attempt
    from behave.agent.model import ModelFailure, ModelReply, ToolCall
    from behave.agent.tools import Tool, ToolResult, object_schema
    from behave.agent.trajectory import trajectory
    from behave.core.records import parse_json

    store, ref, policy, _, _, candidate = fixture(tmp_path, monkeypatch)
    journal = AttemptJournal(tmp_path / "events", store)
    requests = []

    class Backend:
        def generate(self, request):
            requests.append(request)
            if request.turn == 3 and request.dispatch == 0:
                return ModelFailure("context_limit", "too-long")
            # Fixture-native token IDs track the exact request size. The export
            # must not reconstruct the pre-eviction conversation or retokenize.
            ids = tuple(range(100, 100 + len(request.messages)))
            trace = TokenTrace(policy.adapter, store.put(canonical_json(request.as_dict())),
                               ref, ref, ref, ids, (200 + request.turn,), (1,), {})
            calls = (ToolCall(str(request.turn), "advance", {}),) if request.turn < 3 else ()
            return ModelReply("", calls, raw=canonical_json(trace.as_dict()), token_data=trace.as_dict())

    class Verifier:
        identity = policy.adapter

        def verify(self, trace, request, raw_response):
            assert TokenTrace.from_dict(parse_json(raw_response)) == trace
            assert trace.input_ids == tuple(range(100, 100 + len(request.messages)))

    result = run_attempt(journal=journal, backend=Backend(), spec=AttemptSpec("window", "solver", {}),
                         initial_messages=({"role": "user", "content": "task"},),
                         tools=(Tool("advance", "fixture", object_schema({}),
                                     lambda *_: ToolResult({"ok": True}), "fixture"),))
    assert result.reason == "stopped"
    candidate = replace(candidate, trajectory=trajectory(journal))
    monkeypatch.setattr(module, "load_candidate", lambda *_: candidate)
    examples = prepare_examples(store, ref, policy, Verifier())
    assert [t.turn for t in examples.eligible] == [0, 1, 2, 3]
    assert [len(t.tokens.input_ids) for t in examples.eligible] == [2, 5, 8, 8]
    assert len(requests[-2].messages) == 11 and len(requests[-1].messages) == 8
    samples = sample_payloads(examples, rollout_id=8, first_index=0, group_index=2)
    assert all(s["reward"] == 1 and s["group_index"] == 2 for s in samples)
    assert samples[-1]["tokens"] == [100, 101, 102, 103, 104, 105, 106, 107, 203]
    assert all(s["loss_mask"] == [1] and s["response_length"] == 1 for s in samples)


def test_structural_tokens_need_explicit_verifier_and_algorithm_capabilities(tmp_path, monkeypatch):
    store, ref, policy, trace, _, _ = fixture(tmp_path, monkeypatch)
    assert TrainingRequirements.from_bytes(canonical_json(policy.as_dict())) == policy
    assert TokenTrace.from_dict(trace.as_dict()) == trace
    assert prepare_examples(store, ref, policy).turns[0].reason == "token-verifier-unavailable"
    examples = prepare_examples(store, ref, policy, FixtureVerifier())
    assert len(examples.eligible) == 1 and examples.eligible[0].tokens.full_loss_mask == (0, 0, 1, 1)
    assert prepare_examples(store, ref, replace(policy, required_signals=("old_log_probs",)),
                            FixtureVerifier()).turns[0].reason == "training-signals-missing"
    payload, = sample_payloads(examples, rollout_id=8, first_index=9)
    assert payload["group_index"] is None and payload["rollout_id"] == 8
    assert payload["index"] == 9 and payload["loss_mask"] == [1, 1]
    assert "rollout_log_probs" not in payload
    assert payload["tokens"] == [10, 11, 12, 13] and payload["reward"] == 1


def test_turn_exclusion_retains_candidate_group_and_zero_reward(tmp_path, monkeypatch):
    store, ref, policy, _, turn, candidate = fixture(tmp_path, monkeypatch, reward=0)
    excluded = replace(turn, turn=1, exclusion="output-length")
    unavailable = replace(turn, turn=2, token_data=None)
    candidate = replace(candidate, trajectory=replace(candidate.trajectory, turns=(turn, excluded, unavailable)))
    monkeypatch.setattr(module, "load_candidate", lambda *_: candidate)
    examples = prepare_examples(store, ref, policy, FixtureVerifier())
    assert [t.status for t in examples.turns] == ["eligible", "excluded", "unavailable"]
    payload, = sample_payloads(examples, rollout_id=17, first_index=3, group_index=2)
    assert payload["reward"] == 0 and payload["group_index"] == 2 and payload["rollout_id"] == 17
    assert examples.candidate.billing == candidate.billing


@pytest.mark.parametrize("length", [32769, 131072])
def test_export_keeps_full_verified_sequence_without_extra_length_filter(tmp_path, monkeypatch, length):
    store, ref, policy, trace, turn, candidate = fixture(tmp_path, monkeypatch)
    trace = replace(trace, input_ids=(10,) * (length - 2), signals={"old_log_probs": (-0.1, -0.2)})
    turn = replace(turn, token_data=metadata(trace.as_dict()))
    candidate = replace(candidate, trajectory=replace(candidate.trajectory, turns=(turn,)))
    monkeypatch.setattr(module, "load_candidate", lambda *_: candidate)
    verified = []

    class Verifier:
        identity = policy.adapter

        def verify(self, actual, request, raw_response):
            assert actual == trace and raw_response == b"native-fixture"
            assert request.call_id == "run:0:0"
            verified.append(actual)

    examples = prepare_examples(store, ref, replace(policy, required_signals=("old_log_probs",)), Verifier())
    payload, = sample_payloads(examples, rollout_id=0, first_index=0)
    assert verified == [trace] and len(examples.eligible) == 1
    assert payload["tokens"] == list(trace.input_ids + trace.response_ids)
    assert len(payload["tokens"]) == length and payload["loss_mask"] == [1, 1]
    assert payload["rollout_log_probs"] == [-0.1, -0.2]


def test_no_result_is_independent_of_token_eligibility(tmp_path, monkeypatch):
    store, ref, policy, _, _, _ = fixture(tmp_path, monkeypatch, reward=None)
    examples = prepare_examples(store, ref, policy, FixtureVerifier())
    assert examples.eligible and examples.reward == 0
    assert examples.candidate.result.score is None
    payload, = sample_payloads(examples, rollout_id=0, first_index=0)
    assert payload["reward"] == 0


@pytest.mark.parametrize("change", [{"response_mask": (1,)}, {"response_mask": (True, 0)},
    {"response_ids": (True, 2)}, {"input_ids": (-1,)}, {"signals": {"old_log_probs": [0.1, 0.0]}},
    {"signals": {"values": [1]}}, {"signals": {"values": [float("inf"), 0]}},
    {"signals": {"values": [10**400, 0]}}, {"signals": {"unreviewed": [0, 0]}}])
def test_malformed_tokens_cannot_reach_optimizer(tmp_path, monkeypatch, change):
    _, _, _, trace, _, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises((ValueError, TypeError)):
        replace(trace, **change)


def test_wrong_binding_or_raw_tokens_are_not_silently_dropped(tmp_path, monkeypatch):
    store, ref, policy, trace, turn, candidate = fixture(tmp_path, monkeypatch)
    for modified, code in ((replace(trace, request=ref), "training-request-mismatch"),
                           (replace(trace, response_ids=(99, 13)), "fixture-token-mismatch")):
        changed = replace(turn, token_data=metadata(modified.as_dict()))
        monkeypatch.setattr(module, "load_candidate", lambda *_, changed=changed: replace(candidate,
                            trajectory=replace(candidate.trajectory, turns=(changed,))))
        with pytest.raises(RecordError) as error:
            prepare_examples(store, ref, policy, FixtureVerifier())
        assert error.value.code == code


def test_probability_signals_are_carried_without_advantage_calculation(tmp_path, monkeypatch):
    store, ref, policy, trace, turn, candidate = fixture(tmp_path, monkeypatch)
    trace = replace(trace, signals={"old_log_probs": [-1., -2.], "teacher_log_probs": [-3., -4.], "values": [0.1, 0.2]})
    changed = replace(turn, token_data=metadata(trace.as_dict()))
    monkeypatch.setattr(module, "load_candidate", lambda *_: replace(candidate,
                        trajectory=replace(candidate.trajectory, turns=(changed,))))
    examples = prepare_examples(store, ref, replace(policy, required_signals=("old_log_probs", "values")), FixtureVerifier())
    payload, = sample_payloads(examples, rollout_id=0, first_index=0)
    assert payload["rollout_log_probs"] == [-1., -2.] and payload["teacher_log_probs"] == [-3., -4.]
    assert payload["train_metadata"] == {"values": [0.1, 0.2]}
    assert "advantage" not in payload


def test_explicit_slime_materialization_marks_completed_without_a_group_algorithm(tmp_path, monkeypatch):
    import sys

    store, ref, policy, _, _, _ = fixture(tmp_path, monkeypatch)
    examples = prepare_examples(store, ref, policy, FixtureVerifier())

    class SampleFixture:
        Status = SimpleNamespace(COMPLETED="completed")

        def __init__(self, **values):
            self.__dict__.update(values)

    monkeypatch.setitem(sys.modules, "slime.utils.types", SimpleNamespace(Sample=SampleFixture))
    sample, = make_samples(examples, rollout_id=3, first_index=0)
    assert sample.status == "completed" and sample.group_index is None
    assert sample.rollout_id == 3 and sample.reward == 1
    assert sample.tokens == [10, 11, 12, 13] and sample.loss_mask == [1, 1]
