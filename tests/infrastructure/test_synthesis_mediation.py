"""Mediated repair uses scripted models/tools; no claim about semantic adequacy."""

import pytest
from syn_shell import shell_call
from test_attempts import Scripted, call
from test_construction import specification
from test_synthesis import reader_replies
from test_synthesis_source_review import package_and_policy, prepared, read_comparison, review_reply

from behave.core.records import parse_json, thaw_json
from behave.data import checks
from behave.data.admission import admit_package
from behave.data.drafts import load_draft
from behave.data.process_evidence import synthesis_billing
from behave.data.source import review_inputs
from behave.data.synthesis import synthesize
from behave.task_execution.judgment import ComponentJudgment


def test_implementer_only_repair_preserves_builder_and_exports_admission(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    outcomes = iter([0, 1])
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(next(outcomes), "fixture"))
    author = Scripted(*first)
    reader = Scripted(shell_call('write', {"path": "own_test.py", "content": "# Implementer private test"}),
                      *reader_replies(), shell_call('read', {"path": "own_test.py"}),
                      shell_call('read', {"path": "reader.py"}), *reader_replies())
    reviewer = Scripted(review_reply("unclear", implementer_feedback="Repair your output field."),
                        read_comparison(), review_reply())
    result = synthesize(**options, author_backend=author, reader_backend=reader, source_reviewer_backend=reviewer)
    assert result.status == "review_ready"
    assert result.drafts[0] == result.drafts[1]
    assert [p.role for p in result.phases if p.revision == 1] == ["reader", "check", "source_review"]
    assert len(author.requests) == len(first)
    assert "Repair your output field." in repr(reader.requests[3].messages)
    assert "retain across revisions" not in repr(reader.requests)
    assert "Implementer private test" in repr(reader.requests[4].messages)
    assert "class M:" in repr(reader.requests[5].messages)
    assert "Implementer private test" not in repr(reviewer.requests)
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == "include"
    head, _ = options["run"].seal("synthesis")
    synthesis_billing(options["run"].objects, head)
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                      source_reviewer_backend=Scripted()) == result


def test_findings_only_routes_to_builder_then_fresh_blind_reader(tmp_path, monkeypatch):
    options, first, submit = prepared(tmp_path, monkeypatch)
    compared = []
    def compare(*a, **k):
        compared.append(True)
        return ComponentJudgment(1, "fixture")
    monkeypatch.setattr(checks, "compare_sources", compare)
    author = Scripted(*first,
        shell_call('write', {"path": "spec.md", "content": specification("Identity operation. Reset clears state.")}),
        call("submit_draft", {"draft": {"adaptations": None, **{**submit, "adaptations": ["Reset is explicitly defined for the hardware interface."]}}, "scope_findings": []}))
    reader = Scripted(call("submit_reconstruction", {"behavior": None, "findings": ["Reset unspecified"]}),
                      shell_call('list', {}), *reader_replies())
    reviewer = Scripted(review_reply("unclear", builder_feedback="Define reset in the public spec.",
                                   implementer_feedback="This old feedback must not enter a new blind attempt."),
                        read_comparison(), review_reply())
    result = synthesize(**options, author_backend=author, reader_backend=reader, source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and compared == [True]
    assert len(result.drafts) == 2 and len(result.checks) == 1
    assert "Define reset" in repr(author.requests[len(first)].messages)
    assert "old feedback" not in repr(reader.requests)
    visible = {f["path"] for f in parse_json(reader.requests[2].messages[-2]["content"]["stdout"].encode())["files"]}
    assert visible == {"spec.md", "contract.json", ".construction/sim_check.md",
                       ".construction/behavior_environment.json"}
    assert load_draft(options["run"].objects, result.drafts[-1]).record.parent == result.drafts[0]
    adapted = load_draft(options["run"].objects, result.drafts[-1]).record
    assert adapted.adaptations == ("Reset is explicitly defined for the hardware interface.",)
    _, _, files = review_inputs(options["run"].objects, result.drafts[-1], options["plan"].source_review.guidance)
    assert next(f for f in files if f.reference.path == "review/adaptations.json").data == (
        b'["Reset is explicitly defined for the hardware interface."]')
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == "include"


def test_public_comparison_does_not_require_a_second_tool_execution(tmp_path, monkeypatch):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    reviewer = Scripted(review_reply())
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready"
    assert len(reviewer.requests) == 1
    events = options["run"].journal("revisions", "r0", "source_review").load()
    submitted = [thaw_json(e.payload) for e in events if e.kind == "tool_settled"]
    assert [row["terminal"] for row in submitted] == [True]


@pytest.mark.parametrize("findings", [
    ("No answer-changing ambiguities found; local checks passed.",),
    ("The reset rule might be unspecified; review the reset paragraph.",),
])
def test_bound_reviewer_can_resolve_reader_findings_without_reauthoring(tmp_path, monkeypatch, findings):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    reviewer = Scripted(shell_call('read', {"path": "review/reconstruction.json"}), review_reply())
    result = synthesize(**options, author_backend=Scripted(*first),
                        reader_backend=Scripted(*reader_replies(findings=findings)),
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready"
    assert len(result.drafts) == len(result.checks) == 1
    assert findings[0] in repr(reviewer.requests[-1].messages)
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == "include"
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(),
                      source_reviewer_backend=Scripted()) == result


@pytest.mark.parametrize("score,reply", [
    (0, review_reply()),
    (1, review_reply("unclear")),
])
def test_resolving_reader_claims_cannot_bypass_comparison_or_unresolved_review(tmp_path, monkeypatch, score, reply):
    options, first, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(score, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*first),
                        reader_backend=Scripted(*reader_replies(findings=("Please check reset.",))),
                        source_reviewer_backend=Scripted(reply))
    assert (result.status, result.code) == ("unfinished", "review-unresolved-without-repair")
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == "pending"
