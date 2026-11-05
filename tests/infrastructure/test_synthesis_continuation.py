"""Explicit continuation preserves old evidence and does not resample inherited roles."""

from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import Scripted, call
from test_construction import specification
from test_role_process import Unavailable
from test_synthesis import reader_replies
from test_synthesis_source_review import package_and_policy, prepared, review_reply

from behave.agent.run_store import JournalDirectory
from behave.agent.trajectory import CostLedger, trajectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.data.admission import admit_package
from behave.data.continuation import load_continuation
from behave.data.process_evidence import synthesis_billing, synthesis_costs
from behave.data.synthesis import synthesize
from behave.task_execution.judgment import ComponentJudgment


def stopped_pair(tmp_path, monkeypatch, *, score=None, cleanup=CleanupState.COMPLETE):
    options, author, _ = prepared(tmp_path, revisions=1)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        ComponentJudgment(score, "fixture", origin="verifier" if score is None else None,
                                          cleanup=cleanup))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()),
                        source_reviewer_backend=Scripted(review_reply()))
    head, _ = options["run"].seal("synthesis")
    return options, result, head


def test_recheck_reuses_pair_then_reviews_and_admits_without_a_human(tmp_path, monkeypatch):
    original, first, head = stopped_pair(tmp_path / "first", monkeypatch)
    before = synthesis_billing(original["run"].objects, head)
    compared = []
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        (compared.append(True) or ComponentJudgment(1, "rechecked")))
    options = {**original, "plan": replace(original["plan"], run_id="recheck"),
               "reader": original["run"].objects, "run": JournalDirectory(tmp_path / "recheck")}
    reviewer = Scripted(review_reply())
    result = synthesize(**options, continuation_from=head, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and result.drafts == first.drafts
    assert compared == [True]
    assert result.phases[:2] == first.phases[:2]
    assert result.checks != first.checks
    package, policy = package_and_policy(options)
    store = options["run"].objects
    assert admit_package(package=package, policy=policy, reader=store).disposition.outcome == "include"
    latest, closure = options["run"].seal("synthesis")
    expected = CostLedger.combine((before, trajectory(options["run"].journal("revisions", "r0", "source_review")).billing))
    assert synthesis_billing(store, latest) == expected
    assert synthesis_costs(store, latest) == expected
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(store, closure)
    assert synthesis_billing(exported, latest) == expected
    assert synthesis_costs(exported, latest) == expected
    assert load_continuation(exported, head).draft == first.drafts[-1]
    assert synthesize(**{**options, "reader": Unavailable()}, continuation_from=head,
        author_backend=Scripted(), reader_backend=Scripted(), source_reviewer_backend=Scripted()) == result
    assert compared == [True]
    assert original["run"].seal("synthesis")[0] == head


def test_live_cost_projection_skips_all_request_and_raw_payloads(tmp_path, monkeypatch):
    from behave.agent.journal import load_chain
    from behave.core.records import thaw_json

    original, _, head = stopped_pair(tmp_path, monkeypatch)
    store = original["run"].objects
    expected = synthesis_billing(store, head)
    root = load_chain(store, head, verify_artifacts=False)
    from behave.data.synthesis_records import SynthesisResult
    result = SynthesisResult.from_bytes(store.read(BlobRef.from_dict(thaw_json(root[-1].payload)["result"])))
    forbidden = set()
    for phase in result.phases:
        if phase.role == "check":
            continue
        for event in load_chain(store, phase.head, verify_artifacts=False):
            if event.kind == "model_requested":
                forbidden.add(BlobRef.from_dict(thaw_json(event.payload)["request"]))
            elif event.kind == "model_settled":
                forbidden.add(BlobRef.from_dict(thaw_json(event.payload)["raw"]))

    class Metadata:
        def read(self, ref):
            assert ref not in forbidden, "live accounting reread a request/raw payload"
            return store.read(ref)

    assert synthesis_costs(Metadata(), head) == expected


@pytest.mark.parametrize("score,cleanup,code", [
    (1, CleanupState.COMPLETE, "continuation-not-supported"),
    (None, CleanupState.INCOMPLETE, "continuation-not-supported"),
])
def test_recheck_does_not_retry_scored_or_unquiescent_runs(tmp_path, monkeypatch, score, cleanup, code):
    original, _, head = stopped_pair(tmp_path, monkeypatch, score=score, cleanup=cleanup)
    with pytest.raises(RecordError) as failure:
        load_continuation(original["run"].objects, head)
    assert failure.value.code == code


def test_recheck_requires_a_new_identity_and_same_task_policy(tmp_path, monkeypatch):
    original, _, head = stopped_pair(tmp_path, monkeypatch)
    for plan, code in ((original["plan"], "continuation-run-id"),
                       (replace(original["plan"], run_id="retry", task_id="other"), "continuation-plan-mismatch")):
        with pytest.raises(RecordError) as failure:
            synthesize(**{**original, "plan": plan, "reader": original["run"].objects,
                "run": JournalDirectory(tmp_path / plan.run_id)}, continuation_from=head,
                author_backend=Scripted(), reader_backend=Scripted(), source_reviewer_backend=Scripted())
        assert failure.value.code == code


def test_repeated_no_result_keeps_pair_and_costs_without_auto_retry(tmp_path, monkeypatch):
    original, first, head = stopped_pair(tmp_path / "first", monkeypatch)
    before = synthesis_billing(original["run"].objects, head)
    second = {**original, "plan": replace(original["plan"], run_id="second"),
              "reader": original["run"].objects, "run": JournalDirectory(tmp_path / "second")}
    again = synthesize(**second, continuation_from=head, author_backend=Scripted(), reader_backend=Scripted(),
                       source_reviewer_backend=Scripted())
    assert again.status == "check_unavailable" and again.drafts == first.drafts
    second_head, _ = second["run"].seal("synthesis")
    assert synthesis_billing(second["run"].objects, second_head) == before
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: ComponentJudgment(1, "rechecked"))
    third = {**second, "plan": replace(second["plan"], run_id="third"),
             "reader": second["run"].objects, "run": JournalDirectory(tmp_path / "third")}
    result = synthesize(**third, continuation_from=second_head, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=Scripted(review_reply()))
    assert result.status == "review_ready" and result.drafts == first.drafts
    package, policy = package_and_policy(third)
    assert admit_package(package=package, policy=policy, reader=third["run"].objects).disposition.outcome == "include"


