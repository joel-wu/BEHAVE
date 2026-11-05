"""Source batch proposals preserve state, input ownership and native hits."""

from itertools import product

import pytest

from behave.sim.symbolic import interp, ranges
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_batch
from behave.sim.rtl.source_goals import manifest_of

from .test_rtl_source_batch import _lower
from .test_rtl_source_locations import _prepare_clocked_replay

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.fixture(autouse=True)
def _search_lifetime():
    # Standalone primitives no longer invent a per-query timeout. Give these
    # direct calls the same finite owning stage used by the public scheduler.
    from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

    with search_scope(SearchBudget(SearchPolicy()).direction(1)):
        yield


def _goal(source, plan, text, event="true", family="control-choice"):
    manifest = manifest_of(plan.declarations, filename="design.sv")
    # An outer if/loop's source span includes all nested statements. Select the
    # smallest matching site, not whichever goal hash sorts first.
    matches = [goal for goal in manifest.values()
               if goal.family == family and goal.event == event
               and text in source.encode()[goal.where("start"):goal.where("end")].decode()]
    return min(matches, key=lambda goal: (goal.where("end") - goal.where("start"),
                                         goal.where("start"))).id


def _boundary(batch, values, active):
    names = {field.name: name for name, field in batch.storage}
    # Unique spellings are only a fixture convenience. The production bridge
    # consumes compiler-resolved storage names, including instance identity.
    assert len(names) == len(batch.storage)
    cells = {names[key]: value for key, value in values.items()}
    state = {name: cells[name] for name in batch.procedure.state}
    inputs = {name: cells[name] for name in batch.procedure.inputs if name not in batch.active}
    inputs.update(zip(batch.active, active, strict=True))
    return state, inputs, names


def _env(batch):
    return ranges.fixpoint(batch.procedure, {**batch.procedure.state_ranges, **batch.input_ranges})


def test_source_query_matches_every_fixed_state_and_activation(tmp_path):
    source = """module M(input clk,input [1:0] a,b,output reg [1:0] q);
      always @(posedge clk) if(q==a && b==2) q<=1; else q<=0;
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    env = _env(batch)
    target = _goal(source, plan, "q==a")
    for q, b, active in product(range(4), range(4), (False, True)):
        state, inputs, names = _boundary(batch, {"q": q, "a": 0, "b": b}, (active,))
        answer = query_source_batch(batch, env, state, inputs, (target,), mutable_inputs=(names["a"],))
        possible = active and b == 2
        assert answer.scope is probing.Scope.RTL_LOCAL_HIT
        assert answer.outcome is (probing.Outcome.SAT if possible else probing.Outcome.LOCAL_UNSAT), answer.detail
        if possible:
            assert answer.goal == target
            assert answer.candidate == {names["a"]: q, names["b"]: b}
            marks = interp.run_step(batch.procedure, {**inputs, **answer.candidate}, state)[2]
            assert target in {goal for mark, _, goal in batch.occurrences if mark in marks}


def test_helper_occurrences_do_not_free_a_fixed_operand(tmp_path):
    source = """module M(input clk,mode,input [3:0] a,b,output reg [3:0] q);
      function automatic [3:0] f(input [3:0] v);
        if(v==9) return 13; return v;
      endfunction
      always @(posedge clk) q <= mode ? f(a) : f(b);
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    env = _env(batch)
    target = _goal(source, plan, "v==9")
    assert len([key for _, key, goal in batch.occurrences if goal == target]) == 2
    for mode in (0, 1):
        state, inputs, names = _boundary(batch, {"q": 0, "a": 0, "b": 0, "mode": mode}, (True,))
        answer = query_source_batch(batch, env, state, inputs, (target,), mutable_inputs=(names["a"],))
        assert answer.outcome is (probing.Outcome.SAT if mode else probing.Outcome.LOCAL_UNSAT), answer.detail
        if mode:
            assert answer.goal == target
            assert answer.candidate == {names["a"]: 9, names["b"]: 0, names["mode"]: 1}


