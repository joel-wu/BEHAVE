"""Measurement uses public task/run authorities, including clockless tasks."""
import json
from types import SimpleNamespace

import pytest


def test_measurement_writer_preserves_complete_json_without_whole_document_string(tmp_path, monkeypatch):
    from tools.measure_search_score import write

    record = {'concrete': 'identity', 'evaluation': {'passed': True},
              'raw_records': [{'stimulus': {'signed': -17, 'wide': 1 << 256},
                               'trace': {'events': ['entry', '返回'], 'nested': [[0, 1], []]}},
                              {'stimulus': {}, 'trace': None}]}
    # The shared record writer encodes one array item at a time. A whole
    # document dumps() call would recreate the removed multi-GB string.
    monkeypatch.setattr(json, 'dumps', lambda *_args, **_kwargs: pytest.fail('whole-document dumps'))
    path = tmp_path / 'measurement.json'
    write(path, record)
    assert json.loads(path.read_text()) == record
    assert list(tmp_path.iterdir()) == [path]

from behave.sim import api
from tests.sim.test_settled_protocol import contract as settled_contract
from tests.sim.test_task_interface import write_interface
from tools.measure_search_score import (
    analysis_generation_spec,
    baseline_policy,
    behavior_source,
    goal_hit_summary,
    prepare_candidate,
    reference_hit_summary,
    replay_summary,
    runtime_options,
    search_observation,
    search_policy,
    search_receipt,
    task_configuration,
)


def test_measurement_links_declared_behavior_and_local_helpers(tmp_path):
    (tmp_path / "spec.md").write_text("# Example\n## Interface\n### Behavior interface\n"
                                     "```python\nclass Selected:\n    pass\n```\n")
    (tmp_path / "behavior.py").write_text(
        "from helper import increment\nclass Auxiliary:\n    pass\n"
        "class Selected:\n    def process(self, x):\n        return increment(x)\n")
    (tmp_path / "helper.py").write_text("def increment(x):\n    return x + 1\n")
    prepared = behavior_source(tmp_path)
    assert {"behavior.py", "helper.py"} <= set(prepared.modules)
    namespace = {}
    exec(prepared.source, namespace)  # noqa: S102 -- the fixed fixture above, never submitted code
    assert namespace[prepared.model]().process(4) == 5
    assert prepared.model != "Auxiliary"


@pytest.mark.parametrize('fails', [False, True])
def test_analysis_inputs_survive_failed_binding_without_replay(tmp_path, monkeypatch, fails):
    from behave.sim import command
    from behave.sim.core.analysis import _analysis_subject_from_wire
    from tests.sim.test_observation_eligibility import _fixture
    from tools.measure_search_score import command_stage_timings

    task, stimulus, trace, _, catalogue = _fixture()
    failure = api.RunFailure.verifier('comparison', api.Refusal('unavailable', 'fixture'))
    evaluation = api.ConcreteEvaluation(task,
        (api.RunResult.not_compared(stimulus, trace, failure),),
        hits=('source-hit',), catalogue=catalogue)
    identity, before = evaluation.fingerprint, evaluation.as_evidence_dict()
    path, calls, stages = tmp_path / 'analysis_input.json', [], []

    def bind(_command, original):
        assert original is evaluation
        saved = json.loads(path.read_text())
        restored = _analysis_subject_from_wire(task, saved, catalogue)
        assert restored.fingerprint == identity
        assert restored.results[0].trace.fingerprint == trace.fingerprint
        assert restored.results[0].observations_eligible
        calls.append('bind')
        if fails:
            raise TimeoutError('binding budget')
        return 'bound'

    monkeypatch.setattr(command, '_analysis_inputs', bind)
    with command_stage_timings(stages, lambda: None, analysis_input=path):
        if fails:
            with pytest.raises(TimeoutError, match='binding budget'):
                command._analysis_inputs(None, evaluation)
        else:
            assert command._analysis_inputs(None, evaluation) == 'bound'
    assert command._analysis_inputs is bind and calls == ['bind']
    assert stages[0]['input_persist_s'] >= 0
    assert stages[0]['status'] == ('raised' if fails else 'returned')
    assert evaluation.fingerprint == identity and evaluation.as_evidence_dict() == before


@pytest.mark.parametrize('stopped', [False, True])
def test_saved_analysis_reuses_strict_score_owned_inputs_without_rescoring(monkeypatch, stopped):
    from behave.sim import command
    from behave.sim.behavior.exact_service import _analysis_subject_image
    from behave.sim.rtl.analysis import RTLAnalysisRequest
    from tests.sim.test_observation_eligibility import _fixture
    from tools.measure_search_score import analyse_saved

    task, stimulus, trace, _, catalogue = _fixture()
    result = api.RunResult.not_compared(stimulus, trace,
        api.RunFailure.verifier('comparison', api.Refusal('unavailable', 'fixture')))
    evaluation = api.ConcreteEvaluation(task, (result,), hits=('source-hit',), catalogue=catalogue)
    image = _analysis_subject_image(evaluation)
    before = json.dumps(image, sort_keys=True)
    request = SimpleNamespace(task=task, catalogue=catalogue, goal_policy=None, _record_audit=None,
        _analysis_request=object.__new__(RTLAnalysisRequest),
        analysis_policy=SimpleNamespace(feasibility_timeout_ms=None, query_timeout_ms=None, batch=None))
    calls = []
    def bind(original, subject):
        assert original is request and subject.fingerprint == evaluation.fingerprint
        assert subject.results[0].trace.fingerprint == trace.fingerprint
        calls.append('bind')
        if stopped:
            raise TimeoutError('bind stopped')
        return None, api.Refusal('test-no-exact', 'fixture'), None
    def unexpected(*_args, **_kwargs):
        pytest.fail('saved Analysis must not generate, execute or score a concrete pool')
    monkeypatch.setattr(command, '_analysis_inputs', bind)
    monkeypatch.setattr(command, 'handle_command', unexpected)
    monkeypatch.setattr(command.generation_service, 'generate_stimuli', unexpected)
    monkeypatch.setattr(command.evaluation_service, 'evaluate_generated', unexpected)
    outcome = analyse_saved(request, image, wall_seconds=10)
    assert calls == ['bind'] and outcome.concrete == evaluation.fingerprint
    assert outcome.verdict is api.Verdict.REFUSED
    assert outcome.evidence['refusal']['kind'] == (
        'analysis-coordinator-failed' if stopped else 'test-no-exact')
    assert before == json.dumps(image, sort_keys=True)
    # Provenance is deliberately not part of stimulus identity; alter an
    # actual input, which must not be accepted under the saved score's hash.
    image['raw_records'][0]['stimulus']['queues'][0][1][0][0] ^= 1
    with pytest.raises(ValueError, match='another stimulus'):
        analyse_saved(request, image, wall_seconds=10)
    assert calls == ['bind']  # Reject mismatched records before exact construction.