def test_continued_mismatch_returns_to_reviewer_and_preserves_blind_rebuild(tmp_path, monkeypatch):
    original, first, head = stopped_pair(tmp_path / "first", monkeypatch)
    outcomes = iter((0, 1))
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: ComponentJudgment(next(outcomes), "fixture"))
    options = {**original, "plan": replace(original["plan"], run_id="repair", max_revisions=2),
               "reader": original["run"].objects, "run": JournalDirectory(tmp_path / "repair")}
    _, _, submit = prepared(tmp_path / "fixture")
    author = Scripted(shell_call('write', {"path": "spec.md", "content": specification("Eight-bit identity; reset clears state.")}),
                      call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []}))
    reader = Scripted(shell_call('list', {}), *reader_replies())
    reviewer = Scripted(review_reply("unclear", builder_feedback="Clarify reset.",
                                    implementer_feedback="OLD_REPAIR_MUST_NOT_LEAK"), review_reply())
    result = synthesize(**options, continuation_from=head, author_backend=author, reader_backend=reader,
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and result.drafts[0] == first.drafts[0]
    assert len(result.drafts) == 2 and len(author.requests) == 2
    assert "OLD_REPAIR_MUST_NOT_LEAK" not in repr(reader.requests)
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == "include"


@pytest.mark.parametrize("recipient", ["builder", "implementer"])
def test_exhausted_repair_continues_from_saved_draft_and_feedback(tmp_path, monkeypatch, recipient):
    original, first_author, submit = prepared(tmp_path / "first", revisions=1)
    comparisons = []
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        (comparisons.append(True) or ComponentJudgment(1, "fixture")))
    feedback = "Clarify reset." if recipient == "builder" else "Correct your reset implementation."
    first = synthesize(**original, author_backend=Scripted(*first_author),
        reader_backend=Scripted(shell_call("write", {"path": "own_test.py", "content": "PRIVATE_B2_TEST"}),
                               *reader_replies()),
        source_reviewer_backend=Scripted(review_reply("unclear", **{recipient + "_feedback": feedback})))
    assert first.status == "revision_limit"
    head, _ = original["run"].seal("synthesis")
    before = synthesis_billing(original["run"].objects, head)
    options = {**original, "plan": replace(original["plan"], run_id="saved-repair", max_revisions=2),
               "reader": original["run"].objects, "run": JournalDirectory(tmp_path / "continued")}
    author = Scripted(*([] if recipient == "implementer" else [
        shell_call("write", {"path": "spec.md", "content": specification("Identity; reset clears state.")}),
        call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})]))
    reader = Scripted(shell_call("list", {}) if recipient == "builder" else
                      shell_call("read", {"path": "own_test.py"}), *reader_replies())
    result = synthesize(**options, continuation_from=head, author_backend=author, reader_backend=reader,
                        source_reviewer_backend=Scripted(review_reply()))
    assert result.status == "review_ready"
    assert result.phases[:len(first.phases)] == first.phases
    assert comparisons == [True, True]  # One old check, one check of the repaired pair.
    if recipient == "builder":
        assert len(author.requests) == 2 and feedback in repr(author.requests[0].messages)
        assert result.drafts[0] != result.drafts[1]
        assert "PRIVATE_B2_TEST" not in repr(reader.requests)
        assert "own_test.py" not in repr(reader.requests)
    else:
        assert not author.requests and result.drafts[0] == result.drafts[1]
        assert feedback in repr(reader.requests[0].messages)
        assert "PRIVATE_B2_TEST" in repr(reader.requests[1].messages)
    package, policy = package_and_policy(options)
    store = options["run"].objects
    assert admit_package(package=package, policy=policy, reader=store).disposition.outcome == "include"
    latest, closure = options["run"].seal("synthesis")
    expected = CostLedger.combine((before, *(trajectory(options["run"].journal("revisions", "r1", role)).billing
        for role in (("reader", "source_review") if recipient == "implementer" else
                     ("author", "reader", "source_review")))))
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(store, closure)
    assert synthesis_billing(exported, latest) == expected
    assert synthesize(**{**options, "reader": Unavailable()}, continuation_from=head,
        author_backend=Scripted(), reader_backend=Scripted(), source_reviewer_backend=Scripted()) == result
    assert comparisons == [True, True]
    assert original["run"].seal("synthesis")[0] == head


