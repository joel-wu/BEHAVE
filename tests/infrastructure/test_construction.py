"""Draft/reader seams use real runtime tools, not a second chat or reward loop."""

from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import Scripted, call
from test_judge import BEHAVIOR, REFERENCE, real_task

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.model import ModelReply
from behave.agent.workspace import Workspace
from behave.core.artifacts import ArtifactSelection, BlobRef, FileRef, SourceArtifact
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data import checks
from behave.data.construction import run_author, run_reader
from behave.data.drafts import Reconstruction, ScopeProposal, SourceRecord, load_draft, load_scope
from behave.data.reviews import DraftCheck, compare_reconstruction
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment


def selected(path, symbol):
    return {"files": [path], "entry_file": path, "entry_symbol": symbol}


def specification(description="Return the eight-bit input value as one output transaction."):
    return ("# Identity\n\n" + description + "\n\n## Interface\n"
            "### RTL interface\nAn eight-bit input in_a and output out_y.\n"
            "```verilog\nmodule M(input clk, rst, in_valid, input [7:0] in_a, "
            "output in_ready, out_valid, input out_ready, output [7:0] out_y);\n```\n"
            "### Behavior interface\nIdentity() provides process(a), returning [(a,)].\n"
            "## Modules\nIdentity passes through its input.\n"
            "## Functional Behavior\n" + description + "\n"
            "## Reset Behavior\nReset clears transaction state.\n")