def test_saved_analysis_uses_shared_capability_checks_and_keeps_public_type_boundary():
    from behave.sim import analysis_service
    from behave.sim.analysis_budget import analysis_scope
    from behave.sim.core.analysis import _AnalysisSubject
    from tests.sim.test_observation_eligibility import _fixture

    task, stimulus, trace, _, catalogue = _fixture()
    result = api.RunResult.not_compared(stimulus, trace,
        api.RunFailure.verifier('comparison', api.Refusal('unavailable', 'fixture')))
    evaluation = api.ConcreteEvaluation(task, (result,), hits=('source-hit',), catalogue=catalogue)
    subject = _AnalysisSubject.from_evaluation(evaluation)
    # A provider that fails to carry its frozen policy cannot bypass preflight
    # merely because the concrete view came from a saved worker document.
    exact = SimpleNamespace(instance=SimpleNamespace(goal_capability_required=lambda: True))
    with analysis_scope(10):
        for value in (evaluation, subject):
            outcome = analysis_service._analyse(value, exact)
            assert outcome.concrete == evaluation.fingerprint
            assert outcome.evidence['refusal']['kind'] == 'analysis-goal-policy-missing'
    refused = analysis_service.analyse(subject, exact, wall_timeout_seconds=10)
    assert refused.evidence['refusal']['kind'] == 'analysis-concrete-record-invalid'


@pytest.mark.parametrize('mode', ['matching', 'foreign', 'malformed', 'missing'])
def test_saved_native_observer_checked_before_domain_binding(monkeypatch, mode):
    from behave.sim import command
    from behave.sim.behavior.exact_service import _analysis_subject_image
    from behave.sim.rtl.analysis import RTLAnalysisRequest
    from behave.sim.rtl.native_events import NativeEventPlan
    from behave.sim.rtl.source_bindings import EventBindings
    from tests.sim.test_observation_eligibility import _fixture
    from tools.measure_search_score import analyse_saved

    task, stimulus, trace, _, catalogue = _fixture()
    plan = NativeEventPlan(EventBindings(0, ()), '1' * 64, None)
    raw = trace.as_dict()
    if mode != 'missing':
        raw['source_evidence'] = {'version': 'rtl-native-observation/3',
            'plan': 'malformed' if mode == 'malformed' else plan.evidence_image}
    trace = api.AuditedTrace.from_dict(raw)
    result = api.RunResult.not_compared(stimulus, trace,
        api.RunFailure.verifier('comparison', api.Refusal('fixture', 'fixture')))
    evaluation = api.ConcreteEvaluation(task, (result,), hits=('source-hit',), catalogue=catalogue)
    image = _analysis_subject_image(evaluation)
    exact_request = object.__new__(RTLAnalysisRequest)
    object.__setattr__(exact_request, 'artifact', SimpleNamespace(
        observation_binding=SimpleNamespace(native_plan='2' * 64 if mode == 'foreign' else plan.identity)))
    request = SimpleNamespace(task=task, catalogue=catalogue, goal_policy=None, _record_audit=None,
        _analysis_request=exact_request,
        analysis_policy=SimpleNamespace(feasibility_timeout_ms=None, query_timeout_ms=None, batch=None))
    calls = []

    def bind(*_args):
        calls.append('bind')
        return None, api.Refusal('normal-audit-still-required', 'fixture'), None

    monkeypatch.setattr(command, '_analysis_inputs', bind)
    outcome = analyse_saved(request, image, wall_seconds=10)
    assert outcome.concrete == evaluation.fingerprint
    expected = {'foreign': 'rtl-analysis-saved-observer-mismatch',
                'malformed': 'rtl-analysis-saved-observer-invalid'}
    assert outcome.evidence['refusal']['kind'] == expected.get(mode, 'normal-audit-still-required')
    assert calls == ([] if mode in expected else ['bind'])
    # Matching identity alone must never classify a goal or publish a score.
    assert outcome.verdict is api.Verdict.REFUSED


def test_measurement_behavior_uses_public_preparation_without_bounded_analysis(tmp_path, monkeypatch):
    prepared = SimpleNamespace(source="class Model: pass\n", file="behavior.py", model="Model",
                               dependencies={"helper.py": b"VALUE = 1\n"})
    calls = []
    monkeypatch.setattr(api, "realization_of_behavior_source",
                        lambda source, contract, **kw: calls.append((source, contract, kw)) or "realization")
    monkeypatch.setattr(api, "admit", lambda *a, **kw: (a, kw))
    monkeypatch.setattr(api, "prepare_behavior", lambda task, config, generation, **kw:
                        calls.append((task, config, generation, kw)) or "handle")
    args = SimpleNamespace(checkout=tmp_path, task="task", output=tmp_path,
                           artifact="behavior", analysis=False)
    phases = []

    def phase(name, operation):
        phases.append(name)
        return operation()

    assert prepare_candidate(args, "contract", None, "reference", prepared, phase) == "handle"
    assert phases == ["realization", "admission", "prepare_behavior"]
    assert calls[0] == (prepared.source, "contract", {"file": "behavior.py", "model": "Model"})
    assert calls[1][3] == {"bounded_analysis": False}
    assert (tmp_path / "prepared.py").read_text() == prepared.source
    assert (tmp_path / "helper.py").read_bytes() == prepared.dependencies["helper.py"]


