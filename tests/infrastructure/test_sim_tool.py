import pytest

from behave.agent.local_workspace import LocalWorkspace
from behave.agent.model import ModelReply, ToolCall
from behave.agent.run_store import JournalDirectory
from behave.agent.workspace import Workspace
from behave.core.artifacts import FileRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.sim_tool import ComparisonToolInputs, comparison_tool
from tests.infrastructure.test_ppa import RTL, FakeNative, experiment, fixture_task

RTL_SELECTION = {"files": ["top.sv"], "entry_file": "top.sv", "entry_symbol": "M"}
BEHAVIOR_SELECTION = {"files": ["model.py"], "entry_file": "model.py", "entry_symbol": "Model"}


def context(*, provided=False, visible=False):
    _, task, _ = fixture_task()
    return ComparisonToolInputs(task.contract, task.run_policy, task.judge_policy,
                                task.reference_behavior if provided else None, visible)


@pytest.mark.parametrize("local", [False, True])
def test_comparison_captures_current_pair_not_archived_files(tmp_path, local):
    objects = DirectoryStore(tmp_path / "objects")
    workspace = LocalWorkspace(objects, tmp_path / "live") if local else Workspace(objects)
    workspace.write("top.sv", RTL)
    workspace.write("model.py", b"first model")
    calls = []

    def execute(inputs, *, objects, workdir):
        calls.append(inputs)
        assert inputs.reference_visible is True
        evidence = objects.put(b"own execution diagnostic")
        return ComponentJudgment(1, "match", evidence=(evidence,), detail={"case": "own-input"})

    tool = comparison_tool(context(), executor=execute)
    arguments = {"rtl": RTL_SELECTION, "behavior": BEHAVIOR_SELECTION}
    first = tool.handler(workspace, arguments)
    workspace.write("model.py", b"revised model")
    second = tool.handler(workspace, arguments)
    assert first.content["matched"] is True
    assert first.content["reference_source"] == "agent"
    assert calls[0].reference_behavior.files[0].data == b"first model"
    assert calls[1].reference_behavior.files[0].data == b"revised model"
    assert calls[0].source == calls[1].source
    from behave.core.records import parse_json
    from behave.task_execution.comparison import ComparisonInputs

    for result, captured in zip((first, second), calls, strict=True):
        for reference in result.artifacts:
            assert objects.read(reference)
        record = parse_json(objects.read(result.artifacts[0]))
        # Reproduce the earlier comparison after live sources were overwritten,
        # including policies and the reference; evidence alone is insufficient.
        assert ComparisonInputs.from_dict(record["inputs"], objects) == captured
        assert set(captured.stage_contents(objects)) <= set(result.artifacts)


@pytest.mark.parametrize("visible", [False, True])
def test_supplied_reference_visibility_does_not_change_execution(tmp_path, visible):
    inputs = context(provided=True, visible=visible)
    workspace = Workspace(DirectoryStore(tmp_path / "objects"),
                          read_only_paths=tuple(file.reference.path for file in inputs.public_files()))
    workspace.seed(inputs.public_files())
    workspace.write("top.sv", RTL)
    workspace.write("model.py", b"cannot replace provided source")

    def execute(comparison, *, objects, workdir):
        assert comparison.reference_behavior == inputs.reference_behavior
        assert comparison.reference_visible is visible
        ref = objects.put(b'{"code":"mismatch","reason":{"kind":"mismatch","why":"PRIVATE-EXPECTED"}}')
        return ComponentJudgment(0, "mismatch", diagnostics=(FileRef("failure.json", ref),),
                                 detail={"internal": "NEVER-PUBLIC"})

    tool = comparison_tool(inputs, executor=execute)
    assert set(tool.parameters["properties"]) == {"rtl"}
    result = tool.handler(workspace, {"rtl": RTL_SELECTION})
    assert result.content["matched"] is False
    assert ("PRIVATE-EXPECTED" in repr(result.content)) == visible
    assert "NEVER-PUBLIC" not in repr(result.content)
    assert ("reference/behavior.py" in tool.description) == visible
    if visible:
        with pytest.raises(RecordError, match="cannot change an input file"):
            workspace.write("reference/behavior.py", b"edited")
    with pytest.raises(RecordError):
        tool.handler(workspace, {"rtl": RTL_SELECTION, "behavior": BEHAVIOR_SELECTION})