def seed(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    original = FileRef("original.py", store.put(b"# ORIGINAL_SECRET\ndef identity(a): return a\n"))
    source = SourceRecord("identity-source", "fixture:source", "r1", None,
                          SourceArtifact((original,), original.path, "identity"))
    source_ref = store.put(source.to_bytes())
    proposal = ScopeProposal(source_ref, "Unsigned identity transaction", ("Preserve all eight input bits",),
                             ("Eight-bit ports and an explicit transaction boundary",))
    scope = store.put(proposal.to_bytes())
    return store, source, proposal, scope


def author(tmp_path, *, real=False, experience=None):
    store, source, proposal, scope = seed(tmp_path)
    judge_policy = JudgePolicy("fixture-command", "fixture-authority", search_seconds=0).to_bytes()
    contract, run_policy = b"{}", b"{}"
    if real:
        task, _, _ = real_task(tmp_path / "fixture")
        judge_policy, contract, run_policy = task.judge_policy, task.contract.data, task.run_policy
    journal = AttemptJournal(tmp_path / "author", store)
    spec = AttemptSpec("author-1", "task_author", {"model": "fixture"})
    # The current public reference adapter requires a behavior.py entry; draft
    # records themselves do not impose that adapter-specific filename.
    selection = {"spec": "spec.md", "contract": "contract.json", "behavior": selected("behavior.py", "Identity"),
                 "run_policy": "generation.json", "public_files": [], "rtl": None}
    bodies = {"spec.md": specification(),
              "contract.json": contract.decode(), "generation.json": run_policy.decode(),
              "behavior.py": REFERENCE.decode() + "# PRIMARY_SECRET\n",
              "scratch.txt": "AUTHOR_TRANSCRIPT_SECRET"}
    script = Scripted(*(shell_call('write', {"path": path, "content": content}, key=str(i))
                        for i, (path, content) in enumerate(bodies.items())), call("submit_draft", {"draft": {"adaptations": None, **selection}, "scope_findings": []}))
    kwargs = {"task_id": "identity", "scope": scope, "reader": store, "journal": journal, "spec": spec,
              "judge_policy": judge_policy, "development_tools": (fixture_shell(),)}
    if experience is not None:
        kwargs["construction_experience"] = store.put(experience)
    result = run_author(backend=script, **kwargs)
    ref = BlobRef.from_dict(thaw_json(result.final_action)["draft"])
    return kwargs, result, ref, selection, source, proposal


def reconstruct(tmp_path, kwargs, ref):
    journal = AttemptJournal(tmp_path / "reader", kwargs["reader"])
    script = Scripted(shell_call('list', {}), shell_call('read', {"path": "behavior.py"}),
                      shell_call('read', {"path": ".construction/source/original.py"}),
                      shell_call('write', {"path": "independent.py", "content": BEHAVIOR.decode()}),
                      call("submit_reconstruction", {"behavior": selected("independent.py", "M"), "findings": []}))
    options = {"draft": ref, "reader": kwargs["reader"], "journal": journal,
               "spec": AttemptSpec("reader-1", "spec_reader", {"model": "fixture"}),
               "development_tools": (fixture_shell(),)}
    result = run_reader(backend=script, **options)
    output = BlobRef.from_dict(thaw_json(result.final_action)["reconstruction"])
    return output, script, result, options


def test_draft_canonical_and_dependency_identity(tmp_path):
    kwargs, result, ref, _, source, scope = author(tmp_path)
    loaded = load_draft(kwargs["reader"], ref)
    for record in (source, scope, loaded.record):
        assert type(record).from_bytes(record.to_bytes()) == record
        with pytest.raises(RecordError):
            type(record).from_bytes(record.to_bytes() + b"\n")
        row = parse_json(record.to_bytes())
        with pytest.raises(RecordError):
            type(record).from_bytes(canonical_json({**row, "approved": True}))
    assert loaded.record.task.reference_rtl is None
    assert run_author(backend=Scripted(), **kwargs) == result
    assert BlobRef.of(replace(scope, adaptations=("Different numeric scope",)).to_bytes()) != loaded.record.scope
    assert load_scope(kwargs["reader"], kwargs["scope"])[1].license is None


def test_reader_visibility_recovery_and_protected_inputs(tmp_path):
    kwargs, _, ref, _, _, _ = author(tmp_path)
    reconstruction, script, result, options = reconstruct(tmp_path, kwargs, ref)
    messages = repr([request.messages for request in script.requests])
    assert not any(secret in messages for secret in ("PRIMARY_SECRET", "ORIGINAL_SECRET", "AUTHOR_TRANSCRIPT_SECRET"))
    listed = parse_json(script.requests[1].messages[-2]["content"]["stdout"].encode())["files"]
    assert {f["path"] for f in listed} == {"spec.md", "contract.json",
        ".construction/sim_check.md", ".construction/behavior_environment.json"}
    assert "No such file" in messages
    assert run_reader(backend=Scripted(), **options) == result
    record = Reconstruction.from_bytes(kwargs["reader"].read(reconstruction))
    assert record.draft == ref and record.behavior.entry_file == "independent.py"
    # Changing the role's context cannot reopen the same journal as a fresh reader.
    with pytest.raises(ValueError, match="spec_reader"):
        run_reader(backend=Scripted(), **{**options, "spec": replace(options["spec"], role="task_author")})


def test_empty_reader_submission_is_repairable_feedback(tmp_path):
    kwargs, _, draft, _, _, _ = author(tmp_path)
    journal = AttemptJournal(tmp_path / "reader", kwargs["reader"])
    script = Scripted(
        call("submit_reconstruction", {"behavior": None, "findings": []}, key="empty"),
        call("submit_reconstruction", {"behavior": None, "findings": ["Output width is unspecified."]},
             key="corrected"),
    )
    options = {"draft": draft, "reader": kwargs["reader"], "journal": journal,
               "spec": AttemptSpec("reader-1", "spec_reader", {"model": "fixture"})}
    result = run_reader(backend=script, **options)
    assert len(script.requests) == 2
    assert thaw_json(script.requests[1].messages[-2])["content"] == {"error": "reconstruction-findings-required"}
    output = BlobRef.from_dict(thaw_json(result.final_action)["reconstruction"])
    record = Reconstruction.from_bytes(kwargs["reader"].read(output))
    assert record.behavior is None and record.findings == ("Output width is unspecified.",)
    assert run_reader(backend=Scripted(), **options) == result


def test_reader_submission_does_not_reclassify_storage_failure(tmp_path, monkeypatch):
    from behave.agent.attempt import AttemptInterrupted

    kwargs, _, draft, _, _, _ = author(tmp_path)
    store = kwargs["reader"]
    put = store.put

    def unavailable(payload):
        if b'"schema":"behave.spec-reconstruction/' in payload:
            raise RecordError("content-unavailable", "injected store failure")
        return put(payload)

    monkeypatch.setattr(store, "put", unavailable)
    with pytest.raises(AttemptInterrupted, match="content-unavailable"):
        run_reader(draft=draft, reader=store, journal=AttemptJournal(tmp_path / "reader", store),
                   backend=Scripted(call("submit_reconstruction", {"behavior": None, "findings": ["Ambiguous."]})),
                   spec=AttemptSpec("reader-1", "spec_reader", {"model": "fixture"}))


def test_repair_keeps_scratch_and_creates_new_revision_without_reusing_reader(tmp_path, monkeypatch):
    kwargs, _, first, selection, _, _ = author(tmp_path)
    reconstruction, _, _, _ = reconstruct(tmp_path, kwargs, first)
    store = kwargs["reader"]
    options = {**kwargs, "parent": first, "journal": AttemptJournal(tmp_path / "repair", store),
               "spec": replace(kwargs["spec"], attempt_id="author-2"), "feedback": "Clarify output field naming."}
    script = Scripted(shell_call('read', {"path": "scratch.txt"}),
                      shell_call('write', {"path": "spec.md", "content": specification("The output field is out_y.")}),
                      call("submit_draft", {"draft": {"adaptations": None, **selection}, "scope_findings": []}))
    result = run_author(backend=script, **options)
    second = BlobRef.from_dict(thaw_json(result.final_action)["draft"])
    assert first != second and load_draft(store, second).record.parent == first
    assert "AUTHOR_TRANSCRIPT_SECRET" in repr(script.requests[-1].messages)
    assert load_draft(store, first).file("spec.md").data == specification().encode()
    assert run_author(backend=Scripted(), **options) == result
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("stale reader must not dispatch"))
    with pytest.raises(RecordError, match="different draft"):
        compare_reconstruction(draft=second, reconstruction=reconstruction, reader=store,
                               journal=AttemptJournal(tmp_path / "stale", store))
    changed = store.put(replace(ScopeProposal.from_bytes(store.read(kwargs["scope"])), intent="Another task").to_bytes())
    with pytest.raises(RecordError, match="silently change"):
        run_author(backend=Scripted(), **{**options, "scope": changed})


