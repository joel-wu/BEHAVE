"""One explicit entry selects realization, execution, lowering and source audit."""

import pytest

from behave.sim import api
from behave.sim.behavior.construction import ConstructionFacts
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.realization import (
    BEHAVIOR_REALIZATION_VERSION,
    _realization_identity,
    realization_job,
)
from behave.sim.behavior.task_boundary import lower_task_behavior

from .test_api_path import _ref, contract

SOURCE = """\
class Wrong:
    def reset(self): pass
    def process(self, a): return {'y': 7}
class Selected:
    def reset(self): pass
    def process(self, a): return {'y': (a * 2) & 255}
"""


def _job(source=SOURCE, model="Selected"):
    task = contract()
    return task, realization_job({
        "version": BEHAVIOR_REALIZATION_VERSION, "source": source,
        "file": "entry.py", "contract": task.as_dict(), "model": model})


def test_explicit_entry_is_part_of_source_identity():
    task, selected = _job()
    _, wrong = _job(model="Wrong")
    assert selected["status"] == wrong["status"] == "realized"
    assert _realization_identity(selected, task) != _realization_identity(wrong, task)
    assert _job(model=None)[1]["refusal"]["kind"] == "model-class-ambiguous"
    assert _job(model="Absent")[1]["refusal"]["kind"] == "model-class-not-found"


@pytest.mark.parametrize("parameters", ("a, extra", "", "wrong", "a, in_a"))
def test_proven_signature_fault_is_attributed_at_its_check(parameters):
    task, answer = _job(
        f"class Selected:\n def process(self, {parameters}): return {{'y': 0}}\n")
    with pytest.raises(api.FailureRaised) as caught:
        _realization_identity(answer, task)
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.kind == "behavior-boundary-input-binding-incomplete"
    assert caught.value.failure.determines_score


@pytest.mark.parametrize("parameters", ("a, in_a=None", "in_a, a=None"))
def test_ambiguous_optional_alias_is_not_a_proven_candidate_fault(parameters):
    task, answer = _job(
        f"class Selected:\n def process(self, {parameters}): return {{'y': 0}}\n")
    with pytest.raises(api.Refused, match="optional-inputs-unsupported"):
        _realization_identity(answer, task)


def test_unmapped_optional_parameter_preserves_native_default():
    task, answer = _job(
        "class Selected:\n def process(self, a, extra=7): return {'y': a + extra}\n")
    assert answer["status"] == "realized"
    assert _realization_identity(answer, task)

    from behave.sim.behavior.task_boundary import task_call_inputs

    assert task_call_inputs(task, ("a", "extra"), optional_inputs=("extra",)) == ("a",)
    # Exact lowering still needs to represent the definition-time default. It
    # must report this boundary explicitly instead of treating it as a free input.
    with pytest.raises(api.Refused, match="optional-inputs-unsupported"):
        lower_task_behavior(
            "class Selected:\n def process(self, a, extra=7): return {'y': extra}\n",
            "entry.py", task, 1, cls="Selected")


def test_mapped_optional_argument_uses_the_task_input():
    task, answer = _job(
        "class Selected:\n def process(self, a=7): return {'y': a}\n")
    assert answer["status"] == "realized"
    assert _realization_identity(answer, task)


@pytest.mark.isolation
@pytest.mark.parametrize("default,passed", [(2, True), (3, False)])
def test_public_baseline_uses_actual_unmapped_default(tmp_path, default, passed):
    text = ("class Selected:\n def reset(self): pass\n"
            f" def process(self, a, scale={default}): return {{'y': (a * scale) & 255}}\n")
    candidate = tmp_path / "candidate.py"
    candidate.write_text(text)
    task_contract, reference = contract(), _ref()
    task = api.admit(task_contract, api.realization_of_behavior_source(
        text, task_contract, model="Selected"), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(candidate, model="Selected"))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "run", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=4),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False))
    assert result.decision.authoritative, result.as_dict()
    assert result.decision.passed is passed, result.as_dict()


def test_lowering_and_catalogue_keep_the_explicit_entry():
    instance, _ = lower_task_behavior(
        SOURCE, "entry.py", contract(), 2, cls="Selected",
        construction=ConstructionFacts((), (), (), ()))
    run = instance.replay([{"a": 3}, {"a": 5}])
    assert [step.transactions for step in run.outputs] == [[{"y": 6}], [{"y": 10}]]
    assert not run.broken
    with pytest.raises(LoweringError) as caught:
        lower_task_behavior(SOURCE, "entry.py", contract(), 2,
                            construction=ConstructionFacts((), (), (), ()))
    assert caught.value.code == "model-class-ambiguous"


