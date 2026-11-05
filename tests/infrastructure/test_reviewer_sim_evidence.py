"""Reviewer tools consume shared Sim evidence, not a second model executor."""

from dataclasses import replace

import pytest
from syn_shell import shell_call
from test_attempts import Scripted, call
from test_synthesis import reader_replies
from test_synthesis_source_review import package_and_policy, prepared, read_comparison, review_reply

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.data import checks, source as source_module
from behave.data.admission import admit_package
from behave.data.drafts import load_draft
from behave.data.reviews import DraftCheck
from behave.data.source import SourceReview
from behave.data.synthesis import synthesize
from behave.task_execution.judgment import ComponentJudgment


@pytest.mark.parametrize("inspect", [False, True])
def test_review_uses_the_pinned_public_comparison_without_reexecuting(tmp_path, monkeypatch, inspect):
    options, author, _ = prepared(tmp_path, monkeypatch, revisions=1)
    store = options["run"].objects
    native = store.put(b'{"fixture": "shared comparison evidence"}')
    comparisons = []

    def compare(inputs, **kwargs):
        comparisons.append(inputs)
        return ComponentJudgment(1, "fixture", evidence=(native,))

    monkeypatch.setattr(checks, "compare_sources", compare)
    reads = [read_comparison(), shell_call("read", {"path": "review/evidence/1.blob"})] if inspect else []
    reviewer = Scripted(*reads, review_reply())
    result = synthesize(**options, author_backend=Scripted(*author),
                        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(comparisons) == 1
    loaded = load_draft(store, result.drafts[-1])
    comparison = comparisons[0]
    assert comparison.component == "behavior"
    assert comparison.reference_behavior == loaded.source(loaded.record.task.reference_behavior)
    assert comparison.contract == loaded.file(loaded.record.task.contract.path)
    assert comparison.run_policy == store.read(loaded.record.task.run_policy)
    assert comparison.judge_policy == loaded.judge_policy
    assert "check_behavior_examples" not in repr(reviewer.requests)
    assert {tool["name"] for tool in reviewer.requests[0].tools} == {"run_command", "submit_source_review"}
    events = options["run"].journal("revisions", "r0", "source_review").load()
    report = SourceReview.from_bytes(store.read(BlobRef.from_dict(
        thaw_json(events[-1].payload)["final_action"]["source_review"])))
    assert report.check == result.checks[-1] and report.challenges == ()
    if inspect:
        outputs = [parse_json(store.read(BlobRef.from_dict(thaw_json(event.payload)["result"])))
                   for event in events if event.kind == "tool_settled"]
        assert outputs[0]["exit_code"] == outputs[1]["exit_code"] == 0
        assert outputs[0]["stdout"] == store.read(result.checks[-1]).decode()
        assert outputs[1]["stdout"] == store.read(native).decode()
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=store).disposition.outcome == "include"
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                      source_reviewer_backend=Scripted()) == result
    assert len(comparisons) == 1


@pytest.mark.parametrize("score,origin", [(0, "candidate"), (None, "reference"),
                                         (None, "verifier"), (None, "infrastructure")])
def test_shell_success_and_reviewer_approval_cannot_replace_public_result(tmp_path, monkeypatch, score, origin):
    options, author, _ = prepared(tmp_path, monkeypatch, revisions=1)
    judgment = ComponentJudgment(score, "fixture", origin=origin)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: judgment)
    reviewer = Scripted(call("run_command", {}), review_reply())
    result = synthesize(**options, author_backend=Scripted(*author),
                        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=reviewer)
    assert result.status != "review_ready"
    store = options["run"].objects
    check = DraftCheck.from_bytes(store.read(result.checks[-1]))
    assert check.comparison.result == judgment
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=store).disposition.outcome != "include"


def test_legacy_example_receipts_remain_readable_without_replaying_models(tmp_path, monkeypatch):
    options, author, _ = prepared(tmp_path, monkeypatch, revisions=1)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()),
               source_reviewer_backend=Scripted(review_reply()))
    events = options["run"].journal("revisions", "r0", "source_review").load()
    store = options["run"].objects
    report = SourceReview.from_bytes(store.read(BlobRef.from_dict(
        thaw_json(events[-1].payload)["final_action"]["source_review"])))
    archived = replace(report, challenges=(BlobRef.of(canonical_json({"legacy": "tool event"})),))
    assert SourceReview.from_bytes(archived.to_bytes()) == archived


def test_old_submission_binding_is_not_silently_accepted_under_new_rules(tmp_path, monkeypatch):
    options, author, _ = prepared(tmp_path, monkeypatch, revisions=1)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    with monkeypatch.context() as previous:
        previous.setattr(source_module, "REVIEW_VERSION", "source-review/4")
        synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()),
                   source_reviewer_backend=Scripted(review_reply()))
    package, policy = package_and_policy(options)
    with pytest.raises(RecordError, match="unknown submission contract"):
        admit_package(package=package, policy=policy, reader=options["run"].objects)
