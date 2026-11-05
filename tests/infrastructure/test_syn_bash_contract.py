"""The live Syn surface is Bash plus a role's structured submission."""

from dataclasses import replace

import pytest
from syn_shell import shell_call
from test_attempts import Scripted, call
from test_role_process import job_for
from test_synthesis import reader_replies
from test_synthesis_source_review import prepared, review_reply

from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.data import checks
from behave.data.evidence import evidence_files
from behave.data.role import AuthorInputs, RoleJob, input_closure
from behave.data.synthesis import synthesize
from behave.isolation.model import ResourceLimits
from behave.task_execution.judgment import ComponentJudgment


def test_builder_reader_reviewer_have_only_bash_and_submit(tmp_path, monkeypatch):
    options, replies, _ = prepared(tmp_path)
    evidence = options["run"].objects.put(b'{"counterexample": 17}')
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **kw:
                        ComponentJudgment(1, "fixture", evidence=(evidence,)))
    builder, implementer = Scripted(*replies), Scripted(*reader_replies())
    reviewer = Scripted(shell_call("read", {"path": "review/evidence/1.blob"}),
                        shell_call("write", {"path": "review/evidence/1.blob", "content": "forged"}),
                        review_reply())
    result = synthesize(**options, author_backend=builder, reader_backend=implementer,
                        source_reviewer_backend=reviewer)
    assert result.status == "review_ready"
    for backend, final in ((builder, "submit_draft"), (implementer, "submit_reconstruction"),
                           (reviewer, "submit_source_review")):
        for request in backend.requests:
            assert {tool["name"] for tool in request.tools} == {"run_command", final}
            shell = next(tool for tool in request.tools if tool["name"] == "run_command")
            assert set(shell["parameters"]["properties"]) == {"command"}
            for tool in request.tools:
                strict_objects(tool["parameters"])
    assert '"counterexample": 17' in reviewer.requests[1].messages[-2]["content"]["stdout"]
    assert reviewer.requests[2].messages[-2]["content"]["exit_code"] != 0


def strict_objects(schema):
    """The provider uses strict tools: every object property must be declared."""
    if isinstance(schema, dict) or hasattr(schema, "items"):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
        assert "oneOf" not in schema
        for value in schema.values():
            strict_objects(value)
    elif isinstance(schema, (tuple, list)):
        for value in schema:
            strict_objects(value)


def test_scope_feedback_is_a_variant_of_submit_draft(tmp_path):
    options, _, _ = prepared(tmp_path)
    builder = Scripted(call("submit_draft", {"draft": None, **{"scope_findings": ["The retained computation needs a wider output."]}}))
    result = synthesize(**options, author_backend=builder, reader_backend=Scripted(),
                        source_reviewer_backend=Scripted())
    assert result.status == "scope_review_requested"
    assert {tool["name"] for tool in builder.requests[0].tools} == {"run_command", "submit_draft"}


@pytest.mark.parametrize("mode", ["missing", "python", "cocotb"])
def test_removed_runtime_modes_are_rejected_before_execution(tmp_path, mode):
    from test_cocotb_development import POLICY

    options, _, _ = prepared(tmp_path)
    plan = options["plan"]
    job = job_for(AuthorInputs(plan.task_id, plan.scope, plan.judge_policy), [])
    if mode == "missing":
        job = replace(job, shell_limits=None)
    elif mode == "python":
        job = replace(job, shell_limits=None, python_limits=ResourceLimits())
    else:
        job = replace(job, cocotb_policy=POLICY)
    assert RoleJob.from_bytes(job.to_bytes()) == job  # Old records still decode.
    with pytest.raises(ValueError, match="Bash|shell_limits"):
        input_closure(job, options["reader"])


def test_evidence_files_are_complete_allowlisted_bytes(tmp_path):
    options, _, _ = prepared(tmp_path)
    store = options["reader"]
    body = b"x" * (2 << 20) + b"\x00\xff"
    ref = store.put(body)
    files = evidence_files("review/evidence", b"{}", (ref,), store)
    assert [file.reference.path for file in files] == ["review/evidence/index.json", "review/evidence/0.blob",
                                                      "review/evidence/1.blob"]
    assert files[-1].data == body and files[-1].reference.content == BlobRef.of(body)
    assert parse_json(files[0].data)["documents"][-1] == files[-1].reference.as_dict()