@pytest.mark.parametrize('with_memory', [False, True])
def test_measurement_passes_declared_analysis_schedule_to_rtl_preparation(tmp_path, monkeypatch, with_memory):
    folder = tmp_path / 'task'
    folder.mkdir()
    source = 'module M(input clk); endmodule'
    (folder / 'rtl.sv').write_text(source)
    schedule = api.GlobalSchedule([api.ClockTiming('clk', 2)],
        [api.GlobalEventSlot(0, event=api.EventSlot(ticks=('clk',)))])
    path = tmp_path / 'schedule.json'
    path.write_text(json.dumps(schedule.as_dict()))
    memory = api.MemoryEnvironment([api.MemoryRun('data', initial=((3, 17),),
        ready=((None, (1,)),))])
    memory_path = tmp_path / 'memory.json'
    memory_path.write_text(json.dumps(memory.as_dict()))
    prepared = SimpleNamespace(source=source, as_dict=lambda: {})
    monkeypatch.setattr(api, 'prepare_rtl_bundle', lambda *a: prepared)
    monkeypatch.setattr(api, 'realization_of_rtl_source', lambda *a, **kw: 'realization')
    monkeypatch.setattr(api, 'admit', lambda *a, **kw: 'admitted')
    calls = []
    monkeypatch.setattr(api, 'prepare_rtl', lambda *a, **kw: calls.append((a, kw)) or 'handle')
    args = SimpleNamespace(checkout=tmp_path, task='task', output=tmp_path,
        artifact='rtl', top='M', prepare_seconds=300, analysis_clock_schedule=path,
        analysis_memory_environment=memory_path if with_memory else None)
    assert prepare_candidate(args, 'contract', 'generation', 'reference', None,
        lambda name, operation: operation()) == 'handle'
    assert calls[0][1]['analysis_clock_schedule'].as_dict() == schedule.as_dict()
    bound = calls[0][1]['analysis_memory_environment']
    assert (None if bound is None else bound.as_dict()) == (memory.as_dict() if with_memory else None)
    assert json.loads(memory_path.read_text()) == memory.as_dict()
    assert calls[0][0][2] == 'generation'
    if with_memory:
        memory_path.write_text('{"runs": []}')
        with pytest.raises(TypeError, match='exactly version and runs'):
            prepare_candidate(args, 'contract', 'generation', 'reference', None,
                lambda name, operation: operation())
        assert len(calls) == 1  # Invalid declarations never reach preparation.


@pytest.mark.parametrize('option', ['analysis_clock_schedule', 'analysis_memory_environment'])
def test_measurement_does_not_silently_ignore_rtl_environment_for_behavior(tmp_path, option):
    args = SimpleNamespace(checkout=tmp_path, task='task', artifact='behavior',
                           **{option: tmp_path / 'declared.json'})
    with pytest.raises(ValueError, match='environments require RTL'):
        prepare_candidate(args, None, None, None, None,
                          lambda name, operation: pytest.fail('must reject before preparing'))


@pytest.mark.parametrize("reason,probes,refusal,state", [
    ("queue-empty", 3, None, "queried"),
    ("time-budget-exhausted", 3, None, "queried"),
    ("time-budget-exhausted", 0, None, "no-query-completed"),
    ("goals-covered", 0, None, "no-query-completed"),
    ("baseline-failed", 0, None, "not-exercised"),
    ("provider-unavailable", 0, "unsupported-source", "needs-review"),
])
def test_search_reporting_does_not_confuse_no_new_inputs_with_broken_search(reason, probes, refusal, state):
    row = {"provider": "test", "provider_refusal": refusal, "refusals": {},
           "outcomes": {"local-unsat": probes, "sat": 0}, "candidates_retained": 0,
           "search_budget": {"stop_reason": reason, "probes_completed": probes}}
    result = search_observation([row])
    assert result["directions"][0]["state"] == state
    assert result["exercised"] == (state == "queried")
    assert "passed" not in result
    assert not search_observation([])["exercised"]


@pytest.mark.parametrize("unsupported,unknown,expected", [(1, 0, "needs-review"),
                                                        (1, 1, "needs-review"),
                                                        (0, 1, "queried")])
def test_search_reporting_separates_unsupported_queries_from_unknown(unsupported, unknown, expected):
    row = {"provider": "test", "outcomes": {"unsupported": unsupported, "unknown": unknown},
           "search_budget": {"stop_reason": "time-budget-exhausted",
                             "probes_completed": unsupported + unknown}}
    result = search_observation([row])
    assert result["directions"][0]["state"] == expected
    assert result["exercised"] == (expected == "queried")


@pytest.mark.parametrize("finalize_seconds", [30, 120])
def test_measurement_worker_uses_public_cpu_admission(tmp_path, monkeypatch, finalize_seconds):
    import time

    import tools.measure_search_score as measurement
    from behave.core import resources, runtime_defaults
    from behave.sim.cpu_admission import cpu_capacity, reserve_cpus
    from behave.sim.scoring_policy import active_scoring

    monkeypatch.setattr(resources, "allocated_cpus", lambda: (7, 11))
    monkeypatch.setattr(runtime_defaults, "EVAL_SCORE_FINALIZE_SECONDS", finalize_seconds)
    deadline = time.monotonic() + finalize_seconds + 60
    args = SimpleNamespace(seconds=1200, prepare_seconds=30,
                           score_deadline=deadline, analysis=False)
    receipt = {}

    def measure(got, *, cpus, cpu_usage, scoring_budget):
        assert got is args and cpus == (7, 11)
        assert cpu_capacity() == 2
        assert active_scoring() is scoring_budget
        assert scoring_budget.policy == api.ScoringPolicy(time_budget_s=1200)
        assert scoring_budget.deadline == deadline - finalize_seconds
        assert 0 < scoring_budget.remaining(1200) <= 60
        with api.scoring_scope(scoring_budget.policy) as nested_budget:
            assert nested_budget is scoring_budget
        with reserve_cpus(2) as width:
            assert width == 2
            with reserve_cpus(2) as nested:
                assert nested == 1
        receipt.update(cpu_usage.as_dict())
        return 0

    monkeypatch.setattr(measurement, "_measure", measure)
    assert measurement.worker(args) == 0
    assert cpu_capacity() is None
    assert active_scoring() is None
    assert len(receipt["reservations"]) == 1
    assert receipt["reservations"][0]["workers"] == 2
    assert receipt["reservations"][0]["status"] == "admitted"
    assert receipt["reservations"][0]["released"] is True


