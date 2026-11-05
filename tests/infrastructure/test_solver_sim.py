"""The normal solver loop with Sim development feedback and no submitted TB."""

import shlex
import sys
from dataclasses import replace

import pytest

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.model import ModelReply, ToolCall
from behave.agent.process_model import ModelCommand
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.records import RecordError, parse_json
from behave.core.storage import DirectoryStore
from behave.task_execution.capture import load_submission
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.sim_tool import ComparisonToolInputs
from behave.task_execution.solver import SolverJob, run_solver, run_solver_process
from tests.infrastructure.test_ppa import RTL, fixture_task
from tests.infrastructure.test_solver_process import BROKER, ROOT
from tests.infrastructure.test_solver_settings import PUBLIC_SPEC

R = {"files": ["top.sv"], "entry_file": "top.sv", "entry_symbol": "M"}
B = {"files": ["model.py"], "entry_file": "model.py", "entry_symbol": "Identity"}


@pytest.mark.parametrize("setting,visible", [("joint_sim", False), ("rtl_sim", False), ("rtl_sim", True)])
def test_solver_routes_comparison_and_submit_without_tb(tmp_path, monkeypatch, setting, visible):
    from behave.task_execution import sim_tool

    public, task, _ = fixture_task()
    public = replace(public, files=tuple(
        FileData(FileRef(public.spec_path, BlobRef.of(PUBLIC_SPEC)), PUBLIC_SPEC)
        if file.reference.path == public.spec_path else file for file in public.files))
    own = setting == "joint_sim"
    comparison = ComparisonToolInputs.baseline(task, source="agent" if own else "provided",
                                              reference_visible=visible)
    objects = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "solver", objects)
    arguments = {"rtl": R, **({"behavior": B} if own else {})}
    compared, requests = [], []

    def execute(inputs, *, objects, workdir):
        compared.append(inputs)
        return ComponentJudgment(1, "match", detail={"reference_detail": "not public when hidden"})

    monkeypatch.setattr(sim_tool, "compare_sources", execute)

    def write(workspace, arguments):
        assert workspace.snapshot().file(public.contract_path) is None
        assert (b"class Counter" in workspace.read("spec.md")) == own
        assert (workspace.snapshot().file("reference/behavior.py") is not None) == visible
        workspace.write("top.sv", RTL)
        workspace.write("model.py", task.reference_behavior.files[0].data)
        return ToolResult({"written": True})

    class Backend:
        def generate(self, request):
            requests.append(request)
            turn = len(requests) - 1
            assert [tool["name"] for tool in request.tools] == ["run_command", "compare_rtl", "submit"]
            for tool in request.tools[1:]:
                assert set(tool["parameters"]["properties"]) == set(arguments)
            if turn == 0:
                call = ToolCall("write", "run_command", {})
            elif turn == 1:
                call = ToolCall("compare", "compare_rtl", arguments)
            elif turn == 2:
                feedback = request.messages[-2]["content"]
                assert feedback["matched"] is True
                assert feedback["report"].endswith("/report.md")
                assert "reference_detail" not in repr(feedback)
                call = ToolCall("submit", "submit", arguments)
            else:
                raise AssertionError("completed solver must not call its backend")
            return ModelReply("", (call,))

    spec = AttemptSpec("comparison", "solver", {"model": "fixture"})
    kwargs = dict(task=public, spec=spec, backend=Backend(), journal=journal,
                  command_tool=Tool("run_command", "fixture writer", object_schema({}), write, "fixture/1"),
                  setting=setting, comparison=comparison)
    outcome = run_solver(**kwargs)
    assert outcome.status == "awaiting_judgment" and outcome.score is None
    frozen = load_submission(objects, outcome.submission)
    assert frozen.testbench.selection is None
    assert (frozen.behavior.selection is not None) == own
    assert compared[0].reference_behavior.files[0].data == task.reference_behavior.files[0].data
    assert run_solver(**kwargs) == outcome
    assert len(compared) == 1 and len(requests) == 3

    # Final scoring dispatches afresh against the hidden task reference, rather
    # than promoting the development match into a correctness result.
    from behave.task_execution.judge import judge_submission

    final_calls = []

    class GoldenJudge:
        identity = "golden-fixture/1"

        def evaluate(self, request, objects):
            final_calls.append(request)
            assert request.task.reference_behavior == task.reference_behavior
            return ComponentJudgment(0, "golden-mismatch", origin="candidate")

    judgment = judge_submission(task=task, submission=outcome.submission,
        journal=AttemptJournal(tmp_path / "judge", objects), backend=GoldenJudge())
    assert judgment.rtl.score == 0 and judgment.score == 0
    assert (judgment.behavior is not None) == own
    assert {call.component for call in final_calls} == ({"behavior", "rtl"} if own else {"rtl"})
    assert len(compared) == 1 and len(requests) == 3  # No model or development re-run.


