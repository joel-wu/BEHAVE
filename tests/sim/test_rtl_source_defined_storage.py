"""Partial local writes define only the source bits actually assigned."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_bounded_source_loops import _assert_closure, _lower

pytestmark = [pytest.mark.eda('slang', 'verilator', 'make'), pytest.mark.solver]


@pytest.mark.parametrize('body,complete', [
    ("case(v) 0:f=1; 1:f=2; default:f=3; endcase", True),
    ("case(v) 0:f=1; 1:f=2; endcase", False),
    ("if(v[0]) f=1; else f=2;", True),
    ("if(v[0]) f=1;", False),
    ("for(int i=0;i<8;i++) f[i]=v[i%4];", True),
    ("for(int i=0;i<7;i++) f[i]=v[i%4];", False),
])
def test_deferred_helper_definition_is_local_not_a_history_obligation(tmp_path, body, complete):
    from .test_rtl_source_scheduler import _lower as lower_scheduler

    source = f'''module M(input [3:0] a,output logic [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        {body}
      endfunction
      always_comb q=f(a);
    endmodule'''
    machine, _ = lower_scheduler(tmp_path, source, defer_domains=True)
    undefined = [cause for _, cause in machine.domain_checks
                 if cause.kind == 'rtl-source-ir-return-undefined']
    assert bool(undefined) is not complete


@pytest.mark.parametrize('body,expected', [
    ('cells[0][1]=v; return cells[0][1];', lambda a: a),
    ('cells[0][0]=1; cells[0][1]=v; cells[1][0]=3; cells[1][1]=4; '
     'return cells[0][0]+cells[0][1]*2+cells[1][0]*4+cells[1][1]*8;', lambda a: 45+2*a),
    ('int ix; ix=v&1; cells[ix][1]=v+3; return cells[ix][1];', lambda a: a+3),
    ('if(v[0]) cells[0][1]=v; else cells[1][0]=v; '
     'if(v[0]) return cells[0][1]; else return cells[1][0];', lambda a: a),
    ('for(int i=0;i<2;i++) for(int j=0;j<2;j++) cells[i][j]=v+i+j; '
     'return cells[0][0]+cells[0][1]+cells[1][0]+cells[1][1];', lambda a: 4*a+4),
])
def test_partial_array_catalogue_has_native_queries(tmp_path, record_property, body, expected):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1][0:1];
        {body}
      endfunction
      always @(posedge clk) begin if(1'b0) q=0; q=f(a); end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize('body', [
    'cells[0][0]=v; return cells[0][1];',
    'if(v[0]) cells[0][1]=v; return cells[0][1];',
    'cells[0][1]=v; return cells[v&1][1];',
    'cells[0][1]=cells[0][1]+1; return cells[0][1];',
])
def test_uninitialized_array_reads_remain_explicit(tmp_path, body):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1][0:1];
        {body}
      endfunction
      always @(posedge clk) q=f(a);
    endmodule'''
    with pytest.raises(Refused, match='rtl-source-ir-local-undefined'):
        _lower(tmp_path, source)


@pytest.mark.parametrize('direction', ['output', 'ref'])
def test_partial_writes_through_formals_define_actual_bits(tmp_path, record_property, direction):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic void fill(input [3:0] v,{direction} logic [7:0] x);
        x[7:4]=15; x[3:0]=v;
        if(1'b0) x=0;
      endfunction
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1];
        fill(v,cells[1]); return cells[1];
      endfunction
      always @(posedge clk) q=f(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 240+a)


@pytest.mark.parametrize('body,expected', [
    ('f[3:0]=v; f[7:4]=v^15;', lambda a: a+16*(a^15)),
    ('logic [7:0] partial; partial[3:0]=v; return partial[3:0];', lambda a: a),
    ('bit [7:0] defaulted[0:1]; return defaulted[1];', lambda a: 0),
    ('logic [7:0] partial; if(v[0]) begin partial[3:0]=v; '
     'return partial[3:0]; end else return v;', lambda a: a),
])
def test_packed_and_two_state_storage_catalogues(tmp_path, record_property, body, expected):
    # Declarations stay before statements; the dead branch supplies an
    # explicitly retained local-UNSAT entry without changing the calculation.
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        {body}
      endfunction
      always @(posedge clk) begin if(1'b0) q=0; q=f(a); end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize('body,role', [
    ('f[3:0]=v;', 'return'),
    ('logic [7:0] x; x[3:0]=v; return x;', 'local'),
    ('logic [7:0] x; x[3:0]=v; return x[4];', 'local'),
])
def test_partial_definition_is_not_whole_definition(tmp_path, body, role):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v); {body} endfunction
      always @(posedge clk) q=f(a);
    endmodule'''
    with pytest.raises(Refused, match=f'rtl-source-ir-{role}-undefined'):
        _lower(tmp_path, source)


@pytest.mark.parametrize('low,high', [(0, 1), (1, 0), (-2, -1), (7, 6)])
def test_selected_definition_uses_declared_array_orientation(tmp_path, record_property, low, high):
    first = min(low, high)
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[{low}:{high}];
        int ix;
        ix={first}+int'(v&1);
        cells[ix][3:0]=v;
        return cells[ix][3:0];
      endfunction
      always @(posedge clk) begin if(1'b0) q=0; q=f(a); end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: a)


def test_changed_selector_does_not_reuse_old_definition(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1];
        int ix;
        ix=v&1; cells[ix]=v; ix=1-ix; return cells[ix];
      endfunction
      always @(posedge clk) q=f(a);
    endmodule'''
    with pytest.raises(Refused, match='rtl-source-ir-local-undefined'):
        _lower(tmp_path, source)


def test_whole_array_copyout_requires_every_output_cell(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic void fill(input [3:0] v,output logic [7:0] x[0:1]);
        x[0]=v; x[1]=v+1;
        if(1'b0) x[0]=99;
      endfunction
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1];
        fill(v,cells); return cells[0]+cells[1];
      endfunction
      always @(posedge clk) q=f(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 2*a+1)
    other = tmp_path/'missing-cell'
    other.mkdir()
    with pytest.raises(Refused, match='rtl-source-ir-output-undefined'):
        _lower(other, source.replace('x[1]=v+1;', ''))


@pytest.mark.parametrize('later_bad_call', [False, True])
def test_definition_proof_retains_caller_guards(tmp_path, record_property, later_bad_call):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1];
        if(v[0]) cells[1]=v;
        if(1'b0) return 0;
        return cells[1];
      endfunction
      always @(posedge clk) begin
        if(a[0]) q=f(a); else q=0;
        {'q=f(0);' if later_bad_call else ''}
      end
    endmodule'''
    if later_bad_call:
        with pytest.raises(Refused, match='rtl-source-ir-local-undefined'):
            _lower(tmp_path, source)
    else:
        _assert_closure(tmp_path, record_property, source, range(16), lambda a: a if a&1 else 0)


@pytest.mark.parametrize('deferred', [False, True])
def test_proof_only_storage_is_erased_without_changing_the_complete_relation(
        tmp_path, monkeypatch, record_property, deferred):
    import z3

    from behave.sim.symbolic import encoder, ir, ranges, typecheck as types
    from behave.sim.rtl.source_defined import DefinedStorage
    from behave.sim.rtl.source_ir import _sequence

    original = DefinedStorage.erase
    captured = []

    def capture(self, body):
        masks = {mask for mask, _, _ in self.cells.values()}
        captured.append((body, {name: types.INTEGER if name in masks else types.BOOLEAN
                                for name in self.private}))
        return original(self, body)

    monkeypatch.setattr(DefinedStorage, 'erase', capture)
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1];
        for(int i=0;i<2;i++) begin cells[i][3:0]=v; cells[i][7:4]=i; end
        if(v[0]) return cells[1];
        return cells[0];
      endfunction
      always @(posedge clk) q {'<=' if deferred else '='} f(a);
    endmodule'''
    _, lowered, _ = _lower(tmp_path, source)
    body, private = captured.pop()
    assert not captured and private
    proc = lowered.procedure
    assert private.keys().isdisjoint(proc.types)
    assert private.keys().isdisjoint(proc.locals)
    assert private.keys().isdisjoint(
        node.name for node in ir.expression_nodes(proc.body) if isinstance(node, ir.Var))
    assert private.keys().isdisjoint(
        node.var for node in ir.walk(lowered.activation) if isinstance(node, ir.Assign))
    initialization = [cmd for write in lowered.deferred for cmd in write.initialize()]
    before = ir.Procedure(proc.state, [], [*proc.locals, *private], proc.outputs,
                          _sequence([*initialization, body]), types={**proc.types, **private})
    before.state_ranges = proc.state_ranges
    left = encoder.encode_step(before, env=ranges.fixpoint(before, before.state_ranges), tag='proof-present')
    right = encoder.encode_step(proc, pre=left.pre, env=ranges.fixpoint(proc, proc.state_ranges),
                                tag='proof-erased')
    assert left.post.keys() == right.post.keys()
    assert left.hits.keys() == right.hits.keys()
    differences = [left.post[name] != right.post[name] for name in left.post]
    differences.extend(left.hits[goal] != right.hits[goal] for goal in left.hits)
    solver = z3.Solver()
    solver.add(left.constraint, right.constraint, z3.Or(differences))
    assert solver.check() == z3.unsat
    assert ir.cost(proc.body) < ir.cost(before.body)
    record_property('private_proof_erasure', {
        'private_locals': len(private), 'before_nodes': ir.cost(before.body),
        'after_nodes': ir.cost(proc.body), 'full_relation_miter': 'unsat'})


def test_definition_proof_must_succeed_before_erasure(tmp_path, monkeypatch):
    from behave.sim.rtl.source_defined import DefinedStorage

    def unexpected(self, body):
        pytest.fail('unproved source storage must never reach proof erasure')

    monkeypatch.setattr(DefinedStorage, 'erase', unexpected)
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] cells[0:1];
        cells[0]=v;
        return cells[1];
      endfunction
      always @(posedge clk) q=f(a);
    endmodule'''
    with pytest.raises(Refused, match='rtl-source-ir-local-undefined'):
        _lower(tmp_path, source)


@pytest.mark.cosim
@pytest.mark.parametrize('declaration,stores,value', [
    ('logic [15:0] cells[0:1][0:1];', 'cells[1][0]=v;', 'cells[1][0]'),
    ('logic [15:0] partial;', 'partial[7:0]=v[7:0]; partial[15:8]=v[15:8];', 'partial'),
])
def test_public_search_reads_only_defined_bits(tmp_path, record_property, declaration, stores, value):
    import json

    from behave.sim import api
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower as lower_scheduler

    source = f'''module M(input clk,rst,in_valid,input [15:0] a,
      output reg out_valid,output reg [15:0] y);
      function automatic [15:0] helper(input [15:0] v);
        {declaration}
        {stores}
        if(1'b0) return 0;
        if({value}==16'hc35b) return {value};
        return v;
      endfunction
      always @(posedge clk) begin
        if(rst) begin y<=0; out_valid<=0; end
        else begin out_valid<=in_valid; if(in_valid) y<=helper(a); end
      end
    endmodule'''
    kind = api.FiniteType(16)
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', kind, 'a')],
                     api.sampled_edge('in', valid='in_valid'), 'c', kind=api.Kind.TRANSACTION),
        api.Endpoint('out', 'out', [api.Field('y', kind, 'y')],
                     api.sampled_edge('out', valid='out_valid'), 'c', kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)], [api.ClockDomain('c', 'clk', 'rst')],
        protocol_instances=[api.ProtocolInstance('compute', ('in', 'out'))],
        source_read_rules=[api.SourceReadRule(value, 'magic',
            api.GoalCondition('eq', api.GoalField('value'), 0xc35b))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': b'class M:\n    def reset(self): pass\n'
                        b'    def process(self,a): return {"y":a}\n'}, config={'class': 'M'})
    path = tmp_path/'design.sv'
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, 'M', tmp_path/'build'))
    _, plan = lower_scheduler(tmp_path, source)
    target = _goal(source, plan, f"{value}==16'hc35b")
    dead = _goal(source, plan, "1'b0")
    declared = handle.frontend_catalogue.as_dict()
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path/'result', baseline=api.BaselinePolicy(case_limit=3), reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path/'search')[1:],
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=8, debug=True), run_analysis=False))
    (tmp_path/'result.json').write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    search, = result.generated.searches
    before = set(search.telemetry['baseline_coverage']['hits'])
    after = set(search.telemetry['coverage']['hits'])
    confirmed = {goal for run in result.evaluation.results for row in run.trace.records
                 for goal, hit in row.hits if hit is True}
    assert target not in before and target in after
    assert after <= confirmed
    custom = {goal.id for goal in handle.frontend_catalogue.values()
              if goal.family == 'value-observation'}
    assert custom and custom.isdisjoint(before) and custom <= after
    assert dead in handle.frontend_catalogue and dead not in after
    assert handle.frontend_catalogue.as_dict() == declared
    record_property('defined_storage_search', json.dumps({
        'baseline_hits': len(before), 'search_hits': len(after),
        'probes': search.telemetry['probes'], 'termination': search.telemetry['termination']}))
