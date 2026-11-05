from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef, FileData, FileRef, LoadedSource, SourceArtifact
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import JudgeInputs
from behave.task_execution.capture import CapturedComponent, FrozenSubmission
from behave.task_execution.judge import judge_submission
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment

BEHAVIOR = b"class M:\n    def reset(self): pass\n    def process(self, a): return {'y': a}\n"
REFERENCE = b"class Identity:\n    def reset(self): pass\n    def process(self, in_a): return {'out_y': in_a}\n"
RTL = b"""module M(input clk, rst, in_valid, input [7:0] in_a,
output in_ready, out_valid, input out_ready, output [7:0] out_y);
assign in_ready = out_ready && !rst;
assign out_valid = in_valid && !rst;
assign out_y = in_a;
endmodule
"""


def setup(tmp_path, *, behavior=BEHAVIOR, rtl=RTL, testbench=None, policy=None, setting="joint"):
    objects = DirectoryStore(tmp_path / "objects")
    workspace = Workspace(objects)
    components = []
    from behave.task_execution.submission import ArtifactSelection

    for path, body in (("model.py", behavior), ("top.sv", rtl), ("tb.py", testbench)):
        if body is None:
            components.append(CapturedComponent(None, (), ()))
        else:
            workspace.write(path, body)
            file = next(file for file in workspace.snapshot().files if file.path == path)
            components.append(CapturedComponent(ArtifactSelection((path,), path, "M"), (file,), ()))
    workspace.write("not-submitted.txt", b"must survive journal closure publication")
    root, _ = workspace.checkpoint()
    release, revision = BlobRef.of(b"release"), BlobRef.of(b"revision")
    frozen = FrozenSubmission("identity", release, revision, root, *components, setting=setting)
    submission = objects.put(frozen.to_bytes())
    file = FileData(FileRef("behavior.py", BlobRef.of(REFERENCE)), REFERENCE)
    source = LoadedSource(SourceArtifact((file.reference,), "behavior.py", "Identity"), (file,))
    policy = JudgePolicy("fixture-command", "fixture-authority", search_seconds=0, max_retries=1) if policy is None else policy
    contract = FileData(FileRef("contract.json", BlobRef.of(b"{}")), b"{}")
    task = JudgeInputs("identity", release, revision, contract, source, None, b"{}", policy.to_bytes())
    return task, submission, AttemptJournal(tmp_path / "judging", objects)


class ScriptedJudge:
    identity = "test-scripted-judge/1"

    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def evaluate(self, request, objects):
        self.requests.append(request)
        assert request.workdir.parent == Path("/tmp")
        assert request.workdir.is_dir()
        if not self.results:
            pytest.fail("unexpected judge dispatch")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def run(task, submission, journal, backend):
    return judge_submission(task=task, submission=submission, journal=journal, backend=backend)


@pytest.mark.parametrize("score", [0, 1, None])
def test_rtl_only_scores_rtl_and_replays_without_behavior(tmp_path, score):
    task, submission, journal = setup(tmp_path, behavior=None, setting="rtl_only")
    outcome = (ComponentJudgment(None, "not-ready", "verifier") if score is None
               else ComponentJudgment(score, "matched"))
    backend = ScriptedJudge(outcome)
    result = run(task, submission, journal, backend)
    assert [request.component for request in backend.requests] == ["rtl"]
    assert result.behavior is None and result.score == score
    assert result.rtl == outcome and result.cleanup_state == outcome.cleanup
    assert SubmissionJudgment.from_dict(result.as_dict()) == result
    assert run(task, submission, journal, ScriptedJudge()) == result
    # The hidden reference remains available to the RTL evaluator.
    assert backend.requests[0].task.reference_behavior == task.reference_behavior


def test_rtl_only_missing_rtl_is_zero_without_sim(tmp_path):
    task, submission, journal = setup(tmp_path, behavior=None, rtl=None, setting="rtl_only")
    backend = ScriptedJudge()
    result = run(task, submission, journal, backend)
    assert result.behavior is None and result.rtl.score == 0 and result.score == 0
    assert backend.requests == []


def test_historical_joint_rtl_submission_does_not_become_rtl_only(tmp_path):
    task, submission, journal = setup(tmp_path, behavior=None)
    data = journal.objects.read(submission)
    assert "setting" not in parse_json(data)
    frozen = FrozenSubmission.from_bytes(data)
    assert frozen.setting == "joint" and frozen.to_bytes() == data
    result = run(task, submission, journal, ScriptedJudge(ComponentJudgment(1, "matched")))
    assert result.behavior.score == 0 and result.rtl.score == 1 and result.score == .5