@pytest.mark.parametrize("fault", ["state", "input", "extra-input", "flag", "mutable-flag", "mutable-state", "goal"])
def test_incomplete_or_different_boundaries_do_not_become_free_symbols(tmp_path, fault):
    source = "module M(input clk,input [3:0] a,output reg [3:0] q); always @(posedge clk) if(q==a) q<=a; endmodule"
    batch, plan = _lower(tmp_path, source)
    state, inputs, names = _boundary(batch, {"q": 0, "a": 0}, (True,))
    mutable = (names["a"],)
    target = _goal(source, plan, "q==a")
    if fault == "state":
        state.clear()
    elif fault == "input":
        inputs.pop(names["a"])
    elif fault == "extra-input":
        inputs["unknown"] = 0
    elif fault == "flag":
        inputs.pop(batch.active[0])
    elif fault == "mutable-flag":
        mutable = batch.active
    elif fault == "mutable-state":
        mutable = (names["q"],)
    else:
        target = "absent-source-goal"
    answer = query_source_batch(batch, _env(batch), state, inputs, (target,), mutable_inputs=mutable)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail


def test_signed_source_inputs_retain_the_compiler_domain(tmp_path):
    source = "module M(input clk,input signed [3:0] a,output reg q); always @(posedge clk) if(a < -2) q<=1; else q<=0; endmodule"
    batch, plan = _lower(tmp_path, source)
    state, inputs, names = _boundary(batch, {"q": 0, "a": 0}, (True,))
    answer = query_source_batch(batch, _env(batch), state, inputs, (_goal(source, plan, "a < -2"),),
                                mutable_inputs=(names["a"],))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert -8 <= answer.candidate[names["a"]] < -2


def test_solver_does_not_read_a_pending_write_as_current_state(tmp_path):
    source = """module M(input c0,c1,input [3:0] a,output reg [3:0] q,r);
      always @(posedge c0) q<=a;
      always @(posedge c1) if(q==9) r<=a; else r<=0;
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    env = _env(batch)
    target = _goal(source, plan, "q==9")
    for q in (0, 9):
        state, inputs, names = _boundary(batch, {"q": q, "r": 0, "a": 0}, (True, True))
        answer = query_source_batch(batch, env, state, inputs, (target,), mutable_inputs=(names["a"],))
        assert answer.outcome is (probing.Outcome.SAT if q == 9 else probing.Outcome.LOCAL_UNSAT), answer.detail


@pytest.mark.eda("verilator", "make")
def test_baseline_then_source_proposal_is_confirmed_by_native_replay(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      initial q=0;
      function automatic [3:0] f(input [3:0] v);
        if(v==9) return 13; return v;
      endfunction
      always @(posedge clk) q<=f(a);
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    target = _goal(source, plan, "v==9")
    baseline, baseline_hits, replay = _prepare_clocked_replay(tmp_path, source, plan)
    assert target not in baseline_hits[0]
    state, inputs, names = _boundary(batch, {"q": baseline[-1], "a": 0}, (True,))
    answer = query_source_batch(batch, _env(batch), state, inputs, (target,), mutable_inputs=(names["a"],))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.goal == target
    outputs, hits = replay([0, answer.candidate[names["a"]]])
    assert outputs == [0, 13]
    assert target not in hits[0] and target in hits[1]


@pytest.mark.eda("verilator", "make")
def test_qualified_case_residual_proposal_is_confirmed_by_native_replay(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      initial q=0;
      always @(posedge clk) unique case(a)
        4'd9: q<=13;
        default: q<=a;
      endcase
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    target = _goal(source, plan, "case", "arm_0")
    baseline, baseline_hits, replay = _prepare_clocked_replay(tmp_path, source, plan)
    assert target not in baseline_hits[0]
    state, inputs, names = _boundary(batch, {"q": baseline[-1], "a": 0}, (True,))
    answer = query_source_batch(batch, _env(batch), state, inputs, (target,),
                                mutable_inputs=(names["a"],))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names["a"]] == 9
    outputs, hits = replay([0, answer.candidate[names["a"]]])
    assert outputs == [0, 13]
    assert target not in hits[0] and target in hits[1]


@pytest.mark.eda("verilator", "make")
def test_case_label_helper_residual_is_confirmed_by_native_replay(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      initial q=0;
      function automatic [3:0] label_value(input [3:0] v);
        if(v==9) return v;
        return v+1;
      endfunction
      always @(posedge clk) case(a)
        label_value(a): q<=13;
        default: q<=a;
      endcase
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    targets = (_goal(source, plan, "v==9"), _goal(source, plan, "case", "arm_0"))
    baseline, baseline_hits, replay = _prepare_clocked_replay(tmp_path, source, plan)
    assert not set(targets) & baseline_hits[0]
    state, inputs, names = _boundary(batch, {"q": baseline[-1], "a": 0}, (True,))
    for target in targets:
        answer = query_source_batch(batch, _env(batch), state, inputs, (target,),
                                    mutable_inputs=(names["a"],))
        assert answer.outcome is probing.Outcome.SAT, answer.detail
        assert answer.candidate[names["a"]] == 9
        outputs, hits = replay([0, answer.candidate[names["a"]]])
        assert outputs == [0, 13]
        assert target not in hits[0] and set(targets) <= hits[1]


def test_exhaustive_case_local_unsat_does_not_remove_the_no_match_goal(tmp_path):
    source = """module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) unique case(a)
        0:q<=0; 1:q<=1; 2:q<=2; 3:q<=3;
      endcase
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    target = _goal(source, plan, "case", "no-match")
    state, inputs, names = _boundary(batch, {"q": 0, "a": 0}, (True,))
    before = manifest_of(plan.declarations, filename="design.sv").as_dict()
    answer = query_source_batch(batch, _env(batch), state, inputs, (target,),
                                mutable_inputs=(names["a"],))
    assert answer.outcome is probing.Outcome.LOCAL_UNSAT, answer.detail
    assert answer.scope is probing.Scope.RTL_LOCAL_HIT
    assert manifest_of(plan.declarations, filename="design.sv").as_dict() == before


