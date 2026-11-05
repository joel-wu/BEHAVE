// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module QLinearMulBroadcast (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_a,
    input logic [31:0] in_b,
    input logic [7:0] in_zero_a,
    input logic [7:0] in_zero_b,
    input logic [7:0] in_zero_y,
    input logic [2:0] in_shift_a,
    input logic [2:0] in_shift_b,
    output logic [63:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] clip8(input logic signed [63:0] x);
  clip8 = x < -128 ? -128 : x > 127 ? 127 : 32'(x);
endfunction
function automatic signed [31:0] rne(input logic signed [63:0] x, input integer shift);
  logic signed [63:0] d,q,r;
  begin
    d=64'sd1 << shift; q=x >>> shift; r=x-q*d;
    rne=q+((2*r>d || (2*r==d && q[0])) ? 1:0);
  end
endfunction

logic [63:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer r=0;r<2;r=r+1)
    for(integer c=0;c<4;c=c+1) begin
      integer a,b,shift;
      a=s8(in_a[8*(4*r+c) +: 8])-s8(in_zero_a);
      b=s8(in_b[8*c +: 8])-s8(in_zero_b);
      shift=in_shift_a+in_shift_b; value=a*b;
      value=clip8(rne(value,shift)+s8(in_zero_y));
      result[8*(4*r+c) +: 8]=value;
    end
end
assign in_ready = !out_valid || out_ready;
always_ff @(posedge clk) begin
  if (rst) begin
    out_valid <= 0;
    out_values <= 0;

  end else if (in_ready) begin
    out_valid <= in_valid;
    if (in_valid) out_values <= result;

  end
end
endmodule
