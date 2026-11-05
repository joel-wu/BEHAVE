"""Architecture guidance changes search assignments, not measurement or scoring."""

from dataclasses import replace
from importlib.resources import files

import pytest
from test_ppa import experiment, fixture_task
from test_ppa_planning import study

from behave.ppa.config import Experiment, replica_objective
from behave.ppa.planning import build_experiment
from behave.ppa.prompting import bind_feedback, bind_objective, render_objective


def architecture():
    return replace(experiment(), targets_ns=(), parallelism_lanes=(1, 2, 4, 8, 16, 32))


def test_parallelism_roundtrip_and_assignment():
    exp = architecture()
    assert exp.as_dict()['schema'] == 'behave.ppa-experiment/10'
    assert Experiment.from_dict(exp.as_dict()) == exp
    assert [replica_objective(exp.as_dict(), i)['parallelism_lanes'] for i in range(7)] == [1, 2, 4, 8, 16, 32, 1]
    assert replica_objective(exp.as_dict(), 3)['target_ns'] is None


@pytest.mark.parametrize('changes', [
    {'targets_ns': (), 'parallelism_lanes': ()}, {'parallelism_lanes': (4,)},
    {'targets_ns': (), 'parallelism_lanes': (True,)},
    {'targets_ns': (), 'parallelism_lanes': (4, 4)}, {'targets_ns': (), 'parallelism_lanes': (0,)},
])
def test_invalid_or_ambiguous_objective_rejected(changes):
    with pytest.raises(ValueError):
        replace(experiment(), **changes)


def test_recipe_uses_shared_measurement_and_new_schema():
    recipe = study()
    recipe['measurement'].update(targets_ns=[], parallelism_lanes=[1, 2, 4, 8, 16, 32])
    _, _, contract = fixture_task()
    assert build_experiment(recipe, contract) == architecture()


@pytest.mark.parametrize('sim_feedback', [False, True])
def test_parallelism_prompt_has_no_time_bound_and_keeps_testing_duties(sim_feedback):
    prompt = render_objective(parallelism_lanes='${parallelism_lanes}', sim_feedback=sim_feedback)
    bound = bind_objective(prompt, architecture().as_dict(), 2)
    assert 'approximately 4\nparallel element-processing lanes' in bound
    assert '${' not in bound
    assert 'at most' not in bound
    assert 'not a correctness requirement' in bound
    assert 'does not remove the\ndevelopment workflow' in bound
    assert ('compare_rtl' in bound) == sim_feedback


def test_wrong_prompt_mode_rejected_and_hdl_dollar_kept_literal():
    with pytest.raises(ValueError, match='time-bound prompt'):
        bind_objective(render_objective('${primary_target_ns}'), architecture().as_dict(), 0)
    with pytest.raises(ValueError, match='parallelism prompt'):
        bind_objective(render_objective(parallelism_lanes='${parallelism_lanes}'), experiment().as_dict(), 0)
    assert bind_objective('${parallelism_lanes} $signed(x)', architecture().as_dict(), 3) == '8 $signed(x)'


@pytest.mark.parametrize('exp', [experiment(), architecture()])
@pytest.mark.parametrize('sim_feedback', [False, True])
def test_raw_campaign_template_matches_direct_render(exp, sim_feedback):
    template = files('behave.agent').joinpath('prompts/ppa/objective.md').read_text()
    actual = bind_feedback(bind_objective(template, exp.as_dict(), 0), sim_feedback=sim_feedback)
    expected = render_objective(exp.targets_ns[0] if exp.targets_ns else None,
        parallelism_lanes=exp.parallelism_lanes[0] if exp.parallelism_lanes else None,
        sim_feedback=sim_feedback)
    assert actual.strip() == expected.strip()
    assert '${' not in actual
