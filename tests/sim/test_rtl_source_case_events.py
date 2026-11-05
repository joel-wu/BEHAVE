"""Every case-label goal: native execution, local answer and witness replay."""

import json

import pytest

from behave.sim import api

from .test_rtl_bounded_source_loops import _assert_closure
from .test_rtl_source_activation_scheduling import _assert_entries
from .test_rtl_source_ir import _native_agreement

pytestmark = [pytest.mark.eda('slang', 'verilator', 'make'), pytest.mark.solver]


@pytest.mark.parametrize('items,expected', [
    ('f(1):q=16; f(5):q=32; default:q=64;', lambda a: 16 if a == 1 else 32 if a == 5 else 64),
    ('f(1),f(2):q=16; default:q=64;', lambda a: 16 if a in (1, 3) else 64),
    ('default:q=64; f(1):q=16; f(5):q=32;', lambda a: 16 if a == 1 else 32 if a == 5 else 64),
    ('1:q=16; f(a):q=32; default:q=64;', lambda a: 16 if a == 1 else 32 if a & 1 else 64),
    ('f(1):q=16; f(1):q=32; default:q=64;', lambda a: 16 if a == 1 else 64),
    (' '.join(f'f({i}):q={i};' for i in range(9)) + ' default:q=64;',
     lambda a: a-1 if a in (1,3,5,7,9) else 64),
    (' '.join(f'f({i}):q={i};' for i in range(17)) + ' default:q=64;',
     lambda a: a-1 if a & 1 else 64),
])
def test_complete_case_catalogue(tmp_path, record_property, items, expected):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x);
        if(1'b0) return 0;
        if(x[0]) return x;
        return x+1;
      endfunction
      always @(posedge clk) begin q=0; case(a) {items} endcase end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), expected)


def test_nested_case_labels_use_one_order(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] g(input [3:0] x);
        if(x==9) return 3; return x;
      endfunction
      function automatic [3:0] f(input [3:0] x);
        case(x) 1:return 1; g(x):return 5; default:return 7; endcase
      endfunction
      always @(posedge clk) case(a) 1:q=16; f(a):q=32; default:q=64; endcase
    endmodule'''
    _native_agreement(tmp_path, source)


def test_later_label_search_changes_only_the_mutable_input(tmp_path, record_property):
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_batch

    from .test_rtl_source_batch import _lower
    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _boundary, _env, _goal

    source = '''module M(input clk,input [3:0] a,output reg [3:0] q);
      initial q=0;
      function automatic [3:0] f(input [3:0] v);
        if(v==9) return v; return v+1;
      endfunction
      always @(posedge clk) case(a) 0:q<=0; f(a):q<=13; default:q<=a; endcase
    endmodule'''
    batch, plan = _lower(tmp_path, source)
    targets = (_goal(source, plan, 'v==9'), _goal(source, plan, 'case', 'arm_1'))
    baseline, observed, replay = _prepare_clocked_replay(tmp_path, source, plan)
    assert not set(targets) & observed[0]
    assert _goal(source, plan, 'v==9', 'false') not in observed[0]
    state, inputs, names = _boundary(batch, {'q': baseline[-1], 'a': 0}, (True,))
    for target in targets:
        answer = query_source_batch(batch, _env(batch), state, inputs, (target,),
                                    mutable_inputs=(names['a'],), timeout_ms=30_000)
        assert answer.outcome is probing.Outcome.SAT, answer.detail
        assert answer.candidate[names['a']] == 9
        values, hits = replay([0, 9])
        assert values == [0, 13] and set(targets) <= hits[1]
    record_property('search_hits', {'baseline': len(observed[0]),
                                  'after': len(hits[0] | hits[1]), 'targets': len(targets)})


@pytest.mark.eda('iverilog', 'vvp')
@pytest.mark.parametrize('labels', ['label_value(1)', 'label_value(1),label_value(2)'])
def test_side_effect_counts_match_independent_simulator(tmp_path, labels):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int unsigned calls=0;
      function automatic logic [3:0] label_value(input logic [3:0] x);
        calls=calls+1; return x;
      endfunction
      always @(posedge a[0]) begin
        calls=0;
        case(a[4:1])
          {labels}:q=16;
          label_value(5):q=32;
          default:q=64;
        endcase
        q=q+(calls<<8);
      end
    endmodule'''
    values = [value for a in range(16) for value in (a << 1, (a << 1) | 1)]
    # The counter is a numeric oracle for calls; helper lexical scope is not a
    # module-instance scope, so compare the enclosing source event separately.
    _assert_entries(tmp_path, source, values, ('q=q+(calls<<8);',))


