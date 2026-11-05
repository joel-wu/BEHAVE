"""Pre-author scope review, frozen contracts and cold process accounting."""

import sys
from dataclasses import replace

import pytest
from syn_shell import shell_call, written_content
from test_attempts import call
from test_construction import seed
from test_release_admission import candidate
from test_role_process import ROOT, Unavailable, job_for
from test_synthesis import reader_replies, setup
from test_synthesis_command import command
from test_synthesis_process import fixed
from test_synthesis_source_review import package_and_policy, review_reply

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.lifetime import AttemptControl
from behave.agent.model import Charge, ModelReply
from behave.agent.process_model import ModelCommand
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore
from behave.data.admission import admit_package
from behave.data.drafts import ScopeProposal, load_draft, load_scope, scope_closure
from behave.data.publication import publish_release
from behave.data.published import load_published_task
from behave.data.release_records import ReleasePolicy, ScopeReviewPolicy
from behave.data.role import RoleJob, ScopeInputs, ScopeReviewInputs, input_closure
from behave.data.scope import (
    ScopeConfiguration,
    ScopePlan,
    ScopeResult,
    load_scope_preparation,
    preparation_closure,
    prepare_scope,
    scope_review_inputs,
)
from behave.data.synthesis import synthesize
from behave.data.synthesis_records import SourceReviewPlan, SynthesisPlan


