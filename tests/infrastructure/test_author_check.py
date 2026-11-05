import base64
from dataclasses import replace

import pytest
from test_construction import author, reconstruct
from test_judge import REFERENCE, real_task
from test_role_process import author_job
from test_synthesis import setup

from behave.agent.local_workspace import LocalWorkspace
from behave.agent.workspace import Workspace
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.author_check import AuthorCheckRelay, behavior_check_shell, check_guidance, comparison_inputs
from behave.data.author_check_client import exchange as client_exchange
from behave.data.author_check_client import request as client_request
from behave.data.drafts import load_draft
from behave.data.role import RoleJob, input_closure
from behave.data.synthesis_records import SynthesisPlan
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment


def packet(body=REFERENCE):
    encoded = lambda data: base64.b64encode(data).decode()
    return {"behavior": {"files": ["behavior.py"], "entry_file": "behavior.py", "entry_symbol": "Identity"},
            "files": [{"path": "behavior.py", "data": encoded(body)}],
            "contract": encoded(b"{}"), "run_policy": None}


def policy():
    return JudgePolicy("fixture-command", "fixture-authority", search_seconds=0).to_bytes()


def exchange(endpoint, payload):
    return client_exchange(endpoint, payload, defaults.LOCAL_RPC_POLL_SECONDS)


def test_experience_is_persistent_read_only_and_absent_from_blind_view(tmp_path):
    note = b"CONSTRUCTION_PRIVATE: make reset semantics explicit."
    kwargs, _, draft, _, _, _ = author(tmp_path, experience=note)
    loaded = load_draft(kwargs["reader"], draft)
    experience = next(file for file in loaded.files if file.reference.path == "experience/construction.md")
    assert experience.data == note
    assert all(not file.reference.path.startswith("experience/") for file in loaded.reader_files)
    assert all(file.reference.path != "experience/solver.md" for file in loaded.files)
    workspace = Workspace(kwargs["reader"], read_only_paths=(experience.reference.path,))
    workspace.seed(loaded.files)
    with pytest.raises(RecordError, match="cannot change an input"):
        workspace.write(experience.reference.path, b"overwrite")
    with pytest.raises(RecordError, match="construction context"):
        replace(loaded.record, task=replace(loaded.record.task, public_files=(experience.reference,)))
    _, script, _, _ = reconstruct(tmp_path, kwargs, draft)
    assert "CONSTRUCTION_PRIVATE" not in repr([request.messages for request in script.requests])


def test_experience_role_job_cold_copy_and_plan_roundtrip(tmp_path):
    options, job, _ = author_job(tmp_path)
    store = options["reader"]
    experience = store.put(b"Check signedness at endpoint boundaries.")
    inputs = replace(job.inputs, construction_experience=experience)
    job = replace(job, inputs=inputs)
    assert RoleJob.from_bytes(job.to_bytes()) == job
    refs = input_closure(job, store)
    assert experience in refs
    cold = DirectoryStore(tmp_path / "cold")
    cold.publish(store, refs)
    assert input_closure(RoleJob.from_bytes(job.to_bytes()), cold) == refs
    plan = replace(options["plan"], construction_experience=experience)
    assert SynthesisPlan.from_bytes(plan.to_bytes()) == plan
    original = options["plan"]
    assert "construction_experience" not in parse_json(original.to_bytes())
    assert SynthesisPlan.from_bytes(original.to_bytes()) == original


