"""Syn mounts shared feedback without rerunning comparisons or widening roles."""

from dataclasses import replace

import pytest
from syn_shell import shell_call
from test_attempts import Scripted, call
from test_release_admission import assessment
from test_role_process import channels, job_for
from test_synthesis import reader_replies, setup
from test_synthesis_source_review import prepared, review_reply

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import FileRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.data.construction import reader_inputs
from behave.data.repair_evidence import repair_files
from behave.data.role import AuthorInputs, SourceReviewInputs, input_closure, run_role_process
from behave.data.source import review_check, review_inputs
from behave.data.synthesis import synthesize
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.judgment import ComponentJudgment


def diagnostic_result(store, score=0):
    documents = {
        "summary.json": {"run_count": 1, "complete": True, "affected_runs": int(score == 0),
            "runs": [] if score != 0 else [{"index": 0,
                "stimulus": "runs/0/stimulus.json", "observations": "runs/0/observations.json"}]},
        "runs/0/stimulus.json": {"input": 7, "note": "PUBLIC_STIMULUS"},
        "runs/0/observations.json": {"comparison": {"fault_count": 1, "faults": [
            {"kind": "value", "position": 0, "expected": [7], "observed": [9]}]}},
    }
    # Named files deliberately occur only in diagnostics, never evidence.
    return ComponentJudgment(score, "fixture", origin="reference" if score is None else None,
        retryable=score is None, evidence=(store.put(b"retained legacy evidence"),),
        diagnostics=tuple(FileRef(path, store.put(canonical_json(value))) for path, value in documents.items()))


def reviewed_task(tmp_path, monkeypatch, score=0):
    options, first, _ = prepared(tmp_path, revisions=1)
    outcomes = []

    def compare(*args, objects, **kwargs):
        result = diagnostic_result(objects, score)
        outcomes.append(result)
        return result

    monkeypatch.setattr(checks, "compare_sources", compare)
    result = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=Scripted(review_reply(
            "supported" if score == 1 else "unclear")))
    assert len(outcomes) == 1
    return options, result, outcomes[0]


@pytest.mark.parametrize("score", [1, 0, None])
def test_review_feedback_matches_shared_export_and_preserves_disposition(tmp_path, monkeypatch, score):
    options, result, judgment = reviewed_task(tmp_path, monkeypatch, score)
    assert result.status == {1: "review_ready", 0: "unfinished", None: "unfinished"}[score]
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: pytest.fail("feedback re-executed Sim"))
    store, plan = options["run"].objects, options["plan"]
    _, _, mounted = review_inputs(store, result.drafts[0], plan.source_review.guidance, check=result.checks[0])
    reply, exported = comparison_feedback(judgment, store, prefix="review/comparison")
    assert all(file in mounted for file in exported)
    assert reply["matched"] is (None if score is None else score == 1)
    assert not any(file.reference.path.endswith("outcome.json") for file in mounted)
    # Merely storing diagnostics does not give the blind reader their files.
    _, blind = reader_inputs(store, result.drafts[0])
    assert {file.reference.path for file in blind} == {"spec.md", "contract.json",
        ".construction/sim_check.md", ".construction/behavior_environment.json"}


@pytest.mark.parametrize("path", ["review/comparison/report.md", "review/comparison/summary.json",
    "review/comparison/runs/0/observations.json", "review/replay_stimuli.json"])
def test_reviewer_can_cite_supplied_feedback_through_publication(tmp_path, monkeypatch, path):
    from test_synthesis_source_review import package_and_policy

    from behave.data.admission import admit_package

    options, first, _ = prepared(tmp_path, revisions=1)

    def compare(*args, objects, **kwargs):
        replay = objects.put(canonical_json({"stimuli": []}))
        return replace(diagnostic_result(objects, score=1), detail={"replay_pool": replay.as_dict()})

    monkeypatch.setattr(checks, "compare_sources", compare)
    cited = assessment()
    cited["citations"].append({"path": path, "first": 1, "last": 1})
    reviewer = Scripted(call("submit_source_review", {"assessments": [cited], "findings": []}))
    result = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(reviewer.requests) == 1
    package, policy = package_and_policy(options)
    admitted = admit_package(package=package, policy=policy, reader=options["run"].objects)
    assert admitted.disposition.outcome == "include"


