"""Independent four-state oracle for the emitted truth-preserving wrapper."""

import pytest

from behave.sim.rtl.source_expression_events import choice_function, choice_wrapper

from .test_rtl_source_plan import _run

pytestmark = pytest.mark.eda("iverilog", "vvp")


@pytest.mark.parametrize("observed", [False, True])
def test_choice_truth_and_impure_predicate_are_preserved(tmp_path, observed):
    before, after = choice_wrapper("10", "20", observed=f"1'b{int(observed)}")
    source = """module M;
      integer callbacks=0, reads=0;
      integer recorded;
      logic [3:0] value, reference, observed;
      function automatic void record(input int unsigned id, scope);
        callbacks=callbacks+1; recorded=id;
      endfunction
      function automatic [3:0] sample();
        reads=reads+1; sample=value;
      endfunction
    """ + choice_function("record") + """
      task automatic check(input logic [3:0] data, input int expected_id);
        value=data;
        reference=sample() ? 4'b1010 : 4'b1001;
        if(reads != 1) $fatal(1,"plain predicate evaluated more than once");
        reads=0;
        observed=""" + before + "sample()" + after + """ ? 4'b1010 : 4'b1001;
        if(reads != 1 || callbacks != """ + str(int(observed)) + """)
          $fatal(1,"wrapper changed evaluation or observation count");
        if(reference !== observed) $fatal(1,"four-state value changed");
        if(""" + str(int(observed)) + """ && recorded !== expected_id)
          $fatal(1,"incorrect truth event");
        reads=0; callbacks=0;
      endtask
      initial begin
        check(4'b0000,20);
        check(4'b1000,10);
        check(4'bx000,32'hffffffff);
        check(4'bz000,32'hffffffff);
        check(4'b1x0z,10);
        $finish;
      end
    endmodule
    """
    (tmp_path / "design.sv").write_text(source)
    _run(tmp_path, "iverilog", "-g2012", "-s", "M", "-o", "probe", "design.sv")
    _run(tmp_path, "vvp", "probe")


@pytest.mark.parametrize("operator", ["&&", "||"])
def test_logical_terms_preserve_four_state_values_and_short_circuit_effects(tmp_path, operator):
    left_before, left_after = choice_wrapper("10", "20")
    right_before, right_after = choice_wrapper("11", "21")
    expression = f"{left_before}sample(left,1){left_after} {operator} {right_before}sample(right,2){right_after}"
    values = [("4'b0000", False), ("4'b1000", True), ("4'bx000", None),
              ("4'bz000", None), ("4'b1x0z", True)]
    cases = []
    for left, truth in values:
        for right, right_truth in values:
            evaluates_right = truth is not (operator == "||")
            left_id = "32'hffffffff" if truth is None else "10" if truth else "20"
            right_id = "32'hffffffff" if right_truth is None else "11" if right_truth else "21"
            cases.append(f"check({left},{right},{3 if evaluates_right else 1},{left_id},{right_id});")
    source = """module M;
      integer reads=0, callbacks=0, first_id, second_id;
      logic reference, observed;
      function automatic [3:0] sample(input logic [3:0] value, input int id);
        if(reads & id) $fatal(1,"repeated operand evaluation");
        reads=reads | id; sample=value;
      endfunction
      function automatic void record(input int unsigned id, scope);
        if(callbacks == 0) first_id=id; else second_id=id;
        callbacks=callbacks+1;
      endfunction
    """ + choice_function("record") + f"""
      task automatic check(input logic [3:0] left, right,
                           input int expected_reads, expected_first, expected_second);
        reads=0; callbacks=0;
        reference=sample(left,1) {operator} sample(right,2);
        if(reads != expected_reads) $fatal(1,"unexpected native short circuit");
        reads=0;
        observed={expression};
        if(reads != expected_reads) $fatal(1,"wrapper changed operand evaluation");
        if(reference !== observed) $fatal(1,"wrapper changed logical result");
        if(callbacks != (expected_reads == 3 ? 2 : 1)) $fatal(1,"incorrect callback count");
        if(first_id !== expected_first) $fatal(1,"incorrect left truth");
        if(expected_reads == 3 && second_id !== expected_second) $fatal(1,"incorrect right truth");
      endtask
      initial begin
        {''.join(cases)}
        $finish;
      end
    endmodule
    """
    (tmp_path / "design.sv").write_text(source)
    _run(tmp_path, "iverilog", "-g2012", "-s", "M", "-o", "probe", "design.sv")
    _run(tmp_path, "vvp", "probe")