@pytest.mark.parametrize("left,right,score", [(0, 0, 0), (0, 1, .5), (1, 0, .5), (1, 1, 1)])
def test_fixed_mean_and_durable_recovery(tmp_path, left, right, score):
    task, submission, journal = setup(tmp_path)
    judge = ScriptedJudge(ComponentJudgment(left, "native"), ComponentJudgment(right, "native"))
    result = run(task, submission, journal, judge)
    assert result.score == score
    assert [request.component for request in judge.requests] == ["behavior", "rtl"]
    assert all(not request.workdir.exists() for request in judge.requests)
    assert run(task, submission, journal, ScriptedJudge()).as_dict() == result.as_dict()


def test_no_result_retries_identical_submission_not_completed_component(tmp_path):
    task, submission, journal = setup(tmp_path)
    failure = ComponentJudgment(None, "worker", "infrastructure", True)
    backend = ScriptedJudge(ComponentJudgment(1, "native"), failure, ComponentJudgment(0, "native"))
    result = run(task, submission, journal, backend)
    assert result.score == .5
    assert [(r.component, r.dispatch) for r in backend.requests] == [("behavior", 0), ("rtl", 0), ("rtl", 1)]
    first, retry = backend.requests[1:]
    assert first.source == retry.source and first.policy == retry.policy and first.submission == retry.submission
    assert first.workdir != retry.workdir
    assert len([event for event in journal.load() if event.kind == "judge_settled"]) == 3


@pytest.mark.parametrize("origin", ["reference", "verifier", "infrastructure", "resource-policy"])
def test_exhausted_no_result_does_not_renormalize_or_score_zero(tmp_path, origin):
    task, submission, journal = setup(tmp_path)
    failed = ComponentJudgment(None, "failed", origin, True)
    result = run(task, submission, journal, ScriptedJudge(failed, failed, ComponentJudgment(1, "native")))
    assert result.behavior.score is None and result.rtl.score == 1 and result.score is None


def test_missing_artifact_is_zero_without_judge_call(tmp_path):
    task, submission, journal = setup(tmp_path, behavior=None)
    backend = ScriptedJudge(ComponentJudgment(1, "native"))
    result = run(task, submission, journal, backend)
    assert result.score == .5 and result.behavior.code == "submission-component-missing"
    assert [r.component for r in backend.requests] == ["rtl"]


@pytest.mark.parametrize("tb", [None, b"garbage", b"raise AssertionError('must not execute')"])
def test_testbench_never_gates_or_enters_score_judging(tmp_path, tb):
    task, submission, journal = setup(tmp_path, testbench=tb)
    backend = ScriptedJudge(ComponentJudgment(1, "native"), ComponentJudgment(1, "native"))
    assert run(task, submission, journal, backend).score == 1
    assert len(backend.requests) == 2


def test_dangling_dispatch_requires_external_fencing_not_automatic_retry(tmp_path):
    task, submission, journal = setup(tmp_path)
    class WorkerDied(BaseException):
        pass
    with pytest.raises(WorkerDied):
        run(task, submission, journal, ScriptedJudge(WorkerDied()))
    assert journal.load()[-1].kind == "judge_requested"
    with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
        run(task, submission, journal, ScriptedJudge())


def test_wrapper_error_is_verifier_no_result_not_candidate_fault(tmp_path):
    task, submission, journal = setup(tmp_path)
    backend = ScriptedJudge(RuntimeError("secret diagnostic"), RuntimeError(), ComponentJudgment(1, "native"))
    result = run(task, submission, journal, backend)
    assert result.score is None and result.behavior.origin == "verifier"
    assert "secret diagnostic" not in str(result.as_dict())


def test_judge_recovery_rejects_changed_policy_or_private_reference(tmp_path):
    task, submission, journal = setup(tmp_path)
    run(task, submission, journal, ScriptedJudge(ComponentJudgment(1, "native"), ComponentJudgment(1, "native")))
    altered = replace(task, judge_policy=replace(JudgePolicy.from_bytes(task.judge_policy), seed="other").to_bytes())
    with pytest.raises(RecordError, match="replay differs"):
        run(altered, submission, journal, ScriptedJudge())
    file = FileData(FileRef("behavior.py", BlobRef.of(b"changed")), b"changed")
    altered = replace(task, reference_behavior=LoadedSource(
        SourceArtifact((file.reference,), "behavior.py", "Identity"), (file,)))
    with pytest.raises(RecordError, match="replay differs"):
        run(altered, submission, journal, ScriptedJudge())


