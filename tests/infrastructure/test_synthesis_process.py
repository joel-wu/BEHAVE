"""Production role processes inside the existing revision and admission workflow."""

import sys
from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import Scripted, call
from test_role_process import ROOT, Unavailable, job_for
from test_synthesis import reader_replies, setup
from test_synthesis_source_review import package_and_policy, prepared, read_comparison, review_reply

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import load_chain
from behave.agent.lifetime import AttemptControl
from behave.agent.model import Charge, ModelReply
from behave.agent.process_model import ModelCommand
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.admission import admit_package
from behave.data.drafts import load_draft
from behave.data.process_evidence import synthesis_billing
from behave.data.publication import publish_release
from behave.data.published import load_published_task
from behave.data.reviews import DraftCheck
from behave.data.role import AuthorInputs, RoleJob, input_closure
from behave.data.role_policy import ProcessRoles, RolePolicy
from behave.data.synthesis import seal_review_package, synthesize
from behave.data.synthesis_records import SynthesisPlan, SynthesisResult
from behave.isolation.model import ResourceLimits


def fixed(options, authors, readers, reviews=None, *, guards=None):
    """One pinned broker script selects a fixture by revision, not mutable counters."""
    plan = options["plan"]
    options.pop("development_tools", None)  # Process roles use their configured real tools.
    policies, attempts = {}, {}
    for role, scripts, template in (("author", authors, plan.author), ("reader", readers, plan.reader),
                                   ("source_review", reviews, None if plan.source_review is None
                                    else plan.source_review.reviewer)):
        if scripts is None:
            continue
        raw = {str(i): [replace(r, charge=Charge("0.01", "USD", "fixture")).as_dict() for r in replies]
               for i, replies in enumerate(scripts)}
        program = f'''
import sys
from behave.agent.model import ModelReply, ModelRequest
from behave.agent.process_model import encode_model_outcome
from behave.core.records import parse_json, canonical_json
request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
revision = request.call_id.split(':')[1][1:]
{(guards or {}).get(role, '')}
reply = ModelReply.from_dict(parse_json({canonical_json(raw)!r})[revision][request.turn], raw=b'fixture')
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
        command = ModelCommand((sys.executable, "-I", "-B", "-c",
            "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, program), "revision-fixture/1")
        policies[role] = RolePolicy(command, shell_limits=ResourceLimits(wall_seconds=60, processes=32))
        attempts[role] = replace(template, model_policy={"model": "fixture", "transport": command.identity},
                                 request_timeout_seconds=15)
    options["plan"] = replace(plan, author=attempts["author"], reader=attempts["reader"],
        source_review=None if plan.source_review is None else replace(plan.source_review, reviewer=attempts["source_review"]),
        processes=ProcessRoles(**policies))
    return options


@pytest.mark.isolation
def test_process_joint_review_real_check_export_admission_and_billing(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, real=True)
    review = [shell_call('read', {"path": "reconstruction/reader.py"}),
              shell_call('read', {'path': 'review/evidence/0.blob'}),
              read_comparison(),
              review_reply()]
    fixed(options, [first], [reader_replies()], [review], guards={"reader": """
assert b'retain across revisions' not in canonical_json(list(request.messages))
assert 'read_source_review' not in [t['name'] for t in request.tools]
""", "source_review": """
if request.turn == 2:
    messages = canonical_json(list(request.messages))
    assert b'class M:' in messages and b'comparison' in messages
"""})
    options["model_admission"] = ResourcePool(AdmissionPolicy("models", 2))
    result = synthesize(**options)
    run, store = options["run"], options["run"].objects
    assert result.status == "review_ready", (result.status, result.code)
    assert len(result.drafts) == 1
    assert [DraftCheck.from_bytes(store.read(ref)).comparison.result.score for ref in result.checks] == [1]
    assert all(DraftCheck.from_bytes(store.read(ref)).comparison.result.analysis is None for ref in result.checks)
    assert all(p.process is not None for p in result.phases if p.role != "check")
    assert SynthesisPlan.from_bytes(options["plan"].to_bytes()) == options["plan"]
    assert SynthesisResult.from_bytes(result.to_bytes()) == result
    head, closure = run.seal("synthesis")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(store, closure)
    ledger = synthesis_billing(exported, head)
    assert ledger.cost_by_currency() == {"USD": "0.12"}  # 6 author + 2 reader + 4 review; not double-billed.
    assert ledger.unpriced_dispatches == ledger.unresolved_dispatches == 0
    for phase in result.phases:
        if phase.process is not None:
            assert load_chain(exported, phase.process.supervision)[-1].payload["cleanup"] == "complete"
            assert load_chain(exported, phase.process.provider)[0].kind == "broker_initialized"
    package, policy = package_and_policy(options)
    admission = admit_package(package=package, policy=policy, reader=store)
    assert admission.disposition.outcome == "include"
    destination = JournalDirectory(tmp_path / "fixture-release")
    published = publish_release(release_id="process-fixture", policy=policy, packages=(package,), reader=store,
                                destination=destination)
    assert load_published_task(destination.objects, published.release, "identity").manifest == load_draft(store, result.drafts[-1]).record.task
    assert synthesis_billing(destination.objects, head) == ledger
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **k: pytest.fail("cold recovery must not execute"))
    assert synthesize(**{**options, "reader": Unavailable()}) == result
    assert seal_review_package(run) == package
    # A settled role ledger remains immutable after closure.
    old = run.journal("revisions", "r0", "author_provider")
    old.append(old.load(), "unexpected", {})
    for action in (lambda: synthesize(**options), lambda: seal_review_package(run)):
        with pytest.raises(RecordError, match="auxiliary journal changed"):
            action()


@pytest.mark.isolation
def test_process_implementer_repair_real_check_and_admission(tmp_path):
    options, first, _ = prepared(tmp_path, real=True)
    bad = reader_replies()
    bad[0] = shell_call('write', {"path": "reader.py", "content":
        "class M:\n    def reset(self): pass\n    def process(self, a): return {'y': a ^ 255}\n"})
    fixed(options, [first], [bad, [shell_call('read', {"path": "reader.py"}), *reader_replies()]],
          [[review_reply("unclear", implementer_feedback="Return the input value without bit inversion.")],
           [read_comparison(), review_reply()]])
    result = synthesize(**options)
    assert result.status == "review_ready", result.code
    assert result.drafts[0] == result.drafts[1]
    store = options["run"].objects
    assert [DraftCheck.from_bytes(store.read(ref)).comparison.result.score for ref in result.checks] == [0, 1]
    assert [p.role for p in result.phases if p.revision == 1] == ["reader", "check", "source_review"]
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=store).disposition.outcome == "include"
    head, _ = options["run"].seal("synthesis")
    assert synthesis_billing(store, head).cost_by_currency() == {"USD": "0.14"}


@pytest.mark.isolation
def test_process_unfinished_cancellation_and_cold_billing(tmp_path):
    options, _, _ = setup(tmp_path)
    fixed(options, [[ModelReply("cannot finish")]], [[]])
    result = synthesize(**options)
    assert result.status == "unfinished" and not result.drafts
    assert synthesis_billing(options["run"].objects, options["run"].seal("synthesis")[0]).cost_by_currency() == {"USD": "0.01"}
    assert synthesize(**options) == result
    options, _, _ = setup(tmp_path / "cancelled")
    fixed(options, [[]], [[]])
    options["control"] = AttemptControl(cancelled=lambda: True)
    options["model_admission"] = ResourcePool(AdmissionPolicy("models", 1))
    result = synthesize(**options)
    assert result.status == "interrupted" and not result.drafts
    assert len(result.phases) == 1 and result.phases[0].head is None
    assert result.phases[0].process.provider is not None
    billing = synthesis_billing(options["run"].objects, options["run"].seal("synthesis")[0])
    assert billing.cost_by_currency() == {} and billing.unresolved_dispatches == 0


@pytest.mark.parametrize("cleanup", ["unknown", "incomplete", "dangling"])
def test_unsafe_worker_never_seals_or_advances_and_quarantines(tmp_path, monkeypatch, cleanup):
    from behave.data import role as role_module

    options, _, _ = setup(tmp_path)
    fixed(options, [[ModelReply("stopped")]], [[]])
    policy = AdmissionPolicy("shared", 1)
    options["model_admission"] = ResourcePool(policy)
    calls = []
    class LostOwner(BaseException):
        pass
    def launch(**kwargs):
        calls.append(True)
        if cleanup == "dangling":
            raise LostOwner()
        return {"schema": "behave.construction-settlement/1", "cleanup": cleanup, "status": "timed-out",
                "returncode": None, "elapsed_seconds": 1., "interruption": None, "supervision_errors": []}, ()
    monkeypatch.setattr(role_module, "supervise_worker", launch)
    with pytest.raises(LostOwner if cleanup == "dangling" else RecordError):
        synthesize(**options)
    assert [e.kind for e in options["run"].journal("synthesis").load()] == ["synthesis_started", "phase_requested"]
    with pytest.raises(RecordError, match="terminal"):
        seal_review_package(options["run"])
    options["model_admission"] = ResourcePool(policy)
    with pytest.raises((AttemptInterrupted, RecordError)):
        synthesize(**options)
    assert calls == [True]
    with pytest.raises(AttemptInterrupted, match="cleanup"), options["model_admission"].acquire():
        pytest.fail("a new coordinator must quarantine unresolved ownership")


def test_fixed_policy_rejects_callbacks_and_changed_transport(tmp_path):
    options, _, _ = setup(tmp_path)
    fixed(options, [[]], [[]])
    with pytest.raises(ValueError, match="cannot mix"):
        synthesize(**options, author_backend=Scripted())
    with pytest.raises(ValueError, match="transport"):
        replace(options["plan"], author=replace(options["plan"].author, model_policy={"transport": "wrong"}))
    malformed = parse_json(options["plan"].to_bytes())
    malformed["processes"]["plugin"] = "candidate callback"
    with pytest.raises(RecordError):
        SynthesisPlan.from_bytes(canonical_json(malformed))


def test_repair_input_refuses_cross_revision_evidence_before_dispatch(tmp_path, monkeypatch):
    from behave.data import checks
    from behave.task_execution.judgment import ComponentJudgment

    options, author, _ = setup(tmp_path)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    store, plan = options["run"].objects, options["plan"]
    evidence = DraftCheck.from_bytes(store.read(result.checks[0]))
    wrong = store.put(replace(evidence, draft=BlobRef.of(b"another draft")).to_bytes())
    job = job_for(AuthorInputs(plan.task_id, plan.scope, plan.judge_policy, result.drafts[0], "repair", wrong), [])
    assert RoleJob.from_bytes(job.to_bytes()) == job
    with pytest.raises(RecordError, match="parent draft"):
        input_closure(job, store)


@pytest.mark.isolation
def test_sealed_accounting_rejects_missing_or_foreign_auxiliary(tmp_path):
    from behave.data.process_evidence import process_billing

    options, _, _ = setup(tmp_path)
    fixed(options, [[ModelReply("unfinished")]], [[]])
    options["model_admission"] = ResourcePool(AdmissionPolicy("models", 1))
    result = synthesize(**options)
    run = options["run"]
    phase = result.phases[0]
    events = run.journal("synthesis").load()
    config = parse_json(run.objects.read(BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    owner = load_chain(run.objects, phase.process.supervision)
    job = RoleJob.from_bytes(run.objects.read(BlobRef.from_dict(thaw_json(owner[0].payload)["job"])))
    for changed in (replace(phase.process, provider=None),
                    replace(phase.process, provider=phase.head),
                    replace(phase.process, development=phase.process.provider)):
        with pytest.raises(RecordError):
            process_billing(run.objects, replace(phase, process=changed), job, config)
    with pytest.raises(RecordError, match="resource policies"):
        process_billing(run.objects, phase, job, {**config, "model_admission": "changed"})
    with pytest.raises(RecordError, match="manifest"):
        process_billing(run.objects, phase, replace(job, inputs=replace(job.inputs, feedback="other")), config)


def test_coordinator_rejects_removed_development_pool_before_dispatch(tmp_path):
    options, first, _ = setup(tmp_path)
    fixed(options, [first], [reader_replies()])
    options["development_admission"] = ResourcePool(AdmissionPolicy("eda", 1))
    with pytest.raises(ValueError, match="Bash"):
        synthesize(**options)
    assert not options["run"].journal("synthesis").load()