@pytest.mark.cosim
@pytest.mark.parametrize("packaged", (False, True))
def test_selected_entry_scores_and_audits_through_the_public_path(tmp_path, packaged):
    text, label, selected, other = SOURCE, "entry.py", "Selected", "Wrong"
    if packaged:
        prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
            {"entry.py": b"from implementation import Selected as Chosen\n",
             "implementation.py": SOURCE.encode()}, "entry.py", "Chosen"))
        text, label, selected = prepared.source, prepared.file, prepared.model
        other = next(linked for _module, name, linked in prepared.symbols if name == "Wrong")
    source = tmp_path / "entry.py"
    source.write_text(text)
    task_contract = contract()
    reference = _ref()
    realization = api.realization_of_behavior_source(
        text, task_contract, file=label, model=selected)
    task = api.admit(task_contract, realization, reference=reference)
    spec = api.GenerationSpec(horizon=8, queue_bound=1, reset_hold_cycles=3, drain=4)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(source, file=label, model=selected), spec,
        bounded_analysis=True, analysis_bound=1)
    providers = handle.generation_providers(reference, workdir=tmp_path / "search")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "run",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=providers, probe_budget=0, reference=reference, run_analysis=True,
        analysis_wall_timeout_seconds=180))
    assert result.decision.authoritative and result.decision.passed, result.decision.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    with pytest.raises(api.Refused, match="artifact-mismatch"):
        api.prepare_behavior(
            task, api.BehaviorConfig(source, file=label, model=other), spec)


@pytest.mark.parametrize("files,entry", [
    ({"entry.py": b"class Actual:\n def process(self, a): return a + 3\nChosen = Actual\n"}, "entry.py"),
    ({"entry.py": b"from helper import Actual as Chosen\n",
      "helper.py": b"class Actual:\n def process(self, a): return a + 3\n"}, "entry.py"),
    ({"entry.py": b"from helper import Actual\nChosen = Actual\n",
      "helper.py": b"class Spare:\n def process(self, a): return 99\nclass Actual:\n def process(self, a): return a + 3\n"}, "entry.py"),
])
def test_static_bundle_entry_aliases_preserve_class_identity(files, entry):
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, entry, "Chosen"))
    namespace = {}
    exec(compile(prepared.source, prepared.file, "exec"), namespace)  # noqa: S102 - test fixture
    assert prepared.model == "Chosen"
    assert namespace[prepared.model]().process(4) == 7
    assert prepared.as_dict()["model"] == "Chosen"


def test_single_file_capture_does_not_narrow_the_source_language():
    source = b"class Chosen:\n def process(self, a):\n  try: return {'y': a}\n  except ValueError: return {'y': 0}\n"
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {"entry.py": source}, "entry.py", "Chosen"))
    assert prepared.source.encode() == source
    assert prepared.file == "entry.py"
    assert prepared.model == "Chosen"


@pytest.mark.parametrize("source,model,binding", [
    (b"class Actual:\n def process(self, a): return {f'out_{a}': a}\n", "Missing", None),
    (b"class Actual:\n def process(self, a): return a\ndef instance(): return Actual()\n",
     "instance", "function"),
    (b"def compute(a): return a\nif __name__ == '__main__':\n print(compute(3))\n",
     "Missing", None),
])
def test_wrong_entry_has_source_owned_failure_after_namespace_validation(source, model, binding):
    with pytest.raises(api.FailureRaised) as caught:
        api.prepare_behavior_bundle(api.BehaviorSourceBundle({"entry.py": source}, "entry.py", model))
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE and failure.determines_score
    assert failure.kind == "behavior-entry-not-class"
    assert failure.reason.as_dict()["detail"]["binding"] == binding


def test_dynamic_namespace_is_not_misclassified_as_missing_entry():
    with pytest.raises(api.Refused, match="module-effects"):
        api.prepare_behavior_bundle(api.BehaviorSourceBundle(
            {"entry.py": b"exec('class Chosen: pass')\n"}, "entry.py", "Chosen"))


@pytest.mark.parametrize("initialization", [
    "pass", "self.x = 0", "self.x = [0] * 16", "self.x: int = -1",
    "self.x = self.y = (1, 2)", "self.x = {'a': [0, 1]}",
])
@pytest.mark.parametrize("packaged", (False, True))
def test_missing_process_is_a_source_owned_interface_failure(initialization, packaged):
    source = ("class Selected:\n"
              f" def __init__(self): {initialization}\n"
              f" def reset(self): {initialization}\n"
              " def step(self, a): return {'y': a}\n")
    if packaged:
        with pytest.raises(api.FailureRaised) as caught:
            api.prepare_behavior_bundle(api.BehaviorSourceBundle({
                "entry.py": b"from implementation import Selected\n",
                "implementation.py": source.encode(),
            }, "entry.py", "Selected"))
    else:
        task, answer = _job(source)
        with pytest.raises(api.FailureRaised) as caught:
            _realization_identity(answer, task)
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE and failure.determines_score
    assert failure.kind == "behavior-entry-method-missing"