def test_measurement_expired_preparation_budget_cannot_become_a_pass(tmp_path, monkeypatch):
    import time

    import tools.measure_search_score as measurement
    from behave.core import resources
    from behave.sim.scoring_policy import active_scoring

    args = SimpleNamespace(
        checkout=tmp_path, output=tmp_path, task="unstarted", class_name="Model",
        seconds=1200, prepare_seconds=300,
        score_deadline=time.monotonic() - 1, analysis=False, analysis_timeout_ms=None,
        default_baseline=True, baseline_cases=None, queue_depth=None,
        probe_budget=None, search_seconds=600)
    monkeypatch.setattr(resources, "allocated_cpus", lambda: (7, 11))

    def prepare(_folder):
        active_scoring().check()
        pytest.fail("expired preparation obtained a fresh scoring budget")

    monkeypatch.setattr(measurement, "task_configuration", prepare)
    assert measurement.worker(args) == 1
    record = json.loads((tmp_path / "measurement.json").read_text())
    assert record["status"] == "scoring-budget-exhausted"
    assert record["error"].startswith("ScoringStopped:")
    assert record["phase"] == "contract" and record["phases"] == []
    assert record["scoring_budget"]["exhausted"]
    assert not record["cpu_admission"]["reservations"]
    assert record["decision"] is None and "evaluation" not in record


@pytest.mark.parametrize("stopped", [False, True])
@pytest.mark.parametrize("failing_stage", ["tool-lineage", "replay"])
def test_returned_decision_is_published_before_optional_replay_diagnostics(tmp_path, monkeypatch, stopped, failing_stage):
    import tools.measure_search_score as measurement
    from behave.sim.core.record_value import freeze_record_value

    args = SimpleNamespace(checkout=tmp_path, output=tmp_path, task="fixture", artifact="rtl",
        class_name="Model", seconds=1200, prepare_seconds=300,
        analysis=False, analysis_timeout_ms=None, default_baseline=True, baseline_cases=None,
        queue_depth=None, probe_budget=None, search_seconds=600, profile=False)
    baseline = api.BaselinePolicy()
    command = SimpleNamespace(baseline=SimpleNamespace(
        policy=baseline, generation_spec=None, stimuli=()), goal_policy=SimpleNamespace(selections=()))
    handle = SimpleNamespace(generation_providers=lambda *a, **kw: (),
                             command_request=lambda **kw: command,
                             _artifact=SimpleNamespace(tool_lineage=lambda: ()))
    source = SimpleNamespace(source="class Model: pass", model="Model", dependencies={}, as_dict=dict)
    decision = {"invocation": "returned-public-result", "passed": True}
    evaluation = SimpleNamespace(passed=True, complete=False, accepted=100, mismatch_count=0,
                                 safety_hits=(), results=(object(),), execution_cost={})
    result = SimpleNamespace(status=SimpleNamespace(value="evaluated"), record_path="/record/result.json",
        failure=freeze_record_value({}), decision=SimpleNamespace(as_dict=lambda: decision),
        evaluation=evaluation, analysis=None,
        generated=SimpleNamespace(searches=(), search_summary={}, baseline=(), stimuli=()))
    # There is deliberately no CommandResult.as_dict(): obtaining the failure
    # field must not serialize unrelated catalogues and admission evidence.
    monkeypatch.setattr(measurement, "task_configuration", lambda p: (SimpleNamespace(inputs=lambda: ()), None))
    monkeypatch.setattr(measurement, "behavior_source", lambda *a: source)
    monkeypatch.setattr(measurement, "prepare_candidate", lambda *a: handle)
    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "handle_command", lambda c: result)

    def failed_diagnostics(*args):
        published = json.loads((tmp_path / "measurement.json").read_text())
        assert published["status"] == "evaluated" and published["decision"] == decision
        assert published["evaluation"]["accepted"] == 100
        assert published["diagnostics"] == {"status": "running", "stage": "tool-lineage-and-replay"}
        assert published["searches"] == []  # Explicitly completed empty search, not missing export.
        assert published["search_summary"] == {}
        assert published["baseline_members"] == published["stimulus_members"] == 0
        if stopped:
            raise api.ScoringStopped()
        raise RuntimeError("diagnostic traversal failed")

    if failing_stage == "tool-lineage":
        handle._artifact.tool_lineage = failed_diagnostics
    else:
        monkeypatch.setattr(measurement, "replay_summary", failed_diagnostics)
    assert measurement._measure(args, cpus=(0,), cpu_usage=SimpleNamespace(as_dict=dict),
        scoring_budget=SimpleNamespace(policy=api.ScoringPolicy(), receipt=dict)) == 1
    record = json.loads((tmp_path / "measurement.json").read_text())
    assert record["status"] == "evaluated" and record["decision"] == decision
    assert record["diagnostics"]["status"] == ("incomplete" if stopped else "failed")
    assert record["diagnostics"]["stage"] == "tool-lineage-and-replay"
    assert "replay" not in record


