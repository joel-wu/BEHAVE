"""Type-only queries neither evaluate values nor manufacture source hits."""

import pytest

from behave.sim.symbolic import interp

from .test_rtl_source_ir import _encode, _lower, _run_activation

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('operand,expected', [
    ('a', 4), ('int', 32), ('a + 1', 32), ("{2{a}}", 8), ('f(a)', 7), ('q++', 8),
])
def test_bits_uses_compiler_type_without_operand_effects(tmp_path, operand, expected):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [6:0] f(input [3:0] x);
        q=77;
        if(x==3) return 1;
        return 2;
      endfunction
      always @(posedge clk) q=$bits({operand});
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 99}, encoded)
        assert post[names['q']] == expected
        if 'a' in names:
            assert post[names['a']] == a
        assert not any(source.encode()[hit.path[2]:hit.path[3]] == b'q=77;' for hit in hits)


@pytest.mark.parametrize('declaration,expected', [
    ('logic [2:0] cells[4:1];', 12),
    ('logic [2:0] cells[1:4];', 12),
    ('logic [1:0][2:0] cells[1:4];', 24),
])
def test_bits_includes_all_fixed_array_dimensions(tmp_path, declaration, expected):
    lowered, names, _, _ = _lower(tmp_path, f'''module M(input clk,output reg [7:0] q);
      {declaration}
      always @(posedge clk) q=$bits(cells);
    endmodule''')
    post, _, hits = _run_activation(lowered, names, {'q': 99}, _encode(lowered))
    assert post[names['q']] == expected
    assert 'cells' not in names  # The query does not read array storage.


@pytest.mark.parametrize('width', [3, 8, 100])
def test_bits_in_finite_loop_preserves_each_bit_and_occurrence(tmp_path, width):
    source = f'''module M(input clk,input [{width-1}:0] a,output reg [{width-1}:0] q);
      always @(posedge clk)
        for(int i=0;i<$bits(q);i++) q[i]=a[$bits(q)-i-1];
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    for value in (0, 1, 7, (1 << width)-1):
        post, _, hits, error = interp.run_step(lowered.procedure, {},
            state={name: (value if source_name == 'a' else 0) for source_name, name in names.items()})
        assert not error
        assert post[names['q']] == int(f'{value:0{width}b}'[::-1], 2)
        assert hits


@pytest.mark.eda('verilator', 'make')
def test_bits_preserves_native_values_and_exact_goal_hits(tmp_path):
    from .test_rtl_source_ir import _native_agreement

    _native_agreement(tmp_path, '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [6:0] f(input [3:0] x);
        q=77;
        if(x==3) return 1;
        return 2;
      endfunction
      always @(posedge clk) begin
        q=a;
        q=$bits(f(a)) + q;
      end
    endmodule''')