def test_no_result_is_not_a_failed_comparison(tmp_path):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    workspace.write("top.sv", RTL)
    workspace.write("model.py", b"invalid model")
    tool = comparison_tool(context(), executor=lambda *args, **kwargs:
                           ComponentJudgment(None, "reference-invalid", origin="reference"))
    result = tool.handler(workspace, {"rtl": RTL_SELECTION, "behavior": BEHAVIOR_SELECTION})
    assert result.content["matched"] is None
    assert result.content["status"] == "no_result"
    assert "reference-invalid (reference)" in result.content["summary"]
    assert not result.terminal


@pytest.mark.parametrize("provided,visible", [(False, False), (True, False), (True, True)])
def test_real_reference_syntax_error_is_mounted_after_comparison_scratch_closes(tmp_path, provided, visible):
    from dataclasses import replace

    from behave.core.artifacts import BlobRef, FileData, LoadedSource, SourceArtifact
    from behave.core.records import parse_json
    from behave.task_execution.comparison import ComparisonRequest
    from behave.task_execution.simulator_judge import _SimulatorService

    broken = b"class Model:\n def process(self, a)\n  return {'y': a}\n"
    file = FileData(FileRef("model.py", BlobRef.of(broken)), broken)
    reference = LoadedSource(SourceArtifact((file.reference,), "model.py", "Model"), (file,))
    inputs = context(provided=provided, visible=visible)
    if provided:
        inputs = replace(inputs, reference_behavior=reference)
    workspace = LocalWorkspace(DirectoryStore(tmp_path / "objects"), tmp_path / "live")
    workspace.write("top.sv", RTL)
    if not provided:
        workspace.write("model.py", broken)
    scratch = []

    def execute(comparison, *, objects, workdir):
        scratch.append(workdir.parent)
        # Exercise the real public source-preparation path; no EDA or model call.
        return _SimulatorService().evaluate(ComparisonRequest(comparison, workdir), objects)

    arguments = {"rtl": RTL_SELECTION, **({} if provided else {"behavior": BEHAVIOR_SELECTION})}
    result = comparison_tool(inputs, executor=execute).handler(workspace, arguments)
    assert result.content["matched"] is None and not result.terminal
    assert all(not path.exists() for path in scratch)
    report = workspace.read(result.content["report"]).decode()
    if not provided or visible:
        assert "model.py:2" in result.content["summary"] and "model.py:2" in report
        assert "expected ':'" in result.content["summary"]
        path = result.content["report"].rsplit("/", 1)[0] + "/failure.json"
        image = parse_json(workspace.read(path))
        assert image["origin"] == "reference" and image["disposition"] == "no-result"
        assert image["reason"]["detail"]["file"] == "model.py"
    else:
        assert result.content["summary"] == "UNAVAILABLE."
        assert report == "# Comparison report\n\nUNAVAILABLE.\n"
        report_path = workspace.directory / result.content["report"]
        assert list(report_path.parent.iterdir()) == [report_path]


@pytest.mark.parametrize("visible", [False, True])
def test_public_score_diagnostics_are_readable_only_when_tool_visibility_allows(tmp_path, visible):
    inputs = context(provided=True, visible=visible)
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    workspace.write("top.sv", RTL)

    def execute(comparison, *, objects, workdir):
        ref = objects.put(b'{"expected":1,"observed":2}')
        return ComponentJudgment(0, "mismatch", diagnostics=(FileRef("failure.json", ref),))

    result = comparison_tool(inputs, executor=execute).handler(workspace, {"rtl": RTL_SELECTION})
    report_path = result.content["report"]
    assert workspace.read(report_path).startswith(b"# Comparison report")
    if visible:
        path = report_path.rsplit("/", 1)[0] + "/failure.json"
        assert workspace.read(path) == b'{"expected":1,"observed":2}'
    else:
        assert [file.path for file in workspace.snapshot().files if file.path.startswith("comparison/")] == [report_path]
        assert workspace.read(report_path) == b"# Comparison report\n\nFAIL.\n"