@pytest.mark.parametrize("setting", ["joint_sim", "rtl_sim"])
def test_solver_job_records_source_and_roundtrips_comparison(tmp_path, setting):
    public, task, _ = fixture_task()
    comparison = ComparisonToolInputs.baseline(task, source="agent" if setting == "joint_sim" else "provided")
    model = ModelCommand((sys.executable, "-c", "pass"), "fixture/1")
    spec = AttemptSpec("comparison", "solver", {"model": "fixture", "transport": model.identity})
    job = SolverJob(public, spec, model, command_limits=None, setting=setting, comparison=comparison)
    store = DirectoryStore(tmp_path / "objects")
    for data in job.input_blobs():
        store.put(data)
    assert SolverJob.from_bytes(job.to_bytes(), store) == job
    from behave.agent.admission import AdmissionPolicy
    from behave.task_execution.batch_plan import BatchPlan

    plan = BatchPlan("sim-batch", (job,), "golden-fixture/1", 1, AdmissionPolicy("judge", 1),
                     score_on_submit=False)
    assert BatchPlan.from_bytes(plan.to_bytes(), store) == plan
    assert parse_json(job.comparison.judge_policy)["search_seconds"] == 0
    assert parse_json(job.comparison.judge_policy)["analysis"] is None
    with pytest.raises(ValueError, match="comparison source"):
        replace(job, setting="joint")
    with pytest.raises(ValueError, match="comparison source"):
        replace(job, comparison=None)


@pytest.mark.isolation
@pytest.mark.parametrize("setting,visible,broken,expected_error", [
    ("joint_sim", False, None, ""),
    ("rtl_sim", False, None, ""),
    ("rtl_sim", True, None, ""),
    ("joint_sim", False, "class Identity:\n def reset(self): pass\n def process(self, a)\n  return {'y': a}\n",
     "expected ':'"),
    ("joint_sim", False, "class Identity:\n def reset(self): pass\n def process(self, a): return {'y': missing_helper(a)}\n",
     "missing_helper()"),
], ids=["agent-pass", "hidden-pass", "visible-pass", "syntax-repair", "missing-call-repair"])
def test_native_solver_sim_tool_and_final_submission(tmp_path, setting, visible, broken, expected_error, managed=False):
    from behave.isolation.model import ResourceLimits
    from behave.sim import api
    from tools.eval_review_trial import export_candidate

    public, task, _ = fixture_task()
    own = setting == "joint_sim"
    comparison = ComparisonToolInputs.baseline(task, source="agent" if own else "provided",
                                              reference_visible=visible)
    arguments = {"rtl": R, **({"behavior": B} if own else {})}
    fixed = task.reference_behavior.files[0].data.decode()
    repair = "printf %s " + shlex.quote(fixed) + " > model.py"
    command = "printf %s " + shlex.quote(RTL.decode()) + " > top.sv\n"
    command += "printf %s " + shlex.quote(fixed if broken is None else broken) + " > model.py\n"
    command += "test -e reference/behavior.py" if visible else "test ! -e reference/behavior.py"
    if managed:
        command += "\npython -c 'import os; assert len(os.sched_getaffinity(0)) == 1'"
    program = f'''
import shlex
if request.turn == 0:
    call = ToolCall('write', 'run_command', {{'command': {command!r}}})
elif request.turn == 1:
    assert request.messages[-2]['content']['exit_code'] == 0
    call = ToolCall('compare', 'compare_rtl', {arguments!r})
elif {broken is not None!r} and request.turn == 2:
    feedback = request.messages[-2]['content']
    assert feedback['status'] == 'no_result' and feedback['matched'] is None, feedback
    assert {expected_error!r} in feedback['summary'], feedback
    script = ('import pathlib, sys; p=pathlib.Path(sys.argv[1]); '
              'print(p.read_text()); print(p.with_name("failure.json").read_text())')
    command = 'python -c ' + shlex.quote(script) + ' ' + shlex.quote(feedback['report'])
    call = ToolCall('read-error', 'run_command', {{'command': command}})
elif {broken is not None!r} and request.turn == 3:
    feedback = request.messages[-2]['content']
    assert feedback['exit_code'] == 0 and {expected_error!r} in feedback['stdout'], feedback
    call = ToolCall('repair', 'run_command', {{'command': {repair!r}}})
elif {broken is not None!r} and request.turn == 4:
    assert request.messages[-2]['content']['exit_code'] == 0
    call = ToolCall('compare-fixed', 'compare_rtl', {arguments!r})
elif request.turn == {2 if broken is None else 5}:
    assert request.messages[-2]['content']['matched'] is True, request.messages[-2]['content']
    report = request.messages[-2]['content']['report']
    script = 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).read_text())'
    command = 'python -c ' + shlex.quote(script) + ' ' + shlex.quote(report)
    call = ToolCall('read-report', 'run_command', {{'command': command}})
else:
    assert request.messages[-2]['content']['exit_code'] == 0
    assert '# Comparison report' in request.messages[-2]['content']['stdout']
    assert 'PASS.' in request.messages[-2]['content']['stdout']
    call = ToolCall('submit', 'submit', {arguments!r})
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('', (call,))))
'''
    model = ModelCommand((sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, BROKER + program), "sim-tool-fixture/1")
    spec = AttemptSpec("sim-tool", "solver", {"model": "fixture", "transport": model.identity})
    job = SolverJob(public, spec, model, ResourceLimits(), setting=setting, comparison=comparison)
    objects = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "solver", objects)
    supervision = AttemptJournal(tmp_path / "supervision", objects)
    if managed:
        from behave.agent.cpu_resources import CpuResources

        resources = CpuResources(2, cpus=2)
        outcome = run_solver_process(job, journal=journal, supervision=supervision, cpu_resources=resources)
        assert resources.snapshot()["active"] == resources.snapshot()["retired"] == 0
        assert resources.snapshot()["available_cpus"] == 2
    else:
        with api.CpuPool(capacity=2) as pool:
            endpoint = pool.endpoint.as_dict()
            outcome = run_solver_process(job, journal=journal, supervision=supervision,
                                         comparison_cpu_pool=endpoint)
            usage = pool.snapshot()
            assert 0 < usage["peak_reserved"] <= 2
    export_candidate(tmp_path, tmp_path / "trace")
    assert outcome.status == "awaiting_judgment" and outcome.score is None
    captured = load_submission(objects, outcome.submission)
    assert captured.testbench.selection is None
    assert (captured.behavior.selection is not None) == own
    if own:
        assert objects.read(captured.behavior.files[0].content) == fixed.encode()
    assert run_solver_process(job, journal=journal, supervision=supervision) == outcome


