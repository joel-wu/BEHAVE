"""Ambiguity is a repair handoff, never an invented implementation or score."""

import pytest
from test_attempts import Scripted, call
from test_role_process import Unavailable
from test_synthesis import reader_replies, setup
from test_synthesis_process import fixed
from test_synthesis_source_review import package_and_policy, prepared, read_comparison, review_reply

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.data.admission import admit_package
from behave.data.drafts import Reconstruction
from behave.data.process_evidence import synthesis_billing
from behave.data.repair_evidence import repair_closure
from behave.data.reviews import compare_reconstruction
from behave.data.role import AuthorInputs
from behave.data.synthesis import synthesize
from behave.data.synthesis_records import SynthesisResult


def findings():
    return call("submit_reconstruction", {"behavior": None, "findings": ["The output width is ambiguous."]})


def test_findings_only_is_canonical_and_cannot_be_empty():
    ref = BlobRef.of(b"draft")
    record = Reconstruction(ref, None, ("Missing output width",))
    assert Reconstruction.from_bytes(record.to_bytes()) == record
    with pytest.raises(ValueError, match="report findings"):
        Reconstruction(ref, None, ())


def test_exhausted_findings_never_dispatches_comparison_or_invents_score(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path, revisions=1)
    from behave.data import synthesis as synthesis_module
    monkeypatch.setattr(synthesis_module, "compare_reconstruction", lambda **kw: pytest.fail("no implementation"))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(findings()))
    assert result.status == "revision_limit" and result.checks == ()
    assert [p.role for p in result.phases] == ["author", "reader"]
    assert not hasattr(result, "score")
    assert SynthesisResult.from_bytes(result.to_bytes()) == result
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted()) == result


@pytest.mark.isolation
def test_findings_repair_real_process_check_admission_export_and_cold_recovery(tmp_path):
    options, first, submit = prepared(tmp_path, real=True)
    second = [call("read_file", {"path": "spec.md"}),
              call("write_file", {"path": "spec.md", "content": "An eight-bit identity, with eight-bit output."}),
              call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})]
    fixed(options, [first, second], [[findings()], reader_replies()], [
        [review_reply("unclear", builder_feedback="The output width is ambiguous. Specify eight-bit output.")],
        [read_comparison(), review_reply()]], guards={"author": """
if revision == '1':
    assert 'read_reader_findings' not in [t['name'] for t in request.tools]
    assert 'read_review_evidence' not in [t['name'] for t in request.tools]
    if request.turn == 0:
        assert b'output width is ambiguous' in canonical_json(list(request.messages))
"""})
    result = synthesize(**options)
    assert result.status == "review_ready", result.to_bytes()
    assert len(result.drafts) == 2 and len(result.checks) == 1
    assert [(p.revision, p.role) for p in result.phases if p.role == "check"] == [(1, "check")]
    assert SynthesisResult.from_bytes(result.to_bytes()) == result
    run = options["run"]
    head, closure = run.seal("synthesis")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    ledger = synthesis_billing(exported, head)
    assert ledger.cost_by_currency() == {"USD": "0.15"}  # includes mediation and executable examples
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=run.objects).disposition.outcome == "include"
    assert synthesize(**{**options, "reader": Unavailable()}) == result


def test_findings_report_is_bound_to_parent_and_cannot_be_compared(tmp_path):
    options, author, _ = setup(tmp_path, revisions=1)
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(findings()))
    store = options["run"].objects
    ref = store.put(Reconstruction(result.drafts[0], None, ("ambiguous",)).to_bytes())
    plan = options["plan"]
    inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy, result.drafts[0], reader_findings=ref)
    assert repair_closure(inputs, store) == (ref,)
    with pytest.raises(RecordError, match="cannot be scored"):
        compare_reconstruction(draft=result.drafts[0], reconstruction=ref, reader=store,
                               journal=options["run"].journal("unstarted_check"))
    bad = store.put(Reconstruction(BlobRef.of(b"unrelated"), None, ("ambiguous",)).to_bytes())
    from dataclasses import replace
    with pytest.raises(RecordError, match="parent draft"):
        repair_closure(replace(inputs, reader_findings=bad), store)
