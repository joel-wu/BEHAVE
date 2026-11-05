"""One source entry rule for task admission, goals, native calls and lowering."""

import pytest

from behave.sim import api
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.instrumented import load_source
from behave.sim.behavior.isolated import IsolatedModel, _source_protocol_image, hits_of
from behave.sim.behavior.lowering import lower_procedure
from behave.sim.behavior.realization import BEHAVIOR_REALIZATION_VERSION, realization_job
from behave.sim.behavior.source_plan import plan_source

from .test_api_path import _ref, contract


@pytest.mark.parametrize("helper", ("process_aux", "process_coincident", "process_debug"))
def test_task_clocks_bind_handlers_without_inventing_helper_domains(helper):
    from behave.sim.behavior.multiclock import ownership_from_source
    from tests.support.atomic_behavior import SOURCE, _contract

    text = SOURCE + f"\n    def {helper}(self):\n        self.x = 15\n        self.y = 15\n"
    task_contract = _contract()
    # Field-read safety goals take the model's file identity, so the model is
    # extracted under the same file that both catalogues name.
    model, ownership = ownership_from_source(text, file="atomic.py", cls="M", contract=task_contract)
    assert model.domains == ["a", "b"]
    assert helper in model.helpers
    assert all(set(owners) <= {"a", "b"} for _field, owners in ownership.owners)
    realized = realization_job({
        "version": BEHAVIOR_REALIZATION_VERSION, "source": text,
        "file": "atomic.py", "contract": task_contract.as_dict(), "model": "M"})
    assert realized["status"] == "realized", realized
    # Selecting entry methods must not give native and exact two goal lists.
    assert plan_source(model, text, "atomic.py").manifest.as_dict() == (
        _source_protocol_image(text, file="atomic.py", cls="M",
                               clock_domains=("a", "b"))["frontend_catalogue"])


@pytest.mark.isolation
def test_public_atomic_baseline_ignores_unselected_prefixed_helper(tmp_path):
    from tests.support.atomic_behavior import SOURCE, _contract

    text = SOURCE + "\n    def process_debug(self):\n        self.x = 15\n        self.y = 15\n"
    candidate = tmp_path / "atomic.py"
    candidate.write_text(text)
    task_contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    task = api.admit(task_contract, api.realization_of_behavior_source(
        text, task_contract, model="M"), reference=reference)
    generation = api.GenerationSpec(horizon=3, queue_bound=1, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(candidate, model="M"), generation)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "run", reference=reference,
        baseline=api.build_baseline(task_contract, generation, realization=task.realization,
            policy=api.BaselinePolicy(case_limit=2, max_queue_depth=1)),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
    assert result.decision.authoritative and result.decision.passed, result.as_dict()


def source(helper):
    return f"""class M:
    def reset(self): pass
    def {helper}(self, a):
        if a > 3:
            return (a * 2) & 255
        return (a + a) & 255
    def process(self, in_a):
        return {{'out_y': self.{helper}(in_a)}}
"""


@pytest.mark.parametrize("helper", ("process_one", "process_clk", "process_aux"))
def test_prefixed_helper_has_one_shared_entry_and_real_hits(helper):
    text = source(helper)
    model = source_model(text, cls="M")
    assert model.domains == [None]
    assert helper in model.helpers
    facts = _source_protocol_image(text, cls="M")
    assert facts["facts"]["domains"] == {}
    assert facts["facts"]["process_inputs"] == ["in_a"]
    realized = realization_job({
        "version": BEHAVIOR_REALIZATION_VERSION, "source": text,
        "file": "behavior.py", "contract": contract().as_dict(), "model": "M"})
    assert realized["status"] == "realized"
    plan = plan_source(model, text, "behavior.py", resolver=ScopeResolver(model))
    assert plan.manifest.as_dict() == facts["frontend_catalogue"]
    namespace, _, events = load_source(text, goal_plan=plan)
    native = namespace["M"]()
    procedure = lower_procedure(text, cls="M", input_widths=[8], outputs=["out_y"])
    for value in (0, 4, 127, 255):
        _, output, _, error = interp.run_step(procedure, {"in_a": value})
        assert not error
        assert output.transactions == [native.process(in_a=value)]
    coverage = {goal for site in plan.coverage_sites for goal in site.goals}
    assert coverage <= set(events)
    assert set(events) <= set(plan.manifest)


@pytest.mark.isolation
def test_production_child_uses_the_same_prefixed_helper_catalogue():
    text = source("process_one")
    catalogue = api.CatalogueManifest.from_dict(
        _source_protocol_image(text, cls="M")["frontend_catalogue"])
    with IsolatedModel(text, cls="M") as native:
        native.reset()
        for value in (0, 4, 127, 255):
            assert native.process(in_a=value) == {"out_y": (2 * value) & 255}
        assert hits_of(native)
        assert set(hits_of(native)) <= set(catalogue)


@pytest.mark.isolation
def test_public_baseline_scores_a_prefixed_helper_entry(tmp_path):
    text = source("process_clk")
    candidate = tmp_path / "candidate.py"
    candidate.write_text(text)
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, api.realization_of_behavior_source(
        text, task_contract, model="M"), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(candidate, model="M"))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "run", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=100),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False))
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    assert result.evaluation.hits
