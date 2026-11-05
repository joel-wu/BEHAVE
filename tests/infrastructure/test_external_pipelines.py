"""Original external workflows over shared, fully recorded infrastructure."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from behave.agent.attempt import AttemptSpec
from behave.agent.model import Charge, ModelReply
from behave.core.records import parse_json
from behave.core.storage import DirectoryStore
from behave.pipelines.generation import RoleCalls


def test_external_pool_setup_failure_releases_cpu_before_any_tool_is_started(tmp_path, monkeypatch):
    from behave.agent.cpu_resources import CpuResources
    from behave.pipelines.sandbox import PipelineSandbox
    from behave.sim import api

    class Environment:
        def execute(self, *args, **kwargs):
            pytest.fail("tool execution must not start after pool setup failed")

    def failed_pool(**kwargs):
        raise OSError("AF_UNIX path too long")

    monkeypatch.setattr(api, "prepare_tool_environment", lambda _: Environment())
    monkeypatch.setattr(api, "CpuPool", failed_pool)
    resources = CpuResources(1)
    sandbox = PipelineSandbox(tmp_path / "commands", DirectoryStore(tmp_path / "objects"),
                              cpu_resources=resources)
    for _ in range(2):
        with pytest.raises(OSError, match="AF_UNIX"):
            sandbox.execute(tmp_path, api.ToolCommand("test", ("/tools/python", "-c", "print(1)")))
        snapshot = resources.snapshot()
        assert snapshot["active"] == snapshot["retired"] == 0
        assert snapshot["available_cpus"] == snapshot["capacity"]


@pytest.mark.parametrize("role", ["v", "p"])
@pytest.mark.pipelines
def test_chipmate_repair_prompt_keeps_complete_selected_code_and_feedback(role):
    from behave.pipelines.upstream import module

    builder = getattr(module("chipmate", "prompts"), f"build_{role}_prompt")
    code = "SELECTED_CODE_START\n" + "long code line\n" * 300 + "SELECTED_CODE_END"
    feedback = "FEEDBACK_START\n" + "diagnostic line\n" * 300 + "FEEDBACK_END"
    prompt = builder("PUBLIC_TASK", 1, [code], feedback)
    assert code in prompt and feedback in prompt
    assert "truncated" not in prompt
    assert "PUBLIC_TASK" in prompt and "Please refine your" in prompt
    opening = builder("PUBLIC_TASK", 0, [code], feedback)
    assert code not in opening and feedback not in opening


@pytest.mark.pipelines
def test_chipmate_loop_keeps_role_separation_and_selected_previous_pair():
    from behave.pipelines.upstream import module

    class Backend:
        def __init__(self, language):
            self.language = language
            self.prompts = []

        def generate(self, prompt, n, temperature):
            turn = len(self.prompts)
            self.prompts.append(prompt)
            return [f"```{self.language}\n{self.language}_turn{turn}_pair{i}\n```"
                    for i in range(n)]

    verilog, python = Backend("verilog"), Backend("python")

    def verify(ref_sv, dut_sv, dut_py, *, num_tests, seed):
        assert ref_sv == "PUBLIC_PORTS" and num_tests == 1000
        assert dut_sv.split("_", 1)[1] == dut_py.split("_", 1)[1]
        index = int(dut_sv.rsplit("pair", 1)[1])
        match_rate = (0.4, 0.8, 0.6)[index] if seed == 42 else 1.0
        return {"match_rate": match_rate, "mismatches": [], "sv_error": None,
                "py_error": None, "num_tests": num_tests}

    result = module("chipmate", "inference").run_problem(
        task_id="fixture", question="PUBLIC_TASK", ref_sv="PUBLIC_PORTS",
        v_backend=verilog, p_backend=python, n=3, max_turns=3,
        num_verify_tests=1000, seed=42, verifier=verify)
    assert result.matched and result.turns_used == 2
    assert result.verilog == "verilog_turn1_pair0"
    for own, peer in ((verilog, python), (python, verilog)):
        assert len(own.prompts) == 2
        repair = own.prompts[1]
        assert f"{own.language}_turn0_pair1" in repair
        assert f"{own.language}_turn0_pair0" not in repair
        assert f"{own.language}_turn0_pair2" not in repair
        assert f"{peer.language}_turn0_pair1" not in repair


@pytest.mark.parametrize("sv_row,py_row,matched", [
    ({"q": 0}, {"q": 0}, True),
    ({"q": 1}, {"q": 0}, False),
    ({}, {}, False),
    ({"q": None}, {"q": None}, False),
    ({}, {"q": 0}, False),
    ({"q": 0}, {}, False),
    ({"q": None}, {"q": 0}, False),
    ({"q": 0}, {"q": None}, False),
])
@pytest.mark.pipelines
def test_chipmate_comparison_requires_observed_values(monkeypatch, sv_row, py_row, matched):
    from behave.pipelines.upstream import module

    verifier = module("chipmate", "cross_verify")
    monkeypatch.setattr(verifier, "simulate_verilog", lambda *a: ([sv_row], None))
    monkeypatch.setattr(verifier, "simulate_python", lambda *a: ([py_row], None))
    result = verifier.cross_verify("module TopModule(input a, output q); endmodule",
                                   "RTL", "Python", num_tests=1)
    assert result["total_checks"] == 1
    assert result["match_rate"] == int(matched)
    assert result["mismatched"] == int(not matched)
    if not matched:
        assert result["mismatches"][0]["verilog"] == sv_row.get("q")
        assert result["mismatches"][0]["python"] == py_row.get("q")


@pytest.mark.parametrize("rates,best", [((0.0, 0.0), 0), ((0.0, 0.5), 1), ((0.5, 0.0), 0)])
@pytest.mark.pipelines
def test_chipmate_zero_match_is_a_valid_candidate(rates, best):
    from behave.pipelines.upstream import module

    class Backend:
        def __init__(self, language):
            self.language = language

        def generate(self, prompt, n, temperature):
            return [f"```{self.language}\npair{i}\n```" for i in range(n)]

    def verify(ref_sv, dut_sv, dut_py, **options):
        assert dut_sv == dut_py
        return {"match_rate": rates[int(dut_sv[-1])], "mismatches": [],
                "sv_error": None, "py_error": None, "num_tests": 1}

    result = module("chipmate", "inference").run_problem(
        task_id="fixture", question="PUBLIC_TASK", ref_sv="PUBLIC_PORTS",
        v_backend=Backend("verilog"), p_backend=Backend("python"),
        n=2, max_turns=2, num_verify_tests=1, verifier=verify)
    assert not result.matched and result.turns_used == 2
    assert result.best_match_rate == rates[best] and result.best_turn == 0
    assert result.verilog == result.python == f"pair{best}"
    assert all(turn.best_pair == best and turn.best_match_rate == rates[best]
               for turn in result.turn_logs)


@pytest.mark.parametrize("language", ["verilog", "python"])
@pytest.mark.pipelines
def test_chipmate_errors_reach_repair_prompts_without_clipping(tmp_path, monkeypatch, language):
    from types import SimpleNamespace

    from behave.pipelines.upstream import module

    verifier = module("chipmate", "cross_verify")
    directory = tmp_path / "execution"
    directory.mkdir()

    class ExecutionDirectory:
        def __enter__(self):
            return str(directory)

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(verifier.tempfile, "TemporaryDirectory", ExecutionDirectory)
    stdout = "COMPILER_STDOUT\n" if language == "verilog" else ""
    stderr = "ERROR_START\n" + "diagnostic line\n" * 300 + "ERROR_END"
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout=stdout, stderr=stderr))
    simulator = verifier.simulate_verilog if language == "verilog" else verifier.simulate_python
    values, error = simulator("candidate", [("a", 1)], [("q", 1)], [{"a": 0}], False, [])
    assert values is None and error.endswith(stderr)
    feedback = module("chipmate", "feedback")
    prompts = module("chipmate", "prompts")
    result = {"sv_error": error if language == "verilog" else None,
              "py_error": error if language == "python" else None,
              "match_rate": 0.0, "mismatches": []}
    if language == "verilog":
        text = feedback.format_v_feedback(result)
        prompt = prompts.build_v_prompt("PUBLIC_TASK", 1, ["previous RTL"], text)
        assert stdout in error
    else:
        text = feedback.format_p_feedback(result)
        prompt = prompts.build_p_prompt("PUBLIC_TASK", 1, ["previous Python"], text)
    assert error in text and error in prompt


def test_role_calls_preserve_messages_context_and_all_sample_costs(tmp_path):
    requests = []

    class Backend:
        def generate(self, request):
            requests.append(request)
            return ModelReply("candidate", raw=b"provider reasoning", charge=Charge("0.01", "USD", "test"))

    store = DirectoryStore(tmp_path / "objects")
    calls = RoleCalls(tmp_path / "roles", store, AttemptSpec("trial", "solver", {"model": "fixture"}),
                      lambda sampling: (Backend(), {"model": "fixture", **sampling}))
    message = ({"role": "user", "content": "The original upstream prompt."},)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: calls.generate("RTLGenerator", message, temperature=1.0), range(4)))
    assert [reply.content for reply in replies] == ["candidate"] * 4
    assert all(tuple(dict(row) for row in request.messages) == message for request in requests)
    assert all(request.context_tokens == 131072 and not request.tools for request in requests)
    assert len({request.call_id for request in requests}) == 4
    assert calls.accounting().cost_by_currency() == {"USD": "0.04"}
    assert all(head is not None for head, _ in calls.evidence())
    from behave.agent.providers.local_chat import LocalChatEndpoint

    endpoint = LocalChatEndpoint("http://127.0.0.1:30000/v1/chat/completions", "fixture", "weights",
                                 routing="attempt", worker_urls=tuple(
                                     f"http://127.0.0.1:{31000 + i}/v1/chat/completions" for i in range(8)))
    assert len({endpoint.request_url(request) for request in requests}) == 1


def test_external_sampling_reaches_the_actual_provider_command():
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.pipelines.provider import model_factory

    endpoint = LocalChatEndpoint("http://127.0.0.1:30000/v1/chat/completions", "fixture", "test",
                                 parameters={"temperature": 0.3, "reasoning_effort": "xhigh"})
    backend, policy = model_factory(endpoint)({"temperature": 1.0, "top_p": 0.95})
    configured = json.loads(backend.command.argv[-1])
    assert configured["parameters"] == {"temperature": 1.0, "top_p": 0.95, "reasoning_effort": "xhigh"}
    assert policy["transport"] == backend.command.identity


def test_external_sampling_can_explicitly_use_provider_defaults():
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.pipelines.provider import model_factory

    endpoint = LocalChatEndpoint("http://127.0.0.1:30000/v1/chat/completions", "fixture", "test",
                                 parameters={"temperature": .3, "top_p": .95, "reasoning_effort": "xhigh"})
    backend, _ = model_factory(endpoint, provider_sampling="provider_default")({"temperature": .85})
    assert json.loads(backend.command.argv[-1])["parameters"] == {"reasoning_effort": "xhigh"}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
@pytest.mark.pipelines
def test_mage_multiturn_preserves_native_reasoning_and_serializes(provider):
    from test_hosted_providers import body, endpoint, request, request_body, response

    from behave.core.records import canonical_json, thaw_json
    from behave.pipelines.mage_transport import ChatMessage, MessageRole, ModelBinding, TokenCounter

    ep = endpoint(provider)
    native = body(provider, call=False)
    reply = response(ep, 200, canonical_json(native))
    packets, histories = [], []

    class Calls:
        def generate(self, role, messages, **sampling):
            histories.append(messages)
            packets.append(parse_json(request_body(ep, request(ep, messages))))
            return reply

    counter = TokenCounter(ModelBinding(Calls(), .85, .95))
    question = ChatMessage("original task", MessageRole.USER)
    first, _ = counter.count_chat([question])
    counter.count_chat([question, first.message, ChatMessage("repair feedback", MessageRole.USER)])
    assert thaw_json(histories[1][1]) == reply.message()
    if provider == "openai":
        assert packets[1]["input"][1:-1] == native["output"]
    elif provider == "anthropic":
        assert packets[1]["messages"][1]["content"] == native["content"]
    else:
        assert packets[1]["messages"][1]["reasoning_content"] == "keep"


def test_external_default_baseline_retains_release_sampling_policy():
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy
    from tools.pipeline_trial import baseline_policy

    saved = JudgePolicy("older-command", "older-result", seed="release-seed", baseline_cases=19,
                        sequence_length=123, score_timeout_seconds=456, search_seconds=900)
    policy = baseline_policy(saved.to_bytes())
    assert (policy.seed, policy.baseline_cases, policy.sequence_length) == ("release-seed", 19, 123)
    assert policy.score_timeout_seconds == 456 and policy.search_seconds == 0
    assert (policy.command_version, policy.authority_version) == (api.COMMAND_VERSION, api.RESULT_VERSION)


@pytest.mark.parametrize("returned_error", [False, True])
def test_external_adapter_error_retains_paid_calls_trace_and_does_not_resample(tmp_path, monkeypatch, returned_error):
    from dataclasses import replace
    from types import SimpleNamespace

    from test_ppa import file, fixture_task

    from behave.pipelines import mage, workflow

    public, _, _ = fixture_task()
    public = replace(public, files=(file("spec.md", b"# Identity\n### RTL interface\n```verilog\n"
        b"module M(input a, output q); endmodule\n```\n"), public.files[1]))
    requests = []

    class Backend:
        def generate(self, request):
            requests.append(request)
            return ModelReply("retained answer before adapter failure", charge=Charge("0.01", "USD", "test"))

    def broken(*, calls, **options):
        calls.generate("RTLGenerator", ({"role": "user", "content": "public task"},))
        if returned_error:
            return {"verilog": "", "matched": False, "upstream_error": "Exception: fixture adapter bug"}
        raise ValueError("fixture adapter bug")

    monkeypatch.setattr(mage, "run", broken)
    monkeypatch.setattr(workflow, "PipelineSandbox", lambda *args, **kwargs: SimpleNamespace(executions=[]))
    objects = DirectoryStore(tmp_path / "objects")
    arguments = dict(method="mage", settings=mage.MageSettings(), task=public,
                     spec=AttemptSpec("failure", "solver", {"model": "fixture"}),
                     backend_factory=lambda sampling: (Backend(), {"model": "fixture"}),
                     directory=tmp_path / "run", objects=objects)
    result = workflow.run(**arguments)
    assert result["status"] == "execution_error" and result["submission"] is None
    assert result["cost"] == {"USD": "0.01"} and len(result["roles"]) == 1
    trace = workflow.export_trace(result, arguments["directory"], objects)
    assert "fixture adapter bug" in trace and "retained answer before adapter failure" in trace
    assert workflow.run(**arguments) == result and len(requests) == 1


@pytest.mark.pipelines
def test_mage_editor_can_only_invoke_its_advertised_action():
    from behave.pipelines.upstream import module

    upstream = module("mage", "rtl_editor")
    editor = upstream.RTLEditor(None, None)
    editor.rtl_path = "owned/rtl.sv"
    response = editor.run_action(upstream.ActionInput(command="__setattr__", args={"name": "rtl_path", "value": "/host/target"}))
    assert not response["is_action_executed"] and editor.rtl_path == "owned/rtl.sv"


def test_external_prepare_reuses_inputs_but_not_joint_prompts(tmp_path):
    from tools.pipeline_trial import prepare

    template = {"model": "fixture", "tasks": ["before"], "workers": 1, "model_workers": 1,
                "setting": "joint", "attempt": {"max_turns": 60, "context_tokens": 131072}}
    (tmp_path / "template.json").write_text(json.dumps(template))
    (tmp_path / "tasks.txt").write_text("one\ntwo\n")
    output = tmp_path / "prepared"
    result = prepare(output, tmp_path / "template.json", "chipmate", tasks=tmp_path / "tasks.txt", workers=2)
    settings = json.loads((output / "settings.json").read_text())
    assert settings["tasks"] == ["one", "two"] and settings["setting"] == "rtl_only"
    assert settings["score_on_submit"] is False and settings["attempt"] == template["attempt"]
    assert result["options"]["pairs"] == 3 and result["options"]["turns"] == 3
    assert (output / "pipeline.json").is_file()


def test_external_cli_can_select_provider_default_sampling(tmp_path, monkeypatch):
    import sys

    from tools import pipeline_trial as trial

    template = tmp_path / "template.json"
    template.write_text(json.dumps({"model": "fixture", "tasks": ["one"], "workers": 1,
                                   "attempt": {"max_turns": 60}}))
    output = tmp_path / "prepared"
    monkeypatch.setattr(sys, "argv", ["pipeline_trial", "prepare", str(output),
        "--template", str(template), "--method", "mage", "--provider-sampling", "provider_default"])
    trial.main()
    assert json.loads((output / "pipeline.json").read_text())["provider_sampling"] == "provider_default"


def test_external_local_staging_and_archive_are_not_per_turn(tmp_path):
    import os

    from tools.pipeline_trial import local_run

    output = tmp_path / "archive"
    output.mkdir()
    (output / "settings.json").write_text("{}")
    original_scratch = os.environ.get("BEHAVE_SCRATCH_DIR")
    (output / "control").mkdir()
    (output / "control/status.json").write_text('"running"')
    with local_run(output, "rollout") as (local, stopped):
        assert local != output and not local.is_relative_to(output)
        assert not (local / "control").exists()
        (output / "control/status.json").write_text('"supervisor progressed"')
        scratch = Path(os.environ["BEHAVE_SCRATCH_DIR"])
        assert scratch == local / "scratch"
        (scratch / "temporary-build.log").write_text("intermediate tool output")
        assert not stopped.is_set()
        (local / "result.json").write_text('{"status":"completed"}')
        assert not (output / "result.json").exists()
    assert not local.exists()
    assert json.loads((output / "result.json").read_text())["status"] == "completed"
    assert not (output / "scratch").exists()
    assert os.environ.get("BEHAVE_SCRATCH_DIR") == original_scratch
    assert (output / "control/status.json").read_text() == '"supervisor progressed"'


def test_external_staging_does_not_copy_its_own_destination(tmp_path, monkeypatch):
    from tools.pipeline_trial import local_run

    output = tmp_path / "node-local-run"
    output.mkdir()
    (output / "settings.json").write_text("{}")
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(output))
    with local_run(output, "rollout") as (local, _):
        assert local.parent == output
        assert not (local / local.name).exists()
        assert (local / "settings.json").read_text() == "{}"


def test_external_score_stages_only_requested_judgment(tmp_path, monkeypatch):
    from tools import pipeline_trial as trial

    output = tmp_path / "archive"
    trial.write_json(output / "settings.json", {})
    trial.write_json(output / "candidates/task/result.json", {"submission": "kept"})
    trial.write_json(output / "candidates/task/trace.md", {"large": "model history"})
    trial.write_json(output / "objects/unused", {"large": "command output"})
    trial.write_json(output / "inputs/unused", {"another": "task"})
    trial.write_json(output / "judgments/baseline/task/journal/000000.json", {"saved": True})
    trial.write_json(output / "judgments/other/task/result.json", {"other": True})
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(output))
    with trial.local_run(output, "score", judgment="baseline") as (local, _):
        assert (local / "judgments/baseline/task/journal/000000.json").exists()
        assert not (local / "judgments/other").exists()
        assert not (local / "candidates").exists()
        assert not (local / "objects").exists()
        assert not (local / "inputs").exists()
        assert not (local / local.name).exists()
        trial.write_json(local / "judgments/baseline/task/result.json", {"rtl": 1})
    assert json.loads((output / "judgments/baseline/task/result.json").read_text()) == {"rtl": 1}
    assert (output / "candidates/task/trace.md").exists()
    assert (output / "objects/unused").exists()
    assert (output / "judgments/other/task/result.json").exists()


@pytest.mark.isolation
def test_native_external_sandbox_concurrency_logs_and_child_timeout(tmp_path):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from behave.pipelines.sandbox import PipelineSandbox
    from behave.sim import api

    objects = DirectoryStore(tmp_path / "objects")
    sandbox = PipelineSandbox(tmp_path / "commands", objects)
    private = tmp_path / "host-only.txt"
    private.write_text("not mounted")

    def one(number):
        workspace = tmp_path / str(number)
        workspace.mkdir()
        (workspace / "input.txt").write_text(str(number))
        code = (f"from pathlib import Path; assert not Path({str(private)!r}).exists(); "
                f"assert Path('input.txt').read_text() == {str(number)!r}; "
                f"print({str(number)!r})")
        result, stdout, _ = sandbox.execute(workspace, api.ToolCommand("parallel", ("/tools/python", "-c", code)))
        assert result.commands[0]["returncode"] == 0
        return stdout.strip()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(one, range(2))) == ["0", "1"]

    workspace = tmp_path / "timeout"
    workspace.mkdir()
    child = "import time; from pathlib import Path; time.sleep(2); Path('child-survived').write_text('bad')"
    parent = f"import subprocess,time; subprocess.Popen(['/tools/python', '-c', {child!r}]); time.sleep(30)"
    result, _, _ = sandbox.execute(workspace, api.ToolCommand("timeout", ("/tools/python", "-c", parent), timeout_s=1))
    assert result.commands[0]["timed_out"]
    time.sleep(2)
    assert not (workspace / "child-survived").exists()
    result, stdout, _ = sandbox.execute(workspace, api.ToolCommand("logs", ("/tools/python", "-c",
        "import sys; sys.stdout.write('x' * (9 << 20) + 'FINAL=42\\n')")))
    assert result.commands[0]["returncode"] == 0
    assert stdout == "x" * (9 << 20) + "FINAL=42\n"
    assert len(sandbox.executions) == 4
    assert all(objects.read(ref) is not None for group in sandbox.executions for ref in group)


@pytest.mark.isolation
@pytest.mark.pipelines
def test_native_chipmate_original_loop_cross_verifies_inside_sandbox(tmp_path):
    from behave.pipelines.chipmate import ChipmateSettings, run
    from behave.pipelines.sandbox import PipelineSandbox

    class Backend:
        def generate(self, request):
            if ":python:" in request.call_id:
                answer = '```python\nclass TopModule:\n    def eval(self, inputs):\n        return {"q": inputs["a"]}\n```'
            else:
                answer = "```verilog\nmodule M(input [7:0] a,output [7:0] q); assign q=a; endmodule\n```"
            return ModelReply(answer, charge=Charge("0.01", "USD", "test"))

    store = DirectoryStore(tmp_path / "objects")
    calls = RoleCalls(tmp_path / "roles", store, AttemptSpec("trial", "solver", {"model": "fixture"}),
                      lambda sampling: (Backend(), {"model": "fixture", **sampling}))
    sandbox = PipelineSandbox(tmp_path / "commands", store)
    result = run(task_id="identity", question="Implement module M, unsigned 8-bit input a and output q=a.",
                 interface="module M(input [7:0] a, output [7:0] q); endmodule", calls=calls, sandbox=sandbox,
                 settings=ChipmateSettings(pairs=2, turns=2, stimuli=16))
    assert result.matched and result.best_match_rate == 1 and result.turns_used == 1
    assert len(calls.journals) == 4
    assert len(sandbox.executions) == 2
    assert calls.accounting().cost_by_currency() == {"USD": "0.04"}
    for evidence in sandbox.executions:
        receipt = parse_json(store.read(evidence[0]))
        assert receipt["status"] == "completed", receipt
        assert receipt["commands"][0]["returncode"] == 0, receipt


@pytest.mark.isolation
@pytest.mark.pipelines
def test_native_mage_original_no_golden_roles_and_simulation(tmp_path):
    from behave.pipelines.mage import run
    from behave.pipelines.sandbox import PipelineSandbox

    rtl = "module M(input [7:0] a,output [7:0] q); assign q=a; endmodule"
    tb = '''module tb;
      reg [7:0] a; wire [7:0] q; M dut(a,q);
      initial begin a=42; #1;
        if (q==42) $display("SIMULATION PASSED");
        else $display("SIMULATION FAILED - 1 MISMATCHES DETECTED");
        $finish;
      end
    endmodule'''
    requests = []

    class Backend:
        def generate(self, request):
            requests.append(request)
            answer = ({"reasoning": "Test the public identity specification.", "testbench": tb,
                       "interface": "module M(input [7:0] a,output [7:0] q); endmodule"}
                      if ":TBGenerator:" in request.call_id else
                      {"reasoning": "Assign input to output.", "module": rtl})
            return ModelReply(json.dumps(answer), charge=Charge("0.01", "USD", "test"))

    store = DirectoryStore(tmp_path / "objects")
    calls = RoleCalls(tmp_path / "roles", store, AttemptSpec("trial", "solver", {"model": "fixture"}),
                      lambda sampling: (Backend(), {"model": "fixture", **sampling}))
    sandbox = PipelineSandbox(tmp_path / "commands", store)
    result = run(question="Implement module M, unsigned 8-bit input a and output q=a.",
                 workspace=tmp_path / "workspace", calls=calls, sandbox=sandbox)
    assert result["matched"] and result["verilog"] == rtl and result["upstream_error"] is None
    assert len(requests) == 2 and len(sandbox.executions) == 2
    assert [call.call_id.split(":")[1] for call in requests] == ["TBGenerator", "RTLGenerator"]
    assert calls.accounting().cost_by_currency() == {"USD": "0.02"}


@pytest.mark.isolation
@pytest.mark.pipelines
def test_native_mage_original_candidate_selection_and_editor(tmp_path):
    from behave.pipelines.mage import MageSettings, run
    from behave.pipelines.sandbox import PipelineSandbox

    bad = "module M(input [7:0] a,output [7:0] q); assign q=0; endmodule"
    tb = '''module tb; reg [7:0] a=42; wire [7:0] q; M dut(a,q);
    initial begin #1; if(q==42) $display("SIMULATION PASSED");
    else $display("SIMULATION FAILED - 1 MISMATCHES DETECTED"); $finish; end endmodule'''
    requests = []

    class Backend:
        def generate(self, request):
            requests.append(request)
            if ":TBGenerator:" in request.call_id:
                value = {"reasoning": "test identity", "testbench": tb,
                         "interface": "module M(input [7:0] a,output [7:0] q); endmodule"}
            elif ":SimJudge:" in request.call_id:
                value = {"reasoning": "The identity test is correct.", "tb_needs_fix": False}
            elif ":RTLEditor:" in request.call_id:
                value = {"reasoning": "Replace the zero with the input.", "action_input": {
                    "command": "replace_content_by_matching", "args": {"old_content": "assign q=0;", "new_content": "assign q=a;"}}}
            else:
                value = {"reasoning": "initial candidate", "module": bad}
            return ModelReply(json.dumps(value), charge=Charge("0.01", "USD", "test"))

    store = DirectoryStore(tmp_path / "objects")
    calls = RoleCalls(tmp_path / "roles", store, AttemptSpec("trial", "solver", {"model": "fixture"}),
                      lambda sampling: (Backend(), {"model": "fixture", **sampling}))
    sandbox = PipelineSandbox(tmp_path / "commands", store)
    result = run(question="Module M: unsigned 8-bit q equals a.", workspace=tmp_path / "workspace",
                 calls=calls, sandbox=sandbox, settings=MageSettings(candidates=2))
    assert result["matched"] and "assign q=a;" in result["verilog"]
    assert len(requests) == 6 and len(sandbox.executions) == 8
    assert calls.accounting().cost_by_currency() == {"USD": "0.06"}


@pytest.mark.isolation
@pytest.mark.pipelines
def test_native_chipmate_repair_keeps_original_selection_and_rejected_costs(tmp_path):
    from threading import Lock

    from behave.pipelines.chipmate import ChipmateSettings, run
    from behave.pipelines.sandbox import PipelineSandbox

    requests = []
    lock = Lock()

    class Backend:
        def generate(self, request):
            with lock:
                requests.append(request)
            if ":python:" in request.call_id:
                answer = '```python\nclass TopModule:\n    def eval(self, inputs):\n        return {"q": inputs["a"]}\n```'
            else:
                iteration = int(request.call_id.split(":verilog:")[1].split(":")[0])
                expression = "0" if iteration < 2 else "a"
                answer = f"```verilog\nmodule M(input [7:0] a,output [7:0] q); assign q={expression}; endmodule\n```"
            return ModelReply(answer, charge=Charge("0.01", "USD", "test"))

    store = DirectoryStore(tmp_path / "objects")
    calls = RoleCalls(tmp_path / "roles", store, AttemptSpec("trial", "solver", {"model": "fixture"}),
                      lambda sampling: (Backend(), {"model": "fixture", **sampling}))
    sandbox = PipelineSandbox(tmp_path / "commands", store)
    result = run(task_id="identity", question="Implement M with q=a, unsigned 8-bit ports.",
                 interface="module M(input [7:0] a, output [7:0] q); endmodule", calls=calls, sandbox=sandbox,
                 settings=ChipmateSettings(pairs=2, turns=2, stimuli=16))
    assert result.matched and result.turns_used == 2
    assert len(requests) == 8 and len(sandbox.executions) == 4
    assert calls.accounting().cost_by_currency() == {"USD": "0.08"}
    assert "q=a" in result.verilog


@pytest.mark.isolation
@pytest.mark.pipelines
def test_native_external_submission_separate_baseline_and_cold_replay(tmp_path):
    from dataclasses import replace

    from behave.core.artifacts import BlobRef
    from behave.pipelines.mage import MageSettings
    from behave.pipelines.public_inputs import chipmate_interface
    from behave.pipelines.workflow import export_trace, run, score
    from behave.task_execution.capture import load_submission
    from behave.task_execution.simulator_judge import SimulatorJudge
    from tests.infrastructure.test_ppa import RTL, file, fixture_task

    public, judge, _ = fixture_task()
    spec = """# Identity\n\n### RTL interface\n```systemverilog
