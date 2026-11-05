"""Evidence-bound admission and publication; fixtures issue no real approvals."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import Scripted, call
from test_construction import specification
from test_synthesis import reader_replies, setup

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal, Event
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks, publication
from behave.data.admission import admit_package
from behave.data.checks import load_check
from behave.data.drafts import ScopeProposal
from behave.data.publication import publish_release
from behave.data.published import ReleaseManifest, load_published_task
from behave.data.release_records import HumanApproval, ReleaseDisposition, ReleasePolicy, ReviewPackage
from behave.data.source import run_source_review
from behave.data.synthesis import synthesize
from behave.task_execution.judgment import ComponentJudgment


def assessment(conclusion="supported"):
    return {"capability": 0, "conclusion": conclusion, "rationale": "Source and declared eight-bit identity agree.",
        "citations": [{"path": "source/original.py", "first": 2, "last": 2},
                      {"path": "draft/spec.md", "first": 1, "last": 1},
                      {"path": "draft/behavior.py", "first": 1, "last": 2}]}


def candidate(tmp_path, monkeypatch, *, conclusion="supported", findings=(), mode="acquired-training", real=False,
              spec_text=None, source=None, source_reader=None, task_id="identity"):
    options, author, _ = setup(tmp_path, real=real)
    source_path = "source/original.py"
    if source is not None:
        store = options["reader"]
        store.publish(source_reader, tuple(file.content for file in source.selected.files))
        scope = ScopeProposal.from_bytes(store.read(options["plan"].scope))
        scope = replace(scope, source=store.put(source.to_bytes()))
        options["plan"] = replace(options["plan"], scope=store.put(scope.to_bytes()))
        source_path = "source/" + source.selected.entry_file
    options["plan"] = replace(options["plan"], task_id=task_id)
    if not real:
        monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    if spec_text is not None:
        author[0] = shell_call("write", {"path": "spec.md", "content": specification(spec_text)})
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    run, store = options["run"], options["run"].objects
    guidance = store.put(b"Fixture guidance: inspect retained computation and report unresolved changes.")
    policy = ReleasePolicy("fixture/1", mode, guidance, options["plan"].judge_policy, {"model": "fixture"}, False)
    policy_ref = store.put(policy.to_bytes())
    assessed = assessment(conclusion)
    assessed["citations"][0]["path"] = source_path
    script = Scripted(shell_call("list", {}), shell_call("read", {"path": source_path}),
        shell_call("read", {"path": "scratch.txt"}), shell_call("read", {"path": "reader.py"}),
        shell_call("write", {"path": "draft/behavior.py", "content": "overwrite"}),
        call("submit_source_review", {"assessments": [assessed], "findings": list(findings)}))
    kwargs = {"draft": result.drafts[-1], "guidance": guidance, "reader": store,
              "journal": run.journal("source_review"), "development_tools": (fixture_shell(),), "spec": AttemptSpec("source-review", "source_reviewer", {"model": "fixture"})}
    with run.owner():
        review = run_source_review(**kwargs, backend=script)
        source_head, _ = run.seal("source_review")
        synthesis_head, _ = run.seal("synthesis")
    package = ReviewPackage(synthesis_head, source_head)
    ref = store.put(package.to_bytes())
    return SimpleNamespace(options=options, result=result, run=run, store=store, policy=policy, policy_ref=policy_ref,
                           package=package, ref=ref, review=review, script=script, kwargs=kwargs)


def admit(c, *, policy=None, approval=None, package=None):
    return admit_package(package=package or c.ref, policy=policy or c.policy_ref, reader=c.store, approval=approval)


def approval(c):
    return c.store.put(HumanApproval(c.ref, c.policy_ref, "fixture-operator", "Fixture-only approval.").to_bytes())


@pytest.mark.parametrize("conclusion", ["supported", "unclear"])
def test_owned_publication_keeps_decisions_and_closure_without_transcript_readback(
        tmp_path, monkeypatch, conclusion):
    from behave.agent.journal import load_chain
    from behave.data.publication import load_publication

    c = candidate(tmp_path, monkeypatch, conclusion=conclusion)
    full = admit(c)
    transcripts = set()
    for head in (c.package.source_review_head, *(phase.head for phase in c.result.phases)):
        for event in load_chain(c.store, head, verify_artifacts=False):
            if event.kind == "model_requested":
                transcripts.add(BlobRef.from_dict(thaw_json(event.payload)["request"]))
            elif event.kind == "model_settled":
                transcripts.add(BlobRef.from_dict(thaw_json(event.payload)["raw"]))
    assert transcripts and transcripts <= set(full.closure)

    class MetadataReader:
        def read(self, ref):
            assert ref not in transcripts, "live publication reread an old model transcript"
            return c.store.read(ref)

    reader = MetadataReader()
    projected = admit_package(package=c.ref, policy=c.policy_ref, reader=reader, verify_history=False)
    assert projected.disposition == full.disposition and projected.task == full.task
    assert set(projected.closure) == set(full.closure)
    destination = JournalDirectory(tmp_path / "owned-publication")
    options = {"release_id": "owned", "policy": c.policy_ref, "packages": (c.ref,), "reader": reader,
        "destination": destination, "verify_history": False,
        "publish_inputs": lambda target, refs: target.publish_owned(c.store, refs)}
    published = publish_release(**options)
    assert destination.seal("publication")[0] == published.head
    assert load_publication(destination.objects, published.head) == published
    assert publish_release(**options) == published
    assert all(destination.objects.read(ref) == c.store.read(ref) for ref in transcripts)


def test_owned_admission_still_applies_current_policy_and_review_findings(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch, findings=("Source transition is missing.",))
    policy = c.store.put(replace(c.policy, require_license_metadata=True).to_bytes())
    full = admit(c, policy=policy)
    projected = admit_package(package=c.ref, policy=policy, reader=c.store, verify_history=False)
    assert projected.disposition == full.disposition
    assert set(projected.closure) == set(full.closure)
    assert projected.disposition.outcome == "pending"
    assert "source-review-findings-unresolved" in projected.disposition.reasons
    assert "source-license-metadata-missing" in projected.disposition.reasons


def load_published_task_fixture_task(c):
    from behave.data.drafts import load_draft
    return load_draft(c.store, c.result.drafts[-1]).record.task


@pytest.mark.parametrize("conclusion,findings", [("unsupported", ()), ("unclear", ()),
                                               ("supported", ("Scope silently drops a state transition",))])
def test_score_one_and_human_cannot_override_source_issues(tmp_path, monkeypatch, conclusion, findings):
    c = candidate(tmp_path, monkeypatch, conclusion=conclusion, findings=findings, mode="reviewed-benchmark")
    admitted = admit(c, approval=approval(c))
    assert admitted.disposition.outcome == "pending"
    assert "source-review-findings-unresolved" in admitted.disposition.reasons


def test_modes_guidance_model_license_and_exact_human_approval(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch, mode="reviewed-benchmark")
    assert admit(c).disposition.reasons == ("human-approval-required",)
    assert admit(c, approval=approval(c)).disposition.outcome == "include"
    missing_license = c.store.put(replace(c.policy, require_license_metadata=True).to_bytes())
    assert "source-license-metadata-missing" in admit(c, policy=missing_license).disposition.reasons
    changed = replace(c.policy, guidance=c.store.put(b"Changed review obligations"))
    assert "source-review-policy-stale" in admit(c, policy=c.store.put(changed.to_bytes())).disposition.reasons
    changed = replace(c.policy, review_model_policy={"model": "a different model"})
    assert "source-review-policy-stale" in admit(c, policy=c.store.put(changed.to_bytes())).disposition.reasons
    with pytest.raises(RecordError, match="human approval belongs"):
        admit(c, policy=missing_license, approval=approval(c))
    no_review = c.store.put(replace(c.package, source_review_head=None).to_bytes())
    assert "source-review-missing" in admit(c, package=no_review).disposition.reasons
    with pytest.raises(RecordError, match="human approval belongs"):
        admit(c, package=no_review, approval=approval(c))


def test_review_citation_validation_is_feedback_not_approval(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    bad = assessment()
    bad["citations"][0]["last"] = 10000
    script = Scripted(call("submit_source_review", {"assessments": [bad], "findings": []}),
        call("submit_source_review", {"assessments": [], "findings": []}),
        call("submit_source_review", {"assessments": [assessment()], "findings": []}))
    result = run_source_review(**{**c.kwargs, "journal": c.run.journal("bad_citation")}, backend=script)
    assert result.final_action is not None
    assert "review-citation-invalid" in repr(script.requests[1].messages)
    assert "review-capabilities-incomplete" in repr(script.requests[2].messages)


def test_stale_review_cannot_follow_a_new_draft(tmp_path, monkeypatch):
    first = candidate(tmp_path / "first", monkeypatch)
    second = candidate(tmp_path / "second", monkeypatch, spec_text="A changed identity specification.")
    _, refs = first.run.seal("source_review")
    second.store.publish(first.store, refs)
    stale = second.store.put(replace(second.package, source_review_head=first.package.source_review_head).to_bytes())
    with pytest.raises(RecordError, match="stale draft"):
        admit(second, package=stale)


def test_unfinished_review_is_pending_with_its_cost_history_retained(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    events = c.run.journal("source_review").load()
    # A sealed prefix ending after dispatch is unknown, not a completed review.
    event = next(e for e in events if e.kind == "model_requested")
    package = c.store.put(replace(c.package, source_review_head=BlobRef.of(event.to_bytes())).to_bytes())
    result = admit(c, package=package)
    assert result.disposition.outcome == "pending"
    assert "source-review-unfinished" in result.disposition.reasons
    assert BlobRef.of(event.to_bytes()) in result.closure


def test_summary_cannot_rewrite_the_settled_comparison(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    events = c.run.journal("revisions", "r0", "check").load()
    payload = thaw_json(events[-1].payload)
    payload["result"]["score"] = 0
    forged = Event(events[-1].sequence, events[-1].parent, events[-1].kind, payload, events[-1].artifacts)
    head = c.store.put(forged.to_bytes())
    with pytest.raises(RecordError, match="summary differs"):
        load_check(c.store, head)


def test_publication_cold_recovery_and_native_task_reader(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    destination = JournalDirectory(tmp_path / "published")
    options = {"release_id": "fixture-r1", "policy": c.policy_ref, "packages": (c.ref,),
               "reader": c.store, "destination": destination}
    result = publish_release(**options)
    task = load_published_task(destination.objects, result.release, "identity")
    assert task.manifest == load_published_task_fixture_task(c)
    assert not any("source/" in f.reference.path or ".construction/" in f.reference.path for f in task.public.files)
    assert task.judge.reference_rtl is None
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("publication never executes Sim"))
    assert publish_release(**options) == result
    with pytest.raises(RecordError, match="replay differs"):
        publish_release(**{**options, "release_id": "r2"})
    _, refs = destination.seal("publication")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(destination.objects, refs)
    assert load_published_task(exported, result.release, "identity").manifest == task.manifest


def test_pending_package_is_counted_not_culled(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch, conclusion="unclear")
    destination = JournalDirectory(tmp_path / "published")
    result = publish_release(release_id="pending", policy=c.policy_ref, packages=(c.ref,),
                             reader=c.store, destination=destination)
    release = ReleaseManifest.from_bytes(destination.objects.read(result.release))
    assert not release.tasks and release.metadata["requested"] == release.metadata["pending"] == 1
    ref = BlobRef.from_dict(thaw_json(release.metadata)["dispositions"][0])
    disposition = ReleaseDisposition.from_bytes(destination.objects.read(ref))
    assert disposition.package == c.ref and disposition.outcome == "pending"


def test_failed_copy_has_no_terminal_commit_and_resumes(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    destination = JournalDirectory(tmp_path / "published")
    options = {"release_id": "r1", "policy": c.policy_ref, "packages": (c.ref,), "reader": c.store,
               "destination": destination}
    original = DirectoryStore.publish_owned
    def lost(self, source, refs, **kwargs):
        original(self, source, refs[:1], **kwargs)
        raise OSError("fixture copy loss")
    monkeypatch.setattr(DirectoryStore, "publish_owned", lost)
    with pytest.raises(OSError, match="copy loss"):
        publish_release(**options)
    assert [e.kind for e in destination.journal("publication").load()] == ["publication_started"]
    monkeypatch.setattr(DirectoryStore, "publish_owned", original)
    result = publish_release(**options)
    assert load_published_task(destination.objects, result.release, "identity").public.task_id == "identity"


def test_chunk_commit_recovery_keeps_the_publication_event_bounded(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    destination = JournalDirectory(tmp_path / "published")
    options = {"release_id": "r1", "policy": c.policy_ref, "packages": (c.ref,), "reader": c.store,
               "destination": destination}
    monkeypatch.setattr(publication.defaults, "PUBLICATION_CHUNK_REFS", 8)
    original = AttemptJournal.append
    def interrupted(self, expected, kind, payload, artifacts=()):
        result = original(self, expected, kind, payload, artifacts)
        if kind == "content_installed":
            raise OSError("fixture post-commit failure")
        return result
    monkeypatch.setattr(AttemptJournal, "append", interrupted)
    with pytest.raises(OSError, match="post-commit"):
        publish_release(**options)
    assert destination.journal("publication").load()[-1].kind == "content_installed"
    monkeypatch.setattr(AttemptJournal, "append", original)
    result = publish_release(**options)
    events = destination.journal("publication").load()
    chunks = [e for e in events if e.kind == "content_installed"]
    assert len(chunks) > 1 and all(len(e.artifacts) <= 8 for e in chunks)
    assert events[-1].artifacts == (result.release,)
    assert publish_release(**options) == result


def test_publication_reader_accepts_large_runtime_chunk_size(tmp_path, monkeypatch):
    from behave.data.publication import load_publication

    c = candidate(tmp_path, monkeypatch)
    destination = JournalDirectory(tmp_path / "published")
    monkeypatch.setattr(publication.defaults, "PUBLICATION_CHUNK_REFS", 1024)
    result = publish_release(release_id="large-chunks", policy=c.policy_ref, packages=(c.ref,),
                             reader=c.store, destination=destination)
    head, _ = destination.seal("publication")
    assert head == result.head
    monkeypatch.setattr(publication.defaults, "PUBLICATION_CHUNK_REFS", 2)
    assert load_publication(destination.objects, result.head) == result


@pytest.mark.isolation
def test_real_construct_review_publish_and_consume(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch, real=True)
    destination = JournalDirectory(tmp_path / "published")
    result = publish_release(release_id="real-fixture", policy=c.policy_ref, packages=(c.ref,), reader=c.store,
                             destination=destination)
    loaded = load_published_task(destination.objects, result.release, "identity")
    assert loaded.judge.run_policy and loaded.judge.reference_behavior.artifact.entry_file == "behavior.py"
    assert len(loaded.release.tasks) == 1