def test_measurement_captures_analysis_inputs_without_starting_analysis(tmp_path, monkeypatch):
    import tools.measure_search_score as measurement
    from behave.sim import command as commands
    from behave.sim.core.analysis import _analysis_subject_from_wire
    from behave.sim.core.record_value import freeze_record_value
    from tests.sim.test_observation_eligibility import _fixture

    task, stimulus, trace, reference, catalogue = _fixture()
    run = api.RunResult.not_compared(stimulus, trace,
        api.RunFailure.verifier('comparison', api.Refusal('unavailable', 'fixture')))
    evaluation = api.ConcreteEvaluation(task, (run,), hits=('source-hit',), catalogue=catalogue)
    args = SimpleNamespace(checkout=tmp_path, output=tmp_path, task='fixture', artifact='behavior',
        class_name='Model', seconds=1200, prepare_seconds=300, analysis=False, analysis_timeout_ms=None,
        save_analysis_inputs=True, default_baseline=True, baseline_cases=None,
        queue_depth=None, probe_budget=None, search_seconds=600, profile=False)
    command = SimpleNamespace(baseline=SimpleNamespace(policy=api.BaselinePolicy(),
        generation_spec=None, stimuli=(stimulus,)), goal_policy=SimpleNamespace(selections=()))
    def request(**options):
        assert options['capture_analysis'] is True and options['run_analysis'] is False
        return command
    handle = SimpleNamespace(generation_providers=lambda *a, **kw: (),
                             command_request=request)
    source = SimpleNamespace(source='class Model: pass', model='Model', dependencies={}, as_dict=dict)
    result = SimpleNamespace(status=SimpleNamespace(value='evaluated'), record_path='/record/result.json',
        failure=freeze_record_value({}), decision=None, evaluation=evaluation, analysis=None,
        generated=SimpleNamespace(searches=(), search_summary={}, baseline=(stimulus,), stimuli=(stimulus,)))
    calls = []
    monkeypatch.setattr(measurement, 'task_configuration', lambda p: (task.contract, None))
    monkeypatch.setattr(measurement, 'behavior_source', lambda *a: source)
    monkeypatch.setattr(measurement, 'prepare_candidate', lambda *a: handle)
    monkeypatch.setattr(api, 'load_reference', lambda *a, **kw: reference)
    monkeypatch.setattr(api, 'handle_command', lambda c: calls.append(c) or result)
    def unexpected(*a, **kw):
        pytest.fail('saving existing raw records must not start Analysis or replay')
    monkeypatch.setattr(commands, '_analysis_inputs', unexpected)
    monkeypatch.setattr(commands.evaluation_service.ReplayExecution, 'evaluate', unexpected)
    assert measurement._measure(args, cpus=(0,), cpu_usage=SimpleNamespace(as_dict=dict),
        scoring_budget=SimpleNamespace(policy=api.ScoringPolicy(), receipt=dict)) == 0
    assert calls == [command]
    image = json.loads((tmp_path / 'analysis_input.json').read_text())
    restored = _analysis_subject_from_wire(task, image, catalogue)
    assert restored.fingerprint == evaluation.fingerprint
    assert restored.results[0].trace.fingerprint == trace.fingerprint
    record = json.loads((tmp_path / 'measurement.json').read_text())
    assert record['analysis'] is None and record['diagnostics']['status'] == 'complete'


def test_command_receipt_keeps_typed_refusal_without_inventing_a_decision():
    from tools.measure_search_score import command_receipt

    refusal = api.Refusal("fixture-unavailable", "no execution")
    result = SimpleNamespace(status=SimpleNamespace(value="refused"), record_path="",
                             failure=refusal, evaluation=None, decision=None)
    assert command_receipt(result) == {
        "status": "refused", "record_path": "", "failure": refusal.as_dict(),
        "decision": None, "evaluation": None,
    }