def test_draft_rejects_direct_private_export_and_forged_snapshot(tmp_path):
    kwargs, _, ref, _, _, _ = author(tmp_path)
    store = kwargs["reader"]
    loaded = load_draft(store, ref)
    task = loaded.record.task
    primary = next(f for f in task.reference_behavior.files if f.path == task.reference_behavior.entry_file)
    for leaked in (primary, FileRef("renamed.py", primary.content)):
        with pytest.raises(RecordError, match="primary implementations"):
            replace(loaded.record, task=replace(task, public_files=(leaked,)))
    forged = replace(loaded.record, task=replace(task, spec=FileRef("spec.md", store.put(b"forged"))))
    with pytest.raises(RecordError, match="frozen workspace"):
        load_draft(store, store.put(forged.to_bytes()))
    # The extraction primitive is shared with solver submission, not duplicated.
    from behave.task_execution.submission import ArtifactSelection as SolverSelection
    assert SolverSelection is ArtifactSelection


def test_draft_preserves_large_scratch_without_workspace_quotas(tmp_path):
    kwargs, _, ref, _, _, _ = author(tmp_path)
    store = kwargs["reader"]
    loaded = load_draft(store, ref)
    workspace = Workspace(store)
    workspace.restore(loaded.record.workspace)
    body = b"x" * (17 << 20)
    workspace.write("scratch/large.bin", body)
    snapshot, _ = workspace.checkpoint()
    updated = store.put(replace(loaded.record, workspace=snapshot).to_bytes())
    restored = load_draft(store, updated)
    assert restored.file("scratch/large.bin").data == body
    assert restored.record.task == loaded.record.task


@pytest.mark.parametrize("score", [0, 1, None])
def test_reader_check_preserves_native_result_and_does_not_replace_primary(tmp_path, monkeypatch, score):
    kwargs, _, ref, _, _, _ = author(tmp_path)
    reconstruction, _, _, _ = reconstruct(tmp_path, kwargs, ref)
    store = kwargs["reader"]
    primary = load_draft(store, ref).record.task.reference_behavior
    seen = []
    def compare(inputs, **_):
        seen.append(inputs)
        return ComponentJudgment(score, "fixture", "reference" if score is None else None)
    monkeypatch.setattr(checks, "compare_sources", compare)
    options = {"draft": ref, "reconstruction": reconstruction, "reader": store,
               "journal": AttemptJournal(tmp_path / "checking", store)}
    report = compare_reconstruction(**options)
    assert report.comparison.result.score == score
    assert DraftCheck.from_bytes(report.to_bytes()) == report
    assert compare_reconstruction(**options) == report and len(seen) == 1
    assert seen[0].reference_behavior.artifact == primary
    assert load_draft(store, ref).record.task.reference_behavior == primary


def test_no_submission_is_unfinished_and_source_inputs_remain_protected(tmp_path):
    store, _, _, scope = seed(tmp_path)
    journal = AttemptJournal(tmp_path / "unfinished", store)
    spec = AttemptSpec("unfinished", "task_author", {"model": "fixture"})
    backend = Scripted(shell_call('write', {"path": ".construction/scope.json", "content": "forged"}),
                       ModelReply("Cannot finish this task."))
    result = run_author(task_id="identity", scope=scope, reader=store, journal=journal, backend=backend,
                        spec=spec, judge_policy=JudgePolicy("fixture", "fixture", search_seconds=0).to_bytes(),
                        development_tools=(fixture_shell(),))
    assert result.reason == "stopped" and result.final_action is None
    assert "workspace-read-only" in repr(backend.requests[-1].messages)
    restored = Workspace(store)
    restored.restore(result.workspace)
    assert restored.read(".construction/scope.json") == store.read(scope)


@pytest.mark.isolation
def test_real_author_reader_tools_to_concrete_comparison(tmp_path):
    kwargs, _, ref, _, _, _ = author(tmp_path, real=True)
    reconstruction, _, _, _ = reconstruct(tmp_path, kwargs, ref)
    store = kwargs["reader"]
    report = compare_reconstruction(draft=ref, reconstruction=reconstruction, reader=store,
                                   journal=AttemptJournal(tmp_path / "checking", store))
    assert report.comparison.outcome == "passed", report.to_bytes()
    assert report.comparison.result.analysis is None
