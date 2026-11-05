"""Ordinary property execution keeps descriptor semantics and real source hits."""

import pytest

from behave.sim import api
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.isolated import IsolatedModel, hits_of
from behave.sim.behavior.lowering import lower_procedure
from behave.sim.behavior.source_plan import plan_source

from .test_api_path import contract

SOURCE = """class M:
    def reset(self):
        self._value = 0
    @property
    def value(self):
        if self._value < 2:
            return self._value + 1
        return self._value
    @value.setter
    def value(self, value):
        if value < 0:
            self._value = 0
        else:
            self._value = value
    @property
    def unused(self):
        if self._value:
            return 9
        return 8
    def process(self, a):
        self.value = a
        return {'y': self.value}
"""

REFERENCE = """class M:
    def reset(self): pass
    def process(self, in_a): return {'out_y': in_a + 1 if in_a < 2 else in_a}
"""


def test_property_catalogue_follows_reads_and_writes_not_unrelated_descriptors():
    model = source_model(SOURCE, cls="M")
    plan = plan_source(model, SOURCE)
    assert {site.owner[0] for site in plan.coverage_sites} == {"process", "value"}
    assert {"value.fget", "value.fset", "unused.fget"} <= model.helpers.keys()
    assert model.state_contract() == ("_value",)
    assert model.step_state_contract() == ("_value",)
    assert model.step_state_reads() == ("_value",)
    with pytest.raises(LoweringError) as caught:
        lower_procedure(SOURCE, cls="M", input_widths=[8], outputs=["y"])
    assert caught.value.code == "property-lowering-unsupported"


def test_property_atomic_ownership_tracks_the_setter_storage_not_the_descriptor():
    from behave.sim.behavior.multiclock import ownership_from_source
    from tests.support.atomic_behavior import SOURCE as ATOMIC_SOURCE
    from tests.support.atomic_behavior import _contract

    text = ATOMIC_SOURCE.replace("self.x = 0", "self._x = 0") + """
    @property
    def x(self):
        return self._x
    @x.setter
    def x(self, value):
        self._x = value
"""
    model, ownership = ownership_from_source(text, cls="M", contract=_contract())
    assert model.state_contract() == ("_x", "y")
    assert dict(ownership.owners) == {"_x": ("a",), "y": ("b",)}
    assert source_model(text, cls="M", step="process_b").step_state_reads() == ("_x", "y")


@pytest.mark.isolation
def test_property_augmented_store_keeps_both_accessors_and_their_native_effects():
    text = SOURCE.replace("self.value = a", "self.value += a")
    model = source_model(text, cls="M")
    assert model.step_state_contract() == ("_value",)
    plan = plan_source(model, text)
    with IsolatedModel(text, cls="M") as native:
        native.reset()
        assert native.process(a=0) == {"y": 2}
        assert native.process(a=3) == {"y": 5}
        assert set(hits_of(native)) <= set(plan.manifest)


@pytest.mark.isolation
def test_native_properties_replay_the_actual_accessors_and_record_their_hits():
    plan = plan_source(source_model(SOURCE, cls="M"), SOURCE)
    with IsolatedModel(SOURCE, cls="M") as native:
        native.reset()
        assert [native.process(a=a) for a in (-1, 0, 2)] == [{'y': 1}, {'y': 1}, {'y': 2}]
        assert set(hits_of(native)) <= set(plan.manifest)
        decisions = {goal for site in plan.coverage_sites if site.owner[0] == "value"
                     and site.family == "decision" for goal in site.goals}
        assert decisions and decisions <= set(hits_of(native))


@pytest.mark.isolation
@pytest.mark.parametrize("multifile", [False, True])
@pytest.mark.parametrize("wrong", [False, True])
def test_property_public_baseline_preserves_comparison_and_linking(tmp_path, multifile, wrong):
    text = SOURCE.replace("self.value = a", "self.value = a + 1") if wrong else SOURCE
    files = {"behavior.py": text.encode()}
    if multifile:
        files["behavior.py"] = ("from helper import identity\n" + text.replace(
            "return {'y': self.value}", "return {'y': identity(self.value)}")).encode()
        files["helper.py"] = b"def identity(value): return value\n"
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "behavior.py", "M"))
    source = tmp_path / "candidate.py"
    source.write_text(prepared.source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    task_contract = contract()
    task = api.admit(task_contract, api.realization_of_behavior_source(
        prepared.source, task_contract, file=prepared.file, model=prepared.model), reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(
        source, file=prepared.file, model=prepared.model))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "run", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=4),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
    assert result.decision.authoritative, result.as_dict()
    assert result.decision.passed is not wrong, result.as_dict()


@pytest.mark.parametrize("prefix", ["property = lambda f: f\n", "class property: pass\n"])
def test_shadowed_property_is_not_assumed_to_be_a_builtin(prefix):
    with pytest.raises(LoweringError, match="decorated"):
        source_model(prefix + SOURCE, cls="M")
