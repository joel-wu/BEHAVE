"""Scope reopening is a new reviewed lineage, with the old attempt preserved."""

import shlex
import sys
from dataclasses import replace

import pytest
from test_role_process import Unavailable
from test_scope_preparation import prepared
from test_synthesis import reader_replies
from test_synthesis_command import command
from test_synthesis_process import fixed
from test_synthesis_source_review import read_comparison, review_reply

from behave.agent.attempt import AttemptSpec
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.admission import admit_package
from behave.data.escalation import ScopeEscalation
from behave.data.publication import publish_release
from behave.data.release_records import ReleasePolicy, ReviewPackage, ScopeReviewPolicy
from behave.data.scope import ScopeConfiguration, load_scope_preparation
from behave.data.supervisor import ConstructionConfiguration, ConstructionPlan, construct, verify_construction_prefix
from behave.data.synthesis import SynthesisConfiguration
from behave.data.synthesis_records import SourceReviewPlan, SynthesisPlan, SynthesisResult


def configuration(tmp_path, *, source_review=False):
    options, author, _, scope_options = prepared(tmp_path, revise=False)
    author = [r for i, r in enumerate(author) if i != 1]  # new scope already mounts the reviewed contract
    if source_review:
        guide = options["reader"].put(b"Fixture-only source review: retain the full identity behavior.")
        options["plan"] = replace(options["plan"], source_review=SourceReviewPlan(
            guide, AttemptSpec("source", "source_reviewer", {"model": "fixture"})))
    fixed(options, [author], [reader_replies()], [[read_comparison(), review_reply()]] if source_review else None)
    plan = options["plan"]
    model = plan.processes.author.model
    prefix = "request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))"
    injected = prefix + '''
if request.call_id.startswith('construction:'):
    from behave.agent.model import ToolCall, Charge
    reply = ModelReply('', (ToolCall('scope', 'submit_draft',
        {'draft': None, 'scope_findings': ['Specify the eight-bit output width before continuing.']}),),
        charge=Charge('0.01','USD','fixture'))
    sys.stdout.buffer.write(encode_model_outcome(request, reply))
    raise SystemExit(0)
'''
    model = replace(model, argv=(*model.argv[:-1], model.argv[-1].replace(prefix, injected)))
    initial = replace(plan, author=replace(plan.author, model_policy={"model": "fixture", "transport": model.identity}),
                      processes=replace(plan.processes, author=replace(plan.processes.author, model=model)))
    return ConstructionPlan("supervisor", initial, scope_options["plan"]), options["reader"]


@pytest.mark.isolation
def test_real_scope_escalation_reopens_without_regrading_or_resampling(tmp_path, monkeypatch):
    plan, reader = configuration(tmp_path, source_review=True)
    assert ConstructionPlan.from_bytes(plan.to_bytes()) == plan
    run = JournalDirectory(tmp_path / "supervisor")
    def duplicate_audit(*args, **kwargs):
        pytest.fail("live construction must not rerun the offline lineage audit")

    with monkeypatch.context() as live:
        live.setattr("behave.data.supervisor.verify_construction_prefix", duplicate_audit)
        result = construct(plan=plan, reader=reader, run=run)
    assert result["status"] == "review_ready", result
    assert [s["stage"] for s in result["stages"]] == ["synthesis", "scope", "synthesis"]
    assert result["cost_by_currency"] == {"USD": "0.14"}  # includes executable Reviewer examples
    first = JournalDirectory(run.directory / "runs/task_0")
    terminal = thaw_json(first.journal("synthesis").load()[-1].payload)
    old = SynthesisResult.from_bytes(first.objects.read(BlobRef.from_dict(terminal["result"])))
    assert old.status == "scope_review_requested" and not old.drafts and not old.checks
    request = ScopeEscalation.from_bytes(first.objects.read(old.escalation))
    assert request.scope == plan.initial.scope and request.parent is None
    new, _ = load_scope_preparation(run.objects, BlobRef.from_dict(result["stages"][1]["head"]))
    last = JournalDirectory(run.directory / "runs/task_1")
    last_result = SynthesisResult.from_bytes(last.objects.read(BlobRef.from_dict(
        thaw_json(last.journal("synthesis").load()[-1].payload)["result"])))
    latest = SynthesisPlan.from_bytes(last.objects.read(last_result.plan))
    assert latest.scope == new.scopes[-1] and latest.scope != plan.initial.scope
    _, closure = run.seal("construction")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    package = ReviewPackage.from_bytes(exported.read(BlobRef.from_dict(result["package"])))
    ledger, refs = verify_construction_prefix(exported, package.construction_prefix, synthesis_head=package.synthesis_head)
    assert ledger.cost_by_currency() == result["cost_by_currency"] and old.escalation in refs
    rules = ReleasePolicy("fixture/1", "acquired-training", plan.initial.source_review.guidance,
        plan.initial.judge_policy, plan.initial.source_review.reviewer.model_policy, False,
        ScopeReviewPolicy(plan.scope_review.author.inputs.guidance, plan.scope_review.reviewer.model_policy))
    policy = exported.put(rules.to_bytes())
    package_ref = BlobRef.from_dict(result["package"])
    admitted = admit_package(package=package_ref, policy=policy, reader=exported)
    assert admitted.disposition.outcome == "include" and old.escalation in admitted.closure
    release = JournalDirectory(tmp_path / "fixture-release")
    published = publish_release(release_id="supervised-fixture", policy=policy, packages=(package_ref,),
                                reader=exported, destination=release)
    assert verify_construction_prefix(release.objects, package.construction_prefix,
                                      synthesis_head=package.synthesis_head)[0] == ledger
    exercise_published_consumer(tmp_path, release.objects, published.release)
    with pytest.raises(RecordError, match="last completed"):
        verify_construction_prefix(exported, package.construction_prefix,
                                   synthesis_head=BlobRef.from_dict(result["stages"][0]["head"]))
    assert construct(plan=plan, reader=Unavailable(), run=run) == result
    with pytest.raises(RecordError):
        construct(plan=replace(plan, max_reopens=1), reader=Unavailable(), run=run)


