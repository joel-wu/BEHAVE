"""Reviewer argument errors identify the mistake without authorizing a review."""

from dataclasses import replace

import pytest
from test_attempts import Scripted, call
from test_construction import author

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import parse_json, thaw_json
from behave.data.contract import contract_guidance
from behave.data.scope import run_scope_review
from behave.data.source import run_source_review


@pytest.mark.parametrize("role", ["scope_reviewer", "source_reviewer"])
@pytest.mark.parametrize("mistake", ["unknown_path", "outside_lines", "missing_source"])
def test_bad_citation_has_detail_and_corrected_submission_finishes(tmp_path, role, mistake):
    options, _, draft, _, _, proposal = author(tmp_path)
    store = options["reader"]
    guidance = store.put(b"Inspect the retained computation and cite the supplied files.")
    if role == "scope_reviewer":
        contract = next(f.data for f in contract_guidance(".scope/")
                        if f.reference.path == ".scope/contract_ready_valid.json")
        proposal = replace(proposal, contract=FileRef("contract.json", store.put(contract)))
        target = {"proposal": store.put(proposal.to_bytes())}
        run, tool, public_path = run_scope_review, "submit_scope_review", "proposal/scope.json"
        error_code = "scope-review-arguments-invalid"
    else:
        target = {"draft": draft}
        run, tool, public_path = run_source_review, "submit_source_review", "draft/spec.md"
        error_code = "review-arguments-invalid"

    source_citation = {"path": "source/original.py", "first": 1, "last": 2}
    correct = {"capability": 0, "conclusion": "supported", "rationale": "The identity mapping is retained.",
               "citations": [source_citation, {"path": public_path, "first": 1, "last": 1}]}
    if mistake == "unknown_path":
        invalid = {**source_citation, "path": "source/wrong.py"}
        expected_detail = "source/wrong.py"
    elif mistake == "outside_lines":
        invalid = {**source_citation, "last": 10000}
        error_code, expected_detail = "review-citation-invalid", "source/original.py:1-10000"
    if mistake == "missing_source":
        bad = {**correct, "citations": [correct["citations"][1]]}
        error_code, expected_detail = "review-citation-incomplete", "missing citations under source/"
    else:
        bad = {**correct, "citations": [invalid, correct["citations"][1]]}
    model = Scripted(call(tool, {"assessments": [bad], "findings": []}),
                     call(tool, {"assessments": [correct], "findings": []}))
    result = run(**target, guidance=guidance, reader=store,
                 journal=AttemptJournal(tmp_path / "review", store), backend=model,
                 spec=AttemptSpec("review", role, {"model": "fixture"}))

    feedback = thaw_json(model.requests[1].messages[-2])["content"]
    assert feedback["error"] == error_code
    assert expected_detail in feedback["detail"]
    if mistake == "outside_lines":
        assert "capability 0" in feedback["detail"]
        assert "2 lines" in feedback["detail"]
    assert len(model.requests) == 2
    assert result.reason == "final_action"


@pytest.mark.parametrize("correction", ["builder_feedback", "implementer_feedback", "nonblocking"])
def test_missing_repair_recipient_is_corrected_in_the_same_attempt(tmp_path, monkeypatch, correction):
    from test_synthesis import reader_replies
    from test_synthesis_source_review import prepared, review_reply

    from behave.data import checks
    from behave.data.source import review_check
    from behave.data.synthesis import synthesize
    from behave.task_execution.judgment import ComponentJudgment

    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: ComponentJudgment(1, "fixture"))
    built = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=Scripted(review_reply()))
    store = options["run"].objects
    check, _, _ = review_check(store, built.drafts[-1], built.checks[-1])
    finding = "Resolve the inconsistent reset description."
    bad = review_reply("supported", (finding,), builder_feedback=" ", implementer_feedback="")
    fixed = (review_reply() if correction == "nonblocking" else
             review_reply("unclear", (finding,), **{correction: finding}))
    model = Scripted(bad, fixed)
    result = run_source_review(draft=built.drafts[-1], check=built.checks[-1],
        reconstruction=check.reconstruction, guidance=options["plan"].source_review.guidance,
        reader=store, journal=AttemptJournal(tmp_path / "recipient_review", store), backend=model,
        spec=AttemptSpec("recipient-review", "source_reviewer", {"model": "fixture"}))
    feedback = thaw_json(model.requests[1].messages[-2])["content"]
    assert feedback["error"] == "review-repair-recipient-missing"
    assert "builder_feedback" in feedback["detail"] and "implementer_feedback" in feedback["detail"]
    assert len(model.requests) == 2 and result.reason == "final_action"
    review = parse_json(store.read(BlobRef.from_dict(thaw_json(result.final_action)["source_review"])))
    assert review["findings"] == ([] if correction == "nonblocking" else [finding])
    for owner in ("builder_feedback", "implementer_feedback"):
        assert review[owner] == (finding if owner == correction else "")


@pytest.mark.parametrize("owner", ["builder_feedback", "implementer_feedback"])
def test_quoted_empty_feedback_does_not_dispatch_a_spurious_revision(tmp_path, monkeypatch, owner):
    from test_synthesis import reader_replies
    from test_synthesis_source_review import prepared, review_reply

    from behave.data import checks
    from behave.data.synthesis import synthesize
    from behave.task_execution.judgment import ComponentJudgment

    options, first, _ = prepared(tmp_path, monkeypatch, revisions=1)
    comparisons = []

    def compare(*args, **kwargs):
        comparisons.append(True)
        return ComponentJudgment(1, "fixture")

    monkeypatch.setattr(checks, "compare_sources", compare)
    reviewer = Scripted(review_reply(**{owner: '\"\"'}), review_reply())
    result = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(result.drafts) == len(comparisons) == 1
    assert len(reviewer.requests) == 2
    feedback = thaw_json(reviewer.requests[1].messages[-2])["content"]
    assert feedback["error"] == "review-feedback-quoted-empty"
    assert owner in feedback["detail"]
