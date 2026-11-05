"""Transitive frame-local calls are distinct from externally visible writes."""

import pytest
from dataclasses import replace

from behave.sim.rtl.source_call_effects import FrameLocalCalls
from behave.sim.rtl.source_declarations import _nodes
from tests.sim.test_rtl_source_net_drivers import _prepare
from tests.sim.test_rtl_source_locations import _collector
from tests.sim.test_rtl_combinational_events import _trace


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.parametrize('target,accepted', [('scratch', True), ('shared', False)])
def test_nested_copyback_only_certifies_automatic_local_actuals(tmp_path, target, accepted):
    source = f'''module M(input [7:0] a,output wire [31:0] q);
      int shared;
      function automatic void put(input int v,output int result); result=v+1; endfunction
      function automatic int f(input int v);
        int scratch;
        put(v,{target});
        return {target};
      endfunction
      assign q=a;
    endmodule'''
    _, document, _ = _prepare(tmp_path, source)
    routines = {str(node['addr']): node for node in _nodes(document) if node.get('kind') == 'Subroutine'}
    effects = FrameLocalCalls(routines, _nodes)
    names = {node['name']: identity for identity, node in routines.items()}
    assert effects(names['f']) is accepted
    assert not effects(names['put'])  # A direct output call still changes its caller.
    if accepted:
        assert not effects.nonlocal_reads(names['f'])


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.parametrize('nested', [False, True])
def test_local_rhs_helper_nba_matches_uninstrumented_outputs(tmp_path, nested):
    helper = '''function automatic void put(input int v,output int result);
        result=v+1;
      endfunction
      function automatic int f(input int v);
        int scratch;
        put(v,scratch); return scratch;
      endfunction''' if nested else '''function automatic int f(input int v);
        int result;
        result=0;
        for(int i=0;i<4;i=i+1) begin
          if(!v[i]) continue;
          result=i; break;
        end
        return result;
      endfunction'''
    source = '''module M(input [7:0] a,output wire [31:0] q);
      int cells[0:1];
      function automatic int ix(input int v); return v&1; endfunction
      HELPER
      always @(posedge a[0]) cells[ix(a[1])] <= f(a[5:2]);
      assign q=cells[0]+cells[1];
    endmodule'''.replace('HELPER', helper)
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    values = [value for v in range(16) for value in (v*4+(v&1)*2, v*4+(v&1)*2+1)]
    driver = '''const unsigned values[]={VALUES};
      for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }'''.replace('VALUES', ','.join(map(str, values)))
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path/'original', source, flat, driver, native=True)
    observed = _trace(tmp_path/'observed', plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in original] == [row[0] for row in observed]
    cells, expected = [0, 0], []
    for a in values:
        if a & 1:
            v = a >> 2
            cells[(a >> 1) & 1] = v + 1 if nested else next((i for i in range(4) if v >> i & 1), 0)
        expected.append(sum(cells))
    assert [row[0] for row in observed] == expected
    assert any(row[1] for row in observed)  # Probes remain live, not a bare execution fallback.