@pytest.mark.parametrize("source", [
    "class Selected:\n def __init__(self): self.process = self.step\n def step(self, a): return a\n",
    "class Selected:\n def __getattr__(self, name): return self.step\n def step(self, a): return a\n",
    "class Selected:\n def __new__(cls): return 3\n",
    "class Base:\n def process(self, a): return a\nclass Selected(Base): pass\n",
    "def decorate(cls): return cls\n@decorate\nclass Selected: pass\n",
    "class Selected:\n def __init__(self): setattr(self, 'process', self.step)\n def step(self,a): return a\n",
    "class Selected:\n def __init__(self): self.install()\n def install(self): pass\n",
    "class Selected:\n def __init__(self, value=0): self.x = value + 1\n",
    "class Selected:\n reset = 3\n",
    "class Selected:\n process = None\n",
    "class Selected:\n def __init__(self): self.x = 3\n @property\n def x(self): return 0\n",
    "class Selected: pass\ndef install(): return 1\nvalue = install()\n",
    "class Selected:\n def process_slot(self, slot): return {}\n",
    "class Selected:\n def process_clk(self, a): return a\n",
    ("def entry(self, a): return a\n"
     "class Installer:\n @classmethod\n def __class_getitem__(cls, item):\n"
     "  Selected.process = entry\n  return int\n"
     "class Selected:\n def step(self, a): return a\n"
     "def signature(x: Installer[0]): pass\n"),
])
def test_missing_entry_proof_does_not_guess_dynamic_bindings(source):
    import ast

    from behave.sim.behavior.source_entry import missing_process_is_definite

    assert not missing_process_is_definite(ast.parse(source), "Selected")


def test_missing_method_source_facts_keep_typed_failure():
    from behave.sim.behavior.isolated import _decode_source_protocol_image, _source_protocol_image
    from behave.sim.core.failure import CandidateFault

    record = _source_protocol_image("class Selected:\n def step(self, a): return a\n",
                                    "entry.py", "Selected")
    with pytest.raises(CandidateFault) as caught:
        _decode_source_protocol_image(record)
    assert caught.value.refusal.kind == "behavior-entry-method-missing"


@pytest.mark.parametrize("model,binding", [("Missing", None), ("process", "function")])
@pytest.mark.parametrize("files", [
    {"entry.py": b"state = 0\ndef process(a):\n global state\n state += a\n return state\n"},
    {"entry.py": b"from helper import process\n",
     "helper.py": b"state = 0\ndef process(a):\n global state\n state += a\n return state\n"},
    {"entry.py": b"def process(a):\n try:\n  yield a\n except ValueError:\n  pass\n"},
])
def test_wrong_entry_is_not_hidden_by_unexecuted_function_bodies(files, model, binding):
    with pytest.raises(api.FailureRaised) as caught:
        api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "entry.py", model))
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE and failure.determines_score
    assert failure.kind == "behavior-entry-not-class"
    assert failure.reason.detail["binding"] == binding


@pytest.mark.parametrize("files,reason", [
    ({"entry.py": b"def make():\n global Chosen\n Chosen = 3\n return 0\nvalue = make()\n"},
     "dynamic-namespace"),
    ({"entry.py": b"def decorate(f): return f\n@decorate\ndef process(a): return a\n"},
     "definition-effects"),
    ({"entry.py": b"from helper import value\n",
      "helper.py": b"def make():\n global Chosen\n Chosen = 3\n return 0\nvalue = make()\n"},
     "dynamic-namespace"),
    ({"entry.py": b"from helper import Chosen\n",
      "helper.py": b"state = 0\nclass Chosen:\n def process(self, a):\n  global state\n  state += a\n  return state\n"},
     "dynamic-namespace"),
])
def test_entry_classification_does_not_skip_executed_or_selected_code(files, reason):
    with pytest.raises(api.Refused, match=reason):
        api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "entry.py", "Chosen"))


def test_linked_fstring_and_inactive_main_guard_preserve_python_behavior():
    files = {
        "entry.py": b"from helper import Actual as Chosen\n",
        "helper.py": (b"class Actual:\n def process(self, a): return {f'out_{a:02d}': a}\n"
                      b"if '__main__' == __name__:\n raise RuntimeError('not imported')\n"),
    }
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "entry.py", "Chosen"))
    namespace = {"__name__": "behavior"}
    exec(compile(prepared.source, prepared.file, "exec"), namespace)  # noqa: S102 - fixed fixture
    assert namespace["Chosen"]().process(3) == {"out_03": 3}


def test_main_guard_cannot_hide_namespace_rebinding():
    with pytest.raises(api.Refused, match="reserved-name"):
        api.prepare_behavior_bundle(api.BehaviorSourceBundle({
            "entry.py": b"from helper import Actual as Chosen\n",
            "helper.py": (b"__name__ = '__main__'\nclass Actual: pass\n"
                          b"if __name__ == '__main__':\n Actual = None\n"),
        }, "entry.py", "Chosen"))
