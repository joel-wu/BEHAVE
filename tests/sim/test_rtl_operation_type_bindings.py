"""Computation types stay exact; read-only predicate carriers are lossless."""

import subprocess

import pytest

from behave.sim.core.observation_goal import GoalCondition, GoalField
from behave.sim.core.operation_goal import OperationGoal
from behave.sim.rtl.source_elaboration import ElaborationEnvironment
from behave.sim.rtl.source_operation_events import CaptureType, OperationCapture
from behave.sim.rtl.source_operation_types import CaptureTypes


def _binding(*, witness=True):
    variants = []
    for four_state in (False, True):
        kind = CaptureType(4, False, four_state)
        capture = OperationCapture("+", (kind, kind), kind)
        types = (("T'(0)", (4, False, four_state)),) if witness else ()
        variants.append((capture, (ElaborationEnvironment((), types),)))
    return CaptureTypes(tuple(variants))


def test_state_kind_needs_no_runtime_unknown_discriminator():
    original, widths, observers, types = _binding().bind()
    assert not original.result.four_state
    assert widths == ("4", "4", "4") and types == ("T'(0)",) * 3
    (guard, observer), = observers
    assert guard == "((1'b1))"
    assert all(value.four_state for value in (*observer.operands, observer.result))
    assert original.goal_values() == observer.goal_values()


def test_missing_computational_kind_witness_does_not_pick_the_first_type():
    with pytest.raises(ValueError, match="compiler-bound scalar-kind witness"):
        _binding(witness=False).bind()


def test_boolean_result_preserves_state_kind_without_inheriting_operand_signedness():
    variants = []
    for state in (False, True):
        capture = OperationCapture("!", (CaptureType(4, True, state),), CaptureType(1, False, state))
        env = ElaborationEnvironment((), (("T'(0)", (4, True, state)),))
        variants.append((capture, (env,)))
    _, widths, observers, types = CaptureTypes(tuple(variants)).bind()
    assert widths == ("4", "1") and len(observers) == 1
    assert types == ("T'(0)", "unsigned'(T'(0))")


@pytest.mark.eda("iverilog", "vvp")
def test_shared_predicate_carrier_retains_unknowns_and_two_state_conversion(tmp_path):
    binding = _binding()
    _, _, ((_, observer),), _ = binding.bind()
    rule = OperationGoal("eight", GoalCondition("eq", GoalField("result"), 8))
    source = '''module M;
    logic [3:0] a,b,logic_result,logic_plain;
    bit [3:0] bit_result,bit_plain,bit_a,bit_b;
    int sample;
    function automatic void hit(input int id, input int unsigned scope);
      $display("hit %0d", sample);
    endfunction
    function automatic void unknown(input int id, input int unsigned scope);
      $display("unknown %0d", sample);
    endfunction
    ''' + observer.predicate_callback("observe", ((0, rule),), hit="hit", unreadable="unknown")
    for (capture, _), name in zip(binding.variants, ("bit_compute", "logic_compute"), strict=True):
        source += capture.function(name, "observe")
    source += '''initial begin
      for(int i=0;i<16;i=i+1) begin
        a=i; b=1; bit_a=a; bit_b=b;
        bit_plain=bit_a+bit_b; logic_plain=a+b;
        sample=2*i; bit_result=bit_compute(a,b,0);
        sample=2*i+1; logic_result=logic_compute(a,b,0);
        if(bit_result !== bit_plain || logic_result !== logic_plain)
          $fatal(1,"observation changed computation");
      end
      a=4'bx111; b=1; bit_a=a; bit_b=b;
      bit_plain=bit_a+bit_b; logic_plain=a+b;
      sample=32; bit_result=bit_compute(a,b,0);
      sample=33; logic_result=logic_compute(a,b,0);
      if(bit_result !== bit_plain || logic_result !== logic_plain)
        $fatal(1,"X conversion changed computation");
      $finish;
    end endmodule'''
    (tmp_path / "design.sv").write_text(source)
    subprocess.run(["iverilog", "-g2012", "-s", "M", "-o", "sim", "design.sv"],
                   cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
    run = subprocess.run(["vvp", "sim"], cwd=tmp_path, check=True,
                         capture_output=True, text=True, timeout=30)
    hits = {int(line.split()[1]) for line in run.stdout.splitlines() if line.startswith("hit ")}
    unknowns = {int(line.split()[1]) for line in run.stdout.splitlines() if line.startswith("unknown ")}
    assert hits == {14, 15, 32}
    assert unknowns == {33}
