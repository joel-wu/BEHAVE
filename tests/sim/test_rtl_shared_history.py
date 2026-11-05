"""Shared observer helpers preserve four-state, masked write history exactly."""

import subprocess
from itertools import product

import pytest

from behave.sim.rtl.source_deferred_events import DeferredRead

from .test_rtl_deferred_notification_kernel import _icarus_task_wrappers
from .test_rtl_source_net_drivers import _prepare
from .test_rtl_source_plan import _run

pytestmark = pytest.mark.eda("slang", "iverilog", "vvp")


def test_shared_history_helpers_preserve_four_state_copyback(tmp_path):
    source = _icarus_task_wrappers(DeferredRead("unused", 2).helper_declarations) + r"""
module tb;
  logic [1:0] first, value, mask, initial_first;
  bit [1:0] touched;
  bit varied;
  function automatic logic [1:0] bits(input int n);
    for(int i=0;i<2;i++) begin
      case((n>>(2*i))&3)
        0: bits[i]=1'b0;
        1: bits[i]=1'b1;
        2: bits[i]=1'bx;
        3: bits[i]=1'bz;
      endcase
    end
  endfunction
  initial begin
    for(int a=0;a<16;a++)
      for(int b=0;b<16;b++)
        for(int c=0;c<16;c++)
          for(int t=0;t<4;t++)
            for(int v=0;v<2;v++) begin
              first=bits(a); initial_first=first; value=bits(b); mask=bits(c);
              touched=2'(t); varied=1'(v);
              __behave_native_history_enqueue_2(mask,value,touched,first,varied);
              $display("ROW %b %b %b %b",first,touched,varied,
                       __behave_native_history_changed_2(touched,first,initial_first));
            end
    $finish;
  end
endmodule
"""
    (tmp_path / "test.sv").write_text(source)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "test", "test.sv")
    observed = subprocess.run(["vvp", "test"], cwd=tmp_path, capture_output=True,
                              text=True, check=True, timeout=30).stdout.splitlines()
    observed = [row.removeprefix("ROW ") for row in observed if row.startswith("ROW ")]
    expected = []
    alphabet = "01xz"
    for a, b, c, t, v in product(range(16), range(16), range(16), range(4), range(2)):
        first = [alphabet[(a >> (2*i)) & 3] for i in range(2)]
        original = first[:]
        value = [alphabet[(b >> (2*i)) & 3] for i in range(2)]
        mask = [alphabet[(c >> (2*i)) & 3] for i in range(2)]
        touched = [bool(t & (1 << i)) for i in range(2)]
        varied = bool(v)
        for i in range(2):
            if mask[i] != "1":
                continue
            if touched[i]:
                varied |= first[i] != value[i]
            else:
                first[i] = value[i]
            touched[i] = True
        changed = any(on and x != y for on, x, y in zip(touched, first, original))
        expected.append(f"{''.join(reversed(first))} "
                        f"{''.join(str(int(on)) for on in reversed(touched))} "
                        f"{int(varied)} {int(changed)}")
    assert observed == expected


def test_shared_history_code_is_per_width_not_per_scope(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [31:0] x=0; wire [3:0] seen;
      always @(posedge a[0]) begin x<=a; x<=0; end
      for(genvar g=0;g<4;g++) begin: cells
        assign seen[g]=|x;
      end
      always_comb q=seen;
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    native = plan.native(scoped=True).text
    assert native.count("function automatic bit __behave_native_history_changed_32(") == 1
    assert native.count("function automatic void __behave_native_history_enqueue_32(") == 1
    assert native.count("function automatic bit [1:0] __behave_native_history_update_32(") == 1
    assert "function automatic void __behave_native_nba_" not in native
    assert native.count("for(int k=0;k<32;k++)") == 2
