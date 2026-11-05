"""Generated subscribers retain the original elaboration scopes."""

from dataclasses import replace

import pytest

from .test_rtl_combinational_events import _trace
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare


pytestmark = pytest.mark.eda('slang', 'verilator', 'make')


@pytest.mark.parametrize('explicit', (False, True))
@pytest.mark.parametrize('restore', (False, True))
@pytest.mark.parametrize('nodes', (3, 4))
def test_nested_generate_single_member_keeps_nba_subscriber_scope(tmp_path, explicit, restore, nodes):
    body = 'assign q[j]=x[j];'
    if explicit:
        body = 'begin: selected ' + body + ' end'
    source = f'''module M(input [7:0] a, output wire [31:0] q);
      logic [2:0] x=0;
      always @(posedge a[0]) begin x<=a[3:1]; {'x<=0;' if restore else ''} end
      for(genvar level=0;level<2;level++) begin: levels
        if(level==0) begin: inputs
          for(genvar j=0;j<{nodes};j++) begin: lanes
            if(j<3) {body}
            else assign q[j]=0;
          end
        end else begin
          assign q[31:{nodes}]=0;
        end
      end
    endmodule'''
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    driver = '''
      const unsigned values[]={0,3,0,5,0,9,0,15,0,1};
      for(unsigned i=0;i<10;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    original = _trace(tmp_path / 'original', source, flat, driver, native=True)
    native = _trace(tmp_path / 'native', plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in native] == [row[0] for row in original]
    assert [row[0] for row in native] == ([0]*10 if restore else [0,1,1,2,2,4,4,7,7,0])
