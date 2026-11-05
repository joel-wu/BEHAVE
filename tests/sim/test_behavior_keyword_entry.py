"""Task-owned named calls do not invent a positional ABI for variadic entries."""

import pytest

from behave.sim import api
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.isolated import IsolatedModel, _source_protocol_image, hits_of
from behave.sim.behavior.realization import BEHAVIOR_REALIZATION_VERSION, realization_job
from behave.sim.behavior.task_boundary import task_call_inputs, task_input_projections

from .test_api_path import _ref, contract


def source(parameters="*args, **values", expression="values['in_a']"):
    check = "        assert not args\n" if "*args" in parameters else ""
    return ("class M:\n"
            "    def reset(self): pass\n"
            f"    def process(self, {parameters}):\n" + check +
            f"        a = {expression}\n"
            "        if a > 3:\n"
            "            return {'y': (a * 2) & 255}\n"
            "        return {'y': (a + a) & 255}\n")


def realize(text):
    return realization_job({
        "version": BEHAVIOR_REALIZATION_VERSION, "source": text,
        "file": "entry.py", "contract": contract().as_dict(), "model": "M"})


@pytest.mark.parametrize("parameters, expression, names", [
    ("**values", "values['in_a']", ("in_a",)),
    ("*args, **values", "values['in_a']", ("in_a",)),
    ("a, **values", "a", ("a",)),
    ("in_a, **values", "in_a", ("in_a",)),
])
def test_keyword_entry_binds_public_fields_without_rewriting_source(parameters, expression, names):
    text = source(parameters, expression)
    assert realize(text)["status"] == "realized"
    model = source_model(text, keyword_calls=True)
    got = task_call_inputs(contract(), model.inputs, extra_keywords=True)
    assert got == names
    assert task_input_projections(contract(), got)[0].fields == (("a", names[0]),)
    facts = _source_protocol_image(text, cls="M")["facts"]
    assert facts["process_kwargs"] is True
    assert facts["process_inputs"] == model.inputs


@pytest.mark.parametrize("parameters", ("*args", "missing, *args"))
def test_positional_tail_without_keyword_receiver_still_requires_a_declared_abi(parameters):
    answer = realize(source(parameters, "0"))
    assert answer["status"] == "source-failed"
    assert answer["failure"]["origin"] == "candidate"
    assert answer["failure"]["reason"]["kind"] == "behavior-boundary-input-binding-incomplete"


@pytest.mark.parametrize("name", ("a", "in_a"))
def test_unused_positional_tail_does_not_reject_a_valid_named_call(name):
    text = source(f"{name}, *args", name)
    assert realize(text)["status"] == "realized"
    with IsolatedModel(text, cls="M") as model:
        model.reset()
        assert model.process(**{name: 7}) == {"y": 14}
    # Concrete acceptance does not silently add an exact variadic lowering.
    with pytest.raises(LoweringError):
        source_model(text)


@pytest.mark.parametrize("parameters", ("a, in_a, **values", "missing, **values"))
def test_keyword_capture_does_not_mask_an_ambiguous_or_missing_required_parameter(parameters):
    answer = realize(source(parameters, "0"))
    assert answer["status"] == "source-failed"
    assert answer["failure"]["reason"]["kind"] == "behavior-boundary-input-binding-incomplete"


def test_exact_frontend_does_not_ignore_a_keyword_dictionary():
    from behave.sim.behavior.lowering import lower_procedure

    with pytest.raises(LoweringError) as caught:
        lower_procedure(source(), input_widths=[8], outputs=["y"])
    assert caught.value.code == "step-signature-unsupported"


def test_native_keyword_calls_keep_empty_varargs_and_real_source_hits():
    text = source()
    catalogue = api.CatalogueManifest.from_dict(
        _source_protocol_image(text, cls="M")["frontend_catalogue"])
    with IsolatedModel(text, cls="M") as model:
        model.reset()
        for a in (0, 4, 127, 255):
            assert model.process(in_a=a) == {"y": (a * 2) & 255}
        assert hits_of(model)
        assert set(hits_of(model)) <= set(catalogue)


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", (False, True))
def test_public_keyword_baseline_produces_a_real_positive_or_negative_score(tmp_path, wrong):
    text = source(expression="values['in_a'] + 1" if wrong else "values['in_a']")
    path = tmp_path / "entry.py"
    path.write_text(text)
    reference = _ref()
    task_contract = contract()
    realization = api.realization_of_behavior_source(text, task_contract, model="M")
    task = api.admit(task_contract, realization, reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=100),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False))
    assert result.decision is not None and result.decision.authoritative
    assert result.decision.passed is not wrong


@pytest.mark.cosim
def test_same_keyword_binding_on_candidate_and_reference(tmp_path):
    # The reference adapter's existing output ABI uses physical port names.
    # Both sides execute identical source and the same named input binding.
    text = source().replace("{'y':", "{'out_y':")
    path = tmp_path / "entry.py"
    path.write_text(text)
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": text.encode()},
        config={"class": "M"})
    task_contract = contract()
    realization = api.realization_of_behavior_source(text, task_contract, model="M")
    task = api.admit(task_contract, realization, reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=100),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False))
    assert result.decision is not None and result.decision.authoritative
    assert result.decision.passed