def test_judge_checks_task_binding_before_dispatch(tmp_path):
    task, submission, journal = setup(tmp_path)
    with pytest.raises(RecordError, match="another frozen task"):
        run(replace(task, revision=BlobRef.of(b"other")), submission, journal, ScriptedJudge())


def test_journal_retains_entire_workspace_including_unselected_files(tmp_path):
    task, submission, journal = setup(tmp_path)
    run(task, submission, journal, ScriptedJudge(ComponentJudgment(1, "native"), ComponentJudgment(1, "native")))
    root = tmp_path / "copied"
    objects = DirectoryStore(root / "objects")
    destination = AttemptJournal(root / "journal", objects)
    prefix = ()
    for event in journal.load():
        objects.publish(journal.objects, event.artifacts)
        prefix = (*prefix, destination.append(prefix, event.kind, dict(event.payload), event.artifacts))
    assert run(task, submission, destination, ScriptedJudge()).score == 1


@pytest.mark.parametrize("kwargs", [{"score": True, "code": "bad"},
                                  {"score": 1, "code": "bad", "origin": "candidate"},
                                  {"score": 0, "code": "bad", "origin": "infrastructure"},
                                  {"score": 0, "code": "bad", "retryable": True}, {"score": None, "code": "bad"}])
def test_component_judgment_rejects_contradictions(kwargs):
    with pytest.raises((TypeError, ValueError)):
        ComponentJudgment(**kwargs)


def real_task(tmp_path, **kwargs):
    from behave.sim import api
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0,
                         max_retries=0, baseline_cases=2, sequence_length=1)
    task, submission, journal = setup(tmp_path, policy=policy, **kwargs)
    byte = api.FiniteType(8)
    contract = api.TaskContract([
        api.Endpoint("in", api.Direction.IN, [api.Field("a", byte, "in_a")],
                     api.ready_valid("in", "in_valid", "in_ready"), "clk", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", api.Direction.OUT, [api.Field("y", byte, "out_y")],
                     api.ready_valid("out", "out_valid", "out_ready"), "clk", kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1),
    ], [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=3)])
    data = canonical_json(contract.as_dict())
    return replace(task, contract=FileData(FileRef("contract.json", BlobRef.of(data)), data),
                   run_policy=canonical_json(api.GenerationSpec(5, 1, 3, 1).as_dict())), submission, journal


@pytest.mark.isolation
@pytest.mark.parametrize("expression,expected", [(b"a", 1), (b"a ^ 1", 0)])
@pytest.mark.eda("verilator", "yosys")
def test_real_public_behavior_and_rtl_judgments(tmp_path, expression, expected):
    from behave.task_execution.simulator_judge import SimulatorJudge
    task, submission, journal = real_task(tmp_path, behavior=BEHAVIOR.replace(b"'y': a", b"'y': " + expression))
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.score == expected, result.behavior.as_dict()
    assert result.rtl.score == 1, result.rtl.as_dict()
    assert result.score == (expected + 1) / 2
    for component in (result.behavior, result.rtl):
        evidence = parse_json(journal.objects.read(component.evidence[0]))
        assert evidence["result"]["analysis"] is None
        assert evidence["result"]["decision"]["authority"]["version"] == JudgePolicy.from_bytes(task.judge_policy).authority_version
        assert evidence["evaluation"]["accepted"] > 0


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
def test_real_frontend_failure_is_candidate_zero(tmp_path):
    from behave.task_execution.simulator_judge import SimulatorJudge
    task, submission, journal = real_task(tmp_path, rtl=b"module M( syntax broken", behavior=None)
    result = run(task, submission, journal, SimulatorJudge())
    assert result.rtl.score == 0 and result.rtl.origin == "candidate", result.rtl.as_dict()
    assert result.score == 0


@pytest.mark.isolation
def test_version_mismatch_never_produces_a_score(tmp_path):
    from behave.task_execution.simulator_judge import SimulatorJudge
    task, submission, journal = setup(tmp_path)
    result = run(task, submission, journal, SimulatorJudge())
    assert result.score is None and result.behavior.code == "judge-simulator-version-mismatch"