def test_check_captures_only_client_bytes_and_fixed_runtime_policy(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    requested = packet()
    inputs = comparison_inputs(requested, policy(), store)
    assert inputs.component == "behavior"
    assert inputs.source == inputs.reference_behavior
    assert inputs.source.files[0].data == REFERENCE
    assert inputs.judge_policy == policy()
    assert inputs.run_policy == b"null"
    assert inputs.reference_visible
    requested["judge_policy"] = "agent-selected"
    with pytest.raises(RecordError):
        comparison_inputs(requested, policy(), store)


def test_check_client_captures_only_selected_public_spec(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "spec.md").write_bytes(b"public spec declaration")
    (tmp_path / "behavior.py").write_bytes(REFERENCE)
    (tmp_path / "contract.json").write_bytes(b"{}")
    request = client_request({"spec": "spec.md", "behavior": packet()["behavior"],
                              "contract": "contract.json"})
    assert base64.b64decode(request["spec"]) == b"public spec declaration"
    assert [row["path"] for row in request["files"]] == ["behavior.py"]
    inputs = comparison_inputs(request, policy(), DirectoryStore(tmp_path / "objects"))
    assert inputs.source == inputs.reference_behavior


@pytest.mark.parametrize("consistent", [False, True])
def test_declaration_feedback_precedes_existing_behavior_check(tmp_path, consistent):
    calls = []
    spec = b"public declaration"

    def declaration(spec_bytes, contract, **kwargs):
        calls.append("declaration")
        assert spec_bytes == spec and contract == b"{}"
        assert kwargs["deadline"] is None
        return (ComponentJudgment(1, "task-interface-consistent") if consistent else
                ComponentJudgment(None, "admission-signedness", "reference"))

    def comparison(*args, **kwargs):
        calls.append("behavior")
        return ComponentJudgment(1, "matched")

    request = {**packet(), "spec": base64.b64encode(spec).decode()}
    with AuthorCheckRelay(objects=DirectoryStore(tmp_path / "objects"), judge_policy=policy(),
                          deadline=None, cancelled=lambda: False, executor=comparison,
                          declaration_checker=declaration) as relay:
        response = exchange(relay.endpoint, request)
    assert calls == (["declaration", "behavior"] if consistent else ["declaration"])
    reply = response["reply"]
    assert reply["matched"] is (True if consistent else None)
    assert reply["purpose"] == ("behavior-execution-check" if consistent else "task-interface-check")
    report = next(file for file in response["files"] if file["path"] == reply["report"])
    assert b"functional" in base64.b64decode(report["data"])
    if consistent:
        assert reply["declared_interface"] == "consistent"
    else:
        assert "declared_interface" not in reply


@pytest.mark.parametrize("component", ["behavior", "rtl"])
def test_comparison_client_captures_both_bundles_and_replay(tmp_path, monkeypatch, component):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reference.py").write_bytes(REFERENCE)
    (tmp_path / "candidate").write_bytes(b"candidate-source")
    (tmp_path / "contract.json").write_bytes(b"{}")
    stimuli = b'{"stimuli":[]}'
    (tmp_path / "replay.json").write_bytes(stimuli)
    selection = lambda path: {"files": [path], "entry_file": path, "entry_symbol": "Identity"}
    request = {"behavior": selection("reference.py"), "contract": "contract.json",
               "replay_stimuli": "replay.json"}
    if component == "behavior":
        request.update(behavior=selection("candidate"), reference_behavior=selection("reference.py"))
    else:
        request["rtl"] = selection("candidate")
    inputs = comparison_inputs(client_request(request), policy(), DirectoryStore(tmp_path / "objects"))
    assert inputs.component == component
    assert inputs.source.files[0].data == b"candidate-source"
    assert inputs.reference_behavior.files[0].data == REFERENCE
    assert inputs.replay_stimuli == stimuli
    assert inputs.judge_policy == policy()
    assert inputs.reference_visible


def test_supplemental_comparison_is_not_labeled_self_check(tmp_path):
    requested = packet()
    requested["reference_behavior"] = {"files": ["reference.py"], "entry_file": "reference.py",
                                       "entry_symbol": "Identity"}
    requested["files"].append({"path": "reference.py", "data": base64.b64encode(REFERENCE).decode()})
    with AuthorCheckRelay(objects=DirectoryStore(tmp_path / "objects"), judge_policy=policy(), deadline=None,
                          cancelled=lambda: False, executor=lambda *a, **kw: ComponentJudgment(1, "fixture")) as relay:
        response = exchange(relay.endpoint, requested)
        assert response["reply"]["purpose"] == "source-comparison"
        assert "independent_reference" not in response["reply"]
        report = next(file for file in response["files"] if file["path"] == response["reply"]["report"])
        assert b"does not replace the coordinator" in base64.b64decode(report["data"])


def test_request_rejects_ambiguous_comparison_sides(tmp_path):
    requested = packet()
    requested.update(rtl=requested["behavior"], reference_behavior=requested["behavior"])
    with pytest.raises(ValueError, match="either B/B or RTL"):
        comparison_inputs(requested, policy(), DirectoryStore(tmp_path / "objects"))


@pytest.mark.parametrize("score", [1, 0, None])
def test_bash_relay_reuses_comparison_and_labels_self_check(tmp_path, score):
    store = DirectoryStore(tmp_path / "objects")
    calls = []

    def execute(inputs, *, objects, workdir, cancelled):
        calls.append(inputs)
        evidence = objects.put(b"comparison-evidence")
        return ComponentJudgment(score, "fixture", "verifier" if score is None else None, evidence=(evidence,))

    with AuthorCheckRelay(objects=store, judge_policy=policy(), deadline=None,
                          cancelled=lambda: False, executor=execute) as relay:
        reply = exchange(relay.endpoint, packet())
        assert reply["reply"]["matched"] is (None if score is None else score == 1)
        assert reply["reply"]["purpose"] == "behavior-execution-check"
        assert reply["reply"]["independent_reference"] is False
        report = next(file for file in reply["files"] if file["path"] == reply["reply"]["report"])
        assert b"runs the submitted Behavior against itself" in base64.b64decode(report["data"])
        assert all(store.read(ref) is not None for ref in relay.artifacts)
    assert len(calls) == 1


def test_sim_timeout_reaches_author_as_no_result_with_report(tmp_path):
    def execute(*args, **kwargs):
        return ComponentJudgment(None, "judge-worker-timeout", "infrastructure",
                                 retryable=True, elapsed_seconds=1.25)

    with AuthorCheckRelay(objects=DirectoryStore(tmp_path / "objects"), judge_policy=policy(),
                          deadline=None, cancelled=lambda: False, executor=execute) as relay:
        response = exchange(relay.endpoint, packet())
        reply = response["reply"]
        assert reply["status"] == "no_result" and reply["matched"] is None
        assert "judge-worker-timeout" in reply["summary"]
        assert "Elapsed: 1.25 s" in reply["summary"]
        report = next(file for file in response["files"] if file["path"] == reply["report"])
        text = base64.b64decode(report["data"]).decode()
        assert "UNAVAILABLE" in text and "judge-worker-timeout" in text


def test_bad_request_does_not_dispatch_or_poison_next_check(tmp_path):
    def execute(*args, **kwargs):
        return ComponentJudgment(1, "fixture")
    with AuthorCheckRelay(objects=DirectoryStore(tmp_path / "objects"), judge_policy=policy(),
                          deadline=None, cancelled=lambda: False, executor=execute) as relay:
        bad = exchange(relay.endpoint, {"behavior": "invalid"})
        assert bad["reply"]["status"] == "invalid_request"
        assert not relay.artifacts
        assert exchange(relay.endpoint, packet())["reply"]["matched"] is True


def test_check_docs_use_runtime_and_public_capabilities():
    from behave.sim import api
    documents = {file.reference.path: file.data for file in check_guidance()}
    assert parse_json(documents[".construction/behavior_environment.json"]) == api.behavior_source_environment()
    assert b"python /tools/behave_sim_check.py check.json" in documents[".construction/sim_check.md"]


def test_reader_check_guidance_is_read_only(tmp_path):
    from syn_shell import fixture_shell, shell_call
    from test_attempts import Scripted, call

    from behave.agent.attempt import AttemptSpec
    from behave.agent.journal import AttemptJournal
    from behave.data.construction import run_reader

    kwargs, _, draft, _, _, _ = author(tmp_path)
    script = Scripted(
        shell_call("write", {"path": ".construction/sim_check.md", "content": "fake check"}),
        call("submit_reconstruction", {"behavior": None, "findings": ["Output behavior needs clarification."]}),
    )
    run_reader(draft=draft, reader=kwargs["reader"], journal=AttemptJournal(tmp_path / "b2", kwargs["reader"]),
               backend=script, spec=AttemptSpec("b2", "spec_reader", {"model": "fixture"}),
               development_tools=(fixture_shell(),))
    assert "workspace-read-only-changed" in repr(script.requests[-1].messages)


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("broken", [False, True])
def test_real_builder_bash_sim_check(tmp_path, broken):
    from test_task_declaration import spec

    from behave.agent.cpu_resources import CpuResources
    from behave.isolation import ResourceLimits
    from behave.task_execution.shell import development_shell

    task, _, _ = real_task(tmp_path / "task", rtl=None)
    workspace = LocalWorkspace(DirectoryStore(tmp_path / "objects"), tmp_path / "attempt")
    workspace.seed(())
    workspace.write("behavior.py", b"def broken(:\n" if broken else REFERENCE)
    workspace.write("contract.json", task.contract.data)
    workspace.write("spec.md", spec())
    workspace.write("run_policy.json", task.run_policy)
    request = {"behavior": packet()["behavior"], "contract": "contract.json", "run_policy": "run_policy.json",
               "spec": "spec.md"}
    workspace.write("check.json", canonical_json(request))
    resources = CpuResources(1, cpus=2)
    runner = development_shell(ResourceLimits(wall_seconds=120, processes=128), cpu_endpoint=resources)
    result = behavior_check_shell(runner, task.judge_policy).handler(workspace, {
        "command": "python /tools/behave_sim_check.py check.json"})
    assert result.content["status"] == ("exited" if broken else "completed"), result.content
    reply = parse_json(result.content["stdout"].encode())
    assert reply["independent_reference"] is False
    assert reply["declared_interface"] == "consistent"
    assert reply["matched"] is not True if broken else reply["matched"] is True
    assert result.content["exit_code"] != 0 if broken else result.content["exit_code"] == 0
    assert b"Behavior execution check" in workspace.read(reply["report"])
    assert result.artifacts
    assert resources.snapshot()["active"] == 0
    assert resources.snapshot()["available_cpus"] == 2


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("style", ["ready_valid", "sampled_edge", "settled"])
@pytest.mark.parametrize("renamed_ports", [False, True])
def test_contract_guidance_examples_execute_through_builder_shell(tmp_path, style, renamed_ports):
    import re

    from behave.agent.cpu_resources import CpuResources
    from behave.data.contract import contract_guidance
    from behave.isolation import ResourceLimits
    from behave.task_execution.shell import development_shell

    task, _, _ = real_task(tmp_path / "task", rtl=None)
    documents = {file.reference.path: file.data for file in contract_guidance("")}
    model = re.search(rb"```python\n(.*?)```", documents["task_contract.md"], re.S).group(1)
    contract = parse_json(documents[f"contract_{style}.json"])
    if renamed_ports:
        contract["endpoints"][0]["fields"][0]["port"] = "in_data"
        contract["endpoints"][1]["fields"][0]["port"] = "out_data"
        model = model.replace(b"in_x", b"in_data").replace(b"out_y", b"out_data")
    workspace = LocalWorkspace(DirectoryStore(tmp_path / "objects"), tmp_path / "attempt")
    workspace.seed(())
    workspace.write("behavior.py", model)
    workspace.write("contract.json", canonical_json(contract))
    workspace.write("check.json", canonical_json({
        "behavior": {"files": ["behavior.py"], "entry_file": "behavior.py", "entry_symbol": "Model"},
        "contract": "contract.json",
    }))
    resources = CpuResources(1, cpus=2)
    runner = development_shell(ResourceLimits(wall_seconds=120, processes=128), cpu_endpoint=resources)
    result = behavior_check_shell(runner, task.judge_policy).handler(workspace, {
        "command": "python /tools/behave_sim_check.py check.json"})
    assert result.content["exit_code"] == 0, result.content
    reply = parse_json(result.content["stdout"].encode())
    assert reply["matched"] is True, reply
    assert reply["independent_reference"] is False
    assert b"Behavior execution check" in workspace.read(reply["report"])
    assert resources.snapshot()["active"] == 0


@pytest.mark.eda
@pytest.mark.isolation
def test_process_builder_mounts_experience_and_runs_sim(tmp_path):
    from syn_shell import shell_call
    from test_attempts import call
    from test_role_process import channels, job_for

    from behave.core.records import thaw_json
    from behave.data.role import AuthorInputs, run_role_process

    options, replies, _ = setup(tmp_path, real=True)
    plan, store = options["plan"], options["reader"]
    note = b"Keep reset behavior explicit."
    inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy,
                          construction_experience=store.put(note))
    request = {"behavior": packet()["behavior"], "contract": "contract.json", "run_policy": "generation.json"}
    checks = [shell_call("write", {"path": "check.json", "content": canonical_json(request).decode()}, key="request"),
              call("run_command", {"command": "python /tools/behave_sim_check.py check.json"}, key="check")]
    job = job_for(inputs, [*replies[:-1], *checks, replies[-1]], guard="""
assert {tool['name'] for tool in request.tools} == {'run_command', 'submit_draft'}
""")
    result = run_role_process(job, reader=store, **channels(options["run"], "author"))
    loaded = load_draft(options["run"].objects, BlobRef.from_dict(thaw_json(result.final_action)["draft"]))
    files = {file.reference.path: file.data for file in loaded.files}
    assert files["experience/construction.md"] == note
    assert ".construction/sim_check.md" in files
    assert any(path.startswith("sim_check/") and path.endswith("/report.md")
               and b"\nPASS." in body for path, body in files.items())


