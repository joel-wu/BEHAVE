"""Auxiliary RTL uses frozen task semantics, shared processes, checks and release evidence."""

import sys
from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import call
from test_construction import selected
from test_judge import RTL
from test_role_process import ROOT, Unavailable
from test_synthesis import reader_replies
from test_synthesis_process import fixed
from test_synthesis_source_review import package_and_policy, prepared, read_comparison, review_reply

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import load_chain
from behave.agent.model import Charge, ModelReply
from behave.agent.process_model import ModelCommand
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.data.admission import admit_package
from behave.data.drafts import load_draft
from behave.data.process_evidence import synthesis_billing
from behave.data.publication import publish_release
from behave.data.published import load_published_task
from behave.data.role import RoleJob, RtlInputs
from behave.data.role_policy import RolePolicy, RtlBuildPolicy
from behave.data.rtl import RtlBuildResult, comparison_inputs, load_candidate, role_inputs, synthesis_rtl, verify_rtl
from behave.data.synthesis import seal_review_package, synthesize
from behave.data.synthesis_records import SynthesisPlan, SynthesisResult
from behave.isolation.model import ResourceLimits
from behave.task_execution.judgment import ComponentJudgment


def rtl_policy(scripts, *, guard="", revisions=None):
    raw = {str(i): [replace(r, charge=Charge("0.01", "USD", "fixture")).as_dict() for r in replies]
           for i, replies in enumerate(scripts)}
    program = f'''
import sys
from behave.agent.model import ModelReply, ModelRequest
from behave.agent.process_model import encode_model_outcome
from behave.core.records import parse_json, canonical_json
request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
revision = request.call_id.split(':')[1][1:]
{guard}
reply = ModelReply.from_dict(parse_json({canonical_json(raw)!r})[revision][request.turn], raw=b'rtl-fixture')
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    command = ModelCommand((sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, program), "rtl-fixture/1")
    attempt = AttemptSpec("rtl", "rtl_author", {"model": "fixture", "transport": command.identity},
                          request_timeout_seconds=15)
    return RtlBuildPolicy(attempt, RolePolicy(command, shell_limits=ResourceLimits(wall_seconds=60, processes=32)),
                          len(scripts) if revisions is None else revisions)


def rtl_replies(body=RTL):
    return [shell_call('write', {"path": "candidate/top.sv", "content": body.decode()}),
            call("submit_rtl", {"rtl": selected("candidate/top.sv", "M"), "findings": []})]


def setup_rtl(tmp_path, scripts, *, real=False, guard="", revisions=None):
    options, author, _ = prepared(tmp_path, real=real)
    fixed(options, [author], [reader_replies()], [[read_comparison(), review_reply()]])
    options["plan"] = replace(options["plan"], rtl=rtl_policy(scripts, guard=guard, revisions=revisions))
    options["model_admission"] = ResourcePool(AdmissionPolicy("shared-model", 2))
    return options


def auxiliary(options, result, reader=None):
    run = options["run"]
    events = run.journal("synthesis").load()
    config = parse_json(run.objects.read(BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    return synthesis_rtl(reader or run.objects, options["plan"], result, config, events)


def test_policy_records_are_optional_versioned_and_closed(tmp_path):
    options = setup_rtl(tmp_path, [rtl_replies()])
    plan = options["plan"]
    assert SynthesisPlan.from_bytes(plan.to_bytes()) == plan
    assert parse_json(plan.to_bytes())["schema"] == "behave.synthesis-plan/5"
    old = replace(plan, rtl=None)
    assert parse_json(old.to_bytes())["schema"] == "behave.synthesis-plan/3"
    assert SynthesisPlan.from_bytes(old.to_bytes()) == old
    with pytest.raises(TypeError):
        replace(plan, processes=None)
    with pytest.raises(ValueError):
        replace(plan.rtl, max_revisions=0)
    with pytest.raises(ValueError):
        RtlInputs(BlobRef.of(b"draft"), BlobRef.of(b"parent"))
    job = RoleJob(RtlInputs(BlobRef.of(b"draft")), plan.rtl.attempt, plan.rtl.execution.model)
    assert RoleJob.from_bytes(job.to_bytes()) == job


@pytest.mark.isolation
@pytest.mark.eda
def test_real_rtl_repair_export_release_and_cold_recovery(tmp_path, capsys):
    # Invert every output bit, then repair RTL only. Even an all-zero stimulus witnesses the error.
    wrong = RTL.replace(b"out_y = in_a", b"out_y = in_a ^ 8'hff")
    assert wrong != RTL
    first = [shell_call('write', {"path": "scratch/note.txt", "content": "retain me"}), *rtl_replies(wrong)]
    repair = [shell_call('read', {"path": "scratch/note.txt"}),
              shell_call('read', {'path': '.rtl/evidence/0.blob'}), *rtl_replies()]
    options = setup_rtl(tmp_path, [first, repair], real=True, guard="""
if revision == '1' and request.turn == 1:
    assert 'retain me' in repr(request.messages[-2])
if revision == '1' and request.turn == 2:
    assert 'failed' in repr(request.messages[-2]) or '"score":0' in repr(request.messages[-2])
""")
    result = synthesize(**options)
    assert result.status == "review_ready" and len(result.drafts) == 1
    built, ledgers, rtl = auxiliary(options, result)
    assert built.status == "passed", built
    assert len(built.candidates) == len(built.checks) == len(ledgers) == 2
    assert [parse_json(options["run"].objects.read(c))["result"]["score"] for c in built.checks] == [0, 1]
    draft = load_draft(options["run"].objects, result.drafts[0]).record
    assert draft.task.reference_rtl is None  # No author draft mutation or fictitious author revision.
    assert SynthesisResult.from_bytes(result.to_bytes()) == result
    assert RtlBuildResult.from_bytes(built.to_bytes()) == built
    package, policy = package_and_policy(options)
    run = options["run"]
    admitted = admit_package(package=package, policy=policy, reader=run.objects)
    assert admitted.disposition.outcome == "include"
    assert admitted.task == replace(draft.task, reference_rtl=rtl)
    head, closure = run.seal("synthesis")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, (*closure, package, policy))
    assert auxiliary(options, result, exported)[0] == built
    assert synthesis_billing(exported, head).cost_by_currency() == {"USD": "0.17"}
    from behave.cli.inspect import inspect_record
    view = inspect_record("synthesis", exported, head)
    assert view["auxiliary_rtl"]["status"] == "passed" and view["auxiliary_rtl"]["candidates"] == 2
    destination = JournalDirectory(tmp_path / "release")
    published = publish_release(release_id="rtl", policy=policy, packages=(package,), reader=exported, destination=destination)
    assert load_published_task(destination.objects, published.release, "identity").manifest == admitted.task
    # A completed run needs neither upstream source availability nor a new model/check request.
    assert synthesize(**{**options, "reader": Unavailable()}) == result
    assert seal_review_package(run) == package
    assert options["model_admission"].counts == (0, 0)
    from behave.data.__main__ import main
    from behave.data.synthesis import SynthesisConfiguration
    config = SynthesisConfiguration(options["plan"], options["model_admission"].policy)
    config_file = tmp_path / "configuration.json"
    config_file.write_bytes(config.to_bytes())
    assert main(["--configuration", str(config_file), "--source-objects", str(tmp_path / "absent"),
                 "--run-directory", str(run.directory)]) == 0
    output = parse_json(capsys.readouterr().out.encode())
    assert output["auxiliary_rtl"]["status"] == "passed"
    assert output["billing"]["cost_by_currency"] == {"USD": "0.17"}


@pytest.mark.isolation
def test_rtl_mounts_are_frozen_and_author_reader_secrets_are_absent(tmp_path, monkeypatch):
    replies = [shell_call('list', {}),
               shell_call('write', {"path": "behavior.py", "content": "replace oracle"}),
               shell_call('write', {"path": "contract.json", "content": "replace interface"}),
               shell_call('read', {"path": "scratch.txt"}), *rtl_replies()]
    options = setup_rtl(tmp_path, [replies], guard="""
if request.turn == 1:
    names = {f['path'] for f in parse_json(request.messages[-2]['content']['stdout'].encode())["files"]}
    assert names == {'spec.md', 'contract.json', 'behavior.py', '.rtl/scope.json', '.rtl/run_policy.json'}
if request.turn in (2, 3):
    assert 'Read-only file system' in repr(request.messages[-2])
if request.turn == 4:
    assert 'No such file or directory' in repr(request.messages[-2])
assert 'ORIGINAL_SECRET' not in repr(request.messages)
""")
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options)
    built, _, _ = auxiliary(options, result)
    assert built.status == "passed", built
    # A forged snapshot cannot turn a changed oracle into a valid RTL candidate.
    store = options["run"].objects
    record, files = load_candidate(store, built.candidates[0], draft=result.drafts[0])
    from behave.agent.workspace import WorkspaceSnapshot
    snapshot = WorkspaceSnapshot(tuple(f.reference for f in files if f.reference.path != "behavior.py"))
    bad = store.put(replace(record, workspace=store.put(snapshot.to_bytes())).to_bytes())
    with pytest.raises(RecordError, match="frozen task"):
        load_candidate(store, bad, draft=result.drafts[0])


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ["no_result", "candidate_zero", "findings", "cleanup", "unfinished"])
def test_optional_failure_keeps_task_review_and_typed_triage(tmp_path, monkeypatch, kind):
    replies = ([call("submit_rtl", {"rtl": None, "findings": ["Contract needs clarification."]})]
               if kind == "findings" else [ModelReply("No implementation.")] if kind == "unfinished" else rtl_replies())
    options = setup_rtl(tmp_path, [replies])
    def compare(inputs, **kwargs):
        if inputs.component == "behavior":
            return ComponentJudgment(1, "fixture")
        if kind == "no_result":
            return ComponentJudgment(None, "verifier-broken", "verifier", retryable=True)
        return ComponentJudgment(0, "fixture", cleanup=CleanupState.INCOMPLETE if kind == "cleanup" else CleanupState.COMPLETE)
    monkeypatch.setattr(checks, "compare_sources", compare)
    options["check_admission"] = ResourcePool(AdmissionPolicy("checks", 1))
    result = synthesize(**options)
    assert result.status == "review_ready" and len(result.drafts) == 1
    built, _, rtl = auxiliary(options, result)
    expected = {"no_result": "check_unavailable", "candidate_zero": "revision_limit", "findings": "review_requested",
                "cleanup": "cleanup_unconfirmed", "unfinished": "unfinished"}[kind]
    assert built.status == expected and rtl is None
    package, policy = package_and_policy(options)
    admission = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert admission.disposition.reasons == ("auxiliary-rtl-" + expected,)
    assert admission.task.reference_rtl is None
    options["check_admission"] = ResourcePool(AdmissionPolicy("checks", 1))
    assert synthesize(**options) == result
    if kind == "cleanup":
        with pytest.raises(AttemptInterrupted), options["check_admission"].acquire():
            pytest.fail("cleanup failure must quarantine the fresh pool too")


@pytest.mark.isolation
def test_foreign_inputs_missing_branch_and_dropped_receipts_are_rejected(tmp_path, monkeypatch):
    options = setup_rtl(tmp_path, [rtl_replies()])
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options)
    built, _, _ = auxiliary(options, result)
    run, plan = options["run"], options["plan"]
    with pytest.raises(RecordError, match="missing"):
        auxiliary(options, replace(result, rtl_head=None))
    events = run.journal("synthesis").load()
    config = parse_json(run.objects.read(BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    for changed in ({"draft": BlobRef.of(b"another")}, {"policy": replace(plan.rtl, max_revisions=2)}):
        with pytest.raises(RecordError, match="identity changed"):
            verify_rtl(run.objects, result.rtl_head, **{"run_id": plan.run_id, "policy": plan.rtl,
                "draft": result.drafts[0], "resources": config, **changed})
    # A successful comparison never authorizes semantic repair/retry either.
    with pytest.raises(RecordError, match="failed comparison"):
        role_inputs(run.objects, RtlInputs(result.drafts[0], built.candidates[0], built.phases[-1].head))
    with pytest.raises(RecordError, match="another task"):
        comparison_inputs(run.objects, BlobRef.of(b"another"), built.candidates[0])
    # Re-seal a forged summary; hashes alone do not authenticate its assertions.
    from behave.agent.journal import Event
    branch = load_chain(run.objects, result.rtl_head)
    ref = run.objects.put(replace(built, phases=built.phases[:-1]).to_bytes())
    terminal = branch[-1]
    forged = Event(terminal.sequence, terminal.parent, terminal.kind, {"result": ref.as_dict()}, (ref,))
    forged_head = run.objects.put(forged.to_bytes())
    with pytest.raises(RecordError):
        verify_rtl(run.objects, forged_head, run_id=plan.run_id, policy=plan.rtl, draft=result.drafts[0], resources=config)


@pytest.mark.isolation
def test_unsettled_rtl_owner_cannot_erase_the_branch_or_claim_a_terminal_package(tmp_path, monkeypatch):
    from behave.data import rtl as rtl_module
    options = setup_rtl(tmp_path, [rtl_replies()])
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    dispatches = []
    def pending(*a, **k):
        dispatches.append(True)
        raise AttemptInterrupted("fixture-unsettled-owner")
    monkeypatch.setattr(rtl_module, "execute_role", pending)
    with pytest.raises((AttemptInterrupted, RecordError)):
        synthesize(**options)
    events = options["run"].journal("synthesis").load()
    assert dispatches == [True]
    assert events[-1].kind == "rtl_requested"
    assert not any(e.kind == "synthesis_finished" for e in events)
    with pytest.raises(RecordError, match="terminal"):
        seal_review_package(options["run"])


@pytest.mark.isolation
def test_crash_between_child_completion_and_parent_commit_does_not_resample(tmp_path, monkeypatch):
    from behave.data import rtl as rtl_module
    options = setup_rtl(tmp_path, [rtl_replies()])
    comparisons = []
    def compare(inputs, **kwargs):
        comparisons.append(inputs.component)
        return ComponentJudgment(1, "fixture")
    monkeypatch.setattr(checks, "compare_sources", compare)
    original = rtl_module.build_rtl
    class Crash(BaseException):
        pass
    def crash(**kwargs):
        original(**kwargs)
        raise Crash()
    monkeypatch.setattr(rtl_module, "build_rtl", crash)
    with pytest.raises(Crash):
        synthesize(**options)
    run = options["run"]
    assert run.journal("synthesis").load()[-1].kind == "rtl_requested"
    child = JournalDirectory(run.directory / "rtl_build")
    assert child.journal("rtl").load()[-1].kind == "rtl_finished"
    monkeypatch.setattr(rtl_module, "build_rtl", original)
    result = synthesize(**{**options, "reader": Unavailable()})
    assert auxiliary(options, result)[0].status == "passed"
    assert comparisons == ["behavior", "rtl"]
    head, _ = run.seal("synthesis")
    assert synthesis_billing(run.objects, head).cost_by_currency() == {"USD": "0.12"}
