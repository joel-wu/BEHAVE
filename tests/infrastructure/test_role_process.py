"""Real construction processes share supervision, tools and accounting with eval."""

import sys
from dataclasses import replace
from pathlib import Path
from shlex import quote

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import call
from test_construction import selected, specification
from test_judge import BEHAVIOR, REFERENCE
from test_release_admission import assessment
from test_synthesis import setup

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import load_chain
from behave.agent.model import Charge, ModelReply
from behave.agent.process_model import ModelCommand
from behave.agent.run_store import JournalDirectory
from behave.agent.trajectory import trajectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.drafts import load_draft
from behave.data.reviews import compare_reconstruction
from behave.data.role import AuthorInputs, ReaderInputs, RoleJob, SourceReviewInputs, run_role_process
from behave.data.source import SourceReview
from behave.isolation.model import ResourceLimits

ROOT = str(Path(sys.modules["behave"].__file__).resolve().parent.parent)


def job_for(inputs, replies, *, guard="", python=False):
    script = [replace(reply, charge=Charge("0.01", "USD", "fixture")).as_dict() for reply in replies]
    program = f'''
import sys
from behave.agent.model import ModelReply, ModelRequest
from behave.agent.process_model import encode_model_outcome
from behave.core.records import parse_json, canonical_json
request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
{guard}
replies = parse_json({canonical_json({'replies': script})!r})['replies']
reply = ModelReply.from_dict(replies[request.turn], raw=b"fixture-reply")
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    command = ModelCommand((sys.executable, "-I", "-B", "-c",
                            "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, program),
                           "construction-fixture/1")
    spec = AttemptSpec(inputs.role, inputs.role, {"model": "fixture", "transport": command.identity},
                       request_timeout_seconds=15)
    return RoleJob(inputs, spec, command, shell_limits=ResourceLimits(wall_seconds=60, processes=32))


def author_job(tmp_path, **kwargs):
    options, replies, submit = setup(tmp_path, real=True)
    plan = options["plan"]
    inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy)
    return options, job_for(inputs, replies, **kwargs), submit


def channels(run, name):
    return {"journal": run.journal(name), "supervision": run.journal(name + "_supervision")}


class Unavailable:
    def read(self, _):
        pytest.fail("recovery must not read the upstream store")


def artifact(result, name):
    assert result.reason == "final_action"
    return BlobRef.from_dict(thaw_json(result.final_action)[name])


@pytest.mark.isolation
def test_author_reader_review_processes_share_real_comparison_and_provider_pool(tmp_path, monkeypatch):
    options, replies, _ = setup(tmp_path, real=True)
    plan, run = options["plan"], options["run"]
    replies[3] = shell_call('write', {"path": "behavior.py", "content": REFERENCE.decode() + "# PRIMARY_SECRET\n"})
    replies[4] = shell_call('write', {"path": "scratch.txt", "content": "AUTHOR_TRANSCRIPT_SECRET"})
    author = job_for(AuthorInputs(plan.task_id, plan.scope, plan.judge_policy), replies)
    pool = ResourcePool(AdmissionPolicy("shared-construction-model", 2))
    author_channels = {**channels(run, "author"), "provider": run.journal("author_provider"), "model_pool": pool}
    first = run_role_process(author, reader=options["reader"], **author_channels)
    draft = artifact(first, "draft")
    independent = job_for(ReaderInputs(draft), [
        shell_call('list', {}), shell_call('read', {"path": "behavior.py"}),
        shell_call('read', {"path": ".construction/source/original.py"}),
        shell_call('write', {"path": "reader.py", "content": BEHAVIOR.decode()}),
        call("submit_reconstruction", {"behavior": selected("reader.py", "M"), "findings": []}),
    ], guard="""
messages = canonical_json(list(request.messages))
assert all(s not in messages for s in (b'PRIMARY_SECRET', b'ORIGINAL_SECRET', b'AUTHOR_TRANSCRIPT_SECRET'))
if request.turn == 1:
    assert {f['path'] for f in parse_json(request.messages[-2]['content']['stdout'].encode())["files"]} == {
        'spec.md', 'contract.json', '.construction/sim_check.md', '.construction/behavior_environment.json'}
if request.turn == 4:
    assert b'No such file' in messages
""")
    reader_channels = {**channels(run, "reader"), "provider": run.journal("reader_provider"), "model_pool": pool}
    second = run_role_process(independent, reader=run.objects, **reader_channels)
    report = compare_reconstruction(draft=draft, reconstruction=artifact(second, "reconstruction"),
                                   reader=run.objects, journal=run.journal("check"))
    assert report.comparison.outcome == "passed", report.to_bytes()
    assert report.comparison.result.analysis is None  # Only concrete comparison here.
    guidance = run.objects.put(b"Retain each source capability; cite source and task evidence.")
    check = run.objects.put(report.to_bytes())
    challenge = ("import runpy\n"
                 "b1 = runpy.run_path('/workspace/draft/behavior.py')['Identity']()\n"
                 "b2 = runpy.run_path('/workspace/reconstruction/reader.py')['M']()\n"
                 "b1.reset(); b2.reset()\n"
                 "for x in (0, 1, 127, 128, 254, 255):\n"
                 "    assert b1.process(x)['out_y'] == b2.process(x)['y'] == x\n"
                 "print('review-challenge-ok')\n")
    review = job_for(SourceReviewInputs(draft, guidance, check), [
        shell_call('list', {}), shell_call('write', {"path": "draft/spec.md", "content": "rewrite"}),
        shell_call('write', {"path": "challenge.py", "content": challenge}),
        call("run_command", {"command": "python3 /workspace/challenge.py"}),
        shell_call('read', {"path": "source/original.py"}),
        shell_call('read', {"path": "draft/behavior.py"}),
        shell_call('read', {"path": "reconstruction/reader.py"}),
        call("submit_source_review", {"assessments": [assessment()], "findings": []}),
    ], python=True, guard="""
assert {'run_command', 'submit_source_review'} == {t['name'] for t in request.tools}
if request.turn == 1:
    names = {f['path'] for f in parse_json(request.messages[-2]['content']['stdout'].encode())["files"]}
    assert 'draft/behavior.py' in names and 'source/original.py' in names
    assert 'reconstruction/reader.py' in names and 'review/check.json' in names
    assert all('scratch' not in n and '.construction' not in n for n in names)
if request.turn == 2:
    assert request.messages[-2]['content']['exit_code'] != 0
if request.turn == 4:
    assert request.messages[-2]['content']['exit_code'] == 0
    assert 'review-challenge-ok' in request.messages[-2]['content']['stdout']
""")
    review_channels = {**channels(run, "review"), "provider": run.journal("review_provider"), "model_pool": pool}
    third = run_role_process(review, reader=run.objects, **review_channels)
    record = SourceReview.from_bytes(run.objects.read(artifact(third, "source_review")))
    assert record.draft == draft and record.guidance == guidance and record.check == check and not record.findings
    assert pool.counts == (0, 0)
    for name, turns in (("author", 6), ("reader", 5), ("review", 8)):
        assert trajectory(run.journal(name)).cost_by_currency() == {"USD": f"0.{turns:02d}"}
        events = run.journal(name + "_provider").load()
        assert sum(e.kind == "model_settled" for e in events) == turns
        assert run.journal(name + "_supervision").load()[-1].payload["cleanup"] == "complete"
    # Export all three kinds of journal, not just model-visible role turns.
    exported = DirectoryStore(tmp_path / "export")
    for name in ("author", "reader", "review"):
        for part in (name, name + "_supervision", name + "_provider"):
            head, closure = run.seal(part)
            exported.publish(run.objects, closure)
            assert load_chain(exported, head) == run.journal(part).load()
    assert load_draft(exported, draft).record == load_draft(run.objects, draft).record
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("completed workers must not relaunch"))
    for job, result, journals in ((author, first, author_channels), (independent, second, reader_channels),
                                  (review, third, review_channels)):
        assert run_role_process(job, reader=Unavailable(), **journals) == result
    assert not (run.directory / "release").exists()


@pytest.mark.eda
@pytest.mark.isolation
def test_reviewer_process_runs_shared_sim_comparison_and_explicit_replay(tmp_path):
    from test_construction import author, reconstruct

    from behave.agent.journal import AttemptJournal
    from behave.data.source import review_inputs
    from behave.sim import api

    kwargs, _, draft, _, _, _ = author(tmp_path, real=True)
    reconstruction, _, _, _ = reconstruct(tmp_path, kwargs, draft)
    store = kwargs["reader"]
    check = compare_reconstruction(draft=draft, reconstruction=reconstruction, reader=store,
                                   journal=AttemptJournal(tmp_path / "check", store))
    guidance, check_ref = store.put(b"Check source and both implementations."), store.put(check.to_bytes())
    loaded, _, files = review_inputs(store, draft, guidance, check=check_ref)
    prepared = {file.reference.path: file.data for file in files}
    request = parse_json(prepared["review/sim_check.json"])
    assert request["behavior"]["entry_file"].startswith("reconstruction/")
    assert request["reference_behavior"]["entry_file"].startswith("draft/")
    contract = api.TaskContract.from_dict(parse_json(loaded.file(loaded.record.task.contract.path).data))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=2, max_queue_depth=2))
    stimuli = canonical_json({"stimuli": [item.as_dict() for item in baseline.stimuli]})
    replay = canonical_json({**request, "replay_stimuli": "replay.json"})
    job = job_for(SourceReviewInputs(draft, guidance, check_ref), [
        call("run_command", {"command": "python /tools/behave_sim_check.py review/sim_check.json"}),
        shell_call("write", {"path": "replay.json", "content": stimuli.decode()}),
        shell_call("write", {"path": "replay_check.json", "content": replay.decode()}),
        call("run_command", {"command": "python /tools/behave_sim_check.py replay_check.json"}),
        shell_call("write", {"path": "review/sim_check.json", "content": "{}"}),
        call("submit_source_review", {"assessments": [assessment()], "findings": []}),
    ], guard="""
assert {t['name'] for t in request.tools} == {'run_command', 'submit_source_review'}
if request.turn in (1, 4):
    result = request.messages[-2]['content']
    assert result['exit_code'] == 0, result
    reply = parse_json(result['stdout'].encode())
    assert reply['matched'] is True and reply['purpose'] == 'source-comparison', reply
if request.turn == 5:
    assert request.messages[-2]['content']['exit_code'] != 0
""")
    journal = AttemptJournal(tmp_path / "review", store)
    result = run_role_process(job, reader=store, journal=journal,
                              supervision=AttemptJournal(tmp_path / "review_supervision", store))
    assert result.reason == "final_action"
    report = SourceReview.from_bytes(store.read(artifact(result, "source_review")))
    assert report.check == check_ref and report.supported
    # Evidence from the supplemental runs survives the process, without
    # replacing the coordinator's original check binding.
    from behave.data.drafts import load_workspace
    workspace = {f.reference.path: f.data for f in load_workspace(store, result.workspace)}
    assert sum(path.startswith("sim_check/") and path.endswith("/report.md") for path in workspace) == 2


def test_closed_role_manifest_and_readonly_capabilities(tmp_path):
    options, job, _ = author_job(tmp_path)
    ref = BlobRef.of(b"draft")
    for inputs in (job.inputs, ReaderInputs(ref), SourceReviewInputs(ref, BlobRef.of(b"guide"))):
        role = job_for(inputs, [ModelReply("stopped")])
        assert RoleJob.from_bytes(role.to_bytes()) == role
        bad = parse_json(role.to_bytes())
        bad["inputs"]["callback"] = "import user_code"
        with pytest.raises(RecordError):
            RoleJob.from_bytes(canonical_json(bad))
        with pytest.raises(RecordError, match="canonical"):
            RoleJob.from_bytes(role.to_bytes() + b"\n")
    reviewer = job_for(SourceReviewInputs(ref, ref, ref), [], python=True)
    assert reviewer.shell_limits is not None and reviewer.python_limits is None
    assert RoleJob.from_bytes(reviewer.to_bytes()) == reviewer
    with pytest.raises(ValueError, match="role differ"):
        replace(job, inputs=ReaderInputs(ref))
    with pytest.raises(ValueError, match="transport"):
        replace(job, attempt=replace(job.attempt, model_policy={"transport": "wrong"}))
    with pytest.raises(ValueError, match="separate execution ledger"):
        run_role_process(job, reader=options["reader"], **channels(options["run"], "role"),
                         model_pool=ResourcePool(AdmissionPolicy("model", 1)))


@pytest.mark.isolation
def test_normal_unfinished_and_cancel_are_not_task_scores(tmp_path):
    options, original, _ = author_job(tmp_path)
    job = job_for(original.inputs, [ModelReply("Cannot finish.")])
    run = options["run"]
    result = run_role_process(job, reader=options["reader"], **channels(run, "unfinished"))
    assert result.reason == "stopped" and result.final_action is None
    for _ in range(2):
        with pytest.raises(AttemptInterrupted, match="construction-worker-cancelled"):
            run_role_process(job, reader=options["reader"], **channels(run, "cancelled"), cancelled=lambda: True)
    assert not run.journal("cancelled").load()


@pytest.mark.isolation
def test_author_command_timeout_is_feedback_not_an_attempt_deadline(tmp_path):
    options, original, _ = author_job(tmp_path)
    job = job_for(original.inputs, [call("run_command", {"command": "sleep 60"}),
                                   ModelReply("done")], python=True, guard="""
if request.turn == 1:
    assert request.messages[-2]['content']['status'] == 'timed-out'
""")
    job = replace(job, shell_limits=replace(job.shell_limits, wall_seconds=1))
    run = options["run"]
    journals = channels(run, "author")
    result = run_role_process(job, reader=options["reader"], **journals)
    assert result.reason == "stopped" and result.turns == 2
    events = journals["journal"].load()
    assert any(e.kind == "tool_settled" for e in events) and events[-1].kind == "terminal"
    receipt = journals["supervision"].load()[-1].payload
    assert receipt["status"] == "exited" and receipt["cleanup"] == "complete"
    assert run_role_process(job, reader=Unavailable(), **journals) == result
    assert journals["journal"].load() == events


@pytest.mark.isolation
def test_reader_development_has_only_public_workspace(tmp_path):
    from test_construction import author

    kwargs, _, draft, _, _, _ = author(tmp_path / "fixture", real=True)
    command = ("from pathlib import Path; "
               "assert {p.name for p in Path('/workspace').iterdir()} == {'spec.md','contract.json'}; "
               "assert not Path('/workspace/behavior.py').exists(); print('reader-ok')")
    job = job_for(ReaderInputs(draft), [
        call("run_command", {"command": "python3 -c " + quote(command)}), ModelReply("Cannot finish yet.")], python=True,
        guard="""
if request.turn == 1:
    assert b'reader-ok' in canonical_json(list(request.messages))
""")
    run = JournalDirectory(tmp_path / "process")
    result = run_role_process(job, reader=kwargs["reader"], **channels(run, "reader"))
    assert result.reason == "stopped"
    assert b"PRIMARY_SECRET" not in canonical_json([thaw_json(e.payload) for e in run.journal("reader").load()])


def test_unknown_supervision_is_never_relaunched(tmp_path, monkeypatch):
    options, job, _ = author_job(tmp_path)
    journals = channels(options["run"], "author")
    from behave.isolation import service

    class LostOwner(BaseException):
        pass

    monkeypatch.setattr(service, "run_service", lambda *a, **kw: (_ for _ in ()).throw(LostOwner()))
    with pytest.raises(LostOwner):
        run_role_process(job, reader=options["reader"], **journals)
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("unknown worker must not relaunch"))
    with pytest.raises(AttemptInterrupted, match="unknown"):
        run_role_process(job, reader=Unavailable(), **journals)


@pytest.mark.parametrize("cleanup", [CleanupState.INCOMPLETE, CleanupState.UNKNOWN])
def test_unconfirmed_cleanup_cannot_return_a_completed_role(tmp_path, monkeypatch, cleanup):
    options, job, _ = author_job(tmp_path)
    journals = channels(options["run"], "author")
    from behave.isolation import service
    from behave.isolation.service import ServiceResult

    def run(*args, **kwargs):
        from test_attempts import Scripted

        from behave.data.construction import run_author

        run_author(task_id=job.inputs.task_id, scope=job.inputs.scope, reader=journals["journal"].objects,
                   journal=journals["journal"], spec=job.attempt, backend=Scripted(ModelReply("stopped")),
                   judge_policy=options["reader"].read(job.inputs.judge_policy))
        status = "cleanup-incomplete" if cleanup is CleanupState.INCOMPLETE else "unknown"
        return ServiceResult(status, None, b"", b"", 0., 0.)

    monkeypatch.setattr(service, "run_service", run)
    with pytest.raises(RecordError, match="quiescent"):
        run_role_process(job, reader=options["reader"], **journals)
    assert journals["journal"].load()[-1].kind == "terminal"
    assert journals["supervision"].load()[-1].payload["cleanup"] == cleanup.value


@pytest.mark.parametrize("guidance,code", [(b" \n", "review-guidance-empty"), (b"\xff", "review-guidance-not-text")])
def test_invalid_review_configuration_never_dispatches(tmp_path, monkeypatch, guidance, code):
    from test_construction import author

    kwargs, _, draft, _, _, _ = author(tmp_path / "fixture")
    upstream = kwargs["reader"]
    job = job_for(SourceReviewInputs(draft, upstream.put(guidance)), [ModelReply("not reached")])
    run = JournalDirectory(tmp_path / "process")
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("configuration must fail before dispatch"))
    with pytest.raises(RecordError) as error:
        run_role_process(job, reader=upstream, **channels(run, "review"))
    assert error.value.code == code and not run.journal("review_supervision").load()


@pytest.mark.isolation
def test_repair_process_retains_parent_scratch_and_pins_ancestry(tmp_path):
    from test_construction import author

    kwargs, _, parent, submit, _, _ = author(tmp_path / "fixture", real=True)
    upstream = kwargs["reader"]
    policy = upstream.put(kwargs["judge_policy"])
    inputs = AuthorInputs("identity", kwargs["scope"], policy, parent, "Clarify output naming without reducing scope.")
    job = job_for(inputs, [shell_call('read', {"path": "scratch.txt"}),
                          shell_call('write', {"path": "spec.md", "content": specification("Return the eight-bit input as out_y.")}),
                          call("submit_draft", {"draft": {"adaptations": None, **submit}, "scope_findings": []})], guard="""
if request.turn == 1:
    assert b'AUTHOR_TRANSCRIPT_SECRET' in canonical_json(list(request.messages))
""")
    run = JournalDirectory(tmp_path / "repair")
    journals = channels(run, "author")
    result = run_role_process(job, reader=upstream, **journals)
    current = load_draft(run.objects, artifact(result, "draft"))
    previous = load_draft(run.objects, parent)
    assert current.record.parent == parent and current.record.scope == previous.record.scope
    assert current.record.task.reference_behavior == previous.record.task.reference_behavior
    assert current.file("spec.md").data != previous.file("spec.md").data
    assert run_role_process(job, reader=Unavailable(), **journals) == result
    with pytest.raises(RecordError, match="replay differs"):
        run_role_process(replace(job, inputs=replace(inputs, feedback="changed")), reader=Unavailable(), **journals)


@pytest.mark.eda
@pytest.mark.isolation
def test_author_ordinary_cocotb_via_bash(tmp_path):
    from test_cocotb_development import TB
    from test_judge import RTL

    options, original, _ = author_job(tmp_path)
    makefile = ("SIM = verilator\nTOPLEVEL_LANG = verilog\nVERILOG_SOURCES = $(PWD)/top.sv\n"
                "COCOTB_TOPLEVEL = M\nCOCOTB_TEST_MODULES = tb\n"
                "include $(shell cocotb-config --makefiles)/Makefile.sim\n")
    files = {"top.sv": "`timescale 1ns/1ps\n" + RTL.decode(), "model.py": BEHAVIOR.decode(), "Makefile": makefile,
             "tb.py": TB.replace("PRIVATE_ROOT", repr(str(options["run"].directory)))}
    replies = [shell_call("write", {"path": name, "content": body}, key=name) for name, body in files.items()]
    job = job_for(original.inputs, [*replies, call("run_command", {"command": "make"}),
                                    ModelReply("Development only, no draft yet.")], guard="""
assert {t['name'] for t in request.tools} == {'run_command', 'submit_draft'}
""")
    run = options["run"]
    result = run_role_process(job, reader=options["reader"], **channels(run, "author"))
    assert result.reason == "stopped" and result.final_action is None
    assert not run.journal("development").load()
    events = [event for event in run.journal("author").load() if event.kind == "tool_settled"]
    command = parse_json(run.objects.read(BlobRef.from_dict(thaw_json(events[-1].payload)["result"])))
    assert command["status"] == "completed", command
    assert command["exit_code"] == 0, command["stdout"] + command["stderr"]
    assert "TESTS=1 PASS=1 FAIL=0" in command["stdout"], command["stdout"] + command["stderr"]
    assert "score" not in command