module M(input clk, rst, in_valid, out_ready, input [7:0] in_a,
output in_ready, out_valid, output [7:0] out_y);
```
### Behavior interface
PRIVATE_MODEL_INTERFACE_DO_NOT_PROMPT
### Transaction protocol
One output per accepted input, combinational identity, reset suppresses transfers.
"""
    public = replace(public, files=(file("spec.md", spec.encode()), public.files[1]))
    top, interface = chipmate_interface(spec)
    assert top == "M" and "input [7:0] in_a" in interface and "output [7:0] out_y" in interface
    tb = """module tb;
    reg clk=0,rst=0,in_valid=1,out_ready=1; reg [7:0] in_a=42;
    wire in_ready,out_valid; wire [7:0] out_y;
    M dut(clk,rst,in_valid,out_ready,in_a,in_ready,out_valid,out_y);
    initial begin #1; if(out_y==42 && out_valid) $display("SIMULATION PASSED");
    else $display("SIMULATION FAILED - 1 MISMATCHES DETECTED"); $finish; end
    endmodule"""
    requests = []

    class Backend:
        def generate(self, request):
            requests.append(request)
            assert "PRIVATE_MODEL_INTERFACE_DO_NOT_PROMPT" not in str(request.messages)
            answer = ({"reasoning": "Test identity", "testbench": tb, "interface": interface.replace("TopModule", "M")}
                      if ":TBGenerator:" in request.call_id else {"reasoning": "Identity", "module": RTL.decode()})
            return ModelReply(json.dumps(answer), charge=Charge("0.01", "USD", "test"))

    store = DirectoryStore(tmp_path / "objects")
    kwargs = dict(method="mage", settings=MageSettings(), task=public,
                  spec=AttemptSpec("trial", "solver", {"model": "fixture"}),
                  backend_factory=lambda sampling: (Backend(), {"model": "fixture", **sampling}),
                  directory=tmp_path / "rollout", objects=store)
    result = run(**kwargs)
    assert result["status"] == "completed" and len(requests) == 2
    frozen = load_submission(store, BlobRef.from_dict(result["submission"]))
    assert frozen.setting == "rtl_only" and frozen.rtl.selection.entry_symbol == "M"
    assert frozen.behavior.selection is None and frozen.testbench.selection is None
    assert [file.path for file in frozen.rtl.files] == ["rtl.sv"]
    assert "TBGenerator" in export_trace(result, kwargs["directory"], store)
    assert run(**kwargs) == result and len(requests) == 2
    judgment = score(task=judge, result=result, directory=tmp_path / "judge", objects=store, backend=SimulatorJudge())
    assert judgment.rtl.score == 1 and judgment.behavior is None

    class NeverJudge:
        identity = SimulatorJudge.identity

        def evaluate(self, *args):
            raise AssertionError("completed judgment must not dispatch again")

    assert score(task=judge, result=result, directory=tmp_path / "judge", objects=store,
                 backend=NeverJudge()) == judgment


@pytest.mark.isolation
def test_native_external_operator_rollout_then_score(tmp_path, monkeypatch):
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision
    from tests.infrastructure.test_ppa import RTL, file, fixture_task
    from tools import pipeline_trial as trial

    _, judge, _ = fixture_task()
    source = DirectoryStore(tmp_path / "published")
    spec = file("spec.md", b"""# Identity\n### RTL interface\n```systemverilog
