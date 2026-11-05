"""Regression coverage for task errors, layout feedback and construction memory."""

from dataclasses import replace

import pytest
from syn_shell import shell_call
from test_attempts import Scripted, call
from test_construction import author, specification
from test_judge import REFERENCE
from test_synthesis import reader_replies
from test_synthesis_source_review import package_and_policy, prepared, review_reply

from behave.agent.journal import AttemptJournal
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.data.construction import run_author
from behave.data.publication import publish_release
from behave.data.synthesis import synthesize
from behave.improve.construction import construction_history, history_file
from behave.task_execution.judgment import ComponentJudgment


@pytest.mark.parametrize("malformed", [
    "A free-form task without the required sections.",
    specification().replace("### Behavior interface", "### Reference model"),
    specification() + "\n```python\nunfinished",
])
def test_spec_layout_error_is_repairable_before_blind_reconstruction(tmp_path, malformed):
    options, _, draft, selection, _, _ = author(tmp_path)
    options = {**options, "parent": draft,
               "journal": AttemptJournal(tmp_path / "repair", options["reader"]),
               "spec": replace(options["spec"], attempt_id="layout-repair")}
    submit = call("submit_draft", {"draft": {"adaptations": None, **selection}, "scope_findings": []})
    backend = Scripted(shell_call("write", {"path": "spec.md", "content": malformed}), submit,
                      shell_call("write", {"path": "spec.md", "content": specification()}), submit)
    result = run_author(backend=backend, **options)
    error = thaw_json(backend.requests[2].messages[-2])["content"]
    assert error["error"] == "draft-spec-format" and error["path"] == "spec.md"
    assert error["detail"] and result.final_action is not None


def test_reference_error_reaches_reviewer_then_builder_and_fresh_b2(tmp_path, monkeypatch):
    options, first, selection = prepared(tmp_path, revisions=2)
    outcomes = iter((ComponentJudgment(None, "judge-reference-unavailable", "reference"),
                     ComponentJudgment(1, "fixture")))
    def compare(inputs, **kwargs):
        assert inputs.reference_visible
        return next(outcomes)
    monkeypatch.setattr(checks, "compare_sources", compare)
    builder = Scripted(*first,
        shell_call("write", {"path": "behavior.py", "content": REFERENCE.decode() + "\n# Fixed B1"}),
        call("submit_draft", {"draft": {"adaptations": None, **selection}, "scope_findings": []}))
    blind = Scripted(*reader_replies(), shell_call("list", {}), *reader_replies())
    reviewer = Scripted(shell_call("read", {"path": "review/comparison/report.md"}),
        review_reply("unclear", builder_feedback="Repair B1's invalid return shape.",
                     implementer_feedback="OLD_B2_REPAIR_MUST_NOT_LEAK"), review_reply())
    result = synthesize(**options, author_backend=builder, reader_backend=blind, source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(result.drafts) == len(result.checks) == 2
    assert "UNAVAILABLE" in repr(reviewer.requests[1].messages)
    assert "Repair B1" in repr(builder.requests[-1].messages)
    assert "OLD_B2_REPAIR_MUST_NOT_LEAK" not in repr(blind.requests)
    package, policy = package_and_policy(options)
    store = options["run"].objects
    published = publish_release(release_id="fixed", policy=policy, packages=(package,), reader=store,
                                destination=JournalDirectory(tmp_path / "publication"))
    # History reads coordinator/review records, not private implementation bytes.
    publication = DirectoryStore(tmp_path / "publication" / "objects")
    from behave.data.published import ReleaseManifest
    manifest = ReleaseManifest.from_bytes(publication.read(published.release))
    store.put(publication.read(published.release))
    for ref in manifest.metadata["dispositions"]:
        ref = BlobRef.from_dict(dict(ref))
        store.put(publication.read(ref))
    history_plan = store.put(canonical_json({"initial": {"scope": options["plan"].scope.as_dict()}}))
    stages = [{"name": "construction:identity", "outcome": {"plan": history_plan.as_dict(), "status": result.status, "code": result.code,
        "stages": [{"stage": "synthesis", "head": options["run"].seal("synthesis")[0].as_dict()}]}}]
    history = store.put(construction_history(reader=store, round_id="round-1", prior=None,
        stages=stages, briefs={"identity-source": canonical_json({"source_id": "identity-source", "objective": "Preserve all bits"})},
        release=published.release))
    row = parse_json(history_file(store, history).data)["rounds"][0]["tasks"][0]
    assert row["admission"]["outcome"] == "include"
    revisions = row["construction"]["attempts"][0]["revisions"]
    assert revisions[0]["check"]["score"] is None and revisions[1]["check"]["score"] == 1
    assert revisions[0]["review"]["builder_feedback"] == "Repair B1's invalid return shape."
    next_history = construction_history(reader=store, round_id="round-2", prior=history,
                                         stages=(), briefs={}, release=None)
    assert [r["round_id"] for r in parse_json(next_history)["rounds"]] == ["round-1", "round-2"]
    assert b"# Fixed B1" not in next_history and b"ORIGINAL_SECRET" not in next_history


def test_history_retains_scope_failure_without_a_draft(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    source = store.put(canonical_json({"source_id": "source-a"}))
    plan = store.put(canonical_json({"author": {"inputs": {"source": source.as_dict()}}}))
    review = store.put(canonical_json({"assessments": [], "findings": ["Numeric representation is ambiguous"]}))
    stages = [{"name": "scope:task-a", "outcome": {"plan": plan.as_dict(), "status": "revision_limit",
        "code": "scope-review-budget", "scopes": [], "reviews": [review.as_dict()]}}]
    data = construction_history(reader=store, round_id="r", prior=None, stages=stages, briefs={}, release=None)
    task = parse_json(data)["rounds"][0]["tasks"][0]
    assert task["source_id"] == "source-a"
    assert task["scope"]["reviews"][0]["findings"] == ["Numeric representation is ambiguous"]
    assert "admission" not in task and "construction" not in task
