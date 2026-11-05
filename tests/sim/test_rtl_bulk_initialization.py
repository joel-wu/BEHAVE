"""Bulk initialization preserves cells and is never a runtime-loop shortcut."""

import json

import pytest

from behave.sim.rtl.bulk_initialization import normalize_initial_loops
from .test_rtl_rom_normalization import _compile, _typed
from .test_rtl_source_plan import _run


SOURCE = '''module M(input clk, we, input [3:0] a, input [7:0] d, output [7:0] q);
localparam N=16;
reg [7:0] memory[0:N-1];
initial for(integer i=0;i<N;i=i+1) memory[i]=8'ha5;
always @(posedge clk) if(we) memory[a]<=d;
assign q=memory[a];
endmodule
'''


@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("source", [SOURCE, SOURCE.replace("8'ha5", "-1"),
    SOURCE.replace("i=0;i<N", "i=3;i<12"), SOURCE.replace("[0:N-1]", "[N-1:0]"),
    SOURCE.replace('reg [7:0]', 'reg [2:0]'),
    SOURCE.replace('initial for', 'initial begin for').replace(
        "memory[i]=8'ha5;", "memory[i]=8'ha5; end")])
def test_bulk_and_original_have_the_same_complete_memory_initial_image(tmp_path, source):
    cst, ast = _typed(tmp_path/'typed', source)
    plan, files = normalize_initial_loops(source, cst, ast)
    assert len(plan.replacements) == 1 and len(files) == 1
    images = []
    for name in ('original', 'bulk'):
        where = tmp_path/name
        where.mkdir()
        if name == 'original':
            (where/'design.sv').write_text(source)
        else:
            (where/'design.sv').write_text(plan.text)
            for filename, data in files.items():
                (where/filename).write_bytes(data)
        _run(where, 'yosys', '-Q', '-T', '-p',
             'read_verilog -sv design.sv; hierarchy -top M; proc; memory_collect; write_json image.json')
        cells = json.loads((where/'image.json').read_text())['modules']['M']['cells']
        memory, = [c['parameters'] for c in cells.values() if c['type'] == '$mem_v2']
        images.append({k: memory[k] for k in ('INIT', 'SIZE', 'WIDTH', 'OFFSET')})
    assert images[0] == images[1]
    _compile(tmp_path/'shared', source)  # the actual common frontend, not a test substitute


@pytest.mark.eda("slang")
@pytest.mark.parametrize("source", [
    SOURCE.replace('i=i+1', 'i=i+2'),
    SOURCE.replace("8'ha5", 'i'),
    SOURCE.replace('memory[i]=', 'memory[i]+='),
    SOURCE.replace('memory[i]=', 'memory[i]<='),
    SOURCE.replace('memory[i]=', 'memory[i & 3]='),
    SOURCE.replace('i<N', "4'(i)<N"),  # truncation would make this nonterminating
    SOURCE.replace('memory[i]=', '#1 memory[i]='),
    SOURCE.replace('initial for', 'always @(posedge clk) for'),
    SOURCE.replace('initial for', 'initial if(we) for'),
    SOURCE.replace('initial for', 'initial #1 for'),
    SOURCE.replace('initial for', 'initial repeat(2) for'),
    SOURCE.replace('initial for(integer i=0', 'integer i; initial for(i=0'),
])
def test_unproved_initializers_are_left_unchanged(tmp_path, source):
    cst, ast = _typed(tmp_path/'typed', source)
    plan, files = normalize_initial_loops(source, cst, ast)
    assert plan.text == source and not files


@pytest.mark.eda("slang")
def test_different_instance_initializers_cannot_share_one_file(tmp_path):
    source = SOURCE.replace('module M(', 'module R #(parameter N=16)(').replace('localparam N=16;', '')
    source += '''module M(input clk,we,input [3:0] a,input [7:0] d,output [15:0] q);
R #(8) x(clk,we,a,d,q[7:0]); R #(16) y(clk,we,a,d,q[15:8]); endmodule
'''
    cst, ast = _typed(tmp_path/'typed', source)
    plan, files = normalize_initial_loops(source, cst, ast)
    assert plan.text == source and not files


@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("extra", ['memory[0]=0;', 'memory[7]=7;', 'memory[0]++;'])
def test_competing_initial_writes_keep_their_original_priority(tmp_path, extra):
    source = SOURCE.replace('initial for', 'initial begin '+extra+' for').replace(
        "memory[i]=8'ha5;", "memory[i]=8'ha5; memory[7]=7; end")
    cst, ast = _typed(tmp_path/'typed', source)
    plan, files = normalize_initial_loops(source, cst, ast)
    assert plan.text == source and not files
    _compile(tmp_path/'shared', source)