@pytest.mark.parametrize("missing", ["source", "draft"])
def test_feedback_citation_does_not_replace_source_or_draft(tmp_path, monkeypatch, missing):
    options, first, _ = prepared(tmp_path, revisions=1)
    monkeypatch.setattr(checks, "compare_sources",
        lambda *a, objects, **kw: diagnostic_result(objects, score=1))
    cited = assessment()
    cited["citations"] = [c for c in cited["citations"] if not c["path"].startswith(missing + "/")]
    cited["citations"].append({"path": "review/comparison/report.md", "first": 1, "last": 1})
    reviewer = Scripted(call("submit_source_review", {"assessments": [cited], "findings": []}),
        review_reply())
    result = synthesize(**options, author_backend=Scripted(*first),
        reader_backend=Scripted(*reader_replies()), source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(reviewer.requests) == 2
    feedback = reviewer.requests[1].messages[-2]["content"]
    assert feedback["error"] == "review-citation-incomplete"
    assert f"missing citations under {missing}/" in feedback["detail"]


def test_reviewer_gets_recorded_replay_pool_after_pass_without_rechecking(tmp_path, monkeypatch):
    from behave.sim import api

    options, first, _ = prepared(tmp_path, revisions=1)
    stimulus = api.Stimulus({"input": [(7,)]}, [api.EventSlot(offers=("input",))],
                            source="PUBLIC_REPLAY_POOL")
    payload = canonical_json({"stimuli": [stimulus.as_dict()]})
    comparisons = []

    def compare(*args, objects, **kwargs):
        ref = objects.put(payload)
        comparisons.append(ref)
        return ComponentJudgment(1, "fixture", evidence=(ref,), detail={"replay_pool": ref.as_dict()})

    monkeypatch.setattr(checks, "compare_sources", compare)
    reviewer = Scripted(shell_call("read", {"path": "review/replay_stimuli.json"}),
        shell_call("write", {"path": "review/replay_stimuli.json", "content": "overwrite"}),
        review_reply())
    reader = Scripted(*reader_replies())
    result = synthesize(**options, author_backend=Scripted(*first), reader_backend=reader,
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and len(comparisons) == 1
    assert "PUBLIC_REPLAY_POOL" in repr(reviewer.requests[-1].messages)
    assert "workspace-read-only-changed" in repr(reviewer.requests[-1].messages)
    assert "PUBLIC_REPLAY_POOL" not in repr(reader.requests)
    store, plan = options["run"].objects, options["plan"]
    inputs = SourceReviewInputs(result.drafts[0], plan.source_review.guidance, result.checks[0])
    closure = input_closure(job_for(inputs, []), store)
    assert comparisons[0] in closure
    cold = DirectoryStore(tmp_path / "cold")
    cold.publish(store, closure)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw: pytest.fail("re-executed Sim"))
    _, _, files = review_inputs(cold, inputs.draft, inputs.guidance, check=inputs.check)
    mounted = {file.reference.path: file.data for file in files}
    assert mounted["review/replay_stimuli.json"] == payload
    assert api.Stimulus.from_dict(parse_json(payload)["stimuli"][0]) == stimulus
    assert b"review/replay_stimuli.json" in mounted["review/sim_check.md"]
    _, blind = reader_inputs(cold, inputs.draft)
    assert all(file.reference.path != "review/replay_stimuli.json" for file in blind)


@pytest.mark.parametrize("role", ["reviewer", "builder"])
def test_named_only_dependencies_survive_role_store_transfer(tmp_path, monkeypatch, role):
    options, result, judgment = reviewed_task(tmp_path, monkeypatch)
    store, plan = options["run"].objects, options["plan"]
    if role == "reviewer":
        inputs = SourceReviewInputs(result.drafts[0], plan.source_review.guidance, result.checks[0])
    else:
        inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy,
                              parent=result.drafts[0], check=result.checks[0])
    closure = input_closure(job_for(inputs, []), store)
    assert all(file.content in closure for file in judgment.diagnostics)
    assert all(file.content not in judgment.evidence for file in judgment.diagnostics)
    fresh = DirectoryStore(tmp_path / "fresh")
    fresh.publish(store, closure)
    if role == "reviewer":
        _, _, files = review_inputs(fresh, inputs.draft, inputs.guidance, check=inputs.check)
        prefix = "review/comparison"
    else:
        files = repair_files(inputs, fresh)
        prefix = ".construction/comparison"
    _, expected = comparison_feedback(judgment, fresh, prefix=prefix)
    assert all(file in files for file in expected)