@pytest.mark.isolation
def test_supported_class_alias_reaches_concrete_evaluation(tmp_path):
    from behave.task_execution.simulator_judge import SimulatorJudge
    task, submission, journal = real_task(tmp_path, behavior=BEHAVIOR + b"Alias = M\n", rtl=None)
    frozen = FrozenSubmission.from_bytes(journal.objects.read(submission))
    selected = replace(frozen.behavior.selection, entry_symbol="Alias")
    frozen = replace(frozen, behavior=replace(frozen.behavior, selection=selected))
    result = run(task, journal.objects.put(frozen.to_bytes()), journal, SimulatorJudge())
    assert result.behavior.code == "simulator-concrete-score"
    assert result.behavior.score == 1


@pytest.mark.isolation
def test_bundle_dependencies_are_not_silently_discarded(tmp_path):
    from behave.task_execution.simulator_judge import SimulatorJudge
    task, submission, journal = real_task(tmp_path, rtl=None)
    frozen = FrozenSubmission.from_bytes(journal.objects.read(submission))
    workspace = Workspace(journal.objects)
    workspace.restore(frozen.workspace)
    workspace.write("helper.py", b"VALUE = 7")
    source_files = tuple(file for file in workspace.snapshot().files if file.path in ("model.py", "helper.py"))
    component = CapturedComponent(replace(frozen.behavior.selection, files=("model.py", "helper.py")), source_files, ())
    root, _ = workspace.checkpoint()
    frozen = replace(frozen, workspace=root, behavior=component)
    result = run(task, journal.objects.put(frozen.to_bytes()), journal, SimulatorJudge())
    assert result.behavior.score == 1, result.behavior.as_dict()
    receipt = parse_json(journal.objects.read(result.behavior.evidence[2]))
    assert receipt["schema"] == "behave.prepared-behavior-source/2"
    from behave.sim.behavior.source_bundle import BehaviorSourceBundle
    captured = {file.path: journal.objects.read(file.content) for file in source_files}
    assert receipt["bundle"] == BehaviorSourceBundle(captured, "model.py", "M").identity.as_dict()


@pytest.mark.parametrize("change", [lambda row: row.update(max_retries=True),
                                  lambda row: row.update(unknown=True),
                                  lambda row: row.update(max_retries=-1),
                                  lambda row: row.update(schema="unknown")])
def test_judge_policy_is_closed_and_retry_budget_is_finite(change):
    row = parse_json(JudgePolicy("command", "authority", search_seconds=0).to_bytes())
    change(row)
    with pytest.raises((ValueError, TypeError)):
        JudgePolicy.from_bytes(canonical_json(row))


@pytest.mark.parametrize("retries", [0, 1, 11])
def test_retry_budgets_have_one_explicit_limit(retries):
    from behave.task_execution.testbench import TBPolicy

    judge = JudgePolicy("command", "authority", search_seconds=0, max_retries=retries)
    tb = TBPolicy("backend", "collector", "metric", max_retries=retries)
    assert JudgePolicy.from_bytes(judge.to_bytes()).max_retries == retries
    assert TBPolicy.from_bytes(tb.to_bytes()).max_retries == retries
    for invalid in (-1, True, float("inf")):
        with pytest.raises(ValueError):
            replace(judge, max_retries=invalid)
        with pytest.raises(ValueError):
            replace(tb, max_retries=invalid)


def test_judge_stops_after_the_explicit_retry_count(tmp_path):
    policy = JudgePolicy("command", "authority", search_seconds=0, max_retries=11)
    task, submission, journal = setup(tmp_path, rtl=None, policy=policy)
    failure = ComponentJudgment(None, "temporary", "infrastructure", True)
    backend = ScriptedJudge(*([failure] * 12))
    result = run(task, submission, journal, backend)
    assert result.score is None
    assert [request.dispatch for request in backend.requests] == list(range(12))
    assert not backend.results


