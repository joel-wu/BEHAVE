"""Joint review sees both implementations without guessing a repair owner."""

from dataclasses import replace

import pytest
from syn_shell import shell_call
from test_attempts import Scripted, call
from test_construction import specification
from test_release_admission import assessment
from test_synthesis import reader_replies, setup

from behave.agent.attempt import AttemptSpec
from behave.agent.model import Charge, ModelReply
from behave.agent.run_store import JournalDirectory
from behave.agent.trajectory import trajectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.data import synthesis as synthesis_module
from behave.data.admission import admit_package
from behave.data.drafts import load_draft
from behave.data.publication import publish_release
from behave.data.published import load_published_task
from behave.data.release_records import ReleasePolicy, ReviewPackage
from behave.data.source import SourceReview, review_check, review_inputs
from behave.data.synthesis import seal_review_package, synthesize
from behave.data.synthesis_records import SourceReviewPlan, SynthesisPlan, SynthesisResult
from behave.task_execution.judgment import ComponentJudgment


def prepared(tmp_path, monkeypatch=None, *, revisions=3, real=False):
    options, author, submit = setup(tmp_path, revisions=revisions, real=real)
    guidance = options["reader"].put(b"Fixture guidance: retain every declared source capability; cite discrepancies.")
    review = SourceReviewPlan(guidance, AttemptSpec("source", "source_reviewer", {"model": "review-fixture"}))
    options["plan"] = replace(options["plan"], source_review=review)
    return options, author, submit


def review_reply(conclusion="supported", findings=(), **repairs):
    return call("submit_source_review", {"assessments": [assessment(conclusion)], "findings": list(findings), **repairs})


def read_comparison():
    return shell_call('read', {'path': 'review/evidence/0.blob'})


@pytest.mark.parametrize("body", ["null", " \nnull\n"])
def test_joint_reviewer_accepts_runtime_default_policy(tmp_path, monkeypatch, body):
    options, first, _ = prepared(tmp_path, monkeypatch)
    first.insert(-1, shell_call("write", {"path": "generation.json", "content": body}, key="runtime-policy"))
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*first),
                        reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=Scripted(review_reply()))
    assert result.status == "review_ready"
    _, _, files = review_inputs(options["run"].objects, result.drafts[-1],
                               options["plan"].source_review.guidance, check=result.checks[-1])
    mounted = {file.reference.path: file.data for file in files}
    assert parse_json(mounted["review/implementations.json"])["run_policy"] is None
    assert mounted["review/run_policy.json"] == body.encode()


def test_reviewer_sees_exact_rtl_only_projection_without_rewriting_spec(tmp_path, monkeypatch):
    from behave.data.published import PublicTaskInputs
    from behave.data.spec_format import without_behavior_interface
    from behave.task_execution.solver import solver_input_files

    options, first, _ = prepared(tmp_path, monkeypatch)
    spec = specification("Model-only instruction misplaced outside the interface.")
    first.insert(-1, shell_call("write", {"path": "spec.md", "content": spec}, key="projection-spec"))
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    reviewer = Scripted(shell_call("read", {"path": "review/rtl_only_spec.md"}), review_reply())
    result = synthesize(**options, author_backend=Scripted(*first),
                        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=reviewer)
    loaded, _, files = review_inputs(options["run"].objects, result.drafts[-1],
                                    options["plan"].source_review.guidance, check=result.checks[-1])
    mounted = {file.reference.path: file.data for file in files}
    projected = mounted["review/rtl_only_spec.md"]
    assert mounted["draft/spec.md"] == spec.encode()
    assert projected == without_behavior_interface(spec).encode()
    assert b"Behavior interface" not in projected
    assert b"Identity() provides process(a)" not in projected
    assert b"Reset clears transaction state" in projected
    # Expose the mistake for review; do not silently delete text or auto-approve it.
    assert b"Model-only instruction misplaced" in projected
    public_task = PublicTaskInputs("identity", BlobRef.of(b"projection-fixture-release"),
        result.drafts[-1], "spec.md", "contract.json",
        (loaded.file("spec.md"), loaded.file("contract.json")))
    public = solver_input_files(public_task, setting="rtl_only")
    assert public[0].data == projected
    assert "review/rtl_only_spec.md" in repr(reviewer.requests[0].messages)