def test_comparison_description_uses_runtime_timeout():
    from dataclasses import replace

    from behave.task_execution.judge_policy import JudgePolicy

    inputs = context()
    policy = replace(JudgePolicy.from_bytes(inputs.judge_policy), score_timeout_seconds=37)
    tool = comparison_tool(replace(inputs, judge_policy=policy.to_bytes()))
    assert "37 seconds" in tool.description
    assert "`report` path" in tool.description
    assert "${" not in tool.description


def test_ppa_same_rtl_different_behavior_are_distinct_candidates(tmp_path):
    from behave.ppa.workflow import SearchArchive

    class Native(FakeNative):
        def __init__(self):
            self.references = []

        def run(self, inputs, *args):
            self.references.append(inputs.reference_behavior)
            return super().run(inputs, *args)

    _, task, _ = fixture_task()
    native = Native()
    run = JournalDirectory(tmp_path / "run")
    archive = SearchArchive(run, task, experiment(), native, comparison=context())
    workspace = LocalWorkspace(run.objects, tmp_path / "live")
    workspace.write("top.sv", RTL)
    workspace.write("model.py", b"first model")
    check = archive.tools()[0]
    arguments = {"rtl": RTL_SELECTION, "behavior": BEHAVIOR_SELECTION}
    first = check.handler(workspace, arguments).content["candidate"]
    workspace.write("model.py", b"second model")
    second = check.handler(workspace, arguments).content["candidate"]
    assert first != second
    archive.action(first, "final")  # pool recheck, not final golden validation
    assert native.references[-1].files[0].data == b"first model"
    archive.action(first, "final", final=True)
    assert native.references[-1] == task.reference_behavior
    assert archive.candidates[first]["history"][-1]["reference_behavior"] == task.reference_behavior.artifact.as_dict()


def test_ppa_self_agreement_does_not_make_a_golden_frontier(tmp_path):
    from behave.agent.attempt import AttemptSpec
    from behave.ppa.finalize import compare_searches
    from behave.ppa.workflow import run_search

    public, task, _ = fixture_task()

    class Native(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            result = super().run(inputs, exp, mode, pool, objects)
            if mode != "eda" and inputs.reference_behavior == task.reference_behavior:
                result["score"] = 0
                if "performance" in result:
                    result["performance"] = {"score": 0, "time_ns": None}
            return result

    class Scripted:
        def __init__(self):
            self.turn = 0

        def generate(self, request):
            turn = self.turn
            self.turn += 1
            if turn == 0:
                name, args = "write_file", {"path": "top.sv", "content": RTL.decode()}
            elif turn == 1:
                name, args = "write_file", {"path": "model.py", "content": "own model"}
            elif turn == 2:
                name, args = "compare_rtl", {"rtl": RTL_SELECTION, "behavior": BEHAVIOR_SELECTION}
            elif turn == 3:
                feedback = request.messages[-2]["content"]
                assert feedback["matched"] is True and feedback["reference_source"] == "agent"
                name, args = "measure_ppa", {"candidate": feedback["candidate"]}
            elif turn == 4:
                name, args = "finish_search", {}
            else:
                raise AssertionError("no new model call during cold replay")
            return ModelReply("", (ToolCall(str(turn), name, args),))

    kwargs = dict(public=public, task=task, experiment=experiment(),
                  spec=AttemptSpec("self-ppa", "ppa", {"model": "scripted"}), backend=Scripted(),
                  native=Native(), directory=tmp_path / "search", prompt="self-reference fixture",
                  behavior_source="agent")
    result = run_search(**kwargs)
    candidate, = result["candidates"]
    assert candidate["development_match"] == 1
    assert "final_score" not in candidate and candidate["score"] == 1
    assert result["frontier"] == []
    assert run_search(**kwargs) == result
    final = compare_searches(searches={"self": tmp_path / "search"}, task=task, experiment=experiment(),
                             native=Native(), directory=tmp_path / "final")
    assert final["frontiers"]["self"] == []
    assert final["candidates"][candidate["candidate"]]["final_score"] == 0