def test_direct_builder_repair_reads_report_without_rescoring_and_keeps_reader_blind(tmp_path, monkeypatch):
    options, first, submit = setup(tmp_path, revisions=2)
    calls = []

    def compare(*args, objects, **kwargs):
        calls.append(True)
        return diagnostic_result(objects, 0 if len(calls) == 1 else 1)

    monkeypatch.setattr(checks, "compare_sources", compare)
    author = Scripted(*first, shell_call("read", {"path": ".construction/comparison/report.md"}),
        shell_call("read", {"path": ".construction/comparison/runs/0/stimulus.json"}),
        call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []}))
    reader = Scripted(*reader_replies(), shell_call("list", {}), *reader_replies())
    result = synthesize(**options, author_backend=author, reader_backend=reader)
    assert result.status == "review_ready" and len(calls) == 2
    assert "expected [7], observed [9]" in repr(author.requests[-1].messages)
    assert "PUBLIC_STIMULUS" in repr(author.requests[-1].messages)
    assert "PUBLIC_STIMULUS" not in repr(reader.requests)
    names = parse_json(reader.requests[3].messages[-2]["content"]["stdout"].encode())["files"]
    assert {file["path"] for file in names} == {"spec.md", "contract.json",
        ".construction/sim_check.md", ".construction/behavior_environment.json"}


def test_reviewer_mediation_does_not_forward_comparison_package(tmp_path, monkeypatch):
    options, first, submit = prepared(tmp_path, revisions=2)
    scores = iter((0, 1))
    monkeypatch.setattr(checks, "compare_sources", lambda *a, objects, **kw:
                        diagnostic_result(objects, next(scores)))
    author = Scripted(*first, shell_call("list", {}),
        call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []}))
    reader = Scripted(*reader_replies(), shell_call("list", {}), *reader_replies())
    reviewer = Scripted(review_reply("unclear", builder_feedback="Clarify the output semantics."), review_reply())
    result = synthesize(**options, author_backend=author, reader_backend=reader, source_reviewer_backend=reviewer)
    assert result.status == "review_ready" and result.drafts[0] != result.drafts[1]
    for requests in (author.requests, reader.requests):
        assert "PUBLIC_STIMULUS" not in repr(requests)
    names = parse_json(author.requests[-1].messages[-2]["content"]["stdout"].encode())["files"]
    assert not any("comparison/" in file["path"] or ".construction/check/" in file["path"] for file in names)


def test_new_builder_repair_drops_previous_diagnostic_files_not_user_scratch(tmp_path, monkeypatch):
    options, first, submit = setup(tmp_path, revisions=3)
    checked = []

    def compare(*args, objects, **kwargs):
        checked.append(True)
        return (diagnostic_result(objects) if len(checked) == 1 else
                ComponentJudgment(int(len(checked) == 3), "legacy-without-diagnostics"))

    monkeypatch.setattr(checks, "compare_sources", compare)
    submit_reply = call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})
    author = Scripted(*first, submit_reply, shell_call("list", {}),
                      shell_call("read", {"path": ".construction/comparison/report.md"}), submit_reply)
    result = synthesize(**options, author_backend=author,
                        reader_backend=Scripted(*reader_replies(), *reader_replies(), *reader_replies()))
    assert result.status == "review_ready" and len(checked) == 3
    files = parse_json(author.requests[-2].messages[-2]["content"]["stdout"].encode())["files"]
    names = {file["path"] for file in files}
    assert "scratch.txt" in names and ".construction/comparison/report.md" in names
    assert not any(name.startswith(".construction/comparison/runs/") for name in names)
    assert "No detailed diagnostics were recorded" in repr(author.requests[-1].messages[-2])