@pytest.mark.isolation
def test_native_solver_shares_shell_and_comparison_cpu_budget(tmp_path):
    test_native_solver_sim_tool_and_final_submission(tmp_path, "joint_sim", False, None, "", managed=True)


def test_batch_shares_cpu_admission_without_reserving_idle_rollouts(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from behave.agent.admission import AdmissionPolicy
    from behave.task_execution import batch as batch_module
    from behave.task_execution.batch_plan import BatchPlan

    public, task, _ = fixture_task()
    comparison = ComparisonToolInputs.baseline(task)
    model = ModelCommand((sys.executable, "-c", "pass"), "fixture/1")
    spec = AttemptSpec("first", "solver", {"model": "fixture", "transport": model.identity})
    job = SolverJob(public, spec, model, command_limits=None, setting="joint_sim", comparison=comparison)
    jobs = (job, replace(job, attempt=replace(spec, attempt_id="second")))
    plan = BatchPlan("sim-batch", jobs, "unused", 50, AdmissionPolicy("judge", 1), score_on_submit=False)
    calls = []

    def execute(**kwargs):
        calls.append(kwargs["cpu_resources"])
        assert calls[-1].snapshot()["active"] == 0
        raise RecordError("fixture-stop", "stop before any candidate execution")

    monkeypatch.setattr("behave.sim.api.CpuPool", lambda **_: pytest.fail("idle rollout holds no Sim pool"))
    monkeypatch.setattr(batch_module, "execute_task", execute)
    monkeypatch.setattr("behave.core.resources.os.sched_getaffinity", lambda _: set(range(16)))
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "8")
    options = dict(plan=plan, reader=DirectoryStore(tmp_path / "inputs"), directory=tmp_path / "batch")
    report = batch_module.run_batch(**options)
    assert report.summary["requested"] == 2 and report.summary["no_result"] == 2
    assert len(calls) == 2 and calls[0] is calls[1]
    assert calls[0].snapshot()["capacity"] == 8
    assert calls[0].snapshot()["slots"] == 1
    assert batch_module.run_batch(**options) == report
    assert len(calls) == 2