module M(input clk, rst, in_valid, out_ready, input [7:0] in_a,
output in_ready, out_valid, output [7:0] out_y);
```
### Behavior interface
PRIVATE MODEL INTERFACE
### Protocol
One accepted input gives the same output, in order. Reset suppresses transfers.
""")
    for item in (spec, judge.contract, *judge.reference_behavior.files):
        source.put(item.data)
    manifest = TaskManifest("identity", spec.reference, judge.contract.reference,
        judge.reference_behavior.artifact, source.put(judge.run_policy), source.put(judge.judge_policy))
    release = ReleaseManifest("external-fixture", (TaskRevision("identity", source.put(manifest.to_bytes())),),
                              source.put(b"fixture release"))
    endpoint = LocalChatEndpoint("http://127.0.0.1:30000/v1/chat/completions", "fixture", "test")
    template = {"provider": "sglang", "endpoint": endpoint.as_dict(), "model": "fixture",
                "task_objects": str(source.directory), "release": source.put(release.to_bytes()).as_dict(),
                "tasks": ["identity"], "workers": 1, "model_workers": 4, "judge_workers": 1,
                "attempt": {"max_turns": 60, "context_tokens": 131072}}
    trial.write_json(tmp_path / "template.json", template)
    output = tmp_path / "trial"
    trial.prepare(output, tmp_path / "template.json", "mage")
    requests = []
    tb = '''module tb; reg clk=0,rst=0,in_valid=1,out_ready=1; reg [7:0] in_a=42;
    wire in_ready,out_valid; wire [7:0] out_y;
    M dut(clk,rst,in_valid,out_ready,in_a,in_ready,out_valid,out_y);
    initial begin #1; if(out_y==42 && out_valid) $display("SIMULATION PASSED");
    else $display("SIMULATION FAILED - 1 MISMATCHES DETECTED"); $finish; end endmodule'''

    class Backend:
        def generate(self, request):
            requests.append(request)
            assert "PRIVATE MODEL INTERFACE" not in str(request.messages)
            value = ({"reasoning": "identity test", "testbench": tb, "interface": RTL.decode()}
                     if ":TBGenerator:" in request.call_id else {"reasoning": "identity", "module": RTL.decode()})
            return ModelReply(json.dumps(value), charge=Charge("0.01", "USD", "test"))

    monkeypatch.setattr("behave.pipelines.provider.model_factory", lambda endpoint, **options:
                        lambda sampling: (Backend(), {"model": "fixture", **sampling}))
    rolled = trial.rollout(output)
    assert rolled[0]["status"] == "completed" and len(requests) == 2
    assert (output / "candidates/identity/trace.md").is_file()
    (tmp_path / "policy.json").write_bytes(judge.judge_policy)
    scored = trial.score(output, policy_file=tmp_path / "policy.json")
    assert scored[0]["rtl"]["score"] == 1 and scored[0]["behavior"] is None
    assert trial.rollout(output) == rolled and len(requests) == 2
    assert trial.score(output, policy_file=tmp_path / "policy.json") == scored
