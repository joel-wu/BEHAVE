"""A frozen source catalogue includes callable values, not only direct calls."""

import pytest

from behave.sim import api
from behave.sim.behavior import goals
from behave.sim.behavior.catalogue import source_frontend_catalogue
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.isolated import IsolatedModel, hits_of

from .test_behavior_public_exact_construction import _contract, _generation, _reference
from .test_command_handler import _BaselineOnly


def _source(body):
    return '''class M:
    def reset(self):
        pass
    def helper(self, a):
        if a:
            return a
        return 0
    def apply(self, fn, a):
        return fn(a)
    def unused(self, a):
        if a:
            return 99
        return 98
    def process(self, a):
''' + "\n".join("        " + line for line in body.splitlines()) + "\n"


@pytest.mark.isolation
@pytest.mark.parametrize("body, owners", [
    ("R = self.helper\nreturn {'y': R(a)}", {"process", "helper"}),
    ("R = self.helper\nS = R\nreturn {'y': S(a)}", {"process", "helper"}),
    ("return {'y': self.apply(self.helper, a)}", {"process", "helper", "apply"}),
])
def test_callable_values_have_source_declared_occurrences_before_replay(body, owners):
    source = _source(body)
    manifest = source_frontend_catalogue(source_model(source), source)
    expected = {
        site.goal(outcome).id
        for site in goals.enumerate_sites(source, "behavior.py")
        if site.owner[0] in owners
        for outcome in site.outcomes
    }
    assert set(manifest) == expected
    with IsolatedModel(source) as model:
        model.reset()
        for a in (0, 1):
            assert model.process(a=a) == {"y": a}
        assert set(hits_of(model)) <= set(manifest)


@pytest.mark.isolation
def test_callable_helper_failure_keeps_its_declared_safety_occurrence():
    source = _source("R = self.helper\nreturn {'y': R(a)}").replace(
        "return 0", "return 1 // a")
    manifest = source_frontend_catalogue(source_model(source), source)
    safety = {goal.id for goal in manifest.values() if goal.cls == "safety"}
    assert len(safety) == 1
    with IsolatedModel(source) as model:
        model.reset()
        with pytest.raises(ZeroDivisionError):
            model.process(a=0)
        assert safety <= set(hits_of(model)) <= set(manifest)


def test_a_function_reference_declares_possible_goals_not_observed_hits():
    source = _source("unused_value = self.helper\nreturn {'y': a}")
    manifest = source_frontend_catalogue(source_model(source), source)
    declared_lines = {goal.where("line") for goal in manifest.values()}
    assert {5, 6, 7} <= declared_lines
    assert not {11, 12, 13} & declared_lines


def test_shadowed_function_values_do_not_resolve_by_spelling():
    source = '''def helper(a):
    if a:
        return 99
    return 98
class M:
    def process(self, helper):
        R = helper
        return {'y': R}
'''
    manifest = source_frontend_catalogue(source_model(source), source)
    assert {goal.where("line") for goal in manifest.values()} == {7, 8}


@pytest.mark.isolation
def test_public_score_does_not_lose_a_locally_named_helper(tmp_path):
    source = _source("R = self.helper\nreturn {'y': R(a)}")
    path = tmp_path / "candidate.py"
    path.write_text(source)
    contract = _contract()
    reference = _reference(source)
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file="candidate.py"), reference=reference)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
        _generation(contract), bounded_analysis=False)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", reference=reference,
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=False))
    assert result.authority_kind == "concrete_evaluation", result.as_dict()
    assert result.evaluation.complete and result.evaluation.passed