def exercise_published_consumer(tmp_path, objects, release):
    """The actual emitted task feeds a solver and real B/B + B/RTL scoring."""
    from test_judge import BEHAVIOR, RTL
    from test_solver_process import BROKER, ROOT

    from behave.agent.admission import AdmissionPolicy
    from behave.agent.process_model import ModelCommand
    from behave.data.published import load_published_task
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.simulator_judge import SimulatorJudge
    from behave.task_execution.solver import SolverJob
    from behave.train.learning_report import load_training_report
    from behave.train.plan import TrainingManifest, TrainingPlan
    from behave.train.rollouts import run_rollouts
    from behave.train.training_data import TrainingRequirements

    task = load_published_task(objects, release, "identity").public
    edit = ("printf %s " + shlex.quote(BEHAVIOR.decode()) + " > agent.py\n"
            + "printf %s " + shlex.quote(RTL.decode()) + " > top.sv")
    program = BROKER + f'''
if request.turn == 0:
    calls = (ToolCall('edit','run_command',{{'command':{edit!r}}}),)
else:
    calls = (ToolCall('s','submit',{{
        'behavior':{{'files':['agent.py'],'entry_file':'agent.py','entry_symbol':'M'}},
        'rtl':{{'files':['top.sv'],'entry_file':'top.sv','entry_symbol':'M'}},'testbench':None}}),)
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('', calls, charge=Charge('0.01','USD','fixture'))))
'''
    command = ModelCommand((sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, program), "joint-fixture/1")
    attempt = AttemptSpec("published-solver", "solver", {"model": "fixture", "transport": command.identity},
                          request_timeout_seconds=15)
    job = SolverJob(task, attempt, command)
    execution = BatchPlan("published-batch", (job,), SimulatorJudge.identity, 1, AdmissionPolicy("eda", 1))
    manifest = objects.put(TrainingManifest(release, ((task.task_id, task.revision),)).to_bytes())
    provenance = BlobRef.of(b"fixture-does-not-supply-training-tokens")
    requirements = TrainingRequirements("fixture/1", provenance, provenance, provenance)
    plan = TrainingPlan(execution, manifest, requirements, (None,))
    run = JournalDirectory(tmp_path / "published-rollouts")
    report = run_rollouts(plan=plan, approved_manifest=manifest, reader=objects, run=run, judge=SimulatorJudge())
    assert report["rewarded"] == 1 and report["candidates"][0]["examples"]["reward"] == 1, report
    assert len(report["candidates"][0]["examples"]["turns"]) == 2 and report["eligible_turns"] == 0
    assert report["billing"]["known_estimates_by_currency"] == {"USD": "0.02"}
    head, refs = run.seal("training")
    archive = DirectoryStore(tmp_path / "rollout-export")
    archive.publish(run.objects, refs)
    assert load_training_report(archive, head, approved_manifest=manifest) == report


