// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module RequantizeS32S8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    input logic [30:0] in_multiplier,
    input logic [3:0] in_shift,
    input logic [7:0] in_zero,
    output logic [31:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
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
logic [31:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<4;i=i+1) begin
    value=highmul($signed(in_data[32*i +: 32]),$signed({1'b0,in_multiplier}));
    wide=64'(rdiv(value,32'(in_shift)))+64'(s8(in_zero));
    result[8*i +: 8]=8'(clip8(wide));
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