def script_job(inputs, revisions, guard=""):
    job = job_for(inputs, [])
    scripts = [[replace(r, charge=Charge("0.01", "USD", "scope-fixture")).as_dict() for r in replies]
               for replies in revisions]
    program = f'''
import sys
from behave.agent.model import ModelReply, ModelRequest
from behave.agent.process_model import encode_model_outcome
from behave.core.records import canonical_json, parse_json
request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
revision = int(request.call_id.split(':')[1][1:])
{guard}
reply = ModelReply.from_dict(parse_json({canonical_json({'scripts': scripts})!r})['scripts'][revision][request.turn], raw=b'scope-fixture')
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    model = ModelCommand((sys.executable, "-I", "-B", "-c",
                         "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, program), "scope-fixture/1")
    return replace(job, model=model, attempt=replace(job.attempt, model_policy={"transport": model.identity}))


def review(conclusion="supported", *, last=2):
    return call("submit_scope_review", {"assessments": [{"capability": 0, "conclusion": conclusion,
        "rationale": "Check the full identity computation and explicit interface, not just a task name.",
        "citations": [{"path": "source/original.py", "first": 1, "last": last},
                      {"path": "proposal/scope.json", "first": 1, "last": 1},
                      {"path": "proposal/contract.json", "first": 1, "last": 1}]}], "findings": []})


def prepared(tmp_path, *, revise=True):
    options, author, submit = setup(tmp_path / "task", real=True)
    store = options["reader"]
    old = ScopeProposal.from_bytes(store.read(options["plan"].scope))
    guide = store.put(b"Retain the computation and protocol; justify every numeric adaptation.")
    contract = written_content(author[1])
    args = {"intent": old.intent, "capabilities": list(old.capabilities),
            "adaptations": list(old.adaptations), "contract": "contract.json"}
    initial = [shell_call('write', {"path": "author-secret", "content": "SCOPE_AUTHOR_PRIVATE"}),
               shell_call('write', {"path": "contract.json", "content": contract}), call("submit_scope", args)]
    again = [shell_call('read', {"path": ".scope/feedback.json"}),
             call("submit_scope", {**args, "intent": old.intent + "; retain every bit"})]
    proposer = script_job(ScopeInputs(old.source, guide), [initial, again], guard="""
if revision == 1 and request.turn == 1:
    assert b'unsupported' in canonical_json(list(request.messages))
""")
    reviewer = script_job(ScopeReviewInputs(BlobRef.of(b"placeholder"), guide),
                         [[review("unsupported") if revise else review()], [review()]], guard="""
assert {t['name'] for t in request.tools} == {'run_command', 'submit_scope_review'}
assert b'SCOPE_AUTHOR_PRIVATE' not in canonical_json(list(request.messages))
""")
    plan = ScopePlan("scope", proposer, reviewer.attempt, reviewer.policy)
    run = JournalDirectory(tmp_path / "scope")
    return options, author, submit, {"plan": plan, "reader": store, "run": run,
                                   "model_pool": ResourcePool(AdmissionPolicy("scope-models", 2))}


def test_scope_reviewer_receives_shared_contract_guidance_only(tmp_path):
    from behave.agent.prompting import render
    from behave.agent.workspace import Workspace

    options, author, _, scope_options = prepared(tmp_path)
    store = options["reader"]
    scope = ScopeProposal.from_bytes(store.read(options["plan"].scope))
    contract = FileRef("contract.json", store.put(written_content(author[1]).encode()))
    proposal = store.put(replace(scope, contract=contract).to_bytes())
    _, files = scope_review_inputs(store, proposal, scope_options["plan"].author.inputs.guidance)
    mounted = {f.reference.path: f.data for f in files}
    assert mounted["review/task_contract.md"] == render("synthesis/task_contract").encode()
    assert {"review/contract_settled.json", "review/contract_ready_valid.json"} <= mounted.keys()
    assert not any(path.startswith(("experience/", ".scope/")) for path in mounted)
    workspace = Workspace(store, read_only_paths=tuple(mounted))
    workspace.seed(files)
    with pytest.raises(RecordError, match="cannot change an input"):
        workspace.write("review/task_contract.md", b"changed")


@pytest.mark.isolation
def test_scope_repair_then_real_author_reader_check_and_cold_export(tmp_path, monkeypatch):
    options, author, _, scope_options = prepared(tmp_path)
    result = prepare_scope(**scope_options)
    assert result.status == "review_ready" and len(result.scopes) == 2, result.to_bytes()
    assert ScopeResult.from_bytes(result.to_bytes()) == result
    assert ScopePlan.from_bytes(scope_options["plan"].to_bytes()) == scope_options["plan"]
    run = scope_options["run"]
    head, closure = run.seal("scope")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    recovered, billing = load_scope_preparation(exported, head)
    assert recovered == result
    assert billing.cost_by_currency() == {"USD": "0.07"}
    assert billing.unpriced_dispatches == billing.unresolved_dispatches == 0
    scope, _, _ = load_scope(exported, result.scopes[-1])
    assert scope.parent == result.scopes[0] and scope.contract.path == "contract.json"
    assert preparation_closure(exported, head, scope=result.scopes[-1])
    with pytest.raises(RecordError, match="final reviewed scope"):
        preparation_closure(exported, head, scope=result.scopes[0])
    options["reader"].publish(exported, closure)
    guidance = options["reader"].put(b"Review retained source behavior after construction.")
    options["plan"] = replace(options["plan"], scope=result.scopes[-1], preparation=head,
        source_review=SourceReviewPlan(guidance, AttemptSpec("source", "source_reviewer", {"model": "fixture"})))
    # The author can no longer write contract.json; the scoped file is already mounted.
    author = [r for i, r in enumerate(author) if i != 1]
    from test_synthesis_source_review import read_comparison

    fixed(options, [author], [reader_replies()], [[read_comparison(), review_reply()]])
    assert SynthesisPlan.from_bytes(options["plan"].to_bytes()) == options["plan"]
    authored = synthesize(**options)
    assert authored.status == "review_ready", authored.to_bytes()
    task = load_draft(options["run"].objects, authored.drafts[-1])
    assert task.record.task.contract == scope.contract
    package, old_policy = package_and_policy(options)
    objects = options["run"].objects
    requirement = ScopeReviewPolicy(scope_options["plan"].author.inputs.guidance,
                                   scope_options["plan"].reviewer.model_policy)
    policy = replace(ReleasePolicy.from_bytes(objects.read(old_policy)), scope_review=requirement)
    assert ReleasePolicy.from_bytes(policy.to_bytes()) == policy
    policy_ref = objects.put(policy.to_bytes())
    admission = admit_package(package=package, policy=policy_ref, reader=objects)
    assert admission.disposition.outcome == "include"
    for mismatch in (replace(requirement, guidance=objects.put(b"Different review criteria")),
                     replace(requirement, model_policy={"model": "not-the-reviewed-model"})):
        stale = objects.put(replace(policy, scope_review=mismatch).to_bytes())
        denied = admit_package(package=package, policy=stale, reader=objects)
        assert denied.disposition.outcome == "pending" and "scope-review-policy-stale" in denied.disposition.reasons
    release = JournalDirectory(tmp_path / "fixture-release")
    published = publish_release(release_id="scoped-fixture", policy=policy_ref, packages=(package,),
                                reader=objects, destination=release)
    assert load_published_task(release.objects, published.release, "identity").manifest == task.record.task
    assert load_scope_preparation(release.objects, head) == (result, billing)
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("cold replay must not execute"))
    assert prepare_scope(**{**scope_options, "reader": Unavailable()}) == result
    assert synthesize(**{**options, "reader": Unavailable()}) == authored
    run.journal("revisions", "r0", "scope_author_provider").append(
        run.journal("revisions", "r0", "scope_author_provider").load(), "unexpected", {})
    with pytest.raises(RecordError, match="auxiliary journal changed"):
        prepare_scope(**scope_options)


def test_release_policy_cannot_omit_required_scope_review(tmp_path, monkeypatch):
    item = candidate(tmp_path, monkeypatch)
    policy = replace(item.policy, scope_review=ScopeReviewPolicy(item.policy.guidance, {"model": "scope"}))
    outcome = admit_package(package=item.ref, policy=item.store.put(policy.to_bytes()), reader=item.store)
    assert outcome.disposition.outcome == "pending"
    assert "scope-review-missing" in outcome.disposition.reasons


@pytest.mark.isolation
def test_scope_cli_preflight_terminal_and_resume(tmp_path):
    _, _, _, options = prepared(tmp_path, revise=False)
    config = ScopeConfiguration(options["plan"], AdmissionPolicy("scope-models", 2))
    path = tmp_path / "scope-config.json"
    path.write_bytes(config.to_bytes())
    assert ScopeConfiguration.from_bytes(config.to_bytes()) == config
    run = tmp_path / "command"
    preflight, output = command(path, options["reader"].directory, run, "--stage", "scope", "--dry-run")
    assert preflight.returncode == 0 and output["status"] == "validated"
    assert not run.exists()
    completed, output = command(path, options["reader"].directory, run, "--stage", "scope")
    assert completed.returncode == 0 and output["status"] == "review_ready", output
    assert output["billing"]["cost_by_currency"] == {"USD": "0.04"}
    assert output["scope"] and "review_package" not in output and "score" not in output
    again, cold = command(path, tmp_path / "absent", run, "--stage", "scope")
    assert again.returncode == 0 and cold == output
    directory = JournalDirectory(run)
    journal = directory.journal("revisions", "r0", "scope_author")
    journal.append(journal.load(), "unexpected", {})
    changed, rejected = command(path, tmp_path / "absent", run, "--stage", "scope")
    assert changed.returncode == 1 and rejected["code"] == "scope-phase-mismatch"


@pytest.mark.isolation
@pytest.mark.parametrize("stop", ["unfinished", "cancelled", "revision_limit"])
def test_scope_nonready_statuses_never_authorize_construction(tmp_path, stop):
    _, _, _, options = prepared(tmp_path)
    if stop == "unfinished":
        options["plan"] = replace(options["plan"], author=script_job(options["plan"].author.inputs,
                                                                   [[ModelReply("Cannot specify this task.")]]))
    elif stop == "cancelled":
        options["control"] = AttemptControl(cancelled=lambda: True)
    else:
        options["plan"] = replace(options["plan"], max_revisions=1)
    result = prepare_scope(**options)
    assert result.status == ("interrupted" if stop == "cancelled" else stop)
    head, _ = options["run"].seal("scope")
    assert load_scope_preparation(options["run"].objects, head)[0] == result
    with pytest.raises(RecordError, match="final reviewed scope"):
        preparation_closure(options["run"].objects, head, scope=BlobRef.of(b"anything"))


@pytest.mark.parametrize("cleanup", ["unknown", "dangling"])
def test_scope_unknown_owner_is_not_relaunched_or_sealed(tmp_path, monkeypatch, cleanup):
    from behave.data import role as role_module
    _, _, _, options = prepared(tmp_path)
    calls = []
    class Lost(BaseException):
        pass
    def launch(**kwargs):
        calls.append(True)
        if cleanup == "dangling":
            raise Lost()
        return {"schema": "behave.construction-settlement/1", "cleanup": "unknown", "status": "timed-out",
                "returncode": None, "elapsed_seconds": 1., "interruption": None, "supervision_errors": []}, ()
    monkeypatch.setattr(role_module, "supervise_worker", launch)
    with pytest.raises((RecordError, Lost)):
        prepare_scope(**options)
    assert options["run"].journal("scope").load()[-1].kind == "scope_phase_requested"
    options["model_pool"] = ResourcePool(options["model_pool"].policy)
    with pytest.raises((AttemptInterrupted, RecordError)):
        prepare_scope(**options)
    assert calls == [True]
    with pytest.raises(AttemptInterrupted, match="cleanup"), options["model_pool"].acquire():
        pytest.fail("unsafe scope worker capacity must remain quarantined")


def test_scope_ancestry_contract_and_process_manifest_validation(tmp_path):
    store, source, proposal, old = seed(tmp_path)
    contract = FileRef("contract.json", store.put(b"{}"))
    new = store.put(replace(proposal, contract=contract, parent=old).to_bytes())
    assert set(scope_closure(store, new)) >= {old, new, contract.content}
    foreign = store.put(replace(source, source_id="other").to_bytes())
    crossed = store.put(replace(proposal, source=foreign, contract=contract, parent=old).to_bytes())
    with pytest.raises(RecordError, match="retain the pinned source"):
        load_scope(store, crossed)
    guide = store.put(b"Review computation and interface.")
    for inputs in (ScopeInputs(proposal.source, guide), ScopeReviewInputs(new, guide)):
        job = job_for(inputs, [])
        assert RoleJob.from_bytes(job.to_bytes()) == job
        assert input_closure(job, store)
        executable = job_for(inputs, [], python=True)
        assert RoleJob.from_bytes(executable.to_bytes()) == executable
    missing = store.put(replace(proposal, contract=FileRef("contract.json", BlobRef.of(b"missing"))).to_bytes())
    with pytest.raises((RecordError, OSError)):
        load_scope(store, missing)
