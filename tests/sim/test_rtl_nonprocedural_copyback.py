"""Compiler-owned copyback errors are not generic frontend incompatibility."""

import pytest

from behave.sim.rtl.catalogue import source_catalogue_with_execution_of
from behave.sim.rtl.frontend_error import SourceRejected
from behave.sim.rtl.source_rules import SourceRule
from tests.sim.test_rtl_tool_isolation import _needs_namespace


@pytest.mark.isolation
@pytest.mark.eda('slang')
@pytest.mark.parametrize('direction', ['output', 'inout', 'ref'])
def test_nonprocedural_copyback_has_typed_original_source_evidence(tmp_path, direction):
    _needs_namespace()
    path = tmp_path / 'original.sv'
    source = f'''module M(input int a,output wire [31:0] y);
      int copy;
      function automatic int f(input int value,{direction} int result);
        result=value+1; return result;
      endfunction
      assign y=f(a,copy);
    endmodule'''
    path.write_text(source)
    with pytest.raises(SourceRejected) as caught:
        source_catalogue_with_execution_of(path, 'M')
    evidence = caught.value.reason.detail['source_rules']
    assert {row['rule'] for row in evidence['violations']} == {SourceRule.NONPROCEDURAL_COPYBACK_CALL.value}
    assert path.read_text() == source