@pytest.mark.isolation
@pytest.mark.parametrize("role", ["reviewer", "builder"])
def test_fresh_role_worker_reads_report_and_attachments_for_whole_attempt(tmp_path, monkeypatch, role):
    options, result, _ = reviewed_task(tmp_path, monkeypatch)
    store, plan = options["run"].objects, options["plan"]
    if role == "reviewer":
        check, _, _ = review_check(store, result.drafts[0], result.checks[0])
        inputs = SourceReviewInputs(result.drafts[0], plan.source_review.guidance,
                                   result.checks[0], reconstruction=check.reconstruction)
        prefix, submit = "review/comparison", review_reply("unclear", ("Resolve mismatch.",),
                                                          builder_feedback="Resolve mismatch in B1.")
    else:
        inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy,
                              parent=result.drafts[0], check=result.checks[0])
        prefix = ".construction/comparison"
        submit = call("submit_draft", {"draft": {"adaptations": None, "spec": "spec.md",
            "contract": "contract.json", "behavior": {"files": ["behavior.py"],
                "entry_file": "behavior.py", "entry_symbol": "Identity"},
            "run_policy": "generation.json", "public_files": [], "rtl": None}, "scope_findings": []})
    replies = [shell_call("read", {"path": prefix + "/report.md"}),
        shell_call("read", {"path": prefix + "/runs/0/stimulus.json"}),
        shell_call("write", {"path": "scratch/review.txt", "content": "still in this attempt"}),
        shell_call("read", {"path": prefix + "/runs/0/observations.json"}), submit]
    job = job_for(inputs, replies, guard="""
if request.turn == 1:
    assert 'expected [7], observed [9]' in repr(request.messages[-2])
if request.turn == 2:
    assert 'PUBLIC_STIMULUS' in repr(request.messages[-2])
if request.turn == 4:
    assert 'fault_count' in repr(request.messages[-2])
""")
    fresh = DirectoryStore(tmp_path / "worker_inputs")
    fresh.publish(store, input_closure(job, store))
    run = JournalDirectory(tmp_path / "worker")
    outcome = run_role_process(job, reader=fresh, **channels(run, "role"))
    assert outcome.reason == "final_action"
    assert run.journal("role_supervision").load()[-1].payload["cleanup"] == "complete"


@pytest.mark.isolation
def test_rtl_repair_worker_reads_named_only_diagnostics(tmp_path, monkeypatch):
    from test_rtl_construction import auxiliary, rtl_replies, setup_rtl

    repair = [shell_call("read", {"path": ".rtl/comparison/report.md"}),
              shell_call("read", {"path": ".rtl/comparison/runs/0/stimulus.json"}), *rtl_replies()]
    options = setup_rtl(tmp_path, [rtl_replies(), repair], guard="""
if revision == '1' and request.turn == 1:
    assert 'expected [7], observed [9]' in repr(request.messages[-2])
if revision == '1' and request.turn == 2:
    assert 'PUBLIC_STIMULUS' in repr(request.messages[-2])
""")
    checked = []

    def compare(inputs, *, objects, **kwargs):
        if inputs.component == "behavior":
            return ComponentJudgment(1, "fixture")
        checked.append(True)
        return diagnostic_result(objects, 0 if len(checked) == 1 else 1)

    monkeypatch.setattr(checks, "compare_sources", compare)
    result = synthesize(**options)
    built, _, rtl = auxiliary(options, result)
    assert result.status == "review_ready" and built.status == "passed" and rtl is not None
    assert len(checked) == 2
