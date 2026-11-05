"""Source failures enter the default catalogue and the actual public scorer."""

import pytest

from behave.sim import api
from behave.sim.rtl.catalogue import catalogue_manifest_of

from .test_baseline_public_sequence import _contract


REFERENCE = '''class M:
    def reset(self): pass
    def process(self, a): return {"y": a}
'''
RTL = '''module M(input clk,rst,in_valid,out_ready,input [7:0] a,
    output in_ready,out_valid,output reg [7:0] y);
    reg full;
    assign in_ready = !full || out_ready;
    assign out_valid = full;
    always @(posedge clk) begin
        if(rst) begin full<=0; y<=0; end
        else if(in_ready) begin
            full<=in_valid;
            if(in_valid) begin
                CHECK
                y<=a;
            end
        end
    end
endmodule
'''


@pytest.mark.isolation
@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("check", ["assert(a == a);", "assert(a != 0);",
                                  "if(1'b0) assert(a != 0);",
                                  'unique case(a) 0:; default:; endcase',
                                  'priority case(a) 0:; endcase'])
def test_default_catalogue_retains_constant_and_dead_assertions(tmp_path, check):
    source = tmp_path / "candidate.sv"
    source.write_text(RTL.replace("CHECK", check))
    manifest, sites = catalogue_manifest_of(source, "M")
    failures = {goal for goal, entry in manifest.items() if entry.family == "artifact-failure"}
    assert len(failures) == 1
    assert all(manifest[goal].cls == api.SAFETY for goal in failures)
    assert {goal for _, goal in manifest.occurrences if goal in failures} == failures
    assert {site.goal for site in sites.values()} == set(manifest) - failures


@pytest.mark.isolation
@pytest.mark.eda("slang", "yosys")
def test_default_source_failure_retains_each_elaborated_instance(tmp_path):
    source = tmp_path / "candidate.sv"
    source.write_text('''module Leaf(input clk, valid, input [7:0] a);
        always @(posedge clk) if(valid) assert(a != 0);
        endmodule
        module M(input clk, valid, input [7:0] a);
        Leaf left(clk, valid, a); Leaf right(clk, valid, a);
        endmodule''')
    catalogue, _ = catalogue_manifest_of(source, "M")
    failure, = (goal for goal, entry in catalogue.items() if entry.family == "artifact-failure")
    occurrences = [key for key, goal in catalogue.occurrences if goal == failure]
    assert len(occurrences) == len(set(occurrences)) == 2


def _command(tmp_path, source):
    task_contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    task = api.admit(task_contract, api.realization_of_rtl_source(
        source, top="M", contract=task_contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"),
                             api.GenerationSpec(8, 2, 2, 2))
    return handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        search_policy=api.SearchPolicy(time_budget_s=0))


@pytest.mark.cosim
@pytest.mark.parametrize("check,fails", [
    ("assert(a == a);", False),
    ("assert(a != 0);", True),
    ("if(1'b0) assert(a != 0);", False),
    ('unique case(a) 0:; default:; endcase', False),
    ('unique case(a) 1:; endcase', True),
    ('priority case(a) 1:; endcase', True),
    ('unique0 case(a) 0:; 0:; endcase', True),
])
def test_default_public_assertion_decision(tmp_path, check, fails):
    request = _command(tmp_path, RTL.replace("CHECK", check))
    failures = {goal for goal, entry in request.catalogue.items() if entry.family == "artifact-failure"}
    assert len(failures) == 1
    result = api.handle_command(request)
    assert result.decision.authoritative, result.as_dict()
    assert result.decision.passed is not fails, result.as_dict()
    assert set(result.evaluation.safety_hits) == (failures if fails else set())
    assert result.evaluation.mismatch_count == 0
    if fails:
        assert result.decision.failures
        assert all(failure.origin is api.FailureOrigin.CANDIDATE
                   for _, failure in result.decision.failures)


@pytest.mark.cosim
def test_source_failure_only_catalogue_still_has_public_native_observation(tmp_path):
    source = '''module M(input clk,rst,in_valid,out_ready,input [7:0] a,
        output in_ready,out_valid,output [7:0] y);
        assign in_ready=out_ready; assign out_valid=in_valid; assign y=a;
        always @(posedge clk) assert(a == a);
        endmodule'''
    path = tmp_path / "source.sv"
    path.write_text(source)
    catalogue, sites = catalogue_manifest_of(path, "M")
    assert not sites
    assert len(catalogue) == 1
    assert all(goal.family == "artifact-failure" for goal in catalogue.values())
    result = api.handle_command(_command(tmp_path, source))
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    assert result.evaluation.safety_hits == ()
    assert result.evaluation.records
    assert all(trace.source_evidence is not None for trace in result.evaluation.records)
