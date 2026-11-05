"""Search capability is visible feedback, not a replacement correctness score."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.sim import api
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.simulator_judge import _with_diagnostics
from tests.infrastructure.test_score_diagnostics import evaluation


def command_result(*, termination="queue-empty", probes=0, refusals=True, failure=False,
                   budget=600, generated=True, value=None):
    telemetry = {
        "termination": termination,
        "search_budget": {"probes_started": probes, "probes_completed": probes,
                          "probes_cancelled": 0, "elapsed_s": 2.5},
        "refusals": {"output-key-not-static": 41} if refusals else {},
        "refusal_why": {"output-key-not-static": "PRIVATE-SOURCE output key has no finite selector plan"},
        "refusal_detail": {"output-key-not-static": {
            "line": 24, "source": "PRIVATE-WHOLE-PROGRAM", "query": "PRIVATE-SOLVER-QUERY"}},
        "coverage": {"PRIVATE-BRANCH": True},
    }
    if failure:
        telemetry["failure"] = {"kind": "rtl-exact-isolated-layout-unavailable",
            "why": "PRIVATE-SOURCE import not supported", "detail": {"line": 81}}
    searches = [SimpleNamespace(provider="behavior/PRIVATE-SOURCE-HASH", generated=(), telemetry=telemetry)]
    return SimpleNamespace(evaluation=value, generated=(
        SimpleNamespace(portfolio=SimpleNamespace(searches=searches)) if generated else None),
        command=SimpleNamespace(search_policy=api.SearchPolicy(time_budget_s=budget),
                                _provider_images=(object(),), probe_budget=None))


@pytest.mark.parametrize("visible", [False, True])
def test_search_projection_keeps_only_public_capability_details(visible):
    images = api.command_diagnostics(command_result(), reference_visible=visible)
    search = images["search.json"]
    assert search["status"] == "limited"
    row = search["directions"][0]
    assert row["status"] == "unavailable" and row["probes_started"] == 0
    assert row["refusals"][0]["code"] == "output-key-not-static"
    assert ("why" in row["refusals"][0]) is visible
    assert b"PRIVATE-WHOLE-PROGRAM" not in canonical_json(images)
    assert b"PRIVATE-SOLVER-QUERY" not in canonical_json(images)
    assert b"PRIVATE-BRANCH" not in canonical_json(images)
    assert b"PRIVATE-SOURCE-HASH" not in canonical_json(images)
    if not visible:
        assert b"PRIVATE" not in canonical_json(images)


@pytest.mark.parametrize("score", [0, 1, None])
def test_search_failure_is_in_reply_and_report_without_rewriting_verdict(tmp_path, score):
    store = DirectoryStore(tmp_path / "objects")
    result = _with_diagnostics(ComponentJudgment(score, "fixture",
        origin="verifier" if score is None else None, evidence=(BlobRef.of(b"PRIVATE"),)),
        store, lambda: api.command_diagnostics(command_result(), reference_visible=True))
    before = result.as_dict()
    reply, files = comparison_feedback(result, store, prefix="comparison/current")
    assert reply["matched"] is (None if score is None else bool(score))
    assert reply["search"]["directions"][0]["probes_started"] == 0
    assert "Search: limited." in reply["summary"]
    assert "output-key-not-static" in reply["summary"]
    assert "verdict covers only the tests executed" in reply["summary"]
    report = files[-1].data.decode()
    assert "line: 24" in report and "no finite selector plan" in report
    assert "Search status is separate from functional correctness" in report
    assert result.as_dict() == before

    class NoReads:
        def read(self, reference):
            pytest.fail("hidden feedback must not read diagnostics")

    hidden, hidden_files = comparison_feedback(result, NoReads(), diagnostics_visible=False)
    assert "search" not in hidden and len(hidden_files) == 1
    assert "PRIVATE" not in repr(hidden)


@pytest.mark.parametrize("termination,probes,refusals,failure,expected", [
    ("queue-empty", 0, True, False, "unavailable"),
    ("queue-empty", 7, True, False, "limited"),
    ("search-failed", 0, False, True, "failed"),
    ("time-budget-exhausted", 4, False, False, "budget_exhausted"),
    ("scoring-budget-exhausted", 4, False, False, "budget_exhausted"),
    ("goals-covered", 0, False, False, "completed"),
    ("queue-empty", 4, False, False, "completed"),
])
def test_search_outcomes_are_distinguished(termination, probes, refusals, failure, expected):
    result = command_result(termination=termination, probes=probes, refusals=refusals, failure=failure)
    images = api.command_diagnostics(result)
    assert images["search.json"]["directions"][0]["status"] == expected
    assert images["search.json"]["directions"][0]["generated_stimuli"] == 0


@pytest.mark.parametrize("budget,expected", [(0, "disabled"), (600, "not_run")])
def test_no_generation_is_not_successful_search(budget, expected):
    images = api.command_diagnostics(command_result(budget=budget, generated=False))
    assert images["search.json"]["status"] == expected
    assert images["search.json"]["directions"] == []


@pytest.mark.parametrize("seconds,probes,expected", [
    (None, 1, "not_run"), (None, 0, "disabled"),
    (600, None, "not_run"), (600, 0, "disabled"),
    (0, 1, "disabled"), (0, None, "disabled"),
])
def test_search_report_accepts_each_public_budget_form(tmp_path, seconds, probes, expected):
    result = command_result(generated=False)
    result.command.search_policy = api.SearchPolicy(time_budget_s=seconds, max_probes=probes)
    images = api.command_diagnostics(result)
    assert images["search.json"]["status"] == expected
    assert images["search.json"]["requested_budget_seconds"] == seconds
    store = DirectoryStore(tmp_path / "objects")
    judgment = _with_diagnostics(ComponentJudgment(1, "simulator-concrete-score"), store,
        lambda: api.command_diagnostics(result))
    assert judgment.score == 1
    assert judgment.detail.get("diagnostics_status") != "export_failed"
    assert any(file.path == "search.json" for file in judgment.diagnostics)


@pytest.mark.parametrize("visible", [False, True])
def test_worker_timeout_keeps_its_classification_in_search_feedback(visible):
    from behave.sim.engines.rtl.generation import _IsolatedRTLGeneration
    from behave.sim.job_isolation import IsolatedJobFailure

    failure = _IsolatedRTLGeneration._unavailable("source",
        IsolatedJobFailure("timeout", "session probe exceeded 35s"))
    assert failure.kind == "rtl-generation-isolated-source-timeout"
    result = command_result(refusals=False, failure=True)
    result.generated.portfolio.searches[0].telemetry["failure"] = failure.as_dict()
    images = api.command_diagnostics(result, reference_visible=visible)
    row = images["search.json"]["directions"][0]
    assert row["status"] == "failed"  # A dead worker is not solver UNKNOWN.
    assert row["failure"]["code"] == failure.kind
    if visible:
        assert row["failure"]["detail"]["failure"] == "timeout"
        assert row["failure"]["detail"]["detail"] == "session probe exceeded 35s"
    else:
        assert "detail" not in row["failure"]


def test_projection_preserves_actual_evaluation_and_score():
    value = evaluation(gain=2)
    before = value.fingerprint, value.passed
    images = api.command_diagnostics(command_result(value=value), reference_visible=True)
    assert images["summary.json"] == api.evaluation_diagnostics(value)["summary.json"]
    assert (value.fingerprint, value.passed) == before


@pytest.mark.parametrize("visible", [False, True])
def test_typed_rtl_cause_identifies_construct_without_exposing_private_records(visible):
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    cause = api.Refusal("rtl-state-clock-ownership-unsupported-state-cell",
        "cannot assign every represented state field to a clock",
        first={"cell": "qs", "kind": "unsupported-state-cell", "source": "PRIVATE-SOURCE"},
        count=2, query="PRIVATE-QUERY")
    wrapper = RTLExactJobContext.unavailable("layout", api.Refused(cause))
    assert wrapper.detail["cause"]["kind"] == cause.kind
    result = command_result(refusals=False, failure=True)
    result.generated.portfolio.searches[0].telemetry["failure"] = wrapper.as_dict()
    images = api.command_diagnostics(result, reference_visible=visible)
    failure = images["search.json"]["directions"][0]["failure"]
    if visible:
        assert failure["detail"]["cause_kind"] == cause.kind
        assert failure["detail"]["cell"] == "qs"
        assert failure["detail"]["count"] == 2
    else:
        assert "detail" not in failure
    assert b"PRIVATE" not in canonical_json(images)


def test_command_search_export_retains_an_existing_failure_diagnostic(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    failure = FileRef("failure.json", store.put(canonical_json({
        "code": "compile-failed", "origin": "candidate", "phase": "compile"})))
    original = ComponentJudgment(0, "compile-failed", diagnostics=(failure,))
    result = _with_diagnostics(original, store, lambda: api.command_diagnostics(
        command_result(generated=False)), append=True)
    assert failure in result.diagnostics
    reply, files = comparison_feedback(result, store)
    assert reply["matched"] is False and reply["search"]["status"] == "not_run"
    assert "compile-failed" in reply["summary"]
    assert "Execution failure" in files[-1].data.decode()


def test_failed_diagnostic_export_cannot_hide_behind_pass(tmp_path):
    store = DirectoryStore(tmp_path / "objects")

    def broken_export():
        raise RuntimeError("private diagnostic failure")

    result = _with_diagnostics(ComponentJudgment(1, "simulator-concrete-score"), store, broken_export)
    reply, _ = comparison_feedback(result, store)
    assert reply["matched"] is True
    assert "Diagnostic export: export_failed" in reply["summary"]
    assert "private diagnostic failure" not in reply["summary"]


@pytest.mark.parametrize("seconds", [5, None])
def test_real_public_command_refusal_reaches_shared_agent_feedback(tmp_path, seconds):
    from tests.sim.test_command_handler import _request
    from tests.sim.test_generation_scheduler import _Provider

    class RefusingProvider(_Provider):
        name = "behavior/test"

        def select(self, seed, *, targets=None):
            return api.Refusal("output-key-not-static", "no finite string selector plan", line=7)

    result = api.handle_command(_request(tmp_path, provider=RefusingProvider({}, {}),
        run_analysis=False, probe_budget=None,
        search_policy=api.SearchPolicy(time_budget_s=seconds, max_probes=1)))
    assert result.decision.authoritative and result.decision.passed
    before = result.as_dict()
    store = DirectoryStore(tmp_path / "objects")
    judgment = _with_diagnostics(ComponentJudgment(1, "simulator-concrete-score"), store,
        lambda: api.command_diagnostics(result, reference_visible=True))
    reply, files = comparison_feedback(judgment, store)
    assert reply["matched"] is True
    assert reply["search"]["status"] == "limited"
    assert "output-key-not-static" in reply["summary"]
    assert "line: 7" in files[-1].data.decode()
    assert result.as_dict() == before


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
@pytest.mark.parametrize("component", ["behavior", "rtl"])
@pytest.mark.parametrize("seconds", [0, 2])
def test_native_judge_exports_search_status_to_agent(tmp_path, component, seconds):
    from test_data_checks import comparison

    from behave.task_execution.comparison import ComparisonRequest
    from behave.task_execution.simulator_judge import _SimulatorService

    inputs, journal = comparison(tmp_path, component=component, real=True)
    inputs = replace(inputs, reference_visible=True,
        judge_policy=replace(inputs.policy, search_seconds=seconds).to_bytes())
    work = tmp_path / "comparison"
    work.mkdir()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        judgment = _SimulatorService().evaluate(ComparisonRequest(inputs, work), journal.objects)
    assert judgment.score == 1, judgment.as_dict()
    assert judgment.detail.get("diagnostics_status") != "export_failed"
    reply, files = comparison_feedback(judgment, journal.objects)
    assert "search" in reply and "Search:" in reply["summary"]
    assert reply["search"]["requested_budget_seconds"] == seconds
    assert (reply["search"]["status"] == "disabled") is (seconds == 0)
    assert any(file.reference.path == "search.json" for file in files)
