// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ProjectionResidualS8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_a,
    input logic [31:0] in_b,
    input logic [127:0] in_weights,
    input logic [63:0] in_bias,
    input logic [8:0] in_offset_a,
    input logic [8:0] in_offset_b,
    input logic [30:0] in_mult_a,
    input logic [30:0] in_mult_b,
    input logic [30:0] in_mult_out,
    input logic [4:0] in_shift_a,
    input logic [4:0] in_shift_b,
    input logic [4:0] in_shift_out,
    input logic [2:0] in_left_shift,
    input logic [7:0] in_zero_out,
    output logic [31:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] highmul(input logic signed [31:0] a, b);
  logic signed [63:0] product, adjusted;
  begin
    product = 64'(a) * 64'(b);
    adjusted = product + (product >= 0 ? 64'sd1073741824 : -64'sd1073741823);
    highmul = (a == -32'sd2147483648 && b == -32'sd2147483648)
              ? 32'sd2147483647 : 32'(adjusted / 64'sd2147483648);
  end
endfunction
function automatic signed [31:0] rdiv(input logic signed [31:0] x, input integer exponent);
  logic [63:0] mask, remainder, threshold;
  begin
    mask = (64'd1 << exponent) - 1;
    remainder = {32'd0, x} & mask;
    threshold = (mask >> 1) + (x < 0 ? 1 : 0);
    rdiv = (x >>> exponent) + (remainder > threshold ? 1 : 0);
  end
endfunction
function automatic signed [31:0] clip8(input logic signed [63:0] x);
  clip8 = x < -128 ? -128 : x > 127 ? 127 : 32'(x);
endfunction
function automatic signed [31:0] s9(input logic [8:0] x);
  s9={{23{x[8]}},x};
endfunction

logic [31:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<4;i=i+1) begin
    integer a,b;
    a=s16(in_bias[16*i +: 16]);
    for(integer j=0;j<4;j=j+1)
      a=a+s8(in_weights[8*(4*i+j) +: 8])*(s8(in_a[8*j +: 8])+s9(in_offset_a));
    a=a*(1 << in_left_shift);
    b=(s8(in_b[8*i +: 8])+s9(in_offset_b))*(1 << in_left_shift);
    a=rdiv(highmul(a,$signed({1'b0,in_mult_a})),in_shift_a);
    b=rdiv(highmul(b,$signed({1'b0,in_mult_b})),in_shift_b);
    value=clip8(rdiv(highmul(a+b,$signed({1'b0,in_mult_out})),in_shift_out)+s8(in_zero_out));
    result[8*i +: 8]=value[7:0];
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
