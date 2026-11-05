"""Construction tools document real file formats and recover from inline prose."""

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import Scripted, call
from test_construction import author, reconstruct, seed

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.core.artifacts import BlobRef
from behave.core.records import parse_json, thaw_json
from behave.data.contract import contract_guidance
from behave.data.drafts import load_draft, load_scope
from behave.data.scope import run_scope_proposal, scope_inputs, scope_review_inputs
from behave.sim import api


def test_examples_roundtrip_current_public_contract_schema():
    files = {f.reference.path: f.data for f in contract_guidance(".scope/")}
    assert b"workspace-relative" in files[".scope/task_contract.md"]
    assert b"not executable protocol compatibility" in files[".scope/task_contract.md"]
    assert b"literal RTL signal names" in files[".scope/task_contract.md"]
    assert b"separate presentation-control pins" in files[".scope/task_contract.md"]
    assert b"does not provide an importable" in files[".scope/task_contract.md"]
    assert b"def reset(self)" in files[".scope/task_contract.md"]
    assert b"declared payload `port` as a keyword argument" in files[".scope/task_contract.md"]
    assert b"process(self, in_data)" in files[".scope/task_contract.md"]
    assert b"reference with its default constructor" in files[".scope/task_contract.md"]
    assert b"Prose does not supply per-run constructor settings" in files[".scope/task_contract.md"]
    assert b"payload field named `valid` is ordinary data" in files[".scope/task_contract.md"]
    assert b"Signal names and prose do not create" in files[".scope/task_contract.md"]
    for name in ("ready_valid", "sampled_edge", "settled"):
        row = parse_json(files[f".scope/contract_{name}.json"])
        contract = api.TaskContract.from_dict(row)
        assert contract.as_dict() == row
        assert len(contract.endpoints) == 2
        assert bool(contract.clocks) == (name != "settled")
        assert all(field.port == field.name for endpoint in contract.endpoints for field in endpoint.fields)
        if name == "ready_valid":
            assert contract.endpoints[0].adapter.environment_controls == ("in_valid",)
            assert contract.endpoints[0].adapter.artifact_controls == ("in_ready",)
            assert contract.endpoints[1].adapter.environment_controls == ("out_ready",)
            assert contract.endpoints[1].adapter.artifact_controls == ("out_valid",)


@pytest.mark.parametrize("style", ["ready_valid", "sampled_edge", "settled"])
def test_reference_port_renaming_requires_matching_behavior_names(style):
    import re

    from behave.sim.core.reference import SlotEvent
    from tests.support.reference import load

    files = {f.reference.path: f.data for f in contract_guidance("")}
    row = parse_json(files[f"contract_{style}.json"])
    row["endpoints"][0]["fields"][0]["port"] = "in_data"
    row["endpoints"][1]["fields"][0]["port"] = "out_data"
    contract = api.TaskContract.from_dict(row)
    model = re.search(rb"```python\n(.*?)```", files["task_contract.md"], re.S).group(1)
    event = SlotEvent(0, accepted_inputs=(("input", (7,)),))
    unchanged = load({"behavior.py": model}, config={"class": "Model"}).instantiate(contract)
    try:
        unchanged.reset()
        with pytest.raises(TypeError, match="in_data"):
            unchanged.advance(event)
    finally:
        unchanged.close()
    renamed = model.replace(b"in_x", b"in_data").replace(b"out_y", b"out_data")
    reference = load({"behavior.py": renamed}, config={"class": "Model"}).instantiate(contract)
    try:
        reference.reset()
        assert reference.advance(event) == [(7,)]
    finally:
        reference.close()