@pytest.mark.parametrize("outcome", ["completed", "failed", "timed-out"])
@pytest.mark.parametrize("analysis", (False, True))
def test_measurement_runs_staged_checkout_once_and_preserves_evidence(tmp_path, monkeypatch, outcome, analysis):
    import time

    import tools.measure_search_score as measurement

    checkout = tmp_path / "staged"
    checkout.mkdir()
    evidence = checkout / "retained-input.txt"
    evidence.write_text("original source")
    args = SimpleNamespace(
        checkout=checkout, output=tmp_path / "result", task="task", class_name="Model",
        seconds=1200, prepare_seconds=300,
        top=None, default_baseline=True, queue_depth=None, baseline_cases=None,
        probe_budget=None, search_seconds=600, analysis=analysis, analysis_seconds=73,
        analysis_timeout_ms=None, analysis_horizon=503 if analysis else None,
        analysis_drain_slots=7 if analysis else None,
        analysis_clock_schedule=tmp_path / 'clock.json' if analysis else None,
        analysis_memory_environment=tmp_path / 'memory.json' if analysis else None, profile=False)
    launches, waits, signals = [], [], []

    def wait(*, timeout):
        waits.append(timeout)
        if outcome == "timed-out" and len(waits) == 1:
            raise measurement.subprocess.TimeoutExpired("measurement", timeout)
        return 0 if outcome == "completed" else 1

    def launch(argv, **options):
        launches.append((argv, options))
        return SimpleNamespace(pid=123, wait=wait, poll=lambda: 1)

    monkeypatch.setattr(measurement.sys, "path", list(measurement.sys.path))
    monkeypatch.setattr(measurement.subprocess, "Popen", launch)
    monkeypatch.setattr(measurement.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    began = time.monotonic()
    assert measurement.run(args) == (0 if outcome == "completed" else 1)
    argv, options = launches[0]
    assert len(launches) == 1
    assert argv[2:6] == [str(checkout / "tools/measure_search_score.py"), "_worker",
                         str(checkout), str(args.output)]
    assert options["cwd"] == checkout and options["env"]["PYTHONPATH"] == str(checkout / "src")
    deadline = float(argv[argv.index("--score-deadline") + 1])
    assert began + 1200 <= deadline <= time.monotonic() + 1200
    ceiling = 1200 + (73 if analysis else 0)
    if analysis:
        assert argv[argv.index("--analysis-seconds") + 1] == "73"
        assert argv[argv.index("--analysis-horizon") + 1] == "503"
        assert argv[argv.index("--analysis-drain-slots") + 1] == "7"
        assert argv[argv.index("--analysis-memory-environment") + 1] == str(tmp_path / 'memory.json')
        assert argv[argv.index("--analysis-clock-schedule") + 1] == str(tmp_path / 'clock.json')
    assert 0 < waits[0] <= ceiling
    assert evidence.read_text() == "original source"
    record = json.loads((args.output / "worker.json").read_text())
    assert record["status"] == outcome
    assert record["wall_seconds"] == 1200 and record["worker_seconds"] == ceiling
    assert "runtime_digest" not in record
    if outcome == "timed-out":
        assert record["score"] is None and record["cleanup_state"] == "complete"
        assert signals == [(123, measurement.signal.SIGTERM)]
    else:
        assert signals == []


def _observed_catalogue():
    return api.CatalogueManifest([
        api.Goal("entered", "coverage", family="location", provider="test", policy="test/1"),
        api.Goal("cold", "coverage", family="control-choice", provider="test", policy="test/1"),
        api.Goal("violation", "safety", family="artifact-failure", provider="test", policy="test/1"),
    ])


def test_hit_summary_separates_safety_from_coverage_without_proving_unhit_goals():
    summary = goal_hit_summary(_observed_catalogue(), {
        "baseline": {"entered"}, "search": {"entered"}, "continuous": {"violation"},
    })
    classes = {row["class"]: row for row in summary["classes"]}
    assert classes["coverage"] == {
        "class": "coverage", "declared": 2, "baseline_hit": 1,
        "search_only_hit": 0, "continuous_only_hit": 0, "unhit": 1,
    }
    assert classes["safety"] == {
        "class": "safety", "declared": 1, "baseline_hit": 0,
        "search_only_hit": 0, "continuous_only_hit": 1, "unhit": 0,
    }
    assert summary["unhit"] == ["cold"]
    catalogue = _observed_catalogue()
    assert summary["catalogue"] == {
        "version": "goal-catalogue-view/1", "digest": catalogue.digest(),
        "goals": catalogue.goals_image(), "occurrence_count": len(catalogue.occurrences),
    }
    assert not ({"unreachable", "proved", "discharged"} & summary.keys())


def test_goal_summary_never_expands_or_discards_the_occurrence_manifest(monkeypatch):
    from behave.sim.core.catalogue import OccurrenceKey

    catalogue = api.CatalogueManifest(
        tuple(_observed_catalogue().values()),
        occurrences=tuple((OccurrenceKey("fixture", (index,), "entry"), "entered")
                          for index in range(1000)))
    original = catalogue.canonical

    def no_full_export(_catalogue):
        pytest.fail("reporting expanded the already-persisted occurrence map")

    monkeypatch.setattr(api.CatalogueManifest, "as_dict", no_full_export)
    summary = goal_hit_summary(catalogue, {
        "baseline": {"entered"}, "search": {"cold"}, "continuous": set(),
    })
    assert summary["catalogue"]["goals"] == catalogue.goals_image()
    assert summary["catalogue"]["occurrence_count"] == 1000
    assert summary["catalogue"]["digest"] == catalogue.digest()
    assert "occurrences" not in summary["catalogue"]
    assert catalogue.canonical == original
    assert summary["baseline_hits"] == ["entered"]
    assert summary["search_only_hits"] == ["cold"]
    assert summary["unhit"] == ["violation"]


def test_reference_summary_uses_its_own_records_and_retains_missing_feedback():
    from behave.sim.core.reference_observation import ReferenceSourceObservation

    catalogue = _observed_catalogue()
    observed = ReferenceSourceObservation("reference", "trace", catalogue, frozenset({"cold"}))
    run = SimpleNamespace(was_compared=True, comparison=SimpleNamespace(reference_source=observed))
    missing = SimpleNamespace(was_compared=False)
    summary = reference_hit_summary((run, missing), ("baseline", "continuous"))
    assert summary["complete"] is False
    assert summary["unavailable_runs"] == [1]
    assert summary["goals"]["baseline_hits"] == ["cold"]
    assert summary["records"] == [{"run": 0, "record": observed.fingerprint}]
    assert reference_hit_summary((run, missing), ("baseline", "continuous"), {1})["complete"]
    absent = reference_hit_summary((missing,), ("baseline",))
    assert absent["goals"] is None and absent["complete"] is False


def test_reference_summary_rejects_mixed_catalogues_instead_of_merging_denominators():
    from behave.sim.core.reference_observation import ReferenceSourceObservation

    def run(catalogue):
        observed = ReferenceSourceObservation("reference", "trace", catalogue, frozenset())
        return SimpleNamespace(was_compared=True, comparison=SimpleNamespace(reference_source=observed))

    with pytest.raises(ValueError, match="reference catalogue changed"):
        reference_hit_summary((run(_observed_catalogue()), run(api.CatalogueManifest(()))),
                              ("baseline", "continuous"))


def test_hit_summary_rejects_an_undeclared_hit():
    with pytest.raises(ValueError, match="outside its source catalogue"):
        goal_hit_summary(_observed_catalogue(), {
            "baseline": {"invented"}, "search": set(), "continuous": set(),
        })


def test_measurement_defaults_match_sim_and_overrides_are_explicit():
    args = SimpleNamespace(default_baseline=True, baseline_cases=None, queue_depth=None)
    assert baseline_policy(args).as_dict() == api.BaselinePolicy().as_dict()
    args.default_baseline = False
    assert baseline_policy(args).as_dict() == api.BaselinePolicy().as_dict()
    args.baseline_cases, args.queue_depth = 9, 32
    assert baseline_policy(args).case_limit == 9
    assert baseline_policy(args).max_queue_depth == 32
    args.default_baseline = True
    with pytest.raises(ValueError, match="cannot be combined"):
        baseline_policy(args)


def test_measurement_resource_defaults_follow_the_selected_runtime(monkeypatch):
    from behave.core import runtime_defaults as defaults

    monkeypatch.setattr(defaults, 'EVAL_SCORE_SECONDS', 123)
    monkeypatch.setattr(defaults, 'SIM_RTL_TOOL_SECONDS', 456)
    args = SimpleNamespace(seconds=None, prepare_seconds=None,
                           analysis_timeout_ms=None)
    runtime_options(args)
    assert vars(args) == {"seconds": 123, "prepare_seconds": 456,
                          "analysis_timeout_ms": None}
    args.seconds = 321
    runtime_options(args)
    assert args.seconds == 321


@pytest.mark.parametrize("inputs", [3, 100])
def test_measurement_analysis_reuses_baseline_input_limit_and_explicit_slot_horizon(inputs):
    args = SimpleNamespace(seconds=None, prepare_seconds=None, analysis=True,
                           analysis_seconds=60, analysis_horizon=503)
    contract = settled_contract()
    baseline = api.BaselinePolicy(max_queue_depth=inputs)
    domain = analysis_generation_spec(args, contract, baseline)
    assert domain.queue_bound == inputs
    assert domain.horizon == 503
    replay = api.ReplayPolicy.for_contract(contract)
    assert domain.reset_hold_cycles == replay.reset_hold_cycles
    assert domain.drain == replay.drain_slots
    assert domain.stall_every == replay.stall_every
    args.analysis = False
    assert analysis_generation_spec(args, contract, baseline) is None


@pytest.mark.parametrize("horizon", [None, 0, -1, True, 2.5])
def test_measurement_never_invents_an_analysis_horizon(horizon):
    args = SimpleNamespace(seconds=None, prepare_seconds=None, analysis=True,
                           analysis_seconds=60, analysis_horizon=horizon)
    with pytest.raises(ValueError, match="analysis-horizon"):
        runtime_options(args)


def test_measurement_analysis_drain_override_is_explicit_and_does_not_change_baseline():
    args = SimpleNamespace(seconds=None, prepare_seconds=None, analysis=True,
                           analysis_seconds=60, analysis_horizon=10, analysis_drain_slots=3)
    contract = settled_contract()
    baseline = api.BaselinePolicy(max_queue_depth=4)
    original = baseline.as_dict()
    domain = analysis_generation_spec(args, contract, baseline)
    assert domain.drain == 3 and domain.queue_bound == 4 and domain.horizon == 10
    assert baseline.as_dict() == original


def test_measurement_allows_explicit_zero_drain_for_clockless_sampling():
    args = SimpleNamespace(seconds=None, prepare_seconds=None, analysis=True,
                           analysis_seconds=60, analysis_horizon=4, analysis_drain_slots=0)
    domain = analysis_generation_spec(args, settled_contract(), api.BaselinePolicy(max_queue_depth=4))
    assert domain.drain == 0 and domain.reset_hold_cycles == 0


@pytest.mark.parametrize('drain', [-2, -1, True, 2.5])
def test_measurement_rejects_invalid_analysis_drain_override(drain):
    args = SimpleNamespace(seconds=None, prepare_seconds=None, analysis=True,
                           analysis_seconds=60, analysis_horizon=10, analysis_drain_slots=drain)
    with pytest.raises(ValueError, match='analysis-drain-slots'):
        analysis_generation_spec(args, settled_contract(), api.BaselinePolicy(max_queue_depth=4))


def test_measurement_uses_public_search_time_or_an_explicit_total_probe_count():
    assert search_policy(SimpleNamespace(probe_budget=None, search_seconds=None)).as_dict() == api.SearchPolicy().as_dict()
    for probes in (0, 8):
        policy = search_policy(SimpleNamespace(probe_budget=probes, search_seconds=None))
        assert policy.max_probes == probes
        assert policy.time_budget_s is None
    for seconds in (0, 600):
        policy = search_policy(SimpleNamespace(probe_budget=None, search_seconds=seconds))
        assert policy.time_budget_s == seconds
        assert policy.max_probes is None
    with pytest.raises(ValueError, match="choose a search time"):
        search_policy(SimpleNamespace(probe_budget=8, search_seconds=600))


@pytest.mark.parametrize("seconds", [-1, float("inf"), float("nan")])
def test_measurement_search_time_uses_public_validation(seconds):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        search_policy(SimpleNamespace(probe_budget=None, search_seconds=seconds))


def test_replay_measurement_includes_monitor_hits_and_does_not_replay(tmp_path, monkeypatch):
    from behave.sim.core.hit_vector import HitVector
    from tests.sim.test_command_handler import _request

    request = _request(tmp_path, run_analysis=False)
    result = api.handle_command(request)
    assert result.evaluation is not None
    before = result.evaluation.fingerprint

    def no_slot_name_expansion(_vector):
        pytest.fail("diagnostic export expanded slot hit names instead of the trace union")

    monkeypatch.setattr(HitVector, "__iter__", no_slot_name_expansion)
    summary = replay_summary(result.evaluation, result.generated.baseline)
    assert summary["baseline_hits"] == list(result.evaluation.hits)
    assert summary["search_only_hits"] == []
    assert all(row["baseline"] for row in summary["runs"])
    assert set().union(*(set(row["hits"]) for row in summary["runs"])) == set(result.evaluation.hits)
    assert all(row["admissible"] for row in summary["runs"])
    for row, run in zip(summary['runs'], result.evaluation.results, strict=True):
        restored = api.Stimulus.from_dict(row['stimulus_image'])
        assert restored.identity_bytes == run.stimulus.identity_bytes
        assert restored.fingerprint == row['stimulus']
    assert result.evaluation.fingerprint == before


def test_search_receipt_keeps_existing_witness_links_without_executing():
    original = {"telemetry": {"provider": "rtl/exact-local", "answers": []},
                "generated": ["new-input"],
                "provenance": {"new-input": [{"request": "query", "seed": "baseline"}]}}
    search = SimpleNamespace(as_dict=lambda: original)
    got = search_receipt(search)
    assert got == {**original["telemetry"], "generated": original["generated"],
                   "provenance": original["provenance"]}
    assert "generated" not in original["telemetry"]


def test_replay_measurement_preserves_failed_run_and_memory_comparison(tmp_path):
    from tests.sim.test_api_path import Doubling
    from tests.sim.test_command_handler import _request

    class Wrong(Doubling):
        def settle(self, presented, controls):
            return {**super().settle(presented, controls), self.out_port: 255}

    result = api.handle_command(_request(tmp_path, artifact=Wrong(), run_analysis=False))
    assert result.evaluation.mismatch_count > 0
    summary = replay_summary(result.evaluation, ())
    assert summary["baseline_hits"] == []
    assert summary["search_only_hits"] == list(result.evaluation.hits)
    for row, run in zip(summary["runs"], result.evaluation.results, strict=True):
        assert row["comparison"] == (run.comparison.as_dict() if run.was_compared else None)


@pytest.mark.parametrize("origin", [api.RunFailure.candidate, api.RunFailure.verifier])
def test_partial_observations_are_reported_without_replay_or_a_completed_trace(origin):
    from behave.sim.core.catalogue import catalogue_subset
    from behave.sim.core.failure import ObservedGoals
    from behave.sim.run_assessment import check_run
    from tests.sim.test_api_path import _ref, contract, realization, schedule

    stimulus = schedule([3])
    task = api.admit(contract(), realization(), reference=_ref())
    catalogue = _observed_catalogue()
    failure = origin("execution", api.Refusal("stopped", "original failure"),
        observed=ObservedGoals(stimulus.fingerprint, catalogue_subset(catalogue, {"entered"})))
    assessment = check_run(stimulus, failure)
    evaluation = api.ConcreteEvaluation(task, [assessment.result],
                                       hits=assessment.hits, catalogue=catalogue)
    before = evaluation.fingerprint
    summary = replay_summary(evaluation, [stimulus])
    assert summary["baseline_hits"] == ["entered"]
    assert summary["unhit"] == ["cold", "violation"]
    assert summary["runs"][0]["failure"] == failure.as_dict()
    assert summary["runs"][0]["accepted"] is None
    assert summary["runs"][0]["trace_completion"] is None
    assert not summary["runs"][0]["compared"]
    assert evaluation.fingerprint == before and not evaluation.passed
    if origin == api.RunFailure.verifier:
        assert not evaluation.complete


@pytest.mark.parametrize('completion', [api.Completion.NORMAL, api.Completion.CHECKPOINT])
def test_replay_measurement_distinguishes_complete_comparison_from_complete_trace(completion):
    from behave.sim.run_assessment import assess_run
    from tests.sim.test_observation_eligibility import _fixture

    task, stimulus, original, reference, catalogue = _fixture()
    trace = api.AuditedTrace(stimulus, records=original.records,
        initial_state=dict(original.initial_state), completion=completion,
        domain=original.domain, artifact=original.artifact)
    assessment = assess_run(task, stimulus, trace, reference)
    assert assessment.result.comparison.complete
    evaluation = api.ConcreteEvaluation(task, (assessment.result,),
        hits=assessment.hits, catalogue=catalogue)
    before = evaluation.fingerprint
    row = replay_summary(evaluation, (stimulus,))['runs'][0]
    assert row['trace_completion'] == completion.value and row['compared']
    assert row['comparison']['complete']
    assert evaluation.complete is (completion is api.Completion.NORMAL)
    assert evaluation.fingerprint == before


@pytest.mark.parametrize("search_member", [False, True])
def test_continuous_replay_is_not_reported_as_search(search_member):
    from tests.sim.test_api_path import Doubling, _evaluate, _ref, contract, realization
    from tests.sim.test_replay_suite import _requests

    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    requests = _requests()
    suite = api.build_replay_suite(c, requests)
    evaluation = _evaluate(task, suite, Doubling(), reference)
    assert evaluation.complete
    baseline = requests[:1] if search_member else requests
    summary = replay_summary(evaluation, baseline)
    assert [row["phase"] for row in summary["runs"]] == [
        "baseline", "search" if search_member else "baseline", "continuous"]
    if not search_member:
        assert summary["search_only_hits"] == []
    partition = [set(summary[key]) for key in
                 ("baseline_hits", "search_only_hits", "continuous_only_hits", "unhit")]
    assert set.union(*partition) == set(evaluation.catalogue)
    assert sum(map(len, partition)) == len(evaluation.catalogue)
    for group in summary["families"]:
        assert group["declared"] == sum(group[key] for key in
            ("baseline_hit", "search_only_hit", "continuous_only_hit", "unhit"))


def test_clockless_task_uses_zero_reset_without_spec_inference(tmp_path):
    expected = settled_contract()
    write_interface(tmp_path, expected)
    contract, generation = task_configuration(tmp_path)
    assert contract.digest == expected.digest
    assert contract.clocks == ()
    assert generation is None
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1, max_queue_depth=4))
    assert all(seed.replay_policy.reset_hold_cycles == 0 for seed in baseline.stimuli)


