"""Revision orchestration uses real role tools and immutable phase journals."""

from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import Scripted, call
from test_construction import seed, selected, specification
from test_judge import BEHAVIOR, REFERENCE, real_task

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.lifetime import AttemptControl
from behave.agent.model import Charge, ModelReply
from behave.agent.run_store import JournalDirectory
from behave.agent.trajectory import trajectory
from behave.agent.workspace import Workspace
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks, synthesis as synthesis_module
from behave.data.drafts import load_draft
from behave.data.reviews import DraftCheck
from behave.data.evidence import evidence_page
from behave.data.synthesis import synthesize
from behave.data.synthesis_records import SynthesisPlan, SynthesisResult
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment


def setup(tmp_path, *, revisions=3, real=False):
    store, _, _, scope = seed(tmp_path / "inputs")
    policy = JudgePolicy("fixture", "fixture", search_seconds=0).to_bytes()
    contract, generation = b"{}", b"{}"
    if real:
        task, _, _ = real_task(tmp_path / "fixture")
        policy, contract, generation = task.judge_policy, task.contract.data, task.run_policy
    plan = SynthesisPlan("construction", "identity", scope, store.put(policy),
        AttemptSpec("author", "task_author", {"model": "fixture"}),
        AttemptSpec("reader", "spec_reader", {"model": "fixture"}), revisions)
    submit = {"spec": "spec.md", "contract": "contract.json", "behavior": selected("behavior.py", "Identity"),
              "run_policy": "generation.json", "public_files": [], "rtl": None}
    bodies = {"spec.md": specification("An eight-bit identity operation."), "contract.json": contract.decode(),
              "generation.json": generation.decode(), "behavior.py": REFERENCE.decode(),
              "scratch.txt": "retain across revisions"}
    first = [shell_call('write', {"path": name, "content": body}, str(i)) for i, (name, body) in enumerate(bodies.items())]
    first.append(call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []}))
    return {"plan": plan, "reader": store, "run": JournalDirectory(tmp_path / "run"),
            "development_tools": (fixture_shell(),)}, first, submit


def reader_replies(*, findings=()):
    return [shell_call('write', {"path": "reader.py", "content": BEHAVIOR.decode()}),
            call("submit_reconstruction", {"behavior": selected("reader.py", "M"), "findings": list(findings)})]


def test_pass_yields_review_package_not_publication_and_replays_cold(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path)
    calls = []
    def compare(*args, **kwargs):
        calls.append(True)
        return ComponentJudgment(1, "fixture")
    monkeypatch.setattr(checks, "compare_sources", compare)
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    assert result.status == "review_ready" and len(result.drafts) == len(result.checks) == 1
    assert [p.role for p in result.phases] == ["author", "reader", "check"]
    assert SynthesisResult.from_bytes(result.to_bytes()) == result
    assert SynthesisPlan.from_bytes(options["plan"].to_bytes()) == options["plan"]
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted()) == result
    assert calls == [True]
    assert not (options["run"].directory / "release").exists()
    with pytest.raises(RecordError, match="replay differs"):
        synthesize(**{**options, "plan": replace(options["plan"], max_revisions=4)},
                   author_backend=Scripted(), reader_backend=Scripted())


@pytest.mark.parametrize("score,findings", [(0, ()), (1, ("Output name is ambiguous",))])
def test_repair_preserves_history_scope_and_costs(tmp_path, monkeypatch, score, findings):
    options, first, submit = setup(tmp_path)
    evidence = options["run"].objects.put(b'{"input": 7, "reference": 7, "candidate": 0}')
    outcomes = iter([ComponentJudgment(score, "fixture", evidence=(evidence,)), ComponentJudgment(1, "fixture")])
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: next(outcomes))
    repair = [shell_call('read', {'path': '.construction/check/1.blob'}),
              shell_call('read', {"path": "scratch.txt"}),
              shell_call('write', {"path": "spec.md", "content": specification("Clarified eight-bit identity output.")}),
              call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})]
    author = Scripted(*(replace(r, charge=Charge("0.01", "USD", "fixture")) for r in (*first, *repair)))
    reader = Scripted(*reader_replies(findings=findings), *reader_replies())
    result = synthesize(**options, author_backend=author, reader_backend=reader)
    assert result.status == "review_ready" and len(result.drafts) == 2
    first_draft, last_draft = (load_draft(options["run"].objects, ref).record for ref in result.drafts)
    assert last_draft.parent == result.drafts[0] and first_draft.scope == last_draft.scope
    assert last_draft.task.spec.content != first_draft.task.spec.content
    assert last_draft.task.reference_behavior == first_draft.task.reference_behavior
    assert "retain across revisions" in repr(author.requests[-1].messages)
    assert '"candidate": 0' in repr(author.requests[-1].messages)
    assert "does not establish which implementation is wrong" in repr(author.requests[len(first)].messages)
    phase_costs = [trajectory(options["run"].journal("revisions", f"r{r}", "author")).cost_by_currency()["USD"]
                   for r in (0, 1)]
    assert phase_costs == ["0.06", "0.04"]
    # Sealed closure can move to a fresh store without losing parent scope/files.
    _, refs = options["run"].seal("synthesis")
    fresh = DirectoryStore(tmp_path / "export")
    fresh.publish(options["run"].objects, refs)
    assert load_draft(fresh, result.drafts[-1]).record == last_draft


def test_infrastructure_unavailable_stops_without_semantic_retry(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(
        None, "worker-unavailable", "infrastructure", retryable=True))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    assert result.status == "check_unavailable" and result.code == "worker-unavailable"
    assert len(result.drafts) == 1
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted()) == result