def test_eval_tb_and_supervisor_defaults_have_one_configuration_source():
    import inspect

    from behave.core import runtime_defaults as defaults
    from behave.isolation.service import run_service
    from behave.task_execution.analysis import AnalysisOptions
    from behave.task_execution.testbench import TBPolicy

    judge = JudgePolicy("command", "authority")
    tb = TBPolicy("backend", "collector", "metric")
    options = AnalysisOptions(wall_timeout_seconds=60)
    assert judge.worker_log_bytes == tb.log_bytes == defaults.LOG_RETAIN_BYTES
    parameters = inspect.signature(run_service).parameters
    assert parameters["output_bytes"].default == defaults.LOG_RETAIN_BYTES
    assert parameters["cleanup_seconds"].default == defaults.SERVICE_CLEANUP_SECONDS
    assert judge.score_timeout_seconds == defaults.EVAL_SCORE_SECONDS
    assert judge.search_seconds == defaults.SIM_SEARCH_SECONDS
    assert judge.baseline_cases == defaults.SIM_BASELINE_CASES
    assert judge.sequence_length == defaults.SIM_SEQUENCE_LENGTH
    assert judge.seed == defaults.SIM_STIMULUS_SEED
    assert tb.worker_timeout_seconds == (defaults.TB_COMPILE_SECONDS + defaults.TB_RUN_SECONDS
                                         + defaults.TB_WORKER_OVERHEAD_SECONDS)
    assert options.wall_timeout_seconds == 60
    assert options.query_timeout_ms is options.feasibility_timeout_ms is None


def test_new_policy_uses_public_sim_defaults_and_explicit_policy_stays_explicit(monkeypatch):
    from behave.sim import api

    saved = JudgePolicy("command", "authority", search_seconds=0, seed="saved", baseline_cases=2, sequence_length=60)
    monkeypatch.setattr(api, "DEFAULT_STIMULUS_SEED", "updated")
    monkeypatch.setattr(api, "DEFAULT_BASELINE_CASES", 15)
    monkeypatch.setattr(api, "DEFAULT_SEQUENCE_LENGTH", 100)
    monkeypatch.setattr(api, "DEFAULT_SEARCH_SECONDS", 17.5)
    new = JudgePolicy("command", "authority")
    assert (new.seed, new.baseline_cases, new.sequence_length) == ("updated", 15, 100)
    assert new.search_seconds == 17.5
    assert JudgePolicy.from_bytes(saved.to_bytes()) == saved


def test_default_search_is_inside_the_whole_score_budget_and_saved_values_stay_explicit():
    policy = JudgePolicy("command", "authority")
    assert policy.search_seconds == 600.0
    assert policy.score_timeout_seconds == 1200
    assert policy.analysis is None
    for seconds in (0, 60, 600, 900):
        saved = JudgePolicy("command", "authority", search_seconds=seconds,
                            score_timeout_seconds=900).to_bytes()
        restored = JudgePolicy.from_bytes(saved)
        assert restored.search_seconds == seconds
        assert restored.score_timeout_seconds == 900


def test_search_time_has_one_finite_nonnegative_limit():
    for budget in (0, 1, 0.25, 37.5):
        policy = JudgePolicy("command", "authority", search_seconds=budget)
        assert JudgePolicy.from_bytes(policy.to_bytes()) == policy
    for budget in (-1, True, None, "1", float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="search_seconds"):
            JudgePolicy("command", "authority", search_seconds=budget)


@pytest.mark.parametrize("name,value", [
    ("local_guidance", False), ("timeout_seconds", 300),
    ("per_stimulus_seconds", 10), ("queue_depth", 100),
    ("component_timeout_seconds", 900), ("probe_budget", 8), ("max_probes", 8),
])
def test_removed_policy_knobs_are_not_silently_ignored(name, value):
    policy = JudgePolicy("command", "authority", search_seconds=2)
    row = parse_json(policy.to_bytes())
    assert name not in row
    row[name] = value
    with pytest.raises(ValueError):
        JudgePolicy.from_bytes(canonical_json(row))


def test_old_policy_is_not_silently_reinterpreted_or_rewritten():
    old = canonical_json({"schema": "behave.judge-policy/3", "command_version": "command",
        "authority_version": "authority", "max_retries": 1, "seed": "behave",
        "baseline_cases": 12, "queue_depth": 60, "probe_budget": 8, "local_guidance": False,
        "timeout_seconds": 300, "per_stimulus_seconds": 10, "component_timeout_seconds": 900,
        "worker_log_bytes": 1048576, "analysis": None})
    with pytest.raises(ValueError):
        JudgePolicy.from_bytes(old)
    assert parse_json(old)["queue_depth"] == 60