def test_exhausted_repair_requires_explicit_runtime_budget_extension(tmp_path, monkeypatch):
    from behave.data.continuation import validate_plan

    options, author, _ = prepared(tmp_path, revisions=1)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: ComponentJudgment(1, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()),
        source_reviewer_backend=Scripted(review_reply("unclear", builder_feedback="Clarify reset.")))
    assert result.status == "revision_limit"
    head, _ = options["run"].seal("synthesis")
    with pytest.raises(RecordError) as failure:
        validate_plan(load_continuation(options["run"].objects, head), replace(options["plan"], run_id="retry"))
    assert failure.value.code == "continuation-budget"


@pytest.mark.parametrize("recipient", ["builder", "implementer"])
def test_legacy_quote_only_feedback_returns_to_reviewer_not_an_author(tmp_path, monkeypatch, recipient):
    from behave.data.source import SourceReview

    original, author, _ = prepared(tmp_path / "first", revisions=1)
    comparisons = []
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        (comparisons.append(True) or ComponentJudgment(1, "fixture")))
    # Produce historical-format evidence under the old submission behavior.
    with monkeypatch.context() as legacy:
        legacy.setattr(SourceReview, "quoted_feedback_field", property(lambda self: None))
        first = synthesize(**original, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()),
            source_reviewer_backend=Scripted(review_reply(**{recipient + "_feedback": '""'})))
    assert first.status == "revision_limit"
    head, _ = original["run"].seal("synthesis")
    resumed = {**original, "plan": replace(original["plan"], run_id="review-format-correction"),
               "reader": original["run"].objects, "run": JournalDirectory(tmp_path / "continued")}
    reviewer = Scripted(review_reply())
    result = synthesize(**resumed, continuation_from=head, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(reviewer.requests) == 1
    assert result.drafts == first.drafts and result.checks == first.checks and comparisons == [True]
    package, policy = package_and_policy(resumed)
    assert admit_package(package=package, policy=policy, reader=resumed["run"].objects).disposition.outcome == "include"
    assert original["run"].seal("synthesis")[0] == head


@pytest.mark.parametrize("score", [0, 1])
def test_unresolved_review_reuses_pair_and_check_without_automatic_acceptance(tmp_path, monkeypatch, score):
    original, author, _ = prepared(tmp_path / "first", revisions=1)
    compared = []
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        (compared.append(True) or ComponentJudgment(score, "fixture")))
    first = synthesize(**original, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()),
                      source_reviewer_backend=Scripted(review_reply("unclear")))
    assert first.code == "review-unresolved-without-repair"
    head, _ = original["run"].seal("synthesis")
    options = {**original, "plan": replace(original["plan"], run_id="review-again"),
               "reader": original["run"].objects, "run": JournalDirectory(tmp_path / "continued")}
    result = synthesize(**options, continuation_from=head, author_backend=Scripted(), reader_backend=Scripted(),
                        source_reviewer_backend=Scripted(review_reply()))
    assert result.drafts == first.drafts and result.checks == first.checks
    assert result.phases[:3] == first.phases[:3] and compared == [True]
    assert result.status == ("review_ready" if score else "unfinished")
    package, policy = package_and_policy(options)
    assert admit_package(package=package, policy=policy, reader=options["run"].objects).disposition.outcome == (
        "include" if score else "pending")
    assert original["run"].seal("synthesis")[0] == head