@pytest.mark.parametrize('statement,condition', [
    ('unique case(a) 0:; endcase', 'a!=0'),
    ('priority case(a) 0:; endcase', 'a!=0'),
    ('unique0 case(a) 0:; 0:; endcase', 'a==0'),
])
def test_case_failure_is_searchable_but_its_later_branch_is_not(tmp_path, statement, condition):
    source = f'''module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin
        {statement}
        if({condition}) q=9;
      end
    endmodule'''
    batch, plan = _lower(tmp_path, source)
    state, inputs, names = _boundary(batch, {'q': 0, 'a': 0}, (True,))
    failure = _goal(source, plan, 'case(a)', 'failure', 'artifact-failure')
    after = _goal(source, plan, condition, 'true')
    answer = query_source_batch(batch, _env(batch), state, inputs, (failure,),
                                mutable_inputs=(names['a'],))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    _, _, hits, error = interp.run_step(batch.procedure, {**inputs, **answer.candidate}, state)
    assert error in {'RTLCaseNoMatch', 'RTLCaseOverlap'}
    observed = {goal for mark, _, goal in batch.occurrences if mark in hits}
    assert failure in observed and after not in observed
    unreachable = query_source_batch(batch, _env(batch), state, inputs, (after,),
                                     mutable_inputs=(names['a'],))
    assert unreachable.outcome is probing.Outcome.LOCAL_UNSAT, unreachable.detail


@pytest.mark.eda("verilator", "make")
def test_native_replay_confirms_each_structural_goal_family(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      initial q=0;
      always @(posedge clk) begin
        q<=a;
        if(a==9 && a[0]) begin
          for(int i=0;i<2;i++) q<=13;
        end
        assert(a!=9) else q<=13;
      end
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    env = _env(batch)
    baseline, baseline_hits, replay = _prepare_clocked_replay(tmp_path, source, plan)
    state, inputs, names = _boundary(batch, {"q": baseline[-1], "a": 0}, (True,))
    targets = [_goal(source, plan, text, event, family) for family, text, event in (
        ("location", "q<=13", "entry"),
        ("control-choice", "a==9", "true"),
        ("boolean-expression", "a==9", "true"),
        ("loop-activation", "for", "nonzero"),
        ("artifact-failure", "assert", "failure"),
    )]
    for target in targets:
        assert target not in baseline_hits[0]
        answer = query_source_batch(batch, env, state, inputs, (target,), mutable_inputs=(names["a"],))
        assert answer.outcome is probing.Outcome.SAT, answer.detail
        assert answer.goal == target
        outputs, hits = replay([0, answer.candidate[names["a"]]])
        assert outputs == [0, 13]
        assert target in hits[1]
