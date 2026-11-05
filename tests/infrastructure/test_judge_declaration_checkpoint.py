"""Declaration evidence belongs to the same final concrete handoff as scoring."""

from dataclasses import replace

import pytest
from test_data_checks import comparison

from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.task_execution.analysis import AnalysisOptions, AnalysisReport
from behave.task_execution.comparison import ComparisonRequest
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.simulator_judge import _SimulatorService


@pytest.mark.parametrize("score", [None, 0, 1])
@pytest.mark.parametrize("analysis", [None, AnalysisOptions(1)])
def test_declared_task_publishes_the_same_concrete_evidence_as_its_return(
        tmp_path, monkeypatch, score, analysis):
    from behave.task_execution import task_declaration

    inputs, journal = comparison(tmp_path)
    spec = b"bound declaration fixture"
    inputs = replace(inputs, spec=FileData(FileRef("spec.md", BlobRef.of(spec)), spec),
                     judge_policy=replace(inputs.policy, analysis=analysis).to_bytes())
    declaration = ComponentJudgment(1, "task-interface-consistent",
        evidence=(journal.objects.put(b"declaration evidence"),),
        diagnostics=(FileRef("declaration.json", journal.objects.put(b"declaration diagnostic")),))
    monkeypatch.setattr(task_declaration, "check_task_declaration", lambda *a, **kw: declaration)
    concrete = ComponentJudgment(score, "native", origin="reference" if score is None else None,
        evidence=(journal.objects.put(b"comparison evidence"),),
        detail={"evaluation": BlobRef.of(b"evaluation").sha256})

    def evaluate(self, request, objects, *, on_decided, scoring_budget):
        on_decided(concrete)
        if analysis is not None and score is not None:
            return replace(concrete, analysis=AnalysisReport(
                concrete.detail["evaluation"], objects.put(b"analysis policy"), "analysis-timeout"))
        return concrete

    monkeypatch.setattr(_SimulatorService, "_evaluate", evaluate)
    workdir = tmp_path / "service"
    workdir.mkdir()
    published = []
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, workdir), journal.objects,
                                          on_decided=published.append)
    assert published == [replace(result, analysis=None)]
    assert result.evidence == (*declaration.closure, *concrete.evidence)
    assert result.score == score
