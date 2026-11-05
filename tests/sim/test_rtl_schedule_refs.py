"""Read/write call arguments must participate in combinational cycle analysis."""

from dataclasses import replace

import pytest

from .test_rtl_combinational_events import _trace
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda('slang', 'verilator', 'make')


@pytest.mark.parametrize('mode', ['ref', 'const ref'])
@pytest.mark.parametrize('width', [1, 8, 65])
def test_read_write_dependency_is_cut_before_native_ordering(tmp_path, mode, width):
    # No BEHAVE marker or observer storage: the compiler must handle ordinary
    # reference arguments too. The output uses an independently known identity.
    effect = 'old = value;' if mode == 'ref' else ''
    source = f'''module M(input [7:0] a, output [31:0] q);
      logic [{width-1}:0] value=0;
      function automatic logic [{width-1}:0] update(
          input logic [{width-1}:0] value, {mode} logic [{width-1}:0] old);
        /* verilator no_inline_task */
        {effect}
        return old == value ? old : value;
      endfunction
      always_comb value=update({width}'(a),value);
      assign q=32'(value);
    endmodule'''
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    driver = '''
      for(unsigned i=0;i<256;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i; dut.eval(); uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    rows = _trace(tmp_path/'native', source, flat, driver, native=True)
    assert rows == [(i & ((1 << width)-1), 0, 0) for i in range(256)]
