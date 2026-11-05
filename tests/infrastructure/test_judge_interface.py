"""Sim owns candidate semantics; the caller consumes typed dispositions only."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_data_checks import comparison, replace_source
from test_judge import RTL

from behave.core.artifacts import BlobRef, FileData, FileRef, LoadedSource, SourceArtifact, read_verified
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.checks import check_sources
from behave.sim import api
from behave.task_execution.comparison import ComparisonRequest
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.simulator_judge import (
    _failure,
    _SimulatorService,
)


def test_eval_delegates_complete_generation_record_to_sim(tmp_path, monkeypatch):
    inputs, journal = comparison(tmp_path, real=True)
    image = {"future-public-generation-record": "owned by Sim"}
    inputs = replace(inputs, run_policy=canonical_json(image))
    received = []

    def parse(image):
        received.append(image)
        return object()

    def stop(*args, **kwargs):
        raise api.Refused(api.Refusal("fixture-stop", "no reference execution needed"))

    monkeypatch.setattr(api, "GenerationSpec", SimpleNamespace(from_dict=parse))
    monkeypatch.setattr(api, "load_reference", stop)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert received == [image]
    assert result.code == "judge-reference-unavailable"


def test_eval_can_use_sim_runtime_baseline_without_a_fixed_horizon(tmp_path, monkeypatch):
    inputs, journal = comparison(tmp_path, real=True)
    inputs = replace(inputs, run_policy=b"null")
    received = []

    def prepare(task, config, generation, **kwargs):
        received.append(generation)
        raise api.Refused(api.Refusal("fixture-stop", "no execution needed"))

    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "realization_of_behavior_source", lambda *a, **kw: object())
    monkeypatch.setattr(api, "admit", lambda *a, **kw: object())
    monkeypatch.setattr(api, "prepare_behavior", prepare)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert received == [None]
    assert result.code == "judge-simulator-refused"


@pytest.mark.isolation
@pytest.mark.parametrize("explicit", [False, True])
def test_incomplete_baseline_stays_unresolved_through_diagnostic_publication(tmp_path, monkeypatch, explicit):
    from behave.sim import evaluation
    from behave.sim.scoring_policy import active_scoring, check_scoring

    inputs, journal = comparison(tmp_path, real=True)
    inputs = replace(inputs, run_policy=b"null", judge_policy=replace(
        inputs.policy, search_seconds=0, analysis=None, baseline_cases=3, sequence_length=2).to_bytes())
    if explicit:
        contract = api.TaskContract.from_dict(parse_json(inputs.contract.data))
        baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3, max_queue_depth=2))
        inputs = replace(inputs, replay_stimuli=canonical_json({
            "stimuli": [stimulus.as_dict() for stimulus in baseline.stimuli]}))
    assess = evaluation.assess_checked
    diagnostics = api.evaluation_diagnostics

    def settle(*args, **kwargs):
        result = assess(*args, **kwargs)
        assert result.result.ok
        active_scoring().deadline = 0
        return result

    def publish(*args, **kwargs):
        check_scoring()
        assert active_scoring().finished
        return diagnostics(*args, **kwargs)

    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "1")
    monkeypatch.setattr(evaluation, "assess_checked", settle)
    monkeypatch.setattr(api, "evaluation_diagnostics", publish)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score is None, result.as_dict()
    assert not result.detail["scoring_scope"]["required_complete"]
    assert result.detail["scoring_scope"]["checked_inputs"] > 0
    assert result.diagnostics and result.detail.get("diagnostics_status") != "export_failed"


def test_no_work_budget_stays_no_result_and_eval_reward_is_zero(tmp_path):
    from behave.task_execution.judgment import final_score

    inputs, journal = comparison(tmp_path, real=True)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work, score_deadline=0), journal.objects)
    assert result.score is None and result.origin == "resource-policy"
    assert result.code == "judge-budget-no-effective-tests"
    assert final_score(result.score) == 0


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
@pytest.mark.parametrize("wrong", [False, True])
def test_transaction_prefix_reaches_eval_without_shortening_requested_pool(tmp_path, monkeypatch, wrong):
    from behave.sim import evaluation
    from behave.sim.scoring_policy import active_scoring

    inputs, journal = comparison(tmp_path, real=True, component="rtl")
    if wrong:
        source = inputs.source.files[0].data.replace(
            b"assign out_y = in_a;", b"assign out_y = in_a ^ 8'h01;")
        inputs = replace(inputs, source=replace_source(inputs.source, source))
    inputs = replace(inputs, run_policy=b"null", judge_policy=replace(
        inputs.policy, search_seconds=0, analysis=None, baseline_cases=3, sequence_length=100).to_bytes())
    # Checked transaction prefixes preserve evidence, not a passing baseline.
    policy_type = api.ScoringPolicy
    monkeypatch.setattr(api, "ScoringPolicy",
        lambda **kwargs: policy_type(transaction_checkpoints=True, **kwargs))
    original = evaluation.assess_checked
    checkpoints = []

    def assess(*args, **kwargs):
        assessment = original(*args, **kwargs)
        trace = assessment.result.trace
        if trace is not None and trace.completion is api.Completion.CHECKPOINT:
            checkpoints.append(trace)
            active_scoring().deadline = 0
        return assessment

    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "1")
    monkeypatch.setattr(evaluation, "assess_checked", assess)
    work = tmp_path / "service"
    work.mkdir()
    decided = []
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects,
                                            on_decided=decided.append)
    assert result.score == (0 if wrong else None), result.as_dict()
    assert decided == [result] and checkpoints
    assert checkpoints[0].stimulus.depth("in") == 100
    scope = result.detail["scoring_scope"]
    assert scope["requested_runs"] > 1 and scope["completed_runs"] == 0
    assert scope["checked_transaction_prefixes"] == 1 and scope["accepted"] == 1
    assert scope["checked_inputs"] == 1
    assert scope["unexecuted_runs"] > 0
    assert not scope["required_complete"]
    assert scope["scope"] == "incomplete-baseline"
    assert result.diagnostics and result.analysis is None


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
@pytest.mark.parametrize("component", ["behavior", "rtl"])
@pytest.mark.parametrize("wrong", [False, True])
def test_runtime_baseline_scores_real_sources(tmp_path, component, wrong):
    inputs, journal = comparison(tmp_path, real=True, component=component)
    if wrong:
        body = inputs.source.files[0].data
        body = (body.replace(b"'y': a", b"'y': a ^ 1") if component == "behavior"
                else body.replace(b"assign out_y = in_a;", b"assign out_y = in_a ^ 8'h01;"))
        inputs = replace(inputs, source=replace_source(inputs.source, body))
    inputs = replace(inputs, run_policy=b"null",
                     judge_policy=replace(inputs.policy, search_seconds=0, analysis=None).to_bytes())
    result = check_sources(inputs, journal=journal).result
    assert result.score == (0 if wrong else 1), result.as_dict()
    assert result.detail["baseline"]["search_generated"] == 0
    assert result.diagnostics, result.as_dict()
    documents = {file.path: parse_json(read_verified(journal.objects, file.content)) for file in result.diagnostics}
    summary = documents["summary.json"]
    assert summary["evaluation"] == result.detail["evaluation"]
    assert summary["runs_omitted"] == 0
    if wrong:
        assert summary["affected_runs"] > 0
        for row in summary["runs"]:
            assert documents[row["stimulus"]]["queues"]
            faults = documents[row["observations"]]["comparison"]["faults"]
            assert faults and all(fault["expected"] != fault["observed"] for fault in faults)
    else:
        assert summary["affected_runs"] == 0
    # Reload the frozen receipt, not a second comparison.
    assert check_sources(inputs, journal=journal).result == result


@pytest.mark.parametrize("value", [0, 2, True, 1.0])
def test_invalid_generation_record_is_not_a_candidate_failure(tmp_path, value):
    inputs, journal = comparison(tmp_path, real=True)
    image = {**parse_json(inputs.run_policy), "max_body_steps": value}
    inputs = replace(inputs, run_policy=canonical_json(image))
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score is None and result.origin == "verifier"
    assert result.code == "judge-task-policy-invalid"


def test_invalid_task_policy_keeps_its_exception_in_the_worker_log(tmp_path, caplog):
    inputs, journal = comparison(tmp_path, real=True)
    inputs = replace(inputs, run_policy=b"not JSON")
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score is None and result.code == "judge-task-policy-invalid"
    logged = [record for record in caplog.records if record.name == "behave.task_execution.simulator_judge"]
    assert len(logged) == 1 and logged[0].exc_info
    assert result.detail["error_type"] == logged[0].exc_info[0].__name__


@pytest.mark.parametrize("origin", [api.FailureOrigin.CANDIDATE, api.FailureOrigin.VERIFIER,
                                   api.FailureOrigin.INFRASTRUCTURE])
def test_eval_consumes_public_rtl_manifest_failure_without_reclassification(tmp_path, monkeypatch, origin):
    from test_sim_source_bundle import multifile_inputs

    inputs, journal = multifile_inputs(tmp_path)
    failure = api.RunFailure(origin, "rtl-manifest", api.Refusal("fixture-error", "not a scoring rule"))

    def fail(*args, **kwargs):
        raise api.FailureRaised(failure)

    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "bundle_for_entry", fail)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score == (0 if origin is api.FailureOrigin.CANDIDATE else None)
    assert result.detail["failure"] == failure.as_dict()
    assert result.origin == origin.value


@pytest.mark.parametrize("component", ["behavior", "rtl"])
@pytest.mark.parametrize("search_seconds", [0, 0.25])
def test_eval_only_passes_public_search_controls_not_internal_timeouts(
        tmp_path, monkeypatch, component, search_seconds):
    from behave.task_execution import simulator_judge

    inputs, journal = comparison(tmp_path, real=True, component=component)
    policy = replace(inputs.policy, search_seconds=search_seconds, sequence_length=23)
    inputs = replace(inputs, judge_policy=policy.to_bytes())
    calls = {}
    stopped = api.Refusal("fixture-command-stop", "do not execute a real simulator")

    class Handle:
        def generation_providers(self, reference, *, workdir, enable_local_guidance):
            calls["guidance"] = enable_local_guidance
            return ()

        def command_request(self, **kwargs):
            calls["command"] = kwargs
            return object()

    def behavior_config(path, *, file, model):
        calls["behavior_config"] = (file, model)
        return object()

    def realization(text, contract, *, file, model):
        calls["behavior_realization"] = (file, model)
        return object()

    def rtl_config(path, top, build):
        calls["rtl_config"] = top
        return object()

    def execute(command):
        raise api.Refused(stopped)

    prepared = SimpleNamespace(source="class Linked: pass", file="linked.py", model="Linked",
                               dependencies={}, as_dict=lambda: {"fixture": "prepared"})
    monkeypatch.setattr(simulator_judge, "_link_behavior", lambda *args: prepared)
    monkeypatch.setattr(api, "load_reference", lambda *args, **kwargs: object())
    # Narrow signatures fail if Eval reintroduces an internal timeout override.
    monkeypatch.setattr(api, "realization_of_behavior_source", realization)
    monkeypatch.setattr(api, "realization_of_rtl_source", lambda text, *, top, contract: object())
    monkeypatch.setattr(api, "admit", lambda *args, **kwargs: object())
    monkeypatch.setattr(api, "BehaviorConfig", behavior_config)
    monkeypatch.setattr(api, "RTLConfig", rtl_config)
    monkeypatch.setattr(api, "prepare_behavior", lambda *args, **kwargs: Handle())
    monkeypatch.setattr(api, "prepare_rtl", lambda *args, **kwargs: Handle())
    monkeypatch.setattr(api, "prepare_rtl_bundle", lambda *args: prepared)
    monkeypatch.setattr(api, "handle_command", execute)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score is None and result.detail["reason"] == stopped.as_dict()
    if search_seconds > 0:
        assert calls["guidance"] is True
    else:
        assert "guidance" not in calls
        assert calls["command"]["providers"] == ()
    assert component + "_config" in calls
    if component == "behavior":
        assert calls["behavior_realization"] == calls["behavior_config"] == ("linked.py", "Linked")
        assert not (work / "sources").exists()
    else:
        assert (work / "prepared.sv").is_file()
    command = calls["command"]
    assert "probe_budget" not in command and command["run_analysis"] is False
    assert type(command["search_policy"]) is api.SearchPolicy
    assert command["search_policy"].time_budget_s == search_seconds
    assert command["search_policy"].max_probes is None
    assert command["baseline"].max_queue_depth == 23
    assert command["baseline"].case_limit == policy.baseline_cases
    assert command["baseline"].seed == policy.seed


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
@pytest.mark.parametrize("component", ["behavior", "rtl"])
@pytest.mark.parametrize("wrong", [False, True])
def test_eval_search_expiry_still_scores_and_retains_public_budget_evidence(tmp_path, component, wrong):
    inputs, journal = comparison(tmp_path, real=True, component=component)
    if wrong:
        body = inputs.source.files[0].data
        body = body.replace(b"'y': a", b"'y': a ^ 1") if component == "behavior" else body.replace(
            b"assign out_y = in_a;", b"assign out_y = in_a ^ 8'h01;")
        inputs = replace(inputs, source=replace_source(inputs.source, body))
    policy = replace(inputs.policy, search_seconds=0.001)
    inputs = replace(inputs, judge_policy=policy.to_bytes())
    report = check_sources(inputs, journal=journal)
    assert report.result.score == (0 if wrong else 1), report.as_dict()
    assert report.result.cleanup.quiescent
    evidence = parse_json(journal.objects.read(report.result.evidence[0]))
    assert evidence["request"]["search_policy"] == api.SearchPolicy(time_budget_s=0.001).as_dict()
    assert evidence["request"]["probe_budget"] is None
    assert evidence["evaluation"]["accepted"] > 0
    assert evidence["result"]["analysis"] is None
    searches = evidence["result"]["generation"]["portfolio"]["searches"]
    assert searches
    for search in searches:
        budget = search["telemetry"]["search_budget"]
        # A concrete baseline mismatch settles failure before search starts.
        assert budget["stop_reason"] == ("baseline-failed" if wrong else "time-budget-exhausted")
        assert budget["policy"]["max_probes"] is None


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
@pytest.mark.parametrize("component", ["behavior", "rtl"])
def test_supervised_judge_scores_native_search_counterexample(tmp_path, component, record_property):
    from test_judge import BEHAVIOR, real_task, run

    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.simulator_judge import SimulatorJudge

    behavior = BEHAVIOR.replace(b"'y': a", b"'y': (a ^ 1) if a == 137 else a")
    rtl = RTL.replace(b"assign out_y = in_a;",
                      b"assign out_y = in_a == 8'd137 ? (in_a ^ 8'h01) : in_a;")
    task, submission, journal = real_task(
        tmp_path, behavior=behavior if component == "behavior" else None,
        rtl=rtl if component == "rtl" else None)
    # Use production time/length defaults, but a small baseline that misses the
    # planted branch. This tests search, not chance discovery by random seeds.
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, baseline_cases=2, max_retries=0)
    task = replace(task, judge_policy=policy.to_bytes(), run_policy=b"null")
    contract = api.TaskContract.from_dict(parse_json(task.contract.data))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(
        case_limit=policy.baseline_cases, max_queue_depth=policy.sequence_length, seed=policy.seed))
    assert baseline.stimuli
    assert all(txn != (137,) for stimulus in baseline.stimuli
               for _, queue in stimulus.queues for txn in queue)

    outcome = run(task, submission, journal, SimulatorJudge(cpu_capacity=2))
    result = getattr(outcome, component)
    assert result.score == 0, (result.code, result.origin)
    assert result.cleanup.quiescent
    assert result.detail["baseline"]["status"] == "completed"
    assert result.detail["baseline"]["search_generated"] > 0
    evidence = parse_json(journal.objects.read(result.evidence[0]))
    assert evidence["request"]["search_policy"] == api.SearchPolicy(
        time_budget_s=policy.search_seconds).as_dict()
    assert evidence["request"]["probe_budget"] is None
    assert evidence["result"]["analysis"] is None
    authority = evidence["result"]["decision"]["authority"]
    assert authority["authoritative"] and not authority["passed"]
    evaluation = evidence["evaluation"]
    assert evaluation["mismatch_count"] > 0
    assert evaluation["failures"] == []
    runs = evaluation["results"]
    baseline_ids = {stimulus.fingerprint for stimulus in baseline.stimuli}
    checked_baseline = [run for run in runs if run["stimulus"] in baseline_ids]
    assert {run["stimulus"] for run in checked_baseline} == baseline_ids
    assert all(run["comparison"]["complete"] and run["comparison"]["ok"]
               for run in checked_baseline)
    assert any(run["comparison"] is not None and not run["comparison"]["ok"]
               and run["stimulus"] not in baseline_ids
               for run in runs)
    record_property("search_generated", result.detail["baseline"]["search_generated"])
    record_property("mismatch_count", evaluation["mismatch_count"])


@pytest.mark.parametrize("origin", list(api.FailureOrigin))
@pytest.mark.parametrize("kind", ["admission-handshake-missing", "new-unknown-refusal"])
def test_only_typed_disposition_selects_score_and_no_result_does_not_imply_retry(origin, kind):
    reason = api.Refusal(kind, "candidate failed: prose must not select score zero")
    options = {"policy": "test-limit"} if origin is api.FailureOrigin.RESOURCE_POLICY else {}
    failure = api.RunFailure(origin, "candidate-interface", reason, **options)
    result = _failure(api, failure)
    assert result.score == (0 if origin is api.FailureOrigin.CANDIDATE else None)
    assert result.origin == origin.value and result.code == kind
    assert result.detail["failure"] == failure.as_dict()
    assert not result.retryable


def test_published_resource_zero_is_preserved_without_a_candidate_origin_guess():
    failure = api.RunFailure.resource("candidate-run", api.Refusal("limit", "limit"),
        policy="published-budget", disposition=api.FailureDisposition.SCORE_ZERO)
    result = _failure(api, failure)
    assert result.score == 0 and result.origin == "resource-policy" and not result.retryable


@pytest.mark.parametrize("origin", list(api.FailureOrigin))
def test_only_infrastructure_namespace_launch_failure_is_retryable(origin):
    options = {"policy": "test-limit"} if origin is api.FailureOrigin.RESOURCE_POLICY else {}
    failure = api.RunFailure(origin, "reference-preparation",
                             api.Refusal("isolation-launch-failed", "startup deadline"), **options)
    result = _failure(api, failure)
    assert result.retryable is (origin is api.FailureOrigin.INFRASTRUCTURE)
    assert result.detail["failure"] == failure.as_dict()


def test_namespace_launch_retry_uses_existing_component_budget(tmp_path):
    from test_judge import ScriptedJudge, run, setup

    task, submission, journal = setup(tmp_path)
    failure = api.RunFailure(api.FailureOrigin.INFRASTRUCTURE, "reference-preparation",
                             api.Refusal("isolation-launch-failed", "startup deadline"))
    unavailable = _failure(api, failure)
    from behave.task_execution.judgment import ComponentJudgment

    backend = ScriptedJudge(unavailable, unavailable, ComponentJudgment(1, "matched"))
    result = run(task, submission, journal, backend)
    assert [(request.component, request.dispatch) for request in backend.requests] == [
        ("behavior", 0), ("behavior", 1), ("rtl", 0)]
    assert result.behavior.score is None and result.rtl.score == 1
    # Journal replay must not launch a third try or repeat the valid RTL score.
    assert run(task, submission, journal, backend) == result
    assert len(backend.requests) == 3


@pytest.mark.parametrize("phase", ["prepare_behavior_bundle", "realization_of_behavior_source", "admit"])
@pytest.mark.parametrize("origin", [api.FailureOrigin.CANDIDATE, api.FailureOrigin.VERIFIER,
                                   api.FailureOrigin.INFRASTRUCTURE])
def test_public_preparation_failure_keeps_sim_attribution(tmp_path, monkeypatch, phase, origin):
    inputs, journal = comparison(tmp_path, real=True)
    failure = api.RunFailure(origin, "source-preparation", api.Refusal("new-failure", "diagnostic"))

    def fail(*args, **kwargs):
        raise api.FailureRaised(failure)

    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "realization_of_behavior_source", lambda *a, **kw: object())
    monkeypatch.setattr(api, "admit", lambda *a, **kw: object())
    monkeypatch.setattr(api, phase, fail)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score == (0 if origin is api.FailureOrigin.CANDIDATE else None)
    assert result.origin == origin.value and not result.retryable
    assert result.detail["failure"] == failure.as_dict()


@pytest.mark.parametrize("body", [
    b"class Helper: pass\nclass M: pass\n",
    b"class Implementation: pass\nM = Implementation\n",
    b"from helper import Model as M\n",
])
def test_entry_selection_is_sent_to_sim_without_an_eval_ast_gate(tmp_path, monkeypatch, body):
    inputs, journal = comparison(tmp_path, real=True, behavior=body)
    received = []
    reason = api.Refusal("source-capability", "only Sim decides which bundle forms it supports")

    def prepare(bundle):
        received.append(bundle)
        raise api.Refused(reason)

    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "prepare_behavior_bundle", prepare)
    monkeypatch.setattr(api, "realization_of_behavior_source",
                        lambda *a, **kw: pytest.fail("must not fall back to implicit model selection"))
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert len(received) == 1
    bundle = received[0]
    assert bundle.model == inputs.source.artifact.entry_symbol == "M"
    assert bundle.entry == inputs.source.artifact.entry_file
    assert dict(bundle.files) == {file.reference.path: file.data for file in inputs.source.files}
    assert result.score is None and result.detail["reason"] == reason.as_dict()


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("native_layout", [False, True])
def test_invalid_explicit_entry_preserves_source_owner(tmp_path, monkeypatch, reference, native_layout):
    inputs, journal = comparison(tmp_path, real=True)
    selected = inputs.reference_behavior if reference else inputs.source
    files = {file.reference.path: file.data for file in selected.files}
    entry = selected.artifact.entry_file
    if native_layout:
        files["behavior.py"] = files.pop(entry)
        entry = "behavior.py"
    else:
        files["entry.py"] = files.pop(entry)
        entry = "entry.py"
    loaded = tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in sorted(files.items()))
    source = LoadedSource(SourceArtifact(tuple(file.reference for file in loaded), entry, "*"), loaded)
    inputs = replace(inputs, **{"reference_behavior" if reference else "source": source})
    if not reference:
        monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score == (None if reference else 0), result.as_dict()
    assert result.origin == ("reference" if reference else "candidate")
    assert result.diagnostics


@pytest.fixture
def command_service(tmp_path, monkeypatch):
    inputs, journal = comparison(tmp_path, real=True)
    work = tmp_path / "service"
    work.mkdir()
    calls = []

    class Evaluation:
        fingerprint = BlobRef.of(b"fixture evaluation").sha256
        budget_exhausted = False
        requested_stimuli = compared = unexecuted = ()
        required_stimuli = ()
        required_complete = True
        score_scope = "requested-pool"
        accepted = 1
        checked_inputs = 1

        @property
        def no_result_failures(self):
            pytest.fail("failure attribution must come from the public decision")

        @property
        def passed(self):
            pytest.fail("the caller must consume Sim's decision, not recompute it")

        def as_evidence_dict(self):
            return {"fixture": "retained evidence"}

    command = SimpleNamespace(as_dict=lambda: {"fixture": "command"})
    decision = SimpleNamespace(authoritative=True, passed=True, failures=(),
        evaluation_fingerprint=Evaluation.fingerprint, authority={"version": api.RESULT_VERSION})
    result = SimpleNamespace(record_path=str(work / "must-not-read.json"), invocation="fixture",
        evaluation=Evaluation(), decision=decision, status=api.CommandStatus.EVALUATED,
        generated=None, as_dict=lambda: {"fixture": "result"})
    handle = SimpleNamespace(generation_providers=lambda *a, **kw: (),
                             command_request=lambda **kw: command)

    def execute(got):
        assert got is command
        calls.append(got)
        return result

    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "realization_of_behavior_source", lambda *a, **kw: object())
    monkeypatch.setattr(api, "admit", lambda *a, **kw: object())
    monkeypatch.setattr(api, "prepare_behavior", lambda *a, **kw: handle)
    monkeypatch.setattr(api, "handle_command", execute)
    # This fixture models the committed decision, not a ConcreteEvaluation.
    # Real diagnostic projections are covered by test_score_diagnostics.
    monkeypatch.setattr(api, "command_diagnostics", lambda *args, **kwargs: {})
    return inputs, journal, work, result, calls


@pytest.mark.parametrize("reference", [False, True])
def test_linker_dependencies_remain_readable_in_exported_score_evidence(command_service, tmp_path, reference):
    inputs, journal, work, _result, calls = command_service
    model, input_name, output_name = ("Identity", "in_a", "out_y") if reference else ("M", "a", "y")
    entry = (f"from helper import f\nclass {model}:\n def reset(self): pass\n"
             f" def process(self, {input_name}): return {{'{output_name}': f({input_name})}}\n").encode()
    files = {"entry.py": entry, "helper.py": b"from fp_lib import fp32_abs\ndef f(a): return fp32_abs(a)\n"}
    loaded = tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in sorted(files.items()))
    source = LoadedSource(SourceArtifact(tuple(file.reference for file in loaded), "entry.py", model), loaded)
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "entry.py", model))
    assert prepared.dependencies and "fp_lib.py" not in files
    inputs = replace(inputs, **{"reference_behavior" if reference else "source": source})

    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score == 1 and len(calls) == 1
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(journal.objects, result.closure)
    report_ref = BlobRef.of(canonical_json(prepared.as_dict()))
    report = parse_json(read_verified(exported, report_ref))
    for name, ref in report["dependencies"].items():
        assert read_verified(exported, BlobRef.from_dict(ref)) == prepared.dependencies[name]


@pytest.mark.parametrize("passed,authoritative,score", [(True, True, 1), (False, True, 0),
                                                      (None, False, None)])
def test_score_consumes_sim_decision_without_recomputing_evaluation_passed(
        command_service, passed, authoritative, score):
    inputs, journal, work, result, calls = command_service
    result.decision.passed = passed
    result.decision.authoritative = authoritative
    judgment = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert judgment.score == score and len(calls) == 1
    assert judgment.evidence and not judgment.retryable
    assert "persisted" not in parse_json(journal.objects.read(judgment.evidence[0]))


def test_score_progress_reads_compared_indices_not_run_objects(command_service):
    inputs, journal, work, result, _calls = command_service
    result.evaluation.results = (
        None,
        SimpleNamespace(trace=SimpleNamespace(completion=api.Completion.NORMAL)),
        SimpleNamespace(trace=SimpleNamespace(completion=api.Completion.CHECKPOINT)),
        None,
    )
    result.evaluation.compared = (1, 2)
    result.evaluation.requested_stimuli = (None,) * 5
    result.evaluation.unexecuted = (None,)
    result.evaluation.budget_exhausted = True
    result.evaluation.required_stimuli = (None,)
    result.evaluation.score_scope = "required-baseline"
    judgment = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert judgment.score == 1
    assert judgment.detail["scoring_scope"] == {
        "budget_exhausted": True, "requested_runs": 5,
        "scope": "required-baseline", "required_runs": 1, "required_complete": True,
        "completed_runs": 1, "checked_transaction_prefixes": 1,
        "unexecuted_runs": 1, "accepted": 1, "checked_inputs": 1,
    }


def test_no_result_keeps_accepted_and_checked_input_counts_distinct(command_service):
    inputs, journal, work, result, _calls = command_service
    result.evaluation.accepted = 9
    result.evaluation.checked_inputs = 2
    result.decision.authoritative = False
    result.decision.passed = None
    judgment = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert judgment.score is None
    assert judgment.detail["scoring_scope"]["accepted"] == 9
    assert judgment.detail["scoring_scope"]["checked_inputs"] == 2


@pytest.mark.parametrize("status,retryable", [(api.CommandStatus.REFUSED, False),
    (api.CommandStatus.INFRASTRUCTURE_FAILURE, True), (api.CommandStatus.EVALUATED, False)])
def test_missing_decision_is_no_result_not_a_reconstructed_score(command_service, status, retryable):
    inputs, journal, work, result, calls = command_service
    result.decision = None
    result.status = status
    judgment = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert judgment.score is None and judgment.retryable is retryable
    assert judgment.code == "judge-command-no-result" and len(calls) == 1


@pytest.mark.parametrize("origin", [api.FailureOrigin.REFERENCE, api.FailureOrigin.VERIFIER,
                                  api.FailureOrigin.INFRASTRUCTURE, api.FailureOrigin.RESOURCE_POLICY])
def test_committed_no_result_preserves_failure_attribution(command_service, origin):
    inputs, journal, work, result, _calls = command_service
    options = {"policy": "published-limit"} if origin is api.FailureOrigin.RESOURCE_POLICY else {}
    failure = api.RunFailure(origin, "replay", api.Refusal("fixture-failure", "diagnostic"), **options)
    result.decision.authoritative = False
    result.decision.passed = None
    # An earlier candidate failure cannot override the committed no-result.
    candidate = api.RunFailure.candidate("replay", api.Refusal("candidate-fault", "diagnostic"))
    result.decision.failures = ((0, candidate), (1, failure))
    completed = []
    judgment = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects,
                                            on_decided=completed.append)
    assert judgment.score is None and judgment.origin == origin.value and not judgment.retryable
    assert judgment.detail["failure"] == failure.as_dict()
    assert completed == [judgment]
    assert judgment.detail["evaluation"] == result.evaluation.fingerprint


@pytest.mark.parametrize("error", [RuntimeError("analysis diagnostic"), None])
def test_analysis_failure_keeps_score_checkpoint_and_exception_log(command_service, monkeypatch, caplog, error):
    from behave.task_execution.analysis import AnalysisOptions

    inputs, journal, work, result, calls = command_service
    inputs = replace(inputs, judge_policy=replace(inputs.policy, analysis=AnalysisOptions(wall_timeout_seconds=60)).to_bytes())
    decided = []

    def analyze(got, **kwargs):
        assert got is result and len(decided) == 1
        if error is not None:
            raise error
        return SimpleNamespace(concrete=BlobRef.of(b"wrong evaluation").sha256)

    monkeypatch.setattr(api, "analyse_command", analyze)
    judgment = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects,
                                           on_decided=decided.append)
    assert judgment.score == 1 and replace(judgment, analysis=None) == decided[0]
    assert judgment.analysis.code == "analysis-service-failed" and judgment.analysis.outcome is None
    assert len(calls) == 1
    logged = [record for record in caplog.records if record.name == "behave.task_execution.simulator_judge"]
    assert len(logged) == 1 and logged[0].exc_info
    assert logged[0].exc_info[0] is (RuntimeError if error is not None else ValueError)
    assert str(logged[0].exc_info[1]) == (str(error) if error is not None else "analysis belongs to another evaluation")


@pytest.mark.isolation
@pytest.mark.parametrize("expression,score", [("a", 1), ("a ^ 1", 0)])
def test_explicit_behavior_entry_is_used_with_multiple_runnable_classes(tmp_path, expression, score):
    source = ("class Alternate:\n def reset(self): pass\n def process(self, a): return {'y': 255}\n"
              "class M:\n def reset(self): pass\n"
              f" def process(self, a): return {{'y': {expression}}}\n").encode()
    inputs, journal = comparison(tmp_path, real=True, behavior=source)
    report = check_sources(inputs, journal=journal)
    assert report.result.score == score, report.as_dict()
    assert report.result.cleanup.quiescent
    native = parse_json(journal.objects.read(report.result.evidence[0]))
    assert native["result"]["decision"]["authority"]["passed"] is bool(score)
    assert check_sources(inputs, journal=journal) == report


@pytest.mark.parametrize("phase", ["realization_of_behavior_source", "admit", "prepare_behavior"])
@pytest.mark.parametrize("kind", ["admission-handshake-missing",
    "behavior-boundary-input-binding-incomplete", "new-unknown-refusal"])
def test_plain_refusal_is_never_promoted_to_candidate_zero(tmp_path, monkeypatch, phase, kind):
    inputs, journal = comparison(tmp_path, real=True)
    reason = api.Refusal(kind, "not an attributed candidate fault")

    def fail(*args, **kwargs):
        raise api.Refused(reason)

    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "realization_of_behavior_source", lambda *a, **kw: object())
    monkeypatch.setattr(api, "admit", lambda *a, **kw: object())
    monkeypatch.setattr(api, phase, fail)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score is None and result.origin == "verifier" and not result.retryable
    assert result.detail["reason"] == reason.as_dict()


@pytest.mark.isolation
@pytest.mark.parametrize("broken,kind", [
    (RTL.replace(b"input out_ready,", b"").replace(b"= out_ready", b"= 1'b1"),
     "admission-handshake-missing"),
    (RTL.replace(b"input [7:0] in_a", b"input [6:0] in_a"), "admission-narrower-field"),
    (RTL.replace(b"input out_ready", b"input [1:0] out_ready"), "admission-handshake-width"),
])
def test_real_candidate_interface_fault_is_zero_with_no_semantic_retry(tmp_path, broken, kind):
    inputs, journal = comparison(tmp_path, component="rtl", real=True, rtl=broken)
    report = check_sources(inputs, journal=journal)
    assert report.outcome == "failed"
    assert report.result.score == 0 and report.result.origin == "candidate"
    assert report.result.code == kind and report.result.retryable is False
    assert report.result.cleanup.quiescent
    assert check_sources(inputs, journal=journal) == report


@pytest.mark.parametrize("phase,origin", [("load_reference", "reference"), ("prepare_behavior", "verifier")])
def test_signature_reason_outside_candidate_realization_never_becomes_zero(tmp_path, monkeypatch, phase, origin):
    inputs, journal = comparison(tmp_path, real=True)
    reason = api.Refusal("behavior-boundary-input-binding-incomplete", "same code in a different phase")

    def fail(*args, **kwargs):
        raise api.Refused(reason)

    monkeypatch.setattr(api, "load_reference", lambda *a, **kw: object())
    monkeypatch.setattr(api, "realization_of_behavior_source", lambda *a, **kw: object())
    monkeypatch.setattr(api, "admit", lambda *a, **kw: object())
    monkeypatch.setattr(api, phase, fail)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score is None and result.origin == origin
    assert not result.retryable
    assert result.detail["reason"]["kind"] == reason.kind


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("origin", [api.FailureOrigin.CANDIDATE, api.FailureOrigin.REFERENCE,
                                   api.FailureOrigin.VERIFIER, api.FailureOrigin.INFRASTRUCTURE])
def test_reference_preparation_preserves_typed_no_result_and_never_scores_candidate(tmp_path, monkeypatch, origin, visible):
    inputs, journal = comparison(tmp_path, real=True)
    inputs = replace(inputs, reference_visible=visible)
    failure = api.RunFailure(origin, "reference-preparation",
                             api.Refusal("source-failed", "REFERENCE-DIAGNOSTIC", file="model.py", line=12))

    def fail(*args, **kwargs):
        raise api.FailureRaised(failure)

    monkeypatch.setattr(api, "load_reference", fail)
    work = tmp_path / "service"
    work.mkdir()
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert result.score is None and not result.retryable
    assert result.origin == ("reference" if origin is api.FailureOrigin.CANDIDATE else origin.value)
    assert result.detail["failure"] == failure.as_dict()
    diagnostic, = result.diagnostics
    image = parse_json(journal.objects.read(diagnostic.content))
    assert image["origin"] == result.origin and image["disposition"] == "no-result"
    assert ("reason" in image) == visible
    reply, files = comparison_feedback(result, journal.objects)
    assert ("REFERENCE-DIAGNOSTIC" in reply["summary"]) == visible
    assert ("model.py:12" in files[-1].data.decode()) == visible


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("reason,expected", [
    (api.Refusal("reference-source-facts-unavailable", "could not inspect this reference",
                 worker_detail="LoweringError: process takes *args at line 33"), "process takes *args at line 33"),
    (api.Refusal("behavior-bundle-dynamic-namespace", "outside the static-link subset",
                 file="behavior.py", line=32), "behavior.py:32"),
    (api.Refusal("reference-model-calls-what-this-build-does-not-provide", "unsupported calls",
                 calls=["line 45: map() is not provided", "line 46: bytes() is not provided"]),
     "line 45: map() is not provided"),
])
def test_reference_refusals_export_actionable_errors_only_for_visible_sources(tmp_path, monkeypatch, visible,
                                                                           reason, expected):
    inputs, journal = comparison(tmp_path, real=True)
    inputs = replace(inputs, reference_visible=visible)

    def refuse(*args, **kwargs):
        raise api.Refused(reason)

    monkeypatch.setattr(api, "load_reference", refuse)
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, tmp_path), journal.objects)
    assert result.score is None and result.origin == "reference" and not result.retryable
    assert result.code == "judge-reference-unavailable"
    assert result.as_dict()["detail"]["reason"] == reason.as_dict()
    diagnostic, = result.diagnostics
    assert diagnostic.path == "failure.json" and diagnostic.content in result.closure
    image = parse_json(journal.objects.read(diagnostic.content))
    assert ("reason" in image) == visible
    if visible:
        assert image["reason"] == reason.as_dict()
    reply, files = comparison_feedback(result, journal.objects)
    assert reply["matched"] is None
    assert (expected in reply["summary"]) == visible
    assert (expected in files[-1].data.decode()) == visible
    assert "No detailed diagnostics were recorded" not in files[-1].data.decode()
    if visible and "calls" in reason.as_dict().get("detail", {}):
        assert "line 46: bytes()" not in reply["summary"]
        assert "line 46: bytes()" in files[-1].data.decode()
    hidden_reply, hidden_files = comparison_feedback(result, journal.objects, diagnostics_visible=False)
    assert hidden_reply["summary"] == "UNAVAILABLE."
    assert len(hidden_files) == 1 and expected not in hidden_files[0].data.decode()


@pytest.mark.isolation
@pytest.mark.parametrize("signature,output,kind", [
    ("a, extra", "{'y': a}", "behavior-boundary-input-binding-incomplete"),
    ("", "{'y': 0}", "behavior-boundary-input-binding-incomplete"),
    ("wrong", "{'y': wrong}", "behavior-boundary-input-binding-incomplete"),
    ("a, in_a", "{'y': a}", "behavior-boundary-input-binding-incomplete"),
    ("a", "{'y': a, 'extra': 0}", "behavior-concrete-output-binding-mismatch"),
])
def test_real_behavior_signature_fault_is_zero_and_cold_replayable(tmp_path, signature, output, kind):
    body = f"class M:\n def reset(self): pass\n def process(self, {signature}): return {output}\n".encode()
    inputs, journal = comparison(tmp_path, real=True, behavior=body)
    report = check_sources(inputs, journal=journal)
    assert report.outcome == "failed" and report.result.score == 0, report.as_dict()
    if kind == "behavior-concrete-output-binding-mismatch":
        _assert_output_failure(report, journal)
    else:
        assert report.result.code == kind and report.result.origin == "candidate"
    assert report.result.retryable is False and report.result.cleanup.quiescent
    assert check_sources(inputs, journal=journal) == report


@pytest.mark.isolation
def test_optional_behavior_input_preserves_default_and_scores(tmp_path):
    body = b"class M:\n def reset(self): pass\n def process(self, a, extra=None): return {'y': a}\n"
    inputs, journal = comparison(tmp_path, real=True, behavior=body)
    report = check_sources(inputs, journal=journal)
    assert report.outcome == "passed" and report.result.score == 1, report.as_dict()
    assert not report.result.retryable and report.result.cleanup.quiescent
    assert check_sources(inputs, journal=journal) == report


def _assert_output_failure(report, journal):
    # Concrete failures are retained per stimulus inside the authoritative
    # evaluation, not flattened into the component's aggregate status code.
    assert report.result.code == "simulator-concrete-score"
    evidence = parse_json(journal.objects.read(report.result.evidence[0]))
    failures = [api.RunFailure.from_dict(row) for _index, row in evidence["evaluation"]["failures"]]
    assert failures
    assert all(failure.origin is api.FailureOrigin.CANDIDATE
               and failure.disposition is api.FailureDisposition.SCORE_ZERO
               and failure.phase == "candidate-output"
               and failure.kind == "behavior-concrete-output-binding-mismatch"
               for failure in failures)


@pytest.mark.isolation
@pytest.mark.parametrize("key", ["y", "out_y"])
@pytest.mark.parametrize("body", [
    "class M:\n def reset(self): self.pending = None\n def process(self, a):\n  self.pending = {KEY: a}\n  return self.pending\n",
    "class M:\n def reset(self): pass\n def process(self, a):\n  result = {KEY: a}\n  return result\n",
    "class M:\n def reset(self): pass\n def result(self, a): return {KEY: a}\n def process(self, a): return self.result(a)\n",
    "class M:\n def reset(self): pass\n def process(self, a):\n  def helper(): return {'unrelated': 0}\n  row = dict([(KEY, a)])\n  return [row]\n",
    "class M:\n def reset(self): pass\n def process(self, a):\n  row = dict([(KEY, a)])\n  return (row,)\n",
])
def test_real_computed_output_mapping_scores_without_static_return_inference(tmp_path, body, key):
    body = body.replace("KEY", repr(key)).encode()
    inputs, journal = comparison(tmp_path, real=True, behavior=body)
    report = check_sources(inputs, journal=journal)
    assert report.outcome == "passed" and report.result.score == 1, report.as_dict()
    assert report.result.cleanup.quiescent
    assert check_sources(inputs, journal=journal) == report


@pytest.mark.isolation
@pytest.mark.parametrize("row", ["{}", "{'y': a, 'extra': 0}", "{'y': a, 'out_y': a}", "{'unknown': a}"])
def test_real_dynamic_output_faults_are_checked_at_concrete_return(tmp_path, row):
    body = f"class M:\n def reset(self): pass\n def process(self, a):\n  row = {row}\n  return row\n".encode()
    inputs, journal = comparison(tmp_path, real=True, behavior=body)
    report = check_sources(inputs, journal=journal)
    assert report.outcome == "failed" and report.result.score == 0, report.as_dict()
    _assert_output_failure(report, journal)
    assert report.result.cleanup.quiescent
    assert check_sources(inputs, journal=journal) == report


@pytest.mark.isolation
def test_concrete_admission_does_not_invent_an_exact_output_signature(tmp_path):
    from behave.sim.behavior.task_boundary import task_input_projections, task_procedure_boundary

    source = "class M:\n def reset(self): self.pending = None\n def process(self, a):\n  self.pending = {'y': a}\n  return self.pending\n"
    inputs, _journal = comparison(tmp_path, real=True)
    contract = api.TaskContract.from_dict(parse_json(inputs.contract.data))
    assert task_input_projections(contract, ("a",))[0].fields == (("a", "a"),)
    assert isinstance(api.realization_of_behavior_source(source, contract, file="behavior.py"), api.Realization)
    # A successful concrete admission is not a claim that finite analysis has
    # derived an output layout. Keep that gate honest and independent.
    with pytest.raises(api.Refused) as caught:
        task_procedure_boundary(source, "behavior.py", contract)
    assert caught.value.refusal.kind == "behavior-boundary-field-binding-unresolved"


@pytest.mark.parametrize("names,expected", [
    (("a",), "a"), (("in_a",), "in_a"),
    ((), None), (("wrong",), None), (("a", "in_a"), None),
])
def test_input_projection_requires_one_declared_spelling(tmp_path, names, expected):
    from behave.sim.behavior.task_boundary import task_input_projections

    inputs, _journal = comparison(tmp_path, real=True)
    contract = api.TaskContract.from_dict(parse_json(inputs.contract.data))
    if expected is not None:
        assert task_input_projections(contract, names)[0].fields == (("a", expected),)
    else:
        with pytest.raises(api.Refused) as caught:
            task_input_projections(contract, names)
        assert caught.value.refusal.kind == "behavior-boundary-input-binding-incomplete"
        assert caught.value.refusal.detail["field"] == "a"


def test_input_projection_keeps_task_duplicates_distinct_and_domains_separate():
    from behave.sim.behavior.task_boundary import task_input_projections

    contract = api.TaskContract([
        api.Endpoint(domain, api.Direction.IN, [api.Field("a", api.FiniteType(8), domain + "_a")],
            api.ready_valid(api.Direction.IN, domain + "_valid", domain + "_ready"),
            clock=domain, kind=api.Kind.TRANSACTION)
        for domain in ("left", "right")],
        [api.ClockDomain(domain, domain + "_clk", domain + "_rst") for domain in ("left", "right")],
        source="distinct domain input namespaces")
    for domain in ("left", "right"):
        assert task_input_projections(contract, ("a",), clock=domain)[0].endpoint == domain
    with pytest.raises(api.Refused) as caught:
        task_input_projections(contract, ("a",))
    assert caught.value.refusal.kind == "behavior-boundary-field-binding-duplicate"