def test_previous_count_policy_is_not_interpreted_as_seconds():
    row = parse_json(JudgePolicy("command", "authority").to_bytes())
    row["schema"] = "behave.judge-policy/4"
    del row["search_seconds"]
    row["probe_budget"] = 8
    with pytest.raises(ValueError):
        JudgePolicy.from_bytes(canonical_json(row))


def test_comparison_parses_policy_once_and_refreshes_on_replacement(tmp_path, monkeypatch):
    from behave.task_execution.comparison import ComparisonInputs, ComparisonRequest

    task, _, _ = setup(tmp_path)
    parse = JudgePolicy.from_bytes
    parsed = []

    def count_parse(cls, data):
        parsed.append(data)
        return parse(data)

    monkeypatch.setattr(JudgePolicy, "from_bytes", classmethod(count_parse))
    inputs = ComparisonInputs("behavior", task.reference_behavior, task.reference_behavior, task.contract,
                              task.run_policy, task.judge_policy)
    request = ComparisonRequest(inputs, tmp_path / "execution", 1)
    policy = inputs.policy
    assert request.policy is inputs.policy is policy
    assert parsed == [task.judge_policy]

    changed_bytes = replace(policy, seed="another-seed").to_bytes()
    changed = replace(inputs, judge_policy=changed_bytes)
    assert changed.policy.seed == "another-seed"
    assert changed.judge_policy == changed_bytes
    assert inputs.judge_policy == task.judge_policy and inputs.policy is policy
    assert parsed == [task.judge_policy, changed_bytes]
    with pytest.raises((ValueError, TypeError)):
        replace(inputs, judge_policy=b"{}")


def test_comparison_stages_shared_content_once_and_keeps_record_manifest(tmp_path, monkeypatch):
    from behave.task_execution.comparison import ComparisonInputs

    task, _, _ = setup(tmp_path)
    inputs = ComparisonInputs("behavior", task.reference_behavior, task.reference_behavior, task.contract,
                              task.run_policy, task.judge_policy)
    local = DirectoryStore(tmp_path / "local")
    put = local.put
    written = []

    def count_put(data):
        written.append(data)
        return put(data)

    monkeypatch.setattr(local, "put", count_put)
    identity, closure = inputs.stage(local)
    descriptor = canonical_json(inputs.as_dict())
    # Candidate/reference share their source, and contract/run policy are both {}.
    expected = [task.contract.data, task.reference_behavior.files[0].data, task.judge_policy, descriptor]
    assert written == expected
    assert identity == BlobRef.of(descriptor)
    assert closure == (identity, *(BlobRef.of(data) for data in expected[:-1]))
    assert ComparisonInputs.from_dict(parse_json(local.read(identity)), local) == inputs


def test_worker_wire_preserves_the_exact_input_closure(tmp_path):
    import json

    from behave.task_execution.comparison import ComparisonInputs, ComparisonRequest
    from behave.task_execution.judge import JudgeRequest, _restore, _stage

    task, submission, journal = setup(tmp_path)
    frozen = FrozenSubmission.from_bytes(journal.objects.read(submission))
    source = LoadedSource(frozen.behavior.artifact, tuple(
        FileData(file, journal.objects.read(file.content)) for file in frozen.behavior.files))
    policy = JudgePolicy.from_bytes(task.judge_policy)
    inputs = ComparisonInputs("behavior", source, task.reference_behavior, task.contract,
                              task.run_policy, task.judge_policy)
    request = ComparisonRequest(inputs, tmp_path / "execution", 1)
    local = DirectoryStore(tmp_path / "local")
    packet = _stage(request, local)
    expected = {file.reference.content for file in (inputs.contract, *source.files, *task.reference_behavior.files)}
    expected.update((BlobRef.of(inputs.run_policy), BlobRef.of(inputs.judge_policy)))
    assert {path.name for path in local.directory.iterdir()} == {ref.sha256 for ref in expected}
    assert parse_json(packet)["inputs"] == inputs.as_dict()
    assert _restore(packet, local, request.workdir) == request
    # Policy identity keeps the original bytes; field agreement does not
    # require the publisher to have used our JSON whitespace/key ordering.
    pretty = replace(request, inputs=replace(inputs, judge_policy=json.dumps(parse_json(task.judge_policy), indent=2).encode()))
    assert _restore(_stage(pretty, local), local, request.workdir) == pretty
    with pytest.raises(ValueError, match="policy differs"):
        from behave.task_execution.simulator_judge import SimulatorJudge
        SimulatorJudge().evaluate(JudgeRequest(task, submission, "behavior", source,
                                              replace(policy, score_timeout_seconds=1), 1, request.workdir), local)
    altered = parse_json(packet)
    altered["inputs"]["component"] = "arbitrary.callable"
    with pytest.raises(ValueError, match="component"):
        _restore(canonical_json(altered), local, request.workdir)