def test_reviewer_input_failure_can_resume_without_reauthoring_or_rechecking(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    first.insert(-1, shell_call("write", {"path": "generation.json", "content": "null"}, key="runtime-policy"))
    comparisons = []

    def compare(*args, **kwargs):
        comparisons.append(True)
        return ComponentJudgment(1, "fixture")

    def unavailable(**kwargs):
        raise RecordError("record-object-required", "expected a JSON object")

    monkeypatch.setattr(checks, "compare_sources", compare)
    with monkeypatch.context() as broken:
        broken.setattr(synthesis_module, "run_source_review", unavailable)
        with pytest.raises(RecordError, match="expected a JSON object"):
            synthesize(**options, author_backend=Scripted(*first),
                       reader_backend=Scripted(*reader_replies()),
                       source_reviewer_backend=Scripted())
    result = synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=Scripted(review_reply()))
    assert result.status == "review_ready"
    assert len(result.drafts) == len(result.checks) == len(comparisons) == 1


def package_and_policy(options):
    plan, run = options["plan"], options["run"]
    policy = ReleasePolicy("fixture", "acquired-training", plan.source_review.guidance, plan.judge_policy,
                           plan.source_review.reviewer.model_policy, False)
    return seal_review_package(run), run.objects.put(policy.to_bytes())


