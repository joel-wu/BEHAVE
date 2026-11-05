"""MAGE failure paths keep evidence and RTL without adding model retries."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.pipelines.upstream import module


pytestmark = pytest.mark.pipelines

RTL = "module M(input a, output q); assign q=a; endmodule"


def reply(value):
    from behave.pipelines.mage_transport import ChatMessage, ChatResponse, MessageRole

    content = value if isinstance(value, str) else json.dumps(value)
    return ChatResponse(ChatMessage(content, MessageRole.ASSISTANT))


def counter():
    return SimpleNamespace(set_cur_tag=Mock())


@pytest.mark.parametrize("success,stdout,stderr,passed,count", [
    (True, "SIMULATION PASSED", "", True, 0),
    (True, "SIMULATION PASSED", "tb.sv:1: warning: no explicit time unit", True, 0),
    (True, "SIMULATION FAILED - 3 MISMATCHES DETECTED", "", False, 3),
    (True, "SIMULATION PASSED\nSIMULATION FAILED - 3 MISMATCHES DETECTED", "", False, 3),
    (True, "SIMULATION FAILED: 3 errors", "", False, None),
    (True, "SIMULATION FAILED -  MISMATCHES DETECTED", "", False, None),
    (True, "", "", False, None),
    (False, "SIMULATION PASSED", "fatal execution error", False, None),
    (False, "SIMULATION FAILED - 3 MISMATCHES DETECTED", "Timeout 60s reached.", False, None),
])
def test_simulation_status_does_not_invent_zero_mismatches(
        tmp_path, monkeypatch, success, stdout, stderr, passed, count):
    reviewer = module("mage", "sim_reviewer")
    output = json.dumps({"stdout": stdout, "stderr": stderr})
    command = Mock(return_value=(success, output))
    monkeypatch.setattr(reviewer, "run_bash_command", command)
    assert reviewer.sim_review(str(tmp_path)) == (passed, count, output)
    assert " && vvp " in command.call_args.args[0]
    assert command.call_args.kwargs == {"timeout": 60}


@pytest.mark.parametrize("success", [False, True])
def test_syntax_check_uses_compiler_status_and_preserves_warning(monkeypatch, success):
    reviewer = module("mage", "sim_reviewer")
    output = json.dumps({"stdout": "", "stderr": "rtl.sv:1: warning: no explicit time unit"})
    monkeypatch.setattr(reviewer, "run_bash_command", Mock(return_value=(success, output)))
    assert reviewer.check_syntax("rtl.sv") == (success, output)


@pytest.mark.isolation
@pytest.mark.parametrize("statement,passed,count", [
    ('$display("SIMULATION PASSED");', True, 0),
    ('$display("SIMULATION FAILED - 3 MISMATCHES DETECTED");', False, 3),
    ('$display("SIMULATION PASSED"); $display("SIMULATION FAILED - 3 MISMATCHES DETECTED");', False, 3),
    ('$display("SIMULATION FAILED: malformed summary");', False, None),
    ('$display("no summary");', False, None),
    ('$display("SIMULATION PASSED"); $fatal(1, "runtime failure");', False, None),
])
def test_native_mage_simulation_status_with_compiler_warning(tmp_path, statement, passed, count):
    from behave.core.storage import DirectoryStore
    from behave.pipelines.mage_transport import _TOOLS
    from behave.pipelines.sandbox import PipelineSandbox

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rtl.sv").write_text(
        "module M(input [7:0] a, output [7:0] q); assign q=a; endmodule")
    # The intentional input width mismatch produces a harmless compiler warning.
    (workspace / "tb.sv").write_text(
        "module tb; reg a; wire [7:0] q; M dut(a,q); initial begin a=1; #1; "
        + statement + " $finish; end endmodule")
    sandbox = PipelineSandbox(tmp_path / "commands", DirectoryStore(tmp_path / "objects"))
    token = _TOOLS.set((sandbox, workspace))
    try:
        actual_passed, actual_count, output = module("mage", "sim_reviewer").sim_review(str(workspace))
    finally:
        _TOOLS.reset(token)
    assert (actual_passed, actual_count) == (passed, count)
    assert "warning:" in json.loads(output)["stderr"]
    assert len(sandbox.executions) == 1


@pytest.mark.parametrize("value", ["not JSON", {}, {"reasoning": "r", "module": 3}])
@pytest.mark.parametrize("ablation", [False, True])
def test_invalid_rtl_replies_end_normally_with_original_budget(tmp_path, value, ablation):
    generator = module("mage", "rtl_generator").RTLGenerator(counter())
    response = reply(value)
    generator.generate = Mock(return_value=response)
    if ablation:
        result = generator.ablation_chat("SPEC", str(tmp_path / "rtl.sv"))
    else:
        result = generator.chat("SPEC", "TB", "INTERFACE", str(tmp_path / "rtl.sv"))
    assert result == (False, "")
    assert generator.generate.call_count == generator.max_trials == 5
    second_request = generator.generate.call_args_list[1].args[0]
    assert response.message in second_request
    assert any("Invalid RTL response:" in message.content for message in second_request)


def test_rtl_format_error_keeps_last_source_and_can_recover(tmp_path, monkeypatch):
    upstream = module("mage", "rtl_generator")
    generator = upstream.RTLGenerator(counter())
    original = reply({"reasoning": "first", "module": "BAD_RTL"})
    generator.generate = Mock(side_effect=[original, reply({}), reply({"reasoning": "fix", "module": RTL})])
    checker = Mock(side_effect=[(False, "syntax error"), (True, "")])
    monkeypatch.setattr(upstream, "check_syntax", checker)
    assert generator.chat("SPEC", "TB", "INTERFACE", str(tmp_path / "rtl.sv")) == (True, RTL)
    assert generator.generate.call_count == 3 and checker.call_count == 2
    assert (tmp_path / "rtl.sv").read_text() == RTL


def test_invalid_final_rtl_reply_preserves_preceding_source(tmp_path, monkeypatch):
    upstream = module("mage", "rtl_generator")
    generator = upstream.RTLGenerator(counter())
    generator.generate = Mock(side_effect=[reply({"reasoning": "first", "module": RTL})] + [reply({})] * 4)
    monkeypatch.setattr(upstream, "check_syntax", Mock(return_value=(False, "syntax error")))
    assert generator.chat("SPEC", "TB", "INTERFACE", str(tmp_path / "rtl.sv")) == (False, RTL)
    assert (tmp_path / "rtl.sv").read_text() == RTL


def test_candidate_repairs_keep_every_assistant_message(tmp_path, monkeypatch):
    from behave.pipelines.mage_transport import MessageRole

    upstream = module("mage", "rtl_generator")
    generator = upstream.RTLGenerator(counter())
    responses = [reply({"reasoning": "r", "module": code}) for code in ("BAD_0", "BAD_1", RTL)]
    responses[1].message.additional_kwargs = {"continuation": {"native": "retained"}}
    generator.batch_generate = Mock(return_value=responses[:1])
    generator.generate = Mock(side_effect=responses[1:])
    monkeypatch.setattr(upstream, "check_syntax", Mock(side_effect=[
        (False, "error 0"), (False, "error 1"), (True, "")]))
    assert generator.gen_candidates("SPEC", "TB", "INTERFACE", str(tmp_path / "rtl.sv"), 1) == [(True, RTL)]
    second_repair = generator.generate.call_args_list[1].args[0]
    assistants = [m for m in second_repair if m.role == MessageRole.ASSISTANT]
    assert assistants == [responses[0].message, responses[1].message]
    assert assistants[1].additional_kwargs == {"continuation": {"native": "retained"}}


def test_invalid_candidate_replies_keep_original_per_candidate_budget(tmp_path):
    generator = module("mage", "rtl_generator").RTLGenerator(counter())
    generator.batch_generate = Mock(return_value=[reply({}), reply("not JSON")])
    generator.generate = Mock(return_value=reply({}))
    assert generator.gen_candidates("SPEC", "TB", "INTERFACE", str(tmp_path / "rtl.sv"), 2) == [(False, ""), (False, "")]
    assert generator.generate.call_count == 2 * (generator.max_trials - 1)


@pytest.mark.parametrize("value", ["not JSON", {}, {"reasoning": "r", "interface": 3, "testbench": "TB"}])
def test_invalid_tb_replies_do_not_raise_or_extend_budget(value):
    generator = module("mage", "tb_generator").TBGenerator(counter())
    generator.generate = Mock(return_value=reply(value))
    assert generator.chat("SPEC") == ("", "")
    assert generator.generate.call_count == generator.json_decode_max_trial == 3
    assert any("Invalid TB response:" in m.content for m in generator.generate.call_args_list[1].args[0])


def test_tb_format_feedback_allows_repair():
    generator = module("mage", "tb_generator").TBGenerator(counter())
    generator.generate = Mock(side_effect=[reply({}), reply({"reasoning": "fixed", "interface": "IF", "testbench": "TB"})])
    assert generator.chat("SPEC") == ("TB", "IF")
    assert generator.generate.call_count == 2


@pytest.mark.parametrize("value,expected", [
    ({}, None), ("not JSON", None),
    ({"reasoning": "RTL bug", "tb_needs_fix": False}, False),
    ({"reasoning": "TB bug", "tb_needs_fix": True}, True),
])
def test_sim_judge_never_guesses_invalid_decisions(value, expected):
    judge = module("mage", "sim_judge").SimJudge(counter())
    judge.generate = Mock(return_value=reply(value))
    assert judge.chat("SPEC", "LOG", "RTL", "TB") is expected
    assert judge.generate.call_count == 1


def agent_fixture(tmp_path):
    agent = module("mage", "agent").TopAgent(None)
    agent.output_dir_per_run = str(tmp_path)
    agent.rtl_max_candidates = 2
    agent.tb_gen = SimpleNamespace(reset=Mock(), set_golden_tb_path=Mock(), set_failed_trial=Mock(),
                                   chat=Mock(return_value=("TB", "INTERFACE")))
    agent.rtl_gen = SimpleNamespace(reset=Mock(), chat=Mock(side_effect=[(True, RTL), (False, "BAD")]),
                                    gen_candidates=Mock(return_value=[(False, "BAD")]))
    agent.sim_judge = SimpleNamespace(reset=Mock(), chat=Mock(return_value=False))
    agent.sim_reviewer = SimpleNamespace(review=Mock(return_value=(False, 2, "mismatches")))
    agent.rtl_edit = SimpleNamespace(reset=Mock(), chat=Mock())
    return agent


def test_all_bad_candidates_preserve_initial_rtl(tmp_path):
    agent = agent_fixture(tmp_path)
    assert agent.run_instance("SPEC") == (False, RTL)
    assert (tmp_path / "rtl.sv").read_text() == RTL
    agent.rtl_edit.chat.assert_not_called()


def test_tb_repair_exhaustion_keeps_rtl_and_does_not_generate_untested_tb(tmp_path):
    agent = agent_fixture(tmp_path)
    agent.sim_judge.chat.return_value = True
    assert agent.run_instance("SPEC") == (False, RTL)
    assert agent.sim_reviewer.review.call_count == agent.sim_max_retry == 4
    assert agent.tb_gen.chat.call_count == 4
    agent.rtl_gen.gen_candidates.assert_not_called()
    assert (tmp_path / "rtl.sv").read_text() == RTL


@pytest.mark.parametrize("stage", ["initial_tb", "revised_tb", "sim_judge"])
def test_invalid_role_response_finishes_with_available_rtl(tmp_path, stage):
    agent = agent_fixture(tmp_path)
    expected = RTL
    if stage == "initial_tb":
        agent.tb_gen.chat.return_value = ("", "")
        expected = ""
    elif stage == "revised_tb":
        agent.sim_judge.chat.return_value = True
        agent.tb_gen.chat.side_effect = [("TB", "INTERFACE"), ("", "")]
    else:
        agent.sim_judge.chat.return_value = None
    assert agent.run_instance("SPEC") == (False, expected)
    agent.rtl_edit.chat.assert_not_called()
    if expected:
        assert (tmp_path / "rtl.sv").read_text() == RTL


def test_incomplete_simulation_cannot_outrank_counted_candidate(tmp_path):
    agent = agent_fixture(tmp_path)
    agent.rtl_gen.chat.side_effect = [(True, RTL), (True, "UNMEASURED_RTL")]
    agent.rtl_gen.gen_candidates.return_value = [(True, "MEASURED_RTL")]
    agent.sim_reviewer.review.side_effect = [
        (False, None, "initial timeout"), (False, None, "candidate timeout"), (False, 4, "mismatches")]
    agent.rtl_edit.chat.return_value = (True, "MEASURED_RTL")
    assert agent.run_instance("SPEC") == (True, "MEASURED_RTL")
    assert agent.rtl_edit.chat.call_args.kwargs["sim_mismatch_cnt"] == 4
    assert (tmp_path / "rtl.sv").read_text() == "MEASURED_RTL"


def test_passing_candidate_is_not_simulated_again(tmp_path):
    agent = agent_fixture(tmp_path)
    agent.rtl_gen.chat.side_effect = [(True, RTL), (True, "PASSING_RTL")]
    agent.sim_reviewer.review.side_effect = [(False, 1, "mismatch"), (True, 0, "passed")]
    assert agent.run_instance("SPEC") == (True, "PASSING_RTL")
    assert agent.sim_reviewer.review.call_count == 2
    agent.rtl_edit.chat.assert_not_called()


@pytest.mark.parametrize("args,diagnostic", [
    ({"old_content": "x"}, "new_content"),
    ({"old_content": "x", "new_content": "y", "extra": "z"}, "extra"),
    ({"old_content": 1, "new_content": "y"}, "old_content must be a string"),
])
def test_bad_editor_arguments_return_feedback_without_editing(tmp_path, args, diagnostic):
    upstream = module("mage", "rtl_editor")
    editor = upstream.RTLEditor(counter(), None)
    editor.rtl_path = str(tmp_path / "rtl.sv")
    Path(editor.rtl_path).write_text(RTL)
    result = editor.run_action(upstream.ActionInput(command="replace_content_by_matching", args=args))
    assert result["is_action_executed"] is False and diagnostic in result["error_msg"]
    assert Path(editor.rtl_path).read_text() == RTL


def test_editor_invalid_json_and_arguments_use_existing_turns_then_recover(tmp_path, monkeypatch):
    upstream = module("mage", "rtl_editor")
    editor = upstream.RTLEditor(counter(), SimpleNamespace(review=Mock(return_value=(True, 0, "passed"))))
    (tmp_path / "rtl.sv").write_text(RTL)
    (tmp_path / "tb.sv").write_text("TB")
    editor.generate = Mock(side_effect=[reply({}), reply({"reasoning": "r", "action_input": {
        "command": "replace_content_by_matching", "args": {"old_content": "q=a"}}}),
        reply({"reasoning": "fix", "action_input": {"command": "replace_content_by_matching",
              "args": {"old_content": "q=a", "new_content": "q=~a"}}})])
    monkeypatch.setattr(upstream, "check_syntax", Mock(return_value=(True, "")))
    assert editor.chat("SPEC", str(tmp_path), "mismatch", 1) == (True, RTL.replace("q=a", "q=~a"))
    assert editor.generate.call_count == 3
    assert any("Invalid editor response:" in m.content for m in editor.generate.call_args_list[1].args[0])
    assert any("new_content" in m.content and "Invalid action arguments:" in m.content
               for m in editor.generate.call_args_list[2].args[0])


def test_editor_invalid_replies_exhaust_without_losing_rtl(tmp_path):
    editor = module("mage", "rtl_editor").RTLEditor(counter(), None)
    (tmp_path / "rtl.sv").write_text(RTL)
    (tmp_path / "tb.sv").write_text("TB")
    editor.generate = Mock(return_value=reply({}))
    assert editor.chat("SPEC", str(tmp_path), "mismatch", 1) == (False, RTL)
    assert editor.generate.call_count == editor.max_trials == 15


def test_incomplete_editor_simulation_rolls_back(tmp_path, monkeypatch):
    upstream = module("mage", "rtl_editor")
    reviewer = SimpleNamespace(review=Mock(return_value=(False, None, "Timeout 60s reached.")))
    editor = upstream.RTLEditor(counter(), reviewer)
    editor.rtl_path = str(tmp_path / "rtl.sv")
    Path(editor.rtl_path).write_text(RTL)
    editor.last_mismatch_cnt = 3
    monkeypatch.setattr(upstream, "check_syntax", Mock(return_value=(True, "")))
    result = editor.replace_content_by_matching("q=a", "q=~a")
    assert result["is_action_executed"] is False and result["sim_mismatch_cnt"] is None
    assert "Timeout" in result["error_msg"]
    assert Path(editor.rtl_path).read_text() == RTL
    assert not editor.is_done and editor.last_mismatch_cnt == 3


def test_model_interruption_still_propagates(tmp_path):
    generator = module("mage", "rtl_generator").RTLGenerator(counter())
    generator.generate = Mock(side_effect=AttemptInterrupted("cancelled"))
    with pytest.raises(AttemptInterrupted, match="cancelled"):
        generator.chat("SPEC", "TB", "INTERFACE", str(tmp_path / "rtl.sv"))