@pytest.mark.eda('iverilog', 'vvp')
@pytest.mark.parametrize('expression', [
    'label_value(a[1]) || label_value(a[2])',
    'label_value(a[1]) && label_value(a[2])',
    'a[1] ? label_value(a[2]) : label_value(a[3])',
])
def test_short_circuit_stores_are_not_unconditional(tmp_path, expression):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int unsigned calls=0;
      function automatic logic label_value(input logic x);
        calls=calls+1; return x;
      endfunction
      always @(posedge a[0]) begin
        calls=0;
        q={expression};
        q=q+(calls<<8);
      end
    endmodule'''
    values = [value for a in range(8) for value in (a << 1, (a << 1) | 1)]
    _assert_entries(tmp_path, source, values, ('q=q+(calls<<8);',))


@pytest.mark.cosim
@pytest.mark.parametrize('effect', ['none', 'blocking', 'nonblocking'])
def test_public_search_reaches_later_case_label(tmp_path, record_property, effect):
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower

    blocking = effect == 'blocking'
    write = {'none': '', 'blocking': 'selected=~v;', 'nonblocking': 'y<=~v;'}[effect]
    source = f"""module M(input clk,rst,in_valid,input [15:0] a,
      output reg out_valid,output reg [15:0] y);
      {'reg [15:0] selected;' if blocking else ''}
      function automatic [15:0] label_value(input [15:0] v);
        {write}
        if(1'b0) return 0;
        if(v==16'hc35b) return v;
        return v+1;
      endfunction
      always @(posedge clk) begin
        if(rst) begin y<=0; out_valid<=0; end
        else begin
          out_valid<=in_valid;
          if(in_valid) begin
            {'selected=a;' if blocking else ''}
            case({'selected' if blocking else 'a'})
            0:y<=a;
            label_value(a):y<=a;
            default:y<=a;
            endcase
          end
        end
      end
    endmodule"""
    kind = api.FiniteType(16)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", kind, "a")],
                     api.sampled_edge("in", valid="in_valid"), "c", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", kind, "y")],
                     api.sampled_edge("out", valid="out_valid"), "c", kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)], [api.ClockDomain("c", "clk", "rst")],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n    def reset(self):\n        pass\n'
                        b'    def process(self,a):\n        return {"y":a}\n'}, config={"class": "M"})
    path = tmp_path / "design.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    _machine, plan = _lower(tmp_path, source)
    targets = {_goal(source, plan, "v==16'hc35b"),
               _goal(source, plan, "case(", "arm_1")}
    dead = _goal(source, plan, "1'b0")
    declared = handle.frontend_catalogue.as_dict()
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", baseline=baseline, reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path / "search")[1:],
        search_policy=api.SearchPolicy(time_budget_s=60), run_analysis=False))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    search, = result.generated.searches
    before = set(search.telemetry["baseline_coverage"]["hits"])
    after = set(search.telemetry["coverage"]["hits"])
    native_hits = {goal for run in result.evaluation.results for row in run.trace.records
                   for goal, hit in row.hits if hit is True}
    assert not targets & before
    assert targets <= after <= native_hits
    assert dead in handle.frontend_catalogue and dead not in after
    assert handle.frontend_catalogue.as_dict() == declared
    assert result.analysis is None
    record_property("case_search", json.dumps({
        "baseline_hits": len(before), "search_hits": len(after),
        "new_hits": len(after - before), "probes": search.telemetry["probes"],
        "termination": search.telemetry["termination"], "retained_unhit": dead}))