def test_joint_review_publishes_exact_pair_and_replays_cold(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    compared = []
    def compare(*args, **kwargs):
        compared.append(True)
        return ComponentJudgment(1, "fixture")
    monkeypatch.setattr(checks, "compare_sources", compare)
    author = Scripted(*first)
    reader = Scripted(*reader_replies())
    reviewer = Scripted(*(replace(r, charge=Charge("0.07", "USD", "fixture"))
                          for r in (read_comparison(), review_reply())))
    result = synthesize(**options, author_backend=author, reader_backend=reader, source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and result.code == "source-and-finite-comparison-agree"
    assert len(result.drafts) == len(compared) == 1
    assert [p.role for p in result.phases] == ["author", "reader", "check", "source_review"]
    store, run = options["run"].objects, options["run"]
    new = load_draft(store, result.drafts[-1])
    terminal = run.journal("revisions", "r0", "source_review").load()[-1]
    review_ref = BlobRef.from_dict(thaw_json(terminal.payload)["final_action"]["source_review"])
    report = SourceReview.from_bytes(store.read(review_ref))
    assert report.check == result.checks[-1]
    assert SourceReview.from_bytes(report.to_bytes()) == report
    assert all("read_source_review" not in repr(r.tools) for r in reviewer.requests)
    assert trajectory(run.journal("revisions", "r0", "source_review")).cost_by_currency() == {"USD": "0.14"}
    package, policy = package_and_policy(options)
    sealed = ReviewPackage.from_bytes(store.read(package))
    assert sealed.source_review_head == result.phases[-1].head
    assert admit_package(package=package, policy=policy, reader=store).disposition.outcome == "include"
    destination = JournalDirectory(tmp_path / "release")
    published = publish_release(release_id="repaired", policy=policy, packages=(package,), reader=store,
                                 destination=destination)
    assert load_published_task(destination.objects, published.release, "identity").manifest == new.record.task
    # The review, its check/B2 inputs and costs survive a complete store export.
    _, closure = run.seal("synthesis")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(store, closure)
    assert exported.read(result.phases[3].head) == store.read(result.phases[3].head)
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                      source_reviewer_backend=Scripted()) == result
    assert seal_review_package(run) == package and len(compared) == 1
    assert SynthesisPlan.from_bytes(options["plan"].to_bytes()) == options["plan"]
    assert SynthesisResult.from_bytes(result.to_bytes()) == result


@pytest.mark.parametrize("conclusion,findings", [("unsupported", ()), ("unclear", ()),
                                               ("supported", ("The specification drops a source capability",)),
                                               ("supported", ("Verified advisory (no action requested)",))])
def test_review_findings_do_not_guess_repair_owner(tmp_path, monkeypatch, conclusion, findings):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    author, reader = Scripted(*first), Scripted(*reader_replies())
    reviewer = Scripted(review_reply(conclusion, findings), *([ModelReply("stop")] if findings else []))
    result = synthesize(**options, author_backend=author, reader_backend=reader,
                        source_reviewer_backend=reviewer)
    assert (result.status, result.code) == ("unfinished", "source-review-stopped" if findings else
                                          "review-unresolved-without-repair")
    if findings:
        feedback = thaw_json(reviewer.requests[1].messages[-2])["content"]
        assert feedback["error"] == "review-repair-recipient-missing"
    assert len(result.drafts) == len(result.checks) == 1
    assert len(author.requests) == len(first) and len(reader.requests) == 2
    assert all("read_review_evidence" not in repr(request.tools) for request in author.requests)
    package, policy = package_and_policy(options)
    admitted = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert "construction-unfinished" in admitted.disposition.reasons
    assert admitted.disposition.outcome == "pending"
    if not findings:
        assert "source-review-findings-unresolved" in admitted.disposition.reasons


def test_joint_view_is_pinned_and_does_not_expose_private_scratch(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    cited = assessment()
    cited["citations"].append({"path": "reconstruction/reader.py", "first": 1, "last": 3})
    reviewer = Scripted(
        shell_call('list', {}),
        shell_call('write', {"path": "reconstruction/reader.py", "content": "overwrite"}),
        shell_call('write', {"path": "challenge.py", "content": "# PRIVATE_REVIEWER_TEST"}),
        read_comparison(),
        call("submit_source_review", {"assessments": [cited], "findings": []}))
    reader = Scripted(shell_call('write', {"path": "scratch.txt", "content": "PRIVATE_IMPLEMENTER_TEST"}),
                      *reader_replies())
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=reader,
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready"
    names = {f["path"] for f in parse_json(reviewer.requests[1].messages[-2]["content"]["stdout"].encode())["files"]}
    assert {"source/original.py", "draft/spec.md", "draft/behavior.py", "reconstruction/reader.py",
            "review/check.json", "review/implementations.json"} <= names
    assert all("scratch" not in name and ".construction" not in name for name in names)
    assert "workspace-read-only" in repr(reviewer.requests[2].messages)
    assert "PRIVATE_IMPLEMENTER_TEST" not in repr(reviewer.requests)
    assert "PRIVATE_REVIEWER_TEST" not in repr(reader.requests)
    store = options["run"].objects
    report, reconstruction, source = review_check(store, result.drafts[-1], result.checks[-1])
    _, _, files = review_inputs(store, result.drafts[-1], options["plan"].source_review.guidance,
                                check=result.checks[-1])
    metadata = parse_json(next(f.data for f in files if f.reference.path == "review/implementations.json"))
    assert metadata["implementer"]["artifact"] == source.artifact.as_dict()
    assert reconstruction.draft == report.draft == result.drafts[-1]
    with pytest.raises(RecordError) as error:
        review_check(store, BlobRef.of(b"different draft"), result.checks[-1])
    assert error.value.code == "review-check-mismatch"
    terminal = options["run"].journal("revisions", "r0", "source_review").load()[-1]
    ref = BlobRef.from_dict(thaw_json(terminal.payload)["final_action"]["source_review"])
    from behave.data.repair_evidence import repair_closure
    from behave.data.role import AuthorInputs

    plan = options["plan"]
    author_inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy, result.drafts[-1],
                                 check=result.checks[-1], source_review=ref)
    with pytest.raises(RecordError) as error:
        repair_closure(author_inputs, store)
    assert error.value.code == "repair-evidence-needs-mediation"
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=store).disposition.outcome == "include"


