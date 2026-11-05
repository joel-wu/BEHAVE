"""Public baseline consumes source goals without a synthesized-action witness."""

import json

import pytest

from behave.sim import api
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.source_goals import is_source_goal

from .test_baseline_public_sequence import REFERENCE, _contract
from .test_rtl_tool_isolation import _needs_namespace

pytestmark = [pytest.mark.isolation, pytest.mark.eda]

SOURCE = """module M(input wire clk,rst,in_valid,out_ready,
    input wire [7:0] a, output wire in_ready,out_valid,output wire [7:0] y);
    function automatic [7:0] adjust(input logic [7:0] v);
      if (v[0]) return v+8'd1;
      return v;
    endfunction
    assign in_ready=out_ready;
    assign out_valid=in_valid;
    assign y=adjust(8'd0)+a;
endmodule
"""


@pytest.mark.parametrize("wrong,on_change", [(False, False), (True, False), (True, True)],
                         ids=["correct", "immediate-failure", "failure-after-input-change"])
def test_public_source_baseline_records_constant_call_events(tmp_path, wrong, on_change):
    _needs_namespace()
    error = "(a & 8'd1)" if on_change else "8'd1"
    source = SOURCE.replace("+a;", f"+a+{error};") if wrong else SOURCE
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"),
                             api.GenerationSpec(220, 128, 2, 4))
    request = handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed is not wrong
    if wrong:
        # A witnessed mismatch can finish scoring before the remaining
        # baseline/workload runs; the observed prefix must still carry hits.
        assert result.evaluation.mismatch_count > 0
        assert result.decision.evidence["mismatch_count"] > 0
    else:
        assert result.evaluation.complete
    catalogue = result.evaluation.catalogue
    source_goals = [goal for goal in catalogue.values() if goal.provider == "rtl-structural"]
    assert source_goals and all(is_source_goal(goal) for goal in source_goals)
    assert {goal.family for goal in source_goals} == {"location", "control-choice"}
    choices = {goal.event: goal.id for goal in source_goals if goal.family == "control-choice"}
    assert set(choices) == {"true", "false"}  # Constant-dead source outcome stays declared.
    hits = set()
    for run in result.evaluation.results:
        assert run.trace.source_evidence is not None
        for row in run.trace.records:
            hits.update(goal for goal, active in row.hits if active)
    assert choices["true"] not in hits
    if not wrong or on_change:
        # Constant helper calls are observed when their continuous assignment
        # activates. An immediate mismatch may end this command after only the
        # all-zero trace, before any post-reset change to `a`. Do not require a
        # hit from a later, unexecuted trace. The delayed mutant forces an input
        # change before its mismatch, so it must retain this actual helper hit.
        assert choices["false"] in hits
    for run in result.evaluation.results[:len(result.generated.stimuli)]:
        assert run.stimulus.depth("in") == 100
        if run.trace.completion is api.Completion.NORMAL:
            assert run.trace.accepted_count(["in"]) == 100
        else:
            # Online comparison publishes checked prefixes and stops at a
            # counterexample. It must not invent acceptance of the suffix.
            assert wrong
            assert run.trace.completion is api.Completion.CHECKPOINT
            assert 0 < run.trace.accepted_count(["in"]) < 100
    if wrong:
        assert any(run.was_compared and not run.ok for run in result.evaluation.results)
    assert path.read_text() == source


def test_source_observer_accepts_the_returning_clock_edge(tmp_path):
    _needs_namespace()
    source = SOURCE.replace("    assign y=adjust(8'd0)+a;", """
    reg q;
    always @(negedge clk) q<=a[0];
    assign y={7'd0,q};""")
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    artifact = RTLArtifact(path, "M", tmp_path / "build")
    catalogue = artifact.frontend_catalogue()
    assert catalogue and all(is_source_goal(goal) for goal in catalogue.values())
    # Both physical edges execute inside the native Driver cycle. The old
    # synthesized-action edge whitelist is not the source observer's domain.
    binding = artifact.bind_observation(_contract())
    assert binding.native and artifact.observation_binding is binding
    assert "negedge clk" in artifact._observation.native.source
    assert artifact._observation.native.plan.check_catalogue(catalogue) is None
    assert artifact.frontend_catalogue() is catalogue


def test_public_baseline_retains_a_cancelled_nba_consumer_event(tmp_path):
    _needs_namespace()
    source = SOURCE.replace("    assign y=adjust(8'd0)+a;", """
    logic [7:0] scratch=0,observed;
    always @(posedge clk) if(!rst && in_valid && in_ready) begin
      scratch<=a; scratch<=0;
    end
    always_comb observed=scratch;
    assign y=adjust(8'd0)+a;
    """)
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"),
                             api.GenerationSpec(220, 128, 2, 4))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    assert result.evaluation.complete
    start = source.encode().index(b"observed=scratch;")
    goal, = [goal.id for goal in result.evaluation.catalogue.values()
             if goal.provider == "rtl-structural" and goal.family == "location"
             and dict(goal.provenance)["start"] == start]
    # Startup alone would already hit this goal once. Require it again on the
    # actual nonzero accepted transactions, whose NBA writes restore scratch.
    tested = 0
    for run in result.evaluation.results:
        assert run.trace.source_evidence is not None
        for row in run.trace.records:
            payload = dict(row.accepted).get("in")
            if payload is not None and payload[0]:
                assert dict(row.hits)[goal] is True
                tested += 1
    assert tested > 1
    (tmp_path / "nba-public-summary.json").write_text(json.dumps({
        "authoritative": result.decision.authoritative,
        "passed": result.decision.passed,
        "accepted": sum(run.trace.accepted_count(["in"]) for run in result.evaluation.results),
        "nonzero_accepted_with_consumer_hit": tested,
    }, indent=2))
    assert path.read_text() == source