def test_obsolete_neighboring_run_file_is_not_a_policy_source(tmp_path):
    write_interface(tmp_path, settled_contract())
    (tmp_path / 'run.toml').write_text('invalid TOML; horizon = 1')
    _, generation = task_configuration(tmp_path)
    assert generation is None


def test_measurement_without_run_manifest_uses_runtime_baseline(tmp_path):
    from behave.core import runtime_defaults

    write_interface(tmp_path, settled_contract())
    contract, generation = task_configuration(tmp_path)
    assert generation is None
    baseline = api.build_baseline(contract)
    assert baseline.generation_spec is None
    assert baseline.policy.case_limit == runtime_defaults.SIM_BASELINE_CASES
    assert baseline.policy.max_queue_depth == runtime_defaults.SIM_SEQUENCE_LENGTH
    assert all(seed.replay_policy is not None and not seed.slots
               for seed in baseline.stimuli)
    assert all(seed.depth(endpoint.name) == runtime_defaults.SIM_SEQUENCE_LENGTH
               for seed in baseline.stimuli for endpoint in contract.inputs())


def test_measurement_still_checks_explicit_interface_identity(tmp_path):
    write_interface(tmp_path, settled_contract())
    (tmp_path / 'interface.json').write_text('{}')
    with pytest.raises(api.Refused, match='declarations-interface-changed'):
        task_configuration(tmp_path)