@pytest.mark.parametrize("status,returncode", [("timeout", -9), ("output-limit", -9),
                                               ("launch-failed", None), ("cleanup-incomplete", None), ("exited", 1)])
def test_worker_failure_is_no_result_and_retains_bounded_logs(tmp_path, monkeypatch, status, returncode):
    from behave.isolation import service
    from behave.task_execution.simulator_judge import SimulatorJudge

    def fail(*args, **kwargs):
        return service.ServiceResult(status, returncode, b"diagnostic", b"stderr", .1)
    monkeypatch.setattr(service, "run_service", fail)
    task, submission, journal = setup(tmp_path, rtl=None, policy=JudgePolicy("cmd", "auth", search_seconds=0, max_retries=0))
    result = run(task, submission, journal, SimulatorJudge())
    assert result.score is None and result.behavior.origin == "infrastructure"
    assert result.behavior.retryable == (status != "cleanup-incomplete")
    assert b"diagnostic" in [journal.objects.read(ref) for ref in result.behavior.evidence]
    assert all(journal.objects.read(ref) is not None for event in journal.load() for ref in event.artifacts)


@pytest.mark.parametrize("kind", ["judge", "testbench", "ppa"])
@pytest.mark.parametrize("configured", [False, True])
def test_fixed_service_preserves_explicit_host_settings(tmp_path, monkeypatch, kind, configured):
    import time

    from behave.isolation import service
    from behave.task_execution._worker_transport import _SERVICES, exchange

    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("BEHAVE_EDA_ROOT", "separate-tools")
    settings = {"BEHAVE_REPLAY_WORKERS": "2", "BEHAVE_ANALYSIS_WORKERS": "3",
                "BEHAVE_REPO_ROOT": "deployment-assets"}
    for name, value in settings.items():
        if configured:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UNRELATED_PRIVATE_SETTING", "do-not-forward")
    packet = b"{}"

    def fixture(argv, **kwargs):
        env = kwargs["environment"]
        expected = {**settings, "BEHAVE_REPO_ROOT": str(caller / "deployment-assets")} if configured else {}
        assert {name: env[name] for name in settings if name in env} == expected
        assert env["BEHAVE_EDA_ROOT"] == str(caller / "separate-tools")
        assert "UNRELATED_PRIVATE_SETTING" not in env
        (kwargs["cwd"] / "response.json").write_bytes(canonical_json({
            "schema": _SERVICES[kind][1], "request": BlobRef.of(packet).as_dict(), "result": {},
        }))
        return service.ServiceResult("exited", 0, b"", b"", .1)

    monkeypatch.setattr(service, "run_service", fixture)
    response = exchange(kind, packet, workdir=tmp_path, objects=DirectoryStore(tmp_path / "objects"),
                        deadline=time.monotonic() + 10, timeout_seconds=10, log_bytes=1024)
    assert response.code is None


@pytest.mark.parametrize("kind", ["judge", "testbench", "ppa"])
def test_fixed_service_logs_can_truncate_without_discarding_its_response(tmp_path, monkeypatch, kind):
    import time

    from behave.isolation import service
    from behave.task_execution._worker_transport import _SERVICES, exchange

    # Internal JSON can exceed the removed 1 MiB cap independently of log retention.
    packet = canonical_json({"input": "x" * ((1 << 20) + 1)})
    result = {"completed": True, "diagnostic": "y" * ((1 << 20) + 1)}
    objects = DirectoryStore(tmp_path / "objects")

    def fixture(argv, **kwargs):
        assert kwargs["output_bytes"] == 3
        (kwargs["cwd"] / "response.json").write_bytes(canonical_json({
            "schema": _SERVICES[kind][1], "request": BlobRef.of(packet).as_dict(), "result": result,
        }))
        return service.ServiceResult("exited", 0, b"log", b"", .1, dropped_output_bytes=200)

    monkeypatch.setattr(service, "run_service", fixture)
    response = exchange(kind, packet, workdir=tmp_path, objects=objects, deadline=time.monotonic() + 10,
                        timeout_seconds=10, log_bytes=3)
    assert response.code is None and response.result == result and not response.retryable
    receipt = parse_json(objects.read(response.evidence[0]))
    assert receipt["status"] == "exited" and receipt["dropped_output_bytes"] == 200