def test_findings_only_does_not_guess_builder_fault_or_fabricate_check(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("no B2 to compare"))
    reviewer = Scripted(shell_call('read', {"path": "review/reconstruction.json"}),
                        review_reply("unclear", ("Unspecified reset",)), ModelReply("stop"))
    result = synthesize(**options, author_backend=Scripted(*first), source_reviewer_backend=reviewer,
        reader_backend=Scripted(call("submit_reconstruction", {"behavior": None, "findings": ["Unspecified reset"]})))
    assert (result.status, result.code) == ("unfinished", "source-review-stopped")
    assert not result.checks and len(reviewer.requests) == 3
    assert thaw_json(reviewer.requests[-1].messages[-2])["content"]["error"] == "review-repair-recipient-missing"
    assert "Unspecified reset" in repr(reviewer.requests[-1].messages)
    assert len(result.drafts) == 1
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == "pending"


def test_mismatch_is_reviewed_but_reviewer_cannot_override_score(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch, revisions=2)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(0, "fixture"))
    reviewer = Scripted(shell_call('read', {"path": "review/check.json"}), read_comparison(), review_reply())
    result = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=reviewer)
    assert (result.status, result.code) == ("unfinished", "review-unresolved-without-repair")
    assert len(result.drafts) == 1 and len(reviewer.requests) == 3
    assert [p.revision for p in result.phases if p.role == "source_review"] == [0]
    assert '"score":0' in repr(reviewer.requests[-1].messages)
    package, policy = package_and_policy(options)
    admitted = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert admitted.disposition.outcome == "pending"
    assert set(admitted.disposition.reasons) == {"construction-unfinished"}


def test_draft_cannot_inherit_review_of_another_check(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch, revisions=2)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    original = synthesis_module.run_source_review
    def wrong(**kwargs):
        result = original(**kwargs)
        store = kwargs["journal"].objects
        ref = BlobRef.from_dict(thaw_json(result.final_action)["source_review"])
        review = SourceReview.from_bytes(store.read(ref))
        ref = store.put(replace(review, check=BlobRef.of(b"old check")).to_bytes())
        return replace(result, final_action={"source_review": ref.as_dict()})
    monkeypatch.setattr(synthesis_module, "run_source_review", wrong)
    with pytest.raises(RecordError, match="another draft, check or policy"):
        synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                   source_reviewer_backend=Scripted(read_comparison(), review_reply()))


def test_package_refuses_phase_changes_after_terminal(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
               source_reviewer_backend=Scripted(read_comparison(), review_reply()))
    run = options["run"]
    seal_review_package(run)
    journal = run.journal("revisions", "r0", "source_review")
    journal.append(journal.load(), "unexpected", {})
    with pytest.raises(RecordError, match="changed after termination"):
        seal_review_package(run)


@pytest.mark.parametrize("judgment", [ComponentJudgment(None, "infra", "infrastructure"),
                                     ComponentJudgment(1, "fixture")])
def test_unavailable_stops_but_reader_findings_with_behavior_reach_reviewer(tmp_path, monkeypatch, judgment):
    options, first, _ = prepared(tmp_path, monkeypatch, revisions=1)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: judgment)
    reviewer = Scripted(read_comparison(), review_reply())
    result = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies(findings=("Ambiguous",))), source_reviewer_backend=reviewer)
    assert len(reviewer.requests) == (0 if judgment.score is None else 2)
    assert result.status == ("check_unavailable" if judgment.score is None else "review_ready")
    package, _ = package_and_policy(options)
    assert (ReviewPackage.from_bytes(options["run"].objects.read(package)).source_review_head is None) == (judgment.score is None)


def test_unfinished_source_review_stops_without_spending_another_revision(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=Scripted(ModelReply("Need more evidence; cannot finish.")))
    assert result.status == "unfinished" and result.code == "source-review-stopped" and len(result.drafts) == 1
    package, policy = package_and_policy(options)
    admitted = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert "source-review-unfinished" in admitted.disposition.reasons