@pytest.mark.isolation
@pytest.mark.eda
def test_process_continuation_rechecks_with_public_sim_without_restarting_authors(tmp_path, monkeypatch, capsys):
    from test_synthesis_process import fixed

    from behave.data import synthesis as synthesis_module
    from behave.data.__main__ import main
    from behave.data.synthesis import SynthesisConfiguration
    from behave.data.synthesis_records import SynthesisResult

    options, author, _ = prepared(tmp_path / "first", real=True)
    fixed(options, [author], [reader_replies()], [[review_reply()]])
    compare = checks.compare_sources
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        ComponentJudgment(None, "runtime-fixture", origin="verifier"))
    first = synthesize(**options)
    head, _ = options["run"].seal("synthesis")
    monkeypatch.setattr(checks, "compare_sources", compare)
    execute = synthesis_module.execute_role
    called = []
    def reviewer_only(job, **kwargs):
        called.append(job.inputs.role)
        assert job.inputs.role == "source_reviewer"
        return execute(job, **kwargs)
    monkeypatch.setattr(synthesis_module, "execute_role", reviewer_only)
    resumed = {**options, "reader": options["run"].objects,
               "plan": replace(options["plan"], run_id="public-recheck"),
               "run": JournalDirectory(tmp_path / "continued")}
    configuration = tmp_path / "continuation.json"
    configuration.write_bytes(SynthesisConfiguration(resumed["plan"]).to_bytes())
    reference = tmp_path / "synthesis_head.json"
    reference.write_bytes(canonical_json(head.as_dict()))
    arguments = ["--stage", "synthesis", "--configuration", str(configuration),
                 "--source-objects", str(options["run"].objects.directory),
                 "--run-directory", str(tmp_path / "continued"), "--continue-from", str(reference)]
    assert main([*arguments, "--dry-run"]) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "validated"
    assert called == [] and not (tmp_path / "continued").exists()
    assert main(arguments) == 0
    output = parse_json(capsys.readouterr().out.encode())
    assert output["status"] == "review_ready"
    result = SynthesisResult.from_bytes(resumed["run"].objects.read(BlobRef.from_dict(output["result"])))
    assert result.status == "review_ready" and result.drafts == first.drafts
    assert called == ["source_reviewer"]
    package, policy = package_and_policy(resumed)
    assert admit_package(package=package, policy=policy, reader=resumed["run"].objects).disposition.outcome == "include"
    assert synthesize(**{**resumed, "reader": Unavailable()}, continuation_from=head) == result
    assert called == ["source_reviewer"]