@pytest.mark.parametrize("score", [0, 1])
def test_unconfirmed_cleanup_stops_and_keeps_the_decided_score(tmp_path, monkeypatch, score):
    options, author, _ = setup(tmp_path)
    policy = AdmissionPolicy("checks", 1)
    options["check_admission"] = ResourcePool(policy)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(
        score, "fixture", cleanup=CleanupState.INCOMPLETE))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    assert result.status == "cleanup_unconfirmed" and len(result.drafts) == 1
    assert parse_json(options["run"].objects.read(result.checks[0]))["comparison"]["result"]["score"] == score
    options["check_admission"] = ResourcePool(policy)
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted()) == result
    with pytest.raises(AttemptInterrupted, match="cleanup-incomplete"), options["check_admission"].acquire():
        pytest.fail("terminal replay must retain the cleanup witness")


def test_enclosing_cancellation_reaches_comparison_admission(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path)
    cancelled = [False]
    options["control"] = AttemptControl(cancelled=lambda: cancelled[0])
    original = synthesis_module.run_reader
    def cancel(**kwargs):
        result = original(**kwargs)
        cancelled[0] = True
        return result
    monkeypatch.setattr(synthesis_module, "run_reader", cancel)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("cancelled; no check dispatch"))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    assert result.status == "check_unavailable" and result.code == "comparison-cancelled"
    assert "check_requested" not in [e.kind for e in options["run"].journal("revisions", "r0", "check").load()]


def test_revision_exhaustion_and_no_submission_are_not_scores(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path, revisions=1)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(0, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    assert result.status == "revision_limit" and not hasattr(result, "score")
    options, _, _ = setup(tmp_path / "unfinished")
    result = synthesize(**options, author_backend=Scripted(ModelReply("cannot finish")), reader_backend=Scripted())
    assert result.status == "unfinished" and result.code == "author-stopped" and not result.drafts


def test_crash_after_draft_commit_recovers_without_resampling_author(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path)
    class Crash(BaseException):
        pass
    original = synthesis_module.run_reader
    monkeypatch.setattr(synthesis_module, "run_reader", lambda **kw: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted())
    authored = options["run"].journal("revisions", "r0", "author").load()
    monkeypatch.setattr(synthesis_module, "run_reader", original)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options, author_backend=Scripted(), reader_backend=Scripted(*reader_replies()))
    assert result.status == "review_ready"
    assert options["run"].journal("revisions", "r0", "author").load() == authored


def test_check_commit_before_coordinator_crash_recovers_without_reexecution(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    original = synthesis_module.compare_reconstruction
    class Crash(BaseException):
        pass
    def crash(**kwargs):
        original(**kwargs)
        raise Crash()
    monkeypatch.setattr(synthesis_module, "compare_reconstruction", crash)
    with pytest.raises(Crash):
        synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    assert options["run"].journal("revisions", "r0", "check").load()[-1].kind == "check_finished"
    monkeypatch.setattr(synthesis_module, "compare_reconstruction", original)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("already committed check"))
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted()).status == "review_ready"


def test_archived_evidence_pages_remain_reproducible(tmp_path):
    options, _, _ = setup(tmp_path)
    store = options["reader"]
    body = ("Original evidence — " * 4000).encode()
    reference = store.put(body)
    offset, pieces = 0, []
    while offset is not None:
        row, payload = evidence_page(b"{}", (reference,), store, 1, offset)
        assert payload == body
        pieces.append(row["content"])
        offset = row["next_offset"]
    assert "".join(pieces).encode() == body


def test_unsettled_check_stays_unknown_and_does_not_relaunch(tmp_path, monkeypatch):
    options, author, _ = setup(tmp_path)
    policy = AdmissionPolicy("checks", 1)
    options["check_admission"] = ResourcePool(policy)
    class Crash(BaseException):
        pass
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("must not redispatch"))
    options["check_admission"] = ResourcePool(policy)
    result = synthesize(**options, author_backend=Scripted(), reader_backend=Scripted())
    assert result.status == "interrupted" and result.code == "attempt-outcome-unknown"
    with pytest.raises(AttemptInterrupted, match="cleanup-unknown"), options["check_admission"].acquire():
        pytest.fail("fresh pool must not erase an unknown check")
    options["check_admission"] = ResourcePool(policy)
    assert synthesize(**options, author_backend=Scripted(), reader_backend=Scripted()) == result
    with pytest.raises(AttemptInterrupted, match="cleanup-unknown"), options["check_admission"].acquire():
        pytest.fail("terminal replay must restore quarantine too")
    assert options["run"].journal("revisions", "r0", "check").load()[-1].kind == "check_requested"


def test_shared_local_owner_is_fail_fast_and_does_not_unlink(tmp_path):
    options, _, _ = setup(tmp_path)
    with options["run"].owner():
        inode = (options["run"].directory / "owner.lock").stat().st_ino
        with pytest.raises(AttemptInterrupted, match="run-already-owned"):
            synthesize(**options, author_backend=Scripted(), reader_backend=Scripted())
    with options["run"].owner():
        assert (options["run"].directory / "owner.lock").stat().st_ino == inode


@pytest.mark.isolation
def test_real_construction_revision_to_review_package(tmp_path):
    options, author, _ = setup(tmp_path, real=True)
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    assert result.status == "review_ready", result.to_bytes()
    check = parse_json(options["run"].objects.read(result.checks[-1]))
    assert check["comparison"]["result"]["score"] == 1
    assert check["comparison"]["result"]["analysis"] is None