def test_sealed_source_review_survives_coordinator_crash(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    original = synthesis_module.run_source_review
    class Crash(BaseException):
        pass
    def crash(**kwargs):
        original(**kwargs)
        raise Crash()
    monkeypatch.setattr(synthesis_module, "run_source_review", crash)
    with pytest.raises(Crash):
        synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                    source_reviewer_backend=Scripted(read_comparison(), review_reply()))
    with pytest.raises(RecordError, match="only a terminal"):
        seal_review_package(options["run"])
    saved = options["run"].journal("revisions", "r0", "source_review").load()
    monkeypatch.setattr(synthesis_module, "run_source_review", original)
    reviewer = Scripted()
    result = synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and not reviewer.requests
    assert options["run"].journal("revisions", "r0", "source_review").load() == saved


def test_unknown_source_request_is_retained_not_redispatched(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    class Crash(BaseException):
        pass
    class Broken:
        def generate(self, request):
            raise Crash()
    with pytest.raises(Crash):
        synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                    source_reviewer_backend=Broken())
    result = synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=Scripted())
    assert result.status == "interrupted" and result.code == "attempt-outcome-unknown"
    package, policy = package_and_policy(options)
    admitted = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert admitted.disposition.outcome == "pending" and result.phases[-1].head in admitted.closure
    assert options["run"].journal("revisions", "r0", "source_review").load()[-1].kind == "model_requested"


def test_planned_review_cannot_be_replaced_by_a_standalone_report(tmp_path, monkeypatch):
    from behave.data.source import run_source_review
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=Scripted(read_comparison(), review_reply()))
    package, policy = package_and_policy(options)
    run, plan = options["run"], options["plan"]
    run_source_review(draft=result.drafts[-1], guidance=plan.source_review.guidance, reader=run.objects,
        journal=run.journal("replacement"), backend=Scripted(review_reply()), spec=plan.source_review.reviewer)
    other = run.objects.put(replace(ReviewPackage.from_bytes(run.objects.read(package)),
                                   source_review_head=run.seal("replacement")[0]).to_bytes())
    with pytest.raises(RecordError, match="planned revision"):
        admit_package(package=other, policy=policy, reader=run.objects)
    changed = replace(plan, source_review=replace(plan.source_review, guidance=options["reader"].put(b"Other obligations")))
    with pytest.raises(RecordError, match="replay differs"):
        synthesize(**{**options, "plan": changed}, author_backend=Scripted(), reader_backend=Scripted(),
                   source_reviewer_backend=Scripted())


def test_wrong_review_subject_is_refused_before_repair(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    original = synthesis_module.run_source_review
    def wrong(**kwargs):
        result = original(**kwargs)
        store = kwargs["journal"].objects
        ref = BlobRef.from_dict(thaw_json(result.final_action)["source_review"])
        review = SourceReview.from_bytes(store.read(ref))
        ref = store.put(replace(review, draft=BlobRef.of(b"another draft")).to_bytes())
        return replace(result, final_action={"source_review": ref.as_dict()})
    monkeypatch.setattr(synthesis_module, "run_source_review", wrong)
    with pytest.raises(RecordError, match="another draft, check or policy"):
        synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                    source_reviewer_backend=Scripted(read_comparison(), review_reply()))


@pytest.mark.isolation
def test_real_comparison_and_joint_review_publish(tmp_path):
    options, first, _ = prepared(tmp_path, real=True)
    result = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=Scripted(read_comparison(), review_reply()))
    assert result.status == "review_ready" and len(result.checks) == 1
    package, policy = package_and_policy(options)
    destination = JournalDirectory(tmp_path / "release")
    publication = publish_release(release_id="real-repair", policy=policy, packages=(package,),
                                   reader=options["run"].objects, destination=destination)
    assert load_published_task(destination.objects, publication.release, "identity").judge.reference_rtl is None