def test_scope_inline_prose_feedback_then_real_json_submission(tmp_path):
    store, _, old, _ = seed(tmp_path)
    guide = store.put(b"Preserve the computation and declare its finite interface.")
    initial, _ = scope_inputs(store, old.source, guide)
    mounted = {f.reference.path: f.data for f in initial}
    args = {"intent": old.intent, "capabilities": list(old.capabilities),
            "adaptations": list(old.adaptations), "contract": "# Contract\ninput x, output y"}
    model = Scripted(
        call("submit_scope", args),
        shell_call("read", {"path": ".scope/task_contract.md"}),
        shell_call("write", {"path": "contract.json", "content": "{}"}),
        call("submit_scope", {**args, "contract": "contract.json"}),
        shell_call("write", {"path": "contract.json", "content":
            mounted[".scope/contract_ready_valid.json"].decode()}),
        shell_call("write", {"path": "spec.md", "content": "Scratch draft, not submitted."}),
        shell_call("write", {"path": "verify.py", "content": "assert True"}),
        call("submit_scope", {**args, "contract": "contract.json"}),
    )
    result = run_scope_proposal(source=old.source, guidance=guide, reader=store,
        journal=AttemptJournal(tmp_path / "scope", store), backend=model,
        spec=AttemptSpec("scope", "scope_author", {"model": "fixture"}),
        development_tools=(fixture_shell(),))
    first = thaw_json(model.requests[1].messages[-2])["content"]
    assert first["error"] == "record-text-invalid"
    assert "control characters" in first["detail"]
    assert "workspace-relative JSON file" in first["hint"]
    invalid_json = thaw_json(model.requests[4].messages[-2])["content"]
    assert invalid_json["detail"] and "task_contract.md" in invalid_json["hint"]
    ref = BlobRef.from_dict(thaw_json(result.final_action)["scope"])
    proposal, _, _ = load_scope(store, ref)
    assert proposal.contract.path == "contract.json"
    assert store.read(proposal.contract.content) == mounted[".scope/contract_ready_valid.json"]
    _, review_files = scope_review_inputs(store, ref, guide)
    review_paths = {f.reference.path for f in review_files}
    assert "proposal/contract.json" in review_paths
    assert not any(path.endswith(("spec.md", "verify.py")) for path in review_paths)


def test_scope_submission_is_final_and_review_cannot_fill_in_placeholder_scope():
    from behave.agent.prompting import render

    author = " ".join(render("synthesis/scope_author/system").split())
    submit = " ".join(render("synthesis/scope_author/submit").split())
    reviewer = " ".join(render("synthesis/scope_reviewer/system").split())
    assert "do not use it as a dry-run validator" in author
    assert "ends this attempt" in author
    assert "Successful submission ends the attempt" in submit
    assert "not string-encoded arrays" in submit
    assert "Do not approve placeholder scope fields by supplying the missing proposal" in reviewer
    assert "Neither a valid contract nor your own inferred intent replaces missing substantive content" in " ".join(
        render("synthesis/scope_reviewer/submit").split())


@pytest.mark.parametrize("field", ["capabilities", "adaptations"])
def test_scope_array_error_explains_arguments_without_rewriting_contract(tmp_path, field):
    store, _, old, _ = seed(tmp_path)
    guide = store.put(b"Preserve the computation and declare its finite interface.")
    initial, _ = scope_inputs(store, old.source, guide)
    mounted = {f.reference.path: f.data for f in initial}
    contract = mounted[".scope/contract_ready_valid.json"]
    args = {"intent": old.intent, "capabilities": list(old.capabilities),
            "adaptations": list(old.adaptations), "contract": "contract.json"}
    model = Scripted(
        shell_call("write", {"path": "contract.json", "content": contract.decode()}),
        call("submit_scope", {**args, field: "Incorrect string instead of array."}),
        call("submit_scope", args),
    )
    result = run_scope_proposal(source=old.source, guidance=guide, reader=store,
        journal=AttemptJournal(tmp_path / "scope", store), backend=model,
        spec=AttemptSpec("scope", "scope_author", {"model": "fixture"}),
        development_tools=(fixture_shell(),))
    reply = thaw_json(model.requests[2].messages[-2])["content"]
    assert reply["error"] == "record-array-invalid"
    assert "capabilities and adaptations" in reply["hint"]
    assert "JSON arrays of strings" in reply["hint"]
    proposal, _, _ = load_scope(store, BlobRef.from_dict(thaw_json(result.final_action)["scope"]))
    assert store.read(proposal.contract.content) == contract


def test_builder_receives_guidance_but_blind_reader_does_not(tmp_path):
    kwargs, _, ref, _, _, _ = author(tmp_path)
    draft = load_draft(kwargs["reader"], ref)
    mounted = {f.reference.path: f.data for f in draft.files}
    assert b"run_policy" in mounted[".construction/task_contract.md"]
    assert ".construction/contract_ready_valid.json" in mounted
    _, model, _, _ = reconstruct(tmp_path, kwargs, ref)
    listed = parse_json(model.requests[1].messages[-2]["content"]["stdout"].encode())["files"]
    assert {item["path"] for item in listed} == {"spec.md", "contract.json",
        ".construction/sim_check.md", ".construction/behavior_environment.json"}
