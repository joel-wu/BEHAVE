"""A fixed, complete baseline owns pass eligibility; Search only adds evidence."""

import pytest

from behave.sim import api, evaluation, generation
from behave.sim.evaluation_policy import verdict
from behave.sim.scoring_policy import active_scoring
from tests.support.device_boundary import prepare_device_command
from tests.support.reference import load

from .test_api_path import Doubling, _ref, contract, realization
from .test_command_handler import _BaselineOnly
from .test_replay_suite import CountingDevice, _requests


def _command(tmp_path, *, artifact=None, reference=None):
    c, reference = contract(), reference or _ref()
    task = api.admit(c, realization(), reference=reference)
    return prepare_device_command(
        task, artifact or Doubling(), run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=100),
        providers=(_BaselineOnly(),), search_policy=api.SearchPolicy(time_budget_s=600),
        reference=reference, run_analysis=False, generation_spec=None)


def test_search_pool_keeps_the_fixed_baseline_workload():
    c, seeds = contract(), _requests(tail=0)
    extra = api.Stimulus(queues={"in": [(3,)]}, replay_policy=seeds[0].replay_policy)
    fixed = api.build_replay_suite(c, seeds)
    expanded = api.build_replay_suite(c, (*seeds, extra), baseline=seeds)
    assert expanded.required_stimuli == fixed.stimuli
    assert expanded.continuous[0] == fixed.continuous[0]
    assert [s.depth("in") for s in expanded.continuous] == [200, 201]
    assert len({s.identity_bytes for s in expanded.stimuli}) == len(expanded.stimuli)
    unchanged = api.build_replay_suite(c, seeds, baseline=seeds)
    assert unchanged.stimuli == fixed.stimuli
    with pytest.raises(ValueError, match="baseline must belong"):
        api.build_replay_suite(c, seeds, baseline=(extra,))


@pytest.mark.parametrize("bad_extra", [False, True])
def test_search_budget_retains_complete_baseline_and_any_new_counterexample(monkeypatch, bad_extra):
    class Candidate(Doubling):
        def settle(self, presented, controls):
            result = super().settle(presented, controls)
            if (bad_extra and presented is not None and presented[0] == 3
                    and controls["in_valid"] and result["in_ready"]):
                self.next_held = (99,)
            return result

    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "1")
    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    execution = evaluation.ReplayExecution(task, Candidate(), reference, catalogue)
    seeds = _requests(tail=0)
    fixed = api.build_replay_suite(c, seeds)
    extra = api.Stimulus(queues={"in": [(3,)]}, replay_policy=seeds[0].replay_policy)
    expanded = api.build_replay_suite(c, (*seeds, extra), baseline=seeds)
    with api.scoring_scope(api.ScoringPolicy()) as budget:
        baseline = execution.evaluate(fixed)
        assert baseline.complete and baseline.passed
        if bad_extra:
            assert execution.evaluate((extra,)).failure_witness is not None
        budget.deadline = 0
        result = execution.evaluate(expanded)
    assert result.required_complete and result.budget_exhausted and not result.complete
    assert verdict(result).authoritative
    assert result.passed is not bad_extra
    assert (result.failure_witness is not None) is bad_extra
    assert result.score_scope == "required-baseline"
    prior = {run.stimulus.identity_bytes: run for run in baseline.results}
    assert all(run is prior[run.stimulus.identity_bytes] for run in result.results
               if run.stimulus.identity_bytes in prior)


def test_public_command_completes_both_baseline_phases_before_search(tmp_path, monkeypatch):
    command = _command(tmp_path)
    generate = generation.generate_stimuli
    observed = []

    def search(*args, **kwargs):
        execution = kwargs["_execution"]
        expected = api.build_replay_suite(command.task.contract, command.baseline.stimuli)
        observed.extend(execution._assessments[s.identity_bytes].result for s in expected.stimuli)
        assert all(run.ok for run in observed)
        assert len(observed) > len(command.baseline.stimuli)
        result = generate(*args, **kwargs)
        active_scoring().deadline = 0
        return result

    monkeypatch.setattr(generation, "generate_stimuli", search)
    result = api.handle_command(command)
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.complete
    assert result.evaluation.budget_exhausted
    assert tuple(observed) == result.evaluation.results


def test_baseline_continuous_failure_prevents_search(tmp_path, monkeypatch):
    reference = load({"behavior.py": b"""class M:
    def reset(self): self.n = 0
    def process(self, in_a):
        self.n += 1
        return {'out_y': (in_a + self.n) & 255}
"""}, config={"class": "M"})
    command = _command(tmp_path, artifact=CountingDevice(True), reference=reference)
    generate = generation.generate_stimuli
    stops = []

    def search(*args, **kwargs):
        stops.append(kwargs["_search_stop"])
        return generate(*args, **kwargs)

    monkeypatch.setattr(generation, "generate_stimuli", search)
    result = api.handle_command(command)
    assert stops == ["baseline-failed"]
    assert result.decision.authoritative and not result.decision.passed, result.as_dict()
    runs = result.evaluation.results
    assert all(run.ok for run in runs[:len(command.baseline.stimuli)])
    assert runs[result.evaluation.failure_witness].stimulus.source == "continuous-replay"


def test_required_membership_is_evidence_not_a_telemetry_override(monkeypatch):
    from .test_scoring_budget import _run

    prior, _ = _run(monkeypatch)
    forged = api.ConcreteEvaluation(prior.task, prior.results, prior.hits,
        catalogue=prior.catalogue, requested_stimuli=prior.requested_stimuli,
        telemetry={"required_complete": True, "scoring_budget": {
            "policy": {"boundary": "fully-checked-transaction-prefix"}}},
        budget_exhausted=True)
    assert not forged.required_complete and not verdict(forged).authoritative
    with pytest.raises(ValueError, match="nonempty distinct subset"):
        api.ConcreteEvaluation(prior.task, prior.results, prior.hits,
            catalogue=prior.catalogue, requested_stimuli=prior.requested_stimuli,
            required_stimuli=())


def test_command_rejects_dropping_mandatory_continuous_workload(tmp_path):
    from behave.sim.command import _require_concrete_handoff

    command = _command(tmp_path)
    result = api.handle_command(command)
    prior = result.evaluation
    assert prior.required_complete and result.decision.passed
    reduced = api.ConcreteEvaluation(prior.task, prior.results, prior.hits,
        catalogue=prior.catalogue, requested_stimuli=prior.requested_stimuli,
        required_stimuli=command.baseline.stimuli)
    with pytest.raises(api.Refused, match="sim-command-concrete-baseline-mismatch"):
        _require_concrete_handoff(command, result.generated, reduced)