@pytest.mark.isolation
def test_zero_reopen_budget_keeps_request_and_has_no_package(tmp_path):
    plan, reader = configuration(tmp_path)
    run = JournalDirectory(tmp_path / "zero")
    result = construct(plan=replace(plan, max_reopens=0), reader=reader, run=run)
    assert result["status"] == "scope_reopen_limit" and result["package"] is None
    assert len(result["stages"]) == 1 and result["cost_by_currency"] == {"USD": "0.01"}
    assert not (run.directory / "runs/scope_1").exists()


def test_scope_request_rejects_empty_findings():
    ref = BlobRef.of(b"scope")
    with pytest.raises(ValueError, match="requires findings"):
        ScopeEscalation("task", ref, None, ())
    request = ScopeEscalation("task", ref, None, ("missing handshake semantics",))
    assert ScopeEscalation.from_bytes(request.to_bytes()) == request
    assert parse_json(request.to_bytes())["scope"] == ref.as_dict()


@pytest.mark.isolation
@pytest.mark.parametrize("recipient", ["builder", "implementer"])
def test_supervisor_continues_saved_repair_and_retains_finished_task(tmp_path, monkeypatch, recipient):
    from syn_shell import shell_call
    from test_attempts import call
    from test_construction import specification
    from test_synthesis_source_review import prepared as task_fixture

    from behave.data import checks
    from behave.data import synthesis as synthesis_module
    from behave.improve.construction import construction_handoff
    from behave.task_execution.judgment import ComponentJudgment

    options, author, submit = task_fixture(tmp_path / "template", revisions=1)
    repair = [shell_call("write", {"path": "spec.md", "content": specification("Identity; reset clears state.")}),
              call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})]
    fixed(options, [author, repair], [reader_replies(), reader_replies()],
          [[review_reply("unclear", **{recipient + "_feedback": "Clarify reset."})], [review_reply()]])
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: ComponentJudgment(1, "fixture"))
    configuration = ConstructionConfiguration("task", SynthesisConfiguration(options["plan"]), None)
    original = JournalDirectory(tmp_path / "original")
    first = construct(plan=configuration.plan, reader=options["reader"], run=original)
    assert first["status"] == "revision_limit"
    head, _ = original.seal("construction")
    updated = replace(configuration, synthesis=replace(configuration.synthesis,
        plan=replace(configuration.synthesis.plan, max_revisions=2)))
    task_id = options["plan"].task_id
    retained, repairs = construction_handoff(original.objects, {task_id: head}, updated)
    assert retained == {}
    plan, prior = repairs[task_id]
    assert prior.as_dict() == first["stages"][-1]["head"]
    called, execute = [], synthesis_module.execute_role

    def new_revision_only(job, **kwargs):
        called.append((kwargs["revision"], kwargs["role"]))
        assert kwargs["revision"] == 1
        return execute(job, **kwargs)

    monkeypatch.setattr(synthesis_module, "execute_role", new_revision_only)
    resumed = JournalDirectory(tmp_path / "continued")
    result = construct(plan=plan, reader=original.objects, run=resumed, continuation_from=prior)
    assert result["status"] == "review_ready"
    assert called == ([(1, "author")] if recipient == "builder" else []) + [(1, "reader"), (1, "source_review")]
    package = ReviewPackage.from_bytes(resumed.objects.read(BlobRef.from_dict(result["package"])))
    current_head, refs = resumed.seal("construction")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(resumed.objects, refs)
    ledger, _ = verify_construction_prefix(exported, package.construction_prefix,
                                           synthesis_head=package.synthesis_head)
    assert ledger.cost_by_currency() == result["cost_by_currency"]
    retained, repairs = construction_handoff(exported, {task_id: current_head}, updated)
    assert repairs == {} and retained[f"construction:{task_id}"][0]["outcome"] == result
    assert construct(plan=plan, reader=Unavailable(), run=resumed, continuation_from=prior) == result
    assert original.seal("construction")[0] == head


def test_supervisor_command_preflight_and_source_consistency(tmp_path):
    plan, reader = configuration(tmp_path)
    config = ConstructionConfiguration(plan.run_id, SynthesisConfiguration(plan.initial),
                                       ScopeConfiguration(plan.scope_review))
    assert ConstructionConfiguration.from_bytes(config.to_bytes()) == config
    path = tmp_path / "configuration.json"
    path.write_bytes(config.to_bytes())
    run = tmp_path / "untouched"
    completed, output = command(path, reader.directory, run, "--stage", "construction", "--dry-run")
    assert completed.returncode == 0, completed.stderr
    assert output["status"] == "validated" and output["max_reopens"] == 2
    assert not run.exists()