@pytest.mark.parametrize("response", ["missing", "fifo", "symlink", "unbound", "malformed"])
def test_worker_response_must_be_regular_valid_and_bound(tmp_path, monkeypatch, capsys, response):
    import os

    from behave.isolation import service
    from behave.task_execution.judge import WORKER_SCHEMA as SCHEMA
    from behave.task_execution.simulator_judge import SimulatorJudge

    def fake(*args, **kwargs):
        path = kwargs["cwd"] / "response.json"
        if response == "fifo":
            os.mkfifo(path)
        elif response == "symlink":
            path.symlink_to("objects")
        elif response == "malformed":
            path.write_bytes(b"not a JSON result")
        elif response == "unbound":
            path.write_bytes(canonical_json({"schema": SCHEMA, "request": BlobRef.of(b"other").as_dict(),
                                             "result": ComponentJudgment(1, "false-pass").as_dict()}))
        return service.ServiceResult("exited", 0, b"", b"", .1)
    monkeypatch.setattr(service, "run_service", fake)
    task, submission, journal = setup(tmp_path, rtl=None, policy=JudgePolicy("cmd", "auth", search_seconds=0, max_retries=0))
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.code == "judge-worker-invalid-response"
    assert result.score is None
    assert "Traceback (most recent call last)" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["judge", "testbench", "ppa"])
def test_supervision_exception_keeps_traceback_and_does_not_authorize_retry(tmp_path, monkeypatch, capsys, kind):
    import time

    from behave.core.lifecycle import CleanupState
    from behave.isolation import service
    from behave.task_execution._worker_transport import exchange

    def failing_supervisor(*args, **kwargs):
        raise RuntimeError("fixture supervisor failed")

    monkeypatch.setattr(service, "run_service", failing_supervisor)
    objects = DirectoryStore(tmp_path / "objects")
    result = exchange(kind, b"{}", workdir=tmp_path, objects=objects,
                      deadline=time.monotonic() + 10, timeout_seconds=10, log_bytes=1024)
    assert result.code == f"{kind}-worker-supervision-failed" and result.result is None
    assert result.cleanup is CleanupState.UNKNOWN and not result.retryable
    stderr = capsys.readouterr().err
    assert "failing_supervisor" in stderr and "RuntimeError: fixture supervisor failed" in stderr
    assert parse_json(objects.read(result.evidence[0]))["cleanup"] == "unknown"


def test_judge_stage_and_main_do_not_cap_internal_json(tmp_path, monkeypatch):
    import sys

    from behave.task_execution import judge as worker
    from behave.task_execution.comparison import ComparisonInputs, ComparisonRequest
    from behave.task_execution.simulator_judge import _SimulatorService

    task, _, _ = setup(tmp_path)
    inputs = ComparisonInputs("behavior", task.reference_behavior, task.reference_behavior,
                              task.contract, task.run_policy, task.judge_policy)
    root = tmp_path / "worker"
    store = DirectoryStore(root / "objects")
    request = ComparisonRequest(inputs, root / "execution")
    encode = worker.canonical_json
    monkeypatch.setattr(worker, "canonical_json", lambda row: encode(row) + b" " * ((1 << 20) + 1))
    packet = worker._stage(request, store)
    reference = store.put(packet)
    assert len(packet) > 1 << 20
    monkeypatch.setattr(sys, "argv", ["worker", str(root), reference.sha256, str(reference.size_bytes)])
    result = ComponentJudgment(1, "fixture")
    monkeypatch.setattr(_SimulatorService, "evaluate", lambda *a, **kw: result)
    worker.main()
    response = (root / "response.json").read_bytes()
    assert len(response) > 1 << 20 and parse_json(response)["result"] == result.as_dict()


def test_worker_limits_are_part_of_policy_identity():
    policy = JudgePolicy("cmd", "auth", search_seconds=0)
    assert policy.SCHEMA == "behave.judge-policy/5"
    assert replace(policy, score_timeout_seconds=1).to_bytes() != policy.to_bytes()
    assert replace(policy, worker_log_bytes=1).to_bytes() != policy.to_bytes()
    for field in ("score_timeout_seconds", "worker_log_bytes"):
        with pytest.raises(ValueError):
            replace(policy, **{field: 0})