@pytest.mark.isolation
@pytest.mark.parametrize("recipient", ["builder", "implementer"])
def test_process_repair_continuation_only_launches_new_revision(tmp_path, monkeypatch, recipient):
    from test_synthesis_process import fixed

    from behave.data import synthesis as synthesis_module

    options, author, submit = prepared(tmp_path / "first", revisions=1)
    repair = [shell_call("write", {"path": "spec.md", "content": specification("Identity; reset clears state.")}),
              call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})]
    fixed(options, [author, repair], [reader_replies(), reader_replies()],
          [[review_reply("unclear", **{recipient + "_feedback": "Clarify reset."})], [review_reply()]])
    compared = []
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        (compared.append(True) or ComponentJudgment(1, "fixture")))
    first = synthesize(**options)
    assert first.status == "revision_limit"
    head, _ = options["run"].seal("synthesis")
    called = []
    execute = synthesis_module.execute_role

    def new_revision_only(job, **kwargs):
        called.append((kwargs["revision"], kwargs["role"]))
        assert kwargs["revision"] == 1
        return execute(job, **kwargs)

    monkeypatch.setattr(synthesis_module, "execute_role", new_revision_only)
    resumed = {**options, "reader": options["run"].objects,
               "plan": replace(options["plan"], run_id="continued-repair", max_revisions=2),
               "run": JournalDirectory(tmp_path / "continued")}
    result = synthesize(**resumed, continuation_from=head)
    assert result.status == "review_ready" and compared == [True, True]
    assert called == ([(1, "author")] if recipient == "builder" else []) + [
        (1, "reader"), (1, "source_review")]
    assert result.phases[:len(first.phases)] == first.phases
    package, policy = package_and_policy(resumed)
    assert admit_package(package=package, policy=policy, reader=resumed["run"].objects).disposition.outcome == "include"
    assert synthesize(**{**resumed, "reader": Unavailable()}, continuation_from=head) == result
    assert resumed["run"].seal("synthesis")[0] != head
    assert options["run"].seal("synthesis")[0] == head


@pytest.mark.isolation
def test_rtl_findings_return_to_reviewer_then_builder_and_fresh_b2(tmp_path, monkeypatch):
    from test_rtl_construction import rtl_policy, rtl_replies
    from test_synthesis_process import fixed

    from behave.data.rtl import construction_status

    options, author, submit = prepared(tmp_path / "first")
    repair = [shell_call('write', {"path": "spec.md", "content": specification("Identity operation. Reset clears state.")}),
              call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})]
    review_problem = [shell_call('read', {"path": "review/rtl_findings.json"}),
                      review_reply("unclear", builder_feedback="Clarify reset in the spec.",
                                   implementer_feedback="OLD_READER_HINT")]
    fixed(options, [author, repair], [reader_replies(), reader_replies()],
          [[review_reply()], review_problem, [review_reply()]], guards={
              "source_review": "if request.call_id.startswith('rtl-followup:'): revision = str(int(revision) + 1)",
              "reader": "assert b'RTL_SPEC_CLAIM' not in canonical_json(list(request.messages))\n"
                        "assert b'OLD_READER_HINT' not in canonical_json(list(request.messages))"})
    rtl = rtl_policy([[call("submit_rtl", {"rtl": None, "findings": ["RTL_SPEC_CLAIM: reset is unclear"]})],
                      rtl_replies()], revisions=1,
                     guard="if request.call_id.startswith('rtl-followup:'): revision = '1'")
    options["plan"] = replace(options["plan"], rtl=rtl)
    compared = []
    def compare(inputs, **kwargs):
        compared.append(inputs.component)
        return ComponentJudgment(1, "fixture")
    monkeypatch.setattr(checks, "compare_sources", compare)
    first = synthesize(**options)
    assert construction_status(options["run"].objects, first)[0] == "rtl_review_requested"
    head, _ = options["run"].seal("synthesis")
    resumed = {**options, "reader": options["run"].objects,
               "plan": replace(options["plan"], run_id="rtl-followup"),
               "run": JournalDirectory(tmp_path / "continued")}
    result = synthesize(**resumed, continuation_from=head)
    assert result.status == "review_ready" and len(result.drafts) == 2
    assert compared == ["behavior", "behavior", "rtl"]
    assert result.phases[:3] == first.phases[:3]
    assert construction_status(resumed["run"].objects, result)[0] == "review_ready"
    package, policy = package_and_policy(resumed)
    assert admit_package(package=package, policy=policy, reader=resumed["run"].objects).disposition.outcome == "include"
    newest, closure = resumed["run"].seal("synthesis")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(resumed["run"].objects, closure)
    assert synthesis_billing(exported, newest) == synthesis_billing(resumed["run"].objects, newest)
    assert synthesize(**{**resumed, "reader": Unavailable()}, continuation_from=head) == result