@pytest.mark.eda
@pytest.mark.isolation
def test_process_b2_self_check_is_blind_and_not_correctness(tmp_path):
    from syn_shell import shell_call
    from test_attempts import call
    from test_role_process import job_for

    from behave.agent.journal import AttemptJournal
    from behave.core.records import thaw_json
    from behave.data.drafts import Reconstruction, load_workspace
    from behave.data.reviews import compare_reconstruction
    from behave.data.role import ReaderInputs, run_role_process

    kwargs, _, draft, _, _, _ = author(tmp_path, real=True, experience=b"CONSTRUCTION_PRIVATE")
    store = kwargs["reader"]
    selection = {"files": ["b2.py"], "entry_file": "b2.py", "entry_symbol": "Identity"}
    # Deliberately wrong for the identity spec, but executable: self-check must
    # not consult B1. The independent comparison below must still catch it.
    own = REFERENCE.replace(b"'out_y': in_a", b"'out_y': in_a ^ 1")
    request = {"behavior": selection, "contract": "contract.json"}
    job = job_for(ReaderInputs(draft), [
        shell_call("list", {}),
        shell_call("read", {"path": "behavior.py"}),
        shell_call("read", {"path": "experience/construction.md"}),
        shell_call("read", {"path": ".construction/source/original.py"}),
        shell_call("write", {"path": "b2.py", "content": own.decode()}),
        shell_call("write", {"path": "check.json", "content": canonical_json(request).decode()}),
        call("run_command", {"command": "python /tools/behave_sim_check.py check.json"}),
        call("submit_reconstruction", {"behavior": selection, "findings": []}),
    ], guard="""
assert {tool['name'] for tool in request.tools} == {'run_command', 'submit_reconstruction'}
messages = canonical_json(list(request.messages))
assert all(s not in messages for s in (b'PRIMARY_SECRET', b'ORIGINAL_SECRET', b'AUTHOR_TRANSCRIPT_SECRET', b'CONSTRUCTION_PRIVATE'))
if request.turn == 1:
    assert {f['path'] for f in parse_json(request.messages[-2]['content']['stdout'].encode())['files']} == {
        'spec.md', 'contract.json', '.construction/sim_check.md', '.construction/behavior_environment.json'}
if request.turn == 7:
    assert parse_json(request.messages[-2]['content']['stdout'].encode())['matched'] is True
""")
    result = run_role_process(job, reader=store, journal=AttemptJournal(tmp_path / "b2", store),
                              supervision=AttemptJournal(tmp_path / "b2_supervision", store))
    reconstruction = BlobRef.from_dict(thaw_json(result.final_action)["reconstruction"])
    record = Reconstruction.from_bytes(store.read(reconstruction))
    files = {file.reference.path: file.data for file in load_workspace(store, record.workspace)}
    assert files["b2.py"] == own
    assert all(not path.startswith("experience/") for path in files)
    assert any(path.startswith("sim_check/") and path.endswith("/report.md")
               and b"\nPASS." in body for path, body in files.items())
    check = compare_reconstruction(draft=draft, reconstruction=reconstruction, reader=store,
                                   journal=AttemptJournal(tmp_path / "independent_check", store))
    assert check.comparison.result.score == 0
